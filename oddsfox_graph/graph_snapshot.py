from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any

from . import __version__
from .queries import DuckDB

GRAPH_SNAPSHOT_ARTIFACT = "graph_snapshot.json"


def write_graph_snapshot(
    db: DuckDB,
    out_dir: Path,
    source_manifest: str = "build_manifest.json",
) -> dict[str, Any]:
    source_watermark = db.scalar(
        """
        SELECT max(coalesce(last_seen_ts, first_seen_ts))
        FROM nodes_table
        """
    )
    built_at = (
        source_watermark.isoformat()
        if isinstance(source_watermark, datetime)
        else "1970-01-01T00:00:00+00:00"
    )
    counts = {
        "nodes": int(db.scalar("SELECT count(*) FROM nodes_v") or 0),
        "logic_edges": int(db.scalar("SELECT count(*) FROM logic_edges_v") or 0),
        "conditionals": int(db.scalar("SELECT count(*) FROM conditional_edges_v") or 0),
    }
    header = {
        "version": f"v{__version__}",
        "built_at": built_at,
        "source_manifest": source_manifest,
        "counts": counts,
    }
    snapshot = {
        **header,
        "storage": "external-artifacts",
        "nodes_artifact": "nodes.parquet",
        "logic_edges_artifact": "logic_edges.parquet",
        "conditional_edges_artifact": "conditional_edges.parquet",
    }
    destination = out_dir / GRAPH_SNAPSHOT_ARTIFACT
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
        + "\n",
        encoding="utf-8",
    )
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
    return snapshot
