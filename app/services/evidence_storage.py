# services/evidence_storage.py
#
# Finding 6 (Citizen Report Flow Audit) — evidence photo storage.
#
# The audit flagged storage provider as an open decision (ImageKit vs. a
# simpler MVP-appropriate approach) and explicitly listed "for the hackathon
# demo specifically, accept only images ... and keep storage minimal" as
# option (c). This module implements that option: photos are saved directly
# to disk under app/static/uploads/evidence/ and served by Flask's normal
# static file handling — zero new third-party credentials required.
#
# Framework-agnostic in spirit (no Flask request/session access), but does
# use Flask's `current_app` to resolve the static folder path and
# `url_for` to build the public URL, since that is the one thing Flask
# already does correctly and re-implementing path resolution would be
# redundant. If a future round moves to ImageKit/S3, only this module
# and its two call sites in citizen/routes.py should need to change.

import os
import uuid
import logging

from flask import current_app, url_for

logger = logging.getLogger(__name__)

_EVIDENCE_SUBDIR = os.path.join("uploads", "evidence")

# Basic content-type allowlist — image-only for MVP per audit recommendation (c).
_ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# 8 MB per photo — generous for a phone camera photo, small enough to keep
# a hackathon demo instance from filling up disk from repeated uploads.
_MAX_BYTES = 8 * 1024 * 1024


class EvidenceUploadError(Exception):
    """
    Raised when a photo cannot be stored (wrong type, too large, disk error).
    Caller must treat this the same as any other evidence-upload failure —
    log it and continue without evidence, never block report submission.
    """


def save_evidence_photo(file_storage) -> str:
    """
    Save an uploaded photo (a werkzeug FileStorage from request.files) to
    app/static/uploads/evidence/ and return its public URL.

    Parameters
    ----------
    file_storage : werkzeug.datastructures.FileStorage

    Returns
    -------
    str — a URL usable directly in an <img src="..."> tag (via url_for('static', ...)).

    Raises
    ------
    EvidenceUploadError on invalid content type, oversized file, or write failure.
    Caller (citizen/routes.py) must catch this and degrade gracefully —
    evidence is optional, it must never block report submission.
    """
    content_type = (file_storage.mimetype or "").lower()
    ext = _ALLOWED_CONTENT_TYPES.get(content_type)
    if not ext:
        raise EvidenceUploadError(
            f"Unsupported evidence file type: {content_type or 'unknown'}. "
            "Only JPEG, PNG, WEBP, and GIF images are accepted."
        )

    data = file_storage.read()
    if not data:
        raise EvidenceUploadError("Uploaded evidence file was empty.")
    if len(data) > _MAX_BYTES:
        raise EvidenceUploadError(
            f"Evidence file too large ({len(data)} bytes) — max {_MAX_BYTES} bytes."
        )

    static_folder = current_app.static_folder
    evidence_dir = os.path.join(static_folder, _EVIDENCE_SUBDIR)
    os.makedirs(evidence_dir, exist_ok=True)

    filename = f"{uuid.uuid4().hex}{ext}"
    dest_path = os.path.join(evidence_dir, filename)

    try:
        with open(dest_path, "wb") as f:
            f.write(data)
    except OSError as e:
        logger.warning("Failed to write evidence photo to disk: %s", e)
        raise EvidenceUploadError("Could not save the evidence photo.") from e

    relative_path = "/".join([_EVIDENCE_SUBDIR.replace(os.sep, "/"), filename])
    return url_for("static", filename=relative_path)
