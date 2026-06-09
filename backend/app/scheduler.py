from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from .database import SessionLocal
from .schedule import run_daily_cycle
from .seed import DEFAULT_CHARACTER_ID, DEFAULT_USER_ID, ensure_seed


logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return
    scheduler = BackgroundScheduler(timezone="Asia/Hong_Kong")
    scheduler.add_job(_daily_job, "cron", hour=4, minute=0, id="daily_cycle", replace_existing=True)
    scheduler.start()
    _scheduler = scheduler
    logger.info("scheduler started daily_cycle=04:00 Asia/Hong_Kong")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("scheduler stopped")
    _scheduler = None


def _daily_job() -> None:
    logger.info("daily cycle job started")
    try:
        with SessionLocal() as session:
            ensure_seed(session, DEFAULT_USER_ID, DEFAULT_CHARACTER_ID)
            result = run_daily_cycle(session, user_id=DEFAULT_USER_ID, character_id=DEFAULT_CHARACTER_ID, day=datetime.now())
    except Exception:
        logger.exception("daily cycle job failed")
        raise
    logger.info("daily cycle job completed result=%s", result)
