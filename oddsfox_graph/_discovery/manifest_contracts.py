"""Strict, mode-independent contracts for completed graph publications."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator

from .versions import (
    AGGREGATION_CONTRACT_VERSION,
    CACHE_ENTRY_VERSION,
    BUILD_MANIFEST_SCHEMA_VERSION,
    CANDIDATE_STATE_VERSION,
    CONSTRAINT_VERSION,
    DOMAIN_TAXONOMY_VERSION,
    EXECUTION_PLAN_VERSION,
    EXTRACTOR_VERSION,
    INPUT_ADAPTER_VERSION,
    NORMALIZATION_VERSION,
    PARSE_FALLBACK_VERSION,
    PUBLICATION_VERSION,
    RETRIEVAL_VERSION,
    RULE_VERSION,
    SOLVER_VERSION,
    VIEWER_API_VERSION,
    VIEWER_ARTIFACT_VERSION,
    VISUALIZATION_LAYOUT_VERSION,
    WC2026_SOURCE_SCHEMA,
    ann_version_binding,
    rule_registry_hash,
)


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CoverageStatus = Literal["not_applicable", "not_started", "partial", "complete"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CatalogInputSelection(_FrozenModel):
    strategy: Literal["all_eligible_markets", "volume_desc_then_market_id"]
    input_market_rows: int = Field(ge=0)
    input_rows: int = Field(ge=0)
    input_propositions: int = Field(ge=0)
    invalid_market_rows: int = Field(ge=0)
    eligible_markets: int = Field(ge=0)
    eligible_propositions: int = Field(ge=0)
    selected_markets: int = Field(ge=0)
    selected_propositions: int = Field(ge=0)
    truncated: bool


class WC2026InputSelection(_FrozenModel):
    strategy: Literal["all_valid_pipeline_wc2026_markets"]
    source: Literal["oddsfox-pipeline"]
    scope: Literal["wc2026"]
    universe: Literal["knockout_progression"]
    selection: Literal["all_valid_pipeline_wc2026_markets"]
    adapter_version: str
    input_hourly_rows: int = Field(ge=0)
    input_rows: int = Field(ge=0)
    input_market_rows: int = Field(ge=0)
    input_propositions: int = Field(ge=0)
    invalid_market_rows: Literal[0]
    eligible_markets: int = Field(ge=0)
    eligible_propositions: int = Field(ge=0)
    selected_markets: int = Field(ge=0)
    selected_propositions: int = Field(ge=0)
    teams: int = Field(ge=0)
    stages: int = Field(ge=0)
    stage_keys: tuple[str, ...]
    first_hour_epoch: int
    last_hour_epoch: int
    normalized_semantic_fingerprint: Sha256
    truncated: Literal[False]


InputSelection = Annotated[
    CatalogInputSelection | WC2026InputSelection,
    Field(discriminator="strategy"),
]


class CatalogScope(_FrozenModel):
    source: Literal["input-parquet"] = "input-parquet"
    scope: Literal["catalog"] = "catalog"
    universe: Literal["all-markets"] = "all-markets"
    selection: Literal["all_eligible_markets", "volume_desc_then_market_id"]
    truncated: bool


class WC2026Scope(_FrozenModel):
    source: Literal["oddsfox-pipeline"] = "oddsfox-pipeline"
    scope: Literal["wc2026"] = "wc2026"
    universe: Literal["knockout_progression"] = "knockout_progression"
    selection: Literal["all_valid_pipeline_wc2026_markets"] = (
        "all_valid_pipeline_wc2026_markets"
    )
    truncated: Literal[False] = False


GraphScope = Annotated[CatalogScope | WC2026Scope, Field(discriminator="scope")]


class InputBinding(_FrozenModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )

    path: str
    sha256: Sha256
    schema_id: Literal[
        "polymarket-market-snapshot-v1",
        "polymarket-wc2026-graph-hourly-v1",
    ] = Field(alias="schema")
    profile: Literal[
        "polymarket-market-snapshot-v1",
        "polymarket-wc2026-graph-hourly-v1",
    ]
    normalized_semantic_fingerprint: Sha256 | None = None
    selection: InputSelection

    @property
    def schema(self) -> str:  # type: ignore[override]
        return self.schema_id

    @model_validator(mode="after")
    def validate_profile_and_selection(self) -> InputBinding:
        if self.profile != self.schema:
            raise ValueError("input profile must match the resolved schema")
        is_wc2026 = self.schema == WC2026_SOURCE_SCHEMA
        if is_wc2026 != isinstance(self.selection, WC2026InputSelection):
            raise ValueError("input selection does not match the resolved schema")
        expected_semantics = (
            self.selection.normalized_semantic_fingerprint
            if isinstance(self.selection, WC2026InputSelection)
            else None
        )
        if self.normalized_semantic_fingerprint != expected_semantics:
            raise ValueError("input semantic fingerprint does not match selection")
        return self


class DeadlineSummary(_FrozenModel):
    seconds: float = Field(gt=0)
    elapsed_seconds: float = Field(ge=0)
    met: bool
    cutoff_triggered: bool
    assessed_pairs: int = Field(ge=0)
    unassessed_pairs: int = Field(ge=0)


class ResponseLimits(_FrozenModel):
    nodes: int = Field(ge=1, le=5_000)
    edges: int = Field(ge=0, le=10_000)


class VersionBindings(_FrozenModel):
    input_adapter: str
    publication: str
    normalization: str
    extractor: str
    rules: str
    candidate_state: str
    execution_plan: str
    viewer_api: str
    viewer_artifacts: str
    visualization_layout: str
    aggregation: str
    solver: str
    constraints: str
    domain_taxonomy: str | None = None
    retrieval: str | None = None
    parse_fallback: str | None = None
    ann: dict[str, JsonValue] | None = None
    cache: int | None = None
    rule_registry_hash: Sha256 | None = None

    @model_validator(mode="after")
    def validate_current_contracts(self) -> VersionBindings:
        expected: dict[str, object] = {
            "input_adapter": INPUT_ADAPTER_VERSION,
            "publication": PUBLICATION_VERSION,
            "normalization": NORMALIZATION_VERSION,
            "extractor": EXTRACTOR_VERSION,
            "rules": RULE_VERSION,
            "candidate_state": CANDIDATE_STATE_VERSION,
            "execution_plan": EXECUTION_PLAN_VERSION,
            "viewer_api": VIEWER_API_VERSION,
            "viewer_artifacts": VIEWER_ARTIFACT_VERSION,
            "visualization_layout": VISUALIZATION_LAYOUT_VERSION,
            "aggregation": AGGREGATION_CONTRACT_VERSION,
            "solver": SOLVER_VERSION,
            "constraints": CONSTRAINT_VERSION,
        }
        mismatches = [
            name for name, value in expected.items() if getattr(self, name) != value
        ]
        optional_expected: dict[str, object] = {
            "domain_taxonomy": DOMAIN_TAXONOMY_VERSION,
            "retrieval": RETRIEVAL_VERSION,
            "parse_fallback": PARSE_FALLBACK_VERSION,
            "ann": ann_version_binding(),
            "cache": CACHE_ENTRY_VERSION,
            "rule_registry_hash": rule_registry_hash(),
        }
        mismatches.extend(
            name
            for name, value in optional_expected.items()
            if getattr(self, name) is not None and getattr(self, name) != value
        )
        if mismatches:
            raise ValueError(
                "stale manifest version bindings: " + ", ".join(mismatches)
            )
        return self


class _BuildManifestBase(_FrozenModel):
    schema_version: Literal["graph-build-manifest-v1"]
    command: Literal["discover"]
    version: str
    build_mode: Literal["fast", "full"]
    validation_status: Literal["DETERMINISTIC_VALIDATED", "EXPERIMENTAL_FULL"]
    deadline: DeadlineSummary
    input: InputBinding
    scope: GraphScope
    source_tree_fingerprint: Sha256
    discovery_semantics_fingerprint: Sha256
    graph_content_fingerprint: Sha256
    versions: VersionBindings
    artifacts: tuple[str, ...]
    artifact_hashes: dict[str, Sha256]
    state_hashes: dict[str, Sha256]
    published_file_hashes: dict[str, Sha256]
    stats: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_common_invariants(self) -> _BuildManifestBase:
        if self.schema_version != BUILD_MANIFEST_SCHEMA_VERSION:
            raise ValueError("build manifest schema is incompatible")
        artifact_names = set(self.artifacts)
        if len(artifact_names) != len(self.artifacts):
            raise ValueError("build manifest artifact names must be unique")
        if set(self.published_file_hashes) != artifact_names:
            raise ValueError("published hashes must exactly cover declared artifacts")
        if not set(self.artifact_hashes) <= artifact_names:
            raise ValueError("artifact hashes contain undeclared files")
        if not set(self.state_hashes) <= artifact_names:
            raise ValueError("state hashes contain undeclared files")
        if isinstance(self.input.selection, WC2026InputSelection):
            if not isinstance(self.scope, WC2026Scope):
                raise ValueError("WC2026 input requires the WC2026 scope")
        else:
            if not isinstance(self.scope, CatalogScope):
                raise ValueError("catalog input requires the catalog scope")
            if (
                self.scope.selection != self.input.selection.strategy
                or self.scope.truncated != self.input.selection.truncated
            ):
                raise ValueError("catalog scope does not match input selection")
        return self


class FastBuildManifest(_BuildManifestBase):
    build_mode: Literal["fast"]
    validation_status: Literal["DETERMINISTIC_VALIDATED"]


class FullBuildManifest(_BuildManifestBase):
    build_mode: Literal["full"]
    validation_status: Literal["EXPERIMENTAL_FULL"]
    models: dict[str, JsonValue]
    prompts: dict[str, JsonValue]
    inference: dict[str, JsonValue]
    limits: dict[str, JsonValue]
    incremental: dict[str, JsonValue]
    qualification: JsonValue
    compute: JsonValue
    solver: JsonValue
    rules: JsonValue
    cache: dict[str, JsonValue]
    usage: dict[str, JsonValue]
    reports: tuple[str, ...]
    stage_timings: dict[str, float]
    stage_metrics: JsonValue


BuildManifest = Annotated[
    FastBuildManifest | FullBuildManifest,
    Field(discriminator="build_mode"),
]
_BUILD_MANIFEST_ADAPTER: TypeAdapter[BuildManifest] = TypeAdapter(BuildManifest)


class ViewerInputBinding(_FrozenModel):
    sha256: Sha256
    normalized_semantic_fingerprint: Sha256 | None = None


class ViewerManifest(_FrozenModel):
    schema_version: Literal["viewer-artifacts-v4"]
    api_version: Literal["viewer-api-v4"]
    layout_version: str
    build_mode: Literal["fast", "full"]
    validation_status: Literal["DETERMINISTIC_VALIDATED", "EXPERIMENTAL_FULL"]
    input_profile: Literal[
        "polymarket-market-snapshot-v1",
        "polymarket-wc2026-graph-hourly-v1",
    ]
    input: ViewerInputBinding
    scope: GraphScope
    source_tree_fingerprint: Sha256
    discovery_semantics_fingerprint: Sha256
    source_watermark: str | None
    graph_content_fingerprint: Sha256
    response_limits: ResponseLimits
    evidence_tiers: tuple[
        Literal["source_contract", "deterministic_rule", "generative_consensus"],
        ...,
    ] = ()

    @model_validator(mode="after")
    def validate_current_contracts(self) -> ViewerManifest:
        if self.schema_version != VIEWER_ARTIFACT_VERSION:
            raise ValueError("viewer artifact schema is incompatible")
        if self.api_version != VIEWER_API_VERSION:
            raise ValueError("viewer API schema is incompatible")
        expected_status = (
            "DETERMINISTIC_VALIDATED"
            if self.build_mode == "fast"
            else "EXPERIMENTAL_FULL"
        )
        if self.validation_status != expected_status:
            raise ValueError("viewer status does not match build mode")
        is_wc2026 = self.input_profile == WC2026_SOURCE_SCHEMA
        if is_wc2026 != isinstance(self.scope, WC2026Scope):
            raise ValueError("viewer scope does not match input profile")
        return self


class CoverageSummary(_FrozenModel):
    schema_version: Literal["coverage-summary-v2"]
    all_market_selection: bool
    input_selection: InputSelection
    markets: int = Field(ge=0)
    propositions: int = Field(ge=0)
    events: int = Field(ge=0)
    components: int = Field(ge=0)
    parsed: int = Field(ge=0)
    parse_quarantined: int = Field(ge=0)
    candidates: int = Field(ge=0)
    classification_eligible: int = Field(ge=0)
    classification_assessed: int = Field(ge=0)
    classification_unclassified: int = Field(ge=0)
    classification_status: CoverageStatus
    classification_coverage: float | None = Field(default=None, ge=0, le=1)
    classification_gap: float | None = Field(default=None, ge=0, le=1)
    accepted_edges: int = Field(ge=0)
    rejected_edges: int = Field(ge=0)
    quarantined_pairs: int = Field(ge=0)


def load_build_manifest(path: Path) -> BuildManifest:
    """Load a current build manifest without accepting legacy shapes."""

    try:
        return _BUILD_MANIFEST_ADAPTER.validate_json(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ValueError(
            "Graph build manifest is incompatible; run a clean v0.13 WC2026 "
            "discovery"
        ) from exc


def load_viewer_manifest(path: Path) -> ViewerManifest:
    """Load a current viewer manifest without accepting legacy shapes."""

    try:
        return ViewerManifest.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ValueError(
            "Graph viewer manifest is incompatible; run a clean v0.13 WC2026 "
            "discovery"
        ) from exc


def validate_manifest_pair(
    build: BuildManifest,
    viewer: ViewerManifest,
) -> None:
    """Reject cross-file drift after each document validates independently."""

    mismatches: list[str] = []
    for name, left, right in (
        ("build mode", build.build_mode, viewer.build_mode),
        ("validation status", build.validation_status, viewer.validation_status),
        ("input profile", build.input.profile, viewer.input_profile),
        ("input hash", build.input.sha256, viewer.input.sha256),
        (
            "input semantic fingerprint",
            build.input.normalized_semantic_fingerprint,
            viewer.input.normalized_semantic_fingerprint,
        ),
        ("scope", build.scope, viewer.scope),
        (
            "discovery semantic fingerprint",
            build.discovery_semantics_fingerprint,
            viewer.discovery_semantics_fingerprint,
        ),
        (
            "source-tree audit fingerprint",
            build.source_tree_fingerprint,
            viewer.source_tree_fingerprint,
        ),
        (
            "graph content fingerprint",
            build.graph_content_fingerprint,
            viewer.graph_content_fingerprint,
        ),
    ):
        if left != right:
            mismatches.append(name)
    if mismatches:
        raise ValueError("Build/viewer manifest mismatch: " + ", ".join(mismatches))


def resolved_scope(selection: InputSelection) -> CatalogScope | WC2026Scope:
    """Construct the single public scope representation for an input selection."""

    if isinstance(selection, WC2026InputSelection):
        return WC2026Scope()
    return CatalogScope(
        selection=selection.strategy,
        truncated=selection.truncated,
    )


def current_version_bindings(
    *,
    domain_taxonomy: str | None = None,
    retrieval: str | None = None,
    parse_fallback: str | None = None,
    ann: Mapping[str, JsonValue] | None = None,
    cache: int | None = None,
    rule_registry_hash: Sha256 | None = None,
) -> VersionBindings:
    """Build common bindings while permitting only declared extension fields."""

    return VersionBindings(
        input_adapter=INPUT_ADAPTER_VERSION,
        publication=PUBLICATION_VERSION,
        normalization=NORMALIZATION_VERSION,
        extractor=EXTRACTOR_VERSION,
        rules=RULE_VERSION,
        candidate_state=CANDIDATE_STATE_VERSION,
        execution_plan=EXECUTION_PLAN_VERSION,
        viewer_api=VIEWER_API_VERSION,
        viewer_artifacts=VIEWER_ARTIFACT_VERSION,
        visualization_layout=VISUALIZATION_LAYOUT_VERSION,
        aggregation=AGGREGATION_CONTRACT_VERSION,
        solver=SOLVER_VERSION,
        constraints=CONSTRAINT_VERSION,
        domain_taxonomy=domain_taxonomy,
        retrieval=retrieval,
        parse_fallback=parse_fallback,
        ann=dict(ann) if ann is not None else None,
        cache=cache,
        rule_registry_hash=rule_registry_hash,
    )


__all__ = [
    "BuildManifest",
    "CatalogInputSelection",
    "CatalogScope",
    "CoverageSummary",
    "FastBuildManifest",
    "FullBuildManifest",
    "InputBinding",
    "InputSelection",
    "VersionBindings",
    "ViewerManifest",
    "WC2026InputSelection",
    "WC2026Scope",
    "current_version_bindings",
    "load_build_manifest",
    "load_viewer_manifest",
    "resolved_scope",
    "validate_manifest_pair",
]
