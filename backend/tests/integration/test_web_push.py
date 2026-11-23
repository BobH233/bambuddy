import base64
import json
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.core.config import settings
from backend.app.models.web_push import WebPushSubscription
from backend.app.schemas.web_push import PushSubscriptionInput
from backend.app.services import web_push
from backend.app.services.notification_service import NotificationService
from backend.tests.integration.test_printer_scope_1727 import _admin_token, _auth, _group, _user

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def encoded(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def subscription(suffix="test", categories=None):
    key = ec.generate_private_key(ec.SECP256R1())
    return {
        "endpoint": "https://web.push.apple.com/" + suffix,
        "keys": {
            "p256dh": encoded(
                key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
            ),
            "auth": encoded(b"0123456789abcdef"),
        },
        "categories": categories or ["prints", "errors"],
    }


@pytest.fixture(autouse=True)
def push_runtime(monkeypatch, tmp_path, test_engine):
    monkeypatch.setattr(settings, "base_dir", tmp_path)
    monkeypatch.setattr(web_push, "async_session", async_sessionmaker(test_engine, expire_on_commit=False))
    web_push._load_key.cache_clear()
    yield
    web_push._load_key.cache_clear()


async def member(client, admin, name="operator", printers=None, permissions=None):
    perms = await _group(client, admin, name + "-permissions", permissions=permissions or ["printers:read"])
    groups = [perms]
    if printers is not None:
        groups.append(await _group(client, admin, name + "-printers", printer_ids=printers))
    return _auth((await _user(client, admin, name, groups))[0])


async def test_anonymous_cannot_obtain_config_or_subscribe(async_client):
    assert (await async_client.get("/api/v1/web-push/config")).status_code == 401
    assert (await async_client.post("/api/v1/web-push/subscribe", json=subscription())).status_code == 401


async def test_operator_can_subscribe_without_admin_notification_permissions(async_client, db_session):
    admin = await _admin_token(async_client)
    operator = await member(async_client, admin)
    config = await async_client.get("/api/v1/web-push/config", headers=operator)
    assert config.status_code == 200
    assert config.headers["cache-control"] == "no-store"
    assert len(base64.urlsafe_b64decode(config.json()["public_key"] + "==")) == 65
    info = subscription()
    assert (await async_client.post("/api/v1/web-push/subscribe", headers=operator, json=info)).status_code == 200
    info["categories"] = ["maintenance"]
    assert (await async_client.post("/api/v1/web-push/subscribe", headers=operator, json=info)).status_code == 200
    rows = (await db_session.execute(select(WebPushSubscription))).scalars().all()
    assert len(rows) == 1 and rows[0].categories == ["maintenance"]
    result = await async_client.get("/api/v1/web-push/config", headers=operator)
    assert result.json()["subscriptions"][0]["categories"] == ["maintenance"]
    assert info["endpoint"] not in result.text
    assert info["keys"]["auth"] not in result.text
    assert "PRIVATE KEY" not in result.text


async def test_account_cannot_take_over_test_or_delete_another_accounts_device(async_client, monkeypatch, db_session):
    admin = await _admin_token(async_client)
    a = await member(async_client, admin, "operator-a")
    b = await member(async_client, admin, "operator-b")
    info = subscription()
    await async_client.post("/api/v1/web-push/subscribe", headers=a, json=info)
    assert (await async_client.post("/api/v1/web-push/subscribe", headers=b, json=info)).status_code == 409
    mocked = Mock(return_value="sent")
    monkeypatch.setattr(web_push, "send_push", mocked)
    assert (
        await async_client.post("/api/v1/web-push/test", headers=b, json={"endpoint": info["endpoint"]})
    ).status_code == 404
    assert not mocked.called
    assert not (await async_client.get("/api/v1/web-push/config", headers=b)).json()["subscriptions"]
    await async_client.post("/api/v1/web-push/unsubscribe", headers=b, json={"endpoint": info["endpoint"]})
    assert len((await db_session.execute(select(WebPushSubscription))).scalars().all()) == 1
    await async_client.post("/api/v1/web-push/unsubscribe", headers=a, json={"endpoint": info["endpoint"]})
    assert not (await db_session.execute(select(WebPushSubscription))).scalars().all()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://web.push.apple.com/test",
        "https://127.0.0.1/test",
        "https://web.push.apple.com.evil.example/test",
        "https://evil.example/push",
        "https://web.push.apple.com:8443/test",
        "https://u:p@web.push.apple.com/test",
        "https://web.push.apple.com/test#fragment",
        "https://169.254.169.254/latest/meta-data/",
    ],
)
async def test_rejects_non_push_and_unsafe_endpoints(endpoint):
    info = subscription()
    info["endpoint"] = endpoint
    with pytest.raises(ValueError):
        PushSubscriptionInput.model_validate(info)


