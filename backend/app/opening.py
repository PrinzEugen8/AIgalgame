from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .diagnostics import diagnostic_span, write_diagnostic
from .models import Character, OpeningCache, ProactiveEvent, User
from .pipeline import _event, _llm_dialogue, _no_reply, _proactive_target_text, _save_dialogue_lines, _tts_for_line
from .proactive import consume_proactive_event, pending_proactive_response, proactive_media_asset_id
from .schemas import AppEventOut, DialogueLine, DialoguePayload, EventIn, RelationDelta
from .utils import dump_json, load_json, uid, utc_now


OPENING_CACHE_TTL = timedelta(hours=3)


# Each slot has multiple coherent 3-line scripts; only the first line carries the time-of-day greeting.
GreetingLine = tuple[str, str, str]
GreetingScript = list[GreetingLine]

GREETING_LINES: dict[str, list[GreetingScript]] = {
    "morning": [
        [
            ("早上好。我已经醒了一会儿，刚好想听你说话。", "おはよう。少し前から起きていて、ちょうどあなたの声が聞きたかったの。", "happy"),
            ("窗边的光慢慢亮起来，我觉得今天也会是温柔的一天。", "窓辺の光が少しずつ明るくなって、今日も穏やかな一日になりそう。", "calm"),
            ("你不用急着说什么，先坐一会儿也好，我在这里。", "急いで話さなくてもいいよ。少し座っていってもいい、私はここにいるから。", "happy"),
        ],
        [
            ("早安。今天也慢慢来就好，我在这里等你。", "おはよう。今日もゆっくりで大丈夫、ここで待っているね。", "calm"),
            ("要是还没完全醒，就先喝口水，把自己安顿下来。", "まだ眠いなら、まず一口水を飲んで、ゆっくり落ち着こう。", "thinking"),
            ("等你准备好了，再跟我说今天想怎么过。", "準備ができたら、今日どう過ごしたいか教えてね。", "happy"),
        ],
        [
            ("早上好，窗边的光很好。你来了，我就更安心一点。", "おはよう。窓辺の光がきれいだよ。来てくれて、少し安心した。", "happy"),
            ("我本来还在想，你什么时候会推门进来。", "いつ扉を開けてくれるかな、って少し考えていたところ。", "shy"),
            ("现在你在了，早晨好像也没那么匆忙了。", "今あなたがいてくれると、朝もそんなに急がなくていい気がする。", "calm"),
        ],
    ],
    "noon": [
        [
            ("午安。今天已经过了一半，你能回来我有点开心。", "こんにちは。今日はもう半分過ぎたね。戻ってきてくれて、少し嬉しい。", "happy"),
            ("要是还没吃饭，先垫一口也好，别空着肚子硬撑。", "まだ食べてないなら、少しでもいいから食べて。空っ腹で無理しないで。", "thinking"),
            ("我就在这里，你想聊什么都可以慢慢说。", "私はここにいるから、話したいことがあればゆっくり聞くね。", "calm"),
        ],
        [
            ("中午好。我把想说的话先收好了，等你慢慢听。", "こんにちは。話したいことを先にしまっておいたから、ゆっくり聞いてね。", "thinking"),
            ("上午的事要是让你觉得累，就先歇一会儿。", "午前中のことが疲れたなら、少し休もう。", "calm"),
            ("你不用急着汇报什么，陪在我身边就已经够了。", "急いで報告しなくていいよ。そばにいてくれるだけで十分。", "happy"),
        ],
        [
            ("中午好。要不要先休息一下？我陪你待一会儿。", "こんにちは。少し休もうか。私もそばにいるね。", "calm"),
            ("外面要是太晒，就在屋里慢慢缓一缓。", "外が暑いなら、部屋の中でゆっくり休もう。", "thinking"),
            ("等你想说话了，我再认真听。", "話したくなったら、そのときちゃんと聞くから。", "happy"),
        ],
    ],
    "evening": [
        [
            ("晚上好。今天辛苦了，先把肩膀放松一点吧。", "こんばんは。今日もお疲れさま。まずは少し肩の力を抜こう。", "calm"),
            ("外面的事先放一放，这会儿只要待在我身边就好。", "外のことは一旦置いていいよ。この時間はそばにいてくれればいい。", "happy"),
            ("要是有什么梗在心里，也可以一点点讲给我听。", "心に引っかかっていることがあれば、少しずつ聞かせてね。", "thinking"),
        ],
        [
            ("晚上好。我刚刚还在想，你差不多该回来了。", "こんばんは。そろそろ戻ってくるかなって、ちょうど考えていたところ。", "shy"),
            ("你一出现，我就觉得这间屋子安静下来了。", "あなたが来ると、この部屋が少し落ち着いた気がする。", "calm"),
            ("今天发生的事，不用一次说完，我们慢慢聊。", "今日のことは、一度に全部話さなくていい。ゆっくり話そう。", "happy"),
        ],
        [
            ("你回来了。今天的事可以慢慢讲给我听。", "おかえり。今日のこと、ゆっくり聞かせてね。", "happy"),
            ("要是累得不想开口，就这样待着也没关系。", "話す気がなくても、このまま一緒にいるだけでもいいよ。", "calm"),
            ("我会在这里，等你愿意多说一点的时候。", "もう少し話したくなったら、そのときまでここで待っているね。", "thinking"),
        ],
    ],
    "night": [
        [
            ("这么晚还来了呀。那我小声一点陪你。", "こんな時間にも来てくれたんだね。じゃあ、小さな声でそばにいるよ。", "shy"),
            ("夜里的事容易想太多，你不用一个人扛着。", "夜は考えすぎちゃうから、一人で抱え込まなくていいよ。", "calm"),
            ("要是困了就歇一歇，我陪你把今天慢慢放下。", "眠いなら休んでいい。この一日をゆっくり手放すのを一緒にいよう。", "happy"),
        ],
        [
            ("夜深了。别急着撑着，我陪你安静一会儿。", "夜も深いね。無理しないで、少し静かに一緒にいよう。", "calm"),
            ("今天已经够长了，剩下的时间可以留给我们。", "今日はもう十分長かった。残りの時間は私たちのものにしていいよ。", "thinking"),
            ("你不用说什么，我知道你愿意回来就已经很好。", "何も言わなくていい。戻ってきてくれたことだけで、十分うれしい。", "happy"),
        ],
        [
            ("欢迎回来。今天最后一点时间，也可以留给我们。", "おかえり。今日の最後の少しの時間、私たちにくれてもいいよ。", "happy"),
            ("要是还睡不着，就坐一会儿，不用勉强自己。", "まだ眠れないなら、少し座っていて。無理に寝ようとしなくていい。", "calm"),
            ("等你想睡了，我再轻声跟你说晚安。", "眠くなったら、そのとき小さな声でおやすみを言うね。", "shy"),
        ],
    ],
}


