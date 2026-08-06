#!/usr/bin/env python3
"""Evaluate a fine-tuned (or base) MLX model on a held-out fragment split.

Scores node/edge exact-match F1 against teacher CompactGraphFragment labels.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from oddsgraph.schema import CompactGraphFragment, GraphFragment


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _prompt_and_label(row: dict) -> tuple[str, CompactGraphFragment]:
    if "messages" in row:
        prompt = row["messages"][0]["content"]
        completion = row["messages"][1]["content"]
    else:
        text = row["text"]
        # Split on last newline before compact JSON object.
        idx = text.rfind("\n{")
        if idx < 0:
            raise ValueError("Cannot split text row into prompt/completion")
        prompt = text[:idx]
        completion = text[idx + 1 :]
    return prompt, CompactGraphFragment.model_validate_json(completion)


def _f1(pred: set, gold: set) -> float:
    if not pred and not gold:
        return 1.0
    if not pred or not gold:
        return 0.0
    tp = len(pred & gold)
    precision = tp / len(pred)
    recall = tp / len(gold)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _sets(fragment: GraphFragment) -> tuple[set, set]:
    nodes = {(n.local_id, n.type.value, n.label) for n in fragment.nodes}
    edges = {(e.source, e.target, e.type.value) for e in fragment.edges}
    return nodes, edges


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--valid",
        type=Path,
        default=Path("build/finetune/valid.jsonl"),
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("models/qwen3-4b-mlx"),
    )
    parser.add_argument(
        "--adapter-path",
        type=Path,
        default=None,
        help="Optional LoRA adapter directory",
    )
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--offline-score-only",
        action="store_true",
        help="Skip model calls; score teacher labels against themselves (smoke)",
    )
    args = parser.parse_args()

    if not args.valid.exists():
        raise SystemExit(f"Validation split not found: {args.valid}")

    rows = _load_jsonl(args.valid)
    if args.limit is not None:
        rows = rows[: args.limit]

    generator = None
    if not args.offline_score_only:
        if sys.platform != "darwin":
            raise SystemExit("Live eval requires Apple Silicon + mlx extra")
        try:
            import mlx_lm
            import outlines
        except ImportError as exc:
            raise SystemExit(
                "Install mlx extra: uv sync --frozen --extra mlx"
            ) from exc
        model, tokenizer = mlx_lm.load(
            str(args.model),
            adapter_path=str(args.adapter_path) if args.adapter_path else None,
        )
        outlines_model = outlines.from_mlxlm(model, tokenizer)

        def generator(prompt: str) -> CompactGraphFragment:
            result = outlines_model(
                prompt,
                CompactGraphFragment,
                max_tokens=args.max_tokens,
            )
            if isinstance(result, CompactGraphFragment):
                return result
            if isinstance(result, str):
                return CompactGraphFragment.model_validate_json(result)
            return CompactGraphFragment.model_validate(result)

    node_scores: list[float] = []
    edge_scores: list[float] = []
    for row in rows:
        prompt, gold_compact = _prompt_and_label(row)
        gold = gold_compact.to_graph_fragment()
        if args.offline_score_only:
            pred = gold
        else:
            assert generator is not None
            pred = generator(prompt).to_graph_fragment()
        gold_nodes, gold_edges = _sets(gold)
        pred_nodes, pred_edges = _sets(pred)
        node_scores.append(_f1(pred_nodes, gold_nodes))
        edge_scores.append(_f1(pred_edges, gold_edges))

    report = {
        "examples": len(rows),
        "node_f1": round(sum(node_scores) / len(node_scores), 4) if node_scores else 0.0,
        "edge_f1": round(sum(edge_scores) / len(edge_scores), 4) if edge_scores else 0.0,
        "offline_score_only": args.offline_score_only,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
