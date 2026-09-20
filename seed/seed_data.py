# seed/seed_data.py
#
# Seed script for BRICS People First MVP demo data.
#
# Run from the project root:
#   python seed/seed_data.py
#
# Idempotent — checks for existing rows before inserting.
# Safe to re-run; will skip rows that already exist.
#
# What this seeds:
#   1. Countries (India, Brazil, Russia)
#   2. AdministrativeRegions (one state + one district per country)
#   3. Categories (6 fixed MVP categories)
#   4. Departments (India: health/water/pwd/education/environment)
#   5. Demo accounts — real CitizenAccount/GovernmentAccount rows,
#      is_demo=True, reachable via the one-click /login country-card picker
#      (see app/auth/actors.py::DEMO_ACCOUNTS)
#   6. Reference data (InfrastructureDataPoint + DemographicDataPoint +
#      GovernmentInvestment rows for India/healthcare — enough for the
#      Priority Evidence Card to render)
#   7. REQUIRED DEMO BEAT (Progress Log §10 / §13.2):
#      One seeded DemandCluster for India/healthcare with:
#        activeStatus = "UnderGovernmentReview"
#        ~18 Verification rows of which 15 are "StillHappening"
#        → community_sentiment yields ~83% still affected
#      This produces the flagship "82% still affected / Under Review" moment.

import os
import sys
import uuid
from datetime import date, datetime, timezone

# Allow running as a script from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app import create_app
from app.extensions import db
from app.models.shared import Country, AdministrativeRegion, Category, EventLog
from app.models.citizen_models import Report, Contribution, Verification, Evidence
from app.models.demand_cluster import DemandCluster
from app.models.government_models import GovernmentDecision, Project, Outcome
from app.models.reference_data import (
    InfrastructureDataPoint,
    DemographicDataPoint,
    GovernmentInvestment,
)
from app.models.auth_models import CitizenAccount, GovernmentAccount, Department
from app.models.mplads_models import MpladsStateSummary
from app.auth.security import hash_password

app = create_app()


# ---------------------------------------------------------------------------
# Stable IDs — hardcoded so re-runs stay idempotent and demo beats are
# reproducible.  Using short readable strings instead of random UUIDs.
# ---------------------------------------------------------------------------

# Countries
ID_COUNTRY_IN = "country-in"
ID_COUNTRY_BR = "country-br"
ID_COUNTRY_RU = "country-ru"

# Regions (state level)
ID_REGION_IN_MH  = "region-in-mh"      # Maharashtra, India
ID_REGION_IN_DL  = "region-in-dl"      # Delhi, India
ID_REGION_BR_SP  = "region-br-sp"      # São Paulo, Brazil
ID_REGION_BR_RJ  = "region-br-rj"      # Rio de Janeiro, Brazil
ID_REGION_RU_MSK = "region-ru-msk"     # Moscow Oblast, Russia
ID_REGION_RU_SPB = "region-ru-spb"     # Saint Petersburg, Russia

# Regions (district/city level) — Finding 3 fix (Citizen Report Flow Audit).
# Seeded as children of the state-level rows above via parent_region_id so
# the ILIKE fuzzy match in citizen/routes.py::report_flow() has realistic
# city/district targets instead of only two state names per country.
ID_REGION_IN_MH_NASHIK = "region-in-mh-nashik"     # required demo beat locality
ID_REGION_IN_MH_SINNAR = "region-in-mh-sinnar"     # required demo beat locality
ID_REGION_IN_MH_YEOLA  = "region-in-mh-yeola"      # required demo beat locality
ID_REGION_IN_MH_PUNE   = "region-in-mh-pune"
ID_REGION_IN_DL_SOUTH  = "region-in-dl-south"
ID_REGION_BR_SP_CAPITAL = "region-br-sp-capital"
ID_REGION_BR_SP_CAMPINAS = "region-br-sp-campinas"
ID_REGION_BR_RJ_CAPITAL  = "region-br-rj-capital"
ID_REGION_RU_MSK_KHIMKI  = "region-ru-msk-khimki"
ID_REGION_RU_SPB_CENTRAL = "region-ru-spb-central"

# Categories
ID_CAT_HEALTH   = "cat-healthcare"
ID_CAT_WATER    = "cat-water"
ID_CAT_ROADS    = "cat-roads"
ID_CAT_ELEC     = "cat-electricity"
ID_CAT_EDU      = "cat-education"
ID_CAT_WASTE    = "cat-waste"

# Demo actors
ID_CITIZEN_IN  = "actor-citizen-in"
ID_MP_IN       = "actor-mp-in"
ID_PO_IN       = "actor-po-in"
ID_CITIZEN_BR  = "actor-citizen-br"
ID_MP_BR       = "actor-mp-br"
ID_PO_BR       = "actor-po-br"
ID_CITIZEN_RU  = "actor-citizen-ru"
ID_MP_RU       = "actor-mp-ru"
ID_PO_RU       = "actor-po-ru"
ID_ADMIN       = "actor-admin"

# Demo beat cluster
ID_CLUSTER_DEMO = "cluster-demo-india-health"

# Shared password for every seeded is_demo=True account. Not a secret in any
# meaningful sense — these are reviewer accounts on demo data, reachable
# only through the one-click /login country-card picker anyway (real accounts
# can never authenticate with a password through that path — see
# app/auth/session.py::set_demo_session). Documented in README.md.
DEMO_ACTOR_PASSWORD = "DemoPass!2026"

