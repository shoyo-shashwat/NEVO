# tests/test_region_matching.py
#
# Regression tests for app/services/region_matching.py -- specifically
# targeting the bug where gap_assessment.py / investment_alignment.py
# matched reference data by category+country only, ignoring geography
# entirely (two clusters on opposite sides of the country got identical
# evidence). Each test below maps to a scenario from the fix's design
# discussion.
#
# No Flask app, no DB session -- region_matching.py is pure Python, so these
# rows are simple stand-ins with just a `.region_id` attribute, not real
# SQLAlchemy models.

from dataclasses import dataclass

import pytest

from app.services.region_matching import (
    build_ancestor_chain,
    build_ancestor_chains,
    resolve_region_matches,
)


@dataclass
class FakeRow:
    """Stand-in for InfrastructureDataPoint / DemographicDataPoint / GovernmentInvestment."""
    region_id: object       # None means country-wide
    label: str              # test-only identifier so assertions can tell rows apart


# ---------------------------------------------------------------------------
# build_ancestor_chain
# ---------------------------------------------------------------------------

def test_ancestor_chain_walks_up_to_country_wide():
    parent_lookup = {
        "constituency-1": "district-1",
        "district-1": "state-1",
        "state-1": "national-1",
        "national-1": None,
    }
    chain = build_ancestor_chain("constituency-1", parent_lookup)
    assert chain == ["constituency-1", "district-1", "state-1", "national-1", None]


def test_ancestor_chain_region_with_no_parent_still_ends_with_country_fallback():
    chain = build_ancestor_chain("national-1", {"national-1": None})
    assert chain == ["national-1", None]


def test_ancestor_chain_guards_against_cycles():
    # Malformed data: A's parent is B, B's parent is A. Must not infinite-loop.
    parent_lookup = {"region-a": "region-b", "region-b": "region-a"}
    chain = build_ancestor_chain("region-a", parent_lookup)
    assert chain == ["region-a", "region-b", None]


def test_build_ancestor_chains_covers_every_input_region():
    parent_lookup = {"d1": "s1", "s1": None, "d2": "s1"}
    chains = build_ancestor_chains(["d1", "d2"], parent_lookup)
    assert chains["d1"] == ["d1", "s1", None]
    assert chains["d2"] == ["d2", "s1", None]


# ---------------------------------------------------------------------------
# resolve_region_matches
# ---------------------------------------------------------------------------

def test_same_category_different_districts_get_different_evidence():
    """Test 1 -- the core bug. Two clusters in different districts must not
    silently receive the same evidence row."""
    candidates = [
        FakeRow(region_id="district-A", label="A-row"),
        FakeRow(region_id="district-B", label="B-row"),
    ]
    chains = {
        "district-A": ["district-A", "state-1", None],
        "district-B": ["district-B", "state-1", None],
    }

    result_a = resolve_region_matches(["district-A"], candidates, chains)
    result_b = resolve_region_matches(["district-B"], candidates, chains)

    assert result_a.matched_rows[0].label == "A-row"
    assert result_b.matched_rows[0].label == "B-row"
    assert result_a.matched_rows[0] is not result_b.matched_rows[0]
    assert result_a.scope_label == "region_specific"
    assert result_b.scope_label == "region_specific"


def test_multi_region_cluster_incorporates_all_regions_evidence():
    """Test 2 -- a cluster spanning multiple regions must not just pick one
    'best' region and discard the rest."""
    candidates = [
        FakeRow(region_id="district-A", label="A-row"),
        FakeRow(region_id="district-B", label="B-row"),
    ]
    chains = {
        "district-A": ["district-A", "state-1", None],
        "district-B": ["district-B", "state-1", None],
    }

    result = resolve_region_matches(["district-A", "district-B"], candidates, chains)

    labels = {row.label for row in result.matched_rows}
    assert labels == {"A-row", "B-row"}
    assert result.matched_count == 2
    assert result.native_match_count == 2
    assert result.fallback_used is False


