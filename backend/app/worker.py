"""Single durable SQLite queue consumer. Restart fails interrupted jobs, never silently rebills."""

import json, threading, logging, time
from .config import settings
from .security import redact_error
from .db import connect, now, uid, event, finish
from .agents import recognize, explain
from . import dedup as dedup_service
from . import batches as batch_service
from . import recognize_service
from .provider import ModelError

# Run kinds and their handlers. The handler returns the run result.
HANDLERS = {
    "recognize": recognize,
    "explain": explain,
}


def _dedup(run_id, request):
    from .provider import Provider

    return dedup_service.judge_pair(
        run_id, request["page_a"], request["page_b"], Provider(run_id)
    )


log = logging.getLogger(__name__)


def claim(include_batch=True):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # Interactive runs (reader questions, dedup judgements) take priority over
        # batch pages so a long recognition queue never starves a reader.
        row = db.execute(
            "SELECT * FROM runs WHERE status='queued' AND kind IN ('explain','dedup') "
            "ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not row:
            row = db.execute(
                "SELECT * FROM runs WHERE status='queued' AND kind NOT IN ('explain','dedup') "
                "AND (? OR kind!='batch') ORDER BY created_at LIMIT 1",
                (include_batch,),
            ).fetchone()
        if row:
            db.execute(
                "UPDATE runs SET status='running',updated_at=? WHERE id=?",
                (now(), row["id"]),
            )
        return dict(row) if row else None


def _run_batch_page(row):
    """Dispatch one batch page through the staged recognition flow.

    A batch item is claimed straight into a child run, so progress is visible
    through the normal run APIs and SSE stream.
    """
    item = batch_service.claim_next_item()
    if not item:
        return False
    child_id = row["id"]
    with connect() as db:
        db.execute("UPDATE runs SET kind='recognize' WHERE id=?", (child_id,))
        db.execute(
            "UPDATE batch_items SET run_id=? WHERE id=?", (child_id, item["id"])
        )
    try:
        result = recognize_service.recognize_with_stages(
            child_id, item["page_id"], batch_id=item["batch_id"]
        )
    except Exception as e:
        message = (
            redact_error(e)
            if isinstance(e, (ModelError, ValueError))
            else "处理失败，见运行日志。"
        )
        batch_service.complete_item(item["id"], child_id, error=message)
        return {"rate_limited": "429" in message or "限流" in message, "error": message}
    batch_service.complete_item(item["id"], child_id, result=result)
    time.sleep(max(0.0, settings.batch_page_delay_seconds))
    return {"rate_limited": False, "result": result}


def process(row):
    event(row["id"], "run.started", agent=row["kind"])
    try:
        if row["kind"] == "batch":
            outcome = _run_batch_page(row)
            # The batch child run reports its own result; the trigger run only
            # carries the dispatch summary.
            finish(row["id"], "completed", result=outcome or {"dispatched": False})
            return outcome
        request = json.loads(row["request_json"])
        if row["kind"] == "dedup":
            result = _dedup(row["id"], request)
        else:
            handler = HANDLERS.get(row["kind"])
            if handler is None:
                raise ModelError(f"未知任务类型：{row['kind']}")
            result = handler(row["id"], request)
        # The conditional finish only succeeds from 'running'; a concurrent cancel wins.
        finish(row["id"], "completed", result=result)
        return {"rate_limited": False, "result": result}
    except Exception as e:
        message = (
            redact_error(e)
            if isinstance(e, (ModelError, ValueError))
            else "处理失败，结果未发布。请检查服务日志。"
        )
        if not finish(row["id"], "failed", error=message):
            log.info("Run %s already terminal; failure not written.", row["id"])
        log.warning("Agent run %s failed (%s)", row["id"], type(e).__name__)
        return {"rate_limited": "429" in message or "限流" in message, "error": message}


class Worker:
    def __init__(self):
        self.stop = threading.Event()
        self.thread = None
        # Consecutive rate-limit hits cause an exponential backoff so batches stop
        # hammering a limited free model. Reset on any successful page.
        self._backoff = 0
        self._batch_ready_at = 0.0

    def start(self):
        with connect() as db:
            interrupted = db.execute(
                "SELECT id,kind FROM runs WHERE status='running'"
            ).fetchall()
        for r in interrupted:
            if r["kind"] == "batch":
                # A stale dispatch run is not user work; release it and return the
                # claimed page to pending so the batch can resume.
                finish(r["id"], "failed", error="批次调度在服务重启时中断，已放回待处理。")
                with connect() as db:
                    db.execute(
                        "UPDATE batch_items SET status='pending',run_id=NULL,updated_at=? "
                        "WHERE run_id=? AND status='running'",
                        (now(), r["id"]),
                    )
            else:
                finish(
                    r["id"],
                    "failed",
                    error="服务重启中断了运行；未自动重试，请核对草稿后重新提交。",
                )
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def loop(self):
        while not self.stop.is_set():
            row = claim(include_batch=time.monotonic() >= self._batch_ready_at)
            if row:
                result = process(row)
                # Successful interactive answers must not cancel a batch's cooldown.
                if row["kind"] == "batch" or (result or {}).get("rate_limited"):
                    self._register_backoff(result)
                continue
            if not self._kick_batch():
                self.stop.wait(0.5)

    def _register_backoff(self, outcome):
        if not outcome:
            return
        if outcome.get("rate_limited"):
            base = max(1, settings.rate_limit_backoff_seconds)
            self._backoff = (
                min(self._backoff * 2, settings.rate_limit_max_backoff_seconds)
                if self._backoff
                else base
            )
            self._batch_ready_at = time.monotonic() + self._backoff
            log.warning("Rate limited; backing off %ss before next batch page.", self._backoff)
        else:
            self._backoff = 0
            self._batch_ready_at = 0.0

    def _kick_batch(self):
        """Enqueue one dispatch run so the consumer processes a single batch page.

        The window is one page: after each page the loop re-checks whether an
        interactive run is waiting, so narration is never starved.
        """
        try:
            if time.monotonic() < self._batch_ready_at:
                return False
            if not batch_service.next_active_batch():
                return False
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                pending = db.execute(
                    "SELECT count(*) FROM runs WHERE status IN ('queued','running')"
                ).fetchone()[0]
                if pending >= settings.max_queued_runs:
                    return False
                run_id = uid()
                db.execute(
                    "INSERT INTO runs(id,owner,kind,request_json,status,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (run_id, "batch", "batch", "{}", "queued", now(), now()),
                )
            return True
        except Exception:
            log.exception("Batch kick failed")
            return False

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
