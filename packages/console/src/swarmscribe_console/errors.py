"""Expected failures with an HTTP status and a stable code. Messages are fixed text or name
only what the caller sent in a checked form; they never carry secrets."""


class ConsoleError(Exception):
    status = 400
    code = "bad_request"
    retry_after: int | None = None

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class Unauthenticated(ConsoleError):
    """No valid session. `clear_cookie` asks the handler to delete the browser's cookie."""

    status = 401
    code = "unauthenticated"

    def __init__(self, message: str, *, clear_cookie: bool = False):
        super().__init__(message)
        self.clear_cookie = clear_cookie


class Forbidden(ConsoleError):
    status = 403
    code = "forbidden"


class CsrfRejected(Forbidden):
    code = "csrf_failed"


class NotFound(ConsoleError):
    status = 404
    code = "not_found"


class Conflict(ConsoleError):
    status = 409
    code = "conflict"


class Invalid(ConsoleError):
    status = 422
    code = "invalid_request"


class Unavailable(ConsoleError):
    """Something the console depends on (its database, an identity provider) is down."""

    status = 503
    code = "unavailable"
    retry_after = 10


class PayloadTooLarge(ConsoleError):
    status = 413
    code = "too_large"


class BadGateway(ConsoleError):
    """A leader answered something the console cannot pass on."""

    status = 502
    code = "bad_gateway"


class CredentialRejected(ConsoleError):
    """The leader does not know the stored credential (a 401 that is not "revoked")."""

    status = 502
    code = "leader_credential_rejected"


class LeaderUnavailable(Unavailable):
    status = 503
    code = "leader_unreachable"
    retry_after = 15


class CredentialRevoked(ConsoleError):
    """The leader revoked this console's credential; a console administrator must replace
    it (PUT /api/admin/leaders/{name}/credential)."""

    status = 503
    code = "leader_credential_revoked"


class CredentialUnreadableError(ConsoleError):
    status = 503
    code = "leader_credential_unreadable"


class PassedThrough(ConsoleError):
    """A leader's own error, passed to the browser with its status and code."""

    def __init__(self, status: int, code: str, message: str, retry_after: int | None = None):
        super().__init__(message, code=code)
        self.status = status
        self.retry_after = retry_after
