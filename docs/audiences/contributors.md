---
description: Change OddsFox Graph code, tests, or docs with the repository layout, test commands, and PR checklist.
---

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
| `oddsgraph/` | Package: CLI, reduce, infer, resolution, build, export |
| `scripts/` | Operator/dev utilities (schedule export, fine-tune, benchmarks) |
| `tests/` | Unit and optional live integration tests |
| `docs/` | This documentation site |
| `models/` | Local GGUF / MLX weight instructions |

Setup, tests, extras, and docs workflow live in
[Development](../development/index.md). Experimental residual-path LoRA scripts
are covered in [Fine-tuning](../guides/finetuning.md).

## Next pages

| Goal | Page |
| --- | --- |
| Setup / tests / docs | [Development](../development/index.md) |
| Architecture | [Architecture](../concepts/architecture.md) |
| Entity resolution | [Entity resolution](../concepts/entity-resolution.md) |
| Ontology contract | [Ontology](../reference/ontology.md) |
| CLI surface | [CLI](../reference/cli.md) |
| Fine-tuning scripts | [Fine-tuning](../guides/finetuning.md) |
