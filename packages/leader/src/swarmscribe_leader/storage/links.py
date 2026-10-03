"""HMAC-signed, expiring links for files the leader serves itself."""

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import asdict, dataclass


class InvalidLink(Exception):
    """The link is malformed, forged or expired."""


@dataclass(frozen=True)
class LinkClaims:
    location_id: str
    key: str
    method: str
    version: str
    expires: int


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class LinkSigner:
    def __init__(self, key: bytes):
        if len(key) < 32:
            raise ValueError("the link key must be at least 32 bytes")
        self._key = key

    def _mac(self, payload: str) -> bytes:
        return hmac.new(self._key, payload.encode("ascii"), hashlib.sha256).digest()

    def sign(self, claims: LinkClaims) -> str:
        body = json.dumps(asdict(claims), separators=(",", ":"), sort_keys=True).encode("utf-8")
        payload = _encode(body)
        return payload + "." + _encode(self._mac(payload))

    def verify(self, token: str, *, now: float) -> LinkClaims:
        payload, dot, signature = token.partition(".")
        if not dot or not payload or not signature or not token.isascii():
            raise InvalidLink("malformed link")
        try:
            given = _decode(signature)
        except (ValueError, binascii.Error) as exc:
            raise InvalidLink("malformed link") from exc
        if _encode(given) != signature:
            raise InvalidLink("malformed link")
        if not hmac.compare_digest(given, self._mac(payload)):
            raise InvalidLink("bad link signature")
        try:
            body = _decode(payload)
            if _encode(body) != payload:
                raise InvalidLink("malformed link")
            claims = LinkClaims(**json.loads(body))
        except (ValueError, TypeError, binascii.Error) as exc:
            raise InvalidLink("malformed link") from exc
        if claims.expires <= now:
            raise InvalidLink("link expired")
        return claims
