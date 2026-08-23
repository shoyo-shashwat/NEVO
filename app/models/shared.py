# models/shared.py
# Cross-cutting reference entities: Country, AdministrativeRegion, Category, EventLog.
# All other models import from here — never the other way around.

import uuid
from datetime import datetime, timezone

from app.extensions import db


def _uuid():
    """Default factory for UUID primary keys (stored as strings for broad DB compat)."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Country
# ---------------------------------------------------------------------------

class Country(db.Model):
    """
    One row per supported country.
    MVP instances: India (IN), Brazil (BR), Russia (RU).

    supported_languages: comma-separated ISO 639-1 codes stored as a plain
    string (e.g. "hi,en") — simple enough for MVP, avoids a join table.
    administrative_hierarchy_adapter: opaque config key that country-specific
    code can use to resolve region level names (e.g. "india_v1").
    """
    __tablename__ = "countries"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    code = db.Column(db.String(4), unique=True, nullable=False)   # ISO 3166-1 alpha-2
    name = db.Column(db.String(100), nullable=False)
    supported_languages = db.Column(db.Text, nullable=False, default="en")
    administrative_hierarchy_adapter = db.Column(db.String(50), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="active")

    # Relationships
    regions = db.relationship("AdministrativeRegion", back_populates="country",
                               lazy="dynamic", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Country {self.code} — {self.name}>"


# ---------------------------------------------------------------------------
# AdministrativeRegion
# ---------------------------------------------------------------------------

class AdministrativeRegion(db.Model):
    """
    Hierarchical region tree, scoped to a Country.
    Self-referencing via parent_region_id for national → state → district → constituency.

    level values (Progress Log §5.4.1):
      national | state_province | district_municipality | constituency
    """
    __tablename__ = "administrative_regions"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    country_id = db.Column(db.String(36), db.ForeignKey("countries.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    level = db.Column(
        db.Enum(
            "national",
            "state_province",
            "district_municipality",
            "constituency",
            name="region_level_enum",
        ),
        nullable=False,
    )
    parent_region_id = db.Column(
        db.String(36), db.ForeignKey("administrative_regions.id"), nullable=True
    )

    # Relationships
    country = db.relationship("Country", back_populates="regions")
    children = db.relationship(
        "AdministrativeRegion",
        backref=db.backref("parent", remote_side=[id]),
        lazy="dynamic",
    )

    def __repr__(self):
        return f"<AdministrativeRegion {self.name} ({self.level})>"


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------

class Category(db.Model):
    """
    Fixed MVP taxonomy of development problem categories.
    translations: JSON dict keyed by ISO 639-1 code, e.g. {"hi": "स्वास्थ्य सेवा", "pt": "Saúde"}.

    MVP set (Progress Log §5.4.1):
      healthcare_access | water_sanitation | roads_transport |
      electricity_utilities | education_access | waste_environment
    """
    __tablename__ = "categories"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)          # canonical English name
    translations = db.Column(db.JSON, nullable=False, default=dict)

    def __repr__(self):
        return f"<Category {self.code}>"


# ---------------------------------------------------------------------------
# EventLog
# ---------------------------------------------------------------------------

class EventLog(db.Model):
    """
    Append-only audit / timeline trail.
    Links a Report (and optionally its DemandCluster) to a lifecycle stage.

    stage values drive the Citizen Timeline (Progress Log §13.1 / Citizen §9):
      Submitted | AIUnderstood | JoinedDemand | UnderReview |
      Decision | Implementation | Outcome

    This table is append-only — no updates, no deletes in normal operation.
    """
    __tablename__ = "event_logs"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    report_id = db.Column(
        db.String(36), db.ForeignKey("reports.id"), nullable=False
    )
    demand_cluster_id = db.Column(
        db.String(36), db.ForeignKey("demand_clusters.id"), nullable=True
    )
    stage = db.Column(
        db.Enum(
            "Submitted",
            "AIUnderstood",
            "JoinedDemand",
            "UnderReview",
            "Decision",
            "Implementation",
            "Outcome",
            name="event_stage_enum",
        ),
        nullable=False,
    )
    metadata_ = db.Column("metadata", db.JSON, nullable=True)
    timestamp = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    report = db.relationship("Report", back_populates="event_logs")
    demand_cluster = db.relationship("DemandCluster", back_populates="event_logs")

    def __repr__(self):
        return f"<EventLog {self.stage} report={self.report_id}>"
