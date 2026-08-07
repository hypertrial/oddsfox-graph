---
description: OddsFox Graph Settings defaults from oddsgraph/config.py for paths, LLM backends, and pipeline behavior.
---

# Configuration

Runtime settings live in the `Settings` dataclass in `oddsgraph/config.py`.
CLI flags override selected fields at runtime; the table below is the code
default source of truth.

## Paths

| Field | Default | Purpose |
| --- | --- | --- |
| `repo_root` | package parent | Repository root |
| `data_dir` | `<repo>/data` | Source parquet directory |
| `build_dir` | `<repo>/build` | Build artifacts |
| `models_dir` | `<repo>/models` | Local model weights |
| `fragments_dir` | `build/fragments` | Per-event fragment JSON |
| `failed_fragments_dir` | `build/fragments/_failed` | Failed fragment dumps |
| `input_glob` | `data/*market_hourly_odds*.parquet` | Preferred input glob |
| `fallback_glob` | `polymarket_wc2026_market_hourly_odds_*.parquet` | Fallback input glob |
| `semantic_markets_path` | `build/semantic_markets.parquet` | Reduced markets |
| `nodes_path` | `build/nodes.parquet` | Exported nodes |
| `edges_path` | `build/edges.parquet` | Exported edges |
| `rejected_edges_path` | `build/rejected_edges.parquet` | Rejected edges |
| `ontology_path` | `build/ontology.json` | Ontology dump |
| `inference_report_path` | `build/inference_report.json` | Inference report |
| `implies_closure_path` | `build/implies_closure.parquet` | On-demand transitive IMPLIES |

## Model / LLM

| Field | Default | Purpose |
| --- | --- | --- |
| `model_path` | `models/qwen3-4b-q4_k_m.gguf` | GGUF path |
| `mlx_model_path` | `models/qwen3-4b-mlx` | MLX model directory |
| `n_ctx` | `8192` | Model context window |
| `n_gpu_layers` | `-1` | GPU layers (`-1` = all) |
| `max_tokens` | `4096` | Max generation tokens |
| `temperature` | `0.1` | Sampling temperature |
| `max_retries` | `2` | LLM retries |
| `chunk_token_budget` | `5000` | Max estimated input tokens per chunk |
| `chunk_output_token_budget` | `4096` | Max estimated output tokens per chunk |
| `max_markets_per_chunk` | `24` | Hard cap on markets per chunk |
| `chunk_context_safety_margin` | `64` | Context safety margin |
| `max_text_field_chars` | `500` | Truncate long prompt fields |
| `flash_attn` | `true` | Metal flash attention (in-process) |
| `n_batch` / `n_ubatch` | `1024` | llama.cpp batch sizes |
| `llm_backend` | `inprocess` | `inprocess`, `server`, or `mlx` |
| `server_base_url` | `http://127.0.0.1:8080` | llama-server base URL |
| `server_request_timeout` | `120.0` | HTTP timeout seconds |
| `llm_concurrency` | `2` | Concurrent server requests |

## Pipeline behavior

| Field | Default | Purpose |
| --- | --- | --- |
| `deterministic_topology` | `true` | Skip LLM for template-covered events |
| `official_bracket` | `true` | Inject curated FIFA schedule on build |
| `compile_propositions` | `true` | Compile formal propositions onto OUTCOME nodes |
| `apply_rules` | `true` | Apply deterministic logical rules over propositions |
| `verify_deterministic` | `false` | LLM confirm/patch over deterministic output |
| `verify_max_tokens` | `512` | Max tokens for verify pass |
| `use_few_shot_exemplars` | `true` | Few-shot exemplars in residual prompts |
| `few_shot_top_k` | `2` | Exemplars per residual prompt |
| `competition_label` | `World Cup 2026` | COMPETITION label/slug base |
| `fuzzy_threshold` | `92` | Entity resolution fuzzy match cutoff |
| `minimum_confidence` | `0.0` | Edge confidence floor on build |
| `resume` | `true` | Reuse completed fragments |
| `limit_events` | `None` | Optional event cap |
| `event_ids` | `[]` | Optional explicit event ID list |

## See also

- [CLI](cli.md)
- [Running the pipeline](../guides/running-the-pipeline.md)
- [Inference backends](../guides/inference-backends.md)
- [llama-server](../guides/llama-server.md)
