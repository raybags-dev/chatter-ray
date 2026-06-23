"""LLM agent: Groq-backed conversational agent with tool use.

The agent handles incoming visitor messages, maintains conversation history,
and can invoke tools:
  - generate_pipeline_token(name, email) — issues a DataForge access token
  - escalate_to_human(reason)            — pings Discord + flags session
  - (answers FAQ from its system prompt)

All tool results are streamed back as chat messages.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from groq import AsyncGroq

from app.core.config import settings

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "generate_pipeline_token",
            "description": (
                "Issue a DataForge ELT pipeline access token and email it directly to "
                "the visitor. Use this when the visitor clearly wants to run the pipeline "
                "and has provided their name and email."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Visitor's full name"},
                    "email": {"type": "string", "description": "Visitor's email address"},
                },
                "required": ["name", "email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": (
                "Flag this session for human takeover and notify Ray via Discord. "
                "Use when the visitor asks about job opportunities, consulting, "
                "custom work, or anything requiring a human decision."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Brief reason for escalation",
                    }
                },
                "required": ["reason"],
            },
        },
    },
]

_SYSTEM_PROMPT = """You are the AI assistant on Ray Baguma's portfolio site — think of yourself as a friendly, knowledgeable sidekick who genuinely cares about the visitor.

=== RAY'S PROFILE (stick to these facts — never make things up) ===
Name: Raymond Baguma (everyone calls him Ray)
Role: Data Engineer / Full-Stack Developer
Location: Remote
Portfolio: raybags.com

Projects:
- DataForge ELT — Python 3.13, FastAPI, DuckDB, dbt-core, Playwright crawlers, S3 data lake, React/Vite dashboard. Visitors get one free pipeline run.
- Data Annotation Platform — collaborative labelling tool built for ML teams.
- This chat — event-driven with WebSockets, Redis pub/sub, Groq LLM + tool use, human takeover. A nice slice of real-world async design.

Skills: Python, FastAPI, dbt, DuckDB, SQLAlchemy, Alembic, React, Next.js, TypeScript, Docker, GitHub Actions, PostgreSQL, Supabase, Redis, Playwright, SQLite.

Contact: baguma.github@gmail.com | GitHub: raybags-dev

=== HOW TO TALK ===
- Be warm, casual, and conversational — like a knowledgeable friend, not a corporate bot.
- Short replies: 2–3 sentences max. No bullet walls, no hollow openers like "Certainly!" or "Absolutely!".
- Show some personality — light humour is welcome. Never use profanity or inappropriate language.
- Vary your sentence starts; don't kick off every reply with "I".
- If you genuinely don't know something about Ray, say so and point them to baguma.github@gmail.com.

=== YOUR JOB ===
1. Answer questions about Ray's skills, background, and projects — only using the facts above.
2. Help visitors try DataForge — one free pipeline run at raybags.com/dataforge.
   For extra runs, collect name + email, then call generate_pipeline_token.
3. If anyone wants to speak to Ray directly, or discuss work/hiring/consulting:
   - Call escalate_to_human immediately.
   - Tell them: "I've just pinged Ray — he should jump in shortly. You can also reach him directly at baguma.github@gmail.com."

=== RULES ===
- Never invent facts about Ray's experience, rates, opinions, or availability.
- "speak to Ray", "contact you", "hire you", "job", "consulting" → escalate_to_human, no exceptions.
- Always collect name + email before calling generate_pipeline_token.
"""


async def _call_portfolio_api(name: str, email: str) -> dict[str, Any]:
    """Call portfolio backend to submit a pipeline request (which stores + notifies admin)."""
    if not settings.PORTFOLIO_API_URL:
        return {"ok": False, "detail": "Portfolio API not configured"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{settings.PORTFOLIO_API_URL}/pipeline-requests",
            json={"name": name, "email": email, "reason": "Requested via chat"},
        )
        return r.json() if r.status_code < 300 else {"ok": False, "detail": r.text}


async def _ping_discord(reason: str, session_id: str) -> None:
    if not settings.DISCORD_WEBHOOK:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            settings.DISCORD_WEBHOOK,
            json={
                "embeds": [{
                    "title": "Chat escalation — human needed",
                    "color": 0xFF4444,
                    "fields": [
                        {"name": "Session", "value": session_id, "inline": True},
                        {"name": "Reason", "value": reason, "inline": False},
                    ],
                    "footer": {"text": "raybags.com/chat"},
                }]
            },
        )


async def run_agent(
    history: list[dict[str, str]],
    session_id: str,
) -> tuple[str, str | None]:
    """Run one LLM turn.

    Returns (reply_text, tool_called_name | None).
    Raises on API errors.
    """
    if not settings.GROQ_API_KEY:
        return "LLM not configured — please set GROQ_API_KEY.", None

    client = AsyncGroq(api_key=settings.GROQ_API_KEY)
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}, *history]

    response = await client.chat.completions.create(
        model=settings.GROQ_MODEL,
        messages=messages,
        tools=_TOOLS,
        tool_choice="auto",
        max_tokens=512,
    )

    choice = response.choices[0]
    msg = choice.message
    tool_name: str | None = None

    if msg.tool_calls:
        call = msg.tool_calls[0]
        tool_name = call.function.name
        args = json.loads(call.function.arguments)

        if tool_name == "generate_pipeline_token":
            result = await _call_portfolio_api(args["name"], args["email"])
            if result.get("ok"):
                reply = (
                    f"Done! I've sent a pipeline access token to **{args['email']}**. "
                    "Check your inbox — it expires in 48 hours. "
                    "Visit https://raybags.com/dataforge/ and enter it when prompted."
                )
            else:
                reply = (
                    "Something went wrong issuing the token. "
                    "Please try the request form at https://raybags.com/dataforge."
                )

        elif tool_name == "escalate_to_human":
            await _ping_discord(args["reason"], session_id)
            reply = (
                "I've flagged this for Ray — he'll jump in shortly. "
                "Feel free to keep chatting in the meantime."
            )
        else:
            reply = "I'm not sure how to handle that right now."

    else:
        reply = msg.content or ""

    return reply, tool_name
