from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("AIGALGAME_DATA_DIR", os.path.abspath("backend/.test-data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import providers  # noqa: E402
from app.config import secret_store  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Character, Experience, Memory, Message, Moment, MomentInteraction, ProactiveEvent, ProviderConfig, RelationState, ScheduleSlot, TtsVoiceProfile, User  # noqa: E402
from app.pipeline import _tts_for_line, handle_event  # noqa: E402
from app.proactive import create_proactive_event, ensure_news_candidate, pending_proactive_response  # noqa: E402
from app.providers import ImageProvider, ProviderError, VolcArkWebSearchClient, VolcSeedTtsClient, get_enabled_provider, provider_presets, upsert_provider  # noqa: E402
from app.schedule import mark_interruption, run_daily_cycle  # noqa: E402
from app.schemas import EventIn, ProviderConfigIn  # noqa: E402
from app.seed import ensure_seed  # noqa: E402


client = TestClient(app)


def setup_module() -> None:
    init_db()
    with SessionLocal() as session:
        ensure_seed(session)


def test_health_and_bootstrap() -> None:
    assert client.get("/api/health").json()["ok"] is True
    payload = client.get("/api/bootstrap").json()
    assert payload["character"]["name"] == "小樱"
    assert "providers" not in payload


def test_admin_routes_do_not_expose_secrets() -> None:
    assert client.get("/admin").status_code == 200
    pairing = client.get("/api/admin/pairing").json()
    assert pairing["server_url"].startswith("http://")
    status = client.get("/api/admin/status").json()
    assert status["ok"] is True
    assert "providers" in status
    serialized = json.dumps(status, ensure_ascii=False)
    if status["providers"]:
        assert "has_secret_fields" in serialized
    for leaked_value in ("ark-key", "openai-key", "gemini-key", "app-id"):
        assert leaked_value not in serialized


def test_provider_presets() -> None:
    payload = client.get("/api/config/provider-presets").json()
    assert payload["llm"][0]["provider"] == "volc_ark"
    tts_fields = {item["name"] for item in payload["tts"][0]["fields"]}
    assert {"credential_mode", "parameter_mode", "resource_id", "speaker", "x_api_key", "access_key", "emotion_map"}.issubset(tts_fields)
    search_fields = {item["name"] for item in payload["search"][0]["fields"]}
    assert {"max_keyword", "limit", "max_tool_calls", "user_location"}.issubset(search_fields)
    assert payload["search"][0]["supports_models"] is True
    assert [item["provider"] for item in payload["image"]] == ["doubao_seedream", "openai_gpt_image", "gemini_image"]
    assert "supports_web_search" not in str(payload)


def test_provider_enablement_is_exclusive_and_legacy_duplicates_are_deduped() -> None:
    with SessionLocal() as session:
        first = upsert_provider(
            session,
            ProviderConfigIn(
                provider_id="exclusive_llm_one",
                kind="llm",
                provider="deepseek",
                base_url="https://api.deepseek.com",
                model="deepseek-chat",
            ),
        )
        second = upsert_provider(
            session,
            ProviderConfigIn(
                provider_id="exclusive_llm_two",
                kind="llm",
                provider="deepseek",
                base_url="https://api.deepseek.com",
                model="deepseek-chat",
            ),
        )
        session.refresh(first)
        assert first.enabled is False
        assert second.enabled is True

        supported = upsert_provider(
            session,
            ProviderConfigIn(
                provider_id="exclusive_tts_supported",
                kind="tts",
                provider="volc_seed_tts",
                base_url="https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                secrets={"x_api_key": "tts-key"},
                metadata={"resource_id": "resource-tts", "speaker": "voice-01"},
            ),
        )
        legacy = ProviderConfig(
            provider_id="exclusive_tts_legacy",
            kind="tts",
            provider="volcengine",
            label="Legacy TTS",
            base_url="https://openspeech.bytedance.com/api/v3/tts/unidirectional",
            enabled=True,
        )
        session.merge(legacy)
        supported.enabled = True
        session.commit()

        selected = get_enabled_provider(session, "tts")
        assert selected is not None
        assert selected.provider_id == "exclusive_tts_supported"
        assert session.get(ProviderConfig, "exclusive_tts_legacy").enabled is False


def test_search_provider_can_use_model_lookup_route() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://ark.cn-beijing.volces.com/api/v3/models"
        assert request.headers["Authorization"] == "Bearer ark-key"
        return httpx.Response(200, json={"data": [{"id": "doubao-response-search"}]})

    with SessionLocal() as session:
        upsert_provider(
            session,
            ProviderConfigIn(
                provider_id="test_search_models",
                kind="search",
                provider="volc_ark_web_search",
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                model="doubao-response-search",
                secrets={"api_key": "ark-key"},
                metadata={"max_keyword": 2, "limit": 3, "max_tool_calls": 1},
            ),
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        payload = client.get("/api/config/providers/models?provider_id=test_search_models").json()
        assert payload == {"ok": True, "models": ["doubao-response-search"]}
    finally:
        providers.HTTP_TRANSPORT = None


def test_volc_tts_request_and_chunked_audio_parse() -> None:
    audio = base64.b64encode(b"ID3" + b"a" * 220).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
        assert request.headers["X-Api-App-Id"] == "app-id"
        assert request.headers["X-Api-Access-Key"] == "access"
        assert request.headers["X-Api-Resource-Id"] == "resource-tts"
        body = json.loads(request.content.decode())
        assert body["user"]["uid"] == "aigalgame"
        assert body["req_params"]["text"] == "测试语音"
        assert body["req_params"]["speaker"] == "voice-01"
        assert body["req_params"]["audio_params"] == {
            "format": "mp3",
            "sample_rate": 24000,
            "speech_rate": 12,
            "loudness_rate": -4,
            "emotion_scale": 3,
        }
        content = f'data: {{"data":"{audio[:80]}"}}\n\ndata: {{"data":"{audio[80:]}"}}\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content.encode())

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_tts_volc",
                    kind="tts",
                    provider="volc_seed_tts",
                    base_url="https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                    secrets={"app_id": "app-id", "access_key": "access"},
                    metadata={
                        "credential_mode": "app_credentials",
                        "parameter_mode": "manual",
                        "resource_id": "resource-tts",
                        "speaker": "voice-01",
                        "format": "mp3",
                        "sample_rate": 24000,
                        "speech_rate": 12,
                        "loudness_rate": -4,
                        "emotion_scale": 3,
                    },
                ),
            )
            asset = VolcSeedTtsClient(config).synthesize(session, "测试语音")
            assert asset.asset_type == "tts_audio"
            assert Path(asset.local_path).exists()
    finally:
        providers.HTTP_TRANSPORT = None


def test_initial_moments_are_seeded_with_npc_interactions() -> None:
    with SessionLocal() as session:
        ensure_seed(session, user_id="seed_moment_user", character_id="sakura")
        seed_ids = {"seed_moment_sakura_morning", "seed_moment_sakura_walk", "seed_moment_sakura_evening"}
        moments = session.query(Moment).filter(Moment.moment_id.in_(seed_ids)).all()
        assert len(moments) == 3
        interactions = session.query(MomentInteraction).filter(MomentInteraction.moment_id.in_(seed_ids)).all()
        assert len(interactions) >= 6
        assert {item.actor_type for item in interactions} >= {"npc"}


def test_admin_voice_crud_and_character_voice_selection() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    voice_id = f"voice_route_{suffix}"
    with SessionLocal() as session:
        upsert_provider(
            session,
            ProviderConfigIn(
                provider_id="test_admin_voice_provider",
                kind="tts",
                provider="volc_seed_tts",
                base_url="https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                secrets={"x_api_key": "tts-key"},
            ),
        )

    created = client.post(
        "/api/admin/tts-voices",
        json={
            "voice_id": voice_id,
            "provider_id": "test_admin_voice_provider",
            "label": "接口测试音色",
            "speaker": "speaker-route",
            "resource_id": "resource-route",
            "language": "zh",
            "enabled": True,
        },
    ).json()
    assert created["voice_id"] == voice_id

    updated = client.put(
        "/api/admin/characters/sakura",
        json={"tts_voice_profile_id": voice_id, "key_reply_threshold": 82},
    ).json()
    assert updated["tts_voice_profile_id"] == voice_id
    assert updated["key_reply_threshold"] == 82

    assert client.delete(f"/api/admin/tts-voices/{voice_id}").json()["ok"] is True
    characters = client.get("/api/admin/characters").json()["items"]
    sakura = next(item for item in characters if item["character_id"] == "sakura")
    assert sakura["tts_voice_profile_id"] == ""


def test_tts_voice_profile_uses_own_resource_and_speaker_and_skips_unreadable_text() -> None:
    requests: list[dict[str, object]] = []
    audio = base64.b64encode(b"ID3" + b"z" * 220).decode()
    text = f"测试语音 {datetime.now(timezone.utc).isoformat()}"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        requests.append(body)
        assert request.headers["X-Api-Resource-Id"] == "resource-profile-zh"
        assert body["req_params"]["speaker"] == "speaker-profile-zh"
        assert body["req_params"]["text"] == text
        return httpx.Response(200, content=f'data: {{"data":"{audio}"}}\n'.encode())

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id="tts_profile_user", character_id="sakura")
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_tts_voice_profile_provider",
                    kind="tts",
                    provider="volc_seed_tts",
                    base_url="https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                    secrets={"x_api_key": "tts-key"},
                    metadata={"resource_id": "wrong-global-resource", "speaker": "wrong-global-speaker"},
                ),
            )
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_tts_voice_profile_other_enabled_provider",
                    kind="tts",
                    provider="volc_seed_tts",
                    base_url="https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                    secrets={"x_api_key": "other-tts-key"},
                    metadata={"resource_id": "other-resource", "speaker": "other-speaker"},
                ),
            )
            session.merge(
                TtsVoiceProfile(
                    voice_id="voice_profile_zh",
                    provider_id=config.provider_id,
                    label="测试中文音色",
                    speaker="speaker-profile-zh",
                    resource_id="resource-profile-zh",
                    language="zh",
                    enabled=True,
                )
            )
            user = session.get(User, "tts_profile_user")
            character = session.get(Character, "sakura")
            user.tts_enabled = True
            character.tts_voice_type = "wrong-legacy-speaker"
            character.tts_voice_profile_id = "voice_profile_zh"
            session.commit()

            assert _tts_for_line(session, user, character, "...", "calm") == ("", "")
            assert requests == []
            tts_url, tts_error = _tts_for_line(session, user, character, text, "happy")
            assert tts_error == ""
            assert tts_url.startswith("/media/")
            assert len(requests) == 1
    finally:
        providers.HTTP_TRANSPORT = None


