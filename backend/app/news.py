from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .diagnostics import write_diagnostic
from .models import ProviderConfig, TrendRadarSnapshot
from .providers import TrendRadarClient, get_enabled_provider_by_provider, provider_ready
from .utils import dump_json, load_json, uid, utc_now


TREND_RADAR_TIMEZONE = ZoneInfo("Asia/Hong_Kong")
TREND_RADAR_LAZY_SYNC_AFTER = time(4, 0)


def _trend_radar_local_time(local_time: datetime | None = None) -> datetime:
    if local_time is None:
        return datetime.now(TREND_RADAR_TIMEZONE)
    if local_time.tzinfo is None:
        return local_time.replace(tzinfo=TREND_RADAR_TIMEZONE)
    return local_time.astimezone(TREND_RADAR_TIMEZONE)


def _snapshot_for_date(
    session: Session,
    *,
    provider_id: str,
    local_date: str,
    status: str | None = None,
) -> TrendRadarSnapshot | None:
    stmt = select(TrendRadarSnapshot).where(
        TrendRadarSnapshot.provider_id == provider_id,
        TrendRadarSnapshot.local_date == local_date,
    )
    if status:
        stmt = stmt.where(TrendRadarSnapshot.status == status)
    return session.execute(stmt.order_by(TrendRadarSnapshot.fetched_at.desc()).limit(1)).scalar_one_or_none()


