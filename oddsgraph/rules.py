from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .queries import q

_DEFAULT_TAXONOMY = Path(__file__).resolve().parent / "taxonomies" / "wc2026.json"


@dataclass(frozen=True)
class Taxonomy:
    name: str
    stage_rules: tuple[tuple[str, int], ...]
    stage_subject_aliases: tuple[tuple[str, str], ...]
    single_winner_slugs: tuple[str, ...]
    single_winner_slug_patterns: tuple[str, ...]
    source_path: Path
    content_hash: str


def default_taxonomy_path() -> Path:
    return _DEFAULT_TAXONOMY


def load_taxonomy(path: Path | None = None) -> Taxonomy:
    source = path or default_taxonomy_path()
    raw = json.loads(source.read_text(encoding="utf-8"))
    stage_rules = tuple(
        (item["pattern"], int(item["rank"]))
        for item in raw.get("stage_rules", [])
    )
    stage_subject_aliases = tuple(sorted(
        (str(alias), str(canonical))
        for alias, canonical in raw.get("stage_subject_aliases", {}).items()
    ))
    single_winner_slugs = tuple(raw.get("single_winner_slugs", []))
    single_winner_slug_patterns = tuple(raw.get("single_winner_slug_patterns", []))
    content_hash = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    return Taxonomy(
        name=str(raw.get("name", source.stem)),
        stage_rules=stage_rules,
        stage_subject_aliases=stage_subject_aliases,
        single_winner_slugs=single_winner_slugs,
        single_winner_slug_patterns=single_winner_slug_patterns,
        source_path=source,
        content_hash=content_hash,
    )


def stage_rules_values_sql(taxonomy: Taxonomy) -> str:
    if not taxonomy.stage_rules:
        return "('^$', 0)"
    return ",\n".join(f"('{q(pattern)}', {rank})" for pattern, rank in taxonomy.stage_rules)


def stage_subject_alias_values_sql(taxonomy: Taxonomy) -> str:
    if not taxonomy.stage_subject_aliases:
        return "('__no_stage_subject_alias__', '__no_stage_subject_alias__')"
    return ",\n".join(
        f"('{q(alias)}', '{q(canonical)}')"
        for alias, canonical in taxonomy.stage_subject_aliases
    )


def single_winner_values_sql(taxonomy: Taxonomy) -> str:
    if not taxonomy.single_winner_slugs:
        return "('__no_single_winner_slug__')"
    return ",\n".join(f"('{q(slug)}')" for slug in taxonomy.single_winner_slugs)


def single_winner_pattern_sql(taxonomy: Taxonomy, column: str) -> str:
    if not taxonomy.single_winner_slug_patterns:
        return "false"
    return " OR ".join(f"{column} LIKE '{q(pattern)}'" for pattern in taxonomy.single_winner_slug_patterns)
