"""
server/store.py - SQLite persistence for runs and their live event streams.

Each call opens its own connection, so the store is safe to use from the
agent worker threads and the async request handlers at the same time.
"""

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

TERMINAL_STATUSES = {"completed", "failed", "stopped", "interrupted"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    created_at      REAL NOT NULL,
    started_at      REAL,
    finished_at     REAL,
    status          TEXT NOT NULL,
    kind            TEXT NOT NULL,          -- 'benchmark' | 'custom'
    task_name       TEXT,
    title           TEXT,
    difficulty      TEXT,
    judge_requested INTEGER NOT NULL DEFAULT 0,
    task_json       TEXT NOT NULL,
    record_json     TEXT,
    itinerary       TEXT,
    error           TEXT,
    error_kind      TEXT,                   -- e.g. 'credits_exhausted', 'auth_error'
    success         INTEGER,
    total_tokens    INTEGER,
    api_calls       INTEGER,
    cost_usd        REAL,
    judge_score     REAL,
    judge_passed    INTEGER
);
CREATE INDEX IF NOT EXISTS runs_created ON runs (created_at DESC);
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   TEXT NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    type     TEXT NOT NULL,
    payload  TEXT NOT NULL,
    ts       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run ON events (run_id, id);
"""

SUMMARY_COLUMNS = (
    "id", "created_at", "started_at", "finished_at", "status", "kind", "task_name", "title", "difficulty",
    "judge_requested", "error", "error_kind", "success", "total_tokens", "api_calls", "cost_usd", "judge_score", "judge_passed",
)


class RunStore:

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._write_lock = threading.Lock()
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()
        except (OSError, sqlite3.OperationalError) as exc:
            raise RuntimeError(
                f"Can't write the database at {self.db_path} ({exc}). Check that the directory exists and is "
                "writable by the server's user; on Railway, set RAILWAY_RUN_UID=0 so the app can write to its volume."
            ) from exc

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
            if existing and "error_kind" not in existing:
                conn.execute("ALTER TABLE runs ADD COLUMN error_kind TEXT")  # databases from before this column
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ runs

    def create_run(self, *, kind: str, task: Dict[str, Any], task_name: Optional[str], judge: bool) -> str:
        run_id = uuid.uuid4().hex[:12]
        with self._write_lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO runs (id, created_at, status, kind, task_name, title, difficulty, judge_requested, task_json) "
                "VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?)",
                (run_id, time.time(), kind, task_name, task.get("title", ""), task.get("difficulty", "custom"),
                 int(judge), json.dumps(task)),
            )
        return run_id

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        for key in ("record_json",):
            if key in fields and not isinstance(fields[key], str) and fields[key] is not None:
                fields[key] = json.dumps(fields[key], default=str)
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self._write_lock, self._connect() as conn:
            conn.execute(f"UPDATE runs SET {assignments} WHERE id = ?", (*fields.values(), run_id))

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        run = dict(row)
        run["task"] = json.loads(run.pop("task_json"))
        run["record"] = json.loads(run.pop("record_json")) if run.get("record_json") else None
        return run

    def run_status(self, run_id: str) -> Optional[str]:
        """Cheap status lookup; None if the run doesn't exist."""
        with self._connect() as conn:
            row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
        return row["status"] if row else None

    def list_runs(self, kind: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        query = f"SELECT {', '.join(SUMMARY_COLUMNS)} FROM runs"
        params: List[Any] = []
        if kind:
            query += " WHERE kind = ?"
            params.append(kind)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def latest_completed_records(self, kind: str = "benchmark") -> List[Dict[str, Any]]:
        """Newest completed record per task (what the benchmark dashboard aggregates)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT task_name, record_json FROM runs WHERE kind = ? AND status = 'completed' "
                "AND record_json IS NOT NULL ORDER BY created_at DESC",
                (kind,),
            ).fetchall()
        latest: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            latest.setdefault(row["task_name"], json.loads(row["record_json"]))
        return list(latest.values())

    def totals(self) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS runs, COALESCE(SUM(total_tokens), 0) AS tokens, COALESCE(SUM(api_calls), 0) AS api_calls, "
                "COALESCE(SUM(cost_usd), 0) AS cost_usd, SUM(status = 'completed') AS completed, SUM(success = 1) AS succeeded "
                "FROM runs"
            ).fetchone()
        return {key: row[key] or 0 for key in row.keys()}

    def delete_run(self, run_id: str) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute("DELETE FROM events WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))

    def mark_unfinished_interrupted(self) -> int:
        """Runs left queued/running by a previous server process can never finish."""
        with self._write_lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE runs SET status = 'interrupted', error = 'Server restarted before the run finished.', "
                "finished_at = ? WHERE status IN ('queued', 'running')",
                (time.time(),),
            )
            return cursor.rowcount

    # ---------------------------------------------------------------- events

    def add_event(self, run_id: str, event_type: str, payload: Dict[str, Any]) -> int:
        with self._write_lock, self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO events (run_id, type, payload, ts) VALUES (?, ?, ?, ?)",
                (run_id, event_type, json.dumps(payload, default=str), time.time()),
            )
            return cursor.lastrowid

    def events_after(self, run_id: str, after_id: int = 0) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, type, payload, ts FROM events WHERE run_id = ? AND id > ? ORDER BY id",
                (run_id, after_id),
            ).fetchall()
        return [{"id": r["id"], "type": r["type"], "ts": r["ts"], "data": json.loads(r["payload"])} for r in rows]
