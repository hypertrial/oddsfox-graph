from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from collections.abc import Iterator, Sequence
from typing import Any, cast

from .bulk import create_and_fill, insert_rows
from ..queries import DuckDB, q


CANDIDATE_COLUMNS = {
    "proposition_a_id": "VARCHAR",
    "proposition_b_id": "VARCHAR",
    "candidate_reasons": "VARCHAR[]",
    "embedding_similarity": "DOUBLE",
    "embedding_rank": "INTEGER",
    "deterministic_relation": "VARCHAR",
    "rule_id": "VARCHAR",
    "rule_status": "VARCHAR",
    "classification_relation": "VARCHAR",
    "classification_confidence": "DOUBLE",
    "primary_relation": "VARCHAR",
    "primary_confidence": "DOUBLE",
    "verifier_relation": "VARCHAR",
    "verifier_confidence": "DOUBLE",
    "consensus_status": "VARCHAR",
    "a_implies_b": "BOOLEAN",
    "b_implies_a": "BOOLEAN",
    "explanation": "VARCHAR",
    "nli_a_to_b_entailment": "DOUBLE",
    "nli_a_to_b_contradiction": "DOUBLE",
    "nli_a_to_b_neutral": "DOUBLE",
    "nli_b_to_a_entailment": "DOUBLE",
    "nli_b_to_a_contradiction": "DOUBLE",
    "nli_b_to_a_neutral": "DOUBLE",
    "nli_action": "VARCHAR",
    "status": "VARCHAR",
    "discovery_method": "VARCHAR",
    "prompt_version": "VARCHAR",
    "primary_model_version": "VARCHAR",
    "verifier_model_version": "VARCHAR",
    "primary_assessment_id": "VARCHAR",
    "verifier_assessment_id": "VARCHAR",
    "primary_inference_fingerprint": "VARCHAR",
    "verifier_inference_fingerprint": "VARCHAR",
    "consensus_fingerprint": "VARCHAR",
    "automation_profile_id": "VARCHAR",
    "evidence_tier": "VARCHAR",
    "extractor_id": "VARCHAR",
    "extractor_version": "VARCHAR",
    "source_spans_json": "VARCHAR",
    "rule_applicability_fingerprint": "VARCHAR",
    "proof_scope_key": "VARCHAR",
}

CANDIDATE_BLOCK_COLUMNS = {
    "block_id": "VARCHAR",
    "reason_kind": "VARCHAR",
    "group_key": "VARCHAR",
    "member_fingerprint": "VARCHAR",
    "member_count": "INTEGER",
    "candidate_version": "VARCHAR",
}

CANDIDATE_REASON_COLUMNS = {
    "block_id": "VARCHAR",
    "proposition_a_id": "VARCHAR",
    "proposition_b_id": "VARCHAR",
    "reason": "VARCHAR",
    "embedding_similarity": "DOUBLE",
    "embedding_rank": "INTEGER",
    "candidate_version": "VARCHAR",
}

EMBEDDING_STATE_COLUMNS = {
    "proposition_id": "VARCHAR",
    "text_hash": "VARCHAR",
    "embedding_model": "VARCHAR",
    "embedding_revision": "VARCHAR",
    "embedding": "FLOAT[]",
}

SEMANTIC_NEIGHBOR_STATE_COLUMNS = {
    "proposition_id": "VARCHAR",
    "neighbor_id": "VARCHAR",
    "similarity": "DOUBLE",
    "neighbor_rank": "INTEGER",
    "proposition_text_hash": "VARCHAR",
    "neighbor_text_hash": "VARCHAR",
    "embedding_model": "VARCHAR",
    "embedding_revision": "VARCHAR",
}


