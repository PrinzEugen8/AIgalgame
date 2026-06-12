from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .character_profiles import resolve_character_profile
from .diagnostics import write_diagnostic
from .models import Character, ContentItem, Moment, ProactiveEvent, ProviderConfig, User
from .news import trend_radar_payload_for_news
from .persona import normalize_persona_card
from .providers import ProviderError, VolcArkWebSearchClient, get_enabled_provider, get_enabled_provider_by_provider, provider_ready
from .utils import dump_json, load_json, stable_hash, uid, utc_now


DEFAULT_INFORMATION_PROFILE: dict[str, Any] = {
    "archetype": "otaku",
    "platforms": [
        {"id": "bilibili", "label": "B站", "weight": 0.58, "topics": ["动画", "游戏", "二次元", "Galgame", "机器人少女"]},
        {"id": "weibo", "label": "微博", "weight": 0.22, "topics": ["热搜", "校园", "天气", "节日"]},
        {"id": "web", "label": "网页搜索", "weight": 0.2, "topics": ["AI", "游戏", "生活方式"]},
    ],
    "personal_topics": ["ATRI", "机器人少女", "海边小镇", "动画", "Galgame", "AI 伴侣"],
    "avoid_topics": ["血腥暴力", "低俗擦边", "未经证实的隐私"],
}
DEFAULT_REPOST_PROFILE: dict[str, Any] = {
    "daily_limit": 2,
    "min_interest_score": 45,
    "templates": [
        "我刚刷到这个，感觉你可能会喜欢。",
        "这个有点有趣，我忍不住想转给你。",
        "我看到这个的时候，第一反应是想问问你。",
    ],
}
PLATFORM_HINTS: dict[str, tuple[str, ...]] = {
    "bilibili": ("bilibili.com", "b23.tv", "B站", "哔哩", "bilibili"),
    "weibo": ("weibo.com", "微博", "weibo"),
}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _local_now(user: User | None, local_time: datetime | None = None) -> datetime:
    if local_time is not None:
        return local_time
    return datetime.now(timezone.utc)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _character_card(character: Character | None) -> dict[str, Any]:
    if character is None:
        return normalize_persona_card({})
    return normalize_persona_card(load_json(character.persona_card_json, {}), name=character.name)


def information_profile_for_character(
    character: Character | None,
    *,
    session: Session | None = None,
    user_id: str = "",
    character_id: str = "",
) -> dict[str, Any]:
    if session is not None and user_id and character_id:
        card = resolve_character_profile(session, user_id=user_id, character_id=character_id, character=character).card
    else:
        card = _character_card(character)
    profile = _as_dict(card.get("information_profile"))
    return profile or DEFAULT_INFORMATION_PROFILE


def repost_profile_for_character(
    character: Character | None,
    *,
    session: Session | None = None,
    user_id: str = "",
    character_id: str = "",
) -> dict[str, Any]:
    if session is not None and user_id and character_id:
        card = resolve_character_profile(session, user_id=user_id, character_id=character_id, character=character).card
    else:
        card = _character_card(character)
    profile = _as_dict(card.get("repost_profile"))
    return profile or DEFAULT_REPOST_PROFILE


def _platform_records(profile: dict[str, Any]) -> list[dict[str, Any]]:
    records = [item for item in _as_list(profile.get("platforms")) if isinstance(item, dict)]
    return records or DEFAULT_INFORMATION_PROFILE["platforms"]


def _platform_weight(profile: dict[str, Any], platform: str) -> float:
    for record in _platform_records(profile):
        if str(record.get("id") or "") == platform:
            try:
                return max(0.0, float(record.get("weight", 0.2)))
            except (TypeError, ValueError):
                return 0.2
    return 0.2 if platform else 0.1


def _platform_topics(profile: dict[str, Any], platform: str) -> list[str]:
    topics: list[str] = []
    for record in _platform_records(profile):
        if str(record.get("id") or "") == platform:
            topics.extend(str(item).strip() for item in _as_list(record.get("topics")) if str(item).strip())
    topics.extend(str(item).strip() for item in _as_list(profile.get("personal_topics")) if str(item).strip())
    seen: set[str] = set()
    result: list[str] = []
    for topic in topics:
        if topic.casefold() in seen:
            continue
        seen.add(topic.casefold())
        result.append(topic)
    return result


def _infer_platform(profile: dict[str, Any], *, source_name: str, url: str) -> str:
    haystack = f"{source_name} {url}".casefold()
    for platform, hints in PLATFORM_HINTS.items():
        if any(hint.casefold() in haystack for hint in hints):
            return platform
    records = sorted(_platform_records(profile), key=lambda item: -float(item.get("weight") or 0))
    return str(records[0].get("id") or "web") if records else "web"


