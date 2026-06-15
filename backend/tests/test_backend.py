from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("AIGALGAME_DATA_DIR", os.path.abspath("backend/.test-data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import providers, schedule as schedule_module, scheduler as scheduler_module  # noqa: E402
from app.config import secret_store  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.diagnostics import diagnostic_path, diagnostic_span, runtime_logs, write_diagnostic  # noqa: E402
from app.image_generation import build_safe_image_request, generate_safe_image  # noqa: E402
from app.main import app  # noqa: E402
from app.models import CalendarEvent, Character, DeviceRegistration, Experience, MediaAsset, Memory, Message, Moment, MomentInteraction, OpeningCache, ProactiveDeliveryAttempt, ProactiveEvent, ProviderConfig, RelationState, ScheduleSlot, TouchReactionPool, TrendRadarSnapshot, TtsVoiceProfile, User, UserCommitment, UserLocation, WeatherSnapshot  # noqa: E402
from app.news import dispatch_trend_radar_workflow, sync_trend_radar_snapshot, trend_radar_payload_for_news  # noqa: E402
from app.online import clear_online_state, is_online, mark_offline, mark_online  # noqa: E402
from app.opening import consume_ready_opening, prepare_due_openings, prepare_opening  # noqa: E402
from app.pipeline import _normalize_line_text, _split_expression_tag, _tts_for_line, handle_event  # noqa: E402
from app.opening import _instant_greeting_payload  # noqa: E402
from app.touch_reactions import (  # noqa: E402
    _touch_prompt,
    _tier_style_guidance,
    consume_touch_reaction,
    list_touch_pool_admin,
    refresh_touch_reaction_pools,
    touch_pool_coverage_admin,
)
from app.proactive import consume_proactive_event, create_proactive_event, ensure_news_candidate, ensure_proactive_event_image, pending_proactive_response  # noqa: E402
from app.providers import ImageProvider, ProviderError, VolcArkWebSearchClient, VolcSeedTtsClient, get_enabled_provider, get_task_llm_provider, provider_presets, upsert_provider  # noqa: E402
from app.push import register_device, send_proactive_push  # noqa: E402
from app.schedule import ensure_schedule, mark_interruption, run_daily_cycle  # noqa: E402
from app.schemas import DialogueLine, EventIn, ProviderConfigIn  # noqa: E402
from app.seed import ensure_seed  # noqa: E402
from app.utils import dump_json, uid  # noqa: E402
from app.vector_memory import safe_index_memory_vector, search_memory_vectors  # noqa: E402


client = TestClient(app)


def setup_module() -> None:
    init_db()
    with SessionLocal() as session:
        ensure_seed(session)


def _clear_diagnostics() -> None:
    path = diagnostic_path()
    if path.exists():
        path.unlink()


def _append_diagnostic_raw(item: dict[str, object]) -> None:
    path = diagnostic_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")


def _llm_json_response(payload: dict[str, object]) -> httpx.Response:
    content = json.dumps(payload, ensure_ascii=False)
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})


def _disable_embedding_providers() -> None:
    with SessionLocal() as session:
        for config in session.execute(select(ProviderConfig).where(ProviderConfig.kind == "embedding")).scalars():
            config.enabled = False
        session.commit()


def test_health_and_bootstrap() -> None:
    assert client.get("/api/health").json()["ok"] is True
    payload = client.get("/api/bootstrap").json()
    assert payload["character"]["character_id"] == "atri"
    assert payload["character"]["name"] == "亚托莉"
    assert payload["live2d"]["appearance_id"] == "neko"
    assert payload["live2d"]["touch_pool_version"]
    assert "providers" not in payload
    with SessionLocal() as session:
        assert session.get(Character, "atri") is not None
        assert session.get(Character, "sakura") is None


def test_appearance_id_does_not_create_character() -> None:
    payload = client.get("/api/bootstrap", params={"character_id": "neko", "appearance_id": "neko"}).json()
    assert payload["character"]["character_id"] == "atri"
    assert client.put("/api/admin/characters/neko", json={"name": "亚托莉"}).status_code == 404
    with SessionLocal() as session:
        ensure_seed(session, character_id="neko")
        assert session.get(Character, "atri") is not None
        assert session.get(Character, "neko") is None
        assert session.get(Character, "murasame") is None


def test_user_active_character_drives_bootstrap_when_character_is_omitted() -> None:
    user_id = f"active_character_{uid('test')}"
    with SessionLocal() as session:
        ensure_seed(session, user_id=user_id, character_id="atri")
        atri = session.get(Character, "atri")
        assert atri is not None
        if session.get(Character, "miyu") is None:
            session.add(
                Character(
                    character_id="miyu",
                    name="小鸟游弥柚",
                    persona_prompt=atri.persona_prompt,
                    persona_card_json=atri.persona_card_json,
                    speech_style=atri.speech_style,
                    relationship_boundary=atri.relationship_boundary,
                )
            )
            session.commit()

    relation = client.put(f"/api/admin/users/{user_id}/relation", json={"character_id": "miyu", "affection": 234}).json()
    assert relation["character_id"] == "miyu"
    users_page = client.get(f"/api/admin/users?q={user_id}&page=1&page_size=5").json()
    user = next(item for item in users_page["items"] if item["user_id"] == user_id)
    assert user["active_character_id"] == "miyu"

    boot = client.get("/api/bootstrap", params={"user_id": user_id, "appearance_id": "neko"}).json()
    assert boot["character"]["character_id"] == "miyu"
    assert boot["relation"]["affection"] == 234


def test_schema_has_persona_profile_and_vector_columns() -> None:
    with SessionLocal() as session:
        def columns(table: str) -> set[str]:
            return {str(row[1]) for row in session.connection().exec_driver_sql(f"PRAGMA table_info({table})").all()}

        assert "profile_json" in columns("users")
        assert "active_character_id" in columns("users")
        assert "persona_card_json" in columns("characters")
        memory_columns = columns("memories")
        assert {"tags_json", "metadata_json", "vector_status", "vector_updated_at"}.issubset(memory_columns)
        message_columns = columns("messages")
        assert {"source_event_id", "request_fingerprint"}.issubset(message_columns)


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


def test_admin_static_moves_proactive_debug_out_of_user_data() -> None:
    html = client.get("/admin").text
    users_section = html.split('data-page="users"', 1)[1].split('data-page="providers"', 1)[0]
    debug_section = html.split('data-page="debug"', 1)[1]
    assert 'id="proactiveEditor"' not in users_section
    assert 'id="proactiveEditor"' in debug_section
    assert 'id="proactiveDebugManager"' in debug_section


def test_admin_static_exposes_user_character_switcher() -> None:
    script = client.get("/admin/assets/admin.js").text
    user_data_script = script.split("function activeUser()", 1)[1].split("const LIVE2D_AREA_COLORS", 1)[0]
    assert "activeUserCharacterId" in user_data_script
    assert 'id="userCharacterSelect"' in user_data_script
    assert "selectedUserCharacterId()" in user_data_script
    assert 'character_id: "atri"' not in user_data_script
    assert 'value="atri"' not in user_data_script


def test_runtime_logs_redact_and_reconstruct_spans() -> None:
    _clear_diagnostics()
    with diagnostic_span(
        "unit_span",
        feature="测试模块",
        stage="unit",
        purpose="secret check",
        summary="runtime secret check",
        request={"headers": {"Authorization": "Bearer should-not-leak"}, "api_key": "secret-key", "messages": [{"content": "hello"}]},
    ) as span:
        write_diagnostic("unit_decision", feature="测试模块", stage="judge", decision={"reason": "ok"})
        with diagnostic_span("unit_child", feature="测试模块", stage="child", summary="nested child") as child:
            child.add(output={"child": True})
        span.add(response={"reply": "done", "token": "hidden-token"})
    write_diagnostic("unit_running", phase="start", status="running", span_id="span_running_test", feature="测试模块", stage="running", summary="still running")

    payload = runtime_logs(limit=20, feature="测试模块")
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "Bearer should-not-leak" not in serialized
    assert "secret-key" not in serialized
    assert "hidden-token" not in serialized
    assert '"Authorization": "***"' in serialized
    assert '"api_key": "***"' in serialized
    assert '"token": "***"' in serialized
    completed = next(item for item in payload["items"] if item["event"] == "unit_span")
    child = next(item for item in payload["items"] if item["event"] == "unit_child")
    assert completed["status"] == "ok"
    assert completed["elapsed_ms"] >= 0
    assert child["parent_id"] == completed["id"]
    assert child["id"] in completed["children_ids"]
    trace = next(trace for trace in payload["traces"] if trace["trace_id"] == completed["trace_id"])
    assert any(node["id"] == child["id"] and node["parent_id"] == completed["id"] for node in trace["nodes"])
    running = next(item for item in payload["items"] if item["event"] == "unit_running")
    assert running["status"] == "running"
    assert running["running"] is True


def test_admin_runtime_logs_route_filters() -> None:
    _clear_diagnostics()
    write_diagnostic("route_filter_test", feature="过滤模块", stage="api", summary="needle trace", trace_id="trace_filter_case")
    payload = client.get("/api/admin/runtime-logs?feature=%E8%BF%87%E6%BB%A4%E6%A8%A1%E5%9D%97&q=needle&limit=5").json()
    assert payload["ok"] is True
    assert payload["total"] == 1
    assert payload["items"][0]["event"] == "route_filter_test"
    assert "过滤模块" in payload["features"]


def test_runtime_logs_treat_expected_skips_as_ok() -> None:
    _clear_diagnostics()
    write_diagnostic("proactive_judge_skipped", feature="开场预热", reason="no_due_event", user_id="demo_user")
    write_diagnostic("tts_translate_rejected", feature="TTS", reason="invalid_language")

    payload = runtime_logs(limit=20)
    skipped = next(item for item in payload["items"] if item["event"] == "proactive_judge_skipped")
    rejected = next(item for item in payload["items"] if item["event"] == "tts_translate_rejected")
    assert skipped["status"] == "ok"
    assert rejected["status"] == "warn"


def test_runtime_logs_legacy_cache_before_and_flow() -> None:
    _clear_diagnostics()
    _append_diagnostic_raw({"ts": 1000.0, "event": "legacy_old", "summary": "older legacy"})
    _append_diagnostic_raw({"ts": 2000.0, "event": "legacy_new", "feature": "旧模块", "summary": "newer legacy"})
    _append_diagnostic_raw({"ts": 2100.0, "event": "weather_cache_hit", "feature": "天气服务", "stage": "ensure_weather_snapshot", "summary": "cache", "cache_hit": True})
    with diagnostic_span("flow_one", feature="流程A", stage="input", trace_id="trace_flow_case", input={"text": "hello"}) as span:
        span.add(output={"step": "one"})
        with diagnostic_span("flow_two", feature="流程B", stage="output", trace_id="trace_flow_case", input={"weather": "sunny"}) as child_span:
            child_span.add(output={"reply": "done"})

    payload = runtime_logs(limit=20, include_cache_hits=False)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "weather_cache_hit" not in serialized
    assert payload["legacy_count"] >= 2
    assert "旧诊断日志" in payload["features"]
    legacy_trace = next(item for item in payload["traces"] if item["trace_id"] == "legacy")
    assert legacy_trace["is_legacy"] is True
    assert any(item["event"] == "legacy_new" for item in legacy_trace["items"])
    flow = next(item for item in payload["flows"] if item["trace_id"] == "trace_flow_case")
    assert len(flow["nodes"]) == 2
    assert flow["edges"][0]["from"] == flow["nodes"][0]["id"]
    trace = next(item for item in payload["traces"] if item["trace_id"] == "trace_flow_case")
    assert len(trace["items"]) == 2
    first = next(item for item in payload["items"] if item["event"] == "flow_one")
    second = next(item for item in payload["items"] if item["event"] == "flow_two")
    assert first["next_id"] == second["id"]
    assert second["prev_id"] == first["id"]
    assert second["parent_id"] == first["id"]
    assert second["id"] in first["children_ids"]
    assert trace["edges"][0]["from"] == first["id"]
    assert trace["edges"][0]["to"] == second["id"]
    assert "hello" in first["input_summary"]
    assert "done" in second["output_summary"]

    with_cache = runtime_logs(limit=20, include_cache_hits=True)
    assert any(item["event"] == "weather_cache_hit" and item["is_cache_hit"] for item in with_cache["items"])
    older = runtime_logs(limit=20, before_ts=1500.0, include_cache_hits=True)
    assert [item["event"] for item in older["items"]] == ["legacy_old"]


def test_online_tracker_and_prewarm_interval() -> None:
    clear_online_state()
    assert is_online("online_user") is False
    mark_online("online_user", "phone")
    mark_online("online_user", "tablet")
    assert is_online("online_user") is True
    mark_offline("online_user", "phone")
    assert is_online("online_user") is True
    mark_offline("online_user", "tablet")
    assert is_online("online_user") is False
    assert scheduler_module._prewarm_interval_minutes(1) == 5
    assert scheduler_module._prewarm_interval_minutes(15) == 15
    assert scheduler_module._prewarm_interval_minutes(30) == 30
    assert scheduler_module._prewarm_interval_minutes("bad") == 15


def test_prewarm_once_skips_online_and_fresh_cache() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    online_user_id = f"prewarm_online_{suffix}"
    cache_user_id = f"prewarm_cache_{suffix}"
    clear_online_state()
    with SessionLocal() as session:
        ensure_seed(session, user_id=online_user_id, character_id="atri")
        ensure_seed(session, user_id=cache_user_id, character_id="atri")
        for user_id in (online_user_id, cache_user_id):
            user = session.get(User, user_id)
            user.story_completed = True
            user.notifications_enabled = True
        session.add(
            OpeningCache(
                cache_id=f"opening_cache_{suffix}",
                user_id=cache_user_id,
                character_id="atri",
                kind="proactive",
                payload_json=dump_json({"lines": [], "relation_delta": {}}),
                status="ready",
                expires_at="2099-01-01T00:00:00+00:00",
            )
        )
        session.commit()

        mark_online(online_user_id, "phone")
        online_result = scheduler_module._prewarm_once(session, user_ids={online_user_id})
        assert online_result["checked"] == 1
        assert online_result["skipped_online"] == 1
        mark_offline(online_user_id, "phone")

        cache_result = scheduler_module._prewarm_once(session, user_ids={cache_user_id})
        assert cache_result["checked"] == 1
        assert cache_result["skipped_cache"] == 1
    clear_online_state()


def test_prewarm_once_does_not_skip_judge_for_greeting_cache() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"prewarm_greeting_cache_{suffix}"
    event_id = ""
    judge_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal judge_calls
        body = json.loads(request.content.decode())
        if "Context JSON:" in body["messages"][1]["content"]:
            judge_calls += 1
            return _llm_json_response(
                {
                    "should_send": True,
                    "selected_event_id": event_id,
                    "reason": "普通开场缓存不能阻止主动消息判断。",
                    "next_check_after_minutes": 60,
                }
            )
        return _llm_json_response(
            {
                "reply_mode": "normal",
                "lines": [{"text": "这条主动消息已经提前准备好了。", "emotion": "happy", "pose": "happy"}],
                "normal_replies": [],
                "key_reply_score": 0,
                "key_replies": [],
                "relation_delta": {"affection": 0, "trust": 0, "dependency": 0, "mood": 0},
                "memory_candidates": [],
                "interest_topics": [],
            }
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        clear_online_state()
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_prewarm_greeting_cache_llm_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.notifications_enabled = True
            user.tts_enabled = False
            session.add(
                OpeningCache(
                    cache_id=f"greeting_cache_{suffix}",
                    user_id=user_id,
                    character_id="atri",
                    kind="greeting",
                    payload_json=dump_json({"lines": [], "relation_delta": {}}),
                    status="ready",
                    expires_at="2099-01-01T00:00:00+00:00",
                )
            )
            event = create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="memory",
                source_id=f"prewarm_greeting_memory_{suffix}",
                title="亚托莉有话想说",
                text="主动想告诉用户：普通开场缓存不应该挡住这个候选。",
                priority=90,
                scheduled_at=datetime.fromisoformat("2026-06-09T11:50:00+08:00"),
            )
            assert event is not None
            event_id = event.proactive_event_id
            session.commit()

            result = scheduler_module._prewarm_once(session, user_ids={user_id})
            assert result["checked"] == 1
            assert result["skipped_cache"] == 0
            assert result["prepared"] == 1
            assert judge_calls == 1
            session.refresh(event)
            assert event.prepared_at
    finally:
        providers.HTTP_TRANSPORT = None
        clear_online_state()


def test_prewarm_once_offline_without_cache_runs_weather_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"prewarm_offline_{suffix}"
    calls: list[dict[str, object]] = []

    def fake_prepare_due_openings(session: SessionLocal, **kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {"ok": True, "checked": 1, "prepared": 0, "skipped": 1, "failed": 0}

    monkeypatch.setattr(scheduler_module, "prepare_due_openings", fake_prepare_due_openings)
    clear_online_state()
    with SessionLocal() as session:
        ensure_seed(session, user_id=user_id, character_id="atri")
        user = session.get(User, user_id)
        user.story_completed = True
        user.notifications_enabled = True
        session.commit()

        result = scheduler_module._prewarm_once(session, user_ids={user_id})
        assert result["checked"] == 1
        assert result["skipped_online"] == 0
        assert result["skipped_cache"] == 0
        assert result["skipped_no_event"] == 1
        assert calls
        assert calls[0]["generate_weather"] is True
        assert calls[0]["generate_news"] is False


def test_llm_diagnostic_records_request_and_response() -> None:
    _clear_diagnostics()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["messages"][1]["content"] == "return json"
        content = json.dumps({"ok": True, "reply": "recorded"}, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_diag_llm",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            result = providers.OpenAICompatibleClient(config).chat_json(
                [{"role": "system", "content": "json only"}, {"role": "user", "content": "return json"}],
                diagnostic={
                    "feature": "LLM测试",
                    "stage": "chat_json",
                    "purpose": "record request",
                    "input": {"case": "diagnostic", "references": {"weather": {"used": True, "summary": "sunny"}}},
                    "references": {"weather": {"used": True, "summary": "sunny"}},
                },
            )
            assert result["ok"] is True
    finally:
        providers.HTTP_TRANSPORT = None

    payload = runtime_logs(limit=20, feature="LLM测试")
    llm_span = next(item for item in payload["items"] if item["event"] == "llm_request")
    assert llm_span["status"] == "ok"
    assert llm_span["details"]["start"]["request"]["messages"][1]["content"] == "return json"
    assert llm_span["details"]["start"]["references"]["weather"]["used"] is True
    assert "天气" in llm_span["references_summary"]
    assert llm_span["details"]["end"]["response"]["reply"] == "recorded"


def test_llm_json_request_enforces_deepseek_json_output_contract() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        bodies.append(body)
        content = json.dumps({"ok": True}, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_deepseek_json_contract",
                    kind="llm_task",
                    provider="deepseek",
                    base_url="https://api.deepseek.com",
                    model="deepseek-chat",
                    secrets={"api_key": "deepseek-key"},
                    metadata={"extra_body": {"response_format": {"type": "text"}, "max_tokens": 4096, "top_p": 0.5}},
                ),
            )
            result = providers.OpenAICompatibleClient(config).chat_json(
                [{"role": "system", "content": "be concise"}, {"role": "user", "content": "return object"}],
                max_tokens=4096,
            )
            assert result["ok"] is True
    finally:
        providers.HTTP_TRANSPORT = None

    body = bodies[0]
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 4096
    assert body["top_p"] == 0.5
    assert body["messages"][1]["content"] == "return object"
    system_prompt = body["messages"][0]["content"]
    assert "json" in system_prompt.lower()
    assert '{"ok": true}' in system_prompt


