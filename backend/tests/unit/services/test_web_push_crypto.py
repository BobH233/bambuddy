import base64
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import http_ece
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from requests import Response

from backend.app.core.config import settings
from backend.app.services import web_push


def test_persistent_private_key_is_atomic_private_and_not_returned(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "base_dir", tmp_path)
    web_push._load_key.cache_clear()
    with ThreadPoolExecutor(max_workers=4) as pool:
        keys = list(pool.map(lambda _: web_push.public_key(), range(8)))
    assert len(set(keys)) == 1
    assert web_push.key_path().stat().st_mode & 0o777 == 0o600
    web_push._load_key.cache_clear()
    assert web_push.public_key() == keys[0]
    assert not list(tmp_path.glob(".webpush-vapid-*"))


def test_real_payload_encryption_vapid_signature_and_redirect_protection(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "base_dir", tmp_path)
    web_push.public_key()
    client_key = ec.generate_private_key(ec.SECP256R1())
    auth = b"0123456789abcdef"

    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    subscription = {
        "endpoint": "https://web.push.apple.com/mock-endpoint",
        "keys": {
            "p256dh": encode(
                client_key.public_key().public_bytes(
                    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
                )
            ),
            "auth": encode(auth),
        },
    }
    captured = {}

    def request(self, method, url, **kwargs):
        captured.update(kwargs)
        assert kwargs["allow_redirects"] is False
        result = Response()
        result.status_code = 201
        return result

    monkeypatch.setattr(web_push.Session, "request", request)
    payload = {"title": "打印完成", "body": "测试", "url": "/"}
    assert web_push.send_push(subscription, payload) == "sent"
    decoded = http_ece.decrypt(captured["data"], private_key=client_key, auth_secret=auth, version="aes128gcm")
    assert json.loads(decoded) == payload
    authorization = captured["headers"]["Authorization"]
    token = authorization.split("t=", 1)[1].split(",", 1)[0]
    claims = jwt.decode(
        token,
        web_push._load_key(str(web_push.key_path())).public_key(),
        algorithms=["ES256"],
        audience="https://web.push.apple.com",
    )
    assert claims["sub"] == settings.web_push_contact


def test_failed_push_logs_no_endpoint_keys_or_remote_response(monkeypatch, caplog):
    response = Response()
    response.status_code = 403
    response._content = b"private-response-body"
    error = web_push.WebPushException("private-endpoint-and-key", response=response)
    monkeypatch.setattr(web_push, "webpush", Mock(side_effect=error))
    assert web_push.send_push({"endpoint": "https://web.push.apple.com/private-endpoint"}, {}) == "failed"
    assert "private" not in caplog.text
