"""Bale Bot API connector (https://tapi.bale.ai/bot<TOKEN>/sendMessage).

Channel ``config`` (editable from the DB/dashboard later):
    {"chat_id": "@channel_username", "token_env": "BALE_TOKEN_TAX"}
The token itself is read from the environment variable named in ``token_env`` -
it is never stored in the database or sent to the browser. The bot must be an
admin of the channel.
"""
import json
import os

from .. import http
from .base import Connector, PermanentError, PublishResult, TransientError

API = "https://tapi.bale.ai/bot{token}/{method}"
MAX_LEN = 4096
CAPTION_MAX = 1024


class BaleConnector(Connector):
    kind = "bale"

    def __init__(self, transport=http.request, multipart=http.post_multipart):
        self.transport = transport
        self.multipart = multipart

    def publish(self, content, channel):
        cfg = (channel or {}).get("config", {})
        chat_id = cfg.get("chat_id")
        token_env = cfg.get("token_env") or "BALE_TOKEN"  # one shared token unless a channel names its own
        if not chat_id:
            raise PermanentError("کانال بله بدون chat_id تنظیم شده است")
        token = os.environ.get(token_env)
        if not token:
            raise PermanentError(f"متغیر محیطی {token_env} روی سرور تنظیم نشده است")
        text = content["body"]
        image = (content.get("meta") or {}).get("image_path")
        if image:
            return self._photo(token, chat_id, image, text)
        if len(text) > MAX_LEN:
            raise PermanentError(f"متن بلندتر از {MAX_LEN} نویسه است")
        try:
            raw = self.transport("POST", API.format(token=token, method="sendMessage"),
                                 json_body={"chat_id": chat_id, "text": text})
        except http.HttpError as e:
            if e.status == 0 or e.status == 429 or e.status >= 500:
                raise TransientError(str(e)) from None
            raise PermanentError(f"بله درخواست را رد کرد ({e.status}): {e.body[:150]}") from None
        data = json.loads(raw)
        if not data.get("ok"):
            raise PermanentError(f"پاسخ ناموفق بله: {raw[:150]}")
        return PublishResult(external_id=str(data.get("result", {}).get("message_id", "")))

    def _photo(self, token, chat_id, image_path, caption):
        try:
            data = open(image_path, "rb").read()
        except OSError:
            raise PermanentError("فایل تصویر پست پیدا نشد") from None
        short = caption if len(caption) <= CAPTION_MAX else ""
        try:
            raw = self.multipart(API.format(token=token, method="sendPhoto"),
                                 {"chat_id": chat_id, "caption": short}, {"photo": ("post.jpg", data, "image/jpeg") if data[:2] == b"\xff\xd8" else ("post.png", data, "image/png")})
            data = json.loads(raw)
            if not data.get("ok"):
                raise PermanentError(f"پاسخ ناموفق بله: {raw[:150]}")
            if not short and caption:                    # long text goes as a follow-up message
                try:
                    self.transport("POST", API.format(token=token, method="sendMessage"),
                                   json_body={"chat_id": chat_id, "text": caption[:MAX_LEN]})
                except http.HttpError:
                    pass  # the photo is already posted; never retry the whole item (would duplicate it)
        except http.HttpError as e:
            if e.status == 0 or e.status == 429 or e.status >= 500:
                raise TransientError(str(e)) from None
            raise PermanentError(f"بله درخواست را رد کرد ({e.status}): {e.body[:150]}") from None
        return PublishResult(external_id=str(data.get("result", {}).get("message_id", "")))
