"""Temporary driver: run batches to completion with backoff, resilient to restart.

Not a permanent project script; used to finish recognizing the current corpus.
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import init, connect  # noqa: E402
from app import batches as bs  # noqa: E402
from app.worker import Worker  # noqa: E402

LOG = ROOT / ".batch-run.log"


def say(msg):
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(time.strftime("%H:%M:%S ") + msg + "\n")


def pending_count():
    with connect() as db:
        return db.execute(
            "SELECT count(*) FROM batch_items i JOIN batches b ON b.id=i.batch_id "
            "WHERE b.control='active' AND i.status IN ('pending','running')"
        ).fetchone()[0]


def main():
    init()
    say("driver start")
    deadline = time.time() + 7200
    worker = Worker()
    worker.start()
    while time.time() < deadline:
        time.sleep(20)
        if not worker.thread.is_alive():
            say("worker thread stopped; restarting")
            worker.stop.set()
            worker = Worker()
            worker.start()
        if pending_count() == 0:
            say("ALL DONE")
            break
    worker.close()
    with connect() as db:
        for batch in db.execute("SELECT id, source_id FROM batches"):
            say(f'{batch["source_id"]} {bs.get_batch(batch["id"])["summary"]}')
    say("driver exit")


if __name__ == "__main__":
    main()
