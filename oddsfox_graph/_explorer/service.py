"""Loopback-only HTTP service for the packaged graph explorer."""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from .._discovery.provenance import canonical_json_sha256, sha256_file
from .._discovery.versions import WC2026_SOURCE_SCHEMA
from .contracts import (
    CompareResult,
    ComponentDetail,
    ComponentSummary,
    EdgeMode,
    EntitySearchResult,
    EventDetail,
    EventSummary,
    EvidenceTier,
    ExploreHome,
    ExplorerMetadata,
    GraphFilter,
    GraphPage,
    GraphView,
    HumanHighlight,
    MarketDetail,
    QuarantineSummary,
    RecordingPlan,
    RelationshipDetail,
    StageDetail,
    StageSummary,
    TeamDetail,
    TeamSummary,
)
from .. import __version__
from .._discovery.manifest_contracts import CoverageSummary
from ..graph import Diagnostic, Edge, Graph, Node, NodeDetail, Proof, Relation


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
    input_profile = metadata.build.input.schema
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
            "graph": metadata.viewer.graph_content_fingerprint,
            "build_manifest": sha256_file(out_dir.resolve() / "build_manifest.json"),
            "client": client_fingerprint,
        }
    )
    etag = f'"{fingerprint}"'
    return _create_http_app(
        graph=graph,
        metadata=metadata,
        client_fingerprint=client_fingerprint,
        etag=etag,
        static_directory=static_directory,
        max_response_nodes=max_response_nodes,
        max_response_edges=max_response_edges,
    )


def create_schema_app() -> FastAPI:
    """Build the route table without opening an operator graph directory."""

    return _create_http_app(
        graph=cast(Graph, object()),
        metadata=None,
        client_fingerprint="0" * 64,
        etag='"schema"',
        static_directory=None,
        max_response_nodes=5_000,
        max_response_edges=10_000,
    )


