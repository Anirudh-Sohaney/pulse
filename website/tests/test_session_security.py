import pytest
import jwt
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import settings


def test_existing_hs256_token_remains_compatible():
    # Issued by python-jose before the PyJWT migration, using a test-only key.
    token = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
             "eyJzdWIiOiJsZWdhY3lfdXNlciIsImp0aSI6ImxlZ2FjeV9zZXNzaW9uIiw"
             "iZXhwIjoxODkzNDU2MDAwLCJjc3JmX2hhc2giOiJsZWdhY3lfaGFzaCJ9."
             "FNUPus3rqtUTkCNOG5nZEbQFctr32nbhF4cppAit9BA")
    payload = jwt.decode(token, "test-secret-for-legacy-compatibility-only",
                         algorithms=["HS256"])
    assert payload["sub"] == "legacy_user"
    assert payload["jti"] == "legacy_session"


def test_cookie_session_requires_csrf_for_writes_and_uses_argon2id(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))
    client = TestClient(create_app())
    response = client.post("/api/auth/register", json={
        "username": "secure_user", "password": "a-long-unique-password-123",
    })
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert "access_token" not in response.json()
    assert "httponly" in response.headers["set-cookie"].lower()
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.headers["cache-control"] == "no-store"
    session = client.post("/api/auth/session", data={
        "username": "secure_user", "password": "a-long-unique-password-123"})
    assert session.status_code == 200
    assert "access_token" not in session.json()
    token_response = client.post("/api/auth/token", data={
        "username": "secure_user", "password": "a-long-unique-password-123"})
    assert token_response.status_code == 200
    assert "set-cookie" not in token_response.headers
    token = token_response.json()["access_token"]
    import sqlite3
    with sqlite3.connect(tmp_path / "accounts.sqlite3") as db:
        stored = db.execute("SELECT password_hash FROM accounts WHERE username='secure_user'").fetchone()[0]
    assert stored.startswith("$argon2id$")
    assert client.post("/api/auth/logout").status_code == 403
    csrf = client.cookies.get("pulse_csrf")
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.post("/api/auth/logout", headers={"Authorization": f"bearer {token}"}).status_code == 200
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_existing_scrypt_and_bcrypt_accounts_upgrade_after_login(tmp_path, monkeypatch):
    import bcrypt
    import hashlib
    import secrets
    import sqlite3

    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))
    client = TestClient(create_app())
    account = {"username": "migrating_user", "password": "a-long-unique-password-123"}
    assert client.post("/api/auth/register", json=account).status_code == 201
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(account["password"].encode(), salt=salt,
                            n=2**14, r=8, p=1, dklen=32)
    old_scrypt = f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"
    with sqlite3.connect(tmp_path / "accounts.sqlite3") as db:
        db.execute("UPDATE accounts SET password_hash=? WHERE username=?",
                   (old_scrypt, account["username"]))
    assert client.post("/api/auth/session", data={
        **account, "password": "wrong-password"}).status_code == 401
    with sqlite3.connect(tmp_path / "accounts.sqlite3") as db:
        assert db.execute("SELECT password_hash FROM accounts").fetchone()[0] == old_scrypt
    assert client.post("/api/auth/session", data=account).status_code == 200
    with sqlite3.connect(tmp_path / "accounts.sqlite3") as db:
        assert db.execute("SELECT password_hash FROM accounts").fetchone()[0].startswith("$argon2id$")

    old_bcrypt = bcrypt.hashpw(account["password"].encode(), bcrypt.gensalt()).decode()
    with sqlite3.connect(tmp_path / "accounts.sqlite3") as db:
        db.execute("UPDATE accounts SET password_hash=? WHERE username=?",
                   (old_bcrypt, account["username"]))
    assert client.post("/api/auth/session", data=account).status_code == 200
    with sqlite3.connect(tmp_path / "accounts.sqlite3") as db:
        assert db.execute("SELECT password_hash FROM accounts").fetchone()[0].startswith("$argon2id$")


