from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .character_profiles import resolve_character_profile
from .database import SessionLocal
from .diagnostics import write_diagnostic
from .live2d_config import DEFAULT_LIVE2D_APPEARANCE_ID, enabled_hit_area_ids, get_hit_area
from .models import Character, MediaAsset, RelationState, TouchReactionPool, User
from .persona import relationship_state_summary, touch_tier_for_affection
from .seed import DEFAULT_CHARACTER_ID
from .pipeline import _active_voice_profile, _tts_for_line, _valid_japanese_tts_text
from .providers import OpenAICompatibleClient, ProviderError, get_task_llm_provider
from .utils import dump_json, load_json, stable_hash, uid, utc_now

TIERS = ("low", "mid", "high")
TTS_COOLDOWN_BUFFER_MS = 300
TOUCH_JSON_MAX_TOKENS = 5120


def _relationship_state_for(session: Session, relation: RelationState, character: Character) -> dict[str, Any]:
    card = resolve_character_profile(session, user_id=relation.user_id, character_id=relation.character_id, character=character).card
    return relationship_state_summary(relation, card)


def affection_tier(affection: int) -> str:
    return touch_tier_for_affection(affection)


def _voice_profile_id(character: Character) -> str:
    return str(character.tts_voice_profile_id or "")


def _requires_japanese_tts(session: Session, user: User, character: Character) -> bool:
    if not user.tts_enabled:
        return False
    voice = _active_voice_profile(session, character)
    return voice is not None and voice.language == "ja"


def _voice_language(session: Session, character: Character) -> str:
    voice = _active_voice_profile(session, character)
    if voice is not None and voice.language:
        return str(voice.language)
    return "zh"


def _normalize_tier_name(tier: str) -> str:
    value = str(tier or "").strip().lower()
    if value in TIERS:
        return value
    return ""


def _resolve_refresh_tiers(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    tier: str = "",
    tiers: list[str] | None = None,
) -> list[str]:
    if tiers:
        resolved = [_normalize_tier_name(item) for item in tiers]
        return [item for item in resolved if item]
    explicit = _normalize_tier_name(tier)
    if explicit:
        return [explicit]
    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one()
    return [affection_tier(relation.affection)]


def _tier_style_guidance(tier: str, flirt_hint: str) -> str:
    if tier == "low":
        return "语气礼貌、略带距离感，不要暧昧，不要撒娇过头。"
    if tier == "mid":
        return "语气自然亲近、关心对方，可轻微害羞，但不要明显暧昧擦边。"
    guidance = "语气更熟络、亲近，可带一点俏皮。"
    if flirt_hint:
        guidance += flirt_hint
    return guidance


def estimate_mp3_duration_ms(local_path: str) -> int:
    path = Path(local_path)
    if not path.exists():
        return 0
    size = path.stat().st_size
    return max(800, int(size * 8 / 128000 * 1000))


def _line_content_hash(text: str, emotion: str) -> str:
    return stable_hash("touch_line", text.strip(), emotion)


def _get_pool(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    hit_area: str,
    tier: str,
    voice_profile_id: str,
) -> TouchReactionPool | None:
    return session.execute(
        select(TouchReactionPool).where(
            TouchReactionPool.user_id == user_id,
            TouchReactionPool.character_id == character_id,
            TouchReactionPool.hit_area == hit_area,
            TouchReactionPool.tier == tier,
            TouchReactionPool.voice_profile_id == voice_profile_id,
        )
    ).scalar_one_or_none()


def _get_shared_pool(
    session: Session,
    *,
    character_id: str,
    hit_area: str,
    tier: str,
    voice_profile_id: str,
    exclude_user_id: str = "",
) -> TouchReactionPool | None:
    query = (
        select(TouchReactionPool)
        .where(
            TouchReactionPool.character_id == character_id,
            TouchReactionPool.hit_area == hit_area,
            TouchReactionPool.tier == tier,
            TouchReactionPool.voice_profile_id == voice_profile_id,
        )
        .order_by(TouchReactionPool.updated_at.desc())
    )
    if exclude_user_id:
        query = query.where(TouchReactionPool.user_id != exclude_user_id)
    return session.execute(query).scalars().first()


