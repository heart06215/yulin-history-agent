"""Persistent page batches with an explicit control/result state machine.

Control (does the scheduler keep dispatching?): active / paused / cancelled
Result (what did the pages do?): pending / running / completed / partial_failed /
failed / cancelled

The worker only ever moves one item at a time, so the queue window stays bounded
regardless of how many pages the batch contains. Pausing stops future dispatch;
the in-flight page finishes at a safe checkpoint. Cancelling stops future steps;
already formed drafts are kept.
"""

import json

from .config import settings
from .db import connect, uid, now, audit

TERMINAL_RESULTS = ("completed", "partial_failed", "failed", "cancelled")
CONTROL_STATES = ("active", "paused", "cancelled")


class BatchError(ValueError):
    pass


def _params(batch):
    return json.loads(batch["params_json"] or "{}")


def create_batch(source_id, page_ids, kind="recognize", params=None):
    if not page_ids:
        raise BatchError("批次没有可处理的页面。")
    if len(page_ids) > settings.batch_max_pages * 50:
        raise BatchError("批次页数超过允许上限。")
    params = params or {}
    batch_id = uid()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        source = db.execute(
            "SELECT id FROM sources WHERE id=?", (source_id,)
        ).fetchone()
        if not source:
            raise BatchError("材料不存在。")
        known = {
            r["id"]
            for r in db.execute(
                "SELECT id FROM pages WHERE source_id=?", (source_id,)
            )
        }
        unique = []
        for pid in page_ids:
            if pid not in known:
                raise BatchError("批次包含不属于该材料的页面。")
            if pid not in unique:
                unique.append(pid)
        db.execute(
            "INSERT INTO batches(id,source_id,control,result,kind,params_json,total,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                batch_id,
                source_id,
                "active",
                "pending",
                kind,
                json.dumps(params, ensure_ascii=False),
                len(unique),
                now(),
                now(),
            ),
        )
        scan = {
            r["id"]: r["scan_index"]
            for r in db.execute(
                "SELECT id,scan_index FROM pages WHERE source_id=?", (source_id,)
            )
        }
        for pid in unique:
            db.execute(
                "INSERT INTO batch_items(id,batch_id,page_id,scan_index,status,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (uid(), batch_id, pid, scan[pid], "pending", now(), now()),
            )
        audit(
            db,
            "batch_created",
            "batch",
            batch_id,
            after={"source_id": source_id, "pages": len(unique), "kind": kind},
        )
    return batch_id


def _recompute(db, batch_id):
    rows = db.execute(
        "SELECT status,count(*) count FROM batch_items WHERE batch_id=? GROUP BY status",
        (batch_id,),
    ).fetchall()
    counts = {r["status"]: r["count"] for r in rows}
    total = sum(counts.values())
    batch = db.execute("SELECT control FROM batches WHERE id=?", (batch_id,)).fetchone()
    succeeded = counts.get("completed", 0)
    failed = counts.get("failed", 0)
    skipped = counts.get("skipped", 0)
    cancelled = counts.get("cancelled", 0)
    pending = counts.get("pending", 0) + counts.get("running", 0)
    if batch and batch["control"] == "cancelled" and pending == 0:
        result = "cancelled"
    elif pending > 0:
        result = "running" if counts.get("running") else "pending"
    elif cancelled > 0 and failed == 0:
        result = "cancelled"
    elif failed == 0 and cancelled == 0:
        result = "completed"
    elif succeeded == 0 and failed > 0:
        result = "failed"
    else:
        result = "partial_failed"
    db.execute(
        "UPDATE batches SET result=?,updated_at=? WHERE id=?", (result, now(), batch_id)
    )
    return {
        "total": total,
        "completed": succeeded,
        "failed": failed,
        "skipped": skipped,
        "cancelled": cancelled,
        "pending": pending,
        "result": result,
    }


def claim_next_item():
    """Return the next dispatchable item for an active batch, or None.

    Dispatch order puts narration (explain/dedup) ahead of recognition: batches
    are only claimed when no interactive run is waiting, so a queue full of
    batch pages never starves a reader's question.
    """
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        interactive = db.execute(
            "SELECT count(*) FROM runs WHERE status='queued' AND kind IN ('explain','dedup')"
        ).fetchone()[0]
        if interactive:
            return None
        row = db.execute(
            """SELECT i.*,b.control,b.kind,b.source_id FROM batch_items i
            JOIN batches b ON b.id=i.batch_id
            WHERE b.control='active' AND i.status='pending'
            ORDER BY b.created_at,i.scan_index LIMIT 1"""
        ).fetchone()
        if not row:
            return None
        db.execute(
            "UPDATE batch_items SET status='running',updated_at=? WHERE id=?",
            (now(), row["id"]),
        )
        db.execute(
            "UPDATE batches SET result='running',updated_at=? WHERE id=?",
            (now(), row["batch_id"]),
        )
        return dict(row)


