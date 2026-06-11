from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Live2DHitAreaConfig
from .utils import dump_json, load_json, uid, utc_now

DEFAULT_LIVE2D_APPEARANCE_ID = "neko"
APPEARANCE_IDS = frozenset({"neko", "atri", "murasame"})
APPEARANCE_LABELS: dict[str, str] = {
    "neko": "NEKO Live2D",
    "atri": "亚托莉 立绘",
    "murasame": "美月 立绘",
}
# Backward-compatible alias for appearance storage column semantics.
DEFAULT_CHARACTER_ID = DEFAULT_LIVE2D_APPEARANCE_ID
MODEL_COORD_MIN = -0.5
MODEL_COORD_MAX = 1.5
REPO_ROOT = Path(__file__).resolve().parents[2]
ANDROID_RES_DIR = REPO_ROOT / "android" / "app" / "src" / "main" / "res" / "drawable-nodpi"
ANDROID_ASSETS_DIR = REPO_ROOT / "android" / "app" / "src" / "main" / "assets"
LIVE2D_MODELS_DIR = ANDROID_ASSETS_DIR / "live2d" / "models"

DEFAULT_PLACEMENT: dict[str, float] = {
    "scale": 1.14,
    "offsetX": 0.0,
    "offsetY": -12.0,
    "bottomInset": 34.0,
}

CHARACTER_PREVIEW_META: dict[str, dict[str, Any]] = {
    "neko": {
        "renderer_mode": "live2d",
        "model_url": "/admin/assets/live2d-models/neko/neko.model3.json",
        "default_placement": {
            "scale": 1.10,
            "offsetX": 0.0,
            "offsetY": -10.0,
            "bottomInset": 30.0,
        },
    },
    "atri": {
        "renderer_mode": "static_png",
        "reference_image": "character_atri_idle.png",
        "default_placement": {
            "scale": 1.14,
            "offsetX": 0.0,
            "offsetY": -12.0,
            "bottomInset": 34.0,
        },
    },
    "murasame": {
        "renderer_mode": "static_png",
        "reference_image": "character_murasame_idle.png",
        "default_placement": {
            "scale": 1.08,
            "offsetX": 0.0,
            "offsetY": -6.0,
            "bottomInset": 42.0,
        },
    },
}

DEFAULT_NEKO_HIT_AREAS: list[dict[str, Any]] = [
    {
        "area_id": "head",
        "label": "头",
        "left": 0.38,
        "top": 0.05,
        "right": 0.62,
        "bottom": 0.26,
        "priority": 40,
        "default_motion": "TapHead",
        "default_expression": "happy",
        "base_cooldown_ms": 1200,
        "flirt_hint": False,
        "tap_motions": [{"motion": "TapHead", "weight": 1.0}, {"motion": "idle", "weight": 0.25}],
        "reactions": [
            {"intensity": "soft", "motion": "TapHead", "expression": "happy", "cooldown_ms": 1200},
        ],
    },
    {
        "area_id": "chest",
        "label": "胸",
        "left": 0.43,
        "top": 0.30,
        "right": 0.57,
        "bottom": 0.42,
        "priority": 30,
        "default_motion": "TapChest",
        "default_expression": "shy",
        "base_cooldown_ms": 2200,
        "flirt_hint": True,
        "tap_motions": [{"motion": "TapChest", "weight": 1.0}, {"motion": "StepBack", "weight": 0.35}],
        "reactions": [
            {"intensity": "flirty", "motion": "TapChest", "expression": "shy", "cooldown_ms": 2200},
            {"intensity": "boundary", "motion": "StepBack", "expression": "shy", "cooldown_ms": 2600},
        ],
    },
    {
        "area_id": "hand",
        "label": "手",
        "left": 0.28,
        "top": 0.40,
        "right": 0.72,
        "bottom": 0.66,
        "priority": 25,
        "default_motion": "TapHand",
        "default_expression": "happy",
        "base_cooldown_ms": 1300,
        "flirt_hint": False,
        "tap_motions": [{"motion": "TapHand", "weight": 1.0}, {"motion": "happy", "weight": 0.3}],
        "reactions": [
            {"intensity": "soft", "motion": "TapHand", "expression": "happy", "cooldown_ms": 1300},
        ],
    },
    {
        "area_id": "body",
        "label": "身体",
        "left": 0.34,
        "top": 0.26,
        "right": 0.66,
        "bottom": 0.78,
        "priority": 10,
        "default_motion": "TapBody",
        "default_expression": "thinking",
        "base_cooldown_ms": 1300,
        "flirt_hint": True,
        "tap_motions": [{"motion": "TapBody", "weight": 1.0}, {"motion": "thinking", "weight": 0.3}],
        "reactions": [
            {"intensity": "soft", "motion": "TapBody", "expression": "thinking", "cooldown_ms": 1300},
        ],
    },
]


