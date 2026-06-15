from __future__ import annotations

import ipaddress
import logging
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .calendar_events import calendar_items, create_calendar_event, day_note, ensure_calendar_events, ensure_calendar_proactive_candidates, update_calendar_event
from .database import get_session, init_db
from .diagnostics import runtime_logs, tail_diagnostics, write_diagnostic
from .image_generation import (
    MANUAL_IMAGE_COOLDOWN_SECONDS,
    ImageRequestBlocked,
    generate_safe_image,
    normalize_image_kind,
)
from .logging_setup import maybe_start_debugger, setup_logging
from .models import (
    Character,
    CalendarEvent,
    DeviceRegistration,
    MediaAsset,
    Memory,
    Moment,
    MomentInteraction,
    Message,
    OpeningCache,
    ProviderConfig,
    ProactiveDeliveryAttempt,
    ProactiveEvent,
    RelationState,
    ScheduleSlot,
    TtsVoiceProfile,
    User,
    UserCommitment,
    UserLocation,
)
from .opening import consume_ready_opening, prepare_due_openings, prepare_opening
from .live2d_config import (
    DEFAULT_LIVE2D_APPEARANCE_ID,
    LIVE2D_MODELS_DIR,
    area_to_dict,
    create_hit_area,
    delete_hit_area,
    ensure_default_hit_areas,
    list_hit_areas,
    live2d_bootstrap_payload,
    preview_config_for_character,
    reference_image_path,
    reorder_hit_areas,
    update_hit_area,
)
from .touch_reactions import (
    consume_touch_reaction,
    list_touch_pool_admin,
    refresh_touch_reaction_pools,
    refresh_touch_reaction_pools_background,
    touch_pool_coverage_admin,
    touch_pool_version,
    touch_reaction_bundle,
)
from .online import mark_heartbeat, mark_offline, mark_online, presence_context
from .persona import normalize_memory_layer, normalize_persona_card, relation_attitude
from .pipeline import handle_event
from .proactive import consume_proactive_event, create_moment_feedback_event, create_proactive_event, ensure_news_candidate, mark_proactive_delivered, mark_proactive_dismissed, pending_proactive_response
from .push import record_delivery_attempt, register_device
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
from .vector_memory import delete_memory_vector, safe_index_memory_vector
from .weather import ensure_weather_candidate, read_weather_snapshot, refresh_weather_snapshot, update_user_location, weather_snapshot_to_dict


setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Galgame Android Demo Backend")
ADMIN_DIR = Path(__file__).resolve().parent / "admin_static"