def test_llm_json_retries_empty_content_from_json_output_mode() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(
                200,
                json={"choices": [{"finish_reason": "stop", "message": {"content": "", "reasoning_content": "thinking"}}]},
            )
        content = json.dumps({"ok": True, "reply": "retried"}, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_deepseek_json_empty_retry",
                    kind="llm_task",
                    provider="deepseek",
                    base_url="https://api.deepseek.com",
                    model="deepseek-chat",
                    secrets={"api_key": "deepseek-key"},
                ),
            )
            result = providers.OpenAICompatibleClient(config).chat_json(
                [{"role": "system", "content": "json only"}, {"role": "user", "content": "return json"}],
                max_tokens=4096,
            )
            assert result["reply"] == "retried"
    finally:
        providers.HTTP_TRANSPORT = None

    assert len(bodies) == 2
    assert bodies[0]["response_format"] == {"type": "json_object"}
    assert bodies[1]["max_tokens"] == 8192
    retry_prompt = bodies[1]["messages"][-1]["content"]
    assert "previous response" in retry_prompt.lower()
    assert "<empty content>" in retry_prompt


def test_llm_text_retries_empty_content_from_length_finish() -> None:
    bodies: list[dict[str, object]] = []
    ja_text = "\u8a71\u3057\u3066\u307f\u3066\u3002"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(
                200,
                json={"choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content": "thinking"}}]},
            )
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": ja_text}}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_deepseek_text_length_retry",
                    kind="llm_task",
                    provider="deepseek",
                    base_url="https://api.deepseek.com",
                    model="deepseek-reasoner",
                    secrets={"api_key": "deepseek-key"},
                ),
            )
            result = providers.OpenAICompatibleClient(config).chat_text(
                [
                    {"role": "system", "content": "Output only Japanese."},
                    {"role": "user", "content": "Chinese line: \u8bf4\u5427\u3002"},
                ],
                max_tokens=4096,
            )
            assert result == ja_text
    finally:
        providers.HTTP_TRANSPORT = None

    assert len(bodies) == 2
    assert "response_format" not in bodies[0]
    assert bodies[0]["max_tokens"] == 4096
    assert bodies[1]["max_tokens"] == 4096


def test_user_message_trace_records_reply_judgement() -> None:
    _clear_diagnostics()

    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(
            {
                "reply_mode": "normal",
                "pace_reason": "用户直接聊天，需要正常回复",
                "lines": [{"text": "我听见了。", "emotion": "calm", "pose": "idle"}],
                "normal_replies": [{"text": "继续说"}],
                "key_reply_score": 12,
                "key_reply_reason": "普通聊天，不需要特殊回复",
                "key_replies": [],
                "relation_delta": {"affection": 0, "trust": 0, "dependency": 0, "mood": 0},
                "memory_candidates": [{"layer": "chat", "content": "用户说今天想测试日志", "importance": 0.5, "confidence": 0.8}],
                "interest_topics": [],
            },
            ensure_ascii=False,
        )
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id="trace_reply_user", character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_trace_reply_llm",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, "trace_reply_user")
            assert user is not None
            user.story_completed = True
            user.tts_enabled = False
            session.commit()
            result = handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id="trace_reply_user",
                    character_id="atri",
                    session_id="trace_reply_session",
                    payload={"text": "今天想测试日志"},
                    client_context={"local_time": "2026-06-10T10:15:00+08:00"},
                ),
            )
            assert result.event_type == "dialogue"
    finally:
        providers.HTTP_TRANSPORT = None

    payload = runtime_logs(limit=50, feature="回复模块")
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "reply_trace" in serialized
    for stage in ("save_user_message", "context_build", "llm_dialogue", "payload_build", "tts_lines", "side_effects", "save_dialogue", "commit"):
        assert stage in serialized
    assert "reply_context_ready" in serialized
    assert "reply_llm_judgement" in serialized
    assert "reply_output_ready" in serialized
    assert "用户直接聊天，需要正常回复" in serialized
    assert '"references"' in serialized
    for key in (
        "user_input",
        "schedule",
        "character_schedule",
        "user_schedule",
        "weather",
        "persona",
        "user_profile",
        "relation_attitude",
        "memory",
        "event_memory",
        "recent_dialogue",
        "moment_interactions",
        "gate",
        "subject_hint",
    ):
        assert key in serialized
    assert "memory_writes" in serialized
    reply_span = next(item for item in payload["items"] if item["event"] == "reply_trace")
    child_spans = [item for item in payload["items"] if item.get("parent_id") == reply_span["id"]]
    assert child_spans
    assert reply_span["elapsed_ms"] >= max(item["elapsed_ms"] for item in child_spans)
    trace = next(trace for trace in payload["traces"] if trace["trace_id"] == reply_span["trace_id"])
    assert any(node["id"] == reply_span["id"] for node in trace["nodes"])
    assert any(item["stage"] == "context_build" for item in trace["items"])


def test_provider_presets() -> None:
    payload = client.get("/api/config/provider-presets").json()
    assert payload["llm"][0]["provider"] == "volc_ark"
    assert [item["provider"] for item in payload["llm_task"]] == ["volc_ark", "deepseek", "openai_compatible"]
    embedding_fields = {item["name"] for item in payload["embedding"][0]["fields"]}
    assert payload["embedding"][0]["provider"] == "openai_compatible"
    assert {"base_url", "model", "api_key", "batch_size", "dimensions"}.issubset(embedding_fields)
    tts_fields = {item["name"] for item in payload["tts"][0]["fields"]}
    assert {"credential_mode", "parameter_mode", "resource_id", "speaker", "x_api_key", "access_key", "emotion_map"}.issubset(tts_fields)
    search_by_provider = {item["provider"]: item for item in payload["search"]}
    assert {"trend_radar", "volc_ark_web_search"}.issubset(search_by_provider)
    trend_fields = {item["name"] for item in search_by_provider["trend_radar"]["fields"]}
    assert {"cache_minutes", "max_titles", "github_token", "github_workflow_id", "github_ref", "timeout"}.issubset(trend_fields)
    assert search_by_provider["trend_radar"]["supports_models"] is False
    search_fields = {item["name"] for item in search_by_provider["volc_ark_web_search"]["fields"]}
    assert {"max_keyword", "limit", "max_tool_calls", "user_location"}.issubset(search_fields)
    assert search_by_provider["volc_ark_web_search"]["supports_models"] is True
    weather_fields = {item["name"] for item in payload["weather"][0]["fields"]}
    assert {"auth_mode", "key_id", "project_id", "private_key", "api_key", "include_warning", "include_minutely"}.issubset(weather_fields)
    assert [item["provider"] for item in payload["image"]] == ["doubao_seedream", "openai_gpt_image", "gemini_image"]
    assert "supports_web_search" not in str(payload)


def test_embedding_provider_test_uses_openai_compatible_embeddings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://embedding.example/v1/embeddings"
        body = json.loads(request.content.decode())
        assert body["model"] == "embed-test"
        assert body["input"]
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        payload = client.post(
            "/api/config/providers/test",
            json={
                "provider_id": "test_embedding_provider",
                "kind": "embedding",
                "provider": "openai_compatible",
                "base_url": "https://embedding.example/v1",
                "model": "embed-test",
                "api_key": "embed-key",
                "metadata": {"dimensions": 3},
                "test_text": "用户喜欢咖啡",
            },
        ).json()
        assert payload["ok"] is True
        assert payload["details"]["dimensions"] == 3
    finally:
        providers.HTTP_TRANSPORT = None
        _disable_embedding_providers()


def test_qdrant_vector_memory_upsert_search_and_filter() -> None:
    suffix = uid("vec")
    user_id = f"vector_user_{suffix}"

    def vector_for(text: str) -> list[float]:
        return [1.0, 0.0, 0.0] if "咖啡" in text else [0.0, 1.0, 0.0]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        inputs = body["input"]
        if isinstance(inputs, str):
            inputs = [inputs]
        return httpx.Response(
            200,
            json={"data": [{"index": index, "embedding": vector_for(str(text))} for index, text in enumerate(inputs)]},
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"embedding_{suffix}",
                    kind="embedding",
                    provider="openai_compatible",
                    base_url="https://embedding.example/v1",
                    model="embed-test",
                    secrets={"api_key": "embed-key"},
                ),
            )
            memory = Memory(
                memory_id=f"mem_{suffix}",
                user_id=user_id,
                character_id="atri",
                layer="event",
                content="用户说周末想去咖啡店。",
                tags_json=dump_json(["plan"]),
                metadata_json=dump_json({"kind": "event"}),
                importance=0.8,
                confidence=0.9,
            )
            other = Memory(
                memory_id=f"mem_other_{suffix}",
                user_id=f"other_{user_id}",
                character_id="atri",
                layer="event",
                content="其他用户也喜欢咖啡。",
                importance=0.8,
                confidence=0.9,
            )
            session.add_all([memory, other])
            session.flush()
            assert safe_index_memory_vector(session, memory)["status"] == "ready"
            assert safe_index_memory_vector(session, other)["status"] == "ready"
            hits = search_memory_vectors(session, user_id=user_id, character_id="atri", query_text="咖啡安排", layers={"event"}, limit=5)
            assert hits
            assert hits[0].memory_id == memory.memory_id
            assert all(hit.memory_id != other.memory_id for hit in hits)
            config = session.get(ProviderConfig, f"embedding_{suffix}")
            assert config is not None
            config.enabled = False
            session.commit()
    finally:
        providers.HTTP_TRANSPORT = None


def _trend_radar_payload() -> dict[str, object]:
    return {
        "generated_at": "2026-06-10T12:00:00+08:00",
        "total_titles_processed": 12,
        "failed_sources": [],
        "trends": [
            {
                "keyword_group": "AI 游戏",
                "match_count": 6,
                "titles": [
                    {
                        "title": "AI 游戏原型工具登上热榜",
                        "url": "https://trend.example/ai-game",
                        "source": "知乎",
                        "ranks": [2, 4],
                        "is_new": True,
                        "appearance_count": 3,
                        "time_info": "12时00分",
                    },
                    {
                        "title": "独立游戏团队讨论 AI 角色",
                        "url": "https://trend.example/ai-character",
                        "source": "B站",
                        "ranks": [7],
                        "is_new": False,
                        "appearance_count": 1,
                        "time_info": "11时30分",
                    },
                ],
            },
            {
                "keyword_group": "芯片",
                "match_count": 4,
                "titles": [
                    {
                        "title": "芯片公司发布新品",
                        "url": "https://trend.example/chip",
                        "source": "百度",
                        "ranks": [3],
                        "is_new": True,
                        "appearance_count": 2,
                        "time_info": "12时10分",
                    }
                ],
            },
        ],
    }


def test_trend_radar_provider_test_reads_trends() -> None:
    providers._TREND_RADAR_CACHE.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://trend.example/api/trends.json"
        return httpx.Response(200, json=_trend_radar_payload())

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        response = client.post(
            "/api/config/providers/test",
            json={
                "provider_id": "test_trend_radar_provider",
                "kind": "search",
                "provider": "trend_radar",
                "label": "TrendRadar Test",
                "base_url": "https://trend.example",
                "metadata": {"cache_minutes": 0, "max_titles": 2, "timeout": 10},
            },
        ).json()
        assert response["ok"] is True
        assert response["provider"] == "trend_radar"
        assert response["details"]["trend_count"] == 2
        assert response["details"]["first_title"]["title"] == "AI 游戏原型工具登上热榜"
    finally:
        providers.HTTP_TRANSPORT = None
        providers._TREND_RADAR_CACHE.clear()


def _trend_radar_sqlite_bytes() -> bytes:
    path = os.path.abspath("backend/.test-data/trend_radar_sample.db")
    if os.path.exists(path):
        os.unlink(path)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE platforms (id TEXT PRIMARY KEY, name TEXT NOT NULL, is_active INTEGER DEFAULT 1, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE news_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            platform_id TEXT NOT NULL,
            rank INTEGER NOT NULL,
            url TEXT DEFAULT '',
            mobile_url TEXT DEFAULT '',
            first_crawl_time TEXT NOT NULL,
            last_crawl_time TEXT NOT NULL,
            crawl_count INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE rank_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_item_id INTEGER NOT NULL,
            rank INTEGER NOT NULL,
            crawl_time TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute("INSERT INTO platforms(id, name) VALUES('zhihu', '知乎')")
    conn.execute(
        """
        INSERT INTO news_items(title, platform_id, rank, url, first_crawl_time, last_crawl_time, crawl_count)
        VALUES('AI 游戏工具登上热榜', 'zhihu', 2, 'https://example.com/ai-game', '12-00', '13-00', 2)
        """
    )
    conn.execute("INSERT INTO rank_history(news_item_id, rank, crawl_time) VALUES(1, 2, '12-00')")
    conn.execute("INSERT INTO rank_history(news_item_id, rank, crawl_time) VALUES(1, 1, '13-00')")
    conn.commit()
    conn.close()
    with open(path, "rb") as fh:
        return fh.read()


def test_trend_radar_provider_test_reads_github_sqlite_output() -> None:
    providers._TREND_RADAR_CACHE.clear()
    db_bytes = _trend_radar_sqlite_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://api.github.com/repos/PrinzEugen8/AI_news/contents/output/news?ref=master":
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "2026-06-10.db",
                        "download_url": "https://raw.githubusercontent.com/PrinzEugen8/AI_news/master/output/news/2026-06-10.db",
                    }
                ],
            )
        assert url == "https://raw.githubusercontent.com/PrinzEugen8/AI_news/master/output/news/2026-06-10.db"
        return httpx.Response(200, content=db_bytes)

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        response = client.post(
            "/api/config/providers/test",
            json={
                "provider_id": "test_trend_radar_github_provider",
                "kind": "search",
                "provider": "trend_radar",
                "label": "TrendRadar GitHub Test",
                "base_url": "https://github.com/PrinzEugen8/AI_news",
                "metadata": {"cache_minutes": 0, "max_titles": 2, "timeout": 10},
            },
        ).json()
        assert response["ok"] is True
        assert response["details"]["trend_count"] == 1
        assert response["details"]["first_title"]["title"] == "AI 游戏工具登上热榜"
    finally:
        providers.HTTP_TRANSPORT = None
        providers._TREND_RADAR_CACHE.clear()


def test_trend_radar_snapshot_sync_uses_dated_github_raw_db() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    db_bytes = _trend_radar_sqlite_bytes()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        assert "api.github.com" not in url
        assert url == "https://raw.githubusercontent.com/PrinzEugen8/AI_news/master/output/news/2026-06-10.db"
        return httpx.Response(200, content=db_bytes)

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_trend_snapshot_raw_{suffix}",
                    kind="search",
                    provider="trend_radar",
                    base_url="https://github.com/PrinzEugen8/AI_news",
                    metadata={"cache_minutes": 0, "timeout": 10},
                ),
            )
            snapshot = sync_trend_radar_snapshot(
                session,
                config=config,
                local_time=datetime.fromisoformat("2026-06-10T04:00:00+08:00"),
                force=True,
            )
            assert snapshot is not None
            assert snapshot.status == "ok"
            assert snapshot.local_date == "2026-06-10"
            assert snapshot.generated_at == "2026-06-10T13:00:00+08:00"
            assert calls == ["https://raw.githubusercontent.com/PrinzEugen8/AI_news/master/output/news/2026-06-10.db"]
    finally:
        providers.HTTP_TRANSPORT = None


def test_trend_radar_dispatch_runs_workflow_and_records_snapshot() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    db_bytes = _trend_radar_sqlite_bytes()
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        url = str(request.url)
        if request.method == "POST":
            assert url == "https://api.github.com/repos/PrinzEugen8/AI_news/actions/workflows/crawler.yml/dispatches"
            assert request.headers.get("authorization") == "Bearer github-token"
            assert json.loads(request.content.decode()) == {"ref": "master"}
            return httpx.Response(204)
        if "actions/workflows/crawler.yml/runs" in url:
            return httpx.Response(
                200,
                json={
                    "workflow_runs": [
                        {
                            "id": 27307232211,
                            "status": "completed",
                            "conclusion": "success",
                            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                            "html_url": "https://github.com/PrinzEugen8/AI_news/actions/runs/27307232211",
                        }
                    ]
                },
            )
        assert url == "https://raw.githubusercontent.com/PrinzEugen8/AI_news/master/output/news/2026-06-10.db"
        return httpx.Response(200, content=db_bytes)

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_trend_dispatch_{suffix}",
                    kind="search",
                    provider="trend_radar",
                    base_url="https://github.com/PrinzEugen8/AI_news",
                    secrets={"github_token": "github-token"},
                    metadata={"cache_minutes": 0, "timeout": 10, "github_dispatch_poll_seconds": 0},
                ),
            )
            snapshot = dispatch_trend_radar_workflow(
                session,
                config=config,
                local_time=datetime.fromisoformat("2026-06-10T03:00:00+08:00"),
            )
            assert snapshot is not None
            assert snapshot.status == "ok"
            assert snapshot.local_date == "2026-06-10"
            assert snapshot.endpoint.endswith("/output/news/2026-06-10.db")
            assert any(method == "POST" and url.endswith("/dispatches") for method, url in calls)
    finally:
        providers.HTTP_TRANSPORT = None


def test_trend_radar_dispatch_missing_token_skips_without_http() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"dispatch without token must not call GitHub: {request.url}")

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_trend_dispatch_no_token_{suffix}",
                    kind="search",
                    provider="trend_radar",
                    base_url="https://github.com/PrinzEugen8/AI_news",
                    metadata={"cache_minutes": 0, "timeout": 10},
                ),
            )
            assert dispatch_trend_radar_workflow(
                session,
                config=config,
                local_time=datetime.fromisoformat("2026-06-10T03:00:00+08:00"),
            ) is None
    finally:
        providers.HTTP_TRANSPORT = None


