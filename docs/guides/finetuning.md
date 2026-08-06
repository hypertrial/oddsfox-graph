---
description: Experimental LoRA fine-tuning scripts for residual LLM events using deterministic and verified fragments.
---

# Fine-tuning

Experimental scripts under `scripts/` support residual-path fine-tuning for
events that still go through the LLM after deterministic topology. Use
deterministic or verified fragments as teacher labels. These scripts are not
part of the stable CLI surface — see
[Known limitations](../concepts/limitations.md).

## Scripts

| Script | Role |
| --- | --- |
| `scripts/export_finetune_dataset.py` | Export chat JSONL from fragments + markets |
| `scripts/finetune_lora.py` | LoRA fine-tune against an MLX base model |
| `scripts/eval_finetuned_model.py` | Evaluate node/edge F1 on a held-out split |

## Typical flow

```bash
# 1) Export chat JSONL from fragments + markets
uv run python scripts/export_finetune_dataset.py \
  --markets build/semantic_markets.parquet \
  --fragments-dir build/fragments \
  --output-dir build/finetune
# --val-ratio must be in [0, 1); default 0.1

# 2) Train LoRA adapter (Apple Silicon + --extra mlx)
uv run python scripts/finetune_lora.py \
  --model models/qwen3-4b-mlx \
  --data build/finetune \
  --iters 200

# 3) Evaluate node/edge F1 on the held-out split
uv run python scripts/eval_finetuned_model.py \
  --model models/qwen3-4b-mlx \
  --adapter-path build/finetune/adapters \
  --valid build/finetune/valid.jsonl
```

`export_finetune_dataset.py` rejects `--val-ratio` values of `1.0` or higher,
and the split always keeps at least one training row so `train.jsonl` and
`valid.jsonl` never become identical.

Point `--mlx-model-path` at a fused/adapted model once eval F1 looks good.
Exact flags may evolve with the residual pipeline; prefer `--help` on each
script and keep fine-tuned weights out of git.

## See also

- [Inference backends](inference-backends.md)
- [Deterministic topology](deterministic-topology.md)
- [Contributors](../audiences/contributors.md)
- [Development](../development/index.md)
