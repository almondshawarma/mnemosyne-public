"""P2 integration test against the TEST sync user only (runs in the `integration` image on the Obelisk).

    python scripts/integration_p2.py seed    # build a test collection (M-* types, decks) and full-upload it
    python scripts/integration_p2.py run     # propose → approve → write via anki-core-test → verify from a fresh client

`seed` is the only full upload anywhere in mnemosyne, and it refuses any account whose name lacks "test".
The real collection is never touched: anki-core-test has its own volume and logs in as the test user.
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import yaml
from anki.collection import Collection

from mnemosyne.anki.core_client import CoreClient
from mnemosyne.cards import Payload, Schema
from mnemosyne.inbox import propose
from mnemosyne.ledger import db, store
from mnemosyne.write import write_approved

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP / "scripts"))
from anki_apply import notetypes

ENDPOINT = os.environ.get("SYNC_ENDPOINT", "http://anki-sync:8080/")
USER = os.environ["TEST_SYNC_USER"]
PASSWORD = Path(os.environ["TEST_SYNC_PASSWORD_FILE"]).read_text(encoding="utf-8").strip()
FULL_SYNC = {2, 3, 4}


def guard() -> None:
    if "test" not in USER.lower():
        sys.exit(f"refusing: {USER!r} is not a test account")


def fresh(path: Path) -> Collection:
    """A brand-new client that pulls the server's collection."""
    col = Collection(str(path))
    auth = col.sync_login(USER, PASSWORD, ENDPOINT)
    out = col.sync_collection(auth, sync_media=False)
    if out.required in FULL_SYNC:
        col.close_for_full_sync()
        col.full_upload_or_download(auth=auth, server_usn=None, upload=False)
        col.reopen(after_full_sync=True)
    return col


def seed() -> None:
    guard()
    with tempfile.TemporaryDirectory() as tmp:
        col = Collection(str(Path(tmp) / "seed.anki2"))
        try:
            print(json.dumps(notetypes(col, dry_run=False)))
            for deck in yaml.safe_load((APP / "config" / "decks.yaml").read_text(encoding="utf-8"))["decks"]:
                col.decks.id(deck)
            auth = col.sync_login(USER, PASSWORD, ENDPOINT)
            out = col.sync_collection(auth, sync_media=False)
            if out.required in FULL_SYNC:
                col.close_for_full_sync()
                col.full_upload_or_download(auth=auth, server_usn=None, upload=True)
                print("seeded: full upload to the test account")
            else:
                print(f"seed: server answered {out.required}; already seeded")
        finally:
            col.close()


def payload(run: str) -> Payload:
    tag = "course::nuen201::l06-relativistic-energy"
    cards = [{"note_type": "M-Basic", "deck": "Now", "mode": "audio",
              "fields": {"Front": f"P2 integration {run}: card {i}", "Back": f"answer {i}",
                         "SpeakFront": f"integration card {i}", "SpeakBack": f"answer {i}", "Source": "integration"},
              "tags": [tag, "topic::test", "type::result", "src::chat"]} for i in (1, 2)]
    return Payload.model_validate({"source": {"kind": "chat", "ref": f"integration {run}"}, "cards": cards})


async def run() -> None:
    guard()
    core = CoreClient(os.environ["ANKI_CORE_URL"], Path(os.environ["ANKI_CORE_TOKEN_FILE"]).read_text().strip())
    print("anki-core status:", json.dumps(await core.status())[:300])
    with tempfile.TemporaryDirectory() as tmp:
        con = db.connect(Path(tmp) / "ledger.db")
        db.migrate(con)
        schema = Schema.load(APP / "desktop" / "notetypes", APP / "config" / "decks.yaml")
        run_id = os.urandom(3).hex()
        proposal = propose(con, schema, payload(run_id), "integration")
        assert proposal.batch_id and proposal.created == 2, proposal
        assert propose(con, schema, payload(run_id), "integration").already_proposed
        assert store.decide(con, proposal.batch_id, True, "integration")
        first = await write_approved(con, core)
        assert first.get("created") == 2 and not first.get("errors"), first
        assert await write_approved(con, core) == {"drafts": 0}
        ids = [d.mnemosyne_id for d in store.batch_drafts(con, proposal.batch_id)]

        client = fresh(Path(tmp) / "verify.anki2")
        try:
            for mid in ids:
                found = client.find_notes(f"MnemosyneID:{mid}")
                assert len(found) == 1, (mid, found)
                note = client.get_note(found[0])
                assert {"gen::ai", "mode::audio"} <= set(note.tags), note.tags
                assert client.decks.name(client.get_card(note.card_ids()[0]).did) == "Now"
        finally:
            client.close()
    print(f"P2 integration passed: run {run_id}, 2 notes written once and visible to a fresh client")


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["seed"]:
            seed()
        case ["run"]:
            asyncio.run(run())
        case _:
            sys.exit(__doc__)
