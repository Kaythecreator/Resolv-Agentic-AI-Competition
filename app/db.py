from __future__ import annotations

import json
import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "complaints.db"


def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS complaints (
                complaint_id TEXT PRIMARY KEY,
                input_data TEXT NOT NULL,
                status TEXT NOT NULL,
                state_data TEXT NOT NULL,
                agent_log TEXT NOT NULL,
                trace_ids TEXT NOT NULL DEFAULT '[]',
                error TEXT,
                error_traceback TEXT,
                trace_id TEXT,
                total_latency_seconds REAL,
                total_tokens INTEGER,
                total_cost REAL,
                metrics_last_synced_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _ensure_column(conn, "complaints", "error_traceback", "TEXT")
        _ensure_column(conn, "complaints", "trace_ids", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "complaints", "trace_id", "TEXT")
        _ensure_column(conn, "complaints", "total_latency_seconds", "REAL")
        _ensure_column(conn, "complaints", "total_tokens", "INTEGER")
        _ensure_column(conn, "complaints", "total_cost", "REAL")
        _ensure_column(conn, "complaints", "metrics_last_synced_at", "TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS complaint_debug_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                complaint_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                node_name TEXT,
                from_status TEXT,
                to_status TEXT,
                details TEXT,
                error_class TEXT,
                error_message TEXT,
                traceback TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS complaint_agent_metrics (
                complaint_id TEXT NOT NULL,
                node_name TEXT NOT NULL,
                occurrence_index INTEGER NOT NULL,
                trace_id TEXT,
                run_id TEXT,
                latency_seconds REAL,
                total_tokens INTEGER,
                total_cost REAL,
                source TEXT NOT NULL,
                synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (complaint_id, node_name, occurrence_index)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS complaint_trace_metrics (
                complaint_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                latency_seconds REAL,
                total_tokens INTEGER,
                total_cost REAL,
                synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (complaint_id, trace_id)
            )
            """
        )
        conn.commit()


def upsert_complaint(record: dict):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO complaints (
                complaint_id, input_data, status, state_data, agent_log, trace_ids, error, error_traceback,
                trace_id, total_latency_seconds, total_tokens, total_cost, metrics_last_synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(complaint_id) DO UPDATE SET
                input_data=excluded.input_data,
                status=excluded.status,
                state_data=excluded.state_data,
                agent_log=excluded.agent_log,
                trace_ids=excluded.trace_ids,
                error=excluded.error,
                error_traceback=excluded.error_traceback,
                trace_id=excluded.trace_id,
                total_latency_seconds=excluded.total_latency_seconds,
                total_tokens=excluded.total_tokens,
                total_cost=excluded.total_cost,
                metrics_last_synced_at=excluded.metrics_last_synced_at,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                record["complaint_id"],
                json.dumps(record.get("input", {})),
                record.get("status", "pending"),
                json.dumps(record.get("state", {})),
                json.dumps(record.get("agent_log", [])),
                json.dumps(record.get("trace_ids", [])),
                record.get("error"),
                record.get("error_traceback"),
                record.get("trace_id"),
                record.get("total_latency_seconds"),
                record.get("total_tokens"),
                record.get("total_cost"),
                record.get("metrics_last_synced_at"),
            ),
        )
        conn.commit()


def upsert_agent_metric(record: dict):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO complaint_agent_metrics (
                complaint_id, node_name, occurrence_index, trace_id, run_id,
                latency_seconds, total_tokens, total_cost, source, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(complaint_id, node_name, occurrence_index) DO UPDATE SET
                trace_id=excluded.trace_id,
                run_id=excluded.run_id,
                latency_seconds=excluded.latency_seconds,
                total_tokens=excluded.total_tokens,
                total_cost=excluded.total_cost,
                source=excluded.source,
                synced_at=CURRENT_TIMESTAMP
            """,
            (
                record["complaint_id"],
                record["node_name"],
                record["occurrence_index"],
                record.get("trace_id"),
                record.get("run_id"),
                record.get("latency_seconds"),
                record.get("total_tokens"),
                record.get("total_cost"),
                record.get("source", "langsmith"),
            ),
        )
        conn.commit()


def delete_agent_metrics(complaint_id: str):
    with _connect() as conn:
        conn.execute("DELETE FROM complaint_agent_metrics WHERE complaint_id = ?", (complaint_id,))
        conn.commit()


def upsert_trace_metric(record: dict):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO complaint_trace_metrics (
                complaint_id, trace_id, latency_seconds, total_tokens, total_cost, synced_at
            ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(complaint_id, trace_id) DO UPDATE SET
                latency_seconds=excluded.latency_seconds,
                total_tokens=excluded.total_tokens,
                total_cost=excluded.total_cost,
                synced_at=CURRENT_TIMESTAMP
            """,
            (
                record["complaint_id"],
                record["trace_id"],
                record.get("latency_seconds"),
                record.get("total_tokens"),
                record.get("total_cost"),
            ),
        )
        conn.commit()


def delete_trace_metrics(complaint_id: str):
    with _connect() as conn:
        conn.execute("DELETE FROM complaint_trace_metrics WHERE complaint_id = ?", (complaint_id,))
        conn.commit()


def fetch_trace_metrics(complaint_id: str) -> dict[str, dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT complaint_id, trace_id, latency_seconds, total_tokens, total_cost, synced_at
            FROM complaint_trace_metrics
            WHERE complaint_id = ?
            ORDER BY synced_at ASC, trace_id ASC
            """,
            (complaint_id,),
        ).fetchall()
    metrics: dict[str, dict] = {}
    for row in rows:
        metrics[row["trace_id"]] = {
            "complaint_id": row["complaint_id"],
            "trace_id": row["trace_id"],
            "latency_seconds": row["latency_seconds"],
            "total_tokens": row["total_tokens"],
            "total_cost": row["total_cost"],
            "synced_at": row["synced_at"],
        }
    return metrics


def fetch_agent_metrics(complaint_id: str) -> dict[tuple[str, int], dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT complaint_id, node_name, occurrence_index, trace_id, run_id,
                   latency_seconds, total_tokens, total_cost, source, synced_at
            FROM complaint_agent_metrics
            WHERE complaint_id = ?
            ORDER BY node_name ASC, occurrence_index ASC
            """,
            (complaint_id,),
        ).fetchall()
    metrics: dict[tuple[str, int], dict] = {}
    for row in rows:
        metrics[(row["node_name"], row["occurrence_index"])] = {
            "complaint_id": row["complaint_id"],
            "node_name": row["node_name"],
            "occurrence_index": row["occurrence_index"],
            "trace_id": row["trace_id"],
            "run_id": row["run_id"],
            "latency_seconds": row["latency_seconds"],
            "total_tokens": row["total_tokens"],
            "total_cost": row["total_cost"],
            "source": row["source"],
            "synced_at": row["synced_at"],
        }
    return metrics


def fetch_all_complaints() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT complaint_id, input_data, status, state_data, agent_log, trace_ids, error, error_traceback, "
            "trace_id, total_latency_seconds, total_tokens, total_cost, metrics_last_synced_at, created_at, updated_at "
            "FROM complaints ORDER BY updated_at DESC, complaint_id ASC"
        ).fetchall()
    return [_row_to_record(row) for row in rows]


def fetch_complaint(complaint_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT complaint_id, input_data, status, state_data, agent_log, trace_ids, error, error_traceback, "
            "trace_id, total_latency_seconds, total_tokens, total_cost, metrics_last_synced_at, created_at, updated_at "
            "FROM complaints WHERE complaint_id = ?",
            (complaint_id,),
        ).fetchone()
    return _row_to_record(row) if row else None


def log_debug_event(event: dict):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO complaint_debug_events (
                complaint_id, thread_id, phase, node_name, from_status, to_status,
                details, error_class, error_message, traceback
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["complaint_id"],
                event["thread_id"],
                event["phase"],
                event.get("node_name"),
                event.get("from_status"),
                event.get("to_status"),
                json.dumps(event.get("details")) if event.get("details") is not None else None,
                event.get("error_class"),
                event.get("error_message"),
                event.get("traceback"),
            ),
        )
        conn.commit()


def fetch_debug_events(complaint_id: str, limit: int = 100) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT complaint_id, thread_id, phase, node_name, from_status, to_status,
                   details, error_class, error_message, traceback, created_at
            FROM complaint_debug_events
            WHERE complaint_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (complaint_id, limit),
        ).fetchall()
    events = []
    for row in rows:
        events.append(
            {
                "complaint_id": row["complaint_id"],
                "thread_id": row["thread_id"],
                "phase": row["phase"],
                "node_name": row["node_name"],
                "from_status": row["from_status"],
                "to_status": row["to_status"],
                "details": json.loads(row["details"]) if row["details"] else None,
                "error_class": row["error_class"],
                "error_message": row["error_message"],
                "traceback": row["traceback"],
                "created_at": row["created_at"],
            }
        )
    return events


def _row_to_record(row: sqlite3.Row) -> dict:
    return {
        "complaint_id": row["complaint_id"],
        "input": json.loads(row["input_data"]),
        "status": row["status"],
        "state": json.loads(row["state_data"]),
        "agent_log": json.loads(row["agent_log"]),
        "trace_ids": json.loads(row["trace_ids"] or "[]"),
        "error": row["error"],
        "error_traceback": row["error_traceback"],
        "trace_id": row["trace_id"],
        "total_latency_seconds": row["total_latency_seconds"],
        "total_tokens": row["total_tokens"],
        "total_cost": row["total_cost"],
        "metrics_last_synced_at": row["metrics_last_synced_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