def complete_item(item_id, run_id, result=None, error=None):
    status = "failed" if error else "completed"
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT batch_id FROM batch_items WHERE id=?", (item_id,)
        ).fetchone()
        if not row:
            return None
        db.execute(
            "UPDATE batch_items SET status=?,run_id=?,error=?,result_json=?,updated_at=? "
            "WHERE id=?",
            (
                status,
                run_id,
                error or "",
                json.dumps(result, ensure_ascii=False) if result is not None else None,
                now(),
                item_id,
            ),
        )
        return _recompute(db, row["batch_id"])


def skip_item(item_id, reason=""):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT batch_id FROM batch_items WHERE id=?", (item_id,)
        ).fetchone()
        if not row:
            return None
        db.execute(
            "UPDATE batch_items SET status='skipped',error=?,updated_at=? WHERE id=?",
            (reason, now(), item_id),
        )
        return _recompute(db, row["batch_id"])


def cancel_item(item_id, reason="批次取消；已形成的草稿保留。"):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT batch_id FROM batch_items WHERE id=?", (item_id,)
        ).fetchone()
        if not row:
            return None
        db.execute(
            "UPDATE batch_items SET status='cancelled',error=?,updated_at=? WHERE id=?",
            (reason, now(), item_id),
        )
        return _recompute(db, row["batch_id"])


def set_control(batch_id, control):
    if control not in CONTROL_STATES:
        raise BatchError("无效的批次控制状态。")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT control FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not row:
            raise BatchError("批次不存在。")
        if row["control"] == "cancelled" and control != "cancelled":
            raise BatchError("已取消的批次不能重新激活。")
        db.execute(
            "UPDATE batches SET control=?,updated_at=? WHERE id=?",
            (control, now(), batch_id),
        )
        if control == "cancelled":
            db.execute(
                "UPDATE batch_items SET status='cancelled',updated_at=? "
                "WHERE batch_id=? AND status='pending'",
                (now(), batch_id),
            )
        elif control == "paused":
            pass  # in-flight item continues to its safe checkpoint
        summary = _recompute(db, batch_id)
        audit(
            db,
            "batch_" + control,
            "batch",
            batch_id,
            after={"control": control, **(summary or {})},
        )
        return summary


def retry_failed(batch_id):
    """Create fresh attempts for failed/cancelled items only. Never duplicates drafts."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        batch = db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise BatchError("批次不存在。")
        if batch["control"] == "cancelled":
            raise BatchError("已取消的批次不能重试。")
        rows = db.execute(
            "SELECT id FROM batch_items WHERE batch_id=? AND status IN ('failed','cancelled')",
            (batch_id,),
        ).fetchall()
        for r in rows:
            db.execute(
                "UPDATE batch_items SET status='pending',run_id=NULL,error='',"
                "result_json=NULL,updated_at=? WHERE id=?",
                (now(), r["id"]),
            )
        db.execute(
            "UPDATE batches SET control='active',updated_at=? WHERE id=?",
            (now(), batch_id),
        )
        _recompute(db, batch_id)
        audit(db, "batch_retry", "batch", batch_id, after={"retried": len(rows)})
        return {"retried": len(rows)}


def get_batch(batch_id):
    with connect() as db:
        batch = db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise BatchError("批次不存在。")
        items = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM batch_items WHERE batch_id=? ORDER BY scan_index",
                (batch_id,),
            )
        ]
        summary = _recompute(db, batch_id)
    data = dict(batch)
    data["params"] = _params(batch)
    data["items"] = items
    data["summary"] = summary
    return data


def list_batches(limit=20):
    with connect() as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT * FROM batches ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        ]


def next_active_batch():
    with connect() as db:
        row = db.execute(
            "SELECT id FROM batches WHERE control='active' AND result NOT IN "
            "('completed','failed','cancelled') ORDER BY created_at LIMIT 1"
        ).fetchone()
        return row["id"] if row else None
