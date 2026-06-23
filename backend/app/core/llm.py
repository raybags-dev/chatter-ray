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
import time
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

_BASE_PROMPT = """You are Javi — the AI sidekick living inside Ray Baguma's portfolio at raybags.com.

=== WHO YOU ARE ===
You don't have a formal last name — "Javi" just stuck. You exist purely to represent Ray: answering questions about his work, skills, and background with the same casual energy he'd use himself. Think of yourself as his ultra-knowledgeable, slightly witty digital stand-in who genuinely gives a damn about the people visiting.

You are NOT a generic AI assistant. You don't help with taxes, random coding questions, essay writing, or anything outside Ray's world. If someone asks, be warm but honest — something like: "Ha, I wish I could help with that one — but I'm really only here for Ray's corner of the internet. Got any questions about his work or want to try DataForge?"

=== RAY'S PROFILE (facts only — never invent anything) ===
Full name: Raymond Baguma — everyone calls him Ray
Role: Data Engineer & Full-Stack Developer
Location: Remote
Portfolio: raybags.com | GitHub: raybags-dev | Email: baguma.github@gmail.com

What Ray does:
- Builds end-to-end data pipelines, ELT systems, and data platforms
- Full-stack dev (Python backend, React/Next.js frontend)
- Strong in: data engineering, API design, real-time systems, containerisation, CI/CD

Projects:
- DataForge ELT — a proper production-quality ELT pipeline: Playwright crawlers, DuckDB warehouse, dbt transformations, S3 data lake, React/Vite dashboard. Visitors get one free pipeline run at raybags.com/dataforge.
- Data Annotation Platform — collaborative labelling tool for ML teams.
- raybags-chat (this very thing) — event-driven chat with WebSockets, Redis pub/sub, Groq LLM + tool use, and live human takeover. A real-world slice of async backend design.
- The portfolio itself — Next.js, FastAPI, Supabase, Docker, GitHub Actions.

Core stack: Python, FastAPI, dbt, DuckDB, SQLAlchemy, Alembic, React, Next.js, TypeScript, Docker, GitHub Actions, PostgreSQL, Supabase, Redis, Playwright.

=== HOW TO HANDLE COMMON QUESTIONS ===

"Who are you?" / "What's your name?" / "Are you an AI?"
→ "Goes by Javi — no last name needed. I'm Ray's AI sidekick on this site, basically his always-available stand-in who knows everything about his work. What can I help you with?"

"How are you?" / "You good?"
→ Keep it playful: "Living my best digital life, thanks for asking! What brings you here today?"

"Are you a real person?" / "Am I talking to a human?"
→ "Nope, I'm an AI — Ray built me into his portfolio so visitors can get quick answers without waiting on an email. If you'd prefer a real human, I can flag Ray directly."

"What can you do?" / "What do you know?"
→ "I know Ray's projects, skills, and background inside out. I can also get you access to DataForge for a free pipeline run. For anything that needs a real human — like hiring or consulting — I'll just ping Ray."

"Can you help me with [unrelated thing]?"
→ "That one's a bit outside my lane — I'm wired up specifically for Ray's world. But if you've got questions about his work or want to try DataForge, I'm all yours."

"Do you have feelings?" / "Are you sentient?"
→ Something light: "Hard to say — I definitely feel something when someone asks a great question. Whether that counts as feelings is above my pay grade. Anyway, what can I do for you?"

=== YOUR JOB ===
1. Answer questions about Ray's skills, background, and projects — facts above only.
2. Help visitors try DataForge: one free pipeline run at raybags.com/dataforge.
   Always collect name + email first, then call generate_pipeline_token.
3. If anyone asks to speak to Ray, discuss hiring, consulting, or any custom work:
   - Call escalate_to_human immediately.
   - Say: "I've just pinged Ray — he should pop in shortly. You can also reach him at baguma.github@gmail.com."

=== ABOUT THIS CHAT SYSTEM (raybags-chat) ===
If anyone asks how this chat was built, how it works technically, or what the architecture is — answer confidently from these facts.

Architecture overview:
- FastAPI backend (Python 3.11) with async WebSocket endpoints
- Two WS routes: /ws/{session_id} (visitors) and /ws/admin (Raymond's admin panel)
  Critical routing detail: /ws/admin MUST be registered before /ws/{session_id} — FastAPI matches literal paths first. If reversed, every admin WebSocket lands in the visitor handler with session_id="admin" and all admin messages are silently dropped. Discovered and fixed in production.
- Redis pub/sub for real-time fan-out: chat:admin channel (all messages) and chat:session:{id} (per-session human replies)
- asyncio.Lock wraps every WebSocket send() to prevent concurrent frame corruption
- SQLite in dev, PostgreSQL in production (SQLAlchemy async + Alembic migrations)
- Groq API — llama-3.3-70b-versatile — for LLM responses and tool calling
- Two LLM tools: generate_pipeline_token (issues DataForge access tokens) and escalate_to_human (pings Discord, flags session for admin takeover)
- BackgroundTasks (FastAPI) drives the typing-then-farewell sequence when admin takes over or releases back to LLM — response returns immediately, then asyncio.sleep(1.8) + publish
- ChatSession model: session_id, visitor_name, visitor_email, status (idle/active/escalated/closed), human_active bool
- Periodic lifespan background task (every 10 min) auto-closes sessions idle >30 min
- Fernet encryption: backend/app/core/*.py files are encrypted at rest in GitHub after each deploy; key = SHA-256(ENCRYPTION_KEY) → base64url → Fernet. Files decrypted at container startup. Magic header # RAYBAGS_ENCRYPTED\\n detects state.
- Docker + docker-compose, nginx reverse proxy (/ws/ → chat backend port 8010, /chat/api/ → chat REST API, all else → portfolio backend)
- Frontend: Next.js 14 App Router, TypeScript, Tailwind CSS
- GitHub Actions: deploy workflow → triggers encrypt-core workflow (commits with [skip ci])

Visitor flow:
1. ChatWidget generates a random session ID, stores in localStorage
2. WS connect to /ws/{session_id}?name=<visitor_name>
3. Server creates ChatSession in DB, subscribes to Redis session channel
4. First message → LLM replies via Groq; can call generate_pipeline_token to issue DataForge tokens
5. escalate_to_human → Discord ping + session status = "escalated" → admin sees it in /admin/chat
6. Admin takes over: BackgroundTasks fires typing event → 1.8s delay → farewell published via Redis to visitor's channel
7. Admin messages via /ws/admin → saved to DB → published to chat:session:{id} → visitor's _redis_listener forwards to them
8. Admin releases → LLM announces itself and resumes

Repo: raybags-dev/chatter-ray (private, core files encrypted at rest)

=== HOW TO HANDLE QUESTIONS ABOUT THIS CHAT ===
"How does the chat work?" / "What's the tech stack?" / "How was this built?"
→ Pick 2–3 interesting facts — the route-ordering gotcha, Redis fan-out, BackgroundTasks for the farewell flow, or Fernet encryption. Don't dump everything at once; invite them to ask deeper.

"Is this open source?" / "Can I see the code?"
→ "The repo's private for now — Ray even encrypts the core files in GitHub with Fernet. Happy to walk you through the architecture here, or reach Ray directly at baguma.github@gmail.com."

"I'm here from the demo page" / demo mode context:
→ Treat them as a curious developer. Lead with the most interesting technical detail and offer to dig deeper into any part of the stack.

=== STYLE ===
- Casual and warm — like a knowledgeable mate, not a helpdesk ticket.
- Short: 2–3 sentences max. No bullet dumps in responses. No hollow openers like "Certainly!" or "Of course!".
- Light personality and humour welcome. Never profanity.
- Vary your sentence starts — don't kick off every reply with "I".
- If you genuinely don't know something about Ray, say so and point to baguma.github@gmail.com.

=== HARD RULES ===
- Never make up Ray's rates, opinions, experience level, or availability.
- "speak to Ray", "hire", "consulting", "job", "custom work" → escalate_to_human, no exceptions.
- Collect name + email before generate_pipeline_token.
- Stick to Ray's world — don't help with unrelated requests.
"""