def test_missing_regional_evidence_falls_back_and_is_flagged():
    """Test 3 -- one region has native data, another doesn't and must fall
    back, with the fallback explicitly flagged (not silently blended in)."""
    candidates = [
        FakeRow(region_id="district-A", label="A-district-row"),
        FakeRow(region_id="state-1", label="state-row"),
    ]
    chains = {
        "district-A": ["district-A", "state-1", None],
        "district-B": ["district-B", "state-1", None],  # no district-B row exists
    }

    result = resolve_region_matches(["district-A", "district-B"], candidates, chains)

    match_a = next(m for m in result.matches if m.region_id == "district-A")
    match_b = next(m for m in result.matches if m.region_id == "district-B")

    assert match_a.matched_row.label == "A-district-row"
    assert match_a.is_fallback is False

    assert match_b.matched_row.label == "state-row"
    assert match_b.matched_region_id == "state-1"
    assert match_b.is_fallback is True

    assert result.fallback_used is True
    assert result.scope_label == "mixed"


def test_state_level_fallback_when_no_district_data_exists():
    """Test 4."""
    candidates = [FakeRow(region_id="state-1", label="state-row")]
    chains = {"district-A": ["district-A", "state-1", None]}

    result = resolve_region_matches(["district-A"], candidates, chains)

    assert result.matched_rows[0].label == "state-row"
    assert result.matches[0].is_fallback is True
    assert result.scope_label == "broader_region"


def test_country_wide_fallback_when_no_regional_or_state_data_exists():
    """Test 5 -- must fall back to the country-wide row (region_id=None) and
    flag it as a fallback, never presented as if it were local evidence."""
    candidates = [FakeRow(region_id=None, label="country-row")]
    chains = {"district-A": ["district-A", "state-1", "national-1", None]}

    result = resolve_region_matches(["district-A"], candidates, chains)

    assert result.matched_rows[0].label == "country-row"
    assert result.matches[0].matched_region_id is None
    assert result.matches[0].is_fallback is True
    assert result.scope_label == "broader_region"


def test_no_evidence_at_any_level_is_unmatched_not_a_crash():
    result = resolve_region_matches(["district-A"], [], {"district-A": ["district-A", None]})

    assert result.matched_rows == []
    assert result.unmatched_region_ids == ["district-A"]
    assert result.scope_label == "no_data"
    assert result.coverage_fraction == 0.0


def test_category_isolation_is_the_callers_responsibility_via_candidate_filtering():
    """Test 6 -- region_matching.py has no concept of category; isolation is
    guaranteed by the caller only ever passing already category-filtered
    candidates (verified here by simulating an unfiltered candidate list
    slipping through and confirming region_matching just matches on
    region_id blindly -- i.e. the caller MUST filter by category first)."""
    candidates = [
        FakeRow(region_id="district-A", label="healthcare-row"),
        FakeRow(region_id="district-A", label="education-row"),  # wrong category, same region
    ]
    chains = {"district-A": ["district-A", None]}

    result = resolve_region_matches(["district-A"], candidates, chains)

    # Without category pre-filtering, region_matching can't distinguish these --
    # it takes whichever candidate for that region appears first. This is why
    # callers (gap_assessment.py / investment_alignment.py) must filter by
    # category_id before calling resolve_region_matches.
    assert result.matched_rows[0].label == "healthcare-row"


def test_coverage_fraction_reflects_partial_matching():
    candidates = [FakeRow(region_id="district-A", label="A-row")]
    chains = {
        "district-A": ["district-A", None],
        "district-B": ["district-B", None],
    }
    result = resolve_region_matches(["district-A", "district-B"], candidates, chains)
    assert result.coverage_fraction == 0.5


def test_empty_region_ids_resolves_to_no_matches():
    result = resolve_region_matches([], [FakeRow(region_id=None, label="x")], {})
    assert result.matches == []
    assert result.scope_label == "no_data"
    assert result.coverage_fraction == 0.0
