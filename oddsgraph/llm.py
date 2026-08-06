"""Local LLM wrapper for grammar-constrained graph extraction."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from pydantic import ValidationError

from oddsgraph.config import Settings
from oddsgraph.prompts import ALLOWED_EDGE_TYPES, ALLOWED_NODE_TYPES
from oddsgraph.schema import GraphFragment

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

    def generate_fragment(
        self,
        prompt: str,
        event_id: str,
        max_tokens_override: int | None = None,
    ) -> GraphFragment:
        last_error: Exception | None = None
        raw_output = ""
        max_tokens = max_tokens_override if max_tokens_override is not None else self.settings.max_tokens

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
                parsed = json.loads(raw_output)
                fragment = GraphFragment.model_validate(parsed)
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
        raise LLMInferenceError(f"LLM inference failed for event {event_id}: {last_error}")

    def _write_failed_output(
        self,
        event_id: str,
        raw_output: str,
        error: Exception | None,
    ) -> None:
        self.settings.failed_fragments_dir.mkdir(parents=True, exist_ok=True)
        path = self.settings.failed_fragments_dir / f"{event_id}.txt"
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
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._llama: Any = None
        self._grammar: Any = None

    def _ensure_loaded(self) -> None:
        if self._llama is not None:
            return
        from llama_cpp import Llama, LlamaGrammar, LlamaRAMCache

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

        self._grammar = LlamaGrammar.from_json_schema(
            json.dumps(GraphFragment.model_json_schema())
        )
        self._llama = Llama(**llama_kwargs)
        self._llama.set_cache(LlamaRAMCache())

    def _complete(
        self,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        self._ensure_loaded()
        response = self._llama.create_chat_completion(
            messages=[
                {"role": "system", "content": "You are a structured graph extractor."},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            grammar=self._grammar,
        )
        return response["choices"][0]["message"]["content"]


def build_graph_llm(settings: Settings) -> BaseGraphLLM:
    if settings.llm_backend == "server":
        from oddsgraph.llm_remote import RemoteGraphLLM

        return RemoteGraphLLM(settings)
    return LocalGraphLLM(settings)
