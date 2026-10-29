import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from starlette.testclient import TestClient

from app import orchestrator, queue, repo, scheduler, seed
from app.api import create_app
from app.config import Settings
from app.db import DB
from app.util import utcnow


def client():
    s = Settings(db_path=os.path.join(tempfile.mkdtemp(), "m.db"),
                 tokens={"a" * 20: ("sara", "admin"), "e" * 20: ("reza", "editor"), "v" * 20: ("mina", "viewer")})
    return TestClient(create_app(s)), {"X-API-Token": "e" * 20}, {"X-API-Token": "v" * 20}, {"X-API-Token": "a" * 20}


class MonitorTests(unittest.TestCase):
    def test_external_events_are_recorded_idempotently_without_touching_the_flow(self):
        c, ed, vw, ad = client()
        ev = {"agent": "doctor_regime", "external_id": "bale-msg-55", "title": "پست امروز", "text": "متن پست منتشرشده"}
        self.assertEqual(c.post("/api/external/events", headers=ed, json=ev).status_code, 201)
        r = c.post("/api/external/events", headers=ed, json=ev)
        self.assertTrue(r.json()["duplicate"])
        c.post("/api/external/events", headers=ed, json={**ev, "external_id": "bale-msg-56", "ok": False, "error": "ارسال نشد"})
        items = c.get("/api/content?agent=doctor_regime", headers=vw).json()
        self.assertEqual(sorted(i["status"] for i in items), ["failed", "published"])
        self.assertEqual(c.get("/api/overview", headers=vw).json()["content"]["failed"], 1)
        trace = c.get(f"/api/content/{items[0]['uid']}", headers=vw).json()
        self.assertEqual(len(trace["publish_log"]), 1)

    def test_only_monitor_agents_accept_events_and_validation(self):
        c, ed, vw, ad = client()
        self.assertEqual(c.post("/api/external/events", headers=ed, json={"agent": "seo", "external_id": "1", "text": "x"}).status_code, 409)
        self.assertEqual(c.post("/api/external/events", headers=ed, json={"agent": "fitplus", "text": "x"}).status_code, 400)
        self.assertEqual(c.post("/api/external/events", headers=ed, json={"agent": "nope", "external_id": "1", "text": "x"}).status_code, 404)
        self.assertEqual(c.post("/api/external/events", headers=vw, json={"agent": "fitplus", "external_id": "1", "text": "x"}).status_code, 403)

    def test_monitor_agent_cannot_be_run_as_generator(self):
        c, ed, vw, ad = client()
        c.put("/api/agents/fitplus", headers=ed, json={"enabled": True})
        c.post("/api/agents/fitplus/run", headers=ed, json={})
        tick = c.post("/api/worker/tick", headers=ad).json()
        self.assertEqual(tick["processed"][0]["status"], "retry")  # fails loudly, never invents content


class MetricsAndReportTests(unittest.TestCase):
    def test_metrics_upsert_and_validation(self):
        c, ed, vw, ad = client()
        row = {"source": "gsc", "name": "organic_visits", "day": "2026-11-01", "value": 30}
        self.assertEqual(c.post("/api/metrics", headers=ed, json={"rows": [row]}).status_code, 201)
        c.post("/api/metrics", headers=ed, json={"rows": [{**row, "value": 35}]})
        rows = c.get("/api/metrics?name=organic_visits", headers=vw).json()
        self.assertEqual([r["value"] for r in rows], [35])
        for bad in ({"rows": []}, {"rows": [{**row, "day": "01/11"}]}, {"rows": [{**row, "value": "x"}]}, {}):
            self.assertEqual(c.post("/api/metrics", headers=ed, json=bad).status_code, 400, bad)

    def test_weekly_report_summarises_articles_keywords_and_metrics_and_is_not_published(self):
        db = DB(os.path.join(tempfile.mkdtemp(), "r.db"))
        seed.ensure(db)
        repo.update_agent(db, "seo", {"enabled": True, "sources": ["الف", "ب", "پ", "ت"]}, actor=None)
        repo.insert_content(db, "a1", "seo", None, "article", "t", "b", {}, "published")
        repo.insert_content(db, "a2", "seo", None, "article", "t", "b", {}, "pending_approval")
        db.execute("INSERT INTO fingerprints (content_uid,scope,norm_hash,shingles,source_ref,created_at) VALUES ('a1','seo','h','[]','kw:الف',?)",
                   (utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),))
        today = utcnow()
        for i, v in enumerate([10, 12, 14]):
            db.execute("INSERT INTO metrics (source,name,day,value) VALUES ('gsc','organic_visits',?,?)",
                       ((today - timedelta(days=i)).strftime("%Y-%m-%d"), v))
        for i, v in enumerate([5, 5]):
            db.execute("INSERT INTO metrics (source,name,day,value) VALUES ('gsc','organic_visits',?,?)",
                       ((today - timedelta(days=8 + i)).strftime("%Y-%m-%d"), v))
        queue.enqueue(db, "run_agent", "seo", {"task": "weekly_report"})
        orchestrator.drain(db, "t", Settings(db_path="x", tokens={}))
        rep = repo.list_content(db, status="report")[0]
        self.assertIn("مقاله‌های این هفته: 2", rep["body"])
        self.assertIn("باقی‌مانده: 3", rep["body"])
        self.assertIn("organic_visits: 36 (هفته قبل: 10)", rep["body"])
        self.assertIn("نه تعهد", rep["body"])
        self.assertEqual(queue.counts(db)["queued"], 0)           # nothing to publish
        self.assertEqual(db.one("SELECT COUNT(*) AS n FROM publish_log")["n"], 0)


class CalendarTests(unittest.TestCase):
    def test_upcoming_lists_future_slots_in_order_for_enabled_agents_only(self):
        db = DB(os.path.join(tempfile.mkdtemp(), "cal.db"))
        seed.ensure(db)
        repo.update_agent(db, "bale_tax", {"enabled": True}, actor=None)
        repo.update_agent(db, "seo", {"enabled": True}, actor=None)
        tz = ZoneInfo("Asia/Tehran")
        now = datetime(2026, 10, 26, 8, 0, tzinfo=tz).astimezone(timezone.utc)  # Monday 08:00
        cal = scheduler.upcoming(db, 7, now)
        tax = [c for c in cal if c["agent"] == "bale_tax"]
        self.assertEqual(len(tax), 8)                              # 09:00 today .. 09:00 in 7 days
        self.assertTrue(tax[0]["at"].startswith("2026-10-26T09:00"))
        seo = [(c["at"][:10], c["task"]) for c in cal if c["agent"] == "seo"]
        self.assertEqual(seo, [("2026-10-26", "article"), ("2026-10-28", "article"), ("2026-10-29", "article"),
                               ("2026-10-31", "article"), ("2026-11-01", "weekly_report"), ("2026-11-02", "article")])   # 4 articles/week
        self.assertEqual([c["at"] for c in cal], sorted(c["at"] for c in cal))
        self.assertNotIn("youtube_kids", {c["agent"] for c in cal})


if __name__ == "__main__":
    unittest.main()
