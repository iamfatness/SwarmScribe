"""Walk every /api route with its full path, and prove each unsafe one is CSRF-checked.

FastAPI nests routers included with `include_router`, whose prefix is not part of the inner
route's own path. `api_routes` composes it, and refuses (raises) any /api route of a type it
cannot inspect, so a new kind of route can never slip past the CSRF rule unnoticed. A Mount
outside /api (the static single-page app at "/") is allowed only when nothing inside it
serves an /api path, because the walker cannot prove what a mounted sub-app guards."""

from collections.abc import Iterator

from fastapi.routing import APIRoute
from starlette.routing import BaseRoute, Mount, Route

from .deps import SAFE_METHODS, checked


class UnguardedRoute(Exception):
    """A route under /api that this module cannot prove is CSRF-protected."""


def api_routes(
    routes: list[BaseRoute], prefix: str = "", inherited: tuple = ()
) -> Iterator[tuple[str, APIRoute, tuple]]:
    """(full path, route, dependency callables declared by its include chain)."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route, inherited
        elif hasattr(route, "original_router") and hasattr(route, "include_context"):
            context = route.include_context
            router = route.original_router
            extra = tuple(d.dependency for d in (*context.dependencies, *router.dependencies))
            yield from api_routes(router.routes, prefix + context.prefix, inherited + extra)
        else:
            path = prefix + getattr(route, "path", "")
            if isinstance(route, Mount) and not path.startswith("/api"):
                inner = _api_path_inside(route, path)
                if inner is not None:
                    raise UnguardedRoute(
                        f"a Mount at {path or '/'!r} contains the /api route {inner!r}, "
                        "which cannot be proven CSRF-protected; mount it under /api or "
                        "include its router instead"
                    )
            if isinstance(route, Route) and not path.startswith("/api"):
                continue
            if path.startswith("/api") or not hasattr(route, "path"):
                raise UnguardedRoute(f"cannot inspect route {type(route).__name__} at {path!r}")


def _api_path_inside(mount: Mount, path: str) -> str | None:
    """The first /api path served inside a mount, or None. A mount holding a plain app (such
    as StaticFiles) has no routes to look at."""
    for route in getattr(mount, "routes", None) or []:
        inner = path + getattr(route, "path", "")
        if inner.startswith("/api"):
            return inner
        if isinstance(route, Mount):
            found = _api_path_inside(route, inner)
            if found is not None:
                return found
    return None


def _calls(dependant) -> Iterator:
    for sub in dependant.dependencies:
        yield sub.call
        yield from _calls(sub)


def is_guarded(route: APIRoute, inherited: tuple) -> bool:
    return checked in (*inherited, *_calls(route.dependant))


def unguarded(routes: list[BaseRoute]) -> list[str]:
    """`METHOD /full/path` for each unsafe-method /api route lacking the CSRF dependency."""
    found = []
    for path, route, inherited in api_routes(routes):
        if not path.startswith("/api") or is_guarded(route, inherited):
            continue
        found.extend(f"{m} {path}" for m in sorted(route.methods - SAFE_METHODS))
    return found


def assert_guarded(routes: list[BaseRoute]) -> None:
    missing = unguarded(routes)
    if missing:
        raise UnguardedRoute("routes without the CSRF/Person dependency: " + ", ".join(missing))
