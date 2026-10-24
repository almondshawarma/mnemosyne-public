"""Reads and writes of mnemosyne's Ledger tables. The only module besides db.py that touches SQL."""

import json
import secrets
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from mnemosyne.cards import CardIn, Source
from mnemosyne.ledger.db import now

# (mnemosyne_id, content_hash, card, note fields, note tags, status, review_reason, merge_into, merge_up)
NewDraft = tuple[str, str, CardIn, dict[str, str], list[str], str, str | None, int | None, bool]


@dataclass(frozen=True)
class Draft:
    mnemosyne_id: str
    batch_id: str
    note_type: str
    deck: str
    fields: dict[str, str]
    tags: list[str]
    mode: str
    status: str
    note_id: int | None
    review_reason: str | None = None
    merge_into: int | None = None   # the Anki note dedupe matched; the page can merge this card into it
    merge_up: bool = False          # …and this card is the more fundamental framing, so it can rewrite that note

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Draft":
        return cls(row["mnemosyne_id"], row["batch_id"], row["note_type"], row["deck"], json.loads(row["fields_json"]),
                   json.loads(row["tags_json"]), row["mode"], row["status"], row["note_id"], row["review_reason"],
                   row["merge_into"], bool(row["merge_up"]))


@dataclass(frozen=True)
class Batch:
    id: str
    status: str
    kind: str
    course: str | None
    lecture: int | None
    ref: str | None
    origin: str
    counts: dict[str, int]
    link: str | None = None
    created_at: str | None = None

    @property
    def size(self) -> int:
        return sum(self.counts.values())


@dataclass(frozen=True)
class CardDecision:
    """One pending draft decided on its own. Edits replace fields, tags, or deck; `before` keeps what they were."""

    mnemosyne_id: str
    approve: bool
    note: str = ""
    fields: dict[str, str] | None = None
    tags: list[str] | None = None
    deck: str | None = None
    before: dict[str, Any] | None = None


def source_by_hash(con: sqlite3.Connection, payload_hash: str) -> int | None:
    row = con.execute("SELECT id FROM card_source WHERE payload_hash = ?", (payload_hash,)).fetchone()
    return None if row is None else int(row["id"])


def batch_for_source(con: sqlite3.Connection, source_id: int) -> str:
    return str(con.execute("SELECT id FROM card_batch WHERE source_id = ?", (source_id,)).fetchone()["id"])


def save_skipped(con: sqlite3.Connection, batch_id: str | None, skipped: list[dict[str, str]]) -> None:
    """What the generation left out, and why, kept on its batch for the weekly audit."""
    if batch_id and skipped:
        con.execute("UPDATE card_batch SET skipped_json = ? WHERE id = ?",
                    (json.dumps(skipped, ensure_ascii=False), batch_id))


def existing_hashes(con: sqlite3.Connection, hashes: list[str]) -> set[str]:
    if not hashes:
        return set()
    marks = ",".join("?" * len(hashes))
    return {r["content_hash"] for r in con.execute(f"SELECT content_hash FROM card_draft WHERE content_hash IN ({marks})",
                                                    hashes)}


