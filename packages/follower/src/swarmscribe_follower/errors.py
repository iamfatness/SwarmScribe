"""How the follower process ends. Service managers act on these codes (follower spec 4.1)."""

EXIT_OK = 0
EXIT_CONFIGURATION = 2  # invalid settings, a locked state folder, a scratch folder not ours
EXIT_UNFIT = 3  # this machine cannot do the work: device, GPU libraries, model
EXIT_UNAUTHORISED = 4  # no or invalid join token, credential revoked: do not restart blindly
EXIT_PROTOCOL = 5  # the leader speaks another protocol version: do not restart blindly


class FollowerExit(Exception):
    """The follower must stop, with this exit code and this one-line reason. The reason is
    printed and logged, so it never holds a token, a credential or a link."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
