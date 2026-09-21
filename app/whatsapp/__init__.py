# whatsapp/__init__.py
from flask import Blueprint

whatsapp_bp = Blueprint(
    "whatsapp",
    __name__,
    url_prefix="/whatsapp",
)

from app.whatsapp import routes  # noqa: F401, E402 — registers route handlers
