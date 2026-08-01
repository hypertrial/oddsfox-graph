from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Literal

from .graph import Graph, Relation


EDGE_TYPES = (
    "compatible",
    "complement",
    "equivalent",
    "implies",
    "mutually_exclusive",
)
OUTPUT_FORMATS = ("table", "json", "jsonl")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oddsfox-graph")
    sub = parser.add_subparsers(dest="cmd", required=True)

    discover = sub.add_parser("discover")
    discover.add_argument("--mode", required=True, choices=("fast", "full"))
    discover.add_argument("--input", required=True, type=Path)
    discover.add_argument("--out", required=True, type=Path)
    _add_full_discovery_arguments(discover, required=False)
    discover.add_argument("--incremental-from", type=Path)
    discover.add_argument("--offline", action="store_true")
    discover.add_argument("--deadline-seconds", type=float)
    discover.add_argument(
        "--progress-format",
        choices=("auto", "plain", "json", "quiet"),
        default="auto",
    )
    discover.add_argument("--output-format", choices=OUTPUT_FORMATS, default="table")

    qualify = sub.add_parser("qualify")
    _add_full_discovery_arguments(qualify, required=True)
    qualify.add_argument("--input", required=True, type=Path)
    qualify.add_argument("--out", required=True, type=Path)
    qualify.add_argument("--seed", type=int, default=0)
    qualify.add_argument("--output-format", choices=("table", "json"), default="table")

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--mode", required=True, choices=("fast", "full"))
    _add_runtime_arguments(doctor, required=False)
    doctor.add_argument("--input", required=True, type=Path)
    doctor.add_argument("--out", required=True, type=Path)
    doctor.add_argument("--cache-dir", type=Path)
    doctor.add_argument("--automation-profile", type=Path)
    doctor.add_argument("--compute-profile", type=Path)
    doctor.add_argument("--output-format", choices=("table", "json"), default="table")

    manifest = sub.add_parser("model-manifest")
    manifest.add_argument("--model-path", required=True, type=Path)
    manifest.add_argument("--model-id", required=True)
    manifest.add_argument("--revision", required=True)
    manifest.add_argument("--license", required=True)
    manifest.add_argument("--runtime", required=True, choices=("llama.cpp", "vllm"))
    manifest.add_argument("--llm-base-url", required=True)
    manifest.add_argument("--allow-remote-inference", action="store_true")
    manifest.add_argument("--output", required=True, type=Path)

    check = sub.add_parser("model-check")
    check.add_argument("--model-manifest", required=True, type=Path)
    check.add_argument("--llm-base-url", required=True)
    check.add_argument("--allow-remote-inference", action="store_true")
    check.add_argument("--output-format", choices=("table", "json"), default="table")

    release = sub.add_parser("release-validate")
    release.add_argument("--fixture-root", required=True, type=Path)
    release.add_argument("--work-dir", required=True, type=Path)
    release.add_argument("--output-format", choices=("table", "json"), default="table")

    summary = sub.add_parser("run-summary")
    summary.add_argument("--out", required=True, type=Path)
    summary.add_argument("--output-format", choices=("table", "json"), default="table")

    serve = sub.add_parser("serve")
    serve.add_argument("--out", required=True, type=Path)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open-browser", action="store_true")
    serve.add_argument("--max-response-nodes", type=int, default=5_000)
    serve.add_argument("--max-response-edges", type=int, default=10_000)

    explorer_export = sub.add_parser("explorer-export")
    explorer_export.add_argument("--out", required=True, type=Path)
    explorer_export.add_argument("--destination", required=True, type=Path)
    explorer_export.add_argument(
        "--scope",
        required=True,
        choices=("event", "component", "neighborhood"),
    )
    explorer_export.add_argument("--identifier", required=True)
    explorer_export.add_argument("--max-nodes", type=int, default=5_000)
    explorer_export.add_argument("--max-edges", type=int, default=10_000)
    explorer_export.add_argument(
        "--output-format", choices=("table", "json"), default="table"
    )

    nodes = sub.add_parser("nodes")
    _add_query_output(nodes)
    nodes.add_argument("--top", type=int, default=50)

    edges = sub.add_parser("edges")
    _add_query_output(edges)
    edges.add_argument("--edge-type", choices=EDGE_TYPES)
    edges.add_argument("--top", type=int, default=50)

    condition = sub.add_parser("condition")
    _add_query_output(condition)
    condition.add_argument("--a", required=True)
    condition.add_argument("--b", required=True)

    explain = sub.add_parser("explain")
    _add_query_output(explain)
    explain.add_argument("--node", required=True)

    explain_edge = sub.add_parser("explain-edge")
    _add_query_output(explain_edge)
    explain_edge.add_argument("--src", required=True)
    explain_edge.add_argument("--dst", required=True)
    explain_edge.add_argument("--edge-type", required=True, choices=EDGE_TYPES)

    search = sub.add_parser("search")
    _add_query_output(search)
    search.add_argument("--query", required=True)
    search.add_argument("--top", type=int, default=20)

    prove = sub.add_parser("prove")
    _add_query_output(prove)
    prove.add_argument("--from", dest="from_node", required=True)
    prove.add_argument("--to", dest="to_node", required=True)
    prove.add_argument("--max-hops", type=int, default=4)
    prove.add_argument("--max-paths", type=int, default=3)

    why_not = sub.add_parser("why-not")
    _add_query_output(why_not)
    why_not.add_argument("--a", required=True)
    why_not.add_argument("--b", required=True)
    why_not.add_argument("--relation", required=True, choices=EDGE_TYPES)
    return parser


