from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, cast

from .bulk import create_and_fill
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
    "supporting_fields": "VARCHAR",
    "a_implies_b": "BOOLEAN",
    "b_implies_a": "BOOLEAN",
    "explanation": "VARCHAR",
    "assumptions": "VARCHAR[]",
    "requires_review": "BOOLEAN",
    "status": "VARCHAR",
    "discovery_method": "VARCHAR",
    "model_version": "VARCHAR",
    "prompt_version": "VARCHAR",
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


class CandidateStore:
    """Disk-backed working set for bounded candidate access and publication."""

    def __init__(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="oddsfox-candidates-"))
        self.path = self.directory / "candidates.duckdb"
        self.db = DuckDB(self.path)
        self.db.execute("SET TimeZone = 'UTC'")
        self.db.execute(
            f"SET temp_directory = '{q(self.directory / 'spill')}'"
        )
        self._closed = False
        self.structural_member_limit: int | None = None

    @classmethod
    def from_parquet(
        cls,
        path: Path,
        *,
        block_path: Path,
        reason_path: Path,
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
            return store
        except Exception:
            store.close()
            raise

    def rows(self, where: str = "", order_by: str = "") -> list[dict[str, Any]]:
        suffix = f" WHERE {where}" if where else ""
        ordering = f" ORDER BY {order_by}" if order_by else ""
        return self.db.rows(
            f"SELECT * FROM relation_candidates_work{suffix}{ordering}"
        )

    def deterministic_rows(self) -> list[dict[str, Any]]:
        return self.rows(
            "deterministic_relation IS NOT NULL",
            "proposition_a_id, proposition_b_id",
        )

    def classification_rows(self, limit: int) -> list[dict[str, Any]]:
        return self.db.rows(
            """
            SELECT *
            FROM relation_candidates_work
            WHERE discovery_method IS NULL
            ORDER BY
                len(candidate_reasons) DESC,
                coalesce(embedding_similarity, -1.0) DESC,
                proposition_a_id,
                proposition_b_id
            LIMIT ?
            """,
            [int(limit)],
        )

    def mark_classification_budget(self) -> None:
        self.db.execute(
            """
            UPDATE relation_candidates_work
            SET status = 'not_classified_budget'
            WHERE discovery_method IS NULL
            """
        )

    def reset_for_run(self) -> None:
        self.db.execute(
            """
            UPDATE relation_candidates_work
            SET
                classification_relation = NULL,
                classification_confidence = NULL,
                supporting_fields = NULL,
                a_implies_b = NULL,
                b_implies_a = NULL,
                explanation = CASE
                    WHEN deterministic_relation IS NULL THEN NULL
                    ELSE explanation
                END,
                assumptions = [],
                requires_review = false,
                model_version = NULL,
                prompt_version = NULL,
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

    def update_rows(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        self.db.execute("DROP TABLE IF EXISTS candidate_updates")
        create_and_fill(
            self.db,
            "candidate_updates",
            CANDIDATE_COLUMNS,
            [{key: row.get(key) for key in CANDIDATE_COLUMNS} for row in rows],
        )
        assignments = ", ".join(
            f"{column} = u.{column}"
            for column in CANDIDATE_COLUMNS
            if column not in {"proposition_a_id", "proposition_b_id"}
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

    def component_rows(self, proposition_ids: list[str], version: str) -> list[dict[str, Any]]:
        self.db.execute("DROP TABLE IF EXISTS component_propositions")
        create_and_fill(
            self.db,
            "component_propositions",
            {"component_id": "VARCHAR"},
            [{"component_id": proposition_id} for proposition_id in proposition_ids],
        )

        return self.db.rows(
            """
            WITH incident AS (
                SELECT
                    proposition_a_id AS component_id,
                    proposition_a_id || ':' || proposition_b_id || ':' ||
                        coalesce(array_to_string(candidate_reasons, ','), '') || ':' ||
                        coalesce(embedding_similarity::VARCHAR, '') || ':' ||
                        coalesce(embedding_rank::VARCHAR, '') || ':' ||
                        coalesce(rule_id, '') AS payload
                FROM relation_candidates_work
                UNION ALL
                SELECT
                    proposition_b_id AS component_id,
                    proposition_a_id || ':' || proposition_b_id || ':' ||
                        coalesce(array_to_string(candidate_reasons, ','), '') || ':' ||
                        coalesce(embedding_similarity::VARCHAR, '') || ':' ||
                        coalesce(embedding_rank::VARCHAR, '') || ':' ||
                        coalesce(rule_id, '') AS payload
                FROM relation_candidates_work
            ),
            fingerprints AS (
                SELECT
                    component_id,
                    sha256(string_agg(payload, '|' ORDER BY payload))
                        AS component_fingerprint,
                    count(*)::INTEGER AS pair_count
                FROM incident
                GROUP BY component_id
            )
            SELECT
                p.component_id,
                coalesce(f.component_fingerprint, sha256(''))
                    AS component_fingerprint,
                coalesce(f.pair_count, 0)::INTEGER AS pair_count,
                ?::VARCHAR AS candidate_version
            FROM component_propositions p
            LEFT JOIN fingerprints f USING (component_id)
            ORDER BY p.component_id
            """,
            [version],
        )

    def block_execution_rows(self) -> list[dict[str, Any]]:
        return self.db.rows(
            """
            SELECT *
            FROM candidate_block_execution_work
            ORDER BY block_id
            """
        )

    def stats(self) -> dict[str, int]:
        row = self.db.rows(
            """
            SELECT
                count(*)::INTEGER AS candidate_edges,
                count(*) FILTER (
                    WHERE discovery_method = 'llm'
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

    def seal(self) -> None:
        if not self._closed:
            self.db.close()
            self._closed = True

    def attach_to(self, db: DuckDB, table: str = "relation_candidates_v") -> None:
        self.seal()
        db.execute(f"ATTACH '{q(self.path)}' AS candidate_workspace (READ_ONLY)")
        columns = ", ".join(CANDIDATE_COLUMNS)
        db.execute(
            f"""
            CREATE TABLE {table} AS
            SELECT {columns}
            FROM candidate_workspace.relation_candidates_work
            """
        )
        db.execute(
            """
            CREATE TABLE candidate_blocks_v AS
            SELECT * FROM candidate_workspace.candidate_blocks_work
            """
        )
        db.execute(
            """
            CREATE TABLE candidate_reason_rows_v AS
            SELECT * FROM candidate_workspace.candidate_reason_rows_work
            """
        )
        db.execute("DETACH candidate_workspace")

    def close(self) -> None:
        self.seal()
        shutil.rmtree(self.directory, ignore_errors=True)

    def __del__(self) -> None:  # pragma: no cover - exception-path safety net
        try:
            self.close()
        except Exception:
            pass
