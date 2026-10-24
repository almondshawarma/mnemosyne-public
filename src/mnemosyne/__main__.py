"""mnemosyne serve | migrate | propose FILE [--origin ORIGIN] [--url URL]"""

import argparse
import json
import logging
import sys
from pathlib import Path

import httpx
import structlog
import uvicorn

from mnemosyne.config import Settings, read_secret
from mnemosyne.ledger import db


def main() -> None:
    structlog.configure(processors=[structlog.processors.TimeStamper(fmt="iso"),
                                    structlog.processors.add_log_level, structlog.processors.JSONRenderer()],
                        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))
    p = argparse.ArgumentParser(prog="mnemosyne", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8110)
    sub.add_parser("migrate")
    prop = sub.add_parser("propose", help="send a payload file (e.g. drafts/<batch>.json) to a running service")
    prop.add_argument("file")
    prop.add_argument("--origin", default="inbox")
    prop.add_argument("--url", default="http://localhost:8110")
    a = p.parse_args()
    settings = Settings()
    if a.cmd == "migrate":
        print(json.dumps({"applied": db.migrate(db.connect(settings.ledger))}))
    elif a.cmd == "serve":
        from mnemosyne.api import create_app

        uvicorn.run(create_app(settings), host=a.host, port=a.port, log_level="info")
    else:
        token = read_secret(settings.approve_token_file) or read_secret(settings.propose_token_file)
        if token is None:
            sys.exit("no token file configured (MNEMOSYNE_APPROVE_TOKEN_FILE or MNEMOSYNE_PROPOSE_TOKEN_FILE)")
        doc = json.loads(Path(a.file).read_text(encoding="utf-8"))
        payload = {"source": doc["source"], "cards": doc["cards"]}
        r = httpx.post(f"{a.url}/inbox", json={"payload": payload, "origin": a.origin},
                       headers={"Authorization": f"Bearer {token}"}, timeout=60)
        print(r.text)
        r.raise_for_status()


if __name__ == "__main__":
    main()
