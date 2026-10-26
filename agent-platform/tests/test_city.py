import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from starlette.testclient import TestClient

from app import city, llm, orchestrator, queue, repo, seed
from app.api import create_app
from app.config import Settings
from app.db import DB

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)  # a Saturday


def new_db():
    return DB(os.path.join(tempfile.mkdtemp(), "city.db"))


def make_citizen(db, ig, comment="کامنت معتبر و کامل", follow=True, verified=True, consent=True, job="معلم",
                 when=NOW - timedelta(days=1)):
    city.register(db, ig, job, consent)
    city.add_comment(db, ig, f"c-{ig}", "post1", comment, when)
    city.set_follow(db, ig, True)
    if verified:
        city.verify(db, ig, "admin")
    city.set_follow(db, ig, follow)  # may unfollow after being verified


class RulesTests(unittest.TestCase):
    def setUp(self):
        self.db = new_db()

    def test_id_normalisation_and_validation(self):
        self.assertEqual(city.norm_id("  @Sara.Fit_1 "), "sara.fit_1")
        for bad in ("", "a b", "x" * 31, "سارا", "a@b"):
            with self.assertRaises(city.CityError):
                city.norm_id(bad)

    def test_membership_needs_follow_and_a_valid_comment(self):
        city.register(self.db, "@sara")
        with self.assertRaises(city.CityError):
            city.verify(self.db, "sara", "admin")           # nothing yet
        city.set_follow(self.db, "sara", True)
        with self.assertRaises(city.CityError):
            city.verify(self.db, "sara", "admin")           # follow only
        city.add_comment(self.db, "sara", "c1", "p", "😍😍", NOW)  # emoji only -> invalid
        with self.assertRaises(city.CityError):
            city.verify(self.db, "sara", "admin")
        city.add_comment(self.db, "sara", "c2", "p", "عالی بود، ممنون از آموزش", NOW)
        self.assertEqual(city.verify(self.db, "sara", "admin")["status"], "verified")
        self.assertEqual(city.stats(self.db)["population"], 1)

    def test_pending_is_not_population_and_raw_followers_are_separate(self):
        city.register(self.db, "a")
        city.register(self.db, "b")
        city.set_raw_followers(self.db, 52000)
        st = city.stats(self.db)
        self.assertEqual((st["population"], st["pending"], st["raw_followers"]), (0, 2, 52000))

    def test_unfollow_or_invalidated_comment_removes_from_population(self):
        make_citizen(self.db, "sara")
        make_citizen(self.db, "ali")
        self.assertEqual(city.stats(self.db)["population"], 2)
        city.set_follow(self.db, "sara", False)
        city.invalidate_comment(self.db, "c-ali")
        self.assertEqual(city.stats(self.db)["population"], 0)
        city.set_follow(self.db, "sara", True)   # follows again -> counted again
        self.assertEqual(city.stats(self.db)["population"], 1)

    def test_comment_spam_rules(self):
        city.register(self.db, "sara")
        self.assertTrue(city.add_comment(self.db, "sara", "1", "p", "متن اول خوب", NOW))
        self.assertFalse(city.add_comment(self.db, "sara", "2", "p", "متن اول خوب", NOW))          # repeat
        self.assertFalse(city.add_comment(self.db, "sara", "3", "p", "بیا https://spam.com", NOW))  # link
        self.assertIsNone(city.add_comment(self.db, "stranger", "4", "p", "سلام دوستان", NOW))      # not registered: not stored
        self.assertIsNone(self.db.one("SELECT * FROM city_comments WHERE comment_id='4'"))
        for i in range(12):
            city.add_comment(self.db, "sara", f"f{i}", "p", f"کامنت شماره {i} با متن متفاوت", NOW)
        self.assertGreaterEqual(self.db.one("SELECT COUNT(*) AS n FROM city_comments WHERE valid=0")["n"], 3)  # flood is cut off
        self.assertTrue(city.add_comment(self.db, "sara", "1", "p", "تکرار همان شناسه", NOW))  # idempotent replay

    def test_cap_sends_new_members_to_waitlist(self):
        orig = city.CAP
        city.CAP = 2
        try:
            for ig in ("a", "b", "c"):
                make_citizen(self.db, ig)
            self.assertEqual(self.db.one("SELECT status FROM citizens WHERE ig_id='c'")["status"], "waitlist")
            self.assertEqual(city.stats(self.db)["population"], 2)
        finally:
            city.CAP = orig

    def test_rename_keeps_comments_and_blocks_collisions(self):
        make_citizen(self.db, "old_id")
        make_citizen(self.db, "other")
        with self.assertRaises(city.CityError):
            city.rename(self.db, "old_id", "other")
        city.rename(self.db, "@Old_ID", "new_id")
        self.assertEqual(city.stats(self.db)["population"], 2)
        self.assertEqual(self.db.one("SELECT ig_id FROM city_comments WHERE comment_id='c-old_id'")["ig_id"], "new_id")

    def test_job_must_be_respectful_and_short(self):
        for bad in ("x" * 41, "ببین https://a.com", "@spam", "کثافت"):
            with self.assertRaises(city.CityError):
                city.register(self.db, "u", bad)
        self.assertEqual(city.register(self.db, "u", "  مربی   ورزش ")["job"], "مربی ورزش")


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.db = new_db()
        for i in range(25):
            make_citizen(self.db, f"user{i:02d}")
        make_citizen(self.db, "private", consent=False)           # eligible but did not consent to display
        make_citizen(self.db, "old", when=NOW - timedelta(days=30))  # comment outside the period
        make_citizen(self.db, "left", follow=False)               # unfollowed
        city.register(self.db, "pending_user", "x", True)         # never verified

    def pick(self, key="2026-W45", now=NOW):
        return city.select_weekly(self.db, key, now - timedelta(days=7), now)

    def test_ten_unique_consenting_eligible_citizens(self):
        got = [p["ig_id"] for p in self.pick()]
        self.assertEqual(len(got), 10)
        self.assertEqual(len(set(got)), 10)
        for excluded in ("private", "old", "left", "pending_user"):
            self.assertNotIn(excluded, got)

    def test_repeat_call_returns_same_result_and_is_reproducible(self):
        first = self.pick()
        self.assertEqual(self.pick(), first)
        run = self.db.one("SELECT * FROM city_runs WHERE week_key='2026-W45'")
        self.assertEqual(run["candidates"], 25)
        self.assertEqual(len(run["seed"]), 64)

    def comment_all(self, when, tag):
        for i in range(25):
            city.add_comment(self.db, f"user{i:02d}", f"{tag}-{i}", "p", f"کامنت هفته {tag} شماره {i}", when)

    def test_no_unnecessary_repeats_across_weeks(self):
        w1 = {p["ig_id"] for p in self.pick("2026-W45")}
        n2 = NOW + timedelta(days=7)
        self.comment_all(n2 - timedelta(days=1), "w2")
        w2 = {p["ig_id"] for p in self.pick("2026-W46", n2)}
        self.assertEqual((len(w1), len(w2)), (10, 10))
        self.assertEqual(w1 & w2, set())  # 25 candidates are enough for two disjoint weeks
        n3 = NOW + timedelta(days=14)
        self.comment_all(n3 - timedelta(days=1), "w3")
        w3 = {p["ig_id"] for p in self.pick("2026-W47", n3)}
        self.assertEqual(len(w3), 10)
        self.assertEqual(len(w3 & (w1 | w2)), 5)  # only 5 fresh remain, then the oldest picks are reused

    def test_erasure_anonymises_history_and_removes_data(self):
        picked = self.pick()[0]["ig_id"]
        self.assertTrue(city.erase(self.db, picked))
        self.assertIsNone(city.get(self.db, picked))
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM city_comments WHERE ig_id=?", (picked,))["n"], 0)
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM city_selections WHERE ig_id=?", (picked,))["n"], 0)
        self.assertFalse(city.erase(self.db, picked))

    def test_withdrawn_consent_excludes_from_future_selection(self):
        city.set_consent(self.db, "user00", False)
        self.assertNotIn("user00", [p["ig_id"] for p in self.pick()])


