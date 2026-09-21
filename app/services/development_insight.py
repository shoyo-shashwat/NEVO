# services/development_insight.py
#
# Computes a DevelopmentInsight — a short evidence-grounded narrative plus a
# list of potential interventions for a DemandCluster. Computed fresh, never
# persisted, same pattern as gap_assessment.py / investment_alignment.py /
# priority_scoring.py.
#
# Deliberately NOT a new LLM call. The narrative is composed from data the
# system has already computed (gap assessment, investment alignment,
# geographic spread) using fixed sentence templates, for three reasons:
#   1. It keeps the "evidence indicates a gap" discipline (Progress Log
#      §5.3.1 / Government §7) mechanically enforced rather than hoping a
#      model completion stays in-register every time.
#   2. No new AI provider dependency, no hallucination risk, no added
#      latency/cost on a page that's meant to be the product's centerpiece.
#   3. It's auditable: every sentence traces directly to a field already
#      shown elsewhere on the Intelligence Page.
#
# Potential interventions are a fixed per-category menu (Government §14 /
# the India-only MVP design review) — presented as OPTIONS for the
# government user to weigh, never as an automated recommendation. This
# mirrors investment_alignment.py's reasoning requirement: no unexplained
# output.

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.gap_assessment import InfrastructureGapAssessment
from app.services.investment_alignment import InvestmentAlignment


@dataclass
class DevelopmentInsight:
    demand_cluster_id: str
    narrative: str                          # 2-4 sentence evidence-grounded summary
    potential_interventions: list = field(default_factory=list)   # list[str], options not directives
    intervention_comparison: list = field(default_factory=list)   # list[dict] -- see _INTERVENTION_MENU
    geographic_spread: dict = field(default_factory=dict)         # {"region_count": int, "localities": list[str]}


# Fixed per-category intervention menu. Kept short and generic on purpose —
# these are the kinds of responses a government user would actually recognize
# for that sector, not project-specific plans (the system has no basis for
# proposing a specific site/budget/design).
#
# Each option also carries fixed comparison attributes (existing_infra_use /
# geographic_reach / complexity) -- these describe general, typical
# properties of that INTERVENTION TYPE (e.g. "establishing a new facility"
# inherently uses less existing infrastructure and has a wider potential
# reach than "upgrading an existing facility"), never a per-cluster
# projection. No cost, capacity, or population-served numbers are invented
# here -- ChatGPT's review flagged fabricated figures like "serves 80,000
# people" as exactly the kind of manufactured precision this codebase
# avoids elsewhere; a Planning/MP user sees these as qualitative comparison
# attributes only, explicitly labeled as general characteristics of the
# option type, not a projection for this specific cluster.
_INTERVENTION_MENU = {
    "healthcare_access": [
        {"label": "Upgrade the nearest existing facility", "existing_infra_use": "High", "geographic_reach": "Local", "complexity": "Low"},
        {"label": "Expand capacity or staffing at existing facilities", "existing_infra_use": "High", "geographic_reach": "Local", "complexity": "Medium"},
        {"label": "Establish an additional facility in the underserved area", "existing_infra_use": "Low", "geographic_reach": "Regional", "complexity": "High"},
        {"label": "Improve emergency/transport access to existing facilities", "existing_infra_use": "Medium", "geographic_reach": "Regional", "complexity": "Medium"},
    ],
    "water_sanitation": [
        {"label": "Expand or repair existing water supply infrastructure", "existing_infra_use": "High", "geographic_reach": "Local", "complexity": "Medium"},
        {"label": "Establish new water access points in the underserved area", "existing_infra_use": "Low", "geographic_reach": "Regional", "complexity": "High"},
        {"label": "Improve water quality monitoring and treatment", "existing_infra_use": "High", "geographic_reach": "Local", "complexity": "Low"},
    ],
    "roads_transport": [
        {"label": "Upgrade or repair the existing road network", "existing_infra_use": "High", "geographic_reach": "Local", "complexity": "Medium"},
        {"label": "Establish new road connectivity to the underserved area", "existing_infra_use": "Low", "geographic_reach": "Regional", "complexity": "High"},
        {"label": "Improve public transport frequency or coverage", "existing_infra_use": "High", "geographic_reach": "Regional", "complexity": "Low"},
    ],
    "electricity_utilities": [
        {"label": "Upgrade existing electrical infrastructure/capacity", "existing_infra_use": "High", "geographic_reach": "Local", "complexity": "Medium"},
        {"label": "Extend grid connectivity to the underserved area", "existing_infra_use": "Low", "geographic_reach": "Regional", "complexity": "High"},
        {"label": "Improve reliability of existing supply", "existing_infra_use": "High", "geographic_reach": "Local", "complexity": "Low"},
    ],
    "education_access": [
        {"label": "Expand capacity at existing educational facilities", "existing_infra_use": "High", "geographic_reach": "Local", "complexity": "Medium"},
        {"label": "Establish an additional facility in the underserved area", "existing_infra_use": "Low", "geographic_reach": "Regional", "complexity": "High"},
        {"label": "Improve transport access to existing facilities", "existing_infra_use": "Medium", "geographic_reach": "Regional", "complexity": "Low"},
    ],
    "waste_environment": [
        {"label": "Expand waste collection/management coverage", "existing_infra_use": "High", "geographic_reach": "Regional", "complexity": "Medium"},
        {"label": "Establish new waste management infrastructure", "existing_infra_use": "Low", "geographic_reach": "Regional", "complexity": "High"},
        {"label": "Improve enforcement of existing environmental standards", "existing_infra_use": "High", "geographic_reach": "Local", "complexity": "Low"},
    ],
}

