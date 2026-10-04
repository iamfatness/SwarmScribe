"""The leader actions the console proxies (fleet console spec 5.4) — an allow-list.

Each entry names the method, the leader route under /v1/admin, the console role it needs
(the leader's own role for that route, so the console refuses first: spec 7, "authorised
twice"), the audit action, the query parameters passed on, and whether it takes a body.
Nothing outside this list reaches a leader. Path parameters must be UUIDs or leader-style
names, so `..`, encoded slashes and other path tricks cannot form a leader URL; test_proxy
checks the list against the leader's own router."""

import re
from dataclasses import dataclass, field
from urllib.parse import quote

UUID_PATTERN = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
NAME_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}"
PARAMETERS = {
    "job_id": UUID_PATTERN,
    "follower_id": UUID_PATTERN,
    "token_id": UUID_PATTERN,
    "location": NAME_PATTERN,
}
_PARAM = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class ProxyRoute:
    method: str
    template: str  # relative to /v1/admin/, e.g. "jobs/{job_id}/retry"
    role: str
    action: str
    query: tuple[str, ...] = ()
    body: bool = False
    pattern: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        regex, position = "", 0
        for found in _PARAM.finditer(self.template):
            name = found.group(1)
            regex += re.escape(self.template[position : found.start()])
            regex += f"(?P<{name}>{PARAMETERS[name]})"
            position = found.end()
        regex += re.escape(self.template[position:])
        object.__setattr__(self, "pattern", re.compile(regex))

    def leader_path(self, params: dict[str, str]) -> str:
        quoted = {name: quote(value, safe="") for name, value in params.items()}
        return "/v1/admin/" + self.template.format(**quoted)


ROUTES: tuple[ProxyRoute, ...] = (
    ProxyRoute("GET", "status", "viewer", "status.view"),
    ProxyRoute("GET", "locations", "viewer", "locations.view"),
    ProxyRoute("GET", "jobs", "viewer", "jobs.view", query=("state", "location", "limit")),
    ProxyRoute("GET", "followers", "viewer", "followers.view", query=("state",)),
    ProxyRoute("GET", "tokens", "admin", "tokens.view"),
    ProxyRoute("GET", "consent/report", "viewer", "consent.view", query=("location", "limit")),
    ProxyRoute("POST", "locations", "admin", "locations.add", body=True),
    ProxyRoute("POST", "locations/{location}/enable", "admin", "locations.enable"),
    ProxyRoute("POST", "locations/{location}/disable", "admin", "locations.disable"),
    ProxyRoute("POST", "locations/{location}/ingest", "operator", "locations.ingest"),
    ProxyRoute("POST", "jobs/{job_id}/retry", "operator", "jobs.retry"),
    ProxyRoute("POST", "jobs/{job_id}/cancel", "operator", "jobs.cancel"),
    ProxyRoute("POST", "jobs/{job_id}/priority", "operator", "jobs.priority", body=True),
    ProxyRoute("POST", "followers/{follower_id}/drain", "operator", "followers.drain"),
    ProxyRoute("POST", "followers/{follower_id}/revoke", "admin", "followers.revoke"),
    ProxyRoute("POST", "tokens", "admin", "tokens.create", body=True),
    ProxyRoute("POST", "tokens/{token_id}/revoke", "admin", "tokens.revoke"),
)


def match_route(method: str, rest: str) -> tuple[ProxyRoute, dict[str, str]] | None:
    for route in ROUTES:
        if route.method == method:
            found = route.pattern.fullmatch(rest)
            if found is not None:
                return route, found.groupdict()
    return None


def target_of(params: dict[str, str]) -> str | None:
    return ", ".join(f"{name}={value}" for name, value in sorted(params.items())) or None
