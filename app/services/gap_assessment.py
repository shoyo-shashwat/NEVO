# services/gap_assessment.py
#
# Computes InfrastructureGapAssessment — a value object computed fresh on
# every request, never persisted (Progress Log §5.3.3).
#
# Reads live data from:
#   InfrastructureDataPoint  — official coverage, facility distance
#   DemographicDataPoint     — population affected
#   DemandCluster            — citizen demand present / total_reports
#
# Language rule (Progress Log §5.3.1 / Government §7):
#   Output must always say "evidence indicates a gap" — never
#   "citizens proved a gap."  The confidence level reflects combined
#   evidence quality, not just volume of complaints.
#
# Framework-agnostic pure function: takes IDs, returns a plain dict.
# No Flask imports.  Requires an active SQLAlchemy session (Flask app context).

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------

@dataclass
class InfrastructureGapAssessment:
    """
    Computed-fresh value object representing the infrastructure gap evidence
    for a given DemandCluster.

    confidence: overall assessment of how well-evidenced the gap is.
        HIGH         — recent official data + high demand + large population
        MEDIUM       — partial evidence (some data stale or demand moderate)
        LOW          — weak signal (low demand or limited official data)
        NEEDS_VALIDATION — data too stale or contradictory to be reliable

    All numeric fields reflect source data directly — nothing is invented.
    """
    demand_cluster_id: str
    category_code: str

    # From InfrastructureDataPoint
    official_coverage: str | None          # "Low" | "Medium" | "High" | None
    nearest_facility_km: float | None
    infra_data_freshness: str | None       # "recent" | "stale" | "unknown"
    infra_source: str | None

    # From DemographicDataPoint
    population_affected: int | None
    demo_data_freshness: str | None

    # From DemandCluster (live)
    citizen_demand_present: bool
    total_reports: int
    unique_contributors: int

    # Derived
    confidence: str                        # HIGH | MEDIUM | LOW | NEEDS_VALIDATION
    computed_at: datetime


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate(demand_cluster_id: str) -> InfrastructureGapAssessment:
    """
    Compute a fresh InfrastructureGapAssessment for the given DemandCluster.

    Reads InfrastructureDataPoint + DemographicDataPoint matching the
    cluster's category + region, plus live DemandCluster counts.

    Parameters
    ----------
    demand_cluster_id : str — the DemandCluster to assess

    Returns
    -------
    InfrastructureGapAssessment dataclass

    Raises
    ------
    ValueError if the cluster does not exist
    """
    # Import inside function — keeps module free of Flask/SQLAlchemy at
    # import time, callable from plain Python with an active session.
    from app.extensions import db
    from app.models.demand_cluster import DemandCluster
    from app.models.reference_data import InfrastructureDataPoint, DemographicDataPoint
    from app.models.shared import Category

    cluster = db.session.get(DemandCluster, demand_cluster_id)
    if cluster is None:
        raise ValueError(f"DemandCluster {demand_cluster_id!r} not found")

    category = db.session.get(Category, cluster.category_id)
    category_code = category.code if category else "unknown"

    # Best-matching InfrastructureDataPoint: same category + country,
    # prefer most-recently-synced row, then same region.
    infra = (
        db.session.query(InfrastructureDataPoint)
        .filter_by(category_id=cluster.category_id, country_id=cluster.country_id)
        .order_by(InfrastructureDataPoint.platform_last_synced.desc().nullslast())
        .first()
    )

    # Best-matching DemographicDataPoint: same category + country
    demo = (
        db.session.query(DemographicDataPoint)
        .filter_by(category_id=cluster.category_id, country_id=cluster.country_id)
        .order_by(DemographicDataPoint.platform_last_synced.desc().nullslast())
        .first()
    )

    # Live demand signals from the cluster aggregate
    total_reports = cluster.total_reports
    unique_contributors = cluster.unique_contributors
    citizen_demand_present = total_reports > 0

    # Derive confidence
    confidence = _derive_confidence(
        infra=infra,
        demo=demo,
        total_reports=total_reports,
        unique_contributors=unique_contributors,
    )

    return InfrastructureGapAssessment(
        demand_cluster_id=demand_cluster_id,
        category_code=category_code,
        official_coverage=infra.official_coverage if infra else None,
        nearest_facility_km=infra.nearest_facility_distance_km if infra else None,
        infra_data_freshness=infra.freshness_status if infra else None,
        infra_source=infra.source if infra else None,
        population_affected=demo.population_affected if demo else None,
        demo_data_freshness=demo.freshness_status if demo else None,
        citizen_demand_present=citizen_demand_present,
        total_reports=total_reports,
        unique_contributors=unique_contributors,
        confidence=confidence,
        computed_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Confidence derivation
# ---------------------------------------------------------------------------

def _derive_confidence(
    infra,
    demo,
    total_reports: int,
    unique_contributors: int,
) -> str:
    """
    Derive evidence confidence from available data signals.

    Rules (in order of precedence):
    1. No infrastructure data at all → NEEDS_VALIDATION
    2. Stale infrastructure data + low demand → NEEDS_VALIDATION
    3. High coverage (infra says "High") + citizen demand → LOW
       (official data contradicts the reported gap — needs validation)
    4. Recent data + high demand (>= 5 unique contributors) + low coverage
       → HIGH
    5. Moderate signals → MEDIUM
    6. Weak signals → LOW
    """
    if infra is None:
        return "NEEDS_VALIDATION"

    infra_fresh = infra.freshness_status == "recent"
    infra_stale = infra.freshness_status == "stale"
    coverage_low = infra.official_coverage == "Low"
    coverage_high = infra.official_coverage == "High"
    high_demand = unique_contributors >= 5
    any_demand = total_reports > 0

    # Stale data with no meaningful demand signal
    if infra_stale and not high_demand:
        return "NEEDS_VALIDATION"

    # Official coverage is high but citizens are complaining — contradiction
    if coverage_high and any_demand:
        return "LOW"

    # Strong positive case: recent data confirms low coverage + real demand
    if infra_fresh and coverage_low and high_demand:
        return "HIGH"

    # Moderate case: some evidence on both sides
    if any_demand and (coverage_low or not coverage_high):
        return "MEDIUM"

    return "LOW"
