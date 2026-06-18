from __future__ import annotations

import json
import random
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .diagnostics import write_diagnostic
from .models import Character, Message, RelationState, User
from .pipeline import _tts_for_line
from .providers import OpenAICompatibleClient, ProviderError, get_task_llm_provider
from .utils import uid


ACTION_TO_MOTION: dict[str, str] = {
    "phone_tablet": "idle_tablet",
    "phone_texting": "idle_texting",
    "typing": "idle_typing",
    "yawn": "idle_yawn",
    "waking": "idle_waking",
    "idle": "idle",
}

LOOP_ACTION_DURATION_RANGE: dict[str, tuple[float, float]] = {
    "phone_tablet": (45.0, 120.0),
    "phone_texting": (45.0, 120.0),
    "typing": (45.0, 120.0),
    "waking": (30.0, 90.0),
    "idle": (30.0, 75.0),
}

ONE_SHOT_ACTIONS = {"yawn"}

FALLBACK_LINES: dict[str, tuple[str, str]] = {
    "phone_tablet": ("我看一下刚刚记下来的东西。", "thinking"),
    "phone_texting": ("嗯……要不要给你发点什么呢。", "happy"),
    "typing": ("把这个小想法记下来，免得等会忘了。", "thinking"),
    "yawn": ("呼……稍微伸个懒腰。", "calm"),
    "waking": ("我在哦，只是刚刚有点走神。", "happy"),
    "idle": ("我就在这里，慢慢等你。", "calm"),
}


def relaxroom_idle_action(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    session_id: str = "relaxroom_unity",
    idle_seconds: int = 0,
    local_time: str = "",
    scene: str = "Scene_01",
    allow_llm: bool = True,
) -> dict[str, Any]:
    user = session.get(User, user_id)
    character = session.get(Character, character_id)
    if user is None or character is None:
        raise ProviderError("user or character missing")

    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one_or_none()
    recent = _recent_dialogue(session, user_id=user_id, character_id=character_id, session_id=session_id)
    decision = _fallback_decision(idle_seconds)

    if allow_llm:
        try:
            decision = _llm_idle_decision(
                session,
                user=user,
                character=character,
                relation=relation,
                recent_dialogue=recent,
                idle_seconds=idle_seconds,
                local_time=local_time,
                scene=scene,
            )
        except Exception as exc:  # noqa: BLE001
            write_diagnostic(
                "relaxroom_idle_llm_fallback",
                feature="RelaxRoomAI闲时动作",
                stage="idle_action",
                user_id=user_id,
                character_id=character_id,
                error_type=type(exc).__name__,
                message=str(exc),
            )

    action = _normalize_action(decision.get("action"))
    motion = ACTION_TO_MOTION[action]
    action_duration = _duration_for_action(action, decision.get("action_duration_seconds"))
    lines = _build_lines(
        session,
        user=user,
        character=character,
        action=action,
        motion=motion,
        raw_lines=decision.get("lines") if isinstance(decision.get("lines"), list) else [],
    )
    cooldown = _clamp_float(decision.get("cooldown_seconds"), 35.0, 240.0, max(75.0, action_duration + 12.0))
    if action_duration > 0:
        cooldown = max(cooldown, min(240.0, action_duration + 12.0))
    payload = {
        "ok": True,
        "action": action,
        "motion": motion,
        "lines": lines,
        "cooldown_seconds": cooldown,
        "action_duration_seconds": action_duration,
        "idle_seconds": max(0, int(idle_seconds or 0)),
        "scene": scene,
    }
    return {"event_type": "idle_action", "event_id": uid("evt"), "session_id": session_id, "payload": payload}


