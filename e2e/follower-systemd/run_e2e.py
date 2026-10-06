"""The native Linux install of the follower, under a real systemd, against a real leader
(follower spec 8.3; plan F4a).

There is no Linux host to install on, so the "machine" is a container that runs systemd as
PID 1 (e2e/follower-systemd/Dockerfile: Debian 12, systemd, curl and uv, nothing of
SwarmScribe). The driver installs the follower in it exactly as the README tells an operator
to (wheels built from this repository, `uv tool install`, a service user, the two files in
/etc/swarmscribe-follower, the unit from deploy/systemd) and then checks what the unit does
when it is stopped, killed, drained, revoked, short of memory and unable to work.

    uv run python e2e/follower-systemd/run_e2e.py up      # wheels, images, leader, the machine
    uv run python e2e/follower-systemd/run_e2e.py run     # install, then the scenario
    uv run python e2e/follower-systemd/run_e2e.py down    # remove the containers and the network

`run` needs a fresh `up` each time (it drains and revokes). The leader is the leader's test
image with Postgres beside it, administered with the leader's own functions through
`docker exec` (e2e/follower-kind/admin.py), as the kind test does. Nothing here prints a
token, a credential or a link.

Environment: E2E_PREFIX names the containers, the network and the image (default
`follower-systemd`); LEADER_IMAGE is the leader's test image (`swarmscribe-leader:e2e`); UV is
how uv is run (`uv`; on the development machine `python -m uv`)."""

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DIST = HERE / "work" / "dist"
ADMIN = ROOT / "e2e" / "follower-kind" / "admin.py"
UNIT = ROOT / "deploy" / "systemd" / "swarmscribe-follower.service"
ENV_EXAMPLE = ROOT / "deploy" / "systemd" / "follower.env.example"
CONSTRAINTS = ROOT / "deploy" / "follower-constraints.txt"
PREFIX = os.environ.get("E2E_PREFIX", "follower-systemd")
NETWORK, POSTGRES, LEADER, MACHINE = (
    f"{PREFIX}-net", f"{PREFIX}-postgres", f"{PREFIX}-leader", f"{PREFIX}-machine",
)
MACHINE_IMAGE = f"{PREFIX}:e2e"
LEADER_IMAGE = os.environ.get("LEADER_IMAGE", "swarmscribe-leader:e2e")
UV = shlex.split(os.environ.get("UV", "uv"))
SERVICE = "swarmscribe-follower"
ENV_FILE = "/etc/swarmscribe-follower/follower.env"
DROP_IN = f"/etc/systemd/system/{SERVICE}.service.d/test.conf"
LEADER_ENV = {
    "SWARMSCRIBE_DATABASE_URL": f"postgresql://postgres:postgres@{POSTGRES}:5432/swarmscribe",
    # The leader builds its own file links from this, and the follower fetches them from the
    # machine: it must be the leader's name on the network (follower spec 12.6).
    "SWARMSCRIBE_PUBLIC_URL": f"http://{LEADER}:8080",
    "SWARMSCRIBE_LINK_KEY": "follower-systemd-link-key-0123456789abcdef",
    "SWARMSCRIBE_LEASE_SECONDS": "8",
    "SWARMSCRIBE_HEARTBEAT_SECONDS": "2",
    "SWARMSCRIBE_REAPER_INTERVAL_SECONDS": "1",
    "SWARMSCRIBE_SCANNER_INTERVAL_SECONDS": "1",
    "SWARMSCRIBE_CLAIM_RETRY_AFTER": "1",
    "SWARMSCRIBE_FOLLOWER_GONE_AFTER_SECONDS": "20",
}
INSTALL_ENV = {
    "UV_TOOL_DIR": "/opt/swarmscribe-follower/tools",
    "UV_TOOL_BIN_DIR": "/usr/local/bin",
    "UV_PYTHON_INSTALL_DIR": "/opt/swarmscribe-follower/python",
    "UV_COMPILE_BYTECODE": "1",
}
# Postgres needs a moment after its container starts.
MIGRATE = "for i in $(seq 60); do swarmscribe-leader migrate && exit 0; sleep 2; done; exit 1"
LONG_REPEATS = 96  # the 5-second speech fixture 96 times: eight minutes
STEP_SECONDS = 180.0


