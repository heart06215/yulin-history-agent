"""Model-output tolerance and planner nudging tests."""

import json
import pytest
from app.config import settings
from app.db import init, connect, now, uid
from app import repository as repo
from app.agents import Translation, Recognition, recognize
from app.provider import classify_http_error


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "glm_api_key", "fake-only")
    init()
    with connect() as db:
        db.execute(
            "INSERT INTO sources(id,title,edition_id,year,volume,source_url,local_path,"
            "sha256,page_count,rights_status,metadata_json,created_at) "
            "VALUES('book','测试县志','edition',1681,'卷一','u','raw/test.pdf','abc',1,"
            "'source_declared_public_domain','{}',?)",
            (now(),),
        )
        db.execute("INSERT INTO pages(id,source_id,scan_index) VALUES('book-p1','book',1)")
    yield


def run_row(kind="recognize"):
    rid = uid()
    with connect() as db:
        db.execute(
            "INSERT INTO runs(id,owner,kind,request_json,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (rid, "t", kind, "{}", "running", now(), now()),
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


def test_translation_notes_are_coerced_from_objects():
    parsed = Translation.model_validate(
        {
            "translation": "白话。",
            "notes": [{"缺字": "第三字不可辨"}, {"异体字": "作「甯」"}, "单一字符串"],
        }
    )
    assert parsed.notes == ["缺字：第三字不可辨", "异体字：作「甯」", "单一字符串"]


def test_translation_notes_accept_single_string_and_ignore_extra():
    parsed = Translation.model_validate(
        {"translation": "白话。", "notes": "只有一条", "confidence": 0.9}
    )
    assert parsed.notes == ["只有一条"]


def test_long_notes_are_truncated_not_rejected():
    # 47 object entries is a real model output; it must not fail the whole stage.
    parsed = Translation.model_validate(
        {
            "translation": "白话。",
            "notes": [{"字": f"说明{i}"} for i in range(47)],
        }
    )
    assert len(parsed.notes) == 30
    assert parsed.notes[0] == "字：说明0"


def test_recognition_uncertainties_accept_objects():
    parsed = Recognition.model_validate(
        {"transcription": "米脂□志", "uncertainties": [{"第三字": "字形残损"}]}
    )
    assert parsed.uncertainties == ["第三字：字形残损"]


def test_classify_http_error_is_specific():
    assert classify_http_error(429, {"Retry-After": "30"})[0] == "rate_limited"
    assert "30" in classify_http_error(429, {"Retry-After": "30"})[1]
    assert classify_http_error(401)[0] == "auth_error"
    assert classify_http_error(503)[0] == "server_error"
    assert classify_http_error(400)[0] == "http_error"


def test_recognize_nudges_planner_after_text_only_reply(monkeypatch):
    monkeypatch.setattr("app.agents.extract_text", lambda p: "")
    monkeypatch.setattr("app.agents.render_page", lambda p: b"test-image")
    fake = FakeProvider(
        [
            {"role": "assistant", "content": "原页没有文字层，需要用视觉识别。"},
            call("inspect_page"),
            call("recognize_image"),
            answer({"transcription": "米脂□志", "uncertainties": [{"第三字": "残损"}]}),
            call("translate_transcription"),
            answer({"translation": "米脂的地方志。", "notes": [{"缺字": "第三字"}]}),
            call("save_draft"),
        ]
    )
    result = recognize(run_row(), {"page_id": "book-p1"}, fake)
    assert result["method"] == "vision_api"
    version = repo.page_info("book-p1", True)["version"]
    assert "第三字：残损" in version["notes"]
    assert "缺字：第三字" in version["notes"]
