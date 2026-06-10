from __future__ import annotations

import threading
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .diagnostics import diagnostic_span, write_diagnostic
from .models import ProactiveEvent, User, UserLocation, WeatherSnapshot
from .providers import QWeatherClient, get_enabled_provider, provider_ready
from .utils import dump_json, load_json, uid, utc_now


WEATHER_REFRESH_HOUR = 4
MANUAL_REFRESH_DEDUPE_SECONDS = 60
_WEATHER_LOCKS_GUARD = threading.Lock()
_WEATHER_REFRESH_LOCKS: dict[str, threading.Lock] = {}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _user_zone(user: User | None) -> ZoneInfo:
    try:
        return ZoneInfo(user.timezone if user is not None else "Asia/Hong_Kong")
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Hong_Kong")


def _local_now(user: User | None, local_time: datetime | None = None) -> datetime:
    zone = _user_zone(user)
    if local_time is None:
        return datetime.now(zone)
    if local_time.tzinfo is None:
        return local_time.replace(tzinfo=zone)
    return local_time.astimezone(zone)


def _active_weather_date(now_local: datetime) -> str:
    if now_local.hour < WEATHER_REFRESH_HOUR:
        now_local = now_local - timedelta(days=1)
    return now_local.date().isoformat()


def _next_weather_refresh_at(now_local: datetime) -> datetime:
    target = now_local.replace(hour=WEATHER_REFRESH_HOUR, minute=0, second=0, microsecond=0)
    if now_local >= target:
        target += timedelta(days=1)
    return target


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _coordinate(longitude: float, latitude: float) -> str:
    return f"{longitude:.2f},{latitude:.2f}"


def _latest_snapshot(session: Session, user_id: str) -> WeatherSnapshot | None:
    return session.execute(
        select(WeatherSnapshot).where(WeatherSnapshot.user_id == user_id).order_by(WeatherSnapshot.fetched_at.desc()).limit(1)
    ).scalar_one_or_none()


