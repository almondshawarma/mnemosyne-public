#!/usr/bin/env python3
"""Find a past Claude Code session and turn it into a readable trace.

Claude Code keeps every session as a JSONL transcript under ~/.claude/projects/<project-slug>/<session>.jsonl
(or $CLAUDE_CONFIG_DIR/projects). This script searches them for a phrase and dumps one as plain text:
what Aman asked, what Claude said, every tool call it made, and a truncated copy of each result. That trace
is the raw material for an unpacking: the reasoning is in the sequence of checks, not in the final summary.

    python find_episode.py search "billion bytes"                 # which sessions mention it
    python find_episode.py search "obsidian" --since 2026-10-01   # narrow by date
    python find_episode.py dump <path-to.jsonl> --out trace.txt   # the readable trace
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def projects_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return Path(base) / "projects"


def rows(path: Path):
    """Yield parsed JSONL rows, skipping any line that isn't valid JSON (a session still being written)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def blocks(row):
    """Normalize a row's message content to a list of content blocks."""
    content = row.get("message", {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content if isinstance(content, list) else []


def result_text(block) -> str:
    c = block.get("content")
    if isinstance(c, list):
        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return str(c or "")


def first_user_text(path: Path) -> str:
    for r in rows(path):
        if r.get("type") != "user":
            continue
        for b in blocks(r):
            if b.get("type") == "text":
                t = REMINDER.sub("", b["text"]).strip()
                if t:
                    return t
    return ""


def search(args):
    needle = args.phrase.lower()
    hits = []
    for f in projects_dir().glob("*/*.jsonl"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        n = text.lower().count(needle)
        if not n:
            continue
        stamps = re.findall(r'"timestamp":"([^"]+)"', text)
        start, end = (stamps[0], stamps[-1]) if stamps else ("?", "?")
        if args.since and end < args.since:
            continue
        hits.append((end, n, start, f))
    for end, n, start, f in sorted(hits, reverse=True)[: args.limit]:
        print(f"{start[:16]} -> {end[:16]}  hits={n:<4} {f.parent.name}")
        print(f"    {f}")
        print(f"    first ask: {first_user_text(f)[:200]!r}")
    if not hits:
        print("no session mentions that phrase")


def dump(args):
    if not args.out:
        write_trace(args, sys.stdout)
        return
    with open(args.out, "w", encoding="utf-8") as out:
        write_trace(args, out)
    print(f"wrote {args.out}")


def write_trace(args, out):
    w = args.max_result
    for r in rows(Path(args.path)):
        kind = r.get("type")
        if kind not in ("user", "assistant"):
            continue
        for b in blocks(r):
            t = b.get("type")
            if t == "text":
                text = REMINDER.sub("", b["text"]).strip()
                if text:
                    print(f"\n[{kind.upper()}] {text}", file=out)
            elif t == "tool_use":
                print(f"\n[TOOL {b.get('name')}] {json.dumps(b.get('input'), ensure_ascii=False)[:w]}", file=out)
            elif t == "tool_result":
                print(f"[RESULT] {result_text(b)[:w]}", file=out)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="find sessions that mention a phrase")
    s.add_argument("phrase")
    s.add_argument("--since", help="ISO date, e.g. 2026-10-01")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(func=search)
    d = sub.add_parser("dump", help="print one session as a readable trace")
    d.add_argument("path")
    d.add_argument("--out", help="write here instead of stdout (traces get long)")
    d.add_argument("--max-result", type=int, default=1500, help="truncate each tool input/result to N chars")
    d.set_defaults(func=dump)
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
