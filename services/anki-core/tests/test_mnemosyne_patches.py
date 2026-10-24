"""The two mnemosyne patches: HTTP to listed internal sync hosts, and anki_notes_upsert."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from anki.collection import Collection

from anki_mcp.collection import AnkiCollectionService, DuplicateNoteError
from anki_mcp.config import validate_sync_endpoint

KEY = "MnemosyneID"


def test_http_sync_host_needs_explicit_allowlist() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        validate_sync_endpoint("http://anki-sync:8080/")
    assert validate_sync_endpoint("http://anki-sync:8080/", frozenset({"anki-sync"})) == (
        "http://anki-sync:8080/"
    )
    with pytest.raises(ValueError, match="HTTPS"):
        validate_sync_endpoint("http://evil.example/", frozenset({"anki-sync"}))


@pytest.fixture
def collection_path(tmp_path: Path) -> Iterator[str]:
    path = str(tmp_path / "collection.anki2")
    col = Collection(path)
    try:
        models = col.models
        model = models.new("M-Basic")
        for name in ("Front", "Back", KEY):
            models.add_field(model, models.new_field(name))
        template = models.new_template("Card 1")
        template["qfmt"] = "{{Front}}"
        template["afmt"] = "{{Back}}"
        models.add_template(model, template)
        models.add(model)
        col.decks.id("Now")
    finally:
        col.close()
    yield path


def note(key: str, front: str, back: str = "b", tags: list[str] | None = None) -> dict[str, object]:
    return {
        "deck": "Now",
        "note_type": "M-Basic",
        "fields": {"Front": front, "Back": back, KEY: key},
        "tags": tags or [],
    }


def field(got: dict[str, object], name: str) -> str:
    return next(f["value"] for f in got["fields"] if f["name"] == name)  # type: ignore[index,union-attr]


async def upsert(
    service: AnkiCollectionService, notes: list[dict[str, object]]
) -> dict[str, object]:
    return await service.executor.run(lambda adapter: adapter.upsert_notes(KEY, notes))


@pytest.mark.anyio
async def test_upsert_creates_updates_and_is_idempotent(collection_path: str) -> None:
    async with AnkiCollectionService(collection_path, max_page_size=100) as service:
        first = await upsert(service, [note("a-1", "one"), note("a-2", "two", tags=["x"])])
        assert (first["created"], first["updated"], first["unchanged"]) == (2, 0, 0)

        again = await upsert(service, [note("a-1", "one"), note("a-2", "two", tags=["x"])])
        assert (again["created"], again["updated"], again["unchanged"]) == (0, 0, 2)

        changed = await upsert(service, [note("a-1", "one", back="B", tags=["y"])])
        assert changed["updated"] == 1
        nid = changed["notes"][0]["note_id"]  # type: ignore[index]
        got = await service.get_note(nid)
        assert field(got, "Back") == "B"
        assert set(got["tags"]) == {"y"}


@pytest.mark.anyio
async def test_upsert_keeps_deck_and_extra_tags(collection_path: str) -> None:
    async with AnkiCollectionService(collection_path, max_page_size=100) as service:
        created = await upsert(service, [note("k-1", "front", tags=["course"])])
        nid = created["notes"][0]["note_id"]  # type: ignore[index]
        await service.add_note_tags([nid], ["marked"])
        await upsert(service, [note("k-1", "front edited", tags=["course"])])
        got = await service.get_note(nid)
        assert field(got, "Front") == "front edited"
        assert set(got["tags"]) == {"course", "marked"}


@pytest.mark.anyio
async def test_upsert_validates_before_writing(collection_path: str) -> None:
    async with AnkiCollectionService(collection_path, max_page_size=100) as service:
        bad_deck = {**note("v-2", "two"), "deck": "Missing"}
        with pytest.raises(LookupError):
            await upsert(service, [note("v-1", "one"), bad_deck])
        assert (await service.search_notes(f"{KEY}:v-1", offset=0, limit=10))["total"] == 0

        with pytest.raises(ValueError, match="repeats"):
            await upsert(service, [note("d-1", "one"), note("d-1", "two")])
        with pytest.raises(ValueError, match="plain"):
            await upsert(service, [note("bad key!", "x")])

        await upsert(service, [note("f-1", "same front")])
        with pytest.raises(DuplicateNoteError):
            await upsert(service, [note("f-2", "same front")])


async def filtered(service: AnkiCollectionService, method: str, *args: object) -> dict[str, object]:
    return await service.executor.run(lambda adapter: getattr(adapter, method)(*args))


@pytest.mark.anyio
async def test_filtered_decks_build_rebuild_and_return_cards_home(collection_path: str) -> None:
    async with AnkiCollectionService(collection_path, max_page_size=100) as service:
        with pytest.raises(ValueError, match="FSRS"):
            await filtered(service, "upsert_filtered_deck", "~Exam::X", "tag:x", 10, 8, True)
        await service.executor.run(lambda a: a.collection.set_config("fsrs", True))
        await upsert(service, [note("e-1", "one", tags=["course::phys207::l02-x"]),
                               note("e-2", "two", tags=["course::phys207::l03-y"]),
                               note("e-3", "three", tags=["course::math309::l01-z"])])
        search = "(tag:course::phys207::l02* OR tag:course::phys207::l03*) -is:suspended"
        deck = "~Exam::PHYS207-Exam-I"
        made = await filtered(service, "upsert_filtered_deck", deck, search, 9999, 8, True)
        assert made["created"] is True and made["cards"] == 2

        narrower = "tag:course::phys207::l02*"
        again = await filtered(service, "upsert_filtered_deck", deck, narrower, 9999, 8, True)
        assert (again["id"], again["created"], again["cards"]) == (made["id"], False, 1)
        assert (await filtered(service, "rebuild_filtered_deck", deck))["cards"] == 1

        gone = await filtered(service, "remove_filtered_deck", deck)
        assert gone == {"removed": True, "id": made["id"], "returned_cards": 1}
        home = await service.search_notes("deck:Now", offset=0, limit=10)
        assert home["total"] == 3, "every card is back in its home deck"
        assert (await filtered(service, "remove_filtered_deck", deck))["removed"] is False


@pytest.mark.anyio
async def test_filtered_deck_tools_never_touch_a_normal_deck(collection_path: str) -> None:
    async with AnkiCollectionService(collection_path, max_page_size=100) as service:
        with pytest.raises(ValueError, match="not a filtered deck"):
            await filtered(service, "upsert_filtered_deck", "Now", "tag:x", 10, 8, True)
        with pytest.raises(ValueError, match="not a filtered deck"):
            await filtered(service, "remove_filtered_deck", "Now")
        with pytest.raises(ValueError, match="order"):
            await filtered(service, "upsert_filtered_deck", "~Walk::Due", "is:due", 10, 10, True)
