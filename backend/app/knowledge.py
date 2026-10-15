"""Read-only retrieval over public scans and explicitly labelled recognition drafts.

Never return private/restricted sources or revive withdrawn published text.
Unpublished recognition previews are a separate surface from public editions.
"""

from .db import connect
from . import repository as repo

DRAFT_GATE = """v.ever_published=0 AND NOT EXISTS
    (SELECT 1 FROM versions old WHERE old.page_id=v.page_id AND old.ever_published=1)"""


def catalog(query="", source_id=None):
    with connect() as db:
        rows = db.execute(
            f"""SELECT s.id,s.title,s.edition_id,s.year,s.volume,s.source_url,s.page_count,
            s.rights_status,s.visibility,
            (SELECT count(DISTINCT v.page_id) FROM versions v JOIN pages p ON p.id=v.page_id
             WHERE p.source_id=s.id AND (v.published=1 OR ({DRAFT_GATE}))) recognized_pages,
            (SELECT count(DISTINCT v.page_id) FROM versions v JOIN pages p ON p.id=v.page_id
             WHERE p.source_id=s.id AND length(trim(v.translation))>0
             AND (v.published=1 OR ({DRAFT_GATE}))) translated_pages,
            (SELECT count(DISTINCT v.page_id) FROM versions v JOIN pages p ON p.id=v.page_id
             WHERE p.source_id=s.id AND v.reviewed=1 AND v.published=1) reviewed_pages,
            (SELECT min(p.scan_index) FROM versions v JOIN pages p ON p.id=v.page_id
             WHERE p.source_id=s.id AND (v.published=1 OR ({DRAFT_GATE}))) first_recognized_page
            FROM sources s WHERE {repo.PUBLIC_SOURCE_SQL}
            AND (? IS NULL OR s.id=?) AND (s.title || s.volume LIKE ?)
            ORDER BY s.year,s.id LIMIT 100""",
            (source_id, source_id, f"%{query.strip()}%"),
        ).fetchall()
    return [dict(row) for row in rows]


def material_evidence(page_id, version_id=None):
    published = repo.read_evidence(page_id, version_id)
    if published:
        return {**published, "evidence_status": "published"}
    with connect() as db:
        row = db.execute(
            f"""{repo._EVIDENCE_SELECT} WHERE p.id=? AND {repo.PUBLIC_SOURCE_SQL}
            AND ({DRAFT_GATE}) AND (? IS NULL OR v.id=?)
            ORDER BY v.created_at DESC,v.id DESC LIMIT 1""",
            (page_id, version_id, version_id),
        ).fetchone()
    if not row:
        return None
    evidence = repo.evidence(row)
    evidence.update(
        evidence_status="recognition_draft",
        reviewed=False,
        reader_url=f"/library/{row['source_id']}?page={row['scan_index']}&draft={row['id']}",
    )
    return evidence


def recognition_preview(page_id, version_id=None):
    evidence = material_evidence(page_id, version_id)
    if not evidence:
        return None
    with connect() as db:
        version = db.execute("SELECT * FROM versions WHERE id=?", (evidence["version_id"],)).fetchone()
    return {**dict(version), "evidence_status": evidence["evidence_status"]}


def search_materials(query, source_id=None):
    query = query.strip()[:100]
    if not query:
        return []
    # Full phrases first, then meaningful Chinese bigrams. Single-character
    # matches make a local gazetteer search too noisy to be useful.
    phrases = [query] + [term for term in repo.tokens(query) if len(term) >= 2][:12]
    phrases = list(dict.fromkeys(phrases))
    predicates = " OR ".join("(v.transcription LIKE ? OR v.translation LIKE ?)" for _ in phrases)
    params = [value for phrase in phrases for value in (f"%{phrase}%", f"%{phrase}%")]
    with connect() as db:
        rows = db.execute(
            f"""{repo._EVIDENCE_SELECT} WHERE {repo.PUBLIC_SOURCE_SQL}
            AND (v.published=1 OR ({DRAFT_GATE}))
            AND (? IS NULL OR s.id=?) AND ({predicates})
            AND NOT EXISTS (SELECT 1 FROM versions newer WHERE newer.page_id=v.page_id
                AND newer.ever_published=0 AND v.ever_published=0
                AND (newer.created_at>v.created_at OR
                    (newer.created_at=v.created_at AND newer.id>v.id)))
            ORDER BY (v.transcription LIKE ?) DESC,v.published DESC,v.created_at DESC LIMIT 8""",
            (source_id, source_id, *params, f"%{query}%"),
        ).fetchall()
    # A source may be withdrawn between selecting a match and reading it.
    return [item for row in rows if (item := material_evidence(row["page_id"], row["id"]))]


def material_pages(source_id, offset=0, limit=50):
    if not 0 <= offset or not 1 <= limit <= 100:
        raise ValueError("分页范围错误。")
    if not catalog(source_id=source_id):
        raise repo.PublicAccessError("该来源当前不可公开访问。")
    with connect() as db:
        pages = db.execute(
            "SELECT id,scan_index FROM pages WHERE source_id=? ORDER BY scan_index LIMIT ? OFFSET ?",
            (source_id, limit, offset),
        ).fetchall()
    results = []
    for page in pages:
        evidence = material_evidence(page["id"])
        results.append({
            **dict(page),
            "status": evidence["evidence_status"] if evidence else "unrecognized",
            "reader_url": evidence["reader_url"] if evidence else f"/library/{source_id}?page={page['scan_index']}",
        })
    return results
