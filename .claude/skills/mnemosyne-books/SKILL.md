---
name: mnemosyne-books
description: Read, quote, cite, and pull practice problems from Aman's own textbooks (Griffiths, University Physics, Chen, Leon, Haberman, Beiser, Serway, Susskind, and the rest in his Calibre library) through the mnemosyne_book* MCP tools. Use whenever a study session, tutoring, card writing, or a question would be better grounded in the book he actually uses: "what does Griffiths say", "find it in my book", "give me a practice problem", "cite the page", a review session, or explaining something his course textbook covers. Also for any AI or agent that finds his Calibre books tagged ai:* and needs to know what the tags mean.
---

# mnemosyne-books

Aman's textbooks live in Calibre on his server. mnemosyne knows where every chapter and section starts, maps the
page numbers printed in his copy onto the PDF, and knows which books' text can be trusted. Use his books instead of
general knowledge whenever they cover the point: he learns in his course's notation, and a page he can turn to is
worth more than a paraphrase.

## The tools (read-only; nothing here writes anywhere)

| Tool | Use it to |
|---|---|
| `mnemosyne_books()` | list the books, their keys, courses, and status |
| `mnemosyne_books(book)` | one book's chapters and sections: what `where` can name |
| `mnemosyne_book_search(query, book?)` | find where his books discuss something (full text, every book or one) |
| `mnemosyne_book_read(book, where)` | the printed text of a section ("2.3"), chapter ("ch 2"), or pages ("p 39-47"), with a citation |
| `mnemosyne_book_problems(book, where, difficulty?)` | practice problems for a chapter or section, with the book's difficulty marks |

Keys are short: `griffiths-em`, `griffiths-qm`, `griffiths-particles`, `young` (University Physics), `chen`, `leon`,
`haberman`, `spiegel`, `boyce`, `beiser`, `serway`, `susskind-qm`, `susskind-cm`, `strogatz`, `jaynes`, `scherz`,
`nahin`, `hibbeler-statics`, `hibbeler-dynamics`, `strang`, `grady`, `bossavit`. `mnemosyne_books()` is the source of truth. Pages in `where` are the numbers printed in the book, the ones Aman reads
off his own copy. A reading is capped at 15 pages; `more` says where it continues.

In Claude Code the same tools appear as `mcp__helios__mnemosyne_book_*`.

## Treat each book by its status

Every result carries `status` and a one-line `note`. Follow them; don't rediscover them.

| `status.text` | Means | Do |
|---|---|---|
| `clean` | the text layer is faithful | quote it. Superscripts flatten ("10-9" is 10⁻⁹) and a displayed equation can split across lines: check the page image before quoting an equation |
| `math-garbled` | prose fine, symbols lost (TeX glyphs, Mathematica's "zHtL" for z(t)) | quote prose; take every equation from the page images, which come with `images="auto"` |
| `ocr` | a scan read by OCR | prose mostly right, math and tables not: read the images |
| `none` | no text layer | images only |

`status.outline` says what `where` can be: `sections` ("2.3"), `chapters` ("ch 2" or pages), `unnumbered` or
`none` (pages only).

The same status is on each book in Calibre as tags, for any AI that meets the library directly:
`ai:born-digital|scanned`, `ai:text-clean|math-garbled|ocr|none`, `ai:read-text|read-images-for-math|read-images`,
`ai:outline-sections|chapters|unnumbered|none`, and `mnemosyne:<key>` when these tools can read it.

## Citing and quoting

- **Cite every time you use a book**, with the `cite` the tool gives: "Griffiths QM, 2.3 The Harmonic Oscillator,
  pp. 39–55", or a problem's "University Physics, Problem 21.7, p. 709". He'll check it in his copy.
- **Quote short**: the sentence or equation that does the work, then your explanation. Don't paste pages back at
  him; he owns the book. Paraphrase the rest and point to the page.
- **Equations**: from the text only when the status is `clean` and the line isn't broken; otherwise from the page
  image, written in MathJax `\( \)` / `\[ \]`.
- **Use the book's notation** when it differs from yours (Griffiths' script r for the separation vector; Chen's KT
  in eV). If his course uses a different book for the same idea, say where they differ.

## Practice problems in a session

1. Pick the chapter or section from what he's working on, or from the tutor's weak cards and open mistakes.
2. `mnemosyne_book_problems(book, where)`. `difficulty` (1–3) filters by the book's own marks: Griffiths' stars,
   University Physics' dots. Problems with no mark (0) always come through.
3. Give him **one** problem with its citation, and let him work it before you help (retrieval first, as the tutor
   skill says). Pick a different problem from the same section next, not the next number.
4. Numbers come from the page image when the status isn't `clean`. Never invent a problem's given values.
5. To check his answer, work it yourself. University Physics has answers to odd-numbered problems at the back
   (`mnemosyne_book_search("ANSWERS TO ODD-NUMBERED PROBLEMS", "young")`, then read those pages). Don't hand him an
   answer before he's tried.
6. A trap he falls into becomes an M-Error card through mnemosyne-cards, cited to the problem.

## Searching

Search for the words the book would use, not the question: "Larmor radius", "method of images", "Fermi's golden
rule". Hits come with section and printed page; read the section before you rely on it, since a snippet loses
context. When several books cover it, prefer his current course's book (`courses`), then the more rigorous one.

## When the tools aren't there

Say so in one line and work from general knowledge, telling him to check his book's section on it. Don't guess page
numbers.
