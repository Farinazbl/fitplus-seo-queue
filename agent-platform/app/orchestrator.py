"""The pipeline every piece of content goes through.

    agent drafts -> policy -> duplicate check -> signature -> approval gate
        -> (publish job) -> connector -> publish log

Generation and publishing are separate jobs on purpose: if a destination is
temporarily down, only the *publish* job is retried, so a retry can never
regenerate (and duplicate) the content.
"""
from typing import Optional

from . import agents as _agents  # noqa: F401  (registers built-in agents)
from . import dedup, llm, policy, queue, repo
from .agents import base as agent_registry
from . import connectors as _connectors  # noqa: F401  (registers real connectors)
from .connectors import base as connectors
from .util import iso, new_uid, utcnow


def _safe(fn):
    """The semantic layer is optional: if the LLM is down, fall back to the text layers only."""
    if fn is None:
        return None

    def wrapped(db, scope, text):
        try:
            return fn(db, scope, text)
        except Exception:  # noqa: BLE001
            return None
    return wrapped


# ---------- generation ----------
def _process_draft(db, cfg, job, draft, settings) -> str:
    uid = new_uid()
    if draft.kind == "report":  # internal reports are stored for the dashboard, never published
        repo.insert_content(db, uid, cfg["key"], job["id"], "report", draft.title, draft.body, draft.meta, "report")
        return "report"
    repo.insert_content(db, uid, cfg["key"], job["id"], draft.kind, draft.title, draft.body,
                        draft.meta, "draft")

    violations, auto_review = policy.check(cfg["policy"], draft.body)
    if violations:
        repo.set_content_status(db, uid, "rejected", "؛ ".join(violations))
        repo.audit(db, "system", "content.rejected", "content", uid, {"violations": violations})
        return "rejected"

    semantic = None
    if cfg["policy"].get("semantic_dedup"):
        try:
            semantic = dedup.llm_semantic_check(llm.get_client(), settings.dedup_window_days)
        except Exception:  # noqa: BLE001 - never block on the optional layer
            semantic = None
    dup = dedup.find_duplicate(
        db, scope=cfg["key"], text=draft.body, source_ref=draft.meta.get("source_ref"),
        window_days=settings.dedup_window_days, threshold=settings.dedup_threshold,
        semantic_check=_safe(semantic))
    if dup:
        reason = f"تکراری ({dup['why']}) نسبت به {dup['uid']}"
        repo.set_content_status(db, uid, "rejected", reason)
        repo.audit(db, "system", "content.duplicate", "content", uid, dup)
        return "duplicate"

    channel = repo.get_channel(db, cfg["channel_id"])
    signature = cfg["signature"] or (channel["signature"] if channel else "")
    final_body = f"{draft.body}\n\n{signature}" if signature else draft.body
    dedup.remember(db, uid, cfg["key"], draft.body, draft.meta.get("source_ref"))

    reasons = auto_review + list(draft.review_reasons)
    if cfg["publish_mode"] != "auto" and not reasons:
        reasons.append("حالت انتشار این ایجنت «تأیید دستی» است")
    if reasons:
        repo.set_content_status(db, uid, "pending_approval", None, body=final_body)
        repo.add_approval(db, uid, reasons)
        return "pending_approval"

    repo.set_content_status(db, uid, "approved", None, body=final_body)
    queue.enqueue(db, "publish", cfg["key"], {"content_uid": uid}, dedupe_key=f"publish:{uid}")
    return "queued_for_publish"


def run_agent_job(db, job, settings) -> str:
    cfg = repo.get_agent(db, job["agent_key"])
    if cfg is None or not cfg["enabled"]:
        return "رد شد: ایجنت غیرفعال است"
    agent = agent_registry.get(cfg["agent_type"], db)
    drafts = agent.generate(cfg, job["payload"].get("task", "default"), job["payload"])
    outcomes = [_process_draft(db, cfg, job, d, settings) for d in drafts]
    return f"{len(drafts)} پیش‌نویس: " + "، ".join(outcomes) if outcomes else "پیش‌نویسی تولید نشد"


