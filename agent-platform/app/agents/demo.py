"""Demo agent used to exercise the whole pipeline end to end.

It produces whatever text it is given in ``payload["text"]`` (or a numbered sample
based on the slot), so tests and the dashboard "run now" button can show
publishing, duplicate rejection and approvals without any external service.
Real agents (Phase 2/3) replace this with LLM/image/video generation.
"""
from .base import Agent, Draft, register


@register("demo")
class DemoAgent(Agent):
    def generate(self, cfg, task, payload):
        text = payload.get("text")
        if text is None:
            slot = payload.get("slot", "manual")
            text = f"نکته آزمایشی برای اجرای {slot}: این متن فقط برای آزمون سامانه است."
        if cfg.get("template"):
            text = cfg["template"].replace("{text}", text)
        return [Draft(
            body=text,
            title=payload.get("title", "نمونه"),
            kind=payload.get("kind", "text"),
            meta={"source_ref": payload.get("source_ref")} if payload.get("source_ref") else {},
            review_reasons=list(payload.get("review_reasons", [])),
        )]


@register("pending")
class PendingAgent(Agent):
    """Placeholder for agents scheduled for a later phase; never runs."""

    def generate(self, cfg, task, payload):
        raise NotImplementedError("این ایجنت در فاز بعدی پیاده‌سازی می‌شود")


@register("campaign")
class CampaignAgent(Agent):
    """Event-driven (see app/campaign.py); messages are dispatched by the worker, not by run_agent jobs."""

    def generate(self, cfg, task, payload):
        raise NotImplementedError("کمپین منیجر رویدادمحور است و با وظیفه run_agent اجرا نمی‌شود")


@register("monitor")
class MonitorAgent(Agent):
    """Existing production flow stays untouched; the platform only records what it did
    (POST /api/external/events) so it shows up in the dashboard, traces and alerts."""

    def generate(self, cfg, task, payload):
        raise NotImplementedError("این ایجنت فقط مانیتورینگ است و محتوا تولید نمی‌کند")
