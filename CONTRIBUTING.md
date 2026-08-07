# Contributing

Thanks for looking at OddsFox Graph. This file is a short pointer; the full
contributor workflow lives in the docs site.

Start with [Contributors](https://graph.oddsfox.io/audiences/contributors/)
and [Development guide](https://graph.oddsfox.io/development/) (or, from a
local checkout: `docs/audiences/contributors.md` and
`docs/development/index.md`).

## Before opening a PR

```bash
uv sync --frozen --extra dev
uv run ruff check .
uv run pytest

uv sync --extra docs
uv run mkdocs build --strict
uv run pytest tests/docs -q
```

- Prefer deterministic topology coverage over new residual LLM work when
  adding topology (see
  [Deterministic topology](https://graph.oddsfox.io/guides/deterministic-topology/)).
- Keep ontology changes paired with validation and docs updates
  (`docs/reference/ontology.md`, `oddsgraph/ontology.py`).
- Do not commit large parquet exports or model weights — both are
  git-ignored on purpose.
- Add a `CHANGELOG.md` entry under `[Unreleased]` for user-visible changes
  (new CLI flags, changed defaults, output schema changes).
- If you add or move a page under `docs/`, add a matching entry to
  `mkdocs.yml`'s `nav` — `tests/docs/test_docs_structure.py` enforces this.

## Reporting issues

Open a GitHub issue with repro steps, the command you ran, and relevant
output from `inference_report.json` if applicable. This is local, MIT-licensed
software with no hosted service — see
[Known limitations](https://graph.oddsfox.io/concepts/limitations/) before
filing scope-related issues.
