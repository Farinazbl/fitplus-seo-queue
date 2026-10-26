"""WordPress REST connector (``/wp-json/wp/v2/posts``) using an Application Password.

Channel ``config``:
    {"site": "https://example.ir", "user": "bot", "password_env": "WP_APP_PASSWORD",
     "post_status": "draft" | "publish"}
Default ``post_status`` is ``draft`` - an editor presses publish - until the client
decides the quality is good enough to go straight to ``publish``.
"""
import base64
import json
import os

from .. import http
from .base import Connector, PermanentError, PublishResult, TransientError


class WordPressConnector(Connector):
    kind = "wordpress"

    def __init__(self, transport=http.request):
        self.transport = transport

    def publish(self, content, channel):
        cfg = (channel or {}).get("config", {})
        site, user, env = cfg.get("site"), cfg.get("user"), cfg.get("password_env")
        if not (site and user and env):
            raise PermanentError("تنظیمات وردپرس (site/user/password_env) کامل نیست")
        password = os.environ.get(env)
        if not password:
            raise PermanentError(f"متغیر محیطی {env} روی سرور تنظیم نشده است")
        auth = base64.b64encode(f"{user}:{password}".encode()).decode()
        post = {"title": content["title"] or "بدون عنوان", "content": content["body"],
                "status": cfg.get("post_status", "draft"),
                "excerpt": content["meta"].get("meta_description", "")}
        try:
            raw = self.transport("POST", site.rstrip("/") + "/wp-json/wp/v2/posts",
                                 headers={"Authorization": f"Basic {auth}"}, json_body=post)
        except http.HttpError as e:
            if e.status in (0, 429) or e.status >= 500:
                raise TransientError(str(e)) from None
            raise PermanentError(f"وردپرس درخواست را رد کرد ({e.status}): {e.body[:150]}") from None
        data = json.loads(raw)
        if "id" not in data:
            raise PermanentError(f"پاسخ نامعتبر وردپرس: {raw[:150]}")
        return PublishResult(external_id=str(data["id"]))
