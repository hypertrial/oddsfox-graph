from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .candidates import generate_candidate_store
from .relations import (
    SEMANTIC_KEYS,
    deterministic_relation,
    hashable,
    is_winner_proposition,
    proposition_signature,
    stage_rank,
)
from .workspace import CandidateStore


def embedding_text(proposition: dict[str, Any]) -> str:
    parts = [
        " ".join(proposition.get("subject") or []),
        proposition.get("predicate"),
        proposition.get("object"),
        proposition.get("operator"),
        proposition.get("threshold"),
        proposition.get("unit"),
        proposition.get("competition"),
        proposition.get("event_scope"),
        proposition.get("jurisdiction"),
        proposition.get("outcome"),
        proposition.get("question"),
        proposition.get("description"),
    ]
    return " | ".join(
        str(part)
        for part in parts
        if part not in (None, "", [])
    )


def generate_candidate_workspace(
    propositions: Sequence[dict[str, Any]],
    config: Any,
    embedder: Callable[[list[str], Any], Any],
    *,
    baseline_embeddings: dict[str, list[float]] | None = None,
    baseline_neighbors: Sequence[dict[str, Any]] | None = None,
    baseline_embedding_path: Path | None = None,
    baseline_neighbor_path: Path | None = None,
    embedding_state_sink: list[dict[str, Any]] | None = None,
    neighbor_state_sink: list[dict[str, Any]] | None = None,
    neighborhood_execution_sink: list[dict[str, Any]] | None = None,
    baseline_candidate_blocks: Path | None = None,
    baseline_candidate_reasons: Path | None = None,
    baseline_neighborhood_fingerprints: dict[str, str] | None = None,
    enabled_rule_ids: set[str] | None = None,
) -> CandidateStore:
    return generate_candidate_store(
        propositions,
        config,
        embedder,
        semantic_keys=SEMANTIC_KEYS,
        hashable=hashable,
        proposition_signature=proposition_signature,
        deterministic_relation=deterministic_relation,
        embedding_text=embedding_text,
        stage_rank=stage_rank,
        is_winner=is_winner_proposition,
        baseline_embeddings=baseline_embeddings,
        baseline_neighbors=baseline_neighbors,
        baseline_embedding_path=baseline_embedding_path,
        baseline_neighbor_path=baseline_neighbor_path,
        embedding_state_sink=embedding_state_sink,
        neighbor_state_sink=neighbor_state_sink,
        neighborhood_execution_sink=neighborhood_execution_sink,
        baseline_candidate_blocks=baseline_candidate_blocks,
        baseline_candidate_reasons=baseline_candidate_reasons,
        baseline_neighborhood_fingerprints=(
            baseline_neighborhood_fingerprints
        ),
        enabled_rule_ids=enabled_rule_ids,
    )
