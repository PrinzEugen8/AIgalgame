from __future__ import annotations

import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .config import settings


SENSITIVE_KEYS = {"api_key", "x_api_key", "access_key", "app_key", "app_id", "token", "authorization", "secret"}


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
    if isinstance(value, str) and len(value) > 240:
        return value[:240] + "..."
    return value


def diagnostic_path() -> Path:
    return settings.log_dir / "diagnostics.jsonl"


def write_diagnostic(event: str, **payload: Any) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    item = {
        "ts": time.time(),
        "event": event,
        **_redact(payload),
    }
    with diagnostic_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")


def tail_diagnostics(limit: int = 200) -> list[dict[str, Any]]:
    path = diagnostic_path()
    if not path.exists():
        return []
    limit = max(1, min(int(limit or 200), 1000))
    lines: Iterable[str] = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
    items: list[dict[str, Any]] = []
    for line in lines:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            items.append(parsed)
    return items
