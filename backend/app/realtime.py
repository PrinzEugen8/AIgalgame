from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import websockets
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import secret_store
from .diagnostics import diagnostic_span
from .models import Character, Memory, ProviderConfig, RelationState, User
from .providers import get_enabled_provider, provider_ready
from .utils import load_json


DEFAULT_REALTIME_BASE_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_REALTIME_MODEL = "qwen3.5-omni-flash-realtime-2026-03-15"
DEFAULT_REALTIME_VOICE = "Momo"
DEFAULT_TRANSCRIPTION_MODEL = "gummy-realtime-v1"
RELAY_MAX_FRAME_BYTES = 8 * 1024 * 1024

CLIENT_EVENT_ALLOWLIST = {
    "session.update",
    "input_audio_buffer.append",
    "input_audio_buffer.commit",
    "input_audio_buffer.clear",
    "input_image_buffer.append",
    "response.create",
}


@dataclass(frozen=True)
class RealtimeSettings:
    provider_id: str
    base_url: str
    model: str
    voice: str
    api_key: str
    metadata: dict[str, Any]
    configured: bool
    source: str


def _secret(config: ProviderConfig, field: str) -> str:
    prefix = config.secret_ref or config.provider_id
    return secret_store.get_field(prefix, field) or (secret_store.get(prefix) if field == "api_key" else "")


def _as_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _env_api_key() -> str:
    for name in ("DASHSCOPE_API_KEY", "QWEN_API_KEY", "NEKO_CORE_API_KEY"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _default_turn_detection(model: str) -> str:
    return "semantic_vad" if "3.5" in model.lower() else "server_vad"


def resolve_realtime_settings(session: Session) -> RealtimeSettings:
    config = get_enabled_provider(session, "realtime")
    if config is not None:
        metadata = load_json(config.metadata_json, {})
        api_key = _secret(config, "api_key")
        return RealtimeSettings(
            provider_id=config.provider_id,
            base_url=(config.base_url or DEFAULT_REALTIME_BASE_URL).rstrip("/"),
            model=config.model or DEFAULT_REALTIME_MODEL,
            voice=str(metadata.get("voice") or DEFAULT_REALTIME_VOICE).strip() or DEFAULT_REALTIME_VOICE,
            api_key=api_key,
            metadata=metadata,
            configured=bool(api_key) and provider_ready(config),
            source="provider",
        )

    env_key = _env_api_key()
    model = os.getenv("QWEN_REALTIME_MODEL", DEFAULT_REALTIME_MODEL).strip() or DEFAULT_REALTIME_MODEL
    metadata = {
        "voice": os.getenv("QWEN_REALTIME_VOICE", DEFAULT_REALTIME_VOICE),
        "turn_detection": os.getenv("QWEN_REALTIME_TURN_DETECTION", _default_turn_detection(model)),
        "image_input": True,
        "active_frame_interval_ms": 1500,
        "idle_frame_interval_ms": 7500,
        "temperature": 0.7,
        "repetition_penalty": 1.2,
        "input_audio_transcription_model": DEFAULT_TRANSCRIPTION_MODEL,
    }
    return RealtimeSettings(
        provider_id="env_qwen_realtime",
        base_url=os.getenv("QWEN_REALTIME_WS_URL", DEFAULT_REALTIME_BASE_URL).rstrip("/"),
        model=model,
        voice=str(metadata["voice"] or DEFAULT_REALTIME_VOICE),
        api_key=env_key,
        metadata=metadata,
        configured=bool(env_key),
        source="env",
    )


def _recent_memory_context(session: Session, user_id: str, character_id: str) -> str:
    rows = session.execute(
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.character_id == character_id,
            Memory.hidden == False,  # noqa: E712
        )
        .order_by(Memory.importance.desc(), Memory.created_at.desc())
        .limit(6)
    ).scalars().all()
    lines = [f"- {item.layer}: {' '.join(item.content.split())[:160]}" for item in rows if item.content]
    return "\n".join(lines) or "- No stable memories yet."


