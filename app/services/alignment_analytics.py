# services/alignment_analytics.py
#
# Aggregate, population-based investment-alignment analytics across a set of
# DemandClusters — the "Population represented by identified demand" metric
# locked in the India-only MVP design review.
#
# Deliberately NOT a single collapsed "coverage score" (Government dashboard
# design decision): the five alignment states (ALIGNED / PARTIALLY_ADDRESSED /
# UNADDRESSED / IMPLEMENTATION_ACCESS_GAP / EMERGING_GAP) are always exposed
# as separate percentages, with a combined "potentially addressed" figure
# (ALIGNED + PARTIALLY_ADDRESSED) available for a single-number summary when
# needed — never silently visually collapsed into one score.
#
# Computed fresh on every request, never persisted, same pattern as
# gap_assessment.py / investment_alignment.py / priority_scoring.py.

from __future__ import annotations

from dataclasses import dataclass, field

ALIGNMENT_STATES = [
    "ALIGNED", "PARTIALLY_ADDRESSED", "UNADDRESSED",
    "IMPLEMENTATION_ACCESS_GAP", "EMERGING_GAP",
]

_POTENTIALLY_ADDRESSED_STATES = ("ALIGNED", "PARTIALLY_ADDRESSED")


@dataclass
class AlignmentBreakdown:
    scope_label: str                                    # e.g. "All India", a state/district name
    cluster_count: int
    clusters_with_population_data: int
    total_population: int
    by_state_population: dict = field(default_factory=dict)   # {state: summed population_affected}
    by_state_pct: dict = field(default_factory=dict)          # {state: % of total_population}
    potentially_addressed_pct: float = 0.0                    # ALIGNED + PARTIALLY_ADDRESSED

    @property
    def not_addressed_pct(self) -> float:
        """UNADDRESSED + IMPLEMENTATION_ACCESS_GAP + EMERGING_GAP -- the complement of
        potentially_addressed_pct. Exists so callers don't show only the UNADDRESSED
        state's own percentage as if it were the whole "needs attention" picture,
        silently ignoring IMPLEMENTATION_ACCESS_GAP/EMERGING_GAP population sitting
        right next to it in the same bar."""
        return round(100.0 - self.potentially_addressed_pct, 1) if self.total_population else 0.0

    @property
    def methodology(self) -> str:
        return (
            "Population represented by identified demand clusters, broken down by each "
            "cluster's investment-alignment state (population_affected summed per state, "
            "as a share of total population represented). Clusters without population "
            "data are excluded from these percentages but still counted in cluster_count — "
            "see clusters_with_population_data. Known limitation: if two different demand "
            "clusters' regions overlap, the population in the overlapping area is counted "
            "once per cluster, not deduplicated across clusters — this metric reflects "
            "cluster-level demand signal, not a strict unique-population census."
        )


# ---------------------------------------------------------------------------
# Pure aggregation (no DB access) — unit-tested directly
# ---------------------------------------------------------------------------

def compute_breakdown(cluster_results: list, scope_label: str = "") -> AlignmentBreakdown:
    """
    cluster_results: list of (population_affected_or_None, alignment_state) tuples,
                      one per DemandCluster already resolved by the caller via
                      gap_assessment.calculate() + investment_alignment.calculate().
    """
    cluster_count = len(cluster_results)
    with_population = [(p, s) for p, s in cluster_results if p is not None]
    total_population = sum(p for p, _ in with_population)

    by_state_population = {state: 0 for state in ALIGNMENT_STATES}
    for p, state in with_population:
        by_state_population[state] = by_state_population.get(state, 0) + p

    if total_population > 0:
        by_state_pct = {
            state: round(pop / total_population * 100, 1)
            for state, pop in by_state_population.items()
        }
    else:
        by_state_pct = {state: 0.0 for state in ALIGNMENT_STATES}

    potentially_addressed_pct = round(
        sum(by_state_pct.get(s, 0.0) for s in _POTENTIALLY_ADDRESSED_STATES), 1
    )

    return AlignmentBreakdown(
        scope_label=scope_label,
        cluster_count=cluster_count,
        clusters_with_population_data=len(with_population),
        total_population=total_population,
        by_state_population=by_state_population,
        by_state_pct=by_state_pct,
        potentially_addressed_pct=potentially_addressed_pct,
    )


# ---------------------------------------------------------------------------
# DB-facing entry point
# ---------------------------------------------------------------------------

def calculate(country_id: str, region_id: str | None = None, category_id: str | None = None) -> AlignmentBreakdown:
    """
    Compute a fresh AlignmentBreakdown for every DemandCluster matching
    country_id (+ optional category_id), optionally restricted to clusters
    whose region_ids overlap region_id or any of its descendants (e.g. a
    state-level filter includes all of that state's districts/constituencies).

    N+1 gap_assessment/investment_alignment calls, one pair per cluster —
    acceptable at MVP seed-data scale (see gap_assessment.py's own note on
    this same tradeoff); revisit with caching/batching if cluster counts
    grow into the thousands.
    """
    from app.models.demand_cluster import DemandCluster
    import app.services.gap_assessment as gap_svc
    import app.services.investment_alignment as align_svc

    query = DemandCluster.query.filter_by(country_id=country_id)
    if category_id:
        query = query.filter_by(category_id=category_id)
    clusters = query.all()

    scope_label = "All India"
    if region_id:
        allowed = _descendant_region_ids(country_id, region_id)
        clusters = [c for c in clusters if set(c.region_ids or []) & allowed]
        scope_label = _region_name(region_id) or scope_label

    results = []
    for c in clusters:
        gap = gap_svc.calculate(c.id)
        alignment = align_svc.calculate(c.id, gap_assessment=gap)
        results.append((gap.population_affected, alignment.state))

    return compute_breakdown(results, scope_label=scope_label)


def _descendant_region_ids(country_id: str, region_id: str) -> set:
    """region_id itself plus every region beneath it in the administrative hierarchy."""
    from app.models.shared import AdministrativeRegion

    all_regions = AdministrativeRegion.query.filter_by(country_id=country_id).all()
    children_map: dict = {}
    for r in all_regions:
        children_map.setdefault(r.parent_region_id, []).append(r.id)

    result = {region_id}
    frontier = [region_id]
    while frontier:
        current = frontier.pop()
        for child in children_map.get(current, []):
            if child not in result:
                result.add(child)
                frontier.append(child)
    return result


def _region_name(region_id: str) -> str | None:
    from app.extensions import db
    from app.models.shared import AdministrativeRegion

    region = db.session.get(AdministrativeRegion, region_id)
    return region.name if region else None
