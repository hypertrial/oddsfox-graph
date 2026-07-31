"""Deterministic graph aggregations used by the local explorer."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from typing import Any, cast

from .._discovery.bulk import create_and_fill
from .._discovery.provenance import canonical_json_sha256
from .._discovery.versions import VISUALIZATION_LAYOUT_VERSION
from ..qualification import assign_domain_fields
from ..queries import DuckDB


EVENT_SUMMARY_COLUMNS = {
    "event_key": "VARCHAR",
    "event_id": "VARCHAR",
    "event_slug": "VARCHAR",
    "label": "VARCHAR",
    "primary_domain": "VARCHAR",
    "category": "VARCHAR",
    "market_count": "BIGINT",
    "proposition_count": "BIGINT",
    "active_market_count": "BIGINT",
    "closed_market_count": "BIGINT",
    "accepted_edge_count": "BIGINT",
    "rejected_edge_count": "BIGINT",
    "quarantined_pair_count": "BIGINT",
    "unclassified_pair_count": "BIGINT",
    "classification_eligible_count": "BIGINT",
    "classification_assessed_count": "BIGINT",
    "classification_coverage": "DOUBLE",
    "deterministic_edge_count": "BIGINT",
    "consensus_edge_count": "BIGINT",
    "complement_count": "BIGINT",
    "equivalent_count": "BIGINT",
    "mutually_exclusive_count": "BIGINT",
    "implies_count": "BIGINT",
    "compatible_count": "BIGINT",
    "component_count": "BIGINT",
    "first_seen_ts": "TIMESTAMPTZ",
    "last_seen_ts": "TIMESTAMPTZ",
}

EVENT_RELATION_SUMMARY_COLUMNS = {
    "src_event_key": "VARCHAR",
    "dst_event_key": "VARCHAR",
    "edge_type": "VARCHAR",
    "edge_count": "BIGINT",
    "min_confidence": "DOUBLE",
    "max_confidence": "DOUBLE",
    "mean_confidence": "DOUBLE",
    "deterministic_count": "BIGINT",
    "consensus_count": "BIGINT",
    "source_market_count": "BIGINT",
    "destination_market_count": "BIGINT",
    "aggregation_only": "BOOLEAN",
}

COMPONENT_SUMMARY_COLUMNS = {
    "component_id": "VARCHAR",
    "component_fingerprint": "VARCHAR",
    "proposition_count": "BIGINT",
    "market_count": "BIGINT",
    "event_count": "BIGINT",
    "edge_count": "BIGINT",
    "deterministic_edge_count": "BIGINT",
    "consensus_edge_count": "BIGINT",
    "quarantined_pair_count": "BIGINT",
    "unclassified_pair_count": "BIGINT",
    "classification_coverage": "DOUBLE",
    "representative_node_ids": "VARCHAR[]",
    "layout_min_x": "DOUBLE",
    "layout_min_y": "DOUBLE",
    "layout_max_x": "DOUBLE",
    "layout_max_y": "DOUBLE",
}

NODE_METRIC_COLUMNS = {
    "node_id": "VARCHAR",
    "market_id": "VARCHAR",
    "event_key": "VARCHAR",
    "component_id": "VARCHAR",
    "total_degree": "BIGINT",
    "incoming_degree": "BIGINT",
    "outgoing_degree": "BIGINT",
    "complement_degree": "BIGINT",
    "equivalent_degree": "BIGINT",
    "mutually_exclusive_degree": "BIGINT",
    "implies_degree": "BIGINT",
    "compatible_degree": "BIGINT",
    "rejected_count": "BIGINT",
    "quarantine_count": "BIGINT",
    "parse_status": "VARCHAR",
    "classification_state": "VARCHAR",
    "classification_eligible_count": "BIGINT",
    "classification_assessed_count": "BIGINT",
    "unclassified_pair_count": "BIGINT",
    "classification_coverage": "DOUBLE",
}

VISUALIZATION_LAYOUT_COLUMNS = {
    "layout_level": "VARCHAR",
    "object_id": "VARCHAR",
    "parent_id": "VARCHAR",
    "x": "DOUBLE",
    "y": "DOUBLE",
    "radius": "DOUBLE",
    "layout_rank": "BIGINT",
    "layout_version": "VARCHAR",
    "graph_fingerprint": "VARCHAR",
}


class _UnionFind:
    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        while parent != self.parent[parent]:
            parent = self.parent[parent]
        while value != parent:
            next_value = self.parent[value]
            self.parent[value] = parent
            value = next_value
        return parent

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            self.parent[right_root] = left_root
        else:
            self.parent[left_root] = right_root


def build_explorer_tables(
    db: DuckDB,
    *,
    input_selection: Mapping[str, object],
) -> dict[str, object]:
    """Create explorer tables in the final discovery workspace."""

    node_ids = [
        str(row["node_id"])
        for row in db.rows("SELECT node_id FROM nodes_table ORDER BY node_id")
    ]
    edge_rows = db.rows(
        """
        SELECT src_node_id, dst_node_id, edge_type, proposal_id
        FROM logic_edges_v
        ORDER BY src_node_id, dst_node_id, edge_type, proposal_id
        """
    )
    union_find = _UnionFind(node_ids)
    for edge in edge_rows:
        union_find.union(str(edge["src_node_id"]), str(edge["dst_node_id"]))
    members_by_root: dict[str, list[str]] = defaultdict(list)
    for node_id in node_ids:
        members_by_root[union_find.find(node_id)].append(node_id)
    edge_tokens_by_root: dict[str, list[str]] = defaultdict(list)
    for edge in edge_rows:
        root = union_find.find(str(edge["src_node_id"]))
        edge_tokens_by_root[root].append(
            "|".join(
                (
                    str(edge["src_node_id"]),
                    str(edge["dst_node_id"]),
                    str(edge["edge_type"]),
                    str(edge["proposal_id"]),
                )
            )
        )
    component_by_node: dict[str, str] = {}
    component_fingerprint: dict[str, str] = {}
    for root, members in sorted(members_by_root.items()):
        ordered_members = sorted(members)
        fingerprint = canonical_json_sha256(
            {
                "members": ordered_members,
                "edges": sorted(edge_tokens_by_root.get(root, [])),
            }
        )
        component_id = f"component-{fingerprint[:16]}"
        component_fingerprint[component_id] = fingerprint
        for member in ordered_members:
            component_by_node[member] = component_id
    create_and_fill(
        db,
        "node_components_v",
        {
            "node_id": "VARCHAR",
            "component_id": "VARCHAR",
            "component_fingerprint": "VARCHAR",
        },
        [
            {
                "node_id": node_id,
                "component_id": component_by_node[node_id],
                "component_fingerprint": component_fingerprint[
                    component_by_node[node_id]
                ],
            }
            for node_id in sorted(node_ids)
        ],
    )

    market_rows = db.rows(
        """
        SELECT DISTINCT market_id, event_id, event_slug, question, description,
                        category, tags
        FROM propositions_v
        ORDER BY market_id
        """
    )
    create_and_fill(
        db,
        "market_domains_v",
        {"market_id": "VARCHAR", "primary_domain": "VARCHAR"},
        [
            {
                "market_id": str(row["market_id"]),
                "primary_domain": assign_domain_fields(
                    question=str(row.get("question") or ""),
                    description=str(row.get("description") or ""),
                    event_slug=_optional_text(row.get("event_slug")),
                    event_id=_optional_text(row.get("event_id")),
                    category=_optional_text(row.get("category")),
                    tags=tuple(str(value) for value in cast(list[object], row.get("tags") or [])),
                ),
            }
            for row in market_rows
        ],
    )
    db.execute(
        """
        CREATE TABLE explorer_propositions_v AS
        SELECT
            p.*,
            coalesce(nullif(p.event_id, ''), nullif(p.event_slug, ''),
                     'market:' || p.market_id) AS event_key,
            d.primary_domain,
            c.component_id,
            c.component_fingerprint
        FROM propositions_v p
        JOIN market_domains_v d USING (market_id)
        JOIN node_components_v c ON c.node_id = p.proposition_id
        """
    )
    _create_event_relation_summary(db)
    _create_event_summary(db)
    _create_node_metrics(db)

    layout_rows = _layout_rows(db, component_fingerprint)
    create_and_fill(
        db,
        "visualization_layout_v",
        VISUALIZATION_LAYOUT_COLUMNS,
        layout_rows,
    )
    _create_component_summary(db)
    coverage = coverage_summary(db, input_selection=input_selection)
    return coverage


def coverage_summary(
    db: DuckDB,
    *,
    input_selection: Mapping[str, object],
) -> dict[str, object]:
    row = db.rows(
        """
        SELECT
            (SELECT count(*) FROM propositions_v) AS propositions,
            (SELECT count(DISTINCT market_id) FROM propositions_v) AS markets,
            (SELECT count(*) FROM propositions_v WHERE parse_status = 'parsed')
                AS parsed,
            (SELECT count(*) FROM propositions_v WHERE parse_status != 'parsed')
                AS parse_quarantined,
            (SELECT count(*) FROM relation_candidates_v) AS candidates,
            (SELECT count(*) FROM relation_candidates_v
             WHERE deterministic_relation IS NULL
               AND status != 'quarantined_parse') AS classification_eligible,
            (SELECT count(*) FROM relation_candidates_v
             WHERE deterministic_relation IS NULL
               AND status IN ('accepted', 'rejected', 'quarantined'))
                AS classification_assessed,
            (SELECT count(*) FROM relation_candidates_v
             WHERE status = 'not_classified_budget') AS unclassified,
            (SELECT count(*) FROM logic_edges_v) AS accepted_edges,
            (SELECT count(*) FROM rejected_edges_v) AS rejected_edges,
            (SELECT count(*) FROM quarantined_pairs_v) AS quarantined_pairs,
            (SELECT count(*) FROM event_summary_v) AS events,
            (SELECT count(*) FROM component_summary_v) AS components
        """
    )[0]
    eligible = _integer(row["classification_eligible"])
    assessed = _integer(row["classification_assessed"])
    coverage = 1.0 if eligible == 0 else assessed / eligible
    return {
        "schema_version": "coverage-summary-v1",
        "all_market_selection": not bool(input_selection.get("truncated")),
        "input_selection": dict(input_selection),
        "markets": _integer(row["markets"]),
        "propositions": _integer(row["propositions"]),
        "events": _integer(row["events"]),
        "components": _integer(row["components"]),
        "parsed": _integer(row["parsed"]),
        "parse_quarantined": _integer(row["parse_quarantined"]),
        "candidates": _integer(row["candidates"]),
        "classification_eligible": eligible,
        "classification_assessed": assessed,
        "classification_unclassified": _integer(row["unclassified"]),
        "classification_coverage": coverage,
        "classification_gap": 1.0 - coverage,
        "accepted_edges": _integer(row["accepted_edges"]),
        "rejected_edges": _integer(row["rejected_edges"]),
        "quarantined_pairs": _integer(row["quarantined_pairs"]),
    }


def _create_event_relation_summary(db: DuckDB) -> None:
    db.execute(
        """
        CREATE TABLE event_relation_summary_v AS
        WITH joined AS (
            SELECT
                CASE WHEN e.edge_type = 'implies' THEN src.event_key
                     ELSE least(src.event_key, dst.event_key) END AS src_event_key,
                CASE WHEN e.edge_type = 'implies' THEN dst.event_key
                     ELSE greatest(src.event_key, dst.event_key) END AS dst_event_key,
                e.edge_type,
                e.confidence,
                e.discovery_method,
                src.market_id AS src_market_id,
                dst.market_id AS dst_market_id
            FROM logic_edges_v e
            JOIN explorer_propositions_v src
              ON src.proposition_id = e.src_node_id
            JOIN explorer_propositions_v dst
              ON dst.proposition_id = e.dst_node_id
        )
        SELECT
            src_event_key,
            dst_event_key,
            edge_type,
            count(*)::BIGINT AS edge_count,
            min(confidence)::DOUBLE AS min_confidence,
            max(confidence)::DOUBLE AS max_confidence,
            avg(confidence)::DOUBLE AS mean_confidence,
            count(*) FILTER (WHERE discovery_method = 'deterministic')::BIGINT
                AS deterministic_count,
            count(*) FILTER (
                WHERE discovery_method = 'generative_consensus'
            )::BIGINT AS consensus_count,
            count(DISTINCT src_market_id)::BIGINT AS source_market_count,
            count(DISTINCT dst_market_id)::BIGINT AS destination_market_count,
            true AS aggregation_only
        FROM joined
        GROUP BY src_event_key, dst_event_key, edge_type
        ORDER BY src_event_key, dst_event_key, edge_type
        """
    )


def _create_event_summary(db: DuckDB) -> None:
    db.execute(
        """
        CREATE TABLE event_summary_v AS
        WITH base AS (
            SELECT
                p.event_key,
                min(p.event_id) FILTER (
                    WHERE p.event_id IS NOT NULL AND p.event_id != ''
                )
                    AS event_id,
                min(p.event_slug) FILTER (
                    WHERE p.event_slug IS NOT NULL AND p.event_slug != ''
                ) AS event_slug,
                coalesce(
                    min(p.event_slug) FILTER (
                        WHERE p.event_slug IS NOT NULL AND p.event_slug != ''
                    ),
                    min(p.event_id) FILTER (
                        WHERE p.event_id IS NOT NULL AND p.event_id != ''
                    ),
                    min(p.question)
                ) AS label,
                min(p.primary_domain) AS primary_domain,
                min(p.category) FILTER (
                    WHERE p.category IS NOT NULL AND p.category != ''
                )
                    AS category,
                count(DISTINCT p.market_id)::BIGINT AS market_count,
                count(*)::BIGINT AS proposition_count,
                count(DISTINCT p.market_id) FILTER (WHERE n.is_active)::BIGINT
                    AS active_market_count,
                count(DISTINCT p.market_id) FILTER (WHERE n.is_closed)::BIGINT
                    AS closed_market_count,
                count(DISTINCT p.component_id)::BIGINT AS component_count,
                min(n.first_seen_ts) AS first_seen_ts,
                max(n.last_seen_ts) AS last_seen_ts
            FROM explorer_propositions_v p
            JOIN nodes_table n ON n.node_id = p.proposition_id
            GROUP BY p.event_key
        ), edge_touch AS (
            SELECT DISTINCT e.proposal_id, p.event_key, e.edge_type,
                            e.discovery_method
            FROM logic_edges_v e
            JOIN explorer_propositions_v p
              ON p.proposition_id IN (e.src_node_id, e.dst_node_id)
        ), edge_counts AS (
            SELECT
                event_key,
                count(*)::BIGINT AS accepted_edge_count,
                count(*) FILTER (WHERE discovery_method = 'deterministic')::BIGINT
                    AS deterministic_edge_count,
                count(*) FILTER (
                    WHERE discovery_method = 'generative_consensus'
                )::BIGINT AS consensus_edge_count,
                count(*) FILTER (WHERE edge_type = 'complement')::BIGINT
                    AS complement_count,
                count(*) FILTER (WHERE edge_type = 'equivalent')::BIGINT
                    AS equivalent_count,
                count(*) FILTER (WHERE edge_type = 'mutually_exclusive')::BIGINT
                    AS mutually_exclusive_count,
                count(*) FILTER (WHERE edge_type = 'implies')::BIGINT
                    AS implies_count,
                count(*) FILTER (WHERE edge_type = 'compatible')::BIGINT
                    AS compatible_count
            FROM edge_touch GROUP BY event_key
        ), rejected_touch AS (
            SELECT p.event_key, count(DISTINCT r.proposal_id)::BIGINT AS count
            FROM rejected_edges_v r
            JOIN explorer_propositions_v p
              ON p.proposition_id IN (r.src_node_id, r.dst_node_id)
            GROUP BY p.event_key
        ), quarantine_touch AS (
            SELECT p.event_key, count(DISTINCT q.quarantine_id)::BIGINT AS count
            FROM quarantined_pairs_v q
            JOIN explorer_propositions_v p
              ON p.proposition_id IN (
                    q.proposition_a_id, coalesce(q.proposition_b_id, '')
              )
            GROUP BY p.event_key
        ), classification_touch AS (
            SELECT p.event_key,
                   count(DISTINCT (
                       c.proposition_a_id, c.proposition_b_id
                   )) FILTER (
                       WHERE c.deterministic_relation IS NULL
                         AND c.status != 'quarantined_parse'
                   )::BIGINT AS eligible,
                   count(DISTINCT (
                       c.proposition_a_id, c.proposition_b_id
                   )) FILTER (
                       WHERE c.deterministic_relation IS NULL
                         AND c.status IN ('accepted','rejected','quarantined')
                   )::BIGINT AS assessed,
                   count(DISTINCT (
                       c.proposition_a_id, c.proposition_b_id
                   )) FILTER (
                       WHERE c.status = 'not_classified_budget'
                   )::BIGINT AS unclassified
            FROM relation_candidates_v c
            JOIN explorer_propositions_v p
              ON p.proposition_id IN (
                    c.proposition_a_id, c.proposition_b_id
              )
            GROUP BY p.event_key
        )
        SELECT
            b.event_key, b.event_id, b.event_slug, b.label,
            b.primary_domain, b.category, b.market_count, b.proposition_count,
            b.active_market_count, b.closed_market_count,
            coalesce(e.accepted_edge_count, 0)::BIGINT AS accepted_edge_count,
            coalesce(r.count, 0)::BIGINT AS rejected_edge_count,
            coalesce(q.count, 0)::BIGINT AS quarantined_pair_count,
            coalesce(c.unclassified, 0)::BIGINT AS unclassified_pair_count,
            coalesce(c.eligible, 0)::BIGINT AS classification_eligible_count,
            coalesce(c.assessed, 0)::BIGINT AS classification_assessed_count,
            CASE WHEN coalesce(c.eligible, 0) = 0 THEN 1.0
                 ELSE c.assessed::DOUBLE / c.eligible END
                AS classification_coverage,
            coalesce(e.deterministic_edge_count, 0)::BIGINT
                AS deterministic_edge_count,
            coalesce(e.consensus_edge_count, 0)::BIGINT AS consensus_edge_count,
            coalesce(e.complement_count, 0)::BIGINT AS complement_count,
            coalesce(e.equivalent_count, 0)::BIGINT AS equivalent_count,
            coalesce(e.mutually_exclusive_count, 0)::BIGINT
                AS mutually_exclusive_count,
            coalesce(e.implies_count, 0)::BIGINT AS implies_count,
            coalesce(e.compatible_count, 0)::BIGINT AS compatible_count,
            b.component_count, b.first_seen_ts, b.last_seen_ts
        FROM base b
        LEFT JOIN edge_counts e USING (event_key)
        LEFT JOIN rejected_touch r USING (event_key)
        LEFT JOIN quarantine_touch q USING (event_key)
        LEFT JOIN classification_touch c USING (event_key)
        ORDER BY b.event_key
        """
    )


def _create_node_metrics(db: DuckDB) -> None:
    db.execute(
        """
        CREATE TABLE node_metrics_v AS
        WITH accepted AS (
            SELECT node_id,
                   count(*)::BIGINT AS total_degree,
                   count(*) FILTER (
                       WHERE edge_type != 'implies' OR node_id = dst_node_id
                   )::BIGINT AS incoming_degree,
                   count(*) FILTER (
                       WHERE edge_type != 'implies' OR node_id = src_node_id
                   )::BIGINT AS outgoing_degree,
                   count(*) FILTER (WHERE edge_type = 'complement')::BIGINT
                       AS complement_degree,
                   count(*) FILTER (WHERE edge_type = 'equivalent')::BIGINT
                       AS equivalent_degree,
                   count(*) FILTER (
                       WHERE edge_type = 'mutually_exclusive'
                   )::BIGINT AS mutually_exclusive_degree,
                   count(*) FILTER (WHERE edge_type = 'implies')::BIGINT
                       AS implies_degree,
                   count(*) FILTER (WHERE edge_type = 'compatible')::BIGINT
                       AS compatible_degree
            FROM (
                SELECT e.*, endpoint.node_id
                FROM logic_edges_v e
                CROSS JOIN LATERAL (
                    VALUES (e.src_node_id), (e.dst_node_id)
                ) endpoint(node_id)
            )
            GROUP BY node_id
        ), rejected AS (
            SELECT node_id, count(DISTINCT proposal_id)::BIGINT AS count
            FROM (
                SELECT proposal_id, src_node_id AS node_id FROM rejected_edges_v
                UNION ALL
                SELECT proposal_id, dst_node_id AS node_id FROM rejected_edges_v
            ) GROUP BY node_id
        ), quarantined AS (
            SELECT node_id, count(DISTINCT quarantine_id)::BIGINT AS count
            FROM (
                SELECT quarantine_id, proposition_a_id AS node_id
                FROM quarantined_pairs_v
                UNION ALL
                SELECT quarantine_id, proposition_b_id AS node_id
                FROM quarantined_pairs_v WHERE proposition_b_id IS NOT NULL
            ) GROUP BY node_id
        ), classification_touch AS (
            SELECT proposition_a_id AS proposition_id,
                   deterministic_relation, status
            FROM relation_candidates_v
            UNION ALL
            SELECT proposition_b_id AS proposition_id,
                   deterministic_relation, status
            FROM relation_candidates_v
        ), classification AS (
            SELECT proposition_id,
                   CASE
                       WHEN count(*) FILTER (
                           WHERE status = 'not_classified_budget'
                       ) > 0 THEN 'partial'
                       WHEN count(*) FILTER (
                           WHERE status IN ('accepted','rejected','quarantined')
                       ) > 0 THEN 'assessed'
                       ELSE 'not_retrieved'
                   END AS classification_state,
                   count(*) FILTER (
                       WHERE deterministic_relation IS NULL
                         AND status != 'quarantined_parse'
                   )::BIGINT AS eligible,
                   count(*) FILTER (
                       WHERE deterministic_relation IS NULL
                         AND status IN ('accepted','rejected','quarantined')
                   )::BIGINT AS assessed,
                   count(*) FILTER (
                       WHERE status = 'not_classified_budget'
                   )::BIGINT AS unclassified
            FROM classification_touch
            GROUP BY proposition_id
        )
        SELECT
            p.proposition_id AS node_id,
            p.market_id,
            p.event_key,
            p.component_id,
            coalesce(a.total_degree, 0)::BIGINT AS total_degree,
            coalesce(a.incoming_degree, 0)::BIGINT AS incoming_degree,
            coalesce(a.outgoing_degree, 0)::BIGINT AS outgoing_degree,
            coalesce(a.complement_degree, 0)::BIGINT AS complement_degree,
            coalesce(a.equivalent_degree, 0)::BIGINT AS equivalent_degree,
            coalesce(a.mutually_exclusive_degree, 0)::BIGINT
                AS mutually_exclusive_degree,
            coalesce(a.implies_degree, 0)::BIGINT AS implies_degree,
            coalesce(a.compatible_degree, 0)::BIGINT AS compatible_degree,
            coalesce(r.count, 0)::BIGINT AS rejected_count,
            coalesce(q.count, 0)::BIGINT AS quarantine_count,
            p.parse_status,
            coalesce(c.classification_state, 'not_retrieved')
                AS classification_state,
            coalesce(c.eligible, 0)::BIGINT AS classification_eligible_count,
            coalesce(c.assessed, 0)::BIGINT AS classification_assessed_count,
            coalesce(c.unclassified, 0)::BIGINT AS unclassified_pair_count,
            CASE WHEN coalesce(c.eligible, 0) = 0 THEN 1.0
                 ELSE c.assessed::DOUBLE / c.eligible END
                AS classification_coverage
        FROM explorer_propositions_v p
        LEFT JOIN accepted a ON a.node_id = p.proposition_id
        LEFT JOIN rejected r ON r.node_id = p.proposition_id
        LEFT JOIN quarantined q ON q.node_id = p.proposition_id
        LEFT JOIN classification c ON c.proposition_id = p.proposition_id
        ORDER BY p.proposition_id
        """
    )


def _layout_rows(
    db: DuckDB,
    component_fingerprints: Mapping[str, str],
) -> list[dict[str, Any]]:
    components = db.rows(
        """
        SELECT component_id, count(*)::BIGINT AS proposition_count
        FROM explorer_propositions_v
        GROUP BY component_id
        ORDER BY proposition_count DESC, component_id
        """
    )
    column_count = max(1, math.ceil(math.sqrt(len(components))))
    component_positions: dict[str, tuple[float, float, float, int]] = {}
    rows: list[dict[str, Any]] = []
    for rank, component in enumerate(components):
        component_id = str(component["component_id"])
        proposition_count = _integer(component["proposition_count"])
        radius = max(24.0, math.sqrt(proposition_count) * 8.0)
        x = float(rank % column_count) * 1_000.0
        y = float(rank // column_count) * 1_000.0
        component_positions[component_id] = (x, y, radius, rank)
        rows.append(
            {
                "layout_level": "component",
                "object_id": component_id,
                "parent_id": None,
                "x": x,
                "y": y,
                "radius": radius,
                "layout_rank": rank,
                "layout_version": VISUALIZATION_LAYOUT_VERSION,
                "graph_fingerprint": component_fingerprints[component_id],
            }
        )
    events = db.rows(
        """
        WITH membership AS (
            SELECT event_key, component_id, count(*)::BIGINT AS members
            FROM explorer_propositions_v
            GROUP BY event_key, component_id
        ), ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY event_key ORDER BY members DESC, component_id
            ) AS choice
            FROM membership
        )
        SELECT e.event_key, e.proposition_count, e.accepted_edge_count,
               r.component_id
        FROM event_summary_v e
        JOIN ranked r ON r.event_key = e.event_key AND r.choice = 1
        ORDER BY r.component_id, e.accepted_edge_count DESC,
                 e.proposition_count DESC, e.event_key
        """
    )
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for event in events:
        grouped[str(event["component_id"])].append(event)
    for component_id, component_events in sorted(grouped.items()):
        center_x, center_y, component_radius, _ = component_positions[component_id]
        count = len(component_events)
        for rank, event in enumerate(component_events):
            ring = int(math.sqrt(rank))
            slots = max(1, 6 * (ring + 1))
            angle = 2.0 * math.pi * (rank % slots) / slots
            distance = min(component_radius * 0.8, 18.0 + ring * 18.0)
            event_key = str(event["event_key"])
            rows.append(
                {
                    "layout_level": "event",
                    "object_id": event_key,
                    "parent_id": component_id,
                    "x": center_x + math.cos(angle) * distance,
                    "y": center_y + math.sin(angle) * distance,
                    "radius": max(
                        4.0,
                        math.sqrt(_integer(event["proposition_count"])) * 2.0,
                    ),
                    "layout_rank": rank,
                    "layout_version": VISUALIZATION_LAYOUT_VERSION,
                    "graph_fingerprint": component_fingerprints[component_id],
                }
            )
        if count == 1:
            rows[-1]["x"] = center_x
            rows[-1]["y"] = center_y
    return rows


def _create_component_summary(db: DuckDB) -> None:
    db.execute(
        """
        CREATE TABLE component_summary_v AS
        WITH nodes AS (
            SELECT
                component_id,
                min(component_fingerprint) AS component_fingerprint,
                count(*)::BIGINT AS proposition_count,
                count(DISTINCT market_id)::BIGINT AS market_count,
                count(DISTINCT event_key)::BIGINT AS event_count,
                list(proposition_id ORDER BY proposition_id)[:5]
                    AS representative_node_ids
            FROM explorer_propositions_v
            GROUP BY component_id
        ), edges AS (
            SELECT p.component_id,
                   count(*)::BIGINT AS edge_count,
                   count(*) FILTER (
                       WHERE e.discovery_method = 'deterministic'
                   )::BIGINT AS deterministic_edge_count,
                   count(*) FILTER (
                       WHERE e.discovery_method = 'generative_consensus'
                   )::BIGINT AS consensus_edge_count
            FROM logic_edges_v e
            JOIN explorer_propositions_v p
              ON p.proposition_id = e.src_node_id
            GROUP BY p.component_id
        ), quarantine AS (
            SELECT p.component_id,
                   count(DISTINCT q.quarantine_id)::BIGINT AS count
            FROM quarantined_pairs_v q
            JOIN explorer_propositions_v p
              ON p.proposition_id IN (
                   q.proposition_a_id, coalesce(q.proposition_b_id, '')
              )
            GROUP BY p.component_id
        ), candidate_touch AS (
            SELECT DISTINCT
                   c.proposition_a_id, c.proposition_b_id,
                   p.component_id, c.deterministic_relation, c.status
            FROM relation_candidates_v c
            JOIN explorer_propositions_v p
              ON p.proposition_id IN (
                   c.proposition_a_id, c.proposition_b_id
              )
        ), candidate_coverage AS (
            SELECT component_id,
                   count(*) FILTER (
                       WHERE c.deterministic_relation IS NULL
                         AND c.status != 'quarantined_parse'
                   )::BIGINT AS eligible,
                   count(*) FILTER (
                       WHERE c.deterministic_relation IS NULL
                         AND c.status IN ('accepted','rejected','quarantined')
                   )::BIGINT AS assessed,
                   count(*) FILTER (
                       WHERE c.status = 'not_classified_budget'
                   )::BIGINT AS unclassified
            FROM candidate_touch c
            GROUP BY component_id
        ), bounds AS (
            SELECT parent_id AS component_id,
                   min(x - radius)::DOUBLE AS min_x,
                   min(y - radius)::DOUBLE AS min_y,
                   max(x + radius)::DOUBLE AS max_x,
                   max(y + radius)::DOUBLE AS max_y
            FROM visualization_layout_v
            WHERE layout_level = 'event'
            GROUP BY parent_id
        )
        SELECT
            n.component_id,
            n.component_fingerprint,
            n.proposition_count,
            n.market_count,
            n.event_count,
            coalesce(e.edge_count, 0)::BIGINT AS edge_count,
            coalesce(e.deterministic_edge_count, 0)::BIGINT
                AS deterministic_edge_count,
            coalesce(e.consensus_edge_count, 0)::BIGINT
                AS consensus_edge_count,
            coalesce(q.count, 0)::BIGINT AS quarantined_pair_count,
            coalesce(c.unclassified, 0)::BIGINT AS unclassified_pair_count,
            CASE WHEN coalesce(c.eligible, 0) = 0 THEN 1.0
                 ELSE c.assessed::DOUBLE / c.eligible END
                AS classification_coverage,
            n.representative_node_ids,
            coalesce(b.min_x, l.x - l.radius)::DOUBLE AS layout_min_x,
            coalesce(b.min_y, l.y - l.radius)::DOUBLE AS layout_min_y,
            coalesce(b.max_x, l.x + l.radius)::DOUBLE AS layout_max_x,
            coalesce(b.max_y, l.y + l.radius)::DOUBLE AS layout_max_y
        FROM nodes n
        LEFT JOIN edges e USING (component_id)
        LEFT JOIN quarantine q USING (component_id)
        LEFT JOIN candidate_coverage c USING (component_id)
        LEFT JOIN bounds b USING (component_id)
        JOIN visualization_layout_v l
          ON l.layout_level = 'component' AND l.object_id = n.component_id
        ORDER BY n.component_id
        """
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _integer(value: object) -> int:
    return int(cast(int, value))
