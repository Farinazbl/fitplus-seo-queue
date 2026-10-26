"""Publishing connectors.

One connector per destination kind. A connector either publishes (returns a
``PublishResult``), raises ``TransientError`` (the queue retries with backoff) or
raises ``PermanentError`` (the item is marked failed and logged).

Where an official API is missing or the account has no permission, the platform
falls back to ``ManualQueueConnector``: the item becomes ``ready_manual`` -
finished, checked and waiting for a person to post it. This is the RFP's
"ready-to-publish output + manual approval queue" requirement.

Real connectors (Bale, WordPress, Instagram Graph API, YouTube Data API, SMS)
are added per phase and registered with ``register(kind, connector)``.
"""
from dataclasses import dataclass
from typing import Optional


class TransientError(Exception):
    """Temporary failure (network, rate limit): retry later."""


class PermanentError(Exception):
    """The destination rejected the item: do not retry."""


@dataclass
class PublishResult:
    external_id: Optional[str] = None
    manual: bool = False


class Connector:
    kind = "base"

    def publish(self, content: dict, channel: Optional[dict]) -> PublishResult:
        raise NotImplementedError


class LogConnector(Connector):
    """Development connector: pretends to publish and remembers what it got."""
    kind = "log"

    def __init__(self):
        self.published = []

    def publish(self, content, channel):
        self.published.append(content["uid"])
        return PublishResult(external_id=f"log-{content['uid']}")


class ManualQueueConnector(Connector):
    kind = "manual"

    def publish(self, content, channel):
        return PublishResult(external_id=None, manual=True)


_REGISTRY = {"log": LogConnector()}
_FALLBACK = ManualQueueConnector()


def register(kind: str, connector: Connector) -> None:
    _REGISTRY[kind] = connector


def for_channel(channel: Optional[dict]) -> Connector:
    if channel and channel["kind"] in _REGISTRY:
        return _REGISTRY[channel["kind"]]
    return _FALLBACK
