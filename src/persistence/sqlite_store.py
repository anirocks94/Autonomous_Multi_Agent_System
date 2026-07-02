"""SQLite store for structured workflow history."""
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from config import Config


class SQLiteStore:
    """Thread-safe SQLite storage for workflow records and live events."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or Config.SQLITE_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    # ── Schema ────────────────────────────────────────────────────────

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS workflows (
                    session_id   TEXT PRIMARY KEY,
                    error_type   TEXT,
                    error_message TEXT,
                    status       TEXT,
                    confidence   REAL,
                    strategy     TEXT,
                    file_path    TEXT,
                    investigation_json TEXT,
                    fix_json     TEXT,
                    decisions_json TEXT,
                    pr_url       TEXT,
                    pr_number    INTEGER,
                    failure_reason TEXT,
                    created_at   TEXT,
                    updated_at   TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS workflow_events (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id   TEXT NOT NULL,
                    node_name    TEXT NOT NULL,
                    data_json    TEXT,
                    timestamp    TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES workflows(session_id)
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path), check_same_thread=False)

    # ── Workflow CRUD ─────────────────────────────────────────────────

    def save_workflow(self, state: Dict[str, Any]) -> None:
        """Upsert a complete workflow record from DebugState."""
        now = datetime.now().isoformat()
        error = state.get("error_event", {})
        inv = state.get("investigation_output")
        fix = state.get("fix_output")
        code_ctx = state.get("code_context", {})

        with self._lock, self._connect() as conn:
            conn.execute("""
                INSERT INTO workflows
                    (session_id, error_type, error_message, status, confidence,
                     strategy, file_path, investigation_json, fix_json,
                     decisions_json, pr_url, pr_number, failure_reason,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    status=excluded.status,
                    confidence=excluded.confidence,
                    strategy=excluded.strategy,
                    investigation_json=excluded.investigation_json,
                    fix_json=excluded.fix_json,
                    decisions_json=excluded.decisions_json,
                    pr_url=excluded.pr_url,
                    pr_number=excluded.pr_number,
                    failure_reason=excluded.failure_reason,
                    updated_at=excluded.updated_at
            """, (
                state.get("session_id"),
                error.get("error_type"),
                error.get("message"),
                state.get("status"),
                state.get("confidence", 0.0),
                state.get("fix_strategy"),
                code_ctx.get("file_path") if code_ctx else (inv or {}).get("file_path"),
                json.dumps(inv) if inv else None,
                json.dumps(fix) if fix else None,
                json.dumps(self._serialise_decisions(state.get("decisions", []))),
                state.get("pr_url"),
                state.get("pr_number"),
                state.get("failure_reason"),
                now, now,
            ))

    def get_workflow(self, session_id: str) -> Optional[Dict]:
        """Fetch a single workflow by session_id."""
        with self._lock, self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM workflows WHERE session_id = ?", (session_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_workflows(self, status: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """List workflows, optionally filtered by status."""
        with self._lock, self._connect() as conn:
            conn.row_factory = sqlite3.Row
            if status:
                rows = conn.execute(
                    "SELECT * FROM workflows WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                    (status, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM workflows ORDER BY updated_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    # ── Live events ───────────────────────────────────────────────────

    def record_event(self, session_id: str, node_name: str, data: Optional[Dict] = None) -> None:
        """Record a node-level event for live progress tracking."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO workflow_events (session_id, node_name, data_json, timestamp) VALUES (?, ?, ?, ?)",
                (session_id, node_name, json.dumps(data) if data else None, datetime.now().isoformat())
            )

    def get_events(self, session_id: str) -> List[Dict]:
        """Get all events for a session, ordered by time."""
        with self._lock, self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM workflow_events WHERE session_id = ? ORDER BY timestamp",
                (session_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Stats ─────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Aggregate statistics for the dashboard."""
        with self._lock, self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM workflows").fetchone()[0]
            success = conn.execute(
                "SELECT COUNT(*) FROM workflows WHERE status = 'pr_created'"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM workflows WHERE status = 'failed'"
            ).fetchone()[0]
            return {
                "total_workflows": total,
                "successful": success,
                "failed": failed,
                "success_rate": (success / total * 100) if total > 0 else 0.0,
            }

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _serialise_decisions(decisions: list) -> list:
        """Convert Decision dicts (with datetime) to JSON-safe list."""
        safe = []
        for d in decisions:
            entry = dict(d)
            if isinstance(entry.get("timestamp"), datetime):
                entry["timestamp"] = entry["timestamp"].isoformat()
            safe.append(entry)
        return safe
