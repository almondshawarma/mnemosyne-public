"""Additive mnemosyne hooks for the Helios stack on the Obelisk. Backs up every file it touches to
<file>.bak-mnemosyne first and aborts before writing anything if an anchor isn't found exactly once."""
import shutil
import sys
from pathlib import Path

STACK = Path.home() / "helios" / "stack"
MARK = "mnemosyne"

TG_TOP_ANCHOR = 'INV_KINDS = {"person", "song", "artist", "album", "topic", "org", "other"}\n'
TG_TOP = '''
# ---- mnemosyne (Anki cards): announce draft batches, forward approval buttons ----
import urllib.error
MNEMOSYNE = os.environ.get("MNEMOSYNE_URL", "").rstrip("/")
MN_TOKEN = os.environ.get("MNEMOSYNE_APPROVE_TOKEN", "").strip()
MN_CHAT = os.environ.get("MNEMOSYNE_CHAT_ID", "").strip() or (sorted(ALLOWED)[0] if ALLOWED else "")
'''

TG_FUNCS_ANCHOR = "def handle_callback(cq):\n"
TG_FUNCS = '''def mn(method, path, body=None):
    req = urllib.request.Request(MNEMOSYNE + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {MN_TOKEN}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def mn_outbox_loop():
    """Every 30 s, announce new mnemosyne card batches with Approve / Reject buttons."""
    while True:
        try:
            for item in mn("GET", "/telegram/outbox"):
                r = tg("sendMessage", {"chat_id": MN_CHAT, "text": item["text"], "disable_web_page_preview": "true",
                                       "reply_markup": json.dumps({"inline_keyboard": item["keyboard"]})})
                mn("POST", "/telegram/sent", {"batch_id": item["batch_id"],
                                              "message_ref": str(r["result"]["message_id"])})
        except Exception as e:
            print("mnemosyne outbox error:", e, flush=True)
        time.sleep(30)


def mn_callback(cq, cid, uid, cbid, data):
    try:
        rep = mn("POST", "/telegram/callback", {"data": data, "by": f"telegram:{uid}"})
    except Exception as e:
        print("mnemosyne callback error:", e, flush=True)
        tg("answerCallbackQuery", {"callback_query_id": cbid, "text": "mnemosyne unreachable"})
        return
    tg("answerCallbackQuery", {"callback_query_id": cbid, "text": (rep.get("toast") or "")[:190]})
    if rep.get("text"):
        try:
            tg("editMessageText", {"chat_id": cid, "message_id": cq["message"]["message_id"], "text": rep["text"]})
        except Exception:
            pass
    for m in rep.get("messages") or []:
        tg("sendMessage", {"chat_id": cid, "text": m, "disable_web_page_preview": "true"})


def mn_propose_block(chat_id, text):
    """A pasted ```mnemosyne-cards block (the skill's fallback output) becomes a batch to approve."""
    m = re.search(r"```mnemosyne-cards\\s*(\\{.*\\})\\s*```", text, re.S)
    if not m:
        return False
    try:
        payload = json.loads(m.group(1))
    except ValueError as e:
        send(chat_id, f"⚠ That mnemosyne-cards block isn't valid JSON ({e}). Long blocks get split by "
                      "Telegram; send those through the viewer or Claude Code instead.")
        return True
    try:
        res = mn("POST", "/inbox", {"payload": payload, "origin": "telegram"})
    except urllib.error.HTTPError as e:
        send(chat_id, f"⚠ mnemosyne rejected it: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:800]}")
        return True
    except Exception as e:
        send(chat_id, f"⚠ mnemosyne unreachable: {e}")
        return True
    if res.get("already_proposed"):
        send(chat_id, f"🧠 Already proposed as batch {res.get('batch_id')} ({res.get('status')}).")
        return True
    lines = [f"🧠 Batch {res.get('batch_id')}: {res.get('created', 0)} new, {res.get('duplicates', 0)} duplicates, "
             f"{len(res.get('invalid') or [])} invalid."]
    for bad in (res.get("invalid") or [])[:10]:
        lines.append(f"• card {bad.get('index')}: {'; '.join(bad.get('problems') or [])}")
    if res.get("created"):
        lines.append("Approval buttons follow shortly.")
    send(chat_id, "\\n".join(lines))
    return True


'''

TG_CB_ANCHOR = '''    else:
        tg("answerCallbackQuery", {"callback_query_id": cbid})
'''
TG_CB = '''    elif parts and parts[0] == "mn" and MNEMOSYNE:
        mn_callback(cq, cid, uid, cbid, data)
'''

TG_HANDLE_ANCHOR = '''    if text in ("/start", "/help"):
'''
TG_HANDLE = '''    if MNEMOSYNE and "```mnemosyne-cards" in text and mn_propose_block(chat_id, text):
        return
'''

TG_MAIN_ANCHOR = '''    offset = None
    try:                                  # drain backlog so a restart doesn't re-answer old msgs
'''
TG_MAIN = '''    if MNEMOSYNE and MN_TOKEN and MN_CHAT:
        threading.Thread(target=mn_outbox_loop, daemon=True).start()
        print("mnemosyne approvals on", flush=True)
'''

