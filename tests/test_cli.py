from __future__ import annotations

import argparse

import pytest

from oddsfox_graph.cli import build_parser


def _commands(parser: argparse.ArgumentParser) -> set[str]:
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if choices:
            return set(choices)
    raise AssertionError("No argparse subcommands found")


def test_cli_contains_current_discovery_and_explorer_workflows() -> None:
    commands = _commands(build_parser())
    assert commands == {
        "discover",
        "doctor",
        "qualify",
        "release-validate",
        "run-summary",
        "model-manifest",
        "model-check",
        "nodes",
        "edges",
        "condition",
        "explain",
        "explain-edge",
        "search",
        "prove",
        "why-not",
        "serve",
        "explorer-export",
    }


@pytest.mark.parametrize(
    "command",
    (
        "build",
        "review-export",
        "review-score",
        "benchmark-export",
        "benchmark-compile",
        "evaluate",
        "model-profile",
        "benchmark-summary",
    ),
)
def test_removed_commands_are_rejected(command: str) -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([command])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "flag",
    (
        "--benchmark",
        "--model-profile",
        "--require-ready",
        "--pricing-file",
        "--parse-model",
        "--classify-model",
    ),
)
def test_removed_discovery_flags_are_rejected(flag: str) -> None:
    required = [
        "discover",
        "--input",
        "input.parquet",
        "--out",
        "out",
        "--cache-dir",
        "cache",
        "--primary-model-manifest",
        "primary.json",
        "--verifier-model-manifest",
        "verifier.json",
        "--compute-profile",
        "compute.json",
    ]
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([*required, flag, "removed"])
    assert exc.value.code == 2


def test_discovery_all_propositions_is_explicit_and_exclusive() -> None:
    required = [
        "discover",
        "--input", "input.parquet",
        "--out", "out",
        "--cache-dir", "cache",
        "--primary-model-manifest", "primary.json",
        "--verifier-model-manifest", "verifier.json",
        "--compute-profile", "compute.json",
    ]
    args = build_parser().parse_args([*required, "--all-propositions"])
    assert args.all_propositions is True
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(
            [*required, "--all-propositions", "--max-propositions", "10"]
        )
    assert exc.value.code == 2


def test_explorer_export_uses_explicit_scope_and_identifier() -> None:
    args = build_parser().parse_args(
        [
            "explorer-export",
            "--out", "graph",
            "--destination", "export",
            "--scope", "event",
            "--identifier", "event-one",
        ]
    )
    assert args.scope == "event"
    assert args.identifier == "event-one"


@pytest.mark.parametrize(
    "command",
    ("nodes", "edges", "condition", "explain", "explain-edge", "search", "prove", "why-not"),
)
def test_query_commands_offer_all_output_formats(command: str) -> None:
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    command_parser = subparsers.choices[command]
    output = next(action for action in command_parser._actions if action.dest == "output_format")
    assert set(output.choices or ()) == {"table", "json", "jsonl"}
