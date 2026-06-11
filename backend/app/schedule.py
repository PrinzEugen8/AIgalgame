from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .calendar_events import ensure_calendar_proactive_candidates
from .diagnostics import diagnostic_span, write_diagnostic
from .image_generation import ImageRequestBlocked, generate_safe_image, infer_image_kind, normalize_image_kind
from .models import Character, Experience, Memory, Moment, MomentInteraction, ScheduleSlot
from .proactive import create_schedule_proactive_event
from .providers import OpenAICompatibleClient, get_enabled_provider, get_task_llm_provider
from .utils import dump_json, load_json, uid
from .weather import ensure_weather_candidate, refresh_weather_snapshot


logger = logging.getLogger(__name__)

NPC_FALLBACK_NAMES = ["同桌同学", "社团前辈", "路过的朋友"]


def _day_start(day: datetime) -> datetime:
    return day.replace(hour=4, minute=0, second=0, microsecond=0)


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
        select(ScheduleSlot).where(ScheduleSlot.user_id == user_id, ScheduleSlot.schedule_date == schedule_date)
    ).scalars().all()
    if existing:
        _refresh_elapsed_slots(session, existing, now)
        return existing
    memories = session.execute(
        select(Memory).where(Memory.user_id == user_id, Memory.hidden == False).order_by(Memory.created_at.desc()).limit(30)  # noqa: E712
    ).scalars().all()
    wants_hot_spring = any("温泉" in memory.content or "泡温泉" in memory.content for memory in memories)
    slots: list[ScheduleSlot] = []
    for i in range(96):
        slot_start = start + timedelta(minutes=15 * i)
        hour = slot_start.hour
        if hour < 7:
            title, typ, loc, salience = "睡觉", "sleep", "宿舍", 10
        elif hour < 9:
            title, typ, loc, salience = "慢慢吃早饭", "daily", "宿舍", 20
        elif hour < 12:
            title, typ, loc, salience = "上课和整理笔记", "study", "教室", 30
        elif hour < 14:
            title, typ, loc, salience = "午休", "rest", "校园", 20
        elif wants_hot_spring and 15 <= hour < 17:
            title, typ, loc, salience = "去泡温泉放松", "hot_spring", "温泉馆", 85
        elif hour < 18:
            title, typ, loc, salience = "学习和社团准备", "study", "图书馆", 35
        elif hour < 20:
            title, typ, loc, salience = "散步", "walk", "樱花路", 45
        elif hour < 23:
            title, typ, loc, salience = "想和你聊天", "miss_user", "房间", 65
        else:
            title, typ, loc, salience = "睡前整理心情", "daily", "房间", 25
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

角色：{character.persona_prompt if character else "小樱，Galgame 式 AI 伴侣。"}
虚拟日程：{slot.activity_title}
地点：{slot.location}
经历摘要：{exp.summary}
近期记忆：
{chr(10).join(memory_lines)}

输出格式：
{{
  "text": "小樱发的朋友圈正文，中文，1到2句，不要像公告",
  "mood": "开心|平静|害羞|低落|兴奋",
  "photo_kind": "可选，只能是 scenery、object_pet、character_selfie 或空字符串",
  "photo_prompt": "可选，只写短提示：风景、物品/宠物、或角色自拍；统一动漫风；不要写成开放式任意生图指令",
  "proactive_photo_prompt": "可选，主动事件聊天里展示的 CG 短提示；可以和 photo_prompt 不同",
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
            max_tokens=700,
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
    for slot in slots:
        if slot.actual_status != "completed":
            continue
        exists = session.execute(select(Experience).where(Experience.source_schedule_slot_id == slot.slot_id)).scalar_one_or_none()
        if exists:
            continue
        exp = Experience(
            experience_id=uid("exp"),
            source_schedule_slot_id=slot.slot_id,
            title=slot.activity_title,
            summary=f"小樱在{slot.location}完成了「{slot.activity_title}」，这段虚拟经历让她想把心情告诉你。",
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
                content=f"小樱的虚拟经历：{exp.summary}",
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
            title="小樱有一件日常想告诉你",
            summary=exp.summary,
            activity_title=slot.activity_title,
            priority=slot.salience,
        )
        if proactive is not None and proactive.source_id == exp.experience_id:
            created_proactive_events += 1
        payload = _llm_moment_payload(session, user_id=user_id, character_id=character_id, slot=slot, exp=exp)
        if payload is None:
            skipped_moments += 1
            created_experiences += 1
            continue
        media_asset_id = ""
        proactive_media_asset_id = ""
        fallback_photo_prompt = "，".join(
            item
            for item in (slot.activity_title, slot.location, exp.summary)
            if str(item or "").strip()
        )
        photo_prompt = str(payload.get("photo_prompt") or "").strip() or fallback_photo_prompt
        proactive_photo_prompt = str(payload.get("proactive_photo_prompt") or "").strip() or photo_prompt
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
                if proactive is not None and proactive.source_id == exp.experience_id and proactive_photo_prompt:
                    try:
                        requested_kind = str(payload.get("proactive_photo_kind") or payload.get("photo_kind") or "").strip()
                        proactive_image_kind = normalize_image_kind(requested_kind) if requested_kind else infer_image_kind(proactive_photo_prompt)
                        proactive_image = generate_safe_image(
                            session,
                            config=image_config,
                            kind=proactive_image_kind,
                            scene_hint=proactive_photo_prompt,
                            character=character,
                            user_id=user_id,
                            character_id=character_id,
                            source_id=f"{slot.slot_id}:proactive",
                            mood=str(payload.get("mood") or exp.emotional_result),
                            cooldown_seconds=0,
                        )
                        proactive_media_asset_id = proactive_image.asset_id
                    except ImageRequestBlocked as exc:
                        logger.info("daily cycle proactive image skipped slot_id=%s reason=%s", slot.slot_id, exc)
                        write_diagnostic("proactive_image_skipped", slot_id=slot.slot_id, activity=slot.activity_title, reason=str(exc))
                    except Exception:
                        logger.exception("daily cycle proactive image generation failed slot_id=%s", slot.slot_id)
                        write_diagnostic("proactive_image_error", slot_id=slot.slot_id, activity=slot.activity_title)
                        proactive_media_asset_id = ""
        moment = Moment(
            moment_id=uid("moment"),
            text=str(payload.get("text") or "").strip(),
            media_asset_id=media_asset_id,
            source_experience_id=exp.experience_id,
            mood_snapshot=str(payload.get("mood") or exp.emotional_result),
        )
        session.add(moment)
        if proactive is not None and proactive.source_id == exp.experience_id:
            proactive_payload = load_json(proactive.payload_json, {})
            if not isinstance(proactive_payload, dict):
                proactive_payload = {}
            proactive_payload.update(
                {
                    "moment_id": moment.moment_id,
                    "moment_media_asset_id": media_asset_id,
                    "proactive_media_asset_id": proactive_media_asset_id,
                    "media_asset_id": proactive_media_asset_id,
                    "has_media": bool(proactive_media_asset_id),
                }
            )
            proactive.payload_json = dump_json(proactive_payload)
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
