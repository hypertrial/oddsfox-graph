---
description: Develop OddsFox Graph with uv extras, pytest, optional live model tests, and strict MkDocs builds.
---

# Development guide

## Setup

```bash
uv sync --frozen --extra dev
```

Optional extras:

```bash
uv sync --frozen --extra explore
uv sync --frozen --extra mlx
uv sync --extra docs
```

## Tests

```bash
uv run pytest
```

Lint (also run in CI):

```bash
uv run ruff check .
```

Live model integration (optional):

```bash
ODDSGRAPH_LIVE_MODEL_TEST=1 uv run pytest -m integration
```

Live server integration (optional, requires running `llama-server`):

```bash
ODDSGRAPH_LIVE_SERVER_TEST=1 uv run pytest -m integration
```

Docs structure and strict build:

```bash
uv run mkdocs build --strict
uv run pytest tests/docs -q
```

## Benchmarks

```bash
uv run python scripts/benchmark_build.py \
  --markets tests/fixtures/golden_semantic_markets.parquet
uv run python scripts/benchmark_infer.py --help
```

`benchmark_build.py` times proposition compilation, rule application, entity
resolution, and the full build pipeline. Residual LLM decode timing lives in
[Inference backends](../guides/inference-backends.md).

## Quality expectations

- Prefer deterministic coverage over residual LLM work when adding topology.
- Keep ontology changes paired with validation and docs updates.
- Do not commit large parquet exports or model weights.

## See also

- [Contributors](../audiences/contributors.md)
- [Architecture](../concepts/architecture.md)
- [Fine-tuning](../guides/finetuning.md)
