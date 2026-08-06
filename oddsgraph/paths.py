"""Filesystem path helpers for build artifacts."""

from __future__ import annotations

import re
from pathlib import Path

# Single path segment only: no separators, no ``..``, no empty names.
_SAFE_EVENT_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def sanitize_event_id_for_path(event_id: str) -> str:
    """Return ``event_id`` if it is safe as a single filesystem path segment.

    Rejects empty values, path separators, and ``..`` so fragment writes cannot
    escape ``build/fragments`` (or nest unexpectedly under it).
    """
    cleaned = str(event_id).strip()
    if cleaned in {".", ".."} or not _SAFE_EVENT_ID.fullmatch(cleaned):
        raise ValueError(f"Unsafe event_id for filesystem path: {event_id!r}")
    return cleaned


def event_artifact_path(directory: Path, event_id: str, suffix: str) -> Path:
    """Return ``directory / f"{safe_id}{suffix}"``, staying under ``directory``.

    ``suffix`` must be a single path-segment suffix such as ``.json`` or
    ``__part0.json`` (no directory separators).
    """
    safe_id = sanitize_event_id_for_path(event_id)
    if not suffix or "/" in suffix or "\\" in suffix:
        raise ValueError(f"Artifact suffix must be a single path segment: {suffix!r}")
    path = directory / f"{safe_id}{suffix}"
    base = directory.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(base):
        raise ValueError(
            f"Resolved artifact path escapes directory {directory}: {resolved}"
        )
    return path