def _add_runtime_arguments(
    parser: argparse.ArgumentParser,
    *,
    required: bool,
) -> None:
    parser.add_argument("--primary-model-manifest", required=required, type=Path)
    parser.add_argument("--verifier-model-manifest", required=required, type=Path)
    parser.add_argument("--primary-base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--verifier-base-url", default="http://127.0.0.1:8081/v1")
    parser.add_argument("--allow-remote-inference", action="store_true")


def _add_full_discovery_arguments(
    parser: argparse.ArgumentParser,
    *,
    required: bool,
) -> None:
    _add_runtime_arguments(parser, required=required)
    parser.add_argument("--cache-dir", required=required, type=Path)
    parser.add_argument("--automation-profile", type=Path)
    parser.add_argument("--compute-profile", required=required, type=Path)
    parser.add_argument("--embedding-model", default="sentence-transformers/all-MiniLM-L6-v2")
    parser.add_argument("--embedding-revision", default="1110a243fdf4706b3f48f1d95db1a4f5529b4d41")
    parser.add_argument("--accept-confidence", type=float, default=0.95)
    parser.add_argument("--relation-threshold", action="append", default=[], metavar="RELATION=VALUE")
    parser.add_argument("--parse-confidence", type=float, default=0.95)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--embedding-block-size", type=int, default=512)
    parser.add_argument("--max-propositions", type=int)
    parser.add_argument("--max-candidates", type=int, default=400_000)
    parser.add_argument("--max-llm-pairs", type=int, default=5_000)
    parser.add_argument("--classification-coverage-target", type=float, default=0.0)
    parser.add_argument("--max-visible-coverage-gap", type=float, default=1.0)
    parser.add_argument("--llm-concurrency", type=int, default=2)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--generation-top-p", type=float, default=0.8)
    parser.add_argument("--generation-top-k", type=int, default=20)
    parser.add_argument("--presence-penalty", type=float, default=1.5)
    parser.add_argument("--parse-max-output-tokens", type=int, default=4096)
    parser.add_argument("--classify-max-output-tokens", type=int, default=1024)


