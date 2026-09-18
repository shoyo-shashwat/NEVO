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
# Region-aware investment matching (same fix as gap_assessment.py, see that
# module's docstring): GovernmentInvestment was previously matched by
# category_id + country_id ONLY, so a cluster in one state could be marked
# "ALIGNED" because of an unrelated investment on the other side of the
# country. Investments are now filtered to those actually scoped to the
# cluster's own region_ids or a broader region that covers them (state /
# national / country-wide) — never an unrelated region. See
# app/services/region_matching.py and tests/test_investment_alignment.py.
#
# Framework-agnostic pure function.
# Requires an active SQLAlchemy session (Flask app context).

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from app.services.gap_assessment import InfrastructureGapAssessment
from app.services.region_matching import fetch_ancestor_chains

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

    referenced_investment_ids: the GovernmentInvestment IDs consulted — now
               only investments actually region-relevant to the cluster
               (see module docstring), not every investment of the same
               category anywhere in the country.

    investment_scope: "region_specific" | "mixed" | "broader_region" | "no_data"
               — mirrors InfrastructureGapAssessment.evidence_scope, so the UI
               can show whether the referenced investments are local to the
               cluster or a broader-region fallback (e.g. a state-wide scheme).
    """
    demand_cluster_id: str
    state: AlignmentState
    reasoning: str                             # required, never empty
    referenced_gap_assessment: InfrastructureGapAssessment
    referenced_investment_ids: list[str] = field(default_factory=list)
    investment_scope: str = "no_data"
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
    import app.services.gap_assessment as gap_svc

    cluster = db.session.get(DemandCluster, demand_cluster_id)
    if cluster is None:
        raise ValueError(f"DemandCluster {demand_cluster_id!r} not found")

    # Reuse gap assessment if provided, otherwise compute fresh
    if gap_assessment is None:
        gap_assessment = gap_svc.calculate(demand_cluster_id)

    investments, investment_scope = _region_relevant_investments(
        category_id=cluster.category_id,
        country_id=cluster.country_id,
        region_ids=list(cluster.region_ids or []),
    )

    state, reasoning = _derive_alignment(
        cluster=cluster,
        gap=gap_assessment,
        investments=investments,
        investment_scope=investment_scope,
    )

    return InvestmentAlignment(
        demand_cluster_id=demand_cluster_id,
        state=state,
        reasoning=reasoning,
        referenced_gap_assessment=gap_assessment,
        referenced_investment_ids=[inv.id for inv in investments],
        investment_scope=investment_scope,
        computed_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Region-aware investment matching (DB-facing; wraps region_matching.py)
# ---------------------------------------------------------------------------

def _region_relevant_investments(category_id: str, country_id: str, region_ids: list) -> tuple[list, str]:
    """
    Fetch GovernmentInvestment rows for this category+country, then filter
    to those actually relevant to the cluster's geography: scoped to one of
    the cluster's own regions, to an ancestor of one of them (state/national
    covering the cluster), or country-wide (region_id IS NULL).

    Unlike gap_assessment's "one best row per region" resolution, a cluster
    can legitimately have several relevant investments (two different
    healthcare projects in the same district), so this returns the full
    matching list rather than picking one.

    Returns (investments, scope_label) where scope_label mirrors
    ResolvedEvidence.scope_label:
      "no_data"         — no relevant investment found at any level
      "region_specific" — every relevant investment is scoped to the cluster's own region(s)
      "mixed"           — some native, some broader-region/country-wide
      "broader_region"  — every relevant investment is a broader-region/country-wide fallback
    """
    from app.extensions import db
    from app.models.reference_data import GovernmentInvestment

    candidates = (
        db.session.query(GovernmentInvestment)
        .filter_by(category_id=category_id, country_id=country_id)
        .all()
    )

    if not region_ids:
        return _filter_relevant_investments(candidates, region_ids=[], chains={})

    chains = fetch_ancestor_chains(
        region_ids, extra_region_ids=[c.region_id for c in candidates if c.region_id is not None],
    )
    return _filter_relevant_investments(candidates, region_ids, chains)


def _filter_relevant_investments(candidates: list, region_ids: list, chains: dict) -> tuple[list, str]:
    """
    Pure filtering logic (no DB access) -- separated from
    _region_relevant_investments() so it's unit-testable without a database.

    region_ids=[] (cluster has no region assigned) means only country-wide
    (region_id IS NULL) candidates can be relevant, and that's always a
    fallback since there's no cluster-specific region to have been "native."
    """
    if not region_ids:
        relevant = [c for c in candidates if c.region_id is None]
        return relevant, ("broader_region" if relevant else "no_data")

    # Union of every level (own region + all ancestors + country-wide=None)
    # across all of the cluster's regions -- an investment scoped to any of
    # these levels is geographically relevant to this cluster.
    acceptable_scopes = {level for chain in chains.values() for level in chain}

    relevant = [c for c in candidates if c.region_id in acceptable_scopes]
    native = [c for c in relevant if c.region_id in region_ids]
    broader = [c for c in relevant if c.region_id not in region_ids]

    if not relevant:
        scope = "no_data"
    elif native and not broader:
        scope = "region_specific"
    elif native and broader:
        scope = "mixed"
    else:
        scope = "broader_region"

    return relevant, scope


# ---------------------------------------------------------------------------
# Alignment derivation
# ---------------------------------------------------------------------------

def _derive_alignment(
    cluster,
    gap: InfrastructureGapAssessment,
    investments: list,
    investment_scope: str,
) -> tuple[AlignmentState, str]:
    """
    Derive alignment state and required reasoning string.

    Returns (state, reasoning) tuple.
    """
    has_demand = gap.citizen_demand_present
    gap_confirmed = gap.confidence in ("HIGH", "MEDIUM")
    trend_increasing = cluster.trend == "increasing"

    scope_note = (
        " (investment scope: broader-region/state-wide, not specific to this area)"
        if investment_scope == "broader_region" else ""
    )

    if not investments:
        if has_demand and gap_confirmed:
            return (
                "UNADDRESSED",
                "High citizen demand + confirmed infrastructure gap + "
                "no relevant government investment identified in this area.",
            )
        return (
            "UNADDRESSED",
            "Citizen demand present but no relevant government investment found in this area. "
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
    suffix = f" (investment{'s' if len(investments) > 1 else ''}: {inv_ids_str}){scope_note}"

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
