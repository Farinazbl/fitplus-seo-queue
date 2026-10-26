"""News sources.

* ``rss``: any RSS/Atom URL listed in the agent's ``sources`` (website feeds).
* ``inbox``: items pushed to ``POST /api/inbox`` - for channels the bot cannot read
  itself (Bale/Telegram posts the client is allowed to use, forwarded or pasted).

Source entries in the agent config are strings: ``rss:https://site/feed`` or ``inbox``.
"""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional

from . import http

_TAGS = re.compile(r"<[^>]+>")


@dataclass
class Item:
    ref: str
    title: str
    text: str
    origin: str


def _clean(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", _TAGS.sub(" ", s or "")).strip()


def parse_feed(xml_text: str, origin: str) -> List[Item]:
    root = ET.fromstring(xml_text)
    items = []
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        get = lambda *names: next((c.text for c in node if c.tag.split("}")[-1] in names and c.text), None)
        link = get("link") or next((c.get("href") for c in node if c.tag.split("}")[-1] == "link"), None)
        ref = get("guid", "id") or link
        text = _clean(get("encoded", "content", "description", "summary"))
        if ref and text:
            items.append(Item(ref=ref, title=_clean(get("title")), text=text, origin=origin))
    return items


def fetch(db, sources: List[str], transport=http.request, limit=30) -> List[Item]:
    out: List[Item] = []
    for s in sources:
        if s.startswith("rss:"):
            url = s[4:]
            try:
                out += parse_feed(transport("GET", url), url)[:limit]
            except (http.HttpError, ET.ParseError):
                continue  # one broken source must not stop the others
        elif s == "inbox":
            rows = db.all("SELECT id, title, text FROM inbox WHERE used=0 ORDER BY id LIMIT ?", (limit,))
            out += [Item(ref=f"inbox:{r['id']}", title=r["title"], text=r["text"], origin="inbox") for r in rows]
    return out
