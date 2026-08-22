# models/demand_cluster.py
# DemandCluster — aggregate root that owns Contribution and Verification.
#
# Key invariants (Progress Log §5.2.2, §13.2, §13.3):
#
#   total_reports and unique_contributors are DERIVED properties — always
#   computed from owned Contribution children, never stored as independently-
#   writable columns.  This is enforced by the Python properties below;
#   no code path should ever try to set them directly.
#
#   active_status is driven ONLY by GovernmentDecision + Outcome signals.
#   Verification votes never touch this field.
#
#   No DemandCluster is created silently — every creation requires either
#   a high-confidence system suggestion (citizen-confirmable) or an explicit
#   citizen action (confidence-tiered routing, §5.2.3).
#
# Schema v2 — geospatial + embeddings (Step 4):
#   centroid_wkt (Text placeholder) replaced by:
#     centroid  — GeoAlchemy2 GEOGRAPHY(Point, 4326) for PostGIS map queries
#     embedding — pgvector VECTOR(1024) for Cohere Embed v4 similarity search
#   Migration: "schema v2 — geospatial + embeddings"

import uuid
from datetime import datetime, timezone

from sqlalchemy import func
from geoalchemy2 import Geography
from pgvector.sqlalchemy import Vector

from app.extensions import db


def _uuid():
    return str(uuid.uuid4())


class DemandCluster(db.Model):
    """
    Aggregate root representing a shared community development problem.

    User-facing label: "Community issue" (§5.2.4).

    active_status drives the official timeline badge.
    review_status tracks the government workflow state (§13.3).

    centroid: PostGIS GEOGRAPHY(Point, 4326) — WGS84 lon/lat point.
              Used for map hotspot queries and proximity filtering.
              Set when the cluster is created from its first Report's location.

    embedding: pgvector VECTOR(1024) — Cohere Embed v4 embedding of the
               cluster's aggregated problem summary.
               Used by demand_matching.py for cosine similarity search.
               Asymmetric search: stored with input_type="search_document".
    """
    __tablename__ = "demand_clusters"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)

    # Scope
    country_id = db.Column(
        db.String(36), db.ForeignKey("countries.id"), nullable=False
    )
    # region_ids stored as JSON list so a cluster can span multiple localities
    # (Progress Log §5.4.2 note: "region-set").
    region_ids = db.Column(db.JSON, nullable=True, default=list)
    category_id = db.Column(
        db.String(36), db.ForeignKey("categories.id"), nullable=False
    )

    # Geospatial — PostGIS GEOGRAPHY(Point) (schema v2, Step 4)
    # Replaces the centroid_wkt Text placeholder from the initial schema.
    # NOTE: GeoAlchemy2 automatically creates a GIST spatial index for every
    # Geography/Geometry column at column-creation time.  Do NOT add an
    # explicit create_index() call for this column (or any future geography
    # column) in Alembic migrations — it will raise DuplicateTable.
    centroid = db.Column(Geography(geometry_type="POINT", srid=4326), nullable=True)

    # Semantic embedding — pgvector VECTOR(1536) (schema v3, corrected from v2)
    # Cohere embed-v4.0 returns 1536-dimensional vectors (verified empirically).
    # demand_matching.py queries this column using <=> (cosine distance) operator.
    # Stored with input_type="search_document" (see cohere_client.py).
    embedding = db.Column(Vector(1536), nullable=True)

    # Demand signals
    affected_localities = db.Column(db.JSON, nullable=True, default=list)
    trend = db.Column(
        db.Enum("stable", "increasing", "decreasing", name="trend_enum"),
        nullable=False,
        default="stable",
    )
    confidence = db.Column(
        db.Enum("low", "medium", "high", name="cluster_confidence_enum"),
        nullable=False,
        default="medium",
    )

    # Official lifecycle status (§13.2 — driven by government signals only)
    active_status = db.Column(
        db.Enum(
            "Active",
            "UnderGovernmentReview",
            "Deferred",
            "Deprioritized",
            "ActionTaken",
            "Resolved",
            name="cluster_active_status_enum",
        ),
        nullable=False,
        default="Active",
    )

    # Government workflow state (§13.3)
    review_status = db.Column(
        db.Enum(
            "NotReviewed",
            "PendingValidation",
            "UnderReview",
            "Decided",
            name="cluster_review_status_enum",
        ),
        nullable=False,
        default="NotReviewed",
    )

    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships — Contribution and Verification are owned by this aggregate
    contributions = db.relationship(
        "Contribution", back_populates="demand_cluster",
        lazy="dynamic", cascade="all, delete-orphan",
    )
    verifications = db.relationship(
        "Verification", back_populates="demand_cluster",
        lazy="dynamic", cascade="all, delete-orphan",
    )
    event_logs = db.relationship(
        "EventLog", back_populates="demand_cluster",
        lazy="dynamic", cascade="all, delete-orphan",
    )

    # Reference-side relationships (no ownership)
    country = db.relationship("Country", foreign_keys=[country_id])
    category = db.relationship("Category", foreign_keys=[category_id])

    # -----------------------------------------------------------------------
    # Derived counts — NEVER stored columns (§5.2.2 hard invariant)
    # -----------------------------------------------------------------------

    @property
    def total_reports(self) -> int:
        """
        Count of all Contribution rows owned by this cluster.
        Uses a DB-side COUNT so it works efficiently even with lazy='dynamic'.
        """
        return self.contributions.count()

    @property
    def unique_contributors(self) -> int:
        """
        Count of distinct citizen_id values across this cluster's Contributions.
        COUNT(DISTINCT citizen_id) scoped to this cluster — the anti-manipulation
        mechanism (§5.2.2): one citizen submitting 10 reports still counts as 1.
        """
        from app.models.citizen_models import Contribution  # local import avoids circularity
        return (
            db.session.query(func.count(func.distinct(Contribution.citizen_id)))
            .filter(Contribution.demand_cluster_id == self.id)
            .scalar()
            or 0
        )

    @property
    def community_sentiment(self) -> dict:
        """
        Display-only aggregate over Verification votes.
        Returns e.g.:
          {"StillHappening": 82, "Improved": 10, "Worse": 5, "Resolved": 3,
           "total": 100, "still_affected_pct": 82}

        NEVER used to update active_status — read/display only (§13.2).
        Produces the "82% still affected" demo beat alongside "Under Review".
        """
        from app.models.citizen_models import Verification  # local import
        rows = (
            db.session.query(Verification.state, func.count(Verification.id))
            .filter(Verification.demand_cluster_id == self.id)
            .group_by(Verification.state)
            .all()
        )
        counts = {state: cnt for state, cnt in rows}
        total = sum(counts.values()) or 1  # avoid division by zero
        still_affected = counts.get("StillHappening", 0) + counts.get("Worse", 0)
        return {
            **counts,
            "total": total,
            "still_affected_pct": round(still_affected / total * 100),
        }

    def __repr__(self):
        return (
            f"<DemandCluster {self.id} "
            f"active_status={self.active_status} "
            f"review_status={self.review_status}>"
        )
