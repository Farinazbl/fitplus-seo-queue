"""Database-backed job queue with retries, dead-letter state and stale-job recovery.

Statuses: queued -> running -> done | queued (retry with backoff) | dead.
``dedupe_key`` makes enqueueing idempotent, which is what keeps the scheduler
from creating the same slot twice.
"""
import json
import sqlite3
from datetime import timedelta
from typing import Optional

from .util import iso, parse, utcnow


def backoff_seconds(attempts: int) -> int:
    return min(30 * 2 ** max(attempts - 1, 0), 1800)


def enqueue(db, kind: str, agent_key: Optional[str] = None, payload: Optional[dict] = None,
            run_at=None, dedupe_key: Optional[str] = None, max_attempts: int = 3) -> Optional[int]:
    """Add a job. Returns its id, or ``None`` if ``dedupe_key`` already exists."""
    now = utcnow()
    try:
        cur = db.execute(
            "INSERT INTO jobs (kind,agent_key,payload,status,max_attempts,run_at,dedupe_key,created_at) "
            "VALUES (?,?,?,'queued',?,?,?,?)",
            (kind, agent_key, json.dumps(payload or {}, ensure_ascii=False), max_attempts,
             iso(run_at or now), dedupe_key, iso(now)))
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def claim(db, worker: str, now=None) -> Optional[dict]:
    """Atomically take the oldest due job."""
    now = now or utcnow()
    with db.tx() as c:
        row = c.execute(
            "SELECT * FROM jobs WHERE status='queued' AND run_at <= ? ORDER BY run_at, id LIMIT 1",
            (iso(now),)).fetchone()
        if not row:
            return None
        c.execute("UPDATE jobs SET status='running', locked_at=?, worker=?, attempts=attempts+1 WHERE id=?",
                  (iso(now), worker, row["id"]))
    job = db.one("SELECT * FROM jobs WHERE id=?", (row["id"],))
    job["payload"] = json.loads(job["payload"])
    return job


def complete(db, job_id: int, note: Optional[str] = None, now=None) -> None:
    db.execute("UPDATE jobs SET status='done', note=?, finished_at=?, locked_at=NULL WHERE id=?",
               (note, iso(now or utcnow()), job_id))


def fail(db, job: dict, error: str, now=None) -> str:
    """Retry with exponential backoff, or move to ``dead`` once attempts are exhausted."""
    now = now or utcnow()
    if job["attempts"] >= job["max_attempts"]:
        db.execute("UPDATE jobs SET status='dead', last_error=?, finished_at=?, locked_at=NULL WHERE id=?",
                   (error[:2000], iso(now), job["id"]))
        return "dead"
    db.execute("UPDATE jobs SET status='queued', last_error=?, run_at=?, locked_at=NULL WHERE id=?",
               (error[:2000], iso(now + timedelta(seconds=backoff_seconds(job["attempts"]))), job["id"]))
    return "retry"


def recover_stale(db, minutes: int = 15, now=None) -> int:
    """Jobs stuck in ``running`` (worker crashed) go back to the queue, or to ``dead``."""
    now = now or utcnow()
    cutoff = iso(now - timedelta(minutes=minutes))
    stuck = db.all("SELECT * FROM jobs WHERE status='running' AND locked_at < ?", (cutoff,))
    for job in stuck:
        fail(db, job, "worker stopped responding (stale lock)", now)
    return len(stuck)


def counts(db) -> dict:
    out = {"queued": 0, "running": 0, "done": 0, "dead": 0}
    for r in db.all("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"):
        out[r["status"]] = r["n"]
    return out


def alerts(db, now=None, stale_minutes: int = 15, overdue_minutes: int = 30) -> list:
    """Conditions the operator should be told about (shown on the dashboard)."""
    now = now or utcnow()
    out = []
    dead = db.one("SELECT COUNT(*) AS n FROM jobs WHERE status='dead'")["n"]
    if dead:
        out.append({"level": "error", "code": "dead_jobs", "message": f"{dead} وظیفه پس از چند تلاش ناموفق متوقف شده است"})
    stale = db.one("SELECT COUNT(*) AS n FROM jobs WHERE status='running' AND locked_at < ?",
                   (iso(now - timedelta(minutes=stale_minutes)),))["n"]
    if stale:
        out.append({"level": "warning", "code": "stale_jobs", "message": f"{stale} وظیفه بیش از {stale_minutes} دقیقه در حال اجراست"})
    overdue = db.one("SELECT COUNT(*) AS n FROM jobs WHERE status='queued' AND run_at < ?",
                     (iso(now - timedelta(minutes=overdue_minutes)),))["n"]
    if overdue:
        out.append({"level": "warning", "code": "overdue_jobs", "message": f"{overdue} وظیفه بیش از {overdue_minutes} دقیقه در صف مانده است (آیا worker در حال اجراست؟)"})
    return out
