from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .queries import DuckDB, q

KNOCKOUT_ARTIFACT = "knockout_artifacts.json"
KNOCKOUT_HISTORY_START = datetime(2026, 6, 28, tzinfo=timezone.utc)
KNOCKOUT_HISTORY_START_EPOCH = int(KNOCKOUT_HISTORY_START.timestamp())

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
    sibling_assets = _sibling_assets(db, [str(row["market_id"]) for row in rows])
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
            "yes_asset_id": row["clob_token_id"],
            "no_asset_id": sibling_assets.get(str(row["market_id"])),
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
        if item["no_asset_id"]:
            asset_ids.append(str(item["no_asset_id"]))

    hourly = _hourly_stage_probabilities(db, team_stage_markets)
    hourly_conditionals = _hourly_conditionals(hourly)

    artifact = {
        "competition": "wc2026",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest": source_manifest,
        "history_start_hour_utc": hourly[0]["hour_utc"] if hourly else None,
        "history_end_hour_utc": hourly[-1]["hour_utc"] if hourly else None,
        "stages": STAGES,
        "teams": sorted(teams.values(), key=lambda item: item["name"]),
        "team_stage_markets": team_stage_markets,
        "conditional_probabilities": _conditionals(price_by_team_stage),
        "team_stage_probabilities_hourly": hourly,
        "conditional_probabilities_hourly": hourly_conditionals,
        "bracket_slots": _bracket_slots(),
        "asset_ids": sorted(set(asset_ids)),
        "result_overrides": {},
    }
    (out_dir / KNOCKOUT_ARTIFACT).write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return artifact


def _sibling_assets(db: DuckDB, market_ids: list[str]) -> dict[str, str]:
    if not market_ids:
        return {}
    ids = ", ".join(f"'{q(market_id)}'" for market_id in sorted(set(market_ids)))
    rows = db.rows(f"""
        SELECT DISTINCT market_id, clob_token_id, lower(outcome_label) AS outcome_label
        FROM input_prices
        WHERE market_id IN ({ids})
    """)
    no_by_market: dict[str, str] = {}
    for row in rows:
        if row["outcome_label"] == "no" and row["clob_token_id"]:
            no_by_market[str(row["market_id"])] = str(row["clob_token_id"])
    return no_by_market


def _hourly_stage_probabilities(
    db: DuckDB,
    markets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not markets:
        return []
    market_ids = ", ".join(
        f"'{q(str(market['market_id']))}'" for market in markets
    )
    rows = db.rows(f"""
        SELECT market_id, clob_token_id, lower(outcome_label) AS outcome_label,
               hour_epoch, price
        FROM (
            SELECT
                market_id,
                clob_token_id,
                outcome_label,
                CAST(floor(odds_timestamp_epoch / 3600) * 3600 AS BIGINT) AS hour_epoch,
                price,
                row_number() OVER (
                    PARTITION BY market_id, clob_token_id, CAST(floor(odds_timestamp_epoch / 3600) * 3600 AS BIGINT)
                    ORDER BY odds_timestamp_epoch DESC
                ) AS rn
            FROM input_prices
            WHERE market_id IN ({market_ids})
        )
        WHERE rn = 1
        ORDER BY hour_epoch, market_id, outcome_label
    """)
    if not rows:
        return []

    min_hour = min(int(row["hour_epoch"]) for row in rows)
    max_hour = max(int(row["hour_epoch"]) for row in rows)
    start_hour = KNOCKOUT_HISTORY_START_EPOCH if max_hour >= KNOCKOUT_HISTORY_START_EPOCH else min_hour

    price_by_market_hour: dict[tuple[str, int], dict[str, float]] = {}
    for row in rows:
        key = (str(row["market_id"]), int(row["hour_epoch"]))
        price_by_market_hour.setdefault(key, {})[str(row["clob_token_id"])] = float(row["price"])

    out: list[dict[str, Any]] = []
    for market in markets:
        last_probability: float | None = None
        last_hour: int | None = None
        yes_asset = str(market["asset_id"])
        no_asset = market.get("no_asset_id")
        for hour in range(start_hour, max_hour + 1, 3600):
            prices = price_by_market_hour.get((str(market["market_id"]), hour), {})
            probability, source = _market_probability(
                prices.get(yes_asset),
                prices.get(str(no_asset)) if no_asset else None,
            )
            stale_age_hours: int | None = 0 if probability is not None else None
            if probability is None and last_probability is not None and last_hour is not None:
                probability = last_probability
                source = "carried_forward"
                stale_age_hours = int((hour - last_hour) / 3600)
            elif probability is not None:
                last_probability = probability
                last_hour = hour
            out.append(
                {
                    "team_id": market["team_id"],
                    "team": market["team"],
                    "stage_key": market["stage_key"],
                    "hour_utc": _hour_utc(hour),
                    "hour_epoch": hour,
                    "probability": probability,
                    "source": source,
                    "stale_age_hours": stale_age_hours,
                }
            )
    return sorted(out, key=lambda row: (row["hour_epoch"], row["stage_key"], row["team"]))


def _market_probability(yes: float | None, no: float | None) -> tuple[float | None, str]:
    if yes is None:
        return None, "missing"
    if no is not None and yes + no > 0:
        return max(0.0, min(1.0, yes / (yes + no))), "hourly_devig_close"
    return max(0.0, min(1.0, yes)), "hourly_close"


def _hourly_conditionals(hourly: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not hourly:
        return []
    by_key = {
        (str(row["team_id"]), str(row["stage_key"]), int(row["hour_epoch"])): row
        for row in hourly
    }
    teams = sorted({(str(row["team_id"]), str(row["team"])) for row in hourly})
    hours = sorted({int(row["hour_epoch"]) for row in hourly})
    stage_keys = [str(stage["key"]) for stage in STAGES]
    out: list[dict[str, Any]] = []
    for team_id, team in teams:
        for hour in hours:
            for i, from_stage in enumerate(stage_keys):
                base = by_key.get((team_id, from_stage, hour))
                for to_stage in stage_keys[i + 1 :]:
                    later = by_key.get((team_id, to_stage, hour))
                    probability = _ratio(
                        _row_probability(later),
                        _row_probability(base),
                    )
                    out.append(
                        {
                            "team_id": team_id,
                            "team": team,
                            "from_stage": from_stage,
                            "to_stage": to_stage,
                            "hour_utc": _hour_utc(hour),
                            "hour_epoch": hour,
                            "probability": probability,
                            "method": "market_ratio",
                            "stale_age_hours": _conditional_stale_age(base, later),
                        }
                    )
    return out


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


def _row_probability(row: dict[str, Any] | None) -> float | None:
    if row is None:
        return None
    return _number(row.get("probability"))


def _conditional_stale_age(
    base: dict[str, Any] | None,
    later: dict[str, Any] | None,
) -> int | None:
    ages = [
        int(row["stale_age_hours"])
        for row in (base, later)
        if row is not None and row.get("stale_age_hours") is not None
    ]
    return max(ages) if ages else None


def _hour_utc(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


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
