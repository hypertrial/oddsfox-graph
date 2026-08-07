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

### Added

- Formal proposition layer: deterministic compiler attaches `Proposition`
  payloads to covered `OUTCOME` nodes and emits `REFERS_TO`, `PRICES`,
  `COMPLEMENT`, and `EXACTLY_ONE` (+ `CONSTRAINT` nodes).
- Deterministic rule engine (`IMPLIES` / `EQUIVALENT` / `MUTEX`) with flat
  provenance fields (`derivation_type`, `rule_id`, `rule_version`, `premises`).
- `oddsgraph build --propositions/--no-propositions` and
  `--reasoning/--no-reasoning` (both default on).
- On-demand `oddsgraph closure` writing `build/implies_closure.parquet`.
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

### Changed

- Ontology adds `CONSTRAINT`, `REFERS_TO`, `EQUIVALENT`, `COMPLEMENT`,
  `MUTEX`, and `EXACTLY_ONE`.
- Exported `nodes.parquet` includes `proposition_json`; `edges.parquet`
  includes derivation provenance columns.
- Docs updated for the topology↔market bridge (no longer universally
  disconnected; residual market types may still lack propositions).
- Empty parquet exports use explicit Arrow schemas so list columns stay
  `list[string]` (explorer search/UNNEST no longer BinderErrors on empty
  graphs).

### Fixed

- `wc.champion_reaches_final` no longer treats Third Place as Final (rank
  collision); implication requires the Final stage slug.
- `soccer_team_to_advance` markets now emit `CONSTRAINT` + `EXACTLY_ONE`
  as documented for covered templates.
- Explorer missing-parquet stubs match the export schema; `graph_counts`
  falls back to parquet when `inference_report` histograms are empty.
- `oddsgraph closure` writes via the edge Arrow schema (was still passing
  the removed row-template API).
- Troubleshooting / explorer expand copy no longer claims topology and
  market layers are universally disconnected.

### Documentation

- Introduce OddsFox Graph as a **Logical Knowledge Graph Compiler** (README,
  homepage, package/CLI description, site meta).
- Restructure Architecture around compiler phases (lexer / parser / constant
  folding / semantic analysis / linker / type check / codegen) with a phase
  diagram that separates residual LLM and official-bracket paths.
- Add Concepts glossary for core terms (fragment, residual event, resolution
  tier, inference method, and related).
- De-duplicate `README.md` against the docs site; migrate MLX and outlines
  content into a new [Inference backends](docs/guides/inference-backends.md)
  guide.
- Add per-page SEO `description:` front matter across all MkDocs pages.
- Vendor `mermaid@11.15.0` locally under `docs/assets/javascripts/` (no CDN).
- Add a scheduled/non-blocking lychee external link-checker workflow.
