"""A lecture that fails to card is shown on /review with a retry, and mentioned once in Telegram (2026-10-20: PHYS 221
L06 and L07 failed for days with no trace but an ingest_job row and a Paperless tag)."""
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from mnemosyne import api as api_module
from mnemosyne.api import create_app
from mnemosyne.config import Settings
from mnemosyne.pipeline import problem, record_job
from test_service import APPROVE, PROPOSE, FakeCore, bearer

ROOT = Path(__file__).resolve().parent.parent
TITLES = {455: "MATH 309 Exam 1 Review", 506: "PHYS 221 L06", 610: "NUEN 201 L09", 620: "PHYS 221 L07"}


class Paperless:
    async def document(self, doc_id: int) -> SimpleNamespace:
        if doc_id not in TITLES:   # trashed by hand: Paperless says 404
            request = httpx.Request("GET", f"http://paperless/api/documents/{doc_id}/")
            raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
        return SimpleNamespace(title=TITLES[doc_id])

    def link(self, doc_id: int) -> str:
        return f"http://paperless/documents/{doc_id}/details"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    (tmp_path / "propose").write_text(PROPOSE)
    (tmp_path / "approve").write_text(APPROVE)
    settings = Settings(ledger=tmp_path / "helios.db", repo=ROOT, propose_token_file=tmp_path / "propose",
                        approve_token_file=tmp_path / "approve", public_url="http://mn:8110")
    c = TestClient(create_app(settings, core=FakeCore(), schedule=False))
    state: Any = c.app.state.mn  # type: ignore[attr-defined]
    state.pipeline = SimpleNamespace(paperless=Paperless())
    con = state.con
    record_job(con, 506, "error", 2, "no valid cards (pages 1-24 of 30): unparseable output after retry: Expecting ','")
    record_job(con, 455, "error", 2, "can't tell course and lecture from 'MATH 309 Exam 1 Review'")
    record_job(con, 490, "error", 2, "lecture via gateway: HTTP 524")          # a copy that 507 replaced
    con.execute("INSERT INTO doc_version (doc_id, replaces, at) VALUES (507, 490, '2026-10-21T01:31:39+00:00')")
    record_job(con, 503, "error", 2, "lecture via gateway: HTTP 524")          # gone from Paperless
    record_job(con, 610, "error", 0, "Rate Limit Reached")                     # waiting on the allowance
    record_job(con, 620, "error", 1, "lecture via gateway: HTTP 524")          # the next poll tries again
    return c


def test_plain_words() -> None:
    assert "cut off or garbled" in problem("no valid cards (pages 1-24 of 48): [{'problems': ['unparseable output")
    assert "timed out" in problem('lecture via protected.Claude: HTTP 524: <!DOCTYPE html>')
    assert "course and lecture" in problem("can't tell course and lecture from 'MATH 309 Exam 1 Review'")
    assert problem("HTTP 400 Rate Limit Reached").startswith("Waiting for the TAMU allowance")
    assert problem("something new\nwith a traceback") == "something new"


def test_the_review_queue_lists_unfinished_documents_that_still_exist(client: TestClient) -> None:
    docs = client.get("/review/queue", headers=bearer(APPROVE)).json()["documents"]
    by_id = {d["doc_id"]: d for d in docs}
    assert set(by_id) == {455, 506, 610, 620}, "replaced copies and documents gone from Paperless are left out"
    l06 = by_id[506]
    assert l06["title"] == "PHYS 221 L06" and l06["link"] == "http://paperless/documents/506/details"
    assert l06["gave_up"] and not l06["waiting_on_gateway"] and l06["job"] == "paperless" and l06["since"]
    assert "cut off or garbled" in l06["problem"]
    assert by_id[610]["waiting_on_gateway"] and not by_id[610]["gave_up"], "throttled reads as waiting, not failed"
    assert not by_id[620]["gave_up"], "one failed try: the poller tries once more"


def test_a_failure_is_announced_once(client: TestClient) -> None:
    def failures() -> list[dict[str, Any]]:
        out = client.get("/telegram/outbox", headers=bearer(APPROVE)).json()
        return [o for o in out if o["batch_id"].startswith("failed:")]

    first = failures()
    assert sorted(o["batch_id"] for o in first) == ["failed:455", "failed:506"], "only what the poller gave up on"
    l06 = next(o for o in first if o["batch_id"] == "failed:506")
    assert "PHYS 221 L06" in l06["text"] and "http://mn:8110/review#waiting" in l06["text"]
    for o in first:
        assert client.post("/telegram/sent", json={"batch_id": o["batch_id"], "message_ref": "1"},
                           headers=bearer(APPROVE)).json() == {"ok": True}
    assert failures() == [], "not repeated on the next poll"
    record_job(client.app.state.mn.con, 620, "error", 2, "HTTP 524")  # type: ignore[attr-defined]
    assert [o["batch_id"] for o in failures()] == ["failed:620"], "a new failure gets its own notice"


def test_try_again_runs_the_failed_job_and_says_how_it_went(client: TestClient,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, str | None]] = []

    async def process(svc: object, doc_id: int, job: str | None = None) -> None:
        calls.append((doc_id, job))
        if doc_id == 455:   # it worked this time
            record_job(client.app.state.mn.con, 455, "done", 1, kind="prep")  # type: ignore[attr-defined]

    monkeypatch.setattr(api_module, "process_document", process)
    again = client.post("/pipeline/document/506?job=paperless", headers=bearer(APPROVE)).json()
    assert again == {"doc_id": 506, "failed": problem("unparseable output"), "waiting_on_gateway": False}
    done = client.post("/pipeline/document/455?job=prep", headers=bearer(APPROVE)).json()
    assert done == {"doc_id": 455, "skipped": "already processed"}
    assert calls == [(506, "paperless"), (455, "prep")]
    assert 455 not in {d["doc_id"] for d in client.get("/review/queue", headers=bearer(APPROVE)).json()["documents"]}
    assert client.post("/pipeline/document/506", headers=bearer(PROPOSE)).status_code == 401, "retrying needs approve"
