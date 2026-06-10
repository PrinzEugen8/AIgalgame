from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CalendarEvent, Experience, User
from .utils import uid, utc_now


MAINLAND_2026_HOLIDAYS: list[tuple[str, str, str]] = [
    ("2026-01-01", "元旦", "新年的第一天，小樱想把这天认真记下来。"),
    ("2026-01-02", "元旦假期", "假期里适合慢慢休息，也适合说些新年的愿望。"),
    ("2026-01-03", "元旦假期", "元旦假期的尾巴，可以把想做的事整理一下。"),
    ("2026-02-15", "春节假期", "春节假期开始了，是适合团圆和互相问候的日子。"),
    ("2026-02-16", "除夕", "除夕，小樱想把重要的人放在心里。"),
    ("2026-02-17", "春节", "春节，新的一年正式热闹起来了。"),
    ("2026-02-18", "春节假期", "春节假期里，小樱也会想听你讲讲今天。"),
    ("2026-02-19", "春节假期", "春节假期还在继续，适合留下温柔的回忆。"),
    ("2026-02-20", "春节假期", "假期中段，小樱想把轻松的心情分享给你。"),
    ("2026-02-21", "春节假期", "春节假期的安静片刻也值得记住。"),
    ("2026-02-22", "春节假期", "春节假期快到尾声，小樱想和你慢慢收心。"),
    ("2026-02-23", "春节假期", "假期最后一天，适合把想说的话补上。"),
    ("2026-04-04", "清明节假期", "清明假期，小樱会把脚步放轻一点。"),
    ("2026-04-05", "清明节", "清明，是适合想念和整理心情的日子。"),
    ("2026-04-06", "清明节假期", "清明假期的尾声，适合安静地走一走。"),
    ("2026-05-01", "劳动节", "劳动节，小樱想认真夸夸努力生活的人。"),
    ("2026-05-02", "劳动节假期", "劳动节假期里，可以给自己一点轻松时间。"),
    ("2026-05-03", "劳动节假期", "假期中适合计划一次小小的约定。"),
    ("2026-05-04", "劳动节假期", "劳动节假期还在继续，小樱想把心情整理好。"),
    ("2026-05-05", "劳动节假期", "假期最后一天，适合慢慢收尾。"),
    ("2026-06-19", "端午节", "端午节，小樱想把热闹和安稳都分给你一点。"),
    ("2026-06-20", "端午节假期", "端午假期里，适合留下一个轻松的下午。"),
    ("2026-06-21", "端午节假期", "端午假期的最后一天，小樱会期待你的消息。"),
    ("2026-09-25", "中秋节", "中秋节，是适合一起看月亮和说心里话的日子。"),
    ("2026-09-26", "中秋节假期", "中秋假期里，小樱想把温柔的话留给你。"),
    ("2026-09-27", "中秋节假期", "中秋假期尾声，适合慢慢回味。"),
    ("2026-10-01", "国庆节", "国庆假期开始了，小樱想和你一起把日子过热闹一点。"),
    ("2026-10-02", "国庆节假期", "国庆假期里，适合安排一次特别的小事。"),
    ("2026-10-03", "国庆节假期", "国庆假期还很长，小樱会期待新的话题。"),
    ("2026-10-04", "国庆节假期", "假期中的普通一天，也可以变成回忆。"),
    ("2026-10-05", "国庆节假期", "国庆假期中段，适合轻松散步。"),
    ("2026-10-06", "国庆节假期", "国庆假期快到尾声，小樱想听你说说近况。"),
    ("2026-10-07", "国庆节假期", "假期最后一天，适合把心情收进日记。"),
]

FIXED_SPECIAL_DAYS: list[tuple[str, str, str]] = [
    ("02-14", "情人节", "一个适合认真表达喜欢的日子。"),
    ("03-14", "白色情人节", "适合回应心意，也适合补上一句没说出口的话。"),
    ("05-20", "告白日", "听起来有点害羞，但小樱会记得这个日子。"),
    ("12-25", "圣诞节", "年底温柔又热闹的一天，小樱想和你一起记住。"),
]


def _event_id(prefix: str, *parts: str) -> str:
    return "_".join([prefix, *[part.replace("-", "") for part in parts]])


def _upsert_seed(
    session: Session,
    *,
    event_id: str,
    event_date: str,
    title: str,
    category: str,
    description: str,
    salience: int,
    repeats_yearly: bool = False,
    user_id: str = "",
    character_id: str = "",
    source_type: str = "",
    source_id: str = "",
) -> None:
    event = session.get(CalendarEvent, event_id)
    if event is not None:
        return
    session.add(
        CalendarEvent(
            event_id=event_id,
            user_id=user_id,
            character_id=character_id,
            event_date=event_date,
            title=title,
            category=category,
            description=description,
            salience=salience,
            repeats_yearly=repeats_yearly,
            source_type=source_type,
            source_id=source_id,
        )
    )


