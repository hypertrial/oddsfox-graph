#!/usr/bin/env python3
"""Thin wrapper around mlx_lm.lora for self-distillation fine-tuning.

Requires the mlx extra on Apple Silicon:

  uv sync --frozen --extra mlx
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("models/qwen3-4b-mlx"),
        help="Base MLX model directory",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("build/finetune"),
        help="Directory containing train.jsonl / valid.jsonl",
    )
    parser.add_argument(
        "--adapter-path",
        type=Path,
        default=Path("build/finetune/adapters"),
    )
    parser.add_argument("--iters", type=int, default=200)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--num-layers", type=int, default=8)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the command without executing",
    )
    args = parser.parse_args()

    if sys.platform != "darwin":
        raise SystemExit("finetune_lora.py is intended for Apple Silicon (darwin) only")

    if not args.model.exists() and not args.dry_run:
        raise SystemExit(f"MLX model not found: {args.model}")
    train = args.data / "train.jsonl"
    if not train.exists() and not args.dry_run:
        raise SystemExit(
            f"Missing {train}. Run scripts/export_finetune_dataset.py first."
        )

    args.adapter_path.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "mlx_lm.lora",
        "--model",
        str(args.model),
        "--train",
        "--data",
        str(args.data),
        "--adapter-path",
        str(args.adapter_path),
        "--batch-size",
        str(args.batch_size),
        "--iters",
        str(args.iters),
        "--learning-rate",
        str(args.learning_rate),
        "--num-layers",
        str(args.num_layers),
    ]
    print(" ".join(cmd))
    if args.dry_run:
        return

    try:
        import mlx_lm  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "mlx_lm is not installed. Run: uv sync --frozen --extra mlx"
        ) from exc

    if not args.model.exists():
        raise SystemExit(f"MLX model not found: {args.model}")
    if not train.exists():
        raise SystemExit(
            f"Missing {train}. Run scripts/export_finetune_dataset.py first."
        )

    # Prefer python -m; fall back to mlx_lm.lora console script if present.
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        which = shutil.which("mlx_lm.lora")
        if which:
            cmd[0:3] = [which]
            subprocess.run(cmd, check=True)
        else:
            raise


if __name__ == "__main__":
    main()
