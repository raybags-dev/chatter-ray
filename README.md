# raybags-chat

Real-time chat system for [raybags.com](https://raybags.com/chat) — event-driven architecture with a Groq-powered LLM agent, Redis pub/sub message routing, WebSocket connections, and seamless human takeover.

Built as a portfolio project to showcase event-driven system design alongside the [DataForge ELT](https://raybags.com/dataforge) pipeline.

---

## Architecture

```
Visitor browser                  VPS (raybags.com)
───────────────                  ─────────────────────────────────────────────────────
ChatWidget (React)
  │  WebSocket /ws/{session_id}  ┌─ Nginx (portfolio-base) ─────────────────────────┐
  └──────────────────────────────►  /ws/*  → chat-backend:8010                       │
                                 │  /chat/* → chat-frontend:8011                     │
Admin browser                   └──────────────────────────────────────────────────-─┘
  │  WebSocket /ws/admin?token=…       │               │
  └────────────────────────────────────┤           chat-frontend
                                       │           (Vite React SPA, port 8011)
                                 chat-backend (FastAPI, port 8010)
                                   │     │
                             Groq LLM    Redis pub/sub
                             (llama-3.3) (session channels + admin channel)
                                   │
                             Supabase Postgres (shared with portfolio-base)
                             chat_sessions + chat_messages tables
```

**Message flow**
1. Visitor sends a message → saved to Postgres → published to `chat:admin` Redis channel.
2. If `session.human_active` is false, the Groq LLM agent runs and replies.
3. If the agent calls `escalate_to_human`, a Discord webhook fires and `human_active` flips to `true`.
4. Admin connects to `/ws/admin`, sees all live messages, and can reply directly to any session via `chat:session:{id}` Redis channel.

---

## Features

- **LLM agent** (Groq `llama-3.3-70b-versatile`) answers questions about Ray's background, projects, and skills.
- **Tool use** — the agent can issue DataForge pipeline tokens and escalate to a human without the visitor noticing any seam.
- **Human takeover** — admin WebSocket + Redis routing lets Ray jump into any session mid-conversation.
- **Persistent sessions** — `sessionStorage` key keeps the chat history across page refreshes.
- **Floating chat widget** embedded on raybags.com (no page reload).

---

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic |
| Real-time | WebSockets, Redis pub/sub (asyncio) |
| LLM | Groq Cloud API, `llama-3.3-70b-versatile`, tool use |
| Frontend | React 18, Vite, Tailwind CSS |
| Database | Supabase Postgres (shared with portfolio-base) |
| Deploy | Docker, GitHub Actions → VPS via SSH |
| Proxy | Nginx (portfolio-base) with `/ws/` and `/chat/` location blocks |

---

## Local development

### Prerequisites

- Python 3.12+
- Node 20+
- Docker (for Redis)
- A Groq API key — free at [console.groq.com](https://console.groq.com)

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Start Redis
docker run -d --name chat-redis -p 6379:6379 redis:7-alpine

# Copy and fill in environment
cp ../.env .env   # or create manually (see Environment Variables below)

# Run migrations
alembic upgrade head

# Start API server
uvicorn app.main:app --reload --port 8010
```

### Frontend

```bash
cd frontend
npm install
npm run dev   # starts on http://localhost:3001
```

The chat widget is embedded in the portfolio-base frontend at `raybags.com`. For standalone local testing, visit `http://localhost:3001`.

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | yes | — | Postgres async URL (`postgresql+asyncpg://…`) |
| `REDIS_URL` | yes | `redis://localhost:6379/1` | Redis connection URL |
| `SECRET_KEY` | yes | — | App secret key (shared with portfolio-base) |
| `GROQ_API_KEY` | yes | — | Groq Cloud API key |
| `GROQ_MODEL` | no | `llama-3.3-70b-versatile` | Groq model ID |
| `PORTFOLIO_API_URL` | no | `https://raybags.com/api/v1` | Portfolio backend URL (for token issuance) |
| `PORTFOLIO_ADMIN_TOKEN` | yes | — | Admin JWT (shared with portfolio-base, used for WS auth + service calls) |
| `DISCORD_WEBHOOK` | no | — | Discord webhook URL for escalation notifications |
| `CORS_ORIGINS` | no | `http://localhost:3000,https://raybags.com` | Comma-separated allowed origins |

---

## API reference

### REST

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | — | Liveness check |
| `GET` | `/api/v1/sessions` | `?token=` | List chat sessions |
| `GET` | `/api/v1/sessions/{id}` | `?token=` | Session detail + messages |
| `POST` | `/api/v1/sessions/{id}/takeover` | `?token=` | Enable human mode |
| `POST` | `/api/v1/sessions/{id}/release` | `?token=` | Return to LLM |

### WebSocket

| Path | Description |
|---|---|
| `/ws/{session_id}` | Visitor connection — receives agent/human messages |
| `/ws/admin?token=` | Admin panel — receives all session messages, can reply |

**Visitor message format**
```json
{ "type": "msg", "content": "Hello!" }
```

**Server envelope**
```json
{ "type": "msg", "sender": "agent", "content": "Hey!", "session_id": "abc", "ts": 1234567890.1 }
```

---

## Testing

```bash
cd backend
pytest -v
```

The test suite covers:
- **Unit** — Redis channel helpers, LLM agent with mocked Groq (text reply, token tool, escalation tool, no-key fallback)
- **Integration** — health endpoint, sessions REST API (auth, CRUD, takeover/release), WebSocket admin auth

Tests use an in-memory SQLite database (no external DB needed) and mock all external services (Groq, Redis, Discord, portfolio API).

---

## Deployment

**First time:**
```bash
./scripts/setup-vps.sh          # creates /opt/raybags-chat on the VPS
```

**Every deploy:**
```bash
./scripts/deploy.sh "feat: my change"
```

This commits and pushes to `main`. GitHub Actions:
1. Builds Docker images for backend + frontend and pushes to Docker Hub.
2. SSHs to the VPS, writes `.env.prod`, pulls images, runs `alembic upgrade head`, starts services.

The portfolio-base Nginx proxy routes `/ws/*` and `/chat/*` to this service automatically.

---

## Live

- **Chat widget**: [raybags.com](https://raybags.com) (bottom-right)
- **DataForge ELT**: [raybags.com/dataforge](https://raybags.com/dataforge)
- **Portfolio**: [raybags.com](https://raybags.com)
