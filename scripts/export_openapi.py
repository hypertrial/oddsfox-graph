"""Export the deterministic explorer OpenAPI contract for frontend type generation."""

from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from oddsfox_graph._discovery.provenance import atomic_write_json
from oddsfox_graph._explorer.service import create_schema_app


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export the schema-only explorer OpenAPI document."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("web/openapi.json"),
    )
    args = parser.parse_args()
    output = args.output.resolve()
    document = json.loads(json.dumps(create_schema_app().openapi(), sort_keys=True))
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output, document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
