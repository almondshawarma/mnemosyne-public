"""The review page (/review): single-card decisions on waiting drafts, and exam coverage from any device."""
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mnemosyne.api import create_app
from mnemosyne.config import Settings
from mnemosyne.schedule import exams
from test_service import APPROVE, PROPOSE, FakeCore, bearer, payload

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def env(tmp_path: Path) -> tuple[TestClient, FakeCore, Settings]:
    (tmp_path / "propose").write_text(PROPOSE)
    (tmp_path / "approve").write_text(APPROVE)
    settings = Settings(ledger=tmp_path / "helios.db", repo=ROOT, propose_token_file=tmp_path / "propose",
                        approve_token_file=tmp_path / "approve")
    core = FakeCore()
    return TestClient(create_app(settings, core=core, schedule=False)), core, settings


def waiting(client: TestClient, *fronts: str) -> tuple[str, dict[str, str]]:
    """Propose cards the default policy holds for review; return the batch and {front: MnemosyneID}."""
    made = client.post("/inbox", json={"payload": payload(*fronts), "origin": "mcp"}, headers=bearer(PROPOSE)).json()
    queue = client.get("/review/queue", headers=bearer(APPROVE)).json()
    batch = next(b for b in queue["batches"] if b["id"] == made["batch_id"])
    return batch["id"], {d["fields"]["Front"]: d["mnemosyne_id"] for d in batch["drafts"]}


def decide(client: TestClient, batch_id: str, *decisions: dict[str, Any]) -> dict[str, Any]:
    r = client.post(f"/batches/{batch_id}/cards", json={"decisions": list(decisions)}, headers=bearer(APPROVE))
    assert r.status_code == 200, r.text
    return r.json()


def test_page_is_served_but_its_data_needs_the_approve_token(env: tuple[TestClient, FakeCore, Settings]) -> None:
    client, _, _ = env
    page = client.get("/review")
    assert page.status_code == 200 and "Mnemosyne review" in page.text
    assert client.get("/review/queue").status_code == 401
    assert client.get("/review/queue", headers=bearer(PROPOSE)).status_code == 401, "proposing never grants review"
    assert client.post("/batches/x/cards", json={"decisions": [{"mnemosyne_id": "m", "action": "approve"}]},
                       headers=bearer(PROPOSE)).status_code == 401


def test_each_card_is_approved_edited_or_rejected_on_its_own(env: tuple[TestClient, FakeCore, Settings]) -> None:
    client, core, settings = env
    batch_id, ids = waiting(client, "a", "b", "c")
    out = decide(client, batch_id,
                 {"mnemosyne_id": ids["a"], "action": "approve"},
                 {"mnemosyne_id": ids["b"], "action": "edit", "fields": {"Back": r"\(E_0=mc^2\)"}, "deck": "Core",
                  "note": "rest energy"},
                 {"mnemosyne_id": ids["c"], "action": "reject", "note": "too obvious"})
    assert (out["changed"], out["problems"]) == (3, {})
    assert out["batch"]["status"] == "approved"
    assert {n["fields"]["Front"]: (n["deck"], n["fields"]["Back"]) for n in core.notes.values()} == {
        "a": ("Now", r"\(E=mc^2\)"), "b": ("Core", r"\(E_0=mc^2\)")}, "approval writes; the rejected card never does"

    from mnemosyne.ledger import db
    con = db.connect(settings.ledger)
    rows = {r["mnemosyne_id"]: r for r in con.execute("SELECT * FROM card_draft")}
    assert json.loads(rows[ids["b"]]["before_json"]) == {"Back": r"\(E=mc^2\)", "deck": "Now"}
    assert (rows[ids["c"]]["status"], rows[ids["c"]]["decision_note"]) == ("rejected", "too obvious")
    assert rows[ids["a"]]["decided_by"] == "review-page"
    assert client.get("/review/queue", headers=bearer(APPROVE)).json()["batches"] == []

    again = decide(client, batch_id, {"mnemosyne_id": ids["c"], "action": "approve"})
    assert again["changed"] == 0 and "not pending" in again["problems"][ids["c"]][0], "decisions happen once"


