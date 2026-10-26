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


class BaleConnector(Connector):
    kind = "bale"

    def __init__(self, transport=http.request):
        self.transport = transport

    def publish(self, content, channel):
        cfg = (channel or {}).get("config", {})
        chat_id, token_env = cfg.get("chat_id"), cfg.get("token_env")
        if not chat_id or not token_env:
            raise PermanentError("کانال بله بدون chat_id یا token_env تنظیم شده است")
        token = os.environ.get(token_env)
        if not token:
            raise PermanentError(f"متغیر محیطی {token_env} روی سرور تنظیم نشده است")
        text = content["body"]
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