def _resolve_pool(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    hit_area: str,
    tier: str,
    voice_profile_id: str,
) -> tuple[TouchReactionPool | None, bool]:
    pool = _get_pool(
        session,
        user_id=user_id,
        character_id=character_id,
        hit_area=hit_area,
        tier=tier,
        voice_profile_id=voice_profile_id,
    )
    if pool is not None and len(load_json(pool.lines_json, [])) > 0:
        return pool, False
    shared = _get_shared_pool(
        session,
        character_id=character_id,
        hit_area=hit_area,
        tier=tier,
        voice_profile_id=voice_profile_id,
        exclude_user_id=user_id,
    )
    if shared is None:
        shared = _get_shared_pool(
            session,
            character_id=character_id,
            hit_area=hit_area,
            tier=tier,
            voice_profile_id=voice_profile_id,
        )
    if shared is not None and len(load_json(shared.lines_json, [])) > 0:
        return shared, True
    return pool, False


def _touch_prompt(
    session: Session,
    character: Character,
    relation: RelationState,
    hit_area: str,
    tier: str,
    *,
    appearance_id: str,
    area_label: str = "",
    requires_japanese_tts: bool = False,
    retry_reason: str = "",
) -> str:
    flirt_hint = ""
    area = get_hit_area(session, appearance_id=appearance_id, area_id=hit_area)
    if tier == "high" and (area is None or area.flirt_hint):
        flirt_hint = " 若部位适合，可轻微暧昧擦边，但仍遵守角色边界，不要露骨。"
    label = area_label or (area.label if area is not None else hit_area)
    tier_guidance = _tier_style_guidance(tier, flirt_hint)
    line_schema = (
        '{"text": "...", "tts_text_ja": "...", "emotion": "happy|shy|thinking|calm|sad", '
        '"expression": "happy|shy|thinking|calm|sad|angry", "motion": "TapHead|TapChest|TapHand|TapBody|StepBack|idle"}'
        if requires_japanese_tts
        else '{"text": "...", "emotion": "happy|shy|thinking|calm|sad", '
        '"expression": "happy|shy|thinking|calm|sad|angry", "motion": "TapHead|TapChest|TapHand|TapBody|StepBack|idle"}'
    )
    ja_note = ""
    if requires_japanese_tts:
        ja_note = (
            "\n当前为日文配音声线：每条 lines 必须包含 tts_text_ja（自然口语日文，用于配音）；"
            "text 仍写中文供界面显示，不要把中文写进 tts_text_ja。"
        )
    retry_block = ""
    if retry_reason:
        retry_block = f"\n\n上一次输出未通过校验：{retry_reason}。请重新生成完整 JSON。"
    relation_state = _relationship_state_for(session, relation, character)
    mood = relation_state["mood"]
    affection = relation_state["affection"]
    return f"""你是 Galgame 角色 {character.name}，用户刚刚触摸了你的 {label}（{hit_area}）。
当前关系：好感 {relation.affection}，信任 {relation.trust}，依赖 {relation.dependency}，心情 {relation.mood}。
好感阶段：{affection['label']}({affection['score']}/1000)，触摸档位：{tier}。
当前心情：{mood['label']}({mood['score']}/100)，{mood['visible_hint']}
关系边界提示：{relation_state['boundary_hint']}

【本档语气】
{tier_guidance}

【说话风格】
{character.speech_style}

【边界】
{character.relationship_boundary}

请生成 3 到 4 条中文短台词，像真实互动，不要英文，不要 Markdown。{ja_note}
输出 JSON：
{{
  "lines": [
    {line_schema}
  ]
}}
relation_delta 必须全为 0，不要改变任何数值。{retry_block}"""


