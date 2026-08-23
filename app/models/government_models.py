# models/government_models.py
# Government-side aggregate roots: GovernmentDecision, Project, Outcome.
#
# Key invariants (Progress Log §5.3, §13.3, §13.4):
#
#   GovernmentDecision.reason is HUMAN-AUTHORED ONLY.
#   AI must never write to this field under any circumstances (§5.3.1 / Master Prompt §6).
#
#   Outcome.status defaults to 'AwaitingOutcomeData' and must never be
#   auto-filled with invented numbers.  A Project can sit at 'Completion'
#   indefinitely while its Outcome stays unresolved — this is correct (§13.4).
#
#   GovernmentDecision is its own aggregate root — references DemandCluster
#   by ID only, not owned by it (§5.3.5).

import uuid
from datetime import datetime, timezone

from app.extensions import db


def _uuid():
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# GovernmentDecision  (aggregate root)
# ---------------------------------------------------------------------------

class GovernmentDecision(db.Model):
    """
    A recorded human decision about a DemandCluster.

    decided_by_id: references the session actor (PolicymakerMP or
                   PlanningDepartmentOfficer row id).
    decided_by_role: stored explicitly so queries can filter by role type
                     without joining to two separate actor tables.

    reason: REQUIRED, HUMAN-AUTHORED.  AI may simplify this text for citizen
            display but must never be the original author.

    linked_project_id: optional at creation; can be set immediately
                       (link existing) or after (create new project first).
    """
    __tablename__ = "government_decisions"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)

    # References — IDs only, no foreign-key to a cross-blueprint table
    demand_cluster_id = db.Column(
        db.String(36), db.ForeignKey("demand_clusters.id"), nullable=False, index=True
    )
    country_id = db.Column(
        db.String(36), db.ForeignKey("countries.id"), nullable=False
    )  # denormalized from cluster for query convenience (§5.4.2)

    decided_by_id = db.Column(db.String(36), nullable=False)
    decided_by_role = db.Column(
        db.Enum("mp", "planning_officer", name="decision_actor_role_enum"),
        nullable=False,
    )

    decision_type = db.Column(
        db.Enum(
            "Prioritize",
            "Defer",
            "Deprioritize",
            "NeedsValidation",
            "Redirected",
            name="decision_type_enum",
        ),
        nullable=False,
    )

    # Human-authored only — hard invariant
    reason = db.Column(db.Text, nullable=False)

    linked_project_id = db.Column(
        db.String(36), db.ForeignKey("projects.id"), nullable=True
    )

    timestamp = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    demand_cluster = db.relationship("DemandCluster", foreign_keys=[demand_cluster_id])
    country = db.relationship("Country", foreign_keys=[country_id])
    linked_project = db.relationship(
        "Project", foreign_keys=[linked_project_id], back_populates="decisions"
    )

    def __repr__(self):
        return (
            f"<GovernmentDecision {self.decision_type} "
            f"cluster={self.demand_cluster_id}>"
        )


# ---------------------------------------------------------------------------
# Project  (aggregate root)
# ---------------------------------------------------------------------------

class Project(db.Model):
    """
    A government project or intervention linked to one or more DemandClusters.

    Can be created fresh from a GovernmentDecision, or an existing project
    can be linked (§5.3.6 — both paths are valid MVP scope).

    status lifecycle (§13.4):
      Planning → Approval → Tender → Construction → Completion

    milestones: JSON list of milestone objects, e.g.:
      [{"label": "Survey complete", "date": "2025-03", "status": "done"}]
    Only shown to citizens when officially provided — no invented dates.
    """
    __tablename__ = "projects"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)

    official_project_id = db.Column(db.String(100), nullable=True)  # external reference
    name = db.Column(db.String(300), nullable=False)
    country_id = db.Column(
        db.String(36), db.ForeignKey("countries.id"), nullable=False
    )
    region_id = db.Column(
        db.String(36), db.ForeignKey("administrative_regions.id"), nullable=True
    )

    status = db.Column(
        db.Enum(
            "Planning",
            "Approval",
            "Tender",
            "Construction",
            "Completion",
            name="project_status_enum",
        ),
        nullable=False,
        default="Planning",
    )

    milestones = db.Column(db.JSON, nullable=False, default=list)

    linked_demand_cluster_id = db.Column(
        db.String(36), db.ForeignKey("demand_clusters.id"), nullable=True
    )

    start_date = db.Column(db.Date, nullable=True)
    expected_completion = db.Column(db.Date, nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    country = db.relationship("Country", foreign_keys=[country_id])
    region = db.relationship("AdministrativeRegion", foreign_keys=[region_id])
    linked_demand_cluster = db.relationship(
        "DemandCluster", foreign_keys=[linked_demand_cluster_id]
    )
    decisions = db.relationship(
        "GovernmentDecision", back_populates="linked_project",
        foreign_keys="GovernmentDecision.linked_project_id",
        lazy="dynamic",
    )
    outcomes = db.relationship(
        "Outcome", back_populates="project",
        lazy="dynamic", cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Project {self.name!r} status={self.status}>"


# ---------------------------------------------------------------------------
# Outcome  (child of Project)
# ---------------------------------------------------------------------------

class Outcome(db.Model):
    """
    Tracks whether a completed project actually resolved the underlying problem.

    Hard invariants (Progress Log §13.4 / Master Prompt §6):
      - status defaults to 'AwaitingOutcomeData' — never auto-verified.
      - after_indicator and impact_percent must never be invented; they stay
        NULL until real official/community evidence arrives.
      - A Project at 'Completion' with Outcome still 'AwaitingOutcomeData'
        is intentional and correct — do not treat as a bug to fix.
    """
    __tablename__ = "outcomes"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)

    project_id = db.Column(
        db.String(36), db.ForeignKey("projects.id"), nullable=False, index=True
    )
    demand_cluster_id = db.Column(
        db.String(36), db.ForeignKey("demand_clusters.id"), nullable=False
    )

    before_indicator = db.Column(db.Text, nullable=True)
    after_indicator = db.Column(db.Text, nullable=True)   # NULL until verified data arrives
    impact_percent = db.Column(db.Float, nullable=True)   # NULL until verified data arrives

    status = db.Column(
        db.Enum(
            "AwaitingOutcomeData",
            "Verified",
            name="outcome_status_enum",
        ),
        nullable=False,
        default="AwaitingOutcomeData",   # hard invariant — never change this default
    )

    timestamp = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    project = db.relationship("Project", back_populates="outcomes")
    demand_cluster = db.relationship("DemandCluster", foreign_keys=[demand_cluster_id])

    def __repr__(self):
        return f"<Outcome project={self.project_id} status={self.status}>"
