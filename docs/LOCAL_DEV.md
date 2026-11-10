# LOCAL SETUP & DOCKER IMAGE

## Quick start: Docker

Prerequisites: Docker Engine/Desktop running Linux containers and an OpenAI API key with available API credits; a ChatGPT subscription does not substitute for API billing.

1. Copy `.env.example` to `.env` and set `OPENAI_API_KEY`.
2. Start Docker and run the following commands from the repository.
3. Open [http://localhost:8000](http://localhost:8000), submit a question, review the plan, and select **Start research**.

```sh
# Build the frontend and install Python plus Chromium inside the container.
docker compose up --build -d

# Inspect application startup and research errors.
docker compose logs -f browsepilot
```

Compose binds to loopback by default; deployed users need only a normal web browser, with no local Python, Node, Playwright, Chrome, or Edge installation.

```sh
# Stop the service while retaining research in the named volume.
docker compose down
```

Do not add `--volumes` unless you intend to erase research data.

## Local development

Developers need Python 3.11+ and Node 22 (the Docker build version).

### Windows PowerShell

```powershell
# Prepare isolated backend dependencies and the server-side browser.
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item .env.example .env
# Edit .env and set OPENAI_API_KEY.

# Build and serve the React frontend from FastAPI.
npm ci --prefix frontend
npm run build --prefix frontend
.\.venv\Scripts\python.exe -m backend.run
```

### macOS / Linux

```sh
# Prepare backend dependencies and Chromium, including required system packages.
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium
cp .env.example .env
# Edit .env and set OPENAI_API_KEY.

# Build and serve the application.
npm ci --prefix frontend
npm run build --prefix frontend
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

For frontend hot reload, run `npm run dev --prefix frontend` in another terminal and open [http://127.0.0.1:5173](http://127.0.0.1:5173); Vite proxies `/api` to port 8000.

On Windows, use `python -m backend.run` from the repository root (with the virtual environment's Python as shown above); optional `--host` and `--port` arguments are supported. The launcher creates a top-level Proactor event loop before starting FastAPI and calls Uvicorn's asynchronous server on that loop, without nested loops or request-time policy changes. Restart the backend manually after Python edits; this launcher deliberately does not accept `--reload` or `--workers`.

The pinned Uvicorn version selects a Selector loop on Windows when reload or multiple workers enable its subprocess supervisor. Windows Selector loops cannot run the subprocess transport required by Playwright, while Proactor loops can ([Python asyncio platform documentation](https://docs.python.org/3/library/asyncio-platforms.html)). `WindowsSelectorEventLoopPolicy` likewise creates incompatible loops; setting `WindowsProactorEventLoopPolicy` inside an already-running FastAPI app cannot replace its active loop, and Uvicorn's explicit loop factory can override policy-based defaults. The dedicated launcher avoids both issues; Linux and Docker keep their existing startup commands. This fix was implemented by source inspection only, without tests or runtime verification.

## Environment variables

Cloud Run's dedicated Docker target explicitly sets `BROWSEPILOT_ENV_FILE=/secrets/browsepilot/env` to select a Secret Manager dotenv mount instead of the default project `.env`; it still never falls back to a process `OPENAI_API_KEY`. This deployment-specific override is described in [DEPLOYMENT.md](DEPLOYMENT.md).

`OPENAI_API_KEY` is read **only from the `.env` file in the project root**, resolved relative to `backend/config.py`; system, terminal, and IDE environment keys cannot override it. Enter the literal key, not a `${VARIABLE}` reference; missing or blank values disable planning instead of falling back to another credential. Restart the backend after changing `.env`; startup logs show the credential file path and whether a key is configured, never the key itself. Other settings retain normal environment-variable precedence; all secrets remain on the server.

Docker Compose mounts `.env` read-only at `/app/.env`; keep it readable by container UID 10001 and never bake it into the image. For other container hosts, mount a secret file at `/app/.env` containing `OPENAI_API_KEY=...`; an environment-only API key is intentionally ignored. A correct key can still receive HTTP 429 for account quota or rate limits; changing the credential source does not itself resolve those account limits.

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | empty | Required for planning and execution |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Accessible model supporting Responses structured outputs |
| `APP_ENV` | `development` | Production requires a password of at least 16 characters |
| `ACCESS_PASSWORD` | empty | Shared workspace access gate |
| `COOKIE_SECURE` | `false` | Set true for deployed HTTPS |
| `ALLOWED_ORIGINS` | empty | Exact comma-separated browser origins when required behind a proxy |
| `MAX_STEPS` | `18` | Browser action limit |
| `MAX_RUNTIME_SECONDS` | `240` | Startup, browsing, extraction, and synthesis deadline |
| `MAX_CONCURRENT_SESSIONS` | `2` | Combined planning/execution job capacity |
| `MAX_SESSION_TOKENS` | `60000` | Conservative allowance including regeneration and retries |
| `MAX_MODEL_CALLS` | `32` | Request attempts, including retries |
| `API_TIMEOUT_SECONDS` | `35` | One OpenAI request timeout |
| `SEARCH_PROVIDERS` | `bing,duckduckgo,google` | Ordered public search providers; comma-separated subset of these names |
| `DATA_DIR` | `data` | SQLite/screenshots; `/app/data` in Docker |
| `FRONTEND_DIR` | `frontend/dist` | Built frontend path |
| `PORT` | `8000` in Docker | Container HTTP listener |

Before each model call, a conservative input/schema/output token allowance is reserved; actual usage replaces it after a successful response, while failed-call estimates remain reserved. This can stop a session early and is not a dollar-denominated spending cap; configure project billing controls separately.