def content_dedupe_key(platform: str, url: str, title: str) -> str:
    normalized_url = " ".join(str(url or "").split()).lower()
    normalized_title = " ".join(str(title or "").split()).casefold()
    return stable_hash(str(platform or "web"), normalized_url or normalized_title)


def _topic_hits(profile: dict[str, Any], platform: str, title: str, summary: str, tags: list[str]) -> int:
    haystack = " ".join([title, summary, " ".join(tags)]).casefold()
    if not haystack:
        return 0
    hits = 0
    for topic in _platform_topics(profile, platform):
        normalized = topic.casefold()
        if normalized and normalized in haystack:
            hits += 1
    return hits


def _interest_score(profile: dict[str, Any], *, platform: str, title: str, summary: str, tags: list[str], hot_score: int) -> int:
    score = int(_platform_weight(profile, platform) * 42)
    score += min(32, _topic_hits(profile, platform, title, summary, tags) * 8)
    score += min(24, max(0, hot_score) // 4)
    avoided = [str(item).casefold() for item in _as_list(profile.get("avoid_topics")) if str(item).strip()]
    haystack = f"{title} {summary}".casefold()
    if any(item and item in haystack for item in avoided):
        score -= 45
    return max(0, min(100, score))


def upsert_content_item(
    session: Session,
    *,
    provider_id: str,
    platform: str,
    source_name: str,
    title: str,
    url: str = "",
    summary: str = "",
    tags: list[str] | None = None,
    published_at: str = "",
    hot_score: int = 0,
    interest_score: int = 0,
    payload: dict[str, Any] | None = None,
) -> ContentItem:
    title = " ".join(str(title or "").split())[:240]
    summary = " ".join(str(summary or "").split())[:500]
    tags = [str(item).strip() for item in (tags or []) if str(item).strip()]
    platform = str(platform or "web").strip() or "web"
    dedupe_key = content_dedupe_key(platform, url, title)
    matches = session.execute(
        select(ContentItem)
        .where(ContentItem.dedupe_key == dedupe_key)
        .order_by(ContentItem.fetched_at.desc(), ContentItem.updated_at.desc(), ContentItem.created_at.desc())
    ).scalars().all()
    existing = matches[0] if matches else None
    if len(matches) > 1:
        write_diagnostic("content_item_duplicate_dedupe_key", dedupe_key=dedupe_key, kept_item_id=existing.item_id, duplicate_count=len(matches))
    item = existing or ContentItem(item_id=uid("content"), dedupe_key=dedupe_key)
    item.provider_id = provider_id
    item.platform = platform
    item.source_name = source_name[:120]
    item.title = title
    item.url = str(url or "")
    item.summary = summary
    item.tags_json = dump_json(tags)
    item.published_at = str(published_at or "")
    item.hot_score = max(0, min(100, _as_int(hot_score)))
    item.interest_score = max(0, min(100, _as_int(interest_score)))
    item.payload_json = dump_json(payload or {})
    item.fetched_at = utc_now()
    item.updated_at = utc_now()
    if existing is None:
        session.add(item)
        session.flush()
    return item


def _trend_hot_score(trend: dict[str, Any], title: dict[str, Any]) -> int:
    ranks = [item for item in (title.get("ranks") or []) if isinstance(item, int)]
    rank_score = max(0, 48 - min(ranks or [99]) * 3)
    return max(0, min(100, rank_score + (28 if title.get("is_new") else 0) + min(_as_int(trend.get("match_count")), 24)))


def _items_from_trend_radar(
    session: Session,
    *,
    config: ProviderConfig,
    profile: dict[str, Any],
    local_time: datetime,
) -> list[ContentItem]:
    if not provider_ready(config):
        return []
    try:
        payload = trend_radar_payload_for_news(session, config=config, local_time=local_time)
    except Exception as exc:  # noqa: BLE001
        write_diagnostic("information_circle_skipped", reason="trend_radar_error", error_type=type(exc).__name__, message=str(exc))
        return []
    if not payload:
        return []
    items: list[ContentItem] = []
    for trend in payload.get("trends") or []:
        if not isinstance(trend, dict):
            continue
        tags = [str(trend.get("keyword_group") or "")]
        for title in trend.get("titles") or []:
            if not isinstance(title, dict):
                continue
            source_name = str(title.get("source") or "")
            title_text = str(title.get("title") or "")
            url = str(title.get("url") or "")
            platform = _infer_platform(profile, source_name=source_name, url=url)
            hot_score = _trend_hot_score(trend, title)
            interest_score = _interest_score(profile, platform=platform, title=title_text, summary=source_name, tags=tags, hot_score=hot_score)
            items.append(
                upsert_content_item(
                    session,
                    provider_id=config.provider_id,
                    platform=platform,
                    source_name=source_name,
                    title=title_text,
                    url=url,
                    summary=title_text,
                    tags=tags,
                    hot_score=hot_score,
                    interest_score=interest_score,
                    payload={"trend": trend.get("keyword_group"), "raw": title},
                )
            )
    return items


def _items_from_ark_search(
    session: Session,
    *,
    config: ProviderConfig,
    profile: dict[str, Any],
) -> list[ContentItem]:
    if not provider_ready(config):
        return []
    platforms = sorted(_platform_records(profile), key=lambda item: -float(item.get("weight") or 0))[:2]
    items: list[ContentItem] = []
    for platform_record in platforms:
        platform = str(platform_record.get("id") or "web")
        topics = _platform_topics(profile, platform)[:4]
        site_hint = "site:bilibili.com " if platform == "bilibili" else "site:weibo.com " if platform == "weibo" else ""
        query = f"请搜索{site_hint}{' '.join(topics)} 的最新有趣内容，返回标题、链接、发布时间和简短摘要。"
        try:
            result = VolcArkWebSearchClient(config).search(query, require_published_at=False)
        except (ProviderError, Exception) as exc:  # noqa: BLE001
            write_diagnostic("information_circle_skipped", reason="ark_search_error", provider_id=config.provider_id, platform=platform, error_type=type(exc).__name__, message=str(exc))
            continue
        summary = str(result.get("summary") or "")
        for source in (result.get("sources") or [])[:5]:
            if not isinstance(source, dict):
                continue
            source_name = str(source.get("source") or source.get("site_name") or "")
            title = str(source.get("title") or "")
            url = str(source.get("url") or "")
            actual_platform = _infer_platform(profile, source_name=source_name, url=url) if platform == "web" else platform
            hot_score = 45
            interest_score = _interest_score(profile, platform=actual_platform, title=title, summary=summary, tags=topics, hot_score=hot_score)
            items.append(
                upsert_content_item(
                    session,
                    provider_id=config.provider_id,
                    platform=actual_platform,
                    source_name=source_name,
                    title=title,
                    url=url,
                    summary=summary,
                    tags=topics,
                    published_at=str(source.get("published_at") or ""),
                    hot_score=hot_score,
                    interest_score=interest_score,
                    payload={"search_summary": summary, "raw": source},
                )
            )
    return items


def fetch_information_items(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    local_time: datetime | None = None,
) -> list[ContentItem]:
    user = session.get(User, user_id)
    character = session.get(Character, character_id)
    profile = information_profile_for_character(character, session=session, user_id=user_id, character_id=character_id)
    now_local = _local_now(user, local_time)
    items: list[ContentItem] = []
    trend = get_enabled_provider_by_provider(session, "search", "trend_radar")
    if trend is not None:
        items.extend(_items_from_trend_radar(session, config=trend, profile=profile, local_time=now_local))
    ark = get_enabled_provider_by_provider(session, "search", "volc_ark_web_search")
    if ark is None and trend is None:
        ark = get_enabled_provider(session, "search")
    if ark is not None and ark.provider == "volc_ark_web_search":
        items.extend(_items_from_ark_search(session, config=ark, profile=profile))
    if items:
        session.commit()
    return sorted(items, key=lambda item: (-int(item.interest_score or 0), -int(item.hot_score or 0), item.title))


def _event_local_date(event: ProactiveEvent, fallback: datetime) -> str:
    parsed = _parse_iso(event.created_at)
    if parsed is None:
        return fallback.date().isoformat()
    if fallback.tzinfo is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(fallback.tzinfo)
    return parsed.date().isoformat()


def _repost_count_today(session: Session, *, user_id: str, character_id: str, now_local: datetime) -> int:
    rows = session.execute(
        select(ProactiveEvent).where(
            ProactiveEvent.user_id == user_id,
            ProactiveEvent.character_id == character_id,
            ProactiveEvent.source_type == "repost",
            ProactiveEvent.status != "dismissed",
        )
    ).scalars().all()
    today = now_local.date().isoformat()
    return sum(1 for event in rows if _event_local_date(event, now_local) == today)


def _existing_repost_dedupe_keys(session: Session, *, user_id: str) -> set[str]:
    rows = session.execute(
        select(ProactiveEvent).where(ProactiveEvent.user_id == user_id, ProactiveEvent.source_type == "repost")
    ).scalars().all()
    return {event.dedupe_key for event in rows if event.dedupe_key}


def select_repost_item(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    local_time: datetime | None = None,
) -> ContentItem | None:
    character = session.get(Character, character_id)
    repost_profile = repost_profile_for_character(character, session=session, user_id=user_id, character_id=character_id)
    min_score = max(0, min(100, _as_int(repost_profile.get("min_interest_score"), 45)))
    existing_keys = _existing_repost_dedupe_keys(session, user_id=user_id)
    candidates = session.execute(
        select(ContentItem)
        .where(ContentItem.interest_score >= min_score)
        .order_by(ContentItem.interest_score.desc(), ContentItem.hot_score.desc(), ContentItem.fetched_at.desc())
        .limit(40)
    ).scalars().all()
    for item in candidates:
        if f"repost:{user_id}:{item.dedupe_key}" not in existing_keys:
            return item
    fetched = fetch_information_items(session, user_id=user_id, character_id=character_id, local_time=local_time)
    for item in fetched:
        if item.interest_score >= min_score and f"repost:{user_id}:{item.dedupe_key}" not in existing_keys:
            return item
    return None


def _repost_text(profile: dict[str, Any], item: ContentItem, *, local_time: datetime) -> str:
    templates = [str(template).strip() for template in _as_list(profile.get("templates")) if str(template).strip()]
    if not templates:
        templates = DEFAULT_REPOST_PROFILE["templates"]
    index = int(stable_hash(item.item_id, local_time.date().isoformat())[:4], 16) % len(templates)
    intro = templates[index]
    source = f"（{item.source_name}）" if item.source_name else ""
    title = item.title or item.summary or "一条内容"
    return f"{intro} {title}{source}"


def create_repost_moment_and_event(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    item: ContentItem,
    local_time: datetime | None = None,
) -> ProactiveEvent | None:
    from .proactive import create_proactive_event

    user = session.get(User, user_id)
    character = session.get(Character, character_id)
    now_local = _local_now(user, local_time)
    repost_profile = repost_profile_for_character(character, session=session, user_id=user_id, character_id=character_id)
    text = _repost_text(repost_profile, item, local_time=now_local)
    payload = {
        "content_item_id": item.item_id,
        "platform": item.platform,
        "source_name": item.source_name,
        "title": item.title,
        "url": item.url,
        "summary": item.summary,
        "interest_score": item.interest_score,
        "hot_score": item.hot_score,
        "tags": load_json(item.tags_json, []),
    }
    moment = Moment(
        moment_id=uid("moment"),
        text=text,
        moment_type="repost",
        source_platform=item.platform,
        source_title=item.title,
        source_url=item.url,
        source_summary=item.summary,
        source_payload_json=dump_json(payload),
        mood_snapshot="好奇",
    )
    session.add(moment)
    event = create_proactive_event(
        session,
        user_id=user_id,
        character_id=character_id,
        source_type="repost",
        source_id=item.item_id,
        title="亚托莉转发了一条内容",
        text=f"我刚转发了一条觉得有趣的内容：{item.title or item.summary}",
        priority=max(54, min(86, int(item.interest_score or 0))),
        dedupe_key=f"repost:{user_id}:{item.dedupe_key}",
        payload={**payload, "moment_id": moment.moment_id},
        expires_at=now_local.astimezone(timezone.utc) + timedelta(hours=12) if now_local.tzinfo else datetime.now(timezone.utc) + timedelta(hours=12),
    )
    write_diagnostic("repost_created", moment_id=moment.moment_id, content_item_id=item.item_id, proactive_event_id=event.proactive_event_id if event else "")
    session.commit()
    return event


def ensure_repost_candidate(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    local_time: datetime | None = None,
) -> ProactiveEvent | None:
    user = session.get(User, user_id)
    if user is not None and not user.news_enabled:
        return None
    character = session.get(Character, character_id)
    now_local = _local_now(user, local_time)
    repost_profile = repost_profile_for_character(character, session=session, user_id=user_id, character_id=character_id)
    daily_limit = max(0, min(5, _as_int(repost_profile.get("daily_limit"), 2)))
    if daily_limit <= 0 or _repost_count_today(session, user_id=user_id, character_id=character_id, now_local=now_local) >= daily_limit:
        return None
    item = select_repost_item(session, user_id=user_id, character_id=character_id, local_time=now_local)
    if item is None:
        write_diagnostic("repost_skipped", reason="no_content_item", user_id=user_id, character_id=character_id)
        return None
    return create_repost_moment_and_event(session, user_id=user_id, character_id=character_id, item=item, local_time=now_local)
