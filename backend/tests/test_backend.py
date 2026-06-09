from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("AIGALGAME_DATA_DIR", os.path.abspath("backend/.test-data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import providers  # noqa: E402
from app.config import secret_store  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Memory, Moment, ScheduleSlot  # noqa: E402
from app.providers import ImageProvider, ProviderError, VolcArkWebSearchClient, VolcSeedTtsClient, provider_presets, upsert_provider  # noqa: E402
from app.schedule import mark_interruption, run_daily_cycle  # noqa: E402
from app.schemas import ProviderConfigIn  # noqa: E402
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
    assert "has_secret_fields" in serialized
    for leaked_value in ("ark-key", "openai-key", "gemini-key", "app-id"):
        assert leaked_value not in serialized


def test_provider_presets() -> None:
    payload = client.get("/api/config/provider-presets").json()
    assert payload["llm"][0]["provider"] == "volc_ark"
    tts_fields = {item["name"] for item in payload["tts"][0]["fields"]}
    assert {"resource_id", "speaker", "x_api_key", "access_key"}.issubset(tts_fields)
    search_fields = {item["name"] for item in payload["search"][0]["fields"]}
    assert {"max_keyword", "limit", "max_tool_calls", "user_location"}.issubset(search_fields)
    assert [item["provider"] for item in payload["image"]] == ["doubao_seedream", "openai_gpt_image", "gemini_image"]
    assert "supports_web_search" not in str(payload)


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
            "speech_rate": 0.2,
            "loudness_rate": 0.0,
            "pitch_rate": -0.1,
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
                        "resource_id": "resource-tts",
                        "speaker": "voice-01",
                        "format": "mp3",
                        "sample_rate": 24000,
                        "speech_rate": 0.2,
                        "loudness_rate": 0,
                        "pitch_rate": -0.1,
                    },
                ),
            )
            asset = VolcSeedTtsClient(config).synthesize(session, "测试语音")
            assert asset.asset_type == "tts_audio"
            assert Path(asset.local_path).exists()
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
    response = client.post("/api/events", json={"event_type": "app_opened", "session_id": "story_test", "payload": {}}).json()
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
