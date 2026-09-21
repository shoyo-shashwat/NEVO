# auth/__init__.py
from flask import Blueprint

auth_bp = Blueprint(
    "auth",
    __name__,
    template_folder="templates",
    url_prefix="",
)

from app.auth import routes  # noqa: F401, E402 — registers route handlers