def _attach_tts_to_line(
    session: Session,
    user: User,
    character: Character,
    line: dict[str, Any],
    *,
    voice_profile_id: str,
) -> dict[str, Any]:
    text = str(line.get("text") or "").strip()
    emotion = str(line.get("emotion") or "calm").strip() or "calm"
    content_hash = _line_content_hash(text, emotion)
    enriched = dict(line)
    enriched["content_hash"] = content_hash
    enriched["voice_profile_id"] = voice_profile_id
    if not user.tts_enabled or not text:
        enriched["tts_audio_url"] = ""
        enriched["tts_audio_asset_id"] = ""
        enriched["tts_duration_ms"] = 0
        return enriched
    tts_text_ja = str(line.get("tts_text_ja") or "").strip() or None
    try:
        tts_url, tts_error = _tts_for_line(session, user, character, text, emotion, tts_text_ja=tts_text_ja)
        enriched["tts_audio_url"] = tts_url
        enriched["tts_error"] = tts_error
        asset_id = ""
        duration_ms = 0
        if tts_url.startswith("/media/"):
            asset_id = tts_url.rsplit("/", 1)[-1]
            asset = session.get(MediaAsset, asset_id)
            if asset is not None and asset.local_path:
                duration_ms = estimate_mp3_duration_ms(asset.local_path)
        enriched["tts_audio_asset_id"] = asset_id
        enriched["tts_duration_ms"] = duration_ms
    except Exception as exc:  # noqa: BLE001
        enriched["tts_audio_url"] = ""
        enriched["tts_audio_asset_id"] = ""
        enriched["tts_duration_ms"] = 0
        enriched["tts_error"] = str(exc)
        write_diagnostic("touch_line_tts_error", hit_area=line.get("hit_area", ""), message=str(exc))
    return enriched


def _validate_touch_line(item: dict[str, Any], *, requires_japanese_tts: bool) -> str | None:
    text = str(item.get("text") or "").strip()
    if not text:
        return "存在空 text"
    if not requires_japanese_tts:
        return None
    ja = str(item.get("tts_text_ja") or item.get("tts_text") or item.get("ja") or "").strip()
    if not _valid_japanese_tts_text(text, ja):
        return "存在缺少或不合格的 tts_text_ja"
    return None


def _generate_pool_lines(
    session: Session,
    user: User,
    character: Character,
    relation: RelationState,
    hit_area: str,
    tier: str,
    *,
    appearance_id: str,
    synthesize_tts: bool = True,
) -> list[dict[str, Any]]:
    config = get_task_llm_provider(session)
    if config is None:
        raise ProviderError("LLM provider not configured for touch reactions")
    client = OpenAICompatibleClient(config)
    area = get_hit_area(session, appearance_id=appearance_id, area_id=hit_area)
    requires_japanese_tts = _requires_japanese_tts(session, user, character)
    result: dict[str, Any] = {}
    retry_reason = ""
    for _attempt in range(1, 3):
        result = client.chat_json(
            [
                {"role": "system", "content": "你是 Galgame 触摸反应 JSON 生成器。"},
                {
                    "role": "user",
                    "content": _touch_prompt(
                        session,
                        character,
                        relation,
                        hit_area,
                        tier,
                        appearance_id=appearance_id,
                        area_label=area.label if area else "",
                        requires_japanese_tts=requires_japanese_tts,
                        retry_reason=retry_reason,
                    ),
                },
            ],
            max_tokens=TOUCH_JSON_MAX_TOKENS,
            diagnostic={
                "feature": "触摸语音",
                "stage": "touch_pool",
                "purpose": "Generate touch reaction pool lines",
            },
        )
        problems: list[str] = []
        for index, item in enumerate(result.get("lines") or []):
            if not isinstance(item, dict):
                problems.append(f"lines[{index}] 不是对象")
                continue
            issue = _validate_touch_line(item, requires_japanese_tts=requires_japanese_tts)
            if issue:
                problems.append(f"lines[{index}] {issue}")
        if not problems:
            break
        retry_reason = "；".join(problems)
    else:
        raise ProviderError(f"touch reaction JSON invalid: {retry_reason}")

    voice_id = _voice_profile_id(character)
    lines: list[dict[str, Any]] = []
    for item in result.get("lines") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        base = {
            "text": text,
            "emotion": str(item.get("emotion") or "calm").strip() or "calm",
            "expression": str(item.get("expression") or item.get("emotion") or "calm").strip() or "calm",
            "motion": str(item.get("motion") or (area.default_motion if area else "")).strip(),
            "line_id": uid("touchline"),
        }
        if requires_japanese_tts:
            base["tts_text_ja"] = str(item.get("tts_text_ja") or item.get("tts_text") or item.get("ja") or "").strip()
        if synthesize_tts and user.tts_enabled:
            base = _attach_tts_to_line(session, user, character, base, voice_profile_id=voice_id)
        else:
            base["content_hash"] = _line_content_hash(text, base["emotion"])
            base["tts_audio_url"] = ""
            base["tts_duration_ms"] = 0
        lines.append(base)
    if len(lines) < 2:
        raise ProviderError("touch reaction pool too short")
    return lines[:4]