def expect(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAILED: {message}", file=sys.stderr)
        raise SystemExit(1)


def run(*command: str, stdin: str | None = None, check: bool = True) -> str:
    done = subprocess.run(command, input=stdin, capture_output=True, text=True)
    if check and done.returncode != 0:
        said = (done.stdout + done.stderr).strip()[-1500:]
        expect(False, f"`{' '.join(command[:6])} ...` exited {done.returncode}: {said}")
    return done.stdout


def docker(*arguments: str, **kwargs) -> str:
    return run("docker", *arguments, **kwargs)


def machine(script: str, *, stdin: str | None = None, check: bool = True) -> str:
    """Run a shell script as root in the machine."""
    return docker("exec", "-i", MACHINE, "bash", "-ec", script, stdin=stdin, check=check)


def admin(*arguments: str):
    source = ADMIN.read_text(encoding="utf-8")
    return json.loads(docker("exec", "-i", LEADER, "python", "-", *arguments, stdin=source))


def wait(what: str, probe, seconds: float = STEP_SECONDS, every: float = 1.0):
    deadline = time.monotonic() + seconds
    while True:
        found = probe()
        if found:
            return found
        expect(time.monotonic() < deadline, f"timed out after {seconds:.0f} s waiting for {what}")
        time.sleep(every)


def unit() -> dict[str, str]:
    shown = machine(
        f"systemctl show {SERVICE} -p ActiveState -p SubState -p Result -p ExecMainStatus"
        " -p NRestarts -p MainPID"
    )
    return dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)


def journal() -> str:
    return machine(f"journalctl -u {SERVICE} --no-pager -o cat")


def job(key: str) -> dict:
    return admin("state")["jobs"].get(f"talks/{key}", {"state": "absent", "tried": []})


def followers(state: str) -> list[dict]:
    return [row for row in admin("state")["followers"] if row["state"] == state]


def queue(key: str, repeats: int) -> None:
    admin("recording", "talks", key, str(repeats))


def held(key: str, seconds: float) -> None:
    """Wait until the follower holds `key`, and then `seconds` more: it is in the middle."""
    wait(f"{key} to be leased", lambda: job(key)["state"] == "leased")
    time.sleep(seconds)
    expect(job(key)["state"] == "leased", f"{key} was not held long enough to be interrupted")


def set_env(name: str, value: str) -> None:
    machine(f"sed -i '/^{name}=/d' {ENV_FILE}; echo '{name}={value}' >> {ENV_FILE}")


# --- up and down ------------------------------------------------------------------------------


def up() -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    for old in DIST.glob("*.whl"):
        old.unlink()
    for package in ("protocol", "engine", "follower"):
        run(*UV, "build", "--package", f"swarmscribe-{package}", "--wheel", "-o", str(DIST))
    docker("build", "-t", MACHINE_IMAGE, "-f", str(HERE / "Dockerfile"), str(HERE))
    down()
    docker("network", "create", NETWORK)
    docker(
        "run", "-d", "--name", POSTGRES, "--network", NETWORK,
        "-e", "POSTGRES_PASSWORD=postgres", "-e", "POSTGRES_DB=swarmscribe", "postgres:16",
    )
    leader_env = [part for name, value in LEADER_ENV.items() for part in ("-e", f"{name}={value}")]
    docker(
        "run", "--rm", "--network", NETWORK, *leader_env, LEADER_IMAGE,
        "sh", "-c", MIGRATE,
    )
    docker("run", "-d", "--name", LEADER, "--network", NETWORK, *leader_env, LEADER_IMAGE)
    ready = (
        "import urllib.request as u; "
        "print(u.urlopen('http://localhost:8080/readyz', timeout=2).status)"
    )
    wait(
        "the leader to be ready",
        lambda: "200" in docker("exec", LEADER, "python", "-c", ready, check=False),
        60,
    )
    # systemd as PID 1 needs a private cgroup namespace it may write to, and /run as tmpfs.
    docker(
        "run", "-d", "--name", MACHINE, "--network", NETWORK, "--privileged",
        "--cgroupns=private", "--tmpfs", "/run", "--tmpfs", "/run/lock", MACHINE_IMAGE,
    )
    wait(
        "systemd to be running in the machine",
        lambda: machine("systemctl is-system-running", check=False).strip()
        in ("running", "degraded"),
        60,
    )
    version = machine("systemctl --version").splitlines()[0]
    print(f"up: a leader at http://{LEADER}:8080 and a machine with {version}")


def down() -> None:
    for name in (MACHINE, LEADER, POSTGRES):
        docker("rm", "-f", "-v", name, check=False)
    docker("network", "rm", NETWORK, check=False)


# --- the install, as the README has it ----------------------------------------------------------


