# government/__init__.py
from flask import Blueprint

government_bp = Blueprint(
    "government",
    __name__,
    template_folder="templates",
    url_prefix="/gov",
)

from app.government import routes  # noqa: F401, E402
