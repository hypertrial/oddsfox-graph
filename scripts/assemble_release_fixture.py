from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from oddsfox_graph import __version__
from oddsfox_graph._discovery.manifest_contracts import (
    FastBuildManifest,
    load_build_manifest,
)
from oddsfox_graph._discovery.provenance import (
    atomic_write_json,
    canonical_json_sha256,
    sha256_file,
)
from oddsfox_graph._discovery.versions import (
    CANONICAL_CATALOG_SHA256,
    RELEASE_FIXTURE_SCHEMA_VERSION,
)
from oddsfox_graph.release_validation import REQUIRED_FILES, validate_release_fixture


def assemble_release_fixture(
    *,
    input_path: Path,
    fast_baseline: Path,
    performance_report: Path,
    destination: Path,
) -> dict[str, Any]:
    """Validate, assemble, and atomically publish one release fixture."""

    if os.path.lexists(destination):
        raise ValueError(f"Release fixture destination already exists: {destination}")
    source = _regular_file(input_path, "canonical input")
    baseline = _regular_directory(fast_baseline, "fast baseline")
    report = _regular_file(performance_report, "performance report")
    target = destination.resolve()
    _validate_destination(target, source=source, baseline=baseline, report=report)

    source_sha256 = sha256_file(source)
    if source_sha256 != CANONICAL_CATALOG_SHA256:
        raise ValueError(
            "Release fixture input is not the canonical catalog: "
            f"expected {CANONICAL_CATALOG_SHA256}, got {source_sha256}"
        )

    manifest = load_build_manifest(baseline / "build_manifest.json")
    if not isinstance(manifest, FastBuildManifest):
        raise ValueError("Release fixture baseline must be a completed fast build")
    if manifest.input.sha256 != source_sha256:
        raise ValueError("Release fixture baseline does not bind the supplied input")

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{target.name}.fixture-staging-",
            dir=target.parent,
        )
    )
    validation_work: Path | None = None
    published = False
    try:
        validation_work = Path(
            tempfile.mkdtemp(
                prefix=f".{target.name}.fixture-validation-",
                dir=target.parent,
            )
        )
        shutil.copy2(source, staging / "input.parquet")
        baseline_target = staging / "baselines" / "fast"
        baseline_target.parent.mkdir(parents=True)
        shutil.copytree(baseline, baseline_target, symlinks=True)
        _regular_directory(baseline_target, "copied fast baseline")
        shutil.copy2(report, staging / "performance_report.json")

        atomic_write_json(
            staging / "expected_artifact_hashes.json",
            {"fast": manifest.artifact_hashes},
        )
        bound_files = {
            relative: sha256_file(staging / relative)
            for relative in REQUIRED_FILES
        }
        tree_sha256, tree_file_count = _tree_digest(baseline_target)
        atomic_write_json(
            staging / "release-fixture.json",
            {
                "schema_version": RELEASE_FIXTURE_SCHEMA_VERSION,
                "package_version": __version__,
                "source_sha256": source_sha256,
                "files": bound_files,
                "trees": {
                    "baselines/fast": {
                        "sha256": tree_sha256,
                        "file_count": tree_file_count,
                    }
                },
            },
        )

        validation = validate_release_fixture(staging, validation_work)
        if validation.get("passed") is not True:
            raise RuntimeError("Assembled release fixture did not pass validation")
        if os.path.lexists(target):
            raise ValueError(
                f"Release fixture destination appeared during assembly: {target}"
            )
        os.replace(staging, target)
        published = True
        return {
            "destination": str(target),
            "schema_version": RELEASE_FIXTURE_SCHEMA_VERSION,
            "source_sha256": source_sha256,
            "baseline_graph_fingerprint": manifest.graph_content_fingerprint,
            "baseline_artifact_hashes": manifest.artifact_hashes,
            "tree_sha256": tree_sha256,
            "tree_file_count": tree_file_count,
            "validation": validation,
        }
    finally:
        if validation_work is not None:
            shutil.rmtree(validation_work, ignore_errors=True)
        if not published:
            shutil.rmtree(staging, ignore_errors=True)


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symbolic link: {path}")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"{label} is missing: {path}") from exc
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file: {resolved}")
    return resolved


def _regular_directory(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symbolic link: {path}")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"{label} is missing: {path}") from exc
    if not resolved.is_dir():
        raise ValueError(f"{label} must be a directory: {resolved}")
    for entry in resolved.rglob("*"):
        if entry.is_symlink():
            raise ValueError(f"{label} contains a symbolic link: {entry}")
        if not entry.is_file() and not entry.is_dir():
            raise ValueError(f"{label} contains a special file: {entry}")
    return resolved


def _validate_destination(
    destination: Path,
    *,
    source: Path,
    baseline: Path,
    report: Path,
) -> None:
    if os.path.lexists(destination):
        raise ValueError(f"Release fixture destination already exists: {destination}")
    for label, candidate in (
        ("canonical input", source),
        ("fast baseline", baseline),
        ("performance report", report),
    ):
        if (
            destination == candidate
            or destination in candidate.parents
            or candidate in destination.parents
        ):
            raise ValueError(
                "Release fixture destination must not overlap "
                f"the {label}: {destination} and {candidate}"
            )


def _tree_digest(directory: Path) -> tuple[str, int]:
    rows = [
        {
            "path": path.relative_to(directory).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    ]
    if not rows:
        raise ValueError(f"Release fixture baseline is empty: {directory}")
    return canonical_json_sha256(rows), len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Atomically assemble and validate the canonical fast release fixture."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--fast-baseline", required=True, type=Path)
    parser.add_argument("--performance-report", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    result = assemble_release_fixture(
        input_path=args.input,
        fast_baseline=args.fast_baseline,
        performance_report=args.performance_report,
        destination=args.destination,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
