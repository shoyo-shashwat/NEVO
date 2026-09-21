# tests/test_gap_assessment.py
#
# Unit tests for the pure aggregation/confidence logic in
# app/services/gap_assessment.py -- the DB-facing calculate() /
# _resolve_cluster_evidence() functions need a live Postgres+PostGIS session
# and are covered separately by manual/integration verification, but the
# aggregation rules (worst-case coverage, max distance, summed population,
# confidence derivation) are pure and fully testable here.

from dataclasses import dataclass

from app.services.gap_assessment import (
    _aggregate_demographics,
    _aggregate_infrastructure,
    _combine_scope,
    _derive_confidence,
)
from app.services.region_matching import ResolvedEvidence, RegionMatch


@dataclass
class FakeInfraRow:
    official_coverage: str | None
    nearest_facility_distance_km: float | None
    freshness_status: str | None
    source: str | None = None


@dataclass
class FakeDemoRow:
    population_affected: int | None
    freshness_status: str | None


# ---------------------------------------------------------------------------
# _aggregate_infrastructure
# ---------------------------------------------------------------------------

def test_aggregate_infrastructure_empty_returns_all_none():
    agg = _aggregate_infrastructure([])
    assert agg == {"official_coverage": None, "nearest_facility_km": None,
                    "freshness_status": None, "source": None}


def test_aggregate_infrastructure_takes_worst_case_coverage():
    rows = [
        FakeInfraRow(official_coverage="High", nearest_facility_distance_km=2.0, freshness_status="recent"),
        FakeInfraRow(official_coverage="Low", nearest_facility_distance_km=18.0, freshness_status="recent"),
    ]
    agg = _aggregate_infrastructure(rows)
    # "Low" is worse than "High" -- a cluster spanning a well-served and a
    # poorly-served district should not look well-served overall.
    assert agg["official_coverage"] == "Low"


def test_aggregate_infrastructure_takes_max_distance():
    rows = [
        FakeInfraRow(official_coverage="Medium", nearest_facility_distance_km=3.0, freshness_status="recent"),
        FakeInfraRow(official_coverage="Medium", nearest_facility_distance_km=22.5, freshness_status="recent"),
    ]
    agg = _aggregate_infrastructure(rows)
    assert agg["nearest_facility_km"] == 22.5


def test_aggregate_infrastructure_takes_worst_freshness():
    rows = [
        FakeInfraRow(official_coverage="Medium", nearest_facility_distance_km=5.0, freshness_status="recent"),
        FakeInfraRow(official_coverage="Medium", nearest_facility_distance_km=5.0, freshness_status="stale"),
    ]
    agg = _aggregate_infrastructure(rows)
    assert agg["freshness_status"] == "stale"


def test_aggregate_infrastructure_joins_distinct_sources():
    rows = [
        FakeInfraRow(official_coverage="Low", nearest_facility_distance_km=5.0, freshness_status="recent", source="MoHFW 2023"),
        FakeInfraRow(official_coverage="Low", nearest_facility_distance_km=5.0, freshness_status="recent", source="State Health Dept"),
    ]
    agg = _aggregate_infrastructure(rows)
    assert agg["source"] == "MoHFW 2023, State Health Dept"


# ---------------------------------------------------------------------------
# _aggregate_demographics
# ---------------------------------------------------------------------------

def test_aggregate_demographics_sums_population_across_regions():
    rows = [
        FakeDemoRow(population_affected=500_000, freshness_status="recent"),
        FakeDemoRow(population_affected=300_000, freshness_status="recent"),
        FakeDemoRow(population_affected=200_000, freshness_status="stale"),
    ]
    agg = _aggregate_demographics(rows)
    assert agg["population_affected"] == 1_000_000
    assert agg["freshness_status"] == "stale"


def test_aggregate_demographics_empty_returns_none():
    agg = _aggregate_demographics([])
    assert agg == {"population_affected": None, "freshness_status": None}


def test_aggregate_demographics_ignores_none_populations():
    rows = [FakeDemoRow(population_affected=None, freshness_status="recent"),
            FakeDemoRow(population_affected=100, freshness_status="recent")]
    agg = _aggregate_demographics(rows)
    assert agg["population_affected"] == 100


