"""Background worker: ``python -m app.worker``.

Every cycle: recover stale jobs, enqueue due schedule slots, run queued jobs.
Run exactly one worker per database in Phase 1 (jobs are claimed atomically, so
more are safe, but one is plenty for this load).
"""
import logging
import os
import signal
import socket
import time

from . import approver, campaign, config, orchestrator, queue, repo, scheduler, seed
from .connectors import sms
from .db import DB

log = logging.getLogger("worker")


def cycle(db, settings, worker_id: str) -> int:
    queue.recover_stale(db, settings.stale_job_minutes)
    scheduler.tick(db, tz_name=settings.timezone)
    campaign.recover_unknown(db)
    cm = repo.get_agent(db, "campaign_manager")
    if cm and cm["enabled"]:
        st = campaign.dispatch(db, sms.get_provider(), cm, tz_name=settings.timezone)
        if any(st.values()):
            log.info("campaign dispatch %s", st)
    try:                                   # approvals inside Bale (needs BALE_TOKEN + APPROVER_IDS)
        approver.poll(db)
        approver.notify_pending(db)
    except Exception:  # noqa: BLE001
        log.exception("approver cycle failed")
    results = orchestrator.drain(db, worker_id, settings)
    for r in results:
        log.info("job %s %s -> %s %s", r["job_id"], r["kind"], r["status"], r.get("note") or r.get("error", ""))
    return len(results)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = config.from_env()
    db = DB(settings.db_path)
    if settings.seed:
        seed.ensure(db)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    log.info("worker %s started", worker_id)
    while not stop["flag"]:
        try:
            cycle(db, settings, worker_id)
        except Exception:  # noqa: BLE001 - the loop must survive any single failure
            log.exception("worker cycle failed")
        time.sleep(5)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