def _add_query_output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--output-format", choices=OUTPUT_FORMATS, default="table")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_argv)
    args._provided_flags = frozenset(
        token.partition("=")[0] for token in raw_argv if token.startswith("--")
    )
    try:
        result = _dispatch(args)
        _emit(result, args.output_format if hasattr(args, "output_format") else "json")
        if (
            args.cmd == "doctor"
            and isinstance(result, dict)
            and not result.get("passed", False)
        ):
            return 1
        if args.cmd == "discover" and isinstance(result, dict):
            deadline = result.get("deadline")
            if isinstance(deadline, dict) and deadline.get("met") is False:
                return 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _dispatch(args: argparse.Namespace) -> dict[str, Any] | list[dict[str, Any]]:
    if args.cmd in {"discover", "qualify"}:
        from ._discovery.contracts import DiscoveryConfig
        mode = getattr(args, "mode", "full")
        if mode == "fast":
            _reject_fast_only_conflicts(args)
            config = DiscoveryConfig(
                mode="fast",
                incremental_from=args.incremental_from,
                max_propositions=args.max_propositions,
                deadline_seconds=args.deadline_seconds or 120.0,
                output_format=args.output_format,
                progress_format=args.progress_format,
            )
            from .discovery import discover

            return discover(args.input, args.out, config=config)
        _require_full_arguments(args, qualification=args.cmd == "qualify")
        from ._discovery.inference import load_model_manifest

        primary = load_model_manifest(args.primary_model_manifest)
        verifier = load_model_manifest(args.verifier_model_manifest)
        config = DiscoveryConfig(
            mode="full",
            cache_dir=args.cache_dir,
            incremental_from=getattr(args, "incremental_from", None),
            compute_profile=args.compute_profile,
            automation_profile=getattr(args, "automation_profile", None),
            primary_model_manifest=args.primary_model_manifest,
            verifier_model_manifest=args.verifier_model_manifest,
            offline=getattr(args, "offline", False),
            primary_base_url=args.primary_base_url,
            verifier_base_url=args.verifier_base_url,
            allow_remote_inference=args.allow_remote_inference,
            primary_model=primary.loaded_model_identifier,
            verifier_model=verifier.loaded_model_identifier,
            embedding_model=args.embedding_model,
            embedding_revision=args.embedding_revision,
            sampling_seed=getattr(args, "seed", 0),
            temperature=args.temperature,
            generation_top_p=args.generation_top_p,
            generation_top_k=args.generation_top_k,
            presence_penalty=args.presence_penalty,
            parse_max_output_tokens=args.parse_max_output_tokens,
            classify_max_output_tokens=args.classify_max_output_tokens,
            accept_confidence=args.accept_confidence,
            relation_thresholds=_relation_thresholds(args.relation_threshold),
            parse_confidence=args.parse_confidence,
            top_k=args.top_k,
            embedding_block_size=args.embedding_block_size,
            max_propositions=args.max_propositions,
            max_candidates=args.max_candidates,
            max_llm_pairs=args.max_llm_pairs,
            classification_coverage_target=args.classification_coverage_target,
            max_visible_coverage_gap=args.max_visible_coverage_gap,
            llm_concurrency=args.llm_concurrency,
            output_format=getattr(args, "output_format", "table"),
            progress_format=getattr(args, "progress_format", "quiet"),
            deadline_seconds=getattr(args, "deadline_seconds", None) or 3_600.0,
        )
        if args.cmd == "discover":
            from .discovery import discover

            return discover(args.input, args.out, config=config)
        from .qualification import qualify_catalog

        return qualify_catalog(args.input, args.out, config=config)
    if args.cmd == "doctor":
        from .operability import doctor

        return doctor(
            args.input,
            args.out,
            args.mode,
            args.cache_dir,
            args.automation_profile,
            args.primary_model_manifest,
            args.verifier_model_manifest,
            args.primary_base_url,
            args.verifier_base_url,
            args.compute_profile,
            allow_remote=args.allow_remote_inference,
        ).model_dump(mode="json")
    if args.cmd == "model-manifest":
        from .model_tools import create_model_manifest

        return create_model_manifest(
            args.model_path,
            model_id=args.model_id,
            revision=args.revision,
            license_id=args.license,
            runtime=args.runtime,
            llm_base_url=args.llm_base_url,
            output_path=args.output,
            allow_remote=args.allow_remote_inference,
        )
    if args.cmd == "model-check":
        from .model_tools import check_model

        return check_model(
            args.model_manifest,
            args.llm_base_url,
            allow_remote=args.allow_remote_inference,
        )
    if args.cmd == "release-validate":
        from .release_validation import validate_release_fixture

        return validate_release_fixture(args.fixture_root, args.work_dir)
    if args.cmd == "run-summary":
        from .operability import run_summary

        return run_summary(args.out)
    if args.cmd == "serve":
        from .explorer import serve_graph

        serve_graph(
            args.out,
            host=args.host,
            port=args.port,
            open_browser=args.open_browser,
            max_response_nodes=args.max_response_nodes,
            max_response_edges=args.max_response_edges,
        )
        return {"status": "stopped"}
    if args.cmd == "explorer-export":
        from .explorer import export_explorer

        return export_explorer(
            args.out,
            args.destination,
            scope=args.scope,
            identifier=args.identifier,
            max_nodes=args.max_nodes,
            max_edges=args.max_edges,
        )
    graph = Graph.open(args.out)
    if args.cmd == "nodes":
        return [row.model_dump(mode="json") for row in graph.nodes(args.top)]
    if args.cmd == "edges":
        edge_relation = args.edge_type
        return [
            row.model_dump(mode="json")
            for row in graph.edges(edge_relation, args.top)
        ]
    if args.cmd == "condition":
        return [dict(row) for row in graph.condition(args.a, args.b)]
    if args.cmd == "explain":
        return graph.explain_node(args.node)
    if args.cmd == "explain-edge":
        return graph.explain_edge(args.src, args.dst, args.edge_type)
    if args.cmd == "search":
        return [row.model_dump(mode="json") for row in graph.search(args.query, args.top)]
    if args.cmd == "prove":
        return [
            row.model_dump(mode="json")
            for row in graph.prove(
                args.from_node,
                args.to_node,
                max_hops=args.max_hops,
                max_paths=args.max_paths,
            )
        ]
    if args.cmd == "why-not":
        why_relation: Relation = args.relation
        return graph.why_not(args.a, args.b, why_relation).model_dump(mode="json")
    raise AssertionError(f"Unhandled command {args.cmd}")


