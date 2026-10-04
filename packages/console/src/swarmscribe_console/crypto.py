"""What the console keeps secret with its own key (fleet console spec 5.1 and 5.2).

SWARMSCRIBE_CONSOLE_KEY is 32 random bytes. HKDF-SHA256 derives two independent keys from it:
one seals leader credentials with AES-GCM, one makes each session's CSRF token (an HMAC of
the session id, so it is never stored). A sealed credential is `0x01 || nonce || ciphertext`;
its associated data is the sealing context the caller passes (leaders.sealing_context: the
leader's name and base URL), so a credential copied onto another leader's row, or left on a
row whose URL was changed in the database, does not open. The context is case-folded
(lowercased) as a whole before it is used."""

import base64
import hashlib
import hmac
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

FORMAT_V1 = b"\x01"
NONCE_BYTES = 12
TAG_BYTES = 16


class CredentialUnreadable(Exception):
    """A stored credential this key cannot open: another key, another leader's row or URL,
    or a tampered value. The message never contains the credential."""


def _derive(master: bytes, purpose: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"swarmscribe-console/" + purpose,
    ).derive(master)


def _associated_data(context: str) -> bytes:
    return b"swarmscribe-console/leader:" + context.lower().encode("ascii")


class ConsoleKeys:
    def __init__(self, master: bytes):
        if len(master) != 32:
            raise ValueError("the console key must be exactly 32 bytes")
        self._aead = AESGCM(_derive(master, b"leader-credentials/v1"))
        self._csrf = _derive(master, b"csrf/v1")

    def seal_credential(self, context: str, credential: str) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._aead.encrypt(
            nonce, credential.encode("ascii"), _associated_data(context)
        )
        return FORMAT_V1 + nonce + sealed

    def open_credential(self, context: str, sealed: bytes) -> str:
        if len(sealed) <= 1 + NONCE_BYTES + TAG_BYTES or sealed[:1] != FORMAT_V1:
            raise CredentialUnreadable("the stored leader credential is not in a known format")
        nonce, body = sealed[1 : 1 + NONCE_BYTES], sealed[1 + NONCE_BYTES :]
        try:
            plain = self._aead.decrypt(nonce, body, _associated_data(context))
        except InvalidTag:
            raise CredentialUnreadable(
                "the stored leader credential does not belong to this leader/URL, "
                "or was sealed with another console key"
            ) from None
        return plain.decode("ascii")

    def csrf_token(self, session_id: str) -> str:
        mac = hmac.new(self._csrf, session_id.encode("ascii"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")

    def csrf_matches(self, session_id: str, presented: str | None) -> bool:
        if not presented:
            return False
        expected = self.csrf_token(session_id).encode("ascii")
        return hmac.compare_digest(expected, presented.encode("utf-8", "replace"))
