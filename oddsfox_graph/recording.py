"""Deterministic browser rendering and H.264 recording publication."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode, urlparse

from ._discovery.provenance import atomic_write_json, sha256_file
from .explorer import create_explorer_app
from .graph import Graph


ProgressFormat = Literal["plain", "json", "quiet"]


@dataclass(frozen=True)
class RecordingOptions:
    graph_dir: Path
    destination: Path
    highlights: int = 6
    min_confidence: float = 0.95
    width: int = 1_920
    height: int = 1_080
    fps: int = 30
    progress_format: ProgressFormat = "plain"


@dataclass(frozen=True)
class RecordingRuntime:
    playwright: Callable[[], Any]
    chromium_path: Path
    ffmpeg_path: Path
    ffmpeg_version: str


class RecordingProgress:
    def __init__(self, output_format: ProgressFormat) -> None:
        self.output_format = output_format

    def emit(self, phase: str, **values: object) -> None:
        if self.output_format == "quiet":
            return
        payload = {"phase": phase, **values}
        if self.output_format == "json":
            print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)
            return
        suffix = " ".join(f"{key}={value}" for key, value in values.items())
        print(
            f"[record] {phase}{' ' if suffix else ''}{suffix}",
            file=sys.stderr,
            flush=True,
        )


def record_graph(
    graph_dir: Path,
    destination: Path,
    *,
    highlights: int = 6,
    min_confidence: float = 0.95,
    width: int = 1_920,
    height: int = 1_080,
    fps: int = 30,
    progress_format: ProgressFormat = "plain",
) -> dict[str, object]:
    """Render every story frame, encode it, validate it, and publish atomically."""

    options = validate_recording_options(
        RecordingOptions(
            graph_dir=graph_dir,
            destination=destination,
            highlights=highlights,
            min_confidence=min_confidence,
            width=width,
            height=height,
            fps=fps,
            progress_format=progress_format,
        )
    )
    progress = RecordingProgress(options.progress_format)
    progress.emit("preflight")
    graph = Graph.open(options.graph_dir)
    plan = graph.recording_plan(
        limit=options.highlights,
        min_confidence=options.min_confidence,
    )
    runtime = recording_preflight()
    options.destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{options.destination.name}.recording-",
            dir=options.destination.parent,
        )
    )
    published = False
    try:
        result = _render_recording(
            options, runtime, plan.model_dump(mode="json"), staging, progress
        )
        if os.path.lexists(options.destination):
            raise ValueError(
                f"Recording destination appeared during rendering: {options.destination}"
            )
        os.replace(staging, options.destination)
        published = True
        progress.emit("published", destination=str(options.destination))
        return {
            **result,
            "destination": str(options.destination),
            "recording": str(options.destination / "recording.mp4"),
            "story": str(options.destination / "story.json"),
            "manifest": str(options.destination / "recording_manifest.json"),
        }
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)


def validate_recording_options(options: RecordingOptions) -> RecordingOptions:
    source = options.graph_dir.resolve()
    if os.path.lexists(options.destination):
        raise ValueError(
            f"Recording destination already exists: {options.destination}"
        )
    destination = options.destination.resolve()
    if not 1 <= options.highlights <= 12:
        raise ValueError("highlights must be between 1 and 12")
    if not 0.0 <= options.min_confidence <= 1.0:
        raise ValueError("min_confidence must be between 0 and 1")
    _validate_even_dimension("width", options.width, 640, 3_840)
    _validate_even_dimension("height", options.height, 360, 2_160)
    if options.fps not in {24, 30, 60}:
        raise ValueError("fps must be one of 24, 30, or 60")
    if options.progress_format not in {"plain", "json", "quiet"}:
        raise ValueError("progress_format must be plain, json, or quiet")
    if os.path.lexists(destination):
        raise ValueError(f"Recording destination already exists: {destination}")
    if (
        source == destination
        or source in destination.parents
        or destination in source.parents
    ):
        raise ValueError(
            "Recording destination must not overlap the source graph directory"
        )
    return RecordingOptions(
        graph_dir=source,
        destination=destination,
        highlights=options.highlights,
        min_confidence=options.min_confidence,
        width=options.width,
        height=options.height,
        fps=options.fps,
        progress_format=options.progress_format,
    )


def recording_preflight() -> RecordingRuntime:
    try:
        module = importlib.import_module("playwright.sync_api")
        playwright = module.sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            'Recording support is not installed. Run: pip install "oddsfox-graph[recording]"'
        ) from exc
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError(
            "FFmpeg is required for recording. Install FFmpeg and ensure `ffmpeg` is on PATH."
        )
    try:
        version_result = subprocess.run(
            [ffmpeg, "-version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"Unable to run FFmpeg at {ffmpeg}: {exc}") from exc
    with playwright() as browser_runtime:
        chromium = Path(browser_runtime.chromium.executable_path)
        if not chromium.is_file():
            raise RuntimeError(
                "Playwright Chromium is missing. Run: python -m playwright install chromium"
            )
    return RecordingRuntime(
        playwright=playwright,
        chromium_path=chromium,
        ffmpeg_path=Path(ffmpeg),
        ffmpeg_version=version_result.stdout.splitlines()[0],
    )


def ffmpeg_command(
    executable: Path,
    destination: Path,
    *,
    fps: int,
) -> list[str]:
    return [
        str(executable),
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-framerate",
        str(fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-metadata",
        "encoder=",
        str(destination),
    ]


def _render_recording(
    options: RecordingOptions,
    runtime: RecordingRuntime,
    expected_plan: dict[str, object],
    staging: Path,
    progress: RecordingProgress,
) -> dict[str, object]:
    timings: dict[str, float] = {}
    started = time.monotonic()
    mp4_path = staging / "recording.mp4"
    story_path = staging / "story.json"
    frame_digest = hashlib.sha256()
    ffmpeg_process: subprocess.Popen[bytes] | None = None
    chromium_version = "unknown"
    progress.emit("server_start")
    with _local_explorer_server(options.graph_dir) as port:
        timings["server_start_seconds"] = time.monotonic() - started
        browser_started = time.monotonic()
        with runtime.playwright() as browser_runtime:
            browser = browser_runtime.chromium.launch(
                executable_path=str(runtime.chromium_path),
                headless=True,
                args=[
                    "--force-color-profile=srgb",
                    "--disable-background-networking",
                    "--disable-component-update",
                ],
            )
            chromium_version = str(browser.version)
            try:
                context = browser.new_context(
                    viewport={"width": options.width, "height": options.height},
                    device_scale_factor=1,
                    locale="en-US",
                    timezone_id="UTC",
                    color_scheme="dark",
                    reduced_motion="reduce",
                    bypass_csp=True,
                )
                context.route("**/*", _loopback_route)
                page = context.new_page()
                query = urlencode(
                    {
                        "presentation": 1,
                        "highlights": options.highlights,
                        "min_confidence": options.min_confidence,
                        "width": options.width,
                        "height": options.height,
                        "fps": options.fps,
                    }
                )
                page.goto(
                    f"http://127.0.0.1:{port}/?{query}",
                    wait_until="networkidle",
                )
                page.wait_for_function(
                    "window.__ODDSFOX_RECORDING__?.ready === true",
                    timeout=120_000,
                )
                story = page.evaluate("window.__ODDSFOX_RECORDING__.getStory()")
                frame_count = int(
                    page.evaluate("window.__ODDSFOX_RECORDING__.getFrameCount()")
                )
                _validate_story(story, expected_plan, options, frame_count)
                atomic_write_json(story_path, story)
                timings["browser_start_seconds"] = time.monotonic() - browser_started
                progress.emit("encode_start", frames=frame_count)
                encode_started = time.monotonic()
                ffmpeg_process = subprocess.Popen(
                    ffmpeg_command(
                        runtime.ffmpeg_path,
                        mp4_path,
                        fps=options.fps,
                    ),
                    stdin=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                if ffmpeg_process.stdin is None:
                    raise RuntimeError("FFmpeg stdin is unavailable")
                for frame in range(frame_count):
                    page.evaluate(
                        "frame => window.__ODDSFOX_RECORDING__.seek(frame)",
                        frame,
                    )
                    png = page.screenshot(
                        type="png",
                        full_page=True,
                        animations="disabled",
                    )
                    _validate_png_dimensions(png, options.width, options.height)
                    frame_digest.update(png)
                    try:
                        ffmpeg_process.stdin.write(png)
                    except BrokenPipeError as exc:
                        raise RuntimeError(
                            "FFmpeg stopped while receiving rendered frames: "
                            + _read_process_error(ffmpeg_process)
                        ) from exc
                    if (
                        frame == 0
                        or (frame + 1) % options.fps == 0
                        or frame + 1 == frame_count
                    ):
                        progress.emit(
                            "render_frames",
                            frame=frame + 1,
                            frames=frame_count,
                        )
                ffmpeg_process.stdin.close()
                return_code = ffmpeg_process.wait()
                if return_code != 0:
                    raise RuntimeError(
                        "FFmpeg encoding failed: " + _read_process_error(ffmpeg_process)
                    )
                timings["render_encode_seconds"] = time.monotonic() - encode_started
            finally:
                if ffmpeg_process is not None and ffmpeg_process.poll() is None:
                    ffmpeg_process.terminate()
                    try:
                        ffmpeg_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        ffmpeg_process.kill()
                        ffmpeg_process.wait()
                browser.close()

    progress.emit("validate")
    validation_started = time.monotonic()
    _validate_mp4(runtime.ffmpeg_path, mp4_path)
    timings["validation_seconds"] = time.monotonic() - validation_started
    timings["total_seconds"] = time.monotonic() - started
    story_value = json.loads(story_path.read_text(encoding="utf-8"))
    highlights = story_value["highlights"]
    manifest = {
        "schema_version": "oddsfox-recording-manifest-v1",
        "graph_fingerprint": story_value["graph_fingerprint"],
        "graph_sha256": story_value["graph_fingerprint"],
        "story_sha256": sha256_file(story_path),
        "frame_stream_sha256": frame_digest.hexdigest(),
        "mp4_sha256": sha256_file(mp4_path),
        "selected_proposal_ids": [item["proposal_id"] for item in highlights],
        "width": options.width,
        "height": options.height,
        "fps": options.fps,
        "frames": story_value["timeline"]["frame_count"],
        "duration_seconds": story_value["timeline"]["duration_seconds"],
        "ranking_version": story_value["ranking_version"],
        "layout_version": story_value["layout_version"],
        "layout_fingerprint": story_value["layout_fingerprint"],
        "client_version": story_value["client_version"],
        "client_fingerprint": story_value["client_fingerprint"],
        "chromium_version": chromium_version,
        "ffmpeg_version": runtime.ffmpeg_version,
        "render_timings_seconds": timings,
        "output_bytes": mp4_path.stat().st_size,
    }
    atomic_write_json(staging / "recording_manifest.json", manifest)
    return {
        "status": "recorded",
        "frames": manifest["frames"],
        "duration_seconds": manifest["duration_seconds"],
        "selected_proposal_ids": manifest["selected_proposal_ids"],
        "mp4_sha256": manifest["mp4_sha256"],
        "output_bytes": manifest["output_bytes"],
    }


@contextmanager
def _local_explorer_server(graph_dir: Path) -> Iterator[int]:
    import uvicorn

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            create_explorer_app(graph_dir),
            host="127.0.0.1",
            port=port,
            access_log=False,
            log_level="error",
        )
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        name="oddsfox-recording-server",
    )
    thread.start()
    deadline = time.monotonic() + 30
    try:
        while not server.started and thread.is_alive():
            if time.monotonic() >= deadline:
                raise RuntimeError("Timed out starting the loopback explorer server")
            time.sleep(0.01)
        if not server.started:
            raise RuntimeError("The loopback explorer server failed to start")
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)
        listener.close()


def _loopback_route(route: Any) -> None:
    parsed = urlparse(route.request.url)
    if parsed.scheme in {"data", "blob"} or parsed.hostname in {
        "127.0.0.1",
        "::1",
        "localhost",
    }:
        route.continue_()
    else:
        route.abort("blockedbyclient")


def _validate_story(
    story: object,
    expected_plan: dict[str, object],
    options: RecordingOptions,
    frame_count: int,
) -> None:
    if not isinstance(story, dict):
        raise RuntimeError("Presentation controller returned a non-object story")
    if story.get("schema_version") != "oddsfox-recording-story-v1":
        raise RuntimeError(
            "Presentation controller returned an unsupported story schema"
        )
    if story.get("graph_fingerprint") != expected_plan["graph_fingerprint"]:
        raise RuntimeError(
            "Presentation story graph fingerprint does not match the source"
        )
    if story.get("source_fingerprint") != expected_plan["graph_fingerprint"]:
        raise RuntimeError(
            "Presentation story source fingerprint does not match the source"
        )
    if story.get("ranking_version") != expected_plan.get("ranking_version"):
        raise RuntimeError(
            "Presentation story ranking version does not match the recording plan"
        )
    if story.get("mode") != expected_plan.get("mode") or story.get(
        "validation_status"
    ) != expected_plan.get("validation_status"):
        raise RuntimeError(
            "Presentation story graph status does not match the recording plan"
        )
    expected_highlights = expected_plan.get("highlights")
    actual_highlights = story.get("highlights")
    if not isinstance(expected_highlights, list) or not isinstance(
        actual_highlights, list
    ):
        raise RuntimeError("Presentation story highlights are invalid")
    if actual_highlights != expected_highlights:
        raise RuntimeError(
            "Presentation story proposals or score breakdowns do not match "
            "the recording plan"
        )
    if story.get("context_pruning") != expected_plan.get("context_pruning"):
        raise RuntimeError(
            "Presentation story context pruning does not match the recording plan"
        )
    viewport = story.get("viewport")
    if viewport != {
        "width": options.width,
        "height": options.height,
        "fps": options.fps,
    }:
        raise RuntimeError(
            "Presentation story viewport does not match recording options"
        )
    timeline = story.get("timeline")
    if not isinstance(timeline, dict) or timeline.get("frame_count") != frame_count:
        raise RuntimeError(
            "Presentation controller frame count does not match its story"
        )
    expected_frames = options.fps * (3 + 7 * len(expected_highlights) + 3)
    if frame_count != expected_frames:
        raise RuntimeError(
            f"Presentation story has {frame_count} frames; expected {expected_frames}"
        )


def _validate_png_dimensions(png: bytes, width: int, height: int) -> None:
    if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("Browser returned an invalid PNG frame")
    actual_width, actual_height = struct.unpack(">II", png[16:24])
    if (actual_width, actual_height) != (width, height):
        raise RuntimeError(
            "Browser frame dimensions do not match the requested viewport: "
            f"got {actual_width}x{actual_height}, expected {width}x{height}"
        )


def _validate_mp4(ffmpeg: Path, mp4_path: Path) -> None:
    if not mp4_path.is_file() or mp4_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg did not produce a non-empty MP4")
    result = subprocess.run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-i",
            str(mp4_path),
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Recorded MP4 failed FFmpeg validation: "
            + result.stderr.decode("utf-8", errors="replace").strip()
        )


def _read_process_error(process: subprocess.Popen[bytes]) -> str:
    if process.stderr is None:
        return "no FFmpeg diagnostics available"
    return process.stderr.read().decode("utf-8", errors="replace").strip()


def _validate_even_dimension(
    name: str,
    value: int,
    minimum: int,
    maximum: int,
) -> None:
    if value < minimum or value > maximum or value % 2:
        raise ValueError(
            f"{name} must be an even integer between {minimum} and {maximum}"
        )
