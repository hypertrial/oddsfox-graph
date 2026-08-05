import shutil
from pathlib import Path

from typer.testing import CliRunner

from oddsfox_graph.cli import app
from oddsfox_graph.config import Settings

from tests.helpers import GOLDEN_MARKETS_PATH, load_fixture_fragment

runner = CliRunner()


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "reduce" in result.output
    assert "infer" in result.output
    assert "build" in result.output
    assert "validate" in result.output
    assert "run" in result.output


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
