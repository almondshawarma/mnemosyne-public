"""Throwaway fixture collections built from tests/fixtures/. Never a real collection."""
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from anki.collection import Collection
from anki.decks import DeckId

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def legacy_col_path(tmp_path: Path) -> Path:
    path = tmp_path / "legacy.anki2"
    col = Collection(str(path))
    for row in json.loads((FIXTURES / "legacy_notes.json").read_text(encoding="utf-8")):
        notetype = col.models.by_name(row.get("notetype", "Basic"))
        assert notetype is not None
        note = col.new_note(notetype)
        note.fields[0], note.fields[1] = row["front"], row["back"]
        note.tags = row["tags"]
        col.add_note(note, DeckId(col.decks.id(row.get("deck", "Default")) or 1))
        if reviews := row.get("reviews", 0):  # first card becomes a review card with that much history
            cid = note.cards()[0].id
            assert col.db is not None
            col.db.execute("update cards set type = 2, queue = 2, ivl = 1, due = 0 where id = ?", cid)
            for i in range(reviews):
                col.db.execute("insert into revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type)"
                               " values (?, ?, -1, 3, 1, 0, 2500, 5000, 1)", cid * 10 + i, cid)
    col.close()
    return path


@pytest.fixture
def legacy_db(legacy_col_path: Path) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(f"file:{legacy_col_path}?mode=ro", uri=True)
    yield con
    con.close()
