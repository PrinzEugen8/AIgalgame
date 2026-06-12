from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from .diagnostics import write_diagnostic
from .models import Character, User
from .persona import FIXED_MEMORY_LAYERS, MEMORY_LAYERS, VECTOR_RECALL_LAYERS
from .providers import OpenAICompatibleClient, get_enabled_provider
from .schemas import EventIn
from .utils import clamp, load_json
from .weather import is_weather_question


PLAN_MEMORY_LAYERS = FIXED_MEMORY_LAYERS | VECTOR_RECALL_LAYERS
PLAN_KEYS = {
    "use_recent_dialogue",
    "use_user_profile",
    "use_memory",
    "use_moment_interactions",
    "use_character_schedule",
    "use_user_schedule",
    "use_calendar",
    "use_weather",
    "use_web_search",
    "context_sources",
    "sources",
}


@dataclass
class ContextPlan:
    use_recent_dialogue: bool = True
    use_user_profile: bool = False
    use_memory: bool = False
    memory_query: str = ""
    memory_layers: list[str] = field(default_factory=lambda: sorted(PLAN_MEMORY_LAYERS))
    memory_limit: int = 6
    use_moment_interactions: bool = False
    use_character_schedule: bool = False
    schedule_scope: str = "none"
    use_user_schedule: bool = False
    user_schedule_scope: str = "active"
    use_calendar: bool = False
    calendar_query: str = ""
    use_weather: bool = False
    use_web_search: bool = False
    web_search_query: str = ""
    reason: str = ""
    confidence: float = 0.6
    source: str = "heuristic"
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _compact_text(value: str, limit: int = 1200) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "需要", "是", "取用"}


def _as_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        return clamp(int(float(value)), minimum, maximum)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.6) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _source_flag(payload: dict[str, Any], name: str, default: bool) -> bool:
    sources = payload.get("context_sources")
    if not isinstance(sources, dict):
        sources = payload.get("sources")
    if isinstance(sources, dict) and name in sources:
        value = sources.get(name)
        if isinstance(value, dict):
            return _as_bool(value.get("use", value.get("used")), default)
        return _as_bool(value, default)
    return _as_bool(payload.get(f"use_{name}"), default)


def _source_payload(payload: dict[str, Any], name: str) -> dict[str, Any]:
    sources = payload.get("context_sources")
    if not isinstance(sources, dict):
        sources = payload.get("sources")
    value = sources.get(name) if isinstance(sources, dict) else {}
    return value if isinstance(value, dict) else {}


