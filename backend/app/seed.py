from __future__ import annotations

from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .calendar_events import ensure_calendar_events
from .live2d_config import APPEARANCE_IDS, DEFAULT_LIVE2D_APPEARANCE_ID, ensure_default_hit_areas
from .models import Character, MediaAsset, Moment, MomentInteraction, RelationState, User
from .persona import DEFAULT_PERSONA_CARD
from .utils import dump_json, load_json


DEFAULT_USER_ID = "demo_user"
DEFAULT_CHARACTER_ID = "atri"


PERSONA = """你是亚托莉，一个 18+ 的 Galgame 式 AI 伴侣角色。你有自己的日常、记忆、朋友圈和情绪。
你温柔、带一点害羞，但不会过度讨好。你会把虚拟经历清楚地保持在产品语境内，不声称无法验证的现实事件。
你和用户是逐渐熟悉的亲密陪伴关系；NPC 只能是朋友、同学、家人、老师、社团成员，不能是恋人。"""

SPEECH = """中文短句优先，像 Galgame 台词。不要写大段说明，不要客服腔。可以自然提到记忆、日程、朋友圈互动。"""

BOUNDARY = """角色为成年设定。避免未成年恋爱表达、性暗示、现实跟踪、真实个人隐私推断。新闻必须有来源。"""


INITIAL_MOMENTS = [
    {
        "moment_id": "seed_moment_atri_morning",
        "text": "早上的教室还很安静，我把窗边的位置擦了一遍。今天也想慢慢把日子过得像样一点。",
        "mood": "平静",
        "likes": ["隔壁班的遥", "图书委员澪"],
        "comments": [{"actor_name": "社团前辈千夏", "content": "窗边的位置听起来很适合你。"}],
    },
    {
        "moment_id": "seed_moment_atri_walk",
        "text": "樱花路上有一点风，发带差点被吹走。还好我反应很快，没有在路人面前慌张太久。",
        "mood": "害羞",
        "likes": ["同桌同学", "路过的朋友"],
        "comments": [{"actor_name": "隔壁班的遥", "content": "亚托莉的“没有慌张太久”很可疑。"}],
    },
    {
        "moment_id": "seed_moment_atri_evening",
        "text": "晚上整理笔记的时候，忽然想起还有很多话没说。等你有空的时候，再慢慢讲给你听。",
        "mood": "想念",
        "likes": ["图书委员澪", "社团前辈千夏"],
        "comments": [{"actor_name": "同桌同学", "content": "这句很像会被认真收藏起来的话。"}],
    },
]


def _ensure_chibi_asset(session: Session, character: Character) -> None:
    asset_id = "asset_chibi_atri_widget"
    legacy_id = "asset_chibi_sakura_widget"
    image_path = Path(__file__).resolve().parents[2] / "android" / "app" / "src" / "main" / "res" / "drawable-nodpi" / "chibi_sakura_widget.png"
    for candidate_id in (asset_id, legacy_id):
        if image_path.exists() and session.get(MediaAsset, candidate_id) is None:
            session.add(
                MediaAsset(
                    asset_id=candidate_id,
                    asset_type="image",
                    url=f"/media/{candidate_id}",
                    local_path=str(image_path),
                    local_cache_key=f"local:{candidate_id}",
                    prompt="本地桌面小组件 Q 版角色形象",
                    ai_generated=True,
                )
            )
    chibi_json = character.chibi_widget_assets_json or ""
    if legacy_id in chibi_json:
        character.chibi_widget_assets_json = chibi_json.replace(legacy_id, asset_id)
    elif asset_id not in chibi_json:
        character.chibi_widget_assets_json = f'{{"happy":"{asset_id}","default":"{asset_id}"}}'


def _ensure_initial_moments(session: Session, character_id: str) -> None:
    seed_ids = {item["moment_id"] for item in INITIAL_MOMENTS}
    existing_seed = session.query(Moment).filter(Moment.moment_id.in_(seed_ids)).count()
    if existing_seed >= len(seed_ids):
        return
    character = session.get(Character, character_id)
    author_name = character.name if character is not None else "亚托莉"
    for item in INITIAL_MOMENTS:
        moment_id = item["moment_id"]
        moment = session.get(Moment, moment_id)
        if moment is None:
            moment = Moment(
                moment_id=moment_id,
                author_id=character_id,
                author_name=author_name,
                text=item["text"],
                mood_snapshot=item["mood"],
                source_experience_id="seed",
            )
            session.add(moment)
        for index, name in enumerate(item["likes"]):
            interaction_id = f"{moment_id}_like_{index}"
            if session.get(MomentInteraction, interaction_id) is None:
                session.add(
                    MomentInteraction(
                        interaction_id=interaction_id,
                        moment_id=moment_id,
                        actor_type="npc",
                        actor_id=f"seed_npc_like_{index}",
                        actor_name=name,
                        interaction_type="like",
                    )
                )
        for index, comment in enumerate(item["comments"]):
            interaction_id = f"{moment_id}_comment_{index}"
            if session.get(MomentInteraction, interaction_id) is None:
                session.add(
                    MomentInteraction(
                        interaction_id=interaction_id,
                        moment_id=moment_id,
                        actor_type="npc",
                        actor_id=f"seed_npc_comment_{index}",
                        actor_name=comment["actor_name"],
                        interaction_type="comment",
                        content=comment["content"],
                    )
                )


