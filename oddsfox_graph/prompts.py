"""Prompt construction for per-event LLM graph inference."""

from __future__ import annotations

import json

from oddsfox_graph.ontology import EdgeType, NodeType
from oddsfox_graph.schema import SemanticMarket

SYSTEM_RULES = """
You extract a typed logical graph from Polymarket WC2026 market metadata.

Rules:
- Use ONLY the supplied Polymarket records. Do not use general knowledge.
- Do not fill gaps or hallucinate missing topology.
- Emit ONLY these node types: COMPETITION, STAGE, GROUP, ROUND, MATCH, TEAM.
- Do NOT emit EVENT, MARKET, or OUTCOME nodes (those are built deterministically).
- Emit ONLY these edge types: PART_OF, PARTICIPATES_IN, QUALIFIES_FOR, ADVANCES_TO, PRICES, IMPLIES.
- Do NOT emit HAS_MARKET or HAS_OUTCOME edges.
- Every edge MUST cite supporting evidence from the supplied records.
- Edge direction matters: TEAM PARTICIPATES_IN MATCH (not MATCH PARTICIPATES_IN TEAM).
- TEAM QUALIFIES_FOR applies to STAGE, ROUND, or GROUP only — not other teams or matches.
- Do not create edges between two teams unless IMPLIES is explicitly supported by market text.
- Every node MUST include evidence_market_ids from the supplied records.
- confidence must be between 0 and 1.
- Return JSON matching the GraphFragment schema with nodes and edges arrays.
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
    EdgeType.PRICES,
    EdgeType.IMPLIES,
}

_PROMPT_FOOTER = "\n\nReturn a GraphFragment JSON object."


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


def build_event_prompt(
    event_id: str,
    markets: list[SemanticMarket],
    max_text_field_chars: int = 500,
) -> str:
    event_title = markets[0].event_title if markets else event_id
    event_slug = markets[0].event_slug if markets else None
    payload = {
        "event_id": event_id,
        "event_title": event_title,
        "event_slug": event_slug,
        "markets": [_serialize_market(m, max_text_field_chars) for m in markets],
    }
    return (
        SYSTEM_RULES
        + "\n\nPolymarket records:\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
        + _PROMPT_FOOTER
    )


def estimate_prompt_tokens(prompt: str) -> int:
    return max(1, len(prompt) // 4)


def estimate_output_tokens(market_count: int) -> int:
    # Conservative estimate for nodes + edges JSON per market
    return max(1, market_count * 200)


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
) -> bool:
    prompt = build_event_prompt(
        event_id, markets, max_text_field_chars=max_text_field_chars
    )
    return (
        estimate_prompt_tokens(prompt) > token_budget
        or estimate_output_tokens(len(markets)) > output_token_budget
    )


def _chunk_exceeds_budget_incremental(
    header_tokens: int,
    market_token_sizes: list[int],
    market_indices: list[int],
    token_budget: int,
    output_token_budget: int,
) -> bool:
    input_tokens = header_tokens + sum(market_token_sizes[i] for i in market_indices)
    input_tokens += len(market_indices) * 2
    return (
        input_tokens > token_budget
        or estimate_output_tokens(len(market_indices)) > output_token_budget
    )


def chunk_markets_for_prompt(
    markets: list[SemanticMarket],
    event_id: str,
    token_budget: int,
    output_token_budget: int = 3000,
    max_markets_per_chunk: int = 8,
    max_text_field_chars: int = 500,
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
            ):
                chunks.append([market])
                current_indices = []
        else:
            current_indices = trial_indices

    if current_indices:
        chunks.append([markets[i] for i in current_indices])

    return chunks