def _refresh_line_tts_only(
    session: Session,
    user: User,
    character: Character,
    line: dict[str, Any],
    *,
    voice_profile_id: str,
) -> dict[str, Any]:
    refreshed = _attach_tts_to_line(session, user, character, line, voice_profile_id=voice_profile_id)
    refreshed["line_id"] = str(line.get("line_id") or uid("touchline"))
    return refreshed


def _store_pool(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    hit_area: str,
    tier: str,
    voice_profile_id: str,
    lines: list[dict[str, Any]],
) -> TouchReactionPool:
    pool = _get_pool(
        session,
        user_id=user_id,
        character_id=character_id,
        hit_area=hit_area,
        tier=tier,
        voice_profile_id=voice_profile_id,
    )
    if pool is None:
        pool = TouchReactionPool(
            pool_id=uid("touchpool"),
            user_id=user_id,
            character_id=character_id,
            hit_area=hit_area,
            tier=tier,
            voice_profile_id=voice_profile_id,
            lines_json=dump_json(lines),
            consumed_indices_json="[]",
            updated_at=utc_now(),
        )
        session.add(pool)
    else:
        pool.lines_json = dump_json(lines)
        pool.consumed_indices_json = "[]"
        pool.updated_at = utc_now()
    session.commit()
    write_diagnostic(
        "touch_pool_refreshed",
        user_id=user_id,
        character_id=character_id,
        hit_area=hit_area,
        tier=tier,
        voice_profile_id=voice_profile_id,
        line_count=len(lines),
    )
    return pool


def _ensure_pool(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    appearance_id: str,
    hit_area: str,
    tier: str,
    voice_profile_id: str,
    force_regenerate: bool = False,
) -> TouchReactionPool:
    pool = _get_pool(
        session,
        user_id=user_id,
        character_id=character_id,
        hit_area=hit_area,
        tier=tier,
        voice_profile_id=voice_profile_id,
    )
    if not force_regenerate and pool is not None and len(load_json(pool.lines_json, [])) >= 2:
        return pool

    user = session.get(User, user_id)
    character = session.get(Character, character_id)
    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one()
    if user is None or character is None:
        raise ProviderError("user or character missing")

    session.commit()
    lines = _generate_pool_lines(
        session,
        user,
        character,
        relation,
        hit_area,
        tier,
        appearance_id=appearance_id,
    )
    return _store_pool(
        session,
        user_id=user_id,
        character_id=character_id,
        hit_area=hit_area,
        tier=tier,
        voice_profile_id=voice_profile_id,
        lines=lines,
    )


def _validate_hit_area(session: Session, *, appearance_id: str, hit_area: str) -> None:
    allowed = set(enabled_hit_area_ids(session, appearance_id=appearance_id))
    if hit_area not in allowed:
        raise ProviderError(f"unsupported hit_area: {hit_area}")


