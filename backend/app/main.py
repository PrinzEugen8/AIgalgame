from __future__ import annotations

import ipaddress
import logging
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .calendar_events import calendar_items, create_calendar_event, day_note, ensure_calendar_events, update_calendar_event
from .database import get_session, init_db
from .diagnostics import runtime_logs, tail_diagnostics, write_diagnostic
from .logging_setup import maybe_start_debugger, setup_logging
from .models import (
    Character,
    CalendarEvent,
    MediaAsset,
    Memory,
    Moment,
    MomentInteraction,
    Message,
    OpeningCache,
    ProviderConfig,
    ProactiveEvent,
    RelationState,
    ScheduleSlot,
    TtsVoiceProfile,
    User,
    UserLocation,
)
from .opening import consume_ready_opening, prepare_due_openings, prepare_opening
from .online import mark_offline, mark_online
from .pipeline import handle_event
from .proactive import consume_proactive_event, create_moment_feedback_event, mark_proactive_delivered, pending_proactive_response
from .providers import (
    OpenAICompatibleClient,
    ProviderError,
    VolcSeedTtsClient,
    get_enabled_provider,
    provider_presets,
    provider_to_out,
    run_provider_test,
    upsert_provider,
)
from .schemas import (
    CharacterAdminIn,
    CharacterAdminOut,
    EventIn,
    ProviderConfigIn,
    ProviderConfigOut,
    ProviderTestRequest,
    ProviderTestResult,
    TtsVoiceProfileIn,
    TtsVoiceProfileOut,
)
from .schedule import ensure_schedule, run_daily_cycle
from .scheduler import start_scheduler, stop_scheduler
from .seed import DEFAULT_CHARACTER_ID, DEFAULT_USER_ID, ensure_seed
from .utils import clamp, dump_json, load_json, uid
from .weather import ensure_weather_candidate, read_weather_snapshot, refresh_weather_snapshot, update_user_location, weather_snapshot_to_dict


setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Galgame Android Demo Backend")
ADMIN_DIR = Path(__file__).resolve().parent / "admin_static"

app.mount("/admin/assets", StaticFiles(directory=ADMIN_DIR), name="admin_assets")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_http_request(request: Request, call_next: Any) -> Any:
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.exception("request failed method=%s path=%s elapsed_ms=%s", request.method, request.url.path, elapsed_ms)
        raise
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "request completed method=%s path=%s status=%s elapsed_ms=%s",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


@app.on_event("startup")
def startup() -> None:
    logger.info(
        "starting backend host=%s port=%s db_path=%s data_dir=%s log_dir=%s",
        settings.host,
        settings.port,
        settings.db_path,
        settings.data_dir,
        settings.log_dir,
    )
    maybe_start_debugger()
    init_db()
    with next(get_session()) as session:
        ensure_seed(session)
    start_scheduler()
    logger.info("backend startup complete")


@app.on_event("shutdown")
def shutdown() -> None:
    logger.info("backend shutting down")
    stop_scheduler()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "ai-galgame-backend", "lan_ready": True}


def _ip_score(address: str) -> int:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return -1000
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or address.startswith("198.18."):
        return -1000
    parts = [int(part) for part in address.split(".")]
    score = 0
    if ip.is_private:
        score += 100
    if parts[-1] not in (0, 1, 255):
        score += 40
    if address.startswith("192.168."):
        score += 30
    elif address.startswith("10."):
        score += 20
    elif parts[0] == 172 and 16 <= parts[1] <= 31:
        score += 10
    return score


def _candidate_lan_ips() -> list[str]:
    addresses: set[str] = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(str(item[4][0]))
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            addresses.add(str(sock.getsockname()[0]))
    except OSError:
        pass
    return sorted(addresses, key=_ip_score, reverse=True)


def _detect_lan_ip() -> str:
    candidates = [address for address in _candidate_lan_ips() if _ip_score(address) > 0]
    if candidates:
        return candidates[0]
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


@app.get("/admin")
def admin_page() -> FileResponse:
    return FileResponse(ADMIN_DIR / "index.html")


@app.get("/api/admin/pairing")
def admin_pairing() -> dict[str, Any]:
    lan_ip = _detect_lan_ip()
    server_url = f"http://{lan_ip}:{settings.port}"
    return {"server_url": server_url, "admin_url": f"{server_url}/admin"}


@app.get("/api/admin/status")
def admin_status(session: Session = Depends(get_session)) -> dict[str, Any]:
    providers = [provider_to_out(item).model_dump() for item in session.execute(select(ProviderConfig)).scalars().all()]
    configured = {
        kind: any(item["kind"] == kind and item["ready"] for item in providers)
        for kind in ("llm", "llm_task", "tts", "search", "weather", "image")
    }
    return {"ok": True, "providers": providers, "configured": configured}


def _voice_to_out(voice: TtsVoiceProfile) -> TtsVoiceProfileOut:
    return TtsVoiceProfileOut(
        voice_id=voice.voice_id,
        provider_id=voice.provider_id,
        label=voice.label,
        speaker=voice.speaker,
        resource_id=voice.resource_id,
        language=voice.language,
        enabled=voice.enabled,
        last_test_ok=voice.last_test_ok,
        last_test_message=voice.last_test_message,
        updated_at=voice.updated_at,
    )


