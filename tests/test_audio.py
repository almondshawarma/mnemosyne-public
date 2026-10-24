"""Audio the pipeline owns: IPA recordings attached to M-IPA cards as they're written, each file stored once."""
import asyncio
import json
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from mnemosyne import audio
from mnemosyne.cards import Payload, Schema
from mnemosyne.inbox import propose
from mnemosyne.ledger import db, store
from mnemosyne.write import write_approved
from test_service import FakeCore

ROOT = Path(__file__).resolve().parent.parent


class MediaCore(FakeCore):
    def __init__(self, fail_media: bool = False) -> None:
        super().__init__()
        self.media: dict[str, str] = {}
        self.fail_media = fail_media

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert tool == "anki_media_store" and arguments["filename"].startswith("mn_")
        if self.fail_media:
            raise RuntimeError("anki-core unreachable")
        self.media[arguments["filename"]] = arguments["content_base64"]
        return {"state": "committed"}


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "helios.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    folder = tmp_path / "repo" / "data" / "audio" / "ipa"
    folder.mkdir(parents=True)
    (folder / "mn_ipa_voiceless-retroflex-plosive.mp3").write_bytes(b"ID3 fake mp3")
    (folder / "manifest.json").write_text(json.dumps({"ʈ": {
        "file": "mn_ipa_voiceless-retroflex-plosive.mp3", "source": "File:Voiceless retroflex stop.oga",
        "author": "Peter Isotalo", "license": "CC BY-SA 3.0",
        "page": "https://commons.wikimedia.org/wiki/File:Voiceless_retroflex_stop.oga",
        "url": "https://upload.wikimedia.org/x.oga"}}, ensure_ascii=False), encoding="utf-8")
    return tmp_path / "repo"


def ipa(symbol: str) -> dict[str, Any]:
    return {"note_type": "M-IPA", "deck": "Lang::IPA", "mode": "audio",
            "fields": {"Symbol": symbol, "Description": "a sound", "ExampleWord": ""},
            "tags": ["type::definition", "src::chat", "ipa::place::retroflex"]}


def approved(con: sqlite3.Connection, schema: Schema, *cards: dict[str, Any]) -> None:
    out = propose(con, schema, Payload.model_validate({"source": {"kind": "chat"}, "cards": list(cards)}), "mcp")
    assert out.batch_id and store.decide(con, out.batch_id, True, "test")


def schema() -> Schema:
    return Schema.load(ROOT / "desktop" / "notetypes", ROOT / "config" / "decks.yaml")


def test_an_ipa_card_is_written_with_its_recording(con: sqlite3.Connection, repo: Path) -> None:
    approved(con, schema(), ipa("<b>ʈ</b>"), ipa("ʘ"))
    core = MediaCore()
    out = asyncio.run(write_approved(con, core, [repo / "data"]))
    assert out["created"] == 2
    notes = {n["fields"]["Symbol"]: n for n in core.notes.values()}
    assert notes["<b>ʈ</b>"]["fields"]["SoundAudio"] == "[sound:mn_ipa_voiceless-retroflex-plosive.mp3]"
    assert notes["ʘ"]["fields"]["SoundAudio"] == "", "no recording: written without one"
    assert list(core.media) == ["mn_ipa_voiceless-retroflex-plosive.mp3"]
    approved(con, schema(), {**ipa("ʈ"), "fields": {**ipa("ʈ")["fields"], "Description": "again"}})
    core.media.clear()
    asyncio.run(write_approved(con, core, [repo / "data"]))
    assert core.media == {}, "the same file isn't stored twice"


def test_a_sign_card_gets_its_video_from_the_media_folder(con: sqlite3.Connection, tmp_path: Path) -> None:
    media = tmp_path / "media" / "video" / "asl"
    media.mkdir(parents=True)
    (media / "mn_asl_thank-you-1198.mp4").write_bytes(b"fake mp4")
    (media / "manifest.json").write_text(json.dumps({"thank you": {
        "file": "mn_asl_thank-you-1198.mp4", "source": "ASL Signbank", "author": "H, C & L-M", "license": "CC BY-NC-SA 4.0",
        "page": "https://aslsignbank.com/dictionary/gloss/1198.html"}}), encoding="utf-8")
    sign = {"note_type": "M-Sign", "deck": "Lang::ASL", "mode": "visual",
            "fields": {"Gloss": "Thank you", "Notes": "", "Source": audio.source({"source": "ASL Signbank", "author": "x",
                                                                                     "license": "CC BY-NC-SA 4.0"})},
            "tags": ["type::definition", "src::chat", "lang::asl"]}
    approved(con, schema(), sign)
    core = MediaCore()
    asyncio.run(write_approved(con, core, [tmp_path / "media", tmp_path / "repo-data"]))
    note = next(iter(core.notes.values()))
    assert note["fields"]["Video"] == "[sound:mn_asl_thank-you-1198.mp4]", "matched by meaning, case and spaces aside"
    assert note["fields"]["Source"].startswith("Video: ASL Signbank")


def test_cards_needing_audio_wait_when_media_fails(con: sqlite3.Connection, repo: Path) -> None:
    approved(con, schema(), ipa("ʈ"), {**ipa("x"), "note_type": "M-Basic",
                                       "fields": {"Front": "f", "Back": "b", "SpeakFront": "f", "SpeakBack": "b"},
                                       "deck": "Core"})
    out = asyncio.run(write_approved(con, MediaCore(fail_media=True), [repo / "data"]))
    assert out["created"] == 1
    assert [d.note_type for d in store.approved_unwritten(con)] == ["M-IPA"], "retried on the next write"


def test_the_ipa_payload_is_valid_and_ordered_for_the_languages_ahead() -> None:
    raw = subprocess.run([sys.executable, str(ROOT / "scripts" / "ipa_cards.py")], capture_output=True, check=True)
    cards = json.loads(raw.stdout)["cards"]
    data = yaml.safe_load((ROOT / "data" / "ipa.yaml").read_text(encoding="utf-8"))
    assert len(cards) == len(data["consonants"]) + len(data["vowels"])
    s = schema()
    assert all(not s.problems(Payload.model_validate({"source": {"kind": "chat"}, "cards": [c]}).cards[0])
               for c in cards)
    symbols = [c["fields"]["Symbol"] for c in cards]
    assert symbols.index("ʈ") < symbols.index("ɢ") and symbols.index("ʃ") < symbols.index("ʟ")
    assert len(set(symbols)) == len(symbols)
    assert all(c["fields"]["Symbol"] == audio.key_of(c["fields"]["Symbol"]) for c in cards), "symbols match the manifest"
