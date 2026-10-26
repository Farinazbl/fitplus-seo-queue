import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from starlette.testclient import TestClient

from app import config, dedup, orchestrator, policy, queue, repo, scheduler, seed
from app.api import create_app
from app.config import Settings
from app.connectors import base as connectors
from app.db import DB
from app.util import utcnow

TOKENS = {"a" * 20: ("sara", "admin"), "e" * 20: ("reza", "editor"), "v" * 20: ("mina", "viewer")}


def make_db():
    d = tempfile.mkdtemp()
    return DB(os.path.join(d, "t.db")), d


class DedupTests(unittest.TestCase):
    def setUp(self):
        self.db, _ = make_db()

    def test_persian_variants_are_the_same_text(self):
        a = "مالیات بر ارزش افزوده برای سال ۱۴۰۴ تغییر کرد و مؤدیان باید اظهارنامه بدهند"
        b = "مالیات بر ارزش افزوده برای سال 1404 تغییر کرد و مؤدیان باید اظهارنامه بدهند!"
        dedup.remember(self.db, "c1", "bale_tax", a)
        self.assertEqual(dedup.find_duplicate(self.db, "bale_tax", b)["why"], "exact")

    def test_near_duplicate_and_different_text(self):
        a = "سازمان امور مالیاتی مهلت ارائه اظهارنامه مشاغل را تا پایان ماه آینده تمدید کرد و اعلام شد"
        near = "سازمان امور مالیاتی مهلت ارائه اظهارنامه مشاغل را تا پایان ماه آینده تمدید کرد"
        other = "نرخ جدید حقوق و دستمزد برای کارگران در سال آینده اعلام خواهد شد و بخشنامه ابلاغ شد"
        dedup.remember(self.db, "c1", "bale_tax", a)
        self.assertEqual(dedup.find_duplicate(self.db, "bale_tax", near)["why"], "near")
        self.assertIsNone(dedup.find_duplicate(self.db, "bale_tax", other))

    def test_same_source_ref_and_scope_isolation(self):
        dedup.remember(self.db, "c1", "bale_tax", "متن اول درباره موضوعی کاملاً متفاوت", source_ref="msg-17")
        hit = dedup.find_duplicate(self.db, "bale_tax", "متن دیگر و جدید با کلمات دیگر", source_ref="msg-17")
        self.assertEqual(hit["why"], "same_source")
        self.assertIsNone(dedup.find_duplicate(self.db, "bale_accounting", "متن دیگر و جدید با کلمات دیگر",
                                               source_ref="msg-17"))

    def test_window_is_fifteen_days(self):
        t = "یک خبر مالیاتی با متن مشخص و کاملاً یکتا برای آزمون پنجره زمانی"
        old = utcnow() - timedelta(days=16)
        dedup.remember(self.db, "c1", "bale_tax", t, now=old)
        self.assertIsNone(dedup.find_duplicate(self.db, "bale_tax", t))
        dedup.remember(self.db, "c2", "bale_tax", t, now=utcnow() - timedelta(days=14))
        self.assertIsNotNone(dedup.find_duplicate(self.db, "bale_tax", t))


class PolicyTests(unittest.TestCase):
    def test_tax_channel_rules(self):
        p = seed.TAX_POLICY
        v, _ = policy.check(p, "برای جزئیات به https://example.com مراجعه کنید " * 3)
        self.assertTrue(any("لینک" in x for x in v))
        v, _ = policy.check(p, "این خبر از کانال Accounting_House برداشته شده است " * 3)
        self.assertTrue(any("منبع" in x for x in v))
        v, _ = policy.check(p, "در دوره آموزشی ما شرکت کنید و مالیات را یاد بگیرید " * 3)
        self.assertTrue(any("ممنوع" in x for x in v))
        v, _ = policy.check(p, "خبر عادی درباره تغییر مهلت اظهارنامه مالیاتی برای مشاغل و شرکت‌ها " * 2)
        self.assertEqual(v, [])

    def test_review_terms_do_not_reject(self):
        v, review = policy.check({"review_terms": ["سود قطعی"]}, "این طرح سود قطعی دارد")
        self.assertEqual(v, [])
        self.assertEqual(len(review), 1)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.db, _ = make_db()

    def test_dedupe_key_makes_enqueue_idempotent(self):
        self.assertIsNotNone(queue.enqueue(self.db, "run_agent", "x", dedupe_key="k"))
        self.assertIsNone(queue.enqueue(self.db, "run_agent", "x", dedupe_key="k"))

    def test_retry_with_backoff_then_dead(self):
        queue.enqueue(self.db, "run_agent", "x", max_attempts=2)
        now = utcnow()
        job = queue.claim(self.db, "w", now)
        self.assertEqual(queue.fail(self.db, job, "boom", now), "retry")
        self.assertIsNone(queue.claim(self.db, "w", now))  # not due yet
        later = now + timedelta(seconds=queue.backoff_seconds(1) + 1)
        job = queue.claim(self.db, "w", later)
        self.assertEqual(job["attempts"], 2)
        self.assertEqual(queue.fail(self.db, job, "boom", later), "dead")
        self.assertEqual(queue.counts(self.db)["dead"], 1)
        self.assertTrue(any(a["code"] == "dead_jobs" for a in queue.alerts(self.db)))

    def test_stale_running_job_is_recovered(self):
        queue.enqueue(self.db, "run_agent", "x")
        now = utcnow()
        queue.claim(self.db, "crashed-worker", now)
        self.assertEqual(queue.recover_stale(self.db, 15, now + timedelta(minutes=30)), 1)
        self.assertEqual(queue.counts(self.db)["queued"], 1)


