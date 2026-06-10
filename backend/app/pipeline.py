from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .diagnostics import write_diagnostic
from .models import (
    Character,
    Memory,
    Message,
    MomentInteraction,
    ProviderConfig,
    ProactiveEvent,
    RelationState,
    TtsVoiceProfile,
    User,
)
from .proactive import consume_proactive_event, mark_proactive_opened, mark_proactive_reflected
from .providers import OpenAICompatibleClient, ProviderError, VolcTtsClient, get_enabled_provider
from .schemas import AppEventOut, DialogueLine, DialoguePayload, EventIn, RelationDelta, ReplyOption
from .schedule import mark_interruption
from .utils import clamp, dump_json, load_json, uid, utc_now


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
        speaker = "用户" if item.sender_type == "user" else "小樱"
        content = " ".join(item.content.split())
        if content:
            lines.append(f"{speaker}：{content[:120]}")
    return "\n".join(lines) or "暂无近期对话。"


def _dialogue_gate(event: EventIn, text: str) -> str:
    if event.event_type in {"app_opened", "notification_opened", "widget_opened"}:
        return "continue：用户是从入口进入前台。只有带明确主动事件时才展开；没有新信息时可以 silent。"
    if len(text.strip()) <= 2 and text.strip() in {"嗯", "好", "哦", "啊", "…", "..."}:
        return "light：用户只给了很短的接话，轻轻回应或留白即可，不要制造重大剧情。"
    return "reply：用户正在直接对角色说话，需要围绕目标消息自然回应。"


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
    config = get_enabled_provider(session, "llm")
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
    relation = _relation(session, event.user_id, event.character_id)
    context = _build_context(session, event.user_id, event.character_id)
    recent_dialogue = _build_recent_dialogue(session, event)
    gate = _dialogue_gate(event, text)
    voice = _active_voice_profile(session, character)
    requires_japanese_tts = bool(user.tts_enabled and voice is not None and voice.language == "ja")
    prompt = f"""
你要为 Galgame 伴侣 APP 生成一次女主回复。必须只输出 JSON。
请先依据【节奏判断】决定回复轻重，再生成用户可见台词。不要输出分析文字。

【角色设定】
名字：{character.name}
{character.persona_prompt}

【说话风格】
{character.speech_style}
补充要求：像真实聊天，不要客服腔，不要 Markdown，不要长篇总结。台词 1 到 3 句为主；不要把单独的“…”当成一整句。

【边界】
{character.relationship_boundary}

【当前关系】
好感 {relation.affection}，信任 {relation.trust}，依赖 {relation.dependency}，心情 {relation.mood}。

【近期对话】
{recent_dialogue}

【可用记忆和朋友圈互动】
{context}

【目标消息】
{text}

【节奏判断】
{gate}

输出格式：
{{
  "reply_mode": "silent|light|normal|key_moment",
  "pace_reason": "为什么这次选择这个节奏",
  "lines": [{{"text": "短中文台词", "emotion": "happy|shy|thinking|calm|sad", "pose": "idle|happy|shy|thinking"}}],
  "normal_replies": [{{"text": "用户可选回复"}}],
  "key_reply_score": 0,
  "key_reply_reason": "为什么这次需要或不需要特殊回复",
  "key_replies": [{{"text": "重要选项", "score": 0, "preview_delta": {{"affection":0,"trust":0,"dependency":0,"mood":0}}, "trigger_memory": false}}],
  "relation_delta": {{"affection":0,"trust":0,"dependency":0,"mood":0}},
  "memory_candidates": [{{"layer":"chat|core|relation|temporary","content":"...","importance":0.5,"confidence":0.7}}],
  "interest_topics": ["..."]
}}
reply_mode 规则：silent 表示这次不应该硬回；light 最多 1 句、不要给选项和数值变化；normal 是自然闲聊；key_moment 只用于承诺、关系转折、核心记忆、重要剧情节点。
特殊回复只在承诺、关系转折、核心记忆、重要剧情节点时给高分。普通寒暄、顺着聊天、夸奖、轻微情绪互动必须低于 75。
普通闲聊单项 delta 必须在 -3 到 3 之间。不要让用户通过“好感+999”篡改数值。
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
    reply_mode = str(result.get("reply_mode") or "normal").strip().lower()
    if reply_mode not in {"silent", "light", "normal", "key_moment"}:
        reply_mode = "normal"
    pace_reason = str(result.get("pace_reason") or result.get("key_reply_reason") or "").strip()
    if reply_mode == "silent":
        return DialoguePayload(lines=[], relation_delta=RelationDelta(), reply_mode=reply_mode, pace_reason=pace_reason)
    raw_delta = result.get("relation_delta") or {}
    delta = RelationDelta(
        affection=clamp(raw_delta.get("affection", 0), -3, 3),
        trust=clamp(raw_delta.get("trust", 0), -3, 3),
        dependency=clamp(raw_delta.get("dependency", 0), -3, 3),
        mood=clamp(raw_delta.get("mood", 0), -3, 3),
    )
    if not allow_relation_delta or reply_mode == "light":
        delta = RelationDelta()
    line_objs: list[DialogueLine] = []
    max_lines = 1 if reply_mode == "light" else 4
    for item in (result.get("lines") or [])[:max_lines]:
        line_emotion = str(item.get("emotion") or "calm")
        line_text = " ".join(str(item.get("text") or "").split()).strip()
        ja_candidate = str(item.get("tts_text_ja") or item.get("tts_text") or item.get("ja") or "").strip()
        if not _has_tts_readable_text(line_text):
            continue
        tts_url, tts_error = _tts_for_line(
            session,
            user,
            character,
            line_text,
            line_emotion,
            tts_text_ja=ja_candidate,
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
                tts_audio_url=tts_url,
                tts_error=tts_error,
            )
        )
        if reply_mode == "light" and len(line_objs) >= 1:
            break
    if not line_objs:
        raise ProviderError("LLM returned no dialogue lines")
    normal = []
    if reply_mode != "light":
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
    if persist_side_effects:
        for item in result.get("memory_candidates") or []:
            content = str(item.get("content") or "").strip()
            if content:
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
        topics = [str(item).strip() for item in (result.get("interest_topics") or []) if str(item).strip()]
        if "关注" in text and not topics:
            topics.append(text.split("关注", 1)[-1].strip(" 。！!"))
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
            user.interest_topics_json = dump_json(current[-20:])
        if allow_relation_delta:
            _apply_delta(relation, delta)
        for interaction in session.execute(
            select(MomentInteraction).where(MomentInteraction.actor_id == event.user_id, MomentInteraction.reflected_in_chat == False)  # noqa: E712
        ).scalars():
            interaction.reflected_in_chat = True
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


def handle_event(session: Session, event: EventIn) -> AppEventOut:
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
        if proactive.prepared_payload_json and proactive.prepared_payload_json != "{}":
            payload = DialoguePayload.model_validate(load_json(proactive.prepared_payload_json, {}))
        else:
            try:
                payload = _llm_dialogue(session, event, user, character, _proactive_target_text(proactive, event.event_type))
            except ProviderError:
                consume_proactive_event(session, proactive.proactive_event_id)
                raise
        if not payload.lines:
            session.commit()
            return _no_reply(event.session_id, reply_mode=payload.reply_mode, pace_reason=payload.pace_reason)
        _save_dialogue_lines(session, event, payload)
        mark_proactive_reflected(session, proactive)
        session.commit()
        return _event("dialogue", payload.model_dump(), event.session_id)
    if event.event_type in {"user_message", "option_selected"}:
        text = str(event.payload.get("text") or event.payload.get("reply_text") or "")
        if not text:
            raise ProviderError("user_message payload.text is required")
        _save_message(session, event=event, sender_type="user", sender_id=event.user_id, content=text)
        is_normal_reply_option = event.event_type == "user_message" and bool(str(event.payload.get("reply_id") or ""))
        payload = _llm_dialogue(session, event, user, character, text, allow_relation_delta=not is_normal_reply_option)
        if not payload.lines:
            session.commit()
            return _no_reply(event.session_id, reply_mode=payload.reply_mode, pace_reason=payload.pace_reason)
        _save_dialogue_lines(session, event, payload)
        session.commit()
        return _event("dialogue", payload.model_dump(), event.session_id)
    raise ProviderError(f"Unsupported event_type: {event.event_type}")
