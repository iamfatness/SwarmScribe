# Fleet Console C4a — Console Image and Compose Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `swarmscribe-console` container image (backend serving the built web app) and prove it in GitHub Actions with a Compose test of the console and two real leaders, one of which is killed.

**Architecture:** Three small console changes make the image deployable: probe endpoints, a CA file for leaders on a private CA, and a cap on pending sign-ins. A multi-stage Dockerfile builds the web app with Node, installs the console with `uv`, and leaves a non-root, read-only-capable image without engine libraries. The Compose test runs that image against two real leaders and a stand-in for Entra ID that is reached at Entra's real host name through a network alias and a test CA, so no test seam exists in any production code path.

**Tech Stack:** Python 3.12 (FastAPI, httpx 0.28, SQLAlchemy 2, `cryptography`), uv 0.12, Node 24.15 (Vite), Docker and Docker Compose (GitHub Actions only), Postgres 16, pytest.

**Spec:** `docs/superpowers/specs/2026-10-03-fleet-console-design.md` (sections 1, 5.3, 7, 8, 9), with `docs/superpowers/specs/2026-10-02-swarmscribe-architecture-design.md` (sections 4, 10, 11, 12) for how images are meant to look across the project.

**This plan is the first of two.** C4b (`2026-10-04-fleet-console-c4b-helm-chart.md`) is the Helm chart and the deployment guide; it needs this plan's image, `/healthz`, `/readyz` and `SWARMSCRIBE_CONSOLE_LEADER_CA_FILE`. C4a works on its own: after it, the console can be built, run with `docker run`, and is proven by the Compose test.

## Global Constraints

- The image is named `swarmscribe-console` and is "backend serving the built web app" (fleet console spec 8). `SWARMSCRIBE_CONSOLE_STATIC_DIR` points at the copied `dist/`.
- The image carries no engine, model or GPU libraries (master spec 4: "the leader image carries no model or GPU libraries"; the console imports `swarmscribe_leader` and `swarmscribe_protocol` as libraries and nothing else internal).
- Secrets are never baked into an image (master spec 10). Every value in `e2e/console-compose/` is test-only and says so.
- "TLS everywhere; leader URLs must be https" (fleet console spec 7). The Compose leaders therefore serve TLS; nothing relaxes the console's `https://` rule.
- "Logs never contain cookies, CSRF tokens, leader credentials, join tokens or link URLs" (spec 7). The test driver prints none of them either.
- "A leader that stops answering is shown as unreachable within one minute; nothing else in the console stops working" (spec 1). The Compose test runs the poller's default settings: every 15 s, timeout 5 s, three consecutive failures (spec 5.3).
- "Leaders never share a database with" the console (spec 1): three databases in the Compose test.
- **No test seam in production paths.** `create_app`'s `fetch`, `idp_transport` and `leader_transport` parameters stay test-only keyword arguments that `serve` never passes. Nothing in this plan adds an environment variable, flag or code branch that exists for tests.
- Health endpoints follow the master spec's section 11 names: `/healthz` (process alive) and `/readyz` (database reachable, migrations current).
- Python `>=3.11`; `ruff` line length 100, rules `E, F, I, UP, B`; Node `>=24.15 <25` (`.npmrc` has `engine-strict`); the console's Postgres is 14 or later.
- **Docker is not available on the Windows development machine.** Every Docker step in this plan is verified in GitHub Actions; the steps say so, and say what can be run locally instead.
- On the Windows machine, `uv` is run as `python -m uv` (for example `python -m uv run pytest ...`). The commands below are written `uv run ...`.
- Other work happens in this repository at the same time. Start from an up-to-date `main` that has C3a merged, on a new branch `fleet-console-c4a`. C3b touches `packages/console-web`, `README.md` and `.github/workflows/ci.yml`; expect to merge, and never discard a hunk you did not write.

## Rulings

Decisions this plan makes. Those marked **(owner)** are the owner's to overturn; each has the plan's recommendation built in, and the plan says what changes if the answer is different.

1. **Dockerfile location: `docker/console.Dockerfile`.** The master spec's layout puts Dockerfiles in `docker/`. (The leader's only Dockerfile so far is the test one in `e2e/compose/`; it is reused as it is and not moved.)
2. **Base images are pinned by tag and digest**: `node:24.15.0-bookworm-slim@sha256:4e6b70dd…` and `python:3.12-slim-bookworm@sha256:54c85f3c…` (looked up on Docker Hub on 2026-10-04). The tag says what it is; the digest makes the build repeatable. `uv` is pinned to `0.12.22`.
3. **Entrypoint `swarmscribe-console`, default command `serve --host 0.0.0.0 --port 8080`.** `migrate` and `admins add` are the same image with other arguments, run as a separate job (Compose: one-shot services; Helm, in C4b: a hook Job). No init container: two replicas starting at once must not both run Alembic.
4. **Non-root user 10001:10001, nothing written at run time.** The Compose test runs the console with `read_only: true`, all capabilities dropped and only a tmpfs `/tmp`.
5. **`/readyz` is ready when the database's schema is this console's *or newer*.** After a pre-upgrade migration the old replicas must stay in service until they are replaced; reporting "not ready" would take every old replica out at once. `serve` still refuses to *start* on a database that is ahead. **(owner)**
6. **`/readyz` does not ask the identity provider or any leader.** An identity-provider outage must not take the console away from people who are already signed in.
7. **New production setting `SWARMSCRIBE_CONSOLE_LEADER_CA_FILE`.** Leader calls use `trust_env=False`, so today nothing can make the console trust a private CA, although private leader addresses are explicitly supported. The CA file is added to, not instead of, the public roots, and is used for leader calls only. It is a real deployment need, and the Compose test uses it the way a deployment would. **(owner)** If refused: the Compose test must instead build a derived test image that appends the test CA to `certifi`'s bundle, which tests an image nobody ships.
8. **Pending sign-ins: capped in the app, rate-limited at the ingress.** The app bounds the table (`SWARMSCRIBE_CONSOLE_LOGIN_ATTEMPTS_MAX`, default 10000) by dropping the *oldest* pending sign-ins, so a flood evicts itself and a prompt sign-in still completes. A per-client rate limit needs the client's address, which only the ingress has: C4b adds the Ingress for it. **(owner)** The alternative, refusing new sign-ins at the cap, lets about 17 requests a second lock everyone out for as long as they continue.
9. **KMS key: out of C4.** `SWARMSCRIBE_CONSOLE_KEY` from the environment is the only key source. The sealed format's version byte (`0x01`) leaves room for an envelope format later, but KMS needs a provider choice, a cloud SDK in the image and a re-seal command; that is its own spec. **(owner)**
10. **The Compose test signs in through a stand-in identity provider at Entra's real host name.** The console's and the leader's Entra endpoints are fixed and the admin CLI refuses any other host, so the stand-in holds the network alias `login.microsoftonline.com` with a certificate from a CA the test makes, trusted through `SSL_CERT_FILE` (identity-provider calls) and ruling 7's setting (leader calls). The console, the leader and the CLI are unchanged. C3a's harness injects an in-process provider into `create_app`; that cannot work here, because the thing under test is the shipped image started by its own entrypoint.
11. **The Compose leaders are the real leader application served over TLS by a 15-line wrapper** (`serve_tls.py`), because `swarmscribe-leader serve` has no TLS option and the console calls https only. The leader image is the existing `e2e/compose/Dockerfile`, unchanged.
12. **Console credentials are created by the real CLI** (`swarmscribe-admin login` then `console create`) in a one-off container on the Compose network, after a real device sign-in at the stand-in.
13. **The Compose test lives in `e2e/console-compose/`**, next to the leader's `e2e/compose/`, and runs as its own GitHub Actions job `console-compose-e2e`.
14. **The image is built and tested in CI but not pushed.** No registry or release process exists in the project yet. **(owner)** Recommendation: a follow-up that pushes to `ghcr.io/iamfatness/swarmscribe-console` on a version tag.

## Review Focus

Inputs and conditions the spec implies and that are most likely to bite a person running this, each pinned by a test in the task that owns the code:

1. **A leader on a private CA** (the normal case for a leader on a LAN): reached when the CA file is configured, refused without it, refused for the wrong host name even with it, and never trusted because of `SSL_CERT_FILE` — Task 2, `test_leader_ca.py`.
2. **A replica during a database outage or a rolling upgrade**: `/readyz` says 503 without leaking the database URL when Postgres is down, 503 when the schema is behind or absent, and stays 200 when the schema is ahead; `/healthz` stays 200 throughout — Task 1, `test_health.py`.
3. **A flood of abandoned sign-ins**: the table stops growing at the cap, the newest sign-in still completes, and the log gets one line, not one per request — Task 3, `test_sign_in_cap.py`.
4. **An optional setting passed as an empty string** (Compose and Kubernetes do this for unset variables): `SWARMSCRIBE_CONSOLE_LEADER_CA_FILE=""` means none — Task 2; the Compose file passes `SWARMSCRIBE_CONSOLE_GOOGLE_CLIENT_ID: ""` on purpose — Task 5.
5. **A read-only root filesystem and a non-root user**: the image starts, migrates, serves the web app and passes its own HEALTHCHECK with `read_only: true` and no capabilities — Tasks 4 and 5 (CI).
6. **A person who signs in but holds no role**: refused with 403 and no session, through the real image and the real flow — Task 5, `run_e2e.py`.

## File Structure

| File | Responsibility |
|---|---|
| `packages/console/src/swarmscribe_console/api/health.py` (new) | `/healthz` and `/readyz` |
| `packages/console/src/swarmscribe_console/app.py` (modify) | mount the health router; remember the head revision; pass the CA file to `LeaderClient` |
| `packages/console/src/swarmscribe_console/config.py` (modify) | `leader_ca_file`, `login_attempts_max` |
| `packages/console/src/swarmscribe_console/leader_client.py` (modify) | `leader_tls_context`; `LeaderClient(ca_file=...)` |
| `packages/console/src/swarmscribe_console/oidc.py` (modify) | `trim_pending_sign_ins` |
| `packages/console/src/swarmscribe_console/api/auth.py` (modify) | call the trim when a sign-in starts |
| `packages/console/tests/test_health.py`, `test_leader_ca.py`, `test_sign_in_cap.py` (new) | their tests |
| `docker/console.Dockerfile` (new) | the image |
| `docker/check-console-image.sh` (new) | what the image must hold, checked without a database |
| `.dockerignore`, `.gitignore` (modify) | keep `node_modules`, `dist` and test work folders out |
| `e2e/console-compose/fake_idp.py` (new) | the stand-in for Entra ID |
| `e2e/console-compose/serve_tls.py` (new) | a real leader, served over TLS |
| `e2e/console-compose/initdb.sql` (new) | the three databases |
| `e2e/console-compose/docker-compose.yml` (new) | the stack |
| `e2e/console-compose/run_e2e.py` (new) | the test CA (`certs`) and the scenario (`run`) |
| `packages/console/tests/test_compose_stand_in.py` (new) | the Compose test's own parts, checked without Docker |
| `.github/workflows/ci.yml` (modify) | job `console-compose-e2e` |
| `README.md` (modify) | two settings; "Console image"; "Console Compose test" |

---

### Task 1: Health endpoints (`/healthz`, `/readyz`)

The console has no probe endpoint today (the leader has both). The image's HEALTHCHECK, the Compose test and C4b's Kubernetes probes need them.

**Files:**
- Create: `packages/console/src/swarmscribe_console/api/health.py`
- Modify: `packages/console/src/swarmscribe_console/app.py`
- Test: `packages/console/tests/test_health.py`

**Interfaces:**
- Consumes: `swarmscribe_console.db.migrate.current_revision(engine) -> str | None`, `head_revision() -> str`, `is_known_revision(revision: str) -> bool` (all exist).
- Produces: `GET /healthz` → `200 {"status": "ok"}`. `GET /readyz` → `200 {"status": "ready"}`, or `503 {"status": "database unreachable"}`, or `503 {"status": "database migrations are not current"}`. Both carry `Cache-Control: no-store`, need no session, and are plain routes registered before the static mount. `app.state.head_revision: str`. Task 4's HEALTHCHECK, Task 5's driver and C4b's probes use these paths and bodies.

- [ ] **Step 1: Write the failing tests**

Create `packages/console/tests/test_health.py`:

