from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from oddsfox_graph._discovery.bulk import create_and_fill
from oddsfox_graph._discovery.workspace import (
    CANDIDATE_COLUMNS,
    EMBEDDING_STATE_COLUMNS,
    CandidateStore,
)
from oddsfox_graph._discovery.publication import (
    FinalizedFileHashRegistry,
    publish_directory_atomically,
)
from oddsfox_graph.queries import DuckDB


def _candidate(
    proposition_a_id: str,
    proposition_b_id: str,
    reasons: list[str],
    similarity: float,
    rank: int,
) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(CANDIDATE_COLUMNS)
    row.update(
        {
            "proposition_a_id": proposition_a_id,
            "proposition_b_id": proposition_b_id,
            "candidate_reasons": reasons,
            "embedding_similarity": similarity,
            "embedding_rank": rank,
            "status": "pending",
        }
    )
    return row


def _component_rows(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    store = CandidateStore()
    try:
        create_and_fill(
            store.db,
            "relation_candidates_work",
            CANDIDATE_COLUMNS,
            candidates,
        )
        return store.component_rows(["a", "b", "c"], "candidate-state-v5")
    finally:
        store.close()


def test_bulk_insert_accepts_single_pass_iterables() -> None:
    db = DuckDB()
    try:
        create_and_fill(
            db,
            "streamed_rows",
            {"row_id": "INTEGER", "value": "VARCHAR"},
            ({"row_id": index, "value": str(index)} for index in range(1_025)),
            chunk_size=256,
        )

        assert db.scalar("SELECT count(*) FROM streamed_rows") == 1_025
    finally:
        db.close()


def test_finalized_file_hash_registry_reuses_and_invalidates_hashes(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    published = tmp_path / "published"
    staging.mkdir()
    (staging / "artifact").write_bytes(b"first")
    registry = FinalizedFileHashRegistry(staging)

    first = registry.hash("artifact")
    assert registry.hash("artifact") == first
    assert registry.instrumentation() == {
        "files_read": 1,
        "bytes_read": 5,
        "cache_hits": 1,
        "files_verified": 0,
        "bytes_verified": 0,
    }

    staging.rename(published)
    registry.rebase(published)
    assert registry.hash("artifact") == first
    (published / "artifact").write_bytes(b"second")
    registry.invalidate("artifact")
    assert registry.hash("artifact") != first
    with pytest.raises(ValueError, match="relative"):
        registry.hash("../escape")


def test_component_fingerprints_are_stable_across_databases_and_row_order() -> None:
    candidates = [
        _candidate("a", "b", ["embedding:1", "predicate:x"], 0.875, 1),
        _candidate("a", "c", ["unit:usd"], 0.625, 2),
        _candidate("b", "c", ["event:e"], 0.5, 3),
    ]

    first = _component_rows(candidates)
    second = _component_rows(list(reversed(candidates)))

    assert first == second


def test_component_fingerprint_tracks_pair_membership() -> None:
    candidates = [
        _candidate("a", "b", ["embedding:1"], 0.875, 1),
        _candidate("b", "c", ["event:e"], 0.5, 3),
    ]
    changed = [
        _candidate("a", "b", ["embedding:1"], 0.875, 1),
        _candidate("a", "c", ["event:e"], 0.5, 3),
    ]

    before = {
        row["component_id"]: row["component_fingerprint"]
        for row in _component_rows(candidates)
    }
    after = {
        row["component_id"]: row["component_fingerprint"]
        for row in _component_rows(changed)
    }

    assert before["a"] != after["a"]
    assert before["b"] != after["b"]
    assert before["c"] != after["c"]


def test_component_fingerprint_ignores_scheduling_reason_changes() -> None:
    before = _component_rows([_candidate("a", "b", ["embedding:1"], 0.875, 1)])
    after = _component_rows(
        [
            _candidate(
                "a",
                "b",
                ["embedding:1", "predicate:x"],
                0.9,
                2,
            )
        ]
    )

    assert before == after


def test_inference_queue_streams_bounded_candidate_batches() -> None:
    store = CandidateStore()
    candidates = [
        _candidate(
            f"a-{index:04d}",
            f"b-{index:04d}",
            ["embedding:1"],
            1.0 - index / 10_000,
            index + 1,
        )
        for index in range(1_025)
    ]
    try:
        create_and_fill(
            store.db,
            "relation_candidates_work",
            CANDIDATE_COLUMNS,
            candidates,
        )
        store.prepare_inference_queue(1_000)

        batches = list(store.inference_batches(batch_size=256))

        assert [len(batch) for batch in batches] == [256, 256, 256, 232]
        assert store.instrumentation()["max_materialized_batch_rows"] == 256
    finally:
        store.close()


def test_inference_queue_balances_distinct_event_pair_scopes() -> None:
    store = CandidateStore()
    candidates = [
        _candidate("a1", "a2", ["event:a"], 0.99, 1),
        _candidate("a1", "a3", ["event:a"], 0.98, 2),
        _candidate("a2", "a3", ["event:a"], 0.97, 3),
        _candidate("b1", "b2", ["event:b"], 0.50, 4),
    ]
    try:
        create_and_fill(
            store.db,
            "relation_candidates_work",
            CANDIDATE_COLUMNS,
            candidates,
        )
        store.prepare_inference_queue(
            2,
            {
                "a1": "a|b",
                "a2": "a|b",
                "a3": "a|b",
                "b1": "a",
                "b2": "b|a|b",
            },
        )

        selected = [
            (str(row["proposition_a_id"]), str(row["proposition_b_id"]))
            for batch in store.inference_batches(batch_size=10)
            for row in batch
        ]

        assert set(selected) == {("a1", "a2"), ("b1", "b2")}
    finally:
        store.close()


def test_changed_text_is_not_hidden_by_cross_proposition_vector_reuse() -> None:
    store = CandidateStore()
    try:
        create_and_fill(
            store.db,
            "baseline_proposition_embeddings",
            EMBEDDING_STATE_COLUMNS,
            [
                {
                    "proposition_id": "a",
                    "text_hash": "old-a",
                    "embedding_model": "embedding-model",
                    "embedding_revision": "revision",
                    "embedding": [1.0, 0.0],
                },
                {
                    "proposition_id": "b",
                    "text_hash": "new-a",
                    "embedding_model": "embedding-model",
                    "embedding_revision": "revision",
                    "embedding": [0.0, 1.0],
                },
            ],
        )

        changed = store.changed_embedding_source_ids(
            [
                {"proposition_id": "a", "text_hash": "new-a"},
                {"proposition_id": "b", "text_hash": "new-a"},
            ],
            model="embedding-model",
            revision="revision",
        )

        assert changed == {"a"}
    finally:
        store.close()


def test_atomic_publication_restores_previous_output_on_swap_failure(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    staging = tmp_path / "staging"
    out = tmp_path / "out"
    staging.mkdir()
    out.mkdir()
    (staging / "new").write_text("new", encoding="utf-8")
    (out / "old").write_text("old", encoding="utf-8")

    import oddsfox_graph._discovery.publication as publication

    replace = publication.os.replace
    calls = 0

    def fail_new_swap(source: Any, destination: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated directory swap failure")
        replace(source, destination)

    monkeypatch.setattr(publication.os, "replace", fail_new_swap)

    try:
        publish_directory_atomically(staging, out)
    except OSError as exc:
        assert "simulated directory swap failure" in str(exc)
    else:
        raise AssertionError("publication unexpectedly succeeded")
    assert (out / "old").read_text(encoding="utf-8") == "old"
    assert (staging / "new").read_text(encoding="utf-8") == "new"


def test_atomic_publication_restores_previous_output_on_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "staging"
    out = tmp_path / "out"
    staging.mkdir()
    out.mkdir()
    (staging / "new").write_text("new", encoding="utf-8")
    (out / "old").write_text("old", encoding="utf-8")

    import oddsfox_graph._discovery.publication as publication

    replace = publication.os.replace
    calls = 0

    def interrupt_new_swap(source: Any, destination: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        replace(source, destination)

    monkeypatch.setattr(publication.os, "replace", interrupt_new_swap)

    with pytest.raises(KeyboardInterrupt):
        publish_directory_atomically(staging, out)

    assert (out / "old").read_text(encoding="utf-8") == "old"
    assert (staging / "new").read_text(encoding="utf-8") == "new"


def test_atomic_publication_requires_completion_marker_before_swap(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    out = tmp_path / "out"
    staging.mkdir()
    out.mkdir()
    (staging / "new").write_text("new", encoding="utf-8")
    (out / "old").write_text("old", encoding="utf-8")

    with pytest.raises(ValueError, match="staging is incomplete"):
        publish_directory_atomically(
            staging,
            out,
            completion_marker="build_manifest.json",
        )

    assert (out / "old").read_text(encoding="utf-8") == "old"
    assert (staging / "new").read_text(encoding="utf-8") == "new"


def test_atomic_publication_is_finalized_explicitly(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    out = tmp_path / "out"
    staging.mkdir()
    out.mkdir()
    (staging / "new").write_text("new", encoding="utf-8")
    (out / "old").write_text("old", encoding="utf-8")

    swap = publish_directory_atomically(staging, out)
    assert swap.backup is not None and (swap.backup / "old").is_file()

    swap.finalize()

    assert (out / "new").read_text(encoding="utf-8") == "new"
    assert swap.backup is not None and not swap.backup.exists()


def test_atomic_publication_preserves_existing_file_target(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    out = tmp_path / "out"
    staging.mkdir()
    out.write_text("preserve", encoding="utf-8")

    try:
        publish_directory_atomically(staging, out)
    except ValueError as exc:
        assert "must be a directory path" in str(exc)
    else:
        raise AssertionError("publication unexpectedly replaced a file")

    assert out.read_text(encoding="utf-8") == "preserve"
    assert staging.is_dir()
