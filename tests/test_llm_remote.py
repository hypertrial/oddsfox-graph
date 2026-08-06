"""Tests for RemoteGraphLLM HTTP backend."""

from __future__ import annotations

import json

import httpx
import pytest

from oddsgraph.config import Settings
from oddsgraph.llm_remote import RemoteGraphLLM, _SERVER_START_HINT


def _valid_response_content() -> str:
    return json.dumps({"nodes": [], "edges": []})


def test_remote_graph_llm_posts_grammar_and_messages(tmp_path) -> None:
    settings = Settings()
    settings.server_base_url = "http://testserver"
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": _valid_response_content()}}],
            },
        )

    transport = httpx.MockTransport(handler)
    llm = RemoteGraphLLM(settings)
    llm._client = httpx.Client(
        base_url=settings.server_base_url,
        transport=transport,
    )
    llm._health_checked = True

    fragment = llm.generate_fragment("user prompt", "evt1", max_tokens_override=256)
    assert fragment.nodes == []
    assert captured["path"] == "/v1/chat/completions"
    body = captured["body"]
    assert body["max_tokens"] == 256
    assert "grammar" in body
    assert len(body["grammar"]) > 100
    assert body["messages"][1]["content"] == "user prompt"


def test_remote_graph_llm_health_failure_raises_with_hint(tmp_path) -> None:
    settings = Settings()
    settings.server_base_url = "http://testserver"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    transport = httpx.MockTransport(handler)
    llm = RemoteGraphLLM(settings)
    llm._client = httpx.Client(
        base_url=settings.server_base_url,
        transport=transport,
    )

    with pytest.raises(ConnectionError) as exc_info:
        llm._ensure_server_ready()
    assert _SERVER_START_HINT in str(exc_info.value)