def test_japanese_voice_generates_extra_japanese_tts_text_only() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    resource_id = f"resource-ja-{suffix}"
    speaker = f"speaker-ja-{suffix}"
    ja_text = "今日は少し声を聞かせたいです。"
    audio = base64.b64encode(b"ID3" + b"j" * 220).decode()
    tts_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body = json.loads(request.content.decode())
        if url.endswith("/chat/completions"):
            assert "response_format" not in body
            assert "Chinese line:" in body["messages"][1]["content"]
            return httpx.Response(200, json={"choices": [{"message": {"content": ja_text}}]})
        tts_bodies.append(body)
        assert request.headers["X-Api-Resource-Id"] == resource_id
        assert body["req_params"]["speaker"] == speaker
        assert body["req_params"]["text"] == ja_text
        return httpx.Response(200, content=f'data: {{"data":"{audio}"}}\n'.encode())

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id="tts_profile_ja_user", character_id="sakura")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_tts_voice_profile_ja_llm",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_tts_voice_profile_ja_provider",
                    kind="tts",
                    provider="volc_seed_tts",
                    base_url="https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                    secrets={"x_api_key": "tts-key"},
                ),
            )
            session.merge(
                TtsVoiceProfile(
                    voice_id="voice_profile_ja",
                    provider_id=config.provider_id,
                    label="测试日文音色",
                    speaker=speaker,
                    resource_id=resource_id,
                    language="ja",
                    enabled=True,
                )
            )
            user = session.get(User, "tts_profile_ja_user")
            character = session.get(Character, "sakura")
            user.tts_enabled = True
            character.tts_voice_profile_id = "voice_profile_ja"
            session.commit()

            tts_url, tts_error = _tts_for_line(session, user, character, "今天也想听你说说话。", "calm")
            assert tts_error == ""
            assert tts_url.startswith("/media/")
            assert tts_bodies and tts_bodies[0]["req_params"]["text"] == ja_text
    finally:
        providers.HTTP_TRANSPORT = None