def test_sign_out_everywhere_revokes_cookie_and_bearer_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))
    first = TestClient(create_app())
    second = TestClient(create_app())
    account = {"username": "all_sessions", "password": "a-long-unique-password-123"}
    assert first.post("/api/auth/register", json=account).status_code == 201
    assert second.post("/api/auth/session", data=account).status_code == 200
    token = first.post("/api/auth/token", data=account).json()["access_token"]
    assert second.get("/api/auth/me").status_code == 200
    assert first.post("/api/auth/logout-all").status_code == 403
    csrf = first.cookies.get("pulse_csrf")
    response = first.post("/api/auth/logout-all", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.json() == {"logged_out": True, "sessions_revoked": 3}
    assert first.get("/api/auth/me").status_code == 401
    assert second.get("/api/auth/me").status_code == 401
    assert first.get("/api/auth/me", headers={
        "Authorization": f"Bearer {token}"}).status_code == 401
    assert second.post("/api/auth/session", data=account).status_code == 200


def test_failed_login_is_rate_limited(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))
    client = TestClient(create_app())
    for _ in range(5):
        response = client.post("/api/auth/token", data={"username": "nobody", "password": "wrong"})
        assert response.status_code == 401
    assert client.post("/api/auth/token", data={"username": "nobody", "password": "wrong"}).status_code == 429


def test_browser_auth_rejects_cross_origin_requests(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))
    client = TestClient(create_app())
    account = {"username": "origin_user", "password": "a-long-unique-password-123"}
    assert client.post("/api/auth/register", json=account,
                       headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post("/api/auth/register", json=account,
                       headers={"Origin": "http://testserver",
                                "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/auth/register", json=account,
                       headers={"Origin": "http://testserver",
                                "Sec-Fetch-Site": "same-origin"}).status_code == 201
    assert client.post("/api/auth/session", data=account,
                       headers={"Origin": "https://attacker.example"}).status_code == 403


def test_api_keys_are_one_time_visible_revocable_and_authorize_signal_history(tmp_path, monkeypatch):
    from backend import signal_store

    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))
    client = TestClient(create_app())
    signal_store.initialize()
    observed_at = datetime.now(timezone.utc).isoformat()
    with signal_store.connect() as db:
        signal_id = signal_store.ensure_source_definition(
            db, signal_id="api_key_test_signal", cadence="daily",
            geography_level="state", geography_id="AR", unit="index",
            source_name="Test source", source_url="https://example.test/source")
        db.execute("""INSERT INTO signal_records
            (row_hash, signal_uid, signal_date, observation_date, value,
             source_timestamp, ingested_at, data_quality, missingness,
             forecast_horizon, source_kind, source_url)
            VALUES ('api_key_test_row', ?, '2026-10-10', '2026-10-10', 7.5,
                    ?, ?, 'observed', '', '', 'test_source',
                    'https://example.test/source')""", (signal_id, observed_at, observed_at))
    account = {"username": "api_key_owner", "password": "a-long-unique-password-123"}
    assert client.post("/api/auth/register", json=account).status_code == 201
    csrf = client.cookies.get("pulse_csrf")
    created = client.post("/api/auth/api-keys", json={"name": "nightly job"},
                          headers={"X-CSRF-Token": csrf})
    assert created.status_code == 201
    body = created.json()
    assert body["key"].startswith("pulse_live_")
    assert body["scope"] == "signals:read"
    key = body["key"]
    listed = client.get("/api/auth/api-keys").json()["keys"]
    assert listed[0]["key_prefix"] == body["key_prefix"]
    assert "key" not in listed[0]
    headers = {"X-API-Key": key}
    history = client.post("/api/v1/signals/history", headers=headers,
                          json={"ids": [signal_id], "date": "2026-10-10"})
    assert history.status_code == 200
    assert history.json()["values"][0]["value"] == 7.5
    today = client.get("/api/v1/signals/today?limit=1500&offset=0", headers=headers)
    assert today.status_code == 200
    assert today.json()["snapshot_date"]
    assert next(row for row in today.json()["values"] if row["id"] == signal_id)["value"] == 7.5
    study = client.get("/api/v1/signals/demand/horizon-study", headers=headers)
    assert study.status_code == 200
    assert study.json()["drug_count"] == 30
    assert client.delete(f"/api/auth/api-keys/{body['id']}",
                         headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/v1/signals/today", headers=headers).status_code == 401


def test_api_key_daily_limit_applies_to_keyed_signal_requests(tmp_path, monkeypatch):
    from backend.routes import auth

    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))
    monkeypatch.setattr(auth, "API_KEY_DAILY_LIMIT", 1)
    client = TestClient(create_app())
    assert client.post("/api/auth/register", json={
        "username": "api_key_quota", "password": "a-long-unique-password-123",
    }).status_code == 201
    created = client.post("/api/auth/api-keys", json={"name": "quota test"},
                          headers={"X-CSRF-Token": client.cookies.get("pulse_csrf")})
    key = created.json()["key"]
    headers = {"X-API-Key": key}
    assert client.get("/api/v1/signals/freshness", headers=headers).status_code == 200
    limited = client.get("/api/v1/signals/freshness", headers=headers)
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "86400"
def test_hosted_same_origin_auth_uses_public_origin_behind_http_proxy(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "COOKIE_SECURE", True)
    monkeypatch.setattr(settings, "PUBLIC_ORIGIN", "https://pulse.example")
    monkeypatch.setattr(settings, "CORS_ORIGINS", [])
    client = TestClient(create_app(), base_url="http://internal-api:8000")
    account = {"username": "proxy_user", "password": "a-long-unique-password-123"}
    headers = {"Origin": "https://pulse.example", "Sec-Fetch-Site": "same-origin"}
    assert client.post("/api/auth/register", json=account).status_code == 403
    assert client.post("/api/auth/register", json=account, headers=headers).status_code == 201
    assert client.post("/api/auth/session", data=account).status_code == 403
    assert client.post("/api/auth/session", data=account, headers=headers).status_code == 200
    assert client.post("/api/auth/token", data=account).status_code == 200
    assert client.post("/api/auth/session", data=account, headers={
        "Origin": "https://attacker.example", "Sec-Fetch-Site": "same-origin"}).status_code == 403
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["https://other.example"])
    assert client.post("/api/auth/session", data=account, headers={
        "Origin": "https://other.example"}).status_code == 403


