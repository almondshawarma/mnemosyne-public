"""Lecture → card payload. The prompt imports the canon from SKILL.md; it never restates card rules here.

Invalid output is retried once with the problems listed; whatever is still invalid is returned so the
caller can send it to review rather than dropping it (CLAUDE.md, LLM usage).
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mnemosyne.cards import CardIn, Payload, Schema, Source
from mnemosyne.extract import Page, parse_title, slug
from mnemosyne.generate.llm import LLM, Content, image, text
from mnemosyne.verify.quantities import verify_card

FENCE = re.compile(r"```(?:mnemosyne-cards|json)?\s*(\{.*\})\s*```", re.DOTALL)


@dataclass
class Lecture:
    course: str                 # NUEN201
    lecture: int
    title: str
    kind: str                   # slides | notes | graded-homework | ...
    ref: str                    # "NUEN 201 L07 - Quantum Theory of Light"
    link: str | None
    emphasis: list[str]         # Paperless notes and other priority signals
    existing: list[str]         # prompts of cards this lecture already has
    pages: list[Page]
    partial: bool = False       # another copy of this deck was carded already; only new or newly marked pages are here
    part: str | None = None     # "pages 25-48 of 55": a long deck carded in parts, each under the gateway's 100k TPM
    # A reading (a textbook section) instead of a lecture: its src::book:: tag, what the model is told about it, and
    # the ebook's clean text when the pages are a scan of Aman's copy.
    required_tag: str | None = None
    about: str | None = None
    reference: str | None = None

    @property
    def tag(self) -> str:
        return self.required_tag or f"course::{self.course.lower()}::l{self.lecture:02d}-{slug(self.title)}"


@dataclass
class Generated:
    payload: Payload | None
    problems: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    skipped: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    raw: str = ""
    questions: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    prerequisites: list[Any] = field(default_factory=list[Any])   # a paper's: what it assumes (papers.clean)


@dataclass(frozen=True)
class Context:
    """Vocabulary and conventions the prompt carries besides the canon: config/topics.yaml, config/annotations.yaml,
    and the anchors file the number checks resolve against."""
    topics: str = ""
    annotations: str = ""
    anchors: Path | None = None

    @classmethod
    def load(cls, config_dir: Path, anchors: Path) -> "Context":
        def read(name: str) -> str:
            path = config_dir / name
            return path.read_text(encoding="utf-8") if path.exists() else ""
        return cls(read("topics.yaml"), read("annotations.yaml"), anchors)


KINDS = ("clarify", "mechanism", "predict", "connect", "apply", "verify")


def golden_reference(golden_dir: Path, course: str) -> str:
    """The reviewed golden cards for this course as examples, and the rejected ones as anti-examples."""
    lines: list[str] = []
    for path in sorted(golden_dir.glob(f"{course.lower()}-*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        good = [c for c in doc["cards"] if "review" in c]
        if good:
            lines.append(f"### Approved cards from {doc['source']['ref']}")
            lines += [json.dumps({k: c[k] for k in ("note_type", "deck", "fields", "tags", "mode")}, ensure_ascii=False)
                      for c in good]
        for c in doc.get("rejected", []):
            first = next(iter(c["fields"].values()))
            lines.append(f"REJECTED ({c['review'].get('reason', '')}): {first}")
        for c in doc.get("superseded", []):
            first = next(iter(c["fields"].values()))
            lines.append(f"RESTRUCTURED ({c['review'].get('reason', '')}): {first}")
    return "\n".join(lines)


def system_prompt(skill_md: str, golden: str, ctx: Context | None = None) -> str:
    ctx = ctx or Context()
    return (
        "You write Anki cards for Aman with the mnemosyne canon below. Follow it exactly: it is the "
        "single source of truth for how cards are written.\n\n" + skill_md +
        ("\n\n## Golden reference for this course (reviewed by Aman)\n" + golden if golden else "") +
        ("\n\n## Topic vocabulary (config/topics.yaml, canon 22)\n```yaml\n" + ctx.topics + "```" if ctx.topics else "") +
        ("\n\n## How Aman marks slides (config/annotations.yaml)\n```yaml\n" + ctx.annotations + "```"
         if ctx.annotations else "") +
        "\n\n## Output\nReply with exactly one fenced block tagged mnemosyne-cards holding the payload JSON. "
        "Add a top-level \"skipped\" list of {\"what\", \"why\"} for content you deliberately left out, and a "
        "top-level \"questions\" list for every question Aman wrote on the slides (see \"Aman's marks and "
        "questions\"; an empty list if he wrote none). No prose outside the block."
    )


def user_content(lec: Lecture) -> Content:
    header = [lec.about, f"Every card needs the tag {lec.tag}."] if lec.about else [
        f"Source: {lec.ref} ({lec.kind}). Course {lec.course}, lecture {lec.lecture}.",
        f"Every card needs the tag {lec.tag}.",
        "Page images show Aman's handwritten annotations; treat them as the strongest priority signals (canon 16).",
    ]
    if lec.part:
        header.append(f"This deck is long, so it's carded in parts to keep each request small; this part is {lec.part}. "
                      "Card these pages. Cards from the earlier parts are in the list below: don't repeat them.")
    if lec.partial:
        header.append("This deck was carded before, from an earlier copy. Only the pages that are new, or that carry "
                      "writing the earlier copy didn't, are included: card what they add, mainly what's newly marked.")
    if lec.emphasis:
        header.append("Emphasis notes from Aman:\n" + "\n".join(f"- {e}" for e in lec.emphasis))
    if lec.existing:
        header.append("Cards this lecture already has (don't repeat them; add what's missing):\n"
                      + "\n".join(f"- {e}" for e in lec.existing[:80]))
    content: Content = [text("\n\n".join(header))]
    for page in lec.pages:
        content.append(text(f"--- page {page.number} ---\n{page.text or '(no text layer)'}"))
        if page.png:
            content.append(image(page.png))
    if lec.reference:
        content.append(text("--- the ebook's printed text for the same pages (a clean reference; its math may be "
                            "garbled, so trust the images) ---\n" + lec.reference))
    return content


def parse(raw: str) -> dict[str, Any]:
    m = FENCE.search(raw)
    body = m.group(1) if m else raw[raw.find("{"): raw.rfind("}") + 1]
    loaded: Any = json.loads(body)
    if not isinstance(loaded, dict):
        raise TypeError("payload is not an object")
    return dict(loaded)  # pyright: ignore[reportUnknownArgumentType]


def uncomputable(card: CardIn, anchors: Path | None) -> list[str]:
    """Number checks the verify gate can't even evaluate. They're the model's syntax, so they go back for the retry
    instead of reaching Aman as 'number not checked'."""
    if anchors is None or not card.checks:
        return []
    v = verify_card(card.note_type, card.tags, [c.model_dump() for c in card.checks], anchors)
    if v.status == "skip" and ("can't parse" in v.detail or "can't compute" in v.detail):
        return [(f"a check can't be computed ({v.detail[:160]}): write the quantity as a pint expression from numbers, "
                 "units, and constant names, or drop the check if the card has no number")]
    return []


def questions(doc: dict[str, Any], schema: Schema) -> list[dict[str, Any]]:
    """Aman's questions from the payload, cleaned. A question's card keeps its problems instead of being dropped."""
    out: list[dict[str, Any]] = []
    raw: list[Any] = doc.get("questions") or []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        q: dict[str, Any] = dict(entry)  # pyright: ignore[reportUnknownArgumentType]
        if not str(q.get("asked", "")).strip():
            continue
        item: dict[str, Any] = {k: q.get(k) for k in ("page", "asked", "about", "kind", "answer", "sharper",
                                                       "beyond_course", "card", "no_card")}
        item["asked"] = str(item["asked"]).strip()
        item["no_card"] = (str(item["no_card"] or "").strip()[:300] or None) if item["card"] is None else None
        item["kind"] = item["kind"] if item["kind"] in KINDS else "clarify"
        item["beyond_course"] = bool(item["beyond_course"])
        try:
            item["page"] = int(item["page"]) if item["page"] is not None else None
        except (TypeError, ValueError):
            item["page"] = None
        if item["card"] is not None:
            try:
                card = CardIn.model_validate(item["card"])
                item["card_problems"] = schema.problems(card)
            except ValidationError as e:
                item["card_problems"] = [err["msg"] for err in e.errors()][:3]
        out.append(item)
    return out


def check(doc: dict[str, Any], lec: Lecture, schema: Schema,
          anchors: Path | None = None) -> tuple[Payload | None, list[dict[str, Any]]]:
    """Validate the whole payload, then each card. Returns the valid part and a problem list."""
    kind = {"graded-homework": "homework", "graded-exam": "exam"}.get(lec.kind, lec.kind)
    source = Source(kind=kind, course=lec.course or None, lecture=lec.lecture or None,
                    ref=lec.ref, link=lec.link)
    cards: list[CardIn] = []
    problems: list[dict[str, Any]] = []
    for i, raw in enumerate(doc.get("cards") or []):
        try:
            card = CardIn.model_validate(raw)
        except ValidationError as e:
            problems.append({"index": i, "problems": [err["msg"] for err in e.errors()], "card": raw})
            continue
        found = schema.problems(card) + uncomputable(card, anchors)
        if lec.tag not in card.tags:
            found.append(f"missing the lecture tag {lec.tag}")
        if found:
            problems.append({"index": i, "problems": found, "card": raw})
        else:
            cards.append(card)
    return (Payload(source=source, cards=cards) if cards else None), problems


async def generate(llm: LLM, route: str, skill_md: str, golden: str, lec: Lecture, schema: Schema,
                   ctx: Context | None = None) -> Generated:
    ctx = ctx or Context()
    system = system_prompt(skill_md, golden, ctx)
    content = user_content(lec)
    raw = await llm.complete(route, system, content)
    try:
        doc = parse(raw)
        payload, problems = check(doc, lec, schema, ctx.anchors)
    except (ValueError, TypeError) as e:
        doc, payload, problems = {}, None, [{"index": None, "problems": [f"unparseable output: {e}"]}]
    if problems:
        fix = ("Your previous reply had these problems. Reply again with the complete corrected payload "
               "(all cards, not just the fixed ones):\n" + json.dumps([{k: p[k] for k in ("index", "problems")}
                                                                        for p in problems], ensure_ascii=False)
               + "\n\nPrevious reply:\n" + raw[:60000])
        raw = await llm.complete(route, system, [*content, text(fix)])
        try:
            doc = parse(raw)
            payload, problems = check(doc, lec, schema, ctx.anchors)
        except (ValueError, TypeError) as e:
            problems = [{"index": None, "problems": [f"unparseable output after retry: {e}"]}]
    skipped: list[dict[str, str]] = []
    items: list[Any] = doc.get("skipped") or []
    for item in items:
        if isinstance(item, dict):
            skipped.append({str(k): str(v) for k, v in item.items()})  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    prereqs: Any = doc.get("prerequisites") or []
    return Generated(payload, problems, skipped, raw, questions(doc, schema),
                     list(prereqs) if isinstance(prereqs, list) else [])  # pyright: ignore[reportUnknownArgumentType]


QUESTIONS_ONLY = """
## This pass: Aman's questions only

