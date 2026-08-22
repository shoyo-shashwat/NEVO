# models/citizen_models.py
# Citizen-side entities: Report, Contribution, Verification, Evidence.
#
# Key invariants (Progress Log §5.2, §13.1):
#   - Draft is NOT a separate entity or table. Report is created immediately
#     with status='Draft' and transitions in-place to 'Unclustered' once
#     Category + Location are resolved by AI extraction.
#   - originalRawInput + originalLanguage are the single, permanent raw-input
#     field pair — captured once on creation, never overwritten. No duplicate
#     rawTextOriginalLanguage / detectedLanguage fields (§13.1 consolidation).
#   - Contribution owns all five fields: report_id, citizen_id,
#     demand_cluster_id, type, timestamp (§5.2.1).

import uuid
from datetime import datetime, timezone

from app.extensions import db


def _uuid():
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Report  (aggregate root)
# ---------------------------------------------------------------------------

class Report(db.Model):
    """
    One Report per citizen submission.

    Lifecycle (Progress Log §13.1 state machine):
      Draft      — created immediately on input receipt; Category or Location
                   still missing; AI clarification loop running.
      Unclustered — Category + Location confirmed; report is valid/complete;
                    demand matching has not yet run or found no cluster.
      Clustered   — a Contribution record linking this report to a
                    DemandCluster has been created.

    originalRawInput: citizen's first input verbatim (transcribed text if voice).
                      Never overwritten across clarification rounds.
    originalLanguage: ISO 639-1 code detected from that raw input.

    channel: how the input arrived — text | voice | messaging
    """
    __tablename__ = "reports"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)

    # Identity & scope
    citizen_id = db.Column(db.String(36), nullable=False, index=True)
    country_id = db.Column(
        db.String(36), db.ForeignKey("countries.id"), nullable=False
    )
    region_id = db.Column(
        db.String(36), db.ForeignKey("administrative_regions.id"), nullable=True
    )
    category_id = db.Column(
        db.String(36), db.ForeignKey("categories.id"), nullable=True
    )

    # Raw input — permanent, captured once on Report creation (§13.1).
    # NEVER include original_raw_input in any update/edit payload.
    # Clarification rounds may update structured fields (category, severity,
    # etc.) but must never touch this column.  Treat it as write-once.
    original_raw_input = db.Column(db.Text, nullable=False)
    original_language = db.Column(db.String(10), nullable=True)   # ISO 639-1, set on first extract

    # Channel
    channel = db.Column(
        db.Enum("text", "voice", "messaging", name="report_channel_enum"),
        nullable=False,
        default="text",
    )

    # Structured fields — populated/updated by AI extraction rounds
    severity = db.Column(
        db.Enum("low", "medium", "high", "critical", name="severity_enum"),
        nullable=True,
    )
    duration = db.Column(db.String(200), nullable=True)     # free-form: "3 months", "ongoing"
    affected_group = db.Column(db.String(200), nullable=True)

    # Lifecycle status (§13.1 — no separate Draft table)
    status = db.Column(
        db.Enum("Draft", "Unclustered", "Clustered", name="report_status_enum"),
        nullable=False,
        default="Draft",
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

    # Relationships
    country = db.relationship("Country", foreign_keys=[country_id])
    region = db.relationship("AdministrativeRegion", foreign_keys=[region_id])
    category = db.relationship("Category", foreign_keys=[category_id])
    contributions = db.relationship("Contribution", back_populates="report",
                                     lazy="dynamic", cascade="all, delete-orphan")
    evidence = db.relationship("Evidence", back_populates="report",
                                lazy="dynamic", cascade="all, delete-orphan")
    event_logs = db.relationship("EventLog", back_populates="report",
                                  lazy="dynamic", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Report {self.id} status={self.status} channel={self.channel}>"


# ---------------------------------------------------------------------------
# Contribution  (child of DemandCluster — §5.2.1 / §5.2.2)
# ---------------------------------------------------------------------------

class Contribution(db.Model):
    """
    Links a Report to a DemandCluster.

    All five fields from §5.2.1:
      report_id, citizen_id, demand_cluster_id, type, timestamp

    Created only via DemandCluster methods — never directly — so that
    DemandCluster can maintain its derived counts (totalReports,
    uniqueContributors) without risk of drift.

    type values:
      joined         — citizen chose to join an existing cluster
      confirmed      — citizen confirmed an existing cluster is relevant
      evidence_added — citizen attached new evidence to a cluster
    """
    __tablename__ = "contributions"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)

    # All five §5.2.1 fields
    report_id = db.Column(
        db.String(36), db.ForeignKey("reports.id"), nullable=False, index=True
    )
    citizen_id = db.Column(db.String(36), nullable=False, index=True)
    demand_cluster_id = db.Column(
        db.String(36), db.ForeignKey("demand_clusters.id"), nullable=False, index=True
    )
    type = db.Column(
        db.Enum("joined", "confirmed", "evidence_added", name="contribution_type_enum"),
        nullable=False,
    )
    timestamp = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    report = db.relationship("Report", back_populates="contributions")
    demand_cluster = db.relationship("DemandCluster", back_populates="contributions")

    def __repr__(self):
        return (
            f"<Contribution report={self.report_id} "
            f"cluster={self.demand_cluster_id} type={self.type}>"
        )


# ---------------------------------------------------------------------------
# Verification  (child of DemandCluster — §5.2.1 / §13.2)
# ---------------------------------------------------------------------------

class Verification(db.Model):
    """
    Community pulse vote by a citizen on a DemandCluster.

    Hard invariant (Progress Log §13.2 / Master Prompt §6):
      These votes NEVER write to DemandCluster.active_status.
      They are aggregated into a separate display-only stat
      ("82% still affected") shown alongside the official status.

    state values:
      StillHappening | Improved | Worse | Resolved
    """
    __tablename__ = "verifications"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    citizen_id = db.Column(db.String(36), nullable=False, index=True)
    demand_cluster_id = db.Column(
        db.String(36), db.ForeignKey("demand_clusters.id"), nullable=False, index=True
    )
    state = db.Column(
        db.Enum(
            "StillHappening", "Improved", "Worse", "Resolved",
            name="verification_state_enum",
        ),
        nullable=False,
    )
    timestamp = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationship
    demand_cluster = db.relationship("DemandCluster", back_populates="verifications")

    def __repr__(self):
        return (
            f"<Verification citizen={self.citizen_id} "
            f"cluster={self.demand_cluster_id} state={self.state}>"
        )


# ---------------------------------------------------------------------------
# Evidence  (dual-attach — §5.2.1)
# ---------------------------------------------------------------------------

class Evidence(db.Model):
    """
    Supporting material attached at submission time (to a Report) or later
    (to a DemandCluster, to strengthen it).

    attached_to: 'Report' | 'DemandCluster'
    attached_to_id: the id of the Report or DemandCluster row.

    type values: photo | video | audio | document
    """
    __tablename__ = "evidence"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    type = db.Column(
        db.Enum("photo", "video", "audio", "document", name="evidence_type_enum"),
        nullable=False,
    )
    url = db.Column(db.Text, nullable=False)
    uploaded_by = db.Column(db.String(36), nullable=False)   # citizen_id
    timestamp = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    attached_to = db.Column(
        db.Enum("Report", "DemandCluster", name="evidence_attached_to_enum"),
        nullable=False,
    )
    attached_to_id = db.Column(db.String(36), nullable=False, index=True)

    # Convenience FK for the Report-attachment case (nullable — may be cluster-attached)
    report_id = db.Column(
        db.String(36), db.ForeignKey("reports.id"), nullable=True, index=True
    )

    # Relationships
    report = db.relationship("Report", back_populates="evidence")

    def __repr__(self):
        return f"<Evidence {self.type} attached_to={self.attached_to} id={self.attached_to_id}>"
