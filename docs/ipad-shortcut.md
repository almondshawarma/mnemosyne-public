# "→ Mnemosyne" Shortcut (iPad and iPhone)

Share a PDF (a GoodNotes/Notability export, Files, a scan), pick the course and kind, and mnemosyne files it into Paperless with the right tags. A lecture becomes cards within about 10 minutes: you get a Telegram message when they're added, and one with buttons if any need you.

**Needs:** Tailscale connected on the device, and the propose token. Get the token on the ThinkPad:

```bash
ssh obelisk 'cat ~/helios/mnemosyne/secrets/propose_token'
```

That token can only propose, never approve. If it ever leaks, delete `secrets/propose_token`, re-run `scripts/obelisk_secrets.sh`, and restart the api.

## Build it (Shortcuts app → + → name it "→ Mnemosyne")

1. **Shortcut details (ⓘ):** turn on **Show in Share Sheet**, and set the input types to **PDFs** and **Files**.
2. **Get Contents of URL:** `http://<obelisk>:8110/courses/codes` (method GET). This returns a plain list of course codes.
3. **Choose from List:** *Contents of URL*, with the prompt "Course". Set a variable `Course`. (No Repeat loop needed; the loop version adds courses more than once.)
4. *(Step removed.)*
5. **Choose from Menu:** "Kind", with options `slides`, `notes`, `syllabus`, `graded-homework`, `graded-exam`, `review-sheet`, `paper`. Under each option, add **Text** with that same word, then after the menu set a variable `Kind` to *Menu Result*.
6. **Ask for Input:** Number, prompt "Lecture / assignment number (blank to use the one in the file name)". Set a variable `Number`.
7. **Ask for Input:** Text, prompt "Title", default *Shortcut Input › Name*. Set a variable `Title`. The file name is fine as it is: a leading "PHYS 221 L02 - " is stripped, and its number is used if you left Number blank, so titles never repeat the course.
8. **Get Contents of URL:** `http://<obelisk>:8110/intake`
   - Method **POST**.
   - Headers: key `Authorization`, value `Bearer ` followed by the token: the word Bearer, one space, then the 43-character token with no quotes and no line break. A mistyped token gets **401**, and the server log says whether the Bearer prefix arrived and how long the token was.
   - Request Body **Form**:
     - `file` (type File) = *Shortcut Input*
     - `course` = *Course*
     - `kind` = *Kind*
     - `number` = *Number*
     - `title` = *Title*
9. **Get Dictionary Value:** `title` from *Contents of URL*, then **Show Notification** "Filed: *Dictionary Value*".

If the course list can't load (Tailscale off), step 2 fails. Turn Tailscale on and share again.

If the file's name starts with a different course than the one you picked (say "MATH 309 L07 - …" with NUEN201), nothing is filed and the notification reads "NOT filed: …". Share it again with the right course.

The course list ends with **none**: pick it for a paper you're reading for yourself. (For a lecture, none is refused.)

## What happens after

| Kind | Paperless type | Opt-in tag | What mnemosyne does |
|---|---|---|---|
| slides, notes | Lecture | (lectures are in by default) | Lecture pipeline. It polls every 30 s for 5 min after an upload, and every 5 min otherwise. |
| review-sheet | Lecture | `mnemosyne` | Lecture pipeline |
| syllabus | Syllabus | `mnemosyne` | Exam map with Telegram confirmation (P3, in progress) |
| graded-homework, graded-exam | Assignment / Exam, plus `graded` | `mnemosyne` | Error cards, always reviewed (P3, in progress) |
| paper | Paper (plus the course tag, unless you picked **none**) | `mnemosyne` | Research-paper cards, always reviewed, and a "Before you read" list in its Obsidian note. Give it a title like "Smith 2025 - Bohm diffusion in stellarators": the title becomes its tag. |

For emphasis ("this is on the exam", "confusing"), add a **note** to the document in Paperless, or star and highlight it in the GoodNotes export. Both reach the model as priority signals.

## Re-sharing a lecture (a newer annotated version)

Share it again, the same way. mnemosyne finds the earlier document with the same course and lecture number (or the same title, for documents without a number):

- **A changed file:** once Paperless has consumed the new one, the old one's notes and your own tags are copied over, and the old one moves to Paperless's **trash** (recoverable for 30 days). The new version then goes through the pipeline. Cards already in Anki for that lecture are passed to the model, so it adds what's missing instead of repeating them.
- **The identical file:** Paperless refuses it as a duplicate, so mnemosyne runs the existing document through the pipeline again instead.

Your slides and your notes for the same lecture count as the same document, because both are filed as type Lecture with the same number. Keep one annotated PDF per lecture.