def create_batch(con: sqlite3.Connection, payload_hash: str, origin: str, source: Source,
                 drafts: list[NewDraft]) -> str:
    """Insert source, batch, and drafts in one transaction."""
    stamp = now()
    batch_id = secrets.token_hex(3)
    statuses = {d[5] for d in drafts}
    status = "empty" if not drafts else ("approved" if statuses == {"approved"} else "pending")
    con.execute("BEGIN IMMEDIATE")
    try:
        cur = con.execute(
            "INSERT INTO card_source (payload_hash, origin, kind, course, lecture, ref, link, received_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (payload_hash, origin, source.kind, source.course, source.lecture, source.ref, source.link, stamp))
        con.execute("INSERT INTO card_batch (id, source_id, status, created_at, decided_by, decided_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (batch_id, cur.lastrowid, status, stamp,
                     origin if status == "approved" else None, stamp if status == "approved" else None))
        for mid, chash, card, fields, tags, dstatus, reason, merge_into, merge_up in drafts:
            con.execute(
                "INSERT INTO card_draft (mnemosyne_id, batch_id, content_hash, note_type, deck, fields_json, tags_json,"
                " mode, checks_json, confidence, status, review_reason, merge_into, merge_up, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (mid, batch_id, chash, card.note_type, card.deck, json.dumps(fields, ensure_ascii=False),
                 json.dumps(tags, ensure_ascii=False), card.mode,
                 json.dumps([c.model_dump() for c in card.checks], ensure_ascii=False), card.confidence,
                 dstatus, reason, merge_into, int(merge_up), stamp, stamp))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return batch_id


def get_batch(con: sqlite3.Connection, batch_id: str) -> Batch | None:
    row = con.execute(
        "SELECT b.id, b.status, b.created_at, s.kind, s.course, s.lecture, s.ref, s.origin, s.link FROM card_batch b"
        " JOIN card_source s ON s.id = b.source_id WHERE b.id = ?", (batch_id,)).fetchone()
    if row is None:
        return None
    counts = {r["status"]: int(r["n"]) for r in con.execute(
        "SELECT status, COUNT(*) AS n FROM card_draft WHERE batch_id = ? GROUP BY status", (batch_id,))}
    return Batch(row["id"], row["status"], row["kind"], row["course"], row["lecture"], row["ref"], row["origin"], counts,
                 row["link"], row["created_at"])


def batch_drafts(con: sqlite3.Connection, batch_id: str) -> list[Draft]:
    rows = con.execute("SELECT * FROM card_draft WHERE batch_id = ? ORDER BY id", (batch_id,))
    return [Draft.from_row(r) for r in rows]


def list_batches(con: sqlite3.Connection, status: str | None = None, limit: int = 50) -> list[Batch]:
    query = "SELECT id FROM card_batch" + (" WHERE status = ?" if status else "") + " ORDER BY created_at DESC LIMIT ?"
    args: tuple[Any, ...] = (status, limit) if status else (limit,)
    return [b for r in con.execute(query, args).fetchall() if (b := get_batch(con, r["id"])) is not None]


def decide(con: sqlite3.Connection, batch_id: str, approve: bool, by: str) -> bool:
    """Approve or reject every still-pending draft in a pending batch. False if nothing was pending."""
    stamp = now()
    new = "approved" if approve else "rejected"
    con.execute("BEGIN IMMEDIATE")
    try:
        cur = con.execute("UPDATE card_batch SET status = ?, decided_by = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
                          (new, by, stamp, batch_id))
        if cur.rowcount:
            con.execute("UPDATE card_draft SET status = ?, updated_at = ? WHERE batch_id = ? AND status = 'pending'",
                        (new, stamp, batch_id))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return bool(cur.rowcount)


def decide_cards(con: sqlite3.Connection, batch_id: str, decisions: list[CardDecision], by: str) -> int:
    """Approve, edit, or reject single pending drafts. When none are left pending, the batch is settled too.
    Returns how many drafts changed; a draft that is no longer pending is left alone."""
    stamp = now()
    changed = 0
    con.execute("BEGIN IMMEDIATE")
    try:
        for d in decisions:
            cur = con.execute(
                "UPDATE card_draft SET status = ?, decided_by = ?, decision_note = ?,"
                " fields_json = COALESCE(?, fields_json), tags_json = COALESCE(?, tags_json), deck = COALESCE(?, deck),"
                " before_json = COALESCE(?, before_json), updated_at = ?, decided_at = ?"
                " WHERE mnemosyne_id = ? AND batch_id = ? AND status = 'pending'",
                ("approved" if d.approve else "rejected", by, d.note or None,
                 None if d.fields is None else json.dumps(d.fields, ensure_ascii=False),
                 None if d.tags is None else json.dumps(d.tags, ensure_ascii=False), d.deck,
                 None if d.before is None else json.dumps(d.before, ensure_ascii=False), stamp, stamp,
                 d.mnemosyne_id, batch_id))
            changed += cur.rowcount
        _settle(con, batch_id, by, stamp)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return changed