def _relation_thresholds(values: list[str]) -> dict[str, float]:
    from ._discovery.contracts import DEFAULT_RELATION_THRESHOLDS

    thresholds = dict(DEFAULT_RELATION_THRESHOLDS)
    for value in values:
        relation, separator, raw_threshold = value.partition("=")
        if not separator or not relation or not raw_threshold:
            raise ValueError("--relation-threshold must use RELATION=VALUE syntax")
        thresholds[relation] = float(raw_threshold)
    return thresholds


def _require_full_arguments(
    args: argparse.Namespace,
    *,
    qualification: bool,
) -> None:
    required = [
        "cache_dir",
        "compute_profile",
        "primary_model_manifest",
        "verifier_model_manifest",
    ]
    if not qualification:
        required.append("automation_profile")
    missing = [name for name in required if getattr(args, name, None) is None]
    if missing:
        flags = ", ".join("--" + name.replace("_", "-") for name in missing)
        raise ValueError(f"Full mode requires {flags}")


def _reject_fast_only_conflicts(args: argparse.Namespace) -> None:
    conflicts = [
        name
        for name in (
            "cache_dir",
            "automation_profile",
            "compute_profile",
            "primary_model_manifest",
            "verifier_model_manifest",
        )
        if getattr(args, name, None) is not None
    ]
    if args.offline:
        conflicts.append("offline")
    if args.relation_threshold:
        conflicts.append("relation_threshold")
    full_only_flags = {
        "--primary-base-url",
        "--verifier-base-url",
        "--allow-remote-inference",
        "--embedding-model",
        "--embedding-revision",
        "--accept-confidence",
        "--relation-threshold",
        "--parse-confidence",
        "--top-k",
        "--embedding-block-size",
        "--max-candidates",
        "--max-llm-pairs",
        "--classification-coverage-target",
        "--max-visible-coverage-gap",
        "--llm-concurrency",
        "--temperature",
        "--generation-top-p",
        "--generation-top-k",
        "--presence-penalty",
        "--parse-max-output-tokens",
        "--classify-max-output-tokens",
    }
    provided: frozenset[str] = getattr(args, "_provided_flags", frozenset())
    conflicts.extend(
        flag.removeprefix("--").replace("-", "_")
        for flag in sorted(full_only_flags & set(provided))
    )
    if conflicts:
        flags = ", ".join(
            "--" + name.replace("_", "-") for name in sorted(set(conflicts))
        )
        raise ValueError(f"Fast mode rejects full-mode options: {flags}")


def _emit(
    value: dict[str, Any] | list[dict[str, Any]],
    output_format: Literal["table", "json", "jsonl"] | str,
) -> None:
    if output_format == "json":
        print(json.dumps(value, indent=2, sort_keys=True, default=str))
        return
    rows = value if isinstance(value, list) else [value]
    if output_format == "jsonl":
        for row in rows:
            print(json.dumps(row, sort_keys=True, default=str))
        return
    if not rows:
        print("(no rows)")
        return
    flattened = [_flatten(row) for row in rows]
    columns = sorted({key for row in flattened for key in row})
    widths = {
        column: min(
            80,
            max(len(column), *(len(str(row.get(column, ""))) for row in flattened)),
        )
        for column in columns
    }
    print("  ".join(column.ljust(widths[column]) for column in columns))
    print("  ".join("-" * widths[column] for column in columns))
    for row in flattened:
        print(
            "  ".join(
                str(row.get(column, ""))[: widths[column]].ljust(widths[column])
                for column in columns
            )
        )


def _flatten(value: dict[str, Any]) -> dict[str, object]:
    return {
        key: (
            json.dumps(item, sort_keys=True, default=str)
            if isinstance(item, (dict, list, tuple))
            else item
        )
        for key, item in value.items()
    }


if __name__ == "__main__":
    raise SystemExit(main())
