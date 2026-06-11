from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .diagnostics import write_diagnostic
from .models import User, UserCommitment
from .proactive import create_proactive_event
from .providers import OpenAICompatibleClient, get_task_llm_provider
from .schemas import EventIn
from .utils import dump_json, stable_hash, uid, utc_now


TIME_HINTS = ("明天", "后天", "早上", "上午", "中午", "下午", "晚上", "今晚", "明早", "点", ":", "：")
COMMITMENT_HINTS = ("提醒", "记得", "别忘", "赶", "飞机", "航班", "火车", "高铁", "会议", "上班", "考试", "约", "日程", "安排")


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _user_zone(user: User) -> ZoneInfo:
    try:
        return ZoneInfo(user.timezone or "Asia/Hong_Kong")
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Hong_Kong")


def _looks_like_commitment(text: str) -> bool:
    normalized = str(text or "").strip()
    return bool(normalized) and any(item in normalized for item in TIME_HINTS) and any(item in normalized for item in COMMITMENT_HINTS)


def _fallback_remind_at(event_at: datetime) -> datetime:
    hour = event_at.astimezone(event_at.tzinfo or timezone.utc).hour
    if 5 <= hour <= 11:
        return event_at - timedelta(hours=1)
    return event_at - timedelta(hours=2)


def extract_user_commitment(
    session: Session,
    *,
    event: EventIn,
    text: str,
    local_time: datetime | None = None,
) -> UserCommitment | None:
    if event.event_type != "user_message" or not _looks_like_commitment(text):
        return None
    user = session.get(User, event.user_id)
    if user is None:
        return None
    config = get_task_llm_provider(session)
    if config is None:
        write_diagnostic("commitment_extract_skipped", reason="llm_not_configured", user_id=event.user_id)
        return None
    zone = _user_zone(user)
    now_local = local_time.astimezone(zone) if local_time and local_time.tzinfo else (local_time.replace(tzinfo=zone) if local_time else datetime.now(zone))
    prompt = {
        "task": "Extract a future user commitment/reminder from the message. Return JSON only.",
        "now_local": now_local.isoformat(),
        "timezone": user.timezone,
        "message": text,
        "schema": {
            "has_commitment": "boolean",
            "title": "short title",
            "description": "details",
            "event_at": "ISO datetime with timezone",
            "remind_at": "ISO datetime with timezone; when absent choose a natural reminder before event_at",
            "confidence": "0..1",
        },
    }
    try:
        result = OpenAICompatibleClient(config).chat_json(
            [
                {"role": "system", "content": "You extract explicit future appointments and reminders from user messages. Return JSON only."},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            max_tokens=500,
            temperature=0.0,
            diagnostic={
                "feature": "commitment_extraction",
                "stage": "extract",
                "purpose": "Extract user commitment from chat message",
                "input": {"user_id": event.user_id, "text": text, "now_local": now_local.isoformat()},
            },
        )
    except Exception as exc:  # noqa: BLE001
        write_diagnostic("commitment_extract_error", user_id=event.user_id, error_type=type(exc).__name__, message=str(exc))
        return None
    if not bool(result.get("has_commitment")):
        return None
    try:
        confidence = float(result.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < 0.55:
        return None
    event_at = _parse_iso(result.get("event_at"))
    if event_at is None:
        return None
    if event_at <= now_local.astimezone(event_at.tzinfo or timezone.utc):
        return None
    remind_at = _parse_iso(result.get("remind_at")) or _fallback_remind_at(event_at)
    title = " ".join(str(result.get("title") or "用户约定").split())[:120]
    description = " ".join(str(result.get("description") or text).split())[:500]
    dedupe_key = stable_hash(event.user_id, title, event_at.astimezone(timezone.utc).isoformat())
    existing = session.execute(
        select(UserCommitment).where(UserCommitment.user_id == event.user_id, UserCommitment.dedupe_key == dedupe_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    commitment = UserCommitment(
        commitment_id=uid("commit"),
        user_id=event.user_id,
        character_id=event.character_id,
        title=title,
        description=description,
        event_at=event_at.astimezone(timezone.utc).isoformat(),
        remind_at=remind_at.astimezone(timezone.utc).isoformat(),
        timezone=user.timezone,
        source_message_id=event.event_id or "",
        dedupe_key=dedupe_key,
        payload_json=dump_json({"source_text": text, "confidence": confidence}),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(commitment)
    create_proactive_event(
        session,
        user_id=event.user_id,
        character_id=event.character_id,
        source_type="appointment",
        source_id=commitment.commitment_id,
        title=title,
        text=description,
        priority=88 if any(item in text for item in ("飞机", "航班", "火车", "高铁", "考试")) else 76,
        dedupe_key=f"appointment:{event.user_id}:{dedupe_key}",
        payload={"commitment_id": commitment.commitment_id, "event_at": commitment.event_at, "remind_at": commitment.remind_at, "source_text": text},
        scheduled_at=remind_at,
        expires_at=event_at + timedelta(hours=2),
    )
    write_diagnostic("commitment_created", user_id=event.user_id, commitment_id=commitment.commitment_id, title=title, remind_at=commitment.remind_at)
    return commitment
