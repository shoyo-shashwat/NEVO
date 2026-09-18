# tests/test_development_insight.py
#
# Unit tests for the pure narrative-composition logic in
# app/services/development_insight.py. Every sentence must trace to a field
# already present on gap/alignment -- these tests check the composition
# doesn't invent claims the evidence doesn't support and stays in the
# "evidence indicates" register (never "AI has determined" / "proven").

from dataclasses import dataclass

from app.services.development_insight import _compose_narrative, _INTERVENTION_MENU, _DEFAULT_INTERVENTIONS


@dataclass
class FakeCluster:
    unique_contributors: int
    total_reports: int


@dataclass
class FakeGap:
    official_coverage: str | None
    population_affected: int | None
    evidence_scope: str


@dataclass
class FakeAlignment:
    state: str


def test_narrative_includes_contributor_and_report_counts():
    narrative = _compose_narrative(
        category_name="Healthcare Access",
        cluster=FakeCluster(unique_contributors=2843, total_reports=384),
        gap=FakeGap(official_coverage="Low", population_affected=84000, evidence_scope="region_specific"),
        alignment=FakeAlignment(state="UNADDRESSED"),
        geographic_spread={"region_count": 1, "region_names": ["Nashik District"], "localities": []},
    )
    assert "2843" in narrative
    assert "384" in narrative
    assert "Nashik District" in narrative


def test_narrative_never_claims_proof_only_evidence_indicates():
    narrative = _compose_narrative(
        category_name="Water Supply",
        cluster=FakeCluster(unique_contributors=10, total_reports=15),
        gap=FakeGap(official_coverage="Low", population_affected=1000, evidence_scope="region_specific"),
        alignment=FakeAlignment(state="UNADDRESSED"),
        geographic_spread={"region_count": 1, "region_names": ["District X"], "localities": []},
    )
    assert "evidence indicates" in narrative.lower()
    assert "proven" not in narrative.lower()
    assert "ai has determined" not in narrative.lower()


def test_narrative_flags_broader_region_evidence_honestly():
    narrative = _compose_narrative(
        category_name="Roads",
        cluster=FakeCluster(unique_contributors=5, total_reports=5),
        gap=FakeGap(official_coverage="Medium", population_affected=None, evidence_scope="broader_region"),
        alignment=FakeAlignment(state="ALIGNED"),
        geographic_spread={"region_count": 1, "region_names": ["District Y"], "localities": []},
    )
    assert "broader-region data" in narrative


def test_narrative_omits_population_sentence_when_unavailable():
    narrative = _compose_narrative(
        category_name="Education",
        cluster=FakeCluster(unique_contributors=3, total_reports=3),
        gap=FakeGap(official_coverage=None, population_affected=None, evidence_scope="no_data"),
        alignment=FakeAlignment(state="UNADDRESSED"),
        geographic_spread={"region_count": 0, "region_names": [], "localities": []},
    )
    assert "live in the affected area" not in narrative
    assert "No official infrastructure coverage data" in narrative


def test_narrative_singular_citizen_and_report_grammar():
    """Regression test: '1 citizens (1 reports) have raised' read as an
    obvious grammar bug on low-volume clusters (a real, common case -- most
    newly-formed clusters start with exactly one contributor)."""
    narrative = _compose_narrative(
        category_name="Water Supply",
        cluster=FakeCluster(unique_contributors=1, total_reports=1),
        gap=FakeGap(official_coverage="Low", population_affected=None, evidence_scope="region_specific"),
        alignment=FakeAlignment(state="UNADDRESSED"),
        geographic_spread={"region_count": 1, "region_names": ["District X"], "localities": []},
    )
    assert "1 citizen (" in narrative
    assert "1 citizens" not in narrative
    assert "1 report)" in narrative
    assert "1 reports)" not in narrative
    assert "1 citizen (1 report) has raised" in narrative


def test_narrative_multi_region_spread_phrasing():
    narrative = _compose_narrative(
        category_name="Healthcare Access",
        cluster=FakeCluster(unique_contributors=100, total_reports=100),
        gap=FakeGap(official_coverage="Low", population_affected=50000, evidence_scope="region_specific"),
        alignment=FakeAlignment(state="UNADDRESSED"),
        geographic_spread={"region_count": 3, "region_names": ["A", "B", "C"], "localities": []},
    )
    assert "across 3 administrative regions" in narrative


def test_intervention_menu_covers_every_mvp_category():
    expected_categories = {
        "healthcare_access", "water_sanitation", "roads_transport",
        "electricity_utilities", "education_access", "waste_environment",
    }
    assert expected_categories == set(_INTERVENTION_MENU.keys())
    for category, interventions in _INTERVENTION_MENU.items():
        assert len(interventions) >= 2, category


def test_intervention_menu_options_carry_comparison_attributes_not_invented_numbers():
    """Every option must expose the fixed qualitative comparison attributes
    (never a cost/budget/population-served number, which the system has no
    basis to project per-cluster)."""
    for category, interventions in _INTERVENTION_MENU.items():
        for opt in interventions:
            assert set(opt.keys()) == {"label", "existing_infra_use", "geographic_reach", "complexity"}, category
            for forbidden in ("cost", "budget", "population_served", "roi"):
                assert forbidden not in opt, f"{category}: {opt['label']} has a fabricated {forbidden} field"


def test_default_interventions_never_prescribe_a_specific_build():
    for opt in _DEFAULT_INTERVENTIONS:
        assert "build" not in opt["label"].lower()
