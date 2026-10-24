#!/usr/bin/env python3
"""FitPlus Bale/Telegram channel post images, 4 design families that rotate every few days.

  A 'poster'   – full-bleed real (modest) photo, dark gradient, big bold headline (Gymshark / Nike style)
  B 'neon'     – black, electric-lime accents, huge number/keyword, sport stripes (training-app style)
  C 'wellness' – soft pastel card, rounded shapes, friendly icon (wellness-app style)
  D 'magazine' – split layout: photo half + brand-blue half with headline (fitness-magazine style)

Usage: python3 tools/channel_posts.py OUT_DIR [theme] [post]
Writes OUT_DIR/<theme>/post-NN.jpg (1080x1350, 4:5 – the format feeds favour).
Only photos from photos/approved.json are used (modest: men fully clothed, women only with hijab).
"""
import html, json, os, sys
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = os.path.join(ROOT, 'tools', 'fonts')
FA = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
THEMES = ['poster', 'classic', 'neon', 'wellness', 'circle', 'magazine']
W, H = 1080, 1350

# n: (headline, highlight word, tag, photo topic, big number/keyword)
META = {
    1: ('۳ اشتباه رایج تازه‌کارها', 'اشتباه', 'نکته', 'workout', '۳'),
    2: ('گرم کردن ۵ دقیقه‌ای قبل از هر تمرین', 'گرم کردن', 'تمرین', 'workout', '۵\''),
    3: ('حرکات شکم، چربی شکم رو آب نمی‌کنه', 'چربی شکم', 'باور غلط', 'fat-loss', '✕'),
    4: ('عضله توی خواب ساخته می‌شه', 'خواب', 'ریکاوری', 'recovery', '۸h'),
    5: ('آب کافی، اثر بزرگ', 'آب کافی', 'عادت', 'recovery', '۲L'),
    6: ('تمرین در خونه بدون وسیله', 'بدون وسیله', 'تمرین', 'home-workout', '۴×۳'),
    7: ('چرا بیشتر آدم‌ها ورزش رو ول می‌کنن؟', 'ول می‌کنن', 'انگیزه', 'app', '؟'),
    8: ('وزنه زدن خانم‌ها رو حجیم نمی‌کنه', 'حجیم', 'باور غلط', 'workout', '✕'),
    9: ('۳۰ دقیقه پیاده‌روی رو دست‌کم نگیر', 'پیاده‌روی', 'شروع', 'cardio', '۳۰\''),
    10: ('انگیزه می‌ره، عادت می‌مونه', 'عادت', 'ذهن', 'workout', '۲۱'),
    11: ('پروتئین رو توی همه‌ی وعده‌ها پخش کن', 'پروتئین', 'تغذیه', 'nutrition', 'P+'),
    12: ('۵ دقیقه کشش بعد از تمرین', 'کشش', 'ریکاوری', 'recovery', '۵\''),
    13: ('چالش ۷ روزه‌ی پلانک', 'چالش', 'چالش', 'home-workout', '۷'),
    14: ('عرق بیشتر = چربی‌سوزی بیشتر؟ نه!', 'نه!', 'باور غلط', 'cardio', '✕'),
    15: ('۳ حرکت برای پشت‌میزنشین‌ها', 'پشت‌میزنشین‌ها', 'سلامت', 'home-workout', '۳'),
    16: ('ترازو همه‌ی ماجرا رو نمی‌گه', 'ترازو', 'نکته', 'fat-loss', 'kg'),
    17: ('فقط ۳۰ روز به خودت فرصت بده', '۳۰ روز', 'انگیزه', 'app', '۳۰'),
    18: ('ورزش ناشتا معجزه نمی‌کنه', 'ناشتا', 'باور غلط', 'cardio', '✕'),
    19: ('میان‌وعده‌های ساده و سالم', 'میان‌وعده', 'تغذیه', 'nutrition', '۵'),
    20: ('استراحت بین ست‌ها چقدر باشه؟', 'استراحت', 'تمرین', 'workout', '۶۰"'),
    21: ('اسکوات درست چه شکلیه؟', 'اسکوات', 'تکنیک', 'home-workout', '۹۰°'),
    22: ('وزنت ثابت مونده؟ نگران نباش', 'ثابت', 'کاهش وزن', 'fat-loss', '—'),
    23: ('وقت نداری؟ ۲۰ دقیقه کافیه', '۲۰ دقیقه', 'تمرین', 'workout', '۲۰\''),
    24: ('برای لاغری نون و برنج رو حذف نکن', 'حذف نکن', 'باور غلط', 'nutrition', '✕'),
    25: ('ورزش، ضداسترس طبیعی', 'ضداسترس', 'ذهن', 'cardio', '۱۵\''),
    26: ('«لاغر شدن» هدف نیست!', 'هدف', 'هدف‌گذاری', 'app', '🎯'),
    27: ('کفش ورزشی مناسب داری؟', 'کفش', 'نکته', 'cardio', '👟'),
    28: ('تمرین اینتروال ۱۲ دقیقه‌ای', 'اینتروال', 'تمرین', 'home-workout', '۱۲\''),
    29: ('یک روز تمرین نرفتی؟ چیزی خراب نشده', 'خراب نشده', 'باور غلط', 'workout', '۱'),
    30: ('یک ماه کامل با هم بودیم!', 'یک ماه', 'فیت‌پلاس', 'app', '۳۰'),
}

