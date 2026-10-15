"""M2 F21/F22 tests: provider resolution, capability checks, usage summary."""

import pytest
from fastapi.testclient import TestClient
from app.config import settings, ConfigError
from app.db import init, connect, now
from app.provider import Provider, ModelError, day_start_utc
from app.main import app
from app import repository as repo

HEADERS = {"Authorization": "Bearer test-admin"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "glm_api_key", "glm-fake-key-value")
    monkeypatch.setattr(settings, "text_api_key", "")
    monkeypatch.setattr(settings, "text_base_url", "")
    monkeypatch.setattr(settings, "text_model", "")
    monkeypatch.setattr(settings, "text_supports_tools", True)
    init()
    yield


def run_id():
    rid = "run-" + now()
    with connect() as db:
        db.execute(
            "INSERT INTO runs(id,owner,kind,request_json,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (rid, "o", "explain", "{}", "running", now(), now()),
        )
    return rid


def test_text_provider_falls_back_to_glm():
    assert settings.text_provider["supplier"] == "glm"
    assert settings.text_provider["model"] == settings.glm_text_model


def test_partial_text_config_is_rejected():
    settings.text_api_key = "x"
    with pytest.raises(ConfigError):
        _ = settings.text_provider
    c = TestClient(app)
    r = c.get("/api/v1/health")
    assert r.status_code == 200 and r.json()["config_error"]


def test_full_text_config_takes_priority():
    settings.text_api_key = "t-key"
    settings.text_base_url = "https://text.example/v1"
    settings.text_model = "text-custom"
    provider = settings.text_provider
    assert provider["supplier"] == "text" and provider["model"] == "text-custom"


def test_capability_mismatch_blocks_before_request():
    settings.text_supports_tools = False
    with pytest.raises(ModelError, match="工具调用"):
        Provider(run_id()).complete(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
        )


def test_model_config_endpoint_masks_secrets():
    c = TestClient(app)
    assert c.get("/api/v1/admin/model-config").status_code == 401
    body = c.get("/api/v1/admin/model-config", headers=HEADERS).json()
    assert body["text"]["configured"] is True
    assert "glm-fake-key-value" not in body["text"]["api_key"]
    assert body["limits"]["budget_timezone"] == settings.budget_timezone


def test_usage_summary_reports_nonzero_and_no_fake_zero_cost():
    rid = run_id()
    with connect() as db:
        db.execute(
            "INSERT INTO model_calls(run_id,model,status,stage,supplier,started_at,created_at,input_tokens,output_tokens,error_class) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (rid, "m", "completed", "plan", "glm", now(), now(), 10, 5, ""),
        )
        db.execute(
            "INSERT INTO model_calls(run_id,model,status,stage,supplier,started_at,created_at,error_class) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (rid, "m", "failed", "recognize", "vision", now(), now(), "http_error"),
        )
    summary = repo.usage_summary()
    assert summary["calls_today"] == 2
    assert summary["call_status"]["completed"] == 1
    assert any(f["error_class"] == "http_error" for f in summary["failures_by_class"])
    # No price configuration: cost is not reported as a real zero.
    assert summary["pricing_configured"] is False
    c = TestClient(app)
    body = c.get("/api/v1/admin/usage", headers=HEADERS).json()
    assert body["calls_today"] == 2
    assert body["calls_remaining"] == settings.daily_model_call_limit - 2


def test_day_boundary_is_stable_format():
    assert day_start_utc().endswith("+00:00")
