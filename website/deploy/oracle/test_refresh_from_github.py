"""Exercise the updater's post-start rollback in an isolated Git fixture."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("refresh_from_github.sh")
BRANCH = "codex/production-readiness"


def run(*args: str, cwd: Path | None = None, env: dict | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, text=True,
                            capture_output=True, check=True)
    return result.stdout.strip()


def test_failed_container_start_restores_checkout_and_quarantines_commit():
    with tempfile.TemporaryDirectory(prefix="pulse-update-test-") as temporary:
        root = Path(temporary)
        remote = root / "remote.git"
        repo = root / "repo"
        state = root / "state"
        fake_bin = root / "bin"
        state.mkdir()
        fake_bin.mkdir()
        run("git", "init", "--bare", str(remote))
        run("git", "clone", str(remote), str(repo))
        run("git", "config", "user.name", "Updater Test", cwd=repo)
        run("git", "config", "user.email", "updater@example.invalid", cwd=repo)
        run("git", "checkout", "-b", BRANCH, cwd=repo)
        (repo / "website").mkdir()
        (repo / "website" / "old.txt").write_text("old\n")
        run("git", "add", ".", cwd=repo)
        run("git", "commit", "-m", "healthy", cwd=repo)
        base = run("git", "rev-parse", "HEAD", cwd=repo)
        run("git", "push", "-u", "origin", BRANCH, cwd=repo)
        (state / "deployed-commit").write_text(base + "\n")
        (repo / "website" / "new.txt").write_text("new\n")
        run("git", "add", ".", cwd=repo)
        run("git", "commit", "-m", "candidate", cwd=repo)
        target = run("git", "rev-parse", "HEAD", cwd=repo)
        run("git", "push", "origin", BRANCH, cwd=repo)
        run("git", "reset", "--hard", base, cwd=repo)

        docker_log = root / "docker.log"
        first_up = root / "first-up"
        (fake_bin / "docker").write_text(
            "#!/bin/bash\n"
            "echo \"$*\" >> \"$MOCK_DOCKER_LOG\"\n"
            "if [[ \"$1\" == inspect ]]; then\n"
            "  if [[ \"$*\" == *ExitCode* ]]; then echo 0; else echo healthy; fi\n"
            "  exit 0\n"
            "fi\n"
            "if [[ \"$1\" == exec && \"$*\" == *audit_signal_store* "
            "&& -n \"${MOCK_FAIL_AUDIT:-}\" ]]; then exit 1; fi\n"
            "if [[ \" $* \" == *' up '* && ! -e \"$MOCK_FIRST_UP\" ]]; then\n"
            "  touch \"$MOCK_FIRST_UP\"\n"
            "  exit 1\n"
            "fi\n")
        (fake_bin / "curl").write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  */api/v1/system/version*) "
            "printf '{\"runtime_revision\":\"%s\"}\\n' \"$PULSE_REVISION\" ;;\n"
            "esac\n")
        for name in ("python3", "sleep"):
            (fake_bin / name).write_text("#!/bin/sh\nexit 0\n")
        for executable in fake_bin.iterdir():
            executable.chmod(0o755)

        env = {**os.environ,
               "PATH": f"{fake_bin}:{os.environ['PATH']}",
               "PULSE_DEPLOY_REPO": str(repo),
               "PULSE_DEPLOY_BRANCH": BRANCH,
               "PULSE_DEPLOY_STATE_DIR": str(state),
               "PULSE_DEPLOY_LOCK_FILE": str(root / "update.lock"),
               "MOCK_DOCKER_LOG": str(docker_log),
               "MOCK_FIRST_UP": str(first_up)}
        failed = subprocess.run(("bash", str(SCRIPT)), env=env, text=True,
                                capture_output=True)
        assert failed.returncode != 0, failed.stdout + failed.stderr
        assert run("git", "rev-parse", "HEAD", cwd=repo) == base
        assert (state / "deployed-commit").read_text().strip() == base
        assert (state / "rejected-deploy-commit").read_text().strip() == target
        assert docker_log.read_text().count(" up -d --no-build") == 2

        previous_log = docker_log.read_text()
        rejected = subprocess.run(("bash", str(SCRIPT)), env=env, text=True,
                                  capture_output=True)
        assert rejected.returncode == 0
        assert run("git", "rev-parse", "HEAD", cwd=repo) == base
        assert docker_log.read_text() == previous_log

        run("git", "reset", "--hard", target, cwd=repo)
        (repo / "website" / "fixed.txt").write_text("fixed\n")
        run("git", "add", ".", cwd=repo)
        run("git", "commit", "-m", "fixed candidate", cwd=repo)
        fixed = run("git", "rev-parse", "HEAD", cwd=repo)
        run("git", "push", "origin", BRANCH, cwd=repo)
        run("git", "reset", "--hard", base, cwd=repo)
        recovered = subprocess.run(("bash", str(SCRIPT)), env=env, text=True,
                                   capture_output=True)
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        assert run("git", "rev-parse", "HEAD", cwd=repo) == fixed
        assert (state / "deployed-commit").read_text().strip() == fixed
        assert not (state / "rejected-deploy-commit").exists()
        assert "exec website-backend-1 python -m scripts.audit_signal_store" in docker_log.read_text()
        assert "exec website-backend-1 python -m scripts.audit_live_signal_api" in docker_log.read_text()

        (repo / "website" / "bad-audit.txt").write_text("bad audit\n")
        run("git", "add", ".", cwd=repo)
        run("git", "commit", "-m", "candidate with failing audit", cwd=repo)
        bad_audit = run("git", "rev-parse", "HEAD", cwd=repo)
        run("git", "push", "origin", BRANCH, cwd=repo)
        run("git", "reset", "--hard", fixed, cwd=repo)
        audit_failed = subprocess.run(("bash", str(SCRIPT)),
                                      env={**env, "MOCK_FAIL_AUDIT": "1"},
                                      text=True, capture_output=True)
        assert audit_failed.returncode != 0
        assert run("git", "rev-parse", "HEAD", cwd=repo) == fixed
        assert (state / "deployed-commit").read_text().strip() == fixed
        assert (state / "rejected-deploy-commit").read_text().strip() == bad_audit


def test_nested_markdown_update_keeps_healthy_containers():
    with tempfile.TemporaryDirectory(prefix="pulse-doc-update-test-") as temporary:
        root = Path(temporary)
        remote = root / "remote.git"
        repo = root / "repo"
        state = root / "state"
        fake_bin = root / "bin"
        state.mkdir()
        fake_bin.mkdir()
        run("git", "init", "--bare", str(remote))
        run("git", "clone", str(remote), str(repo))
        run("git", "config", "user.name", "Updater Test", cwd=repo)
        run("git", "config", "user.email", "updater@example.invalid", cwd=repo)
        run("git", "checkout", "-b", BRANCH, cwd=repo)
        doc = repo / "model" / "docs" / "NEWS_ONLY_INTEGRATION.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("original\n")
        run("git", "add", ".", cwd=repo)
        run("git", "commit", "-m", "healthy", cwd=repo)
        base = run("git", "rev-parse", "HEAD", cwd=repo)
        run("git", "push", "-u", "origin", BRANCH, cwd=repo)
        (state / "deployed-commit").write_text(base + "\n")
        doc.write_text("updated\n")
        run("git", "add", ".", cwd=repo)
        run("git", "commit", "-m", "documentation", cwd=repo)
        target = run("git", "rev-parse", "HEAD", cwd=repo)
        run("git", "push", "origin", BRANCH, cwd=repo)
        run("git", "reset", "--hard", base, cwd=repo)

        docker_log = root / "docker.log"
        (fake_bin / "docker").write_text(
            "#!/bin/sh\n"
            "echo \"$*\" >> \"$MOCK_DOCKER_LOG\"\n"
            "case \"$1\" in inspect) case \"$*\" in *ExitCode*) echo 0;; *) echo healthy;; esac;; esac\n")
        (fake_bin / "curl").write_text("#!/bin/sh\nexit 0\n")
        for executable in fake_bin.iterdir():
            executable.chmod(0o755)
        env = {**os.environ,
               "PATH": f"{fake_bin}:{os.environ['PATH']}",
               "PULSE_DEPLOY_REPO": str(repo),
               "PULSE_DEPLOY_BRANCH": BRANCH,
               "PULSE_DEPLOY_STATE_DIR": str(state),
               "PULSE_DEPLOY_LOCK_FILE": str(root / "update.lock"),
               "MOCK_DOCKER_LOG": str(docker_log)}
        updated = subprocess.run(("bash", str(SCRIPT)), env=env, text=True,
                                 capture_output=True)
        assert updated.returncode == 0, updated.stdout + updated.stderr
        assert "retaining the running containers" in updated.stdout
        assert (state / "deployed-commit").read_text().strip() == target
        logged = docker_log.read_text()
        assert "exec website-backend-1 python -m scripts.audit_signal_store" in logged
        assert " build " not in logged and " up " not in logged


if __name__ == "__main__":
    test_failed_container_start_restores_checkout_and_quarantines_commit()
    test_nested_markdown_update_keeps_healthy_containers()
    print("Updater rollback test passed")
