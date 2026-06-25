from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from models import WebhookEnvelope


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


async def validate_speakr_request(request: Request, secret: str) -> ValidatedWebhook:
    raw_body = await request.body()
    signature = request.headers.get("Speakr-Signature")
    if not verify_signature(raw_body, signature, secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        payload = WebhookEnvelope.model_validate_json(raw_body)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload") from exc

    event = request.headers.get("Speakr-Event", payload.event)
    delivery_id = request.headers.get("Speakr-Delivery-Id")
    return ValidatedWebhook(
        delivery_id=delivery_id,
        event=event,
        payload=payload,
        raw_body=raw_body,
    )

