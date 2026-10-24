# Your questions in Obsidian

Questions you wrote on a lecture's slides live in **that lecture's Paperless stub** in `Sources/Paperless/`, the note the homelab already makes for every document. There's no separate questions note (since 2026-10-05).
- The block sits just under the stub's "below-is-yours" line, between `mnemosyne` markers, so the morning stub refresh keeps it.
- Your own notes under `## Notes` are never touched.
- The stub already links the day the lecture was taught (`day: "[[Sep 28, 2026]]"`), so it shows in that daily note's backlinks. It also links the course.

Each question is a task with a short answer and a sharper way to ask it. Tick it when you've got it; mnemosyne reads the tick back within 5 minutes, and the review page's Questions tab does the same from your phone.

A brand-new lecture's stub appears with the homelab's 05:15 UTC run. Its questions join it on the next poll after that, and until then they're on the review page.

AI can't read or write Daily Notes, so for them to show your questions, paste one of these into your templates.

**Daily note template**: every open question, grouped by lecture:

````markdown
```dataview
TASK FROM "Sources/Paperless"
WHERE !completed AND contains(text, "❓")
GROUP BY file.link
```
````

**Course note** (e.g. PHYS 207's): that course's questions, open ones first. The stubs link the course note, so `[[]]` (this note) finds them:

````markdown
```dataview
TASK FROM [[]]
WHERE contains(text, "❓")
SORT completed ASC
```
````

## How to mark slides

These build on what you already do. Change them in `config/annotations.yaml`.

- **A question:** write it as a question, ending in "?". No special color is needed: on PHYS 207 L07 and PHYS 217 L06 it found 17 questions just from how you wrote them. Star a question you especially want answered.
- **Must-card:** your red ★, which you already use. Writing "card" or "card idea" next to something works too, the way you wrote "card ideas: what is conserved when X happens?".
- **Anything else you write** is still read as emphasis, and it's the strongest signal for what gets carded.

## Where questions go

- **The review page's Questions tab:** Got it, Card it (when the answer is worth keeping, a card is already drafted), Ask Hyperion, or Drop.
- **Hyperion:** questions that reach past the course (antennas, lightning) go there by themselves, at most 2 per lecture. The rest go when you press Ask Hyperion.
- **The Sunday report:** how many you asked, the mix of kinds, and one of them asked sharper.
- **The tutor:** Claude offers your open questions at the start of a study session, and keeps new ones you ask it.
