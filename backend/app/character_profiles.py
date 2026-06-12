from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from .models import Character, UserCharacterProfile
from .persona import normalize_persona_card
from .utils import dump_json, load_json, utc_now


EDITABLE_PERSONA_OVERLAY_KEYS = {
    "speech_profile",
    "life_story",
    "relationships",
    "schedule_profile",
    "information_profile",
    "repost_profile",
    "editable_overrides",
    "personality_profile",
    "likes_dislikes",
    "personality",
    "interests",
    "likes",
    "dislikes",
    "experiences",
    "autobiography",
    "relationship_status",
    "relationship_attitudes",
}

IMMUTABLE_PERSONA_KEYS = {
    "identity",
    "canon_profile",
    "boundaries",
    "growth_rules",
    "name",
    "schema_version",
}


@dataclass(frozen=True)
class ResolvedCharacterProfile:
    user_id: str
    character_id: str
    character_name: str
    base_card: dict[str, Any]
    overlay: dict[str, Any]
    card: dict[str, Any]
    source_memory_ids: list[str]
    revision: int
    has_overlay: bool


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = deepcopy(base)
        for key, value in override.items():
            merged[key] = _deep_merge(merged.get(key), value)
        return merged
    return deepcopy(override)


def sanitize_persona_overlay(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    overlay: dict[str, Any] = {}
    for key in sorted(EDITABLE_PERSONA_OVERLAY_KEYS):
        if key in value:
            overlay[key] = deepcopy(value[key])
    return overlay


def ignored_persona_overlay_keys(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    allowed = EDITABLE_PERSONA_OVERLAY_KEYS
    return sorted(str(key) for key in value if key not in allowed)


def persona_overlay_has_content(value: Any) -> bool:
    overlay = sanitize_persona_overlay(value)
    for item in overlay.values():
        if item not in ({}, [], "", None):
            return True
    return False


def merge_persona_overlay(base_card: dict[str, Any], overlay: dict[str, Any], *, name: str = "") -> dict[str, Any]:
    sanitized = sanitize_persona_overlay(overlay)
    merged = _deep_merge(base_card, sanitized)
    return normalize_persona_card(merged, name=name or str(base_card.get("name") or ""))


def get_or_create_user_character_profile(session: Session, *, user_id: str, character_id: str) -> UserCharacterProfile:
    row = session.get(UserCharacterProfile, (user_id, character_id))
    if row is None:
        row = UserCharacterProfile(user_id=user_id, character_id=character_id)
        session.add(row)
    return row


def update_user_character_profile(
    profile: UserCharacterProfile,
    *,
    overlay: dict[str, Any],
    source_memory_ids: list[str] | None = None,
) -> dict[str, Any]:
    ignored_keys = ignored_persona_overlay_keys(overlay)
    sanitized = sanitize_persona_overlay(overlay)
    profile.overlay_json = dump_json(sanitized)
    if source_memory_ids is not None:
        profile.source_memory_ids_json = dump_json([str(item).strip() for item in source_memory_ids if str(item).strip()])
    profile.revision = int(profile.revision or 0) + 1
    profile.updated_at = utc_now()
    return {"ignored_keys": ignored_keys, "overlay": sanitized}


def resolve_character_profile(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    character: Character | None = None,
) -> ResolvedCharacterProfile:
    character = character or session.get(Character, character_id)
    display_name = character.name if character is not None and character.name else character_id
    base_card = normalize_persona_card(load_json(character.persona_card_json if character is not None else "{}", {}), name=display_name)
    profile = session.get(UserCharacterProfile, (user_id, character_id))
    overlay = sanitize_persona_overlay(load_json(profile.overlay_json, {}) if profile is not None else {})
    resolved = merge_persona_overlay(base_card, overlay, name=display_name)
    source_memory_ids = load_json(profile.source_memory_ids_json, []) if profile is not None else []
    return ResolvedCharacterProfile(
        user_id=user_id,
        character_id=character_id,
        character_name=display_name,
        base_card=base_card,
        overlay=overlay,
        card=resolved,
        source_memory_ids=[str(item) for item in source_memory_ids if str(item).strip()],
        revision=int(profile.revision or 0) if profile is not None else 0,
        has_overlay=persona_overlay_has_content(overlay),
    )


def user_character_profile_to_out(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    include_cards: bool = True,
) -> dict[str, Any]:
    profile = get_or_create_user_character_profile(session, user_id=user_id, character_id=character_id)
    resolved = resolve_character_profile(session, user_id=user_id, character_id=character_id)
    payload = {
        "user_id": user_id,
        "character_id": character_id,
        "overlay": resolved.overlay,
        "source_memory_ids": resolved.source_memory_ids,
        "revision": profile.revision,
        "has_overlay": resolved.has_overlay,
        "editable_keys": sorted(EDITABLE_PERSONA_OVERLAY_KEYS),
        "immutable_keys": sorted(IMMUTABLE_PERSONA_KEYS),
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }
    if include_cards:
        payload["base_card"] = resolved.base_card
        payload["resolved_card"] = resolved.card
    return payload