def _character_to_out(character: Character) -> CharacterAdminOut:
    return CharacterAdminOut(
        character_id=character.character_id,
        name=character.name,
        age_setting=character.age_setting,
        persona_prompt=character.persona_prompt,
        speech_style=character.speech_style,
        relationship_boundary=character.relationship_boundary,
        tts_voice_type=character.tts_voice_type,
        tts_voice_profile_id=character.tts_voice_profile_id,
        key_reply_threshold=character.key_reply_threshold,
    )


def _user_to_out(user: User) -> dict[str, Any]:
    return {
        "user_id": user.user_id,
        "display_name": user.display_name,
        "timezone": user.timezone,
        "sleep_start": user.sleep_start,
        "sleep_end": user.sleep_end,
        "interest_topics": load_json(user.interest_topics_json, []),
        "proactive_daily_limit": user.proactive_daily_limit,
        "proactive_next_check_at": user.proactive_next_check_at,
        "proactive_judgement": load_json(user.proactive_judgement_json, {}),
        "notifications_enabled": user.notifications_enabled,
        "widget_bubbles_enabled": user.widget_bubbles_enabled,
        "news_enabled": user.news_enabled,
        "tts_enabled": user.tts_enabled,
        "story_completed": user.story_completed,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


def _relation_to_out(relation: RelationState) -> dict[str, Any]:
    return {
        "id": relation.id,
        "user_id": relation.user_id,
        "character_id": relation.character_id,
        "affection": relation.affection,
        "trust": relation.trust,
        "dependency": relation.dependency,
        "mood": relation.mood,
        "relationship_stage": relation.relationship_stage,
        "last_interaction_at": relation.last_interaction_at,
        "updated_at": relation.updated_at,
    }


def _memory_to_out(memory: Memory) -> dict[str, Any]:
    return {
        "memory_id": memory.memory_id,
        "user_id": memory.user_id,
        "character_id": memory.character_id,
        "layer": memory.layer,
        "content": memory.content,
        "source_event_id": memory.source_event_id,
        "importance": memory.importance,
        "confidence": memory.confidence,
        "hidden": memory.hidden,
        "created_at": memory.created_at,
    }


def _calendar_event_to_out(event: CalendarEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "user_id": event.user_id,
        "character_id": event.character_id,
        "date": event.event_date,
        "title": event.title,
        "category": event.category,
        "description": event.description,
        "salience": event.salience,
        "repeats_yearly": event.repeats_yearly,
        "source_type": event.source_type,
        "source_id": event.source_id,
        "hidden": event.hidden,
        "created_at": event.created_at,
        "updated_at": event.updated_at,
    }


def _proactive_event_to_out(event: ProactiveEvent) -> dict[str, Any]:
    return {
        "proactive_event_id": event.proactive_event_id,
        "user_id": event.user_id,
        "character_id": event.character_id,
        "source_type": event.source_type,
        "source_id": event.source_id,
        "title": event.title,
        "text": event.text,
        "priority": event.priority,
        "status": event.status,
        "dedupe_key": event.dedupe_key,
        "scheduled_at": event.scheduled_at,
        "expires_at": event.expires_at,
        "prepared": bool(load_json(event.prepared_payload_json, {}) and event.prepared_at),
        "prepared_at": event.prepared_at,
        "prepare_error": event.prepare_error,
        "delivered_at": event.delivered_at,
        "opened_at": event.opened_at,
        "reflected_at": event.reflected_at,
        "created_at": event.created_at,
        "updated_at": event.updated_at,
    }


def _page_bounds(page: int, page_size: int) -> tuple[int, int]:
    safe_page = max(1, int(page or 1))
    safe_size = max(1, min(int(page_size or 20), 100))
    return safe_page, safe_size


def _page_response(items: list[Any], total: int, page: int, page_size: int) -> dict[str, Any]:
    return {"ok": True, "items": items, "total": total, "page": page, "page_size": page_size}


def _paginate_scalars(session: Session, stmt: Any, *, page: int, page_size: int) -> tuple[list[Any], int, int, int]:
    page, page_size = _page_bounds(page, page_size)
    total = int(session.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one() or 0)
    rows = session.execute(stmt.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    return rows, total, page, page_size


def _ensure_relation(session: Session, user_id: str, character_id: str = DEFAULT_CHARACTER_ID) -> RelationState:
    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one_or_none()
    if relation is None:
        relation = RelationState(user_id=user_id, character_id=character_id)
        session.add(relation)
        session.commit()
    return relation


def _update_user_fields(user: User, payload: dict[str, Any]) -> None:
    for field in ("display_name", "timezone", "sleep_start", "sleep_end"):
        if field in payload:
            setattr(user, field, str(payload.get(field) or "").strip())
    if "proactive_daily_limit" in payload:
        user.proactive_daily_limit = str(payload.get("proactive_daily_limit") or "low").strip() or "low"
    for field in ("notifications_enabled", "widget_bubbles_enabled", "news_enabled", "tts_enabled", "story_completed"):
        if field in payload:
            setattr(user, field, bool(payload.get(field)))
    if "interest_topics" in payload:
        topics = [str(item).strip() for item in payload.get("interest_topics") or [] if str(item).strip()]
        user.interest_topics_json = dump_json(topics[-20:])
    user.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


@app.get("/api/admin/users")
def admin_users(page: int = 1, page_size: int = 20, q: str = "", session: Session = Depends(get_session)) -> dict[str, Any]:
    ensure_seed(session)
    stmt = select(User)
    query = q.strip()
    if query:
        like = f"%{query}%"
        stmt = stmt.where(or_(User.user_id.like(like), User.display_name.like(like)))
    stmt = stmt.order_by(User.created_at.desc())
    users, total, page, page_size = _paginate_scalars(session, stmt, page=page, page_size=page_size)
    user_ids = [item.user_id for item in users]
    relation_stmt = select(RelationState).where(RelationState.user_id.in_(user_ids)).order_by(RelationState.user_id)
    relations = session.execute(relation_stmt).scalars().all() if user_ids else []
    return {
        **_page_response([_user_to_out(item) for item in users], total, page, page_size),
        "relations": [_relation_to_out(item) for item in relations],
    }


@app.post("/api/admin/users")
def admin_create_user(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    user_id = str(payload.get("user_id") or "").strip() or uid("user")
    if session.get(User, user_id) is not None:
        raise HTTPException(status_code=409, detail="user_id already exists")
    user = User(user_id=user_id)
    _update_user_fields(user, payload)
    session.add(user)
    session.commit()
    ensure_seed(session, user_id=user_id, character_id=str(payload.get("character_id") or DEFAULT_CHARACTER_ID))
    return _user_to_out(session.get(User, user_id))


@app.put("/api/admin/users/{user_id}")
def admin_update_user(user_id: str, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    _update_user_fields(user, payload)
    session.commit()
    return _user_to_out(user)


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    for model, field in (
        (RelationState, RelationState.user_id),
        (Memory, Memory.user_id),
        (ScheduleSlot, ScheduleSlot.user_id),
        (Message, Message.user_id),
        (ProactiveEvent, ProactiveEvent.user_id),
        (OpeningCache, OpeningCache.user_id),
        (CalendarEvent, CalendarEvent.user_id),
    ):
        session.query(model).filter(field == user_id).delete(synchronize_session=False)
    session.delete(user)
    session.commit()
    return {"ok": True}


@app.put("/api/admin/users/{user_id}/relation")
def admin_update_relation(user_id: str, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    character_id = str(payload.get("character_id") or DEFAULT_CHARACTER_ID)
    if session.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    relation = _ensure_relation(session, user_id, character_id)
    for field in ("affection", "trust", "dependency"):
        if field in payload:
            setattr(relation, field, clamp(int(payload.get(field) or 0), 0, 1000))
    if "mood" in payload:
        relation.mood = clamp(int(payload.get("mood") or 0), -100, 100)
    if "relationship_stage" in payload:
        relation.relationship_stage = str(payload.get("relationship_stage") or "").strip() or relation.relationship_stage
    relation.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    session.commit()
    return _relation_to_out(relation)


@app.get("/api/admin/users/{user_id}/memories")
def admin_memories(user_id: str, page: int = 1, page_size: int = 20, q: str = "", session: Session = Depends(get_session)) -> dict[str, Any]:
    stmt = select(Memory).where(Memory.user_id == user_id)
    query = q.strip()
    if query:
        like = f"%{query}%"
        stmt = stmt.where(or_(Memory.content.like(like), Memory.layer.like(like), Memory.character_id.like(like)))
    stmt = stmt.order_by(Memory.created_at.desc())
    memories, total, page, page_size = _paginate_scalars(session, stmt, page=page, page_size=page_size)
    return _page_response([_memory_to_out(item) for item in memories], total, page, page_size)


@app.post("/api/admin/users/{user_id}/memories")
def admin_create_memory(user_id: str, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    if session.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    content = str(payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content required")
    memory = Memory(
        memory_id=str(payload.get("memory_id") or uid("mem")),
        user_id=user_id,
        character_id=str(payload.get("character_id") or DEFAULT_CHARACTER_ID),
        layer=str(payload.get("layer") or "chat"),
        content=content,
        source_event_id=str(payload.get("source_event_id") or "admin"),
        importance=float(payload.get("importance") or 0.5),
        confidence=float(payload.get("confidence") or 0.8),
        hidden=bool(payload.get("hidden", False)),
    )
    session.add(memory)
    session.commit()
    return _memory_to_out(memory)


@app.put("/api/admin/memories/{memory_id}")
def admin_update_memory(memory_id: str, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    memory = session.get(Memory, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="memory not found")
    for field in ("layer", "content", "source_event_id"):
        if field in payload:
            setattr(memory, field, str(payload.get(field) or "").strip())
    if "importance" in payload:
        memory.importance = float(payload.get("importance") or 0)
    if "confidence" in payload:
        memory.confidence = float(payload.get("confidence") or 0)
    if "hidden" in payload:
        memory.hidden = bool(payload.get("hidden"))
    session.commit()
    return _memory_to_out(memory)


@app.delete("/api/admin/memories/{memory_id}")
def admin_delete_memory(memory_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    memory = session.get(Memory, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="memory not found")
    session.delete(memory)
    session.commit()
    return {"ok": True}


@app.get("/api/admin/calendar-events")
def admin_calendar_events(
    user_id: str = DEFAULT_USER_ID,
    character_id: str = DEFAULT_CHARACTER_ID,
    page: int = 1,
    page_size: int = 20,
    q: str = "",
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_seed(session, user_id=user_id, character_id=character_id)
    ensure_calendar_events(session, user_id=user_id, character_id=character_id)
    stmt = select(CalendarEvent).where(CalendarEvent.user_id.in_(["", user_id]), CalendarEvent.character_id.in_(["", character_id]))
    query = q.strip()
    if query:
        like = f"%{query}%"
        stmt = stmt.where(or_(CalendarEvent.title.like(like), CalendarEvent.description.like(like), CalendarEvent.category.like(like)))
    stmt = stmt.order_by(CalendarEvent.event_date, CalendarEvent.salience.desc())
    events, total, page, page_size = _paginate_scalars(session, stmt, page=page, page_size=page_size)
    return _page_response([_calendar_event_to_out(item) for item in events], total, page, page_size)


@app.post("/api/admin/calendar-events")
def admin_create_calendar_event(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        event = create_calendar_event(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _calendar_event_to_out(event)


@app.put("/api/admin/calendar-events/{event_id}")
def admin_update_calendar_event(event_id: str, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    event = update_calendar_event(session, event_id, payload)
    if event is None:
        raise HTTPException(status_code=404, detail="calendar event not found")
    return _calendar_event_to_out(event)


@app.delete("/api/admin/calendar-events/{event_id}")
def admin_delete_calendar_event(event_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    event = session.get(CalendarEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="calendar event not found")
    session.delete(event)
    session.commit()
    return {"ok": True}


@app.get("/api/admin/proactive-events")
def admin_proactive_events(
    user_id: str = "",
    character_id: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 20,
    q: str = "",
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(ProactiveEvent)
    if user_id:
        stmt = stmt.where(ProactiveEvent.user_id == user_id)
    if character_id:
        stmt = stmt.where(ProactiveEvent.character_id == character_id)
    if status:
        stmt = stmt.where(ProactiveEvent.status == status)
    query = q.strip()
    if query:
        like = f"%{query}%"
        stmt = stmt.where(or_(ProactiveEvent.title.like(like), ProactiveEvent.text.like(like), ProactiveEvent.source_type.like(like)))
    stmt = stmt.order_by(ProactiveEvent.created_at.desc())
    events, total, page, page_size = _paginate_scalars(session, stmt, page=page, page_size=page_size)
    return _page_response([_proactive_event_to_out(item) for item in events], total, page, page_size)


@app.post("/api/admin/proactive-events/prewarm")
def admin_prewarm_proactive(payload: dict[str, Any] | None = None, session: Session = Depends(get_session)) -> dict[str, Any]:
    body = payload or {}
    user_id = str(body.get("user_id") or "")
    character_id = str(body.get("character_id") or DEFAULT_CHARACTER_ID)
    return prepare_due_openings(
        session,
        user_id=user_id,
        character_id=character_id,
        limit=int(body.get("limit") or 8),
        generate_news=bool(body.get("generate_news", False)),
    )


@app.get("/api/admin/tts-voices")
def list_tts_voices(session: Session = Depends(get_session)) -> dict[str, Any]:
    voices = session.execute(select(TtsVoiceProfile).order_by(TtsVoiceProfile.updated_at.desc())).scalars().all()
    providers = [
        provider_to_out(item).model_dump()
        for item in session.execute(select(ProviderConfig).where(ProviderConfig.kind == "tts")).scalars().all()
    ]
    return {"ok": True, "items": [_voice_to_out(item).model_dump() for item in voices], "providers": providers}


@app.post("/api/admin/tts-voices", response_model=TtsVoiceProfileOut)
def create_tts_voice(payload: TtsVoiceProfileIn, session: Session = Depends(get_session)) -> TtsVoiceProfileOut:
    provider_id = payload.provider_id
    if not provider_id:
        provider = get_enabled_provider(session, "tts")
        provider_id = provider.provider_id if provider is not None else ""
    voice_id = payload.voice_id or f"voice_{uid('tts')[-8:]}"
    if session.get(TtsVoiceProfile, voice_id) is not None:
        raise HTTPException(status_code=409, detail="voice_id already exists")
    voice = TtsVoiceProfile(
        voice_id=voice_id,
        provider_id=provider_id,
        label=payload.label or payload.speaker,
        speaker=payload.speaker.strip(),
        resource_id=payload.resource_id.strip(),
        language=payload.language,
        enabled=payload.enabled,
    )
    if not voice.speaker or not voice.resource_id:
        raise HTTPException(status_code=400, detail="speaker and resource_id are required")
    session.add(voice)
    session.commit()
    return _voice_to_out(voice)


@app.put("/api/admin/tts-voices/{voice_id}", response_model=TtsVoiceProfileOut)
def update_tts_voice(voice_id: str, payload: TtsVoiceProfileIn, session: Session = Depends(get_session)) -> TtsVoiceProfileOut:
    voice = session.get(TtsVoiceProfile, voice_id)
    if voice is None:
        raise HTTPException(status_code=404, detail="voice not found")
    voice.provider_id = payload.provider_id or voice.provider_id
    voice.label = payload.label or payload.speaker
    voice.speaker = payload.speaker.strip()
    voice.resource_id = payload.resource_id.strip()
    voice.language = payload.language
    voice.enabled = payload.enabled
    voice.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    if not voice.speaker or not voice.resource_id:
        raise HTTPException(status_code=400, detail="speaker and resource_id are required")
    session.commit()
    return _voice_to_out(voice)


@app.delete("/api/admin/tts-voices/{voice_id}")
def delete_tts_voice(voice_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    voice = session.get(TtsVoiceProfile, voice_id)
    if voice is None:
        raise HTTPException(status_code=404, detail="voice not found")
    for character in session.execute(select(Character).where(Character.tts_voice_profile_id == voice_id)).scalars():
        character.tts_voice_profile_id = ""
    session.delete(voice)
    session.commit()
    return {"ok": True}


@app.post("/api/admin/tts-voices/{voice_id}/test", response_model=TtsVoiceProfileOut)
def test_tts_voice(voice_id: str, payload: dict[str, Any] | None = None, session: Session = Depends(get_session)) -> TtsVoiceProfileOut:
    voice = session.get(TtsVoiceProfile, voice_id)
    if voice is None:
        raise HTTPException(status_code=404, detail="voice not found")
    config = session.get(ProviderConfig, voice.provider_id) if voice.provider_id else get_enabled_provider(session, "tts")
    if config is None:
        raise HTTPException(status_code=400, detail="tts provider is not configured")
    text = str((payload or {}).get("text") or ("今日は少しだけ声の調子を試します。" if voice.language == "ja" else "今天也想听你说说话。"))
    try:
        asset = VolcSeedTtsClient(config).synthesize(session, text, voice_type=voice.speaker, resource_id=voice.resource_id)
    except Exception as exc:  # noqa: BLE001
        voice.last_test_ok = False
        voice.last_test_message = str(exc)
    else:
        voice.last_test_ok = True
        voice.last_test_message = f"OK: {asset.url}"
    voice.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    session.commit()
    return _voice_to_out(voice)


@app.get("/api/admin/characters")
def list_characters(session: Session = Depends(get_session)) -> dict[str, Any]:
    ensure_seed(session)
    characters = session.execute(select(Character).order_by(Character.character_id)).scalars().all()
    return {"ok": True, "items": [_character_to_out(item).model_dump() for item in characters]}


@app.put("/api/admin/characters", response_model=CharacterAdminOut)
def update_default_character(payload: CharacterAdminIn, session: Session = Depends(get_session)) -> CharacterAdminOut:
    return update_character(DEFAULT_CHARACTER_ID, payload, session)


@app.put("/api/admin/characters/{character_id}", response_model=CharacterAdminOut)
def update_character(character_id: str, payload: CharacterAdminIn, session: Session = Depends(get_session)) -> CharacterAdminOut:
    ensure_seed(session, character_id=character_id)
    character = session.get(Character, character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="character not found")
    if payload.name is not None:
        character.name = payload.name.strip() or character.name
    if payload.persona_prompt is not None:
        character.persona_prompt = payload.persona_prompt.strip()
    if payload.speech_style is not None:
        character.speech_style = payload.speech_style.strip()
    if payload.relationship_boundary is not None:
        character.relationship_boundary = payload.relationship_boundary.strip()
    if payload.tts_voice_profile_id is not None:
        if payload.tts_voice_profile_id and session.get(TtsVoiceProfile, payload.tts_voice_profile_id) is None:
            raise HTTPException(status_code=400, detail="tts voice profile not found")
        character.tts_voice_profile_id = payload.tts_voice_profile_id
    if payload.key_reply_threshold is not None:
        character.key_reply_threshold = clamp(payload.key_reply_threshold, 0, 100)
    character.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    session.commit()
    return _character_to_out(character)


@app.get("/api/bootstrap")
def bootstrap(
    user_id: str = DEFAULT_USER_ID,
    character_id: str = DEFAULT_CHARACTER_ID,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_seed(session, user_id=user_id, character_id=character_id)
    user = session.get(User, user_id)
    character = session.get(Character, character_id)
    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one()
    return {
        "user": {
            "user_id": user_id,
            "story_completed": bool(user and user.story_completed),
            "interest_topics": load_json(user.interest_topics_json if user else "[]", []),
        },
        "character": {
            "character_id": character_id,
            "name": character.name if character else "小樱",
            "age_setting": character.age_setting if character else "18+",
        },
        "relation": {
            "affection": relation.affection,
            "trust": relation.trust,
            "dependency": relation.dependency,
            "mood": relation.mood,
            "stage": relation.relationship_stage,
        },
    }


@app.get("/api/config/provider-presets")
def config_presets() -> dict[str, Any]:
    return provider_presets()


@app.post("/api/config/providers", response_model=ProviderConfigOut)
def save_provider(payload: ProviderConfigIn, session: Session = Depends(get_session)) -> ProviderConfigOut:
    return provider_to_out(upsert_provider(session, payload))


@app.post("/api/config/providers/test", response_model=ProviderTestResult)
def test_provider(payload: ProviderTestRequest, session: Session = Depends(get_session)) -> ProviderTestResult:
    return run_provider_test(session, payload, payload.test_text)


@app.get("/api/config/providers/models")
def provider_models(provider_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    config = session.get(ProviderConfig, provider_id)
    if config is None:
        raise HTTPException(status_code=404, detail="provider not found")
    try:
        return {"ok": True, "models": OpenAICompatibleClient(config).list_models()}
    except Exception as exc:  # noqa: BLE001
        logger.exception("provider model lookup failed provider_id=%s", provider_id)
        return {"ok": False, "models": [], "message": str(exc)}


@app.post("/api/events")
def post_event(event: EventIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        result = handle_event(session, event)
        write_diagnostic("event_ok", event_type=event.event_type, session_id=event.session_id, result_type=result.event_type)
        return result.model_dump()
    except ProviderError as exc:
        logger.warning(
            "provider error during event event_type=%s session_id=%s message=%s",
            event.event_type,
            event.session_id,
            exc,
        )
        write_diagnostic("event_error", event_type=event.event_type, session_id=event.session_id, error_type=type(exc).__name__, message=str(exc))
        return {"event_type": "error", "event_id": uid("evt"), "session_id": event.session_id, "payload": {"message": str(exc)}}


def _parse_client_time(value: str = "") -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _location_to_out(location: UserLocation | None) -> dict[str, Any] | None:
    if location is None:
        return None
    return {
        "user_id": location.user_id,
        "provider": location.provider,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "accuracy_m": location.accuracy_m,
        "qweather_location_id": location.qweather_location_id,
        "city_name": location.city_name,
        "adm1": location.adm1,
        "adm2": location.adm2,
        "country": location.country,
        "timezone": location.timezone,
        "captured_at": location.captured_at,
        "updated_at": location.updated_at,
    }


@app.post("/api/location")
def location_update(
    payload: dict[str, Any] | None = None,
    user_id: str = DEFAULT_USER_ID,
    character_id: str = DEFAULT_CHARACTER_ID,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_seed(session, user_id=user_id, character_id=character_id)
    body = payload or {}
    try:
        latitude = float(body.get("latitude"))
        longitude = float(body.get("longitude"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="latitude and longitude are required") from exc
    location = update_user_location(
        session,
        user_id=str(body.get("user_id") or user_id),
        latitude=latitude,
        longitude=longitude,
        accuracy_m=float(body.get("accuracy_m") or 0),
        provider=str(body.get("provider") or "android"),
        captured_at=str(body.get("captured_at") or ""),
    )
    local_time = _parse_client_time(str(body.get("local_time") or ""))
    snapshot = read_weather_snapshot(session, user_id=location.user_id, local_time=local_time, allow_stale=True)
    return {
        "ok": True,
        "location": _location_to_out(location),
        "weather": weather_snapshot_to_dict(snapshot),
        "proactive_event_id": "",
    }


@app.get("/api/weather/current")
def weather_current(
    user_id: str = DEFAULT_USER_ID,
    refresh: bool = False,
    local_time: str = "",
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_seed(session, user_id=user_id, character_id=DEFAULT_CHARACTER_ID)
    snapshot = read_weather_snapshot(session, user_id=user_id, local_time=_parse_client_time(local_time), allow_stale=True)
    location = session.get(UserLocation, user_id)
    return {"ok": True, "location": _location_to_out(location), "weather": weather_snapshot_to_dict(snapshot)}


@app.post("/api/weather/refresh")
def weather_refresh(
    payload: dict[str, Any] | None = None,
    user_id: str = DEFAULT_USER_ID,
    character_id: str = DEFAULT_CHARACTER_ID,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_seed(session, user_id=user_id, character_id=character_id)
    body = payload or {}
    effective_user_id = str(body.get("user_id") or user_id)
    effective_character_id = str(body.get("character_id") or character_id)
    local_time = _parse_client_time(str(body.get("local_time") or ""))
    snapshot = refresh_weather_snapshot(session, user_id=effective_user_id, local_time=local_time, force=True)
    event = ensure_weather_candidate(session, user_id=effective_user_id, character_id=effective_character_id, local_time=local_time) if snapshot is not None else None
    location = session.get(UserLocation, effective_user_id)
    return {
        "ok": True,
        "location": _location_to_out(location),
        "weather": weather_snapshot_to_dict(snapshot),
        "proactive_event_id": event.proactive_event_id if event is not None else "",
    }


@app.get("/api/proactive/pending")
def proactive_pending(
    user_id: str = DEFAULT_USER_ID,
    character_id: str = DEFAULT_CHARACTER_ID,
    local_time: str = "",
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_seed(session, user_id=user_id, character_id=character_id)
    return pending_proactive_response(
        session,
        user_id=user_id,
        character_id=character_id,
        local_time=_parse_client_time(local_time),
    )


@app.post("/api/proactive/{event_id}/delivered")
def proactive_delivered(event_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    event = mark_proactive_delivered(session, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="proactive event not found")
    return {"ok": True, "event": {"proactive_event_id": event.proactive_event_id, "status": event.status}}


@app.post("/api/proactive/{event_id}/consume")
def proactive_consume(event_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    event = consume_proactive_event(session, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="proactive event not found")
    return {"ok": True, "event": {"proactive_event_id": event.proactive_event_id, "status": event.status}}


@app.post("/api/opening/prepare")
def opening_prepare(
    payload: dict[str, Any] | None = None,
    user_id: str = DEFAULT_USER_ID,
    character_id: str = DEFAULT_CHARACTER_ID,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_seed(session, user_id=user_id, character_id=character_id)
    body = payload or {}
    return prepare_opening(
        session,
        user_id=str(body.get("user_id") or user_id),
        character_id=str(body.get("character_id") or character_id),
        local_time=_parse_client_time(str(body.get("local_time") or "")),
        proactive_event_id=str(body.get("proactive_event_id") or ""),
        allow_llm=bool(body.get("allow_llm", True)),
    )


@app.get("/api/opening/ready")
def opening_ready(
    user_id: str = DEFAULT_USER_ID,
    character_id: str = DEFAULT_CHARACTER_ID,
    session_id: str = "android",
    local_time: str = "",
    proactive_event_id: str = "",
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_seed(session, user_id=user_id, character_id=character_id)
    result = consume_ready_opening(
        session,
        user_id=user_id,
        character_id=character_id,
        session_id=session_id,
        local_time=_parse_client_time(local_time),
        proactive_event_id=proactive_event_id,
    )
    write_diagnostic("event_ok", event_type="opening_ready", session_id=session_id, result_type=result.event_type)
    return result.model_dump()


@app.websocket("/ws/app")
async def app_ws(websocket: WebSocket, user_id: str = DEFAULT_USER_ID, device_id: str = "device") -> None:
    await websocket.accept()
    mark_online(user_id, device_id)
    logger.info("websocket connected user_id=%s device_id=%s", user_id, device_id)
    await websocket.send_json({"event_type": "connected", "event_id": uid("evt"), "payload": {"user_id": user_id, "device_id": device_id}})
    try:
        while True:
            payload = await websocket.receive_json()
            event = EventIn(**payload, user_id=payload.get("user_id") or user_id)
            with next(get_session()) as session:
                try:
                    result = handle_event(session, event)
                    await websocket.send_json(result.model_dump())
                except ProviderError as exc:
                    logger.warning(
                        "provider error during websocket event event_type=%s session_id=%s message=%s",
                        event.event_type,
                        event.session_id,
                        exc,
                    )
                    await websocket.send_json({"event_type": "error", "event_id": uid("evt"), "session_id": event.session_id, "payload": {"message": str(exc)}})
    except WebSocketDisconnect:
        logger.info("websocket disconnected user_id=%s device_id=%s", user_id, device_id)
        return
    finally:
        mark_offline(user_id, device_id)


@app.get("/api/state/home")
def home_state(user_id: str = DEFAULT_USER_ID, character_id: str = DEFAULT_CHARACTER_ID, session: Session = Depends(get_session)) -> dict[str, Any]:
    ensure_seed(session, user_id=user_id, character_id=character_id)
    ensure_schedule(session, user_id=user_id, character_id=character_id, day=datetime.now())
    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one()
    next_slot = session.execute(
        select(ScheduleSlot).where(ScheduleSlot.user_id == user_id, ScheduleSlot.actual_status == "pending").order_by(ScheduleSlot.start_at).limit(1)
    ).scalar_one_or_none()
    unread = session.execute(
        select(MomentInteraction).where(MomentInteraction.actor_id == user_id, MomentInteraction.reflected_in_chat == False)  # noqa: E712
    ).scalars().all()
    return {
        "character": {"name": "小樱", "pose": "happy" if relation.mood >= 0 else "sad"},
        "relation": {
            "affection": relation.affection,
            "trust": relation.trust,
            "dependency": relation.dependency,
            "mood": relation.mood,
            "stage": relation.relationship_stage,
        },
        "schedule": {
            "current_title": next_slot.activity_title if next_slot else "想和你聊天",
            "current_status": next_slot.actual_status if next_slot else "pending",
        },
        "unread_count": len(unread),
    }


@app.get("/api/moments")
def moments(session: Session = Depends(get_session)) -> dict[str, Any]:
    rows = session.execute(select(Moment).order_by(Moment.created_at.desc()).limit(50)).scalars().all()
    items: list[dict[str, Any]] = []
    for row in rows:
        interactions = session.execute(select(MomentInteraction).where(MomentInteraction.moment_id == row.moment_id)).scalars().all()
        items.append(
            {
                "moment_id": row.moment_id,
                "author_name": row.author_name,
                "text": row.text,
                "media_asset_id": row.media_asset_id,
                "media_url": f"/media/{row.media_asset_id}" if row.media_asset_id else "",
                "mood_snapshot": row.mood_snapshot,
                "created_at": row.created_at,
                "likes": len([item for item in interactions if item.interaction_type == "like"]),
                "like_actors": [
                    {
                        "actor_type": item.actor_type,
                        "actor_id": item.actor_id,
                        "actor_name": item.actor_name or item.actor_id,
                        "created_at": item.created_at,
                    }
                    for item in interactions
                    if item.interaction_type == "like"
                ],
                "comments": [
                    {
                        "actor_type": item.actor_type,
                        "actor_id": item.actor_id,
                        "actor_name": item.actor_name or item.actor_id,
                        "content": item.content,
                        "created_at": item.created_at,
                    }
                    for item in interactions
                    if item.interaction_type == "comment"
                ],
            }
        )
    return {"items": items}


@app.post("/api/moments/{moment_id}/like")
def like_moment(moment_id: str, user_id: str = DEFAULT_USER_ID, session: Session = Depends(get_session)) -> dict[str, Any]:
    if session.get(Moment, moment_id) is None:
        raise HTTPException(status_code=404, detail="moment not found")
    interaction = MomentInteraction(interaction_id=uid("mi"), moment_id=moment_id, actor_id=user_id, actor_name="你", interaction_type="like")
    session.add(interaction)
    session.add(Memory(memory_id=uid("mem"), user_id=user_id, character_id=DEFAULT_CHARACTER_ID, layer="temporary", content="用户点赞了小樱的朋友圈。", source_event_id=interaction.interaction_id, importance=0.6, confidence=0.9))
    create_moment_feedback_event(
        session,
        user_id=user_id,
        character_id=DEFAULT_CHARACTER_ID,
        interaction_id=interaction.interaction_id,
        interaction_type="like",
        moment_id=moment_id,
    )
    session.commit()
    return {"ok": True, "interaction_id": interaction.interaction_id}


@app.post("/api/moments/{moment_id}/comments")
def comment_moment(moment_id: str, payload: dict[str, Any], user_id: str = DEFAULT_USER_ID, session: Session = Depends(get_session)) -> dict[str, Any]:
    if session.get(Moment, moment_id) is None:
        raise HTTPException(status_code=404, detail="moment not found")
    content = str(payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content required")
    interaction = MomentInteraction(interaction_id=uid("mi"), moment_id=moment_id, actor_id=user_id, actor_name="你", interaction_type="comment", content=content)
    session.add(interaction)
    session.add(Memory(memory_id=uid("mem"), user_id=user_id, character_id=DEFAULT_CHARACTER_ID, layer="temporary", content=f"用户评论了小樱的朋友圈：{content}", source_event_id=interaction.interaction_id, importance=0.8, confidence=0.95))
    create_moment_feedback_event(
        session,
        user_id=user_id,
        character_id=DEFAULT_CHARACTER_ID,
        interaction_id=interaction.interaction_id,
        interaction_type="comment",
        moment_id=moment_id,
        content=content,
    )
    session.commit()
    return {"ok": True, "interaction_id": interaction.interaction_id}


@app.get("/api/calendar")
def calendar(month: str = "", user_id: str = DEFAULT_USER_ID, character_id: str = DEFAULT_CHARACTER_ID, session: Session = Depends(get_session)) -> dict[str, Any]:
    day = datetime.now()
    prefix = month or day.strftime("%Y-%m")
    items = calendar_items(session, user_id=user_id, character_id=character_id, month=prefix)
    notes = {item["date"]: day_note(session, day=item["date"]) for item in items}
    return {
        "month": prefix,
        "days": [
            {
                **item,
                "day_note": notes.get(item["date"], ""),
            }
            for item in items
        ],
    }


@app.get("/api/journal")
def journal(user_id: str = DEFAULT_USER_ID, session: Session = Depends(get_session)) -> dict[str, Any]:
    memories = session.execute(select(Memory).where(Memory.user_id == user_id, Memory.hidden == False).order_by(Memory.created_at.desc()).limit(80)).scalars().all()  # noqa: E712
    return {
        "memories": [
            {
                "memory_id": item.memory_id,
                "layer": item.layer,
                "content": item.content,
                "importance": item.importance,
                "confidence": item.confidence,
                "created_at": item.created_at,
            }
            for item in memories
        ]
    }


@app.get("/api/widget/state")
def widget_state(user_id: str = DEFAULT_USER_ID, session: Session = Depends(get_session)) -> dict[str, Any]:
    home = home_state(user_id=user_id, session=session)
    mood = home["relation"]["mood"]
    status = "开心" if mood >= 20 else "想聊天" if mood >= 0 else "有点低落"
    bubble = "今天也想听你说说话。" if status == "想聊天" else "刚刚有新的小事想告诉你。"
    return {"character_name": "小樱", "status": status, "bubble": bubble, "unread_count": home["unread_count"], "open_target": "home"}


@app.get("/media/{asset_id}")
def get_media(asset_id: str, session: Session = Depends(get_session)) -> FileResponse:
    asset = session.get(MediaAsset, asset_id)
    if asset is None or not asset.local_path:
        raise HTTPException(status_code=404, detail="asset not found")
    path = Path(asset.local_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="asset file missing")
    return FileResponse(path)


@app.post("/api/debug/run-daily-cycle")
def debug_daily(user_id: str = DEFAULT_USER_ID, character_id: str = DEFAULT_CHARACTER_ID, session: Session = Depends(get_session)) -> dict[str, Any]:
    result = run_daily_cycle(session, user_id=user_id, character_id=character_id, day=datetime.now())
    logger.info("debug daily cycle user_id=%s character_id=%s result=%s", user_id, character_id, result)
    return {"ok": True, **result}


@app.get("/api/debug/logs")
def debug_logs(limit: int = 200) -> dict[str, Any]:
    return {"ok": True, "items": tail_diagnostics(limit)}


@app.get("/api/admin/runtime-logs")
def admin_runtime_logs(
    limit: int = 200,
    feature: str = "",
    status: str = "",
    q: str = "",
    trace_id: str = "",
    before_ts: float | None = None,
    include_legacy: bool = True,
    include_cache_hits: bool = False,
) -> dict[str, Any]:
    return runtime_logs(
        limit=limit,
        feature=feature,
        status=status,
        q=q,
        trace_id=trace_id,
        before_ts=before_ts,
        include_legacy=include_legacy,
        include_cache_hits=include_cache_hits,
    )


@app.post("/api/debug/generate-proactive")
def debug_proactive(user_id: str = DEFAULT_USER_ID, session: Session = Depends(get_session)) -> dict[str, Any]:
    logger.info("debug proactive requested user_id=%s", user_id)
    ensure_seed(session, user_id=user_id, character_id=DEFAULT_CHARACTER_ID)
    pending = pending_proactive_response(session, user_id=user_id, character_id=DEFAULT_CHARACTER_ID, generate_news=True)
    prewarm = prepare_due_openings(session, user_id=user_id, character_id=DEFAULT_CHARACTER_ID, limit=1, generate_news=False)
    return {"ok": True, "pending": pending, "prewarm": prewarm}


@app.post("/api/debug/advance-time")
def debug_advance(payload: dict[str, Any]) -> dict[str, Any]:
    logger.info("debug advance-time payload=%s", payload)
    return {"ok": True, "message": "Demo uses client-provided local_time for interruption checks.", "received": payload}
