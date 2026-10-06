"""The native Windows install of the follower, against a real leader, WITHOUT administrator
rights (follower spec 8.3; plan F4b).

What it proves: `uv tool install` of the follower with its `cuda` extra, from wheels built
from this repository, on a Python that uv manages, in a folder of its own; that the GPU
libraries are found with nothing copied and no PATH; and the service's own code, started
exactly as the service control manager will start it (the real interpreter with
`service_boot.py`), in a console (`--foreground`): it registers, transcribes, hands a
recording back when it is stopped mid-job, comes back as the same follower, and says the
right thing to Windows when it is revoked and when the machine cannot do the work.

What it cannot prove: anything that needs the service control manager itself (the
registration, the service's account and its folders' permissions, the stop control arriving
from Windows, the recovery actions). That is the owner's procedure in the outcomes document.

    uv run python e2e/follower-windows/run_e2e.py up          # wheels, leader, the install
    uv run python e2e/follower-windows/run_e2e.py run         # on the GPU, large-v3
    uv run python e2e/follower-windows/run_e2e.py run --cpu   # on the CPU, tiny.en
    uv run python e2e/follower-windows/run_e2e.py down

For the owner's procedure with the real service (the outcomes document), against the same
leader: `token PATH` writes a new pool token into PATH and prints nothing of it,
`recording NAME REPEATS` queues the speech fixture REPEATS times over, and `state` prints the
followers and the recordings.

`run` needs a fresh `up` each time (it revokes). The leader is the leader's test image with
Postgres beside it, published on 127.0.0.1:18080 only and administered through `docker exec`
(e2e/follower-kind/admin.py). Models come from the user's own Hugging Face cache (large-v3 is
3 GB the first time). Nothing here prints a token, a credential or a link.

Environment: E2E_PREFIX names the containers and the network (default `follower-windows`);
LEADER_IMAGE (`swarmscribe-leader:e2e`); UV is how uv is run (`uv`; on the development
machine `python -m uv`)."""

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = HERE / "work"
DIST = WORK / "dist"
ADMIN = ROOT / "e2e" / "follower-kind" / "admin.py"
PREFIX = os.environ.get("E2E_PREFIX", "follower-windows")
NETWORK, POSTGRES, LEADER = f"{PREFIX}-net", f"{PREFIX}-postgres", f"{PREFIX}-leader"
LEADER_IMAGE = os.environ.get("LEADER_IMAGE", "swarmscribe-leader:e2e")
# `uv run` itself sets UV to the path of the uv.exe that runs it, with backslashes: split it as
# Windows text, not as POSIX shell text.
UV = [part.strip('"') for part in shlex.split(os.environ.get("UV", "uv"), posix=False)]
PORT = 18080
LEADER_URL = f"http://127.0.0.1:{PORT}"
LEADER_ENV = {
    "SWARMSCRIBE_DATABASE_URL": f"postgresql://postgres:postgres@{POSTGRES}:5432/swarmscribe",
    # The leader builds its own file links from this, and the follower fetches them from
    # Windows: it must be the published address (follower spec 12.6).
    "SWARMSCRIBE_PUBLIC_URL": LEADER_URL,
    "SWARMSCRIBE_LINK_KEY": "follower-windows-link-key-0123456789abcdef",
    "SWARMSCRIBE_LEASE_SECONDS": "8",
    "SWARMSCRIBE_HEARTBEAT_SECONDS": "2",
    "SWARMSCRIBE_REAPER_INTERVAL_SECONDS": "1",
    "SWARMSCRIBE_SCANNER_INTERVAL_SECONDS": "1",
    "SWARMSCRIBE_CLAIM_RETRY_AFTER": "1",
    "SWARMSCRIBE_FOLLOWER_GONE_AFTER_SECONDS": "20",
}
# Everything uv makes stays under work/: its Python, the tool's environment, the command.
UV_ENV = {
    "UV_PYTHON_INSTALL_DIR": str(WORK / "python"),
    "UV_TOOL_DIR": str(WORK / "tools"),
    "UV_TOOL_BIN_DIR": str(WORK / "bin"),
}
FOLLOWER = WORK / "bin" / "swarmscribe-follower.exe"
TOOL_PYTHON = WORK / "tools" / "swarmscribe-follower" / "Scripts" / "python.exe"
ENV_FILE = WORK / "follower.env"
TOKEN_FILE = WORK / "join-token"
OUTPUT = WORK / "service-output.txt"
# The follower this harness started: its pid and creation time, so that `down` can find a leftover
# of a failed run by what it is, never by a name.
PID_FILE = WORK / "service.pid"
# Postgres needs a moment after its container starts.
MIGRATE = "for i in $(seq 60); do swarmscribe-leader migrate && exit 0; sleep 2; done; exit 1"
LONG_REPEATS = 96  # the 5-second speech fixture 96 times: eight minutes
STEP_SECONDS = 300.0


