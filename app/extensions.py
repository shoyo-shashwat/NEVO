# extensions.py
# Single home for Flask extension instances so that models, blueprints,
# and the app factory can all import from here without circular imports.

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

db = SQLAlchemy()
migrate = Migrate()
