"""Tag every book in Calibre with how an AI should treat it: run on the Obelisk from ~/helios/mnemosyne.

    python3 scripts/calibre_tags.py            # dry run: what would change
    python3 scripts/calibre_tags.py --apply    # write it through calibredb, as Calibre's own user

The api judges each PDF (mnemosyne.library.status) and books.yaml says which are mnemosyne's. Only tags starting
`ai:` or `mnemosyne:` are ever added or removed; Aman's own tags stay as they are. Re-running is a no-op until a PDF
or books.yaml changes, so run it again after adding a book.

  ai:born-digital | ai:scanned                       how the PDF was made
  ai:text-clean | ai:text-math-garbled | ai:text-ocr | ai:text-none
                                                     whether its text layer can be quoted as is
  ai:read-text | ai:read-images-for-math | ai:read-images
                                                     what to read: the text, or page images for the math (or all)
  ai:outline-sections | ai:outline-chapters | ai:outline-unnumbered | ai:outline-none
                                                     what a reading can name: "2.3", "ch 2", or only pages
  mnemosyne:<key>                                    in config/books.yaml under <key>: the mnemosyne_book_* tools
                                                     read, search, and pull problems from it
"""
import json
import subprocess
import sys

CALIBRE = "helios-calibre-web-1"
LIBRARY = "/calibre-library"
OURS = ("ai:", "mnemosyne:")


def run(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def main() -> None:
    apply = "--apply" in sys.argv[1:]
    listed = json.loads(run("docker", "exec", CALIBRE, "calibredb", "list", "--with-library", LIBRARY,
                            "--for-machine", "-f", "title,tags"))
    current = {int(b["id"]): (b["title"], list(b.get("tags") or [])) for b in listed}
    wanted = {int(k): v for k, v in json.loads(run("docker", "compose", "exec", "-T", "api",
                                                   "python", "-m", "mnemosyne.library", "tags")).items()}
    changed = 0
    for cid, (title, tags) in sorted(current.items()):
        keep = [t for t in tags if not t.startswith(OURS)]
        new = keep + wanted.get(cid, [])
        if sorted(new) == sorted(tags):
            continue
        changed += 1
        added = sorted(set(new) - set(tags))
        removed = sorted(set(tags) - set(new))
        print(f"{cid:>4} {title[:50]:<50} +{added} -{removed}")
        if apply:
            run("docker", "exec", "-u", "abc", CALIBRE, "calibredb", "set_metadata", "--with-library", LIBRARY,
                str(cid), "--field", "tags:" + ",".join(new))
    missing = sorted(set(wanted) - set(current))
    if missing:
        print(f"PDFs Calibre doesn't list (skipped): {missing}")
    print(f"{changed} book(s) {'updated' if apply else 'would change; run with --apply to write'}")


if __name__ == "__main__":
    main()
