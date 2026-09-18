# services/region_matching.py
#
# Shared region-aware evidence matching for gap_assessment.py and
# investment_alignment.py.
#
# Both InfrastructureDataPoint/DemographicDataPoint/GovernmentInvestment are
# scoped to a single, optional region_id, while DemandCluster.region_ids is a
# JSON list (a cluster can span multiple administrative regions). Filtering
# reference data by category_id + country_id alone -- the previous behavior
# in gap_assessment.py and investment_alignment.py -- ignores geography
# entirely: two clusters on opposite sides of the country would receive
# identical evidence. This module resolves, for each of a cluster's regions,
# the most geographically specific matching reference row available, walking
# up the administrative hierarchy (constituency -> district -> state ->
# national -> country-wide) when no row exists at a region's own level.
#
# Matching happens per region first, aggregation happens second (callers in
# gap_assessment.py / investment_alignment.py own the field-specific
# aggregation -- e.g. sum populations, take worst-case coverage -- since that
# logic is specific to each reference-data shape).
#
# Everything here is a pure function -- no Flask, no DB session -- so it's
# unit-testable without a live Postgres/PostGIS database. The DB-facing
# queries that build `candidates` and `ancestor_chains` live in the callers.

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RegionMatch:
    """One cluster region resolved against the best available reference row."""
    region_id: str
    matched_row: object | None        # the reference row, or None if nothing matched at any level
    matched_region_id: str | None     # region_id the matched row actually belongs to (None = country-wide)
    is_fallback: bool                 # True if matched_region_id != region_id (had to broaden scope)


@dataclass
class ResolvedEvidence:
    """Result of matching every region in a cluster against candidate reference rows."""
    matches: list[RegionMatch] = field(default_factory=list)

    @property
    def matched_rows(self) -> list:
        """Rows actually found, one per region that resolved to something (deduplicated by identity)."""
        seen_ids = set()
        rows = []
        for m in self.matches:
            if m.matched_row is not None and id(m.matched_row) not in seen_ids:
                seen_ids.add(id(m.matched_row))
                rows.append(m.matched_row)
        return rows

    @property
    def fallback_used(self) -> bool:
        return any(m.is_fallback for m in self.matches if m.matched_row is not None)

    @property
    def unmatched_region_ids(self) -> list[str]:
        return [m.region_id for m in self.matches if m.matched_row is None]

    @property
    def native_match_count(self) -> int:
        return sum(1 for m in self.matches if m.matched_row is not None and not m.is_fallback)

    @property
    def matched_count(self) -> int:
        return sum(1 for m in self.matches if m.matched_row is not None)

    @property
    def scope_label(self) -> str:
        """
        Human-readable evidence scope for display / explainability.

        "no_data"        — nothing matched at any level for any region
        "region_specific" — every region matched at its own level (no fallback)
        "mixed"           — some regions matched natively, others via fallback
        "broader_region"  — every matched region required a fallback (state/country)
        """
        if self.matched_count == 0:
            return "no_data"
        if not self.fallback_used:
            return "region_specific"
        if self.native_match_count > 0:
            return "mixed"
        return "broader_region"

    @property
    def coverage_fraction(self) -> float:
        """Fraction of the cluster's regions for which ANY evidence (native or fallback) was found."""
        if not self.matches:
            return 0.0
        return self.matched_count / len(self.matches)


def resolve_region_matches(
    region_ids: list[str],
    candidates: list,
    ancestor_chains: dict[str, list],
) -> ResolvedEvidence:
    """
    Pure matching logic -- no DB access.

    region_ids: the DemandCluster's region_ids (list of AdministrativeRegion ids).
                Never empty in practice, but an empty list resolves to no matches.
    candidates: reference rows (InfrastructureDataPoint / DemographicDataPoint /
                GovernmentInvestment) already filtered by the caller to the right
                category_id + country_id, each with a `.region_id` attribute
                (None on a row means it's a country-wide fallback row).
    ancestor_chains: {region_id: [region_id, parent_id, grandparent_id, ..., None]}
                      for every id in region_ids -- most specific first, country-wide
                      (None) always last. Build with build_ancestor_chain().

    For each region_id, walks its ancestor chain and returns the first candidate
    row found at that level, or None if no candidate matches any level in the chain.
    """
    by_region: dict = {}
    for row in candidates:
        by_region.setdefault(row.region_id, []).append(row)

    matches = []
    for region_id in region_ids:
        chain = ancestor_chains.get(region_id, [region_id, None])
        matched_row = None
        matched_region_id = None
        for level in chain:
            rows_at_level = by_region.get(level)
            if rows_at_level:
                matched_row = rows_at_level[0]
                matched_region_id = level
                break
        matches.append(RegionMatch(
            region_id=region_id,
            matched_row=matched_row,
            matched_region_id=matched_region_id,
            is_fallback=(matched_row is not None and matched_region_id != region_id),
        ))
    return ResolvedEvidence(matches=matches)


def build_ancestor_chain(region_id: str, parent_lookup: dict) -> list:
    """
    Pure hierarchy walk -- no DB access.

    parent_lookup: {region_id: parent_region_id_or_None} covering at least every
                    ancestor of region_id (build from AdministrativeRegion rows).
    Returns [region_id, parent, grandparent, ..., None] -- None (country-wide
    fallback) is always appended last, even if region_id has no parents.

    Guards against a malformed/cyclic parent chain (never trust external data to
    be well-formed) by stopping if a region reappears.
    """
    chain = [region_id]
    seen = {region_id}
    current = region_id
    while True:
        parent = parent_lookup.get(current)
        if parent is None or parent in seen:
            break
        chain.append(parent)
        seen.add(parent)
        current = parent
    chain.append(None)
    return chain


def build_ancestor_chains(region_ids: list[str], parent_lookup: dict) -> dict:
    """Convenience wrapper: build_ancestor_chain() for every id in region_ids."""
    return {rid: build_ancestor_chain(rid, parent_lookup) for rid in region_ids}


# ---------------------------------------------------------------------------
# The one DB-facing convenience function in this module.
#
# Everything above is pure and unit-tested without a database (see
# tests/test_region_matching.py). Both gap_assessment.py and
# investment_alignment.py need the same "walk AdministrativeRegion parents
# for a set of region ids" query, so it lives here once rather than being
# duplicated in each caller. Local import of db/AdministrativeRegion keeps
# this module importable without a Flask app context unless this specific
# function is actually called.
# ---------------------------------------------------------------------------

def fetch_ancestor_chains(region_ids: list[str], extra_region_ids: list[str] = ()) -> dict:
    """
    Build ancestor chains for `region_ids`, covering the parent tree of both
    `region_ids` and `extra_region_ids` (typically the region_ids seen on
    candidate reference rows, so a candidate scoped to a cluster region's
    ancestor is resolvable). Requires an active SQLAlchemy session.
    """
    from app.extensions import db
    from app.models.shared import AdministrativeRegion

    parent_lookup: dict = {}
    to_resolve = set(region_ids) | set(extra_region_ids)
    to_resolve.discard(None)
    resolved = set()
    while to_resolve:
        rid = to_resolve.pop()
        if rid in resolved:
            continue
        resolved.add(rid)
        region = db.session.get(AdministrativeRegion, rid)
        if region is None:
            parent_lookup[rid] = None
            continue
        parent_lookup[rid] = region.parent_region_id
        if region.parent_region_id and region.parent_region_id not in resolved:
            to_resolve.add(region.parent_region_id)

    return build_ancestor_chains(list(region_ids), parent_lookup)