def _round_coord(value: float, *, digits: int = 2) -> float:
    return round(float(value), digits)


def _resolve_appearance_id(*, appearance_id: str = "", character_id: str = "") -> str:
    value = str(appearance_id or character_id or DEFAULT_LIVE2D_APPEARANCE_ID).strip()
    return value or DEFAULT_LIVE2D_APPEARANCE_ID


def _normalize_area_payload(payload: dict[str, Any]) -> dict[str, Any]:
    left = _round_coord(payload.get("left", 0.0))
    top = _round_coord(payload.get("top", 0.0))
    right = _round_coord(payload.get("right", 1.0))
    bottom = _round_coord(payload.get("bottom", 1.0))
    if right < left:
        left, right = right, left
    if bottom < top:
        top, bottom = bottom, top
    return {
        "area_id": str(payload.get("area_id") or "").strip().lower(),
        "appearance_id": _resolve_appearance_id(
            appearance_id=str(payload.get("appearance_id") or ""),
            character_id=str(payload.get("character_id") or ""),
        ),
        "label": str(payload.get("label") or payload.get("area_id") or "").strip(),
        "left": _round_coord(max(MODEL_COORD_MIN, min(MODEL_COORD_MAX, left))),
        "top": _round_coord(max(MODEL_COORD_MIN, min(MODEL_COORD_MAX, top))),
        "right": _round_coord(max(MODEL_COORD_MIN, min(MODEL_COORD_MAX, right))),
        "bottom": _round_coord(max(MODEL_COORD_MIN, min(MODEL_COORD_MAX, bottom))),
        "priority": int(payload.get("priority", 0)),
        "enabled": bool(payload.get("enabled", True)),
        "default_motion": str(payload.get("default_motion") or "idle").strip() or "idle",
        "default_expression": str(payload.get("default_expression") or "calm").strip() or "calm",
        "base_cooldown_ms": int(payload.get("base_cooldown_ms", 1400)),
        "tap_motions": payload.get("tap_motions") if isinstance(payload.get("tap_motions"), list) else load_json(str(payload.get("tap_motions_json") or "[]"), []),
        "reactions": payload.get("reactions") if isinstance(payload.get("reactions"), list) else load_json(str(payload.get("reactions_json") or "[]"), []),
        "flirt_hint": bool(payload.get("flirt_hint", False)),
    }


def area_to_dict(row: Live2DHitAreaConfig) -> dict[str, Any]:
    return {
        "area_id": row.area_id,
        "id": row.area_id,
        "appearance_id": row.character_id,
        "character_id": row.character_id,
        "label": row.label,
        "left": _round_coord(row.left),
        "top": _round_coord(row.top),
        "right": _round_coord(row.right),
        "bottom": _round_coord(row.bottom),
        "priority": row.priority,
        "enabled": row.enabled,
        "default_motion": row.default_motion,
        "default_expression": row.default_expression,
        "base_cooldown_ms": row.base_cooldown_ms,
        "tap_motions": load_json(row.tap_motions_json, []),
        "reactions": load_json(row.reactions_json, []),
        "flirt_hint": row.flirt_hint,
        "updated_at": row.updated_at,
    }


def bootstrap_area_dict(row: Live2DHitAreaConfig) -> dict[str, Any]:
    item = area_to_dict(row)
    item["tap_motions"] = [
        {"hit_area": row.area_id, "motion": str(entry.get("motion") or ""), "weight": float(entry.get("weight", 1.0))}
        for entry in item["tap_motions"]
        if str(entry.get("motion") or "").strip()
    ]
    reactions: list[dict[str, Any]] = []
    for entry in item["reactions"]:
        reactions.append(
            {
                "hit_area": row.area_id,
                "intensity": str(entry.get("intensity") or "soft"),
                "motion": str(entry.get("motion") or row.default_motion),
                "expression": str(entry.get("expression") or row.default_expression),
                "cooldown_ms": int(entry.get("cooldown_ms", row.base_cooldown_ms)),
            }
        )
    if not reactions:
        reactions.append(
            {
                "hit_area": row.area_id,
                "intensity": "soft",
                "motion": row.default_motion,
                "expression": row.default_expression,
                "cooldown_ms": row.base_cooldown_ms,
            }
        )
    item["reactions"] = reactions
    return item