def test_trend_radar_404_is_pending_and_can_retry() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    db_bytes = _trend_radar_sqlite_bytes()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(404, text="Not Found")
        return httpx.Response(200, content=db_bytes)

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_trend_404_retry_{suffix}",
                    kind="search",
                    provider="trend_radar",
                    base_url="https://github.com/PrinzEugen8/AI_news",
                    metadata={"cache_minutes": 0, "timeout": 10},
                ),
            )
            first = sync_trend_radar_snapshot(
                session,
                config=config,
                local_time=datetime.fromisoformat("2026-06-10T04:00:00+08:00"),
                force=True,
            )
            second = sync_trend_radar_snapshot(
                session,
                config=config,
                local_time=datetime.fromisoformat("2026-06-10T04:05:00+08:00"),
                force=False,
            )
            assert first is None
            assert second is not None
            assert second.status == "ok"
            assert len(calls) == 2
            errors = session.execute(
                select(TrendRadarSnapshot).where(
                    TrendRadarSnapshot.provider_id == config.provider_id,
                    TrendRadarSnapshot.local_date == "2026-06-10",
                    TrendRadarSnapshot.status == "error",
                )
            ).scalars().all()
            assert errors == []
    finally:
        providers.HTTP_TRANSPORT = None


def test_trend_radar_news_retries_old_404_and_falls_back_to_latest_ok() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404, text="Not Found")

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_trend_old_404_fallback_{suffix}",
                    kind="search",
                    provider="trend_radar",
                    base_url="https://github.com/PrinzEugen8/AI_news",
                    metadata={"cache_minutes": 0, "timeout": 10},
                ),
            )
            session.add(
                TrendRadarSnapshot(
                    snapshot_id=f"trend_old_ok_{suffix}",
                    provider_id=config.provider_id,
                    local_date="2026-06-10",
                    status="ok",
                    generated_at="2026-06-10T12:00:00+08:00",
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    endpoint="https://raw.githubusercontent.com/PrinzEugen8/AI_news/master/output/news/2026-06-10.db",
                    payload_json=json.dumps(_trend_radar_payload(), ensure_ascii=False),
                )
            )
            session.add(
                TrendRadarSnapshot(
                    snapshot_id=f"trend_old_404_{suffix}",
                    provider_id=config.provider_id,
                    local_date="2026-06-11",
                    status="error",
                    generated_at="",
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    endpoint="https://raw.githubusercontent.com/PrinzEugen8/AI_news/master/output/news/2026-06-11.db",
                    error_message="404 Not Found",
                    payload_json="{}",
                )
            )
            session.commit()
            payload = trend_radar_payload_for_news(
                session,
                config=config,
                local_time=datetime.fromisoformat("2026-06-11T04:10:00+08:00"),
            )
            assert payload is not None
            assert payload["generated_at"] == "2026-06-10T12:00:00+08:00"
            assert calls == ["https://raw.githubusercontent.com/PrinzEugen8/AI_news/master/output/news/2026-06-11.db"]
    finally:
        providers.HTTP_TRANSPORT = None


def test_news_candidate_uses_trend_radar_snapshot_without_http() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"unexpected TrendRadar HTTP request: {request.url}")

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            user_id = f"trend_snapshot_user_{suffix}"
            ensure_seed(session, user_id=user_id, character_id="atri")
            user = session.get(User, user_id)
            user.interest_topics_json = json.dumps(["AI 游戏"], ensure_ascii=False)
            user.news_enabled = True
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_trend_snapshot_news_{suffix}",
                    kind="search",
                    provider="trend_radar",
                    base_url="https://trend.example",
                    metadata={"cache_minutes": 0, "max_titles": 2},
                ),
            )
            session.add(
                TrendRadarSnapshot(
                    snapshot_id=f"trend_snapshot_{suffix}",
                    provider_id=config.provider_id,
                    local_date="2026-06-10",
                    status="ok",
                    generated_at="2026-06-10T12:00:00+08:00",
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    endpoint="https://trend.example/api/trends.json",
                    payload_json=json.dumps(_trend_radar_payload(), ensure_ascii=False),
                )
            )
            session.commit()
            event = ensure_news_candidate(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-10T12:15:00+08:00"),
            )
            assert event is not None
            payload = json.loads(event.payload_json)
            assert payload["generated_at"] == "2026-06-10T12:00:00+08:00"
            assert payload["topic"] == "AI 游戏"
    finally:
        providers.HTTP_TRANSPORT = None


def test_trend_radar_lazy_sync_records_error_once_after_window() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(403, text="rate limited")

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_trend_lazy_once_{suffix}",
                    kind="search",
                    provider="trend_radar",
                    base_url="https://github.com/PrinzEugen8/AI_news",
                    metadata={"cache_minutes": 0, "timeout": 10},
                ),
            )
            first = trend_radar_payload_for_news(
                session,
                config=config,
                local_time=datetime.fromisoformat("2026-06-10T04:10:00+08:00"),
            )
            second = trend_radar_payload_for_news(
                session,
                config=config,
                local_time=datetime.fromisoformat("2026-06-10T05:10:00+08:00"),
            )
            assert first is None
            assert second is None
            assert calls == ["https://raw.githubusercontent.com/PrinzEugen8/AI_news/master/output/news/2026-06-10.db"]
            errors = session.execute(
                select(TrendRadarSnapshot).where(
                    TrendRadarSnapshot.provider_id == config.provider_id,
                    TrendRadarSnapshot.local_date == "2026-06-10",
                    TrendRadarSnapshot.status == "error",
                )
            ).scalars().all()
            assert len(errors) == 1
    finally:
        providers.HTTP_TRANSPORT = None


def test_trend_radar_does_not_lazy_sync_before_daily_window() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=_trend_radar_payload())

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_trend_before_window_{suffix}",
                    kind="search",
                    provider="trend_radar",
                    base_url="https://trend.example",
                    metadata={"cache_minutes": 0, "timeout": 10},
                ),
            )
            payload = trend_radar_payload_for_news(
                session,
                config=config,
                local_time=datetime.fromisoformat("2026-06-10T03:59:00+08:00"),
            )
            assert payload is None
            assert calls == []
    finally:
        providers.HTTP_TRANSPORT = None


def test_task_llm_prefers_task_provider_and_falls_back_to_chat_provider() -> None:
    with SessionLocal() as session:
        chat = upsert_provider(
            session,
            ProviderConfigIn(
                provider_id="test_task_fallback_llm",
                kind="llm",
                provider="deepseek",
                base_url="https://api.deepseek.com",
                model="chat-model",
            ),
        )
        task = upsert_provider(
            session,
            ProviderConfigIn(
                provider_id="test_task_primary_llm",
                kind="llm_task",
                provider="deepseek",
                base_url="https://api.deepseek.com",
                model="task-model",
            ),
        )
        selected = get_task_llm_provider(session)
        assert selected is not None
        assert selected.provider_id == task.provider_id
        task.enabled = False
        session.commit()
        selected = get_task_llm_provider(session)
        assert selected is not None
        assert selected.provider_id == chat.provider_id


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
        ensure_seed(session, user_id="seed_moment_user", character_id="atri")
        seed_ids = {"seed_moment_atri_morning", "seed_moment_atri_walk", "seed_moment_atri_evening"}
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
        "/api/admin/characters/atri",
        json={
            "tts_voice_profile_id": voice_id,
            "key_reply_threshold": 82,
            "persona_card": {
                "name": "亚托莉",
                "personality": ["认真", "温柔"],
                "relationship_attitudes": {
                    "good": "会更主动分享日程。",
                    "neutral": "保持自然陪伴。",
                    "bad": "先保持距离。",
                },
            },
        },
    ).json()
    assert updated["tts_voice_profile_id"] == voice_id
    assert updated["key_reply_threshold"] == 82
    assert updated["persona_card"]["personality"] == ["认真", "温柔"]
    assert updated["persona_card"]["relationship_attitudes"]["good"] == "会更主动分享日程。"

    assert client.delete(f"/api/admin/tts-voices/{voice_id}").json()["ok"] is True
    characters = client.get("/api/admin/characters").json()["items"]
    atri = next(item for item in characters if item["character_id"] == "atri")
    assert atri["tts_voice_profile_id"] == ""


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
            ensure_seed(session, user_id="tts_profile_user", character_id="atri")
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
            character = session.get(Character, "atri")
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
            ensure_seed(session, user_id="tts_profile_ja_user", character_id="atri")
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
            character = session.get(Character, "atri")
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
            ensure_seed(session, user_id="tts_profile_ja_reject_user", character_id="atri")
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
            character = session.get(Character, "atri")
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
                has_retry_hint = "上一次输出没有通过日文配音校验" in body["messages"][1]["content"]
                content = json.dumps(
                    {
                        "reply_mode": "normal",
                        "lines": [
                            {
                                "text": cn_text,
                                "emotion": "calm",
                                "pose": "idle",
                                **({"tts_text_ja": ja_text} if has_retry_hint else {}),
                            }
                        ],
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
        if "embeddings" in url:
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})
        tts_bodies.append(body)
        assert request.headers["X-Api-Resource-Id"] == resource_id
        return httpx.Response(200, content=f'data: {{"data":"{audio}"}}\n'.encode())

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
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
            character = session.get(Character, "atri")
            user.story_completed = True
            user.tts_enabled = True
            character.tts_voice_profile_id = f"voice_ja_retry_{suffix}"
            session.commit()

            result = handle_event(
                session,
                EventIn(event_type="user_message", user_id=user_id, character_id="atri", session_id=session_id, payload={"text": "我回来了"}),
            )
            assert result.payload["lines"][0]["text"] == cn_text
            assert len(llm_bodies) == 2
            assert "response_format" in llm_bodies[0]
            assert "response_format" in llm_bodies[1]
            assert "上一次输出没有通过日文配音校验" in llm_bodies[1]["messages"][1]["content"]
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
            ensure_seed(session, user_id=user_id, character_id="atri")
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
            character = session.get(Character, "atri")
            user.story_completed = True
            user.tts_enabled = True
            character.tts_voice_profile_id = voice_id
            session.commit()

            with pytest.raises(ProviderError, match="日文配音文本生成失败"):
                handle_event(
                    session,
                    EventIn(event_type="user_message", user_id=user_id, character_id="atri", session_id=session_id, payload={"text": "我回来了"}),
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


def test_qweather_provider_uses_api_key_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["api_key"] = request.headers.get("X-QW-Api-Key", "")
        assert "key=qweather-key" not in str(request.url)
        return httpx.Response(
            200,
            json={
                "code": "200",
                "updateTime": "2026-06-10T10:00+08:00",
                "now": {"obsTime": "2026-06-10T09:55+08:00", "temp": "27", "text": "多云"},
            },
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        payload = client.post(
            "/api/config/providers/test",
            json={
                "provider_id": "test_qweather_provider",
                "kind": "weather",
                "provider": "qweather",
                "label": "QWeather Test",
                "base_url": "https://qweather.example",
                "secrets": {"api_key": "qweather-key"},
                "metadata": {"auth_mode": "api_key", "test_location": "101010100"},
                "enabled": True,
            },
        ).json()
        assert payload["ok"] is True
        assert payload["kind"] == "weather"
        assert seen["url"].startswith("https://qweather.example/v7/weather/now")
        assert seen["api_key"] == "qweather-key"
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
            ensure_seed(session, user_id="threshold_user", character_id="atri")
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
            character = session.get(Character, "atri")
            user.story_completed = True
            user.tts_enabled = False
            character.key_reply_threshold = 75
            session.commit()
            result = handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id="threshold_user",
                    character_id="atri",
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
            ensure_seed(session, user_id="normal_reply_user", character_id="atri")
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
                select(RelationState).where(RelationState.user_id == "normal_reply_user", RelationState.character_id == "atri")
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
                    character_id="atri",
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


def test_free_user_message_does_not_apply_relation_delta() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(
            {
                "lines": [{"text": "我听见啦。", "emotion": "calm", "pose": "idle"}],
                "normal_replies": [],
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
            ensure_seed(session, user_id="free_delta_user", character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_free_delta_llm",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, "free_delta_user")
            relation = session.execute(
                select(RelationState).where(RelationState.user_id == "free_delta_user", RelationState.character_id == "atri")
            ).scalar_one()
            user.story_completed = True
            user.tts_enabled = False
            relation.affection = 85
            relation.trust = 60
            relation.dependency = 35
            relation.mood = 12
            before = (relation.affection, relation.trust, relation.dependency, relation.mood)
            session.commit()

            result = handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id="free_delta_user",
                    character_id="atri",
                    session_id="free_delta_session",
                    payload={"text": "今天在做什么？"},
                    client_context={"local_time": "2026-06-10T10:15:00+08:00"},
                ),
            )
            session.refresh(relation)
            assert (relation.affection, relation.trust, relation.dependency, relation.mood) == before
            assert result.payload["relation_delta"] == {"affection": 0, "trust": 0, "dependency": 0, "mood": 0}
    finally:
        providers.HTTP_TRANSPORT = None


def test_option_selected_can_apply_relation_delta() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(
            {
                "reply_mode": "key_moment",
                "lines": [{"text": "嗯，这个约定我会认真记住。", "emotion": "shy", "pose": "shy"}],
                "normal_replies": [],
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
            ensure_seed(session, user_id="option_delta_user", character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_option_delta_llm",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, "option_delta_user")
            relation = session.execute(
                select(RelationState).where(RelationState.user_id == "option_delta_user", RelationState.character_id == "atri")
            ).scalar_one()
            user.story_completed = True
            user.tts_enabled = False
            relation.affection = 85
            relation.trust = 60
            relation.dependency = 35
            relation.mood = 12
            before = (relation.affection, relation.trust, relation.dependency, relation.mood)
            session.commit()

            result = handle_event(
                session,
                EventIn(
                    event_type="option_selected",
                    user_id="option_delta_user",
                    character_id="atri",
                    session_id="option_delta_session",
                    payload={"reply_text": "那我们周末约会吧。"},
                ),
            )
            session.refresh(relation)
            assert (relation.affection, relation.trust, relation.dependency, relation.mood) == (
                before[0] + 3,
                before[1] + 2,
                before[2] + 1,
                before[3] + 3,
            )
            assert result.payload["relation_delta"] == {"affection": 3, "trust": 2, "dependency": 1, "mood": 3}
    finally:
        providers.HTTP_TRANSPORT = None


def test_schedule_question_prompt_includes_actual_slot_context() -> None:
    seen_prompt = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_prompt
        body = json.loads(request.content.decode())
        seen_prompt = body["messages"][1]["content"]
        assert "【今日真实日程】" in seen_prompt
        assert "当前：10:15-10:30 上课和整理笔记" in seen_prompt
        assert "刚刚/上一段：10:00-10:15 上课和整理笔记" in seen_prompt
        content = json.dumps(
            {
                "lines": [{"text": "刚刚是在教室上课和整理笔记。", "emotion": "calm", "pose": "idle"}],
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

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id="schedule_prompt_user", character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_schedule_prompt_llm",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, "schedule_prompt_user")
            user.story_completed = True
            user.tts_enabled = False
            session.commit()
            result = handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id="schedule_prompt_user",
                    character_id="atri",
                    session_id="schedule_prompt_session",
                    payload={"text": "刚刚的日程安排是什么？"},
                    client_context={"local_time": "2026-06-10T10:15:00+08:00"},
                ),
            )
            assert "上课和整理笔记" in result.payload["lines"][0]["text"]
            assert "像素解谜" not in seen_prompt
    finally:
        providers.HTTP_TRANSPORT = None


def test_weather_question_prompt_includes_weather_context() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"weather_prompt_user_{suffix}"
    seen_prompt = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_prompt
        body = json.loads(request.content.decode())
        seen_prompt = body["messages"][1]["content"]
        assert "【今日天气】" in seen_prompt
        assert "晚上可能有雨" in seen_prompt
        content = json.dumps(
            {
                "lines": [{"text": "今天晚上可能会下雨，出门的话把伞带上吧。", "emotion": "thinking", "pose": "thinking"}],
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

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_weather_prompt_llm_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.tts_enabled = False
            session.add(
                WeatherSnapshot(
                    snapshot_id=f"weather_prompt_snapshot_{suffix}",
                    user_id=user_id,
                    weather_date="2026-06-10",
                    location_key="101280601",
                    city_name="深圳",
                    observed_at="2026-06-10T11:55:00+08:00",
                    fetched_at="2026-06-10T04:00:00+00:00",
                    expires_at="2026-06-10T14:00:00+00:00",
                    weather_text="多云",
                    severity="rain",
                    severity_score=82,
                    trigger_key="evening_rain",
                    summary="深圳现在多云，晚上可能有雨。",
                    now_json=dump_json({"now": {"temp": "29", "text": "多云"}}),
                    hourly_json=dump_json({"hourly": [{"fxTime": "2026-06-10T20:00+08:00", "temp": "27", "text": "小雨", "pop": "70"}]}),
                    daily_json=dump_json({"daily": [{"textDay": "多云", "textNight": "小雨", "tempMin": "25", "tempMax": "31", "precip": "2"}]}),
                    warning_json=dump_json({"warning": []}),
                    minutely_json=dump_json({}),
                )
            )
            session.commit()
            result = handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id=user_id,
                    character_id="atri",
                    session_id=f"weather_prompt_session_{suffix}",
                    payload={"text": "今天天气怎么样？晚上会下雨吗？"},
                    client_context={"local_time": "2026-06-10T12:00:00+08:00"},
                ),
            )
            assert "伞" in result.payload["lines"][0]["text"]
            assert "【今日真实日程】" in seen_prompt
    finally:
        providers.HTTP_TRANSPORT = None


def test_memory_interest_requires_explicit_user_interest() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"interest_filter_user_{suffix}"

    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(
            {
                "lines": [{"text": "我会按真实日程回答你。", "emotion": "calm", "pose": "idle"}],
                "normal_replies": [],
                "key_reply_score": 0,
                "key_replies": [],
                "relation_delta": {"affection": 0, "trust": 0, "dependency": 0, "mood": 0},
                "memory_candidates": [{"layer": "chat", "content": "用户最近关注：像素解谜", "importance": 0.9, "confidence": 0.9}],
                "interest_topics": ["像素解谜"],
            },
            ensure_ascii=False,
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_interest_filter_llm",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.tts_enabled = False
            session.commit()

            handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id=user_id,
                    character_id="atri",
                    session_id="interest_filter_session",
                    payload={"text": "刚刚的日程安排是什么？"},
                    client_context={"local_time": "2026-06-10T10:15:00+08:00"},
                ),
            )
            leaked = session.query(Memory).filter(Memory.user_id == user_id, Memory.content.contains("像素解谜")).all()
            assert leaked == []

            handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id=user_id,
                    character_id="atri",
                    session_id="interest_filter_session",
                    payload={"text": "我最近关注像素解谜。"},
                    client_context={"local_time": "2026-06-10T10:15:00+08:00"},
                ),
            )
            saved = session.query(Memory).filter(Memory.user_id == user_id, Memory.content.contains("像素解谜")).all()
            assert saved
            assert "像素解谜" in json.loads(user.interest_topics_json)
    finally:
        providers.HTTP_TRANSPORT = None


def test_subject_hint_prevents_ambiguous_user_message_from_role_memory() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"subject_hint_user_{suffix}"
    seen_prompt = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_prompt
        body = json.loads(request.content.decode())
        seen_prompt = body["messages"][1]["content"]
        content = json.dumps(
            {
                "reply_mode": "normal",
                "pace_reason": "按用户自己的陈述回应。",
                "lines": [{"text": "诶？你刚刚这么说，我会有点在意啦。", "emotion": "shy", "pose": "shy"}],
                "normal_replies": [],
                "key_reply_score": 0,
                "key_replies": [],
                "relation_delta": {"affection": 0, "trust": 0, "dependency": 0, "mood": 0},
                "memory_candidates": [
                    {"layer": "temporary", "content": "用户调侃我去找别的女人，我解释了我在玩像素解谜", "importance": 0.9, "confidence": 0.9}
                ],
                "interest_topics": [],
            },
            ensure_ascii=False,
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_subject_hint_llm",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.tts_enabled = False
            session.commit()

            handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id=user_id,
                    character_id="atri",
                    session_id=f"subject_hint_session_{suffix}",
                    payload={"text": "刚刚找别的女人去了"},
                ),
            )
            assert "目标消息没有明确主语时，默认动作主体是用户自己" in seen_prompt
            assert "不要把用户自己的陈述改写成" in seen_prompt
            leaked = session.query(Memory).filter(Memory.user_id == user_id, Memory.content.contains("调侃我去找别的女人")).all()
            assert leaked == []
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
            ensure_seed(session, user_id="light_reply_user", character_id="atri")
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
                select(RelationState).where(RelationState.user_id == "light_reply_user", RelationState.character_id == "atri")
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
                    character_id="atri",
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


def test_profile_mutation_writes_character_override_from_chat() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"profile_mutation_user_{suffix}"

    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(
            {
                "reply_mode": "normal",
                "pace_reason": "用户明确提出养成偏好。",
                "reply_depth": "normal",
                "lines": [{"text": "好，我记住这个称呼和你的偏好了。", "emotion": "calm", "pose": "idle"}],
                "normal_replies": [],
                "key_reply_score": 0,
                "key_replies": [],
                "relation_delta": {"affection": 0, "trust": 0, "dependency": 0, "mood": 0},
                "memory_candidates": [],
                "interest_topics": [],
            },
            ensure_ascii=False,
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_profile_mutation_llm_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.tts_enabled = False
            user.profile_json = "{}"
            session.commit()

            result = handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id=user_id,
                    character_id="atri",
                    session_id=f"profile_mutation_session_{suffix}",
                    payload={"text": "以后叫我博士，我喜欢机器人少女，别再说高性能。"},
                ),
            )
            assert result.event_type == "dialogue"
            session.refresh(user)
            override = json.loads(user.profile_json)["character_overrides"]["atri"]
            assert override["editable_overrides"]["preferred_user_name"] == "博士"
            assert "高性能" in override["editable_overrides"]["disabled_phrases"]
            assert "机器人少女" in override["information_profile"]["personal_topics"]
            assert result.payload["profile_mutation"]["changed"] is True
    finally:
        providers.HTTP_TRANSPORT = None


def test_reply_continuation_adds_extra_bubbles_for_deep_topic() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"continuation_user_{suffix}"
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            payload = {
                "reply_mode": "normal",
                "pace_reason": "用户在说重要情绪话题。",
                "reply_depth": "multi_bubble",
                "should_continue": True,
                "continuation_intent": "再主动补一句安抚和追问。",
                "lines": [{"text": "我在听，你不用急着把话说漂亮。", "emotion": "calm", "pose": "idle"}],
                "normal_replies": [],
                "key_reply_score": 0,
                "key_replies": [],
                "relation_delta": {"affection": 0, "trust": 0, "dependency": 0, "mood": 0},
                "memory_candidates": [],
                "interest_topics": [],
            }
        else:
            payload = {"lines": [{"text": "先把最难受的那一小块交给我，好吗？", "emotion": "sad", "pose": "thinking"}]}
        content = json.dumps(payload, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12}})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_continuation_llm_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.tts_enabled = False
            session.commit()

            result = handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id=user_id,
                    character_id="atri",
                    session_id=f"continuation_session_{suffix}",
                    payload={"text": "我有点难过，想和你认真聊聊。"},
                ),
            )
            assert calls == 2
            assert result.payload["continued"] is True
            assert [line["text"] for line in result.payload["lines"]] == ["我在听，你不用急着把话说漂亮。", "先把最难受的那一小块交给我，好吗？"]
            assert result.payload["stats"]["llm"]["tokens"]["total_tokens"] == 24
    finally:
        providers.HTTP_TRANSPORT = None