_COUNTRY_ID_BY_CODE = {
    "IN": ID_COUNTRY_IN,
    "BR": ID_COUNTRY_BR,
    "RU": ID_COUNTRY_RU,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _exists(model, id_val):
    return db.session.get(model, id_val) is not None


def _now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------

def seed_countries():
    rows = [
        Country(
            id=ID_COUNTRY_IN, code="IN", name="India",
            supported_languages="hi,en",
            administrative_hierarchy_adapter="india_v1",
            status="active",
        ),
        # BR/RU kept as inactive, not deleted — India-only product scope for
        # now (2026), but demo accounts/data for these already exist and
        # dropping the rows would cascade-delete them. status="inactive"
        # removes them from every active user-facing picker (signup's
        # Country.query.filter_by(status="active") is the only place that
        # reads this field) without touching existing FKs.
        Country(
            id=ID_COUNTRY_BR, code="BR", name="Brazil",
            supported_languages="pt,en",
            administrative_hierarchy_adapter="brazil_v1",
            status="inactive",
        ),
        Country(
            id=ID_COUNTRY_RU, code="RU", name="Russia",
            supported_languages="ru,en",
            administrative_hierarchy_adapter="russia_v1",
            status="inactive",
        ),
    ]
    for r in rows:
        if not _exists(Country, r.id):
            db.session.add(r)
    db.session.flush()
    print("  Countries: OK")


def seed_regions():
    rows = [
        AdministrativeRegion(
            id=ID_REGION_IN_MH, country_id=ID_COUNTRY_IN,
            name="Maharashtra", level="state_province",
        ),
        AdministrativeRegion(
            id=ID_REGION_IN_DL, country_id=ID_COUNTRY_IN,
            name="Delhi", level="state_province",
        ),
        AdministrativeRegion(
            id=ID_REGION_BR_SP, country_id=ID_COUNTRY_BR,
            name="São Paulo", level="state_province",
        ),
        AdministrativeRegion(
            id=ID_REGION_BR_RJ, country_id=ID_COUNTRY_BR,
            name="Rio de Janeiro", level="state_province",
        ),
        AdministrativeRegion(
            id=ID_REGION_RU_MSK, country_id=ID_COUNTRY_RU,
            name="Moscow Oblast", level="state_province",
        ),
        AdministrativeRegion(
            id=ID_REGION_RU_SPB, country_id=ID_COUNTRY_RU,
            name="Saint Petersburg", level="state_province",
        ),
    ]
    for r in rows:
        if not _exists(AdministrativeRegion, r.id):
            db.session.add(r)
    db.session.flush()
    print("  Regions: OK")


def seed_district_regions():
    """
    Finding 3 (Citizen Report Flow Audit) — Option A: seed finer-grained
    district/city-level AdministrativeRegion rows as children of the
    state-level rows above, using the existing parent_region_id
    self-reference. Without these, the ILIKE fuzzy match in
    citizen/routes.py::report_flow() against only two state names per
    country silently fails to resolve a region for almost any real
    city/district input (e.g. "Nashik", "Sinnar Block").

    Must run after seed_regions() — depends on the state-level parent rows.
    """
    rows = [
        # India / Maharashtra — required demo beat localities (Progress Log §10)
        AdministrativeRegion(
            id=ID_REGION_IN_MH_NASHIK, country_id=ID_COUNTRY_IN,
            name="Nashik", level="district_municipality",
            parent_region_id=ID_REGION_IN_MH,
        ),
        AdministrativeRegion(
            id=ID_REGION_IN_MH_SINNAR, country_id=ID_COUNTRY_IN,
            name="Sinnar", level="district_municipality",
            parent_region_id=ID_REGION_IN_MH,
        ),
        AdministrativeRegion(
            id=ID_REGION_IN_MH_YEOLA, country_id=ID_COUNTRY_IN,
            name="Yeola", level="district_municipality",
            parent_region_id=ID_REGION_IN_MH,
        ),
        AdministrativeRegion(
            id=ID_REGION_IN_MH_PUNE, country_id=ID_COUNTRY_IN,
            name="Pune", level="district_municipality",
            parent_region_id=ID_REGION_IN_MH,
        ),
        AdministrativeRegion(
            id=ID_REGION_IN_DL_SOUTH, country_id=ID_COUNTRY_IN,
            name="South Delhi", level="district_municipality",
            parent_region_id=ID_REGION_IN_DL,
        ),
        # Brazil
        AdministrativeRegion(
            id=ID_REGION_BR_SP_CAPITAL, country_id=ID_COUNTRY_BR,
            name="São Paulo (Capital)", level="district_municipality",
            parent_region_id=ID_REGION_BR_SP,
        ),
        AdministrativeRegion(
            id=ID_REGION_BR_SP_CAMPINAS, country_id=ID_COUNTRY_BR,
            name="Campinas", level="district_municipality",
            parent_region_id=ID_REGION_BR_SP,
        ),
        AdministrativeRegion(
            id=ID_REGION_BR_RJ_CAPITAL, country_id=ID_COUNTRY_BR,
            name="Rio de Janeiro (Capital)", level="district_municipality",
            parent_region_id=ID_REGION_BR_RJ,
        ),
        # Russia
        AdministrativeRegion(
            id=ID_REGION_RU_MSK_KHIMKI, country_id=ID_COUNTRY_RU,
            name="Khimki", level="district_municipality",
            parent_region_id=ID_REGION_RU_MSK,
        ),
        AdministrativeRegion(
            id=ID_REGION_RU_SPB_CENTRAL, country_id=ID_COUNTRY_RU,
            name="Central District", level="district_municipality",
            parent_region_id=ID_REGION_RU_SPB,
        ),
    ]
    for r in rows:
        if not _exists(AdministrativeRegion, r.id):
            db.session.add(r)
    db.session.flush()
    print("  District/city regions: OK")


def seed_categories():
    rows = [
        Category(id=ID_CAT_HEALTH, code="healthcare_access",
                 name="Healthcare Access",
                 translations={"hi": "स्वास्थ्य सेवा", "pt": "Saúde", "ru": "Здравоохранение"}),
        Category(id=ID_CAT_WATER, code="water_sanitation",
                 name="Water & Sanitation",
                 translations={"hi": "जल और स्वच्छता", "pt": "Água e Saneamento", "ru": "Водоснабжение"}),
        Category(id=ID_CAT_ROADS, code="roads_transport",
                 name="Roads & Transport",
                 translations={"hi": "सड़क और परिवहन", "pt": "Estradas e Transporte", "ru": "Дороги и транспорт"}),
        Category(id=ID_CAT_ELEC, code="electricity_utilities",
                 name="Electricity & Utilities",
                 translations={"hi": "बिजली और उपयोगिताएँ", "pt": "Eletricidade", "ru": "Электроснабжение"}),
        Category(id=ID_CAT_EDU, code="education_access",
                 name="Education Access",
                 translations={"hi": "शिक्षा पहुँच", "pt": "Educação", "ru": "Образование"}),
        Category(id=ID_CAT_WASTE, code="waste_environment",
                 name="Waste / Drainage / Public Environment",
                 translations={"hi": "कचरा और जल निकासी", "pt": "Resíduos e Drenagem", "ru": "Отходы и дренаж"}),
    ]
    for r in rows:
        if not _exists(Category, r.id):
            db.session.add(r)
    db.session.flush()
    print("  Categories: OK")


def seed_departments():
    """
    Seed the department taxonomy department_officer accounts are scoped to.
    India only for now — matches what already existed in the database
    before this repo had any department-aware code (see
    app/models/auth_models.py provenance note). Extend per-country as
    department_officer accounts are provisioned for Brazil/Russia.
    """
    rows = [
        ("dept-in-health", "Health", "health"),
        ("dept-in-water", "Water & Sanitation", "water"),
        ("dept-in-pwd", "Public Works (Roads & Electricity)", "pwd"),
        ("dept-in-education", "Education", "education"),
        ("dept-in-environment", "Environment & Waste", "environment"),
    ]
    for dept_id, name, code in rows:
        if not _exists(Department, dept_id):
            db.session.add(Department(id=dept_id, country_id=ID_COUNTRY_IN, name=name, code=code))
    db.session.flush()
    print("  Departments: OK")


def seed_actors():
    """
    Idempotently ensure every account in app/auth/actors.py::DEMO_ACCOUNTS
    exists and is marked is_demo=True — reachable via the one-click /login
    country-card picker. Looks accounts up by email (their natural key);
    never overwrites an existing account's password_hash, so re-running
    this script cannot lock anyone out of an account already in use.
    """
    from app.auth.actors import DEMO_ACCOUNTS

    dept_by_code = {d.code: d.id for d in Department.query.filter_by(country_id=ID_COUNTRY_IN).all()}

    for a in DEMO_ACCOUNTS:
        country_id = _COUNTRY_ID_BY_CODE[a["country_code"]]

        if a["account_type"] == "citizen":
            existing = CitizenAccount.query.filter_by(email=a["email"]).first()
            if existing:
                if not existing.is_demo:
                    existing.is_demo = True
                if not existing.region_id and a.get("region_id"):
                    existing.region_id = a["region_id"]
                continue
            db.session.add(CitizenAccount(
                email=a["email"],
                password_hash=hash_password(DEMO_ACTOR_PASSWORD),
                full_name=a["full_name"],
                country_id=country_id,
                region_id=a.get("region_id"),
                preferred_language="en",
                consent_given_at=_now(),
                consent_version="2026-09-13",
                is_active=True,
                is_demo=True,
            ))
        else:
            existing = GovernmentAccount.query.filter_by(email=a["email"]).first()
            if existing:
                if not existing.is_demo:
                    existing.is_demo = True
                if not existing.region_id and a.get("region_id"):
                    existing.region_id = a["region_id"]
                continue
            db.session.add(GovernmentAccount(
                email=a["email"],
                password_hash=hash_password(DEMO_ACTOR_PASSWORD),
                full_name=a["full_name"],
                role=a["role"],
                country_id=country_id,
                region_id=a.get("region_id"),
                department_id=dept_by_code.get(a.get("department_code")),
                is_active=True,
                is_demo=True,
            ))
    db.session.flush()
    print(f"  Demo accounts ensured (password: {DEMO_ACTOR_PASSWORD}): OK")
    return DEMO_ACCOUNTS


def seed_reference_data():
    """
    Seed one InfrastructureDataPoint + one DemographicDataPoint for
    India / healthcare — enough for the Priority Evidence Card to render
    a meaningful gap assessment on the demo cluster.
    """
    infra_id = "infra-in-mh-health"
    demo_id  = "demo-in-mh-health"
    inv_id   = "inv-in-health-nhm"

    if not _exists(InfrastructureDataPoint, infra_id):
        db.session.add(InfrastructureDataPoint(
            id=infra_id,
            country_id=ID_COUNTRY_IN,
            region_id=ID_REGION_IN_MH,
            category_id=ID_CAT_HEALTH,
            official_coverage="Low",
            nearest_facility_distance_km=31.0,
            capacity_notes="Primary Health Centre capacity below WHO minimum ratio",
            source="National Health Mission District Survey 2023",
            source_url="https://nhm.gov.in",
            source_last_updated=date(2023, 6, 30),
            platform_last_synced=_now(),
            data_period="2022-23",
            geographic_granularity="district",
            verification_status="verified",
            freshness_status="recent",
        ))

    if not _exists(DemographicDataPoint, demo_id):
        db.session.add(DemographicDataPoint(
            id=demo_id,
            country_id=ID_COUNTRY_IN,
            region_id=ID_REGION_IN_MH,
            category_id=ID_CAT_HEALTH,
            population_affected=84000,
            population_total=210000,
            demographic_notes="Rural population with limited transport access to district hospital",
            source="Census of India 2021",
            source_last_updated=date(2021, 12, 31),
            platform_last_synced=_now(),
            data_period="2021",
            verification_status="verified",
            freshness_status="recent",
        ))

    if not _exists(GovernmentInvestment, inv_id):
        db.session.add(GovernmentInvestment(
            id=inv_id,
            name="National Health Mission — Maharashtra PHC Upgradation",
            country_id=ID_COUNTRY_IN,
            region_id=ID_REGION_IN_MH,
            category_id=ID_CAT_HEALTH,
            type="Programme",
            status="active",
            coverage_area="Selected districts of Maharashtra",
            target_population=120000,
            start_date=date(2022, 4, 1),
            expected_completion=date(2025, 3, 31),
            source="NHM Annual Report 2023-24",
            source_last_updated=date(2024, 3, 31),
            platform_last_synced=_now(),
            data_period="2022-25",
            verification_status="verified",
            freshness_status="recent",
        ))

    db.session.flush()
    print("  Reference data (India / healthcare): OK")


def seed_demo_beat():
    """
    Seeds the required flagship demo moment (Progress Log §10 / §13.2):

      DemandCluster: India / Healthcare / Maharashtra
        active_status  = "UnderGovernmentReview"  ← official government status
        review_status  = "UnderReview"

      18 Verification rows:
        15 × StillHappening  → 83% still affected
         2 × Improved
         1 × Resolved

      This produces the on-screen juxtaposition:
        "Community says: 83% still affected"  ←→  "Under Review"

      Also seeds:
        - 8 Report rows (representing many voices, all Clustered)
        - 8 Contribution rows linking those reports to the cluster
        - EventLog entries for timeline display
    """
    if _exists(DemandCluster, ID_CLUSTER_DEMO):
        print("  Demo beat cluster already exists — skipping")
        return

    # --- DemandCluster ---
    cluster = DemandCluster(
        id=ID_CLUSTER_DEMO,
        country_id=ID_COUNTRY_IN,
        region_ids=[ID_REGION_IN_MH, ID_REGION_IN_MH_NASHIK, ID_REGION_IN_MH_SINNAR],
        category_id=ID_CAT_HEALTH,
        affected_localities=["Nashik Rural", "Sinnar Block", "Yeola Taluka"],
        trend="increasing",
        confidence="high",
        active_status="UnderGovernmentReview",
        review_status="UnderReview",
    )
    db.session.add(cluster)
    db.session.flush()

    # --- 8 Reports (representing distinct citizens) ---
    citizen_ids = [
        f"demo-citizen-in-{i}" for i in range(1, 9)
    ]
    report_ids = []
    for i, cid in enumerate(citizen_ids):
        r = Report(
            id=f"demo-report-in-{i+1}",
            anonymous_token=cid,
            country_id=ID_COUNTRY_IN,
            region_id=ID_REGION_IN_MH_NASHIK,
            category_id=ID_CAT_HEALTH,
            original_raw_input=(
                "There is no proper healthcare facility in our area. "
                "The nearest hospital is very far and we cannot afford transport."
            ),
            # Already English, so problem_summary_en is the same text — this
            # field exists for the multilingual "common format" case
            # (non-English original_language), but every citizen-facing
            # screen that reads it (evidence_detail.html, citizen_voice.html)
            # filters on problem_summary_en being non-null, so it still needs
            # to be populated here for the demo cluster's reports to show up.
            problem_summary_en=(
                "There is no proper healthcare facility in our area. "
                "The nearest hospital is very far and we cannot afford transport."
            ),
            original_language="en",
            channel="text",
            severity="high",
            duration="over 2 years",
            affected_group="Rural residents",
            status="Clustered",
        )
        db.session.add(r)
        report_ids.append(r.id)
    db.session.flush()

    # --- Contributions (one per citizen, all "joined") ---
    for cid, rid in zip(citizen_ids, report_ids):
        db.session.add(Contribution(
            id=f"contrib-{rid}",
            report_id=rid,
            anonymous_token=cid,
            demand_cluster_id=ID_CLUSTER_DEMO,
            type="joined",
        ))

    # --- 18 Verification rows (15 StillHappening + 2 Improved + 1 Resolved)
    # This produces: (15+0) / 18 = 83% still affected when Worse=0
    # community_sentiment counts StillHappening + Worse as "still affected"
    verif_states = (
        ["StillHappening"] * 15 +
        ["Improved"] * 2 +
        ["Resolved"] * 1
    )
    # Use a mix of the 8 seeded citizens + extra anonymous IDs to simulate
    # community participation beyond just the original reporters
    verif_citizens = citizen_ids + [
        f"demo-verifier-in-{i}" for i in range(1, 11)
    ]
    for i, state in enumerate(verif_states):
        cid = verif_citizens[i % len(verif_citizens)]
        db.session.add(Verification(
            id=f"verif-demo-{i+1}",
            anonymous_token=cid,
            demand_cluster_id=ID_CLUSTER_DEMO,
            state=state,
        ))

    # --- EventLog entries for timeline display ---
    stages = [
        ("Submitted",     report_ids[0]),
        ("AIUnderstood",  report_ids[0]),
        ("JoinedDemand",  report_ids[0]),
        ("UnderReview",   report_ids[0]),
    ]
    for stage, rid in stages:
        db.session.add(EventLog(
            id=f"evt-demo-{stage.lower()}",
            report_id=rid,
            demand_cluster_id=ID_CLUSTER_DEMO,
            stage=stage,
        ))

    db.session.flush()
    print("  Demo beat cluster seeded (18 verifications → 83% still affected / Under Review): OK")


# ---------------------------------------------------------------------------
# Multi-state expansion — 7 Indian states with real districts, government
# accounts, citizens, evidence, and demand clusters, so the product actually
# demonstrates cross-geography behaviour instead of a Maharashtra-only demo.
# Maharashtra itself is included (reusing its existing region/district IDs
# above) so it gets the same multi-sector treatment as the six new states,
# on top of the flagship healthcare cluster seed_demo_beat() already seeds.
# ---------------------------------------------------------------------------

CATEGORY_ID_BY_CODE = {
    "healthcare_access": ID_CAT_HEALTH,
    "water_sanitation": ID_CAT_WATER,
    "roads_transport": ID_CAT_ROADS,
    "electricity_utilities": ID_CAT_ELEC,
    "education_access": ID_CAT_EDU,
    "waste_environment": ID_CAT_WASTE,
}

# Short, stable abbreviations for building id strings that must fit the
# String(36) primary-key columns used throughout this schema — the full
# category code (e.g. "electricity_utilities") is too long once combined
# with a table prefix + state code.
CATEGORY_ABBR = {
    "healthcare_access": "health",
    "water_sanitation": "water",
    "roads_transport": "roads",
    "electricity_utilities": "elec",
    "education_access": "edu",
    "waste_environment": "waste",
}

_RAW_STATES = [
    {"code": "MH", "name": "Maharashtra", "existing": True,
     "districts": [("Nashik", ID_REGION_IN_MH_NASHIK), ("Pune", ID_REGION_IN_MH_PUNE),
                   ("Sinnar", ID_REGION_IN_MH_SINNAR), ("Yeola", ID_REGION_IN_MH_YEOLA)],
     "language": "mr", "sectors": ["water_sanitation", "roads_transport"]},
    {"code": "UP", "name": "Uttar Pradesh", "existing": False,
     "districts": ["Lucknow", "Varanasi", "Prayagraj", "Kanpur Nagar", "Gorakhpur"],
     "language": "hi", "sectors": ["education_access", "water_sanitation", "healthcare_access"]},
    {"code": "KA", "name": "Karnataka", "existing": False,
     "districts": ["Bengaluru Urban", "Mysuru", "Belagavi", "Dakshina Kannada", "Dharwad"],
     "language": "kn", "sectors": ["roads_transport", "healthcare_access", "electricity_utilities"]},
    {"code": "RJ", "name": "Rajasthan", "existing": False,
     "districts": ["Jaipur", "Jodhpur", "Udaipur", "Kota", "Ajmer"],
     "language": "hi", "sectors": ["water_sanitation", "roads_transport", "healthcare_access"]},
    {"code": "GJ", "name": "Gujarat", "existing": False,
     "districts": ["Ahmedabad", "Surat", "Vadodara", "Rajkot", "Gandhinagar"],
     "language": "gu", "sectors": ["water_sanitation", "waste_environment", "roads_transport"]},
    {"code": "TN", "name": "Tamil Nadu", "existing": False,
     "districts": ["Chennai", "Coimbatore", "Madurai", "Salem", "Tiruchirappalli"],
     "language": "ta", "sectors": ["healthcare_access", "education_access", "water_sanitation"]},
    {"code": "WB", "name": "West Bengal", "existing": False,
     "districts": ["Kolkata", "Howrah", "Darjeeling", "North 24 Parganas", "South 24 Parganas"],
     "language": "bn", "sectors": ["roads_transport", "water_sanitation", "healthcare_access"]},
]


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-").replace("(", "").replace(")", "").replace("/", "-")


def _region_id(code: str, district: str | None = None) -> str:
    return f"region-in-{code.lower()}-{_slug(district)}" if district else f"region-in-{code.lower()}"


def _build_state_expansion() -> list[dict]:
    result = []
    for s in _RAW_STATES:
        if s["existing"]:
            state_id = ID_REGION_IN_MH
            district_pairs = s["districts"]
        else:
            state_id = _region_id(s["code"])
            district_pairs = [(name, _region_id(s["code"], name)) for name in s["districts"]]
        result.append({
            "code": s["code"], "name": s["name"], "existing": s["existing"],
            "state_id": state_id, "districts": district_pairs,
            "language": s["language"], "sectors": s["sectors"],
        })
    return result


STATE_EXPANSION = _build_state_expansion()

# Approximate real-world (lng, lat) per district — used only for DemandCluster
# centroids so the map genuinely plots pins across India instead of only
# Maharashtra. India-centroid fallback for anything not listed.
DISTRICT_COORDS = {
    "Nashik": (73.7898, 19.9975), "Pune": (73.8567, 18.5204),
    "Sinnar": (73.9958, 19.8496), "Yeola": (74.4923, 20.0419),
    "Lucknow": (80.9462, 26.8467), "Varanasi": (83.0069, 25.3176),
    "Prayagraj": (81.8463, 25.4358), "Kanpur Nagar": (80.3319, 26.4499),
    "Gorakhpur": (83.3732, 26.7606),
    "Bengaluru Urban": (77.5946, 12.9716), "Mysuru": (76.6394, 12.2958),
    "Belagavi": (74.4977, 15.8497), "Dakshina Kannada": (74.8560, 12.9141),
    "Dharwad": (75.0078, 15.4589),
    "Jaipur": (75.7873, 26.9124), "Jodhpur": (73.0243, 26.2389),
    "Udaipur": (73.7125, 24.5854), "Kota": (75.8648, 25.2138), "Ajmer": (74.6399, 26.4499),
    "Ahmedabad": (72.5714, 23.0225), "Surat": (72.8311, 21.1702),
    "Vadodara": (73.1812, 22.3072), "Rajkot": (70.8022, 22.3039), "Gandhinagar": (72.6369, 23.2156),
    "Chennai": (80.2707, 13.0827), "Coimbatore": (76.9558, 11.0168),
    "Madurai": (78.1198, 9.9252), "Salem": (78.1460, 11.6643), "Tiruchirappalli": (78.7047, 10.7905),
    "Kolkata": (88.3639, 22.5726), "Howrah": (88.2636, 22.5958),
    "Darjeeling": (88.2627, 27.0410), "North 24 Parganas": (88.4327, 22.6198),
    "South 24 Parganas": (88.3936, 22.1667),
}
_INDIA_CENTROID = (78.9629, 20.5937)

# Real citizen-voice text in the state's own language for one "flagship"
# report per (category, language) combination actually used above, keyed so
# the original citizen voice is preserved in-language (never fabricated as
# English) while problem_summary_en gives government reviewers a real English
# gloss — same "common format" contract citizen/routes.py's real Groq
# extraction produces. Every other report in a cluster uses an English
# template (ENGLISH_TEMPLATES) — this keeps bulk seeding fast/deterministic
# while still proving multilingual storage end-to-end for each state.
NATIVE_SENTENCES = {
    ("water_sanitation", "mr"): ("आमच्या भागात पिण्याचे स्वच्छ पाणी मिळत नाही.",
                                  "There is no clean drinking water available in our area."),
    ("roads_transport", "mr"): ("आमचा रस्ता खूप खराब आहे, पावसाळ्यात ये-जा करणे कठीण होते.",
                                 "Our road is in very poor condition and becomes impassable during the monsoon."),
    ("water_sanitation", "hi"): ("हमारे गांव में पीने का साफ पानी नहीं मिलता है।",
                                  "There is no clean drinking water in our village."),
    ("healthcare_access", "hi"): ("नजदीक में कोई अच्छा अस्पताल नहीं है, बीमार होने पर बहुत दूर जाना पड़ता है।",
                                   "There is no good hospital nearby; we must travel very far when someone falls ill."),
    ("education_access", "hi"): ("हमारे इलाके में माध्यमिक विद्यालय नहीं है, बच्चों को दूर पढ़ने जाना पड़ता है।",
                                   "There is no secondary school in our area; children must travel far to study."),
    ("roads_transport", "kn"): ("ನಮ್ಮ ಗ್ರಾಮದ ರಸ್ತೆ ತುಂಬಾ ಹಾಳಾಗಿದೆ, ಬಸ್ ಸೌಲಭ್ಯ ಸಹ ಇಲ್ಲ.",
                                 "Our village road is very bad, and there is no bus facility either."),
    ("healthcare_access", "kn"): ("ಹತ್ತಿರದಲ್ಲಿ ಆಸ್ಪತ್ರೆ ಇಲ್ಲ, ತುರ್ತು ಸಂದರ್ಭದಲ್ಲಿ ತುಂಬಾ ಕಷ್ಟವಾಗುತ್ತದೆ.",
                                   "There is no hospital nearby; emergencies are very difficult to handle."),
    ("electricity_utilities", "kn"): ("ನಮ್ಮ ಪ್ರದೇಶದಲ್ಲಿ ವಿದ್ಯುತ್ ಪೂರೈಕೆ ಆಗಾಗ್ಗೆ ಕಡಿತಗೊಳ್ಳುತ್ತದೆ.",
                                       "The electricity supply in our area is frequently interrupted."),
    ("water_sanitation", "gu"): ("અમારા વિસ્તારમાં પીવાનું ચોખ્ખું પાણી મળતું નથી.",
                                  "We don't get clean drinking water in our area."),
    ("waste_environment", "gu"): ("અમારા વિસ્તારમાં કચરાની યોગ્ય વ્યવસ્થા નથી, ગંદકી ફેલાય છે.",
                                   "There is no proper waste management in our area, and it is causing pollution."),
    ("roads_transport", "gu"): ("અમારો રસ્તો ખૂબ ખરાબ છે, વાહન ચલાવવું મુશ્કેલ છે.",
                                 "Our road is in very bad condition, making it hard to drive."),
    ("healthcare_access", "ta"): ("எங்கள் பகுதியில் அரசு மருத்துவமனை இல்லை, நோய்வாய்ப்பட்டால் தூரம் செல்ல வேண்டும்.",
                                   "There is no government hospital in our area; we must travel far when ill."),
    ("education_access", "ta"): ("எங்கள் ஊரில் மேல்நிலைப் பள்ளி இல்லை, குழந்தைகள் தூரம் செல்ல வேண்டும்.",
                                   "There is no secondary school in our town; children must travel far."),
    ("water_sanitation", "ta"): ("எங்கள் பகுதியில் குடிநீர் பிரச்சனை உள்ளது.",
                                  "There is a drinking water shortage in our area."),
    ("roads_transport", "bn"): ("আমাদের গ্রামের রাস্তা খুব খারাপ, বর্ষায় চলাচল করা কঠিন হয়ে যায়।",
                                 "Our village road is very poor; travel becomes difficult during the rains."),
    ("water_sanitation", "bn"): ("আমাদের এলাকায় পরিষ্কার পানীয় জলের অভাব আছে।",
                                  "There is a shortage of clean drinking water in our area."),
    ("healthcare_access", "bn"): ("কাছে কোনো হাসপাতাল নেই, অসুস্থ হলে অনেক দূরে যেতে হয়।",
                                   "There is no hospital nearby; we must travel very far when ill."),
}

ENGLISH_TEMPLATES = {
    "healthcare_access": [
        "There is no primary healthcare centre near our locality; the nearest hospital is over an hour away.",
        "We have been asking for a functioning health sub-centre for years but nothing has changed.",
        "During emergencies we have no choice but to travel a long distance for basic medical help.",
        "The local clinic has no doctor available most days of the week.",
    ],
    "water_sanitation": [
        "Our area has no piped water connection; we depend on an unreliable tanker supply.",
        "The drainage in our locality overflows every monsoon and nobody has fixed it.",
        "Drinking water here is often contaminated and there is no proper filtration system.",
        "Households in our street still fetch water from a shared borewell over a kilometre away.",
    ],
    "roads_transport": [
        "The approach road to our village is unpaved and becomes unusable during the rains.",
        "There is no public bus service connecting our area to the nearest town.",
        "The last-mile road connectivity to our settlement has been neglected for years.",
        "Commuters here have no safe footpath alongside the main road.",
    ],
    "education_access": [
        "There is no secondary school within walking distance; children drop out after primary school.",
        "Our school lacks basic infrastructure like toilets and a functioning library.",
        "Students here have far less access to digital learning tools than the nearby town.",
        "The nearest government school is understaffed and overcrowded.",
    ],
    "electricity_utilities": [
        "Power cuts are frequent and unpredictable, affecting small businesses and students alike.",
        "There is no street lighting on our main road, which is unsafe at night.",
        "Several households in our locality still have no formal electricity connection.",
        "Voltage fluctuations regularly damage household appliances here.",
    ],
    "waste_environment": [
        "There is no regular waste collection service in our neighbourhood.",
        "Open drains and garbage dumping have become a serious health hazard here.",
        "Industrial waste appears to be affecting our local water bodies and nobody has addressed it.",
        "There is no designated dumping ground, so waste is burned in the open.",
    ],
}

CITIZEN_NAMES = {
    "MH": ["Sanjay Deshmukh", "Vaishali Jadhav", "Ganesh Pawar", "Sunanda Kale",
           "Ramesh Bhosale", "Anjali More", "Prakash Shinde", "Snehal Gaikwad",
           "Vitthal Chavan", "Manisha Kadam"],
    "UP": ["Ramesh Yadav", "Sunita Verma", "Anil Kumar", "Pooja Singh",
           "Rajesh Gupta", "Meena Devi", "Vikas Pandey", "Kavita Sharma",
           "Suresh Chauhan", "Anita Mishra", "Deepak Tiwari", "Rekha Srivastava"],
    "KA": ["Manjunath Gowda", "Lakshmi Hegde", "Ravindra Shetty", "Sowmya Rao",
           "Suresh Naik", "Deepa Kulkarni", "Nagaraj Patil", "Roopa Bhat",
           "Prakash Reddy", "Shilpa Iyer", "Chandan Kumar", "Vidya Murthy"],
    "RJ": ["Mahendra Singh Rathore", "Kamla Devi", "Bhanwar Lal", "Geeta Choudhary",
           "Om Prakash Meena", "Sita Bai", "Ratan Singh Shekhawat", "Nirmala Rajput",
           "Girraj Prasad", "Shanti Devi", "Kishore Sharma", "Radha Kanwar"],
    "GJ": ["Nikunj Patel", "Hetal Shah", "Bharat Trivedi", "Falguni Desai",
           "Mahesh Vasava", "Kiran Rathwa", "Jayesh Chauhan", "Nayana Solanki",
           "Ashwin Parmar", "Bhavna Modi", "Chirag Joshi", "Rekha Vaghela"],
    "TN": ["Murugan Pillai", "Kalaivani Raman", "Suresh Kannan", "Lakshmi Priya",
           "Gopal Krishnan", "Meenakshi Sundaram", "Karthik Raja", "Vasanthi Devi",
           "Senthil Kumar", "Anitha Rajendran", "Muthu Vel", "Deepa Narayanan"],
    "WB": ["Subrata Mondal", "Rina Das", "Amit Chakraborty", "Mousumi Ghosh",
           "Debashis Roy", "Sikha Bhattacharya", "Prabir Sarkar", "Ananya Banerjee",
           "Tapan Mandal", "Ruma Dey", "Ashok Halder", "Sumita Pramanik"],
}

# Cycled per DemandCluster (index-based, not random, so re-runs are
# deterministic) to spread the five real investment-alignment states across
# the multi-state dataset instead of every cluster landing on the same one:
#   (investment_present, investment_status, investment_freshness,
#    infra_coverage, infra_freshness, trend) -> resulting alignment_state
PROFILES = [
    (False, None, None, "Low", "recent", "stable"),               # UNADDRESSED
    (True, "active", "recent", "Low", "recent", "increasing"),    # IMPLEMENTATION_ACCESS_GAP
    (False, None, None, "Low", "recent", "increasing"),           # EMERGING_GAP
    (True, "active", "recent", "Low", "recent", "stable"),        # PARTIALLY_ADDRESSED
    (True, "completed", "recent", "Medium", "recent", "stable"),  # ALIGNED
]

PROJECT_STATUS_CYCLE = ["Construction", "Completion", "Planning", "Completion", "Tender", "Completion", "Approval"]
OUTCOME_CYCLE = ["skip", "verified", "skip", "awaiting", "skip", "verified", "skip"]


def seed_multi_state_regions():
    """States/districts for UP/KA/RJ/GJ/TN/WB — Maharashtra already exists."""
    for cfg in STATE_EXPANSION:
        if cfg["existing"]:
            continue
        if not _exists(AdministrativeRegion, cfg["state_id"]):
            db.session.add(AdministrativeRegion(
                id=cfg["state_id"], country_id=ID_COUNTRY_IN, name=cfg["name"], level="state_province",
            ))
        for dname, did in cfg["districts"]:
            if not _exists(AdministrativeRegion, did):
                db.session.add(AdministrativeRegion(
                    id=did, country_id=ID_COUNTRY_IN, name=dname, level="district_municipality",
                    parent_region_id=cfg["state_id"],
                ))
    db.session.flush()
    print(f"  Multi-state regions ({len(STATE_EXPANSION) - 1} new states + districts): OK")


def seed_multi_state_government():
    """One MP (state_admin) + one Planning Officer (district_officer) per NEW
    state — Maharashtra's already exist via app/auth/actors.py::DEMO_ACCOUNTS."""
    for cfg in STATE_EXPANSION:
        if cfg["existing"]:
            continue
        code = cfg["code"].lower()
        mp_email = f"mp.{code}.in@nevo.demo"
        po_email = f"po.{code}.in@nevo.demo"
        first_district_name, first_district_id = cfg["districts"][0]

        if GovernmentAccount.query.filter_by(email=mp_email).first() is None:
            db.session.add(GovernmentAccount(
                email=mp_email, password_hash=hash_password(DEMO_ACTOR_PASSWORD),
                full_name=f"State Administrator – {cfg['name']}", role="state_admin",
                country_id=ID_COUNTRY_IN, region_id=cfg["state_id"],
                is_active=True, is_demo=True,
            ))
        if GovernmentAccount.query.filter_by(email=po_email).first() is None:
            db.session.add(GovernmentAccount(
                email=po_email, password_hash=hash_password(DEMO_ACTOR_PASSWORD),
                full_name=f"District Planning Officer – {first_district_name}, {cfg['name']}",
                role="district_officer", country_id=ID_COUNTRY_IN, region_id=first_district_id,
                is_active=True, is_demo=True,
            ))
    db.session.flush()
    print(f"  Multi-state government accounts (MP+PO per new state, password: {DEMO_ACTOR_PASSWORD}): OK")


def seed_multi_state_citizens() -> dict:
    """Returns {state_code: [CitizenAccount.id, ...]} for use by demand seeding."""
    citizen_ids_by_state: dict = {}
    for cfg in STATE_EXPANSION:
        code = cfg["code"]
        names = CITIZEN_NAMES[code]
        ids = []
        for i, full_name in enumerate(names):
            email = f"citizen{i + 1}.{code.lower()}@nevo.demo"
            district_name, district_id = cfg["districts"][i % len(cfg["districts"])]
            existing = CitizenAccount.query.filter_by(email=email).first()
            if existing:
                if not existing.region_id:
                    existing.region_id = district_id
                ids.append(existing.id)
                continue
            acc = CitizenAccount(
                email=email, password_hash=hash_password(DEMO_ACTOR_PASSWORD),
                full_name=full_name, country_id=ID_COUNTRY_IN, region_id=district_id,
                locality=f"{district_name} Ward {i + 1}", preferred_language=cfg["language"],
                consent_given_at=_now(), consent_version="2026-09-13",
                is_active=True, is_demo=True,
            )
            db.session.add(acc)
            db.session.flush()
            ids.append(acc.id)
        citizen_ids_by_state[code] = ids
    db.session.flush()
    total = sum(len(v) for v in citizen_ids_by_state.values())
    print(f"  Multi-state citizens ({total} accounts across {len(STATE_EXPANSION)} states, password: {DEMO_ACTOR_PASSWORD}): OK")
    return citizen_ids_by_state


def seed_multi_state_demand_data(citizen_ids_by_state: dict) -> dict:
    """
    Real DemandClusters per (state, sector): distinct citizens, distinct report
    text (native-language flagship + English-template fillers), real
    Contribution/Verification rows, a real Cohere embedding via
    demand_matching.store_cluster_embedding(), and a real-world centroid.
    Returns {state_code: flagship_cluster_id} for project/decision linking.
    """
    from sqlalchemy import text as _text
    from app.services.demand_matching import store_cluster_embedding

    cluster_index = 0
    state_flagship_cluster: dict = {}

    for cfg in STATE_EXPANSION:
        code = cfg["code"]
        state_id = cfg["state_id"]
        citizen_ids = citizen_ids_by_state[code]

        for sector_i, category_code in enumerate(cfg["sectors"]):
            category_id = CATEGORY_ID_BY_CODE[category_code]
            profile = PROFILES[cluster_index % len(PROFILES)]
            inv_present, inv_status, inv_freshness, coverage, infra_freshness, trend = profile
            district_name, district_id = cfg["districts"][sector_i % len(cfg["districts"])]

            abbr = CATEGORY_ABBR[category_code]
            infra_id = f"infra-in-{code.lower()}-{abbr}"
            demo_id = f"demo-in-{code.lower()}-{abbr}"
            inv_id = f"inv-in-{code.lower()}-{abbr}"
            population = 60000 + cluster_index * 15000

            if not _exists(InfrastructureDataPoint, infra_id):
                db.session.add(InfrastructureDataPoint(
                    id=infra_id, country_id=ID_COUNTRY_IN, region_id=state_id, category_id=category_id,
                    official_coverage=coverage, nearest_facility_distance_km=float(12 + cluster_index * 3),
                    capacity_notes=f"State-level {category_code.replace('_', ' ')} capacity assessment for {cfg['name']}.",
                    source=f"{cfg['name']} State Development Report 2024", source_last_updated=date(2024, 3, 31),
                    platform_last_synced=_now(), data_period="2023-24", geographic_granularity="state",
                    verification_status="verified", freshness_status=infra_freshness,
                ))
            if not _exists(DemographicDataPoint, demo_id):
                db.session.add(DemographicDataPoint(
                    id=demo_id, country_id=ID_COUNTRY_IN, region_id=state_id, category_id=category_id,
                    population_affected=population, population_total=population * 3,
                    demographic_notes=f"Estimated population affected by {category_code.replace('_', ' ')} gaps in {district_name}, {cfg['name']}.",
                    source="Census of India 2021 (state projection)", source_last_updated=date(2021, 12, 31),
                    platform_last_synced=_now(), data_period="2021",
                    verification_status="verified", freshness_status="recent",
                ))
            if inv_present and not _exists(GovernmentInvestment, inv_id):
                db.session.add(GovernmentInvestment(
                    id=inv_id, name=f"{cfg['name']} {category_code.replace('_', ' ').title()} Improvement Programme",
                    country_id=ID_COUNTRY_IN, region_id=state_id, category_id=category_id,
                    type="Programme", status=inv_status, coverage_area=f"Selected districts of {cfg['name']}",
                    target_population=population, start_date=date(2022, 4, 1), expected_completion=date(2025, 3, 31),
                    source=f"{cfg['name']} Planning Department Annual Report", source_last_updated=date(2024, 3, 31),
                    platform_last_synced=_now(), data_period="2022-25",
                    verification_status="verified", freshness_status=inv_freshness,
                ))
            db.session.flush()

            cluster_id = f"cluster-in-{code.lower()}-{abbr}"
            if not _exists(DemandCluster, cluster_id):
                cluster = DemandCluster(
                    id=cluster_id, country_id=ID_COUNTRY_IN, region_ids=[state_id, district_id],
                    category_id=category_id, affected_localities=[district_name],
                    trend=trend, confidence="medium",
                    active_status="UnderGovernmentReview" if cluster_index % 4 == 0 else "Active",
                    review_status="NotReviewed",
                )
                db.session.add(cluster)
                db.session.flush()

                n_reporters = 5 + (cluster_index % 4)   # 5-8 distinct citizens per cluster
                reporters = [citizen_ids[i % len(citizen_ids)] for i in range(cluster_index, cluster_index + n_reporters)]
                summaries = []
                for j, citizen_id in enumerate(reporters):
                    native = NATIVE_SENTENCES.get((category_code, cfg["language"]))
                    if j == 0 and native:
                        raw_text, summary_en, lang = native[0], native[1], cfg["language"]
                    else:
                        templates = ENGLISH_TEMPLATES[category_code]
                        raw_text = templates[j % len(templates)]
                        summary_en, lang = raw_text, "en"
                    summaries.append(summary_en)

                    report = Report(
                        citizen_account_id=citizen_id,
                        consent_given_at=_now(), country_id=ID_COUNTRY_IN, region_id=district_id,
                        category_id=category_id, original_raw_input=raw_text, original_language=lang,
                        problem_summary_en=summary_en, channel="text",
                        severity=["medium", "high", "high", "critical"][j % 4],
                        duration=["6 months", "1 year", "over 2 years", "ongoing"][j % 4],
                        affected_group="Rural residents" if j % 2 == 0 else "Low-income urban households",
                        status="Clustered",
                    )
                    db.session.add(report)
                    db.session.flush()
                    db.session.add(Contribution(
                        report_id=report.id, citizen_account_id=citizen_id,
                        demand_cluster_id=cluster_id, type="joined",
                    ))
                    db.session.add(EventLog(report_id=report.id, demand_cluster_id=cluster_id, stage="Submitted"))
                    db.session.add(EventLog(report_id=report.id, demand_cluster_id=cluster_id, stage="JoinedDemand"))

                if trend == "increasing":
                    states_mix = ["StillHappening"] * 8 + ["Worse"] * 2
                elif trend == "decreasing":
                    states_mix = ["Improved"] * 5 + ["Resolved"] * 3 + ["StillHappening"] * 2
                else:
                    states_mix = ["StillHappening"] * 6 + ["Improved"] * 2 + ["Resolved"] * 1
                for k, vstate in enumerate(states_mix):
                    verifier_id = citizen_ids[(cluster_index + k) % len(citizen_ids)]
                    db.session.add(Verification(
                        citizen_account_id=verifier_id,
                        demand_cluster_id=cluster_id, state=vstate,
                    ))
                db.session.flush()

                try:
                    store_cluster_embedding(cluster_id, " ".join(summaries[:5]))
                except Exception as e:
                    print(f"    (warning) embedding failed for {cluster_id}: {e}")

                lng, lat = DISTRICT_COORDS.get(district_name, _INDIA_CENTROID)
                db.session.execute(
                    _text(
                        "UPDATE demand_clusters SET centroid = ST_SetSRID(ST_MakePoint(:lng, :lat), 4326) "
                        "WHERE id = :id"
                    ),
                    {"lng": lng, "lat": lat, "id": cluster_id},
                )
                db.session.flush()

                if sector_i == 0:
                    state_flagship_cluster[code] = cluster_id

            cluster_index += 1

    db.session.commit()
    print(f"  Multi-state demand clusters ({cluster_index} total across {len(STATE_EXPANSION)} states): OK")
    return state_flagship_cluster


def seed_multi_state_projects(state_flagship_cluster: dict):
    """One GovernmentDecision + Project (+ Outcome for some) per state,
    linked to that state's flagship cluster — proves the decision/project/
    outcome lifecycle works across states, not only Maharashtra."""
    for idx, cfg in enumerate(STATE_EXPANSION):
        code = cfg["code"]
        cluster_id = state_flagship_cluster.get(code)
        if not cluster_id:
            continue

        mp_email = "state.admin.in@nevo.demo" if cfg["existing"] else f"mp.{code.lower()}.in@nevo.demo"
        mp_account = GovernmentAccount.query.filter_by(email=mp_email).first()

        decision_id = f"decision-in-{code.lower()}-1"
        if not _exists(GovernmentDecision, decision_id):
            db.session.add(GovernmentDecision(
                id=decision_id, demand_cluster_id=cluster_id, country_id=ID_COUNTRY_IN,
                decided_by_id=(mp_account.id if mp_account else "seed"),
                decided_by_account_id=(mp_account.id if mp_account else None),
                decided_by_role="mp", decision_type="Prioritize",
                reason=(f"Community evidence and infrastructure gap data for {cfg['name']} support "
                        "prioritizing this demand for the next planning cycle."),
            ))
            cluster = db.session.get(DemandCluster, cluster_id)
            if cluster:
                cluster.review_status = "Decided"
                cluster.active_status = "UnderGovernmentReview"
        db.session.flush()

        project_id = f"project-in-{code.lower()}-1"
        status = PROJECT_STATUS_CYCLE[idx % len(PROJECT_STATUS_CYCLE)]
        if not _exists(Project, project_id):
            cluster = db.session.get(DemandCluster, cluster_id)
            region_id = (cluster.region_ids or [None])[0] if cluster else None
            db.session.add(Project(
                id=project_id, name=f"{cfg['name']} Community Development Initiative",
                country_id=ID_COUNTRY_IN, region_id=region_id, status=status,
                milestones=[{"label": "Survey and needs assessment complete", "status": "done"}],
                linked_demand_cluster_id=cluster_id, expected_completion=date(2026, 12, 31),
            ))
            db.session.flush()
            existing_decision = db.session.get(GovernmentDecision, decision_id)
            if existing_decision:
                existing_decision.linked_project_id = project_id

        outcome_kind = OUTCOME_CYCLE[idx % len(OUTCOME_CYCLE)]
        if outcome_kind != "skip" and not Outcome.query.filter_by(project_id=project_id).first():
            if outcome_kind == "verified":
                db.session.add(Outcome(
                    project_id=project_id, demand_cluster_id=cluster_id,
                    before_indicator="Baseline community survey indicated a significant service gap.",
                    after_indicator="Post-implementation survey shows measurable improvement in service access.",
                    impact_percent=42.0, status="Verified",
                ))
            else:
                db.session.add(Outcome(
                    project_id=project_id, demand_cluster_id=cluster_id,
                    before_indicator="Baseline community survey indicated a significant service gap.",
                    status="AwaitingOutcomeData",
                ))
    db.session.commit()
    print(f"  Multi-state decisions/projects/outcomes ({len(STATE_EXPANSION)} states): OK")


# ---------------------------------------------------------------------------
# MPLADS reference context — real, sourced, state-level MPLADS fund
# utilization figures for the 7 seeded states, fetched live from
# Empowered Indian's public MPLADS Dashboard (https://empoweredindian.in
# /mplads/states, "Both Houses", 18th Lok Sabha term) on 2026-09-20. Every
# figure below is real government-derived data as aggregated by that
# platform — not invented, not a NEVO-computed estimate. See
# app/models/mplads_models.py for why this is deliberately kept separate
# from the category-scoped GovernmentInvestment evidence pipeline.
# ---------------------------------------------------------------------------

MPLADS_TERM = "18th Lok Sabha (2024-29)"
MPLADS_SOURCE = "Empowered Indian — MPLADS Dashboard (public data aggregated from official MPLADS records)"
MPLADS_SOURCE_URL = "https://empoweredindian.in/mplads/states"
MPLADS_AS_OF = date(2026, 9, 20)

# code -> (mp_count, total_allocated_cr, total_expenditure_cr, utilization_pct, works_completed, works_recommended)
MPLADS_STATE_DATA = {
    "MH": (67, 952.3, 194.7, 20.4, 1178, 5712),
    "UP": (111, 1778.3, 912.4, 51.3, 10262, 25980),
    "KA": (42, 623.9, 180.5, 28.9, 1318, 6006),
    "RJ": (35, 514.0, 140.0, 27.2, 1313, 4401),
    "GJ": (35, 504.7, 137.0, 27.1, 3119, 9775),
    "TN": (58, 845.2, 367.8, 43.5, 3629, 6941),
    "WB": (53, 783.7, 231.8, 29.6, 2426, 5922),
}


def seed_mplads_reference():
    """Idempotent upsert-by-(region, term) of real MPLADS state summaries."""
    region_by_code = {cfg["code"]: cfg["state_id"] for cfg in STATE_EXPANSION}

    for code, (mp_count, allocated_cr, expenditure_cr, util_pct, completed, recommended) in MPLADS_STATE_DATA.items():
        region_id = region_by_code[code]
        existing = MpladsStateSummary.query.filter_by(region_id=region_id, lok_sabha_term=MPLADS_TERM).first()
        if existing:
            continue
        db.session.add(MpladsStateSummary(
            country_id=ID_COUNTRY_IN, region_id=region_id, lok_sabha_term=MPLADS_TERM,
            mp_count=mp_count,
            total_allocated=allocated_cr * 10_000_000,      # crore -> rupees
            total_expenditure=expenditure_cr * 10_000_000,
            fund_utilization_pct=util_pct,
            works_completed=completed, works_recommended=recommended,
            source=MPLADS_SOURCE, source_url=MPLADS_SOURCE_URL,
            source_last_updated=MPLADS_AS_OF, platform_last_synced=_now(),
        ))
    db.session.commit()
    print(f"  MPLADS reference context ({len(MPLADS_STATE_DATA)} states, real data as of {MPLADS_AS_OF}): OK")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    with app.app_context():
        print("Seeding BRICS People First demo data...")
        seed_countries()
        seed_regions()
        seed_district_regions()
        seed_categories()
        seed_departments()
        seed_actors()
        seed_reference_data()
        seed_demo_beat()
        db.session.commit()

        print("Seeding multi-state (7-state) demo data...")
        seed_multi_state_regions()
        seed_multi_state_government()
        citizen_ids_by_state = seed_multi_state_citizens()
        db.session.commit()
        state_flagship_cluster = seed_multi_state_demand_data(citizen_ids_by_state)
        seed_multi_state_projects(state_flagship_cluster)
        seed_mplads_reference()
        print("Seed complete.")


if __name__ == "__main__":
    run()
