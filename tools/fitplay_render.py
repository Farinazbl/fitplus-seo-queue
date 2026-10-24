#!/usr/bin/env python3
"""Render the «فیتپلی می‌گه» mascot post series (1080x1350) from telegram/fitplay/<pose>.png."""
import html, os, sys
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'telegram'))
import fitplay_posts as FP

FONTS = os.path.join(ROOT, 'tools', 'fonts')
SRC = os.path.join(ROOT, 'telegram', 'fitplay')
OUT = os.path.join(ROOT, 'telegram', 'fitplay-posts')


def page(pose, head, hl, tag, n):
    faces = ''.join("@font-face{font-family:Vaz;font-weight:%s;src:url('file://%s/Vazirmatn-%s.woff2')}" % (w, FONTS, f)
                    for w, f in (('500', 'Medium'), ('700', 'Bold'), ('800', 'ExtraBold')))
    h = html.escape(head)
    h = h.replace(html.escape(hl), '<em>%s</em>' % html.escape(hl), 1)
    return """<html dir="rtl"><head><style>%s
*{box-sizing:border-box}html,body{margin:0;width:1080px;height:1350px;overflow:hidden;font-family:Vaz}
.st{position:relative;width:1080px;height:1350px;overflow:hidden}
.bg{position:absolute;inset:-60px;background:url('file://%s/%s.png') center/cover;filter:blur(40px)}
.fg{position:absolute;left:0;right:0;top:150px;height:1180px;background:url('file://%s/%s.png') center/auto 100%% no-repeat;
-webkit-mask-image:linear-gradient(90deg,transparent 0,#000 70px,#000 calc(100%% - 70px),transparent 100%%)}
.top{position:absolute;top:0;left:0;right:0;height:330px;background:linear-gradient(180deg,rgba(20,10,154,.92) 0%%,rgba(20,10,154,.75) 55%%,rgba(20,10,154,0) 100%%)}
.hd{position:absolute;top:44px;right:60px;left:60px;display:flex;justify-content:space-between;align-items:center}
.lg{display:flex;align-items:center;gap:12px;color:#fff;font-weight:800;font-size:34px}.lg img{height:40px}
.tag{background:#C8F135;color:#140A9A;font-weight:800;font-size:28px;padding:8px 24px;border-radius:999px}
h1{position:absolute;top:120px;right:60px;left:60px;margin:0;color:#fff;font-size:%dpx;font-weight:800;line-height:1.35;text-shadow:0 4px 18px rgba(0,0,0,.25)}
h1 em{font-style:normal;color:#C8F135}
.bar{position:absolute;bottom:40px;right:60px;left:60px;background:#fff;border-radius:999px;padding:16px 18px 16px 30px;display:flex;justify-content:space-between;align-items:center;box-shadow:0 18px 40px -18px rgba(20,10,154,.5)}
.bar b{color:#140A9A;font-size:30px;font-weight:800}.bar span{direction:ltr;color:#fff;background:#140A9A;border-radius:999px;padding:10px 24px;font-weight:800;font-size:26px}
.num{position:absolute;bottom:130px;left:60px;color:#140A9A;font-weight:800;font-size:26px;background:rgba(255,255,255,.75);border-radius:14px;padding:6px 16px}
</style></head><body><div class="st"><div class="bg"></div><div class="fg"></div><div class="top"></div>
<div class="hd"><span class="lg"><img src="file://%s/tools/logo-white.png" alt=""><span>فیت‌پلاس</span></span><span class="tag">%s</span></div>
<h1>%s</h1><div class="num">فیتپلی #%s</div>
<div class="bar"><b>یه قدم، هر روز 💪</b><span>@fitplusapp</span></div></div></body></html>""" % (
        faces, SRC, pose, SRC, pose, 78 if len(head) < 22 else 66, ROOT, html.escape(tag), h, str(n).translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')))


def main():
    os.makedirs(OUT, exist_ok=True)
    tmp = os.path.join(OUT, '_p.html')
    with sync_playwright() as p:
        b = p.chromium.launch(args=['--allow-file-access-from-files'])
        pg = b.new_page(viewport={'width': 1080, 'height': 1350})
        for n, (pose, head, hl, tag, _) in enumerate(FP.POSTS, 1):
            open(tmp, 'w', encoding='utf-8').write(page(pose, head, hl, tag, n))
            pg.goto('file://' + tmp)
            pg.evaluate('document.fonts.ready')
            pg.wait_for_timeout(200)
            pg.screenshot(path=os.path.join(OUT, 'fitplay-%02d.jpg' % n), type='jpeg', quality=88)
        b.close()
    os.remove(tmp)


if __name__ == '__main__':
    main()
