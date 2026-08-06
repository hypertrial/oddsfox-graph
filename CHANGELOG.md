# Changelog

All notable user-facing changes to OddsFox Graph are documented here. Format
loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project does not yet follow semantic versioning strictly; treat CLI
flags and output schema as pre-1.0 and subject to change.

> **About pre-rebuild tags:** Git tags `v0.1.0`–`v0.5.0` (and up through
> `v0.13.0`) predate the WC2026 hourly-odds rebuild (`Reset repository to a
> minimal skeleton for the WC2026 hourly-odds rebuild`) and belong to an
> earlier, unrelated discovery-graph implementation. `pyproject.toml`'s
> `version = "0.1.0"` was reset at that point and has not yet been tagged
> under the current `oddsgraph` CLI. Do not treat those old tags as history
> for this CLI.

## [Unreleased]

Add entries here for user-visible changes (new/changed CLI flags, changed
defaults, output schema changes, new docs pages) as part of the PR that
introduces them.

### Added

- `oddsgraph` CLI (`reduce`, `infer`, `build`, `validate`, `explore`, `run`)
  over Polymarket WC2026 hourly-odds parquet.
- Deterministic topology extraction for match/group/stage/tournament
  templates, covering ~91% of WC2026 events without an LLM call.
- Optional LLM verification tier (`--verify-deterministic`) that
  confirms/patches deterministic topology.
- Official WC2026 knockout bracket injection (`--official-bracket`, default
  on) from a curated FIFA schedule snapshot.
- Local structured LLM extraction for residual events, with `inprocess`
  (llama.cpp + outlines), `server` (`llama-server`), and `mlx` (Apple Silicon
  MLX) backends.
- Few-shot exemplar retrieval (`--few-shot`) for residual LLM prompts.
- Entity resolution with tiered exact/alias/fuzzy matching and confidence
  filtering (`--minimum-confidence`).
- Local Dash + Cytoscape explorer (`oddsgraph explore`) with a knockout-
  bracket default view and a full-topology view.
- LoRA fine-tuning / self-distillation scripts (`scripts/export_finetune_dataset.py`,
  `scripts/finetune_lora.py`, `scripts/eval_finetuned_model.py`) for the
  residual LLM path.
- MkDocs documentation site with persona-based navigation (Analysts,
  Operators, Contributors, Integrators).
