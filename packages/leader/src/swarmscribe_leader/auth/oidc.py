"""Validate administrators' ID tokens from Microsoft Entra ID and Google.

Tokens are never logged: messages name the failed check only."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from ..config import Settings
from ..errors import Unauthorized

logger = logging.getLogger(__name__)

Fetch = Callable[[str], Awaitable[dict[str, Any]]]
ProviderName = Literal["entra", "google"]

CLOCK_SKEW_SECONDS = 60
MAX_TOKEN_CHARS = 16 * 1024
JWKS_MIN_REFRESH_SECONDS = 60
JWKS_MAX_AGE_SECONDS = 24 * 3600
READY_RETRY_SECONDS = 5
ENTRA_AUTHORITY = "https://login.microsoftonline.com"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
GOOGLE_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"


class MetadataUnavailable(Exception):
    """A provider's discovery document or signing keys cannot be fetched (transient)."""


@dataclass(frozen=True)
class Provider:
    name: ProviderName
    issuers: tuple[str, ...]
    client_id: str
    discovery_url: str
    tenant_id: str | None = None
    hosted_domain: str | None = None


@dataclass(frozen=True)
class Identity:
    provider: ProviderName
    issuer: str
    subject: str
    email: str | None
    claims: dict[str, Any]

    @property
    def actor(self) -> str:
        """How the audit log names this person: email, then the (issuer, sub) identity."""
        return f"{self.email or 'unknown'} ({self.issuer} {self.subject})"


def providers_from(settings: Settings) -> tuple[Provider, ...]:
    found: list[Provider] = []
    if settings.entra_client_id and settings.entra_tenant_id:
        issuer = f"{ENTRA_AUTHORITY}/{settings.entra_tenant_id}/v2.0"
        found.append(
            Provider(
                name="entra",
                issuers=(issuer,),
                client_id=settings.entra_client_id,
                discovery_url=f"{issuer}/.well-known/openid-configuration",
                tenant_id=settings.entra_tenant_id,
            )
        )
    if settings.google_client_id:
        found.append(
            Provider(
                name="google",
                issuers=GOOGLE_ISSUERS,
                client_id=settings.google_client_id,
                discovery_url=GOOGLE_DISCOVERY,
                hosted_domain=settings.google_hosted_domain,
            )
        )
    return tuple(found)


def login_providers(settings: Settings) -> list[dict[str, str | None]]:
    """What the admin CLI needs for each provider's device-code sign-in."""
    found: list[dict[str, str | None]] = []
    if settings.entra_client_id:
        base = f"{ENTRA_AUTHORITY}/{settings.entra_tenant_id}/oauth2/v2.0"
        found.append(
            {
                "name": "entra",
                "client_id": settings.entra_client_id,
                "device_authorization_endpoint": f"{base}/devicecode",
                "token_endpoint": f"{base}/token",
                "scope": "openid profile email offline_access",
                "client_secret": None,
            }
        )
    if settings.google_client_id:
        secret = settings.google_client_secret
        found.append(
            {
                "name": "google",
                "client_id": settings.google_client_id,
                "device_authorization_endpoint": "https://oauth2.googleapis.com/device/code",
                "token_endpoint": "https://oauth2.googleapis.com/token",
                "scope": "openid email profile",
                # Google's limited-input-device clients need their secret for device sign-in;
                # Google documents it as not confidential.
                "client_secret": secret.get_secret_value() if secret else None,
            }
        )
    return found


