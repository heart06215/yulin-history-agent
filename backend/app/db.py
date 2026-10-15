"""SQLite access, versioned migrations and run-state transitions.

Migration contract:
- Migrations are ordered by version and applied at most once. The version row
  and the schema change are committed in the same transaction, and any failure
  rolls the whole migration back.
- Before an existing database is upgraded it is copied with the SQLite backup
  API (a consistent snapshot, not a copy of a live WAL file), a pre-migration
  report is written, and duplicate published rows are reported instead of being
  silently deleted.
"""

import json, sqlite3, threading, uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from .config import settings


class MigrationError(RuntimeError):
    """Raised when a database cannot be upgraded safely."""


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


@contextmanager
def connect():
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.db_path, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


MIGRATIONS = []


def _script(db, sql):
    """Run DDL statement by statement so the migration stays inside one transaction.

    sqlite3.executescript would implicitly commit, which would defeat the
    all-or-nothing migration guarantee.
    """
    for statement in sql.split(";"):
        statement = statement.strip()
        if statement:
            db.execute(statement)


def migration(version, description):
    def decorate(fn):
        MIGRATIONS.append((version, description, fn))
        return fn

    return decorate


@migration(1, "base schema: sources, pages, versions, runs, events, model calls")
def _migration_1(db):
    _script(
        db,
        """
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS sources (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, edition_id TEXT NOT NULL, year INTEGER,
          volume TEXT NOT NULL, source_url TEXT NOT NULL, local_path TEXT NOT NULL,
          sha256 TEXT NOT NULL UNIQUE, page_count INTEGER NOT NULL, rights_status TEXT NOT NULL,
          metadata_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_sources_edition ON sources(edition_id);
        CREATE TABLE IF NOT EXISTS pages (
          id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
          scan_index INTEGER NOT NULL CHECK(scan_index>0), UNIQUE(source_id,scan_index));
        CREATE TABLE IF NOT EXISTS versions (
          id TEXT PRIMARY KEY, page_id TEXT NOT NULL REFERENCES pages(id),
          transcription TEXT NOT NULL, translation TEXT NOT NULL, notes TEXT NOT NULL,
          method TEXT NOT NULL, model TEXT NOT NULL, input_hash TEXT NOT NULL,
          published INTEGER NOT NULL DEFAULT 0, reviewed INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_versions_page ON versions(page_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_versions_publish ON versions(published,page_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS text_search USING fts5(version_id UNINDEXED, tokens);
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
          request_json TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT,
          error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          idempotency_key TEXT, UNIQUE(owner,idempotency_key));
        CREATE INDEX IF NOT EXISTS idx_runs_queue ON runs(status,created_at);
        CREATE INDEX IF NOT EXISTS idx_runs_owner ON runs(owner,created_at);
        CREATE TABLE IF NOT EXISTS run_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
          type TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_run_events ON run_events(run_id,id);
        CREATE TABLE IF NOT EXISTS model_calls (
          id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
          model TEXT NOT NULL, status TEXT NOT NULL, usage_json TEXT, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_model_calls_time ON model_calls(created_at);
        """,
    )


@migration(2, "versions.ever_published history flag")
def _migration_2(db):
    columns = {r[1] for r in db.execute("PRAGMA table_info(versions)")}
    if "ever_published" not in columns:
        db.execute(
            "ALTER TABLE versions ADD COLUMN ever_published INTEGER NOT NULL DEFAULT 0"
        )
        db.execute("UPDATE versions SET ever_published=published")


@migration(3, "timeline_events gazetteer and milestone nodes")
def _migration_3(db):
    _script(
        db,
        """
        CREATE TABLE IF NOT EXISTS timeline_events (
          id TEXT PRIMARY KEY, sort_year INTEGER NOT NULL, year_label TEXT NOT NULL,
          title TEXT NOT NULL, body TEXT NOT NULL, event_type TEXT NOT NULL,
          source_note TEXT NOT NULL, source_url TEXT NOT NULL DEFAULT '',
          published INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_timeline_year ON timeline_events(sort_year,id);
        """,
    )


