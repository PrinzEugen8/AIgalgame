from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    media_dir: Path
    secrets_path: Path
    log_dir: Path
    host: str = "0.0.0.0"
    port: int = 8899
    log_level: str = "INFO"
    debugpy_enabled: bool = False
    debugpy_host: str = "127.0.0.1"
    debugpy_port: int = 5678
    debugpy_wait_for_client: bool = False
    prewarm_interval_minutes: int = 15


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def load_settings() -> Settings:
    data_dir = Path(os.getenv("AIGALGAME_DATA_DIR", ROOT_DIR / "data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    media_dir = data_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    log_dir = Path(os.getenv("AIGALGAME_LOG_DIR", data_dir / "logs")).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        data_dir=data_dir,
        db_path=Path(os.getenv("AIGALGAME_DB_PATH", data_dir / "app.sqlite3")).resolve(),
        media_dir=media_dir,
        secrets_path=Path(os.getenv("AIGALGAME_SECRETS_PATH", data_dir / "secrets.local.json")).resolve(),
        log_dir=log_dir,
        host=os.getenv("AIGALGAME_HOST", "0.0.0.0"),
        port=int(os.getenv("AIGALGAME_PORT", "8899")),
        log_level=os.getenv("AIGALGAME_LOG_LEVEL", "INFO").upper(),
        debugpy_enabled=_env_bool("AIGALGAME_DEBUGPY"),
        debugpy_host=os.getenv("AIGALGAME_DEBUGPY_HOST", "127.0.0.1"),
        debugpy_port=int(os.getenv("AIGALGAME_DEBUGPY_PORT", "5678")),
        debugpy_wait_for_client=_env_bool("AIGALGAME_DEBUGPY_WAIT"),
        prewarm_interval_minutes=int(os.getenv("AIGALGAME_PREWARM_INTERVAL_MINUTES", "15")),
    )


settings = load_settings()


class SecretStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def set(self, key: str, value: str | None) -> None:
        payload = self._load()
        if value:
            payload[key] = value
        else:
            payload.pop(key, None)
        self._save(payload)

    def set_many(self, prefix: str, values: dict[str, str | None]) -> None:
        payload = self._load()
        for field, value in values.items():
            key = f"{prefix}.{field}"
            if value:
                payload[key] = value
            else:
                payload.pop(key, None)
        self._save(payload)

    def get(self, key: str | None) -> str:
        if not key:
            return ""
        value = self._load().get(key)
        return str(value or "")

    def get_field(self, prefix: str | None, field: str) -> str:
        if not prefix:
            return ""
        payload = self._load()
        value = payload.get(f"{prefix}.{field}")
        if value is None and field == "api_key":
            value = payload.get(prefix)
        return str(value or "")

    def has_fields(self, prefix: str | None, fields: list[str]) -> dict[str, bool]:
        return {field: bool(self.get_field(prefix, field)) for field in fields}


secret_store = SecretStore(settings.secrets_path)