def test_holiday_question_uses_calendar_context_in_prompt() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"holiday_calendar_user_{suffix}"
    seen_prompt = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_prompt
        body = json.loads(request.content.decode())
        seen_prompt = body["messages"][1]["content"]
        content = json.dumps(
            {
                "reply_mode": "normal",
                "pace_reason": "依据日历事件回答。",
                "reply_depth": "normal",
                "lines": [{"text": "日历上看，一星期后是端午节。", "emotion": "calm", "pose": "idle"}],
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

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_holiday_calendar_llm_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.tts_enabled = False
            session.commit()

            result = handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id=user_id,
                    character_id="atri",
                    session_id=f"holiday_calendar_session_{suffix}",
                    payload={"text": "再过一个星期是不是要放假了？"},
                    client_context={"local_time": "2026-06-12T12:00:00+08:00"},
                ),
            )
            assert result.event_type == "dialogue"
            assert "2026-06-19" in seen_prompt
            assert "端午节" in seen_prompt
    finally:
        providers.HTTP_TRANSPORT = None


def test_duplicate_event_replays_saved_dialogue_without_second_llm_call() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"duplicate_event_user_{suffix}"
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        content = json.dumps(
            {
                "reply_mode": "normal",
                "pace_reason": "普通回复。",
                "reply_depth": "normal",
                "lines": [{"text": "这次我只生成一次。", "emotion": "calm", "pose": "idle"}],
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

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_duplicate_event_llm_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.tts_enabled = False
            session.commit()
            event = EventIn(
                event_type="user_message",
                event_id=f"dup_evt_{suffix}",
                user_id=user_id,
                character_id="atri",
                session_id=f"duplicate_event_session_{suffix}",
                payload={"text": "这条消息可能会重试。"},
            )
            first = handle_event(session, event)
            second = handle_event(session, event)
            assert calls == 1
            assert first.payload["lines"][0]["text"] == second.payload["lines"][0]["text"]
            assert second.payload["reply_mode"] == "cached_duplicate"
    finally:
        providers.HTTP_TRANSPORT = None


def test_app_opened_without_proactive_event_returns_no_reply() -> None:
    with SessionLocal() as session:
        ensure_seed(session, user_id="no_reply_user", character_id="atri")
        user = session.get(User, "no_reply_user")
        user.story_completed = True
        session.add(
            Message(
                message_id=f"msg_no_reply_{str(datetime.now(timezone.utc).timestamp()).replace('.', '')}",
                session_id="no_reply_session",
                user_id="no_reply_user",
                character_id="atri",
                sender_type="heroine",
                sender_id="atri",
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
                character_id="atri",
                session_id="no_reply_session",
                payload={},
            ),
        )
        assert result.event_type == "no_reply"


def test_location_upload_reads_existing_weather_without_qweather_refresh() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"weather_location_user_{suffix}"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise AssertionError(f"location upload must not call QWeather: {request.url}")

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            user = session.get(User, user_id)
            user.story_completed = True
            user.news_enabled = False
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_qweather_location_{suffix}",
                    kind="weather",
                    provider="qweather",
                    base_url="https://qweather.example",
                    secrets={"api_key": "qweather-key"},
                    metadata={"auth_mode": "api_key", "include_minutely": True, "cache_minutes": 120},
                ),
            )
            session.add(
                WeatherSnapshot(
                    snapshot_id=f"weather_existing_{suffix}",
                    user_id=user_id,
                    weather_date="2026-06-10",
                    location_key="101280601",
                    city_name="深圳",
                    observed_at="2026-06-10T04:00:00+08:00",
                    fetched_at="2026-06-10T04:00:00+08:00",
                    expires_at="2026-06-11T04:00:00+08:00",
                    weather_text="雷阵雨",
                    severity="severe",
                    severity_score=94,
                    trigger_key="thunderstorm",
                    summary="深圳现在雷阵雨，晚上可能有雨。",
                    now_json=dump_json({"now": {"temp": "28", "text": "雷阵雨"}}),
                    hourly_json=dump_json({"hourly": [{"fxTime": "2026-06-10T20:00+08:00", "temp": "27", "text": "雷阵雨", "pop": "80"}]}),
                    daily_json=dump_json({"daily": [{"textDay": "雷阵雨", "textNight": "雷阵雨", "tempMin": "25", "tempMax": "30", "precip": "12"}]}),
                    warning_json=dump_json({"warning": []}),
                    minutely_json=dump_json({}),
                )
            )
            session.commit()
        payload = client.post(
            f"/api/location?user_id={user_id}&character_id=atri",
            json={
                "latitude": 22.54,
                "longitude": 114.06,
                "accuracy_m": 32,
                "provider": "test",
                "local_time": "2026-06-10T15:30:00+08:00",
            },
        ).json()
        assert payload["ok"] is True
        assert calls == []
        assert payload["location"]["qweather_location_id"] == ""
        assert payload["weather"]["trigger_key"] == "thunderstorm"
        assert payload["weather"]["severity_score"] >= 90
        assert payload["proactive_event_id"] == ""
        with SessionLocal() as session:
            location = session.get(UserLocation, user_id)
            assert location.city_name == ""
    finally:
        providers.HTTP_TRANSPORT = None


def test_manual_weather_refreshes_qweather_once_and_creates_weather_proactive() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"weather_manual_refresh_user_{suffix}"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        assert request.headers.get("X-QW-Api-Key") == "qweather-key"
        if path == "/geo/v2/city/lookup":
            return httpx.Response(
                200,
                json={
                    "code": "200",
                    "location": [
                        {
                            "id": "101280601",
                            "name": "深圳",
                            "adm1": "广东省",
                            "adm2": "深圳市",
                            "country": "中国",
                            "tz": "Asia/Shanghai",
                        }
                    ],
                },
            )
        if path == "/v7/weather/now":
            return httpx.Response(
                200,
                json={
                    "code": "200",
                    "updateTime": "2026-06-10T15:00+08:00",
                    "now": {"obsTime": "2026-06-10T15:00+08:00", "temp": "28", "text": "雷阵雨", "windScale": "3"},
                },
            )
        if path == "/v7/weather/3d":
            return httpx.Response(
                200,
                json={
                    "code": "200",
                    "daily": [
                        {"fxDate": "2026-06-10", "tempMin": "25", "tempMax": "30", "textDay": "雷阵雨", "textNight": "雷阵雨", "precip": "12"}
                    ],
                },
            )
        if path == "/v7/weather/24h":
            return httpx.Response(
                200,
                json={
                    "code": "200",
                    "hourly": [
                        {"fxTime": "2026-06-10T20:00+08:00", "temp": "27", "text": "雷阵雨", "pop": "80", "precip": "3.2"}
                    ],
                },
            )
        if path == "/v7/minutely/5m":
            return httpx.Response(200, json={"code": "200", "summary": "未来两小时有阵雨", "minutely": []})
        return httpx.Response(404, json={"code": "404"})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            user = session.get(User, user_id)
            user.story_completed = True
            user.news_enabled = False
            session.add(UserLocation(user_id=user_id, latitude=22.54, longitude=114.06, accuracy_m=32, provider="test"))
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_qweather_manual_{suffix}",
                    kind="weather",
                    provider="qweather",
                    base_url="https://qweather.example",
                    secrets={"api_key": "qweather-key"},
                    metadata={"auth_mode": "api_key", "include_minutely": True, "include_warning": False},
                ),
            )
            session.commit()
        payload = client.post(
            f"/api/weather/refresh?user_id={user_id}&character_id=atri",
            json={"local_time": "2026-06-10T15:30:00+08:00"},
        ).json()
        assert payload["ok"] is True
        assert payload["location"]["qweather_location_id"] == "101280601"
        assert payload["weather"]["trigger_key"] == "thunderstorm"
        assert payload["weather"]["severity_score"] >= 90
        assert payload["proactive_event_id"]
        assert "/v7/warning/now" not in calls
        first_call_count = len(calls)
        second = client.post(
            f"/api/weather/refresh?user_id={user_id}&character_id=atri",
            json={"local_time": "2026-06-10T15:30:10+08:00"},
        ).json()
        assert second["weather"]["snapshot_id"] == payload["weather"]["snapshot_id"]
        assert len(calls) == first_call_count
        with SessionLocal() as session:
            proactive = session.get(ProactiveEvent, payload["proactive_event_id"])
            assert proactive is not None
            assert proactive.source_type == "weather"
            assert proactive.priority >= 90
            location = session.get(UserLocation, user_id)
            assert location.city_name == "深圳"
    finally:
        providers.HTTP_TRANSPORT = None


def test_proactive_pending_uses_existing_weather_without_qweather_refresh() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"weather_pending_user_{suffix}"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "qweather" in str(request.url):
            raise AssertionError(f"pending proactive must not call QWeather: {request.url}")
        if request.url.path.endswith("/chat/completions"):
            return _llm_json_response({"should_send": False, "selected_event_id": "", "reason": "defer weather judge"})
        calls.append(request.url.path)
        raise AssertionError(f"unexpected request: {request.url}")

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            user = session.get(User, user_id)
            user.story_completed = True
            user.news_enabled = False
            for config in session.execute(select(ProviderConfig).where(ProviderConfig.kind.in_(["llm_task", "llm"]))).scalars():
                config.enabled = False
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_qweather_pending_judge_{suffix}",
                    kind="llm_task",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-task",
                    secrets={"api_key": "ark-key"},
                ),
            )
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_qweather_pending_{suffix}",
                    kind="weather",
                    provider="qweather",
                    base_url="https://qweather.example",
                    secrets={"api_key": "qweather-key"},
                    metadata={"auth_mode": "api_key"},
                ),
            )
            session.add(
                WeatherSnapshot(
                    snapshot_id=f"weather_pending_snapshot_{suffix}",
                    user_id=user_id,
                    weather_date="2026-06-10",
                    location_key="101280601",
                    city_name="深圳",
                    observed_at="2026-06-10T04:00:00+08:00",
                    fetched_at="2026-06-10T04:00:00+08:00",
                    expires_at="2026-06-11T04:00:00+08:00",
                    weather_text="雷阵雨",
                    severity="severe",
                    severity_score=94,
                    trigger_key="thunderstorm",
                    summary="深圳现在雷阵雨，晚上可能有雨。",
                    now_json=dump_json({"now": {"temp": "28", "text": "雷阵雨"}}),
                    hourly_json=dump_json({"hourly": []}),
                    daily_json=dump_json({"daily": []}),
                    warning_json=dump_json({"warning": []}),
                    minutely_json=dump_json({}),
                )
            )
            session.commit()
            result = pending_proactive_response(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-10T15:30:00+08:00"),
                generate_news=False,
            )
            assert result["ok"] is True
            weather_event = session.execute(
                select(ProactiveEvent).where(ProactiveEvent.user_id == user_id, ProactiveEvent.source_type == "weather")
            ).scalar_one_or_none()
            assert weather_event is not None
            assert weather_event.priority >= 90
        assert calls == []
    finally:
        providers.HTTP_TRANSPORT = None


def test_daily_cycle_invokes_weather_refresh_once(monkeypatch: pytest.MonkeyPatch) -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"weather_daily_user_{suffix}"
    calls = {"refresh": 0, "candidate": 0}

    def fake_refresh_weather_snapshot(session: object, **kwargs: object) -> object:
        calls["refresh"] += 1
        assert kwargs["user_id"] == user_id
        assert kwargs["force"] is False
        return object()

    def fake_ensure_weather_candidate(session: object, **kwargs: object) -> None:
        calls["candidate"] += 1
        assert kwargs["user_id"] == user_id
        assert "force" not in kwargs
        return None

    monkeypatch.setattr(schedule_module, "refresh_weather_snapshot", fake_refresh_weather_snapshot)
    monkeypatch.setattr(schedule_module, "ensure_weather_candidate", fake_ensure_weather_candidate)
    with SessionLocal() as session:
        ensure_seed(session, user_id=user_id, character_id="atri")
        user = session.get(User, user_id)
        user.story_completed = True
        session.commit()
        run_daily_cycle(session, user_id=user_id, character_id="atri", day=datetime.fromisoformat("2099-06-10T04:00:00+08:00"))
    assert calls == {"refresh": 1, "candidate": 1}


def test_proactive_judge_selects_event_without_hard_daily_gap_or_sleep_blocks() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"proactive_limit_user_{suffix}"
    selected_event_id = ""
    judge_contexts: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        prompt = body["messages"][1]["content"]
        context = json.loads(prompt.split("Context JSON:", 1)[1])
        judge_contexts.append(context)
        return _llm_json_response(
            {
                "should_send": True,
                "selected_event_id": selected_event_id,
                "reason": "虽然处在睡眠和短间隔上下文里，但这条候选更重要。",
            }
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_proactive_judge_select_{suffix}",
                    kind="llm_task",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.notifications_enabled = True
            user.proactive_daily_limit = "high"
            user.sleep_start = "00:30"
            user.sleep_end = "08:00"
            for index in range(3):
                delivered = create_proactive_event(
                    session,
                    user_id=user_id,
                    character_id="atri",
                    source_type="memory",
                    source_id=f"delivered_{suffix}_{index}",
                    title=f"已投递标题 {index}",
                    text=f"已投递内容 {index}",
                    priority=50,
                    dedupe_key=f"delivered_{suffix}_{index}",
                    scheduled_at=datetime.fromisoformat("2026-06-08T16:00:00+00:00"),
                )
                delivered.status = "delivered"
                delivered.delivered_at = f"2026-06-09T00:{10 + index:02d}:00+08:00"
            high = create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="memory",
                source_id=f"pending_high_{suffix}",
                title="高优先级但不选",
                text="这条优先级更高，但判断器不选它。",
                priority=95,
                dedupe_key=f"pending_high_{suffix}",
                scheduled_at=datetime.fromisoformat("2026-06-08T16:00:00+00:00"),
            )
            selected = create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="memory",
                source_id=f"pending_selected_{suffix}",
                title="判断器选择目标",
                text="这条由判断器指定投递。",
                priority=40,
                dedupe_key=f"pending_selected_{suffix}",
                scheduled_at=datetime.fromisoformat("2026-06-08T16:00:00+00:00"),
            )
            selected_event_id = selected.proactive_event_id
            session.commit()

            result = pending_proactive_response(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-09T01:00:00+08:00"),
                generate_news=False,
                generate_weather=False,
            )
            assert result["event"]["proactive_event_id"] == selected_event_id
            assert judge_contexts
            context = judge_contexts[0]
            assert context["delivery_history"]["today_delivered_count"] == 3
            assert context["sleep_window"]["is_sleep_time"] is True
            candidate_ids = {item["proactive_event_id"] for item in context["candidates"]}
            assert {high.proactive_event_id, selected_event_id}.issubset(candidate_ids)
            session.refresh(user)
            judgement = json.loads(user.proactive_judgement_json)
            assert judgement["selected_event_id"] == selected_event_id
            assert "user_availability" in context
    finally:
        providers.HTTP_TRANSPORT = None


