"""Idempotent import of verified downloaded PDFs. Never labels files as translated."""

import hashlib, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from pypdf import PdfReader
from app.db import connect, init, now


def main():
    init()
    records = json.loads((ROOT / "data/manifests/downloads.json").read_text())
    report = []
    correction_path = ROOT / "data/manifests/verified-volume-notes.json"
    corrections = (
        json.loads(correction_path.read_text()) if correction_path.exists() else {}
    )
    for r in records:
        if r.get("status") != "downloaded":
            continue
        path = ROOT / r["local_path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != r["sha256"]:
            raise ValueError("File hash mismatch: " + r["id"])
        count = (
            1
            if path.suffix.lower() in (".jpg", ".jpeg", ".png")
            else len(PdfReader(path).pages)
        )
        with connect() as db:
            existing = db.execute(
                "SELECT sha256 FROM sources WHERE id=?", (r["id"],)
            ).fetchone()
            if existing and existing["sha256"] != r["sha256"]:
                raise ValueError(
                    "Source ID already belongs to a different file; use a new source ID."
                )
            db.execute(
                """INSERT OR IGNORE INTO sources(id,title,edition_id,year,volume,source_url,local_path,sha256,page_count,rights_status,metadata_json,created_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    r["id"],
                    r["title"],
                    r["edition_id"],
                    r["year"],
                    corrections.get(r["id"], {}).get("display_volume", r["volume"]),
                    r["source_url"],
                    path.relative_to(ROOT / "data").as_posix(),
                    r["sha256"],
                    count,
                    r["rights_status"],
                    json.dumps(r, ensure_ascii=False),
                    now(),
                ),
            )
            db.execute(
                "UPDATE sources SET metadata_json=? WHERE id=?",
                (json.dumps(r, ensure_ascii=False), r["id"]),
            )
            if r["id"] in corrections:
                db.execute(
                    "UPDATE sources SET volume=? WHERE id=?",
                    (corrections[r["id"]]["display_volume"], r["id"]),
                )
            for i in range(1, count + 1):
                db.execute(
                    "INSERT OR IGNORE INTO pages(id,source_id,scan_index) VALUES(?,?,?)",
                    (f"{r['id']}-p{i}", r["id"], i),
                )
        report.append(
            {
                "id": r["id"],
                "title": r["title"],
                "edition_id": r["edition_id"],
                "volume": r["volume"],
                "scan_pages": count,
                "sha256": r["sha256"],
                "source_url": r["source_url"],
                "rights_status": r["rights_status"],
            }
        )
    (ROOT / "data/manifests/import-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(
        "Imported:",
        len(report),
        "material files;",
        sum(r["scan_pages"] for r in report),
        "scan pages",
    )


if __name__ == "__main__":
    main()
