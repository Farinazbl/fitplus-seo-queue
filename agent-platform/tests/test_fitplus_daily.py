import io, os, tempfile, unittest
from unittest import mock
from app import imaging, llm, queue, orchestrator, repo, seed
from app.config import from_env
from app.connectors.bale import BaleConnector
from app.db import DB

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
OUT = ("TOPIC: سالاد مرغ\nCARD: یک ناهار سبک و پرپروتئین\nIMAGE: grilled chicken salad on a wooden table\n"
       "CAPTION: 🥗 سالاد مرغ یک ناهار سبک است. مرغ گریل‌شده را با سبزی تازه و کمی روغن زیتون مخلوط کنید و نوش جان کنید. "
       "برای شرایط خاص با پزشک یا متخصص مشورت کنید.")


def fake_photo():
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (640, 480), (90, 140, 70)).save(b, "PNG"); return b.getvalue()


POUT = ("TOPIC: کدوحلوایی\nTITLE: کدوحلوایی\nSUBTITLE: دوست رژیم شما در پاییز\nFACT1: سلامت چشم | غنی از بتاکاروتن\n"
        "FACT2: سیری | فیبر بالا\nFACT3: گوارش | کمک به روده\nFACT4: کم‌کالری | مناسب رژیم\nTIP: با کمی دارچین میان‌وعده‌ی پاییزی بساز\n"
        "IMAGE: roasted pumpkin cubes in a stoneware bowl on a rustic table, soft window light\n"
        "CAPTION: 🎃 کدوحلوایی پخته یک میان‌وعده‌ی سبک و خوشمزه است. با کمی دارچین و مغز تخمه سرو کنید و لذت ببرید. "
        "برای شرایط خاص با پزشک یا متخصص مشورت کنید.")


class Fake:
    def complete(self, s, u, max_tokens=0): return POUT if "TITLE:" in s else OUT


@unittest.skipUnless(os.path.exists(FONT), "font missing")
class T(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"APP_DB": os.path.join(d, "t.db"), "APP_FONT": FONT})
        self.env.start()
        self.db = DB(os.environ["APP_DB"]); seed.ensure(self.db)
        repo.update_agent(self.db, "fitplus_daily", {"enabled": True}, actor=None)
        llm.set_client(Fake())

    def tearDown(self): llm.set_client(None); self.env.stop()

    def test_card_is_png_with_logo_and_text(self):
        png = imaging.render_card(fake_photo(), "یک ناهار سبک و پرپروتئین برای شما", footer="@fitplusapp")
        from PIL import Image
        im = Image.open(io.BytesIO(png)); self.assertEqual(im.size, (1080, 1080))

    def test_agent_creates_pending_image_post(self):
        seen = {}
        def poster(prompt): seen["p"] = prompt; return fake_photo()
        with mock.patch.object(imaging, "generate_poster", poster):
            queue.enqueue(self.db, "run_agent", "fitplus_daily", {"task": "default", "slot": "s"}, dedupe_key="s")
            orchestrator.drain(self.db, "w", from_env())
        items = self.db.all("SELECT * FROM content_items WHERE agent_key='fitplus_daily'")
        self.assertEqual(len(items), 1)
        c = repo.get_content(self.db, items[0]["uid"])
        self.assertEqual(c["status"], "pending_approval")
        self.assertTrue(os.path.isfile(c["meta"]["image_path"]))
        self.assertTrue(c["body"].rstrip().endswith("@Fitplusapp"))
        self.assertIn("کدوحلوایی", seen["p"]); self.assertIn("fitplusapp", seen["p"])
        self.assertEqual(c["meta"]["style"], "poster")

    def test_channels_are_pinned_and_never_shared(self):
        ids = {}
        for key in ("fitplus_daily", "drrezhim_daily"):
            a = repo.get_agent(self.db, key)
            ids[key] = repo.get_channel(self.db, a["channel_id"])["config"]["chat_id"]
        self.assertEqual(ids, {"fitplus_daily": "@fitplusapp", "drrezhim_daily": "@drrezhim"})
        self.assertEqual(len(set(ids.values())), 2)

    def test_drrezhim_poster_uses_its_own_brand(self):
        repo.update_agent(self.db, "drrezhim_daily", {"enabled": True}, actor=None)
        seen = {}
        def poster(prompt): seen["p"] = prompt; return fake_photo()
        with mock.patch.object(imaging, "generate_poster", poster):
            queue.enqueue(self.db, "run_agent", "drrezhim_daily", {"task": "default", "slot": "s"}, dedupe_key="s")
            orchestrator.drain(self.db, "w", from_env())
        self.assertIn("@drrezhim", seen["p"]); self.assertNotIn("@Fitplusapp", seen["p"])
        c = repo.get_content(self.db, self.db.all("SELECT uid FROM content_items WHERE agent_key='drrezhim_daily'")[0]["uid"])
        self.assertFalse(c["body"].rstrip().endswith("@Fitplusapp"))
        self.assertTrue(c["body"].rstrip().endswith("@Drrezhim"))
        self.assertIn("همیشه همراهته", c["body"])

    def test_missing_font_fails_clearly(self):
        with mock.patch.dict(os.environ, {"APP_FONT": "/nope.ttf"}), self.assertRaises(imaging.ImagingError):
            imaging.render_card(fake_photo(), "x")

    def test_bale_sends_photo_with_caption(self):
        p = os.path.join(tempfile.mkdtemp(), "a.png"); open(p, "wb").write(fake_photo())
        seen = {}
        def mp(url, fields, files): seen.update(url=url, f=fields, files=files); return '{"ok": true, "result": {"message_id": 7}}'
        with mock.patch.dict(os.environ, {"BALE_TOKEN": "1:x"}):
            r = BaleConnector(multipart=mp).publish({"body": "کپشن", "meta": {"image_path": p}}, {"config": {"chat_id": "@c"}})
        self.assertTrue(seen["url"].endswith("/sendPhoto"))
        self.assertEqual(seen["f"]["caption"], "کپشن"); self.assertEqual(r.external_id, "7")

    def test_generate_photo_parses_b64(self):
        import base64, json
        tr = lambda *a, **k: json.dumps({"data": [{"b64_json": base64.b64encode(b"IMG").decode()}]})
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k"}):
            self.assertEqual(imaging.generate_photo("p", transport=tr), b"IMG")


if __name__ == "__main__":
    unittest.main()
