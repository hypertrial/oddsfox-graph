"""Typed read-only API for a completed OddsFox graph."""

from __future__ import annotations

import heapq
import itertools
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from . import __version__
from ._explorer.contracts import (
    CompareResult,
    ComponentDetail,
    ComponentSummary,
    EdgeMode,
    EntitySearchResult,
    ExploreHome,
    ExplorerMetadata,
    EventDetail,
    EventSummary,
    EvidenceTier,
    GraphFilter,
    GraphPage,
    GraphView,
    HumanHighlight,
    MarketDetail,
    RelationshipDetail,
    RecordingPlan,
    QuarantineSummary,
    StageDetail,
    StageSummary,
    TeamDetail,
    TeamSummary,
)
from ._explorer.queries import ExplorerStore
from ._discovery.provenance import sha256_file
from ._discovery.versions import (
    WC2026_SOURCE_SCHEMA,
    discovery_semantics_fingerprint,
)
from ._discovery.manifest_contracts import (
    BuildManifest,
    CoverageSummary,
    WC2026Scope,
    load_build_manifest,
    load_viewer_manifest,
    validate_manifest_pair,
)
from .queries import DuckDB
from .search import (
    PATH_SENTINEL,
    nodes_by_ids,
    read_rows,
    require_artifact,
    resolve_node,
    search_nodes,
)


Relation = Literal[
    "compatible",
    "complement",
    "equivalent",
    "implies",
    "mutually_exclusive",
]

_MAX_PROOF_HOPS = 8
_MAX_PROOF_PATHS = 20
_MAX_PROOF_OUTGOING = 10_000
_MAX_PROOF_STATES = 100_000


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    market_id: str
    outcome_index: int
    clob_token_id: str
    question: str
    outcome_label: str
    event_slug: str
    is_active: bool
    is_closed: bool
    market_family: str
    canonical_proposition: str
    proposition_type: str
    expected_tokens: int
    first_seen_ts: datetime | None = None
    last_seen_ts: datetime | None = None
    plain_claim: str | None = None


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    src_node_id: str
    dst_node_id: str
    edge_type: Relation
    edge_basis: str
    confidence: float
    market_id_src: str
    market_id_dst: str
    event_slug_src: str
    event_slug_dst: str
    evidence: str
    discovery_method: Literal["deterministic", "generative_consensus"]
    rule_version: str | None = None
    prompt_version: str | None = None
    explanation: str
    assumptions: tuple[str, ...] = ()
    rule_id: str | None = None
    proposal_id: str
    solver_version: str
    constraint_version: str
    solver_component_id: str
    primary_model_version: str | None = None
    verifier_model_version: str | None = None
    primary_assessment_id: str | None = None
    verifier_assessment_id: str | None = None
    primary_inference_fingerprint: str | None = None
    verifier_inference_fingerprint: str | None = None
    consensus_fingerprint: str | None = None
    automation_profile_id: str | None = None
    evidence_tier: EvidenceTier
    extractor_id: str | None = None
    extractor_version: str | None = None
    source_spans_json: str | None = None
    rule_applicability_fingerprint: str | None = None
    proof_scope_key: str | None = None


