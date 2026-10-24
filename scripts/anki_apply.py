"""Apply committed docs/runs/ diffs to a collection, from Anki Desktop's debug console.

Tools → Debug Console (Ctrl+Shift+;). This runs inside Anki's own process, so the
collection file is never opened a second time (invariant 1):

    import runpy; m = runpy.run_path(r"<repo>/scripts/anki_apply.py")
    m["notetypes"](mw.col)                                    # dry run: what would be created
    m["notetypes"](mw.col, dry_run=False)
    m["step"](mw.col, r"<repo>/docs/runs/<date>-p1.json", "delete_notes")                  # dry run
    m["step"](mw.col, r"<repo>/docs/runs/<date>-p1.json", "delete_notes", dry_run=False)
    m["retag"](mw.col, r"<repo>/docs/runs/<date>-retag.json", dry_run=False)

A "legacy-audit" plan (docs/runs/<date>-legacy-audit.json) uses the same `step`, with its own steps:
set_fields, add_tags, remove_tags, suspend_cards, suspend_replaced.

Safety, for every step:
- A row is applied only if the collection still matches its `before` state. Rows already
  at their `after` state count as done, so re-running a step is a no-op. Anything else
  is drift: it's reported and left untouched.
- Each call is one undo step ("mnemosyne <step>") where Anki allows it. Preset/deck config
  and deck deletion are not undoable in Anki; the step says so, and the backup covers them.
- Stdlib + `anki` only, because it runs under Anki's bundled Python.
"""
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from anki.cards import CardId
from anki.collection import Collection
from anki.consts import MODEL_CLOZE
from anki.decks import DeckConfigId, DeckId
from anki.errors import InvalidInput, NotFoundError
from anki.notes import Note, NoteId

UNDO_LABEL = "mnemosyne retag"
NOTETYPES = Path(__file__).resolve().parent.parent / "desktop" / "notetypes"


def _load(plan_path: str | Path, *kinds: str) -> dict[str, Any]:
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    if plan.get("kind") not in kinds or plan.get("version") != 1:
        raise ValueError(f"{plan_path} is not a v1 {' or '.join(kinds)} plan")
    return plan


def _note(col: Collection, nid: int) -> Note | None:
    try:
        return col.get_note(NoteId(nid))
    except NotFoundError:
        return None


def _report(summary: dict[str, Any]) -> dict[str, Any]:
    print({k: (v if not isinstance(v, list) or len(v) <= 10 else f"{len(v)} items: {v[:10]}…") for k, v in summary.items()})
    return summary


# --- retag -------------------------------------------------------------------
def retag(col: Collection, plan_path: str | Path, dry_run: bool = False) -> dict[str, Any]:
    plan = _load(plan_path, "retag")
    todo: list[tuple[Note, list[str]]] = []
    already, drifted, missing = 0, [], []
    for row in plan["notes"]:
        note = _note(col, row["nid"])
        if note is None:
            missing.append(row["nid"])
            continue
        current = sorted(note.tags)
        if current == row["after"]:
            already += 1
        elif current == row["before"]:
            todo.append((note, row["after"]))
        else:
            drifted.append(row["nid"])
    summary: dict[str, Any] = {"to_update": len(todo), "already_applied": already,
                               "drifted": drifted, "missing": missing, "dry_run": dry_run}
    if dry_run or not todo:
        return _report(summary)

    undo = col.add_custom_undo_entry(UNDO_LABEL)
    # Anki re-cases a new tag to match an existing registered tag that differs only in
    # case (course:: would become Course::). So clear the old tags first, drop the
    # now-unused registry entries, then write the new tags.
    for note, _ in todo:
        note.tags = []
    col.update_notes([n for n, _ in todo])
    col.tags.clear_unused_tags()
    for note, after in todo:
        note.tags = after
    col.update_notes([n for n, _ in todo])
    col.merge_undo_entries(undo)

    wrong = [n.id for n, after in todo if sorted(col.get_note(n.id).tags) != after]
    summary["updated"] = len(todo) - len(wrong)
    summary["verify_failed"] = wrong
    return _report(summary)


