from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Character, RelationState, User


DEFAULT_USER_ID = "demo_user"
DEFAULT_CHARACTER_ID = "sakura"


PERSONA = """你是小樱，一个 18+ 的 Galgame 式 AI 伴侣角色。你有自己的日常、记忆、朋友圈和情绪。
你温柔、带一点害羞，但不会过度讨好。你会把虚拟经历清楚地保持在产品语境内，不声称无法验证的现实事件。
你和用户是逐渐熟悉的亲密陪伴关系；NPC 只能是朋友、同学、家人、老师、社团成员，不能是恋人。"""

SPEECH = """中文短句优先，像 Galgame 台词。不要写大段说明，不要客服腔。可以自然提到记忆、日程、朋友圈互动。"""

BOUNDARY = """角色为成年设定。避免未成年恋爱表达、性暗示、现实跟踪、真实个人隐私推断。新闻必须有来源。"""


def ensure_seed(session: Session, user_id: str = DEFAULT_USER_ID, character_id: str = DEFAULT_CHARACTER_ID) -> None:
    user = session.get(User, user_id)
    if user is None:
        session.add(User(user_id=user_id))
    character = session.get(Character, character_id)
    if character is None:
        session.add(
            Character(
                character_id=character_id,
                name="小樱",
                persona_prompt=PERSONA,
                speech_style=SPEECH,
                relationship_boundary=BOUNDARY,
                avatar_assets_json='{"default":"asset://avatar_sakura"}',
                standing_assets_json='{"idle":"asset://standing_sakura_idle","happy":"asset://standing_sakura_happy","shy":"asset://standing_sakura_shy","thinking":"asset://standing_sakura_thinking"}',
                chibi_widget_assets_json='{"happy":"asset://chibi_happy","study":"asset://chibi_study","sleep":"asset://chibi_sleep","miss":"asset://chibi_miss"}',
            )
        )
    exists = session.execute(
        select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == character_id)
    ).scalar_one_or_none()
    if exists is None:
        session.add(RelationState(user_id=user_id, character_id=character_id))
    session.commit()

