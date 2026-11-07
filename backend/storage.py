# backend/storage.py — SQLite session snapshots and append-only chronological audit events.
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from backend.config import settings


def now() -> str:
    # Store timezone-aware UTC timestamps consistently across all application events.
    return datetime.now(timezone.utc).isoformat()


class Store:
    """Keep short SQLite transactions on one process with durable WAL storage."""

    def __init__(self):
        # Prepare the persistent volume and recover sessions interrupted by a restart.
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = settings.data_dir / "browsepilot.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, updated_at TEXT, body TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, created_at TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS events_session ON events(session_id, id)")
        for session in self.list_all():
            if session["status"] in {"planning", "running", "reporting"}:
                session.update(status="failed", error="Server restarted before this research finished.", completed_at=now())
                for step in session["steps"]:
                    if step["status"] in {"pending", "in_progress"}:
                        step.update(status="skipped", detail="Interrupted by server restart")
                self.save(session)
                self.event(session["id"], "error", {"message": session["error"]})

    @contextmanager
    def connect(self):
        # Close every connection explicitly; SQLite's transaction context alone does not close it.
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, session_id: str):
        # Parameterized lookups prevent request data from becoming SQL syntax.
        with self.connect() as db:
            row = db.execute("SELECT body FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_all(self, limit: int | None = None):
        # Return full records for recovery or a bounded history page for clients.
        with self.connect() as db:
            rows = db.execute("SELECT body FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit or -1,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save(self, session: dict):
        # Bump the revision used by SSE to detect durable state changes.
        session["updated_at"] = now()
        session["version"] = session.get("version", 0) + 1
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, body=excluded.body", (session["id"], session["updated_at"], json.dumps(session)))

    def event(self, session_id: str, kind: str, body: dict):
        # Audit errors and browser commands independently from the latest snapshot.
        with self.connect() as db:
            db.execute("INSERT INTO events(session_id, created_at, kind, body) VALUES (?, ?, ?, ?)", (session_id, now(), kind, json.dumps(body)))

    def events(self, session_id: str):
        # Preserve event order when reopening a completed research workspace.
        with self.connect() as db:
            rows = db.execute("SELECT id, created_at, kind, body FROM events WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
        return [{"id": row[0], "created_at": row[1], "kind": row[2], **json.loads(row[3])} for row in rows]
