"""Web Push delivery independent of administrator notification channels."""

import asyncio
import base64
import hashlib
import json
import logging
import os
import tempfile
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush
from requests import Session
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from backend.app.core.config import settings
from backend.app.core.database import async_session
from backend.app.core.printer_scope import resolve_user_printer_scope
from backend.app.models.user import User
from backend.app.models.web_push import WebPushSubscription
from backend.app.schemas.web_push import validate_endpoint

logger = logging.getLogger(__name__)

EVENT_CATEGORIES = {
    **dict.fromkeys(
        (
            "print_start",
            "print_complete",
            "print_failed",
            "print_stopped",
            "print_confirm_request",
            "print_pause_upcoming",
        ),
        "prints",
    ),
    **dict.fromkeys(("print_progress", "first_layer_complete", "bed_cooled"), "progress"),
    **dict.fromkeys(
        ("printer_offline", "printer_error", "ai_failure_detection", "plate_not_empty", "plate_clear_required"),
        "errors",
    ),
    **dict.fromkeys(
        (
            "filament_low",
            "print_missing_spool_assignment",
            "ams_humidity_high",
            "ams_temperature_high",
            "ams_drying_suspended",
            "ams_ht_humidity_high",
            "ams_ht_temperature_high",
        ),
        "ams",
    ),
    "maintenance_due": "maintenance",
    **dict.fromkeys(
        (
            "queue_job_added",
            "queue_job_assigned",
            "queue_job_started",
            "queue_job_waiting",
            "queue_job_skipped",
            "queue_job_failed",
            "queue_completed",
        ),
        "queue",
    ),
}


def endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()


def key_path() -> Path:
    return settings.base_dir / "webpush-vapid.pem"


@lru_cache(maxsize=4)
def _load_key(path_string: str) -> ec.EllipticCurvePrivateKey:
    path = Path(path_string)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        fd, temporary = tempfile.mkstemp(prefix=".webpush-vapid-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(pem)
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            os.unlink(temporary)
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("Invalid VAPID key")
    path.chmod(0o600)
    return key


def public_key() -> str:
    key = _load_key(str(key_path()))
    encoded = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()


class NoRedirectSession(Session):
    def request(self, method, url, **kwargs):
        validate_endpoint(url)
        kwargs["allow_redirects"] = False
        return super().request(method, url, **kwargs)


def send_push(subscription: dict, payload: dict) -> str:
    try:
        validate_endpoint(subscription["endpoint"])
        with NoRedirectSession() as session:
            response = webpush(
                subscription_info=subscription,
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=str(key_path()),
                vapid_claims={"sub": settings.web_push_contact},
                ttl=3600,
                timeout=10,
                requests_session=session,
            )
        if response.status_code in (404, 410):
            return "expired"
        return "sent" if 200 <= response.status_code < 300 else "failed"
    except WebPushException as exc:
        if exc.response is not None and exc.response.status_code in (404, 410):
            return "expired"
        logger.warning("Web Push delivery failed (%s)", getattr(exc.response, "status_code", "network"))
    except Exception as exc:
        logger.warning("Web Push delivery failed (%s)", type(exc).__name__)
    return "failed"


async def has_subscribers(db, event_field: str) -> bool:
    category = EVENT_CATEGORIES.get(event_field.removeprefix("on_"))
    if category is None or not key_path().exists():
        return False
    with db.no_autoflush:
        categories = (await db.execute(select(WebPushSubscription.categories))).scalars().all()
    return any(category in selected for selected in categories)


async def notify(event_type: str, title: str, message: str, printer_id: int | None) -> None:
    category = EVENT_CATEGORIES.get(event_type)
    if category is None or not key_path().exists():
        return
    async with async_session() as db:
        rows = (
            await db.execute(
                select(WebPushSubscription, User)
                .join(User, User.id == WebPushSubscription.user_id)
                .options(selectinload(User.groups))
                .where(User.is_active.is_(True))
            )
        ).all()
        recipients = []
        for row, user in rows:
            if category not in row.categories or not user.has_permission("printers:read"):
                continue
            scope = await resolve_user_printer_scope(db, user)
            if not scope.allows(printer_id) or (printer_id is None and not scope.is_unrestricted):
                continue
            if category == "queue" and not (user.is_admin or user.has_permission("queue:read_all")):
                continue
            recipients.append((row.id, {"endpoint": row.endpoint, "keys": {"p256dh": row.p256dh, "auth": row.auth}}))
    payload = {
        "title": title[:160],
        "body": message[:650],
        "url": "/queue" if category == "queue" else "/",
        "tag": f"bambuddy:{printer_id}:{event_type}",
    }
    limit = asyncio.Semaphore(4)

    async def deliver(row_id, subscription):
        async with limit:
            return row_id, await asyncio.to_thread(send_push, subscription, payload)

    results = await asyncio.gather(*(deliver(row_id, info) for row_id, info in recipients))
    expired = [row_id for row_id, result in results if result == "expired"]
    if expired:
        async with async_session() as db:
            await db.execute(delete(WebPushSubscription).where(WebPushSubscription.id.in_(expired)))
            await db.commit()
