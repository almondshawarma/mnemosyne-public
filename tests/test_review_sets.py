"""Review sets on /review: in-session batches Claude pushes, Aman decides card by card, Claude takes back."""
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mnemosyne.api import create_app
from mnemosyne.config import Settings
from mnemosyne.ledger import db
from test_service import APPROVE, PROPOSE, FakeCore, bearer, card

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def env(tmp_path: Path) -> tuple[TestClient, FakeCore, Settings]:
    (tmp_path / "propose").write_text(PROPOSE)
    (tmp_path / "approve").write_text(APPROVE)
    settings = Settings(ledger=tmp_path / "helios.db", repo=ROOT, propose_token_file=tmp_path / "propose",
                        approve_token_file=tmp_path / "approve")
    core = FakeCore()
    return TestClient(create_app(settings, core=core, schedule=False)), core, settings


def body(review_id: str, *cards: dict[str, Any], kind: str = "lecture") -> dict[str, Any]:
    return {"id": review_id, "title": "NUEN 201 L06", "kind": kind,
            "source": {"kind": "slides", "course": "NUEN201", "lecture": 6},
            "cards": list(cards), "signals": ["you circled slide 8"],
            "skipped": [{"what": "photos of Einstein", "why": "motivation"}, {"what": "γ table", "why": "visual"}],
            "canon": {"1": "Atomic"}}


def keyed(front: str, key: str, **extra: Any) -> dict[str, Any]:
    return {**card(front), "key": key, "why": f"why {front}", **extra}


def push(client: TestClient, b: dict[str, Any]) -> dict[str, Any]:
    r = client.post("/reviews", json=b, headers=bearer(APPROVE))
    assert r.status_code == 200, r.text
    return r.json()


def decide(client: TestClient, review_id: str, *decisions: dict[str, Any]) -> dict[str, Any]:
    r = client.post(f"/reviews/{review_id}/items", json={"decisions": list(decisions)}, headers=bearer(APPROVE))
    assert r.status_code == 200, r.text
    return r.json()


def test_a_session_batch_round_trips_through_the_page(env: tuple[TestClient, FakeCore, Settings]) -> None:
    client, core, _ = env
    made = push(client, body("nuen201-l06-t", keyed("a", "ka"), keyed("b", "kb"), keyed("c", "kc", question="which γ?"),
                             {**card("$bad$"), "key": "kd"}))
    assert made["cards"] == 4 and made["new"] == 3 and made["without_draft"] == 1 and made["url"] == "/review#r/nuen201-l06-t"
    assert core.notes == {}, "nothing is approved by the push"
    assert client.get("/telegram/outbox", headers=bearer(APPROVE)).json() == [], "Telegram leaves these alone"
    queue = client.get("/review/queue", headers=bearer(APPROVE)).json()
    assert queue["batches"] == [], "the held drafts show inside the review, not as loose waiting cards"
    assert [(r["id"], r["cards"], r["decided"]) for r in queue["reviews"]] == [("nuen201-l06-t", 4, 0)]

    full = client.get("/reviews/nuen201-l06-t", headers=bearer(APPROVE)).json()
    items = {i["key"]: i for i in full["items"]}
    assert items["ka"]["card"]["why"] == "why a" and items["ka"]["draft"]["status"] == "pending"
    assert items["kc"]["draft"]["review_reason"] == "question: which γ?"
    assert "never $" in " ".join(items["kd"]["problems"]) and items["kd"]["draft"] is None
    assert full["signals"] == ["you circled slide 8"] and full["canon"] == {"1": "Atomic"}

    out = decide(client, "nuen201-l06-t",
                 {"key": "ka", "action": "approve"},
                 {"key": "kb", "action": "edit", "fields": {"Back": r"\(E_0=mc^2\)"}, "reason": "rest energy"},
                 {"key": "kc", "action": "reject", "reason": ""})
    assert out["changed"] == 2 and "say why" in out["problems"]["kc"][0]
    assert {n["fields"]["Front"]: n["fields"]["Back"] for n in core.notes.values()} == {
        "a": r"\(E=mc^2\)", "b": r"\(E_0=mc^2\)"}
    out = decide(client, "nuen201-l06-t", {"key": "kc", "action": "reject", "reason": "too obvious"},
                 {"key": "ka", "action": "reject", "reason": "changed my mind"})
    assert out["changed"] == 1 and "already decided" in out["problems"]["ka"][0]

    notes = client.put("/reviews/nuen201-l06-t/notes", json={"notes": "more limits", "promote": [1]},
                       headers=bearer(APPROVE))
    assert notes.json() == {"changed": True}
    assert client.post("/reviews/nuen201-l06-t/finish", headers=bearer(APPROVE)).json() == {"changed": True}
    assert client.put("/reviews/nuen201-l06-t/notes", json={"notes": "late"}, headers=bearer(APPROVE)).json() == {
        "changed": False}, "a finished review is Claude's"
    assert client.post("/reviews/nuen201-l06-t/reopen", headers=bearer(APPROVE)).json() == {"changed": True}
    opened = client.get("/reviews/nuen201-l06-t", headers=bearer(APPROVE)).json()
    assert opened["status"] == "open" and sum(1 for i in opened["items"] if i["verdict"]) == 3, "decisions stay"
    assert client.post("/reviews/nuen201-l06-t/finish", headers=bearer(APPROVE)).json() == {"changed": True}

    ex = client.get("/reviews/nuen201-l06-t/export", headers=bearer(APPROVE)).json()
    assert [(c["key"], c["review"]["verdict"]) for c in ex["cards"]] == [("ka", "approve"), ("kb", "edit")]
    assert ex["cards"][1]["fields"]["Back"] == r"\(E_0=mc^2\)"
    assert ex["cards"][1]["review"]["before"] == {"Back": r"\(E=mc^2\)"} and ex["cards"][1]["why"] == "why b"
    assert [(c["key"], c["review"]["reason"]) for c in ex["rejected"]] == [("kc", "too obvious")]
    assert [c["key"] for c in ex["pending"]] == ["kd"] and "kd" in ex["anki"]["no_draft"]
    assert ex["review"] == {"notes": "more limits", "promote": [{"what": "γ table", "why": "visual"}]}
    assert client.post("/reviews/nuen201-l06-t/applied", headers=bearer(APPROVE)).json() == {"changed": True}
    assert client.post("/reviews/nuen201-l06-t/reopen", headers=bearer(APPROVE)).json() == {"changed": False}, (
        "applied is final")
    assert client.get("/review/queue", headers=bearer(APPROVE)).json()["reviews"] == []


