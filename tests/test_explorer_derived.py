from __future__ import annotations

from collections import defaultdict
import random
from time import perf_counter

from oddsfox_graph._explorer.derived import (
    ESSENTIAL_PROJECTION_VERSION,
    ProjectionEdge,
    essential_projection,
)


def test_essential_projection_preserves_contract_and_reports_omissions() -> None:
    edges = (
        _edge("a-b", "a", "b"),
        _edge("b-c", "b", "c"),
        _edge("a-c", "a", "c"),
        _edge("a-c-weaker", "a", "c", confidence=0.9),
        _edge("c-a-cycle", "c", "a", confidence=0.8),
        _edge("same", "a", "b", relation="equivalent"),
        _edge("same-reversed", "b", "a", relation="equivalent", confidence=0.9),
    )

    result = essential_projection(edges)

    assert result.version == ESSENTIAL_PROJECTION_VERSION
    assert result.duplicate_ids == ("a-c-weaker", "same-reversed")
    assert set(result.retained_ids) == {"a-b", "b-c", "a-c", "c-a-cycle", "same"}
    assert result.redundant_ids == ()


def test_essential_projection_uses_equal_or_stronger_alternate_paths() -> None:
    result = essential_projection(
        (
            _edge("a-b", "a", "b", confidence=0.95),
            _edge("b-c", "b", "c", confidence=0.95),
            _edge("a-c", "a", "c", confidence=0.95),
            _edge("a-d", "a", "d", confidence=1.0),
            _edge("d-e", "d", "e", confidence=0.9),
            _edge("a-e", "a", "e", confidence=0.95),
        )
    )

    assert "a-c" in result.redundant_ids
    assert "a-e" in result.retained_ids
    preserved = essential_projection(
        (
            _edge("a-b", "a", "b"),
            _edge("b-c", "b", "c"),
            _edge("a-c", "a", "c"),
        ),
        preserve_ids=frozenset({"a-c"}),
    )
    assert "a-c" in preserved.retained_ids


def test_optimized_projection_matches_reference_on_seeded_graphs() -> None:
    generator = random.Random(20260802)
    relations = ("implies", "equivalent", "complement", "mutually_exclusive")
    confidences = (0.7, 0.9, 0.95, 1.0)
    for _ in range(500):
        nodes = tuple(str(index) for index in range(generator.randint(1, 8)))
        edges = tuple(
            _edge(
                f"edge-{index}",
                generator.choice(nodes),
                generator.choice(nodes),
                relation=generator.choice(relations),
                confidence=generator.choice(confidences),
            )
            for index in range(generator.randint(0, 28))
        )
        preserve = frozenset(
            generator.sample(
                [edge.id for edge in edges],
                k=min(len(edges), generator.randint(0, 2)),
            )
        )
        assert essential_projection(edges, preserve_ids=preserve).retained_ids == (
            _reference_projection(edges, preserve)
        )


def test_projection_meets_wc2026_latency_and_scaling_budget() -> None:
    baseline = _wc2026_scale_edges(copies=1)
    scaled = _wc2026_scale_edges(copies=4)
    baseline_ms = _median_runtime_ms(baseline)
    scaled_ms = _median_runtime_ms(scaled)

    assert len(baseline) == 834
    assert baseline_ms <= 25.0
    assert scaled_ms < baseline_ms * 8.0


def _edge(
    identifier: str,
    source: str,
    target: str,
    *,
    relation: str = "implies",
    confidence: float = 1.0,
) -> ProjectionEdge:
    return ProjectionEdge(
        id=identifier,
        source=source,
        target=target,
        relation=relation,
        confidence=confidence,
    )


def _wc2026_scale_edges(*, copies: int) -> tuple[ProjectionEdge, ...]:
    edges: list[ProjectionEdge] = []

    def append(source: str, target: str, relation: str) -> None:
        edges.append(
            _edge(f"scale-{len(edges):05d}", source, target, relation=relation)
        )

    for copy in range(copies):
        prefix = f"{copy}:"
        for team in range(16):
            for level in range(6):
                append(
                    f"{prefix}{team}-{level}-yes",
                    f"{prefix}{team}-{level}-no",
                    "complement",
                )
                for lower in range(level):
                    append(
                        f"{prefix}{team}-{level}-yes",
                        f"{prefix}{team}-{lower}-yes",
                        "implies",
                    )
                    append(
                        f"{prefix}{team}-{lower}-no",
                        f"{prefix}{team}-{level}-no",
                        "implies",
                    )
        for left in range(16):
            for right in range(left + 1, 16):
                append(
                    f"{prefix}{left}-5-yes",
                    f"{prefix}{right}-5-yes",
                    "mutually_exclusive",
                )
    while len(edges) < 834 * copies:
        index = len(edges)
        append(f"padding-{index}-a", f"padding-{index}-b", "compatible")
    return tuple(edges)


def _median_runtime_ms(edges: tuple[ProjectionEdge, ...]) -> float:
    for _ in range(3):
        essential_projection(edges)
    timings: list[float] = []
    for _ in range(15):
        started = perf_counter()
        essential_projection(edges)
        timings.append((perf_counter() - started) * 1_000)
    return sorted(timings)[len(timings) // 2]


def _reference_projection(
    edges: tuple[ProjectionEdge, ...],
    preserve: frozenset[str],
) -> tuple[str, ...]:
    deduplicated: dict[tuple[str, str, str], ProjectionEdge] = {}
    for edge in sorted(
        edges,
        key=lambda item: (
            item.id not in preserve,
            -item.confidence,
            item.id,
        ),
    ):
        source, target = edge.source, edge.target
        if edge.relation != "implies" and source > target:
            source, target = target, source
        deduplicated.setdefault((edge.relation, source, target), edge)
    candidates = tuple(deduplicated.values())
    traversable = tuple(
        edge
        for edge in candidates
        if edge.relation in {"implies", "equivalent"}
    )

    def reachable(
        source: str,
        target: str,
        excluded: str,
        minimum_confidence: float,
    ) -> bool:
        adjacency: dict[str, list[str]] = defaultdict(list)
        for edge in traversable:
            if edge.id == excluded or edge.confidence < minimum_confidence:
                continue
            adjacency[edge.source].append(edge.target)
            if edge.relation == "equivalent":
                adjacency[edge.target].append(edge.source)
        frontier = [source]
        seen = {source}
        while frontier:
            node = frontier.pop()
            for neighbor in adjacency[node]:
                if neighbor == target:
                    return True
                if neighbor not in seen:
                    seen.add(neighbor)
                    frontier.append(neighbor)
        return False

    retained = []
    for edge in candidates:
        if (
            edge.relation != "implies"
            or edge.id in preserve
            or reachable(edge.target, edge.source, edge.id, 0.0)
            or not reachable(
                edge.source,
                edge.target,
                edge.id,
                edge.confidence,
            )
        ):
            retained.append(edge)
    return tuple(
        edge.id
        for edge in sorted(
            retained,
            key=lambda item: (
                -item.confidence,
                item.relation,
                item.source,
                item.target,
                item.id,
            ),
        )
    )
