"""Offline deployment safeguards; no live model calls or real data mutations."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.config import Settings, settings, ConfigError
from app.main import app, admin, session
from app.security import SecurityMiddleware, redact_error

TOKEN = "0123456789abcdef" * 4
ORIGIN = "https://history.example.com"


@pytest.fixture(autouse=True)
def production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "public_origin", ORIGIN)
    monkeypatch.setattr(settings, "allowed_hosts", "")
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    monkeypatch.setattr(settings, "glm_api_key", "private-model-key-for-test")
    monkeypatch.setattr(settings, "text_api_key", "")
    monkeypatch.setattr(settings, "vision_api_key", "")
    monkeypatch.setattr(settings, "public_requests_per_minute", 12)
    monkeypatch.setattr(settings, "admin_requests_per_minute", 120)


def guarded_app():
    guarded = FastAPI()
    guarded.add_middleware(SecurityMiddleware)
    guarded.add_api_route("/api/v1/sessions", session, methods=["POST"])
    guarded.add_api_route("/api/v1/agent/runs", lambda: {"ok": True}, methods=["POST"])
    guarded.add_api_route("/api/v1/admin/records", lambda: {"ok": True}, methods=["GET", "POST"])
    guarded.add_api_route("/api/v1/health", lambda: {"status": "ok"})
    return guarded


def configured(**values):
    return Settings(_env_file=None, app_env="production", public_origin=ORIGIN, admin_token=TOKEN, **values)


def test_valid_config_and_secret_serialization():
    config = configured(glm_api_key="private-model-key-for-test")
    config.validate_deployment()
    assert config.admin_token not in repr(config)
    assert config.glm_api_key not in repr(config)
    assert "admin_token" not in config.model_dump()
    assert "glm_api_key" not in config.model_dump()


@pytest.mark.parametrize("origin", ["http://history.example.com", "https://history.example.com/path", "https://user:secret@history.example.com", "https://history.example.com/?key=secret", "https://history.example.com/#fragment", "https://history.example.com:bad", "https://[invalid", ""])
def test_invalid_production_origin_fails_closed(origin):
    config = Settings(_env_file=None, app_env="production", public_origin=origin, admin_token=TOKEN)
    with pytest.raises(ConfigError, match="PUBLIC_ORIGIN"):
        config.validate_deployment()


@pytest.mark.parametrize("token", ["", "test-admin", "x" * 64, "changeme-0123456789abcdef" * 2])
def test_weak_admin_token_fails_closed(token):
    config = Settings(_env_file=None, app_env="production", public_origin=ORIGIN, admin_token=token)
    with pytest.raises(ConfigError, match="ADMIN_TOKEN"):
        config.validate_deployment()


@pytest.mark.parametrize("address", ["http://provider.example/v1", "https://user:secret@provider.example/v1", "https://provider.example/v1?api_key=secret"])
def test_model_credentials_cannot_travel_in_insecure_urls(address):
    with pytest.raises(ConfigError, match="模型地址"):
        configured(glm_api_key="model-test-key", glm_base_url=address).validate_deployment()


def test_wildcard_hosts_rejected():
    with pytest.raises(ConfigError, match="通配符"):
        configured(allowed_hosts="*").validate_deployment()


@pytest.mark.parametrize("address", ["https://api.example.com:bad", "https://[invalid"])
def test_malformed_model_urls_fail_without_echoing_input(address):
    with pytest.raises(ConfigError, match="模型地址"):
        configured(glm_api_key="model-test-key", glm_base_url=address).validate_deployment()


def test_origin_uses_browser_canonical_default_port(monkeypatch):
    monkeypatch.setattr(settings, "public_origin", "https://History.Example.com:443/")
    settings.validate_deployment()
    assert settings.site_origin == ORIGIN
    response = TestClient(guarded_app(), base_url=ORIGIN).post("/api/v1/sessions", headers={"Origin": ORIGIN})
    assert response.status_code == 200


def test_cookie_secure_httponly_strict():
    client = TestClient(guarded_app(), base_url=ORIGIN)
    response = client.post("/api/v1/sessions", headers={"Origin": ORIGIN})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert "max-age=86400" in cookie


@pytest.mark.parametrize("path", ["/api/v1/sessions", "/api/v1/agent/runs", "/api/v1/admin/records"])
def test_foreign_origin_rejected_before_handlers(path):
    client = TestClient(guarded_app(), base_url=ORIGIN)
    assert client.post(path, headers={"Origin": "https://attacker.example", "Authorization": "Bearer " + TOKEN}).status_code == 403


def test_cross_site_fetch_without_origin_rejected():
    client = TestClient(guarded_app(), base_url=ORIGIN)
    assert client.post("/api/v1/sessions", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


@pytest.mark.parametrize("host", ["attacker.example", "user@history.example.com", "[invalid", "history.example.com:bad", "history.example.com/extra", "history.example.com?spoof", "history.example.com#spoof"])
def test_untrusted_or_malformed_host_rejected(host):
    client = TestClient(guarded_app(), base_url=ORIGIN)
    assert client.get("/api/v1/health", headers={"Host": host}).status_code == 400


def test_http_writes_are_rejected_but_health_is_readable():
    client = TestClient(guarded_app(), base_url="http://localhost")
    assert client.get("/api/v1/health").status_code == 200
    assert client.post("/api/v1/sessions").status_code == 400


def test_public_ip_limit_ignores_untrusted_forwarded_headers(monkeypatch):
    monkeypatch.setattr(settings, "public_requests_per_minute", 2)
    client = TestClient(guarded_app(), base_url=ORIGIN)
    assert client.post("/api/v1/sessions").status_code == 200
    assert client.post("/api/v1/agent/runs").status_code == 200
    response = client.post("/api/v1/agent/runs", headers={"X-Forwarded-For": "different-ip"})
    assert response.status_code == 429 and response.headers["retry-after"] == "60"
    assert response.headers["cache-control"] == "no-store"


def test_admin_read_limit(monkeypatch):
    monkeypatch.setattr(settings, "admin_requests_per_minute", 2)
    client = TestClient(guarded_app(), base_url=ORIGIN)
    assert client.get("/api/v1/admin/records").status_code == 200
    assert client.get("/api/v1/admin/records").status_code == 200
    assert client.get("/api/v1/admin/records").status_code == 429


def test_cache_and_security_headers():
    response = TestClient(guarded_app(), base_url=ORIGIN).get("/api/v1/health")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_error_redaction_and_validation_input_omission(monkeypatch):
    key = settings.glm_api_key
    redacted = redact_error(f"key={key}; Bearer {TOKEN}; https://example.com/?api_key=other-private-value")
    assert key not in redacted and TOKEN not in redacted and "other-private-value" not in redacted
    monkeypatch.setattr(settings, "app_env", "development")
    response = TestClient(app).post("/api/v1/agent/runs", json={"kind": key, "idempotency_key": "validation-test-key"})
    assert response.status_code == 422 and key not in response.text
    assert all("input" not in error for error in response.json()["detail"])


def test_non_ascii_authorization_is_an_auth_failure():
    with pytest.raises(HTTPException) as error:
        admin("Bearer 非法令牌")
    assert error.value.status_code == 401
