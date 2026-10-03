import importlib.util
import itertools
import re
import sys
import uuid
from pathlib import Path

import httpx
import pytest
import yaml
from swarmscribe_leader.app import create_app
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.config import Settings
from swarmscribe_leader.db.models import JobAttempt

COMPOSE = Path(__file__).resolve().parents[3] / "e2e" / "compose"


def load_driver():
    spec = importlib.util.spec_from_file_location("compose_e2e_driver", COMPOSE / "run_e2e.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class LabelledUpstreams(httpx.AsyncBaseTransport):
    """The in-process leader behind a stand-in for the proxy: each answer names an
    upstream, in turn, as nginx's X-Upstream header does."""

    def __init__(self, app, upstreams):
        self.inner = httpx.ASGITransport(app=app)
        self.upstreams = itertools.cycle(upstreams)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        response.headers["X-Upstream"] = next(self.upstreams)
        return response


def leader_settings(database_url) -> Settings:
    return Settings(
        database_url=database_url,
        public_url="http://leader",
        link_key="k" * 32,
        lease_seconds=2,
        heartbeat_seconds=1,
        reaper_interval_seconds=0.2,
        scanner_interval_seconds=0.2,
        claim_retry_after=1,
    )


async def test_the_compose_scenario_passes_against_an_in_process_leader(
    engine, migrated_database_url, tmp_path
):
    driver = load_driver()
    app = create_app(leader_settings(migrated_database_url), background=True)
    kills: list[str] = []
    async with app.router.lifespan_context(app):
        report = await driver.run(
            base_url="http://leader",
            database_url=migrated_database_url,
            data_dir=tmp_path,
            storage_root=str(tmp_path),
            kill_replica=lambda: kills.append("leader-1"),
            transport=LabelledUpstreams(app, ["10.0.0.1:8080", "10.0.0.2:8080"]),
            replicas=2,
            work_seconds=0.05,
            timeout=90,
        )
    assert kills == ["leader-1"]
    assert report.completed == len(driver.CONSENTED)
    assert report.killed_after == 2
    assert report.upstreams_before_kill == {"10.0.0.1:8080", "10.0.0.2:8080"}


async def test_the_scenario_fails_when_one_replica_served_everything(
    engine, migrated_database_url, tmp_path
):
    driver = load_driver()
    app = create_app(leader_settings(migrated_database_url), background=True)
    kills: list[str] = []
    # A failed-over request names both upstreams; it proves neither served it alone.
    upstreams = ["10.0.0.1:8080", "10.0.0.1:8080, 10.0.0.2:8080"]
    async with app.router.lifespan_context(app):
        with pytest.raises(AssertionError, match="replica"):
            await driver.run(
                base_url="http://leader",
                database_url=migrated_database_url,
                data_dir=tmp_path,
                storage_root=str(tmp_path),
                kill_replica=lambda: kills.append("leader-1"),
                transport=LabelledUpstreams(app, upstreams),
                replicas=2,
                work_seconds=0.05,
                timeout=90,
            )
    assert kills == []  # refused before the kill


async def test_a_job_completed_by_two_attempts_is_found(sessionmaker, factory):
    driver = load_driver()
    follower, _ = await factory.follower()
    once = await factory.job(state="completed")
    twice = await factory.job(
        await factory.recording(key="talks/two.mp3"), state="completed"
    )
    async with sessionmaker() as session:
        for job, outcomes in ((once, ["expired", "completed"]), (twice, ["completed"] * 2)):
            for outcome in outcomes:
                session.add(
                    JobAttempt(
                        job_id=job.id,
                        follower_id=follower.id,
                        lease_id=uuid.uuid4(),
                        started_at=utcnow(),
                        ended_at=utcnow(),
                        outcome=outcome,
                    )
                )
        await session.commit()
    async with sessionmaker() as session:
        assert await driver.jobs_completed_more_than_once(session) == [twice.id]


def test_link_key_reads_the_key_a_leader_link_names():
    from swarmscribe_leader.storage.links import LinkClaims, LinkSigner

    token = LinkSigner(b"k" * 32).sign(
        LinkClaims(location_id="l", key="talks/a.mp3", method="GET", version="1-2", expires=9)
    )
    assert load_driver().link_key(f"http://leader/v1/files/{token}") == "talks/a.mp3"


def test_two_identical_replicas_sit_behind_a_proxy_that_logs_no_links():
    compose = yaml.safe_load((COMPOSE / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert {"postgres", "migrate", "leader-1", "leader-2", "proxy"} <= set(services)
    one, two = services["leader-1"], services["leader-2"]
    assert one["environment"] == two["environment"]
    assert one["volumes"] == two["volumes"] == ["./work/data:/data"]
    assert one["environment"]["SWARMSCRIBE_PUBLIC_URL"] == "http://localhost:8080"
    nginx = (COMPOSE / "nginx.conf").read_text(encoding="utf-8")
    assert "access_log off;" in nginx
    assert "error_log /dev/stderr crit;" in nginx


def test_the_proxy_fails_over_and_names_the_upstream_without_logging():
    nginx = (COMPOSE / "nginx.conf").read_text(encoding="utf-8")
    servers = re.findall(r"^\s*server (leader-[12]):8080 ([^;]*);", nginx, re.MULTILINE)
    assert [name for name, _ in servers] == ["leader-1", "leader-2"]
    for _, options in servers:
        assert "max_fails=1" in options
        assert "fail_timeout=" in options
    assert re.search(r"proxy_next_upstream\s+error timeout http_502 http_503;", nginx)
    assert re.search(r"add_header\s+X-Upstream\s+\$upstream_addr\s+always;", nginx)
    assert "log_format" not in nginx  # the header is never logged


def test_every_service_the_scenario_waits_for_has_a_healthcheck():
    compose = yaml.safe_load((COMPOSE / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert "pg_isready" in str(services["postgres"]["healthcheck"]["test"])
    for name in ("leader-1", "leader-2"):
        assert "/readyz" in str(services[name]["healthcheck"]["test"]), name
        assert services[name]["depends_on"]["migrate"]["condition"] == (
            "service_completed_successfully"
        )
    assert services["migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["proxy"]["depends_on"] == {
        "leader-1": {"condition": "service_healthy"},
        "leader-2": {"condition": "service_healthy"},
    }


def test_the_image_installs_dependencies_before_copying_the_source():
    root = COMPOSE.parents[1]
    lines = [
        line.strip()
        for line in (COMPOSE / "Dockerfile").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    full_copy = lines.index("COPY . .")
    first_sync = next(i for i, line in enumerate(lines) if line.startswith("RUN uv sync"))
    assert first_sync < full_copy
    assert "--no-install-workspace" in lines[first_sync]
    manifests = " ".join(line for line in lines[:first_sync] if line.startswith("COPY "))
    for manifest in ("uv.lock", "packages/protocol/pyproject.toml",
                     "packages/leader/pyproject.toml", "packages/engine/pyproject.toml"):
        assert manifest in manifests, manifest
    ignored = (root / ".dockerignore").read_text(encoding="utf-8").split()
    for entry in (".git", ".venv", ".pgdata", ".superpowers", "**/__pycache__", "*.env",
                  "**/*.env", "e2e/compose/work"):
        assert entry in ignored, entry


def test_ci_shows_every_services_logs_on_failure():
    workflow = yaml.safe_load(
        (COMPOSE.parents[1] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["compose-e2e"]["steps"]
    (logs,) = [step for step in steps if "logs" in step.get("run", "")]
    assert logs["if"] == "failure()"
    for service in ("postgres", "migrate", "leader-1", "leader-2", "proxy"):
        assert service in logs["run"].split(), service
    (scenario,) = [step for step in steps if "run_e2e.py" in step.get("run", "")]
    assert "--replicas 2" in scenario["run"]