def test_japanese_voice_rejects_chinese_translation_and_does_not_call_tts() -> None:
    tts_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tts_calls
        url = str(request.url)
        if url.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "今天也想听你说说话。"}}]},
            )
        tts_calls += 1
        return httpx.Response(500, json={"unexpected": "tts should not be called with Chinese fallback"})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id="tts_profile_ja_reject_user", character_id="sakura")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_tts_voice_profile_ja_reject_llm",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_tts_voice_profile_ja_reject_provider",
                    kind="tts",
                    provider="volc_seed_tts",
                    base_url="https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                    secrets={"x_api_key": "tts-key"},
                ),
            )
            session.merge(
                TtsVoiceProfile(
                    voice_id="voice_profile_ja_reject",
                    provider_id=config.provider_id,
                    label="测试日文音色拒绝中文",
                    speaker="speaker-ja-reject",
                    resource_id="resource-ja-reject",
                    language="ja",
                    enabled=True,
                )
            )
            user = session.get(User, "tts_profile_ja_reject_user")
            character = session.get(Character, "sakura")
            user.tts_enabled = True
            character.tts_voice_profile_id = "voice_profile_ja_reject"
            session.commit()

            with pytest.raises(ProviderError, match="日文配音文本生成失败"):
                _tts_for_line(session, user, character, "今天也想听你说说话。", "calm")
            assert tts_calls == 0
    finally:
        providers.HTTP_TRANSPORT = None


def test_japanese_voice_pipeline_retries_missing_tts_text_ja() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"ja_retry_user_{suffix}"
    session_id = f"ja_retry_session_{suffix}"
    resource_id = f"resource-ja-retry-{suffix}"
    speaker = f"speaker-ja-retry-{suffix}"
    cn_text = f"今天也想听你说说话 {suffix}"
    ja_text = "今日もあなたの話を少し聞きたいです。"
    audio = base64.b64encode(b"ID3" + b"r" * 220).decode()
    llm_bodies: list[dict[str, object]] = []
    tts_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body = json.loads(request.content.decode())
        if url.endswith("/chat/completions"):
            llm_bodies.append(body)
            if "response_format" in body:
                assert "tts_text_ja" in body["messages"][1]["content"]
                content = json.dumps(
                    {
                        "reply_mode": "normal",
                        "lines": [{"text": cn_text, "emotion": "calm", "pose": "idle"}],
                        "normal_replies": [],
                        "key_reply_score": 0,
                        "key_replies": [],
                        "relation_delta": {"affection": 0, "trust": 0, "dependency": 0, "mood": 0},
                        "memory_candidates": [],
                        "interest_topics": [],
                    },
                    ensure_ascii=False,
                )
                return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
            return httpx.Response(200, json={"choices": [{"message": {"content": ja_text}}]})
        tts_bodies.append(body)
        assert request.headers["X-Api-Resource-Id"] == resource_id
        return httpx.Response(200, content=f'data: {{"data":"{audio}"}}\n'.encode())

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="sakura")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_ja_retry_llm_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_ja_retry_tts_{suffix}",
                    kind="tts",
                    provider="volc_seed_tts",
                    base_url="https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                    secrets={"x_api_key": "tts-key"},
                ),
            )
            session.merge(
                TtsVoiceProfile(
                    voice_id=f"voice_ja_retry_{suffix}",
                    provider_id=config.provider_id,
                    label="ja retry",
                    speaker=speaker,
                    resource_id=resource_id,
                    language="ja",
                    enabled=True,
                )
            )
            user = session.get(User, user_id)
            character = session.get(Character, "sakura")
            user.story_completed = True
            user.tts_enabled = True
            character.tts_voice_profile_id = f"voice_ja_retry_{suffix}"
            session.commit()

            result = handle_event(
                session,
                EventIn(event_type="user_message", user_id=user_id, character_id="sakura", session_id=session_id, payload={"text": "我回来了"}),
            )
            assert result.payload["lines"][0]["text"] == cn_text
            assert len(llm_bodies) == 2
            assert "response_format" in llm_bodies[0]
            assert "response_format" not in llm_bodies[1]
            assert tts_bodies[0]["req_params"]["text"] == ja_text
    finally:
        providers.HTTP_TRANSPORT = None