def _settle(con: sqlite3.Connection, batch_id: str, by: str, stamp: str) -> None:
    """When no draft in the batch is pending any more, the batch takes its outcome."""
    left = {r["status"] for r in con.execute("SELECT DISTINCT status FROM card_draft WHERE batch_id = ?", (batch_id,))}
    if "pending" not in left:
        # a merged card landed too, in the note it joined
        settled = ("approved" if left & {"approved", "error"} else "written" if left & {"written", "merged"} else "rejected")
        con.execute("UPDATE card_batch SET status = ?, decided_by = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
                    (settled, by, stamp, batch_id))


def merge_draft(con: sqlite3.Connection, mnemosyne_id: str, by: str, note: str) -> bool:
    """A pending draft whose idea now lives in the note it was merged into: it's settled as 'merged', never written."""
    stamp = now()
    con.execute("BEGIN IMMEDIATE")
    try:
        cur = con.execute("UPDATE card_draft SET status = 'merged', decided_by = ?, decision_note = ?, updated_at = ?,"
                          " decided_at = ? WHERE mnemosyne_id = ? AND status = 'pending'",
                          (by, note or None, stamp, stamp, mnemosyne_id))
        row = con.execute("SELECT batch_id FROM card_draft WHERE mnemosyne_id = ?", (mnemosyne_id,)).fetchone()
        if cur.rowcount and row is not None:
            _settle(con, row["batch_id"], by, stamp)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return bool(cur.rowcount)


def get_draft(con: sqlite3.Connection, mnemosyne_id: str) -> Draft | None:
    row = con.execute("SELECT * FROM card_draft WHERE mnemosyne_id = ?", (mnemosyne_id,)).fetchone()
    return None if row is None else Draft.from_row(row)


def drafts_by_hash(con: sqlite3.Connection, hashes: list[str]) -> dict[str, str]:
    """content_hash → mnemosyne_id for drafts already in the Ledger."""
    if not hashes:
        return {}
    marks = ",".join("?" * len(hashes))
    return {r["content_hash"]: r["mnemosyne_id"] for r in con.execute(
        f"SELECT content_hash, mnemosyne_id FROM card_draft WHERE content_hash IN ({marks})", hashes)}


def revise(con: sqlite3.Connection, d: CardDecision, by: str) -> bool:
    """Edit a draft that is already approved or written. It goes back to approved, so the next write updates the
    note in place (the upsert is keyed on MnemosyneID)."""
    stamp = now()
    cur = con.execute(
        "UPDATE card_draft SET status = 'approved', decided_by = ?, decision_note = ?,"
        " fields_json = COALESCE(?, fields_json), tags_json = COALESCE(?, tags_json), deck = COALESCE(?, deck),"
        " before_json = ?, updated_at = ?, decided_at = ? WHERE mnemosyne_id = ? AND status IN ('approved', 'written', 'error')",
        (by, d.note or None, None if d.fields is None else json.dumps(d.fields, ensure_ascii=False),
         None if d.tags is None else json.dumps(d.tags, ensure_ascii=False), d.deck,
         json.dumps(d.before, ensure_ascii=False), stamp, stamp, d.mnemosyne_id))
    return bool(cur.rowcount)


# ---------- review sets: in-session batches reviewed on /review ----------

# (key, position, card as written, mnemosyne_id or None, problems or None)
ReviewItem = tuple[str, int, dict[str, Any], str | None, list[str] | None]


