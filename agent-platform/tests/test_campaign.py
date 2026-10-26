import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import campaign
from app.connectors.base import PermanentError, TransientError
from app.connectors.sms import HttpSmsProvider, LogSms
from app.db import DB
from app import http

TZ = ZoneInfo("Asia/Tehran")


def at(y, mo, d, h=12, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ).astimezone(timezone.utc)


def new_db():
    return DB(os.path.join(tempfile.mkdtemp(), "c.db"))


def rows(db, scenario=None):
    sql = "SELECT * FROM sms_messages" + (" WHERE scenario=?" if scenario else "") + " ORDER BY send_at"
    return db.all(sql, (scenario,) if scenario else ())


class Flaky(LogSms):
    def __init__(self, errors):
        super().__init__()
        self.errors = list(errors)

    def send(self, phone, text, ref):
        if self.errors:
            raise self.errors.pop(0)
        return super().send(phone, text, ref)


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.db = new_db()
        campaign.upsert_contact(self.db, "u1", phone="09120000001", name="سارا", consent=True)

    def test_signup_schedules_days_1_3_9_once(self):
        t0 = at(2026, 11, 1, 12)
        campaign.record_event(self.db, "u1", "signup", t0)
        campaign.record_event(self.db, "u1", "signup", t0)  # replay must not duplicate
        got = [(r["scenario"], r["send_at"]) for r in rows(self.db)]
        self.assertEqual([g[0] for g in got], ["signup_1", "signup_3", "signup_9"])
        self.assertEqual(len(got), 3)

    def test_purchase_cancels_signup_messages_and_prevents_new_ones(self):
        campaign.record_event(self.db, "u1", "signup", at(2026, 11, 1))
        campaign.record_event(self.db, "u1", "purchase", at(2026, 11, 2))
        self.assertEqual({r["status"] for r in rows(self.db)}, {"cancelled"})
        campaign.upsert_contact(self.db, "u2", phone="09120000002", consent=True)
        campaign.record_event(self.db, "u2", "purchase", at(2026, 11, 1))
        campaign.record_event(self.db, "u2", "signup", at(2026, 11, 1))
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM sms_messages WHERE contact_id=2")["n"], 0)

    def test_purchase_between_scheduling_and_sending_still_wins(self):
        campaign.record_event(self.db, "u1", "signup", at(2026, 11, 1))
        cid = self.db.one("SELECT id FROM contacts")["id"]
        self.db.execute("INSERT INTO contact_events (contact_id,kind,at) VALUES (?, 'purchase', '2026-11-02T00:00:00Z')", (cid,))
        provider = LogSms()
        campaign.dispatch(self.db, provider, now=at(2026, 11, 2, 12))
        self.assertEqual(provider.sent, [])
        self.assertEqual(rows(self.db, "signup_1")[0]["reason"], "خرید انجام شده است")

    def test_subscription_reminder_and_expiry_schedule(self):
        end = at(2026, 12, 10, 12)
        campaign.record_event(self.db, "u1", "subscription_start", at(2026, 11, 10), end_at=end)
        sched = {r["scenario"]: r["send_at"] for r in rows(self.db)}
        fmt = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(sched["sub_reminder"], fmt(end - timedelta(days=3)))
        self.assertEqual([sched[f"expired_{d}"] for d in (0, 3, 7)], [fmt(end + timedelta(days=d)) for d in (0, 3, 7)])

    def test_renewal_cancels_old_reminders_and_schedules_new_period(self):
        e1, e2 = at(2026, 12, 10), at(2027, 1, 10)
        campaign.record_event(self.db, "u1", "subscription_start", at(2026, 11, 10), end_at=e1)
        campaign.record_event(self.db, "u1", "renewal", at(2026, 12, 9), end_at=e2)
        old = [r for r in rows(self.db) if "20261210" in r["step_key"]]
        new = [r for r in rows(self.db) if "20270110" in r["step_key"]]
        self.assertEqual({r["status"] for r in old}, {"cancelled"})
        self.assertEqual({r["status"] for r in new}, {"queued"})
        self.assertEqual(len(new), 4)

    def test_expired_messages_sent_only_if_not_renewed(self):
        e1 = at(2026, 12, 10, 12)
        campaign.record_event(self.db, "u1", "subscription_start", at(2026, 11, 10), end_at=e1)
        provider = LogSms()
        campaign.dispatch(self.db, provider, now=e1)  # reminder is 3 days old (stale) -> cancelled, expired_0 sent
        self.assertEqual(len(provider.sent), 1)
        self.assertIn("به پایان رسید", provider.sent[0]["text"])
        # renewal arrives before day +3: that message must be cancelled even though the event was never "cancel"-ed
        cid = self.db.one("SELECT id FROM contacts")["id"]
        self.db.execute("INSERT INTO contact_events (contact_id,kind,at,end_at) VALUES (?, 'renewal', ?, '2027-01-10T08:30:00Z')", (cid, "2026-12-12T00:00:00Z"))
        campaign.dispatch(self.db, provider, now=e1 + timedelta(days=3))
        self.assertEqual(len(provider.sent), 1)
        self.assertEqual(rows(self.db, "expired_3")[0]["reason"], "اشتراک تمدید شده است")


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.db = new_db()
        campaign.upsert_contact(self.db, "u1", phone="09120000001", name="سارا", consent=True)
        campaign.record_event(self.db, "u1", "signup", at(2026, 11, 1, 12))

    def test_quiet_hours_defer_to_next_morning_then_send(self):
        provider = LogSms()
        night = at(2026, 11, 2, 23, 30)  # day-1 message is due, but it is night in Tehran
        st = campaign.dispatch(self.db, provider, now=night)
        self.assertEqual((st["sent"], st["deferred"]), (0, 1))
        self.assertEqual(rows(self.db, "signup_1")[0]["send_at"], at(2026, 11, 3, 9).strftime("%Y-%m-%dT%H:%M:%SZ"))
        campaign.dispatch(self.db, provider, now=at(2026, 11, 3, 9, 1))
        self.assertEqual(len(provider.sent), 1)

    def test_stale_message_is_cancelled_not_sent_late(self):
        provider = LogSms()
        campaign.dispatch(self.db, provider, now=at(2026, 11, 20, 12))
        self.assertEqual(provider.sent, [])
        self.assertTrue(all(r["status"] == "cancelled" for r in rows(self.db)))

    def test_no_consent_no_message_and_optout_is_final(self):
        campaign.upsert_contact(self.db, "u1", consent=False)
        provider = LogSms()
        campaign.dispatch(self.db, provider, now=at(2026, 11, 2, 12))
        self.assertEqual(provider.sent, [])
        campaign.upsert_contact(self.db, "u1", consent=True)
        self.assertTrue(campaign.opt_out(self.db, "u1"))
        campaign.upsert_contact(self.db, "u1", consent=True)  # an import must not silently re-subscribe
        self.assertEqual(self.db.one("SELECT consent FROM contacts")["consent"], 0)
        campaign.dispatch(self.db, provider, now=at(2026, 11, 2, 12))
        self.assertEqual(provider.sent, [])

    def test_transient_error_retries_then_gives_up_permanent_fails_at_once(self):
        provider = Flaky([TransientError("t1"), TransientError("t2")])
        now = at(2026, 11, 2, 12)
        campaign.dispatch(self.db, provider, now=now)
        self.assertEqual(rows(self.db, "signup_1")[0]["status"], "queued")
        campaign.dispatch(self.db, provider, now=now + timedelta(minutes=11))
        campaign.dispatch(self.db, provider, now=now + timedelta(minutes=40))
        self.assertEqual(rows(self.db, "signup_1")[0]["status"], "sent")
        self.assertEqual(len(provider.sent), 1)  # exactly one delivery
        p2 = Flaky([PermanentError("bad number")])
        campaign.upsert_contact(self.db, "u3", phone="1", consent=True)
        campaign.record_event(self.db, "u3", "signup", at(2026, 11, 1, 12))
        campaign.dispatch(self.db, p2, now=now)
        self.assertEqual(self.db.one("SELECT status FROM sms_messages WHERE contact_id=2 AND scenario='signup_1'")["status"], "failed")

    def test_crash_during_send_is_never_resent(self):
        msg = rows(self.db, "signup_1")[0]
        self.db.execute("UPDATE sms_messages SET status='sending', sent_at=? WHERE id=?", ("2026-11-02T08:00:00Z", msg["id"]))
        self.assertEqual(campaign.recover_unknown(self.db, now=at(2026, 11, 2, 13)), 1)
        provider = LogSms()
        campaign.dispatch(self.db, provider, now=at(2026, 11, 2, 14))
        self.assertEqual(provider.sent, [])
        self.assertEqual(rows(self.db, "signup_1")[0]["status"], "unknown")

    def test_daily_cap(self):
        for i in range(5):
            campaign.upsert_contact(self.db, f"x{i}", phone=f"0912{i}", consent=True)
            campaign.record_event(self.db, f"x{i}", "signup", at(2026, 11, 1, 12))
        provider = LogSms()
        campaign.dispatch(self.db, provider, cfg={"policy": {"daily_cap": 3}}, now=at(2026, 11, 2, 12))
        self.assertEqual(len(provider.sent), 3)

    def test_erasure_removes_everything(self):
        self.assertTrue(campaign.erase_contact(self.db, "u1"))
        for t in ("contacts", "contact_events", "sms_messages"):
            self.assertEqual(self.db.one(f"SELECT COUNT(*) AS n FROM {t}")["n"], 0)

    def test_phone_masking(self):
        self.assertEqual(campaign.mask("09121234567"), "*******4567")


