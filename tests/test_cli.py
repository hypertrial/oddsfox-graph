import re
import shutil
from pathlib import Path

from typer.testing import CliRunner

from oddsgraph.cli import app
from oddsgraph.config import Settings

from tests.helpers import GOLDEN_MARKETS_PATH, load_fixture_fragment

runner = CliRunner()


def _plain_output(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return re.sub(r"\s+", " ", text)


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    output = _plain_output(result.output)
    assert "Usage: oddsgraph" in output
    assert "reduce" in output
    assert "infer" in output
    assert "build" in output
    assert "validate" in output
    assert "run" in output
    assert "explore" in output
    assert "closure" in output


def test_cli_explore_help() -> None:
    result = runner.invoke(app, ["explore", "--help"])
    assert result.exit_code == 0
    output = _plain_output(result.output)
    assert "--host" in output
    assert "--port" in output
    assert "--debug" in output


def test_cli_explore_missing_artifacts(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    result = runner.invoke(app, ["--build-dir", str(build_dir), "explore"])
    assert result.exit_code == 1
    assert "No exported graph found" in result.output

    help_result = runner.invoke(app, ["infer", "--help"])
    assert help_result.exit_code == 0
    output = _plain_output(help_result.output)
    assert "--llm-backend" in output
    assert "--server-url" in output
    assert "--concurrency" in output
    # Rich may truncate long dual flags in narrow terminals (ellipsis \u2026).
    assert "deterministic-" in output
    assert "no-determinis" in output


def test_cli_run_help_includes_infer_and_build_options() -> None:
    help_result = runner.invoke(app, ["run", "--help"])
    assert help_result.exit_code == 0
    output = _plain_output(help_result.output)
    assert "odds-history" in output
    assert "reduce" in output and "infer" in output and "build" in output
    assert "--llm-backend" in output
    assert "--concurrency" in output
    assert "official-brack" in output
    assert "minimum-confid" in output
    assert "verify-determi" in output
    assert "few-shot" in output
    assert "mlx" in output.lower() or "--mlx-model-path" in output
    assert "propositions" in output
    assert "reasoning" in output


def test_cli_root_help_run_mentions_odds_history() -> None:
    root = runner.invoke(app, ["--help"])
    assert root.exit_code == 0
    root_out = _plain_output(root.output)
    assert "odds-history" in root_out

    run_help = runner.invoke(app, ["run", "--help"])
    assert run_help.exit_code == 0
    run_out = _plain_output(run_help.output)
    assert "odds-history" in run_out
    # Docstring stage list, not only the standalone command name elsewhere.
    assert "build" in run_out and "validate" in run_out


def test_cli_explore_accepts_build_dir_after_subcommand(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    result = runner.invoke(app, ["explore", "--build-dir", str(build_dir)])
    assert result.exit_code == 1
    assert "No exported graph found" in result.output
    assert "No such option" not in result.output


def test_cli_explore_help_includes_build_dir() -> None:
    result = runner.invoke(app, ["explore", "--help"])
    assert result.exit_code == 0
    output = _plain_output(result.output)
    assert "--build-dir" in output
    assert "--host" in output
    assert "--port" in output
    assert "--debug" in output


def test_cli_build_help_includes_proposition_flags() -> None:
    help_result = runner.invoke(app, ["build", "--help"])
    assert help_result.exit_code == 0
    output = _plain_output(help_result.output)
    assert "propositions" in output
    assert "reasoning" in output


def test_cli_build_and_validate_with_fixture(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    settings = Settings()
    settings.configure_build_dir(build_dir)
    settings.ensure_dirs()
    shutil.copy(GOLDEN_MARKETS_PATH, settings.semantic_markets_path)

    inferred = load_fixture_fragment("351746")
    fragment_path = settings.fragments_dir / "351746.json"
    fragment_path.write_text(inferred.model_dump_json(indent=2), encoding="utf-8")

    build_result = runner.invoke(
        app,
        ["--build-dir", str(build_dir), "build"],
    )
    assert build_result.exit_code == 0
    assert "Exported" in build_result.output

    validate_result = runner.invoke(
        app,
        ["--build-dir", str(build_dir), "validate"],
    )
    assert validate_result.exit_code == 0
    assert "Validation PASSED" in validate_result.output


def test_cli_closure_writes_empty_parquet(tmp_path: Path) -> None:
    from oddsgraph.export import EDGE_SCHEMA, write_parquet

    build_dir = tmp_path / "build"
    settings = Settings()
    settings.configure_build_dir(build_dir)
    settings.ensure_dirs()
    write_parquet(settings.edges_path, [], EDGE_SCHEMA)

    result = runner.invoke(app, ["--build-dir", str(build_dir), "closure"])
    assert result.exit_code == 0
    assert settings.implies_closure_path.exists()
    assert "transitive IMPLIES" in result.output