# ---------- publishing ----------
def publish_job(db, job) -> str:
    uid = job["payload"]["content_uid"]
    content = repo.get_content(db, uid)
    if not content or content["status"] != "approved":
        return "رد شد: محتوا در وضعیت قابل انتشار نیست"
    if repo.has_successful_publish(db, uid):
        return "قبلاً منتشر شده است"
    cfg = repo.get_agent(db, content["agent_key"])
    channel = repo.get_channel(db, cfg["channel_id"]) if cfg else None
    destination = channel["name"] if channel else "manual"
    connector = connectors.for_channel(channel)
    try:
        result = connector.publish(content, channel)
    except connectors.PermanentError as e:
        repo.add_publish_log(db, uid, destination, ok=False, error=str(e))
        repo.set_content_status(db, uid, "failed", str(e))
        repo.audit(db, "system", "content.publish_failed", "content", uid, {"error": str(e)})
        return "ناموفق (دائمی)"
    except connectors.TransientError as e:
        repo.add_publish_log(db, uid, destination, ok=False, error=str(e))
        raise
    repo.add_publish_log(db, uid, destination, ok=True, external_id=result.external_id, manual=result.manual)
    repo.set_content_status(db, uid, "ready_manual" if result.manual else "published")
    repo.audit(db, "system", "content.ready_manual" if result.manual else "content.published",
               "content", uid, {"destination": destination, "external_id": result.external_id})
    return "آماده انتشار دستی" if result.manual else "منتشر شد"


# ---------- worker step ----------
def process_next(db, worker: str, settings) -> Optional[dict]:
    """Claim and run one job. Returns a summary, or ``None`` if the queue is empty."""
    job = queue.claim(db, worker)
    if job is None:
        return None
    try:
        if job["kind"] == "run_agent":
            note = run_agent_job(db, job, settings)
        elif job["kind"] == "publish":
            note = publish_job(db, job)
        else:
            raise ValueError(f"نوع وظیفه ناشناخته: {job['kind']}")
        queue.complete(db, job["id"], note)
        return {"job_id": job["id"], "kind": job["kind"], "status": "done", "note": note}
    except Exception as e:  # noqa: BLE001 - any failure goes through retry/dead-letter
        state = queue.fail(db, job, f"{type(e).__name__}: {e}")
        if state == "dead" and job["kind"] == "publish":
            uid = job["payload"].get("content_uid")
            repo.set_content_status(db, uid, "failed", f"انتشار پس از چند تلاش ناموفق ماند: {e}")
            repo.audit(db, "system", "content.publish_failed", "content", uid, {"error": str(e)})
        return {"job_id": job["id"], "kind": job["kind"], "status": state, "error": str(e)}


def drain(db, worker: str, settings, limit: int = 50) -> list:
    out = []
    for _ in range(limit):
        r = process_next(db, worker, settings)
        if r is None:
            break
        out.append(r)
    return out


# ---------- human decisions ----------
def decide_approval(db, approval_id: int, decision: str, actor: str,
                    note: Optional[str] = None, edited_body: Optional[str] = None) -> dict:
    if decision not in ("approved", "rejected"):
        raise ValueError("decision باید approved یا rejected باشد")
    appr = repo.get_approval(db, approval_id)
    if appr is None:
        raise KeyError(approval_id)
    if appr["status"] != "pending":
        raise ValueError("این مورد قبلاً تصمیم‌گیری شده است")
    uid = appr["content_uid"]
    content = repo.get_content(db, uid)
    db.execute("UPDATE approvals SET status=?, decided_by=?, note=?, decided_at=? WHERE id=?",
               (decision, actor, note, iso(utcnow()), approval_id))
    if decision == "approved":
        repo.set_content_status(db, uid, "approved", None, body=edited_body)
        queue.enqueue(db, "publish", content["agent_key"], {"content_uid": uid},
                      dedupe_key=f"publish:{uid}")
    else:
        repo.set_content_status(db, uid, "rejected", note or "ردشده توسط ناظر")
    repo.audit(db, actor, f"approval.{decision}", "content", uid, {"approval_id": approval_id, "note": note,
                                                                   "edited": edited_body is not None})
    return repo.get_approval(db, approval_id)
