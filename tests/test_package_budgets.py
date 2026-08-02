from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_wheel_size.py"
BUDGET = ROOT / "oddsfox_graph" / "benchmarks" / "m4-v0.13-fast-performance-budget.json"


def test_wheel_size_checker_accepts_bounded_wheel(tmp_path: Path) -> None:
    wheel = tmp_path / "oddsfox_graph-0.13.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("oddsfox_graph/__init__.py", '__version__ = "0.13.0"')
        archive.writestr(
            "oddsfox_graph/benchmarks/m4-v0.13-fast-performance-budget.json",
            BUDGET.read_bytes(),
        )
        archive.writestr(
            "oddsfox_graph/static/explorer/index.html",
            '<script type="module" src="./assets/index-current.js"></script>',
        )
        archive.writestr(
            "oddsfox_graph/static/explorer/assets/index-current.js",
            "export {};",
        )

    completed = subprocess.run(
        [sys.executable, str(CHECKER), "--wheel", str(wheel)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0
    assert '"wheel": "oddsfox_graph-0.13.0-py3-none-any.whl"' in completed.stdout


def test_wheel_size_checker_rejects_oversized_wheel(tmp_path: Path) -> None:
    wheel = tmp_path / "oddsfox_graph-0.13.0-py3-none-any.whl"
    with wheel.open("wb") as handle:
        handle.truncate(2 * 1024 * 1024 + 1)

    completed = subprocess.run(
        [sys.executable, str(CHECKER), "--wheel", str(wheel)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode != 0
    assert "exceeds the 2 MiB" in completed.stderr


def test_wheel_size_checker_rejects_removed_static_runtime(tmp_path: Path) -> None:
    wheel = tmp_path / "oddsfox_graph-0.13.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("oddsfox_graph/__init__.py", '__version__ = "0.13.0"')
        archive.writestr(
            "oddsfox_graph/benchmarks/m4-v0.13-fast-performance-budget.json",
            BUDGET.read_bytes(),
        )
        archive.writestr(
            "oddsfox_graph/static/explorer/index.html",
            '<script type="module" src="./assets/index-current.js"></script>',
        )
        archive.writestr(
            "oddsfox_graph/static/explorer/assets/index-current.js",
            "export {};",
        )
        archive.writestr(
            "oddsfox_graph/static/explorer/assets/duckdb-stale.wasm",
            b"stale",
        )

    completed = subprocess.run(
        [sys.executable, str(CHECKER), "--wheel", str(wheel)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode != 0
    assert "removed static runtime content" in completed.stderr


def test_wheel_size_checker_rejects_invalid_performance_budget(tmp_path: Path) -> None:
    wheel = tmp_path / "oddsfox_graph-0.13.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("oddsfox_graph/__init__.py", '__version__ = "0.13.0"')
        archive.writestr(
            "oddsfox_graph/benchmarks/m4-v0.13-fast-performance-budget.json",
            "{}",
        )
        archive.writestr(
            "oddsfox_graph/static/explorer/index.html",
            '<script type="module" src="./assets/index-current.js"></script>',
        )
        archive.writestr(
            "oddsfox_graph/static/explorer/assets/index-current.js",
            "export {};",
        )

    completed = subprocess.run(
        [sys.executable, str(CHECKER), "--wheel", str(wheel)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode != 0
    assert "Performance budget is invalid" in completed.stderr