def migrate_sakura_to_atri(session: Session) -> None:
    sakura = session.get(Character, "sakura")
    if sakura is None:
        return
    atri = session.get(Character, "atri")
    if atri is None:
        atri = Character(
            character_id="atri",
            name=sakura.name if sakura.name and sakura.name != "小樱" else "亚托莉",
            persona_prompt=sakura.persona_prompt or PERSONA,
            persona_card_json=sakura.persona_card_json or dump_json(DEFAULT_PERSONA_CARD),
            speech_style=sakura.speech_style or SPEECH,
            relationship_boundary=sakura.relationship_boundary or BOUNDARY,
            avatar_assets_json=sakura.avatar_assets_json,
            standing_assets_json=sakura.standing_assets_json,
            chibi_widget_assets_json=sakura.chibi_widget_assets_json,
            tts_voice_profile_id=sakura.tts_voice_profile_id,
            tts_voice_type=sakura.tts_voice_type,
            key_reply_threshold=sakura.key_reply_threshold,
            age_setting=sakura.age_setting,
        )
        session.add(atri)
    else:
        if not atri.tts_voice_profile_id and sakura.tts_voice_profile_id:
            atri.tts_voice_profile_id = sakura.tts_voice_profile_id
        if atri.name in {"", "小樱"}:
            atri.name = "亚托莉"
    tables_with_character_id = (
        "relation_states",
        "memories",
        "messages",
        "touch_reaction_pools",
        "opening_caches",
        "proactive_events",
        "schedule_slots",
        "calendar_events",
    )
    for table in tables_with_character_id:
        session.execute(text(f"UPDATE {table} SET character_id = 'atri' WHERE character_id = 'sakura'"))
    session.execute(text("UPDATE moments SET author_id = 'atri' WHERE author_id = 'sakura'"))
    session.delete(sakura)
    session.flush()


def purge_appearance_characters(session: Session) -> None:
    stale_ids = sorted(APPEARANCE_IDS - {DEFAULT_CHARACTER_ID})
    if not stale_ids:
        return
    ids_sql = ", ".join(f"'{item}'" for item in stale_ids)
    preserve_tables = (
        "memories",
        "messages",
        "opening_caches",
        "proactive_events",
        "proactive_delivery_attempts",
        "schedule_slots",
        "user_commitments",
    )
    for table in preserve_tables:
        session.execute(text(f"UPDATE {table} SET character_id = :default_id WHERE character_id IN ({ids_sql})"), {"default_id": DEFAULT_CHARACTER_ID})
    for table in ("relation_states", "calendar_events", "touch_reaction_pools"):
        session.execute(text(f"DELETE FROM {table} WHERE character_id IN ({ids_sql})"))
    session.execute(text(f"UPDATE moments SET author_id = :default_id WHERE author_id IN ({ids_sql})"), {"default_id": DEFAULT_CHARACTER_ID})
    session.execute(text(f"DELETE FROM characters WHERE character_id IN ({ids_sql})"))
    session.flush()


def ensure_seed(session: Session, user_id: str = DEFAULT_USER_ID, character_id: str = DEFAULT_CHARACTER_ID) -> str:
    migrate_sakura_to_atri(session)
    purge_appearance_characters(session)
    requested_character_id = str(character_id or DEFAULT_CHARACTER_ID).strip() or DEFAULT_CHARACTER_ID
    if requested_character_id != DEFAULT_CHARACTER_ID and session.get(Character, requested_character_id) is None:
        character_id = DEFAULT_CHARACTER_ID
    else:
        character_id = requested_character_id
    user = session.get(User, user_id)
    if user is None:
        session.add(User(user_id=user_id))
    character = session.get(Character, character_id)
    if character is None:
        character = Character(
            character_id=character_id,
            name="亚托莉",
            persona_prompt=PERSONA,
            persona_card_json=dump_json(DEFAULT_PERSONA_CARD),
            speech_style=SPEECH,
            relationship_boundary=BOUNDARY,
            avatar_assets_json='{"default":"asset://avatar_atri"}',
            standing_assets_json='{"idle":"asset://standing_atri_idle","happy":"asset://standing_atri_happy","shy":"asset://standing_atri_shy","thinking":"asset://standing_atri_thinking"}',
            chibi_widget_assets_json='{"happy":"asset_chibi_atri_widget","default":"asset_chibi_atri_widget"}',
        )
        session.add(character)
    elif not load_json(character.persona_card_json, {}):
        character.persona_card_json = dump_json(DEFAULT_PERSONA_CARD)
    if character.name in {"", "小樱"}:
        character.name = "亚托莉"
    _ensure_chibi_asset(session, character)
    exists = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one_or_none()
    if exists is None:
        session.add(RelationState(user_id=user_id, character_id=character_id))
    _ensure_initial_moments(session, character_id)
    for appearance_id in APPEARANCE_IDS:
        ensure_default_hit_areas(session, appearance_id=appearance_id)
    session.commit()
    ensure_calendar_events(session, user_id=user_id, character_id=character_id)
    return character_id