class FakeLLM:
    def __init__(self, reply="عنوان\nمتن کپشن"):
        self.reply, self.calls = reply, []

    def complete(self, system, user, max_tokens=1200):
        self.calls.append(user)
        return self.reply


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.db = new_db()
        seed.ensure(self.db)
        self.settings = Settings(db_path="x", tokens={})
        for i in range(12):
            make_citizen(self.db, f"user{i:02d}", when=utcnow_minus(1))
        city.register(self.db, "pend")
        repo.update_agent(self.db, "virtual_city", {"enabled": True, "publish_mode": "auto"}, actor=None)

    def tearDown(self):
        llm.set_client(None)

    def run_task(self, task):
        queue.enqueue(self.db, "run_agent", "virtual_city", {"task": task})
        return orchestrator.drain(self.db, "t", self.settings)

    def test_population_reel_uses_verified_count_only(self):
        fake = FakeLLM()
        llm.set_client(fake)
        self.run_task("reel")
        self.assertIn("12 نفر", fake.calls[0])
        self.assertNotIn("13", fake.calls[0])  # the pending user is not counted
        item = repo.list_content(self.db, agent_key="virtual_city")[0]
        self.assertEqual(item["meta"]["population"], 12)

    def test_weekly_selection_always_needs_human_approval_even_in_auto_mode(self):
        fake = FakeLLM("user00: معلم مهربان\nuser01: ورزشکار")
        llm.set_client(fake)
        self.run_task("weekly_selection")
        item = repo.list_content(self.db, agent_key="virtual_city")[0]
        self.assertEqual(item["status"], "pending_approval")
        self.assertEqual(len(item["meta"]["selected"]), 10)
        self.assertIn("آیدی", repo.list_approvals(self.db)[0]["reasons"][0])

    def test_unknown_task_dead_letters(self):
        llm.set_client(FakeLLM())
        queue.enqueue(self.db, "run_agent", "virtual_city", {"task": "nope"}, max_attempts=1)
        self.assertEqual(orchestrator.drain(self.db, "t", self.settings)[0]["status"], "dead")


