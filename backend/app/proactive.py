from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .diagnostics import write_diagnostic
from .models import ProactiveEvent, User
from .providers import VolcArkWebSearchClient, get_enabled_provider, provider_ready
from .utils import dump_json, load_json, uid, utc_now


PROACTIVE_STATUSES = {"pending", "delivered", "opened", "reflected", "expired", "dismissed"}
PROACTIVE_SOURCES = {"schedule", "memory", "moment_interaction", "news"}
MAX_DAILY_DELIVERIES = 3
MIN_DELIVERY_GAP = timedelta(minutes=90)
DEFAULT_EXPIRY = timedelta(days=2)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _user_zone(user: User | None) -> ZoneInfo:
    try:
        return ZoneInfo(user.timezone if user is not None else "Asia/Hong_Kong")
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Hong_Kong")


def _local_now(user: User | None, local_time: datetime | None = None) -> datetime:
    zone = _user_zone(user)
    if local_time is None:
        return datetime.now(zone)
    if local_time.tzinfo is None:
        return local_time.replace(tzinfo=zone)
    return local_time.astimezone(zone)


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_hhmm(value: str, fallback: time) -> time:
    try:
        hour, minute = str(value or "").split(":", 1)
        return time(int(hour), int(minute[:2]))
    except Exception:  # noqa: BLE001
        return fallback


def _is_sleep_time(user: User, now_local: datetime) -> bool:
    start = _parse_hhmm(user.sleep_start, time(0, 30))
    end = _parse_hhmm(user.sleep_end, time(8, 0))
    current = now_local.time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def _event_time_local(event: ProactiveEvent, user: User) -> datetime | None:
    value = _parse_iso(event.delivered_at or event.opened_at or event.updated_at or event.created_at)
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_user_zone(user))


def _daily_delivery_count(session: Session, user: User, now_local: datetime) -> int:
    events = session.execute(
        select(ProactiveEvent).where(
            ProactiveEvent.user_id == user.user_id,
            ProactiveEvent.status.in_(["delivered", "opened", "reflected"]),
        )
    ).scalars().all()
    return sum(1 for item in events if (local := _event_time_local(item, user)) is not None and local.date() == now_local.date())


def _latest_delivery(session: Session, user: User) -> datetime | None:
    events = session.execute(
        select(ProactiveEvent).where(
            ProactiveEvent.user_id == user.user_id,
            ProactiveEvent.status.in_(["delivered", "opened", "reflected"]),
        )
    ).scalars().all()
    times = [_event_time_local(item, user) for item in events]
    valid = [item for item in times if item is not None]
    return max(valid) if valid else None


def _expire_old_pending(session: Session, user_id: str, now_utc: datetime) -> None:
    for event in session.execute(
        select(ProactiveEvent).where(ProactiveEvent.user_id == user_id, ProactiveEvent.status == "pending")
    ).scalars():
        expires_at = _parse_iso(event.expires_at)
        if expires_at is not None and expires_at <= now_utc:
            event.status = "expired"
            event.updated_at = utc_now()
            write_diagnostic("proactive_expired", proactive_event_id=event.proactive_event_id, source_type=event.source_type)


