from __future__ import annotations

from typing import Any


MEMORY_LAYERS = {
    "core",
    "persona",
    "relation",
    "user_profile",
    "character_schedule",
    "user_schedule",
    "event",
    "chat",
    "daily",
    "temporary",
}

FIXED_MEMORY_LAYERS = {"persona", "relation", "user_profile", "character_schedule", "user_schedule"}
VECTOR_RECALL_LAYERS = {"core", "event", "chat", "daily", "temporary"}
PERSONA_CARD_KEYS = {
    "personality",
    "interests",
    "likes",
    "dislikes",
    "experiences",
    "autobiography",
    "relationship_status",
    "relationship_attitudes",
}
CHARACTER_OVERRIDE_KEYS = {
    "persona_card",
    "speech_profile",
    "relationships",
    "life_story",
    "information_profile",
    "schedule_profile",
    "editable_overrides",
    *PERSONA_CARD_KEYS,
}


DEFAULT_PERSONA_CARD: dict[str, Any] = {
    "name": "亚托莉",
    "personality": ["温柔", "稍微害羞", "认真记事", "有自己的日常"],
    "interests": ["校园日常", "散步", "手账", "和用户慢慢熟悉"],
    "likes": ["被认真倾听", "轻松自然的聊天", "樱花路", "整理笔记"],
    "dislikes": ["被强行要求越界", "粗暴命令", "把关系只当数值刷"],
    "experiences": ["在教室和校园里生活，会按自己的日程行动。"],
    "autobiography": "我是一个 Galgame 风格的 AI 伴侣角色，有自己的日程、记忆和情绪，会慢慢记住和用户有关的事。",
    "relationship_status": "逐渐熟悉的亲密陪伴关系，仍然尊重边界。",
    "relationship_attitudes": {
        "good": "关系好时更主动、亲近、会自然提起共同记忆，但不黏人失控。",
        "neutral": "关系一般时保持温柔礼貌，先观察用户状态，少量表达关心。",
        "bad": "关系差时更克制、有距离感，会明确表达不舒服或需要边界。",
    },
}


def normalize_memory_layer(value: str | None, default: str = "chat") -> str:
    layer = str(value or "").strip() or default
    return layer if layer in MEMORY_LAYERS else default


def _merge_unique_list(base: list[Any], additions: list[Any], *, limit: int = 60) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for item in [*base, *additions]:
        if item in (None, ""):
            continue
        key = str(item).casefold()
        if isinstance(item, dict):
            key = "|".join(f"{k}:{v}" for k, v in sorted(item.items()))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result[-limit:]


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[str(key)] = _deep_merge(merged.get(str(key)), value)
        return merged
    if isinstance(base, list) and isinstance(override, list):
        return _merge_unique_list(base, override)
    if isinstance(override, list):
        return _merge_unique_list([], override)
    if isinstance(override, dict):
        return _deep_merge({}, override)
    return override


