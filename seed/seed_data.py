# seed/seed_data.py
#
# Seed script for NEVO demo data.
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
#   4. Departments (India) + real CitizenAccount/GovernmentAccount rows
#      (Phase 0) — one per demo role, hashed passwords, see seed_accounts()
#   5. Reference data (InfrastructureDataPoint + DemographicDataPoint +
#      GovernmentInvestment rows for India/healthcare — enough for the
#      Priority Evidence Card to render)
#   6. REQUIRED DEMO BEAT (Progress Log §10 / §13.2):
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
from app.models.accounts import CitizenAccount, GovernmentAccount, Department
from app.auth.security import hash_password, CURRENT_CONSENT_VERSION

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

# Demo accounts — Phase 0 replaces the old session-only actor registry with
# real CitizenAccount/GovernmentAccount rows. Same IDs reused so the demo
# beat cluster and any pre-existing GovernmentDecision.decided_by_id values
# in the dev database keep resolving to the same identity.
# Role mapping: mp -> state_admin, planning_officer -> district_officer,
# admin -> national_admin (Phase 0 design §2).
ID_CITIZEN_IN  = "actor-citizen-in"
ID_MP_IN       = "actor-mp-in"        # now role=state_admin
ID_PO_IN       = "actor-po-in"        # now role=district_officer
ID_CITIZEN_BR  = "actor-citizen-br"
ID_MP_BR       = "actor-mp-br"
ID_PO_BR       = "actor-po-br"
ID_CITIZEN_RU  = "actor-citizen-ru"
ID_MP_RU       = "actor-mp-ru"
ID_PO_RU       = "actor-po-ru"
ID_ADMIN       = "actor-admin"        # now role=national_admin (India only)

# New Phase 0 roles — demo accounts for India only (only India has full
# reference/demo data seeded).
ID_DEPT_OFFICER_IN = "actor-dept-officer-in"
ID_ANALYST_IN      = "actor-analyst-in"
ID_REVIEWER_IN     = "actor-reviewer-in"

# Departments (India) — needed for department_officer scoping and
# "assign responsible department" (Government §7/§12).
ID_DEPT_HEALTH_IN = "dept-in-health"
ID_DEPT_WATER_IN  = "dept-in-water"
ID_DEPT_PWD_IN    = "dept-in-pwd"          # roads + electricity
ID_DEPT_EDU_IN    = "dept-in-education"
ID_DEPT_ENV_IN    = "dept-in-environment"  # waste/drainage

# All demo accounts share one password so the credentials are easy to
# document and rotate — NEVER reuse this pattern for real accounts.
DEMO_PASSWORD = "NevoDemo!2026"

