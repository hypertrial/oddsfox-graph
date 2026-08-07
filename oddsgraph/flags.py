"""Team flag ISO mapping and asset URL helpers for the explorer."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent / "data"
_ISO_PATH = _DATA_DIR / "team_flag_iso.json"
_FLAGS_DIR = Path(__file__).resolve().parent / "explorer" / "assets" / "flags"


BLANK_FLAG_URL = "/assets/flags/_blank.svg"


@lru_cache(maxsize=1)
def load_team_flag_iso() -> dict[str, str]:
    """Return canonical team display name -> ISO / regional flag code."""
    return json.loads(_ISO_PATH.read_text(encoding="utf-8"))


def flag_code_for_team(team: str) -> str | None:
    mapping = load_team_flag_iso()
    return mapping.get(team)


def flag_asset_path(team: str) -> Path | None:
    code = flag_code_for_team(team)
    if not code:
        return None
    path = _FLAGS_DIR / f"{code}.svg"
    return path if path.exists() else None


def flag_url_for_team(team: str) -> str | None:
    """Dash-served asset URL for a team's local SVG flag."""
    code = flag_code_for_team(team)
    if not code:
        return None
    path = _FLAGS_DIR / f"{code}.svg"
    if not path.exists():
        return None
    return f"/assets/flags/{code}.svg"


def flag_url_or_blank(team: str | None) -> str:
    """Return a team flag URL, or the transparent blank placeholder."""
    if not team:
        return BLANK_FLAG_URL
    return flag_url_for_team(str(team)) or BLANK_FLAG_URL


def missing_flag_teams(teams: list[str] | set[str]) -> list[str]:
    """Return teams lacking an ISO mapping or local SVG asset."""
    missing: list[str] = []
    for team in sorted(teams):
        if flag_asset_path(team) is None:
            missing.append(team)
    return missing
