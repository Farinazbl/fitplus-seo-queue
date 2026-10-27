"""Branded post images: photo (image API) + logo + Persian text card.

Needs Pillow with RAQM (Persian shaping/RTL). Pillow wheels for Windows/Linux include it;
``pip install pillow``. The font must be a real file (the client's Yekan Bakh): APP_FONT.
The image API key is the same OPENAI_API_KEY; model via APP_IMAGE_MODEL.
"""
import base64
import io
import json
import os
from pathlib import Path

from . import http

IMAGES_URL = "https://api.openai.com/v1/images/generations"
ASSETS = Path(__file__).parent / "assets"
STYLE = ("Professional food and fitness photography, shot on a full-frame camera with an 85mm lens, shallow depth of field, "
         "soft natural window light, rich true-to-life colors, crisp detail, appetizing editorial styling on a rustic wood or "
         "stone surface, generous clean negative space in the lower third for text. Real, fresh, whole ingredients with natural "
         "imperfections and water droplets - never plastic, never illustration, never 3D render, no AI-looking gloss.")
SAFETY = ("Natural, high-quality photograph, realistic lighting. If any people appear they must be adults, "
          "fully clothed, preferably men; any woman must wear a hijab and be fully covered. No text, no logos, "
          "no watermarks, no nudity.")


SAFETY_POSTER = ("If any people appear they must be adult men, fully clothed (or a woman fully covered with hijab). "
                 "No nudity. Every Persian word must be spelled exactly as given, with correct letter joining and "
                 "right-to-left order; do not add, drop or invent any text.")


class ImagingError(Exception):
    pass


