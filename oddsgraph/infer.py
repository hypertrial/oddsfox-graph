"""Per-event LLM inference orchestration."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oddsgraph.config import Settings
from oddsgraph.llm import BaseGraphLLM, build_graph_llm
from oddsgraph.paths import event_artifact_path, sanitize_event_id_for_path
from oddsgraph.prompts import (
    build_event_prompt,
    build_verification_prompt,
    chunk_markets_for_prompt,
    estimate_output_tokens,
    select_exemplars,
)
from oddsgraph.reduce import list_semantic_market_event_ids, load_semantic_markets, select_event_ids
from oddsgraph.reporting import merge_per_event_status
from oddsgraph.schema import GraphFragment, SemanticMarket, merge_fragments
from oddsgraph.topology import classify_events, covered_event_ids

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FragmentLoadResult:
    fragments: dict[str, GraphFragment]
    verified_event_ids: set[str]


@dataclass
class _InferTask:
    event_id: str
    chunk_index: int
    markets: list[SemanticMarket]
    few_shot_exemplars: list[dict]
    max_text_field_chars: int
    max_tokens: int
    part_path: Path


@dataclass(frozen=True)
class _VerifyTask:
    event_id: str
    markets: list[SemanticMarket]
    candidate: GraphFragment
    max_text_field_chars: int


def _fragment_path(settings: Settings, event_id: str) -> Path:
    return event_artifact_path(settings.fragments_dir, event_id, ".json")


def _part_fragment_path(settings: Settings, event_id: str, part: int) -> Path:
    return event_artifact_path(
        settings.fragments_dir, event_id, f"__part{part}.json"
    )


def _chunk_manifest_path(settings: Settings, event_id: str) -> Path:
    return event_artifact_path(
        settings.fragments_dir, event_id, "__chunk_manifest.json"
    )


def _verified_fragment_path(settings: Settings, event_id: str) -> Path:
    return event_artifact_path(
        settings.fragments_dir, event_id, "__verified.json"
    )


def _verify_manifest_path(settings: Settings, event_id: str) -> Path:
    return event_artifact_path(
        settings.fragments_dir, event_id, "__verify_manifest.json"
    )


def _load_fragment(path: Path) -> GraphFragment:
    data = json.loads(path.read_text(encoding="utf-8"))
    return GraphFragment.model_validate(data)


def _save_fragment(path: Path, fragment: GraphFragment) -> None:
    path.write_text(fragment.model_dump_json(indent=2), encoding="utf-8")


def _build_chunk_manifest(
    settings: Settings,
    chunks: list[list[SemanticMarket]],
) -> dict:
    return {
        "chunk_count": len(chunks),
        "chunks": [[market.market_id for market in chunk] for chunk in chunks],
        "max_markets_per_chunk": settings.max_markets_per_chunk,
        "chunk_token_budget": settings.chunk_token_budget,
        "chunk_output_token_budget": settings.chunk_output_token_budget,
        "max_text_field_chars": settings.max_text_field_chars,
        "n_ctx": settings.n_ctx,
        "chunk_context_safety_margin": settings.chunk_context_safety_margin,
    }


def _chunk_manifest_matches(
    settings: Settings,
    event_id: str,
    chunks: list[list[SemanticMarket]],
) -> bool:
    path = _chunk_manifest_path(settings, event_id)
    if not path.exists():
        return False
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return saved == _build_chunk_manifest(settings, chunks)


def _save_chunk_manifest(
    settings: Settings,
    event_id: str,
    chunks: list[list[SemanticMarket]],
) -> None:
    path = _chunk_manifest_path(settings, event_id)
    path.write_text(
        json.dumps(_build_chunk_manifest(settings, chunks), indent=2),
        encoding="utf-8",
    )


def _clear_part_fragments(settings: Settings, event_id: str) -> None:
    safe = sanitize_event_id_for_path(event_id)
    for path in settings.fragments_dir.glob(f"{safe}__part*.json"):
        path.unlink()
    manifest_path = _chunk_manifest_path(settings, safe)
    if manifest_path.exists():
        manifest_path.unlink()


def _chunk_max_tokens(settings: Settings, chunk_size: int) -> int:
    estimated = int(estimate_output_tokens(chunk_size) * 1.5)
    return min(settings.max_tokens, max(1, estimated))


def _effective_concurrency(settings: Settings) -> int:
    if settings.llm_backend == "server":
        return max(1, settings.llm_concurrency)
    if settings.llm_concurrency > 1:
        logger.warning(
            "llm_concurrency=%d ignored for %s backend; using 1",
            settings.llm_concurrency,
            settings.llm_backend,
        )
    return 1


def _fragments_equal(a: GraphFragment, b: GraphFragment) -> bool:
    a_nodes = {
        (
            n.local_id,
            n.type.value,
            n.label,
            tuple(sorted(n.aliases)),
            tuple(sorted(n.evidence_market_ids)),
        )
        for n in a.nodes
    }
    b_nodes = {
        (
            n.local_id,
            n.type.value,
            n.label,
            tuple(sorted(n.aliases)),
            tuple(sorted(n.evidence_market_ids)),
        )
        for n in b.nodes
    }
    a_edges = {
        (
            e.source,
            e.target,
            e.type.value,
            tuple(sorted(e.evidence_market_ids)),
            e.evidence_text,
        )
        for e in a.edges
    }
    b_edges = {
        (
            e.source,
            e.target,
            e.type.value,
            tuple(sorted(e.evidence_market_ids)),
            e.evidence_text,
        )
        for e in b.edges
    }
    return a_nodes == b_nodes and a_edges == b_edges


def _fragment_fingerprint(fragment: GraphFragment) -> str:
    """Stable hash of the equality-relevant fragment topology."""
    payload: dict[str, Any] = {
        "nodes": sorted(
            (
                n.local_id,
                n.type.value,
                n.label,
                tuple(sorted(n.aliases)),
                tuple(sorted(n.evidence_market_ids)),
            )
            for n in fragment.nodes
        ),
        "edges": sorted(
            (
                e.source,
                e.target,
                e.type.value,
                tuple(sorted(e.evidence_market_ids)),
                e.evidence_text,
            )
            for e in fragment.edges
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _save_verify_manifest(
    settings: Settings,
    event_id: str,
    candidate: GraphFragment,
    status: str,
) -> None:
    path = _verify_manifest_path(settings, event_id)
    path.write_text(
        json.dumps(
            {
                "candidate_fingerprint": _fragment_fingerprint(candidate),
                "status": status,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _resume_verified_fragment(
    settings: Settings,
    event_id: str,
    candidate: GraphFragment,
) -> tuple[GraphFragment, str] | None:
    """Return (fragment, status) when resume artifacts match the candidate topology."""
    verified_path = _verified_fragment_path(settings, event_id)
    manifest_path = _verify_manifest_path(settings, event_id)
    if not (settings.resume and verified_path.exists() and manifest_path.exists()):
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if manifest.get("candidate_fingerprint") != _fragment_fingerprint(candidate):
        return None
    status = manifest.get("status")
    if status not in {"deterministic_verified", "deterministic_corrected"}:
        status = (
            "deterministic_verified"
            if _fragments_equal(_load_fragment(verified_path), candidate)
            else "deterministic_corrected"
        )
    return _load_fragment(verified_path), status


def _run_infer_task(llm: BaseGraphLLM, task: _InferTask) -> None:
    prompt = build_event_prompt(
        task.event_id,
        task.markets,
        task.max_text_field_chars,
        few_shot_exemplars=task.few_shot_exemplars,
    )
    fragment = llm.generate_fragment(
        prompt,
        task.event_id,
        max_tokens_override=task.max_tokens,
    )
    _save_fragment(task.part_path, fragment)


def verify_deterministic_fragments(
    settings: Settings,
    by_event: dict[str, list[SemanticMarket]],
    covered: set[str],
    llm: BaseGraphLLM | None,
) -> tuple[dict[str, GraphFragment], dict[str, str]]:
    """LLM confirm/patch pass over deterministic topology (opt-in)."""
    if not settings.verify_deterministic or not covered:
        return {}, {}

    classified = classify_events(
        [m for eid in covered for m in by_event.get(eid, [])],
        competition_label=settings.competition_label,
    )
    tasks: list[_VerifyTask] = []
    verified: dict[str, GraphFragment] = {}
    status: dict[str, str] = {}

    for event_id in sorted(covered):
        topology = classified.get(event_id)
        if topology is None or not topology.fragment.nodes:
            continue
        resumed = _resume_verified_fragment(
            settings, event_id, topology.fragment
        )
        if resumed is not None:
            loaded, event_status = resumed
            verified[event_id] = loaded
            status[event_id] = event_status
            continue
        markets = by_event[event_id]
        tasks.append(
            _VerifyTask(
                event_id=event_id,
                markets=markets,
                candidate=topology.fragment,
                max_text_field_chars=settings.max_text_field_chars,
            )
        )

    if not tasks:
        return verified, status
    if llm is None:
        llm = build_graph_llm(settings)

    concurrency = _effective_concurrency(settings)

    def _run_verify(task: _VerifyTask) -> tuple[str, GraphFragment, str]:
        prompt = build_verification_prompt(
            task.event_id,
            task.markets,
            task.candidate,
            task.max_text_field_chars,
        )
        fragment = llm.generate_fragment(
            prompt,
            task.event_id,
            max_tokens_override=settings.verify_max_tokens,
        )
        if _fragments_equal(fragment, task.candidate):
            return task.event_id, task.candidate, "deterministic_verified"
        return task.event_id, fragment, "deterministic_corrected"

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_task = {
            executor.submit(_run_verify, task): task for task in tasks
        }
        for future in as_completed(future_to_task):
            task = future_to_task[future]
            try:
                event_id, fragment, event_status = future.result()
            except Exception:
                logger.exception(
                    "Verification failed for deterministic event %s", task.event_id
                )
                status[task.event_id] = "deterministic"
                continue
            verified[event_id] = fragment
            status[event_id] = event_status
            _save_fragment(_verified_fragment_path(settings, event_id), fragment)
            _save_verify_manifest(
                settings, event_id, task.candidate, event_status
            )

    return verified, status


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

    covered: set[str] = set()
    if settings.deterministic_topology:
        covered = covered_event_ids(
            [m for m in markets if m.event_id in set(event_ids)],
            competition_label=settings.competition_label,
        )

    results: dict[str, GraphFragment] = {}
    status: dict[str, str] = {}

    event_chunks: dict[str, list[list[SemanticMarket]]] = {}
    pending_tasks: list[_InferTask] = []

    # Reserve prompt budget for few-shot exemplar blocks when enabled.
    few_shot_token_reserve = 0
    if settings.use_few_shot_exemplars:
        few_shot_token_reserve = max(0, settings.few_shot_top_k) * 700
    chunk_token_budget = max(512, settings.chunk_token_budget - few_shot_token_reserve)

    for event_id in event_ids:
        if event_id in covered:
            if not settings.verify_deterministic:
                status[event_id] = "deterministic"
            continue

        fragment_path = _fragment_path(settings, event_id)
        if settings.resume and fragment_path.exists():
            results[event_id] = _load_fragment(fragment_path)
            status[event_id] = "skipped"
            continue

        event_markets = by_event[event_id]
        chunks = chunk_markets_for_prompt(
            event_markets,
            event_id,
            chunk_token_budget,
            settings.chunk_output_token_budget,
            settings.max_markets_per_chunk,
            settings.max_text_field_chars,
            settings.n_ctx,
            settings.chunk_context_safety_margin,
        )
        event_chunks[event_id] = chunks

        if settings.resume and not _chunk_manifest_matches(settings, event_id, chunks):
            _clear_part_fragments(settings, event_id)

        few_shot: list[dict] = []
        if settings.use_few_shot_exemplars:
            few_shot = select_exemplars(
                event_markets[0].event_title if event_markets else None,
                event_markets[0].question if event_markets else None,
                top_k=settings.few_shot_top_k,
            )

        for idx, chunk in enumerate(chunks):
            part_path = _part_fragment_path(settings, event_id, idx)
            if settings.resume and part_path.exists():
                continue
            pending_tasks.append(
                _InferTask(
                    event_id=event_id,
                    chunk_index=idx,
                    markets=list(chunk),
                    few_shot_exemplars=list(few_shot),
                    max_text_field_chars=settings.max_text_field_chars,
                    max_tokens=_chunk_max_tokens(settings, len(chunk)),
                    part_path=part_path,
                )
            )

    failed_events: set[str] = set()
    if pending_tasks:
        llm = llm or build_graph_llm(settings)

    if settings.verify_deterministic and covered:
        verified, verify_status = verify_deterministic_fragments(
            settings, by_event, covered, llm
        )
        results.update(verified)
        status.update(verify_status)
        for event_id in covered:
            status.setdefault(event_id, "deterministic")

    if pending_tasks:
        assert llm is not None
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
                except Exception:
                    failed_events.add(task.event_id)
                    logger.exception(
                        "Inference failed for event %s chunk %d",
                        task.event_id,
                        task.chunk_index,
                    )

    for event_id in event_ids:
        if event_id in results:
            continue
        if event_id in covered:
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

        merged = merge_fragments(chunk_fragments)
        fragment_path = _fragment_path(settings, event_id)
        _save_fragment(fragment_path, merged)
        _save_chunk_manifest(settings, event_id, chunks)
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


def load_all_fragments(settings: Settings) -> FragmentLoadResult:
    fragments: dict[str, GraphFragment] = {}
    verified_event_ids: set[str] = set()
    for path in sorted(settings.fragments_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        if "__part" in path.name or "__chunk_manifest" in path.name:
            continue
        if path.name.endswith("__verify_manifest.json"):
            continue
        if path.name.endswith("__verified.json"):
            # Prefer verified topology fragments when present.
            event_id = path.name[: -len("__verified.json")]
            fragments[event_id] = _load_fragment(path)
            verified_event_ids.add(event_id)
            continue
        event_id = path.stem
        if event_id not in fragments:
            fragments[event_id] = _load_fragment(path)
    return FragmentLoadResult(
        fragments=fragments,
        verified_event_ids=verified_event_ids,
    )
