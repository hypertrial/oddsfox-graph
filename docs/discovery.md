# Discovery Workflow

## Source contract

The input Parquet schema identifier is
`polymarket-market-snapshot-v1`. `market_id`, `question`, `outcomes`, and
`clob_token_ids` are required. `event_id`, `event_slug`, `description`,
`volume`, `start_time`, `end_time`, `category`, and `tags` are optional.
The required scalar columns are strings, `outcomes` and `clob_token_ids` are
string lists, and `tags` is a string list when present. Rows with missing or
mismatched required values are counted and excluded from bounded selection;
the remaining eligible catalog must have unique market IDs, outcomes, and token
IDs.

## Execution

Online discovery requires `--model-manifest` and a conforming llama.cpp or vLLM
Chat Completions endpoint. Non-loopback `--llm-base-url` values require
`--allow-remote-inference`. `--model-profile` enables only calibrated relations;
otherwise positive model proposals enter `review_queue.parquet`.

For the default local model on an M4:

```bash
llama-server \
  --model /models/Qwen3-4B-Q8_0.gguf \
  --alias Qwen/Qwen3-4B-GGUF:Q8_0 \
  --host 127.0.0.1 \
  --port 8080 \
  --ctx-size 8192
```

Create the content-bound manifest with `model-manifest`, then run `model-check`
against the loaded server before discovery. vLLM uses the same local Chat
Completions contract but requires its own runtime-specific manifest and model
profile.

```bash
oddsfox-graph discover \
  --input <catalog.parquet> \
  --out <output-directory> \
  --cache-dir <cache-directory> \
  --model-manifest <model-manifest.json> \
  --model-profile <model-profile.json> \
  --compute-profile <compute-profile.json> \
  --require-ready
```

`--offline` performs no inference calls and requires complete cache and state
coverage. A same-directory replay reuses the published model manifest/profile;
a distinct output must receive those paths explicitly. `--incremental-from`
must name a distinct, manifest-complete v0.8 output. Incompatible cache entries
and baselines are rejected; regenerate them with a clean run.

`--cache-dir` is a dedicated directory containing
`inference-cache-v6.sqlite3`. The cache is transactional, integrity checked,
bulk-read and bulk-written, and opened read-only during offline discovery.
Incompatible caches, profiles, or baselines must be regenerated.

Candidate reasons select and prioritize pairs, but are not sent to the
generative model and cannot serve as supporting-field citations. Model
profiles bind the exact parse and classification request contracts in addition
to prompts, response schemas, settings, weights, and runtime metadata.

Defaults are 5,000 propositions, 400,000 candidates, 5,000 generative
classifications, top 20 embedding neighbors, block size 512, and concurrency 2.
Per-relation CLI thresholds may tighten but never weaken profile thresholds.

## Publication

Artifacts and `state/` are staged, validated, sorted, hashed, and atomically
published. The manifest records `input_schema`, input hash and selection,
models, request contracts, prompts, fingerprints, versions, limits, compute
accounting, solver and rule statistics, transactional cache statistics,
stage-level RSS, storage sizes, hashes, and timings. Its presence is the
completion marker.