def test_japanese_voice_pipeline_failure_does_not_save_chinese_only_line() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"ja_fail_user_{suffix}"
    session_id = f"ja_fail_session_{suffix}"
    cn_text = f"不该被保存的中文台词 {suffix}"
    tts_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tts_calls
        url = str(request.url)
        body = json.loads(request.content.decode())
        if url.endswith("/chat/completions"):
            if "response_format" in body:
                content = json.dumps(
                    {
                        "reply_mode": "normal",
                        "lines": [{"text": cn_text, "emotion": "calm", "pose": "idle"}],
                        "normal_replies": [],
                        "key_reply_score": 0,
                        "key_replies": [],
                        "relation_delta": {"affection": 0, "trust": 0, "dependency": 0, "mood": 0},
                        "memory_candidates": [],
                        "interest_topics": [],
                    },
                    ensure_ascii=False,
                )
                return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
            return httpx.Response(200, json={"choices": [{"message": {"content": cn_text}}]})
        tts_calls += 1
        return httpx.Response(500, json={"unexpected": "tts should not run"})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="sakura")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_ja_fail_llm_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_ja_fail_tts_{suffix}",
                    kind="tts",
                    provider="volc_seed_tts",
                    base_url="https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                    secrets={"x_api_key": "tts-key"},
                ),
            )
            voice_id = f"voice_ja_fail_{suffix}"
            session.merge(
                TtsVoiceProfile(
                    voice_id=voice_id,
                    provider_id=config.provider_id,
                    label="ja fail",
                    speaker=f"speaker-ja-fail-{suffix}",
                    resource_id=f"resource-ja-fail-{suffix}",
                    language="ja",
                    enabled=True,
                )
            )
            user = session.get(User, user_id)
            character = session.get(Character, "sakura")
            user.story_completed = True
            user.tts_enabled = True
            character.tts_voice_profile_id = voice_id
            session.commit()

            with pytest.raises(ProviderError, match="日文配音文本生成失败"):
                handle_event(
                    session,
                    EventIn(event_type="user_message", user_id=user_id, character_id="sakura", session_id=session_id, payload={"text": "我回来了"}),
                )
            session.rollback()
            saved = session.execute(
                select(Message).where(Message.user_id == user_id, Message.session_id == session_id, Message.sender_type == "heroine", Message.content == cn_text)
            ).scalars().all()
            assert saved == []
            assert tts_calls == 0
    finally:
        providers.HTTP_TRANSPORT = None


def test_volc_ark_web_search_normalizes_sources_and_requires_publish_time() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        requests.append(body)
        assert str(request.url) == "https://ark.cn-beijing.volces.com/api/v3/responses"
        assert body["tools"][0]["type"] == "web_search"
        assert body["tools"][0]["max_keyword"] == 2
        assert body["tools"][0]["limit"] == 3
        assert body["max_tool_calls"] == 1
        payload = {
            "id": "resp_mock",
            "output_text": "找到一条新闻。",
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {
                                "title": "AI 游戏新闻",
                                "url": "https://example.com/news",
                                "publish_time": "2026-06-09T10:00:00+08:00",
                                "summary": "来源摘要",
                            }
                        ]
                    },
                }
            ],
            "usage": {"tool_usage_details": [{"type": "web_search"}]},
        }
        return httpx.Response(200, json=payload)

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_search_volc",
                    kind="search",
                    provider="volc_ark_web_search",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-search-model",
                    secrets={"api_key": "ark-key"},
                    metadata={"max_keyword": 2, "limit": 3, "max_tool_calls": 1},
                ),
            )
            result = VolcArkWebSearchClient(config).search("AI 游戏新闻", require_published_at=True)
            assert result["sources"][0]["title"] == "AI 游戏新闻"
            assert result["sources"][0]["published_at"] == "2026-06-09T10:00:00+08:00"
            assert requests[0]["input"][0]["content"][0]["type"] == "input_text"
    finally:
        providers.HTTP_TRANSPORT = None

    def no_publish_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": [{"content": [{"annotations": [{"title": "无时间", "url": "https://example.com"}]}]}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(no_publish_handler)
    try:
        with SessionLocal() as session:
            config = session.get(type(config), "test_search_volc")
            with pytest.raises(ProviderError, match="published_at"):
                VolcArkWebSearchClient(config).search("AI 游戏新闻", require_published_at=True)
    finally:
        providers.HTTP_TRANSPORT = None


def test_volc_ark_web_search_404_returns_actionable_diagnostic() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://ark.cn-beijing.volces.com/api/v3/responses"
        return httpx.Response(404, json={"error": {"message": "not found"}})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_search_volc_404",
                    kind="search",
                    provider="volc_ark_web_search",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-response-search",
                    secrets={"api_key": "ark-key"},
                ),
            )
            with pytest.raises(ProviderError, match="does not support Responses or Web Search"):
                VolcArkWebSearchClient(config).search("AI galgame news", require_published_at=True)
    finally:
        providers.HTTP_TRANSPORT = None