def test_registration_and_distributed_username_guesses_are_limited(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))
    client = TestClient(create_app())
    account = {"username": "signup_user", "password": "a-long-unique-password-123"}
    assert client.post("/api/auth/register", json=account).status_code == 201
    for _ in range(4):
        assert client.post("/api/auth/register", json=account).status_code == 409
    assert client.post("/api/auth/register", json=account).status_code == 429
    for number in range(30):
        response = client.post("/api/auth/token", data={
            "username": f"guess_{number}", "password": "wrong"})
        assert response.status_code == 401
    assert client.post("/api/auth/token", data={
        "username": "guess_31", "password": "wrong"}).status_code == 429


def test_registration_limit_is_atomic_under_concurrent_requests(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi import HTTPException
    from backend.routes.auth import _reserve_registration_attempt

    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))

    def attempt(_):
        try:
            _reserve_registration_attempt("198.51.100.9")
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(12)))
    assert results.count(200) == 5
    assert results.count(429) == 7


def test_compose_proxy_hostname_trusts_only_the_resolved_peer(monkeypatch):
    import socket
    from starlette.requests import Request
    from backend.routes import auth

    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", [])
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOSTNAMES", ["frontend"])
    monkeypatch.setattr(auth.socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("172.20.0.5", 0))])

    def request(peer, forwarded):
        return Request({"type": "http", "method": "POST", "path": "/api/auth/session",
                        "headers": [(b"x-real-ip", forwarded.encode())],
                        "client": (peer, 1234), "server": ("backend", 8000),
                        "scheme": "http"})

    assert auth._client_ip(request("172.20.0.5", "198.51.100.42")) == "198.51.100.42"
    assert auth._client_ip(request("172.20.0.6", "198.51.100.42")) == "172.20.0.6"
    assert auth._client_ip(request("172.20.0.5", "not-an-ip")) == "172.20.0.5"


def test_account_database_is_owner_only_even_when_existing_mode_is_open(tmp_path, monkeypatch):
    import stat
    from backend.routes.auth import _connect

    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))
    with _connect():
        pass
    path = tmp_path / "accounts.sqlite3"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.chmod(0o644)
    with _connect():
        pass
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_local_signing_key_is_atomic_across_concurrent_starts(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import stat
    from backend.config import _load_local_secret

    path = tmp_path / ".jwt_secret"
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(_load_local_secret, [path] * 16))
    assert len(set(values)) == 1
    assert len(values[0]) >= 48
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(tmp_path.glob(".jwt_secret.*")) == []