_DEFAULT_INTERVENTIONS = [
    {"label": "Review the underlying evidence with the responsible department", "existing_infra_use": "—", "geographic_reach": "—", "complexity": "Low"},
    {"label": "Assess whether existing infrastructure can be expanded", "existing_infra_use": "High", "geographic_reach": "Local", "complexity": "Medium"},
]


def calculate(
    demand_cluster_id: str,
    gap: InfrastructureGapAssessment | None = None,
    alignment: InvestmentAlignment | None = None,
) -> DevelopmentInsight:
    """
    Compute a fresh DevelopmentInsight for the given DemandCluster.

    Accepts an already-computed gap/alignment (the evidence_detail view
    computes both anyway) to avoid recomputation; computes them fresh if not
    supplied.
    """
    from app.extensions import db
    from app.models.demand_cluster import DemandCluster
    from app.models.shared import Category, AdministrativeRegion
    import app.services.gap_assessment as gap_svc
    import app.services.investment_alignment as align_svc

    cluster = db.session.get(DemandCluster, demand_cluster_id)
    if cluster is None:
        raise ValueError(f"DemandCluster {demand_cluster_id!r} not found")

    if gap is None:
        gap = gap_svc.calculate(demand_cluster_id)
    if alignment is None:
        alignment = align_svc.calculate(demand_cluster_id, gap_assessment=gap)

    category = db.session.get(Category, cluster.category_id)
    category_name = category.name if category else "this category"

    region_ids = list(cluster.region_ids or [])
    region_names = []
    if region_ids:
        regions = db.session.query(AdministrativeRegion).filter(
            AdministrativeRegion.id.in_(region_ids)
        ).all()
        region_names = [r.name for r in regions]

    geographic_spread = {
        "region_count": len(region_ids),
        "region_names": region_names,
        "localities": list(cluster.affected_localities or []),
    }

    narrative = _compose_narrative(
        category_name=category_name,
        cluster=cluster,
        gap=gap,
        alignment=alignment,
        geographic_spread=geographic_spread,
    )

    intervention_comparison = _INTERVENTION_MENU.get(
        category.code if category else None, _DEFAULT_INTERVENTIONS
    )
    potential_interventions = [opt["label"] for opt in intervention_comparison]

    return DevelopmentInsight(
        demand_cluster_id=demand_cluster_id,
        narrative=narrative,
        potential_interventions=potential_interventions,
        intervention_comparison=intervention_comparison,
        geographic_spread=geographic_spread,
    )


def _compose_narrative(category_name: str, cluster, gap: InfrastructureGapAssessment,
                        alignment: InvestmentAlignment, geographic_spread: dict) -> str:
    """
    Pure sentence composition -- every clause traces to a field already
    displayed elsewhere on the page. No invented claims.
    """
    sentences = []

    # Sentence 1 -- who / where / how much
    spread = geographic_spread["region_count"]
    place = ""
    if spread == 1:
        place = f" in {geographic_spread['region_names'][0]}" if geographic_spread["region_names"] else ""
    elif spread > 1:
        place = f" across {spread} administrative regions"

    citizen_word = "citizen" if cluster.unique_contributors == 1 else "citizens"
    report_word = "report" if cluster.total_reports == 1 else "reports"
    verb = "has" if cluster.unique_contributors == 1 else "have"
    sentences.append(
        f"{cluster.unique_contributors} {citizen_word} ({cluster.total_reports} {report_word}) {verb} raised "
        f"{category_name.lower()} concerns{place}."
    )

    # Sentence 2 -- infrastructure evidence, scoped honestly
    if gap.official_coverage:
        scope_phrase = {
            "region_specific": "",
            "mixed": ", based on a mix of region-specific and broader-region data",
            "broader_region": ", based on broader-region data rather than this area specifically",
            "no_data": "",
        }.get(gap.evidence_scope, "")
        sentences.append(
            f"Evidence indicates {gap.official_coverage.lower()} official infrastructure coverage"
            f"{scope_phrase}."
        )
    else:
        sentences.append("No official infrastructure coverage data is currently available for this area.")

    # Sentence 3 -- population context
    if gap.population_affected:
        sentences.append(f"An estimated {gap.population_affected:,} people live in the affected area.")

    # Sentence 4 -- investment alignment, in plain language
    alignment_phrases = {
        "UNADDRESSED": "no relevant government investment has been identified for this area",
        "PARTIALLY_ADDRESSED": "existing investment appears insufficient for the scale of the identified gap",
        "ALIGNED": "existing or recent investment appears relevant to this demand",
        "IMPLEMENTATION_ACCESS_GAP": "an active investment exists, but demand is still increasing — coverage may not be reaching this area",
        "EMERGING_GAP": "demand is increasing while existing interventions appear inactive or completed",
    }
    sentences.append(
        f"On investment: {alignment_phrases.get(alignment.state, alignment.state.lower())}."
    )

    return " ".join(sentences)