def install(token: str) -> None:
    machine("mkdir -p /dist")
    docker("cp", f"{DIST}{os.sep}.", f"{MACHINE}:/dist/")
    docker("cp", str(CONSTRAINTS), f"{MACHINE}:/dist/follower-constraints.txt")
    exports = " ".join(f"{name}={value}" for name, value in INSTALL_ENV.items())
    machine(
        f"{exports} uv tool install --python 3.12 --find-links /dist"
        " --constraints /dist/follower-constraints.txt swarmscribe-follower"
    )
    machine(
        "useradd --system --home-dir /var/lib/swarmscribe-follower --shell /usr/sbin/nologin"
        " swarmscribe-follower\n"
        "install -d -o root -g swarmscribe-follower -m 0750 /etc/swarmscribe-follower"
    )
    docker("cp", str(ENV_EXAMPLE), f"{MACHINE}:/tmp/follower.env")
    docker("cp", str(UNIT), f"{MACHINE}:/tmp/{SERVICE}.service")
    machine(
        f"install -o root -g swarmscribe-follower -m 0640 /tmp/follower.env {ENV_FILE}\n"
        f"install -o root -g root -m 0644 /tmp/{SERVICE}.service /etc/systemd/system/\n"
        "umask 027; cat > /etc/swarmscribe-follower/join-token\n"
        "chgrp swarmscribe-follower /etc/swarmscribe-follower/join-token",
        stdin=token,
    )
    set_env("SWARMSCRIBE_LEADER_URL", f"http://{LEADER}:8080")
    set_env("SWARMSCRIBE_FOLLOWER_ALLOW_HTTP", "1")  # the test leader is plain http
    set_env("SWARMSCRIBE_FOLLOWER_STARTUP_MODEL", "tiny.en")
    # Test-only: two cores, so that eight minutes of audio take long enough to interrupt;
    # and a restart after 2 s instead of the unit's 30.
    machine(
        f"mkdir -p $(dirname {DROP_IN})\n"
        f"printf '[Service]\\nCPUQuota=200%%\\nRestartSec=2\\n' > {DROP_IN}\n"
        f"systemctl daemon-reload\nsystemd-analyze verify /etc/systemd/system/{SERVICE}.service\n"
        f"systemctl enable --now {SERVICE}"
    )


# --- the scenario -----------------------------------------------------------------------------


