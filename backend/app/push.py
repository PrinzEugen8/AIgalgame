from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .diagnostics import write_diagnostic
from .models import DeviceRegistration, ProactiveDeliveryAttempt, ProactiveEvent
from .providers import FcmHttpV1Client, ProviderError, get_enabled_provider, provider_ready
from .utils import dump_json, uid, utc_now


def register_device(session: Session, payload: dict[str, Any]) -> DeviceRegistration:
    device_id = str(payload.get("device_id") or "").strip()
    if not device_id:
        raise ValueError("device_id is required")
    registration = session.get(DeviceRegistration, device_id)
    if registration is None:
        registration = DeviceRegistration(device_id=device_id, user_id=str(payload.get("user_id") or "demo_user"))
        session.add(registration)
    registration.user_id = str(payload.get("user_id") or registration.user_id or "demo_user")
    registration.platform = str(payload.get("platform") or "android")
    registration.push_token = str(payload.get("push_token") or registration.push_token or "")
    registration.app_version = str(payload.get("app_version") or registration.app_version or "")
    registration.locale = str(payload.get("locale") or registration.locale or "")
    registration.timezone = str(payload.get("timezone") or registration.timezone or "")
    registration.notifications_enabled = bool(payload.get("notifications_enabled", registration.notifications_enabled))
    registration.last_seen_at = utc_now()
    registration.updated_at = utc_now()
    session.commit()
    write_diagnostic("device_registered", user_id=registration.user_id, device_id=device_id, platform=registration.platform, has_push_token=bool(registration.push_token))
    return registration


def record_delivery_attempt(
    session: Session,
    *,
    event: ProactiveEvent,
    channel: str,
    target_device_id: str = "",
    status: str,
    status_code: int = 0,
    error_message: str = "",
    request: dict[str, Any] | None = None,
    response: dict[str, Any] | None = None,
) -> ProactiveDeliveryAttempt:
    attempt = ProactiveDeliveryAttempt(
        attempt_id=uid("pda"),
        proactive_event_id=event.proactive_event_id,
        user_id=event.user_id,
        character_id=event.character_id,
        channel=channel,
        target_device_id=target_device_id,
        status=status,
        status_code=status_code,
        error_message=error_message,
        request_json=dump_json(request or {}),
        response_json=dump_json(response or {}),
        created_at=utc_now(),
    )
    session.add(attempt)
    session.commit()
    write_diagnostic(
        "proactive_delivery_attempt",
        proactive_event_id=event.proactive_event_id,
        channel=channel,
        target_device_id=target_device_id,
        status=status,
        status_code=status_code,
        error_message=error_message[:200],
    )
    return attempt


def send_proactive_push(session: Session, event: ProactiveEvent) -> dict[str, Any]:
    config = get_enabled_provider(session, "push")
    if config is None or not provider_ready(config):
        record_delivery_attempt(session, event=event, channel="fcm", status="skipped", error_message="push provider not configured")
        return {"ok": False, "sent": 0, "failed": 0, "skipped": 1, "reason": "push_provider_not_configured"}
    devices = session.execute(
        select(DeviceRegistration).where(
            DeviceRegistration.user_id == event.user_id,
            DeviceRegistration.platform == "android",
            DeviceRegistration.notifications_enabled == True,  # noqa: E712
        )
    ).scalars().all()
    if not devices:
        record_delivery_attempt(session, event=event, channel="fcm", status="skipped", error_message="no registered device")
        return {"ok": False, "sent": 0, "failed": 0, "skipped": 1, "reason": "no_registered_device"}
    client = FcmHttpV1Client(config)
    sent = 0
    failed = 0
    skipped = 0
    for device in devices:
        if not device.push_token:
            skipped += 1
            record_delivery_attempt(session, event=event, channel="fcm", target_device_id=device.device_id, status="skipped", error_message="empty push token")
            continue
        data = {
            "proactive_event_id": event.proactive_event_id,
            "title": event.title,
            "text": event.text,
            "source_type": event.source_type,
        }
        try:
            result = client.send(token=device.push_token, title=event.title, body=event.text, data=data)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            record_delivery_attempt(
                session,
                event=event,
                channel="fcm",
                target_device_id=device.device_id,
                status="failed",
                error_message=str(exc),
                request={"data": data},
            )
            continue
        sent += 1
        record_delivery_attempt(
            session,
            event=event,
            channel="fcm",
            target_device_id=device.device_id,
            status="sent",
            status_code=int(result.get("status_code") or 0),
            request=result.get("request") if isinstance(result.get("request"), dict) else {"data": data},
            response=result.get("body") if isinstance(result.get("body"), dict) else {},
        )
    if sent:
        from .proactive import mark_proactive_delivered

        mark_proactive_delivered(session, event.proactive_event_id)
    return {"ok": bool(sent), "sent": sent, "failed": failed, "skipped": skipped}


def send_selected_background_push(session: Session, event_id: str) -> dict[str, Any]:
    event = session.get(ProactiveEvent, event_id)
    if event is None:
        raise ProviderError("proactive event not found")
    return send_proactive_push(session, event)
