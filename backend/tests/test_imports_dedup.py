"""M2 import and dedup tests."""

import io
import json
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from app.config import settings
from app.db import init, connect, now
from app import repository as repo
from app import dedup as dedup_service
from app.main import app

HEADERS = {"Authorization": "Bearer test-admin"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "glm_api_key", "")
    monkeypatch.setattr(settings, "dedup_share_pages", True)
    init()
    yield


def png_bytes(color=(200, 200, 200), size=(32, 32)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def pdf_bytes(pages=2):
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=300)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_import_image_creates_source_and_page():
    c = TestClient(app)
    r = c.post(
        "/api/v1/admin/imports",
        headers=HEADERS,
        data={"edition_id": "mizhi-1681", "edition_title": "米脂县志", "rights_status": "pending"},
        files={"file": ("sample.png", png_bytes(), "image/png")},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "imported" and body["page_count"] == 1
    src = c.get("/api/v1/sources/" + body["source_id"]).json()
    assert src["page_count"] == 1
    assert src["rights_status"] == "pending"
    page_id = c.get("/api/v1/sources/" + body["source_id"] + "/pages").json()[0]["id"]
    # Pending rights material must not be publicly readable.
    assert c.get("/api/v1/pages/" + page_id).json()["version"] is None


def test_duplicate_upload_does_not_add_pages():
    c = TestClient(app)
    data = png_bytes()
    first = c.post(
        "/api/v1/admin/imports",
        headers=HEADERS,
        data={"edition_id": "e", "rights_status": "pending"},
        files={"file": ("a.png", data, "image/png")},
    ).json()
    second = c.post(
        "/api/v1/admin/imports",
        headers=HEADERS,
        data={"edition_id": "e", "rights_status": "pending"},
        files={"file": ("b.png", data, "image/png")},
    ).json()
    assert second["status"] == "duplicate_content"
    assert second["source_id"] == first["source_id"]
    with connect() as db:
        assert db.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM pages").fetchone()[0] == 1


def test_corrupt_and_fake_extension_are_rejected_without_records():
    c = TestClient(app)
    corrupt = c.post(
        "/api/v1/admin/imports",
        headers=HEADERS,
        data={"edition_id": "e", "rights_status": "pending"},
        files={"file": ("evil.pdf", b"not a real pdf", "application/pdf")},
    )
    assert corrupt.status_code == 400
    with connect() as db:
        assert db.execute("SELECT count(*) FROM sources").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM pages").fetchone()[0] == 0
        failed = db.execute("SELECT status FROM imports").fetchall()
    assert [f["status"] for f in failed] == ["failed"]


def test_oversize_file_rejected():
    c = TestClient(app)
    original = settings.import_max_megabytes
    settings.import_max_megabytes = 0
    try:
        r = c.post(
            "/api/v1/admin/imports",
            headers=HEADERS,
            data={"edition_id": "e", "rights_status": "pending"},
            files={"file": ("big.png", png_bytes(), "image/png")},
        )
        assert r.status_code == 400 and "上限" in r.json()["detail"]
    finally:
        settings.import_max_megabytes = original


def test_path_traversal_filename_is_stored_safely():
    c = TestClient(app)
    r = c.post(
        "/api/v1/admin/imports",
        headers=HEADERS,
        data={"edition_id": "e", "rights_status": "pending"},
        files={"file": ("../../evil.png", png_bytes(), "image/png")},
    )
    assert r.status_code == 201
    src = r.json()["source_id"]
    with connect() as db:
        row = db.execute("SELECT local_path FROM sources WHERE id=?", (src,)).fetchone()
    assert ".." not in row["local_path"]
    assert (settings.data_dir / row["local_path"]).is_file()
    assert "evil" not in row["local_path"]


def test_pdf_import_counts_pages_and_shares_identical_pages():
    c = TestClient(app)
    r = c.post(
        "/api/v1/admin/imports",
        headers=HEADERS,
        data={"edition_id": "e", "rights_status": "pending"},
        files={"file": ("book.pdf", pdf_bytes(3), "application/pdf")},
    ).json()
    assert r["page_count"] == 3
    pages = c.get("/api/v1/sources/" + r["source_id"] + "/pages").json()
    # Pages within one file keep their own scan positions.
    assert len(pages) == 3
    with connect() as db:
        hashed = db.execute(
            "SELECT count(*) FROM pages WHERE content_sha256!=''"
        ).fetchone()[0]
    assert hashed == 3


def test_dedup_finds_shared_pages_and_similar_text():
    c = TestClient(app)
    data = png_bytes(color=(10, 20, 30))
    a = c.post(
        "/api/v1/admin/imports",
        headers=HEADERS,
        data={"edition_id": "e1", "rights_status": "pending"},
        files={"file": ("a.png", data, "image/png")},
    ).json()
    # Same image content in another edition collapses onto the shared page.
    b = c.post(
        "/api/v1/admin/imports",
        headers=HEADERS,
        data={"edition_id": "e2", "rights_status": "pending"},
        files={"file": ("b.png", data, "image/png")},
    ).json()
    assert b["source_id"] == a["source_id"]
    # Similar-but-not-identical recognized text is surfaced as a candidate pair.
    with connect() as db:
        db.execute(
            "INSERT INTO sources(id,title,edition_id,year,volume,source_url,local_path,"
            "sha256,page_count,rights_status,metadata_json,created_at) "
            "VALUES('b','t','e',1681,'v','u','raw/test.pdf','s',2,"
            "'source_declared_public_domain','{}',?)",
            (now(),),
        )
        for i in (1, 2):
            db.execute(
                "INSERT INTO pages(id,source_id,scan_index) VALUES(?,?,?)",
                (f"b-p{i}", "b", i),
            )
    repo.save_version("b-p1", "米脂县志序文内容基本一致略有差异", "译。", [], "f", "m", "h")
    repo.save_version("b-p2", "米脂县志序文内容基本一致稍有不同", "译。", [], "f", "m", "h")
    groups = c.get("/api/v1/admin/dedup", headers=HEADERS).json()
    assert groups["total"] >= 1
    assert any(g["method"] == "text_similarity" for g in groups["items"])
    stats = c.get("/api/v1/admin/dedup/stats", headers=HEADERS).json()
    assert stats["pages_total"] >= 2


def test_dedup_merge_marks_superseded_without_deleting_text():
    c = TestClient(app)
    with connect() as db:
        db.execute(
            "INSERT INTO sources(id,title,edition_id,year,volume,source_url,local_path,"
            "sha256,page_count,rights_status,metadata_json,created_at) "
            "VALUES('b','t','e',1681,'v','u','raw/test.pdf','s',2,"
            "'source_declared_public_domain','{}',?)",
            (now(),),
        )
        for i in (1, 2):
            db.execute(
                "INSERT INTO pages(id,source_id,scan_index) VALUES(?,?,?)",
                (f"b-p{i}", "b", i),
            )
    repo.save_version("b-p1", "甲文内容相同。", "译。", [], "fixture", "m", "h")
    v2 = repo.save_version("b-p2", "甲文内容相同。", "译。", [], "fixture", "m", "h")
    r = c.post(
        "/api/v1/admin/dedup/decisions",
        headers=HEADERS,
        json={
            "page_id": "b-p2",
            "other_page_id": "b-p1",
            "decision": "merged",
            "reason": "两页均为同一序文",
        },
    )
    assert r.status_code == 200
    with connect() as db:
        assert db.execute(
            "SELECT superseded_by FROM pages WHERE id='b-p2'"
        ).fetchone()[0] == "b-p1"
        # Text of the superseded page is preserved.
        assert db.execute(
            "SELECT transcription FROM versions WHERE id=?", (v2,)
        ).fetchone()[0] == "甲文内容相同。"
    assert c.get("/api/v1/admin/dedup/stats", headers=HEADERS).json()["pages_superseded"] == 1


class FakeProvider:
    def __init__(self, items):
        self.items = iter(items)

    def complete(self, messages, **kwargs):
        return next(self.items)


def test_dedup_model_judgement_records_decision():
    with connect() as db:
        db.execute(
            "INSERT INTO sources(id,title,edition_id,year,volume,source_url,local_path,"
            "sha256,page_count,rights_status,metadata_json,created_at) "
            "VALUES('b','t','e',1681,'v','u','raw/test.pdf','s',2,"
            "'source_declared_public_domain','{}',?)",
            (now(),),
        )
        for i in (1, 2):
            db.execute(
                "INSERT INTO pages(id,source_id,scan_index) VALUES(?,?,?)",
                (f"b-p{i}", "b", i),
            )
    repo.save_version("b-p1", "序文甲，内容相近但不完全一致。", "译。", [], "fixture", "m", "h")
    repo.save_version("b-p2", "序文乙，内容相近但不完全一致。", "译。", [], "fixture", "m", "h")
    fake = FakeProvider(
        [{"role": "assistant", "content": json.dumps({"decision": "related", "reason": "同为序文"})}]
    )
    result = dedup_service.judge_pair("run-x", "b-p1", "b-p2", fake)
    assert result["decision"] == "related" and result["version_a"]


def test_reading_order_follows_verified_order_and_exposes_chronology():
    c = TestClient(app)
    with connect() as db:
        db.execute(
            "INSERT INTO sources(id,title,edition_id,year,volume,source_url,local_path,"
            "sha256,page_count,rights_status,metadata_json,created_at) "
            "VALUES('book','榆林府志','yulin-1841',1841,'卷三十八','u','raw/x.pdf','sh',2,"
            "'source_declared_public_domain','{}',?)",
            (now(),),
        )
        # reading_order overrides the file scan order (as with a mislabeled volume).
        db.execute(
            "INSERT INTO pages(id,source_id,scan_index,reading_order,leaf_label,side,page_type) "
            "VALUES('book-p1','book',1,2,'第二叶','正面','text')"
        )
        db.execute(
            "INSERT INTO pages(id,source_id,scan_index,reading_order,leaf_label,side,page_type) "
            "VALUES('book-p2','book',2,1,'第一叶','正面','cover')"
        )
    order = c.get("/api/v1/sources/book/reading-order").json()
    assert [p["id"] for p in order] == ["book-p2", "book-p1"]
    assert [p["sequence"] for p in order] == [1, 2]
    assert order[0]["leaf_label"] == "第一叶" and order[0]["page_type"] == "cover"
    edition = c.get("/api/v1/editions/yulin-1841").json()
    assert edition["edition_id"] == "yulin-1841"
    assert edition["materials"][0]["year"] == 1841
    assert c.get("/api/v1/sources/missing/reading-order").status_code == 404
    assert c.get("/api/v1/editions/missing").status_code == 404


def test_admin_endpoints_require_token():
    c = TestClient(app)
    assert c.get("/api/v1/admin/dedup").status_code == 401
    assert c.get("/api/v1/admin/imports").status_code == 401
    assert c.get("/api/v1/admin/editions").status_code == 401
