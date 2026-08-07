"""Pure knockout-tree view-model for the mirrored bracket UI (no Dash)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from oddsgraph.explorer.presentation import fifa_match_id, is_edge


LEFT_ROUNDS_OUT_TO_IN: tuple[tuple[str, str, str], ...] = (
    ("r32", "R32", "Round of 32"),
    ("r16", "R16", "Round of 16"),
    ("qf", "QF", "Quarter-finals"),
    ("sf", "SF", "Semi-final"),
)

RIGHT_ROUNDS_IN_TO_OUT: tuple[tuple[str, str, str], ...] = tuple(
    reversed(LEFT_ROUNDS_OUT_TO_IN)
)

# Feeder round → next round for one-hop ripple animation.
_RIPPLE_ROUND_HOPS: tuple[tuple[str, str], ...] = (
    ("r32", "r16"),
    ("r16", "qf"),
    ("qf", "sf"),
)


@dataclass(frozen=True)
class BracketHalf:
    """One side of the mirrored knockout tree (8→4→2→1)."""

    r32: list[dict[str, Any]]
    r16: list[dict[str, Any]]
    qf: list[dict[str, Any]]
    sf: list[dict[str, Any]]

    def by_round(self, round_id: str) -> list[dict[str, Any]]:
        return getattr(self, round_id)


@dataclass(frozen=True)
class BracketTree:
    """Mirrored knockout bracket: left/right halves + Final/Third Place."""

    left: BracketHalf
    right: BracketHalf
    final: dict[str, Any] | None
    third_place: dict[str, Any] | None

    @property
    def champion(self) -> str | None:
        """Predicted champion from the Final card, when resolvable."""
        if not self.final:
            return None
        data = self.final.get("data") or self.final
        winner = data.get("winner_team")
        if winner and data.get("resolved"):
            return str(winner)
        home = data.get("home_team")
        away = data.get("away_team")
        home_prob = data.get("current_home_prob")
        if home and away and home_prob is not None:
            return str(home) if float(home_prob) >= 0.5 else str(away)
        if data.get("is_champion") and home and away:
            # Champion flag is set on the Final; prefer winner_team when present.
            return str(winner) if winner else str(home)
        if home and away and home_prob is None:
            return None
        return str(home) if home else (str(away) if away else None)


def _node_data(el: dict[str, Any]) -> dict[str, Any]:
    return el.get("data") or el


def _node_id(el: dict[str, Any]) -> str | None:
    data = _node_data(el)
    eid = data.get("id") or data.get("canonical_id")
    return str(eid) if eid else None


def _stable_sort_key(el: dict[str, Any]) -> tuple[int, str, str]:
    data = _node_data(el)
    eid = _node_id(el) or ""
    return (
        fifa_match_id(data.get("aliases")) or 10**9,
        str(data.get("label") or ""),
        eid,
    )


def _stable_order(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(elements, key=_stable_sort_key)


def _index_graph(
    elements: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, list[str]],
]:
    """Return ``(nodes_by_id, predecessors)`` from bracket element dicts."""
    nodes_by_id: dict[str, dict[str, Any]] = {}
    predecessors: dict[str, list[str]] = {}
    for el in elements:
        if is_edge(el):
            data = _node_data(el)
            if data.get("edge_type") != "ADVANCES_TO":
                continue
            source = data.get("source")
            target = data.get("target")
            if source and target:
                predecessors.setdefault(str(target), []).append(str(source))
            continue
        data = _node_data(el)
        if str(data.get("type") or "") != "MATCH":
            continue
        eid = _node_id(el)
        if eid:
            nodes_by_id[eid] = el
    return nodes_by_id, predecessors


def _expand_feeders(
    nodes_by_id: dict[str, dict[str, Any]],
    predecessors: dict[str, list[str]],
    parent_ids: list[str],
) -> list[str]:
    """Expand parent match ids to ordered feeder (predecessor) ids."""
    feeders: list[str] = []
    for parent_id in parent_ids:
        preds = [
            pid for pid in predecessors.get(parent_id, []) if pid in nodes_by_id
        ]
        ordered = _stable_order([nodes_by_id[pid] for pid in preds])
        feeders.extend(_node_id(el) for el in ordered if _node_id(el))
    return feeders


def _slice_by_ids(
    nodes_by_id: dict[str, dict[str, Any]],
    ids: list[str],
) -> list[dict[str, Any]]:
    return [nodes_by_id[i] for i in ids if i in nodes_by_id]


def _build_half(
    nodes_by_id: dict[str, dict[str, Any]],
    predecessors: dict[str, list[str]],
    semi_final_id: str,
) -> BracketHalf:
    sf_ids = [semi_final_id]
    qf_ids = _expand_feeders(nodes_by_id, predecessors, sf_ids)
    r16_ids = _expand_feeders(nodes_by_id, predecessors, qf_ids)
    r32_ids = _expand_feeders(nodes_by_id, predecessors, r16_ids)
    return BracketHalf(
        sf=_slice_by_ids(nodes_by_id, sf_ids),
        qf=_slice_by_ids(nodes_by_id, qf_ids),
        r16=_slice_by_ids(nodes_by_id, r16_ids),
        r32=_slice_by_ids(nodes_by_id, r32_ids),
    )


def _empty_half() -> BracketHalf:
    return BracketHalf(r32=[], r16=[], qf=[], sf=[])


def _fallback_halves(
    nodes_by_id: dict[str, dict[str, Any]],
) -> tuple[BracketHalf, BracketHalf]:
    """Split by stage when Final feeders are missing (partial fixtures)."""
    by_stage: dict[str, list[dict[str, Any]]] = {}
    for el in nodes_by_id.values():
        stage = str(_node_data(el).get("stage") or "")
        by_stage.setdefault(stage, []).append(el)

    def split(stage: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        ordered = _stable_order(by_stage.get(stage, []))
        mid = (len(ordered) + 1) // 2
        return ordered[:mid], ordered[mid:]

    left_r32, right_r32 = split("Round of 32")
    left_r16, right_r16 = split("Round of 16")
    left_qf, right_qf = split("Quarterfinals")
    left_sf, right_sf = split("Semifinals")
    return (
        BracketHalf(r32=left_r32, r16=left_r16, qf=left_qf, sf=left_sf),
        BracketHalf(r32=right_r32, r16=right_r16, qf=right_qf, sf=right_sf),
    )


def build_knockout_tree(elements: list[dict[str, Any]]) -> BracketTree:
    """Split MATCH elements into a mirrored left/right knockout tree.

    Walks ``ADVANCES_TO`` edges backward from the Final to find its two
    Semifinal predecessors, then recursively expands QF → R16 → R32 for each
    half. Within each feeder group, matches are ordered by FIFA match id.
    """
    nodes_by_id, predecessors = _index_graph(elements)

    final_el: dict[str, Any] | None = None
    third_el: dict[str, Any] | None = None
    for el in nodes_by_id.values():
        stage = str(_node_data(el).get("stage") or "")
        if stage == "Final":
            final_el = el
        elif stage == "Third Place":
            third_el = el

    if final_el is None:
        left, right = _fallback_halves(nodes_by_id)
        return BracketTree(left=left, right=right, final=None, third_place=third_el)

    final_id = _node_id(final_el)
    if not final_id:
        left, right = _fallback_halves(nodes_by_id)
        return BracketTree(left=left, right=right, final=final_el, third_place=third_el)
    sf_preds = [
        pid for pid in predecessors.get(final_id, []) if pid in nodes_by_id
    ]
    # Prefer Semifinals-stage feeders; fall back to any ADVANCES_TO pred.
    sf_els = [
        nodes_by_id[pid]
        for pid in sf_preds
        if str(_node_data(nodes_by_id[pid]).get("stage") or "") == "Semifinals"
    ]
    if len(sf_els) < 2:
        sf_els = [nodes_by_id[pid] for pid in sf_preds]
    sf_els = _stable_order(sf_els)

    if len(sf_els) >= 2:
        left_sf_id = _node_id(sf_els[0])
        right_sf_id = _node_id(sf_els[1])
        if left_sf_id and right_sf_id:
            left = _build_half(nodes_by_id, predecessors, left_sf_id)
            right = _build_half(nodes_by_id, predecessors, right_sf_id)
        else:
            left, right = _fallback_halves(nodes_by_id)
    elif len(sf_els) == 1:
        left_sf_id = _node_id(sf_els[0])
        if left_sf_id:
            left = _build_half(nodes_by_id, predecessors, left_sf_id)
            right = _empty_half()
        else:
            left, right = _fallback_halves(nodes_by_id)
    else:
        left, right = _fallback_halves(nodes_by_id)

    return BracketTree(
        left=left,
        right=right,
        final=final_el,
        third_place=third_el,
    )


@dataclass(frozen=True)
class RippleState:
    """One-hop ripple animation targets for just-finished matches.

    ``active_pairs`` keys are ``"{half}:{round_id}"`` (e.g. ``"left:r32"``,
    ``"right:sf"``) mapping to feeder-pair indices whose connector overlay
    should animate. ``target_ids`` are downstream MATCH ids to highlight.
    """

    active_pairs: dict[str, frozenset[int]]
    target_ids: frozenset[str]


def _match_just_finished(el: dict[str, Any] | None) -> bool:
    if not el:
        return False
    return bool(_node_data(el).get("just_finished"))


def _ripple_half(
    half: BracketHalf,
    *,
    half_key: str,
) -> tuple[dict[str, frozenset[int]], set[str]]:
    """Compute feeder-pair ripples within one bracket half."""
    active: dict[str, frozenset[int]] = {}
    targets: set[str] = set()
    for feeder_round, next_round in _RIPPLE_ROUND_HOPS:
        feeders = half.by_round(feeder_round)
        next_matches = half.by_round(next_round)
        if not feeders or not next_matches:
            continue
        pair_indices: set[int] = set()
        for pair_index in range(len(feeders) // 2):
            top = feeders[pair_index * 2] if pair_index * 2 < len(feeders) else None
            bottom = (
                feeders[pair_index * 2 + 1]
                if pair_index * 2 + 1 < len(feeders)
                else None
            )
            if not (_match_just_finished(top) or _match_just_finished(bottom)):
                continue
            pair_indices.add(pair_index)
            if pair_index < len(next_matches):
                target_id = _node_id(next_matches[pair_index])
                if target_id:
                    targets.add(target_id)
        if pair_indices:
            active[f"{half_key}:{feeder_round}"] = frozenset(pair_indices)
    return active, targets


def compute_ripple(tree: BracketTree) -> RippleState:
    """Return one-hop ripple pairs/targets from just-finished matches.

    Animates only the immediate next-round slot(s) a just-finished match
    feeds. Semifinal finishes also highlight Final and Third Place cards.
    """
    active: dict[str, frozenset[int]] = {}
    targets: set[str] = set()

    for half, half_key in ((tree.left, "left"), (tree.right, "right")):
        half_active, half_targets = _ripple_half(half, half_key=half_key)
        active.update(half_active)
        targets.update(half_targets)

    # Semi → Final / Third Place hop.
    left_sf = tree.left.sf[0] if tree.left.sf else None
    right_sf = tree.right.sf[0] if tree.right.sf else None
    left_done = _match_just_finished(left_sf)
    right_done = _match_just_finished(right_sf)
    if left_done:
        active["left:sf"] = frozenset({0})
    if right_done:
        active["right:sf"] = frozenset({0})
    if left_done or right_done:
        final_id = _node_id(tree.final) if tree.final else None
        if final_id:
            targets.add(final_id)
        # Third Place shares the same two Semifinal feeders (loser slots).
        third_id = _node_id(tree.third_place) if tree.third_place else None
        if third_id:
            targets.add(third_id)

    return RippleState(active_pairs=active, target_ids=frozenset(targets))


__all__ = [
    "BracketHalf",
    "BracketTree",
    "LEFT_ROUNDS_OUT_TO_IN",
    "RIGHT_ROUNDS_IN_TO_OUT",
    "RippleState",
    "build_knockout_tree",
    "compute_ripple",
]
