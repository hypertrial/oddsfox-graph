"""Prompt construction for per-event LLM graph inference."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz, process

from oddsgraph.ontology import EdgeType, NodeType
from oddsgraph.schema import CompactGraphFragment, GraphFragment, SemanticMarket

SYSTEM_RULES = """
You extract a typed logical graph from Polymarket WC2026 market metadata.

Rules:
- Use ONLY the supplied Polymarket records. Do not use general knowledge.
- Do not fill gaps or hallucinate missing topology.
- Emit ONLY these node types: COMPETITION, STAGE, GROUP, ROUND, MATCH, TEAM.
- Do NOT emit EVENT, MARKET, or OUTCOME nodes (those are built deterministically).
- Emit ONLY these edge types: PART_OF, PARTICIPATES_IN, QUALIFIES_FOR, ADVANCES_TO.
- Do NOT emit HAS_MARKET, HAS_OUTCOME, PRICES, or IMPLIES edges.
- Every edge MUST cite supporting evidence from the supplied records.
- Edge direction matters: TEAM PARTICIPATES_IN MATCH (not MATCH PARTICIPATES_IN TEAM).
- TEAM QUALIFIES_FOR applies to STAGE, ROUND, or GROUP only — not other teams or matches.
- Do not create edges between two teams.
- Every node MUST include evidence market ids from the supplied records.
- confidence (field c) must be between 0 and 1.
- Return compact JSON with short keys:
  nodes array key "n": each node uses id,t,l,a,c,e
    (local_id, type, label, aliases, confidence, evidence_market_ids)
  edges array key "g": each edge uses s,d,t,c,e,x
    (source, target, type, confidence, evidence_market_ids, evidence_text)
""".strip()

VERIFICATION_RULES = """
You verify a candidate topology fragment against Polymarket WC2026 market metadata.

Rules:
- Use ONLY the supplied Polymarket records and the candidate fragment.
- If the candidate is correct, return it unchanged in compact JSON.
- If it is wrong or incomplete, return a corrected compact fragment.
- Emit ONLY these node types: COMPETITION, STAGE, GROUP, ROUND, MATCH, TEAM.
- Emit ONLY these edge types: PART_OF, PARTICIPATES_IN, QUALIFIES_FOR, ADVANCES_TO.
- Do not invent topology unsupported by the records.
- Return compact JSON with short keys:
  nodes array key "n": each node uses id,t,l,a,c,e
  edges array key "g": each edge uses s,d,t,c,e,x
