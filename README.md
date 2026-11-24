# Insta-Poster

Self-hosted **Webapp + CLI + REST API + Python SDK (Code Mode)** to manage a local image pool and let humans or AI agents schedule and publish **Instagram Feed Posts, Carousels, and Stories (9:16)**.

Built with FastAPI, SQLite, Pillow, and React (styled with the **EDKA Schematic / Blueprint UI** and a built-in **English / Spanish** language switcher).

---

## Key Features

- **Managed Image Pool (`data/pool/` + SQLite)**
  - Drag-and-drop upload from the web UI or local folder synchronization with automatic SHA-256 deduplication.
  - Tags, agent-readable notes/descriptions, per-format usage counters (Post vs. Story), and smart candidate selection (`pool pick`).
- **Automatic Instagram Media Preparation (`Pillow`)**
  - Converts any image (JPG, PNG, WEBP, BMP, TIFF) into Instagram-compliant RGB JPEGs.
  - Adapts aspect ratios for Feed (`1:1`, `4:5`, `1.91:1`) and Stories (`1080×1920`) with `auto`, `pad_blur`, `crop`, or `pad_solid` fit modes and real-time preview before publishing.
- **Swappable Publishing Adapters**
  - `dry_run` *(default)*: safe local simulation that writes rendered JPEGs and JSON receipts to `data/published_dry_run/` without touching Instagram.
  - `graph_api` *(official Meta API)*: publishes posts, carousels, and stories via `graph.instagram.com` (Instagram Login, no Facebook Page required) or `graph.facebook.com`. Supports temporary hosting via ImgBB or direct HTTPS delivery with automatically HMAC-signed `/media/prepared/*` URLs.
- **Built-In Multi-User Authentication (Dokploy-Ready)**
  - Protects the Web UI, REST API (`/api/*`), OpenAPI/Swagger (`/docs`), and all media endpoints (`/media/*`). Only `/api/health` remains public for healthchecks.
  - Single-tier multi-user model (all users share full access), `scrypt` password hashing, SQLite-backed `HttpOnly` session cookies (auto-`Secure` behind Traefik/HTTPS), login rate limiting, plus `X-API-Key` and `Authorization: Bearer` support for AI agents.
- **Agent-First Interfaces (CLI + REST API + Code Mode)**
  - Every CLI command supports `--json`.
  - OpenAPI spec at `/openapi.json`, interactive docs at `/docs`, and a token-efficient discovery endpoint at `/api/agent/schema`.
  - Ergonomic Python SDK (`InstaPoster.local()` / `InstaPoster.http(...)`). See [AGENT_GUIDE.md](AGENT_GUIDE.md).
- **Bilingual Schematic UI (`EN // ES`)**
  - Architectural blueprint light interface with live English / Spanish switching directly from the login screen or sidebar.

---

## Quick Start (Local)

