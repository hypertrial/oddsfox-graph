"""Human-first World Cup 2026 explorer queries.

The discovery adapter owns semantic validation.  This module deliberately uses
those structured columns and never attempts to infer teams or stages from copy.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
import re
from typing import Literal, cast

from pydantic import BaseModel

from ..queries import DuckDB
from .contracts import (
    ClaimSummary,
    CompareResult,
    CoverageStatus,
    EdgeMode,
    EntitySearchResult,
    ExploreHome,
    ExplorerCapabilities,
    GraphDisplayStats,
    HumanHighlight,
    MarketDetail,
    RelationshipDetail,
    RelationshipGroupSummary,
    StageDetail,
    StageSummary,
    TeamDetail,
    TeamSummary,
    TournamentScope,
)


_REQUIRED_COLUMNS = frozenset(
    {
        "team_name",
        "stage_key",
        "stage_rank",
        "progression_level",
        "market_direction",
        "progression_outcome",
        "is_progression",
        "market_status",
        "is_still_alive",
    }
)

_STAGE_LABELS = {
    0: "Round of 32",
    1: "Round of 16",
    2: "Quarterfinals",
    3: "Semifinals",
    4: "Final",
    5: "World Cup winner",
}

_STAGE_KEYS = {
    0: "round_of_32",
    1: "round_of_16",
    2: "quarterfinal",
    3: "semifinal",
    4: "final",
    5: "winner",
}

_PROGRESSION_PHRASES = {
    0: "reaches the round of 32",
    1: "reaches the round of 16",
    2: "reaches the quarterfinals",
    3: "reaches the semifinals",
    4: "reaches the final",
    5: "wins the World Cup",
}

_NEGATIVE_PROGRESSION_PHRASES = {
    0: "reach the round of 32",
    1: "reach the round of 16",
    2: "reach the quarterfinals",
    3: "reach the semifinals",
    4: "reach the final",
    5: "win the World Cup",
}


def essential_relationship_rows(
    rows: Iterable[dict[str, object]],
    *,
    preserve_proposal_ids: frozenset[str] = frozenset(),
) -> list[dict[str, object]]:
    """Return one deterministic relationship projection with redundant implications removed."""

    deduplicated: dict[tuple[str, str, str], dict[str, object]] = {}
    ordered = sorted(
        rows,
        key=lambda item: (
            str(item["proposal_id"]) not in preserve_proposal_ids,
            -float(cast(float, item["confidence"])),
            str(item["proposal_id"]),
        ),
    )
    for row in ordered:
        relation = str(row["edge_type"])
        source = str(row["src_node_id"])
        target = str(row["dst_node_id"])
        if relation != "implies" and source > target:
            source, target = target, source
        deduplicated.setdefault((relation, source, target), row)
    candidates = list(deduplicated.values())
    traversable = [
        row
        for row in candidates
        if str(row["edge_type"]) in {"implies", "equivalent"}
    ]

    def reachable(
        source: str,
        target: str,
        *,
        excluded: str,
        minimum_confidence: float,
    ) -> bool:
        adjacency: dict[str, list[str]] = defaultdict(list)
        for edge in traversable:
            if str(edge["proposal_id"]) == excluded:
                continue
            if float(cast(float, edge["confidence"])) < minimum_confidence:
                continue
            left = str(edge["src_node_id"])
            right = str(edge["dst_node_id"])
            adjacency[left].append(right)
            if str(edge["edge_type"]) == "equivalent":
                adjacency[right].append(left)
        frontier = [source]
        seen = {source}
        while frontier:
            node = frontier.pop()
            for neighbor in adjacency.get(node, ()):
                if neighbor == target:
                    return True
                if neighbor not in seen:
                    seen.add(neighbor)
                    frontier.append(neighbor)
        return False

    retained: list[dict[str, object]] = []
    for row in candidates:
        relation = str(row["edge_type"])
        proposal_id = str(row["proposal_id"])
        if relation != "implies" or proposal_id in preserve_proposal_ids:
            retained.append(row)
            continue
        source = str(row["src_node_id"])
        target = str(row["dst_node_id"])
        confidence = float(cast(float, row["confidence"]))
        in_cycle = reachable(
            target,
            source,
            excluded=proposal_id,
            minimum_confidence=0.0,
        )
        redundant = reachable(
            source,
            target,
            excluded=proposal_id,
            minimum_confidence=confidence,
        )
        if in_cycle or not redundant:
            retained.append(row)
    return sorted(
        retained,
        key=lambda row: (
            -float(cast(float, row["confidence"])),
            str(row["edge_type"]),
            str(row["src_node_id"]),
            str(row["dst_node_id"]),
            str(row["proposal_id"]),
        ),
    )


class HumanExplorer:
    """Structured WC2026 reads over one completed graph database."""

    def __init__(
        self,
        db: DuckDB,
        *,
        coverage: Mapping[str, object],
        build: Mapping[str, object],
    ) -> None:
        self.db = db
        self.coverage = dict(coverage)
        self.build = dict(build)
        columns = {
            str(row["column_name"])
            for row in db.rows("DESCRIBE explorer_propositions_v")
        }
        missing = sorted(_REQUIRED_COLUMNS - columns)
        if missing:
            raise ValueError(
                "This graph is not a WC2026 human-explorer output; missing "
                "structured field(s): "
                + ", ".join(missing)
                + ". Run a clean discovery with "
                "--input-profile polymarket-wc2026-graph-hourly-v1."
            )

    def explore_home(
        self,
        *,
        team_limit: int = 24,
        highlight_limit: int = 6,
    ) -> ExploreHome:
        if not 1 <= team_limit <= 100:
            raise ValueError("team_limit must be between 1 and 100")
        if not 1 <= highlight_limit <= 12:
            raise ValueError("highlight_limit must be between 1 and 12")
        claims = self._claims()
        all_relationships = self._relationships(claims, edge_mode="all")
        relationships = self._relationships(claims, edge_mode="essential")
        stages = self._stages(claims)
        teams = self._teams(claims)
        highlights = self._highlights(relationships, highlight_limit)
        groups = self._relationship_groups(relationships)
        stats = graph_display_stats(
            tuple(claim.plain_claim for claim in claims.values()),
            tuple(
                (item.source.id, item.target.id) for item in relationships
            ),
            input_edge_count=len(all_relationships),
        )
        return ExploreHome(
            scope=self._scope(claims, stages, teams),
            stages=stages,
            teams=teams[:team_limit],
            notable_relationships=highlights,
            relationship_groups=groups,
            capabilities=ExplorerCapabilities(
                mode="live",
                proof=True,
                why_not=True,
                recording=True,
                regeneration=True,
            ),
            display_stats=stats,
            coverage=self.coverage,
        )

    def snapshot(
        self,
        node_ids: Iterable[str] | None = None,
        relationship_ids: Iterable[str] | None = None,
    ) -> dict[str, tuple[BaseModel, ...]]:
        """Return normalized rows used by the self-contained static explorer."""

        claims = self._claims()
        if node_ids is not None:
            selected = frozenset(node_ids)
            claims = {
                node_id: claim
                for node_id, claim in claims.items()
                if node_id in selected
            }
        all_relationships = self._relationships(
            claims,
            edge_mode="all",
            proposal_ids=relationship_ids,
        )
        relationships = self._relationships(
            claims,
            edge_mode="essential",
            proposal_ids=relationship_ids,
        )
        return {
            "stages": self._stages(claims),
            "teams": self._teams(claims),
            "markets": self._markets(claims),
            "claims": tuple(claims.values()),
            "relationships": all_relationships,
            "essential_relationships": relationships,
            "groups": self._relationship_groups(relationships),
        }

    def stages(self) -> tuple[StageSummary, ...]:
        return self._stages(self._claims())

    def stage(self, stage_key: str) -> StageDetail:
        levels = {key: level for level, key in _STAGE_KEYS.items()}
        if stage_key not in levels:
            raise KeyError(f"Unknown World Cup stage {stage_key!r}")
        level = levels[stage_key]
        claims = self._claims()
        matching = {
            node_id: claim
            for node_id, claim in claims.items()
            if claim.normalized_progression_level == level
        }
        summary = next(item for item in self._stages(claims) if item.stage_key == stage_key)
        team_names = {claim.canonical_team_name for claim in matching.values()}
        teams = tuple(
            item
            for item in self._teams(claims)
            if item.canonical_team_name in team_names
        )
        return StageDetail(
            summary=summary,
            teams=teams,
            markets=self._markets(matching),
        )

    def teams(
        self,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> tuple[tuple[TeamSummary, ...], str | None, bool]:
        if not 1 <= limit <= 1_000:
            raise ValueError("team limit must be between 1 and 1000")
        teams = self._teams(self._claims())
        if cursor is not None:
            teams = tuple(item for item in teams if item.team_key > cursor)
        selected = teams[: limit + 1]
        truncated = len(selected) > limit
        page = selected[:limit]
        return page, (page[-1].team_key if truncated else None), truncated

    def team(self, team_key: str) -> TeamDetail:
        claims = self._claims()
        matching = {
            node_id: claim
            for node_id, claim in claims.items()
            if _team_key(claim.canonical_team_name) == team_key
        }
        if not matching:
            raise KeyError(f"Unknown World Cup team {team_key!r}")
        summary = next(
            item for item in self._teams(claims) if item.team_key == team_key
        )
        return TeamDetail(summary=summary, markets=self._markets(matching))

    def market(self, market_id: str) -> MarketDetail:
        claims = self._claims()
        markets = self._markets(
            {
                node_id: claim
                for node_id, claim in claims.items()
                if claim.market_id == market_id
            }
        )
        if not markets:
            raise KeyError(f"Unknown World Cup market {market_id!r}")
        return markets[0]

    def relationship(self, proposal_id: str) -> RelationshipDetail:
        claims = self._claims()
        for relationship in self._relationships(claims, edge_mode="all"):
            if relationship.proposal_id == proposal_id:
                return relationship
        raise KeyError(f"Unknown World Cup relationship {proposal_id!r}")

    def highlights(
        self,
        *,
        limit: int = 6,
        min_confidence: float = 0.95,
    ) -> tuple[HumanHighlight, ...]:
        if not 1 <= limit <= 12:
            raise ValueError("highlight limit must be between 1 and 12")
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        claims = self._claims()
        relationships = tuple(
            item
            for item in self._relationships(claims, edge_mode="essential")
            if item.confidence >= min_confidence
        )
        return self._highlights(relationships, limit)

    def search(self, query: str, *, limit: int = 20) -> tuple[EntitySearchResult, ...]:
        text = " ".join(query.split()).casefold()
        if not text:
            raise ValueError("Search query must not be empty")
        if not 1 <= limit <= 100:
            raise ValueError("search limit must be between 1 and 100")
        claims = self._claims()
        candidates: list[tuple[int, str, EntitySearchResult]] = []
        for team in self._teams(claims):
            self._add_search_candidate(
                candidates,
                text,
                "team",
                team.team_key,
                team.canonical_team_name,
                f"{team.market_count} progression "
                f"{'market' if team.market_count == 1 else 'markets'}",
            )
        for stage in self._stages(claims):
            self._add_search_candidate(
                candidates,
                text,
                "stage",
                stage.stage_key,
                stage.label,
                f"{stage.team_count} {'team' if stage.team_count == 1 else 'teams'}",
            )
        for market in self._markets(claims):
            self._add_search_candidate(
                candidates,
                text,
                "market",
                market.market_id,
                market.question,
                f"{market.canonical_team_name} · {_STAGE_LABELS[market.stage_rank]}",
            )
        for claim in claims.values():
            self._add_search_candidate(
                candidates,
                text,
                "claim",
                claim.id,
                claim.plain_claim,
                f"{claim.answer} outcome",
            )
        candidates.sort(key=lambda item: (item[0], item[1], item[2].id))
        return tuple(item[2] for item in candidates[:limit])

    def search_claims(
        self, query: str, *, limit: int = 20
    ) -> tuple[ClaimSummary, ...]:
        """Return graph claims matched and ranked by their human-readable text."""

        text = " ".join(query.split()).casefold()
        if not text:
            raise ValueError("Search query must not be empty")
        if not 1 <= limit <= 100:
            raise ValueError("search limit must be between 1 and 100")
        candidates: list[tuple[int, str, ClaimSummary]] = []
        for claim in self._claims().values():
            searchable = " ".join(
                (
                    claim.plain_claim,
                    claim.question,
                    claim.answer,
                    claim.technical_canonical_label,
                    claim.id,
                )
            ).casefold()
            position = searchable.find(text)
            if position >= 0:
                candidates.append((position, claim.plain_claim.casefold(), claim))
        candidates.sort(key=lambda item: (item[0], item[1], item[2].id))
        seen_labels: set[str] = set()
        result: list[ClaimSummary] = []
        for _, label, claim in candidates:
            if label in seen_labels:
                continue
            seen_labels.add(label)
            result.append(claim)
            if len(result) == limit:
                break
        return tuple(result)

    def compare(self, source_id: str, target_id: str, *, max_hops: int = 4) -> CompareResult:
        if not 1 <= max_hops <= 4:
            raise ValueError("max_hops must be between 1 and 4")
        claims = self._claims()
        try:
            source = claims[source_id]
            target = claims[target_id]
        except KeyError as exc:
            raise KeyError(f"Unknown World Cup claim {exc.args[0]!r}") from exc
        if source_id == target_id:
            return CompareResult(
                status="same_claim",
                source=source,
                target=target,
                explanation="You selected the same outcome twice.",
            )
        all_relationships = self._relationships(claims, edge_mode="all")
        direct = self._direct(all_relationships, source_id, target_id)
        if direct is not None:
            return CompareResult(
                status="direct",
                source=source,
                target=target,
                direct=direct,
                explanation=_relationship_explanation(direct),
            )
        relationships = self._relationships(claims, edge_mode="essential")
        path = self._shortest_path(
            relationships, source_id, target_id, max_hops=max_hops
        )
        if path:
            return CompareResult(
                status="path",
                source=source,
                target=target,
                path=path,
                explanation=(
                    f"A {len(path)}-step logic path connects these outcomes."
                ),
            )
        return CompareResult(
            status="no_proven_relationship",
            source=source,
            target=target,
            explanation=(
                "No supported direct relationship or short progression proof "
                "connects these outcomes."
            ),
        )

    def _claims(self) -> dict[str, ClaimSummary]:
        rows = self.db.rows(
            """
            SELECT p.proposition_id, p.market_id, p.event_slug, p.question,
                   n.canonical_proposition, p.team_name AS canonical_team_name,
                   p.stage_key, p.stage_rank,
                   p.progression_level AS normalized_progression_level,
                   p.market_direction, p.is_progression AS is_progression_token,
                   p.market_status, p.is_still_alive,
                   epoch(p.market_close_time)::BIGINT AS market_close_epoch,
                   n.outcome_label, n.is_active, n.is_closed
            FROM explorer_propositions_v p
            JOIN nodes_table n ON n.node_id = p.proposition_id
            ORDER BY p.proposition_id
            """
        )
        claims: dict[str, ClaimSummary] = {}
        for row in rows:
            answer = str(row["outcome_label"]).strip().title()
            if answer not in {"Yes", "No"}:
                raise ValueError(
                    f"WC2026 claim {row['proposition_id']!r} has non-binary "
                    f"outcome {answer!r}"
                )
            rank = int(cast(int, row["stage_rank"]))
            level = int(cast(int, row["normalized_progression_level"]))
            if rank not in _STAGE_LABELS or level not in _STAGE_LABELS:
                raise ValueError("WC2026 stage ranks must be between 0 and 5")
            team = str(row["canonical_team_name"]).strip()
            progression = bool(row["is_progression_token"])
            plain = (
                f"{team} {_PROGRESSION_PHRASES[level]}"
                if progression
                else f"{team} does not {_NEGATIVE_PROGRESSION_PHRASES[level]}"
            )
            market_status = str(row.get("market_status") or "").strip()
            if not market_status:
                market_status = (
                    "closed" if bool(row.get("is_closed")) else
                    "active" if bool(row.get("is_active")) else "inactive"
                )
            claim = ClaimSummary(
                id=str(row["proposition_id"]),
                market_id=str(row["market_id"]),
                canonical_team_name=team,
                stage_key=str(row["stage_key"]),
                stage_rank=rank,
                normalized_progression_level=level,
                question=str(row["question"]),
                answer=cast(Literal["Yes", "No"], answer),
                plain_claim=plain,
                is_progression_token=progression,
                market_status=market_status,
                is_still_alive=(
                    None if row.get("is_still_alive") is None
                    else bool(row["is_still_alive"])
                ),
                market_close_epoch=(
                    None
                    if row.get("market_close_epoch") is None
                    else int(cast(int, row["market_close_epoch"]))
                ),
                technical_canonical_label=str(row["canonical_proposition"]),
            )
            claims[claim.id] = claim
        if not claims:
            raise ValueError("WC2026 graph contains no claims")
        return claims

    def _markets(self, claims: Mapping[str, ClaimSummary]) -> tuple[MarketDetail, ...]:
        by_market: dict[str, list[ClaimSummary]] = defaultdict(list)
        for claim in claims.values():
            by_market[claim.market_id].append(claim)
        if not by_market:
            return ()
        rows = self.db.rows(
            """
            SELECT DISTINCT market_id, event_slug, question,
                   team_name AS canonical_team_name, stage_key, stage_rank,
                   progression_level AS normalized_progression_level,
                   market_direction,
                   market_status, is_still_alive,
                   epoch(market_close_time)::BIGINT AS market_close_epoch
            FROM explorer_propositions_v
            WHERE market_id IN (SELECT unnest(?))
            ORDER BY market_id
            """,
            [sorted(by_market)],
        )
        result: list[MarketDetail] = []
        for row in rows:
            market_id = str(row["market_id"])
            direction = str(row["market_direction"])
            if direction not in {"winner", "advance", "elimination"}:
                raise ValueError(
                    f"WC2026 market {market_id!r} has invalid direction {direction!r}"
                )
            result.append(
                MarketDetail(
                    market_id=market_id,
                    event_slug=str(row.get("event_slug") or ""),
                    question=str(row["question"]),
                    canonical_team_name=str(row["canonical_team_name"]),
                    stage_key=str(row["stage_key"]),
                    stage_rank=int(cast(int, row["stage_rank"])),
                    normalized_progression_level=int(
                        cast(int, row["normalized_progression_level"])
                    ),
                    market_direction=cast(
                        Literal["winner", "advance", "elimination"], direction
                    ),
                    market_status=str(row.get("market_status") or "unknown"),
                    is_still_alive=(
                        None if row.get("is_still_alive") is None
                        else bool(row["is_still_alive"])
                    ),
                    market_close_epoch=(
                        None
                        if row.get("market_close_epoch") is None
                        else int(cast(int, row["market_close_epoch"]))
                    ),
                    claims=tuple(
                        sorted(
                            by_market[market_id],
                            key=lambda item: (not item.is_progression_token, item.id),
                        )
                    ),
                )
            )
        return tuple(result)

    def _relationships(
        self,
        claims: Mapping[str, ClaimSummary],
        *,
        edge_mode: EdgeMode,
        proposal_ids: Iterable[str] | None = None,
    ) -> tuple[RelationshipDetail, ...]:
        rows = self.db.rows(
            """
            SELECT *
            FROM logic_edges_v
            ORDER BY confidence DESC, proposal_id
            """
        )
        selected_proposals = (
            None if proposal_ids is None else frozenset(proposal_ids)
        )
        rows = [
            row
            for row in rows
            if str(row["src_node_id"]) in claims
            and str(row["dst_node_id"]) in claims
            and (
                selected_proposals is None
                or str(row["proposal_id"]) in selected_proposals
            )
        ]
        if edge_mode == "essential":
            rows = [row for row in rows if str(row["edge_type"]) != "compatible"]
            rows = essential_relationship_rows(rows)
        result: list[RelationshipDetail] = []
        for row in rows:
            source_id = str(row["src_node_id"])
            target_id = str(row["dst_node_id"])
            explanation = " ".join(
                str(row.get("explanation") or row.get("evidence") or "").split()
            )
            if not explanation:
                explanation = _plain_relation_basis(
                    str(row["edge_type"]), claims[source_id], claims[target_id]
                )
            result.append(
                RelationshipDetail.model_validate(
                    {
                        "proposal_id": str(row["proposal_id"]),
                        "source": claims[source_id],
                        "target": claims[target_id],
                        "relation": str(row["edge_type"]),
                        "basis": str(row.get("rule_id") or row.get("edge_basis") or "logic"),
                        "confidence": float(cast(float, row["confidence"])),
                        "evidence_tier": str(
                            row.get("evidence_tier") or "deterministic_rule"
                        ),
                        "discovery_method": str(row["discovery_method"]),
                        "explanation": explanation,
                    }
                )
            )
        return tuple(result)

    def _coverage_by(
        self,
        claims: Mapping[str, ClaimSummary],
        dimension: Literal["progression_level", "team_name"],
    ) -> dict[str, tuple[int, int, CoverageStatus, float | None]]:
        if not claims:
            return {}
        dimension_sql = {
            "progression_level": "p.progression_level::VARCHAR",
            "team_name": "p.team_name",
        }[dimension]
        rows = self.db.rows(
            f"""
            WITH selected AS (
                SELECT p.proposition_id, {dimension_sql} AS group_value
                FROM explorer_propositions_v p
                WHERE p.proposition_id IN (SELECT unnest(?))
            ), candidate_endpoints AS (
                SELECT proposition_a_id, proposition_b_id,
                       proposition_a_id AS proposition_id,
                       deterministic_relation, status
                FROM relation_candidates_v
                UNION ALL
                SELECT proposition_a_id, proposition_b_id,
                       proposition_b_id AS proposition_id,
                       deterministic_relation, status
                FROM relation_candidates_v
            ), candidate_touch AS (
                SELECT DISTINCT c.proposition_a_id, c.proposition_b_id,
                       s.group_value, c.deterministic_relation, c.status
                FROM candidate_endpoints c
                JOIN selected s USING (proposition_id)
            )
            SELECT group_value,
                   count(*) FILTER (
                       WHERE deterministic_relation IS NULL
                         AND status != 'quarantined_parse'
                   )::BIGINT AS eligible,
                   count(*) FILTER (
                       WHERE deterministic_relation IS NULL
                         AND status IN ('accepted', 'rejected', 'quarantined')
                   )::BIGINT AS assessed
            FROM candidate_touch
            GROUP BY group_value
            ORDER BY group_value
            """,
            [sorted(claims)],
        )
        return {
            str(row["group_value"]): _classification_coverage(
                int(cast(int, row["eligible"])),
                int(cast(int, row["assessed"])),
            )
            for row in rows
        }

    def _stages(self, claims: Mapping[str, ClaimSummary]) -> tuple[StageSummary, ...]:
        grouped: dict[int, list[ClaimSummary]] = defaultdict(list)
        for claim in claims.values():
            grouped[claim.normalized_progression_level].append(claim)
        coverage_by_level = self._coverage_by(claims, "progression_level")
        rows: list[StageSummary] = []
        for level in range(6):
            values = grouped.get(level, [])
            eligible, assessed, coverage_status, coverage = coverage_by_level.get(
                str(level), _classification_coverage(0, 0)
            )
            market_ids = {item.market_id for item in values}
            statuses = {
                market_id: next(
                    item.market_status for item in values if item.market_id == market_id
                )
                for market_id in market_ids
            }
            rows.append(
                StageSummary(
                    stage_key=_STAGE_KEYS[level],
                    label=_STAGE_LABELS[level],
                    stage_rank=level,
                    normalized_progression_level=level,
                    team_count=len({item.canonical_team_name for item in values}),
                    market_count=len(market_ids),
                    claim_count=len(values),
                    active_market_count=sum(
                        status in {"active", "live"}
                        for status in statuses.values()
                    ),
                    closed_market_count=sum(
                        status in {"closed", "resolved"}
                        for status in statuses.values()
                    ),
                    classification_eligible_count=eligible,
                    classification_assessed_count=assessed,
                    classification_status=coverage_status,
                    classification_coverage=coverage,
                )
            )
        return tuple(sorted(rows, key=lambda item: item.stage_rank))

    def _teams(self, claims: Mapping[str, ClaimSummary]) -> tuple[TeamSummary, ...]:
        grouped: dict[str, list[ClaimSummary]] = defaultdict(list)
        for claim in claims.values():
            grouped[claim.canonical_team_name].append(claim)
        coverage_by_team = self._coverage_by(claims, "team_name")
        teams: list[TeamSummary] = []
        for name, values in grouped.items():
            eligible, assessed, coverage_status, coverage = coverage_by_team.get(
                name, _classification_coverage(0, 0)
            )
            levels = [item.normalized_progression_level for item in values]
            statuses = sorted({item.market_status for item in values})
            alive_values = {item.is_still_alive for item in values} - {None}
            teams.append(
                TeamSummary(
                    team_key=_team_key(name),
                    canonical_team_name=name,
                    is_still_alive=(
                        next(iter(alive_values)) if len(alive_values) == 1 else None
                    ),
                    market_status=statuses[0] if len(statuses) == 1 else "mixed",
                    market_count=len({item.market_id for item in values}),
                    claim_count=len(values),
                    stage_keys=tuple(
                        _STAGE_KEYS[level] for level in sorted(set(levels))
                    ),
                    min_stage_rank=min(levels),
                    max_stage_rank=max(levels),
                    classification_eligible_count=eligible,
                    classification_assessed_count=assessed,
                    classification_status=coverage_status,
                    classification_coverage=coverage,
                )
            )
        return tuple(sorted(teams, key=lambda item: (item.canonical_team_name, item.team_key)))

    def _scope(
        self,
        claims: Mapping[str, ClaimSummary],
        stages: tuple[StageSummary, ...],
        teams: tuple[TeamSummary, ...],
    ) -> TournamentScope:
        selection = self.coverage.get("input_selection")
        source = selection if isinstance(selection, dict) else {}
        return TournamentScope(
            input_hourly_rows=_first_int(
                source, "input_hourly_rows", "source_rows", "rows"
            ),
            market_count=len({item.market_id for item in claims.values()}),
            claim_count=len(claims),
            team_count=len(teams),
            stage_count=len(stages),
            first_odds_hour_epoch=_optional_first_int(
                source, "first_odds_hour_epoch", "first_hour_epoch", "odds_hour_min"
            ),
            last_odds_hour_epoch=_optional_first_int(
                source, "last_odds_hour_epoch", "last_hour_epoch", "odds_hour_max"
            ),
            adapter_version=str(
                source.get("adapter_version")
                or self.build.get("input_profile")
                or "polymarket-wc2026-graph-hourly-v1"
            ),
        )

    @staticmethod
    def _highlights(
        relationships: Iterable[RelationshipDetail], limit: int
    ) -> tuple[HumanHighlight, ...]:
        ordered = sorted(
            relationships,
            key=lambda item: (
                -max(item.source.stage_rank, item.target.stage_rank),
                -item.confidence,
                item.proposal_id,
            ),
        )
        selected: list[RelationshipDetail] = []
        teams: set[str] = set()
        templates: set[tuple[object, ...]] = set()
        endpoints: set[str] = set()
        for item in ordered:
            item_teams = {
                item.source.canonical_team_name,
                item.target.canonical_team_name,
            }
            template = (
                item.relation,
                item.source.stage_rank,
                item.target.stage_rank,
                item.source.is_progression_token,
                item.target.is_progression_token,
            )
            if item_teams & teams or template in templates:
                continue
            if item.source.id in endpoints or item.target.id in endpoints:
                continue
            selected.append(item)
            teams.update(item_teams)
            templates.add(template)
            endpoints.update((item.source.id, item.target.id))
            if len(selected) == limit:
                break
        return tuple(
            HumanHighlight(rank=index, relationship=item)
            for index, item in enumerate(selected, start=1)
        )

    @staticmethod
    def _relationship_groups(
        relationships: Iterable[RelationshipDetail],
    ) -> tuple[RelationshipGroupSummary, ...]:
        winner_edges = [
            item
            for item in relationships
            if item.relation == "mutually_exclusive"
            and item.source.normalized_progression_level == 5
            and item.target.normalized_progression_level == 5
            and item.source.is_progression_token
            and item.target.is_progression_token
            and item.source.canonical_team_name != item.target.canonical_team_name
        ]
        if not winner_edges:
            return ()
        members = tuple(
            sorted(
                {
                    claim.id
                    for item in winner_edges
                    for claim in (item.source, item.target)
                }
            )
        )
        return (
            RelationshipGroupSummary(
                id="wc2026-one-winner",
                title="Only one team can win the World Cup",
                description=(
                    f"The {len(members)} listed winner outcomes form one "
                    "tournament constraint."
                ),
                relation="mutually_exclusive",
                member_claim_ids=members,
                relationship_count=len(winner_edges),
            ),
        )

    @staticmethod
    def _direct(
        relationships: Iterable[RelationshipDetail],
        source_id: str,
        target_id: str,
    ) -> RelationshipDetail | None:
        matches = [
            item
            for item in relationships
            if (
                item.source.id == source_id and item.target.id == target_id
            )
            or (
                item.relation != "implies"
                and item.source.id == target_id
                and item.target.id == source_id
            )
        ]
        if not matches:
            return None
        return min(matches, key=lambda item: (-item.confidence, item.proposal_id))

    @staticmethod
    def _shortest_path(
        relationships: Iterable[RelationshipDetail],
        source_id: str,
        target_id: str,
        *,
        max_hops: int,
    ) -> tuple[RelationshipDetail, ...]:
        adjacency: dict[str, list[tuple[str, RelationshipDetail]]] = defaultdict(list)
        for item in relationships:
            if item.relation not in {"implies", "equivalent"}:
                continue
            adjacency[item.source.id].append((item.target.id, item))
            if item.relation == "equivalent":
                adjacency[item.target.id].append((item.source.id, item))
        for values in adjacency.values():
            values.sort(key=lambda value: (-value[1].confidence, value[1].proposal_id))
        queue: deque[tuple[str, tuple[RelationshipDetail, ...]]] = deque(
            [(source_id, ())]
        )
        best_hops = {source_id: 0}
        while queue:
            node_id, path = queue.popleft()
            if len(path) >= max_hops:
                continue
            for neighbor, relationship in adjacency.get(node_id, []):
                next_path = (*path, relationship)
                if neighbor == target_id:
                    return next_path
                if best_hops.get(neighbor, max_hops + 1) <= len(next_path):
                    continue
                best_hops[neighbor] = len(next_path)
                queue.append((neighbor, next_path))
        return ()

    @staticmethod
    def _add_search_candidate(
        target: list[tuple[int, str, EntitySearchResult]],
        query: str,
        kind: str,
        identifier: str,
        label: str,
        description: str,
    ) -> None:
        haystack = f"{label} {description}".casefold()
        position = haystack.find(query)
        if position < 0:
            return
        target.append(
            (
                position,
                label.casefold(),
                EntitySearchResult.model_validate(
                    {
                        "kind": kind,
                        "id": identifier,
                        "label": label,
                        "description": description,
                    }
                ),
            )
        )


def graph_display_stats(
    labels: tuple[str, ...],
    edges: tuple[tuple[str, str], ...],
    *,
    input_edge_count: int | None = None,
) -> GraphDisplayStats:
    node_count = len(labels)
    edge_count = len(edges)
    degree: dict[str, int] = defaultdict(int)
    for source, target in edges:
        degree[source] += 1
        degree[target] += 1
    density = (
        min(1.0, edge_count / (node_count * (node_count - 1)))
        if node_count > 1
        else 0.0
    )
    uniqueness = len({label.casefold() for label in labels}) / node_count if node_count else 1.0
    maximum_degree = max(degree.values(), default=0)
    network = (
        node_count <= 15
        and edge_count <= 24
        and density <= 0.15
        and uniqueness >= 0.50
        and maximum_degree <= 8
    )
    total_edges = edge_count if input_edge_count is None else input_edge_count
    return GraphDisplayStats(
        input_node_count=node_count,
        input_edge_count=total_edges,
        display_node_count=node_count,
        display_edge_count=edge_count,
        omitted_edge_count=max(0, total_edges - edge_count),
        density=density,
        label_uniqueness=uniqueness,
        max_degree=maximum_degree,
        recommended_representation="network" if network else "grouped",
    )


def _team_key(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return value or "team"


def _plain_relation_basis(
    relation: str, source: ClaimSummary, target: ClaimSummary
) -> str:
    if relation == "implies":
        return f"If {source.plain_claim}, then {target.plain_claim}."
    if relation == "equivalent":
        return "These outcomes describe the same progression result."
    if relation == "complement":
        return "Exactly one answer can be true for this market."
    if relation == "mutually_exclusive":
        return "These outcomes cannot both happen."
    return "These outcomes are compatible."


def _relationship_explanation(item: RelationshipDetail) -> str:
    return _plain_relation_basis(item.relation, item.source, item.target)


def _classification_coverage(
    eligible: int,
    assessed: int,
) -> tuple[int, int, CoverageStatus, float | None]:
    if eligible == 0:
        return eligible, assessed, "not_applicable", None
    if assessed == 0:
        return eligible, assessed, "not_started", 0.0
    if assessed < eligible:
        return eligible, assessed, "partial", assessed / eligible
    return eligible, assessed, "complete", assessed / eligible


def _first_int(source: Mapping[str, object], *keys: str) -> int:
    return _optional_first_int(source, *keys) or 0


def _optional_first_int(source: Mapping[str, object], *keys: str) -> int | None:
    for key in keys:
        value = source.get(key)
        if value is not None:
            return int(cast(int, value))
    return None
