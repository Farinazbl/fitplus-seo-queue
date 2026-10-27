"""FitPlus daily post: text + natural photo + branded card (logo, Yekan Bakh text), for the Bale channel.

Weekly rotation of formats (by weekday): recipe, healthy food, exercise, vegetable benefits, diet tip, ...
Goes through the approval queue (a channel admin approves each post), then Bale ``sendPhoto``.
Settings in ``policy``: ``channel_tag`` (printed on the image), ``footer`` (house footer appended to the caption).
"""
import re
import uuid

from .. import imaging, llm
from ..util import utcnow
from .base import Agent, Draft, register

FORMATS = ["دستور غذایی سالم (مواد لازم و روش کوتاه)", "معرفی یک غذای سالم و دلیل خوب بودنش",
           "یک حرکت یا برنامه ورزشی ساده برای مبتدی‌ها", "خواص یک سبزی یا میوه (فقط ادعاهای عمومی و محتاطانه)",
           "یک نکته کاربردی رژیم و تغذیه", "دستور غذایی سالم (مواد لازم و روش کوتاه)",
           "یک عادت ساده برای زندگی سالم"]
SYSTEM = """تو تولیدکننده محتوای کانال بله اپلیکیشن ورزشی فیت‌پلاس هستی. قالب امروز: {fmt}.
خروجی دقیقاً با این برچسب‌ها، هر کدام در یک خط جدا (CAPTION تا آخر ادامه دارد):
TOPIC: موضوع کوتاه
CARD: جمله کوتاه روی تصویر (حداکثر ۱۲ کلمه)
IMAGE: توضیح انگلیسی دقیق یک عکس حرفه‌ای (۲ تا ۳ جمله): نام دقیق مواد غذایی واقعی و تازه، ظرف و سطح چیدمان، زاویه دوربین؛ بدون متن روی عکس
CAPTION: متن پست (حداکثر ۷۰۰ نویسه، فوتر کانال را خودت ننویس)، ساده و دوستانه، یک ایموجی مناسب در شروع
- ادعای درمانی قطعی، وعده کاهش وزن مشخص و آمار یا پژوهش مطمئن‌نشده ننویس. دارو و درمان توصیه نکن.
- لینک نساز. آخر کپشن یک جمله «برای شرایط خاص با پزشک یا متخصص مشورت کنید» بیاور.
سبک کانال (دوستانه، خودمانی، کوتاه و کاربردی؛ مثل نمونه‌های زیر):
«سالاد پروتئینی ۱۰ دقیقه‌ای — مواد لازم: کاهو و سبزیجات برگ‌سبز، گوجه، خیار و فلفل رنگی، یک کف دست مرغ گریل یا نخود پخته، کمی پنیر کم‌چرب، یک قاشق روغن زیتون + آبلیمو. همه رو مخلوط کن؛ یک وعده‌ی سبک، سیرکننده و پروتئینی آماده‌ست.»
«فلفل دلمه‌ای رنگی؛ ویتامین C بیشتر از پرتقال! — کالری کم و ترد، مناسب میان‌وعده؛ خام توی سالاد یا تفت سریع کنار مرغ. هرچی رنگ بشقابت متنوع‌تر، مواد مغذی متنوع‌تر.»
قواعد کیفیت: اولین خط یک تیتر جذاب و مشخص (نه کلی‌گویی)؛ ۳ تا ۵ خط کوتاه با مقدار، زمان یا ترتیب مشخص (مثلاً «یک کف دست مرغ»، «۳ ست ۱۲ تایی»)؛ یک ترفند عملی که همین امروز بشود انجامش داد؛ بدون جمله‌های کلی مثل «سلامتی مهم است».
- موضوعات اخیر که تکرار نشوند: {recent}
{extra}"""
POSTER_SYSTEM = """تو تولیدکننده محتوای کانال بله اپلیکیشن ورزشی فیت‌پلاس هستی. قالب امروز: {fmt}.
خروجی دقیقاً با این برچسب‌ها، هر کدام در یک خط جدا (CAPTION تا آخر ادامه دارد):
TOPIC: موضوع کوتاه
TITLE: تیتر اصلی پوستر (۲ تا ۴ کلمه، جذاب و مشخص)
SUBTITLE: زیرتیتر (حداکثر ۸ کلمه)
FACT1: عنوان دو کلمه‌ای | توضیح کوتاه (حداکثر ۷ کلمه)
FACT2: عنوان | توضیح
FACT3: عنوان | توضیح
FACT4: عنوان | توضیح
TIP: یک نکته‌ی مهم یا ترفند عملی (حداکثر ۱۸ کلمه)
IMAGE: English, 2 sentences: the exact real photograph for the background (real fresh whole ingredients on a rustic table with soft window light and blurred green plants; or, for exercise topics, a fit adult man fully clothed in a modern gym performing the move)
CAPTION: متن پست (حداکثر ۶۰۰ نویسه، فوتر کانال را خودت ننویس)، ساده و دوستانه، یک ایموجی در شروع
- ادعای درمانی قطعی، وعده کاهش وزن مشخص و آمار یا پژوهش مطمئن‌نشده ننویس. دارو توصیه نکن. لینک نساز.
- متن‌های پوستر باید کوتاه، دقیق و املای فارسی کاملاً درست باشند؛ عدد فقط وقتی مقدار معمولی و مطمئن است (مثل ۳ ست، ۳۰ ثانیه).
- موضوعات اخیر که تکرار نشوند: {recent}
{extra}"""
POSTER_PROMPT = """Design ONE complete, professional Persian (Farsi, right-to-left) social-media infographic poster, portrait 2:3, in the style of a premium health & fitness brand.
Background: a real, bright, high-end photograph: {image} Soft depth of field, natural light, rich true colors.
Layout (all text in Persian, bold clean modern sans typeface, exactly as written):
1. Top: a big bold title on a deep purple (#5B2FD0) painted brush-stroke banner, white text: "{title}". Directly below it a lime-green (#B5DB3A) strip with dark text: "{subtitle}".
2. Top-right corner: the brand logo, a purple dumbbell icon with a small smile, with the wordmark "fitplusapp" in white/purple.
3. Left column: four rounded translucent white glass cards stacked, each with a glossy circular colored icon on the right side and a bold heading plus a one-line description:
   - {f1}
   - {f2}
   - {f3}
   - {f4}
4. A wide rounded note box with a lightbulb icon: "نکته مهم: {tip}".
5. Bottom: a dark indigo (#231B6B) rounded footer pill with the text "آنالیز رایگان و برنامه ورزشی اختصاصی فیت‌پلاس" and the handle "@Fitplusapp" in lime.
Color palette: purple, indigo, lime green, white. Clean spacing, readable contrast, no clutter."""
_TAG = {k: re.compile(rf"^{k}:\s*(.+)$", re.M) for k in ("TOPIC", "CARD", "IMAGE")}


