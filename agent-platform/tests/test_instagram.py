import os, tempfile, unittest
from app import llm, orchestrator, repo, seed
from app.agents import instagram_nutrition as ig
from app.config import from_env
from app.db import DB


class Fake:
    def __init__(self, out): self.out = out; self.calls = []
    def complete(self, system, user, max_tokens=0): self.calls.append(system); return self.out


REEL = "TOPIC: آب و عملکرد\nSCENES:\n1. لیوان آب | بنوشید\nCAPTION: نکته #تغذیه\nمشورت با متخصص"


class T(unittest.TestCase):
    def setUp(self):
        self.db = DB(os.path.join(tempfile.mkdtemp(), "t.db")); seed.ensure(self.db)
        self.s = from_env()

    def tearDown(self): llm.set_client(None)

    def run_task(self, task):
        repo.update_agent(self.db, 'instagram_nutrition', {'enabled': True}, actor=None)
        from app import queue
        queue.enqueue(self.db, "run_agent", "instagram_nutrition", {"task": task, "slot": task + "1"}, dedupe_key=task + "1")
        return orchestrator.drain(self.db, "w", self.s)

    def test_reel_goes_to_approval_with_axis(self):
        fake = Fake(REEL); llm.set_client(fake)
        self.run_task("reel")
        items = repo.list_content(self.db, agent_key="instagram_nutrition") if hasattr(repo, "list_content") else \
            self.db.all("SELECT * FROM content_items WHERE agent_key='instagram_nutrition'")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "pending_approval")
        self.assertEqual(items[0]["kind"], "reel")
        self.assertIn(ig.week_axis(), fake.calls[0])

    def test_bad_format_does_not_create_content(self):
        llm.set_client(Fake("متن بدون قالب")); self.run_task("carousel")
        self.assertEqual(self.db.all("SELECT * FROM content_items WHERE agent_key='instagram_nutrition'"), [])

    def test_axes_rotate_over_four_weeks(self):
        from datetime import datetime, timedelta, timezone
        d = datetime(2026, 11, 2, tzinfo=timezone.utc)
        self.assertEqual(len({ig.week_axis(d + timedelta(weeks=i)) for i in range(4)}), 4)

    def test_invalid_task_rejected(self):
        with self.assertRaises(ValueError):
            ig.InstagramNutritionAgent(self.db).generate({"key": "instagram_nutrition", "prompt": ""}, "x", {})


if __name__ == "__main__":
    unittest.main()