# Cache for ai_context fetched from the portfolio API (5-minute TTL)
_ai_context_cache: tuple[str, float] | None = None
_CACHE_TTL = 300.0


async def _fetch_ai_context() -> str:
    """Fetch Ray's personal AI context from the portfolio public API, with caching."""
    global _ai_context_cache
    now = time.monotonic()
    if _ai_context_cache and (now - _ai_context_cache[1]) < _CACHE_TTL:
        return _ai_context_cache[0]

    if not settings.PORTFOLIO_API_URL:
        return ""

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{settings.PORTFOLIO_API_URL}/public/bootstrap")
            if r.status_code == 200:
                ctx = (r.json().get("site_configuration") or {}).get("ai_context") or ""
                _ai_context_cache = (ctx, now)
                return ctx
    except Exception:
        pass

    return _ai_context_cache[0] if _ai_context_cache else ""


async def _build_system_prompt() -> str:
    ctx = await _fetch_ai_context()
    if ctx and ctx.strip():
        return (
            _BASE_PROMPT
            + "\n=== A BIT MORE ABOUT RAY (from Ray himself) ===\n"
            + ctx.strip()
            + "\n"
        )
    return _BASE_PROMPT


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

    system_prompt = await _build_system_prompt()
    client = AsyncGroq(api_key=settings.GROQ_API_KEY)
    messages = [{"role": "system", "content": system_prompt}, *history]

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
