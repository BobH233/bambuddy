import base64
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, ConfigDict, Field, field_validator

CATEGORIES = {"prints", "progress", "errors", "ams", "maintenance", "queue"}
DEFAULT_CATEGORIES = ["prints", "errors"]
PUSH_HOSTS = {"fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"}
PUSH_SUFFIXES = (".push.apple.com", ".push.services.mozilla.com", ".notify.windows.com")


def validate_endpoint(value: str) -> str:
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if (
            url.scheme != "https"
            or url.username is not None
            or url.password is not None
            or url.port not in (None, 443)
            or url.fragment
            or not url.path.strip("/")
            or not (host in PUSH_HOSTS or host.endswith(PUSH_SUFFIXES))
        ):
            raise ValueError
    except ValueError:
        raise ValueError("请选择浏览器生成的有效推送订阅") from None
    return value


def decode_key(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


class PushKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")
    p256dh: str = Field(min_length=80, max_length=100)
    auth: str = Field(min_length=20, max_length=32)

    @field_validator("p256dh")
    @classmethod
    def public_key(cls, value: str) -> str:
        try:
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decode_key(value))
        except (ValueError, TypeError):
            raise ValueError("推送订阅公钥无效") from None
        return value

    @field_validator("auth")
    @classmethod
    def auth_key(cls, value: str) -> str:
        try:
            if len(decode_key(value)) != 16:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("推送订阅认证信息无效") from None
        return value


class PushEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(max_length=2048)

    @field_validator("endpoint")
    @classmethod
    def endpoint_url(cls, value: str) -> str:
        return validate_endpoint(value)


class PushSubscriptionInput(PushEndpoint):
    keys: PushKeys
    categories: list[str] = Field(default_factory=lambda: DEFAULT_CATEGORIES.copy(), max_length=6)

    @field_validator("categories")
    @classmethod
    def valid_categories(cls, value: list[str]) -> list[str]:
        if not value or set(value) - CATEGORIES:
            raise ValueError("请选择有效的通知类型")
        return sorted(set(value))
