"""News rewrite agent for the two Bale channels (tax payers / accounting).

Reads the configured sources, takes items not seen in the last 15 days, and asks
the LLM for an ORIGINAL rewrite that obeys the channel rules. The platform then
applies the hard rules (no links / ids / source names / course ads) and the
duplicate check; if the model slips, the draft is rejected, not published.

Settings in the agent's ``policy``: ``max_per_run`` (default 1).
"""
import re

from .. import llm, sources
from ..textutil import fold
from .base import Agent, Draft, register

SYSTEM = """تو ویراستار یک کانال خبری فارسی هستی. خبر ورودی را به‌طور کامل بازنویسی کن:
- ساختار و جمله‌ها را از نو بنویس؛ عین عبارت‌های منبع را کپی نکن.
- فقط واقعیت‌های موجود در متن را بنویس؛ عدد، تاریخ یا حکمی از خودت اضافه نکن.
- نام منبع، لینک، آیدی (@) و تبلیغ دوره یا خدمات را نیاور.
- لحن رسمی و روان، حداکثر ۷۰۰ نویسه، با یک خط عنوان و سپس متن.
- خبری که درباره شخص یا افراد مشخص است (نام، فعالیت، اتفاق یا اظهارنظر شخصی) منتشر نمی‌شود؛ در این حالت فقط بنویس: SKIP
- اگر متن برای بازنویسی کافی یا قابل‌اعتماد نیست، یا با موضوع کانال نمی‌خواند، فقط بنویس: SKIP
{topic}
{extra}"""


_NUM = re.compile(r"\d[\d,٬.]*")


def unsupported_numbers(source: str, rewrite: str):
    """Numbers/dates in the rewrite that do not appear in the source (fact-check guard)."""
    norm = lambda t: fold(t).replace("٬", "").replace(",", "")
    src = norm(source)
    return sorted({n for n in _NUM.findall(norm(rewrite)) if n.strip(".") and n.strip(".") not in src})


@register("news_rewrite")
class NewsRewriteAgent(Agent):
    def __init__(self, db=None):
        self.db = db

    def generate(self, cfg, task, payload):
        items = sources.fetch(self.db, cfg["sources"])
        limit = int(cfg["policy"].get("max_per_run", 1))
        client = llm.get_client()
        extra = f"\nدستور تکمیلی کانال:\n{cfg['prompt']}" if cfg["prompt"] else ""
        topic = cfg["policy"].get("topic")
        topic_line = f"- موضوع این کانال: «{topic}». خبر نامرتبط را SKIP کن." if topic else ""
        drafts = []
        for item in items:
            if len(drafts) >= limit:
                break
            if self._seen(cfg["key"], item.ref):
                continue
            out = client.complete(SYSTEM.format(extra=extra, topic=topic_line), f"عنوان: {item.title}\n\n{item.text[:4000]}")
            if not out or out.strip().upper().startswith("SKIP"):
                continue
            title, _, body = out.partition("\n")
            bad = unsupported_numbers(item.title + " " + item.text, out)
            reasons = [f"عدد/تاریخ در منبع نیست (راستی‌آزمایی): {', '.join(bad)}"] if bad else []
            drafts.append(Draft(body=(body.strip() or title.strip()), title=title.strip()[:120],
                                meta={"source_ref": item.ref, "origin": item.origin},
                                review_reasons=reasons))
            if item.ref.startswith("inbox:"):
                self.db.execute("UPDATE inbox SET used=1 WHERE id=?", (int(item.ref[6:]),))
        return drafts

    def _seen(self, scope, ref) -> bool:
        from ..util import iso, utcnow
        from datetime import timedelta
        return self.db.one("SELECT 1 AS x FROM fingerprints WHERE scope=? AND source_ref=? AND created_at>=?",
                           (scope, ref, iso(utcnow() - timedelta(days=15)))) is not None
