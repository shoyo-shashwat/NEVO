# tests/test_investment_alignment.py
#
# Unit tests for the pure region-filtering and alignment-derivation logic in
# app/services/investment_alignment.py -- targeting the same class of bug as
# gap_assessment.py: GovernmentInvestment was previously matched by
# category_id + country_id ONLY, so an investment on the other side of the
# country could make an unrelated cluster look "ALIGNED".

from dataclasses import dataclass, field

from app.services.investment_alignment import _derive_alignment, _filter_relevant_investments


@dataclass
class FakeInvestment:
    id: str
    name: str
    region_id: object
    status: str = "active"
    freshness_status: str = "recent"


@dataclass
class FakeCluster:
    trend: str = "stable"


@dataclass
class FakeGap:
    citizen_demand_present: bool = True
    confidence: str = "HIGH"


# ---------------------------------------------------------------------------
# _filter_relevant_investments
# ---------------------------------------------------------------------------

def test_investment_in_unrelated_region_is_excluded():
    """The core bug: an investment scoped to a completely different region
    must not be treated as relevant just because category+country match."""
    candidates = [
        FakeInvestment(id="1", name="District A Clinic Upgrade", region_id="district-A"),
        FakeInvestment(id="2", name="District Z Clinic Upgrade", region_id="district-Z"),
    ]
    chains = {"district-A": ["district-A", "state-1", None]}

    relevant, scope = _filter_relevant_investments(candidates, region_ids=["district-A"], chains=chains)

    assert [inv.id for inv in relevant] == ["1"]
    assert scope == "region_specific"


def test_state_wide_investment_covers_cluster_as_broader_region():
    candidates = [FakeInvestment(id="1", name="State Healthcare Scheme", region_id="state-1")]
    chains = {"district-A": ["district-A", "state-1", None]}

    relevant, scope = _filter_relevant_investments(candidates, region_ids=["district-A"], chains=chains)

    assert [inv.id for inv in relevant] == ["1"]
    assert scope == "broader_region"


def test_mixed_native_and_broader_investments():
    candidates = [
        FakeInvestment(id="1", name="District Clinic", region_id="district-A"),
        FakeInvestment(id="2", name="State Scheme", region_id="state-1"),
    ]
    chains = {"district-A": ["district-A", "state-1", None]}

    relevant, scope = _filter_relevant_investments(candidates, region_ids=["district-A"], chains=chains)

    assert {inv.id for inv in relevant} == {"1", "2"}
    assert scope == "mixed"


def test_country_wide_investment_is_relevant_but_broader():
    candidates = [FakeInvestment(id="1", name="National Scheme", region_id=None)]
    chains = {"district-A": ["district-A", "state-1", None]}

    relevant, scope = _filter_relevant_investments(candidates, region_ids=["district-A"], chains=chains)

    assert [inv.id for inv in relevant] == ["1"]
    assert scope == "broader_region"


def test_no_relevant_investment_returns_no_data():
    candidates = [FakeInvestment(id="1", name="District Z Clinic", region_id="district-Z")]
    chains = {"district-A": ["district-A", "state-1", None]}

    relevant, scope = _filter_relevant_investments(candidates, region_ids=["district-A"], chains=chains)

    assert relevant == []
    assert scope == "no_data"


def test_cluster_with_no_region_only_matches_country_wide():
    candidates = [
        FakeInvestment(id="1", name="District Clinic", region_id="district-A"),
        FakeInvestment(id="2", name="National Scheme", region_id=None),
    ]
    relevant, scope = _filter_relevant_investments(candidates, region_ids=[], chains={})
    assert [inv.id for inv in relevant] == ["2"]
    assert scope == "broader_region"


def test_multi_region_cluster_pulls_investments_from_all_its_regions():
    candidates = [
        FakeInvestment(id="1", name="District A Clinic", region_id="district-A"),
        FakeInvestment(id="2", name="District B Clinic", region_id="district-B"),
        FakeInvestment(id="3", name="Unrelated District Clinic", region_id="district-Z"),
    ]
    chains = {
        "district-A": ["district-A", "state-1", None],
        "district-B": ["district-B", "state-1", None],
    }
    relevant, scope = _filter_relevant_investments(candidates, region_ids=["district-A", "district-B"], chains=chains)
    assert {inv.id for inv in relevant} == {"1", "2"}
    assert scope == "region_specific"


# ---------------------------------------------------------------------------
# _derive_alignment
# ---------------------------------------------------------------------------

def test_no_investments_with_confirmed_gap_is_unaddressed():
    state, reasoning = _derive_alignment(
        cluster=FakeCluster(trend="stable"),
        gap=FakeGap(citizen_demand_present=True, confidence="HIGH"),
        investments=[],
        investment_scope="no_data",
    )
    assert state == "UNADDRESSED"


def test_increasing_trend_with_active_investment_is_implementation_gap():
    inv = [FakeInvestment(id="1", name="Clinic Upgrade", region_id="district-A", status="active")]
    state, reasoning = _derive_alignment(
        cluster=FakeCluster(trend="increasing"),
        gap=FakeGap(),
        investments=inv,
        investment_scope="region_specific",
    )
    assert state == "IMPLEMENTATION_ACCESS_GAP"


def test_broader_region_scope_is_noted_in_reasoning_text():
    inv = [FakeInvestment(id="1", name="State Scheme", region_id="state-1", status="active")]
    state, reasoning = _derive_alignment(
        cluster=FakeCluster(trend="stable"),
        gap=FakeGap(confidence="MEDIUM"),
        investments=inv,
        investment_scope="broader_region",
    )
    assert "broader-region" in reasoning or "state-wide" in reasoning