class NodeDetail(BaseModel):
    """One analyst node plus its bounded accepted relationships."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node: Node
    edges: tuple[Edge, ...]
    edges_truncated: bool = False


class ProofStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    src_node_id: str
    dst_node_id: str
    edge_type: Literal["implies", "equivalent"]
    confidence: float
    proposal_id: str


class Proof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    from_node_id: str
    to_node_id: str
    steps: tuple[ProofStep, ...]
    hops: int
    bottleneck_confidence: float


class Diagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal[
        "accepted",
        "solver_rejected",
        "quarantined_parse",
        "model_disagreement",
        "assumption",
        "invalid_citation",
        "nli_veto",
        "below_threshold",
        "inference_failure",
        "not_applicable_to_deterministic_rules",
        "full_mode_not_run",
        "deadline_budget_exhausted",
        "not_retrieved",
        "unknown_node",
    ]
    proposition_a_id: str | None = None
    proposition_b_id: str | None = None
    relation: str | None = None
    explanation: str
    candidate_reasons: tuple[str, ...] = ()
    provenance: tuple[JsonValue, ...] = ()


class Graph:
    """A manifest-complete, immutable graph output directory."""

    def __init__(self, out_dir: Path, *, manifest: BuildManifest) -> None:
        self.out_dir = out_dir
        self.build_mode = manifest.build_mode
        self._build_manifest = manifest

    @classmethod
    def open(cls, out_dir: str | Path) -> Graph:
        resolved = Path(out_dir).resolve()
        manifest_path = resolved / "build_manifest.json"
        if not manifest_path.is_file():
            raise ValueError(f"Graph output is incomplete: missing {manifest_path}")
        manifest = load_build_manifest(manifest_path)
        if manifest.version != __version__:
            raise ValueError(
                f"Graph output is incompatible; run a clean v{__version__} discovery"
            )
        if manifest.discovery_semantics_fingerprint != discovery_semantics_fingerprint():
            raise ValueError(
                "Graph output has an incompatible discovery semantics "
                "fingerprint; run a clean WC2026 discovery"
            )
        if (
            manifest.input.schema == WC2026_SOURCE_SCHEMA
            and not isinstance(manifest.scope, WC2026Scope)
        ):
            raise ValueError(
                "Graph output is not a complete WC2026 knockout-progression scope"
            )
        required = (
            "oddsfox_graph.duckdb",
            "nodes.parquet",
            "logic_edges.parquet",
            "conditional_edges.parquet",
            "relation_candidates.parquet",
            "rejected_edges.parquet",
            "quarantined_pairs.parquet",
            "event_summary.parquet",
            "event_relation_summary.parquet",
            "component_summary.parquet",
            "node_metrics.parquet",
            "visualization_layout.parquet",
            "coverage_summary.json",
            "viewer_manifest.json",
        )
        if any(artifact not in manifest.artifacts for artifact in required):
            raise ValueError("Graph manifest does not declare the query artifacts")
        for artifact in required:
            require_artifact(resolved, artifact)
        viewer = load_viewer_manifest(resolved / "viewer_manifest.json")
        validate_manifest_pair(manifest, viewer)
        coverage_path = resolved / "coverage_summary.json"
        try:
            coverage = CoverageSummary.model_validate_json(coverage_path.read_bytes())
        except (OSError, ValueError) as exc:
            raise ValueError(
                "Graph coverage artifacts are incompatible; run a clean "
                f"v{__version__} WC2026 discovery"
            ) from exc
        declared_coverage_hash = manifest.published_file_hashes.get(
            "coverage_summary.json"
        )
        if declared_coverage_hash != sha256_file(coverage_path):
            raise ValueError("Graph coverage summary hash does not match its build")
        if coverage.input_selection != manifest.input.selection:
            raise ValueError("Graph coverage selection does not match its build")
        declared_viewer_hash = manifest.published_file_hashes.get(
            "viewer_manifest.json"
        )
        if declared_viewer_hash != sha256_file(resolved / "viewer_manifest.json"):
            raise ValueError("Graph viewer manifest hash does not match its build")
        return cls(resolved, manifest=manifest)

    def metadata(self) -> ExplorerMetadata:
        return self._explorer().metadata()

    def coverage(self) -> CoverageSummary:
        return self._explorer().coverage()

    def explore_home(
        self,
        *,
        team_limit: int = 24,
        highlight_limit: int = 6,
    ) -> ExploreHome:
        return self._explorer().explore_home(
            team_limit=team_limit,
            highlight_limit=highlight_limit,
        )

    def stages(self) -> tuple[StageSummary, ...]:
        return self._explorer().stages()

    def stage(self, stage_key: str) -> StageDetail:
        return self._explorer().stage(stage_key)

    def teams(
        self,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> GraphPage[TeamSummary]:
        return self._explorer().teams(cursor=cursor, limit=limit)

    def team(self, team_key: str) -> TeamDetail:
        return self._explorer().team(team_key)

    def market(self, market_id: str) -> MarketDetail:
        return self._explorer().market(market_id)

    def relationship(self, proposal_id: str) -> RelationshipDetail:
        return self._explorer().relationship(proposal_id)

    def human_highlights(
        self,
        *,
        limit: int = 6,
        min_confidence: float = 0.95,
    ) -> tuple[HumanHighlight, ...]:
        return self._explorer().human_highlights(
            limit=limit, min_confidence=min_confidence
        )

    def entity_search(
        self, query: str, *, limit: int = 20
    ) -> tuple[EntitySearchResult, ...]:
        return self._explorer().entity_search(query, limit=limit)

    def compare(
        self, source_id: str, target_id: str, *, max_hops: int = 4
    ) -> CompareResult:
        return self._explorer().compare(
            source_id, target_id, max_hops=max_hops
        )

    def events(
        self,
        filters: GraphFilter | None = None,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> GraphPage[EventSummary]:
        return self._explorer().events(filters, cursor=cursor, limit=limit)

    def components(
        self,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> GraphPage[ComponentSummary]:
        return self._explorer().components(cursor=cursor, limit=limit)

    def overview(
        self,
        level: Literal["component", "event", "proposition"] = "event",
        filters: GraphFilter | None = None,
        *,
        max_nodes: int = 5_000,
        max_edges: int = 10_000,
        edge_mode: EdgeMode = "all",
    ) -> GraphView:
        return self._explorer().overview(
            level,
            filters,
            max_nodes=max_nodes,
            max_edges=max_edges,
            edge_mode=edge_mode,
        )

    def neighborhood(
        self,
        node_ids: tuple[str, ...],
        *,
        hops: int = 1,
        filters: GraphFilter | None = None,
        max_nodes: int = 5_000,
        max_edges: int = 10_000,
        edge_mode: EdgeMode = "all",
    ) -> GraphView:
        return self._explorer().neighborhood(
            node_ids,
            hops=hops,
            filters=filters,
            max_nodes=max_nodes,
            max_edges=max_edges,
            edge_mode=edge_mode,
        )

    def subgraph(
        self,
        node_ids: tuple[str, ...],
        *,
        hops: int = 1,
        filters: GraphFilter | None = None,
        max_nodes: int = 5_000,
        max_edges: int = 10_000,
        edge_mode: EdgeMode = "all",
    ) -> GraphView:
        return self.neighborhood(
            node_ids,
            hops=hops,
            filters=filters,
            max_nodes=max_nodes,
            max_edges=max_edges,
            edge_mode=edge_mode,
        )

    def recording_plan(
        self,
        limit: int = 6,
        min_confidence: float = 0.95,
    ) -> RecordingPlan:
        """Build a deterministic, balanced recording plan for this graph."""

        return self._explorer().recording_plan(
            limit=limit,
            min_confidence=min_confidence,
        )

    def event(self, event_key: str) -> EventDetail:
        return self._explorer().event(event_key)

    def event_graph(
        self,
        event_key: str,
        filters: GraphFilter | None = None,
        *,
        max_nodes: int = 5_000,
        max_edges: int = 10_000,
        edge_mode: EdgeMode = "all",
    ) -> GraphView:
        return self._explorer().event_graph(
            event_key,
            filters,
            max_nodes=max_nodes,
            max_edges=max_edges,
            edge_mode=edge_mode,
        )

    def component(self, component_id: str) -> ComponentDetail:
        return self._explorer().component(component_id)

    def component_graph(
        self,
        component_id: str,
        filters: GraphFilter | None = None,
        *,
        max_nodes: int = 5_000,
        max_edges: int = 10_000,
    ) -> GraphView:
        return self._explorer().component_graph(
            component_id,
            filters,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )

    def diagnostics(
        self,
        *,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> GraphPage[QuarantineSummary]:
        return self._explorer().diagnostics(
            status=status,
            cursor=cursor,
            limit=limit,
        )

    def accepted_proposal(self, proposal_id: str) -> dict[str, object]:
        return self._explorer().edge(proposal_id)

    def search(self, query: str, top: int = 20) -> tuple[Node, ...]:
        if top <= 0:
            return ()
        legacy_rows = search_nodes(self.out_dir, query, top)
        input_profile = self._build_manifest.input.schema
        if input_profile != WC2026_SOURCE_SCHEMA:
            return tuple(Node.model_validate(row) for row in legacy_rows)
        claim_matches = (
            self._explorer().claim_search(query, limit=min(top, 100))
            if query.strip()
            else ()
        )
        candidate_ids = list(
            dict.fromkeys(
                [claim.id for claim in claim_matches]
                + [str(row["node_id"]) for row in legacy_rows]
            )
        )
        normalized_query = query.strip().casefold()
        ordered_ids = [
            *(
                node_id
                for node_id in candidate_ids
                if node_id.casefold() == normalized_query
            ),
            *(
                node_id
                for node_id in candidate_ids
                if node_id.casefold() != normalized_query
            ),
        ]
        rows_by_id = {
            str(row["node_id"]): row
            for row in (*legacy_rows, *nodes_by_ids(self.out_dir, ordered_ids))
        }
        plain_claims = self._explorer().plain_claims(tuple(ordered_ids))
        result: list[Node] = []
        seen_labels: set[str] = set()
        for node_id in ordered_ids:
            row = rows_by_id[node_id]
            plain_claim = plain_claims.get(node_id)
            display_label = str(plain_claim or row["canonical_proposition"]).casefold()
            if display_label in seen_labels and node_id.casefold() != normalized_query:
                continue
            seen_labels.add(display_label)
            result.append(
                Node.model_validate({**row, "plain_claim": plain_claim})
            )
            if len(result) == top:
                break
        return tuple(result)

    def nodes(self, top: int = 50) -> tuple[Node, ...]:
        rows = read_rows(
            self.out_dir,
            "nodes.parquet",
            f"""
            SELECT * FROM read_parquet('{PATH_SENTINEL}')
            ORDER BY event_slug, market_id, outcome_index
            LIMIT {int(top)}
            """,
        )
        return tuple(Node.model_validate(row) for row in rows)

    def edges(
        self,
        edge_type: Relation | None = None,
        top: int = 50,
    ) -> tuple[Edge, ...]:
        where = "WHERE edge_type = ?" if edge_type else ""
        rows = read_rows(
            self.out_dir,
            "logic_edges.parquet",
            f"""
            SELECT * FROM read_parquet('{PATH_SENTINEL}')
            {where}
            ORDER BY edge_basis, edge_type, src_node_id, dst_node_id
            LIMIT {int(top)}
            """,
            [edge_type] if edge_type else None,
        )
        return tuple(Edge.model_validate(row) for row in rows)

    def condition(self, a: str, b: str) -> tuple[dict[str, object], ...]:
        a_id = self._resolve(a)
        b_id = self._resolve(b)
        return tuple(
            read_rows(
                self.out_dir,
                "conditional_edges.parquet",
                f"""
                SELECT * FROM read_parquet('{PATH_SENTINEL}')
                WHERE a_node_id = ? AND b_node_id = ?
                ORDER BY method, a_node_id, b_node_id
                """,
                [a_id, b_id],
            )
        )

    def explain_node(
        self,
        node: str,
        *,
        edge_limit: int | None = None,
    ) -> dict[str, object]:
        if edge_limit is not None and not 0 <= edge_limit <= 10_000:
            raise ValueError("edge_limit must be between 0 and 10000")
        node_id = self._resolve(node)
        node_rows = read_rows(
            self.out_dir,
            "nodes.parquet",
            f"SELECT * FROM read_parquet('{PATH_SENTINEL}') WHERE node_id = ?",
            [node_id],
        )
        limit_sql = f"LIMIT {edge_limit + 1}" if edge_limit is not None else ""
        touching_rows = read_rows(
            self.out_dir,
            "logic_edges.parquet",
            f"""
            SELECT * FROM read_parquet('{PATH_SENTINEL}')
            WHERE src_node_id = ? OR dst_node_id = ?
            ORDER BY edge_type, src_node_id, dst_node_id
            {limit_sql}
            """,
            [node_id, node_id],
        )
        truncated = edge_limit is not None and len(touching_rows) > edge_limit
        touching = (
            touching_rows[:edge_limit]
            if edge_limit is not None
            else touching_rows
        )
        result: dict[str, object] = {"node": node_rows[0], "edges": touching}
        if edge_limit is not None:
            result["edges_truncated"] = truncated
        return result

    def explain_edge(self, src: str, dst: str, relation: Relation) -> dict[str, object]:
        src_id = self._resolve(src)
        dst_id = self._resolve(dst)
        symmetric = relation != "implies"
        where = (
            "((src_node_id = ? AND dst_node_id = ?) OR "
            "(src_node_id = ? AND dst_node_id = ?))"
            if symmetric
            else "src_node_id = ? AND dst_node_id = ?"
        )
        params: list[object] = [src_id, dst_id]
        if symmetric:
            params.extend((dst_id, src_id))
        params.append(relation)
        rows = read_rows(
            self.out_dir,
            "logic_edges.parquet",
            f"""
            SELECT * FROM read_parquet('{PATH_SENTINEL}')
            WHERE {where} AND edge_type = ?
            ORDER BY confidence DESC, proposal_id
            """,
            params,
        )
        return {"src_node_id": src_id, "dst_node_id": dst_id, "relation": relation, "edges": rows}

    def prove(
        self,
        from_node: str,
        to_node: str,
        *,
        max_hops: int = 4,
        max_paths: int = 3,
    ) -> tuple[Proof, ...]:
        if not 1 <= max_hops <= _MAX_PROOF_HOPS:
            raise ValueError(
                f"max_hops must be between 1 and {_MAX_PROOF_HOPS}"
            )
        if not 1 <= max_paths <= _MAX_PROOF_PATHS:
            raise ValueError(
                f"max_paths must be between 1 and {_MAX_PROOF_PATHS}"
            )
        source = self._resolve(from_node)
        target = self._resolve(to_node)
        db = DuckDB(self.out_dir / "oddsfox_graph.duckdb", read_only=True)
        adjacency: dict[str, tuple[ProofStep, ...]] = {}

        def outgoing(node_id: str) -> tuple[ProofStep, ...]:
            cached = adjacency.get(node_id)
            if cached is not None:
                return cached
            rows = db.rows(
                """
                SELECT src_node_id, dst_node_id, edge_type, confidence, proposal_id
                FROM (
                    SELECT src_node_id, dst_node_id, edge_type,
                           confidence, proposal_id
                    FROM logic_edges_v
                    WHERE src_node_id = ?
                      AND edge_type IN ('implies', 'equivalent')
                    UNION ALL
                    SELECT dst_node_id AS src_node_id,
                           src_node_id AS dst_node_id,
                           edge_type, confidence, proposal_id
                    FROM logic_edges_v
                    WHERE dst_node_id = ? AND edge_type = 'equivalent'
                ) traversable
                ORDER BY dst_node_id, edge_type, proposal_id
                LIMIT ?
                """,
                [node_id, node_id, _MAX_PROOF_OUTGOING + 1],
            )
            if len(rows) > _MAX_PROOF_OUTGOING:
                raise ValueError(
                    f"Proof expansion for {node_id!r} exceeds the "
                    f"{_MAX_PROOF_OUTGOING}-edge safety cap"
                )
            result = tuple(ProofStep.model_validate(row) for row in rows)
            adjacency[node_id] = result
            return result
        counter = itertools.count()
        queue: list[
            tuple[
                int,
                float,
                tuple[str, ...],
                int,
                str,
                tuple[ProofStep, ...],
                frozenset[str],
            ]
        ] = [(0, -1.0, (source,), next(counter), source, (), frozenset({source}))]
        proofs: list[Proof] = []
        examined_states = 0
        generated_states = 1
        try:
            while queue and len(proofs) < max_paths:
                examined_states += 1
                if examined_states > _MAX_PROOF_STATES:
                    raise ValueError(
                        "Proof traversal exceeded the bounded search-state limit"
                    )
                hops, negative_bottleneck, path_nodes, _, node, path, visited = heapq.heappop(
                    queue
                )
                if node == target and path:
                    proofs.append(
                        Proof(
                            from_node_id=source,
                            to_node_id=target,
                            steps=path,
                            hops=hops,
                            bottleneck_confidence=-negative_bottleneck,
                        )
                    )
                    continue
                if hops >= max_hops:
                    continue
                for step in outgoing(node):
                    if step.dst_node_id in visited:
                        continue
                    next_path = (*path, step)
                    bottleneck = min(-negative_bottleneck, step.confidence)
                    generated_states += 1
                    if generated_states > _MAX_PROOF_STATES:
                        raise ValueError(
                            "Proof traversal exceeded the bounded search-state limit"
                        )
                    heapq.heappush(
                        queue,
                        (
                            hops + 1,
                            -bottleneck,
                            (*path_nodes, step.dst_node_id),
                            next(counter),
                            step.dst_node_id,
                            next_path,
                            visited | {step.dst_node_id},
                        ),
                    )
        finally:
            db.close()
        return tuple(proofs)

    def why_not(self, a: str, b: str, relation: Relation) -> Diagnostic:
        a_id = resolve_node(self.out_dir, a, require_unique=True)
        b_id = resolve_node(self.out_dir, b, require_unique=True)
        if a_id is None or b_id is None:
            return Diagnostic(status="unknown_node", relation=relation, explanation="One or both nodes do not exist")
        accepted = self.explain_edge(a_id, b_id, relation)["edges"]
        if accepted:
            candidates = _pair_rows(
                self.out_dir,
                "relation_candidates.parquet",
                a_id,
                b_id,
                None,
            )
            return Diagnostic.model_validate(
                {
                    "status": "accepted",
                    "proposition_a_id": a_id,
                    "proposition_b_id": b_id,
                    "relation": relation,
                    "explanation": "The relation is published",
                    "provenance": accepted,
                    "candidate_reasons": (
                        candidates[0].get("candidate_reasons") if candidates else []
                    ),
                }
            )
        rejected = _pair_rows(self.out_dir, "rejected_edges.parquet", a_id, b_id, relation)
        if rejected:
            return Diagnostic.model_validate({"status": "solver_rejected", "proposition_a_id": a_id, "proposition_b_id": b_id, "relation": relation, "explanation": str(rejected[0].get("rejection_reason") or "Rejected by solver"), "provenance": rejected})
        parse_quarantine = read_rows(
            self.out_dir,
            "quarantined_pairs.parquet",
            f"""
            SELECT * FROM read_parquet('{PATH_SENTINEL}')
            WHERE stage = 'parse' AND proposition_a_id IN (?, ?)
            ORDER BY quarantine_id
            LIMIT 20
            """,
            [a_id, b_id],
        )
        if parse_quarantine:
            return Diagnostic.model_validate(
                {
                    "status": "quarantined_parse",
                    "proposition_a_id": a_id,
                    "proposition_b_id": b_id,
                    "relation": relation,
                    "explanation": str(
                        parse_quarantine[0].get("explanation")
                        or "One or both propositions failed parse consensus"
                    ),
                    "provenance": parse_quarantine,
                }
            )
        quarantined = _pair_rows(self.out_dir, "quarantined_pairs.parquet", a_id, b_id, None)
        if quarantined:
            reason = str(quarantined[0].get("reason_code") or "inference_failure")
            status_map = {
                "model_disagreement": "model_disagreement",
                "assumption": "assumption",
                "invalid_citation": "invalid_citation",
                "nli_veto": "nli_veto",
                "below_threshold": "below_threshold",
                "qualification_mismatch": "below_threshold",
                "inference_failure": "inference_failure",
                "authoritative_conflict": "quarantined_parse",
                "missing_model_parse": "quarantined_parse",
            }
            status = status_map.get(reason, "inference_failure")
            return Diagnostic.model_validate({"status": status, "proposition_a_id": a_id, "proposition_b_id": b_id, "relation": relation, "explanation": str(quarantined[0].get("explanation") or reason), "provenance": quarantined})
        candidates = _pair_rows(self.out_dir, "relation_candidates.parquet", a_id, b_id, None)
        if candidates:
            if self.build_mode == "fast":
                return Diagnostic.model_validate(
                    {
                        "status": "not_applicable_to_deterministic_rules",
                        "proposition_a_id": a_id,
                        "proposition_b_id": b_id,
                        "relation": relation,
                        "explanation": "The pair has a deterministic proof, but not for the requested relation",
                        "candidate_reasons": candidates[0].get("candidate_reasons"),
                        "provenance": candidates,
                    }
                )
            candidate_status = str(candidates[0].get("status") or "")
            if candidate_status == "deadline_budget_exhausted":
                status = "deadline_budget_exhausted"
                explanation = "The pair was retrieved but the full-mode scheduling cutoff was reached"
            elif candidate_status == "not_classified_budget":
                status = "below_threshold"
                explanation = "The pair was retrieved but was outside the bounded inference budget"
            else:
                status = "below_threshold"
                explanation = "The pair was retrieved but did not produce the requested relation"
            return Diagnostic.model_validate({"status": status, "proposition_a_id": a_id, "proposition_b_id": b_id, "relation": relation, "explanation": explanation, "candidate_reasons": candidates[0].get("candidate_reasons"), "provenance": candidates})
        if self.build_mode == "fast":
            if relation == "compatible":
                return Diagnostic(
                    status="full_mode_not_run",
                    proposition_a_id=a_id,
                    proposition_b_id=b_id,
                    relation=relation,
                    explanation="Fast mode does not run semantic consensus",
                )
            return Diagnostic(
                status="not_applicable_to_deterministic_rules",
                proposition_a_id=a_id,
                proposition_b_id=b_id,
                relation=relation,
                explanation="No exact deterministic rule applies to this pair",
            )
        return Diagnostic(status="not_retrieved", proposition_a_id=a_id, proposition_b_id=b_id, relation=relation, explanation="The pair was not generated by retrieval")

    def _resolve(self, text: str) -> str:
        result = resolve_node(self.out_dir, text, require_unique=True)
        if result is None:
            raise ValueError(f"Could not resolve node {text!r}")
        return result

    def _explorer(self) -> ExplorerStore:
        return ExplorerStore(self.out_dir)

def _pair_rows(
    out_dir: Path,
    artifact: str,
    a_id: str,
    b_id: str,
    relation: str | None,
) -> list[dict[str, object]]:
    if artifact == "relation_candidates.parquet":
        source_column, target_column = "proposition_a_id", "proposition_b_id"
        relation_column = "classification_relation"
    elif artifact == "quarantined_pairs.parquet":
        source_column, target_column = "proposition_a_id", "proposition_b_id"
        relation_column = "proposed_relation"
    else:
        source_column, target_column = "src_node_id", "dst_node_id"
        relation_column = "edge_type"
    symmetric = relation != "implies"
    pair_filter = f"{source_column} = ? AND {target_column} = ?"
    params: list[object] = [a_id, b_id]
    if symmetric:
        pair_filter = f"(({pair_filter}) OR ({source_column} = ? AND {target_column} = ?))"
        params.extend((b_id, a_id))
    relation_filter = f" AND {relation_column} = ?" if relation else ""
    if relation:
        params.append(relation)
    return read_rows(
        out_dir,
        artifact,
        f"""
        SELECT * FROM read_parquet('{PATH_SENTINEL}')
        WHERE {pair_filter}{relation_filter}
        LIMIT 20
        """,
        params,
    )
