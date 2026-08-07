#!/usr/bin/env python3
"""Export FIFA-reviewed WC2026 fixtures from oddsfox-pipeline DuckDB.

Writes ``oddsgraph/data/wc2026_schedule.json`` for ``oddsgraph.bracket``.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

# Align staging spellings with Polymarket Group-Winner / match vocabulary.
_TEAM_REMAP = {
    "Czech Republic": "Czechia",
    "Turkey": "Türkiye",
    "Congo DR": "DR Congo",
}

_DEFAULT_DB = Path(
    "/Volumes/Mac SSD/hypertrial/active/OddsFox/oddsfox-pipeline/oddsfox.duckdb"
)
_REPO_ROOT = Path(__file__).resolve().parent.parent
_OUT = _REPO_ROOT / "oddsgraph" / "data" / "wc2026_schedule.json"

# Provenance constants mirrored from oddsfox-pipeline openfootball ingestion.
_OPENFOOTBALL_REVISION = "bd46a148289f9930da66c140d4d7d2325e95d387"
_OPENFOOTBALL_BASE = (
    f"https://raw.githubusercontent.com/openfootball/worldcup/{_OPENFOOTBALL_REVISION}/"
)
_FIFA_SCHEDULE_SHA256 = (
    "165fb909253b746e6173a4443bdc3e5d786530f0684af6e85c1fd21fff252811"
)


def _remap(team: str) -> str:
    return _TEAM_REMAP.get(team, team)


def export(db_path: Path, out_path: Path) -> dict:
    con = duckdb.connect(str(db_path), read_only=True)
    rows = con.execute(
        """
        SELECT
          fifa_match_id,
          stage_key,
          group_label,
          home_team,
          away_team,
          kickoff_at_utc,
          venue,
          match_status
        FROM openfootball_wc2026_staging.stg_openfootball_wc2026_schedule_fixtures
        ORDER BY fifa_match_id
        """
    ).fetchall()
    con.close()

    if len(rows) != 104:
        raise SystemExit(f"Expected 104 fixtures, got {len(rows)}")

    # Preserve curated local overlays (e.g. Final / Third Place winners) when
    # re-exporting from the pipeline DuckDB, which does not store winners.
    prior_winners: dict[int, str] = {}
    if out_path.exists():
        try:
            prior = json.loads(out_path.read_text(encoding="utf-8"))
            for raw in prior.get("fixtures") or []:
                if not isinstance(raw, dict):
                    continue
                winner = raw.get("winner_team")
                fifa_id = raw.get("fifa_match_id")
                if winner and fifa_id is not None:
                    prior_winners[int(fifa_id)] = _remap(str(winner))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            prior_winners = {}

    fixtures = []
    for (
        fifa_match_id,
        stage_key,
        group_label,
        home_team,
        away_team,
        kickoff_at_utc,
        venue,
        match_status,
    ) in rows:
        fixture = {
            "fifa_match_id": int(fifa_match_id),
            "stage_key": stage_key,
            "group_label": group_label,
            "home_team": _remap(home_team),
            "away_team": _remap(away_team),
            "kickoff_at_utc": kickoff_at_utc.isoformat(sep="T")
            if kickoff_at_utc is not None
            else None,
            "venue": venue,
            "match_status": match_status,
        }
        winner = prior_winners.get(int(fifa_match_id))
        if winner:
            fixture["winner_team"] = _remap(winner)
        fixtures.append(fixture)

    payload = {
        "_provenance": {
            "source": "oddsfox-pipeline openfootball_wc2026_staging.stg_openfootball_wc2026_schedule_fixtures",
            "source_url": _OPENFOOTBALL_BASE,
            "openfootball_revision": _OPENFOOTBALL_REVISION,
            "fifa_schedule_sha256": _FIFA_SCHEDULE_SHA256,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "row_count": len(fixtures),
            "team_remaps": _TEAM_REMAP,
        },
        "fixtures": fixtures,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=_DEFAULT_DB,
        help="Path to oddsfox-pipeline oddsfox.duckdb",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_OUT,
        help="Output JSON path",
    )
    args = parser.parse_args()
    if not args.db.exists():
        raise SystemExit(f"DuckDB not found: {args.db}")
    payload = export(args.db, args.out)
    print(f"Wrote {payload['_provenance']['row_count']} fixtures to {args.out}")


if __name__ == "__main__":
    main()
