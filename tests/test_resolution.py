from oddsgraph.config import Settings
from oddsgraph.deterministic import build_deterministic_fragments_by_event
from oddsgraph.ontology import NodeType
from oddsgraph.resolution import resolve_fragments
from oddsgraph.schema import GraphFragment, Node, SemanticMarket

from tests.helpers import load_fixture_fragment


def test_exact_id_resolution() -> None:
    fragment = load_fixture_fragment("351746")
    settings = Settings()
    state = resolve_fragments([fragment], settings, inference_method="llm")
    assert "team:turkiye" in state.canonical_nodes
    assert state.tier_counts.get("new_entity", 0) >= 1


def test_minimum_confidence_does_not_block_node_creation() -> None:
    fragment = GraphFragment(
        nodes=[
            Node(
                local_id="team:xyz",
                type=NodeType.TEAM,
                label="Unique Team XYZ",
                confidence=0.4,
                evidence_market_ids=["1"],
            )
        ]
    )
    settings = Settings()
    settings.minimum_confidence = 0.5
    settings.fuzzy_threshold = 99
    state = resolve_fragments([fragment], settings, inference_method="llm")
    assert "team:unique-team-xyz" in state.canonical_nodes


def test_deterministic_and_llm_fragments_merge_same_canonical_id() -> None:
    det_fragment = GraphFragment(
        nodes=[
            Node(
                local_id="team:shared",
                type=NodeType.TEAM,
                label="Shared Team",
                confidence=0.9,
                evidence_market_ids=["m1"],
            )
        ]
    )
    llm_fragment = GraphFragment(
        nodes=[
            Node(
                local_id="team:llm-shared",
                type=NodeType.TEAM,
                label="Shared Team",
                confidence=0.8,
                evidence_market_ids=["m2"],
                aliases=["Shared"],
            )
        ]
    )
    settings = Settings()
    state = resolve_fragments(
        [det_fragment, llm_fragment],
        settings,
        inference_methods=["deterministic", "llm"],
    )
    canonical = state.canonical_nodes["team:shared-team"]
    assert canonical.confidence == 0.9
    assert sorted(canonical.evidence_market_ids) == ["m1", "m2"]
    assert "Shared" in canonical.aliases


def test_kor_alias_does_not_merge_south_korea_into_curacao() -> None:
    cura = build_deterministic_fragments_by_event(
        [
            SemanticMarket(
                market_id="c1",
                event_id="c",
                event_title="Germany vs. Curaçao",
                event_slug="fifwc-ger-kor-2026-06-14",
                outcomes=["Yes", "No"],
                sports_market_type="moneyline",
            )
        ]
    )["c"]
    korea = GraphFragment(
        nodes=[
            Node(
                local_id="team:south-korea",
                type=NodeType.TEAM,
                label="South Korea",
                aliases=["KOR"],
                confidence=0.9,
                evidence_market_ids=["llm1"],
            )
        ]
    )
    state = resolve_fragments(
        [cura, korea],
        Settings(),
        inference_methods=["deterministic", "llm"],
    )
    teams = sorted(
        n.label for n in state.canonical_nodes.values() if n.type == NodeType.TEAM
    )
    assert "Curaçao" in teams
    assert "South Korea" in teams
    assert state.local_to_canonical["team:south-korea"] == "team:south-korea"


def test_merged_aliases_are_usable_by_later_nodes() -> None:
    markets = [
        SemanticMarket(
            market_id="g1",
            event_id="g1",
            event_title="World Cup Group G Winner",
            group_item_title="Iran",
            question="q",
            outcomes=["Yes", "No"],
        ),
        SemanticMarket(
            market_id="m1",
            event_id="m1",
            event_title="IR Iran vs. New Zealand",
            event_slug="fifwc-irn-nzl-2026-06-15",
            question="q",
            outcomes=["Yes", "No"],
            sports_market_type="moneyline",
        ),
    ]
    det = list(build_deterministic_fragments_by_event(markets).values())
    llm = GraphFragment(
        nodes=[
            Node(
                local_id="team:ir-iran",
                type=NodeType.TEAM,
                label="IR Iran",
                aliases=[],
                confidence=0.9,
                evidence_market_ids=["llm1"],
            )
        ]
    )
    state = resolve_fragments(
        det + [llm],
        Settings(),
        inference_methods=["deterministic"] * len(det) + ["llm"],
    )
    iran_teams = [
        n
        for n in state.canonical_nodes.values()
        if n.type == NodeType.TEAM and "iran" in n.label.casefold()
    ]
    assert len(iran_teams) == 1
    assert iran_teams[0].canonical_id == "team:iran"
    assert state.local_to_canonical["team:ir-iran"] == "team:iran"


