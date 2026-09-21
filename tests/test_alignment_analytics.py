# tests/test_alignment_analytics.py
#
# Unit tests for the pure aggregation logic in
# app/services/alignment_analytics.py -- the population-based five-state
# breakdown ("Population represented by identified demand"). Deliberately
# checks that the five states are always reported separately, never
# collapsed into a single "coverage score", and that clusters without
# population data are excluded from percentages but still counted.

from app.services.alignment_analytics import ALIGNMENT_STATES, compute_breakdown


def test_empty_results_returns_zeroed_breakdown():
    breakdown = compute_breakdown([], scope_label="All India")
    assert breakdown.cluster_count == 0
    assert breakdown.total_population == 0
    assert breakdown.potentially_addressed_pct == 0.0
    assert all(v == 0.0 for v in breakdown.by_state_pct.values())


def test_percentages_computed_correctly():
    results = [
        (500_000, "ALIGNED"),
        (300_000, "PARTIALLY_ADDRESSED"),
        (200_000, "UNADDRESSED"),
    ]
    breakdown = compute_breakdown(results, scope_label="Maharashtra")
    assert breakdown.total_population == 1_000_000
    assert breakdown.by_state_pct["ALIGNED"] == 50.0
    assert breakdown.by_state_pct["PARTIALLY_ADDRESSED"] == 30.0
    assert breakdown.by_state_pct["UNADDRESSED"] == 20.0
    assert breakdown.by_state_pct["IMPLEMENTATION_ACCESS_GAP"] == 0.0
    assert breakdown.by_state_pct["EMERGING_GAP"] == 0.0


def test_all_five_states_always_present_even_if_unused():
    results = [(100, "ALIGNED")]
    breakdown = compute_breakdown(results)
    assert set(breakdown.by_state_pct.keys()) == set(ALIGNMENT_STATES)
    assert set(breakdown.by_state_population.keys()) == set(ALIGNMENT_STATES)


def test_potentially_addressed_combines_aligned_and_partially_addressed_only():
    results = [
        (400_000, "ALIGNED"),
        (200_000, "PARTIALLY_ADDRESSED"),
        (400_000, "IMPLEMENTATION_ACCESS_GAP"),  # must NOT count as "potentially addressed"
    ]
    breakdown = compute_breakdown(results)
    assert breakdown.potentially_addressed_pct == 60.0  # (400k+200k)/1M


def test_clusters_without_population_data_excluded_from_percentages_but_counted():
    results = [
        (100_000, "ALIGNED"),
        (None, "UNADDRESSED"),   # no population data -- must not silently zero out the denominator
    ]
    breakdown = compute_breakdown(results)
    assert breakdown.cluster_count == 2
    assert breakdown.clusters_with_population_data == 1
    assert breakdown.total_population == 100_000
    assert breakdown.by_state_pct["ALIGNED"] == 100.0


def test_scope_label_is_passed_through():
    breakdown = compute_breakdown([(100, "ALIGNED")], scope_label="Nashik District")
    assert breakdown.scope_label == "Nashik District"


def test_methodology_text_mentions_exclusion_of_missing_population_data():
    breakdown = compute_breakdown([])
    assert "excluded" in breakdown.methodology.lower()


def test_not_addressed_pct_includes_implementation_access_gap_not_just_unaddressed():
    """Regression test: a UI label that showed only the UNADDRESSED state's
    percentage was misleading when IMPLEMENTATION_ACCESS_GAP or EMERGING_GAP
    dominated instead (e.g. 100% Implementation Access Gap displayed as
    "0.0% unaddressed", implying nothing needed attention)."""
    breakdown = compute_breakdown([(100_000, "IMPLEMENTATION_ACCESS_GAP")])
    assert breakdown.by_state_pct["UNADDRESSED"] == 0.0
    assert breakdown.not_addressed_pct == 100.0


def test_not_addressed_pct_is_complement_of_potentially_addressed():
    breakdown = compute_breakdown([
        (300_000, "ALIGNED"),
        (200_000, "PARTIALLY_ADDRESSED"),
        (500_000, "UNADDRESSED"),
    ])
    assert breakdown.potentially_addressed_pct == 50.0
    assert breakdown.not_addressed_pct == 50.0


def test_not_addressed_pct_zero_when_no_population_data():
    breakdown = compute_breakdown([])
    assert breakdown.not_addressed_pct == 0.0
