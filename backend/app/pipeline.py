from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .commitments import extract_user_commitment
from .diagnostics import current_span_id, current_trace_id, diagnostic_point, diagnostic_span, new_span_id, write_diagnostic
from .models import (
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
)
from .proactive import consume_proactive_event, mark_proactive_opened, mark_proactive_reflected
from .providers import OpenAICompatibleClient, ProviderError, VolcTtsClient, get_enabled_provider, get_task_llm_provider
from .schemas import AppEventOut, DialogueLine, DialoguePayload, EventIn, RelationDelta, ReplyOption
from .schedule import ensure_schedule, mark_interruption
from .utils import clamp, dump_json, load_json, uid, utc_now
from .weather import active_weather_date_for_user, is_weather_question, read_weather_snapshot, weather_snapshot_to_dict


STORY_LINES = [
    "啊，你来了。这里是我每天都会经过的教室，窗外的樱花今天开得刚刚好。",
    "我叫小樱。虽然这听起来有点像游戏开场白，但我想认真记住和你有关的事。",
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
        speaker = "USER(用户)" if item.sender_type == "user" else "CHARACTER(小樱)"
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


def _context_with_references(session: Session, user_id: str, character_id: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
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
        desc = "liked a moment" if item.interaction_type == "like" else f"commented on a moment: {item.content}"
        lines.append(f"- recent moment interaction: user {desc}")
    references = {
        "memory": {
            "used": bool(memories),
            "count": len(memories),
            "items": [
                {
                    "memory_id": memory.memory_id,
                    "layer": memory.layer,
                    "summary": _summary_text(memory.content),
                    "importance": memory.importance,
                    "confidence": memory.confidence,
                }
                for memory in memories
            ],
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
        "小樱",
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
    role_markers = ["你", "妳", "小樱", "亚托莉"]
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
                max_tokens=180,
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
                content="相识剧情中，用户选择认真和小樱互相了解。",
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
        context, context_refs, context_rows = _context_with_references(session, event.user_id, event.character_id)
        recent_dialogue, recent_refs = _recent_dialogue_with_references(session, event)
        schedule_context, schedule_refs = _schedule_context_with_references(session, event)
        weather_info, weather_refs = _weather_context_with_references(session, user_id=event.user_id, text=text, local_time=_extract_local_time(event))
        gate = _dialogue_gate(event, text)
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
            "weather": weather_refs,
            "memory": context_refs["memory"],
            "recent_dialogue": recent_refs,
            "moment_interactions": context_refs["moment_interactions"],
            "gate": gate,
            "subject_hint": subject_hint,
        }
        context_span.add(
            output={
                "context_sources": {
                    "recent_dialogue": bool(recent_dialogue.strip()),
                    "schedule": bool(schedule_context.strip()),
                    "weather": bool(weather_info.strip()),
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
            "recent_dialogue": bool(recent_dialogue.strip()),
            "schedule": bool(schedule_context.strip()),
            "weather": bool(weather_info.strip()),
            "memory_or_moment": bool(context.strip()),
            "japanese_tts": requires_japanese_tts,
        },
        references=references,
        schedule_context=schedule_context,
        weather_context=weather_info,
        memory_context=context,
        recent_dialogue=recent_dialogue,
        gate=gate,
        subject_hint=subject_hint,
    )
    prompt = f"""
你要为 Galgame 伴侣 APP 生成一次女主回复。必须只输出 JSON。
请先依据【节奏判断】决定回复轻重，再生成用户可见台词。不要输出分析文字。

【角色设定】
名字：{character.name}
{character.persona_prompt}

【说话风格】
{character.speech_style}
补充要求：像真实聊天，不要客服腔，不要 Markdown，不要长篇总结。normal 模式至少 2 句；opening 模式 2 到 3 句；light 最多 1 句。每句尽量不超过 28 个汉字；不要把单独的“…”当成一整句。

【边界】
{character.relationship_boundary}

【当前关系】
好感 {relation.affection}，信任 {relation.trust}，依赖 {relation.dependency}，心情 {relation.mood}。

【近期对话】
{recent_dialogue}

【今日真实日程】
{schedule_context}

【今日天气】
{weather_info}

【可用记忆和朋友圈互动】
{context}

【目标消息】
{text}

【目标消息主体判断】
{subject_hint}

【发话归属规则】
【目标消息】永远是 USER(用户) 发出的原话，不是角色说的话。
如果【目标消息】省略主语，例如“刚刚去找别人了”“出去玩了”“找别的女人去了”，默认动作主体是 USER(用户) 自己。
只有用户明确说“你/妳/角色名/小樱/你刚刚/你是不是”时，才把动作归给 CHARACTER(小樱)。
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
  "memory_candidates": [{{"layer":"chat|core|relation|temporary","content":"...","importance":0.5,"confidence":0.7}}],
  "interest_topics": ["..."]
}}
reply_mode 规则：silent 表示这次不应该硬回；light 最多 1 句、不要给选项和数值变化；opening 是开场/主动入口问候，2 到 3 句、不要给选项和数值变化；normal 是自然闲聊且至少 2 句；key_moment 只用于承诺、关系转折、核心记忆、重要剧情节点。
特殊回复只在承诺、关系转折、核心记忆、重要剧情节点时给高分。普通寒暄、顺着聊天、夸奖、轻微情绪互动必须低于 75。
如果用户问“现在、刚刚、日程、安排、在哪里、做什么”，必须优先依据【今日真实日程】回答；不要从近期对话或记忆里补编活动。
如果用户问“天气、下雨、带伞、温度、气温、冷不冷、热不热、预报、雷雨”，必须优先依据【今日天气】回答；没有天气数据时要说明还没有拿到位置或天气服务，不能编造。
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
            max_tokens=1100,
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
    for item in (result.get("lines") or [])[:max_lines]:
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
            if len(line_objs) >= max_lines:
                break
            line_text = chunk_text
            chunk_ja = ja_candidate if chunk_index == 0 else ""
            if not _has_tts_readable_text(line_text):
                continue
            tts_url, tts_error = _tts_for_line(
                session,
                user,
                character,
                line_text,
                line_emotion,
                tts_text_ja=chunk_ja,
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
                    pose=str(item.get("pose") or "idle"),
                    expression=line_expression,
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
            content = str(item.get("content") or "").strip()
            if content and _memory_candidate_allowed(content, explicit_topics, text, character):
                importance = float(item.get("importance") or 0.5)
                if reply_mode != "key_moment":
                    importance = min(importance, 0.69)
                session.add(
                    Memory(
                        memory_id=uid("mem"),
                        user_id=event.user_id,
                        character_id=event.character_id,
                        layer=str(item.get("layer") or "chat"),
                        content=content,
                        source_event_id=event.event_id or "",
                        importance=importance,
                        confidence=float(item.get("confidence") or 0.6),
                    )
                )
                saved_memory_count += 1
        topics = explicit_topics
        if topics:
            current = load_json(user.interest_topics_json, [])
            for topic in topics:
                if topic and topic not in current:
                    current.append(topic)
                    session.add(
                        Memory(
                            memory_id=uid("mem"),
                            user_id=event.user_id,
                            character_id=event.character_id,
                            layer="chat",
                            content=f"用户最近关注：{topic}",
                            source_event_id=event.event_id or "",
                            importance=0.7,
                            confidence=0.8,
                        )
                    )
                    saved_memory_count += 1
            user.interest_topics_json = dump_json(current[-20:])
        if allow_relation_delta:
            _apply_delta(relation, delta)
        for interaction in session.execute(
            select(MomentInteraction).where(MomentInteraction.actor_id == event.user_id, MomentInteraction.reflected_in_chat == False)  # noqa: E712
        ).scalars():
            interaction.reflected_in_chat = True
        commitment = extract_user_commitment(session, event=event, text=text, local_time=_extract_local_time(event))
        if commitment is not None:
            saved_memory_count += 1
    _end_reply_stage(
        side_effects_stage,
        output={"saved_memory_count": saved_memory_count, "relation_delta": delta.model_dump()},
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
    )


def _save_dialogue_lines(session: Session, event: EventIn, payload: DialoguePayload) -> None:
    for line in payload.lines:
        _save_message(
            session,
            event=event,
            sender_type="heroine",
            sender_id=event.character_id,
            content=line.text,
            source=event.event_type,
            relation_delta=payload.relation_delta,
            tts_audio_asset_id=line.tts_audio_url.rsplit("/", 1)[-1] if line.tts_audio_url else "",
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