async def test_rejects_malformed_keys_and_unknown_or_empty_categories():
    for patch in [{"keys": {"p256dh": "x" * 87, "auth": "x" * 22}}, {"categories": ["secrets"]}, {"categories": []}]:
        with pytest.raises(ValueError):
            PushSubscriptionInput.model_validate({**subscription(), **patch})


async def test_test_notification_is_device_specific_rate_limited_and_removes_expired(
    async_client, monkeypatch, db_session
):
    admin = _auth(await _admin_token(async_client))
    await async_client.get("/api/v1/web-push/config", headers=admin)
    a, b = subscription("one"), subscription("two")
    for info in [a, b]:
        await async_client.post("/api/v1/web-push/subscribe", headers=admin, json=info)
    mocked = Mock(return_value="sent")
    monkeypatch.setattr(web_push, "send_push", mocked)
    path = "/api/v1/web-push/test"
    assert (await async_client.post(path, headers=admin, json={"endpoint": a["endpoint"]})).status_code == 200
    assert mocked.call_args[0][0]["endpoint"] == a["endpoint"]
    assert (await async_client.post(path, headers=admin, json={"endpoint": a["endpoint"]})).status_code == 429
    mocked.return_value = "expired"
    assert (await async_client.post(path, headers=admin, json={"endpoint": b["endpoint"]})).status_code == 410
    assert len((await db_session.execute(select(WebPushSubscription))).scalars().all()) == 1


async def test_delivery_rechecks_printer_scope_active_account_and_selected_events(
    async_client, db_session, printer_factory, monkeypatch
):
    from backend.app.models.user import User

    admin = await _admin_token(async_client)
    ours, theirs = await printer_factory(name="ours"), await printer_factory(name="theirs")
    operator = await member(async_client, admin, printers=[ours.id])
    await async_client.get("/api/v1/web-push/config", headers=operator)
    await async_client.post("/api/v1/web-push/subscribe", headers=operator, json=subscription())
    sent = Mock(return_value="sent")
    monkeypatch.setattr(web_push, "send_push", sent)
    await web_push.notify("print_complete", "打印完成", "测试模型", theirs.id)
    await web_push.notify("print_progress", "进度", "25%", ours.id)
    await web_push.notify("queue_completed", "队列完成", "", None)
    assert sent.call_count == 0
    await web_push.notify("print_complete", "打印完成", "测试模型", ours.id)
    assert sent.call_count == 1
    user = (await db_session.execute(select(User).where(User.username == "operator"))).scalar_one()
    user.is_active = False
    await db_session.commit()
    await web_push.notify("print_complete", "打印完成", "测试模型", ours.id)
    assert sent.call_count == 1


async def test_events_reach_personal_push_when_no_global_channel_is_configured(
    async_client, db_session, printer_factory, monkeypatch
):
    admin = _auth(await _admin_token(async_client))
    printer = await printer_factory()
    await async_client.get("/api/v1/web-push/config", headers=admin)
    await async_client.post("/api/v1/web-push/subscribe", headers=admin, json=subscription())
    sent = Mock(return_value="sent")
    monkeypatch.setattr(web_push, "send_push", sent)
    await NotificationService().on_print_complete(
        printer.id, "测试打印机", "completed", {"filename": "sample.3mf"}, db_session
    )
    assert sent.call_count == 1
    assert "sample" in json.dumps(sent.call_args[0][1])


async def test_push_failure_does_not_block_existing_channels(db_session, monkeypatch):
    service = NotificationService()
    monkeypatch.setattr(web_push, "notify", AsyncMock(side_effect=RuntimeError("network")))
    provider = Mock(id=1, name="test", daily_digest_enabled=False)
    service._send_to_provider = AsyncMock(return_value=(True, ""))
    service._update_provider_status = AsyncMock()
    service._log_notification = AsyncMock()
    await service._send_to_providers([provider], "title", "message", db_session, "print_complete", 1)
    service._send_to_provider.assert_awaited_once()
