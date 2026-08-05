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


def _serialize_market(market: SemanticMarket) -> dict:
    return {
        "market_id": market.market_id,
        "event_id": market.event_id,
        "event_slug": market.event_slug,
        "event_title": market.event_title,
        "event_description": market.event_description,
        "question": market.question,
        "description": market.description,
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
    strict: bool = False,
) -> str:
    event_title = markets[0].event_title if markets else event_id
    event_slug = markets[0].event_slug if markets else None
    payload = {
        "event_id": event_id,
        "event_title": event_title,
        "event_slug": event_slug,
        "markets": [_serialize_market(m) for m in markets],
    }
    strict_note = (
        "\nSTRICT: Previous output failed validation. "
        "Return only valid JSON with required fields and valid confidence values."
        if strict
        else ""
    )
    return (
        SYSTEM_RULES
        + strict_note
        + "\n\nPolymarket records:\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
        + "\n\nReturn a GraphFragment JSON object."
    )


def estimate_prompt_tokens(prompt: str) -> int:
    # Rough heuristic: ~4 chars per token
    return max(1, len(prompt) // 4)


def chunk_markets_for_prompt(
    markets: list[SemanticMarket],
    event_id: str,
    token_budget: int,
) -> list[list[SemanticMarket]]:
    if not markets:
        return []
    chunks: list[list[SemanticMarket]] = []
    current: list[SemanticMarket] = []
    for market in markets:
        trial = current + [market]
        prompt = build_event_prompt(event_id, trial)
        if estimate_prompt_tokens(prompt) > token_budget and current:
            chunks.append(current)
            current = [market]
        else:
            current = trial
    if current:
        chunks.append(current)
    return chunks