def test_special_replies_are_gated_by_character_threshold() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
        content = json.dumps(
            {
                "lines": [{"text": "嗯，我听到了。今天就慢慢说吧。", "emotion": "calm", "pose": "idle"}],
                "normal_replies": [{"text": "那我继续说。"}],
                "key_reply_score": 20,
                "key_reply_reason": "普通闲聊，不应该给特殊回复",
                "key_replies": [
                    {
                        "text": "这是一个低分特殊选项",
                        "score": 20,
                        "preview_delta": {"affection": 9, "trust": 9, "dependency": 0, "mood": 0},
                        "trigger_memory": True,
                    }
                ],
                "relation_delta": {"affection": 0, "trust": 0, "dependency": 0, "mood": 0},
                "memory_candidates": [],
                "interest_topics": [],
            },
            ensure_ascii=False,
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id="threshold_user", character_id="sakura")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_threshold_llm",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, "threshold_user")
            character = session.get(Character, "sakura")
            user.story_completed = True
            user.tts_enabled = False
            character.key_reply_threshold = 75
            session.commit()
            result = handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id="threshold_user",
                    character_id="sakura",
                    session_id="threshold_session",
                    payload={"text": "今天有点累。"},
                ),
            )
            assert result.payload["lines"]
            assert result.payload["normal_replies"]
            assert result.payload["key_replies"] == []
    finally:
        providers.HTTP_TRANSPORT = None


def test_normal_reply_option_does_not_apply_relation_delta() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(
            {
                "lines": [{"text": "嗯，那就继续慢慢说。", "emotion": "calm", "pose": "idle"}],
                "normal_replies": [{"text": "好。"}],
                "key_reply_score": 0,
                "key_replies": [],
                "relation_delta": {"affection": 3, "trust": 2, "dependency": 1, "mood": 3},
                "memory_candidates": [],
                "interest_topics": [],
            },
            ensure_ascii=False,
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id="normal_reply_user", character_id="sakura")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_normal_reply_llm",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, "normal_reply_user")
            relation = session.execute(
                select(RelationState).where(RelationState.user_id == "normal_reply_user", RelationState.character_id == "sakura")
            ).scalar_one()
            user.story_completed = True
            user.tts_enabled = False
            before = (relation.affection, relation.trust, relation.dependency, relation.mood)
            session.commit()

            result = handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id="normal_reply_user",
                    character_id="sakura",
                    session_id="normal_reply_session",
                    payload={"text": "好。", "reply_id": "normal_reply_1"},
                ),
            )
            session.refresh(relation)
            after = (relation.affection, relation.trust, relation.dependency, relation.mood)
            assert after == before
            assert result.payload["relation_delta"] == {"affection": 0, "trust": 0, "dependency": 0, "mood": 0}
    finally:
        providers.HTTP_TRANSPORT = None


def test_light_reply_mode_has_no_options_or_relation_delta() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(
            {
                "reply_mode": "light",
                "pace_reason": "用户只是短确认，轻轻回应。",
                "lines": [
                    {"text": "嗯，我在。", "emotion": "calm", "pose": "idle"},
                    {"text": "慢慢来就好。", "emotion": "calm", "pose": "idle"},
                ],
                "normal_replies": [{"text": "好。"}],
                "key_reply_score": 100,
                "key_replies": [{"text": "重要选择", "score": 100, "preview_delta": {"affection": 10}}],
                "relation_delta": {"affection": 3, "trust": 2, "dependency": 1, "mood": 3},
                "memory_candidates": [{"layer": "core", "content": "不该成为高权重记忆", "importance": 0.95, "confidence": 0.9}],
                "interest_topics": [],
            },
            ensure_ascii=False,
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id="light_reply_user", character_id="sakura")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_light_reply_llm",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, "light_reply_user")
            relation = session.execute(
                select(RelationState).where(RelationState.user_id == "light_reply_user", RelationState.character_id == "sakura")
            ).scalar_one()
            user.story_completed = True
            user.tts_enabled = False
            before = (relation.affection, relation.trust, relation.dependency, relation.mood)
            session.commit()

            result = handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id="light_reply_user",
                    character_id="sakura",
                    session_id="light_reply_session",
                    payload={"text": "嗯"},
                ),
            )
            session.refresh(relation)
            assert result.event_type == "dialogue"
            assert result.payload["reply_mode"] == "light"
            assert len(result.payload["lines"]) == 1
            assert result.payload["normal_replies"] == []
            assert result.payload["key_replies"] == []
            assert result.payload["relation_delta"] == {"affection": 0, "trust": 0, "dependency": 0, "mood": 0}
            assert (relation.affection, relation.trust, relation.dependency, relation.mood) == before
            memory = session.query(Memory).filter(Memory.user_id == "light_reply_user", Memory.content.contains("不该成为高权重")).first()
            assert memory is not None
            assert memory.importance <= 0.69
    finally:
        providers.HTTP_TRANSPORT = None