Don't card the lecture itself. Read the page images for what Aman wrote by hand and reply with exactly one fenced json
block: {"questions": [...], "must_card": [{"page": 4, "what": "what he starred"}]}

`questions` follows "Aman's marks and questions" above, each question with its `card` (or `no_card`). `must_card`
lists everything he marked must-card, so it can be checked against the cards this lecture already has. Empty lists
when there's nothing.
"""

QUESTION_CARD = """
## This pass: one of Aman's questions

Write the `card` for the question below, following "Aman's marks and questions" above. Reply with exactly one fenced
json block: {"card": {...}, "no_card": null}, or {"card": null, "no_card": "why"} in the two cases the canon allows.
"""


def question_tag(q: dict[str, Any]) -> str | None:
    """The lecture tag a question's card carries, when the question came off a lecture."""
    ref = parse_title(str(q.get("lecture_title") or ""))
    return f"course::{ref.course.lower()}::l{ref.lecture:02d}-{slug(ref.title)}" if ref else None


def question_content(q: dict[str, Any], tag: str | None, existing: list[str], brief: str | None) -> Content:
    body = {k: q[k] for k in ("asked", "about", "kind", "answer", "sharper", "course", "lecture_title", "page")
            if q.get(k) not in (None, "")}
    parts = ["The question:\n" + json.dumps(body, ensure_ascii=False, indent=1)]
    if tag:
        parts.append(f"The card needs the tag {tag}.")
    if existing:
        parts.append("Cards this lecture already has:\n" + "\n".join(f"- {e}" for e in existing[:80]))
    if brief:
        parts.append("Hyperion's public-web brief on it (a lead, not a source: numbers in it still need the verify "
                     "gate):\n" + brief[:6000])
    return [text("\n\n".join(parts))]


