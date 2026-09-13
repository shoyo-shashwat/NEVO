# NEVO

Flask monolith: citizen complaints → aggregated collective demand → government decisions, backed by PostgreSQL + PostGIS + pgvector. (Internal/historical codename: "BRICS People First" — still seen in some comments and DB values.)

## Setup (local dev)

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then fill in DATABASE_URL, SECRET_KEY, GROQ_API_KEY, COHERE_API_KEY, ELEVENLABS_API_KEY
                                    # SMTP_* can stay empty — password reset emails just won't send until configured

flask db upgrade                   # applies migrations (Postgres must have `postgis` and `vector` extensions enabled)
python3 seed/seed_data.py          # departments, seeded accounts, categories, regions, a sample cluster

flask run
```

Open `http://127.0.0.1:5000/` — that redirects to the citizen home page. "Report a problem" works immediately with no login (anonymous submission is fully supported); creating a citizen account is optional and adds tracking/history/notifications.

`seed/seed_data.py` prints the seeded demo login credentials (one password, `NevoDemo!2026`, shared by every demo account — rotate before any real deployment). It creates one citizen account per seeded country, plus government accounts for every role (national_admin, state_admin, district_officer, department_officer, analyst, reviewer) for India specifically. Government accounts are never self-signup — sign in at `/auth/government/login`, and a `national_admin` can create further accounts at `/gov/admin` → "+ Create account".

To bootstrap a *real* first national_admin (outside the seed script): `flask create-national-admin --email you@example.com --password ... --full-name "Your Name"`.

## Structure

- `app/auth/` — real authentication (Flask-Login), RBAC (`rbac.py`), security helpers (`security.py`: password hashing, anonymous-identity cookie, complaint-ID generation), and the auth blueprint (signup/login/logout/password reset/government account provisioning)
- `app/citizen/` — citizen-facing blueprint (report, community, map, timeline, complaint tracking)
- `app/government/` — government-facing blueprint (dashboard, evidence, decisions, projects/outcomes, admin)
- `app/models/` — shared SQLAlchemy models; `accounts.py` holds `CitizenAccount`/`GovernmentAccount`/`Department`/`AuditLog`/`PasswordResetToken`
- `app/services/` — AI clients (Groq, Cohere, ElevenLabs), demand matching, priority scoring, evidence storage, `audit.py` (audit log writes), `email_client.py` (SMTP, real integration boundary — see below), `report_lifecycle.py` (propagates cluster/project/outcome events down to member reports' status)
- `app/static/vendor/leaflet/` — the Leaflet mapping library, vendored locally (not loaded from a CDN) so the map still works on networks that block unpkg.com
- `migrations/` — Alembic migrations (`flask db migrate` / `flask db upgrade` — never hand-edit the schema)
- `seed/seed_data.py` — demo data + demo accounts (see Setup above)
- `templates/` — shared shell (`base.html`, with the persistent nav bar); each blueprint has its own `templates/<blueprint>/` for its screens
- `tests/` — pytest suite (auth flows, RBAC scoping, report submission, government-role smoke tests) — run with `pytest tests/ -q`
- `docs/superpowers/specs/` and `docs/superpowers/plans/` — the Phase 0 production-readiness design doc and implementation plan

## Notes

- The two blueprints never import from each other — cross-blueprint reads go through `app/models/` directly.
- `render.yaml` + `start.sh` are the Render.com deploy config.
- Evidence photos are stored on local disk under `app/static/uploads/evidence/` — this is ephemeral on most PaaS filesystems (cleared on redeploy); swap `app/services/evidence_storage.py` for S3/ImageKit if you need durability.
- The map's background tile imagery comes from OpenStreetMap's public tile servers at runtime — that part needs a working internet connection the same way any map does. The mapping *library* itself is vendored locally so it doesn't depend on a CDN.
- Password reset / notification emails require `SMTP_HOST` (and related `SMTP_*` vars) to be set. Without them, `app/services/email_client.py` raises `EmailNotConfiguredError` rather than pretending to send — the user sees an honest "email delivery not configured" message instead of a silent no-op.
- Rate limiting (`Flask-Limiter`) uses in-memory storage by default (`RATELIMIT_STORAGE_URI=memory://`) — fine for a single instance; point it at Redis before running more than one app instance.
