from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, default="你")
    timezone: Mapped[str] = mapped_column(String, default="Asia/Hong_Kong")
    sleep_start: Mapped[str] = mapped_column(String, default="00:30")
    sleep_end: Mapped[str] = mapped_column(String, default="08:00")
    interest_topics_json: Mapped[str] = mapped_column(Text, default="[]")
    proactive_daily_limit: Mapped[str] = mapped_column(String, default="low")
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    widget_bubbles_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    news_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tts_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    story_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)


class Character(Base):
    __tablename__ = "characters"

    character_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="小樱")
    age_setting: Mapped[str] = mapped_column(String, default="18+")
    persona_prompt: Mapped[str] = mapped_column(Text)
    speech_style: Mapped[str] = mapped_column(Text)
    relationship_boundary: Mapped[str] = mapped_column(Text)
    avatar_assets_json: Mapped[str] = mapped_column(Text, default="{}")
    standing_assets_json: Mapped[str] = mapped_column(Text, default="{}")
    chibi_widget_assets_json: Mapped[str] = mapped_column(Text, default="{}")
    tts_voice_type: Mapped[str] = mapped_column(String, default="")
    tts_voice_profile_id: Mapped[str] = mapped_column(String, default="")
    key_reply_threshold: Mapped[int] = mapped_column(Integer, default=75)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)


class TtsVoiceProfile(Base):
    __tablename__ = "tts_voice_profiles"

    voice_id: Mapped[str] = mapped_column(String, primary_key=True)
    provider_id: Mapped[str] = mapped_column(String, default="", index=True)
    label: Mapped[str] = mapped_column(String, default="")
    speaker: Mapped[str] = mapped_column(String)
    resource_id: Mapped[str] = mapped_column(String)
    language: Mapped[str] = mapped_column(String, default="zh")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_test_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    last_test_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)


class RelationState(Base):
    __tablename__ = "relation_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.user_id"), index=True)
    character_id: Mapped[str] = mapped_column(String, ForeignKey("characters.character_id"), index=True)
    affection: Mapped[int] = mapped_column(Integer, default=85)
    trust: Mapped[int] = mapped_column(Integer, default=60)
    dependency: Mapped[int] = mapped_column(Integer, default=35)
    mood: Mapped[int] = mapped_column(Integer, default=12)
    relationship_stage: Mapped[str] = mapped_column(String, default="初识")
    last_interaction_at: Mapped[str] = mapped_column(String, default=now_iso)
    last_decay_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)


class Memory(Base):
    __tablename__ = "memories"

    memory_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    character_id: Mapped[str] = mapped_column(String, index=True)
    layer: Mapped[str] = mapped_column(String, default="chat")
    content: Mapped[str] = mapped_column(Text)
    source_event_id: Mapped[str] = mapped_column(String, default="")
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    embedding_json: Mapped[str] = mapped_column(Text, default="[]")
    is_user_editable: Mapped[bool] = mapped_column(Boolean, default=True)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    last_retrieved_at: Mapped[str] = mapped_column(String, default="")
    expires_at: Mapped[str] = mapped_column(String, default="")


class ScheduleSlot(Base):
    __tablename__ = "schedule_slots"

    slot_id: Mapped[str] = mapped_column(String, primary_key=True)
    schedule_date: Mapped[str] = mapped_column(String, index=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    character_id: Mapped[str] = mapped_column(String, index=True)
    start_at: Mapped[str] = mapped_column(String, index=True)
    end_at: Mapped[str] = mapped_column(String)
    activity_title: Mapped[str] = mapped_column(String)
    activity_type: Mapped[str] = mapped_column(String)
    location: Mapped[str] = mapped_column(String, default="")
    planned_status: Mapped[str] = mapped_column(String, default="planned")
    actual_status: Mapped[str] = mapped_column(String, default="pending")
    interrupted_by_session_id: Mapped[str] = mapped_column(String, default="")
    salience: Mapped[int] = mapped_column(Integer, default=20)
    can_generate_moment: Mapped[bool] = mapped_column(Boolean, default=False)
    can_generate_photo: Mapped[bool] = mapped_column(Boolean, default=False)


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, default="", index=True)
    character_id: Mapped[str] = mapped_column(String, default="", index=True)
    event_date: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String, default="special")
    description: Mapped[str] = mapped_column(Text, default="")
    salience: Mapped[int] = mapped_column(Integer, default=80)
    repeats_yearly: Mapped[bool] = mapped_column(Boolean, default=False)
    source_type: Mapped[str] = mapped_column(String, default="")
    source_id: Mapped[str] = mapped_column(String, default="", index=True)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)


class Experience(Base):
    __tablename__ = "experiences"

    experience_id: Mapped[str] = mapped_column(String, primary_key=True)
    source_schedule_slot_id: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(Text)
    emotional_result: Mapped[str] = mapped_column(String, default="")
    memory_layer: Mapped[str] = mapped_column(String, default="daily")
    can_trigger_message: Mapped[bool] = mapped_column(Boolean, default=True)
    can_trigger_moment: Mapped[bool] = mapped_column(Boolean, default=True)
    can_trigger_photo: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)