def test_proactive_judge_can_defer_without_next_check() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"proactive_defer_user_{suffix}"

    def handler(request: httpx.Request) -> httpx.Response:
        return _llm_json_response(
            {
                "should_send": False,
                "selected_event_id": "",
                "reason": "现在不打扰，稍后再看。",
            }
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_proactive_judge_defer_{suffix}",
                    kind="llm_task",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.notifications_enabled = True
            create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="memory",
                source_id=f"pending_defer_{suffix}",
                title="暂缓测试",
                text="这条可以被判断器暂缓。",
                priority=80,
                dedupe_key=f"pending_defer_{suffix}",
                scheduled_at=datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
            )
            session.commit()
            result = pending_proactive_response(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-09T12:00:00+08:00"),
                generate_news=False,
                generate_weather=False,
            )
            assert result["event"] is None
            session.refresh(user)
            judgement = json.loads(user.proactive_judgement_json)
            assert judgement["should_send"] is False
    finally:
        providers.HTTP_TRANSPORT = None


def test_proactive_stale_next_check_does_not_block_judgement() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"proactive_next_check_user_{suffix}"
    selected_event_id = ""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _llm_json_response({"should_send": True, "selected_event_id": selected_event_id, "reason": "stale next_check ignored"})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_proactive_judge_skip_{suffix}",
                    kind="llm_task",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.notifications_enabled = True
            user.proactive_next_check_at = "2026-06-09T05:00:00+00:00"
            event = create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="memory",
                source_id=f"pending_skip_{suffix}",
                title="不再被 next_check 挡住",
                text="这条应该重新触发判断器。",
                priority=90,
                dedupe_key=f"pending_skip_{suffix}",
                scheduled_at=datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
            )
            selected_event_id = event.proactive_event_id
            session.commit()
            result = pending_proactive_response(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-09T12:06:00+08:00"),
                generate_news=False,
                generate_weather=False,
            )
            assert result["event"]["proactive_event_id"] == selected_event_id
            assert calls == 1
    finally:
        providers.HTTP_TRANSPORT = None


def test_proactive_error_state_does_not_block_retry() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"proactive_error_retry_{suffix}"
    selected_event_id = ""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _llm_json_response({"should_send": True, "selected_event_id": selected_event_id, "reason": "retry after error", "next_check_after_minutes": 10})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_proactive_error_retry_{suffix}",
                    kind="llm_task",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.notifications_enabled = True
            user.proactive_next_check_at = "2026-06-09T06:00:00+00:00"
            user.proactive_judgement_json = dump_json(
                {
                    "status": "error",
                    "reason": "previous model error",
                    "decided_at": "2026-06-09T04:00:00+00:00",
                    "next_check_at": user.proactive_next_check_at,
                }
            )
            event = create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="memory",
                source_id=f"pending_error_retry_{suffix}",
                title="错误重试",
                text="这条应该在错误状态后仍可重新触发判断器。",
                priority=90,
                dedupe_key=f"pending_error_retry_{suffix}",
                scheduled_at=datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
            )
            assert event is not None
            selected_event_id = event.proactive_event_id
            session.commit()
            result = pending_proactive_response(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-09T12:06:00+08:00"),
                generate_news=False,
                generate_weather=False,
            )
            assert result["event"]["proactive_event_id"] == selected_event_id
            assert calls == 1
    finally:
        providers.HTTP_TRANSPORT = None


def test_proactive_next_check_keeps_selected_pending_unread_visible() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"proactive_unread_pending_user_{suffix}"

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"selected unread event should bypass LLM: {request.url}")

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_proactive_unread_pending_{suffix}",
                    kind="llm_task",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.notifications_enabled = True
            user.proactive_next_check_at = "2026-06-09T05:00:00+00:00"
            event = create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="memory",
                source_id=f"selected_pending_{suffix}",
                title="Unread message",
                text="Already selected and should keep the widget unread badge.",
                priority=90,
                dedupe_key=f"selected_pending_{suffix}",
                scheduled_at=datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
            )
            user.proactive_judgement_json = json.dumps(
                {
                    "status": "decided",
                    "should_send": True,
                    "selected_event_id": event.proactive_event_id,
                    "next_check_after_minutes": 30,
                },
                ensure_ascii=False,
            )
            session.commit()
            result = pending_proactive_response(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-09T12:00:00+08:00"),
                generate_news=False,
                generate_weather=False,
            )
            assert result["event"]["proactive_event_id"] == event.proactive_event_id
            assert result["widget"]["proactive_event_id"] == event.proactive_event_id
            assert result["widget"]["unread_count"] == 1
    finally:
        providers.HTTP_TRANSPORT = None


def test_proactive_judge_invalid_json_defaults_to_no_send() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"proactive_bad_json_user_{suffix}"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_proactive_judge_bad_json_{suffix}",
                    kind="llm_task",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.notifications_enabled = True
            create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="memory",
                source_id=f"pending_bad_json_{suffix}",
                title="非法 JSON 测试",
                text="模型非法输出时不应该投递。",
                priority=90,
                dedupe_key=f"pending_bad_json_{suffix}",
                scheduled_at=datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
            )
            session.commit()
            result = pending_proactive_response(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-09T12:00:00+08:00"),
                generate_news=False,
                generate_weather=False,
            )
            assert result["event"] is None
            session.refresh(user)
            judgement = json.loads(user.proactive_judgement_json)
            assert judgement["status"] == "error"
            assert judgement["should_send"] is False
    finally:
        providers.HTTP_TRANSPORT = None


def test_proactive_judge_missing_provider_defaults_to_no_send() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"proactive_no_provider_user_{suffix}"
    with SessionLocal() as session:
        for config in session.execute(select(ProviderConfig).where(ProviderConfig.kind.in_(["llm", "llm_task"]))).scalars():
            config.enabled = False
        ensure_seed(session, user_id=user_id, character_id="atri")
        user = session.get(User, user_id)
        user.story_completed = True
        user.notifications_enabled = True
        create_proactive_event(
            session,
            user_id=user_id,
            character_id="atri",
            source_type="memory",
            source_id=f"pending_no_provider_{suffix}",
            title="无模型测试",
            text="没有判断器模型时不应该投递。",
            priority=90,
            dedupe_key=f"pending_no_provider_{suffix}",
            scheduled_at=datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
        )
        session.commit()
        result = pending_proactive_response(
            session,
            user_id=user_id,
            character_id="atri",
            local_time=datetime.fromisoformat("2026-06-09T12:00:00+08:00"),
            generate_news=False,
            generate_weather=False,
        )
        assert result["event"] is None
        session.refresh(user)
        judgement = json.loads(user.proactive_judgement_json)
        assert judgement["status"] == "error"
        assert judgement["error_type"] == "missing_provider"


def test_proactive_pending_and_delivered_routes() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"proactive_route_user_{suffix}"
    event_id = ""

    def handler(request: httpx.Request) -> httpx.Response:
        return _llm_json_response(
            {
                "should_send": True,
                "selected_event_id": event_id,
                "reason": "路由测试允许投递。",
                "next_check_after_minutes": 60,
            }
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    with SessionLocal() as session:
        try:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_proactive_route_judge_{suffix}",
                    kind="llm_task",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.notifications_enabled = True
            event = create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
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
                f"/api/proactive/pending?user_id={user_id}&character_id=atri&local_time=2026-06-09T12:00:00%2B08:00"
            ).json()
            assert payload["event"]["proactive_event_id"] == event_id
            assert payload["widget"]["unread_count"] == 1
            assert payload["widget"]["chibi_url"] == "/media/asset_chibi_atri_widget"
            delivered = client.post(f"/api/proactive/{event_id}/delivered").json()
            assert delivered["event"]["status"] == "delivered"
            retained = client.get(
                f"/api/proactive/pending?user_id={user_id}&character_id=atri&local_time=2026-06-09T12:10:00%2B08:00"
            ).json()
            assert retained["event"] is None
            assert retained["widget"]["proactive_event_id"] == event_id
            assert retained["widget"]["unread_count"] == 1
        finally:
            providers.HTTP_TRANSPORT = None


def test_proactive_consume_clears_widget_pending_event() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"proactive_consume_user_{suffix}"
    event_id = ""

    def handler(request: httpx.Request) -> httpx.Response:
        return _llm_json_response(
            {
                "should_send": True,
                "selected_event_id": event_id,
                "reason": "清红点测试允许投递。",
                "next_check_after_minutes": 60,
            }
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    with SessionLocal() as session:
        try:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_proactive_consume_judge_{suffix}",
                    kind="llm_task",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.notifications_enabled = True
            event = create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="memory",
                source_id=f"consume_memory_{suffix}",
                title="清红点测试",
                text="这条主动消息被点击后应该马上清掉红点。",
                priority=90,
                dedupe_key=f"consume_memory_{suffix}",
                scheduled_at=datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
            )
            event_id = event.proactive_event_id
            session.commit()
            before = pending_proactive_response(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-09T12:00:00+08:00"),
                generate_news=False,
                generate_weather=False,
            )
            assert before["event"]["proactive_event_id"] == event.proactive_event_id
            consume_proactive_event(session, event.proactive_event_id)
            after = pending_proactive_response(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-09T12:01:00+08:00"),
                generate_news=False,
                generate_weather=False,
            )
            assert after["event"] is None
            assert after["widget"]["unread_count"] == 0
        finally:
            providers.HTTP_TRANSPORT = None


def test_proactive_daily_limit_is_soft_and_does_not_block_judgement() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"proactive_daily_limit_{suffix}"
    selected_event_id = ""

    def handler(request: httpx.Request) -> httpx.Response:
        return _llm_json_response(
            {
                "should_send": True,
                "selected_event_id": selected_event_id,
                "reason": "low preference is soft only",
            }
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_proactive_soft_limit_{suffix}",
                    kind="llm_task",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.notifications_enabled = True
            user.proactive_daily_limit = "low"
            for index in range(2):
                delivered = create_proactive_event(
                    session,
                    user_id=user_id,
                    character_id="atri",
                    source_type="memory",
                    source_id=f"delivered_limit_{suffix}_{index}",
                    title="Delivered",
                    text="Already delivered.",
                    priority=50,
                    dedupe_key=f"delivered_limit_{suffix}_{index}",
                    scheduled_at=datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
                )
                delivered.status = "delivered"
                delivered.delivered_at = f"2026-06-09T0{index}:00:00+00:00"
            pending = create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="memory",
                source_id=f"pending_limit_{suffix}",
                title="Pending",
                text="Should still be judged even after many deliveries today.",
                priority=95,
                dedupe_key=f"pending_limit_{suffix}",
                scheduled_at=datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
            )
            selected_event_id = pending.proactive_event_id
            session.commit()
            result = pending_proactive_response(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-09T12:00:00+08:00"),
                generate_news=False,
                generate_weather=False,
            )
            assert result["event"]["proactive_event_id"] == selected_event_id
            session.refresh(user)
            judgement = json.loads(user.proactive_judgement_json)
            assert judgement["status"] == "decided"
            assert judgement["should_send"] is True
    finally:
        providers.HTTP_TRANSPORT = None


def test_proactive_unlimited_daily_limit_allows_judgement_after_stale_limited_state() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"proactive_unlimited_limit_{suffix}"
    selected_event_id = ""
    judge_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal judge_calls
        body = json.loads(request.content.decode())
        prompt = body["messages"][1]["content"]
        assert "Context JSON:" in prompt
        judge_calls += 1
        return _llm_json_response(
            {
                "should_send": True,
                "selected_event_id": selected_event_id,
                "reason": "unlimited policy allows another proactive message.",
                "next_check_after_minutes": 10,
            }
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_unlimited_limit_llm_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.notifications_enabled = True
            user.proactive_daily_limit = "unlimited"
            user.proactive_next_check_at = "2026-06-09T06:00:00+00:00"
            user.proactive_judgement_json = dump_json(
                {
                    "status": "limited",
                    "reason": "daily limit reached: 4/2",
                    "next_check_at": user.proactive_next_check_at,
                }
            )
            for index in range(4):
                delivered = create_proactive_event(
                    session,
                    user_id=user_id,
                    character_id="atri",
                    source_type="memory",
                    source_id=f"unlimited_delivered_{suffix}_{index}",
                    title="Delivered",
                    text="Already delivered.",
                    priority=50,
                    dedupe_key=f"unlimited_delivered_{suffix}_{index}",
                    scheduled_at=datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
                )
                delivered.status = "delivered"
                delivered.delivered_at = f"2026-06-09T0{index}:00:00+00:00"
            pending = create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="memory",
                source_id=f"unlimited_pending_{suffix}",
                title="Pending",
                text="Should still be judged because policy is unlimited.",
                priority=95,
                dedupe_key=f"unlimited_pending_{suffix}",
                scheduled_at=datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
            )
            assert pending is not None
            selected_event_id = pending.proactive_event_id
            session.commit()

            result = pending_proactive_response(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-09T12:00:00+08:00"),
                generate_news=False,
                generate_weather=False,
            )
            assert result["event"]["proactive_event_id"] == pending.proactive_event_id
            assert judge_calls == 1
            session.refresh(user)
            judgement = json.loads(user.proactive_judgement_json)
            assert judgement["status"] == "decided"
    finally:
        providers.HTTP_TRANSPORT = None


def test_foreground_check_prepares_and_consumes_dialogue() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"foreground_user_{suffix}"
    selected_event_id = ""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        prompt = body["messages"][1]["content"]
        if "Context JSON:" in prompt:
            return _llm_json_response({"should_send": True, "selected_event_id": selected_event_id, "reason": "idle foreground", "next_check_after_minutes": 10})
        return _llm_json_response(
            {
                "reply_mode": "light",
                "pace_reason": "foreground proactive",
                "lines": [{"text": "差不多该提醒你啦。", "emotion": "calm", "pose": "idle"}],
                "normal_replies": [],
                "key_replies": [],
                "relation_delta": {"affection": 0, "trust": 0, "dependency": 0, "mood": 0},
                "memory_candidates": [],
            }
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_foreground_task_{suffix}",
                    kind="llm_task",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-task",
                    secrets={"api_key": "ark-key"},
                ),
            )
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_foreground_chat_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.notifications_enabled = True
            event = create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="appointment",
                source_id=f"appointment_{suffix}",
                title="Flight reminder",
                text="Wake up for the flight.",
                priority=95,
                dedupe_key=f"appointment_{suffix}",
                scheduled_at=datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
            )
            selected_event_id = event.proactive_event_id
            session.commit()
        response = client.post(
            "/api/proactive/foreground-check",
            json={
                "user_id": user_id,
                "character_id": "atri",
                "device_id": f"device_{suffix}",
                "screen": "home",
                "idle_seconds": 45,
                "input_active": False,
                "local_time": "2026-06-09T12:00:00+08:00",
            },
        ).json()
        assert response["event_type"] == "dialogue"
        assert response["payload"]["proactive_event_id"] == selected_event_id
        with SessionLocal() as session:
            refreshed = session.get(ProactiveEvent, selected_event_id)
            assert refreshed.status == "reflected"
            attempt = session.execute(
                select(ProactiveDeliveryAttempt).where(ProactiveDeliveryAttempt.proactive_event_id == selected_event_id)
            ).scalar_one()
            assert attempt.channel == "foreground"
            assert attempt.status == "sent"
    finally:
        providers.HTTP_TRANSPORT = None


def test_fcm_push_sends_and_records_attempt() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"fcm_user_{suffix}"
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    service_account = {
        "project_id": "test-project",
        "client_email": "firebase-adminsdk@test-project.iam.gserviceaccount.com",
        "private_key": private_pem,
        "token_uri": "https://oauth2.googleapis.com/token",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://oauth2.googleapis.com/token":
            return httpx.Response(200, json={"access_token": "oauth-token"})
        assert str(request.url) == "https://fcm.googleapis.com/v1/projects/test-project/messages:send"
        assert request.headers.get("authorization") == "Bearer oauth-token"
        body = json.loads(request.content.decode())
        assert body["message"]["data"]["proactive_event_id"]
        return httpx.Response(200, json={"name": "projects/test-project/messages/1"})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_fcm_{suffix}",
                    kind="push",
                    provider="fcm_http_v1",
                    base_url="https://fcm.googleapis.com/v1",
                    secrets={"service_account_json": json.dumps(service_account)},
                ),
            )
            register_device(
                session,
                {
                    "device_id": f"device_{suffix}",
                    "user_id": user_id,
                    "platform": "android",
                    "push_token": "fcm-token",
                    "notifications_enabled": True,
                },
            )
            event = create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="weather",
                source_id=f"weather_{suffix}",
                title="Rain tonight",
                text="Remember to bring an umbrella.",
                priority=88,
                dedupe_key=f"weather_{suffix}",
                scheduled_at=datetime.now(timezone.utc),
            )
            session.commit()
            result = send_proactive_push(session, event)
            assert result["sent"] == 1
            refreshed = session.get(ProactiveEvent, event.proactive_event_id)
            assert refreshed.status == "delivered"
            attempt = session.execute(
                select(ProactiveDeliveryAttempt).where(ProactiveDeliveryAttempt.proactive_event_id == event.proactive_event_id)
            ).scalar_one()
            assert attempt.status == "sent"
            assert attempt.status_code == 200
    finally:
        providers.HTTP_TRANSPORT = None


def test_user_message_extracts_appointment_commitment() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"commitment_user_{suffix}"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        prompt = body["messages"][1]["content"]
        if "Extract a future user commitment" in prompt:
            return _llm_json_response(
                {
                    "has_commitment": True,
                    "title": "赶飞机",
                    "description": "明早 9 点要赶飞机，提前确认起床。",
                    "event_at": "2026-06-10T09:00:00+08:00",
                    "remind_at": "2026-06-10T08:00:00+08:00",
                    "delivery_timing": "advance",
                    "confidence": 0.92,
                }
            )
        return _llm_json_response(
            {
                "reply_mode": "light",
                "pace_reason": "remember commitment",
                "lines": [{"text": "好，我会记得提醒你。", "emotion": "calm", "pose": "idle"}],
                "normal_replies": [],
                "key_replies": [],
                "relation_delta": {"affection": 0, "trust": 0, "dependency": 0, "mood": 0},
                "memory_candidates": [],
            }
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_commitment_chat_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_commitment_task_{suffix}",
                    kind="llm_task",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-task",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.tts_enabled = False
            session.commit()
            result = handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id=user_id,
                    character_id="atri",
                    session_id=f"commitment_session_{suffix}",
                    payload={"text": "我明天早上9点要赶飞机"},
                    client_context={"local_time": "2026-06-09T20:00:00+08:00"},
                ),
            )
            assert result.event_type == "dialogue"
            commitment = session.execute(select(UserCommitment).where(UserCommitment.user_id == user_id)).scalar_one()
            assert commitment.title == "赶飞机"
            proactive = session.execute(select(ProactiveEvent).where(ProactiveEvent.source_id == commitment.commitment_id)).scalar_one()
            assert proactive.source_type == "appointment"
            assert proactive.scheduled_at == "2026-06-10T00:00:00+00:00"
            payload = json.loads(proactive.payload_json)
            assert payload["delivery_timing"] == "advance"
    finally:
        providers.HTTP_TRANSPORT = None


