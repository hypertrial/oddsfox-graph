"""Typed read-only API for a completed OddsFox graph."""

from __future__ import annotations

import json
import heapq
import itertools
from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from . import __version__
from .search import PATH_SENTINEL, read_rows, require_artifact, resolve_node, search_nodes


Relation = Literal[
    "compatible",
    "complement",
    "equivalent",
    "implies",
    "mutually_exclusive",
]


class Node(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    node_id: str
    market_id: str
    outcome_label: str
    event_slug: str
    canonical_proposition: str


class Edge(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    src_node_id: str
    dst_node_id: str
    edge_type: Relation
    edge_basis: str
    confidence: float
    evidence: str
    discovery_method: Literal["deterministic", "generative_consensus"]


class ProofStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    src_node_id: str
    dst_node_id: str
    edge_type: Literal["implies", "equivalent"]
    confidence: float
    proposal_id: str


class Proof(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_node_id: str
    to_node_id: str
    steps: tuple[ProofStep, ...]
    hops: int
    bottleneck_confidence: float


class Diagnostic(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

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
        "not_retrieved",
        "unknown_node",
    ]
    proposition_a_id: str | None = None
    proposition_b_id: str | None = None
    relation: str | None = None
    explanation: str


class Graph:
    """A manifest-complete, immutable graph output directory."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir

    @classmethod
    def open(cls, out_dir: str | Path) -> Graph:
        resolved = Path(out_dir).resolve()
        manifest_path = resolved / "build_manifest.json"
        if not manifest_path.is_file():
            raise ValueError(f"Graph output is incomplete: missing {manifest_path}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Graph manifest is invalid: {exc}") from exc
        if not isinstance(manifest, dict):
            raise ValueError("Graph manifest must be a JSON object")
        if manifest.get("command") != "discover":
            raise ValueError("Graph manifest is not a discovery output")
        if manifest.get("version") != __version__:
            raise ValueError(
                "Graph output is incompatible; run a clean v0.9 discovery"
            )
        required = (
            "nodes.parquet",
            "logic_edges.parquet",
            "conditional_edges.parquet",
            "relation_candidates.parquet",
            "rejected_edges.parquet",
            "quarantined_pairs.parquet",
        )
        declared = manifest.get("artifacts")
        if not isinstance(declared, list) or any(
            artifact not in declared for artifact in required
        ):
            raise ValueError("Graph manifest does not declare the query artifacts")
        for artifact in required:
            require_artifact(resolved, artifact)
        return cls(resolved)

    def search(self, query: str, top: int = 20) -> tuple[Node, ...]:
        return tuple(Node.model_validate(row) for row in search_nodes(self.out_dir, query, top))

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

    def explain_node(self, node: str) -> dict[str, object]:
        node_id = self._resolve(node)
        node_rows = read_rows(
            self.out_dir,
            "nodes.parquet",
            f"SELECT * FROM read_parquet('{PATH_SENTINEL}') WHERE node_id = ?",
            [node_id],
        )
        touching = read_rows(
            self.out_dir,
            "logic_edges.parquet",
            f"""
            SELECT * FROM read_parquet('{PATH_SENTINEL}')
            WHERE src_node_id = ? OR dst_node_id = ?
            ORDER BY edge_type, src_node_id, dst_node_id
            """,
            [node_id, node_id],
        )
        return {"node": node_rows[0], "edges": touching}

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
        if max_hops < 1 or max_paths < 1:
            raise ValueError("max_hops and max_paths must be positive")
        source = self._resolve(from_node)
        target = self._resolve(to_node)
        rows = read_rows(
            self.out_dir,
            "logic_edges.parquet",
            f"""
            SELECT src_node_id, dst_node_id, edge_type, confidence, proposal_id
            FROM read_parquet('{PATH_SENTINEL}')
            WHERE edge_type IN ('implies', 'equivalent')
            ORDER BY src_node_id, dst_node_id, edge_type, proposal_id
            """,
        )
        adjacency: dict[str, list[ProofStep]] = defaultdict(list)
        for row in rows:
            step = ProofStep.model_validate(row)
            adjacency[step.src_node_id].append(step)
            if step.edge_type == "equivalent":
                adjacency[step.dst_node_id].append(
                    step.model_copy(
                        update={
                            "src_node_id": step.dst_node_id,
                            "dst_node_id": step.src_node_id,
                        }
                    )
                )
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
        while queue and len(proofs) < max_paths:
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
            for step in adjacency.get(node, []):
                if step.dst_node_id in visited:
                    continue
                next_path = (*path, step)
                bottleneck = min(-negative_bottleneck, step.confidence)
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
            candidate_status = str(candidates[0].get("status") or "")
            explanation = (
                "The pair was retrieved but was outside the bounded inference budget"
                if candidate_status == "not_classified_budget"
                else "The pair was retrieved but did not produce the requested relation"
            )
            return Diagnostic.model_validate({"status": "below_threshold", "proposition_a_id": a_id, "proposition_b_id": b_id, "relation": relation, "explanation": explanation, "candidate_reasons": candidates[0].get("candidate_reasons"), "provenance": candidates})
        return Diagnostic(status="not_retrieved", proposition_a_id=a_id, proposition_b_id=b_id, relation=relation, explanation="The pair was not generated by retrieval")

    def _resolve(self, text: str) -> str:
        result = resolve_node(self.out_dir, text, require_unique=True)
        if result is None:
            raise ValueError(f"Could not resolve node {text!r}")
        return result


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
