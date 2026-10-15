"""Retrieval must distinguish drafts, published text, scans and withdrawn text."""

import json
import pytest
from fastapi.testclient import TestClient
from app.config import settings
from app.db import connect, init, now, uid
from app import knowledge, repository as repo
from app.agents import explain
from app.main import app
from app.provider import ModelError


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "glm_api_key", "")
    monkeypatch.setattr(settings, "text_api_key", "")
    monkeypatch.setattr(settings, "text_base_url", "")
    monkeypatch.setattr(settings, "text_model", "")
    init()
    with connect() as db:
        db.execute(
            "INSERT INTO sources(id,title,edition_id,year,volume,source_url,local_path,sha256,page_count,rights_status,metadata_json,created_at) "
            "VALUES('book','榆林府志','edition',1841,'卷九','https://example.org','raw/test.pdf','abc',3,'source_declared_public_domain','{}',?)",
            (now(),),
        )
        for index in range(1, 4):
            db.execute("INSERT INTO pages(id,source_id,scan_index) VALUES(?,?,?)", (f"book-p{index}", "book", index))


def draft(text="榆林衛改置榆林府。"):
    return repo.save_version("book-p1", text, "榆林卫改置榆林府。", [], "agent_transcription", "fixture", "hash")


def run_row():
    run_id = uid()
    with connect() as db:
        db.execute("INSERT INTO runs(id,owner,kind,request_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (run_id, "test", "explain", "{}", "running", now(), now()))
    return run_id


class FakeProvider:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append(kwargs)
        return next(self.messages)


def tool_call(name, **arguments):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": uid(), "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}


def final(citation_id, quote, text="史料讲解"):
    return {"role": "assistant", "content": json.dumps({"answer": text, "citations": [{"citation_id": citation_id, "quote": quote}], "uncertainties": []}, ensure_ascii=False)}


def test_recognition_preview_does_not_publish_draft():
    version = draft()
    client = TestClient(app)
    preview = client.get("/api/v1/knowledge/pages/book-p1").json()
    assert preview["id"] == version
    assert preview["evidence_status"] == "recognition_draft"
    assert preview["published"] == 0
    assert client.get("/api/v1/pages/book-p1").json()["version"] is None
    assert repo.search("榆林") == []
    results = knowledge.search_materials("榆林")
    assert results[0]["citation_id"] == version
    assert "draft=" in results[0]["reader_url"]
    assert knowledge.catalog()[0]["first_recognized_page"] == 1


@pytest.mark.parametrize("column,value", [("visibility", "private"), ("rights_status", "restricted")])
def test_knowledge_gates_every_draft_surface(column, value):
    draft()
    with connect() as db:
        db.execute(f"UPDATE sources SET {column}=?", (value,))
    assert knowledge.catalog() == []
    assert knowledge.search_materials("榆林") == []
    assert knowledge.material_evidence("book-p1") is None
    assert TestClient(app).get("/api/v1/knowledge/pages/book-p1").status_code == 404
    with pytest.raises(repo.PublicAccessError):
        knowledge.material_pages("book")


def test_withdrawal_cannot_resurface_old_draft():
    draft("从未发布的旧初稿。")
    published = draft("已发布的文字。")
    repo.publish(published)
    repo.withdraw("book-p1")
    assert knowledge.material_evidence("book-p1") is None
    assert knowledge.search_materials("初稿") == []
    assert knowledge.recognition_preview("book-p1") is None


def test_search_returns_only_latest_unpublished_revision():
    old = draft("甲版本旧词。")
    new = draft("乙版本新词。")
    with connect() as db:
        db.execute("UPDATE versions SET created_at='2020-01-01' WHERE id=?", (old,))
        db.execute("UPDATE versions SET created_at='2020-01-02' WHERE id=?", (new,))
    assert knowledge.search_materials("旧词") == []
    assert knowledge.search_materials("新词")[0]["citation_id"] == new


def test_explanation_can_cite_a_draft_and_preserves_status():
    version = draft()
    provider = FakeProvider([tool_call("search_gazetteer_materials", query="榆林"), final(version, "榆林衛改置榆林府")])
    result = explain(run_row(), {"question": "榆林卫怎么改的？"}, provider)
    assert result["citations"][0]["evidence_status"] == "recognition_draft"
    assert result["citations"][0]["reviewed"] is False


def test_prose_answer_gets_one_structured_pass_with_checked_citations():
    version = draft()
    provider = FakeProvider([
        tool_call("search_gazetteer_materials", query="榆林"),
        {"role": "assistant", "content": "识别初稿记载榆林卫改置榆林府。"},
        final(version, "榆林衛改置榆林府"),
    ])
    result = explain(run_row(), {"question": "榆林卫怎么改的？"}, provider)
    assert result["citations"][0]["citation_id"] == version
    assert provider.calls[-1]["json_mode"] is True
    assert provider.calls[-1]["stage"] == "explain_format"


