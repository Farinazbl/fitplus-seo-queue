"""Approval inside Bale: the bot sends every pending post to the approver(s) with ✅/❌ buttons.

Approving enqueues the normal publish job (same gate as the web panel). Only user ids listed in
``APPROVER_IDS`` (comma separated Bale user ids) may decide; everyone else's presses are refused.
The same BALE_TOKEN bot is used. Do not run another consumer of getUpdates on this bot (e.g. an old n8n flow).
"""
import json
import logging
import os

from . import http, orchestrator, repo

log = logging.getLogger("approver")
API = "https://tapi.bale.ai/bot{token}/{method}"
PREFIX = "fp"


def approver_ids() -> list:
    return [x.strip() for x in os.environ.get("APPROVER_IDS", "").split(",") if x.strip()]


def _init(db):
    db.execute("CREATE TABLE IF NOT EXISTS approver_notices (approval_id INTEGER, chat_id TEXT, "
               "PRIMARY KEY (approval_id, chat_id))")
    db.execute("CREATE TABLE IF NOT EXISTS approver_state (k TEXT PRIMARY KEY, v TEXT)")


def _call(token, method, body, transport):
    raw = transport("POST", API.format(token=token, method=method), json_body=body, timeout=30)
    data = json.loads(raw)
    if not data.get("ok"):
        raise http.HttpError(400, raw)
    return data["result"]


def _keyboard(aid):
    return {"inline_keyboard": [[{"text": "✅ تأیید و انتشار", "callback_data": f"{PREFIX}:a:{aid}"},
                                 {"text": "❌ رد", "callback_data": f"{PREFIX}:r:{aid}"}]]}


def _label(db, content):
    cfg = repo.get_agent(db, content["agent_key"]) or {}
    ch = repo.get_channel(db, cfg.get("channel_id")) if cfg else None
    dest = ((ch or {}).get("config") or {}).get("chat_id", "")
    return cfg.get("title", content["agent_key"]), dest


def notify_pending(db, transport=http.request, multipart=http.post_multipart) -> int:
    token = os.environ.get("BALE_TOKEN")
    ids = approver_ids()
    if not token or not ids:
        return 0
    _init(db)
    sent = 0
    for ap in repo.list_approvals(db, "pending"):
        content = repo.get_content(db, ap["content_uid"])
        if not content:
            continue
        title, dest = _label(db, content)
        head = f"📝 پیش‌نویس جدید برای انتشار در {dest or 'کانال'}\n«{title}»"
        if ap.get("reasons"):
            head += "\n⚠️ نیازمند بررسی: " + "، ".join(ap["reasons"] if isinstance(ap["reasons"], list) else [str(ap["reasons"])])
        text = f"{head}\n\n{content['body']}"
        kb = json.dumps(_keyboard(ap["id"]))
        img = (content.get("meta") or {}).get("image_path")
        for chat in ids:
            if db.one("SELECT 1 FROM approver_notices WHERE approval_id=? AND chat_id=?", (ap["id"], chat)):
                continue
            try:
                if img and os.path.isfile(img):
                    # the photo goes alone; the full text + buttons follow (captions are limited to 1024 chars)
                    multipart(API.format(token=token, method="sendPhoto"), {"chat_id": chat}, {"photo": ("post.png", open(img, "rb").read(), "image/png")})
                _call(token, "sendMessage", {"chat_id": chat, "text": text[:4000], "reply_markup": json.loads(kb)}, transport)
            except (http.HttpError, ValueError, KeyError) as e:
                log.warning("approver notify failed for %s: %s", ap["id"], e)
                continue
            db.execute("INSERT OR IGNORE INTO approver_notices (approval_id, chat_id) VALUES (?,?)", (ap["id"], chat))
            sent += 1
    return sent


def _answer(token, transport, cq_id, text):
    try:
        _call(token, "answerCallbackQuery", {"callback_query_id": cq_id, "text": text}, transport)
    except (http.HttpError, ValueError, KeyError):
        pass


def poll(db, transport=http.request) -> int:
    """Handle button presses (and /start, which tells a person their user id for APPROVER_IDS)."""
    token = os.environ.get("BALE_TOKEN")
    if not token:
        return 0
    _init(db)
    row = db.one("SELECT v FROM approver_state WHERE k='offset'")
    offset = int(row["v"]) if row else 0
    try:
        updates = _call(token, "getUpdates", {"offset": offset, "timeout": 0}, transport)
    except (http.HttpError, ValueError, KeyError) as e:
        log.warning("getUpdates failed: %s", e)
        return 0
    handled = 0
    allowed = approver_ids()
    for u in updates:
        offset = max(offset, u["update_id"] + 1)
        cq = u.get("callback_query")
        msg = u.get("message")
        if cq and str(cq.get("data", "")).startswith(PREFIX + ":"):
            who = str(cq["from"]["id"])
            if who not in allowed:
                _answer(token, transport, cq["id"], "شما اجازه‌ی تأیید ندارید")
                continue
            try:
                _, act, aid = cq["data"].split(":")
                orchestrator.decide_approval(db, int(aid), "approved" if act == "a" else "rejected", f"bale:{who}")
                _answer(token, transport, cq["id"], "✅ تأیید شد؛ در حال انتشار" if act == "a" else "❌ رد شد")
                chat = (cq.get("message") or {}).get("chat", {}).get("id")
                if chat:
                    _call(token, "sendMessage", {"chat_id": chat, "text": "✅ تأیید شد و در صف انتشار قرار گرفت." if act == "a" else "❌ این پیش‌نویس رد شد."}, transport)
                handled += 1
            except (ValueError, KeyError) as e:
                _answer(token, transport, cq["id"], "این مورد قبلاً تصمیم‌گیری شده یا پیدا نشد")
                log.info("approval press ignored: %s", e)
        elif msg and str(msg.get("text", "")).startswith("/start"):
            log.info("start from user id=%s name=%s", msg["from"].get("id"), msg["from"].get("first_name", ""))
            try:
                _call(token, "sendMessage", {"chat_id": msg["chat"]["id"], "text": f"شناسه‌ی کاربری شما: {msg['from']['id']}"}, transport)
            except (http.HttpError, ValueError, KeyError):
                pass
    db.execute("INSERT INTO approver_state (k, v) VALUES ('offset', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
               (str(offset),))
    return handled
