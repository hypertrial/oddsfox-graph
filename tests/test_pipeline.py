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


def test_pipeline_rejects_all_implies_on_rule_cycle(monkeypatch) -> None:
    """Cyclic rule IMPLIES must reject every rule-engine IMPLIES edge."""
    from oddsgraph.ontology import EdgeType, NodeType
    from oddsgraph.schema import CanonicalEdge, CanonicalNode, SemanticMarket
    import oddsgraph.pipeline as pipeline_mod

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
    ]

    def _cyclic_rules(nodes: list[CanonicalNode]) -> list[CanonicalEdge]:
        outcomes = [
            n.canonical_id
            for n in nodes
            if n.type == NodeType.OUTCOME and n.proposition is not None
        ]
        assert len(outcomes) >= 2
        a, b = outcomes[0], outcomes[1]
        return [
            CanonicalEdge(
                source_id=a,
                target_id=b,
                edge_type=EdgeType.IMPLIES,
                confidence=1.0,
                evidence_market_ids=["rule:a"],
                inference_method="rule_engine",
                derivation_type="rule",
                rule_id="test.cycle_a",
                rule_version=1,
                premises=["a", "b"],
            ),
            CanonicalEdge(
                source_id=b,
                target_id=a,
                edge_type=EdgeType.IMPLIES,
                confidence=1.0,
                evidence_market_ids=["rule:b"],
                inference_method="rule_engine",
                derivation_type="rule",
                rule_id="test.cycle_b",
                rule_version=1,
                premises=["b", "a"],
            ),
            CanonicalEdge(
                source_id=a,
                target_id=b,
                edge_type=EdgeType.MUTEX,
                confidence=1.0,
                evidence_market_ids=["rule:m"],
                inference_method="rule_engine",
                derivation_type="rule",
                rule_id="test.mutex",
                rule_version=1,
                premises=["a", "b"],
            ),
        ]

    monkeypatch.setattr(pipeline_mod, "apply_rules", _cyclic_rules)
    settings = Settings()
    settings.official_bracket = False
    result = build_pipeline_from_markets(settings, markets)
    assert not any(e.edge_type == EdgeType.IMPLIES for e in result.graph.edges)
    assert any(e.edge_type == EdgeType.MUTEX for e in result.graph.edges)
    assert any(e.rejection_reason == "implies_cycle" for e in result.graph.rejected_edges)
    # Both cyclic IMPLIES edges are rejected, not only one side of the merge.
    rejected_implies = [
        e for e in result.graph.rejected_edges if e.rejection_reason == "implies_cycle"
    ]
    assert len(rejected_implies) == 2


def test_pipeline_cycle_preserves_fragment_implies(monkeypatch) -> None:
    """Rule-created cycles must not wipe pre-existing fragment IMPLIES edges."""
    from oddsgraph.graphbuild import GraphBuildResult
    from oddsgraph.ontology import EdgeType, NodeType
    from oddsgraph.schema import CanonicalEdge, CanonicalNode, SemanticMarket
    import oddsgraph.pipeline as pipeline_mod

    markets = [
        SemanticMarket(
            market_id="20",
            event_id="200",
            event_title="Brazil vs. Morocco",
            event_slug="fifwc-bra-mar-2026-06-13",
            question="Will Brazil win?",
            group_item_title="Brazil",
            sports_market_type="moneyline",
            outcomes=["Yes", "No"],
        ),
        SemanticMarket(
            market_id="21",
            event_id="200",
            event_title="Brazil vs. Morocco",
            event_slug="fifwc-bra-mar-2026-06-13",
            question="Will Morocco win?",
            group_item_title="Morocco",
            sports_market_type="moneyline",
            outcomes=["Yes", "No"],
        ),
    ]

    real_build = pipeline_mod.build_graph_from_fragments
    fragment_pair: dict[str, str] = {}

    def _build_with_fragment_implies(*args, **kwargs) -> GraphBuildResult:
        result = real_build(*args, **kwargs)
        outcomes = [n.canonical_id for n in result.nodes if n.type == NodeType.OUTCOME]
        assert len(outcomes) >= 2
        a, b = outcomes[0], outcomes[1]
        fragment_pair["a"] = a
        fragment_pair["b"] = b
        result.edges.append(
            CanonicalEdge(
                source_id=a,
                target_id=b,
                edge_type=EdgeType.IMPLIES,
                confidence=1.0,
                evidence_market_ids=["frag:a"],
                inference_method="deterministic",
                derivation_type="direct",
            )
        )
        return result

    def _back_edge(nodes: list[CanonicalNode]) -> list[CanonicalEdge]:
        a, b = fragment_pair["a"], fragment_pair["b"]
        return [
            CanonicalEdge(
                source_id=b,
                target_id=a,
                edge_type=EdgeType.IMPLIES,
                confidence=1.0,
                evidence_market_ids=["rule:back"],
                inference_method="rule_engine",
                derivation_type="rule",
                rule_id="test.cycle_back",
                rule_version=1,
                premises=[b, a],
            )
        ]

    monkeypatch.setattr(pipeline_mod, "build_graph_from_fragments", _build_with_fragment_implies)
    monkeypatch.setattr(pipeline_mod, "apply_rules", _back_edge)
    settings = Settings()
    settings.official_bracket = False
    result = build_pipeline_from_markets(settings, markets)

    implies = [e for e in result.graph.edges if e.edge_type == EdgeType.IMPLIES]
    assert len(implies) == 1
    assert implies[0].source_id == fragment_pair["a"]
    assert implies[0].target_id == fragment_pair["b"]
    assert implies[0].inference_method == "deterministic"
    rejected = [
        e for e in result.graph.rejected_edges if e.rejection_reason == "implies_cycle"
    ]
    assert len(rejected) == 1
    assert rejected[0].source_id == fragment_pair["b"]
    assert rejected[0].target_id == fragment_pair["a"]


def test_pipeline_ignores_fragments_outside_selected_markets(tmp_path) -> None:
    """Stale on-disk fragments for other events must not enter a scoped build."""
    from oddsgraph.ontology import NodeType
    from oddsgraph.pipeline import run_build_and_export
    from oddsgraph.schema import GraphFragment, Node, SemanticMarket

    markets = [
        SemanticMarket(
            market_id="20",
            event_id="keep",
            event_title="Brazil vs. Morocco",
            event_slug="fifwc-bra-mar-2026-06-13",
            question="Will Brazil win?",
            group_item_title="Brazil",
            sports_market_type="moneyline",
            outcomes=["Yes", "No"],
        )
    ]
    settings = Settings()
    settings.configure_build_dir(tmp_path / "build")
    settings.ensure_dirs()
    settings.official_bracket = False
    settings.compile_propositions = False
    settings.apply_rules = False
    stale = GraphFragment(
        nodes=[
            Node(
                local_id="team:stale",
                type=NodeType.TEAM,
                label="STALE B",
                confidence=1.0,
                evidence_market_ids=["z"],
            )
        ],
        edges=[],
    )
    (settings.fragments_dir / "other.json").write_text(
        stale.model_dump_json(), encoding="utf-8"
    )
    result = run_build_and_export(settings, markets=markets)
    labels = [n.label for n in result.graph.nodes]
    assert "STALE B" not in labels
