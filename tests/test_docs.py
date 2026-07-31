from __future__ import annotations

import re
from pathlib import Path

from oddsfox_graph import __version__
from oddsfox_graph._discovery.versions import (
    CACHE_ENTRY_VERSION,
    RELEASE_FIXTURE_SCHEMA_VERSION,
)
from oddsfox_graph.artifacts import ARTIFACT_COLUMNS, REPORTS
from oddsfox_graph.cli import build_parser
from oddsfox_graph.discovery import DISCOVERY_PARQUET_ARTIFACTS


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def test_cli_docs_cover_every_current_subcommand() -> None:
    subcommands = _subcommand_parsers(build_parser())
    cli_doc = (DOCS / "cli.md").read_text(encoding="utf-8")
    assert subcommands == {
        "condition",
        "discover",
        "doctor",
        "edges",
        "explain",
        "explain-edge",
        "model-check",
        "model-manifest",
        "nodes",
        "prove",
        "qualify",
        "release-validate",
        "run-summary",
        "search",
        "why-not",
        "serve",
        "explorer-export",
    }
    for command in sorted(subcommands):
        assert f"`{command}`" in cli_doc
    for flag in (
        "--primary-model-manifest",
        "--verifier-model-manifest",
        "--primary-base-url",
        "--verifier-base-url",
        "--progress-format",
        "--output-format",
        "--all-propositions",
        "--classification-coverage-target",
        "--max-visible-coverage-gap",
        "--destination",
        "--identifier",
    ):
        assert flag in cli_doc + (DOCS / "discovery.md").read_text(encoding="utf-8")


def test_artifact_docs_cover_current_public_contract() -> None:
    text = (DOCS / "artifacts.md").read_text(encoding="utf-8")
    for artifact in DISCOVERY_PARQUET_ARTIFACTS:
        assert f"`{artifact}`" in text
    for artifact, columns in ARTIFACT_COLUMNS.items():
        assert f"`{artifact}`" in text
        for column in columns:
            assert f"`{column}`" in text
    for report in REPORTS:
        assert f"`{report}`" in text
    for provenance in (
        "primary_model_version",
        "verifier_model_version",
        "consensus_fingerprint",
        "automation_profile_id",
    ):
        assert f"`{provenance}`" in text


def test_removed_workflows_do_not_appear_in_product_or_docs() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            ROOT / "README.md",
            *sorted(DOCS.glob("*.md")),
            *sorted((ROOT / "oddsfox_graph").rglob("*.py")),
        ]
    ).casefold()
    for removed in (
        "review-export",
        "review-score",
        "benchmark-export",
        "benchmark-compile",
        "model-profile",
        "ready_to_scale",
        "pricing-file",
        "review_queue.parquet",
        "benchmark.parquet",
        "evaluation_report.json",
    ):
        assert removed not in text


def test_current_versions_and_fixture_schema_are_documented() -> None:
    assert __version__ == "0.10.0"
    assert CACHE_ENTRY_VERSION == 7
    schema = (DOCS / "release-fixture-manifest.schema.json").read_text(
        encoding="utf-8"
    )
    assert '"0.10.0"' in schema
    assert RELEASE_FIXTURE_SCHEMA_VERSION in schema


def test_local_markdown_links_resolve() -> None:
    for markdown_file in [ROOT / "README.md", *sorted(DOCS.glob("*.md"))]:
        text = markdown_file.read_text(encoding="utf-8")
        anchors = _anchors(text)
        for target in _markdown_links(text):
            if _is_external_or_generated_link(target):
                continue
            path_part, _, anchor = target.partition("#")
            if path_part:
                linked_file = (markdown_file.parent / path_part).resolve()
                assert linked_file.exists(), f"{markdown_file}: missing link target {target}"
                linked_anchors = _anchors(linked_file.read_text(encoding="utf-8"))
            else:
                linked_file = markdown_file
                linked_anchors = anchors
            if anchor:
                assert anchor in linked_anchors, (
                    f"{markdown_file}: missing anchor {target} in {linked_file}"
                )


def _markdown_links(text: str) -> list[str]:
    raw_links = re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", text)
    return [link.split()[0].strip("<>") for link in raw_links]


def _subcommand_parsers(parser: object) -> set[str]:
    for action in parser._actions:  # type: ignore[attr-defined]
        choices = getattr(action, "choices", None)
        if choices:
            return set(choices)
    raise AssertionError("No argparse subcommands found")


def _is_external_or_generated_link(target: str) -> bool:
    return target.startswith(("http://", "https://", "mailto:")) or target.endswith(
        (".png", ".jpg", ".jpeg", ".gif", ".svg")
    )


def _anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        heading = re.sub(r"`([^`]+)`", r"\1", match.group(2)).lower()
        heading = re.sub(r"[^a-z0-9 -]", "", heading)
        anchors.add(re.sub(r"\s+", "-", heading.strip()))
    return anchors