@migration(4, "revision lineage, review and audit records, single published version")
def _migration_4(db):
    columns = {r[1] for r in db.execute("PRAGMA table_info(versions)")}
    if "base_version_id" not in columns:
        db.execute("ALTER TABLE versions ADD COLUMN base_version_id TEXT")
    if "revision_note" not in columns:
        db.execute(
            "ALTER TABLE versions ADD COLUMN revision_note TEXT NOT NULL DEFAULT ''"
        )
    if "translation_applicable" not in columns:
        db.execute(
            "ALTER TABLE versions ADD COLUMN translation_applicable INTEGER NOT NULL DEFAULT 1"
        )
    _script(
        db,
        """
        CREATE TABLE IF NOT EXISTS reviews (
          id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(id),
          result TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
          operator_label TEXT NOT NULL DEFAULT 'local_admin',
          created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_reviews_version ON reviews(version_id,created_at);
        CREATE TABLE IF NOT EXISTS audit_logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
          object_type TEXT NOT NULL, object_id TEXT NOT NULL,
          before_json TEXT, after_json TEXT, actor TEXT NOT NULL DEFAULT 'local_admin',
          created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_audit_object ON audit_logs(object_type,object_id,id);
        CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(created_at);
        """,
    )
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_versions_one_published "
        "ON versions(page_id) WHERE published=1"
    )


@migration(5, "source visibility gate for public reads")
def _migration_5(db):
    columns = {r[1] for r in db.execute("PRAGMA table_info(sources)")}
    if "visibility" not in columns:
        db.execute(
            "ALTER TABLE sources ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public'"
        )


@migration(8, "batches, batch items and recognition stage artifacts")
def _migration_8(db):
    _script(
        db,
        """
        CREATE TABLE IF NOT EXISTS batches (
          id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
          control TEXT NOT NULL DEFAULT 'active',
          result TEXT NOT NULL DEFAULT 'pending',
          kind TEXT NOT NULL DEFAULT 'recognize',
          params_json TEXT NOT NULL DEFAULT '{}',
          total INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_batches_control ON batches(control,created_at);
        CREATE TABLE IF NOT EXISTS batch_items (
          id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
          page_id TEXT NOT NULL, scan_index INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending', run_id TEXT,
          error TEXT NOT NULL DEFAULT '',
          result_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_batch_items ON batch_items(batch_id,scan_index);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_batch_item_page
          ON batch_items(batch_id,page_id);
        CREATE TABLE IF NOT EXISTS page_artifacts (
          id TEXT PRIMARY KEY, page_id TEXT NOT NULL,
          stage TEXT NOT NULL, input_hash TEXT NOT NULL,
          result_json TEXT NOT NULL, status TEXT NOT NULL,
          model TEXT NOT NULL DEFAULT '', config_version TEXT NOT NULL DEFAULT 'v1',
          batch_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_page_artifact
          ON page_artifacts(page_id,stage,input_hash);
        """,
    )


