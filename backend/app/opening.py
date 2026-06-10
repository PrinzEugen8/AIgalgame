from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .diagnostics import write_diagnostic
from .models import Character, OpeningCache, ProactiveEvent, User
from .pipeline import _event, _llm_dialogue, _no_reply, _proactive_target_text, _save_dialogue_lines, _tts_for_line
from .proactive import consume_proactive_event, pending_proactive_response
from .schemas import AppEventOut, DialogueLine, DialoguePayload, EventIn, RelationDelta
from .utils import dump_json, load_json, uid, utc_now


OPENING_CACHE_TTL = timedelta(hours=3)


GREETING_LINES: dict[str, list[tuple[str, str, str]]] = {
    "morning": [
        ("早上好。我已经醒了一会儿，刚好想听你说话。", "おはよう。少し前から起きていて、ちょうどあなたの声が聞きたかったの。", "happy"),
        ("早安。今天也慢慢来就好，我在这里等你。", "おはよう。今日もゆっくりで大丈夫、ここで待っているね。", "calm"),
        ("早上好，窗边的光很好。你来了，我就更安心一点。", "おはよう。窓辺の光がきれいだよ。来てくれて、少し安心した。", "happy"),
    ],
    "noon": [
        ("中午好。要不要先休息一下？我陪你待一会儿。", "こんにちは。少し休もうか。私もそばにいるね。", "calm"),
        ("午安。今天已经过了一半，你能回来我有点开心。", "こんにちは。今日はもう半分過ぎたね。戻ってきてくれて、少し嬉しい。", "happy"),
        ("中午好。我把想说的话先收好了，等你慢慢听。", "こんにちは。話したいことを先にしまっておいたから、ゆっくり聞いてね。", "thinking"),
    ],
    "evening": [
        ("晚上好。今天辛苦了，先把肩膀放松一点吧。", "こんばんは。今日もお疲れさま。まずは少し肩の力を抜こう。", "calm"),
        ("晚上好。我刚刚还在想，你差不多该回来了。", "こんばんは。そろそろ戻ってくるかなって、ちょうど考えていたところ。", "shy"),
        ("你回来了。今天的事可以慢慢讲给我听。", "おかえり。今日のこと、ゆっくり聞かせてね。", "happy"),
    ],
    "night": [
        ("这么晚还来了呀。那我小声一点陪你。", "こんな時間にも来てくれたんだね。じゃあ、小さな声でそばにいるよ。", "shy"),
        ("夜深了。别急着撑着，我陪你安静一会儿。", "夜も深いね。無理しないで、少し静かに一緒にいよう。", "calm"),
        ("欢迎回来。今天最后一点时间，也可以留给我们。", "おかえり。今日の最後の少しの時間、私たちにくれてもいいよ。", "happy"),
    ],
}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _now(local_time: datetime | None = None) -> datetime:
    if local_time is None:
        return datetime.now(timezone.utc)
    if local_time.tzinfo is None:
        return local_time.replace(tzinfo=timezone.utc)
    return local_time.astimezone(timezone.utc)


def _slot(local_time: datetime | None) -> str:
    hour = (local_time or datetime.now()).hour
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 17:
        return "noon"
    if 17 <= hour < 23:
        return "evening"
    return "night"


def _fresh_cache_query(session: Session, user_id: str, character_id: str, now_utc: datetime) -> Any:
    return (
        select(OpeningCache)
        .where(
            OpeningCache.user_id == user_id,
            OpeningCache.character_id == character_id,
            OpeningCache.status == "ready",
            OpeningCache.consumed_at == "",
        )
        .order_by(OpeningCache.created_at.desc())
    )


def _is_cache_fresh(cache: OpeningCache, now_utc: datetime) -> bool:
    expires_at = _parse_iso(cache.expires_at)
    return expires_at is None or expires_at > now_utc


