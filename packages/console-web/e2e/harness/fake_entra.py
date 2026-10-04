"""Microsoft Entra ID as the console sees it, for the end-to-end harness only.

The console's authorization endpoint is the real Entra host, so the browser never reaches
it: the Playwright test stops that navigation, asks the harness's control server to "sign
in" a persona at that URL (`authorize`), and visits the callback it returns. The console
fetches discovery and keys through `fetch` and exchanges the code through `transport`, both
handed to create_app, so nothing leaves the machine."""

import base64
import hashlib
import json
import secrets
import time
import uuid
from urllib.parse import parse_qsl, urlencode

import httpx
import jwt
from console_testkit import ENTRA_CLIENT, ENTRA_ISSUER, ENTRA_SECRET, ENTRA_TENANT, GROUPS
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

JWKS_URL = f"https://login.microsoftonline.com/{ENTRA_TENANT}/discovery/v2.0/keys"
KEY_ID = "e2e-key-1"

# Personas the end-to-end tests sign in as: their Entra groups decide their console roles
# (serve.py grants viewer, operator and admin on every leader, and console administration).
PERSONAS: dict[str, list[str]] = {
    "viewer": [GROUPS["viewer"]],
    "operator": [GROUPS["operator"]],
    "admin": [GROUPS["admin"], GROUPS["console"]],
}


class UnknownPersona(Exception):
    pass


class FakeEntra:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.codes: dict[str, dict] = {}

    async def fetch(self, url: str) -> dict:
        if url == f"{ENTRA_ISSUER}/.well-known/openid-configuration":
            return {"issuer": ENTRA_ISSUER, "jwks_uri": JWKS_URL}
        if url == JWKS_URL:
            jwk = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
            jwk.update(kid=KEY_ID, use="sig", alg="RS256")
            return {"keys": [jwk]}
        raise KeyError(url)

    def authorize(self, location: str, persona: str) -> str:
        """The persona signs in at `location` (the console's redirect to Entra). Returns the
        path and query of the console callback the browser is sent back to."""
        if persona not in PERSONAS:
            raise UnknownPersona(persona)
        params = dict(httpx.URL(location).params)
        code = secrets.token_urlsafe(24)
        self.codes[code] = {
            "persona": persona,
            "nonce": params["nonce"],
            "challenge": params["code_challenge"],
            "redirect_uri": params["redirect_uri"],
        }
        back = httpx.URL(params["redirect_uri"])
        return f"{back.path}?{urlencode({'code': code, 'state': params['state']})}"

    def _id_token(self, persona: str, nonce: str) -> str:
        now = int(time.time())
        claims = {
            "iss": ENTRA_ISSUER,
            "aud": ENTRA_CLIENT,
            "tid": ENTRA_TENANT,
            "sub": f"e2e-{persona}",
            "oid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"e2e-{persona}")),
            "email": f"{persona}@example.org",
            "groups": PERSONAS[persona],
            "nonce": nonce,
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
        }
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": KEY_ID})

    async def _token_endpoint(self, request: httpx.Request) -> httpx.Response:
        form = dict(parse_qsl((await request.aread()).decode()))
        grant = self.codes.pop(form.get("code", ""), None)
        if grant is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        verifier = form.get("code_verifier", "")
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        if (
            form.get("grant_type") != "authorization_code"
            or challenge != grant["challenge"]
            or form.get("redirect_uri") != grant["redirect_uri"]
            or form.get("client_id") != ENTRA_CLIENT
            or form.get("client_secret") != ENTRA_SECRET
        ):
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "expires_in": 3600,
                "id_token": self._id_token(grant["persona"], grant["nonce"]),
                "access_token": f"access-{secrets.token_urlsafe(16)}",
            },
        )

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._token_endpoint)
