"""Local LLM wrapper for grammar-constrained graph extraction."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from oddsfox_graph.config import Settings
from oddsfox_graph.prompts import ALLOWED_EDGE_TYPES, ALLOWED_NODE_TYPES
from oddsfox_graph.schema import Edge, GraphFragment, Node

logger = logging.getLogger(__name__)


class LLMInferenceError(RuntimeError):
    pass


class LocalGraphLLM:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._llama: Any = None
        self._grammar: Any = None

    def _ensure_loaded(self) -> None:
        if self._llama is not None:
            return
        from llama_cpp import Llama, LlamaGrammar

        model_path = self.settings.model_path
        if not model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")

        self._grammar = LlamaGrammar.from_json_schema(
            json.dumps(GraphFragment.model_json_schema())
        )
        self._llama = Llama(
            model_path=str(model_path),
            n_gpu_layers=self.settings.n_gpu_layers,
            n_ctx=self.settings.n_ctx,
            verbose=False,
        )

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
        strict: bool = False,
    ) -> GraphFragment:
        self._ensure_loaded()
        last_error: Exception | None = None
        raw_output = ""

        for attempt in range(self.settings.max_retries + 1):
            attempt_prompt = prompt
            if attempt > 0:
                attempt_prompt += (
                    "\n\nSTRICT RETRY: Return valid JSON only. "
                    "confidence must be 0-1. evidence_market_ids must be non-empty."
                )
            try:
                response = self._llama.create_chat_completion(
                    messages=[
                        {"role": "system", "content": "You are a structured graph extractor."},
                        {"role": "user", "content": attempt_prompt},
                    ],
                    max_tokens=self.settings.max_tokens,
                    temperature=self.settings.temperature,
                    grammar=self._grammar,
                )
                raw_output = response["choices"][0]["message"]["content"]
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
