"""Page fingerprints of carded lecture PDFs, and which copy of a deck replaced which (migration 0009)."""

import sqlite3

from mnemosyne.extract import PageMark
from mnemosyne.ledger.db import now


def save(con: sqlite3.Connection, doc_id: int, course: str, marks: list[PageMark]) -> None:
    con.execute("DELETE FROM deck_page WHERE doc_id = ?", (doc_id,))
    con.executemany("INSERT INTO deck_page (doc_id, course, page, text_key, thumb) VALUES (?, ?, ?, ?, ?)",
                    [(doc_id, course, m.number, m.key, m.thumb) for m in marks])


def marks(con: sqlite3.Connection, doc_id: int) -> list[PageMark]:
    return [PageMark(int(r["page"]), str(r["text_key"]), bytes(r["thumb"])) for r in con.execute(
        "SELECT page, text_key, thumb FROM deck_page WHERE doc_id = ? ORDER BY page", (doc_id,))]


def has(con: sqlite3.Connection, doc_id: int) -> bool:
    return con.execute("SELECT 1 FROM deck_page WHERE doc_id = ? LIMIT 1", (doc_id,)).fetchone() is not None


# Lectures, and papers (their pages keyed "paper:<slug>" in place of a course)
CARDED = "SELECT CAST(ref AS INTEGER) FROM ingest_job WHERE kind IN ('paperless', 'paper') AND status = 'done'"


def carded_docs(con: sqlite3.Connection, course: str, exclude: int | None = None) -> list[int]:
    """Documents of a course the pipeline finished carding, whose pages are fingerprinted."""
    return [int(r["doc_id"]) for r in con.execute(
        f"SELECT DISTINCT doc_id FROM deck_page WHERE course = ? AND doc_id IN ({CARDED}) AND doc_id != ?"
        " ORDER BY doc_id", (course, exclude if exclude is not None else -1))]


def seen(con: sqlite3.Connection, course: str, keys: set[str], exclude: int) -> list[PageMark]:
    """Pages with these printed texts in any carded copy of the course's decks."""
    if not keys:
        return []
    marks_ = ",".join("?" * len(keys))
    return [PageMark(int(r["page"]), str(r["text_key"]), bytes(r["thumb"])) for r in con.execute(
        f"SELECT page, text_key, thumb FROM deck_page WHERE course = ? AND text_key IN ({marks_})"
        f" AND doc_id != ? AND doc_id IN ({CARDED})", (course, *sorted(keys), exclude))]


def set_course(con: sqlite3.Connection, doc_id: int, course: str) -> None:
    """A deck filed under the wrong course: its pages count as seen in the right one."""
    con.execute("UPDATE deck_page SET course = ? WHERE doc_id = ?", (course, doc_id))


def replaced(con: sqlite3.Connection, new: int, old: int) -> None:
    con.execute("INSERT INTO doc_version (doc_id, replaces, at) VALUES (?, ?, ?) ON CONFLICT (doc_id) DO UPDATE SET"
                " replaces = excluded.replaces, at = excluded.at", (new, old, now()))


def predecessor(con: sqlite3.Connection, doc_id: int) -> int | None:
    row = con.execute("SELECT replaces FROM doc_version WHERE doc_id = ?", (doc_id,)).fetchone()
    return None if row is None else int(row["replaces"])
