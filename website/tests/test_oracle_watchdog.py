"""The Oracle watchdog repairs sustained failures and respects startup grace."""

import fcntl
from subprocess import CompletedProcess

from deploy.oracle import watchdog


class FakeCommands:
    def __init__(self):
        self.health = {service: "healthy" for service in watchdog.SERVICES}
        self.public_ok = True
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)
        if command[:2] == ["docker", "inspect"]:
            service = command[-1].removeprefix("website-").removesuffix("-1")
            return CompletedProcess(command, 0, f"running {self.health[service]}\n", "")
        if command[:2] == ["docker", "restart"]:
            service = command[-1].removeprefix("website-").removesuffix("-1")
            self.health[service] = "starting"
            return CompletedProcess(command, 0, "", "")
        if command[0] == "curl":
            if "--resolve" in command and not self.public_ok:
                return CompletedProcess(command, 22, "", "failed")
            return CompletedProcess(command, 0, "ok", "")
        if command[:2] == ["systemctl", "restart"]:
            self.public_ok = True
            return CompletedProcess(command, 0, "", "")
        raise AssertionError(command)


def test_restarts_only_after_two_failed_checks(tmp_path):
    commands = FakeCommands()
    commands.health["frontend"] = "unhealthy"
    watchdog.check_services(state_dir=tmp_path, runner=commands)
    assert not any(call[:2] == ["docker", "restart"] for call in commands.calls)
    watchdog.check_services(state_dir=tmp_path, runner=commands)
    assert [call[-1] for call in commands.calls if call[:2] == ["docker", "restart"]] == [
        "website-frontend-1"]
    watchdog.check_services(state_dir=tmp_path, runner=commands)
    assert not (tmp_path / "watchdog-frontend-failures").exists()


def test_success_clears_failure_and_proxy_only_restarts_with_healthy_upstreams(tmp_path):
    commands = FakeCommands()
    commands.health["backend"] = "unhealthy"
    commands.public_ok = False
    watchdog.check_services(state_dir=tmp_path, runner=commands)
    assert not any(call[:2] == ["systemctl", "restart"] for call in commands.calls)
    commands.health["backend"] = "healthy"
    watchdog.check_services(state_dir=tmp_path, runner=commands)
    assert not (tmp_path / "watchdog-backend-failures").exists()
    watchdog.check_services(state_dir=tmp_path, runner=commands)
    assert [call[-1] for call in commands.calls if call[:2] == ["systemctl", "restart"]] == [
        "nginx"]


def test_main_uses_existing_updater_lock_and_skips_active_deploy(tmp_path, monkeypatch):
    lock = tmp_path / "updater.lock"
    lock.write_bytes(b"")
    monkeypatch.setattr(watchdog, "LOCK", lock)
    checks = []
    monkeypatch.setattr(watchdog, "check_services", lambda: checks.append(True))
    assert watchdog.main() == 0
    assert checks == [True]
    with lock.open("rb") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert watchdog.main() == 0
    assert checks == [True]
