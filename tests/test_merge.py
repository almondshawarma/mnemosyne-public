"""Cross-course duplicates (canon 22): one note per idea, carrying every course, worded in the most fundamental
framing. The ranking reads the real config, so these also pin what Aman decided on 2026-10-21."""
import asyncio
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from mnemosyne import merge, weekly
from mnemosyne.cards import Payload, Schema
from mnemosyne.ledger import db, store
from mnemosyne.pipeline import check_and_propose
from test_pipeline import ROOT, ScriptedLLM, card, services

F = merge.Foundations.load(ROOT / "config")
L09 = "course::math309::l09-dot-product-orthogonal-subspaces-and-least"


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "helios.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def schema() -> Schema:
    return Schema.load(ROOT / "desktop" / "notetypes", ROOT / "config" / "decks.yaml")


def test_the_more_fundamental_framing_wins() -> None:
    math309 = [L09, "topic::math::linear-algebra::orthogonality", "type::formula"]
    calc3 = ["topic::math::linear-algebra::vectors", "src::legacy"]                       # legacy: no course tag
    statics = ["course::meen221::l03-cartesian-vectors", "topic::mechanics::statics",
               "topic::math::linear-algebra::vectors", "type::formula"]
    assert merge.merges_up(F, math309, calc3), "MATH 309 is linear algebra's home; Calc III only used it"
    assert merge.merges_up(F, math309, statics), "…and statics only used it"
    assert not merge.merges_up(F, statics, math309), "the reverse is an ordinary duplicate"
    lab = ["course::phys217::l06-capacitance", "topic::physics::em::capacitance", "type::result"]
    lecture = ["course::phys207::l07-capacitors", "topic::physics::em::capacitance", "type::result"]
    assert merge.merges_up(F, lecture, lab) and not merge.merges_up(F, lab, lecture)
    assert not merge.merges_up(F, math309, [*math309, "course::math309::l05-fundamental-subspaces"]), \
        "the same course carding it again is never a merge-up"
    assert not merge.merges_up(F, math309, ["course::math309::l01-introduction", "topic::math::linear-algebra::vectors"])


def dot(tags: list[str]) -> dict[str, Any]:
    c = card("Dot product of two vectors, in components")
    c["tags"] = tags
    return c


def payload(course: str, lecture: int, c: dict[str, Any]) -> Payload:
    return Payload.model_validate({"source": {"kind": "slides", "course": course, "lecture": lecture}, "cards": [c]})


def test_a_more_fundamental_duplicate_is_held_as_a_merge_up(con: sqlite3.Connection, schema: Schema) -> None:
    svc = services(con, schema, ScriptedLLM(""), {5: "Dot product of two vectors, in components"})
    svc.core.notes[5] = {"tags": ["course::meen221::l03-cartesian-vectors", "topic::mechanics::statics"]}  # type: ignore[attr-defined]
    out = asyncio.run(check_and_propose(svc, payload("MATH309", 9, dot([L09, "topic::math::linear-algebra::vectors",
                                                                         "type::formula", "src::slides"])),
                                        "paperless", "slides", False))
    assert (out.created, out.duplicates, out.adopted, out.dropped) == (1, 0, {}, {})
    [d] = store.batch_drafts(con, out.batch_id or "")
    assert (d.status, d.merge_into, d.merge_up) == ("pending", 5, True)
    assert (d.review_reason or "").startswith("merge_up (1.00)")
    assert not svc.core.tagged, "nothing is handed over until Aman taps"  # type: ignore[attr-defined]


def test_a_less_fundamental_duplicate_is_still_dropped_and_adopted(con: sqlite3.Connection, schema: Schema) -> None:
    svc = services(con, schema, ScriptedLLM(""), {5: "Dot product of two vectors, in components"})
    svc.core.notes[5] = {"tags": [L09, "topic::math::linear-algebra::vectors"]}  # type: ignore[attr-defined]
    statics = ["course::meen221::l03-cartesian-vectors", "topic::math::linear-algebra::vectors", "type::formula",
               "src::slides"]
    out = asyncio.run(check_and_propose(svc, payload("MEEN221", 3, dot(statics)), "paperless", "slides", False))
    assert (out.created, out.duplicates) == (0, 1)
    assert out.adopted == {5: ["course::meen221::l03-cartesian-vectors", "topic::math::linear-algebra::vectors"]}


def held(con: sqlite3.Connection, schema: Schema, notes: dict[int, dict[str, Any]]) -> tuple[Any, store.Draft]:
    svc = services(con, schema, ScriptedLLM(""), {5: "Dot product of two vectors, in components"})
    svc.core.notes.update(notes)  # type: ignore[attr-defined]
    out = asyncio.run(check_and_propose(svc, payload("MATH309", 9, dot([L09, "topic::math::linear-algebra::vectors",
                                                                         "principle::linearity", "type::formula",
                                                                         "src::slides"])),
                                        "paperless", "slides", False))
    [d] = store.batch_drafts(con, out.batch_id or "")
    return svc, d


