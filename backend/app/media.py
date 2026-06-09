from __future__ import annotations

import base64
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import MediaAsset
from .utils import stable_hash, uid


def save_media(
    session: Session,
    *,
    asset_type: str,
    content: bytes,
    extension: str,
    cache_key: str = "",
    prompt: str = "",
    source_event_id: str = "",
    ai_generated: bool = False,
) -> MediaAsset:
    if cache_key:
        existing = session.execute(select(MediaAsset).where(MediaAsset.local_cache_key == cache_key)).scalar_one_or_none()
        if existing is not None and Path(existing.local_path).exists():
            return existing
    asset_id = uid("asset")
    safe_ext = extension.lstrip(".") or "bin"
    target_dir = settings.media_dir / asset_type
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{asset_id}.{safe_ext}"
    path.write_bytes(content)
    asset = MediaAsset(
        asset_id=asset_id,
        asset_type=asset_type,
        url=f"/media/{asset_id}",
        local_path=str(path),
        local_cache_key=cache_key,
        prompt=prompt,
        source_event_id=source_event_id,
        ai_generated=ai_generated,
    )
    session.add(asset)
    session.commit()
    return asset


def media_from_base64(session: Session, *, asset_type: str, b64: str, extension: str, prompt: str) -> MediaAsset:
    return save_media(
        session,
        asset_type=asset_type,
        content=base64.b64decode(b64),
        extension=extension,
        cache_key=stable_hash(asset_type, prompt, b64[:64]),
        prompt=prompt,
        ai_generated=True,
    )