class ManualCampaignTests(unittest.TestCase):
    def setUp(self):
        self.db = new_db()
        for i, consent in enumerate([True, True, True, False]):
            campaign.upsert_contact(self.db, f"u{i}", phone=f"0912000000{i}", name=f"n{i}", consent=consent)
        campaign.record_event(self.db, "u0", "signup", at(2026, 11, 1))
        campaign.record_event(self.db, "u1", "subscription_start", at(2026, 11, 1), end_at=at(2027, 1, 1))
        campaign.record_event(self.db, "u2", "subscription_start", at(2026, 9, 1), end_at=at(2026, 10, 1))

    def test_segments_respect_consent(self):
        now = at(2026, 11, 5)
        names = lambda seg: sorted(c["external_id"] for c in campaign.segment_contacts(self.db, {"type": seg}, now))
        self.assertEqual(names("all"), ["u0", "u1", "u2"])  # u3 has no consent
        self.assertEqual(names("signup_no_purchase"), ["u0"])
        self.assertEqual(names("active_subscription"), ["u1"])
        self.assertEqual(names("expired"), ["u2"])
        with self.assertRaises(ValueError):
            campaign.segment_contacts(self.db, {"type": "nonsense"})

    def test_manual_campaign_respects_limit_and_sends_fixed_text(self):
        res = campaign.create_manual(self.db, "نمونه", {"type": "all"}, "پیام دستی", at(2026, 11, 5, 12), 2, "sara", now=at(2026, 11, 5))
        self.assertEqual(res["recipients"], 2)
        provider = LogSms()
        campaign.dispatch(self.db, provider, now=at(2026, 11, 5, 12, 1))
        self.assertEqual([m["text"] for m in provider.sent], ["پیام دستی", "پیام دستی"])
        again = campaign.create_manual(self.db, "دوباره", {"type": "all"}, "پیام دستی", at(2026, 11, 6, 12), 5, "sara", now=at(2026, 11, 5))
        self.assertEqual(again["recipients"], 3)  # a new campaign is a new step; the same one never double-sends

    def test_invalid_event_inputs(self):
        with self.assertRaises(ValueError):
            campaign.record_event(self.db, "u0", "bogus")
        with self.assertRaises(ValueError):
            campaign.record_event(self.db, "u0", "renewal")