# Demo beat cluster
ID_CLUSTER_DEMO = "cluster-demo-india-health"


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
        Country(
            id=ID_COUNTRY_BR, code="BR", name="Brazil",
            supported_languages="pt,en",
            administrative_hierarchy_adapter="brazil_v1",
            status="active",
        ),
        Country(
            id=ID_COUNTRY_RU, code="RU", name="Russia",
            supported_languages="ru,en",
            administrative_hierarchy_adapter="russia_v1",
            status="active",
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
    rows = [
        Department(id=ID_DEPT_HEALTH_IN, country_id=ID_COUNTRY_IN, name="Health", code="health"),
        Department(id=ID_DEPT_WATER_IN, country_id=ID_COUNTRY_IN, name="Water & Sanitation", code="water"),
        Department(id=ID_DEPT_PWD_IN, country_id=ID_COUNTRY_IN, name="Public Works (Roads & Electricity)", code="pwd"),
        Department(id=ID_DEPT_EDU_IN, country_id=ID_COUNTRY_IN, name="Education", code="education"),
        Department(id=ID_DEPT_ENV_IN, country_id=ID_COUNTRY_IN, name="Environment & Waste", code="environment"),
    ]
    for r in rows:
        if not _exists(Department, r.id):
            db.session.add(r)
    db.session.flush()
    print("  Departments (India): OK")


def seed_accounts():
    """
    Real CitizenAccount / GovernmentAccount rows (Phase 0) — replaces the
    old session-only demo actor registry. Same IDs as before so existing
    seeded content (the demo-beat cluster, any pre-existing
    GovernmentDecision.decided_by_id values) keeps resolving correctly.

    All demo accounts share DEMO_PASSWORD — printed below so a fresh
    contributor can log in immediately. Rotate before any real deployment.
    """
    consent_now = _now()

    citizens = [
        (ID_CITIZEN_IN, "citizen.in@nevo.demo", "Priya Sharma", ID_COUNTRY_IN),
        (ID_CITIZEN_BR, "citizen.br@nevo.demo", "Carlos Oliveira", ID_COUNTRY_BR),
        (ID_CITIZEN_RU, "citizen.ru@nevo.demo", "Natasha Ivanova", ID_COUNTRY_RU),
    ]
    for account_id, email, name, country_id in citizens:
        if not _exists(CitizenAccount, account_id):
            db.session.add(CitizenAccount(
                id=account_id, email=email, password_hash=hash_password(DEMO_PASSWORD),
                full_name=name, country_id=country_id,
                consent_given_at=consent_now, consent_version=CURRENT_CONSENT_VERSION,
            ))

    gov_accounts = [
        # id, email, name, role, country_id, region_id, department_id
        (ID_ADMIN, "national.admin.in@nevo.demo", "National Administrator (India)",
         "national_admin", ID_COUNTRY_IN, None, None),
        (ID_MP_IN, "state.admin.in@nevo.demo", "State Administrator — Maharashtra",
         "state_admin", ID_COUNTRY_IN, ID_REGION_IN_MH, None),
        (ID_PO_IN, "district.officer.in@nevo.demo", "District Officer — Nashik",
         "district_officer", ID_COUNTRY_IN, ID_REGION_IN_MH_NASHIK, None),
        (ID_DEPT_OFFICER_IN, "health.officer.in@nevo.demo", "Health Department Officer — Maharashtra",
         "department_officer", ID_COUNTRY_IN, ID_REGION_IN_MH, ID_DEPT_HEALTH_IN),
        (ID_ANALYST_IN, "analyst.in@nevo.demo", "Evidence Analyst — Maharashtra",
         "analyst", ID_COUNTRY_IN, ID_REGION_IN_MH, None),
        (ID_REVIEWER_IN, "reviewer.in@nevo.demo", "Cluster Reviewer — Maharashtra",
         "reviewer", ID_COUNTRY_IN, ID_REGION_IN_MH, None),
        (ID_MP_BR, "state.admin.br@nevo.demo", "State Administrator (Brazil)",
         "state_admin", ID_COUNTRY_BR, ID_REGION_BR_SP, None),
        (ID_PO_BR, "district.officer.br@nevo.demo", "District Officer (Brazil)",
         "district_officer", ID_COUNTRY_BR, ID_REGION_BR_SP_CAPITAL, None),
        (ID_MP_RU, "state.admin.ru@nevo.demo", "State Administrator (Russia)",
         "state_admin", ID_COUNTRY_RU, ID_REGION_RU_MSK, None),
        (ID_PO_RU, "district.officer.ru@nevo.demo", "District Officer (Russia)",
         "district_officer", ID_COUNTRY_RU, ID_REGION_RU_MSK_KHIMKI, None),
    ]
    for account_id, email, name, role, country_id, region_id, department_id in gov_accounts:
        if not _exists(GovernmentAccount, account_id):
            db.session.add(GovernmentAccount(
                id=account_id, email=email, password_hash=hash_password(DEMO_PASSWORD),
                full_name=name, role=role, country_id=country_id,
                region_id=region_id, department_id=department_id,
                provisioned_by=None,
            ))

    db.session.flush()
    print(f"  Accounts seeded — demo password for all: {DEMO_PASSWORD!r} (rotate before real deployment)")
    print("  Citizen logins:  citizen.in@nevo.demo / citizen.br@nevo.demo / citizen.ru@nevo.demo")
    print("  Government logins: national.admin.in@nevo.demo, state.admin.in@nevo.demo, "
          "district.officer.in@nevo.demo, health.officer.in@nevo.demo, analyst.in@nevo.demo, "
          "reviewer.in@nevo.demo (+ .br / .ru variants)")


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
    from app.auth.security import generate_complaint_id
    for i, cid in enumerate(citizen_ids):
        r = Report(
            id=f"demo-report-in-{i+1}",
            complaint_id=generate_complaint_id("IN"),
            anonymous_token=cid,  # synthetic demo identities, not real accounts
            country_id=ID_COUNTRY_IN,
            region_id=ID_REGION_IN_MH_NASHIK,
            category_id=ID_CAT_HEALTH,
            original_raw_input=(
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
# Main
# ---------------------------------------------------------------------------

def run():
    with app.app_context():
        print("Seeding NEVO demo data...")
        seed_countries()
        seed_regions()
        seed_district_regions()
        seed_categories()
        seed_departments()
        seed_accounts()
        seed_reference_data()
        seed_demo_beat()
        db.session.commit()
        print("Seed complete.")


if __name__ == "__main__":
    run()