def create_review(con: sqlite3.Connection, review_id: str, title: str, kind: str, payload: dict[str, Any],
                  items: list[ReviewItem]) -> None:
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute("INSERT INTO review_set (id, title, kind, payload_json, status, created_at) VALUES (?, ?, ?, ?, 'open', ?)",
                    (review_id, title, kind, json.dumps(payload, ensure_ascii=False), now()))
        for key, pos, card, mid, problems in items:
            con.execute("INSERT INTO review_item (review_id, key, position, card_json, mnemosyne_id, problems)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (review_id, key, pos, json.dumps(card, ensure_ascii=False), mid,
                         None if not problems else json.dumps(problems, ensure_ascii=False)))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def delete_review(con: sqlite3.Connection, review_id: str) -> bool:
    """Remove a review nobody has decided anything in (a mistaken push). Its held drafts stay in the Ledger,
    so pushing the batch again links them instead of duplicating them."""
    con.execute("BEGIN IMMEDIATE")
    try:
        decided = con.execute("SELECT 1 FROM review_item WHERE review_id = ? AND verdict IS NOT NULL LIMIT 1",
                              (review_id,)).fetchone()
        status = con.execute("SELECT status FROM review_set WHERE id = ?", (review_id,)).fetchone()
        if status is None or status["status"] != "open" or decided is not None:
            con.execute("ROLLBACK")
            return False
        con.execute("DELETE FROM review_item WHERE review_id = ?", (review_id,))
        con.execute("DELETE FROM review_set WHERE id = ?", (review_id,))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return True


def review_exists(con: sqlite3.Connection, review_id: str) -> bool:
    return con.execute("SELECT 1 FROM review_set WHERE id = ?", (review_id,)).fetchone() is not None


def get_review(con: sqlite3.Connection, review_id: str) -> dict[str, Any] | None:
    """The set with its items, each joined to its draft as it is now (status, reason, current fields)."""
    row = con.execute("SELECT * FROM review_set WHERE id = ?", (review_id,)).fetchone()
    if row is None:
        return None
    items: list[dict[str, Any]] = []
    for r in con.execute("SELECT i.*, d.status AS draft_status, d.review_reason, d.fields_json, d.tags_json, d.deck,"
                         " d.note_type, d.mode, d.before_json FROM review_item i"
                         " LEFT JOIN card_draft d ON d.mnemosyne_id = i.mnemosyne_id"
                         " WHERE i.review_id = ? ORDER BY i.position", (review_id,)):
        items.append({
            "key": r["key"], "card": json.loads(r["card_json"]), "mnemosyne_id": r["mnemosyne_id"],
            "problems": json.loads(r["problems"]) if r["problems"] else None,
            "verdict": r["verdict"], "reason": r["reason"] or "", "decided_at": r["decided_at"],
            "edits": json.loads(r["edits_json"]) if r["edits_json"] else None,
            "draft": None if r["draft_status"] is None else {
                "status": r["draft_status"], "review_reason": r["review_reason"], "note_type": r["note_type"],
                "deck": r["deck"], "mode": r["mode"], "fields": json.loads(r["fields_json"]),
                "tags": json.loads(r["tags_json"])}})
    payload: dict[str, Any] = json.loads(row["payload_json"])
    return {"id": row["id"], "title": row["title"], "kind": row["kind"], "status": row["status"],
            "created_at": row["created_at"], "finished_at": row["finished_at"], "notes": row["notes"],
            "promote": json.loads(row["promote_json"]), **payload, "items": items}


def list_reviews(con: sqlite3.Connection, statuses: tuple[str, ...] = ("open", "reviewed")) -> list[dict[str, Any]]:
    marks = ",".join("?" * len(statuses))
    rows = con.execute(f"SELECT s.id, s.title, s.kind, s.status, s.created_at, COUNT(i.key) AS cards,"
                       f" SUM(i.verdict IS NOT NULL) AS decided FROM review_set s"
                       f" LEFT JOIN review_item i ON i.review_id = s.id WHERE s.status IN ({marks})"
                       f" GROUP BY s.id ORDER BY s.created_at DESC", statuses)
    return [dict(r) for r in rows]