def _llm_idle_decision(
    session: Session,
    *,
    user: User,
    character: Character,
    relation: RelationState | None,
    recent_dialogue: str,
    idle_seconds: int,
    local_time: str,
    scene: str,
) -> dict[str, Any]:
    config = get_task_llm_provider(session)
    if config is None:
        return _fallback_decision(idle_seconds)

    relation_text = "unknown"
    if relation is not None:
        relation_text = (
            f"affection={relation.affection}, trust={relation.trust}, "
            f"dependency={relation.dependency}, mood={relation.mood}, stage={relation.relationship_stage}"
        )

    prompt = {
        "task": "Decide RelaxRoomAI idle behavior for a seated 3D character. Output JSON only.",
        "scene": scene,
        "local_time": local_time,
        "idle_seconds": max(0, int(idle_seconds or 0)),
        "character": {"id": character.character_id, "name": character.name, "style": character.speech_style},
        "relation": relation_text,
        "recent_dialogue": recent_dialogue,
        "available_actions": [
            {"action": "phone_tablet", "motion": "idle_tablet", "meaning": "play with a touchscreen/tablet while seated"},
            {"action": "phone_texting", "motion": "idle_texting", "meaning": "pick up phone, one-hand texting loop, then can put it down"},
            {"action": "typing", "motion": "idle_typing", "meaning": "sit-to-type, typing loop"},
            {"action": "yawn", "motion": "idle_yawn", "meaning": "stretch or yawn while keeping seated lower body"},
            {"action": "waking", "motion": "idle_waking", "meaning": "small waking/coming-back gesture"},
            {"action": "idle", "motion": "idle", "meaning": "stay seated quietly"},
        ],
        "rules": [
            "Pick one action that feels natural after the user has been idle.",
            "For loop actions phone_tablet, phone_texting, typing, waking, and idle, set action_duration_seconds between 30 and 120 so the motion can breathe.",
            "For yawn, leave action_duration_seconds at 0 because it is a short one-shot.",
            "Generate 0 to 2 short Chinese self-talk lines, not a direct pushy question.",
            "Use calm/happy/thinking/shy/sad as emotion.",
            "Keep boundaries safe and affectionate; do not mention backend, LLM, or system.",
            "If idle_seconds is under 90, prefer subtle actions such as phone_tablet, phone_texting, or idle.",
        ],
        "schema": {
            "action": "phone_tablet|phone_texting|typing|yawn|waking|idle",
            "cooldown_seconds": 45,
            "action_duration_seconds": 60,
            "lines": [{"text": "短中文自言自语", "emotion": "calm|happy|thinking|shy|sad", "expression": "calm|happy|thinking|shy|sad"}],
        },
    }
    result = OpenAICompatibleClient(config).chat_json(
        [
            {"role": "system", "content": "You generate compact JSON for a seated galgame character idle action."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        max_tokens=1200,
        temperature=0.65,
        diagnostic={
            "feature": "RelaxRoomAI闲时动作",
            "stage": "idle_action",
            "purpose": "Choose seated idle action and self-talk",
            "input": {"user_id": user.user_id, "character_id": character.character_id, "idle_seconds": idle_seconds},
        },
    )
    return result if isinstance(result, dict) else _fallback_decision(idle_seconds)


def _build_lines(
    session: Session,
    *,
    user: User,
    character: Character,
    action: str,
    motion: str,
    raw_lines: list[Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in raw_lines[:2]:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split()).strip()
        if not text:
            continue
        emotion = str(item.get("emotion") or "calm").strip() or "calm"
        expression = str(item.get("expression") or emotion).strip() or emotion
        candidates.append(_line_dict(text=text, emotion=emotion, expression=expression, motion=motion))

    if not candidates:
        text, emotion = FALLBACK_LINES[action]
        candidates.append(_line_dict(text=text, emotion=emotion, expression=emotion, motion=motion))

    enriched: list[dict[str, Any]] = []
    for line in candidates:
        if user.tts_enabled:
            try:
                tts_url, tts_error = _tts_for_line(session, user, character, line["text"], line["emotion"])
                line["tts_audio_url"] = tts_url
                line["tts_error"] = tts_error
            except Exception as exc:  # noqa: BLE001
                line["tts_audio_url"] = ""
                line["tts_error"] = str(exc)
        enriched.append(line)
    return enriched


def _line_dict(*, text: str, emotion: str, expression: str, motion: str) -> dict[str, Any]:
    return {
        "line_id": uid("idleline"),
        "text": text,
        "emotion": emotion,
        "pose": motion,
        "expression": expression,
        "motion": motion,
        "controller": {
            "state": "speaking",
            "face": expression or emotion,
            "animation": motion,
            "mouth": "auto",
            "lipsync": True,
            "pause": 0.0,
            "tags": [f"[face:{expression or emotion}]", f"[anim:{motion}]"],
        },
        "background": "relaxroom_scene_01",
        "tts_audio_url": "",
        "tts_error": "",
    }


def _fallback_decision(idle_seconds: int) -> dict[str, Any]:
    if idle_seconds >= 240:
        action = random.choice(["yawn", "waking", "phone_texting"])
    elif idle_seconds >= 120:
        action = random.choice(["phone_tablet", "phone_texting", "typing"])
    else:
        action = random.choice(["phone_tablet", "phone_texting", "idle"])
    duration = _duration_for_action(action, None)
    return {"action": action, "cooldown_seconds": max(75, int(duration + 12)), "action_duration_seconds": duration, "lines": []}


def _normalize_action(value: Any) -> str:
    action = str(value or "").strip().lower()
    return action if action in ACTION_TO_MOTION else "idle"


def _duration_for_action(action: str, requested: Any) -> float:
    if action in ONE_SHOT_ACTIONS:
        return 0.0
    minimum, maximum = LOOP_ACTION_DURATION_RANGE.get(action, (30.0, 90.0))
    try:
        value = float(requested)
    except (TypeError, ValueError):
        value = random.uniform(minimum, maximum)
    return round(max(minimum, min(maximum, value)), 1)


def _recent_dialogue(session: Session, *, user_id: str, character_id: str, session_id: str) -> str:
    rows = session.execute(
        select(Message)
        .where(Message.user_id == user_id, Message.character_id == character_id, Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(6)
    ).scalars().all()
    if not rows:
        return "No recent dialogue."
    lines = []
    for row in reversed(rows):
        speaker = "USER" if row.sender_type == "user" else "CHARACTER"
        content = " ".join(row.content.split())
        if content:
            lines.append(f"{speaker}: {content[:100]}")
    return "\n".join(lines) or "No recent dialogue."


def _clamp_float(value: Any, minimum: float, maximum: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))
