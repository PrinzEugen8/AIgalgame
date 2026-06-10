from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .diagnostics import write_diagnostic
from .models import Character, ProactiveEvent, ProviderConfig, User
from .news import trend_radar_payload_for_news
from .providers import OpenAICompatibleClient, ProviderError, VolcArkWebSearchClient, get_enabled_provider, get_task_llm_provider, provider_ready
from .utils import dump_json, load_json, uid, utc_now
from .weather import ensure_weather_candidate


PROACTIVE_STATUSES = {"pending", "delivered", "opened", "reflected", "expired", "dismissed"}
PROACTIVE_SOURCES = {"schedule", "memory", "moment_interaction", "news", "weather"}
DEFAULT_EXPIRY = timedelta(days=2)
PROACTIVE_JUDGE_CANDIDATE_LIMIT = 8
DEFAULT_JUDGE_NEXT_CHECK_MINUTES = 30
MAX_JUDGE_NEXT_CHECK_MINUTES = 24 * 60
JUDGE_TEXT_LIMIT = 180


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
    moment_id: str = "",
    content: str = "",
) -> ProactiveEvent | None:
    if interaction_type == "like":
        text = "我看到你给我的朋友圈点了赞。虽然只是一下下，但我还是有点开心。"
        dedupe_key = f"moment_interaction:{user_id}:{moment_id or interaction_id}:like"
    else:
        normalized = " ".join(content.split())[:80]
        text = f"我看到你在朋友圈里说「{normalized}」。这句话我想当面回应你。"
        dedupe_key = f"moment_interaction:{user_id}:{moment_id or interaction_id}:comment:{normalized}"
    return create_proactive_event(
        session,
        user_id=user_id,
        character_id=character_id,
        source_type="moment_interaction",
        source_id=interaction_id,
        title="小樱注意到了你的互动",
        text=text,
        priority=72 if interaction_type == "comment" else 64,
        dedupe_key=dedupe_key,
        payload={"interaction_type": interaction_type, "content": content, "moment_id": moment_id},
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


def _news_topic_records(session: Session, *, user_id: str, topics: list[str], now_local: datetime) -> tuple[list[dict[str, Any]], list[ProactiveEvent]]:
    records: list[dict[str, Any]] = []
    existing_events: list[ProactiveEvent] = []
    seen: set[str] = set()
    for topic in topics:
        normalized = " ".join(topic.split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        dedupe_key = f"news:{user_id}:{normalized}:{now_local.date().isoformat()}"
        existing = session.execute(
            select(ProactiveEvent).where(ProactiveEvent.user_id == user_id, ProactiveEvent.dedupe_key == dedupe_key)
        ).scalar_one_or_none()
        if existing is not None:
            existing_events.append(existing)
            continue
        records.append({"topic": normalized, "dedupe_key": dedupe_key})
    return records, existing_events


def _normalize_match_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _topic_matches(topic: str, haystack: str) -> bool:
    normalized_topic = _normalize_match_text(topic)
    normalized_haystack = _normalize_match_text(haystack)
    if not normalized_topic or not normalized_haystack:
        return False
    tokens = normalized_topic.split()
    if len(tokens) > 1:
        return all(token in normalized_haystack for token in tokens)
    return normalized_topic in normalized_haystack


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _trend_radar_score(trend: dict[str, Any], title: dict[str, Any]) -> int:
    ranks = [rank for rank in title.get("ranks") or [] if isinstance(rank, int)]
    best_rank = min(ranks) if ranks else 999
    rank_score = max(0, 45 - best_rank * 3)
    new_score = 55 if title.get("is_new") else 0
    trend_score = min(_as_int(trend.get("match_count")), 60)
    appearance_score = min(_as_int(title.get("appearance_count"), 1) * 4, 32)
    return new_score + rank_score + trend_score + appearance_score


def _related_trend_sources(trend: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for item in trend.get("titles") or []:
        if not isinstance(item, dict):
            continue
        sources.append(
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "source": str(item.get("source") or ""),
                "ranks": item.get("ranks") or [],
                "is_new": bool(item.get("is_new")),
                "appearance_count": _as_int(item.get("appearance_count"), 1),
                "time_info": str(item.get("time_info") or ""),
            }
        )
        if len(sources) >= limit:
            break
    return sources


def _trend_radar_max_titles(config: ProviderConfig) -> int:
    metadata = load_json(config.metadata_json, {})
    return max(1, min(_as_int(metadata.get("max_titles"), 3), 10))


def _ensure_trend_radar_news_candidate(
    session: Session,
    *,
    config: ProviderConfig,
    user_id: str,
    character_id: str,
    topic_records: list[dict[str, Any]],
    now_local: datetime,
) -> ProactiveEvent | None:
    if not provider_ready(config):
        write_diagnostic("proactive_news_skipped", reason="trend_radar_not_ready", provider_id=config.provider_id, user_id=user_id)
        return None
    try:
        payload = trend_radar_payload_for_news(session, config=config, local_time=now_local)
    except Exception as exc:  # noqa: BLE001
        write_diagnostic(
            "proactive_news_skipped",
            reason="trend_radar_error",
            provider_id=config.provider_id,
            user_id=user_id,
            error_type=type(exc).__name__,
            message=str(exc),
        )
        return None
    if not payload:
        write_diagnostic("proactive_news_skipped", reason="trend_radar_no_snapshot", provider_id=config.provider_id, user_id=user_id)
        return None
    best: dict[str, Any] | None = None
    for record in topic_records:
        topic = str(record["topic"])
        for trend in payload.get("trends") or []:
            if not isinstance(trend, dict):
                continue
            keyword_group = str(trend.get("keyword_group") or "")
            for title in trend.get("titles") or []:
                if not isinstance(title, dict):
                    continue
                haystack = " ".join([keyword_group, str(title.get("title") or ""), str(title.get("source") or "")])
                if not _topic_matches(topic, haystack):
                    continue
                score = _trend_radar_score(trend, title)
                if best is None or score > int(best["score"]):
                    best = {"score": score, "record": record, "trend": trend, "title": title}
    if best is None:
        write_diagnostic("proactive_news_skipped", reason="trend_radar_no_match", user_id=user_id, topics=[item["topic"] for item in topic_records])
        return None

    record = best["record"]
    trend = best["trend"]
    title = best["title"]
    topic = str(record["topic"])
    related_sources = _related_trend_sources(trend, limit=_trend_radar_max_titles(config))
    first_source = related_sources[0] if related_sources else {}
    headline = str(title.get("title") or "").strip()
    source_name = str(title.get("source") or "").strip()
    summary = headline if not source_name else f"{headline}（{source_name}）"
    priority = min(89, 66 + int(best["score"]) // 6)
    return create_proactive_event(
        session,
        user_id=user_id,
        character_id=character_id,
        source_type="news",
        source_id=str(title.get("url") or ""),
        title="小樱看到了一条热点",
        text=f"我刚看到和「{topic}」有关的热点：{summary}",
        priority=priority,
        dedupe_key=str(record["dedupe_key"]),
        payload={
            "topic": topic,
            "keyword_group": str(trend.get("keyword_group") or ""),
            "generated_at": str(payload.get("generated_at") or ""),
            "source": source_name,
            "ranks": title.get("ranks") or [],
            "time_info": str(title.get("time_info") or ""),
            "match_count": _as_int(trend.get("match_count")),
            "sources": related_sources,
            "summary": summary,
            "trend_radar": {
                "total_titles_processed": payload.get("total_titles_processed") or 0,
                "failed_sources": payload.get("failed_sources") or [],
                "report_image_url": payload.get("report_image_url") or "",
            },
            "first_source": first_source,
        },
        expires_at=now_local.astimezone(timezone.utc) + timedelta(hours=18),
    )


def _ark_search_candidates(session: Session, primary: ProviderConfig | None) -> list[ProviderConfig]:
    candidates: list[ProviderConfig] = []
    if primary is not None and primary.provider == "volc_ark_web_search":
        candidates.append(primary)
    for item in session.execute(
        select(ProviderConfig)
        .where(ProviderConfig.kind == "search", ProviderConfig.provider == "volc_ark_web_search")
        .order_by(ProviderConfig.enabled.desc(), ProviderConfig.updated_at.desc())
    ).scalars():
        if all(existing.provider_id != item.provider_id for existing in candidates):
            candidates.append(item)
    return candidates


def _ensure_ark_news_candidate(
    session: Session,
    *,
    primary: ProviderConfig | None,
    user_id: str,
    character_id: str,
    topic_records: list[dict[str, Any]],
    now_local: datetime,
) -> ProactiveEvent | None:
    candidates = _ark_search_candidates(session, primary)
    if not candidates:
        write_diagnostic("proactive_news_skipped", reason="ark_search_not_configured", user_id=user_id)
        return None
    last_error: Exception | None = None
    for record in reversed(topic_records):
        topic = str(record["topic"])
        for search in candidates:
            try:
                result = VolcArkWebSearchClient(search).search(
                    f"请联网搜索与「{topic}」相关的最新内容，必须返回标题、链接、发布时间。",
                    require_published_at=True,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
            sources = result.get("sources") or []
            if not sources or not sources[0].get("published_at"):
                write_diagnostic("proactive_news_skipped", reason="missing_verifiable_source", topic=topic)
                continue
            first = sources[0]
            summary = str(result.get("summary") or first.get("title") or "").strip()
            return create_proactive_event(
                session,
                user_id=user_id,
                character_id=character_id,
                source_type="news",
                source_id=str(first.get("url") or ""),
                title="小樱看到了一条新消息",
                text=f"我刚看到和「{topic}」有关的新内容：{summary}",
                priority=80,
                dedupe_key=str(record["dedupe_key"]),
                payload={"topic": topic, "sources": sources[:3], "summary": summary},
                expires_at=now_local.astimezone(timezone.utc) + timedelta(hours=18),
            )
    if last_error is not None:
        write_diagnostic(
            "proactive_news_skipped",
            reason="search_error",
            user_id=user_id,
            error_type=type(last_error).__name__,
            message=str(last_error),
        )
    return None


def ensure_news_candidate(session: Session, *, user_id: str, character_id: str, local_time: datetime | None = None) -> ProactiveEvent | None:
    user = session.get(User, user_id)
    if user is None or not user.news_enabled:
        return None
    topics = [str(item).strip() for item in load_json(user.interest_topics_json, []) if str(item).strip()]
    if not topics:
        return None
    now_local = _local_now(user, local_time)
    topic_records, existing_events = _news_topic_records(session, user_id=user_id, topics=topics, now_local=now_local)
    if not topic_records:
        return existing_events[-1] if existing_events else None
    search = get_enabled_provider(session, "search")
    if search is None:
        write_diagnostic("proactive_news_skipped", reason="search_not_configured", user_id=user_id)
        return _ensure_ark_news_candidate(session, primary=None, user_id=user_id, character_id=character_id, topic_records=topic_records, now_local=now_local)
    if search.provider == "trend_radar":
        event = _ensure_trend_radar_news_candidate(
            session,
            config=search,
            user_id=user_id,
            character_id=character_id,
            topic_records=topic_records,
            now_local=now_local,
        )
        if event is not None:
            return event
        return _ensure_ark_news_candidate(session, primary=search, user_id=user_id, character_id=character_id, topic_records=topic_records, now_local=now_local)
    if search.provider == "volc_ark_web_search":
        return _ensure_ark_news_candidate(session, primary=search, user_id=user_id, character_id=character_id, topic_records=topic_records, now_local=now_local)
    write_diagnostic("proactive_news_skipped", reason="search_not_ready", provider_id=search.provider_id, provider=search.provider)
    return _ensure_ark_news_candidate(session, primary=search, user_id=user_id, character_id=character_id, topic_records=topic_records, now_local=now_local)


def _due_pending_events(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    now_utc: datetime,
    limit: int = PROACTIVE_JUDGE_CANDIDATE_LIMIT,
) -> list[ProactiveEvent]:
    events = session.execute(
        select(ProactiveEvent)
        .where(
            ProactiveEvent.user_id == user_id,
            ProactiveEvent.character_id == character_id,
            ProactiveEvent.status == "pending",
        )
        .order_by(ProactiveEvent.priority.desc(), ProactiveEvent.created_at)
    ).scalars().all()
    due: list[ProactiveEvent] = []
    for event in events:
        scheduled_at = _parse_iso(event.scheduled_at) or now_utc
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        if scheduled_at <= now_utc:
            due.append(event)
            if len(due) >= limit:
                break
    return due


def _next_check_at(user: User) -> datetime | None:
    value = _parse_iso(user.proactive_next_check_at)
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _next_check_pending(user: User, now_utc: datetime) -> bool:
    next_check_at = _next_check_at(user)
    return next_check_at is not None and next_check_at > now_utc


def _event_minutes_since(value: str, now_utc: datetime) -> int | None:
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((now_utc - parsed.astimezone(timezone.utc)).total_seconds() // 60))


def _judge_text(value: Any, limit: int = JUDGE_TEXT_LIMIT) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _payload_for_judge(payload: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("topic", "summary", "activity_title", "memory", "interaction_type", "content", "trigger_key", "severity"):
        if key in payload:
            compact[key] = _judge_text(payload.get(key))
    sources = payload.get("sources")
    if isinstance(sources, list) and sources:
        first = sources[0]
        if isinstance(first, dict):
            compact["first_source"] = {
                "title": _judge_text(first.get("title")),
                "source": _judge_text(first.get("source"), 60),
                "time_info": _judge_text(first.get("time_info"), 60),
            }
    return compact


def _event_for_judge(event: ProactiveEvent, now_utc: datetime) -> dict[str, Any]:
    payload = load_json(event.payload_json, {})
    if not isinstance(payload, dict):
        payload = {}
    return {
        "proactive_event_id": event.proactive_event_id,
        "source_type": event.source_type,
        "source_id": event.source_id,
        "title": _judge_text(event.title),
        "text": _judge_text(event.text, 220),
        "priority": event.priority,
        "scheduled_at": event.scheduled_at,
        "expires_at": event.expires_at,
        "created_at": event.created_at,
        "age_minutes": _event_minutes_since(event.created_at, now_utc),
        "payload": _payload_for_judge(payload),
    }


def _judge_sleep_context(user: User, now_local: datetime) -> dict[str, Any]:
    return {
        "sleep_start": user.sleep_start,
        "sleep_end": user.sleep_end,
        "is_sleep_time": _is_sleep_time(user, now_local),
    }


def _proactive_judge_context(
    session: Session,
    *,
    user: User,
    character: Character | None,
    events: list[ProactiveEvent],
    now_local: datetime,
    now_utc: datetime,
) -> dict[str, Any]:
    latest = _latest_delivery(session, user)
    previous = load_json(user.proactive_judgement_json, {})
    if not isinstance(previous, dict):
        previous = {}
    return {
        "current_local_time": now_local.isoformat(),
        "current_utc_time": now_utc.isoformat(),
        "user": {
            "user_id": user.user_id,
            "display_name": user.display_name,
            "timezone": user.timezone,
            "interest_topics": load_json(user.interest_topics_json, []),
            "proactive_daily_limit": user.proactive_daily_limit,
            "notifications_enabled": user.notifications_enabled,
            "story_completed": user.story_completed,
        },
        "character": {
            "character_id": character.character_id if character is not None else "",
            "name": character.name if character is not None else "小樱",
        },
        "delivery_history": {
            "today_delivered_count": _daily_delivery_count(session, user, now_local),
            "latest_delivery_at": latest.isoformat() if latest is not None else "",
        },
        "sleep_window": _judge_sleep_context(user, now_local),
        "previous_judgement": {
            "status": previous.get("status") or "",
            "should_send": previous.get("should_send"),
            "selected_event_id": previous.get("selected_event_id") or "",
            "reason": _judge_text(previous.get("reason")),
            "next_check_at": previous.get("next_check_at") or "",
        },
        "candidates": [_event_for_judge(event, now_utc) for event in events],
    }


def _judge_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是主动消息投递判断器。你只输出一个 JSON 对象，不要输出 Markdown、解释或多余文本。"
                "你要在减少打扰和不错过重要陪伴之间做判断。"
            ),
        },
        {
            "role": "user",
            "content": (
                "根据下面上下文判断现在是否应该向客户端投递一条主动消息。\n"
                "规则：\n"
                "1. 只能从 candidates 里选择一个 proactive_event_id，不能编造 ID。\n"
                "2. 睡眠窗口、今日投递数、最近投递时间只是节奏上下文，由你判断是否要暂缓。\n"
                "3. 如果暂缓或不适合打扰，should_send=false，selected_event_id 为空字符串。\n"
                "4. next_check_after_minutes 表示多久后再重新判断，请给 1 到 1440 的整数。\n"
                "5. 不要展开推理，直接输出最终 JSON。\n"
                "6. 输出固定 JSON，例如：{\"should_send\":false,\"selected_event_id\":\"\",\"reason\":\"今日节奏偏满，稍后再判断。\",\"next_check_after_minutes\":60}。\n\n"
                f"上下文 JSON：\n{json.dumps(context, ensure_ascii=False, indent=2)}"
            ),
        },
    ]


def _judge_next_check_minutes(value: Any) -> int:
    try:
        minutes = int(float(value))
    except (TypeError, ValueError):
        minutes = DEFAULT_JUDGE_NEXT_CHECK_MINUTES
    return max(1, min(minutes, MAX_JUDGE_NEXT_CHECK_MINUTES))


def _store_judgement(
    user: User,
    *,
    now_utc: datetime,
    status: str,
    candidate_ids: list[str],
    should_send: bool,
    selected_event_id: str,
    reason: str,
    next_check_after_minutes: int,
    raw: dict[str, Any] | None = None,
    error_type: str = "",
) -> None:
    next_check_at = now_utc + timedelta(minutes=next_check_after_minutes)
    user.proactive_next_check_at = _utc_iso(next_check_at)
    user.proactive_judgement_json = dump_json(
        {
            "status": status,
            "should_send": should_send,
            "selected_event_id": selected_event_id,
            "reason": reason,
            "next_check_after_minutes": next_check_after_minutes,
            "next_check_at": user.proactive_next_check_at,
            "candidate_ids": candidate_ids,
            "decided_at": _utc_iso(now_utc),
            "error_type": error_type,
            "raw": raw or {},
        }
    )


def _judge_proactive_delivery(
    session: Session,
    *,
    user: User,
    character: Character | None,
    events: list[ProactiveEvent],
    now_local: datetime,
    now_utc: datetime,
) -> ProactiveEvent | None:
    candidate_ids = [event.proactive_event_id for event in events]
    write_diagnostic(
        "proactive_judge_started",
        user_id=user.user_id,
        character_id=character.character_id if character is not None else "",
        candidate_count=len(events),
        candidate_ids=candidate_ids,
        local_time=now_local.isoformat(),
    )
    config = get_task_llm_provider(session)
    if config is None:
        reason = "llm_not_configured"
        _store_judgement(
            user,
            now_utc=now_utc,
            status="error",
            candidate_ids=candidate_ids,
            should_send=False,
            selected_event_id="",
            reason=reason,
            next_check_after_minutes=DEFAULT_JUDGE_NEXT_CHECK_MINUTES,
            error_type="missing_provider",
        )
        write_diagnostic("proactive_judge_error", user_id=user.user_id, reason=reason)
        return None

    context = _proactive_judge_context(session, user=user, character=character, events=events, now_local=now_local, now_utc=now_utc)
    try:
        result = OpenAICompatibleClient(config).chat_json(
            _judge_messages(context),
            max_tokens=1200,
            temperature=0.1,
            diagnostic={
                "feature": "主动消息判断器",
                "stage": "judge",
                "purpose": "Judge proactive delivery",
                "input": context,
            },
        )
        if not isinstance(result, dict):
            raise ProviderError("proactive judge did not return a JSON object")
    except Exception as exc:  # noqa: BLE001
        reason = str(exc)
        _store_judgement(
            user,
            now_utc=now_utc,
            status="error",
            candidate_ids=candidate_ids,
            should_send=False,
            selected_event_id="",
            reason=reason,
            next_check_after_minutes=DEFAULT_JUDGE_NEXT_CHECK_MINUTES,
            error_type=type(exc).__name__,
        )
        write_diagnostic("proactive_judge_error", user_id=user.user_id, error_type=type(exc).__name__, message=reason)
        return None

    should_send = bool(result.get("should_send"))
    selected_event_id = str(result.get("selected_event_id") or "").strip()
    reason = str(result.get("reason") or "").strip()
    next_check_after_minutes = _judge_next_check_minutes(result.get("next_check_after_minutes"))
    selected = {event.proactive_event_id: event for event in events}.get(selected_event_id)
    if should_send and selected is None:
        error_reason = f"invalid selected_event_id: {selected_event_id}"
        _store_judgement(
            user,
            now_utc=now_utc,
            status="error",
            candidate_ids=candidate_ids,
            should_send=False,
            selected_event_id=selected_event_id,
            reason=error_reason,
            next_check_after_minutes=next_check_after_minutes,
            raw=result,
            error_type="invalid_selection",
        )
        write_diagnostic("proactive_judge_error", user_id=user.user_id, reason="invalid_selection", selected_event_id=selected_event_id, candidate_ids=candidate_ids)
        return None

    if not should_send:
        selected_event_id = ""

    _store_judgement(
        user,
        now_utc=now_utc,
        status="decided",
        candidate_ids=candidate_ids,
        should_send=should_send,
        selected_event_id=selected_event_id,
        reason=reason,
        next_check_after_minutes=next_check_after_minutes,
        raw=result,
    )
    write_diagnostic(
        "proactive_judge_decided",
        user_id=user.user_id,
        should_send=should_send,
        selected_event_id=selected_event_id,
        reason=reason,
        next_check_after_minutes=next_check_after_minutes,
    )
    return selected if should_send else None


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


def _character_chibi_url(character: Character | None) -> str:
    assets = load_json(character.chibi_widget_assets_json if character is not None else "{}", {})
    if not isinstance(assets, dict):
        return ""
    for key in ("happy", "default", "study", "miss", "sleep"):
        value = str(assets.get(key) or "").strip()
        if value.startswith("/media/") or value.startswith("http://") or value.startswith("https://"):
            return value
        if value and not value.startswith("asset://"):
            return f"/media/{value}"
    return ""


def proactive_widget_payload(event: ProactiveEvent | None, character: Character | None = None) -> dict[str, Any]:
    chibi_url = _character_chibi_url(character)
    if event is None:
        return {
            "character_name": character.name if character is not None else "小樱",
            "status": "想聊天",
            "bubble": "今天也想听你说说话。",
            "unread_count": 0,
            "proactive_event_id": "",
            "chibi_url": chibi_url,
        }
    return {
        "character_name": character.name if character is not None else "小樱",
        "status": "有话想说",
        "bubble": event.text[:80],
        "unread_count": 1,
        "proactive_event_id": event.proactive_event_id,
        "chibi_url": chibi_url,
    }


def pending_proactive_response(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    local_time: datetime | None = None,
    generate_news: bool = True,
    generate_weather: bool = True,
) -> dict[str, Any]:
    user = session.get(User, user_id)
    character = session.get(Character, character_id)
    if user is None:
        return {"ok": True, "event": None, "widget": proactive_widget_payload(None, character)}
    now_local = _local_now(user, local_time)
    now_utc = now_local.astimezone(timezone.utc)
    if not user.story_completed:
        write_diagnostic("proactive_judge_skipped", user_id=user_id, character_id=character_id, reason="story_not_ready")
        session.commit()
        return {"ok": True, "event": None, "widget": proactive_widget_payload(None, character)}
    if not user.notifications_enabled:
        write_diagnostic("proactive_judge_skipped", user_id=user_id, character_id=character_id, reason="notifications_disabled")
        session.commit()
        return {"ok": True, "event": None, "widget": proactive_widget_payload(None, character)}
    if generate_news:
        ensure_news_candidate(session, user_id=user_id, character_id=character_id, local_time=local_time)
    if generate_weather:
        ensure_weather_candidate(session, user_id=user_id, character_id=character_id, local_time=local_time)
    _expire_old_pending(session, user_id, now_utc)
    if _next_check_pending(user, now_utc):
        write_diagnostic(
            "proactive_judge_skipped",
            user_id=user_id,
            character_id=character_id,
            reason="next_check_pending",
            next_check_at=user.proactive_next_check_at,
        )
        session.commit()
        return {"ok": True, "event": None, "widget": proactive_widget_payload(None, character)}
    events = _due_pending_events(session, user_id=user_id, character_id=character_id, now_utc=now_utc)
    if not events:
        write_diagnostic("proactive_judge_skipped", user_id=user_id, character_id=character_id, reason="no_due_event")
        session.commit()
        return {"ok": True, "event": None, "widget": proactive_widget_payload(None, character)}
    event = _judge_proactive_delivery(session, user=user, character=character, events=events, now_local=now_local, now_utc=now_utc)
    session.commit()
    return {"ok": True, "event": proactive_event_payload(event), "widget": proactive_widget_payload(event, character)}


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


def consume_proactive_event(session: Session, event_id: str) -> ProactiveEvent | None:
    event = session.get(ProactiveEvent, event_id)
    if event is None:
        return None
    now = utc_now()
    event.status = "reflected"
    event.opened_at = event.opened_at or now
    event.reflected_at = event.reflected_at or now
    event.updated_at = now
    session.commit()
    write_diagnostic("proactive_consumed", proactive_event_id=event.proactive_event_id, source_type=event.source_type)
    return event


def mark_proactive_reflected(session: Session, event: ProactiveEvent) -> None:
    event.status = "reflected"
    event.reflected_at = event.reflected_at or utc_now()
    event.updated_at = utc_now()
    write_diagnostic("proactive_reflected", proactive_event_id=event.proactive_event_id, source_type=event.source_type)
