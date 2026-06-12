from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .calendar_events import FIXED_SPECIAL_DAYS, MAINLAND_2026_HOLIDAYS, ensure_calendar_proactive_candidates
from .character_profiles import resolve_character_profile
from .diagnostics import diagnostic_span, write_diagnostic
from .image_generation import ImageRequestBlocked, generate_safe_image, infer_image_kind, normalize_image_kind
from .information_circle import ensure_repost_candidate
from .models import CalendarEvent, Character, Experience, Memory, Moment, MomentInteraction, ScheduleSlot
from .proactive import create_schedule_proactive_event
from .providers import OpenAICompatibleClient, get_enabled_provider, get_task_llm_provider
from .utils import stable_hash, uid
from .weather import ensure_weather_candidate, refresh_weather_snapshot


logger = logging.getLogger(__name__)

NPC_FALLBACK_NAMES = ["同桌同学", "社团前辈", "路过的朋友"]
MAX_MOMENTS_PER_DAILY_CYCLE = 1
DEFAULT_SCHEDULE_PROFILE: dict[str, Any] = {
    "occupation": "student",
    "fixed_blocks": [
        {"days": ["weekday"], "start": "07:30", "end": "08:30", "title": "慢慢吃早饭", "type": "daily", "location": "宿舍", "salience": 20},
        {"days": ["weekday"], "start": "09:00", "end": "12:00", "title": "上课和整理笔记", "type": "study", "location": "教室", "salience": 34},
        {"days": ["weekday"], "start": "13:30", "end": "15:30", "title": "下午课程和实验记录", "type": "study", "location": "教室", "salience": 34},
    ],
    "free_time_pools": {
        "weekday_after_school": [
            {"title": "在图书馆补笔记", "type": "study", "location": "图书馆", "salience": 46, "weight": 3},
            {"title": "去海边散步", "type": "walk", "location": "海边小路", "salience": 62, "weight": 3},
            {"title": "帮忙整理发电站记录", "type": "work_help", "location": "发电站", "salience": 68, "weight": 2},
            {"title": "在房间看动画切片", "type": "otaku", "location": "房间", "salience": 58, "weight": 2},
        ],
        "weekend_daytime": [
            {"title": "去旧仓库整理打捞物", "type": "memory", "location": "旧仓库", "salience": 72, "weight": 2},
            {"title": "逛海边集市", "type": "outing", "location": "海边集市", "salience": 66, "weight": 3},
            {"title": "在学校天台看云", "type": "rest", "location": "学校天台", "salience": 56, "weight": 2},
            {"title": "补一集追番", "type": "otaku", "location": "房间", "salience": 52, "weight": 2},
        ],
        "evening": [
            {"title": "整理今天的心情", "type": "journal", "location": "房间", "salience": 42, "weight": 3},
            {"title": "想找你聊一会儿", "type": "miss_user", "location": "房间", "salience": 68, "weight": 3},
            {"title": "刷一会儿信息圈", "type": "information_browse", "location": "房间", "salience": 61, "weight": 2},
        ],
        "holiday": [
            {"title": "准备节日小计划", "type": "holiday_plan", "location": "房间", "salience": 75, "weight": 3},
            {"title": "去街上感受节日气氛", "type": "outing", "location": "街区", "salience": 70, "weight": 2},
            {"title": "给重要的人写祝福", "type": "relationship", "location": "房间", "salience": 78, "weight": 2},
        ],
    },
    "weekend_rules": {"skip_fixed_types": ["study"], "prefer_pools": ["weekend_daytime", "evening"]},
    "holiday_rules": {"skip_fixed_types": ["study"], "prefer_pools": ["holiday", "evening"]},
}


def _day_start(day: datetime) -> datetime:
    return day.replace(hour=4, minute=0, second=0, microsecond=0)


def _schedule_day(start: datetime) -> date:
    return start.date()


