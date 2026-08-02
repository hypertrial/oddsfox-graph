"""One authoritative contract for release performance budgets."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .versions import (
    AGGREGATION_CONTRACT_VERSION,
    CANONICAL_CATALOG_SHA256,
    EXTRACTOR_VERSION,
    FAST_READY_BENCHMARK_HARNESS_SHA256,
    FAST_READY_BENCHMARK_VERSION,
    INPUT_ADAPTER_VERSION,
    NORMALIZATION_VERSION,
    PERFORMANCE_BUDGET_VERSION,
    PUBLICATION_VERSION,
    RULE_VERSION,
    SOURCE_SCHEMA,
    VIEWER_API_VERSION,
    VIEWER_ARTIFACT_VERSION,
    discovery_semantics_fingerprint,
    source_tree_fingerprint,
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class HardwareBinding(_FrozenModel):
    system: str
    machine: str
    processor: str


class PerformanceVersionBindings(_FrozenModel):
    benchmark_contract: Literal["fast-ready-benchmark-v2"]
    benchmark_harness_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_adapter: str
    normalization: str
    extractor: str
    rules: str
    publication: str
    aggregation: str
    viewer_api: str
    viewer_artifacts: str
    discovery_semantics_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_tree_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class PerformanceGates(_FrozenModel):
    max_manifest_query_ready_seconds: float = Field(gt=0)
    require_logical_hash_equality: Literal[True]
    require_no_inference_resources: Literal[True]


class PerformanceDiagnostics(_FrozenModel):
    record_peak_rss: bool
    peak_rss_is_blocking: bool


class PerformanceBudget(_FrozenModel):
    schema_version: Literal["performance-budget-v4"]
    input_profile: Literal["polymarket-market-snapshot-v1"]
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    system: str
    machine: str
    processor_exact: Literal["Apple M4"]
    python_version: Literal["3.11"]
    repetitions: Literal[3]
    selection: Literal["complete-valid-catalog"]
    versions: PerformanceVersionBindings
    gates: PerformanceGates
    diagnostics: PerformanceDiagnostics


class BudgetApplicability(_FrozenModel):
    applicable: bool
    reasons: tuple[str, ...]


def current_hardware() -> HardwareBinding:
    """Return the stable hardware identity used by the release gate."""

    processor = platform.processor()
    if platform.system() == "Darwin":
        observed = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            check=False,
            text=True,
        ).stdout.strip()
        processor = observed or processor
        if "Apple" not in processor:
            profile = subprocess.run(
                ["system_profiler", "SPHardwareDataType"],
                capture_output=True,
                check=False,
                text=True,
            ).stdout
            processor = next(
                (
                    line.partition(":")[2].strip()
                    for line in profile.splitlines()
                    if line.strip().startswith("Chip:")
                ),
                processor,
            )
    return HardwareBinding(
        system=platform.system(),
        machine=platform.machine(),
        processor=processor,
    )


def current_python_version() -> str:
    """Return the major/minor interpreter identity bound by the benchmark."""

    return f"{sys.version_info.major}.{sys.version_info.minor}"


def current_performance_versions() -> PerformanceVersionBindings:
    """Bind a budget to the exact code and semantic contracts it measures."""

    return PerformanceVersionBindings(
        benchmark_contract=cast(
            Literal["fast-ready-benchmark-v2"],
            FAST_READY_BENCHMARK_VERSION,
        ),
        benchmark_harness_sha256=FAST_READY_BENCHMARK_HARNESS_SHA256,
        input_adapter=INPUT_ADAPTER_VERSION,
        normalization=NORMALIZATION_VERSION,
        extractor=EXTRACTOR_VERSION,
        rules=RULE_VERSION,
        publication=PUBLICATION_VERSION,
        aggregation=AGGREGATION_CONTRACT_VERSION,
        viewer_api=VIEWER_API_VERSION,
        viewer_artifacts=VIEWER_ARTIFACT_VERSION,
        discovery_semantics_fingerprint=discovery_semantics_fingerprint(),
        source_tree_fingerprint=source_tree_fingerprint(),
    )


def packaged_performance_budget_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "m4-v0.13-fast-performance-budget.json"
    )


def validate_performance_budget(value: object) -> PerformanceBudget:
    """Validate structure and reject budgets bound to any previous code."""

    try:
        budget = PerformanceBudget.model_validate(value)
    except ValidationError as exc:
        raise ValueError(f"Performance budget is invalid: {exc}") from exc
    if budget.schema_version != PERFORMANCE_BUDGET_VERSION:
        raise ValueError("Performance budget schema is stale")
    if budget.input_profile != SOURCE_SCHEMA:
        raise ValueError("Performance budget input profile is stale")
    if budget.input_sha256 != CANONICAL_CATALOG_SHA256:
        raise ValueError("Performance budget input hash is stale")
    expected_versions = current_performance_versions()
    if budget.versions != expected_versions:
        changed = [
            name
            for name in PerformanceVersionBindings.model_fields
            if getattr(budget.versions, name) != getattr(expected_versions, name)
        ]
        raise ValueError(
            "Performance budget version binding mismatch: " + ", ".join(changed)
        )
    return budget


def load_performance_budget(path: Path | None = None) -> PerformanceBudget:
    """Load the packaged budget or an explicit operator-supplied override."""

    resolved = (path or packaged_performance_budget_path()).resolve()
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Performance budget cannot be read: {resolved}: {exc}"
        ) from exc
    return validate_performance_budget(value)


def performance_budget_applicability(
    budget: PerformanceBudget,
    *,
    input_profile: str | None,
    input_sha256: str | None,
    repetitions: int,
    hardware: HardwareBinding | None = None,
    python_version: str | None = None,
) -> BudgetApplicability:
    """Explain whether a valid budget applies to this particular invocation."""

    observed = hardware or current_hardware()
    reasons: list[str] = []
    if input_profile != budget.input_profile:
        reasons.append("input_profile")
    if input_sha256 != budget.input_sha256:
        reasons.append("input_sha256")
    if repetitions != budget.repetitions:
        reasons.append("repetitions")
    if observed.system != budget.system:
        reasons.append("system")
    if observed.machine != budget.machine:
        reasons.append("machine")
    if observed.processor != budget.processor_exact:
        reasons.append("processor")
    if (python_version or current_python_version()) != budget.python_version:
        reasons.append("python_version")
    return BudgetApplicability(applicable=not reasons, reasons=tuple(reasons))


__all__ = [
    "BudgetApplicability",
    "HardwareBinding",
    "PerformanceBudget",
    "PerformanceDiagnostics",
    "PerformanceGates",
    "PerformanceVersionBindings",
    "current_hardware",
    "current_python_version",
    "current_performance_versions",
    "load_performance_budget",
    "packaged_performance_budget_path",
    "performance_budget_applicability",
    "validate_performance_budget",
]
