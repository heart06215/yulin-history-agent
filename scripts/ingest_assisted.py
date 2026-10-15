"""Insert human-assisted transcriptions/translations as draft versions.

These are produced by the assistant reading the page images directly (the free
vision model is rate limited). They are saved as unpublished, NOT human-reviewed
drafts so the public reader gate still applies until an operator publishes them.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import init, connect  # noqa: E402
from app import repository as repo  # noqa: E402

T = json.loads((ROOT / ".local-eval/my-transcriptions.json").read_text(encoding="utf-8"))
TR = json.loads((ROOT / ".local-eval/my-translations.json").read_text(encoding="utf-8"))


def main():
    init()
    created = []
    for page_id, entry in T.items():
        text = entry["transcription"]
        uncertainties = entry.get("uncertainties", [])
        applicable = entry.get("translation_applicable", True)
        if not text.strip():
            # Blank cover page: no version; record the classification instead.
            with connect() as db:
                db.execute(
                    "UPDATE pages SET page_type=?, mapping_basis=? WHERE id=?",
                    ("blank", "assisted_reading", page_id),
                )
            print(page_id, "-> blank page, no version")
            continue
        translation = TR.get(page_id, "")
        notes = uncertainties + [
            "本页由助手直接阅读原图转录（未调用限流中的免费视觉模型）。"
        ]
        version_id = repo.save_version(
            page_id,
            text,
            translation,
            notes,
            "assisted_transcription",
            "assistant-read-image",
            "assisted-" + page_id,
            translation_applicable=applicable,
        )
        with connect() as db:
            db.execute(
                "UPDATE pages SET page_type=?, mapping_basis=? WHERE id=?",
                ("text", "assisted_reading", page_id),
            )
        created.append((page_id, version_id, len(text), len(translation)))
    for page_id, vid, n, tn in created:
        print(f"{page_id}  version={vid[:12]}  transcription={n}  translation={tn}")
    print("total drafts created:", len(created))


if __name__ == "__main__":
    main()