def test_user_message_extracts_on_time_call_me_commitment() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"on_time_commitment_user_{suffix}"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        prompt = body["messages"][1]["content"]
        if "Extract a future user commitment" in prompt:
            return _llm_json_response(
                {
                    "has_commitment": True,
                    "title": "14:30叫我",
                    "description": "14:30叫我一下",
                    "event_at": "2026-06-09T14:30:00+08:00",
                    "delivery_timing": "on_time",
                    "confidence": 0.95,
                }
            )
        return _llm_json_response(
            {
                "reply_mode": "light",
                "pace_reason": "remember commitment",
                "lines": [{"text": "好，14:30我会叫你。", "emotion": "calm", "pose": "idle"}],
                "normal_replies": [],
                "key_replies": [],
                "relation_delta": {"affection": 0, "trust": 0, "dependency": 0, "mood": 0},
                "memory_candidates": [],
            }
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_on_time_commitment_chat_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_on_time_commitment_task_{suffix}",
                    kind="llm_task",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.tts_enabled = False
            session.commit()
            handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id=user_id,
                    character_id="atri",
                    session_id=f"on_time_commitment_session_{suffix}",
                    payload={"text": "14:30叫我一下"},
                    client_context={"local_time": "2026-06-09T13:00:00+08:00"},
                ),
            )
            commitment = session.execute(select(UserCommitment).where(UserCommitment.user_id == user_id)).scalar_one()
            assert commitment.remind_at == commitment.event_at
            proactive = session.execute(select(ProactiveEvent).where(ProactiveEvent.source_id == commitment.commitment_id)).scalar_one()
            payload = json.loads(proactive.payload_json)
            assert payload["delivery_timing"] == "on_time"
    finally:
        providers.HTTP_TRANSPORT = None


def test_user_message_extracts_simple_clock_reminder_without_commitment_llm() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"simple_clock_commitment_user_{suffix}"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        prompt = body["messages"][1]["content"]
        if "Extract a future user commitment" in prompt:
            pytest.fail("simple clock reminder should not call commitment extraction LLM")
        return _llm_json_response(
            {
                "reply_mode": "light",
                "pace_reason": "remember commitment",
                "lines": [{"text": "好，10点30我会提醒你。", "emotion": "calm", "pose": "idle"}],
                "normal_replies": [],
                "key_reply_score": 0,
                "key_reply_reason": "",
                "key_replies": [],
                "relation_delta": {"affection": 0, "trust": 0, "dependency": 0, "mood": 0},
                "memory_candidates": [],
                "interest_topics": [],
            }
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_simple_clock_commitment_chat_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_simple_clock_commitment_task_{suffix}",
                    kind="llm_task",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-task",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.tts_enabled = False
            session.commit()
            handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id=user_id,
                    character_id="atri",
                    session_id=f"simple_clock_commitment_session_{suffix}",
                    payload={"text": "一会儿10点30提醒我一下，有事情"},
                    client_context={"local_time": "2026-06-12T10:20:49+08:00"},
                ),
            )
            commitment = session.execute(select(UserCommitment).where(UserCommitment.user_id == user_id)).scalar_one()
            assert commitment.title == "有事情"
            assert commitment.event_at == "2026-06-12T02:30:00+00:00"
            assert commitment.remind_at == commitment.event_at
            proactive = session.execute(select(ProactiveEvent).where(ProactiveEvent.source_id == commitment.commitment_id)).scalar_one()
            payload = json.loads(proactive.payload_json)
            assert payload["delivery_timing"] == "on_time"
    finally:
        providers.HTTP_TRANSPORT = None


def test_appointment_on_time_fast_path_delivers_without_judge() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"on_time_fast_path_{suffix}"

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"on_time appointment should bypass judge: {request.url}")

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            user = session.get(User, user_id)
            user.story_completed = True
            user.notifications_enabled = True
            remind_at = datetime.fromisoformat("2026-06-09T14:30:00+08:00")
            create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="appointment",
                source_id=f"commit_on_time_{suffix}",
                title="14:30叫我",
                text="到点啦。",
                priority=90,
                dedupe_key=f"on_time_fast_{suffix}",
                payload={
                    "delivery_timing": "on_time",
                    "event_at": remind_at.astimezone(timezone.utc).isoformat(),
                    "remind_at": remind_at.astimezone(timezone.utc).isoformat(),
                },
                scheduled_at=remind_at,
                expires_at=remind_at + timedelta(hours=2),
            )
            session.commit()
            result = pending_proactive_response(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=remind_at,
                generate_news=False,
                generate_weather=False,
                appointment_only=True,
            )
            assert result["event"] is not None
            assert result["event"]["source_type"] == "appointment"
    finally:
        providers.HTTP_TRANSPORT = None


def test_follow_up_appointment_not_due_during_busy_window() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"follow_up_busy_{suffix}"

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"follow_up should not judge during busy window: {request.url}")

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            user = session.get(User, user_id)
            user.story_completed = True
            user.notifications_enabled = True
            busy_start = datetime.fromisoformat("2026-06-09T16:00:00+08:00")
            busy_end = datetime.fromisoformat("2026-06-09T18:00:00+08:00")
            remind_at = busy_end + timedelta(minutes=10)
            create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="appointment",
                source_id=f"commit_follow_up_{suffix}",
                title="忙完了联系",
                text="忙完了吗？",
                priority=90,
                dedupe_key=f"follow_up_busy_{suffix}",
                payload={
                    "delivery_timing": "follow_up",
                    "busy_start": busy_start.astimezone(timezone.utc).isoformat(),
                    "busy_end": busy_end.astimezone(timezone.utc).isoformat(),
                    "event_at": busy_end.astimezone(timezone.utc).isoformat(),
                    "remind_at": remind_at.astimezone(timezone.utc).isoformat(),
                },
                scheduled_at=remind_at,
                expires_at=busy_end + timedelta(hours=4),
            )
            session.commit()
            result = pending_proactive_response(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-09T17:00:00+08:00"),
                generate_news=False,
                generate_weather=False,
                appointment_only=True,
            )
            assert result["event"] is None
    finally:
        providers.HTTP_TRANSPORT = None


def test_opening_prepare_and_ready_consumes_cached_greeting_with_tts() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"opening_greeting_user_{suffix}"
    voice_id = f"voice_opening_greeting_{suffix}"
    resource_id = f"resource-opening-{suffix}"
    speaker = f"speaker-opening-{suffix}"
    audio = base64.b64encode(b"ID3" + b"o" * 220).decode()
    tts_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        tts_bodies.append(body)
        assert request.headers["X-Api-Resource-Id"] == resource_id
        assert body["req_params"]["speaker"] == speaker
        assert any("ぁ" <= ch <= "ヿ" for ch in body["req_params"]["text"])
        return httpx.Response(200, content=f'data: {{"data":"{audio}"}}\n'.encode())

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_opening_greeting_tts_{suffix}",
                    kind="tts",
                    provider="volc_seed_tts",
                    base_url="https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                    secrets={"x_api_key": "tts-key"},
                ),
            )
            session.merge(
                TtsVoiceProfile(
                    voice_id=voice_id,
                    provider_id=config.provider_id,
                    label="opening greeting",
                    speaker=speaker,
                    resource_id=resource_id,
                    language="ja",
                    enabled=True,
                )
            )
            user = session.get(User, user_id)
            character = session.get(Character, "atri")
            user.story_completed = True
            user.tts_enabled = True
            character.tts_voice_profile_id = voice_id
            session.commit()

            prepared = prepare_opening(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-09T20:00:00+08:00"),
                allow_llm=False,
            )
            assert prepared["kind"] == "greeting"
            cache = session.get(OpeningCache, prepared["cache_id"])
            assert cache is not None and cache.status == "ready"
            ready = consume_ready_opening(
                session,
                user_id=user_id,
                character_id="atri",
                session_id=f"opening_session_{suffix}",
                local_time=datetime.fromisoformat("2026-06-09T20:01:00+08:00"),
            )
            assert ready.event_type == "dialogue"
            assert ready.payload["cached"] is True
            assert ready.payload["opening_kind"] == "greeting"
            assert ready.payload["reply_mode"] == "opening"
            assert len(ready.payload["lines"]) >= 2
            assert ready.payload["lines"][0]["tts_audio_url"].startswith("/media/")
            session.refresh(cache)
            assert cache.status == "consumed"
            assert tts_bodies
    finally:
        providers.HTTP_TRANSPORT = None


def test_prepare_due_openings_prewarms_proactive_cache() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"opening_prewarm_user_{suffix}"
    llm_calls = 0
    event_id = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal llm_calls
        llm_calls += 1
        body = json.loads(request.content.decode())
        if "Context JSON:" in body["messages"][1]["content"]:
            llm_calls -= 1
            return _llm_json_response(
                {
                    "should_send": True,
                    "selected_event_id": event_id,
                    "reason": "预热主动开场前允许投递。",
                    "next_check_after_minutes": 60,
                }
            )
        assert "主动想告诉用户" in body["messages"][1]["content"]
        content = json.dumps(
            {
                "reply_mode": "normal",
                "pace_reason": "服务端预生成主动开场。",
                "lines": [{"text": "我刚刚想把这件小事提前告诉你。", "emotion": "happy", "pose": "happy"}],
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

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_opening_prewarm_llm_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.tts_enabled = False
            session.commit()
            event = create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="memory",
                source_id=f"prewarm_memory_{suffix}",
                title="亚托莉有话想说",
                text="主动想告诉用户：我想起了一件适合打开时说的小事。",
                priority=90,
                scheduled_at=datetime.fromisoformat("2026-06-09T11:50:00+08:00"),
            )
            assert event is not None
            event_id = event.proactive_event_id
            session.commit()

            prewarmed = prepare_due_openings(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-09T12:00:00+08:00"),
                generate_news=False,
            )
            assert prewarmed["prepared"] == 1
            session.refresh(event)
            assert event.prepared_at
            assert json.loads(event.prepared_payload_json)
            cache = session.execute(
                select(OpeningCache).where(OpeningCache.proactive_event_id == event.proactive_event_id, OpeningCache.status == "ready")
            ).scalar_one()
            assert cache.kind == "proactive"

            ready = consume_ready_opening(
                session,
                user_id=user_id,
                character_id="atri",
                session_id=f"opening_prewarm_session_{suffix}",
                local_time=datetime.fromisoformat("2026-06-09T12:01:00+08:00"),
            )
            assert ready.payload["cached"] is True
            assert ready.payload["opening_kind"] == "proactive"
            assert ready.payload["proactive_event_id"] == event.proactive_event_id
            assert llm_calls == 1
    finally:
        providers.HTTP_TRANSPORT = None


def test_repeated_moment_like_dedupes_proactive_event_by_moment() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"moment_like_dedupe_user_{suffix}"
    moment_id = "seed_moment_atri_walk"
    first = client.post(f"/api/moments/{moment_id}/like?user_id={user_id}").json()
    second = client.post(f"/api/moments/{moment_id}/like?user_id={user_id}").json()
    assert first["ok"] is True and second["ok"] is True
    with SessionLocal() as session:
        rows = session.execute(
            select(ProactiveEvent).where(
                ProactiveEvent.user_id == user_id,
                ProactiveEvent.source_type == "moment_interaction",
                ProactiveEvent.dedupe_key == f"moment_interaction:{user_id}:{moment_id}:like",
            )
        ).scalars().all()
        assert len(rows) == 1


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
            ensure_seed(session, user_id="proactive_open_user", character_id="atri")
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
                character_id="atri",
                source_type="moment_interaction",
                source_id="mi_open",
                title="亚托莉注意到了你的互动",
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
                    character_id="atri",
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


def test_news_candidate_uses_trend_radar_all_keywords() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    providers._TREND_RADAR_CACHE.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "https://trend.example/api/trends.json"
        return httpx.Response(200, json=_trend_radar_payload())

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            user_id = f"trend_news_user_{suffix}"
            ensure_seed(session, user_id=user_id, character_id="atri")
            user = session.get(User, user_id)
            user.interest_topics_json = json.dumps(["AI 游戏", "完全不匹配"], ensure_ascii=False)
            user.news_enabled = True
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_trend_news_{suffix}",
                    kind="search",
                    provider="trend_radar",
                    base_url="https://trend.example",
                    metadata={"cache_minutes": 0, "max_titles": 2},
                ),
            )
            session.commit()
            event = ensure_news_candidate(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-10T12:15:00+08:00"),
            )
            assert event is not None
            payload = json.loads(event.payload_json)
            assert event.source_type == "news"
            assert payload["topic"] == "AI 游戏"
            assert payload["keyword_group"] == "AI 游戏"
            assert payload["generated_at"] == "2026-06-10T12:00:00+08:00"
            assert len(payload["sources"]) == 2
            assert event.priority < 90
    finally:
        providers.HTTP_TRANSPORT = None
        providers._TREND_RADAR_CACHE.clear()


def test_news_candidate_skips_deduped_trend_radar_topic() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    providers._TREND_RADAR_CACHE.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_trend_radar_payload())

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            user_id = f"trend_dedupe_user_{suffix}"
            ensure_seed(session, user_id=user_id, character_id="atri")
            user = session.get(User, user_id)
            user.interest_topics_json = json.dumps(["AI 游戏", "芯片"], ensure_ascii=False)
            user.news_enabled = True
            create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="news",
                source_id="existing_ai_game",
                title="existing",
                text="already created",
                dedupe_key=f"news:{user_id}:AI 游戏:2026-06-10",
            )
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_trend_dedupe_{suffix}",
                    kind="search",
                    provider="trend_radar",
                    base_url="https://trend.example/api/trends.json",
                    metadata={"cache_minutes": 0, "max_titles": 3},
                ),
            )
            session.commit()
            event = ensure_news_candidate(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-10T13:00:00+08:00"),
            )
            assert event is not None
            payload = json.loads(event.payload_json)
            assert payload["topic"] == "芯片"
            assert event.dedupe_key == f"news:{user_id}:芯片:2026-06-10"
    finally:
        providers.HTTP_TRANSPORT = None
        providers._TREND_RADAR_CACHE.clear()


def test_news_candidate_does_not_call_disabled_ark_when_trend_radar_has_no_match() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    calls: list[str] = []
    providers._TREND_RADAR_CACHE.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == "https://trend.example/api/trends.json":
            return httpx.Response(200, json={**_trend_radar_payload(), "trends": []})
        body = json.loads(request.content.decode())
        assert "AI 游戏" in json.dumps(body, ensure_ascii=False)
        return httpx.Response(
            200,
            json={
                "output_text": "找到一条 AI 游戏新闻。",
                "output": [
                    {
                        "content": [
                            {
                                "annotations": [
                                    {
                                        "title": "AI 游戏新闻",
                                        "url": "https://ark.example/news",
                                        "published_at": "2026-06-10T11:00:00+08:00",
                                        "summary": "Ark 搜索来源摘要",
                                    }
                                ]
                            }
                        ]
                    }
                ],
            },
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            user_id = f"trend_fallback_user_{suffix}"
            ensure_seed(session, user_id=user_id, character_id="atri")
            user = session.get(User, user_id)
            user.interest_topics_json = json.dumps(["AI 游戏"], ensure_ascii=False)
            user.news_enabled = True
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_trend_fallback_ark_{suffix}",
                    kind="search",
                    provider="volc_ark_web_search",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-response-search",
                    secrets={"api_key": "ark-key"},
                ),
            )
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_trend_fallback_{suffix}",
                    kind="search",
                    provider="trend_radar",
                    base_url="https://trend.example",
                    metadata={"cache_minutes": 0},
                ),
            )
            session.commit()
            event = ensure_news_candidate(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-10T14:00:00+08:00"),
            )
            assert event is None
            assert calls == ["https://trend.example/api/trends.json"]
    finally:
        providers.HTTP_TRANSPORT = None
        providers._TREND_RADAR_CACHE.clear()


def test_news_candidate_requires_verifiable_publish_time() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": [{"content": [{"annotations": [{"title": "无时间", "url": "https://example.com"}]}]}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id="news_skip_user", character_id="atri")
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
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-09T12:00:00+08:00"),
            ) is None
            assert session.query(ProactiveEvent).filter(ProactiveEvent.user_id == "news_skip_user", ProactiveEvent.source_type == "news").count() == 0
    finally:
        providers.HTTP_TRANSPORT = None
 
 
