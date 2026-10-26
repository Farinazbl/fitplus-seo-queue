"""Settings, read from environment variables (never from the browser)."""
import os
from dataclasses import dataclass, field
from typing import Dict, Tuple

ROLES = ("viewer", "editor", "admin")


def parse_tokens(spec: str) -> Dict[str, Tuple[str, str]]:
    """``"alice=TOKEN1:admin,bob=TOKEN2:editor"`` -> ``{token: (name, role)}``."""
    out = {}
    for part in filter(None, (p.strip() for p in spec.split(","))):
        ident, _, role = part.rpartition(":")
        if role not in ROLES:
            raise ValueError(f"نقش نامعتبر در APP_TOKENS: {role!r}")
        name, _, token = ident.rpartition("=")
        token = token or ident
        if len(token) < 16:
            raise ValueError("هر توکن باید دست‌کم ۱۶ نویسه باشد")
        out[token] = (name or role, role)
    return out


@dataclass
class Settings:
    db_path: str = "agents.db"
    tokens: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    timezone: str = "Asia/Tehran"
    dedup_window_days: int = 15
    dedup_threshold: float = 0.6
    stale_job_minutes: int = 15
    seed: bool = True


def from_env() -> Settings:
    return Settings(
        db_path=os.environ.get("APP_DB", "agents.db"),
        tokens=parse_tokens(os.environ.get("APP_TOKENS", "")),
        timezone=os.environ.get("APP_TZ", "Asia/Tehran"),
        dedup_window_days=int(os.environ.get("APP_DEDUP_DAYS", "15")),
        dedup_threshold=float(os.environ.get("APP_DEDUP_THRESHOLD", "0.6")),
        stale_job_minutes=int(os.environ.get("APP_STALE_MINUTES", "15")),
        seed=os.environ.get("APP_SEED", "1") == "1",
    )
