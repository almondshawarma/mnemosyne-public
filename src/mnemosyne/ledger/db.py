"""Ledger connection and migrations. mnemosyne's tables share helios.db with the rest of Helios."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path | str) -> sqlite3.Connection:
    """Autocommit connection; callers open explicit transactions for multi-statement writes.

    The journal mode is left alone: helios.db is shared, and its mode belongs to Helios.
    """
    con = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def migrate(con: sqlite3.Connection, directory: Path = MIGRATIONS) -> list[str]:
    """Apply pending migrations in name order, each in its own transaction. Idempotent."""
    con.execute("CREATE TABLE IF NOT EXISTS mnemosyne_migration (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    done = {row["name"] for row in con.execute("SELECT name FROM mnemosyne_migration")}
    applied: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        if path.name in done:
            continue
        con.execute("BEGIN IMMEDIATE")
        try:
            for statement in path.read_text(encoding="utf-8").split(";\n"):
                if statement.strip():
                    con.execute(statement)
            con.execute("INSERT INTO mnemosyne_migration VALUES (?, ?)", (path.name, now()))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        applied.append(path.name)
    return applied
