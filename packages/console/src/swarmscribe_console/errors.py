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
