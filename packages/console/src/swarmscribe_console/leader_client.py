"""Calls from the console to a leader's /v1/admin API, in C1's contract.

Every call carries `Authorization: Console <credential>`, `X-SwarmScribe-Actor` and
`X-SwarmScribe-Actor-Role` in exactly the form the leader's parse_delegation accepts:
`<issuer> <subject> <email>` in visible ASCII separated by single spaces (the email `-` when
the person has none the leader would take), or `system:poller` with role viewer. Redirects
are never followed, the environment's proxy and CA settings are ignored (trust_env=False: these
calls carry credentials), answers over MAX_BODY_BYTES are refused, and a call that has not
finished within its timeout is unreachable whatever the transport does. The actor and role are
checked with the leader's own parse_delegation before anything is sent. A 2xx answer that is not
JSON is a LeaderBadAnswer (the proxy maps it to 502 bad_gateway). Nothing here logs."""

import asyncio
import json
import re
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
from swarmscribe_leader.auth.consoles import (
    MAX_EMAIL_CHARS,
    MAX_ISSUER_CHARS,
    MAX_SUBJECT_CHARS,
    POLLER_ACTOR,
    InvalidActor,
    InvalidActorRole,
    parse_delegation,
)

from .errors import Forbidden

__all__ = [
    "MAX_BODY_BYTES",
    "POLLER_ACTOR",
    "REVOKED_CODE",
    "ActorNotRepresentable",
    "LeaderBadAnswer",
    "LeaderClient",
    "LeaderReply",
    "LeaderTarget",
    "LeaderUnreachable",
    "is_revoked",
    "leader_tls_context",
    "person_actor",
]

MAX_BODY_BYTES = 16 * 1024 * 1024
# C1b: a revoked console credential's 401 carries this code (an unknown one: unauthorized).
# Owner ruling 2026-10-03: the code alone decides; message text is never matched.
REVOKED_CODE = "credential_revoked"
_VISIBLE = re.compile(r"[!-~]+")
_EMAIL = re.compile(r"[^@]+@[^@]+")
_BAD_PATH_CHARS = re.compile(r"[\\@#?\x00-\x20\x7f-\U0010ffff]")
_ENCODED_SEPARATOR = re.compile(r"%(?:2f|5c|00)", re.IGNORECASE)


def _no_constants(_name: str) -> None:
    raise ValueError("NaN and Infinity are not JSON")


def _checked_url(base_url: str, path: str) -> str:
    """base_url + path, or ValueError. A path is `/` then segments: no second leading slash,
    backslash, `@`, `#`, `?` (queries go in params), control character, encoded slash or
    dot segment. The result must stay on base_url's scheme, host and port. Messages are
    fixed: neither value is echoed."""
    base = httpx.URL(base_url)
    if base.scheme != "https" or not base.host:
        raise ValueError("a leader is called over https only")
    if (
        not path.startswith("/")
        or path.startswith("//")
        or _BAD_PATH_CHARS.search(path)
        or _ENCODED_SEPARATOR.search(path)
        or any(unquote(seg) in (".", "..") for seg in path.split("/"))
    ):
        raise ValueError("not a path the console sends to a leader")
    url = httpx.URL(base_url.rstrip("/") + path)
    if (url.scheme, url.host, url.port) != (base.scheme, base.host, base.port):
        raise ValueError("not a path the console sends to a leader")
    return str(url)


class ActorNotRepresentable(Forbidden):
    code = "actor_not_representable"


def person_actor(issuer: str, subject: str, email: str | None) -> str:
    """X-SwarmScribe-Actor for a signed-in person. An email the leader would refuse is sent
    as `-`; an issuer or subject it would refuse cannot be sent at all."""
    if not (
        issuer.startswith("https://")
        and issuer != "https://"
        and len(issuer) <= MAX_ISSUER_CHARS
        and _VISIBLE.fullmatch(issuer)
    ):
        raise ActorNotRepresentable(
            "your identity provider's issuer cannot be sent to a leader"
        )
    if not (len(subject) <= MAX_SUBJECT_CHARS and _VISIBLE.fullmatch(subject)):
        raise ActorNotRepresentable("your account's identifier cannot be sent to a leader")
    sendable = (
        email is not None
        and len(email) <= MAX_EMAIL_CHARS
        and _VISIBLE.fullmatch(email) is not None
        and _EMAIL.fullmatch(email) is not None
    )
    return f"{issuer} {subject} {email if sendable else '-'}"