def _create_http_app(
    *,
    graph: Graph,
    metadata: ExplorerMetadata | None,
    client_fingerprint: str,
    etag: str,
    static_directory: Path | None,
    max_response_nodes: int,
    max_response_edges: int,
) -> FastAPI:
    app = FastAPI(
        title="FIFA World Cup 2026 Outcome Map",
        version=metadata.package_version if metadata is not None else __version__,
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
            "default-src 'self'; script-src 'self'; "
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
    def get_meta() -> ExplorerMetadata:
        if metadata is None:  # pragma: no cover - schema-only app is never served
            raise RuntimeError("Schema-only explorer has no runtime metadata")
        return metadata.model_copy(update={"client_fingerprint": client_fingerprint})

    @app.get("/api/v1/coverage")
    def get_coverage() -> CoverageSummary:
        return graph.coverage()

    @app.get("/api/v1/explore")
    def explore(
        team_limit: int = Query(default=24, ge=1, le=100),
        highlight_limit: int = Query(default=6, ge=1, le=12),
    ) -> ExploreHome:
        return graph.explore_home(
            team_limit=team_limit,
            highlight_limit=highlight_limit,
        )

    @app.get("/api/v1/stages")
    def stages() -> list[StageSummary]:
        return list(graph.stages())

    @app.get("/api/v1/stages/{stage_key}")
    def stage(stage_key: str) -> StageDetail:
        return graph.stage(stage_key)

    @app.get("/api/v1/teams")
    def teams(
        cursor: str | None = None,
        limit: int = Query(default=100, ge=1, le=1_000),
    ) -> GraphPage[TeamSummary]:
        return graph.teams(cursor=cursor, limit=limit)

    @app.get("/api/v1/teams/{team_key}")
    def team(team_key: str) -> TeamDetail:
        return graph.team(team_key)

    @app.get("/api/v1/markets/{market_id}")
    def market(market_id: str) -> MarketDetail:
        return graph.market(market_id)

    @app.get("/api/v1/relationships/{proposal_id}")
    def relationship(proposal_id: str) -> RelationshipDetail:
        return graph.relationship(proposal_id)

    @app.get("/api/v1/entity-search")
    def entity_search(
        q: str,
        limit: int = Query(default=20, ge=1, le=100),
    ) -> list[EntitySearchResult]:
        return list(graph.entity_search(q, limit=limit))

    @app.get("/api/v1/compare")
    def compare(
        a: str,
        b: str,
        max_hops: int = Query(default=4, ge=1, le=4),
    ) -> CompareResult:
        return graph.compare(a, b, max_hops=max_hops)

    @app.get("/api/v1/highlights")
    def highlights(
        limit: int = Query(default=6, ge=1, le=12),
        min_confidence: float = Query(default=0.95, ge=0.0, le=1.0),
    ) -> list[HumanHighlight]:
        return list(
            graph.human_highlights(
                limit=limit, min_confidence=min_confidence
            )
        )

    @app.get("/api/v1/recording-plan")
    def recording_plan(
        limit: int = Query(default=6, ge=1, le=12),
        min_confidence: float = Query(default=0.95, ge=0.0, le=1.0),
    ) -> RecordingPlan:
        return graph.recording_plan(
            limit=limit,
            min_confidence=min_confidence,
        )

    @app.get("/api/v1/search")
    def search(q: str, limit: int = Query(default=20, ge=1, le=100)) -> list[Node]:
        return list(graph.search(q, limit))

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
    ) -> GraphView:
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
        )

    @app.get("/api/v1/events")
    def events(
        cursor: str | None = None,
        limit: int = Query(default=100, ge=1, le=1_000),
        domains: list[str] = Query(default=[]),
        active_only: bool = False,
        closed_only: bool = False,
    ) -> GraphPage[EventSummary]:
        return graph.events(
            GraphFilter(
                domains=tuple(domains),
                active_only=active_only,
                closed_only=closed_only,
            ),
            cursor=cursor,
            limit=limit,
        )

    @app.get("/api/v1/events/{event_key:path}")
    def event(event_key: str) -> EventDetail:
        return graph.event(event_key)

    @app.get("/api/v1/event-graph/{event_key:path}")
    def event_graph(
        event_key: str,
        relations: list[Relation] = Query(default=[]),
        min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
        include_compatible: bool = False,
        evidence_tiers: list[EvidenceTier] = Query(default=[]),
        edge_mode: EdgeMode = "all",
    ) -> GraphView:
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
        )

    @app.get("/api/v1/component-graph/{component_id}")
    def component_graph(
        component_id: str,
        relations: list[Relation] = Query(default=[]),
        min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
        include_compatible: bool = False,
        evidence_tiers: list[EvidenceTier] = Query(default=[]),
    ) -> GraphView:
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
        )

    @app.get("/api/v1/components")
    def components(
        cursor: str | None = None,
        limit: int = Query(default=100, ge=1, le=1_000),
    ) -> GraphPage[ComponentSummary]:
        return graph.components(cursor=cursor, limit=limit)

    @app.get("/api/v1/components/{component_id}")
    def component(component_id: str) -> ComponentDetail:
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
    ) -> GraphView:
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
        )

    @app.get("/api/v1/nodes/{node_id}")
    def node(node_id: str) -> NodeDetail:
        return NodeDetail.model_validate(
            graph.explain_node(node_id, edge_limit=max_response_edges)
        )

    @app.get("/api/v1/edges/{proposal_id}")
    def edge(proposal_id: str) -> Edge:
        return Edge.model_validate(graph.accepted_proposal(proposal_id))

    @app.get("/api/v1/prove")
    def prove(
        from_node: str,
        to_node: str,
        max_hops: int = Query(default=4, ge=1, le=8),
        max_paths: int = Query(default=3, ge=1, le=20),
    ) -> list[Proof]:
        return list(
            graph.prove(
                from_node, to_node, max_hops=max_hops, max_paths=max_paths
            )
        )

    @app.get("/api/v1/why-not")
    def why_not(a: str, b: str, relation: Relation) -> Diagnostic:
        return graph.why_not(a, b, relation)

    @app.get("/api/v1/diagnostics")
    def diagnostics(
        status: str | None = None,
        cursor: str | None = None,
        limit: int = Query(default=100, ge=1, le=1_000),
    ) -> GraphPage[QuarantineSummary]:
        return graph.diagnostics(
            status=status,
            cursor=cursor,
            limit=limit,
        )

    if static_directory is not None and static_directory.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=static_directory, html=True),
            name="explorer",
        )
    return app
