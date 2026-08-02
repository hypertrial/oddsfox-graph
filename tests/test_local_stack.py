from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "local_stack.sh"


def _environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    capture = tmp_path / "arguments.txt"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$ODDSFOX_CAPTURE\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    environment = {
        **os.environ,
        "ODDSFOX_RUNTIME_ROOT": str(tmp_path / "runtime"),
        "ODDSFOX_ALLOW_NON_SSD_RUNTIME": "1",
        "ODDSFOX_PYTHON": str(fake_python),
        "ODDSFOX_CAPTURE": str(capture),
    }
    environment.pop("ODDSFOX_WC2026_INPUT", None)
    environment.pop("ODDSFOX_GENERIC_CATALOG", None)
    return environment, capture


def test_fast_wrapper_passes_explicit_wc2026_profile(tmp_path: Path) -> None:
    environment, capture = _environment(tmp_path)
    source = tmp_path / "wc export.parquet"
    source.touch()

    completed = subprocess.run(
        ["bash", str(SCRIPT), "fast", str(source)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    arguments = capture.read_text(encoding="utf-8").splitlines()
    assert arguments[:3] == ["-m", "oddsfox_graph.cli", "discover"]
    assert arguments[arguments.index("--input") + 1] == str(source)
    assert arguments[arguments.index("--input-profile") + 1] == (
        "polymarket-wc2026-graph-hourly-v1"
    )
    assert "--max-propositions" not in arguments


def test_fast_wrapper_fails_actionably_without_wc2026_input(tmp_path: Path) -> None:
    environment, _ = _environment(tmp_path)

    completed = subprocess.run(
        ["bash", str(SCRIPT), "fast"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "WC2026 input is required" in completed.stderr


def test_generic_smoke_is_explicitly_separate_and_bounded(tmp_path: Path) -> None:
    environment, capture = _environment(tmp_path)
    source = tmp_path / "generic.parquet"
    source.touch()

    completed = subprocess.run(
        ["bash", str(SCRIPT), "generic-benchmark-smoke", str(source)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    arguments = capture.read_text(encoding="utf-8").splitlines()
    assert arguments[arguments.index("--input-profile") + 1] == (
        "polymarket-market-snapshot-v1"
    )
    assert arguments[arguments.index("--max-propositions") + 1] == "5000"
