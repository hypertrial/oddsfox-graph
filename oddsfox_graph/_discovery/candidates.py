from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .bulk import create_and_fill
from .provenance import text_sha256
from .workspace import CandidateStore
from .versions import CANDIDATE_STATE_VERSION
from ..queries import q


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

_FEATURE_COLUMNS = {
    "proposition_id": "VARCHAR",
    "predicate": "VARCHAR",
    "unit": "VARCHAR",
    "time_start": "TIMESTAMPTZ",
    "time_end": "TIMESTAMPTZ",
}

SIMILARITY_DECIMALS = 4

_DETERMINISTIC_RELATION_COLUMNS = {
    "proposition_a_id": "VARCHAR",
    "proposition_b_id": "VARCHAR",
    "edge_type": "VARCHAR",
    "rule_id": "VARCHAR",
    "explanation": "VARCHAR",
}


def candidate_sort_key(row: dict[str, Any]) -> tuple[object, ...]:
    similarity = row["embedding_similarity"]
    nli_signal = max(
        (
            float(row.get(field) or 0.0)
            for field in (
                "nli_a_to_b_entailment",
                "nli_a_to_b_contradiction",
                "nli_b_to_a_entailment",
                "nli_b_to_a_contradiction",
            )
        ),
        default=0.0,
    )
    return (
        -len(row["candidate_reasons"]),
        -nli_signal,
        -float(similarity if similarity is not None else -1.0),
        str(row["proposition_a_id"]),
        str(row["proposition_b_id"]),
    )


def structural_member_limit(max_candidates: int) -> int:
    return max(64, int(math.sqrt(2 * int(max_candidates))) + 1)


