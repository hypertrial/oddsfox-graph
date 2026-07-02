from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import linprog

from . import thresholds as T
from .queries import DuckDB, q


@dataclass(frozen=True)
class EventModel:
    event_slug: str
    node_ids: list[str]
    observed: np.ndarray
    index: dict[str, int]


def compute_transitive_closure(db: DuckDB, out_dir: Path) -> None:
    edges = db.rows("""
        SELECT src_node_id, dst_node_id, confidence, evidence
        FROM logic_edges_v
        WHERE edge_type = 'implies'
    """)
    graph: dict[str, set[str]] = defaultdict(set)
    meta: dict[tuple[str, str], dict[str, Any]] = {}
    for row in edges:
        src = row["src_node_id"]
        dst = row["dst_node_id"]
        graph[src].add(dst)
        meta[(src, dst)] = row

    derived: list[dict[str, Any]] = []
    for start in graph:
        visited: set[str] = set()
        queue: deque[tuple[str, list[str]]] = deque((n, [start, n]) for n in graph[start])
        while queue:
            node, path = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            for nxt in graph.get(node, ()):
                new_path = path + [nxt]
                if len(new_path) > 2:
                    src, dst = new_path[0], new_path[-1]
                    if (src, dst) not in meta:
                        base = meta.get((new_path[0], new_path[1]), {})
                        derived.append({
                            "src_node_id": src,
                            "dst_node_id": dst,
                            "edge_type": "implies",
                            "edge_basis": "transitive",
                            "confidence": float(base.get("confidence") or 0.5),
                            "path": "->".join(new_path),
                            "evidence": "transitive closure of accepted implications",
                        })
                        meta[(src, dst)] = derived[-1]
                queue.append((nxt, new_path))

    if derived:
        db.execute("CREATE TABLE derived_edges_v AS " + _derived_values_sql(derived))
    else:
        db.execute("""
            CREATE TABLE derived_edges_v AS
            SELECT
                NULL::VARCHAR AS src_node_id,
                NULL::VARCHAR AS dst_node_id,
                NULL::VARCHAR AS edge_type,
                NULL::VARCHAR AS edge_basis,
                NULL::DOUBLE AS confidence,
                NULL::VARCHAR AS path,
                NULL::VARCHAR AS evidence
            WHERE false
        """)
    db.execute(f"COPY derived_edges_v TO '{q(out_dir / 'derived_edges.parquet')}' (FORMAT PARQUET);")


def solve_event_coherence(db: DuckDB, out_dir: Path) -> list[str]:
    warnings: list[str] = []
    events = db.rows("""
        SELECT event_slug, list(node_id ORDER BY node_id) AS node_ids
        FROM nodes_v
        WHERE event_slug IS NOT NULL
        GROUP BY event_slug
    """)
    coherence_rows: list[dict[str, Any]] = []
    repair_rows: list[dict[str, Any]] = []

    for event in events:
        slug = event["event_slug"]
        node_ids = list(event["node_ids"])
        if len(node_ids) > T.LP_MAX_NODES_PER_EVENT:
            warnings.append(f"skipped LP for {slug}: {len(node_ids)} nodes exceeds cap")
            continue
        prices = {
            row["node_id"]: float(row["current_price"] or 0.0)
            for row in db.rows(f"""
                SELECT node_id, current_price
                FROM nodes_v
                WHERE event_slug = '{q(slug)}'
            """)
        }
        model = EventModel(
            slug,
            node_ids,
            np.array([prices[n] for n in node_ids]),
            {n: i for i, n in enumerate(node_ids)},
        )
        constraints = _collect_constraints(db, model)
        if len(constraints) > T.LP_MAX_CONSTRAINTS_PER_EVENT:
            warnings.append(f"skipped LP for {slug}: {len(constraints)} constraints exceeds cap")
            continue
        repaired, distance, status = _solve_l1_repair(model, constraints)
        if not math.isfinite(distance):
            distance = 1e6
        solver_status = "optimal" if status == "optimal" else "infeasible"
        coherence_rows.append({
            "event_slug": slug,
            "node_count": len(node_ids),
            "constraint_count": len(constraints),
            "incoherence_distance": distance,
            "solver_status": solver_status,
        })
        for node_id, obs, rep in zip(node_ids, model.observed, repaired):
            repair_rows.append({
                "event_slug": slug,
                "node_id": node_id,
                "observed_price": float(obs),
                "repaired_price": float(rep),
                "adjustment": float(rep - obs),
            })

    _write_table(db, out_dir / "coherence.parquet", "coherence_v", coherence_rows, [
        "event_slug", "node_count", "constraint_count", "incoherence_distance", "solver_status",
    ])
    _write_table(db, out_dir / "coherence_repairs.parquet", "coherence_repairs_v", repair_rows, [
        "event_slug", "node_id", "observed_price", "repaired_price", "adjustment",
    ])
    return warnings


