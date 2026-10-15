"""M0/M1 consistency tests: migrations, snapshots, single-published, terminal states."""

import sqlite3
import pytest
from app.config import settings
from app.db import (
    init,
    connect,
    now,
    finish,
    MigrationError,
    MIGRATIONS,
    _current_version,
)


HEAD = max(version for version, _description, _fn in MIGRATIONS)

V3_SCHEMA = """
CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
INSERT INTO schema_version VALUES(1);
INSERT INTO schema_version VALUES(2);
INSERT INTO schema_version VALUES(3);
CREATE TABLE sources (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, edition_id TEXT NOT NULL, year INTEGER,
  volume TEXT NOT NULL, source_url TEXT NOT NULL, local_path TEXT NOT NULL,
  sha256 TEXT NOT NULL UNIQUE, page_count INTEGER NOT NULL, rights_status TEXT NOT NULL,
  metadata_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE pages (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
  scan_index INTEGER NOT NULL CHECK(scan_index>0), UNIQUE(source_id,scan_index));
CREATE TABLE versions (
  id TEXT PRIMARY KEY, page_id TEXT NOT NULL REFERENCES pages(id),
  transcription TEXT NOT NULL, translation TEXT NOT NULL, notes TEXT NOT NULL,
  method TEXT NOT NULL, model TEXT NOT NULL, input_hash TEXT NOT NULL,
  published INTEGER NOT NULL DEFAULT 0, reviewed INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, ever_published INTEGER NOT NULL DEFAULT 0);
CREATE VIRTUAL TABLE text_search USING fts5(version_id UNINDEXED, tokens);
CREATE TABLE runs (
  id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
  request_json TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT,
  error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  idempotency_key TEXT, UNIQUE(owner,idempotency_key));
CREATE TABLE run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
  type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE model_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
  model TEXT NOT NULL, status TEXT NOT NULL, usage_json TEXT, created_at TEXT NOT NULL);
CREATE TABLE timeline_events (
  id TEXT PRIMARY KEY, sort_year INTEGER NOT NULL, year_label TEXT NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL, event_type TEXT NOT NULL,
  source_note TEXT NOT NULL, source_url TEXT NOT NULL DEFAULT '',
  published INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
"""


def make_v3_db(data_dir, duplicate_published=False):
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "raw").mkdir(exist_ok=True)
    (data_dir / "raw" / "test.pdf").write_bytes(b"%PDF-fixture")
    db = sqlite3.connect(data_dir / "catalog.sqlite3")
    db.executescript(V3_SCHEMA)
    db.execute(
        "INSERT INTO sources(id,title,edition_id,year,volume,source_url,local_path,"
        "sha256,page_count,rights_status,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "book",
            "测试县志",
            "edition",
            1681,
            "卷一",
            "https://example.org",
            "raw/test.pdf",
            "sha-book",
            1,
            "source_declared_public_domain",
            "{}",
            now(),
        ),
    )
    db.execute(
        "INSERT INTO pages(id,source_id,scan_index) VALUES(?,?,?)", ("book-p1", "book", 1)
    )
    for i in range(2 if duplicate_published else 1):
        db.execute(
            "INSERT INTO versions(id,page_id,transcription,translation,notes,method,model,"
            "input_hash,published,reviewed,created_at,ever_published) VALUES(?,?,?,?,?,?,?,?,1,0,?,1)",
            (f"v{i}", "book-p1", f"正文{i}", "译文", "[]", "fixture", "m", "h", now()),
        )
    db.commit()
    db.close()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    yield


def _current_version_sqlite(path):
    db = sqlite3.connect(path / "catalog.sqlite3")
    try:
        return db.execute("SELECT max(version) FROM schema_version").fetchone()[0]
    finally:
        db.close()


