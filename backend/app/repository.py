"""Data access layer.

Public reads share one gate: a piece of text or an image is exposed only when
its source is a declared public-domain source with public visibility, the row
belongs to the requested page and, for the current reader, the version is the
current published revision. Historical links require the version to have been
published before and to still pass the source gate.
"""

import json, re
from .db import connect, uid, now, audit
from .config import settings
from .data_paths import data_file

PUBLIC_RIGHTS = "source_declared_public_domain"
PUBLIC_VISIBILITY = "public"

# The single definition of "this source may be shown publicly".
PUBLIC_SOURCE_SQL = (
    f"s.rights_status='{PUBLIC_RIGHTS}' AND s.visibility='{PUBLIC_VISIBILITY}'"
)


class PublicAccessError(ValueError):
    """Raised when a public read is not permitted (rights, visibility, state)."""


def tokens(text):
    words = re.findall(r"[a-zA-Z0-9]+|[\u3400-\u9fff]", text.lower())
    han = "".join(re.findall(r"[\u3400-\u9fff]", text))
    return list(dict.fromkeys(words + [han[i : i + 2] for i in range(len(han) - 1)]))


def stats():
    with connect() as db:
        s = dict(
            db.execute(
                "SELECT count(DISTINCT edition_id) sources,count(*) volumes,coalesce(sum(page_count),0) pages FROM sources"
            ).fetchone()
        )
        s.update(
            dict(
                db.execute(f"""SELECT count(DISTINCT v.page_id) recognized_pages,
          count(DISTINCT CASE WHEN length(trim(v.translation))>0 THEN v.page_id END) translated_pages,
          count(DISTINCT CASE WHEN v.reviewed=1 THEN v.page_id END) reviewed_pages,
          count(DISTINCT CASE WHEN v.published=1 THEN v.page_id END) published_pages
          FROM versions v JOIN pages p ON p.id=v.page_id JOIN sources s ON s.id=p.source_id
          WHERE {PUBLIC_SOURCE_SQL}""").fetchone()
            )
        )
        s["translated_sources"] = db.execute(
            f"""SELECT count(DISTINCT s.edition_id) FROM versions v
          JOIN pages p ON p.id=v.page_id JOIN sources s ON s.id=p.source_id
          WHERE length(trim(v.translation))>0 AND {PUBLIC_SOURCE_SQL}"""
        ).fetchone()[0]
        return s


def list_sources():
    with connect() as db:
        return [
            dict(r)
            for r in db.execute(
                """SELECT s.id,s.title,s.edition_id,s.year,s.volume,s.source_url,s.page_count,s.rights_status,s.visibility,
          (SELECT count(DISTINCT v.page_id) FROM versions v JOIN pages p ON p.id=v.page_id WHERE p.source_id=s.id) recognized_pages,
          (SELECT count(DISTINCT v.page_id) FROM versions v JOIN pages p ON p.id=v.page_id WHERE p.source_id=s.id AND length(trim(v.translation))>0) translated_pages,
          (SELECT count(DISTINCT v.page_id) FROM versions v JOIN pages p ON p.id=v.page_id WHERE p.source_id=s.id AND v.reviewed=1) reviewed_pages
          FROM sources s ORDER BY s.year,s.volume"""
            )
        ]


def page_info(page_id, include_draft=False):
    with connect() as db:
        row = db.execute(
            """SELECT p.*,s.title,s.volume,s.year,s.rights_status,s.visibility,s.source_url,s.local_path,s.sha256,s.page_count
          FROM pages p JOIN sources s ON s.id=p.source_id WHERE p.id=?""",
            (page_id,),
        ).fetchone()
        if not row:
            raise ValueError("PAGE_NOT_FOUND")
        data = dict(row)
        condition = "" if include_draft else "AND published=1"
        v = db.execute(
            f"SELECT * FROM versions WHERE page_id=? {condition} ORDER BY created_at DESC,id DESC LIMIT 1",
            (page_id,),
        ).fetchone()
        data["version"] = dict(v) if v else None
        return data


def asset_path(page):
    return data_file(page["local_path"])


def source_is_public(page_row):
    rights = page_row["rights_status"]
    try:
        visibility = page_row["visibility"]
    except (KeyError, IndexError):
        visibility = PUBLIC_VISIBILITY
    return rights == PUBLIC_RIGHTS and visibility == PUBLIC_VISIBILITY


