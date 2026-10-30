# Carding a stuck lecture in a session

The pipeline cards lectures through the TAMU AI gateway, which gives Aman $10 of tokens a day (reset at midnight UTC,
6–7 p.m. Central) and 100k tokens a minute. When the day's allowance runs out, lectures wait: they retry by
themselves every poll and are carded after the reset, without losing a try. When he'd rather not wait ("card my
stuck lectures", or Telegram's ⏳ nudge), card them here, on his own Claude account.

1. **Find them:** `mnemosyne_stuck_documents`. Lectures are `job: paperless`. Work only the ones he asks for, or
   all `waiting_on_gateway` lectures when he says "all". Prep material and papers wait for the pipeline: they write
   more than cards. Graded work the pipeline gave up on has its own path, below.
2. **Read each deck:** `mnemosyne_doc_read(doc_id, pages)`, 8 pages a call, images on. His handwriting, stars, and
   questions are on the images: they lead, as the canon says ("Aman's marks and questions").
3. **Check what he has:** `mnemosyne_search_cards` with `tag:course::<code>::lNN*` for that lecture, the topic,
   and other courses (a PHYS 217 lab may already hold a PHYS 207 idea). Don't write a second card for an idea he
   holds; step 4 hands it this lecture's tag instead.
4. **Write the cards by this skill's canon,** exactly as the pipeline would:
   - `source`: `{"kind": "slides", "course": "PHYS207", "lecture": 9, "ref": "<the Paperless title>",
     "link": "<its Paperless link>"}`. The link must be the document's Paperless link: settling looks for it.
   - Lecture tag `course::<code>::lNN-<slug of the title>` on every card, as for any lecture.
   - **Propose them through the checked path** when you're in Claude Code on the laptop: `POST /inbox` with
     `{"payload": …, "origin": "session", "checks": true}` and the approve token, run on the Obelisk over ssh
     (the token stays there; see the obelisk-access notes). That is the pipeline's own gate: the number checks,
     dedupe, and the approval policy, so slides auto-approve and anything doubtful waits on the review page.
   - **Include the lecture's cards he already has**, with their exact prompts (find them with
     `mnemosyne_search_cards`). Dedupe drops them and gives the existing notes this lecture's tag, which is what
     puts them in the exam deck.
   - Elsewhere (claude.ai, the phone), `mnemosyne_propose_cards` works too, but it skips those checks and every
     card waits for his approval.
5. **Keep his questions:** each question he wrote on the slides goes to `mnemosyne_log_question` with the course
   and lecture number.
6. **Settle it:** `mnemosyne_settle_document(doc_id)`. It records the slides, so when he sends the deck again with
   more annotations only the new or newly marked slides are carded, and it marks the lecture done so the pipeline
   doesn't card it a second time. It refuses if nothing was proposed for that document. It waits for the poller
   if one is running, so a call can time out on your side and still finish: check `mnemosyne_stuck_documents`
   before calling it again.
7. **Tell him** in one line per lecture: how many cards, and the questions kept.

A deck he's still annotating is fine to card now: send it again when he's done and only what's new gets carded.

## Debriefing graded work in a session

When a returned exam or homework is tagged `mnemosyne-error` (the gateway gave up, as when the reply ran past the
`graded` route's max_tokens), debrief it here, in Claude Code on the laptop:

1. Read every page (`mnemosyne_doc_read`, images on). Re-solve each question that lost points before reading his work.
2. Write the debrief in exactly the format the pipeline asks the model for: `DEBRIEF` in `src/mnemosyne/graded.py`,
   with this skill's canon for the cards (M-Error for conceptual and fact misses only; `src::exam` or `src::homework`,
   the lecture's tag, `type::trap`, deck Now).
3. `POST /graded/<doc_id>/written` with the JSON as the body and the approve token, run on the Obelisk over ssh.
   It is checked like the model's but strictly: any problem comes back as a 422 and nothing is written. Fix and
   send it again. Once accepted, the cards wait on the review page, the mistakes reach the tutor, the stub gets the
   debrief, and the document is tagged done.