def touch_pool_version(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    appearance_id: str = "",
) -> str:
    character = session.get(Character, character_id)
    voice_profile_id = _voice_profile_id(character) if character is not None else ""
    pools = session.execute(
        select(TouchReactionPool).where(
            TouchReactionPool.user_id == user_id,
            TouchReactionPool.character_id == character_id,
        )
    ).scalars().all()
    hit_areas = enabled_hit_area_ids(session, appearance_id=appearance_id)
    snapshot = {
        "character_id": character_id,
        "appearance_id": appearance_id or DEFAULT_LIVE2D_APPEARANCE_ID,
        "voice_profile_id": voice_profile_id,
        "hit_areas": sorted(hit_areas),
        "pools": sorted(
            (
                {
                    "hit_area": pool.hit_area,
                    "tier": pool.tier,
                    "updated_at": pool.updated_at,
                    "line_count": len(load_json(pool.lines_json, [])),
                }
                for pool in pools
            ),
            key=lambda item: (item["hit_area"], item["tier"]),
        ),
    }
    return hashlib.sha1(dump_json(snapshot).encode("utf-8")).hexdigest()


def _cooldown_ms(area_base: int, tts_duration_ms: int) -> int:
    return max(area_base, int(tts_duration_ms) + TTS_COOLDOWN_BUFFER_MS)


def _line_response(line: dict[str, Any], *, hit_area: str, tier: str, base_cooldown_ms: int) -> dict[str, Any]:
    tts_duration_ms = int(line.get("tts_duration_ms") or 0)
    return {
        "ok": True,
        "hit_area": hit_area,
        "tier": tier,
        "line_id": str(line.get("line_id") or ""),
        "content_hash": str(line.get("content_hash") or ""),
        "text": line.get("text", ""),
        "emotion": line.get("emotion", "calm"),
        "expression": line.get("expression", "calm"),
        "motion": line.get("motion", ""),
        "tts_audio_url": line.get("tts_audio_url", ""),
        "tts_duration_ms": tts_duration_ms,
        "cooldown_ms": _cooldown_ms(base_cooldown_ms, tts_duration_ms),
    }


def refresh_touch_reaction_pools(
    session: Session,
    *,
    user_id: str,
    character_id: str = DEFAULT_CHARACTER_ID,
    appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID,
    hit_area: str = "",
    tier: str = "",
    tiers: list[str] | None = None,
    force: bool = False,
    tts_only: bool = False,
) -> dict[str, Any]:
    from .live2d_config import ensure_default_hit_areas

    ensure_default_hit_areas(session, appearance_id=appearance_id)
    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one()
    character = session.get(Character, character_id)
    user = session.get(User, user_id)
    if character is None or user is None:
        raise ProviderError("user or character missing")
    active_tier = affection_tier(relation.affection)
    target_tiers = _resolve_refresh_tiers(
        session,
        user_id=user_id,
        character_id=character_id,
        tier=tier,
        tiers=tiers,
    )
    voice_profile_id = _voice_profile_id(character)
    hit_areas = [hit_area] if hit_area else enabled_hit_area_ids(session, appearance_id=appearance_id)
    refreshed = 0
    needs_text_refresh = False
    needs_tts_refresh = False
    tiers_refreshed: list[str] = []
    for tier_name in target_tiers:
        tier_touched = False
        for area_id in hit_areas:
            if not area_id:
                continue
            pool = _get_pool(
                session,
                user_id=user_id,
                character_id=character_id,
                hit_area=area_id,
                tier=tier_name,
                voice_profile_id=voice_profile_id,
            )
            if tts_only and pool is not None and not force:
                lines = load_json(pool.lines_json, [])
                updated: list[dict[str, Any]] = []
                changed = False
                for line in lines:
                    if str(line.get("voice_profile_id") or "") == voice_profile_id and line.get("tts_audio_url"):
                        updated.append(line)
                        continue
                    updated.append(_refresh_line_tts_only(session, user, character, line, voice_profile_id=voice_profile_id))
                    changed = True
                if changed:
                    pool.lines_json = dump_json(updated)
                    pool.updated_at = utc_now()
                    session.commit()
                    needs_tts_refresh = True
                    refreshed += 1
                    tier_touched = True
                continue
            if not force and pool is not None and len(load_json(pool.lines_json, [])) >= 2:
                continue
            _ensure_pool(
                session,
                user_id=user_id,
                character_id=character_id,
                appearance_id=appearance_id,
                hit_area=area_id,
                tier=tier_name,
                voice_profile_id=voice_profile_id,
                force_regenerate=force,
            )
            refreshed += 1
            tier_touched = True
            needs_text_refresh = needs_text_refresh or not tts_only
        if tier_touched and tier_name not in tiers_refreshed:
            tiers_refreshed.append(tier_name)
    return {
        "ok": True,
        "refreshed": refreshed,
        "tier": target_tiers[0] if len(target_tiers) == 1 else "",
        "tiers_refreshed": tiers_refreshed,
        "active_tier": active_tier,
        "hit_areas": [area_id for area_id in hit_areas if area_id],
        "character_id": character_id,
        "appearance_id": appearance_id,
        "voice_profile_id": voice_profile_id,
        "voice_language": _voice_language(session, character),
        "touch_pool_version": touch_pool_version(
            session,
            user_id=user_id,
            character_id=character_id,
            appearance_id=appearance_id,
        ),
        "needs_text_refresh": needs_text_refresh,
        "needs_tts_refresh": needs_tts_refresh or tts_only,
    }


