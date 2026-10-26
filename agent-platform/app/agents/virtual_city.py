"""Virtual City content agent: population reel/story and the weekly 10-citizen feature.

Facts (population, ids, jobs) always come from the database and are passed to the
model as data; the model only writes the wording. The population figure is the
count of VERIFIED, currently eligible accounts - never the raw follower count.
Anything that shows citizens' ids/jobs always goes through human approval.
"""
from datetime import timedelta

from .. import city, llm
from ..util import utcnow
from .base import Agent, Draft, register

SYSTEM = """تو نویسنده محتوای یک «شهر داستانی» در اینستاگرام هستی. شهروندان دنبال‌کنندگان واقعی پیج‌اند.
لحن: گرم، محترمانه و شاد. هرگز تمسخر، قضاوت، ویژگی منفی یا اطلاعات شخصی نیاور.
فقط از داده‌هایی که داده می‌شود استفاده کن؛ عدد، آیدی یا شغل از خودت نساز."""


def week_key(now) -> str:
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


@register("virtual_city")
class VirtualCityAgent(Agent):
    def __init__(self, db=None):
        self.db = db

    def generate(self, cfg, task, payload):
        client = llm.get_client()
        st = city.stats(self.db)
        extra = f"\n{cfg['prompt']}" if cfg["prompt"] else ""
        if task in ("reel", "story"):
            kind = "ریلز" if task == "reel" else "استوری"
            text = client.complete(
                SYSTEM + extra,
                f"یک متن کوتاه برای {kind} بنویس که جمعیت فعلی شهر را اعلام کند.\n"
                f"جمعیت شهر (حساب‌های تأییدشده): {st['population']:,} نفر از ظرفیت {st['cap']:,}.\n"
                "فقط همین عدد را بگو. خط اول عنوان، بقیه کپشن.")
            title, _, body = text.partition("\n")
            return [Draft(body=body.strip() or title, title=title.strip()[:100], kind=task,
                          meta={"population": st["population"]})]
        if task == "weekly_selection":
            now = payload.get("now") or utcnow()
            key = payload.get("week_key") or week_key(now)
            picked = city.select_weekly(self.db, key, now - timedelta(days=7), now)
            if not picked:
                return []
            lines = "\n".join(f"- {p['ig_id']}" + (f" | شغل: {p['job']}" if p["job"] else " | (شغل اعلام نکرده)")
                              for p in picked)
            text = client.complete(
                SYSTEM + extra,
                f"برای شهروندان این هفته یک معرفی کوتاه (یک یا دو جمله) بنویس. اگر شغل ندارد فقط خوشامد بگو.\n"
                f"فرمت هر خط: آیدی: معرفی\n{lines}")
            shown = [p["ig_id"] for p in picked]
            return [Draft(body=text.strip(), title=f"شهروندان هفته {key}", kind="reel",
                          meta={"week_key": key, "selected": shown},
                          review_reasons=["نمایش آیدی/شغل کاربران؛ بازبینی نهایی پیش از انتشار"])]
        raise NotImplementedError(f"وظیفه ناشناخته برای شهر مجازی: {task}")