def test_a_bad_edit_leaves_the_card_waiting(env: tuple[TestClient, FakeCore, Settings]) -> None:
    client, core, _ = env
    batch_id, ids = waiting(client, "a", "b")
    out = decide(client, batch_id,
                 {"mnemosyne_id": ids["a"], "action": "edit", "fields": {"Back": "$x$"}},
                 {"mnemosyne_id": ids["b"], "action": "edit", "fields": {"MnemosyneID": "mine"}})
    assert out["changed"] == 0
    assert "never $" in " ".join(out["problems"][ids["a"]])
    assert "MnemosyneID" in out["problems"][ids["b"]][0]
    assert core.notes == {}
    out = decide(client, batch_id, {"mnemosyne_id": ids["a"], "action": "edit",
                                    "tags": ["course::nuen201::l06-relativistic-energy", "type::result", "type::fundamental"]})
    assert "exactly one type::" in " ".join(out["problems"][ids["a"]])


def test_partly_decided_batches_stay_in_the_queue(env: tuple[TestClient, FakeCore, Settings]) -> None:
    client, core, _ = env
    batch_id, ids = waiting(client, "a", "b")
    out = decide(client, batch_id, {"mnemosyne_id": ids["a"], "action": "edit",
                                    "tags": ["course::nuen201::l05-relativistic-energy", "type::result"]})
    assert out["batch"]["status"] == "pending"
    tags = next(iter(core.notes.values()))["tags"]
    assert tags[:2] == ["course::nuen201::l05-relativistic-energy", "type::result"]
    assert {"gen::ai", "mode::audio"} <= set(tags), "pipeline tags survive a tag edit"
    left = client.get("/review/queue", headers=bearer(APPROVE)).json()["batches"]
    assert [[d["fields"]["Front"] for d in b["drafts"]] for b in left] == [["b"]]
    telegram = client.post("/telegram/callback", json={"data": f"mn|r|{batch_id}"}, headers=bearer(APPROVE)).json()
    assert telegram["toast"] == "Rejected 2 cards", "Telegram's reject-all still settles what's left"


def test_exam_maps_and_coverage_from_the_page(env: tuple[TestClient, FakeCore, Settings]) -> None:
    client, _, settings = env
    from mnemosyne.ledger import db
    con = db.connect(settings.ledger)
    exams.upsert(con, [exams.Exam("PHYS207", "exam", "Exam I", "2099-09-30T10:20", "PHYS207|exam i"),
                       exams.Exam("PHYS207", "exam", "Exam II", "2099-10-21T10:20", "PHYS207|exam ii"),
                       exams.Exam("NUEN201", "exam", "Exam 1", "2099-10-06", "NUEN201|exam 1")])
    exams.save_map(con, "PHYS207", 12, {"exams": [
        {"title": "Exam I", "starts_at": "2099-09-30T10:20", "lectures": [], "topics": "Ch. 21-23"},
        {"title": "Exam II", "starts_at": "2099-10-21T10:20", "lectures": [7, 8], "topics": ""}], "notes": "by chapter"})
    client.post("/inbox", json={"payload": payload("a", "b"), "origin": "viewer"}, headers=bearer(APPROVE))

    queue = client.get("/review/queue", headers=bearer(APPROVE)).json()
    assert [m["course"] for m in queue["maps"]] == ["PHYS207"] and queue["maps"][0]["detail"]["notes"] == "by chapter"
    assert [e["title"] for e in queue["exams"]] == ["Exam I", "Exam 1", "Exam II"]
    assert queue["coverage"] == {"NUEN201": {"6": 2}}
    assert "Core" in queue["decks"]

    r = client.post("/exams/map/phys207/decision", json={"action": "confirm", "lectures": {"Exam I": [6, 2, 3, 4, 5]}},
                    headers=bearer(APPROVE))
    assert r.json() == {"changed": True}
    got = {r["title"]: (json.loads(r["lectures"]), r["confirmed"]) for r in exams.course_exams(con, "PHYS207")}
    assert got == {"Exam I": ([2, 3, 4, 5, 6], 1), "Exam II": ([7, 8], 1)}

    nuen = next(e for e in queue["exams"] if e["course"] == "NUEN201")
    assert client.put(f"/exams/{nuen['id']}/lectures", json={"lectures": [2, 3, 4, 5, 6, 7, 8, 9, 10]},
                      headers=bearer(APPROVE)).json() == {"changed": True}
    assert client.put(f"/exams/{nuen['id']}/lectures", json={"lectures": [0]}, headers=bearer(APPROVE)).status_code == 400
    assert client.put("/exams/999/lectures", json={"lectures": [1]}, headers=bearer(APPROVE)).status_code == 404
    after = client.get("/review/queue", headers=bearer(APPROVE)).json()
    assert after["maps"] == []
    assert next(e for e in after["exams"] if e["course"] == "NUEN201")["lectures"] == [2, 3, 4, 5, 6, 7, 8, 9, 10]