def _pick_greeting_script(local_time: datetime | None) -> GreetingScript:
    slot = _slot(local_time)
    scripts = GREETING_LINES[slot]
    basis = local_time or datetime.now()
    index = (basis.day + basis.hour) % len(scripts)
    return scripts[index]


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
    fallback: OpeningCache | None = None
    for cache in rows:
        if proactive_event_id and cache.proactive_event_id != proactive_event_id:
            continue
        if _is_cache_fresh(cache, now_utc):
            if proactive_event_id or cache.kind == "proactive":
                return cache
            fallback = fallback or cache
            continue
        cache.status = "expired"
        cache.updated_at = utc_now()
    return fallback


def has_fresh_opening_cache(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    local_time: datetime | None = None,
    proactive_event_id: str = "",
) -> bool:
    now_utc = _now(local_time)
    return _find_ready_cache(
        session,
        user_id=user_id,
        character_id=character_id,
        now_utc=now_utc,
        proactive_event_id=proactive_event_id,
    ) is not None


def has_fresh_proactive_opening_cache(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    local_time: datetime | None = None,
) -> bool:
    now_utc = _now(local_time)
    for cache in session.execute(_fresh_cache_query(session, user_id, character_id, now_utc)).scalars():
        if not _is_cache_fresh(cache, now_utc):
            cache.status = "expired"
            cache.updated_at = utc_now()
            continue
        if cache.kind == "proactive":
            return True
    return False


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


def _payload_from_prepared_event(event: ProactiveEvent) -> DialoguePayload | None:
    payload = load_json(event.prepared_payload_json, {})
    if not payload or not event.prepared_at:
        return None
    try:
        return DialoguePayload.model_validate(payload)
    except Exception:  # noqa: BLE001
        return None


