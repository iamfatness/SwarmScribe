import importlib.util
import sys
from pathlib import Path

import httpx
import yaml
from swarmscribe_leader.app import create_app
from swarmscribe_leader.config import Settings

COMPOSE = Path(__file__).resolve().parents[3] / "e2e" / "compose"


def load_driver():
    spec = importlib.util.spec_from_file_location("compose_e2e_driver", COMPOSE / "run_e2e.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def test_the_compose_scenario_passes_against_an_in_process_leader(
    engine, migrated_database_url, tmp_path
):
    driver = load_driver()
    settings = Settings(
        database_url=migrated_database_url,
        public_url="http://leader",
        link_key="k" * 32,
        lease_seconds=2,
        heartbeat_seconds=1,
        reaper_interval_seconds=0.2,
        scanner_interval_seconds=0.2,
        claim_retry_after=1,
    )
    app = create_app(settings, background=True)
    kills: list[str] = []
    async with app.router.lifespan_context(app):
        report = await driver.run(
            base_url="http://leader",
            database_url=migrated_database_url,
            data_dir=tmp_path,
            storage_root=str(tmp_path),
            kill_replica=lambda: kills.append("leader-1"),
            transport=httpx.ASGITransport(app=app),
            work_seconds=0.05,
            timeout=90,
        )
    assert kills == ["leader-1"]
    assert report.completed == len(driver.CONSENTED)
    assert report.killed_after == 2


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
