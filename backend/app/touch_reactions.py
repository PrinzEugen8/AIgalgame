from __future__ import annotations

import random
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import SessionLocal
from .diagnostics import write_diagnostic
from .models import Character, RelationState, TouchReactionPool, User
from .providers import OpenAICompatibleClient, ProviderError, get_task_llm_provider
from .utils import dump_json, load_json, uid, utc_now

HIT_AREAS = ("head", "chest", "hand", "body")
TIERS = ("low", "mid", "high")


def affection_tier(affection: int) -> str:
    if affection >= 70:
        return "high"
    if affection >= 45:
        return "mid"
    return "low"


def _get_pool(session: Session, *, user_id: str, character_id: str, hit_area: str, tier: str) -> TouchReactionPool | None:
    return session.execute(
        select(TouchReactionPool).where(
            TouchReactionPool.user_id == user_id,
            TouchReactionPool.character_id == character_id,
            TouchReactionPool.hit_area == hit_area,
            TouchReactionPool.tier == tier,
        )
    ).scalar_one_or_none()


def _touch_prompt(character: Character, relation: RelationState, hit_area: str, tier: str) -> str:
    flirt_hint = ""
    if hit_area in {"chest", "body"} and tier == "high":
        flirt_hint = "高好感时允许轻微暧昧擦边，但仍遵守角色边界，不要露骨。"
    return f"""你是 Galgame 角色 {character.name}，用户刚刚触摸了你的 {hit_area} 部位。
当前关系：好感 {relation.affection}，信任 {relation.trust}，依赖 {relation.dependency}，心情 {relation.mood}。
好感档位：{tier}。{flirt_hint}

【说话风格】
{character.speech_style}

【边界】
{character.relationship_boundary}

请生成 3 到 4 条中文短台词，像真实互动，不要英文，不要 Markdown。
输出 JSON：
{{
  "lines": [
    {{"text": "...", "emotion": "happy|shy|thinking|calm|sad", "expression": "happy|shy|thinking|calm|sad|angry", "motion": "TapHead|TapChest|TapHand|TapBody|StepBack|idle"}}
  ]
}}
relation_delta 必须全为 0，不要改变任何数值。"""


def _generate_pool_lines(session: Session, user: User, character: Character, relation: RelationState, hit_area: str, tier: str) -> list[dict[str, Any]]:
    config = get_task_llm_provider(session)
    if config is None:
        raise ProviderError("LLM provider not configured for touch reactions")
    client = OpenAICompatibleClient(config)
    result = client.chat_json(
        [{"role": "system", "content": "你是 Galgame 触摸反应 JSON 生成器。"}, {"role": "user", "content": _touch_prompt(character, relation, hit_area, tier)}],
        max_tokens=700,
    )
    lines: list[dict[str, Any]] = []
    for item in result.get("lines") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        lines.append(
            {
                "text": text,
                "emotion": str(item.get("emotion") or "calm").strip() or "calm",
                "expression": str(item.get("expression") or item.get("emotion") or "calm").strip() or "calm",
                "motion": str(item.get("motion") or "").strip(),
            }
        )
    if len(lines) < 2:
        raise ProviderError("touch reaction pool too short")
    return lines[:4]


def _store_pool(
    session: Session,
    *,
    user_id: str,
    character_id: str,
    hit_area: str,
    tier: str,
    lines: list[dict[str, Any]],
) -> TouchReactionPool:
    pool = _get_pool(session, user_id=user_id, character_id=character_id, hit_area=hit_area, tier=tier)
    if pool is None:
        pool = TouchReactionPool(
            pool_id=uid("touchpool"),
            user_id=user_id,
            character_id=character_id,
            hit_area=hit_area,
            tier=tier,
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
    write_diagnostic("touch_pool_refreshed", user_id=user_id, character_id=character_id, hit_area=hit_area, tier=tier, line_count=len(lines))
    return pool


def _ensure_pool(session: Session, *, user_id: str, character_id: str, hit_area: str, tier: str) -> TouchReactionPool:
    pool = _get_pool(session, user_id=user_id, character_id=character_id, hit_area=hit_area, tier=tier)
    if pool is not None and len(load_json(pool.lines_json, [])) >= 2:
        return pool

    user = session.get(User, user_id)
    character = session.get(Character, character_id)
    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one()
    if user is None or character is None:
        raise ProviderError("user or character missing")

    # Release DB write lock before slow LLM call.
    session.commit()
    lines = _generate_pool_lines(session, user, character, relation, hit_area, tier)
    return _store_pool(session, user_id=user_id, character_id=character_id, hit_area=hit_area, tier=tier, lines=lines)


def refresh_touch_reaction_pools(session: Session, *, user_id: str, character_id: str) -> dict[str, Any]:
    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one()
    tier = affection_tier(relation.affection)
    refreshed = 0
    for hit_area in HIT_AREAS:
        pool = _get_pool(session, user_id=user_id, character_id=character_id, hit_area=hit_area, tier=tier)
        if pool is not None and len(load_json(pool.lines_json, [])) >= 2:
            continue
        _ensure_pool(session, user_id=user_id, character_id=character_id, hit_area=hit_area, tier=tier)
        refreshed += 1
    return {"ok": True, "refreshed": refreshed, "tier": tier}


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


def consume_touch_reaction(session: Session, *, user_id: str, character_id: str, hit_area: str) -> dict[str, Any]:
    hit_area = str(hit_area or "").strip().lower()
    if hit_area not in HIT_AREAS:
        raise ProviderError(f"unsupported hit_area: {hit_area}")
    relation = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one()
    tier = affection_tier(relation.affection)
    pool = _ensure_pool(session, user_id=user_id, character_id=character_id, hit_area=hit_area, tier=tier)
    lines: list[dict[str, Any]] = load_json(pool.lines_json, [])
    consumed: list[int] = load_json(pool.consumed_indices_json, [])
    available = [index for index in range(len(lines)) if index not in consumed]
    if len(available) < 2:
        pool = _ensure_pool(session, user_id=user_id, character_id=character_id, hit_area=hit_area, tier=tier)
        lines = load_json(pool.lines_json, [])
        consumed = []
        available = list(range(len(lines)))
    pick = random.choice(available)
    consumed.append(pick)
    pool.consumed_indices_json = dump_json(consumed)
    pool.updated_at = utc_now()
    session.commit()
    line = lines[pick]
    return {
        "ok": True,
        "hit_area": hit_area,
        "tier": tier,
        "text": line.get("text", ""),
        "emotion": line.get("emotion", "calm"),
        "expression": line.get("expression", "calm"),
        "motion": line.get("motion", ""),
    }