def utcnow_minus(days):
    from app.util import utcnow
    return utcnow() - timedelta(days=days)


class CityApiTests(unittest.TestCase):
    def setUp(self):
        s = Settings(db_path=os.path.join(tempfile.mkdtemp(), "a.db"),
                     tokens={"a" * 20: ("sara", "admin"), "e" * 20: ("reza", "editor"), "v" * 20: ("mina", "viewer")})
        self.c = TestClient(create_app(s))
        self.ed, self.ad, self.vw = ({"X-API-Token": t * 20} for t in "eav")

    def test_full_membership_flow_over_http(self):
        post = lambda path, **j: self.c.post("/api/city/" + path, headers=self.ed, json=j)
        self.assertEqual(post("register", ig_id="@Sara", job="معلم", consent_display=True).status_code, 201)
        self.assertEqual(post("verify", ig_id="sara").status_code, 400)
        self.assertEqual(post("follow", ig_id="sara", follows=True).status_code, 200)
        self.assertEqual(post("comments", ig_id="sara", comment_id="c1", post_id="p", text="کامنت معتبر من").json(), {"stored": True, "valid": True})
        self.assertEqual(post("comments", ig_id="nobody", comment_id="c2", post_id="p", text="سلام دوستان").json()["stored"], False)
        self.assertEqual(post("verify", ig_id="sara").json()["status"], "verified")
        self.assertEqual(self.c.get("/api/city/stats", headers=self.vw).json()["population"], 1)
        self.assertEqual(self.c.delete("/api/city/citizens/sara", headers=self.ed).status_code, 403)
        self.assertEqual(self.c.delete("/api/city/citizens/sara", headers=self.ad).status_code, 200)
        self.assertEqual(self.c.get("/api/city/stats", headers=self.vw).json()["population"], 0)

    def test_validation_errors(self):
        post = lambda path, **j: self.c.post("/api/city/" + path, headers=self.ed, json=j).status_code
        self.assertEqual(post("register", ig_id="بد آیدی"), 400)
        self.assertEqual(post("follow", ig_id="ghost", follows=True), 404)
        self.assertEqual(post("follow", ig_id="ghost", follows="yes"), 400)
        self.assertEqual(post("comments", ig_id="x", comment_id="", post_id="p", text="t"), 400)
        self.assertEqual(post("raw-followers", raw_followers=-1), 400)
        self.assertEqual(self.c.post("/api/city/register", headers=self.vw, json={"ig_id": "a"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
