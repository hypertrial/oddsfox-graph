"""Per-event LLM inference orchestration."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

from oddsfox_graph.config import Settings
from oddsfox_graph.llm import LLMInferenceError, LocalGraphLLM
from oddsfox_graph.prompts import build_event_prompt, chunk_markets_for_prompt
from oddsfox_graph.schema import Edge, GraphFragment, Node, SemanticMarket

logger = logging.getLogger(__name__)


def _fragment_path(settings: Settings, event_id: str) -> Path:
    return settings.fragments_dir / f"{event_id}.json"


def _part_fragment_path(settings: Settings, event_id: str, part: int) -> Path:
    return settings.fragments_dir / f"{event_id}__part{part}.json"


def _load_fragment(path: Path) -> GraphFragment:
    data = json.loads(path.read_text(encoding="utf-8"))
    return GraphFragment.model_validate(data)


def _save_fragment(path: Path, fragment: GraphFragment) -> None:
    path.write_text(fragment.model_dump_json(indent=2), encoding="utf-8")


def _merge_fragments(fragments: list[GraphFragment]) -> GraphFragment:
    nodes_by_id: dict[str, Node] = {}
    edges_seen: set[tuple[str, str, str]] = set()
    edges: list = []

    for fragment in fragments:
        for node in fragment.nodes:
            existing = nodes_by_id.get(node.local_id)
            if existing is None:
                nodes_by_id[node.local_id] = node
            else:
                merged_evidence = sorted(
                    set(existing.evidence_market_ids) | set(node.evidence_market_ids)
                )
                merged_aliases = sorted(set(existing.aliases) | set(node.aliases))
                nodes_by_id[node.local_id] = node.model_copy(
                    update={
                        "confidence": max(existing.confidence, node.confidence),
                        "evidence_market_ids": merged_evidence,
                        "aliases": merged_aliases,
                    }
                )
        for edge in fragment.edges:
            key = (edge.source, edge.target, edge.type.value)
            if key in edges_seen:
                continue
            edges_seen.add(key)
            edges.append(edge)

    return GraphFragment(nodes=list(nodes_by_id.values()), edges=edges)


def infer_event_fragments(
    settings: Settings,
    markets: list[SemanticMarket],
    llm: LocalGraphLLM | None = None,
) -> dict[str, GraphFragment]:
    settings.ensure_dirs()
    by_event: dict[str, list[SemanticMarket]] = defaultdict(list)
    for market in markets:
        by_event[market.event_id].append(market)

    event_ids = list(by_event.keys())
    if settings.event_ids:
        event_ids = [eid for eid in settings.event_ids if eid in by_event]
    if settings.limit_events is not None:
        event_ids = event_ids[: settings.limit_events]

    llm = llm or LocalGraphLLM(settings)
    results: dict[str, GraphFragment] = {}
    status: dict[str, str] = {}

    for event_id in event_ids:
        fragment_path = _fragment_path(settings, event_id)
        if settings.resume and fragment_path.exists():
            results[event_id] = _load_fragment(fragment_path)
            status[event_id] = "skipped"
            continue

        event_markets = by_event[event_id]
        chunks = chunk_markets_for_prompt(
            event_markets,
            event_id,
            settings.chunk_token_budget,
            settings.chunk_output_token_budget,
            settings.max_markets_per_chunk,
            settings.max_text_field_chars,
        )
        chunk_fragments: list[GraphFragment] = []

        try:
            for idx, chunk in enumerate(chunks):
                part_path = _part_fragment_path(settings, event_id, idx)
                if settings.resume and part_path.exists():
                    chunk_fragments.append(_load_fragment(part_path))
                    continue
                prompt = build_event_prompt(
                    event_id, chunk, settings.max_text_field_chars
                )
                fragment = llm.generate_fragment(prompt, event_id)
                _save_fragment(part_path, fragment)
                chunk_fragments.append(fragment)

            merged = _merge_fragments(chunk_fragments)
            _save_fragment(fragment_path, merged)
            results[event_id] = merged
            status[event_id] = "success"
        except LLMInferenceError:
            status[event_id] = "failed"
            logger.error("Inference failed for event %s", event_id)

    existing_status: dict[str, str] = {}
    if settings.inference_report_path.exists():
        try:
            data = json.loads(settings.inference_report_path.read_text(encoding="utf-8"))
            existing_status = data.get("per_event_status", {})
        except json.JSONDecodeError:
            existing_status = {}

    merged_status = {**existing_status, **status}
    settings.inference_report_path.write_text(
        json.dumps({"per_event_status": merged_status}, indent=2),
        encoding="utf-8",
    )
    return results


def load_all_fragments(settings: Settings) -> dict[str, GraphFragment]:
    fragments: dict[str, GraphFragment] = {}
    for path in sorted(settings.fragments_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        if "__part" in path.name:
            continue
        event_id = path.stem
        fragments[event_id] = _load_fragment(path)
    return fragments