""".strip()

ALLOWED_NODE_TYPES = {
    NodeType.COMPETITION,
    NodeType.STAGE,
    NodeType.GROUP,
    NodeType.ROUND,
    NodeType.MATCH,
    NodeType.TEAM,
}

ALLOWED_EDGE_TYPES = {
    EdgeType.PART_OF,
    EdgeType.PARTICIPATES_IN,
    EdgeType.QUALIFIES_FOR,
    EdgeType.ADVANCES_TO,
}

_PROMPT_FOOTER = "\n\nReturn a CompactGraphFragment JSON object."
_EXEMPLARS_PATH = Path(__file__).resolve().parent / "data" / "llm_exemplars.json"


def _truncate_text(text: str | None, max_chars: int) -> str | None:
    if text is None:
        return None
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _serialize_market(market: SemanticMarket, max_text_field_chars: int = 500) -> dict:
    return {
        "market_id": market.market_id,
        "event_id": market.event_id,
        "event_slug": market.event_slug,
        "event_title": market.event_title,
        "event_description": _truncate_text(market.event_description, max_text_field_chars),
        "question": market.question,
        "description": _truncate_text(market.description, max_text_field_chars),
        "market_slug": market.market_slug,
        "sports_market_type": market.sports_market_type,
        "group_item_title": market.group_item_title,
        "outcomes": market.outcomes,
        "event_tags": market.event_tags,
        "game_start_time": str(market.game_start_time) if market.game_start_time else None,
        "end_time": str(market.end_time) if market.end_time else None,
    }


@lru_cache(maxsize=1)
def load_exemplars() -> list[dict]:
    if not _EXEMPLARS_PATH.exists():
        return []
    data = json.loads(_EXEMPLARS_PATH.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    return list(data.get("exemplars", []))


def select_exemplars(
    event_title: str | None,
    question: str | None,
    exemplars: list[dict] | None = None,
    top_k: int = 2,
) -> list[dict]:
    """Rank curated exemplars by rapidfuzz similarity to the current event text."""
    pool = exemplars if exemplars is not None else load_exemplars()
    if not pool or top_k <= 0:
        return []
    query = " ".join(part for part in [event_title or "", question or ""] if part).strip()
    if not query:
        return pool[:top_k]

    choices = {
        str(idx): " ".join(
            part
            for part in [
                item.get("event_title") or "",
                item.get("question") or "",
                item.get("event_slug") or "",
            ]
            if part
        )
        for idx, item in enumerate(pool)
    }
    matches = process.extract(
        query,
        choices,
        scorer=fuzz.token_sort_ratio,
        limit=min(top_k, len(pool)),
    )
    selected: list[dict] = []
    for _label, _score, key in matches:
        selected.append(pool[int(key)])
    return selected


def _format_exemplars_block(exemplars: list[dict]) -> str:
    if not exemplars:
        return ""
    blocks: list[str] = ["\n\nFew-shot examples (follow this compact JSON style):"]
    for idx, exemplar in enumerate(exemplars, start=1):
        fragment = exemplar.get("fragment") or {"n": [], "g": []}
        blocks.append(
            f"\nExample {idx}:\n"
            f"event_title: {exemplar.get('event_title')}\n"
            f"question: {exemplar.get('question')}\n"
            f"output:\n{json.dumps(fragment, ensure_ascii=False)}"
        )
    return "".join(blocks)


def build_event_prompt(
    event_id: str,
    markets: list[SemanticMarket],
    max_text_field_chars: int = 500,
    few_shot_exemplars: list[dict] | None = None,
) -> str:
    event_title = markets[0].event_title if markets else event_id
    event_slug = markets[0].event_slug if markets else None
    payload = {
        "event_id": event_id,
        "event_title": event_title,
        "event_slug": event_slug,
        "markets": [_serialize_market(m, max_text_field_chars) for m in markets],
    }
    exemplars = few_shot_exemplars
    if exemplars is None:
        exemplars = []
    return (
        SYSTEM_RULES
        + _format_exemplars_block(exemplars)
        + "\n\nPolymarket records:\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
        + _PROMPT_FOOTER
    )


def build_verification_prompt(
    event_id: str,
    markets: list[SemanticMarket],
    candidate_fragment: GraphFragment,
    max_text_field_chars: int = 500,
) -> str:
    event_title = markets[0].event_title if markets else event_id
    event_slug = markets[0].event_slug if markets else None
    compact = CompactGraphFragment.from_graph_fragment(candidate_fragment)
    payload = {
        "event_id": event_id,
        "event_title": event_title,
        "event_slug": event_slug,
        "markets": [_serialize_market(m, max_text_field_chars) for m in markets],
        "candidate_fragment": json.loads(compact.model_dump_json()),
    }
    return (
        VERIFICATION_RULES
        + "\n\nPolymarket records and candidate:\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
        + _PROMPT_FOOTER
    )


def estimate_prompt_tokens(prompt: str) -> int:
    return max(1, len(prompt) // 4)


def estimate_output_tokens(market_count: int) -> int:
    # Compact wire format: fewer tokens per market than the full schema.
    return max(1, market_count * 120)


def _market_prompt_tokens(market: SemanticMarket, max_text_field_chars: int) -> int:
    serialized = _serialize_market(market, max_text_field_chars)
    return estimate_prompt_tokens(json.dumps(serialized, ensure_ascii=False))


def _event_header_tokens(
    event_id: str,
    markets: list[SemanticMarket],
    max_text_field_chars: int,
) -> int:
    if not markets:
        return estimate_prompt_tokens(SYSTEM_RULES + _PROMPT_FOOTER)
    event_title = markets[0].event_title or event_id
    event_slug = markets[0].event_slug
    header_payload = json.dumps(
        {
            "event_id": event_id,
            "event_title": event_title,
            "event_slug": event_slug,
            "markets": [],
        },
        indent=2,
        ensure_ascii=False,
    )
    return estimate_prompt_tokens(
        SYSTEM_RULES + "\n\nPolymarket records:\n" + header_payload + _PROMPT_FOOTER
    )


def _chunk_exceeds_budget(
    event_id: str,
    markets: list[SemanticMarket],
    token_budget: int,
    output_token_budget: int,
    max_text_field_chars: int,
    n_ctx: int,
    context_safety_margin: int = 64,
) -> bool:
    if not markets:
        return False
    market_token_sizes = [
        _market_prompt_tokens(m, max_text_field_chars) for m in markets
    ]
    header_tokens = _event_header_tokens(event_id, markets, max_text_field_chars)
    return _chunk_exceeds_budget_incremental(
        header_tokens,
        market_token_sizes,
        list(range(len(markets))),
        token_budget,
        output_token_budget,
        n_ctx,
        context_safety_margin,
    )


def _chunk_exceeds_budget_incremental(
    header_tokens: int,
    market_token_sizes: list[int],
    market_indices: list[int],
    token_budget: int,
    output_token_budget: int,
    n_ctx: int,
    context_safety_margin: int = 64,
) -> bool:
    input_tokens = header_tokens + sum(market_token_sizes[i] for i in market_indices)
    input_tokens += len(market_indices) * 2
    output_tokens = estimate_output_tokens(len(market_indices))
    total_tokens = input_tokens + output_tokens
    if total_tokens > n_ctx - context_safety_margin:
        return True
    return input_tokens > token_budget or output_tokens > output_token_budget


def chunk_markets_for_prompt(
    markets: list[SemanticMarket],
    event_id: str,
    token_budget: int,
    output_token_budget: int = 4096,
    max_markets_per_chunk: int = 24,
    max_text_field_chars: int = 500,
    n_ctx: int = 8192,
    context_safety_margin: int = 64,
) -> list[list[SemanticMarket]]:
    if not markets:
        return []

    market_token_sizes = [
        _market_prompt_tokens(market, max_text_field_chars) for market in markets
    ]
    header_tokens = _event_header_tokens(event_id, markets, max_text_field_chars)

    chunks: list[list[SemanticMarket]] = []
    current_indices: list[int] = []

    for index, market in enumerate(markets):
        trial_indices = current_indices + [index]
        exceeds = (
            len(trial_indices) > max_markets_per_chunk
            or _chunk_exceeds_budget_incremental(
                header_tokens,
                market_token_sizes,
                trial_indices,
                token_budget,
                output_token_budget,
                n_ctx,
                context_safety_margin,
            )
        )
        if exceeds and current_indices:
            chunks.append([markets[i] for i in current_indices])
            current_indices = [index]
            if _chunk_exceeds_budget_incremental(
                header_tokens,
                market_token_sizes,
                current_indices,
                token_budget,
                output_token_budget,
                n_ctx,
                context_safety_margin,
            ):
                chunks.append([market])
                current_indices = []
        else:
            current_indices = trial_indices

    if current_indices:
        chunks.append([markets[i] for i in current_indices])

    return chunks
