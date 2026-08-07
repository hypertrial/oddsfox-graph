import shutil
from pathlib import Path

from oddsgraph.config import Settings
from oddsgraph.pipeline import build_pipeline_from_markets, run_build_and_export
from oddsgraph.reduce import load_semantic_markets, select_event_ids

from tests.helpers import GOLDEN_MARKETS_PATH, load_fixture_fragment, load_golden_markets


def test_build_pipeline_from_markets_matches_fixture_fragments() -> None:
    markets = load_golden_markets()
    inferred = {
        "351746": load_fixture_fragment("351746"),
        "98266": load_fixture_fragment("98266"),
    }
    settings = Settings()
    result = build_pipeline_from_markets(settings, markets, inferred)

    assert len(result.graph.nodes) > 0
    assert result.report.node_counts.get("MARKET", 0) > 0


def test_run_build_and_export_writes_artifacts(tmp_path: Path) -> None:
    settings = Settings()
    settings.configure_build_dir(tmp_path / "build")
    settings.ensure_dirs()
    shutil.copy(GOLDEN_MARKETS_PATH, settings.semantic_markets_path)

    inferred = load_fixture_fragment("351746")
    fragment_path = settings.fragments_dir / "351746.json"
    fragment_path.write_text(inferred.model_dump_json(indent=2), encoding="utf-8")

    result = run_build_and_export(settings)
    assert settings.nodes_path.exists()
    assert settings.edges_path.exists()
    assert settings.inference_report_path.exists()
    assert len(result.graph.nodes) > 0


def test_load_semantic_markets_empty_event_filter_returns_no_rows() -> None:
    filtered = load_semantic_markets(GOLDEN_MARKETS_PATH, event_ids=[])
    assert filtered == []


def test_load_semantic_markets_event_filter_matches_full_load() -> None:
    markets = load_golden_markets()
    event_ids = select_event_ids(
        sorted({market.event_id for market in markets}),
        ["351746"],
        None,
    )
    filtered = load_semantic_markets(GOLDEN_MARKETS_PATH, event_ids=event_ids)
    expected = [market for market in markets if market.event_id == "351746"]
    assert len(filtered) == len(expected)
    assert {market.market_id for market in filtered} == {market.market_id for market in expected}


def test_pipeline_compiles_propositions_and_rules() -> None:
    from oddsgraph.ontology import EdgeType
    from oddsgraph.schema import SemanticMarket

    markets = [
        SemanticMarket(
            market_id="10",
            event_id="100",
            event_title="Brazil vs. Morocco",
            event_slug="fifwc-bra-mar-2026-06-13",
            question="Will Brazil win?",
            group_item_title="Brazil",
            sports_market_type="moneyline",
            outcomes=["Yes", "No"],
        ),
        SemanticMarket(
            market_id="11",
            event_id="100",
            event_title="Brazil vs. Morocco",
            event_slug="fifwc-bra-mar-2026-06-13",
            question="Will Morocco win?",
            group_item_title="Morocco",
            sports_market_type="moneyline",
            outcomes=["Yes", "No"],
        ),
        SemanticMarket(
            market_id="20",
            event_id="200",
            event_title="World Cup Winner",
            event_slug="world-cup-winner",
            question="Will Brazil win the 2026 FIFA World Cup?",
            group_item_title="Brazil",
            outcomes=["Yes", "No"],
        ),
        SemanticMarket(
            market_id="30",
            event_id="300",
            event_title="World Cup: Nation to Reach Final",
            event_slug="nation-final",
            question="Will Brazil reach the final?",
            group_item_title="Brazil",
            outcomes=["Yes", "No"],
        ),
    ]
    settings = Settings()
    settings.official_bracket = False
    result = build_pipeline_from_markets(settings, markets)
    assert result.propositions
    assert any(n.proposition is not None for n in result.graph.nodes)
    assert any(e.edge_type == EdgeType.REFERS_TO for e in result.graph.edges)
    assert any(e.edge_type == EdgeType.PRICES for e in result.graph.edges)
    assert any(e.derivation_type == "rule" for e in result.graph.edges)


def test_pipeline_can_disable_propositions() -> None:
    from oddsgraph.ontology import EdgeType
    from oddsgraph.schema import SemanticMarket

    markets = [
        SemanticMarket(
            market_id="10",
            event_id="100",
            event_title="Brazil vs. Morocco",
            event_slug="fifwc-bra-mar-2026-06-13",
            question="Will Brazil win?",
            group_item_title="Brazil",
            sports_market_type="moneyline",
            outcomes=["Yes", "No"],
        ),
    ]
    settings = Settings()
    settings.official_bracket = False
    settings.compile_propositions = False
    settings.apply_rules = False
    result = build_pipeline_from_markets(settings, markets)
    assert result.propositions is None
    assert not any(e.edge_type == EdgeType.REFERS_TO for e in result.graph.edges)
    assert not any(n.proposition is not None for n in result.graph.nodes)
