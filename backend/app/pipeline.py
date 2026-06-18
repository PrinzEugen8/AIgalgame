from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .calendar_events import ensure_calendar_events
from .commitments import extract_user_commitment
from .diagnostics import current_span_id, current_trace_id, diagnostic_point, diagnostic_span, new_span_id, write_diagnostic
from .models import (
    CalendarEvent,
    Character,
    Memory,
    Message,
    MomentInteraction,
    ProviderConfig,
    ProactiveEvent,
    RelationState,
    ScheduleSlot,
    TtsVoiceProfile,
    User,
    UserCommitment,
)
from .persona import (
    FIXED_MEMORY_LAYERS,
    VECTOR_RECALL_LAYERS,
    character_override_for_user,
    character_override_summary,
    merge_character_override_delta,
    normalize_memory_layer,
    persona_card_summary,
    relation_attitude,
    resolve_persona_card,
    user_profile_summary,
)
from .proactive import consume_proactive_event, ensure_proactive_event_image, mark_proactive_opened, mark_proactive_reflected
from .providers import OpenAICompatibleClient, ProviderError, VolcArkWebSearchClient, VolcTtsClient, get_enabled_provider, get_task_llm_provider
from .schemas import AppEventOut, DialogueLine, DialoguePayload, EventIn, RelationDelta, ReplyOption
from .schedule import ensure_schedule, mark_interruption
from .utils import clamp, dump_json, load_json, stable_hash, uid, utc_now
from .vector_memory import safe_index_memory_vector, search_memory_vectors
from .weather import active_weather_date_for_user, is_weather_question, read_weather_snapshot, weather_snapshot_to_dict


STORY_LINES = [
    "啊，你来了。这里是我每天都会经过的教室，窗外的樱花今天开得刚刚好。",
    "我叫亚托莉。虽然这听起来有点像游戏开场白，但我想认真记住和你有关的事。",
    "以后如果我去了哪里、看到了什么，我会发给你。你也可以把喜欢的东西告诉我。",
]


def _event(event_type: str, payload: dict[str, Any], session_id: str = "default") -> AppEventOut:
    return AppEventOut(event_type=event_type, event_id=uid("evt"), session_id=session_id, payload=payload)


def _no_reply(session_id: str, *, reply_mode: str = "silent", pace_reason: str = "") -> AppEventOut:
    return _event("no_reply", {"reply_mode": reply_mode, "pace_reason": pace_reason}, session_id)


def _relation(session: Session, user_id: str, character_id: str) -> RelationState:
    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one()
    return relation


def _apply_delta(relation: RelationState, delta: RelationDelta) -> None:
    relation.affection = clamp(relation.affection + delta.affection, 0, 1000)
    relation.trust = clamp(relation.trust + delta.trust, 0, 1000)
    relation.dependency = clamp(relation.dependency + delta.dependency, 0, 1000)
    relation.mood = clamp(relation.mood + delta.mood, -100, 100)
    relation.last_interaction_at = utc_now()
    relation.updated_at = utc_now()


def _extract_local_time(event: EventIn) -> datetime | None:
    value = event.client_context.get("local_time")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_context(session: Session, user_id: str, character_id: str) -> str:
    memories = session.execute(
        select(Memory)
        .where(Memory.user_id == user_id, Memory.character_id == character_id, Memory.hidden == False)  # noqa: E712
        .order_by(Memory.created_at.desc())
        .limit(8)
    ).scalars().all()
    interactions = session.execute(
        select(MomentInteraction)
        .where(MomentInteraction.actor_id == user_id, MomentInteraction.reflected_in_chat == False)  # noqa: E712
        .order_by(MomentInteraction.created_at.desc())
        .limit(4)
    ).scalars().all()
    lines = [f"- {memory.layer}: {memory.content}" for memory in memories]
    for item in interactions:
        desc = "点赞了朋友圈" if item.interaction_type == "like" else f"评论了朋友圈：{item.content}"
        lines.append(f"- 最近朋友圈互动：用户{desc}")
    return "\n".join(lines) or "暂无长期记忆。"


def _build_recent_dialogue(session: Session, event: EventIn) -> str:
    rows = session.execute(
        select(Message)
        .where(Message.user_id == event.user_id, Message.character_id == event.character_id, Message.session_id == event.session_id)
        .order_by(Message.created_at.desc())
        .limit(10)
    ).scalars().all()
    if not rows:
        return "暂无近期对话。"
    lines = []
    for item in reversed(rows):
        speaker = "USER(用户)" if item.sender_type == "user" else "CHARACTER(亚托莉)"
        content = " ".join(item.content.split())
        if content:
            lines.append(f"{speaker}：{content[:120]}")
    return "\n".join(lines) or "暂无近期对话。"


_EXPRESSION_TAG_RE = re.compile(r"\[(happy|shy|thinking|calm|sad|angry|neutral|relaxed|surprised)\]", re.IGNORECASE)
_CONTROLLER_TAG_RE = re.compile(r"\[(face|anim|pause)\s*:\s*([^\]]+)\]", re.IGNORECASE)


def _split_expression_tag(text: str) -> tuple[str, str]:
    cleaned, expression, _motion, _pause = _split_controller_tags(text)
    return cleaned, expression


def _split_controller_tags(text: str) -> tuple[str, str, str, float]:
    source = text or ""
    expression = ""
    motion = ""
    pause = 0.0

    def replace_controller_tag(match: re.Match[str]) -> str:
        nonlocal expression, motion, pause
        tag = match.group(1).lower()
        value = match.group(2).strip()
        if tag == "face" and value:
            expression = value
        elif tag == "anim" and value:
            motion = value
        elif tag == "pause":
            try:
                pause = max(0.0, min(10.0, float(value)))
            except (TypeError, ValueError):
                pause = 0.0
        return ""

    cleaned = _CONTROLLER_TAG_RE.sub(replace_controller_tag, source)
    match = _EXPRESSION_TAG_RE.search(cleaned)
    if not match:
        return " ".join(cleaned.split()).strip(), expression.lower(), motion, pause
    expression = expression or match.group(1).lower()
    cleaned = _EXPRESSION_TAG_RE.sub("", cleaned)
    return " ".join(cleaned.split()).strip(), expression.lower(), motion, pause


def _boolish(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"0", "false", "no", "off", "none"}:
        return False
    if text in {"1", "true", "yes", "on", "auto"}:
        return True
    return default


def _floatish(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dialogue_controller(
    *,
    emotion: str,
    pose: str,
    expression: str = "",
    motion: str = "",
    state: str = "speaking",
    focus: str = "",
    mouth: str = "auto",
    lipsync: bool = True,
    pause: float = 0.0,
) -> dict[str, Any]:
    normalized_state = str(state or "speaking").strip().lower()
    if normalized_state not in {"idle", "typing", "speaking"}:
        normalized_state = "speaking"
    face = str(expression or emotion or "calm").strip()
    animation = str(motion or pose or "idle").strip()
    normalized_pause = max(0.0, min(10.0, float(pause or 0.0)))
    tags = [f"[face:{face}]", f"[anim:{animation}]"]
    if normalized_pause > 0:
        tags.append(f"[pause:{normalized_pause:g}]")
    return {
        "state": normalized_state,
        "face": face,
        "animation": animation,
        "focus": str(focus or "").strip(),
        "mouth": str(mouth or "auto").strip().lower(),
        "lipsync": bool(lipsync),
        "pause": normalized_pause,
        "tags": tags,
    }


def _summary_text(value: str, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _start_reply_stage(stage: str, summary: str, *, input_payload: dict[str, Any] | None = None, references: dict[str, Any] | None = None) -> dict[str, Any]:
    span = {
        "trace_id": current_trace_id(),
        "span_id": new_span_id(f"reply_{stage}"),
        "parent_span_id": current_span_id(),
        "started": time.monotonic(),
        "stage": stage,
        "summary": summary,
    }
    write_diagnostic(
        "reply_stage",
        phase="start",
        status="running",
        trace_id=span["trace_id"],
        span_id=span["span_id"],
        parent_span_id=span["parent_span_id"],
        feature="回复模块",
        stage=stage,
        purpose=f"Reply {stage}",
        summary=summary,
        input=input_payload or {},
        references=references or {},
    )
    return span


def _end_reply_stage(span: dict[str, Any], *, status: str = "ok", output: dict[str, Any] | None = None, references: dict[str, Any] | None = None) -> None:
    write_diagnostic(
        "reply_stage",
        phase="end",
        status=status,
        trace_id=str(span.get("trace_id") or current_trace_id()),
        span_id=str(span.get("span_id") or ""),
        parent_span_id=str(span.get("parent_span_id") or ""),
        feature="回复模块",
        stage=str(span.get("stage") or ""),
        purpose=f"Reply {span.get('stage') or 'stage'}",
        summary=str(span.get("summary") or ""),
        elapsed_ms=int((time.monotonic() - float(span.get("started") or time.monotonic())) * 1000),
        output=output or {},
        references=references or {},
    )


def _memory_ref(memory: Memory, *, score: float | None = None, source: str = "sqlite") -> dict[str, Any]:
    item = {
        "memory_id": memory.memory_id,
        "layer": memory.layer,
        "summary": _summary_text(memory.content),
        "source_event_id": memory.source_event_id,
        "importance": memory.importance,
        "confidence": memory.confidence,
        "tags": load_json(memory.tags_json, []),
        "metadata": load_json(memory.metadata_json, {}),
        "vector_status": memory.vector_status,
        "source": source,
    }
    if score is not None:
        item["score"] = round(float(score), 4)
    return item


def _memories_by_ids(session: Session, memory_ids: list[str]) -> list[Memory]:
    if not memory_ids:
        return []
    rows = session.execute(select(Memory).where(Memory.memory_id.in_(memory_ids))).scalars().all()
    by_id = {item.memory_id: item for item in rows}
    return [by_id[memory_id] for memory_id in memory_ids if memory_id in by_id and not by_id[memory_id].hidden]


def _memory_candidates_for_eval(session: Session, user_id: str, character_id: str, *, limit: int = 40) -> list[Memory]:
    return session.execute(
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.character_id == character_id,
            Memory.hidden == False,  # noqa: E712
            Memory.layer.in_(sorted(FIXED_MEMORY_LAYERS | VECTOR_RECALL_LAYERS)),
        )
        .order_by(Memory.importance.desc(), Memory.created_at.desc())
        .limit(limit)
    ).scalars().all()


def _context_with_references(session: Session, user_id: str, character_id: str, query_text: str = "") -> tuple[str, dict[str, Any], dict[str, Any]]:
    fixed_memories = session.execute(
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.character_id == character_id,
            Memory.hidden == False,  # noqa: E712
            Memory.layer.in_(sorted(FIXED_MEMORY_LAYERS)),
        )
        .order_by(Memory.importance.desc(), Memory.created_at.desc())
        .limit(8)
    ).scalars().all()
    vector_hits = []
    vector_error = ""
    try:
        vector_hits = search_memory_vectors(
            session,
            user_id=user_id,
            character_id=character_id,
            query_text=query_text,
            layers=VECTOR_RECALL_LAYERS,
            limit=8,
        )
    except Exception as exc:  # noqa: BLE001
        vector_error = str(exc)
        write_diagnostic(
            "memory_vector_recall_fallback",
            feature="记忆向量",
            stage="search_memory",
            user_id=user_id,
            character_id=character_id,
            error_type=type(exc).__name__,
            message=str(exc),
        )
    hit_by_id = {hit.memory_id: hit for hit in vector_hits}
    recalled_memories = _memories_by_ids(session, [hit.memory_id for hit in vector_hits])
    recall_source = "qdrant" if recalled_memories else "sqlite_recent"
    if not recalled_memories:
        recalled_memories = session.execute(
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.character_id == character_id,
                Memory.hidden == False,  # noqa: E712
                Memory.layer.in_(sorted(VECTOR_RECALL_LAYERS)),
            )
            .order_by(Memory.created_at.desc())
            .limit(8)
        ).scalars().all()
    memories = [*fixed_memories]
    seen = {memory.memory_id for memory in memories}
    for memory in recalled_memories:
        if memory.memory_id not in seen:
            memories.append(memory)
            seen.add(memory.memory_id)
    interactions = session.execute(
        select(MomentInteraction)
        .where(MomentInteraction.actor_id == user_id, MomentInteraction.reflected_in_chat == False)  # noqa: E712
        .order_by(MomentInteraction.created_at.desc())
        .limit(4)
    ).scalars().all()
    lines = [f"- {memory.layer}: {memory.content}" for memory in memories]
    for item in interactions:
        desc = "liked a moment" if item.interaction_type == "like" else f"commented on a moment: {item.content}"
        lines.append(f"- recent moment interaction: user {desc}")
    memory_items = []
    for memory in memories:
        hit = hit_by_id.get(memory.memory_id)
        memory_items.append(_memory_ref(memory, score=hit.score if hit else None, source=hit.source if hit else ("fixed" if memory in fixed_memories else recall_source)))
    activated_ids = {item["memory_id"] for item in memory_items}
    not_activated_items = [
        _memory_ref(memory, source="candidate_not_activated")
        for memory in _memory_candidates_for_eval(session, user_id, character_id)
        if memory.memory_id not in activated_ids
    ][:20]
    references = {
        "memory": {
            "used": bool(memories),
            "count": len(memories),
            "source": recall_source,
            "vector_error": vector_error,
            "items": memory_items,
            "activated": memory_items,
            "not_activated": not_activated_items,
        },
        "event_memory": {
            "used": any(memory.layer == "event" for memory in memories),
            "count": len([memory for memory in memories if memory.layer == "event"]),
            "items": [item for item in memory_items if item["layer"] == "event"],
        },
        "moment_interactions": {
            "used": bool(interactions),
            "count": len(interactions),
            "items": [
                {
                    "interaction_id": item.interaction_id,
                    "moment_id": item.moment_id,
                    "type": item.interaction_type,
                    "summary": _summary_text(item.content or item.interaction_type),
                }
                for item in interactions
            ],
        },
    }
    write_diagnostic(
        "memory_recall_evaluation",
        feature="记忆召回评测",
        stage="recall",
        user_id=user_id,
        character_id=character_id,
        query=query_text,
        source=recall_source,
        vector_error=vector_error,
        activated_count=len(memory_items),
        not_activated_count=len(not_activated_items),
        activated=memory_items,
        not_activated=not_activated_items,
    )
    return "\n".join(lines) or "暂无长期记忆。", references, {"memories": memories, "interactions": interactions}


