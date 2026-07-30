from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from .bulk import create_and_fill
from ..queries import DuckDB


_MEMBERSHIP_COLUMNS = {
    "kind": "VARCHAR",
    "group_key": "VARCHAR",
    "proposition_id": "VARCHAR",
}

_DETERMINISTIC_MEMBERSHIP_COLUMNS = {
    **_MEMBERSHIP_COLUMNS,
    "numeric_value": "DOUBLE",
    "time_start": "TIMESTAMPTZ",
    "time_end": "TIMESTAMPTZ",
    "stage_rank": "INTEGER",
    "subject_key": "VARCHAR",
}

_EMBEDDING_REASON_COLUMNS = {
    "proposition_a_id": "VARCHAR",
    "proposition_b_id": "VARCHAR",
    "embedding_similarity": "DOUBLE",
    "embedding_rank": "INTEGER",
}

_FEATURE_COLUMNS = {
    "proposition_id": "VARCHAR",
    "predicate": "VARCHAR",
    "unit": "VARCHAR",
    "time_start": "TIMESTAMPTZ",
    "time_end": "TIMESTAMPTZ",
}

_PAIR_COLUMNS = {
    "proposition_a_id": "VARCHAR",
    "proposition_b_id": "VARCHAR",
}


def candidate_sort_key(row: dict[str, Any]) -> tuple[object, ...]:
    similarity = row["embedding_similarity"]
    return (
        -len(row["candidate_reasons"]),
        -float(similarity if similarity is not None else -1.0),
        str(row["proposition_a_id"]),
        str(row["proposition_b_id"]),
    )