def save_version(
    page_id,
    text,
    translation,
    notes,
    method,
    model,
    input_hash,
    base_version_id=None,
    revision_note="",
    translation_applicable=True,
):
    if not text.strip():
        raise ValueError("EMPTY_TRANSCRIPTION")
    version_id = uid()
    with connect() as db:
        if not db.execute("SELECT 1 FROM pages WHERE id=?", (page_id,)).fetchone():
            raise ValueError("PAGE_NOT_FOUND")
        db.execute(
            """INSERT INTO versions(id,page_id,transcription,translation,notes,method,model,input_hash,
             published,reviewed,ever_published,created_at,base_version_id,revision_note,translation_applicable)
         VALUES(?,?,?,?,?,?,?,?,0,0,0,?,?,?,?)""",
            (
                version_id,
                page_id,
                text,
                translation,
                json.dumps(notes, ensure_ascii=False),
                method,
                model,
                input_hash,
                now(),
                base_version_id,
                revision_note,
                int(bool(translation_applicable)),
            ),
        )
    return version_id


def publish(version_id, reviewed=False, reason="", actor="local_admin"):
    """Atomically move the page's public pointer to this version.

    Publish, index update, review record and audit entry share one short write
    transaction. No model call or file rendering happens here.
    """
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            """SELECT v.*,p.id AS page_id,s.rights_status,s.visibility
          FROM versions v JOIN pages p ON p.id=v.page_id JOIN sources s ON s.id=p.source_id
          WHERE v.id=?""",
            (version_id,),
        ).fetchone()
        if not row:
            raise ValueError("VERSION_NOT_FOUND")
        if not source_is_public(row):
            raise ValueError("SOURCE_RIGHTS_PENDING")
        page_id = row["page_id"]
        previous = [
            r["id"]
            for r in db.execute(
                "SELECT id FROM versions WHERE page_id=? AND published=1", (page_id,)
            )
        ]
        for old in previous:
            db.execute("DELETE FROM text_search WHERE version_id=?", (old,))
        db.execute("UPDATE versions SET published=0 WHERE page_id=?", (page_id,))
        db.execute(
            "UPDATE versions SET published=1,ever_published=1,reviewed=? WHERE id=?",
            (int(reviewed), version_id),
        )
        db.execute(
            "INSERT INTO text_search(version_id,tokens) VALUES(?,?)",
            (
                version_id,
                " ".join(tokens(row["transcription"] + " " + row["translation"])),
            ),
        )
        if reviewed:
            db.execute(
                "INSERT INTO reviews(id,version_id,result,reason,operator_label,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (uid(), version_id, "human_reviewed", reason, actor, now()),
            )
        audit(
            db,
            "publish",
            "version",
            version_id,
            before={"page_id": page_id, "previous_published": previous},
            after={"published": 1, "reviewed": bool(reviewed)},
            actor=actor,
        )
        return {"page_id": page_id, "previous": previous}


