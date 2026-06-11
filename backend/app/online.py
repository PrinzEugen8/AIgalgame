from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any


_lock = threading.Lock()
_connections: dict[str, set[str]] = defaultdict(set)
_last_seen: dict[str, float] = {}
_presence: dict[str, dict[str, Any]] = {}
FOREGROUND_TTL_SECONDS = 90.0


def mark_online(user_id: str, device_id: str = "device") -> None:
    resolved_user = str(user_id or "").strip()
    if not resolved_user:
        return
    resolved_device = str(device_id or "device").strip() or "device"
    with _lock:
        _connections[resolved_user].add(resolved_device)
        _last_seen[resolved_user] = time.time()
        _presence[resolved_user] = {**_presence.get(resolved_user, {}), "app_state": "foreground", "device_id": resolved_device, "last_seen": _last_seen[resolved_user]}


def mark_offline(user_id: str, device_id: str = "device") -> None:
    resolved_user = str(user_id or "").strip()
    if not resolved_user:
        return
    resolved_device = str(device_id or "device").strip() or "device"
    with _lock:
        devices = _connections.get(resolved_user)
        if devices is not None:
            devices.discard(resolved_device)
            if not devices:
                _connections.pop(resolved_user, None)
        _last_seen[resolved_user] = time.time()
        _presence[resolved_user] = {**_presence.get(resolved_user, {}), "app_state": "background", "device_id": resolved_device, "last_seen": _last_seen[resolved_user]}


def mark_heartbeat(user_id: str, device_id: str = "device", context: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved_user = str(user_id or "").strip()
    if not resolved_user:
        return {}
    resolved_device = str(device_id or "device").strip() or "device"
    now = time.time()
    payload = dict(context or {})
    payload["device_id"] = resolved_device
    payload["last_seen"] = now
    with _lock:
        _last_seen[resolved_user] = now
        if str(payload.get("app_state") or "foreground") == "foreground":
            _connections[resolved_user].add(resolved_device)
        _presence[resolved_user] = payload
        return dict(payload)


def is_online(user_id: str) -> bool:
    resolved_user = str(user_id or "").strip()
    if not resolved_user:
        return False
    with _lock:
        if _connections.get(resolved_user):
            return True
        context = _presence.get(resolved_user) or {}
        return str(context.get("app_state") or "") == "foreground" and time.time() - float(context.get("last_seen") or 0.0) <= FOREGROUND_TTL_SECONDS


def online_users() -> set[str]:
    with _lock:
        now = time.time()
        users = {user_id for user_id, devices in _connections.items() if devices}
        users.update(
            user_id
            for user_id, context in _presence.items()
            if str(context.get("app_state") or "") == "foreground" and now - float(context.get("last_seen") or 0.0) <= FOREGROUND_TTL_SECONDS
        )
        return users


def presence_context(user_id: str) -> dict[str, Any]:
    resolved_user = str(user_id or "").strip()
    if not resolved_user:
        return {}
    with _lock:
        context = dict(_presence.get(resolved_user) or {})
        last_seen = float(context.get("last_seen") or _last_seen.get(resolved_user) or 0.0)
        if last_seen:
            context["seconds_since_last_seen"] = max(0, int(time.time() - last_seen))
        context["online"] = bool(_connections.get(resolved_user)) or (
            str(context.get("app_state") or "") == "foreground" and time.time() - last_seen <= FOREGROUND_TTL_SECONDS
        )
        return context


def snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "online_users": sorted(user_id for user_id, devices in _connections.items() if devices),
            "connections": {user_id: sorted(devices) for user_id, devices in _connections.items() if devices},
            "last_seen": dict(_last_seen),
            "presence": {user_id: dict(context) for user_id, context in _presence.items()},
        }


def clear_online_state() -> None:
    with _lock:
        _connections.clear()
        _last_seen.clear()
        _presence.clear()
