# models/reference_data.py
# Persisted reference / seeded data entities:
#   InfrastructureDataPoint, DemographicDataPoint, GovernmentInvestment.
#
# These are seeded once (or periodically synced from external sources) and
# read during request-time computation of InfrastructureGapAssessment and
# InvestmentAlignment.  They are NOT created or edited by government users
# through the platform UI (§5.3.1).
#
# Freshness / provenance tracking (Government §10) lives on these rows —
# source, source_last_updated, platform_last_synced — NOT on the computed
# value objects (InfrastructureGapAssessment / InvestmentAlignment).

import uuid
from datetime import datetime, timezone

from app.extensions import db


def _uuid():
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# InfrastructureDataPoint
# ---------------------------------------------------------------------------

class InfrastructureDataPoint(db.Model):
    """
    One row of official infrastructure data for a given category + region.

    official_coverage: government's reported service coverage level.
    nearest_facility_distance: km to the nearest relevant facility (nullable).
    source / source_last_updated / platform_last_synced: provenance &
    freshness tracking per Government §10.
    """
    __tablename__ = "infrastructure_data_points"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)

    country_id = db.Column(
        db.String(36), db.ForeignKey("countries.id"), nullable=False, index=True
    )
    region_id = db.Column(
        db.String(36), db.ForeignKey("administrative_regions.id"), nullable=True
    )
    category_id = db.Column(
        db.String(36), db.ForeignKey("categories.id"), nullable=False
    )

    official_coverage = db.Column(
        db.Enum("Low", "Medium", "High", name="coverage_level_enum"),
        nullable=False,
    )
    nearest_facility_distance_km = db.Column(db.Float, nullable=True)
    capacity_notes = db.Column(db.Text, nullable=True)  # free-form extra context

    # Provenance & freshness (Government §10)
    source = db.Column(db.String(300), nullable=True)
    source_url = db.Column(db.Text, nullable=True)
    source_last_updated = db.Column(db.Date, nullable=True)
    platform_last_synced = db.Column(
        db.DateTime(timezone=True), nullable=True
    )
    data_period = db.Column(db.String(50), nullable=True)    # e.g. "2022-23"
    geographic_granularity = db.Column(db.String(100), nullable=True)
    verification_status = db.Column(
        db.Enum("unverified", "verified", "needs_review",
                name="data_verification_enum"),
        nullable=False,
        default="unverified",
    )
    freshness_status = db.Column(
        db.Enum("recent", "stale", "unknown", name="freshness_status_enum"),
        nullable=False,
        default="unknown",
    )

    # Relationships
    country = db.relationship("Country", foreign_keys=[country_id])
    region = db.relationship("AdministrativeRegion", foreign_keys=[region_id])
    category = db.relationship("Category", foreign_keys=[category_id])

    def __repr__(self):
        return (
            f"<InfrastructureDataPoint "
            f"coverage={self.official_coverage} region={self.region_id}>"
        )


# ---------------------------------------------------------------------------
# DemographicDataPoint
# ---------------------------------------------------------------------------

class DemographicDataPoint(db.Model):
    """
    Population / demographic context for a region and category.

    Identified as a new entity in Progress Log §5.3.1 — was previously an
    implied concept ("Demographic Context") with no entity status.

    population_affected: estimated number of people affected by a gap in
                         this category within this region.
    """
    __tablename__ = "demographic_data_points"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)

    country_id = db.Column(
        db.String(36), db.ForeignKey("countries.id"), nullable=False, index=True
    )
    region_id = db.Column(
        db.String(36), db.ForeignKey("administrative_regions.id"), nullable=True
    )
    category_id = db.Column(
        db.String(36), db.ForeignKey("categories.id"), nullable=False
    )

    population_affected = db.Column(db.Integer, nullable=True)
    population_total = db.Column(db.Integer, nullable=True)
    demographic_notes = db.Column(db.Text, nullable=True)

    # Provenance & freshness (same pattern as InfrastructureDataPoint)
    source = db.Column(db.String(300), nullable=True)
    source_url = db.Column(db.Text, nullable=True)
    source_last_updated = db.Column(db.Date, nullable=True)
    platform_last_synced = db.Column(db.DateTime(timezone=True), nullable=True)
    data_period = db.Column(db.String(50), nullable=True)
    verification_status = db.Column(
        db.Enum("unverified", "verified", "needs_review",
                name="demo_verification_enum"),
        nullable=False,
        default="unverified",
    )
    freshness_status = db.Column(
        db.Enum("recent", "stale", "unknown", name="demo_freshness_enum"),
        nullable=False,
        default="unknown",
    )

    # Relationships
    country = db.relationship("Country", foreign_keys=[country_id])
    region = db.relationship("AdministrativeRegion", foreign_keys=[region_id])
    category = db.relationship("Category", foreign_keys=[category_id])

    def __repr__(self):
        return (
            f"<DemographicDataPoint "
            f"pop_affected={self.population_affected} region={self.region_id}>"
        )


# ---------------------------------------------------------------------------
# GovernmentInvestment
# ---------------------------------------------------------------------------

class GovernmentInvestment(db.Model):
    """
    An existing government programme, project, funding stream, or pipeline.

    type values (Government §8):
      Programme | Project | Funding | Pipeline

    status: current state of the investment/intervention.

    Used by InvestmentAlignment (services/investment_alignment.py) to
    determine whether citizen demand is already being addressed.
    """
    __tablename__ = "government_investments"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)

    name = db.Column(db.String(300), nullable=False)
    country_id = db.Column(
        db.String(36), db.ForeignKey("countries.id"), nullable=False, index=True
    )
    region_id = db.Column(
        db.String(36), db.ForeignKey("administrative_regions.id"), nullable=True
    )
    category_id = db.Column(
        db.String(36), db.ForeignKey("categories.id"), nullable=False
    )

    type = db.Column(
        db.Enum(
            "Programme", "Project", "Funding", "Pipeline",
            name="investment_type_enum",
        ),
        nullable=False,
    )
    status = db.Column(db.String(100), nullable=False, default="active")

    budget = db.Column(db.Numeric(18, 2), nullable=True)      # optional
    coverage_area = db.Column(db.Text, nullable=True)
    target_population = db.Column(db.Integer, nullable=True)

    start_date = db.Column(db.Date, nullable=True)
    expected_completion = db.Column(db.Date, nullable=True)

    # Provenance & freshness (Government §10)
    source = db.Column(db.String(300), nullable=True)
    source_url = db.Column(db.Text, nullable=True)
    source_last_updated = db.Column(db.Date, nullable=True)
    platform_last_synced = db.Column(db.DateTime(timezone=True), nullable=True)
    data_period = db.Column(db.String(50), nullable=True)
    geographic_granularity = db.Column(db.String(100), nullable=True)
    verification_status = db.Column(
        db.Enum("unverified", "verified", "needs_review",
                name="inv_verification_enum"),
        nullable=False,
        default="unverified",
    )
    freshness_status = db.Column(
        db.Enum("recent", "stale", "unknown", name="inv_freshness_enum"),
        nullable=False,
        default="unknown",
    )

    # Relationships
    country = db.relationship("Country", foreign_keys=[country_id])
    region = db.relationship("AdministrativeRegion", foreign_keys=[region_id])
    category = db.relationship("Category", foreign_keys=[category_id])

    def __repr__(self):
        return f"<GovernmentInvestment {self.name!r} type={self.type} status={self.status}>"
