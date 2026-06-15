from __future__ import annotations

import contextvars
import json
import time
import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import settings


SENSITIVE_KEYS = {
    "api_key",
    "x_api_key",
    "access_key",
    "app_key",
    "app_id",
    "token",
    "authorization",
    "secret",
    "password",
    "private_key",
    "cookie",
}
MAX_STRING_LENGTH = 12000
MAX_RUNTIME_ITEMS = 500
RUNNING_STALE_SECONDS = 15 * 60

_CURRENT_TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar("diagnostic_trace_id", default="")
_CURRENT_SPAN_ID: contextvars.ContextVar[str] = contextvars.ContextVar("diagnostic_span_id", default="")
_CURRENT_FEATURE: contextvars.ContextVar[str] = contextvars.ContextVar("diagnostic_feature", default="")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in SENSITIVE_KEYS):
                redacted[str(key)] = "***"
            else:
                redacted[str(key)] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        cleaned = value.replace("\r\n", "\n")
        if len(cleaned) > MAX_STRING_LENGTH:
            return cleaned[:MAX_STRING_LENGTH] + f"...[truncated {len(cleaned) - MAX_STRING_LENGTH} chars]"
        return cleaned
    return value


def diagnostic_path() -> Path:
    return settings.log_dir / "diagnostics.jsonl"


def current_trace_id() -> str:
    return _CURRENT_TRACE_ID.get()


def current_span_id() -> str:
    return _CURRENT_SPAN_ID.get()


def new_trace_id(prefix: str = "trace") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def new_span_id(prefix: str = "span") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _with_context(payload: dict[str, Any]) -> dict[str, Any]:
    trace_id = str(payload.get("trace_id") or _CURRENT_TRACE_ID.get() or "")
    span_id = str(payload.get("span_id") or _CURRENT_SPAN_ID.get() or "")
    feature = str(payload.get("feature") or _CURRENT_FEATURE.get() or "")
    enriched = dict(payload)
    if trace_id:
        enriched["trace_id"] = trace_id
    if span_id:
        enriched["span_id"] = span_id
    if feature:
        enriched["feature"] = feature
    enriched.setdefault("phase", "point")
    return enriched


def write_diagnostic(event: str, **payload: Any) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    item = {
        "ts": time.time(),
        "event": event,
        **_redact(_with_context(payload)),
    }
    with diagnostic_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")


class DiagnosticSpan:
    def __init__(self, span_id: str) -> None:
        self.span_id = span_id
        self._end_payload: dict[str, Any] = {}
        self.status = "ok"

    def add(self, **payload: Any) -> None:
        self._end_payload.update(payload)


@contextmanager
def diagnostic_span(
    event: str,
    *,
    feature: str,
    stage: str = "",
    summary: str = "",
    purpose: str = "",
    trace_id: str = "",
    span_id: str = "",
    parent_span_id: str = "",
    **payload: Any,
) -> Iterator[DiagnosticSpan]:
    resolved_trace_id = trace_id or _CURRENT_TRACE_ID.get() or new_trace_id()
    resolved_span_id = span_id or new_span_id()
    resolved_parent_span_id = parent_span_id or _CURRENT_SPAN_ID.get() or ""
    trace_token = _CURRENT_TRACE_ID.set(resolved_trace_id)
    span_token = _CURRENT_SPAN_ID.set(resolved_span_id)
    feature_token = _CURRENT_FEATURE.set(feature or _CURRENT_FEATURE.get())
    started = time.monotonic()
    start_ts = time.time()
    span = DiagnosticSpan(resolved_span_id)
    write_diagnostic(
        event,
        phase="start",
        status="running",
        trace_id=resolved_trace_id,
        span_id=resolved_span_id,
        parent_span_id=resolved_parent_span_id,
        feature=feature,
        stage=stage,
        summary=summary,
        purpose=purpose,
        **payload,
    )
    try:
        yield span
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        write_diagnostic(
            event,
            phase="end",
            status="error",
            trace_id=resolved_trace_id,
            span_id=resolved_span_id,
            parent_span_id=resolved_parent_span_id,
            feature=feature,
            stage=stage,
            summary=summary,
            purpose=purpose,
            elapsed_ms=elapsed_ms,
            started_ts=start_ts,
            error_type=type(exc).__name__,
            message=str(exc),
            **span._end_payload,
        )
        raise
    else:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        write_diagnostic(
            event,
            phase="end",
            status=span.status,
            trace_id=resolved_trace_id,
            span_id=resolved_span_id,
            parent_span_id=resolved_parent_span_id,
            feature=feature,
            stage=stage,
            summary=summary,
            purpose=purpose,
            elapsed_ms=elapsed_ms,
            started_ts=start_ts,
            **span._end_payload,
        )
    finally:
        _CURRENT_SPAN_ID.reset(span_token)
        _CURRENT_TRACE_ID.reset(trace_token)
        _CURRENT_FEATURE.reset(feature_token)