def _brazil_morocco_match_fragments() -> tuple[GraphFragment, GraphFragment]:
    dateful = GraphFragment(
        nodes=[
            Node(
                local_id="match:brazil-vs-morocco-2026-06-14",
                type=NodeType.MATCH,
                label="Brazil vs. Morocco",
                confidence=0.9,
                evidence_market_ids=["m1"],
            )
        ]
    )
    label_only = GraphFragment(
        nodes=[
            Node(
                local_id="match:brazil-vs-morocco",
                type=NodeType.MATCH,
                label="Brazil vs. Morocco",
                confidence=0.8,
                evidence_market_ids=["m2"],
            )
        ]
    )
    return dateful, label_only


def test_match_local_id_preferred_over_label_only_id() -> None:
    dateful, label_only = _brazil_morocco_match_fragments()
    # Register dateful first via local_id suggested path, then merge by slug/label.
    state = resolve_fragments(
        [dateful, label_only],
        Settings(),
        inference_methods=["deterministic", "llm"],
    )
    assert "match:brazil-vs-morocco-2026-06-14" in state.canonical_nodes
    assert "match:brazil-vs-morocco" not in state.canonical_nodes
    assert state.local_to_canonical["match:brazil-vs-morocco"] == (
        "match:brazil-vs-morocco-2026-06-14"
    )


def test_distinct_dateful_match_ids_do_not_collapse() -> None:
    """Same display label on different dates must stay distinct fixtures."""
    june = GraphFragment(
        nodes=[
            Node(
                local_id="match:brazil-vs-morocco-2026-06-13",
                type=NodeType.MATCH,
                label="Brazil vs. Morocco",
                confidence=1.0,
                evidence_market_ids=["m1"],
            )
        ],
        edges=[],
    )
    july = GraphFragment(
        nodes=[
            Node(
                local_id="match:brazil-vs-morocco-2026-07-13",
                type=NodeType.MATCH,
                label="Brazil vs. Morocco",
                confidence=1.0,
                evidence_market_ids=["m2"],
            )
        ],
        edges=[],
    )
    state = resolve_fragments([june, july], Settings())
    assert "match:brazil-vs-morocco-2026-06-13" in state.canonical_nodes
    assert "match:brazil-vs-morocco-2026-07-13" in state.canonical_nodes
    assert state.local_to_canonical["match:brazil-vs-morocco-2026-06-13"] == (
        "match:brazil-vs-morocco-2026-06-13"
    )
    assert state.local_to_canonical["match:brazil-vs-morocco-2026-07-13"] == (
        "match:brazil-vs-morocco-2026-07-13"
    )


def test_match_dateful_id_upgrades_when_label_only_arrives_first() -> None:
    """Bracket is appended after topology; dateful MATCH ids must still win."""
    dateful, label_only = _brazil_morocco_match_fragments()
    state = resolve_fragments(
        [label_only, dateful],
        Settings(),
        inference_methods=["deterministic", "official_bracket"],
    )
    assert "match:brazil-vs-morocco-2026-06-14" in state.canonical_nodes
    assert "match:brazil-vs-morocco" not in state.canonical_nodes
    assert state.local_to_canonical["match:brazil-vs-morocco"] == (
        "match:brazil-vs-morocco-2026-06-14"
    )
    assert state.local_to_canonical["match:brazil-vs-morocco-2026-06-14"] == (
        "match:brazil-vs-morocco-2026-06-14"
    )
    assert sorted(
        state.canonical_nodes["match:brazil-vs-morocco-2026-06-14"].evidence_market_ids
    ) == ["m1", "m2"]


def test_resolve_fragments_output_unchanged_after_perf_refactor() -> None:
    """Golden resolution snapshot must stay identical after hot-path optimizations."""
    import json

    from oddsgraph.deterministic import build_deterministic_fragments_by_event
    from oddsgraph.propositions import compile_propositions
    from tests.helpers import FIXTURES_DIR, load_golden_markets

    baseline_path = FIXTURES_DIR / "resolution_golden_baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    markets = load_golden_markets()
    det = build_deterministic_fragments_by_event(
        markets, include_topology=True, competition_label="World Cup 2026"
    )
    comp = compile_propositions(markets)
    fragments = list(det.values())
    methods = ["deterministic"] * len(det)
    if comp.fragment.nodes or comp.fragment.edges:
        fragments.append(comp.fragment)
        methods.append("proposition_compiler")

    state = resolve_fragments(fragments, Settings(), inference_methods=methods)
    actual = {
        "tier_counts": dict(sorted(state.tier_counts.items())),
        "local_to_canonical": dict(sorted(state.local_to_canonical.items())),
        "canonical_nodes": {
            cid: node.model_dump(mode="json")
            for cid, node in sorted(state.canonical_nodes.items())
        },
    }
    assert actual["tier_counts"] == baseline["tier_counts"]
    assert actual["local_to_canonical"] == baseline["local_to_canonical"]
    assert actual["canonical_nodes"] == baseline["canonical_nodes"]
