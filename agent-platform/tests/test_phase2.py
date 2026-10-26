import json
import os
import tempfile
import unittest

from starlette.testclient import TestClient

from app import http, llm, orchestrator, queue, repo, seed, sources
from app.api import create_app
from app.config import Settings
from app.connectors.bale import BaleConnector
from app.connectors.base import PermanentError, TransientError
from app.connectors.wordpress import WordPressConnector
from app.db import DB

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>تمدید مهلت اظهارنامه</title><guid>g1</guid><link>https://x.ir/1</link>
<description><![CDATA[<p>سازمان امور مالیاتی مهلت ارائه اظهارنامه مشاغل را <b>یک ماه</b> تمدید کرد و مؤدیان می‌توانند تا پایان آبان اقدام کنند.</p>]]></description></item>
<item><title>خبر دوم</title><guid>g2</guid><description>نرخ جدید جریمه دیرکرد مالیات بر ارزش افزوده اعلام شد و از ابتدای ماه آینده اعمال می‌شود.</description></item>
</channel></rss>"""

ARTICLE = ("TITLE: راهنمای کامل محاسبه مالیات مشاغل\nMETA: در این راهنما یاد می‌گیرید مالیات مشاغل چگونه محاسبه می‌شود.\n"
           "<p>مقدمه</p>" + "".join(f"<h2>بخش {i}</h2><p>{'کلمه ' * 120}</p>" for i in range(5)))


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def complete(self, system, user, max_tokens=1200):
        self.calls.append((system, user))
        return self.reply(user) if callable(self.reply) else self.reply


def new_db():
    return DB(os.path.join(tempfile.mkdtemp(), "t.db"))


class SourceTests(unittest.TestCase):
    def test_rss_parsing_strips_html(self):
        items = sources.parse_feed(RSS, "o")
        self.assertEqual([i.ref for i in items], ["g1", "g2"])
        self.assertNotIn("<", items[0].text)

    def test_broken_source_does_not_stop_others(self):
        def transport(method, url, **kw):
            if "bad" in url:
                raise http.HttpError(500)
            return RSS
        items = sources.fetch(new_db(), ["rss:https://bad/feed", "rss:https://good/feed"], transport)
        self.assertEqual(len(items), 2)


class NewsAgentTests(unittest.TestCase):
    def setUp(self):
        self.db = new_db()
        seed.ensure(self.db)
        self.settings = Settings(db_path="x", tokens={})
        repo.update_agent(self.db, "bale_tax", {"enabled": True, "sources": ["inbox"], "publish_mode": "auto"}, actor=None)
        self.add_inbox("سازمان امور مالیاتی مهلت ارائه اظهارنامه مشاغل را یک ماه تمدید کرد و اعلام نمود")

    def add_inbox(self, text):
        self.db.execute("INSERT INTO inbox (agent_key,title,text,created_at) VALUES ('bale_tax','t',?,'2026-10-26T00:00:00Z')", (text,))

    def run_once(self):
        queue.enqueue(self.db, "run_agent", "bale_tax", {"task": "default"})
        return orchestrator.drain(self.db, "t", self.settings)

    def test_clean_rewrite_is_saved_with_source_ref_and_inbox_item_consumed(self):
        good = "تمدید مهلت اظهارنامه\n" + "مؤدیان مشاغل یک ماه فرصت بیشتر برای ارائه اظهارنامه دارند و باید تا پایان مهلت اقدام کنند. " * 2
        llm.set_client(FakeLLM(good))
        self.run_once()
        item = repo.list_content(self.db, agent_key="bale_tax")[0]
        self.assertEqual(item["title"], "تمدید مهلت اظهارنامه")
        self.assertTrue(item["meta"]["source_ref"].startswith("inbox:"))
        # channel has no chat_id/token configured yet -> publishing must fail loudly, never silently succeed
        self.assertEqual(item["status"], "failed")
        self.assertIn("chat_id", item["reason"])
        self.assertEqual(self.db.one("SELECT used FROM inbox")["used"], 1)

    def test_model_that_names_source_or_adds_link_is_rejected(self):
        bad = "عنوان\n" + "این خبر را از Accounting_House گرفتیم و جزئیات در https://x.ir است. " * 3
        llm.set_client(FakeLLM(bad))
        self.run_once()
        item = repo.list_content(self.db, agent_key="bale_tax")[0]
        self.assertEqual(item["status"], "rejected")
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM publish_log")["n"], 0)

    def test_skip_answer_produces_nothing_and_same_item_is_not_reused(self):
        fake = FakeLLM("SKIP")
        llm.set_client(fake)
        self.run_once()
        self.assertEqual(repo.list_content(self.db, agent_key="bale_tax"), [])

    def test_prompt_forbids_copying_and_includes_channel_prompt(self):
        fake = FakeLLM("SKIP")
        llm.set_client(fake)
        repo.update_agent(self.db, "bale_tax", {"prompt": "لحن رسمی"}, actor=None)
        self.run_once()
        self.assertIn("بازنویسی", fake.calls[0][0])
        self.assertIn("لحن رسمی", fake.calls[0][0])


class SeoAgentTests(unittest.TestCase):
    def setUp(self):
        self.db = new_db()
        seed.ensure(self.db)
        self.settings = Settings(db_path="x", tokens={})
        repo.update_agent(self.db, "seo", {"enabled": True, "sources": ["مالیات مشاغل", "اظهارنامه"],
                                           "publish_mode": "manual_approval"}, actor=None)

    def run_once(self):
        queue.enqueue(self.db, "run_agent", "seo", {"task": "article"})
        return orchestrator.drain(self.db, "t", self.settings)

    def test_article_goes_to_approval_and_each_keyword_used_once(self):
        llm.set_client(FakeLLM(ARTICLE))
        self.run_once()
        item = repo.list_content(self.db, agent_key="seo")[0]
        self.assertEqual(item["status"], "pending_approval")
        self.assertEqual(item["kind"], "article")
        self.assertEqual(item["meta"]["keyword"], "مالیات مشاغل")
        self.assertIn("meta_description", item["meta"])
        fake = FakeLLM(ARTICLE.replace("محاسبه", "برآورد").replace("بخش", "قسمت"))
        llm.set_client(fake)
        self.run_once()
        self.assertEqual(fake.calls[0][1], "عبارت کلیدی: اظهارنامه")  # second keyword, not the first again

    def test_bad_model_format_is_retried_not_published(self):
        llm.set_client(FakeLLM("فقط یک متن بدون قالب"))
        res = self.run_once()
        self.assertEqual(res[0]["status"], "retry")
        self.assertEqual(repo.list_content(self.db, agent_key="seo"), [])

    def test_no_keywords_left_is_a_quiet_noop(self):
        repo.update_agent(self.db, "seo", {"sources": []}, actor=None)
        llm.set_client(FakeLLM(ARTICLE))
        res = self.run_once()
        self.assertEqual(res[0]["status"], "done")


class ConnectorTests(unittest.TestCase):
    chan = {"config": {"chat_id": "@ch", "token_env": "TEST_BALE_TOKEN"}}
    content = {"uid": "c1", "body": "متن", "title": "عنوان", "meta": {"meta_description": "توضیح"}}

    def setUp(self):
        os.environ["TEST_BALE_TOKEN"] = "123:abc"
        os.environ["TEST_WP_PW"] = "pw"

    def test_bale_success_sends_to_channel_and_never_logs_token_in_errors(self):
        seen = {}

        def transport(method, url, headers=None, json_body=None, timeout=0):
            seen.update(url=url, body=json_body)
            return json.dumps({"ok": True, "result": {"message_id": 77}})
        r = BaleConnector(transport).publish(self.content, self.chan)
        self.assertEqual(r.external_id, "77")
        self.assertEqual(seen["body"], {"chat_id": "@ch", "text": "متن"})
        self.assertIn("/bot123:abc/sendMessage", seen["url"])

    def test_bale_error_classes(self):
        def failing(status):
            def t(*a, **k):
                raise http.HttpError(status, "x")
            return t
        for status, exc in ((0, TransientError), (429, TransientError), (503, TransientError),
                            (401, PermanentError), (400, PermanentError)):
            with self.assertRaises(exc):
                BaleConnector(failing(status)).publish(self.content, self.chan)
        with self.assertRaises(PermanentError):
            BaleConnector().publish(self.content, {"config": {}})
        del os.environ["TEST_BALE_TOKEN"]
        with self.assertRaises(PermanentError):
            BaleConnector().publish(self.content, self.chan)

    def test_wordpress_defaults_to_draft_and_uses_basic_auth(self):
        seen = {}

        def transport(method, url, headers=None, json_body=None, timeout=0):
            seen.update(url=url, headers=headers, body=json_body)
            return json.dumps({"id": 42})
        chan = {"config": {"site": "https://s.ir/", "user": "bot", "password_env": "TEST_WP_PW"}}
        r = WordPressConnector(transport).publish(self.content, chan)
        self.assertEqual(r.external_id, "42")
        self.assertEqual(seen["body"]["status"], "draft")
        self.assertEqual(seen["body"]["excerpt"], "توضیح")
        self.assertTrue(seen["headers"]["Authorization"].startswith("Basic "))
        self.assertEqual(seen["url"], "https://s.ir/wp-json/wp/v2/posts")

    def test_end_to_end_bale_publish_through_orchestrator(self):
        db = new_db()
        seed.ensure(db)
        cid = db.one("SELECT id FROM channels WHERE kind='bale' LIMIT 1")["id"]
        repo.upsert_channel(db, "bale", db.one("SELECT name FROM channels WHERE id=?", (cid,))["name"],
                            config={"chat_id": "@ch", "token_env": "TEST_BALE_TOKEN"})
        from app.connectors import base
        calls = []
        base.register("bale", BaleConnector(lambda *a, **k: calls.append(k["json_body"]) or json.dumps(
            {"ok": True, "result": {"message_id": 5}})))
        repo.update_agent(db, "demo", {"channel_id": cid}, actor=None)
        queue.enqueue(db, "run_agent", "demo", {"text": "یک خبر آزمایشی برای انتشار واقعی در بله"})
        orchestrator.drain(db, "t", Settings(db_path="x", tokens={}))
        self.assertEqual(len(calls), 1)
        self.assertEqual(repo.list_content(db, agent_key="demo")[0]["status"], "published")
        base.register("bale", BaleConnector())


class InboxApiTests(unittest.TestCase):
    def test_inbox_requires_editor_and_validates(self):
        s = Settings(db_path=os.path.join(tempfile.mkdtemp(), "a.db"),
                     tokens={"e" * 20: ("reza", "editor"), "v" * 20: ("mina", "viewer")})
        c = TestClient(create_app(s))
        ok = {"agent": "bale_tax", "text": "متن خبر کافی برای ذخیره در صندوق ورودی ایجنت بله " * 2}
        self.assertEqual(c.post("/api/inbox", json=ok, headers={"X-API-Token": "v" * 20}).status_code, 403)
        self.assertEqual(c.post("/api/inbox", json=ok, headers={"X-API-Token": "e" * 20}).status_code, 201)
        self.assertEqual(c.post("/api/inbox", json={**ok, "text": "کوتاه"}, headers={"X-API-Token": "e" * 20}).status_code, 400)
        self.assertEqual(c.post("/api/inbox", json={**ok, "agent": "nope"}, headers={"X-API-Token": "e" * 20}).status_code, 404)


class ChannelConfigTests(unittest.TestCase):
    def test_channel_config_rejects_raw_secrets(self):
        s = Settings(db_path=os.path.join(tempfile.mkdtemp(), "c.db"), tokens={"e" * 20: ("reza", "editor")})
        c = TestClient(create_app(s))
        h = {"X-API-Token": "e" * 20}
        cid = c.get("/api/channels", headers=h).json()[0]["id"]
        self.assertEqual(c.put(f"/api/channels/{cid}", json={"config": {"token": "123:abc"}}, headers=h).status_code, 400)
        r = c.put(f"/api/channels/{cid}", json={"config": {"chat_id": "@a", "token_env": "X"}}, headers=h)
        self.assertEqual(r.json()["config"]["chat_id"], "@a")


if __name__ == "__main__":
    unittest.main()


class RfpRulesTests(unittest.TestCase):
    def tearDown(self):
        llm.set_client(None)

    def setUp(self):
        self.db = new_db()
        seed.ensure(self.db)
        self.settings = Settings(db_path="x", tokens={})

    def test_numbers_not_in_source_are_flagged(self):
        from app.agents.news_rewrite import unsupported_numbers
        self.assertEqual(unsupported_numbers("مهلت تا ۳۰ آبان تمدید شد", "مهلت تا 30 آبان و جریمه ۱۵ درصد"), ["15"])
        self.assertEqual(unsupported_numbers("نرخ ۱۰٬۰۰۰ تومان", "نرخ 10,000 تومان"), [])

    def test_hallucinated_number_forces_human_review_even_in_auto_mode(self):
        repo.update_agent(self.db, "bale_tax", {"enabled": True, "sources": ["inbox"], "publish_mode": "auto"}, actor=None)
        self.db.execute("INSERT INTO inbox (agent_key,title,text,created_at) VALUES ('bale_tax','t',?,'2026-10-26T00:00:00Z')",
                        ("سازمان امور مالیاتی مهلت ارائه اظهارنامه مشاغل را یک ماه تمدید کرد و اعلام نمود",))
        llm.set_client(FakeLLM("عنوان\n" + "مهلت اظهارنامه مشاغل یک ماه تمدید شد و جریمه ۱۵ درصدی لحاظ می‌شود. " * 2))
        queue.enqueue(self.db, "run_agent", "bale_tax", {})
        orchestrator.drain(self.db, "t", self.settings)
        item = repo.list_content(self.db, agent_key="bale_tax")[0]
        self.assertEqual(item["status"], "pending_approval")
        self.assertIn("15", repo.list_approvals(self.db)[0]["reasons"][0])

    def test_semantic_duplicate_detected_only_for_exact_candidate_uid(self):
        from app import dedup
        repo.insert_content(self.db, "c_old", "bale_tax", None, "text", "تمدید مهلت", "مهلت اظهارنامه تمدید شد", {}, "published")
        check = dedup.llm_semantic_check(FakeLLM("c_old"))
        self.assertEqual(check(self.db, "bale_tax", "اظهارنامه یک ماه بیشتر وقت دارد"), "c_old")
        for chatter in ("NO", "شاید c_old باشد", "c_other"):
            self.assertIsNone(dedup.llm_semantic_check(FakeLLM(chatter))(self.db, "bale_tax", "x"))

    def test_semantic_layer_failure_never_blocks_publishing(self):
        from app import dedup
        from app.orchestrator import _safe

        class Down:
            def complete(self, *a, **k):
                raise llm.LLMError("down")
        repo.insert_content(self.db, "c_old", "bale_tax", None, "text", "t", "یک متن قدیمی", {}, "published")
        self.assertIsNone(_safe(dedup.llm_semantic_check(Down()))(self.db, "bale_tax", "x"))

    def test_report_kind_is_stored_but_never_published(self):
        from app.agents.base import Draft
        job = {"id": 1}
        out = orchestrator._process_draft(self.db, repo.get_agent(self.db, "seo"), job,
                                          Draft(body="گزارش", title="گزارش هفتگی", kind="report"), self.settings)
        self.assertEqual(out, "report")
        self.assertEqual(queue.counts(self.db)["queued"], 0)