def create_proactive_event(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    source_type: str,
    source_id: str = "",
    title: str,
    text: str,
    priority: int = 50,
    dedupe_key: str = "",
    payload: dict[str, Any] | None = None,
    scheduled_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> ProactiveEvent | None:
    source_type = source_type.strip()
    text = " ".join(str(text or "").split())
    title = " ".join(str(title or "").split()) or "小樱想和你说话"
    if source_type not in PROACTIVE_SOURCES or not text:
        return None
    if dedupe_key:
        existing = session.execute(
            select(ProactiveEvent).where(ProactiveEvent.user_id == user_id, ProactiveEvent.dedupe_key == dedupe_key)
        ).scalar_one_or_none()
        if existing is not None and existing.status != "dismissed":
            return existing
    now = datetime.now(timezone.utc)
    event = ProactiveEvent(
        proactive_event_id=uid("pe"),
        user_id=user_id,
        character_id=character_id,
        source_type=source_type,
        source_id=source_id,
        title=title[:120],
        text=text[:500],
        priority=max(0, min(100, int(priority))),
        status="pending",
        dedupe_key=dedupe_key,
        scheduled_at=_utc_iso(scheduled_at or now),
        expires_at=_utc_iso(expires_at or (now + DEFAULT_EXPIRY)),
        payload_json=dump_json(payload or {}),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(event)
    write_diagnostic("proactive_created", proactive_event_id=event.proactive_event_id, source_type=source_type, priority=event.priority)
    return event


def create_schedule_proactive_event(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    source_id: str,
    title: str,
    summary: str,
    activity_title: str,
    priority: int,
) -> ProactiveEvent | None:
    text = f"{summary} 我想等你有空的时候讲给你听。"
    return create_proactive_event(
        session,
        user_id=user_id,
        character_id=character_id,
        source_type="schedule",
        source_id=source_id,
        title=title or "小樱有一件日常想告诉你",
        text=text,
        priority=priority,
        dedupe_key=f"schedule:{user_id}:{source_id}",
        payload={"activity_title": activity_title, "summary": summary},
    )


def create_moment_feedback_event(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    interaction_id: str,
    interaction_type: str,
    content: str = "",
) -> ProactiveEvent | None:
    if interaction_type == "like":
        text = "我看到你给我的朋友圈点了赞。虽然只是一下下，但我还是有点开心。"
    else:
        text = f"我看到你在朋友圈里说「{content[:80]}」。这句话我想当面回应你。"
    return create_proactive_event(
        session,
        user_id=user_id,
        character_id=character_id,
        source_type="moment_interaction",
        source_id=interaction_id,
        title="小樱注意到了你的互动",
        text=text,
        priority=72 if interaction_type == "comment" else 64,
        dedupe_key=f"moment_interaction:{interaction_id}",
        payload={"interaction_type": interaction_type, "content": content},
    )


def create_memory_proactive_event(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    memory_id: str,
    content: str,
    priority: int = 58,
) -> ProactiveEvent | None:
    return create_proactive_event(
        session,
        user_id=user_id,
        character_id=character_id,
        source_type="memory",
        source_id=memory_id,
        title="小樱想起了一件事",
        text=f"我刚刚想起你之前提到过的事：{content[:160]}",
        priority=priority,
        dedupe_key=f"memory:{memory_id}",
        payload={"memory": content},
    )


def ensure_news_candidate(session: Session, *, user_id: str, character_id: str, local_time: datetime | None = None) -> ProactiveEvent | None:
    user = session.get(User, user_id)
    if user is None or not user.news_enabled:
        return None
    topics = [str(item).strip() for item in load_json(user.interest_topics_json, []) if str(item).strip()]
    if not topics:
        return None
    now_local = _local_now(user, local_time)
    topic = topics[-1]
    topic_key = f"news:{user_id}:{topic}:{now_local.date().isoformat()}"
    existing = session.execute(
        select(ProactiveEvent).where(ProactiveEvent.user_id == user_id, ProactiveEvent.dedupe_key == topic_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    search = get_enabled_provider(session, "search")
    if search is None:
        write_diagnostic("proactive_news_skipped", reason="search_not_configured", user_id=user_id, topic=topic)
        return None
    if search.provider != "volc_ark_web_search" or not provider_ready(search):
        write_diagnostic("proactive_news_skipped", reason="search_not_ready", provider_id=search.provider_id, topic=topic)
        return None
    try:
        result = VolcArkWebSearchClient(search).search(
            f"请联网搜索与「{topic}」相关的最新内容，必须返回标题、链接、发布时间。",
            require_published_at=True,
        )
    except Exception as exc:  # noqa: BLE001
        write_diagnostic("proactive_news_skipped", reason="search_error", topic=topic, error_type=type(exc).__name__, message=str(exc))
        return None
    sources = result.get("sources") or []
    if not sources or not sources[0].get("published_at"):
        write_diagnostic("proactive_news_skipped", reason="missing_verifiable_source", topic=topic)
        return None
    first = sources[0]
    summary = str(result.get("summary") or first.get("title") or "").strip()
    text = f"我刚看到和「{topic}」有关的新内容：{summary}"
    return create_proactive_event(
        session,
        user_id=user_id,
        character_id=character_id,
        source_type="news",
        source_id=str(first.get("url") or ""),
        title="小樱看到了一条新消息",
        text=text,
        priority=80,
        dedupe_key=topic_key,
        payload={"topic": topic, "sources": sources[:3], "summary": summary},
        expires_at=now_local.astimezone(timezone.utc) + timedelta(hours=18),
    )


def _next_due_pending(session: Session, *, user_id: str, character_id: str, now_utc: datetime) -> ProactiveEvent | None:
    events = session.execute(
        select(ProactiveEvent)
        .where(
            ProactiveEvent.user_id == user_id,
            ProactiveEvent.character_id == character_id,
            ProactiveEvent.status == "pending",
        )
        .order_by(ProactiveEvent.priority.desc(), ProactiveEvent.created_at)
    ).scalars().all()
    for event in events:
        scheduled_at = _parse_iso(event.scheduled_at) or now_utc
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        if scheduled_at <= now_utc:
            return event
    return None


def proactive_event_payload(event: ProactiveEvent | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        "proactive_event_id": event.proactive_event_id,
        "title": event.title,
        "text": event.text,
        "source_type": event.source_type,
        "priority": event.priority,
    }


def proactive_widget_payload(event: ProactiveEvent | None) -> dict[str, Any]:
    if event is None:
        return {
            "character_name": "小樱",
            "status": "想聊天",
            "bubble": "今天也想听你说说话。",
            "unread_count": 0,
            "proactive_event_id": "",
        }
    return {
        "character_name": "小樱",
        "status": "有话想说",
        "bubble": event.text[:80],
        "unread_count": 1,
        "proactive_event_id": event.proactive_event_id,
    }


def pending_proactive_response(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    local_time: datetime | None = None,
    generate_news: bool = True,
) -> dict[str, Any]:
    user = session.get(User, user_id)
    if user is None:
        return {"ok": True, "event": None, "widget": proactive_widget_payload(None)}
    if generate_news:
        ensure_news_candidate(session, user_id=user_id, character_id=character_id, local_time=local_time)
    now_local = _local_now(user, local_time)
    now_utc = now_local.astimezone(timezone.utc)
    _expire_old_pending(session, user_id, now_utc)
    event = _next_due_pending(session, user_id=user_id, character_id=character_id, now_utc=now_utc)
    if event is None:
        session.commit()
        return {"ok": True, "event": None, "widget": proactive_widget_payload(None)}
    if _is_sleep_time(user, now_local):
        session.commit()
        return {"ok": True, "event": None, "widget": proactive_widget_payload(None)}
    if _daily_delivery_count(session, user, now_local) >= MAX_DAILY_DELIVERIES:
        session.commit()
        return {"ok": True, "event": None, "widget": proactive_widget_payload(None)}
    latest = _latest_delivery(session, user)
    if latest is not None and now_local - latest < MIN_DELIVERY_GAP:
        session.commit()
        return {"ok": True, "event": None, "widget": proactive_widget_payload(None)}
    session.commit()
    return {"ok": True, "event": proactive_event_payload(event), "widget": proactive_widget_payload(event)}


def mark_proactive_delivered(session: Session, event_id: str) -> ProactiveEvent | None:
    event = session.get(ProactiveEvent, event_id)
    if event is None:
        return None
    if event.status == "pending":
        event.status = "delivered"
    event.delivered_at = event.delivered_at or utc_now()
    event.updated_at = utc_now()
    session.commit()
    write_diagnostic("proactive_delivered", proactive_event_id=event.proactive_event_id, source_type=event.source_type)
    return event


def mark_proactive_opened(session: Session, event_id: str) -> ProactiveEvent | None:
    event = session.get(ProactiveEvent, event_id)
    if event is None:
        return None
    if event.status in {"pending", "delivered"}:
        event.status = "opened"
    event.opened_at = event.opened_at or utc_now()
    event.updated_at = utc_now()
    session.commit()
    write_diagnostic("proactive_opened", proactive_event_id=event.proactive_event_id, source_type=event.source_type)
    return event


def mark_proactive_reflected(session: Session, event: ProactiveEvent) -> None:
    event.status = "reflected"
    event.reflected_at = event.reflected_at or utc_now()
    event.updated_at = utc_now()
    write_diagnostic("proactive_reflected", proactive_event_id=event.proactive_event_id, source_type=event.source_type)