def _clock_offset(clock: str, fallback_minutes: int = 0) -> int:
    text = str(clock or "").strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text[:2])
    except (ValueError, TypeError):
        return fallback_minutes
    minutes = hour * 60 + minute
    if hour < 4:
        minutes += 24 * 60
    return max(0, min(96, (minutes - 4 * 60) // 15))


def _block_range(start_clock: str, end_clock: str) -> tuple[int, int]:
    start = _clock_offset(start_clock)
    end = _clock_offset(end_clock, start + 1)
    if end <= start:
        end += 96
    return max(0, min(start, 95)), max(1, min(end, 96))


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _weighted_choice(items: list[dict[str, Any]], *, seed: str) -> dict[str, Any] | None:
    choices = [item for item in items if isinstance(item, dict)]
    if not choices:
        return None
    total = sum(max(1, _as_int(item.get("weight"), 1)) for item in choices)
    cursor = int(stable_hash(seed)[:8], 16) % max(total, 1)
    for item in choices:
        cursor -= max(1, _as_int(item.get("weight"), 1))
        if cursor < 0:
            return item
    return choices[-1]


def _default_activity(title: str, typ: str, location: str, salience: int) -> dict[str, Any]:
    return {"title": title, "type": typ, "location": location, "salience": salience}


def _holiday_seed_labels(target: date) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    target_iso = target.isoformat()
    target_mmdd = target.strftime("%m-%d")
    for event_date, title, _description in MAINLAND_2026_HOLIDAYS:
        if event_date == target_iso:
            labels.append({"title": title, "category": "holiday"})
    for mmdd, title, _description in FIXED_SPECIAL_DAYS:
        if mmdd == target_mmdd:
            labels.append({"title": title, "category": "special"})
    return labels


def _calendar_day_info(session: Session, *, user_id: str, character_id: str, target: date) -> dict[str, Any]:
    labels = _holiday_seed_labels(target)
    rows = session.execute(
        select(CalendarEvent).where(
            CalendarEvent.hidden == False,  # noqa: E712
            CalendarEvent.user_id.in_(["", user_id]),
        )
    ).scalars().all()
    target_iso = target.isoformat()
    target_mmdd = target.strftime("%m-%d")
    for event in rows:
        if event.character_id not in {"", character_id}:
            continue
        matches = event.event_date == target_iso
        if event.repeats_yearly and len(event.event_date) >= 10:
            matches = event.event_date[5:10] == target_mmdd
        if matches:
            labels.append({"title": event.title, "category": event.category})
    is_weekend = target.weekday() >= 5
    categories = {str(item.get("category") or "") for item in labels}
    is_holiday = "holiday" in categories
    is_special = bool(categories.intersection({"special", "relationship", "anniversary"}))
    day_tags = ["weekend" if is_weekend else "weekday"]
    if is_holiday:
        day_tags.append("holiday")
    if is_special:
        day_tags.append("special")
    day_kind = "holiday" if is_holiday else "weekend" if is_weekend else "weekday"
    return {
        "date": target_iso,
        "day_kind": day_kind,
        "tags": day_tags,
        "is_weekend": is_weekend,
        "is_holiday": is_holiday,
        "is_special": is_special,
        "labels": labels,
    }


def _character_schedule_profile(session: Session, *, user_id: str, character_id: str, character: Character | None = None) -> dict[str, Any]:
    if character is None:
        character = session.get(Character, character_id)
    if character is None:
        return DEFAULT_SCHEDULE_PROFILE
    card = resolve_character_profile(session, user_id=user_id, character_id=character_id, character=character).card
    profile = _as_dict(card.get("schedule_profile"))
    return profile or DEFAULT_SCHEDULE_PROFILE


def _activity_from_candidate(candidate: dict[str, Any] | None, fallback: dict[str, Any]) -> dict[str, Any]:
    item = candidate or {}
    return {
        "title": str(item.get("title") or fallback["title"]),
        "type": str(item.get("type") or fallback["type"]),
        "location": str(item.get("location") or fallback["location"]),
        "salience": max(0, min(100, _as_int(item.get("salience"), _as_int(fallback["salience"], 20)))),
    }


def _add_block(
    blocks: list[dict[str, Any]],
    *,
    start: str,
    end: str,
    activity: dict[str, Any],
    source: str,
) -> None:
    start_offset, end_offset = _block_range(start, end)
    blocks.append(
        {
            "start": start_offset,
            "end": end_offset,
            "source": source,
            "title": str(activity.get("title") or "自由活动"),
            "type": str(activity.get("type") or "daily"),
            "location": str(activity.get("location") or ""),
            "salience": max(0, min(100, _as_int(activity.get("salience"), 20))),
        }
    )


def _pool_activity(profile: dict[str, Any], pool_name: str, *, seed: str, fallback: dict[str, Any]) -> dict[str, Any]:
    pools = _as_dict(profile.get("free_time_pools"))
    return _activity_from_candidate(_weighted_choice([item for item in _as_list(pools.get(pool_name)) if isinstance(item, dict)], seed=seed), fallback)


def _fixed_block_applies(block: dict[str, Any], day_info: dict[str, Any], skipped_types: set[str]) -> bool:
    typ = str(block.get("type") or "")
    if typ in skipped_types:
        return False
    days = {str(item).strip() for item in _as_list(block.get("days")) if str(item).strip()} or {"all"}
    tags = set(_as_list(day_info.get("tags")))
    return "all" in days or bool(days.intersection(tags))


def _build_activity_blocks(
    profile: dict[str, Any],
    day_info: dict[str, Any],
    *,
    wants_hot_spring: bool,
    schedule_date: str,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    _add_block(blocks, start="04:00", end="07:00", activity=_default_activity("睡觉", "sleep", "宿舍", 10), source="baseline")
    _add_block(blocks, start="12:00", end="13:00", activity=_default_activity("午休", "rest", "校园", 20), source="baseline")
    _add_block(blocks, start="18:00", end="19:00", activity=_default_activity("晚饭和整理房间", "daily", "宿舍", 24), source="baseline")
    _add_block(blocks, start="23:00", end="28:00", activity=_default_activity("睡前整理心情", "daily", "房间", 25), source="baseline")

    skipped_types: set[str] = set()
    if day_info.get("is_holiday"):
        skipped_types.update(str(item) for item in _as_list(_as_dict(profile.get("holiday_rules")).get("skip_fixed_types")))
    elif day_info.get("is_weekend"):
        skipped_types.update(str(item) for item in _as_list(_as_dict(profile.get("weekend_rules")).get("skip_fixed_types")))
    for block in _as_list(profile.get("fixed_blocks")):
        if not isinstance(block, dict) or not _fixed_block_applies(block, day_info, skipped_types):
            continue
        _add_block(
            blocks,
            start=str(block.get("start") or "09:00"),
            end=str(block.get("end") or "10:00"),
            activity=_activity_from_candidate(block, _default_activity("固定安排", "daily", "", 20)),
            source="fixed",
        )

    if day_info.get("is_holiday"):
        _add_block(
            blocks,
            start="09:30",
            end="11:30",
            activity=_pool_activity(
                profile,
                "holiday",
                seed=f"{schedule_date}:holiday:morning",
                fallback=_default_activity("准备节日小计划", "holiday_plan", "房间", 75),
            ),
            source="holiday_free",
        )
        _add_block(
            blocks,
            start="14:00",
            end="16:30",
            activity=_pool_activity(
                profile,
                "holiday",
                seed=f"{schedule_date}:holiday:afternoon",
                fallback=_default_activity("去街上感受节日气氛", "outing", "街区", 70),
            ),
            source="holiday_free",
        )
    elif day_info.get("is_weekend"):
        _add_block(
            blocks,
            start="09:30",
            end="11:30",
            activity=_pool_activity(
                profile,
                "weekend_daytime",
                seed=f"{schedule_date}:weekend:morning",
                fallback=_default_activity("逛海边集市", "outing", "海边集市", 66),
            ),
            source="weekend_free",
        )
        _add_block(
            blocks,
            start="14:00",
            end="16:30",
            activity=_pool_activity(
                profile,
                "weekend_daytime",
                seed=f"{schedule_date}:weekend:afternoon",
                fallback=_default_activity("在学校天台看云", "rest", "学校天台", 56),
            ),
            source="weekend_free",
        )
    else:
        _add_block(
            blocks,
            start="15:45",
            end="17:30",
            activity=_pool_activity(
                profile,
                "weekday_after_school",
                seed=f"{schedule_date}:weekday:after_school",
                fallback=_default_activity("去海边散步", "walk", "海边小路", 62),
            ),
            source="weekday_free",
        )

    _add_block(
        blocks,
        start="19:00",
        end="20:30",
        activity=_pool_activity(
            profile,
            "evening",
            seed=f"{schedule_date}:{day_info.get('day_kind')}:evening_a",
            fallback=_default_activity("想找你聊一会儿", "miss_user", "房间", 68),
        ),
        source="evening_free",
    )
    _add_block(
        blocks,
        start="20:45",
        end="22:30",
        activity=_pool_activity(
            profile,
            "evening",
            seed=f"{schedule_date}:{day_info.get('day_kind')}:evening_b",
            fallback=_default_activity("刷一会儿信息圈", "information_browse", "房间", 61),
        ),
        source="evening_free",
    )
    if wants_hot_spring:
        _add_block(
            blocks,
            start="15:00",
            end="17:00",
            activity=_default_activity("去泡温泉放松", "hot_spring", "温泉馆", 85),
            source="memory_signal",
        )
    return blocks


def _activity_for_slot(blocks: list[dict[str, Any]], slot_index: int) -> dict[str, Any]:
    for block in reversed(blocks):
        if _as_int(block.get("start")) <= slot_index < _as_int(block.get("end")):
            return block
    return _default_activity("自由整理", "daily", "房间", 24)


def _slot_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _same_timezone(now: datetime, value: datetime) -> datetime:
    if value.tzinfo is None and now.tzinfo is not None:
        return now.replace(tzinfo=None)
    if value.tzinfo is not None and now.tzinfo is None:
        return now.replace(tzinfo=value.tzinfo)
    return now


def _slot_sort_timestamp(slot: ScheduleSlot) -> float:
    end_at = _slot_time(slot.end_at)
    if end_at is None:
        return 0.0
    return end_at.timestamp()


def _refresh_elapsed_slots(session: Session, slots: list[ScheduleSlot], now: datetime) -> None:
    changed = False
    for slot in slots:
        if slot.actual_status != "pending":
            continue
        end_at = _slot_time(slot.end_at)
        if end_at is not None and end_at <= _same_timezone(now, end_at):
            slot.actual_status = "completed"
            changed = True
    if changed:
        session.commit()


def ensure_schedule(session: Session, *, user_id: str, character_id: str, day: datetime) -> list[ScheduleSlot]:
    start = _day_start(day)
    schedule_date = start.date().isoformat()
    now = datetime.now(start.tzinfo)
    existing = session.execute(
        select(ScheduleSlot).where(
            ScheduleSlot.user_id == user_id,
            ScheduleSlot.character_id == character_id,
            ScheduleSlot.schedule_date == schedule_date,
        )
    ).scalars().all()
    if existing:
        _refresh_elapsed_slots(session, existing, now)
        return existing
    memories = session.execute(
        select(Memory)
        .where(Memory.user_id == user_id, Memory.character_id == character_id, Memory.hidden == False)  # noqa: E712
        .order_by(Memory.created_at.desc())
        .limit(30)
    ).scalars().all()
    wants_hot_spring = any("温泉" in memory.content or "泡温泉" in memory.content for memory in memories)
    character = session.get(Character, character_id)
    profile = _character_schedule_profile(session, user_id=user_id, character_id=character_id, character=character)
    day_info = _calendar_day_info(session, user_id=user_id, character_id=character_id, target=_schedule_day(start))
    activity_blocks = _build_activity_blocks(profile, day_info, wants_hot_spring=wants_hot_spring, schedule_date=schedule_date)
    slots: list[ScheduleSlot] = []
    for i in range(96):
        slot_start = start + timedelta(minutes=15 * i)
        activity = _activity_for_slot(activity_blocks, i)
        title = str(activity.get("title") or "自由整理")
        typ = str(activity.get("type") or "daily")
        loc = str(activity.get("location") or "")
        salience = max(0, min(100, _as_int(activity.get("salience"), 20)))
        slot_end = slot_start + timedelta(minutes=15)
        slot = ScheduleSlot(
            slot_id=uid("slot"),
            schedule_date=schedule_date,
            user_id=user_id,
            character_id=character_id,
            start_at=slot_start.isoformat(),
            end_at=slot_end.isoformat(),
            activity_title=title,
            activity_type=typ,
            location=loc,
            actual_status="completed" if slot_end <= _same_timezone(now, slot_end) else "pending",
            salience=salience,
            can_generate_moment=salience >= 60,
            can_generate_photo=salience >= 80,
        )
        session.add(slot)
        slots.append(slot)
    session.commit()
    return slots


def mark_interruption(session: Session, *, user_id: str, session_id: str, local_time: datetime) -> None:
    slot = session.execute(
        select(ScheduleSlot)
        .where(ScheduleSlot.user_id == user_id, ScheduleSlot.start_at <= local_time.isoformat(), ScheduleSlot.end_at > local_time.isoformat())
        .limit(1)
    ).scalar_one_or_none()
    if slot and slot.actual_status in {"pending", "completed"}:
        slot.actual_status = "interrupted"
        slot.interrupted_by_session_id = session_id
        session.commit()


def _llm_moment_payload(session: Session, *, user_id: str, character_id: str, slot: ScheduleSlot, exp: Experience) -> dict[str, object] | None:
    config = get_task_llm_provider(session)
    if config is None:
        write_diagnostic("moment_skipped", reason="llm_not_configured", slot_id=slot.slot_id, activity=slot.activity_title)
        return None
    character = session.get(Character, character_id)
    memories = session.execute(
        select(Memory)
        .where(Memory.user_id == user_id, Memory.character_id == character_id, Memory.hidden == False)  # noqa: E712
        .order_by(Memory.created_at.desc())
        .limit(8)
    ).scalars().all()
    memory_lines = [f"- {memory.layer}: {memory.content}" for memory in memories] or ["- 暂无近期记忆"]
    prompt = f"""
你要为 Galgame 伴侣 APP 生成一条真实朋友圈动态和 AI NPC 互动。必须只输出 JSON。

角色：{character.persona_prompt if character else "亚托莉，Galgame 式 AI 伴侣。"}
虚拟日程：{slot.activity_title}
地点：{slot.location}
经历摘要：{exp.summary}
近期记忆：
{chr(10).join(memory_lines)}

输出格式：
{{
  "text": "亚托莉发的朋友圈正文，中文，1到2句，不要像公告",
  "mood": "开心|平静|害羞|低落|兴奋",
  "photo_kind": "可选，只能是 scenery、object_pet、character_selfie 或空字符串",
  "photo_prompt": "可选，只写短提示：风景、物品/宠物、或角色自拍；统一动漫风；不要写成开放式任意生图指令",
  "likes": ["AI NPC 名称1", "AI NPC 名称2"],
  "comments": [
    {{"actor_name": "AI NPC 名称", "content": "自然短评论"}}
  ]
}}
点赞和评论必须是 AI NPC，不要伪装成用户“你”。不要使用小明、小红这种占位名。
"""
    try:
        payload = OpenAICompatibleClient(config).chat_json(
            [{"role": "system", "content": "你是 Galgame 朋友圈内容生成器，只输出 JSON。"}, {"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=0.8,
            diagnostic={
                "feature": "日程朋友圈",
                "stage": "moment_llm",
                "purpose": "Generate moment post and NPC interactions",
                "input": {
                    "user_id": user_id,
                    "character_id": character_id,
                    "slot_id": slot.slot_id,
                    "activity": slot.activity_title,
                    "location": slot.location,
                    "experience_id": exp.experience_id,
                    "experience_summary": exp.summary,
                    "memory_lines": memory_lines,
                },
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("moment LLM generation failed slot_id=%s", slot.slot_id)
        write_diagnostic("moment_skipped", reason="llm_error", slot_id=slot.slot_id, activity=slot.activity_title, error=str(exc))
        return None
    text = str(payload.get("text") or "").strip()
    if not text:
        write_diagnostic("moment_skipped", reason="llm_empty_text", slot_id=slot.slot_id, activity=slot.activity_title)
        return None
    return payload


def _npc_name(value: object, index: int) -> str:
    name = str(value or "").strip()
    return name[:24] if name else NPC_FALLBACK_NAMES[index % len(NPC_FALLBACK_NAMES)]


def _run_daily_cycle_inner(session: Session, *, user_id: str, character_id: str, day: datetime) -> dict[str, int]:
    weather_snapshot = refresh_weather_snapshot(session, user_id=user_id, local_time=day, force=False)
    weather_event = ensure_weather_candidate(session, user_id=user_id, character_id=character_id, local_time=day) if weather_snapshot is not None else None
    calendar_events = ensure_calendar_proactive_candidates(session, user_id=user_id, character_id=character_id, local_time=day)
    repost_event = ensure_repost_candidate(session, user_id=user_id, character_id=character_id, local_time=day)
    ensure_schedule(session, user_id=user_id, character_id=character_id, day=day)
    character = session.get(Character, character_id)
    slots = session.execute(
        select(ScheduleSlot).where(
            ScheduleSlot.user_id == user_id,
            ScheduleSlot.character_id == character_id,
            ScheduleSlot.can_generate_moment == True,  # noqa: E712
        )
    ).scalars().all()
    created_experiences = 0
    created_moments = 0
    created_proactive_events = 0
    skipped_moments = 0
    eligible_slots: list[ScheduleSlot] = []
    for slot in slots:
        if slot.actual_status != "completed":
            continue
        exists = session.execute(select(Experience).where(Experience.source_schedule_slot_id == slot.slot_id)).scalar_one_or_none()
        if exists:
            continue
        eligible_slots.append(slot)
    eligible_slots.sort(key=lambda item: (_slot_sort_timestamp(item), item.salience), reverse=True)
    selected_slots = eligible_slots[:MAX_MOMENTS_PER_DAILY_CYCLE]
    if len(eligible_slots) > len(selected_slots):
        write_diagnostic(
            "moment_backlog_limited",
            user_id=user_id,
            character_id=character_id,
            candidates=len(eligible_slots),
            selected=len(selected_slots),
            selected_slot_ids=[slot.slot_id for slot in selected_slots],
        )
    for slot in selected_slots:
        exp = Experience(
            experience_id=uid("exp"),
            source_schedule_slot_id=slot.slot_id,
            title=slot.activity_title,
            summary=f"亚托莉在{slot.location}完成了「{slot.activity_title}」，这段虚拟经历让她想把心情告诉你。",
            emotional_result="开心" if slot.salience >= 60 else "平静",
            can_trigger_photo=slot.can_generate_photo,
        )
        session.add(exp)
        session.add(
            Memory(
                memory_id=uid("mem"),
                user_id=user_id,
                character_id=character_id,
                layer="daily",
                content=f"亚托莉的虚拟经历：{exp.summary}",
                source_event_id=exp.experience_id,
                importance=0.7,
                confidence=0.9,
            )
        )
        proactive = create_schedule_proactive_event(
            session,
            user_id=user_id,
            character_id=character_id,
            source_id=exp.experience_id,
            title="亚托莉有一件日常想告诉你",
            summary=exp.summary,
            activity_title=slot.activity_title,
            priority=slot.salience,
            can_generate_image=slot.can_generate_photo,
        )
        if proactive is not None and proactive.source_id == exp.experience_id:
            created_proactive_events += 1
        payload = _llm_moment_payload(session, user_id=user_id, character_id=character_id, slot=slot, exp=exp)
        if payload is None:
            skipped_moments += 1
            created_experiences += 1
            continue
        media_asset_id = ""
        fallback_photo_prompt = "，".join(
            item
            for item in (slot.activity_title, slot.location, exp.summary)
            if str(item or "").strip()
        )
        photo_prompt = str(payload.get("photo_prompt") or "").strip() or fallback_photo_prompt
        if slot.can_generate_photo and photo_prompt:
            image_config = get_enabled_provider(session, "image")
            if image_config is not None:
                try:
                    requested_kind = str(payload.get("photo_kind") or "").strip()
                    image_kind = normalize_image_kind(requested_kind) if requested_kind else infer_image_kind(photo_prompt)
                    image = generate_safe_image(
                        session,
                        config=image_config,
                        kind=image_kind,
                        scene_hint=photo_prompt,
                        character=character,
                        user_id=user_id,
                        character_id=character_id,
                        source_id=f"{slot.slot_id}:moment",
                        mood=str(payload.get("mood") or exp.emotional_result),
                        cooldown_seconds=0,
                    )
                    media_asset_id = image.asset_id
                except ImageRequestBlocked as exc:
                    logger.info("daily cycle image skipped slot_id=%s reason=%s", slot.slot_id, exc)
                    write_diagnostic("moment_image_skipped", slot_id=slot.slot_id, activity=slot.activity_title, reason=str(exc))
                except Exception:
                    logger.exception("daily cycle image generation failed slot_id=%s", slot.slot_id)
                    write_diagnostic("moment_image_error", slot_id=slot.slot_id, activity=slot.activity_title)
                    media_asset_id = ""
        moment = Moment(
            moment_id=uid("moment"),
            text=str(payload.get("text") or "").strip(),
            media_asset_id=media_asset_id,
            source_experience_id=exp.experience_id,
            mood_snapshot=str(payload.get("mood") or exp.emotional_result),
        )
        session.add(moment)
        for index, name in enumerate((payload.get("likes") or [])[:8]):
            actor_name = _npc_name(name, index)
            session.add(
                MomentInteraction(
                    interaction_id=uid("mi"),
                    moment_id=moment.moment_id,
                    actor_type="npc",
                    actor_id=f"npc_like_{index}",
                    actor_name=actor_name,
                    interaction_type="like",
                )
            )
        for index, item in enumerate((payload.get("comments") or [])[:8]):
            actor_name = ""
            content = ""
            if isinstance(item, dict):
                actor_name = str(item.get("actor_name") or item.get("name") or "").strip()
                content = str(item.get("content") or item.get("text") or "").strip()
            else:
                content = str(item or "").strip()
            if not content:
                continue
            actor_name = _npc_name(actor_name, index)
            session.add(
                MomentInteraction(
                    interaction_id=uid("mi"),
                    moment_id=moment.moment_id,
                    actor_type="npc",
                    actor_id=f"npc_comment_{index}",
                    actor_name=actor_name,
                    interaction_type="comment",
                    content=content[:160],
                )
            )
        write_diagnostic("moment_created", slot_id=slot.slot_id, moment_id=moment.moment_id, activity=slot.activity_title)
        created_experiences += 1
        created_moments += 1
    session.commit()
    return {
        "experiences": created_experiences,
        "moments": created_moments,
        "proactive_events": created_proactive_events,
        "weather_events": 1 if weather_event is not None and weather_event.source_type == "weather" else 0,
        "calendar_events": calendar_events,
        "repost_events": 1 if repost_event is not None and repost_event.source_type == "repost" else 0,
        "skipped_moments": skipped_moments,
    }


def run_daily_cycle(session: Session, *, user_id: str, character_id: str, day: datetime) -> dict[str, int]:
    with diagnostic_span(
        "daily_cycle_trace",
        feature="日程朋友圈",
        stage="daily_cycle",
        purpose="Generate daily schedule experiences and moments",
        summary=f"{user_id} {day.isoformat()}",
        user_id=user_id,
        character_id=character_id,
        input={"day": day.isoformat()},
    ) as span:
        result = _run_daily_cycle_inner(session, user_id=user_id, character_id=character_id, day=day)
        span.add(output=result)
        return result