```python
import httpx
import pytest
from console_testkit import PUBLIC_URL
from sqlalchemy import text
from swarmscribe_console.app import create_app
from swarmscribe_console.db.migrate import head_revision


async def test_healthz_answers_without_a_session(client):
    answer = await client.get("/healthz")
    assert answer.status_code == 200
    assert answer.json() == {"status": "ok"}
    assert answer.headers["cache-control"] == "no-store"
    assert "script-src 'self'" in answer.headers["content-security-policy"]


async def test_readyz_is_ready_on_a_migrated_database(client):
    answer = await client.get("/readyz")
    assert (answer.status_code, answer.json()) == (200, {"status": "ready"})
    assert answer.headers["cache-control"] == "no-store"


async def test_readyz_says_when_the_database_cannot_be_reached(make_settings, idp, fake_leader):
    application = create_app(
        make_settings(database_url="postgresql://console:secret-pw@127.0.0.1:1/none"),
        background=False,
        fetch=idp.fetch,
        idp_transport=idp.transport,
        leader_transport=fake_leader.transport,
    )
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url=PUBLIC_URL
        ) as made:
            ready = await made.get("/readyz")
            alive = await made.get("/healthz")
    assert (ready.status_code, ready.json()) == (503, {"status": "database unreachable"})
    assert "secret-pw" not in ready.text
    # Liveness is about the process: a database outage must not get the pod restarted.
    assert alive.status_code == 200


async def test_readyz_is_not_ready_when_the_schema_is_behind(app, client):
    # What a console newer than the database sees: its head is not the database's revision.
    app.state.head_revision = "9999_not_applied_yet"
    answer = await client.get("/readyz")
    assert answer.status_code == 503
    assert answer.json() == {"status": "database migrations are not current"}


async def test_readyz_is_not_ready_before_the_first_migration(client, engine):
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE alembic_version RENAME TO alembic_version_away"))
    try:
        answer = await client.get("/readyz")
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("ALTER TABLE alembic_version_away RENAME TO alembic_version")
            )
    assert answer.status_code == 503
    assert answer.json() == {"status": "database migrations are not current"}


async def test_readyz_stays_ready_when_the_database_is_ahead(client, engine):
    """A rolling upgrade: the migration has run and this older replica is still serving. It
    must stay in service until it is replaced, or every old replica drops out at once."""
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE alembic_version SET version_num = 'newer_than_this'"))
    try:
        answer = await client.get("/readyz")
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE alembic_version SET version_num = :head"),
                {"head": head_revision()},
            )
    assert (answer.status_code, answer.json()) == (200, {"status": "ready"})


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
async def test_the_web_app_does_not_shadow_the_probes(
    path, tmp_path, engine, make_settings, idp, fake_leader
):
    (tmp_path / "index.html").write_text("<!doctype html><title>console</title>")
    application = create_app(
        make_settings(static_dir=str(tmp_path)),
        background=False,
        fetch=idp.fetch,
        idp_transport=idp.transport,
        leader_transport=fake_leader.transport,
    )
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url=PUBLIC_URL
        ) as made:
            answer = await made.get(path)
    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
async def test_a_probe_cannot_be_posted_to(client, path):
    assert (await client.post(path)).status_code == 405
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/console/tests/test_health.py -q`
Expected: FAIL. `/healthz` and `/readyz` answer 404 (no static app in these fixtures), and `test_readyz_is_not_ready_when_the_schema_is_behind` fails the same way.

- [ ] **Step 3: Write the health router**

Create `packages/console/src/swarmscribe_console/api/health.py`:

```python
"""Liveness and readiness, for the container's HEALTHCHECK and Kubernetes probes.

/healthz says the process answers. /readyz says this replica can serve: the database answers
and its schema is this console's, or a newer one. A newer schema means a rolling upgrade's
migration has already run; this replica keeps serving until it is replaced (`serve` itself
still refuses to start on a database that is ahead). Neither asks an identity provider or a
leader: their outages must not take the console out of service. Both are outside /api and
/auth, need no session, and say nothing about the deployment beyond a fixed status word."""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..db.migrate import current_revision, is_known_revision

router = APIRouter()

READY_TIMEOUT_SECONDS = 3.0
_NO_STORE = {"Cache-Control": "no-store"}


def _answer(status: str, code: int = 200) -> JSONResponse:
    return JSONResponse({"status": status}, status_code=code, headers=_NO_STORE)


@router.get("/healthz")
async def healthz() -> JSONResponse:
    return _answer("ok")


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    try:
        revision = await asyncio.wait_for(
            current_revision(request.app.state.engine), READY_TIMEOUT_SECONDS
        )
    except Exception:  # refused, unreachable, bad credentials, or no answer in time
        return _answer("database unreachable", 503)
    if revision == request.app.state.head_revision:
        return _answer("ready")
    if revision is None or is_known_revision(revision):
        return _answer("database migrations are not current", 503)
    return _answer("ready")  # the database is ahead: a newer console has migrated it
```

- [ ] **Step 4: Mount it**

In `packages/console/src/swarmscribe_console/app.py`:

Add two imports, each in its alphabetical place:

```python
from .api import fleet as fleet_api
from .api import health as health_api
```

```python
from .crypto import ConsoleKeys
from .db.migrate import head_revision
```

Remember the head revision next to the engine (it is read from the migration scripts, not the database, so it is computed once):

```python
    app.state.engine = engine
    app.state.head_revision = head_revision()
```

Include the router before every other router, so nothing can shadow it:

```python
    api_errors.install(app)
    app.add_middleware(BodyLimit)
    app.include_router(health_api.router)  # /healthz, /readyz: probes, no session
    app.include_router(auth_api.router)  # /auth/*: before a session exists, so no Person guard
```

`assert_guarded` needs no change: it inspects `/api` routes only, and both probes are GET.

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest packages/console/tests/test_health.py -q`
Expected: 10 passed.

- [ ] **Step 6: Run the console suite and the linter**

Run: `uv run pytest packages/console -q` and `uv run ruff check .`
Expected: all pass; no lint errors.

- [ ] **Step 7: Commit**

```bash
git add packages/console/src/swarmscribe_console/api/health.py packages/console/src/swarmscribe_console/app.py packages/console/tests/test_health.py
git commit -m "Console: /healthz and /readyz for container and Kubernetes probes"
```

---

### Task 2: A CA file for leaders on a private CA

Calls to leaders carry the console credential, so `LeaderClient` ignores the environment (`trust_env=False`) and trusts only the public roots that ship with httpx. A leader on a LAN usually has a certificate from the organisation's own CA, and today there is no way to trust it.

**Files:**
- Modify: `packages/console/src/swarmscribe_console/config.py`
- Modify: `packages/console/src/swarmscribe_console/leader_client.py`
- Modify: `packages/console/src/swarmscribe_console/app.py`
- Modify: `README.md`
- Test: `packages/console/tests/test_leader_ca.py`

**Interfaces:**
- Consumes: `httpx.create_ssl_context(trust_env=False) -> ssl.SSLContext` (httpx 0.28: the default context with the bundled public roots).
- Produces: `Settings.leader_ca_file: Path | None` (environment `SWARMSCRIBE_CONSOLE_LEADER_CA_FILE`; blank means none; refused unless it is a readable PEM file holding at least one certificate). `leader_tls_context(ca_file: Path | None) -> ssl.SSLContext | None`. `LeaderClient(transport=None, *, ca_file: Path | None = None)`. Task 5's Compose file and C4b's chart set the variable.

- [ ] **Step 1: Write the failing tests**

Create `packages/console/tests/test_leader_ca.py`:

```python
"""Leaders whose certificates come from a private CA (SWARMSCRIBE_CONSOLE_LEADER_CA_FILE).

The round-trip tests talk real TLS to a server on an ephemeral port of 127.0.0.1."""

import asyncio
import datetime
import ipaddress
import ssl
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import ValidationError
from swarmscribe_console.app import create_app
from swarmscribe_console.config import Settings
from swarmscribe_console.leader_client import (
    POLLER_ACTOR,
    LeaderClient,
    LeaderTarget,
    LeaderUnreachable,
    leader_tls_context,
)

KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
CREDENTIAL = "k" * 21 + "_" + "Q" * 21
CA_NAME = "test leader CA"


def settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql://u:p@127.0.0.1:1/none",
        "public_url": "https://console.example.org",
        "key": KEY,
        "entra_tenant_id": "0f0e0d0c-0b0a-4908-8706-050403020100",
        "entra_client_id": "entra-client",
        "entra_client_secret": "entra-secret-value",
    }
    values.update(overrides)
    return Settings(**values)