def scenario() -> None:
    started = time.monotonic()
    token = admin("pool-token", "outside", "default")
    admin("profile", "cpu", "tiny.en", "int8")
    admin("location", "talks", "mono")
    install(token)
    del token

    # 1. It registers (after downloading tiny.en), as its own user, with nothing exposed.
    wait("the follower to register", lambda: followers("active"))
    registered = time.monotonic() - started
    first = followers("active")[0]["id"]
    expect(unit()["ActiveState"] == "active", f"the unit is not active: {unit()}")
    pid = unit()["MainPID"]
    owner = machine(f"ps -o user:32= -p {pid}").strip()
    expect(owner == "swarmscribe-follower", f"the follower runs as {owner}")
    modes = machine(
        "stat -c '%U %a' /var/lib/swarmscribe-follower /var/lib/swarmscribe-follower/state"
        " /var/lib/swarmscribe-follower/state/credential.json"
    ).split("\n")[:3]
    expect(
        modes == ["swarmscribe-follower 700"] * 2 + ["swarmscribe-follower 600"],
        f"the state folder or the credential is not private: {modes}",
    )
    exposed = machine(
        f"tr '\\0' '\\n' < /proc/{pid}/environ | grep -c '^SWARMSCRIBE_JOIN_TOKEN=' || true\n"
        f"systemctl show {SERVICE} -p Environment --value | grep -c TOKEN || true\n"
        "ss -Hltnp 2>/dev/null | grep -c python || true"
    ).split()
    expect(
        exposed == ["0", "0", "0"], f"a token in the environment, or a listening port: {exposed}"
    )

    # 2. A recording is transcribed.
    queue("short.wav", 1)
    wait("short.wav to complete", lambda: job("short.wav")["state"] == "completed")
    expect(bool(job("short.wav")["text"]), "short.wav has an empty transcript")

    # 3. A stop that fits the grace period (900 s): the recording in hand is finished first.
    queue("fits.wav", LONG_REPEATS)
    held("fits.wav", 4)
    began = time.monotonic()
    machine(f"systemctl stop {SERVICE}")
    stop_fits = time.monotonic() - began
    done = job("fits.wav")
    expect(
        (done["state"], done["attempts"]) == ("completed", 1),
        f"a stop with 900 s of grace did not finish the recording: {done}",
    )
    state = unit()
    ended = (state["ActiveState"], state["Result"], state["ExecMainStatus"])
    expect(ended == ("inactive", "success", "0"), f"after a stop the unit is {state}")

    # 4. A stop that does not fit (grace 1 s): the recording is handed back, no attempt counted.
    set_env("SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS", "1")
    machine(f"systemctl start {SERVICE}")
    queue("released.wav", LONG_REPEATS)
    held("released.wav", 4)
    began = time.monotonic()
    machine(f"systemctl stop {SERVICE}")
    stop_releases = time.monotonic() - began
    back = job("released.wav")
    expect(
        back["state"] == "queued" and back["tried"][-1][1] == "released",
        f"a stop with 1 s of grace did not release the recording: {back}",
    )
    expect(stop_releases < 15, f"the stop took {stop_releases:.1f} s")
    set_env("SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS", "900")

    # 5. Started again it is the same follower: the credential was kept, nothing registers.
    machine(f"systemctl start {SERVICE}")
    wait("released.wav to complete", lambda: job("released.wav")["state"] == "completed")
    again = job("released.wav")
    expect(again["attempts"] == 1, f"the released attempt was counted: {again}")
    expect(
        [row["id"] for row in admin("state")["followers"]] == [first]
        and journal().count('"event": "registered"') == 1,
        "the follower registered again after a restart",
    )

    # 6. Killed outright mid-job: systemd restarts it, and the recording is redone.
    queue("killed.wav", LONG_REPEATS)
    held("killed.wav", 3)
    machine(f"kill -9 {unit()['MainPID']}")
    wait("systemd to restart the follower", lambda: int(unit()["NRestarts"]) >= 1, 30)
    wait("killed.wav to complete", lambda: job("killed.wav")["state"] == "completed")
    redone = job("killed.wav")
    expect(
        [outcome for _, outcome in redone["tried"]] == ["expired", "completed"],
        f"the killed follower's recording was not redone once: {redone}",
    )

    # 7. MemoryMax= on the unit is the memory guard's limit.
    machine(
        f"echo 'MemoryMax=2500M' >> {DROP_IN}\n"
        f"systemctl daemon-reload\nsystemctl restart {SERVICE}"
    )
    admin("silence", "talks", "an-hour.wav", "3600")
    wait("the hour to be refused", lambda: job("an-hour.wav")["state"] == "failed")
    reason = job("an-hour.wav")["failure_reason"] or ""
    expect(
        "out_of_resources" in reason and "may use 2500 MiB" in reason and "cgroup" in reason,
        f"the hour was not refused by the unit's MemoryMax: {reason}",
    )

    # 8. Drained: it exits 0, and Restart=on-failure leaves it stopped.
    restarts = unit()["NRestarts"]
    admin("drain", first)
    wait("the drained follower to exit", lambda: unit()["ActiveState"] == "inactive", 60)
    time.sleep(5)
    state = unit()
    ended = (state["ActiveState"], state["ExecMainStatus"], state["NRestarts"])
    expect(ended == ("inactive", "0", restarts), f"a drained follower was started again: {state}")

    # 9. Revoked: exit 4, and RestartPreventExitStatus keeps it out.
    machine(f"rm /var/lib/swarmscribe-follower/state/credential.json\nsystemctl start {SERVICE}")
    wait("a second follower to register", lambda: followers("active"))
    admin("revoke-pool", "outside")
    wait("the revoked follower to exit", lambda: unit()["ActiveState"] == "failed", 60)
    time.sleep(5)
    state = unit()
    expect(
        (state["ExecMainStatus"], state["SubState"], state["NRestarts"]) == ("4", "failed", "0"),
        f"a revoked follower was restarted, or did not exit 4: {state}",
    )
    expect("this follower has been revoked" in journal(), "the journal does not say why")

    # 10. Exit 3 (no GPU where one is demanded) is restarted, but not for ever.
    set_env("SWARMSCRIBE_FOLLOWER_DEVICE", "cuda")
    machine(f"systemctl reset-failed {SERVICE}\nsystemctl start {SERVICE}", check=False)
    wait(
        "systemd to give up on exit 3",
        lambda: unit()["ActiveState"] == "failed" and "repeated too quickly" in journal(),
        90,
    )
    state = unit()
    expect(
        state["ExecMainStatus"] == "3" and int(state["NRestarts"]) >= 4,
        f"exit 3 was not restarted up to the start limit: {state}",
    )
    expect("cuda was requested" in journal(), "the journal does not say why")

    print(
        f"passed (tiny.en on cpu, systemd {machine('systemctl --version').split()[1]}):"
        f" installed with uv tool install and registered in {registered:.0f} s; a stop with"
        f" 900 s of grace finished the recording ({stop_fits:.0f} s) and one with 1 s released it"
        f" ({stop_releases:.1f} s, no attempt counted); a killed follower was restarted and its"
        " recording redone; MemoryMax= refused an hour; drain exited 0 and stayed stopped;"
        " revoke exited 4 and was not restarted; exit 3 was restarted"
        f" {state['NRestarts']} times, then left failed"
    )


def main() -> int:
    command = sys.argv[1] if len(sys.argv) == 2 else ""
    if command not in ("up", "run", "down"):
        print(__doc__, file=sys.stderr)
        return 2
    {"up": up, "run": scenario, "down": down}[command]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
