# services/investment_alignment.py
#
# Computes InvestmentAlignment — a value object computed fresh on every
# request, never persisted (Progress Log §5.3.2).
#
# Alignment states (Government §9):
#   UNADDRESSED           — demand + gap + no relevant intervention
#   PARTIALLY_ADDRESSED   — intervention exists but insufficient for the gap
#   ALIGNED               — intervention appears relevant to the demand
#   IMPLEMENTATION_ACCESS_GAP — intervention exists but isn't reaching the area
#   EMERGING_GAP          — demand increasing while coverage isn't keeping pace
#
# reasoning is a REQUIRED field on every result — the Priority Evidence Card
# always shows "WHY FLAGGED" alongside the state label (Progress Log §5.3.2).
#
# Framework-agnostic pure function.
# Requires an active SQLAlchemy session (Flask app context).

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from app.services.gap_assessment import InfrastructureGapAssessment

logger = logging.getLogger(__name__)

AlignmentState = Literal[
    "UNADDRESSED",
    "PARTIALLY_ADDRESSED",
    "ALIGNED",
    "IMPLEMENTATION_ACCESS_GAP",
    "EMERGING_GAP",
]


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------

@dataclass
class InvestmentAlignment:
    """
    Computed-fresh value object representing the alignment between
    citizen demand, the infrastructure gap, and existing government investment.

    state: one of the five alignment states (Government §9).

    reasoning: REQUIRED — a short structured explanation of why this state
               was assigned.  Used in the Priority Evidence Card "WHY FLAGGED"
               section.  Never left blank.

    referenced_investment_ids: the GovernmentInvestment IDs consulted.
               Empty list means no relevant investment was found (UNADDRESSED).
    """
    demand_cluster_id: str
    state: AlignmentState
    reasoning: str                             # required, never empty
    referenced_gap_assessment: InfrastructureGapAssessment
    referenced_investment_ids: list[str] = field(default_factory=list)
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate(
    demand_cluster_id: str,
    gap_assessment: InfrastructureGapAssessment | None = None,
) -> InvestmentAlignment:
    """
    Compute a fresh InvestmentAlignment for the given DemandCluster.

    Parameters
    ----------
    demand_cluster_id : str
        The DemandCluster to assess.

    gap_assessment : InfrastructureGapAssessment | None
        If already computed (e.g. by the evidence_detail view which calls
        both gap_assessment.calculate() and this function in sequence),
        pass it in to avoid a second DB read.  If None, it will be
        computed fresh here.

    Returns
    -------
    InvestmentAlignment dataclass.
    """
    from app.extensions import db
    from app.models.demand_cluster import DemandCluster
    from app.models.reference_data import GovernmentInvestment
    import app.services.gap_assessment as gap_svc

    cluster = db.session.get(DemandCluster, demand_cluster_id)
    if cluster is None:
        raise ValueError(f"DemandCluster {demand_cluster_id!r} not found")

    # Reuse gap assessment if provided, otherwise compute fresh
    if gap_assessment is None:
        gap_assessment = gap_svc.calculate(demand_cluster_id)

    # Find relevant GovernmentInvestments for this category + country
    investments = (
        db.session.query(GovernmentInvestment)
        .filter_by(
            category_id=cluster.category_id,
            country_id=cluster.country_id,
        )
        .all()
    )

    state, reasoning = _derive_alignment(
        cluster=cluster,
        gap=gap_assessment,
        investments=investments,
    )

    return InvestmentAlignment(
        demand_cluster_id=demand_cluster_id,
        state=state,
        reasoning=reasoning,
        referenced_gap_assessment=gap_assessment,
        referenced_investment_ids=[inv.id for inv in investments],
        computed_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Alignment derivation
# ---------------------------------------------------------------------------

def _derive_alignment(
    cluster,
    gap: InfrastructureGapAssessment,
    investments: list,
) -> tuple[AlignmentState, str]:
    """
    Derive alignment state and required reasoning string.

    Returns (state, reasoning) tuple.
    """
    has_demand = gap.citizen_demand_present
    gap_confirmed = gap.confidence in ("HIGH", "MEDIUM")
    trend_increasing = cluster.trend == "increasing"

    if not investments:
        if has_demand and gap_confirmed:
            return (
                "UNADDRESSED",
                "High citizen demand + confirmed infrastructure gap + "
                "no relevant government investment identified.",
            )
        return (
            "UNADDRESSED",
            "Citizen demand present but no relevant government investment found. "
            "Infrastructure gap data is limited or inconclusive.",
        )

    # Investments exist — assess quality of coverage
    active_investments = [
        inv for inv in investments
        if inv.status.lower() not in ("completed", "cancelled", "closed")
    ]
    recent_investments = [
        inv for inv in investments
        if inv.freshness_status == "recent"
    ]

    inv_ids_str = ", ".join(inv.name for inv in investments[:2])
    suffix = f" (investment{'s' if len(investments) > 1 else ''}: {inv_ids_str})"

    # Increasing demand despite active investment → implementation/access gap
    if trend_increasing and active_investments:
        return (
            "IMPLEMENTATION_ACCESS_GAP",
            f"Active investment exists but citizen demand is increasing — "
            f"intended coverage may not be reaching the affected area.{suffix}",
        )

    # Demand increasing, investment not keeping pace
    if trend_increasing and not active_investments:
        return (
            "EMERGING_GAP",
            f"Citizen demand is increasing while existing interventions "
            f"appear inactive or completed.{suffix}",
        )

    # Active investment but gap still confirmed → partially addressed
    if gap_confirmed and active_investments:
        return (
            "PARTIALLY_ADDRESSED",
            f"Relevant investment exists but confirmed infrastructure gap "
            f"suggests incomplete coverage.{suffix}",
        )

    # Investment present and gap not strongly confirmed → aligned
    if recent_investments:
        return (
            "ALIGNED",
            f"Recent government investment appears relevant to the observed demand. "
            f"Infrastructure gap evidence is limited or low-confidence.{suffix}",
        )

    # Old/stale investment, gap present
    return (
        "PARTIALLY_ADDRESSED",
        f"Investment exists but data is stale — cannot confirm adequate coverage "
        f"for the current level of citizen demand.{suffix}",
    )
