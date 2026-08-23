# citizen/__init__.py
from flask import Blueprint

citizen_bp = Blueprint(
    "citizen",
    __name__,
    template_folder="templates",
    url_prefix="/citizen",
)

from app.citizen import routes  # noqa: F401, E402 — registers route handlers
