import os

import pytest
from swarmscribe_console.crypto import ConsoleKeys, CredentialUnreadable

MASTER = bytes(range(32))
CREDENTIAL = "q" * 21 + "Z_-" + "x" * 19  # 43 URL-safe characters, like C1's credentials


@pytest.fixture
def keys():
    return ConsoleKeys(MASTER)


def test_a_sealed_credential_opens_to_the_same_value(keys):
    sealed = keys.seal_credential("eu-1", CREDENTIAL)
    assert keys.open_credential("eu-1", sealed) == CREDENTIAL


def test_the_sealed_form_does_not_contain_the_credential(keys):
    sealed = keys.seal_credential("eu-1", CREDENTIAL)
    assert CREDENTIAL.encode() not in sealed
    assert sealed[:1] == b"\x01"
    assert len(sealed) == 1 + 12 + len(CREDENTIAL) + 16


def test_every_seal_uses_a_fresh_nonce(keys):
    assert keys.seal_credential("eu-1", CREDENTIAL) != keys.seal_credential("eu-1", CREDENTIAL)


def test_the_leader_name_is_bound_to_the_ciphertext_ignoring_case(keys):
    sealed = keys.seal_credential("EU-1", CREDENTIAL)
    assert keys.open_credential("eu-1", sealed) == CREDENTIAL
    with pytest.raises(CredentialUnreadable):
        keys.open_credential("us-1", sealed)


@pytest.mark.parametrize(
    "spoil",
    [
        lambda s: s[:-1] + bytes([s[-1] ^ 1]),
        lambda s: s[:20] + bytes([s[20] ^ 1]) + s[21:],
        lambda s: s[:5],
        lambda s: b"\x02" + s[1:],
        lambda s: b"",
    ],
    ids=["tag-flipped", "ciphertext-flipped", "truncated", "unknown-format", "empty"],
)
def test_a_tampered_credential_does_not_open(keys, spoil):
    sealed = keys.seal_credential("eu-1", CREDENTIAL)
    with pytest.raises(CredentialUnreadable):
        keys.open_credential("eu-1", spoil(sealed))


def test_another_key_cannot_open_it(keys):
    sealed = keys.seal_credential("eu-1", CREDENTIAL)
    with pytest.raises(CredentialUnreadable):
        ConsoleKeys(os.urandom(32)).open_credential("eu-1", sealed)


def test_the_master_key_must_be_32_bytes():
    with pytest.raises(ValueError):
        ConsoleKeys(bytes(31))


def test_the_csrf_token_is_fixed_per_session_and_differs_between_sessions(keys):
    first = keys.csrf_token("session-one")
    assert first == keys.csrf_token("session-one")
    assert first != keys.csrf_token("session-two")
    assert first != ConsoleKeys(os.urandom(32)).csrf_token("session-one")
    assert len(first) == 43


@pytest.mark.parametrize("presented", [None, "", "x" * 43, "é" * 43])
def test_only_the_sessions_own_token_matches(keys, presented):
    assert keys.csrf_matches("session-one", keys.csrf_token("session-one"))
    assert not keys.csrf_matches("session-one", presented)
    assert not keys.csrf_matches("session-one", keys.csrf_token("session-two"))