PHOTO = {1: 177, 2: 203, 3: 47, 4: 184, 5: 208, 6: 49, 7: 215, 8: 24, 9: 79, 10: 172, 11: 94, 12: 182, 13: 170,
         14: 187, 15: 217, 16: 171, 17: 176, 18: 186, 19: 7, 20: 179, 21: 175, 22: 204, 23: 173, 24: 8, 25: 197,
         26: 218, 27: 110, 28: 188, 29: 180, 30: 148}
sys.path.insert(0, os.path.join(ROOT, 'telegram'))
import new_posts
for _i, (_b, _t, _h, _tag, _ph, _big) in enumerate(new_posts.NEW, 31):
    META[_i] = (_t, _h, _tag, 'nutrition', _big)
    PHOTO[_i] = _ph

LOGO = ('<svg viewBox="120 360 930 300" aria-hidden="true"><rect x="133" y="450" width="62" height="102" rx="6" fill="{s}"/>'
        '<rect x="221" y="369" width="98" height="265" rx="6" fill="{m}"/>'
        '<path d="M370 436q0-8 8-8h70q8 0 8 8A128 137 0 0 0 712 436q0-8 8-8h72q8 0 8 8A215 219 0 0 1 370 436Z" fill="{m}"/>'
        '<rect x="851" y="369" width="98" height="265" rx="6" fill="{m}"/><rect x="976" y="450" width="62" height="102" rx="6" fill="{s}"/></svg>')


def logo(main, side):
    light = main.lower() in ('#fff', '#ffffff')
    return '<img src="file://%s" alt="">' % os.path.join(ROOT, 'tools', 'logo-white.png' if light else 'logo-color.png')


def headline(meta):
    t, hl = meta[0], meta[1]
    e = html.escape(t)
    h = html.escape(hl)
    return e.replace(h, '<em>%s</em>' % h, 1) if h in e else e


IDX = json.load(open(os.path.join(ROOT, 'photos', 'index.json'), encoding='utf-8'))
APPROVED = set(json.load(open(os.path.join(ROOT, 'photos', 'approved.json'), encoding='utf-8')))


def pick_photo(n):
    f = IDX[PHOTO[n]]['file']
    assert f in APPROVED, 'photo for post %d is not approved: %s' % (n, f)
    return 'file://' + os.path.join(ROOT, f)


POSTS = json.load(open(os.path.join(ROOT, 'telegram', 'posts.json'), encoding='utf-8'))


def subtitle(n):
    import re
    lines = [l for l in POSTS[n - 1].split('\n') if l.strip()][1:]
    for l in lines:
        c = re.sub('[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u25AA-\u25FE]', '', l)
        c = re.sub(r'^[\s\-•▫·*۰-۹0-9.:)]+', '', c).strip()
        if len(c) > 10:
            return c if len(c) <= 46 else c[:44].rsplit(' ', 1)[0] + '…'
    return ''