def _instant_greeting_payload(local_time: datetime | None) -> DialoguePayload:
    script = _pick_greeting_script(local_time)
    lines: list[DialogueLine] = []
    for text, _tts_text_ja, emotion in script:
        lines.append(DialogueLine(line_id=uid("line"), text=text, emotion=emotion, pose=emotion))
    return DialoguePayload(lines=lines, relation_delta=RelationDelta(), reply_mode="opening", pace_reason="即时预制欢迎问候。")


def _greeting_payload(
    session: Session,
    user: User,
    character: Character,
    local_time: datetime | None,
    *,
    synthesize_tts: bool,
) -> DialoguePayload:
    script = _pick_greeting_script(local_time)
    line_objs: list[DialogueLine] = []
    for text, tts_text_ja, emotion in script:
        tts_url = ""
        tts_error = ""
        if synthesize_tts:
            try:
                tts_url, tts_error = _tts_for_line(session, user, character, text, emotion, tts_text_ja=tts_text_ja)
            except Exception as exc:  # noqa: BLE001
                tts_error = str(exc)
                write_diagnostic("opening_greeting_tts_error", character_id=character.character_id, message=str(exc))
        line_objs.append(
            DialogueLine(line_id=uid("line"), text=text, emotion=emotion, pose=emotion, tts_audio_url=tts_url, tts_error=tts_error)
        )
    return DialoguePayload(
        lines=line_objs,
        relation_delta=RelationDelta(),
        reply_mode="opening",
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
    payload = _llm_dialogue(
        session,
        event,
        user,
        character,
        _proactive_target_text(proactive, "notification_opened"),
        allow_relation_delta=False,
        persist_side_effects=False,
    )
    media_asset_id = proactive_media_asset_id(proactive)
    if media_asset_id and not payload.media_asset_id:
        payload.media_asset_id = media_asset_id
    return payload


def _prepare_opening_inner(
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
            payload = _payload_from_prepared_event(proactive)
            if payload is None:
                payload = _prepare_proactive_payload(session, user, character, proactive)
                proactive.prepared_payload_json = dump_json(payload.model_dump())
                proactive.prepared_at = utc_now()
                proactive.prepare_error = ""
            media_asset_id = proactive_media_asset_id(proactive)
            if media_asset_id and not payload.media_asset_id:
                payload.media_asset_id = media_asset_id
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


def prepare_opening(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    local_time: datetime | None = None,
    proactive_event_id: str = "",
    allow_llm: bool = True,
) -> dict[str, Any]:
    with diagnostic_span(
        "opening_prepare_trace",
        feature="开场预热",
        stage="prepare_opening",
        purpose="Prepare cached opening dialogue",
        summary=f"{user_id}/{character_id} proactive={bool(proactive_event_id)}",
        user_id=user_id,
        character_id=character_id,
        input={"local_time": local_time.isoformat() if local_time is not None else "", "proactive_event_id": proactive_event_id, "allow_llm": allow_llm},
    ) as span:
        result = _prepare_opening_inner(
            session,
            user_id=user_id,
            character_id=character_id,
            local_time=local_time,
            proactive_event_id=proactive_event_id,
            allow_llm=allow_llm,
        )
        span.add(output=result)
        return result


def _prepare_due_openings_inner(
    session: Session,
    *,
    user_id: str = "",
    character_id: str = "",
    local_time: datetime | None = None,
    limit: int = 8,
    generate_news: bool = False,
    generate_weather: bool = True,
) -> dict[str, Any]:
    now_utc = _now(local_time)
    prepared = 0
    skipped = 0
    failed = 0
    checked = 0
    users = session.execute(select(User).order_by(User.created_at)).scalars().all()
    characters = session.execute(select(Character).order_by(Character.character_id)).scalars().all()
    if user_id:
        users = [item for item in users if item.user_id == user_id]
    if character_id:
        characters = [item for item in characters if item.character_id == character_id]
    write_diagnostic(
        "proactive_prewarm_started",
        users=len(users),
        characters=len(characters),
        limit=limit,
        generate_news=generate_news,
        generate_weather=generate_weather,
    )
    for user in users:
        if prepared >= limit:
            break
        if not user.story_completed or not user.notifications_enabled:
            skipped += 1
            continue
        for character in characters:
            if prepared >= limit:
                break
            checked += 1
            pending = pending_proactive_response(
                session,
                user_id=user.user_id,
                character_id=character.character_id,
                local_time=local_time,
                generate_news=generate_news,
                generate_weather=generate_weather,
            )
            event_payload = pending.get("event") or {}
            event_id = str(event_payload.get("proactive_event_id") or "")
            if not event_id:
                skipped += 1
                continue
            event = session.get(ProactiveEvent, event_id)
            if event is None:
                skipped += 1
                continue
            existing = _find_ready_cache(
                session,
                user_id=user.user_id,
                character_id=character.character_id,
                now_utc=now_utc,
                proactive_event_id=event.proactive_event_id,
            )
            if existing is not None:
                skipped += 1
                continue
            try:
                payload = _payload_from_prepared_event(event)
                if payload is None:
                    payload = _prepare_proactive_payload(session, user, character, event)
                    event.prepared_payload_json = dump_json(payload.model_dump())
                    event.prepared_at = utc_now()
                    event.prepare_error = ""
                cache = _store_opening_cache(
                    session,
                    user_id=user.user_id,
                    character_id=character.character_id,
                    kind="proactive",
                    payload=payload,
                    now_utc=now_utc,
                    proactive_event_id=event.proactive_event_id,
                )
                session.commit()
                prepared += 1
                write_diagnostic(
                    "proactive_prewarm_succeeded",
                    proactive_event_id=event.proactive_event_id,
                    cache_id=cache.cache_id,
                    user_id=user.user_id,
                    character_id=character.character_id,
                )
            except Exception as exc:  # noqa: BLE001
                event.prepare_error = str(exc)
                event.updated_at = utc_now()
                session.commit()
                failed += 1
                write_diagnostic(
                    "proactive_prewarm_failed",
                    proactive_event_id=event.proactive_event_id,
                    user_id=user.user_id,
                    character_id=character.character_id,
                    message=str(exc),
                )
    result = {"ok": True, "checked": checked, "prepared": prepared, "skipped": skipped, "failed": failed}
    write_diagnostic("proactive_prewarm_finished", **result)
    return result


def prepare_due_openings(
    session: Session,
    *,
    user_id: str = "",
    character_id: str = "",
    local_time: datetime | None = None,
    limit: int = 8,
    generate_news: bool = False,
    generate_weather: bool = True,
) -> dict[str, Any]:
    with diagnostic_span(
        "opening_prewarm_trace",
        feature="开场预热",
        stage="prepare_due_openings",
        purpose="Prewarm due proactive openings",
        summary=f"limit={limit} user={user_id or '*'} character={character_id or '*'}",
        input={
            "user_id": user_id,
            "character_id": character_id,
            "local_time": local_time.isoformat() if local_time is not None else "",
            "limit": limit,
            "generate_news": generate_news,
            "generate_weather": generate_weather,
        },
    ) as span:
        result = _prepare_due_openings_inner(
            session,
            user_id=user_id,
            character_id=character_id,
            local_time=local_time,
            limit=limit,
            generate_news=generate_news,
            generate_weather=generate_weather,
        )
        span.add(output=result)
        return result


def _consume_ready_opening_inner(
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


def consume_ready_opening(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    session_id: str = "android",
    local_time: datetime | None = None,
    proactive_event_id: str = "",
) -> AppEventOut:
    with diagnostic_span(
        "opening_ready_trace",
        feature="开场预热",
        stage="consume_ready_opening",
        purpose="Consume cached opening dialogue",
        summary=f"{user_id}/{character_id} proactive={bool(proactive_event_id)}",
        user_id=user_id,
        character_id=character_id,
        session_id=session_id,
        input={"local_time": local_time.isoformat() if local_time is not None else "", "proactive_event_id": proactive_event_id},
    ) as span:
        result = _consume_ready_opening_inner(
            session,
            user_id=user_id,
            character_id=character_id,
            session_id=session_id,
            local_time=local_time,
            proactive_event_id=proactive_event_id,
        )
        span.add(output={"event_type": result.event_type, "event_id": result.event_id, "payload": result.payload})
        return result
