import json
import pytest
from fastapi.testclient import TestClient
from app.config import settings
from app.db import init, connect, now, uid
from app import repository as repo
from app.agents import explain, recognize
from app.provider import ModelError, Provider
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
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "book",
                "测试县志",
                "edition",
                1681,
                "测试卷",
                "https://example.org",
                "raw/test.pdf",
                "abc",
                2,
                "source_declared_public_domain",
                "{}",
                now(),
            ),
        )
        for i in (1, 2):
            db.execute(
                "INSERT INTO pages(id,source_id,scan_index) VALUES(?,?,?)",
                (f"book-p{i}", "book", i),
            )


def version(text="米脂山川风俗记载。"):
    return repo.save_version(
        "book-p1",
        text,
        "关于米脂山川与风俗的记载。",
        [],
        "test_fixture",
        "test-only",
        "hash",
    )


def run_row(kind="explain"):
    rid = uid()
    with connect() as db:
        db.execute(
            "INSERT INTO runs(id,owner,kind,request_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (rid, "test", kind, "{}", "running", now(), now()),
        )
    return rid


class FakeProvider:
    """Test-only provider. Never available through the runtime app."""

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


def test_downloads_are_not_translations():
    assert repo.stats()["pages"] == 2 and repo.stats()["translated_pages"] == 0


def test_draft_is_private_and_not_reviewed():
    version()
    assert repo.page_info("book-p1")["version"] is None and repo.search("米脂") == []
    assert repo.stats()["translated_pages"] == 1 and repo.stats()["reviewed_pages"] == 0


def test_publish_revise_and_repeat_keep_versions_and_one_index():
    old = version()
    repo.publish(old)
    assert repo.search("米脂")[0]["citation_id"] == old
    new = version("榆林边城。")
    repo.publish(new, True)
    repo.publish(new, True)
    assert (
        repo.page_info("book-p1")["version"]["id"] == new
        and repo.stats()["translated_pages"] == 1
    )
    with connect() as db:
        assert (
            db.execute(
                "SELECT transcription FROM versions WHERE id=?", (old,)
            ).fetchone()[0]
            == "米脂山川风俗记载。"
        )
        assert db.execute("SELECT count(*) FROM text_search").fetchone()[0] == 1


def test_rights_block_publication():
    v = version()
    with connect() as db:
        db.execute("UPDATE sources SET rights_status='pending'")
    with pytest.raises(ValueError, match="RIGHTS"):
        repo.publish(v)


def test_search_handles_injection_text():
    v = version()
    repo.publish(v)
    repo.search('" OR 1=1 -- 米脂')
    repo.search("***")
    assert repo.stats()["sources"] == 1


def test_explain_reads_tools_and_checks_quote():
    v = version()
    repo.publish(v)
    fake = FakeProvider(
        [
            call("read_page", page_id="book-p1"),
            answer(
                {
                    "answer": "本段记述米脂山川风俗。",
                    "citations": [{"citation_id": v, "quote": "米脂山川风俗"}],
                    "uncertainties": [],
                }
            ),
        ]
    )
    result = explain(
        run_row(),
        {"question": "本页内容？", "source_id": "book", "page_id": "book-p1"},
        fake,
    )
    assert (
        result["mode"] == "live_explanation"
        and result["citations"][0]["scan_index"] == 1
        and len(fake.calls) == 2
    )


def test_fabricated_quote_rejected():
    v = version()
    repo.publish(v)
    fake = FakeProvider(
        [
            call("read_page", page_id="book-p1"),
            answer(
                {
                    "answer": "错误事实",
                    "citations": [{"citation_id": v, "quote": "没有这句话"}],
                }
            ),
            answer({"answer": "错误事实", "citations": [{"citation_id": v, "quote": "没有这句话"}]}),
        ]
    )
    with pytest.raises(ModelError, match="引用"):
        explain(run_row(), {"question": "test"}, fake)


def test_no_evidence_uses_safe_response():
    fake = FakeProvider(
        [
            call("search_gazetteers", query="无此人"),
            answer({"answer": "模型胡说", "citations": []}),
        ]
    )
    r = explain(run_row(), {"question": "无此人"}, fake)
    assert r["mode"] == "insufficient_evidence" and "模型胡说" not in r["answer"]


