from oddsfox_graph.prompts import (
    _serialize_market,
    chunk_markets_for_prompt,
    estimate_output_tokens,
)
from oddsfox_graph.schema import SemanticMarket


def _market(market_id: str, description: str | None = None) -> SemanticMarket:
    return SemanticMarket(
        market_id=market_id,
        event_id="100",
        question=f"Question {market_id}",
        description=description,
    )


def test_chunk_markets_respects_max_markets_per_chunk() -> None:
    markets = [_market(str(i)) for i in range(20)]
    chunks = chunk_markets_for_prompt(
        markets,
        "100",
        token_budget=100000,
        output_token_budget=100000,
        max_markets_per_chunk=8,
    )
    assert len(chunks) >= 3
    assert all(len(chunk) <= 8 for chunk in chunks)
    assert sum(len(c) for c in chunks) == 20


def test_chunk_markets_respects_output_token_budget() -> None:
    markets = [_market(str(i)) for i in range(12)]
    chunks = chunk_markets_for_prompt(
        markets,
        "100",
        token_budget=100000,
        output_token_budget=1000,
        max_markets_per_chunk=100,
    )
    assert len(chunks) > 1
    for chunk in chunks:
        assert estimate_output_tokens(len(chunk)) <= 1000


def test_serialize_market_truncates_long_description() -> None:
    long_text = "x" * 1000
    market = _market("1", description=long_text)
    serialized = _serialize_market(market, max_text_field_chars=100)
    assert serialized["description"] is not None
    assert len(serialized["description"]) == 100
    assert serialized["description"].endswith("...")
