"""Turns each agent's schedule into queued jobs.

A schedule is a list of items. Supported item types (times are local, default
Asia/Tehran; weekdays use Monday=0 ... Sunday=6):

    {"type": "daily",        "times": ["09:00"]}
    {"type": "weekly",       "days": [1, 4], "times": ["10:00"]}
    {"type": "every_n_days", "n": 2, "anchor": "2026-10-27", "times": ["17:00"]}
    {"type": "interval",     "minutes": 60}
    {"type": "event"} / {"type": "external"}      -> never scheduled by time

An optional ``"task"`` names what the agent should do in that slot (e.g. "reel").
Every slot has a stable key, and the job queue's ``dedupe_key`` guarantees one
job per slot even if the scheduler runs many times or restarts.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import queue, repo
from .util import utcnow

MAX_LATE_MINUTES = 180


def _times(item):
    for t in item.get("times", []):
        h, m = t.split(":")
        yield int(h), int(m)


def due_slots(agent_key: str, item: dict, now_utc: datetime, tz: ZoneInfo,
              max_late_minutes: int = MAX_LATE_MINUTES) -> list:
    """Return the slot keys of ``item`` that have passed within the lateness window."""
    kind = item.get("type")
    task = item.get("task", "default")
    local_now = now_utc.astimezone(tz)

    if kind == "interval":
        step = int(item["minutes"]) * 60
        slot = int(now_utc.timestamp()) // step * step
        return [f"{agent_key}:{task}:i{slot}"]

    if kind not in ("daily", "weekly", "every_n_days"):
        return []

    slots = []
    for day_offset in (0, -1):  # today, and yesterday for slots just before midnight
        day: date = (local_now + timedelta(days=day_offset)).date()
        if kind == "weekly" and day.weekday() not in item.get("days", []):
            continue
        if kind == "every_n_days":
            anchor = date.fromisoformat(item["anchor"])
            if (day - anchor).days < 0 or (day - anchor).days % int(item["n"]) != 0:
                continue
        for h, m in _times(item):
            slot_dt = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
            late = (local_now - slot_dt).total_seconds() / 60
            if 0 <= late <= max_late_minutes:
                slots.append(f"{agent_key}:{task}:{slot_dt.isoformat()}")
    return slots


def tick(db, now_utc: datetime = None, tz_name: str = "Asia/Tehran") -> int:
    """Enqueue every due slot of every enabled agent. Returns the number of new jobs."""
    now_utc = now_utc or utcnow()
    tz = ZoneInfo(tz_name)
    created = 0
    for agent in repo.list_agents(db):
        if not agent["enabled"]:
            continue
        for item in agent["schedule"]:
            for key in due_slots(agent["key"], item, now_utc, tz):
                job_id = queue.enqueue(
                    db, "run_agent", agent["key"],
                    {"task": item.get("task", "default"), "slot": key}, dedupe_key=key)
                if job_id:
                    created += 1
    return created


def upcoming(db, days: int = 14, now_utc: datetime = None, tz_name: str = "Asia/Tehran") -> list:
    """Content calendar: the next scheduled slots of every enabled agent (time-based items only)."""
    now_utc = now_utc or utcnow()
    tz = ZoneInfo(tz_name)
    out = []
    for agent in repo.list_agents(db):
        if not agent["enabled"]:
            continue
        for item in agent["schedule"]:
            if item.get("type") not in ("daily", "weekly", "every_n_days"):
                continue
            for offset in range(days + 1):
                day = (now_utc.astimezone(tz) + timedelta(days=offset)).date()
                for h, m in _times(item):
                    slot = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
                    if slot <= now_utc:
                        continue
                    if due_slots(agent["key"], item, slot.astimezone(timezone.utc) + timedelta(minutes=1), tz):
                        out.append({"at": slot.isoformat(), "agent": agent["key"], "title": agent["title"],
                                    "task": item.get("task", "default")})
    return sorted(out, key=lambda r: r["at"])
