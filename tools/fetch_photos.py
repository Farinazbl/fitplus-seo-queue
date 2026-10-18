#!/usr/bin/env python3
"""Build the FitPlus photo library (runs on GitHub Actions, which has open internet).

Reads tools/photo_queries.json  {"topic": ["english query", ...], ...}
Downloads landscape, non-premium Unsplash photos (Unsplash License: free for
commercial use) at 2400px into photos/<topic>/<id>.jpg and records them in
photos/index.json. Already-downloaded ids are skipped.
"""
import json, os, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PER_QUERY = 4
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36',
      'Accept': 'application/json'}


def get(url, binary=False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if binary else json.loads(data.decode())


def main():
    queries = json.load(open(os.path.join(ROOT, 'tools', 'photo_queries.json'), encoding='utf-8'))
    idx_f = os.path.join(ROOT, 'photos', 'index.json')
    index = json.load(open(idx_f, encoding='utf-8')) if os.path.exists(idx_f) else []
    have = {p['id'] for p in index}
    for topic, qs in queries.items():
        os.makedirs(os.path.join(ROOT, 'photos', topic), exist_ok=True)
        for q in qs:
            url = ('https://unsplash.com/napi/search/photos?' +
                   urllib.parse.urlencode({'query': q, 'per_page': 20, 'orientation': 'landscape'}))
            try:
                res = get(url)['results']
            except Exception as e:
                print('search failed', q, repr(e)[:300])
                try:
                    print('body', e.read()[:300])
                except Exception:
                    pass
                continue
            n = 0
            for p in res:
                if n >= PER_QUERY:
                    break
                if p.get('premium') or p.get('plus') or p['id'] in have:
                    continue
                raw = p['urls']['raw']
                img_url = raw + ('&' if '?' in raw else '?') + 'w=2400&h=1350&fit=crop&crop=entropy&q=82&fm=jpg'
                try:
                    data = get(img_url, binary=True)
                except Exception as e:
                    print('download failed', p['id'], e)
                    continue
                rel = 'photos/%s/%s.jpg' % (topic, p['id'])
                open(os.path.join(ROOT, rel), 'wb').write(data)
                index.append({'id': p['id'], 'topic': topic, 'query': q, 'file': rel,
                              'alt': p.get('alt_description') or '', 'author': p['user']['name'],
                              'source': p['links']['html']})
                have.add(p['id'])
                n += 1
                print('ok', topic, q, p['id'], len(data) // 1024, 'KB')
                time.sleep(0.5)
    json.dump(index, open(idx_f, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('total photos', len(index))


if __name__ == '__main__':
    import io, sys
    buf = io.StringIO()
    class T:
        def write(self, x): sys.__stdout__.write(x); buf.write(x)
        def flush(self): pass
    sys.stdout = T()
    try:
        main()
    finally:
        os.makedirs(os.path.join(ROOT, 'photos'), exist_ok=True)
        open(os.path.join(ROOT, 'photos', 'fetch_log.txt'), 'w').write(buf.getvalue())
