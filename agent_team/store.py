"""Run records and an append-only event log in SQLite. Event sequence numbers double as SSE event IDs."""

import json
import sqlite3
import threading
import time
from pathlib import Path

FINISHED = {"done", "idle", "max_turns", "timeout", "token_budget", "stalled", "stopped", "failed", "interrupted", "no_progress"}
RUN_COLUMNS = ("id", "goal", "status", "created_at", "finished_at")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    kind TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_run ON events (run_id, seq);
"""


class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.version = 0
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)

    def create_run(self, run_id, goal):
        with self.lock:
            self.db.execute(
                "INSERT INTO runs (id, goal, status, created_at) VALUES (?, ?, 'running', ?)",
                (run_id, goal, time.time()),
            )

    def add_event(self, run_id, kind, data):
        with self.lock:
            self._insert_event(run_id, kind, data)
            self._changed()

    def finish_run(self, run_id, status):
        """Record the final status as an event and on the run, in one transaction."""
        self._set_status(run_id, status, finished=True)

    def resume_run(self, run_id):
        self._set_status(run_id, "running", finished=False)

    def mark_interrupted(self):
        """Runs still marked running when the app starts were cut off by a restart."""
        with self.lock:
            running = [row[0] for row in self.db.execute("SELECT id FROM runs WHERE status = 'running'")]
        for run_id in running:
            self.finish_run(run_id, "interrupted")

    def events_after(self, run_id, after, limit=500):
        with self.lock:
            rows = self.db.execute(
                "SELECT seq, kind, data FROM events WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (run_id, after, limit),
            ).fetchall()
        return [(seq, kind, json.loads(data)) for seq, kind, data in rows]

    def get_run(self, run_id):
        with self.lock:
            row = self.db.execute(
                "SELECT id, goal, status, created_at, finished_at FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        return dict(zip(RUN_COLUMNS, row)) if row else None

    def list_runs(self, limit=50):
        with self.lock:
            rows = self.db.execute(
                "SELECT id, goal, status, created_at, finished_at FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(zip(RUN_COLUMNS, row)) for row in rows]

    def current_version(self):
        with self.lock:
            return self.version

    def wait_for_change(self, seen, timeout):
        """Block until something was written after version `seen`, or the timeout passes. Returns True if changed."""
        with self.changed:
            if self.version == seen:
                self.changed.wait(timeout)
            return self.version != seen

    def _set_status(self, run_id, status, finished):
        with self.lock:
            self.db.execute("BEGIN")
            try:
                self._insert_event(run_id, "status", {"status": status})
                self.db.execute(
                    "UPDATE runs SET status = ?, finished_at = ? WHERE id = ?",
                    (status, time.time() if finished else None, run_id),
                )
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            self._changed()

    def _insert_event(self, run_id, kind, data):
        self.db.execute(
            "INSERT INTO events (run_id, kind, data, created_at) VALUES (?, ?, ?, ?)",
            (run_id, kind, json.dumps(data), time.time()),
        )

    def _changed(self):
        self.version += 1
        self.changed.notify_all()
