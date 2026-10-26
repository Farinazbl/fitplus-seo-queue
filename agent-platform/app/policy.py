"""Publishing policy: hard rejections and "needs a human" routing.

The policy dict lives in each agent's configuration, so the rules for the two
tax/accounting Bale channels (no links, no course ads, no source names, ...) and
the sensitive-content routing for health/finance are edited from the dashboard.

Supported keys:
    forbid_links        bool   reject text containing URLs / t.me / ble.ir links
    forbid_mentions     bool   reject @handles (the channel signature is added later)
    forbid_terms        list   reject if any term appears (e.g. course promotion)
    forbid_source_names list   reject if a source channel/site is named
    review_terms        list   do not reject, but send to the human approval queue
    min_chars/max_chars int    length limits
"""
import re

from .textutil import fold

_LINK = re.compile(
    r"(https?://|www\.|t\.me/|ble\.ir/|bale\.ai/|instagram\.com/|\b[\w-]+\.(com|ir|org|net|me|ai)\b)",
    re.IGNORECASE)
_MENTION = re.compile(r"(?<!\w)@\w{3,}")


def check(policy: dict, text: str):
    """Return ``(violations, review_reasons)``; both are lists of Persian messages."""
    violations, review = [], []
    folded = fold(text)

    if policy.get("forbid_links") and _LINK.search(text):
        violations.append("متن شامل لینک یا نشانی اینترنتی است")
    if policy.get("forbid_mentions") and _MENTION.search(text):
        violations.append("متن شامل آیدی (@) است")
    for term in policy.get("forbid_terms", []):
        if fold(term) in folded:
            violations.append(f"عبارت ممنوع: «{term}»")
    for name in policy.get("forbid_source_names", []):
        if fold(name) in folded:
            violations.append(f"ذکر نام منبع ممنوع است: «{name}»")
    n = len(text.strip())
    if policy.get("min_chars") and n < policy["min_chars"]:
        violations.append("متن کوتاه‌تر از حداقل مجاز است")
    if policy.get("max_chars") and n > policy["max_chars"]:
        violations.append("متن بلندتر از حداکثر مجاز است")

    for term in policy.get("review_terms", []):
        if fold(term) in folded:
            review.append(f"محتوای حساس نیازمند تأیید انسانی: «{term}»")
    return violations, review
