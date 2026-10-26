"""Virtual City (RFP section 3.1): citizens, eligibility, population, weekly selection.

Eligibility = verified by an admin AND currently follows the page AND has at least
one valid comment. Everything else is "pending" and is never counted as population.
The official Instagram API may not expose followers or the follow/comment match, so
the intake is: volunteer registration (+ comments received through the API/webhook)
-> admin verification. Raw follower count is stored separately and never announced
as the population.

Privacy: comments are stored only for registered users (data minimisation); a user
can withdraw display consent or be erased completely; public display of an id or job
requires recorded consent.
"""
import hashlib
import re
from datetime import timedelta
from typing import Optional

from . import repo
from .textutil import fold, normalize
from .util import iso, parse, utcnow

CAP = 400_000
WEEK_PICKS = 10
_IG = re.compile(r"^[a-z0-9._]{1,30}$")
_LINK = re.compile(r"(https?://|www\.|\.com|\.ir|t\.me/)", re.I)
_BAD_JOB_WORDS = ["کیر", "کس", "جنده", "حرامزاده", "کثافت"]


class CityError(ValueError):
    pass


def norm_id(raw: str) -> str:
    ig = (raw or "").strip().lstrip("@").lower()
    if not _IG.match(ig):
        raise CityError("آیدی اینستاگرام نامعتبر است")
    return ig


def clean_job(job: Optional[str]) -> Optional[str]:
    """Jobs are shown publicly, so keep them short, link-free and respectful."""
    if not job or not job.strip():
        return None
    job = re.sub(r"\s+", " ", job.strip())
    if len(job) > 40 or _LINK.search(job) or "@" in job or any(w in fold(job) for w in _BAD_JOB_WORDS):
        raise CityError("شغل نامناسب است (حداکثر ۴۰ نویسه، بدون لینک/آیدی/الفاظ نامحترمانه)")
    return job


def kv_get(db, key, default=None):
    row = db.one("SELECT value FROM kv WHERE key=?", (key,))
    return row["value"] if row else default


