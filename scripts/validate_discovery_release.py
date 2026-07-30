from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate cache-complete discovery against the real catalog."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--expected-hashes", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--propositions", default="500,2000")
    args = parser.parse_args()

    from oddsfox_graph.discovery import DiscoveryConfig, discover
    from oddsfox_graph.review import score_review

    expected = json.loads(args.expected_hashes.read_text(encoding="utf-8"))
    limits = [int(value) for value in args.propositions.split(",") if value]
    if not limits or any(value < 1 for value in limits):
        raise ValueError("--propositions must contain positive comma-separated limits")

    results: dict[str, Any] = {}
    for limit in limits:
        out = args.work_dir / str(limit)
        stats = discover(
            args.input,
            out,
            config=DiscoveryConfig(
                cache_dir=args.cache_dir,
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
    evaluation = score_review(args.work_dir / str(largest), args.labels)
    if not evaluation["passed"]:
        raise RuntimeError("Human review thresholds failed")
    results["evaluation"] = evaluation
    args.work_dir.mkdir(parents=True, exist_ok=True)
    (args.work_dir / "release-validation.json").write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
