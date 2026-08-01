"""Loopback-only HTTP service for the packaged graph explorer."""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from .._discovery.provenance import canonical_json_sha256, sha256_file
from .._discovery.versions import WC2026_SOURCE_SCHEMA
from .contracts import EdgeMode, EvidenceTier, GraphFilter
from ..graph import Graph, Relation


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error: str
    detail: str


def validate_loopback_host(host: str) -> str:
    normalized = host.strip().removeprefix("[").removesuffix("]")
    if normalized == "localhost":
        return normalized
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError as exc:
        raise ValueError("Explorer host must be a loopback address") from exc
    if not address.is_loopback:
        raise ValueError("Explorer host must be a loopback address")
    return normalized


def create_app(
    out_dir: Path,
    *,
    max_response_nodes: int = 5_000,
    max_response_edges: int = 10_000,
) -> FastAPI:
    graph = Graph.open(out_dir)
    if not 1 <= max_response_nodes <= 5_000:
        raise ValueError("max_response_nodes must be between 1 and 5000")
    if not 0 <= max_response_edges <= 10_000:
        raise ValueError("max_response_edges must be between 0 and 10000")
    metadata = graph.metadata()
    build_input = metadata.build.get("input")
    input_profile = (
        build_input.get("schema")
        if isinstance(build_input, dict)
        else metadata.build.get("input_schema")
    )
    if input_profile != WC2026_SOURCE_SCHEMA:
        raise ValueError(
            "The FIFA World Cup 2026 Outcome Map requires a graph built with "
            "--input-profile polymarket-wc2026-graph-hourly-v1"
        )
    static_directory = Path(__file__).resolve().parents[1] / "static" / "explorer"
    client_fingerprint = (
        canonical_json_sha256(
            [
                {
                    "path": path.relative_to(static_directory).as_posix(),
                    "sha256": sha256_file(path),
                }
                for path in sorted(static_directory.rglob("*"))
                if path.is_file()
            ]
        )
        if static_directory.is_dir()
        else "missing"
    )
    fingerprint = canonical_json_sha256(
        {
            "graph": metadata.viewer.get("graph_content_fingerprint") or "unknown",
            "build_manifest": sha256_file(out_dir.resolve() / "build_manifest.json"),
            "client": client_fingerprint,
        }
    )
    etag = f'"{fingerprint}"'
    app = FastAPI(
        title="FIFA World Cup 2026 Outcome Map",
        version=metadata.package_version,
        docs_url=None,
        openapi_url="/api/openapi.json",
    )

    @app.middleware("http")
    async def security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method == "GET" and request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        response = await call_next(request)
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(KeyError)
    async def missing(_: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(error="not_found", detail=str(exc)).model_dump(),
        )

    @app.exception_handler(ValueError)
    async def invalid(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(error="invalid_request", detail=str(exc)).model_dump(),
        )

    @app.get("/api/v1/meta")
    def get_meta() -> dict[str, object]:
        payload = metadata.model_dump(mode="json")
        viewer = payload["viewer"]
        if isinstance(viewer, dict):
            viewer["client_fingerprint"] = client_fingerprint
        return payload

    @app.get("/api/v1/coverage")
    def get_coverage() -> dict[str, object]:
        return graph.coverage()

    @app.get("/api/v1/explore")
    def explore(
        team_limit: int = Query(default=24, ge=1, le=100),
        highlight_limit: int = Query(default=6, ge=1, le=12),
    ) -> dict[str, object]:
        return graph.explore_home(
            team_limit=team_limit,
            highlight_limit=highlight_limit,
        ).model_dump(mode="json")

    @app.get("/api/v1/stages")
    def stages() -> list[dict[str, object]]:
        return [row.model_dump(mode="json") for row in graph.stages()]

    @app.get("/api/v1/stages/{stage_key}")
    def stage(stage_key: str) -> dict[str, object]:
        return graph.stage(stage_key).model_dump(mode="json")

    @app.get("/api/v1/teams")
    def teams(
        cursor: str | None = None,
        limit: int = Query(default=100, ge=1, le=1_000),
    ) -> dict[str, object]:
        return graph.teams(cursor=cursor, limit=limit).model_dump(mode="json")

    @app.get("/api/v1/teams/{team_key}")
    def team(team_key: str) -> dict[str, object]:
        return graph.team(team_key).model_dump(mode="json")

    @app.get("/api/v1/markets/{market_id}")
    def market(market_id: str) -> dict[str, object]:
        return graph.market(market_id).model_dump(mode="json")

    @app.get("/api/v1/relationships/{proposal_id}")
    def relationship(proposal_id: str) -> dict[str, object]:
        return graph.relationship(proposal_id).model_dump(mode="json")

    @app.get("/api/v1/entity-search")
    def entity_search(
        q: str,
        limit: int = Query(default=20, ge=1, le=100),
    ) -> list[dict[str, object]]:
        return [
            row.model_dump(mode="json")
            for row in graph.entity_search(q, limit=limit)
        ]

    @app.get("/api/v1/compare")
    def compare(
        a: str,
        b: str,
        max_hops: int = Query(default=4, ge=1, le=4),
    ) -> dict[str, object]:
        return graph.compare(a, b, max_hops=max_hops).model_dump(mode="json")

    @app.get("/api/v1/highlights")
    def highlights(
        limit: int = Query(default=6, ge=1, le=12),
        min_confidence: float = Query(default=0.95, ge=0.0, le=1.0),
    ) -> list[dict[str, object]]:
        return [
            row.model_dump(mode="json")
            for row in graph.human_highlights(
                limit=limit,
                min_confidence=min_confidence,
            )
        ]

    @app.get("/api/v1/recording-plan")
    def recording_plan(
        limit: int = Query(default=6, ge=1, le=12),
        min_confidence: float = Query(default=0.95, ge=0.0, le=1.0),
    ) -> dict[str, object]:
        return graph.recording_plan(
            limit=limit,
            min_confidence=min_confidence,
        ).model_dump(mode="json")

    @app.get("/api/v1/search")
    def search(q: str, limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, object]]:
        return [row.model_dump(mode="json") for row in graph.search(q, limit)]

    @app.get("/api/v1/overview")
    def overview(
        level: Literal["component", "event", "proposition"] = "event",
        domains: list[str] = Query(default=[]),
        relations: list[Relation] = Query(default=[]),
        min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
        active_only: bool = False,
        closed_only: bool = False,
        include_compatible: bool = False,
        evidence_tiers: list[EvidenceTier] = Query(default=[]),
        edge_mode: EdgeMode = "all",
        max_nodes: int = Query(default=max_response_nodes, ge=1, le=max_response_nodes),
        max_edges: int = Query(default=max_response_edges, ge=0, le=max_response_edges),
    ) -> dict[str, object]:
        filters = GraphFilter(
            domains=tuple(domains),
            relations=tuple(relations),
            min_confidence=min_confidence,
            active_only=active_only,
            closed_only=closed_only,
            include_compatible=include_compatible,
            evidence_tiers=tuple(evidence_tiers),
        )
        return graph.overview(
            level,
            filters,
            max_nodes=max_nodes,
            max_edges=max_edges,
            edge_mode=edge_mode,
        ).model_dump(mode="json")

    @app.get("/api/v1/events")
    def events(
        cursor: str | None = None,
        limit: int = Query(default=100, ge=1, le=1_000),
        domains: list[str] = Query(default=[]),
        active_only: bool = False,
        closed_only: bool = False,
    ) -> dict[str, object]:
        return graph.events(
            GraphFilter(
                domains=tuple(domains),
                active_only=active_only,
                closed_only=closed_only,
            ),
            cursor=cursor,
            limit=limit,
        ).model_dump(mode="json")

    @app.get("/api/v1/events/{event_key:path}")
    def event(event_key: str) -> dict[str, object]:
        return graph.event(event_key)

    @app.get("/api/v1/event-graph/{event_key:path}")
    def event_graph(
        event_key: str,
        relations: list[Relation] = Query(default=[]),
        min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
        include_compatible: bool = False,
        evidence_tiers: list[EvidenceTier] = Query(default=[]),
        edge_mode: EdgeMode = "all",
    ) -> dict[str, object]:
        return graph.event_graph(
            event_key,
            GraphFilter(
                relations=tuple(relations),
                min_confidence=min_confidence,
                include_compatible=include_compatible,
                evidence_tiers=tuple(evidence_tiers),
            ),
            max_nodes=max_response_nodes,
            max_edges=max_response_edges,
            edge_mode=edge_mode,
        ).model_dump(mode="json")

    @app.get("/api/v1/component-graph/{component_id}")
    def component_graph(
        component_id: str,
        relations: list[Relation] = Query(default=[]),
        min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
        include_compatible: bool = False,
        evidence_tiers: list[EvidenceTier] = Query(default=[]),
    ) -> dict[str, object]:
        return graph.component_graph(
            component_id,
            GraphFilter(
                relations=tuple(relations),
                min_confidence=min_confidence,
                include_compatible=include_compatible,
                evidence_tiers=tuple(evidence_tiers),
            ),
            max_nodes=max_response_nodes,
            max_edges=max_response_edges,
        ).model_dump(mode="json")

    @app.get("/api/v1/components")
    def components(
        cursor: str | None = None,
        limit: int = Query(default=100, ge=1, le=1_000),
    ) -> dict[str, object]:
        return graph.components(cursor=cursor, limit=limit).model_dump(mode="json")

    @app.get("/api/v1/components/{component_id}")
    def component(component_id: str) -> dict[str, object]:
        return graph.component(component_id)

    @app.get("/api/v1/subgraph")
    def subgraph(
        node: list[str] = Query(min_length=1),
        hops: int = Query(default=1, ge=0, le=4),
        relations: list[Relation] = Query(default=[]),
        min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
        include_compatible: bool = False,
        evidence_tiers: list[EvidenceTier] = Query(default=[]),
        max_nodes: int = Query(default=max_response_nodes, ge=1, le=max_response_nodes),
        max_edges: int = Query(default=max_response_edges, ge=0, le=max_response_edges),
        edge_mode: EdgeMode = "all",
    ) -> dict[str, object]:
        return graph.subgraph(
            tuple(node),
            hops=hops,
            filters=GraphFilter(
                relations=tuple(relations),
                min_confidence=min_confidence,
                include_compatible=include_compatible,
                evidence_tiers=tuple(evidence_tiers),
            ),
            max_nodes=max_nodes,
            max_edges=max_edges,
            edge_mode=edge_mode,
        ).model_dump(mode="json")

    @app.get("/api/v1/nodes/{node_id}")
    def node(node_id: str) -> dict[str, object]:
        return graph.explain_node(node_id, edge_limit=max_response_edges)

    @app.get("/api/v1/edges/{proposal_id}")
    def edge(proposal_id: str) -> dict[str, object]:
        return graph.accepted_proposal(proposal_id)

    @app.get("/api/v1/prove")
    def prove(
        from_node: str,
        to_node: str,
        max_hops: int = Query(default=4, ge=1, le=8),
        max_paths: int = Query(default=3, ge=1, le=20),
    ) -> list[dict[str, object]]:
        return [
            row.model_dump(mode="json")
            for row in graph.prove(
                from_node,
                to_node,
                max_hops=max_hops,
                max_paths=max_paths,
            )
        ]

    @app.get("/api/v1/why-not")
    def why_not(a: str, b: str, relation: Relation) -> dict[str, object]:
        return graph.why_not(a, b, relation).model_dump(mode="json")

    @app.get("/api/v1/diagnostics")
    def diagnostics(
        status: str | None = None,
        cursor: str | None = None,
        limit: int = Query(default=100, ge=1, le=1_000),
    ) -> dict[str, object]:
        return graph.diagnostics(
            status=status,
            cursor=cursor,
            limit=limit,
        ).model_dump(mode="json")

    if static_directory.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=static_directory, html=True),
            name="explorer",
        )
    return app