def page(theme, n, meta, photo):
    faces = ''.join("@font-face{font-family:Vaz;font-weight:%s;src:url('file://%s/Vazirmatn-%s.woff2')}" % (w, FONTS, f)
                    for w, f in (('400', 'Regular'), ('500', 'Medium'), ('700', 'Bold'), ('800', 'ExtraBold')))
    hl, tag, big = headline(meta), html.escape(meta[2]), html.escape(meta[4])
    num = str(n).translate(FA)
    tsz = 92 if len(meta[0]) < 22 else (80 if len(meta[0]) < 30 else 70)
    base = ('%s *{box-sizing:border-box}html,body{margin:0;width:%dpx;height:%dpx;overflow:hidden;font-family:Vaz}'
            '.st{position:relative;width:%dpx;height:%dpx;overflow:hidden}.lg{display:flex;align-items:center;gap:14px;font-weight:800;font-size:34px}'
            '.lg img{height:44px;width:auto}.lg{font-size:38px}.id{direction:ltr;font-weight:800}' % (faces, W, H, W, H))
    if theme == 'poster':
        css = base + """
.ph{position:absolute;inset:0;background:url('%s') center 30%%/cover}
.sh{position:absolute;inset:0;background:linear-gradient(180deg,rgba(8,6,30,.15) 0%%,rgba(8,6,30,0) 30%%,rgba(8,6,30,.55) 55%%,rgba(8,6,30,.96) 100%%)}
.top{position:absolute;top:56px;right:60px;left:60px;display:flex;justify-content:space-between;align-items:center;color:#fff}
.tag{background:#D7FF3A;color:#0B0730;font-weight:800;font-size:30px;padding:10px 26px;border-radius:999px}
.bt{position:absolute;right:60px;left:60px;bottom:70px;color:#fff}
h1{margin:0 0 26px;font-size:%dpx;font-weight:800;line-height:1.35}
h1 em{font-style:normal;color:#D7FF3A}
.row{display:flex;justify-content:space-between;align-items:center;font-size:30px;font-weight:700;opacity:.92}
.cta{background:#fff;color:#140A9A;border-radius:999px;padding:14px 30px;font-weight:800}
.no{position:absolute;top:150px;left:60px;font-size:40px;font-weight:800;color:rgba(255,255,255,.85);direction:ltr}
""" % (photo, tsz)
        body = """<div class="ph"></div><div class="sh"></div>
<div class="top"><span class="lg">%s<span>فیت‌پلاس</span></span><span class="tag">%s</span></div>
<div class="bt"><h1>%s</h1><div class="row"><span class="cta">برنامه اختصاصی رایگان ←</span><span class="id">@fitplusapp</span></div></div>""" % (
            logo('#fff', '#D7FF3A'), tag, hl)
    elif theme == 'neon':
        css = base + """
.st{background:#0A0A0F;color:#fff}
.str{position:absolute;width:1600px;height:90px;background:#D7FF3A;transform:rotate(-14deg);top:1010px;left:-260px;opacity:.95}
.str2{position:absolute;width:1600px;height:22px;background:#7C5CFF;transform:rotate(-14deg);top:1140px;left:-260px}
.pic{position:absolute;top:150px;right:60px;left:60px;height:440px;border-radius:36px;background:url('%s') center 35%%/cover;border:5px solid #D7FF3A}
.big{position:absolute;top:470px;left:90px;font-size:150px;font-weight:800;line-height:1;color:#D7FF3A;direction:ltr;text-shadow:0 8px 30px rgba(0,0,0,.6)}
.top{position:absolute;top:56px;right:60px;left:60px;display:flex;justify-content:space-between;align-items:center}
.tag{border:3px solid #D7FF3A;color:#D7FF3A;font-weight:800;font-size:30px;padding:8px 24px;border-radius:12px}
h1{position:absolute;right:60px;left:60px;top:650px;margin:0;font-size:%dpx;font-weight:800;line-height:1.4}
h1 em{font-style:normal;background:#D7FF3A;color:#0A0A0F;padding:0 12px;border-radius:10px}
.ft{position:absolute;bottom:50px;right:60px;left:60px;display:flex;justify-content:space-between;align-items:center;font-size:30px;font-weight:700}
.cta{color:#0A0A0F;background:#fff;border-radius:14px;padding:14px 28px;font-weight:800}
""" % (photo, tsz)
        body = """<div class="str"></div><div class="pic"></div><div class="big">%s</div><div class="str2"></div>
<div class="top"><span class="lg">%s<span>فیت‌پلاس</span></span><span class="tag">%s</span></div>
<h1>%s</h1><div class="ft"><span class="cta">آنالیز رایگان بدن ←</span><span class="id">@fitplusapp</span></div>""" % (
            big, logo('#fff', '#D7FF3A'), tag, hl)
    elif theme == 'wellness':
        pal = [('#FFE8DC', '#FF7A59'), ('#DDF7EC', '#10B981'), ('#E8E6FF', '#5B4BFF'), ('#FFF3C9', '#F59E0B')][n % 4]
        css = base + """
.st{background:%s;color:#1B1838}
.b1{position:absolute;width:640px;height:640px;border-radius:50%%;background:%s;opacity:.16;top:-220px;left:-200px}
.b2{position:absolute;width:300px;height:300px;border-radius:50%%;background:#fff;opacity:.6;bottom:260px;right:-90px}
.top{position:absolute;top:60px;right:64px;left:64px;display:flex;justify-content:space-between;align-items:center;color:#140A9A}
.tag{background:#fff;color:%s;font-weight:800;font-size:30px;padding:10px 26px;border-radius:999px;box-shadow:0 8px 20px -10px rgba(0,0,0,.2)}
.card{position:absolute;top:250px;right:64px;left:64px;bottom:250px;background:#fff;border-radius:56px;box-shadow:0 40px 80px -40px rgba(30,20,90,.35);overflow:hidden}
.pic{position:absolute;top:0;left:0;right:0;height:52%%;background:url('%s') center 30%%/cover}
.txt{position:absolute;top:52%%;right:0;left:0;bottom:0;padding:44px 56px;display:flex;flex-direction:column;justify-content:center}
h1{margin:0;font-size:%dpx;font-weight:800;line-height:1.4}
h1 em{font-style:normal;color:%s}
.ft{position:absolute;bottom:90px;right:64px;left:64px;display:flex;justify-content:space-between;align-items:center;font-size:30px;font-weight:700;color:#140A9A}
.cta{background:%s;color:#fff;border-radius:999px;padding:16px 32px;font-weight:800}
""" % (pal[0], pal[1], pal[1], photo, tsz - 14, pal[1], pal[1])
        body = """<div class="b1"></div><div class="b2"></div>
<div class="top"><span class="lg">%s<span>فیت‌پلاس</span></span><span class="tag">%s</span></div>
<div class="card"><div class="pic"></div><div class="txt"><h1>%s</h1></div></div>
<div class="ft"><span class="cta">شروع رایگان ←</span><span class="id">@fitplusapp</span></div>""" % (
            logo('#140A9A', '#5B4BFF'), tag, hl)
    elif theme == 'classic':  # the original FitPlus look (blue/white cards), now with matching photo
        dark = n % 2 == 0
        bg, fg, sub, card = ('#1A0FB8', '#fff', '#D4CCFF', '#fff') if dark else ('#EEEDFF', '#14122B', '#4B4870', '#1A0FB8')
        css = base + """
.st{background:%s;color:%s}
.wm{position:absolute;left:-120px;top:520px;width:900px;opacity:.07}
.top{position:absolute;top:60px;right:64px;left:64px;display:flex;justify-content:space-between;align-items:center}
.tag{background:%s;color:%s;font-weight:800;font-size:28px;padding:8px 24px;border-radius:999px}
.hd{position:absolute;top:190px;right:64px;left:64px;height:420px;display:flex;flex-direction:column;justify-content:center}
h1{margin:0;font-size:%dpx;font-weight:800;line-height:1.4}
.ln{width:110px;height:9px;border-radius:9px;background:%s;margin:30px 0 24px}
.sub{font-size:36px;font-weight:500;color:%s}
.pic{position:absolute;top:640px;right:64px;left:64px;height:430px;border-radius:34px;background:url('%s') center 35%%/cover;box-shadow:0 30px 60px -30px rgba(0,0,0,.45)}
.bar{position:absolute;bottom:60px;right:64px;left:64px;background:%s;border-radius:22px;padding:24px 32px;display:flex;justify-content:space-between;align-items:center;font-size:30px;font-weight:700;color:%s}
""" % (bg, fg, card, bg, tsz - 6, fg if dark else '#1A0FB8', sub, photo, card, bg)
        body = """<img class="wm" src="file://%s" alt="">
<div class="top"><span class="lg" style="color:%s">%s<span>فیت‌پلاس</span></span><span class="tag">%s</span></div>
<div class="hd"><h1>%s</h1><div class="ln"></div><div class="sub">%s</div></div><div class="pic"></div>
<div class="bar"><span>نصب رایگان از کافه‌بازار و مایکت</span><span class="id">@fitplusapp</span></div>""" % (
            os.path.join(ROOT, 'tools', 'logo-white.png' if dark else 'logo-color.png'), fg,
            logo('#fff' if dark else '#140A9A', ''), tag, html.escape(meta[0]), html.escape(subtitle(n)))
    elif theme == 'circle':  # clean white, round photo, big headline (minimal studio look)
        acc = ['#FF6B35', '#1A0FB8', '#00A878', '#E63946'][n % 4]
        css = base + """
.st{background:#FFFFFF;color:#111}
.ring{position:absolute;width:760px;height:760px;border-radius:50%%;border:22px solid %s;top:110px;left:-190px;opacity:.18}
.pic{position:absolute;width:620px;height:620px;border-radius:50%%;top:180px;left:-80px;background:url('%s') center 35%%/cover;box-shadow:0 40px 80px -40px rgba(0,0,0,.5)}
.top{position:absolute;top:60px;right:64px;left:64px;display:flex;justify-content:space-between;align-items:center;color:#140A9A}
.tag{color:%s;font-weight:800;font-size:30px;border-bottom:5px solid %s;padding-bottom:4px}
.big{position:absolute;top:230px;right:64px;font-size:170px;font-weight:800;color:%s;line-height:1;direction:ltr}
h1{position:absolute;top:850px;right:64px;left:64px;margin:0;font-size:%dpx;font-weight:800;line-height:1.4}
h1 em{font-style:normal;color:%s}
.sub{position:absolute;top:1080px;right:64px;left:64px;font-size:34px;font-weight:500;color:#555}
.ft{position:absolute;bottom:56px;right:64px;left:64px;display:flex;justify-content:space-between;align-items:center;font-size:30px;font-weight:700;color:#140A9A}
.cta{background:%s;color:#fff;border-radius:999px;padding:14px 30px;font-weight:800}
""" % (acc, photo, acc, acc, acc, tsz - 8, acc, acc)
        body = """<div class="ring"></div><div class="pic"></div><div class="big">%s</div>
<div class="top"><span class="lg">%s<span>فیت‌پلاس</span></span><span class="tag">%s</span></div>
<h1>%s</h1><div class="sub">%s</div><div class="ft"><span class="cta">برنامه رایگان ←</span><span class="id">@fitplusapp</span></div>""" % (
            big, logo('#140A9A', ''), tag, hl, html.escape(subtitle(n)))
    else:  # magazine
        css = base + """
.st{background:#140A9A;color:#fff}
.ph{position:absolute;top:0;left:0;right:0;height:640px;background:url('%s') center 40%%/cover}
.ph:after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(20,10,154,.35) 0%%,rgba(20,10,154,0) 30%%,rgba(20,10,154,0) 70%%,#140A9A 100%%)}
.hd{position:absolute;top:50px;right:60px;left:60px;display:flex;justify-content:space-between;align-items:center}
.issue{background:rgba(0,0,0,.35);border-radius:999px;padding:8px 22px;font-size:26px;font-weight:700}
.side{position:absolute;top:600px;right:60px;left:60px;bottom:60px;display:flex;flex-direction:column}
.tag{align-self:flex-start;background:#FF5C7A;color:#fff;font-weight:800;font-size:28px;padding:8px 22px;border-radius:8px}
h1{margin:26px 0 0;font-size:%dpx;font-weight:800;line-height:1.4}
h1 em{font-style:normal;color:#FFD84D}
.line{width:120px;height:10px;background:#FFD84D;border-radius:10px;margin-top:30px}
.ft{margin-top:auto;display:flex;justify-content:space-between;align-items:center;font-size:30px;font-weight:700}
.cta{background:#fff;color:#140A9A;border-radius:999px;padding:14px 30px;font-weight:800}
""" % (photo, tsz)
        body = """<div class="ph"></div><div class="hd"><span class="lg">%s<span>فیت‌پلاس</span></span><span class="issue">شماره %s</span></div>
<div class="side"><span class="tag">%s</span><h1>%s</h1><div class="line"></div>
<div class="ft"><span class="cta">برنامه رایگان ←</span><span class="id">@fitplusapp</span></div></div>""" % (
            logo('#fff', '#FFD84D'), num, tag, hl)
    return '<html dir="rtl"><head><style>%s</style></head><body><div class="st">%s</div></body></html>' % (css, body)


def main():
    out = os.path.abspath(sys.argv[1])
    only_theme = sys.argv[2] if len(sys.argv) > 2 else None
    only_post = int(sys.argv[3]) if len(sys.argv) > 3 else None
    tmp = os.path.join(out, '_p.html')
    os.makedirs(out, exist_ok=True)
    with sync_playwright() as p:
        br = p.chromium.launch(args=['--allow-file-access-from-files'])
        pg = br.new_page(viewport={'width': W, 'height': H})
        for t in THEMES:
            if only_theme and t != only_theme:
                continue
            os.makedirs(os.path.join(out, t), exist_ok=True)
            for n in sorted(META):
                if only_post and n != only_post:
                    continue
                photo = pick_photo(n)
                open(tmp, 'w', encoding='utf-8').write(page(t, n, META[n], photo))
                pg.goto('file://' + tmp)
                pg.evaluate('document.fonts.ready')
                pg.wait_for_timeout(150)
                pg.screenshot(path=os.path.join(out, t, 'post-%02d.jpg' % n), type='jpeg', quality=86)
        br.close()
    os.remove(tmp)


if __name__ == '__main__':
    main()
