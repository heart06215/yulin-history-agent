"""M1 F01/F03/F04 API tests: draft revision, optimistic concurrency, SSE cursor, withdraw."""

import pytest
from fastapi.testclient import TestClient
from app.config import settings
from app.db import init, connect, now
from app import repository as repo
from app.main import app

HEADERS = {"Authorization": "Bearer test-admin"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "glm_api_key", "")
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    init()
    with connect() as db:
        db.execute(
            "INSERT INTO sources(id,title,edition_id,year,volume,source_url,local_path,"
            "sha256,page_count,rights_status,metadata_json,created_at) "
            "VALUES('book','测试县志','edition',1681,'卷一','https://example.org',"
            "'raw/test.pdf','abc',2,'source_declared_public_domain','{}',?)",
            (now(),),
        )
        for i in (1, 2):
            db.execute(
                "INSERT INTO pages(id,source_id,scan_index) VALUES(?,?,?)",
                (f"book-p{i}", "book", i),
            )
    yield


def test_draft_save_does_not_change_public_text():
    published = repo.save_version("book-p1", "已发布甲文。", "白话甲。", [], "fixture", "m", "h")
    repo.publish(published)
    c = TestClient(app)
    assert c.get("/api/v1/pages/book-p1").json()["version"]["id"] == published

    r = c.post(
        f"/api/v1/admin/versions/{published}/revise",
        headers=HEADERS,
        json={
            "transcription": "草稿乙文（未发布）。",
            "translation": "白话乙。",
            "notes": ["待核"],
            "base_version_id": published,
            "revision_note": "初次校勘草稿",
        },
    )
    assert r.status_code == 200
    draft = r.json()["version_id"]
    # Public reader unchanged; the draft is visible to the curator only.
    assert c.get("/api/v1/pages/book-p1").json()["version"]["id"] == published
    assert c.get("/api/v1/admin/pages/book-p1", headers=HEADERS).json()["version"]["id"] == draft
    listing = c.get("/api/v1/admin/pages/book-p1/versions", headers=HEADERS).json()
    assert {v["id"] for v in listing} == {published, draft}
    assert listing[0]["id"] == draft  # newest first
    # The draft did not inherit the reviewed flag nor enter the public index.
    hits = c.get("/api/v1/search?q=草稿未发布乙文").json()
    assert all(h["version_id"] != draft for h in hits)


def test_stale_baseline_conflicts_and_keeps_content():
    original = repo.save_version("book-p1", "初稿。", "译。", [], "fixture", "m", "h")
    c = TestClient(app)
    first = c.post(
        f"/api/v1/admin/versions/{original}/revise",
        headers=HEADERS,
        json={"transcription": "标签A的修改。", "translation": "译A", "base_version_id": original},
    )
    assert first.status_code == 200
    second = c.post(
        f"/api/v1/admin/versions/{original}/revise",
        headers=HEADERS,
        json={"transcription": "标签B的修改。", "translation": "译B", "base_version_id": original},
    )
    assert second.status_code == 409
    assert second.json()["detail"]["current_version_id"] == first.json()["version_id"]


def test_withdraw_and_republish_via_api():
    v = repo.save_version("book-p1", "甲文。", "译。", [], "fixture", "m", "h")
    repo.publish(v)
    c = TestClient(app)
    assert c.delete("/api/v1/admin/pages/book-p1/publication", headers=HEADERS).status_code == 200
    assert c.get("/api/v1/pages/book-p1").json()["version"] is None
    r = c.put(
        "/api/v1/admin/pages/book-p1/publication",
        headers=HEADERS,
        json={"version_id": v},
    )
    assert r.status_code == 200
    assert c.get("/api/v1/pages/book-p1").json()["version"]["id"] == v


def test_audit_log_records_publication():
    v = repo.save_version("book-p1", "甲文。", "译。", [], "fixture", "m", "h")
    repo.publish(v, reviewed=True, reason="人工核对")
    c = TestClient(app)
    items = c.get("/api/v1/admin/audit-logs", headers=HEADERS).json()["items"]
    assert any(i["action"] == "publish" and i["object_id"] == v for i in items)


def test_sse_after_event_id_resumes_without_duplicates():
    c = TestClient(app)
    c.post("/api/v1/sessions")
    with connect() as db:
        db.execute(
            "INSERT INTO runs(id,owner,kind,request_json,status,created_at,updated_at) "
            "VALUES('r-sse','x','explain','{}','running',?,?)",
            (now(), now()),
        )
    # Use an owner cookie that matches the run: read via API is owner-scoped, so
    # insert with the real owner instead.
    session = c.cookies.get("yulin_session")
    import hashlib

    real_owner = hashlib.sha256(session.encode()).hexdigest()
    with connect() as db:
        # Terminal status so the streamed response ends instead of polling forever.
        db.execute(
            "UPDATE runs SET owner=?,status='completed' WHERE id='r-sse'", (real_owner,)
        )
        db.execute(
            "INSERT INTO run_events(run_id,type,payload,created_at) VALUES('r-sse','run.started','{}',?)",
            (now(),),
        )
    first = c.get("/api/v1/agent/runs/r-sse/stream").text
    cursor = int(first.split("id: ")[1].split("\n")[0])
    resumed = c.get(f"/api/v1/agent/runs/r-sse/stream?after_event_id={cursor}").text
    assert "run.started" not in resumed
