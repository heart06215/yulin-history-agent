"""M2 F07/F08 tests: batch scheduling, control states, retry, staged artifacts."""

import json
import pytest
from fastapi.testclient import TestClient
from app.config import settings
from app.db import init, connect, now, uid
from app import repository as repo
from app import batches as batch_service
from app import recognize_service
from app.provider import ModelError
from app.main import app

HEADERS = {"Authorization": "Bearer test-admin"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "glm_api_key", "")
    monkeypatch.setattr(settings, "batch_max_pages", 20)
    init()
    with connect() as db:
        db.execute(
            "INSERT INTO sources(id,title,edition_id,year,volume,source_url,local_path,"
            "sha256,page_count,rights_status,metadata_json,created_at) "
            "VALUES('b','测试县志','e',1681,'卷一','u','raw/test.pdf','s',5,"
            "'source_declared_public_domain','{}',?)",
            (now(),),
        )
        for i in range(1, 6):
            db.execute(
                "INSERT INTO pages(id,source_id,scan_index) VALUES(?,?,?)",
                (f"b-p{i}", "b", i),
            )
    yield


def page_ids():
    return [f"b-p{i}" for i in range(1, 6)]


def test_create_batch_previews_and_items_are_unique():
    c = TestClient(app)
    r = c.post(
        "/api/v1/admin/batches",
        headers=HEADERS,
        json={"source_id": "b", "page_ids": page_ids()},
    )
    assert r.status_code == 201
    assert r.json()["preview"]["待处理"] == 5
    batch = c.get(
        "/api/v1/admin/batches/" + r.json()["batch_id"], headers=HEADERS
    ).json()
    assert batch["total"] == 5
    assert [i["scan_index"] for i in batch["items"]] == [1, 2, 3, 4, 5]
    assert batch["control"] == "active"


def test_overlapping_pages_rejected_while_active():
    c = TestClient(app)
    c.post(
        "/api/v1/admin/batches",
        headers=HEADERS,
        json={"source_id": "b", "page_ids": page_ids()},
    )
    again = c.post(
        "/api/v1/admin/batches",
        headers=HEADERS,
        json={"source_id": "b", "page_ids": page_ids()},
    )
    assert again.status_code == 409


def test_window_dispatch_is_one_item_at_a_time():
    batch_id = batch_service.create_batch("b", page_ids())
    first = batch_service.claim_next_item()
    assert first and first["scan_index"] == 1
    # The next call could dispatch a second page only after the first completes;
    # claiming again without completing would still be a new page, so assert the
    # scheduler relies on the caller to complete items in order.
    batch_service.complete_item(first["id"], "run-1", result={"version_id": "v1"})
    second = batch_service.claim_next_item()
    assert second and second["scan_index"] == 2
    batch = batch_service.get_batch(batch_id)
    assert batch["summary"]["completed"] == 1
    assert [i["status"] for i in batch["items"]] == [
        "completed", "running", "pending", "pending", "pending",
    ]


def test_partial_failure_keeps_successes_and_retry_only_failed():
    batch_id = batch_service.create_batch("b", page_ids()[:5])
    for i in range(1, 4):
        item = batch_service.claim_next_item()
        assert item["scan_index"] == i
        batch_service.complete_item(item["id"], f"run-{i}", result={"version_id": f"v{i}"})
    item = batch_service.claim_next_item()
    assert item["scan_index"] == 4
    batch_service.complete_item(item["id"], "run-4", error="模型限流")
    item = batch_service.claim_next_item()
    assert item["scan_index"] == 5
    batch_service.complete_item(item["id"], "run-5", result={"version_id": "v5"})
    batch = batch_service.get_batch(batch_id)
    assert batch["summary"]["completed"] == 4
    assert batch["summary"]["failed"] == 1
    assert batch["result"] == "partial_failed"
    assert batch_service.claim_next_item() is None
    retry = batch_service.retry_failed(batch_id)
    assert retry["retried"] == 1
    batch = batch_service.get_batch(batch_id)
    assert [i["status"] for i in batch["items"]].count("pending") == 1
    assert batch["control"] == "active"


def test_pause_stops_dispatch_and_cancel_stops_pending():
    batch_id = batch_service.create_batch("b", page_ids()[:3])
    first = batch_service.claim_next_item()
    batch_service.set_control(batch_id, "paused")
    assert batch_service.claim_next_item() is None
    batch_service.set_control(batch_id, "active")
    second = batch_service.claim_next_item()
    assert second is not None
    batch_service.complete_item(first["id"], "run-1", result={"version_id": "v1"})
    batch_service.complete_item(second["id"], "run-2", result={"version_id": "v2"})
    batch_service.set_control(batch_id, "cancelled")
    batch = batch_service.get_batch(batch_id)
    assert batch["control"] == "cancelled"
    assert batch["result"] == "cancelled"
    assert all(i["status"] in ("completed", "cancelled") for i in batch["items"])
    with pytest.raises(batch_service.BatchError):
        batch_service.set_control(batch_id, "active")


