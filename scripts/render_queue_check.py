"""Tells Aman on Telegram when the render queue (media/render-queue, drained by scripts/render_runner.py) stops
draining. Runs on the Obelisk every 5 minutes from his crontab, under the same bare python3 as the runner:

    */5 * * * * /usr/bin/python3 ~/helios/mnemosyne/scripts/render_queue_check.py >> ... # mnemosyne-render-check

Three ways the queue has stalled, or can:
- the runner dies before it reaches the queue: its log ends in a traceback (2026-10-28: an import it couldn't load,
  and five chat clips waited with nothing said);
- a clip has waited `WAIT_MIN` minutes with nothing rendering;
- a render has run past the runner's own timeout, so the run that held it is gone or stuck.

One message when it starts and one when it clears, not one every 5 minutes: the last state is kept in STATE.
Standard library only. Credentials come from the stack's .env, as the morning digest reads them.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "media" / "render-queue"
LOG = QUEUE / "runner.log"
ENV = Path.home() / "helios" / "stack" / ".env"
STATE = Path.home() / ".local" / "state" / "mnemosyne" / "render-check.json"
WAIT_MIN = 30          # minutes a clip may wait with nothing rendering
WORKING_MIN = 20       # the runner's TIMEOUT is 15 minutes per render


def started(working: Path) -> float:
    """When the runner took the clip. The rename to .working keeps the request's mtime (when it was submitted) but
    sets its ctime."""
    return working.stat().st_ctime


def problems(queue: Path, log: Path, now: float) -> list[str]:
    """What's wrong with the queue right now, in words for the message; empty when it's draining."""
    found: list[str] = []
    waiting = sorted(queue.glob("*.json"), key=lambda p: p.stat().st_mtime)
    working = list(queue.glob("*.working"))
    # a traceback stays at the log's end until the next render logs, so it counts only while nothing renders
    if waiting and not working and log.is_file() and now - log.stat().st_mtime < 600:
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
        start = next((i for i in range(len(tail) - 1, -1, -1) if tail[i].startswith("Traceback")), None)
        if start is not None:
            found.append("the runner crashes before it renders anything:\n" + "\n".join(tail[start:][-4:]))
    if waiting and not working:
        oldest = (now - waiting[0].stat().st_mtime) / 60
        if oldest >= WAIT_MIN:
            found.append(f"{len(waiting)} clip(s) waiting, the oldest ({waiting[0].stem}) for {oldest:.0f} min, "
                         "and nothing is rendering")
    for w in working:
        took = (now - started(w)) / 60
        if took >= WORKING_MIN:
            found.append(f"{w.stem} has been rendering for {took:.0f} min, past the runner's timeout")
    return found


def load_env(path: Path) -> None:
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    ids = [x.strip() for x in os.environ.get("TELEGRAM_ALLOWED_IDS", "").split(",") if x.strip()]
    if not token or not ids:
        raise SystemExit("render_queue_check: TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_IDS not set")
    for chat in ids:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
        urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data),
                               timeout=30)


def main() -> int:
    now = time.time()
    found = problems(QUEUE, LOG, now) if QUEUE.is_dir() else []
    try:
        was = bool(json.loads(STATE.read_text(encoding="utf-8")).get("alerting"))
    except (OSError, ValueError):
        was = False
    if found and not was:
        load_env(ENV)
        telegram("⚠️ mnemosyne clip renders are stuck\n\n" + "\n\n".join(found)
                 + f"\n\nLog: {LOG}")
    elif was and not found:
        load_env(ENV)
        telegram("✅ mnemosyne clip renders are moving again")
    if bool(found) != was:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"alerting": bool(found), "since": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                                   time.gmtime(now))}),
                         encoding="utf-8")
        print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {'alert' if found else 'clear'}: {found}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