def _collect_constraints(db: DuckDB, model: EventModel) -> list[tuple[str, list[tuple[int, float]], float]]:
    constraints: list[tuple[str, list[tuple[int, float]], float]] = []
    slug = q(model.event_slug)

    for row in db.rows(f"""
        SELECT market_id, list(node_id ORDER BY outcome_index) AS node_ids
        FROM nodes_v
        WHERE event_slug = '{slug}'
        GROUP BY market_id
    """):
        ids = [n for n in row["node_ids"] if n in model.index]
        if len(ids) < 2:
            continue
        coeffs = [(model.index[n], 1.0) for n in ids]
        constraints.append(("simplex", coeffs, 1.0))

    for row in db.rows(f"""
        SELECT src_node_id, dst_node_id
        FROM logic_edges_v
        WHERE edge_type = 'complement'
            AND event_slug_src = '{slug}'
    """):
        if row["src_node_id"] in model.index and row["dst_node_id"] in model.index:
            i, j = model.index[row["src_node_id"]], model.index[row["dst_node_id"]]
            constraints.append(("complement", [(i, 1.0), (j, 1.0)], 1.0))

    for table in ("logic_edges_v", "derived_edges_v"):
        for row in db.rows(f"""
            SELECT src_node_id, dst_node_id
            FROM {table}
            WHERE edge_type = 'implies'
                AND event_slug_src = '{slug}'
        """) if table == "logic_edges_v" else db.rows(f"""
            SELECT d.src_node_id, d.dst_node_id
            FROM derived_edges_v d
            JOIN nodes_v s ON s.node_id = d.src_node_id
            WHERE d.edge_type = 'implies' AND s.event_slug = '{slug}'
        """):
            if row["src_node_id"] in model.index and row["dst_node_id"] in model.index:
                i, j = model.index[row["src_node_id"]], model.index[row["dst_node_id"]]
                constraints.append(("implies", [(i, 1.0), (j, -1.0)], 0.0))

    for row in db.rows(f"""
        SELECT src_node_id, dst_node_id
        FROM logic_edges_v
        WHERE edge_type = 'mutually_exclusive'
            AND event_slug_src = '{slug}'
    """):
        if row["src_node_id"] in model.index and row["dst_node_id"] in model.index:
            i, j = model.index[row["src_node_id"]], model.index[row["dst_node_id"]]
            constraints.append(("exclusion", [(i, 1.0), (j, 1.0)], 1.0))

    families: dict[str, list[str]] = defaultdict(list)
    for row in db.rows(f"""
        SELECT node_id, event_slug
        FROM nodes_v
        WHERE event_slug = '{slug}' AND is_single_winner_family AND outcome_label = 'Yes'
    """):
        families[row["event_slug"]].append(row["node_id"])
    for nodes in families.values():
        coeffs = [(model.index[n], 1.0) for n in nodes if n in model.index]
        if len(coeffs) >= 2:
            constraints.append(("family_sum", coeffs, 1.0))
    return constraints


def _solve_l1_repair(
    model: EventModel,
    constraints: list[tuple[str, list[tuple[int, float]], float]],
) -> tuple[np.ndarray, float, str]:
    n = len(model.node_ids)
    if n == 0:
        return model.observed.copy(), 0.0, "empty"
    # Variables: x (n), s_plus (n), s_minus (n)
    num_vars = 3 * n
    c = np.zeros(num_vars)
    c[n:2 * n] = 1.0
    c[2 * n:] = 1.0

    A_eq = []
    b_eq = []
    for i in range(n):
        row = np.zeros(num_vars)
        row[i] = 1.0
        row[n + i] = -1.0
        row[2 * n + i] = 1.0
        A_eq.append(row)
        b_eq.append(model.observed[i])
    A_eq_arr = np.array(A_eq) if A_eq else None
    b_eq_arr = np.array(b_eq) if b_eq else None

    A_ub = []
    b_ub = []
    for _, coeffs, rhs in constraints:
        row = np.zeros(num_vars)
        for idx, weight in coeffs:
            row[idx] = weight
        if _ == "implies":
            A_ub.append(row)
            b_ub.append(rhs)
        else:
            # equality via two inequalities
            A_ub.append(row)
            b_ub.append(rhs)
            A_ub.append(-row)
            b_ub.append(-rhs)

    bounds = [(0.0, 1.0)] * n + [(0.0, None)] * (2 * n)
    result = linprog(
        c,
        A_ub=np.array(A_ub) if A_ub else None,
        b_ub=np.array(b_ub) if b_ub else None,
        A_eq=A_eq_arr,
        b_eq=b_eq_arr,
        bounds=bounds,
        method="highs",
    )
    if not result.success:
        return model.observed.copy(), float("inf"), result.message
    x = result.x[:n]
    distance = float(np.sum(np.abs(x - model.observed)))
    return x, distance, "optimal"


def _derived_values_sql(rows: list[dict[str, Any]]) -> str:
    values = ", ".join(
        "("
        f"'{q(row['src_node_id'])}', '{q(row['dst_node_id'])}', '{q(row['edge_type'])}', "
        f"'{q(row['edge_basis'])}', {row['confidence']}, '{q(row['path'])}', '{q(row['evidence'])}'"
        ")"
        for row in rows
    )
    return (
        "SELECT * FROM (VALUES " + values + ") AS t("
        "src_node_id, dst_node_id, edge_type, edge_basis, confidence, path, evidence)"
    )


def _write_table(
    db: DuckDB,
    path: Path,
    table: str,
    rows: list[dict[str, Any]],
    columns: list[str],
) -> None:
    if rows:
        values = ", ".join(
            "(" + ", ".join(_lit(row.get(col)) for col in columns) + ")"
            for row in rows
        )
        db.execute(f"CREATE TABLE {table} AS SELECT * FROM (VALUES {values}) AS t({', '.join(columns)})")
    else:
        nulls = ", ".join(f"NULL::{_duck_type(col)} AS {col}" for col in columns)
        db.execute(f"CREATE TABLE {table} AS SELECT {nulls} WHERE false")
    db.execute(f"COPY {table} TO '{q(path)}' (FORMAT PARQUET);")


def _duck_type(col: str) -> str:
    if col.endswith("_count"):
        return "BIGINT"
    if col in {"event_slug", "node_id", "solver_status"}:
        return "VARCHAR"
    return "DOUBLE"


def _lit(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return "'" + q(value) + "'"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "1e308"
        return repr(float(value))
    if isinstance(value, int):
        return str(value)
    return repr(value)