@migration(7, "editions, import jobs, page hashes and duplicate reports")
def _migration_7(db):
    _script(
        db,
        """
        CREATE TABLE IF NOT EXISTS editions (
          id TEXT PRIMARY KEY, title TEXT NOT NULL,
          publication TEXT NOT NULL DEFAULT '', region TEXT NOT NULL DEFAULT '',
          completeness TEXT NOT NULL DEFAULT 'unknown',
          verification_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS imports (
          id TEXT PRIMARY KEY, source_id TEXT, filename TEXT NOT NULL,
          sha256 TEXT NOT NULL, status TEXT NOT NULL,
          error TEXT NOT NULL DEFAULT '', metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_imports_status ON imports(status,created_at);
        CREATE INDEX IF NOT EXISTS idx_imports_sha ON imports(sha256);
        CREATE TABLE IF NOT EXISTS duplicate_reports (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, page_id TEXT NOT NULL,
          other_page_id TEXT NOT NULL, score REAL,
          decision TEXT NOT NULL, method TEXT NOT NULL,
          model TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_duplicate_decision ON duplicate_reports(decision,created_at);
        """,
    )
    source_columns = {r[1] for r in db.execute("PRAGMA table_info(sources)")}
    additions = {
        "source_volume": "TEXT NOT NULL DEFAULT ''",
        "verified_volume": "TEXT NOT NULL DEFAULT ''",
        "material_type": "TEXT NOT NULL DEFAULT 'scan'",
        "license_note": "TEXT NOT NULL DEFAULT ''",
        "page_hashes_json": "TEXT NOT NULL DEFAULT '[]'",
    }
    for name, ddl in additions.items():
        if name not in source_columns:
            db.execute(f"ALTER TABLE sources ADD COLUMN {name} {ddl}")
    page_columns = {r[1] for r in db.execute("PRAGMA table_info(pages)")}
    page_additions = {
        "original_page_no": "INTEGER",
        "leaf_label": "TEXT NOT NULL DEFAULT ''",
        "side": "TEXT NOT NULL DEFAULT ''",
        "page_type": "TEXT NOT NULL DEFAULT 'unknown'",
        "reading_order": "INTEGER",
        "mapping_basis": "TEXT NOT NULL DEFAULT ''",
        "content_sha256": "TEXT NOT NULL DEFAULT ''",
        "superseded_by": "TEXT",
    }
    for name, ddl in page_additions.items():
        if name not in page_columns:
            db.execute(f"ALTER TABLE pages ADD COLUMN {name} {ddl}")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_pages_content_sha ON pages(content_sha256)"
    )
    # Backfill editions from the material files that already exist.
    for row in db.execute(
        "SELECT DISTINCT edition_id FROM sources WHERE edition_id NOT IN (SELECT id FROM editions)"
    ):
        db.execute(
            "INSERT INTO editions(id,title,created_at) VALUES(?,?,?)",
            (row["edition_id"], row["edition_id"], now()),
        )


@migration(6, "model call observability columns")
def _migration_6(db):
    columns = {r[1] for r in db.execute("PRAGMA table_info(model_calls)")}
    additions = {
        "supplier": "TEXT NOT NULL DEFAULT ''",
        "stage": "TEXT NOT NULL DEFAULT ''",
        "request_id": "TEXT NOT NULL DEFAULT ''",
        "started_at": "TEXT NOT NULL DEFAULT ''",
        "finished_at": "TEXT NOT NULL DEFAULT ''",
        "duration_ms": "INTEGER",
        "input_tokens": "INTEGER",
        "output_tokens": "INTEGER",
        "error_class": "TEXT NOT NULL DEFAULT ''",
        "estimated_cost": "REAL",
        "price_version": "TEXT NOT NULL DEFAULT ''",
    }
    for name, ddl in additions.items():
        if name not in columns:
            db.execute(f"ALTER TABLE model_calls ADD COLUMN {name} {ddl}")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_model_calls_run ON model_calls(run_id,id)"
    )


_LOCK = threading.Lock()


