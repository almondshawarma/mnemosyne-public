# Reviewing a batch on the review page

Aman reviews card batches on mnemosyne's review page: **http://<obelisk>:8110/review**, on the tailnet, from his phone, iPad, or ThinkPad. He approves, edits, or rejects each card with a reason, adds notes for the batch, and marks left-out items that should have been cards. Batches you push appear in its "From Claude" tab.

Everything lives in mnemosyne's Ledger (`review_set`, `review_item`, and the card drafts):
- Your new cards become drafts held for him. The verify gate and dedupe still run, and their findings show on each card.
- Cards whose content is already in the Ledger (say, golden cards already in Anki) are linked, not duplicated.
- Approving a new card writes it to Anki. Editing a card that is already in Anki updates that note in place.
- Rejecting a card that is already in Anki changes nothing there. The export lists it so you can ask him what to do.
- Telegram never announces these batches.

`review.py` sits next to this file. It needs only Python 3.12: run it with `uv run review.py …`, or with `python` directly. The API is reached from the Obelisk (`ssh obelisk`), where the tokens live in `~/helios/mnemosyne/secrets/`. Never print a token.

## Push a batch

1. Generate the payload as SKILL.md describes. Add these review-only keys where they help:
   - `question` on a card: something only Aman can answer. It shows in a yellow box, and the page lists these under "Questions".
   - `why` on a card: one line on why it's shaped this way. Add `canon: [n]` for the rules it applies; the page shows each rule's title.
   - `signals`: what raised priority, e.g. his annotations.
   - `skipped`: `[{what, why}]` for content you deliberately left out. He can mark any of these "Should be a card".
2. Save the payload to a scratch file and run
   `review.py hold PAYLOAD --id <id> --title "<title>" --kind lecture|golden|cards body.json`.
   Ids are lowercase kebab-case and unique, e.g. `nuen201-l08-2026-10-02`.
3. Post it:
   `scp body.json obelisk:/tmp/mn-review.json`, then over `ssh obelisk`:
   `cd ~/helios/mnemosyne && curl -s -X POST -H "Authorization: Bearer $(cat secrets/approve_token)" -H "Content-Type: application/json" --data-binary @/tmp/mn-review.json http://<obelisk>:8110/reviews`.
   - The reply counts `cards`, `new` (fresh drafts), `linked` (already in the Ledger), and `without_draft` (cards that fail the schema; the page shows why).
   - An id that already exists gets 409. Never reuse one: decisions Aman made are never overwritten.
   - A suspected duplicate (the embedding check at 0.92 or above) is still held, with a "probably a duplicate" flag, so Aman decides it. If you know a card duplicates an existing note, leave it out and list it in `skipped`.
   - A push you got wrong can be undone with `DELETE /reviews/<id>` while nothing in it is decided. The held drafts stay, and pushing again links them.
4. Send Aman the link `http://<obelisk>:8110/review#r/<id>` and the card count.

## Take it back

When Aman says he's done (or `GET /reviews/<id>` shows `"status": "reviewed"`). If he finished by mistake, he can press **Undo finish** on the page, or you can `POST /reviews/<id>/reopen`; either works until the batch is applied.

1. Fetch the export: `GET /reviews/<id>/export` with the approve token. Save it to a scratch file as `reviewed.json`. It has the same shape the old viewer produced:
   - `cards`: approved and edited cards, each with `review` (`verdict`, `reason`, `before` for edits).
   - `rejected` and `pending`.
   - `review`: `notes`, and `promote` (the skipped items he wants carded).
   - `anki`: `rejected_but_in_anki` and `no_draft`.
2. Run `review.py summary reviewed.json`. If anything is pending, ask whether he's finished before going on.
3. Hand the result on:
   - **Golden batches** (`kind: golden`), in the mnemosyne repo: `uv run scripts/golden_review.py merge reviewed.json`, then commit.
   - **Everything else:** approved cards are already in Anki; there's nothing to write.
   - **Promoted items:** write them as new cards in a follow-up review.
   - **A tag or field change he asks for in a note** on a card that is already in Anki: `POST /drafts/<mnemosyne_id>/revise`, quoting his note as the reason, and update the golden file to match.
   - **`rejected_but_in_anki`:** tell Aman which cards these are. Removing or suspending them is a destructive operation (CLAUDE.md invariant 9), so it needs a dry-run diff and his explicit OK.
4. Read every `reason` and the batch `notes`. Also read `GET /review/decisions?since=<date>`, which has his reasons on pipeline cards decided in the Waiting tab. A pattern that isn't in the canon yet becomes a proposed canon change: tell Aman in chat what you'd add, and add it only once he agrees.
5. `POST /reviews/<id>/applied` and tell Aman the summary line.
