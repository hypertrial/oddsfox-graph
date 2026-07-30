from __future__ import annotations

import hashlib
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
    "operator": "VARCHAR",
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
    baseline_embeddings: dict[str, list[float]] | None = None,
    baseline_neighbors: Sequence[dict[str, Any]] | None = None,
    embedding_state_sink: list[dict[str, Any]] | None = None,
    neighbor_state_sink: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Generate capped candidates without materializing structural all-pairs in Python."""

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
                "operator": proposition.get("operator"),
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
                    if key not in {"operator", "threshold"}
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
                    if key not in {"operator", "threshold"}
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
                        hashable(
                            event_key or proposition.get("event_scope")
                        ),
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
        baseline_embeddings=baseline_embeddings,
        baseline_neighbors=baseline_neighbors,
        embedding_state_sink=embedding_state_sink,
        neighbor_state_sink=neighbor_state_sink,
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
            if relation is not None:
                deterministic_relations[(a_id, b_id)] = relation
        deterministic_pair_count = len(deterministic_relations)
        if deterministic_pair_count > int(config.max_candidates):
            raise ValueError(
                f"Deterministic rules produced {deterministic_pair_count} candidates, "
                f"exceeding max_candidates={config.max_candidates}; refusing to "
                "truncate proven relations"
            )

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
            "rule_id": None,
            "rule_status": None,
            "classification_relation": None,
            "classification_confidence": None,
            "supporting_fields": None,
            "a_implies_b": None,
            "b_implies_a": None,
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
            row["rule_id"] = relation.get("rule_id")
            row["rule_status"] = "enabled"
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
    *,
    baseline_embeddings: dict[str, list[float]] | None = None,
    baseline_neighbors: Sequence[dict[str, Any]] | None = None,
    embedding_state_sink: list[dict[str, Any]] | None = None,
    neighbor_state_sink: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - dependency installation guard
        raise ImportError(
            'Automated discovery requires `pip install -e ".[discovery]"`.'
        ) from exc

    texts = [embedding_text(proposition_by_id[proposition_id]) for proposition_id in ids]
    text_hashes = [
        hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts
    ]
    baseline_embeddings = baseline_embeddings or {}
    vectors: list[Any | None] = [
        baseline_embeddings.get(text_hash) for text_hash in text_hashes
    ]
    missing_indices = [
        index for index, vector in enumerate(vectors) if vector is None
    ]
    if missing_indices:
        encoded = np.asarray(
            embedder([texts[index] for index in missing_indices], config),
            dtype=np.float32,
        )
        if (
            encoded.ndim != 2
            or encoded.shape[0] != len(missing_indices)
            or encoded.shape[1] == 0
        ):
            raise ValueError("Embedding model returned an invalid matrix shape")
        encoded_norms = np.linalg.norm(encoded, axis=1, keepdims=True)
        encoded = encoded / np.maximum(encoded_norms, 1e-12)
        for encoded_index, proposition_index in enumerate(missing_indices):
            vectors[proposition_index] = encoded[encoded_index]
    matrix = np.asarray(vectors, dtype=np.float32)
    if (
        matrix.ndim != 2
        or matrix.shape[0] != len(ids)
        or matrix.shape[1] == 0
        or not np.isfinite(matrix).all()
    ):
        raise ValueError("Embedding model returned an invalid matrix shape")
    if embedding_state_sink is not None:
        missing_index_set = set(missing_indices)
        embedding_state_sink.extend(
            {
                "proposition_id": proposition_id,
                "text_hash": text_hash,
                "embedding_model": str(config.embedding_model),
                "embedding_revision": str(config.embedding_revision),
                "embedding": matrix[index].astype(float).tolist(),
                "reused": index not in missing_index_set,
            }
            for index, (proposition_id, text_hash) in enumerate(
                zip(ids, text_hashes, strict=True)
            )
        )
    rows = []
    block_size = int(getattr(config, "embedding_block_size", 512))
    neighbor_count = min(int(config.top_k), len(ids) - 1)
    baseline_by_source: dict[str, list[dict[str, Any]]] = {}
    for row in baseline_neighbors or []:
        baseline_by_source.setdefault(
            str(row["proposition_id"]),
            [],
        ).append(row)
    can_reuse_neighbors = bool(baseline_by_source)
    id_to_index = {
        proposition_id: index for index, proposition_id in enumerate(ids)
    }
    changed_ids = {
        ids[index] for index in missing_indices
    } | (set(ids) - set(baseline_by_source))

    def emit(
        proposition_id: str,
        ranked_neighbors: Sequence[tuple[str, float]],
    ) -> None:
        for rank, (other_id, similarity) in enumerate(
            ranked_neighbors,
            start=1,
        ):
            a_id, b_id = sorted((proposition_id, other_id))
            rows.append(
                {
                    "proposition_a_id": a_id,
                    "proposition_b_id": b_id,
                    "embedding_similarity": similarity,
                    "embedding_rank": rank,
                }
            )
            if neighbor_state_sink is not None:
                neighbor_state_sink.append(
                    {
                        "proposition_id": proposition_id,
                        "neighbor_id": other_id,
                        "similarity": similarity,
                        "neighbor_rank": rank,
                        "proposition_text_hash": text_hashes[
                            id_to_index[proposition_id]
                        ],
                        "neighbor_text_hash": text_hashes[
                            id_to_index[other_id]
                        ],
                        "embedding_model": str(config.embedding_model),
                        "embedding_revision": str(config.embedding_revision),
                    }
                )

    if can_reuse_neighbors:
        for proposition_id in ids:
            index = id_to_index[proposition_id]
            prior = sorted(
                baseline_by_source.get(proposition_id, []),
                key=lambda row: int(row["neighbor_rank"]),
            )
            prior_valid = (
                len(prior) == neighbor_count
                and all(
                    str(row["neighbor_id"]) in id_to_index
                    and str(row["proposition_text_hash"])
                    == text_hashes[index]
                    and str(row["neighbor_text_hash"])
                    == text_hashes[
                        id_to_index[str(row["neighbor_id"])]
                    ]
                    for row in prior
                )
            )
            must_recompute_full = (
                proposition_id in changed_ids
                or not prior_valid
                or any(
                    str(row["neighbor_id"]) in changed_ids for row in prior
                )
            )
            if must_recompute_full:
                scores = np.round(matrix[index] @ matrix.T, decimals=6)
                scores[index] = -np.inf
                ranked_indices = np.argsort(
                    -scores,
                    kind="stable",
                )[:neighbor_count]
                ranked_neighbors = [
                    (ids[int(other_index)], float(scores[int(other_index)]))
                    for other_index in ranked_indices
                ]
            else:
                options = {
                    str(row["neighbor_id"]): float(row["similarity"])
                    for row in prior
                }
                for changed_id in changed_ids:
                    if changed_id == proposition_id:
                        continue
                    options[changed_id] = float(
                        np.round(
                            matrix[index] @ matrix[id_to_index[changed_id]],
                            decimals=6,
                        )
                    )
                ranked_neighbors = sorted(
                    options.items(),
                    key=lambda item: (-item[1], item[0]),
                )[:neighbor_count]
            emit(proposition_id, ranked_neighbors)
    else:
        for block_start in range(0, len(ids), block_size):
            block_end = min(block_start + block_size, len(ids))
            similarities = np.round(
                matrix[block_start:block_end] @ matrix.T,
                decimals=6,
            )
            for block_offset, index in enumerate(
                range(block_start, block_end)
            ):
                proposition_id = ids[index]
                scores = similarities[block_offset]
                scores[index] = -np.inf
                ranked = np.argsort(
                    -scores,
                    kind="stable",
                )[:neighbor_count]
                emit(
                    proposition_id,
                    [
                        (
                            ids[int(other_index)],
                            float(scores[int(other_index)]),
                        )
                        for other_index in ranked
                    ],
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
        AND (
            a.numeric_value != b.numeric_value
            OR a.operator != b.operator
        )
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
