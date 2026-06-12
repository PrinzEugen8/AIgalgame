from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .character_profiles import resolve_character_profile
from .commitments import extract_user_commitment
from .context_planner import build_context_plan, context_plan_reference, user_profile_has_content
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
    normalize_memory_layer,
    persona_card_summary,
    relation_attitude,
    relationship_state_summary,
    sync_relationship_stage,
    user_profile_summary,
)
from .proactive import consume_proactive_event, ensure_proactive_event_image, mark_proactive_opened, mark_proactive_reflected
from .providers import OpenAICompatibleClient, ProviderError, VolcArkWebSearchClient, VolcTtsClient, get_enabled_provider, get_enabled_provider_by_provider, get_task_llm_provider
from .schemas import AppEventOut, DialogueLine, DialoguePayload, EventIn, RelationDelta, ReplyOption
from .schedule import ensure_schedule, mark_interruption
from .utils import clamp, dump_json, load_json, uid, utc_now
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
    sync_relationship_stage(relation)
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


_EXPRESSION_TAG_RE = re.compile(r"\[(happy|shy|thinking|calm|sad|angry)\]", re.IGNORECASE)


def _split_expression_tag(text: str) -> tuple[str, str]:
    match = _EXPRESSION_TAG_RE.search(text or "")
    if not match:
        return text, ""
    expression = match.group(1).lower()
    cleaned = _EXPRESSION_TAG_RE.sub("", text).strip()
    return cleaned, expression