def test_history_search_matches_a_full_natural_language_question():
    node_id = repo.save_timeline_event(1731, "1731年", "榆林府设立", "榆林卫改置榆林府。", "name_change", "测试史料", "")
    repo.set_timeline_published(node_id, True)
    provider = FakeProvider([
        tool_call("search_history_nodes", query="榆林卫何时改为榆林府？"),
        final(f"timeline:{node_id}", "榆林卫改置榆林府"),
    ])
    result = explain(run_row(), {"question": "榆林卫何时改为榆林府？"}, provider)
    assert result["citations"][0]["evidence_status"] == "curated_history"


def test_normalized_quote_gets_one_repair_without_relaxing_original_match():
    version = draft()
    provider = FakeProvider([
        tool_call("search_gazetteer_materials", query="榆林"),
        final(version, "榆林卫改置榆林府"),
        final(version, "榆林衛改置榆林府"),
    ])
    result = explain(run_row(), {"question": "榆林卫怎么改的？"}, provider)
    assert result["citations"][0]["quote"] == "榆林衛改置榆林府"
    assert provider.calls[-1]["stage"] == "explain_format"


def test_no_evidence_prose_returns_an_honest_empty_answer():
    provider = FakeProvider([tool_call("search_history_nodes", query="未知地名"), {"role": "assistant", "content": "没有查到。"}])
    assert explain(run_row(), {"question": "未知地名"}, provider)["mode"] == "insufficient_evidence"


def test_template_placeholder_is_repaired_before_display():
    version = draft()
    provider = FakeProvider([
        tool_call("search_gazetteer_materials", query="榆林"),
        final(version, "榆林衛改置榆林府", text="讲解正文"),
        final(version, "榆林衛改置榆林府", text="据识别初稿，榆林卫改置为榆林府，初稿尚未校勘。"),
    ])
    assert explain(run_row(), {"question": "榆林卫怎么改的？"}, provider)["answer"].startswith("据识别初稿")


def test_unknown_model_page_id_can_recover_to_real_evidence():
    version = draft()
    provider = FakeProvider([
        tool_call("read_material_page", page_id="model-invented-id"),
        tool_call("search_gazetteer_materials", query="榆林", source_id="book"),
        final(version, "榆林衛改置榆林府"),
    ])
    assert explain(run_row(), {"question": "榆林卫怎么改的？"}, provider)["citations"][0]["citation_id"] == version


def test_explicit_book_and_volume_are_grounded_before_planning():
    version = draft("修志以存一邑文献。")
    provider = FakeProvider([final(version, "修志以存一邑文献")])
    result = explain(run_row(), {"question": "请读取榆林府志卷九，说明修志动因。"}, provider)
    assert result["citations"][0]["evidence_status"] == "recognition_draft"


def test_unrecognized_scan_is_ephemeral_and_validates_quote(monkeypatch):
    monkeypatch.setattr("app.agents.render_page", lambda page: b"jpeg-fixture")
    provider = FakeProvider([
        tool_call("read_unrecognized_scan", page_id="book-p2"),
        {"role": "assistant", "content": json.dumps({"transcription": "榆林城在北方。□字待辨。", "uncertainties": ["有一字待辨"]})},
        final("scan:book-p2", "榆林城在北方"),
    ])
    result = explain(run_row(), {"question": "本页记什么？"}, provider)
    assert provider.calls[1]["vision"] is True
    assert result["citations"][0]["evidence_status"] == "live_scan"
    assert "有一字待辨" in result["uncertainties"]
    with connect() as db:
        assert db.execute("SELECT count(*) FROM versions").fetchone()[0] == 0


def test_unrecognized_scan_cannot_bypass_source_gate(monkeypatch):
    with connect() as db:
        db.execute("UPDATE sources SET visibility='private'")
    provider = FakeProvider([tool_call("read_unrecognized_scan", page_id="book-p2")])
    with pytest.raises(ModelError, match="不可公开"):
        explain(run_row(), {"question": "本页？"}, provider)
    assert len(provider.calls) == 1


def test_live_scan_rejects_invented_quote(monkeypatch):
    monkeypatch.setattr("app.agents.render_page", lambda page: b"jpeg-fixture")
    provider = FakeProvider([tool_call("read_unrecognized_scan", page_id="book-p2"), {"role": "assistant", "content": '{"transcription":"实有原文","uncertainties":[]}'}, final("scan:book-p2", "不存在的引文"), final("scan:book-p2", "仍然虚构的引文")])
    with pytest.raises(ModelError, match="引用"):
        explain(run_row(), {"question": "本页？"}, provider)


def test_generic_text_provider_can_enqueue_without_glm_key(monkeypatch):
    monkeypatch.setattr(settings, "text_api_key", "fixture-key")
    monkeypatch.setattr(settings, "text_base_url", "https://example.org/v1")
    monkeypatch.setattr(settings, "text_model", "fixture-model")
    client = TestClient(app)
    client.post("/api/v1/sessions")
    result = client.post("/api/v1/agent/runs", json={"question": "榆林？", "idempotency_key": "generic-provider-1"})
    assert result.status_code == 200


def test_page_inventory_includes_unrecognized_scans():
    draft()
    pages = knowledge.material_pages("book")
    assert [page["status"] for page in pages] == ["recognition_draft", "unrecognized", "unrecognized"]
    assert knowledge.material_pages("book", offset=1, limit=1)[0]["id"] == "book-p2"
    with pytest.raises(ValueError):
        knowledge.material_pages("book", offset=-1)