_PTAGS = {k: re.compile(rf"^{k}:\s*(.+)$", re.M) for k in ("TOPIC", "TITLE", "SUBTITLE", "FACT1", "FACT2", "FACT3", "FACT4", "TIP", "IMAGE")}


@register("fitplus_daily")
class FitplusDailyAgent(Agent):
    def __init__(self, db=None):
        self.db = db

    def generate(self, cfg, task, payload, photo_fn=None):
        rows = self.db.all("SELECT title FROM content_items WHERE agent_key=? AND title!='' "
                           "ORDER BY created_at DESC LIMIT 20", (cfg["key"],)) if self.db else []
        fmt = FORMATS[utcnow().weekday()]
        extra = f"دستور تکمیلی:\n{cfg['prompt']}" if cfg.get("prompt") else ""
        if (cfg["policy"].get("style") or "card") == "poster":
            return self._poster(cfg, fmt, rows, extra, photo_fn)
        out = llm.get_client().complete(
            SYSTEM.format(fmt=fmt, recent="؛ ".join(r["title"] for r in rows) or "ندارد", extra=extra),
            "پست امروز را بنویس.", max_tokens=1200)
        found = {k: r.search(out) for k, r in _TAG.items()}
        if not all(found.values()) or "CAPTION:" not in out:
            raise ValueError("خروجی مدل قالب TOPIC/CARD/IMAGE/CAPTION را رعایت نکرد")  # queue retries
        caption = out.split("CAPTION:", 1)[1].strip()
        if len(caption) < 60:
            raise ValueError("کپشن خیلی کوتاه است")
        tag = (cfg["policy"].get("channel_tag") or "").strip()
        footer = (cfg["policy"].get("footer") or "").strip()      # the channel's house footer (CTA + @ids)
        if footer:
            caption = f"{caption}\n\n{footer}"
        try:
            photo = (photo_fn or imaging.generate_photo)(found["IMAGE"].group(1).strip())
            png = imaging.render_card(photo, found["CARD"].group(1).strip(), footer=tag)
        except imaging.ImagingError as e:
            raise ValueError(str(e)) from None
        path = imaging.media_dir(__import__("os").environ.get("APP_DB")) / f"{uuid.uuid4().hex}.png"
        path.write_bytes(png)
        return [Draft(body=caption, title=found["TOPIC"].group(1).strip()[:100], kind="image_post",
                      meta={"image_path": str(path), "format": fmt})]

    def _poster(self, cfg, fmt, rows, extra, poster_fn):
        out = llm.get_client().complete(
            POSTER_SYSTEM.format(fmt=fmt, recent="؛ ".join(r["title"] for r in rows) or "ندارد", extra=extra),
            "پست امروز را بنویس.", max_tokens=1500)
        f = {k: r.search(out) for k, r in _PTAGS.items()}
        if not all(f.values()) or "CAPTION:" not in out:
            raise ValueError("خروجی مدل قالب پوستر را رعایت نکرد")  # queue retries
        caption = out.split("CAPTION:", 1)[1].strip()
        if len(caption) < 60:
            raise ValueError("کپشن خیلی کوتاه است")
        footer = (cfg["policy"].get("footer") or "").strip()
        if footer:
            caption = f"{caption}\n\n{footer}"
        facts = [f[f"FACT{i}"].group(1).strip().replace("|", " - ") for i in range(1, 5)]
        prompt = POSTER_PROMPT.format(image=f["IMAGE"].group(1).strip(), title=f["TITLE"].group(1).strip(),
                                      subtitle=f["SUBTITLE"].group(1).strip(), tip=f["TIP"].group(1).strip(),
                                      f1=facts[0], f2=facts[1], f3=facts[2], f4=facts[3])
        try:
            png = (poster_fn or imaging.generate_poster)(prompt)
        except imaging.ImagingError as e:
            raise ValueError(str(e)) from None
        path = imaging.media_dir(__import__("os").environ.get("APP_DB")) / f"{uuid.uuid4().hex}.png"
        path.write_bytes(png)
        return [Draft(body=caption, title=f["TOPIC"].group(1).strip()[:100], kind="image_post",
                      meta={"image_path": str(path), "format": fmt, "style": "poster"})]