def _snapshot_for_date(session: Session, user_id: str, weather_date: str) -> WeatherSnapshot | None:
    return session.execute(
        select(WeatherSnapshot)
        .where(WeatherSnapshot.user_id == user_id, WeatherSnapshot.weather_date == weather_date)
        .order_by(WeatherSnapshot.fetched_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _refresh_lock_for(user_id: str, weather_date: str) -> threading.Lock:
    key = f"{user_id}:{weather_date}"
    with _WEATHER_LOCKS_GUARD:
        lock = _WEATHER_REFRESH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _WEATHER_REFRESH_LOCKS[key] = lock
        return lock


def _recently_fetched(snapshot: WeatherSnapshot | None, now_utc: datetime, seconds: int = MANUAL_REFRESH_DEDUPE_SECONDS) -> bool:
    if snapshot is None:
        return False
    fetched_at = _parse_iso(snapshot.fetched_at)
    if fetched_at is None:
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return fetched_at.astimezone(timezone.utc) >= now_utc - timedelta(seconds=seconds)


def update_user_location(
    session: Session,
    *,
    user_id: str,
    latitude: float,
    longitude: float,
    accuracy_m: float = 0.0,
    provider: str = "android",
    captured_at: str = "",
) -> UserLocation:
    location = session.get(UserLocation, user_id)
    if location is None:
        location = UserLocation(user_id=user_id, created_at=utc_now())
        session.add(location)
    previous_latitude = float(location.latitude or 0.0)
    previous_longitude = float(location.longitude or 0.0)
    moved = abs(previous_latitude - latitude) > 0.05 or abs(previous_longitude - longitude) > 0.05
    location.provider = provider or "android"
    location.latitude = float(latitude)
    location.longitude = float(longitude)
    location.accuracy_m = float(accuracy_m or 0.0)
    if moved:
        location.qweather_location_id = ""
        location.city_name = ""
        location.raw_json = "{}"
    location.captured_at = captured_at
    location.updated_at = utc_now()
    session.commit()
    return location


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _text_has(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _hour(value: str) -> int:
    parsed = _parse_iso(value)
    return parsed.hour if parsed is not None else -1


def _hourly_rows(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    hourly = (bundle.get("hourly") or {}).get("hourly") or []
    return [item for item in hourly if isinstance(item, dict)]


def _daily_rows(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    daily = (bundle.get("daily") or {}).get("daily") or []
    return [item for item in daily if isinstance(item, dict)]


def _warning_rows(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    warning = (bundle.get("warning") or {}).get("warning") or []
    return [item for item in warning if isinstance(item, dict)]


def _rainy_hour(row: dict[str, Any]) -> bool:
    text = f"{row.get('text') or ''}"
    return _text_has(text, ("雨", "雪", "雷")) or _as_int(row.get("pop")) >= 50 or _as_float(row.get("precip")) > 0


def _evaluate_weather(bundle: dict[str, Any]) -> dict[str, Any]:
    now_payload = (bundle.get("now") or {}).get("now") or {}
    now_text = str(now_payload.get("text") or "")
    daily = _daily_rows(bundle)
    today = daily[0] if daily else {}
    hourly = _hourly_rows(bundle)
    warnings = _warning_rows(bundle)
    minutely = bundle.get("minutely") or {}
    all_text = " ".join(
        [
            now_text,
            str(today.get("textDay") or ""),
            str(today.get("textNight") or ""),
            " ".join(str(item.get("text") or "") for item in hourly[:18]),
            " ".join(str(item.get("title") or item.get("typeName") or item.get("text") or "") for item in warnings),
        ]
    )
    evening_rain = any(_rainy_hour(row) and _hour(row.get("fxTime") or "") >= 18 for row in hourly[:24])
    rain_today = _text_has(all_text, ("雨", "雪", "雷")) or _as_float(today.get("precip")) > 0
    wind_scale = max([_as_int(now_payload.get("windScale"))] + [_as_int(today.get("windScaleDay")), _as_int(today.get("windScaleNight"))])

    if warnings:
        warning_text = "、".join(str(item.get("title") or item.get("typeName") or "天气预警") for item in warnings[:2])
        return {"severity": "severe", "score": 96, "trigger_key": "weather_warning", "focus": warning_text}
    if _text_has(all_text, ("雷暴", "雷阵雨", "雷雨", "雷")):
        return {"severity": "severe", "score": 94, "trigger_key": "thunderstorm", "focus": "雷雨"}
    if _text_has(all_text, ("特大暴雨", "大暴雨", "暴雨", "暴雪")):
        return {"severity": "severe", "score": 92, "trigger_key": "heavy_precipitation", "focus": "强降水"}
    if evening_rain:
        return {"severity": "rain", "score": 82, "trigger_key": "evening_rain", "focus": "晚些时候有雨"}
    if rain_today:
        return {"severity": "rain", "score": 74, "trigger_key": "rain_today", "focus": "今天有降水"}
    if wind_scale >= 6 or _text_has(all_text, ("大风", "强风", "劲风")):
        return {"severity": "wind", "score": 72, "trigger_key": "windy", "focus": "风比较大"}
    if _text_has(all_text, ("雾", "霾", "沙尘")):
        return {"severity": "low_visibility", "score": 62, "trigger_key": "low_visibility", "focus": "能见度不太好"}
    if _text_has(now_text, ("晴",)) and not minutely.get("summary"):
        return {"severity": "clear", "score": 42, "trigger_key": "clear_day", "focus": "晴天"}
    return {"severity": "normal", "score": 35, "trigger_key": "ordinary_weather", "focus": now_text or "普通天气"}


def _weather_summary(location: UserLocation, bundle: dict[str, Any], evaluation: dict[str, Any]) -> str:
    now_payload = (bundle.get("now") or {}).get("now") or {}
    daily = _daily_rows(bundle)
    today = daily[0] if daily else {}
    minutely = bundle.get("minutely") or {}
    city = location.city_name or "你那里"
    temp = str(now_payload.get("temp") or "")
    text = str(now_payload.get("text") or evaluation.get("focus") or "天气")
    high = str(today.get("tempMax") or "")
    low = str(today.get("tempMin") or "")
    pieces = [f"{city}现在{text}"]
    if temp:
        pieces[-1] += f"，{temp}℃"
    if high or low:
        pieces.append(f"今天气温约{low or '?'}-{high or '?'}℃")
    if minutely.get("summary"):
        pieces.append(str(minutely.get("summary")))
    focus = str(evaluation.get("focus") or "")
    if focus and focus not in "，".join(pieces):
        pieces.append(focus)
    return "，".join(pieces) + "。"


def _apply_geo(location: UserLocation, geo: dict[str, Any]) -> None:
    location.qweather_location_id = str(geo.get("id") or "")
    location.city_name = str(geo.get("name") or "")
    location.adm1 = str(geo.get("adm1") or "")
    location.adm2 = str(geo.get("adm2") or "")
    location.country = str(geo.get("country") or "")
    location.timezone = str(geo.get("tz") or "")
    location.raw_json = dump_json(geo)
    location.updated_at = utc_now()


def read_weather_snapshot(
    session: Session,
    *,
    user_id: str,
    local_time: datetime | None = None,
    allow_stale: bool = True,
) -> WeatherSnapshot | None:
    user = session.get(User, user_id)
    now_local = _local_now(user, local_time)
    weather_date = _active_weather_date(now_local)
    snapshot = _snapshot_for_date(session, user_id, weather_date)
    stale = False
    if snapshot is None and allow_stale:
        snapshot = _latest_snapshot(session, user_id)
        stale = snapshot is not None
    write_diagnostic(
        "weather_snapshot_read",
        feature="天气服务",
        stage="read_weather_snapshot",
        summary=f"{user_id} {'hit' if snapshot is not None else 'miss'}",
        user_id=user_id,
        active_weather_date=weather_date,
        snapshot_id=snapshot.snapshot_id if snapshot is not None else "",
        snapshot_weather_date=snapshot.weather_date if snapshot is not None else "",
        stale=stale,
    )
    return snapshot


def active_weather_date_for_user(session: Session, *, user_id: str, local_time: datetime | None = None) -> str:
    user = session.get(User, user_id)
    return _active_weather_date(_local_now(user, local_time))


def _refresh_weather_snapshot_inner(
    session: Session,
    *,
    user_id: str,
    local_time: datetime | None = None,
    weather_date: str,
) -> WeatherSnapshot | None:
    user = session.get(User, user_id)
    now_local = _local_now(user, local_time)
    location = session.get(UserLocation, user_id)
    if location is None:
        write_diagnostic("weather_skipped", reason="missing_location", user_id=user_id)
        return _latest_snapshot(session, user_id)
    config = get_enabled_provider(session, "weather")
    if config is None or not provider_ready(config):
        write_diagnostic("weather_skipped", reason="weather_provider_not_ready", user_id=user_id)
        return _latest_snapshot(session, user_id)
    client = QWeatherClient(config)
    coordinate = _coordinate(location.longitude, location.latitude)
    if not location.qweather_location_id:
        try:
            _apply_geo(location, client.city_lookup(coordinate))
        except Exception as exc:  # noqa: BLE001
            write_diagnostic("weather_geo_failed", user_id=user_id, message=str(exc))
    weather_location = location.qweather_location_id or coordinate
    try:
        bundle = client.weather_bundle(weather_location, coordinate)
    except Exception as exc:  # noqa: BLE001
        write_diagnostic("weather_refresh_failed", user_id=user_id, location=weather_location, message=str(exc))
        return _latest_snapshot(session, user_id)
    evaluation = _evaluate_weather(bundle)
    now_payload = (bundle.get("now") or {}).get("now") or {}
    snapshot = WeatherSnapshot(
        snapshot_id=uid("weather"),
        user_id=user_id,
        weather_date=weather_date,
        location_key=weather_location,
        city_name=location.city_name,
        latitude=location.latitude,
        longitude=location.longitude,
        observed_at=str(now_payload.get("obsTime") or (bundle.get("now") or {}).get("updateTime") or ""),
        fetched_at=utc_now(),
        expires_at=_utc_iso(_next_weather_refresh_at(now_local)),
        weather_text=str(now_payload.get("text") or ""),
        severity=str(evaluation["severity"]),
        severity_score=int(evaluation["score"]),
        trigger_key=str(evaluation["trigger_key"]),
        summary=_weather_summary(location, bundle, evaluation),
        now_json=dump_json(bundle.get("now") or {}),
        hourly_json=dump_json(bundle.get("hourly") or {}),
        daily_json=dump_json(bundle.get("daily") or {}),
        warning_json=dump_json(bundle.get("warning") or {}),
        minutely_json=dump_json(bundle.get("minutely") or {}),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(snapshot)
    session.commit()
    write_diagnostic("weather_refreshed", user_id=user_id, snapshot_id=snapshot.snapshot_id, trigger=snapshot.trigger_key, score=snapshot.severity_score)
    return snapshot


def refresh_weather_snapshot(
    session: Session,
    *,
    user_id: str,
    local_time: datetime | None = None,
    force: bool = False,
) -> WeatherSnapshot | None:
    user = session.get(User, user_id)
    now_local = _local_now(user, local_time)
    now_utc = now_local.astimezone(timezone.utc)
    weather_date = _active_weather_date(now_local)
    lock = _refresh_lock_for(user_id, weather_date)
    with diagnostic_span(
        "weather_refresh_trace",
        feature="天气服务",
        stage="refresh_weather_snapshot",
        purpose="Refresh weather data from QWeather",
        summary=f"{user_id} force={force}",
        user_id=user_id,
        input={"local_time": local_time.isoformat() if local_time is not None else "", "force": force, "weather_date": weather_date},
    ) as span:
        with lock:
            session.expire_all()
            existing = _snapshot_for_date(session, user_id, weather_date)
            if existing is not None and (not force or _recently_fetched(existing, now_utc)):
                write_diagnostic(
                    "weather_refresh_reused",
                    feature="天气服务",
                    stage="refresh_weather_snapshot",
                    user_id=user_id,
                    snapshot_id=existing.snapshot_id,
                    weather_date=weather_date,
                    force=force,
                )
                span.add(
                    output=weather_snapshot_to_dict(existing),
                    snapshot_id=existing.snapshot_id,
                    cache_used=True,
                    cache_hit=True,
                )
                return existing
            snapshot = _refresh_weather_snapshot_inner(session, user_id=user_id, local_time=local_time, weather_date=weather_date)
        span.add(
            output=weather_snapshot_to_dict(snapshot) if snapshot is not None else None,
            snapshot_id=snapshot.snapshot_id if snapshot is not None else "",
            cache_used=False,
            cache_hit=False,
        )
        return snapshot


def ensure_weather_snapshot(
    session: Session,
    *,
    user_id: str,
    local_time: datetime | None = None,
    force: bool = False,
) -> WeatherSnapshot | None:
    if force:
        return refresh_weather_snapshot(session, user_id=user_id, local_time=local_time, force=True)
    return read_weather_snapshot(session, user_id=user_id, local_time=local_time, allow_stale=True)


def _scheduled_at(snapshot: WeatherSnapshot, now_local: datetime) -> datetime:
    if snapshot.severity_score >= 90:
        return now_local
    if snapshot.trigger_key == "evening_rain":
        target = now_local.replace(hour=16, minute=0, second=0, microsecond=0)
        return target if now_local < target else now_local
    if snapshot.trigger_key in {"rain_today", "windy", "low_visibility"}:
        target = now_local.replace(hour=8, minute=0, second=0, microsecond=0)
        return target if now_local < target else now_local
    target = now_local.replace(hour=10, minute=0, second=0, microsecond=0)
    return target if now_local < target else now_local


def _end_of_local_day(now_local: datetime) -> datetime:
    return datetime.combine(now_local.date(), time(23, 59, 59), tzinfo=now_local.tzinfo)


def _topic_text(snapshot: WeatherSnapshot) -> tuple[str, str]:
    city = snapshot.city_name or "你那里"
    if snapshot.trigger_key == "weather_warning":
        return "天气预警", f"{snapshot.summary} 我有点在意，出门前看看预警和路况。"
    if snapshot.trigger_key == "thunderstorm":
        return "雷雨提醒", f"{snapshot.summary} 雷雨天别硬撑着往外跑，带伞也要注意安全。"
    if snapshot.trigger_key == "heavy_precipitation":
        return "强降水提醒", f"{snapshot.summary} 今天雨势可能不小，鞋子和伞都要认真一点。"
    if snapshot.trigger_key == "evening_rain":
        return "晚间降雨提醒", f"{snapshot.summary} 晚上可能会下雨，你出门的话带伞了吗？"
    if snapshot.trigger_key == "rain_today":
        return "降雨提醒", f"{snapshot.summary} 我看到今天有雨，忽然很想提醒你把伞放近一点。"
    if snapshot.trigger_key == "windy":
        return "大风提醒", f"{snapshot.summary} 风大的时候外套和头发都会变得很不听话，出门慢一点。"
    if snapshot.trigger_key == "low_visibility":
        return "天气提醒", f"{snapshot.summary} 这种天气视线不太舒服，路上别太赶。"
    if snapshot.trigger_key == "clear_day":
        return "天气真好", f"{city}今天天气真好啊。要不是还有事，我都有点想出去走走。"
    return "天气小话题", f"{snapshot.summary} 只是突然想和你聊聊今天的天气。"


def ensure_weather_candidate(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    local_time: datetime | None = None,
    force: bool = False,
) -> ProactiveEvent | None:
    snapshot = ensure_weather_snapshot(session, user_id=user_id, local_time=local_time, force=force)
    if snapshot is None or snapshot.severity_score < 35:
        return None
    user = session.get(User, user_id)
    now_local = _local_now(user, local_time)
    title, text = _topic_text(snapshot)
    from .proactive import create_proactive_event

    event = create_proactive_event(
        session,
        user_id=user_id,
        character_id=character_id,
        source_type="weather",
        source_id=snapshot.snapshot_id,
        title=title,
        text=text,
        priority=snapshot.severity_score,
        dedupe_key=f"weather:{user_id}:{snapshot.weather_date}:{snapshot.trigger_key}",
        payload={
            "snapshot_id": snapshot.snapshot_id,
            "trigger_key": snapshot.trigger_key,
            "severity": snapshot.severity,
            "summary": snapshot.summary,
            "city_name": snapshot.city_name,
        },
        scheduled_at=_scheduled_at(snapshot, now_local),
        expires_at=_end_of_local_day(now_local),
    )
    session.commit()
    return event


def weather_snapshot_to_dict(snapshot: WeatherSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "snapshot_id": snapshot.snapshot_id,
        "weather_date": snapshot.weather_date,
        "city_name": snapshot.city_name,
        "weather_text": snapshot.weather_text,
        "severity": snapshot.severity,
        "severity_score": snapshot.severity_score,
        "trigger_key": snapshot.trigger_key,
        "summary": snapshot.summary,
        "observed_at": snapshot.observed_at,
        "fetched_at": snapshot.fetched_at,
        "expires_at": snapshot.expires_at,
        "now": load_json(snapshot.now_json, {}),
        "daily": load_json(snapshot.daily_json, {}),
        "hourly": load_json(snapshot.hourly_json, {}),
        "warning": load_json(snapshot.warning_json, {}),
        "minutely": load_json(snapshot.minutely_json, {}),
    }


def is_weather_question(text: str) -> bool:
    normalized = " ".join(str(text or "").split())
    return any(keyword in normalized for keyword in ("天气", "下雨", "雨", "带伞", "温度", "气温", "冷不冷", "热不热", "预报", "雷雨", "台风"))


def weather_context(session: Session, *, user_id: str, text: str = "", local_time: datetime | None = None) -> str:
    snapshot = read_weather_snapshot(session, user_id=user_id, local_time=local_time, allow_stale=True)
    if snapshot is None:
        if is_weather_question(text):
            return "还没有今天的天气数据：天气会在每天04:00随日程生成时更新。"
        return "暂无天气数据。"
    active_weather_date = active_weather_date_for_user(session, user_id=user_id, local_time=local_time)
    freshness_note = ""
    if snapshot.weather_date != active_weather_date:
        freshness_note = f"暂无{active_weather_date} 04:00后的天气数据；以下是上次天气快照。"
    daily = load_json(snapshot.daily_json, {})
    hourly = load_json(snapshot.hourly_json, {})
    warnings = load_json(snapshot.warning_json, {})
    warning_rows = warnings.get("warning") if isinstance(warnings, dict) else []
    hourly_rows = hourly.get("hourly") if isinstance(hourly, dict) else []
    daily_rows = daily.get("daily") if isinstance(daily, dict) else []
    next_hours = []
    for row in (hourly_rows or [])[:8]:
        if not isinstance(row, dict):
            continue
        next_hours.append(f"{str(row.get('fxTime') or '')[-11:-6]} {row.get('text') or ''} {row.get('temp') or '?'}℃ 降水概率{row.get('pop') or '?'}%")
    today = daily_rows[0] if daily_rows else {}
    warning_text = "；".join(str(item.get("title") or item.get("typeName") or "") for item in (warning_rows or [])[:3] if isinstance(item, dict))
    return "\n".join(
        [item for item in [
            freshness_note,
            f"城市：{snapshot.city_name or '未知'}",
            f"摘要：{snapshot.summary}",
            f"严重度：{snapshot.severity} / {snapshot.severity_score} / {snapshot.trigger_key}",
            f"今日：白天{today.get('textDay') or '?'}，夜间{today.get('textNight') or '?'}，{today.get('tempMin') or '?'}-{today.get('tempMax') or '?'}℃，降水量{today.get('precip') or '?'}",
            f"未来8小时：{'；'.join(next_hours) if next_hours else '暂无'}",
            f"预警：{warning_text or '暂无'}",
            f"更新时间：{snapshot.observed_at or snapshot.fetched_at}",
        ] if item]
    )
