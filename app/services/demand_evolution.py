# services/demand_evolution.py
#
# Per-cluster demand-over-time analytics — pure analytics on Contribution
# timestamps already recorded, no new AI model (India-only MVP design
# review, "Demand Evolution" P1 item: "You don't need another AI model.
# It's basically analytics on your existing data.").
#
# Computed fresh, never persisted, same pattern as the rest of services/.
#
# Language discipline: "growing"/"stable"/"declining" is a simple two-period
# count comparison, not a statistical trend test — worded as a plain
# comparison, matching the same "increased over the period, not a trend"
# rule already applied in government/routes.py::citizen_voice()'s emerging
# themes.

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta


@dataclass
class DemandEvolution:
    demand_cluster_id: str
    monthly_counts: list = field(default_factory=list)   # [{"month": "2026-01", "count": int}, ...]
    direction: str = "stable"                              # "growing" | "stable" | "declining"
    recent_count: int = 0                                  # last 30 days
    prior_count: int = 0                                   # 30 days before that


def calculate(demand_cluster_id: str, months: int = 6) -> DemandEvolution:
    """
    Monthly Contribution counts for a cluster over the last `months` months,
    plus a simple two-period (last 30 days vs. prior 30 days) direction call.
    """
    from app.extensions import db
    from app.models.citizen_models import Contribution

    now = datetime.now(timezone.utc)

    contributions = (
        db.session.query(Contribution.timestamp)
        .filter(Contribution.demand_cluster_id == demand_cluster_id)
        .all()
    )
    timestamps = [row[0] for row in contributions]

    monthly_counts = _bucket_by_month(timestamps, now, months)

    window_start = now - timedelta(days=30)
    prior_start = now - timedelta(days=60)
    recent_count = sum(1 for t in timestamps if t >= window_start)
    prior_count = sum(1 for t in timestamps if prior_start <= t < window_start)

    if recent_count > prior_count and recent_count >= 3:
        direction = "growing"
    elif recent_count < prior_count and prior_count >= 3:
        direction = "declining"
    else:
        direction = "stable"

    return DemandEvolution(
        demand_cluster_id=demand_cluster_id,
        monthly_counts=monthly_counts,
        direction=direction,
        recent_count=recent_count,
        prior_count=prior_count,
    )


def _bucket_by_month(timestamps: list, now: datetime, months: int) -> list:
    """Pure bucketing -- no DB access. Returns oldest-to-newest month buckets."""
    buckets: dict = {}
    month_keys = []
    year, month = now.year, now.month
    for _ in range(months):
        key = f"{year:04d}-{month:02d}"
        month_keys.append(key)
        buckets[key] = 0
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    month_keys.reverse()

    for t in timestamps:
        key = f"{t.year:04d}-{t.month:02d}"
        if key in buckets:
            buckets[key] += 1

    return [{"month": key, "count": buckets[key]} for key in month_keys]