def test_app_opened_without_proactive_event_returns_no_reply() -> None:
    with SessionLocal() as session:
        ensure_seed(session, user_id="no_reply_user", character_id="sakura")
        user = session.get(User, "no_reply_user")
        user.story_completed = True
        session.add(
            Message(
                message_id=f"msg_no_reply_{str(datetime.now(timezone.utc).timestamp()).replace('.', '')}",
                session_id="no_reply_session",
                user_id="no_reply_user",
                character_id="sakura",
                sender_type="heroine",
                sender_id="sakura",
                content="旧问候不应该被重复回放。",
                source="app_opened",
            )
        )
        session.commit()
        result = handle_event(
            session,
            EventIn(
                event_type="app_opened",
                user_id="no_reply_user",
                character_id="sakura",
                session_id="no_reply_session",
                payload={},
            ),
        )
        assert result.event_type == "no_reply"


def test_proactive_pending_delivery_limits_sleep_and_gap() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"proactive_limit_user_{suffix}"
    with SessionLocal() as session:
        ensure_seed(session, user_id=user_id, character_id="sakura")
        for index in range(3):
            session.add(
                ProactiveEvent(
                    proactive_event_id=f"pe_limit_delivered_{suffix}_{index}",
                    user_id=user_id,
                    character_id="sakura",
                    source_type="memory",
                    source_id=f"delivered_{suffix}_{index}",
                    title=f"已投递标题 {index}",
                    text=f"已投递内容 {index}",
                    priority=50,
                    status="delivered",
                    dedupe_key=f"delivered_{suffix}_{index}",
                    scheduled_at="2026-06-09T01:00:00+00:00",
                    delivered_at=f"2026-06-09T0{index + 1}:00:00+08:00",
                    created_at=f"2026-06-09T0{index + 1}:00:00+08:00",
                    updated_at=f"2026-06-09T0{index + 1}:00:00+08:00",
                )
            )
        create_proactive_event(
            session,
            user_id=user_id,
            character_id="sakura",
            source_type="memory",
            source_id=f"pending_limit_{suffix}",
            title="待投递标题",
            text="待投递内容",
            priority=90,
            dedupe_key=f"pending_limit_{suffix}",
            scheduled_at=datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
        )
        session.commit()
        limited = pending_proactive_response(
            session,
            user_id=user_id,
            character_id="sakura",
            local_time=datetime.fromisoformat("2026-06-09T12:00:00+08:00"),
            generate_news=False,
        )
        assert limited["event"] is None

        gap_user_id = f"proactive_gap_user_{suffix}"
        ensure_seed(session, user_id=gap_user_id, character_id="sakura")
        user = session.get(User, gap_user_id)
        user.sleep_start = "00:30"
        user.sleep_end = "08:00"
        session.add(
            ProactiveEvent(
                proactive_event_id=f"pe_gap_recent_{suffix}",
                user_id=gap_user_id,
                character_id="sakura",
                source_type="memory",
                source_id=f"gap_recent_{suffix}",
                title="刚投递标题",
                text="刚投递内容",
                priority=50,
                status="delivered",
                dedupe_key=f"gap_recent_{suffix}",
                scheduled_at="2026-06-09T02:00:00+00:00",
                delivered_at="2026-06-09T12:00:00+08:00",
                created_at="2026-06-09T12:00:00+08:00",
                updated_at="2026-06-09T12:00:00+08:00",
            )
        )
        create_proactive_event(
            session,
            user_id=gap_user_id,
            character_id="sakura",
            source_type="memory",
            source_id=f"pending_gap_{suffix}",
            title="间隔测试",
            text="间隔不够时不应该投递。",
            priority=90,
            dedupe_key=f"pending_gap_{suffix}",
            scheduled_at=datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
        )
        session.commit()
        too_soon = pending_proactive_response(
            session,
            user_id=gap_user_id,
            character_id="sakura",
            local_time=datetime.fromisoformat("2026-06-09T12:30:00+08:00"),
            generate_news=False,
        )
        assert too_soon["event"] is None
        after_gap = pending_proactive_response(
            session,
            user_id="proactive_gap_user",
            character_id="sakura",
            local_time=datetime.fromisoformat("2026-06-09T13:31:00+08:00"),
            generate_news=False,
        )
        assert after_gap["event"]["proactive_event_id"]

        ensure_seed(session, user_id="proactive_sleep_user", character_id="sakura")
        create_proactive_event(
            session,
            user_id="proactive_sleep_user",
            character_id="sakura",
            source_type="memory",
            source_id="pending_sleep",
            title="睡眠测试",
            text="睡眠时间不应该投递。",
            priority=90,
            dedupe_key="pending_sleep",
            scheduled_at=datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
        )
        session.commit()
        sleeping = pending_proactive_response(
            session,
            user_id="proactive_sleep_user",
            character_id="sakura",
            local_time=datetime.fromisoformat("2026-06-09T01:00:00+08:00"),
            generate_news=False,
        )
        assert sleeping["event"] is None


def test_proactive_pending_and_delivered_routes() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"proactive_route_user_{suffix}"
    with SessionLocal() as session:
        ensure_seed(session, user_id=user_id, character_id="sakura")
        event = create_proactive_event(
            session,
            user_id=user_id,
            character_id="sakura",
            source_type="memory",
            source_id=f"route_memory_{suffix}",
            title="路由测试",
            text="这是一条可以投递的主动消息。",
            priority=88,
            dedupe_key=f"route_memory_{suffix}",
            scheduled_at=datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
        )
        event_id = event.proactive_event_id
        session.commit()
    payload = client.get(
        f"/api/proactive/pending?user_id={user_id}&character_id=sakura&local_time=2026-06-09T12:00:00%2B08:00"
    ).json()
    assert payload["event"]["proactive_event_id"] == event_id
    assert payload["widget"]["unread_count"] == 1
    delivered = client.post(f"/api/proactive/{event_id}/delivered").json()
    assert delivered["event"]["status"] == "delivered"


