# Local model weights

Download the Qwen3-4B Q4_K_M GGUF model for local Metal-accelerated inference,
and optionally convert an MLX checkpoint for the `mlx` backend.

## Recommended GGUF model (inprocess / server)

- Repository: `Qwen/Qwen3-4B-GGUF`
- File: `Qwen3-4B-Q4_K_M.gguf` (~2.5 GB)

### Download

Using Hugging Face CLI:

```bash
huggingface-cli download Qwen/Qwen3-4B-GGUF Qwen3-4B-Q4_K_M.gguf \
  --local-dir models \
  --local-dir-use-symlinks False
mv models/Qwen3-4B-Q4_K_M.gguf models/qwen3-4b-q4_k_m.gguf
```

Or with curl:

```bash
curl -L -o models/qwen3-4b-q4_k_m.gguf \
  "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf"
```

### Metal acceleration (llama-cpp-python)

On Apple Silicon, install `llama-cpp-python` with Metal support:

```bash
CMAKE_ARGS="-DGGML_METAL=on" uv sync --frozen --extra dev
```

The pipeline uses `n_gpu_layers=-1` to offload all layers to the GPU.
Constrained decoding uses `outlines` (FSM) instead of raw llama.cpp GBNF.

## Optional MLX model (`--llm-backend mlx`)

```bash
uv sync --frozen --extra mlx
uv run python -m mlx_lm.convert \
  --hf-path Qwen/Qwen3-4B \
  --mlx-path models/qwen3-4b-mlx -q
```

Then:

```bash
oddsgraph infer --llm-backend mlx --mlx-model-path models/qwen3-4b-mlx
```

After LoRA fine-tuning (`scripts/finetune_lora.py`), point `--mlx-model-path`
at the fused/adapted model directory.
