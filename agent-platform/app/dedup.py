"""Duplicate detection.

Two layers, both scoped per agent (or any scope string) and limited to a
sliding window (15 days by default, as required by the RFP):

1. exact: hash of the normalised text, or the same ``source_ref``;
2. near-duplicate: Jaccard / containment over 3-word shingles.

A semantic layer (embeddings or an LLM judge) plugs in through ``semantic_check``;
it is intentionally not built in Phase 1.
"""
import hashlib
import json
from datetime import timedelta
from typing import Callable, Optional

from .textutil import normalize
from .util import iso, utcnow

SHINGLE = 3


def _h(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "big")


def shingle_hashes(norm: str) -> set:
    words = norm.split()
    if not words:
        return set()
    if len(words) < SHINGLE:
        return {_h(" ".join(words))}
    return {_h(" ".join(words[i:i + SHINGLE])) for i in range(len(words) - SHINGLE + 1)}


def similarity(a: set, b: set) -> float:
    """Return the larger of Jaccard and (slightly discounted) containment."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    jaccard = inter / len(a | b)
    containment = inter / min(len(a), len(b))
    return max(jaccard, containment * 0.9)


def find_duplicate(db, scope: str, text: str, source_ref: Optional[str] = None,
                   window_days: int = 15, threshold: float = 0.6, now=None,
                   semantic_check: Optional[Callable] = None) -> Optional[dict]:
    """Return ``{"uid":..., "why":..., "score":...}`` for the first duplicate found."""
    now = now or utcnow()
    cutoff = iso(now - timedelta(days=window_days))
    norm = normalize(text)
    norm_hash = hashlib.sha256(norm.encode()).hexdigest()
    mine = shingle_hashes(norm)

    rows = db.all(
        "SELECT content_uid, norm_hash, shingles, source_ref FROM fingerprints "
        "WHERE scope = ? AND created_at >= ?", (scope, cutoff))
    for r in rows:
        if source_ref and r["source_ref"] == source_ref:
            return {"uid": r["content_uid"], "why": "same_source", "score": 1.0}
        if r["norm_hash"] == norm_hash:
            return {"uid": r["content_uid"], "why": "exact", "score": 1.0}
    for r in rows:
        score = similarity(mine, set(json.loads(r["shingles"])))
        if score >= threshold:
            return {"uid": r["content_uid"], "why": "near", "score": round(score, 3)}
    if semantic_check:
        hit = semantic_check(db, scope, text)
        if hit:
            return {"uid": hit, "why": "semantic", "score": 1.0}
    return None


def remember(db, content_uid: str, scope: str, text: str,
             source_ref: Optional[str] = None, now=None) -> None:
    now = now or utcnow()
    norm = normalize(text)
    db.execute(
        "INSERT INTO fingerprints (content_uid, scope, norm_hash, shingles, source_ref, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (content_uid, scope, hashlib.sha256(norm.encode()).hexdigest(),
         json.dumps(sorted(shingle_hashes(norm))), source_ref, iso(now)))


SEMANTIC_SYSTEM = """تو بررسی‌کننده تکرار خبر هستی. فهرست خبرهای قبلی و یک خبر جدید داده می‌شود.
اگر خبر جدید همان رویداد/پیام یکی از خبرهای قبلی است (حتی با کلمات متفاوت) فقط شناسه همان خبر را بنویس.
اگر موضوع یا اطلاعات تازه‌ای دارد فقط بنویس: NO"""


def llm_semantic_check(client, window_days: int = 15, max_candidates: int = 25):
    """Build a ``semantic_check`` that asks the LLM whether the text repeats a recent item.

    Only an answer that is exactly one of the candidate uids counts as a duplicate;
    anything else (including model chatter) means "not a duplicate".
    """
    def check(db, scope, text):
        cutoff = iso(utcnow() - timedelta(days=window_days))
        rows = db.all(
            "SELECT uid, title, body FROM content_items WHERE agent_key=? AND created_at>=? "
            "AND status NOT IN ('rejected','draft') ORDER BY created_at DESC LIMIT ?",
            (scope, cutoff, max_candidates))
        if not rows:
            return None
        listing = "\n".join(f"[{r['uid']}] {r['title']} — {r['body'][:200]}" for r in rows)
        answer = client.complete(SEMANTIC_SYSTEM, f"خبرهای قبلی:\n{listing}\n\nخبر جدید:\n{text[:1500]}", max_tokens=40).strip()
        return answer if answer in {r["uid"] for r in rows} else None
    return check
