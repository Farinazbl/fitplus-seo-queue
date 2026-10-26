"""Campaign manager: contacts, events, automatic SMS scenarios (RFP section 4).

Scenarios (all times relative to the event, adjusted into the allowed sending window):

    signup (no purchase yet)  -> day 1, 3, 9 after sign-up, only if still no purchase
    active subscription       -> 3 days before it ends
    subscription ended        -> on the end day, +3 and +7 days, only if not renewed

Stop rule: a purchase cancels the sign-up messages; a renewal cancels reminder and
expiry messages of the previous period. Every condition is re-checked at send time
as well, so a purchase that happens between scheduling and sending still wins.

Safety: consent is required and opt-out is final; a (contact, step) pair can exist
only once; messages are only sent inside the quiet-hours window (default 09-21
Tehran); messages older than ``MAX_LATE_HOURS`` are cancelled instead of being sent
late; a crash during a provider call leaves the message ``unknown`` (never re-sent
automatically) so nobody receives a duplicate.
"""
import json
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from . import repo
from .connectors.base import PermanentError, TransientError
from .util import iso, new_uid, parse, utcnow

MAX_LATE_HOURS = 24
MAX_ATTEMPTS = 3
DEFAULT_QUIET = (9, 21)
DEFAULT_TEMPLATES = {
    "signup_1": "{name} عزیز، ثبت‌نام شما انجام شد. برای شروع، اشتراک خود را فعال کنید.",
    "signup_3": "{name} عزیز، هنوز اشتراک شما فعال نشده است. برای شروع تمرین‌ها اینجا را ببینید.",
    "signup_9": "{name} عزیز، فرصت استفاده از امکانات برنامه را از دست ندهید.",
    "sub_reminder": "{name} عزیز، ۳ روز تا پایان اشتراک شما مانده است. برای تمدید اقدام کنید.",
    "expired_0": "{name} عزیز، اشتراک شما امروز به پایان رسید. برای ادامه، آن را تمدید کنید.",
    "expired_3": "{name} عزیز، اشتراک شما تمدید نشده است. هر زمان خواستید ادامه دهید.",
    "expired_7": "{name} عزیز، دلتنگ تمرین‌های شما هستیم. برای بازگشت اشتراک را تمدید کنید.",
}
EVENT_KINDS = {"signup", "purchase", "subscription_start", "renewal"}


# ---------- contacts ----------
def upsert_contact(db, external_id: str, phone: Optional[str] = None, name: str = "",
                   consent: Optional[bool] = None) -> dict:
    row = db.one("SELECT * FROM contacts WHERE external_id=?", (external_id,))
    now = iso(utcnow())
    if row is None:
        cur = db.execute("INSERT INTO contacts (external_id,phone,name,consent,consent_at,created_at) VALUES (?,?,?,?,?,?)",
                         (external_id, phone, name, int(bool(consent)), now if consent else None, now))
        return db.one("SELECT * FROM contacts WHERE id=?", (cur.lastrowid,))
    if phone is not None:
        db.execute("UPDATE contacts SET phone=? WHERE id=?", (phone, row["id"]))
    if name:
        db.execute("UPDATE contacts SET name=? WHERE id=?", (name, row["id"]))
    if consent is not None and not row["opted_out"]:  # opt-out is final: only a new explicit sign-up flow can reset it
        db.execute("UPDATE contacts SET consent=?, consent_at=? WHERE id=?", (int(consent), now if consent else None, row["id"]))
    return db.one("SELECT * FROM contacts WHERE id=?", (row["id"],))


def opt_out(db, external_id: str) -> bool:
    row = db.one("SELECT id FROM contacts WHERE external_id=?", (external_id,))
    if not row:
        return False
    db.execute("UPDATE contacts SET opted_out=1, consent=0 WHERE id=?", (row["id"],))
    db.execute("UPDATE sms_messages SET status='cancelled', reason='لغو عضویت توسط کاربر' "
               "WHERE contact_id=? AND status='queued'", (row["id"],))
    return True


def erase_contact(db, external_id: str) -> bool:
    """Right to deletion: removes the contact, its events and messages."""
    row = db.one("SELECT id FROM contacts WHERE external_id=?", (external_id,))
    if not row:
        return False
    for table in ("sms_messages", "contact_events"):
        db.execute(f"DELETE FROM {table} WHERE contact_id=?", (row["id"],))
    db.execute("DELETE FROM contacts WHERE id=?", (row["id"],))
    return True


def mask(phone: Optional[str]) -> str:
    return ("*" * max(len(phone) - 4, 0) + phone[-4:]) if phone else ""


# ---------- scheduling ----------
def next_window(dt, tz: ZoneInfo, quiet=DEFAULT_QUIET):
    """Move ``dt`` into the allowed sending window (local hours ``[start, end)``)."""
    start, end = quiet
    local = dt.astimezone(tz)
    if start <= local.hour < end:
        return dt
    day = local.date() if local.hour < start else (local + timedelta(days=1)).date()
    return datetime(day.year, day.month, day.day, start, 0, tzinfo=tz).astimezone(dt.tzinfo)