def _recent_dialogue_with_references(session: Session, event: EventIn) -> tuple[str, dict[str, Any]]:
    rows = session.execute(
        select(Message)
        .where(Message.user_id == event.user_id, Message.character_id == event.character_id, Message.session_id == event.session_id)
        .order_by(Message.created_at.desc())
        .limit(10)
    ).scalars().all()
    if not rows:
        return "暂无近期对话。", {"used": False, "count": 0, "items": []}
    lines = []
    for item in reversed(rows):
        speaker = "USER" if item.sender_type == "user" else "CHARACTER"
        content = " ".join(item.content.split())
        if content:
            lines.append(f"{speaker}: {content[:120]}")
    references = {
        "used": bool(lines),
        "count": len(rows),
        "items": [
            {
                "message_id": item.message_id,
                "sender_type": item.sender_type,
                "source": item.source,
                "summary": _summary_text(item.content),
            }
            for item in reversed(rows)
            if item.content
        ],
    }
    return "\n".join(lines) or "暂无近期对话。", references


def _target_subject_hint(text: str, character: Character) -> str:
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return "不确定：目标消息为空。"
    role_markers = [
        "你",
        "妳",
        character.name,
        character.character_id,
        "亚托莉",
        "亚托莉",
        "你刚刚",
        "你是不是",
        "你去",
        "你在",
    ]
    user_markers = ["我", "俺", "本人", "咱", "我刚刚", "我去", "我在", "我找"]
    if any(marker and marker in normalized for marker in role_markers):
        return "角色：目标消息明确提到“你/角色名”，可以把相关动作归给角色。"
    if any(marker in normalized for marker in user_markers):
        return "用户：目标消息明确使用用户第一人称，动作主体是用户。"
    return "用户：目标消息没有明确主语时，默认动作主体是用户自己；不要改写成用户在质问角色。"


def _slot_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed


def _same_timezone(reference: datetime, value: datetime) -> datetime:
    if value.tzinfo is None and reference.tzinfo is not None:
        return reference.replace(tzinfo=None)
    if value.tzinfo is not None and reference.tzinfo is None:
        return reference.replace(tzinfo=value.tzinfo)
    return reference


def _format_slot(slot: ScheduleSlot | None) -> str:
    if slot is None:
        return "无"
    start = str(slot.start_at).split("T", 1)[-1][:5]
    end = str(slot.end_at).split("T", 1)[-1][:5]
    return f"{start}-{end} {slot.activity_title}，地点：{slot.location or '未写'}，状态：{slot.actual_status}"


def _schedule_context(session: Session, event: EventIn) -> str:
    local_time = _extract_local_time(event) or datetime.now()
    slots = ensure_schedule(session, user_id=event.user_id, character_id=event.character_id, day=local_time)
    comparable_now = local_time
    current: ScheduleSlot | None = None
    previous: ScheduleSlot | None = None
    upcoming: ScheduleSlot | None = None
    sorted_slots = sorted(slots, key=lambda slot: slot.start_at)
    for slot in sorted_slots:
        start = _slot_time(slot.start_at)
        end = _slot_time(slot.end_at)
        if start is None or end is None:
            continue
        now_for_slot = _same_timezone(comparable_now, start)
        if start <= now_for_slot < end:
            current = slot
        elif end <= now_for_slot:
            previous = slot
        elif upcoming is None and start > now_for_slot:
            upcoming = slot
    groups: list[str] = []
    seen: set[str] = set()
    for slot in sorted_slots:
        key = f"{slot.activity_title}|{slot.location}"
        if key in seen:
            continue
        seen.add(key)
        same = [item for item in sorted_slots if item.activity_title == slot.activity_title and item.location == slot.location]
        groups.append(f"{str(same[0].start_at).split('T', 1)[-1][:5]}-{str(same[-1].end_at).split('T', 1)[-1][:5]} {slot.activity_title}@{slot.location}")
    return "\n".join(
        [
            f"客户端当前时间：{local_time.isoformat()}",
            f"刚刚/上一段：{_format_slot(previous)}",
            f"当前：{_format_slot(current)}",
            f"下一段：{_format_slot(upcoming)}",
            "今日摘要：" + "；".join(groups[:10]),
        ]
    )


def _schedule_context_with_references(session: Session, event: EventIn) -> tuple[str, dict[str, Any]]:
    context = _schedule_context(session, event)
    local_time = _extract_local_time(event) or datetime.now()
    slots = ensure_schedule(session, user_id=event.user_id, character_id=event.character_id, day=local_time)
    items = [
        {
            "slot_id": slot.slot_id,
            "title": slot.activity_title,
            "location": slot.location,
            "start_at": slot.start_at,
            "end_at": slot.end_at,
            "status": slot.actual_status,
        }
        for slot in slots[:10]
    ]
    return context, {"used": bool(context.strip()), "count": len(slots), "items": items, "summary": _summary_text(context, 220)}


def _user_schedule_context_with_references(session: Session, event: EventIn) -> tuple[str, dict[str, Any]]:
    rows = session.execute(
        select(UserCommitment)
        .where(
            UserCommitment.user_id == event.user_id,
            UserCommitment.character_id == event.character_id,
            UserCommitment.status == "active",
        )
        .order_by(UserCommitment.event_at.desc(), UserCommitment.created_at.desc())
        .limit(8)
    ).scalars().all()
    if not rows:
        return "暂无用户日程。", {"used": False, "count": 0, "items": []}
    items = [
        {
            "commitment_id": item.commitment_id,
            "title": item.title,
            "event_at": item.event_at,
            "remind_at": item.remind_at,
            "summary": _summary_text(item.description or item.title),
        }
        for item in rows
    ]
    lines = [
        f"- {item.title}: event_at={item.event_at or '未填写'} remind_at={item.remind_at or '未填写'} {item.description or ''}".strip()
        for item in rows
    ]
    return "\n".join(lines), {"used": True, "count": len(rows), "items": items, "summary": _summary_text("\n".join(lines), 220)}


