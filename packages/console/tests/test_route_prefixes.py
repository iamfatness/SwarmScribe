"""Every route the console serves lives under a prefix the Helm chart's Ingress knows.

The chart (deploy/helm/swarmscribe-console) lists the web app's pages, /assets, /api and /auth
as explicit Ingress paths and keeps /healthz and /readyz off the Internet. A backend route
under any other prefix would answer in tests and 404 at the ingress controller, so this test
fails first. The web app's own pages are listed in
packages/console-web/src/app/routePrefixes.json."""

from starlette.routing import Mount
from swarmscribe_console.app import create_app

ALLOWED = ("/api/", "/auth/", "/healthz", "/readyz")


def walk(routes, prefix=""):
    for route in routes:
        if hasattr(route, "original_router") and hasattr(route, "include_context"):
            yield from walk(route.original_router.routes, prefix + route.include_context.prefix)
        else:
            yield prefix + getattr(route, "path", ""), route


def test_every_route_starts_with_a_prefix_the_chart_routes(make_settings, tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html>", encoding="utf-8")
    app = create_app(make_settings(static_dir=tmp_path), background=False)
    seen = []
    for path, route in walk(app.routes):
        if isinstance(route, Mount) and path in ("", "/"):
            continue  # the web app's static files and index.html: pages and /assets
        seen.append(path)
        assert path.startswith(ALLOWED), (
            f"{path!r} is not under /api/, /auth/, /healthz or /readyz: add it to the chart's "
            "ingress.paths (values.yaml) and to ALLOWED here, or move it"
        )
    assert any(path.startswith("/api/") for path in seen)
    assert any(path.startswith("/auth/") for path in seen)
    assert "/healthz" in seen
    assert "/readyz" in seen
