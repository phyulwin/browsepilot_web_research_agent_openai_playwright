# backend/main.py — same-origin FastAPI service, authenticated workspace, SSE, and static frontend.
import asyncio
import hashlib
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.agent import AgentManager, report_markdown
from backend.config import DOTENV_FILE, settings
from backend.models import Approval, ResearchRequest
from backend.storage import Store, now

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    # Recover interrupted work once and keep all background jobs owned by this application.
    logger.info("OpenAI API key source: %s (%s); system API keys are ignored", DOTENV_FILE, "configured" if settings.openai_api_key else "missing or empty")
    app.state.store = Store()
    app.state.manager = AgentManager(app.state.store)
    app.state.login_attempts = {}
    try:
        yield
    finally:
        await app.state.manager.shutdown()


app = FastAPI(title="BrowsePilot", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def authorized(request):
    # Authenticate a signed, short-lived HttpOnly cookie without sending the password to JavaScript storage.
    if not settings.access_password:
        return True
    try:
        # Bearer access avoids third-party cookie restrictions on GitHub Pages.
        header = request.headers.get("authorization", "")
        token = header[7:] if header.startswith("Bearer ") else request.cookies.get("browsepilot_access", "")
        expiry, signature = token.split(".", 1)
        expected = hmac.new(settings.access_password.encode(), expiry.encode(), hashlib.sha256).hexdigest()
        return time.time() < int(expiry) and hmac.compare_digest(signature, expected)
    except (ValueError, TypeError):
        return False


@app.middleware("http")
async def security(request: Request, call_next):
    # Gate all research data and prevent cross-origin requests from starting paid work.
    if request.url.path.startswith("/api/"):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            allowed = {str(request.base_url).rstrip("/"), *filter(None, settings.allowed_origins.split(","))}
            if origin and origin not in allowed:
                return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site" and origin not in allowed:
                return JSONResponse({"detail": "Cross-site request rejected"}, status_code=403)
            try:
                if int(request.headers.get("content-length", "0")) > 16000:
                    return JSONResponse({"detail": "Request too large"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Invalid request size"}, status_code=400)
        if request.url.path not in {"/api/health", "/api/auth", "/api/login"} and not authorized(request):
            return JSONResponse({"detail": "Sign in to access this research workspace"}, status_code=401)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
async def health():
    # A liveness probe deliberately exposes neither model configuration nor secret values.
    return {"status": "ok"}


@app.get("/api/auth")
async def auth_status(request: Request):
    # Let the client render an access screen before requesting protected session data.
    return {"authenticated": authorized(request), "required": bool(settings.access_password)}


class Login(BaseModel):
    """Bound the workspace password payload."""

    password: str = Field(max_length=256)


@app.post("/api/login")
async def login(body: Login, request: Request):
    # Apply a per-address attempt window and issue an eight-hour signed access cookie.
    address = request.client.host if request.client else "unknown"
    attempts = app.state.login_attempts
    cutoff = time.monotonic() - 60
    for host in list(attempts):
        attempts[host] = [value for value in attempts[host] if value > cutoff]
        if not attempts[host]:
            del attempts[host]
    recent = attempts.setdefault(address, [])
    if len(recent) >= 5:
        raise HTTPException(429, "Too many sign-in attempts; try again in one minute")
    recent.append(time.monotonic())
    if not hmac.compare_digest(body.password, settings.access_password):
        raise HTTPException(401, "Incorrect workspace password")
    expiry = str(int(time.time()) + 8 * 3600)
    signature = hmac.new(settings.access_password.encode(), expiry.encode(), hashlib.sha256).hexdigest()
    response = JSONResponse({"authenticated": True, "access_token": expiry + "." + signature})
    response.set_cookie("browsepilot_access", expiry + "." + signature, httponly=True, secure=settings.cookie_secure, samesite="strict", max_age=8 * 3600)
    return response


# CORS is outermost so preflight and authentication failures receive exact-origin headers.
app.add_middleware(CORSMiddleware, allow_origins=[item.strip() for item in settings.allowed_origins.split(",") if item.strip()],
                   allow_credentials=False, allow_methods=["GET", "POST"],
                   allow_headers=["Authorization", "Content-Type"], expose_headers=["Content-Disposition"])


@app.post("/api/logout")
async def logout():
    # Revoke this browser's local access cookie without affecting running research.
    response = JSONResponse({"authenticated": False})
    response.delete_cookie("browsepilot_access")
    return response


@app.get("/api/config")
async def configuration():
    # Return only operational settings appropriate for the user interface.
    return {"configured": bool(settings.openai_api_key), "model": settings.openai_model, "max_steps": settings.max_steps, "max_runtime_seconds": settings.max_runtime_seconds, "max_concurrent_sessions": settings.max_concurrent_sessions, "max_session_tokens": settings.max_session_tokens, "access_protected": bool(settings.access_password)}


def get_session(session_id):
    # Resolve IDs through SQLite before using them in any filename or response.
    session = app.state.store.get(session_id)
    if not session:
        raise HTTPException(404, "Research session not found")
    return session


def snapshot(session_id):
    # An SSE reconnect receives a complete durable snapshot and its ordered audit events.
    return {**get_session(session_id), "events": app.state.store.events(session_id)}


def ensure_capacity():
    # Reject new work before making API calls when configuration or resource limits prevent it.
    if not settings.openai_api_key:
        raise HTTPException(503, "Set OPENAI_API_KEY in the project's .env file and restart the backend to enable research planning")
    if app.state.manager.busy():
        raise HTTPException(429, "All research slots are busy; wait for an active session to finish")


@app.get("/api/sessions")
async def sessions():
    # Keep history lightweight; detailed evidence and logs load only when a session is opened.
    return [{key: session.get(key) for key in ("id", "title", "prompt", "status", "created_at", "completed_at", "action_count")} for session in app.state.store.list_all(50)]


@app.post("/api/sessions", status_code=202)
async def create_session(body: ResearchRequest):
    # Persist the request first; the asynchronous planner never starts a browser.
    ensure_capacity()
    session = {"id": uuid4().hex, "prompt": body.prompt.strip(), "title": "Planning your research", "objective": "", "status": "planning", "message": "Turning your question into a research plan", "steps": [], "evidence": [], "report": None, "error": None, "active_url": "", "page_title": "", "created_at": now(), "started_at": None, "completed_at": None, "action_count": 0, "screenshot_version": 0, "revision": 1, "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "budget_tokens": 0}}
    if len(session["prompt"]) < 10:
        raise HTTPException(422, "Please provide at least ten non-whitespace characters")
    app.state.store.save(session)
    app.state.manager.launch(session, planning=True)
    return snapshot(session["id"])


@app.get("/api/sessions/{session_id}")
async def session_detail(session_id: str):
    # Reopening a session uses the same response contract as live progress updates.
    return snapshot(session_id)


@app.post("/api/sessions/{session_id}/regenerate", status_code=202)
async def regenerate(session_id: str):
    # Invalidate earlier approvals and keep regeneration inside the original token allowance.
    session = get_session(session_id)
    if session["status"] != "awaiting_approval":
        raise HTTPException(409, "Only an unapproved plan can be regenerated")
    ensure_capacity()
    session.update(status="planning", revision=session["revision"] + 1, message="Revising your research plan", steps=[])
    app.state.store.save(session)
    app.state.manager.launch(session, planning=True)
    return snapshot(session_id)


@app.post("/api/sessions/{session_id}/approve", status_code=202)
async def approve(session_id: str, body: Approval):
    # Compare revision and state atomically within this single event-loop worker.
    session = get_session(session_id)
    if session["status"] != "awaiting_approval" or session["revision"] != body.revision:
        raise HTTPException(409, "This plan changed or has already started; refresh it before approving")
    ensure_capacity()
    session.update(status="running", started_at=now(), message="Launching your isolated research browser")
    app.state.store.event(session_id, "approval", {"message": "User approved the research plan", "revision": body.revision})
    app.state.store.save(session)
    app.state.manager.launch(session)
    return snapshot(session_id)


@app.post("/api/sessions/{session_id}/stop")
async def stop(session_id: str):
    # Stopping is idempotent and preserves already collected evidence and audit history.
    get_session(session_id)
    await app.state.manager.stop(session_id)
    return snapshot(session_id)


@app.get("/api/sessions/{session_id}/events")
async def events(session_id: str, request: Request):
    # Send persisted state on every revision and heartbeat comments during long model calls.
    get_session(session_id)

    async def stream():
        version = -1
        heartbeat = time.monotonic()
        while not await request.is_disconnected():
            session = get_session(session_id)
            if session["version"] != version:
                version = session["version"]
                yield f"id: {version}\nevent: snapshot\ndata: {json.dumps(snapshot(session_id))}\n\n"
                if session["status"] not in {"planning", "running", "reporting"}:
                    break
            elif time.monotonic() - heartbeat > 10:
                heartbeat = time.monotonic()
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.6)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@app.get("/api/sessions/{session_id}/screenshot")
async def screenshot(session_id: str):
    # Return only the authenticated session's latest browser-captured JPEG.
    session = get_session(session_id)
    path = settings.data_dir / f"{session['id']}.jpg"
    if not path.exists():
        raise HTTPException(404, "No browser screenshot has been captured yet")
    return Response(path.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/sessions/{session_id}/report.md")
async def download_report(session_id: str):
    # Generate the copy/download representation from the same report shown in the UI.
    session = get_session(session_id)
    if not session["report"]:
        raise HTTPException(409, "A report is not available for this session")
    return Response(report_markdown(session), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="browsepilot-{session_id[:8]}.md"'})


# Mount only the built public directory; API paths never fall back to the application shell.
if (settings.frontend_dir / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=settings.frontend_dir / "assets"), name="assets")


@app.get("/{path:path}")
async def frontend(path: str):
    # Resolve paths against the static root to prevent traversal or exposing server-side files.
    if path.startswith("api/"):
        raise HTTPException(404, "API endpoint not found")
    root = settings.frontend_dir.resolve()
    candidate = (root / path).resolve()
    if candidate.is_relative_to(root) and candidate.is_file():
        return FileResponse(candidate)
    if (root / "index.html").exists():
        return FileResponse(root / "index.html")
    raise HTTPException(503, "Build the frontend with npm run build in frontend/ first")
