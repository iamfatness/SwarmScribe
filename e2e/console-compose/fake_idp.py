"""Microsoft Entra ID as the console and the leaders see it, for the console Compose test only.

The console, the leaders and `swarmscribe-admin` all use fixed Entra addresses on
https://login.microsoftonline.com, and the admin CLI refuses any other host. So the test
changes nothing in them: inside the Compose network this server holds the network alias
`login.microsoftonline.com` and a certificate for that name from the test's own CA
(work/certs, trusted through SSL_CERT_FILE). Nothing here is part of any image or package.

It serves one tenant:

- the discovery document and the signing keys;
- the authorization endpoint: instead of a sign-in page it signs in the persona named by
  `login_hint` at once and redirects back with a code;
- the device authorization endpoint (`swarmscribe-admin login`): the device code is approved
  at once, as the persona DEVICE_PERSONA;
- the token endpoint: authorization code with PKCE and the client secret (the console),
  and the device-code grant (the admin CLI).

Run inside the Compose network (docker-compose.yml does):

    python /e2e/fake_idp.py
"""

import base64
import hashlib
import json
import os
import secrets
import time
import uuid
from urllib.parse import parse_qsl, urlencode

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

AUTHORITY = "https://login.microsoftonline.com"
KEY_ID = "console-compose-key-1"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

# Entra group object ids. docker-compose.yml repeats CONSOLE_ADMINS (the console's bootstrap
# administrator) and LEADER_ADMINS (the leaders' admin role); keep them the same.
CONSOLE_ADMINS = "c0c0c0c0-0000-4000-8000-000000000001"
FLEET_ADMINS = "c0c0c0c0-0000-4000-8000-000000000002"
LEADER_ADMINS = "c0c0c0c0-0000-4000-8000-000000000003"
PERSONAS: dict[str, list[str]] = {
    "console-admin": [CONSOLE_ADMINS, FLEET_ADMINS],
    "leader-admin": [LEADER_ADMINS],
    "stranger": [],
}
DEVICE_PERSONA = "leader-admin"


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _error(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


async def _form(request: Request) -> dict[str, str]:
    """The urlencoded body (Starlette's own form parsing needs python-multipart, which the
    leader image this runs in does not carry)."""
    return dict(parse_qsl((await request.body()).decode()))


def create_app(
    *,
    tenant: str,
    console_client_id: str,
    console_client_secret: str,
    console_redirect_uri: str,
    leader_client_id: str,
) -> Starlette:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = f"{AUTHORITY}/{tenant}/v2.0"
    jwks_uri = f"{AUTHORITY}/{tenant}/discovery/v2.0/keys"
    codes: dict[str, dict[str, str]] = {}
    device_codes: dict[str, str] = {}

    def id_token(persona: str, client_id: str, nonce: str | None) -> str:
        now = int(time.time())
        claims = {
            "iss": issuer,
            "aud": client_id,
            "tid": tenant,
            "sub": f"compose-{persona}",
            "oid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"compose-{persona}")),
            "email": f"{persona}@example.org",
            "groups": PERSONAS[persona],
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
        }
        if nonce is not None:
            claims["nonce"] = nonce
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": KEY_ID})

    def tokens(persona: str, client_id: str, nonce: str | None) -> JSONResponse:
        return JSONResponse(
            {
                "token_type": "Bearer",
                "expires_in": 3600,
                "id_token": id_token(persona, client_id, nonce),
                "access_token": f"access-{secrets.token_urlsafe(16)}",
                "refresh_token": f"refresh-{secrets.token_urlsafe(16)}",
            }
        )

    async def healthz(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def discovery(_request: Request) -> JSONResponse:
        return JSONResponse({"issuer": issuer, "jwks_uri": jwks_uri})

    async def keys(_request: Request) -> JSONResponse:
        jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
        jwk.update(kid=KEY_ID, use="sig", alg="RS256")
        return JSONResponse({"keys": [jwk]})

    async def authorize(request: Request) -> JSONResponse | RedirectResponse:
        query = request.query_params
        persona = query.get("login_hint", "")
        if (
            query.get("client_id") != console_client_id
            or query.get("redirect_uri") != console_redirect_uri
            or query.get("response_type") != "code"
            or query.get("code_challenge_method") != "S256"
            or not query.get("code_challenge")
            or not query.get("state")
            or not query.get("nonce")
        ):
            return _error("invalid_request")
        if persona not in PERSONAS:
            return _error("unknown_persona")
        code = secrets.token_urlsafe(24)
        codes[code] = {
            "persona": persona,
            "nonce": query["nonce"],
            "challenge": query["code_challenge"],
        }
        back = urlencode({"code": code, "state": query["state"]})
        return RedirectResponse(f"{console_redirect_uri}?{back}", status_code=302)

    async def device_authorization(request: Request) -> JSONResponse:
        form = await _form(request)
        if form.get("client_id") != leader_client_id:
            return _error("invalid_client")
        device_code = secrets.token_urlsafe(32)
        device_codes[device_code] = DEVICE_PERSONA
        return JSONResponse(
            {
                "device_code": device_code,
                "user_code": "COMPOSE",
                "verification_uri": f"{AUTHORITY}/device",
                "expires_in": 300,
                "interval": 1,
            }
        )

    async def token(request: Request) -> JSONResponse:
        form = await _form(request)
        grant_type = form.get("grant_type")
        if grant_type == "authorization_code":
            grant = codes.pop(form.get("code", ""), None)
            if (
                grant is None
                or form.get("client_id") != console_client_id
                or form.get("client_secret") != console_client_secret
                or form.get("redirect_uri") != console_redirect_uri
                or _challenge(form.get("code_verifier", "")) != grant["challenge"]
            ):
                return _error("invalid_grant")
            return tokens(grant["persona"], console_client_id, grant["nonce"])
        if grant_type == DEVICE_GRANT:
            persona = device_codes.pop(form.get("device_code", ""), None)
            if persona is None or form.get("client_id") != leader_client_id:
                return _error("invalid_grant")
            return tokens(persona, leader_client_id, None)
        return _error("unsupported_grant_type")

    return Starlette(
        routes=[
            Route("/healthz", healthz),
            Route(f"/{tenant}/v2.0/.well-known/openid-configuration", discovery),
            Route(f"/{tenant}/discovery/v2.0/keys", keys),
            Route(f"/{tenant}/oauth2/v2.0/authorize", authorize),
            Route(f"/{tenant}/oauth2/v2.0/devicecode", device_authorization, methods=["POST"]),
            Route(f"/{tenant}/oauth2/v2.0/token", token, methods=["POST"]),
        ]
    )


def main() -> None:
    env = os.environ
    app = create_app(
        tenant=env["FAKE_IDP_TENANT"],
        console_client_id=env["FAKE_IDP_CONSOLE_CLIENT_ID"],
        console_client_secret=env["FAKE_IDP_CONSOLE_CLIENT_SECRET"],
        console_redirect_uri=env["FAKE_IDP_CONSOLE_REDIRECT_URI"],
        leader_client_id=env["FAKE_IDP_LEADER_CLIENT_ID"],
    )
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=443,
        ssl_certfile="/certs/server.pem",
        ssl_keyfile="/certs/server.key",
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