def _table_exists(db, name):
    return (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _current_version(db):
    if not _table_exists(db, "schema_version"):
        return 0
    row = db.execute("SELECT max(version) FROM schema_version").fetchone()
    return int(row[0] or 0)


def _snapshot(reason):
    """Consistent pre-migration snapshot using the SQLite backup API."""
    backups = settings.data_dir / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    dest = backups / f"{reason}-{stamp}.sqlite3"
    source = sqlite3.connect(settings.db_path)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return dest


def _pre_migration_report(db, from_version):
    """Write a pre-migration report; refuse to continue on duplicate published rows."""
    report = {
        "from_version": from_version,
        "generated_at": now(),
    }
    if _table_exists(db, "sources"):
        missing = []
        from .data_paths import data_file
        for row in db.execute("SELECT id,local_path FROM sources"):
            try:
                data_file(row["local_path"])
            except ValueError:
                missing.append({"source_id": row["id"], "local_path": row["local_path"]})
        report["missing_files"] = missing
    if _table_exists(db, "versions"):
        duplicates = [
            {"page_id": r["page_id"], "count": r["count"]}
            for r in db.execute(
                "SELECT page_id,count(*) AS count FROM versions WHERE published=1 "
                "GROUP BY page_id HAVING count(*)>1"
            )
        ]
        report["duplicate_published"] = duplicates
        report["orphan_versions"] = [
            r["id"]
            for r in db.execute(
                "SELECT v.id FROM versions v LEFT JOIN pages p ON p.id=v.page_id "
                "WHERE p.id IS NULL"
            )
        ]
    backups = settings.data_dir / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    (backups / f"pre-migration-report-{stamp}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if report.get("duplicate_published"):
        raise MigrationError(
            "检测到同一页存在多个当前发布版本，已停止迁移以避免静默删除历史版本。"
            "请先人工核对下列页面："
            + json.dumps(report["duplicate_published"], ensure_ascii=False)
        )


def init():
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _run_migrations()


def _run_migrations():
    probes = sqlite3.connect(settings.db_path)
    try:
        current = _current_version(probes)
    finally:
        probes.close()
    pending = [m for m in MIGRATIONS if m[0] > current]
    if not pending:
        return
    if current > 0:
        _snapshot(f"pre-migration-v{current}")
        report_connection = sqlite3.connect(settings.db_path)
        report_connection.row_factory = sqlite3.Row
        try:
            _pre_migration_report(report_connection, current)
        finally:
            report_connection.close()
    connection = sqlite3.connect(settings.db_path, isolation_level=None, timeout=20)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        for version, _description, fn in pending:
            connection.execute("BEGIN IMMEDIATE")
            try:
                fn(connection)
                connection.execute(
                    "INSERT OR IGNORE INTO schema_version(version) VALUES(?)",
                    (version,),
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
    finally:
        connection.close()


def event(run_id, kind, **payload):
    with connect() as db:
        db.execute(
            "INSERT INTO run_events(run_id,type,payload,created_at) VALUES(?,?,?,?)",
            (run_id, kind, json.dumps(payload, ensure_ascii=False), now()),
        )


# A run may only reach a terminal state from these predecessors. The conditional
# UPDATE makes the transition atomic: the terminal event is written only when the
# state actually changed, so a late completion cannot overwrite a cancellation.
TERMINAL_TRANSITIONS = {
    "completed": ("running",),
    "failed": ("running",),
    "cancelled": ("queued", "running"),
}


def finish(run_id, status, result=None, error=None):
    if status not in TERMINAL_TRANSITIONS:
        raise ValueError("INVALID_TERMINAL_STATUS")
    predecessors = TERMINAL_TRANSITIONS[status]
    placeholders = ",".join("?" for _ in predecessors)
    with connect() as db:
        cursor = db.execute(
            f"UPDATE runs SET status=?,result_json=?,error=?,updated_at=? "
            f"WHERE id=? AND status IN ({placeholders})",
            (
                status,
                json.dumps(result, ensure_ascii=False) if result is not None else None,
                error,
                now(),
                run_id,
                *predecessors,
            ),
        )
        if cursor.rowcount != 1:
            return False
        db.execute(
            "INSERT INTO run_events(run_id,type,payload,created_at) VALUES(?,?,?,?)",
            (
                run_id,
                "run." + status,
                json.dumps({"result": result, "error": error}, ensure_ascii=False),
                now(),
            ),
        )
        return True


def audit(
    db, action, object_type, object_id, before=None, after=None, actor="local_admin"
):
    """Append an audit row inside the caller's transaction."""
    db.execute(
        "INSERT INTO audit_logs(action,object_type,object_id,before_json,after_json,actor,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (
            action,
            object_type,
            object_id,
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(after, ensure_ascii=False) if after is not None else None,
            actor,
            now(),
        ),
    )
