"""Public facade for serving and exporting the local graph explorer."""

from __future__ import annotations

import shutil
import tempfile
import threading
import webbrowser
from pathlib import Path
from typing import Literal

from ._discovery.provenance import (
    atomic_write_json,
    canonical_json_sha256,
    sha256_file,
)
from ._discovery.publication import publish_directory_atomically
from ._discovery.bulk import create_and_fill
from fastapi import FastAPI

from ._explorer.service import create_app, validate_loopback_host
from .graph import Graph
from .queries import DuckDB, q


STATIC_NODE_COLUMNS = {
    "id": "VARCHAR",
    "label": "VARCHAR",
    "level": "VARCHAR",
    "parent_id": "VARCHAR",
    "x": "DOUBLE",
    "y": "DOUBLE",
    "size": "DOUBLE",
    "domain": "VARCHAR",
    "component_id": "VARCHAR",
    "market_id": "VARCHAR",
    "proposition_count": "BIGINT",
    "edge_count": "BIGINT",
    "classification_coverage": "DOUBLE",
}

STATIC_EDGE_COLUMNS = {
    "id": "VARCHAR",
    "source": "VARCHAR",
    "target": "VARCHAR",
    "relation": "VARCHAR",
    "count": "BIGINT",
    "confidence": "DOUBLE",
    "discovery_method": "VARCHAR",
    "evidence_tier": "VARCHAR",
    "aggregation_only": "BOOLEAN",
}


def create_explorer_app(
    out_dir: Path,
    *,
    max_response_nodes: int = 5_000,
    max_response_edges: int = 10_000,
) -> FastAPI:
    """Create the read-only ASGI explorer application."""

    return create_app(
        out_dir,
        max_response_nodes=max_response_nodes,
        max_response_edges=max_response_edges,
    )


def validate_explorer_host(host: str) -> str:
    """Require an explicit loopback bind address."""

    return validate_loopback_host(host)


def serve_graph(
    out_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    max_response_nodes: int = 5_000,
    max_response_edges: int = 10_000,
) -> None:
    """Run the read-only explorer until interrupted."""

    import uvicorn

    validated_host = validate_explorer_host(host)
    if not 1 <= port <= 65_535:
        raise ValueError("port must be between 1 and 65535")
    app = create_explorer_app(
        out_dir,
        max_response_nodes=max_response_nodes,
        max_response_edges=max_response_edges,
    )
    if open_browser:
        browser_host = (
            f"[{validated_host}]" if ":" in validated_host else validated_host
        )
        browser_timer = threading.Timer(
            0.5,
            webbrowser.open,
            args=(f"http://{browser_host}:{port}/",),
        )
        browser_timer.daemon = True
        browser_timer.start()
    uvicorn.run(app, host=validated_host, port=port, access_log=False)


def export_explorer(
    out_dir: Path,
    destination: Path,
    *,
    scope: Literal["event", "component", "neighborhood"],
    identifier: str,
    max_nodes: int = 5_000,
    max_edges: int = 10_000,
) -> dict[str, object]:
    """Publish a deterministic bounded static explorer snapshot."""

    if scope not in {"event", "component", "neighborhood"}:
        raise ValueError(f"Unsupported explorer export scope {scope!r}")
    if not 1 <= max_nodes <= 5_000:
        raise ValueError("max_nodes must be between 1 and 5000")
    if not 0 <= max_edges <= 10_000:
        raise ValueError("max_edges must be between 0 and 10000")
    source = out_dir.resolve()
    destination = destination.resolve()
    if (
        source == destination
        or source in destination.parents
        or destination in source.parents
    ):
        raise ValueError(
            "Static explorer destination must not overlap the source graph directory"
        )
    graph = Graph.open(source)
    if scope == "neighborhood":
        view = graph.neighborhood(
            (identifier,),
            hops=2,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
    elif scope == "event":
        node_ids = _selected_nodes(source, "event_key", [identifier], max_nodes)
        view = graph.neighborhood(
            tuple(node_ids),
            hops=1,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
    else:
        node_ids = _selected_nodes(
            source,
            "component_id",
            [identifier],
            max_nodes,
        )
        view = graph.neighborhood(
            tuple(node_ids),
            hops=1,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
    if view.truncated_nodes or view.truncated_edges:
        raise ValueError(
            "Static export exceeds the declared node or edge ceiling; "
            "use a narrower scope or higher safe limits"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.explorer-",
            dir=destination.parent,
        )
    )
    try:
        assets = Path(__file__).resolve().parent / "static" / "explorer"
        if not assets.is_dir():
            raise RuntimeError("Packaged explorer assets are missing")
        shutil.copytree(assets, staging, dirs_exist_ok=True)
        snapshot = view.model_dump(mode="json")
        snapshot_db = DuckDB()
        try:
            create_and_fill(
                snapshot_db,
                "snapshot_nodes",
                STATIC_NODE_COLUMNS,
                [node.model_dump(mode="json") for node in view.nodes],
            )
            create_and_fill(
                snapshot_db,
                "snapshot_edges",
                STATIC_EDGE_COLUMNS,
                [edge.model_dump(mode="json") for edge in view.edges],
            )
            snapshot_db.execute(
                f"COPY snapshot_nodes TO '{q(staging / 'snapshot_nodes.parquet')}' "
                "(FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            snapshot_db.execute(
                f"COPY snapshot_edges TO '{q(staging / 'snapshot_edges.parquet')}' "
                "(FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        finally:
            snapshot_db.close()
        manifest = {
            "schema_version": "static-explorer-v2",
            "package_version": graph.metadata().package_version,
            "source_graph": canonical_json_sha256(graph.metadata().build),
            "scope": scope,
            "identifier": identifier,
            "max_nodes": max_nodes,
            "max_edges": max_edges,
            "snapshot_hash": canonical_json_sha256(snapshot),
            "snapshot_files": {
                name: sha256_file(staging / name)
                for name in ("snapshot_nodes.parquet", "snapshot_edges.parquet")
            },
            "coverage": graph.coverage(),
            "data_format": "duckdb-wasm-parquet",
        }
        atomic_write_json(staging / "static_manifest.json", manifest)
        publish_directory_atomically(staging, destination).finalize()
        return manifest
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _selected_nodes(
    out_dir: Path,
    column: Literal["component_id", "event_key"],
    values: list[str],
    limit: int,
) -> list[str]:
    db = DuckDB(out_dir / "oddsfox_graph.duckdb", read_only=True)
    try:
        rows = db.rows(
            f"""
            SELECT proposition_id
            FROM explorer_propositions_v
            WHERE {column} IN (SELECT unnest(?))
            ORDER BY proposition_id
            LIMIT ?
            """,
            [values, limit + 1],
        )
    finally:
        db.close()
    if len(rows) > limit:
        raise ValueError("Static export node selection exceeds max_nodes")
    if not rows:
        raise KeyError(f"Unknown {column.removesuffix('_id')} {values[0]!r}")
    return [str(row["proposition_id"]) for row in rows]
