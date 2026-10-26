"""SEO article agent: two articles a week from a keyword list.

``sources`` holds the keyword/topic list, one entry per line (``rss:`` entries
are ignored here). Each keyword is used once (tracked through ``source_ref``).
The article is written for readers first: no keyword stuffing, no invented
statistics, no health/finance promises. Output is HTML for WordPress with a
title and a meta description (<= 155 chars). Goal metrics (e.g. 50 organic
visits/day) are KPIs measured later from Search Console, not promises.
"""
import re

from .. import llm
from .base import Agent, Draft, register

SYSTEM = """تو نویسنده سئو برای یک وب‌سایت فارسی هستی. درباره عبارت کلیدی داده‌شده یک مقاله کامل و مفید بنویس:
- خروجی دقیقاً این قالب باشد: خط اول «TITLE: عنوان» (حداکثر ۶۰ نویسه)، خط دوم «META: توضیح متا» (حداکثر ۱۵۵ نویسه)، سپس بدنه مقاله به HTML.
- بدنه: مقدمه کوتاه، دست‌کم ۴ زیرعنوان <h2>، پاراگراف‌های <p>، و جمع‌بندی. حدود ۸۰۰ تا ۱۲۰۰ کلمه.
- عبارت کلیدی را طبیعی در عنوان، مقدمه و یکی دو زیرعنوان بیاور؛ تکرار بی‌جا نکن.
- آمار، نام پژوهش یا ادعای پزشکی/مالی که مطمئن نیستی را ننویس؛ توصیه‌ها عمومی و محتاطانه باشند.
- لینک و آدرس سایت نساز.
{extra}"""

_TITLE = re.compile(r"^TITLE:\s*(.+)$", re.M)
_META = re.compile(r"^META:\s*(.+)$", re.M)


def parse_article(text: str):
    t, m = _TITLE.search(text), _META.search(text)
    if not t or not m:
        return None
    body = text[m.end():].strip()
    return t.group(1).strip()[:120], m.group(1).strip()[:155], body


@register("seo_article")
class SeoArticleAgent(Agent):
    def __init__(self, db=None):
        self.db = db

    def generate(self, cfg, task, payload):
        if task == "weekly_report":
            return [self._weekly_report(cfg)]
        keywords = [s.strip() for s in cfg["sources"] if s.strip() and not s.startswith("rss:")]
        used = {r["source_ref"] for r in self.db.all(
            "SELECT source_ref FROM fingerprints WHERE scope=? AND source_ref IS NOT NULL", (cfg["key"],))}
        todo = [k for k in keywords if f"kw:{k}" not in used]
        if not todo:
            return []
        keyword = todo[0]
        extra = f"\nدستور تکمیلی:\n{cfg['prompt']}" if cfg["prompt"] else ""
        out = llm.get_client().complete(SYSTEM.format(extra=extra), f"عبارت کلیدی: {keyword}", max_tokens=3500)
        parsed = parse_article(out)
        if parsed is None:
            raise ValueError("خروجی مدل قالب TITLE/META را رعایت نکرد")  # queue retries
        title, meta_desc, body = parsed
        if len(re.sub(r"<[^>]+>", " ", body).split()) < 300:
            raise ValueError("مقاله خیلی کوتاه است")
        return [Draft(body=body, title=title, kind="article",
                      meta={"source_ref": f"kw:{keyword}", "keyword": keyword, "meta_description": meta_desc})]

    def _weekly_report(self, cfg) -> Draft:
        from datetime import timedelta
        from ..util import iso, utcnow
        now = utcnow()
        week_ago = iso(now - timedelta(days=7))
        by = {r["status"]: r["n"] for r in self.db.all(
            "SELECT status, COUNT(*) AS n FROM content_items WHERE agent_key=? AND kind='article' AND created_at>=? "
            "GROUP BY status", (cfg["key"], week_ago))}
        used = {r["source_ref"] for r in self.db.all(
            "SELECT source_ref FROM fingerprints WHERE scope=? AND source_ref LIKE 'kw:%'", (cfg["key"],))}
        left = [k for k in cfg["sources"] if k.strip() and not k.startswith("rss:") and f"kw:{k.strip()}" not in used]
        d0, d7, d14 = [(now - timedelta(days=n)).strftime("%Y-%m-%d") for n in (0, 7, 14)]
        lines = [f"گزارش هفتگی سئو — تا {d0}", "",
                 f"مقاله‌های این هفته: {sum(by.values())} (منتشرشده/پیش‌نویس: {by.get('published', 0) + by.get('ready_manual', 0)}، "
                 f"در انتظار تأیید: {by.get('pending_approval', 0)}، ردشده: {by.get('rejected', 0)}، ناموفق: {by.get('failed', 0)})",
                 f"عبارت‌های کلیدی باقی‌مانده: {len(left)}" + ("  ⚠ فهرست کلیدواژه رو به پایان است" if len(left) < 3 else ""), ""]
        names = [r["name"] for r in self.db.all("SELECT DISTINCT name FROM metrics ORDER BY name")]
        if not names:
            lines.append("داده‌ای از Search Console / Analytics ثبت نشده است (از POST /api/metrics ارسال شود).")
        for name in names:
            q = lambda lo, hi: self.db.one("SELECT SUM(value) AS v, COUNT(*) AS n FROM metrics WHERE name=? AND day>? AND day<=?",
                                           (name, lo, hi))
            cur, prev = q(d7, d0), q(d14, d7)
            first = self.db.one("SELECT MIN(day) AS d FROM metrics WHERE name=?", (name,))["d"]
            delta = "" if not prev["n"] else f" (هفته قبل: {prev['v']:.0f})"
            lines.append(f"{name}: {(cur['v'] or 0):.0f}{delta} — خط مبنا از {first}")
        lines += ["", "یادآوری: هدف ۵۰ بازدید ارگانیک روزانه شاخص عملکردی است و بر اساس خط مبنا سنجیده می‌شود، نه تعهد."]
        return Draft(body="\n".join(lines), title=f"گزارش هفتگی سئو {d0}", kind="report", meta={"week_ending": d0})
