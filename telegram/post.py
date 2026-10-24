#!/usr/bin/env python3
"""FitPlus Telegram channel poster — runs on GitHub Actions (outside Iran).

Modes:
  python3 telegram/post.py slot      -> post the next FitPlus tip (image + caption)
  python3 telegram/post.py articles  -> announce new site articles from existing.json (once each)
Env: TELEGRAM_BOT_TOKEN (secret), TELEGRAM_CHANNEL (e.g. @fitplusapp)
State is kept in telegram/state.json and committed back by the workflow.
"""
import time, json, os, re, sys, urllib.error, urllib.request, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKEN = os.environ['TELEGRAM_BOT_TOKEN'].strip()
CHANNEL = os.environ.get('TELEGRAM_CHANNEL', '').strip()
API = 'https://api.telegram.org/bot%s/' % TOKEN
STATE_F = os.path.join(HERE, 'state.json')
BTN = {'inline_keyboard': [[{'text': '🎯 آنالیز رایگان و دریافت برنامه', 'url': 'https://web.fitplusapp.com/'}]]}


def load(p, d):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return d


def call(method, fields, file_field=None):
    if file_field is None:
        data = json.dumps(fields).encode()
        req = urllib.request.Request(API + method, data=data, headers={'Content-Type': 'application/json'})
    else:
        name, path = file_field
        b = uuid.uuid4().hex
        parts = []
        for k, v in fields.items():
            v = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
            parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v)).encode())
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: image/jpeg\r\n\r\n'
                      % (b, name, os.path.basename(path))).encode())
        parts.append(open(path, 'rb').read())
        parts.append(('\r\n--%s--\r\n' % b).encode())
        req = urllib.request.Request(API + method, data=b''.join(parts),
                                     headers={'Content-Type': 'multipart/form-data; boundary=' + b})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode() or '{}')


def clean(caption):
    caption = re.sub(r'ورود به فیت‌پلاس:\s*https?://\S+\s*', '', caption)
    caption = re.sub(r'@[A-Za-z0-9_]{3,}', '', caption)  # no foreign @ids
    caption = caption.strip()
    tail = '\n\n📢 %s' % CHANNEL if CHANNEL else ''
    if len(caption) + len(tail) > 1024:  # Telegram photo caption limit
        caption = caption[:1020 - len(tail)].rsplit('\n', 1)[0]
    return caption + tail


def slot(state):
    posts = load(os.path.join(HERE, 'posts.json'), [])
    n = state.get('next', 0) % len(posts)
    # alternate days: original design one day, one of the 6 new designs the next
    themes = ['poster', 'classic', 'neon', 'wellness', 'circle', 'magazine']
    day = int((time.time() + 12600) // 86400)
    img = os.path.join(HERE, 'images', 'post-%02d.jpg' % (n + 1))
    if day % 2 or not os.path.exists(img):
        th = themes[(day // 2) % len(themes)] if day % 2 else 'classic'
        img = os.path.join(HERE, 'images-themes', th, 'post-%02d.jpg' % (n + 1))
    r = call('sendPhoto', {'chat_id': CHANNEL, 'caption': clean(posts[n]), 'reply_markup': BTN}, ('photo', img))
    print('slot post', n + 1, r.get('ok'), r.get('description'))
    if not r.get('ok'):
        sys.exit(1)
    state['next'] = n + 1


def articles(state):
    done = set(state.get('announced', []))
    # articles that existed before the channel launch are not announced
    if 'announced' not in state:
        state['announced'] = [a['slug'] for a in load(os.path.join(ROOT, 'existing.json'), [])]
        print('initialised announced list'); return
    for a in load(os.path.join(ROOT, 'existing.json'), []):
        if a['slug'] in done:
            continue
        try:  # only announce once the article is live on the site
            with urllib.request.urlopen(urllib.request.Request(a['url'], headers={'User-Agent': 'Mozilla/5.0'}), timeout=30) as r:
                live = r.status == 200
        except Exception:
            live = False
        if not live:
            print('not live yet', a['slug']); continue
        text = '📚 مقاله جدید فیت‌پلاس\n\n%s\n\n%s' % (a['title'], a['url'])
        if CHANNEL:
            text += '\n\n📢 %s' % CHANNEL
        img = os.path.join(ROOT, 'queue', a['slug'], a['slug'] + '.jpg')
        kb = {'inline_keyboard': [[{'text': '📖 مطالعه مقاله', 'url': a['url']}],
                                  [{'text': '🎯 آنالیز رایگان', 'url': 'https://web.fitplusapp.com/'}]]}
        if os.path.exists(img):
            r = call('sendPhoto', {'chat_id': CHANNEL, 'caption': text, 'reply_markup': kb}, ('photo', img))
        else:
            r = call('sendMessage', {'chat_id': CHANNEL, 'text': text, 'reply_markup': kb})
        print('article', a['slug'], r.get('ok'), r.get('description'))
        if r.get('ok'):
            state['announced'].append(a['slug'])


if __name__ == '__main__':
    st = load(STATE_F, {})
    (articles if (sys.argv[1:] or ['slot'])[0] == 'articles' else slot)(st)
    json.dump(st, open(STATE_F, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