def test_notification_opened_reflects_proactive_event_in_chat() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert "主动想告诉用户" in body["messages"][1]["content"]
        content = json.dumps(
            {
                "reply_mode": "normal",
                "pace_reason": "用户点开了主动消息。",
                "lines": [{"text": "我刚刚就是想跟你说这件事。", "emotion": "happy", "pose": "happy"}],
                "normal_replies": [{"text": "我听着。"}],
                "key_reply_score": 0,
                "key_replies": [],
                "relation_delta": {"affection": 1, "trust": 0, "dependency": 0, "mood": 1},
                "memory_candidates": [],
                "interest_topics": [],
            },
            ensure_ascii=False,
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id="proactive_open_user", character_id="sakura")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_proactive_open_llm",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, "proactive_open_user")
            user.story_completed = True
            user.tts_enabled = False
            proactive = create_proactive_event(
                session,
                user_id="proactive_open_user",
                character_id="sakura",
                source_type="moment_interaction",
                source_id="mi_open",
                title="小樱注意到了你的互动",
                text="我看到你在朋友圈里留言了。",
                priority=80,
                dedupe_key="mi_open",
            )
            session.commit()
            result = handle_event(
                session,
                EventIn(
                    event_type="notification_opened",
                    user_id="proactive_open_user",
                    character_id="sakura",
                    session_id="proactive_open_session",
                    payload={"proactive_event_id": proactive.proactive_event_id},
                ),
            )
            assert result.event_type == "dialogue"
            assert result.payload["lines"][0]["text"] == "我刚刚就是想跟你说这件事。"
            refreshed = session.get(ProactiveEvent, proactive.proactive_event_id)
            assert refreshed.status == "reflected"
            assert refreshed.opened_at
            assert refreshed.reflected_at
    finally:
        providers.HTTP_TRANSPORT = None


def test_news_candidate_requires_verifiable_publish_time() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": [{"content": [{"annotations": [{"title": "无时间", "url": "https://example.com"}]}]}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id="news_skip_user", character_id="sakura")
            user = session.get(User, "news_skip_user")
            user.interest_topics_json = json.dumps(["AI 游戏"], ensure_ascii=False)
            user.news_enabled = True
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_news_skip_search",
                    kind="search",
                    provider="volc_ark_web_search",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-response-search",
                    secrets={"api_key": "ark-key"},
                ),
            )
            session.commit()
            assert ensure_news_candidate(
                session,
                user_id="news_skip_user",
                character_id="sakura",
                local_time=datetime.fromisoformat("2026-06-09T12:00:00+08:00"),
            ) is None
            assert session.query(ProactiveEvent).filter(ProactiveEvent.user_id == "news_skip_user", ProactiveEvent.source_type == "news").count() == 0
    finally:
        providers.HTTP_TRANSPORT = None


def test_image_providers_save_real_media_assets() -> None:
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"p" * 100
    image_b64 = base64.b64encode(image_bytes).decode()

    def doubao_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, headers={"content-type": "image/png"}, content=image_bytes)
        body = json.loads(request.content.decode())
        assert str(request.url) == "https://ark.cn-beijing.volces.com/api/v3/images/generations"
        assert body["response_format"] == "url"
        assert body["output_format"] == "png"
        return httpx.Response(200, json={"data": [{"url": "https://image.example/doubao.png"}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(doubao_handler)
    try:
        with SessionLocal() as session:
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_image_doubao",
                    kind="image",
                    provider="doubao_seedream",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-seedream-5-0-260128",
                    secrets={"api_key": "ark-key"},
                    metadata={"size": "1024x1024", "output_format": "png", "response_format": "url"},
                ),
            )
            asset = ImageProvider(config).generate(session, "测试图片")
            assert asset.ai_generated is True
            assert Path(asset.local_path).exists()
    finally:
        providers.HTTP_TRANSPORT = None

    def openai_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert str(request.url) == "https://api.openai.com/v1/images/generations"
        assert body["model"] == "gpt-image-2"
        assert "response_format" not in body
        return httpx.Response(200, json={"data": [{"b64_json": image_b64}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(openai_handler)
    try:
        with SessionLocal() as session:
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_image_openai",
                    kind="image",
                    provider="openai_gpt_image",
                    base_url="https://api.openai.com/v1",
                    model="gpt-image-2",
                    secrets={"api_key": "openai-key"},
                    metadata={"size": "1024x1024", "output_format": "png", "quality": "auto"},
                ),
            )
            asset = ImageProvider(config).generate(session, "测试图片")
            assert asset.ai_generated is True
            assert Path(asset.local_path).exists()
    finally:
        providers.HTTP_TRANSPORT = None

    def gemini_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert str(request.url) == "https://generativelanguage.googleapis.com/v1/models/gemini-3.1-flash-image:generateContent"
        assert request.headers["x-goog-api-key"] == "gemini-key"
        assert body["generationConfig"]["responseModalities"] == ["IMAGE"]
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"inlineData": {"mimeType": "image/png", "data": image_b64}},
                            ]
                        }
                    }
                ]
            },
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(gemini_handler)
    try:
        with SessionLocal() as session:
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_image_gemini",
                    kind="image",
                    provider="gemini_image",
                    base_url="https://generativelanguage.googleapis.com/v1",
                    model="gemini-3.1-flash-image",
                    secrets={"api_key": "gemini-key"},
                    metadata={"response_modalities": ["IMAGE"]},
                ),
            )
            asset = ImageProvider(config).generate(session, "测试图片")
            assert asset.ai_generated is True
            assert Path(asset.local_path).exists()
    finally:
        providers.HTTP_TRANSPORT = None