def generate_candidates(
    propositions: Sequence[dict[str, Any]],
    config: Any,
    embedder: Callable[[list[str], Any], Any],
    *,
    semantic_keys: Sequence[str],
    hashable: Callable[[object], object],
    proposition_signature: Callable[[dict[str, Any]], tuple[object, ...]],
    deterministic_relation: Callable[
        [dict[str, Any], dict[str, Any], float],
        dict[str, Any] | None,
    ],
    embedding_text: Callable[[dict[str, Any]], str],
    stage_rank: Callable[[dict[str, Any]], int | None],
    is_winner: Callable[[dict[str, Any]], bool],
) -> list[dict[str, Any]]:
    """Generate capped candidates without materializing structural all-pairs in Python."""

    if len(propositions) < 2:
        return []
    proposition_by_id = {
        str(proposition["proposition_id"]): proposition
        for proposition in propositions
    }
    ids = sorted(proposition_by_id)
    structural_memberships: list[dict[str, Any]] = []
    deterministic_memberships: list[dict[str, Any]] = []

    def add_structural(kind: str, key: object, proposition_id: str) -> None:
        structural_memberships.append(
            {
                "kind": kind,
                "group_key": repr(key),
                "proposition_id": proposition_id,
            }
        )

    def add_deterministic(
        kind: str,
        key: object,
        proposition_id: str,
        proposition: dict[str, Any],
        *,
        numeric_value: float | None = None,
        rank: int | None = None,
    ) -> None:
        deterministic_memberships.append(
            {
                "kind": kind,
                "group_key": repr(key),
                "proposition_id": proposition_id,
                "numeric_value": numeric_value,
                "time_start": proposition.get("time_start"),
                "time_end": proposition.get("time_end"),
                "stage_rank": rank,
                "subject_key": repr(sorted(proposition.get("subject") or [])),
            }
        )

    for proposition_id in ids:
        proposition = proposition_by_id[proposition_id]
        add_structural("market", proposition["market_id"], proposition_id)
        add_deterministic(
            "market",
            proposition["market_id"],
            proposition_id,
            proposition,
        )
        event_key = proposition.get("event_id") or proposition.get("event_slug")
        if event_key:
            add_structural("event", event_key, proposition_id)
        if proposition.get("competition"):
            add_structural(
                "competition",
                proposition["competition"],
                proposition_id,
            )
        for subject in proposition.get("subject") or []:
            add_structural("entity", subject, proposition_id)

        high_confidence = (
            float(proposition.get("parse_confidence") or 0.0)
            >= float(config.parse_confidence)
        )
        if proposition.get("parse_status") == "parsed":
            signature = proposition_signature(proposition)
            add_structural("signature", signature, proposition_id)
            if proposition.get("threshold") is not None:
                numeric_key = tuple(
                    hashable(proposition.get(key))
                    for key in semantic_keys
                    if key != "threshold"
                )
                add_structural("numeric_rule", numeric_key, proposition_id)
            if proposition.get("time_start") and proposition.get("time_end"):
                time_key = tuple(
                    hashable(proposition.get(key))
                    for key in semantic_keys
                    if key not in {"time_start", "time_end"}
                )
                add_structural("time_rule", time_key, proposition_id)

        if high_confidence:
            signature = proposition_signature(proposition)
            add_deterministic(
                "signature",
                signature,
                proposition_id,
                proposition,
            )
            if (
                proposition.get("threshold") is not None
                and proposition.get("operator")
                in {
                    "greater_than",
                    "greater_than_or_equal",
                    "less_than",
                    "less_than_or_equal",
                }
            ):
                numeric_key = tuple(
                    hashable(proposition.get(key))
                    for key in semantic_keys
                    if key != "threshold"
                )
                add_deterministic(
                    "numeric",
                    numeric_key,
                    proposition_id,
                    proposition,
                    numeric_value=float(proposition["threshold"]),
                )
            if proposition.get("time_start") and proposition.get("time_end"):
                time_key = tuple(
                    hashable(proposition.get(key))
                    for key in semantic_keys
                    if key not in {"time_start", "time_end"}
                )
                add_deterministic(
                    "time",
                    time_key,
                    proposition_id,
                    proposition,
                )
            rank = stage_rank(proposition)
            if rank is not None and proposition.get("polarity") == "positive":
                add_deterministic(
                    "stage",
                    (
                        hashable(proposition.get("subject")),
                        hashable(proposition.get("competition")),
                        proposition.get("polarity"),
                    ),
                    proposition_id,
                    proposition,
                    rank=rank,
                )
            if (
                event_key
                and is_winner(proposition)
                and proposition.get("polarity") == "positive"
            ):
                add_deterministic(
                    "winner",
                    event_key,
                    proposition_id,
                    proposition,
                )

    embedding_rows = _embedding_reason_rows(
        ids,
        proposition_by_id,
        config,
        embedder,
        embedding_text,
    )
    feature_rows = [
        {
            "proposition_id": proposition_id,
            "predicate": proposition_by_id[proposition_id].get("predicate"),
            "unit": proposition_by_id[proposition_id].get("unit"),
            "time_start": proposition_by_id[proposition_id].get("time_start"),
            "time_end": proposition_by_id[proposition_id].get("time_end"),
        }
        for proposition_id in ids
    ]

    db = DuckDB()
    try:
        db.execute("SET TimeZone = 'UTC'")
        create_and_fill(
            db,
            "structural_memberships",
            _MEMBERSHIP_COLUMNS,
            structural_memberships,
        )
        create_and_fill(
            db,
            "deterministic_memberships",
            _DETERMINISTIC_MEMBERSHIP_COLUMNS,
            deterministic_memberships,
        )
        create_and_fill(
            db,
            "embedding_reasons",
            _EMBEDDING_REASON_COLUMNS,
            embedding_rows,
        )
        create_and_fill(db, "proposition_features", _FEATURE_COLUMNS, feature_rows)
        db.execute(_DETERMINISTIC_PAIR_SQL)
        deterministic_pair_count = int(
            db.scalar("SELECT count(*) FROM deterministic_pairs") or 0
        )
        if deterministic_pair_count > int(config.max_candidates):
            raise ValueError(
                f"Deterministic rules produced {deterministic_pair_count} candidates, "
                f"exceeding max_candidates={config.max_candidates}; refusing to "
                "truncate proven relations"
            )

        deterministic_relations: dict[tuple[str, str], dict[str, Any]] = {}
        for pair in db.rows(
            """
            SELECT proposition_a_id, proposition_b_id
            FROM deterministic_pairs
            ORDER BY proposition_a_id, proposition_b_id
            """
        ):
            a_id = str(pair["proposition_a_id"])
            b_id = str(pair["proposition_b_id"])
            relation = deterministic_relation(
                proposition_by_id[a_id],
                proposition_by_id[b_id],
                float(config.parse_confidence),
            )
            if relation is None:
                raise RuntimeError(
                    "Deterministic candidate generation disagrees with relation "
                    f"evaluation for {(a_id, b_id)}"
                )
            deterministic_relations[(a_id, b_id)] = relation

        create_and_fill(
            db,
            "accepted_deterministic_pairs",
            _PAIR_COLUMNS,
            [
                {
                    "proposition_a_id": pair[0],
                    "proposition_b_id": pair[1],
                }
                for pair in deterministic_relations
            ],
        )
        rows = db.rows(
            _CAPPED_CANDIDATE_SQL,
            [int(config.max_candidates)],
        )
    finally:
        db.close()

    candidates = []
    for raw in rows:
        a_id = str(raw["proposition_a_id"])
        b_id = str(raw["proposition_b_id"])
        relation = deterministic_relations.get((a_id, b_id))
        row: dict[str, Any] = {
            "proposition_a_id": a_id,
            "proposition_b_id": b_id,
            "candidate_reasons": list(raw["candidate_reasons"] or []),
            "embedding_similarity": raw["embedding_similarity"],
            "embedding_rank": raw["embedding_rank"],
            "deterministic_relation": None,
            "classification_relation": None,
            "classification_confidence": None,
            "explanation": None,
            "assumptions": [],
            "requires_review": False,
            "status": "pending",
            "discovery_method": None,
            "model_version": None,
            "prompt_version": None,
        }
        if relation is not None:
            row["_deterministic"] = relation
            row["deterministic_relation"] = str(relation["edge_type"])
            row["status"] = "accepted"
            row["discovery_method"] = "deterministic"
            row["explanation"] = relation["explanation"]
        candidates.append(row)
    return candidates