def _find_ready_cache(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    now_utc: datetime,
    proactive_event_id: str = "",
) -> OpeningCache | None:
    rows = session.execute(_fresh_cache_query(session, user_id, character_id, now_utc)).scalars().all()
    for cache in rows:
        if proactive_event_id and cache.proactive_event_id != proactive_event_id:
            continue
        if _is_cache_fresh(cache, now_utc):
            return cache
        cache.status = "expired"
        cache.updated_at = utc_now()
    return None


def _store_opening_cache(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    kind: str,
    payload: DialoguePayload,
    now_utc: datetime,
    proactive_event_id: str = "",
) -> OpeningCache:
    for cache in session.execute(_fresh_cache_query(session, user_id, character_id, now_utc)).scalars():
        if proactive_event_id and cache.proactive_event_id != proactive_event_id:
            continue
        if not proactive_event_id and cache.kind != kind:
            continue
        cache.status = "replaced"
        cache.updated_at = utc_now()
    cache = OpeningCache(
        cache_id=uid("opening"),
        user_id=user_id,
        character_id=character_id,
        kind=kind,
        proactive_event_id=proactive_event_id,
        payload_json=dump_json(payload.model_dump()),
        status="ready",
        expires_at=(now_utc + OPENING_CACHE_TTL).isoformat(),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(cache)
    return cache


def _payload_from_cache(cache: OpeningCache) -> DialoguePayload:
    return DialoguePayload.model_validate(load_json(cache.payload_json, {}))


def _instant_greeting_payload(local_time: datetime | None) -> DialoguePayload:
    slot = _slot(local_time)
    choices = GREETING_LINES[slot]
    index = ((local_time or datetime.now()).day + (local_time or datetime.now()).hour) % len(choices)
    text, _, emotion = choices[index]
    return DialoguePayload(lines=[DialogueLine(line_id=uid("line"), text=text, emotion=emotion, pose=emotion)], relation_delta=RelationDelta())


def _greeting_payload(
    session: Session,
    user: User,
    character: Character,
    local_time: datetime | None,
    *,
    synthesize_tts: bool,
) -> DialoguePayload:
    slot = _slot(local_time)
    choices = GREETING_LINES[slot]
    basis = local_time or datetime.now()
    index = (basis.day + basis.hour) % len(choices)
    text, tts_text_ja, emotion = choices[index]
    tts_url = ""
    tts_error = ""
    if synthesize_tts:
        try:
            tts_url, tts_error = _tts_for_line(session, user, character, text, emotion, tts_text_ja=tts_text_ja)
        except Exception as exc:  # noqa: BLE001
            tts_error = str(exc)
            write_diagnostic("opening_greeting_tts_error", character_id=character.character_id, message=str(exc))
    return DialoguePayload(
        lines=[DialogueLine(line_id=uid("line"), text=text, emotion=emotion, pose=emotion, tts_audio_url=tts_url, tts_error=tts_error)],
        relation_delta=RelationDelta(),
        reply_mode="light",
        pace_reason="打开应用时没有可用主动事件，使用预制欢迎问候。",
    )


def _prepare_proactive_payload(session: Session, user: User, character: Character, proactive: ProactiveEvent) -> DialoguePayload:
    event = EventIn(
        event_type="notification_opened",
        user_id=user.user_id,
        character_id=character.character_id,
        session_id="opening_prepare",
        payload={"proactive_event_id": proactive.proactive_event_id},
    )
    return _llm_dialogue(
        session,
        event,
        user,
        character,
        _proactive_target_text(proactive, "notification_opened"),
        allow_relation_delta=False,
        persist_side_effects=False,
    )


def prepare_opening(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    local_time: datetime | None = None,
    proactive_event_id: str = "",
    allow_llm: bool = True,
) -> dict[str, Any]:
    user = session.get(User, user_id)
    character = session.get(Character, character_id)
    if user is None or character is None or not user.story_completed:
        return {"ok": True, "prepared": False, "reason": "story_not_ready"}
    now_utc = _now(local_time)
    existing = _find_ready_cache(session, user_id=user_id, character_id=character_id, now_utc=now_utc, proactive_event_id=proactive_event_id)
    if existing is not None:
        return {"ok": True, "prepared": True, "cache_id": existing.cache_id, "kind": existing.kind, "cached": True}

    proactive: ProactiveEvent | None = None
    if proactive_event_id:
        proactive = session.get(ProactiveEvent, proactive_event_id)
    elif allow_llm:
        pending = pending_proactive_response(session, user_id=user_id, character_id=character_id, local_time=local_time)
        event_payload = pending.get("event") or {}
        event_id = str(event_payload.get("proactive_event_id") or "")
        proactive = session.get(ProactiveEvent, event_id) if event_id else None

    if proactive is not None and allow_llm:
        try:
            payload = _prepare_proactive_payload(session, user, character, proactive)
            proactive.prepared_payload_json = dump_json(payload.model_dump())
            proactive.prepared_at = utc_now()
            proactive.prepare_error = ""
            cache = _store_opening_cache(
                session,
                user_id=user_id,
                character_id=character_id,
                kind="proactive",
                payload=payload,
                now_utc=now_utc,
                proactive_event_id=proactive.proactive_event_id,
            )
            session.commit()
            write_diagnostic("opening_prepared", kind="proactive", cache_id=cache.cache_id, proactive_event_id=proactive.proactive_event_id)
            return {"ok": True, "prepared": True, "cache_id": cache.cache_id, "kind": "proactive"}
        except Exception as exc:  # noqa: BLE001
            proactive.prepare_error = str(exc)
            proactive.updated_at = utc_now()
            session.commit()
            write_diagnostic("opening_prepare_error", kind="proactive", proactive_event_id=proactive.proactive_event_id, message=str(exc))

    payload = _greeting_payload(session, user, character, local_time, synthesize_tts=True)
    cache = _store_opening_cache(
        session,
        user_id=user_id,
        character_id=character_id,
        kind="greeting",
        payload=payload,
        now_utc=now_utc,
    )
    session.commit()
    write_diagnostic("opening_prepared", kind="greeting", cache_id=cache.cache_id)
    return {"ok": True, "prepared": True, "cache_id": cache.cache_id, "kind": "greeting"}


def consume_ready_opening(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    session_id: str = "android",
    local_time: datetime | None = None,
    proactive_event_id: str = "",
) -> AppEventOut:
    user = session.get(User, user_id)
    if user is None or not user.story_completed:
        return _no_reply(session_id, pace_reason="开场剧情尚未完成，不使用 opening 问候。")
    now_utc = _now(local_time)
    cache = _find_ready_cache(session, user_id=user_id, character_id=character_id, now_utc=now_utc, proactive_event_id=proactive_event_id)
    if cache is None:
        payload = _instant_greeting_payload(local_time)
        write_diagnostic("opening_ready_uncached_greeting", user_id=user_id, character_id=character_id)
        return _event("dialogue", {**payload.model_dump(), "opening_kind": "greeting", "cached": False}, session_id)

    payload = _payload_from_cache(cache)
    cache.consumed_at = utc_now()
    cache.status = "consumed"
    cache.updated_at = utc_now()
    source_event = EventIn(
        event_type="app_opened",
        user_id=user_id,
        character_id=character_id,
        session_id=session_id,
        payload={"opening_cache_id": cache.cache_id, "proactive_event_id": cache.proactive_event_id},
    )
    _save_dialogue_lines(session, source_event, payload)
    if cache.proactive_event_id:
        consume_proactive_event(session, cache.proactive_event_id)
    session.commit()
    write_diagnostic("opening_consumed", kind=cache.kind, cache_id=cache.cache_id, proactive_event_id=cache.proactive_event_id)
    return _event(
        "dialogue",
        {
            **payload.model_dump(),
            "opening_kind": cache.kind,
            "opening_cache_id": cache.cache_id,
            "proactive_event_id": cache.proactive_event_id,
            "cached": True,
        },
        session_id,
    )
