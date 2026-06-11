from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .database import SessionLocal
from .diagnostics import write_diagnostic
from .models import ProactiveEvent, User, UserCommitment
from .news import dispatch_trend_radar_workflow, sync_trend_radar_snapshot
from .online import is_online
from .opening import has_fresh_proactive_opening_cache, prepare_due_openings
from .push import send_selected_background_push
from .schedule import run_daily_cycle
from .seed import DEFAULT_CHARACTER_ID, DEFAULT_USER_ID, ensure_seed
from .utils import load_json


logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def _prewarm_interval_minutes(value: int | None = None) -> int:
    raw = settings.prewarm_interval_minutes if value is None else value
    try:
        resolved = int(raw)
    except (TypeError, ValueError):
        resolved = 15
    return max(5, resolved)


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return
    scheduler = BackgroundScheduler(timezone="Asia/Hong_Kong")
    scheduler.add_job(_trend_radar_dispatch_job, "cron", hour=3, minute=0, id="trend_radar_dispatch", replace_existing=True)
    scheduler.add_job(_daily_job, "cron", hour=4, minute=0, id="daily_cycle", replace_existing=True)
    scheduler.add_job(
        _prewarm_job,
        "interval",
        minutes=_prewarm_interval_minutes(),
        id="proactive_prewarm",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        next_run_time=datetime.now() + timedelta(minutes=2),
    )
    scheduler.start()
    _scheduler = scheduler
    try:
        with SessionLocal() as session:
            restored = restore_commitment_jobs(session)
    except Exception:
        logger.exception("restore commitment jobs failed")
        restored = 0
    logger.info(
        "scheduler started trend_radar_dispatch=03:00 daily_cycle=04:00 proactive_prewarm=%sm first_run=120s restored_commitments=%s Asia/Hong_Kong",
        _prewarm_interval_minutes(),
        restored,
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("scheduler stopped")
    _scheduler = None


def _trend_radar_dispatch_job() -> None:
    logger.info("trend radar dispatch job started")
    try:
        with SessionLocal() as session:
            ensure_seed(session, DEFAULT_USER_ID, DEFAULT_CHARACTER_ID)
            snapshot = dispatch_trend_radar_workflow(session, local_time=datetime.now())
            result = {
                "trend_radar_snapshot_id": snapshot.snapshot_id if snapshot is not None else "",
                "trend_radar_status": snapshot.status if snapshot is not None else "skipped",
            }
    except Exception:
        logger.exception("trend radar dispatch job failed")
        raise
    logger.info("trend radar dispatch job completed result=%s", result)


def _daily_job() -> None:
    logger.info("daily cycle job started")
    results: list[dict[str, object]] = []
    try:
        with SessionLocal() as session:
            ensure_seed(session, DEFAULT_USER_ID, DEFAULT_CHARACTER_ID)
            trend_snapshot = sync_trend_radar_snapshot(session, local_time=datetime.now(), force=True)
            results.append(
                {
                    "trend_radar_snapshot_id": trend_snapshot.snapshot_id if trend_snapshot is not None else "",
                    "trend_radar_status": trend_snapshot.status if trend_snapshot is not None else "skipped",
                }
            )
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
            result = _prewarm_once(session)
    except Exception:
        logger.exception("proactive prewarm job failed")
        raise
    logger.info("proactive prewarm job completed result=%s", result)


def _prewarm_once(session: Session, *, user_ids: set[str] | None = None) -> dict[str, int | bool]:
    stmt = select(User).order_by(User.created_at)
    if user_ids:
        stmt = stmt.where(User.user_id.in_(user_ids))
    users = session.execute(stmt).scalars().all()
    result: dict[str, int | bool] = {
        "ok": True,
        "checked": 0,
        "prepared": 0,
        "failed": 0,
        "skipped_not_ready": 0,
        "skipped_online": 0,
        "skipped_cache": 0,
        "skipped_no_event": 0,
        "push_sent": 0,
        "push_failed": 0,
        "push_skipped": 0,
    }
    write_diagnostic(
        "proactive_prewarm_scheduler_started",
        feature="开场预热",
        stage="_prewarm_job",
        summary=f"users={len(users)}",
        interval_minutes=_prewarm_interval_minutes(),
    )
    for user in users:
        result["checked"] = int(result["checked"]) + 1
        if not user.story_completed or not user.notifications_enabled:
            result["skipped_not_ready"] = int(result["skipped_not_ready"]) + 1
            write_diagnostic(
                "proactive_prewarm_skipped",
                feature="开场预热",
                stage="_prewarm_job",
                user_id=user.user_id,
                reason="not_ready",
            )
            continue
        if is_online(user.user_id):
            result["skipped_online"] = int(result["skipped_online"]) + 1
            write_diagnostic(
                "proactive_prewarm_skipped",
                feature="开场预热",
                stage="_prewarm_job",
                user_id=user.user_id,
                reason="user_online",
            )
            continue
        if has_fresh_proactive_opening_cache(session, user_id=user.user_id, character_id=DEFAULT_CHARACTER_ID):
            result["skipped_cache"] = int(result["skipped_cache"]) + 1
            write_diagnostic(
                "proactive_prewarm_skipped",
                feature="开场预热",
                stage="_prewarm_job",
                user_id=user.user_id,
                reason="fresh_proactive_cache",
            )
            continue
        try:
            prepared = prepare_due_openings(
                session,
                user_id=user.user_id,
                character_id=DEFAULT_CHARACTER_ID,
                limit=1,
                generate_news=False,
                generate_weather=True,
            )
        except Exception as exc:  # noqa: BLE001
            result["failed"] = int(result["failed"]) + 1
            write_diagnostic(
                "proactive_prewarm_failed",
                feature="开场预热",
                stage="_prewarm_job",
                user_id=user.user_id,
                message=str(exc),
            )
            continue
        prepared_count = int(prepared.get("prepared") or 0)
        failed_count = int(prepared.get("failed") or 0)
        skipped_count = int(prepared.get("skipped") or 0)
        result["prepared"] = int(result["prepared"]) + prepared_count
        result["failed"] = int(result["failed"]) + failed_count
        result["skipped_no_event"] = int(result["skipped_no_event"]) + skipped_count
        judgement = load_json(user.proactive_judgement_json, {})
        selected_event_id = str(judgement.get("selected_event_id") or "") if isinstance(judgement, dict) else ""
        if prepared_count and selected_event_id:
            try:
                push_result = send_selected_background_push(session, selected_event_id)
                result["push_sent"] = int(result["push_sent"]) + int(push_result.get("sent") or 0)
                result["push_failed"] = int(result["push_failed"]) + int(push_result.get("failed") or 0)
                result["push_skipped"] = int(result["push_skipped"]) + int(push_result.get("skipped") or 0)
            except Exception as exc:  # noqa: BLE001
                result["push_failed"] = int(result["push_failed"]) + 1
                write_diagnostic(
                    "proactive_push_failed",
                    feature="开场预热",
                    stage="_prewarm_job",
                    user_id=user.user_id,
                    proactive_event_id=selected_event_id,
                    message=str(exc),
                )
    write_diagnostic("proactive_prewarm_scheduler_finished", feature="开场预热", stage="_prewarm_job", **result)
    return result


def schedule_commitment_delivery(commitment_id: str, remind_at: datetime) -> None:
    global _scheduler
    if _scheduler is None or not _scheduler.running:
        return
    run_at = remind_at
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=timezone.utc)
    if run_at <= datetime.now(timezone.utc):
        run_at = datetime.now(timezone.utc) + timedelta(seconds=2)
    _scheduler.add_job(
        _commitment_delivery_job,
        trigger="date",
        run_date=run_at,
        args=[commitment_id],
        id=f"commitment:{commitment_id}",
        replace_existing=True,
    )
    write_diagnostic("commitment_job_scheduled", commitment_id=commitment_id, run_at=run_at.isoformat())


