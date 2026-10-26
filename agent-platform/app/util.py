"""Small shared helpers: UTC time handling and ids."""
import uuid
from datetime import datetime, timezone

_FMT = "%Y-%m-%dT%H:%M:%SZ"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_FMT)


def parse(s: str) -> datetime:
    return datetime.strptime(s, _FMT).replace(tzinfo=timezone.utc)


def new_uid(prefix: str = "c") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"