def _latest_ok_snapshot(session: Session, *, provider_id: str) -> TrendRadarSnapshot | None:
    return session.execute(
        select(TrendRadarSnapshot)
        .where(TrendRadarSnapshot.provider_id == provider_id, TrendRadarSnapshot.status == "ok")
        .order_by(TrendRadarSnapshot.local_date.desc(), TrendRadarSnapshot.fetched_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _exception_status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return int(status_code) if isinstance(status_code, int) else None


def _snapshot_allows_retry(snapshot: TrendRadarSnapshot) -> bool:
    if snapshot.status == "pending":
        return True
    if snapshot.status != "error":
        return False
    message = (snapshot.error_message or "").lower()
    return "404" in message or "not found" in message


def _record_snapshot(
    session: Session,
    *,
    config: ProviderConfig,
    local_date: str,
    status: str,
    endpoint: str,
    payload: dict[str, Any] | None = None,
    error_message: str = "",
) -> TrendRadarSnapshot:
    now = utc_now()
    snapshot = TrendRadarSnapshot(
        snapshot_id=uid("trend"),
        provider_id=config.provider_id,
        local_date=local_date,
        status=status,
        generated_at=str((payload or {}).get("generated_at") or ""),
        fetched_at=now,
        endpoint=endpoint,
        error_message=error_message[:1200],
        payload_json=dump_json(payload or {}),
        created_at=now,
        updated_at=now,
    )
    session.add(snapshot)
    session.commit()
    return snapshot


def sync_trend_radar_snapshot(
    session: Session,
    *,
    config: ProviderConfig | None = None,
    local_time: datetime | None = None,
    force: bool = False,
) -> TrendRadarSnapshot | None:
    config = config or get_enabled_provider_by_provider(session, "search", "trend_radar")
    if config is None or config.provider != "trend_radar":
        write_diagnostic("trend_radar_sync_skipped", reason="provider_not_configured")
        return None
    if not provider_ready(config):
        write_diagnostic("trend_radar_sync_skipped", reason="provider_not_ready", provider_id=config.provider_id)
        return None

    now_local = _trend_radar_local_time(local_time)
    local_date = now_local.date().isoformat()
    if not force:
        previous = _snapshot_for_date(session, provider_id=config.provider_id, local_date=local_date)
        if previous is not None and not _snapshot_allows_retry(previous):
            write_diagnostic(
                "trend_radar_sync_skipped",
                reason="already_attempted",
                provider_id=config.provider_id,
                snapshot_id=previous.snapshot_id,
                status=previous.status,
                local_date=local_date,
            )
            return previous if previous.status == "ok" else None

    client = TrendRadarClient(config)
    endpoint = client.endpoint_for_date(now_local.date())
    try:
        payload = client.fetch_for_date(now_local.date())
    except Exception as exc:  # noqa: BLE001
        status_code = _exception_status_code(exc)
        if status_code == 404:
            write_diagnostic(
                "trend_radar_sync_pending",
                provider_id=config.provider_id,
                local_date=local_date,
                endpoint=endpoint,
                status_code=status_code,
                error_type=type(exc).__name__,
                message=str(exc),
            )
            return None
        snapshot = _record_snapshot(
            session,
            config=config,
            local_date=local_date,
            status="error",
            endpoint=endpoint,
            error_message=str(exc),
        )
        write_diagnostic(
            "trend_radar_sync_failed",
            provider_id=config.provider_id,
            snapshot_id=snapshot.snapshot_id,
            local_date=local_date,
            endpoint=endpoint,
            error_type=type(exc).__name__,
            message=str(exc),
        )
        return None

    snapshot = _record_snapshot(session, config=config, local_date=local_date, status="ok", endpoint=endpoint, payload=payload)
    write_diagnostic(
        "trend_radar_sync_succeeded",
        provider_id=config.provider_id,
        snapshot_id=snapshot.snapshot_id,
        local_date=local_date,
        endpoint=endpoint,
        generated_at=snapshot.generated_at,
        trend_count=len(payload.get("trends") or []),
        total_titles_processed=payload.get("total_titles_processed") or 0,
    )
    return snapshot


def dispatch_trend_radar_workflow(
    session: Session,
    *,
    config: ProviderConfig | None = None,
    local_time: datetime | None = None,
) -> TrendRadarSnapshot | None:
    config = config or get_enabled_provider_by_provider(session, "search", "trend_radar")
    if config is None or config.provider != "trend_radar":
        write_diagnostic("trend_radar_dispatch_skipped", reason="provider_not_configured")
        return None
    if not provider_ready(config):
        write_diagnostic("trend_radar_dispatch_skipped", reason="provider_not_ready", provider_id=config.provider_id)
        return None

    now_local = _trend_radar_local_time(local_time)
    local_date = now_local.date().isoformat()
    existing = _snapshot_for_date(session, provider_id=config.provider_id, local_date=local_date, status="ok")
    if existing is not None:
        write_diagnostic(
            "trend_radar_dispatch_skipped",
            reason="snapshot_already_ok",
            provider_id=config.provider_id,
            snapshot_id=existing.snapshot_id,
            local_date=local_date,
        )
        return existing

    client = TrendRadarClient(config)
    if not client.github_token_configured():
        write_diagnostic("trend_radar_dispatch_skipped", reason="missing_github_token", provider_id=config.provider_id, local_date=local_date)
        return None

    try:
        payload, run_info = client.dispatch_and_fetch_for_date(now_local.date())
    except Exception as exc:  # noqa: BLE001
        write_diagnostic(
            "trend_radar_dispatch_failed",
            provider_id=config.provider_id,
            local_date=local_date,
            error_type=type(exc).__name__,
            message=str(exc),
        )
        return None

    endpoint = client.endpoint_for_date(now_local.date())
    snapshot = _record_snapshot(session, config=config, local_date=local_date, status="ok", endpoint=endpoint, payload=payload)
    write_diagnostic(
        "trend_radar_dispatch_succeeded",
        provider_id=config.provider_id,
        snapshot_id=snapshot.snapshot_id,
        local_date=local_date,
        endpoint=endpoint,
        generated_at=snapshot.generated_at,
        trend_count=len(payload.get("trends") or []),
        total_titles_processed=payload.get("total_titles_processed") or 0,
        **run_info,
    )
    return snapshot


def trend_radar_payload_for_news(
    session: Session,
    *,
    config: ProviderConfig,
    local_time: datetime,
) -> dict[str, Any] | None:
    now_local = _trend_radar_local_time(local_time)
    local_date = now_local.date().isoformat()
    snapshot = _snapshot_for_date(session, provider_id=config.provider_id, local_date=local_date, status="ok")
    if snapshot is not None:
        return load_json(snapshot.payload_json, {})

    previous = _snapshot_for_date(session, provider_id=config.provider_id, local_date=local_date)
    if previous is not None and not _snapshot_allows_retry(previous):
        write_diagnostic(
            "trend_radar_news_skipped",
            reason="sync_already_failed",
            provider_id=config.provider_id,
            snapshot_id=previous.snapshot_id,
            local_date=local_date,
            status=previous.status,
        )
        return None

    if now_local.time() < TREND_RADAR_LAZY_SYNC_AFTER:
        write_diagnostic(
            "trend_radar_news_skipped",
            reason="before_daily_sync_window",
            provider_id=config.provider_id,
            local_date=local_date,
            local_time=now_local.isoformat(),
        )
        return None

    snapshot = sync_trend_radar_snapshot(session, config=config, local_time=now_local, force=False)
    if snapshot is not None and snapshot.status == "ok":
        return load_json(snapshot.payload_json, {})
    fallback = _latest_ok_snapshot(session, provider_id=config.provider_id)
    if fallback is not None:
        write_diagnostic(
            "trend_radar_news_fallback",
            reason="today_snapshot_unavailable",
            provider_id=config.provider_id,
            snapshot_id=fallback.snapshot_id,
            fallback_date=fallback.local_date,
            requested_date=local_date,
        )
        return load_json(fallback.payload_json, {})
    if snapshot is None or snapshot.status != "ok":
        return None
    return load_json(snapshot.payload_json, {})
