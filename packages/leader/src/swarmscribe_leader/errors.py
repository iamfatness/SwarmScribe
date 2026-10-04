class LeaderError(Exception):
    """An expected failure with an HTTP status and a stable error code."""

    status = 400
    code = "bad_request"
    retry_after: int | None = None

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class Unauthorized(LeaderError):
    """No usable credential was sent. Answered with `WWW-Authenticate: <scheme>`: Bearer,
    or Console when a console credential was refused."""

    status = 401
    code = "unauthorized"
    scheme = "Bearer"
    bearer_error: str | None = None


class InvalidToken(Unauthorized):
    """A credential was sent but is not valid (malformed, forged, expired, revoked).
    Answered with `WWW-Authenticate: Bearer error="invalid_token"` (RFC 6750)."""

    bearer_error = "invalid_token"


class Forbidden(LeaderError):
    status = 403
    code = "forbidden"


class NotFound(LeaderError):
    status = 404
    code = "not_found"


class Conflict(LeaderError):
    status = 409
    code = "conflict"


class StaleLease(Conflict):
    code = "stale_lease"


class PreconditionFailed(LeaderError):
    status = 412
    code = "source_changed"


class PayloadTooLarge(LeaderError):
    status = 413
    code = "too_large"


class ServiceUnavailable(LeaderError):
    """Something the leader depends on (an identity provider, a group directory) cannot be
    reached right now; the caller should retry."""

    status = 503
    code = "unavailable"
    retry_after = 10
