from typer.testing import CliRunner

from oddsfox_graph.cli import app

runner = CliRunner()


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "reduce" in result.output
    assert "infer" in result.output
    assert "build" in result.output
    assert "validate" in result.output
    assert "run" in result.output
