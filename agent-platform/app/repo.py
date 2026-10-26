"""Data access helpers. JSON columns are decoded/encoded here and nowhere else."""
import json
from typing import Optional

from .util import iso, utcnow

AGENT_JSON = ("sources", "schedule", "policy")
EDITABLE_AGENT_FIELDS = {
    "title", "enabled", "prompt", "sources", "template", "signature",
    "schedule", "publish_mode", "channel_id", "policy",
}


# ---------- audit ----------
def audit(db, actor: str, action: str, entity: str, entity_id=None, detail=None, now=None):
    db.execute(
        "INSERT INTO audit_log (actor, action, entity, entity_id, detail, created_at) VALUES (?,?,?,?,?,?)",
        (actor, action, entity, None if entity_id is None else str(entity_id),
         json.dumps(detail or {}, ensure_ascii=False), iso(now or utcnow())))


# ---------- channels ----------
def _channel(row):
    if row:
        row["config"] = json.loads(row["config"])
    return row


def upsert_channel(db, kind: str, name: str, project: str = "default",
                   signature: str = "", config: Optional[dict] = None) -> int:
    existing = db.one("SELECT id FROM channels WHERE project=? AND name=?", (project, name))
    cfg = json.dumps(config or {}, ensure_ascii=False)
    if existing:
        db.execute("UPDATE channels SET kind=?, signature=?, config=? WHERE id=?",
                   (kind, signature, cfg, existing["id"]))
        return existing["id"]
    cur = db.execute("INSERT INTO channels (project, kind, name, signature, config) VALUES (?,?,?,?,?)",
                     (project, kind, name, signature, cfg))
    return cur.lastrowid


def get_channel(db, channel_id) -> Optional[dict]:
    if channel_id is None:
        return None
    return _channel(db.one("SELECT * FROM channels WHERE id=?", (channel_id,)))


def list_channels(db):
    return [_channel(r) for r in db.all("SELECT * FROM channels ORDER BY id")]


# ---------- agents ----------
def _agent(row):
    if row:
        for f in AGENT_JSON:
            row[f] = json.loads(row[f])
        row["enabled"] = bool(row["enabled"])
    return row


def get_agent(db, key: str) -> Optional[dict]:
    return _agent(db.one("SELECT * FROM agents WHERE key=?", (key,)))


def list_agents(db):
    return [_agent(r) for r in db.all("SELECT * FROM agents ORDER BY rowid")]


def upsert_agent(db, key: str, **fields) -> None:
    """Create an agent, or replace the given fields of an existing one (no audit)."""
    now = iso(utcnow())
    if get_agent(db, key) is None:
        base = dict(title=key, agent_type="demo", enabled=0, prompt="", sources=[], template="",
                    signature="", schedule=[], publish_mode="manual_approval",
                    channel_id=None, policy={})
        base.update(fields)
        for f in AGENT_JSON:
            base[f] = json.dumps(base[f], ensure_ascii=False)
        base["enabled"] = int(bool(base["enabled"]))
        db.execute(
            "INSERT INTO agents (key,title,agent_type,enabled,prompt,sources,template,signature,"
            "schedule,publish_mode,channel_id,policy,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, base["title"], base["agent_type"], base["enabled"], base["prompt"], base["sources"],
             base["template"], base["signature"], base["schedule"], base["publish_mode"],
             base["channel_id"], base["policy"], now))
    else:
        update_agent(db, key, fields, actor=None)


def update_agent(db, key: str, fields: dict, actor: Optional[str]) -> dict:
    """Edit an agent's settings; every change is written to the audit log."""
    current = get_agent(db, key)
    if current is None:
        raise KeyError(key)
    unknown = set(fields) - EDITABLE_AGENT_FIELDS - {"agent_type"}
    if unknown:
        raise ValueError(f"فیلد نامعتبر: {', '.join(sorted(unknown))}")
    if "publish_mode" in fields and fields["publish_mode"] not in ("auto", "manual_approval"):
        raise ValueError("publish_mode باید auto یا manual_approval باشد")
    sets, params, changed = [], [], {}
    for name, value in fields.items():
        if current.get(name) == value:
            continue
        changed[name] = {"from": current.get(name), "to": value}
        stored = json.dumps(value, ensure_ascii=False) if name in AGENT_JSON else (
            int(bool(value)) if name == "enabled" else value)
        sets.append(f"{name}=?")
        params.append(stored)
    if sets:
        sets.append("updated_at=?")
        params.append(iso(utcnow()))
        db.execute(f"UPDATE agents SET {', '.join(sets)} WHERE key=?", (*params, key))
        if actor is not None:
            audit(db, actor, "agent.update", "agent", key, changed)
    return get_agent(db, key)


