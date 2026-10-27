import json, os, tempfile, unittest
from unittest import mock
from app import approver, orchestrator, queue, repo, seed
from app.config import from_env
from app.db import DB


class T(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"BALE_TOKEN": "1:x", "APPROVER_IDS": "42"}); self.env.start()
        self.db = DB(os.path.join(d, "t.db")); seed.ensure(self.db)

    def tearDown(self): self.env.stop()

    def _pending(self):
        from app import orchestrator as o
        cfg = repo.get_agent(self.db, "fitplus_daily")
        from app.agents.base import Draft
        return o._process_draft(self.db, {**cfg, "publish_mode": "manual_approval"}, {"id": None, "payload": {}},
                                Draft(body="متن پست آزمایشی برای تأیید و انتشار در کانال", title="عنوان"), from_env())

    def test_notify_once_and_approve_only_by_allowed(self):
        self._pending()
        calls = []
        def tr(m, url, headers=None, json_body=None, timeout=0):
            calls.append((url.rsplit("/", 1)[1], json_body)); return json.dumps({"ok": True, "result": []})
        self.assertEqual(approver.notify_pending(self.db, tr), 1)
        self.assertEqual(approver.notify_pending(self.db, tr), 0)           # not sent twice
        sent = [c for c in calls if c[0] == "sendMessage"][0][1]
        aid = int(sent["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[2])
        def upd(uid):
            def t2(m, url, headers=None, json_body=None, timeout=0):
                if url.endswith("getUpdates"):
                    return json.dumps({"ok": True, "result": [{"update_id": 5, "callback_query": {"id": "c", "from": {"id": uid},
                           "data": f"fp:a:{aid}", "message": {"chat": {"id": 42}}}}]})
                return json.dumps({"ok": True, "result": {}})
            return t2
        self.assertEqual(approver.poll(self.db, upd(99)), 0)                # stranger refused
        self.assertEqual(repo.get_approval(self.db, aid)["status"], "pending")
        self.db.execute("DELETE FROM approver_state")
        self.assertEqual(approver.poll(self.db, upd(42)), 1)
        self.assertEqual(repo.get_approval(self.db, aid)["status"], "approved")


if __name__ == "__main__":
    unittest.main()
