"""Insert agent-transcribed drafts. Labels state exactly what produced the text:
method='agent_transcription', model='glm-5.3-flash (智能体转录，未调用视觉模型)'.
Nothing is published or marked reviewed here; review happens in the workbench.
"""

import hashlib, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.db import connect, init
from app import repository as repo

SOURCE_DIRS = [
    (ROOT / "data/tmp-transcribe", "p*.json"),
    (ROOT / "data/tmp-transcribe-vol3", "v3p*.json"),
    (ROOT / "data/tmp-transcribe-v2", "v2p*.json"),
    (ROOT / "data/tmp-v32", "v32p*.json"),
    (ROOT / "data/tmp-v35", "v35p*.json"),
    (ROOT / "data/tmp-v37", "v37p*.json"),
    (ROOT / "data/tmp-v7", "v7p*.json"),
    (ROOT / "data/tmp-v4", "v4p*.json"),
    (ROOT / "data/tmp-mz1", "m1p*.json"),
    (ROOT / "data/tmp-mz8", "m8p*.json"),
    (ROOT / "data/tmp-long", "long*.json"),
]
AGENT_MODEL = "glm-5.3-flash（智能体转录，未调用视觉模型）"


def main():
    init()
    inserted, skipped = [], []
    seen_pages = set()
    for source_dir, pattern in SOURCE_DIRS:
        for path in sorted(source_dir.glob(pattern)):
            item = json.loads(path.read_text(encoding="utf-8"))
            page_id = item["page_id"]
            if page_id in seen_pages:
                continue
            seen_pages.add(page_id)
            with connect() as db:
                row = db.execute(
                    "SELECT s.sha256, s.local_path FROM pages p JOIN sources s ON s.id=p.source_id WHERE p.id=?",
                    (page_id,),
                ).fetchone()
                existing = db.execute(
                    "SELECT id FROM versions WHERE page_id=? AND method='agent_transcription'",
                    (page_id,),
                ).fetchone()
            if not row:
                skipped.append((page_id, "page not found"))
                continue
            if existing:
                skipped.append((page_id, "already saved"))
                continue
            fingerprint = hashlib.sha256(
                f"{row['sha256']}:{item['scan_index']}:glm-5.3-flash:agent:v1".encode()
            ).hexdigest()
            version_id = repo.save_version(
                page_id,
                item["transcription"],
                item["translation"],
                item["notes"] + item["uncertainties"],
                "agent_transcription",
                AGENT_MODEL,
                fingerprint,
            )
            inserted.append((page_id, item["scan_index"], version_id))
    for page_id, scan_index, version_id in inserted:
        print("saved", page_id, "->", version_id)
    for page_id, reason in skipped:
        print("skip ", page_id, f"({reason})")
    print(f"inserted {len(inserted)}, skipped {len(skipped)}")


if __name__ == "__main__":
    main()
