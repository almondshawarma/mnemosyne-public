"""Adds the mnemosyne_book* tools to every Helios MCP instance: Aman's textbooks, read-only, for quoting, citing,
and practice problems (the mnemosyne-books skill). They only read, so the propose token is enough.
Additive and idempotent like the other patches: backs up to *.bak-mnemosyne-books, refuses to run twice."""
import shutil
import sys
from pathlib import Path

STACK = Path.home() / "helios" / "stack"
HOOKS = Path.home() / "helios" / "mnemosyne" / "ops" / "helios-hooks"
ANCHOR = "    if mnemo.can_approve():\n"
TOOLS = '''    @mcp.tool()
    def mnemosyne_books(book: str = "") -> dict:
        """Aman's textbooks (Calibre) and how to treat each: status.text says whether the text layer can be quoted
        (clean) or its math must come from page images (math-garbled, ocr). With `book` (a key such as
        "griffiths-qm"), that book's chapters and sections, i.e. what `where` can name."""
        return mnemo.books(book)

    @mcp.tool()
    def mnemosyne_book_search(query: str, book: str = "", limit: int = 10) -> dict:
        """Find where Aman's textbooks discuss something (full text). Each hit has the book, printed page, section,
        a snippet, and a citation. Leave `book` empty to search them all. Follow up with mnemosyne_book_read."""
        return mnemo.book_search(query, book, limit)

    @mcp.tool()
    def mnemosyne_book_read(book: str, where: str, images: str = "auto"):
        """Read from one of Aman's textbooks: `where` is a section ("2.3"), chapter ("ch 2"), or printed pages
        ("p 39-47"). Returns the printed text page by page with printed page numbers and a citation ("cite"). Page
        images come too when the book's text can't carry the math (images="auto"); "yes" or "no" to force."""
        return mnemo.with_images(mnemo.book_read(book, where, images))

    @mcp.tool()
    def mnemosyne_book_problems(book: str, where: str, difficulty: int = 0, limit: int = 8, offset: int = 0,
                                images: str = "auto"):
        """Practice problems from one of Aman's textbooks for a chapter ("ch 21") or section ("21.3"), each with its
        number, printed page, the book's difficulty mark (1-3, 0 when it has none), and a citation. `offset` pages
        through the rest. Page images come too when the text can't carry the math."""
        return mnemo.with_images(mnemo.book_problems(book, where, difficulty, limit, offset, images))

'''


def main() -> None:
    srv = STACK / "mcp" / "server.py"
    text = srv.read_text()
    if "mnemosyne_book_read" in text:
        sys.exit("already patched")
    if text.count(ANCHOR) != 1:
        sys.exit(f"abort: anchor found {text.count(ANCHOR)} times")
    new = text.replace(ANCHOR, TOOLS + ANCHOR)
    compile(new, "server.py", "exec")
    shutil.copy2(srv, srv.with_name("server.py.bak-mnemosyne-books"))
    shutil.copy2(STACK / "mcp" / "mnemo.py", STACK / "mcp" / "mnemo.py.bak-mnemosyne-books")
    srv.write_text(new)
    shutil.copy2(HOOKS / "mnemo.py", STACK / "mcp" / "mnemo.py")
    print("patched mcp/server.py (+4 mnemosyne_book* tools) and updated mcp/mnemo.py; now rebuild mcp and mcp-local")


if __name__ == "__main__":
    main()
