from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any


_lock = threading.Lock()
_connections: dict[str, set[str]] = defaultdict(set)
_last_seen: dict[str, float] = {}


def mark_online(user_id: str, device_id: str = "device") -> None:
    resolved_user = str(user_id or "").strip()
    if not resolved_user:
        return
    resolved_device = str(device_id or "device").strip() or "device"
    with _lock:
        _connections[resolved_user].add(resolved_device)
        _last_seen[resolved_user] = time.time()


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


def is_online(user_id: str) -> bool:
    resolved_user = str(user_id or "").strip()
    if not resolved_user:
        return False
    with _lock:
        return bool(_connections.get(resolved_user))


def online_users() -> set[str]:
    with _lock:
        return {user_id for user_id, devices in _connections.items() if devices}


def snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "online_users": sorted(user_id for user_id, devices in _connections.items() if devices),
            "connections": {user_id: sorted(devices) for user_id, devices in _connections.items() if devices},
            "last_seen": dict(_last_seen),
        }


def clear_online_state() -> None:
    with _lock:
        _connections.clear()
        _last_seen.clear()
