"""Canonical ID builders and slug normalization."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent / "data"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def normalize_label(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    text = _SLUG_RE.sub("-", text)
    return text.strip("-")


def slugify(text: str) -> str:
    return normalize_label(text)


@lru_cache(maxsize=1)
def load_team_codes() -> dict[str, str]:
    path = _DATA_DIR / "team_codes.json"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return {k.lower(): v for k, v in data.items()}


def team_aliases_from_code(code: str) -> list[str]:
    codes = load_team_codes()
    name = codes.get(code.lower())
    if not name:
        return []
    return [name, normalize_label(name)]


def event_id(event_id: str) -> str:
    return f"event:{event_id}"


def market_id(market_id: str) -> str:
    return f"market:{market_id}"


def outcome_id(market_id: str, label: str) -> str:
    return f"outcome:{market_id}:{slugify(label)}"


def team_id(label: str) -> str:
    return f"team:{slugify(label)}"


def competition_id(label: str) -> str:
    return f"competition:{slugify(label)}"


def stage_id(competition_label: str, stage_label: str) -> str:
    return f"stage:{slugify(competition_label)}:{slugify(stage_label)}"


def group_id(competition_label: str, group_label: str) -> str:
    return f"group:{slugify(competition_label)}:{slugify(group_label)}"


def round_id(competition_label: str, round_label: str) -> str:
    return f"round:{slugify(competition_label)}:{slugify(round_label)}"


def match_id(*parts: str) -> str:
    slug_parts = [slugify(p) for p in parts if p]
    return "match:" + ":".join(slug_parts)
