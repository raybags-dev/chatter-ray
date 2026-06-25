"""Unit tests for the LLM agent."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_groq_response(content: str | None = None, tool_name: str | None = None,
                        tool_args: dict | None = None):
    """Build a fake Groq chat-completion response object."""
    import json

    msg = MagicMock()

    if tool_name:
        call = MagicMock()
        call.function.name = tool_name
        call.function.arguments = json.dumps(tool_args or {})
        msg.tool_calls = [call]
        msg.content = None
    else:
        msg.tool_calls = None
        msg.content = content

    choice = MagicMock()
    choice.message = msg

    response = MagicMock()
    response.choices = [choice]
    return response


# ── tests ─────────────────────────────────────────────────────────────────────

async def test_no_api_key_returns_fallback():
    """When GROQ_API_KEY is falsy the agent returns a safe fallback message."""
    with patch("app.core.llm.settings") as mock_settings:
        mock_settings.GROQ_API_KEY = None

        from app.core.llm import run_agent
        reply, tool = await run_agent([], "session-no-key")

    assert "LLM not configured" in reply
    assert tool is None


async def test_simple_text_reply():
    """Plain text response from Groq is returned as-is."""
    fake_response = _make_groq_response(content="Hello from Ray!")

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=fake_response)

    with (
        patch("app.core.llm.settings") as mock_settings,
        patch("app.core.llm.AsyncGroq", return_value=mock_client),
    ):
        mock_settings.GROQ_API_KEY = "test-key"
        mock_settings.GROQ_MODEL = "llama-3.3-70b-versatile"

        from app.core.llm import run_agent
        reply, tool = await run_agent(
            [{"role": "user", "content": "Hi there"}], "session-text"
        )

    assert reply == "Hello from Ray!"
    assert tool is None


async def test_generate_pipeline_token_tool_success():
    """generate_pipeline_token → calls portfolio API, returns confirmation."""
    fake_response = _make_groq_response(
        tool_name="generate_pipeline_token",
        tool_args={"name": "Jane Doe", "email": "jane@example.com"},
    )

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=fake_response)

    mock_portfolio_result = {"ok": True, "message": "stored"}

    with (
        patch("app.core.llm.settings") as mock_settings,
        patch("app.core.llm.AsyncGroq", return_value=mock_client),
        patch("app.core.llm._call_portfolio_api", new=AsyncMock(return_value=mock_portfolio_result)),
    ):
        mock_settings.GROQ_API_KEY = "test-key"
        mock_settings.GROQ_MODEL = "llama-3.3-70b-versatile"

        from app.core.llm import run_agent
        reply, tool = await run_agent(
            [{"role": "user", "content": "I want to run the pipeline. I'm Jane, jane@example.com"}],
            "session-token",
        )

    assert tool == "generate_pipeline_token"
    assert "jane@example.com" in reply
    assert "token" in reply.lower()


async def test_generate_pipeline_token_tool_failure():
    """When the portfolio API fails, the agent replies with a fallback message."""
    fake_response = _make_groq_response(
        tool_name="generate_pipeline_token",
        tool_args={"name": "Bob", "email": "bob@example.com"},
    )

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=fake_response)

    with (
        patch("app.core.llm.settings") as mock_settings,
        patch("app.core.llm.AsyncGroq", return_value=mock_client),
        patch("app.core.llm._call_portfolio_api", new=AsyncMock(return_value={"ok": False, "detail": "DB error"})),
    ):
        mock_settings.GROQ_API_KEY = "test-key"
        mock_settings.GROQ_MODEL = "llama-3.3-70b-versatile"

        from app.core.llm import run_agent
        reply, tool = await run_agent([], "session-fail-token")

    assert tool == "generate_pipeline_token"
    assert "wrong" in reply.lower() or "request form" in reply.lower()


async def test_escalate_to_human_pings_discord():
    """escalate_to_human calls _ping_discord and returns escalation message."""
    fake_response = _make_groq_response(
        tool_name="escalate_to_human",
        tool_args={"reason": "Visitor asking about hiring"},
    )

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=fake_response)

    mock_ping = AsyncMock()

    with (
        patch("app.core.llm.settings") as mock_settings,
        patch("app.core.llm.AsyncGroq", return_value=mock_client),
        patch("app.core.llm._ping_discord", mock_ping),
    ):
        mock_settings.GROQ_API_KEY = "test-key"
        mock_settings.GROQ_MODEL = "llama-3.3-70b-versatile"

        from app.core.llm import run_agent
        reply, tool = await run_agent(
            [{"role": "user", "content": "I'd like to hire you"}], "session-escalate"
        )

    assert tool == "escalate_to_human"
    assert "ray" in reply.lower() or "flagged" in reply.lower()
    mock_ping.assert_awaited_once_with("Visitor asking about hiring", "session-escalate")


async def test_embedded_escalate_call_in_content():
    """Model embeds <function=escalate_to_human> in content — should be handled cleanly."""
    fake_response = MagicMock()
    msg = MagicMock()
    msg.tool_calls = None
    msg.content = (
        "I can't share that. "
        '<function=escalate_to_human>{"reason": "sensitive request"}</function>'
    )
    fake_response.choices = [MagicMock(message=msg)]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=fake_response)
    mock_ping = AsyncMock()

    with (
        patch("app.core.llm.settings") as mock_settings,
        patch("app.core.llm.AsyncGroq", return_value=mock_client),
        patch("app.core.llm._ping_discord", mock_ping),
    ):
        mock_settings.GROQ_API_KEY = "test-key"
        mock_settings.GROQ_MODEL = "llama-3.3-70b-versatile"

        from app.core.llm import run_agent
        reply, tool = await run_agent([], "session-embedded")

    assert tool == "escalate_to_human"
    assert "<function=" not in reply
    assert "flagged" in reply.lower() or "ray" in reply.lower()
    mock_ping.assert_awaited_once_with("sensitive request", "session-embedded")


async def test_embedded_call_stripped_from_plain_text():
    """Any residual <function=...> syntax is stripped from plain text replies."""
    fake_response = MagicMock()
    msg = MagicMock()
    msg.tool_calls = None
    msg.content = "Here is some info. <function=unknown_tool>{}</function>"
    fake_response.choices = [MagicMock(message=msg)]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=fake_response)

    with (
        patch("app.core.llm.settings") as mock_settings,
        patch("app.core.llm.AsyncGroq", return_value=mock_client),
    ):
        mock_settings.GROQ_API_KEY = "test-key"
        mock_settings.GROQ_MODEL = "llama-3.3-70b-versatile"

        from app.core.llm import run_agent
        reply, tool = await run_agent([], "session-strip")

    assert "<function=" not in reply
    assert tool is None
    assert "Here is some info." in reply


async def test_escalate_skips_discord_when_no_webhook():
    """_ping_discord is a no-op when DISCORD_WEBHOOK is not set."""
    with patch("app.core.llm.settings") as mock_settings:
        mock_settings.DISCORD_WEBHOOK = None

        from app.core.llm import _ping_discord
        # Should not raise — no httpx call made
        await _ping_discord("test reason", "session-x")
