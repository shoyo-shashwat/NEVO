# services/cohere_client.py
#
# Thin wrapper around the Cohere API for text embeddings.
#
# Framework-agnostic: no Flask imports, no SQLAlchemy, no app context.
# Call directly from a Python shell:
#
#   from dotenv import load_dotenv; load_dotenv()
#   from app.services.cohere_client import embed_text
#   vector = embed_text("There is no water in our area")
#   print(len(vector))   # should be 1024 for embed-v4
#
# These embeddings are used by demand_matching.py for pgvector similarity
# search — the embedding is stored on DemandCluster and compared against
# incoming Report embeddings to find similar community issues.
# (Migration "schema v2 — geospatial" in Step 4 adds the VECTOR column.)
#
# Asymmetric search direction (do NOT flip these):
#   Incoming Report being matched  → embed_text(text, input_type="search_query")
#   DemandCluster being stored     → embed_texts([text], input_type="search_document")
# Cohere Embed v4 uses asymmetric search: query and document embeddings live
# in different subspaces.  Using "search_document" for both sides will produce
# meaningless similarity scores.  demand_matching.py must follow this pattern.

import os
import logging

import cohere
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Cohere embed-v4.0 output dimension.
# Verified empirically: embed-v4.0 returns 1536-dimensional vectors.
# Note: cohere==5.15.0 does not expose an output_dimension parameter on embed();
# the model always returns 1536 and this constant must match that exactly.
# If a mismatch is detected at runtime, it is a hard failure — a silently wrong
# dimension reaching pgvector would corrupt similarity search results.
EMBED_DIMENSION = 1536

# Input type for search/matching — "search_document" for stored cluster
# embeddings, "search_query" for incoming report embeddings being compared.
_INPUT_TYPE_DOCUMENT = "search_document"
_INPUT_TYPE_QUERY    = "search_query"


# ---------------------------------------------------------------------------
# Client initialisation
# ---------------------------------------------------------------------------

def _get_client() -> cohere.Client:
    """Return an authenticated Cohere client. Fails fast if key is missing."""
    api_key = os.environ.get("COHERE_API_KEY", "")
    if not api_key:
        raise EnvironmentError(
            "COHERE_API_KEY is not set. Add it to .env before calling cohere_client."
        )
    return cohere.Client(api_key=api_key)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def embed_text(text: str, input_type: str = _INPUT_TYPE_QUERY) -> list[float]:
    """
    Embed a single text string using Cohere Embed v4.

    Parameters
    ----------
    text       : str — the text to embed (Report problem_summary or cluster summary)
    input_type : str — "search_query"    for a new Report being matched
                       "search_document" for a DemandCluster being stored

    Returns
    -------
    list[float] — embedding vector of length EMBED_DIMENSION (1024)

    Raises
    ------
    EnvironmentError        if COHERE_API_KEY is missing
    cohere.CohereAPIError   on API-level failures (caller handles retry/fallback)
    ValueError              if the API returns an unexpected embedding shape
    """
    client = _get_client()

    response = client.embed(
        texts=[text],
        model="embed-v4.0",
        input_type=input_type,
        embedding_types=["float"],
    )

    embeddings = response.embeddings.float
    if not embeddings or len(embeddings) == 0:
        raise ValueError("Cohere returned empty embeddings list")

    vector = embeddings[0]

    if len(vector) != EMBED_DIMENSION:
        # Hard fail — a silent mismatch must never reach pgvector.
        raise ValueError(
            f"Cohere embedding dimension mismatch: expected {EMBED_DIMENSION}, "
            f"got {len(vector)}. The EMBED_DIMENSION constant must match "
            f"what embed-v4.0 actually returns."
        )

    return vector


def embed_texts(texts: list[str], input_type: str = _INPUT_TYPE_DOCUMENT) -> list[list[float]]:
    """
    Embed a batch of text strings in a single API call.

    Use this when seeding or re-indexing multiple DemandCluster embeddings
    at once — more efficient than calling embed_text() in a loop.

    Parameters
    ----------
    texts      : list[str] — texts to embed (max 96 per Cohere batch limit)
    input_type : str — same semantics as embed_text()

    Returns
    -------
    list[list[float]] — one vector per input text, in the same order

    Raises
    ------
    ValueError if texts is empty
    """
    if not texts:
        raise ValueError("embed_texts requires at least one text")

    client = _get_client()

    response = client.embed(
        texts=texts,
        model="embed-v4.0",
        input_type=input_type,
        embedding_types=["float"],
    )

    vectors = response.embeddings.float

    # Hard-fail on dimension mismatch — check first vector as representative.
    if vectors and len(vectors[0]) != EMBED_DIMENSION:
        raise ValueError(
            f"Cohere batch embedding dimension mismatch: expected {EMBED_DIMENSION}, "
            f"got {len(vectors[0])}."
        )

    return vectors
