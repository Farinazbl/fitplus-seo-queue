#!/usr/bin/env python3
"""Turn a raw article into a queued, publish-ready FitPlus post.

Usage:  python3 tools/build_post.py article.json
article.json keys:
  slug, title, excerpt (=meta description), category (38 workout | 48 nutrition),
  tags [3 Persian], rank_title, focus_keyword ("main,sec1,sec2"),
  body_html (raw HTML: intro <p> first, <h2>/<h3>, مزایای/معایب lists, one <table>,
             CTA <p>در <strong>فیت‌پلاس</strong>، ... <a href="https://web.fitplusapp.com/">..</a>.</p>,
             FAQ + FAQPage JSON-LD),
  mid_cta (one persuasive sentence for the middle CTA banner),
  image: {theme, kicker, title, sub, cards:[[fact,label]x3]}
Writes queue/<slug>/post.json + queue/<slug>/<slug>.jpg (2400x1350). Then commit & push.
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = os.path.join(ROOT, 'tools', 'fonts')
P, P2, G, R = '#6C3BFF', '#9B6BFF', '#12B76A', '#F04438'
ZW = '‌'
THEMES = {
    'purple': ['#2A1470', '#6C3BFF', '#B07CFF'], 'green': ['#0B4F3C', '#12B76A', '#6CE9A6'],
    'orange': ['#7A2E0E', '#F97316', '#FDBA74'], 'blue': ['#1E3A8A', '#3B82F6', '#93C5FD'],
    'pink': ['#831843', '#EC4899', '#F9A8D4'], 'teal': ['#134E4A', '#14B8A6', '#99F6E4'],
}


def style(s, mid_sentence):
    s = s.strip()
    s = s.replace('<p>', '<p style="background:linear-gradient(135deg,#F4EFFF,#EAF2FF);border-right:5px solid %s;'
                  'border-radius:14px;padding:18px 22px;font-size:1.05em;line-height:2">' % P, 1)

    def card(m):
        t, b = m.group(1), m.group(2)
        ok = 'مزایا' in t
        c, bg = (G, '#ECFDF3') if ok else (R, '#FEF3F2')
        return ('<div style="background:%s;border:1px solid %s33;border-radius:16px;padding:16px 22px;margin:16px 0">'
                '<h3 style="font-size:1.1em;color:%s;margin:0 0 6px">%s%s</h3><ul style="margin:0;padding-right:20px">%s</ul></div>'
                % (bg, c, c, '✅ ' if ok else '⚠️ ', t, b))
    s = re.sub(r'<h3>((?:مزایای|معایب)[^<]*)</h3>\s*<ul>([\s\S]*?)</ul>', card, s)
    s = s.replace('<h2>', '<h2 style="font-size:1.45em;color:#1D1A3A;margin:34px 0 14px;padding-bottom:10px;border-bottom:3px solid #EDE7FF">')
    s = s.replace('<h3>', '<h3 style="font-size:1.15em;color:%s;margin:22px 0 10px">' % P)
    s = s.replace('<ul>', '<ul style="padding-right:22px">').replace('<li>', '<li style="margin:8px 0;line-height:1.9">')
    s = s.replace('<table>', '<div style="overflow-x:auto;margin:16px 0"><table style="width:100%;border-collapse:separate;'
                  'border-spacing:0;border-radius:14px;overflow:hidden;box-shadow:0 4px 18px rgba(108,59,255,.12);text-align:center">', 1)
    s = s.replace('</table>', '</table></div>', 1)
    s = s.replace('<th>', '<th style="background:%s;color:#fff;padding:12px 10px;font-weight:700">' % P)
    n = [0]

    def tr(_):
        n[0] += 1
        return '<tr style="background:%s">' % ('#FFFFFF' if (n[0] - 1) % 2 else '#F8F6FF')
    s = re.sub(r'<tr>', tr, s)
    s = s.replace('<td>', '<td style="padding:11px 10px;border-bottom:1px solid #EEE">')
    s = re.sub(r'<p>(در <strong>فیت' + ZW + r'پلاس</strong>[\s\S]*?)<a href="(https://web\.fitplusapp\.com/)">([^<]+)</a>\.</p>',
               lambda m: ('<div style="background:linear-gradient(135deg,%s,%s);color:#fff;border-radius:18px;padding:24px;margin:22px 0;text-align:center">'
                          '<p style="color:#fff;margin:0 0 14px;line-height:2">%s</p><a href="%s" style="display:inline-block;background:#fff;color:%s;'
                          'font-weight:700;padding:12px 28px;border-radius:999px;text-decoration:none">%s ←</a></div>'
                          % (P, P2, m.group(1).replace('<strong>', '<strong style="color:#fff">', 1).strip(), m.group(2), P, m.group(3))), s, count=1)
    s = s.replace('<p>', '<p style="line-height:2.1">')
    top = ('<!--fp-cta1--><div style="text-align:center;margin:18px 0 8px"><a href="https://web.fitplusapp.com/" style="display:inline-block;'
           'background:linear-gradient(135deg,#6C3BFF,#3B82F6);color:#fff;font-weight:700;padding:13px 28px;border-radius:999px;'
           'text-decoration:none;box-shadow:0 8px 22px rgba(108,59,255,.3)">🎯 آنالیز رایگان بدن و دریافت برنامه</a></div>')
    mid = ('<!--fp-cta2--><div style="display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;'
           'background:linear-gradient(135deg,#0B1220,#1E1B4B);color:#fff;border-radius:16px;padding:18px 22px;margin:28px 0">'
           '<span style="font-weight:700;line-height:1.9;color:#fff">%s</span><a href="https://web.fitplusapp.com/" style="background:#34D399;'
           'color:#0B1220;font-weight:700;padding:10px 24px;border-radius:999px;text-decoration:none;white-space:nowrap">شروع رایگان ←</a></div>'
           % mid_sentence)
    i = s.index('</p>') + 4
    s = s[:i] + top + s[i:]
    hs = [m.start() for m in re.finditer(r'<h2 ', s)]
    if hs:
        k = hs[len(hs) // 2]
        s = s[:k] + mid + s[k:]
    return s


DRAW = r"""
async (o)=>{for(const w of ['400','500','700','800'])await document.fonts.load(w+' 40px Vaz');
const S=2,W=1200,H=675,c=document.createElement('canvas');c.width=W*S;c.height=H*S;const x=c.getContext('2d');x.scale(S,S);
x.direction='rtl';x.textAlign='center';
let g=x.createLinearGradient(0,0,W,H);o.g.forEach((col,i)=>g.addColorStop(i/(o.g.length-1),col));x.fillStyle=g;x.fillRect(0,0,W,H);
const rg=x.createRadialGradient(950,120,10,950,120,520);rg.addColorStop(0,'rgba(255,255,255,.22)');rg.addColorStop(1,'rgba(255,255,255,0)');x.fillStyle=rg;x.fillRect(0,0,W,H);
x.strokeStyle='rgba(255,255,255,.06)';x.lineWidth=1;for(let i=0;i<W;i+=40){x.beginPath();x.moveTo(i,0);x.lineTo(i,H);x.stroke()}for(let j=0;j<H;j+=40){x.beginPath();x.moveTo(0,j);x.lineTo(W,j);x.stroke()}
x.globalAlpha=.10;x.fillStyle='#fff';[[1090,580,240],[110,80,170]].forEach(([a,b,r])=>{x.beginPath();x.arc(a,b,r,0,7);x.fill()});x.globalAlpha=1;
x.fillStyle='rgba(255,255,255,.16)';x.beginPath();x.roundRect(470,52,260,40,20);x.fill();x.fillStyle='#fff';x.font='700 20px Vaz';x.fillText(o.kicker,600,79);
x.shadowColor='rgba(0,0,0,.25)';x.shadowBlur=18;x.shadowOffsetY=4;x.fillStyle='#fff';
let fs=64;x.font='800 '+fs+'px Vaz';while(x.measureText(o.title).width>1080&&fs>40){fs-=2;x.font='800 '+fs+'px Vaz'}x.fillText(o.title,600,175);
x.shadowBlur=0;x.shadowOffsetY=0;x.font='400 30px Vaz';x.fillStyle='rgba(255,255,255,.92)';x.fillText(o.sub,600,232);
o.cards.forEach(([a,b],k)=>{const cx=150+(2-k)*310;x.save();x.shadowColor='rgba(0,0,0,.18)';x.shadowBlur=24;x.shadowOffsetY=8;x.fillStyle='rgba(255,255,255,.17)';x.beginPath();x.roundRect(cx,295,280,180,26);x.fill();x.restore();
x.strokeStyle='rgba(255,255,255,.35)';x.lineWidth=1.5;x.beginPath();x.roundRect(cx,295,280,180,26);x.stroke();
x.fillStyle='#fff';let f2=48;x.font='800 '+f2+'px Vaz';while(x.measureText(a).width>250&&f2>28){f2-=2;x.font='800 '+f2+'px Vaz'}x.fillText(a,cx+140,382);
x.font='500 25px Vaz';x.fillStyle='rgba(255,255,255,.92)';x.fillText(b,cx+140,430);});
x.fillStyle='#fff';x.beginPath();x.roundRect(470,545,260,58,29);x.fill();x.fillStyle=o.dark;x.font='800 27px Vaz';x.fillText('فیت‌پلاس',600,583);
return c.toDataURL('image/jpeg',.93)}
"""


def render_image(img, out_path):
    import base64
    from playwright.sync_api import sync_playwright
    faces = ''.join("@font-face{font-family:Vaz;font-weight:%s;src:url('file://%s/Vazirmatn-%s.woff2')}" % (w, FONTS, n)
                    for w, n in (('400', 'Regular'), ('500', 'Medium'), ('700', 'Bold'), ('800', 'ExtraBold')))
    html = '<html><head><style>%s</style></head><body></body></html>' % faces
    tmp = os.path.join(ROOT, 'tools', '_render.html')
    open(tmp, 'w', encoding='utf-8').write(html)
    g = THEMES.get(img.get('theme', 'purple'), THEMES['purple'])
    o = {'g': g, 'dark': g[0], 'kicker': img['kicker'], 'title': img['title'], 'sub': img['sub'], 'cards': img['cards']}
    with sync_playwright() as p:
        exe = '/opt/pw-browsers/chromium/chrome-linux/chrome'
        b = p.chromium.launch(executable_path=exe if os.path.exists(exe) else None, args=['--allow-file-access-from-files'])
        page = b.new_page()
        page.goto('file://' + tmp)
        data = page.evaluate(DRAW, o)
        b.close()
    os.remove(tmp)
    open(out_path, 'wb').write(base64.b64decode(data.split(',', 1)[1]))


def main(path):
    a = json.load(open(path, encoding='utf-8'))
    slug = a['slug']
    d = os.path.join(ROOT, 'queue', slug)
    os.makedirs(d, exist_ok=True)
    content = style(a['body_html'], a.get('mid_cta', 'برنامه' + ZW + 'ای که دقیقاً برای بدن تو ساخته شود، نتیجه بهتری می' + ZW + 'دهد.'))
    img_name = slug + '.jpg'
    render_image(a['image'], os.path.join(d, img_name))
    post = {'title': a['title'], 'slug': slug, 'content': content, 'excerpt': a['excerpt'],
            'category': int(a.get('category', 38)), 'tags': a.get('tags', []),
            'rank_title': a.get('rank_title', a['title']), 'rank_description': a['excerpt'],
            'focus_keyword': a.get('focus_keyword', ''), 'image': img_name, 'alt': a.get('alt', a['title'])}
    json.dump(post, open(os.path.join(d, 'post.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('queued', d, 'ctas=%d' % content.count('web.fitplusapp.com'), 'zwnj=%d' % content.count(ZW))


if __name__ == '__main__':
    main(sys.argv[1])
