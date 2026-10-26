"""HTTP API + dashboard (Starlette).

Auth: send the token in the ``X-API-Token`` header. Roles: viewer < editor < admin.
Tokens live only on the server (APP_TOKENS); the dashboard keeps the one the
operator types in sessionStorage and sends it with each call.
"""
import hmac
import os
from typing import Optional

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import campaign, city, orchestrator, queue, repo, scheduler, seed
from .connectors import sms
from .util import iso, parse, utcnow
from .config import Settings
from .db import DB

RANK = {"viewer": 1, "editor": 2, "admin": 3}
SCHEDULE_TYPES = {"daily", "weekly", "every_n_days", "interval", "event", "external"}
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def create_app(settings: Settings) -> Starlette:
    db = DB(settings.db_path)
    if settings.seed:
        seed.ensure(db)

    # ---------- helpers ----------
    def auth(request: Request, min_role: str):
        supplied = request.headers.get("x-api-token", "")
        found = None
        for token, (name, role) in settings.tokens.items():
            if hmac.compare_digest(token, supplied):
                found = (name, role)
        if found is None:
            raise HTTPException(401, "توکن نامعتبر یا ارسال‌نشده است")
        if RANK[found[1]] < RANK[min_role]:
            raise HTTPException(403, "دسترسی کافی برای این کار ندارید")
        return found

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "بدنه درخواست باید JSON معتبر باشد")
        if not isinstance(data, dict):
            raise HTTPException(400, "بدنه درخواست باید یک شیء JSON باشد")
        return data

    def int_param(request: Request, name: str, default: int, hi: int = 500) -> int:
        try:
            return max(1, min(int(request.query_params.get(name, default)), hi))
        except ValueError:
            raise HTTPException(400, f"پارامتر {name} باید عدد باشد")

    def validate_agent_fields(fields: dict) -> dict:
        allowed = repo.EDITABLE_AGENT_FIELDS
        bad = set(fields) - allowed
        if bad:
            raise HTTPException(400, f"فیلد قابل ویرایش نیست: {', '.join(sorted(bad))}")
        if "schedule" in fields:
            sched = fields["schedule"]
            if not isinstance(sched, list) or not all(isinstance(i, dict) and i.get("type") in SCHEDULE_TYPES
                                                      for i in sched):
                raise HTTPException(400, "schedule باید فهرستی از آیتم‌های معتبر باشد")
        for name in ("sources",):
            if name in fields and not isinstance(fields[name], list):
                raise HTTPException(400, f"{name} باید فهرست باشد")
        if "policy" in fields and not isinstance(fields["policy"], dict):
            raise HTTPException(400, "policy باید شیء باشد")
        if "enabled" in fields and not isinstance(fields["enabled"], bool):
            raise HTTPException(400, "enabled باید true/false باشد")
        return fields

    def agent_or_404(key: str) -> dict:
        agent = repo.get_agent(db, key)
        if agent is None:
            raise HTTPException(404, "ایجنت پیدا نشد")
        return agent

    # ---------- routes ----------
    async def health(request):
        return JSONResponse({"ok": True})

    async def me(request):
        name, role = auth(request, "viewer")
        return JSONResponse({"name": name, "role": role})

    async def overview(request):
        auth(request, "viewer")
        content = {r["status"]: r["n"] for r in db.all(
            "SELECT status, COUNT(*) AS n FROM content_items GROUP BY status")}
        return JSONResponse({
            "jobs": queue.counts(db),
            "content": content,
            "pending_approvals": db.one("SELECT COUNT(*) AS n FROM approvals WHERE status='pending'")["n"],
            "alerts": queue.alerts(db, stale_minutes=settings.stale_job_minutes),
            "agents": [{"key": a["key"], "title": a["title"], "enabled": a["enabled"],
                        "publish_mode": a["publish_mode"], "agent_type": a["agent_type"]}
                       for a in repo.list_agents(db)],
        })

    async def agents_list(request):
        auth(request, "viewer")
        return JSONResponse(repo.list_agents(db))

    async def agent_update(request):
        name, _ = auth(request, "editor")
        key = request.path_params["key"]
        agent_or_404(key)
        fields = validate_agent_fields(await body(request))
        try:
            updated = repo.update_agent(db, key, fields, actor=name)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return JSONResponse(updated)

    async def agent_run(request):
        name, _ = auth(request, "editor")
        key = request.path_params["key"]
        agent = agent_or_404(key)
        if not agent["enabled"]:
            raise HTTPException(409, "ایجنت غیرفعال است؛ ابتدا آن را فعال کنید")
        payload = await body(request) if await request.body() else {}
        job_id = queue.enqueue(db, "run_agent", key, {"task": payload.get("task", "manual"), **payload})
        repo.audit(db, name, "agent.run", "agent", key, {"job_id": job_id})
        return JSONResponse({"job_id": job_id}, status_code=202)

    async def channels_list(request):
        auth(request, "viewer")
        return JSONResponse(repo.list_channels(db))

    async def channel_update(request):
        name, _ = auth(request, "editor")
        cid = int(request.path_params["cid"])
        ch = repo.get_channel(db, cid)
        if ch is None:
            raise HTTPException(404, "کانال پیدا نشد")
        data = await body(request)
        sig = data.get("signature", ch["signature"])
        if not isinstance(sig, str):
            raise HTTPException(400, "signature باید متن باشد")
        cfg = data.get("config", ch["config"])
        if not isinstance(cfg, dict) or any(k in cfg for k in ("token", "password")):
            raise HTTPException(400, "config باید شیء باشد و نباید خود توکن/رمز را شامل شود؛ فقط نام متغیر محیطی (token_env/password_env)")
        repo.upsert_channel(db, ch["kind"], ch["name"], ch["project"], sig, cfg)
        repo.audit(db, name, "channel.update", "channel", cid, {"signature": sig, "config": cfg})
        return JSONResponse(repo.get_channel(db, cid))

    async def jobs_list(request):
        auth(request, "viewer")
        status = request.query_params.get("status")
        limit = int_param(request, "limit", 100)
        rows = db.all("SELECT * FROM jobs " + ("WHERE status=? " if status else "") +
                      "ORDER BY id DESC LIMIT ?", ((status, limit) if status else (limit,)))
        return JSONResponse(rows)

    async def job_retry(request):
        name, _ = auth(request, "editor")
        jid = int(request.path_params["jid"])
        job = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
        if job is None:
            raise HTTPException(404, "وظیفه پیدا نشد")
        if job["status"] != "dead":
            raise HTTPException(409, "فقط وظیفه‌های متوقف‌شده را می‌توان دوباره اجرا کرد")
        db.execute("UPDATE jobs SET status='queued', attempts=0, last_error=NULL, finished_at=NULL WHERE id=?", (jid,))
        repo.audit(db, name, "job.retry", "job", jid)
        return JSONResponse({"ok": True})

    async def content_list(request):
        auth(request, "viewer")
        return JSONResponse(repo.list_content(
            db, status=request.query_params.get("status"),
            agent_key=request.query_params.get("agent"), limit=int_param(request, "limit", 100)))

    async def content_trace(request):
        auth(request, "viewer")
        t = repo.trace(db, request.path_params["uid"])
        if t is None:
            raise HTTPException(404, "محتوا پیدا نشد")
        return JSONResponse(t)

    async def approvals_list(request):
        auth(request, "viewer")
        return JSONResponse(repo.list_approvals(db, request.query_params.get("status", "pending")))

    async def approval_decide(request):
        name, _ = auth(request, "editor")
        data = await body(request)
        try:
            result = orchestrator.decide_approval(
                db, int(request.path_params["aid"]), data.get("decision", ""), name,
                data.get("note"), data.get("edited_body"))
        except KeyError:
            raise HTTPException(404, "مورد تأیید پیدا نشد")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return JSONResponse(result)

    async def inbox_add(request):
        name, _ = auth(request, "editor")
        data = await body(request)
        agent = agent_or_404(data.get("agent", ""))
        text = data.get("text")
        if not isinstance(text, str) or len(text.strip()) < 40:
            raise HTTPException(400, "text باید دست‌کم ۴۰ نویسه باشد")
        from .util import iso, utcnow
        cur = db.execute("INSERT INTO inbox (agent_key,title,text,created_at) VALUES (?,?,?,?)",
                         (agent["key"], str(data.get("title", ""))[:200], text.strip(), iso(utcnow())))
        repo.audit(db, name, "inbox.add", "agent", agent["key"], {"inbox_id": cur.lastrowid})
        return JSONResponse({"id": cur.lastrowid}, status_code=201)

    # ----- campaign manager -----
    def parse_time(value, field):
        if value is None:
            return None
        try:
            return parse(value)
        except (ValueError, TypeError):
            raise HTTPException(400, f"{field} باید به شکل 2026-11-01T09:00:00Z باشد")

    async def campaign_contact(request):
        name, _ = auth(request, "editor")
        d = await body(request)
        if not isinstance(d.get("external_id"), str) or not d["external_id"].strip():
            raise HTTPException(400, "external_id الزامی است")
        c = campaign.upsert_contact(db, d["external_id"].strip(), d.get("phone"), d.get("name", ""),
                                    d.get("consent") if isinstance(d.get("consent"), bool) else None)
        repo.audit(db, name, "contact.upsert", "contact", c["external_id"], {"consent": bool(c["consent"])})
        return JSONResponse({"external_id": c["external_id"], "consent": bool(c["consent"]),
                             "opted_out": bool(c["opted_out"]), "phone": campaign.mask(c["phone"])})

    async def campaign_event(request):
        name, _ = auth(request, "editor")
        d = await body(request)
        try:
            c = campaign.record_event(db, str(d.get("external_id", "")).strip() or "?", d.get("kind", ""),
                                      parse_time(d.get("at"), "at"), parse_time(d.get("end_at"), "end_at"))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return JSONResponse({"ok": True, "external_id": c["external_id"]}, status_code=201)

    async def campaign_optout(request):
        name, _ = auth(request, "editor")
        d = await body(request)
        if not campaign.opt_out(db, str(d.get("external_id", ""))):
            raise HTTPException(404, "مخاطب پیدا نشد")
        repo.audit(db, name, "contact.optout", "contact", d["external_id"])
        return JSONResponse({"ok": True})

    async def campaign_erase(request):
        name, _ = auth(request, "admin")
        ext = request.path_params["ext"]
        if not campaign.erase_contact(db, ext):
            raise HTTPException(404, "مخاطب پیدا نشد")
        repo.audit(db, name, "contact.erase", "contact", ext)
        return JSONResponse({"ok": True})

    async def campaign_manual(request):
        name, _ = auth(request, "admin")
        d = await body(request)
        send_at = parse_time(d.get("send_at"), "send_at")
        if send_at is None or send_at < utcnow():
            raise HTTPException(400, "send_at باید زمانی در آینده باشد")
        try:
            res = campaign.create_manual(db, str(d.get("name", "کمپین دستی")), d.get("segment") or {"type": "all"},
                                         str(d.get("text", "")), send_at, int(d.get("max_recipients", 0)), name)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e))
        return JSONResponse(res, status_code=201)

    async def campaign_messages(request):
        auth(request, "viewer")
        status = request.query_params.get("status")
        rows = db.all("SELECT m.uid, m.scenario, m.status, m.reason, m.send_at, m.sent_at, c.external_id, c.phone "
                      "FROM sms_messages m JOIN contacts c ON c.id=m.contact_id "
                      + ("WHERE m.status=? " if status else "") + "ORDER BY m.id DESC LIMIT ?",
                      ((status, int_param(request, "limit", 100)) if status else (int_param(request, "limit", 100),)))
        for r in rows:
            r["phone"] = campaign.mask(r["phone"])
        return JSONResponse(rows)

    async def campaign_stats(request):
        auth(request, "viewer")
        return JSONResponse(campaign.stats(db))

    # ----- virtual city -----
    def city_call(fn, *args):
        try:
            return fn(*args)
        except city.CityError as e:
            raise HTTPException(400, str(e))
        except KeyError:
            raise HTTPException(404, "شهروند پیدا نشد")

    def citizen_view(c):
        return {k: c[k] for k in ("ig_id", "status", "follow_ok", "consent_display", "job", "registered_at", "verified_at")}

    async def city_register(request):
        name, _ = auth(request, "editor")
        d = await body(request)
        c = city_call(city.register, db, d.get("ig_id", ""), d.get("job"), d.get("consent_display") is True)
        repo.audit(db, name, "city.register", "citizen", c["ig_id"])
        return JSONResponse(citizen_view(c), status_code=201)

    async def city_comment(request):
        auth(request, "editor")
        d = await body(request)
        for f in ("comment_id", "post_id", "text"):
            if not isinstance(d.get(f), str) or not d[f].strip():
                raise HTTPException(400, f"{f} الزامی است")
        valid = city_call(city.add_comment, db, d.get("ig_id", ""), d["comment_id"], d["post_id"], d["text"],
                          parse_time(d.get("created_at"), "created_at"))
        if valid is None:
            return JSONResponse({"stored": False, "reason": "کاربر ثبت‌نام نکرده؛ برای کمینه‌سازی داده ذخیره نشد"})
        return JSONResponse({"stored": True, "valid": valid}, status_code=201)

    async def city_invalidate(request):
        name, _ = auth(request, "editor")
        if not city.invalidate_comment(db, request.path_params["cid"]):
            raise HTTPException(404, "کامنت پیدا نشد")
        repo.audit(db, name, "city.comment_invalidated", "comment", request.path_params["cid"])
        return JSONResponse({"ok": True})

    async def city_follow(request):
        name, _ = auth(request, "editor")
        d = await body(request)
        if not isinstance(d.get("follows"), bool):
            raise HTTPException(400, "follows باید true/false باشد")
        return JSONResponse(citizen_view(city_call(city.set_follow, db, d.get("ig_id", ""), d["follows"])))

    async def city_verify(request):
        name, _ = auth(request, "editor")
        d = await body(request)
        return JSONResponse(citizen_view(city_call(city.verify, db, d.get("ig_id", ""), name)))

    async def city_rename(request):
        name, _ = auth(request, "editor")
        d = await body(request)
        c = city_call(city.rename, db, d.get("old", ""), d.get("new", ""))
        repo.audit(db, name, "city.rename", "citizen", c["ig_id"], {"old": d.get("old")})
        return JSONResponse(citizen_view(c))

    async def city_consent(request):
        name, _ = auth(request, "editor")
        d = await body(request)
        if not isinstance(d.get("consent_display"), bool):
            raise HTTPException(400, "consent_display باید true/false باشد")
        c = city_call(city.set_consent, db, d.get("ig_id", ""), d["consent_display"], d.get("job"))
        repo.audit(db, name, "city.consent", "citizen", c["ig_id"], {"consent_display": d["consent_display"]})
        return JSONResponse(citizen_view(c))

    async def city_erase(request):
        name, _ = auth(request, "admin")
        if not city_call(city.erase, db, request.path_params["ig"]):
            raise HTTPException(404, "شهروند پیدا نشد")
        repo.audit(db, name, "city.erase", "citizen", request.path_params["ig"])
        return JSONResponse({"ok": True})

    async def city_stats(request):
        auth(request, "viewer")
        return JSONResponse(city.stats(db))

    async def city_raw(request):
        name, _ = auth(request, "editor")
        d = await body(request)
        if not isinstance(d.get("raw_followers"), int) or d["raw_followers"] < 0:
            raise HTTPException(400, "raw_followers باید عدد صحیح نامنفی باشد")
        city.set_raw_followers(db, d["raw_followers"])
        return JSONResponse(city.stats(db))

    async def city_list(request):
        auth(request, "viewer")
        status = request.query_params.get("status", "pending")
        return JSONResponse([citizen_view(c) for c in db.all(
            "SELECT * FROM citizens WHERE status=? ORDER BY registered_at DESC LIMIT ?", (status, int_param(request, "limit", 100)))])

    # ----- monitoring of existing flows, metrics, calendar -----
    async def external_event(request):
        name, _ = auth(request, "editor")
        d = await body(request)
        agent = agent_or_404(d.get("agent", ""))
        if agent["agent_type"] != "monitor":
            raise HTTPException(409, "این ایجنت مانیتورینگ نیست")
        ext = str(d.get("external_id", "")).strip()
        text = d.get("text")
        if not ext or not isinstance(text, str) or not text.strip():
            raise HTTPException(400, "external_id و text الزامی است")
        import hashlib
        uid = "ext_" + hashlib.sha256(f"{agent['key']}:{ext}".encode()).hexdigest()[:16]
        if repo.get_content(db, uid):
            return JSONResponse({"uid": uid, "duplicate": True})
        ok = d.get("ok", True) is True
        repo.insert_content(db, uid, agent["key"], None, "external", str(d.get("title", ""))[:200], text.strip(),
                            {"external_id": ext, "monitor_only": True}, "published" if ok else "failed",
                            None if ok else str(d.get("error", "خطا در جریان فعلی"))[:300])
        repo.add_publish_log(db, uid, agent["title"], ok, external_id=ext, error=None if ok else str(d.get("error", ""))[:300])
        return JSONResponse({"uid": uid, "duplicate": False}, status_code=201)

    async def metrics_push(request):
        auth(request, "editor")
        d = await body(request)
        rows = d.get("rows")
        if not isinstance(rows, list) or not rows or len(rows) > 1000:
            raise HTTPException(400, "rows باید فهرستی با ۱ تا ۱۰۰۰ مورد باشد")
        for r in rows:
            if not (isinstance(r, dict) and isinstance(r.get("source"), str) and isinstance(r.get("name"), str)
                    and isinstance(r.get("day"), str) and isinstance(r.get("value"), (int, float))):
                raise HTTPException(400, "هر مورد: source, name, day (YYYY-MM-DD), value")
            try:
                from datetime import date
                date.fromisoformat(r["day"])
            except ValueError:
                raise HTTPException(400, "day باید YYYY-MM-DD باشد")
        with db.tx() as c:
            for r in rows:
                c.execute("INSERT INTO metrics (source,name,day,value) VALUES (?,?,?,?) "
                          "ON CONFLICT(source,name,day) DO UPDATE SET value=excluded.value",
                          (r["source"], r["name"], r["day"], r["value"]))
        return JSONResponse({"stored": len(rows)}, status_code=201)

    async def metrics_list(request):
        auth(request, "viewer")
        name = request.query_params.get("name")
        return JSONResponse(db.all("SELECT * FROM metrics " + ("WHERE name=? " if name else "") + "ORDER BY day DESC, id DESC LIMIT ?",
                                   ((name, int_param(request, "limit", 200, 2000)) if name else (int_param(request, "limit", 200, 2000),))))

    async def calendar(request):
        auth(request, "viewer")
        return JSONResponse(scheduler.upcoming(db, int_param(request, "days", 14, 60), tz_name=settings.timezone))

    async def audit_list(request):
        auth(request, "admin")
        return JSONResponse(db.all("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (int_param(request, "limit", 100),)))

    async def worker_tick(request):
        """Run one scheduler tick and process the queue (handy when no worker is running)."""
        auth(request, "admin")
        enq = scheduler.tick(db, tz_name=settings.timezone)
        results = orchestrator.drain(db, "api-tick", settings, limit=20)
        cm = repo.get_agent(db, "campaign_manager")
        sms_stats = campaign.dispatch(db, sms.get_provider(), cm, tz_name=settings.timezone) if cm and cm["enabled"] else None
        return JSONResponse({"enqueued": enq, "processed": results, "sms": sms_stats})

    async def handle_http(request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    routes = [
        Route("/api/health", health),
        Route("/api/me", me),
        Route("/api/overview", overview),
        Route("/api/agents", agents_list),
        Route("/api/agents/{key}", agent_update, methods=["PUT"]),
        Route("/api/agents/{key}/run", agent_run, methods=["POST"]),
        Route("/api/channels", channels_list),
        Route("/api/channels/{cid:int}", channel_update, methods=["PUT"]),
        Route("/api/jobs", jobs_list),
        Route("/api/jobs/{jid:int}/retry", job_retry, methods=["POST"]),
        Route("/api/content", content_list),
        Route("/api/content/{uid}", content_trace),
        Route("/api/approvals", approvals_list),
        Route("/api/approvals/{aid:int}/decide", approval_decide, methods=["POST"]),
        Route("/api/inbox", inbox_add, methods=["POST"]),
        Route("/api/campaign/contacts", campaign_contact, methods=["POST"]),
        Route("/api/campaign/contacts/{ext}", campaign_erase, methods=["DELETE"]),
        Route("/api/campaign/events", campaign_event, methods=["POST"]),
        Route("/api/campaign/optout", campaign_optout, methods=["POST"]),
        Route("/api/campaign/manual", campaign_manual, methods=["POST"]),
        Route("/api/campaign/messages", campaign_messages),
        Route("/api/campaign/stats", campaign_stats),
        Route("/api/city/register", city_register, methods=["POST"]),
        Route("/api/city/comments", city_comment, methods=["POST"]),
        Route("/api/city/comments/{cid}/invalidate", city_invalidate, methods=["POST"]),
        Route("/api/city/follow", city_follow, methods=["POST"]),
        Route("/api/city/verify", city_verify, methods=["POST"]),
        Route("/api/city/rename", city_rename, methods=["POST"]),
        Route("/api/city/consent", city_consent, methods=["POST"]),
        Route("/api/city/raw-followers", city_raw, methods=["POST"]),
        Route("/api/city/citizens", city_list),
        Route("/api/city/citizens/{ig}", city_erase, methods=["DELETE"]),
        Route("/api/city/stats", city_stats),
        Route("/api/external/events", external_event, methods=["POST"]),
        Route("/api/metrics", metrics_push, methods=["POST"]),
        Route("/api/metrics", metrics_list),
        Route("/api/calendar", calendar),
        Route("/api/audit", audit_list),
        Route("/api/worker/tick", worker_tick, methods=["POST"]),
        Mount("/", StaticFiles(directory=STATIC_DIR, html=True)),
    ]
    app = Starlette(routes=routes, exception_handlers={HTTPException: handle_http})
    app.state.db = db
    app.state.settings = settings
    return app