def generate_candidate_store(
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
    baseline_embedding_path: Path | None = None,
    baseline_neighbor_path: Path | None = None,
    neighborhood_execution_sink: list[dict[str, Any]] | None = None,
    baseline_candidate_blocks: Any | None = None,
    baseline_candidate_reasons: Any | None = None,
    baseline_neighborhood_fingerprints: dict[str, str] | None = None,
    enabled_rule_ids: set[str] | None = None,
) -> CandidateStore:
    """Generate candidates into a disk-backed relational working set."""

    proposition_by_id = {
        str(proposition["proposition_id"]): proposition
        for proposition in propositions
    }
    ids = sorted(proposition_by_id)
    structural_memberships: list[dict[str, Any]] = []
    deterministic_memberships: list[dict[str, Any]] = []

    def rule_enabled(rule_id: str) -> bool:
        return enabled_rule_ids is None or rule_id in enabled_rule_ids

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
        if (
            rule_enabled("same_market.binary_complement.v1")
            or rule_enabled("same_market.categorical_exclusion.v1")
        ):
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
            proposition.get("parse_status") == "parsed"
            and
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
            if rule_enabled("equivalence.normalized_fields.v1"):
                add_deterministic(
                    "signature",
                    signature,
                    proposition_id,
                    proposition,
                )
            if (
                rule_enabled("threshold.interval_containment.v2")
                and
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
            if (
                rule_enabled("time.interval_containment.v1")
                and proposition.get("time_start")
                and proposition.get("time_end")
            ):
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
            if (
                rule_enabled("tournament.stage_progression.v1")
                and rank is not None
                and proposition.get("polarity") == "positive"
            ):
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
                rule_enabled("event.single_winner.v1")
                and
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

    store = CandidateStore()
    store.initialize_semantic_state()
    if baseline_embedding_path is not None or baseline_neighbor_path is not None:
        if baseline_embedding_path is None or baseline_neighbor_path is None:
            raise ValueError("Incremental semantic state requires both baseline paths")
        store.load_baseline_semantic_state(
            embedding_path=baseline_embedding_path,
            neighbor_path=baseline_neighbor_path,
        )
    _embedding_reason_rows(
        ids,
        proposition_by_id,
        config,
        embedder,
        embedding_text,
        baseline_state_available=baseline_embedding_path is not None,
        neighborhood_execution_sink=neighborhood_execution_sink,
        baseline_neighborhood_fingerprints=baseline_neighborhood_fingerprints,
        state_store=store,
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

    db = store.db
    try:
        create_and_fill(
            db,
            "structural_memberships",
            _MEMBERSHIP_COLUMNS,
            structural_memberships,
        )
        db.execute(
            _CREATE_CANDIDATE_BLOCKS_SQL,
            [CANDIDATE_STATE_VERSION],
        )
        create_and_fill(
            db,
            "deterministic_memberships",
            _DETERMINISTIC_MEMBERSHIP_COLUMNS,
            deterministic_memberships,
        )
        db.execute(
            """
            CREATE TABLE directed_embedding_neighbors AS
            SELECT proposition_id, neighbor_id, similarity, neighbor_rank
            FROM semantic_neighbors_work
            """
        )
        db.execute(
            """
            CREATE TABLE embedding_reasons AS
            SELECT
                least(proposition_id, neighbor_id) AS proposition_a_id,
                greatest(proposition_id, neighbor_id) AS proposition_b_id,
                max(similarity) AS embedding_similarity,
                min(neighbor_rank)::INTEGER AS embedding_rank
            FROM directed_embedding_neighbors
            GROUP BY 1, 2
            """
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
            if (
                relation is not None
                and (
                    enabled_rule_ids is None
                    or relation.get("rule_id") in enabled_rule_ids
                )
            ):
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
            _DETERMINISTIC_RELATION_COLUMNS,
            [
                {
                    "proposition_a_id": pair[0],
                    "proposition_b_id": pair[1],
                    "edge_type": relation["edge_type"],
                    "rule_id": relation.get("rule_id"),
                    "explanation": relation["explanation"],
                }
                for pair, relation in deterministic_relations.items()
            ],
        )
        member_limit = structural_member_limit(int(config.max_candidates))
        store.structural_member_limit = member_limit
        _create_candidate_reason_state(
            db,
            member_limit,
            baseline_candidate_blocks,
            baseline_candidate_reasons,
        )
        db.execute(
            _CREATE_CANDIDATE_STORE_SQL,
            [
                int(config.max_candidates),
                int(config.max_candidates),
            ],
        )
        return store
    except Exception:
        store.close()
        raise


def _embedding_reason_rows(
    ids: list[str],
    proposition_by_id: dict[str, dict[str, Any]],
    config: Any,
    embedder: Callable[[list[str], Any], Any],
    embedding_text: Callable[[dict[str, Any]], str],
    *,
    baseline_state_available: bool = False,
    neighborhood_execution_sink: list[dict[str, Any]] | None = None,
    baseline_neighborhood_fingerprints: dict[str, str] | None = None,
    state_store: CandidateStore | None = None,
) -> None:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - installation guard
        raise ImportError(
            "Automated discovery dependencies are missing; "
            "reinstall oddsfox-graph."
        ) from exc
    if state_store is None:
        raise ValueError("Embedding retrieval requires a candidate workspace")

    texts = [
        embedding_text(proposition_by_id[proposition_id])
        for proposition_id in ids
    ]
    text_hashes = [
        text_sha256(text) for text in texts
    ]
    encode_block_size = 512
    matrix_path = state_store.directory / "embedding-matrix.f32"
    matrix: Any | None = None
    dimension: int | None = None
    reused_indices: set[int] = set()

    for block_start in range(0, len(ids), encode_block_size):
        block_end = min(block_start + encode_block_size, len(ids))
        block_hashes = text_hashes[block_start:block_end]
        reused_by_hash: dict[str, Any] = {}
        if baseline_state_available:
            reused_by_hash.update(
                {
                    str(row["text_hash"]): row["embedding"]
                    for row in state_store.baseline_embeddings(
                        block_hashes,
                        model=str(config.embedding_model),
                        revision=str(config.embedding_revision),
                    )
                }
            )
        block_vectors: list[Any | None] = [
            reused_by_hash.get(text_hash) for text_hash in block_hashes
        ]
        missing_offsets = [
            offset
            for offset, vector in enumerate(block_vectors)
            if vector is None
        ]
        if missing_offsets:
            encoded = np.asarray(
                embedder(
                    [texts[block_start + offset] for offset in missing_offsets],
                    config,
                ),
                dtype=np.float32,
            )
            if (
                encoded.ndim != 2
                or encoded.shape[0] != len(missing_offsets)
                or encoded.shape[1] == 0
            ):
                raise ValueError(
                    "Embedding model returned an invalid matrix shape"
                )
            encoded_norms = np.linalg.norm(
                encoded,
                axis=1,
                keepdims=True,
            )
            encoded = encoded / np.maximum(encoded_norms, 1e-12)
            for encoded_index, offset in enumerate(missing_offsets):
                block_vectors[offset] = encoded[encoded_index]
        block_matrix = np.asarray(block_vectors, dtype=np.float32)
        if (
            block_matrix.ndim != 2
            or block_matrix.shape[0] != block_end - block_start
            or block_matrix.shape[1] == 0
            or not np.isfinite(block_matrix).all()
        ):
            raise ValueError("Embedding model returned an invalid matrix shape")
        if dimension is None:
            dimension = int(block_matrix.shape[1])
            matrix = np.memmap(
                matrix_path,
                mode="w+",
                dtype=np.float32,
                shape=(len(ids), dimension),
            )
        elif int(block_matrix.shape[1]) != dimension:
            raise ValueError("Embedding dimensions changed within one run")
        assert matrix is not None
        matrix[block_start:block_end] = block_matrix
        reused_offsets = set(range(block_end - block_start)) - set(
            missing_offsets
        )
        reused_indices.update(
            block_start + offset for offset in reused_offsets
        )
        block_rows = [
            {
                "proposition_id": ids[index],
                "text_hash": text_hashes[index],
                "embedding_model": str(config.embedding_model),
                "embedding_revision": str(config.embedding_revision),
                "embedding": block_matrix[index - block_start]
                .astype(float)
                .tolist(),
                "reused": index in reused_indices,
            }
            for index in range(block_start, block_end)
        ]
        state_store.append_embedding_state(block_rows)

    if matrix is None:
        return
    matrix.flush()
    state_store.embedding_vectors_reused = len(reused_indices)
    state_store.embedding_vectors_recomputed = len(ids) - len(reused_indices)

    baseline_source_ids: set[str] = set()
    changed_source_text_ids: set[str] = set()
    if baseline_state_available:
        baseline_source_ids.update(
            state_store.baseline_neighbor_source_ids(
                ids,
                model=str(config.embedding_model),
                revision=str(config.embedding_revision),
            )
        )
        changed_source_text_ids = state_store.changed_embedding_source_ids(
            [
                {
                    "proposition_id": proposition_id,
                    "text_hash": text_hashes[index],
                }
                for index, proposition_id in enumerate(ids)
            ],
            model=str(config.embedding_model),
            revision=str(config.embedding_revision),
        )
    can_reuse_neighbors = bool(baseline_source_ids)
    id_to_index = {
        proposition_id: index for index, proposition_id in enumerate(ids)
    }
    changed_ids = {
        ids[index]
        for index in range(len(ids))
        if index not in reused_indices
    } | (set(ids) - baseline_source_ids) | changed_source_text_ids
    block_size = int(getattr(config, "embedding_block_size", 512))
    neighbor_count = min(int(config.top_k), len(ids) - 1)
    neighbor_buffer: list[dict[str, Any]] = []
    reusable_source_ids: set[str] = set()
    reusable_boundaries: dict[str, tuple[float, str]] = {}
    if (
        baseline_state_available
        and baseline_neighborhood_fingerprints is not None
        and (set(ids) - changed_ids) <= set(baseline_neighborhood_fingerprints)
    ):
        reusable_boundaries = state_store.valid_baseline_neighbor_boundaries(
            [
                {
                    "proposition_id": proposition_id,
                    "text_hash": text_hashes[index],
                }
                for index, proposition_id in enumerate(ids)
            ],
            changed_ids,
            neighbor_count=neighbor_count,
            model=str(config.embedding_model),
            revision=str(config.embedding_revision),
        )

    def flush_neighbors() -> None:
        if neighbor_buffer:
            state_store.append_semantic_neighbors(neighbor_buffer)
            neighbor_buffer.clear()

    def emit(
        proposition_id: str,
        ranked_neighbors: Sequence[tuple[str, float]],
        *,
        status: str,
    ) -> None:
        for rank, (other_id, similarity) in enumerate(
            ranked_neighbors,
            start=1,
        ):
            neighbor_row = {
                "proposition_id": proposition_id,
                "neighbor_id": other_id,
                "similarity": similarity,
                "neighbor_rank": rank,
                "proposition_text_hash": text_hashes[
                    id_to_index[proposition_id]
                ],
                "neighbor_text_hash": text_hashes[id_to_index[other_id]],
                "embedding_model": str(config.embedding_model),
                "embedding_revision": str(config.embedding_revision),
            }
            neighbor_buffer.append(neighbor_row)
            if len(neighbor_buffer) >= 10_000:
                flush_neighbors()
        if neighborhood_execution_sink is not None:
            fingerprint_payload = "|".join(
                f"{neighbor_id}:{similarity}:{rank}"
                for rank, (neighbor_id, similarity) in sorted(
                    enumerate(ranked_neighbors, start=1),
                    key=lambda item: (
                        item[1][0],
                        item[1][1],
                        item[0],
                    ),
                )
            )
            neighborhood_execution_sink.append(
                {
                    "proposition_id": proposition_id,
                    "status": status,
                    "neighborhood_fingerprint": text_sha256(
                        fingerprint_payload
                    ),
                }
            )

    if can_reuse_neighbors:
        changed_id_order = sorted(changed_ids)
        changed_indices = [
            id_to_index[changed_id]
            for changed_id in changed_id_order
        ]
        for block_start in range(0, len(ids), block_size):
            block_ids = ids[block_start : block_start + block_size]
            changed_scores = (
                np.round(
                    matrix[
                        block_start : block_start + len(block_ids)
                    ]
                    @ matrix[changed_indices].T,
                    decimals=SIMILARITY_DECIMALS,
                )
                if changed_indices and len(changed_indices) < len(ids)
                else None
            )
            reusable_block_ids: set[str] = set()
            for proposition_id in block_ids:
                boundary = reusable_boundaries.get(proposition_id)
                can_reuse = boundary is not None
                if can_reuse and changed_id_order:
                    assert changed_scores is not None
                    index = id_to_index[proposition_id]
                    changed_options = [
                        (
                            changed_id,
                            float(
                                changed_scores[
                                    index - block_start,
                                    changed_offset,
                                ]
                            ),
                        )
                        for changed_offset, changed_id in enumerate(
                            changed_id_order
                        )
                        if changed_id != proposition_id
                    ]
                    if changed_options:
                        best_changed_id, best_changed_score = min(
                            changed_options,
                            key=lambda item: (-item[1], item[0]),
                        )
                        assert boundary is not None
                        boundary_score, boundary_id = boundary
                        can_reuse = (
                            best_changed_score < boundary_score
                            or (
                                best_changed_score == boundary_score
                                and best_changed_id >= boundary_id
                            )
                        )
                if can_reuse:
                    reusable_block_ids.add(proposition_id)
            reusable_source_ids.update(reusable_block_ids)
            affected_block_ids = [
                proposition_id
                for proposition_id in block_ids
                if proposition_id not in reusable_block_ids
            ]
            prior_by_source: dict[str, list[dict[str, Any]]] = {
                proposition_id: []
                for proposition_id in affected_block_ids
            }
            if baseline_state_available and affected_block_ids:
                for row in state_store.baseline_neighbors(
                    affected_block_ids,
                    model=str(config.embedding_model),
                    revision=str(config.embedding_revision),
                ):
                    prior_by_source.setdefault(
                        str(row["proposition_id"]),
                        [],
                    ).append(row)
            for proposition_id in block_ids:
                if proposition_id in reusable_block_ids:
                    if neighborhood_execution_sink is not None:
                        assert baseline_neighborhood_fingerprints is not None
                        neighborhood_execution_sink.append(
                            {
                                "proposition_id": proposition_id,
                                "status": "reused",
                                "neighborhood_fingerprint": (
                                    baseline_neighborhood_fingerprints[
                                        proposition_id
                                    ]
                                ),
                            }
                        )
                    continue
                index = id_to_index[proposition_id]
                prior = sorted(
                    prior_by_source.get(proposition_id, []),
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
                recompute_full = (
                    proposition_id in changed_ids
                    or not prior_valid
                    or any(
                        str(row["neighbor_id"]) in changed_ids
                        for row in prior
                    )
                )
                if recompute_full:
                    scores = np.round(
                        matrix[index] @ matrix.T,
                        decimals=SIMILARITY_DECIMALS,
                    )
                    scores[index] = -np.inf
                    ranked_indices = _top_k_indices(
                        scores,
                        neighbor_count,
                        np,
                    )
                    ranked_neighbors = [
                        (
                            ids[int(other_index)],
                            float(scores[int(other_index)]),
                        )
                        for other_index in ranked_indices
                    ]
                    status = "recomputed"
                else:
                    prior_neighbors = [
                        (str(row["neighbor_id"]), float(row["similarity"]))
                        for row in prior
                    ]
                    options = dict(prior_neighbors)
                    for changed_offset, changed_id in enumerate(
                        changed_id_order
                    ):
                        if changed_id == proposition_id:
                            continue
                        assert changed_scores is not None
                        options[changed_id] = float(
                            changed_scores[
                                index - block_start,
                                changed_offset,
                            ]
                        )
                    ranked_neighbors = sorted(
                        options.items(),
                        key=lambda item: (-item[1], item[0]),
                    )[:neighbor_count]
                    status = (
                        "reused"
                        if ranked_neighbors == prior_neighbors
                        else "recomputed"
                    )
                emit(
                    proposition_id,
                    ranked_neighbors,
                    status=status,
                )
        state_store.copy_baseline_neighbors(
            reusable_source_ids,
            model=str(config.embedding_model),
            revision=str(config.embedding_revision),
        )
    else:
        for block_start in range(0, len(ids), block_size):
            block_end = min(block_start + block_size, len(ids))
            similarities = np.round(
                matrix[block_start:block_end] @ matrix.T,
                decimals=SIMILARITY_DECIMALS,
            )
            for block_offset, index in enumerate(
                range(block_start, block_end)
            ):
                scores = similarities[block_offset]
                scores[index] = -np.inf
                ranked = _top_k_indices(scores, neighbor_count, np)
                emit(
                    ids[index],
                    [
                        (
                            ids[int(other_index)],
                            float(scores[int(other_index)]),
                        )
                        for other_index in ranked
                    ],
                    status="recomputed",
                )
    flush_neighbors()
    matrix.flush()
    del matrix
    matrix_path.unlink(missing_ok=True)


def _top_k_indices(scores: Any, count: int, np: Any) -> Any:
    """Return exact score-descending, stable-index top-k without a full sort."""
    if count <= 0:
        return np.asarray([], dtype=np.int64)
    if count >= len(scores):
        candidates = np.arange(len(scores), dtype=np.int64)
    else:
        partition = np.argpartition(-scores, count - 1)[:count]
        boundary = float(np.min(scores[partition]))
        above = np.flatnonzero(scores > boundary)
        needed = count - len(above)
        equal = np.flatnonzero(scores == boundary)[:needed]
        candidates = np.concatenate((above, equal))
    return np.asarray(
        sorted(
            (int(index) for index in candidates),
            key=lambda index: (-float(scores[index]), index),
        ),
        dtype=np.int64,
    )


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


_CREATE_CANDIDATE_BLOCKS_SQL = """
CREATE TABLE candidate_blocks_work AS
SELECT
    sha256(kind || ':' || group_key) AS block_id,
    kind AS reason_kind,
    group_key,
    sha256(string_agg(proposition_id, '|' ORDER BY proposition_id))
        AS member_fingerprint,
    count(*)::INTEGER AS member_count,
    ?::VARCHAR AS candidate_version
FROM structural_memberships
GROUP BY kind, group_key
"""

_STRUCTURAL_CONTRIBUTION_SQL = """
WITH ranked_memberships AS (
    SELECT
        *,
        row_number() OVER (
            PARTITION BY kind, group_key
            ORDER BY proposition_id
        ) AS member_rank
    FROM structural_memberships
),
bounded_memberships AS (
    SELECT
        kind,
        group_key,
        proposition_id,
        sha256(kind || ':' || group_key) AS block_id
    FROM ranked_memberships
    WHERE member_rank <= ?
),
recomputed_memberships AS (
    SELECT m.*
    FROM bounded_memberships m
    JOIN candidate_block_execution_work e USING (block_id)
    WHERE e.status = 'recomputed'
)
SELECT
    a.block_id,
    a.proposition_id AS proposition_a_id,
    b.proposition_id AS proposition_b_id,
    'shared_' || a.kind AS reason,
    NULL::DOUBLE AS embedding_similarity,
    NULL::INTEGER AS embedding_rank,
    ?::VARCHAR AS candidate_version
FROM recomputed_memberships a
JOIN recomputed_memberships b
  ON a.block_id = b.block_id
 AND a.proposition_id < b.proposition_id
"""


def _create_candidate_reason_state(
    db: Any,
    member_limit: int,
    baseline_candidate_blocks: Any | None,
    baseline_candidate_reasons: Any | None,
) -> None:
    if baseline_candidate_blocks is None or baseline_candidate_reasons is None:
        db.execute(
            """
            CREATE TABLE candidate_block_execution_work AS
            SELECT
                block_id,
                'recomputed'::VARCHAR AS status,
                NULL::VARCHAR AS input_fingerprint,
                member_fingerprint AS output_fingerprint
            FROM candidate_blocks_work
            """
        )
        db.execute(
            "CREATE TABLE candidate_reason_rows_work AS "
            + _STRUCTURAL_CONTRIBUTION_SQL,
            [member_limit, CANDIDATE_STATE_VERSION],
        )
        return
    block_path = q(baseline_candidate_blocks)
    reason_path = q(baseline_candidate_reasons)
    db.execute(
        f"""
        CREATE TABLE candidate_block_execution_work AS
        WITH prior AS (
            SELECT block_id, member_fingerprint
            FROM read_parquet('{block_path}')
            WHERE candidate_version = '{CANDIDATE_STATE_VERSION}'
        )
        SELECT
            coalesce(c.block_id, p.block_id) AS block_id,
            CASE
                WHEN c.block_id IS NULL THEN 'removed'
                WHEN p.member_fingerprint = c.member_fingerprint THEN 'reused'
                ELSE 'recomputed'
            END AS status,
            p.member_fingerprint AS input_fingerprint,
            c.member_fingerprint AS output_fingerprint
        FROM candidate_blocks_work c
        FULL OUTER JOIN prior p USING (block_id)
        """
    )
    db.execute(
        "CREATE TABLE recomputed_candidate_reason_rows AS "
        + _STRUCTURAL_CONTRIBUTION_SQL,
        [member_limit, CANDIDATE_STATE_VERSION],
    )
    db.execute(
        f"""
        CREATE TABLE candidate_reason_rows_work AS
        SELECT r.*
        FROM read_parquet('{reason_path}') r
        JOIN candidate_block_execution_work e USING (block_id)
        WHERE e.status = 'reused'
        UNION ALL
        SELECT * FROM recomputed_candidate_reason_rows
        """
    )
    db.execute("DROP TABLE recomputed_candidate_reason_rows")


_CREATE_CANDIDATE_STORE_SQL = """
CREATE TABLE relation_candidates_work AS
WITH
bounded_structural_reasons AS (
    SELECT
        proposition_a_id,
        proposition_b_id,
        reason,
        embedding_similarity,
        embedding_rank
    FROM candidate_reason_rows_work
    QUALIFY row_number() OVER (
        PARTITION BY reason
        ORDER BY proposition_a_id, proposition_b_id, block_id
    ) <= ?
),
raw_reasons AS (
    SELECT * FROM bounded_structural_reasons

    UNION ALL

    SELECT
        proposition_a_id,
        proposition_b_id,
        'embedding_top_k' AS reason,
        embedding_similarity,
        embedding_rank
    FROM embedding_reasons

    UNION ALL

    SELECT
        proposition_a_id,
        proposition_b_id,
        'deterministic_rule' AS reason,
        NULL::DOUBLE AS embedding_similarity,
        NULL::INTEGER AS embedding_rank
    FROM accepted_deterministic_pairs
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
    p.proposition_a_id,
    p.proposition_b_id,
    p.candidate_reasons,
    p.embedding_similarity,
    p.embedding_rank,
    d.edge_type AS deterministic_relation,
    d.rule_id,
    CASE WHEN d.rule_id IS NULL THEN NULL ELSE 'enabled' END AS rule_status,
    NULL::VARCHAR AS classification_relation,
    NULL::DOUBLE AS classification_confidence,
    NULL::VARCHAR AS primary_relation,
    NULL::DOUBLE AS primary_confidence,
    NULL::VARCHAR AS verifier_relation,
    NULL::DOUBLE AS verifier_confidence,
    NULL::VARCHAR AS consensus_status,
    NULL::BOOLEAN AS a_implies_b,
    NULL::BOOLEAN AS b_implies_a,
    d.explanation,
    NULL::DOUBLE AS nli_a_to_b_entailment,
    NULL::DOUBLE AS nli_a_to_b_contradiction,
    NULL::DOUBLE AS nli_a_to_b_neutral,
    NULL::DOUBLE AS nli_b_to_a_entailment,
    NULL::DOUBLE AS nli_b_to_a_contradiction,
    NULL::DOUBLE AS nli_b_to_a_neutral,
    NULL::VARCHAR AS nli_action,
    CASE WHEN d.rule_id IS NULL THEN 'pending' ELSE 'accepted' END AS status,
    CASE WHEN d.rule_id IS NULL THEN NULL ELSE 'deterministic' END
        AS discovery_method,
    NULL::VARCHAR AS prompt_version,
    NULL::VARCHAR AS primary_model_version,
    NULL::VARCHAR AS verifier_model_version,
    NULL::VARCHAR AS primary_assessment_id,
    NULL::VARCHAR AS verifier_assessment_id,
    NULL::VARCHAR AS primary_inference_fingerprint,
    NULL::VARCHAR AS verifier_inference_fingerprint,
    NULL::VARCHAR AS consensus_fingerprint,
    NULL::VARCHAR AS automation_profile_id
FROM prioritized p
LEFT JOIN accepted_deterministic_pairs d USING (
    proposition_a_id,
    proposition_b_id
)
"""
