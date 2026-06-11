from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Character, MediaAsset, ProviderConfig
from .providers import ImageProvider, ProviderError
from .utils import stable_hash


ImageKind = Literal["scenery", "object_pet", "character_selfie"]

SAFE_IMAGE_KINDS: tuple[ImageKind, ...] = ("scenery", "object_pet", "character_selfie")
SELFIE_ASSET_TYPE = "character_selfie"
SAFE_IMAGE_ASSET_TYPE = "experience_cg"
MANUAL_IMAGE_COOLDOWN_SECONDS = 10 * 60
DAILY_SELFIE_COOLDOWN_SECONDS = 6 * 60 * 60

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SELFIE_REFERENCE_PATH = ROOT_DIR / "assets" / "Live2D" / "NEKO" / "selfie.png"

STYLE_LINE = (
    "Consistent high-quality anime visual novel CG style, soft clean line art, gentle pastel lighting, "
    "tasteful composition, no text, no watermark, no logo."
)

SELFIE_CHARACTER_LINE = (
    "Use the provided reference image for the same fictional adult anime catgirl character: white short bob hair, "
    "white cat ears, white tail, pale gray-blue eyes, black hairpins, small bell choker, and a loose white long-sleeve outfit."
)

SCENERY_HINTS = (
    "风景",
    "景色",
    "天空",
    "云",
    "雨",
    "雪",
    "樱花",
    "街",
    "教室",
    "窗",
    "海",
    "山",
    "庭院",
    "landscape",
    "scenery",
    "sky",
    "street",
    "classroom",
    "garden",
)

OBJECT_PET_HINTS = (
    "物品",
    "书",
    "茶",
    "杯",
    "蛋糕",
    "花",
    "书签",
    "宠物",
    "猫",
    "狗",
    "小动物",
    "object",
    "still life",
    "pet",
    "cat",
    "dog",
)

SELFIE_HINTS = (
    "自拍",
    "自撮",
    "她",
    "角色",
    "本人",
    "亚托莉",
    "亚托莉",
    "猫耳",
    "selfie",
    "portrait",
    "character",
    "catgirl",
)

class ImageRequestBlocked(ProviderError):
    pass


@dataclass(frozen=True)
class SafeImageRequest:
    kind: ImageKind
    prompt: str
    asset_type: str
    source_event_id: str
    reference_image_path: Path | None = None
    reference_image_ids: list[str] | None = None


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u3000", " ")).strip()


def sanitize_scene_hint(value: str, *, max_length: int = 140) -> str:
    cleaned = _normalized_text(value)
    return cleaned[:max_length] if cleaned else ""


def normalize_image_kind(value: str) -> ImageKind:
    kind = str(value or "").strip().lower().replace("-", "_")
    if kind in {"scene", "landscape", "background", "scenery"}:
        return "scenery"
    if kind in {"object", "pet", "object_pet", "still_life"}:
        return "object_pet"
    if kind in {"selfie", "character", "character_selfie", "portrait"}:
        return "character_selfie"
    raise ImageRequestBlocked("Unsupported image kind")


def infer_image_kind(scene_hint: str, *, allow_selfie: bool = True) -> ImageKind:
    lower = scene_hint.lower()
    if allow_selfie and any(item in lower for item in SELFIE_HINTS):
        return "character_selfie"
    if any(item in lower for item in OBJECT_PET_HINTS):
        return "object_pet"
    if any(item in lower for item in SCENERY_HINTS):
        return "scenery"
    return "scenery"


def character_selfie_reference_path(character_id: str) -> Path | None:
    if character_id in {"atri", "sakura", "neko"} and DEFAULT_SELFIE_REFERENCE_PATH.exists():
        return DEFAULT_SELFIE_REFERENCE_PATH
    return DEFAULT_SELFIE_REFERENCE_PATH if DEFAULT_SELFIE_REFERENCE_PATH.exists() else None