if LIVE2D_MODELS_DIR.is_dir():
    app.mount(
        "/admin/assets/live2d-models",
        StaticFiles(directory=str(LIVE2D_MODELS_DIR)),
        name="admin_live2d_models",
    )

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
        for kind in ("llm", "llm_task", "embedding", "tts", "search", "weather", "image")
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
    persona_card = normalize_persona_card(load_json(character.persona_card_json, {}), name=character.name)
    return CharacterAdminOut(
        character_id=character.character_id,
        name=character.name,
        age_setting=character.age_setting,
        persona_card=persona_card,
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
        "profile": load_json(user.profile_json, {}),
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


def _relation_to_out(relation: RelationState, character: Character | None = None) -> dict[str, Any]:
    persona_card = normalize_persona_card(load_json(character.persona_card_json, {}), name=character.name) if character is not None else {}
    attitude_band, attitude_text = relation_attitude(relation, persona_card)
    return {
        "id": relation.id,
        "user_id": relation.user_id,
        "character_id": relation.character_id,
        "affection": relation.affection,
        "trust": relation.trust,
        "dependency": relation.dependency,
        "mood": relation.mood,
        "relationship_stage": relation.relationship_stage,
        "attitude_band": attitude_band,
        "attitude_text": attitude_text,
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
        "tags": load_json(memory.tags_json, []),
        "metadata": load_json(memory.metadata_json, {}),
        "importance": memory.importance,
        "confidence": memory.confidence,
        "vector_status": memory.vector_status,
        "vector_updated_at": memory.vector_updated_at,
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


def _schedule_slot_to_out(slot: ScheduleSlot) -> dict[str, Any]:
    return {
        "slot_id": slot.slot_id,
        "schedule_date": slot.schedule_date,
        "user_id": slot.user_id,
        "character_id": slot.character_id,
        "start_at": slot.start_at,
        "end_at": slot.end_at,
        "activity_title": slot.activity_title,
        "activity_type": slot.activity_type,
        "location": slot.location,
        "planned_status": slot.planned_status,
        "actual_status": slot.actual_status,
        "interrupted_by_session_id": slot.interrupted_by_session_id,
        "salience": slot.salience,
        "can_generate_moment": slot.can_generate_moment,
        "can_generate_photo": slot.can_generate_photo,
    }


def _device_registration_to_out(item: DeviceRegistration) -> dict[str, Any]:
    return {
        "device_id": item.device_id,
        "user_id": item.user_id,
        "platform": item.platform,
        "has_push_token": bool(item.push_token),
        "app_version": item.app_version,
        "locale": item.locale,
        "timezone": item.timezone,
        "notifications_enabled": item.notifications_enabled,
        "last_seen_at": item.last_seen_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _delivery_attempt_to_out(item: ProactiveDeliveryAttempt) -> dict[str, Any]:
    return {
        "attempt_id": item.attempt_id,
        "proactive_event_id": item.proactive_event_id,
        "user_id": item.user_id,
        "character_id": item.character_id,
        "channel": item.channel,
        "target_device_id": item.target_device_id,
        "status": item.status,
        "status_code": item.status_code,
        "error_message": item.error_message,
        "request": load_json(item.request_json, {}),
        "response": load_json(item.response_json, {}),
        "created_at": item.created_at,
    }


def _commitment_to_out(item: UserCommitment) -> dict[str, Any]:
    return {
        "commitment_id": item.commitment_id,
        "user_id": item.user_id,
        "character_id": item.character_id,
        "title": item.title,
        "description": item.description,
        "event_at": item.event_at,
        "remind_at": item.remind_at,
        "timezone": item.timezone,
        "source_message_id": item.source_message_id,
        "status": item.status,
        "dedupe_key": item.dedupe_key,
        "payload": load_json(item.payload_json, {}),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
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
        user.proactive_daily_limit = str(payload.get("proactive_daily_limit") or "unlimited").strip() or "unlimited"
    for field in ("notifications_enabled", "widget_bubbles_enabled", "news_enabled", "tts_enabled", "story_completed"):
        if field in payload:
            setattr(user, field, bool(payload.get(field)))
    if "interest_topics" in payload:
        topics = [str(item).strip() for item in payload.get("interest_topics") or [] if str(item).strip()]
        user.interest_topics_json = dump_json(topics[-20:])
    if "profile" in payload:
        profile = payload.get("profile")
        user.profile_json = dump_json(profile if isinstance(profile, dict) else {})
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
    character_ids = {item.character_id for item in relations}
    characters = {
        item.character_id: item
        for item in session.execute(select(Character).where(Character.character_id.in_(character_ids))).scalars().all()
    } if character_ids else {}
    return {
        **_page_response([_user_to_out(item) for item in users], total, page, page_size),
        "relations": [_relation_to_out(item, characters.get(item.character_id)) for item in relations],
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
    return _relation_to_out(relation, session.get(Character, character_id))


@app.get("/api/admin/users/{user_id}/memories")
def admin_memories(
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    q: str = "",
    character_id: str = "",
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(Memory).where(Memory.user_id == user_id)
    if character_id:
        stmt = stmt.where(Memory.character_id == character_id)
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
        layer=normalize_memory_layer(str(payload.get("layer") or "chat")),
        content=content,
        source_event_id=str(payload.get("source_event_id") or "admin"),
        tags_json=dump_json([str(item).strip() for item in (payload.get("tags") or []) if str(item).strip()]),
        metadata_json=dump_json(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
        importance=float(payload.get("importance") or 0.5),
        confidence=float(payload.get("confidence") or 0.8),
        hidden=bool(payload.get("hidden", False)),
    )
    session.add(memory)
    session.flush()
    safe_index_memory_vector(session, memory)
    session.commit()
    return _memory_to_out(memory)


@app.put("/api/admin/memories/{memory_id}")
def admin_update_memory(memory_id: str, payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    memory = session.get(Memory, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="memory not found")
    for field in ("layer", "content", "source_event_id"):
        if field in payload:
            value = str(payload.get(field) or "").strip()
            setattr(memory, field, normalize_memory_layer(value) if field == "layer" else value)
    if "tags" in payload:
        memory.tags_json = dump_json([str(item).strip() for item in (payload.get("tags") or []) if str(item).strip()])
    if "metadata" in payload:
        metadata = payload.get("metadata")
        memory.metadata_json = dump_json(metadata if isinstance(metadata, dict) else {})
    if "importance" in payload:
        memory.importance = float(payload.get("importance") or 0)
    if "confidence" in payload:
        memory.confidence = float(payload.get("confidence") or 0)
    if "hidden" in payload:
        memory.hidden = bool(payload.get("hidden"))
    safe_index_memory_vector(session, memory)
    session.commit()
    return _memory_to_out(memory)


@app.delete("/api/admin/memories/{memory_id}")
def admin_delete_memory(memory_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    memory = session.get(Memory, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="memory not found")
    delete_memory_vector(memory.memory_id)
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
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
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


@app.get("/api/admin/device-registrations")
def admin_device_registrations(user_id: str = "", page: int = 1, page_size: int = 20, session: Session = Depends(get_session)) -> dict[str, Any]:
    stmt = select(DeviceRegistration).order_by(DeviceRegistration.updated_at.desc())
    if user_id:
        stmt = stmt.where(DeviceRegistration.user_id == user_id)
    rows, total, page, page_size = _paginate_scalars(session, stmt, page=page, page_size=page_size)
    return _page_response([_device_registration_to_out(item) for item in rows], total, page, page_size)


@app.get("/api/admin/proactive-delivery-attempts")
def admin_delivery_attempts(
    user_id: str = "",
    proactive_event_id: str = "",
    page: int = 1,
    page_size: int = 20,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(ProactiveDeliveryAttempt).order_by(ProactiveDeliveryAttempt.created_at.desc())
    if user_id:
        stmt = stmt.where(ProactiveDeliveryAttempt.user_id == user_id)
    if proactive_event_id:
        stmt = stmt.where(ProactiveDeliveryAttempt.proactive_event_id == proactive_event_id)
    rows, total, page, page_size = _paginate_scalars(session, stmt, page=page, page_size=page_size)
    return _page_response([_delivery_attempt_to_out(item) for item in rows], total, page, page_size)


@app.get("/api/admin/user-commitments")
def admin_user_commitments(user_id: str = "", page: int = 1, page_size: int = 20, session: Session = Depends(get_session)) -> dict[str, Any]:
    stmt = select(UserCommitment).order_by(UserCommitment.remind_at.desc())
    if user_id:
        stmt = stmt.where(UserCommitment.user_id == user_id)
    rows, total, page, page_size = _paginate_scalars(session, stmt, page=page, page_size=page_size)
    return _page_response([_commitment_to_out(item) for item in rows], total, page, page_size)


@app.post("/api/admin/proactive-events/prewarm")
def admin_prewarm_proactive(payload: dict[str, Any] | None = None, session: Session = Depends(get_session)) -> dict[str, Any]:
    body = payload or {}
    user_id = str(body.get("user_id") or "")
    character_id = str(body.get("character_id") or DEFAULT_CHARACTER_ID)
    character_id = ensure_seed(session, user_id=user_id or DEFAULT_USER_ID, character_id=character_id)
    return prepare_due_openings(
        session,
        user_id=user_id,
        character_id=character_id,
        limit=int(body.get("limit") or 8),
        generate_news=bool(body.get("generate_news", False)),
    )


@app.get("/api/admin/ai-schedule/today")
def admin_ai_schedule_today(
    user_id: str = DEFAULT_USER_ID,
    character_id: str = DEFAULT_CHARACTER_ID,
    local_time: str = "",
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
    day = _parse_client_time(local_time) or datetime.now()
    slots = ensure_schedule(session, user_id=user_id, character_id=character_id, day=day)
    return {"ok": True, "items": [_schedule_slot_to_out(item) for item in sorted(slots, key=lambda slot: slot.start_at)]}


def _payload_bool(payload: dict[str, Any], key: str, default: bool = False) -> bool:
    value = payload.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _payload_int(payload: dict[str, Any], key: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(float(payload.get(key, default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _admin_due_time(payload: dict[str, Any], local_time: datetime | None) -> datetime:
    scheduled_at = _parse_client_time(str(payload.get("scheduled_at") or ""))
    if scheduled_at is not None:
        return scheduled_at
    now = local_time or datetime.now(timezone.utc)
    if _payload_bool(payload, "due_now", True):
        return now
    return now + timedelta(minutes=15)


@app.post("/api/admin/proactive-events/generate")
def admin_generate_proactive_source(payload: dict[str, Any] | None = None, session: Session = Depends(get_session)) -> dict[str, Any]:
    body = payload or {}
    user_id = str(body.get("user_id") or DEFAULT_USER_ID)
    character_id = str(body.get("character_id") or DEFAULT_CHARACTER_ID)
    source_type = str(body.get("source_type") or "memory").strip()
    if source_type not in {"news", "weather", "schedule", "calendar_event", "moment_interaction", "appointment", "memory"}:
        raise HTTPException(status_code=400, detail="unsupported proactive source_type")
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
    local_time = _parse_client_time(str(body.get("local_time") or ""))
    title = str(body.get("title") or "").strip()
    text = str(body.get("text") or "").strip()
    priority = _payload_int(body, "priority", 75, minimum=0, maximum=100)
    generated_by = "manual"
    events: list[ProactiveEvent] = []

    if source_type == "news" and not _payload_bool(body, "manual_only", False):
        event = ensure_news_candidate(session, user_id=user_id, character_id=character_id, local_time=local_time)
        if event is not None:
            generated_by = "news_provider"
            events.append(event)
    elif source_type == "weather" and not _payload_bool(body, "manual_only", False):
        snapshot = refresh_weather_snapshot(session, user_id=user_id, local_time=local_time, force=_payload_bool(body, "force_refresh", True))
        event = ensure_weather_candidate(session, user_id=user_id, character_id=character_id, local_time=local_time) if snapshot is not None else None
        if event is not None:
            generated_by = "weather_provider"
            events.append(event)
    elif source_type == "calendar_event" and not _payload_bool(body, "manual_only", False):
        generated = ensure_calendar_proactive_candidates(session, user_id=user_id, character_id=character_id, local_time=local_time)
        if generated:
            generated_by = "calendar_event_provider"
            events.extend(generated)
    elif source_type == "schedule" and not _payload_bool(body, "manual_only", False):
        slots = ensure_schedule(session, user_id=user_id, character_id=character_id, day=local_time or datetime.now())
        slot_id = str(body.get("slot_id") or "")
        slot = next((item for item in slots if item.slot_id == slot_id), None)
        slot = slot or next((item for item in slots if item.actual_status == "pending" and item.salience >= 60), None) or (slots[0] if slots else None)
        if slot is not None:
            event = create_proactive_event(
                session,
                user_id=user_id,
                character_id=character_id,
                source_type="schedule",
                source_id=slot.slot_id,
                title=title or "亚托莉有一件日常想告诉你",
                text=text or f"亚托莉今天在{slot.location}安排了「{slot.activity_title}」，想找个合适的时候告诉你。",
                priority=priority,
                scheduled_at=_admin_due_time(body, local_time),
                payload={"activity_title": slot.activity_title, "location": slot.location, "activity_type": slot.activity_type, "admin_generated": True},
            )
            if event is not None:
                generated_by = "schedule_slot"
                events.append(event)

    if not events:
        if source_type == "moment_interaction":
            event = create_moment_feedback_event(
                session,
                user_id=user_id,
                character_id=character_id,
                interaction_id=uid("admin_interaction"),
                interaction_type=str(body.get("interaction_type") or "comment"),
                moment_id=str(body.get("moment_id") or "admin_moment"),
                content=text or "后台测试：用户刚刚在朋友圈有互动。",
            )
        else:
            fallback_text = text or {
                "news": "后台测试：有一条用户可能感兴趣的新闻，适合主动开口。",
                "weather": "后台测试：天气有变化，适合提醒用户。",
                "schedule": "后台测试：今天的 AI 日程里有一件事想告诉用户。",
                "calendar_event": "后台测试：最近有一个日历事件适合问问用户安排。",
                "appointment": "后台测试：用户之前提到的约定快到了，需要温柔提醒。",
                "memory": "后台测试：亚托莉想起了一件和用户有关的小事。",
            }[source_type]
            event = create_proactive_event(
                session,
                user_id=user_id,
                character_id=character_id,
                source_type=source_type,
                source_id=str(body.get("source_id") or f"admin:{uid('src')}"),
                title=title or "后台测试主动消息",
                text=fallback_text,
                priority=priority,
                scheduled_at=_admin_due_time(body, local_time),
                payload={"admin_generated": True, "source_type": source_type},
            )
        if event is not None:
            events.append(event)

    session.commit()
    return {"ok": True, "source_type": source_type, "generated_by": generated_by, "items": [_proactive_event_to_out(item) for item in events]}


@app.post("/api/admin/proactive-events/judge")
def admin_judge_proactive_now(payload: dict[str, Any] | None = None, session: Session = Depends(get_session)) -> dict[str, Any]:
    body = payload or {}
    user_id = str(body.get("user_id") or DEFAULT_USER_ID)
    character_id = str(body.get("character_id") or DEFAULT_CHARACTER_ID)
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
    local_time = _parse_client_time(str(body.get("local_time") or ""))
    foreground_context = {
        "app_state": str(body.get("app_state") or "admin"),
        "screen": str(body.get("screen") or "admin"),
        "idle_seconds": _payload_int(body, "idle_seconds", 120, minimum=0, maximum=86400),
        "input_active": _payload_bool(body, "input_active", False),
    }
    result = pending_proactive_response(
        session,
        user_id=user_id,
        character_id=character_id,
        local_time=local_time,
        generate_news=_payload_bool(body, "generate_news", False),
        generate_weather=_payload_bool(body, "generate_weather", False),
        delivery_channel=str(body.get("delivery_channel") or "admin"),
        foreground_context=foreground_context,
        appointment_only=_payload_bool(body, "appointment_only", False),
    )
    prepare_result: dict[str, Any] | None = None
    event_payload = result.get("event") or {}
    event_id = str(event_payload.get("proactive_event_id") or "")
    if event_id and _payload_bool(body, "prepare", False):
        prepare_result = prepare_opening(session, user_id=user_id, character_id=character_id, local_time=local_time, proactive_event_id=event_id)
    user = session.get(User, user_id)
    return {
        "ok": True,
        "pending": result,
        "prepared": prepare_result,
        "judgement": load_json(user.proactive_judgement_json, {}) if user is not None else {},
        "next_check_at": user.proactive_next_check_at if user is not None else "",
    }


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
    ensure_seed(session)
    character = session.get(Character, character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="character not found")
    if payload.name is not None:
        character.name = payload.name.strip() or character.name
    if payload.persona_card is not None:
        character.persona_card_json = dump_json(normalize_persona_card(payload.persona_card, name=character.name))
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
    appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
    user = session.get(User, user_id)
    character = session.get(Character, character_id)
    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one()
    character = session.get(Character, character_id)
    display_name = character.name if character and character.name else character_id
    persona_card = normalize_persona_card(load_json(character.persona_card_json if character else "{}", {}), name=display_name)
    attitude_band, attitude_text = relation_attitude(relation, persona_card)
    live2d = live2d_bootstrap_payload(session, appearance_id=appearance_id)
    live2d["touch_pool_version"] = touch_pool_version(
        session,
        user_id=user_id,
        character_id=character_id,
        appearance_id=appearance_id,
    )
    return {
        "user": {
            "user_id": user_id,
            "story_completed": bool(user and user.story_completed),
            "interest_topics": load_json(user.interest_topics_json if user else "[]", []),
        },
        "character": {
            "character_id": character_id,
            "name": display_name,
            "age_setting": character.age_setting if character else "18+",
        },
        "relation": {
            "affection": relation.affection,
            "trust": relation.trust,
            "dependency": relation.dependency,
            "mood": relation.mood,
            "stage": relation.relationship_stage,
            "attitude_band": attitude_band,
            "attitude_text": attitude_text,
        },
        "live2d": live2d,
        "touch_reactions_ready": True,
    }


@app.get("/api/live2d/config")
def live2d_config(
    user_id: str = DEFAULT_USER_ID,
    character_id: str = DEFAULT_CHARACTER_ID,
    appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
    live2d = live2d_bootstrap_payload(session, appearance_id=appearance_id)
    live2d["touch_pool_version"] = touch_pool_version(
        session,
        user_id=user_id,
        character_id=character_id,
        appearance_id=appearance_id,
    )
    return {"ok": True, **live2d}


@app.get("/api/live2d/touch/bundle")
def live2d_touch_bundle(
    user_id: str = DEFAULT_USER_ID,
    character_id: str = DEFAULT_CHARACTER_ID,
    appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
    ensure_default_hit_areas(session, appearance_id=appearance_id)
    session.commit()
    try:
        return touch_reaction_bundle(
            session,
            user_id=user_id,
            character_id=character_id,
            appearance_id=appearance_id,
        )
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/live2d/touch")
def live2d_touch(payload: dict[str, Any] | None = None, session: Session = Depends(get_session)) -> dict[str, Any]:
    body = payload or {}
    user_id = str(body.get("user_id") or DEFAULT_USER_ID)
    character_id = str(body.get("character_id") or DEFAULT_CHARACTER_ID)
    appearance_id = str(body.get("appearance_id") or body.get("live2d_appearance_id") or DEFAULT_LIVE2D_APPEARANCE_ID)
    hit_area = str(body.get("hit_area") or "")
    ensure_seed(session, user_id=user_id, character_id=character_id)
    try:
        return consume_touch_reaction(
            session,
            user_id=user_id,
            character_id=character_id,
            appearance_id=appearance_id,
            hit_area=hit_area,
        )
    except ProviderError as exc:
        message = str(exc)
        if message == "touch_pool_missing":
            raise HTTPException(
                status_code=404,
                detail={"ok": False, "pool_missing": True, "message": "touch reaction pool not prefetched"},
            ) from exc
        raise HTTPException(status_code=503, detail=message) from exc


@app.post("/api/live2d/touch/refresh")
def live2d_touch_refresh(
    background_tasks: BackgroundTasks,
    payload: dict[str, Any] | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    body = payload or {}
    user_id = str(body.get("user_id") or DEFAULT_USER_ID)
    character_id = str(body.get("character_id") or DEFAULT_CHARACTER_ID)
    appearance_id = str(body.get("appearance_id") or body.get("live2d_appearance_id") or DEFAULT_LIVE2D_APPEARANCE_ID)
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
    hit_area = str(body.get("hit_area") or "")
    force = bool(body.get("force"))
    tts_only = bool(body.get("tts_only"))
    if body.get("sync"):
        try:
            return refresh_touch_reaction_pools(
                session,
                user_id=user_id,
                character_id=character_id,
                appearance_id=appearance_id,
                hit_area=hit_area,
                tier=str(body.get("tier") or ""),
                tiers=body.get("tiers") if isinstance(body.get("tiers"), list) else None,
                force=force,
                tts_only=tts_only,
            )
        except ProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    background_tasks.add_task(refresh_touch_reaction_pools_background, user_id=user_id, character_id=character_id)
    return {
        "ok": True,
        "queued": True,
        "user_id": user_id,
        "character_id": character_id,
        "appearance_id": appearance_id,
    }


@app.get("/api/admin/live2d/hit-areas")
def admin_list_live2d_hit_areas(
    appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID,
    character_id: str = "",
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    resolved = appearance_id or character_id or DEFAULT_LIVE2D_APPEARANCE_ID
    ensure_default_hit_areas(session, appearance_id=resolved)
    session.commit()
    rows = list_hit_areas(session, appearance_id=resolved)
    return {"ok": True, "items": [area_to_dict(row) for row in rows], "appearance_id": resolved, "character_id": resolved}


@app.get("/api/admin/live2d/preview-config")
def admin_live2d_preview_config(
    appearance_id: str = "",
    character_id: str = "",
) -> dict[str, Any]:
    from .live2d_config import _resolve_appearance_id

    resolved = _resolve_appearance_id(appearance_id=appearance_id, character_id=character_id)
    return {"ok": True, **preview_config_for_character(resolved)}


@app.get("/api/admin/live2d/hit-areas/reference-image")
def admin_live2d_reference_image(
    appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID,
    character_id: str = "",
) -> FileResponse:
    resolved = appearance_id or character_id or DEFAULT_LIVE2D_APPEARANCE_ID
    path = reference_image_path(resolved)
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="reference image not found")
    return FileResponse(path, media_type="image/png")


@app.post("/api/admin/live2d/hit-areas")
def admin_create_live2d_hit_area(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        row = create_hit_area(session, payload)
        return {"ok": True, "item": area_to_dict(row)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/admin/live2d/hit-areas/{area_id}")
def admin_update_live2d_hit_area(
    area_id: str,
    payload: dict[str, Any],
    appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID,
    character_id: str = "",
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    resolved = appearance_id or character_id or DEFAULT_LIVE2D_APPEARANCE_ID
    try:
        row = update_hit_area(session, appearance_id=resolved, area_id=area_id, payload=payload)
        return {"ok": True, "item": area_to_dict(row)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/admin/live2d/hit-areas/{area_id}")
def admin_delete_live2d_hit_area(
    area_id: str,
    appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID,
    character_id: str = "",
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    resolved = appearance_id or character_id or DEFAULT_LIVE2D_APPEARANCE_ID
    try:
        delete_hit_area(session, appearance_id=resolved, area_id=area_id)
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/admin/live2d/hit-areas/reorder")
def admin_reorder_live2d_hit_areas(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    appearance_id = str(payload.get("appearance_id") or payload.get("character_id") or DEFAULT_LIVE2D_APPEARANCE_ID)
    ordered = payload.get("ordered_area_ids") or []
    if not isinstance(ordered, list):
        raise HTTPException(status_code=400, detail="ordered_area_ids must be a list")
    rows = reorder_hit_areas(session, appearance_id=appearance_id, ordered_area_ids=[str(item) for item in ordered])
    return {"ok": True, "items": [area_to_dict(row) for row in rows]}


@app.get("/api/admin/live2d/touch-pools/coverage")
def admin_touch_pool_coverage(
    character_id: str = DEFAULT_CHARACTER_ID,
    appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID,
    user_id: str = DEFAULT_USER_ID,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_seed(session, user_id=user_id, character_id=character_id)
    ensure_default_hit_areas(session, appearance_id=appearance_id)
    session.commit()
    try:
        return touch_pool_coverage_admin(
            session,
            user_id=user_id,
            character_id=character_id,
            appearance_id=appearance_id,
        )
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/admin/live2d/touch-pools")
def admin_list_touch_pools(
    hit_area: str,
    character_id: str = DEFAULT_CHARACTER_ID,
    appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID,
    user_id: str = DEFAULT_USER_ID,
    tier: str = "",
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
    ensure_default_hit_areas(session, appearance_id=appearance_id)
    session.commit()
    try:
        return list_touch_pool_admin(
            session,
            user_id=user_id,
            character_id=character_id,
            appearance_id=appearance_id,
            hit_area=hit_area,
            tier=tier,
        )
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/admin/live2d/touch-pools/refresh")
def admin_refresh_touch_pool(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    user_id = str(payload.get("user_id") or DEFAULT_USER_ID)
    character_id = str(payload.get("character_id") or DEFAULT_CHARACTER_ID)
    appearance_id = str(payload.get("appearance_id") or payload.get("live2d_appearance_id") or DEFAULT_LIVE2D_APPEARANCE_ID)
    hit_area = str(payload.get("hit_area") or "")
    force = bool(payload.get("force"))
    tts_only = bool(payload.get("tts_only"))
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
    try:
        return refresh_touch_reaction_pools(
            session,
            user_id=user_id,
            character_id=character_id,
            appearance_id=appearance_id,
            hit_area=hit_area,
            tier=str(payload.get("tier") or ""),
            tiers=payload.get("tiers") if isinstance(payload.get("tiers"), list) else None,
            force=force,
            tts_only=tts_only,
        )
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
        character_id = ensure_seed(session, user_id=event.user_id, character_id=event.character_id)
        event = event.model_copy(update={"character_id": character_id})
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
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
    body = payload or {}
    effective_user_id = str(body.get("user_id") or user_id)
    effective_character_id = ensure_seed(session, user_id=effective_user_id, character_id=str(body.get("character_id") or character_id))
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
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
    return pending_proactive_response(
        session,
        user_id=user_id,
        character_id=character_id,
        local_time=_parse_client_time(local_time),
    )


@app.post("/api/devices/register")
def devices_register(payload: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    user_id = str(payload.get("user_id") or DEFAULT_USER_ID)
    character_id = ensure_seed(session, user_id=user_id, character_id=str(payload.get("character_id") or DEFAULT_CHARACTER_ID))
    try:
        registration = register_device(session, {**payload, "user_id": user_id, "character_id": character_id})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "device": _device_registration_to_out(registration)}


@app.post("/api/presence/heartbeat")
def presence_heartbeat(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = payload or {}
    user_id = str(body.get("user_id") or DEFAULT_USER_ID)
    device_id = str(body.get("device_id") or "android")
    context = {
        "app_state": str(body.get("app_state") or "foreground"),
        "screen": str(body.get("screen") or ""),
        "idle_seconds": int(body.get("idle_seconds") or 0),
        "input_active": bool(body.get("input_active", False)),
        "local_time": str(body.get("local_time") or ""),
    }
    return {"ok": True, "presence": mark_heartbeat(user_id, device_id, context)}


@app.post("/api/proactive/foreground-check")
def proactive_foreground_check(payload: dict[str, Any] | None = None, session: Session = Depends(get_session)) -> dict[str, Any]:
    body = payload or {}
    user_id = str(body.get("user_id") or DEFAULT_USER_ID)
    character_id = str(body.get("character_id") or DEFAULT_CHARACTER_ID)
    device_id = str(body.get("device_id") or "android")
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
    heartbeat = {
        "app_state": "foreground",
        "screen": str(body.get("screen") or ""),
        "idle_seconds": int(body.get("idle_seconds") or 0),
        "input_active": bool(body.get("input_active", False)),
        "local_time": str(body.get("local_time") or ""),
        "dialogue_state": str(body.get("dialogue_state") or ""),
    }
    mark_heartbeat(user_id, device_id, heartbeat)
    local_time = _parse_client_time(str(body.get("local_time") or ""))
    pending = pending_proactive_response(
        session,
        user_id=user_id,
        character_id=character_id,
        local_time=local_time,
        delivery_channel="foreground",
        foreground_context=presence_context(user_id),
    )
    event_payload = pending.get("event") or {}
    event_id = str(event_payload.get("proactive_event_id") or "")
    if not event_id:
        return {"event_type": "no_reply", "event_id": uid("evt"), "session_id": str(body.get("session_id") or "android"), "payload": {"pace_reason": "no foreground proactive event"}}
    prepared = prepare_opening(
        session,
        user_id=user_id,
        character_id=character_id,
        local_time=local_time,
        proactive_event_id=event_id,
        allow_llm=True,
    )
    if not prepared.get("prepared"):
        return {"event_type": "no_reply", "event_id": uid("evt"), "session_id": str(body.get("session_id") or "android"), "payload": {"pace_reason": str(prepared.get("reason") or "not prepared")}}
    event = session.get(ProactiveEvent, event_id)
    if event is not None:
        record_delivery_attempt(session, event=event, channel="foreground", target_device_id=device_id, status="sent")
    result = consume_ready_opening(
        session,
        user_id=user_id,
        character_id=character_id,
        session_id=str(body.get("session_id") or "android"),
        local_time=local_time,
        proactive_event_id=event_id,
    )
    output = result.model_dump()
    if isinstance(output.get("payload"), dict):
        output["payload"]["proactive_event_id"] = event_id
    if output.get("event_type") == "dialogue":
        consume_proactive_event(session, event_id)
    return output


@app.post("/api/proactive/{event_id}/delivered")
def proactive_delivered(event_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    event = mark_proactive_delivered(session, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="proactive event not found")
    return {"ok": True, "event": {"proactive_event_id": event.proactive_event_id, "status": event.status}}


@app.post("/api/proactive/{event_id}/dismiss")
def proactive_dismiss(event_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    event = mark_proactive_dismissed(session, event_id)
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
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
    body = payload or {}
    body_character_id = ensure_seed(session, user_id=str(body.get("user_id") or user_id), character_id=str(body.get("character_id") or character_id))
    return prepare_opening(
        session,
        user_id=str(body.get("user_id") or user_id),
        character_id=body_character_id,
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
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
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
                    character_id = ensure_seed(session, user_id=event.user_id, character_id=event.character_id)
                    event = event.model_copy(update={"character_id": character_id})
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
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
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
    character = session.get(Character, character_id)
    display_name = character.name if character and character.name else character_id
    persona_card = normalize_persona_card(load_json(character.persona_card_json if character else "{}", {}), name=display_name)
    attitude_band, attitude_text = relation_attitude(relation, persona_card)
    return {
        "character": {"name": display_name, "pose": "happy" if relation.mood >= 0 else "sad"},
        "relation": {
            "affection": relation.affection,
            "trust": relation.trust,
            "dependency": relation.dependency,
            "mood": relation.mood,
            "stage": relation.relationship_stage,
            "attitude_band": attitude_band,
            "attitude_text": attitude_text,
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


@app.post("/api/images/generate")
def generate_image(
    payload: dict[str, Any],
    user_id: str = DEFAULT_USER_ID,
    character_id: str = DEFAULT_CHARACTER_ID,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
    image_config = get_enabled_provider(session, "image")
    if image_config is None:
        raise HTTPException(status_code=400, detail="image provider is not configured")
    try:
        kind = normalize_image_kind(str(payload.get("kind") or payload.get("image_kind") or ""))
    except ImageRequestBlocked as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    scene_hint = str(payload.get("scene_hint") or payload.get("prompt") or "").strip()
    if not scene_hint:
        raise HTTPException(status_code=400, detail="scene_hint is required")
    character = session.get(Character, character_id)
    source_id = str(payload.get("source_id") or uid("manual_image"))
    try:
        asset = generate_safe_image(
            session,
            config=image_config,
            kind=kind,
            scene_hint=scene_hint,
            character=character,
            user_id=user_id,
            character_id=character_id,
            source_id=source_id,
            mood=str(payload.get("mood") or ""),
            cooldown_seconds=MANUAL_IMAGE_COOLDOWN_SECONDS,
        )
    except ImageRequestBlocked as exc:
        status_code = 429 if "cooling down" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    session.commit()
    return {
        "ok": True,
        "kind": kind,
        "asset_id": asset.asset_id,
        "url": asset.url,
        "asset_type": asset.asset_type,
    }


@app.post("/api/moments/{moment_id}/like")
def like_moment(moment_id: str, user_id: str = DEFAULT_USER_ID, session: Session = Depends(get_session)) -> dict[str, Any]:
    if session.get(Moment, moment_id) is None:
        raise HTTPException(status_code=404, detail="moment not found")
    interaction = MomentInteraction(interaction_id=uid("mi"), moment_id=moment_id, actor_id=user_id, actor_name="你", interaction_type="like")
    session.add(interaction)
    session.add(Memory(memory_id=uid("mem"), user_id=user_id, character_id=DEFAULT_CHARACTER_ID, layer="temporary", content="用户点赞了亚托莉的朋友圈。", source_event_id=interaction.interaction_id, importance=0.6, confidence=0.9))
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
    session.add(Memory(memory_id=uid("mem"), user_id=user_id, character_id=DEFAULT_CHARACTER_ID, layer="temporary", content=f"用户评论了亚托莉的朋友圈：{content}", source_event_id=interaction.interaction_id, importance=0.8, confidence=0.95))
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
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
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
    return {"character_name": home["character"]["name"], "status": status, "bubble": bubble, "unread_count": home["unread_count"], "open_target": "home"}


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
    character_id = ensure_seed(session, user_id=user_id, character_id=character_id)
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