class Moment(Base):
    __tablename__ = "moments"

    moment_id: Mapped[str] = mapped_column(String, primary_key=True)
    author_type: Mapped[str] = mapped_column(String, default="heroine")
    author_id: Mapped[str] = mapped_column(String, default="sakura")
    author_name: Mapped[str] = mapped_column(String, default="小樱")
    text: Mapped[str] = mapped_column(Text)
    media_asset_id: Mapped[str] = mapped_column(String, default="")
    source_experience_id: Mapped[str] = mapped_column(String, default="")
    mood_snapshot: Mapped[str] = mapped_column(String, default="")
    visibility: Mapped[str] = mapped_column(String, default="private_demo")
    created_at: Mapped[str] = mapped_column(String, default=now_iso)


class MomentInteraction(Base):
    __tablename__ = "moment_interactions"

    interaction_id: Mapped[str] = mapped_column(String, primary_key=True)
    moment_id: Mapped[str] = mapped_column(String, index=True)
    actor_type: Mapped[str] = mapped_column(String, default="user")
    actor_id: Mapped[str] = mapped_column(String, default="demo_user")
    actor_name: Mapped[str] = mapped_column(String, default="")
    interaction_type: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text, default="")
    reflected_in_chat: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)


class ProactiveEvent(Base):
    __tablename__ = "proactive_events"

    proactive_event_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    character_id: Mapped[str] = mapped_column(String, index=True)
    source_type: Mapped[str] = mapped_column(String, index=True)
    source_id: Mapped[str] = mapped_column(String, default="", index=True)
    title: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=50, index=True)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    dedupe_key: Mapped[str] = mapped_column(String, default="", index=True)
    scheduled_at: Mapped[str] = mapped_column(String, default=now_iso, index=True)
    expires_at: Mapped[str] = mapped_column(String, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    prepared_payload_json: Mapped[str] = mapped_column(Text, default="{}")
    prepared_at: Mapped[str] = mapped_column(String, default="")
    prepare_error: Mapped[str] = mapped_column(Text, default="")
    delivered_at: Mapped[str] = mapped_column(String, default="")
    opened_at: Mapped[str] = mapped_column(String, default="")
    reflected_at: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)


class UserLocation(Base):
    __tablename__ = "user_locations"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String, default="android")
    latitude: Mapped[float] = mapped_column(Float, default=0.0)
    longitude: Mapped[float] = mapped_column(Float, default=0.0)
    accuracy_m: Mapped[float] = mapped_column(Float, default=0.0)
    qweather_location_id: Mapped[str] = mapped_column(String, default="", index=True)
    city_name: Mapped[str] = mapped_column(String, default="")
    adm1: Mapped[str] = mapped_column(String, default="")
    adm2: Mapped[str] = mapped_column(String, default="")
    country: Mapped[str] = mapped_column(String, default="")
    timezone: Mapped[str] = mapped_column(String, default="")
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    captured_at: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)


class WeatherSnapshot(Base):
    __tablename__ = "weather_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    weather_date: Mapped[str] = mapped_column(String, index=True)
    location_key: Mapped[str] = mapped_column(String, default="", index=True)
    city_name: Mapped[str] = mapped_column(String, default="")
    latitude: Mapped[float] = mapped_column(Float, default=0.0)
    longitude: Mapped[float] = mapped_column(Float, default=0.0)
    observed_at: Mapped[str] = mapped_column(String, default="")
    fetched_at: Mapped[str] = mapped_column(String, default=now_iso, index=True)
    expires_at: Mapped[str] = mapped_column(String, default="", index=True)
    weather_text: Mapped[str] = mapped_column(String, default="")
    severity: Mapped[str] = mapped_column(String, default="normal")
    severity_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    trigger_key: Mapped[str] = mapped_column(String, default="", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    now_json: Mapped[str] = mapped_column(Text, default="{}")
    hourly_json: Mapped[str] = mapped_column(Text, default="{}")
    daily_json: Mapped[str] = mapped_column(Text, default="{}")
    warning_json: Mapped[str] = mapped_column(Text, default="{}")
    minutely_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)


class OpeningCache(Base):
    __tablename__ = "opening_caches"

    cache_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    character_id: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String, default="greeting", index=True)
    proactive_event_id: Mapped[str] = mapped_column(String, default="", index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String, default="ready", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[str] = mapped_column(String, default="", index=True)
    consumed_at: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)


class Message(Base):
    __tablename__ = "messages"

    message_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, index=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    character_id: Mapped[str] = mapped_column(String, index=True)
    sender_type: Mapped[str] = mapped_column(String)
    sender_id: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    message_mode: Mapped[str] = mapped_column(String, default="daily_chat")
    source: Mapped[str] = mapped_column(String, default="")
    relation_delta_json: Mapped[str] = mapped_column(Text, default="{}")
    tts_audio_asset_id: Mapped[str] = mapped_column(String, default="")
    media_asset_id: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[str] = mapped_column(String, default=now_iso)


class MediaAsset(Base):
    __tablename__ = "media_assets"

    asset_id: Mapped[str] = mapped_column(String, primary_key=True)
    asset_type: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)
    local_path: Mapped[str] = mapped_column(String, default="")
    local_cache_key: Mapped[str] = mapped_column(String, default="", index=True)
    prompt: Mapped[str] = mapped_column(Text, default="")
    negative_prompt: Mapped[str] = mapped_column(Text, default="")
    reference_image_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    source_event_id: Mapped[str] = mapped_column(String, default="")
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)


class ProviderConfig(Base):
    __tablename__ = "provider_configs"

    provider_id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    provider: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String, default="")
    base_url: Mapped[str] = mapped_column(String, default="")
    model: Mapped[str] = mapped_column(String, default="")
    secret_ref: Mapped[str] = mapped_column(String, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)
