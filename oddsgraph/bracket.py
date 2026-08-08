"""Official WC2026 bracket topology from curated FIFA schedule data."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from oddsgraph import ids
from oddsgraph.fragments import make_edge, make_node, match_local_id
from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.schema import Edge, GraphFragment, Node
from oddsgraph.topology import DEFAULT_COMPETITION_LABEL

_DATA_DIR = Path(__file__).resolve().parent / "data"
_SCHEDULE_PATH = _DATA_DIR / "wc2026_schedule.json"

STAGE_KEY_TO_LABEL: dict[str, str] = {
    "group_stage": "Group Stage",
    "round_of_32": "Round of 32",
    "round_of_16": "Round of 16",
    "quarterfinal": "Quarterfinals",
    "semifinal": "Semifinals",
    "third_place": "Third Place",
    "final": "Final",
}

# Knockout stage ranks used to wire MATCH ADVANCES_TO MATCH by team continuity.
KNOCKOUT_STAGE_RANK: dict[str, int] = {
    "round_of_32": 1,
    "round_of_16": 2,
    "quarterfinal": 3,
    "semifinal": 4,
    "final": 5,
    "third_place": 5,
}

STAGE_LADDER: list[tuple[str, str]] = [
    ("Group Stage", "Round of 32"),
    ("Round of 32", "Round of 16"),
    ("Round of 16", "Quarterfinals"),
    ("Quarterfinals", "Semifinals"),
    ("Semifinals", "Final"),
    ("Final", "Champion"),
    ("Semifinals", "Third Place"),
]

ALL_STAGE_LABELS: list[str] = [
    "Group Stage",
    "Round of 32",
    "Round of 16",
    "Quarterfinals",
    "Semifinals",
    "Third Place",
    "Final",
    "Champion",
]

_SCHEDULE_EVIDENCE = ["official:wc2026-schedule"]


@lru_cache(maxsize=1)
def load_wc2026_schedule() -> dict:
    with _SCHEDULE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _kickoff_date(kickoff_at_utc: str | None) -> str | None:
    if not kickoff_at_utc:
        return None
    # ISO timestamps are YYYY-MM-DDTHH:MM:SS…
    return kickoff_at_utc[:10]


def _kickoff_epoch(kickoff_at_utc: str | None) -> int | None:
    if not kickoff_at_utc:
        return None
    text = str(kickoff_at_utc).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


# Approximate lock time after kickoff for completed schedule fixtures.
_SCHEDULE_MATCH_DURATION_SECONDS = 2 * 3600

@lru_cache(maxsize=1)
def schedule_knockout_outcomes() -> dict[str, dict[str, object]]:
    """Return knockout MATCH outcomes keyed by match local id.

    Explicit ``winner_team`` on a fixture wins. Otherwise winners are derived
    from team continuity into the next knockout rank (a team that appears in
    the next round won its feeder). Final / Third Place need an explicit
    winner because there is no later MATCH.
    """
    schedule = load_wc2026_schedule()
    fixtures = [f for f in (schedule.get("fixtures") or []) if isinstance(f, dict)]

    by_id: dict[str, dict[str, object]] = {}
    team_appearances: dict[str, list[tuple[int, str, str, str]]] = defaultdict(list)

    for raw in fixtures:
        stage_key = str(raw.get("stage_key") or "")
        rank = KNOCKOUT_STAGE_RANK.get(stage_key)
        if rank is None:
            continue
        home = ids.canonical_team_name(str(raw.get("home_team") or ""))
        away = ids.canonical_team_name(str(raw.get("away_team") or ""))
        if not home or not away:
            continue
        kickoff = raw.get("kickoff_at_utc")
        match_id = _match_local_id(home, away, kickoff if isinstance(kickoff, str) else None)
        kickoff_epoch = _kickoff_epoch(kickoff if isinstance(kickoff, str) else None)
        explicit = raw.get("winner_team")
        winner = (
            ids.canonical_team_name(str(explicit))
            if explicit
            else None
        )
        if winner and winner not in {home, away}:
            winner = None
        completed = str(raw.get("match_status") or "").casefold() == "completed"
        end_epoch = None
        # Explicit curated winners (Final / Third Place) lock at kickoff+2h even
        # when match_status is still ``scheduled``; derived winners still require
        # completed status so unfinished feeders stay open on the slider.
        if winner is not None and kickoff_epoch is not None and (
            completed or explicit
        ):
            end_epoch = kickoff_epoch + _SCHEDULE_MATCH_DURATION_SECONDS
        by_id[match_id] = {
            "home_team": home,
            "away_team": away,
            "stage_key": stage_key,
            "match_start_epoch": kickoff_epoch,
            "match_end_epoch": end_epoch,
            "winner_team": winner,
            "match_status": raw.get("match_status"),
        }
        for team in (home, away):
            team_appearances[ids.normalize_label(team)].append(
                (rank, match_id, home, away)
            )

    # Derive feeder winners from continuity into the next rank.
    # Skip Third Place appearances: losing semifinalists also "advance" in rank
    # to Third Place without having won their semifinal.
    for team_key, appearances in team_appearances.items():
        ordered = sorted(appearances, key=lambda item: item[0])
        for idx in range(len(ordered) - 1):
            prev_rank, prev_id, prev_home, prev_away = ordered[idx]
            next_rank, next_id, _nh, _na = ordered[idx + 1]
            if next_rank != prev_rank + 1:
                continue
            next_entry = by_id.get(next_id)
            if next_entry is not None and next_entry.get("stage_key") == "third_place":
                continue
            entry = by_id.get(prev_id)
            if entry is None or entry.get("winner_team"):
                continue
            if ids.normalize_label(prev_home) == team_key:
                winner = prev_home
            elif ids.normalize_label(prev_away) == team_key:
                winner = prev_away
            else:
                continue
            entry["winner_team"] = winner
            start = entry.get("match_start_epoch")
            if (
                entry.get("match_end_epoch") is None
                and isinstance(start, int)
                and str(entry.get("match_status") or "").casefold() == "completed"
            ):
                entry["match_end_epoch"] = start + _SCHEDULE_MATCH_DURATION_SECONDS

    return by_id


def _match_local_id(team_a: str, team_b: str, kickoff_at_utc: str | None) -> str:
    return match_local_id(team_a, team_b, date=_kickoff_date(kickoff_at_utc))


def _node(
    local_id: str,
    node_type: NodeType,
    label: str,
    evidence: list[str],
    aliases: list[str] | None = None,
) -> Node:
    return make_node(local_id, node_type, label, evidence, aliases=aliases)


def _edge(
    source: str,
    target: str,
    edge_type: EdgeType,
    evidence: list[str],
    evidence_text: str = "",
) -> Edge:
    return make_edge(
        source, target, edge_type, evidence, evidence_text=evidence_text
    )


def build_official_bracket_fragment(
    competition_label: str = DEFAULT_COMPETITION_LABEL,
) -> GraphFragment:
    """Build STAGE ladder + official MATCH placement + MATCH ADVANCES_TO MATCH."""
    schedule = load_wc2026_schedule()
    fixtures = schedule.get("fixtures") or []
    if len(fixtures) != 104:
        raise ValueError(
            f"wc2026_schedule.json must contain 104 fixtures; found {len(fixtures)}"
        )

    nodes: list[Node] = [
        _node(
            ids.competition_id(competition_label),
            NodeType.COMPETITION,
            competition_label,
            _SCHEDULE_EVIDENCE,
            aliases=[ids.slugify(competition_label)],
        )
    ]
    edges: list[Edge] = []

    for stage_label in ALL_STAGE_LABELS:
        stage_local = ids.stage_id(competition_label, stage_label)
        nodes.append(
            _node(stage_local, NodeType.STAGE, stage_label, _SCHEDULE_EVIDENCE)
        )
        edges.append(
            _edge(
                stage_local,
                ids.competition_id(competition_label),
                EdgeType.PART_OF,
                _SCHEDULE_EVIDENCE,
                evidence_text=stage_label,
            )
        )

    for src_label, tgt_label in STAGE_LADDER:
        edges.append(
            _edge(
                ids.stage_id(competition_label, src_label),
                ids.stage_id(competition_label, tgt_label),
                EdgeType.ADVANCES_TO,
                _SCHEDULE_EVIDENCE,
                evidence_text=f"{src_label} -> {tgt_label}",
            )
        )

    # team_key -> list of (rank, fifa_match_id, match_local_id)
    team_knockout_matches: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    teams_seen: set[str] = set()

    for fixture in fixtures:
        fifa_id = int(fixture["fifa_match_id"])
        stage_key = fixture["stage_key"]
        stage_label = STAGE_KEY_TO_LABEL.get(stage_key)
        if stage_label is None:
            raise ValueError(f"Unknown stage_key {stage_key!r} in schedule")

        raw_home = fixture["home_team"]
        raw_away = fixture["away_team"]
        home = ids.canonical_team_name(raw_home)
        away = ids.canonical_team_name(raw_away)
        evidence = [f"official:fifa-{fifa_id}"]
        kickoff = fixture.get("kickoff_at_utc")
        match_label = f"{home} vs. {away}"
        match_local = _match_local_id(home, away, kickoff)

        aliases = [f"fifa-match-{fifa_id}"]
        date = _kickoff_date(kickoff)
        if date:
            aliases.append(f"{ids.slugify(home)}-vs-{ids.slugify(away)}-{date}")

        nodes.append(
            _node(match_local, NodeType.MATCH, match_label, evidence, aliases=aliases)
        )
        edges.append(
            _edge(
                match_local,
                ids.stage_id(competition_label, stage_label),
                EdgeType.PART_OF,
                evidence,
                evidence_text=stage_label,
            )
        )

        for raw_team, team in ((raw_home, home), (raw_away, away)):
            if team not in teams_seen:
                team_aliases = [raw_team] if raw_team != team else []
                nodes.append(
                    _node(
                        ids.team_id(team),
                        NodeType.TEAM,
                        team,
                        evidence,
                        aliases=team_aliases or None,
                    )
                )
                teams_seen.add(team)
            edges.append(
                _edge(
                    ids.team_id(team),
                    match_local,
                    EdgeType.PARTICIPATES_IN,
                    evidence,
                    evidence_text=match_label,
                )
            )

        rank = KNOCKOUT_STAGE_RANK.get(stage_key)
        if rank is not None:
            for team in (home, away):
                team_knockout_matches[ids.normalize_label(team)].append(
                    (rank, fifa_id, match_local)
                )

    # Wire MATCH ADVANCES_TO MATCH from team continuity across consecutive ranks.
    advances_seen: set[tuple[str, str]] = set()
    for matches in team_knockout_matches.values():
        matches_sorted = sorted(matches, key=lambda item: (item[0], item[1]))
        for idx in range(len(matches_sorted) - 1):
            prev_rank, prev_fifa, prev_local = matches_sorted[idx]
            next_rank, next_fifa, next_local = matches_sorted[idx + 1]
            if next_rank != prev_rank + 1:
                continue
            key = (prev_local, next_local)
            if key in advances_seen:
                continue
            advances_seen.add(key)
            edges.append(
                _edge(
                    prev_local,
                    next_local,
                    EdgeType.ADVANCES_TO,
                    [
                        f"official:fifa-{prev_fifa}",
                        f"official:fifa-{next_fifa}",
                    ],
                    evidence_text="team continuity across consecutive knockout stages",
                )
            )

    return GraphFragment(nodes=nodes, edges=edges)
