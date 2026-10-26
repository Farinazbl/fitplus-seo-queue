"""SQLite storage layer (Phase 1).

The schema uses plain portable SQL so the move to PostgreSQL later only touches
this module (connection handling, AUTOINCREMENT -> SERIAL, BEGIN IMMEDIATE).
"""
import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY,
    project TEXT NOT NULL DEFAULT 'default',
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    signature TEXT NOT NULL DEFAULT '',
    config TEXT NOT NULL DEFAULT '{}',
    UNIQUE (project, name)
);
CREATE TABLE IF NOT EXISTS agents (
    key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    agent_type TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    prompt TEXT NOT NULL DEFAULT '',
    sources TEXT NOT NULL DEFAULT '[]',
    template TEXT NOT NULL DEFAULT '',
    signature TEXT NOT NULL DEFAULT '',
    schedule TEXT NOT NULL DEFAULT '[]',
    publish_mode TEXT NOT NULL DEFAULT 'manual_approval',
    channel_id INTEGER REFERENCES channels(id),
    policy TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    agent_key TEXT,
    payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    run_at TEXT NOT NULL,
    locked_at TEXT,
    worker TEXT,
    last_error TEXT,
    note TEXT,
    dedupe_key TEXT UNIQUE,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_run ON jobs (status, run_at);
CREATE TABLE IF NOT EXISTS content_items (
    uid TEXT PRIMARY KEY,
    agent_key TEXT NOT NULL,
    job_id INTEGER,
    kind TEXT NOT NULL DEFAULT 'text',
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_content_status ON content_items (status);
CREATE TABLE IF NOT EXISTS fingerprints (
    id INTEGER PRIMARY KEY,
    content_uid TEXT NOT NULL,
    scope TEXT NOT NULL,
    norm_hash TEXT NOT NULL,
    shingles TEXT NOT NULL,
    source_ref TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fp_scope ON fingerprints (scope, created_at);
CREATE TABLE IF NOT EXISTS publish_log (
    id INTEGER PRIMARY KEY,
    content_uid TEXT NOT NULL,
    destination TEXT NOT NULL,
    external_id TEXT,
    ok INTEGER NOT NULL,
    manual INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY,
    content_uid TEXT NOT NULL,
    reasons TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    decided_by TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY,
    agent_key TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    external_id TEXT NOT NULL UNIQUE,
    phone TEXT,
    name TEXT NOT NULL DEFAULT '',
    consent INTEGER NOT NULL DEFAULT 0,
    consent_at TEXT,
    opted_out INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contact_events (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    at TEXT NOT NULL,
    end_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_contact ON contact_events (contact_id, kind);
CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    segment TEXT NOT NULL,
    text TEXT NOT NULL,
    send_at TEXT NOT NULL,
    max_recipients INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sms_messages (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    scenario TEXT NOT NULL,
    step_key TEXT NOT NULL,
    campaign_id INTEGER,
    text TEXT,
    send_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    reason TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    provider_id TEXT,
    sent_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (contact_id, step_key)
);
CREATE INDEX IF NOT EXISTS idx_sms_due ON sms_messages (status, send_at);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS citizens (
    ig_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    follow_ok INTEGER NOT NULL DEFAULT 0,
    consent_display INTEGER NOT NULL DEFAULT 0,
    job TEXT,
    source TEXT NOT NULL DEFAULT 'volunteer',
    registered_at TEXT NOT NULL,
    verified_at TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS city_comments (
    comment_id TEXT PRIMARY KEY,
    ig_id TEXT NOT NULL REFERENCES citizens(ig_id) ON DELETE CASCADE ON UPDATE CASCADE,
    post_id TEXT NOT NULL,
    text TEXT NOT NULL,
    valid INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_city_comments_user ON city_comments (ig_id, valid, created_at);
CREATE TABLE IF NOT EXISTS city_runs (
    week_key TEXT PRIMARY KEY,
    seed TEXT NOT NULL,
    candidates INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS city_selections (
    id INTEGER PRIMARY KEY,
    week_key TEXT NOT NULL,
    ig_id TEXT NOT NULL,
    job TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (week_key, ig_id)
);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    name TEXT NOT NULL,
    day TEXT NOT NULL,
    value REAL NOT NULL,
    UNIQUE (source, name, day)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT,
    detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""


class DB:
    """Thread-safe wrapper: one autocommit connection per thread."""

    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()
        with self.tx() as c:
            for stmt in SCHEMA.split(";"):
                if stmt.strip():
                    c.execute(stmt)

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, isolation_level=None, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.conn = c
        return c

    @contextmanager
    def tx(self):
        c = self.conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        return self.conn().execute(sql, params)

    def one(self, sql: str, params=()):
        row = self.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params=()):
        return [dict(r) for r in self.execute(sql, params).fetchall()]
