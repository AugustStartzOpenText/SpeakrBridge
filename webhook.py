from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status

from models import RecordingRef, WebhookEnvelope

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ValidatedWebhook:
    delivery_id: str | None
    event: str
    payload: WebhookEnvelope
    raw_body: bytes


def verify_signature(body: bytes, header: str | None, secret: str) -> bool:
    if not header:
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def _coerce_recording_id(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _extract_recording_ref(payload_data: dict[str, Any]) -> RecordingRef | None:
    candidates: list[dict[str, Any]] = []

    top_level_recording = payload_data.get("recording")
    if isinstance(top_level_recording, dict):
        candidates.append(top_level_recording)

    data = payload_data.get("data")
    if isinstance(data, dict):
        nested_recording = data.get("recording")
        if isinstance(nested_recording, dict):
            candidates.append(nested_recording)
        candidates.append(data)

    candidates.append(payload_data)

    for candidate in candidates:
        recording_id = _coerce_recording_id(candidate.get("id"))
        if recording_id is None:
            recording_id = _coerce_recording_id(candidate.get("recording_id"))
        if recording_id is None:
            continue
        return RecordingRef.model_validate(candidate | {"id": recording_id})

    return None


async def validate_speakr_request(request: Request, secret: str) -> ValidatedWebhook:
    raw_body = await request.body()
    signature = request.headers.get("Speakr-Signature")
    if not verify_signature(raw_body, signature, secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        payload_data = json.loads(raw_body)
        if not isinstance(payload_data, dict):
            raise ValueError("Webhook payload must be a JSON object")
        recording = _extract_recording_ref(payload_data)
        event = request.headers.get("Speakr-Event") or str(payload_data.get("event") or "")
        if not event:
            raise ValueError("Webhook event is missing")
        if recording is None:
            raise ValueError("Recording id is missing from webhook payload")
        payload = WebhookEnvelope(event=event, recording=recording)
    except Exception as exc:
        LOGGER.exception(
            "Invalid webhook payload: %s | body=%s",
            exc,
            raw_body.decode("utf-8", errors="replace"),
            extra={
                "content_type": request.headers.get("content-type"),
                "delivery_id": request.headers.get("Speakr-Delivery-Id"),
                "event_header": request.headers.get("Speakr-Event"),
            },
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload") from exc

    event = request.headers.get("Speakr-Event", payload.event)
    delivery_id = request.headers.get("Speakr-Delivery-Id")
    return ValidatedWebhook(
        delivery_id=delivery_id,
        event=event,
        payload=payload,
        raw_body=raw_body,
    )
