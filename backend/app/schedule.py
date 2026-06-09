from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Experience, Memory, Moment, ScheduleSlot
from .providers import ImageProvider, get_enabled_provider
from .utils import load_json, uid


logger = logging.getLogger(__name__)


def _day_start(day: datetime) -> datetime:
    return day.replace(hour=4, minute=0, second=0, microsecond=0)


def ensure_schedule(session: Session, *, user_id: str, character_id: str, day: datetime) -> list[ScheduleSlot]:
    start = _day_start(day)
    schedule_date = start.date().isoformat()
    existing = session.execute(
        select(ScheduleSlot).where(ScheduleSlot.user_id == user_id, ScheduleSlot.schedule_date == schedule_date)
    ).scalars().all()
    if existing:
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
        slot = ScheduleSlot(
            slot_id=uid("slot"),
            schedule_date=schedule_date,
            user_id=user_id,
            character_id=character_id,
            start_at=slot_start.isoformat(),
            end_at=(slot_start + timedelta(minutes=15)).isoformat(),
            activity_title=title,
            activity_type=typ,
            location=loc,
            actual_status="completed" if slot_start < datetime.now(slot_start.tzinfo) else "pending",
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


def run_daily_cycle(session: Session, *, user_id: str, character_id: str, day: datetime) -> dict[str, int]:
    ensure_schedule(session, user_id=user_id, character_id=character_id, day=day)
    slots = session.execute(
        select(ScheduleSlot).where(ScheduleSlot.user_id == user_id, ScheduleSlot.can_generate_moment == True)  # noqa: E712
    ).scalars().all()
    created_experiences = 0
    created_moments = 0
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
        media_asset_id = ""
        if slot.can_generate_photo:
            image_config = get_enabled_provider(session, "image")
            if image_config is not None:
                try:
                    image = ImageProvider(image_config).generate(
                        session,
                        f"adult anime galgame CG, Sakura, {slot.activity_title}, {slot.location}, cherry blossom color grade",
                    )
                    media_asset_id = image.asset_id
                except Exception:
                    logger.exception("daily cycle image generation failed slot_id=%s", slot.slot_id)
                    media_asset_id = ""
        session.add(
            Moment(
                moment_id=uid("moment"),
                text=f"{slot.activity_title}结束啦。总觉得如果告诉你，你会笑着说我做得不错。",
                media_asset_id=media_asset_id,
                source_experience_id=exp.experience_id,
                mood_snapshot=exp.emotional_result,
            )
        )
        created_experiences += 1
        created_moments += 1
    session.commit()
    return {"experiences": created_experiences, "moments": created_moments}