class CandidateStore:
    """Disk-backed working set for bounded candidate access and publication."""

    def __init__(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="oddsfox-candidates-"))
        self.path = self.directory / "oddsfox_graph.duckdb"
        self.db = DuckDB(self.path)
        self.db.execute("SET TimeZone = 'UTC'")
        self.db.execute(
            f"SET temp_directory = '{q(self.directory / 'spill')}'"
        )
        self._closed = False
        self.structural_member_limit: int | None = None
        self.embedding_vectors_reused = 0
        self.embedding_vectors_recomputed = 0
        self.embedding_write_batches = 0
        self.semantic_neighbor_write_batches = 0
        self.candidate_update_batches = 0
        self.python_rows_materialized = 0
        self.max_materialized_batch_rows = 0

    @classmethod
    def from_parquet(
        cls,
        path: Path,
        *,
        block_path: Path,
        reason_path: Path,
        embedding_path: Path,
        neighbor_path: Path,
    ) -> CandidateStore:
        store = cls()
        columns = ", ".join(CANDIDATE_COLUMNS)
        try:
            store.db.execute(
                f"""
                CREATE TABLE relation_candidates_work AS
                SELECT {columns}
                FROM read_parquet('{q(path)}')
                """
            )
            store.db.execute(
                f"""
                CREATE TABLE candidate_blocks_work AS
                SELECT * FROM read_parquet('{q(block_path)}')
                """
            )
            store.db.execute(
                f"""
                CREATE TABLE candidate_reason_rows_work AS
                SELECT * FROM read_parquet('{q(reason_path)}')
                """
            )
            store.db.execute(
                """
                CREATE TABLE candidate_block_execution_work AS
                SELECT
                    block_id,
                    'reused'::VARCHAR AS status,
                    member_fingerprint AS input_fingerprint,
                    member_fingerprint AS output_fingerprint
                FROM candidate_blocks_work
                """
            )
            store.load_semantic_state(
                embedding_path=embedding_path,
                neighbor_path=neighbor_path,
            )
            store.embedding_vectors_reused = int(
                store.db.scalar(
                    "SELECT count(*) FROM proposition_embeddings_work"
                )
                or 0
            )
            return store
        except Exception:
            store.close()
            raise

    def deterministic_rows(self) -> list[dict[str, Any]]:
        rows = self.db.rows(
            """
            SELECT proposition_a_id, proposition_b_id, rule_id
            FROM relation_candidates_work
            WHERE deterministic_relation IS NOT NULL
            ORDER BY proposition_a_id, proposition_b_id
            """
        )
        self._record_materialization(rows)
        return rows

    def mark_classification_budget(
        self,
        eligible_proposition_ids: list[str],
    ) -> None:
        self.db.execute(
            """
            UPDATE relation_candidates_work
            SET status = CASE
                WHEN proposition_a_id IN (SELECT unnest(?))
                 AND proposition_b_id IN (SELECT unnest(?))
                    THEN 'not_classified_budget'
                ELSE 'quarantined_parse'
            END
            WHERE discovery_method IS NULL
            """,
            [eligible_proposition_ids, eligible_proposition_ids],
        )

    def prepare_inference_queue(
        self,
        limit: int,
        proposition_scopes: dict[str, str] | None = None,
    ) -> None:
        self.db.execute("DROP TABLE IF EXISTS inference_candidate_queue")
        self.db.execute("DROP TABLE IF EXISTS inference_proposition_scopes")
        if proposition_scopes:
            create_and_fill(
                self.db,
                "inference_proposition_scopes",
                {"proposition_id": "VARCHAR", "scope_key": "VARCHAR"},
                [
                    {"proposition_id": proposition_id, "scope_key": scope_key}
                    for proposition_id, scope_key in sorted(
                        proposition_scopes.items()
                    )
                ],
            )
            ranked_source = """
                SELECT
                    c.proposition_a_id,
                    c.proposition_b_id,
                    len(c.candidate_reasons) AS reason_count,
                    coalesce(c.embedding_similarity, -1.0) AS similarity,
                    least(a.scope_key, b.scope_key) AS scheduling_scope_a,
                    greatest(a.scope_key, b.scope_key) AS scheduling_scope_b
                FROM relation_candidates_work c
                JOIN inference_proposition_scopes a
                  ON a.proposition_id = c.proposition_a_id
                JOIN inference_proposition_scopes b
                  ON b.proposition_id = c.proposition_b_id
                WHERE c.discovery_method IS NULL
                  AND c.status IN ('pending', 'not_classified_budget')
            """
        else:
            ranked_source = """
                SELECT
                    proposition_a_id,
                    proposition_b_id,
                    len(candidate_reasons) AS reason_count,
                    coalesce(embedding_similarity, -1.0) AS similarity,
                    ''::VARCHAR AS scheduling_scope_a,
                    ''::VARCHAR AS scheduling_scope_b
                FROM relation_candidates_work
                WHERE discovery_method IS NULL
                  AND status IN ('pending', 'not_classified_budget')
            """
        self.db.execute(
            f"""
            CREATE TABLE inference_candidate_queue AS
            WITH candidates AS (
                {ranked_source}
            ), scoped AS (
                SELECT
                    *,
                    row_number() OVER (
                        PARTITION BY scheduling_scope_a, scheduling_scope_b
                        ORDER BY reason_count DESC, similarity DESC,
                                 proposition_a_id, proposition_b_id
                    ) AS scope_rank
                FROM candidates
            ), selected AS (
                SELECT *
                FROM scoped
                ORDER BY scope_rank,
                         sha256(to_json(list_value(
                             scheduling_scope_a, scheduling_scope_b
                         ))),
                         scheduling_scope_a, scheduling_scope_b,
                         reason_count DESC, similarity DESC,
                         proposition_a_id, proposition_b_id
                LIMIT ?
            )
            SELECT
                row_number() OVER (
                    ORDER BY scope_rank,
                             sha256(to_json(list_value(
                                 scheduling_scope_a, scheduling_scope_b
                             ))),
                             scheduling_scope_a, scheduling_scope_b,
                             reason_count DESC, similarity DESC,
                             proposition_a_id, proposition_b_id
                )::INTEGER AS queue_index,
                proposition_a_id,
                proposition_b_id
            FROM selected
            ORDER BY queue_index
            """,
            [int(limit)],
        )

    def classification_coverage(self) -> dict[str, float | int]:
        row = self.db.rows(
            """
            SELECT
                count(*) FILTER (
                    WHERE deterministic_relation IS NULL
                      AND status != 'quarantined_parse'
                )::BIGINT AS eligible,
                count(*) FILTER (
                    WHERE deterministic_relation IS NULL
                      AND status IN ('accepted', 'rejected', 'quarantined')
                )::BIGINT AS assessed,
                count(*) FILTER (
                    WHERE deterministic_relation IS NULL
                      AND status IN ('not_classified_budget', 'deadline_budget_exhausted')
                )::BIGINT AS unclassified
            FROM relation_candidates_work
            """
        )[0]
        eligible = int(cast(int, row["eligible"]))
        assessed = int(cast(int, row["assessed"]))
        return {
            "eligible": eligible,
            "assessed": assessed,
            "unclassified": int(cast(int, row["unclassified"])),
            "coverage": 1.0 if eligible == 0 else assessed / eligible,
        }

    def mark_deadline_exhausted(self) -> int:
        """Mark queued work that was not completed before the scheduling cutoff."""

        self.db.execute(
            """
            UPDATE relation_candidates_work AS c
            SET status='deadline_budget_exhausted'
            FROM inference_candidate_queue q
            WHERE c.proposition_a_id=q.proposition_a_id
              AND c.proposition_b_id=q.proposition_b_id
              AND c.discovery_method IS NULL
              AND c.status IN ('pending','not_classified_budget')
            """
        )
        return int(
            self.db.scalar(
                "SELECT count(*) FROM relation_candidates_work WHERE status='deadline_budget_exhausted'"
            )
            or 0
        )

    def inference_batches(
        self,
        *,
        batch_size: int = 512,
    ) -> Iterator[list[dict[str, Any]]]:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        total = int(
            self.db.scalar("SELECT count(*) FROM inference_candidate_queue")
            or 0
        )
        for start in range(0, total, batch_size):
            rows = self.db.rows(
                """
                SELECT
                    c.proposition_a_id,
                    c.proposition_b_id,
                    c.candidate_reasons,
                    c.embedding_similarity,
                    c.embedding_rank,
                    c.status,
                    c.discovery_method
                FROM inference_candidate_queue q
                JOIN relation_candidates_work c USING (
                    proposition_a_id,
                    proposition_b_id
                )
                WHERE q.queue_index > ?
                  AND q.queue_index <= ?
                ORDER BY q.queue_index
                """,
                [start, start + batch_size],
            )
            self._record_materialization(rows)
            yield rows

    def reset_for_run(self) -> None:
        self.db.execute(
            """
            UPDATE relation_candidates_work
            SET
                classification_relation = NULL,
                classification_confidence = NULL,
                primary_relation = NULL,
                primary_confidence = NULL,
                verifier_relation = NULL,
                verifier_confidence = NULL,
                consensus_status = NULL,
                a_implies_b = NULL,
                b_implies_a = NULL,
                explanation = CASE
                    WHEN deterministic_relation IS NULL THEN NULL
                    ELSE explanation
                END,
                nli_a_to_b_entailment = NULL,
                nli_a_to_b_contradiction = NULL,
                nli_a_to_b_neutral = NULL,
                nli_b_to_a_entailment = NULL,
                nli_b_to_a_contradiction = NULL,
                nli_b_to_a_neutral = NULL,
                nli_action = NULL,
                prompt_version = NULL,
                primary_model_version = NULL,
                verifier_model_version = NULL,
                primary_assessment_id = NULL,
                verifier_assessment_id = NULL,
                primary_inference_fingerprint = NULL,
                verifier_inference_fingerprint = NULL,
                consensus_fingerprint = NULL,
                automation_profile_id = NULL,
                status = CASE
                    WHEN deterministic_relation IS NULL THEN 'pending'
                    ELSE 'accepted'
                END,
                discovery_method = CASE
                    WHEN deterministic_relation IS NULL THEN NULL
                    ELSE 'deterministic'
                END
            """
        )

    def initialize_semantic_state(self) -> None:
        create_and_fill(
            self.db,
            "proposition_embeddings_work",
            EMBEDDING_STATE_COLUMNS,
            [],
        )
        create_and_fill(
            self.db,
            "semantic_neighbors_work",
            SEMANTIC_NEIGHBOR_STATE_COLUMNS,
            [],
        )

    def load_baseline_semantic_state(
        self,
        *,
        embedding_path: Path,
        neighbor_path: Path,
    ) -> None:
        self.db.execute(
            f"""
            CREATE TABLE baseline_proposition_embeddings AS
            SELECT {", ".join(EMBEDDING_STATE_COLUMNS)}
            FROM read_parquet('{q(embedding_path)}')
            """
        )
        self.db.execute(
            f"""
            CREATE TABLE baseline_semantic_neighbors AS
            SELECT {", ".join(SEMANTIC_NEIGHBOR_STATE_COLUMNS)}
            FROM read_parquet('{q(neighbor_path)}')
            """
        )

    def baseline_embeddings(
        self,
        text_hashes: list[str],
        *,
        model: str,
        revision: str,
    ) -> list[dict[str, Any]]:
        if not text_hashes:
            return []
        rows = self.db.rows(
            """
            SELECT text_hash, embedding
            FROM baseline_proposition_embeddings
            WHERE text_hash IN (SELECT unnest(?))
              AND embedding_model = ?
              AND embedding_revision = ?
            ORDER BY text_hash
            """,
            [text_hashes, model, revision],
        )
        self._record_materialization(rows)
        return rows

    def baseline_neighbor_source_ids(
        self,
        proposition_ids: list[str],
        *,
        model: str,
        revision: str,
    ) -> set[str]:
        if not proposition_ids:
            return set()
        rows = self.db.rows(
            """
            SELECT DISTINCT proposition_id
            FROM baseline_semantic_neighbors
            WHERE proposition_id IN (SELECT unnest(?))
              AND embedding_model = ?
              AND embedding_revision = ?
            ORDER BY proposition_id
            """,
            [proposition_ids, model, revision],
        )
        self._record_materialization(rows)
        return {
            str(row["proposition_id"])
            for row in rows
        }

    def changed_embedding_source_ids(
        self,
        current_text_hashes: list[dict[str, str]],
        *,
        model: str,
        revision: str,
    ) -> set[str]:
        """Find IDs whose proposition-bound embedding input changed.

        Vector reuse is keyed by normalized text hash and can legitimately use
        a vector first produced for another proposition. Incremental neighbor
        invalidation must instead compare each proposition ID with its own
        prior text hash so a newly introduced vector is scored against every
        potentially affected top-k boundary.
        """

        create_and_fill(
            self.db,
            "current_embedding_sources",
            {
                "proposition_id": "VARCHAR",
                "text_hash": "VARCHAR",
            },
            current_text_hashes,
        )
        rows = self.db.rows(
            """
            SELECT c.proposition_id
            FROM current_embedding_sources c
            WHERE NOT EXISTS (
                SELECT 1
                FROM baseline_proposition_embeddings b
                WHERE b.proposition_id = c.proposition_id
                  AND b.text_hash = c.text_hash
                  AND b.embedding_model = ?
                  AND b.embedding_revision = ?
            )
            ORDER BY c.proposition_id
            """,
            [model, revision],
        )
        self._record_materialization(rows)
        return {str(row["proposition_id"]) for row in rows}

    def baseline_neighbors(
        self,
        proposition_ids: list[str],
        *,
        model: str,
        revision: str,
    ) -> list[dict[str, Any]]:
        if not proposition_ids:
            return []
        rows = self.db.rows(
            """
            SELECT *
            FROM baseline_semantic_neighbors
            WHERE proposition_id IN (SELECT unnest(?))
              AND embedding_model = ?
              AND embedding_revision = ?
            ORDER BY proposition_id, neighbor_rank
            """,
            [proposition_ids, model, revision],
        )
        self._record_materialization(rows)
        return rows

    def valid_baseline_neighbor_boundaries(
        self,
        current_text_hashes: list[dict[str, str]],
        changed_ids: set[str],
        *,
        neighbor_count: int,
        model: str,
        revision: str,
    ) -> dict[str, tuple[float, str]]:
        """Return the last valid prior top-k item for unchanged sources."""

        create_and_fill(
            self.db,
            "current_semantic_propositions",
            {
                "proposition_id": "VARCHAR",
                "text_hash": "VARCHAR",
            },
            current_text_hashes,
        )
        create_and_fill(
            self.db,
            "changed_semantic_propositions",
            {"proposition_id": "VARCHAR"},
            [
                {"proposition_id": proposition_id}
                for proposition_id in sorted(changed_ids)
            ],
        )
        rows = self.db.rows(
            """
            WITH valid_sources AS (
                SELECT b.proposition_id
                FROM baseline_semantic_neighbors b
                JOIN current_semantic_propositions source
                  ON source.proposition_id = b.proposition_id
                LEFT JOIN current_semantic_propositions neighbor
                  ON neighbor.proposition_id = b.neighbor_id
                LEFT JOIN changed_semantic_propositions changed_source
                  ON changed_source.proposition_id = b.proposition_id
                LEFT JOIN changed_semantic_propositions changed_neighbor
                  ON changed_neighbor.proposition_id = b.neighbor_id
                WHERE b.embedding_model = ?
                  AND b.embedding_revision = ?
                GROUP BY b.proposition_id
                HAVING count(*) = ?
                   AND count(DISTINCT b.neighbor_id) = ?
                   AND min(b.neighbor_rank) = 1
                   AND max(b.neighbor_rank) = ?
                   AND count(*) FILTER (
                        WHERE changed_source.proposition_id IS NOT NULL
                           OR changed_neighbor.proposition_id IS NOT NULL
                           OR neighbor.proposition_id IS NULL
                           OR source.text_hash != b.proposition_text_hash
                           OR neighbor.text_hash != b.neighbor_text_hash
                   ) = 0
            )
            SELECT
                b.proposition_id,
                b.similarity AS boundary_similarity,
                b.neighbor_id AS boundary_neighbor_id
            FROM baseline_semantic_neighbors b
            JOIN valid_sources v USING (proposition_id)
            WHERE b.neighbor_rank = ?
              AND b.embedding_model = ?
              AND b.embedding_revision = ?
            ORDER BY b.proposition_id
            """,
            [
                model,
                revision,
                neighbor_count,
                neighbor_count,
                neighbor_count,
                neighbor_count,
                model,
                revision,
            ],
        )
        self._record_materialization(rows)
        return {
            str(row["proposition_id"]): (
                float(cast(float, row["boundary_similarity"])),
                str(row["boundary_neighbor_id"]),
            )
            for row in rows
        }

    def copy_baseline_neighbors(
        self,
        proposition_ids: set[str],
        *,
        model: str,
        revision: str,
    ) -> None:
        create_and_fill(
            self.db,
            "reusable_neighbor_sources",
            {"proposition_id": "VARCHAR"},
            [
                {"proposition_id": proposition_id}
                for proposition_id in sorted(proposition_ids)
            ],
        )
        self.db.execute(
            """
            INSERT INTO semantic_neighbors_work
            SELECT b.*
            FROM baseline_semantic_neighbors b
            JOIN reusable_neighbor_sources r USING (proposition_id)
            WHERE b.embedding_model = ?
              AND b.embedding_revision = ?
            """,
            [model, revision],
        )

    def load_semantic_state(
        self,
        *,
        embedding_path: Path,
        neighbor_path: Path,
    ) -> None:
        self.db.execute(
            f"""
            CREATE TABLE proposition_embeddings_work AS
            SELECT {", ".join(EMBEDDING_STATE_COLUMNS)}
            FROM read_parquet('{q(embedding_path)}')
            """
        )
        self.db.execute(
            f"""
            CREATE TABLE semantic_neighbors_work AS
            SELECT {", ".join(SEMANTIC_NEIGHBOR_STATE_COLUMNS)}
            FROM read_parquet('{q(neighbor_path)}')
            """
        )

    def append_embedding_state(self, rows: list[dict[str, Any]]) -> None:
        insert_rows(
            self.db,
            "proposition_embeddings_work",
            EMBEDDING_STATE_COLUMNS,
            rows,
        )
        self.embedding_write_batches += 1

    def append_semantic_neighbors(self, rows: list[dict[str, Any]]) -> None:
        insert_rows(
            self.db,
            "semantic_neighbors_work",
            SEMANTIC_NEIGHBOR_STATE_COLUMNS,
            rows,
        )
        self.semantic_neighbor_write_batches += 1

    def update_nli_rows(self, rows: list[dict[str, Any]]) -> None:
        self._update_columns(
            rows,
            (
                "nli_a_to_b_entailment",
                "nli_a_to_b_contradiction",
                "nli_a_to_b_neutral",
                "nli_b_to_a_entailment",
                "nli_b_to_a_contradiction",
                "nli_b_to_a_neutral",
                "nli_action",
            ),
        )

    def update_deterministic_rows(self, rows: list[dict[str, Any]]) -> None:
        """Persist deterministic proof provenance after rule revalidation."""

        self._update_columns(
            rows,
            (
                "evidence_tier",
                "extractor_id",
                "extractor_version",
                "source_spans_json",
                "rule_applicability_fingerprint",
                "proof_scope_key",
            ),
        )

    def update_generative_rows(self, rows: list[dict[str, Any]]) -> None:
        self._update_columns(
            rows,
            (
                "classification_relation",
                "classification_confidence",
                "primary_relation",
                "primary_confidence",
                "verifier_relation",
                "verifier_confidence",
                "consensus_status",
                "a_implies_b",
                "b_implies_a",
                "explanation",
                "status",
                "discovery_method",
                "prompt_version",
                "primary_model_version",
                "verifier_model_version",
                "primary_assessment_id",
                "verifier_assessment_id",
                "primary_inference_fingerprint",
                "verifier_inference_fingerprint",
                "consensus_fingerprint",
                "automation_profile_id",
                "evidence_tier",
                "extractor_id",
                "extractor_version",
                "source_spans_json",
                "rule_applicability_fingerprint",
                "proof_scope_key",
            ),
        )

    def _update_columns(
        self,
        rows: list[dict[str, Any]],
        columns: tuple[str, ...],
    ) -> None:
        if not rows:
            return
        unknown = set(columns) - set(CANDIDATE_COLUMNS)
        if unknown:
            raise ValueError(f"Unknown candidate update columns: {sorted(unknown)}")
        update_columns = {
            "proposition_a_id": CANDIDATE_COLUMNS["proposition_a_id"],
            "proposition_b_id": CANDIDATE_COLUMNS["proposition_b_id"],
            **{column: CANDIDATE_COLUMNS[column] for column in columns},
        }
        self.db.execute("DROP TABLE IF EXISTS candidate_updates")
        create_and_fill(
            self.db,
            "candidate_updates",
            update_columns,
            [{key: row.get(key) for key in update_columns} for row in rows],
        )
        assignments = ", ".join(
            f"{column} = u.{column}"
            for column in columns
        )
        self.db.execute(
            f"""
            UPDATE relation_candidates_work AS c
            SET {assignments}
            FROM candidate_updates AS u
            WHERE c.proposition_a_id = u.proposition_a_id
              AND c.proposition_b_id = u.proposition_b_id
            """
        )
        self.db.execute("DROP TABLE candidate_updates")
        self.candidate_update_batches += 1

    def component_rows(
        self,
        proposition_ids: list[str],
        version: str,
    ) -> list[dict[str, Any]]:
        self.db.execute("DROP TABLE IF EXISTS component_propositions")
        create_and_fill(
            self.db,
            "component_propositions",
            {"component_id": "VARCHAR"},
            [
                {"component_id": proposition_id}
                for proposition_id in proposition_ids
            ],
        )
        rows = self.db.rows(
            """
            -- DuckDB sums unsigned 128-bit integers through DOUBLE. Convert
            -- SHA-256 chunks to DECIMAL so parallel aggregation remains exact
            -- and component fingerprints are stable across processes.
            WITH left_fingerprints AS (
                SELECT
                    proposition_a_id AS component_id,
                    sha256(
                        count(*)::VARCHAR || ':' ||
                        sum((
                            '0x' || substr(
                                sha256(proposition_b_id),
                                1,
                                16
                            )
                        )::UBIGINT::DECIMAL(38, 0))::VARCHAR || ':' ||
                        sum((
                            '0x' || substr(
                                sha256(proposition_b_id),
                                17,
                                16
                            )
                        )::UBIGINT::DECIMAL(38, 0))::VARCHAR || ':' ||
                        sum((
                            '0x' || substr(
                                sha256(proposition_b_id),
                                33,
                                16
                            )
                        )::UBIGINT::DECIMAL(38, 0))::VARCHAR || ':' ||
                        sum((
                            '0x' || substr(
                                sha256(proposition_b_id),
                                49,
                                16
                            )
                        )::UBIGINT::DECIMAL(38, 0))::VARCHAR
                    ) AS side_fingerprint,
                    count(*)::INTEGER AS pair_count
                FROM relation_candidates_work
                GROUP BY proposition_a_id
            ),
            right_fingerprints AS (
                SELECT
                    proposition_b_id AS component_id,
                    sha256(
                        count(*)::VARCHAR || ':' ||
                        sum((
                            '0x' || substr(
                                sha256(proposition_a_id),
                                1,
                                16
                            )
                        )::UBIGINT::DECIMAL(38, 0))::VARCHAR || ':' ||
                        sum((
                            '0x' || substr(
                                sha256(proposition_a_id),
                                17,
                                16
                            )
                        )::UBIGINT::DECIMAL(38, 0))::VARCHAR || ':' ||
                        sum((
                            '0x' || substr(
                                sha256(proposition_a_id),
                                33,
                                16
                            )
                        )::UBIGINT::DECIMAL(38, 0))::VARCHAR || ':' ||
                        sum((
                            '0x' || substr(
                                sha256(proposition_a_id),
                                49,
                                16
                            )
                        )::UBIGINT::DECIMAL(38, 0))::VARCHAR
                    ) AS side_fingerprint,
                    count(*)::INTEGER AS pair_count
                FROM relation_candidates_work
                GROUP BY proposition_b_id
            )
            SELECT
                p.component_id,
                sha256(
                    coalesce(l.side_fingerprint, '') || '|' ||
                    coalesce(r.side_fingerprint, '')
                ) AS component_fingerprint,
                (
                    coalesce(l.pair_count, 0) +
                    coalesce(r.pair_count, 0)
                )::INTEGER AS pair_count,
                ?::VARCHAR AS candidate_version
            FROM component_propositions p
            LEFT JOIN left_fingerprints l USING (component_id)
            LEFT JOIN right_fingerprints r USING (component_id)
            ORDER BY p.component_id
            """,
            [version],
        )
        self._record_materialization(rows)
        return rows

    def block_execution_rows(self) -> list[dict[str, Any]]:
        rows = self.db.rows(
            """
            SELECT *
            FROM candidate_block_execution_work
            ORDER BY block_id
            """
        )
        self._record_materialization(rows)
        return rows

    def instrumentation(self) -> dict[str, int]:
        return {
            "embedding_write_batches": self.embedding_write_batches,
            "semantic_neighbor_write_batches": (
                self.semantic_neighbor_write_batches
            ),
            "candidate_update_batches": self.candidate_update_batches,
            "python_rows_materialized": self.python_rows_materialized,
            "max_materialized_batch_rows": self.max_materialized_batch_rows,
        }

    def _record_materialization(
        self,
        rows: list[dict[str, Any]],
    ) -> None:
        count = len(rows)
        self.python_rows_materialized += count
        self.max_materialized_batch_rows = max(
            self.max_materialized_batch_rows,
            count,
        )

    def stats(self) -> dict[str, int]:
        row = self.db.rows(
            """
            SELECT
                count(*)::INTEGER AS candidate_edges,
                count(*) FILTER (
                    WHERE discovery_method = 'generative_consensus'
                )::INTEGER AS classified_pairs,
                count(*) FILTER (
                    WHERE status = 'not_classified_budget'
                )::INTEGER AS unclassified_budget_pairs
            FROM relation_candidates_work
            """
        )[0]
        return {
            key: int(cast(int, value))
            for key, value in row.items()
        }

    def matching_pair_ids(
        self,
        pairs: Sequence[tuple[str, str, str]],
    ) -> frozenset[str]:
        """Return qualification case IDs whose canonical pair was retrieved."""
        if not pairs:
            return frozenset()
        self.db.execute("DROP TABLE IF EXISTS qualification_expected_pairs")
        self.db.execute(
            """
            CREATE TEMP TABLE qualification_expected_pairs (
                case_id VARCHAR NOT NULL,
                proposition_a_id VARCHAR NOT NULL,
                proposition_b_id VARCHAR NOT NULL
            )
            """
        )
        self.db.executemany(
            "INSERT INTO qualification_expected_pairs VALUES (?, ?, ?)",
            [
                (case_id, min(first, second), max(first, second))
                for case_id, first, second in pairs
            ],
        )
        rows = self.db.rows(
            """
            SELECT e.case_id
            FROM qualification_expected_pairs e
            JOIN relation_candidates_work c USING (
                proposition_a_id,
                proposition_b_id
            )
            ORDER BY e.case_id
            """
        )
        self.db.execute("DROP TABLE qualification_expected_pairs")
        return frozenset(str(row["case_id"]) for row in rows)

    def seal(self) -> None:
        if not self._closed:
            self.db.close()
            self._closed = True

    def promote_to(self, path: Path) -> None:
        """Move this workspace into the staged final graph database."""
        target = path.resolve()
        if target.exists():
            raise ValueError(f"Candidate workspace target already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        self.seal()
        shutil.move(str(self.path), target)
        self.path = target

    @staticmethod
    def promote_public_tables(db: DuckDB) -> None:
        for current, public in (
            ("relation_candidates_work", "relation_candidates_v"),
            ("candidate_blocks_work", "candidate_blocks_v"),
            ("candidate_reason_rows_work", "candidate_reason_rows_v"),
            ("proposition_embeddings_work", "proposition_embeddings_v"),
            ("semantic_neighbors_work", "semantic_neighbors_v"),
        ):
            db.execute(f"ALTER TABLE {current} RENAME TO {public}")
        for table in (
            "accepted_deterministic_pairs",
            "baseline_proposition_embeddings",
            "baseline_semantic_neighbors",
            "candidate_block_execution_work",
            "candidate_updates",
            "component_propositions",
            "current_embedding_sources",
            "current_semantic_propositions",
            "changed_semantic_propositions",
            "deterministic_memberships",
            "deterministic_pairs",
            "directed_embedding_neighbors",
            "embedding_reasons",
            "inference_candidate_queue",
            "inference_proposition_scopes",
            "proposition_features",
            "reusable_neighbor_sources",
            "structural_memberships",
        ):
            db.execute(f"DROP TABLE IF EXISTS {table}")

    def close(self) -> None:
        self.seal()
        shutil.rmtree(self.directory, ignore_errors=True)

    def __del__(self) -> None:  # pragma: no cover - exception-path safety net
        try:
            self.close()
        except Exception:
            pass