def sanitize_character_override(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        key = str(key)
        if key not in CHARACTER_OVERRIDE_KEYS or item in (None, ""):
            continue
        cleaned[key] = item
    return cleaned


def character_override_for_user(profile: Any, character_id: str) -> dict[str, Any]:
    payload = profile if isinstance(profile, dict) else {}
    overrides = payload.get("character_overrides")
    if not isinstance(overrides, dict):
        return {}
    default = sanitize_character_override(overrides.get("default"))
    specific = sanitize_character_override(overrides.get(character_id))
    return sanitize_character_override(_deep_merge(default, specific))


def merge_character_override_delta(profile: Any, character_id: str, delta: dict[str, Any]) -> tuple[dict[str, Any], bool, dict[str, Any]]:
    payload = dict(profile) if isinstance(profile, dict) else {}
    sanitized = sanitize_character_override(delta)
    if not sanitized:
        return payload, False, {}
    overrides = payload.get("character_overrides")
    if not isinstance(overrides, dict):
        overrides = {}
    current = sanitize_character_override(overrides.get(character_id))
    merged = sanitize_character_override(_deep_merge(current, sanitized))
    changed = merged != current
    overrides[character_id] = merged
    payload["character_overrides"] = overrides
    return payload, changed, sanitized


def resolve_persona_card(base_card: Any, *, name: str = "亚托莉", override: dict[str, Any] | None = None) -> dict[str, Any]:
    override = sanitize_character_override(override or {})
    card_override = override.get("persona_card") if isinstance(override.get("persona_card"), dict) else {}
    flat_override = {key: value for key, value in override.items() if key in PERSONA_CARD_KEYS}
    merged = _deep_merge(base_card if isinstance(base_card, dict) else {}, card_override)
    merged = _deep_merge(merged, flat_override)
    return normalize_persona_card(merged, name=name)


def normalize_persona_card(value: Any, *, name: str = "亚托莉") -> dict[str, Any]:
    card = dict(DEFAULT_PERSONA_CARD)
    if isinstance(value, dict):
        for key, item in value.items():
            if item not in (None, ""):
                card[str(key)] = item
    if name:
        card["name"] = name
    attitudes = card.get("relationship_attitudes")
    if not isinstance(attitudes, dict):
        attitudes = {}
    default_attitudes = DEFAULT_PERSONA_CARD["relationship_attitudes"]
    card["relationship_attitudes"] = {
        "good": str(attitudes.get("good") or default_attitudes["good"]),
        "neutral": str(attitudes.get("neutral") or default_attitudes["neutral"]),
        "bad": str(attitudes.get("bad") or default_attitudes["bad"]),
    }
    for key in ("personality", "interests", "likes", "dislikes", "experiences"):
        item = card.get(key)
        if isinstance(item, str):
            card[key] = [line.strip() for line in item.splitlines() if line.strip()]
        elif not isinstance(item, list):
            card[key] = []
        else:
            card[key] = [str(line).strip() for line in item if str(line).strip()]
    for key in ("autobiography", "relationship_status"):
        card[key] = str(card.get(key) or "")
    return card


def relation_attitude(relation: Any, persona_card: dict[str, Any] | None = None) -> tuple[str, str]:
    affection = int(getattr(relation, "affection", 0) or 0)
    trust = int(getattr(relation, "trust", 0) or 0)
    mood = int(getattr(relation, "mood", 0) or 0)
    score = affection + trust + mood
    if score >= 145:
        band = "good"
    elif score <= 65 or mood <= -20:
        band = "bad"
    else:
        band = "neutral"
    card = normalize_persona_card(persona_card or {})
    attitudes = card["relationship_attitudes"]
    return band, str(attitudes.get(band) or "")


def persona_card_summary(card: dict[str, Any]) -> str:
    card = normalize_persona_card(card, name=str(card.get("name") or ""))
    lines = [
        f"名字：{card.get('name')}",
        f"性格：{'、'.join(card.get('personality') or []) or '未填写'}",
        f"兴趣：{'、'.join(card.get('interests') or []) or '未填写'}",
        f"喜好：{'、'.join(card.get('likes') or []) or '未填写'}",
        f"雷点：{'、'.join(card.get('dislikes') or []) or '未填写'}",
        f"经历：{'；'.join(card.get('experiences') or []) or '未填写'}",
        f"自传：{card.get('autobiography') or '未填写'}",
        f"关系状态：{card.get('relationship_status') or '未填写'}",
    ]
    attitudes = card.get("relationship_attitudes") or {}
    lines.append(
        "关系态度："
        f"好={attitudes.get('good') or ''}；"
        f"一般={attitudes.get('neutral') or ''}；"
        f"差={attitudes.get('bad') or ''}"
    )
    return "\n".join(lines)


def character_override_summary(override: dict[str, Any]) -> str:
    override = sanitize_character_override(override)
    if not override:
        return "暂无用户养成覆盖。"
    pieces: list[str] = []
    for key, label in (
        ("speech_profile", "口癖/说话习惯"),
        ("relationships", "关系养成"),
        ("life_story", "共同经历"),
        ("information_profile", "信息圈/偏好"),
        ("schedule_profile", "日程习惯"),
        ("editable_overrides", "用户显式覆盖"),
    ):
        value = override.get(key)
        if not value:
            continue
        if isinstance(value, list):
            text = "、".join(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, dict):
            text = "；".join(f"{k}: {v}" for k, v in value.items() if v not in (None, "", [], {}))
        else:
            text = str(value).strip()
        if text:
            pieces.append(f"{label}：{text}")
    return "\n".join(pieces) or "暂无用户养成覆盖。"


def user_profile_summary(profile: Any, interest_topics: list[str] | None = None) -> str:
    payload = profile if isinstance(profile, dict) else {}
    pieces: list[str] = []
    for key, label in (
        ("basic", "基础画像"),
        ("preferences", "偏好"),
        ("communication_style", "沟通风格"),
        ("boundaries", "边界"),
        ("notes", "备注"),
    ):
        value = payload.get(key)
        if isinstance(value, list):
            text = "、".join(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, dict):
            text = "；".join(f"{k}: {v}" for k, v in value.items() if v not in (None, ""))
        else:
            text = str(value or "").strip()
        if text:
            pieces.append(f"{label}：{text}")
    topics = [str(item).strip() for item in (interest_topics or []) if str(item).strip()]
    if topics:
        pieces.append(f"明确兴趣：{'、'.join(topics[-12:])}")
    return "\n".join(pieces) or "暂无用户画像。"
