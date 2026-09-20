# models/mplads_models.py
#
# MPLADS (Member of Parliament Local Area Development Scheme) reference
# context, surfaced on the MP/Planning Officer dashboards as real,
# sourced, state-level background — NOT something NEVO computes, invents,
# or uses to autonomously allocate the scheme's funds.
#
# Deliberately state-level and read-only reference data, not wired into
# gap_assessment.py / investment_alignment.py's per-cluster category
# matching: MPLADS spending spans every sector at once (roads, water,
# lighting, sanitation...), so forcing one row into one of the app's six
# fixed demand categories (see GovernmentInvestment.category_id, NOT
# NULL) would misrepresent it as sector-specific evidence it isn't. This
# is intentionally a separate, honestly-scoped concept: "here is the real
# public MPLADS funding picture for this state" shown alongside — not
# folded into — the cluster-level investment-alignment computation.
#
# NEVO's own role per this scheme (see MPLADS official rules: an MP
# RECOMMENDS works; the district authority sanctions and implements them)
# is to help an MP identify which citizen-evidenced needs are worth
# recommending — never to claim it allocates or disburses the ₹5 Cr/year
# entitlement itself.

import uuid
from datetime import datetime, timezone

from app.extensions import db


def _uuid():
    return str(uuid.uuid4())


class MpladsStateSummary(db.Model):
    """
    One row per (state, Lok Sabha term) — real, publicly sourced MPLADS
    fund-utilization figures aggregated across every MP in that state.
    Refreshed manually by re-running the seed step with updated figures
    (see seed/seed_data.py::seed_mplads_reference) — this is reference
    data from a public aggregator, not a live-scraped feed, and every row
    carries its own source/source_url/source_last_updated so staleness is
    always visible rather than silently assumed current.
    """
    __tablename__ = "mplads_state_summaries"
    __table_args__ = (
        db.UniqueConstraint("region_id", "lok_sabha_term", name="uq_mplads_region_term"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)

    country_id = db.Column(db.String(36), db.ForeignKey("countries.id"), nullable=False)
    region_id = db.Column(db.String(36), db.ForeignKey("administrative_regions.id"), nullable=False)
    lok_sabha_term = db.Column(db.String(40), nullable=False)   # e.g. "18th Lok Sabha (2024-29)"

    mp_count = db.Column(db.Integer, nullable=True)
    # Rupees (not crore) — Numeric(18,2), same convention/precision as
    # GovernmentInvestment.budget. Format as crore for display only.
    total_allocated = db.Column(db.Numeric(18, 2), nullable=True)
    total_expenditure = db.Column(db.Numeric(18, 2), nullable=True)
    fund_utilization_pct = db.Column(db.Float, nullable=True)
    works_completed = db.Column(db.Integer, nullable=True)
    works_recommended = db.Column(db.Integer, nullable=True)

    # Provenance — same discipline as reference_data.py's evidence tables.
    source = db.Column(db.String(300), nullable=True)
    source_url = db.Column(db.Text, nullable=True)
    source_last_updated = db.Column(db.Date, nullable=True)
    platform_last_synced = db.Column(db.DateTime(timezone=True), nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    country = db.relationship("Country", foreign_keys=[country_id])
    region = db.relationship("AdministrativeRegion", foreign_keys=[region_id])

    def __repr__(self):
        return f"<MpladsStateSummary region={self.region_id} term={self.lok_sabha_term}>"
