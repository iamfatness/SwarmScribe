import hashlib
import secrets


def new_secret() -> str:
    """32 random bytes, URL-safe base64. Shown once; only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()
