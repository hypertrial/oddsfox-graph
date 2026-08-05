# Local model weights

Download the Qwen3-4B Q4_K_M GGUF model for local Metal-accelerated inference.

## Recommended model

- Repository: `Qwen/Qwen3-4B-GGUF`
- File: `Qwen3-4B-Q4_K_M.gguf` (~2.5 GB)

## Download

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

## Metal acceleration

On Apple Silicon, install `llama-cpp-python` with Metal support:

```bash
CMAKE_ARGS="-DGGML_METAL=on" uv sync
```

The pipeline uses `n_gpu_layers=-1` to offload all layers to the GPU.
