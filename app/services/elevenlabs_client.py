# services/elevenlabs_client.py
#
# Thin wrapper around the ElevenLabs API for speech-to-text transcription
# using Scribe v2.
#
# Framework-agnostic: no Flask imports, no SQLAlchemy, no app context.
# Call directly from a Python shell:
#
#   from dotenv import load_dotenv; load_dotenv()
#   from app.services.elevenlabs_client import transcribe_audio
#   with open("recording.webm", "rb") as f:
#       result = transcribe_audio(f.read(), mime_type="audio/webm")
#   print(result["text"])
#
# Pipeline position (Progress Log §13.1 / Citizen §17.3):
#   Voice input → transcribe_audio() → raw_text
#   → groq_client.extract_report_fields(raw_text)
#
# Transcription failure is a distinct failure mode from extraction
# incompleteness (Progress Log §13.1):
#   - A garbled/silent recording must surface as a channel-level retry
#     BEFORE a Report row is created — never produce an empty Report.
#   - TranscriptionError signals this to the caller (citizen/routes.py).

import io
import os
import logging

from elevenlabs import ElevenLabs
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TranscriptionError(Exception):
    """
    Raised when audio cannot be transcribed to usable text.

    The caller (citizen/routes.py report_flow view) must treat this as a
    channel-level failure and prompt the citizen to re-record — it must NOT
    create a Report row with empty or near-empty original_raw_input.
    """


# ---------------------------------------------------------------------------
# Client initialisation
# ---------------------------------------------------------------------------

def _get_client() -> ElevenLabs:
    """Return an authenticated ElevenLabs client. Fails fast if key is missing."""
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise EnvironmentError(
            "ELEVENLABS_API_KEY is not set. Add it to .env before calling elevenlabs_client."
        )
    return ElevenLabs(api_key=api_key)


# Minimum character threshold — transcriptions shorter than this are likely
# silence, noise, or a recording failure.  Adjust if needed.
# NOTE: This is a rough length floor for MVP, not a real audio-quality check.
# It catches obviously empty/broken transcriptions but will not detect
# near-gibberish audio.  A production build would add confidence-score
# gating from the Scribe response instead.
_MIN_TRANSCRIPT_LENGTH = 10


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def transcribe_audio(
    audio_bytes: bytes,
    mime_type: str = "audio/webm",
    language_code: str | None = None,
) -> dict:
    """
    Transcribe audio bytes to text using ElevenLabs Scribe v2.

    Parameters
    ----------
    audio_bytes   : bytes — raw audio content (webm, mp4, wav, etc.)
    mime_type     : str   — MIME type of the audio, e.g. "audio/webm",
                            "audio/wav", "audio/mp4"
    language_code : str | None — optional ISO 639-1 hint (e.g. "hi", "pt").
                                 Pass None to let Scribe auto-detect.

    Returns
    -------
    dict with keys:
        "text"              : str  — full transcript
        "language_detected" : str  — ISO 639-1 code detected by Scribe
        "duration_seconds"  : float | None

    Raises
    ------
    EnvironmentError    if ELEVENLABS_API_KEY is missing
    TranscriptionError  if audio is silent, too short, or unintelligible —
                        caller must NOT create a Report in this case
    elevenlabs.APIError on API-level failures
    """
    client = _get_client()

    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = _mime_to_filename(mime_type)

    kwargs = {
        "file": audio_file,
        "model_id": "scribe_v2",
    }
    if language_code:
        kwargs["language_code"] = language_code

    response = client.speech_to_text.convert(**kwargs)

    transcript = (response.text or "").strip()

    if len(transcript) < _MIN_TRANSCRIPT_LENGTH:
        logger.warning(
            "Transcription too short (%d chars) — treating as recording failure",
            len(transcript),
        )
        raise TranscriptionError(
            "Audio could not be transcribed to usable text. "
            "The recording may be silent, too short, or corrupted."
        )

    detected_language = getattr(response, "language_code", None) or "unknown"
    duration = getattr(response, "duration_seconds", None)

    return {
        "text": transcript,
        "language_detected": detected_language,
        "duration_seconds": duration,
    }


def _mime_to_filename(mime_type: str) -> str:
    """
    Map a MIME type to a filename with the right extension so ElevenLabs
    can parse the audio format correctly.
    """
    mapping = {
        "audio/webm":  "audio.webm",
        "audio/wav":   "audio.wav",
        "audio/wave":  "audio.wav",
        "audio/mp4":   "audio.mp4",
        "audio/mpeg":  "audio.mp3",
        "audio/mp3":   "audio.mp3",
        "audio/ogg":   "audio.ogg",
        "audio/flac":  "audio.flac",
        "audio/m4a":   "audio.m4a",
    }
    return mapping.get(mime_type.lower(), "audio.webm")
