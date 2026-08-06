"""MLX-LM backend for outlines-constrained graph extraction (Apple Silicon)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from oddsgraph.config import Settings
from oddsgraph.llm import BaseGraphLLM
from oddsgraph.schema import CompactGraphFragment

logger = logging.getLogger(__name__)

_MLX_INSTALL_HINT = (
    "Install the mlx extra on Apple Silicon:\n"
    "  uv sync --frozen --extra mlx\n"
    "Convert a Hugging Face / GGUF-compatible checkpoint, for example:\n"
    "  uv run python -m mlx_lm.convert "
    "--hf-path Qwen/Qwen3-4B-Instruct --mlx-path models/qwen3-4b-mlx -q"
)


class MLXGraphLLM(BaseGraphLLM):
    """In-process mlx-lm backend with outlines FSM constrained decoding."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._outlines_model: Any = None

    def _ensure_loaded(self) -> None:
        if self._outlines_model is not None:
            return
        try:
            import mlx_lm
            import outlines
        except ImportError as exc:
            raise ImportError(
                f"mlx backend requires mlx-lm and outlines. {_MLX_INSTALL_HINT}"
            ) from exc

        model_path = Path(self.settings.mlx_model_path)
        if not model_path.exists():
            raise FileNotFoundError(
                f"MLX model not found: {model_path}\n{_MLX_INSTALL_HINT}"
            )

        model, tokenizer = mlx_lm.load(str(model_path))
        self._outlines_model = outlines.from_mlxlm(model, tokenizer)

    def _complete(
        self,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        self._ensure_loaded()
        # Disable Qwen3 thinking channel so JSON fills the token budget.
        prompt = user_prompt + "\n/no_think"
        result = self._outlines_model(
            prompt,
            CompactGraphFragment,
            max_tokens=max_tokens,
            temp=temperature,
        )
        if isinstance(result, CompactGraphFragment):
            return result.model_dump_json()
        if isinstance(result, str):
            return result
        return json.dumps(result)
