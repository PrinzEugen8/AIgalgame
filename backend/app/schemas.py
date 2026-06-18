from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ProviderConfigIn(BaseModel):
    provider_id: str | None = None
    kind: Literal["llm", "llm_task", "embedding", "tts", "search", "image", "weather", "push"]
    provider: str
    label: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str | None = None
    secrets: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ProviderConfigOut(BaseModel):
    provider_id: str
    kind: str
    provider: str
    label: str
    base_url: str
    model: str
    metadata: dict[str, Any]
    enabled: bool
    has_secret: bool
    has_secret_fields: dict[str, bool] = Field(default_factory=dict)
    missing_secret_fields: list[str] = Field(default_factory=list)
    missing_required_fields: list[str] = Field(default_factory=list)
    ready: bool = False


class ProviderTestRequest(ProviderConfigIn):
    test_text: str = "今天也想和你聊聊天。"


class ProviderTestResult(BaseModel):
    ok: bool
    kind: str
    provider: str
    message: str
    elapsed_ms: int = 0
    details: dict[str, Any] = Field(default_factory=dict)


class TtsVoiceProfileIn(BaseModel):
    voice_id: str | None = None
    provider_id: str = ""
    label: str = ""
    speaker: str
    resource_id: str
    language: Literal["zh", "ja"] = "zh"
    enabled: bool = True


class TtsVoiceProfileOut(BaseModel):
    voice_id: str
    provider_id: str = ""
    label: str = ""
    speaker: str
    resource_id: str
    language: str = "zh"
    enabled: bool
    last_test_ok: bool = False
    last_test_message: str = ""
    updated_at: str = ""


class CharacterAdminIn(BaseModel):
    name: str | None = None
    persona_card: dict[str, Any] | None = None
    persona_prompt: str | None = None
    speech_style: str | None = None
    relationship_boundary: str | None = None
    tts_voice_profile_id: str | None = None
    key_reply_threshold: int | None = None


class CharacterAdminOut(BaseModel):
    character_id: str
    name: str
    age_setting: str = "18+"
    persona_card: dict[str, Any] = Field(default_factory=dict)
    persona_prompt: str
    speech_style: str
    relationship_boundary: str
    tts_voice_type: str = ""
    tts_voice_profile_id: str = ""
    key_reply_threshold: int = 75


class EventIn(BaseModel):
    event_type: str
    event_id: str | None = None
    user_id: str = "demo_user"
    character_id: str = ""
    session_id: str = "default"
    payload: dict[str, Any] = Field(default_factory=dict)
    client_context: dict[str, Any] = Field(default_factory=dict)


class RelationDelta(BaseModel):
    affection: int = 0
    trust: int = 0
    dependency: int = 0
    mood: int = 0


class ReplyOption(BaseModel):
    reply_id: str
    text: str
    type: Literal["normal", "key"] = "normal"
    preview_delta: RelationDelta | None = None
    trigger_memory: bool = False


class DialogueControllerCommand(BaseModel):
    state: str = "speaking"
    face: str = ""
    animation: str = ""
    focus: str = ""
    mouth: str = "auto"
    lipsync: bool = True
    pause: float = 0.0
    tags: list[str] = Field(default_factory=list)


class DialogueVisualCue(BaseModel):
    text: str = ""
    face: str = ""
    expression: str = ""
    focus: str = ""
    weight: float = 1.0


class DialogueLine(BaseModel):
    line_id: str
    text: str
    emotion: str = "calm"
    pose: str = "idle"
    expression: str = ""
    motion: str = ""
    controller: DialogueControllerCommand = Field(default_factory=DialogueControllerCommand)
    visual_cues: list[DialogueVisualCue] = Field(default_factory=list)
    background: str = "classroom_sakura"
    tts_audio_url: str = ""
    tts_error: str = ""

    @model_validator(mode="after")
    def fill_controller_defaults(self) -> "DialogueLine":
        controller = self.controller or DialogueControllerCommand()
        face = (controller.face or self.expression or self.emotion or "calm").strip()
        animation = (controller.animation or self.motion or self.pose or "idle").strip()
        state = (controller.state or "speaking").strip().lower()
        if state not in {"idle", "typing", "speaking"}:
            state = "speaking"
        mouth = (controller.mouth or "auto").strip().lower()
        if mouth in {"none", "off"} and not (controller.animation or "").strip() and not (self.motion or "").strip():
            animation = ""
        tags = list(controller.tags or [])
        if face and not any(str(tag).lower().startswith("[face:") for tag in tags):
            tags.append(f"[face:{face}]")
        if animation and not any(str(tag).lower().startswith("[anim:") for tag in tags):
            tags.append(f"[anim:{animation}]")
        pause = max(0.0, min(10.0, float(controller.pause or 0.0)))
        if pause > 0 and not any(str(tag).lower().startswith("[pause:") for tag in tags):
            tags.append(f"[pause:{pause:g}]")
        self.controller = DialogueControllerCommand(
            state=state,
            face=face,
            animation=animation,
            focus=(controller.focus or "").strip(),
            mouth=mouth,
            lipsync=bool(controller.lipsync),
            pause=pause,
            tags=tags,
        )
        return self


class DialoguePayload(BaseModel):
    lines: list[DialogueLine]
    normal_replies: list[ReplyOption] = Field(default_factory=list)
    key_replies: list[ReplyOption] = Field(default_factory=list)
    relation_delta: RelationDelta = Field(default_factory=RelationDelta)
    media_asset_id: str = ""
    reply_mode: str = "normal"
    pace_reason: str = ""
    reply_depth: str = "normal"
    continuation_intent: str = ""
    continued: bool = False
    profile_mutation: dict[str, Any] = Field(default_factory=dict)
    stats: dict[str, Any] = Field(default_factory=dict)


class AppEventOut(BaseModel):
    event_type: str
    event_id: str
    session_id: str = "default"
    payload: dict[str, Any]
