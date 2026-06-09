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
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .database import get_session, init_db
from .logging_setup import maybe_start_debugger, setup_logging
from .models import (
    MediaAsset,
    Memory,
    Moment,
    MomentInteraction,
    ProviderConfig,
    RelationState,
    ScheduleSlot,
    User,
)
from .pipeline import handle_event
from .providers import (
    OpenAICompatibleClient,
    ProviderError,
    VolcArkWebSearchClient,
    get_enabled_provider,
    provider_presets,
    provider_ready,
    provider_to_out,
    run_provider_test,
    upsert_provider,
)
from .schemas import EventIn, ProviderConfigIn, ProviderConfigOut, ProviderTestRequest, ProviderTestResult
from .schedule import ensure_schedule, run_daily_cycle
from .scheduler import start_scheduler, stop_scheduler
from .seed import DEFAULT_CHARACTER_ID, DEFAULT_USER_ID, ensure_seed
from .utils import dump_json, load_json, uid


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
        for kind in ("llm", "tts", "search", "image")
    }
    return {"ok": True, "providers": providers, "configured": configured}


@app.get("/api/bootstrap")
def bootstrap(
    user_id: str = DEFAULT_USER_ID,
    character_id: str = DEFAULT_CHARACTER_ID,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    ensure_seed(session, user_id=user_id, character_id=character_id)
    user = session.get(User, user_id)
    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one()
    return {
        "user": {
            "user_id": user_id,
            "story_completed": bool(user and user.story_completed),
            "interest_topics": load_json(user.interest_topics_json if user else "[]", []),
        },
        "character": {"character_id": character_id, "name": "小樱", "age_setting": "18+"},
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
        return result.model_dump()
    except ProviderError as exc:
        logger.warning(
            "provider error during event event_type=%s session_id=%s message=%s",
            event.event_type,
            event.session_id,
            exc,
        )
        return {"event_type": "error", "event_id": uid("evt"), "session_id": event.session_id, "payload": {"message": str(exc)}}


@app.websocket("/ws/app")
async def app_ws(websocket: WebSocket, user_id: str = DEFAULT_USER_ID, device_id: str = "device") -> None:
    await websocket.accept()
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
                "comments": [
                    {"actor_id": item.actor_id, "content": item.content, "created_at": item.created_at}
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
    interaction = MomentInteraction(interaction_id=uid("mi"), moment_id=moment_id, actor_id=user_id, interaction_type="like")
    session.add(interaction)
    session.add(Memory(memory_id=uid("mem"), user_id=user_id, character_id=DEFAULT_CHARACTER_ID, layer="temporary", content="用户点赞了小樱的朋友圈。", source_event_id=interaction.interaction_id, importance=0.6, confidence=0.9))
    session.commit()
    return {"ok": True, "interaction_id": interaction.interaction_id}


@app.post("/api/moments/{moment_id}/comments")
def comment_moment(moment_id: str, payload: dict[str, Any], user_id: str = DEFAULT_USER_ID, session: Session = Depends(get_session)) -> dict[str, Any]:
    if session.get(Moment, moment_id) is None:
        raise HTTPException(status_code=404, detail="moment not found")
    content = str(payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content required")
    interaction = MomentInteraction(interaction_id=uid("mi"), moment_id=moment_id, actor_id=user_id, interaction_type="comment", content=content)
    session.add(interaction)
    session.add(Memory(memory_id=uid("mem"), user_id=user_id, character_id=DEFAULT_CHARACTER_ID, layer="temporary", content=f"用户评论了小樱的朋友圈：{content}", source_event_id=interaction.interaction_id, importance=0.8, confidence=0.95))
    session.commit()
    return {"ok": True, "interaction_id": interaction.interaction_id}


@app.get("/api/calendar")
def calendar(month: str = "", user_id: str = DEFAULT_USER_ID, character_id: str = DEFAULT_CHARACTER_ID, session: Session = Depends(get_session)) -> dict[str, Any]:
    day = datetime.now()
    ensure_schedule(session, user_id=user_id, character_id=character_id, day=day)
    prefix = month or day.strftime("%Y-%m")
    rows = session.execute(select(ScheduleSlot).where(ScheduleSlot.user_id == user_id, ScheduleSlot.schedule_date.like(f"{prefix}%"))).scalars().all()
    return {
        "month": prefix,
        "days": [
            {
                "date": row.schedule_date,
                "start_at": row.start_at,
                "activity_title": row.activity_title,
                "status": row.actual_status,
                "salience": row.salience,
            }
            for row in rows
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


@app.post("/api/debug/generate-proactive")
def debug_proactive(user_id: str = DEFAULT_USER_ID, session: Session = Depends(get_session)) -> dict[str, Any]:
    logger.info("debug proactive requested user_id=%s", user_id)
    user = session.get(User, user_id)
    topics = load_json(user.interest_topics_json if user else "[]", [])
    search = get_enabled_provider(session, "search")
    if topics and user and user.news_enabled:
        if search is None:
            return {"ok": False, "message": "Search provider is not configured; cannot generate news proactive message."}
        if search.provider != "volc_ark_web_search" or not provider_ready(search):
            return {"ok": False, "message": "Search provider must be a tested Volc Ark Web Search configuration."}
        try:
            result = VolcArkWebSearchClient(search).search(
                f"请联网搜索与「{topics[-1]}」相关的最新内容，必须返回标题、链接、发布时间。",
                require_published_at=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("debug proactive search failed user_id=%s", user_id)
            return {"ok": False, "message": str(exc)}
        sources = result.get("sources") or []
        if not sources:
            return {"ok": False, "message": "Search result did not include verifiable sources."}
        text = f"我刚看到和「{topics[-1]}」有关的新内容：{result.get('summary') or sources[0].get('title')}"
    else:
        text = "今天有一件小事想告诉你。"
    return {"ok": True, "event": {"event_type": "proactive_message", "payload": {"title": "小樱想和你说话", "text": text}}}


@app.post("/api/debug/advance-time")
def debug_advance(payload: dict[str, Any]) -> dict[str, Any]:
    logger.info("debug advance-time payload=%s", payload)
    return {"ok": True, "message": "Demo uses client-provided local_time for interruption checks.", "received": payload}
