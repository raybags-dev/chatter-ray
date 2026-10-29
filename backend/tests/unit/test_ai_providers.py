"""Unit tests for the pluggable AI providers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.ai_providers import (
    ClaudeProvider,
    GeminiProvider,
    build_provider,
    env_key_for,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _fake_client(payload: dict) -> MagicMock:
    client = MagicMock()
    client.post = AsyncMock(return_value=_FakeResponse(payload))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


async def test_deepseek_openai_compat_text_and_tool_call():
    provider = build_provider("deepseek", "deepseek-chat", "k")
    payload = {
        "choices": [
            {
                "message": {
                    "content": "hi",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "escalate_to_human",
                                "arguments": '{"reason": "hire"}',
                            }
                        }
                    ],
                }
            }
        ]
    }
    with patch("app.core.ai_providers.httpx.AsyncClient", return_value=_fake_client(payload)):
        result = await provider.chat([{"role": "user", "content": "x"}], tools=[{"t": 1}])
    assert result.content == "hi"
    assert result.tool_call == ("escalate_to_human", {"reason": "hire"})
    assert provider.supports_tools is True


async def test_groq_builds_openai_compat():
    provider = build_provider("groq", None, "k")
    assert provider.base_url == "https://api.groq.com/openai/v1"
    assert provider.supports_tools is True


async def test_claude_parses_text_blocks():
    provider = ClaudeProvider("k", "claude-sonnet-4-20250514")
    payload = {"content": [{"type": "text", "text": "hello"}, {"type": "text", "text": " world"}]}
    with patch("app.core.ai_providers.httpx.AsyncClient", return_value=_fake_client(payload)):
        result = await provider.chat(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "x"}]
        )
    assert result.content == "hello world"
    assert provider.supports_tools is False


async def test_gemini_parses_candidates():
    provider = GeminiProvider("k", "gemini-2.0-flash")
    payload = {"candidates": [{"content": {"parts": [{"text": "gemini reply"}]}}]}
    with patch("app.core.ai_providers.httpx.AsyncClient", return_value=_fake_client(payload)):
        result = await provider.chat(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "x"}]
        )
    assert result.content == "gemini reply"
    assert provider.supports_tools is False


async def test_build_provider_requires_key():
    with patch("app.core.ai_providers.env_key_for", return_value=None):
        with pytest.raises(RuntimeError, match="No API key"):
            build_provider("deepseek", None, None)


async def test_build_provider_unknown_rejected():
    with pytest.raises(RuntimeError, match="Unknown AI provider"):
        build_provider("openai-gpt-99", None, "k")


def test_env_key_fallback_map():
    keys = {p: env_key_for(p) for p in ("deepseek", "groq", "claude", "gemini")}
    assert set(keys) == {"deepseek", "groq", "claude", "gemini"}
