"""The mnemosyne service: Ledger, inbox, approvals, Telegram handlers, and the write job (with a fake anki-core)."""
import asyncio
import copy
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mnemosyne.anki.core_client import AnkiCoreError, describe, unwrap
from mnemosyne.api import create_app
from mnemosyne.cards import CardIn, Payload, Schema, content_hash, note_tags, uuid7
from mnemosyne.config import Settings
from mnemosyne.inbox import propose
from mnemosyne.ledger import db, store
from mnemosyne.telegram import parse
from mnemosyne.write import write_approved

ROOT = Path(__file__).resolve().parent.parent
PROPOSE, APPROVE = "p-token", "a-token"


def card(front: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "note_type": "M-Basic", "deck": "Now", "mode": "audio",
        "fields": {"Front": front, "Back": r"\(E=mc^2\)", "SpeakFront": front, "SpeakBack": "E equals m c squared",
                   "Elaboration": "", "Source": "NUEN201 L06 slide 8", "SourceLink": ""},
        "tags": ["course::nuen201::l06-relativistic-energy", "topic::physics::relativity", "type::result", "src::slides"],
        "checks": [], "confidence": 0.9, "golden": {"ignored": True}}
    base.update(over)
    return base


def payload(*fronts: str) -> dict[str, Any]:
    return {"source": {"kind": "slides", "course": "NUEN201", "lecture": 6, "ref": "slides 5-8"},
            "cards": [card(f) for f in fronts]}


class FakeCore:
    """Behaves like anki-core's upsert: keyed on MnemosyneID, replays receipts for a repeated key."""

    def __init__(self) -> None:
        self.notes: dict[str, dict[str, Any]] = {}
        self.receipts: dict[str, dict[str, Any]] = {}
        self.fail = False
        self.calls = 0

    async def upsert(self, notes: list[dict[str, Any]], idempotency_key: str) -> dict[str, Any]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("anki-core unreachable")
        if idempotency_key in self.receipts:
            return self.receipts[idempotency_key]
        out: list[dict[str, Any]] = []
        counts = {"created": 0, "updated": 0, "unchanged": 0}
        for n in notes:
            key = n["fields"]["MnemosyneID"]
            action = "unchanged" if self.notes.get(key) == n else ("updated" if key in self.notes else "created")
            self.notes[key] = copy.deepcopy(n)
            counts[action] += 1
            out.append({"key": key, "note_id": 1000 + list(self.notes).index(key), "action": action})
        receipt = {"local_committed": True, "remote_synced": True, "result": {"notes": out, **counts}}
        self.receipts[idempotency_key] = receipt
        return receipt

    async def status(self) -> dict[str, Any]:
        return {"ok": True}

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(tool)


@pytest.fixture
def schema() -> Schema:
    return Schema.load(ROOT / "desktop" / "notetypes", ROOT / "config" / "decks.yaml")


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "helios.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def app_env(tmp_path: Path) -> tuple[TestClient, FakeCore]:
    (tmp_path / "propose").write_text(PROPOSE)
    (tmp_path / "approve").write_text(APPROVE + "\n")
    settings = Settings(ledger=tmp_path / "helios.db", repo=ROOT, propose_token_file=tmp_path / "propose",
                        approve_token_file=tmp_path / "approve")
    core = FakeCore()
    return TestClient(create_app(settings, core=core, schedule=False)), core


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------- Ledger ----------

def test_migrations_are_idempotent(tmp_path: Path) -> None:
    c = db.connect(tmp_path / "x.db")
    applied = db.migrate(c)
    assert applied[0] == "0001_init.sql" and applied == sorted(applied)
    assert db.migrate(c) == []
    tables = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"card_source", "card_batch", "card_draft", "course", "exam", "ingest_job"} <= tables


# ---------- cards ----------

def test_schema_accepts_good_cards_and_names_problems(schema: Schema) -> None:
    assert schema.problems(CardIn.model_validate(card("ok"))) == []
    bad = CardIn.model_validate(card("x", deck="Nowhere", tags=["Bad Tag"], mode="visual",
                                     fields={"Front": "$x$", "Back": "b", "SpeakFront": "x", "MnemosyneID": "no"}))
    problems = " | ".join(schema.problems(bad))
    for expected in ("pipeline-owned", "unknown deck", "SpeakFront must be filled exactly", "never $", "bad tags"):
        assert expected in problems


def test_golden_cards_all_pass_the_schema(schema: Schema) -> None:
    golden = ROOT / ".claude" / "skills" / "mnemosyne-cards" / "golden"
    cards = [c for p in golden.glob("*.json") for c in json.loads(p.read_text(encoding="utf-8"))["cards"]]
    assert cards
    for c in cards:
        assert schema.problems(CardIn.model_validate(c)) == [], c["fields"]