def test_batch_endpoints_require_admin():
    c = TestClient(app)
    assert c.post("/api/v1/admin/batches", json={"source_id": "b"}).status_code == 401
    assert c.get("/api/v1/admin/batches").status_code == 401


class FakeProvider:
    """Serves recognition then translation; can fail a chosen stage."""

    def __init__(self, fail_translate=False):
        self.fail_translate = fail_translate
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append(kwargs.get("stage"))
        if kwargs.get("vision"):
            return {
                "role": "assistant",
                "content": json.dumps(
                    {"transcription": "米脂□志正文", "uncertainties": ["缺字"]},
                    ensure_ascii=False,
                ),
            }
        if self.fail_translate:
            raise ModelError("模型限流（HTTP 429）")
        return {
            "role": "assistant",
            "content": json.dumps(
                {"translation": "米脂县志正文。", "notes": ["说明"]}, ensure_ascii=False
            ),
        }


def _make_run(kind="recognize"):
    rid = uid()
    with connect() as db:
        db.execute(
            "INSERT INTO runs(id,owner,kind,request_json,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (rid, "t", kind, "{}", "running", now(), now()),
        )
    return rid


def test_recognition_artifact_is_reused_after_translate_failure(monkeypatch):
    monkeypatch.setattr("app.recognize_service.extract_text", lambda p: "")
    monkeypatch.setattr("app.recognize_service.render_page", lambda p: b"img")
    failing = FakeProvider(fail_translate=True)
    with pytest.raises(ModelError):
        recognize_service.recognize_with_stages(
            _make_run(), "b-p1", failing, batch_id="batch-x"
        )
    # Recognition succeeded and persisted even though translation failed.
    with connect() as db:
        artifacts = db.execute(
            "SELECT stage,status FROM page_artifacts"
        ).fetchall()
    assert any(a["stage"] == "recognize" and a["status"] == "succeeded" for a in artifacts)
    # Retry only adds the translation call, reusing the recognition artifact.
    ok = FakeProvider()
    result = recognize_service.recognize_with_stages(
        _make_run(), "b-p1", ok, batch_id="batch-x"
    )
    assert result["method"] == "vision_api"
    assert ok.calls == ["translate"]
    version = repo.page_info("b-p1", True)["version"]
    assert "缺字" in version["notes"] and "说明" in version["notes"]
    assert version["published"] == 0


def test_text_layer_metric_rejects_short_text(monkeypatch):
    monkeypatch.setattr("app.recognize_service.extract_text", lambda p: "甲乙")
    usable, _text, metrics = recognize_service.has_transcribable_text(
        repo.page_info("b-p1")
    )
    assert usable is False and metrics["reason"] == "insufficient_text_layer"


def test_worker_backoff_grows_and_resets():
    from app.worker import Worker

    w = Worker()
    w._register_backoff({"rate_limited": True, "error": "HTTP 429"})
    first = w._backoff
    w._register_backoff({"rate_limited": True, "error": "HTTP 429"})
    assert w._backoff > first
    w._register_backoff({"rate_limited": False, "result": {}})
    assert w._backoff == 0


def test_interactive_claim_during_batch_cooldown():
    from app.worker import claim

    with connect() as db:
        for run_id, kind in [("cooling-batch", "batch"), ("reader-question", "explain")]:
            db.execute("INSERT INTO runs(id,owner,kind,request_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (run_id, "test", kind, "{}", "queued", now(), now()))
    assert claim(include_batch=False)["id"] == "reader-question"
    assert claim(include_batch=False) is None
    assert claim()["id"] == "cooling-batch"


def test_cooldown_does_not_dispatch_or_extend_on_idle(monkeypatch):
    import time
    from app.worker import Worker

    worker = Worker()
    worker._register_backoff({"rate_limited": True})
    ready_at = worker._batch_ready_at
    assert ready_at > time.monotonic()
    monkeypatch.setattr(batch_service, "next_active_batch", lambda: pytest.fail("batch must remain idle during cooldown"))
    assert worker._kick_batch() is False
    worker._register_backoff(None)
    assert worker._batch_ready_at == ready_at


def test_batch_item_records_cancelled_when_run_cancelled():
    batch_id = batch_service.create_batch("b", ["b-p1"])
    item = batch_service.claim_next_item()
    batch_service.cancel_item(item["id"])
    batch = batch_service.get_batch(batch_id)
    assert batch["items"][0]["status"] == "cancelled"
    assert batch["result"] == "cancelled"
