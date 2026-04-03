from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_INIT_LOCK = threading.Lock()
_INITIALIZED: set[Path] = set()
TERMINAL_EVENT_TYPES = {"job_completed", "job_failed", "job_cancelled"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def event_db_path(job_dir: Path) -> Path:
    return job_dir / "events.sqlite3"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=3.0)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_event_store(job_dir: Path) -> Path:
    db_path = event_db_path(job_dir)
    with _INIT_LOCK:
        if db_path in _INITIALIZED:
            return db_path
        job_dir.mkdir(parents=True, exist_ok=True)
        with _connect(db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tuning_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_utc TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tuning_events_type_seq ON tuning_events(event_type, seq);"
            )
            conn.commit()
        _INITIALIZED.add(db_path)
    return db_path


def append_event(job_dir: Path, *, event_type: str, payload: dict[str, Any] | None = None, ts_utc: str | None = None) -> int:
    db_path = ensure_event_store(job_dir)
    event_ts = ts_utc or _utc_now()
    payload_json = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO tuning_events(ts_utc, event_type, payload_json) VALUES (?, ?, ?)",
            (event_ts, str(event_type), payload_json),
        )
        conn.commit()
        return int(cur.lastrowid)


def read_events(
    job_dir: Path,
    *,
    after_seq: int = 0,
    limit: int = 200,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    db_path = event_db_path(job_dir)
    if not db_path.exists():
        return []
    lim = max(1, min(int(limit), 2000))
    after = max(0, int(after_seq))

    sql = "SELECT seq, ts_utc, event_type, payload_json FROM tuning_events WHERE seq > ?"
    params: list[Any] = [after]
    if event_type:
        sql += " AND event_type = ?"
        params.append(str(event_type))
    sql += " ORDER BY seq ASC LIMIT ?"
    params.append(lim)

    out: list[dict[str, Any]] = []
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        for row in rows:
            payload_raw = row["payload_json"]
            try:
                payload = json.loads(payload_raw) if payload_raw else {}
            except Exception:
                payload = {"_decode_error": True, "_raw": payload_raw}
            out.append(
                {
                    "seq": int(row["seq"]),
                    "ts_utc": str(row["ts_utc"]),
                    "event_type": str(row["event_type"]),
                    "payload": payload,
                }
            )
    return out


def get_event_stats(job_dir: Path) -> dict[str, Any]:
    db_path = event_db_path(job_dir)
    if not db_path.exists():
        return {"exists": False, "count": 0, "max_seq": 0, "event_types": {}}

    with _connect(db_path) as conn:
        count_row = conn.execute("SELECT COUNT(*) AS c, COALESCE(MAX(seq), 0) AS max_seq FROM tuning_events").fetchone()
        by_type_rows = conn.execute(
            "SELECT event_type, COUNT(*) AS c FROM tuning_events GROUP BY event_type ORDER BY event_type ASC"
        ).fetchall()

    event_types: dict[str, int] = {str(r["event_type"]): int(r["c"]) for r in by_type_rows}
    return {
        "exists": True,
        "count": int(count_row["c"]) if count_row else 0,
        "max_seq": int(count_row["max_seq"]) if count_row else 0,
        "event_types": event_types,
    }


def summarize_event_sequence(
    events: list[dict[str, Any]],
    *,
    expected_terminal: str | None = None,
) -> dict[str, Any]:
    """
    Lightweight integrity summary for append-only tuning event streams.
    """
    issues: list[str] = []
    event_type_counts: dict[str, int] = {}
    status_values: list[str] = []

    prev_seq = 0
    post_terminal_events = 0
    terminal_seq: int | None = None
    terminal_event_type: str | None = None

    for e in events:
        seq = int(e.get("seq") or 0)
        event_type = str(e.get("event_type") or "")
        payload = e.get("payload") if isinstance(e.get("payload"), dict) else {}

        if seq <= prev_seq:
            issues.append(f"non_monotonic_seq:{prev_seq}->{seq}")
        prev_seq = max(prev_seq, seq)

        if event_type:
            event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1

        if event_type == "status_changed":
            status = payload.get("status")
            if isinstance(status, str) and status.strip():
                status_values.append(status.strip())

        if event_type in TERMINAL_EVENT_TYPES:
            if terminal_seq is None:
                terminal_seq = seq
                terminal_event_type = event_type
            else:
                issues.append(f"multiple_terminal_events:{terminal_event_type},{event_type}")

        if terminal_seq is not None and seq > terminal_seq:
            post_terminal_events += 1

    if post_terminal_events > 0:
        issues.append(f"post_terminal_events:{post_terminal_events}")

    if expected_terminal:
        expected = str(expected_terminal).strip()
        if expected and terminal_event_type != expected:
            issues.append(f"terminal_mismatch:expected={expected},actual={terminal_event_type}")

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "count": len(events),
        "first_seq": int(events[0]["seq"]) if events else 0,
        "last_seq": int(events[-1]["seq"]) if events else 0,
        "terminal_event": terminal_event_type,
        "terminal_seq": terminal_seq,
        "post_terminal_events": post_terminal_events,
        "event_type_counts": event_type_counts,
        "status_values": status_values,
    }