def test_note_fields_and_tags(schema: Schema) -> None:
    c = CardIn.model_validate(card("front"))
    mid = uuid7()
    fields = schema.note_fields(c, mid)
    assert list(fields) == schema.notetypes["M-Basic"]
    assert fields["MnemosyneID"] == mid and fields["Reverse"] == ""
    assert note_tags(c, probation=True)[-3:] == ["gen::ai", "mode::audio", "status::probation"]
    assert content_hash(c) == content_hash(CardIn.model_validate(card("front", tags=[], confidence=0.1)))


def test_uuid7_is_versioned_and_time_ordered() -> None:
    a, b = uuid7(), uuid7()
    assert a[14] == "7" and a[19] in "89ab"
    assert a[:13] <= b[:13]


# ---------- inbox ----------

def test_propose_is_idempotent(con: sqlite3.Connection, schema: Schema) -> None:
    first = propose(con, schema, Payload.model_validate(payload("a", "b")), "mcp")
    assert (first.status, first.created) == ("pending", 2)
    again = propose(con, schema, Payload.model_validate(payload("a", "b")), "mcp")
    assert again.already_proposed and again.batch_id == first.batch_id
    overlap = propose(con, schema, Payload.model_validate(payload("b", "c")), "telegram")
    assert (overlap.created, overlap.duplicates) == (1, 1)
    assert con.execute("SELECT COUNT(*) FROM card_draft").fetchone()[0] == 3


def test_invalid_cards_are_reported_not_dropped_silently(con: sqlite3.Connection, schema: Schema) -> None:
    body = payload("good")
    body["cards"].append(card("bad", deck="Nowhere"))
    result = propose(con, schema, Payload.model_validate(body), "mcp")
    assert result.created == 1
    assert result.invalid == [{"index": 1, "problems": ["unknown deck Nowhere"]}]


# ---------- write ----------

def test_write_is_idempotent_and_retries_failures(con: sqlite3.Connection, schema: Schema) -> None:
    batch = propose(con, schema, Payload.model_validate(payload(*[f"q{i}" for i in range(60)])), "mcp")
    assert batch.batch_id and store.decide(con, batch.batch_id, True, "test")
    core = FakeCore()
    core.fail = True
    assert asyncio.run(write_approved(con, core))["errors"] == 60
    assert len(store.approved_unwritten(con)) == 60
    core.fail = False
    first = asyncio.run(write_approved(con, core))
    assert first["created"] == 60 and core.calls == 4       # two failed chunks, then two chunks of <=50
    assert asyncio.run(write_approved(con, core)) == {"drafts": 0}
    got = store.get_batch(con, batch.batch_id)
    assert got is not None and got.status == "written"


# ---------- API ----------

def test_tokens_gate_each_level(app_env: tuple[TestClient, FakeCore]) -> None:
    client, _ = app_env
    body = {"payload": payload("a"), "origin": "mcp"}
    assert client.post("/inbox", json=body).status_code == 401
    assert client.post("/inbox", json=body, headers=bearer("wrong")).status_code == 401
    assert client.post("/inbox", json=body, headers=bearer(PROPOSE)).status_code == 200
    assert client.get("/batches", headers=bearer(PROPOSE)).status_code == 401, "proposing never grants approval"
    assert client.get("/batches", headers=bearer(APPROVE)).status_code == 200
    viewer = {"payload": payload("v"), "origin": "viewer"}
    assert client.post("/inbox", json=viewer, headers=bearer(PROPOSE)).status_code == 403
    assert client.get("/health").json()["ok"] is True


def test_propose_approve_write_via_telegram(app_env: tuple[TestClient, FakeCore]) -> None:
    client, core = app_env
    made = client.post("/inbox", json={"payload": payload("a", "b"), "origin": "mcp"}, headers=bearer(PROPOSE)).json()
    batch_id = made["batch_id"]
    outbox = client.get("/telegram/outbox", headers=bearer(APPROVE)).json()
    assert [o["batch_id"] for o in outbox] == [batch_id]
    assert "2 cards" in outbox[0]["text"] and "E equals m c squared" in outbox[0]["text"]
    approve_data = outbox[0]["keyboard"][0][0]["callback_data"]
    assert parse(approve_data) == ("a", batch_id)
    client.post("/telegram/sent", json={"batch_id": batch_id, "message_ref": "42"}, headers=bearer(APPROVE))
    assert client.get("/telegram/outbox", headers=bearer(APPROVE)).json() == []

    reply = client.post("/telegram/callback", json={"data": approve_data}, headers=bearer(APPROVE)).json()
    assert reply["toast"] == "Approved 2 cards" and reply["write"]
    assert len(core.notes) == 2, "approval triggers the write"
    assert all(n["deck"] == "Now" and "gen::ai" in n["tags"] for n in core.notes.values())
    again = client.post("/telegram/callback", json={"data": approve_data}, headers=bearer(APPROVE)).json()
    assert again["toast"] == "Already written"

    re_proposed = client.post("/inbox", json={"payload": payload("a", "b"), "origin": "mcp"},
                              headers=bearer(PROPOSE)).json()
    assert re_proposed["already_proposed"] and len(core.notes) == 2, "re-proposing does nothing"


