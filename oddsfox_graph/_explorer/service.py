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
from .contracts import EvidenceTier, GraphFilter
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
    static_directory = Path(__file__).resolve().parents[1] / "static" / "explorer"
    client_fingerprint = (
        sha256_file(static_directory / "index.html")
        if (static_directory / "index.html").is_file()
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
        title="OddsFox Logic Explorer",
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
        level: Literal["component", "event"] = "event",
        domains: list[str] = Query(default=[]),
        relations: list[Relation] = Query(default=[]),
        min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
        active_only: bool = False,
        closed_only: bool = False,
        include_compatible: bool = False,
        evidence_tiers: list[EvidenceTier] = Query(default=[]),
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
