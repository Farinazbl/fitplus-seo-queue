"""Agent interface and registry.

An agent only *produces drafts*. Everything else - rules, duplicate checks,
approval, publishing, retries, logging - is done by the orchestrator, so every
agent gets the same safety net and the same traceability.
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List


@dataclass
class Draft:
    body: str
    title: str = ""
    kind: str = "text"                     # text | image | video | reel | story | ...
    meta: dict = field(default_factory=dict)  # e.g. {"source_ref": "...", "files": [...]}
    review_reasons: List[str] = field(default_factory=list)  # force human approval


class Agent:
    """Subclass and implement ``generate``."""

    def generate(self, cfg: dict, task: str, payload: dict) -> List[Draft]:
        raise NotImplementedError


_AGENTS: Dict[str, Callable[[], Agent]] = {}


def register(agent_type: str):
    def deco(cls):
        _AGENTS[agent_type] = cls
        return cls
    return deco


def get(agent_type: str, db=None) -> Agent:
    if agent_type not in _AGENTS:
        raise NotImplementedError(f"ایجنت «{agent_type}» هنوز پیاده‌سازی نشده است")
    cls = _AGENTS[agent_type]
    try:
        return cls(db)
    except TypeError:
        return cls()


def available():
    return sorted(_AGENTS)
