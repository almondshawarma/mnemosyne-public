"""The review-page batch tools: review.py (in the skill) and golden_review.py, end to end through /reviews."""
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from golden_review import merge, pending
from mnemosyne.api import create_app
from mnemosyne.config import Settings
from review import canon_titles, hold, keyed, summary
from test_service import APPROVE, FakeCore, bearer

ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = ROOT / ".claude" / "skills" / "mnemosyne-cards" / "SKILL.md"
NOW = "2026-09-25T00:00:00+00:00"


def mk(front: str, **extra: Any) -> dict[str, Any]:
    return {"note_type": "M-Basic", "deck": "Now", "fields": {"Front": front, "Back": "b", "Source": "s"},
            "tags": ["type::result"], "mode": "visual", "checks": [], **extra}


def payload() -> dict[str, Any]:
    return {"source": {"kind": "slides", "course": "PHYS207", "lecture": 6},
            "cards": [mk("one", why="w"), mk("two"), mk("three"), mk("four")], "signals": ["circled"],
            "skipped": [{"what": "photos", "why": "motivation"}, {"what": "symbols", "why": "visual"}]}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    (tmp_path / "approve").write_text(APPROVE)
    settings = Settings(ledger=tmp_path / "helios.db", repo=ROOT, approve_token_file=tmp_path / "approve")
    return TestClient(create_app(settings, core=FakeCore(), schedule=False))


def test_canon_titles_cover_every_rule() -> None:
    titles = canon_titles(SKILL_MD.read_text(encoding="utf-8"))
    assert list(titles) == [str(n) for n in range(1, len(titles) + 1)]
    assert len(titles) >= 20
    assert titles["1"] == "Atomic"


def test_keys_are_stable_and_unique() -> None:
    cards = keyed([mk("same"), mk("same"), mk("other", key="fixed")])
    keys = [c["key"] for c in cards]
    assert len(set(keys)) == 3
    assert keys[2] == "fixed"
    assert keyed([mk("same")])[0]["key"] == keys[0], "content decides the key"


def test_hold_builds_the_reviews_body() -> None:
    body = hold(payload(), "phys207-l06-2026-09-25", "PHYS 207 L06", "lecture")
    assert (body["id"], body["kind"], body["signals"]) == ("phys207-l06-2026-09-25", "lecture", ["circled"])
    assert all(c["key"] for c in body["cards"]) and body["cards"][0]["why"] == "w"
    assert body["canon"]["20"]


@pytest.mark.parametrize(("rid", "kind"), [("Has Caps", "lecture"), ("x", "lecture"), ("ok-id", "chat")])
def test_hold_rejects_bad_ids_and_kinds(rid: str, kind: str) -> None:
    with pytest.raises(ValueError):
        hold(payload(), rid, "t", kind)


def test_a_batch_round_trips_and_summarizes(client: TestClient) -> None:
    body = hold(payload(), "b-one", "t", "lecture")
    k = [c["key"] for c in body["cards"]]
    assert client.post("/reviews", json=body, headers=bearer(APPROVE)).status_code == 200
    client.post("/reviews/b-one/items", headers=bearer(APPROVE), json={"decisions": [
        {"key": k[0], "action": "approve"},
        {"key": k[1], "action": "edit", "fields": {"Back": "B"}, "deck": "Core", "reason": "terser"},
        {"key": k[2], "action": "reject", "reason": "too specific"}]})
    client.put("/reviews/b-one/notes", json={"notes": "more limits please", "promote": [1, 7]}, headers=bearer(APPROVE))
    out = client.get("/reviews/b-one/export", headers=bearer(APPROVE)).json()
    edited = out["cards"][1]
    assert edited["fields"]["Back"] == "B" and edited["deck"] == "Core"
    assert edited["review"]["before"] == {"Back": "b", "deck": "Now"}
    assert out["review"] == {"notes": "more limits please", "promote": [{"what": "symbols", "why": "visual"}]}
    assert summary(out) == "approved 1 · edited 1 · rejected 1 · pending 1 · promoted 1"


def test_golden_round_trip(client: TestClient) -> None:
    old = mk("old", key="k-old", review={"verdict": "approve", "reason": "split it", "at": NOW})
    docs: dict[Path, dict[str, Any]] = {Path("a.json"): {
        "status": "draft", "cards": [mk("kept", key="k1", review={"verdict": "approve", "at": NOW}),
                                     mk("new", key="k2"), mk("bad", key="k3")],
        "superseded": [{**old, "replaced_by": ["k2"]}]}}
    batch = pending(docs)
    assert [c["key"] for c in batch["cards"]] == ["k2", "k3"]
    assert batch["cards"][0]["context"]["reason"] == "split it"

    assert client.post("/reviews", json=hold(batch, "golden-x", "t", "golden"), headers=bearer(APPROVE)).status_code == 200
    client.post("/reviews/golden-x/items", headers=bearer(APPROVE), json={"decisions": [
        {"key": "k2", "action": "approve"}, {"key": "k3", "action": "reject", "reason": "obvious"}]})
    reviewed = client.get("/reviews/golden-x/export", headers=bearer(APPROVE)).json()
    assert merge(docs, reviewed) == {"reviewed": 1, "rejected": 1}
    doc = docs[Path("a.json")]
    assert [c["key"] for c in doc["cards"]] == ["k1", "k2"]
    assert "context" not in doc["cards"][1]
    assert [c["key"] for c in doc["rejected"]] == ["k3"]
    assert doc["status"] == "reviewed" and doc["reviewed_by"] == "aman"