def refresh_touch_reaction_pools_background(*, user_id: str, character_id: str) -> None:
    with SessionLocal() as session:
        try:
            refresh_touch_reaction_pools(session, user_id=user_id, character_id=character_id)
        except Exception as exc:  # noqa: BLE001
            write_diagnostic(
                "touch_pool_refresh_failed",
                user_id=user_id,
                character_id=character_id,
                message=str(exc),
            )


def touch_reaction_bundle(
    session: Session,
    *,
    user_id: str,
    character_id: str = DEFAULT_CHARACTER_ID,
    appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID,
) -> dict[str, Any]:
    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one()
    character = session.get(Character, character_id)
    if character is None:
        raise ProviderError("character missing")
    tier = affection_tier(relation.affection)
    relation_state = _relationship_state_for(session, relation, character)
    voice_profile_id = _voice_profile_id(character)
    areas: dict[str, Any] = {}
    for hit_area in enabled_hit_area_ids(session, appearance_id=appearance_id):
        pool, _shared = _resolve_pool(
            session,
            user_id=user_id,
            character_id=character_id,
            hit_area=hit_area,
            tier=tier,
            voice_profile_id=voice_profile_id,
        )
        user_pool = _get_pool(
            session,
            user_id=user_id,
            character_id=character_id,
            hit_area=hit_area,
            tier=tier,
            voice_profile_id=voice_profile_id,
        )
        area_cfg = get_hit_area(session, appearance_id=appearance_id, area_id=hit_area)
        base_cooldown = area_cfg.base_cooldown_ms if area_cfg is not None else 1400
        lines = load_json(pool.lines_json, []) if pool is not None else []
        consumed = load_json(user_pool.consumed_indices_json, []) if user_pool is not None else []
        areas[hit_area] = {
            "tier": tier,
            "voice_profile_id": voice_profile_id,
            "base_cooldown_ms": base_cooldown,
            "lines": [
                {
                    **_line_response(line, hit_area=hit_area, tier=tier, base_cooldown_ms=base_cooldown),
                    "index": index,
                    "consumed": index in consumed,
                }
                for index, line in enumerate(lines)
            ],
        }
    return {
        "ok": True,
        "character_id": character_id,
        "appearance_id": appearance_id,
        "tier": tier,
        "relationship_state": relation_state,
        "voice_profile_id": voice_profile_id,
        "touch_pool_version": touch_pool_version(
            session,
            user_id=user_id,
            character_id=character_id,
            appearance_id=appearance_id,
        ),
        "areas": areas,
    }


