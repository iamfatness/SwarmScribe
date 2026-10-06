import base64
import hashlib
import hmac
import json
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from swarmscribe_leader.auth.oidc import (
    READY_RETRY_SECONDS,
    MetadataUnavailable,
    TokenVerifier,
    login_providers,
    providers_from,
)
from swarmscribe_leader.errors import Unauthorized


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def verifier(idp, sign_in_settings, clock):
    return TokenVerifier(providers_from(sign_in_settings()), fetch=idp.fetch, clock=clock)


async def test_an_entra_token_gives_its_identity(verifier, idp):
    identity = await verifier.verify(idp.entra(email="Person@Example.org"))
    assert (identity.provider, identity.issuer, identity.subject, identity.email) == (
        "entra",
        idp.ENTRA_ISSUER,
        "entra-person-1",
        "person@example.org",
    )
    assert identity.actor == f"person@example.org ({idp.ENTRA_ISSUER} entra-person-1)"


@pytest.mark.parametrize("issuer", ["https://accounts.google.com", "accounts.google.com"])
async def test_a_google_token_gives_its_identity_under_either_issuer_spelling(
    verifier, idp, issuer
):
    identity = await verifier.verify(idp.google(iss=issuer))
    assert (identity.provider, identity.issuer, identity.subject) == (
        "google",
        "https://accounts.google.com",
        "google-person-1",
    )


async def test_an_expired_token_is_refused_as_expired(verifier, idp):
    with pytest.raises(Unauthorized) as excinfo:
        await verifier.verify(idp.entra(lifetime=-120))
    assert excinfo.value.code == "token_expired"


async def test_sixty_seconds_of_clock_skew_are_allowed(verifier, idp):
    assert await verifier.verify(idp.entra(lifetime=-30))
    now = int(time.time())
    assert await verifier.verify(idp.google(nbf=now + 30))
    with pytest.raises(Unauthorized):
        await verifier.verify(idp.google(nbf=now + 120))


@pytest.mark.parametrize(
    "make_token",
    [
        lambda idp: idp.entra(aud="another-client"),
        lambda idp: idp.google(aud=idp.ENTRA_CLIENT),
        lambda idp: idp.entra(iss="https://login.microsoftonline.com/another/v2.0"),
        lambda idp: idp.entra(tid="ffffffff-0000-4000-8000-000000000000"),
        lambda idp: idp.entra(signed_with="rogue"),
        lambda idp: idp.google(email_verified=False),
        lambda idp: idp.google(email_verified=None),
        lambda idp: idp.google(email_verified=1),
        lambda idp: idp.google(email_verified="True"),
        lambda idp: idp.entra(aud=[idp.ENTRA_CLIENT, "another-client"]),
        lambda idp: idp.entra(aud=[idp.ENTRA_CLIENT]),
        lambda idp: idp.entra(exp=None),
        lambda idp: idp.entra(sub=None),
    ],
    ids=[
        "wrong-audience",
        "other-providers-audience",
        "unknown-issuer",
        "other-tenant",
        "forged-signature",
        "unverified-email",
        "no-email-verified-claim",
        "email-verified-integer-1",
        "email-verified-capitalised",
        "audience-list-with-our-client",
        "audience-list-of-only-our-client",
        "no-expiry",
        "no-subject",
    ],
)
async def test_tokens_that_fail_a_check_are_refused(verifier, idp, make_token):
    with pytest.raises(Unauthorized):
        await verifier.verify(make_token(idp))


async def test_only_rs256_is_accepted(verifier, idp):
    forged = pyjwt.encode(
        {"iss": idp.ENTRA_ISSUER, "aud": idp.ENTRA_CLIENT, "sub": "x", "exp": 9999999999},
        "a-shared-secret-of-thirty-two-bytes!!",
        algorithm="HS256",
        headers={"kid": "entra-key-1"},
    )
    with pytest.raises(Unauthorized, match="RS256"):
        await verifier.verify(forged)


@pytest.mark.parametrize("token", ["", "not-a-token", "a.b.c", "x" * 20000])
async def test_malformed_or_oversized_tokens_are_refused(verifier, token):
    with pytest.raises(Unauthorized):
        await verifier.verify(token)


