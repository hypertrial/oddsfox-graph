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

- Explorer dark sports-data shell: branded header, persistent phase tracker,
  bottom playback dock, and on-demand Filters & legend drawer.
- Schedule-derived tournament phase model with active, intermission, Final
  weekend, and complete states; compact UTC timestamps (`Jun 28 · 19:00 UTC`)
  plus full tooltip/ISO forms.
- Explorer playback bounds extend through Final full-time so Champion lock is
  reachable on the slider.
- `stage_odds_history.parquet` from `oddsgraph odds-history` / `run`: hourly
  team stage-reach and World Cup Winner probabilities for explorer projection.
- Explorer projects unresolved future matchups from feeder-branch leaders,
  shows numeric advance probabilities on every card, and renders local SVG
  country flags on both sides of each match node.
- Explorer Final / Third Place cards lock winners to **100%** / **0%** at full
  time (Spain and England in the curated WC2026 schedule) with the same teal
  resolved styling as other locked matches (thicker border on Final / 3rd).
- Explorer playback highlights matches that just finished: when the scrubbed
  hour equals a match’s full-time milestone, the card shows a **Just finished**
  badge and pulse (simultaneous fixtures share the highlight).

### Changed

- Explorer UI is a mirrored HTML/CSS knockout tree (website-style cards and SVG
  connectors) instead of Dash Cytoscape; Inspector / path-highlight / hide-match
  interactions were removed with that migration.
- Explorer match cards use a wider dark layout with aligned probability
  columns, row-aligned flags, and taxi connectors without arrowheads.
- Explorer Controls moved into a closed-by-default utility drawer; playback
  lives in a reserved bottom dock (not an overlay) instead of a persistent left
  sidebar. Action status stays in the dock.
- Explorer match-card tint encodes resolution, not favorite: teal fill means
  the match is resolved at the selected tournament hour; unfinished cards
  rely on numeric percentages and dashed borders when projected.
- Explorer time slider spans official tournament kickoffs through Final
  full-time, not the full early Champion-market odds-history window.
- Explorer Play / time scrubbing advances by schedule kickoff and full-time
  milestones (~184 steps end-to-end) instead of one hour at a time;
  simultaneous fixtures share a step. Manual scrubbing snaps to the same
  milestones.

### Fixed

- Explorer playback dock no longer covers bottom bracket cards on short /
  non-maximized viewports: the dock is a normal-flow flex sibling below the
  scrollable canvas instead of an absolute overlay inside it.
- Explorer Final / Third Place locked cards use the same teal resolved tint as
  other finished matches (gold/silver champion overrides removed).
- Explorer attaches curated schedule winners by team pair when MATCH ids
  drift by kickoff date, so completed knockout cards resolve to teal at
  tournament end instead of staying projected.
- Explorer Dash 4 sliders no longer show white direct-entry number fields or
  white value tooltips in the playback dock and Filters drawer (set
  ``allow_direct_input=False`` and restyle ``.dash-slider-*`` for the dark shell).
- Explorer match-card flag SVGs use node-relative sizes so they scale with
  zoom instead of shrinking when zooming in.
- Flag SVG assets declare explicit ``width`` / ``height`` for consistent
  rendering in match cards.
- Explorer projection maps feeder branches onto schedule home/away slots
  (not alphabetical feeder labels) and ranks Third Place candidates by
  `P(reach Final)`.
- Missing flag assets no longer shift the opposite flag into the wrong slot.
- Odds-history no longer treats the last observed hour of a live
  `soccer_team_to_advance` series as match end / winner lock.
- Explorer time scrub returns no probability before the first odds point
  (no look-ahead to future opening odds).
- Entity resolution coalesces Polymarket slug dates with FIFA kickoff dates
  within ±1 day for the same MATCH team pair, and stamps merged MATCH nodes
  `inference_method=official_bracket` when the schedule fragment binds.
- `reduce` normalizes real `timestamp` `game_start_time` / `end_time` values
  instead of crashing with ArrowTypeError on schema-faithful source parquet.
- Match-result `EXACTLY_ONE` requires both team moneylines and the draw market
  (draw + a single team moneyline no longer emits a false partition).
- Explorer projection falls back to feeder advance odds when stage-reach
  markets are missing, instead of inventing schedule-home favorites.
- Odds-history / stage-odds-history dedupe repeated `(match,hour)` /
  `(team,stage,hour)` rows from multi-file input globs.
- `oddsgraph run --help` and architecture docs include the `odds-history`
  stage that `run` already executes.
- `oddsgraph explore --build-dir` is accepted (in addition to the global
  prefix form).
- `load_all_fragments` honors `--no-resume` and skips `__verified.json`.
- Residual `--resume` re-infers when market membership changes (chunk
  manifest), not only when chunk budget settings change.

### Added

- `oddsgraph odds-history` builds `build/odds_history.parquet`: hourly knockout
  win probabilities from Polymarket `soccer_team_to_advance` markets, with
  winner lock after match end.
- Explorer knockout time slider colors each MATCH by home win-probability
  (green = home favored, red = away) and locks winners to 1 after end.
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
- Local Dash + Cytoscape explorer (`oddsgraph explore`) with a temporal
  knockout-bracket view.
- LoRA fine-tuning / self-distillation scripts (`scripts/export_finetune_dataset.py`,
  `scripts/finetune_lora.py`, `scripts/eval_finetuned_model.py`) for the
  residual LLM path.
- MkDocs documentation site with persona-based navigation (Analysts,
  Operators, Contributors, Integrators).

### Changed

- Explorer is bracket-only: removed Full topology view, search, expand
  neighbors, type filter, and layout chooser.
- Controls and Inspector sidebars are collapsible on all viewport widths.
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
