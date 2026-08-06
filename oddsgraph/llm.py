"""Local LLM wrapper for grammar-constrained graph extraction."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from pydantic import ValidationError

from oddsgraph.config import Settings
from oddsgraph.paths import event_artifact_path
from oddsgraph.prompts import ALLOWED_EDGE_TYPES, ALLOWED_NODE_TYPES
from oddsgraph.schema import CompactGraphFragment, GraphFragment

logger = logging.getLogger(__name__)


class LLMInferenceError(RuntimeError):
    pass


class BaseGraphLLM(ABC):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _filter_llm_fragment(self, fragment: GraphFragment) -> GraphFragment:
        nodes = [n for n in fragment.nodes if n.type in ALLOWED_NODE_TYPES]
        allowed_local_ids = {n.local_id for n in nodes}
        edges = [
            e
            for e in fragment.edges
            if e.type in ALLOWED_EDGE_TYPES
            and e.source in allowed_local_ids
            and e.target in allowed_local_ids
        ]
        return GraphFragment(nodes=nodes, edges=edges)

    def _parse_fragment(self, raw_output: str) -> GraphFragment:
        """Parse LLM JSON as CompactGraphFragment or legacy GraphFragment."""
        parsed = json.loads(raw_output)
        if isinstance(parsed, dict) and ("n" in parsed or "g" in parsed):
            compact = CompactGraphFragment.model_validate(parsed)
            return compact.to_graph_fragment()
        return GraphFragment.model_validate(parsed)

    def generate_fragment(
        self,
        prompt: str,
        event_id: str,
        max_tokens_override: int | None = None,
    ) -> GraphFragment:
        last_error: Exception | None = None
        raw_output = ""
        max_tokens = (
            max_tokens_override
            if max_tokens_override is not None
            else self.settings.max_tokens
        )

        for attempt in range(self.settings.max_retries + 1):
            attempt_prompt = prompt
            if attempt > 0:
                attempt_prompt += (
                    "\n\nSTRICT RETRY: Return valid JSON only. "
                    "confidence must be 0-1. evidence_market_ids must be non-empty."
                )
            try:
                raw_output = self._complete(
                    attempt_prompt,
                    max_tokens=max_tokens,
                    temperature=self.settings.temperature,
                )
                if not raw_output or not raw_output.strip():
                    raise json.JSONDecodeError(
                        "Empty LLM output", raw_output or "", 0
                    )
                fragment = self._parse_fragment(raw_output)
                return self._filter_llm_fragment(fragment)
            except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
                last_error = exc
                logger.warning(
                    "LLM validation failed for event %s attempt %d: %s",
                    event_id,
                    attempt + 1,
                    exc,
                )

        self._write_failed_output(event_id, raw_output, last_error)
        raise LLMInferenceError(
            f"LLM inference failed for event {event_id}: {last_error}"
        )

    def _write_failed_output(
        self,
        event_id: str,
        raw_output: str,
        error: Exception | None,
    ) -> None:
        self.settings.failed_fragments_dir.mkdir(parents=True, exist_ok=True)
        try:
            path = event_artifact_path(
                self.settings.failed_fragments_dir, event_id, ".txt"
            )
        except ValueError as exc:
            logger.warning(
                "Skipping failed-output dump for unsafe event_id %r: %s",
                event_id,
                exc,
            )
            return
        path.write_text(
            f"error: {error}\n\nraw_output:\n{raw_output}",
            encoding="utf-8",
        )

    @abstractmethod
    def _complete(
        self,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Return raw assistant message content from the LLM backend."""


def _resolve_ggml_type(name: str | None) -> int | None:
    if name is None:
        return None
    import llama_cpp

    key = f"GGML_TYPE_{name.upper()}"
    value = getattr(llama_cpp, key, None)
    if value is None:
        raise ValueError(f"Unknown KV cache type: {name}")
    return value


class LocalGraphLLM(BaseGraphLLM):
    """In-process llama-cpp-python backend with outlines FSM constrained decoding."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._llama: Any = None
        self._outlines_model: Any = None

    def _ensure_loaded(self) -> None:
        if self._outlines_model is not None:
            return
        from llama_cpp import Llama, LlamaRAMCache
        import outlines

        model_path = self.settings.model_path
        if not model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")

        llama_kwargs: dict[str, Any] = {
            "model_path": str(model_path),
            "n_gpu_layers": self.settings.n_gpu_layers,
            "n_ctx": self.settings.n_ctx,
            "flash_attn": self.settings.flash_attn,
            "n_batch": self.settings.n_batch,
            "n_ubatch": self.settings.n_ubatch,
            "verbose": False,
        }
        type_k = _resolve_ggml_type(self.settings.kv_cache_type_k)
        type_v = _resolve_ggml_type(self.settings.kv_cache_type_v)
        if type_k is not None:
            llama_kwargs["type_k"] = type_k
        if type_v is not None:
            llama_kwargs["type_v"] = type_v

        self._llama = Llama(**llama_kwargs)
        self._llama.set_cache(LlamaRAMCache())
        self._outlines_model = outlines.from_llamacpp(self._llama, chat_mode=True)

    def _complete(
        self,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        self._ensure_loaded()
        from outlines.inputs import Chat

        # Qwen3 defaults to a <think> channel that can consume the entire
        # max_tokens budget before any JSON is emitted. Force no-think.
        chat = Chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a structured graph extractor. "
                        "Return valid compact JSON only. /no_think"
                    ),
                },
                {"role": "user", "content": user_prompt + "\n/no_think"},
            ]
        )
        result = self._outlines_model(
            chat,
            CompactGraphFragment,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if isinstance(result, CompactGraphFragment):
            return result.model_dump_json()
        if isinstance(result, str):
            return result
        return json.dumps(result)


def build_graph_llm(settings: Settings) -> BaseGraphLLM:
    if settings.llm_backend == "server":
        from oddsgraph.llm_remote import RemoteGraphLLM

        return RemoteGraphLLM(settings)
    if settings.llm_backend == "mlx":
        from oddsgraph.llm_mlx import MLXGraphLLM

        return MLXGraphLLM(settings)
    return LocalGraphLLM(settings)