def media_dir(db_path: str = None) -> Path:
    d = Path(os.environ.get("APP_MEDIA_DIR") or (Path(db_path or "agents.db").resolve().parent / "media"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def generate_photo(prompt: str, transport=http.request) -> bytes:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ImagingError("OPENAI_API_KEY برای ساخت تصویر تنظیم نشده است")
    body = {"model": os.environ.get("APP_IMAGE_MODEL", "gpt-image-2"), "prompt": f"{prompt}\n{STYLE}\n{SAFETY}",
            "size": "1024x1024", "n": 1}
    if body["model"].startswith("gpt-image"):
        body["quality"] = os.environ.get("APP_IMAGE_QUALITY", "high")
    try:
        raw = transport("POST", IMAGES_URL, headers={"Authorization": f"Bearer {key}"}, json_body=body, timeout=180)
        item = json.loads(raw)["data"][0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            raise ImagingError("سرویس تصویر به‌جای فایل، آدرس برگرداند؛ مدل تصویر را عوض کنید")
    except http.HttpError as e:
        raise ImagingError(f"ساخت تصویر ناموفق: {e}") from None
    except (KeyError, IndexError, ValueError, TypeError):
        raise ImagingError("پاسخ نامعتبر از سرویس تصویر") from None
    raise ImagingError("تصویری برنگشت")


def generate_poster(prompt: str, transport=http.request, tries: int = 4) -> bytes:
    """A complete designed infographic poster (photo + Persian text + cards) straight from the image model.

    Asks for JPEG (small response: big PNG downloads get cut on unstable links) and retries dropped connections.
    """
    import time
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ImagingError("OPENAI_API_KEY برای ساخت تصویر تنظیم نشده است")
    body = {"model": os.environ.get("APP_IMAGE_MODEL", "gpt-image-2"), "prompt": f"{prompt}\n{SAFETY_POSTER}",
            "size": os.environ.get("APP_POSTER_SIZE", "1024x1536"), "n": 1,
            "output_format": "jpeg", "output_compression": int(os.environ.get("APP_POSTER_JPEG_Q", "88"))}
    if os.environ.get("APP_IMAGE_QUALITY"):
        body["quality"] = os.environ["APP_IMAGE_QUALITY"]
    last = ""
    for i in range(tries):
        try:
            raw = transport("POST", IMAGES_URL, headers={"Authorization": f"Bearer {key}"}, json_body=body, timeout=240)
            item = json.loads(raw)["data"][0]
            if item.get("b64_json"):
                return base64.b64decode(item["b64_json"])
            raise ImagingError("تصویری برنگشت")
        except http.HttpError as e:
            last = str(e)
            if e.status not in (0, 429) and e.status < 500:      # a real rejection: retrying will not help
                raise ImagingError(f"ساخت پوستر ناموفق: {e}") from None
            time.sleep(min(2 * (i + 1), 8) if tries > 1 and transport is http.request else 0)
        except (KeyError, IndexError, ValueError, TypeError):
            raise ImagingError("پاسخ نامعتبر از سرویس تصویر") from None
        except Exception as e:  # noqa: BLE001 - http.client.IncompleteRead, SSL EOF etc. on a flaky link
            if isinstance(e, ImagingError):
                raise
            last = f"{type(e).__name__}: {e}"
    raise ImagingError(f"ساخت پوستر ناموفق پس از {tries} تلاش: {last}")


def _latin_font(size):
    """The Persian font may lack Latin letters (e.g. BYekan): use a Latin-capable font for @channel."""
    from PIL import ImageFont
    for c in (os.environ.get("APP_FONT_LATIN"), r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial.ttf"):
        if c and Path(c).is_file():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default(size)


def _logo(path, width):
    from PIL import Image
    im = Image.open(path).convert("RGBA")
    px = im.load()
    for y in range(im.height):                       # white background -> transparent
        for x in range(im.width):
            r, g, b, _ = px[x, y]
            px[x, y] = (r, g, b, max(0, min(255, int((255 - min(r, g, b)) * 1.6))))
    im = im.crop(im.getbbox() or (0, 0, im.width, im.height))
    return im.resize((width, max(1, int(im.height * width / im.width))))


def _raqm() -> bool:
    from PIL import features
    return bool(features.check("raqm"))


def _prep(text):
    """With RAQM Pillow shapes/reorders Persian itself; otherwise use arabic-reshaper + python-bidi."""
    if _raqm():
        return text, {"direction": "rtl", "language": "fa"}
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
    except ImportError:
        raise ImagingError("برای متن فارسی روی تصویر: pip install arabic-reshaper python-bidi") from None
    return get_display(arabic_reshaper.reshape(text)), {}


def _font(path, size):
    from PIL import ImageFont
    return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM if _raqm() else ImageFont.Layout.BASIC)


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        shaped, kw = _prep(t)
        if cur and draw.textlength(shaped, font=font, **kw) > max_w:
            lines.append(cur); cur = w
        else:
            cur = t
    return lines + ([cur] if cur else [])


def render_card(photo: bytes, text: str, footer: str = "", logo_path=None, font_path=None, size=1080) -> bytes:
    """Photo with a dark gradient, big Persian text at the bottom, logo top-left, @channel footer."""
    from PIL import Image, ImageDraw
    font_path = font_path or os.environ.get("APP_FONT")
    if not font_path or not Path(font_path).is_file():
        raise ImagingError("فونت پیدا نشد؛ مسیر فایل فونت یکان‌بخ را در APP_FONT بگذارید")
    base = Image.open(io.BytesIO(photo)).convert("RGB")
    side = min(base.size)
    base = base.crop(((base.width - side) // 2, (base.height - side) // 2,
                      (base.width + side) // 2, (base.height + side) // 2)).resize((size, size))
    shade = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for i in range(int(size * 0.55)):                 # bottom gradient for readable text
        a = int(215 * (i / (size * 0.55)) ** 1.4)
        sd.line([(0, size - 1 - (int(size * 0.55) - i)), (size, size - 1 - (int(size * 0.55) - i))], fill=(10, 8, 40, a))
    img = Image.alpha_composite(base.convert("RGBA"), shade)
    d = ImageDraw.Draw(img)
    pad, fs = int(size * 0.07), int(size * 0.062)
    font = _font(font_path, fs)
    lines = _wrap(d, text, font, size - 2 * pad)[:5]
    y = size - pad - int(size * 0.05) - len(lines) * int(fs * 1.55)
    for ln in lines:
        shaped, kw = _prep(ln)
        d.text((size - pad, y), shaped, font=font, fill="white", anchor="ra", **kw,
               stroke_width=max(1, fs // 38), stroke_fill="white")  # light faux-bold: thin Persian fonts stay readable
        y += int(fs * 1.55)
    if footer:
        d.text((size - pad, size - pad), footer, font=_latin_font(int(size * 0.037)), fill=(220, 220, 255), anchor="rs")
    logo = _logo(logo_path or ASSETS / "logo.jpg", int(size * 0.2))
    plate = Image.new("RGBA", (logo.width + 30, logo.height + 30), (255, 255, 255, 215))
    img.alpha_composite(plate, (pad // 2, pad // 2))
    img.alpha_composite(logo, (pad // 2 + 15, pad // 2 + 15))
    out = io.BytesIO()
    img.convert("RGB").save(out, "PNG")
    return out.getvalue()