def write_ca_and_server(folder: Path, san: x509.GeneralName) -> tuple[Path, Path, Path]:
    """A CA and a server certificate it signed for `san`: (ca.pem, server.pem, server.key)."""
    now = datetime.datetime.now(datetime.UTC)
    start, end = now - datetime.timedelta(minutes=5), now + datetime.timedelta(days=1)

    def name(common: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(name(CA_NAME))
        .issuer_name(name(CA_NAME))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    server = (
        x509.CertificateBuilder()
        .subject_name(name("a leader"))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.SubjectAlternativeName([san]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_file, cert_file, key_file = folder / "ca.pem", folder / "server.pem", folder / "server.key"
    ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_file.write_bytes(server.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca_file, cert_file, key_file


class TlsLeader:
    """A TLS server on 127.0.0.1 that answers every request `{"ok": true}`."""

    def __init__(self, folder: Path, san: x509.GeneralName):
        self.ca_file, self._cert, self._key = write_ca_and_server(folder, san)
        self._server: asyncio.Server | None = None
        self.target: LeaderTarget | None = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        body = b'{"ok": true}'
        head = (
            "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
            f"content-length: {len(body)}\r\nconnection: close\r\n\r\n"
        )
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(head.encode() + body)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass  # a client that refused the certificate hangs up mid-handshake
        finally:
            writer.close()

    async def __aenter__(self) -> "TlsLeader":
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self._cert, self._key)
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=context)
        port = self._server.sockets[0].getsockname()[1]
        self.target = LeaderTarget("lan-1", f"https://127.0.0.1:{port}", CREDENTIAL)
        return self

    async def __aexit__(self, *_exc) -> None:
        self._server.close()
        await self._server.wait_closed()


@pytest.fixture
async def private_leader(tmp_path):
    san = x509.IPAddress(ipaddress.ip_address("127.0.0.1"))
    async with TlsLeader(tmp_path, san) as leader:
        yield leader


async def status(client: LeaderClient, target: LeaderTarget):
    try:
        return await client.call(
            target, "GET", "/v1/admin/status", actor=POLLER_ACTOR, role="viewer", timeout=5.0
        )
    finally:
        await client.aclose()


# --- the setting ------------------------------------------------------------------------


def test_no_leader_ca_file_is_the_default():
    assert settings().leader_ca_file is None
    assert leader_tls_context(None) is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_leader_ca_file_is_unset(blank):
    # Compose and Kubernetes pass an unset variable as an empty string.
    assert settings(leader_ca_file=blank).leader_ca_file is None


@pytest.mark.parametrize("kind", ["missing", "not-pem", "a-folder"])
def test_a_leader_ca_file_without_certificates_is_refused(kind, tmp_path):
    bad = {"missing": tmp_path / "none.pem", "not-pem": tmp_path / "junk.pem", "a-folder": tmp_path}
    (tmp_path / "junk.pem").write_text("not a certificate\n")
    with pytest.raises(ValidationError) as refused:
        settings(leader_ca_file=str(bad[kind]))
    assert "leader_ca_file must be a readable PEM file" in str(refused.value)
    assert str(tmp_path) not in str(refused.value)  # hide_input_in_errors


def test_the_ca_file_adds_to_the_public_roots(tmp_path):
    ca_file, _, _ = write_ca_and_server(tmp_path, x509.DNSName("leader.example"))
    context = leader_tls_context(settings(leader_ca_file=str(ca_file)).leader_ca_file)
    names = [dict(part[0] for part in ca["subject"]).get("commonName")
             for ca in context.get_ca_certs()]
    assert CA_NAME in names
    assert len(names) > 1  # the public roots are still trusted
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


# --- real TLS ---------------------------------------------------------------------------


async def test_a_leader_on_a_private_ca_is_reached_with_the_ca_file(private_leader):
    reply = await status(LeaderClient(ca_file=private_leader.ca_file), private_leader.target)
    assert (reply.status, reply.body) == (200, {"ok": True})


async def test_a_leader_on_a_private_ca_is_refused_without_it(private_leader):
    with pytest.raises(LeaderUnreachable) as refused:
        await status(LeaderClient(), private_leader.target)
    assert refused.value.reason == "connect_error"


async def test_the_environment_cannot_add_a_ca(private_leader, monkeypatch):
    # Leader calls carry credentials: SSL_CERT_FILE and SSL_CERT_DIR are never read.
    monkeypatch.setenv("SSL_CERT_FILE", str(private_leader.ca_file))
    monkeypatch.setenv("SSL_CERT_DIR", str(private_leader.ca_file.parent))
    with pytest.raises(LeaderUnreachable):
        await status(LeaderClient(), private_leader.target)


async def test_a_trusted_ca_does_not_excuse_the_wrong_host_name(tmp_path):
    async with TlsLeader(tmp_path, x509.DNSName("another-leader.example")) as leader:
        with pytest.raises(LeaderUnreachable) as refused:
            await status(LeaderClient(ca_file=leader.ca_file), leader.target)
    assert refused.value.reason == "connect_error"


async def test_the_console_uses_the_setting(private_leader, make_settings):
    application = create_app(
        make_settings(leader_ca_file=str(private_leader.ca_file)), background=False
    )
    try:
        reply = await application.state.leader_client.call(
            private_leader.target,
            "GET",
            "/v1/admin/status",
            actor=POLLER_ACTOR,
            role="viewer",
            timeout=5.0,
        )
    finally:
        await application.state.leader_client.aclose()
        await application.state.engine.dispose()
    assert reply.status == 200
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/console/tests/test_leader_ca.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'leader_tls_context'`.

- [ ] **Step 3: Add the setting**

In `packages/console/src/swarmscribe_console/config.py`, add `import ssl` to the imports (after `import json`), and the field after `static_dir`:

```python
    static_dir: Path | None = None  # the built web app (C3): a folder holding index.html
    # CA certificates (one PEM file) trusted for calls to leaders, in addition to the
    # public roots: for leaders whose certificates come from a private CA.
    leader_ca_file: Path | None = None
```

Make the existing blank-is-unset validator cover both paths (rename it, since it no longer serves one field):

```python
    @field_validator("static_dir", "leader_ca_file", mode="before")
    @classmethod
    def _blank_path_is_unset(cls, value: Any) -> Any:
        # Compose/Kubernetes pass an unset variable as an empty string.
        if isinstance(value, str) and not value.strip():
            return None
        return value
```

Add this validator directly after `_static_dir_holds_the_app`:

```python
    @field_validator("leader_ca_file")
    @classmethod
    def _leader_ca_file_holds_certificates(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cafile=str(value))
        except OSError:  # missing, unreadable, or no certificate in it (ssl.SSLError)
            raise ValueError(
                "leader_ca_file must be a readable PEM file of CA certificates"
            ) from None
        return value
```

- [ ] **Step 4: Use it in the leader client**

In `packages/console/src/swarmscribe_console/leader_client.py`, add `import ssl` (after `import re`) and `from pathlib import Path` (after the `dataclasses` import), add `"leader_tls_context",` to `__all__` after `"is_revoked",`, and replace the start of `LeaderClient` (the class statement and `__init__` down to `transport=transport,`) with:

```python
def leader_tls_context(ca_file: Path | None) -> ssl.SSLContext | None:
    """How a leader's certificate is verified. None: httpx's default, the public roots it
    ships with. With `ca_file` (SWARMSCRIBE_CONSOLE_LEADER_CA_FILE): those roots plus the CA
    certificates in that PEM file, for leaders on a private CA. Verification and host-name
    checking are never turned off, and the environment (SSL_CERT_FILE) is never read."""
    if ca_file is None:
        return None
    context = httpx.create_ssl_context(trust_env=False)
    context.load_verify_locations(cafile=str(ca_file))
    return context


class LeaderClient:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        ca_file: Path | None = None,
    ):
        context = leader_tls_context(ca_file)
        self._client = httpx.AsyncClient(
            transport=transport,
            verify=True if context is None else context,
```

The rest of `__init__` (`follow_redirects=False`, `trust_env=False`, `timeout=httpx.Timeout(None)`) is unchanged. The module docstring stays true as written: the environment's proxy and CA settings are still ignored; the CA file is the console's own setting.

In `packages/console/src/swarmscribe_console/app.py`, pass the setting:

```python
    leader_client = LeaderClient(
        transport=leader_transport, ca_file=settings.leader_ca_file
    )
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest packages/console/tests/test_leader_ca.py -q`
Expected: 12 passed.

- [ ] **Step 6: Document the setting**

In `README.md`, in the table under "Run the fleet console (development)", add this row after the `SWARMSCRIBE_CONSOLE_SESSION_LIFETIME_SECONDS` row:

```markdown
| `SWARMSCRIBE_CONSOLE_LEADER_CA_FILE` | optional: a PEM file of CA certificates trusted for calls to leaders, in addition to the public roots (for leaders whose certificates come from a private CA). Calls to leaders never read `SSL_CERT_FILE` |
```

- [ ] **Step 7: Run the console suite and the linter, then commit**

Run: `uv run pytest packages/console -q` and `uv run ruff check .`
Expected: all pass.

```bash
git add packages/console/src/swarmscribe_console/config.py packages/console/src/swarmscribe_console/leader_client.py packages/console/src/swarmscribe_console/app.py packages/console/tests/test_leader_ca.py README.md
git commit -m "Console: SWARMSCRIBE_CONSOLE_LEADER_CA_FILE for leaders on a private CA"
```

---

### Task 3: A cap on pending sign-ins

`GET /auth/login` needs no session and stores a `login_attempts` row each time (kept for `login_attempt_seconds`, 600 by default). Nothing bounds the table (C2 deferral).

**Files:**
- Modify: `packages/console/src/swarmscribe_console/config.py`
- Modify: `packages/console/src/swarmscribe_console/oidc.py`
- Modify: `packages/console/src/swarmscribe_console/api/auth.py`
- Modify: `README.md`
- Test: `packages/console/tests/test_sign_in_cap.py`

**Interfaces:**
- Consumes: `LoginAttempt` (`state_hash` primary key, `expires_at` indexed); `begin_sign_in(...)` adds the new row to the session without flushing.
- Produces: `Settings.login_attempts_max: int` (default `10_000`, at least 1; environment `SWARMSCRIBE_CONSOLE_LOGIN_ATTEMPTS_MAX`). `async trim_pending_sign_ins(session: AsyncSession, *, keep: int) -> int` in `swarmscribe_console.oidc` (how many rows it deleted; the caller flushes before and commits after). C4b's deployment guide names the setting.

- [ ] **Step 1: Write the failing tests**

Create `packages/console/tests/test_sign_in_cap.py`:

```python
"""The bound on pending sign-ins (SWARMSCRIBE_CONSOLE_LOGIN_ATTEMPTS_MAX).

/auth/login needs no session and stores a row every time, so the table is capped: beyond
the cap the oldest pending sign-ins are dropped and the newest survive."""

import logging
from datetime import timedelta

import httpx
import pytest
from console_testkit import GROUPS, PUBLIC_URL, all_rows_text
from pydantic import ValidationError
from sqlalchemy import func, select
from swarmscribe_console import oidc
from swarmscribe_console.app import create_app
from swarmscribe_console.db.models import LoginAttempt
from swarmscribe_console.oidc import trim_pending_sign_ins
from swarmscribe_leader.clock import utcnow

CAP = 3


@pytest.fixture
async def capped(engine, make_settings, idp, graph, google_groups, fake_leader):
    application = create_app(
        make_settings(login_attempts_max=CAP),
        background=False,
        fetch=idp.fetch,
        idp_transport=idp.transport,
        graph=graph,
        google_groups=google_groups,
        leader_transport=fake_leader.transport,
    )
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def browsers(capped):
    made = [
        httpx.AsyncClient(transport=httpx.ASGITransport(app=capped), base_url=PUBLIC_URL)
        for _ in range(CAP + 2)
    ]
    yield made
    for browser in made:
        await browser.aclose()


@pytest.fixture(autouse=True)
def warn_again(monkeypatch):
    # The warning is limited to one a minute by a module-level time; start each test fresh.
    monkeypatch.setattr(oidc, "_last_trim_warning", float("-inf"))


async def pending(sessionmaker) -> int:
    async with sessionmaker() as session:
        return await session.scalar(select(func.count()).select_from(LoginAttempt))


async def start(browser: httpx.AsyncClient) -> str:
    answer = await browser.get("/auth/login", params={"provider": "entra"})
    assert answer.status_code == 302
    return answer.headers["location"]


async def test_pending_sign_ins_are_capped_and_the_newest_survive(
    browsers, idp, factory, sessionmaker
):
    await factory.grant("operator", "all", "entra_group", GROUPS["operator"])
    at_provider = [await start(browser) for browser in browsers]
    assert await pending(sessionmaker) == CAP

    dropped = await browsers[0].get(
        idp.authorize(at_provider[0], groups=[GROUPS["operator"]])
    )
    assert dropped.status_code == 400
    assert "sign in again" in dropped.text

    newest = await browsers[-1].get(
        idp.authorize(at_provider[-1], groups=[GROUPS["operator"]])
    )
    assert newest.status_code == 200
    assert (await browsers[-1].get("/api/session")).status_code == 200


async def test_under_the_cap_nothing_is_dropped(browsers, sessionmaker, caplog):
    with caplog.at_level(logging.WARNING, logger="swarmscribe_console.oidc"):
        for browser in browsers[:CAP]:
            await start(browser)
    assert await pending(sessionmaker) == CAP
    assert caplog.records == []


async def test_a_flood_is_logged_once_and_without_secrets(browsers, engine, caplog):
    with caplog.at_level(logging.WARNING, logger="swarmscribe_console.oidc"):
        at_provider = [await start(browser) for browser in browsers]
    (warning,) = caplog.records
    assert "sign-ins are pending" in warning.getMessage()
    assert "/auth/login" in warning.getMessage()
    for location in at_provider:
        state = dict(httpx.URL(location).params)["state"]
        assert state not in caplog.text
        assert state not in await all_rows_text(engine)  # only its hash is ever stored


async def test_trimming_keeps_the_newest(sessionmaker):
    now = utcnow()
    async with sessionmaker() as session:
        for age in range(4):
            session.add(
                LoginAttempt(
                    state_hash=f"{age}" * 64,
                    browser_hash="b" * 64,
                    provider="entra",
                    nonce="n",
                    code_verifier="v",
                    return_to="/",
                    expires_at=now + timedelta(minutes=10 - age),  # 0 is the newest
                )
            )
        await session.flush()
        assert await trim_pending_sign_ins(session, keep=2) == 2
        assert await trim_pending_sign_ins(session, keep=2) == 0
        await session.commit()
    async with sessionmaker() as session:
        left = (await session.scalars(select(LoginAttempt.state_hash))).all()
    assert sorted(left) == ["0" * 64, "1" * 64]


@pytest.mark.parametrize("bad", [0, -1])
def test_the_cap_is_at_least_one(make_settings, bad):
    with pytest.raises(ValidationError):
        make_settings(login_attempts_max=bad)


def test_the_default_cap(make_settings):
    assert make_settings().login_attempts_max == 10_000
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest packages/console/tests/test_sign_in_cap.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'trim_pending_sign_ins'`.

- [ ] **Step 3: Add the setting**

In `packages/console/src/swarmscribe_console/config.py`, after `login_attempt_seconds`:

```python
    login_attempt_seconds: int = Field(default=600, gt=0)
    # Sign-ins that have gone to the identity provider and not come back. Beyond this many
    # the oldest are dropped, so that requests to /auth/login cannot fill the database.
    login_attempts_max: int = Field(default=10_000, gt=0)
```

- [ ] **Step 4: Write the trim**

In `packages/console/src/swarmscribe_console/oidc.py`:

Add `import logging` and `import time` to the standard-library imports (alphabetical: `hmac`, `logging`, `secrets`, `time`), and change `from sqlalchemy import delete` to `from sqlalchemy import delete, select`.

After `MAX_CODE_CHARS = 4096`:

```python
MAX_CODE_CHARS = 4096
TRIM_WARNING_SECONDS = 60.0

logger = logging.getLogger(__name__)
_last_trim_warning = float("-inf")
```

Directly before the `SignInAttempt` dataclass:

```python
async def trim_pending_sign_ins(session: AsyncSession, *, keep: int) -> int:
    """Drop the oldest pending sign-ins beyond the newest `keep`. Returns how many went.

    /auth/login needs no session and stores a row each time, so without a bound anyone could
    fill the table. The newest are kept: under a flood, a person who signs in promptly still
    finishes, and the flood evicts itself. Rows another transaction has locked are skipped,
    never waited for. The caller has flushed its own new row, and commits."""
    beyond = (
        select(LoginAttempt.state_hash)
        .order_by(LoginAttempt.expires_at.desc(), LoginAttempt.state_hash)
        .offset(keep)
        .with_for_update(skip_locked=True)
    )
    dropped = await session.execute(
        delete(LoginAttempt)
        .where(LoginAttempt.state_hash.in_(beyond))
        .execution_options(synchronize_session=False)
    )
    if dropped.rowcount:
        global _last_trim_warning
        now = time.monotonic()
        if now - _last_trim_warning >= TRIM_WARNING_SECONDS:  # one line a minute at most
            _last_trim_warning = now
            logger.warning(
                "more than %d sign-ins are pending; the oldest were dropped "
                "(rate-limit /auth/login at the ingress)",
                keep,
            )
    return dropped.rowcount
```

The statement Postgres runs is `DELETE FROM login_attempts WHERE state_hash IN (SELECT state_hash FROM login_attempts ORDER BY expires_at DESC, state_hash LIMIT ALL OFFSET :keep FOR UPDATE SKIP LOCKED)`.

- [ ] **Step 5: Call it when a sign-in starts**

In `packages/console/src/swarmscribe_console/api/auth.py`, import `trim_pending_sign_ins` from `..oidc` (after `finish_sign_in`), and in `login`, between `begin_sign_in(...)` and the commit:

```python
            prior_session_hash=prior,
        )
        await session.flush()  # the new sign-in counts, and is the newest, when trimming
        await trim_pending_sign_ins(session, keep=settings.login_attempts_max)
        await session.commit()
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv run pytest packages/console/tests/test_sign_in_cap.py packages/console/tests/test_sign_in.py -q`
Expected: all pass (7 in `test_sign_in_cap.py`; `test_sign_in.py` is unchanged and still passes).

- [ ] **Step 7: Document the setting**

In `README.md`, in the same table, after the row added in Task 2:

```markdown
| `SWARMSCRIBE_CONSOLE_LOGIN_ATTEMPTS_MAX` | optional: how many started sign-ins may be pending at once (default 10000). Beyond it the oldest are dropped, so requests to `/auth/login` cannot fill the database; rate-limit that path per client at your ingress as well |
```

- [ ] **Step 8: Run the console suite and the linter, then commit**

Run: `uv run pytest packages/console -q` and `uv run ruff check .`
Expected: all pass.

```bash
git add packages/console/src/swarmscribe_console/config.py packages/console/src/swarmscribe_console/oidc.py packages/console/src/swarmscribe_console/api/auth.py packages/console/tests/test_sign_in_cap.py README.md
git commit -m "Console: cap pending sign-ins, dropping the oldest"
```

---

### Task 4: The `swarmscribe-console` image

**Files:**
- Create: `docker/console.Dockerfile`
- Create: `docker/check-console-image.sh`
- Modify: `.dockerignore`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: Task 1's `/healthz`; `packages/console-web`'s `npm run build` (writes `dist/` and runs `scripts/check-dist.mjs`); the workspace's `uv.lock`.
- Produces: an image whose entrypoint is `swarmscribe-console`, default command `serve --host 0.0.0.0 --port 8080`, user `10001:10001`, port 8080, `SWARMSCRIBE_CONSOLE_STATIC_DIR=/app/web`, and a HEALTHCHECK on `/healthz`. CI tags it `swarmscribe-console:e2e`. Task 5's Compose file and C4b's chart rely on exactly these.

What was checked while writing this plan, on the Windows machine without Docker: the Python stage's two `uv sync` commands, run on a copy of the manifests and of `packages/{protocol,leader,console}`, install 37 packages, none of them an engine or model library, and the installed wheels include the Alembic migrations (`head_revision()` answers `0001`). The Node stage runs the same two commands as the existing CI job `web`. The image as a whole has not been built: this task's verification is the CI job.

- [ ] **Step 1: Write the image check (the failing test)**

Create `docker/check-console-image.sh`:

```bash
#!/usr/bin/env bash
# What the swarmscribe-console image must hold, checked without a database:
#
#   bash docker/check-console-image.sh swarmscribe-console:e2e
#
# The Compose test (e2e/console-compose) checks the rest by running it.
set -euo pipefail

image="${1:?usage: check-console-image.sh <image>}"

fail() {
  echo "FAILED: $*" >&2
  exit 1
}

user="$(docker inspect --format '{{.Config.User}}' "$image")"
[ "$user" = "10001:10001" ] || fail "the image's user is '$user', not 10001:10001"
[ "$(docker run --rm --entrypoint id "$image" -u)" = "10001" ] \
  || fail "the container does not run as uid 10001"

docker inspect --format '{{json .Config.Healthcheck}}' "$image" | grep -q '/healthz' \
  || fail "the image has no HEALTHCHECK on /healthz"

# The console imports the leader and protocol packages as libraries; the engine and its
# model libraries must never come with them (master spec, section 4).
docker run --rm --entrypoint python "$image" -c '
import importlib.util
import sys

banned = ("swarmscribe_engine", "faster_whisper", "ctranslate2", "av", "onnxruntime", "torch",
          "numpy", "tokenizers", "huggingface_hub")
found = [name for name in banned if importlib.util.find_spec(name)]
sys.exit(f"engine or model libraries in the image: {found}" if found else 0)
' || fail "the image carries engine or model libraries"

docker run --rm --entrypoint sh "$image" -c 'test -f "$SWARMSCRIBE_CONSOLE_STATIC_DIR/index.html"' \
  || fail "SWARMSCRIBE_CONSOLE_STATIC_DIR does not hold the web app's index.html"

if docker run --rm --entrypoint sh "$image" -c 'command -v node || command -v npm || command -v uv' >/dev/null; then
  fail "build tools (node, npm or uv) are in the final image"
fi

# Read-only root filesystem, no network, no configuration: the command still starts, names
# what is missing and exits 2, without a traceback.
status=0
output="$(docker run --rm --read-only --network none "$image" migrate 2>&1)" || status=$?
[ "$status" = "2" ] || fail "migrate without configuration exited $status, not 2"
echo "$output" | grep -q 'invalid configuration' \
  || fail "migrate without configuration did not say so: $output"
if echo "$output" | grep -q 'Traceback'; then
  fail "migrate without configuration ended in a traceback"
fi

size="$(docker image inspect --format '{{.Size}}' "$image")"
echo "ok: $image holds the console and the web app, without the engine ($((size / 1000000)) MB)"
```

Check its syntax locally: `bash -n docker/check-console-image.sh` (no output means it parses).

- [ ] **Step 2: Add the CI job that builds and checks the image**

Append to `.github/workflows/ci.yml`, under `jobs:` (Task 5 adds the Compose steps to this job):

```yaml
  console-compose-e2e:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    steps:
      - uses: actions/checkout@v4
      - name: Build the console image
        run: docker build -t swarmscribe-console:e2e -f docker/console.Dockerfile .
      - name: Check the console image
        run: bash docker/check-console-image.sh swarmscribe-console:e2e
```

- [ ] **Step 3: Keep build contexts small and clean**

Replace `.dockerignore` with:

```
.git
.venv
.pgdata
.pytest_cache
.ruff_cache
.superpowers
**/__pycache__
**/node_modules
packages/console-web/dist
packages/console-web/test-results
packages/console-web/playwright-report
e2e/compose/work
e2e/console-compose/work
*.env
**/*.env
```

(`**/node_modules` matters for both images: the leader's test Dockerfile does `COPY . .`, and the console's web stage must run `npm ci` itself, never reuse a developer's folder. Leaving `dist` out guarantees the image holds what its own build produced.)

- [ ] **Step 4: Write the Dockerfile**

Create `docker/console.Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1
# swarmscribe-console: the fleet console's backend serving the built web app (fleet console
# spec, section 8). Build from the repository root:
#
#   docker build -t swarmscribe-console -f docker/console.Dockerfile .
#
# Base images are pinned by tag and digest: the tag says what it is, the digest makes the
# build repeatable. To move one, change both together; the new digest comes from
#
#   docker buildx imagetools inspect python:3.12-slim-bookworm

# --- the web app ----------------------------------------------------------------------
FROM node:24.15.0-bookworm-slim@sha256:4e6b70dd6cbfc88c8157ba19aa3d9f9cce6ba4703576d55459e45efcbc9c5f5d AS web
WORKDIR /web
# Dependencies first, from the manifests alone: this layer is rebuilt only when they change.
COPY packages/console-web/package.json packages/console-web/package-lock.json packages/console-web/.npmrc ./
RUN npm ci
COPY packages/console-web/ ./
# `vite build`, then scripts/check-dist.mjs refuses a build the console would serve wrongly.
RUN npm run build

# --- the Python environment -----------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS build
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
RUN pip install --no-cache-dir "uv==0.12.22"
WORKDIR /app
# Every workspace member's manifest must be present for uv to read the lock; only the
# console and the two packages it imports (leader, protocol) are installed. The engine and
# its model libraries are never installed: check-console-image.sh proves it.
COPY pyproject.toml uv.lock ./
COPY packages/protocol/pyproject.toml packages/protocol/pyproject.toml
COPY packages/engine/pyproject.toml packages/engine/pyproject.toml
COPY packages/leader/pyproject.toml packages/leader/pyproject.toml
COPY packages/console/pyproject.toml packages/console/pyproject.toml
RUN uv sync --frozen --no-dev --package swarmscribe-console --no-install-workspace
COPY packages/protocol packages/protocol
COPY packages/leader packages/leader
COPY packages/console packages/console
# --no-editable: the three packages are installed into the environment as wheels, so the
# final image needs /app/.venv and nothing of the source tree.
RUN uv sync --frozen --no-dev --package swarmscribe-console --no-editable

# --- the image --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
LABEL org.opencontainers.image.title="swarmscribe-console" \
      org.opencontainers.image.description="SwarmScribe fleet console: backend and web app" \
      org.opencontainers.image.source="https://github.com/iamfatness/SwarmScribe"
RUN groupadd --gid 10001 console \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin console
COPY --from=build /app/.venv /app/.venv
COPY --from=web /web/dist /app/web
# Nothing is written at run time: the root filesystem can be read-only.
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SWARMSCRIBE_CONSOLE_STATIC_DIR=/app/web
WORKDIR /app
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"]
# `serve` is the default; `migrate` and `admins ...` are run by overriding the arguments:
#   docker run ... swarmscribe-console migrate
ENTRYPOINT ["swarmscribe-console"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
```

Notes for whoever changes it later:
- The console depends on `swarmscribe-leader` as a library, so the image also has the `swarmscribe-leader` and `swarmscribe-admin` commands. They are not used and need nothing the image lacks; `check-console-image.sh` proves the engine did not come along.
- The environment is built at `/app/.venv` and copied to the same path on the same base image, so its interpreter links stay valid.

- [ ] **Step 5: Push and watch the job**

Commit, push the branch and open a draft pull request:

```bash
git add docker/console.Dockerfile docker/check-console-image.sh .dockerignore .github/workflows/ci.yml
git commit -m "swarmscribe-console image: web app and backend, non-root, no engine"
git push -u origin fleet-console-c4a
```

Expected in GitHub Actions, job `console-compose-e2e`: the build succeeds and the check prints `ok: swarmscribe-console:e2e holds the console and the web app, without the engine (… MB)`. The existing `compose-e2e` job must still pass (its build context shrank).

If the build fails in the web stage with an engine error from npm, the Node tag no longer satisfies `packages/console-web/package.json`'s `engines`: move tag and digest together (ruling 2). If the check fails, fix the Dockerfile, not the check.

---

### Task 5: The Compose test

**Files:**
- Create: `e2e/console-compose/fake_idp.py`
- Create: `e2e/console-compose/serve_tls.py`
- Create: `e2e/console-compose/initdb.sql`
- Create: `e2e/console-compose/docker-compose.yml`
- Create: `e2e/console-compose/run_e2e.py`
- Create: `packages/console/tests/test_compose_stand_in.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `.gitignore`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 4's image `swarmscribe-console:e2e`; the leader image `swarmscribe-leader:e2e` from `e2e/compose/Dockerfile` (unchanged); Task 1's `/readyz`; Task 2's `SWARMSCRIBE_CONSOLE_LEADER_CA_FILE`; the console API (`/auth/login`, `/auth/callback`, `/api/session`, `/api/admin/grants`, `/api/admin/leaders`, `/api/fleet`, `/api/leaders/{name}/jobs`, `/api/leaders/{name}/tokens`); `swarmscribe-admin --leader <url> login --provider entra` and `swarmscribe-admin --leader <url> --json console create --name <n> --max-role <r>` (prints JSON with `credential`).
- Produces: `fake_idp.create_app(*, tenant, console_client_id, console_client_secret, console_redirect_uri, leader_client_id) -> Starlette`; constants `CONSOLE_ADMINS`, `FLEET_ADMINS`, `LEADER_ADMINS`, `PERSONAS`, `DEVICE_PERSONA`. `run_e2e.py certs` and `run_e2e.py run`; `write_certs(folder: Path) -> None`; `class Browser` with `get`, `post`, `sign_in(persona) -> httpx.Response`, `cookies`, `csrf_token`.

How the pieces fit:

```
host (GitHub runner)                     Compose network
--------------------                     ---------------------------------------------
run_e2e.py  --http://localhost:18080-->  console (swarmscribe-console:e2e, read-only)
            --https://localhost:18443->  fake-idp  = login.microsoftonline.com (alias)
            --docker compose run------>  admin-cli (swarmscribe-admin)
                                         leader-a, leader-b (real leaders, TLS on 8443)
                                         postgres (databases console, leader_a, leader_b)
```

- [ ] **Step 1: Write the tests for the test's own parts (they fail: the files do not exist)**

Create `packages/console/tests/test_compose_stand_in.py`:

```python
"""The console Compose test's own parts (e2e/console-compose), checked without Docker.

The stand-in for Entra ID is exercised in process by the code that talks to it inside the
Compose network: the console's code exchange and token verification, and the admin CLI's
device sign-in from a leader's login-config. Also checked: the certificate the test makes
for itself, the driver's hand-kept cookie jar, and that docker-compose.yml, fake_idp.py and
run_e2e.py agree on the values they share."""

import asyncio
import importlib.util
import secrets
import ssl
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
import yaml
from swarmscribe_console.config import Settings
from swarmscribe_console.oidc import (
    CodeExchangeFailed,
    authorization_url,
    exchange_code,
    web_providers,
)
from swarmscribe_leader.admin_cli.device_flow import ProviderConfig, device_sign_in
from swarmscribe_leader.auth.oidc import TokenVerifier, login_providers, providers_from
from swarmscribe_leader.auth.roles import RoleMapping
from swarmscribe_leader.config import Settings as LeaderSettings

COMPOSE = Path(__file__).resolve().parents[3] / "e2e" / "console-compose"


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, COMPOSE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fake_idp = load("fake_idp", "fake_idp.py")  # run_e2e.py imports it by this name
driver = load("console_compose_driver", "run_e2e.py")

TENANT = "0f0e0d0c-0b0a-4908-8706-050403020100"
CONSOLE_CLIENT = "11111111-1111-4111-8111-111111111111"
LEADER_CLIENT = "22222222-2222-4222-8222-222222222222"
SECRET = "console-compose-client-secret"
REDIRECT = "http://localhost:18080/auth/callback"
STATE, NONCE = "s" * 43, "n" * 43


@pytest.fixture
def transport():
    app = fake_idp.create_app(
        tenant=TENANT,
        console_client_id=CONSOLE_CLIENT,
        console_client_secret=SECRET,
        console_redirect_uri=REDIRECT,
        leader_client_id=LEADER_CLIENT,
    )
    return httpx.ASGITransport(app=app)


@pytest.fixture
def fetch(transport):
    async def fetch(url: str) -> dict:
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    return fetch


@pytest.fixture
def provider():
    settings = Settings(
        database_url="postgresql://u:p@127.0.0.1:1/none",
        public_url="http://localhost:18080",
        key="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
        entra_tenant_id=TENANT,
        entra_client_id=CONSOLE_CLIENT,
        entra_client_secret=SECRET,
    )
    return web_providers(settings)["entra"]


async def visit(transport, provider, verifier: str, persona: str | None) -> httpx.Response:
    """The browser at the authorization page the console sent it to."""
    url = authorization_url(
        provider, redirect_uri=REDIRECT, state=STATE, nonce=NONCE, verifier=verifier
    )
    if persona is not None:
        url += f"&login_hint={persona}"
    async with httpx.AsyncClient(transport=transport) as browser:
        return await browser.get(url)


def code_of(answer: httpx.Response) -> str:
    assert answer.status_code == 302, answer.text
    back = urlsplit(answer.headers["location"])
    assert f"{back.scheme}://{back.netloc}{back.path}" == REDIRECT
    params = dict(parse_qsl(back.query))
    assert params["state"] == STATE
    return params["code"]


# --- the console's sign-in --------------------------------------------------------------


async def test_the_console_signs_a_person_in_through_the_stand_in(transport, fetch, provider):
    verifier = secrets.token_urlsafe(64)
    code = code_of(await visit(transport, provider, verifier, "console-admin"))
    id_token = await exchange_code(
        provider, code=code, verifier=verifier, redirect_uri=REDIRECT, transport=transport
    )
    identity = await TokenVerifier([provider.verification], fetch=fetch).verify(id_token)
    assert identity.provider == "entra"
    assert identity.email == "console-admin@example.org"
    assert identity.claims["nonce"] == NONCE
    assert identity.claims["groups"] == [fake_idp.CONSOLE_ADMINS, fake_idp.FLEET_ADMINS]


async def test_a_code_works_once_and_only_with_its_verifier(transport, provider):
    verifier = secrets.token_urlsafe(64)
    code = code_of(await visit(transport, provider, verifier, "console-admin"))
    with pytest.raises(CodeExchangeFailed):
        await exchange_code(
            provider,
            code=code,
            verifier=secrets.token_urlsafe(64),
            redirect_uri=REDIRECT,
            transport=transport,
        )
    with pytest.raises(CodeExchangeFailed):  # the failed attempt used the code up
        await exchange_code(
            provider, code=code, verifier=verifier, redirect_uri=REDIRECT, transport=transport
        )


@pytest.mark.parametrize("persona", [None, "nobody-by-this-name"])
async def test_the_stand_in_signs_in_named_personas_only(transport, provider, persona):
    answer = await visit(transport, provider, secrets.token_urlsafe(64), persona)
    assert answer.status_code == 400


# --- the admin CLI's sign-in to a leader ------------------------------------------------


async def test_the_admin_cli_signs_in_to_a_leader_through_the_stand_in(transport, fetch):
    leader = LeaderSettings(
        database_url="postgresql://u:p@127.0.0.1:1/none",
        public_url="https://leader-a:8443",
        link_key="k" * 32,
        entra_tenant_id=TENANT,
        entra_client_id=LEADER_CLIENT,
        role_admin_entra_groups=fake_idp.LEADER_ADMINS,
    )
    (config,) = login_providers(leader)  # what GET /v1/admin/login-config hands the CLI
    shown: list[str] = []
    async with httpx.AsyncClient(transport=transport) as http:
        tokens = await device_sign_in(
            http,
            ProviderConfig(
                name=config["name"],
                client_id=config["client_id"],
                device_authorization_endpoint=config["device_authorization_endpoint"],
                token_endpoint=config["token_endpoint"],
                scope=config["scope"],
                client_secret=config["client_secret"],
            ),
            prompt=shown.append,
            sleep=lambda _seconds: asyncio.sleep(0),
        )
    assert len(shown) == 1
    person = await TokenVerifier(providers_from(leader), fetch=fetch).verify(tokens.id_token)
    assert person.email == "leader-admin@example.org"
    admins = RoleMapping.from_settings(leader).entra_groups["admin"]
    assert set(person.claims["groups"]) & admins


# --- the test's certificate -------------------------------------------------------------


async def handshake(port: int, ca_file: Path, name: str) -> None:
    context = ssl.create_default_context(cafile=str(ca_file))
    _reader, writer = await asyncio.open_connection(
        "127.0.0.1", port, ssl=context, server_hostname=name
    )
    writer.close()
    await writer.wait_closed()


async def test_the_test_certificate_covers_every_name_and_no_other(tmp_path):
    driver.write_certs(tmp_path)
    serving = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    serving.load_cert_chain(tmp_path / "server.pem", tmp_path / "server.key")

    async def hang_up(_reader, writer) -> None:
        writer.close()

    server = await asyncio.start_server(hang_up, "127.0.0.1", 0, ssl=serving)
    port = server.sockets[0].getsockname()[1]
    try:
        for name in ("login.microsoftonline.com", "leader-a", "leader-b", "localhost"):
            await handshake(port, tmp_path / "ca.pem", name)
        with pytest.raises(ssl.SSLCertVerificationError):
            await handshake(port, tmp_path / "ca.pem", "graph.microsoft.com")
    finally:
        server.close()
        await server.wait_closed()


# --- the driver's browser ---------------------------------------------------------------


def test_the_browser_keeps_secure_cookies_and_sends_the_csrf_token(tmp_path):
    driver.write_certs(tmp_path)
    seen: list[httpx.Request] = []

    def console(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/set":
            return httpx.Response(
                200,
                headers=[
                    ("set-cookie", "__Host-a=one; Path=/; Secure; HttpOnly; SameSite=Strict"),
                    ("set-cookie", "__Host-b=two; Path=/; Secure; HttpOnly; SameSite=Lax"),
                ],
            )
        if request.url.path == "/drop":
            return httpx.Response(
                200, headers={"set-cookie": '__Host-b=""; Max-Age=0; Path=/; Secure'}
            )
        return httpx.Response(204)

    browser = driver.Browser(tmp_path / "ca.pem")
    browser.console = httpx.Client(
        transport=httpx.MockTransport(console), base_url=driver.CONSOLE
    )
    try:
        browser.get("/set")
        browser.get("/next")
        assert seen[-1].headers["cookie"] == "__Host-a=one; __Host-b=two"
        assert "origin" not in seen[-1].headers
        browser.get("/drop")
        browser.csrf_token = "csrf-value"
        browser.post("/change", json={})
        assert seen[-1].headers["cookie"] == "__Host-a=one"
        assert seen[-1].headers["origin"] == driver.CONSOLE
        assert seen[-1].headers["x-csrf-token"] == "csrf-value"
    finally:
        browser.close()


# --- one set of values ------------------------------------------------------------------


def test_the_compose_file_the_stand_in_and_the_driver_agree():
    services = yaml.safe_load((COMPOSE / "docker-compose.yml").read_text())["services"]
    console = services["console"]["environment"]
    idp = services["fake-idp"]["environment"]
    assert console["SWARMSCRIBE_CONSOLE_PUBLIC_URL"] == driver.CONSOLE
    assert idp["FAKE_IDP_CONSOLE_REDIRECT_URI"] == driver.CONSOLE + "/auth/callback"
    assert idp["FAKE_IDP_TENANT"] == console["SWARMSCRIBE_CONSOLE_ENTRA_TENANT_ID"]
    assert idp["FAKE_IDP_CONSOLE_CLIENT_ID"] == console["SWARMSCRIBE_CONSOLE_ENTRA_CLIENT_ID"]
    assert (
        idp["FAKE_IDP_CONSOLE_CLIENT_SECRET"]
        == console["SWARMSCRIBE_CONSOLE_ENTRA_CLIENT_SECRET"]
    )
    assert services["console"]["ports"] == [f"{urlsplit(driver.CONSOLE).port}:8080"]
    assert services["fake-idp"]["ports"] == [f"{urlsplit(driver.IDP).port}:443"]
    assert services["fake-idp"]["networks"]["default"]["aliases"] == [driver.ENTRA_HOST]
    assert services["console-bootstrap"]["command"][-1] == fake_idp.CONSOLE_ADMINS
    for name in driver.LEADERS:
        leader = services[name]["environment"]
        assert leader["SWARMSCRIBE_ROLE_ADMIN_ENTRA_GROUPS"] == fake_idp.LEADER_ADMINS
        assert leader["SWARMSCRIBE_ENTRA_CLIENT_ID"] == idp["FAKE_IDP_LEADER_CLIENT_ID"]
        assert leader["SWARMSCRIBE_ENTRA_TENANT_ID"] == idp["FAKE_IDP_TENANT"]
        assert leader["SWARMSCRIBE_PUBLIC_URL"] == f"https://{name}:8443"
        assert name in driver.SERVER_NAMES
    # The console runs as the chart runs it: read-only, and through the image's own user.
    assert services["console"]["read_only"] is True
    assert "user" not in services["console"]
    assert "healthcheck" not in services["console"]  # the image's HEALTHCHECK is under test
    assert fake_idp.DEVICE_PERSONA in fake_idp.PERSONAS
    assert fake_idp.FLEET_ADMINS in fake_idp.PERSONAS["console-admin"]
```

Run: `uv run pytest packages/console/tests/test_compose_stand_in.py -q`
Expected: FAIL at collection with `FileNotFoundError` for `e2e/console-compose/fake_idp.py`.

- [ ] **Step 2: Write the stand-in for Entra ID**

Create `e2e/console-compose/fake_idp.py`:

```python
"""Microsoft Entra ID as the console and the leaders see it, for the console Compose test only.

The console, the leaders and `swarmscribe-admin` all use fixed Entra addresses on
https://login.microsoftonline.com, and the admin CLI refuses any other host. So the test
changes nothing in them: inside the Compose network this server holds the network alias
`login.microsoftonline.com` and a certificate for that name from the test's own CA
(work/certs, trusted through SSL_CERT_FILE). Nothing here is part of any image or package.

It serves one tenant:

- the discovery document and the signing keys;
- the authorization endpoint: instead of a sign-in page it signs in the persona named by
  `login_hint` at once and redirects back with a code;
- the device authorization endpoint (`swarmscribe-admin login`): the device code is approved
  at once, as the persona DEVICE_PERSONA;
- the token endpoint: authorization code with PKCE and the client secret (the console),
  and the device-code grant (the admin CLI).

Run inside the Compose network (docker-compose.yml does):

    python /e2e/fake_idp.py
"""

import base64
import hashlib
import json
import os
import secrets
import time
import uuid
from urllib.parse import parse_qsl, urlencode

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

AUTHORITY = "https://login.microsoftonline.com"
KEY_ID = "console-compose-key-1"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

# Entra group object ids. docker-compose.yml repeats CONSOLE_ADMINS (the console's bootstrap
# administrator) and LEADER_ADMINS (the leaders' admin role); keep them the same.
CONSOLE_ADMINS = "c0c0c0c0-0000-4000-8000-000000000001"
FLEET_ADMINS = "c0c0c0c0-0000-4000-8000-000000000002"
LEADER_ADMINS = "c0c0c0c0-0000-4000-8000-000000000003"
PERSONAS: dict[str, list[str]] = {
    "console-admin": [CONSOLE_ADMINS, FLEET_ADMINS],
    "leader-admin": [LEADER_ADMINS],
    "stranger": [],
}
DEVICE_PERSONA = "leader-admin"


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _error(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


async def _form(request: Request) -> dict[str, str]:
    """The urlencoded body (Starlette's own form parsing needs python-multipart, which the
    leader image this runs in does not carry)."""
    return dict(parse_qsl((await request.body()).decode()))


def create_app(
    *,
    tenant: str,
    console_client_id: str,
    console_client_secret: str,
    console_redirect_uri: str,
    leader_client_id: str,
) -> Starlette:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = f"{AUTHORITY}/{tenant}/v2.0"
    jwks_uri = f"{AUTHORITY}/{tenant}/discovery/v2.0/keys"
    codes: dict[str, dict[str, str]] = {}
    device_codes: dict[str, str] = {}

    def id_token(persona: str, client_id: str, nonce: str | None) -> str:
        now = int(time.time())
        claims = {
            "iss": issuer,
            "aud": client_id,
            "tid": tenant,
            "sub": f"compose-{persona}",
            "oid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"compose-{persona}")),
            "email": f"{persona}@example.org",
            "groups": PERSONAS[persona],
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
        }
        if nonce is not None:
            claims["nonce"] = nonce
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": KEY_ID})

    def tokens(persona: str, client_id: str, nonce: str | None) -> JSONResponse:
        return JSONResponse(
            {
                "token_type": "Bearer",
                "expires_in": 3600,
                "id_token": id_token(persona, client_id, nonce),
                "access_token": f"access-{secrets.token_urlsafe(16)}",
                "refresh_token": f"refresh-{secrets.token_urlsafe(16)}",
            }
        )

    async def healthz(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def discovery(_request: Request) -> JSONResponse:
        return JSONResponse({"issuer": issuer, "jwks_uri": jwks_uri})

    async def keys(_request: Request) -> JSONResponse:
        jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
        jwk.update(kid=KEY_ID, use="sig", alg="RS256")
        return JSONResponse({"keys": [jwk]})

    async def authorize(request: Request) -> JSONResponse | RedirectResponse:
        query = request.query_params
        persona = query.get("login_hint", "")
        if (
            query.get("client_id") != console_client_id
            or query.get("redirect_uri") != console_redirect_uri
            or query.get("response_type") != "code"
            or query.get("code_challenge_method") != "S256"
            or not query.get("code_challenge")
            or not query.get("state")
            or not query.get("nonce")
        ):
            return _error("invalid_request")
        if persona not in PERSONAS:
            return _error("unknown_persona")
        code = secrets.token_urlsafe(24)
        codes[code] = {
            "persona": persona,
            "nonce": query["nonce"],
            "challenge": query["code_challenge"],
        }
        back = urlencode({"code": code, "state": query["state"]})
        return RedirectResponse(f"{console_redirect_uri}?{back}", status_code=302)

    async def device_authorization(request: Request) -> JSONResponse:
        form = await _form(request)
        if form.get("client_id") != leader_client_id:
            return _error("invalid_client")
        device_code = secrets.token_urlsafe(32)
        device_codes[device_code] = DEVICE_PERSONA
        return JSONResponse(
            {
                "device_code": device_code,
                "user_code": "COMPOSE",
                "verification_uri": f"{AUTHORITY}/device",
                "expires_in": 300,
                "interval": 1,
            }
        )

    async def token(request: Request) -> JSONResponse:
        form = await _form(request)
        grant_type = form.get("grant_type")
        if grant_type == "authorization_code":
            grant = codes.pop(form.get("code", ""), None)
            if (
                grant is None
                or form.get("client_id") != console_client_id
                or form.get("client_secret") != console_client_secret
                or form.get("redirect_uri") != console_redirect_uri
                or _challenge(form.get("code_verifier", "")) != grant["challenge"]
            ):
                return _error("invalid_grant")
            return tokens(grant["persona"], console_client_id, grant["nonce"])
        if grant_type == DEVICE_GRANT:
            persona = device_codes.pop(form.get("device_code", ""), None)
            if persona is None or form.get("client_id") != leader_client_id:
                return _error("invalid_grant")
            return tokens(persona, leader_client_id, None)
        return _error("unsupported_grant_type")

    return Starlette(
        routes=[
            Route("/healthz", healthz),
            Route(f"/{tenant}/v2.0/.well-known/openid-configuration", discovery),
            Route(f"/{tenant}/discovery/v2.0/keys", keys),
            Route(f"/{tenant}/oauth2/v2.0/authorize", authorize),
            Route(f"/{tenant}/oauth2/v2.0/devicecode", device_authorization, methods=["POST"]),
            Route(f"/{tenant}/oauth2/v2.0/token", token, methods=["POST"]),
        ]
    )


def main() -> None:
    env = os.environ
    app = create_app(
        tenant=env["FAKE_IDP_TENANT"],
        console_client_id=env["FAKE_IDP_CONSOLE_CLIENT_ID"],
        console_client_secret=env["FAKE_IDP_CONSOLE_CLIENT_SECRET"],
        console_redirect_uri=env["FAKE_IDP_CONSOLE_REDIRECT_URI"],
        leader_client_id=env["FAKE_IDP_LEADER_CLIENT_ID"],
    )
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=443,
        ssl_certfile="/certs/server.pem",
        ssl_keyfile="/certs/server.key",
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
```

(The body is parsed with `parse_qsl`, not Starlette's `request.form()`: that needs `python-multipart`, which the leader image does not have.)

- [ ] **Step 3: Write the leader's TLS wrapper and the databases**

Create `e2e/console-compose/serve_tls.py`:

```python
"""A real leader, served over TLS, for the console Compose test only.

The console calls leaders over https only, and `swarmscribe-leader serve` speaks plain HTTP
(in production TLS ends at the leader's ingress). There is no ingress here, so this starts
the same application `serve` starts, with the test CA's certificate. docker-compose.yml runs
`swarmscribe-leader migrate` first; nothing here is part of the leader image.

    python /e2e/serve_tls.py
"""

import logging.config

import uvicorn
from swarmscribe_leader.app import create_app
from swarmscribe_leader.config import Settings
from swarmscribe_leader.main import LOGGING

if __name__ == "__main__":
    logging.config.dictConfig(LOGGING)
    uvicorn.run(
        create_app(Settings()),
        host="0.0.0.0",
        port=8443,
        ssl_certfile="/certs/server.pem",
        ssl_keyfile="/certs/server.key",
        proxy_headers=True,
        access_log=False,
        log_config=None,
    )
```

Create `e2e/console-compose/initdb.sql`:

```sql
CREATE DATABASE console;
CREATE DATABASE leader_a;
CREATE DATABASE leader_b;
```

- [ ] **Step 4: Write the Compose file**

Create `e2e/console-compose/docker-compose.yml`:

```yaml
# The fleet console and everything it talks to (fleet console spec, section 9): Postgres,
# a stand-in for Entra ID, two real leaders with a database each, and the console image.
# Both images are built beforehand and run_e2e.py drives the test (see
# .github/workflows/ci.yml, job console-compose-e2e). Every value here is test-only.
name: swarmscribe-console-e2e

x-entra: &entra
  tenant: &tenant 0f0e0d0c-0b0a-4908-8706-050403020100
  console-client: &console-client 11111111-1111-4111-8111-111111111111
  console-secret: &console-secret console-compose-client-secret
  leader-client: &leader-client 22222222-2222-4222-8222-222222222222
  # The same group ids as in fake_idp.py (CONSOLE_ADMINS, LEADER_ADMINS).
  console-admins: &console-admins c0c0c0c0-0000-4000-8000-000000000001
  leader-admins: &leader-admins c0c0c0c0-0000-4000-8000-000000000003

x-leader-environment: &leader-environment
  SWARMSCRIBE_LINK_KEY: console-compose-link-key-0123456789abcdef
  SWARMSCRIBE_ENTRA_TENANT_ID: *tenant
  SWARMSCRIBE_ENTRA_CLIENT_ID: *leader-client
  SWARMSCRIBE_ROLE_ADMIN_ENTRA_GROUPS: *leader-admins
  # The test CA: the leaders fetch Entra's signing keys from the stand-in.
  SSL_CERT_FILE: /certs/ca.pem

x-leader: &leader
  image: swarmscribe-leader:e2e
  pull_policy: never
  command: ["python", "/e2e/serve_tls.py"]
  volumes:
    - ./work/certs:/certs:ro
    - ./serve_tls.py:/e2e/serve_tls.py:ro
  healthcheck:
    test:
      - CMD
      - python
      - -c
      - "import ssl, sys, urllib.request; c = ssl.create_default_context(cafile='/certs/ca.pem'); sys.exit(0 if urllib.request.urlopen('https://localhost:8443/readyz', timeout=2, context=c).status == 200 else 1)"
    interval: 2s
    timeout: 3s
    retries: 45

x-console: &console
  image: swarmscribe-console:e2e
  pull_policy: never
  # As in the Helm chart: a read-only root filesystem, no capabilities, and the image's own
  # non-root user.
  read_only: true
  cap_drop: ["ALL"]
  security_opt: ["no-new-privileges:true"]
  tmpfs: ["/tmp"]
  volumes:
    - ./work/certs:/certs:ro
  environment:
    SWARMSCRIBE_CONSOLE_DATABASE_URL: postgresql://postgres:postgres@postgres:5432/console
    SWARMSCRIBE_CONSOLE_PUBLIC_URL: http://localhost:18080
    # bytes 0..31; a test key.
    SWARMSCRIBE_CONSOLE_KEY: AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8
    SWARMSCRIBE_CONSOLE_ENTRA_TENANT_ID: *tenant
    SWARMSCRIBE_CONSOLE_ENTRA_CLIENT_ID: *console-client
    SWARMSCRIBE_CONSOLE_ENTRA_CLIENT_SECRET: *console-secret
    # Unset in Compose and Kubernetes arrives as an empty string: it must mean "none".
    SWARMSCRIBE_CONSOLE_GOOGLE_CLIENT_ID: ""
    # Leaders: the test CA, through the console's own setting (leader calls ignore
    # SSL_CERT_FILE). Identity provider: the standard variable.
    SWARMSCRIBE_CONSOLE_LEADER_CA_FILE: /certs/ca.pem
    SSL_CERT_FILE: /certs/ca.pem

services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: postgres
    volumes:
      - ./initdb.sql:/docker-entrypoint-initdb.d/initdb.sql:ro
    healthcheck:
      # Over TCP: the server that runs initdb.sql listens on the socket only, so this
      # passes only once the three databases exist.
      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U postgres -d leader_b"]
      interval: 2s
      timeout: 3s
      retries: 30

  fake-idp:
    image: swarmscribe-leader:e2e
    pull_policy: never
    command: ["python", "/e2e/fake_idp.py"]
    environment:
      FAKE_IDP_TENANT: *tenant
      FAKE_IDP_CONSOLE_CLIENT_ID: *console-client
      FAKE_IDP_CONSOLE_CLIENT_SECRET: *console-secret
      FAKE_IDP_CONSOLE_REDIRECT_URI: http://localhost:18080/auth/callback
      FAKE_IDP_LEADER_CLIENT_ID: *leader-client
    volumes:
      - ./work/certs:/certs:ro
      - ./fake_idp.py:/e2e/fake_idp.py:ro
    networks:
      default:
        # The console, the leaders and swarmscribe-admin reach Entra ID at its real name.
        aliases: ["login.microsoftonline.com"]
    ports:
      - "18443:443"
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - "import ssl, sys, urllib.request; c = ssl.create_default_context(cafile='/certs/ca.pem'); sys.exit(0 if urllib.request.urlopen('https://localhost/healthz', timeout=2, context=c).status == 200 else 1)"
      interval: 2s
      timeout: 3s
      retries: 30

  leader-a-migrate:
    image: swarmscribe-leader:e2e
    pull_policy: never
    command: ["swarmscribe-leader", "migrate"]
    environment: &leader-a-environment
      <<: *leader-environment
      SWARMSCRIBE_DATABASE_URL: postgresql://postgres:postgres@postgres:5432/leader_a
      SWARMSCRIBE_PUBLIC_URL: https://leader-a:8443
    depends_on:
      postgres:
        condition: service_healthy

  leader-a:
    <<: *leader
    environment: *leader-a-environment
    depends_on:
      leader-a-migrate:
        condition: service_completed_successfully
      fake-idp:
        condition: service_healthy

  leader-b-migrate:
    image: swarmscribe-leader:e2e
    pull_policy: never
    command: ["swarmscribe-leader", "migrate"]
    environment: &leader-b-environment
      <<: *leader-environment
      SWARMSCRIBE_DATABASE_URL: postgresql://postgres:postgres@postgres:5432/leader_b
      SWARMSCRIBE_PUBLIC_URL: https://leader-b:8443
    depends_on:
      postgres:
        condition: service_healthy

  leader-b:
    <<: *leader
    environment: *leader-b-environment
    depends_on:
      leader-b-migrate:
        condition: service_completed_successfully
      fake-idp:
        condition: service_healthy

  # `swarmscribe-admin`, for run_e2e.py: `docker compose run admin-cli ...`. Not started by
  # `up`.
  admin-cli:
    image: swarmscribe-leader:e2e
    pull_policy: never
    profiles: ["tools"]
    environment:
      SSL_CERT_FILE: /certs/ca.pem
    volumes:
      - ./work/certs:/certs:ro

  console-migrate:
    <<: *console
    command: ["migrate"]
    depends_on:
      postgres:
        condition: service_healthy

  # The documented bootstrap: the first console administrator, added from the command line.
  console-bootstrap:
    <<: *console
    command: ["admins", "add", "entra_group", *console-admins]
    depends_on:
      console-migrate:
        condition: service_completed_successfully

  console:
    <<: *console
    ports:
      - "18080:8080"
    depends_on:
      console-bootstrap:
        condition: service_completed_successfully
      fake-idp:
        condition: service_healthy
      leader-a:
        condition: service_healthy
      leader-b:
        condition: service_healthy
```

- [ ] **Step 5: Write the driver**

Create `e2e/console-compose/run_e2e.py`:

```python
"""Compose end-to-end scenario for the fleet console (fleet console spec, section 9).

Postgres, a stand-in for Entra ID (fake_idp.py), two real leaders with a database each, and
the real `swarmscribe-console` image running read-only as its non-root user. Everything is
done the way an operator does it:

1. the console's first administrator is added by `swarmscribe-console admins add` (the
   `console-bootstrap` service);
2. on each leader, a leader administrator signs in with `swarmscribe-admin login` and runs
   `swarmscribe-admin console create`;
3. a console administrator signs in through the browser flow, grants a role, and registers
   both leaders with those credentials;
4. the poller shows both leaders reachable and a proxied read works on each;
5. one leader is killed: within a minute it is shown unreachable, while the other keeps
   answering a proxied read and takes an action (a join token is created on it).

CI runs this after `docker compose up` (.github/workflows/ci.yml, job console-compose-e2e):

    python e2e/console-compose/run_e2e.py certs   # before `docker compose up`
    python e2e/console-compose/run_e2e.py run     # after it

Nothing here prints a console credential, a join token, a cookie or a CSRF token.
"""

import argparse
import datetime
import json
import re
import ssl
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fake_idp import FLEET_ADMINS

HERE = Path(__file__).resolve().parent
COMPOSE_FILE = HERE / "docker-compose.yml"
CERTS = HERE / "work" / "certs"
CONSOLE = "http://localhost:18080"  # SWARMSCRIBE_CONSOLE_PUBLIC_URL in docker-compose.yml
IDP = "https://localhost:18443"  # fake_idp.py, published for this script
ENTRA_HOST = "login.microsoftonline.com"
LEADERS = ("leader-a", "leader-b")
KILLED, SURVIVOR = LEADERS
SERVER_NAMES = (ENTRA_HOST, *LEADERS, "localhost")
UNREACHABLE_WITHIN_SECONDS = 60.0  # fleet console spec, section 1
READY_WITHIN_SECONDS = 180.0
SCRIPT = re.compile(r'<script[^>]*\ssrc="(/assets/[^"]+\.js)"')


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# --- certificates ---------------------------------------------------------------------


def write_certs(folder: Path) -> None:
    """The test's own CA and one server certificate for every TLS name in the Compose
    network: ca.pem, server.pem, server.key. Valid for a day; never used outside the test."""
    now = datetime.datetime.now(datetime.UTC)
    start, end = now - datetime.timedelta(minutes=5), now + datetime.timedelta(days=1)

    def name(common: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(name("SwarmScribe console Compose test CA"))
        .issuer_name(name("SwarmScribe console Compose test CA"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    server = (
        x509.CertificateBuilder()
        .subject_name(name(ENTRA_HOST))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(host) for host in SERVER_NAMES]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    folder.mkdir(parents=True, exist_ok=True)
    files = {
        "ca.pem": ca.public_bytes(serialization.Encoding.PEM),
        "server.pem": server.public_bytes(serialization.Encoding.PEM),
        "server.key": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    }
    for filename, content in files.items():
        path = folder / filename
        path.write_bytes(content)
        # Readable inside the containers whatever user they run as (the console's is 10001).
        # The key protects nothing: it is a day-long test key for names inside one network.
        path.chmod(0o644)
    folder.chmod(0o755)


# --- docker compose -------------------------------------------------------------------


def compose(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), *arguments],
        capture_output=True,
        text=True,
    )


def console_credential(leader: str) -> str:
    """A console credential made on `leader` the way an operator makes one: a leader
    administrator signs in (`swarmscribe-admin login`, a device sign-in that the stand-in
    Entra ID approves at once) and runs `console create`, in a one-off container on the
    Compose network. The credential is read from the command's JSON and never printed."""
    url = f"https://{leader}:8443"
    script = (
        f"swarmscribe-admin --leader {url} login --provider entra >&2 && "
        f"swarmscribe-admin --leader {url} --json console create --name fleet --max-role admin"
    )
    done = compose("run", "--rm", "-T", "--no-deps", "admin-cli", "sh", "-c", script)
    expect(
        done.returncode == 0,
        f"creating a console credential on {leader} failed:\n{done.stderr.strip()}",
    )
    try:
        credential = json.loads(done.stdout)["credential"]
    except (ValueError, KeyError, TypeError):
        raise AssertionError(f"`console create` on {leader} printed no credential") from None
    expect(isinstance(credential, str) and len(credential) == 43, "an odd console credential")
    return credential


def wait_for_container_health(deadline: float) -> None:
    """Docker's own view of the console container, from the image's HEALTHCHECK."""
    container = compose("ps", "-q", "console").stdout.strip()
    expect(bool(container), "the console container is not running")
    health = ""
    while time.monotonic() < deadline:
        done = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", container],
            capture_output=True,
            text=True,
        )
        health = done.stdout.strip()
        if health == "healthy":
            return
        time.sleep(2)
    raise AssertionError(f"Docker reports the console container {health or 'without a health'}")


# --- the browser ----------------------------------------------------------------------


class Browser:
    """A browser, as far as the console can tell. It keeps cookies and sends them back to
    http://localhost although they are Secure (browsers treat localhost as secure; Python's
    cookie jar does not, hence the jar by hand), follows no redirect on its own, and sends
    Origin and the CSRF token on every request that changes something."""

    def __init__(self, ca_file: Path) -> None:
        self.console = httpx.Client(base_url=CONSOLE, timeout=30.0, follow_redirects=False)
        self.provider = httpx.Client(
            verify=ssl.create_default_context(cafile=str(ca_file)),
            timeout=30.0,
            follow_redirects=False,
        )
        self.cookies: dict[str, str] = {}
        self.csrf_token: str | None = None

    def close(self) -> None:
        self.console.close()
        self.provider.close()

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if method not in ("GET", "HEAD"):
            headers["Origin"] = CONSOLE
            if self.csrf_token is not None:
                headers["X-CSRF-Token"] = self.csrf_token
        response = self.console.request(method, path, headers=headers, **kwargs)
        for header in response.headers.get_list("set-cookie"):
            first, *attributes = [part.strip() for part in header.split(";")]
            cookie, _, value = first.partition("=")
            expired = any(a.lower().replace(" ", "") == "max-age=0" for a in attributes)
            if expired or value in ("", '""'):
                self.cookies.pop(cookie, None)
            else:
                self.cookies[cookie] = value
        return response

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def sign_in(self, persona: str) -> httpx.Response:
        """The whole browser sign-in: the console's /auth/login, the provider's
        authorization page (the stand-in signs `persona` in at once), the console's callback.
        Returns the callback's answer."""
        started = self.get("/auth/login", params={"provider": "entra"})
        expect(started.status_code == 302, f"/auth/login answered {started.status_code}")
        at_provider = urlsplit(started.headers["location"])
        expect(
            at_provider.scheme == "https" and at_provider.netloc == ENTRA_HOST,
            "the console did not send the browser to Entra ID",
        )
        # The browser would resolve the Entra host itself; from here the stand-in is the
        # published port. Path and query are the console's, untouched.
        went = self.provider.get(
            f"{IDP}{at_provider.path}?{at_provider.query}&login_hint={persona}"
        )
        expect(went.status_code == 302, f"the identity provider answered {went.status_code}")
        back = urlsplit(went.headers["location"])
        expect(
            f"{back.scheme}://{back.netloc}" == CONSOLE and back.path == "/auth/callback",
            "the identity provider sent the browser somewhere else",
        )
        return self.get(f"{back.path}?{back.query}")


# --- the scenario ---------------------------------------------------------------------


def wait_until_ready(deadline: float) -> None:
    with httpx.Client(base_url=CONSOLE, timeout=5.0) as client:
        while time.monotonic() < deadline:
            try:
                if client.get("/readyz").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(1)
    raise AssertionError("the console never became ready")


def check_the_image_serves_the_web_app(browser: Browser) -> None:
    for path in ("/healthz", "/readyz"):
        expect(browser.get(path).status_code == 200, f"{path} is not answering 200")
    page = browser.get("/")
    expect(page.status_code == 200, f"the web app's page answered {page.status_code}")
    expect(
        "script-src 'self'" in page.headers.get("content-security-policy", ""),
        "the web app is served without the Content Security Policy",
    )
    script = SCRIPT.search(page.text)
    expect(script is not None, "the page loads no script from /assets")
    asset = browser.get(script.group(1))
    expect(asset.status_code == 200, "the page's script is not in the image")
    expect(
        "immutable" in asset.headers.get("cache-control", ""),
        "a content-hashed asset is not cached as immutable",
    )
    reloaded = browser.get("/leaders/leader-a")
    expect(
        reloaded.status_code == 200 and reloaded.text == page.text,
        "a route of the web app does not survive a reload",
    )
    expect(
        browser.get("/api/fleet").status_code == 401, "the API answers without a session"
    )


def fleet(browser: Browser) -> dict[str, dict]:
    answer = browser.get("/api/fleet")
    expect(answer.status_code == 200, f"/api/fleet answered {answer.status_code}")
    return {leader["name"]: leader for leader in answer.json()}


def wait_for_health(browser: Browser, wanted: dict[str, str], deadline: float) -> None:
    seen: dict[str, str] = {}
    while time.monotonic() < deadline:
        seen = {name: leader["health"] for name, leader in fleet(browser).items()}
        if all(seen.get(name) == health for name, health in wanted.items()):
            return
        time.sleep(1)
    raise AssertionError(f"expected {wanted}, the console shows {seen}")


def read_jobs(browser: Browser, leader: str) -> httpx.Response:
    return browser.get(f"/api/leaders/{leader}/jobs", params={"limit": "5"})


def run(kill_leader) -> float:
    """The scenario. Returns how many seconds after the kill the leader showed unreachable."""
    wait_until_ready(time.monotonic() + READY_WITHIN_SECONDS)
    credentials = {leader: console_credential(leader) for leader in LEADERS}

    stranger = Browser(CERTS / "ca.pem")
    browser = Browser(CERTS / "ca.pem")
    try:
        check_the_image_serves_the_web_app(browser)

        refused = stranger.sign_in("stranger")
        expect(refused.status_code == 403, "a person with no role in the console signed in")
        expect("__Host-swarmscribe-session" not in stranger.cookies, "a stranger got a session")

        signed_in = browser.sign_in("console-admin")
        expect(signed_in.status_code == 200, f"sign-in answered {signed_in.status_code}")
        session = browser.get("/api/session")
        expect(session.status_code == 200, "no session after signing in")
        expect(session.json()["console_admin"] is True, "the bootstrap administrator is not one")
        browser.csrf_token = session.json()["csrf_token"]

        granted = browser.post(
            "/api/admin/grants",
            json={
                "role": "admin",
                "scope": "all",
                "principal_kind": "entra_group",
                "principal": FLEET_ADMINS,
            },
        )
        expect(granted.status_code == 201, f"granting a role answered {granted.status_code}")
        for leader in LEADERS:
            registered = browser.post(
                "/api/admin/leaders",
                json={
                    "name": leader,
                    "base_url": f"https://{leader}:8443",
                    "labels": {"env": "compose"},
                    "credential": credentials[leader],
                },
            )
            expect(
                registered.status_code == 201,
                f"registering {leader} answered {registered.status_code}",
            )
            expect(
                credentials[leader] not in registered.text, "the API returned a credential"
            )

        wait_for_health(
            browser, dict.fromkeys(LEADERS, "reachable"), time.monotonic() + 60.0
        )
        for leader in LEADERS:
            jobs = read_jobs(browser, leader)
            expect(jobs.status_code == 200, f"reading {leader}'s jobs answered {jobs.status_code}")
        wait_for_container_health(time.monotonic() + 60.0)

        kill_leader(KILLED)
        killed_at = time.monotonic()
        while True:
            seen = fleet(browser)
            # Nothing else stops working while one leader is down (spec, section 1).
            still = read_jobs(browser, SURVIVOR)
            expect(
                still.status_code == 200,
                f"{SURVIVOR} stopped answering through the console ({still.status_code})",
            )
            if seen[KILLED]["health"] == "unreachable":
                break
            expect(
                time.monotonic() - killed_at < UNREACHABLE_WITHIN_SECONDS,
                f"{KILLED} is still shown {seen[KILLED]['health']} a minute after it died",
            )
            time.sleep(1)
        elapsed = time.monotonic() - killed_at
        expect(seen[SURVIVOR]["health"] == "reachable", f"{SURVIVOR} is not shown reachable")

        dead = read_jobs(browser, KILLED)
        expect(
            dead.status_code == 503 and dead.json()["code"] == "leader_unreachable",
            f"a read on the dead leader answered {dead.status_code}",
        )
        created = browser.post(
            f"/api/leaders/{SURVIVOR}/tokens",
            json={"pool": "default", "expires_in_seconds": 3600, "max_uses": 1},
        )
        expect(created.status_code == 201, f"creating a join token answered {created.status_code}")
        token_id = created.json()["id"]
        listed = browser.get(f"/api/leaders/{SURVIVOR}/tokens")
        expect(listed.status_code == 200, f"listing join tokens answered {listed.status_code}")
        expect(
            any(token["id"] == token_id for token in listed.json()),
            "the join token made through the console is not on the leader",
        )
    finally:
        stranger.close()
        browser.close()
    return elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description="SwarmScribe console Compose scenario")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("certs", help="write the test CA and server certificate (before up)")
    commands.add_parser("run", help="run the scenario against the running Compose project")
    args = parser.parse_args()

    if args.command == "certs":
        write_certs(CERTS)
        print(f"wrote {CERTS}")
        return 0

    def kill(leader: str) -> None:
        done = compose("kill", leader)
        expect(done.returncode == 0, f"could not kill {leader}: {done.stderr.strip()}")
        print(f"killed {leader}", flush=True)

    try:
        elapsed = run(kill)
    except AssertionError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(
        f"passed: both leaders reachable; {KILLED} shown unreachable {elapsed:.0f} s after it "
        f"was killed; {SURVIVOR} still answered a read and took an action"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the local tests to see them pass**

Run: `uv run pytest packages/console/tests/test_compose_stand_in.py -q`
Expected: 8 passed.

Run: `uv run ruff check .`
Expected: no errors.

- [ ] **Step 7: Ignore the work folder**

Add to `.gitignore`, after `e2e/compose/work/`:

```
e2e/console-compose/work/
```

- [ ] **Step 8: Add the Compose steps to the CI job**

In `.github/workflows/ci.yml`, replace the `console-compose-e2e` job from Task 4 with:

```yaml
  console-compose-e2e:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - name: Build the leader image
        run: docker build -t swarmscribe-leader:e2e -f e2e/compose/Dockerfile .
      - name: Build the console image
        run: docker build -t swarmscribe-console:e2e -f docker/console.Dockerfile .
      - name: Check the console image
        run: bash docker/check-console-image.sh swarmscribe-console:e2e
      - name: Write the test CA and certificate
        run: uv run python e2e/console-compose/run_e2e.py certs
      - name: Start Postgres, the stand-in identity provider, two leaders and the console
        run: docker compose -f e2e/console-compose/docker-compose.yml up -d
      - name: Run the scenario (kills leader-a)
        run: uv run python e2e/console-compose/run_e2e.py run
      - name: Show every service's logs
        if: failure()
        run: docker compose -f e2e/console-compose/docker-compose.yml logs --no-color
      - name: Tear down
        if: always()
        run: docker compose -f e2e/console-compose/docker-compose.yml --profile tools down -v
```

- [ ] **Step 9: Document the image and the test**

In `README.md`, insert the following directly before the `## Develop` heading (after the "Deployment note: egress" subsection):

````markdown
### Console image

`docker/console.Dockerfile` builds `swarmscribe-console`, the console's backend serving
the built web app. A Node stage builds `packages/console-web`; a Python stage installs the
console and the two packages it imports (leader and protocol) with `uv`; the final image
holds only that environment and `dist/` (`SWARMSCRIBE_CONSOLE_STATIC_DIR=/app/web`). It
carries no engine and no model libraries. It runs as user 10001, writes nothing (the root
filesystem can be read-only), listens on port 8080 and has a `HEALTHCHECK` on `/healthz`.
`serve` is the default command; `migrate` and `admins ...` are run by replacing the
arguments:

```
docker build -t swarmscribe-console -f docker/console.Dockerfile .
docker run --rm --env-file console.env swarmscribe-console migrate
docker run --rm --env-file console.env swarmscribe-console admins add email you@example.org
docker run -d --read-only --tmpfs /tmp -p 8080:8080 --env-file console.env swarmscribe-console
```

`console.env` holds the `SWARMSCRIBE_CONSOLE_*` variables from the table above. Put TLS in
front of it: the console's cookies are `Secure`, and its public URL must be `https://`
(plain `http` is accepted for localhost only).

The console answers two probes without a session. `/healthz` is 200 while the process
runs. `/readyz` is 200 when the database answers and its schema is this console's, or a
newer one (a rolling upgrade has migrated it and this replica is about to be replaced);
otherwise it is 503 with `database unreachable` or `database migrations are not current`.
Neither asks an identity provider or a leader, so their outages do not take the console
out of service.

### Console Compose test

`e2e/console-compose/` runs that image, read-only and as its own user, with Postgres, a
stand-in for Entra ID and two real leaders that have a database each. It does what an
operator does: the first console administrator is added with `swarmscribe-console admins
add`; a leader administrator signs in with `swarmscribe-admin login` and runs
`swarmscribe-admin console create` on each leader; a console administrator signs in
through the browser flow, grants a role and registers both leaders. Then one leader is
killed: within a minute the console shows it unreachable, while the other still answers a
proxied read and takes an action. It runs in GitHub Actions (job `console-compose-e2e`);
locally, with Docker:

```
docker build -t swarmscribe-leader:e2e -f e2e/compose/Dockerfile .
docker build -t swarmscribe-console:e2e -f docker/console.Dockerfile .
bash docker/check-console-image.sh swarmscribe-console:e2e
uv run python e2e/console-compose/run_e2e.py certs
docker compose -f e2e/console-compose/docker-compose.yml up -d
uv run python e2e/console-compose/run_e2e.py run
docker compose -f e2e/console-compose/docker-compose.yml --profile tools down -v
```

The test changes nothing in the console, the leader or the admin CLI to make this
possible. All three use Entra ID's fixed address, so inside the Compose network the
stand-in holds the name `login.microsoftonline.com` (a network alias) with a certificate
from a CA the test makes for itself (`run_e2e.py certs`), which the containers trust
through `SSL_CERT_FILE`. The leaders serve TLS from the same CA, and the console trusts it
for them through `SWARMSCRIBE_CONSOLE_LEADER_CA_FILE`.
````

- [ ] **Step 10: Commit, push, and watch the job**

```bash
git add e2e/console-compose packages/console/tests/test_compose_stand_in.py .github/workflows/ci.yml .gitignore README.md
git commit -m "Console Compose test: the image, two real leaders, one killed"
git push
```

Expected in GitHub Actions, job `console-compose-e2e`: the last line of "Run the scenario" is `passed: both leaders reachable; leader-a shown unreachable NN s after it was killed; leader-b still answered a read and took an action`, with NN at most 60 (about 45 is expected: three polls 14 to 15 seconds apart).

This job has never run anywhere: nothing on the Windows machine can run Compose. Expect to iterate on it. What to look at first when a step fails:

| Failure | Look at |
|---|---|
| `up -d` fails on `leader-a` unhealthy | the leader's log: `sign-in keys could not be fetched` means the leader cannot reach or does not trust the stand-in (`SSL_CERT_FILE`, the alias, the certificate's names) |
| `up -d` fails on `console-migrate` | the console image cannot reach Postgres, or the migration scripts are not in the installed wheel |
| `creating a console credential on leader-a failed` | the printed stderr of `swarmscribe-admin`: a refused endpoint means the alias or the CA is not visible in the `admin-cli` container |
| `the console shows {'leader-a': 'pending', ...}` | `last_error` in `/api/fleet`: `connect_error` means the console does not trust the leaders' certificate (`SWARMSCRIBE_CONSOLE_LEADER_CA_FILE`) or cannot resolve them |
| `leader-a is still shown reachable a minute after it died` | whether failures are timeouts (5 s each) rather than refusals; the bound is the spec's, so report it rather than raising the limit |

Do not weaken an assertion to get the job green. If the spec's one-minute bound does not hold with default settings, that is a finding for the owner.

---

## Self-Review

**Spec coverage.**
- Spec 8, "Container image `swarmscribe-console` (backend serving the built web app)": Task 4; proven serving the web app under its CSP, with immutable hashed assets and the SPA fallback, in Task 5 (`check_the_image_serves_the_web_app`).
- Spec 8, "The console runs anywhere that can reach the leaders over HTTPS": Task 2 (private CA) and Task 5 (the console calls the leaders over HTTPS only; the leaders never call the console).
- Spec 9, "Compose: console + two real leaders, one leader killed → shown unreachable, the other still operable": Task 5, with the spec's one-minute bound from section 1 asserted at default poll settings, a proxied read on the survivor on every loop, an action on the survivor after the kill, and `503 leader_unreachable` for the dead one.
- Spec 7: https-only leader URLs kept (Tasks 2, 5); credentials, tokens and cookies never printed by the driver (Task 5); CSP checked on the image's page (Task 5).
- C2 deferral, cap on pending sign-ins: Task 3 (and the ingress rate limit in C4b).
- C2 deferral, optional KMS key: ruled out of C4 (ruling 9).
- Health endpoints are not in the fleet console spec; they follow the master spec's section 11 and are needed by the HEALTHCHECK and the probes: Task 1.
- Helm values, the NetworkPolicy for the egress deferral, and the deployment guide: C4b.

**Placeholder scan.** No step defers its content; every new file is given whole, and every edit to an existing file shows the lines to add and where.

**Type consistency.** `leader_tls_context(ca_file: Path | None)` and `LeaderClient(transport=None, *, ca_file=None)` are used with those names in Task 2's tests and in `app.py`. `trim_pending_sign_ins(session, *, keep)` is used with that signature in Task 3's tests and in `api/auth.py`. `app.state.head_revision` is set in Task 1 and read by `readyz` and by `test_readyz_is_not_ready_when_the_schema_is_behind`. `fake_idp.create_app`'s five keyword arguments are the ones `main()` reads from `FAKE_IDP_*` and the ones `docker-compose.yml` sets; `test_the_compose_file_the_stand_in_and_the_driver_agree` pins the shared values. The driver's `CONSOLE`, `IDP`, `ENTRA_HOST`, `LEADERS` and `SERVER_NAMES` are the names that test reads.

**Review Focus.** Each of the six lines names its test; none is left to review alone.

**What was verified while writing, and what was not.** Run on a scratch copy outside the repository: Tasks 1 to 3's code and all their tests (29 passed), the whole console suite with those changes (890 passed), Task 5's local tests (8 passed), `ruff check` on every new Python file, the Python stage's `uv sync` commands, and `bash -n` on the image check. Not verified, because the machine has no Docker: the Dockerfile as a whole, `check-console-image.sh`'s Docker calls, `docker-compose.yml` beyond parsing as YAML, and the scenario in `run_e2e.py run`.
