from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from .manifest_contracts import (
    CoverageSummary,
    FastBuildManifest,
    FullBuildManifest,
    ViewerManifest,
    load_viewer_manifest,
    validate_manifest_pair,
)
from .provenance import atomic_write_json, sha256_file
from ..artifacts import ARTIFACT_COLUMNS, artifact_projection
from ..queries import DuckDB, q


def json_text(value: object | None) -> str | None:
    """Serialize scalar metadata to its stable JSON text representation."""

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@dataclass(frozen=True)
class _FinalizedFileHash:
    digest: str
    signature: tuple[int, int, int, int]


class FinalizedFileHashRegistry:
    """Hash finalized publication files once, invalidating changed files safely."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._hashes: dict[str, _FinalizedFileHash] = {}
        self._files_read = 0
        self._bytes_read = 0
        self._cache_hits = 0
        self._files_verified = 0
        self._bytes_verified = 0

    def hash(self, name: str) -> str:
        key, path = self._publication_path(name)
        stat = path.stat()
        if not path.is_file():
            raise ValueError(f"Publication artifact is not a file: {name}")
        signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        cached = self._hashes.get(key)
        if cached is not None and cached.signature == signature:
            self._cache_hits += 1
            return cached.digest
        digest = sha256_file(path)
        self._hashes[key] = _FinalizedFileHash(digest, signature)
        self._files_read += 1
        self._bytes_read += stat.st_size
        return digest

    def hashes(self, names: Sequence[str]) -> dict[str, str]:
        return {name: self.hash(name) for name in sorted(names)}

    def verify(self, expected: dict[str, str]) -> dict[str, str]:
        actual: dict[str, str] = {}
        for name in sorted(expected):
            expected_digest = expected[name]
            if not isinstance(expected_digest, str):
                raise ValueError(f"Publication hash is invalid for {name}")
            digest = self.hash(name)
            actual[name] = digest
            path = self._publication_path(name)[1]
            self._files_verified += 1
            self._bytes_verified += path.stat().st_size
            if digest != expected_digest:
                raise ValueError(f"Publication artifact hash does not match: {name}")
        return actual

    def invalidate(self, name: str) -> None:
        key, _ = self._publication_path(name)
        self._hashes.pop(key, None)

    def rebase(self, root: Path) -> None:
        """Retain hashes across an atomic directory rename."""

        self.root = root.resolve()

    def instrumentation(self) -> dict[str, int]:
        return {
            "files_read": self._files_read,
            "bytes_read": self._bytes_read,
            "cache_hits": self._cache_hits,
            "files_verified": self._files_verified,
            "bytes_verified": self._bytes_verified,
        }

    def _publication_path(self, name: str) -> tuple[str, Path]:
        relative = Path(name)
        if (
            not name
            or relative.is_absolute()
            or relative == Path(".")
            or ".." in relative.parts
        ):
            raise ValueError(f"Publication artifact path must be relative: {name}")
        key = relative.as_posix()
        path = self.root / relative
        try:
            path.resolve().relative_to(self.root)
        except ValueError as exc:
            raise ValueError(
                f"Publication artifact escapes the output directory: {name}"
            ) from exc
        return key, path


def copy_sorted_parquet(
    db: DuckDB,
    table: str,
    path: Path,
    columns: Sequence[str],
    order_by: str,
) -> None:
    projection = ", ".join(columns)
    db.execute(
        f"""
        COPY (
            SELECT {projection}
            FROM {table}
            ORDER BY {order_by}
        ) TO '{q(path)}' (
            FORMAT PARQUET,
            COMPRESSION ZSTD,
            COMPRESSION_LEVEL 1
        )
        """
    )


def write_conditionals(db: DuckDB, out_dir: Path) -> None:
    db.execute(
        """
        CREATE TABLE conditional_edges_v AS
        WITH exact_exclusion AS (
            SELECT
                src_node_id AS a_node_id,
                dst_node_id AS b_node_id,
                0.0 AS p_a_given_b,
                CASE
                    WHEN edge_type = 'complement' THEN 'exact_complement'
                    ELSE 'exact_exclusion'
                END AS method,
                confidence,
                evidence
            FROM logic_edges_v
            WHERE edge_type IN ('complement', 'mutually_exclusive')
            UNION ALL
            SELECT
                dst_node_id AS a_node_id,
                src_node_id AS b_node_id,
                0.0 AS p_a_given_b,
                CASE
                    WHEN edge_type = 'complement' THEN 'exact_complement'
                    ELSE 'exact_exclusion'
                END AS method,
                confidence,
                evidence
            FROM logic_edges_v
            WHERE edge_type IN ('complement', 'mutually_exclusive')
        ),
        exact_equivalence AS (
            SELECT
                src_node_id AS a_node_id,
                dst_node_id AS b_node_id,
                1.0 AS p_a_given_b,
                'exact_equivalence' AS method,
                confidence,
                evidence
            FROM logic_edges_v
            WHERE edge_type = 'equivalent'
            UNION ALL
            SELECT
                dst_node_id AS a_node_id,
                src_node_id AS b_node_id,
                1.0 AS p_a_given_b,
                'exact_equivalence' AS method,
                confidence,
                evidence
            FROM logic_edges_v
            WHERE edge_type = 'equivalent'
        ),
        exact_implication AS (
            SELECT
                dst_node_id AS a_node_id,
                src_node_id AS b_node_id,
                1.0 AS p_a_given_b,
                'exact_implication' AS method,
                confidence,
                evidence
            FROM logic_edges_v
            WHERE edge_type = 'implies'
        )
        SELECT * FROM exact_exclusion
        UNION ALL SELECT * FROM exact_equivalence
        UNION ALL SELECT * FROM exact_implication
        ORDER BY a_node_id, b_node_id, method;
        """
    )
    actual = [
        str(row["column_name"])
        for row in db.rows("DESCRIBE SELECT * FROM conditional_edges_v")
    ]
    expected = ARTIFACT_COLUMNS["conditional_edges.parquet"]
    if actual != expected:
        raise RuntimeError(
            "conditional_edges_v column contract drift: "
            f"expected {expected}, got {actual}"
        )
    db.execute(
        f"""
        COPY (
            SELECT {artifact_projection("conditional_edges.parquet")}
            FROM conditional_edges_v
            ORDER BY a_node_id, b_node_id, method
        ) TO '{q(out_dir / "conditional_edges.parquet")}' (
            FORMAT PARQUET,
            COMPRESSION ZSTD,
            COMPRESSION_LEVEL 1
        );
        """
    )


@dataclass
class PublicationSwap:
    """A directory swap that is finalized only after the manifest is durable."""

    out_dir: Path
    backup: Path | None
    _finished: bool = False

    def finalize(self) -> None:
        if self._finished:
            return
        self._finished = True
        if self.backup is not None:
            shutil.rmtree(self.backup)

    def rollback(self) -> None:
        if self._finished:
            return
        withdrawn: Path | None = None
        try:
            if self.out_dir.exists():
                withdrawn = Path(
                    tempfile.mkdtemp(
                        prefix=f".{self.out_dir.name}.withdrawn-",
                        dir=self.out_dir.parent,
                    )
                )
                withdrawn.rmdir()
                os.replace(self.out_dir, withdrawn)
            if self.backup is not None and self.backup.exists():
                os.replace(self.backup, self.out_dir)
        except BaseException:
            if not self.out_dir.exists():
                if self.backup is not None and self.backup.exists():
                    os.replace(self.backup, self.out_dir)
                elif withdrawn is not None and withdrawn.exists():
                    os.replace(withdrawn, self.out_dir)
            self._finished = True
            if withdrawn is not None:
                shutil.rmtree(withdrawn, ignore_errors=True)
            raise
        self._finished = True
        if withdrawn is not None:
            shutil.rmtree(withdrawn, ignore_errors=True)


def publish_directory_atomically(
    staging: Path,
    out_dir: Path,
    *,
    completion_marker: str | None = None,
) -> PublicationSwap:
    """Swap staging into place and retain the prior output until finalized."""

    if not staging.is_dir():
        raise ValueError(f"Discovery staging directory does not exist: {staging}")
    if completion_marker is not None:
        marker = Path(completion_marker)
        if (
            marker.is_absolute()
            or marker == Path(".")
            or ".." in marker.parts
        ):
            raise ValueError(
                f"Publication completion marker must be relative: {completion_marker}"
            )
        if not (staging / marker).is_file():
            raise ValueError(
                f"Publication staging is incomplete: missing {completion_marker}"
            )
    if out_dir.is_symlink() or (out_dir.exists() and not out_dir.is_dir()):
        raise ValueError(f"Discovery output must be a directory path: {out_dir}")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    try:
        if out_dir.exists():
            backup = Path(
                tempfile.mkdtemp(
                    prefix=f".{out_dir.name}.previous-",
                    dir=out_dir.parent,
                )
            )
            backup.rmdir()
            os.replace(out_dir, backup)
        os.replace(staging, out_dir)
    except BaseException:
        if backup is not None and backup.exists():
            if out_dir.exists() and staging.exists():
                # An interrupt can land after the temporary path was created but
                # before the previous output was moved into it.
                backup.rmdir()
            else:
                if out_dir.exists() and not staging.exists():
                    os.replace(out_dir, staging)
                if not out_dir.exists():
                    os.replace(backup, out_dir)
        elif backup is None and out_dir.exists() and not staging.exists():
            os.replace(out_dir, staging)
        raise
    return PublicationSwap(out_dir=out_dir, backup=backup)


def validate_source_output_paths(input_path: Path, out_dir: Path) -> None:
    """Reject publication targets that could consume the source artifact."""

    if input_path == out_dir or out_dir in input_path.parents:
        raise ValueError(
            "Discovery output must not be the input file or contain the input file: "
            f"input={input_path}, output={out_dir}"
        )


def write_coverage_summary(
    out_dir: Path,
    payload: dict[str, object],
) -> dict[str, object]:
    """Validate and canonically write the coverage contract."""

    validated = CoverageSummary.model_validate_json(
        json.dumps(payload, sort_keys=True, default=str)
    )
    canonical = cast(
        dict[str, object],
        validated.model_dump(mode="json"),
    )
    atomic_write_json(out_dir / "coverage_summary.json", canonical)
    return canonical


def write_viewer_manifest(
    out_dir: Path,
    payload: dict[str, object],
) -> dict[str, object]:
    """Validate and canonically write the viewer contract."""

    validated = ViewerManifest.model_validate_json(
        json.dumps(payload, sort_keys=True, default=str)
    )
    canonical = cast(
        dict[str, object],
        validated.model_dump(mode="json"),
    )
    atomic_write_json(out_dir / "viewer_manifest.json", canonical)
    return canonical


def write_manifest_last(
    out_dir: Path,
    manifest: dict[str, Any],
) -> None:
    """Validate and write the completion marker after every other artifact."""

    serialized = json.dumps(manifest, sort_keys=True, default=str)
    validated: FastBuildManifest | FullBuildManifest
    if manifest.get("build_mode") == "fast":
        validated = FastBuildManifest.model_validate_json(serialized)
    elif manifest.get("build_mode") == "full":
        validated = FullBuildManifest.model_validate_json(serialized)
    else:
        raise ValueError("Discovery build manifest has an invalid build mode")
    viewer = load_viewer_manifest(out_dir / "viewer_manifest.json")
    validate_manifest_pair(validated, viewer)
    atomic_write_json(
        out_dir / "build_manifest.json",
        validated.model_dump(mode="json"),
    )
