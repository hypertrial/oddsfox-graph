from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
from types import ModuleType

import pytest

from oddsfox_graph._discovery.performance_contracts import (
    HardwareBinding,
    current_performance_versions,
    current_python_version,
    load_performance_budget,
    packaged_performance_budget_path,
    performance_budget_applicability,
    validate_performance_budget,
)
from oddsfox_graph._discovery.provenance import sha256_file
from oddsfox_graph._discovery.versions import (
    FAST_READY_BENCHMARK_HARNESS_SHA256,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_HARNESS = REPO_ROOT / "scripts" / "benchmark_discovery.py"


def _benchmark_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "benchmark_discovery_test_module",
        BENCHMARK_HARNESS,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _current_budget_payload() -> dict[str, object]:
    payload = json.loads(packaged_performance_budget_path().read_text(encoding="utf-8"))
    payload["versions"] = current_performance_versions().model_dump(mode="json")
    return payload


def test_packaged_performance_budget_is_current_and_single_source() -> None:
    budget = load_performance_budget()

    assert budget.schema_version == "performance-budget-v4"
    assert budget.versions.benchmark_contract == "fast-ready-benchmark-v2"
    assert budget.processor_exact == "Apple M4"
    assert budget.python_version == "3.11"
    assert current_python_version() == "3.11"
    assert budget.versions.benchmark_harness_sha256 == (
        FAST_READY_BENCHMARK_HARNESS_SHA256
    )
    assert sha256_file(BENCHMARK_HARNESS) == FAST_READY_BENCHMARK_HARNESS_SHA256
    assert packaged_performance_budget_path().name == (
        "m4-v0.13-fast-performance-budget.json"
    )


def test_performance_budget_rejects_stale_version_binding() -> None:
    payload = _current_budget_payload()
    versions = payload["versions"]
    assert isinstance(versions, dict)
    versions["rules"] = "discovery-rules-stale"

    with pytest.raises(ValueError, match="rules"):
        validate_performance_budget(payload)


def test_performance_budget_rejects_a_different_benchmark_harness() -> None:
    payload = _current_budget_payload()
    versions = payload["versions"]
    assert isinstance(versions, dict)
    versions["benchmark_harness_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="benchmark_harness_sha256"):
        validate_performance_budget(payload)


def test_budget_applicability_distinguishes_product_and_release_inputs() -> None:
    budget = validate_performance_budget(_current_budget_payload())
    hardware = HardwareBinding(
        system="Darwin",
        machine="arm64",
        processor="Apple M4",
    )

    applicable = performance_budget_applicability(
        budget,
        input_profile=budget.input_profile,
        input_sha256=budget.input_sha256,
        repetitions=3,
        hardware=hardware,
        python_version="3.11",
    )
    wc2026 = performance_budget_applicability(
        budget,
        input_profile="polymarket-wc2026-graph-hourly-v1",
        input_sha256="3" * 64,
        repetitions=3,
        hardware=hardware,
        python_version="3.11",
    )
    processor_variants = tuple(
        performance_budget_applicability(
            budget,
            input_profile=budget.input_profile,
            input_sha256=budget.input_sha256,
            repetitions=3,
            hardware=hardware.model_copy(update={"processor": processor}),
            python_version="3.11",
        )
        for processor in ("Apple M4 Pro", "Apple M4 Max")
    )
    wrong_python = performance_budget_applicability(
        budget,
        input_profile=budget.input_profile,
        input_sha256=budget.input_sha256,
        repetitions=3,
        hardware=hardware,
        python_version="3.12",
    )

    assert applicable.applicable is True
    assert applicable.reasons == ()
    assert wc2026.applicable is False
    assert wc2026.reasons == ("input_profile", "input_sha256")
    assert all(item.reasons == ("processor",) for item in processor_variants)
    assert wrong_python.reasons == ("python_version",)


def test_readiness_probe_uses_a_fresh_isolated_python_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark = _benchmark_module()
    observed: dict[str, object] = {}

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"package_version":"0.13.0"}',
            stderr="",
        )

    monkeypatch.setattr(benchmark.subprocess, "run", run)
    monkeypatch.setattr(benchmark.time, "monotonic", lambda: 105.0)

    seconds, metadata = benchmark._open_ready_graph(tmp_path, started=100.0)

    command = observed["command"]
    assert isinstance(command, list)
    assert command[1:3] == ["-I", "-c"]
    assert "Graph.open" in command[3]
    assert command[-1] == str(tmp_path)
    assert observed["kwargs"] == {
        "cwd": REPO_ROOT,
        "capture_output": True,
        "text": True,
        "check": False,
    }
    assert seconds == 5.0
    assert metadata == {"package_version": "0.13.0"}
