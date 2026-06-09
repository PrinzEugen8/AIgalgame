from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def load_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def stable_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, int(value)))


def split_cn_lines(text: str, limit: int = 48) -> list[str]:
    chunks = [item.strip() for item in re.split(r"(?<=[。！？!?…])", text) if item.strip()]
    if not chunks:
        chunks = [text.strip()]
    lines: list[str] = []
    for chunk in chunks:
        while len(chunk) > limit:
            lines.append(chunk[:limit])
            chunk = chunk[limit:]
        if chunk:
            lines.append(chunk)
    return lines[:4]

