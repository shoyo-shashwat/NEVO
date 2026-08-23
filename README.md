# BRICS People First

Flask monolith: citizen complaints → aggregated collective demand → government decisions, backed by PostgreSQL + PostGIS + pgvector.

## Setup (local dev)

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then fill in DATABASE_URL, SECRET_KEY, GROQ_API_KEY, COHERE_API_KEY, ELEVENLABS_API_KEY

flask db upgrade                   # applies migrations (Postgres must have `postgis` and `vector` extensions enabled)
python3 seed/seed_data.py          # demo actors, categories, regions, a sample cluster

flask run
```

Open `http://127.0.0.1:5000/` — that's the landing page explaining the product. "Report a problem" and "See what your community needs" work immediately, no login required. Click "Demo sign-in" (or go straight to `/login`) to pick a seeded citizen, MP, planning officer, or admin identity if you want a personal timeline or want to see the government side — country-grouped demo credentials live on that dedicated page, not on the landing page itself.

## Structure

- `app/citizen/` — citizen-facing blueprint (report, community, map, timeline)
- `app/government/` — government-facing blueprint (dashboard, evidence, decisions, projects/outcomes)
- `app/models/` — shared SQLAlchemy models; the only thing the two blueprints share
- `app/services/` — AI clients (Groq, Cohere, ElevenLabs), demand matching, priority scoring, evidence storage, and the shared per-country map view (`map_view.py`) used by both blueprints
- `app/static/vendor/leaflet/` — the Leaflet mapping library, vendored locally (not loaded from a CDN) so the map still works on networks that block unpkg.com
- `migrations/` — Alembic migrations (`flask db migrate` / `flask db upgrade` — never hand-edit the schema)
- `seed/seed_data.py` — demo data
- `templates/` — shared shell (`base.html`, with the persistent nav bar), the landing page (`role_select.html`), and the dedicated demo sign-in page (`login.html`); each blueprint has its own `templates/<blueprint>/` for its screens

## Notes

- The two blueprints never import from each other — cross-blueprint reads go through `app/models/` directly.
- `render.yaml` + `start.sh` are the Render.com deploy config.
- Evidence photos are stored on local disk under `app/static/uploads/evidence/` — this is ephemeral on most PaaS filesystems (cleared on redeploy); swap `app/services/evidence_storage.py` for S3/ImageKit if you need durability.
- The map's background tile imagery comes from OpenStreetMap's public tile servers at runtime — that part needs a working internet connection the same way any map does. The mapping *library* itself is vendored locally so it doesn't depend on a CDN.
