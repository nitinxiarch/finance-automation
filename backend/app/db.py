"""
SQLite-backed run storage and audit log.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).parent.parent.parent / "data" / "runs.db"


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                created_at TEXT,
                status TEXT,
                bank_filename TEXT,
                books_filename TEXT,
                bank_file_hash TEXT,
                books_file_hash TEXT,
                result_json TEXT,
                workbook_path TEXT,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                timestamp TEXT,
                event TEXT,
                detail TEXT
            );
            CREATE TABLE IF NOT EXISTS overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                timestamp TEXT,
                action TEXT,
                match_id TEXT,
                bank_row_ids TEXT,
                books_row_ids TEXT,
                rationale TEXT,
                user TEXT
            );
        """)


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

# Auto-initialize database schema on load
init_db()


def create_run(
    run_id: str,
    bank_filename: str,
    books_filename: str,
    bank_file_hash: str,
    books_file_hash: str,
) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO runs (run_id, created_at, status, bank_filename, books_filename, bank_file_hash, books_file_hash)
               VALUES (?, ?, 'PENDING', ?, ?, ?, ?)""",
            (run_id, datetime.now(timezone.utc).isoformat(), bank_filename, books_filename, bank_file_hash, books_file_hash),
        )
    audit(run_id, "RUN_CREATED", f"bank={bank_filename}, books={books_filename}")


def update_run_status(run_id: str, status: str, error: Optional[str] = None) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE runs SET status=?, error=? WHERE run_id=?",
            (status, error, run_id),
        )


def save_result(run_id: str, result_json: str, workbook_path: str, status: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE runs SET result_json=?, workbook_path=?, status=? WHERE run_id=?",
            (result_json, workbook_path, status, run_id),
        )


def get_run(run_id: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None


def audit(run_id: str, event: str, detail: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (run_id, timestamp, event, detail) VALUES (?,?,?,?)",
            (run_id, datetime.now(timezone.utc).isoformat(), event, detail),
        )


def save_override(run_id: str, override: dict) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO overrides (run_id, timestamp, action, match_id, bank_row_ids, books_row_ids, rationale, user)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                run_id,
                datetime.now(timezone.utc).isoformat(),
                override.get("action"),
                override.get("match_id"),
                json.dumps(override.get("bank_row_ids", [])),
                json.dumps(override.get("books_row_ids", [])),
                override.get("rationale"),
                override.get("user", "user"),
            ),
        )
    audit(run_id, "OVERRIDE", json.dumps(override))


def get_overrides(run_id: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM overrides WHERE run_id=?", (run_id,)).fetchall()
        return [dict(r) for r in rows]


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

