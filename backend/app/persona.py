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