def test_forbidden_tool_rejected():
    with pytest.raises(ModelError, match="未授权"):
        explain(
            run_row(),
            {"question": "test"},
            FakeProvider([call("execute_sql", query="DROP TABLE sources")]),
        )


def test_recognition_path_saves_unpublished_draft(monkeypatch):
    monkeypatch.setattr(
        "app.agents.extract_text",
        lambda p: "米脂县山川风俗沿革记载地理人物城池关隘乡土文献原文内容。",
    )
    fake = FakeProvider(
        [
            call("inspect_page"),
            call("use_text_layer"),
            call("translate_transcription"),
            answer({"translation": "测试初译。", "notes": []}),
            call("save_draft"),
        ]
    )
    result = recognize(run_row("recognize"), {"page_id": "book-p1"}, fake)
    assert (
        result["method"] == "pdf_text_layer"
        and repo.page_info("book-p1", True)["version"]["translation"] == "测试初译。"
    )
    assert repo.page_info("book-p1")["version"] is None


def test_empty_scan_requires_vision(monkeypatch):
    monkeypatch.setattr("app.agents.extract_text", lambda p: "")
    with pytest.raises(ModelError, match="文字层不足"):
        recognize(
            run_row("recognize"),
            {"page_id": "book-p1"},
            FakeProvider([call("inspect_page"), call("use_text_layer")]),
        )


def test_no_key_no_mock():
    with pytest.raises(ModelError, match="API Key"):
        Provider(run_row()).complete([{"role": "user", "content": "hello"}])


def test_api_admin_auth_and_key_guard():
    c = TestClient(app)
    assert c.get("/api/v1/stats").status_code == 200
    assert c.get("/api/v1/admin/pages/book-p1").status_code == 401
    assert (
        c.get(
            "/api/v1/admin/pages/book-p1",
            headers={"Authorization": "Bearer test-admin"},
        ).status_code
        == 200
    )
    c.post("/api/v1/sessions")
    body = {"kind": "explain", "question": "米脂", "idempotency_key": "test-12345"}
    assert c.post("/api/v1/agent/runs", json=body).status_code == 503
    assert (
        c.post(
            "/api/v1/agent/runs",
            json={**body, "kind": "recognize", "page_id": "book-p1"},
        ).status_code
        == 403
    )
    assert c.get("/api/v1/sources/book/pages?limit=99999").status_code == 400


def test_idempotency_owner_isolation_sse_and_cancel(monkeypatch):
    monkeypatch.setattr(settings, "glm_api_key", "fake-only-do-not-send")
    c = TestClient(app)
    c.post("/api/v1/sessions")
    body = {"question": "米脂", "idempotency_key": "test-12345"}
    rid = c.post("/api/v1/agent/runs", json=body).json()["run_id"]
    assert c.post("/api/v1/agent/runs", json=body).json()["run_id"] == rid
    assert (
        c.post("/api/v1/agent/runs", json={**body, "question": "其他"}).status_code
        == 409
    )
    stranger = TestClient(app)
    stranger.post("/api/v1/sessions")
    assert stranger.get("/api/v1/agent/runs/" + rid).status_code == 404
    assert c.get("/api/v1/agent/runs/" + rid).status_code == 200
    c.post("/api/v1/agent/runs/" + rid + "/cancel")
    text = c.get("/api/v1/agent/runs/" + rid + "/stream").text
    assert "run.cancelled" in text and "event: end" in text
    cursor = int(text.split("id: ")[1].split("\n")[0])
    replay = c.get(
        "/api/v1/agent/runs/" + rid + "/stream", headers={"Last-Event-ID": str(cursor)}
    ).text
    assert "run.cancelled" not in replay and "event: end" in replay


def test_historical_reader_never_exposes_draft():
    old = version()
    repo.publish(old)
    draft = version("第二版本")
    repo.publish(draft)
    private = version("未发布内容")
    c = TestClient(app)
    assert c.get("/api/v1/pages/book-p1/versions/" + old).status_code == 200
    assert c.get("/api/v1/pages/book-p1/versions/" + private).status_code == 404


