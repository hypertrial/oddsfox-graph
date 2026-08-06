"""Smoke tests for finetune/benchmark helper scripts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_export_finetune_dataset_from_fixtures(tmp_path: Path) -> None:
    out = tmp_path / "finetune"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/export_finetune_dataset.py",
            "--from-fixtures",
            "--output-dir",
            str(out),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (out / "train.jsonl").exists()
    meta = json.loads((out / "dataset_meta.json").read_text(encoding="utf-8"))
    assert meta["train_rows"] >= 1


def test_eval_finetuned_offline_score(tmp_path: Path) -> None:
    # Build a tiny valid.jsonl then score offline.
    valid = tmp_path / "valid.jsonl"
    valid.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "prompt"},
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "n": [
                                    {
                                        "id": "team:brazil",
                                        "t": "TEAM",
                                        "l": "Brazil",
                                        "a": [],
                                        "c": 0.9,
                                        "e": ["m1"],
                                    }
                                ],
                                "g": [],
                            }
                        ),
                    },
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/eval_finetuned_model.py",
            "--valid",
            str(valid),
            "--offline-score-only",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["node_f1"] == 1.0


def test_benchmark_and_finetune_help() -> None:
    root = Path(__file__).resolve().parents[1]
    for script in [
        "scripts/benchmark_infer.py",
        "scripts/finetune_lora.py",
        "scripts/export_finetune_dataset.py",
        "scripts/eval_finetuned_model.py",
    ]:
        result = subprocess.run(
            [sys.executable, script, "--help"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "usage" in result.stdout.lower() or "Usage" in result.stdout
