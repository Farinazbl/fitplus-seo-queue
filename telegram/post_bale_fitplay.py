#!/usr/bin/env python3
"""FitPlus Bale channel — «فیتپلی می‌گه» daily mascot post — runs on GitHub Actions.

Posts the next FitPlay mascot tip (image + caption) to the Bale channel @fitplusapp,
once per run. Loops back to post #1 after the 8th. State kept in
telegram/fitplay_bale_state.json and committed back by the workflow.

Env: BALE_BOT_TOKEN (secret, from @Fitplusbot), BALE_CHANNEL (var, default @fitplusapp)
"""
import json, os, sys, urllib.error, urllib.request, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.environ['BALE_BOT_TOKEN'].strip()
CHANNEL = os.environ.get('BALE_CHANNEL', '@fitplusapp').strip()
API = 'https://tapi.bale.ai/bot%s/' % TOKEN
STATE_F = os.path.join(HERE, 'fitplay_bale_state.json')

sys.path.insert(0, HERE)
import fitplay_posts as FP  # noqa: E402


def load(p, d):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return d


def send_photo(chat_id, image_path, caption):
    b = uuid.uuid4().hex
    fields = {'chat_id': chat_id, 'caption': caption}
    parts = []
    for k, v in fields.items():
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v)).encode())
    parts.append(('--%s\r\nContent-Disposition: form-data; name="photo"; filename="%s"\r\nContent-Type: image/jpeg\r\n\r\n'
                  % (b, os.path.basename(image_path))).encode())
    parts.append(open(image_path, 'rb').read())
    parts.append(('\r\n--%s--\r\n' % b).encode())
    req = urllib.request.Request(API + 'sendPhoto', data=b''.join(parts),
                                 headers={'Content-Type': 'multipart/form-data; boundary=' + b})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode() or '{}')


def main():
    caps = FP.captions()
    total = len(caps)
    state = load(STATE_F, {'next': 0})
    n = state.get('next', 0) % total
    pose = FP.POSTS[n][0]
    image_path = os.path.join(HERE, 'fitplay-posts', 'fitplay-%02d.jpg' % (n + 1))

    r = send_photo(CHANNEL, image_path, caps[n])
    print('fitplay bale post', n + 1, pose, r.get('ok'), r.get('description'))
    if not r.get('ok'):
        sys.exit(1)

    state['next'] = n + 1
    json.dump(state, open(STATE_F, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
