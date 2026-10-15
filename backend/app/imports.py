"""Material import shared by HTTP and scripts.

Safety rules: only PDF/JPEG/PNG are accepted; the real format is detected from
the file header, not the extension. Files are stored under a generated safe name
inside data/raw/. Identical content is deduplicated by SHA-256.
"""

import hashlib
import io
import json
import uuid
from pathlib import Path

from PIL import Image, ImageFile, UnidentifiedImageError
from pypdf import PdfReader

from .config import settings
from .db import connect, now, uid, audit

ImageFile.LOAD_TRUNCATED_IMAGES = False

# Real magic numbers -> logical kind.
SIGNATURES = (
    (b"%PDF-", "pdf"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
)

SAFE_MEDIA_TYPES = {"pdf": "application/pdf", "jpeg": "image/jpeg", "png": "image/png"}


class ImportError_(ValueError):
    """Raised for a rejected import (never leaves a half-created record)."""


def detect_kind(head: bytes) -> str:
    for signature, kind in SIGNATURES:
        if head.startswith(signature):
            return kind
    raise ImportError_("无法识别的文件格式：仅支持 PDF、JPEG、PNG（按文件头判断，不信任扩展名）。")


def _safe_name(sha256: str, kind: str) -> str:
    return f"upload-{sha256[:24]}.{kind}"


def _dedup_sha(sha256: str, kind: str) -> str:
    return f"{sha256}-{kind}"


WHITESPACE_HASHES = {hashlib.sha256(b"").hexdigest(), hashlib.sha256(b" ").hexdigest()}


def _meaningful(fingerprint: str) -> bool:
    """Empty/whitespace page fingerprints must not collapse distinct pages."""
    return fingerprint not in WHITESPACE_HASHES


def _ensure_edition(db, edition_id, title):
    db.execute(
        "INSERT INTO editions(id,title,created_at) VALUES(?,?,?) "
        "ON CONFLICT(id) DO NOTHING",
        (edition_id, title or edition_id, now()),
    )


def _read_image(data: bytes):
    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ImportError_("图像无法解析或已损坏。") from e
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        animated = getattr(image, "n_frames", 1) > 1
    if animated:
        raise ImportError_("不支持多帧图像，请提供单页扫描图。")
    if width * height > settings.import_max_pixels:
        raise ImportError_(
            f"图像像素过大（{width}×{height}），超过 {settings.import_max_pixels} 上限。"
        )
    return width, height


def _read_pdf(path: Path):
    try:
        reader = PdfReader(str(path))
        count = len(reader.pages)
    except Exception as e:
        raise ImportError_("PDF 无法解析或已损坏。") from e
    if count < 1:
        raise ImportError_("PDF 没有可读页面。")
    if count > settings.import_max_pages:
        raise ImportError_(
            f"PDF 页数 {count} 超过上限 {settings.import_max_pages}。"
        )
    return count


def _page_hashes(path: Path, kind: str, count: int) -> list[str]:
    """Per-page content hashes for duplicate detection (bounded to sane sizes)."""
    limit = settings.import_max_pages
    if kind == "pdf":
        reader = PdfReader(str(path))
        hashes = []
        for i in range(min(count, limit)):
            try:
                raw = reader.pages[i].extract_text() or ""
            except Exception:
                raw = ""
            hashes.append(hashlib.sha256(raw.encode("utf-8")).hexdigest())
        return hashes
    # Image material is a single page.
    hashes = []
    try:
        with Image.open(path) as image:
            resized = image.convert("RGB").resize((64, 64))
            hashes.append(hashlib.sha256(resized.tobytes()).hexdigest())
    except Exception:
        hashes.append("")
    return hashes


def import_bytes(
    data: bytes,
    filename: str,
    *,
    edition_id: str,
    edition_title: str = "",
    year: int | None = None,
    source_url: str = "",
    rights_status: str = "pending",
    license_note: str = "",
    source_volume: str = "",
    verified_volume: str = "",
    title: str = "",
    material_type: str = "scan",
) -> dict:
    """Validate and import one file. Returns a report dict (never partial)."""
    if not data:
        raise ImportError_("上传内容为空。")
    max_bytes = settings.import_max_megabytes * 1024 * 1024
    if len(data) > max_bytes:
        raise ImportError_(f"文件超过 {settings.import_max_megabytes} MiB 上限。")
    kind = detect_kind(data[:16])
    sha256 = hashlib.sha256(data).hexdigest()
    dedup = _dedup_sha(sha256, kind)

    with connect() as db:
        existing = db.execute(
            "SELECT id,page_count FROM sources WHERE sha256=?", (dedup,)
        ).fetchone()
        if existing:
            return {
                "status": "duplicate_content",
                "source_id": existing["id"],
                "sha256": dedup,
                "page_count": existing["page_count"],
                "message": "相同内容已导入，未重复增加页数。",
            }

    raw_dir = settings.data_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / _safe_name(sha256, kind)
    tmp = target.with_suffix(target.suffix + "." + uuid.uuid4().hex + ".tmp")
    tmp.write_bytes(data)
    try:
        if kind == "pdf":
            count = _read_pdf(tmp)
        else:
            _read_image(data)
            count = 1
        hashes = _page_hashes(tmp, kind, count)
        tmp.replace(target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    source_id = uid()
    rel_path = target.relative_to(settings.data_dir).as_posix()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        reuse = db.execute(
            "SELECT id FROM sources WHERE sha256=?", (dedup,)
        ).fetchone()
        if reuse:
            return {
                "status": "duplicate_content",
                "source_id": reuse["id"],
                "sha256": dedup,
                "page_count": count,
                "message": "相同内容已导入，未重复增加页数。",
            }
        _ensure_edition(db, edition_id, edition_title)
        db.execute(
            """INSERT INTO sources(id,title,edition_id,year,volume,source_url,local_path,
              sha256,page_count,rights_status,metadata_json,created_at,
              source_volume,verified_volume,material_type,license_note,page_hashes_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                source_id,
                title or filename,
                edition_id,
                year,
                verified_volume or source_volume,
                source_url,
                rel_path,
                dedup,
                count,
                rights_status,
                json.dumps(
                    {"original_filename": filename, "kind": kind},
                    ensure_ascii=False,
                ),
                now(),
                source_volume,
                verified_volume,
                material_type,
                license_note,
                json.dumps(hashes, ensure_ascii=False),
            ),
        )
        original_no = None
        for i in range(1, count + 1):
            fingerprint = hashes[i - 1]
            shared = None
            # Identical page content is shared with the canonical page of another
            # file. Pages within the same file are always distinct rows, because
            # (source_id, scan_index) is unique and each scan is its own position.
            if settings.dedup_share_pages and fingerprint and _meaningful(fingerprint):
                shared = db.execute(
                    "SELECT id FROM pages WHERE content_sha256=? AND source_id!=? LIMIT 1",
                    (fingerprint, source_id),
                ).fetchone()
            page_id = shared["id"] if shared else uid()
            if not shared:
                db.execute(
                    """INSERT INTO pages(id,source_id,scan_index,original_page_no,
                      page_type,mapping_basis,content_sha256)
                      VALUES(?,?,?,?,?,?,?)""",
                    (
                        page_id,
                        source_id,
                        i,
                        original_no,
                        "unknown",
                        "import",
                        fingerprint,
                    ),
                )
        audit(
            db,
            "import",
            "source",
            source_id,
            after={"filename": filename, "sha256": dedup, "pages": count},
        )
    return {
        "status": "imported",
        "source_id": source_id,
        "sha256": dedup,
        "page_count": count,
        "edition_id": edition_id,
        "rights_status": rights_status,
    }


def visible_sources():
    with connect() as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT id,title,edition_id,volume,page_count,rights_status,material_type,"
                "created_at FROM sources ORDER BY created_at DESC"
            )
        ]


def editions_summary():
    with connect() as db:
        return [
            dict(r)
            for r in db.execute(
                """SELECT e.id,e.title,e.completeness,e.verification_note,
              (SELECT count(*) FROM sources s WHERE s.edition_id=e.id) materials,
              (SELECT coalesce(sum(s.page_count),0) FROM sources s WHERE s.edition_id=e.id) pages
              FROM editions e ORDER BY e.title"""
            )
        ]