def kv_set(db, key, value):
    db.execute("INSERT INTO kv (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, str(value)))


# ---------- citizens ----------
def register(db, raw_id: str, job: Optional[str] = None, consent_display: bool = False) -> dict:
    ig = norm_id(raw_id)
    job = clean_job(job)
    row = db.one("SELECT * FROM citizens WHERE ig_id=?", (ig,))
    if row is None:
        db.execute("INSERT INTO citizens (ig_id, job, consent_display, registered_at) VALUES (?,?,?,?)",
                   (ig, job, int(consent_display), iso(utcnow())))
    else:
        db.execute("UPDATE citizens SET job=COALESCE(?, job), consent_display=? WHERE ig_id=?",
                   (job, int(consent_display), ig))
    return get(db, ig)


def get(db, ig: str) -> Optional[dict]:
    return db.one("SELECT * FROM citizens WHERE ig_id=?", (ig,))


def set_consent(db, raw_id: str, consent: bool, job: Optional[str] = None) -> dict:
    ig = norm_id(raw_id)
    if get(db, ig) is None:
        raise KeyError(ig)
    db.execute("UPDATE citizens SET consent_display=?, job=COALESCE(?, job) WHERE ig_id=?",
               (int(consent), clean_job(job), ig))
    return get(db, ig)


def set_follow(db, raw_id: str, follows: bool) -> dict:
    ig = norm_id(raw_id)
    if get(db, ig) is None:
        raise KeyError(ig)
    db.execute("UPDATE citizens SET follow_ok=? WHERE ig_id=?", (int(follows), ig))
    return get(db, ig)


def rename(db, old: str, new: str) -> dict:
    o, n = norm_id(old), norm_id(new)
    if get(db, o) is None:
        raise KeyError(o)
    if get(db, n) is not None:
        raise CityError("آیدی جدید قبلاً ثبت شده است")
    db.execute("UPDATE citizens SET ig_id=? WHERE ig_id=?", (n, o))  # comments follow via ON UPDATE CASCADE
    db.execute("UPDATE city_selections SET ig_id=? WHERE ig_id=?", (n, o))
    return get(db, n)


def erase(db, raw_id: str) -> bool:
    """Right to deletion: citizen and comments are removed; past selections are anonymised."""
    ig = norm_id(raw_id)
    if get(db, ig) is None:
        return False
    db.execute("UPDATE city_selections SET ig_id='(حذف‌شده)', job=NULL WHERE ig_id=?", (ig,))
    db.execute("DELETE FROM city_comments WHERE ig_id=?", (ig,))
    db.execute("DELETE FROM citizens WHERE ig_id=?", (ig,))
    return True


# ---------- comments ----------
def is_valid_comment(db, ig: str, text: str, now=None) -> bool:
    """Spam control: real text (not just mentions/emoji/links), not a repeat, not a flood."""
    now = now or utcnow()
    t = (text or "").strip()
    if len(normalize(t)) < 3 or _LINK.search(t):
        return False
    if db.one("SELECT 1 AS x FROM city_comments WHERE ig_id=? AND valid=1 AND text=?", (ig, t)):
        return False
    recent = db.one("SELECT COUNT(*) AS n FROM city_comments WHERE ig_id=? AND created_at>=?",
                    (ig, iso(now - timedelta(hours=24))))["n"]
    return recent < 10


def add_comment(db, raw_id: str, comment_id: str, post_id: str, text: str, created_at=None) -> Optional[bool]:
    """Returns ``True``/``False`` for valid/invalid, ``None`` if the user is not registered (ignored)."""
    ig = norm_id(raw_id)
    if get(db, ig) is None:
        return None
    if db.one("SELECT 1 AS x FROM city_comments WHERE comment_id=?", (comment_id,)):
        return db.one("SELECT valid FROM city_comments WHERE comment_id=?", (comment_id,))["valid"] == 1
    created_at = created_at or utcnow()
    valid = is_valid_comment(db, ig, text, created_at)
    db.execute("INSERT INTO city_comments (comment_id,ig_id,post_id,text,valid,created_at) VALUES (?,?,?,?,?,?)",
               (comment_id, ig, post_id, text.strip(), int(valid), iso(created_at)))
    return valid


def invalidate_comment(db, comment_id: str) -> bool:
    """Comment deleted or judged invalid later -> eligibility is recomputed automatically."""
    return db.execute("UPDATE city_comments SET valid=0 WHERE comment_id=?", (comment_id,)).rowcount == 1


# ---------- eligibility ----------
_ELIGIBLE = ("c.status='verified' AND c.follow_ok=1 AND EXISTS "
             "(SELECT 1 FROM city_comments m WHERE m.ig_id=c.ig_id AND m.valid=1)")


def eligible_count(db) -> int:
    return db.one(f"SELECT COUNT(*) AS n FROM citizens c WHERE {_ELIGIBLE}")["n"]


def meets_conditions(db, ig: str) -> bool:
    return db.one(f"SELECT 1 AS x FROM citizens c WHERE c.ig_id=? AND c.follow_ok=1 AND EXISTS "
                  "(SELECT 1 FROM city_comments m WHERE m.ig_id=c.ig_id AND m.valid=1)", (ig,)) is not None


def verify(db, raw_id: str, actor: str) -> dict:
    ig = norm_id(raw_id)
    c = get(db, ig)
    if c is None:
        raise KeyError(ig)
    if not meets_conditions(db, ig):
        raise CityError("شرایط عضویت احراز نشده است (فالو + دست‌کم یک کامنت معتبر)")
    status = "waitlist" if eligible_count(db) >= CAP else "verified"
    db.execute("UPDATE citizens SET status=?, verified_at=? WHERE ig_id=?", (status, iso(utcnow()), ig))
    repo.audit(db, actor, f"city.{status}", "citizen", ig)
    return get(db, ig)


def set_raw_followers(db, n: int) -> None:
    kv_set(db, "raw_followers", int(n))


def stats(db) -> dict:
    by = {r["status"]: r["n"] for r in db.all("SELECT status, COUNT(*) AS n FROM citizens GROUP BY status")}
    eligible = eligible_count(db)
    return {
        "population": min(eligible, CAP),          # the only number that may be announced
        "cap": CAP,
        "pending": by.get("pending", 0),           # never counted as population
        "waitlist": by.get("waitlist", 0),
        "verified_records": by.get("verified", 0),
        "raw_followers": int(kv_get(db, "raw_followers", 0)),  # reference only, never announced as population
    }


# ---------- weekly selection ----------
def _rank(seed: str, ig: str) -> str:
    return hashlib.sha256(f"{seed}|{ig}".encode()).hexdigest()


def select_weekly(db, week_key: str, since, until, n: int = WEEK_PICKS, cool_off_weeks: int = 4) -> list:
    """Pick ``n`` citizens for the week; repeating a week returns the stored result.

    Transparent and reproducible: candidates are citizens who consented to public
    display and wrote a valid comment in the period; they are ordered by
    sha256(seed|id) where seed = sha256(week_key | sorted candidate ids). People
    chosen in the last ``cool_off_weeks`` weeks are used only if there are not enough others.
    """
    existing = db.all("SELECT ig_id, job FROM city_selections WHERE week_key=? ORDER BY id", (week_key,))
    if existing:
        return existing
    rows = db.all(
        f"SELECT c.ig_id, c.job FROM citizens c WHERE {_ELIGIBLE} AND c.consent_display=1 AND EXISTS "
        "(SELECT 1 FROM city_comments m WHERE m.ig_id=c.ig_id AND m.valid=1 AND m.created_at>=? AND m.created_at<?)",
        (iso(since), iso(until)))
    ids = sorted(r["ig_id"] for r in rows)
    seed = hashlib.sha256((week_key + "|" + ",".join(ids)).encode()).hexdigest()
    recent = {r["ig_id"]: r["last"] for r in db.all(
        "SELECT ig_id, MAX(created_at) AS last FROM city_selections WHERE created_at>=? GROUP BY ig_id",
        (iso(until - timedelta(weeks=cool_off_weeks)),))}
    fresh = sorted((r for r in rows if r["ig_id"] not in recent), key=lambda r: _rank(seed, r["ig_id"]))
    stale = sorted((r for r in rows if r["ig_id"] in recent), key=lambda r: (recent[r["ig_id"]], _rank(seed, r["ig_id"])))
    picked = (fresh + stale)[:n]
    now = iso(utcnow())
    db.execute("INSERT INTO city_runs (week_key, seed, candidates, created_at) VALUES (?,?,?,?)",
               (week_key, seed, len(rows), now))
    for r in picked:
        db.execute("INSERT INTO city_selections (week_key, ig_id, job, created_at) VALUES (?,?,?,?)",
                   (week_key, r["ig_id"], r["job"], now))
    return [{"ig_id": r["ig_id"], "job": r["job"]} for r in picked]
