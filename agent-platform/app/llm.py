"""LLM clients. Real calls need ANTHROPIC_API_KEY or OPENAI_API_KEY on the server; tests inject a fake.

Provider choice: APP_LLM_PROVIDER=anthropic|openai, otherwise whichever key is set (Anthropic first).
OpenAI-compatible gateways: set OPENAI_BASE_URL (default https://api.openai.com/v1).
"""
import json
import os

from . import http

URL = "https://api.anthropic.com/v1/messages"


class LLMError(Exception):
    pass


class AnthropicClient:
    def __init__(self, model=None, transport=http.request):
        self.model = model or os.environ.get("APP_LLM_MODEL", "claude-sonnet-5-5")
        self.transport = transport

    def complete(self, system: str, user: str, max_tokens: int = 1200) -> str:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError("ANTHROPIC_API_KEY روی سرور تنظیم نشده است")
        try:
            raw = self.transport("POST", URL, headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                                 json_body={"model": self.model, "max_tokens": max_tokens, "system": system,
                                            "messages": [{"role": "user", "content": user}]}, timeout=60)
        except http.HttpError as e:
            raise LLMError(str(e)) from None  # retried by the queue
        data = json.loads(raw)
        return "".join(b.get("text", "") for b in data.get("content", [])).strip()


class OpenAIClient:
    """OpenAI Chat Completions (also any OpenAI-compatible endpoint via OPENAI_BASE_URL)."""

    def __init__(self, model=None, transport=http.request):
        self.model = model or os.environ.get("APP_LLM_MODEL", "gpt-4o-mini")
        self.transport = transport

    def complete(self, system: str, user: str, max_tokens: int = 1200) -> str:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMError("OPENAI_API_KEY روی سرور تنظیم نشده است")
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        try:
            raw = self.transport("POST", f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                                 json_body={"model": self.model, "max_completion_tokens": max_tokens,
                                            "messages": [{"role": "system", "content": system},
                                                         {"role": "user", "content": user}]}, timeout=90)
        except http.HttpError as e:
            raise LLMError(str(e)) from None  # retried by the queue
        try:
            return (json.loads(raw)["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, ValueError, TypeError):
            raise LLMError("پاسخ نامعتبر از سرویس مدل زبانی") from None


_client = None


def get_client():
    if _client:
        return _client
    provider = os.environ.get("APP_LLM_PROVIDER", "").lower()
    if provider == "openai" or (not provider and not os.environ.get("ANTHROPIC_API_KEY")
                                and os.environ.get("OPENAI_API_KEY")):
        return OpenAIClient()
    return AnthropicClient()


def set_client(client):
    """Tests (or another provider) replace the client here."""
    global _client
    _client = client