def test_a_live_card_can_be_revised_with_a_reason(env: tuple[TestClient, FakeCore, Settings]) -> None:
    client, core, _ = env
    client.post("/inbox", json={"payload": payload("a"), "origin": "viewer"}, headers=bearer(APPROVE))
    mid = next(iter(core.notes))
    tags = ["course::nuen201::l06-relativistic-energy", "course::math251", "topic::physics::relativity",
            "type::result", "src::slides"]
    r = client.post(f"/drafts/{mid}/revise", json={"tags": tags, "note": "Aman: relate it to Calc III"},
                    headers=bearer(APPROVE))
    assert r.status_code == 200 and r.json()["changed"] and "tags" in r.json()["before"]
    assert "course::math251" in core.notes[mid]["tags"] and "gen::ai" in core.notes[mid]["tags"]
    again = client.post(f"/drafts/{mid}/revise", json={"tags": tags, "note": "again"}, headers=bearer(APPROVE))
    assert again.json() == {"changed": False}, "re-running is a no-op"
    assert client.post(f"/drafts/{mid}/revise", json={"tags": ["type::x", "type::y"], "note": "bad"},
                       headers=bearer(APPROVE)).status_code == 400
    assert client.post(f"/drafts/{mid}/revise", json={"tags": tags}, headers=bearer(APPROVE)).status_code == 422, \
        "a live card never changes without a reason"
    assert client.post(f"/drafts/{mid}/revise", json={"tags": tags, "note": "x"}, headers=bearer(PROPOSE)).status_code == 401


def test_page_decisions_are_readable_with_their_notes(env: tuple[TestClient, FakeCore, Settings]) -> None:
    client, _, _ = env
    batch_id, ids = waiting(client, "a", "b")
    decide(client, batch_id, {"mnemosyne_id": ids["a"], "action": "reject", "note": "trivia"},
           {"mnemosyne_id": ids["b"], "action": "edit", "fields": {"Elaboration": "why"}})
    got = {d["fields"]["Front"]: d for d in client.get("/review/decisions", headers=bearer(APPROVE)).json()}
    assert (got["a"]["status"], got["a"]["note"], got["a"]["course"]) == ("rejected", "trivia", "NUEN201")
    assert got["b"]["before"] == {"Elaboration": ""}
    assert client.get("/review/decisions?since=2999-01-01", headers=bearer(APPROVE)).json() == []


def test_coverage_counts_by_lecture_tag() -> None:
    from mnemosyne.web.review import coverage
    got = coverage([["course::meen221::l11-equivalent-systems", "type::result"],
                    ["course::phys207::l06-potential", "course::math251", "type::definition"],
                    ["course::phys207::l06-potential", "course::phys207::l05-gauss"],
                    ["topic::math::linear-algebra::vectors", "type::fundamental"]])
    assert got == {"MEEN221": {"11": 1}, "PHYS207": {"6": 2, "5": 1}}


def test_an_exam_can_be_moved_from_the_page(env: tuple[TestClient, FakeCore, Settings]) -> None:
    client, _, settings = env
    from mnemosyne.ledger import db
    con = db.connect(settings.ledger)
    exams.upsert(con, [exams.Exam("NUEN201", "exam", "Exam II", "2099-10-29T15:55:00-05:00", "NUEN201|exam ii")])
    exam_id = con.execute("SELECT id FROM exam WHERE course = 'NUEN201'").fetchone()[0]
    assert client.put(f"/exams/{exam_id}/date", json={"date": "2099-11-03"}).status_code == 401
    r = client.put(f"/exams/{exam_id}/date", json={"date": "2099-11-03"}, headers=bearer(APPROVE))
    assert r.status_code == 200 and r.json()["starts_at"].startswith("2099-11-03T15:55:00")
    assert client.put(f"/exams/{exam_id}/date", json={"date": "Nov 3"}, headers=bearer(APPROVE)).status_code == 422
    assert client.put("/exams/9999/date", json={"date": "2099-11-03"}, headers=bearer(APPROVE)).status_code == 404
    shown = [e for e in client.get("/review/queue", headers=bearer(APPROVE)).json()["exams"] if e["course"] == "NUEN201"]
    assert shown[0]["date_set_by"] == "page" and shown[0]["calendar_starts_at"] == "2099-10-29T15:55:00-05:00"