@pytest.mark.parametrize("override", [
    {"SECRET_KEY": "short"},
    {"SECRET_KEY": "a" * 32},
    {"COOKIE_SECURE": False},
    {"CORS_ORIGINS": ["http://localhost:3141"]},
    {"CORS_ORIGINS": ["http://example.com"]},
    {"CORS_ORIGINS": ["https://*.example.com"]},
    {"DEBUG": True},
    {"ACCESS_TOKEN_EXPIRE_MINUTES": 0},
    {"ACCESS_TOKEN_EXPIRE_MINUTES": 1441},
    {"ENABLE_LEGACY_SERVER_TRAINING": True},
    {"PUBLIC_ORIGIN": ""},
    {"PUBLIC_ORIGIN": "http://pulse.example"},
    {"PUBLIC_ORIGIN": "https://*.example.com"},
    {"PUBLIC_ORIGIN": "https://pulse.example:bad"},
    {"PUBLIC_ORIGIN": "https://pulse.example:0"},
])
def test_hosted_configuration_rejects_insecure_settings(override):
    from backend.config import _validate_production_configuration

    hosted = settings.model_copy(update={
        "ENVIRONMENT": "production", "SECRET_KEY": "0123456789abcdef" * 3,
        "COOKIE_SECURE": True, "CORS_ORIGINS": [], "DEBUG": False,
        "PUBLIC_ORIGIN": "https://pulse.example",
        "ACCESS_TOKEN_EXPIRE_MINUTES": 30,
        "ENABLE_LEGACY_SERVER_TRAINING": False,
        **override,
    })
    with pytest.raises(RuntimeError):
        _validate_production_configuration(hosted)


def test_hosted_configuration_accepts_same_origin_https_setup():
    from backend.config import _validate_production_configuration

    hosted = settings.model_copy(update={
        "ENVIRONMENT": "production", "SECRET_KEY": "0123456789abcdef" * 3,
        "COOKIE_SECURE": True, "CORS_ORIGINS": ["https://pulse.example"],
        "PUBLIC_ORIGIN": "https://pulse.example",
        "DEBUG": False, "ACCESS_TOKEN_EXPIRE_MINUTES": 30,
        "ENABLE_LEGACY_SERVER_TRAINING": False,
    })
    _validate_production_configuration(hosted)


def test_file_based_signing_key_is_loaded_before_production_validation(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from backend.config import _load_configured_secret_file, _validate_production_configuration

    key_file = tmp_path / "signing_key"
    key_file.write_text("0123456789abcdef" * 3 + "\n", encoding="utf-8")
    hosted = settings.model_copy(update={
        "ENVIRONMENT": "production", "SECRET_KEY": "", "SECRET_KEY_FILE": str(key_file),
        "COOKIE_SECURE": True, "CORS_ORIGINS": [], "PUBLIC_ORIGIN": "https://pulse.example",
    })
    _load_configured_secret_file(hosted)
    _validate_production_configuration(hosted)
    assert hosted.SECRET_KEY == "0123456789abcdef" * 3

    hosted.SECRET_KEY = "also-set-in-environment"
    with pytest.raises(RuntimeError, match="either SECRET_KEY"):
        _load_configured_secret_file(hosted)

    environment = os.environ.copy()
    environment.update({
        "ENVIRONMENT": "production", "SECRET_KEY": "", "SECRET_KEY_FILE": str(key_file),
        "COOKIE_SECURE": "true", "CORS_ORIGINS": "[]",
        "PUBLIC_ORIGIN": "https://pulse.example", "DATA_PATH": str(tmp_path),
    })
    startup = subprocess.run(
        [sys.executable, "-c", "from backend.config import settings; assert len(settings.SECRET_KEY) == 48"],
        cwd=Path(__file__).resolve().parents[1], env=environment,
        capture_output=True, text=True, check=False,
    )
    assert startup.returncode == 0, startup.stderr
