"""SQLite store for mission operations: event log, telecommands and downlink attempts.

Every telemetry frame, diagnostic, decision, command change and downlink is appended to `events`.
The SSE stream tails that table by id, so any API worker can publish and any client can resume.
"""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sat_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    ts TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_sat_kind ON events (sat_id, kind, id);
CREATE TABLE IF NOT EXISTS commands (
    id TEXT PRIMARY KEY,
    sat_id TEXT NOT NULL,
    type TEXT NOT NULL,
    params TEXT NOT NULL,
    source TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    acked_at TEXT,
    result TEXT
);
CREATE INDEX IF NOT EXISTS commands_sat_status ON commands (sat_id, status, created_at);
CREATE TABLE IF NOT EXISTS downlinks (
    id TEXT PRIMARY KEY,
    sat_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    plan TEXT NOT NULL,
    outcome TEXT
);
CREATE INDEX IF NOT EXISTS downlinks_sat ON downlinks (sat_id, created_at);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MissionStore:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def _query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def _execute(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, args).lastrowid

    # ---- events -------------------------------------------------------------------------------

    def add_event(self, sat_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        ts = now_iso()
        event_id = self._execute(
            "INSERT INTO events (sat_id, kind, ts, payload) VALUES (?, ?, ?, ?)",
            (sat_id, kind, ts, json.dumps(payload)),
        )
        if kind == "telemetry" and event_id % 500 == 0:
            self._prune_telemetry(sat_id)
        return {"id": event_id, "sat_id": sat_id, "kind": kind, "ts": ts, "payload": payload}

    def _prune_telemetry(self, sat_id: str) -> None:
        self._execute(
            "DELETE FROM events WHERE sat_id = ? AND kind = 'telemetry' AND id < ("
            " SELECT id FROM events WHERE sat_id = ? AND kind = 'telemetry' ORDER BY id DESC LIMIT 1 OFFSET ?)",
            (sat_id, sat_id, settings.telemetry_history),
        )

    def events_after(self, after_id: int, sat_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        if sat_id is None:
            rows = self._query("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit))
        else:
            rows = self._query("SELECT * FROM events WHERE id > ? AND sat_id = ? ORDER BY id LIMIT ?", (after_id, sat_id, limit))
        return [_event(r) for r in rows]

    def recent_events(self, sat_id: str, kinds: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
        marks = ",".join("?" * len(kinds))
        rows = self._query(
            f"SELECT * FROM events WHERE sat_id = ? AND kind IN ({marks}) ORDER BY id DESC LIMIT ?",
            (sat_id, *kinds, limit),
        )
        return [_event(r) for r in reversed(rows)]

    def latest_event(self, sat_id: str, kind: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM events WHERE sat_id = ? AND kind = ? ORDER BY id DESC LIMIT 1", (sat_id, kind))
        return _event(rows[0]) if rows else None

    def last_event_id(self) -> int:
        return self._query("SELECT COALESCE(MAX(id), 0) AS id FROM events")[0]["id"]

    def satellites(self) -> list[str]:
        return [r["sat_id"] for r in self._query("SELECT DISTINCT sat_id FROM events WHERE kind = 'telemetry' ORDER BY sat_id")]

    # ---- telecommands -------------------------------------------------------------------------

    def add_command(self, sat_id: str, type_: str, params: dict[str, Any], source: str, reason: str) -> dict[str, Any]:
        command = {
            "id": f"CMD-{uuid.uuid4().hex[:10].upper()}",
            "sat_id": sat_id,
            "type": type_,
            "params": params,
            "source": source,
            "reason": reason,
            "status": "PENDING",
            "created_at": now_iso(),
            "acked_at": None,
            "result": None,
        }
        self._execute(
            "INSERT INTO commands (id, sat_id, type, params, source, reason, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (command["id"], sat_id, type_, json.dumps(params), source, reason, "PENDING", command["created_at"]),
        )
        return command

    def get_command(self, command_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM commands WHERE id = ?", (command_id,))
        return _command(rows[0]) if rows else None

    def commands(self, sat_id: str, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if status is None:
            rows = self._query("SELECT * FROM commands WHERE sat_id = ? ORDER BY created_at DESC LIMIT ?", (sat_id, limit))
        else:
            rows = self._query(
                "SELECT * FROM commands WHERE sat_id = ? AND status = ? ORDER BY created_at LIMIT ?", (sat_id, status, limit)
            )
        return [_command(r) for r in rows]

    def mark_sent(self, command_ids: list[str]) -> None:
        for command_id in command_ids:
            self._execute("UPDATE commands SET status = 'SENT' WHERE id = ? AND status = 'PENDING'", (command_id,))

    def ack_command(self, command_id: str, status: str, result: str) -> dict[str, Any] | None:
        self._execute(
            "UPDATE commands SET status = ?, acked_at = ?, result = ? WHERE id = ?", (status, now_iso(), result, command_id)
        )
        return self.get_command(command_id)

    # ---- downlink attempts --------------------------------------------------------------------

    def add_downlink(self, sat_id: str, item_id: str, plan: dict[str, Any]) -> dict[str, Any]:
        attempt = {"id": f"DL-{uuid.uuid4().hex[:10].upper()}", "sat_id": sat_id, "item_id": item_id, "created_at": now_iso(), "plan": plan, "outcome": None}
        self._execute(
            "INSERT INTO downlinks (id, sat_id, item_id, created_at, plan) VALUES (?, ?, ?, ?, ?)",
            (attempt["id"], sat_id, item_id, attempt["created_at"], json.dumps(plan)),
        )
        return attempt

    def get_downlink(self, attempt_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM downlinks WHERE id = ?", (attempt_id,))
        return _downlink(rows[0]) if rows else None

    def set_downlink_outcome(self, attempt_id: str, outcome: dict[str, Any]) -> dict[str, Any] | None:
        self._execute("UPDATE downlinks SET outcome = ? WHERE id = ?", (json.dumps(outcome), attempt_id))
        return self.get_downlink(attempt_id)

    def downlinks(self, sat_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM downlinks WHERE sat_id = ? ORDER BY created_at DESC LIMIT ?", (sat_id, limit))
        return [_downlink(r) for r in rows]


def _event(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "sat_id": row["sat_id"], "kind": row["kind"], "ts": row["ts"], "payload": json.loads(row["payload"])}


def _command(row: sqlite3.Row) -> dict[str, Any]:
    return {**dict(row), "params": json.loads(row["params"])}


def _downlink(row: sqlite3.Row) -> dict[str, Any]:
    return {**dict(row), "plan": json.loads(row["plan"]), "outcome": json.loads(row["outcome"]) if row["outcome"] else None}


_store: MissionStore | None = None
_store_lock = threading.Lock()


def get_store() -> MissionStore:
    """Process-wide store, opened lazily at settings.mission_db_path."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = MissionStore(settings.mission_db_path)
    return _store


def reset_store(store: MissionStore | None = None) -> None:
    """Swap the process-wide store (tests use an in-memory one)."""
    global _store
    with _store_lock:
        if _store is not None and _store is not store:
            _store.close()
        _store = store
