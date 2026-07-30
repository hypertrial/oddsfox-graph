from __future__ import annotations

import csv
import hashlib
import heapq
import json
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from .queries import DuckDB, q


REVIEW_FIELDS = (
    "sample_type",
    "proposition_a_id",
    "proposition_b_id",
    "proposed_relation",
    "discovery_method",
    "was_candidate",
    "explanation",
    "reviewer_expected_relation",
    "reviewer_correct",
    "reviewer_notes",
)

POSITIVE_RELATIONS = {
    "equivalent",
    "A_implies_B",
    "B_implies_A",
    "implies",
    "mutually_exclusive",
    "complement",
    "compatible",
}
REVIEW_RELATIONS = POSITIVE_RELATIONS | {"unrelated", "uncertain"}


def export_review(
    out_dir: Path,
    output_path: Path,
    *,
    accepted: int = 200,
    recall_pairs: int = 200,
    seed: int = 0,
) -> dict[str, int]:
    if accepted < 1 or recall_pairs < 2:
        raise ValueError("accepted must be positive and recall_pairs must be at least 2")
    out_dir = out_dir.resolve()
    output_path = output_path.resolve()
    db = DuckDB()
    try:
        edges = db.rows(
            f"""
            SELECT
                src_node_id AS proposition_a_id,
                dst_node_id AS proposition_b_id,
                edge_type AS proposed_relation,
                discovery_method,
                explanation
            FROM read_parquet('{q(out_dir / "logic_edges.parquet")}')
            ORDER BY discovery_method, edge_type, src_node_id, dst_node_id
            """
        )
        candidates = db.rows(
            f"""
            SELECT
                proposition_a_id,
                proposition_b_id,
                coalesce(classification_relation, deterministic_relation, '') AS proposed_relation,
                coalesce(discovery_method, '') AS discovery_method,
                coalesce(explanation, '') AS explanation,
                status
            FROM read_parquet('{q(out_dir / "relation_candidates.parquet")}')
            ORDER BY status, proposition_a_id, proposition_b_id
            """
        )
        propositions = db.rows(
            f"""
            SELECT proposition_id, category, competition
            FROM read_parquet('{q(out_dir / "propositions.parquet")}')
            ORDER BY proposition_id
            """
        )
    finally:
        db.close()

    accepted_rows = _stratified_take(
        edges,
        accepted,
        seed=seed,
        group_keys=("discovery_method", "proposed_relation"),
    )
    candidate_rows = _stratified_take(
        candidates,
        recall_pairs // 2,
        seed=seed + 1,
        group_keys=("status",),
    )
    candidate_pairs = {
        tuple(sorted((str(row["proposition_a_id"]), str(row["proposition_b_id"]))))
        for row in candidates
    }
    noncandidate_rows = _near_miss_pairs(
        propositions,
        candidate_pairs,
        recall_pairs - len(candidate_rows),
        seed=seed + 2,
    )
    if len(accepted_rows) < accepted:
        raise ValueError(
            f"Requested {accepted} accepted edges, but only "
            f"{len(accepted_rows)} are available"
        )
    if len(candidate_rows) + len(noncandidate_rows) < recall_pairs:
        raise ValueError(
            f"Requested {recall_pairs} recall pairs, but only "
            f"{len(candidate_rows) + len(noncandidate_rows)} are available"
        )

    rows: list[dict[str, object]] = []
    for row in accepted_rows:
        rows.append(
            _review_record(
                "accepted_edge",
                row,
                was_candidate=True,
            )
        )
    for row in candidate_rows:
        rows.append(
            _review_record(
                "recall_audit",
                row,
                was_candidate=True,
            )
        )
    for row in noncandidate_rows:
        rows.append(
            _review_record(
                "recall_audit",
                row,
                was_candidate=False,
            )
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return {
        "accepted_edges": len(accepted_rows),
        "recall_pairs": len(candidate_rows) + len(noncandidate_rows),
        "rows": len(rows),
    }


def score_review(
    out_dir: Path,
    labels_path: Path,
    output_path: Path | None = None,
) -> dict[str, object]:
    out_dir = out_dir.resolve()
    labels_path = labels_path.resolve()
    if not labels_path.is_file():
        raise ValueError(f"Review labels do not exist: {labels_path}")
    with labels_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    missing_columns = set(REVIEW_FIELDS) - set(rows[0] if rows else ())
    if missing_columns:
        raise ValueError(
            "Review labels are missing columns: " + ", ".join(sorted(missing_columns))
        )

    accepted_rows = [row for row in rows if row["sample_type"] == "accepted_edge"]
    recall_rows = [row for row in rows if row["sample_type"] == "recall_audit"]
    if len(accepted_rows) < 200:
        raise ValueError(
            f"At least 200 accepted edges must be reviewed; found {len(accepted_rows)}"
        )
    incomplete_accepted = [
        row
        for row in accepted_rows
        if _parse_bool(row.get("reviewer_correct")) is None
        or not (row.get("reviewer_expected_relation") or "").strip()
    ]
    incomplete_recall = [
        row
        for row in recall_rows
        if not (row.get("reviewer_expected_relation") or "").strip()
    ]
    if incomplete_accepted or incomplete_recall:
        raise ValueError(
            "Review labels are incomplete: "
            f"{len(incomplete_accepted)} accepted-edge judgments and "
            f"{len(incomplete_recall)} recall labels are missing"
        )
    invalid_relations = sorted(
        {
            row["reviewer_expected_relation"].strip()
            for row in rows
            if row["reviewer_expected_relation"].strip() not in REVIEW_RELATIONS
        }
    )
    if invalid_relations:
        raise ValueError(
            "Review labels contain unsupported relations: "
            + ", ".join(invalid_relations)
        )

    deterministic_rows = [
        row for row in accepted_rows if row["discovery_method"] == "deterministic"
    ]
    deterministic_precision = _precision(deterministic_rows)
    overall_precision = _precision(accepted_rows)
    positive_recall = [
        row
        for row in recall_rows
        if row["reviewer_expected_relation"].strip() in POSITIVE_RELATIONS
    ]
    candidate_recall = (
        sum(_parse_bool(row["was_candidate"]) is True for row in positive_recall)
        / len(positive_recall)
        if positive_recall
        else None
    )
    provenance_failures = _provenance_failure_count(out_dir)
    gates = {
        "reviewed_accepted_edges": len(accepted_rows) >= 200,
        "deterministic_precision": (
            deterministic_precision is not None and deterministic_precision > 0.99
        ),
        "overall_precision": overall_precision is not None and overall_precision > 0.95,
        "candidate_recall": candidate_recall is not None and candidate_recall > 0.95,
        "complete_provenance": provenance_failures == 0,
    }
    result: dict[str, object] = {
        "passed": all(gates.values()),
        "gates": gates,
        "metrics": {
            "reviewed_accepted_edges": len(accepted_rows),
            "reviewed_deterministic_edges": len(deterministic_rows),
            "reviewed_recall_pairs": len(recall_rows),
            "positive_recall_pairs": len(positive_recall),
            "deterministic_precision": deterministic_precision,
            "overall_precision": overall_precision,
            "candidate_recall": candidate_recall,
            "provenance_failures": provenance_failures,
        },
        "thresholds": {
            "deterministic_precision": ">0.99",
            "overall_precision": ">0.95",
            "candidate_recall": ">0.95",
            "reviewed_accepted_edges": ">=200",
        },
    }
    destination = (output_path or out_dir / "evaluation.json").resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def _stratified_take(
    rows: list[dict[str, object]],
    limit: int,
    *,
    seed: int,
    group_keys: tuple[str, ...],
) -> list[dict[str, object]]:
    groups: dict[tuple[str, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        groups[tuple(str(row.get(key) or "") for key in group_keys)].append(row)
    for group in groups.values():
        group.sort(key=lambda row: _row_hash(row, seed))
    selected = []
    while len(selected) < limit:
        progressed = False
        for key in sorted(groups):
            group = groups[key]
            if group:
                selected.append(group.pop(0))
                progressed = True
                if len(selected) == limit:
                    break
        if not progressed:
            break
    return selected


def _near_miss_pairs(
    propositions: list[dict[str, object]],
    candidates: set[tuple[str, str]],
    limit: int,
    *,
    seed: int,
) -> list[dict[str, object]]:
    # ponytail: O(n²) is bounded by discover's 2,000-proposition smoke-test limit.
    heap: list[tuple[int, str, str]] = []
    by_id = {str(row["proposition_id"]): row for row in propositions}
    ids = sorted(by_id)
    for a_id, b_id in combinations(ids, 2):
        if (a_id, b_id) in candidates:
            continue
        a, b = by_id[a_id], by_id[b_id]
        related_metadata = bool(
            (a.get("category") and a.get("category") == b.get("category"))
            or (
                a.get("competition")
                and a.get("competition") == b.get("competition")
            )
        )
        score = int(
            hashlib.sha256(f"{seed}|{a_id}|{b_id}".encode()).hexdigest()[:16],
            16,
        )
        if not related_metadata:
            score += 1 << 64
        item = (-score, a_id, b_id)
        if len(heap) < limit:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)
    return [
        {
            "proposition_a_id": a_id,
            "proposition_b_id": b_id,
            "proposed_relation": "",
            "discovery_method": "",
            "explanation": "Independent near-miss pair absent from candidate generation",
        }
        for _, a_id, b_id in sorted(heap, reverse=True)
    ]


def _review_record(
    sample_type: str,
    row: dict[str, object],
    *,
    was_candidate: bool,
) -> dict[str, object]:
    return {
        "sample_type": sample_type,
        "proposition_a_id": row["proposition_a_id"],
        "proposition_b_id": row["proposition_b_id"],
        "proposed_relation": row.get("proposed_relation") or "",
        "discovery_method": row.get("discovery_method") or "",
        "was_candidate": str(was_candidate).lower(),
        "explanation": row.get("explanation") or "",
        "reviewer_expected_relation": "",
        "reviewer_correct": "",
        "reviewer_notes": "",
    }


def _precision(rows: list[dict[str, str]]) -> float | None:
    if not rows:
        return None
    return sum(_parse_bool(row["reviewer_correct"]) is True for row in rows) / len(rows)


def _parse_bool(value: object) -> bool | None:
    normalized = str(value or "").strip().casefold()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    return None


def _provenance_failure_count(out_dir: Path) -> int:
    db = DuckDB()
    try:
        return int(
            db.scalar(
                f"""
                SELECT count(*)
                FROM read_parquet('{q(out_dir / "logic_edges.parquet")}')
                WHERE coalesce(trim(explanation), '') = ''
                    OR assumptions IS NULL
                    OR discovery_method NOT IN ('deterministic', 'llm')
                    OR (
                        discovery_method = 'deterministic'
                        AND coalesce(trim(rule_version), '') = ''
                    )
                    OR (
                        discovery_method = 'llm'
                        AND (
                            coalesce(trim(model_version), '') = ''
                            OR coalesce(trim(prompt_version), '') = ''
                        )
                    )
                """
            )
            or 0
        )
    finally:
        db.close()


def _row_hash(row: dict[str, object], seed: int) -> str:
    return hashlib.sha256(
        (
            str(seed)
            + "|"
            + "|".join(str(row.get(key) or "") for key in sorted(row))
        ).encode()
    ).hexdigest()
