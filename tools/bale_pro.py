#!/usr/bin/env python3
"""FitPlus Bale post image, premium template (1080x1350).

Photo of the post's own subject (generated with OpenAI images when OPENAI_API_KEY is set,
otherwise the approved stock photo), cinematic grade, glass headline card, Yekan Bakh font.

  python3 tools/bale_pro.py OUT.jpg "عنوان" "کلمه‌ی هایلایت" "برچسب" "subject in English" [stock_photo_path]
Env: OPENAI_API_KEY (optional), OPENAI_IMAGE_MODEL (default gpt-image-1)
"""
import base64, html, json, os, sys, urllib.request
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = os.path.join(ROOT, 'tools', 'fonts')
W, H = 1080, 1350
RULES = ('no text, no letters, no logos, no watermark. If any person appears: a man, fully clothed in sportswear '
         '(long sleeves/long pants), never shirtless; a woman only with hijab and fully covered.')


def ai_photo(subject, out):
    """Photoreal image of the post subject via OpenAI. Returns path or None."""
    key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not key:
        return None
    prompt = ('Photorealistic premium editorial photography for a fitness brand: %s. Cinematic moody lighting with a '
              'subtle deep-purple and blue rim light, shallow depth of field, rich detail, clear empty space in the '
              'lower half for text, vertical 4:5 composition; %s' % (subject, RULES))
    body = json.dumps({'model': os.environ.get('OPENAI_IMAGE_MODEL', 'gpt-image-1'), 'prompt': prompt,
                       'size': '1024x1536', 'quality': 'high', 'n': 1}).encode()
    req = urllib.request.Request('https://api.openai.com/v1/images/generations', data=body,
                                 headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            b64 = json.loads(r.read())['data'][0]['b64_json']
        open(out, 'wb').write(base64.b64decode(b64))
        return out
    except Exception as e:
        print('openai image failed:', e, file=sys.stderr)
        return None


def render(out, title, hl, tag, photo):
    e, h = html.escape(title), html.escape(hl)
    head = e.replace(h, '<em>%s</em>' % h, 1) if h in e else e
    size = 104 if len(title) < 20 else (90 if len(title) < 30 else 78)
    logo = os.path.join(ROOT, 'tools', 'logo-white.png')
    page = """<html><head><meta charset=utf-8><style>
@font-face{font-family:YB;font-weight:700;src:url('file://%(f)s/YB-Bold.ttf')}
@font-face{font-family:YB;font-weight:900;src:url('file://%(f)s/YB-Heavy.ttf')}
*{box-sizing:border-box}html,body{margin:0;width:%(W)dpx;height:%(H)dpx;font-family:YB;direction:rtl}
.st{position:relative;width:%(W)dpx;height:%(H)dpx;overflow:hidden;background:#0a0620}
.ph{position:absolute;inset:-20px;background:url('%(p)s') center 35%%/cover;filter:saturate(1.12) contrast(1.06)}
.sh{position:absolute;inset:0;background:
 radial-gradient(120%% 70%% at 50%% 0%%,rgba(124,92,255,.28),transparent 60%%),
 linear-gradient(180deg,rgba(10,6,32,.55) 0%%,rgba(10,6,32,0) 22%%,rgba(10,6,32,.15) 45%%,rgba(10,6,32,.92) 100%%)}
.top{position:absolute;top:56px;right:60px;left:60px;display:flex;justify-content:space-between;align-items:center}
.pill{background:rgba(255,255,255,.16);backdrop-filter:blur(14px);border:1.5px solid rgba(255,255,255,.35);
 color:#fff;font-weight:900;font-size:32px;padding:10px 30px;border-radius:999px}
.brand{display:flex;align-items:center;gap:14px;color:#fff;font-weight:900;font-size:40px}.brand img{height:46px}
.card{position:absolute;right:56px;left:56px;bottom:190px;padding:46px 50px 50px;border-radius:48px;
 background:linear-gradient(160deg,rgba(255,255,255,.20),rgba(255,255,255,.07));
 backdrop-filter:blur(26px);border:2px solid rgba(255,255,255,.32);box-shadow:0 40px 80px -30px rgba(0,0,0,.7)}
.card:before{content:"";display:block;width:96px;height:10px;border-radius:10px;background:#D7FF3A;margin-bottom:26px}
h1{margin:0;color:#fff;font-weight:900;font-size:%(sz)dpx;line-height:1.35;text-shadow:0 6px 24px rgba(0,0,0,.45)}
h1 em{font-style:normal;color:#D7FF3A}
.bar{position:absolute;right:56px;left:56px;bottom:56px;height:96px;border-radius:30px;display:flex;align-items:center;
 justify-content:center;padding:0 38px;color:#fff;font-weight:900;font-size:34px;
 background:rgba(255,255,255,.14);backdrop-filter:blur(22px);border:1.5px solid rgba(255,255,255,.3)}
.id{direction:ltr;font-weight:900;color:#D7FF3A}
</style></head><body><div class="st"><div class="ph"></div><div class="sh"></div>
<div class="top"><span class="brand"><img src="file://%(logo)s">فیت‌پلاس</span><span class="pill">%(tag)s</span></div>
<div class="card"><h1>%(head)s</h1></div>
<div class="bar"><span>نصب رایگان از کافه‌بازار و مایکت</span></div>
</div></body></html>""" % dict(f=FONTS, W=W, H=H, p=photo, sz=size, logo=logo, tag=html.escape(tag), head=head)
    tmp = out + '.html'
    open(tmp, 'w', encoding='utf-8').write(page)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': W, 'height': H})
        pg.goto('file://' + os.path.abspath(tmp))
        pg.wait_for_timeout(600)
        pg.screenshot(path=out, type='jpeg', quality=93)
        b.close()
    os.remove(tmp)


if __name__ == '__main__':
    out, title, hl, tag, subject = sys.argv[1:6]
    stock = sys.argv[6] if len(sys.argv) > 6 else None
    bg = ai_photo(subject, out + '.bg.png') or stock
    assert bg, 'no OPENAI_API_KEY and no stock photo given'
    render(out, title, hl, tag, 'file://' + os.path.abspath(bg))
