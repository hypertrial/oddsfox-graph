"""Pure presentation helpers for the graph explorer (no Dash dependency)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from oddsgraph.bracket import (
    KNOCKOUT_STAGE_RANK,
    STAGE_KEY_TO_LABEL,
    StageWindow,
    schedule_stage_windows,
)

# Reverse map: stage label -> rank (Final and Third Place both rank 5).
STAGE_LABEL_TO_RANK: dict[str, int] = {
    STAGE_KEY_TO_LABEL[key]: rank for key, rank in KNOCKOUT_STAGE_RANK.items()
}

# Left-to-right column index for each knockout stage label.
STAGE_COLUMN: dict[str, int] = {
    "Round of 32": 0,
    "Round of 16": 1,
    "Quarterfinals": 2,
    "Semifinals": 3,
    "Final": 4,
    "Third Place": 4,
}

# One canvas header per bracket column (Final and Third Place share column 4).
BRACKET_COLUMN_HEADERS: tuple[tuple[int, str], ...] = (
    (0, "Round of 32"),
    (1, "Round of 16"),
    (2, "Quarterfinals"),
    (3, "Semifinals"),
    (4, "Final / 3rd"),
)

# Fixed tracker steps (Third Place + Final collapse into Final weekend).
TRACKER_STEPS: tuple[tuple[str, str, str], ...] = (
    ("groups", "Groups", "Group Stage"),
    ("r32", "Round of 32", "R32"),
    ("r16", "Round of 16", "R16"),
    ("qf", "Quarterfinals", "QF"),
    ("sf", "Semifinals", "SF"),
    ("final_weekend", "Final weekend", "Final"),
)

STAGE_HEADER_CLASS = "stage-header"
STAGE_HEADER_TYPE = "STAGE_HEADER"

# Larger cards than the prior 210×64 layout; keep headers aligned to card width.
MATCH_CARD_WIDTH = 248
MATCH_CARD_HEIGHT = 72
COLUMN_X_SPACING = 336
ROW_Y_SPACING = 96
BRACKET_ORIGIN_X = 120
BRACKET_ORIGIN_Y = 88
BRACKET_HEADER_Y = 18
THIRD_PLACE_Y_OFFSET = 200
BRACKET_LAYOUT_PADDING = 48

_FIFA_ALIAS_RE = re.compile(r"^fifa-match-(\d+)$")
_VS_SPLIT_RE = re.compile(r"\s+vs\.?\s+", re.IGNORECASE)

# Interaction / visibility classes preserved independently of type classes.
PRESERVED_CLASSES = frozenset({"hidden", "path-active", "path-muted"})

TimelineState = Literal["past", "active", "up-next", "future"]
PhaseState = Literal["active", "intermission", "complete"]


@dataclass(frozen=True)
class TournamentPhase:
    """Immutable view-model for the selected tournament hour."""

    key: str
    label: str
    detail: str
    state: PhaseState
    active_stage_labels: tuple[str, ...]
    next_stage: str | None
    tracker_step: str

    @property
    def accessible_summary(self) -> str:
        if self.state == "complete":
            return self.label
        if self.detail:
            return f"{self.label}. {self.detail}"
        return self.label


def short_match_label(label: str) -> str:
    """Return a two-line card label from ``Home vs. Away``."""
    parts = _VS_SPLIT_RE.split(label.strip(), maxsplit=1)
    if len(parts) != 2:
        return label.strip()
    home, away = parts[0].strip(), parts[1].strip()
    return f"{home}\n{away}"


def split_match_teams(label: str) -> tuple[str, str] | None:
    """Return ``(home, away)`` display names from a MATCH label, if parseable."""
    parts = _VS_SPLIT_RE.split(label.strip(), maxsplit=1)
    if len(parts) != 2:
        return None
    home, away = parts[0].strip(), parts[1].strip()
    if not home or not away:
        return None
    return home, away


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
    home = data.get("home_team")
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


def apply_time_slice(
    elements: list[dict[str, Any]],
    hour_epoch: int | None,
    *,
    stage_odds: dict[str, dict[str, list[dict[str, Any]]]] | None = None,
) -> list[dict[str, Any]]:
    """Project teams/probs at ``hour_epoch`` and stamp card presentation fields."""
    from oddsgraph.bracket_projection import apply_bracket_projection
    from oddsgraph.flags import flag_url_for_team

    projected = apply_bracket_projection(
        elements,
        hour_epoch,
        stage_odds or {},
        flag_url_for_team=flag_url_for_team,
    )
    return stamp_timeline_states(projected, hour_epoch)


def format_hour_label(hour_epoch: int | None) -> str:
    """Full UTC label for tooltips, titles, and debugging."""
    if hour_epoch is None:
        return "No odds history"
    dt = datetime.fromtimestamp(int(hour_epoch), tz=timezone.utc)
    return f"{dt.strftime('%A, %B')} {dt.day}, {dt.year} at {dt.strftime('%H:%M')} UTC"


def format_hour_label_compact(hour_epoch: int | None) -> str:
    """Primary compact UTC label for the playback dock."""
    if hour_epoch is None:
        return "No odds history"
    dt = datetime.fromtimestamp(int(hour_epoch), tz=timezone.utc)
    return f"{dt.strftime('%b')} {dt.day} · {dt.strftime('%H:%M')} UTC"


def format_hour_iso(hour_epoch: int | None) -> str:
    """Machine-readable ISO-8601 UTC timestamp for ``<time datetime>``."""
    if hour_epoch is None:
        return ""
    dt = datetime.fromtimestamp(int(hour_epoch), tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _window_contains(window: StageWindow, hour_epoch: int) -> bool:
    return int(window.start_epoch) <= int(hour_epoch) < int(window.end_epoch)


def _tracker_step_for_stage_key(stage_key: str) -> str:
    if stage_key == "group_stage":
        return "groups"
    if stage_key == "round_of_32":
        return "r32"
    if stage_key == "round_of_16":
        return "r16"
    if stage_key == "quarterfinal":
        return "qf"
    if stage_key == "semifinal":
        return "sf"
    if stage_key in {"third_place", "final"}:
        return "final_weekend"
    return "groups"


def _stage_label_for_key(stage_key: str) -> str:
    return STAGE_KEY_TO_LABEL.get(stage_key, stage_key)


def phase_at_hour(hour_epoch: int | None) -> TournamentPhase:
    """Return the tournament phase view-model for ``hour_epoch``."""
    windows = schedule_stage_windows()
    if hour_epoch is None or not windows:
        return TournamentPhase(
            key="unavailable",
            label="Schedule unavailable",
            detail="Load official schedule artifacts to scrub tournament time.",
            state="intermission",
            active_stage_labels=(),
            next_stage=None,
            tracker_step="groups",
        )

    hour = int(hour_epoch)
    first = windows[0]
    last = windows[-1]

    if hour < int(first.start_epoch):
        return TournamentPhase(
            key=f"before:{first.stage_key}",
            label=_stage_label_for_key(first.stage_key),
            detail=f"{_stage_label_for_key(first.stage_key)} next",
            state="intermission",
            active_stage_labels=(),
            next_stage=_stage_label_for_key(first.stage_key),
            tracker_step=_tracker_step_for_stage_key(first.stage_key),
        )

    if hour >= int(last.end_epoch):
        return TournamentPhase(
            key="complete",
            label="Tournament complete",
            detail="Champion locked. Scrub backward to replay earlier rounds.",
            state="complete",
            active_stage_labels=("Final",),
            next_stage=None,
            tracker_step="final_weekend",
        )

    active = [w for w in windows if _window_contains(w, hour)]
    if active:
        # Prefer the latest concurrent window (Final over earlier leftovers).
        current = active[-1]
        labels = tuple(w.label for w in active)
        if current.stage_key == "group_stage":
            detail = "Knockout bracket is projected"
            label = "Group Stage"
        elif current.stage_key == "third_place":
            detail = "Third-place playoff"
            label = "Final weekend"
        elif current.stage_key == "final":
            detail = "Final"
            label = "Final weekend"
        else:
            detail = current.label
            label = current.label
        return TournamentPhase(
            key=f"active:{current.stage_key}:{current.start_epoch}",
            label=label,
            detail=detail,
            state="active",
            active_stage_labels=labels,
            next_stage=None,
            tracker_step=_tracker_step_for_stage_key(current.stage_key),
        )

    # Between stages: next upcoming window.
    upcoming = next((w for w in windows if int(w.start_epoch) > hour), None)
    previous = next(
        (w for w in reversed(windows) if int(w.end_epoch) <= hour),
        None,
    )
    if upcoming is None:
        return TournamentPhase(
            key="complete",
            label="Tournament complete",
            detail="Champion locked. Scrub backward to replay earlier rounds.",
            state="complete",
            active_stage_labels=("Final",),
            next_stage=None,
            tracker_step="final_weekend",
        )
    next_label = upcoming.label
    if upcoming.stage_key == "third_place":
        label = "Final weekend"
        detail = "Third-place playoff next"
    elif upcoming.stage_key == "final":
        label = "Final weekend"
        detail = "Final next"
    else:
        label = f"{next_label} next"
        detail = (
            f"After {previous.label}" if previous is not None else f"{next_label} upcoming"
        )
    return TournamentPhase(
        key=f"gap:{upcoming.stage_key}:{upcoming.start_epoch}",
        label=label,
        detail=detail,
        state="intermission",
        active_stage_labels=(),
        next_stage=next_label,
        tracker_step=_tracker_step_for_stage_key(upcoming.stage_key),
    )


def tracker_step_states(
    phase: TournamentPhase,
) -> list[dict[str, str]]:
    """Return tracker step metadata for the shell (completed/active/up-next/future)."""
    step_ids = [step_id for step_id, _label, _abbr in TRACKER_STEPS]
    try:
        active_index = step_ids.index(phase.tracker_step)
    except ValueError:
        active_index = 0

    rows: list[dict[str, str]] = []
    for index, (step_id, label, abbr) in enumerate(TRACKER_STEPS):
        if phase.state == "complete":
            state = "completed" if index <= active_index else "future"
        elif phase.state == "intermission":
            if index < active_index:
                state = "completed"
            elif index == active_index:
                state = "up-next"
            else:
                state = "future"
        else:
            if index < active_index:
                state = "completed"
            elif index == active_index:
                state = "active"
            else:
                state = "future"
        rows.append(
            {
                "id": step_id,
                "label": label,
                "abbr": abbr,
                "state": state,
            }
        )
    return rows


def timeline_state_for_stage(
    stage_label: str,
    phase: TournamentPhase,
) -> TimelineState:
    """Map a knockout stage label onto past/active/up-next/future."""
    if not stage_label:
        return "future"
    if phase.state == "complete":
        return "past"
    if stage_label in phase.active_stage_labels:
        return "active"
    if phase.next_stage == stage_label or (
        phase.tracker_step == "final_weekend"
        and stage_label in {"Final", "Third Place"}
        and phase.state == "intermission"
    ):
        return "up-next"

    # Column order for knockout stages.
    order = {
        "Round of 32": 0,
        "Round of 16": 1,
        "Quarterfinals": 2,
        "Semifinals": 3,
        "Third Place": 4,
        "Final": 5,
    }
    active_ranks = [
        order[label] for label in phase.active_stage_labels if label in order
    ]
    stage_rank_value = order.get(stage_label)
    if stage_rank_value is None:
        return "future"
    if active_ranks and stage_rank_value < min(active_ranks):
        return "past"
    if phase.next_stage and order.get(phase.next_stage, 99) > stage_rank_value:
        # Stages before the upcoming next stage are past during intermission.
        if phase.state == "intermission" and stage_rank_value < order.get(
            phase.next_stage, 99
        ):
            return "past"
    if phase.state == "intermission" and phase.next_stage:
        next_rank = order.get(phase.next_stage)
        if next_rank is not None and stage_rank_value < next_rank:
            return "past"
    if phase.state == "active" and active_ranks and stage_rank_value < min(active_ranks):
        return "past"
    # Group Stage has no knockout column; keep projected knockout cards readable.
    if "Group Stage" in phase.active_stage_labels:
        return "up-next"
    if phase.state == "active" and active_ranks and stage_rank_value > max(active_ranks):
        return "future"
    return "future"


def stamp_timeline_states(
    elements: list[dict[str, Any]],
    hour_epoch: int | None,
) -> list[dict[str, Any]]:
    """Stamp ``timeline_state`` onto MATCH and stage-header nodes."""
    phase = phase_at_hour(hour_epoch)
    updated: list[dict[str, Any]] = []
    for el in elements:
        if is_edge(el):
            updated.append(el)
            continue
        data = dict(el.get("data") or {})
        if is_stage_header(el):
            label = str(data.get("label") or "")
            # Final / 3rd header follows Final weekend active/up-next/past.
            if label == "Final / 3rd":
                if (
                    "Final" in phase.active_stage_labels
                    or "Third Place" in phase.active_stage_labels
                    or phase.state == "complete"
                ):
                    if phase.state == "complete":
                        data["timeline_state"] = "past"
                    else:
                        data["timeline_state"] = "active"
                elif phase.tracker_step == "final_weekend":
                    data["timeline_state"] = "up-next"
                else:
                    # Infer from Final column order.
                    data["timeline_state"] = timeline_state_for_stage("Final", phase)
            else:
                data["timeline_state"] = timeline_state_for_stage(label, phase)
            updated.append({**el, "data": data})
            continue
        stage = str(data.get("stage") or "")
        if str(data.get("type") or "") == "MATCH" or stage:
            data["timeline_state"] = timeline_state_for_stage(stage, phase)
            updated.append({**el, "data": data})
            continue
        updated.append(el)
    return updated


def time_slider_marks(min_hour: int, max_hour: int) -> dict[int, dict[str, str]]:
    """Build compact stage-start marks for the playback slider."""
    marks: dict[int, dict[str, str]] = {}
    windows = schedule_stage_windows()
    for window in windows:
        hour = window.start_hour
        if hour < int(min_hour) or hour > int(max_hour):
            continue
        abbr = {
            "group_stage": "Groups",
            "round_of_32": "R32",
            "round_of_16": "R16",
            "quarterfinal": "QF",
            "semifinal": "SF",
            "third_place": "3rd",
            "final": "Final",
        }.get(window.stage_key, window.label)
        marks[hour] = {
            "label": abbr,
            "style": {"fontSize": "10px", "color": "#94a3b8", "whiteSpace": "nowrap"},
        }
    if int(min_hour) not in marks:
        marks[int(min_hour)] = {
            "label": format_hour_label_compact(min_hour).replace(" UTC", ""),
            "style": {"fontSize": "10px", "color": "#94a3b8", "whiteSpace": "nowrap"},
        }
    if int(max_hour) not in marks:
        marks[int(max_hour)] = {
            "label": "End",
            "style": {"fontSize": "10px", "color": "#94a3b8", "whiteSpace": "nowrap"},
        }
    return marks


def bracket_summary_text(
    elements: list[dict[str, Any]] | None,
    hour_epoch: int | None,
) -> str:
    """Concise screen-reader summary of the bracket at ``hour_epoch``.

    When ``elements`` is omitted (typical for live scrub updates), return
    phase + time only. Match counts are optional because they race the canvas
    scrub callback when derived from a stale ``graph-cyto.elements`` State.
    """
    phase = phase_at_hour(hour_epoch)
    time_text = format_hour_label_compact(hour_epoch)
    base = f"{phase.accessible_summary}. Selected time {time_text}."
    if not elements:
        return base
    projected = 0
    resolved = 0
    for el in elements:
        if is_edge(el) or is_stage_header(el):
            continue
        data = el.get("data") or {}
        if str(data.get("type") or "") != "MATCH":
            continue
        if data.get("resolved"):
            resolved += 1
        elif data.get("projected"):
            projected += 1
    return f"{base} {resolved} resolved matches, {projected} projected."


def fifa_match_id(aliases: list[str] | None) -> int | None:
    """Extract the official FIFA match id from node aliases, if present."""
    for alias in aliases or []:
        match = _FIFA_ALIAS_RE.match(str(alias))
        if match:
            return int(match.group(1))
    return None


def stage_rank(stage_label: str) -> int:
    """Return knockout rank for a stage label, or 0 if unknown."""
    return STAGE_LABEL_TO_RANK.get(stage_label, 0)


def stage_column(stage_label: str) -> int:
    """Return left-to-right column index for a stage label."""
    return STAGE_COLUMN.get(stage_label, 0)


def is_stage_header(el: dict[str, Any]) -> bool:
    """Return True for non-interactive bracket column header nodes."""
    if is_edge(el):
        return False
    classes = str(el.get("classes") or "").split()
    if STAGE_HEADER_CLASS in classes:
        return True
    data = el.get("data") or {}
    return str(data.get("type") or "") == STAGE_HEADER_TYPE


def bracket_stage_headers(
    *,
    columns: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Build non-interactive stage labels for occupied bracket columns."""
    headers: list[dict[str, Any]] = []
    for col, label in BRACKET_COLUMN_HEADERS:
        if columns is not None and col not in columns:
            continue
        headers.append(
            {
                "data": {
                    "id": f"stage-header:{col}",
                    "label": label,
                    "short_label": label,
                    "type": STAGE_HEADER_TYPE,
                    "confidence": 1.0,
                    "aliases": [],
                    "evidence_count": 0,
                    "resolution_method": "",
                    "inference_method": "",
                },
                "classes": STAGE_HEADER_CLASS,
                "position": {
                    "x": BRACKET_ORIGIN_X + col * COLUMN_X_SPACING,
                    "y": BRACKET_HEADER_Y,
                },
                "selectable": False,
                "grabbable": False,
            }
        )
    return headers


