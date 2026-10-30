import asyncio
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

from mnemosyne import neighbors
from mnemosyne.ledger import db
from test_tutor_and_leeches import Collection

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def con(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.connect(tmp_path / "h.db")
    db.migrate(c)
    yield c
    c.close()


def test_full_text_carries_the_answer_and_topics() -> None:
    fields = {"Front": r"Share of a normal distribution within \(\pm2\sigma\)", "Back": "95%",
              "SpeakFront": "spoken", "MnemosyneID": "x"}
    text = neighbors.note_text("M-Basic", fields, ["topic::math::statistics", "course::nuen201::l03-x", "mode::audio"])
    assert text == r"Share of a normal distribution within \pm2\sigma | 95% (topics: math statistics; NUEN201)"


def test_front_is_dedupes_text() -> None:
    assert neighbors.note_text("M-IPA", {"Symbol": "ʙ", "Description": "voiced bilabial trill"}, [], "front") == "ʙ"


def test_media_only_notes_use_what_describes_them() -> None:
    ipa = neighbors.note_text("M-IPA", {"Symbol": "ʙ", "SoundAudio": "[sound:mn_b.mp3]",
                                        "Description": "voiced bilabial trill", "ExampleWord": ""}, [])
    sign = neighbors.note_text("M-Sign", {"Video": '<video src="mn_math.mp4"></video>', "Gloss": "MATH"}, [])
    assert ipa == "voiced bilabial trill | ʙ"
    assert sign == "MATH"


def test_cloze_shows_its_answer() -> None:
    text = neighbors.note_text("M-Cloze", {"Text": "The {{c1::weak::force}} interaction violates parity"}, [])
    assert text == "The weak interaction violates parity"


def test_texwords() -> None:
    assert neighbors.texwords(r"\lambda_D = \sqrt{\frac{a}{b}}") == "lambda _D = sqrt frac a b"


def test_prompts_by_family() -> None:
    assert neighbors.prompt("nomic-embed-text:latest", "query") == "search_query: "
    assert neighbors.prompt("bge-m3", "doc") == ""
    assert neighbors.prompt("unknown", "same") == ""


# ---------- the index and the search ----------

VOCAB = ["debye", "length", "plasma", "parity", "weak", "gauss", "law", "flux", "normal", "sigma"]


async def fake_embed(url: str, model: str, texts: list[str], role: str) -> NDArray[np.float32]:
    """Bag of words over a tiny vocabulary: enough to know which card is nearest."""
    m = np.array([[t.lower().count(w) + 0.01 for w in VOCAB] for t in texts], dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


@pytest.fixture
def indexed(con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> Collection:
    monkeypatch.setattr(neighbors, "embed", fake_embed)
    core = Collection()
    core.add(1, "Debye length", r"\(\lambda_D\) in a plasma", tags=("topic::physics::plasma",))
    core.add(2, "The only interaction that violates parity", "the weak interaction")
    core.add(3, "Gauss's law", "flux through a closed surface")
    core.add(4, "Normal distribution within 2 sigma", "95%")
    return core


def test_refresh_embeds_only_what_changed(con: sqlite3.Connection, indexed: Collection) -> None:
    first = asyncio.run(neighbors.refresh(con, indexed, "http://ollama", "m"))
    assert first == {"created": 4, "updated": 0, "skipped": 0, "rejected": 0, "pruned": 0}
    assert asyncio.run(neighbors.refresh(con, indexed, "http://ollama", "m"))["skipped"] == 4
    indexed.anki_notes[1003]["fields"][1]["value"] = "electric flux through a closed surface"
    del indexed.anki_notes[1004]
    again = asyncio.run(neighbors.refresh(con, indexed, "http://ollama", "m"))
    assert (again["updated"], again["skipped"], again["pruned"]) == (1, 2, 1)


def test_search_by_text_and_by_card(con: sqlite3.Connection, indexed: Collection) -> None:
    asyncio.run(neighbors.refresh(con, indexed, "http://ollama", "m"))
    cfg = neighbors.Config(enabled=True, model="m")
    found = asyncio.run(neighbors.search(con, indexed, cfg, "http://ollama", text="screening length in a plasma", k=2))
    assert found["cards"][0]["card_id"] == 1 and found["shown"] == 2 and found["reranked"] is False
    assert "similarity" in found["cards"][0] and "retrievability" in found["cards"][0]
    near = asyncio.run(neighbors.search(con, indexed, cfg, "http://ollama", card_id=3, k=3))
    assert 3 not in [c["card_id"] for c in near["cards"]]


def test_empty_index_says_so(con: sqlite3.Connection) -> None:
    with pytest.raises(LookupError, match="empty"):
        asyncio.run(neighbors.search(con, Collection(), neighbors.Config(True, "m"), "http://ollama", text="x"))


def test_fuse() -> None:
    assert neighbors.fuse([[1, 2, 3], [3, 1]])[:2] == [1, 3]


def test_config_is_off_by_default(tmp_path: Path) -> None:
    (tmp_path / "llm.yaml").write_text("routes: {}\n")
    assert neighbors.Config.load(tmp_path / "llm.yaml").enabled is False
    assert neighbors.Config.load(ROOT / "config" / "llm.yaml").enabled is True, "on since 2026-10-29"


class Quiet(Collection):
    """Anki's `edited:n` search finds nothing: no note was edited since the last look."""

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool == "anki_notes_search" and arguments["query"].startswith("edited:"):
            return {"items": [], "has_more": False}
        return await super().call(tool, arguments)


def test_fetch_reads_only_new_notes_and_drops_deleted_ones(con: sqlite3.Connection) -> None:
    core = Quiet()
    core.add(1, "Debye length", "lambda_D")
    core.add(2, "Gauss's law", "flux")
    asyncio.run(neighbors.fetch(con, core))
    assert core.log.count("anki_notes_get") == 2
    core.add(3, "Bohm diffusion", "scales as 1/B")
    del core.anki_notes[1001]
    notes = asyncio.run(neighbors.fetch(con, core))
    assert core.log.count("anki_notes_get") == 3, "only the new note is read"
    assert sorted(notes) == [1002, 1003]


def test_a_sign_video_reads_as_video_not_audio() -> None:
    from mnemosyne.learner import plain
    assert plain("[sound:mn_asl_water.mp4]") == "[video]" and plain("[sound:mn_b.mp3]") == "[audio]"


def test_dedupe_cache_is_keyed_on_the_model(con: sqlite3.Connection) -> None:
    from mnemosyne.verify import dedupe

    class Model:
        def __init__(self, model: str) -> None:
            self.model, self.calls = model, 0

        async def embed(self, texts: list[str]) -> list[list[float]]:
            self.calls += 1
            return [[1.0, float(len(t))] for t in texts]

    notes = {1: "Debye length | lambda_D"}
    a, b = Model("nomic-embed-text"), Model("bge-m3")
    asyncio.run(dedupe.index(con, notes, a))
    asyncio.run(dedupe.index(con, notes, a))
    asyncio.run(dedupe.index(con, notes, b))
    assert (a.calls, b.calls) == (1, 1), "same model: cached; another model: embedded again"


def test_dedupe_model_comes_from_config() -> None:
    assert neighbors.Config.load(ROOT / "config" / "llm.yaml").dedupe_model == "nomic-embed-text"