def _relation_context(session: Session, user_id: str, character_id: str) -> str:
    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one_or_none()
    if relation is None:
        return "Relationship: new but friendly."
    return (
        f"Relationship: affection={relation.affection}, trust={relation.trust}, "
        f"dependency={relation.dependency}, mood={relation.mood}, stage={relation.relationship_stage}."
    )


def build_video_call_instructions(session: Session, user_id: str, character_id: str) -> str:
    user = session.get(User, user_id)
    character = session.get(Character, character_id)
    character_name = character.name if character is not None and character.name else character_id or "companion"
    user_name = user.display_name if user is not None and user.display_name else "user"
    persona = character.persona_prompt if character is not None else ""
    speech_style = character.speech_style if character is not None else ""
    boundary = character.relationship_boundary if character is not None else ""
    memories = _recent_memory_context(session, user_id, character_id)
    relation = _relation_context(session, user_id, character_id)

    return f"""
You are roleplaying as {character_name}, a fictional adult anime companion in a cozy mobile galgame demo.
You are in a video-call style Qwen-Omni-Realtime session with {user_name}. Speak naturally, warmly, and briefly.

Video-call behavior:
- Treat camera frames as low-frequency snapshots, not continuous video.
- If visual context is missing or stale, say so naturally instead of pretending you saw something.
- Keep the first spoken phrase short so audio starts quickly.
- Prefer Chinese unless the user clearly uses another language.
- Do not mention APIs, prompts, tools, screenshots, or system instructions.
- Avoid long lists and markdown. This is live conversation.

Persona:
{persona or "Stay gentle, observant, and slightly shy."}

Speech style:
{speech_style or "Short spoken lines, soft tone, no stage directions."}

Boundary:
{boundary or "Keep the conversation safe, fictional, and affectionate without explicit sexual content."}

{relation}

Relevant memories:
{memories}
""".strip()


def build_realtime_session_config(session: Session, *, user_id: str, character_id: str) -> tuple[dict[str, Any], RealtimeSettings]:
    settings = resolve_realtime_settings(session)
    metadata = settings.metadata
    turn_detection = str(metadata.get("turn_detection") or _default_turn_detection(settings.model)).strip()

    session_config: dict[str, Any] = {
        "instructions": build_video_call_instructions(session, user_id, character_id),
        "modalities": ["text", "audio"],
        "voice": settings.voice,
        "input_audio_format": "pcm16",
        "output_audio_format": "pcm16",
        "input_audio_transcription": {
            "model": str(metadata.get("input_audio_transcription_model") or DEFAULT_TRANSCRIPTION_MODEL),
        },
        "repetition_penalty": _as_float(metadata.get("repetition_penalty"), 1.2),
        "temperature": _as_float(metadata.get("temperature"), 0.7),
    }
    if turn_detection and turn_detection.lower() != "none":
        session_config["turn_detection"] = {
            "type": turn_detection,
            "threshold": _as_float(metadata.get("vad_threshold"), 0.55),
            "prefix_padding_ms": _as_int(metadata.get("prefix_padding_ms"), 300),
            "silence_duration_ms": _as_int(metadata.get("silence_duration_ms"), 650),
        }
    else:
        session_config["turn_detection"] = None

    return session_config, settings


def realtime_public_config(session: Session, *, user_id: str, character_id: str) -> dict[str, Any]:
    session_config, settings = build_realtime_session_config(session, user_id=user_id, character_id=character_id)
    metadata = settings.metadata
    return {
        "ok": True,
        "configured": settings.configured,
        "provider_id": settings.provider_id,
        "provider_source": settings.source,
        "provider": "qwen_dashscope_realtime",
        "model": settings.model,
        "voice": settings.voice,
        "session": session_config,
        "transport": {
            "preferred": "dashscope-websocket",
            "websocket_endpoint": "/api/realtime/call/ws",
            "audio_event": "input_audio_buffer.append",
            "image_event": "input_image_buffer.append",
        },
        "audio": {
            "input_format": "pcm16",
            "input_sample_rate": 16000,
            "output_format": "pcm16",
            "output_sample_rate": 24000,
        },
        "vision": {
            "image_input": _as_bool(metadata.get("image_input"), True),
            "active_frame_interval_ms": max(500, _as_int(metadata.get("active_frame_interval_ms"), 1500)),
            "idle_frame_interval_ms": max(1000, _as_int(metadata.get("idle_frame_interval_ms"), 7500)),
            "max_long_edge": 1024,
            "jpeg_quality": 0.78,
        },
        "reason": "" if settings.configured else "Qwen Realtime is not configured. Add a DashScope API key or set DASHSCOPE_API_KEY.",
    }