def test_viewer_batches_arrive_approved(app_env: tuple[TestClient, FakeCore]) -> None:
    client, core = app_env
    made = client.post("/inbox", json={"payload": payload("v1"), "origin": "viewer"}, headers=bearer(APPROVE)).json()
    assert made["status"] == "approved"
    assert len(core.notes) == 1
    assert client.get("/telegram/outbox", headers=bearer(APPROVE)).json() == [], "nothing left to ask about"


class DupCore(FakeCore):
    """anki-core refuses the whole call when one new note's first field already exists in the collection."""

    def __init__(self, taken: set[str]) -> None:
        super().__init__()
        self.taken = taken

    async def upsert(self, notes: list[dict[str, Any]], idempotency_key: str) -> dict[str, Any]:
        if any(n["fields"]["Front"] in self.taken for n in notes):
            self.calls += 1
            raise RuntimeError('{"code":"DUPLICATE_NOTE","message":"a note with the same first field already exists"}')
        return await super().upsert(notes, idempotency_key)


def test_one_duplicate_does_not_hold_back_the_rest(con: sqlite3.Connection, schema: Schema) -> None:
    batch = propose(con, schema, Payload.model_validate(payload(*[f"q{i}" for i in range(60)])), "mcp")
    assert batch.batch_id and store.decide(con, batch.batch_id, True, "test")
    core = DupCore({"q7"})
    out = asyncio.run(write_approved(con, core))
    assert (out["created"], out["errors"]) == (59, 1)
    stuck = store.stuck(con)
    assert [d["fields"]["Front"] for d in stuck] == ["q7"] and "DUPLICATE_NOTE" in stuck[0]["error"]
    assert store.withdraw(con, stuck[0]["mnemosyne_id"], "dup of an old card", "test")
    assert store.stuck(con) == [] and asyncio.run(write_approved(con, core)) == {"drafts": 0}
    got = store.get_batch(con, batch.batch_id)
    assert got is not None and got.status == "written"


class GroupedDupCore(DupCore):
    """The real client's shape (2026-10-20): the refusal arrives wrapped in the MCP client's task group."""

    async def upsert(self, notes: list[dict[str, Any]], idempotency_key: str) -> dict[str, Any]:
        try:
            return await super().upsert(notes, idempotency_key)
        except RuntimeError as e:
            raise ExceptionGroup("unhandled errors in a TaskGroup", [e]) from None


def test_a_wrapped_duplicate_is_still_isolated(con: sqlite3.Connection, schema: Schema) -> None:
    batch = propose(con, schema, Payload.model_validate(payload(*[f"q{i}" for i in range(15)])), "mcp")
    assert batch.batch_id and store.decide(con, batch.batch_id, True, "test")
    out = asyncio.run(write_approved(con, GroupedDupCore({"q3"})))
    assert (out["created"], out["errors"]) == (14, 1)
    [stuck] = store.stuck(con)
    assert stuck["fields"]["Front"] == "q3" and "DUPLICATE_NOTE" in stuck["error"] and "TaskGroup" not in stuck["error"]


def test_unwrap_finds_the_error_inside_nested_groups() -> None:
    inner = AnkiCoreError("anki_notes_upsert: DUPLICATE_NOTE")
    assert unwrap(ExceptionGroup("outer", [ExceptionGroup("inner", [inner])])) is inner
    assert describe(ValueError("plain")) == "plain"


def test_connection_failures_are_not_split(con: sqlite3.Connection, schema: Schema) -> None:
    batch = propose(con, schema, Payload.model_validate(payload("a", "b", "c")), "mcp")
    assert batch.batch_id and store.decide(con, batch.batch_id, True, "test")
    core = FakeCore()
    core.fail = True
    assert asyncio.run(write_approved(con, core))["errors"] == 3 and core.calls == 1


def test_the_review_page_plays_a_card_s_recording_before_it_is_approved(app_env: tuple[TestClient, FakeCore]) -> None:
    client, _ = app_env
    q = {"note_type": "M-IPA", "key": '<span class="ipa">[ʈ]</span>'}
    assert client.get("/media/preview", params=q).status_code == 401
    got = client.get("/media/preview", params=q, headers=bearer(APPROVE))
    assert got.status_code == 200 and got.headers["content-type"] == "audio/mpeg" and got.content[:3] in (b"ID3", b"\xff\xfb")
    assert client.get("/media/preview", params={"note_type": "M-IPA", "key": "no such sound"},
                      headers=bearer(APPROVE)).status_code == 404
    assert client.get("/media/preview", params={"note_type": "M-Basic", "key": "x"}, headers=bearer(APPROVE)).status_code == 404
