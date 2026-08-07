"""Deterministic logical rules over compiled outcome propositions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

from oddsgraph import ids
from oddsgraph.bracket import STAGE_KEY_TO_LABEL, KNOCKOUT_STAGE_RANK
from oddsgraph.ontology import EdgeType, NodeType, is_allowed_edge
from oddsgraph.schema import CanonicalEdge, CanonicalNode, Proposition

# Monotonic stage ranks for reaches_stage / elimination implication.
# Higher rank is "later" in the tournament.
STAGE_MONOTONICITY_RANK: dict[str, int] = {
    "Group Stage": 0,
    "Round of 32": 1,
    "Round of 16": 2,
    "Quarterfinals": 3,
    "Semifinals": 4,
    "Third Place": 5,
    "Final": 5,
    "Champion": 6,
}
# Fill from official knockout ranks when labels match.
for _key, _rank in KNOCKOUT_STAGE_RANK.items():
    label = STAGE_KEY_TO_LABEL[_key]
    STAGE_MONOTONICITY_RANK.setdefault(label, _rank)


def stage_label_from_id(stage_id: str) -> str:
    """Extract stage label from ``stage:<competition>:<slug>`` or raw label."""
    if stage_id.startswith("stage:"):
        parts = stage_id.split(":", 2)
        if len(parts) == 3:
            return parts[2].replace("-", " ").title().replace("Of", "of")
    return stage_id


def stage_rank_for_proposition(stage_id: str) -> int:
    """Return monotonic rank for a stage argument id or label."""
    slug = stage_id.split(":")[-1] if ":" in stage_id else stage_id
    for label, rank in STAGE_MONOTONICITY_RANK.items():
        if ids.slugify(label) == slug or label == stage_id:
            return rank
    return STAGE_MONOTONICITY_RANK.get(stage_label_from_id(stage_id), -1)


def stage_slug_for_proposition(stage_id: str) -> str:
    """Return the stage slug from a ``stage:…`` id or free-form label."""
    if stage_id.startswith("stage:") and stage_id.count(":") >= 2:
        return stage_id.rsplit(":", 1)[-1]
    return ids.slugify(stage_id)


RuleFn = Callable[[Proposition, Proposition], bool]


@dataclass(frozen=True)
class Rule:
    rule_id: str
    version: int
    edge_type: EdgeType
    fn: RuleFn
    # If True, also emit the reverse edge (used for EQUIVALENT).
    bidirectional: bool = False


RULE_REGISTRY: list[Rule] = []


def rule(
    rule_id: str,
    edge_type: EdgeType,
    version: int = 1,
    *,
    bidirectional: bool = False,
) -> Callable[[RuleFn], RuleFn]:
    def decorator(fn: RuleFn) -> RuleFn:
        RULE_REGISTRY.append(
            Rule(
                rule_id=rule_id,
                version=version,
                edge_type=edge_type,
                fn=fn,
                bidirectional=bidirectional or edge_type == EdgeType.EQUIVALENT,
            )
        )
        return fn

    return decorator


def _same_team_and_competition(a: Proposition, b: Proposition) -> bool:
    return (
        a.arguments.get("team")
        and a.arguments.get("team") == b.arguments.get("team")
        and a.arguments.get("competition")
        and a.arguments.get("competition") == b.arguments.get("competition")
        and a.polarity
        and b.polarity
    )


@rule("wc.stage_monotonicity", EdgeType.IMPLIES)
def stage_monotonicity(a: Proposition, b: Proposition) -> bool:
    if a.predicate != "reaches_stage" or b.predicate != "reaches_stage":
        return False
    if not _same_team_and_competition(a, b):
        return False
    a_stage = a.arguments.get("stage")
    b_stage = b.arguments.get("stage")
    if not a_stage or not b_stage or a_stage == b_stage:
        return False
    a_rank = stage_rank_for_proposition(a_stage)
    b_rank = stage_rank_for_proposition(b_stage)
    # Unknown stages use sentinel -1; do not invent ordering against them.
    if a_rank < 0 or b_rank < 0:
        return False
    return a_rank > b_rank


@rule("wc.champion_reaches_final", EdgeType.IMPLIES)
def champion_reaches_final(a: Proposition, b: Proposition) -> bool:
    if a.predicate != "wins_competition" or b.predicate != "reaches_stage":
        return False
    if not _same_team_and_competition(a, b):
        return False
    # Compare Final identity, not monotonic rank — Third Place shares Final's rank.
    return stage_slug_for_proposition(b.arguments.get("stage", "")) == "final"


@rule("wc.elimination_implies_reaches", EdgeType.IMPLIES)
def elimination_implies_reaches(a: Proposition, b: Proposition) -> bool:
    """eliminated_at_stage(S) implies reaches_stage(S) for knockout+ stages."""
    if a.predicate != "eliminated_at_stage" or b.predicate != "reaches_stage":
        return False
    if not _same_team_and_competition(a, b):
        return False
    a_stage = a.arguments.get("stage")
    b_stage = b.arguments.get("stage")
    if not a_stage or a_stage != b_stage:
        return False
    # Group Stage is the start; "reaching" it is vacuous / not marketed that way.
    return stage_rank_for_proposition(a_stage) >= STAGE_MONOTONICITY_RANK["Round of 32"]


@rule("wc.champion_equals_elim_at_champion", EdgeType.EQUIVALENT, bidirectional=True)
def champion_equals_elim_at_champion(a: Proposition, b: Proposition) -> bool:
    if not _same_team_and_competition(a, b):
        return False
    if a.predicate == "wins_competition" and b.predicate == "eliminated_at_stage":
        return stage_rank_for_proposition(b.arguments.get("stage", "")) == STAGE_MONOTONICITY_RANK[
            "Champion"
        ]
    if b.predicate == "wins_competition" and a.predicate == "eliminated_at_stage":
        return stage_rank_for_proposition(a.arguments.get("stage", "")) == STAGE_MONOTONICITY_RANK[
            "Champion"
        ]
    return False


@rule("wc.single_match_winner_mutex", EdgeType.MUTEX, bidirectional=True)
def single_match_winner_mutex(a: Proposition, b: Proposition) -> bool:
    if a.predicate != "wins_match" or b.predicate != "wins_match":
        return False
    if not (a.polarity and b.polarity):
        return False
    if a.arguments.get("match") != b.arguments.get("match"):
        return False
    if not a.arguments.get("match"):
        return False
    return a.arguments.get("team") != b.arguments.get("team")


def _index_key(prop: Proposition) -> list[str]:
    """Return candidate-pair index keys for a proposition.

    Only ``team`` and ``match`` are indexed. Competition/group keys were
    dropped because every registered rule already requires equal team (via
    ``_same_team_and_competition``) or equal match (``single_match_winner_mutex``).
    Indexing on a shared competition alone forced an O(n²) pair scan over all
    propositions in one tournament.
    """
    keys: list[str] = []
    for role in ("team", "match"):
        value = prop.arguments.get(role)
        if value:
            keys.append(f"{role}:{value}")
    return keys or ["__all__"]


def apply_rules(nodes: list[CanonicalNode]) -> list[CanonicalEdge]:
    """Apply registered rules to OUTCOME nodes that carry propositions."""
    outcomes = [
        n
        for n in nodes
        if n.type == NodeType.OUTCOME and n.proposition is not None
    ]
    by_index: dict[str, list[CanonicalNode]] = defaultdict(list)
    for node in outcomes:
        assert node.proposition is not None
        for key in _index_key(node.proposition):
            by_index[key].append(node)

    # Candidate pairs sharing at least one index key.
    pairs: set[tuple[str, str]] = set()
    for group in by_index.values():
        for i, left in enumerate(group):
            for right in group[i + 1 :]:
                a_id, b_id = sorted((left.canonical_id, right.canonical_id))
                pairs.add((a_id, b_id))

    by_id = {n.canonical_id: n for n in outcomes}
    emitted: dict[tuple[str, str, str], CanonicalEdge] = {}

    for left_id, right_id in pairs:
        left = by_id[left_id]
        right = by_id[right_id]
        assert left.proposition is not None and right.proposition is not None
        for node_a, node_b in ((left, right), (right, left)):
            prop_a = node_a.proposition
            prop_b = node_b.proposition
            assert prop_a is not None and prop_b is not None
            for registered in RULE_REGISTRY:
                if not registered.fn(prop_a, prop_b):
                    continue
                if not is_allowed_edge(
                    registered.edge_type, NodeType.OUTCOME, NodeType.OUTCOME
                ):
                    continue
                directions = [(node_a, node_b)]
                if registered.bidirectional:
                    directions.append((node_b, node_a))
                for src, tgt in directions:
                    assert src.proposition is not None and tgt.proposition is not None
                    key = (src.canonical_id, tgt.canonical_id, registered.edge_type.value)
                    if key in emitted:
                        continue
                    evidence = sorted(
                        set(src.evidence_market_ids) | set(tgt.evidence_market_ids)
                    )
                    if not evidence:
                        evidence = ["rule:synthetic"]
                    emitted[key] = CanonicalEdge(
                        source_id=src.canonical_id,
                        target_id=tgt.canonical_id,
                        edge_type=registered.edge_type,
                        confidence=1.0,
                        evidence_market_ids=evidence,
                        evidence_text=registered.rule_id,
                        inference_method="rule_engine",
                        derivation_type="rule",
                        rule_id=registered.rule_id,
                        rule_version=registered.version,
                        premises=[src.proposition.key(), tgt.proposition.key()],
                    )

    return list(emitted.values())