def test_merge_up_rewrites_a_legacy_note_in_place(con: sqlite3.Connection, schema: Schema) -> None:
    svc, d = held(con, schema, {5: {"tags": ["topic::math::linear-algebra::vectors", "src::legacy", "mode::audio"],
                                    "fields": [{"name": "Front", "value": "Dot product of two vectors, in components"},
                                               {"name": "AudioFront", "value": "[sound:mn_old.mp3]"},
                                               {"name": "MnemosyneID", "value": "legacy-5"}]}})
    done = asyncio.run(merge.merge(con, svc.core, schema.decks, d, True, "aman", ""))
    assert done["up"] and not done["write"], "a legacy note isn't in the Ledger: it's upserted directly"
    [note] = svc.core.upserted  # type: ignore[attr-defined]
    assert note["fields"]["MnemosyneID"] == "legacy-5", "same note, so its history stays"
    assert note["fields"]["Front"] == d.fields["Front"] and note["fields"]["AudioFront"] == ""
    assert {L09, "principle::linearity", "mode::audio"} <= set(note["tags"])
    assert store.get_draft(con, d.mnemosyne_id).status == "merged"  # type: ignore[union-attr]
    assert store.get_batch(con, d.batch_id).status == "written"  # type: ignore[union-attr]


def test_merge_hands_tags_to_a_note_mnemosyne_wrote(con: sqlite3.Connection, schema: Schema) -> None:
    other = Payload.model_validate({"source": {"kind": "chat"}, "cards": [card("Something else entirely")]})
    from mnemosyne.inbox import Verdict, propose
    prop = propose(con, schema, other, "chat", lambda _i, _c: Verdict(approved=True))
    [old] = store.batch_drafts(con, prop.batch_id or "")
    con.execute("UPDATE card_draft SET status = 'written', note_id = 5 WHERE id = (SELECT MAX(id) FROM card_draft)")
    svc, d = held(con, schema, {5: {"fields": [{"name": "Front", "value": "Dot product of two vectors, in components"},
                                               {"name": "MnemosyneID", "value": old.mnemosyne_id}],
                                    "tags": ["course::meen221::l03-cartesian-vectors", "topic::mechanics::statics"]}})
    assert d.merge_up
    done = asyncio.run(merge.merge(con, svc.core, schema.decks, d, False, "aman", "keep the statics wording"))
    assert done["write"] and set(done["tags"]) == {L09, "topic::math::linear-algebra::vectors", "principle::linearity"}
    revised = store.get_draft(con, old.mnemosyne_id)
    assert revised is not None and revised.status == "approved" and L09 in revised.tags, "the next write adds them"
    assert not svc.core.upserted, "merge (not up) leaves the wording alone"  # type: ignore[attr-defined]
    note = con.execute("SELECT decision_note FROM card_draft WHERE mnemosyne_id = ?", (d.mnemosyne_id,)).fetchone()
    assert note["decision_note"] == "merged into note 5: keep the statics wording"
    with pytest.raises(ValueError):
        asyncio.run(merge.merge(con, svc.core, schema.decks, store.get_draft(con, d.mnemosyne_id), True, "aman", ""))  # type: ignore[arg-type]


def test_the_same_course_carding_it_again_is_an_ordinary_duplicate(con: sqlite3.Connection, schema: Schema) -> None:
    svc = services(con, schema, ScriptedLLM(""), {5: "Dot product of two vectors, in components"})
    svc.core.notes[5] = {"tags": ["course::math309::l05-fundamental-subspaces", "topic::math::linear-algebra"]}  # type: ignore[attr-defined]
    out = asyncio.run(check_and_propose(svc, payload("MATH309", 9, dot([L09, "topic::math::linear-algebra::vectors",
                                                                         "type::formula", "src::slides"])),
                                        "paperless", "slides", False))
    assert (out.created, out.duplicates) == (0, 1) and L09 in out.adopted[5]
    assert con.execute("SELECT COUNT(*) AS n FROM card_draft").fetchone()["n"] == 0


def test_a_drop_is_recorded_with_its_card_for_the_weekly_audit(con: sqlite3.Connection, schema: Schema) -> None:
    svc = services(con, schema, ScriptedLLM(""), {5: "Dot product of two vectors, in components"})
    svc.core.notes[5] = {"tags": [L09, "topic::math::linear-algebra::vectors"]}  # type: ignore[attr-defined]
    statics = ["course::meen221::l03-cartesian-vectors", "topic::math::linear-algebra::vectors", "type::formula",
               "src::slides"]
    asyncio.run(check_and_propose(svc, payload("MEEN221", 3, dot(statics)), "paperless", "slides", False))
    [row] = con.execute("SELECT * FROM dedupe_match").fetchall()
    assert (row["kind"], row["dropped"], row["note_id"], row["origin"]) == ("duplicate", 1, 5, "paperless")
    kept = json.loads(row["card_json"])
    assert kept["card"]["fields"]["Front"].startswith("Dot product") and kept["source"]["course"] == "MEEN221"
    week = weekly.deduped(con, "2000-01-01", "2999-01-01")
    assert week["dropped"] == 1 and week["drops"][0]["matched_note"] == 5 and week["drops"][0]["payload"]["fields"]


def test_a_kept_card_is_recorded_without_its_payload(con: sqlite3.Connection, schema: Schema) -> None:
    _svc, d = held(con, schema, {})
    row = con.execute("SELECT m.* FROM dedupe_match m JOIN card_draft c ON c.content_hash = m.content_hash"
                      " WHERE c.mnemosyne_id = ?", (d.mnemosyne_id,)).fetchone()
    assert row["dropped"] == 0 and row["card_json"] is None and row["model"] == "bag"
