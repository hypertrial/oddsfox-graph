from __future__ import annotations

from pathlib import Path

import pytest

from oddsfox_graph.cli import main


def test_cli_smoke(synthetic_input: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"

    assert main(["build", "--input", str(synthetic_input), "--out", str(out)]) == 0
    assert main(["search", "--out", str(out), "--query", "Equivalent A"]) == 0
    assert "Will Equivalent A happen?" in capsys.readouterr().out
    assert main(["nodes", "--out", str(out), "--top", "5"]) == 0
    assert main(["edges", "--out", str(out), "--edge-type", "complement", "--top", "5"]) == 0
    assert main(["condition", "--out", str(out), "--a", "comp:Yes", "--b", "comp:No"]) == 0
    assert "exact_complement" in capsys.readouterr().out
    assert main(["condition", "--out", str(out), "--a", "NOT(Will Complement pass?)", "--b", "comp:Yes"]) == 0
    assert "exact_complement" in capsys.readouterr().out
    assert main(["condition", "--out", str(out), "--a", "Alpha", "--b", "comp:Yes"]) == 1
    captured = capsys.readouterr()
    assert "Ambiguous node query" in captured.err
    assert "Candidates:" in captured.err
    assert main(["explain", "--out", str(out), "--node", "comp:Yes"]) == 0
    assert "Logic Edges" in capsys.readouterr().out
    assert main(["benchmark-summary", "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert "runtime_seconds:" in captured.out
    assert "top_stage_timings:" in captured.out


def test_cli_rejects_invalid_edge_type(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["edges", "--out", str(tmp_path), "--edge-type", "bad"])
    assert exc.value.code == 2

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "explain-edge",
                "--out",
                str(tmp_path),
                "--src",
                "a",
                "--dst",
                "b",
                "--edge-type",
                "bad",
            ]
        )
    assert exc.value.code == 2


def test_cli_explain_edge(synthetic_output: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(
            [
                "explain-edge",
                "--out",
                str(synthetic_output),
                "--src",
                "comp:Yes",
                "--dst",
                "comp:No",
                "--edge-type",
                "complement",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "Logic Edge" in out
    assert "Conditionals" in out
