"""Compose end-to-end scenario for the fleet console (fleet console spec, section 9).

Postgres, a stand-in for Entra ID (fake_idp.py), two real leaders with a database each, and
the real `swarmscribe-console` image running read-only as its non-root user. Everything is
done the way an operator does it:

1. the console's first administrator is added by `swarmscribe-console admins add` (the
   `console-bootstrap` service);
2. on each leader, a leader administrator signs in with `swarmscribe-admin login` and runs
   `swarmscribe-admin console create` (a device sign-in the stand-in approves at once);
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
        encoding="utf-8",
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
            encoding="utf-8",
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
