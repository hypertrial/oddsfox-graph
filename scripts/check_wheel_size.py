from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from oddsfox_graph._discovery.performance_contracts import (  # noqa: E402
    validate_performance_budget,
)


MAX_WHEEL_BYTES = 2 * 1024 * 1024
STATIC_ROOT = "oddsfox_graph/static/explorer"
BUDGET_PATH = "oddsfox_graph/benchmarks/m4-v0.13-fast-performance-budget.json"


def check_wheel_size(path: Path) -> int:
    """Return the wheel size, rejecting missing or oversized distributions."""

    resolved = path.resolve()
    if not resolved.is_file() or resolved.suffix != ".whl":
        raise ValueError(f"Wheel is missing or invalid: {resolved}")
    size = resolved.stat().st_size
    if size > MAX_WHEEL_BYTES:
        raise ValueError(
            f"Wheel exceeds the 2 MiB v0.13 delivery budget: {size} bytes"
        )
    try:
        with zipfile.ZipFile(resolved) as archive:
            names = set(archive.namelist())
            required = {
                "oddsfox_graph/__init__.py",
                BUDGET_PATH,
                f"{STATIC_ROOT}/index.html",
            }
            missing = sorted(required - names)
            if missing:
                raise ValueError(
                    "Wheel is missing required v0.13 content: " + ", ".join(missing)
                )
            try:
                archived_budget = json.loads(archive.read(BUDGET_PATH))
            except json.JSONDecodeError as exc:
                raise ValueError("Wheel performance budget is not valid JSON") from exc
            validate_performance_budget(archived_budget)
            forbidden = sorted(
                name
                for name in names
                if name.startswith(f"{STATIC_ROOT}/")
                and (
                    name.lower().endswith((".wasm", ".parquet"))
                    or "duckdb" in name.lower()
                )
            )
            if forbidden:
                raise ValueError(
                    "Wheel contains removed static runtime content: "
                    + ", ".join(forbidden)
                )
            html = archive.read(f"{STATIC_ROOT}/index.html").decode("utf-8")
            match = re.search(r'src="\./(assets/index-[^"]+\.js)"', html)
            if match is None:
                raise ValueError("Wheel explorer has no hashed entry chunk")
            expected_entry = f"{STATIC_ROOT}/{match.group(1)}"
            entry_chunks = sorted(
                name
                for name in names
                if re.fullmatch(rf"{STATIC_ROOT}/assets/index-[^/]+\.js", name)
            )
            if entry_chunks != [expected_entry]:
                raise ValueError(
                    "Wheel explorer contains missing or stale entry chunks: "
                    + ", ".join(entry_chunks)
                )
    except (UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise ValueError(f"Wheel is not a valid v0.13 archive: {resolved}") from exc
    return size


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce the packaged wheel budget.")
    parser.add_argument("--wheel", required=True, type=Path)
    args = parser.parse_args()
    size = check_wheel_size(args.wheel)
    print(json.dumps({"bytes": size, "wheel": args.wheel.name}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
