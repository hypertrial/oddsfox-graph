from pathlib import Path

import pytest

from oddsgraph.config import Settings
from oddsgraph.graphbuild import GraphBuildResult
from oddsgraph.infer import infer_event_fragments
from oddsgraph.reporting import (
    build_inference_report,
    load_inference_report,
    merge_per_event_status,
)
from oddsgraph.resolution import ResolutionState
from oddsgraph.schema import GraphFragment, InferenceReport

from tests.helpers import load_golden_markets


def test_merge_per_event_status_preserves_existing_report_fields(tmp_path: Path) -> None:
    report_path = tmp_path / "inference_report.json"
    initial = InferenceReport(
        model_path="models/test.gguf",
        events_processed=2,
        node_counts={"TEAM": 5},
        edge_counts={"PART_OF": 3},
    )
    report_path.write_text(initial.model_dump_json(indent=2), encoding="utf-8")

    merge_per_event_status(report_path, {"351746": "success"})

    loaded = load_inference_report(report_path)
    assert loaded.model_path == "models/test.gguf"
    assert loaded.node_counts == {"TEAM": 5}
    assert loaded.edge_counts == {"PART_OF": 3}
    assert loaded.per_event_status == {"351746": "success"}


def test_infer_after_build_preserves_report_metrics(tmp_path: Path) -> None:
    settings = Settings()
    settings.configure_build_dir(tmp_path / "build")
    settings.ensure_dirs()

    report = build_inference_report(
        ResolutionState(),
        GraphBuildResult(),
        model_path="models/test.gguf",
        per_event_status={"351746": "success"},
    )
    settings.inference_report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    fragment_path = settings.fragments_dir / "351746.json"
    fragment_path.write_text(
        GraphFragment().model_dump_json(indent=2),
        encoding="utf-8",
    )

    markets = [market for market in load_golden_markets() if market.event_id == "351746"]
    # Match events are covered by deterministic topology by default, so no LLM
    # resume path runs; status is recorded as deterministic.
    infer_event_fragments(settings, markets, llm=_NoOpLLM())

    loaded = load_inference_report(settings.inference_report_path)
    assert loaded.model_path == "models/test.gguf"
    assert loaded.node_counts == report.node_counts
    assert loaded.per_event_status.get("351746") == "deterministic"


class _NoOpLLM:
    def generate_fragment(
        self,
        prompt: str,
        event_id: str,
        max_tokens_override: int | None = None,
    ) -> GraphFragment:
        return GraphFragment()


def test_load_inference_report_warns_on_corrupt_json(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    path = tmp_path / "inference_report.json"
    path.write_text("{not-json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        report = load_inference_report(path)
    assert report.per_event_status == {}
    assert any("Ignoring corrupt inference report" in r.message for r in caplog.records)


def test_events_deterministic_counted_in_report() -> None:
    report = build_inference_report(
        ResolutionState(),
        GraphBuildResult(),
        per_event_status={
            "1": "success",
            "2": "deterministic",
            "3": "deterministic",
            "4": "skipped",
            "5": "failed",
            "6": "deterministic_verified",
            "7": "deterministic_corrected",
        },
    )
    assert report.events_processed == 1
    assert report.events_deterministic == 2
    assert report.events_deterministic_verified == 1
    assert report.events_deterministic_corrected == 1
    assert report.events_skipped == 1
    assert report.events_failed == 1
