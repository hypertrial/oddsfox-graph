# llama-server

For large full-dataset runs on Apple Silicon, start `llama-server` with
continuous batching and point oddsgraph at it.

## Start the server

Install via Homebrew: `brew install llama.cpp`.

```bash
llama-server -m models/qwen3-4b-q4_k_m.gguf -ngl -1 -c 12288 -np 4 -cb -fa on \
  --host 127.0.0.1 --port 8080
```

## Point oddsgraph at it

```bash
oddsgraph run --llm-backend server --concurrency 4
```

Or infer only:

```bash
oddsgraph infer --llm-backend server --server-url http://127.0.0.1:8080 --concurrency 4
```

## Notes

- The default backend remains `inprocess` and does not require a separate server.
- `--concurrency` only applies to the `server` backend.
- Keep `n_ctx` / chunk budgets aligned with [Configuration](../reference/configuration.md).

## See also

- [Running the pipeline](running-the-pipeline.md)
- [Configuration](../reference/configuration.md)
- [FAQ](../concepts/faq.md)