def _normalize_layers(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_layers = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        raw_layers = [str(item).strip() for item in value]
    else:
        raw_layers = []
    layers = [layer for layer in raw_layers if layer in MEMORY_LAYERS]
    return sorted(dict.fromkeys(layers)) or sorted(PLAN_MEMORY_LAYERS)


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _needs_web_search_by_text(text: str) -> bool:
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return False
    return _contains_any(
        normalized,
        (
            "联网",
            "搜索",
            "搜一下",
            "查一下",
            "网上",
            "最新",
            "新闻",
            "热搜",
            "实时",
            "今天发生",
            "最近发生",
            "现在的价格",
            "现在版本",
            "发布了吗",
        ),
    )


def _needs_calendar_by_text(text: str) -> bool:
    return _contains_any(
        str(text or ""),
        ("放假", "假期", "节", "节日", "端午", "中秋", "国庆", "春节", "日期", "几号", "什么时候", "明天", "后天", "下周", "再过"),
    )


def _needs_character_schedule_by_text(text: str, character: Character) -> bool:
    normalized = " ".join(str(text or "").split())
    role_names = tuple(name for name in ("你", "妳", character.name, character.character_id, "亚托莉") if name)
    schedule_markers = ("现在", "刚刚", "今天", "日程", "安排", "在哪", "哪里", "做什么", "干嘛", "忙什么")
    if _contains_any(normalized, role_names) and _contains_any(normalized, schedule_markers):
        return True
    if _contains_any(normalized, ("日程", "安排", "刚刚")) and not _contains_any(normalized, ("我的", "我今天", "我明天", "提醒我")):
        return True
    return False


def _needs_user_schedule_by_text(text: str) -> bool:
    normalized = " ".join(str(text or "").split())
    return _contains_any(
        normalized,
        ("我的日程", "我的安排", "我今天", "我明天", "我待会", "我等下", "提醒我", "别忘了", "约了", "开会", "考试", "几点叫我"),
    )


def _needs_memory_by_text(text: str) -> bool:
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return False
    return _contains_any(
        normalized,
        (
            "记得",
            "记住",
            "之前",
            "以前",
            "上次",
            "那次",
            "我们聊过",
            "我说过",
            "我喜欢",
            "我讨厌",
            "我关注",
            "我想了解",
            "我的名字",
            "我的偏好",
            "承诺",
            "约定",
            "回忆",
            "朋友圈",
        ),
    )


def _needs_moment_interactions_by_text(text: str) -> bool:
    return _contains_any(str(text or ""), ("朋友圈", "动态", "点赞", "评论", "你发的", "照片"))


def _heuristic_context_plan(
    *,
    event: EventIn,
    user: User,
    character: Character,
    text: str,
    user_profile_used: bool,
) -> ContextPlan:
    clean_text = _compact_text(text, 500)
    use_memory = _needs_memory_by_text(clean_text)
    use_character_schedule = _needs_character_schedule_by_text(clean_text, character)
    use_user_schedule = _needs_user_schedule_by_text(clean_text)
    use_calendar = _needs_calendar_by_text(clean_text)
    use_weather = is_weather_question(clean_text)
    use_web_search = _needs_web_search_by_text(clean_text)
    use_moment_interactions = _needs_moment_interactions_by_text(clean_text)
    if event.event_type in {"notification_opened", "widget_opened"}:
        use_character_schedule = True
    if event.event_type == "option_selected":
        use_memory = use_memory or bool(str(event.payload.get("reply_id") or "").strip())
    reasons: list[str] = []
    for enabled, label in (
        (use_memory, "memory"),
        (use_character_schedule, "character_schedule"),
        (use_user_schedule, "user_schedule"),
        (use_calendar, "calendar"),
        (use_weather, "weather"),
        (use_web_search, "web_search"),
        (use_moment_interactions, "moment_interactions"),
    ):
        if enabled:
            reasons.append(label)
    return ContextPlan(
        use_recent_dialogue=event.event_type in {"user_message", "option_selected", "notification_opened", "widget_opened"},
        use_user_profile=user_profile_used and event.event_type in {"user_message", "option_selected", "notification_opened", "widget_opened"},
        use_memory=use_memory,
        memory_query=clean_text,
        memory_layers=sorted(PLAN_MEMORY_LAYERS),
        memory_limit=6 if use_memory else 0,
        use_moment_interactions=use_moment_interactions,
        use_character_schedule=use_character_schedule,
        schedule_scope="today" if "日程" in clean_text or "安排" in clean_text else ("current_next" if use_character_schedule else "none"),
        use_user_schedule=use_user_schedule,
        use_calendar=use_calendar,
        calendar_query=clean_text,
        use_weather=use_weather,
        use_web_search=use_web_search,
        web_search_query=clean_text,
        reason="; ".join(reasons) if reasons else "只需要当前消息、近期对话和固定人设。",
        source="heuristic",
    )


def _normalize_llm_plan(
    payload: dict[str, Any],
    *,
    fallback: ContextPlan,
    text: str,
    character: Character,
) -> ContextPlan:
    memory_payload = _source_payload(payload, "memory")
    character_schedule_payload = _source_payload(payload, "character_schedule") or _source_payload(payload, "schedule")
    user_schedule_payload = _source_payload(payload, "user_schedule")
    calendar_payload = _source_payload(payload, "calendar")
    web_payload = _source_payload(payload, "web_search")
    hard_memory = fallback.use_memory
    hard_character_schedule = fallback.use_character_schedule
    hard_user_schedule = fallback.use_user_schedule
    hard_calendar = fallback.use_calendar
    hard_weather = fallback.use_weather
    hard_web = fallback.use_web_search
    hard_moment = fallback.use_moment_interactions
    plan = ContextPlan(
        use_recent_dialogue=_source_flag(payload, "recent_dialogue", fallback.use_recent_dialogue),
        use_user_profile=_source_flag(payload, "user_profile", fallback.use_user_profile),
        use_memory=_source_flag(payload, "memory", fallback.use_memory) or hard_memory,
        memory_query=str(payload.get("memory_query") or memory_payload.get("query") or fallback.memory_query or text).strip(),
        memory_layers=_normalize_layers(payload.get("memory_layers") or memory_payload.get("layers")),
        memory_limit=_as_int(payload.get("memory_limit", memory_payload.get("limit")), fallback.memory_limit or 6, minimum=0, maximum=12),
        use_moment_interactions=_source_flag(payload, "moment_interactions", fallback.use_moment_interactions) or hard_moment,
        use_character_schedule=_source_flag(payload, "character_schedule", fallback.use_character_schedule) or _source_flag(payload, "schedule", fallback.use_character_schedule) or hard_character_schedule,
        schedule_scope=str(payload.get("schedule_scope") or character_schedule_payload.get("scope") or fallback.schedule_scope or "none").strip().lower(),
        use_user_schedule=_source_flag(payload, "user_schedule", fallback.use_user_schedule) or hard_user_schedule,
        user_schedule_scope=str(payload.get("user_schedule_scope") or user_schedule_payload.get("scope") or fallback.user_schedule_scope or "active").strip().lower(),
        use_calendar=_source_flag(payload, "calendar", fallback.use_calendar) or hard_calendar,
        calendar_query=str(payload.get("calendar_query") or calendar_payload.get("query") or fallback.calendar_query or text).strip(),
        use_weather=_source_flag(payload, "weather", fallback.use_weather) or hard_weather,
        use_web_search=_source_flag(payload, "web_search", fallback.use_web_search) or hard_web,
        web_search_query=str(payload.get("web_search_query") or web_payload.get("query") or fallback.web_search_query or text).strip(),
        reason=str(payload.get("reason") or fallback.reason).strip(),
        confidence=_as_float(payload.get("confidence"), fallback.confidence),
        source="llm",
        raw=payload,
    )
    if plan.schedule_scope not in {"none", "current", "current_next", "upcoming", "today"}:
        plan.schedule_scope = "current_next" if plan.use_character_schedule else "none"
    if not plan.use_memory:
        plan.memory_limit = 0
    elif plan.memory_limit <= 0:
        plan.memory_limit = 6
    if not plan.memory_query:
        plan.memory_query = text
    if not plan.calendar_query:
        plan.calendar_query = text
    if not plan.web_search_query:
        plan.web_search_query = text
    if _needs_character_schedule_by_text(text, character):
        plan.use_character_schedule = True
        if plan.schedule_scope == "none":
            plan.schedule_scope = "current_next"
    return plan


def _planner_prompt(
    *,
    event: EventIn,
    user: User,
    character: Character,
    text: str,
    recent_dialogue: str,
    user_profile_used: bool,
) -> str:
    payload = {
        "task": "为本轮 Galgame 伴侣回复选择需要注入给 replyer 的上下文。固定人设、当前用户消息、关系状态会始终提供；其余来源按需选择。",
        "event_type": event.event_type,
        "user_id": event.user_id,
        "character_id": event.character_id,
        "character_name": character.name,
        "local_time": event.client_context.get("local_time", ""),
        "message": text,
        "recent_dialogue_preview": _compact_text(recent_dialogue, 900),
        "user_has_profile_or_interests": user_profile_used,
        "available_sources": {
            "recent_dialogue": "最近 10 条对话。除非是无意义打开应用，通常需要。",
            "user_profile": "用户画像和兴趣。只有回复需要个性化偏好时取用。",
            "memory": "长期记忆。用于之前、上次、偏好、承诺、共同经历、长期事实。",
            "moment_interactions": "用户最近对朋友圈的点赞/评论。只有聊到朋友圈或互动时取用。",
            "character_schedule": "角色自己的今日真实日程。用于用户问你现在、刚刚、今天、在哪、做什么。",
            "user_schedule": "用户自己的主动承诺/提醒。用于用户问自己的安排或提醒。",
            "calendar": "日期、相对日期、节假日。",
            "weather": "今日天气快照。用于天气、下雨、温度、带伞等问题。",
            "web_search": "联网搜索。用于最新、实时、新闻、价格、版本、搜索、查一下等现实信息。",
        },
        "schema": {
            "use_recent_dialogue": "boolean",
            "use_user_profile": "boolean",
            "use_memory": "boolean",
            "memory_query": "string; 用于检索长期记忆的查询",
            "memory_layers": "array; 可选 core/persona/persona_canon/persona_editable/relation/relationship/mood_event/affection_event/user_profile/shared_memory/character_schedule/user_schedule/event/chat/daily/temporary",
            "memory_limit": "integer 0..12",
            "use_moment_interactions": "boolean",
            "use_character_schedule": "boolean",
            "schedule_scope": "none|current|current_next|upcoming|today",
            "use_user_schedule": "boolean",
            "use_calendar": "boolean",
            "calendar_query": "string",
            "use_weather": "boolean",
            "use_web_search": "boolean",
            "web_search_query": "string",
            "reason": "short Chinese reason",
            "confidence": "0..1",
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def build_context_plan(
    session: Session,
    *,
    event: EventIn,
    user: User,
    character: Character,
    text: str,
    recent_dialogue: str,
    user_profile_used: bool,
    allow_llm: bool = True,
) -> ContextPlan:
    fallback = _heuristic_context_plan(
        event=event,
        user=user,
        character=character,
        text=text,
        user_profile_used=user_profile_used,
    )
    if not allow_llm:
        fallback.source = "heuristic_llm_skipped"
        write_diagnostic(
            "context_planner_fallback",
            feature="上下文计划器",
            stage="plan",
            reason="llm_planner_skipped_for_background_prepare",
            plan=context_plan_reference(fallback),
        )
        return fallback
    config = get_enabled_provider(session, "llm_task")
    if config is None:
        write_diagnostic(
            "context_planner_fallback",
            feature="上下文计划器",
            stage="plan",
            reason="llm_task_not_configured",
            plan=context_plan_reference(fallback),
        )
        return fallback
    try:
        payload = OpenAICompatibleClient(config).chat_json(
            [
                {"role": "system", "content": "你是上下文计划器，只输出 JSON。你决定回复模型需要哪些上下文，不写可见台词。"},
                {
                    "role": "user",
                    "content": _planner_prompt(
                        event=event,
                        user=user,
                        character=character,
                        text=text,
                        recent_dialogue=recent_dialogue,
                        user_profile_used=user_profile_used,
                    ),
                },
            ],
            max_tokens=1600,
            temperature=0.1,
            diagnostic={
                "feature": "上下文计划器",
                "stage": "plan",
                "purpose": "Choose reply context sources",
                "input": {
                    "event_type": event.event_type,
                    "user_id": event.user_id,
                    "character_id": event.character_id,
                    "input_text": text,
                    "fallback": context_plan_reference(fallback),
                },
            },
        )
        if not isinstance(payload, dict) or not PLAN_KEYS.intersection(payload):
            raise ValueError("planner returned JSON without context plan keys")
        plan = _normalize_llm_plan(payload, fallback=fallback, text=text, character=character)
        write_diagnostic(
            "context_planner_result",
            feature="上下文计划器",
            stage="plan",
            source="llm",
            provider_id=config.provider_id,
            model=config.model,
            plan=context_plan_reference(plan),
        )
        return plan
    except Exception as exc:  # noqa: BLE001
        fallback.source = "heuristic_after_error"
        fallback.error = str(exc)
        write_diagnostic(
            "context_planner_error",
            feature="上下文计划器",
            stage="plan",
            provider_id=config.provider_id,
            model=config.model,
            error_type=type(exc).__name__,
            message=str(exc),
            plan=context_plan_reference(fallback),
        )
        return fallback


def context_plan_reference(plan: ContextPlan) -> dict[str, Any]:
    payload = asdict(plan)
    raw = payload.pop("raw", {})
    if raw:
        payload["raw_keys"] = sorted(str(key) for key in raw.keys())[:30]
    payload["summary"] = (
        f"{payload['source']}: memory={payload['use_memory']} schedule={payload['use_character_schedule']} "
        f"user_schedule={payload['use_user_schedule']} calendar={payload['use_calendar']} "
        f"weather={payload['use_weather']} web={payload['use_web_search']}"
    )
    return payload


def user_profile_has_content(user: User) -> bool:
    profile = load_json(user.profile_json, {})
    interests = load_json(user.interest_topics_json, [])
    return bool(profile or interests)