def list_hit_areas(
    session: Session,
    *,
    appearance_id: str = "",
    character_id: str = "",
    enabled_only: bool = False,
) -> list[Live2DHitAreaConfig]:
    resolved = _resolve_appearance_id(appearance_id=appearance_id, character_id=character_id)
    query = select(Live2DHitAreaConfig).where(Live2DHitAreaConfig.character_id == resolved)
    if enabled_only:
        query = query.where(Live2DHitAreaConfig.enabled.is_(True))
    rows = session.execute(query.order_by(Live2DHitAreaConfig.priority.desc(), Live2DHitAreaConfig.area_id)).scalars().all()
    return list(rows)


def enabled_hit_area_ids(session: Session, *, appearance_id: str = "", character_id: str = "") -> list[str]:
    return [row.area_id for row in list_hit_areas(session, appearance_id=appearance_id, character_id=character_id, enabled_only=True)]


def get_hit_area(
    session: Session,
    *,
    area_id: str,
    appearance_id: str = "",
    character_id: str = "",
) -> Live2DHitAreaConfig | None:
    resolved = _resolve_appearance_id(appearance_id=appearance_id, character_id=character_id)
    return session.get(Live2DHitAreaConfig, (area_id, resolved))


def ensure_default_hit_areas(session: Session, *, appearance_id: str = "", character_id: str = "") -> None:
    resolved = _resolve_appearance_id(appearance_id=appearance_id, character_id=character_id)
    existing = session.execute(
        select(Live2DHitAreaConfig.area_id).where(Live2DHitAreaConfig.character_id == resolved)
    ).scalars().all()
    if existing:
        return
    if resolved not in APPEARANCE_IDS:
        return
    now = utc_now()
    for item in DEFAULT_NEKO_HIT_AREAS:
        session.add(
            Live2DHitAreaConfig(
                area_id=item["area_id"],
                character_id=resolved,
                label=item["label"],
                left=item["left"],
                top=item["top"],
                right=item["right"],
                bottom=item["bottom"],
                priority=item["priority"],
                enabled=True,
                default_motion=item["default_motion"],
                default_expression=item["default_expression"],
                base_cooldown_ms=item["base_cooldown_ms"],
                tap_motions_json=dump_json(item["tap_motions"]),
                reactions_json=dump_json(item["reactions"]),
                flirt_hint=item["flirt_hint"],
                created_at=now,
                updated_at=now,
            )
        )


