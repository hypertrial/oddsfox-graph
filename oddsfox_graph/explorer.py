"""Public facade for serving and exporting the local graph explorer."""

from __future__ import annotations

import gzip
import shutil
import tempfile
import threading
import webbrowser
from pathlib import Path
from typing import Literal, cast

from ._discovery.provenance import (
    atomic_write_json,
    canonical_json_sha256,
    sha256_file,
)
from ._discovery.publication import publish_directory_atomically
from ._discovery.versions import (
    ESSENTIAL_PROJECTION_VERSION,
    EXPLORER_DERIVED_SEMANTICS_VERSION,
    STATIC_EXPLORER_CORE_VERSION,
    STATIC_EXPLORER_GRAPH_VERSION,
    STATIC_EXPLORER_VERSION,
    WC2026_SOURCE_SCHEMA,
)
from fastapi import FastAPI

from ._explorer.service import create_app, validate_loopback_host
from ._explorer.derived import (
    graph_display_stats,
    human_highlight_ids,
)
from ._explorer.human import HumanExplorer
from ._explorer.contracts import GraphFilter, RelationshipDetail
from .graph import Graph
from .queries import DuckDB


MAX_STATIC_SNAPSHOT_BYTES = 5 * 1024 * 1024 // 4
MAX_STATIC_SNAPSHOT_GZIP_BYTES = 200 * 1024


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
    scope: Literal["graph", "event", "component", "neighborhood"],
    identifier: str | None = None,
    max_nodes: int = 5_000,
    max_edges: int = 10_000,
) -> dict[str, object]:
    """Publish a deterministic bounded static explorer snapshot."""

    if scope not in {"graph", "event", "component", "neighborhood"}:
        raise ValueError(f"Unsupported explorer export scope {scope!r}")
    if scope != "graph" and not identifier:
        raise ValueError(f"Static explorer scope {scope!r} requires an identifier")
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
    export_filters = GraphFilter(include_compatible=True)
    metadata = graph.metadata()
    build_metadata = metadata.build.model_dump(mode="json")
    viewer_metadata = metadata.viewer.model_dump(mode="json")
    coverage_payload = metadata.coverage.model_dump(mode="json")
    build_input = build_metadata.get("input")
    input_profile = (
        build_input.get("schema")
        if isinstance(build_input, dict)
        else build_metadata.get("input_schema")
    )
    if input_profile != WC2026_SOURCE_SCHEMA:
        raise ValueError(
            "Static explorer export requires a graph built with "
            "--input-profile polymarket-wc2026-graph-hourly-v1"
        )
    if scope == "graph":
        node_ids = _all_nodes(source, max_nodes)
        view = graph.neighborhood(
            tuple(node_ids),
            hops=1,
            filters=export_filters,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
    elif scope == "neighborhood":
        assert identifier is not None
        view = graph.neighborhood(
            (identifier,),
            hops=2,
            filters=export_filters,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
    elif scope == "event":
        assert identifier is not None
        node_ids = _selected_nodes(source, "event_key", [identifier], max_nodes)
        view = graph.neighborhood(
            tuple(node_ids),
            hops=1,
            filters=export_filters,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
    else:
        assert identifier is not None
        node_ids = _selected_nodes(
            source,
            "component_id",
            [identifier],
            max_nodes,
        )
        view = graph.neighborhood(
            tuple(node_ids),
            hops=1,
            filters=export_filters,
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
        client_fingerprint = canonical_json_sha256(
            [
                {
                    "path": path.relative_to(assets).as_posix(),
                    "sha256": sha256_file(path),
                }
                for path in sorted(assets.rglob("*"))
                if path.is_file()
            ]
        )
        shutil.copytree(assets, staging, dirs_exist_ok=True)
        snapshot = view.model_dump(mode="json")
        human_rows = _static_human_rows(
            source,
            tuple(node.id for node in view.nodes),
            tuple(edge.id for edge in view.edges),
        )
        claim_rows = cast(list[dict[str, object]], human_rows["claims"])
        essential_rows = cast(
            list[dict[str, object]], human_rows["essential_relationships"]
        )
        relationship_rows = cast(
            list[dict[str, object]], human_rows["relationships"]
        )
        human_display_stats = graph_display_stats(
            tuple(str(row["plain_claim"]) for row in claim_rows),
            tuple(
                (str(row["source_id"]), str(row["target_id"]))
                for row in essential_rows
            ),
            input_edge_count=len(relationship_rows),
        )
        static_capabilities = {
            "mode": "static",
            "hierarchy": True,
            "search": True,
            "relationship_inspection": True,
            "analyst_graph": True,
            "compare": True,
            "proof": False,
            "why_not": False,
            "recording": False,
            "regeneration": False,
        }
        core_payload = {
            "schema_version": STATIC_EXPLORER_CORE_VERSION,
            "scope": human_rows["scope"],
            "coverage": coverage_payload,
            "capabilities": static_capabilities,
            "display_stats": human_display_stats.model_dump(mode="json"),
            "stages": human_rows["stages"],
            "teams": human_rows["teams"],
            "markets": human_rows["markets"],
            "claims": human_rows["claims"],
            "relationships": human_rows["relationships"],
            "essential_relationship_ids": human_rows[
                "essential_relationship_ids"
            ],
            "highlight_relationship_ids": human_rows[
                "highlight_relationship_ids"
            ],
            "relationship_groups": human_rows["groups"],
        }
        graph_payload = {
            "schema_version": STATIC_EXPLORER_GRAPH_VERSION,
            "view": snapshot,
            "essential_edge_ids": human_rows["essential_relationship_ids"],
            "layout_version": viewer_metadata["layout_version"],
            "coordinate_fingerprint": canonical_json_sha256(
                [
                    {"id": node.id, "x": node.x, "y": node.y}
                    for node in sorted(view.nodes, key=lambda item: item.id)
                ]
            ),
        }
        core_path = staging / "explore_snapshot.json"
        graph_path = staging / "graph_snapshot.json"
        atomic_write_json(core_path, core_payload)
        atomic_write_json(graph_path, graph_payload)
        snapshot_payloads = (core_path.read_bytes(), graph_path.read_bytes())
        snapshot_bytes = sum(len(payload) for payload in snapshot_payloads)
        snapshot_gzip_bytes = sum(
            len(gzip.compress(payload, mtime=0)) for payload in snapshot_payloads
        )
        if (
            snapshot_bytes > MAX_STATIC_SNAPSHOT_BYTES
            or snapshot_gzip_bytes > MAX_STATIC_SNAPSHOT_GZIP_BYTES
        ):
            raise ValueError(
                "Static explorer data exceeds the v0.13 delivery budget "
                f"({snapshot_bytes} raw bytes, {snapshot_gzip_bytes} gzip bytes); "
                "use a narrower export scope"
            )
        snapshot_files = {
            path.name: {
                "schema_version": payload["schema_version"],
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for path, payload in (
                (core_path, core_payload),
                (graph_path, graph_payload),
            )
        }
        manifest = {
            "schema_version": STATIC_EXPLORER_VERSION,
            "package_version": metadata.package_version,
            "source_graph": canonical_json_sha256(build_metadata),
            "graph_content_fingerprint": viewer_metadata[
                "graph_content_fingerprint"
            ],
            "input_profile": input_profile,
            "source_file_sha256": (
                build_input.get("sha256")
                if isinstance(build_input, dict)
                else build_metadata.get("input_hash")
            ),
            "normalized_semantic_fingerprint": (
                build_input.get("normalized_semantic_fingerprint")
                if isinstance(build_input, dict)
                else build_metadata.get("input_semantic_fingerprint")
            ),
            "discovery_semantics_fingerprint": build_metadata[
                "discovery_semantics_fingerprint"
            ],
            "source_tree_fingerprint": build_metadata[
                "source_tree_fingerprint"
            ],
            "client_fingerprint": client_fingerprint,
            "build_mode": build_metadata["build_mode"],
            "validation_status": build_metadata["validation_status"],
            "scope": scope,
            "identifier": identifier,
            "max_nodes": max_nodes,
            "max_edges": max_edges,
            "snapshot_hash": canonical_json_sha256(snapshot_files),
            "snapshot_bytes": snapshot_bytes,
            "snapshot_gzip_bytes": snapshot_gzip_bytes,
            "files": snapshot_files,
            "coverage": core_payload["coverage"],
            "capabilities": static_capabilities,
            "tournament_scope": core_payload["scope"],
            "display_stats": human_display_stats.model_dump(mode="json"),
            "derived_semantics_version": EXPLORER_DERIVED_SEMANTICS_VERSION,
            "essential_projection_version": ESSENTIAL_PROJECTION_VERSION,
            "data_format": "canonical-json-v1",
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


def _all_nodes(out_dir: Path, limit: int) -> list[str]:
    db = DuckDB(out_dir / "oddsfox_graph.duckdb", read_only=True)
    try:
        rows = db.rows(
            "SELECT proposition_id FROM explorer_propositions_v "
            "ORDER BY proposition_id LIMIT ?",
            [limit + 1],
        )
    finally:
        db.close()
    if len(rows) > limit:
        raise ValueError("Static graph export node selection exceeds max_nodes")
    if not rows:
        raise ValueError("Static graph export contains no claims")
    return [str(row["proposition_id"]) for row in rows]


def _static_human_rows(
    out_dir: Path,
    node_ids: tuple[str, ...],
    relationship_ids: tuple[str, ...],
) -> dict[str, object]:
    graph = Graph.open(out_dir)
    metadata = graph.metadata()
    db = DuckDB(out_dir / "oddsfox_graph.duckdb", read_only=True)
    try:
        explorer = HumanExplorer(
            db,
            coverage=graph.coverage(),
            build=metadata.build,
        )
        snapshot = explorer.snapshot(node_ids, relationship_ids)
    finally:
        db.close()
    rows: dict[str, object] = {}
    for name in ("stages", "teams", "claims", "groups"):
        rows[name] = [
            item.model_dump(mode="json")
            for item in snapshot[name]
        ]
    rows["markets"] = [
        item.model_dump(mode="json", exclude={"claims"})
        for item in snapshot["markets"]
    ]
    relationships = cast(
        tuple[RelationshipDetail, ...], snapshot["relationships"]
    )
    essential_relationships = cast(
        tuple[RelationshipDetail, ...], snapshot["essential_relationships"]
    )
    rows["scope"] = graph.explore_home(
        team_limit=100,
        highlight_limit=6,
    ).scope.model_dump(mode="json")
    rows["essential_relationship_ids"] = [
        item.proposal_id for item in essential_relationships
    ]
    rows["highlight_relationship_ids"] = list(
        human_highlight_ids(essential_relationships, limit=12)
    )
    for name, items in (
        ("relationships", relationships),
        ("essential_relationships", essential_relationships),
    ):
        rows[name] = [
            {
                **item.model_dump(mode="json", exclude={"source", "target"}),
                "source_id": item.source.id,
                "target_id": item.target.id,
            }
            for item in items
        ]
    return rows
