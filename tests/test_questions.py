"""Aman's questions: read off annotated slides, kept, sharpened, mirrored to Obsidian, and sent to Hyperion when they
reach past the course. Plus the prompt's new duties: the topic vocabulary, and retrying checks that can't compute."""
import asyncio
import json
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mnemosyne import questions as qstore
from mnemosyne.api import create_app
from mnemosyne.cards import Schema
from mnemosyne.config import Settings
from mnemosyne.generate.propose import Context, generate
from mnemosyne.ingest.paperless import Doc
from mnemosyne.ledger import db
from mnemosyne.pipeline import process
from mnemosyne.project import obsidian
from test_pipeline import TAG, ScriptedLLM, card, estimate, fenced, lecture, services
from test_service import APPROVE, PROPOSE, FakeCore, bearer

ROOT = Path(__file__).resolve().parent.parent
ANCHORS = ROOT / "data" / "anchors.yaml"


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "helios.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def schema() -> Schema:
    return Schema.load(ROOT / "desktop" / "notetypes", ROOT / "config" / "decks.yaml")


QUESTIONS = [
    {"page": 3, "asked": "why does B/A peak at iron?", "about": "binding energy curve", "kind": "mechanism",
     "answer": "Surface and Coulomb terms trade off...", "sharper": "Which two SEMF terms balance to put the peak near A≈60?",
     "beyond_course": False, "card": None},
    {"page": 5, "asked": "could we mine iron from stars?", "about": "nucleosynthesis", "kind": "apply",
     "answer": "Not practically.", "sharper": "How do supernovae distribute iron-peak elements?", "beyond_course": True,
     "card": None},
    {"page": 5, "asked": "   ", "kind": "clarify"},
]


def with_questions(cards: list[dict[str, Any]], qs: list[dict[str, Any]]) -> str:
    body = json.loads(fenced(cards).split("```mnemosyne-cards\n")[1].split("\n```")[0])
    body["questions"] = qs
    return "```mnemosyne-cards\n" + json.dumps(body) + "\n```"


class FakeHyperion:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def investigate(self, subject: str) -> dict[str, Any]:
        self.asked.append(subject)
        return {"id": f"topic:{len(self.asked)}", "status": "queued"}

    async def investigation(self, iid: str) -> dict[str, Any]:
        return {"id": iid, "status": "queued"}


def test_the_prompt_carries_the_topic_vocabulary_and_aman_s_marks(schema: Schema) -> None:
    llm = ScriptedLLM(with_questions([card("Where does binding energy per nucleon peak?")], QUESTIONS))
    ctx = Context.load(ROOT / "config", ANCHORS)
    got = asyncio.run(generate(llm, "lecture", "CANON", "", lecture(), schema, ctx))
    system = llm.prompts[0][0]
    assert "principle" in system and "conservation-of-charge" in system and "red star" in system
    assert [q["asked"] for q in got.questions] == ["why does B/A peak at iron?", "could we mine iron from stars?"]


def test_a_check_that_cannot_compute_goes_back_for_the_retry(schema: Schema) -> None:
    bad = estimate("Plate area for 1 F at a 1 mm gap", "1e8 m^2")
    bad["checks"] = [{"quantity": "Cd/epsilon0 for C=1F, d=1mm", "value": "1e8 m^2", "tolerance": "20%"}]
    good = estimate("Plate area for 1 F at a 1 mm gap", "1e8 m^2")
    good["checks"] = [{"quantity": "1 F * 1 mm / vacuum electric permittivity", "value": "1.13e8 m^2", "tolerance": "2%"}]
    llm = ScriptedLLM(fenced([bad]), fenced([good]))
    got = asyncio.run(generate(llm, "lecture", "CANON", "", lecture(), schema, Context(anchors=ANCHORS)))
    assert got.payload is not None and not got.problems
    assert "pint expression" in str(llm.prompts[1][1][-1])


