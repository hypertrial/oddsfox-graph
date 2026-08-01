from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from oddsfox_graph import recording
from oddsfox_graph.cli import main
from oddsfox_graph.recording import (
    RecordingOptions,
    RecordingRuntime,
    _validate_story,
    ffmpeg_command,
    record_graph,
    recording_preflight,
    validate_recording_options,
)


def options(tmp_path: Path, **changes: Any) -> RecordingOptions:
    values: dict[str, Any] = {
        "graph_dir": tmp_path / "graph",
        "destination": tmp_path / "bundle",
    }
    values.update(changes)
    return RecordingOptions(**values)


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"highlights": 0}, "highlights"),
        ({"highlights": 13}, "highlights"),
        ({"min_confidence": -0.1}, "min_confidence"),
        ({"width": 641}, "width"),
        ({"width": 4_000}, "width"),
        ({"height": 361}, "height"),
        ({"fps": 25}, "fps"),
    ),
)
def test_recording_options_validate_public_bounds(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_recording_options(options(tmp_path, **changes))


def test_recording_destination_must_be_new_and_disjoint(tmp_path: Path) -> None:
    graph = tmp_path / "graph"
    graph.mkdir()
    with pytest.raises(ValueError, match="overlap"):
        validate_recording_options(options(tmp_path, destination=graph / "video"))
    destination = tmp_path / "bundle"
    destination.mkdir()
    with pytest.raises(ValueError, match="already exists"):
        validate_recording_options(options(tmp_path, destination=destination))

    dangling = tmp_path / "dangling-bundle"
    dangling.symlink_to(tmp_path / "missing-target")
    with pytest.raises(ValueError, match="already exists"):
        validate_recording_options(options(tmp_path, destination=dangling))


def test_ffmpeg_command_is_shell_free_streaming_h264(tmp_path: Path) -> None:
    command = ffmpeg_command(
        Path("/usr/bin/ffmpeg"),
        tmp_path / "recording.mp4",
        fps=30,
    )
    assert command[0] == "/usr/bin/ffmpeg"
    assert "image2pipe" in command
    assert "libx264" in command
    assert "yuv420p" in command
    assert "+faststart" in command
    assert "-an" in command
    assert command[-1].endswith("recording.mp4")


def test_preflight_explains_missing_optional_playwright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = importlib.import_module

    def missing(name: str, package: str | None = None) -> Any:
        if name == "playwright.sync_api":
            raise ImportError("missing")
        return original(name, package)

    monkeypatch.setattr(recording.importlib, "import_module", missing)
    with pytest.raises(RuntimeError, match=r"oddsfox-graph\[recording\]"):
        recording_preflight()


def test_story_validation_rejects_browser_highlights_that_differ_from_plan(
    tmp_path: Path,
) -> None:
    expected_plan = {
        "schema_version": "oddsfox-recording-plan-v2",
        "graph_fingerprint": "fixture",
        "ranking_version": "human-wc2026-story-edge-v2",
        "mode": "full",
        "validation_status": "VALID",
        "highlights": [{"proposal_id": "expected"}],
        "context_pruning": {},
    }
    story: dict[str, object] = {
        "schema_version": "oddsfox-recording-story-v2",
        "graph_fingerprint": "fixture",
        "source_fingerprint": "fixture",
        "ranking_version": "human-wc2026-story-edge-v2",
        "mode": "full",
        "validation_status": "VALID",
        "highlights": [{"proposal_id": "unexpected"}],
        "context_pruning": {},
        "viewport": {"width": 1920, "height": 1080, "fps": 30},
        "timeline": {"frame_count": 390},
    }
    with pytest.raises(RuntimeError, match="proposals or score breakdowns"):
        _validate_story(
            story,
            expected_plan,
            options(tmp_path),
            frame_count=390,
        )


def test_recording_publishes_only_after_complete_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    destination = tmp_path / "bundle"

    class Plan:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {"graph_fingerprint": "fixture", "highlights": []}

    class FakeGraph:
        def recording_plan(self, *, limit: int, min_confidence: float) -> Plan:
            assert limit == 6
            assert min_confidence == 0.95
            return Plan()

    monkeypatch.setattr(recording.Graph, "open", lambda _: FakeGraph())
    monkeypatch.setattr(
        recording,
        "recording_preflight",
        lambda: RecordingRuntime(
            playwright=lambda: None,
            chromium_path=Path("chromium"),
            ffmpeg_path=Path("ffmpeg"),
            ffmpeg_version="ffmpeg test",
        ),
    )

    def render(
        _options: RecordingOptions,
        _runtime: RecordingRuntime,
        _plan: dict[str, object],
        staging: Path,
        _progress: object,
    ) -> dict[str, object]:
        (staging / "recording.mp4").write_bytes(b"mp4")
        (staging / "story.json").write_text("{}", encoding="utf-8")
        (staging / "recording_manifest.json").write_text("{}", encoding="utf-8")
        return {"status": "recorded"}

    monkeypatch.setattr(recording, "_render_recording", render)
    result = record_graph(graph_dir, destination, progress_format="quiet")
    assert result["status"] == "recorded"
    assert (destination / "recording.mp4").read_bytes() == b"mp4"
    assert set(path.name for path in destination.iterdir()) == {
        "recording.mp4",
        "story.json",
        "recording_manifest.json",
    }


def test_recording_failure_removes_staging_and_leaves_destination_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    destination = tmp_path / "bundle"

    class Plan:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            return {"graph_fingerprint": "fixture", "highlights": []}

    class FakeGraph:
        def recording_plan(self, *, limit: int, min_confidence: float) -> Plan:
            return Plan()

    monkeypatch.setattr(recording.Graph, "open", lambda _: FakeGraph())
    monkeypatch.setattr(
        recording,
        "recording_preflight",
        lambda: RecordingRuntime(
            playwright=lambda: None,
            chromium_path=Path("chromium"),
            ffmpeg_path=Path("ffmpeg"),
            ffmpeg_version="ffmpeg test",
        ),
    )
    monkeypatch.setattr(
        recording,
        "_render_recording",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("render failed")),
    )
    with pytest.raises(RuntimeError, match="render failed"):
        record_graph(graph_dir, destination, progress_format="quiet")
    assert not destination.exists()
    assert not list(tmp_path.glob(".bundle.recording-*"))


def test_record_cli_rejects_invalid_dimensions_before_opening_graph(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = main(
        [
            "record",
            "--out",
            str(tmp_path / "missing"),
            "--destination",
            str(tmp_path / "bundle"),
            "--width",
            "641",
        ]
    )
    assert result == 1
    assert "width must be an even integer" in capsys.readouterr().err
