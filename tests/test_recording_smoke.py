from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from oddsfox_graph._discovery.provenance import atomic_write_json, sha256_file
from oddsfox_graph.recording import (
    RecordingOptions,
    _local_explorer_server,
    _loopback_route,
    _validate_mp4,
    _validate_png_dimensions,
    _validate_story,
    ffmpeg_command,
    recording_preflight,
)
from test_graph_and_release import _write_graph
from oddsfox_graph.graph import Graph


@pytest.mark.skipif(
    os.environ.get("ODDSFOX_RECORDING_SMOKE") != "1",
    reason="requires the recording extra, Chromium, and FFmpeg",
)
def test_real_controller_to_ffmpeg_three_frame_smoke(tmp_path: Path) -> None:
    runtime = recording_preflight()
    graph_dir = _write_graph(tmp_path / "graph")
    output = tmp_path / "smoke.mp4"
    digest = hashlib.sha256()
    frame_count = 0
    with _local_explorer_server(graph_dir) as port:
        with runtime.playwright() as browser_runtime:
            browser = browser_runtime.chromium.launch(
                executable_path=str(runtime.chromium_path),
                headless=True,
            )
            try:
                context = browser.new_context(
                    viewport={"width": 640, "height": 360},
                    device_scale_factor=1,
                    locale="en-US",
                    timezone_id="UTC",
                    color_scheme="dark",
                    reduced_motion="reduce",
                    bypass_csp=True,
                )
                context.route("**/*", _loopback_route)
                page = context.new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/?presentation=1&highlights=1&min_confidence=0.95&width=640&height=360&fps=24",
                    wait_until="networkidle",
                )
                page.wait_for_function(
                    "window.__ODDSFOX_RECORDING__?.ready === true",
                    timeout=120_000,
                )
                public_frames = int(
                    page.evaluate("window.__ODDSFOX_RECORDING__.getFrameCount()")
                )
                story = page.evaluate("window.__ODDSFOX_RECORDING__.getStory()")
                expected_plan = Graph.open(graph_dir).recording_plan(
                    limit=1,
                    min_confidence=0.95,
                )
                _validate_story(
                    story,
                    expected_plan.model_dump(mode="json"),
                    RecordingOptions(
                        graph_dir=graph_dir,
                        destination=tmp_path / "bundle",
                        highlights=1,
                        width=640,
                        height=360,
                        fps=24,
                    ),
                    public_frames,
                )
                process = subprocess.Popen(
                    ffmpeg_command(runtime.ffmpeg_path, output, fps=24),
                    stdin=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                assert process.stdin is not None
                for frame in (0, 144, public_frames - 1):
                    page.evaluate(
                        "value => window.__ODDSFOX_RECORDING__.seek(value)",
                        frame,
                    )
                    png = page.screenshot(
                        type="png",
                        full_page=True,
                        animations="disabled",
                    )
                    _validate_png_dimensions(png, 640, 360)
                    digest.update(png)
                    process.stdin.write(png)
                    frame_count += 1
                process.stdin.close()
                assert process.wait() == 0, (
                    process.stderr.read().decode("utf-8", errors="replace")
                    if process.stderr
                    else ""
                )
            finally:
                browser.close()
    _validate_mp4(runtime.ffmpeg_path, output)
    manifest = {
        "schema_version": "oddsfox-recording-smoke-v1",
        "frames": frame_count,
        "frame_stream_sha256": digest.hexdigest(),
        "mp4_sha256": sha256_file(output),
        "output_bytes": output.stat().st_size,
    }
    manifest_path = tmp_path / "smoke_manifest.json"
    atomic_write_json(manifest_path, manifest)
    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert loaded["frames"] == 3
    assert loaded["output_bytes"] > 0
