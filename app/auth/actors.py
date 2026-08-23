# auth/actors.py
# Single source of truth for the 10 demo actor definitions.
#
# Imported by:
#   app/__init__.py       — _bootstrap_demo_actors() on every app boot
#   seed/seed_data.py     — seed_actors() when seeding the database
#
# Actor IDs must stay in sync with the ID_* constants in seed/seed_data.py.
# These are session-only identities (Progress Log §17.6) — no DB table.

DEMO_ACTORS = [
    # India
    {"id": "actor-citizen-in", "role": "citizen",          "name": "Priya Sharma (India)",           "country_code": "IN"},
    {"id": "actor-mp-in",      "role": "mp",               "name": "MP Ramesh Kumar (India)",        "country_code": "IN"},
    {"id": "actor-po-in",      "role": "planning_officer", "name": "Officer Anita Desai (India)",    "country_code": "IN"},
    # Brazil
    {"id": "actor-citizen-br", "role": "citizen",          "name": "Carlos Oliveira (Brazil)",       "country_code": "BR"},
    {"id": "actor-mp-br",      "role": "mp",               "name": "MP Fernanda Costa (Brazil)",     "country_code": "BR"},
    {"id": "actor-po-br",      "role": "planning_officer", "name": "Officer João Alves (Brazil)",    "country_code": "BR"},
    # Russia
    {"id": "actor-citizen-ru", "role": "citizen",          "name": "Natasha Ivanova (Russia)",       "country_code": "RU"},
    {"id": "actor-mp-ru",      "role": "mp",               "name": "MP Dmitri Volkov (Russia)",      "country_code": "RU"},
    {"id": "actor-po-ru",      "role": "planning_officer", "name": "Officer Elena Petrova (Russia)", "country_code": "RU"},
    # Admin
    {"id": "actor-admin",      "role": "admin",            "name": "Platform Admin",                 "country_code": "IN"},
]
