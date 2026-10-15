"""M1 F02/F03 tests: unified public gate, withdrawal, version-bound questions."""

import json
import pytest
from fastapi.testclient import TestClient
from app.config import settings
from app.db import init, connect, now, uid
from app import repository as repo
from app.agents import explain
from app.provider import ModelError
from app.main import app


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


def version(text, translation="白话译文"):
    return repo.save_version("book-p1", text, translation, [], "test_fixture", "test-only", "h")


def run_row(kind="explain", request="{}"):
    rid = uid()
    with connect() as db:
        db.execute(
            "INSERT INTO runs(id,owner,kind,request_json,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (rid, "test", kind, request, "running", now(), now()),
        )
    return rid


class FakeProvider:
    def __init__(self, items):
        self.items = iter(items)
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append((messages.copy(), kwargs))
        return next(self.items)


def call(name, **args):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": uid(),
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


def answer(content):
    return {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)}


def set_rights(value):
    with connect() as db:
        db.execute("UPDATE sources SET rights_status=?", (value,))


def set_visibility(value):
    with connect() as db:
        db.execute("UPDATE sources SET visibility=?", (value,))


def test_restricted_source_blocks_every_public_read(monkeypatch):
    old = version("米脂山川风俗记载。")
    repo.publish(old)
    monkeypatch.setattr("app.main.render_page", lambda page: b"jpeg-bytes")
    c = TestClient(app)
    assert c.get("/api/v1/pages/book-p1").json()["version"]["id"] == old
    assert c.get("/api/v1/pages/book-p1/image").status_code == 200
    assert c.get("/api/v1/search?q=米脂").json()
    assert c.get("/api/v1/pages/book-p1/versions").status_code == 200
    assert c.get(f"/api/v1/pages/book-p1/versions/{old}").status_code == 200

    set_rights("pending")
    assert c.get("/api/v1/pages/book-p1").json()["version"] is None
    assert c.get("/api/v1/pages/book-p1/image").status_code == 403
    assert c.get("/api/v1/search?q=米脂").json() == []
    assert c.get("/api/v1/pages/book-p1/versions").status_code == 404
    assert c.get(f"/api/v1/pages/book-p1/versions/{old}").status_code == 404
    assert repo.read_evidence("book-p1") is None

    set_rights("source_declared_public_domain")
    assert c.get("/api/v1/pages/book-p1").json()["version"]["id"] == old


def test_visibility_flag_also_blocks(monkeypatch):
    v = version("甲文")
    repo.publish(v)
    set_visibility("private")
    c = TestClient(app)
    assert c.get("/api/v1/pages/book-p1").json()["version"] is None
    assert c.get("/api/v1/search?q=甲文").json() == []
    assert repo.read_evidence("book-p1") is None


def test_withdraw_then_republish_keeps_history(monkeypatch):
    old = version("旧版甲文")
    repo.publish(old)
    new = version("新版乙文")
    repo.publish(new)
    c = TestClient(app)
    repo.withdraw("book-p1")
    assert c.get("/api/v1/pages/book-p1").json()["version"] is None
    assert c.get("/api/v1/search?q=乙文").json() == []
    # Withdrawal must not silently restore the previous version.
    assert repo.read_evidence("book-p1") is None
    # Historical content is immutable and can be republished explicitly.
    repo.publish(old)
    assert repo.read_evidence("book-p1")["version_id"] == old
    with connect() as db:
        assert (
            db.execute("SELECT transcription FROM versions WHERE id=?", (new,)).fetchone()[0]
            == "新版乙文"
        )
        assert db.execute(
            "SELECT count(*) FROM versions WHERE page_id='book-p1' AND published=1"
        ).fetchone()[0] == 1


def test_version_bound_question_uses_selected_old_version():
    old = version("旧版记载甲事。")
    repo.publish(old)
    new = version("新版记载乙事。")
    repo.publish(new)
    request = {"question": "本页记载？", "page_id": "book-p1", "version_id": old}
    fake = FakeProvider(
        [
            call("read_page", page_id="book-p1"),
            answer(
                {
                    "answer": "旧版记载甲事。",
                    "citations": [{"citation_id": old, "quote": "旧版记载甲事"}],
                    "uncertainties": [],
                }
            ),
        ]
    )
    result = explain(run_row(request=json.dumps(request)), request, fake)
    assert result["citations"][0]["version_id"] == old
    assert result["scope"]["version_id"] == old
    assert all("乙事" not in c["transcription"] for c in result["citations"])


def test_version_bound_question_rejects_citation_of_new_version():
    old = version("旧版甲事。")
    repo.publish(old)
    new = version("新版乙事。")
    repo.publish(new)
    request = {"question": "本页记载？", "page_id": "book-p1", "version_id": old}
    # The model tries to cite the newer version; the bound page only exposes the old one.
    fake = FakeProvider(
        [
            call("read_page", page_id="book-p1"),
            answer(
                {
                    "answer": "新版乙事。",
                    "citations": [{"citation_id": new, "quote": "新版乙事"}],
                }
            ),
        ]
    )
    with pytest.raises(ModelError, match="引用"):
        explain(run_row(request=json.dumps(request)), request, fake)


def test_enqueue_rejects_version_from_other_page(monkeypatch):
    old = version("甲文")
    repo.publish(old)
    other = repo.save_version("book-p2", "另页", "译", [], "fixture", "m", "h")
    repo.publish(other)
    monkeypatch.setattr(settings, "glm_api_key", "fake-only")
    c = TestClient(app)
    c.post("/api/v1/sessions")
    body = {
        "question": "?",
        "page_id": "book-p1",
        "version_id": other,
        "idempotency_key": "conflict-key-1",
    }
    assert c.post("/api/v1/agent/runs", json=body).status_code == 409
