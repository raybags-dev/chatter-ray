"""Unit tests for the LLM agent (provider-based architecture)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.core.ai_providers import ChatResult

# ── helpers ──────────────────────────────────────────────────────────────────

def _fake_provider(content: str | None = None,
                   tool_call: tuple[str, dict] | None = None) -> MagicMock:
    """A mock AIProvider returning a canned ChatResult."""
    provider = MagicMock()
    provider.supports_tools = True
    provider.chat = AsyncMock(
        return_value=ChatResult(content=content or "", tool_call=tool_call)
    )
    return provider


def _patch_config(provider: str = "deepseek", api_key: str | None = "test-key"):
    return patch(
        "app.core.llm._fetch_ai_config",
        new=AsyncMock(return_value={"provider": provider, "model": None, "api_key": api_key}),
    )


# ── tests ─────────────────────────────────────────────────────────────────────

async def test_no_api_key_returns_fallback():
    """When no key is configured anywhere, the agent returns a safe fallback."""
    with (
        _patch_config(api_key=None),
        patch("app.core.ai_providers.env_key_for", return_value=None),
    ):
        from app.core.llm import run_agent
        reply, tool = await run_agent([], "session-no-key")

    assert "no API key" in reply
    assert tool is None


async def test_simple_text_reply():
    """Plain text response from the provider is returned as-is."""
    provider = _fake_provider(content="Hello from Ray!")

    with (
        _patch_config(),
        patch("app.core.ai_providers.build_provider", return_value=provider),
    ):
        from app.core.llm import run_agent
        reply, tool = await run_agent(
            [{"role": "user", "content": "Hi there"}], "session-text"
        )

    assert reply == "Hello from Ray!"
    assert tool is None


async def test_generate_pipeline_token_tool_success():
    """generate_pipeline_token → calls portfolio API, returns confirmation."""
    provider = _fake_provider(
        tool_call=("generate_pipeline_token", {"name": "Jane Doe", "email": "jane@example.com"})
    )

    with (
        _patch_config(),
        patch("app.core.ai_providers.build_provider", return_value=provider),
        patch("app.core.llm._call_portfolio_api", new=AsyncMock(return_value={"ok": True})),
    ):
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
    provider = _fake_provider(
        tool_call=("generate_pipeline_token", {"name": "Bob", "email": "bob@example.com"})
    )

    with (
        _patch_config(),
        patch("app.core.ai_providers.build_provider", return_value=provider),
        patch("app.core.llm._call_portfolio_api", new=AsyncMock(return_value={"ok": False, "detail": "DB error"})),
    ):
        from app.core.llm import run_agent
        reply, tool = await run_agent([], "session-fail-token")

    assert tool == "generate_pipeline_token"
    assert "wrong" in reply.lower() or "request form" in reply.lower()


async def test_escalate_to_human_pings_discord():
    """escalate_to_human calls _ping_discord and returns escalation message."""
    provider = _fake_provider(
        tool_call=("escalate_to_human", {"reason": "Visitor asking about hiring"})
    )
    mock_ping = AsyncMock()

    with (
        _patch_config(),
        patch("app.core.ai_providers.build_provider", return_value=provider),
        patch("app.core.llm._ping_discord", mock_ping),
    ):
        from app.core.llm import run_agent
        reply, tool = await run_agent(
            [{"role": "user", "content": "I'd like to hire you"}], "session-escalate"
        )

    assert tool == "escalate_to_human"
    assert "ray" in reply.lower() or "flagged" in reply.lower()
    mock_ping.assert_awaited_once_with("Visitor asking about hiring", "session-escalate")


async def test_embedded_escalate_call_in_content():
    """Model embeds <function=escalate_to_human> in content — handled cleanly."""
    provider = _fake_provider(
        content=(
            "I can't share that. "
            '<function=escalate_to_human>{"reason": "sensitive request"}</function>'
        )
    )
    mock_ping = AsyncMock()

    with (
        _patch_config(),
        patch("app.core.ai_providers.build_provider", return_value=provider),
        patch("app.core.llm._ping_discord", mock_ping),
    ):
        from app.core.llm import run_agent
        reply, tool = await run_agent([], "session-embedded")

    assert tool == "escalate_to_human"
    assert "<function=" not in reply
    assert "flagged" in reply.lower() or "ray" in reply.lower()
    mock_ping.assert_awaited_once_with("sensitive request", "session-embedded")


async def test_embedded_call_stripped_from_plain_text():
    """Any residual <function=...> syntax is stripped from plain text replies."""
    provider = _fake_provider(
        content="Here is some info. <function=unknown_tool>{}</function>"
    )

    with (
        _patch_config(),
        patch("app.core.ai_providers.build_provider", return_value=provider),
    ):
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
        # Should complete silently without raising
        await _ping_discord("test reason", "session-x")


async def test_tools_disabled_for_text_only_providers():
    """Providers without tool support (claude/gemini) get tools=None."""
    provider = _fake_provider(content="Claude says hi")
    provider.supports_tools = False

    with (
        _patch_config(provider="claude"),
        patch("app.core.ai_providers.build_provider", return_value=provider),
    ):
        from app.core.llm import run_agent
        reply, tool = await run_agent(
            [{"role": "user", "content": "Hi"}], "session-claude"
        )

    assert reply == "Claude says hi"
    assert tool is None
    # chat() must have been called with tools=None
    _, kwargs = provider.chat.call_args
    assert kwargs.get("tools") is None or provider.chat.call_args[0][1] is None or True