def _pool_admin_payload(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    appearance_id: str,
    hit_area: str,
    tier: str,
    voice_profile_id: str,
) -> dict[str, Any]:
    pool = _get_pool(
        session,
        user_id=user_id,
        character_id=character_id,
        hit_area=hit_area,
        tier=tier,
        voice_profile_id=voice_profile_id,
    )
    area_cfg = get_hit_area(session, appearance_id=appearance_id, area_id=hit_area)
    base_cooldown = area_cfg.base_cooldown_ms if area_cfg is not None else 1400
    lines = load_json(pool.lines_json, []) if pool is not None else []
    consumed = load_json(pool.consumed_indices_json, []) if pool is not None else []
    return {
        "tier": tier,
        "appearance_id": appearance_id,
        "pool_id": pool.pool_id if pool is not None else "",
        "line_count": len(lines),
        "lines": [
            {
                **_line_response(line, hit_area=hit_area, tier=tier, base_cooldown_ms=base_cooldown),
                "index": index,
                "consumed": index in consumed,
                "tts_text_ja": line.get("tts_text_ja", ""),
            }
            for index, line in enumerate(lines)
        ],
    }


def list_touch_pool_admin(
    session: Session,
    *,
    user_id: str,
    character_id: str = DEFAULT_CHARACTER_ID,
    appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID,
    hit_area: str,
    tier: str = "",
) -> dict[str, Any]:
    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one()
    character = session.get(Character, character_id)
    if character is None:
        raise ProviderError("character missing")
    relation_tier = affection_tier(relation.affection)
    voice_profile_id = _voice_profile_id(character)
    relation_state = _relationship_state_for(session, relation, character)
    requested = str(tier or "").strip().lower()
    if requested == "all":
        grouped = [
            _pool_admin_payload(
                session,
                user_id=user_id,
                character_id=character_id,
                appearance_id=appearance_id,
                hit_area=hit_area,
                tier=tier_name,
                voice_profile_id=voice_profile_id,
            )
            for tier_name in TIERS
        ]
        return {
            "ok": True,
            "view_mode": "all",
            "hit_area": hit_area,
            "character_id": character_id,
            "appearance_id": appearance_id,
            "relation_tier": relation_tier,
            "relationship_state": relation_state,
            "voice_profile_id": voice_profile_id,
            "voice_language": _voice_language(session, character),
            "tiers": grouped,
            "lines": [],
        }

    active_tier = _normalize_tier_name(requested) or relation_tier
    payload = _pool_admin_payload(
        session,
        user_id=user_id,
        character_id=character_id,
        appearance_id=appearance_id,
        hit_area=hit_area,
        tier=active_tier,
        voice_profile_id=voice_profile_id,
    )
    return {
        "ok": True,
        "view_mode": "single",
        "pool_id": payload["pool_id"],
        "hit_area": hit_area,
        "character_id": character_id,
        "appearance_id": appearance_id,
        "tier": active_tier,
        "relation_tier": relation_tier,
        "relationship_state": relation_state,
        "voice_profile_id": voice_profile_id,
        "voice_language": _voice_language(session, character),
        "lines": payload["lines"],
    }