def test_story_flow_without_llm() -> None:
    with SessionLocal() as session:
        ensure_seed(session, user_id="story_flow_user", character_id="sakura")
        user = session.get(User, "story_flow_user")
        character = session.get(Character, "sakura")
        user.tts_enabled = False
        character.tts_voice_profile_id = ""
        user.story_completed = False
        session.commit()
    response = client.post(
        "/api/events",
        json={"event_type": "app_opened", "user_id": "story_flow_user", "character_id": "sakura", "session_id": "story_test", "payload": {}},
    ).json()
    assert response["event_type"] in {"story_line", "dialogue"}
    if response["event_type"] == "story_line":
        assert response["payload"]["lines"][0]["text"]


def test_schedule_interruption_and_daily_cycle() -> None:
    with SessionLocal() as session:
        ensure_seed(session)
        slot = ScheduleSlot(
            slot_id="slot_test_interrupt",
            schedule_date="2026-06-09",
            user_id="demo_user",
            character_id="sakura",
            start_at="2026-06-09T14:00:00+08:00",
            end_at="2026-06-09T15:00:00+08:00",
            activity_title="学习",
            activity_type="study",
            actual_status="pending",
        )
        session.merge(slot)
        session.commit()
        mark_interruption(session, user_id="demo_user", session_id="s_test", local_time=datetime.fromisoformat("2026-06-09T14:22:00+08:00"))
        updated = session.get(ScheduleSlot, "slot_test_interrupt")
        assert updated.actual_status == "interrupted"


def test_daily_cycle_moment_uses_llm_for_npc_interactions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert str(request.url) == "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
        assert body["response_format"] == {"type": "json_object"}
        content = json.dumps(
            {
                "text": "社团准备结束，忽然觉得今天也有一点点被认真对待。",
                "mood": "开心",
                "photo_prompt": "",
                "likes": ["隔壁班的遥", "社团前辈千夏"],
                "comments": [{"actor_name": "图书委员澪", "content": "这句听起来很像你会说的话。"}],
            },
            ensure_ascii=False,
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id="moment_user", character_id="sakura")
            session.query(MomentInteraction).filter(MomentInteraction.moment_id.in_(session.query(Moment.moment_id).filter(Moment.text.contains("社团准备结束")))).delete(synchronize_session=False)
            session.query(Moment).filter(Moment.text.contains("社团准备结束")).delete(synchronize_session=False)
            session.query(Experience).filter(Experience.source_schedule_slot_id == "slot_test_llm_moment").delete(synchronize_session=False)
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_moment_llm",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            session.merge(
                ScheduleSlot(
                    slot_id="slot_test_llm_moment",
                    schedule_date="2026-06-09",
                    user_id="moment_user",
                    character_id="sakura",
                    start_at="2026-06-09T18:00:00+08:00",
                    end_at="2026-06-09T19:00:00+08:00",
                    activity_title="社团准备",
                    activity_type="study",
                    location="图书馆",
                    actual_status="completed",
                    can_generate_moment=True,
                    can_generate_photo=False,
                    salience=70,
                )
            )
            session.commit()
            result = run_daily_cycle(session, user_id="moment_user", character_id="sakura", day=datetime.fromisoformat("2026-06-09T12:00:00+08:00"))
            assert result["moments"] >= 1
            assert result["proactive_events"] >= 1
            moment = session.query(Moment).filter(Moment.text.contains("社团准备结束")).order_by(Moment.created_at.desc()).first()
            assert moment is not None
            assert "社团准备结束" in moment.text
            proactive = session.query(ProactiveEvent).filter(ProactiveEvent.user_id == "moment_user", ProactiveEvent.source_type == "schedule").order_by(ProactiveEvent.created_at.desc()).first()
            assert proactive is not None
            assert proactive.dedupe_key.startswith("schedule:moment_user:")
            interactions = session.query(MomentInteraction).filter(MomentInteraction.moment_id == moment.moment_id).all()
            assert {item.actor_name for item in interactions} >= {"隔壁班的遥", "社团前辈千夏", "图书委员澪"}
    finally:
        providers.HTTP_TRANSPORT = None


def test_moment_feedback_writes_memory() -> None:
    with SessionLocal() as session:
        moment = Moment(moment_id="moment_test_feedback", text="测试朋友圈")
        session.merge(moment)
        session.commit()
    assert client.post("/api/moments/moment_test_feedback/like").json()["ok"] is True
    assert client.post("/api/moments/moment_test_feedback/comments", json={"content": "看起来不错"}).json()["ok"] is True
    with SessionLocal() as session:
        memories = session.query(Memory).filter(Memory.content.contains("朋友圈")).all()
        assert memories
        proactive = session.query(ProactiveEvent).filter(ProactiveEvent.source_type == "moment_interaction", ProactiveEvent.status == "pending").order_by(ProactiveEvent.created_at.desc()).first()
        assert proactive is not None
        assert "朋友圈" in proactive.text