def withdraw(page_id, reason="", actor="local_admin"):
    """Withdraw the page's current public text without restoring any older version."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        page = db.execute("SELECT id FROM pages WHERE id=?", (page_id,)).fetchone()
        if not page:
            raise ValueError("PAGE_NOT_FOUND")
        current = [
            r["id"]
            for r in db.execute(
                "SELECT id FROM versions WHERE page_id=? AND published=1", (page_id,)
            )
        ]
        for old in current:
            db.execute("DELETE FROM text_search WHERE version_id=?", (old,))
        db.execute("UPDATE versions SET published=0 WHERE page_id=?", (page_id,))
        audit(
            db,
            "withdraw",
            "page",
            page_id,
            before={"published": current},
            after={"published": []},
            actor=actor,
        )
        return {"page_id": page_id, "withdrawn": current}


def evidence(row):
    d = dict(row)
    return {
        "citation_id": d["id"],
        "page_id": d["page_id"],
        "title": d["title"],
        "volume": d["volume"],
        "scan_index": d["scan_index"],
        "transcription": d["transcription"][:7000],
        "translation": d["translation"][:5000],
        "reviewed": bool(d["reviewed"]),
        "source_url": d["source_url"],
        "reader_url": f"/library/{d['source_id']}?page={d['scan_index']}&version={d['id']}",
        "version_id": d["id"],
    }


_EVIDENCE_SELECT = (
    "SELECT v.*,s.title,s.volume,s.source_url,p.scan_index,p.source_id,s.rights_status,s.visibility "
    "FROM versions v JOIN pages p ON p.id=v.page_id JOIN sources s ON s.id=p.source_id"
)


def read_evidence(page_id, version_id=None):
    """Public evidence for a page: current published version, or one explicit version.

    The source gate is re-checked every time, so a rights or visibility change
    takes effect immediately for new reads.
    """
    with connect() as db:
        if version_id:
            row = db.execute(
                f"""{_EVIDENCE_SELECT} WHERE v.id=? AND v.page_id=? AND v.ever_published=1
              AND {PUBLIC_SOURCE_SQL}""",
                (version_id, page_id),
            ).fetchone()
        else:
            row = db.execute(
                f"""{_EVIDENCE_SELECT} WHERE p.id=? AND v.published=1 AND {PUBLIC_SOURCE_SQL}
              ORDER BY v.created_at DESC,v.id DESC LIMIT 1""",
                (page_id,),
            ).fetchone()
        return evidence(row) if row else None


def public_page(page_id, version_id=None):
    """Reader data with the same gate as read_evidence; returns None when blocked."""
    row = read_evidence(page_id, version_id)
    return row


def search(query, source_id=None):
    terms = tokens(query)[:80]
    if not terms:
        return []
    match = " OR ".join('"' + t + '"' for t in terms)
    with connect() as db:
        rows = db.execute(
            f"""SELECT v.*,s.title,s.volume,s.source_url,p.scan_index,p.source_id
          FROM text_search
          JOIN versions v ON v.id=text_search.version_id JOIN pages p ON p.id=v.page_id JOIN sources s ON s.id=p.source_id
          WHERE text_search MATCH ? AND v.published=1 AND {PUBLIC_SOURCE_SQL} AND (? IS NULL OR s.id=?)
          ORDER BY bm25(text_search) LIMIT 6""",
            (match, source_id, source_id),
        ).fetchall()
        return [evidence(r) for r in rows]


TIMELINE_TYPES = ("gazetteer", "event", "milestone", "name_change")


def list_timeline(published_only=True):
    condition = "WHERE published=1" if published_only else ""
    with connect() as db:
        return [
            dict(r)
            for r in db.execute(
                f"SELECT * FROM timeline_events {condition} ORDER BY sort_year,id"
            )
        ]


def save_timeline_event(
    sort_year, year_label, title, body, event_type, source_note, source_url
):
    if event_type not in TIMELINE_TYPES:
        raise ValueError("EVENT_TYPE_INVALID")
    if not (-9999 <= int(sort_year) <= 9999):
        raise ValueError("SORT_YEAR_INVALID")
    if not title.strip() or not body.strip() or not source_note.strip():
        raise ValueError("TIMELINE_FIELDS_REQUIRED")
    event_id = uid()
    with connect() as db:
        db.execute(
            """INSERT INTO timeline_events(id,sort_year,year_label,title,body,event_type,source_note,source_url,published,created_at)
         VALUES(?,?,?,?,?,?,?,?,0,?)""",
            (
                event_id,
                int(sort_year),
                year_label.strip() or str(sort_year),
                title.strip(),
                body.strip(),
                event_type,
                source_note.strip(),
                source_url.strip(),
                now(),
            ),
        )
    return event_id


def set_timeline_published(event_id, published):
    with connect() as db:
        cursor = db.execute(
            "UPDATE timeline_events SET published=? WHERE id=?",
            (int(bool(published)), event_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("TIMELINE_EVENT_NOT_FOUND")


def delete_timeline_event(event_id):
    with connect() as db:
        cursor = db.execute(
            "DELETE FROM timeline_events WHERE id=?", (event_id,)
        )
        if cursor.rowcount == 0:
            raise ValueError("TIMELINE_EVENT_NOT_FOUND")


def page_versions(page_id):
    """Ever-published versions for the reader's history selector; drafts stay private.

    Blocked entirely when the source is not publicly readable.
    """
    with connect() as db:
        gate = db.execute(
            f"SELECT 1 FROM pages p JOIN sources s ON s.id=p.source_id WHERE p.id=? AND {PUBLIC_SOURCE_SQL}",
            (page_id,),
        ).fetchone()
        if not gate:
            raise PublicAccessError("SOURCE_NOT_PUBLIC")
        return [
            dict(r)
            for r in db.execute(
                """SELECT v.id,v.method,v.model,v.reviewed,v.ever_published,v.created_at,v.revision_note,
              (v.id=(SELECT id FROM versions WHERE page_id=? AND published=1 ORDER BY created_at DESC,id DESC LIMIT 1)) current
          FROM versions v WHERE v.page_id=? AND v.ever_published=1 ORDER BY v.created_at DESC""",
                (page_id, page_id),
            )
        ]


def neighbor_page_id(page_id, direction):
    """Resolve adjacent pages through the database, not by string concatenation."""
    if direction not in ("previous", "next"):
        raise ValueError("DIRECTION_INVALID")
    with connect() as db:
        row = db.execute(
            "SELECT source_id,scan_index FROM pages WHERE id=?", (page_id,)
        ).fetchone()
        if not row:
            return None
        index = row["scan_index"] + (-1 if direction == "previous" else 1)
        neighbor = db.execute(
            "SELECT id FROM pages WHERE source_id=? AND scan_index=?",
            (row["source_id"], index),
        ).fetchone()
        return neighbor["id"] if neighbor else None


def reading_order(source_id):
    """Chronological/positional listing of a material's pages.

    Pages are ordered by their verified reading order when present, falling back
    to the local scan index. This is how the reader walks a volume in book order
    rather than by file page number.
    """
    with connect() as db:
        rows = [
            dict(r)
            for r in db.execute(
                """SELECT p.id,p.scan_index,p.reading_order,p.original_page_no,
              p.leaf_label,p.side,p.page_type,p.superseded_by,
              (SELECT count(*) FROM versions v WHERE v.page_id=p.id) version_count,
              (SELECT v.id FROM versions v WHERE v.page_id=p.id AND v.published=1
                ORDER BY v.created_at DESC,v.id DESC LIMIT 1) current_version_id
          FROM pages p WHERE p.source_id=?
          ORDER BY coalesce(p.reading_order,p.scan_index),p.scan_index""",
                (source_id,),
            )
        ]
    for i, row in enumerate(rows, start=1):
        row["sequence"] = i
    return rows


def edition_chronology(edition_id):
    """Materials of one edition ordered by publication year and volume order."""
    with connect() as db:
        return [
            dict(r)
            for r in db.execute(
                """SELECT s.id,s.title,s.year,s.volume,s.source_volume,s.verified_volume,
              s.material_type,s.page_count,s.rights_status,
              (SELECT count(*) FROM pages p WHERE p.source_id=s.id) pages,
              (SELECT count(*) FROM versions v JOIN pages p ON p.id=v.page_id
                WHERE p.source_id=s.id) recognized
          FROM sources s WHERE s.edition_id=?
          ORDER BY s.year IS NULL,s.year,s.volume""",
                (edition_id,),
            )
        ]


def latest_version_id(page_id):
    with connect() as db:
        row = db.execute(
            "SELECT id FROM versions WHERE page_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (page_id,),
        ).fetchone()
        return row["id"] if row else None


def all_page_versions(page_id):
    """Administrator view: every draft and published version for a page."""
    with connect() as db:
        return [
            dict(r)
            for r in db.execute(
                """SELECT v.id,v.method,v.model,v.reviewed,v.published,v.ever_published,
              v.created_at,v.base_version_id,v.revision_note,v.translation_applicable,
              length(v.transcription) AS transcription_length,
              length(v.translation) AS translation_length
          FROM versions v WHERE v.page_id=? ORDER BY v.created_at DESC,v.id DESC""",
                (page_id,),
            )
        ]


def usage_summary(limit_runs=10):
    from .provider import day_start_utc
    from .config import settings

    start = day_start_utc()
    with connect() as db:
        used = db.execute(
            "SELECT count(*) FROM model_calls WHERE created_at>=?", (start,)
        ).fetchone()[0]
        status_counts = {
            r["status"]: r["count"]
            for r in db.execute(
                "SELECT status,count(*) count FROM model_calls WHERE created_at>=? GROUP BY status",
                (start,),
            )
        }
        by_stage = [
            dict(r)
            for r in db.execute(
                "SELECT stage,count(*) count FROM model_calls WHERE created_at>=? GROUP BY stage",
                (start,),
            )
        ]
        failures = [
            dict(r)
            for r in db.execute(
                "SELECT error_class,count(*) count FROM model_calls WHERE created_at>=? "
                "AND status='failed' GROUP BY error_class",
                (start,),
            )
        ]
        tokens = dict(
            db.execute(
                "SELECT coalesce(sum(input_tokens),0) input_tokens,"
                "coalesce(sum(output_tokens),0) output_tokens,"
                "coalesce(sum(estimated_cost),0) estimated_cost,"
                "count(*) priced_calls FROM model_calls "
                "WHERE created_at>=? AND estimated_cost IS NOT NULL",
                (start,),
            ).fetchone()
        )
        recent_runs = [
            dict(r)
            for r in db.execute(
                "SELECT id,kind,status,error,created_at,updated_at FROM runs "
                "ORDER BY created_at DESC LIMIT ?",
                (limit_runs,),
            )
        ]
        runs_by_status = [
            dict(r)
            for r in db.execute(
                "SELECT status,count(*) count FROM runs WHERE created_at>=? GROUP BY status",
                (start,),
            )
        ]
    return {
        "day_start_utc": start,
        "budget_timezone": settings.budget_timezone,
        "daily_model_call_limit": settings.daily_model_call_limit,
        "session_model_call_limit": settings.session_model_call_limit,
        "calls_today": used,
        "calls_remaining": max(0, settings.daily_model_call_limit - used),
        "call_status": status_counts,
        "calls_by_stage": by_stage,
        "failures_by_class": failures,
        "tokens_today": tokens,
        "pricing_configured": bool(settings.price_config_json),
        "recent_runs": recent_runs,
        "runs_today_by_status": runs_by_status,
    }


def list_audit_logs(limit=50, offset=0):
    with connect() as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        ]
