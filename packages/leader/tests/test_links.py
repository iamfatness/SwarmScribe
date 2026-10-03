import pytest
from swarmscribe_leader.storage.links import InvalidLink, LinkClaims, LinkSigner

KEY = b"k" * 32
CLAIMS = LinkClaims(
    location_id="loc", key="talks/one.mp3", method="GET", version="10-1", expires=2_000
)


def test_a_signed_link_verifies_to_the_same_claims():
    signer = LinkSigner(KEY)
    assert signer.verify(signer.sign(CLAIMS), now=1_000) == CLAIMS


def test_the_key_must_be_at_least_32_bytes():
    with pytest.raises(ValueError):
        LinkSigner(b"short")


def test_an_expired_link_is_refused():
    signer = LinkSigner(KEY)
    with pytest.raises(InvalidLink, match="expired"):
        signer.verify(signer.sign(CLAIMS), now=2_000)


def test_a_link_signed_with_another_key_is_refused():
    token = LinkSigner(b"x" * 32).sign(CLAIMS)
    with pytest.raises(InvalidLink):
        LinkSigner(KEY).verify(token, now=1_000)


def test_a_tampered_payload_is_refused():
    signer = LinkSigner(KEY)
    token = signer.sign(CLAIMS)
    other = signer.sign(LinkClaims("loc", "secret.mp3", "GET", "", 2_000))
    forged = other.split(".")[0] + "." + token.split(".")[1]
    with pytest.raises(InvalidLink):
        signer.verify(forged, now=1_000)


@pytest.mark.parametrize("token", ["", "abc", "abc.", ".abc", "a.b.c", "é.é", "!!!.###"])
def test_malformed_tokens_are_refused(token):
    with pytest.raises(InvalidLink):
        LinkSigner(KEY).verify(token, now=1_000)


@pytest.mark.parametrize("suffix", ["=", "=="])
def test_a_token_with_padding_appended_is_refused(suffix):
    signer = LinkSigner(KEY)
    token = signer.sign(CLAIMS)
    assert signer.verify(token, now=1_000) == CLAIMS
    with pytest.raises(InvalidLink):
        signer.verify(token + suffix, now=1_000)
    payload, signature = token.split(".")
    with pytest.raises(InvalidLink):
        signer.verify(payload + "=." + signature, now=1_000)


def test_a_signature_with_non_canonical_trailing_bits_is_refused():
    signer = LinkSigner(KEY)
    payload, signature = signer.sign(CLAIMS).split(".")
    # The last base64 character of a 32-byte digest carries 2 unused bits; flip them.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    i = alphabet.index(signature[-1])
    bumped = alphabet[i // 4 * 4 + (i + 1) % 4]
    with pytest.raises(InvalidLink):
        signer.verify(payload + "." + signature[:-1] + bumped, now=1_000)
