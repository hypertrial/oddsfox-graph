"""Time-aware knockout bracket projection from stage-reach markets."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from oddsgraph.flags import BLANK_FLAG_URL, flag_url_or_blank

_VS_SPLIT_RE = re.compile(r"\s+vs\.?\s+", re.IGNORECASE)

# Target MATCH stage -> (denominator reach stage, numerator reach/champion stage).
CONDITIONAL_LADDER: dict[str, tuple[str, str]] = {
    "Round of 32": ("Round of 32", "Round of 16"),
    "Round of 16": ("Round of 16", "Quarterfinals"),
    "Quarterfinals": ("Quarterfinals", "Semifinals"),
    "Semifinals": ("Semifinals", "Final"),
    "Final": ("Final", "Champion"),
}

# Stage used when ranking feeder candidates for a displayed MATCH stage.
# Third Place ranks by P(reach Final): lower ≈ more likely semifinal loser.
REACH_RANK_STAGE: dict[str, str] = {
    "Round of 32": "Round of 32",
    "Round of 16": "Round of 16",
    "Quarterfinals": "Quarterfinals",
    "Semifinals": "Semifinals",
    "Final": "Final",
    "Third Place": "Final",
}


@dataclass(frozen=True)
class ProjectedSide:
    team: str
    advance_score: float | None


@dataclass(frozen=True)
class ProjectedMatch:
    home: ProjectedSide
    away: ProjectedSide
    current_home_prob: float | None
    projected: bool
    projection_method: str
    probability_available: bool


def split_match_teams(label: str) -> tuple[str, str] | None:
    """Return ``(home, away)`` display names from a MATCH label, if parseable."""
    parts = _VS_SPLIT_RE.split(label.strip(), maxsplit=1)
    if len(parts) != 2:
        return None
    home, away = parts[0].strip(), parts[1].strip()
    if not home or not away:
        return None
    return home, away


def _is_edge(el: dict[str, Any]) -> bool:
    data = el.get("data") or {}
    return "source" in data and "target" in data


def home_prob_at_hour(
    data: dict[str, Any],
    hour_epoch: int | None,
) -> float | None:
    """Return home win-probability at ``hour_epoch``, locking after match end.

    Uses the latest ``odds_series`` point with ``h <= hour_epoch``. When the
    slider is at or past ``match_end_epoch`` and ``winner_team`` is known,
    returns ``1.0`` / ``0.0`` for home / away winners.
    """
    series = data.get("odds_series") or []
    if not isinstance(series, list) or not series:
        return None

    end_epoch = data.get("match_end_epoch")
    winner = data.get("winner_team")
    home = data.get("home_team") or data.get("schedule_home")
    if (
        hour_epoch is not None
        and end_epoch is not None
        and int(hour_epoch) >= int(end_epoch)
        and winner
        and home
    ):
        return 1.0 if str(winner) == str(home) else 0.0

    if hour_epoch is None:
        point = series[0]
    else:
        hour = int(hour_epoch)
        eligible = [p for p in series if int(p.get("h") or 0) <= hour]
        if not eligible:
            return None
        point = eligible[-1]
    try:
        return float(point["home"])
    except (KeyError, TypeError, ValueError):
        return None


def latest_reach_prob(
    series: list[dict[str, Any]] | None,
    hour_epoch: int | None,
) -> float | None:
    """Return the latest reach probability at or before ``hour_epoch``."""
    if not series:
        return None
    if hour_epoch is None:
        point = series[0]
    else:
        hour = int(hour_epoch)
        eligible = [p for p in series if int(p.get("h") or 0) <= hour]
        if not eligible:
            return None
        point = eligible[-1]
    try:
        return float(point["p"])
    except (KeyError, TypeError, ValueError):
        return None


def conditional_advance_score(
    team: str,
    match_stage: str,
    hour_epoch: int | None,
    stage_odds: dict[str, dict[str, list[dict[str, Any]]]],
) -> float | None:
    """Return P(reach next)/P(reach stage) for ``team`` at ``match_stage``."""
    ladder = CONDITIONAL_LADDER.get(match_stage)
    if ladder is None:
        return None
    denom_stage, numer_stage = ladder
    team_odds = stage_odds.get(team) or {}
    denom = latest_reach_prob(team_odds.get(denom_stage), hour_epoch)
    numer = latest_reach_prob(team_odds.get(numer_stage), hour_epoch)
    if denom is None or numer is None:
        return None
    if denom <= 0:
        return None
    return max(0.0, min(1.0, numer / denom))


def reach_prob_for_rank(
    team: str,
    match_stage: str,
    hour_epoch: int | None,
    stage_odds: dict[str, dict[str, list[dict[str, Any]]]],
) -> float | None:
    """P(reach displayed round) used to pick the most likely branch winner."""
    rank_stage = REACH_RANK_STAGE.get(match_stage)
    if rank_stage is None:
        return None
    return latest_reach_prob((stage_odds.get(team) or {}).get(rank_stage), hour_epoch)


def normalize_pair(
    score_a: float | None,
    score_b: float | None,
) -> tuple[float | None, float | None]:
    """Normalize two advance scores to a probability pair summing to 1."""
    if score_a is None or score_b is None:
        return None, None
    total = score_a + score_b
    if total <= 0:
        return None, None
    return score_a / total, score_b / total


def _match_resolved(data: dict[str, Any], hour_epoch: int | None) -> bool:
    end_epoch = data.get("match_end_epoch")
    winner = data.get("winner_team")
    if not winner or end_epoch is None or hour_epoch is None:
        return False
    return int(hour_epoch) >= int(end_epoch)


def _match_teams(data: dict[str, Any]) -> tuple[str, str] | None:
    home = data.get("home_team") or data.get("schedule_home")
    away = data.get("away_team") or data.get("schedule_away")
    if home and away:
        return str(home), str(away)
    return split_match_teams(str(data.get("label") or data.get("schedule_label") or ""))


def _pick_branch_team(
    feeder_data: dict[str, Any],
    *,
    match_stage: str,
    hour_epoch: int | None,
    stage_odds: dict[str, dict[str, list[dict[str, Any]]]],
    prefer_loser: bool = False,
) -> str | None:
    if _match_resolved(feeder_data, hour_epoch):
        winner = str(feeder_data.get("winner_team") or "")
        teams = _match_teams(feeder_data)
        if not prefer_loser:
            return winner or None
        if teams is None:
            return None
        home, away = teams
        if winner == home:
            return away
        if winner == away:
            return home
        return None

    teams = _match_teams(feeder_data)
    if teams is None:
        return None
    scored: list[tuple[float, str]] = []
    unscored: list[str] = []
    for team in teams:
        score = reach_prob_for_rank(team, match_stage, hour_epoch, stage_odds)
        if score is None:
            unscored.append(team)
            continue
        scored.append((score, team))
    if not scored:
        # Stage-reach unavailable: fall back to feeder advance-market odds.
        # Do not invent schedule-home favorites when those are also missing.
        home_prob = home_prob_at_hour(feeder_data, hour_epoch)
        if home_prob is None:
            return None
        home, away = teams
        scored = [(home_prob, home), (1.0 - home_prob, away)]
    elif prefer_loser and unscored:
        # Incomplete markets: treat the missing-odds side as the likelier loser.
        return unscored[0]
    scored.sort(key=lambda item: item[0], reverse=not prefer_loser)
    return scored[0][1]


def _feeder_team_set(feeder: dict[str, Any]) -> set[str]:
    teams: set[str] = set()
    for key in ("schedule_home", "schedule_away", "home_team", "away_team"):
        value = feeder.get(key)
        if value:
            teams.add(str(value))
    parsed = _match_teams(feeder)
    if parsed:
        teams.update(parsed)
    return teams


def order_feeders_for_slots(
    predecessors: list[dict[str, Any]],
    schedule_home: str,
    schedule_away: str,
) -> list[dict[str, Any]]:
    """Order feeders as ``[home_slot_feeder, away_slot_feeder]`` via schedule continuity."""
    if len(predecessors) < 2:
        return list(predecessors)

    home_feeder: dict[str, Any] | None = None
    away_feeder: dict[str, Any] | None = None
    leftover: list[dict[str, Any]] = []
    for feeder in predecessors:
        teams = _feeder_team_set(feeder)
        if schedule_home in teams and home_feeder is None:
            home_feeder = feeder
        elif schedule_away in teams and away_feeder is None:
            away_feeder = feeder
        else:
            leftover.append(feeder)

    if home_feeder is None or away_feeder is None:
        # Fall back to a stable label order when continuity cannot be matched.
        return sorted(
            predecessors,
            key=lambda d: (
                str(d.get("schedule_label") or d.get("label") or ""),
                str(d.get("id") or ""),
            ),
        )

    ordered = [home_feeder, away_feeder]
    for feeder in leftover:
        if feeder not in ordered:
            ordered.append(feeder)
    return ordered


def project_match_at_hour(
    data: dict[str, Any],
    *,
    hour_epoch: int | None,
    predecessors: list[dict[str, Any]],
    stage_odds: dict[str, dict[str, list[dict[str, Any]]]],
) -> ProjectedMatch:
    """Project displayed teams and advance probabilities for one MATCH node."""
    stage = str(data.get("stage") or "")
    schedule_teams = (
        str(
            data.get("schedule_home")
            or data.get("home_team")
            or ""
        ),
        str(
            data.get("schedule_away")
            or data.get("away_team")
            or ""
        ),
    )
    if not schedule_teams[0] or not schedule_teams[1]:
        parsed = split_match_teams(
            str(data.get("schedule_label") or data.get("label") or "")
        )
        if parsed:
            schedule_teams = parsed
    if not schedule_teams[0] or not schedule_teams[1]:
        return ProjectedMatch(
            home=ProjectedSide("TBD", None),
            away=ProjectedSide("TBD", None),
            current_home_prob=None,
            projected=True,
            projection_method="missing_teams",
            probability_available=False,
        )

    if _match_resolved(data, hour_epoch):
        home, away = schedule_teams
        winner = str(data.get("winner_team") or "")
        if winner == home:
            home_prob = 1.0
        elif winner == away:
            home_prob = 0.0
        else:
            home_prob = home_prob_at_hour(data, hour_epoch)
        return ProjectedMatch(
            home=ProjectedSide(home, home_prob),
            away=ProjectedSide(away, None if home_prob is None else 1.0 - home_prob),
            current_home_prob=home_prob,
            projected=False,
            projection_method="resolved",
            probability_available=home_prob is not None,
        )

    direct = home_prob_at_hour(data, hour_epoch)
    if predecessors:
        predecessors = order_feeders_for_slots(
            predecessors, schedule_teams[0], schedule_teams[1]
        )
    if stage == "Third Place":
        home_team = (
            _pick_branch_team(
                predecessors[0],
                match_stage=stage,
                hour_epoch=hour_epoch,
                stage_odds=stage_odds,
                prefer_loser=True,
            )
            if len(predecessors) >= 1
            else None
        )
        away_team = (
            _pick_branch_team(
                predecessors[1],
                match_stage=stage,
                hour_epoch=hour_epoch,
                stage_odds=stage_odds,
                prefer_loser=True,
            )
            if len(predecessors) >= 2
            else None
        )
        if home_team is None or away_team is None:
            return ProjectedMatch(
                home=ProjectedSide("", None),
                away=ProjectedSide("", None),
                current_home_prob=None,
                projected=False,
                projection_method="feeder_odds_unavailable",
                probability_available=False,
            )
        series_home = str(data.get("schedule_home") or schedule_teams[0])
        series_away = str(data.get("schedule_away") or schedule_teams[1])
        if direct is not None and {home_team, away_team} == {series_home, series_away}:
            home_prob = direct if home_team == series_home else 1.0 - direct
            return ProjectedMatch(
                home=ProjectedSide(home_team, home_prob),
                away=ProjectedSide(away_team, 1.0 - home_prob),
                current_home_prob=home_prob,
                projected=True,
                projection_method="third_place_direct",
                probability_available=True,
            )
        return ProjectedMatch(
            home=ProjectedSide(home_team, None),
            away=ProjectedSide(away_team, None),
            current_home_prob=None,
            projected=True,
            projection_method="third_place_unavailable",
            probability_available=False,
        )

    if predecessors:
        home_team = _pick_branch_team(
            predecessors[0],
            match_stage=stage,
            hour_epoch=hour_epoch,
            stage_odds=stage_odds,
        )
        away_team = (
            _pick_branch_team(
                predecessors[1],
                match_stage=stage,
                hour_epoch=hour_epoch,
                stage_odds=stage_odds,
            )
            if len(predecessors) >= 2
            else None
        )
        if home_team is None or away_team is None:
            return ProjectedMatch(
                home=ProjectedSide("", None),
                away=ProjectedSide("", None),
                current_home_prob=None,
                projected=False,
                projection_method="feeder_odds_unavailable",
                probability_available=False,
            )
        method = "stage_conditional"
        projected = not all(_match_resolved(p, hour_epoch) for p in predecessors)
    else:
        home_team, away_team = schedule_teams
        method = "schedule_conditional"
        projected = False

    series_home = str(data.get("schedule_home") or schedule_teams[0])
    series_away = str(data.get("schedule_away") or schedule_teams[1])
    if direct is not None and {home_team, away_team} == {series_home, series_away}:
        home_prob = direct if home_team == series_home else 1.0 - direct
        return ProjectedMatch(
            home=ProjectedSide(home_team, home_prob),
            away=ProjectedSide(away_team, 1.0 - home_prob),
            current_home_prob=home_prob,
            projected=projected,
            projection_method="direct_advance",
            probability_available=True,
        )

    score_home = conditional_advance_score(home_team, stage, hour_epoch, stage_odds)
    score_away = conditional_advance_score(away_team, stage, hour_epoch, stage_odds)
    home_prob, _ = normalize_pair(score_home, score_away)
    return ProjectedMatch(
        home=ProjectedSide(home_team, home_prob),
        away=ProjectedSide(away_team, None if home_prob is None else 1.0 - home_prob),
        current_home_prob=home_prob,
        projected=projected,
        projection_method=method if home_prob is not None else f"{method}_unavailable",
        probability_available=home_prob is not None,
    )


def format_prob_label(prob: float | None) -> str:
    if prob is None:
        return "—"
    return f"{round(prob * 100):d}%"


# Visual team-name budget for short_label probability column.
_CARD_TEAM_NAME_WIDTH = 14
_FIGURE_SPACE = "\u2007"
_NBSP = "\u00a0"


def truncate_team_name(name: str, *, width: int = _CARD_TEAM_NAME_WIDTH) -> str:
    """Truncate a display team name for the match card without losing identity."""
    text = str(name or "").strip()
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1].rstrip() + "…"


def _pad_team_name(name: str, *, width: int = _CARD_TEAM_NAME_WIDTH) -> str:
    truncated = truncate_team_name(name, width=width)
    return truncated + (_FIGURE_SPACE * max(0, width - len(truncated)))


def _pad_mark(mark: str, *, width: int = 8) -> str:
    """Right-align a probability / winner mark in a fixed-width column."""
    text = str(mark)
    if len(text) >= width:
        return text
    return (_FIGURE_SPACE * (width - len(text))) + text


def card_short_label(
    home: str,
    away: str,
    home_prob: float | None,
    away_prob: float | None,
    *,
    winner: str | None = None,
    stage: str | None = None,
) -> str:
    """Build the two-line match card text with a fixed probability column.

    Resolved Final / Third Place (and other knockout) winners keep numeric
    ``100%`` / ``0%`` on the card. Champion / 3rd meaning is carried by
    ``is_champion`` / ``is_third_place_winner``. ``winner`` / ``stage`` remain
    for call-site compatibility.
    """
    del winner, stage
    home_mark = format_prob_label(home_prob)
    away_mark = format_prob_label(away_prob)
    home_row = f"{_pad_team_name(home)}{_NBSP}{_pad_mark(home_mark)}"
    away_row = f"{_pad_team_name(away)}{_NBSP}{_pad_mark(away_mark)}"
    return f"{home_row}\n{away_row}"


_STAGE_PROCESS_ORDER: dict[str, int] = {
    "Round of 32": 1,
    "Round of 16": 2,
    "Quarterfinals": 3,
    "Semifinals": 4,
    "Final": 5,
    "Third Place": 6,
}


def apply_bracket_projection(
    elements: list[dict[str, Any]],
    hour_epoch: int | None,
    stage_odds: dict[str, dict[str, list[dict[str, Any]]]],
    *,
    flag_url_for_team: Callable[[str], str | None] | None = None,
) -> list[dict[str, Any]]:
    """Stamp projected teams, probs, labels, and optional flag URLs at ``hour``."""
    nodes_by_id: dict[str, dict[str, Any]] = {}
    predecessors: dict[str, list[str]] = {}
    match_indices: list[tuple[int, str]] = []
    for index, el in enumerate(elements):
        if _is_edge(el):
            data = el.get("data") or {}
            if data.get("edge_type") != "ADVANCES_TO":
                continue
            source = data.get("source")
            target = data.get("target")
            if source and target:
                predecessors.setdefault(str(target), []).append(str(source))
            continue
        data = el.get("data") or {}
        eid = data.get("id")
        if not eid:
            continue
        nodes_by_id[str(eid)] = dict(data)
        if str(data.get("type") or "") == "MATCH":
            match_indices.append((index, str(eid)))

    match_indices.sort(
        key=lambda item: (
            _STAGE_PROCESS_ORDER.get(
                str(nodes_by_id[item[1]].get("stage") or ""),
                99,
            ),
            str(nodes_by_id[item[1]].get("schedule_label") or nodes_by_id[item[1]].get("label") or ""),
            item[1],
        )
    )

    projected_by_index: dict[int, dict[str, Any]] = {}
    for index, node_id in match_indices:
        el = elements[index]
        data = dict(nodes_by_id[node_id])
        if not data.get("schedule_label"):
            data["schedule_label"] = str(data.get("label") or "")
        teams = split_match_teams(str(data.get("schedule_label") or ""))
        if teams:
            data.setdefault("schedule_home", teams[0])
            data.setdefault("schedule_away", teams[1])
        elif data.get("home_team") and data.get("away_team"):
            data.setdefault("schedule_home", str(data["home_team"]))
            data.setdefault("schedule_away", str(data["away_team"]))
            if not data.get("schedule_label"):
                data["schedule_label"] = (
                    f"{data['schedule_home']} vs. {data['schedule_away']}"
                )

        feeder_ids = predecessors.get(node_id, [])
        feeder_datas = [nodes_by_id[fid] for fid in feeder_ids if fid in nodes_by_id]
        feeder_datas = order_feeders_for_slots(
            feeder_datas,
            str(data.get("schedule_home") or ""),
            str(data.get("schedule_away") or ""),
        )

        projected = project_match_at_hour(
            data,
            hour_epoch=hour_epoch,
            predecessors=feeder_datas,
            stage_odds=stage_odds,
        )
        home = projected.home.team or None
        away = projected.away.team or None
        home_prob = projected.current_home_prob
        away_prob = None if home_prob is None else 1.0 - home_prob

        data["home_team"] = home
        data["away_team"] = away
        winner = None
        if (
            home
            and away
            and _match_resolved(data, hour_epoch)
            and data.get("winner_team")
        ):
            winner = str(data["winner_team"])
        if home and away:
            data["label"] = f"{home} vs. {away}"
            data["short_label"] = card_short_label(
                home,
                away,
                home_prob,
                away_prob,
                winner=winner,
                stage=str(data.get("stage") or ""),
            )
        else:
            data["label"] = str(
                data.get("schedule_label") or data.get("label") or "TBD vs. TBD"
            )
            data["short_label"] = "TBD"
        data["projected"] = projected.projected
        data["resolved"] = _match_resolved(data, hour_epoch)
        data["projection_method"] = projected.projection_method
        data["probability_available"] = projected.probability_available
        stage = str(data.get("stage") or "")
        data["is_champion"] = bool(
            winner and stage == "Final" and winner in {home, away}
        )
        data["is_third_place_winner"] = bool(
            winner and stage == "Third Place" and winner in {home, away}
        )
        data["home_prob_label"] = format_prob_label(home_prob)
        data["away_prob_label"] = format_prob_label(away_prob)
        if home_prob is None:
            data.pop("current_home_prob", None)
        else:
            data["current_home_prob"] = home_prob
        if flag_url_for_team is not None:
            # Prefer caller helper, but always keep two aligned image slots.
            home_flag = flag_url_for_team(home) if home else BLANK_FLAG_URL
            away_flag = flag_url_for_team(away) if away else BLANK_FLAG_URL
            home_flag = home_flag or BLANK_FLAG_URL
            away_flag = away_flag or BLANK_FLAG_URL
            data["home_flag"] = home_flag
            data["away_flag"] = away_flag
            data["flag_images"] = f"{home_flag} {away_flag}"
        else:
            data["home_flag"] = flag_url_or_blank(home)
            data["away_flag"] = flag_url_or_blank(away)
            data["flag_images"] = f"{data['home_flag']} {data['away_flag']}"
        nodes_by_id[node_id] = data
        projected_by_index[index] = {**el, "data": data}

    updated: list[dict[str, Any]] = []
    for index, el in enumerate(elements):
        if index in projected_by_index:
            updated.append(projected_by_index[index])
        else:
            updated.append(el)
    return updated