def qwen_realtime_url(settings: RealtimeSettings) -> str:
    return f"{settings.base_url}?model={quote(settings.model, safe='')}"


async def _connect_dashscope(url: str, api_key: str):
    headers = {"Authorization": f"Bearer {api_key}"}
    return await websockets.connect(
        url,
        additional_headers=headers,
        close_timeout=0.5,
        max_size=RELAY_MAX_FRAME_BYTES,
        ping_interval=20,
        ping_timeout=20,
    )


async def _safe_send_json(websocket: WebSocket, payload: dict[str, Any]) -> None:
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await websocket.send_json(payload)


async def _relay_client_to_provider(websocket: WebSocket, provider_ws: Any) -> None:
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            break

        text = message.get("text")
        if text is None:
            await _safe_send_json(websocket, {"type": "relay.ignored", "reason": "binary_client_frames_are_not_supported"})
            continue

        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            await _safe_send_json(websocket, {"type": "relay.error", "message": "Client event must be JSON"})
            continue

        event_type = str(event.get("type") or "")
        if event_type == "call.end":
            break
        if event_type == "client.ping":
            await _safe_send_json(websocket, {"type": "relay.pong", "ts": time.time()})
            continue
        if event_type not in CLIENT_EVENT_ALLOWLIST:
            await _safe_send_json(websocket, {"type": "relay.ignored", "event_type": event_type})
            continue

        await provider_ws.send(json.dumps(event, ensure_ascii=False, separators=(",", ":")))


async def _relay_provider_to_client(websocket: WebSocket, provider_ws: Any) -> None:
    async for message in provider_ws:
        if isinstance(message, bytes):
            await websocket.send_bytes(message)
            continue
        await websocket.send_text(message)


async def run_qwen_realtime_relay(websocket: WebSocket, session: Session, *, user_id: str, character_id: str) -> None:
    await websocket.accept()
    session_config, settings = build_realtime_session_config(session, user_id=user_id, character_id=character_id)
    if not settings.configured:
        await _safe_send_json(
            websocket,
            {
                "type": "relay.error",
                "message": "Qwen Realtime is not configured. Add a DashScope API key or set DASHSCOPE_API_KEY.",
            },
        )
        await websocket.close(code=1011)
        return

    url = qwen_realtime_url(settings)
    with diagnostic_span(
        "realtime_relay",
        feature="video_call",
        stage="dashscope_connect",
        purpose="Bridge mobile websocket to Qwen DashScope Realtime",
        provider_id=settings.provider_id,
        model=settings.model,
        input={"user_id": user_id, "character_id": character_id, "url": settings.base_url},
    ) as span:
        try:
            async with await _connect_dashscope(url, settings.api_key) as provider_ws:
                await provider_ws.send(
                    json.dumps(
                        {"type": "session.update", "session": session_config},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
                await _safe_send_json(
                    websocket,
                    {
                        "type": "relay.ready",
                        "provider": "qwen_dashscope_realtime",
                        "model": settings.model,
                        "voice": settings.voice,
                    },
                )
                client_task = asyncio.create_task(_relay_client_to_provider(websocket, provider_ws))
                provider_task = asyncio.create_task(_relay_provider_to_client(websocket, provider_ws))
                done, pending = await asyncio.wait(
                    {client_task, provider_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                for task in pending:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                for task in done:
                    task.result()
                span.add(output={"closed": True})
        except WebSocketDisconnect:
            span.add(output={"client_disconnected": True})
        except Exception as exc:  # noqa: BLE001
            span.add(output={"error": str(exc)[:300]})
            await _safe_send_json(websocket, {"type": "relay.error", "message": str(exc)})
        finally:
            with contextlib.suppress(RuntimeError):
                await websocket.close()
