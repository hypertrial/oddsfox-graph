# Contributors

Use this hub when you change OddsFox Graph code, tests, or docs. See
[CONTRIBUTING.md](https://github.com/hypertrial/oddsfox-graph/blob/main/CONTRIBUTING.md)
for the PR checklist and
[CHANGELOG.md](https://github.com/hypertrial/oddsfox-graph/blob/main/CHANGELOG.md)
for notable changes.

<span class="of-persona of-persona--contributor">Contributor</span>

## Repository layout

| Path | Role |
| --- | --- |
| `oddsgraph/` | Package: CLI, reduce, infer, resolution, build, export, explorer |
| `scripts/` | Operator/dev utilities (schedule export, fine-tune, benchmarks) |
| `tests/` | Unit and optional live integration tests |
| `docs/` | This documentation site |
| `models/` | Local GGUF / MLX weight instructions |

## Run tests

```bash
uv sync --frozen --extra dev
uv run pytest
```

Optional live model integration:

```bash
ODDSGRAPH_LIVE_MODEL_TEST=1 uv run pytest -m integration
```

Optional live server integration (requires running `llama-server`):

```bash
ODDSGRAPH_LIVE_SERVER_TEST=1 uv run pytest -m integration
```

## Docs workflow

```bash
uv sync --extra docs
uv run mkdocs serve -a 127.0.0.1:8000
uv run mkdocs build --strict
uv run pytest tests/docs -q
```

## Fine-tuning scripts

Experimental residual-path fine-tuning lives under `scripts/`:

- `export_finetune_dataset.py`
- `finetune_lora.py`
- `eval_finetuned_model.py`

See [Fine-tuning](../guides/finetuning.md).

## Next pages

| Goal | Page |
| --- | --- |
| Architecture | [Architecture](../concepts/architecture.md) |
| Development guide | [Development](../development/index.md) |
| Ontology contract | [Ontology](../reference/ontology.md) |
| CLI surface | [CLI](../reference/cli.md) |

## See also

- [Development](../development/index.md)
- [Entity resolution](../concepts/entity-resolution.md)
- [Fine-tuning](../guides/finetuning.md)