def test_questions_from_a_lecture_land_in_the_ledger_obsidian_and_hyperion(con: sqlite3.Connection, schema: Schema,
                                                                          tmp_path: Path) -> None:
    llm = ScriptedLLM(with_questions([card("Where does binding energy per nucleon peak?")], QUESTIONS))
    svc = services(con, schema, llm, {})
    root = tmp_path / "Paperless"
    svc.notes_dir, hyper = root, FakeHyperion()
    svc.hyperion = hyper
    path = stub(root, 9, "NUEN 201 L08 - Nuclear Binding Energy")
    before = path.read_text(encoding="utf-8")
    doc = Doc(9, "NUEN 201 L08 - Nuclear Binding Energy", frozenset({3}), 1, "2026-09-28")
    out = asyncio.run(process(svc, doc, {3: "NUEN201"}))
    assert out.questions == 2
    assert hyper.asked == ["How do supernovae distribute iron-peak elements? (context: NUEN 201 L08 - Nuclear Binding Energy)"]
    note = path.read_text(encoding="utf-8")
    assert note.startswith(before.split(obsidian.HELIOS)[0]), "the ingester's top is untouched"
    assert note.index(obsidian.HELIOS) < note.index("## Questions from this lecture") < note.index("## Notes")
    assert "- [ ] ❓ **why does B/A peak at iron?** (p. 3) ^q1" in note and "Asked sharper: *Which two SEMF terms" in note
    assert sorted(p.name for p in root.iterdir()) == ["NUEN 201 L08 - Nuclear Binding Energy.md"], "no second file"
    assert qstore.save_from_lecture(con, 9, "NUEN201", 8, doc.title, doc.added, QUESTIONS[:2]) == [], "no repeats"