def expect(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAILED: {message}", file=sys.stderr)
        raise SystemExit(1)


def run(*command: str, stdin: str | None = None, check: bool = True, env=None) -> str:
    done = subprocess.run(
        command, input=stdin, capture_output=True, text=True, env=env, encoding="utf-8",
        errors="replace",
    )
    if check and done.returncode != 0:
        said = (done.stdout + done.stderr).strip()[-1500:]
        expect(False, f"`{' '.join(command[:6])} ...` exited {done.returncode}: {said}")
    return done.stdout


def docker(*arguments: str, **kwargs) -> str:
    return run("docker", *arguments, **kwargs)


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


def job(key: str) -> dict:
    return admin("state")["jobs"].get(f"talks/{key}", {"state": "absent", "tried": []})


def followers(state: str) -> list[dict]:
    return [row for row in admin("state")["followers"] if row["state"] == state]


def uv_env() -> dict[str, str]:
    return {**os.environ, **UV_ENV}


def follower_env() -> dict[str, str]:
    """The environment of a follower started here: nothing of SwarmScribe's inherited."""
    return {
        name: value for name, value in os.environ.items() if not name.startswith("SWARMSCRIBE_")
    }


def write_env(**changes: str) -> None:
    values = {
        "SWARMSCRIBE_LEADER_URL": LEADER_URL,
        "SWARMSCRIBE_FOLLOWER_ALLOW_HTTP": "1",  # the test leader is plain http
        "SWARMSCRIBE_JOIN_TOKEN_FILE": str(TOKEN_FILE),
        "SWARMSCRIBE_FOLLOWER_STATE_DIR": str(WORK / "state"),
        "SWARMSCRIBE_FOLLOWER_SHUTDOWN_GRACE_SECONDS": "1",
        **changes,
    }
    ENV_FILE.write_text("".join(f"{name}={value}\n" for name, value in values.items()), "utf-8")


def service_image() -> list[str]:
    """The command `service install` registers, asked of the installed follower itself."""
    printed = run(
        str(TOOL_PYTHON), "-c",
        "import json; from swarmscribe_follower import windows; print(json.dumps(windows.image()))",
    )
    return json.loads(printed)


def creation_time(pid: int) -> int | None:
    """When the process `pid` was created (a FILETIME), or None when there is no such process."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        exited = wintypes.DWORD()
        kernel.GetExitCodeProcess(handle, ctypes.byref(exited))
        if exited.value != 259:  # STILL_ACTIVE
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


STARTED: list["Service"] = []  # every follower this run started, for the clean-up


class Service:
    """The service's code in a console of its own, started by the service's own command."""

    def __init__(self) -> None:
        self._out = OUTPUT.open("w", encoding="utf-8")
        STARTED.append(self)
        self.process = subprocess.Popen(
            [*service_image(), "--foreground", "--env-file", str(ENV_FILE)],
            stdout=self._out, stderr=subprocess.STDOUT, env=follower_env(),
            # A group of its own, so that the stop control below reaches it alone.
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        PID_FILE.write_text(f"{self.process.pid} {creation_time(self.process.pid)}", "utf-8")

    def stop_control(self) -> None:
        self.process.send_signal(signal.CTRL_BREAK_EVENT)  # what Ctrl+Break sends

    def ended(self, seconds: float = 60) -> int:
        try:
            code = self.process.wait(seconds)
        except subprocess.TimeoutExpired:
            self.process.kill()
            said = self.output()[-1500:]
            expect(False, f"the service did not end within {seconds:.0f} s:\n{said}")
        self._out.close()
        return code

    def stop(self) -> None:
        """Whatever state it is in, it is gone afterwards: terminate, then kill."""
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(10)
        self._out.close()

    def output(self) -> str:
        return OUTPUT.read_text(encoding="utf-8", errors="replace")

    def statuses(self) -> list[str]:
        return [
            line.removeprefix("service status: ")
            for line in self.output().splitlines()
            if line.startswith("service status: ")
        ]


# --- up and down ------------------------------------------------------------------------------


def up() -> None:
    expect(os.name == "nt", "this is the Windows test; on Linux run e2e/follower-systemd")
    down()
    DIST.mkdir(parents=True, exist_ok=True)
    for package in ("protocol", "engine", "follower"):
        run(*UV, "build", "--package", f"swarmscribe-{package}", "--wheel", "-o", str(DIST))
    # A Python of uv's own: the Microsoft Store's cannot run a service, and python.org's may
    # not be there. --no-bin and --no-registry: nothing outside work/ is touched.
    run(*UV, "python", "install", "3.12", "--no-bin", "--no-registry", env=uv_env())
    python = next((WORK / "python").glob("cpython-3.12.*-windows-*/python.exe"))
    run(
        *UV, "tool", "install", "--python", str(python), "--find-links", str(DIST),
        "--constraints", str(ROOT / "deploy" / "follower-constraints.txt"),
        "swarmscribe-follower[cuda]", env=uv_env(),
    )
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
    docker(
        "run", "-d", "--name", LEADER, "--network", NETWORK, "-p", f"127.0.0.1:{PORT}:8080",
        *leader_env, LEADER_IMAGE,
    )
    ready = (
        "import urllib.request as u; "
        "print(u.urlopen('http://localhost:8080/readyz', timeout=2).status)"
    )
    wait(
        "the leader to be ready",
        lambda: "200" in docker("exec", LEADER, "python", "-c", ready, check=False),
        60,
    )
    print(f"up: a leader at {LEADER_URL}; {run(str(FOLLOWER), '--version').strip()} in {WORK}")


def leftover_follower() -> None:
    """A follower of an earlier run that is still alive: found by the pid and creation time this
    harness wrote down (a reused pid has another creation time), and stopped."""
    if not PID_FILE.exists():
        return
    try:
        pid, created = (int(part) for part in PID_FILE.read_text("utf-8").split())
    except ValueError:
        pid, created = 0, 0
    if pid and creation_time(pid) == created:
        print(f"down: a follower of an earlier run (pid {pid}) was still running; stopping it")
        run("taskkill", "/PID", str(pid), "/T", "/F", check=False)
    PID_FILE.unlink()


def down() -> None:
    if WORK.exists():
        leftover_follower()
    for name in (LEADER, POSTGRES):
        docker("rm", "-f", "-v", name, check=False)
    docker("network", "rm", NETWORK, check=False)
    for name in ("state", "follower.env", "join-token", "service-output.txt"):
        path = WORK / name
        if path.is_dir():
            run("cmd", "/c", "rmdir", "/s", "/q", str(path), check=False)
        elif path.exists():
            path.unlink()


# --- the scenario -----------------------------------------------------------------------------


def scenario(cpu: bool) -> None:
    """The scenario; whatever happens in it, the followers it started are stopped afterwards."""
    try:
        run_scenario(cpu)
    finally:
        for started in STARTED:
            started.stop()
        if PID_FILE.exists():
            PID_FILE.unlink()


def run_scenario(cpu: bool) -> None:
    device, model, compute = ("cpu", "tiny.en", "int8") if cpu else ("cuda", "large-v3", "float16")
    base = {"SWARMSCRIBE_FOLLOWER_DEVICE": device, "SWARMSCRIBE_FOLLOWER_STARTUP_MODEL": model}
    TOKEN_FILE.write_text(admin("pool-token", "outside", "default"), encoding="utf-8")
    admin("profile", device, model, compute)
    admin("location", "talks", "mono")
    write_env(**base)

    # 1. The install: the GPU libraries are found where the wheel put them; nothing was copied
    #    beside CTranslate2, and PATH does not name them.
    image = service_image()
    expect("WindowsApps" not in image[0], f"the service's Python is the Store's: {image[0]}")
    if not cpu:
        paths = run(str(FOLLOWER), "cuda-paths", env=follower_env()).strip()
        expect("cublas" in paths and "cublas" not in os.environ["PATH"].lower(), paths)
        beside = run(
            str(TOOL_PYTHON), "-c",
            "import ctranslate2, pathlib;"
            "print(len(list(pathlib.Path(ctranslate2.__file__).parent.glob('cublas*'))))",
        ).strip()
        expect(beside == "0", "a cuBLAS DLL sits beside ctranslate2.dll: the test proves nothing")
    doctor = run(
        str(FOLLOWER), "--env-file", str(ENV_FILE), "doctor", env=follower_env(), check=False
    )
    expect(
        f"model: {model} ({compute}) loaded and ran" in doctor and "result: ready" in doctor
        and "state folder: ok" in doctor,
        f"doctor is not ready:\n{doctor}",
    )
    gpu = next(line for line in doctor.splitlines() if line.startswith("device:"))

    # 2. The service's code, started by the service's own command, registers.
    started = time.monotonic()
    service = Service()
    wait("the follower to register", lambda: followers("active") or service.process.poll())
    expect(service.process.poll() is None, f"the service ended:\n{service.output()[-1500:]}")
    registered = time.monotonic() - started
    first = followers("active")[0]
    expect(first["device"] == device, f"it registered as {first['device']}")
    expect(
        service.statuses()[:2]
        == ["START_PENDING accepts=0x0 exit=0 wait_hint_ms=30000 checkpoint=1",
            "RUNNING accepts=0x101 exit=0 wait_hint_ms=0 checkpoint=0"],
        f"Windows would have been told: {service.statuses()}",
    )

    # 3. A recording is transcribed.
    admin("recording", "talks", "short.wav", "1")
    wait("short.wav to complete", lambda: job("short.wav")["state"] == "completed")

    # 4. The stop control mid-job (grace 1 s): STOP_PENDING with a wait hint past the grace,
    #    the recording handed back, SERVICE_STOPPED without an error, exit 0.
    admin("recording", "talks", "released.wav", str(LONG_REPEATS))
    wait("released.wav to be leased", lambda: job("released.wav")["state"] == "leased")
    time.sleep(3)
    began = time.monotonic()
    service.stop_control()
    code = service.ended()
    stopped = time.monotonic() - began
    back = job("released.wav")
    expect(code == 0, f"a stopped service exited {code}:\n{service.output()[-1500:]}")
    expect(
        back["state"] == "queued" and back["tried"][-1][1] == "released",
        f"the stop control did not release the recording: {back}",
    )
    expect(
        service.statuses()[2:]
        == ["STOP_PENDING accepts=0x100 exit=0 wait_hint_ms=31000 checkpoint=1",
            "STOPPED accepts=0x0 exit=0 wait_hint_ms=0 checkpoint=0"],
        f"Windows would have been told: {service.statuses()}",
    )

    # 5. Started again it is the same follower, and the recording is done with one attempt.
    service = Service()
    wait(
        "released.wav to complete",
        lambda: job("released.wav")["state"] == "completed" or service.process.poll(),
    )
    expect(job("released.wav")["attempts"] == 1, f"the release was counted: {job('released.wav')}")
    expect(
        [row["id"] for row in admin("state")["followers"]] == [first["id"]],
        "the follower registered again after a restart",
    )

    # 6. Revoked: exit 4, told to Windows as SERVICE_STOPPED with service error 4, which its
    #    recovery actions leave alone.
    admin("revoke-pool", "outside")
    code = service.ended()
    expect(code == 4, f"a revoked service exited {code}")
    expect(
        service.statuses()[-1] == "STOPPED accepts=0x0 exit=4 wait_hint_ms=0 checkpoint=0",
        f"Windows would have been told: {service.statuses()}",
    )

    # 7. A machine that cannot do the work: exit 3 and NO SERVICE_STOPPED, so that Windows'
    #    recovery actions restart it (twice, then they leave it).
    without = {
        **base,
        "SWARMSCRIBE_FOLLOWER_OFFLINE": "1",
        "SWARMSCRIBE_FOLLOWER_STARTUP_MODEL": "small.en",
        "SWARMSCRIBE_FOLLOWER_MODEL_DIR": str(WORK / "no-models"),
    }
    write_env(**without)
    service = Service()
    code = service.ended()
    expect(code == 3, f"a follower without its model exited {code}:\n{service.output()[-800:]}")
    expect(
        not any(status.startswith("STOPPED") for status in service.statuses()),
        f"exit 3 was reported as a clean stop: {service.statuses()}",
    )

    print(
        f"passed ({model} on {device}; {gpu.removeprefix('device: ')}): installed with uv tool"
        f" install on uv's Python; the service's code registered in {registered:.0f} s; the"
        f" stop control mid-job released the recording and ended the service in {stopped:.1f} s"
        " with SERVICE_STOPPED; it came back as the same follower; revoked it said"
        " SERVICE_STOPPED with error 4; unable to work it exited 3 without SERVICE_STOPPED"
    )


# --- for the owner's procedure ----------------------------------------------------------------


def owner_token(path: str) -> None:
    """A new pool token into `path`; a profile for each device, as the scenario sets them."""
    name = f"owner-{int(time.time())}"  # a pool token's name is never used twice
    Path(path).write_text(admin("pool-token", name, "default"), encoding="utf-8")
    admin("profile", "cuda", "large-v3", "float16")
    admin("profile", "cpu", "tiny.en", "int8")
    docker(
        "exec", "-i", LEADER, "python", "-", "location", "talks", "mono",
        stdin=ADMIN.read_text(encoding="utf-8"), check=False,  # it may exist already
    )
    print(f"wrote a pool token to {path} (its name at the leader: {name})")


def owner_state() -> None:
    state = admin("state")
    if not state["followers"] and not state["jobs"]:
        print("no follower has registered and no recording is queued")
    for row in state["followers"]:
        print(f"follower {row['id'][:8]} {row['state']} {row['device']}")
    for key, found in sorted(state["jobs"].items()):
        outcomes = [outcome for _, outcome in found["tried"]]
        print(f"{key} {found['state']} attempts={found['attempts']} tried={outcomes}")


def main() -> int:
    arguments = sys.argv[1:]
    if arguments[:1] == ["up"] and len(arguments) == 1:
        up()
    elif arguments[:1] == ["down"] and len(arguments) == 1:
        down()
    elif arguments in (["run"], ["run", "--cpu"]):
        scenario(cpu="--cpu" in arguments)
    elif arguments[:1] == ["token"] and len(arguments) == 2:
        owner_token(arguments[1])
    elif arguments[:1] == ["recording"] and len(arguments) == 3:
        admin("recording", "talks", arguments[1], str(int(arguments[2])))
        print(f"queued talks/{arguments[1]}")
    elif arguments == ["state"]:
        owner_state()
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