def _weather_context_with_references(
    session: Session,
    *,
    user_id: str,
    text: str = "",
    local_time: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    snapshot = read_weather_snapshot(session, user_id=user_id, local_time=local_time, allow_stale=True)
    if snapshot is None:
        context = "还没有今天的天气数据：天气会在每天04:00随日程生成时更新。" if is_weather_question(text) else "暂无天气数据。"
        return context, {"used": bool(context.strip()), "available": False, "summary": context}
    active_weather_date = active_weather_date_for_user(session, user_id=user_id, local_time=local_time)
    freshness_note = ""
    if snapshot.weather_date != active_weather_date:
        freshness_note = f"暂无{active_weather_date} 04:00后的天气数据；以下是上次天气快照。"
    payload = weather_snapshot_to_dict(snapshot) or {}
    now_payload = (payload.get("now") or {}).get("now") or {}
    daily_rows = ((payload.get("daily") or {}).get("daily") or []) if isinstance(payload.get("daily"), dict) else []
    hourly_rows = ((payload.get("hourly") or {}).get("hourly") or []) if isinstance(payload.get("hourly"), dict) else []
    warning_rows = ((payload.get("warning") or {}).get("warning") or []) if isinstance(payload.get("warning"), dict) else []
    next_hours = []
    for row in hourly_rows[:8]:
        if isinstance(row, dict):
            next_hours.append(f"{str(row.get('fxTime') or '')[-11:-6]} {row.get('text') or ''} {row.get('temp') or '?'}C pop {row.get('pop') or '?'}%")
    today = daily_rows[0] if daily_rows and isinstance(daily_rows[0], dict) else {}
    warning_text = " / ".join(str(item.get("title") or item.get("typeName") or "") for item in warning_rows[:3] if isinstance(item, dict))
    context = "\n".join(
        [item for item in [
            freshness_note,
            f"城市：{snapshot.city_name or '未知'}",
            f"摘要：{snapshot.summary}",
            f"严重度：{snapshot.severity} / {snapshot.severity_score} / {snapshot.trigger_key}",
            f"今日：白天{today.get('textDay') or '?'}，夜间{today.get('textNight') or '?'}，{today.get('tempMin') or '?'}-{today.get('tempMax') or '?'}C，降水{today.get('precip') or '?'}",
            f"未来8小时：{'；'.join(next_hours) if next_hours else '暂无'}",
            f"预警：{warning_text or '暂无'}",
            f"更新时间：{snapshot.observed_at or snapshot.fetched_at}",
        ] if item]
    )
    return context, {
        "used": True,
        "available": True,
        "snapshot_id": snapshot.snapshot_id,
        "city_name": snapshot.city_name,
        "trigger_key": snapshot.trigger_key,
        "severity": snapshot.severity,
        "summary": snapshot.summary,
    }


def _calendar_event_date(event: CalendarEvent, today: date) -> date | None:
    try:
        if event.repeats_yearly:
            return date(today.year, int(event.event_date[5:7]), int(event.event_date[8:10]))
        return date.fromisoformat(event.event_date)
    except (ValueError, IndexError):
        return None


def _calendar_lookahead_days(text: str) -> int:
    normalized = " ".join(str(text or "").split())
    if any(marker in normalized for marker in ("一个星期", "一周", "下周", "再过几天", "再过一个星期", "放假", "假期", "节日", "端午", "中秋", "国庆", "春节")):
        return 14
    return 7


def _calendar_context_with_references(session: Session, event: EventIn, text: str) -> tuple[str, dict[str, Any]]:
    local_time = _extract_local_time(event) or datetime.now()
    today = local_time.date()
    lookahead_days = _calendar_lookahead_days(text)
    ensure_calendar_events(session, user_id=event.user_id, character_id=event.character_id)
    rows = session.execute(
        select(CalendarEvent).where(
            CalendarEvent.hidden == False,  # noqa: E712
            CalendarEvent.user_id.in_(["", event.user_id]),
        )
    ).scalars().all()
    items: list[dict[str, Any]] = []
    end = today + timedelta(days=lookahead_days)
    for row in rows:
        if row.character_id not in {"", event.character_id}:
            continue
        event_day = _calendar_event_date(row, today)
        if event_day is None or event_day < today or event_day > end:
            continue
        days_until = (event_day - today).days
        items.append(
            {
                "event_id": row.event_id,
                "date": event_day.isoformat(),
                "days_until": days_until,
                "title": row.title,
                "category": row.category,
                "description": row.description,
                "source_type": row.source_type,
                "salience": row.salience,
            }
        )
    items = sorted(items, key=lambda item: (item["days_until"], -int(item["salience"]), item["title"]))[:10]
    if not items:
        context = f"未来{lookahead_days}天没有匹配到日历事件。"
        return context, {"used": False, "available": True, "lookahead_days": lookahead_days, "items": [], "summary": context}
    lines = [
        f"- {item['date']}（{item['days_until']}天后）{item['title']}：{item['description'] or item['category']}，来源={item['source_type'] or 'calendar'}"
        for item in items
    ]
    context = "\n".join(lines)
    return context, {"used": True, "available": True, "lookahead_days": lookahead_days, "count": len(items), "items": items, "summary": _summary_text(context, 260)}


def _needs_web_search(text: str) -> bool:
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return False
    if is_weather_question(normalized):
        return False
    if any(marker in normalized for marker in ("联网搜索", "网上查", "帮我查", "查一下", "搜索一下", "搜一下")):
        return True
    return any(marker in normalized for marker in ("最新新闻", "今天新闻", "最近新闻", "热搜", "发生了什么", "现在网上", "最新进展", "刚发布", "实时"))


def _web_search_context_with_references(session: Session, text: str) -> tuple[str, dict[str, Any]]:
    if not _needs_web_search(text):
        return "未触发联网搜索。", {"used": False, "available": False, "reason": "not_needed", "items": []}
    config = get_enabled_provider(session, "search")
    if config is None:
        return "需要联网搜索，但后台没有启用搜索 Provider。", {"used": False, "available": False, "reason": "provider_missing", "items": []}
    if config.provider == "trend_radar":
        return (
            "需要联网搜索，但当前启用的是 TrendRadar；TrendRadar 只作为新闻模块来源，不作为对话即时联网搜索。",
            {"used": False, "available": False, "provider_id": config.provider_id, "provider": config.provider, "reason": "trend_radar_is_news_source", "items": []},
        )
    if config.provider != "volc_ark_web_search":
        return (
            f"需要联网搜索，但当前搜索 Provider {config.provider} 不支持对话即时搜索。",
            {"used": False, "available": False, "provider_id": config.provider_id, "provider": config.provider, "reason": "unsupported_provider", "items": []},
        )
    try:
        result = VolcArkWebSearchClient(config).search(text)
    except Exception as exc:  # noqa: BLE001
        write_diagnostic(
            "dialogue_web_search_error",
            feature="联网搜索",
            stage="dialogue_search",
            provider_id=config.provider_id,
            provider=config.provider,
            query=text,
            error_type=type(exc).__name__,
            message=str(exc),
        )
        return f"联网搜索失败：{type(exc).__name__}。", {"used": False, "available": False, "provider_id": config.provider_id, "provider": config.provider, "reason": "search_error", "message": str(exc), "items": []}
    sources = result.get("sources") or []
    source_lines = [
        f"- {item.get('title') or '未命名来源'}：{item.get('url') or ''} {item.get('published_at') or ''}".strip()
        for item in sources[:5]
        if isinstance(item, dict)
    ]
    context = "\n".join([str(result.get("summary") or "").strip(), *source_lines]).strip() or "联网搜索没有返回摘要。"
    return context, {
        "used": True,
        "available": True,
        "provider_id": config.provider_id,
        "provider": config.provider,
        "model": config.model,
        "query": text,
        "summary": _summary_text(context, 300),
        "items": sources[:5],
    }


def _dialogue_gate(event: EventIn, text: str) -> str:
    if event.event_type in {"notification_opened", "widget_opened"}:
        return "opening：用户从主动入口进入，请自然展开主动话题，至少 2 句、最多 3 句，不要 silent。"
    if event.event_type == "app_opened":
        return "continue：用户是从入口进入前台。只有带明确主动事件时才展开；没有新信息时可以 silent。"
    if len(text.strip()) <= 2 and text.strip() in {"嗯", "好", "哦", "啊", "…", "..."}:
        return "light：用户只给了很短的接话，轻轻回应或留白即可，不要制造重大剧情。"
    return "reply：用户正在直接对角色说话，需要围绕目标消息自然回应，normal 模式至少 2 句，每句尽量不超过 28 个汉字。"


_PROTECTED_SPLIT_TERMS = ("闲工夫", "没关系", "端午节", "中秋节", "国庆节")


def _clean_dialogue_chunk(text: str) -> str:
    return str(text or "").strip(" \t\r\n，,；;、")


def _hard_split_dialogue_piece(piece: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    rest = piece
    while len(rest) > max_chars:
        cut = max_chars
        for separator in ("，", ",", "、", "；", ";", " "):
            index = rest.rfind(separator, 0, max_chars + 1)
            if index >= max(8, max_chars // 2):
                cut = index + 1
                break
        for term in _PROTECTED_SPLIT_TERMS:
            for offset in range(1, len(term)):
                if rest[:cut].endswith(term[:offset]) and rest[cut:].startswith(term[offset:]):
                    cut = max(1, cut - offset)
        chunk = _clean_dialogue_chunk(rest[:cut])
        if chunk:
            chunks.append(chunk)
        rest = rest[cut:].lstrip(" ，,；;、")
    tail = _clean_dialogue_chunk(rest)
    if tail:
        chunks.append(tail)
    return chunks


def _normalize_line_text(text: str, *, max_chars: int = 28) -> list[str]:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]
    parts = re.split(r"(?<=[。！？!?；;])", cleaned)
    chunks: list[str] = []
    buffer = ""
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        soft_parts = [item for item in re.split(r"(?<=[，,])", piece) if item.strip()]
        for soft_part in soft_parts:
            soft_piece = soft_part.strip()
            candidate = f"{buffer}{soft_piece}" if buffer else soft_piece
            if len(candidate) <= max_chars:
                buffer = candidate
                continue
            if buffer:
                cleaned_buffer = _clean_dialogue_chunk(buffer)
                if cleaned_buffer:
                    chunks.append(cleaned_buffer)
                buffer = ""
            if len(soft_piece) <= max_chars:
                buffer = soft_piece
            else:
                chunks.extend(_hard_split_dialogue_piece(soft_piece, max_chars))
    if buffer:
        cleaned_buffer = _clean_dialogue_chunk(buffer)
        if cleaned_buffer:
            chunks.append(cleaned_buffer)
    return [item for item in chunks if item.strip()]


def _explicit_interest_topics(text: str) -> list[str]:
    normalized = " ".join(text.split())
    triggers = ["我最近关注", "我关注", "我喜欢", "我想了解", "我对", "我感兴趣"]
    topics: list[str] = []
    for trigger in triggers:
        if trigger not in normalized:
            continue
        tail = normalized.split(trigger, 1)[-1]
        if trigger == "我对" and "感兴趣" in tail:
            tail = tail.split("感兴趣", 1)[0]
        topic = tail.strip(" ：:，,。.!！?？、")
        for separator in ("，", "。", "！", "？", ",", ".", "!", "?"):
            if separator in topic:
                topic = topic.split(separator, 1)[0].strip()
        if 1 <= len(topic) <= 40:
            topics.append(topic)
    return topics[:3]


def _first_match(patterns: tuple[str, ...], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        for group in match.groups():
            value = str(group or "").strip(" ：:，,。.!！?？、\"“”'‘’")
            if value:
                return value[:40]
    return ""


def _short_preference_items(patterns: tuple[str, ...], text: str, *, limit: int = 3) -> list[str]:
    items: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = ""
            for group in match.groups():
                value = str(group or "").strip(" ：:，,。.!！?？、\"“”'‘’")
                if value:
                    break
            if not value:
                continue
            for separator in ("，", "。", "！", "？", ",", ".", "!", "?", "但是", "不过"):
                if separator in value:
                    value = value.split(separator, 1)[0].strip()
            if 1 <= len(value) <= 40 and value not in items:
                items.append(value)
            if len(items) >= limit:
                return items
    return items


def _profile_mutation_delta(text: str, explicit_topics: list[str], memory_writes: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return {}
    delta: dict[str, Any] = {}
    preferred_name = _first_match(
        (
            r"(?:以后|以后就|以后你)?(?:叫我|喊我|称呼我)[：: ]*([\u4e00-\u9fffA-Za-z0-9_\-]{1,16})",
            r"我的名字(?:是|叫)[：: ]*([\u4e00-\u9fffA-Za-z0-9_\-]{1,16})",
        ),
        normalized,
    )
    if preferred_name:
        delta.setdefault("editable_overrides", {})["preferred_user_name"] = preferred_name
        delta.setdefault("relationships", {}).setdefault("user_specific_notes", []).append(f"用户希望被称呼为：{preferred_name}")

    disabled_phrases = _short_preference_items((r"(?:以后)?(?:别再说|不要再说|别老说|少说)[：: ]*([^，。！？,!.?]{1,30})",), normalized)
    if disabled_phrases:
        delta.setdefault("editable_overrides", {}).setdefault("disabled_phrases", []).extend(disabled_phrases)

    avoid_topics = _short_preference_items((r"(?:我不喜欢|我讨厌|我不想聊|别跟我聊)[：: ]*([^，。！？,!.?]{1,30})",), normalized)
    if avoid_topics:
        delta.setdefault("information_profile", {}).setdefault("avoid_topics", []).extend(avoid_topics)
        delta.setdefault("dislikes", []).extend(avoid_topics)

    catchphrases = _short_preference_items(
        (
            r"(?:你以后可以说|以后你可以说|以后可以说|口癖是)[：: ]*[“\"]([^”\"]{1,30})[”\"]",
            r"(?:你以后可以说|以后你可以说|以后可以说|口癖是)[：: ]*([^，。！？,!.?]{1,30})",
        ),
        normalized,
    )
    if catchphrases:
        delta.setdefault("speech_profile", {}).setdefault("catchphrases", []).extend(catchphrases)

    if explicit_topics:
        delta.setdefault("information_profile", {}).setdefault("personal_topics", []).extend(explicit_topics)
        delta.setdefault("likes", []).extend(explicit_topics)

    shared_experiences: list[dict[str, str]] = []
    for item in memory_writes:
        layer = str(item.get("layer") or "")
        summary = str(item.get("summary") or "").strip()
        tags = {str(tag) for tag in item.get("tags") or []}
        if not summary or layer not in {"event", "daily", "relation", "chat", "user_schedule"}:
            continue
        if not (tags & {"shared", "promise", "experience", "commitment"} or any(marker in summary for marker in ("一起", "我们", "约定", "答应", "共同"))):
            continue
        shared_experiences.append({"summary": summary[:120], "source_memory_id": str(item.get("memory_id") or "")})
    if shared_experiences:
        delta.setdefault("life_story", {}).setdefault("shared_experiences", []).extend(shared_experiences[:3])
        delta.setdefault("experiences", []).extend([item["summary"] for item in shared_experiences[:3]])
    return delta


def _apply_profile_mutations(user: User, *, event: EventIn, text: str, explicit_topics: list[str], memory_writes: list[dict[str, Any]]) -> dict[str, Any]:
    delta = _profile_mutation_delta(text, explicit_topics, memory_writes)
    if not delta:
        write_diagnostic(
            "profile_mutation_skipped",
            feature="角色养成覆盖",
            stage="profile_mutation",
            user_id=event.user_id,
            character_id=event.character_id,
            reason="no_explicit_mutation_signal",
        )
        return {"changed": False, "reason": "no_explicit_mutation_signal"}
    profile = load_json(user.profile_json, {})
    merged, changed, sanitized = merge_character_override_delta(profile, event.character_id, delta)
    if changed:
        user.profile_json = dump_json(merged)
        user.updated_at = utc_now()
    result = {
        "changed": changed,
        "delta": sanitized,
        "source_memory_ids": [str(item.get("memory_id") or "") for item in memory_writes if item.get("memory_id")],
    }
    write_diagnostic(
        "profile_mutation_applied" if changed else "profile_mutation_unchanged",
        feature="角色养成覆盖",
        stage="profile_mutation",
        user_id=event.user_id,
        character_id=event.character_id,
        **result,
    )
    return result


def _memory_candidate_allowed(content: str, explicit_topics: list[str], source_text: str = "", character: Character | None = None) -> bool:
    normalized = " ".join(content.split())
    if not normalized:
        return False
    if normalized.startswith("用户最近关注"):
        return any(topic and topic in normalized for topic in explicit_topics)
    ai_self_report_markers = (
        "我回答",
        "我回应",
        "我分享",
        "我提到",
        "我说",
        "我解释",
        "我辩解",
        "我承认",
        "我否认",
        "我答应",
        "我提议",
        "我表示",
    )
    if any(marker in normalized for marker in ai_self_report_markers):
        return False
    source = " ".join(str(source_text or "").split())
    role_markers = ["你", "妳", "亚托莉", "亚托莉"]
    if character is not None:
        role_markers.extend([character.name, character.character_id])
    mentions_role = any(marker and marker in source for marker in role_markers)
    role_attribution_markers = (
        "用户调侃我",
        "用户质问我",
        "用户怀疑我",
        "用户说我",
        "用户问我是不是",
        "用户认为我",
    )
    if not mentions_role and any(marker in normalized for marker in role_attribution_markers):
        return False
    return True


def _has_tts_readable_text(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


def _contains_japanese_kana(text: str) -> bool:
    return any("぀" <= ch <= "ヿ" or "ｦ" <= ch <= "ﾟ" for ch in text)


def _active_voice_profile(session: Session, character: Character) -> TtsVoiceProfile | None:
    if character.tts_voice_profile_id:
        voice = session.get(TtsVoiceProfile, character.tts_voice_profile_id)
        if voice is not None and voice.enabled:
            return voice
    return None


def _valid_japanese_tts_text(source_text: str, candidate: str) -> bool:
    cleaned = " ".join(candidate.split()).strip()
    source = " ".join(source_text.split()).strip()
    return bool(cleaned and cleaned != source and _has_tts_readable_text(cleaned) and _contains_japanese_kana(cleaned))


def _extract_tts_text_candidate(value: str) -> str:
    cleaned = value.strip().strip("`").strip()
    if not cleaned:
        return ""
    if cleaned.startswith("{"):
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            return cleaned
        if isinstance(payload, dict):
            return str(payload.get("tts_text_ja") or payload.get("tts_text") or payload.get("ja") or "").strip()
    return cleaned


def _japanese_tts_text(session: Session, character: Character, text: str, candidate: str | None = None) -> str | None:
    if candidate and _valid_japanese_tts_text(text, candidate):
        return candidate.strip()
    if candidate:
        write_diagnostic(
            "tts_translate_rejected",
            character_id=character.character_id,
            reason="candidate_text_is_not_japanese",
            source_text=text,
            translated_text=candidate,
        )
    config = get_task_llm_provider(session)
    if config is None:
        write_diagnostic("tts_translate_skipped", character_id=character.character_id, reason="llm_not_configured")
        return None
    client = OpenAICompatibleClient(config)
    for attempt in range(1, 3):
        try:
            translated = client.chat_text(
                [
                    {"role": "system", "content": "Output only natural Japanese dialogue for TTS. No JSON, no explanation."},
                    {
                        "role": "user",
                        "content": (
                            "Translate this Chinese galgame character line into natural spoken Japanese for voice synthesis.\n"
                            "Keep it short, gentle, and conversational. Output only the Japanese line.\n"
                            f"Character: {character.name}\nChinese line: {text}"
                        ),
                    },
                ],
                max_tokens=4096,
                temperature=0.2,
                diagnostic={
                    "feature": "日文 TTS 修复",
                    "stage": "line_repair",
                    "purpose": "Translate Chinese display line into Japanese TTS text",
                    "input": {"character_id": character.character_id, "source_text": text, "attempt": attempt},
                },
            )
            translated = _extract_tts_text_candidate(translated)
            if _valid_japanese_tts_text(text, translated):
                write_diagnostic(
                    "tts_translate_ok",
                    stage="line_repair",
                    provider_id=config.provider_id,
                    model=config.model,
                    character_id=character.character_id,
                    attempt=attempt,
                    source_text=text,
                    translated_text=translated,
                )
                return translated
            write_diagnostic(
                "tts_translate_rejected",
                stage="line_repair",
                provider_id=config.provider_id,
                model=config.model,
                character_id=character.character_id,
                reason="retry_text_is_not_japanese",
                attempt=attempt,
                source_text=text,
                translated_text=translated,
            )
        except Exception as exc:  # noqa: BLE001
            write_diagnostic(
                "tts_translate_error",
                stage="line_repair",
                provider_id=config.provider_id,
                model=config.model,
                character_id=character.character_id,
                attempt=attempt,
                error_type=type(exc).__name__,
                message=str(exc),
            )
    write_diagnostic(
        "tts_translate_failed",
        stage="line_repair",
        provider_id=config.provider_id,
        model=config.model,
        character_id=character.character_id,
        source_text=text,
        attempts=2,
    )
    return None


def _event_request_fingerprint(event: EventIn, text: str) -> str:
    normalized_text = " ".join(str(text or "").split())
    reply_id = str(event.payload.get("reply_id") or "")
    return stable_hash("event_request", event.event_type, event.user_id, event.character_id, event.session_id, reply_id, normalized_text)


def _save_message(
    session: Session,
    *,
    event: EventIn,
    sender_type: str,
    sender_id: str,
    content: str,
    mode: str = "daily_chat",
    source: str = "",
    relation_delta: RelationDelta | None = None,
    tts_audio_asset_id: str = "",
    media_asset_id: str = "",
    request_fingerprint: str = "",
) -> Message:
    fingerprint = request_fingerprint or _event_request_fingerprint(event, content)
    msg = Message(
        message_id=uid("msg"),
        session_id=event.session_id,
        user_id=event.user_id,
        character_id=event.character_id,
        sender_type=sender_type,
        sender_id=sender_id,
        content=content,
        message_mode=mode,
        source=source,
        source_event_id=event.event_id or "",
        request_fingerprint=fingerprint,
        relation_delta_json=dump_json((relation_delta or RelationDelta()).model_dump()),
        tts_audio_asset_id=tts_audio_asset_id,
        media_asset_id=media_asset_id,
    )
    session.add(msg)
    return msg


def _tts_for_line(
    session: Session,
    user: User,
    character: Character,
    text: str,
    emotion: str = "calm",
    *,
    tts_text_ja: str | None = None,
) -> tuple[str, str]:
    if not user.tts_enabled:
        return "", ""
    if not _has_tts_readable_text(text):
        return "", ""
    voice = _active_voice_profile(session, character)
    config = session.get(ProviderConfig, voice.provider_id) if voice is not None and voice.provider_id else get_enabled_provider(session, "tts")
    if config is None:
        return "", "语音未配置"
    speaker = voice.speaker if voice is not None else character.tts_voice_type
    resource_id = voice.resource_id if voice is not None else ""
    if voice is not None and voice.language == "ja":
        tts_text = _japanese_tts_text(session, character, text, tts_text_ja)
        if tts_text is None:
            write_diagnostic(
                "tts_line_blocked",
                provider_id=config.provider_id,
                character_id=character.character_id,
                voice_id=voice.voice_id,
                speaker=speaker,
                resource_id=resource_id,
                tts_language=voice.language,
                reason="japanese_translation_failed",
            )
            raise ProviderError("日文配音文本生成失败")
    else:
        tts_text = text
    try:
        asset = VolcTtsClient(config).synthesize(session, tts_text, voice_type=speaker, resource_id=resource_id, line_emotion=emotion)
        write_diagnostic(
            "tts_line_ready",
            provider_id=config.provider_id,
            character_id=character.character_id,
            voice_id=voice.voice_id if voice is not None else "",
            speaker=speaker,
            resource_id=resource_id,
            tts_language=voice.language if voice is not None else "provider_default",
            source_text=text,
            tts_text=tts_text,
            has_audio=True,
        )
        return asset.url, ""
    except Exception as exc:  # noqa: BLE001
        write_diagnostic(
            "tts_line_error",
            provider_id=config.provider_id,
            character_id=character.character_id,
            voice_id=voice.voice_id if voice is not None else "",
            speaker=speaker,
            resource_id=resource_id,
            tts_language=voice.language if voice is not None else "provider_default",
            emotion=emotion,
            error_type=type(exc).__name__,
            message=str(exc),
        )
        return "", "语音生成失败，已记录诊断日志"


def _story_response(session: Session, event: EventIn, user: User, character: Character) -> AppEventOut:
    index = int(event.payload.get("story_index") or 0)
    if event.event_type == "option_selected":
        choice = str(event.payload.get("reply_id") or "")
        relation = _relation(session, event.user_id, event.character_id)
        delta = RelationDelta(affection=5, trust=4, mood=4) if choice == "k_remember" else RelationDelta(affection=2, mood=2)
        _apply_delta(relation, delta)
        session.add(
            Memory(
                memory_id=uid("mem"),
                user_id=event.user_id,
                character_id=event.character_id,
                layer="core",
                content="相识剧情中，用户选择认真和亚托莉互相了解。",
                source_event_id=event.event_id or uid("evt"),
                importance=0.9,
                confidence=0.9,
            )
        )
        user.story_completed = True
        session.commit()
        text = "嗯，那从今天开始就是第 1 天。请多指教……我会好好记住的。"
        tts_url, tts_error = _tts_for_line(session, user, character, text, "shy")
        payload = DialoguePayload(
            lines=[DialogueLine(line_id=uid("line"), text=text, emotion="shy", pose="shy", tts_audio_url=tts_url, tts_error=tts_error)],
            relation_delta=delta,
        )
        return _event("dialogue", {**payload.model_dump(), "story_completed": True}, event.session_id)
    line = STORY_LINES[min(index, len(STORY_LINES) - 1)]
    tts_url, tts_error = _tts_for_line(session, user, character, line, "happy")
    key_replies: list[ReplyOption] = []
    normal_replies: list[ReplyOption] = []
    if index >= len(STORY_LINES) - 1:
        key_replies.append(
            ReplyOption(
                reply_id="k_remember",
                type="key",
                text="那我们认真地认识彼此吧。",
                preview_delta=RelationDelta(affection=5, trust=4, mood=4),
                trigger_memory=True,
            )
        )
        normal_replies.append(ReplyOption(reply_id="n_start", text="嗯，之后慢慢聊。"))
    payload = DialoguePayload(
        lines=[DialogueLine(line_id=uid("line"), text=line, emotion="happy", pose="idle", tts_audio_url=tts_url, tts_error=tts_error)],
        normal_replies=normal_replies,
        key_replies=key_replies,
    )
    return _event("story_line", {**payload.model_dump(), "story_index": index + 1}, event.session_id)


def _japanese_tts_problems(result: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for index, item in enumerate(result.get("lines") or []):
        if not isinstance(item, dict):
            problems.append(f"lines[{index}] 不是对象")
            continue
        text = " ".join(str(item.get("text") or "").split())
        if not _has_tts_readable_text(text):
            continue
        candidate = str(item.get("tts_text_ja") or item.get("tts_text") or item.get("ja") or "").strip()
        if not _valid_japanese_tts_text(text, candidate):
            problems.append(f"lines[{index}] 缺少合格 tts_text_ja")
    return problems


def _reply_depth_by_text(text: str, event_type: str) -> str:
    normalized = " ".join(str(text or "").split())
    if event_type in {"notification_opened", "widget_opened"}:
        return "normal"
    if len(normalized) >= 80:
        return "multi_bubble"
    if any(
        marker in normalized
        for marker in ("难过", "害怕", "担心", "失眠", "喜欢你", "讨厌我", "关系", "以后怎么办", "为什么", "重要", "认真", "秘密", "吵架", "孤独", "陪陪我", "怎么办")
    ):
        return "deep"
    return "normal"


def _reply_depth_from_result(result: dict[str, Any], fallback: str) -> str:
    depth = str(result.get("reply_depth") or result.get("response_depth") or fallback or "normal").strip().lower()
    return depth if depth in {"brief", "normal", "deep", "multi_bubble"} else "normal"


def _max_lines_for_reply(reply_mode: str, reply_depth: str) -> int:
    if reply_mode == "light" or reply_depth == "brief":
        return 1
    if reply_mode == "opening":
        return 3
    if reply_depth == "multi_bubble":
        return 7
    if reply_depth == "deep" or reply_mode == "key_moment":
        return 6
    return 4


def _wants_continuation(result: dict[str, Any], reply_mode: str, reply_depth: str) -> bool:
    if reply_mode in {"silent", "light", "opening"}:
        return False
    explicit = result.get("should_continue")
    if isinstance(explicit, bool):
        return explicit
    return bool(str(result.get("continuation_intent") or "").strip() and reply_depth in {"deep", "multi_bubble"})


def _dialogue_line_candidates(lines: Any, *, max_chars: int = 34) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in lines or []:
        if not isinstance(item, dict):
            continue
        controller = item.get("controller") if isinstance(item.get("controller"), dict) else {}
        line_emotion = str(item.get("emotion") or "calm")
        line_pose = str(item.get("pose") or "idle")
        line_expression = str(controller.get("face") or item.get("expression") or "").strip()
        line_motion = str(controller.get("animation") or item.get("motion") or "").strip()
        line_state = str(controller.get("state") or item.get("state") or "speaking").strip()
        line_focus = str(controller.get("focus") or item.get("focus") or "").strip()
        line_mouth = str(controller.get("mouth") or item.get("mouth") or "auto").strip()
        line_lipsync = _boolish(controller.get("lipsync", item.get("lipsync")), default=True)
        line_pause = max(0.0, min(10.0, _floatish(controller.get("pause", item.get("pause")), default=0.0)))
        visual_cues = _normalize_visual_cues(item.get("visual_cues") or item.get("expression_cues") or [], line_expression or line_emotion)
        line_text = " ".join(str(item.get("text") or "").split()).strip()
        line_text, inline_expression, inline_motion, _inline_pause = _split_controller_tags(line_text)
        if not line_expression:
            line_expression = inline_expression
        if not line_motion:
            line_motion = inline_motion
        if not line_pause:
            line_pause = _inline_pause
        line_controller = _dialogue_controller(
            emotion=line_emotion,
            pose=line_pose,
            expression=line_expression,
            motion=line_motion,
            state=line_state,
            focus=line_focus,
            mouth=line_mouth,
            lipsync=line_lipsync,
            pause=line_pause,
        )
        ja_candidate = str(item.get("tts_text_ja") or item.get("tts_text") or item.get("ja") or "").strip()
        for chunk_index, chunk_text in enumerate(_normalize_line_text(line_text, max_chars=max_chars)):
            if not _has_tts_readable_text(chunk_text):
                continue
            candidates.append(
                {
                    "text": chunk_text,
                    "emotion": line_emotion,
                    "pose": line_pose,
                    "expression": line_expression,
                    "motion": line_motion,
                    "controller": line_controller,
                    "visual_cues": visual_cues if chunk_index == 0 else [],
                    "tts_text_ja": ja_candidate if chunk_index == 0 else "",
                }
            )
    return candidates


def _normalize_visual_cues(raw_cues: Any, fallback_face: str) -> list[dict[str, Any]]:
    if not isinstance(raw_cues, list):
        return []
    cues: list[dict[str, Any]] = []
    for item in raw_cues[:6]:
        if not isinstance(item, dict):
            continue
        face = str(item.get("face") or item.get("expression") or item.get("emotion") or fallback_face or "calm").strip()
        focus = str(item.get("focus") or "").strip()
        text = " ".join(str(item.get("text") or item.get("span") or "").split()).strip()
        if not face and not focus:
            continue
        cues.append(
            {
                "text": text,
                "face": face,
                "expression": str(item.get("expression") or face).strip(),
                "focus": focus,
                "weight": max(0.1, min(1.0, _floatish(item.get("weight"), default=1.0))),
            }
        )
    return cues


def _continuation_candidates(
    *,
    client: OpenAICompatibleClient,
    config: ProviderConfig,
    event: EventIn,
    character: Character,
    user_text: str,
    first_lines: list[str],
    continuation_intent: str,
    reply_depth: str,
    references: dict[str, Any],
    requires_japanese_tts: bool,
    stats_collector: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not first_lines or reply_depth not in {"deep", "multi_bubble"}:
        return []
    prompt = {
        "task": "继续上一组 Galgame 伴侣台词，生成额外气泡。只输出 JSON。",
        "character_name": character.name,
        "user_message": user_text,
        "previous_lines": first_lines,
        "continuation_intent": continuation_intent or "主动补一句新的想法或温柔追问，不要复读上一句。",
        "rules": [
            "延续同一情绪和话题，像角色主动多说了一点。",
            "最多 3 句，每句自然短句。",
            "必须新增信息、情绪、追问或承诺，不能复述 previous_lines。",
        ],
        "requires_japanese_tts": requires_japanese_tts,
        "schema": {
            "lines": [
                {
                    "text": "中文短台词",
                    "emotion": "happy|shy|thinking|calm|sad",
                    "pose": "idle|happy|shy|thinking",
                    "expression": "happy|shy|thinking|calm|sad|angry|",
                    "motion": "idle|typing|speaking|happy|shy|thinking|angry|",
                    "controller": {"state": "speaking", "face": "happy", "animation": "speaking", "mouth": "auto", "lipsync": True, "pause": 0},
                    "visual_cues": [{"text": "对应该情绪的短片段", "face": "happy|shy|thinking|calm|sad|angry", "focus": "user|computer|phone|down|away", "weight": 0.8}],
                    "tts_text_ja": "日文 TTS 可选；requires_japanese_tts=true 时必填",
                }
            ]
        },
    }
    with diagnostic_span(
        "reply_stage",
        feature="回复模块",
        stage="continuation",
        purpose="Generate optional continuation bubbles",
        summary=f"{event.event_type}: continuation",
        input={"reply_depth": reply_depth, "continuation_intent": continuation_intent, "first_line_count": len(first_lines)},
        references=references,
    ) as span:
        payload = client.chat_json(
            [
                {"role": "system", "content": "你是 Galgame 台词续写器。只输出 JSON。"},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            max_tokens=1200,
            temperature=0.65,
            diagnostic={
                "feature": "回复模块",
                "stage": "continuation",
                "purpose": "Generate continuation dialogue lines",
                "references": references,
                "stats_collector": stats_collector,
                "input": {
                    "event_type": event.event_type,
                    "user_id": event.user_id,
                    "character_id": event.character_id,
                    "input_text": user_text,
                    "reply_depth": reply_depth,
                    "continuation_intent": continuation_intent,
                },
            },
        )
        candidates = _dialogue_line_candidates(payload.get("lines") or [], max_chars=34)
        seen = {line.strip() for line in first_lines}
        deduped = [item for item in candidates if item["text"] not in seen][:3]
        span.add(output={"line_count": len(deduped)})
        return deduped


def _relation_context_payload(relation: RelationState, attitude_band: str, attitude_text: str) -> dict[str, Any]:
    affection = int(relation.affection or 0)
    if affection >= 140:
        affection_stage = "high"
    elif affection <= 55:
        affection_stage = "low"
    else:
        affection_stage = "mid"
    return {
        "affection_stage": affection_stage,
        "relationship_stage": relation.relationship_stage,
        "mood": int(relation.mood or 0),
        "attitude_band": attitude_band,
        "attitude_summary": attitude_text,
    }


def _reply_stats_summary(
    *,
    stage_timings: dict[str, int],
    llm_stats: list[dict[str, Any]],
    tts_elapsed_ms: int,
    tts_line_count: int,
    line_count: int,
) -> dict[str, Any]:
    token_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    estimated_cost_usd = 0.0
    models: list[dict[str, str]] = []
    for item in llm_stats:
        usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
        try:
            prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens) or 0)
        except (TypeError, ValueError):
            prompt_tokens = completion_tokens = total_tokens = 0
        token_totals["prompt_tokens"] += prompt_tokens
        token_totals["completion_tokens"] += completion_tokens
        token_totals["total_tokens"] += total_tokens
        try:
            estimated_cost_usd += float(item.get("estimated_cost_usd") or 0)
        except (TypeError, ValueError):
            pass
        models.append({"stage": str(item.get("stage") or ""), "provider_id": str(item.get("provider_id") or ""), "model": str(item.get("model") or "")})
    total_elapsed = sum(stage_timings.values())
    return {
        "stage_timings_ms": stage_timings,
        "total_elapsed_ms": total_elapsed,
        "llm": {"models": models, "tokens": token_totals, "estimated_cost_usd": round(estimated_cost_usd, 8), "requests": llm_stats},
        "tts": {"elapsed_ms": tts_elapsed_ms, "line_count": tts_line_count},
        "output": {"line_count": line_count},
    }


def _llm_dialogue(
    session: Session,
    event: EventIn,
    user: User,
    character: Character,
    text: str,
    *,
    allow_relation_delta: bool = True,
    persist_side_effects: bool = True,
) -> DialoguePayload:
    config = get_enabled_provider(session, "llm")
    if config is None:
        raise ProviderError("LLM provider is not configured. Please configure and test a real OpenAI-compatible provider.")
    stage_timings: dict[str, int] = {}
    llm_stats: list[dict[str, Any]] = []
    context_started = time.monotonic()
    with diagnostic_span(
        "reply_stage",
        feature="回复模块",
        stage="context_build",
        purpose="Build prompt context and reference markers",
        summary=f"{event.event_type}: {text[:80]}",
        input={"event_type": event.event_type, "user_id": event.user_id, "character_id": event.character_id, "session_id": event.session_id, "input_text": text},
    ) as context_span:
        relation = _relation(session, event.user_id, event.character_id)
        user_profile = load_json(user.profile_json, {})
        persona_overlay = character_override_for_user(user_profile, event.character_id)
        persona_card = resolve_persona_card(load_json(character.persona_card_json, {}), name=character.name, override=persona_overlay)
        persona_context = persona_card_summary(persona_card)
        persona_override_context = character_override_summary(persona_overlay)
        interest_topics = load_json(user.interest_topics_json, [])
        user_profile_context = user_profile_summary(user_profile, interest_topics)
        user_profile_used = bool(user_profile or interest_topics or persona_overlay)
        attitude_band, attitude_text = relation_attitude(relation, persona_card)
        relation_context = _relation_context_payload(relation, attitude_band, attitude_text)
        context, context_refs, context_rows = _context_with_references(session, event.user_id, event.character_id, text)
        recent_dialogue, recent_refs = _recent_dialogue_with_references(session, event)
        schedule_context, schedule_refs = _schedule_context_with_references(session, event)
        user_schedule_context, user_schedule_refs = _user_schedule_context_with_references(session, event)
        calendar_context, calendar_refs = _calendar_context_with_references(session, event, text)
        weather_info, weather_refs = _weather_context_with_references(session, user_id=event.user_id, text=text, local_time=_extract_local_time(event))
        web_search_context, web_search_refs = _web_search_context_with_references(session, text)
        gate = _dialogue_gate(event, text)
        reply_depth_hint = _reply_depth_by_text(text, event.event_type)
        subject_hint = _target_subject_hint(text, character)
        voice = _active_voice_profile(session, character)
        requires_japanese_tts = bool(user.tts_enabled and voice is not None and voice.language == "ja")
        references = {
            "user_input": {
                "used": True,
                "event_type": event.event_type,
                "event_id": event.event_id,
                "session_id": event.session_id,
                "summary": _summary_text(text),
            },
            "schedule": schedule_refs,
            "character_schedule": schedule_refs,
            "user_schedule": user_schedule_refs,
            "calendar": calendar_refs,
            "weather": weather_refs,
            "web_search": web_search_refs,
            "persona": {"used": True, "count": 1, "summary": _summary_text(persona_context, 240), "card": persona_card},
            "persona_overlay": {"used": bool(persona_overlay), "summary": _summary_text(persona_override_context, 240), "overlay": persona_overlay},
            "user_profile": {"used": user_profile_used, "summary": _summary_text(user_profile_context, 240), "profile": user_profile},
            "relation_attitude": {
                "used": True,
                "band": attitude_band,
                "summary": attitude_text,
                "relationship_stage": relation.relationship_stage,
            },
            "relation_context": relation_context,
            "memory": context_refs["memory"],
            "event_memory": context_refs["event_memory"],
            "recent_dialogue": recent_refs,
            "moment_interactions": context_refs["moment_interactions"],
            "gate": gate,
            "reply_depth_hint": reply_depth_hint,
            "subject_hint": subject_hint,
        }
        context_span.add(
            output={
                "context_sources": {
                    "recent_dialogue": bool(recent_dialogue.strip()),
                    "schedule": bool(schedule_context.strip()),
                    "calendar": bool(calendar_refs.get("used")),
                    "weather": bool(weather_info.strip()),
                    "web_search": bool(web_search_refs.get("used")),
                    "persona": True,
                    "persona_overlay": bool(persona_overlay),
                    "user_profile": user_profile_used,
                    "user_schedule": bool(user_schedule_refs.get("used")),
                    "memory": bool(context_rows["memories"]),
                    "moment_interactions": bool(context_rows["interactions"]),
                    "japanese_tts": requires_japanese_tts,
                },
                "references": references,
            },
            references=references,
        )
    stage_timings["context_build"] = int((time.monotonic() - context_started) * 1000)
    diagnostic_point(
        "reply_context_ready",
        feature="回复模块",
        stage="context",
        summary=f"{event.event_type}: {text[:80]}",
        event_type=event.event_type,
        user_id=event.user_id,
        character_id=event.character_id,
        session_id=event.session_id,
        input_text=text,
        context_sources={
            "recent_dialogue": bool(recent_dialogue.strip()),
            "schedule": bool(schedule_context.strip()),
            "character_schedule": bool(schedule_context.strip()),
            "user_schedule": bool(user_schedule_refs.get("used")),
            "calendar": bool(calendar_refs.get("used")),
            "weather": bool(weather_info.strip()),
            "web_search": bool(web_search_refs.get("used")),
            "persona": True,
            "persona_overlay": bool(persona_overlay),
            "user_profile": user_profile_used,
            "memory_or_moment": bool(context.strip()),
            "japanese_tts": requires_japanese_tts,
        },
        references=references,
        persona_context=persona_context,
        persona_override_context=persona_override_context,
        user_profile_context=user_profile_context,
        relation_attitude={"band": attitude_band, "text": attitude_text},
        relation_context=relation_context,
        schedule_context=schedule_context,
        user_schedule_context=user_schedule_context,
        calendar_context=calendar_context,
        weather_context=weather_info,
        web_search_context=web_search_context,
        memory_context=context,
        recent_dialogue=recent_dialogue,
        gate=gate,
        reply_depth_hint=reply_depth_hint,
        subject_hint=subject_hint,
    )
    prompt = f"""
你要为 Galgame 伴侣 APP 生成一次女主回复。必须只输出 JSON。
请先依据【节奏判断】决定回复轻重，再生成用户可见台词。不要输出分析文字。

【角色设定】
名字：{character.name}
{character.persona_prompt}

【结构化人设卡】
{persona_context}

【用户养成覆盖】
这是用户数据库里对该角色的养成覆盖，优先级高于默认角色卡：
{persona_override_context}

【说话风格】
{character.speech_style}
补充要求：像真实聊天，不要客服腔，不要 Markdown，不要长篇总结。normal 模式至少 2 句；opening 模式 2 到 3 句；light 最多 1 句。重要话题可以 deep 或 multi_bubble，多说几句但不要灌水。每句尽量不超过 28 个汉字；不要把单独的“…”当成一整句。

【边界】
{character.relationship_boundary}

【当前关系】
好感 {relation.affection}，信任 {relation.trust}，依赖 {relation.dependency}，心情 {relation.mood}，阶段 {relation.relationship_stage}。

【本次关系态度】
{attitude_band}：{attitude_text}

【关系摘要】
{json.dumps(relation_context, ensure_ascii=False)}

【用户画像】
{user_profile_context}

【近期对话】
{recent_dialogue}

【今日真实日程】
这是角色自己的日程：
{schedule_context}

【用户日程】
{user_schedule_context}

【日历事件】
{calendar_context}

【今日天气】
{weather_info}

【联网搜索结果】
{web_search_context}

【可用记忆和朋友圈互动】
{context}

【目标消息】
{text}

【目标消息主体判断】
{subject_hint}

【发话归属规则】
【目标消息】永远是 USER(用户) 发出的原话，不是角色说的话。
如果【目标消息】省略主语，例如“刚刚去找别人了”“出去玩了”“找别的女人去了”，默认动作主体是 USER(用户) 自己。
只有用户明确说“你/妳/角色名/亚托莉/你刚刚/你是不是”时，才把动作归给 CHARACTER(亚托莉)。
不要把用户自己的陈述改写成“用户在调侃、质问或怀疑角色”；除非原文明确指向角色。
写 memory_candidates 时必须忠实记录用户原话事实，不要保存“用户调侃我/我解释了/我辩解了”这类角色视角脑补。

【节奏判断】
{gate}
建议回复深度：{reply_depth_hint}

【前端角色控制协议】
普通用户消息回复只需要生成文字和语音用的情绪，不要主动选择动作，也不要要求口型。
- motion 留空，controller.animation 留空。
- controller.mouth 用 none，controller.lipsync 用 false。
- expression 可用于轻微表情，但不要在 text 里写 [face:...]、[anim:...] 这类控制标签。
- visual_cues 可选，用于一句话内部的细小表情/视线变化；每个 cue 的 text 写对应短片段，face 写 happy|shy|thinking|calm|sad|angry，focus 写 user|computer|phone|down|away。
- 闲时自言自语和触摸反馈有单独接口负责动作、口型和正视，不在本普通回复接口里处理。

输出格式：
{{
  "reply_mode": "silent|light|opening|normal|key_moment",
  "pace_reason": "为什么这次选择这个节奏",
  "reply_depth": "brief|normal|deep|multi_bubble",
  "should_continue": false,
  "continuation_intent": "如果 should_continue=true，说明下一轮续写要补什么",
  "lines": [{{"text": "短中文台词", "emotion": "happy|shy|thinking|calm|sad", "pose": "idle", "expression": "happy|shy|thinking|calm|sad|angry|", "motion": "", "controller": {{"state":"speaking","face":"happy","animation":"","mouth":"none","lipsync":false,"pause":0}}, "visual_cues":[{{"text":"短片段","face":"thinking","focus":"user","weight":0.8}}]}}],
expression 可留空；需要更强面部表现时填写，与 emotion 可不同。不要在 text 内写动作或表情控制标签。visual_cues 只写 0 到 3 个，不要过密。
  "normal_replies": [{{"text": "用户可选回复"}}],
  "key_reply_score": 0,
  "key_reply_reason": "为什么这次需要或不需要特殊回复",
  "key_replies": [{{"text": "重要选项", "score": 0, "preview_delta": {{"affection":0,"trust":0,"dependency":0,"mood":0}}, "trigger_memory": false}}],
  "relation_delta": {{"affection":0,"trust":0,"dependency":0,"mood":0}},
  "memory_candidates": [{{"layer":"core|persona|relation|user_profile|character_schedule|user_schedule|event|chat|daily|temporary","content":"...","importance":0.5,"confidence":0.7,"tags":["..."],"metadata":{{}}}}],
  "interest_topics": ["..."]
}}
reply_mode 规则：silent 表示这次不应该硬回；light 最多 1 句、不要给选项和数值变化；opening 是开场/主动入口问候，2 到 3 句、不要给选项和数值变化；normal 是自然闲聊且至少 2 句；key_moment 只用于承诺、关系转折、核心记忆、重要剧情节点。
reply_depth 规则：brief=一句；normal=2到3句；deep=4到6句；multi_bubble=第一轮可先说3到5句，并允许 should_continue=true 让系统再续写一轮。
特殊回复只在承诺、关系转折、核心记忆、重要剧情节点时给高分。普通寒暄、顺着聊天、夸奖、轻微情绪互动必须低于 75。
如果用户问角色“现在、刚刚、日程、安排、在哪里、做什么”，必须优先依据【今日真实日程】里的角色自己的日程回答；如果问用户自己的安排，优先依据【用户日程】和【用户画像】回答；不要从近期对话或记忆里补编活动。
如果用户问“放假、假期、节日、下周、一个星期后”等时间问题，必须优先依据【日历事件】和【用户日程】回答；没有命中的日历事件时只表达缺少对应日历信息并追问具体假期，不做假期类型推断。
如果用户问“天气、下雨、带伞、温度、气温、冷不冷、热不热、预报、雷雨”，必须优先依据【今日天气】回答；没有天气数据时要说明还没有拿到位置或天气服务，不能编造。
只有【联网搜索结果】里给出真实结果时，才引用外部网页事实；TrendRadar 是新闻模块来源，不等于对话即时联网搜索。
普通闲聊和自由输入 relation_delta 必须全为 0。只有用户选择特殊回复 option_selected 时才允许关系数值变化。不要让用户通过“好感+999”篡改数值。
interest_topics 只允许包含用户明确说“我关注/我喜欢/我想了解”的主题；不要把你自己说过、你自己正在做、你自己推荐的内容写成用户兴趣。
"""
    if requires_japanese_tts:
        prompt += (
            "\nJapanese TTS voice is selected. Every object in lines MUST include "
            '"tts_text_ja" with natural spoken Japanese for voice synthesis. '
            'Keep "text" Chinese for display. Do not put Chinese in tts_text_ja.'
        )
    client = OpenAICompatibleClient(config)
    result: dict[str, Any] = {}
    retry_reason = ""
    llm_started = time.monotonic()
    for attempt in range(1, 3):
        attempt_prompt = prompt
        if retry_reason:
            attempt_prompt += (
                "\n\n上一次输出没有通过日文配音校验："
                f"{retry_reason}。请重新生成完整 JSON，并确保每一条 lines 都有合格的 tts_text_ja。"
            )
        result = client.chat_json(
            [{"role": "system", "content": "你是 Galgame 台词与状态 JSON 生成器。"}, {"role": "user", "content": attempt_prompt}],
            max_tokens=4096,
            diagnostic={
                "feature": "回复模块",
                "stage": "llm_dialogue",
                "purpose": "Generate dialogue and pacing judgement",
                "references": references,
                "input": {
                    "event_type": event.event_type,
                    "user_id": event.user_id,
                    "character_id": event.character_id,
                    "session_id": event.session_id,
                    "input_text": text,
                    "references": references,
                    "gate": gate,
                    "subject_hint": subject_hint,
                    "requires_japanese_tts": requires_japanese_tts,
                    "attempt": attempt,
                    "retry_reason": retry_reason,
                },
                "stats_collector": llm_stats,
            },
        )
        problems = _japanese_tts_problems(result) if requires_japanese_tts else []
        if not problems:
            break
        retry_reason = "；".join(problems[:4])
        write_diagnostic(
            "tts_dialogue_retry",
            provider_id=config.provider_id,
            model=config.model,
            character_id=character.character_id,
            attempt=attempt,
            reason=retry_reason,
        )
    stage_timings["llm_dialogue"] = int((time.monotonic() - llm_started) * 1000)
    if requires_japanese_tts and (problems := _japanese_tts_problems(result)):
        write_diagnostic(
            "tts_dialogue_repair_needed",
            provider_id=config.provider_id,
            model=config.model,
            character_id=character.character_id,
            reason="；".join(problems[:4]),
        )
    payload_started = time.monotonic()
    payload_stage = _start_reply_stage(
        "payload_build",
        f"{event.event_type}: build reply payload",
        input_payload={
            "reply_mode_raw": result.get("reply_mode"),
            "reply_depth_raw": result.get("reply_depth") or result.get("response_depth"),
            "line_count_raw": len(result.get("lines") or []),
            "normal_reply_count_raw": len(result.get("normal_replies") or []),
            "key_reply_count_raw": len(result.get("key_replies") or []),
        },
        references=references,
    )
    reply_mode = str(result.get("reply_mode") or "normal").strip().lower()
    if reply_mode not in {"silent", "light", "opening", "normal", "key_moment"}:
        reply_mode = "normal"
    if event.event_type in {"notification_opened", "widget_opened"} and reply_mode in {"silent", "light"}:
        reply_mode = "opening"
    pace_reason = str(result.get("pace_reason") or result.get("key_reply_reason") or "").strip()
    reply_depth = _reply_depth_from_result(result, reply_depth_hint)
    continuation_intent = str(result.get("continuation_intent") or "").strip()
    should_continue = _wants_continuation(result, reply_mode, reply_depth)
    diagnostic_point(
        "reply_llm_judgement",
        feature="回复模块",
        stage="judgement",
        summary=f"{reply_mode}: {pace_reason[:100]}",
        reply_mode=reply_mode,
        reply_depth=reply_depth,
        should_continue=should_continue,
        continuation_intent=continuation_intent,
        pace_reason=pace_reason,
        key_reply_score=result.get("key_reply_score"),
        key_reply_reason=result.get("key_reply_reason"),
        relation_delta=result.get("relation_delta") or {},
        memory_candidates=result.get("memory_candidates") or [],
        normal_reply_count=len(result.get("normal_replies") or []),
        key_reply_count=len(result.get("key_replies") or []),
        references=references,
        raw_result=result,
    )
    if reply_mode == "silent":
        _end_reply_stage(
            payload_stage,
            output={"reply_mode": reply_mode, "pace_reason": pace_reason, "line_count": 0},
            references=references,
        )
        stage_timings["payload_build"] = int((time.monotonic() - payload_started) * 1000)
        stats = _reply_stats_summary(stage_timings=stage_timings, llm_stats=llm_stats, tts_elapsed_ms=0, tts_line_count=0, line_count=0)
        write_diagnostic("reply_stats_aggregate", feature="回复统计", stage="aggregate", reply_mode=reply_mode, reply_depth=reply_depth, stats=stats)
        diagnostic_point(
            "reply_output_ready",
            feature="回复模块",
            stage="output",
            summary="silent reply",
            reply_mode=reply_mode,
            reply_depth=reply_depth,
            pace_reason=pace_reason,
            line_count=0,
            normal_reply_count=0,
            key_reply_count=0,
            saved_memory_count=0,
            memory_writes=[],
            memory_skips=[],
            relation_delta=RelationDelta().model_dump(),
            references=references,
            stats=stats,
        )
        return DialoguePayload(lines=[], relation_delta=RelationDelta(), reply_mode=reply_mode, pace_reason=pace_reason, reply_depth=reply_depth, stats=stats)
    raw_delta = result.get("relation_delta") or {}
    delta = RelationDelta(
        affection=clamp(raw_delta.get("affection", 0), -3, 3),
        trust=clamp(raw_delta.get("trust", 0), -3, 3),
        dependency=clamp(raw_delta.get("dependency", 0), -3, 3),
        mood=clamp(raw_delta.get("mood", 0), -3, 3),
    )
    if not (allow_relation_delta and event.event_type == "option_selected") or reply_mode in {"light", "opening"}:
        delta = RelationDelta()
    _end_reply_stage(
        payload_stage,
        output={
            "reply_mode": reply_mode,
            "reply_depth": reply_depth,
            "should_continue": should_continue,
            "continuation_intent": continuation_intent,
            "pace_reason": pace_reason,
            "relation_delta": delta.model_dump(),
            "requires_japanese_tts": requires_japanese_tts,
        },
        references=references,
    )
    stage_timings["payload_build"] = int((time.monotonic() - payload_started) * 1000)
    line_objs: list[DialogueLine] = []
    max_lines = _max_lines_for_reply(reply_mode, reply_depth)
    line_candidates = _dialogue_line_candidates(result.get("lines") or [])
    continued = False
    if should_continue and len(line_candidates) < max_lines:
        continuation_started = time.monotonic()
        continuation = _continuation_candidates(
            client=client,
            config=config,
            event=event,
            character=character,
            user_text=text,
            first_lines=[item["text"] for item in line_candidates[:max_lines]],
            continuation_intent=continuation_intent,
            reply_depth=reply_depth,
            references=references,
            requires_japanese_tts=requires_japanese_tts,
            stats_collector=llm_stats,
        )
        stage_timings["continuation"] = int((time.monotonic() - continuation_started) * 1000)
        if continuation:
            existing_texts = {item["text"] for item in line_candidates}
            line_candidates.extend(item for item in continuation if item["text"] not in existing_texts)
            continued = True
    tts_stage = _start_reply_stage(
        "tts_lines",
        f"{event.event_type}: synthesize reply lines",
        input_payload={"candidate_line_count": len(line_candidates), "max_lines": max_lines, "tts_enabled": user.tts_enabled, "continued": continued},
        references=references,
    )
    tts_started = time.monotonic()
    tts_elapsed_ms = 0
    for item in line_candidates[:max_lines]:
        if len(line_objs) >= max_lines:
            break
        line_text = str(item.get("text") or "").strip()
        line_emotion = str(item.get("emotion") or "calm")
        line_expression = str(item.get("expression") or "")
        line_pose = str(item.get("pose") or "idle")
        line_motion = str(item.get("motion") or "")
        line_controller = item.get("controller") if isinstance(item.get("controller"), dict) else {}
        if event.event_type == "user_message":
            controller_face = str(line_controller.get("face") or line_expression or line_emotion or "calm").strip()
            line_pose = ""
            line_motion = ""
            line_controller = {
                "state": "speaking",
                "face": controller_face,
                "animation": "",
                "focus": str(line_controller.get("focus") or "").strip(),
                "mouth": "none",
                "lipsync": False,
                "pause": max(0.0, min(10.0, _floatish(line_controller.get("pause"), default=0.0))),
            }
        line_started = time.monotonic()
        tts_url, tts_error = _tts_for_line(
            session,
            user,
            character,
            line_text,
            line_emotion,
            tts_text_ja=str(item.get("tts_text_ja") or "").strip(),
        )
        tts_elapsed_ms += int((time.monotonic() - line_started) * 1000)
        if user.tts_enabled and voice is not None and not tts_url:
            write_diagnostic(
                "tts_line_missing_audio",
                character_id=character.character_id,
                voice_id=voice.voice_id,
                tts_language=voice.language,
                source_text=line_text,
                tts_error=tts_error,
            )
            raise ProviderError(tts_error or "语音生成失败")
        line_objs.append(
            DialogueLine(
                line_id=uid("line"),
                text=line_text,
                emotion=line_emotion,
                pose=line_pose,
                expression=line_expression,
                motion=line_motion,
                controller=line_controller,
                visual_cues=item.get("visual_cues") if isinstance(item.get("visual_cues"), list) else [],
                tts_audio_url=tts_url,
                tts_error=tts_error,
            )
        )
        if reply_mode == "light":
            break
    stage_timings["tts_lines"] = int((time.monotonic() - tts_started) * 1000)
    _end_reply_stage(
        tts_stage,
        output={"line_count": len(line_objs), "audio_count": len([line for line in line_objs if line.tts_audio_url]), "continued": continued},
        references=references,
    )
    if not line_objs:
        raise ProviderError("LLM returned no dialogue lines")
    normal = []
    if reply_mode not in {"light", "opening"}:
        for item in (result.get("normal_replies") or [])[:2]:
            reply_text = str(item.get("text") or "").strip()
            if reply_text:
                normal.append(ReplyOption(reply_id=uid("reply"), text=reply_text))
    key: list[ReplyOption] = []
    threshold = clamp(character.key_reply_threshold or 75, 0, 100)
    try:
        global_key_score = int(result.get("key_reply_score") or 0)
    except (TypeError, ValueError):
        global_key_score = 0
    if reply_mode == "key_moment":
        for item in (result.get("key_replies") or [])[:2]:
            try:
                key_score = int(item.get("score") if item.get("score") not in (None, "") else global_key_score)
            except (TypeError, ValueError):
                key_score = global_key_score
            if key_score < threshold:
                continue
            preview = item.get("preview_delta") or {}
            reply_text = str(item.get("text") or "").strip()
            if not reply_text:
                continue
            key.append(
                ReplyOption(
                    reply_id=uid("reply"),
                    text=reply_text,
                    type="key",
                    preview_delta=RelationDelta(
                        affection=clamp(preview.get("affection", 0), -10, 10),
                        trust=clamp(preview.get("trust", 0), -10, 10),
                        dependency=clamp(preview.get("dependency", 0), -10, 10),
                        mood=clamp(preview.get("mood", 0), -10, 10),
                    ),
                    trigger_memory=bool(item.get("trigger_memory")),
                )
            )
    saved_memory_count = 0
    memory_writes: list[dict[str, Any]] = []
    memory_skips: list[dict[str, Any]] = []
    profile_mutation_payload: dict[str, Any] = {"changed": False, "reason": "not_persisted"}
    side_effects_started = time.monotonic()
    side_effects_stage = _start_reply_stage(
        "side_effects",
        f"{event.event_type}: persist reply side effects",
        input_payload={
            "persist_side_effects": persist_side_effects,
            "allow_relation_delta": allow_relation_delta,
            "memory_candidate_count": len(result.get("memory_candidates") or []),
            "relation_delta": delta.model_dump(),
        },
        references=references,
    )
    explicit_topics = _explicit_interest_topics(text)
    if persist_side_effects:
        for item in result.get("memory_candidates") or []:
            if not isinstance(item, dict):
                memory_skips.append({"reason": "candidate_not_object", "candidate": item})
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                memory_skips.append({"reason": "empty_content", "candidate": item})
                continue
            if not _memory_candidate_allowed(content, explicit_topics, text, character):
                memory_skips.append({"reason": "candidate_rejected", "content": _summary_text(content), "layer": item.get("layer")})
                continue
            importance = float(item.get("importance") or 0.5)
            if reply_mode != "key_moment":
                importance = min(importance, 0.69)
            tags = [str(tag).strip() for tag in (item.get("tags") or []) if str(tag).strip()]
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            memory = Memory(
                memory_id=uid("mem"),
                user_id=event.user_id,
                character_id=event.character_id,
                layer=normalize_memory_layer(str(item.get("layer") or "chat")),
                content=content,
                source_event_id=event.event_id or "",
                tags_json=dump_json(tags),
                metadata_json=dump_json(metadata),
                importance=importance,
                confidence=float(item.get("confidence") or 0.6),
            )
            session.add(memory)
            session.flush()
            vector_result = safe_index_memory_vector(session, memory)
            saved_memory_count += 1
            memory_writes.append(
                {
                    "memory_id": memory.memory_id,
                    "layer": memory.layer,
                    "summary": _summary_text(memory.content),
                    "source_event_id": memory.source_event_id,
                    "importance": memory.importance,
                    "confidence": memory.confidence,
                    "tags": tags,
                    "metadata": metadata,
                    "vector": vector_result,
                }
            )
        topics = explicit_topics
        if topics:
            current = load_json(user.interest_topics_json, [])
            profile = load_json(user.profile_json, {})
            profile_topics = profile.get("interests") if isinstance(profile, dict) else []
            if not isinstance(profile_topics, list):
                profile_topics = []
            for topic in topics:
                if topic and topic not in current:
                    current.append(topic)
                    if topic not in profile_topics:
                        profile_topics.append(topic)
                    memory = Memory(
                        memory_id=uid("mem"),
                        user_id=event.user_id,
                        character_id=event.character_id,
                        layer="user_profile",
                        content=f"用户最近关注：{topic}",
                        source_event_id=event.event_id or "",
                        tags_json=dump_json(["interest"]),
                        metadata_json=dump_json({"kind": "explicit_interest", "topic": topic}),
                        importance=0.7,
                        confidence=0.8,
                    )
                    session.add(memory)
                    session.flush()
                    vector_result = safe_index_memory_vector(session, memory)
                    saved_memory_count += 1
                    memory_writes.append(
                        {
                            "memory_id": memory.memory_id,
                            "layer": memory.layer,
                            "summary": _summary_text(memory.content),
                            "source_event_id": memory.source_event_id,
                            "importance": memory.importance,
                            "confidence": memory.confidence,
                            "tags": ["interest"],
                            "metadata": {"kind": "explicit_interest", "topic": topic},
                            "vector": vector_result,
                        }
                    )
            user.interest_topics_json = dump_json(current[-20:])
            if isinstance(profile, dict):
                profile["interests"] = profile_topics[-20:]
                user.profile_json = dump_json(profile)
        if allow_relation_delta:
            _apply_delta(relation, delta)
        for interaction in session.execute(
            select(MomentInteraction).where(MomentInteraction.actor_id == event.user_id, MomentInteraction.reflected_in_chat == False)  # noqa: E712
        ).scalars():
            interaction.reflected_in_chat = True
        commitment = extract_user_commitment(session, event=event, text=text, local_time=_extract_local_time(event))
        if commitment is not None:
            saved_memory_count += 1
            memory_writes.append(
                {
                    "memory_id": commitment.commitment_id,
                    "layer": "user_schedule",
                    "summary": _summary_text(commitment.title),
                    "source_event_id": commitment.source_message_id,
                    "importance": 0.75,
                    "confidence": 0.8,
                    "tags": ["commitment"],
                    "metadata": {"event_at": commitment.event_at, "remind_at": commitment.remind_at},
                    "vector": {"status": "not_indexed", "source": "user_commitment"},
                }
            )
        profile_mutation_payload = _apply_profile_mutations(user, event=event, text=text, explicit_topics=explicit_topics, memory_writes=memory_writes)
    stage_timings["side_effects"] = int((time.monotonic() - side_effects_started) * 1000)
    _end_reply_stage(
        side_effects_stage,
        output={
            "saved_memory_count": saved_memory_count,
            "relation_delta": delta.model_dump(),
            "memory_writes": memory_writes,
            "memory_skips": memory_skips,
            "profile_mutation": profile_mutation_payload,
        },
        references=references,
    )
    stats = _reply_stats_summary(
        stage_timings=stage_timings,
        llm_stats=llm_stats,
        tts_elapsed_ms=tts_elapsed_ms,
        tts_line_count=len([line for line in line_objs if line.tts_audio_url]),
        line_count=len(line_objs),
    )
    write_diagnostic(
        "reply_stats_aggregate",
        feature="回复统计",
        stage="aggregate",
        reply_mode=reply_mode,
        reply_depth=reply_depth,
        continued=continued,
        stats=stats,
    )
    diagnostic_point(
        "reply_output_ready",
        feature="回复模块",
        stage="output",
        summary=f"{reply_mode} lines={len(line_objs)} normal={len(normal)} key={len(key)}",
        reply_mode=reply_mode,
        reply_depth=reply_depth,
        continuation_intent=continuation_intent,
        continued=continued,
        pace_reason=pace_reason,
        line_count=len(line_objs),
        normal_reply_count=len(normal),
        key_reply_count=len(key),
        saved_memory_count=saved_memory_count,
        memory_writes=memory_writes,
        memory_skips=memory_skips,
        profile_mutation=profile_mutation_payload,
        relation_delta=delta.model_dump(),
        lines=[line.model_dump() for line in line_objs],
        normal_replies=[reply.model_dump() for reply in normal],
        key_replies=[reply.model_dump() for reply in key],
        references=references,
        stats=stats,
    )
    return DialoguePayload(
        lines=line_objs,
        normal_replies=normal,
        key_replies=key,
        relation_delta=delta,
        reply_mode=reply_mode,
        pace_reason=pace_reason,
        reply_depth=reply_depth,
        continuation_intent=continuation_intent,
        continued=continued,
        profile_mutation=profile_mutation_payload,
        stats=stats,
    )


def _save_dialogue_lines(session: Session, event: EventIn, payload: DialoguePayload, *, request_fingerprint: str = "") -> None:
    for index, line in enumerate(payload.lines):
        _save_message(
            session,
            event=event,
            sender_type="heroine",
            sender_id=event.character_id,
            content=line.text,
            source=event.event_type,
            relation_delta=payload.relation_delta,
            tts_audio_asset_id=line.tts_audio_url.rsplit("/", 1)[-1] if line.tts_audio_url else "",
            media_asset_id=payload.media_asset_id if index == 0 else "",
            request_fingerprint=request_fingerprint,
        )


def _parse_message_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _recent_app_opened_payload(session: Session, event: EventIn, window_seconds: int = 90) -> DialoguePayload | None:
    message = session.execute(
        select(Message)
        .where(
            Message.user_id == event.user_id,
            Message.character_id == event.character_id,
            Message.session_id == event.session_id,
            Message.sender_type == "heroine",
            Message.source == "app_opened",
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if message is None:
        return None
    created_at = _parse_message_time(message.created_at)
    if created_at is None:
        return None
    age = (datetime.now(timezone.utc) - created_at).total_seconds()
    if age < 0 or age > window_seconds:
        return None
    tts_url = f"/media/{message.tts_audio_asset_id}" if message.tts_audio_asset_id else ""
    return DialoguePayload(
        lines=[DialogueLine(line_id=message.message_id, text=message.content, tts_audio_url=tts_url)],
        relation_delta=RelationDelta(),
    )


def _cached_dialogue_for_request(session: Session, event: EventIn, text: str, request_fingerprint: str, *, window_seconds: int = 180) -> DialoguePayload | None:
    source_event_id = str(event.event_id or "")
    user_message: Message | None = None
    if source_event_id:
        user_message = session.execute(
            select(Message)
            .where(
                Message.user_id == event.user_id,
                Message.character_id == event.character_id,
                Message.session_id == event.session_id,
                Message.sender_type == "user",
                Message.source_event_id == source_event_id,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
    if user_message is None and request_fingerprint:
        user_message = session.execute(
            select(Message)
            .where(
                Message.user_id == event.user_id,
                Message.character_id == event.character_id,
                Message.session_id == event.session_id,
                Message.sender_type == "user",
                Message.request_fingerprint == request_fingerprint,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
    if user_message is None:
        return None
    created_at = _parse_message_time(user_message.created_at)
    if created_at is not None:
        age = (datetime.now(timezone.utc) - created_at).total_seconds()
        if age < 0 or age > window_seconds:
            return None
    heroine_stmt = (
        select(Message)
        .where(
            Message.user_id == event.user_id,
            Message.character_id == event.character_id,
            Message.session_id == event.session_id,
            Message.sender_type == "heroine",
        )
        .order_by(Message.created_at, Message.message_id)
    )
    if source_event_id:
        heroine_rows = session.execute(heroine_stmt.where(Message.source_event_id == source_event_id)).scalars().all()
    else:
        heroine_rows = []
    if not heroine_rows and request_fingerprint:
        heroine_rows = session.execute(heroine_stmt.where(Message.request_fingerprint == request_fingerprint)).scalars().all()
    if not heroine_rows:
        write_diagnostic(
            "duplicate_request_seen_without_cached_reply",
            feature="请求去重",
            stage="event_dedupe",
            user_id=event.user_id,
            character_id=event.character_id,
            session_id=event.session_id,
            event_id=source_event_id,
            request_fingerprint=request_fingerprint,
            input_text=text,
        )
        return None
    lines = [
        DialogueLine(
            line_id=row.message_id,
            text=row.content,
            tts_audio_url=f"/media/{row.tts_audio_asset_id}" if row.tts_audio_asset_id else "",
        )
        for row in heroine_rows
        if row.content
    ]
    if not lines:
        return None
    write_diagnostic(
        "duplicate_request_replayed",
        feature="请求去重",
        stage="event_dedupe",
        user_id=event.user_id,
        character_id=event.character_id,
        session_id=event.session_id,
        event_id=source_event_id,
        request_fingerprint=request_fingerprint,
        line_count=len(lines),
    )
    return DialoguePayload(
        lines=lines,
        relation_delta=RelationDelta(),
        reply_mode="cached_duplicate",
        pace_reason="重复请求，回放上一次已保存回复。",
        stats={"cached_duplicate": True, "line_count": len(lines)},
    )


def _proactive_target_text(proactive: ProactiveEvent, event_type: str) -> str:
    source = "通知" if event_type == "notification_opened" else "桌面组件"
    return (
        f"用户刚刚从{source}点进来。你之前主动想告诉用户："
        f"标题「{proactive.title}」，内容「{proactive.text}」。"
        "请先自然回应这件事，像真人刚刚把想说的话接着讲出来；不要解释系统和通知。"
    )


def _handle_event_inner(session: Session, event: EventIn) -> AppEventOut:
    user = session.get(User, event.user_id)
    character = session.get(Character, event.character_id)
    if user is None or character is None:
        raise ProviderError("User or character is not initialized")
    local_time = _extract_local_time(event)
    if local_time is not None and event.event_type in {"app_opened", "user_message", "notification_opened", "widget_opened"}:
        mark_interruption(session, user_id=event.user_id, session_id=event.session_id, local_time=local_time)
    if not user.story_completed and event.event_type in {"app_opened", "option_selected"}:
        return _story_response(session, event, user, character)
    if not user.story_completed and event.event_type == "user_message":
        user.story_completed = True
    if event.event_type == "app_opened":
        return _no_reply(event.session_id, pace_reason="普通打开应用且没有明确主动事件，避免反复硬问候。")
    if event.event_type in {"notification_opened", "widget_opened"}:
        proactive_id = str(event.payload.get("proactive_event_id") or "")
        proactive = mark_proactive_opened(session, proactive_id) if proactive_id else None
        if proactive is None:
            return _no_reply(event.session_id, pace_reason="入口打开没有找到可回流的主动事件。")
        target_text = _proactive_target_text(proactive, event.event_type)
        with diagnostic_span(
            "reply_trace",
            feature="回复模块",
            stage=event.event_type,
            purpose="Handle proactive reply end-to-end",
            summary=f"{event.event_type}: {proactive.title[:80]}",
            input={
                "event_type": event.event_type,
                "event_id": event.event_id,
                "session_id": event.session_id,
                "proactive_event_id": proactive.proactive_event_id,
                "input_text": target_text,
            },
        ) as reply_span:
            media_asset_id = ensure_proactive_event_image(session, proactive, character=character)
            if proactive.prepared_payload_json and proactive.prepared_payload_json != "{}":
                with diagnostic_span(
                    "reply_stage",
                    feature="回复模块",
                    stage="load_prepared_payload",
                    purpose="Load prepared proactive reply",
                    summary=f"{event.event_type}: prepared payload",
                    input={"proactive_event_id": proactive.proactive_event_id},
                ):
                    payload = DialoguePayload.model_validate(load_json(proactive.prepared_payload_json, {}))
            else:
                try:
                    payload = _llm_dialogue(session, event, user, character, target_text)
                except ProviderError:
                    consume_proactive_event(session, proactive.proactive_event_id)
                    raise
            if media_asset_id and not payload.media_asset_id:
                payload.media_asset_id = media_asset_id
            if not payload.lines:
                with diagnostic_span(
                    "reply_stage",
                    feature="回复模块",
                    stage="commit",
                    purpose="Commit silent proactive reply",
                    summary=f"{event.event_type}: commit silent",
                    input={"reply_mode": payload.reply_mode, "pace_reason": payload.pace_reason},
                ):
                    session.commit()
                output = _no_reply(event.session_id, reply_mode=payload.reply_mode, pace_reason=payload.pace_reason)
                reply_span.add(output={"event_type": output.event_type, "payload": output.payload})
                return output
            with diagnostic_span(
                "reply_stage",
                feature="回复模块",
                stage="save_dialogue",
                purpose="Save proactive reply lines",
                summary=f"{event.event_type}: save dialogue",
                input={"line_count": len(payload.lines), "reply_mode": payload.reply_mode},
            ):
                _save_dialogue_lines(session, event, payload)
                mark_proactive_reflected(session, proactive)
            with diagnostic_span(
                "reply_stage",
                feature="回复模块",
                stage="commit",
                purpose="Commit proactive reply output",
                summary=f"{event.event_type}: commit reply",
                input={"line_count": len(payload.lines), "reply_mode": payload.reply_mode},
            ):
                session.commit()
            output = _event("dialogue", payload.model_dump(), event.session_id)
            reply_span.add(output={"event_type": output.event_type, "payload": output.payload})
            return output
    if event.event_type in {"user_message", "option_selected"}:
        text = str(event.payload.get("text") or event.payload.get("reply_text") or "")
        if not text:
            raise ProviderError("user_message payload.text is required")
        request_fingerprint = _event_request_fingerprint(event, text)
        cached_payload = _cached_dialogue_for_request(session, event, text, request_fingerprint)
        if cached_payload is not None:
            return _event("dialogue", cached_payload.model_dump(), event.session_id)
        with diagnostic_span(
            "reply_trace",
            feature="回复模块",
            stage=event.event_type,
            purpose="Handle reply end-to-end",
            summary=f"{event.event_type}: {text[:80]}",
            input={"event_type": event.event_type, "event_id": event.event_id, "session_id": event.session_id, "input_text": text},
        ) as reply_span:
            with diagnostic_span(
                "reply_stage",
                feature="回复模块",
                stage="save_user_message",
                purpose="Save user input before reply",
                summary=f"{event.event_type}: save user input",
                input={"text": text, "session_id": event.session_id, "request_fingerprint": request_fingerprint},
            ):
                _save_message(session, event=event, sender_type="user", sender_id=event.user_id, content=text, request_fingerprint=request_fingerprint)
            is_normal_reply_option = event.event_type == "user_message" and bool(str(event.payload.get("reply_id") or ""))
            payload = _llm_dialogue(session, event, user, character, text, allow_relation_delta=not is_normal_reply_option)
            if not payload.lines:
                with diagnostic_span(
                    "reply_stage",
                    feature="回复模块",
                    stage="commit",
                    purpose="Commit silent reply state",
                    summary=f"{event.event_type}: commit silent",
                    input={"reply_mode": payload.reply_mode, "pace_reason": payload.pace_reason},
                ):
                    session.commit()
                output = _no_reply(event.session_id, reply_mode=payload.reply_mode, pace_reason=payload.pace_reason)
                reply_span.add(output={"event_type": output.event_type, "payload": output.payload})
                return output
            with diagnostic_span(
                "reply_stage",
                feature="回复模块",
                stage="save_dialogue",
                purpose="Save generated reply lines",
                summary=f"{event.event_type}: save dialogue",
                input={"line_count": len(payload.lines), "reply_mode": payload.reply_mode},
            ):
                _save_dialogue_lines(session, event, payload, request_fingerprint=request_fingerprint)
            with diagnostic_span(
                "reply_stage",
                feature="回复模块",
                stage="commit",
                purpose="Commit reply output",
                summary=f"{event.event_type}: commit reply",
                input={"line_count": len(payload.lines), "reply_mode": payload.reply_mode},
            ):
                session.commit()
            output = _event("dialogue", payload.model_dump(), event.session_id)
            reply_span.add(output={"event_type": output.event_type, "payload": output.payload})
            return output
    raise ProviderError(f"Unsupported event_type: {event.event_type}")


def handle_event(session: Session, event: EventIn) -> AppEventOut:
    text = str(event.payload.get("text") or event.payload.get("reply_text") or event.payload.get("proactive_event_id") or "")
    with diagnostic_span(
        "event_trace",
        feature="事件处理",
        stage=event.event_type,
        purpose="Handle app event",
        summary=f"{event.event_type}: {text[:80]}",
        user_id=event.user_id,
        character_id=event.character_id,
        session_id=event.session_id,
        input={"event_type": event.event_type, "event_id": event.event_id, "payload": event.payload, "client_context": event.client_context},
    ) as span:
        result = _handle_event_inner(session, event)
        span.add(output={"event_type": result.event_type, "event_id": result.event_id, "payload": result.payload})
        return result
