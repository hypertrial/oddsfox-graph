from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .provenance import atomic_write_json
from ..queries import DuckDB, q


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
        ) TO '{q(path)}' (FORMAT PARQUET)
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
        if self.backup is not None:
            shutil.rmtree(self.backup)
        self._finished = True

    def rollback(self) -> None:
        if self._finished:
            return
        if self.out_dir.exists():
            shutil.rmtree(self.out_dir)
        if self.backup is not None:
            os.replace(self.backup, self.out_dir)
        self._finished = True


def publish_directory_atomically(
    staging: Path,
    out_dir: Path,
) -> PublicationSwap:
    """Swap staging into place and retain the prior output until finalized."""

    if not staging.is_dir():
        raise ValueError(f"Discovery staging directory does not exist: {staging}")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if out_dir.exists():
        backup = Path(
            tempfile.mkdtemp(
                prefix=f".{out_dir.name}.previous-",
                dir=out_dir.parent,
            )
        )
        backup.rmdir()
        os.replace(out_dir, backup)
    try:
        os.replace(staging, out_dir)
    except Exception:
        if backup is not None:
            os.replace(backup, out_dir)
        raise
    return PublicationSwap(out_dir=out_dir, backup=backup)


def write_manifest_last(
    out_dir: Path,
    manifest: dict[str, Any],
) -> None:
    """Write the completion marker after every other artifact is durable."""
    atomic_write_json(out_dir / "build_manifest.json", manifest)
