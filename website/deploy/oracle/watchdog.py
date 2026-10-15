"""Repair sustained local service failures without racing a GitHub deployment."""

from __future__ import annotations

import fcntl
import subprocess
import sys
from pathlib import Path
from typing import Callable


LOCK = Path("/run/lock/pulse-update.lock")
STATE = Path("/var/lib/pulse")
SERVICES = ("backend", "worker", "backup", "frontend")
LOCAL_PROBES = {
    "backend": "http://127.0.0.1:8000/ready",
    "frontend": "http://127.0.0.1:3141/",
}
PUBLIC_PROBE = "https://pulseintelligence.tech/"
Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=70,
                          check=False)


def _curl(url: str, runner: Runner, *, public: bool = False) -> bool:
    command = ["curl", "--fail", "--silent", "--show-error", "--max-time", "5"]
    if public:
        command += ["--resolve", "pulseintelligence.tech:443:127.0.0.1"]
    return runner(command + [url]).returncode == 0


def _container_healthy(service: str, runner: Runner) -> bool | None:
    container = f"website-{service}-1"
    result = runner(["docker", "inspect", "--format",
                     "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
                     container])
    if result.returncode:
        return False
    fields = result.stdout.strip().split()
    if len(fields) != 2 or fields[0] != "running":
        return False
    if fields[1] == "starting":
        return None  # Docker's start period has not elapsed.
    if fields[1] != "healthy":
        return False
    url = LOCAL_PROBES.get(service)
    return url is None or _curl(url, runner)


def _repair_after_two_failures(name: str, healthy: bool, command: list[str],
                               state_dir: Path, runner: Runner) -> bool:
    counter = state_dir / f"watchdog-{name}-failures"
    if healthy:
        counter.unlink(missing_ok=True)
        return False
    failures = int(counter.read_text()) + 1 if counter.exists() else 1
    if failures < 2:
        counter.write_text(str(failures))
        print(f"{name}: first failed check; awaiting confirmation")
        return False
    result = runner(command)
    if result.returncode:
        raise RuntimeError(f"{name}: restart failed: {result.stderr.strip()}")
    counter.unlink(missing_ok=True)
    print(f"{name}: restarted after two failed checks")
    return True


def check_services(*, state_dir: Path = STATE, runner: Runner = run) -> None:
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    status = {}
    for service in SERVICES:
        healthy = _container_healthy(service, runner)
        status[service] = healthy
        if healthy is not None:
            _repair_after_two_failures(service, healthy,
                                       ["docker", "restart", f"website-{service}-1"],
                                       state_dir, runner)
    # Only blame the public proxy once its local upstreams respond.
    if status["backend"] is True and status["frontend"] is True:
        _repair_after_two_failures("nginx", _curl(PUBLIC_PROBE, runner, public=True),
                                   ["systemctl", "restart", "nginx"], state_dir, runner)


def main() -> int:
    try:
        # The updater creates this Ubuntu-owned file in the sticky /run/lock
        # directory. Open its existing inode without O_CREAT/O_TRUNC so Linux
        # protected_regular does not reject the root watchdog process.
        with LOCK.open("rb") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("deployment in progress; skipping watchdog check")
                return 0
            check_services()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"watchdog failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
