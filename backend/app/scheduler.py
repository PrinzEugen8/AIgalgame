from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from .database import SessionLocal
from .models import User
from .opening import prepare_due_openings
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
    scheduler.add_job(
        _prewarm_job,
        "interval",
        minutes=1,
        id="proactive_prewarm",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        next_run_time=datetime.now() + timedelta(seconds=8),
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("scheduler started daily_cycle=04:00 proactive_prewarm=60s Asia/Hong_Kong")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("scheduler stopped")
    _scheduler = None


def _daily_job() -> None:
    logger.info("daily cycle job started")
    results: list[dict[str, object]] = []
    try:
        with SessionLocal() as session:
            ensure_seed(session, DEFAULT_USER_ID, DEFAULT_CHARACTER_ID)
            users = session.execute(select(User).order_by(User.created_at)).scalars().all()
            for user in users:
                if not user.story_completed:
                    continue
                result = run_daily_cycle(session, user_id=user.user_id, character_id=DEFAULT_CHARACTER_ID, day=datetime.now())
                results.append({"user_id": user.user_id, **result})
    except Exception:
        logger.exception("daily cycle job failed")
        raise
    logger.info("daily cycle job completed results=%s", results)


def _prewarm_job() -> None:
    logger.info("proactive prewarm job started")
    try:
        with SessionLocal() as session:
            ensure_seed(session, DEFAULT_USER_ID, DEFAULT_CHARACTER_ID)
            result = prepare_due_openings(session, character_id=DEFAULT_CHARACTER_ID, limit=8, generate_news=False)
    except Exception:
        logger.exception("proactive prewarm job failed")
        raise
    logger.info("proactive prewarm job completed result=%s", result)