SRV_ANCHOR = '''    mcp.run(transport="streamable-http")
'''
SRV = '''    # ---- mnemosyne: Anki cards (mnemo.py). Cloud-facing instances may only propose. ----
    import mnemo

    @mcp.tool()
    def mnemosyne_propose_cards(payload: dict) -> dict:
        """Propose Anki cards to mnemosyne. `payload` is the mnemosyne-cards JSON the
        mnemosyne-cards skill produces: {"source": {...}, "cards": [...]}. Cards become drafts that
        Aman approves in Telegram; nothing reaches Anki without approval. Re-proposing is a no-op.
        Returns the batch id and counts, plus any cards that failed validation (fix and re-propose)."""
        return mnemo.propose(payload)

    if mnemo.can_approve():
        @mcp.tool()
        def mnemosyne_pending_batches() -> dict:
            """Card batches waiting for Aman's approval."""
            return {"batches": mnemo.pending()}

        @mcp.tool()
        def mnemosyne_approve_batch(batch_id: str, approve: bool = True) -> dict:
            """Approve (or, with approve=false, reject) a pending card batch. Approved cards are
            written to Anki right away. Only when Aman explicitly asks."""
            return mnemo.approve(batch_id, approve)

'''

DOCKER_OLD = "COPY server.py influx.py paperless.py vault.py vault_write.py daily.py mail.py rag.py grocy.py jobs.py gcal.py tasks.py hyperion.py ./\n"
DOCKER_NEW = "COPY server.py influx.py paperless.py vault.py vault_write.py daily.py mail.py rag.py grocy.py jobs.py gcal.py tasks.py hyperion.py mnemo.py ./\n"

HYP = "      HYPERION_DB: /hyperion/hyperion.db\n"
TG_ENV = "      AGENT_URL: http://${HELIOS_HOST_IP}:8100\n"


def once(text, anchor, name):
    if text.count(anchor) != 1:
        sys.exit(f"abort: anchor for {name} found {text.count(anchor)} times")


def main():
    tg_path = STACK / "telegram" / "telegram_bot.py"
    srv_path = STACK / "mcp" / "server.py"
    dock_path = STACK / "mcp" / "Dockerfile"
    comp_path = STACK / "docker-compose.yml"
    tg_txt, srv_txt, dock_txt, comp_txt = (p.read_text() for p in (tg_path, srv_path, dock_path, comp_path))
    if MARK in tg_txt or "mnemo" in srv_txt:
        sys.exit("already patched")
    for text, anchor, name in [(tg_txt, TG_TOP_ANCHOR, "tg top"), (tg_txt, TG_FUNCS_ANCHOR, "tg funcs"),
                               (tg_txt, TG_CB_ANCHOR, "tg callback"), (tg_txt, TG_HANDLE_ANCHOR, "tg handle"),
                               (tg_txt, TG_MAIN_ANCHOR, "tg main"), (srv_txt, SRV_ANCHOR, "server"),
                               (dock_txt, DOCKER_OLD, "Dockerfile"), (comp_txt, TG_ENV, "compose telegram")]:
        once(text, anchor, name)
    if comp_txt.count(HYP) != 2 or "HELIOS_HIDDEN_TIERS" not in comp_txt.split(HYP)[1]:
        sys.exit("abort: expected HYPERION_DB in mcp then mcp-local")

    tg_txt = tg_txt.replace(TG_TOP_ANCHOR, TG_TOP_ANCHOR + TG_TOP)
    tg_txt = tg_txt.replace(TG_FUNCS_ANCHOR, TG_FUNCS + TG_FUNCS_ANCHOR)
    tg_txt = tg_txt.replace(TG_CB_ANCHOR, TG_CB + TG_CB_ANCHOR)
    tg_txt = tg_txt.replace(TG_HANDLE_ANCHOR, TG_HANDLE + TG_HANDLE_ANCHOR)
    tg_txt = tg_txt.replace(TG_MAIN_ANCHOR, TG_MAIN + TG_MAIN_ANCHOR)
    srv_txt = srv_txt.replace(SRV_ANCHOR, SRV + SRV_ANCHOR)
    dock_txt = dock_txt.replace(DOCKER_OLD, DOCKER_NEW)
    first, second, rest = comp_txt.split(HYP)
    comp_txt = (first + HYP + "      MNEMOSYNE_URL: http://mnemosyne:8110\n"
                "      MNEMOSYNE_PROPOSE_TOKEN: ${MNEMOSYNE_PROPOSE_TOKEN:-}\n"
                + second + HYP + "      MNEMOSYNE_URL: http://mnemosyne:8110\n"
                "      MNEMOSYNE_PROPOSE_TOKEN: ${MNEMOSYNE_PROPOSE_TOKEN:-}\n"
                "      MNEMOSYNE_APPROVE_TOKEN: ${MNEMOSYNE_APPROVE_TOKEN:-}\n" + rest)
    comp_txt = comp_txt.replace(TG_ENV, TG_ENV + "      MNEMOSYNE_URL: http://${HELIOS_HOST_IP}:8110\n"
                                "      MNEMOSYNE_APPROVE_TOKEN: ${MNEMOSYNE_APPROVE_TOKEN:-}\n")
    compile(tg_txt, "telegram_bot.py", "exec")
    compile(srv_txt, "server.py", "exec")

    for p, new in [(tg_path, tg_txt), (srv_path, srv_txt), (dock_path, dock_txt), (comp_path, comp_txt)]:
        shutil.copy2(p, p.with_name(p.name + ".bak-mnemosyne"))
        p.write_text(new)
    shutil.copy2(Path.home() / "helios" / "mnemosyne-hooks" / "mnemo.py", STACK / "mcp" / "mnemo.py")
    print("patched: telegram_bot.py, mcp/server.py, mcp/Dockerfile, docker-compose.yml; added mcp/mnemo.py")


if __name__ == "__main__":
    main()
