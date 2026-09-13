# extensions.py
# Single home for Flask extension instances so that models, blueprints,
# and the app factory can all import from here without circular imports.

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()

# In-memory limiter storage — adequate for a single-process deployment.
# Note for future horizontal scaling: switch storage_uri to a shared Redis
# instance so rate limits are enforced across processes/instances, not
# per-process (see Production Gap Analysis — P2 scaling work).
limiter = Limiter(key_func=get_remote_address, default_limits=["200 per hour"])
