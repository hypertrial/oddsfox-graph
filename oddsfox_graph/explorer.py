"""Public facade for serving and exporting the local graph explorer."""

from __future__ import annotations

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
from ._discovery.versions import WC2026_SOURCE_SCHEMA
from ._discovery.bulk import create_and_fill
from fastapi import FastAPI

from ._explorer.service import create_app, validate_loopback_host
from ._explorer.human import HumanExplorer, graph_display_stats
from ._explorer.contracts import RelationshipDetail
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
    "classification_status": "VARCHAR",
}

STATIC_STAGE_COLUMNS = {
    "stage_key": "VARCHAR",
    "label": "VARCHAR",
    "stage_rank": "BIGINT",
    "normalized_progression_level": "BIGINT",
    "team_count": "BIGINT",
    "market_count": "BIGINT",
    "claim_count": "BIGINT",
    "active_market_count": "BIGINT",
    "closed_market_count": "BIGINT",
    "classification_eligible_count": "BIGINT",
    "classification_assessed_count": "BIGINT",
    "classification_status": "VARCHAR",
    "classification_coverage": "DOUBLE",
}

STATIC_TEAM_COLUMNS = {
    "team_key": "VARCHAR",
    "canonical_team_name": "VARCHAR",
    "is_still_alive": "BOOLEAN",
    "market_status": "VARCHAR",
    "market_count": "BIGINT",
    "claim_count": "BIGINT",
    "stage_keys": "VARCHAR[]",
    "min_stage_rank": "BIGINT",
    "max_stage_rank": "BIGINT",
    "classification_eligible_count": "BIGINT",
    "classification_assessed_count": "BIGINT",
    "classification_status": "VARCHAR",
    "classification_coverage": "DOUBLE",
}

STATIC_MARKET_COLUMNS = {
    "market_id": "VARCHAR",
    "event_slug": "VARCHAR",
    "question": "VARCHAR",
    "canonical_team_name": "VARCHAR",
    "stage_key": "VARCHAR",
    "stage_rank": "BIGINT",
    "normalized_progression_level": "BIGINT",
    "market_direction": "VARCHAR",
    "market_status": "VARCHAR",
    "is_still_alive": "BOOLEAN",
}

STATIC_CLAIM_COLUMNS = {
    "id": "VARCHAR",
    "market_id": "VARCHAR",
    "canonical_team_name": "VARCHAR",
    "stage_key": "VARCHAR",
    "stage_rank": "BIGINT",
    "normalized_progression_level": "BIGINT",
    "question": "VARCHAR",
    "answer": "VARCHAR",
    "plain_claim": "VARCHAR",
    "is_progression_token": "BOOLEAN",
    "market_status": "VARCHAR",
    "is_still_alive": "BOOLEAN",
    "technical_canonical_label": "VARCHAR",
}

STATIC_RELATIONSHIP_COLUMNS = {
    "proposal_id": "VARCHAR",
    "source_id": "VARCHAR",
    "target_id": "VARCHAR",
    "relation": "VARCHAR",
    "basis": "VARCHAR",
    "confidence": "DOUBLE",
    "evidence_tier": "VARCHAR",
    "discovery_method": "VARCHAR",
    "explanation": "VARCHAR",
}