def check_question_card(doc: dict[str, Any], tag: str | None, schema: Schema,
                        anchors: Path | None) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    raw, why = doc.get("card"), str(doc.get("no_card") or "").strip()[:300] or None
    if raw is None:
        return None, why, ([] if why else ["give a card, or say in no_card why there isn't one"])
    try:
        card = CardIn.model_validate(raw)
    except ValidationError as e:
        return None, None, [err["msg"] for err in e.errors()][:3]
    found = schema.problems(card) + uncomputable(card, anchors)
    if tag and tag not in card.tags:
        found.append(f"missing the lecture tag {tag}")
    if anchors is not None and card.checks:
        v = verify_card(card.note_type, card.tags, [c.model_dump() for c in card.checks], anchors)
        if v.status == "fail":
            found.append(f"number check failed: {v.detail[:200]}")
    return (card.model_dump(exclude_none=True) if not found else None), None, found


async def question_card(llm: LLM, route: str, skill_md: str, golden: str, q: dict[str, Any], schema: Schema,
                        ctx: Context, existing: list[str], brief: str | None = None
                        ) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """The card one question's answer teaches, for a question that came without one: (card, no_card, problems).
    One retry with the problems, like every generation."""
    system = system_prompt(skill_md, golden, ctx).split("\n\n## Output\n")[0] + QUESTION_CARD
    tag = question_tag(q)
    content = question_content(q, tag, existing, brief)
    raw = await llm.complete(route, system, content)
    try:
        got = check_question_card(parse(raw), tag, schema, ctx.anchors)
    except (ValueError, TypeError) as e:
        got = None, None, [f"unparseable reply: {e}"]
    if got[2]:
        raw = await llm.complete(route, system, [*content, text(
            "Your reply had these problems; answer again in full:\n- " + "\n- ".join(got[2]) + "\n\nReply:\n" + raw[:20000])])
        try:
            got = check_question_card(parse(raw), tag, schema, ctx.anchors)
        except (ValueError, TypeError) as e:
            got = None, None, [f"unparseable reply after retry: {e}"]
    return got


async def extract_questions(llm: LLM, route: str, skill_md: str, lec: Lecture, schema: Schema,
                            ctx: Context | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read one lecture's annotations for questions and must-card marks, without carding it again: for lectures filed
    before questions were kept, and for a second look at any lecture."""
    ctx = ctx or Context()
    system = (system_prompt(skill_md, "", ctx).split("\n\n## Output\n")[0] + QUESTIONS_ONLY)
    raw = await llm.complete(route, system, user_content(lec))
    doc = parse(raw)
    stars: list[Any] = doc.get("must_card") or []
    return questions(doc, schema), [dict(s) for s in stars if isinstance(s, dict)]  # pyright: ignore[reportUnknownArgumentType]