def test_admin_proactive_tools_generate_judge_and_schedule() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"admin_proactive_tools_{suffix}"
    selected_event_id = ""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert "Context JSON:" in body["messages"][1]["content"]
        return _llm_json_response(
            {
                "should_send": True,
                "selected_event_id": selected_event_id,
                "reason": "admin immediate judge selected the generated candidate.",
                "next_check_after_minutes": 10,
            }
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_admin_proactive_tools_llm_{suffix}",
                    kind="llm_task",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.notifications_enabled = True
            user.proactive_daily_limit = "unlimited"
            session.commit()

        schedule = client.get("/api/admin/ai-schedule/today", params={"user_id": user_id, "character_id": "atri"})
        assert schedule.status_code == 200
        assert schedule.json()["items"]

        generated = client.post(
            "/api/admin/proactive-events/generate",
            json={
                "user_id": user_id,
                "character_id": "atri",
                "source_type": "schedule",
                "manual_only": True,
                "due_now": True,
                "priority": 92,
                "text": "后台测试：今天 AI 日程里有一件事想主动告诉用户。",
            },
        )
        assert generated.status_code == 200
        item = generated.json()["items"][0]
        selected_event_id = item["proactive_event_id"]
        assert item["source_type"] == "schedule"

        judged = client.post(
            "/api/admin/proactive-events/judge",
            json={
                "user_id": user_id,
                "character_id": "atri",
                "ignore_next_check": True,
                "generate_news": False,
                "generate_weather": False,
                "idle_seconds": 120,
            },
        )
        assert judged.status_code == 200
        payload = judged.json()
        assert payload["pending"]["event"]["proactive_event_id"] == selected_event_id
        assert payload["judgement"]["selected_event_id"] == selected_event_id
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


def test_safe_image_request_policy_builds_supported_kinds_without_content_gate() -> None:
    with SessionLocal() as session:
        character = session.get(Character, "atri")
        scenery = build_safe_image_request(
            kind="scenery",
            scene_hint="樱花雨后的安静街道",
            character=character,
            user_id="safe_image_user",
            character_id="atri",
            source_id="scene",
        )
        assert scenery.asset_type == "experience_cg"
        assert "No people" in scenery.prompt

        item = build_safe_image_request(
            kind="object_pet",
            scene_hint="窗边的一杯茶和樱花书签",
            character=character,
            user_id="safe_image_user",
            character_id="atri",
            source_id="object",
        )
        assert item.asset_type == "experience_cg"
        assert "No humans" in item.prompt

        selfie = build_safe_image_request(
            kind="character_selfie",
            scene_hint="窗边喝茶的日常自拍",
            character=character,
            user_id="safe_image_user",
            character_id="atri",
            source_id="selfie",
        )
        assert selfie.asset_type == "character_selfie"
        assert selfie.reference_image_path is not None
        assert selfie.reference_image_path.name == "selfie.png"
        assert "same fictional adult anime catgirl" in selfie.prompt

        direct_prompt = build_safe_image_request(
            kind="character_selfie",
            scene_hint="用户给的自拍短提示",
            character=character,
            user_id="safe_image_user",
            character_id="atri",
            source_id="direct_prompt",
        )
        assert "用户给的自拍短提示" in direct_prompt.prompt


def test_proactive_image_requires_explicit_generation_flag() -> None:
    with SessionLocal() as session:
        ensure_seed(session, user_id="proactive_no_image_user", character_id="atri")
        event = create_proactive_event(
            session,
            user_id="proactive_no_image_user",
            character_id="atri",
            source_type="memory",
            source_id="memory_no_image",
            title="小樱想起了一件事",
            text="我刚刚想起你之前提到过的事。",
            priority=58,
            dedupe_key="memory:no-image",
            payload={"memory": "普通聊天提醒，不需要 CG"},
        )
        assert event is not None
        character = session.get(Character, "atri")
        assert ensure_proactive_event_image(session, event, character=character) == ""
        payload = json.loads(event.payload_json)
        assert not payload.get("media_asset_id")
        assert not payload.get("proactive_media_asset_id")


def test_character_selfie_uses_reference_image_and_records_asset() -> None:
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"s" * 100

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, headers={"content-type": "image/png"}, content=image_bytes)
        body = json.loads(request.content.decode())
        assert str(request.url) == "https://ark.cn-beijing.volces.com/api/v3/images/generations"
        assert body["image"][0].startswith("data:image/png;base64,")
        assert body["response_format"] == "url"
        assert body["size"] == "1920x1920"
        assert "same fictional adult anime catgirl" in body["prompt"]
        assert "same fictional adult anime catgirl" in body["prompt"]
        return httpx.Response(200, json={"data": [{"url": "https://image.example/selfie.png"}]})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id="selfie_user", character_id="atri")
            config = upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_image_selfie_doubao",
                    kind="image",
                    provider="doubao_seedream",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-seedream-5-0-260128",
                    secrets={"api_key": "ark-key"},
                    metadata={"size": "1920x1920", "output_format": "png", "response_format": "url"},
                ),
            )
            character = session.get(Character, "atri")
            asset = generate_safe_image(
                session,
                config=config,
                kind="character_selfie",
                scene_hint="窗边喝茶的日常自拍",
                character=character,
                user_id="selfie_user",
                character_id="atri",
                source_id="manual_test",
                cooldown_seconds=0,
            )
            assert asset.asset_type == "character_selfie"
            assert asset.ai_generated is True
            assert asset.source_event_id.startswith("selfie:selfie_user:atri:")
            assert "selfie.png" in asset.reference_image_ids_json
            assert Path(asset.local_path).exists()
    finally:
        providers.HTTP_TRANSPORT = None


def test_story_flow_without_llm() -> None:
    with SessionLocal() as session:
        ensure_seed(session, user_id="story_flow_user", character_id="atri")
        user = session.get(User, "story_flow_user")
        character = session.get(Character, "atri")
        user.tts_enabled = False
        character.tts_voice_profile_id = ""
        user.story_completed = False
        session.commit()
    response = client.post(
        "/api/events",
        json={"event_type": "app_opened", "user_id": "story_flow_user", "character_id": "atri", "session_id": "story_test", "payload": {}},
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
            character_id="atri",
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


def test_ensure_schedule_refreshes_elapsed_pending_slots() -> None:
    user_id = "schedule_refresh_user"
    with SessionLocal() as session:
        ensure_seed(session, user_id=user_id, character_id="atri")
        session.merge(
            ScheduleSlot(
                slot_id="slot_test_refresh_elapsed",
                schedule_date="2026-06-09",
                user_id=user_id,
                character_id="atri",
                start_at="2026-06-09T08:00:00+08:00",
                end_at="2026-06-09T08:15:00+08:00",
                activity_title="早饭",
                activity_type="daily",
                actual_status="pending",
            )
        )
        session.merge(
            ScheduleSlot(
                slot_id="slot_test_refresh_interrupted",
                schedule_date="2026-06-09",
                user_id=user_id,
                character_id="atri",
                start_at="2026-06-09T08:15:00+08:00",
                end_at="2026-06-09T08:30:00+08:00",
                activity_title="早饭",
                activity_type="daily",
                actual_status="interrupted",
            )
        )
        session.commit()

        ensure_schedule(session, user_id=user_id, character_id="atri", day=datetime.fromisoformat("2026-06-09T12:00:00+08:00"))
        elapsed = session.get(ScheduleSlot, "slot_test_refresh_elapsed")
        interrupted = session.get(ScheduleSlot, "slot_test_refresh_interrupted")
        assert elapsed.actual_status == "completed"
        assert interrupted.actual_status == "interrupted"


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
            ensure_seed(session, user_id="moment_user", character_id="atri")
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
                    character_id="atri",
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
            result = run_daily_cycle(session, user_id="moment_user", character_id="atri", day=datetime.fromisoformat("2026-06-09T12:00:00+08:00"))
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


def test_daily_cycle_moment_generates_safe_character_selfie() -> None:
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"m" * 100
    chat_requests: list[dict[str, object]] = []
    image_requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, headers={"content-type": "image/png"}, content=image_bytes)
        body = json.loads(request.content.decode())
        url = str(request.url)
        if url == "https://ark.cn-beijing.volces.com/api/v3/chat/completions":
            chat_requests.append(body)
            if len(chat_requests) == 1:
                payload = {
                    "text": "窗边的茶还冒着热气，所以顺手拍了一张。",
                    "mood": "平静",
                    "photo_kind": "character_selfie",
                    "photo_prompt": "窗边喝茶的日常自拍",
                    "likes": ["图书委员澪"],
                    "comments": [{"actor_name": "同行同学", "content": "这张很像你的气氛。"}],
                }
            else:
                payload = {
                    "lines": [
                        {
                            "line_id": "line_proactive_selfie",
                            "text": "刚才窗边那件事，我想当面和你讲。",
                            "emotion": "happy",
                            "pose": "happy",
                        }
                    ],
                    "relation_delta": {},
                    "reply_mode": "proactive",
                    "pace_reason": "主动事件预热。",
                }
            content = json.dumps(payload, ensure_ascii=False)
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        if url == "https://ark.cn-beijing.volces.com/api/v3/images/generations":
            image_requests.append(body)
            if "image" in body:
                assert body["image"][0].startswith("data:image/png;base64,")
                assert "same fictional adult anime catgirl" in body["prompt"]
            if len(image_requests) == 1:
                assert "窗边喝茶" in body["prompt"]
            else:
                assert "主动聊天" in body["prompt"] or "Galgame CG" in body["prompt"]
            return httpx.Response(200, json={"data": [{"url": f"https://image.example/selfie-{len(image_requests)}.png"}]})
        raise AssertionError(url)

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id="moment_selfie_user", character_id="atri")
            user = session.get(User, "moment_selfie_user")
            assert user is not None
            user.story_completed = True
            user.notifications_enabled = True
            user.tts_enabled = False
            session.query(OpeningCache).filter(OpeningCache.user_id == "moment_selfie_user").delete(synchronize_session=False)
            session.query(MediaAsset).filter(MediaAsset.source_event_id.like("selfie:moment_selfie_user:atri:%")).delete(synchronize_session=False)
            session.query(MomentInteraction).filter(
                MomentInteraction.moment_id.in_(
                    session.query(Moment.moment_id).filter(Moment.text.contains("窗边的茶还冒着热气"))
                )
            ).delete(synchronize_session=False)
            session.query(Moment).filter(Moment.text.contains("窗边的茶还冒着热气")).delete(synchronize_session=False)
            session.query(Experience).filter(Experience.source_schedule_slot_id == "slot_test_selfie_moment").delete(synchronize_session=False)
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_moment_selfie_llm",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_moment_selfie_task_llm",
                    kind="llm_task",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id="test_moment_selfie_image",
                    kind="image",
                    provider="doubao_seedream",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-seedream-5-0-260128",
                    secrets={"api_key": "ark-key"},
                    metadata={"size": "1920x1920", "output_format": "png", "response_format": "url"},
                ),
            )
            session.merge(
                ScheduleSlot(
                    slot_id="slot_test_selfie_moment",
                    schedule_date="2026-06-09",
                    user_id="moment_selfie_user",
                    character_id="atri",
                    start_at="2026-06-09T16:00:00+08:00",
                    end_at="2026-06-09T17:00:00+08:00",
                    activity_title="窗边休息",
                    activity_type="daily",
                    location="教室窗边",
                    actual_status="completed",
                    can_generate_moment=True,
                    can_generate_photo=True,
                    salience=90,
                )
            )
            session.commit()
            result = run_daily_cycle(session, user_id="moment_selfie_user", character_id="atri", day=datetime.fromisoformat("2026-06-09T12:00:00+08:00"))
            assert result["moments"] >= 1
            moment = session.query(Moment).filter(Moment.text.contains("窗边的茶还冒着热气")).order_by(Moment.created_at.desc()).first()
            assert moment is not None
            assert moment.media_asset_id
            asset = session.get(MediaAsset, moment.media_asset_id)
            assert asset is not None
            assert asset.asset_type == "character_selfie"
            assert "selfie.png" in asset.reference_image_ids_json
            proactive = session.query(ProactiveEvent).filter(
                ProactiveEvent.user_id == "moment_selfie_user",
                ProactiveEvent.source_type == "schedule",
                ProactiveEvent.source_id == moment.source_experience_id,
            ).order_by(ProactiveEvent.created_at.desc()).first()
            assert proactive is not None
            proactive_payload = json.loads(proactive.payload_json)
            assert proactive_payload["generate_image"] is True
            assert proactive_payload["image_prompt"]
            assert not proactive_payload.get("moment_id")
            assert not proactive_payload.get("moment_media_asset_id")
            assert not proactive_payload.get("proactive_media_asset_id")
            assert len(image_requests) == 1
            prepared = prepare_opening(
                session,
                user_id="moment_selfie_user",
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-09T18:00:00+08:00"),
                proactive_event_id=proactive.proactive_event_id,
            )
            assert prepared["prepared"] is True
            assert prepared["kind"] == "proactive"
            proactive_payload = json.loads(proactive.payload_json)
            assert proactive_payload["proactive_media_asset_id"]
            assert proactive_payload["media_asset_id"] == proactive_payload["proactive_media_asset_id"]
            assert proactive_payload["proactive_media_asset_id"] != moment.media_asset_id
            assert len(image_requests) == 2
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


def test_calendar_returns_only_important_events() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"calendar_event_user_{suffix}"
    with SessionLocal() as session:
        ensure_seed(session, user_id=user_id, character_id="atri")
        session.add(
            CalendarEvent(
                event_id=f"cal_test_date_event_{suffix}",
                user_id=user_id,
                character_id="atri",
                event_date="2026-06-12",
                title="第一次约会",
                category="relationship",
                description="你们约好一起去看展。",
                salience=95,
                source_type="admin",
            )
        )
        session.commit()

    june = client.get(f"/api/calendar?month=2026-06&user_id={user_id}&character_id=atri").json()
    titles = [item["title"] for item in june["days"]]
    assert "第一次约会" in titles
    assert "端午节" in titles
    assert "睡觉" not in titles
    assert "想和你聊天" not in titles
    date_event = next(item for item in june["days"] if item["title"] == "第一次约会")
    assert date_event["category"] == "relationship"
    assert date_event["day_note"]

    with SessionLocal() as session:
        user = session.get(User, user_id)
        anniversary_month = str(user.created_at)[:7]
    anniversary = client.get(f"/api/calendar?month={anniversary_month}&user_id={user_id}&character_id=atri").json()
    assert "相识纪念日" in [item["title"] for item in anniversary["days"]]


def test_admin_user_relation_memory_and_calendar_event_crud() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"admin_user_{suffix}"
    created = client.post(
        "/api/admin/users",
        json={
            "user_id": user_id,
            "display_name": "测试用户",
            "story_completed": True,
            "interest_topics": ["日程调试"],
            "proactive_daily_limit": "medium",
        },
    ).json()
    assert created["user_id"] == user_id
    assert created["display_name"] == "测试用户"
    assert created["proactive_next_check_at"] == ""
    assert created["proactive_judgement"] == {}
    users_page = client.get(f"/api/admin/users?q={user_id}&page=1&page_size=5").json()
    assert users_page["total"] >= 1
    assert users_page["page"] == 1
    assert any(item["user_id"] == user_id for item in users_page["items"])

    updated = client.put(
        f"/api/admin/users/{user_id}",
        json={
            "display_name": "测试用户改",
            "tts_enabled": False,
            "news_enabled": False,
            "proactive_daily_limit": "high",
            "profile": {"communication_style": "短句直接", "notes": ["喜欢明确计划"]},
        },
    ).json()
    assert updated["display_name"] == "测试用户改"
    assert updated["tts_enabled"] is False
    assert updated["proactive_daily_limit"] == "high"
    assert updated["profile"]["communication_style"] == "短句直接"

    relation = client.put(
        f"/api/admin/users/{user_id}/relation",
        json={"affection": 321, "trust": 222, "dependency": 111, "mood": -12, "relationship_stage": "测试阶段"},
    ).json()
    assert relation["affection"] == 321
    assert relation["relationship_stage"] == "测试阶段"
    assert relation["attitude_band"] in {"good", "neutral", "bad"}
    assert relation["attitude_text"]

    memory = client.post(
        f"/api/admin/users/{user_id}/memories",
        json={
            "content": "用户手动添加的测试记忆",
            "layer": "core",
            "importance": 0.8,
            "confidence": 0.9,
            "tags": ["manual", "test"],
            "metadata": {"kind": "admin"},
        },
    ).json()
    assert memory["content"] == "用户手动添加的测试记忆"
    assert memory["tags"] == ["manual", "test"]
    assert memory["metadata"]["kind"] == "admin"
    assert memory["vector_status"] in {"ready", "error", "hidden", "pending"}
    other_memory = client.post(
        f"/api/admin/users/{user_id}/memories",
        json={
            "character_id": "debug_character",
            "content": "admin alternate character memory",
            "layer": "core",
            "importance": 0.6,
        },
    ).json()
    assert other_memory["character_id"] == "debug_character"
    memories_page = client.get(f"/api/admin/users/{user_id}/memories?q=测试记忆&page=1&page_size=5").json()
    assert memories_page["total"] == 1
    assert memories_page["items"][0]["memory_id"] == memory["memory_id"]
    assert memories_page["items"][0]["tags"] == ["manual", "test"]
    atri_memories_page = client.get(f"/api/admin/users/{user_id}/memories?character_id=atri&page=1&page_size=5").json()
    assert {item["memory_id"] for item in atri_memories_page["items"]} == {memory["memory_id"]}
    alternate_memories_page = client.get(f"/api/admin/users/{user_id}/memories?character_id=debug_character&page=1&page_size=5").json()
    assert {item["memory_id"] for item in alternate_memories_page["items"]} == {other_memory["memory_id"]}
    hidden = client.put(f"/api/admin/memories/{memory['memory_id']}", json={"hidden": True}).json()
    assert hidden["hidden"] is True
    assert client.delete(f"/api/admin/memories/{memory['memory_id']}").json()["ok"] is True
    assert client.delete(f"/api/admin/memories/{other_memory['memory_id']}").json()["ok"] is True

    event = client.post(
        "/api/admin/calendar-events",
        json={
            "user_id": user_id,
            "character_id": "atri",
            "date": "2026-06-18",
            "title": "测试约会日",
            "category": "relationship",
            "salience": 88,
        },
    ).json()
    assert event["title"] == "测试约会日"
    calendar_page = client.get(f"/api/admin/calendar-events?user_id={user_id}&character_id=atri&q=测试约会日&page=1&page_size=5").json()
    assert calendar_page["total"] == 1
    assert calendar_page["items"][0]["event_id"] == event["event_id"]
    changed = client.put(f"/api/admin/calendar-events/{event['event_id']}", json={"hidden": True, "title": "测试约会日改"}).json()
    assert changed["hidden"] is True
    assert changed["title"] == "测试约会日改"
    assert client.delete(f"/api/admin/calendar-events/{event['event_id']}").json()["ok"] is True

    assert client.delete(f"/api/admin/users/{user_id}").json()["ok"] is True


def test_split_expression_tag_strips_inline_marker() -> None:
    text, expression = _split_expression_tag("你好呀[shy]")
    assert text == "你好呀"
    assert expression == "shy"


def test_normalize_line_text_splits_long_sentence() -> None:
    chunks = _normalize_line_text("这是一句非常非常非常非常非常非常非常非常长的台词，需要被拆开。")
    assert len(chunks) >= 2
    assert all(len(chunk) <= 28 for chunk in chunks)


def test_normalize_line_text_keeps_common_words_together() -> None:
    chunks = _normalize_line_text("……不过看你这么闷，我就勉为其难告诉你，我哪来那么多闲工夫管别人。", max_chars=28)
    assert chunks == ["……不过看你这么闷，我就勉为其难告诉你", "我哪来那么多闲工夫管别人。"]


def test_consume_prefers_proactive_over_greeting() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"opening_priority_user_{suffix}"
    event_id = ""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        if "用户刚刚从" in body["messages"][1]["content"]:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "reply_mode": "opening",
                                        "pace_reason": "主动入口展开。",
                                        "lines": [
                                            {"text": "你来了呀。", "emotion": "happy", "pose": "happy"},
                                            {"text": "我正想跟你说件事。", "emotion": "shy", "pose": "shy"},
                                        ],
                                        "normal_replies": [],
                                        "key_replies": [],
                                        "relation_delta": {"affection": 0, "trust": 0, "dependency": 0, "mood": 0},
                                        "memory_candidates": [],
                                        "interest_topics": [],
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"error": "unexpected"})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_opening_priority_llm_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            user = session.get(User, user_id)
            user.story_completed = True
            user.tts_enabled = False
            session.commit()
            greeting = prepare_opening(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-09T08:00:00+08:00"),
                allow_llm=False,
            )
            proactive = create_proactive_event(
                session,
                user_id=user_id,
                character_id="atri",
                source_type="schedule",
                title="想你了",
                text="刚刚有点想你。",
            )
            assert proactive is not None
            session.commit()
            event_id = proactive.proactive_event_id
            prepared = prepare_opening(
                session,
                user_id=user_id,
                character_id="atri",
                local_time=datetime.fromisoformat("2026-06-09T08:06:00+08:00"),
                proactive_event_id=event_id,
                allow_llm=True,
            )
            assert prepared["kind"] == "proactive"
            ready = consume_ready_opening(
                session,
                user_id=user_id,
                character_id="atri",
                session_id=f"priority_{suffix}",
                local_time=datetime.fromisoformat("2026-06-09T08:07:00+08:00"),
            )
            assert ready.payload["opening_kind"] == "proactive"
            greeting_cache = session.get(OpeningCache, greeting["cache_id"])
            assert greeting_cache is not None
            assert greeting_cache.status == "ready"
    finally:
        providers.HTTP_TRANSPORT = None