def record_item(con: sqlite3.Connection, review_id: str, key: str, verdict: str, reason: str,
                edits: dict[str, Any] | None) -> bool:
    """A verdict is recorded once; returns False if the item was already decided."""
    cur = con.execute("UPDATE review_item SET verdict = ?, reason = ?, edits_json = ?, decided_at = ?"
                      " WHERE review_id = ? AND key = ? AND verdict IS NULL",
                      (verdict, reason or None, None if edits is None else json.dumps(edits, ensure_ascii=False),
                       now(), review_id, key))
    return bool(cur.rowcount)


def set_review_notes(con: sqlite3.Connection, review_id: str, notes: str, promote: list[int]) -> bool:
    cur = con.execute("UPDATE review_set SET notes = ?, promote_json = ? WHERE id = ? AND status = 'open'",
                      (notes, json.dumps(sorted(set(promote))), review_id))
    return bool(cur.rowcount)


def set_review_status(con: sqlite3.Connection, review_id: str, status: str) -> bool:
    """open → reviewed (Aman pressed Finish) → applied (Claude merged it)."""
    column = {"reviewed": "finished_at", "applied": "applied_at"}[status]
    before = {"reviewed": "open", "applied": "reviewed"}[status]
    cur = con.execute(f"UPDATE review_set SET status = ?, {column} = ? WHERE id = ? AND status = ?",
                      (status, now(), review_id, before))
    return bool(cur.rowcount)


def reopen_review(con: sqlite3.Connection, review_id: str) -> bool:
    """Undo Finish: a batch finished by mistake opens again, with every decision kept. Only until Claude applies it."""
    cur = con.execute("UPDATE review_set SET status = 'open', finished_at = NULL WHERE id = ? AND status = 'reviewed'",
                      (review_id,))
    return bool(cur.rowcount)


