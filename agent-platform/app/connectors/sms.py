"""SMS providers.

``send`` returns a provider message id, raises ``TransientError`` (retry later) or
``PermanentError`` (give up on this message). ``HttpSmsProvider`` is a generic JSON
gateway adapter configured through environment variables; the real panel's API
must be confirmed with the client before go-live (it is tested here only against
a fake transport).
"""
import json
import os

from .. import http
from .base import PermanentError, TransientError


class SmsProvider:
    def send(self, phone: str, text: str, ref: str) -> str:
        raise NotImplementedError


class LogSms(SmsProvider):
    """Development provider: records messages instead of sending them."""

    def __init__(self):
        self.sent = []

    def send(self, phone, text, ref):
        self.sent.append({"phone": phone, "text": text, "ref": ref})
        return f"log-{ref}"


class HttpSmsProvider(SmsProvider):
    """POST {"to","text","ref"} to SMS_URL with ``Authorization: Bearer $SMS_TOKEN``."""

    def __init__(self, transport=http.request):
        self.transport = transport

    def send(self, phone, text, ref):
        url, token = os.environ.get("SMS_URL"), os.environ.get("SMS_TOKEN")
        if not url or not token:
            raise PermanentError("SMS_URL / SMS_TOKEN روی سرور تنظیم نشده است")
        try:
            raw = self.transport("POST", url, headers={"Authorization": f"Bearer {token}"},
                                 json_body={"to": phone, "text": text, "ref": ref})
        except http.HttpError as e:
            if e.status in (0, 429) or e.status >= 500:
                raise TransientError(str(e)) from None
            raise PermanentError(f"پنل پیامک رد کرد ({e.status})") from None
        try:
            return str(json.loads(raw).get("id", ref))
        except ValueError:
            return ref


_provider = None


def get_provider() -> SmsProvider:
    """Real gateway when SMS_URL is set; ``APP_SMS_DRY_RUN=1`` selects the logging provider.

    With neither, sending fails loudly (message marked failed) instead of pretending
    to deliver.
    """
    if _provider is not None:
        return _provider
    if os.environ.get("APP_SMS_DRY_RUN") == "1":
        return LogSms()
    return HttpSmsProvider()


def set_provider(provider):
    global _provider
    _provider = provider
