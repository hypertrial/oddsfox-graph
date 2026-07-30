from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


CANONICAL_SOURCE_SHA256 = (
    "790bd1595b379472ad65ba0073105b4eb630974d04e7b44d58c8a4929f274aa2"
)
FIXTURE_SCHEMA_VERSION = "discovery-release-fixture-v1"
PACKAGE_VERSION = "0.5.0"
REQUIRED_FILES = (
    "input.parquet",
    "benchmark.parquet",
    "pricing.json",
    "expected-artifact-hashes.json",
    "baselines/5000/build_manifest.json",
    "baselines/20000/build_manifest.json",
)
REQUIRED_TREES = (
    "cache",
    "baselines/5000",
    "baselines/20000",
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_provenance(path: Path) -> dict[str, Any]:
    entries = sorted(path.rglob("*"))
    symlinks = [item for item in entries if item.is_symlink()]
    if symlinks:
        raise ValueError(
            "Release fixture trees cannot contain symbolic links: "
            + str(symlinks[0])
        )
    files = [item for item in entries if item.is_file()]
    digest = hashlib.sha256()
    for item in files:
        relative = item.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(item).encode("ascii"))
        digest.update(b"\n")
    return {"sha256": digest.hexdigest(), "file_count": len(files)}


def _fixture_path(fixture_root: Path, relative: str) -> Path:
    relative_path = Path(relative)
    if (
        relative_path.is_absolute()
        or ".." in relative_path.parts
        or relative_path.as_posix() != relative
    ):
        raise ValueError(f"Unsafe release fixture path: {relative}")
    return fixture_root / relative_path


def _validate_fixture_manifest(
    fixture_root: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        raise ValueError("Unsupported release fixture manifest schema")
    if manifest.get("package_version") != PACKAGE_VERSION:
        raise ValueError("Release fixture was not produced for package v0.5.0")
    if manifest.get("source_sha256") != CANONICAL_SOURCE_SHA256:
        raise ValueError("Release fixture source binding is not canonical")
    files = manifest.get("files")
    trees = manifest.get("trees")
    if not isinstance(files, dict) or not set(REQUIRED_FILES) <= set(files):
        raise ValueError("Release fixture manifest is missing required file bindings")
    if not isinstance(trees, dict) or not set(REQUIRED_TREES) <= set(trees):
        raise ValueError("Release fixture manifest is missing required tree bindings")
    for relative, expected in sorted(files.items()):
        path = _fixture_path(fixture_root, str(relative))
        if (
            not isinstance(expected, str)
            or not _SHA256_PATTERN.fullmatch(expected)
            or not path.is_file()
            or path.is_symlink()
            or _sha256(path) != expected
        ):
            raise ValueError(f"Release fixture file provenance mismatch: {relative}")
    for relative, expected in sorted(trees.items()):
        path = _fixture_path(fixture_root, str(relative))
        if (
            not isinstance(expected, dict)
            or set(expected) != {"sha256", "file_count"}
            or not isinstance(expected.get("sha256"), str)
            or not _SHA256_PATTERN.fullmatch(expected["sha256"])
            or not isinstance(expected.get("file_count"), int)
            or isinstance(expected.get("file_count"), bool)
            or expected["file_count"] < 1
            or not path.is_dir()
            or _tree_provenance(path) != expected
        ):
            raise ValueError(f"Release fixture tree provenance mismatch: {relative}")
    return manifest


def _validate_fixture_argument_bindings(
    args: argparse.Namespace,
    fixture_root: Path,
    manifest: dict[str, Any],
    limits: list[int],
) -> None:
    file_arguments = {
        "input.parquet": args.input,
        "benchmark.parquet": args.benchmark,
        "pricing.json": args.pricing_file,
        "expected-artifact-hashes.json": args.expected_hashes,
    }
    for relative, path in file_arguments.items():
        fixture_path = fixture_root / relative
        if not path.is_file() or (
            path.resolve() != fixture_path.resolve()
            and _sha256(path) != manifest["files"][relative]
        ):
            raise ValueError(
                f"Release validation argument is not fixture-bound: {relative}"
            )
    fixture_cache = fixture_root / "cache"
    if not args.cache_dir.is_dir() or (
        args.cache_dir.resolve() != fixture_cache.resolve()
        and _tree_provenance(args.cache_dir) != manifest["trees"]["cache"]
    ):
        raise ValueError("Release validation cache is not fixture-bound")
    for limit in limits:
        relative = f"baselines/{limit}"
        if relative not in manifest["trees"]:
            raise ValueError(
                f"Release fixture does not bind baseline limit {limit}"
            )
        baseline = args.baseline_dir / str(limit)
        fixture_baseline = fixture_root / relative
        if (
            not baseline.is_dir()
            or (
                baseline.resolve() != fixture_baseline.resolve()
                and _tree_provenance(baseline) != manifest["trees"][relative]
            )
        ):
            raise ValueError(
                f"Release validation baseline is not fixture-bound: {limit}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate cache-complete discovery against the real catalog."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--baseline-dir", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--expected-hashes", required=True, type=Path)
    parser.add_argument("--benchmark", required=True, type=Path)
    parser.add_argument("--pricing-file", required=True, type=Path)
    parser.add_argument("--fixture-manifest", required=True, type=Path)
    parser.add_argument("--propositions", default="5000,20000")
    args = parser.parse_args()
    if _sha256(args.input) != CANONICAL_SOURCE_SHA256:
        raise ValueError("Release validation requires the canonical supplied catalog")
    fixture_root = args.input.resolve().parent
    fixture_manifest = _validate_fixture_manifest(
        fixture_root,
        args.fixture_manifest.resolve(),
    )

    from oddsfox_graph.discovery import DiscoveryConfig, discover

    expected = json.loads(args.expected_hashes.read_text(encoding="utf-8"))
    limits = [int(value) for value in args.propositions.split(",") if value]
    if not limits or any(value < 1 for value in limits):
        raise ValueError("--propositions must contain positive comma-separated limits")
    _validate_fixture_argument_bindings(
        args,
        fixture_root,
        fixture_manifest,
        limits,
    )

    results: dict[str, Any] = {}
    results["fixture"] = fixture_manifest
    for limit in limits:
        out = args.work_dir / str(limit)
        stats = discover(
            args.input,
            out,
            config=DiscoveryConfig(
                cache_dir=args.cache_dir,
                benchmark_path=args.benchmark,
                incremental_from=args.baseline_dir / str(limit),
                pricing_file=args.pricing_file,
                require_ready=True,
                offline=True,
                max_propositions=limit,
            ),
        )
        manifest = json.loads(
            (out / "build_manifest.json").read_text(encoding="utf-8")
        )
        expected_for_limit = expected.get(str(limit))
        if expected_for_limit is None:
            raise ValueError(f"Expected hashes do not contain limit {limit}")
        if manifest["artifact_hashes"] != expected_for_limit:
            raise RuntimeError(
                f"Artifact hashes for {limit} propositions do not match the "
                "recorded online run"
            )
        results[str(limit)] = {
            "stats": stats,
            "artifact_hashes": manifest["artifact_hashes"],
            "stage_timings": manifest["stage_timings"],
            "cache": manifest["cache"],
            "usage": manifest["usage"],
        }

    largest = max(limits)
    results["evaluation"] = json.loads(
        (
            args.work_dir / str(largest) / "evaluation_report.json"
        ).read_text(encoding="utf-8")
    )
    args.work_dir.mkdir(parents=True, exist_ok=True)
    (args.work_dir / "release-validation.json").write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