def test_touch_reaction_has_no_relation_delta() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"touch_user_{suffix}"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        if "触摸反应" in body["messages"][0]["content"] or "触摸了你的" in body["messages"][1]["content"]:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "lines": [
                                            {"text": "别乱摸啦。", "emotion": "shy", "expression": "shy", "motion": "TapHead"},
                                            {"text": "不过……也不算讨厌。", "emotion": "happy", "expression": "happy", "motion": "TapHead"},
                                        ]
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"error": "unexpected"})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            user = session.get(User, user_id)
            character = session.get(Character, "atri")
            assert user is not None and character is not None
            user.tts_enabled = False
            character.tts_voice_profile_id = ""
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_touch_llm_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            session.commit()
            refresh_touch_reaction_pools(session, user_id=user_id, character_id="atri")
            result = consume_touch_reaction(session, user_id=user_id, character_id="atri", hit_area="head")
            assert result["text"]
            assert "affection" not in result
    finally:
        providers.HTTP_TRANSPORT = None


def test_touch_prompt_includes_japanese_schema_for_ja_voice() -> None:
    with SessionLocal() as session:
        ensure_seed(session, character_id="atri")
        character = session.get(Character, "atri")
        relation = session.execute(
            select(RelationState).where(RelationState.user_id == "demo_user", RelationState.character_id == "atri")
        ).scalar_one()
        assert character is not None
        prompt = _touch_prompt(
            session, character, relation, "head", "mid", appearance_id="neko", requires_japanese_tts=True
        )
        assert "tts_text_ja" in prompt
        assert "中文供界面显示" in prompt
        zh_prompt = _touch_prompt(
            session, character, relation, "head", "mid", appearance_id="neko", requires_japanese_tts=False
        )
        assert "tts_text_ja" not in zh_prompt


def test_touch_tier_style_guidance_differs() -> None:
    low = _tier_style_guidance("low", "")
    mid = _tier_style_guidance("mid", "")
    high = _tier_style_guidance("high", " 可暧昧。")
    assert low != mid
    assert "不要暧昧" in low
    assert "可暧昧" in high


def test_refresh_touch_pools_honors_explicit_tier() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"touch_tier_user_{suffix}"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        if "触摸了你的" in body["messages"][1]["content"]:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "lines": [
                                            {"text": "别碰。", "emotion": "shy", "expression": "shy", "motion": "TapHead"},
                                            {"text": "会害羞的。", "emotion": "happy", "expression": "happy", "motion": "TapHead"},
                                        ]
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"error": "unexpected"})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            relation = session.execute(
                select(RelationState).where(RelationState.user_id == user_id, RelationState.character_id == "atri")
            ).scalar_one()
            relation.affection = 80
            user = session.get(User, user_id)
            character = session.get(Character, "atri")
            assert user is not None and character is not None
            user.tts_enabled = False
            character.tts_voice_profile_id = ""
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_touch_tier_llm_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            session.commit()
            result = refresh_touch_reaction_pools(
                session,
                user_id=user_id,
                character_id="atri",
                hit_area="head",
                tier="low",
                force=True,
            )
            assert result["tiers_refreshed"] == ["low"]
            pool = session.execute(
                select(TouchReactionPool).where(
                    TouchReactionPool.user_id == user_id,
                    TouchReactionPool.character_id == "atri",
                    TouchReactionPool.hit_area == "head",
                    TouchReactionPool.tier == "low",
                )
            ).scalar_one()
            assert len(json.loads(pool.lines_json)) >= 2
    finally:
        providers.HTTP_TRANSPORT = None


def test_refresh_touch_pools_can_refresh_all_tiers() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"touch_all_tier_user_{suffix}"
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        if "触摸了你的" in body["messages"][1]["content"]:
            calls["count"] += 1
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "lines": [
                                            {"text": "嗯？", "emotion": "calm", "expression": "calm", "motion": "TapBody"},
                                            {"text": "怎么啦。", "emotion": "happy", "expression": "happy", "motion": "TapBody"},
                                        ]
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"error": "unexpected"})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            user = session.get(User, user_id)
            character = session.get(Character, "atri")
            assert user is not None and character is not None
            user.tts_enabled = False
            character.tts_voice_profile_id = ""
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_touch_all_tier_llm_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            session.commit()
            result = refresh_touch_reaction_pools(
                session,
                user_id=user_id,
                character_id="atri",
                hit_area="head",
                tiers=["low", "mid", "high"],
                force=True,
            )
            assert set(result["tiers_refreshed"]) == {"low", "mid", "high"}
            assert calls["count"] == 3
    finally:
        providers.HTTP_TRANSPORT = None


def test_touch_pool_generation_requires_tts_text_ja_for_japanese_voice() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"touch_ja_user_{suffix}"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        if "触摸了你的" in body["messages"][1]["content"]:
            assert "tts_text_ja" in body["messages"][1]["content"]
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "lines": [
                                            {
                                                "text": "别碰。",
                                                "tts_text_ja": "触らないで。",
                                                "emotion": "shy",
                                                "expression": "shy",
                                                "motion": "TapHead",
                                            },
                                            {
                                                "text": "会害羞的。",
                                                "tts_text_ja": "恥ずかしいよ。",
                                                "emotion": "happy",
                                                "expression": "happy",
                                                "motion": "TapHead",
                                            },
                                        ]
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"error": "unexpected"})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="atri")
            user = session.get(User, user_id)
            character = session.get(Character, "atri")
            assert user is not None and character is not None
            user.tts_enabled = True
            voice = TtsVoiceProfile(
                voice_id=f"voice_ja_touch_{suffix}",
                provider_id="",
                label="测试日文",
                speaker="ja_speaker",
                resource_id="ja_resource",
                language="ja",
                enabled=True,
            )
            session.add(voice)
            character.tts_voice_profile_id = voice.voice_id
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_touch_ja_llm_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            session.commit()
            refresh_touch_reaction_pools(
                session,
                user_id=user_id,
                character_id="atri",
                hit_area="head",
                tier="mid",
                force=True,
            )
            pool = session.execute(
                select(TouchReactionPool).where(
                    TouchReactionPool.user_id == user_id,
                    TouchReactionPool.character_id == "atri",
                    TouchReactionPool.hit_area == "head",
                    TouchReactionPool.tier == "mid",
                )
            ).scalar_one()
            lines = json.loads(pool.lines_json)
            assert lines[0]["tts_text_ja"] == "触らないで。"
    finally:
        providers.HTTP_TRANSPORT = None


def test_list_touch_pool_admin_all_view() -> None:
    with SessionLocal() as session:
        ensure_seed(session, character_id="atri")
        payload = list_touch_pool_admin(session, user_id="demo_user", character_id="atri", hit_area="head", tier="all")
        assert payload["view_mode"] == "all"
        assert len(payload["tiers"]) == 3


def test_consume_touch_reaction_missing_pool_raises() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"touch_missing_pool_user_{suffix}"
    with SessionLocal() as session:
        ensure_seed(session, user_id=user_id, character_id="atri")
        with pytest.raises(ProviderError, match="touch_pool_missing"):
            consume_touch_reaction(session, user_id=user_id, character_id="atri", hit_area="head")


def test_live2d_touch_api_returns_404_when_pool_missing() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"touch_missing_api_user_{suffix}"
    with SessionLocal() as session:
        ensure_seed(session, user_id=user_id, character_id="atri")
        session.commit()
    response = client.post(
        "/api/live2d/touch",
        json={"user_id": user_id, "character_id": "atri", "appearance_id": "neko", "hit_area": "head"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["pool_missing"] is True


def test_touch_pool_coverage_admin_reports_matrix() -> None:
    with SessionLocal() as session:
        ensure_seed(session, user_id="demo_user", character_id="atri")
        payload = touch_pool_coverage_admin(session, user_id="demo_user", character_id="atri", appearance_id="neko")
        assert payload["relation_tier"] in {"low", "mid", "high"}
        assert len(payload["matrix"]) == len(payload["hit_areas"]) * 3
        assert "bundle_preview" in payload


def test_consume_touch_reaction_can_use_shared_pool() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    source_user = f"touch_source_user_{suffix}"
    target_user = f"touch_target_user_{suffix}"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        if "触摸了你的" in body["messages"][1]["content"]:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "lines": [
                                            {"text": "共享池台词一。", "emotion": "shy", "expression": "shy", "motion": "TapHead"},
                                            {"text": "共享池台词二。", "emotion": "happy", "expression": "happy", "motion": "TapHead"},
                                        ]
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"error": "unexpected"})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=source_user, character_id="atri")
            ensure_seed(session, user_id=target_user, character_id="atri")
            user = session.get(User, source_user)
            character = session.get(Character, "atri")
            assert user is not None and character is not None
            user.tts_enabled = False
            for uid in (source_user, target_user):
                relation = session.execute(
                    select(RelationState).where(RelationState.user_id == uid, RelationState.character_id == "atri")
                ).scalar_one()
                relation.affection = 20
            upsert_provider(
                session,
                ProviderConfigIn(
                    provider_id=f"test_shared_touch_llm_{suffix}",
                    kind="llm",
                    provider="volc_ark",
                    base_url="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-chat",
                    secrets={"api_key": "ark-key"},
                ),
            )
            session.commit()
            refresh_touch_reaction_pools(session, user_id=source_user, character_id="atri", hit_area="chest", tier="low", force=True)
            result = consume_touch_reaction(session, user_id=target_user, character_id="atri", hit_area="chest")
            assert result["text"]
            tracking = session.execute(
                select(TouchReactionPool).where(
                    TouchReactionPool.user_id == target_user,
                    TouchReactionPool.character_id == "atri",
                    TouchReactionPool.hit_area == "chest",
                    TouchReactionPool.tier == "low",
                )
            ).scalar_one_or_none()
            assert tracking is not None
            assert json.loads(tracking.consumed_indices_json)
    finally:
        providers.HTTP_TRANSPORT = None


def test_instant_greeting_payload_uses_full_script() -> None:
    payload = _instant_greeting_payload(datetime(2026, 6, 11, 9, 0, tzinfo=timezone.utc))
    assert payload.reply_mode == "opening"
    assert len(payload.lines) == 3


def test_dialogue_line_supports_expression_field() -> None:
    line = DialogueLine(
        line_id="line_test",
        text="你好呀。",
        emotion="happy",
        pose="idle",
        expression="shy",
    )
    payload = line.model_dump()
    assert payload["expression"] == "shy"
    restored = DialogueLine.model_validate(payload)
    assert restored.expression == "shy"


def _map_screen_to_model_space(screen_x: float, screen_y: float, placement: dict[str, float]) -> tuple[float, float]:
    horizontal_range = 480.0
    vertical_range = 900.0
    bottom_inset_range = 650.0
    character_center_x = 0.5
    character_center_y = 0.42
    scale = max(0.25, min(4.0, float(placement.get("scale", 1.0))))
    offset_x_norm = float(placement.get("offsetX", 0.0)) / horizontal_range * 0.14
    offset_y_norm = float(placement.get("offsetY", 0.0)) / vertical_range * 0.11
    bottom_lift = float(placement.get("bottomInset", 0.0)) / bottom_inset_range * 0.07
    x = screen_x - offset_x_norm
    y = screen_y + bottom_lift - offset_y_norm
    x = character_center_x + (x - character_center_x) / max(scale, 0.25)
    y = character_center_y + (y - character_center_y) / max(scale, 0.25)
    return max(0.0, min(1.0, x)), max(0.0, min(1.0, y))


def _map_model_to_screen_space(model_x: float, model_y: float, placement: dict[str, float]) -> tuple[float, float]:
    horizontal_range = 480.0
    vertical_range = 900.0
    bottom_inset_range = 650.0
    character_center_x = 0.5
    character_center_y = 0.42
    scale = max(0.25, min(4.0, float(placement.get("scale", 1.0))))
    offset_x_norm = float(placement.get("offsetX", 0.0)) / horizontal_range * 0.14
    offset_y_norm = float(placement.get("offsetY", 0.0)) / vertical_range * 0.11
    bottom_lift = float(placement.get("bottomInset", 0.0)) / bottom_inset_range * 0.07
    x = character_center_x + (model_x - character_center_x) * max(scale, 0.25)
    y = character_center_y + (model_y - character_center_y) * max(scale, 0.25)
    x += offset_x_norm
    y = y + offset_y_norm - bottom_lift
    return max(0.0, min(1.0, x)), max(0.0, min(1.0, y))


def test_live2d_bootstrap_includes_default_placement() -> None:
    payload = client.get("/api/bootstrap").json()
    live2d = payload["live2d"]
    assert live2d["appearance_id"] == "neko"
    assert live2d["character_id"] == "neko"
    assert live2d["default_placement"]["scale"] == 1.1
    assert live2d["default_placement"]["bottomInset"] == 30


def test_bootstrap_appearance_switch_changes_config_version() -> None:
    neko = client.get("/api/bootstrap", params={"appearance_id": "neko"}).json()["live2d"]
    atri = client.get("/api/bootstrap", params={"appearance_id": "atri"}).json()["live2d"]
    assert neko["appearance_id"] == "neko"
    assert atri["appearance_id"] == "atri"
    assert neko["config_version"] != atri["config_version"]


def test_live2d_config_endpoint_matches_bootstrap_hit_areas() -> None:
    bootstrap_live2d = client.get("/api/bootstrap", params={"appearance_id": "neko"}).json()["live2d"]
    config = client.get("/api/live2d/config", params={"appearance_id": "neko"}).json()
    assert config["appearance_id"] == "neko"
    assert config["config_version"] == bootstrap_live2d["config_version"]
    assert config["hit_areas"] == bootstrap_live2d["hit_areas"]
    assert config["touch_pool_version"] == bootstrap_live2d["touch_pool_version"]


def test_update_hit_area_changes_live2d_config_version() -> None:
    from app.live2d_config import get_hit_area, update_hit_area

    before = client.get("/api/live2d/config", params={"appearance_id": "neko"}).json()
    with SessionLocal() as session:
        ensure_seed(session, character_id="atri")
        row = get_hit_area(session, appearance_id="neko", area_id="head")
        assert row is not None
        update_hit_area(
            session,
            appearance_id="neko",
            area_id="head",
            payload={"left": round(row.left + 0.03, 2)},
        )
        session.commit()
        updated_left = get_hit_area(session, appearance_id="neko", area_id="head").left
    after = client.get("/api/live2d/config", params={"appearance_id": "neko"}).json()
    head = next(item for item in after["hit_areas"] if item["area_id"] == "head")
    assert head["left"] == updated_left
    assert after["config_version"] != before["config_version"]


def test_live2d_preview_config_modes() -> None:
    neko = client.get("/api/admin/live2d/preview-config", params={"appearance_id": "neko"}).json()
    atri = client.get("/api/admin/live2d/preview-config", params={"appearance_id": "atri"}).json()
    assert neko["renderer_mode"] == "live2d"
    assert neko["model_url"].endswith("neko.model3.json")
    assert atri["renderer_mode"] == "static_png"
    assert atri["default_placement"]["scale"] == 1.14


def _map_model_to_screen_space_raw(model_x: float, model_y: float, placement: dict[str, float]) -> tuple[float, float]:
    horizontal_range = 480.0
    vertical_range = 900.0
    bottom_inset_range = 650.0
    character_center_x = 0.5
    character_center_y = 0.42
    scale = max(0.25, min(4.0, float(placement.get("scale", 1.0))))
    offset_x_norm = float(placement.get("offsetX", 0.0)) / horizontal_range * 0.14
    offset_y_norm = float(placement.get("offsetY", 0.0)) / vertical_range * 0.11
    bottom_lift = float(placement.get("bottomInset", 0.0)) / bottom_inset_range * 0.07
    x = character_center_x + (model_x - character_center_x) * max(scale, 0.25)
    y = character_center_y + (model_y - character_center_y) * max(scale, 0.25)
    x += offset_x_norm
    y = y + offset_y_norm - bottom_lift
    return x, y


def test_live2d_character_screen_rect_follows_placement_scale() -> None:
    def character_height(placement: dict[str, float]) -> float:
        top = _map_model_to_screen_space_raw(0.5, 0.0, placement)
        bottom = _map_model_to_screen_space_raw(0.5, 1.0, placement)
        return abs(bottom[1] - top[1])

    default = {"scale": 1.1, "offsetX": 0.0, "offsetY": -10.0, "bottomInset": 30.0}
    shifted = {"scale": 1.32, "offsetX": 48.0, "offsetY": -28.0, "bottomInset": 38.0}
    assert character_height(shifted) > character_height(default)


def test_live2d_hit_area_accepts_extended_model_coords() -> None:
    from app.live2d_config import MODEL_COORD_MAX, MODEL_COORD_MIN, _normalize_area_payload

    payload = _normalize_area_payload(
        {
            "area_id": "leg",
            "character_id": "neko",
            "label": "腿",
            "left": 0.2,
            "top": 0.7,
            "right": 1.2,
            "bottom": 1.35,
        }
    )
    assert payload["bottom"] == 1.35
    assert payload["right"] == 1.2
    assert MODEL_COORD_MIN == -0.5
    assert MODEL_COORD_MAX == 1.5


def test_live2d_coordinate_round_trip_matches_placement_adjustment() -> None:
    placement = {"scale": 1.0, "offsetX": 0.0, "offsetY": 0.0, "bottomInset": 0.0}
    centered = _map_screen_to_model_space(0.5, 0.4, placement)
    round_trip = _map_model_to_screen_space(centered[0], centered[1], placement)
    assert round_trip[0] == pytest.approx(0.5, abs=0.02)
    assert round_trip[1] == pytest.approx(0.4, abs=0.02)

    shifted_placement = {"scale": 1.4, "offsetX": 40.0, "offsetY": 0.0, "bottomInset": 0.0}
    shifted = _map_screen_to_model_space(0.5, 0.4, shifted_placement)
    assert shifted[0] != pytest.approx(centered[0], abs=0.001) or shifted[1] != pytest.approx(centered[1], abs=0.001)
