"""Tiny HTTP helper (stdlib only) so connectors/sources can be tested with a fake."""
import json
import urllib.error
import urllib.request


class HttpError(Exception):
    def __init__(self, status, body=""):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


def request(method, url, headers=None, json_body=None, timeout=20) -> str:
    data = json.dumps(json_body).encode() if json_body is not None else None
    h = {"User-Agent": "agent-platform/1.0", **(headers or {})}
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace")) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HttpError(0, str(e)) from None
