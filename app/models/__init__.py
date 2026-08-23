# models/__init__.py
# Re-exports db so that app factory and all model files share one instance.
# Import order matters for Flask-Migrate to discover all tables.

from app.extensions import db  # noqa: F401

from app.models.shared import Country, AdministrativeRegion, Category, EventLog  # noqa: F401
from app.models.citizen_models import Report, Contribution, Verification, Evidence  # noqa: F401
from app.models.demand_cluster import DemandCluster  # noqa: F401
from app.models.government_models import GovernmentDecision, Project, Outcome  # noqa: F401
from app.models.reference_data import (  # noqa: F401
    InfrastructureDataPoint,
    DemographicDataPoint,
    GovernmentInvestment,
)