async def http_fetch(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


class _ProviderKeys:
    """One provider's discovery metadata and signing keys, fetched lazily and cached.

    Keys are fetched again when they are a day old, or when a token names a key id we do not
    have (a rotation) — but never more than once a minute, so made-up key ids cannot make
    the leader hammer the provider. While a refresh fails, the keys we have keep working.
    """

    def __init__(self, provider: Provider, fetch: Fetch, clock: Callable[[], float]):
        self.provider = provider
        self.fetch = fetch
        self.clock = clock
        self.jwks_uri: str | None = None
        self.keys: dict[str, Any] = {}
        self.fetched_at: float | None = None
        self.attempted_at: float | None = None
        self.lock = asyncio.Lock()

    async def _refresh(self, now: float) -> None:
        self.attempted_at = now
        try:
            if self.jwks_uri is None:
                metadata = await self.fetch(self.provider.discovery_url)
                if metadata.get("issuer") not in self.provider.issuers:
                    raise ValueError("the discovery document names another issuer")
                self.jwks_uri = str(metadata["jwks_uri"])
            document = await self.fetch(self.jwks_uri)
            keys = {}
            for jwk in document.get("keys", []):
                usable = jwk.get("kty") == "RSA" and jwk.get("use", "sig") == "sig"
                if not usable or not jwk.get("kid"):
                    continue
                keys[str(jwk["kid"])] = RSAAlgorithm.from_jwk(jwk)
        except Exception as exc:  # network, HTTP status, malformed documents
            logger.warning(
                "%s sign-in keys could not be fetched: %s", self.provider.name, type(exc).__name__
            )
            return
        self.keys = keys
        self.fetched_at = now

    async def key(self, kid: str) -> Any | None:
        async with self.lock:
            now = self.clock()
            due = (
                self.fetched_at is None
                or now - self.fetched_at > JWKS_MAX_AGE_SECONDS
                or kid not in self.keys
            )
            allowed = (
                self.attempted_at is None or now - self.attempted_at >= JWKS_MIN_REFRESH_SECONDS
            )
            if due and allowed:
                await self._refresh(now)
            if self.fetched_at is None:
                raise MetadataUnavailable(f"{self.provider.name} sign-in keys are not available")
            return self.keys.get(kid)

    async def ready(self) -> bool:
        async with self.lock:
            if self.fetched_at is None:
                now = self.clock()
                if self.attempted_at is None or now - self.attempted_at >= READY_RETRY_SECONDS:
                    await self._refresh(now)
            return self.fetched_at is not None


class TokenVerifier:
    def __init__(
        self,
        providers: Sequence[Provider],
        *,
        fetch: Fetch = http_fetch,
        clock: Callable[[], float] = time.time,
    ):
        self.providers = tuple(providers)
        self._keys = {p.name: _ProviderKeys(p, fetch, clock) for p in self.providers}

    async def ready(self) -> bool:
        """Whether every configured provider's metadata has been fetched at least once."""
        results = [await keys.ready() for keys in self._keys.values()]
        return all(results)

    def _provider_for(self, issuer: object) -> Provider | None:
        for provider in self.providers:
            if issuer in provider.issuers:
                return provider
        return None

    async def verify(self, token: str) -> Identity:
        if not self.providers:
            raise Unauthorized("sign-in is not configured on this leader")
        if len(token) > MAX_TOKEN_CHARS:
            raise Unauthorized("the sign-in token is too large")
        try:
            header = jwt.get_unverified_header(token)
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise Unauthorized("the sign-in token is malformed") from exc
        if header.get("alg") != "RS256":
            raise Unauthorized("the sign-in token must be signed with RS256")
        provider = self._provider_for(unverified.get("iss"))
        if provider is None:
            raise Unauthorized("the sign-in token is from an issuer this leader does not accept")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise Unauthorized("the sign-in token names no signing key")
        key = await self._keys[provider.name].key(kid)
        if key is None:
            raise Unauthorized("the sign-in token is signed with an unknown key")
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=provider.client_id,
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise Unauthorized("the sign-in token has expired", code="token_expired") from exc
        except jwt.PyJWTError as exc:
            raise Unauthorized(f"the sign-in token is not valid: {exc}") from exc
        email = self._check_provider_claims(provider, claims)
        return Identity(
            provider=provider.name,
            issuer=provider.issuers[0],
            subject=str(claims["sub"]),
            email=email,
            claims=claims,
        )

    @staticmethod
    def _check_provider_claims(provider: Provider, claims: dict[str, Any]) -> str | None:
        """Provider-specific checks; returns the person's email (lowercase) if known."""
        if provider.name == "entra":
            if str(claims.get("tid", "")).lower() != provider.tenant_id:
                raise Unauthorized("the sign-in token is from another Entra ID tenant")
            email = claims.get("email") or claims.get("preferred_username")
        else:
            if claims.get("email_verified") not in (True, "true"):
                raise Unauthorized("the Google account's email address is not verified")
            if provider.hosted_domain and str(claims.get("hd", "")).lower() != (
                provider.hosted_domain
            ):
                raise Unauthorized("the Google account is not in the allowed hosted domain")
            email = claims.get("email")
        return str(email).lower() if email else None