def build_safe_image_request(
    *,
    kind: ImageKind,
    scene_hint: str,
    character: Character | None,
    user_id: str,
    character_id: str,
    source_id: str,
    mood: str = "",
) -> SafeImageRequest:
    hint = sanitize_scene_hint(scene_hint)
    character_name = character.name if character is not None and character.name else character_id
    source_key = stable_hash("safe_image", user_id, character_id, source_id, kind)[:16]
    mood_line = f"Mood: {sanitize_scene_hint(mood, max_length=40)}." if mood else ""

    if kind == "scenery":
        prompt = "\n".join(
            [
                STYLE_LINE,
                "Generate a scenery or background image only. No people, no character portrait, no real-person likeness.",
                f"Scene: {hint or 'a quiet everyday place connected to the character diary'}.\n{mood_line}",
            ]
        ).strip()
        return SafeImageRequest(kind=kind, prompt=prompt, asset_type=SAFE_IMAGE_ASSET_TYPE, source_event_id=f"image:{user_id}:{character_id}:{source_key}")

    if kind == "object_pet":
        prompt = "\n".join(
            [
                STYLE_LINE,
                "Generate a still-life, object, food, flower, small prop, or cute pet photo-like image in anime CG style. No humans.",
                f"Subject: {hint or 'a small meaningful daily object'}.\n{mood_line}",
            ]
        ).strip()
        return SafeImageRequest(kind=kind, prompt=prompt, asset_type=SAFE_IMAGE_ASSET_TYPE, source_event_id=f"image:{user_id}:{character_id}:{source_key}")

    reference_path = character_selfie_reference_path(character_id)
    if reference_path is None:
        raise ImageRequestBlocked("Character selfie reference image is not configured")
    prompt = "\n".join(
        [
            STYLE_LINE,
            SELFIE_CHARACTER_LINE,
            f"Generate a safe everyday smartphone selfie by {character_name}.",
            "Framing: upper-body or waist-up, natural expression, stylish anime selfie composition.",
            "Keep her as the same fictional adult anime character from the reference image.",
            f"Scene: {hint or 'a calm daily moment'}.\n{mood_line}",
        ]
    ).strip()
    return SafeImageRequest(
        kind=kind,
        prompt=prompt,
        asset_type=SELFIE_ASSET_TYPE,
        source_event_id=f"selfie:{user_id}:{character_id}:{source_key}",
        reference_image_path=reference_path,
        reference_image_ids=[f"local:{reference_path.name}"],
    )


def _parse_created_at(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def recent_generated_image(
    session: Session,
    *,
    source_prefix: str,
    asset_type: str,
    cooldown_seconds: int,
) -> MediaAsset | None:
    if cooldown_seconds <= 0:
        return None
    row = (
        session.execute(
            select(MediaAsset)
            .where(MediaAsset.asset_type == asset_type, MediaAsset.source_event_id.like(f"{source_prefix}%"))
            .order_by(MediaAsset.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if row is None:
        return None
    created_at = _parse_created_at(row.created_at)
    if created_at is None:
        return None
    if datetime.now(timezone.utc) - created_at <= timedelta(seconds=cooldown_seconds):
        return row
    return None


def generate_safe_image(
    session: Session,
    *,
    config: ProviderConfig,
    kind: ImageKind,
    scene_hint: str,
    character: Character | None,
    user_id: str,
    character_id: str,
    source_id: str,
    mood: str = "",
    cooldown_seconds: int = 0,
) -> MediaAsset:
    request = build_safe_image_request(
        kind=kind,
        scene_hint=scene_hint,
        character=character,
        user_id=user_id,
        character_id=character_id,
        source_id=source_id,
        mood=mood,
    )
    if request.kind == "character_selfie" and config.provider != "doubao_seedream":
        raise ImageRequestBlocked("Character selfie generation requires the Doubao Seedream provider in this backend")
    prefix = f"selfie:{user_id}:{character_id}:" if request.kind == "character_selfie" else f"image:{user_id}:{character_id}:"
    recent = recent_generated_image(
        session,
        source_prefix=prefix,
        asset_type=request.asset_type,
        cooldown_seconds=cooldown_seconds,
    )
    if recent is not None:
        raise ImageRequestBlocked(f"Image generation is cooling down; recent asset: {recent.asset_id}")
    asset = ImageProvider(config).generate(
        session,
        request.prompt,
        asset_type=request.asset_type,
        source_event_id=request.source_event_id,
        reference_image_path=request.reference_image_path,
        reference_image_ids=request.reference_image_ids or [],
    )
    return asset
