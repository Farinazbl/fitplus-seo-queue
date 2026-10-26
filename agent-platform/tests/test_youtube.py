import os, tempfile, unittest
from app import llm, queue, orchestrator, repo, seed
from app.config import from_env
from app.db import DB

OK = "TITLE: ده نکته آب\nCOVER: آب بنوش\nDESCRIPTION: توضیح #آب #سلامت #نکته\nSCRIPT: " + "کلمه " * 200


class Fake:
    def __init__(self, out): self.out = out
    def complete(self, s, u, max_tokens=0): return self.out


class T(unittest.TestCase):
    def setUp(self):
        self.db = DB(os.path.join(tempfile.mkdtemp(), "t.db")); seed.ensure(self.db)
        repo.update_agent(self.db, "youtube_health", {"enabled": True}, actor=None)

    def tearDown(self): llm.set_client(None)

    def run_it(self, out):
        llm.set_client(Fake(out))
        queue.enqueue(self.db, "run_agent", "youtube_health", {"task": "default", "slot": "s"}, dedupe_key="s")
        orchestrator.drain(self.db, "w", from_env())
        return self.db.all("SELECT * FROM content_items WHERE agent_key='youtube_health'")

    def test_package_goes_to_approval(self):
        items = self.run_it(OK)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "pending_approval")
        self.assertEqual(items[0]["kind"], "video_script")
        self.assertIn("متن ویدئو", items[0]["body"])

    def test_bad_format_and_short_script_rejected(self):
        self.assertEqual(self.run_it("متن بی‌قالب"), [])

    def test_all_three_channels_use_real_agent(self):
        for k in ("youtube_kids", "youtube_health", "youtube_finance"):
            self.assertEqual(repo.get_agent(self.db, k)["agent_type"], "youtube_script")


if __name__ == "__main__":
    unittest.main()
