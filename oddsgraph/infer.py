"""Per-event LLM inference orchestration."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from oddsgraph.config import Settings
from oddsgraph.llm import BaseGraphLLM, LLMInferenceError, build_graph_llm
from oddsgraph.prompts import (
    build_event_prompt,
    chunk_markets_for_prompt,
    estimate_output_tokens,
)
from oddsgraph.reduce import list_semantic_market_event_ids, load_semantic_markets, select_event_ids
from oddsgraph.reporting import merge_per_event_status
from oddsgraph.schema import Edge, GraphFragment, Node, SemanticMarket

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _InferTask:
    event_id: str
    chunk_index: int
    prompt: str
    max_tokens: int
    part_path: Path


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


def _chunk_max_tokens(settings: Settings, chunk_size: int) -> int:
    estimated = int(estimate_output_tokens(chunk_size) * 1.5)
    return min(settings.max_tokens, max(1, estimated))


def _effective_concurrency(settings: Settings) -> int:
    if settings.llm_backend == "server":
        return max(1, settings.llm_concurrency)
    if settings.llm_concurrency > 1:
        logger.warning(
            "llm_concurrency=%d ignored for inprocess backend; using 1",
            settings.llm_concurrency,
        )
    return 1


def _run_infer_task(llm: BaseGraphLLM, task: _InferTask) -> None:
    fragment = llm.generate_fragment(
        task.prompt,
        task.event_id,
        max_tokens_override=task.max_tokens,
    )
    _save_fragment(task.part_path, fragment)


def infer_event_fragments(
    settings: Settings,
    markets: list[SemanticMarket],
    llm: BaseGraphLLM | None = None,
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

    llm = llm or build_graph_llm(settings)
    results: dict[str, GraphFragment] = {}
    status: dict[str, str] = {}

    event_chunks: dict[str, list[list[SemanticMarket]]] = {}
    pending_tasks: list[_InferTask] = []

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
            settings.n_ctx,
            settings.chunk_context_safety_margin,
        )
        event_chunks[event_id] = chunks

        for idx, chunk in enumerate(chunks):
            part_path = _part_fragment_path(settings, event_id, idx)
            if settings.resume and part_path.exists():
                continue
            prompt = build_event_prompt(
                event_id, chunk, settings.max_text_field_chars
            )
            pending_tasks.append(
                _InferTask(
                    event_id=event_id,
                    chunk_index=idx,
                    prompt=prompt,
                    max_tokens=_chunk_max_tokens(settings, len(chunk)),
                    part_path=part_path,
                )
            )

    failed_events: set[str] = set()
    if pending_tasks:
        concurrency = _effective_concurrency(settings)
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_task = {
                executor.submit(_run_infer_task, llm, task): task
                for task in pending_tasks
            }
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    future.result()
                except LLMInferenceError:
                    failed_events.add(task.event_id)
                    logger.error(
                        "Inference failed for event %s chunk %d",
                        task.event_id,
                        task.chunk_index,
                    )

    for event_id in event_ids:
        if event_id in results:
            continue
        if event_id in failed_events:
            status[event_id] = "failed"
            continue

        chunks = event_chunks.get(event_id, [])
        chunk_fragments: list[GraphFragment] = []
        missing_part = False
        for idx in range(len(chunks)):
            part_path = _part_fragment_path(settings, event_id, idx)
            if not part_path.exists():
                missing_part = True
                break
            chunk_fragments.append(_load_fragment(part_path))

        if missing_part:
            status[event_id] = "failed"
            logger.error("Missing part fragments for event %s after inference", event_id)
            continue

        merged = _merge_fragments(chunk_fragments)
        fragment_path = _fragment_path(settings, event_id)
        _save_fragment(fragment_path, merged)
        results[event_id] = merged
        status[event_id] = "success"

    merge_per_event_status(settings.inference_report_path, status)
    return results


def load_markets_for_infer(settings: Settings) -> list[SemanticMarket]:
    path = settings.semantic_markets_path
    if not settings.event_ids and settings.limit_events is None:
        return load_semantic_markets(path)

    available_event_ids = list_semantic_market_event_ids(path)
    target_event_ids = select_event_ids(
        available_event_ids,
        settings.event_ids,
        settings.limit_events,
    )
    return load_semantic_markets(path, event_ids=target_event_ids)


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
