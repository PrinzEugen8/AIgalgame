from __future__ import annotations

import base64
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
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
from app.main import app  # noqa: E402
from app.models import CalendarEvent, Character, Experience, Memory, Message, Moment, MomentInteraction, OpeningCache, ProactiveEvent, ProviderConfig, RelationState, ScheduleSlot, TrendRadarSnapshot, TtsVoiceProfile, User, UserLocation, WeatherSnapshot  # noqa: E402
from app.news import dispatch_trend_radar_workflow, sync_trend_radar_snapshot, trend_radar_payload_for_news  # noqa: E402
from app.online import clear_online_state, is_online, mark_offline, mark_online  # noqa: E402
from app.opening import consume_ready_opening, prepare_due_openings, prepare_opening  # noqa: E402
from app.pipeline import _tts_for_line, handle_event  # noqa: E402
from app.proactive import consume_proactive_event, create_proactive_event, ensure_news_candidate, pending_proactive_response  # noqa: E402
from app.providers import ImageProvider, ProviderError, VolcArkWebSearchClient, VolcSeedTtsClient, get_enabled_provider, get_task_llm_provider, provider_presets, upsert_provider  # noqa: E402
from app.schedule import ensure_schedule, mark_interruption, run_daily_cycle  # noqa: E402
from app.schemas import EventIn, ProviderConfigIn  # noqa: E402
from app.seed import ensure_seed  # noqa: E402
from app.utils import dump_json  # noqa: E402


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
        ensure_seed(session, user_id=online_user_id, character_id="sakura")
        ensure_seed(session, user_id=cache_user_id, character_id="sakura")
        for user_id in (online_user_id, cache_user_id):
            user = session.get(User, user_id)
            user.story_completed = True
            user.notifications_enabled = True
        session.add(
            OpeningCache(
                cache_id=f"opening_cache_{suffix}",
                user_id=cache_user_id,
                character_id="sakura",
                kind="greeting",
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
        ensure_seed(session, user_id=user_id, character_id="sakura")
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
            ensure_seed(session, user_id="trace_reply_user", character_id="sakura")
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
                    character_id="sakura",
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
    for key in ("user_input", "schedule", "weather", "memory", "recent_dialogue", "moment_interactions", "gate", "subject_hint"):
        assert key in serialized
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
            ensure_seed(session, user_id=user_id, character_id="sakura")
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
                character_id="sakura",
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
            ensure_seed(session, user_id="free_delta_user", character_id="sakura")
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
                select(RelationState).where(RelationState.user_id == "free_delta_user", RelationState.character_id == "sakura")
            ).scalar_one()
            user.story_completed = True
            user.tts_enabled = False
            before = (relation.affection, relation.trust, relation.dependency, relation.mood)
            session.commit()

            result = handle_event(
                session,
                EventIn(
                    event_type="user_message",
                    user_id="free_delta_user",
                    character_id="sakura",
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
            ensure_seed(session, user_id="option_delta_user", character_id="sakura")
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
                select(RelationState).where(RelationState.user_id == "option_delta_user", RelationState.character_id == "sakura")
            ).scalar_one()
            user.story_completed = True
            user.tts_enabled = False
            before = (relation.affection, relation.trust, relation.dependency, relation.mood)
            session.commit()

            result = handle_event(
                session,
                EventIn(
                    event_type="option_selected",
                    user_id="option_delta_user",
                    character_id="sakura",
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
            ensure_seed(session, user_id="schedule_prompt_user", character_id="sakura")
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
                    character_id="sakura",
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
            ensure_seed(session, user_id=user_id, character_id="sakura")
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
                    character_id="sakura",
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
            ensure_seed(session, user_id=user_id, character_id="sakura")
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
                    character_id="sakura",
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
                    character_id="sakura",
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
            ensure_seed(session, user_id=user_id, character_id="sakura")
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
                    character_id="sakura",
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
            ensure_seed(session, user_id=user_id, character_id="sakura")
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
            f"/api/location?user_id={user_id}&character_id=sakura",
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
            ensure_seed(session, user_id=user_id, character_id="sakura")
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
            f"/api/weather/refresh?user_id={user_id}&character_id=sakura",
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
            f"/api/weather/refresh?user_id={user_id}&character_id=sakura",
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
        calls.append(request.url.path)
        raise AssertionError(f"pending proactive must not call QWeather: {request.url}")

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="sakura")
            user = session.get(User, user_id)
            user.story_completed = True
            user.news_enabled = False
            user.proactive_next_check_at = "2026-06-10T12:00:00+00:00"
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
                character_id="sakura",
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
        ensure_seed(session, user_id=user_id, character_id="sakura")
        user = session.get(User, user_id)
        user.story_completed = True
        session.commit()
        run_daily_cycle(session, user_id=user_id, character_id="sakura", day=datetime.fromisoformat("2099-06-10T04:00:00+08:00"))
    assert calls == {"refresh": 1, "candidate": 1}


def test_proactive_judge_selects_event_without_hard_daily_gap_or_sleep_blocks() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"proactive_limit_user_{suffix}"
    selected_event_id = ""
    judge_contexts: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        prompt = body["messages"][1]["content"]
        context = json.loads(prompt.split("上下文 JSON：\n", 1)[1])
        judge_contexts.append(context)
        return _llm_json_response(
            {
                "should_send": True,
                "selected_event_id": selected_event_id,
                "reason": "虽然处在睡眠和短间隔上下文里，但这条候选更重要。",
                "next_check_after_minutes": 120,
            }
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="sakura")
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
            user.sleep_start = "00:30"
            user.sleep_end = "08:00"
            for index in range(3):
                delivered = create_proactive_event(
                    session,
                    user_id=user_id,
                    character_id="sakura",
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
                character_id="sakura",
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
                character_id="sakura",
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
                character_id="sakura",
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
            assert judgement["next_check_after_minutes"] == 30
            assert user.proactive_next_check_at
    finally:
        providers.HTTP_TRANSPORT = None


def test_proactive_judge_can_defer_and_store_next_check() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"proactive_defer_user_{suffix}"

    def handler(request: httpx.Request) -> httpx.Response:
        return _llm_json_response(
            {
                "should_send": False,
                "selected_event_id": "",
                "reason": "现在不打扰，稍后再看。",
                "next_check_after_minutes": 45,
            }
        )

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="sakura")
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
                character_id="sakura",
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
                character_id="sakura",
                local_time=datetime.fromisoformat("2026-06-09T12:00:00+08:00"),
                generate_news=False,
                generate_weather=False,
            )
            assert result["event"] is None
            session.refresh(user)
            judgement = json.loads(user.proactive_judgement_json)
            assert judgement["should_send"] is False
            assert judgement["next_check_after_minutes"] == 45
            assert datetime.fromisoformat(user.proactive_next_check_at) == datetime.fromisoformat("2026-06-09T04:45:00+00:00")
    finally:
        providers.HTTP_TRANSPORT = None


def test_proactive_next_check_skips_llm_until_due() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"proactive_next_check_user_{suffix}"
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _llm_json_response({"should_send": True, "selected_event_id": "should_not_be_used", "reason": "", "next_check_after_minutes": 10})

    providers.HTTP_TRANSPORT = httpx.MockTransport(handler)
    try:
        with SessionLocal() as session:
            ensure_seed(session, user_id=user_id, character_id="sakura")
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
            create_proactive_event(
                session,
                user_id=user_id,
                character_id="sakura",
                source_type="memory",
                source_id=f"pending_skip_{suffix}",
                title="未到判断时间",
                text="这条不应该触发判断器。",
                priority=90,
                dedupe_key=f"pending_skip_{suffix}",
                scheduled_at=datetime.fromisoformat("2026-06-09T00:00:00+00:00"),
            )
            session.commit()
            result = pending_proactive_response(
                session,
                user_id=user_id,
                character_id="sakura",
                local_time=datetime.fromisoformat("2026-06-09T12:00:00+08:00"),
                generate_news=False,
                generate_weather=False,
            )
            assert result["event"] is None
            assert calls == 0
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
            ensure_seed(session, user_id=user_id, character_id="sakura")
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
                character_id="sakura",
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
                character_id="sakura",
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
            ensure_seed(session, user_id=user_id, character_id="sakura")
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
                character_id="sakura",
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
                character_id="sakura",
                local_time=datetime.fromisoformat("2026-06-09T12:00:00+08:00"),
                generate_news=False,
                generate_weather=False,
            )
            assert result["event"] is None
            session.refresh(user)
            judgement = json.loads(user.proactive_judgement_json)
            assert judgement["status"] == "error"
            assert judgement["should_send"] is False
            assert judgement["next_check_after_minutes"] == 30
    finally:
        providers.HTTP_TRANSPORT = None


def test_proactive_judge_missing_provider_defaults_to_no_send() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"proactive_no_provider_user_{suffix}"
    with SessionLocal() as session:
        for config in session.execute(select(ProviderConfig).where(ProviderConfig.kind.in_(["llm", "llm_task"]))).scalars():
            config.enabled = False
        ensure_seed(session, user_id=user_id, character_id="sakura")
        user = session.get(User, user_id)
        user.story_completed = True
        user.notifications_enabled = True
        create_proactive_event(
            session,
            user_id=user_id,
            character_id="sakura",
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
            character_id="sakura",
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
            ensure_seed(session, user_id=user_id, character_id="sakura")
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
            assert payload["widget"]["chibi_url"] == "/media/asset_chibi_sakura_widget"
            delivered = client.post(f"/api/proactive/{event_id}/delivered").json()
            assert delivered["event"]["status"] == "delivered"
            retained = client.get(
                f"/api/proactive/pending?user_id={user_id}&character_id=sakura&local_time=2026-06-09T12:10:00%2B08:00"
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
            ensure_seed(session, user_id=user_id, character_id="sakura")
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
                character_id="sakura",
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
                character_id="sakura",
                local_time=datetime.fromisoformat("2026-06-09T12:00:00+08:00"),
                generate_news=False,
                generate_weather=False,
            )
            assert before["event"]["proactive_event_id"] == event.proactive_event_id
            consume_proactive_event(session, event.proactive_event_id)
            after = pending_proactive_response(
                session,
                user_id=user_id,
                character_id="sakura",
                local_time=datetime.fromisoformat("2026-06-09T12:01:00+08:00"),
                generate_news=False,
                generate_weather=False,
            )
            assert after["event"] is None
            assert after["widget"]["unread_count"] == 0
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
            ensure_seed(session, user_id=user_id, character_id="sakura")
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
            character = session.get(Character, "sakura")
            user.story_completed = True
            user.tts_enabled = True
            character.tts_voice_profile_id = voice_id
            session.commit()

            prepared = prepare_opening(
                session,
                user_id=user_id,
                character_id="sakura",
                local_time=datetime.fromisoformat("2026-06-09T20:00:00+08:00"),
                allow_llm=False,
            )
            assert prepared["kind"] == "greeting"
            cache = session.get(OpeningCache, prepared["cache_id"])
            assert cache is not None and cache.status == "ready"
            ready = consume_ready_opening(
                session,
                user_id=user_id,
                character_id="sakura",
                session_id=f"opening_session_{suffix}",
                local_time=datetime.fromisoformat("2026-06-09T20:01:00+08:00"),
            )
            assert ready.event_type == "dialogue"
            assert ready.payload["cached"] is True
            assert ready.payload["opening_kind"] == "greeting"
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
        if "上下文 JSON：" in body["messages"][1]["content"]:
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
            ensure_seed(session, user_id=user_id, character_id="sakura")
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
                character_id="sakura",
                source_type="memory",
                source_id=f"prewarm_memory_{suffix}",
                title="小樱有话想说",
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
                character_id="sakura",
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
                character_id="sakura",
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
    moment_id = "seed_moment_sakura_walk"
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
            ensure_seed(session, user_id=user_id, character_id="sakura")
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
                character_id="sakura",
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
            ensure_seed(session, user_id=user_id, character_id="sakura")
            user = session.get(User, user_id)
            user.interest_topics_json = json.dumps(["AI 游戏", "芯片"], ensure_ascii=False)
            user.news_enabled = True
            create_proactive_event(
                session,
                user_id=user_id,
                character_id="sakura",
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
                character_id="sakura",
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
            ensure_seed(session, user_id=user_id, character_id="sakura")
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
                character_id="sakura",
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


def test_ensure_schedule_refreshes_elapsed_pending_slots() -> None:
    user_id = "schedule_refresh_user"
    with SessionLocal() as session:
        ensure_seed(session, user_id=user_id, character_id="sakura")
        session.merge(
            ScheduleSlot(
                slot_id="slot_test_refresh_elapsed",
                schedule_date="2026-06-09",
                user_id=user_id,
                character_id="sakura",
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
                character_id="sakura",
                start_at="2026-06-09T08:15:00+08:00",
                end_at="2026-06-09T08:30:00+08:00",
                activity_title="早饭",
                activity_type="daily",
                actual_status="interrupted",
            )
        )
        session.commit()

        ensure_schedule(session, user_id=user_id, character_id="sakura", day=datetime.fromisoformat("2026-06-09T12:00:00+08:00"))
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


def test_calendar_returns_only_important_events() -> None:
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    user_id = f"calendar_event_user_{suffix}"
    with SessionLocal() as session:
        ensure_seed(session, user_id=user_id, character_id="sakura")
        session.add(
            CalendarEvent(
                event_id=f"cal_test_date_event_{suffix}",
                user_id=user_id,
                character_id="sakura",
                event_date="2026-06-12",
                title="第一次约会",
                category="relationship",
                description="你们约好一起去看展。",
                salience=95,
                source_type="admin",
            )
        )
        session.commit()

    june = client.get(f"/api/calendar?month=2026-06&user_id={user_id}&character_id=sakura").json()
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
    anniversary = client.get(f"/api/calendar?month={anniversary_month}&user_id={user_id}&character_id=sakura").json()
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
        json={"display_name": "测试用户改", "tts_enabled": False, "news_enabled": False, "proactive_daily_limit": "high"},
    ).json()
    assert updated["display_name"] == "测试用户改"
    assert updated["tts_enabled"] is False
    assert updated["proactive_daily_limit"] == "high"

    relation = client.put(
        f"/api/admin/users/{user_id}/relation",
        json={"affection": 321, "trust": 222, "dependency": 111, "mood": -12, "relationship_stage": "测试阶段"},
    ).json()
    assert relation["affection"] == 321
    assert relation["relationship_stage"] == "测试阶段"

    memory = client.post(
        f"/api/admin/users/{user_id}/memories",
        json={"content": "用户手动添加的测试记忆", "layer": "core", "importance": 0.8, "confidence": 0.9},
    ).json()
    assert memory["content"] == "用户手动添加的测试记忆"
    memories_page = client.get(f"/api/admin/users/{user_id}/memories?q=测试记忆&page=1&page_size=5").json()
    assert memories_page["total"] == 1
    assert memories_page["items"][0]["memory_id"] == memory["memory_id"]
    hidden = client.put(f"/api/admin/memories/{memory['memory_id']}", json={"hidden": True}).json()
    assert hidden["hidden"] is True
    assert client.delete(f"/api/admin/memories/{memory['memory_id']}").json()["ok"] is True

    event = client.post(
        "/api/admin/calendar-events",
        json={
            "user_id": user_id,
            "character_id": "sakura",
            "date": "2026-06-18",
            "title": "测试约会日",
            "category": "relationship",
            "salience": 88,
        },
    ).json()
    assert event["title"] == "测试约会日"
    calendar_page = client.get(f"/api/admin/calendar-events?user_id={user_id}&character_id=sakura&q=测试约会日&page=1&page_size=5").json()
    assert calendar_page["total"] == 1
    assert calendar_page["items"][0]["event_id"] == event["event_id"]
    changed = client.put(f"/api/admin/calendar-events/{event['event_id']}", json={"hidden": True, "title": "测试约会日改"}).json()
    assert changed["hidden"] is True
    assert changed["title"] == "测试约会日改"
    assert client.delete(f"/api/admin/calendar-events/{event['event_id']}").json()["ok"] is True

    assert client.delete(f"/api/admin/users/{user_id}").json()["ok"] is True