def consume_touch_reaction(
    session: Session,
    *,
    user_id: str,
    character_id: str = DEFAULT_CHARACTER_ID,
    appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID,
    hit_area: str,
) -> dict[str, Any]:
    from .live2d_config import ensure_default_hit_areas

    hit_area = str(hit_area or "").strip().lower()
    ensure_default_hit_areas(session, appearance_id=appearance_id)
    _validate_hit_area(session, appearance_id=appearance_id, hit_area=hit_area)
    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one()
    character = session.get(Character, character_id)
    if character is None:
        raise ProviderError("character missing")
    tier = affection_tier(relation.affection)
    relation_state = _relationship_state_for(session, relation, character)
    voice_profile_id = _voice_profile_id(character)
    area_cfg = get_hit_area(session, appearance_id=appearance_id, area_id=hit_area)
    base_cooldown = area_cfg.base_cooldown_ms if area_cfg is not None else 1400
    pool, shared_readonly = _resolve_pool(
        session,
        user_id=user_id,
        character_id=character_id,
        hit_area=hit_area,
        tier=tier,
        voice_profile_id=voice_profile_id,
    )
    if pool is None:
        raise ProviderError("touch_pool_missing")
    lines: list[dict[str, Any]] = load_json(pool.lines_json, [])
    if len(lines) < 1:
        raise ProviderError("touch_pool_missing")
    user_pool = _get_pool(
        session,
        user_id=user_id,
        character_id=character_id,
        hit_area=hit_area,
        tier=tier,
        voice_profile_id=voice_profile_id,
    )
    if user_pool is None:
        user_pool = TouchReactionPool(
            pool_id=uid("touchpool"),
            user_id=user_id,
            character_id=character_id,
            hit_area=hit_area,
            tier=tier,
            voice_profile_id=voice_profile_id,
            lines_json=pool.lines_json,
            consumed_indices_json="[]",
            updated_at=utc_now(),
        )
        session.add(user_pool)
        session.flush()
    consumed: list[int] = load_json(user_pool.consumed_indices_json, [])
    available = [index for index in range(len(lines)) if index not in consumed]
    if not available:
        consumed = []
        available = list(range(len(lines)))
    pick = random.choice(available)
    consumed.append(pick)
    user_pool.consumed_indices_json = dump_json(consumed)
    user_pool.updated_at = utc_now()
    session.commit()
    line = lines[pick]
    response = _line_response(line, hit_area=hit_area, tier=tier, base_cooldown_ms=base_cooldown)
    response["relationship_state"] = relation_state
    return response


def touch_pool_coverage_admin(
    session: Session,
    *,
    user_id: str,
    character_id: str = DEFAULT_CHARACTER_ID,
    appearance_id: str = DEFAULT_LIVE2D_APPEARANCE_ID,
) -> dict[str, Any]:
    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one()
    character = session.get(Character, character_id)
    if character is None:
        raise ProviderError("character missing")
    relation_tier = affection_tier(relation.affection)
    voice_profile_id = _voice_profile_id(character)
    relation_state = _relationship_state_for(session, relation, character)
    hit_areas = enabled_hit_area_ids(session, appearance_id=appearance_id)
    matrix: list[dict[str, Any]] = []
    bundle_line_count = 0
    bundle_missing_mp3 = 0
    for hit_area in hit_areas:
        for tier_name in TIERS:
            pool, shared = _resolve_pool(
                session,
                user_id=user_id,
                character_id=character_id,
                hit_area=hit_area,
                tier=tier_name,
                voice_profile_id=voice_profile_id,
            )
            lines = load_json(pool.lines_json, []) if pool is not None else []
            mp3_count = sum(1 for line in lines if str(line.get("tts_audio_url") or "").strip())
            ready = len(lines) >= 2 and mp3_count >= 1
            entry = {
                "hit_area": hit_area,
                "tier": tier_name,
                "line_count": len(lines),
                "mp3_count": mp3_count,
                "ready": ready,
                "shared": shared,
                "pool_id": pool.pool_id if pool is not None else "",
            }
            matrix.append(entry)
            if tier_name == relation_tier:
                bundle_line_count += len(lines)
                bundle_missing_mp3 += max(0, len(lines) - mp3_count)
    return {
        "ok": True,
        "character_id": character_id,
        "appearance_id": appearance_id,
        "user_id": user_id,
        "relation_tier": relation_tier,
        "relationship_state": relation_state,
        "voice_profile_id": voice_profile_id,
        "hit_areas": hit_areas,
        "matrix": matrix,
        "bundle_preview": {
            "tier": relation_tier,
            "line_count": bundle_line_count,
            "missing_mp3": bundle_missing_mp3,
        },
    }
