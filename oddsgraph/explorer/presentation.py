"""Pure presentation helpers for the graph explorer (no Dash dependency)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from oddsgraph.bracket import (
    STAGE_KEY_TO_LABEL,
    StageWindow,
    schedule_playback_milestones,
    schedule_stage_windows,
)

# Fixed tracker steps for knockout playback (Group Stage is not scrubbable).
TRACKER_STEPS: tuple[tuple[str, str, str], ...] = (
    ("r32", "Round of 32", "R32"),
    ("r16", "Round of 16", "R16"),
    ("qf", "Quarterfinals", "QF"),
    ("sf", "Semifinals", "SF"),
    ("final_weekend", "Final weekend", "Final"),
)

# Interaction / visibility classes preserved independently of type classes.
PRESERVED_CLASSES = frozenset({"hidden"})

TimelineState = Literal["past", "active", "up-next", "future"]
PhaseState = Literal["active", "intermission", "complete"]

_FIFA_ALIAS_RE = re.compile(r"^fifa-match-(\d+)$")


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


def apply_time_slice(
    elements: list[dict[str, Any]],
    hour_epoch: int | None,
    *,
    stage_odds: dict[str, dict[str, list[dict[str, Any]]]] | None = None,
    previous_elements: list[dict[str, Any]] | None = None,
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
    stamped = stamp_timeline_states(projected, hour_epoch)
    return stamp_odds_motion(stamped, previous_elements)


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
    if stage_key in {"group_stage", "round_of_32"}:
        return "r32"
    if stage_key == "round_of_16":
        return "r16"
    if stage_key == "quarterfinal":
        return "qf"
    if stage_key == "semifinal":
        return "sf"
    if stage_key in {"third_place", "final"}:
        return "final_weekend"
    return "r32"


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
            tracker_step="r32",
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
    if phase.state == "intermission" and phase.next_stage:
        next_rank = order.get(phase.next_stage)
        if next_rank is not None and stage_rank_value < next_rank:
            return "past"
    if "Group Stage" in phase.active_stage_labels:
        return "up-next"
    if phase.state == "active" and active_ranks and stage_rank_value > max(active_ranks):
        return "future"
    return "future"


def _match_just_finished(data: dict[str, Any], hour_epoch: int | None) -> bool:
    """True when the scrubbed hour is exactly this match's full-time milestone."""
    if hour_epoch is None or not data.get("resolved"):
        return False
    end_epoch = data.get("match_end_epoch")
    if end_epoch is None:
        return False
    return int(hour_epoch) == int(end_epoch)


def stamp_timeline_states(
    elements: list[dict[str, Any]],
    hour_epoch: int | None,
) -> list[dict[str, Any]]:
    """Stamp ``timeline_state`` and ``just_finished`` onto MATCH nodes."""
    phase = phase_at_hour(hour_epoch)
    updated: list[dict[str, Any]] = []
    for el in elements:
        if is_edge(el):
            updated.append(el)
            continue
        data = dict(el.get("data") or {})
        stage = str(data.get("stage") or "")
        if str(data.get("type") or "") == "MATCH" or stage:
            data["timeline_state"] = timeline_state_for_stage(stage, phase)
            data["just_finished"] = _match_just_finished(data, hour_epoch)
            updated.append({**el, "data": data})
            continue
        updated.append(el)
    return updated


def _match_index(elements: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for el in elements:
        if is_edge(el):
            continue
        data = el.get("data") or {}
        match_id = str(data.get("id") or "")
        if match_id and (
            str(data.get("type") or "") == "MATCH" or data.get("stage")
        ):
            indexed[match_id] = data
    return indexed


def stamp_odds_motion(
    elements: list[dict[str, Any]],
    previous_elements: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Stamp per-match odds ticks vs the previously displayed hour.

    Only compares when the same two teams remain on the card (avoids misleading
    deltas when projected participants swap). Resolved cards stay quiet so
    just-finished remains the dominant full-time cue.
    """
    if not previous_elements:
        return elements
    previous_by_id = _match_index(previous_elements)
    if not previous_by_id:
        return elements

    updated: list[dict[str, Any]] = []
    for el in elements:
        if is_edge(el):
            updated.append(el)
            continue
        data = dict(el.get("data") or {})
        match_id = str(data.get("id") or "")
        prev = previous_by_id.get(match_id)
        if (
            not prev
            or data.get("resolved")
            or str(data.get("home_team") or "") != str(prev.get("home_team") or "")
            or str(data.get("away_team") or "") != str(prev.get("away_team") or "")
        ):
            updated.append({**el, "data": data})
            continue

        cur_raw = data.get("current_home_prob")
        prev_raw = prev.get("current_home_prob")
        if cur_raw is None or prev_raw is None:
            updated.append({**el, "data": data})
            continue

        cur = float(cur_raw)
        prev_prob = float(prev_raw)
        delta = cur - prev_prob
        # Ignore sub-percentage-point noise from float / rounding.
        if abs(delta) < 0.005:
            updated.append({**el, "data": data})
            continue

        delta_pp = int(round(delta * 100))
        if delta_pp == 0:
            updated.append({**el, "data": data})
            continue

        data["home_prob_delta_pp"] = delta_pp
        data["odds_tick_home"] = "up" if delta_pp > 0 else "down"
        data["odds_tick_away"] = "down" if delta_pp > 0 else "up"
        data["favorite_flipped"] = (prev_prob >= 0.5) != (cur >= 0.5)
        updated.append({**el, "data": data})
    return updated


def time_slider_marks(min_hour: int, max_hour: int) -> dict[int, dict[str, str] | str]:
    """Stage-boundary labels plus unlabeled kickoff/full-time snaps for ``step=None``."""
    marks: dict[int, dict[str, str] | str] = {}
    windows = schedule_stage_windows()
    for window in windows:
        hour = window.start_hour
        if hour < int(min_hour) or hour > int(max_hour):
            continue
        abbr = {
            "round_of_32": "R32",
            "round_of_16": "R16",
            "quarterfinal": "QF",
            "semifinal": "SF",
            "third_place": "3rd",
            "final": "Final",
        }.get(window.stage_key)
        if abbr is None:
            continue
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
    for epoch in schedule_playback_milestones():
        if min_hour <= epoch <= max_hour:
            marks.setdefault(epoch, "")
    return marks


def bracket_summary_text(
    elements: list[dict[str, Any]] | None,
    hour_epoch: int | None,
) -> str:
    """Concise screen-reader summary of the bracket at ``hour_epoch``."""
    phase = phase_at_hour(hour_epoch)
    time_text = format_hour_label_compact(hour_epoch)
    base = f"{phase.accessible_summary}. Selected time {time_text}."
    if not elements:
        return base
    projected = 0
    resolved = 0
    for el in elements:
        if is_edge(el):
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


def is_edge(el: dict[str, Any]) -> bool:
    data = el.get("data") or {}
    return "source" in data and "target" in data