async def test_a_configured_hosted_domain_is_required(idp, sign_in_settings, clock):
    settings = sign_in_settings(google_hosted_domain="example.org")
    verifier = TokenVerifier(providers_from(settings), fetch=idp.fetch, clock=clock)
    assert await verifier.verify(idp.google(hd="example.org"))
    with pytest.raises(Unauthorized, match="hosted domain"):
        await verifier.verify(idp.google(hd="elsewhere.example"))
    with pytest.raises(Unauthorized, match="hosted domain"):
        await verifier.verify(idp.google())


async def test_keys_are_cached_and_refreshed_for_a_new_key_id(verifier, idp, clock):
    await verifier.verify(idp.entra())
    fetches = len(idp.fetched)
    await verifier.verify(idp.entra())
    assert len(idp.fetched) == fetches  # cached
    idp.rotate("entra", "rogue", "entra-key-2")
    clock.now += 61
    assert await verifier.verify(idp.entra())
    assert len(idp.fetched) == fetches + 1  # the keys again; discovery stays cached


async def test_unknown_key_ids_refresh_the_keys_at_most_once_a_minute(verifier, idp, clock):
    await verifier.verify(idp.entra())
    fetches = len(idp.fetched)
    for _ in range(5):
        with pytest.raises(Unauthorized, match="unknown key"):
            await verifier.verify(idp.entra(kid="made-up"))
    assert len(idp.fetched) == fetches


async def test_a_provider_that_cannot_be_reached_is_unavailable_not_a_refusal(verifier, idp):
    idp.down = True
    with pytest.raises(MetadataUnavailable):
        await verifier.verify(idp.google())


async def test_cached_keys_keep_working_while_the_provider_is_down(verifier, idp, clock):
    await verifier.verify(idp.entra())
    idp.down = True
    clock.now += 2 * 86400  # the keys are stale, the refresh fails
    assert await verifier.verify(idp.entra())


async def test_ready_after_the_metadata_was_fetched_once(verifier, idp, clock):
    idp.down = True
    assert await verifier.ready() is False
    idp.down = False
    assert await verifier.ready() is False  # retried no more than every 5 seconds
    clock.now += 6
    assert await verifier.ready() is True


async def test_a_leader_without_providers_refuses_every_token_and_is_ready(idp, clock):
    verifier = TokenVerifier((), fetch=idp.fetch, clock=clock)
    with pytest.raises(Unauthorized, match="not configured"):
        await verifier.verify(idp.entra())
    assert await verifier.ready() is True


def test_login_providers_give_the_cli_what_it_needs_and_no_entra_secret(sign_in_settings, idp):
    providers = {p["name"]: p for p in login_providers(sign_in_settings())}
    entra, google = providers["entra"], providers["google"]
    base = f"https://login.microsoftonline.com/{idp.ENTRA_TENANT}/oauth2/v2.0"
    assert entra == {
        "name": "entra",
        "client_id": idp.ENTRA_CLIENT,
        "device_authorization_endpoint": f"{base}/devicecode",
        "token_endpoint": f"{base}/token",
        "scope": "openid profile email offline_access",
        "client_secret": None,
    }
    assert google["client_secret"] == "google-device-secret-value"
    assert google["device_authorization_endpoint"] == "https://oauth2.googleapis.com/device/code"
    assert "entra-app-secret-value" not in str(providers)


async def test_an_unsigned_token_is_refused(verifier, idp):
    unsigned = pyjwt.encode(
        {"iss": idp.ENTRA_ISSUER, "aud": idp.ENTRA_CLIENT, "sub": "x", "exp": 9999999999},
        None,
        algorithm="none",
        headers={"kid": "entra-key-1"},
    )
    with pytest.raises(Unauthorized, match="RS256"):
        await verifier.verify(unsigned)


async def test_the_public_key_used_as_an_hmac_secret_is_refused(verifier, idp):
    public_pem = (
        idp.keys["entra"]
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    # PyJWT itself refuses to HMAC-sign with a PEM key, so build the token by hand.
    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    head = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "entra-key-1"}).encode())
    body = b64(
        json.dumps(
            {"iss": idp.ENTRA_ISSUER, "aud": idp.ENTRA_CLIENT, "sub": "x", "exp": 9999999999}
        ).encode()
    )
    signing_input = head + b"." + body
    signature = b64(hmac.new(public_pem, signing_input, hashlib.sha256).digest())
    with pytest.raises(Unauthorized, match="RS256"):
        await verifier.verify((signing_input + b"." + signature).decode())