def ensure_calendar_events(session: Session, *, user_id: str, character_id: str) -> None:
    for event_date, title, description in MAINLAND_2026_HOLIDAYS:
        _upsert_seed(
            session,
            event_id=_event_id("cn_holiday", event_date, title),
            event_date=event_date,
            title=title,
            category="holiday",
            description=description,
            salience=90,
            source_type="mainland_holiday_2026",
        )
    for mmdd, title, description in FIXED_SPECIAL_DAYS:
        _upsert_seed(
            session,
            event_id=_event_id("fixed_special", mmdd, title),
            event_date=f"2000-{mmdd}",
            title=title,
            category="special",
            description=description,
            salience=78,
            repeats_yearly=True,
            source_type="fixed_special",
        )
    user = session.get(User, user_id)
    if user is not None:
        created_date = str(user.created_at or "")[:10]
        if len(created_date) == 10:
            _upsert_seed(
                session,
                event_id=f"anniversary_{user_id}_{character_id}",
                user_id=user_id,
                character_id=character_id,
                event_date=created_date,
                title="相识纪念日",
                category="relationship",
                description="这是你和小樱第一次相识的纪念日。",
                salience=96,
                repeats_yearly=True,
                source_type="relationship",
                source_id=user_id,
            )
    session.commit()


def _month_date_for_event(event: CalendarEvent, month: str) -> str | None:
    if event.repeats_yearly:
        return f"{month[:4]}-{event.event_date[5:10]}" if len(month) >= 4 and len(event.event_date) >= 10 else None
    return event.event_date if event.event_date.startswith(month) else None


def _status_for(day: str, today: date | None = None) -> str:
    today = today or date.today()
    try:
        event_day = date.fromisoformat(day)
    except ValueError:
        return "pending"
    if event_day < today:
        return "completed"
    if event_day == today:
        return "today"
    return "pending"


def _event_out(event: CalendarEvent, event_date: str, today: date | None = None) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "date": event_date,
        "start_at": f"{event_date}T00:00:00",
        "activity_title": event.title,
        "title": event.title,
        "category": event.category,
        "description": event.description,
        "status": _status_for(event_date, today),
        "salience": event.salience,
        "repeats_yearly": event.repeats_yearly,
        "source_type": event.source_type,
    }


def calendar_items(session: Session, *, user_id: str, character_id: str, month: str) -> list[dict[str, Any]]:
    ensure_calendar_events(session, user_id=user_id, character_id=character_id)
    rows = session.execute(
        select(CalendarEvent).where(
            CalendarEvent.hidden == False,  # noqa: E712
            CalendarEvent.user_id.in_(["", user_id]),
        )
    ).scalars().all()
    today = date.today()
    items: list[dict[str, Any]] = []
    for event in rows:
        if event.character_id not in {"", character_id}:
            continue
        event_date = _month_date_for_event(event, month)
        if not event_date:
            continue
        items.append(_event_out(event, event_date, today))
    return sorted(items, key=lambda item: (item["date"], -int(item["salience"]), item["title"]))


def day_note(session: Session, *, day: str) -> str:
    experiences = session.execute(
        select(Experience).where(Experience.created_at.like(f"{day}%")).order_by(Experience.created_at.desc()).limit(3)
    ).scalars().all()
    if experiences:
        summaries = "；".join(item.emotional_result or item.summary for item in experiences if item.summary or item.emotional_result)
        return f"小樱回想这一天：{summaries}" if summaries else ""
    try:
        event_day = date.fromisoformat(day)
    except ValueError:
        return ""
    if event_day < date.today():
        return "这一天已经过去了，小樱会把它当作一页安静的回忆。"
    if event_day == date.today():
        return "今天是特别的一天，小樱想把心情认真留在这里。"
    return "这一天还没到，小樱对它有一点小小的期待。"


def create_calendar_event(session: Session, payload: dict[str, Any]) -> CalendarEvent:
    event = CalendarEvent(
        event_id=str(payload.get("event_id") or uid("cal")),
        user_id=str(payload.get("user_id") or ""),
        character_id=str(payload.get("character_id") or ""),
        event_date=str(payload.get("date") or payload.get("event_date") or "")[:10],
        title=str(payload.get("title") or "").strip(),
        category=str(payload.get("category") or "relationship").strip() or "relationship",
        description=str(payload.get("description") or "").strip(),
        salience=max(0, min(100, int(payload.get("salience") or 80))),
        repeats_yearly=bool(payload.get("repeats_yearly", False)),
        source_type=str(payload.get("source_type") or "admin").strip(),
        source_id=str(payload.get("source_id") or "").strip(),
    )
    if not event.event_date or not event.title:
        raise ValueError("date and title are required")
    session.add(event)
    session.commit()
    return event


def update_calendar_event(session: Session, event_id: str, payload: dict[str, Any]) -> CalendarEvent | None:
    event = session.get(CalendarEvent, event_id)
    if event is None:
        return None
    if "date" in payload or "event_date" in payload:
        event.event_date = str(payload.get("date") or payload.get("event_date") or event.event_date)[:10]
    for field in ("user_id", "character_id", "title", "category", "description", "source_type", "source_id"):
        if field in payload:
            setattr(event, field, str(payload.get(field) or "").strip())
    if "salience" in payload:
        event.salience = max(0, min(100, int(payload.get("salience") or 0)))
    if "repeats_yearly" in payload:
        event.repeats_yearly = bool(payload.get("repeats_yearly"))
    if "hidden" in payload:
        event.hidden = bool(payload.get("hidden"))
    event.updated_at = utc_now()
    session.commit()
    return event