def combine_classes(*parts: str) -> str:
    """Join unique CSS classes preserving order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        for token in str(part or "").split():
            if token and token not in seen:
                seen.add(token)
                ordered.append(token)
    return " ".join(ordered)


def split_classes(classes: str | None) -> tuple[list[str], set[str]]:
    """Split classes into (semantic, preserved) buckets."""
    tokens = (classes or "").split()
    semantic: list[str] = []
    preserved: set[str] = set()
    for token in tokens:
        if token in PRESERVED_CLASSES:
            preserved.add(token)
        elif token:
            semantic.append(token)
    return semantic, preserved


def merge_class_sets(
    semantic: list[str] | str,
    preserved: set[str] | list[str],
) -> str:
    """Rebuild a class string from semantic + preserved buckets."""
    if isinstance(semantic, str):
        semantic_list = [c for c in semantic.split() if c]
    else:
        semantic_list = list(semantic)
    preserved_list = sorted(preserved) if isinstance(preserved, set) else list(preserved)
    return combine_classes(*semantic_list, *preserved_list)


def apply_path_highlight(
    elements: list[dict[str, Any]],
    focus_id: str | None,
) -> list[dict[str, Any]]:
    """Mark the ADVANCES_TO path through ``focus_id``; mute everything else.

    Walks ancestors and descendants. At the Final / Third Place fork, prefer
    Final unless the focus node is itself Third Place (matches the explorer
    copy: "path to the Final").

    When ``focus_id`` is None, strip path classes and return a clean copy.
    """
    if not focus_id:
        return _clear_path_classes(elements)

    nodes_by_id: dict[str, dict[str, Any]] = {}
    for el in elements:
        if is_edge(el):
            continue
        data = el.get("data") or {}
        eid = data.get("id")
        if eid:
            nodes_by_id[str(eid)] = data
    if focus_id not in nodes_by_id:
        return _clear_path_classes(elements)

    predecessors: dict[str, list[str]] = {}
    successors: dict[str, list[str]] = {}
    for el in elements:
        if not is_edge(el):
            continue
        data = el.get("data") or {}
        if data.get("edge_type") != "ADVANCES_TO":
            continue
        source = data.get("source")
        target = data.get("target")
        if not source or not target:
            continue
        predecessors.setdefault(str(target), []).append(str(source))
        successors.setdefault(str(source), []).append(str(target))

    prefer_final = str(nodes_by_id[focus_id].get("stage") or "") != "Third Place"

    def _next_hops(node_id: str) -> list[str]:
        hops = list(successors.get(node_id, []))
        if not prefer_final or len(hops) < 2:
            return hops
        stages = {
            hop: str(nodes_by_id.get(hop, {}).get("stage") or "") for hop in hops
        }
        if "Final" in stages.values() and "Third Place" in stages.values():
            return [hop for hop in hops if stages[hop] != "Third Place"]
        return hops

    active_nodes: set[str] = {focus_id}
    stack = [focus_id]
    while stack:
        current = stack.pop()
        for pred in predecessors.get(current, []):
            if pred not in active_nodes:
                active_nodes.add(pred)
                stack.append(pred)
    stack = [focus_id]
    while stack:
        current = stack.pop()
        for succ in _next_hops(current):
            if succ not in active_nodes:
                active_nodes.add(succ)
                stack.append(succ)

    active_edges: set[str] = set()
    for el in elements:
        if not is_edge(el):
            continue
        data = el.get("data") or {}
        eid = data.get("id")
        source = data.get("source")
        target = data.get("target")
        if (
            eid
            and data.get("edge_type") == "ADVANCES_TO"
            and source in active_nodes
            and target in active_nodes
        ):
            active_edges.add(eid)

    result: list[dict[str, Any]] = []
    for el in elements:
        data = el.get("data") or {}
        eid = data.get("id")
        semantic, preserved = split_classes(el.get("classes"))
        preserved.discard("path-active")
        preserved.discard("path-muted")
        if is_stage_header(el):
            # Column headers stay fully visible during path focus.
            result.append({**el, "classes": merge_class_sets(semantic, preserved)})
            continue
        if is_edge(el):
            if eid in active_edges:
                preserved.add("path-active")
            else:
                preserved.add("path-muted")
        else:
            if eid in active_nodes:
                preserved.add("path-active")
            else:
                preserved.add("path-muted")
        result.append({**el, "classes": merge_class_sets(semantic, preserved)})
    return result


def _clear_path_classes(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for el in elements:
        semantic, preserved = split_classes(el.get("classes"))
        preserved.discard("path-active")
        preserved.discard("path-muted")
        result.append({**el, "classes": merge_class_sets(semantic, preserved)})
    return result


def is_edge(el: dict[str, Any]) -> bool:
    data = el.get("data") or {}
    return "source" in data and "target" in data


def bracket_positions(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """Compute left-to-right preset ``{x, y}`` positions for knockout matches.

    Round-of-32 nodes are ordered by FIFA match id (then label). Later rounds
    sit at the vertical midpoint of their ADVANCES_TO predecessors. Final and
    Third Place share column 4 with a vertical offset.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        data = node.get("data") or node
        node_id = data.get("id") or data.get("canonical_id")
        if node_id:
            by_id[str(node_id)] = data

    predecessors: dict[str, list[str]] = {}
    for edge in edges:
        data = edge.get("data") or edge
        if data.get("edge_type") != "ADVANCES_TO":
            continue
        source = data.get("source")
        target = data.get("target")
        if source and target:
            predecessors.setdefault(str(target), []).append(str(source))

    # Group by stage rank / label.
    by_stage: dict[str, list[str]] = {}
    for node_id, data in by_id.items():
        stage = str(data.get("stage") or "")
        by_stage.setdefault(stage, []).append(node_id)

    positions: dict[str, dict[str, float]] = {}

    # Seed Round of 32 (or earliest available stage) top-to-bottom.
    seed_stage = "Round of 32"
    seed_ids = by_stage.get(seed_stage, [])
    if not seed_ids:
        # Fallback: nodes with no predecessors.
        seed_ids = [
            nid for nid in by_id if not predecessors.get(nid)
        ]
    seed_ids = _stable_order(seed_ids, by_id)

    for index, node_id in enumerate(seed_ids):
        data = by_id[node_id]
        col = stage_column(str(data.get("stage") or seed_stage))
        positions[node_id] = {
            "x": BRACKET_ORIGIN_X + col * COLUMN_X_SPACING,
            "y": BRACKET_ORIGIN_Y + index * ROW_Y_SPACING,
        }

    # Place remaining stages in rank order.
    remaining = [nid for nid in by_id if nid not in positions]
    # Process by increasing stage rank so predecessors are already placed.
    remaining.sort(
        key=lambda nid: (
            stage_rank(str(by_id[nid].get("stage") or "")),
            fifa_match_id(by_id[nid].get("aliases")) or 10**9,
            str(by_id[nid].get("label") or nid),
        )
    )

    for node_id in remaining:
        data = by_id[node_id]
        stage = str(data.get("stage") or "")
        col = stage_column(stage)
        preds = [p for p in predecessors.get(node_id, []) if p in positions]
        if preds:
            y = sum(positions[p]["y"] for p in preds) / len(preds)
        else:
            # Fallback slot within stage cohort.
            cohort = [
                nid
                for nid in _stable_order(by_stage.get(stage, []), by_id)
                if nid not in positions or nid == node_id
            ]
            slot = cohort.index(node_id) if node_id in cohort else 0
            y = BRACKET_ORIGIN_Y + slot * ROW_Y_SPACING * (2 ** max(col, 0))

        if stage == "Third Place":
            # Keep Third Place in the terminal column but below Final.
            final_ys = [
                positions[nid]["y"]
                for nid in by_stage.get("Final", [])
                if nid in positions
            ]
            if final_ys:
                y = max(final_ys) + THIRD_PLACE_Y_OFFSET
            else:
                y = BRACKET_ORIGIN_Y + 8 * ROW_Y_SPACING + THIRD_PLACE_Y_OFFSET

        positions[node_id] = {
            "x": BRACKET_ORIGIN_X + col * COLUMN_X_SPACING,
            "y": y,
        }

    return positions


