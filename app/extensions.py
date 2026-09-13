# extensions.py
# Single home for Flask extension instances so that models, blueprints,
# and the app factory can all import from here without circular imports.

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
# In-memory storage for now (Phase 0 §8) — no Redis dependency yet since
# Phase 1's background job queue hasn't been built. Swapping to a Redis
# storage_uri later is a one-line config change, not a rewrite.
limiter = Limiter(key_func=get_remote_address)