### Prerequisites
- Python `>= 3.11` and [`uv`](https://docs.astral.sh/uv/)
- Node.js `>= 18` and `npm` (for building the React frontend)

### Setup & Run

```bash
# 1. Install Python dependencies
uv sync --extra dev

# 2. Build the React frontend (output is served automatically by FastAPI)
cd frontend && npm install && npm run build && cd ..

# 3. Start the web app, REST API, and background scheduler
uv run insta-poster serve
```

Open `http://127.0.0.1:8000` in your browser. On first launch (if no users exist yet), you will be prompted to create the initial account.

For frontend development with hot reload:

```bash
# Terminal 1 — Backend API
uv run insta-poster serve --reload

# Terminal 2 — Vite dev server (proxies /api, /media, /docs to :8000)
cd frontend && npm run dev
```

---

## Authentication & Dokploy Deployment

Because all routes (except `/api/health`) are protected at the application layer, you can safely deploy Insta-Poster behind Dokploy / Traefik without external HTTP Basic Auth.

### First-User Provisioning

You have two options:

1. **Automatic bootstrap via environment variables (recommended for Dokploy):**
   Set `INSTA_POSTER_INIT_USER` and `INSTA_POSTER_INIT_PASSWORD` before starting the container. If the `users` table is empty, the initial account is created automatically on startup and public setup is immediately closed. Subsequent restarts will **not** overwrite the password once the user exists.
2. **Interactive first-run setup:**
   If no users exist in the database, opening the web UI displays an **Initial Setup** form to create the first account. As soon as the first user is created, `/api/auth/setup` is permanently locked (`403 Forbidden`).

### Recommended Dokploy Configuration

- **Port:** `8000`
- **Healthcheck path:** `/api/health`
- **Persistent Volume:** mount a persistent volume to `data/` (or wherever `INSTA_POSTER_DATA_DIR` points) so your SQLite DB, users, image pool, and adapter settings survive redeploys.
- **HTTPS / Reverse Proxy:** session cookies automatically enable the `Secure` attribute when `X-Forwarded-Proto: https` is present (Traefik sets this by default).
- **Meta Graph API without ImgBB:** if you deploy on a public HTTPS domain (e.g. `https://insta.yourdomain.com`), you can set that domain as `Public Base URL` in `graph_api` settings. Prepared images served under `/media/prepared/*` remain protected by authentication, while `GraphAPIAdapter` appends short-lived HMAC signatures (`?exp=...&sig=...`) so Meta's servers can fetch them securely.

### Managing Users

Manage accounts anytime from the **Users** tab in the Web UI or via the CLI:

```bash
uv run insta-poster user list
uv run insta-poster user create alice
uv run insta-poster user passwd alice
uv run insta-poster user delete alice
```

---

## Environment Variables

Copy [.env.example](.env.example) as a reference when configuring your environment:

| Variable | Default | Description |
| --- | --- | --- |
| `INSTA_POSTER_HOST` | `127.0.0.1` | Bind address for `insta-poster serve` (set `0.0.0.0` in Docker/Dokploy). |
| `INSTA_POSTER_PORT` | `8000` | HTTP port for the web UI and REST API. |
| `INSTA_POSTER_DATA_DIR` | `./data` | Directory for SQLite DB, image pool, thumbnails, and renders. |
| `INSTA_POSTER_INIT_USER` | *(unset)* | Username to auto-seed on first startup when 0 users exist. |
| `INSTA_POSTER_INIT_PASSWORD` | *(unset)* | Password for the auto-seeded initial user (min. 6 characters). |
| `INSTA_POSTER_API_KEY` | *(unset)* | Optional static API key accepted via `X-API-Key` header for agents. |
| `INSTA_POSTER_SESSION_TTL_DAYS` | `30` | Session cookie/token lifetime in days (`1`–`365`). |
| `INSTA_POSTER_SECRET_KEY` | *(auto)* | Secret for HMAC-signed `/media/prepared/*` URLs (auto-persisted in SQLite if unset). |
| `INSTA_POSTER_COOKIE_SECURE` | *(auto)* | Force `Secure` cookie flag (`true` / `false`); auto-detected from HTTPS otherwise. |
| `INSTA_POSTER_CORS_ORIGINS` | `localhost` | Comma-separated list of allowed CORS origins. |

---

## CLI, REST API & Python SDK

### CLI (Humans & AI Agents)

```bash
# Import a local folder into the image pool (deduplicated by SHA-256)
uv run insta-poster pool sync /path/to/photos --tags campaign,summer --json

# Pick an unused candidate image for a Story
uv run insta-poster pool pick --type story --unused --json

# Publish a Story immediately (uses dry_run by default)
uv run insta-poster story create --image <ID> --fit pad_blur --now --json

# Publish or schedule a Feed Post or Carousel
uv run insta-poster post create --image <ID> --caption "Hello world" --hashtags "#launch,#ig" --now --json
uv run insta-poster post create --image <ID1> --image <ID2> --caption "Carousel" --schedule "2026-11-26T18:00:00Z" --json

# Inspect adapter health
uv run insta-poster adapter list --json
```

### REST API Authentication

Authenticate automated HTTP clients in one of two ways:

1. **API Key:** configure `INSTA_POSTER_API_KEY` and send `X-API-Key: <key>`.
2. **User Session Token:** call `POST /api/auth/login` with `{"username": "...", "password": "..."}` and send `Authorization: Bearer <token>`.

### Python SDK (Code Mode)

```python
from insta_poster import InstaPoster

# Direct local SQLite/filesystem access (no server needed)
ip = InstaPoster.local()

# Or remote HTTP access against your Dokploy instance
# ip = InstaPoster.http("https://insta.yourdomain.com", api_key="YOUR_API_KEY")
# ip = InstaPoster.http("https://insta.yourdomain.com", username="admin", password="...")

img = ip.pick_images(pub_type="story", unused_only=True)[0]
story = ip.create_story(img["id"], fit_mode="pad_blur", publish_now=True)
print(story["status"], story["external_id"])
```

---

## Testing

```bash
uv run pytest -v
```

---

## Security & Version Control Notes

- Never commit the `data/` directory, `*.db` files, `.env` files, or `*session*.json` files — they contain your SQLite database, adapter tokens, session hashes, and user credentials. All of these are ignored in [.gitignore](.gitignore).
- Built frontend assets (`backend/insta_poster/static/`) are also gitignored and generated via `cd frontend && npm run build`.
