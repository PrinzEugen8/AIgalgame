from __future__ import annotations

from datetime import datetime, time
from typing import Any

from .models import User


def _parse_hhmm(value: str, fallback: time) -> time:
    try:
        hour, minute = str(value or "").split(":", 1)
        return time(int(hour), int(minute[:2]))
    except Exception:  # noqa: BLE001
        return fallback


def _time_in_range(current: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def _parse_iso_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        from datetime import timezone

        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def get_user_availability(user: User, now_local: datetime) -> dict[str, Any]:
    sleep_start = _parse_hhmm(user.sleep_start, time(0, 30))
    sleep_end = _parse_hhmm(user.sleep_end, time(8, 0))
    slots = [
        {"type": "sleep", "start": sleep_start.strftime("%H:%M"), "end": sleep_end.strftime("%H:%M")},
    ]
    current = now_local.time()
    unavailable: list[str] = []
    for slot in slots:
        start = _parse_hhmm(str(slot["start"]), time(0, 0))
        end = _parse_hhmm(str(slot["end"]), time(0, 0))
        if _time_in_range(current, start, end):
            unavailable.append(str(slot["type"]))
    return {
        "slots": slots,
        "currently_unavailable_types": unavailable,
        "is_unavailable": bool(unavailable),
    }


def is_event_in_busy_window(event_payload: dict[str, Any], now_local: datetime) -> bool:
    delivery_timing = str(event_payload.get("delivery_timing") or "").strip().lower()
    if delivery_timing == "on_time":
        return False
    busy_start = _parse_iso_dt(event_payload.get("busy_start"))
    busy_end = _parse_iso_dt(event_payload.get("busy_end"))
    if busy_start is None or busy_end is None:
        return False
    zone = now_local.tzinfo
    start_local = busy_start.astimezone(zone) if zone is not None else busy_start
    end_local = busy_end.astimezone(zone) if zone is not None else busy_end
    return start_local <= now_local < end_local


def is_user_unavailable(user: User, now_local: datetime, *, allow_penetration: bool = False) -> bool:
    if allow_penetration:
        return False
    return bool(get_user_availability(user, now_local)["is_unavailable"])