# ---------- content ----------
def _content(row):
    if row:
        row["meta"] = json.loads(row["meta"])
    return row


def insert_content(db, uid, agent_key, job_id, kind, title, body, meta, status, reason=None):
    now = iso(utcnow())
    db.execute(
        "INSERT INTO content_items (uid,agent_key,job_id,kind,title,body,meta,status,reason,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (uid, agent_key, job_id, kind, title, body, json.dumps(meta or {}, ensure_ascii=False),
         status, reason, now, now))


def set_content_status(db, uid, status, reason=None, body=None):
    if body is None:
        db.execute("UPDATE content_items SET status=?, reason=?, updated_at=? WHERE uid=?",
                   (status, reason, iso(utcnow()), uid))
    else:
        db.execute("UPDATE content_items SET status=?, reason=?, body=?, updated_at=? WHERE uid=?",
                   (status, reason, body, iso(utcnow()), uid))


def get_content(db, uid) -> Optional[dict]:
    return _content(db.one("SELECT * FROM content_items WHERE uid=?", (uid,)))


def list_content(db, status=None, agent_key=None, limit=100):
    where, params = [], []
    if status:
        where.append("status=?")
        params.append(status)
    if agent_key:
        where.append("agent_key=?")
        params.append(agent_key)
    sql = "SELECT * FROM content_items"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    return [_content(r) for r in db.all(sql, (*params, limit))]


# ---------- publish log / approvals ----------
def add_publish_log(db, uid, destination, ok, external_id=None, manual=False, error=None):
    db.execute(
        "INSERT INTO publish_log (content_uid,destination,external_id,ok,manual,error,created_at) VALUES (?,?,?,?,?,?,?)",
        (uid, destination, external_id, int(ok), int(manual), error, iso(utcnow())))


def has_successful_publish(db, uid) -> bool:
    return db.one("SELECT 1 AS x FROM publish_log WHERE content_uid=? AND ok=1", (uid,)) is not None


def add_approval(db, uid, reasons) -> int:
    cur = db.execute("INSERT INTO approvals (content_uid,reasons,status,created_at) VALUES (?,?,'pending',?)",
                     (uid, json.dumps(reasons, ensure_ascii=False), iso(utcnow())))
    return cur.lastrowid


def _approval(row):
    if row:
        row["reasons"] = json.loads(row["reasons"])
    return row


def get_approval(db, approval_id):
    return _approval(db.one("SELECT * FROM approvals WHERE id=?", (approval_id,)))


def list_approvals(db, status="pending", limit=100):
    rows = db.all(
        "SELECT a.*, c.title, c.body, c.agent_key, c.kind FROM approvals a "
        "JOIN content_items c ON c.uid = a.content_uid WHERE a.status=? ORDER BY a.id DESC LIMIT ?",
        (status, limit))
    return [_approval(r) for r in rows]


# ---------- trace ----------
def trace(db, uid) -> Optional[dict]:
    """Everything known about one content item: from generation to publication."""
    content = get_content(db, uid)
    if not content:
        return None
    return {
        "content": content,
        "job": db.one("SELECT * FROM jobs WHERE id=?", (content["job_id"],)) if content["job_id"] else None,
        "approvals": [_approval(r) for r in db.all(
            "SELECT * FROM approvals WHERE content_uid=? ORDER BY id", (uid,))],
        "publish_log": db.all("SELECT * FROM publish_log WHERE content_uid=? ORDER BY id", (uid,)),
        "audit": db.all("SELECT * FROM audit_log WHERE entity='content' AND entity_id=? ORDER BY id", (uid,)),
    }