@pytest.mark.parametrize(
    "broken",
    [
        {"e": "AQAB"},  # no modulus: InvalidKeyError
        {"n": 5, "e": "AQAB"},  # not text: TypeError
        {"n": "AQ", "e": "AQAB"},  # a modulus of 1: ValueError
    ],
    ids=["no-modulus", "modulus-not-text", "modulus-too-small"],
)
async def test_one_malformed_signing_key_does_not_spoil_the_rest(
    idp, sign_in_settings, clock, broken
):
    broken = {"kty": "RSA", "kid": "broken-key", "use": "sig", **broken}

    async def fetch(url):
        document = await idp.fetch(url)
        if "keys" in document:
            document = {"keys": [broken, *document["keys"]]}
        return document

    verifier = TokenVerifier(providers_from(sign_in_settings()), fetch=fetch, clock=clock)
    assert await verifier.verify(idp.entra())
    with pytest.raises(Unauthorized, match="unknown key"):
        await verifier.verify(idp.entra(kid="broken-key"))


async def test_a_provider_that_comes_back_is_retried_soon_while_no_keys_were_ever_fetched(
    verifier, idp, clock
):
    idp.down = True
    with pytest.raises(MetadataUnavailable):
        await verifier.verify(idp.entra())
    idp.down = False
    clock.now += READY_RETRY_SECONDS + 1  # well inside a minute
    assert await verifier.verify(idp.entra())


@pytest.mark.parametrize("header", ["jku", "x5u"])
async def test_key_location_headers_are_never_followed(verifier, idp, header):
    elsewhere = "https://keys.elsewhere.example/jwks.json"
    good = idp.entra(extra_headers={header: elsewhere})
    assert await verifier.verify(good)  # validated against the configured keys only
    forged = idp.entra(signed_with="rogue", kid="rogue-key", extra_headers={header: elsewhere})
    with pytest.raises(Unauthorized):
        await verifier.verify(forged)
    assert elsewhere not in idp.fetched
    assert set(idp.fetched) <= set(idp._documents())  # discovery and JWKS documents only


@pytest.mark.parametrize(
    "make_token",
    [
        # Google's issuer, signed with Entra's key under Entra's key id.
        lambda idp: idp.google(signed_with="entra", kid="entra-key-1"),
        # Entra's issuer and audience, signed with Google's key under Google's key id.
        lambda idp: idp.entra(signed_with="google", kid="google-key-1"),
    ],
    ids=["google-issuer-entra-key", "entra-issuer-google-key"],
)
async def test_one_providers_key_never_validates_the_others_tokens(verifier, idp, make_token):
    await verifier.verify(idp.entra())  # both key sets are loaded
    await verifier.verify(idp.google())
    with pytest.raises(Unauthorized, match="unknown key"):
        await verifier.verify(make_token(idp))


async def test_callers_waiting_for_a_silent_provider_share_one_attempt(
    idp, sign_in_settings, clock
):
    """A provider that does not answer (an egress rule that drops, say): the callers queued
    behind the one fetch are told "unavailable" when it gives up; they do not each wait for
    a fetch of their own, one after another."""
    import asyncio

    attempts = []

    async def silent(url):
        attempts.append(url)
        await asyncio.sleep(0.05)
        clock.now += 10  # as long as the fetch took to give up; longer than the retry gap
        raise OSError("timed out")

    verifier = TokenVerifier(providers_from(sign_in_settings()), fetch=silent, clock=clock)
    answers = await asyncio.gather(
        *(verifier.verify(idp.entra()) for _ in range(3)), return_exceptions=True
    )
    assert [type(answer) for answer in answers] == [MetadataUnavailable] * 3
    assert len(attempts) == 1
    # ... and the next attempt is due one retry gap after that one ended.
    clock.now += READY_RETRY_SECONDS + 1
    with pytest.raises(MetadataUnavailable):
        await verifier.verify(idp.entra())
    assert len(attempts) == 2


async def test_the_fetch_gives_up_on_a_connection_after_three_seconds(monkeypatch):
    from swarmscribe_leader.auth import oidc

    timeouts = []

    class Recorded:
        def __init__(self, *, timeout):
            timeouts.append(timeout)

        async def __aenter__(self):
            raise OSError("stop here")

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(oidc.httpx, "AsyncClient", Recorded)
    with pytest.raises(OSError):
        await oidc.http_fetch("https://provider.invalid/keys")
    (timeout,) = timeouts
    assert (timeout.connect, timeout.read) == (3.0, 10.0)