def _summary_text(value: str, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _skipped_context(source: str, label: str) -> tuple[str, dict[str, Any]]:
    text = f"计划器本轮未取用{label}。"
    return text, {"used": False, "planned": False, "count": 0, "items": [], "summary": text, "source": source}


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


def _context_with_references(
    session: Session,
    user_id: str,
    character_id: str,
    query_text: str = "",
    *,
    include_memory: bool = True,
    include_moment_interactions: bool = True,
    memory_layers: set[str] | None = None,
    memory_limit: int = 8,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    selected_layers = set(memory_layers or (FIXED_MEMORY_LAYERS | VECTOR_RECALL_LAYERS))
    fixed_layers = selected_layers.intersection(FIXED_MEMORY_LAYERS)
    vector_layers = selected_layers.intersection(VECTOR_RECALL_LAYERS)
    memory_limit = max(0, min(int(memory_limit or 0), 12))
    fixed_memories: list[Memory] = []
    vector_hits = []
    vector_error = ""
    recalled_memories: list[Memory] = []
    hit_by_id = {}
    recall_source = "planner_skipped"
    if include_memory and memory_limit > 0:
        if fixed_layers:
            fixed_memories = session.execute(
                select(Memory)
                .where(
                    Memory.user_id == user_id,
                    Memory.character_id == character_id,
                    Memory.hidden == False,  # noqa: E712
                    Memory.layer.in_(sorted(fixed_layers)),
                )
                .order_by(Memory.importance.desc(), Memory.created_at.desc())
                .limit(memory_limit)
            ).scalars().all()
        if vector_layers:
            try:
                vector_hits = search_memory_vectors(
                    session,
                    user_id=user_id,
                    character_id=character_id,
                    query_text=query_text,
                    layers=vector_layers,
                    limit=memory_limit,
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
                        Memory.layer.in_(sorted(vector_layers)),
                    )
                    .order_by(Memory.created_at.desc())
                    .limit(memory_limit)
                ).scalars().all()
        elif fixed_memories:
            recall_source = "fixed"
        else:
            recall_source = "no_selected_layers"
    memories = [*fixed_memories]
    seen = {memory.memory_id for memory in memories}
    for memory in recalled_memories:
        if memory.memory_id not in seen:
            memories.append(memory)
            seen.add(memory.memory_id)
    interactions: list[MomentInteraction] = []
    if include_moment_interactions:
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
    if not lines:
        if not include_memory and not include_moment_interactions:
            lines.append("计划器本轮未取用长期记忆或朋友圈互动。")
        elif not include_memory:
            lines.append("计划器本轮未取用长期记忆。")
        else:
            lines.append("暂无长期记忆。")
    memory_items = []
    for memory in memories:
        hit = hit_by_id.get(memory.memory_id)
        memory_items.append(_memory_ref(memory, score=hit.score if hit else None, source=hit.source if hit else ("fixed" if memory in fixed_memories else recall_source)))
    references = {
        "memory": {
            "used": bool(memories),
            "planned": bool(include_memory),
            "count": len(memories),
            "source": recall_source,
            "query": query_text,
            "layers": sorted(selected_layers),
            "limit": memory_limit,
            "vector_error": vector_error,
            "items": memory_items,
        },
        "event_memory": {
            "used": any(memory.layer == "event" for memory in memories),
            "planned": bool(include_memory and "event" in selected_layers),
            "count": len([memory for memory in memories if memory.layer == "event"]),
            "items": [item for item in memory_items if item["layer"] == "event"],
        },
        "moment_interactions": {
            "used": bool(interactions),
            "planned": bool(include_moment_interactions),
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
    return "\n".join(lines), references, {"memories": memories, "interactions": interactions}


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


def _schedule_context(session: Session, event: EventIn, *, scope: str = "current_next") -> str:
    scope = str(scope or "current_next").strip().lower()
    if scope == "none":
        return "计划器本轮未取用角色日程。"
    if scope not in {"current", "current_next", "upcoming", "today"}:
        scope = "current_next"
    local_time = _extract_local_time(event) or datetime.now()
    slots = ensure_schedule(session, user_id=event.user_id, character_id=event.character_id, day=local_time)
    comparable_now = local_time
    current: ScheduleSlot | None = None
    previous: ScheduleSlot | None = None
    upcoming: ScheduleSlot | None = None
    upcoming_slots: list[ScheduleSlot] = []
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
        elif start > now_for_slot:
            if upcoming is None:
                upcoming = slot
            if len(upcoming_slots) < 4:
                upcoming_slots.append(slot)
    groups: list[str] = []
    seen: set[str] = set()
    for slot in sorted_slots:
        key = f"{slot.activity_title}|{slot.location}"
        if key in seen:
            continue
        seen.add(key)
        same = [item for item in sorted_slots if item.activity_title == slot.activity_title and item.location == slot.location]
        groups.append(f"{str(same[0].start_at).split('T', 1)[-1][:5]}-{str(same[-1].end_at).split('T', 1)[-1][:5]} {slot.activity_title}@{slot.location}")
    lines = [f"客户端当前时间：{local_time.isoformat()}"]
    if scope in {"current", "current_next", "today"}:
        lines.append(f"刚刚/上一段：{_format_slot(previous)}")
        lines.append(f"当前：{_format_slot(current)}")
        if scope in {"current_next", "today"}:
            lines.append(f"下一段：{_format_slot(upcoming)}")
    if scope == "upcoming":
        lines.append("接下来几段：" + "；".join(_format_slot(slot) for slot in upcoming_slots) if upcoming_slots else "接下来几段：无")
    if scope == "today":
        lines.append("今日摘要：" + "；".join(groups[:10]))
    return "\n".join(lines)


def _schedule_context_with_references(session: Session, event: EventIn, *, scope: str = "current_next") -> tuple[str, dict[str, Any]]:
    scope = str(scope or "current_next").strip().lower()
    if scope == "none":
        return "计划器本轮未取用角色日程。", {"used": False, "planned": False, "count": 0, "items": [], "scope": "none", "summary": "计划器跳过"}
    context = _schedule_context(session, event, scope=scope)
    local_time = _extract_local_time(event) or datetime.now()
    slots = ensure_schedule(session, user_id=event.user_id, character_id=event.character_id, day=local_time)
    selected_slots = slots
    if scope in {"current", "current_next", "upcoming"}:
        comparable_now = local_time
        scoped: list[ScheduleSlot] = []
        for slot in sorted(slots, key=lambda item: item.start_at):
            start = _slot_time(slot.start_at)
            end = _slot_time(slot.end_at)
            if start is None or end is None:
                continue
            now_for_slot = _same_timezone(comparable_now, start)
            if scope == "current" and start <= now_for_slot < end:
                scoped.append(slot)
            elif scope == "current_next" and (start <= now_for_slot < end or start > now_for_slot):
                scoped.append(slot)
            elif scope == "upcoming" and start > now_for_slot:
                scoped.append(slot)
            if len(scoped) >= 6:
                break
        selected_slots = scoped
    items = [
        {
            "slot_id": slot.slot_id,
            "title": slot.activity_title,
            "location": slot.location,
            "start_at": slot.start_at,
            "end_at": slot.end_at,
            "status": slot.actual_status,
        }
        for slot in selected_slots[:10]
    ]
    return context, {"used": bool(context.strip()), "planned": True, "scope": scope, "count": len(selected_slots), "items": items, "summary": _summary_text(context, 220)}


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


_CN_SMALL_NUMBERS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_RELATIVE_DATE_RE = re.compile(r"(?:再过|过|还有)?\s*([0-9]+|[一二两三四五六七八九十]+)\s*个?\s*(天|日|周|星期|礼拜)")


def _cn_number(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text in _CN_SMALL_NUMBERS:
        return _CN_SMALL_NUMBERS[text]
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_SMALL_NUMBERS.get(left, 1 if not left else 0)
        ones = _CN_SMALL_NUMBERS.get(right, 0) if right else 0
        value_int = tens * 10 + ones
        return value_int if value_int > 0 else None
    return None


def _relative_date_evidence(text: str, base_date: date) -> dict[str, Any]:
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return {}
    if "后天" in normalized:
        target = base_date + timedelta(days=2)
        return {"matched": "后天", "target_date": target.isoformat(), "days_offset": 2}
    if "明天" in normalized:
        target = base_date + timedelta(days=1)
        return {"matched": "明天", "target_date": target.isoformat(), "days_offset": 1}
    if "今天" in normalized:
        return {"matched": "今天", "target_date": base_date.isoformat(), "days_offset": 0}
    match = _RELATIVE_DATE_RE.search(normalized)
    if match:
        amount = _cn_number(match.group(1))
        if amount is not None and 0 <= amount <= 60:
            unit = match.group(2)
            days = amount * 7 if unit in {"周", "星期", "礼拜"} else amount
            target = base_date + timedelta(days=days)
            return {"matched": match.group(0), "target_date": target.isoformat(), "days_offset": days}
    if "下周" in normalized:
        target = base_date + timedelta(days=7)
        return {"matched": "下周", "target_date": target.isoformat(), "days_offset": 7}
    return {}


def _calendar_event_dates(item: CalendarEvent, base_date: date) -> list[date]:
    try:
        if item.repeats_yearly:
            month = int(item.event_date[5:7])
            day = int(item.event_date[8:10])
            dates = [date(base_date.year, month, day)]
            if dates[0] < base_date:
                dates.append(date(base_date.year + 1, month, day))
            return dates
        return [date.fromisoformat(item.event_date[:10])]
    except (ValueError, TypeError, IndexError):
        return []


def _calendar_context_with_references(session: Session, event: EventIn, text: str) -> tuple[str, dict[str, Any]]:
    local_time = _extract_local_time(event) or datetime.now()
    base_date = local_time.date()
    relative = _relative_date_evidence(text, base_date)
    target_date = None
    if relative.get("target_date"):
        try:
            target_date = date.fromisoformat(str(relative["target_date"]))
        except ValueError:
            target_date = None
    normalized = str(text or "")
    asks_calendar = any(marker in normalized for marker in ("放假", "假期", "节", "节日", "端午", "中秋", "国庆", "春节", "日期", "几号", "什么时候"))
    lookahead_days = 21 if asks_calendar else 10
    rows = session.execute(
        select(CalendarEvent).where(
            CalendarEvent.hidden == False,  # noqa: E712
            CalendarEvent.user_id.in_(["", event.user_id]),
        )
    ).scalars().all()
    candidates: list[dict[str, Any]] = []
    for item in rows:
        if item.character_id not in {"", event.character_id}:
            continue
        for event_day in _calendar_event_dates(item, base_date):
            days_until = (event_day - base_date).days
            if target_date is not None:
                target_delta = abs((event_day - target_date).days)
                in_window = target_delta <= 3
            else:
                target_delta = 999
                in_window = 0 <= days_until <= lookahead_days and int(item.salience or 0) >= 75
            if not in_window:
                continue
            candidates.append(
                {
                    "event_id": item.event_id,
                    "date": event_day.isoformat(),
                    "title": item.title,
                    "category": item.category,
                    "description": item.description,
                    "salience": item.salience,
                    "days_until": days_until,
                    "target_delta_days": target_delta,
                    "source_type": item.source_type,
                }
            )
    candidates.sort(key=lambda item: (int(item.get("target_delta_days") or 999), int(item.get("days_until") or 999), -int(item.get("salience") or 0)))
    items = candidates[:8]
    lines = [f"客户端当前日期：{base_date.isoformat()}"]
    if relative:
        lines.append(f"相对时间解析：{relative['matched']} => {relative['target_date']}（距当前 {relative['days_offset']} 天）")
    if items:
        lines.append("候选日历事件：")
        for item in items:
            lines.append(
                f"- {item['date']} {item['title']}（{item['category']}，距当前 {item['days_until']} 天）：{item['description'] or '无描述'}"
            )
        if target_date is not None:
            best = items[0]
            lines.append(f"证据焦点：目标日期 {target_date.isoformat()} 附近最强日历事件是 {best['date']}「{best['title']}」。")
    else:
        lines.append("候选日历事件：暂无命中。")
    context = "\n".join(lines)
    return context, {
        "used": bool(relative or items or asks_calendar),
        "count": len(items),
        "base_date": base_date.isoformat(),
        "relative": relative,
        "items": items,
        "summary": _summary_text(context, 240),
    }


def _needs_web_search(text: str) -> bool:
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return False
    markers = (
        "联网",
        "搜索",
        "搜一下",
        "查一下",
        "网上",
        "最新",
        "新闻",
        "热搜",
        "实时",
        "今天发生",
        "最近发生",
        "现在的价格",
        "现在版本",
        "发布了吗",
    )
    return any(marker in normalized for marker in markers)


def _web_search_context_with_references(session: Session, text: str, *, force: bool = False) -> tuple[str, dict[str, Any]]:
    needed = force or _needs_web_search(text)
    if not needed:
        return "本轮没有触发联网搜索。", {"used": False, "planned": False, "needed": False, "source": "", "items": []}
    config = get_enabled_provider_by_provider(session, "search", "volc_ark_web_search")
    if config is None:
        return "本轮需要联网搜索，但未配置对话联网搜索 provider。", {"used": False, "planned": True, "needed": True, "source": "", "items": [], "error": "provider_not_configured"}
    query = (
        "请联网搜索并返回可验证来源，优先给出标题、链接、发布时间和摘要。\n"
        f"用户问题：{text}"
    )
    try:
        result = VolcArkWebSearchClient(config).search(query, require_published_at=False)
    except Exception as exc:  # noqa: BLE001
        write_diagnostic(
            "dialogue_web_search_failed",
            feature="联网搜索",
            stage="dialogue_context",
            provider_id=config.provider_id,
            model=config.model,
            query=_summary_text(text, 240),
            error_type=type(exc).__name__,
            message=str(exc),
        )
        return (
            "本轮尝试联网搜索，但搜索失败；不要编造实时信息。",
            {"used": False, "planned": True, "needed": True, "source": "volc_ark_web_search", "provider_id": config.provider_id, "items": [], "error": str(exc)},
        )
    sources = result.get("sources") if isinstance(result, dict) else []
    sources = [item for item in (sources or []) if isinstance(item, dict)][:5]
    lines = ["联网搜索结果："]
    summary = str(result.get("summary") or "").strip() if isinstance(result, dict) else ""
    if summary:
        lines.append(f"摘要：{summary}")
    for index, item in enumerate(sources, start=1):
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        published_at = str(item.get("published_at") or "").strip()
        site_name = str(item.get("site_name") or "").strip()
        item_summary = str(item.get("summary") or "").strip()
        lines.append(f"{index}. {title}；来源={site_name or url}；发布时间={published_at or '未知'}；链接={url}；摘要={item_summary}")
    if not sources and not summary:
        lines.append("未返回可用来源。")
    return "\n".join(lines), {
        "used": bool(sources or summary),
        "planned": True,
        "needed": True,
        "source": "volc_ark_web_search",
        "provider_id": config.provider_id,
        "model": config.model,
        "count": len(sources),
        "summary": _summary_text("\n".join(lines), 240),
        "items": sources,
        "usage": result.get("usage") if isinstance(result, dict) else {},
        "estimated_cost": result.get("estimated_cost") if isinstance(result, dict) else 0,
    }


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


def _dialogue_gate(event: EventIn, text: str) -> str:
    if event.event_type in {"notification_opened", "widget_opened"}:
        return "opening：用户从主动入口进入，请自然展开主动话题，至少 2 句、最多 3 句，不要 silent。"
    if event.event_type == "app_opened":
        return "continue：用户是从入口进入前台。只有带明确主动事件时才展开；没有新信息时可以 silent。"
    if len(text.strip()) <= 2 and text.strip() in {"嗯", "好", "哦", "啊", "…", "..."}:
        return "light：用户只给了很短的接话，轻轻回应或留白即可，不要制造重大剧情。"
    return "reply：用户正在直接对角色说话，需要围绕目标消息自然回应，normal 模式至少 2 句，每句尽量不超过 28 个汉字。"


_LINE_FRAGMENT_TERMS = {
    "\u5417",  # 吗
    "\u4e48",  # 么
    "\u561b",  # 嘛
    "\u5462",  # 呢
    "\u5427",  # 吧
    "\u554a",  # 啊
    "\u5440",  # 呀
    "\u5566",  # 啦
    "\u5594",  # 喔
    "\u54e6",  # 哦
    "\u5662",  # 噢
    "\u5457",  # 呗
    "\u5450",  # 呐
    "\u54c7",  # 哇
    "\u6b38",  # 欸
    "\u8bf6",  # 诶
    "\u8036",  # 耶
    "\u4e86",  # 了
    "\u55b5",  # 喵
}
_LINE_FRAGMENT_TRIM = " \t\r\n,\uff0c.\u3002!\uff01?\uff1f;\uff1b:\uff1a~\uff5e\u2026"


def _line_fragment_core(text: str) -> str:
    return str(text or "").strip().strip(_LINE_FRAGMENT_TRIM)


def _is_orphan_line_fragment(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    core = _line_fragment_core(stripped)
    return not core or core in _LINE_FRAGMENT_TERMS


def _append_line_fragment(base: str, fragment: str) -> str:
    cleaned = str(fragment or "").strip()
    if not cleaned:
        return base
    return f"{base.rstrip()}{cleaned}"


def _merge_line_text_fragments(lines: list[str]) -> list[str]:
    merged: list[str] = []
    for line in lines:
        text = str(line or "").strip()
        if not text:
            continue
        if _is_orphan_line_fragment(text):
            if merged:
                merged[-1] = _append_line_fragment(merged[-1], text)
            continue
        merged.append(text)
    return merged


def _merge_dialogue_line_candidates(candidates: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    for candidate in candidates:
        text = str(candidate.get("text") or "").strip()
        if not text:
            continue
        if _is_orphan_line_fragment(text):
            if merged:
                merged[-1]["text"] = _append_line_fragment(merged[-1]["text"], text)
                if not merged[-1].get("expression") and candidate.get("expression"):
                    merged[-1]["expression"] = candidate["expression"]
                merged[-1]["tts_text_ja"] = ""
            continue
        merged.append({**candidate, "text": text})
    return merged


def _normalize_line_text(text: str, *, max_chars: int = 28) -> list[str]:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]
    parts = re.split(r"(?<=[。！？!?])", cleaned)
    chunks: list[str] = []
    buffer = ""
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        if len(piece) > max_chars:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            start = 0
            while start < len(piece):
                chunks.append(piece[start : start + max_chars])
                start += max_chars
            continue
        candidate = f"{buffer}{piece}" if buffer else piece
        if len(candidate) <= max_chars:
            buffer = candidate
        else:
            if buffer:
                chunks.append(buffer)
            buffer = piece
    if buffer:
        chunks.append(buffer)
    return _merge_line_text_fragments(chunks)


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
) -> Message:
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
    with diagnostic_span(
        "reply_stage",
        feature="回复模块",
        stage="context_build",
        purpose="Build prompt context and reference markers",
        summary=f"{event.event_type}: {text[:80]}",
        input={"event_type": event.event_type, "user_id": event.user_id, "character_id": event.character_id, "session_id": event.session_id, "input_text": text},
    ) as context_span:
        relation = _relation(session, event.user_id, event.character_id)
        resolved_profile = resolve_character_profile(session, user_id=event.user_id, character_id=event.character_id, character=character)
        persona_card = resolved_profile.card
        persona_context = persona_card_summary(persona_card)
        user_profile = load_json(user.profile_json, {})
        interest_topics = load_json(user.interest_topics_json, [])
        user_profile_context_full = user_profile_summary(user_profile, interest_topics)
        user_profile_available = user_profile_has_content(user)
        attitude_band, attitude_text = relation_attitude(relation, persona_card)
        relationship_state = relationship_state_summary(relation, persona_card)
        mood_payload = relationship_state["mood"]
        affection_payload = relationship_state["affection"]
        relationship_context = (
            f"好感阶段：{affection_payload['label']}({affection_payload['score']}/1000)，触摸档位：{relationship_state['touch_tier']}。\n"
            f"心情：{mood_payload['label']}({mood_payload['score']}/100)，{mood_payload['visible_hint']}\n"
            f"关系边界提示：{relationship_state['boundary_hint']}"
        )
        recent_dialogue, recent_refs = _recent_dialogue_with_references(session, event)
        context_plan = build_context_plan(
            session,
            event=event,
            user=user,
            character=character,
            text=text,
            recent_dialogue=recent_dialogue,
            user_profile_used=user_profile_available,
            allow_llm=persist_side_effects,
        )
        if not context_plan.use_recent_dialogue:
            recent_dialogue, recent_refs = _skipped_context("recent_dialogue", "近期对话")
        user_profile_context = user_profile_context_full if context_plan.use_user_profile else "计划器本轮未取用用户画像。"
        user_profile_used = bool(context_plan.use_user_profile and user_profile_available)
        context, context_refs, context_rows = _context_with_references(
            session,
            event.user_id,
            event.character_id,
            context_plan.memory_query or text,
            include_memory=context_plan.use_memory,
            include_moment_interactions=context_plan.use_moment_interactions,
            memory_layers=set(context_plan.memory_layers),
            memory_limit=context_plan.memory_limit,
        )
        schedule_context, schedule_refs = _schedule_context_with_references(session, event, scope=context_plan.schedule_scope if context_plan.use_character_schedule else "none")
        if context_plan.use_user_schedule:
            user_schedule_context, user_schedule_refs = _user_schedule_context_with_references(session, event)
            user_schedule_refs["planned"] = True
        else:
            user_schedule_context, user_schedule_refs = _skipped_context("user_schedule", "用户日程")
        if context_plan.use_calendar:
            calendar_context, calendar_refs = _calendar_context_with_references(session, event, context_plan.calendar_query or text)
            calendar_refs["planned"] = True
        else:
            calendar_context, calendar_refs = _skipped_context("calendar", "近期日历/节假日")
        if context_plan.use_weather:
            weather_info, weather_refs = _weather_context_with_references(session, user_id=event.user_id, text=text, local_time=_extract_local_time(event))
            weather_refs["planned"] = True
        else:
            weather_info, weather_refs = _skipped_context("weather", "今日天气")
        web_search_context, web_search_refs = _web_search_context_with_references(session, context_plan.web_search_query or text, force=context_plan.use_web_search)
        gate = _dialogue_gate(event, text)
        subject_hint = _target_subject_hint(text, character)
        voice = _active_voice_profile(session, character)
        requires_japanese_tts = bool(user.tts_enabled and voice is not None and voice.language == "ja")
        context_plan_refs = context_plan_reference(context_plan)
        references = {
            "user_input": {
                "used": True,
                "event_type": event.event_type,
                "event_id": event.event_id,
                "session_id": event.session_id,
                "summary": _summary_text(text),
            },
            "context_plan": context_plan_refs,
            "schedule": schedule_refs,
            "character_schedule": schedule_refs,
            "user_schedule": user_schedule_refs,
            "calendar": calendar_refs,
            "weather": weather_refs,
            "web_search": web_search_refs,
            "persona": {
                "used": True,
                "count": 1,
                "summary": _summary_text(persona_context, 240),
                "card": persona_card,
                "overlay": resolved_profile.overlay,
                "overlay_used": resolved_profile.has_overlay,
                "revision": resolved_profile.revision,
                "source_memory_ids": resolved_profile.source_memory_ids,
            },
            "user_profile": {"used": user_profile_used, "summary": _summary_text(user_profile_context, 240), "profile": user_profile},
            "relation_attitude": {
                "used": True,
                "band": attitude_band,
                "summary": attitude_text,
                "relationship_stage": relation.relationship_stage,
                "relationship_state": relationship_state,
            },
            "memory": context_refs["memory"],
            "event_memory": context_refs["event_memory"],
            "recent_dialogue": recent_refs,
            "moment_interactions": context_refs["moment_interactions"],
            "gate": gate,
            "subject_hint": subject_hint,
        }
        context_span.add(
            output={
                "context_sources": {
                    "recent_dialogue": bool(recent_refs.get("used")),
                    "schedule": bool(schedule_refs.get("used")),
                    "weather": bool(weather_refs.get("used")),
                    "calendar": bool(calendar_refs.get("used")),
                    "web_search": bool(web_search_refs.get("used")),
                    "persona": True,
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
            "recent_dialogue": bool(recent_refs.get("used")),
            "schedule": bool(schedule_refs.get("used")),
            "character_schedule": bool(schedule_refs.get("used")),
            "user_schedule": bool(user_schedule_refs.get("used")),
            "weather": bool(weather_refs.get("used")),
            "calendar": bool(calendar_refs.get("used")),
            "web_search": bool(web_search_refs.get("used")),
            "persona": True,
            "user_profile": user_profile_used,
            "memory_or_moment": bool(context_refs["memory"].get("used") or context_refs["moment_interactions"].get("used")),
            "japanese_tts": requires_japanese_tts,
        },
        references=references,
        context_plan=context_plan_refs,
        persona_context=persona_context,
        user_profile_context=user_profile_context,
        relation_attitude={"band": attitude_band, "text": attitude_text},
        relationship_state=relationship_state,
        schedule_context=schedule_context,
        user_schedule_context=user_schedule_context,
        calendar_context=calendar_context,
        weather_context=weather_info,
        web_search_context=web_search_context,
        memory_context=context,
        recent_dialogue=recent_dialogue,
        gate=gate,
        subject_hint=subject_hint,
    )
    context_plan_text = json.dumps(context_plan_refs, ensure_ascii=False, indent=2)
    prompt = f"""
你要为 Galgame 伴侣 APP 生成一次女主回复。必须只输出 JSON。
请先依据【节奏判断】决定回复轻重，再生成用户可见台词。不要输出分析文字。

【角色设定】
名字：{character.name}
{character.persona_prompt}

【结构化人设卡】
{persona_context}

【说话风格】
{character.speech_style}
补充要求：像真实聊天，不要客服腔，不要 Markdown，不要长篇总结。normal 模式至少 2 句；opening 模式 2 到 3 句；light 最多 1 句。每句尽量不超过 28 个汉字；不要把单独的“…”当成一整句。

【边界】
{character.relationship_boundary}

【当前关系】
好感 {relation.affection}，信任 {relation.trust}，依赖 {relation.dependency}，心情 {relation.mood}，阶段 {relation.relationship_stage}。
{relationship_context}

【本次关系态度】
{attitude_band}：{attitude_text}

【本轮上下文计划】
{context_plan_text}

【用户画像】
{user_profile_context}

【近期对话】
{recent_dialogue}

【今日真实日程】
这是角色自己的日程：
{schedule_context}

【用户日程】
{user_schedule_context}

【近期日历/节假日】
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

输出格式：
{{
  "reply_mode": "silent|light|opening|normal|key_moment",
  "pace_reason": "为什么这次选择这个节奏",
  "lines": [{{"text": "短中文台词", "emotion": "happy|shy|thinking|calm|sad", "pose": "idle|happy|shy|thinking", "expression": "happy|shy|thinking|calm|sad|angry|"}}],
expression 可留空；需要更强面部表现时填写，与 emotion 可不同。也可在 text 内写 [shy] 这类标签。
  "normal_replies": [{{"text": "用户可选回复"}}],
  "key_reply_score": 0,
  "key_reply_reason": "为什么这次需要或不需要特殊回复",
  "key_replies": [{{"text": "重要选项", "score": 0, "preview_delta": {{"affection":0,"trust":0,"dependency":0,"mood":0}}, "trigger_memory": false}}],
  "relation_delta": {{"affection":0,"trust":0,"dependency":0,"mood":0}},
  "memory_candidates": [{{"layer":"core|persona|persona_canon|persona_editable|relation|relationship|mood_event|affection_event|user_profile|shared_memory|character_schedule|user_schedule|event|chat|daily|temporary","content":"...","importance":0.5,"confidence":0.7,"tags":["..."],"metadata":{{}}}}],
  "interest_topics": ["..."]
}}
reply_mode 规则：silent 表示这次不应该硬回；light 最多 1 句、不要给选项和数值变化；opening 是开场/主动入口问候，2 到 3 句、不要给选项和数值变化；normal 是自然闲聊且至少 2 句；key_moment 只用于承诺、关系转折、核心记忆、重要剧情节点。
上下文计划规则：【本轮上下文计划】说明本轮实际取用了哪些来源。计划器未取用的来源不要当成已知事实；对应章节若写着“计划器本轮未取用”，就只说明没有取用，不要编造。
特殊回复只在承诺、关系转折、核心记忆、重要剧情节点时给高分。普通寒暄、顺着聊天、夸奖、轻微情绪互动必须低于 75。
如果用户问角色“现在、刚刚、日程、安排、在哪里、做什么”，必须优先依据【今日真实日程】里的角色自己的日程回答；如果问用户自己的安排，优先依据【用户日程】和【用户画像】回答；不要从近期对话或记忆里补编活动。
如果用户问日期、节日、假期、放假或使用相对时间表达，必须依据【近期日历/节假日】里的相对时间解析和候选日历事件回答；具体日历证据优先于泛化季节常识。
如果用户问“天气、下雨、带伞、温度、气温、冷不冷、热不热、预报、雷雨”，必须优先依据【今日天气】回答；没有天气数据时要说明还没有拿到位置或天气服务，不能编造。
如果用户要求最新消息、搜索、联网、热搜、现实新闻或实时资料，必须依据【联网搜索结果】里的来源回答；TrendRadar 只属于新闻主动模块，不作为普通对话搜索依据。
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
    if requires_japanese_tts and (problems := _japanese_tts_problems(result)):
        write_diagnostic(
            "tts_dialogue_repair_needed",
            provider_id=config.provider_id,
            model=config.model,
            character_id=character.character_id,
            reason="；".join(problems[:4]),
        )
    payload_stage = _start_reply_stage(
        "payload_build",
        f"{event.event_type}: build reply payload",
        input_payload={
            "reply_mode_raw": result.get("reply_mode"),
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
    diagnostic_point(
        "reply_llm_judgement",
        feature="回复模块",
        stage="judgement",
        summary=f"{reply_mode}: {pace_reason[:100]}",
        reply_mode=reply_mode,
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
        diagnostic_point(
            "reply_output_ready",
            feature="回复模块",
            stage="output",
            summary="silent reply",
            reply_mode=reply_mode,
            pace_reason=pace_reason,
            line_count=0,
            normal_reply_count=0,
            key_reply_count=0,
            saved_memory_count=0,
            memory_writes=[],
            memory_skips=[],
            relation_delta=RelationDelta().model_dump(),
            references=references,
        )
        return DialoguePayload(lines=[], relation_delta=RelationDelta(), reply_mode=reply_mode, pace_reason=pace_reason)
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
            "pace_reason": pace_reason,
            "relation_delta": delta.model_dump(),
            "requires_japanese_tts": requires_japanese_tts,
        },
        references=references,
    )
    line_objs: list[DialogueLine] = []
    if reply_mode == "light":
        max_lines = 1
    elif reply_mode == "opening":
        max_lines = 3
    else:
        max_lines = 4
    tts_stage = _start_reply_stage(
        "tts_lines",
        f"{event.event_type}: synthesize reply lines",
        input_payload={"candidate_line_count": len(result.get("lines") or []), "max_lines": max_lines, "tts_enabled": user.tts_enabled},
        references=references,
    )
    line_candidates: list[dict[str, str]] = []
    for item in result.get("lines") or []:
        if not isinstance(item, dict):
            continue
        line_emotion = str(item.get("emotion") or "calm")
        line_expression = str(item.get("expression") or "").strip()
        line_text = " ".join(str(item.get("text") or "").split()).strip()
        line_text, inline_expression = _split_expression_tag(line_text)
        if not line_expression:
            line_expression = inline_expression
        ja_candidate = str(item.get("tts_text_ja") or item.get("tts_text") or item.get("ja") or "").strip()
        text_chunks = _normalize_line_text(line_text)
        if not text_chunks:
            continue
        for chunk_index, chunk_text in enumerate(text_chunks):
            if not _has_tts_readable_text(chunk_text):
                continue
            line_candidates.append(
                {
                    "text": chunk_text,
                    "emotion": line_emotion,
                    "pose": str(item.get("pose") or "idle"),
                    "expression": line_expression,
                    "tts_text_ja": ja_candidate if chunk_index == 0 else "",
                }
            )
    line_candidates = _merge_dialogue_line_candidates(line_candidates)[:max_lines]
    for candidate in line_candidates:
        line_text = candidate["text"]
        line_emotion = candidate.get("emotion") or "calm"
        tts_url, tts_error = _tts_for_line(
            session,
            user,
            character,
            line_text,
            line_emotion,
            tts_text_ja=candidate.get("tts_text_ja") or "",
        )
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
                pose=candidate.get("pose") or "idle",
                expression=candidate.get("expression") or "",
                tts_audio_url=tts_url,
                tts_error=tts_error,
            )
        )
        if reply_mode == "light" and len(line_objs) >= 1:
            break
        if len(line_objs) >= max_lines:
            break
    _end_reply_stage(
        tts_stage,
        output={"line_count": len(line_objs), "audio_count": len([line for line in line_objs if line.tts_audio_url])},
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
    commitment_payload: dict[str, Any] = {}
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
    if persist_side_effects:
        explicit_topics = _explicit_interest_topics(text)
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
            proactive = session.execute(
                select(ProactiveEvent)
                .where(ProactiveEvent.source_type == "appointment", ProactiveEvent.source_id == commitment.commitment_id)
                .order_by(ProactiveEvent.created_at.desc())
            ).scalars().first()
            commitment_payload = {
                "commitment_id": commitment.commitment_id,
                "title": commitment.title,
                "description": commitment.description,
                "event_at": commitment.event_at,
                "remind_at": commitment.remind_at,
                "timezone": commitment.timezone,
                "proactive_event_id": proactive.proactive_event_id if proactive is not None else "",
            }
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
    _end_reply_stage(
        side_effects_stage,
        output={
            "saved_memory_count": saved_memory_count,
            "relation_delta": delta.model_dump(),
            "memory_writes": memory_writes,
            "memory_skips": memory_skips,
            "commitment": commitment_payload,
        },
        references=references,
    )
    diagnostic_point(
        "reply_output_ready",
        feature="回复模块",
        stage="output",
        summary=f"{reply_mode} lines={len(line_objs)} normal={len(normal)} key={len(key)}",
        reply_mode=reply_mode,
        pace_reason=pace_reason,
        line_count=len(line_objs),
        normal_reply_count=len(normal),
        key_reply_count=len(key),
        saved_memory_count=saved_memory_count,
        memory_writes=memory_writes,
        memory_skips=memory_skips,
        commitment=commitment_payload,
        relation_delta=delta.model_dump(),
        lines=[line.model_dump() for line in line_objs],
        normal_replies=[reply.model_dump() for reply in normal],
        key_replies=[reply.model_dump() for reply in key],
        references=references,
    )
    return DialoguePayload(
        lines=line_objs,
        normal_replies=normal,
        key_replies=key,
        relation_delta=delta,
        reply_mode=reply_mode,
        pace_reason=pace_reason,
        commitment=commitment_payload,
    )


def _save_dialogue_lines(session: Session, event: EventIn, payload: DialoguePayload) -> None:
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
                input={"text": text, "session_id": event.session_id},
            ):
                _save_message(session, event=event, sender_type="user", sender_id=event.user_id, content=text)
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
                _save_dialogue_lines(session, event, payload)
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