def _schedule(db, contact_id, scenario, step_key, send_at, now=None):
    try:
        db.execute("INSERT INTO sms_messages (uid,contact_id,scenario,step_key,send_at,created_at) VALUES (?,?,?,?,?,?)",
                   (new_uid("sms"), contact_id, scenario, step_key, iso(send_at), iso(now or utcnow())))
        return True
    except Exception as e:  # noqa: BLE001 - UNIQUE(contact_id, step_key) => already scheduled
        if "UNIQUE" in str(e):
            return False
        raise


def _cancel(db, contact_id, scenarios, reason):
    marks = ",".join("?" * len(scenarios))
    db.execute(f"UPDATE sms_messages SET status='cancelled', reason=? WHERE contact_id=? AND status='queued' "
               f"AND scenario IN ({marks})", (reason, contact_id, *scenarios))


def record_event(db, external_id: str, kind: str, at: Optional[datetime] = None,
                 end_at: Optional[datetime] = None) -> dict:
    if kind not in EVENT_KINDS:
        raise ValueError(f"نوع رویداد نامعتبر: {kind}")
    if kind in ("subscription_start", "renewal") and end_at is None:
        raise ValueError("برای اشتراک و تمدید، end_at لازم است")
    contact = upsert_contact(db, external_id)
    at = at or utcnow()
    db.execute("INSERT INTO contact_events (contact_id,kind,at,end_at) VALUES (?,?,?,?)",
               (contact["id"], kind, iso(at), iso(end_at) if end_at else None))
    cid = contact["id"]
    if kind == "signup":
        if not _has(db, cid, "purchase"):
            for d in (1, 3, 9):
                _schedule(db, cid, f"signup_{d}", f"signup:{d}", at + timedelta(days=d))
    elif kind == "purchase":
        _cancel(db, cid, ["signup_1", "signup_3", "signup_9"], "خرید انجام شد")
    else:  # subscription_start / renewal
        _cancel(db, cid, ["signup_1", "signup_3", "signup_9"], "خرید/اشتراک انجام شد")
        if kind == "renewal":
            _cancel(db, cid, ["sub_reminder", "expired_0", "expired_3", "expired_7"], "اشتراک تمدید شد")
        tag = end_at.strftime("%Y%m%d")
        _schedule(db, cid, "sub_reminder", f"remind:{tag}", end_at - timedelta(days=3))
        for d in (0, 3, 7):
            _schedule(db, cid, f"expired_{d}", f"expired:{tag}:{d}", end_at + timedelta(days=d))
    return contact


def _has(db, contact_id, kind) -> bool:
    return db.one("SELECT 1 AS x FROM contact_events WHERE contact_id=? AND kind=?", (contact_id, kind)) is not None


def latest_end(db, contact_id):
    row = db.one("SELECT MAX(end_at) AS e FROM contact_events WHERE contact_id=? AND end_at IS NOT NULL", (contact_id,))
    return parse(row["e"]) if row and row["e"] else None


# ---------- manual campaigns ----------
def segment_contacts(db, segment: dict, now=None):
    now = now or utcnow()
    rows = db.all("SELECT * FROM contacts WHERE consent=1 AND opted_out=0 AND phone IS NOT NULL ORDER BY id")
    kind = segment.get("type", "all")
    out = []
    for c in rows:
        end = latest_end(db, c["id"])
        ok = {"all": True,
              "signup_no_purchase": _has(db, c["id"], "signup") and not _has(db, c["id"], "purchase") and end is None,
              "active_subscription": end is not None and end > now,
              "expired": end is not None and end <= now}.get(kind)
        if ok is None:
            raise ValueError(f"سگمنت نامعتبر: {kind}")
        if ok:
            out.append(c)
    return out


def create_manual(db, name: str, segment: dict, text: str, send_at: datetime, max_recipients: int,
                  actor: str, now=None) -> dict:
    if not text.strip() or max_recipients < 1:
        raise ValueError("متن و سقف تعداد الزامی است")
    targets = segment_contacts(db, segment, now)[:max_recipients]
    cur = db.execute("INSERT INTO campaigns (name,segment,text,send_at,max_recipients,created_by,created_at) VALUES (?,?,?,?,?,?,?)",
                     (name, json.dumps(segment), text, iso(send_at), max_recipients, actor, iso(utcnow())))
    for c in targets:
        try:
            db.execute("INSERT INTO sms_messages (uid,contact_id,scenario,step_key,campaign_id,text,send_at,created_at) "
                       "VALUES (?,?,?,?,?,?,?,?)",
                       (new_uid("sms"), c["id"], "manual", f"manual:{cur.lastrowid}", cur.lastrowid, text,
                        iso(send_at), iso(utcnow())))
        except Exception as e:  # noqa: BLE001
            if "UNIQUE" not in str(e):
                raise
    repo.audit(db, actor, "campaign.create", "campaign", cur.lastrowid, {"name": name, "recipients": len(targets)})
    return {"id": cur.lastrowid, "recipients": len(targets)}


