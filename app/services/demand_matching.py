# services/demand_matching.py
#
# Confidence-tiered demand matching for incoming Reports.
#
# Implements the three-tier routing from Progress Log §5.2.3:
#
#   High confidence   (cosine similarity >= HIGH_THRESHOLD)
#     → auto_suggest: one best match returned, citizen confirms or declines
#
#   Medium confidence (cosine similarity >= MEDIUM_THRESHOLD)
#     → show_candidates: up to MAX_CANDIDATES matches returned, citizen picks
#
#   No match / low confidence (below MEDIUM_THRESHOLD)
#     → no_match: caller must prompt citizen to "Start a new community issue"
#                 — never silently auto-create a DemandCluster
#
# Framework-agnostic: requires an active SQLAlchemy session to be passed in
# (or called from within a Flask app context where db.session is available).
#
# Thresholds are cosine *similarity* (1 - cosine distance).
# pgvector's <=> operator returns cosine *distance*, so we convert:
#   similarity = 1 - distance

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import text

from app.extensions import db
from app.models.demand_cluster import DemandCluster
from app.services.cohere_client import embed_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds (Progress Log §5.2.3 confidence tiers)
# ---------------------------------------------------------------------------

# Similarity >= HIGH_THRESHOLD  → single auto-suggest (citizen must confirm)
HIGH_THRESHOLD = 0.88

# Similarity >= MEDIUM_THRESHOLD → show up to MAX_CANDIDATES candidates
MEDIUM_THRESHOLD = 0.72

# Maximum number of candidates returned in the medium-confidence tier
MAX_CANDIDATES = 3

# Maximum number of clusters to consider per search (pre-filter by category)
SEARCH_LIMIT = 20


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ClusterMatch:
    """A single DemandCluster match with its similarity score."""
    cluster_id: str
    similarity: float          # 0.0 – 1.0, higher = more similar
    category_id: str
    active_status: str
    affected_localities: list


@dataclass
class MatchResult:
    """
    Output of find_similar_clusters().

    tier:
        "auto_suggest"    — one high-confidence match; surface as
                            "This looks like [X] — join it?"
        "show_candidates" — multiple medium-confidence matches; surface as
                            "3 similar issues found nearby — which matches?"
        "no_match"        — no match found; surface as
                            "Start a new community issue" (never auto-seed)

    matches: list of ClusterMatch, ordered by similarity descending.
             Length 1 for auto_suggest, 1–MAX_CANDIDATES for show_candidates,
             empty list for no_match.
    """
    tier: Literal["auto_suggest", "show_candidates", "no_match"]
    matches: list[ClusterMatch] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_similar_clusters(
    report_text: str,
    category_id: str,
    country_id: str,
    limit: int = SEARCH_LIMIT,
) -> MatchResult:
    """
    Find existing DemandClusters similar to an incoming Report.

    Parameters
    ----------
    report_text : str
        The report's problem_summary (or original_raw_input if summary not yet
        extracted).  This is embedded with input_type="search_query" per the
        asymmetric-search rule in cohere_client.py.

    category_id : str
        The report's resolved category ID.  Clusters are pre-filtered to this
        category before vector comparison to reduce noise.

    country_id : str
        The report's country ID.  Clusters are scoped to the same country.

    limit : int
        Max clusters to scan in the vector search (pre-filter pool size).

    Returns
    -------
    MatchResult with tier and ordered matches.

    Notes
    -----
    Only clusters with active_status == "Active" or "UnderGovernmentReview"
    are considered — Deferred/Deprioritized/Resolved clusters are excluded
    so citizens aren't directed to dead ends.
    """
    # Embed the incoming report as a search query (asymmetric search)
    query_vector = embed_text(report_text, input_type="search_query")

    # pgvector cosine distance query, filtered by category + country + status.
    # <=> is the cosine distance operator; we convert to similarity below.
    # Clusters with null embedding are excluded (can't match without one).
    sql = text("""
        SELECT
            id,
            category_id,
            active_status,
            affected_localities,
            (1 - (embedding <=> CAST(:query_vec AS vector))) AS similarity
        FROM demand_clusters
        WHERE
            category_id   = :category_id
            AND country_id = :country_id
            AND active_status IN ('Active', 'UnderGovernmentReview')
            AND embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:query_vec AS vector)
        LIMIT :limit
    """)

    rows = db.session.execute(sql, {
        "query_vec": _vector_to_pg(query_vector),
        "category_id": category_id,
        "country_id": country_id,
        "limit": limit,
    }).fetchall()

    if not rows:
        return MatchResult(tier="no_match")

    best_similarity = float(rows[0].similarity)

    if best_similarity >= HIGH_THRESHOLD:
        # Single high-confidence match — auto-suggest to citizen
        top = rows[0]
        return MatchResult(
            tier="auto_suggest",
            matches=[_row_to_match(top)],
        )

    # Collect all medium-confidence candidates
    candidates = [
        _row_to_match(r) for r in rows
        if float(r.similarity) >= MEDIUM_THRESHOLD
    ]

    if candidates:
        return MatchResult(
            tier="show_candidates",
            matches=candidates[:MAX_CANDIDATES],
        )

    return MatchResult(tier="no_match")


def store_cluster_embedding(cluster_id: str, summary_text: str) -> None:
    """
    Compute and persist the embedding for a DemandCluster.

    Call this when a new cluster is created, or when its aggregated
    problem summary changes significantly.

    Uses input_type="search_document" — the asymmetric counterpart to
    the "search_query" used when matching incoming reports.

    Parameters
    ----------
    cluster_id   : str — the DemandCluster.id to update
    summary_text : str — the cluster's current aggregated problem description
    """
    from app.services.cohere_client import embed_texts

    vectors = embed_texts([summary_text], input_type="search_document")
    embedding = vectors[0]

    db.session.execute(
        text("UPDATE demand_clusters SET embedding = CAST(:vec AS vector) WHERE id = :id"),
        {"vec": _vector_to_pg(embedding), "id": cluster_id},
    )
    db.session.commit()
    logger.info("Updated embedding for cluster %s", cluster_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vector_to_pg(vector: list[float]) -> str:
    """
    Serialise a Python float list to the pgvector literal format:
    "[0.1, 0.2, ...]"
    """
    return "[" + ",".join(str(v) for v in vector) + "]"


def _row_to_match(row) -> ClusterMatch:
    return ClusterMatch(
        cluster_id=row.id,
        similarity=float(row.similarity),
        category_id=row.category_id,
        active_status=row.active_status,
        affected_localities=row.affected_localities or [],
    )
