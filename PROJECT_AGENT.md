# Project agent notes

Public Python graph/docs package (`oddsgraph`) using uv, pytest, ruff, and MkDocs.

## Verification

- Fast: `uv run pytest`
- Completion: `uv run ruff check .` then `uv run pytest`

Docs PRs also need `uv run mkdocs build --strict`. Follow `CONTRIBUTING.md`.

## Invariants

Public repository. Keep private OddsFox trading work out of this workspace and git.