# ---------------------------------------------------------------------------
# _combine_scope
# ---------------------------------------------------------------------------

def _evidence(scope_matches):
    """Build a ResolvedEvidence whose .scope_label comes out as given, via matches."""
    return scope_matches


def test_combine_scope_picks_the_less_local_of_the_two():
    region_specific = ResolvedEvidence(matches=[
        RegionMatch(region_id="d1", matched_row=object(), matched_region_id="d1", is_fallback=False)
    ])
    broader = ResolvedEvidence(matches=[
        RegionMatch(region_id="d1", matched_row=object(), matched_region_id="state-1", is_fallback=True)
    ])
    assert region_specific.scope_label == "region_specific"
    assert broader.scope_label == "broader_region"
    assert _combine_scope(region_specific, broader) == "broader_region"


def test_combine_scope_both_region_specific():
    a = ResolvedEvidence(matches=[RegionMatch("d1", object(), "d1", False)])
    b = ResolvedEvidence(matches=[RegionMatch("d1", object(), "d1", False)])
    assert _combine_scope(a, b) == "region_specific"


def test_combine_scope_no_data_wins_over_everything():
    a = ResolvedEvidence(matches=[RegionMatch("d1", object(), "d1", False)])
    b = ResolvedEvidence(matches=[RegionMatch("d1", None, None, False)])
    assert _combine_scope(a, b) == "no_data"


# ---------------------------------------------------------------------------
# _derive_confidence
# ---------------------------------------------------------------------------

def test_derive_confidence_no_infra_data_needs_validation():
    infra_agg = {"official_coverage": None, "nearest_facility_km": None, "freshness_status": None, "source": None}
    assert _derive_confidence(infra_agg, total_reports=10, unique_contributors=8, evidence_scope="no_data") == "NEEDS_VALIDATION"


def test_derive_confidence_stale_data_low_demand_needs_validation():
    infra_agg = {"official_coverage": "Low", "nearest_facility_km": 10.0, "freshness_status": "stale", "source": "x"}
    assert _derive_confidence(infra_agg, total_reports=1, unique_contributors=1, evidence_scope="region_specific") == "NEEDS_VALIDATION"


def test_derive_confidence_high_coverage_with_demand_is_contradictory_low():
    infra_agg = {"official_coverage": "High", "nearest_facility_km": 1.0, "freshness_status": "recent", "source": "x"}
    assert _derive_confidence(infra_agg, total_reports=5, unique_contributors=5, evidence_scope="region_specific") == "LOW"


def test_derive_confidence_strong_case_is_high():
    infra_agg = {"official_coverage": "Low", "nearest_facility_km": 20.0, "freshness_status": "recent", "source": "x"}
    assert _derive_confidence(infra_agg, total_reports=50, unique_contributors=20, evidence_scope="region_specific") == "HIGH"


def test_derive_confidence_strong_signals_but_broader_region_evidence_capped_below_high():
    """The key new behavior: fallback/broader-region evidence must not be
    allowed to produce HIGH confidence, even with otherwise-strong demand
    signals, because it wasn't actually specific to this cluster's location."""
    infra_agg = {"official_coverage": "Low", "nearest_facility_km": 20.0, "freshness_status": "recent", "source": "x"}
    result = _derive_confidence(infra_agg, total_reports=50, unique_contributors=20, evidence_scope="broader_region")
    assert result != "HIGH"


def test_derive_confidence_weak_signals_is_low():
    # total_reports == 0 doesn't itself force NEEDS_VALIDATION at this layer
    # -- that "no reports -> NEEDS_VALIDATION" override is priority_scoring.
    # stage1_confidence()'s job (it has access to the cluster directly).
    # Here, with no demand and no coverage extremes, LOW is correct.
    infra_agg = {"official_coverage": "Medium", "nearest_facility_km": 5.0, "freshness_status": "recent", "source": "x"}
    assert _derive_confidence(infra_agg, total_reports=0, unique_contributors=0, evidence_scope="region_specific") == "LOW"