def diagnostic_point(event: str, *, feature: str = "", stage: str = "", summary: str = "", **payload: Any) -> None:
    write_diagnostic(event, feature=feature, stage=stage, summary=summary, **payload)


def tail_diagnostics(limit: int = 200) -> list[dict[str, Any]]:
    return read_diagnostics(limit=limit)


def read_diagnostics(limit: int = 200, *, before_ts: float | None = None) -> list[dict[str, Any]]:
    path = diagnostic_path()
    if not path.exists():
        return []
    limit = max(1, min(int(limit or 200), 50000))
    lines: Iterable[str] = reversed(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    items: list[dict[str, Any]] = []
    for line in lines:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            if before_ts is not None:
                try:
                    ts = float(parsed.get("ts") or 0)
                except (TypeError, ValueError):
                    ts = 0
                if ts >= before_ts:
                    continue
            items.append(parsed)
            if len(items) >= limit:
                break
    items.reverse()
    return items


def _public_payload(item: dict[str, Any]) -> dict[str, Any]:
    skip = {"ts", "event", "phase", "status", "trace_id", "span_id", "parent_span_id", "feature", "stage", "summary", "purpose"}
    return {key: value for key, value in item.items() if key not in skip}


def _status_for_point(item: dict[str, Any]) -> str:
    explicit = str(item.get("status") or "")
    if explicit:
        return explicit
    event = str(item.get("event") or "").lower()
    if any(part in event for part in ("error", "failed", "fail")):
        return "error"
    if any(part in event for part in ("rejected", "blocked")):
        return "warn"
    return "ok"


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is None or value == "":
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        return value
    return None


def _short_summary(value: Any, *, max_length: int = 180) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        text = " ".join(value.replace("\r\n", "\n").split())
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except TypeError:
            text = str(value)
    if len(text) > max_length:
        return text[:max_length] + "..."
    return text


def _input_summary(item: dict[str, Any]) -> str:
    details = item.get("details") or {}
    start = details.get("start") or {}
    point = details.get("point") or {}
    value = _first_present(start.get("input"), point.get("input"), start.get("request"), point.get("request"))
    return _short_summary(value)


def _output_summary(item: dict[str, Any]) -> str:
    details = item.get("details") or {}
    end = details.get("end") or {}
    point = details.get("point") or {}
    value = _first_present(
        end.get("output"),
        point.get("output"),
        end.get("response"),
        point.get("response"),
        end.get("response_text"),
        end.get("raw_content"),
        point.get("message"),
    )
    return _short_summary(value)


def _references_payload(item: dict[str, Any]) -> Any:
    details = item.get("details") or {}
    start = details.get("start") or {}
    end = details.get("end") or {}
    point = details.get("point") or {}
    return _first_present(start.get("references"), end.get("references"), point.get("references"), (start.get("input") or {}).get("references") if isinstance(start.get("input"), dict) else None)


def _references_summary(item: dict[str, Any]) -> str:
    references = _references_payload(item)
    if not isinstance(references, dict):
        return ""
    pieces: list[str] = []
    labels = {
        "user_input": "用户输入",
        "schedule": "日程",
        "character_schedule": "角色日程",
        "user_schedule": "用户日程",
        "weather": "天气",
        "persona": "人设",
        "user_profile": "用户画像",
        "relation_attitude": "关系态度",
        "memory": "记忆",
        "event_memory": "事件记忆",
        "recent_dialogue": "上下文",
        "moment_interactions": "朋友圈",
    }
    for key, label in labels.items():
        value = references.get(key)
        if not isinstance(value, dict):
            continue
        used = bool(value.get("used") or value.get("included") or value.get("available") or value.get("count"))
        if not used:
            continue
        count = value.get("count")
        suffix = f"({count})" if count not in (None, "", 0) else ""
        pieces.append(f"{label}{suffix}")
    if references.get("gate"):
        pieces.append("gate")
    if references.get("subject_hint"):
        pieces.append("subject")
    return " / ".join(pieces)


def _raw_is_cache_hit(raw: dict[str, Any]) -> bool:
    event = str(raw.get("event") or "").lower()
    if "cache_hit" in event or event.endswith("_cached"):
        return True
    return bool(raw.get("cache_hit") is True or raw.get("is_cache_hit") is True)


def _item_is_cache_hit(item: dict[str, Any]) -> bool:
    details = item.get("details") or {}
    raw_events = details.get("raw_events") or []
    return any(isinstance(raw, dict) and _raw_is_cache_hit(raw) for raw in raw_events)


def _text_blob(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _matches_filters(item: dict[str, Any], *, feature: str, status: str, q: str, trace_id: str) -> bool:
    if feature:
        item_feature = str(item.get("feature") or "")
        if feature == "旧诊断日志":
            if not item.get("is_legacy"):
                return False
        elif item_feature != feature:
            return False
    if status and str(item.get("status") or "") != status:
        return False
    if trace_id and str(item.get("trace_id") or "") != trace_id:
        return False
    if q and q.lower() not in _text_blob(item).lower():
        return False
    return True


def _build_flows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        trace = str(item.get("trace_id") or "")
        if not trace and item.get("is_legacy"):
            trace = "legacy"
        if not trace:
            continue
        grouped.setdefault(trace, []).append(item)
    flows: list[dict[str, Any]] = []
    for trace, rows in grouped.items():
        ordered = sorted(rows, key=lambda item: float(item.get("started_ts") or 0))
        nodes = [
            {
                "id": item.get("id"),
                "trace_id": trace,
                "feature": item.get("feature") or "",
                "stage": item.get("stage") or "",
                "event": item.get("event") or "",
                "status": item.get("status") or "",
                "summary": item.get("summary") or item.get("purpose") or item.get("event") or "",
                "elapsed_ms": item.get("elapsed_ms") or 0,
                "parent_id": item.get("parent_id") or "",
                "children_ids": item.get("children_ids") or [],
                "input_summary": item.get("input_summary") or "",
                "output_summary": item.get("output_summary") or "",
                "references_summary": item.get("references_summary") or "",
                "is_cache_hit": bool(item.get("is_cache_hit")),
                "is_legacy": bool(item.get("is_legacy")),
            }
            for item in ordered
        ]
        parent_edges = [
            {"from": item.get("parent_id"), "to": item.get("id"), "label": item.get("stage") or item.get("event") or ""}
            for item in ordered
            if item.get("parent_id")
        ]
        sequence_edges = [
            {
                "from": ordered[index].get("id"),
                "to": ordered[index + 1].get("id"),
                "label": ordered[index].get("output_summary") or ordered[index].get("summary") or "",
            }
            for index in range(len(ordered) - 1)
        ]
        flows.append(
            {
                "trace_id": trace,
                "summary": _short_summary(_first_present(ordered[0].get("summary"), ordered[0].get("purpose"), ordered[0].get("event"))),
                "started_ts": ordered[0].get("started_ts") if ordered else 0,
                "ended_ts": ordered[-1].get("ended_ts") if ordered else None,
                "nodes": nodes,
                "edges": parent_edges or sequence_edges,
                "sequence_edges": sequence_edges,
            }
        )
    flows.sort(key=lambda flow: float(flow.get("started_ts") or 0), reverse=True)
    return flows


def _build_traces(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        trace = str(item.get("trace_id") or "")
        if not trace and item.get("is_legacy"):
            trace = "legacy"
        if not trace:
            continue
        grouped.setdefault(trace, []).append(item)
    traces: list[dict[str, Any]] = []
    for trace, rows in grouped.items():
        ordered = sorted(rows, key=lambda item: float(item.get("started_ts") or 0))
        started_ts = min([float(item.get("started_ts") or 0) for item in ordered], default=0)
        ended_values = [float(item.get("ended_ts") or 0) for item in ordered if item.get("ended_ts")]
        ended_ts = max(ended_values) if ended_values else None
        elapsed_ms = int(max([int(item.get("elapsed_ms") or 0) for item in ordered], default=0))
        status = "ok"
        if any(item.get("status") == "error" for item in ordered):
            status = "error"
        elif any(item.get("status") in {"running", "stale"} or item.get("running") for item in ordered):
            status = "running"
        elif any(item.get("status") == "warn" for item in ordered):
            status = "warn"
        nodes = [
            {
                "id": item.get("id"),
                "trace_id": trace,
                "feature": item.get("feature") or "",
                "stage": item.get("stage") or "",
                "event": item.get("event") or "",
                "status": item.get("status") or "",
                "summary": item.get("summary") or item.get("purpose") or item.get("event") or "",
                "elapsed_ms": item.get("elapsed_ms") or 0,
                "parent_id": item.get("parent_id") or "",
                "children_ids": item.get("children_ids") or [],
                "input_summary": item.get("input_summary") or "",
                "output_summary": item.get("output_summary") or "",
                "references_summary": item.get("references_summary") or "",
                "is_cache_hit": bool(item.get("is_cache_hit")),
                "is_legacy": bool(item.get("is_legacy")),
            }
            for item in ordered
        ]
        parent_edges = [
            {"from": item.get("parent_id"), "to": item.get("id"), "label": item.get("stage") or item.get("event") or ""}
            for item in ordered
            if item.get("parent_id")
        ]
        sequence_edges = [
            {
                "from": ordered[index].get("id"),
                "to": ordered[index + 1].get("id"),
                "label": ordered[index].get("output_summary") or ordered[index].get("summary") or "",
            }
            for index in range(len(ordered) - 1)
        ]
        traces.append(
            {
                "trace_id": trace,
                "summary": _short_summary(_first_present(ordered[0].get("summary"), ordered[0].get("purpose"), ordered[0].get("event"))),
                "feature": ordered[0].get("feature") or "",
                "is_legacy": all(bool(item.get("is_legacy")) for item in ordered),
                "status": status,
                "started_ts": started_ts,
                "ended_ts": ended_ts,
                "elapsed_ms": elapsed_ms,
                "running": any(bool(item.get("running")) for item in ordered),
                "stale": any(bool(item.get("stale")) for item in ordered),
                "nodes": nodes,
                "edges": parent_edges or sequence_edges,
                "sequence_edges": sequence_edges,
                "items": ordered,
            }
        )
    traces.sort(key=lambda trace: float(trace.get("started_ts") or 0), reverse=True)
    return traces


def runtime_logs(
    *,
    limit: int = 200,
    feature: str = "",
    status: str = "",
    q: str = "",
    trace_id: str = "",
    before_ts: float | None = None,
    include_legacy: bool = True,
    include_cache_hits: bool = False,
) -> dict[str, Any]:
    now_ts = time.time()
    limit = max(1, min(int(limit or 200), MAX_RUNTIME_ITEMS))
    scan_limit = max(limit * 20, 1000)
    raw_items = read_diagnostics(scan_limit + 1, before_ts=before_ts)
    has_more = len(raw_items) > scan_limit
    if has_more:
        raw_items = raw_items[-scan_limit:]
    spans: dict[tuple[str, str], dict[str, Any]] = {}
    points: list[dict[str, Any]] = []

    for raw in raw_items:
        phase = str(raw.get("phase") or "point")
        trace = str(raw.get("trace_id") or "")
        span = str(raw.get("span_id") or "")
        if span and phase in {"start", "end"}:
            key = (trace, span)
            is_legacy = not trace
            record = spans.setdefault(
                key,
                {
                    "id": f"{trace}:{span}" if trace else f"legacy:{span}",
                    "trace_id": trace,
                    "span_id": span,
                    "parent_span_id": str(raw.get("parent_span_id") or ""),
                    "event": raw.get("event", ""),
                    "feature": raw.get("feature", "") or ("旧诊断日志" if is_legacy else ""),
                    "stage": raw.get("stage", ""),
                    "purpose": raw.get("purpose", ""),
                    "summary": raw.get("summary", ""),
                    "status": "running",
                    "started_ts": float(raw.get("ts") or now_ts),
                    "ended_ts": None,
                    "elapsed_ms": 0,
                    "running": True,
                    "stale": False,
                    "is_legacy": is_legacy,
                    "details": {"start": {}, "end": {}, "points": [], "raw_events": []},
                },
            )
            record["details"]["raw_events"].append(raw)
            if phase == "start":
                record.update(
                    {
                        "event": raw.get("event", record["event"]),
                        "feature": raw.get("feature", record["feature"]) or ("旧诊断日志" if is_legacy else ""),
                        "stage": raw.get("stage", record["stage"]),
                        "purpose": raw.get("purpose", record["purpose"]),
                        "summary": raw.get("summary", record["summary"]),
                        "started_ts": float(raw.get("ts") or record["started_ts"]),
                        "parent_span_id": str(raw.get("parent_span_id") or record.get("parent_span_id") or ""),
                        "is_legacy": is_legacy,
                    }
                )
                record["details"]["start"] = _public_payload(raw)
            else:
                record["status"] = str(raw.get("status") or record["status"] or "ok")
                record["ended_ts"] = float(raw.get("ts") or now_ts)
                record["elapsed_ms"] = int(raw.get("elapsed_ms") or 0)
                record["running"] = False
                record["details"]["end"] = _public_payload(raw)
                record["is_legacy"] = is_legacy
                for field in ("feature", "stage", "purpose", "summary"):
                    if raw.get(field):
                        record[field] = raw[field]
                if raw.get("parent_span_id"):
                    record["parent_span_id"] = str(raw.get("parent_span_id") or "")
        else:
            is_legacy = not trace
            raw_ts = float(raw.get("ts") or now_ts)
            point = {
                "id": f"{trace or 'legacy'}:{span or 'point'}:{raw.get('event')}:{raw_ts}",
                "trace_id": trace,
                "span_id": span,
                "parent_span_id": str(raw.get("parent_span_id") or ""),
                "event": raw.get("event", ""),
                "feature": raw.get("feature", "") or ("旧诊断日志" if is_legacy else ""),
                "stage": raw.get("stage", ""),
                "purpose": raw.get("purpose", ""),
                "summary": raw.get("summary", "") or raw.get("message", "") or raw.get("reason", ""),
                "status": _status_for_point(raw),
                "started_ts": raw_ts,
                "ended_ts": raw_ts,
                "elapsed_ms": int(raw.get("elapsed_ms") or 0),
                "running": False,
                "stale": False,
                "is_legacy": is_legacy,
                "details": {"point": _public_payload(raw), "raw_events": [raw]},
            }
            if span:
                linked = spans.get((trace, span))
                if linked is not None:
                    linked["details"]["points"].append(raw)
                    if str(raw.get("feature") or "") == str(linked.get("feature") or ""):
                        continue
            points.append(point)

    items = list(spans.values()) + points
    for item in items:
        if item.get("running"):
            elapsed_ms = int((now_ts - float(item.get("started_ts") or now_ts)) * 1000)
            item["elapsed_ms"] = max(0, elapsed_ms)
            item["stale"] = elapsed_ms > RUNNING_STALE_SECONDS * 1000
            if item["stale"]:
                item["status"] = "stale"
        if not item.get("summary"):
            item["summary"] = item.get("purpose") or item.get("event") or item.get("stage") or ""
        item["is_cache_hit"] = _item_is_cache_hit(item)
        item["input_summary"] = _input_summary(item)
        item["output_summary"] = _output_summary(item)
        item["references_summary"] = _references_summary(item)
        item.setdefault("prev_id", "")
        item.setdefault("next_id", "")
        item.setdefault("parent_id", "")
        item.setdefault("children_ids", [])

    by_trace: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        trace = str(item.get("trace_id") or "")
        if trace:
            by_trace.setdefault(trace, []).append(item)
    for rows in by_trace.values():
        ordered = sorted(rows, key=lambda item: float(item.get("started_ts") or 0))
        span_to_id = {
            str(item.get("span_id") or ""): str(item.get("id") or "")
            for item in ordered
            if item.get("span_id") and ("start" in (item.get("details") or {}) or "end" in (item.get("details") or {}))
        }
        for index, item in enumerate(ordered):
            item["prev_id"] = ordered[index - 1]["id"] if index > 0 else ""
            item["next_id"] = ordered[index + 1]["id"] if index + 1 < len(ordered) else ""
            parent_span = str(item.get("parent_span_id") or "")
            item["parent_id"] = span_to_id.get(parent_span, "") if parent_span else ""
            item["children_ids"] = []
        id_to_item = {str(item.get("id") or ""): item for item in ordered}
        for item in ordered:
            parent_id = str(item.get("parent_id") or "")
            if parent_id and parent_id in id_to_item:
                id_to_item[parent_id].setdefault("children_ids", []).append(item.get("id"))

    legacy_count = len([item for item in items if item.get("is_legacy")])
    visible_items = [
        item
        for item in items
        if (include_legacy or not item.get("is_legacy")) and (include_cache_hits or not item.get("is_cache_hit"))
    ]
    filtered = [
        item
        for item in visible_items
        if _matches_filters(item, feature=feature.strip(), status=status.strip(), q=q.strip(), trace_id=trace_id.strip())
    ]
    filtered.sort(key=lambda item: float(item.get("started_ts") or 0), reverse=True)
    returned = filtered[:limit]
    feature_set = {str(item.get("feature") or "") for item in items if item.get("feature")}
    if legacy_count:
        feature_set.add("旧诊断日志")
    features = sorted(feature_set)
    oldest_ts = min([float(item.get("started_ts") or 0) for item in returned], default=None)
    return {
        "ok": True,
        "server_time": now_ts,
        "items": returned,
        "total": len(filtered),
        "features": features,
        "flows": _build_flows(returned),
        "traces": _build_traces(returned),
        "legacy_count": legacy_count,
        "has_more": bool(has_more or len(filtered) > limit),
        "oldest_ts": oldest_ts,
    }
