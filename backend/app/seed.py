from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Character, MediaAsset, Moment, MomentInteraction, RelationState, User


DEFAULT_USER_ID = "demo_user"
DEFAULT_CHARACTER_ID = "sakura"


PERSONA = """你是小樱，一个 18+ 的 Galgame 式 AI 伴侣角色。你有自己的日常、记忆、朋友圈和情绪。
你温柔、带一点害羞，但不会过度讨好。你会把虚拟经历清楚地保持在产品语境内，不声称无法验证的现实事件。
你和用户是逐渐熟悉的亲密陪伴关系；NPC 只能是朋友、同学、家人、老师、社团成员，不能是恋人。"""

SPEECH = """中文短句优先，像 Galgame 台词。不要写大段说明，不要客服腔。可以自然提到记忆、日程、朋友圈互动。"""

BOUNDARY = """角色为成年设定。避免未成年恋爱表达、性暗示、现实跟踪、真实个人隐私推断。新闻必须有来源。"""


INITIAL_MOMENTS = [
    {
        "moment_id": "seed_moment_sakura_morning",
        "text": "早上的教室还很安静，我把窗边的位置擦了一遍。今天也想慢慢把日子过得像样一点。",
        "mood": "平静",
        "likes": ["隔壁班的遥", "图书委员澪"],
        "comments": [{"actor_name": "社团前辈千夏", "content": "窗边的位置听起来很适合你。"}],
    },
    {
        "moment_id": "seed_moment_sakura_walk",
        "text": "樱花路上有一点风，发带差点被吹走。还好我反应很快，没有在路人面前慌张太久。",
        "mood": "害羞",
        "likes": ["同桌同学", "路过的朋友"],
        "comments": [{"actor_name": "隔壁班的遥", "content": "小樱的“没有慌张太久”很可疑。"}],
    },
    {
        "moment_id": "seed_moment_sakura_evening",
        "text": "晚上整理笔记的时候，忽然想起还有很多话没说。等你有空的时候，再慢慢讲给你听。",
        "mood": "想念",
        "likes": ["图书委员澪", "社团前辈千夏"],
        "comments": [{"actor_name": "同桌同学", "content": "这句很像会被认真收藏起来的话。"}],
    },
]


def _ensure_chibi_asset(session: Session, character: Character) -> None:
    asset_id = "asset_chibi_sakura_widget"
    image_path = Path(__file__).resolve().parents[2] / "android" / "app" / "src" / "main" / "res" / "drawable-nodpi" / "chibi_sakura_widget.png"
    if image_path.exists() and session.get(MediaAsset, asset_id) is None:
        session.add(
            MediaAsset(
                asset_id=asset_id,
                asset_type="image",
                url=f"/media/{asset_id}",
                local_path=str(image_path),
                local_cache_key="local:chibi_sakura_widget",
                prompt="本地桌面小组件 Q 版小樱形象",
                ai_generated=True,
            )
        )
    if asset_id not in character.chibi_widget_assets_json:
        character.chibi_widget_assets_json = f'{{"happy":"{asset_id}","default":"{asset_id}"}}'


def _ensure_initial_moments(session: Session, character_id: str) -> None:
    existing_count = session.query(Moment).filter(Moment.author_id == character_id).count()
    if existing_count >= len(INITIAL_MOMENTS):
        return
    for item in INITIAL_MOMENTS:
        moment_id = item["moment_id"]
        moment = session.get(Moment, moment_id)
        if moment is None:
            moment = Moment(
                moment_id=moment_id,
                author_id=character_id,
                author_name="小樱",
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


def ensure_seed(session: Session, user_id: str = DEFAULT_USER_ID, character_id: str = DEFAULT_CHARACTER_ID) -> None:
    user = session.get(User, user_id)
    if user is None:
        session.add(User(user_id=user_id))
    character = session.get(Character, character_id)
    if character is None:
        character = Character(
            character_id=character_id,
            name="小樱",
            persona_prompt=PERSONA,
            speech_style=SPEECH,
            relationship_boundary=BOUNDARY,
            avatar_assets_json='{"default":"asset://avatar_sakura"}',
            standing_assets_json='{"idle":"asset://standing_sakura_idle","happy":"asset://standing_sakura_happy","shy":"asset://standing_sakura_shy","thinking":"asset://standing_sakura_thinking"}',
            chibi_widget_assets_json='{"happy":"asset_chibi_sakura_widget","default":"asset_chibi_sakura_widget"}',
        )
        session.add(character)
    _ensure_chibi_asset(session, character)
    exists = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one_or_none()
    if exists is None:
        session.add(RelationState(user_id=user_id, character_id=character_id))
    _ensure_initial_moments(session, character_id)
    session.commit()