STATIC_RELATIONSHIP_GROUP_COLUMNS = {
    "id": "VARCHAR",
    "title": "VARCHAR",
    "description": "VARCHAR",
    "relation": "VARCHAR",
    "member_claim_ids": "VARCHAR[]",
    "relationship_count": "BIGINT",
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
    metadata = graph.metadata()
    build_input = metadata.build.get("input")
    input_profile = (
        build_input.get("schema")
        if isinstance(build_input, dict)
        else metadata.build.get("input_schema")
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
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
    elif scope == "neighborhood":
        assert identifier is not None
        view = graph.neighborhood(
            (identifier,),
            hops=2,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
    elif scope == "event":
        assert identifier is not None
        node_ids = _selected_nodes(source, "event_key", [identifier], max_nodes)
        view = graph.neighborhood(
            tuple(node_ids),
            hops=1,
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
        human_rows = _static_human_rows(source, tuple(node.id for node in view.nodes))
        human_display_stats = graph_display_stats(
            tuple(str(row["plain_claim"]) for row in human_rows["claims"]),
            tuple(
                (str(row["source_id"]), str(row["target_id"]))
                for row in human_rows["relationships"]
            ),
            input_edge_count=len(view.edges),
        )
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
            static_tables = {
                "snapshot_nodes": (STATIC_NODE_COLUMNS, [node.model_dump(mode="json") for node in view.nodes]),
                "snapshot_edges": (STATIC_EDGE_COLUMNS, [edge.model_dump(mode="json") for edge in view.edges]),
                "snapshot_stages": (STATIC_STAGE_COLUMNS, human_rows["stages"]),
                "snapshot_teams": (STATIC_TEAM_COLUMNS, human_rows["teams"]),
                "snapshot_markets": (STATIC_MARKET_COLUMNS, human_rows["markets"]),
                "snapshot_claims": (STATIC_CLAIM_COLUMNS, human_rows["claims"]),
                "snapshot_relationships": (STATIC_RELATIONSHIP_COLUMNS, human_rows["relationships"]),
                "snapshot_relationship_groups": (STATIC_RELATIONSHIP_GROUP_COLUMNS, human_rows["groups"]),
            }
            for table, (columns, rows) in static_tables.items():
                if table not in {"snapshot_nodes", "snapshot_edges"}:
                    create_and_fill(snapshot_db, table, columns, rows)
                snapshot_db.execute(
                    f"COPY {table} TO '{q(staging / f'{table}.parquet')}' "
                    "(FORMAT PARQUET, COMPRESSION ZSTD)"
                )
        finally:
            snapshot_db.close()
        manifest = {
            "schema_version": "static-explorer-v3",
            "package_version": metadata.package_version,
            "source_graph": canonical_json_sha256(metadata.build),
            "graph_content_fingerprint": metadata.viewer[
                "graph_content_fingerprint"
            ],
            "input_profile": input_profile,
            "source_file_sha256": (
                build_input.get("sha256")
                if isinstance(build_input, dict)
                else metadata.build.get("input_hash")
            ),
            "normalized_semantic_fingerprint": (
                build_input.get("normalized_semantic_fingerprint")
                if isinstance(build_input, dict)
                else metadata.build.get("input_semantic_fingerprint")
            ),
            "discovery_semantics_fingerprint": metadata.build[
                "discovery_semantics_fingerprint"
            ],
            "source_tree_fingerprint": metadata.build[
                "source_tree_fingerprint"
            ],
            "client_fingerprint": client_fingerprint,
            "build_mode": metadata.build["build_mode"],
            "validation_status": metadata.build["validation_status"],
            "scope": scope,
            "identifier": identifier,
            "max_nodes": max_nodes,
            "max_edges": max_edges,
            "snapshot_hash": canonical_json_sha256(snapshot),
            "snapshot_files": {
                name: sha256_file(staging / name)
                for name in sorted(
                    path.name for path in staging.glob("snapshot_*.parquet")
                )
            },
            "coverage": graph.coverage(),
            "capabilities": {
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
            },
            "tournament_scope": graph.explore_home(
                team_limit=100,
                highlight_limit=6,
            ).scope.model_dump(mode="json"),
            "display_stats": human_display_stats.model_dump(mode="json"),
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
) -> dict[str, list[dict[str, object]]]:
    db = DuckDB(out_dir / "oddsfox_graph.duckdb", read_only=True)
    try:
        snapshot = HumanExplorer(
            db,
            coverage=Graph.open(out_dir).coverage(),
            build=Graph.open(out_dir).metadata().build,
        ).snapshot(node_ids)
    finally:
        db.close()
    rows: dict[str, list[dict[str, object]]] = {}
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
    rows["relationships"] = [
        {
            **item.model_dump(mode="json", exclude={"source", "target"}),
            "source_id": item.source.id,
            "target_id": item.target.id,
        }
        for item in relationships
    ]
    return rows
