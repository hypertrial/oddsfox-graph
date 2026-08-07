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

- Logical layer guide documenting proposition predicates, compile-time
  structural edges, the WC2026 rule registry, build flags, and on-demand
  `IMPLIES` closure.
- Integrator DuckDB query recipes for `REFERS_TO` bridges, confidence
  filters, rejection-reason histograms, and transitive closure.
- `rejection_reason` value table in the output-artifacts reference.
- Docs regression test that requires global `-v`/`--verbose` before
  `oddsgraph` subcommands.
- `scripts/benchmark_build.py` for timing proposition compilation, rule
  application, entity resolution, and the full build pipeline.
- CI `lint` job running `ruff check` (E4/E7/E9/F) via the `dev` extra.
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

- Explorer knockout bracket draws non-interactive stage column headers
  (Round of 32 → Final / 3rd) across the canvas.
- Contributor checklist now explicitly asks for docs + changelog updates on
  user-visible behavior changes.
- Public export schema API (`NODE_SCHEMA`, `EDGE_SCHEMA`, `write_parquet`,
  `table_with_schema`) replaces the prior underscore-private helpers used by
  the CLI and explorer.
- Rule-engine candidate indexing uses only `team`/`match` keys (dropping
  tournament-wide `competition`/`group` buckets that forced O(n²) pair scans).
- Shared `has_implies_cycle` / `reject_implies_cycle()` helpers: graph build
  drops all IMPLIES on cycle; the post-rule pipeline merge still drops only
  rule-engine IMPLIES so fragment edges are preserved.
- Entity resolution caches `normalize_label` and skips no-op evidence
  `model_copy`s on finalize.
- Explorer callbacks split into `canvas_actions.py` / `inspector.py` with
  stable re-exports from `callbacks.py`.
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

- Known-limitations wording for match-result `EXACTLY_ONE` (draw market
  required) aligned with ontology / compiler behavior.
- Troubleshooting recipes for empty schema-typed `semantic_markets.parquet`
  and explorer market-bridge diagnostics.
- Match-result `EXACTLY_ONE` only emits when a draw market is present in the
  partition (team-only moneylines no longer claim exclusivity).
- Distinct dateful MATCH ids with the same display label no longer collapse
  during entity resolution.
- Scoped `run`/`build` market lists ignore on-disk fragments for other events.
- Failed `--verify-deterministic` deletes stale `__verified.json` artifacts;
  load requires a usable verify manifest.
- Stage monotonicity rules skip unknown stage ranks (`-1`).
- Empty `reduce` writes a typed zero-row semantic-markets parquet.
- Invalid `--llm-backend` values error instead of silently selecting inprocess.
- Explicit `--data-dir` (including the default path) is exclusive of repo-root
  fallback.
- Fragment edges with missing endpoints are rejected as `missing_endpoint`.
- Negative `--limit-events` is rejected.
- Event IDs reject surrounding whitespace and embedded `__` (artifact-suffix
  collisions).
- Fine-tune export removes stale `valid.jsonl` when no validation split is
  written.
- Docs place global `-v` before subcommands.
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