# ---------- dispatch ----------
def _render(msg, contact, templates):
    if msg["text"]:
        return msg["text"]
    tpl = templates.get(msg["scenario"]) or DEFAULT_TEMPLATES[msg["scenario"]]
    return tpl.replace("{name}", contact["name"] or "کاربر")


def _still_valid(db, msg, contact, now) -> Optional[str]:
    """Return a cancellation reason, or ``None`` if the message may be sent."""
    if contact["opted_out"] or not contact["consent"]:
        return "بدون رضایت دریافت پیام"
    if not contact["phone"]:
        return "شماره تلفن ندارد"
    sc = msg["scenario"]
    if sc.startswith("signup_") and (_has(db, contact["id"], "purchase") or latest_end(db, contact["id"])):
        return "خرید انجام شده است"
    if sc in ("sub_reminder", "expired_0", "expired_3", "expired_7"):
        end = datetime.strptime(msg["step_key"].split(":")[1], "%Y%m%d").replace(tzinfo=now.tzinfo)
        latest = latest_end(db, contact["id"])
        if latest and latest.date() > end.date():
            return "اشتراک تمدید شده است"
    return None


def dispatch(db, provider, cfg: Optional[dict] = None, now=None, tz_name="Asia/Tehran", batch=200) -> dict:
    now = now or utcnow()
    tz = ZoneInfo(tz_name)
    policy = (cfg or {}).get("policy", {})
    quiet = tuple(policy.get("quiet_hours", DEFAULT_QUIET))
    templates = policy.get("templates", {})
    daily_cap = int(policy.get("daily_cap", 5000))
    sent_today = db.one("SELECT COUNT(*) AS n FROM sms_messages WHERE status='sent' AND sent_at >= ?",
                        (iso(now - timedelta(hours=24)),))["n"]
    stats = {"sent": 0, "cancelled": 0, "deferred": 0, "failed": 0, "retry": 0}
    rows = db.all("SELECT * FROM sms_messages WHERE status='queued' AND send_at <= ? ORDER BY send_at, id LIMIT ?",
                  (iso(now), batch))
    for msg in rows:
        contact = db.one("SELECT * FROM contacts WHERE id=?", (msg["contact_id"],))
        reason = _still_valid(db, msg, contact, now)
        if reason is None and now - parse(msg["send_at"]) > timedelta(hours=MAX_LATE_HOURS):
            reason = "زمان مناسب ارسال گذشته است (کهنه)"
        if reason:
            db.execute("UPDATE sms_messages SET status='cancelled', reason=? WHERE id=?", (reason, msg["id"]))
            stats["cancelled"] += 1
            continue
        window = next_window(now, tz, quiet)
        if window != now:
            db.execute("UPDATE sms_messages SET send_at=? WHERE id=?", (iso(window), msg["id"]))
            stats["deferred"] += 1
            continue
        if sent_today >= daily_cap:
            stats["deferred"] += 1
            continue
        claimed = db.execute("UPDATE sms_messages SET status='sending', attempts=attempts+1, sent_at=? "
                             "WHERE id=? AND status='queued'", (iso(now), msg["id"])).rowcount
        if claimed != 1:
            continue
        try:
            pid = provider.send(contact["phone"], _render(msg, contact, templates), msg["uid"])
        except TransientError as e:
            if msg["attempts"] + 1 >= MAX_ATTEMPTS:
                db.execute("UPDATE sms_messages SET status='failed', reason=? WHERE id=?", (str(e)[:300], msg["id"]))
                stats["failed"] += 1
            else:
                retry_at = now + timedelta(minutes=10 * 2 ** msg["attempts"])
                db.execute("UPDATE sms_messages SET status='queued', reason=?, send_at=?, sent_at=NULL WHERE id=?",
                           (str(e)[:300], iso(retry_at), msg["id"]))
                stats["retry"] += 1
        except PermanentError as e:
            db.execute("UPDATE sms_messages SET status='failed', reason=? WHERE id=?", (str(e)[:300], msg["id"]))
            stats["failed"] += 1
        else:
            db.execute("UPDATE sms_messages SET status='sent', provider_id=? WHERE id=?", (pid, msg["id"]))
            sent_today += 1
            stats["sent"] += 1
    return stats


def recover_unknown(db, now=None, minutes=15) -> int:
    """Messages stuck in 'sending' (crash during the provider call) are NOT re-sent."""
    now = now or utcnow()
    return db.execute("UPDATE sms_messages SET status='unknown', reason='نتیجه ارسال نامشخص؛ بررسی دستی' "
                      "WHERE status='sending' AND sent_at < ?", (iso(now - timedelta(minutes=minutes)),)).rowcount


def stats(db) -> dict:
    out = {r["status"]: r["n"] for r in db.all("SELECT status, COUNT(*) AS n FROM sms_messages GROUP BY status")}
    out["contacts"] = db.one("SELECT COUNT(*) AS n FROM contacts")["n"]
    out["consented"] = db.one("SELECT COUNT(*) AS n FROM contacts WHERE consent=1 AND opted_out=0")["n"]
    return out
