"""Chat and Telegram proposals (plain /inbox): held for Aman with what the verify gate and dedupe found, and an
honest reason when the checks couldn't run."""
import asyncio
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mnemosyne.api import create_app
from mnemosyne.cards import Payload, Schema
from mnemosyne.config import Settings
from mnemosyne.inbox import UNCHECKED, Verdict, propose
from mnemosyne.ledger import db, store
from mnemosyne.pipeline import Services, propose_checked
from mnemosyne.telegram import announcement
from test_pipeline import FakeCore, ScriptedLLM, card, estimate, services
from test_service import APPROVE, PROPOSE, bearer
from test_service import FakeCore as ApiCore

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "helios.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def schema() -> Schema:
    return Schema.load(ROOT / "desktop" / "notetypes", ROOT / "config" / "decks.yaml")


def payload(*cards: dict[str, Any]) -> Payload:
    return Payload.model_validate({"source": {"kind": "chat", "course": "NUEN201", "lecture": 8}, "cards": list(cards)})


def svc(con: sqlite3.Connection, schema: Schema, existing: dict[int, str]) -> Services:
    return services(con, schema, ScriptedLLM(), existing)


def test_a_chat_proposal_is_held_with_what_the_checks_found(con: sqlite3.Connection, schema: Schema) -> None:
    cards = payload(card("Where does binding energy per nucleon peak?"), card("Mass defect definition, in words"),
                    estimate("Energy of 1 u in MeV", "939.6 MeV"), estimate("Energy of 1 u, right", "931.494 MeV"))
    got = asyncio.run(propose_checked(svc(con, schema, {77: "mass defect definition, in words"}), cards, "mcp"))

    assert got["created"] == 4 and got["held"] == 4 and got["status"] == "pending" and got["invalid"] == []
    assert got["reasons"] == {"dedupe_duplicate": 1, "verify_failed": 1}
    drafts = {d.fields.get("Front") or d.fields.get("Question"): d for d in store.batch_drafts(con, str(got["batch_id"]))}
    assert all(d.status == "pending" for d in drafts.values()), "nothing is approved: the propose token is enough"
    assert (drafts["Mass defect definition, in words"].review_reason or "").startswith("dedupe_duplicate")
    assert (drafts["Energy of 1 u in MeV"].review_reason or "").startswith("verify_failed")
    assert drafts["Energy of 1 u, right"].review_reason is None
    assert not any("P3" in (d.review_reason or "") for d in drafts.values())

    again = asyncio.run(propose_checked(svc(con, schema, {}), cards, "mcp"))
    assert again["already_proposed"] and again["batch_id"] == got["batch_id"]


class DownCore(FakeCore):
    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise OSError("connection refused")


def test_cards_are_kept_when_the_checks_cannot_run(con: sqlite3.Connection, schema: Schema) -> None:
    s = svc(con, schema, {})
    s.core = DownCore({})
    got = asyncio.run(propose_checked(s, payload(card("a"), card("b")), "telegram"))
    assert got["created"] == 2 and got["status"] == "pending"
    reasons = {d.review_reason for d in store.batch_drafts(con, str(got["batch_id"]))}
    assert len(reasons) == 1
    reason = reasons.pop() or ""
    assert reason.startswith("unchecked: the checks couldn't run (anki-core: connection refused")


def test_without_a_pipeline_the_reason_says_so(tmp_path: Path) -> None:
    (tmp_path / "propose").write_text(PROPOSE)
    (tmp_path / "approve").write_text(APPROVE)
    settings = Settings(ledger=tmp_path / "helios.db", repo=ROOT, propose_token_file=tmp_path / "propose",
                        approve_token_file=tmp_path / "approve")
    client = TestClient(create_app(settings, core=ApiCore(), schedule=False))
    body = {"source": {"kind": "chat"}, "cards": [card("a"), card("b")]}
    made = client.post("/inbox", json={"payload": body, "origin": "mcp"}, headers=bearer(PROPOSE)).json()
    assert made["status"] == "pending" and made["created"] == 2
    text = client.get("/telegram/outbox", headers=bearer(APPROVE)).json()[0]["text"]
    assert "⚠ Not checked: this server has no pipeline" in text and text.count("Not checked") == 1, "said once"


def test_old_placeholder_reasons_are_rewritten(con: sqlite3.Connection, schema: Schema) -> None:
    old = "verify_skipped: numeric and dedupe checks arrive in P3"
    made = propose(con, schema, payload(card("a")), "mcp", lambda _i, _c: Verdict(approved=False, reason=old))
    sql = (ROOT / "src" / "mnemosyne" / "ledger" / "migrations" / "0023_unchecked_reason.sql").read_text(encoding="utf-8")
    con.executescript(sql)
    [draft] = store.batch_drafts(con, str(made.batch_id))
    assert (draft.review_reason or "").startswith("unchecked: proposed before chat and Telegram cards")
    assert UNCHECKED.startswith("unchecked: ")


def test_announcement_shows_real_reasons(con: sqlite3.Connection, schema: Schema) -> None:
    got = asyncio.run(propose_checked(svc(con, schema, {}), payload(estimate("Energy of 1 u in MeV", "939.6 MeV")),
                                      "telegram"))
    batch = store.get_batch(con, str(got["batch_id"]))
    assert batch is not None
    text = announcement(batch, store.batch_drafts(con, batch.id))
    assert "↳ verify_failed" in text and "Not checked" not in text
