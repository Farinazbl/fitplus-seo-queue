#!/usr/bin/env python3
"""FitPlus comment -> DM bot (Composio). Runs from cron every 10 minutes."""
import json, os, re, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
def load_env():
    for line in open(os.path.join(BASE, ".env"), encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
load_env()
API_KEY = os.environ["COMPOSIO_API_KEY"]
ACCOUNT = os.environ["COMPOSIO_CONNECTED_ACCOUNT_ID"]
USER_ID = os.environ.get("COMPOSIO_USER_ID", "")
OWN = "fitplus.app"
CUTOFF = datetime(2026, 11, 17, 8, 4, tzinfo=timezone.utc)
STATE = os.path.join(BASE, "done.json")
MAX_PER_RUN = 20

TEXT_A = """برنامه ۷ روزه شکم 💜 فیت‌پلاس
قانون: کسری کالری کم و ثابت + روزی ۸ هزار قدم + پروتئین هر وعده
۱) پیاده‌روی ۳۰ دقیقه + پلانک ۳×۲۰ثانیه
۲) اسکوات ۳×۱۲ + لانج ۳×۸
۳) فقط ۸ هزار قدم + کشش
۴) پلانک ۳×۳۰ + کرانچ ۳×۱۲ + پل باسن ۳×۱۲
۵) پیاده‌روی تند ۴۰ دقیقه
۶) تکرار روز ۲ + پلانک کناری ۳×۲۰
۷) استراحت و خواب کافی
روزی ۸ لیوان آب؛ نوشابه و شیرینی کم
⚠️ بیماری یا درد داری؟ اول با پزشک مشورت کن
برنامه شخصی‌ات تو اپ فیت‌پلاس 📲"""
TEXT_B = """لیست ۷ صبحونه‌ی پروتئینی 🥚 فیت‌پلاس
۱) نیمرو + نان سبوس‌دار
۲) تخم‌مرغ آب‌پز + آووکادو
۳) املت سبزی
۴) ماست + تخم‌مرغ آب‌پز + خیار
۵) تخم‌مرغ و اسفناج
۶) ساندویچ تخم‌مرغ با نان سبوس‌دار
۷) پنکیک جو دوسر + تخم‌مرغ
نکته: برای اغلب آدم‌های سالم روزی ۱ تا ۲ تا مناسبه؛ بیماری قلبی یا کلسترول بالا داری؟ اول با پزشک مشورت کن
برنامه‌ی ورزشی و تغذیه‌ی شخصی‌ات تو اپ فیت‌پلاس 📲"""

def post(path, body):
    req = urllib.request.Request("https://backend.composio.dev/api/v3" + path,
        data=json.dumps(body).encode(), method="POST",
        headers={"x-api-key": API_KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"successful": False, "error": f"HTTP {e.code}: {e.read().decode()[:300]}"}
    except Exception as e:
        return {"successful": False, "error": str(e)}

def tool(slug, args):
    body = {"connected_account_id": ACCOUNT, "arguments": args}
    if USER_ID: body["user_id"] = USER_ID
    return post(f"/tools/execute/{slug}", body)

def keyword(text):
    t = text.replace("ي", "ی").replace("ك", "ک")
    t = re.sub(r"[^\w]|_", "", t)            # drops spaces, ZWNJ, emoji, punctuation
    if t in ("شکم", "شک"): return TEXT_A
    if t in ("تخممرغ", "تخمرغ"): return TEXT_B
    return None

def parse_ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")

def main():
    done = set(json.load(open(STATE))) if os.path.exists(STATE) else set()
    media = tool("INSTAGRAM_GET_IG_USER_MEDIA", {"ig_user_id": "me", "limit": 6,
             "fields": "id,media_type,media_product_type,timestamp"})
    items = (media.get("data") or {}).get("data") or (media.get("data") or {}).get("response_data", {}).get("data") or []
    todo = []
    for m in items:
        if m.get("media_type") != "VIDEO": continue
        c = tool("INSTAGRAM_GET_IG_MEDIA_COMMENTS", {"ig_media_id": m["id"], "limit": 100,
                 "fields": "id,text,username,timestamp,parent_id,replies{username}"})
        d = c.get("data") or {}
        for cm in d.get("data") or d.get("response_data", {}).get("data") or []:
            if cm.get("parent_id") or cm.get("username") == OWN: continue
            if cm["id"] in done: continue
            try:
                if parse_ts(cm["timestamp"]) <= CUTOFF: continue
            except Exception: continue
            if any(r.get("username") == OWN for r in (cm.get("replies") or {}).get("data", [])): continue
            txt = keyword(cm.get("text", ""))
            if txt: todo.append((cm["timestamp"], cm, txt))
    todo.sort(key=lambda x: x[0])
    for _, cm, txt in todo[:MAX_PER_RUN]:
        done.add(cm["id"])                      # never retry the same comment
        json.dump(sorted(done), open(STATE, "w"))
        r = post("/tools/execute/proxy", {"connected_account_id": ACCOUNT,
                 "endpoint": "https://graph.instagram.com/v21.0/me/messages", "method": "POST",
                 "body": {"recipient": {"comment_id": cm["id"]}, "message": {"text": txt}}})
        ok = r.get("successful") is not False and (r.get("data") or {}).get("status", 200) in (200, 201)
        print(datetime.now().isoformat(), cm["id"], cm.get("username"), "DM", "ok" if ok else f"FAIL {r}")
        if ok:
            rr = tool("INSTAGRAM_POST_IG_COMMENT_REPLIES", {"ig_comment_id": cm["id"],
                      "message": f"@{cm.get('username')} ارسال شد💪🏻"})
            print("  public reply", "ok" if rr.get("successful") else f"FAIL {rr.get('error')}")
        time.sleep(2)

if __name__ == "__main__":
    main()