def _embedding_reason_rows(
    ids: list[str],
    proposition_by_id: dict[str, dict[str, Any]],
    config: Any,
    embedder: Callable[[list[str], Any], Any],
    embedding_text: Callable[[dict[str, Any]], str],
) -> list[dict[str, Any]]:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - dependency installation guard
        raise ImportError(
            'Automated discovery requires `pip install -e ".[discovery]"`.'
        ) from exc

    texts = [embedding_text(proposition_by_id[proposition_id]) for proposition_id in ids]
    matrix = np.asarray(embedder(texts, config), dtype=np.float32)
    if (
        matrix.ndim != 2
        or matrix.shape[0] != len(ids)
        or matrix.shape[1] == 0
        or not np.isfinite(matrix).all()
    ):
        raise ValueError("Embedding model returned an invalid matrix shape")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.maximum(norms, 1e-12)
    similarities = matrix @ matrix.T
    rows = []
    for index, proposition_id in enumerate(ids):
        scores = similarities[index].copy()
        scores[index] = -np.inf
        ranked = np.argsort(-scores, kind="stable")[
            : min(int(config.top_k), len(ids) - 1)
        ]
        for rank, other_index in enumerate(ranked, start=1):
            other_id = ids[int(other_index)]
            a_id, b_id = sorted((proposition_id, other_id))
            rows.append(
                {
                    "proposition_a_id": a_id,
                    "proposition_b_id": b_id,
                    "embedding_similarity": float(scores[int(other_index)]),
                    "embedding_rank": rank,
                }
            )
    return rows


_DETERMINISTIC_PAIR_SQL = """
CREATE TABLE deterministic_pairs AS
SELECT DISTINCT
    a.proposition_id AS proposition_a_id,
    b.proposition_id AS proposition_b_id
FROM deterministic_memberships a
JOIN deterministic_memberships b
  ON a.kind = b.kind
 AND a.group_key = b.group_key
 AND a.proposition_id < b.proposition_id
WHERE a.kind IN ('market', 'signature')
   OR (
        a.kind = 'numeric'
        AND a.numeric_value != b.numeric_value
   )
   OR (
        a.kind = 'time'
        AND (
            (
                a.time_start <= b.time_start
                AND a.time_end >= b.time_end
                AND NOT (
                    b.time_start <= a.time_start
                    AND b.time_end >= a.time_end
                )
            )
            OR (
                b.time_start <= a.time_start
                AND b.time_end >= a.time_end
                AND NOT (
                    a.time_start <= b.time_start
                    AND a.time_end >= b.time_end
                )
            )
        )
   )
   OR (a.kind = 'stage' AND a.stage_rank != b.stage_rank)
   OR (a.kind = 'winner' AND a.subject_key != b.subject_key)
"""