def stuck(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Approved cards the write job couldn't put into Anki, with the reason. They are retried on every write."""
    rows = con.execute(
        "SELECT d.*, s.course, s.lecture, s.origin FROM card_draft d JOIN card_batch b ON b.id = d.batch_id"
        " JOIN card_source s ON s.id = b.source_id WHERE d.status = 'approved' AND d.error IS NOT NULL ORDER BY d.id")
    return [{**asdict(Draft.from_row(r)), "error": r["error"], "course": r["course"], "lecture": r["lecture"],
             "origin": r["origin"]} for r in rows]


def withdraw(con: sqlite3.Connection, mnemosyne_id: str, note: str, by: str) -> bool:
    """Take back an approved card that never reached Anki (no note id). Nothing in the collection changes."""
    stamp = now()
    cur = con.execute("UPDATE card_draft SET status = 'rejected', decided_by = ?, decision_note = ?, updated_at = ?,"
                      " decided_at = ? WHERE mnemosyne_id = ? AND status = 'approved' AND note_id IS NULL",
                      (by, note or None, stamp, stamp, mnemosyne_id))
    if cur.rowcount:
        con.execute("UPDATE card_batch SET status = CASE WHEN EXISTS (SELECT 1 FROM card_draft d WHERE d.batch_id ="
                    " card_batch.id AND d.status = 'written') THEN 'written' ELSE 'rejected' END"
                    " WHERE status = 'approved' AND NOT EXISTS (SELECT 1 FROM card_draft d WHERE d.batch_id = card_batch.id"
                    " AND d.status IN ('approved', 'error', 'pending'))")
    return bool(cur.rowcount)


def decided_since(con: sqlite3.Connection, since: str) -> list[dict[str, Any]]:
    """Cards Aman decided one by one (on the page) since a time: verdict, note, and what an edit replaced.
    This is what Claude reads to learn from his reasons."""
    rows = con.execute(
        "SELECT d.mnemosyne_id, d.batch_id, d.status, d.decided_by, d.decision_note, d.before_json, d.fields_json,"
        " d.tags_json, d.note_type, d.review_reason, d.updated_at, s.course, s.lecture, s.origin FROM card_draft d"
        " JOIN card_batch b ON b.id = d.batch_id JOIN card_source s ON s.id = b.source_id"
        " WHERE d.decided_by IS NOT NULL AND d.updated_at >= ? ORDER BY d.updated_at", (since,))
    return [{"mnemosyne_id": r["mnemosyne_id"], "batch_id": r["batch_id"], "status": r["status"],
             "by": r["decided_by"], "note": r["decision_note"] or "", "at": r["updated_at"],
             "before": json.loads(r["before_json"]) if r["before_json"] else None,
             "course": r["course"], "lecture": r["lecture"], "origin": r["origin"], "note_type": r["note_type"],
             "review_reason": r["review_reason"], "fields": json.loads(r["fields_json"]), "tags": json.loads(r["tags_json"])}
            for r in rows]


def approved_tags(con: sqlite3.Connection) -> list[list[str]]:
    """Tags of every approved or written card. Exam coverage counts these the way the deck searches do
    (course::<code>::lNN-…), so cards from review sets or retagged cards count under the right lecture."""
    return [json.loads(r["tags_json"]) for r in
            con.execute("SELECT tags_json FROM card_draft WHERE status IN ('approved', 'written')")]


def open_draft_prompts(con: sqlite3.Connection) -> dict[int, str]:
    """Drafts not in Anki yet (waiting for Aman, or approved and not written): draft id → first field. Dedupe checks
    new cards against these too, or two courses' lectures carded a day apart both land in the queue."""
    out: dict[int, str] = {}
    for r in con.execute("SELECT id, fields_json FROM card_draft WHERE status IN ('pending', 'approved')"):
        fields: dict[str, str] = json.loads(r["fields_json"])
        out[int(r["id"])] = next(iter(fields.values()), "")
    return out


def add_draft_tags(con: sqlite3.Connection, draft_id: int, tags: list[str]) -> list[str]:
    """Add tags to a draft that isn't in Anki yet (tags only add). Returns the tags it gained."""
    row = con.execute("SELECT tags_json FROM card_draft WHERE id = ? AND status IN ('pending', 'approved')",
                      (draft_id,)).fetchone()
    if row is None:
        return []
    have: list[str] = json.loads(row["tags_json"])
    gained = [t for t in tags if t not in have]
    if gained:
        con.execute("UPDATE card_draft SET tags_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps([*have, *gained], ensure_ascii=False), now(), draft_id))
    return gained


def relink(con: sqlite3.Connection, old: str, new: str) -> list[int]:
    """A lecture's PDF was replaced by a new copy: point its cards' SourceLink at the new one. Drafts not in Anki yet
    are simply updated; returns the note ids of written ones, whose field needs the same patch in Anki."""
    rows = con.execute("SELECT note_id, status FROM card_draft WHERE json_extract(fields_json, '$.SourceLink') = ?",
                       (old,)).fetchall()
    con.execute("UPDATE card_draft SET fields_json = json_set(fields_json, '$.SourceLink', ?), updated_at = ?"
                " WHERE json_extract(fields_json, '$.SourceLink') = ?", (new, now(), old))
    con.execute("UPDATE card_source SET link = ? WHERE link = ?", (new, old))
    return [int(r["note_id"]) for r in rows if r["note_id"] is not None and r["status"] == "written"]


def refile(con: sqlite3.Connection, link: str, course: str, lecture: int, ref: str, tags: tuple[str, str],
           source: tuple[str, str]) -> list[tuple[int, str]]:
    """A lecture filed under the wrong course moved to the right one: its sources, and its cards' lecture tag and
    Source field ("NUEN201 L09 p2" → "MATH309 L07 p2"). Returns the written cards' note ids with their new Source,
    for the same patch in Anki."""
    (old_tag, new_tag), (old_src, new_src) = tags, source
    con.execute("UPDATE card_source SET course = ?, lecture = ?, ref = ? WHERE link = ?", (course, lecture, ref, link))
    written: list[tuple[int, str]] = []
    for r in con.execute("SELECT id, status, note_id, tags_json, fields_json FROM card_draft"
                         " WHERE json_extract(fields_json, '$.SourceLink') = ?", (link,)).fetchall():
        tags_ = list(dict.fromkeys(new_tag if t == old_tag else t for t in json.loads(r["tags_json"])))
        fields: dict[str, Any] = json.loads(r["fields_json"])
        src = str(fields.get("Source") or "")
        if src.startswith(old_src):
            fields["Source"] = new_src + src[len(old_src):]
        con.execute("UPDATE card_draft SET tags_json = ?, fields_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(tags_, ensure_ascii=False), json.dumps(fields, ensure_ascii=False), now(), r["id"]))
        if r["note_id"] is not None and r["status"] == "written":
            written.append((int(r["note_id"]), str(fields.get("Source") or "")))
    return written


def set_fields(con: sqlite3.Connection, mnemosyne_id: str, fields: dict[str, str]) -> None:
    """Fields the pipeline fills in itself (audio), right before a draft is written."""
    con.execute("UPDATE card_draft SET fields_json = ?, updated_at = ? WHERE mnemosyne_id = ?",
                (json.dumps(fields, ensure_ascii=False), now(), mnemosyne_id))


def approved_unwritten(con: sqlite3.Connection, limit: int = 500) -> list[Draft]:
    rows = con.execute("SELECT * FROM card_draft WHERE status = 'approved' ORDER BY id LIMIT ?", (limit,))
    return [Draft.from_row(r) for r in rows]


def mark_written(con: sqlite3.Connection, written: dict[str, int]) -> None:
    stamp = now()
    con.execute("BEGIN IMMEDIATE")
    try:
        for mid, note_id in written.items():
            con.execute("UPDATE card_draft SET status = 'written', note_id = ?, written_at = ?, updated_at = ?, error = NULL"
                        " WHERE mnemosyne_id = ?", (note_id, stamp, stamp, mid))
        con.execute("UPDATE card_batch SET status = 'written' WHERE status = 'approved' AND NOT EXISTS"
                    " (SELECT 1 FROM card_draft d WHERE d.batch_id = card_batch.id AND d.status IN ('approved', 'error'))")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def mark_error(con: sqlite3.Connection, mnemosyne_ids: list[str], error: str) -> None:
    """Record a write failure. The draft stays approved, so the next write retries it."""
    stamp = now()
    for mid in mnemosyne_ids:
        con.execute("UPDATE card_draft SET error = ?, updated_at = ? WHERE mnemosyne_id = ?", (error[:2000], stamp, mid))


def unnotified(con: sqlite3.Connection) -> list[Batch]:
    """Batches Telegram hasn't mentioned: pending ones need buttons; auto-approved ones get a short note.
    Viewer batches are skipped because Aman approved every card there himself, and review batches because
    he reviews those on /review."""
    rows = con.execute("SELECT b.id FROM card_batch b JOIN card_source s ON s.id = b.source_id"
                       " WHERE b.notified_at IS NULL AND b.status != 'empty' AND s.origin NOT IN ('viewer', 'review')"
                       " ORDER BY b.created_at")
    return [b for r in rows.fetchall() if (b := get_batch(con, r["id"])) is not None]


def mark_notified(con: sqlite3.Connection, batch_id: str, message_ref: str) -> None:
    con.execute("UPDATE card_batch SET notified_at = ?, message_ref = ? WHERE id = ?", (now(), message_ref, batch_id))
