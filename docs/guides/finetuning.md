# Fine-tuning

Experimental scripts under `scripts/` support residual-path fine-tuning for
events that still go through the LLM after deterministic topology.

## Scripts

| Script | Role |
| --- | --- |
| `scripts/export_finetune_dataset.py` | Export training examples from residual fragments |
| `scripts/finetune_lora.py` | LoRA fine-tune against a base model |
| `scripts/eval_finetuned_model.py` | Evaluate a fine-tuned checkpoint |

## Typical flow

```bash
uv run python scripts/export_finetune_dataset.py
uv run python scripts/finetune_lora.py
uv run python scripts/eval_finetuned_model.py
```

Exact flags and paths evolve with the residual pipeline; prefer `--help` on each
script and keep fine-tuned weights out of git.

## See also

- [Deterministic topology](deterministic-topology.md)
- [Contributors](../audiences/contributors.md)
- [Development](../development/index.md)
