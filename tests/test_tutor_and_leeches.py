"""P4's rest: the learner model the tutor reads, the leech doctor, daily metrics, and the Sunday report."""
import asyncio
import copy
import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mnemosyne import learner
from mnemosyne.api import create_app
from mnemosyne.cards import Schema
from mnemosyne.config import Settings
from mnemosyne.ledger import db, store
from mnemosyne.ledger.db import now
from mnemosyne.project import metrics, report
from mnemosyne.schedule import leech
from test_service import APPROVE, PROPOSE, FakeCore, bearer

ROOT = Path(__file__).resolve().parent.parent
ANCHORS = ROOT / "data" / "anchors.yaml"
TZ = "America/Chicago"
BASIC = ["Front", "Back", "SpeakFront", "SpeakBack", "AudioFront", "AudioBack", "Elaboration", "Reverse", "Source",
         "SourceLink", "MnemosyneID"]


class Collection(FakeCore):
    """A tiny collection behind anki-core's read tools, plus the few writes the leech doctor makes."""

    def __init__(self) -> None:
        super().__init__()
        self.cards: dict[int, dict[str, Any]] = {}
        self.anki_notes: dict[int, dict[str, Any]] = {}
        self.searches: dict[str, list[int]] = {}
        self.totals: dict[str, int] = {}
        self.reviews: dict[Any, list[dict[str, Any]]] = {}
        self.suspended: set[int] = set()
        self.untagged: list[dict[str, Any]] = []
        self.log: list[str] = []

    def add(self, cid: int, front: str, back: str, *, lapses: int = 0, r: float | None = 0.95, d: float = 5.0,
            tags: tuple[str, ...] = (), mid: str = "", queue: int = 2, ctype: int = 2) -> None:
        values = {"Front": front, "Back": back, "SpeakFront": front, "SpeakBack": back, "Source": "legacy",
                  "MnemosyneID": mid or f"legacy-{cid}"}
        fields = [{"name": n, "value": values.get(n, "")} for n in BASIC]
        nid = cid + 1000
        self.anki_notes[nid] = {"id": nid, "note_type_name": "M-Basic", "fields": fields, "tags": list(tags),
                                "card_ids": [cid]}
        self.cards[cid] = {"id": cid, "note_id": nid, "deck_name": "Core", "fields": fields,
                           "scheduling": {"type": ctype, "queue": queue, "lapses": lapses, "reps": 10, "interval": 5},
                           "review_summary": {"reviews": 10, "lapses": lapses, "latest_review_at": 1790000000,
                                              "average_answer_seconds": 7.5, "original_deck": None},
                           "fsrs": {"memory_state": {"stability": 3.2, "difficulty": d}, "retrievability": r}}

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.log.append(tool)
        if tool == "anki_cards_search":
            ids = self.searches.get(arguments["query"], [])
            page = ids[arguments["offset"]:arguments["offset"] + arguments["limit"]]
            return {"items": [{"id": i} for i in page], "total": self.totals.get(arguments["query"], len(ids)),
                    "has_more": False}
        if tool == "anki_cards_get":
            return copy.deepcopy(self.cards[arguments["card_id"]])
        if tool == "anki_notes_get":
            return copy.deepcopy(self.anki_notes[arguments["note_id"]])
        if tool == "anki_reviews_list":
            return {"items": self.reviews.get(arguments.get("card_id") or arguments.get("query"), []), "has_more": False}
        if tool == "anki_review_stats":
            return {"summary": {"retention": 0.88, "reviews": 120, "ratings": {"again": 14}, "daily": []}}
        if tool == "anki_decks_list":
            return {"items": [{"name": "Core", "id": 1}, {"name": "~Exam::PHYS207-Exam-I", "id": 2}], "has_more": False}
        if tool == "anki_notes_search":
            return {"items": [{"id": n["id"], "first_field": n["fields"][0]["value"]} for n in self.anki_notes.values()],
                    "has_more": False}
        if tool == "anki_notes_remove_tags":
            self.untagged.append(arguments)
            return {"state": "committed"}
        if tool == "anki_cards_suspend":
            self.suspended |= set(arguments["card_ids"])
            return {"state": "committed"}
        raise AssertionError(tool)