# --- note types ----------------------------------------------------------------
def _text(path: Path) -> str:
    # LF regardless of how git checked the file out, so comparisons stay stable on Windows.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _spec(path: Path) -> dict[str, Any]:
    spec = json.loads(path.read_text(encoding="utf-8"))
    spec["css_text"] = _text(NOTETYPES / spec["css"])
    for t in spec["templates"]:
        t["qfmt"] = _text(NOTETYPES / t["front"])
        t["afmt"] = _text(NOTETYPES / t["back"])
    return spec


def _differences(existing: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    diffs = []
    if [f["name"] for f in existing["flds"]] != spec["fields"]:
        diffs.append("fields")
    if (existing["type"] == MODEL_CLOZE) != (spec["kind"] == "cloze"):
        diffs.append("kind")
    if existing["css"] != spec["css_text"]:
        diffs.append("css")
    have = [(t["name"], t["qfmt"], t["afmt"]) for t in existing["tmpls"]]
    if have != [(t["name"], t["qfmt"], t["afmt"]) for t in spec["templates"]]:
        diffs.append("templates")
    return diffs


def notetypes(col: Collection, dry_run: bool = True, spec_dir: Path = NOTETYPES) -> dict[str, Any]:
    """Create missing M-* note types from desktop/notetypes/. Never modifies an existing one:
    a type whose definition differs is only reported. Changing a type is a separate, deliberate step."""
    created, unchanged, differs = [], [], {}
    specs = [_spec(p) for p in sorted(spec_dir.glob("*.json"))]
    todo = []
    for spec in specs:
        existing = col.models.by_name(spec["name"])
        if existing is None:
            todo.append(spec)
        elif d := _differences(existing, spec):
            differs[spec["name"]] = d
        else:
            unchanged.append(spec["name"])
    if not dry_run and todo:
        undo = col.add_custom_undo_entry("mnemosyne notetypes")
        mm = col.models
        for spec in todo:
            m = mm.new(spec["name"])
            if spec["kind"] == "cloze":
                m["type"] = MODEL_CLOZE
            for name in spec["fields"]:
                mm.add_field(m, mm.new_field(name))
            for t in spec["templates"]:
                tmpl = mm.new_template(t["name"])
                tmpl["qfmt"], tmpl["afmt"] = t["qfmt"], t["afmt"]
                mm.add_template(m, tmpl)
            m["css"] = spec["css_text"]
            mm.add(m)
            created.append(spec["name"])
        col.merge_undo_entries(undo)
    return _report({"to_create": [s["name"] for s in todo], "created": created, "unchanged": unchanged,
                    "differs": differs, "dry_run": dry_run})


# --- P1 migration steps ----------------------------------------------------------
def step(col: Collection, plan_path: str | Path, name: str, dry_run: bool = True) -> dict[str, Any]:
    plan = _load(plan_path, "p1-migration", "legacy-audit")
    if name not in plan["steps"]:
        raise ValueError(f"unknown step {name!r}; steps in order: {plan['steps_in_order']}")
    fn = {"setup": _setup, "delete_notes": _delete_notes, "change_notetype": _change_notetype,
          "move_cards": _move_cards, "suspend_cards": _suspend_cards, "suspend_replaced": _suspend_cards,
          "reposition_new": _reposition_new, "set_fields": _set_fields, "add_tags": _add_tags,
          "remove_tags": _remove_tags, "delete_decks": _delete_decks}[name]
    undo = None if dry_run else col.add_custom_undo_entry(f"mnemosyne {name}")
    summary = fn(col, plan["steps"][name], dry_run)
    if undo is not None:
        # Some operations (preset/deck config, note type changes) aren't undoable in Anki;
        # say so instead of pretending. The backup taken at the start covers those.
        try:
            col.merge_undo_entries(undo)
            summary["undo"] = f"Edit → Undo 'mnemosyne {name}'"
        except InvalidInput:
            summary["undo"] = "not undoable in Anki; restore the backup to reverse"
    return _report({"step": name, **summary, "dry_run": dry_run})


def _setup(col: Collection, rows: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
    out: dict[str, list[str]] = defaultdict(list)
    confs = {c["name"]: c for c in col.decks.all_config()}
    for row in (r for r in rows if "preset" in r and "deck" not in r):
        want = {"desiredRetention": row["desired_retention"], "new": row["new_per_day"], "rev": row["max_reviews_per_day"]}
        conf = confs.get(row["preset"])
        if conf is None:
            out["presets_created"].append(row["preset"])
            if not dry_run:
                cid = col.decks.add_config_returning_id(row["preset"], clone_from=col.decks.get_config(DeckConfigId(1)))
                conf = col.decks.get_config(cid)
                assert conf is not None
                conf["desiredRetention"], conf["new"]["perDay"], conf["rev"]["perDay"] = want.values()
                col.decks.update_config(conf)
                confs[row["preset"]] = conf
        elif (conf["desiredRetention"], conf["new"]["perDay"], conf["rev"]["perDay"]) != tuple(want.values()):
            out["presets_differ"].append(row["preset"])  # preset edits are invariant 9: report only
        else:
            out["presets_ok"].append(row["preset"])
    for row in (r for r in rows if "deck" in r):
        did = col.decks.id_for_name(row["deck"])
        conf = confs.get(row["preset"])
        if did is None:
            out["decks_created"].append(row["deck"])
            if not dry_run:
                did = col.decks.id(row["deck"])
        if did is None or conf is None:
            continue
        deck = col.decks.get(did)
        assert deck is not None
        if deck.get("conf") == conf["id"]:
            out["decks_ok"].append(row["deck"])
        elif deck.get("conf") in (1, None):
            out["decks_assigned"].append(row["deck"])
            if not dry_run:
                deck["conf"] = conf["id"]
                col.decks.save(deck)
        else:
            out["decks_differ"].append(row["deck"])
    return dict(out)


def _delete_notes(col: Collection, rows: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
    todo, drifted, already = [], [], 0
    for row in rows:
        note = _note(col, row["nid"])
        if note is None:
            already += 1
        elif note.fields[0] == row["front"]:
            todo.append(note.id)
        else:
            drifted.append(row["nid"])
    if not dry_run and todo:
        col.remove_notes(todo)
    return {"to_delete": len(todo), "already": already, "drifted": drifted}


def _change_notetype(col: Collection, rows: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in rows:
        old, new = col.models.by_name(row["from"]), col.models.by_name(row["to"])
        if new is None:
            raise RuntimeError(f"note type {row['to']!r} missing; run notetypes() first")
        todo, already, drifted = [], 0, []
        for nid in row["nids"]:
            note = _note(col, nid)
            if note is None:
                drifted.append(nid)
            elif note.mid == new["id"]:
                already += 1
            elif old is not None and note.mid == old["id"]:
                todo.append(note.id)
            else:
                drifted.append(nid)
        out[row["group"]] = {"to_change": len(todo), "already": already, "drifted": drifted}
        if dry_run or not todo or old is None:
            continue
        req = col.models.change_notetype_info(old_notetype_id=old["id"], new_notetype_id=new["id"]).input
        req.note_ids.extend(todo)
        old_f, fmap = [f["name"] for f in old["flds"]], {v: k for k, v in row["fields"].items()}
        del req.new_fields[:]
        req.new_fields.extend(old_f.index(fmap[f["name"]]) if f["name"] in fmap else -1 for f in new["flds"])
        if new["type"] != MODEL_CLOZE:
            old_t, tmap = [t["name"] for t in old["tmpls"]], {v: k for k, v in row["templates"].items()}
            del req.new_templates[:]
            req.new_templates.extend(old_t.index(tmap[t["name"]]) if t["name"] in tmap else -1 for t in new["tmpls"])
        col.models.change_notetype_of_notes(req)
    return out


def _move_cards(col: Collection, rows: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
    by_target: dict[str, list[int]] = defaultdict(list)
    already, drifted, missing = 0, [], []
    names = {d.id: d.name for d in col.decks.all_names_and_ids()}
    for row in rows:
        try:
            card = col.get_card(row["cid"])
        except NotFoundError:
            missing.append(row["cid"])
            continue
        current = names.get(card.did)
        if current == row["to"]:
            already += 1
        elif current == row["from"]:
            by_target[row["to"]].append(card.id)
        else:
            drifted.append(row["cid"])
    if not dry_run:
        for deck, cids in by_target.items():
            did = col.decks.id_for_name(deck)
            if did is None:
                raise RuntimeError(f"deck {deck!r} missing; run the setup step first")
            col.set_deck([CardId(c) for c in cids], DeckId(did))
    return {"to_move": sum(map(len, by_target.values())), "already": already, "drifted": drifted, "missing": missing}


def _suspend_cards(col: Collection, rows: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
    todo, already, missing = [], 0, []
    for row in rows:
        try:
            card = col.get_card(row["cid"])
        except NotFoundError:
            missing.append(row["cid"])
            continue
        if card.queue == -1:
            already += 1
        else:
            todo.append(card.id)
    if not dry_run and todo:
        col.sched.suspend_cards(todo)
    return {"to_suspend": len(todo), "already": already, "missing": missing}


def _reposition_new(col: Collection, rows: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
    cids, skipped = [], []
    for row in rows:
        try:
            card = col.get_card(row["cid"])
        except NotFoundError:
            skipped.append(row["cid"])
            continue
        (cids if card.type == 0 and card.queue == 0 else skipped).append(card.id)
    dues = [col.get_card(c).due for c in cids]
    assert col.db is not None
    lowest = col.db.scalar("select min(due) from cards where type = 0 and queue = 0")
    done = bool(cids) and dues == list(range(lowest, lowest + len(cids)))
    if not dry_run and cids and not done:
        col.sched.reposition_new_cards(card_ids=cids, starting_from=0, step_size=1, randomize=False,
                                       shift_existing=True)
    return {"to_reposition": 0 if done else len(cids), "already": done, "skipped_not_new": skipped}


def _set_fields(col: Collection, rows: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
    notes: dict[int, Note] = {}
    already, drifted, missing, no_field = 0, [], [], []
    for row in rows:
        note = notes.get(row["nid"]) or _note(col, row["nid"])
        if note is None:
            missing.append(row["nid"])
            continue
        if row["field"] not in note:
            no_field.append(row["nid"])
            continue
        if note[row["field"]] == row["after"]:
            already += 1
        elif note[row["field"]] == row["before"]:
            note[row["field"]] = row["after"]
            notes[note.id] = note
        else:
            drifted.append((row["nid"], row["field"]))
    if not dry_run and notes:
        col.update_notes(list(notes.values()))
    return {"to_update_notes": len(notes), "already": already, "drifted": drifted, "missing": missing,
            "field_missing": sorted(set(no_field))}


def _add_tags(col: Collection, rows: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
    todo, already, missing = [], 0, []
    for row in rows:
        note = _note(col, row["nid"])
        if note is None:
            missing.append(row["nid"])
            continue
        add = [t for t in row["add"] if not note.has_tag(t)]
        if not add:
            already += 1
            continue
        note.tags += add
        todo.append(note)
    if not dry_run and todo:
        col.update_notes(todo)
    return {"to_tag": len(todo), "already": already, "missing": missing}


def _remove_tags(col: Collection, rows: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
    todo, already, missing = [], 0, []
    for row in rows:
        note = _note(col, row["nid"])
        if note is None:
            missing.append(row["nid"])
            continue
        gone = [t for t in row["remove"] if note.has_tag(t)]
        if not gone:
            already += 1
            continue
        for t in gone:
            note.remove_tag(t)
        todo.append(note)
    if not dry_run and todo:
        col.update_notes(todo)
        col.tags.clear_unused_tags()
    return {"to_untag": len(todo), "already": already, "missing": missing}


def _delete_decks(col: Collection, rows: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
    todo, already, non_empty, filtered = [], 0, [], []
    for row in rows:
        did = col.decks.id_for_name(row["deck"])
        if did is None:
            already += 1
            continue
        deck = col.decks.get(did)
        if deck and deck.get("dyn"):
            filtered.append(row["deck"])
        elif col.decks.card_count(did, include_subdecks=True):
            non_empty.append(row["deck"])
        else:
            todo.append(did)
    if not dry_run:
        for did in todo:  # deepest first, so parents are already childless
            if col.decks.get(did, default=False):
                col.decks.remove([did])
    return {"to_delete": len(todo), "already": already, "non_empty_refused": non_empty, "filtered_skipped": filtered}