_CAPPED_CANDIDATE_SQL = """
WITH raw_reasons AS (
    SELECT
        a.proposition_id AS proposition_a_id,
        b.proposition_id AS proposition_b_id,
        'shared_' || a.kind AS reason,
        NULL::DOUBLE AS embedding_similarity,
        NULL::INTEGER AS embedding_rank
    FROM structural_memberships a
    JOIN structural_memberships b
      ON a.kind = b.kind
     AND a.group_key = b.group_key
     AND a.proposition_id < b.proposition_id

    UNION ALL

    SELECT
        proposition_a_id,
        proposition_b_id,
        'embedding_top_k' AS reason,
        embedding_similarity,
        embedding_rank
    FROM embedding_reasons
),
base_pairs AS (
    SELECT DISTINCT proposition_a_id, proposition_b_id
    FROM raw_reasons
),
extra_reasons AS (
    SELECT
        p.proposition_a_id,
        p.proposition_b_id,
        'compatible_predicate' AS reason,
        NULL::DOUBLE AS embedding_similarity,
        NULL::INTEGER AS embedding_rank
    FROM base_pairs p
    JOIN proposition_features a
      ON a.proposition_id = p.proposition_a_id
    JOIN proposition_features b
      ON b.proposition_id = p.proposition_b_id
    WHERE a.predicate IS NOT NULL AND a.predicate = b.predicate

    UNION ALL

    SELECT
        p.proposition_a_id,
        p.proposition_b_id,
        'compatible_unit' AS reason,
        NULL::DOUBLE AS embedding_similarity,
        NULL::INTEGER AS embedding_rank
    FROM base_pairs p
    JOIN proposition_features a
      ON a.proposition_id = p.proposition_a_id
    JOIN proposition_features b
      ON b.proposition_id = p.proposition_b_id
    WHERE a.unit IS NOT NULL AND a.unit = b.unit

    UNION ALL

    SELECT
        p.proposition_a_id,
        p.proposition_b_id,
        'overlapping_dates' AS reason,
        NULL::DOUBLE AS embedding_similarity,
        NULL::INTEGER AS embedding_rank
    FROM base_pairs p
    JOIN proposition_features a
      ON a.proposition_id = p.proposition_a_id
    JOIN proposition_features b
      ON b.proposition_id = p.proposition_b_id
    WHERE a.time_start IS NOT NULL
      AND a.time_end IS NOT NULL
      AND b.time_start IS NOT NULL
      AND b.time_end IS NOT NULL
      AND a.time_start <= b.time_end
      AND b.time_start <= a.time_end
),
aggregated AS (
    SELECT
        proposition_a_id,
        proposition_b_id,
        list_sort(list_distinct(list(reason))) AS candidate_reasons,
        max(embedding_similarity) AS embedding_similarity,
        min(embedding_rank) AS embedding_rank
    FROM (
        SELECT * FROM raw_reasons
        UNION ALL
        SELECT * FROM extra_reasons
    )
    GROUP BY proposition_a_id, proposition_b_id
),
prioritized AS (
    SELECT
        a.*,
        d.proposition_a_id IS NOT NULL AS deterministic
    FROM aggregated a
    LEFT JOIN accepted_deterministic_pairs d USING (
        proposition_a_id,
        proposition_b_id
    )
    ORDER BY
        deterministic DESC,
        len(candidate_reasons) DESC,
        coalesce(embedding_similarity, -1.0) DESC,
        proposition_a_id,
        proposition_b_id
    LIMIT ?
)
SELECT
    proposition_a_id,
    proposition_b_id,
    candidate_reasons,
    embedding_similarity,
    embedding_rank
FROM prioritized
ORDER BY proposition_a_id, proposition_b_id
"""
