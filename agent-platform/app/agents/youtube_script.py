"""YouTube agent (text only): video package = title, description, cover text and a full script.

Not built here: video/voice generation, cover image rendering, upload (YouTube Data API +
OAuth + Google audit) and analytics. Approved packages end as 'ready_manual' for a producer.
Channel differences (kids/English, health, finance) live in the agent's ``prompt`` setting.
"""
import re

from .. import llm
from .base import Agent, Draft, register

SYSTEM = """تو برنامه‌نویس محتوای یک کانال یوتیوب هستی. یک ویدئوی {minutes} دقیقه‌ای کامل طراحی کن.
قالب دقیق خروجی (هر برچسب در یک خط جدا):
TITLE: عنوان (حداکثر ۷۰ نویسه، بدون کلیک‌بیت دروغ)
COVER: متن کوتاه روی کاور (حداکثر ۵ کلمه)
DESCRIPTION: توضیحات ویدئو (۳ تا ۵ خط) و سه هشتگ
SCRIPT: متن کامل گفتار، با بخش‌بندی و زمان تقریبی هر بخش (خروجی تا آخر ادامه دارد)
- ادعای قطعی درباره درمان، کاهش وزن مشخص، سود یا درآمد تضمینی ننویس؛ آمار و پژوهش مطمئن‌نشده نیاور.
- موضوعات اخیر که نباید تکرار شوند: {recent}
{extra}"""
_RE = {k: re.compile(rf"^{k}:\s*(.+)$", re.M) for k in ("TITLE", "COVER", "DESCRIPTION")}


@register("youtube_script")
class YoutubeScriptAgent(Agent):
    def __init__(self, db=None):
        self.db = db

    def generate(self, cfg, task, payload):
        rows = self.db.all("SELECT title FROM content_items WHERE agent_key=? AND title!='' "
                           "ORDER BY created_at DESC LIMIT 20", (cfg["key"],)) if self.db else []
        recent = "؛ ".join(r["title"] for r in rows) or "ندارد"
        minutes = int(cfg["policy"].get("minutes", 12))
        brief = "\n".join(x for x in (cfg["policy"].get("brief"), cfg.get("prompt")) if x)
        extra = f"دستور کانال:\n{brief}" if brief else ""
        out = llm.get_client().complete(SYSTEM.format(minutes=minutes, recent=recent, extra=extra),
                                        "یک ایده جدید انتخاب کن و بسته کامل ویدئو را بنویس.", max_tokens=4000)
        found = {k: r.search(out) for k, r in _RE.items()}
        if not all(found.values()) or "SCRIPT:" not in out:
            raise ValueError("خروجی مدل قالب TITLE/COVER/DESCRIPTION/SCRIPT را رعایت نکرد")  # queue retries
        script = out.split("SCRIPT:", 1)[1].strip()
        if len(script.split()) < 150:
            raise ValueError("متن ویدئو خیلی کوتاه است")
        body = (f"عنوان: {found['TITLE'].group(1).strip()}\nمتن کاور: {found['COVER'].group(1).strip()}\n"
                f"توضیحات: {found['DESCRIPTION'].group(1).strip()}\n\n--- متن ویدئو ---\n{script}")
        return [Draft(body=body, title=found["TITLE"].group(1).strip()[:100], kind="video_script",
                      meta={"media": "not_generated", "minutes": minutes},
                      review_reasons=["بازبینی انسانی پیش از تولید ویدئو"])]
