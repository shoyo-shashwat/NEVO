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
# Region-aware evidence matching (fixes a real bug found 2026 during the
# India-only Intelligence Page design review): InfrastructureDataPoint and
# DemographicDataPoint were previously matched by category_id + country_id
# ONLY — no region_id filter at all, despite the model docstrings claiming
# region-awareness. In practice every cluster of a given category anywhere
# in the country received the exact same evidence row. Evidence is now
# resolved per cluster region via app/services/region_matching.py, walking
# the administrative hierarchy (constituency -> district -> state ->
# national -> country-wide) and aggregating across all of a multi-region
# cluster's regions -- never silently picking one "best" region and
# discarding the rest. See tests/test_region_matching.py and
# tests/test_gap_assessment.py.
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
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.region_matching import (
    ResolvedEvidence,
    RegionMatch,
    fetch_ancestor_chains,
    resolve_region_matches,
)

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

    Provenance fields (evidence_scope, matched_region_ids, fallback_used,
    unmatched_region_ids) make it possible to tell a reader exactly which
    geographic level the displayed evidence actually came from, rather than
    presenting broader-region or country-wide fallback data as if it were
    hyper-local — see region_matching.ResolvedEvidence.scope_label.
    """
    demand_cluster_id: str
    category_code: str

    # From InfrastructureDataPoint (aggregated across the cluster's regions)
    official_coverage: str | None          # "Low" | "Medium" | "High" | None — worst-case across matched regions
    nearest_facility_km: float | None      # max across matched regions (farthest = most conservative)
    infra_data_freshness: str | None       # "recent" | "stale" | "unknown" — worst-case across matched regions
    infra_source: str | None               # distinct sources joined, for display

    # From DemographicDataPoint (summed across the cluster's regions)
    population_affected: int | None
    demo_data_freshness: str | None

    # From DemandCluster (live)
    citizen_demand_present: bool
    total_reports: int
    unique_contributors: int

    # Evidence provenance (region_matching.py) — one combined picture across
    # both InfrastructureDataPoint and DemographicDataPoint resolution, since
    # they're matched against the same cluster.region_ids and a caller only
    # needs one "how local is this evidence" answer.
    evidence_scope: str                    # "region_specific" | "mixed" | "broader_region" | "no_data"
    matched_region_ids: list = field(default_factory=list)
    fallback_used: bool = False
    unmatched_region_ids: list = field(default_factory=list)

    # Derived
    confidence: str = "LOW"                # HIGH | MEDIUM | LOW | NEEDS_VALIDATION
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate(demand_cluster_id: str) -> InfrastructureGapAssessment:
    """
    Compute a fresh InfrastructureGapAssessment for the given DemandCluster.

    Resolves InfrastructureDataPoint + DemographicDataPoint against the
    cluster's actual region_ids (see module docstring), aggregates across
    all matched regions, plus live DemandCluster counts.

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

    region_ids = list(cluster.region_ids or [])

    infra_evidence = _resolve_cluster_evidence(
        InfrastructureDataPoint, cluster.category_id, cluster.country_id, region_ids,
    )
    demo_evidence = _resolve_cluster_evidence(
        DemographicDataPoint, cluster.category_id, cluster.country_id, region_ids,
    )

    infra_agg = _aggregate_infrastructure(infra_evidence.matched_rows)
    demo_agg = _aggregate_demographics(demo_evidence.matched_rows)

    # One combined provenance picture — infra and demo are matched against
    # the same region_ids, so "how local is this evidence" is meaningful as
    # a single answer covering both.
    combined_scope = _combine_scope(infra_evidence, demo_evidence)

    # Live demand signals from the cluster aggregate
    total_reports = cluster.total_reports
    unique_contributors = cluster.unique_contributors
    citizen_demand_present = total_reports > 0

    confidence = _derive_confidence(
        infra_agg=infra_agg,
        total_reports=total_reports,
        unique_contributors=unique_contributors,
        evidence_scope=combined_scope,
    )

    return InfrastructureGapAssessment(
        demand_cluster_id=demand_cluster_id,
        category_code=category_code,
        official_coverage=infra_agg["official_coverage"],
        nearest_facility_km=infra_agg["nearest_facility_km"],
        infra_data_freshness=infra_agg["freshness_status"],
        infra_source=infra_agg["source"],
        population_affected=demo_agg["population_affected"],
        demo_data_freshness=demo_agg["freshness_status"],
        citizen_demand_present=citizen_demand_present,
        total_reports=total_reports,
        unique_contributors=unique_contributors,
        evidence_scope=combined_scope,
        matched_region_ids=sorted({
            m.matched_region_id for m in (infra_evidence.matches + demo_evidence.matches)
            if m.matched_row is not None and m.matched_region_id is not None
        }),
        fallback_used=infra_evidence.fallback_used or demo_evidence.fallback_used,
        unmatched_region_ids=sorted(set(infra_evidence.unmatched_region_ids) | set(demo_evidence.unmatched_region_ids)),
        confidence=confidence,
        computed_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Region-aware evidence resolution (DB-facing; wraps region_matching.py)
# ---------------------------------------------------------------------------

def _resolve_cluster_evidence(model_cls, category_id: str, country_id: str, region_ids: list) -> ResolvedEvidence:
    """
    Fetch category+country-scoped candidate rows and resolve them against a
    cluster's region_ids using the shared region_matching logic.

    Small-dataset MVP approach (per the region_matching.py design discussion):
    fetches all candidates for the category/country, then walks the
    administrative hierarchy in Python rather than pushing the walk into SQL
    (a recursive CTE). Fine at seed-data scale; revisit if InfrastructureData
    Point/DemographicDataPoint rows per category grow into the thousands.
    """
    from app.extensions import db

    candidates = (
        db.session.query(model_cls)
        .filter_by(category_id=category_id, country_id=country_id)
        .all()
    )

    if not region_ids:
        # Cluster has no region assigned — only a country-wide (region_id IS
        # NULL) row can possibly apply; there's nothing to walk up from.
        country_wide = next((c for c in candidates if c.region_id is None), None)
        if country_wide is None:
            return ResolvedEvidence(matches=[])
        return ResolvedEvidence(matches=[
            RegionMatch(region_id=None, matched_row=country_wide, matched_region_id=None, is_fallback=True)
        ])

    chains = fetch_ancestor_chains(
        region_ids, extra_region_ids=[c.region_id for c in candidates if c.region_id is not None],
    )
    return resolve_region_matches(region_ids, candidates, chains)


def _combine_scope(infra_evidence: ResolvedEvidence, demo_evidence: ResolvedEvidence) -> str:
    """Combine two ResolvedEvidence scope labels into one, biased toward the less-local answer."""
    # Worst-to-best so "no_data" or "broader_region" in either source wins.
    rank = {"no_data": 0, "broader_region": 1, "mixed": 2, "region_specific": 3}
    labels = [infra_evidence.scope_label, demo_evidence.scope_label]
    return min(labels, key=lambda l: rank[l])


# ---------------------------------------------------------------------------
# Aggregation across a cluster's matched regions
#
# MVP aggregation rules (per the region_matching.py design discussion) —
# each chosen to be the conservative ("don't understate a gap") direction:
# ---------------------------------------------------------------------------

_COVERAGE_RANK = {"Low": 0, "Medium": 1, "High": 2}
_FRESHNESS_RANK = {"stale": 0, "unknown": 1, "recent": 2}


def _aggregate_infrastructure(rows: list) -> dict:
    """
    official_coverage  -> worst (lowest) across matched regions
    nearest_facility_km -> max across matched regions (farthest = most conservative)
    freshness_status   -> worst (most stale) across matched regions
    source             -> distinct sources, joined for display
    """
    if not rows:
        return {"official_coverage": None, "nearest_facility_km": None,
                "freshness_status": None, "source": None}

    coverages = [r.official_coverage for r in rows if r.official_coverage]
    official_coverage = min(coverages, key=lambda c: _COVERAGE_RANK.get(c, 1)) if coverages else None

    distances = [r.nearest_facility_distance_km for r in rows if r.nearest_facility_distance_km is not None]
    nearest_facility_km = max(distances) if distances else None

    freshness_values = [r.freshness_status for r in rows if r.freshness_status]
    freshness_status = min(freshness_values, key=lambda f: _FRESHNESS_RANK.get(f, 1)) if freshness_values else None

    sources = sorted({r.source for r in rows if r.source})
    source = ", ".join(sources) if sources else None

    return {
        "official_coverage": official_coverage,
        "nearest_facility_km": nearest_facility_km,
        "freshness_status": freshness_status,
        "source": source,
    }


def _aggregate_demographics(rows: list) -> dict:
    """
    population_affected -> summed across matched regions (each row is a
                            distinct region, so summing doesn't double-count)
    freshness_status     -> worst (most stale) across matched regions
    """
    if not rows:
        return {"population_affected": None, "freshness_status": None}

    populations = [r.population_affected for r in rows if r.population_affected is not None]
    population_affected = sum(populations) if populations else None

    freshness_values = [r.freshness_status for r in rows if r.freshness_status]
    freshness_status = min(freshness_values, key=lambda f: _FRESHNESS_RANK.get(f, 1)) if freshness_values else None

    return {"population_affected": population_affected, "freshness_status": freshness_status}


# ---------------------------------------------------------------------------
# Confidence derivation
# ---------------------------------------------------------------------------

def _derive_confidence(
    infra_agg: dict,
    total_reports: int,
    unique_contributors: int,
    evidence_scope: str,
) -> str:
    """
    Derive evidence confidence from available data signals.

    Rules (in order of precedence):
    1. No infrastructure data at any level -> NEEDS_VALIDATION
    2. Stale infrastructure data + low demand -> NEEDS_VALIDATION
    3. High coverage (infra says "High") + citizen demand -> LOW
       (official data contradicts the reported gap — needs validation)
    4. Recent data + high demand (>= 5 unique contributors) + low coverage
       + region-specific evidence -> HIGH
    5. Moderate signals -> MEDIUM
    6. Weak signals -> LOW

    A country-wide/broader-region fallback (evidence_scope == "broader_region")
    caps confidence at MEDIUM even when the other signals look strong — the
    evidence wasn't actually specific to this cluster's location, so treating
    it as HIGH-confidence local evidence would overstate what's known.
    """
    if infra_agg["official_coverage"] is None and infra_agg["freshness_status"] is None:
        return "NEEDS_VALIDATION"

    infra_fresh = infra_agg["freshness_status"] == "recent"
    infra_stale = infra_agg["freshness_status"] == "stale"
    coverage_low = infra_agg["official_coverage"] == "Low"
    coverage_high = infra_agg["official_coverage"] == "High"
    high_demand = unique_contributors >= 5
    any_demand = total_reports > 0

    # Stale data with no meaningful demand signal
    if infra_stale and not high_demand:
        return "NEEDS_VALIDATION"

    # Official coverage is high but citizens are complaining — contradiction
    if coverage_high and any_demand:
        return "LOW"

    # Strong positive case: recent data confirms low coverage + real demand
    # + evidence that's actually specific to this cluster's own region(s).
    if infra_fresh and coverage_low and high_demand and evidence_scope != "broader_region":
        return "HIGH"

    # Moderate case: some evidence on both sides
    if any_demand and (coverage_low or not coverage_high):
        base = "MEDIUM"
    else:
        base = "LOW"

    if evidence_scope == "broader_region" and base == "HIGH":
        base = "MEDIUM"  # unreachable today (HIGH already excluded above) but keeps the cap explicit

    return base
