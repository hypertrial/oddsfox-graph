from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from oddsfox_graph.cli import build_parser


def _commands(parser: argparse.ArgumentParser) -> set[str]:
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if choices:
            return set(choices)
    raise AssertionError("No argparse subcommands found")


def test_cli_contains_only_current_workflows() -> None:
    parser = build_parser()
    commands = _commands(parser)
    assert {"build", "review-export", "review-score"}.isdisjoint(commands)
    assert {
        "discover",
        "benchmark-export",
        "benchmark-compile",
        "evaluate",
        "model-manifest",
        "model-check",
        "model-profile",
        "nodes",
        "edges",
        "condition",
        "explain",
        "explain-edge",
        "search",
        "benchmark-summary",
    } <= commands


@pytest.mark.parametrize("command", ["build", "review-export", "review-score"])
def test_removed_commands_are_rejected(command: str) -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([command])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    ("command", "flag"),
    [
        ("evaluate", "--pricing-file"),
        ("discover", "--pricing-file"),
        ("discover", "--taxonomy"),
    ],
)
def test_removed_flags_are_rejected(
    tmp_path: Path,
    command: str,
    flag: str,
) -> None:
    arguments = [command, "--out", str(tmp_path)]
    if command == "evaluate":
        arguments.extend(
            ["--benchmark", str(tmp_path / "benchmark.parquet")]
        )
    arguments.extend([flag, str(tmp_path / "removed-value")])
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(arguments)
    assert exc.value.code == 2
