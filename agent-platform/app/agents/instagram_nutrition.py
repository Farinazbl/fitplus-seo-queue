"""Instagram nutrition agent: plans and writes content (script, slide text, caption).

Scope: text only. Video/animation and images are NOT generated here (they need a
video/image service); the draft carries a scene-by-scene script and slide texts
for the designer or a future media step. Every draft requires human (scientific)
approval before it is released, and the channel has no Instagram connector yet,
so approved items end as 'ready_manual'.

Weekly rotation (4 axes, by ISO week number): the daily reel follows the week's axis.
"""
import re

from .. import llm
from ..util import utcnow
from .base import Agent, Draft, register

AXES = ["تغذیه ورزشی و عملکرد", "میان‌وعده و آشپزی سالم", "باورهای غلط تغذیه‌ای", "خواص مواد غذایی روزمره"]

SYSTEM = """تو تولیدکننده محتوای تغذیه برای اینستاگرام فارسی هستی. محور این هفته: «{axis}».
- {fmt}
- فقط اطلاعات عمومی و پذیرفته‌شده تغذیه؛ بدون ادعای درمانی، بدون وعده کاهش وزن مشخص، بدون آمار و نام پژوهش مطمئن‌نشده.
- لحن ساده و دوستانه؛ لینک و آی‌دی نساز. در پایان یک جمله «مشورت با متخصص» بیاور.
- خط اول خروجی دقیقاً «TOPIC: موضوع کوتاه» باشد، سپس محتوا.
- موضوعات اخیر که نباید تکرار شوند: {recent}
{extra}"""

FORMATS = {
    "reel": "خروجی: ریلز ۳۰ تا ۴۵ ثانیه‌ای. بخش «SCENES:» با ۴ تا ۶ صحنه (هر صحنه یک خط: توصیف تصویر انیمیشنی + متن روی صفحه)، سپس «CAPTION:» حداکثر ۴ خط با ۳ تا ۵ هشتگ.",
    "carousel": "خروجی: پست اسلایدی. بخش «SLIDES:» با ۶ اسلاید (هر اسلاید یک خط کوتاه، اسلاید اول عنوان)، سپس «CAPTION:» حداکثر ۴ خط با ۳ تا ۵ هشتگ.",
}
_TOPIC = re.compile(r"^TOPIC:\s*(.+)$", re.M)


def week_axis(now=None) -> str:
    return AXES[(now or utcnow()).isocalendar()[1] % 4]


@register("instagram_nutrition")
class InstagramNutritionAgent(Agent):
    def __init__(self, db=None):
        self.db = db

    def generate(self, cfg, task, payload):
        if task not in FORMATS:
            raise ValueError(f"وظیفه نامعتبر: {task}")
        axis = week_axis()
        rows = self.db.all("SELECT title FROM content_items WHERE agent_key=? AND title!='' "
                           "ORDER BY created_at DESC LIMIT 15", (cfg["key"],)) if self.db else []
        recent = "؛ ".join(r["title"] for r in rows) or "ندارد"
        extra = f"دستور تکمیلی:\n{cfg['prompt']}" if cfg.get("prompt") else ""
        out = llm.get_client().complete(
            SYSTEM.format(axis=axis, fmt=FORMATS[task], recent=recent, extra=extra),
            f"یک محتوای {'ریلز' if task == 'reel' else 'اسلایدی'} جدید بنویس.", max_tokens=1500)
        m = _TOPIC.search(out)
        marker = "SCENES:" if task == "reel" else "SLIDES:"
        if not m or marker not in out or "CAPTION:" not in out:
            raise ValueError("خروجی مدل قالب TOPIC/محتوا/CAPTION را رعایت نکرد")  # queue retries
        body = out[m.end():].strip()
        return [Draft(body=body, title=m.group(1).strip()[:100], kind=task,
                      meta={"axis": axis, "media": "not_generated"},
                      review_reasons=["کنترل علمی محتوای تغذیه پیش از انتشار"])]