class ScriptedLLM:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.systems: list[str] = []

    async def complete(self, route: str, system: str, content: list[dict[str, Any]]) -> str:
        assert route == "leech"
        self.systems.append(system)
        return self.replies.pop(0)


def fenced(doc: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(doc) + "\n```"


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "h.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def schema() -> Schema:
    return Schema.load(ROOT / "desktop" / "notetypes", ROOT / "config" / "decks.yaml")


# ---------- the learner model ----------

def test_card_view_reads_prompt_answer_and_memory() -> None:
    col = Collection()
    col.add(1, "<b>Half-life</b> of Co-60 (3 sig figs)", "\\(5.27\\) y", lapses=2, r=0.734)
    v = learner.card_view(col.cards[1])
    assert v["prompt"] == "Half-life of Co-60 (3 sig figs)" and v["answer"] == "\\(5.27\\) y"
    assert (v["state"], v["retrievability"], v["lapses"], v["last_review"]) == ("review", 0.73, 2, "2026-09-21")
    col.add(2, "new one", "x", queue=0, ctype=0, r=None)
    assert learner.card_view(col.cards[2])["state"] == "new" and learner.card_view(col.cards[2])["retrievability"] is None


def test_plain_words_search_every_field_and_anki_syntax_passes_through() -> None:
    assert learner.anki_query("debye length") == '"debye" "length"'
    assert learner.anki_query("tag:course::nuen201::l07* prop:r<0.8") == "tag:course::nuen201::l07* prop:r<0.8"


def test_search_summarizes_what_is_known_and_weak() -> None:
    col = Collection()
    col.add(1, "Debye length", "formula", r=0.97)
    col.add(2, "Debye length as T→0", "→0", r=0.6, lapses=1)
    col.add(3, "Debye sphere", "N_D", queue=0, ctype=0, r=None)
    col.searches['"debye"'] = [1, 2, 3]
    out = asyncio.run(learner.search(col, "debye"))
    assert out["summary"] == {"new": 1, "seen": 2, "mean_retrievability": 0.78, "weak": 1}


def test_a_snapshot_counts_only_after_todays_rollover(con: sqlite3.Connection) -> None:
    learner.save_snapshot(con, {"hello": 1})
    assert learner.snapshot(con, TZ) == {"hello": 1}
    con.execute("UPDATE learner_snapshot SET created_at = '2020-01-01T00:00:00+00:00'")
    assert learner.snapshot(con, TZ) is None


# ---------- the leech doctor ----------

def struggling(col: Collection) -> None:
    col.add(10, "Gas constant", "8.314 J/(mol·K)", lapses=6, tags=("marked", "topic::physics::constants"))
    col.add(11, "Coulomb constant", "8.99e9", lapses=3)
    col.add(12, "Stefan–Boltzmann σ", "5.67e-8", lapses=4, d=9.1)
    col.searches[leech.TIERS[0]] = [10]
    col.searches[leech.TIERS[2]] = [10]
    col.searches[leech.TIERS[3]] = [12]
    col.searches[leech.TIERS[4]] = [11]


def test_candidates_come_most_urgent_first_and_once(con: sqlite3.Connection) -> None:
    col = Collection()
    struggling(col)
    found = asyncio.run(leech.find(col, con, limit=5))
    assert [f["card"]["id"] for f in found] == [10, 12, 11], "marked, then 4 lapses, then 3"
    assert found[0]["marked"] and found[0]["tier"] == leech.TIERS[0]


def test_a_closed_case_reopens_only_after_another_lapse(con: sqlite3.Connection) -> None:
    col = Collection()
    struggling(col)
    for cid, lapses, status in ((11, 3, "kept"), (12, 3, "fixed")):
        con.execute("INSERT INTO leech_case (card_id, note_id, note_type, deck, fields_json, tags_json, signals_json,"
                    " lapses, status, found_at) VALUES (?, 1, 'M-Basic', 'Core', '{}', '[]', '{}', ?, ?, 'x')",
                    (cid, lapses, status))
    found = asyncio.run(leech.find(col, con, limit=5))
    assert [f["card"]["id"] for f in found] == [10, 12], "11 hasn't lapsed since it was kept; 12 has"


REWRITE = {"diagnosis": "ambiguous_cue", "explanation": "Two gas constants exist; the cue doesn't say which.",
           "action": "rewrite", "fields": {"Front": "Gas constant \\(R\\) in J/(mol·K) (4 sig figs)",
                                           "Back": "\\(8.314\\) J/(mol·K)",
                                           "SpeakFront": "the gas constant R, in joules per mole kelvin",
                                           "SpeakBack": "eight point three one four joules per mole kelvin"}}


def doctor(con: sqlite3.Connection, col: Collection, schema: Schema, llm: ScriptedLLM) -> leech.Doctor:
    return leech.Doctor(con, col, llm, None, schema, (ROOT / ".claude/skills/mnemosyne-cards/SKILL.md").read_text(
        encoding="utf-8"), ANCHORS)


def test_the_doctor_diagnoses_with_the_canon_and_waits_for_aman(con: sqlite3.Connection, schema: Schema) -> None:
    col = Collection()
    struggling(col)
    col.reviews[10] = [{"rating": 1, "rating_label": "Again", "answer_seconds": 9, "review_kind": "review"}]
    llm = ScriptedLLM(fenced(REWRITE))
    out = asyncio.run(leech.run(doctor(con, col, schema, llm), limit=1))
    assert out["diagnosed"] == 1 and "mnemosyne canon" in llm.systems[0] and "## The canon" in llm.systems[0]
    [case] = leech.cases(con)
    assert (case["diagnosis"], case["action"], case["problems"]) == ("ambiguous_cue", "rewrite", [])
    assert case["proposal"]["fields"]["Source"] == "legacy", "a rewrite keeps the fields it doesn't change"
    assert "MnemosyneID" not in case["proposal"]["fields"]
    assert case["signals"]["recent_reviews"] == ["Again 9s (review)"] and case["signals"]["marked"]
    assert not col.notes and not col.suspended, "nothing is written before Aman applies it"


def test_an_unusable_answer_is_retried_once_then_recorded(con: sqlite3.Connection, schema: Schema) -> None:
    col = Collection()
    struggling(col)
    llm = ScriptedLLM("no idea", fenced({"diagnosis": "vibes", "action": "rewrite", "fields": {}}))
    out = asyncio.run(leech.run(doctor(con, col, schema, llm), limit=1))
    assert out["failed"] == 1 and not llm.replies
    row = con.execute("SELECT status, problems_json FROM leech_case").fetchone()
    assert row["status"] == "failed" and "diagnosis must be one of" in row["problems_json"]


def proposed(con: sqlite3.Connection, col: Collection, schema: Schema, doc: dict[str, Any]) -> leech.Case:
    asyncio.run(leech.run(doctor(con, col, schema, ScriptedLLM(fenced(doc))), limit=1))
    case = leech.get_case(con, leech.cases(con)[0]["id"])
    assert case is not None
    return case


def test_applying_a_rewrite_edits_the_legacy_note_in_place(con: sqlite3.Connection, schema: Schema) -> None:
    col = Collection()
    struggling(col)
    case = proposed(con, col, schema, REWRITE)
    out = asyncio.run(leech.decide(con, col, schema, ANCHORS, case, "apply", "review-page", "yes"))
    assert out["rewrite"] == "upsert" and out["status"] == "fixed"
    written = col.notes["legacy-10"]
    assert written["fields"]["Front"].startswith("Gas constant \\(R\\) in J") and written["fields"]["MnemosyneID"] == "legacy-10"
    assert written["tags"] == [], "anki-core only adds tags, so a rewrite sends none"
    assert col.untagged[0]["tags"] == ["marked"], "the mark comes off once it's handled"
    with pytest.raises(ValueError, match="is fixed"):
        asyncio.run(leech.decide(con, col, schema, ANCHORS, leech.get_case(con, case.id), "keep", "x", ""))  # type: ignore[arg-type]


def test_keep_and_dismiss(con: sqlite3.Connection, schema: Schema) -> None:
    col = Collection()
    struggling(col)
    case = proposed(con, col, schema, {"diagnosis": "fine", "explanation": "Just hard.", "action": "keep"})
    with pytest.raises(ValueError, match="say why"):
        asyncio.run(leech.decide(con, col, schema, ANCHORS, case, "dismiss", "x", ""))
    out = asyncio.run(leech.decide(con, col, schema, ANCHORS, case, "keep", "x", ""))
    assert out["status"] == "kept" and col.untagged


@pytest.fixture
def api(tmp_path: Path) -> tuple[TestClient, Collection, sqlite3.Connection, Settings]:
    (tmp_path / "propose").write_text(PROPOSE)
    (tmp_path / "approve").write_text(APPROVE)
    settings = Settings(ledger=tmp_path / "helios.db", repo=ROOT, propose_token_file=tmp_path / "propose",
                        approve_token_file=tmp_path / "approve")
    col = Collection()
    c = db.connect(settings.ledger)
    return TestClient(create_app(settings, core=col, con=c, schedule=False)), col, c, settings


REPLACE = {"diagnosis": "not_atomic", "explanation": "Asks for two constants at once.", "action": "replace",
           "cards": [{"note_type": "M-Basic", "deck": "Core", "mode": "audio",
                      "fields": {"Front": "Coulomb constant \\(k\\) in N·m²/C² (3 sig figs)",
                                 "Back": "\\(8.99\\times10^{9}\\) N·m²/C²",
                                 "SpeakFront": "Coulomb's constant k, in newton meters squared per coulomb squared",
                                 "SpeakBack": "eight point nine nine times ten to the nine"},
                      "tags": ["topic::physics::constants", "type::anchor", "src::chat"],
                      "checks": [{"quantity": "1/(4*pi*vacuum electric permittivity)", "value": "8.99e9 N*m^2/C^2",
                                  "tolerance": "0.1%"}]}]}


def test_a_replacement_is_written_before_the_old_card_is_suspended(api: Any, schema: Schema) -> None:
    client, col, c, _ = api
    col.add(11, "Coulomb constant and ε0", "8.99e9; 8.85e-12", lapses=5)
    col.searches[leech.TIERS[2]] = [11]
    case = proposed(c, col, schema, REPLACE)
    assert client.get("/leeches", headers=bearer(PROPOSE)).status_code == 401, "deciding is Aman's"
    listed = client.get("/review/queue", headers=bearer(APPROVE)).json()["leeches"]
    assert [x["id"] for x in listed] == [case.id]
    r = client.post(f"/leeches/{case.id}/decision", json={"verdict": "apply", "note": "right, two facts"},
                    headers=bearer(APPROVE))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "fixed" and col.suspended == {11}
    [draft] = store.batch_drafts(c, store.list_batches(c, None)[0].id)
    assert draft.status == "written" and "gen::ai" in draft.tags


def test_a_replacement_waits_if_the_write_fails(api: Any, schema: Schema) -> None:
    client, col, c, _ = api
    col.add(11, "Coulomb constant and ε0", "8.99e9; 8.85e-12", lapses=5)
    col.searches[leech.TIERS[2]] = [11]
    case = proposed(c, col, schema, REPLACE)
    col.fail = True
    r = client.post(f"/leeches/{case.id}/decision", json={"verdict": "apply"}, headers=bearer(APPROVE)).json()
    assert r["status"] == "applying" and not col.suspended, "no suspension until the replacement is in Anki"
    col.fail = False
    asyncio.run(create_state_write(client))
    assert asyncio.run(leech.finish_pending(c, col)) == 1 and col.suspended == {11}


async def create_state_write(client: TestClient) -> None:
    await client.app.state.mn.write()  # type: ignore[attr-defined]


def test_learner_reads_need_only_the_propose_token(api: Any) -> None:
    client, _, c, _ = api
    learner.save_snapshot(c, {"today": "cached"})
    assert client.get("/learner/profile", headers=bearer(PROPOSE)).json() == {"today": "cached"}
    stats = client.get("/learner/stats", headers=bearer(PROPOSE)).json()
    assert set(stats) == {"load", "metrics", "cards", "leeches"}
    assert client.get("/learner/profile").status_code == 401


# ---------- metrics ----------

def test_fluency_is_right_and_fast() -> None:
    reviews = [{"rating": 3, "answer_seconds": 2.1}, {"rating": 3, "answer_seconds": 5.0},
               {"rating": 1, "answer_seconds": 1.0}, {"rating": 4, "answer_seconds": 2.9}]
    assert metrics.fluency(reviews) == 0.5 and metrics.fluency([]) is None


def test_line_protocol_for_influx() -> None:
    m = {"reviews": 60, "minutes": 11.5, "retention": 0.88, "retention_by_deck": {"Core": 0.9, "Now": None},
         "exam_readiness": {"PHYS207-Exam I": 0.71}, "approval_7d": {"paperless": 0.9}}
    body = metrics.line_protocol(date(2026, 9, 26), TZ, m)
    first, exam, approval = body.split("\n")
    assert first.startswith("mnemosyne reviews=60.0,minutes=11.5,retention=0.88,retention_core=0.9 ")
    assert exam.startswith("mnemosyne_exam,exam=PHYS207-Exam\\ I readiness=0.71 ")
    assert approval.startswith("mnemosyne_approval,origin=paperless rate=0.9 ")


def test_an_anki_day_starts_at_the_rollover() -> None:
    start, end = metrics.anki_day_bounds(date(2026, 9, 26), TZ)
    assert datetime.fromtimestamp(start, UTC).isoformat() == "2026-09-26T09:00:00+00:00" and end - start == 86400


def test_metrics_collect_and_keep(con: sqlite3.Connection) -> None:
    col = Collection()
    con.execute("INSERT INTO load_day VALUES ('2026-09-26', 60, 11.5, 'x')")
    t0 = metrics.anki_day_bounds(date(2026, 9, 26), TZ)[0]
    col.reviews['"deck:Core" rated:2'] = [{"card_id": 1, "reviewed_at": t0 + 100, "rating": 3, "answer_seconds": 2},
                                          {"card_id": 2, "reviewed_at": t0 - 100, "rating": 3, "answer_seconds": 2}]
    m = asyncio.run(metrics.collect(col, con, date(2026, 9, 27), TZ))
    assert (m["reviews"], m["minutes"], m["retention"], m["core_reviews"], m["fluency_index"]) == (60, 11.5, 0.88, 1, 1.0)
    metrics.save(con, date(2026, 9, 26), m)
    assert learner.stats(con, 55)["metrics"][0]["day"] == "2026-09-26"



def test_upkeep_counts_sittings_and_only_amans_decisions(con: sqlite3.Connection) -> None:
    t0 = datetime(2026, 10, 6, 22, 0, tzinfo=UTC)

    def at(minutes: int) -> str:
        return (t0 + timedelta(minutes=minutes)).isoformat(timespec="seconds")

    con.execute("INSERT INTO review_set (id, title, kind, payload_json, status, created_at)"
                " VALUES ('r1', 'L08', 'lecture', '{}', 'reviewed', ?)", (at(-30),))
    for i, m in enumerate((0, 2, 5, 5)):                       # one sitting; two cards in the same second are one
        con.execute("INSERT INTO review_item (review_id, key, position, card_json, verdict, decided_at)"
                    " VALUES ('r1', ?, ?, '{}', 'approve', ?)", (f"k{i}", i, at(m)))
    con.execute("INSERT INTO exam_map (course, status, detail_json, created_at, decided_at)"
                " VALUES ('PHYS207', 'confirmed', '[]', ?, ?)", (at(-60), at(60)))
    con.execute("INSERT INTO card_source (id, payload_hash, origin, kind, received_at) VALUES (1, 'h', 'mcp', 'chat', ?)",
                (at(90),))
    con.execute("INSERT INTO card_batch (id, source_id, status, decided_by, decided_at, created_at)"
                " VALUES ('auto', 1, 'approved', 'mcp', ?, ?)", (at(90), at(90)))       # the approval policy
    con.execute("INSERT INTO card_batch (id, source_id, status, decided_by, decided_at, created_at)"
                " VALUES ('dupe', 1, 'rejected', 'claude', ?, ?)", (at(120), at(100)))  # Claude's own call
    up = metrics.upkeep(con, t0.timestamp(), (t0 + timedelta(hours=3)).timestamp())
    assert up == {"minutes": 7.0, "decisions": 4}, "5 min + 1 for the review, 1 for the exam map"
    assert metrics.upkeep(con, (t0 + timedelta(minutes=30)).timestamp(), (t0 + timedelta(hours=3)).timestamp()) == {
        "minutes": 1.0, "decisions": 1}


def test_a_session_logs_fresh_problems_with_the_propose_token(api: Any) -> None:
    client, _, c, _ = api
    stamp = now()
    c.execute("INSERT INTO graded_work (doc_id, course, kind, title, created_at, updated_at)"
              " VALUES (466, 'PHYS207', 'exam', 'PHYS 207 Exam 01', ?, ?)", (stamp, stamp))
    c.execute("INSERT INTO mistake (id, doc_id, course, question, kind, concept, evidence, created_at, updated_at)"
              " VALUES (7, 466, 'PHYS207', '5', 'conceptual', 'field between sheets', 'his_note', ?, ?)", (stamp, stamp))
    r = client.post("/mistakes/7/attempts", json={"right": False, "note": "added the outer sheets"},
                    headers=bearer(PROPOSE))
    assert r.status_code == 200 and r.json()["attempts"]["tried"] == 1
    assert client.post("/mistakes/8/attempts", json={"right": True}, headers=bearer(PROPOSE)).status_code == 404
    assert client.post("/mistakes/7/attempts", json={"right": True, "status": "fixed"},
                       headers=bearer(PROPOSE)).status_code == 422, "a try can't close a mistake"
    assert c.execute("SELECT status FROM mistake WHERE id = 7").fetchone()[0] == "open"

# ---------- the weekly report ----------

def test_the_report_waits_for_sunday_morning_and_goes_out_once(api: Any) -> None:
    client, col, c, _ = api
    for i, day in enumerate(("2026-09-28", "2026-09-29", "2026-10-02")):
        c.execute("INSERT INTO load_day VALUES (?, ?, ?, 'x')", (day, 50 + i, 40.0 + i))
    c.execute("INSERT INTO metric_day VALUES ('2026-10-03', ?, 'x')",
              (json.dumps({"retention": 0.9, "retention_by_deck": {"Core": 0.92}, "fluency_index": 0.7}),))
    body = asyncio.run(report.build(col, c, date(2026, 10, 4), TZ, 55))
    assert body.startswith("🧠 Mnemosyne · week to Sat Oct 3")
    assert "studied 3 of 7 days" in body and "Retention (30 days): 90% · Core 92%" in body and "Fluency: 70%" in body
    assert report.deliver_after(date(2026, 10, 4), TZ) == "2026-10-04T14:00:00+00:00", "09:00 in Texas"
    report.save(c, date(2026, 10, 4), body, "2999-01-01T00:00:00+00:00")
    assert not [o for o in client.get("/telegram/outbox", headers=bearer(APPROVE)).json() if o["batch_id"].startswith("report:")]
    c.execute("UPDATE report SET deliver_after = '2000-01-01T00:00:00+00:00'")
    [item] = [o for o in client.get("/telegram/outbox", headers=bearer(APPROVE)).json() if o["batch_id"].startswith("report:")]
    assert item["keyboard"] == [] and item["text"] == body
    client.post("/telegram/sent", json={"batch_id": item["batch_id"], "message_ref": "77"}, headers=bearer(APPROVE))
    assert not report.due(c)


def test_image_and_audio_cards_still_say_what_they_ask() -> None:
    assert learner.plain('<img src="vsepr.png"> What geometry is AX<sub>2</sub>E<sub>2</sub>?') == \
        "[image] What geometry is AX 2 E 2 ?"
    col = Collection()
    col.add(5, "", "bent")
    col.cards[5]["fields"][1]["value"] = "[sound:mn_bent.mp3] bent"
    assert learner.card_view(col.cards[5])["prompt"].startswith("[audio]")


def test_image_only_cards_are_left_to_aman(con: sqlite3.Connection) -> None:
    col = Collection()
    col.add(20, '<img src="conic.png">', "hyperboloid of one sheet", lapses=6)
    col.searches[leech.TIERS[2]] = [20]
    assert asyncio.run(leech.find(col, con)) == []
