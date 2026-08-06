"""Pure presentation helpers for the graph explorer (no Dash dependency)."""

from __future__ import annotations

import re
from typing import Any

from oddsgraph.bracket import KNOCKOUT_STAGE_RANK, STAGE_KEY_TO_LABEL

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

COLUMN_X_SPACING = 260
ROW_Y_SPACING = 72
BRACKET_ORIGIN_X = 80
BRACKET_ORIGIN_Y = 40
# Offset Third Place below Final in the terminal column.
THIRD_PLACE_Y_OFFSET = 160

_FIFA_ALIAS_RE = re.compile(r"^fifa-match-(\d+)$")
_VS_SPLIT_RE = re.compile(r"\s+vs\.?\s+", re.IGNORECASE)

# Interaction / visibility classes preserved independently of type classes.
PRESERVED_CLASSES = frozenset({"hidden", "path-active", "path-muted", "hovered"})

NODE_COLORS: dict[str, str] = {
    "COMPETITION": "#1f4e79",
    "STAGE": "#2e75b6",
    "GROUP": "#5b9bd5",
    "ROUND": "#9dc3e6",
    "MATCH": "#ed7d31",
    "TEAM": "#70ad47",
    "EVENT": "#7030a0",
    "MARKET": "#ffc000",
    "OUTCOME": "#a5a5a5",
}

EDGE_COLORS: dict[str, str] = {
    "PART_OF": "#5b9bd5",
    "PARTICIPATES_IN": "#70ad47",
    "QUALIFIES_FOR": "#ed7d31",
    "ADVANCES_TO": "#0f766e",
    "HAS_MARKET": "#7030a0",
    "HAS_OUTCOME": "#a5a5a5",
    "PRICES": "#ffc000",
    "IMPLIES": "#7f7f7f",
}


def short_match_label(label: str) -> str:
    """Return a two-line card label from ``Home vs. Away``."""
    parts = _VS_SPLIT_RE.split(label.strip(), maxsplit=1)
    if len(parts) != 2:
        return label.strip()
    home, away = parts[0].strip(), parts[1].strip()
    return f"{home}\n{away}"


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
        if _is_edge(el):
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
        if not _is_edge(el):
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
        if not _is_edge(el):
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
        if _is_edge(el):
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


def _is_edge(el: dict[str, Any]) -> bool:
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
        "padding": 30,
    }


def topology_layout(name: str = "breadthfirst") -> dict[str, Any]:
    """Cytoscape layout config for the full topology view."""
    if name == "dagre":
        return {
            "name": "dagre",
            "rankDir": "LR",
            "nodeSep": 40,
            "rankSep": 80,
            "animate": True,
            "padding": 20,
        }
    if name == "preset":
        return bracket_layout()
    layout: dict[str, Any] = {"name": name, "animate": True, "padding": 20}
    if name == "breadthfirst":
        layout["directed"] = True
        layout["spacingFactor"] = 1.2
    return layout