def _stable_order(node_ids: list[str], by_id: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(
        node_ids,
        key=lambda nid: (
            fifa_match_id(by_id[nid].get("aliases")) or 10**9,
            str(by_id[nid].get("label") or nid),
            nid,
        ),
    )


def bracket_layout() -> dict[str, Any]:
    """Cytoscape layout config for the preset LR bracket."""
    return {
        "name": "preset",
        "animate": False,
        "fit": True,
        "padding": BRACKET_LAYOUT_PADDING,
    }


def bracket_stylesheet() -> list[dict[str, Any]]:
    """Cytoscape stylesheet tuned for LR match-card bracket view."""
    # Flag slots: left-aligned on each row, sized ~22×14 inside 248×72.
    flag_w = f"{(22 / MATCH_CARD_WIDTH) * 100:.3f}%"
    flag_h = f"{(14 / MATCH_CARD_HEIGHT) * 100:.3f}%"
    flag_x = f"{(10 / MATCH_CARD_WIDTH) * 100:.3f}%"
    flag_y_home = f"{(14 / MATCH_CARD_HEIGHT) * 100:.3f}%"
    flag_y_away = f"{(40 / MATCH_CARD_HEIGHT) * 100:.3f}%"
    styles: list[dict[str, Any]] = [
        {
            "selector": "node",
            "style": {
                "label": "data(short_label)",
                "text-valign": "center",
                "text-halign": "center",
                "text-wrap": "wrap",
                "text-max-width": 168,
                "font-size": "11px",
                "font-family": "JetBrains Mono, ui-monospace, monospace",
                "font-weight": 600,
                "color": "#e2e8f0",
                "background-color": "#172338",
                "border-width": 1.5,
                "border-color": "#334155",
                "shape": "round-rectangle",
                "width": MATCH_CARD_WIDTH,
                "height": MATCH_CARD_HEIGHT,
                "padding": "10px",
                "min-zoomed-font-size": 8,
                "transition-property": "opacity, border-width, background-color",
                "transition-duration": "150ms",
                "background-image": "data(flag_images)",
                "background-fit": "none none",
                "background-clip": "none none",
                "background-repeat": "no-repeat no-repeat",
                "background-width": f"{flag_w} {flag_w}",
                "background-height": f"{flag_h} {flag_h}",
                "background-position-x": f"{flag_x} {flag_x}",
                "background-position-y": f"{flag_y_home} {flag_y_away}",
                "text-margin-x": 8,
                "text-margin-y": 0,
                "overlay-padding": 4,
                "shadow-blur": 10,
                "shadow-color": "#020617",
                "shadow-opacity": 0.35,
                "shadow-offset-x": 0,
                "shadow-offset-y": 3,
            },
        },
        {
            "selector": "edge",
            "style": {
                "label": "",
                "curve-style": "taxi",
                "taxi-direction": "horizontal",
                "taxi-turn": 32,
                "target-arrow-shape": "none",
                "arrow-scale": 0.6,
                "width": 1.75,
                "line-color": "#475569",
                "target-arrow-color": "#475569",
                "opacity": 0.7,
            },
        },
        {
            "selector": ":selected",
            "style": {
                "border-width": 3,
                "border-color": "#22d3ee",
                "line-color": "#22d3ee",
                "target-arrow-color": "#22d3ee",
                "z-index": 999,
            },
        },
        {
            "selector": ".hidden",
            "style": {"display": "none"},
        },
        {
            "selector": ".path-muted",
            "style": {"opacity": 0.2},
        },
        {
            "selector": ".path-active",
            "style": {
                "opacity": 1,
                "border-width": 2.75,
                "border-color": "#22d3ee",
                "line-color": "#22d3ee",
                "target-arrow-color": "#22d3ee",
                "width": 2.5,
                "z-index": 900,
            },
        },
        {
            "selector": "node.path-active",
            "style": {
                "background-color": "#0f2a3a",
                "border-color": "#22d3ee",
            },
        },
        {
            "selector": f".{STAGE_HEADER_CLASS}",
            "style": {
                "label": "data(label)",
                "shape": "round-rectangle",
                "background-color": "#111c2e",
                "background-opacity": 1,
                "border-width": 1,
                "border-color": "#1e293b",
                "width": MATCH_CARD_WIDTH,
                "height": 32,
                "font-size": "12px",
                "font-family": "Inter, system-ui, sans-serif",
                "font-weight": 700,
                "color": "#94a3b8",
                "text-valign": "center",
                "text-halign": "center",
                "text-wrap": "wrap",
                "text-max-width": MATCH_CARD_WIDTH - 16,
                "text-margin-x": 0,
                "padding": "0px",
                "events": "no",
                "background-image": "none",
                "shadow-opacity": 0,
            },
        },
        {
            "selector": 'node[stage = "Round of 32"]',
            "style": {"border-color": "#38bdf8"},
        },
        {
            "selector": 'node[stage = "Round of 16"]',
            "style": {"border-color": "#0ea5e9"},
        },
        {
            "selector": 'node[stage = "Quarterfinals"]',
            "style": {"border-color": "#0284c7"},
        },
        {
            "selector": 'node[stage = "Semifinals"]',
            "style": {"border-color": "#0369a1"},
        },
        {
            "selector": 'node[stage = "Final"]',
            "style": {
                "border-color": "#14b8a6",
                "border-width": 2.25,
                "width": MATCH_CARD_WIDTH + 8,
                "height": MATCH_CARD_HEIGHT + 4,
                "font-size": "11px",
            },
        },
        {
            "selector": 'node[stage = "Third Place"]',
            "style": {
                "border-color": "#64748b",
            },
        },
        {
            "selector": "node[?resolved]",
            "style": {
                "background-color": "#0f2f28",
                "border-color": "#14b8a6",
            },
        },
        {
            "selector": "node[?projected]",
            "style": {
                "border-style": "dashed",
            },
        },
        {
            "selector": "node[?is_champion]",
            "style": {
                "border-color": "#eab308",
                "border-width": 3,
                "background-color": "#2a2412",
                "font-weight": 700,
            },
        },
        {
            "selector": "node[?is_third_place_winner]",
            "style": {
                "border-color": "#94a3b8",
                "border-width": 2.5,
                "background-color": "#1a2333",
                "font-weight": 700,
            },
        },
        {
            "selector": 'node[timeline_state = "past"]',
            "style": {
                "opacity": 0.72,
            },
        },
        {
            "selector": 'node[timeline_state = "active"]',
            "style": {
                "opacity": 1,
                "border-width": 2.5,
            },
        },
        {
            "selector": f'node.{STAGE_HEADER_CLASS}[timeline_state = "active"]',
            "style": {
                "color": "#22d3ee",
                "border-color": "#22d3ee",
                "background-color": "#0f2a3a",
            },
        },
        {
            "selector": f'node.{STAGE_HEADER_CLASS}[timeline_state = "up-next"]',
            "style": {
                "color": "#e2e8f0",
                "border-style": "dashed",
                "border-color": "#64748b",
            },
        },
        {
            "selector": 'node[timeline_state = "future"]',
            "style": {
                "opacity": 0.55,
            },
        },
        {
            "selector": f'node.{STAGE_HEADER_CLASS}[timeline_state = "future"]',
            "style": {
                "opacity": 0.55,
            },
        },
        {
            "selector": f'node.{STAGE_HEADER_CLASS}[timeline_state = "past"]',
            "style": {
                "opacity": 0.65,
            },
        },
    ]
    return styles
