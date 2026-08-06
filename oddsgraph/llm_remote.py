"""Remote llama-server backend for grammar-constrained graph extraction."""

from __future__ import annotations

import json
import logging

import httpx
from llama_cpp.llama_grammar import json_schema_to_gbnf

from oddsgraph.config import Settings
from oddsgraph.llm import BaseGraphLLM
from oddsgraph.schema import GraphFragment

logger = logging.getLogger(__name__)

_SERVER_START_HINT = (
    "Start llama-server before inferring, for example:\n"
    "  llama-server -m models/qwen3-4b-q4_k_m.gguf -ngl -1 -c 12288 "
    "-np 4 -cb -fa on --host 127.0.0.1 --port 8080"
)


class RemoteGraphLLM(BaseGraphLLM):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._grammar = json_schema_to_gbnf(
            json.dumps(GraphFragment.model_json_schema())
        )
        self._client = httpx.Client(
            base_url=settings.server_base_url.rstrip("/"),
            timeout=settings.server_request_timeout,
        )
        self._health_checked = False

    def _ensure_server_ready(self) -> None:
        if self._health_checked:
            return
        try:
            response = self._client.get("/health")
            if response.status_code >= 400:
                raise ConnectionError(
                    f"llama-server health check failed ({response.status_code}): "
                    f"{response.text[:200]}\n{_SERVER_START_HINT}"
                )
        except httpx.HTTPError as exc:
            raise ConnectionError(
                f"Cannot reach llama-server at {self.settings.server_base_url}. "
                f"{exc}\n{_SERVER_START_HINT}"
            ) from exc
        self._health_checked = True

    def _complete(
        self,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        self._ensure_server_ready()
        payload = {
            "messages": [
                {"role": "system", "content": "You are a structured graph extractor."},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "grammar": self._grammar,
        }
        response = self._client.post("/v1/chat/completions", json=payload)
        if response.status_code >= 400:
            raise RuntimeError(
                f"llama-server chat completion failed ({response.status_code}): "
                f"{response.text[:500]}"
            )
        data = response.json()
        return data["choices"][0]["message"]["content"]
