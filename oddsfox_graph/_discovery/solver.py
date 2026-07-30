from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from typing import Any


SOLVER_VERSION = "pysat-rc2-1.9.dev7"
CONSTRAINT_VERSION = "logic-constraints-v1"
SYMMETRIC_RELATIONS = {
    "complement",
    "equivalent",
    "mutually_exclusive",
    "compatible",
}


def solve_proposals(
    proposals: Sequence[dict[str, Any]],
    *,
    reusable_components: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Select a maximum-confidence consistent proposal set by graph component."""

    if not proposals:
        return [], [], {
            "components": 0,
            "proposals": 0,
            "accepted": 0,
            "rejected": 0,
            "hard_clauses": 0,
            "soft_clauses": 0,
            "components_reused": 0,
            "components_recomputed": 0,
            "objective_cost": 0,
        }
    try:
        from pysat.examples.rc2 import RC2
        from pysat.formula import WCNF
    except ImportError as exc:  # pragma: no cover - installation guard
        raise ImportError(
            'Automated discovery consistency solving requires '
            '`pip install -e ".[discovery]"`.'
        ) from exc

    normalized = [_normalize_proposal(row) for row in proposals]
    components = _proposal_components(normalized)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    stats = {
        "components": len(components),
        "proposals": len(normalized),
        "accepted": 0,
        "rejected": 0,
        "hard_clauses": 0,
        "soft_clauses": 0,
        "components_reused": 0,
        "components_recomputed": 0,
        "objective_cost": 0,
    }
    reusable_components = reusable_components or {}
    for component in components:
        component_id = _component_id(component)
        ordered = sorted(component, key=lambda row: str(row["proposal_id"]))
        proposal_hash = proposal_set_hash(ordered)
        reusable = reusable_components.get(proposal_hash)
        if reusable is not None:
            accepted_ids = set(reusable.get("accepted_proposal_ids") or [])
            rejected_by_id = {
                str(row["proposal_id"]): row
                for row in (reusable.get("rejected_rows") or [])
            }
            for row in ordered:
                proposal_id = str(row["proposal_id"])
                if proposal_id in accepted_ids:
                    accepted.append(
                        {
                            **row,
                            "solver_version": SOLVER_VERSION,
                            "constraint_version": CONSTRAINT_VERSION,
                            "solver_component_id": component_id,
                            "_solver_component_objective": int(
                                reusable.get("objective_cost", 0)
                            ),
                            "_solver_component_hard_clauses": int(
                                reusable.get("hard_clause_count", 0)
                            ),
                            "_solver_component_soft_clauses": int(
                                reusable.get("soft_clause_count", 0)
                            ),
                        }
                    )
                elif proposal_id in rejected_by_id:
                    rejected.append(
                        {
                            **rejected_by_id[proposal_id],
                            "solver_component_id": component_id,
                            "_solver_component_objective": int(
                                reusable.get("objective_cost", 0)
                            ),
                            "_solver_component_hard_clauses": int(
                                reusable.get("hard_clause_count", 0)
                            ),
                            "_solver_component_soft_clauses": int(
                                reusable.get("soft_clause_count", 0)
                            ),
                        }
                    )
                else:
                    raise RuntimeError(
                        "Incremental solver state does not cover proposal "
                        f"{proposal_id}"
                    )
            stats["components_reused"] += 1
            stats["hard_clauses"] += int(
                reusable.get("hard_clause_count", 0)
            )
            stats["soft_clauses"] += int(
                reusable.get("soft_clause_count", 0)
            )
            stats["objective_cost"] += int(
                reusable.get("objective_cost", 0)
            )
            continue
        stats["components_recomputed"] += 1
        variables = {
            str(row["proposal_id"]): index
            for index, row in enumerate(ordered, start=1)
        }
        hard_clauses: list[tuple[list[int], str]] = []
        soft_clauses: list[tuple[list[int], int]] = []
        for index, row in enumerate(ordered):
            variable = variables[str(row["proposal_id"])]
            if _is_hard_fact(row):
                hard_clauses.append(([variable], "same_market.preserved"))
            else:
                weight = _proposal_weight(row, index, len(ordered))
                soft_clauses.append(([variable], weight))

        by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in ordered:
            by_pair[_pair(row)].append(row)
        for pair_rows in by_pair.values():
            for left_index, left in enumerate(pair_rows):
                for right in pair_rows[left_index + 1 :]:
                    constraint_id = (
                        "pair.single_strongest_relation"
                        if _same_typed_relation(left, right)
                        else "pair.incompatible_relations"
                    )
                    hard_clauses.append(
                        (
                            [
                                -variables[str(left["proposal_id"])],
                                -variables[str(right["proposal_id"])],
                            ],
                            constraint_id,
                        )
                    )

        dynamic_clauses: list[tuple[list[int], str]] = []
        component_cost = 0
        while True:
            formula = WCNF()
            for clause, _ in [*hard_clauses, *dynamic_clauses]:
                formula.append(clause)
            for clause, weight in soft_clauses:
                formula.append(clause, weight=weight)
            with RC2(formula, adapt=False, exhaust=True, verbose=0) as solver:
                model = solver.compute()
                component_cost = int(solver.cost)
            if model is None:
                raise RuntimeError(
                    f"Hard logic constraints are unsatisfiable in {component_id}"
                )
            selected_variables = {value for value in model if value > 0}
            selected = [
                row
                for row in ordered
                if variables[str(row["proposal_id"])] in selected_variables
            ]
            conflict = _equivalence_conflict(selected)
            if conflict is None:
                break
            conflict_ids, constraint_id = conflict
            dynamic_clauses.append(
                (
                    [-variables[proposal_id] for proposal_id in conflict_ids],
                    constraint_id,
                )
            )

        selected_ids = {str(row["proposal_id"]) for row in selected}
        component_hard_clause_count = len(hard_clauses) + len(
            dynamic_clauses
        )
        component_soft_clause_count = len(soft_clauses)
        for row in ordered:
            decorated = {
                **row,
                "solver_version": SOLVER_VERSION,
                "constraint_version": CONSTRAINT_VERSION,
                "solver_component_id": component_id,
                "_solver_component_objective": component_cost,
                "_solver_component_hard_clauses": component_hard_clause_count,
                "_solver_component_soft_clauses": component_soft_clause_count,
            }
            proposal_id = str(row["proposal_id"])
            if proposal_id in selected_ids:
                accepted.append(decorated)
                continue
            conflicts, constraints = _rejection_conflicts(
                row,
                selected,
                hard_clauses,
                dynamic_clauses,
                variables,
            )
            rejected.append(
                {
                    "proposal_id": proposal_id,
                    "src_node_id": row["src_node_id"],
                    "dst_node_id": row["dst_node_id"],
                    "edge_type": row["edge_type"],
                    "edge_basis": row["edge_basis"],
                    "confidence": row["confidence"],
                    "discovery_method": row["discovery_method"],
                    "rule_id": row.get("rule_id"),
                    "rule_version": row.get("rule_version"),
                    "model_version": row.get("model_version"),
                    "prompt_version": row.get("prompt_version"),
                    "rejection_reason": (
                        "conflicts with selected logic constraints"
                        if conflicts or constraints
                        else "lower-weight proposal excluded by MaxSAT objective"
                    ),
                    "conflicting_proposal_ids": conflicts,
                    "conflicting_constraint_ids": constraints
                    or ["maxsat.weighted_objective"],
                    "solver_component_id": component_id,
                    "_solver_component_objective": component_cost,
                    "_solver_component_hard_clauses": component_hard_clause_count,
                    "_solver_component_soft_clauses": component_soft_clause_count,
                }
            )
        stats["hard_clauses"] += component_hard_clause_count
        stats["soft_clauses"] += component_soft_clause_count
        stats["objective_cost"] += component_cost

    accepted.sort(
        key=lambda row: (
            str(row["src_node_id"]),
            str(row["dst_node_id"]),
            str(row["edge_type"]),
        )
    )
    rejected.sort(key=lambda row: str(row["proposal_id"]))
    stats["accepted"] = len(accepted)
    stats["rejected"] = len(rejected)
    return accepted, rejected, stats


def proposal_set_hash(rows: Sequence[dict[str, Any]]) -> str:
    payload = [
        {
            "proposal_id": str(row["proposal_id"]),
            "src": str(row["src_node_id"]),
            "dst": str(row["dst_node_id"]),
            "edge_type": str(row["edge_type"]),
            "confidence": float(row["confidence"]),
            "hard": _is_hard_fact(row),
        }
        for row in sorted(rows, key=lambda row: str(row["proposal_id"]))
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _normalize_proposal(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    src = str(normalized["src_node_id"])
    dst = str(normalized["dst_node_id"])
    relation = str(normalized["edge_type"])
    if src == dst:
        raise RuntimeError(f"Logic proposal cannot target itself: {src}")
    if relation in SYMMETRIC_RELATIONS and src > dst:
        src, dst = dst, src
        normalized["src_node_id"] = src
        normalized["dst_node_id"] = dst
    proposal_id = normalized.get("proposal_id")
    if not proposal_id:
        raw = "|".join(
            (
                src,
                dst,
                relation,
                str(normalized.get("discovery_method") or ""),
                str(normalized.get("rule_id") or ""),
                str(normalized.get("model_version") or ""),
                str(normalized.get("prompt_version") or ""),
            )
        )
        proposal_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    normalized["proposal_id"] = str(proposal_id)
    normalized.setdefault("rule_id", None)
    return normalized


def _proposal_components(
    proposals: Sequence[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root

    for row in proposals:
        union(str(row["src_node_id"]), str(row["dst_node_id"]))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in proposals:
        grouped[find(str(row["src_node_id"]))].append(row)
    return [
        sorted(rows, key=lambda row: str(row["proposal_id"]))
        for _, rows in sorted(grouped.items())
    ]


def _component_id(rows: Sequence[dict[str, Any]]) -> str:
    nodes = sorted(
        {
            str(row[key])
            for row in rows
            for key in ("src_node_id", "dst_node_id")
        }
    )
    return hashlib.sha256("|".join(nodes).encode("utf-8")).hexdigest()


def _pair(row: dict[str, Any]) -> tuple[str, str]:
    return tuple(
        sorted((str(row["src_node_id"]), str(row["dst_node_id"])))
    )  # type: ignore[return-value]


def _same_typed_relation(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left["src_node_id"],
        left["dst_node_id"],
        left["edge_type"],
    ) == (
        right["src_node_id"],
        right["dst_node_id"],
        right["edge_type"],
    )


def _is_hard_fact(row: dict[str, Any]) -> bool:
    return (
        row.get("discovery_method") == "deterministic"
        and row.get("edge_basis") == "same_market"
    )


def _proposal_weight(
    row: dict[str, Any],
    index: int,
    proposal_count: int,
) -> int:
    confidence_weight = max(
        1,
        int(round(float(row["confidence"]) * 1_000_000)),
    )
    strength = {
        "complement": 4,
        "equivalent": 3,
        "mutually_exclusive": 2,
        "implies": 1,
        "compatible": 0,
    }.get(str(row["edge_type"]), 0)
    tie_space = proposal_count + 1
    return (
        confidence_weight * 5 * tie_space
        + strength * tie_space
        + (proposal_count - index)
    )


def _equivalence_conflict(
    selected: Sequence[dict[str, Any]],
) -> tuple[list[str], str] | None:
    parent: dict[str, str] = {}
    equivalence_ids: dict[tuple[str, str], str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for row in selected:
        if row["edge_type"] == "equivalent":
            pair = _pair(row)
            equivalence_ids[pair] = str(row["proposal_id"])
            union(*pair)
    for row in selected:
        if row["edge_type"] not in {"complement", "mutually_exclusive"}:
            continue
        src, dst = str(row["src_node_id"]), str(row["dst_node_id"])
        if find(src) == find(dst):
            involved = [str(row["proposal_id"])]
            involved.extend(
                proposal_id
                for (left, right), proposal_id in equivalence_ids.items()
                if find(left) == find(src) and find(right) == find(src)
            )
            return sorted(set(involved)), "equivalence.class_self_exclusion"

    collapsed: dict[
        tuple[str, str], dict[str, list[str]]
    ] = defaultdict(lambda: defaultdict(list))
    for row in selected:
        if row["edge_type"] == "equivalent":
            continue
        left, right = find(str(row["src_node_id"])), find(str(row["dst_node_id"]))
        if left == right:
            continue
        key = tuple(sorted((left, right)))
        collapsed[key][str(row["edge_type"])].append(str(row["proposal_id"]))
    for roots, relation_map in collapsed.items():
        if len(relation_map) > 1:
            ids = sorted(
                proposal_id
                for proposal_ids in relation_map.values()
                for proposal_id in proposal_ids
            )
            ids.extend(
                proposal_id
                for (left, _), proposal_id in equivalence_ids.items()
                if find(left) in roots
            )
            return ids, "equivalence.class_relation_consistency"
    return None


def _rejection_conflicts(
    rejected: dict[str, Any],
    selected: Sequence[dict[str, Any]],
    hard_clauses: Sequence[tuple[list[int], str]],
    dynamic_clauses: Sequence[tuple[list[int], str]],
    variables: dict[str, int],
) -> tuple[list[str], list[str]]:
    rejected_id = str(rejected["proposal_id"])
    rejected_variable = variables[rejected_id]
    selected_ids = {
        str(row["proposal_id"]) for row in selected
    }
    proposal_by_variable = {
        variable: proposal_id for proposal_id, variable in variables.items()
    }
    conflicts: set[str] = set()
    constraints: set[str] = set()
    for clause, constraint_id in [*hard_clauses, *dynamic_clauses]:
        if -rejected_variable not in clause:
            continue
        constraints.add(constraint_id)
        conflicts.update(
            proposal_by_variable[-literal]
            for literal in clause
            if literal < 0
            and -literal != rejected_variable
            and proposal_by_variable[-literal] in selected_ids
        )
    return sorted(conflicts), sorted(constraints)