class ProviderTests(unittest.TestCase):
    def test_http_provider_error_classes(self):
        os.environ["SMS_URL"], os.environ["SMS_TOKEN"] = "https://sms.example/send", "tok"

        def failing(status):
            def t(*a, **k):
                raise http.HttpError(status, "x")
            return t
        for status, exc in ((0, TransientError), (429, TransientError), (500, TransientError), (400, PermanentError)):
            with self.assertRaises(exc):
                HttpSmsProvider(failing(status)).send("0912", "t", "r")
        ok = HttpSmsProvider(lambda *a, **k: '{"id": "p77"}').send("0912", "t", "r")
        self.assertEqual(ok, "p77")
        del os.environ["SMS_URL"]
        with self.assertRaises(PermanentError):
            HttpSmsProvider().send("0912", "t", "r")


if __name__ == "__main__":
    unittest.main()


class CampaignApiTests(unittest.TestCase):
    def setUp(self):
        from starlette.testclient import TestClient
        from app.api import create_app
        from app.config import Settings
        from app.connectors import sms
        self.sms = LogSms()
        sms.set_provider(self.sms)
        s = Settings(db_path=os.path.join(tempfile.mkdtemp(), "a.db"),
                     tokens={"a" * 20: ("sara", "admin"), "e" * 20: ("reza", "editor"), "v" * 20: ("mina", "viewer")})
        self.c = TestClient(create_app(s))
        self.ed, self.ad, self.vw = ({"X-API-Token": t * 20} for t in "eav")

    def tearDown(self):
        from app.connectors import sms
        sms.set_provider(None)

    def test_end_to_end_signup_then_purchase_over_http(self):
        r = self.c.post("/api/campaign/contacts", headers=self.ed,
                        json={"external_id": "u1", "phone": "09121234567", "name": "سارا", "consent": True})
        self.assertEqual(r.json()["phone"], "*******4567")  # never echoed in full
        t0 = (datetime.now(timezone.utc) - timedelta(days=1, hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(self.c.post("/api/campaign/events", headers=self.ed, json={"external_id": "u1", "kind": "signup", "at": t0}).status_code, 201)
        self.assertEqual(self.c.post("/api/campaign/events", headers=self.ed, json={"external_id": "u1", "kind": "purchase"}).status_code, 201)
        self.c.put("/api/agents/campaign_manager", headers=self.ed, json={"enabled": True})
        tick = self.c.post("/api/worker/tick", headers=self.ad).json()
        self.assertEqual(tick["sms"]["sent"], 0)
        self.assertEqual(self.sms.sent, [])
        self.assertEqual(self.c.get("/api/campaign/stats", headers=self.vw).json()["cancelled"], 3)

    def test_validation_roles_and_erasure(self):
        self.assertEqual(self.c.post("/api/campaign/events", headers=self.ed, json={"external_id": "u1", "kind": "x"}).status_code, 400)
        self.assertEqual(self.c.post("/api/campaign/events", headers=self.ed, json={"external_id": "u1", "kind": "signup", "at": "yesterday"}).status_code, 400)
        self.assertEqual(self.c.post("/api/campaign/manual", headers=self.ed, json={}).status_code, 403)
        self.assertEqual(self.c.post("/api/campaign/manual", headers=self.ad, json={"text": "x", "max_recipients": 1, "send_at": "2020-01-01T00:00:00Z"}).status_code, 400)
        self.assertEqual(self.c.post("/api/campaign/contacts", headers=self.ed, json={"external_id": "u1", "consent": True, "phone": "0912"}).status_code, 200)
        self.assertEqual(self.c.delete("/api/campaign/contacts/u1", headers=self.ed).status_code, 403)
        self.assertEqual(self.c.delete("/api/campaign/contacts/u1", headers=self.ad).status_code, 200)
        self.assertEqual(self.c.delete("/api/campaign/contacts/u1", headers=self.ad).status_code, 404)
