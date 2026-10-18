#!/usr/bin/env python3
"""Build the FitPlus photo library (runs on GitHub Actions, which has open internet).

Reads tools/photo_queries.json  {"topic": ["english query", ...], ...}
Downloads large landscape CC0/commercial-use stock photos (StockSnap, Nappy,
Rawpixel public-domain) found through the Openverse API into photos/<topic>/<id>.jpg and records them in
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
            url = ('https://api.openverse.org/v1/images/?' + urllib.parse.urlencode(
                {'q': q, 'license_type': 'commercial,modification', 'source': 'stocksnap,nappy,rawpixel',
                 'aspect_ratio': 'wide', 'size': 'large', 'page_size': 20, 'mature': 'false'}))
            try:
                res = get(url)['results']
            except Exception as e:
                print('search failed', q, repr(e)[:300])
                try:
                    print('body', e.read()[:300])
                except Exception:
                    pass
                continue
            print('search', q, len(res))
            n = 0
            for p in res:
                if n >= PER_QUERY:
                    break
                if p['id'] in have or (p.get('width') or 0) < 1600:
                    continue
                try:
                    data = get(p['url'], binary=True)
                except Exception as e:
                    print('download failed', p['id'], repr(e)[:120])
                    continue
                if len(data) < 60000:
                    continue
                rel = 'photos/%s/%s.jpg' % (topic, p['id'][:12])
                open(os.path.join(ROOT, rel), 'wb').write(data)
                index.append({'id': p['id'], 'topic': topic, 'query': q, 'file': rel, 'title': p.get('title') or '',
                              'author': p.get('creator') or '', 'license': p.get('license'),
                              'source': p.get('foreign_landing_url') or '', 'w': p.get('width'), 'h': p.get('height')})
                have.add(p['id'])
                n += 1
                print('ok', topic, q, p['id'], p.get('source'), p.get('width'), len(data) // 1024, 'KB')
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
