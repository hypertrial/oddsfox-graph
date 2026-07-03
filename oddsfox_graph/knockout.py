from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .queries import DuckDB

KNOCKOUT_ARTIFACT = "knockout_artifacts.json"

STAGES = [
    {"key": "round_of_32", "rank": 0, "label": "Round of 32", "slot_count": 16},
    {"key": "round_of_16", "rank": 1, "label": "Round of 16", "slot_count": 8},
    {"key": "quarterfinal", "rank": 2, "label": "Quarterfinals", "slot_count": 4},
    {"key": "semifinal", "rank": 3, "label": "Semifinals", "slot_count": 2},
    {"key": "final", "rank": 4, "label": "Final", "slot_count": 1},
    {"key": "winner", "rank": 5, "label": "Winner", "slot_count": 1},
]

STAGE_BY_RANK = {stage["rank"]: stage for stage in STAGES}


def write_knockout_artifacts(
    db: DuckDB,
    out_dir: Path,
    source_manifest: str = "build_manifest.json",
) -> dict[str, Any]:
    rows = db.rows(
        """
        SELECT
            node_id,
            clob_token_id,
            market_id,
            question,
            outcome_label,
            event_slug,
            stage_subject,
            stage_rank,
            current_price,
            current_price_devig,
            is_active,
            is_closed
        FROM nodes_v
        WHERE stage_rank IS NOT NULL
            AND outcome_label = 'Yes'
        ORDER BY stage_subject, stage_rank
        """
    )
    team_stage_markets = []
    teams: dict[str, dict[str, str]] = {}
    price_by_team_stage: dict[tuple[str, str], float | None] = {}
    asset_ids: list[str] = []

    for row in rows:
        stage = STAGE_BY_RANK.get(int(row["stage_rank"]))
        if stage is None:
            continue
        team = str(row["stage_subject"] or "").strip()
        if not team:
            continue
        team_id = _slugify(team)
        teams.setdefault(team_id, {"team_id": team_id, "name": team})
        probability = _number(row["current_price_devig"])
        source = "current_price_devig"
        if probability is None:
            probability = _number(row["current_price"])
            source = "current_price" if probability is not None else "missing"
        item = {
            "team_id": team_id,
            "team": team,
            "stage_key": stage["key"],
            "stage_rank": stage["rank"],
            "node_id": row["node_id"],
            "asset_id": row["clob_token_id"],
            "market_id": row["market_id"],
            "question": row["question"],
            "event_slug": row["event_slug"],
            "baseline_probability": probability,
            "probability_source": source,
            "is_active": bool(row["is_active"]),
            "is_closed": bool(row["is_closed"]),
        }
        team_stage_markets.append(item)
        price_by_team_stage[(team_id, str(stage["key"]))] = probability
        if row["clob_token_id"]:
            asset_ids.append(str(row["clob_token_id"]))

    artifact = {
        "competition": "wc2026",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest": source_manifest,
        "stages": STAGES,
        "teams": sorted(teams.values(), key=lambda item: item["name"]),
        "team_stage_markets": team_stage_markets,
        "conditional_probabilities": _conditionals(price_by_team_stage),
        "bracket_slots": _bracket_slots(),
        "asset_ids": sorted(set(asset_ids)),
        "result_overrides": {},
    }
    (out_dir / KNOCKOUT_ARTIFACT).write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return artifact


def _conditionals(prices: dict[tuple[str, str], float | None]) -> list[dict[str, Any]]:
    out = []
    stage_keys = [str(stage["key"]) for stage in STAGES]
    teams = sorted({team_id for team_id, _stage in prices})
    for team_id in teams:
        for i, from_stage in enumerate(stage_keys):
            base = prices.get((team_id, from_stage))
            for to_stage in stage_keys[i + 1 :]:
                later = prices.get((team_id, to_stage))
                out.append(
                    {
                        "team_id": team_id,
                        "from_stage": from_stage,
                        "to_stage": to_stage,
                        "probability": _ratio(later, base),
                        "method": "market_ratio",
                    }
                )
    return out


def _ratio(later: float | None, base: float | None) -> float | None:
    if later is None or base is None or base <= 0:
        return None
    return max(0.0, min(1.0, later / base))


def _bracket_slots() -> list[dict[str, Any]]:
    slots = []
    for stage in STAGES:
        for index in range(1, int(stage["slot_count"]) + 1):
            slots.append(
                {
                    "slot_id": f"{stage['key']}-{index}",
                    "stage_key": stage["key"],
                    "slot_index": index,
                    "label": f"{stage['label']} {index}",
                    "sports_slug": None,
                    "team_ids": [],
                }
            )
    return slots


def _number(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "unknown"