class SchedulerTests(unittest.TestCase):
    tz = ZoneInfo("Asia/Tehran")

    def at(self, y, mo, d, h, mi):
        return datetime(y, mo, d, h, mi, tzinfo=self.tz).astimezone(timezone.utc)

    def test_daily_slot_only_inside_lateness_window(self):
        item = {"type": "daily", "times": ["09:00"]}
        self.assertEqual(len(scheduler.due_slots("a", item, self.at(2026, 10, 26, 9, 5), self.tz)), 1)
        self.assertEqual(scheduler.due_slots("a", item, self.at(2026, 10, 26, 8, 59), self.tz), [])
        self.assertEqual(scheduler.due_slots("a", item, self.at(2026, 10, 26, 13, 0), self.tz), [])

    def test_weekly_days_monday_is_zero(self):
        item = {"type": "weekly", "days": [0], "times": ["10:00"]}  # 2026-10-26 is a Monday
        self.assertEqual(len(scheduler.due_slots("a", item, self.at(2026, 10, 26, 10, 1), self.tz)), 1)
        self.assertEqual(scheduler.due_slots("a", item, self.at(2026, 10, 27, 10, 1), self.tz), [])

    def test_every_other_day(self):
        item = {"type": "every_n_days", "n": 2, "anchor": "2026-10-26", "times": ["17:00"]}
        hit = lambda d: len(scheduler.due_slots("a", item, self.at(2026, 10, d, 17, 1), self.tz))
        self.assertEqual([hit(26), hit(27), hit(28)], [1, 0, 1])

    def test_slot_after_midnight_tehran_still_found(self):
        item = {"type": "daily", "times": ["23:50"]}
        self.assertEqual(len(scheduler.due_slots("a", item, self.at(2026, 10, 27, 0, 20), self.tz)), 1)

    def test_tick_is_idempotent_and_skips_disabled(self):
        db, _ = make_db()
        repo.upsert_agent(db, "a1", agent_type="demo", enabled=True,
                          schedule=[{"type": "daily", "times": ["09:00"]}])
        repo.upsert_agent(db, "a2", agent_type="demo", enabled=False,
                          schedule=[{"type": "daily", "times": ["09:00"]}])
        now = self.at(2026, 10, 26, 9, 10)
        self.assertEqual(scheduler.tick(db, now), 1)
        self.assertEqual(scheduler.tick(db, now), 0)
        self.assertEqual(queue.counts(db)["queued"], 1)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.db, _ = make_db()
        self.settings = Settings(db_path="x", tokens={})
        seed.ensure(self.db)

    def run_demo(self, text, **extra):
        queue.enqueue(self.db, "run_agent", "demo", {"task": "manual", "text": text, **extra})
        return orchestrator.drain(self.db, "t", self.settings)

    def test_auto_publish_with_signature_and_full_trace(self):
        res = self.run_demo("نکته آزمایشی شماره یک برای کانال و مخاطبان")
        self.assertEqual([r["kind"] for r in res], ["run_agent", "publish"])
        item = repo.list_content(self.db, agent_key="demo")[0]
        self.assertEqual(item["status"], "published")
        self.assertTrue(item["body"].endswith("— کانال آزمایشی"))
        trace = repo.trace(self.db, item["uid"])
        self.assertEqual(len(trace["publish_log"]), 1)
        self.assertTrue(trace["publish_log"][0]["ok"])
        self.assertTrue(any(a["action"] == "content.published" for a in trace["audit"]))

    def test_duplicate_is_rejected_and_not_published(self):
        self.run_demo("خبر تکراری برای آزمون تشخیص تکرار در سامانه ایجنت‌ها")
        self.run_demo("خبر تکراری برای آزمون تشخیص تکرار در سامانه ایجنت‌ها!")
        statuses = sorted(c["status"] for c in repo.list_content(self.db, agent_key="demo"))
        self.assertEqual(statuses, ["published", "rejected"])
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM publish_log")["n"], 1)

    def test_policy_violation_is_rejected(self):
        self.run_demo("لینک: https://example.com")
        item = repo.list_content(self.db, agent_key="demo")[0]
        self.assertEqual(item["status"], "rejected")
        self.assertIn("لینک", item["reason"])

    def test_sensitive_content_waits_for_human_then_publishes(self):
        res = self.run_demo("متن حساس درباره سلامت", review_reasons=["ادعای سلامت"])
        self.assertEqual([r["kind"] for r in res], ["run_agent"])  # no publish job yet
        item = repo.list_content(self.db, agent_key="demo")[0]
        self.assertEqual(item["status"], "pending_approval")
        appr = repo.list_approvals(self.db)[0]
        orchestrator.decide_approval(self.db, appr["id"], "approved", "reza", "تأیید شد",
                                     edited_body="متن اصلاح‌شده توسط ناظر")
        orchestrator.drain(self.db, "t", self.settings)
        item = repo.get_content(self.db, item["uid"])
        self.assertEqual(item["status"], "published")
        self.assertEqual(item["body"], "متن اصلاح‌شده توسط ناظر")
        with self.assertRaises(ValueError):  # cannot decide twice
            orchestrator.decide_approval(self.db, appr["id"], "rejected", "reza")

    def test_human_rejection_never_publishes(self):
        self.run_demo("متن حساس دیگر", review_reasons=["x"])
        appr = repo.list_approvals(self.db)[0]
        orchestrator.decide_approval(self.db, appr["id"], "rejected", "reza", "نادرست است")
        orchestrator.drain(self.db, "t", self.settings)
        self.assertEqual(repo.get_content(self.db, appr["content_uid"])["status"], "rejected")
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM publish_log")["n"], 0)

    def test_transient_publish_error_retries_without_regenerating(self):
        class Flaky(connectors.Connector):
            calls = 0

            def publish(self, content, channel):
                Flaky.calls += 1
                if Flaky.calls < 2:
                    raise connectors.TransientError("timeout")
                return connectors.PublishResult(external_id="ext-1")

        connectors.register("flaky", Flaky())
        repo.upsert_channel(self.db, "flaky", "flaky-channel")
        cid = self.db.one("SELECT id FROM channels WHERE name='flaky-channel'")["id"]
        repo.update_agent(self.db, "demo", {"channel_id": cid}, actor=None)
        self.run_demo("متن برای آزمون خطای موقت در اتصال به مقصد انتشار")
        item = repo.list_content(self.db, agent_key="demo")[0]
        self.assertEqual(item["status"], "approved")  # first attempt failed, retry scheduled
        self.db.execute("UPDATE jobs SET run_at='2000-01-01T00:00:00Z' WHERE status='queued'")
        orchestrator.drain(self.db, "t", self.settings)
        self.assertEqual(repo.get_content(self.db, item["uid"])["status"], "published")
        self.assertEqual(len(repo.list_content(self.db, agent_key="demo")), 1)  # not regenerated
        logs = self.db.all("SELECT ok FROM publish_log ORDER BY id")
        self.assertEqual([l["ok"] for l in logs], [0, 1])

    def test_unsupported_destination_falls_back_to_manual_queue(self):
        repo.update_agent(self.db, "demo", {"channel_id": self.db.one(
            "SELECT id FROM channels WHERE kind='instagram' LIMIT 1")["id"]}, actor=None)
        self.run_demo("متنی که باید برای انتشار دستی آماده شود و منتشر نشود")
        item = repo.list_content(self.db, agent_key="demo")[0]
        self.assertEqual(item["status"], "ready_manual")

    def test_pending_agent_never_runs_and_dead_letters(self):
        repo.update_agent(self.db, "doctor_regime", {"enabled": True}, actor=None)
        queue.enqueue(self.db, "run_agent", "doctor_regime", {}, max_attempts=1)
        res = orchestrator.drain(self.db, "t", self.settings)
        self.assertEqual(res[0]["status"], "dead")


class ApiTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.settings = Settings(db_path=os.path.join(d, "api.db"), tokens=TOKENS)
        self.client = TestClient(create_app(self.settings))
        self.admin = {"X-API-Token": "a" * 20}
        self.editor = {"X-API-Token": "e" * 20}
        self.viewer = {"X-API-Token": "v" * 20}

    def test_auth_and_roles(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/api/overview").status_code, 401)
        self.assertEqual(self.client.get("/api/overview", headers={"X-API-Token": "bad"}).status_code, 401)
        self.assertEqual(self.client.get("/api/overview", headers=self.viewer).status_code, 200)
        self.assertEqual(self.client.put("/api/agents/demo", json={"title": "x"}, headers=self.viewer).status_code, 403)
        self.assertEqual(self.client.get("/api/audit", headers=self.editor).status_code, 403)
        self.assertEqual(self.client.get("/api/audit", headers=self.admin).status_code, 200)

    def test_agent_edit_is_audited_and_validated(self):
        r = self.client.put("/api/agents/bale_tax", headers=self.editor,
                            json={"signature": "— کانال نمونه", "enabled": True,
                                  "schedule": [{"type": "daily", "times": ["08:30"]}]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["schedule"][0]["times"], ["08:30"])
        audit = self.client.get("/api/audit", headers=self.admin).json()
        self.assertEqual(audit[0]["action"], "agent.update")
        self.assertEqual(audit[0]["actor"], "reza")
        for bad in ({"schedule": "daily"}, {"nope": 1}, {"enabled": "yes"}, {"publish_mode": "whatever"}):
            self.assertEqual(self.client.put("/api/agents/bale_tax", headers=self.editor, json=bad).status_code, 400, bad)
        self.assertEqual(self.client.put("/api/agents/ghost", headers=self.editor, json={}).status_code, 404)

    def test_run_tick_trace_and_approval_flow_over_http(self):
        r = self.client.post("/api/agents/demo/run", headers=self.editor, json={"text": "متن آزمایشی از طریق رابط برنامه‌نویسی"})
        self.assertEqual(r.status_code, 202)
        tick = self.client.post("/api/worker/tick", headers=self.admin).json()
        self.assertEqual([p["kind"] for p in tick["processed"]], ["run_agent", "publish"])
        items = self.client.get("/api/content?agent=demo", headers=self.viewer).json()
        trace = self.client.get(f"/api/content/{items[0]['uid']}", headers=self.viewer).json()
        self.assertEqual(trace["content"]["status"], "published")
        self.client.put("/api/agents/demo", headers=self.editor, json={"publish_mode": "manual_approval"})
        self.client.post("/api/agents/demo/run", headers=self.editor, json={"text": "متن دوم نیازمند تأیید انسانی است"})
        self.client.post("/api/worker/tick", headers=self.admin)
        pending = self.client.get("/api/approvals", headers=self.viewer).json()
        self.assertEqual(len(pending), 1)
        d = self.client.post(f"/api/approvals/{pending[0]['id']}/decide", headers=self.editor,
                             json={"decision": "approved"})
        self.assertEqual(d.status_code, 200)
        self.client.post("/api/worker/tick", headers=self.admin)
        ov = self.client.get("/api/overview", headers=self.viewer).json()
        self.assertEqual(ov["content"]["published"], 2)
        self.assertEqual(ov["pending_approvals"], 0)

    def test_disabled_agent_cannot_be_run_and_dashboard_is_served(self):
        self.assertEqual(self.client.post("/api/agents/seo/run", headers=self.editor, json={}).status_code, 409)
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("dir=\"rtl\"", page.text)


class ConfigTests(unittest.TestCase):
    def test_token_parsing_rules(self):
        t = config.parse_tokens("sara=" + "x" * 16 + ":admin")
        self.assertEqual(t["x" * 16], ("sara", "admin"))
        with self.assertRaises(ValueError):
            config.parse_tokens("short:admin")
        with self.assertRaises(ValueError):
            config.parse_tokens("x" * 16 + ":root")


if __name__ == "__main__":
    unittest.main()


class DashboardTests(unittest.TestCase):
    def test_every_tab_has_a_view_and_matching_api(self):
        import re
        html = open(os.path.join(os.path.dirname(__file__), "..", "app", "static", "index.html"), encoding="utf-8").read()
        tabs = re.findall(r'\["(\w+)","[^"]+"\]', html.split("const TABS =")[1].split("];")[0])
        for t in tabs:
            self.assertRegex(html, rf"\n  async {t}\(\)", t)
        d = tempfile.mkdtemp()
        c = TestClient(create_app(Settings(db_path=os.path.join(d, "d.db"), tokens={"a" * 20: ("sara", "admin")})))
        h = {"X-API-Token": "a" * 20}
        for path in ("/api/calendar?days=14", "/api/content?status=report", "/api/campaign/stats", "/api/city/stats",
                     "/api/campaign/messages?limit=30", "/api/city/citizens?status=pending&limit=30", "/api/jobs", "/api/audit"):
            self.assertEqual(c.get(path, headers=h).status_code, 200, path)