def bracket_stylesheet() -> list[dict[str, Any]]:
    """Cytoscape stylesheet tuned for LR match-card bracket view."""
    styles: list[dict[str, Any]] = [
        {
            "selector": "node",
            "style": {
                "label": "data(short_label)",
                "text-valign": "center",
                "text-halign": "center",
                "text-wrap": "wrap",
                "text-max-width": 140,
                "font-size": "11px",
                "font-weight": 600,
                "color": "#1a1d23",
                "background-color": "#fff7ed",
                "border-width": 1.5,
                "border-color": "#f0a56b",
                "shape": "round-rectangle",
                "width": 150,
                "height": 48,
                "padding": "6px",
                "min-zoomed-font-size": 8,
                "transition-property": "opacity, border-width, background-color",
                "transition-duration": "150ms",
            },
        },
        {
            "selector": "edge",
            "style": {
                "label": "",
                "curve-style": "taxi",
                "taxi-direction": "horizontal",
                "taxi-turn": 24,
                "target-arrow-shape": "triangle",
                "arrow-scale": 0.7,
                "width": 1.75,
                "line-color": "#9aa3ad",
                "target-arrow-color": "#9aa3ad",
                "opacity": 0.9,
            },
        },
        {
            "selector": ":selected",
            "style": {
                "border-width": 3,
                "border-color": "#111827",
                "line-color": "#111827",
                "target-arrow-color": "#111827",
                "z-index": 999,
            },
        },
        {
            "selector": ".hidden",
            "style": {"display": "none"},
        },
        {
            "selector": ".path-muted",
            "style": {"opacity": 0.22},
        },
        {
            "selector": ".path-active",
            "style": {
                "opacity": 1,
                "border-width": 2.5,
                "border-color": "#0f766e",
                "line-color": "#0f766e",
                "target-arrow-color": "#0f766e",
                "width": 2.75,
                "z-index": 900,
            },
        },
        {
            "selector": "node.path-active",
            "style": {
                "background-color": "#ecfdf5",
                "border-color": "#0f766e",
            },
        },
        # Stage accent borders.
        {
            "selector": 'node[stage = "Round of 32"]',
            "style": {"border-color": "#fb923c"},
        },
        {
            "selector": 'node[stage = "Round of 16"]',
            "style": {"border-color": "#f97316"},
        },
        {
            "selector": 'node[stage = "Quarterfinals"]',
            "style": {"border-color": "#ea580c"},
        },
        {
            "selector": 'node[stage = "Semifinals"]',
            "style": {"border-color": "#c2410c"},
        },
        {
            "selector": 'node[stage = "Final"]',
            "style": {
                "border-color": "#9a3412",
                "border-width": 2.5,
                "background-color": "#ffedd5",
                "width": 160,
                "height": 52,
                "font-size": "12px",
            },
        },
        {
            "selector": 'node[stage = "Third Place"]',
            "style": {
                "border-color": "#78716c",
                "background-color": "#f5f5f4",
            },
        },
    ]
    return styles


def topology_stylesheet(
    node_colors: dict[str, str],
    edge_colors: dict[str, str],
) -> list[dict[str, Any]]:
    """Cytoscape stylesheet for the full topology / mixed-type view."""
    styles: list[dict[str, Any]] = [
        {
            "selector": "node",
            "style": {
                "label": "data(label)",
                "text-valign": "center",
                "text-halign": "center",
                "font-size": "10px",
                "color": "#111",
                "background-color": "#888",
                "width": 28,
                "height": 28,
                "text-wrap": "wrap",
                "text-max-width": 80,
                "min-zoomed-font-size": 8,
                "shape": "ellipse",
            },
        },
        {
            "selector": "edge",
            "style": {
                "label": "data(label)",
                "font-size": "8px",
                "color": "#444",
                "curve-style": "bezier",
                "target-arrow-shape": "triangle",
                "arrow-scale": 0.8,
                "width": 1.5,
                "line-color": "#999",
                "target-arrow-color": "#999",
                "text-rotation": "autorotate",
                "text-margin-y": -8,
            },
        },
        {
            "selector": ":selected",
            "style": {
                "border-width": 3,
                "border-color": "#000",
                "line-color": "#000",
                "target-arrow-color": "#000",
                "z-index": 999,
            },
        },
        {
            "selector": ".hidden",
            "style": {"display": "none"},
        },
        {
            "selector": ".path-muted",
            "style": {"opacity": 0.25},
        },
        {
            "selector": ".path-active",
            "style": {
                "opacity": 1,
                "border-width": 3,
                "line-color": "#0f766e",
                "target-arrow-color": "#0f766e",
                "width": 2.5,
                "z-index": 900,
            },
        },
    ]
    for node_type, color in node_colors.items():
        styles.append(
            {
                "selector": f".{node_type}",
                "style": {"background-color": color},
            }
        )
    for edge_type, color in edge_colors.items():
        styles.append(
            {
                "selector": f".{edge_type}",
                "style": {
                    "line-color": color,
                    "target-arrow-color": color,
                },
            }
        )
    styles.append(
        {
            "selector": ".ADVANCES_TO",
            "style": {"width": 2.5},
        }
    )
    return styles