def test_v3_upgrade_preserves_ids_and_is_idempotent(tmp_path):
    make_v3_db(tmp_path)
    init()
    with connect() as db:
        assert _current_version(db) == HEAD
        columns = {r[1] for r in db.execute("PRAGMA table_info(versions)")}
        assert {"base_version_id", "revision_note", "translation_applicable"} <= columns
        source_columns = {r[1] for r in db.execute("PRAGMA table_info(sources)")}
        assert "visibility" in source_columns
        assert db.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
        assert db.execute("SELECT id FROM pages").fetchone()[0] == "book-p1"
        assert db.execute("SELECT transcription FROM versions").fetchone()[0] == "正文0"
        index = db.execute(
            "SELECT sql FROM sqlite_master WHERE name='idx_versions_one_published'"
        ).fetchone()
        assert "WHERE published=1" in index["sql"]
    backups = list((tmp_path / "backups").glob("pre-migration-v3-*.sqlite3"))
    assert backups, "a consistent pre-migration snapshot must exist"
    before = {p.name for p in (tmp_path / "backups").iterdir()}
    init()
    assert _current_version_sqlite(tmp_path) == HEAD
    assert {p.name for p in (tmp_path / "backups").iterdir()} == before


def test_duplicate_published_is_reported_not_deleted(tmp_path):
    make_v3_db(tmp_path, duplicate_published=True)
    with pytest.raises(MigrationError, match="多个当前发布版本"):
        init()
    assert _current_version_sqlite(tmp_path) == 3
    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        assert db.execute("SELECT count(*) FROM versions").fetchone()[0] == 2
    finally:
        db.close()
    reports = list((tmp_path / "backups").glob("pre-migration-report-*.json"))
    assert reports


def test_failed_migration_rolls_back(tmp_path):
    init()
    assert _current_version_sqlite(tmp_path) == HEAD

    def broken(db):
        db.execute("CREATE TABLE half_migrated(x INTEGER)")
        raise RuntimeError("simulated failure")

    MIGRATIONS.append((HEAD + 1, "broken fixture migration", broken))
    try:
        with pytest.raises(RuntimeError, match="simulated"):
            init()
    finally:
        MIGRATIONS.pop()
    assert _current_version_sqlite(tmp_path) == HEAD
    db = sqlite3.connect(tmp_path / "catalog.sqlite3")
    try:
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='half_migrated'"
        ).fetchone()
    finally:
        db.close()


def test_terminal_transition_is_atomic(tmp_path):
    init()
    with connect() as db:
        db.execute(
            "INSERT INTO runs(id,owner,kind,request_json,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("r1", "o", "explain", "{}", "running", now(), now()),
        )
    assert finish("r1", "cancelled") is True
    assert finish("r1", "completed", result={"answer": "late"}) is False
    with connect() as db:
        assert db.execute("SELECT status FROM runs WHERE id='r1'").fetchone()[0] == "cancelled"
        events = [
            r[0]
            for r in db.execute("SELECT type FROM run_events WHERE run_id='r1' ORDER BY id")
        ]
    assert events == ["run.cancelled"]


def test_publish_keeps_one_current_version_and_index(tmp_path):
    init()
    with connect() as db:
        db.execute(
            "INSERT INTO sources(id,title,edition_id,year,volume,source_url,local_path,"
            "sha256,page_count,rights_status,metadata_json,created_at) "
            "VALUES('b','t','e',1681,'v','u','raw/test.pdf','s',1,"
            "'source_declared_public_domain','{}',?)",
            (now(),),
        )
        db.execute("INSERT INTO pages(id,source_id,scan_index) VALUES('p','b',1)")
    from app import repository as repo

    first = repo.save_version("p", "甲文", "译甲", [], "fixture", "m", "h")
    second = repo.save_version("p", "乙文", "译乙", [], "fixture", "m", "h")
    repo.publish(first)
    repo.publish(second)
    with connect() as db:
        assert db.execute(
            "SELECT count(*) FROM versions WHERE page_id='p' AND published=1"
        ).fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM text_search").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE versions SET published=1 WHERE id=?", (first,))
