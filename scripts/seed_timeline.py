"""Install the sourced river timeline without touching gazetteer material."""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import settings
from app.db import connect, init

CATALOG = ROOT / "apps/web/features/history-timeline.json"
LEGACY_IDS = (
    "fdcddcc89d704d9d2e54fe7a62b78c0f", "3e445ed8856727169ae47ea3f26fbcbc",
    "de1b9702c18c6a90015e325a0b3c2dea", "ae442ae75ee9bef8570c587d20ae098b",
    "9bc59e93b808af0bdd5154dfcef48c43", "39b510f536a44344c2d6319700fdf800",
    "57f8f05ecebfbf25f6f991a38a24c4c9",
)


def main():
    init()
    items = json.loads(CATALOG.read_text(encoding="utf-8"))
    with connect() as db:
        first_install = not db.execute("SELECT 1 FROM timeline_events WHERE id='river-shimao'").fetchone()
    if first_install:
        folder = settings.data_dir / "backups"
        folder.mkdir(exist_ok=True)
        target = folder / f"pre-river-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
        with sqlite3.connect(settings.db_path) as source, sqlite3.connect(target) as dest:
            source.backup(dest)
        print(f"Database snapshot: {target}")
    with connect() as db:
        # Retire only original seeds; curator-created entries survive.
        for old_id in LEGACY_IDS:
            db.execute("UPDATE timeline_events SET published=0 WHERE id=?", (old_id,))
        for item in items:
            db.execute(
                """INSERT INTO timeline_events
                (id,sort_year,year_label,title,body,event_type,source_note,source_url,published,created_at)
                VALUES(:id,:sort_year,:year_label,:title,:body,:event_type,:source_note,:source_url,1,datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                sort_year=excluded.sort_year,year_label=excluded.year_label,title=excluded.title,
                body=excluded.body,event_type=excluded.event_type,
                source_note=excluded.source_note,source_url=excluded.source_url""",
                item,
            )
    print(f"Installed {len(items)} sourced history nodes.")


if __name__ == "__main__":
    main()
