"""Refuse oversized request bodies on the JSON API. File uploads have their own limit."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import error_response

MAX_API_BODY_BYTES = 1024 * 1024
FILES_PREFIX = "/v1/files/"
TOO_LARGE = "request body is larger than 1 MiB"


class _TooLarge(Exception):
    pass


class BodyLimit:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_API_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].startswith(FILES_PREFIX):
            await self.app(scope, receive, send)
            return
        refusal = error_response("too_large", TOO_LARGE, 413)
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await refusal(scope, receive, send)
            return

        received = 0
        exceeded = False
        started = False

        async def limited_receive() -> Message:
            # Bodies without a (truthful) Content-Length are counted as they arrive.
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    raise _TooLarge
            return message

        async def guarded_send(message: Message) -> None:
            # The framework answers a body it could not read with its own 400; once the limit
            # was hit, that response is replaced by the 413.
            nonlocal started
            if exceeded:
                if not started and message["type"] == "http.response.start":
                    started = True
                    await refusal(scope, receive, send)
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _TooLarge:
            if not started:
                await refusal(scope, receive, send)