def _commitment_delivery_job(commitment_id: str) -> None:
    from .proactive import deliver_commitment_reminder

    logger.info("commitment delivery job started commitment_id=%s", commitment_id)
    try:
        with SessionLocal() as session:
            result = deliver_commitment_reminder(session, commitment_id)
            event = result.get("event") or {}
            event_id = str(event.get("proactive_event_id") or "") if isinstance(event, dict) else ""
            if event_id:
                push_result = send_selected_background_push(session, event_id)
                write_diagnostic("commitment_push_finished", commitment_id=commitment_id, **push_result)
    except Exception:
        logger.exception("commitment delivery job failed commitment_id=%s", commitment_id)
        raise
    logger.info("commitment delivery job completed commitment_id=%s", commitment_id)


def restore_commitment_jobs(session: Session, *, horizon_hours: int = 24) -> int:
    now_utc = datetime.now(timezone.utc)
    horizon = now_utc + timedelta(hours=horizon_hours)
    restored = 0
    commitments = session.execute(select(UserCommitment).order_by(UserCommitment.remind_at)).scalars().all()
    for commitment in commitments:
        remind_at = datetime.fromisoformat(str(commitment.remind_at).replace("Z", "+00:00"))
        if remind_at.tzinfo is None:
            remind_at = remind_at.replace(tzinfo=timezone.utc)
        if remind_at > horizon:
            continue
        pending = session.execute(
            select(ProactiveEvent).where(
                ProactiveEvent.source_type == "appointment",
                ProactiveEvent.source_id == commitment.commitment_id,
                ProactiveEvent.status == "pending",
            )
        ).scalar_one_or_none()
        if pending is None:
            continue
        schedule_commitment_delivery(commitment.commitment_id, remind_at)
        restored += 1
    write_diagnostic("commitment_jobs_restored", restored=restored)
    return restored