@dataclass(frozen=True)
class LeaderTarget:
    name: str
    base_url: str
    credential: str = field(repr=False)


@dataclass(frozen=True)
class LeaderReply:
    status: int
    body: Any  # the parsed JSON answer, or None when it was not JSON
    retry_after: str | None


class LeaderUnreachable(Exception):
    """The leader did not answer: `reason` is "timeout", "connect_error" or "transport_error"."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class LeaderBadAnswer(Exception):
    """The leader answered more than MAX_BODY_BYTES, or a success that is not JSON."""


def leader_tls_context(ca_file: Path | None) -> ssl.SSLContext | None:
    """How a leader's certificate is verified. None: httpx's default, the public roots
    (certifi's) it ships with. With `ca_file` (SWARMSCRIBE_CONSOLE_LEADER_CA_FILE): those roots plus
    the CA certificates in that PEM file, for leaders on a private CA. Verification and host-name
    checking are never turned off, and the environment (SSL_CERT_FILE) is never read."""
    if ca_file is None:
        return None
    context = httpx.create_ssl_context(trust_env=False)
    context.load_verify_locations(cafile=str(ca_file))
    return context


class LeaderClient:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        ca_file: Path | None = None,
    ):
        context = leader_tls_context(ca_file)
        self._client = httpx.AsyncClient(
            transport=transport,
            verify=True if context is None else context,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(None),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def call(
        self,
        target: LeaderTarget,
        method: str,
        path: str,
        *,
        actor: str,
        role: str,
        timeout: float,
        params: dict[str, str] | None = None,
        json_body: Any = None,
    ) -> LeaderReply:
        try:
            parse_delegation([actor], [role])
        except (InvalidActor, InvalidActorRole):
            raise ActorNotRepresentable("the actor or role cannot be sent to a leader") from None
        headers = {
            "Authorization": f"Console {target.credential}",
            "X-SwarmScribe-Actor": actor,
            "X-SwarmScribe-Actor-Role": role,
            "Accept": "application/json",
            # Count raw bytes against the cap; a leader that compresses anyway is still
            # counted after decoding below.
            "Accept-Encoding": "identity",
        }
        url = _checked_url(target.base_url, path)
        chunks: list[bytes] = []
        try:
            async with asyncio.timeout(timeout):
                async with self._client.stream(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=timeout,
                ) as response:
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_BODY_BYTES:
                            raise LeaderBadAnswer(f"leader {target.name} answered too much")
                        chunks.append(chunk)
        except TimeoutError:
            raise LeaderUnreachable("timeout") from None
        except httpx.TimeoutException:
            raise LeaderUnreachable("timeout") from None
        except httpx.ConnectError:
            raise LeaderUnreachable("connect_error") from None
        except httpx.HTTPError:
            raise LeaderUnreachable("transport_error") from None
        raw = b"".join(chunks)
        body: Any = None
        if raw:
            try:
                if "json" not in response.headers.get("content-type", ""):
                    raise ValueError
                body = json.loads(raw, parse_constant=_no_constants)
            except (ValueError, RecursionError):  # RecursionError: absurdly nested JSON
                if 200 <= response.status_code < 300:
                    raise LeaderBadAnswer(
                        f"leader {target.name} answered something not JSON"
                    ) from None
        return LeaderReply(response.status_code, body, response.headers.get("retry-after"))


def is_revoked(reply: LeaderReply) -> bool:
    """A 401 meaning this console's credential was revoked (C1b), as opposed to unknown."""
    body = reply.body
    return reply.status == 401 and isinstance(body, dict) and body.get("code") == REVOKED_CODE
