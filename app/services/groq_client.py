# services/groq_client.py
#
# Thin wrapper around the Groq API for chat completion and structured
# field extraction.
#
# Framework-agnostic: no Flask imports, no SQLAlchemy, no app context.
# Call directly from a Python shell:
#
#   from dotenv import load_dotenv; load_dotenv()
#   from app.services.groq_client import extract_report_fields
#   result = extract_report_fields("There is no water in our area for 3 months")
#   print(result)
#
# Hard invariant (Master Prompt §6 / Progress Log §5.3.1):
#   No function in this module may return a value intended for
#   GovernmentDecision.reason.  AI extraction is for citizen-submitted
#   report fields only.  Government decisions are human-authored exclusively.

import json
import os
import logging
import re

from groq import Groq
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client initialisation
# ---------------------------------------------------------------------------

def _get_client() -> Groq:
    """Return an authenticated Groq client.  Fails fast if key is missing."""
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise EnvironmentError(
            "GROQ_API_KEY is not set. Add it to .env before calling groq_client."
        )
    return Groq(api_key=api_key)


def _model() -> str:
    return os.environ.get("GROQ_MODEL", "qwen/qwen3-27b")


# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM = """You are a structured-data extraction assistant for a citizen development-reporting platform.

Given a citizen's natural-language description of a local development problem, extract the following fields as a JSON object:

{
  "category": one of ["healthcare_access", "water_sanitation", "roads_transport",
                       "electricity_utilities", "education_access", "waste_environment", null],
  "location": a place name, neighbourhood, district, or region string (null if absent),
  "severity": one of ["low", "medium", "high", "critical", null],
  "duration": free-form string describing how long the problem has existed (null if absent),
  "affected_group": who is affected, e.g. "residents of ward 12", "school children" (null if absent),
  "problem_summary": one concise sentence describing the problem in plain language,
  "language_detected": ISO 639-1 code of the input language (e.g. "en", "hi", "pt", "ru")
}

Rules:
- Return ONLY the JSON object — no markdown fences, no explanation.
- If a field cannot be reliably inferred, set it to null.
- Do not invent location names.
- category must be one of the fixed values above, or null.
- severity should reflect the urgency/impact described, not the emotion of the text.
- problem_summary must be in the same language as the input.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_report_fields(raw_text: str) -> dict:
    """
    Extract structured fields from a citizen's raw report text.

    Parameters
    ----------
    raw_text : str
        The citizen's input — plain text, already transcribed if voice.

    Returns
    -------
    dict with keys:
        category, location, severity, duration, affected_group,
        problem_summary, language_detected
        + meta: { "complete": bool, "missing_fields": list[str] }

    "complete" is True when both category and location are non-null
    (the Draft → Report promotion gate, Progress Log §13.1).

    Raises
    ------
    EnvironmentError   if GROQ_API_KEY is missing
    groq.APIError      on API-level failures (caller handles retry/fallback)
    json.JSONDecodeError if the model returns malformed JSON (logged + re-raised)
    """
    client = _get_client()

    response = client.chat.completions.create(
        model=_model(),
        messages=[
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": raw_text},
        ],
        temperature=0.0,   # deterministic extraction
        max_tokens=512,
    )

    raw_response = response.choices[0].message.content or ""

    # The model (qwen3.6-27b) is a reasoning model that may:
    #   1. Wrap output in <think>...</think> before the JSON
    #   2. Wrap the JSON in markdown fences (```json ... ```)
    #   3. Append trailing commentary after the JSON
    # Solution: extract the first complete {...} JSON object using regex,
    # ignoring everything before and after it.
    match = re.search(r'\{.*?\}', raw_response, re.DOTALL)
    if not match:
        logger.error("Groq: no JSON object found in response: %s", raw_response[:400])
        raise json.JSONDecodeError("No JSON object found in Groq response", raw_response, 0)

    raw_json = match.group(0)

    try:
        fields = json.loads(raw_json)
    except json.JSONDecodeError:
        logger.error("Groq extraction returned non-JSON: %s", raw_json[:300])
        raise

    # Compute completeness gate (Progress Log §13.1)
    missing = [f for f in ("category", "location") if not fields.get(f)]
    fields["meta"] = {
        "complete": len(missing) == 0,
        "missing_fields": missing,
    }

    return fields


def ask_clarification(raw_text: str, missing_fields: list[str]) -> str:
    """
    Generate 1–3 targeted clarification questions for a Draft report.

    Called when extract_report_fields returns meta.complete == False.

    Parameters
    ----------
    raw_text       : the citizen's original input
    missing_fields : list of field names that are null (subset of ["category", "location"])

    Returns
    -------
    str — a short, natural-language question to ask the citizen.
          Always in the same language as the original input.
    """
    client = _get_client()

    fields_str = " and ".join(missing_fields)
    system = (
        "You are a helpful assistant for a citizen reporting platform. "
        "A citizen has described a local development problem but their message "
        f"is missing: {fields_str}. "
        "Ask ONE short, friendly follow-up question to obtain the missing information. "
        "Reply in the same language as the citizen's message. "
        "Do not explain why you are asking. Do not use jargon."
    )

    response = client.chat.completions.create(
        model=_model(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": raw_text},
        ],
        temperature=0.3,
        max_tokens=128,
    )

    return response.choices[0].message.content.strip()


def simplify_decision_for_citizen(official_reason: str, language_code: str = "en") -> str:
    """
    Rewrite an official government decision reason in plain citizen-friendly language.

    THIS FUNCTION READS an existing human-authored reason and simplifies it.
    It does NOT originate or invent a decision reason.

    Parameters
    ----------
    official_reason : str — the human-authored GovernmentDecision.reason text
    language_code   : ISO 639-1 code for the output language (default "en")

    Returns
    -------
    str — simplified version, same meaning, plain language.

    Hard invariant: the caller must never pass the return value of this
    function back into GovernmentDecision.reason.  It is for display only.
    """
    client = _get_client()

    system = (
        "You are a plain-language editor for a government transparency platform. "
        "Rewrite the following official government decision reason so that an "
        "ordinary citizen can understand it. "
        f"Output in ISO 639-1 language: {language_code}. "
        "Preserve the meaning exactly — do not soften, spin, or change the decision. "
        "Output only the rewritten text, nothing else."
    )

    response = client.chat.completions.create(
        model=_model(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": official_reason},
        ],
        temperature=0.2,
        max_tokens=256,
    )

    return response.choices[0].message.content.strip()
