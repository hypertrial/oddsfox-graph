from __future__ import annotations

import argparse
import json
from pathlib import Path

from validate_discovery_release import (
    CANONICAL_SOURCE_SHA256,
    FIXTURE_SCHEMA_VERSION,
    PACKAGE_VERSION,
    REQUIRED_FILES,
    REQUIRED_TREES,
    _sha256,
    _tree_provenance,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a content-bound discovery release fixture manifest."
    )
    parser.add_argument("--fixture-root", required=True, type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        help="Defaults to <fixture-root>/fixture-manifest.json.",
    )
    args = parser.parse_args()
    root = args.fixture_root.resolve()
    if _sha256(root / "input.parquet") != CANONICAL_SOURCE_SHA256:
        raise ValueError("Fixture input is not the canonical supplied catalog")
    files = {}
    for relative in REQUIRED_FILES:
        path = root / relative
        if not path.is_file():
            raise ValueError(f"Missing required fixture file: {relative}")
        files[relative] = _sha256(path)
    trees = {}
    for relative in REQUIRED_TREES:
        path = root / relative
        if not path.is_dir():
            raise ValueError(f"Missing required fixture tree: {relative}")
        provenance = _tree_provenance(path)
        if provenance["file_count"] < 1:
            raise ValueError(f"Required fixture tree is empty: {relative}")
        trees[relative] = provenance
    payload = {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "package_version": PACKAGE_VERSION,
        "source_sha256": CANONICAL_SOURCE_SHA256,
        "files": files,
        "trees": trees,
    }
    output = (args.output or root / "fixture-manifest.json").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