def stub(root: Path, doc_id: int, title: str) -> Path:
    """A Paperless stub as the homelab ingester writes it: its top, then the marker, then Aman's part."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{title}.md"
    path.write_text(f'---\ntags:\n  - paperless\npaperless_id: {doc_id}\nday: "[[Sep 28, 2026]]"\nai: open\n---\n'
                    f"# {title}\n\n## Excerpt\nslides…\n\n\n{obsidian.HELIOS} — your notes here are preserved across "
                    "regenerations -->\n\n## Notes\n- \n", encoding="utf-8")
    return path


def regenerate(path: Path) -> None:
    """What the ingester does each morning: a fresh top, and everything from the marker on kept verbatim."""
    text = path.read_text(encoding="utf-8")
    path.write_text("---\npaperless_id: 9\nnew: top\n---\n# regenerated\n\n" + text[text.index(obsidian.HELIOS):],
                    encoding="utf-8")


def test_ticking_a_question_in_obsidian_answers_it_and_aman_s_own_lines_stay(con: sqlite3.Connection,
                                                                           tmp_path: Path) -> None:
    qstore.save_from_lecture(con, 9, "NUEN201", 8, "NUEN 201 L08 - Nuclear Binding Energy", "2026-09-28", QUESTIONS[:2])
    root = tmp_path / "Paperless"
    assert obsidian.sync_doc(con, root / "missing", 9) is None, "no stub yet: wait for the ingester"
    path = stub(root, 9, "NUEN 201 L08 - Nuclear Binding Energy")
    obsidian.sync_doc(con, root, 9)
    text = path.read_text(encoding="utf-8").replace("- [ ] ❓ **why does B/A", "- [x] ❓ **why does B/A")
    path.write_text(text.replace("## Notes\n- ", "## Notes\n- the SEMF lecture answers this"), encoding="utf-8")
    regenerate(path)
    obsidian.sync_doc(con, root, 9)
    assert [q["status"] for q in qstore.for_doc(con, 9)] == ["answered", "open"]
    again = path.read_text(encoding="utf-8")
    assert "- the SEMF lecture answers this" in again and "- [x] ❓ **why does B/A" in again and "# regenerated" in again
    assert again.count(obsidian.START) == 1
    before = again
    obsidian.sync_doc(con, root, 9)
    assert path.read_text(encoding="utf-8") == before, "nothing changed, nothing rewritten"


def test_the_old_question_notes_move_into_the_stubs(con: sqlite3.Connection, tmp_path: Path) -> None:
    qstore.save_from_lecture(con, 9, "NUEN201", 8, "NUEN 201 L08 - Nuclear Binding Energy", "2026-09-28", QUESTIONS[:2])
    old = tmp_path / "Lectures"
    old.mkdir()
    (old / "NUEN 201 L08 - Nuclear Binding Energy · questions.md").write_text(
        "---\nai: open\n---\n<!-- mnemosyne:start -->\n- [x] ❓ **why does B/A peak at iron?** ^q1\n"
        "<!-- mnemosyne:end -->\n\n## My notes\n- ask in office hours\n", encoding="utf-8")
    (old / "PHYS 207 L09 - Nothing yet · questions.md").write_text("- [ ] ❓ **?** ^q99\n", encoding="utf-8")
    path = stub(tmp_path / "Paperless", 9, "NUEN 201 L08 - Nuclear Binding Energy")
    moved = obsidian.retire_question_notes(con, old, tmp_path / "Paperless")
    assert moved == ["NUEN 201 L08 - Nuclear Binding Energy · questions.md"]
    assert [p.name for p in old.iterdir()] == ["PHYS 207 L09 - Nothing yet · questions.md"], "no stub, so it stays"
    text = path.read_text(encoding="utf-8")
    assert "- [x] ❓ **why does B/A peak at iron?**" in text and "- ask in office hours" in text
    assert qstore.for_doc(con, 9)[0]["status"] == "answered"


def test_the_week_in_questions(con: sqlite3.Connection) -> None:
    qstore.save_from_lecture(con, 9, "NUEN201", 8, "NUEN 201 L08", "2026-09-28", QUESTIONS[:2])
    qstore.add(con, qstore.QuestionIn(asked="what does κ mean?", kind="clarify", course="phys207"))
    st = qstore.stats(con, date(2020, 1, 1))
    assert st["asked"] == 3 and st["kinds"] == {"mechanism": 1, "apply": 1, "clarify": 1} and st["open"] == 3
    assert st["deep_share"] == 0.33 and st["example"]["kind"] == "mechanism"


@pytest.fixture
def api(tmp_path: Path) -> tuple[TestClient, sqlite3.Connection]:
    (tmp_path / "propose").write_text(PROPOSE)
    (tmp_path / "approve").write_text(APPROVE)
    settings = Settings(ledger=tmp_path / "helios.db", repo=ROOT, propose_token_file=tmp_path / "propose",
                        approve_token_file=tmp_path / "approve", stubs_dir=tmp_path / "Paperless")
    stub(tmp_path / "Paperless", 9, "NUEN 201 L08 - Nuclear Binding Energy")
    c = db.connect(settings.ledger)
    return TestClient(create_app(settings, core=FakeCore(), con=c, schedule=False)), c


def test_questions_over_the_api(api: tuple[TestClient, sqlite3.Connection]) -> None:
    client, c = api
    kept = card("Which two SEMF terms balance to put the B/A peak near A≈60?")
    qstore.save_from_lecture(c, 9, "NUEN201", 8, "NUEN 201 L08 - Nuclear Binding Energy", "2026-09-28",
                             [{**QUESTIONS[0], "card": kept}, QUESTIONS[1]])
    made = client.post("/questions", json={"asked": "why is ε0 in Coulomb's law?", "kind": "mechanism"},
                       headers=bearer(PROPOSE))
    assert made.status_code == 200, "a session may log a question with the propose token"
    assert client.get("/questions", headers=bearer(PROPOSE)).status_code == 401, "reading them is Aman's"
    listed = client.get("/review/queue", headers=bearer(APPROVE)).json()
    assert len(listed["questions"]) == 3 and listed["question_stats"]["asked"] == 3
    r = client.post("/questions/1/decision", json={"action": "card"}, headers=bearer(APPROVE)).json()
    assert r["status"] == "carded" and r["mnemosyne_id"] and r["written"]["drafts"] == 1
    assert client.post("/questions/2/decision", json={"action": "hyperion"},
                       headers=bearer(APPROVE)).status_code == 503, "no Helios MCP configured in tests"
    assert client.post("/questions/2/decision", json={"action": "dropped", "note": "idle thought"},
                       headers=bearer(APPROVE)).json()["status"] == "dropped"
    note = next((Path(c.execute("PRAGMA database_list").fetchone()[2]).parent / "Paperless").glob("*.md")).read_text(
        encoding="utf-8")
    assert "- [x] ❓ **why does B/A peak at iron?**" in note and "carded" in note


def test_a_re_filed_deck_adds_no_question_twice_and_fills_a_missing_card(con: sqlite3.Connection) -> None:
    first = [{"page": 3, "asked": "what's the difference? Only stay in R^n if start is R^n", "kind": "clarify"},
             {"page": 2, "asked": "κ?", "kind": "clarify"}]
    again = [{"page": 3, "asked": "What's the difference? - card it", "kind": "clarify", "card": card("Operator vs map")},
             {"page": 2, "asked": "α?", "kind": "clarify"},
             {"page": 4, "asked": "what's the difference? Only stay in R^n if start is R^n", "kind": "clarify"}]
    assert len(qstore.save_from_lecture(con, 462, "MATH309", 6, "MATH 309 L06 - Linear Maps", "2026-10-05", first)) == 2
    new = qstore.save_from_lecture(con, 467, "MATH309", 6, "MATH 309 L06 - Linear Maps", "2026-10-07", again)
    assert len(new) == 2, "a short different question and another page's question are new; the re-read one isn't"
    kept = qstore.get(con, 1)
    assert kept is not None and kept["card"]["fields"]["Front"] == "Operator vs map", "the second reading's card fills in"
    assert qstore.same_question("When do you do which?", "when do you do which ?")


QTAG = "course::math309::l06-linear-maps"


def math_q(con: sqlite3.Connection) -> dict[str, Any]:
    qstore.save_from_lecture(con, 467, "MATH309", 6, "MATH 309 L06 - Linear Maps", "2026-10-07",
                             [{"page": 3, "asked": "what's the difference?", "kind": "clarify",
                               "answer": "An operator maps a space to itself.", "sharper": "When is a map an operator?"}])
    q = qstore.get(con, 1)
    assert q is not None
    return q


def test_a_question_without_a_card_gets_one_written(con: sqlite3.Connection, schema: Schema) -> None:
    from mnemosyne.pipeline import write_question_card
    good = card("When is a linear map an operator?", tags=[QTAG, "topic::math::linear-algebra", "type::fundamental",
                                                           "src::slides"])
    untagged = {**good, "tags": ["topic::math::linear-algebra", "type::fundamental", "src::slides"]}
    llm = ScriptedLLM("```json\n" + json.dumps({"card": untagged}) + "\n```",
                      "```json\n" + json.dumps({"card": good, "no_card": None}) + "\n```")
    svc = services(con, schema, llm, {})
    q = math_q(con)
    assert [w["id"] for w in qstore.without_cards(con)] == [q["id"]]
    out = asyncio.run(write_question_card(svc, q))
    assert out["card"] and not out["problems"]
    assert "missing the lecture tag" in str(llm.prompts[1][1][-1]), "the retry names the problem"
    assert QTAG in str(llm.prompts[0][1][0]) and "## This pass: one of Aman's questions" in llm.prompts[0][0]
    stored = qstore.get(con, 1)
    assert stored is not None and stored["card"]["fields"]["Front"] == "When is a linear map an operator?"
    assert qstore.without_cards(con) == []


def test_a_question_can_say_why_it_has_no_card(con: sqlite3.Connection, schema: Schema) -> None:
    from mnemosyne.pipeline import write_question_card
    llm = ScriptedLLM('```json\n{"card": null, "no_card": "exam logistics"}\n```')
    out = asyncio.run(write_question_card(services(con, schema, llm, {}), math_q(con)))
    stored = qstore.get(con, 1)
    assert out["no_card"] == "exam logistics" and stored is not None and stored["no_card"] == "exam logistics"
    assert stored["card"] is None and qstore.without_cards(con) == []


def test_principle_tags_are_valid(schema: Schema) -> None:
    from mnemosyne.cards import CardIn
    c = CardIn.model_validate({**card("Energy stored in a capacitor"),
                               "tags": [TAG, "topic::physics::em::capacitance", "principle::energy-storage",
                                        "type::result", "src::slides"]})
    assert schema.problems(c) == []


def test_tags_can_be_added_to_a_waiting_card_but_never_invalid_ones(api: tuple[TestClient, sqlite3.Connection]) -> None:
    client, c = api
    made = client.post("/inbox", json={"payload": {"source": {"kind": "slides", "course": "PHYS207", "lecture": 7},
                                                   "cards": [card("Energy stored in a capacitor, given C and V")]},
                                       "origin": "mcp"}, headers=bearer(PROPOSE)).json()
    mid = c.execute("SELECT mnemosyne_id FROM card_draft WHERE batch_id = ?", (made["batch_id"],)).fetchone()[0]
    r = client.post(f"/drafts/{mid}/tags", json={"add": ["course::phys217::l06-capacitance", "principle::energy-storage"],
                                                 "note": "same idea in PHYS 217 L06"}, headers=bearer(APPROVE))
    assert r.json() == {"gained": ["course::phys217::l06-capacitance", "principle::energy-storage"], "status": "pending"}
    assert client.post(f"/drafts/{mid}/tags", json={"add": ["Bad Tag"], "note": "x"},
                       headers=bearer(APPROVE)).status_code == 400
