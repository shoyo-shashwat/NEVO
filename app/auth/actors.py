# auth/actors.py
# Single source of truth for the seeded demo/reviewer account definitions.
#
# Mirrors the 13 accounts that already existed in the database before this
# repo had any auth code (3 citizen, 10 government — see
# app/models/auth_models.py provenance note). seed/seed_data.py uses this
# list to idempotently ensure each account exists and is marked
# is_demo=True; it never overwrites an existing account's password.
#
# account_type: "citizen" | "government"
# role: only meaningful for account_type == "government" — one of
#       GOVERNMENT_ROLES (app/models/auth_models.py)
# department_code: only meaningful for role == "department_officer" —
#       matches Department.code for the given country

DEMO_ACCOUNTS = [
    # --- Citizens ---
    {"account_type": "citizen", "email": "citizen.in@nevo.demo",
     "full_name": "Priya Sharma", "country_code": "IN"},
    {"account_type": "citizen", "email": "citizen.br@nevo.demo",
     "full_name": "Carlos Oliveira", "country_code": "BR"},
    {"account_type": "citizen", "email": "citizen.ru@nevo.demo",
     "full_name": "Natasha Ivanova", "country_code": "RU"},

    # --- Government: India ---
    {"account_type": "government", "email": "national.admin.in@nevo.demo",
     "full_name": "National Administrator (India)", "role": "national_admin",
     "country_code": "IN"},
    {"account_type": "government", "email": "state.admin.in@nevo.demo",
     "full_name": "State Administrator – Maharashtra", "role": "state_admin",
     "country_code": "IN"},
    {"account_type": "government", "email": "district.officer.in@nevo.demo",
     "full_name": "District Officer – Nashik", "role": "district_officer",
     "country_code": "IN"},
    {"account_type": "government", "email": "health.officer.in@nevo.demo",
     "full_name": "Health Department Officer – Maharashtra", "role": "department_officer",
     "country_code": "IN", "department_code": "health"},
    {"account_type": "government", "email": "analyst.in@nevo.demo",
     "full_name": "Evidence Analyst – Maharashtra", "role": "analyst",
     "country_code": "IN"},
    {"account_type": "government", "email": "reviewer.in@nevo.demo",
     "full_name": "Cluster Reviewer – Maharashtra", "role": "reviewer",
     "country_code": "IN"},

    # --- Government: Brazil ---
    {"account_type": "government", "email": "state.admin.br@nevo.demo",
     "full_name": "State Administrator (Brazil)", "role": "state_admin",
     "country_code": "BR"},
    {"account_type": "government", "email": "district.officer.br@nevo.demo",
     "full_name": "District Officer (Brazil)", "role": "district_officer",
     "country_code": "BR"},

    # --- Government: Russia ---
    {"account_type": "government", "email": "state.admin.ru@nevo.demo",
     "full_name": "State Administrator (Russia)", "role": "state_admin",
     "country_code": "RU"},
    {"account_type": "government", "email": "district.officer.ru@nevo.demo",
     "full_name": "District Officer (Russia)", "role": "district_officer",
     "country_code": "RU"},
]
