# The learner graph: design

From a question Aman asked on 2026-10-29, after the neighbours index landed (`docs/embeddings.md`): how could his
notes, his cards, and the connections between them represent ideas in one space, and eventually encode how *he*
learned things and which ideas sit together in his mind, so the tutor can use those parallels?

- **Phase:** P4 (the learner model and tutor tools). The Obsidian part touches P6's "Obsidian projection polish".
- **Status:** design only. Nothing here is built. It ends with a phased plan (§7) and the questions Aman must answer
  (§8).
- **Numbers** are from the live Ledger and vault on 2026-10-29, read-only.

## The short version

- **One graph, in the Ledger.** Typed nodes (cards, vault notes, lectures, questions, mistakes, probes, book
  sections) and typed edges (similar, came from, linked by him, studied together, failed together, built on). No
  graph database: it's about 5k nodes, and sqlite plus numpy is plenty.
- **Shared meaning comes from the embedding.** Every card's whole idea is already in `note_vector`
  (`nomic-embed-text`, 768-d). The homelab's `rag.py` embeds exposed vault notes with the same model, so the two can
  share one space, with one caveat about prefixes and chunks that G0 measures before anything relies on it (§1.2).
- **His structure comes from provenance and behaviour, so it's a graph first.** Each personal edge keeps its
  evidence, so the tutor can say *why* two ideas sit together for him ("you linked these", "both came from the same
  week of 207 and 217"). A learned personal embedding comes later, once there's data to train and test it on (§3).
- **Edges come in three classes, and they're never mixed up:**
  - **his:** things he did or said, like a link he wrote, a connection he stated, or a probe he passed;
  - **curated:** made by the pipeline and approved by him, like tags or a merge;
  - **derived:** computed, like similarity or a time window.

  Links mnemosyne writes into the vault are derived, never his. Without that rule, the graph would end up learning
  from its own output.
- **The tutor gets one new read-only tool, `mnemosyne_bridges`.** Given a new or weak idea, it returns strongly-held
  neighbours from other courses, with what they share. Gaps between clusters become "connect" questions, and canon
  22's merge-up becomes a sweep over existing notes as well as new ones (§4). All of these are proposals.
- **His notes stay small and his own.** The design works with no new notes at all (G1 uses only what the Ledger
  already knows). Since the notes are mostly unwritten, §5 runs on working assumptions and a reader that needs no
  convention, then a checkpoint refines it from what he actually writes. Each concept note he writes, with a line on *why* two ideas are alike, is the most valuable edge
  the system can get (§5).

## What exists today

| Source | Count | Where | Note |
|---|---|---|---|
| Card notes embedded | 3,240 notes (3,549 cards) | `note_vector`, nomic, `full` | 2,014 cards were written by the pipeline and have Ledger provenance. The 1,385 `src::legacy` cards have only their tags. |
| Vault notes | | `rag.py`'s `/vec/vectors.db` | Exposed ones only. Hidden means `ai: none`/`local`, or under `Daily Notes/` or `Main Notes/People/`. |
| Paperless stubs | | `Sources/Paperless/` | One per document; mnemosyne writes only its marked block. |
| School notes | | `Main Notes/School/` | A hub note per course, plus his course notes. **No concept notes yet.** |
| Wikilinks in School notes | | | Mostly structural: people, the university, course hubs. A few point at concepts with no note yet. |
| Card sources | | `card_source` | Paperless slide decks, decks carded in sessions, chat proposals, questions, exams |
| Questions | | `question` | From lectures and sessions; some are `connect` questions, many still open. |
| Mistakes | | `mistake`, `mistake_attempt` | Each has a `topic::` tag, from graded work. |
| Probes | | `probe` | |
| Tags on written cards | 2,014 | `card_draft.tags_json` | 299 carry a `principle::` tag, 359 more than one `topic::`, and 52 more than one course. |
| Merges | 3 merges, 2 merge-ups | `card_draft.merge_into` | |
| Lapses | | anki-core | Too few yet to learn from. |
| Papers | 0 | `paper` | |

Two of these matter most:
- **The open connect questions are Aman asking for edges himself.** They're the first thing bridges should
  answer, and the honest test of whether they work.
- **The dangling concept links are notes he'd already started to want.** Their names are a ready-made list of
  concept notes to write.

## 1. Nodes

### 1.1 What's a node

The **card note is the idea node**. Canon 22 already says one idea is one note, tagged with every course that
teaches it, so there's no separate "idea" layer above cards. Clusters are derived each night and never stored as
ideas.

| Kind | Key | Vector from | Source | Edges only? |
|---|---|---|---|---|
| **card** | Anki note id (MnemosyneID kept) | `neighbors.note_text(…, "full")` | `note_vector` (exists) | |
| **vault note** | vault path (renames re-keyed by text hash) | the note's own text, minus frontmatter and any mnemosyne block | §1.2 | |
| **lecture** | Paperless doc id (the stub's `paperless_id`) | mean of its cards' vectors: a lecture is what it taught | `card_source`, `card_batch`, course/lecture tags | |
| **question** | `question.id` | `asked` + `sharper` | `question` | |
| **mistake** | `mistake.id` | `concept` + `trap` + `fix` | `mistake` | |
| **graded work** | Paperless doc id | none | `graded_work` | yes |
| **probe** | `probe.id` | `hook` + `question` | `probe` | |
| **book section** | book key + outline section | the section title and its first page | `library.section_at`; only sections something points at | |

Later kinds, once they have a reason to exist: clips (`/watch`, which already link the cards they explain), watched
video spans, and papers (`papers.py`'s prerequisites already name the cards and book sections they rest on).

A lecture's stub and the lecture itself are two nodes. The stub's `## Notes` (Aman's words) is a vault note, and the
lecture is its cards. That keeps what he wrote separate from what the slides said.

### 1.2 One space: what's compatible, and the caveat

What's already shared:
- **Model and size:** both stores use `nomic-embed-text` on `helios-ollama`, 768-d.
- **Privacy filter:** `rag.py` filters exposure at embed time, reusing `vault.py`: hidden notes are never indexed, and
  a reindex drops a note that has since become hidden.
- **Retrieval design:** dense + BM25 + RRF + the bge reranker. `neighbors.py` copied it.

What isn't the same:
- **Prefixes:** `rag.py` sends raw text with **no task prefix**. `note_vector` embeds with `search_document: `.
  nomic's prefixes are trained in, so the two sets of vectors can sit at a systematic offset from each other.
- **Units:** `rag.py` stores **~1000-character chunks** (150 overlap), frontmatter and mnemosyne blocks included. The
  graph wants one vector per note, from his words only.

So "already compatible" holds for the model, but not yet for the vectors. G0 measures it (§7) and picks one of these:

| Option | How | For | Against |
|---|---|---|---|
| **A. Read `rag.py`'s index** | Mount `/vec` read-only into the api. A note's vector is the mean of its chunks. | Inherits the homelab's privacy filter exactly; no second copy of note text. | Prefix offset; mnemosyne blocks inside chunks; a homelab compose change. |
| **B. Embed notes in mnemosyne, via helios-mcp** | Fetch exposed notes' text through the homelab's own tools (`get_note`, `search_vault`), strip markers, embed with `search_document:`. | Clean per-note vectors in the same prefix as cards; privacy still decided by the homelab. | A dependency on helios-mcp from the api; a second copy of the vectors. |
| C. Mount the vault | | | Reimplements the exposure rules. **Rejected.** |

The recommendation is B if G0 shows the prefix offset hurts cross-store retrieval, and A otherwise. Either way, the
homelab stays the single gate on what's exposed.

## 2. Edges

Every edge has a kind, a class (his, curated, or derived), a weight, its evidence (JSON: the ids, dates, and words
behind it), and `first_seen`/`last_seen`.

| Kind | Between | From | Class | Available now |
|---|---|---|---|---|
| **similar** | any two vector nodes | kNN (k = 10) over all vectors, above a floor; reranked only where it matters (bridges) | derived | yes |
| **came from** | card → lecture, session, question, mistake, vault note | `card_draft → card_batch → card_source`; `question.draft_id`; `mistake.card_ids`; a `> [!card]` callout's note; legacy cards by their `course::x::lNN` tag | his history | yes |
| **taught in** | card → every course on it | the course tags: canon 22's connections | curated | yes (52 multi-course cards) |
| **shares a principle** | cards with the same `principle::` tag | tags | curated | yes (299 cards) |
| **merged** | merged draft → note | `merge_into`, `merge_up` | curated | yes (5) |
| **linked** | his note → his note, his note → card | `[[wikilinks]]` in exposed notes, outside mnemosyne markers; links to hidden notes (People, Daily Notes) are dropped | his | after G2 |
| **stated** | idea ↔ idea | a parallel he states in a session, logged add-only (§4.1); a connection he ticks in a stub (§5.2); a connect question he answered | his | after G2 |
| **built on** | held card → probe or retry | `probe.note_id`, `mistake_attempt` | his, tested | yes |
| **studied together** | card ↔ card, lecture ↔ lecture | first reviews in the same sitting (the upkeep rule: under 10 minutes apart); lectures in different courses within 3 days (PHYS 207 and 217 often teach a topic a day apart) | derived from behaviour | lectures yes; first sittings thin |
| **failed together** | card ↔ card | both rated Again in one sitting, or one lapse followed by the other's within a week | derived from behaviour | **no**: waits for E3's data |

Rules:
- **Ordinary review co-occurrence isn't an edge.** Anki shuffles due cards, so two cards reviewed in the same
  sitting says nothing about him. Only *first* meetings count.
- **Machine-made links are never his.** The link reader ignores everything inside `<!-- mnemosyne:start -->` blocks,
  and any note tagged `ai-generated` (`Sources/AI/`). Without this rule, the graph would learn from its own
  suggestions.
- **Similar and failed-together use the same geometry in opposite ways.**
  - Close and failing together is **interference**: put the two side by side and name what tells them apart, as the
    tutor already does. LECTOR assumes this; here it's measured.
  - Close, with one held and the other new, is a **bridge**.

**Storage:** two Ledger tables (one migration), `graph_node` and `graph_edge`.
- Edges are keyed on `(src, dst, kind, evidence_ref)`, so every write is an upsert.
- **Derived and curated edges are rebuilt deterministically** at 04:15, after `neighbors_index`, so running it again
  is a no-op.
- **His edges are add-only**, like `probe` and `mistake_attempt`.

At this size (~5k nodes, ~60k edges), a rebuild is seconds of numpy.

### How he learned an idea

The trace isn't a separate table. It's a query over an idea's **came from**, **built on**, and **stated** edges,
ordered by time:
1. where he first met it (the lecture, the date, and the session or chat that carded it);
2. the questions he asked about it;
3. the probes and retries that tested it;
4. the mistakes that touched it;
5. how well he holds it now.

That's what lets the tutor say: "you met RC circuits in the 217 lab before 207's lecture, and you asked why τ = RC".

## 3. Shared meaning vs his structure

**The embedding says what ideas mean to anyone.** RC discharge and radioactive decay are close in nomic's space for
everyone who has read physics.

**His structure is what's particular to him:**
- which ideas he met together;
- which he linked in his own words;
- which fail together;
- which he can build from;
- which crossings he has asked about.

None of that is in a general embedding, and none of it can be inferred from text. It comes from provenance and
behaviour, so it starts as a graph:

- **The data is thin.** E3 and E4 in `docs/embeddings.md` wait for data for the same reason:
  - too few cards have lapsed for a co-failure signal to train on;
  - his explicit edges are a few hundred at most: connect questions, multi-course notes, merges, probes, and a
    handful of concept-shaped wikilinks.
- **A learned embedding from a few hundred pairs would overfit.** It would also hide its reasons. A graph keeps every
  edge inspectable, and the tutor can cite its evidence.
- **The two combine at query time.** Similarity from the embedding, the weight of his and curated edges from the
  graph. The weights live in config, so they can be retuned without a rebuild.

### Later: a personal embedding (G4)

There are two candidates, and both are small enough for the Obelisk's CPU:

1. **An adapter on nomic:** a low-rank linear map (r = 16: 768 × 16 ≈ 12k parameters), trained contrastively. His
   positive pairs (links, stated connections, answered connect questions, co-failures) should land closer than shared
   meaning predicts, and random pairs from the same courses shouldn't. It keeps the general space and bends it toward
   him.
2. **A graph embedding** (node2vec, or a spectral embedding of the his + curated edges), used next to nomic rather
   than instead of it.

**Ship rule (E1's):** held-out his-edges, scored by AUC with a 95% bootstrap CI. A personal embedding ships only if
it beats nomic + the graph scorer with CIs that don't overlap. Otherwise it goes under "Didn't work".

**Trigger:** about 300 personal positive pairs, or E3's trigger (about 300 lapsed cards or about 15k reviews),
whichever comes first. The weekly audit checks it monthly, next to E3 and E4.

## 4. What the tutor does with it

### 4.1 Bridges: a new idea through a strongly-held neighbour from another course

**Input:** a card id, a lecture, or text ("the idea he's about to meet").

**Candidates:** `neighbors.search`'s dense + word + rerank list, filtered to:
- a different home domain (first `topic::` tag) or a different course;
- held strongly: R ≥ 0.9 and stability ≥ 21 days, which the tutor skill's table calls "he owns it".

**Score:** reranked similarity × strength × (1 + bonuses). Bonuses come from his and curated edges between the two
sides: a shared `principle::` tag, his link, a stated connection, or a probe he passed from that card.

**Output:** the top three, in `search_cards`' shape, each with:
- `shares`: the principle tag, or the reranker's top-scoring sentence pair;
- `evidence`: the edges behind it;
- `how_learned`: the trace in §2.

**Example (illustrative, not checked against his cards):** a NUEN 201 card on decay chains, where he's new to
secular equilibrium. A bridge could be the capacitor charging to steady state (PHYS 207), or the first-order linear
ODE he owns from MATH 308. All three are `principle::exponential-growth-and-decay`.

**How the tutor uses it** (a new "Parallels" part of the skill's §2):
- **Retrieval first.** Ask him to recall the bridge card, then ask what the new idea has in common with it. Let him
  make the parallel.
- **When he states it, log it** with the add-only `mnemosyne_log_connection` (a his-edge, in his words).
- **When it doesn't land, say so.** Log nothing.

This replaces the skill's current instruction to "connect it to something he's strong on", which today the tutor
does by hand from `search_cards`.

**His open connect questions come first.** For each one, find bridges on both sides. A session can then work it
with him, as §3 of the skill already does for open questions.

### 4.2 Gaps: two clusters he hasn't connected → a "connect" question

**Clusters:** communities on the **similar** edges (Leiden, or agglomerative clustering on the vectors; at 3k notes
either takes seconds), labelled by their most common home topic. They're rebuilt nightly, and cluster ids aren't
stable, so nothing refers to them across nights.

**A gap** is a pair of clusters where:
- **they're close in meaning:** high mean similarity over their k closest cross pairs, or a shared `principle::` tag;
- **nothing personal joins them:** no note carries both courses, and there's no link of his, no stated connection,
  no connect question touching both, and no merge;
- **he holds both sides:** he knows each idea but not the link between them, so the question is answerable.

Gaps are ranked by how strongly he holds both sides, and by an exam in the next two weeks touching either side.

**The question:** the nightly job only finds candidate pairs (pure computation, no model).
- The question itself is written in a session (the tutor or the weekly audit, on Aman's own account) with
  `kind: connect` and a new origin, `graph`. It goes through the add-only `log_question`, attached to the newer
  side's lecture, so it lands in that stub next to his own questions, marked as suggested.
- **Cap:** three a week. He already has open connect questions of his own, and his questions matter more than
  generated ones.

### 4.3 Canon 22's merge-up, extended

**Today:** canon 22 runs only at intake. When dedupe matches a new card to an existing note (≥ 0.88 on the first
field), `merge.py` uses `foundations` (topics.yaml) and `homes` (courses.yaml) to decide between merging and merging
up. Aman decides on the review page.

**What the graph adds:**

1. **A sweep over existing notes, not just new ones.** It finds cross-course pairs that dedupe never compared:
   - the 1,385 legacy cards;
   - every card written before whole-idea embeddings existed;
   - every pair where the more fundamental course carded the idea *later* (MATH 309's linear algebra arriving after
     statics carded a projection).

   They're proposed as held merge or merge-up items, with the pair shown. E1 found no safe auto-reject threshold, so
   this is **ranked and shown, never automatic**.
2. **Tag joins without merging.** Canon 22 says a course's own view of an idea is its own card (the circuits view of a
   capacitor is circuits). So when two cards are the same principle in different framings, the right move is a tag,
   not a merge: the missing `topic::` or `principle::` tag. Only 299 of 2,014 written cards carry a `principle::` tag.
   These go through the reviewed retag path (`scripts/retag.py`), item by item in the weekly audit.
3. **Gaps in `foundations` and `homes`.** When a cluster spans courses and none of them is its home, that's a weekly
   audit item that proposes a `homes` entry.

Which framing is more fundamental still comes from config, never from the graph (`merge.py`'s rule). The graph only
supplies candidates.

## 5. His notes: what he writes, what mnemosyne adds

**Working assumptions (Aman, 2026-10-29).** His notes are mostly unwritten, so this part starts from assumptions and
is refined as he writes, not settled up front:
- **The reader doesn't depend on a convention.** Every exposed note he wrote counts as a vault-note node, and every
  `[[link]]` in it counts as a **linked** edge. "Exposed" means outside `Sources/`, not tagged `ai-generated`, and not
  hidden. The folder, the `concept` tag, and the `topic:` key below are hints that raise a note's weight, not
  requirements. If he writes differently from what's sketched here, his notes still join the graph.
- **What's sketched below is a starting point**, not a format to follow.
- **Checkpoint:** after his first ten or so cross-course notes, or a month (whichever comes first), the weekly audit
  looks at what he actually wrote. It checks where the notes live, which link shapes he uses, whether he writes the
  *why* of a parallel, and how often bridges matched his own links. It then proposes changes to the reader and to
  this section, item by item. The record goes in `docs/weekly/`.

### 5.1 What he'd write (small, in his words)

- **Concept notes**, one per idea he wants to own *across* courses, not one per lecture.
  - **Where:** assumed to be `Main Notes/School/Concepts/<Idea>.md`. Anywhere exposed works.
  - **Frontmatter:**
    - `tags: [concept, PHYS-207, NUEN-201]`, using the course codes that already join Paperless;
    - `topic: physics::em::capacitance`, from `config/topics.yaml`;
    - `ai: open`.
  - **Body:** the idea in a few lines, then a **Like:** line, for example
    `[[Radioactive decay]]: same ODE, τ = RC`. The words after the colon label the edge, and they're the most valuable
    thing he can write: a parallel *he* sees, with its reason.
  - The bar: write one when an idea turns out to cross a course boundary. That's perhaps one to three a week.
  - The dangling links already name some: `[[uranium]]`, `[[nuclear fusion]]`, `[[gluons]]`,
    `[[confidence interval]]`.
- **A line under `## Notes` in a lecture's stub**, which is already his area: "this reminded me of…". It's read as his
  words about that lecture.
- **`> [!card]` callouts** inside a concept note. The watcher already turns them into drafts. A card written from one
  gets a **came from** edge to the note.

What he **doesn't** need to write: Daily Notes stay private and nothing needs them. Course hub notes already exist,
and concept notes link to them. G1 works with no new notes at all.

### 5.2 What mnemosyne adds automatically (inside its markers, under `Sources/`)

- **A "Connections" part in each lecture stub's mnemosyne block.** It lists the lecture's top three bridges to other
  courses' lectures he holds, each a `[[link]]` to that lecture's stub with the shared principle. Each one is a task
  line, like the questions:
  - **Ticking one** confirms it as his (a **stated** edge), read back on the next poll, the way questions are today.
  - **Unticked lines stay derived.**

  Because they sit inside the markers, the link reader never counts them as his.
- **Suggested connect questions** (§4.2), in the same block's questions list, marked as suggested.
- **No generated "idea pages"** under `Sources/` (an assumption, revisited at the checkpoint). Obsidian's graph view
  would then mix machine edges with his, and the vault's graph is the one place his structure is purely his.

## 6. Constraints, and how the design meets each

| Constraint | How |
|---|---|
| Obsidian `ai:` tiers | Only exposed notes: `ai: open`, outside `Daily Notes/` and `Main Notes/People/`. The homelab's exposure rules decide, through helios-mcp (option B, decision 1); mnemosyne never reimplements them. `ai: local` notes join only as opaque, text-free nodes whose links become edges (decision 2); their titles and text never reach a tool, since the tutor's tool output reaches cloud Claude. An edge to a hidden note is dropped too, since it would leak the note's title. |
| Write only inside mnemosyne markers, under `Sources/` | The only vault write is the stub block, which `obsidian.py` already places. Concept notes are his; mnemosyne reads them and never edits them. |
| Daily Notes are private | Never read. "Studied together" comes from the revlog and Ledger timestamps. Stubs' `day: "[[…]]"` links point at Daily Notes, so they're ignored as edges. |
| Cloud-facing MCP tools can only propose | `mnemosyne_bridges` and any graph reads are read-only. `log_connection` and connect questions are add-only, like `log_probe` and `log_question`. Merges, merge-ups, and tags are held for the review page or the weekly audit, where Aman decides. |
| No paid spend | No LLM in the nightly build: embeddings and clusters are computation. Connect questions are worded in sessions on Aman's own account. Nothing touches the TAMU allowance or adds a billed route. |
| Local models | nomic on `helios-ollama` (274 MB; it fits beside the pinned qwen2.5:7b) and the bge reranker on TEI, both in use today. G1–G3 add no model. G4's adapter trains on CPU in seconds. |
| Invariants 1, 2, 4, 8 | The collection is read only through anki-core. No schema changes. Derived edges are rebuilt deterministically and his edges are add-only, so re-running is a no-op. The work rides the existing 04:15 run and writes only the Ledger. |

## 7. Phased plan

Each phase starts only once the previous one's "done when" passes.

**G0: check the shared space** (one session, read-only).
- Embed about 100 exposed School notes and stubs two ways: `rag.py`'s raw chunks (mean per note) and
  `search_document:` whole notes with markers stripped. Score both on whether each stub's own text retrieves its
  lecture's cards (recall@10, since the answer is known), against card ↔ card retrieval as a baseline.
- Hand-run bridges (`neighbors.search` plus the strength filter) for 10 of his open connect questions, and have Aman
  rate each one useful or not.
- Count the his-edges available today.
- **Done when:** the note embedding (raw chunks vs whole notes) is chosen on numbers, and Aman's rating gives bridges a
  hit rate worth building on. The read path is B, through helios-mcp (decision 1).

**G1: the graph from what the Ledger already knows.**
- **Build:**
  - one migration: `graph_node` and `graph_edge`;
  - a 04:15 step after `neighbors_index`, behind a `graph.enabled` flag in `config/llm.yaml`, next to
    `embeddings.neighbors`;
  - the nodes: cards, lectures, questions, mistakes, probes;
  - the edges: similar, came from, taught in, shares a principle, merged, built on, and lecture-level studied
    together.
- **Expose:**
  - `GET /learner/bridges?card_id=…|q=…`;
  - `mnemosyne_bridges`, read-only, a hook in `ops/helios-hooks/` applied when the flag goes on;
  - the tutor skill's "Parallels" part.
- **Done when:**
  - a second rebuild reports 0 created and 0 updated;
  - a rebuild takes under a minute;
  - for 10 sampled open connect questions, Aman rates at least half of the bridges useful.

**G2: his notes.**
- **Build:**
  - vault-note nodes and **linked** edges from every exposed note he wrote, convention-free (§5's working
    assumptions);
  - a short note in `docs/user-guide.md` on what helps (a *why* after a link), framed as a suggestion;
  - the stub's Connections part, with tickable lines;
  - the add-only `mnemosyne_log_connection`.
- **Done when:**
  - tests prove that marker content, `ai-generated` notes, and hidden notes never become nodes or his-edges;
  - a ticked connection is read back on the next poll;
  - one week of sessions has logged at least one stated connection.
- **Then the notes checkpoint** (§5): once he's written his first ten or so cross-course notes, or after a month,
  the weekly audit refines the reader and §5 from what he actually wrote.

**G3: gaps and canon 22, in the weekly audit.**
- **Build:**
  - up to three connect-question candidates a week in the audit's evidence (`weekly.py`);
  - the merge and merge-up sweep over existing notes;
  - missing `principle::` tags, through the reviewed retag path.

  All of it is decided item by item.
- **Done when:** two audits have run with these items and recorded their approval rates. If fewer than 30% are
  approved two weeks running, the gap score is retuned or dropped.

**G4: behaviour edges and a personal embedding** (when the data exists).
- **Build:**
  - **studied together** at card level, and **failed together**;
  - then the adapter or graph embedding (§3).
- **Trigger:** E3's (about 300 lapsed cards or about 15k reviews) for behaviour edges, and about 300 personal positive
  pairs for the embedding. Checked monthly in the weekly audit.
- **Done when:** the embedding beats nomic + the graph scorer on held-out his-edges with non-overlapping 95% CIs.
  Otherwise the result is recorded under "Didn't work".

**Not in scope:**
- **Scheduling.** FSRS stays content-agnostic; spacing out interfering cards (LECTOR, fsrs4anki-helper #371) is out,
  as in `docs/embeddings.md`.
- **An LLM-built knowledge graph.** Hyperion has one for research discovery. A paper there can later reach this graph
  through `papers.py`'s prerequisites, but the two stay separate.
- **A graph database.**

## 8. Decisions (Aman, 2026-10-29)

The shape of his notes was never one of these questions. It runs on §5's working assumptions until the checkpoint,
because the answer comes from what he writes. The same goes for generated idea pages: none, until the checkpoint says
otherwise.

1. **Vault vectors: B.** The api reads exposed notes through helios-mcp, not a mount of `/vec`. He cares most about
   learning quality and is fine with either. G0 still measures both ways of embedding a note (§7) before G1 fixes
   one.
2. **`ai: local` notes: yes, as edges without text**, as long as nothing exposes his personal life or confidential
   material. "His links sharpen the graph."
   - What a local note becomes: an **opaque** node, with no title, text, or vector, keyed on a hash of its path. Its
     `[[links]]` become edges, so two exposed ideas a local note joins count as related, but the local note itself
     never shows up anywhere.
   - `Daily Notes/`, `Main Notes/People/`, and `ai: none` stay out entirely, edges included.
   - helios-mcp needs a links-only read for local notes (part of B). An edge from a local note to another hidden note
     is dropped.
3. **Machine connections in stubs: yes.** A Connections part in the stub's mnemosyne block, with lines he can tick
   to confirm (§5.2).
4. **Generated connect questions: three a week.** They go in the stubs, with his own, marked as suggested (§5.2).
   The weekly audit refines the cap and the placement.
5. **Stated connections: yes.** `mnemosyne_log_connection` is add-only and in his words.
6. **Canon 22 sweep: yes, legacy notes included.** **Five candidates a week** (Claude's call, as he asked), highest
   similarity first, in the weekly audit's evidence. The weekly audit refines the number by approval rate: under 30%
   approved two weeks running lowers it, and over 80% raises it.
7. **Principle tags: yes.** The weekly audit proposes missing `principle::` tags in batches, through retag.