def create_hit_area(session: Session, payload: dict[str, Any]) -> Live2DHitAreaConfig:
    data = _normalize_area_payload(payload)
    if not data["area_id"]:
        raise ValueError("area_id is required")
    appearance_id = data["appearance_id"]
    if get_hit_area(session, appearance_id=appearance_id, area_id=data["area_id"]) is not None:
        raise ValueError("area_id already exists")
    now = utc_now()
    row = Live2DHitAreaConfig(
        area_id=data["area_id"],
        character_id=appearance_id,
        label=data["label"] or data["area_id"],
        left=data["left"],
        top=data["top"],
        right=data["right"],
        bottom=data["bottom"],
        priority=data["priority"],
        enabled=data["enabled"],
        default_motion=data["default_motion"],
        default_expression=data["default_expression"],
        base_cooldown_ms=data["base_cooldown_ms"],
        tap_motions_json=dump_json(data["tap_motions"]),
        reactions_json=dump_json(data["reactions"]),
        flirt_hint=data["flirt_hint"],
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return row


def update_hit_area(
    session: Session,
    *,
    area_id: str,
    appearance_id: str = "",
    character_id: str = "",
    payload: dict[str, Any] | None = None,
) -> Live2DHitAreaConfig:
    resolved = _resolve_appearance_id(appearance_id=appearance_id, character_id=character_id)
    row = get_hit_area(session, appearance_id=resolved, area_id=area_id)
    if row is None:
        raise ValueError("hit area not found")
    data = _normalize_area_payload({**area_to_dict(row), **(payload or {}), "area_id": area_id, "appearance_id": resolved})
    row.label = data["label"] or row.area_id
    row.left = data["left"]
    row.top = data["top"]
    row.right = data["right"]
    row.bottom = data["bottom"]
    row.priority = data["priority"]
    row.enabled = data["enabled"]
    row.default_motion = data["default_motion"]
    row.default_expression = data["default_expression"]
    row.base_cooldown_ms = data["base_cooldown_ms"]
    row.tap_motions_json = dump_json(data["tap_motions"])
    row.reactions_json = dump_json(data["reactions"])
    row.flirt_hint = data["flirt_hint"]
    row.updated_at = utc_now()
    session.commit()
    return row


def delete_hit_area(
    session: Session,
    *,
    area_id: str,
    appearance_id: str = "",
    character_id: str = "",
) -> None:
    resolved = _resolve_appearance_id(appearance_id=appearance_id, character_id=character_id)
    row = get_hit_area(session, appearance_id=resolved, area_id=area_id)
    if row is None:
        raise ValueError("hit area not found")
    session.delete(row)
    session.commit()


def reorder_hit_areas(
    session: Session,
    *,
    ordered_area_ids: list[str],
    appearance_id: str = "",
    character_id: str = "",
) -> list[Live2DHitAreaConfig]:
    resolved = _resolve_appearance_id(appearance_id=appearance_id, character_id=character_id)
    priority = len(ordered_area_ids) * 10
    rows: list[Live2DHitAreaConfig] = []
    for area_id in ordered_area_ids:
        row = get_hit_area(session, appearance_id=resolved, area_id=area_id)
        if row is None:
            continue
        row.priority = priority
        row.updated_at = utc_now()
        rows.append(row)
        priority -= 10
    session.commit()
    return list_hit_areas(session, appearance_id=resolved)


def default_placement_for_appearance(appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID) -> dict[str, float]:
    meta = CHARACTER_PREVIEW_META.get(appearance_id, {})
    placement = meta.get("default_placement") or DEFAULT_PLACEMENT
    return {
        "scale": float(placement.get("scale", DEFAULT_PLACEMENT["scale"])),
        "offsetX": float(placement.get("offsetX", DEFAULT_PLACEMENT["offsetX"])),
        "offsetY": float(placement.get("offsetY", DEFAULT_PLACEMENT["offsetY"])),
        "bottomInset": float(placement.get("bottomInset", DEFAULT_PLACEMENT["bottomInset"])),
    }


def appearance_renderer_mode(appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID) -> str:
    meta = CHARACTER_PREVIEW_META.get(appearance_id, {})
    return str(meta.get("renderer_mode", "static_png"))


def character_renderer_mode(character_id: str = DEFAULT_LIVE2D_APPEARANCE_ID) -> str:
    return appearance_renderer_mode(character_id)


def default_placement_for_character(character_id: str = DEFAULT_LIVE2D_APPEARANCE_ID) -> dict[str, float]:
    return default_placement_for_appearance(character_id)


def preview_config_for_character(appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID) -> dict[str, Any]:
    meta = CHARACTER_PREVIEW_META.get(appearance_id, {})
    renderer_mode = appearance_renderer_mode(appearance_id)
    config: dict[str, Any] = {
        "appearance_id": appearance_id,
        "character_id": appearance_id,
        "label": APPEARANCE_LABELS.get(appearance_id, appearance_id),
        "renderer_mode": renderer_mode,
        "default_placement": default_placement_for_appearance(appearance_id),
        "stage_width": 480,
        "stage_height": 720,
        "reference_image_url": None,
        "model_url": None,
    }
    if renderer_mode == "live2d":
        config["model_url"] = str(
            meta.get("model_url", f"/admin/assets/live2d-models/{appearance_id}/{appearance_id}.model3.json")
        )
    else:
        path = reference_image_path(appearance_id)
        if path is not None and path.exists():
            config["reference_image_url"] = (
                f"/api/admin/live2d/hit-areas/reference-image?appearance_id={appearance_id}"
            )
    return config


def live2d_bootstrap_payload(
    session: Session,
    *,
    appearance_id: str = "",
    character_id: str = "",
) -> dict[str, Any]:
    resolved = _resolve_appearance_id(appearance_id=appearance_id, character_id=character_id)
    ensure_default_hit_areas(session, appearance_id=resolved)
    session.commit()
    rows = list_hit_areas(session, appearance_id=resolved, enabled_only=True)
    areas = [bootstrap_area_dict(row) for row in rows]
    digest = hashlib.sha1(dump_json(areas).encode("utf-8")).hexdigest()
    return {
        "config_version": digest,
        "appearance_id": resolved,
        "character_id": resolved,
        "label": APPEARANCE_LABELS.get(resolved, resolved),
        "default_placement": default_placement_for_appearance(resolved),
        "hit_areas": areas,
    }


def reference_image_path(appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID) -> Path | None:
    if appearance_renderer_mode(appearance_id) != "static_png":
        return None
    meta = CHARACTER_PREVIEW_META.get(appearance_id, {})
    filename = meta.get("reference_image")
    if not filename:
        return None
    candidates = [
        ANDROID_RES_DIR / str(filename),
        ANDROID_ASSETS_DIR / "standing" / str(filename),
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]