def test_cards_already_in_anki_are_linked_and_edits_update_the_note(env: tuple[TestClient, FakeCore, Settings]) -> None:
    client, core, settings = env
    client.post("/inbox", json={"payload": {"source": {"kind": "slides", "course": "NUEN201", "lecture": 6},
                                            "cards": [card("old"), card("gone")]}, "origin": "viewer"},
                headers=bearer(APPROVE))
    assert len(core.notes) == 2
    mid = next(k for k, n in core.notes.items() if n["fields"]["Front"] == "old")
    made = push(client, body("golden-rev-t", keyed("old", "k1"), keyed("gone", "k2"), kind="golden"))
    assert (made["new"], made["linked"]) == (0, 2), "the same content links to its existing draft"

    decide(client, "golden-rev-t", {"key": "k1", "action": "edit", "fields": {"Elaboration": "added"}, "reason": "why"},
           {"key": "k2", "action": "reject", "reason": "trivia"})
    assert core.notes[mid]["fields"]["Elaboration"] == "added", "the written note is updated in place"
    assert len(core.notes) == 2, "rejecting a card that is already in Anki deletes nothing"
    ex = client.get("/reviews/golden-rev-t/export", headers=bearer(APPROVE)).json()
    assert [r["key"] for r in ex["anki"]["rejected_but_in_anki"]] == ["k2"]
    con = db.connect(settings.ledger)
    row = con.execute("SELECT status, before_json FROM card_draft WHERE mnemosyne_id = ?", (mid,)).fetchone()
    assert (row["status"], json.loads(row["before_json"])) == ("written", {"Elaboration": ""})


def test_pushes_never_overwrite_a_review(env: tuple[TestClient, FakeCore, Settings]) -> None:
    client, _, _ = env
    push(client, body("same-id", keyed("a", "ka")))
    assert client.post("/reviews", json=body("same-id", keyed("b", "kb")), headers=bearer(APPROVE)).status_code == 409
    assert client.post("/reviews", json=body("dup-keys", keyed("a", "k"), keyed("b", "k")),
                       headers=bearer(APPROVE)).status_code == 400
    assert client.post("/reviews", json=body("Bad Id", keyed("a", "ka")), headers=bearer(APPROVE)).status_code == 422
    assert client.post("/reviews", json=body("by-propose", keyed("z", "kz")), headers=bearer(PROPOSE)).status_code == 200
    assert client.get("/reviews/by-propose", headers=bearer(PROPOSE)).status_code == 401
    assert client.post("/reviews/by-propose/items", json={"decisions": [{"key": "kz", "action": "approve"}]},
                       headers=bearer(PROPOSE)).status_code == 401


def test_a_mistaken_push_can_be_deleted_until_something_is_decided(env: tuple[TestClient, FakeCore, Settings]) -> None:
    client, _, _ = env
    push(client, body("oops", keyed("a", "ka"), keyed("b", "kb")))
    assert client.delete("/reviews/oops", headers=bearer(APPROVE)).json() == {"changed": True}
    again = push(client, body("oops-2", keyed("a", "ka"), keyed("b", "kb")))
    assert (again["new"], again["linked"]) == (0, 2), "the held drafts are linked again, not duplicated"
    decide(client, "oops-2", {"key": "ka", "action": "approve"})
    assert client.delete("/reviews/oops-2", headers=bearer(APPROVE)).status_code == 409
    assert client.delete("/reviews/nope", headers=bearer(APPROVE)).status_code == 409