def test_scan_agent_calls_vision_and_keeps_uncertainties(monkeypatch):
    monkeypatch.setattr("app.agents.extract_text", lambda p: "")
    monkeypatch.setattr("app.agents.render_page", lambda p: b"test-image-only")
    fake = FakeProvider(
        [
            call("inspect_page"),
            call("recognize_image"),
            answer({"transcription": "米脂□志", "uncertainties": ["第三字不清楚"]}),
            call("translate_transcription"),
            answer({"translation": "米脂的地方志，缺字待核。", "notes": ["不补缺字"]}),
            call("save_draft"),
        ]
    )
    result = recognize(run_row("recognize"), {"page_id": "book-p1"}, fake)
    assert result["method"] == "vision_api"
    assert any(kwargs.get("vision") for _, kwargs in fake.calls)
    assert "第三字不清楚" in repo.page_info("book-p1", True)["version"]["notes"]


def test_timeline_public_shows_only_published_events():
    hidden = repo.save_timeline_event(
        1471, "明成化七年（1471）", "待核实节点", "正文", "milestone", "出处", ""
    )
    public = repo.save_timeline_event(
        1681, "清康熙二十年（1681）", "《米脂县志》刊行", "正文", "gazetteer", "来源元数据", "https://example.org"
    )
    repo.set_timeline_published(public, True)
    c = TestClient(app)
    assert [e["id"] for e in c.get("/api/v1/timeline").json()] == [public]
    assert c.get("/api/v1/admin/timeline").status_code == 401
    headers = {"Authorization": "Bearer test-admin"}
    assert {
        e["id"] for e in c.get("/api/v1/admin/timeline", headers=headers).json()
    } == {hidden, public}
    r = c.post(
        "/api/v1/admin/timeline/" + hidden + "/publish",
        headers=headers,
        json={"published": True},
    )
    assert r.status_code == 200
    assert {e["id"] for e in c.get("/api/v1/timeline").json()} == {hidden, public}
    assert (
        c.delete("/api/v1/admin/timeline/" + hidden, headers=headers).status_code
        == 200
    )
    assert [e["id"] for e in c.get("/api/v1/timeline").json()] == [public]


def test_timeline_rejects_unverified_type_and_ordering():
    headers = {"Authorization": "Bearer test-admin"}
    c = TestClient(app)
    assert (
        c.post(
            "/api/v1/admin/timeline",
            headers=headers,
            json={
                "sort_year": 2000,
                "year_label": "y",
                "title": "t",
                "body": "b",
                "event_type": "rumor",
                "source_note": "s",
            },
        ).status_code
        == 422
    )
    accepted = c.post(
        "/api/v1/admin/timeline",
        headers=headers,
        json={
            "sort_year": -221,
            "year_label": "秦（公元前221年）",
            "title": "t",
            "body": "b",
            "event_type": "event",
            "source_note": "s",
        },
    )
    assert accepted.status_code == 200
    c.delete(
        "/api/v1/admin/timeline/" + accepted.json()["event_id"], headers=headers
    )
    with pytest.raises(ValueError, match="SORT_YEAR"):
        repo.save_timeline_event(
            10000, "x", "t", "b", "milestone", "s", ""
        )
    qin = repo.save_timeline_event(-221, "秦（公元前221年）", "秦置上郡", "b", "event", "s", "")
    a = repo.save_timeline_event(1681, "a", "甲", "b", "gazetteer", "s", "")
    b = repo.save_timeline_event(1841, "b", "乙", "b", "gazetteer", "s", "")
    assert [e["id"] for e in repo.list_timeline(False)] == [qin, a, b]


def test_page_versions_lists_ever_published_with_current_flag():
    first = version()
    repo.publish(first)
    second = version("榆林边城。")
    repo.publish(second, True)
    listed = repo.page_versions("book-p1")
    assert [v["id"] for v in listed] == [second, first]
    assert listed[0]["current"] == 1 and listed[1]["current"] == 0
    draft = version("未发布草稿")
    assert all(v["id"] != draft for v in repo.page_versions("book-p1"))
    c = TestClient(app)
    assert {v["id"] for v in c.get("/api/v1/pages/book-p1/versions").json()} == {
        first,
        second,
    }
