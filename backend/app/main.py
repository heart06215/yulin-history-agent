import asyncio, hashlib, hmac, json, secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Response,
    Depends,
    Header,
    Query,
    UploadFile,
    File,
    Form,
)
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field
from .config import settings, ConfigError
from .db import init, connect, uid, now, finish
from . import repository as repo
from . import knowledge
from . import imports as importer
from . import dedup as dedup_service
from . import batches as batch_service
from .repository import PublicAccessError
from .documents import render_page
from .worker import Worker
from .security import SecurityMiddleware, redact_error

worker = Worker()


@asynccontextmanager
async def lifespan(app):
    settings.validate_deployment()
    init()
    worker.start()
    yield
    worker.close()


app = FastAPI(title="榆林有志 · Agent API", version="0.2.0", lifespan=lifespan,
              docs_url=None if settings.production else "/docs",
              redoc_url=None if settings.production else "/redoc",
              openapi_url=None if settings.production else "/openapi.json")
app.add_middleware(SecurityMiddleware)


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    from fastapi.responses import JSONResponse

    detail = redact_error(exc.detail) if isinstance(exc.detail, str) else exc.detail
    return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    from fastapi.responses import JSONResponse

    # Raw input and validation context can contain credentials supplied by mistake.
    errors = [{"loc": error["loc"], "type": error["type"], "msg": redact_error(error["msg"])} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(ValueError)
async def value_error(request, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=400, content={"detail": redact_error(exc)})


@app.exception_handler(ConfigError)
async def config_error(request, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=503, content={"detail": redact_error(exc)})


def admin(authorization: str = Header(default="")):
    if not settings.admin_token:
        raise HTTPException(503, "管理员令牌未配置。")
    if not hmac.compare_digest(authorization.encode("utf-8"), ("Bearer " + settings.admin_token).encode("utf-8")):
        raise HTTPException(401, "管理员令牌错误。")
    return True


def owner(request):
    token = request.cookies.get("yulin_session")
    if not token or len(token) != 64:
        raise HTTPException(401, "请先创建会话。")
    return hashlib.sha256(token.encode()).hexdigest()


def get_run(run_id, request):
    with connect() as db:
        row = db.execute(
            "SELECT * FROM runs WHERE id=? AND owner=?", (run_id, owner(request))
        ).fetchone()
    if not row:
        raise HTTPException(404, "运行不存在。")
    result = dict(row)
    result.pop("owner")
    result["request"] = json.loads(result.pop("request_json"))
    result["result"] = (
        json.loads(result.pop("result_json")) if result["result_json"] else None
    )
    return result


@app.get("/api/v1/health")
def health():
    try:
        text = settings.text_provider
        vision = settings.vision_provider
        config_error = ""
    except ConfigError as e:
        text = {"model": "", "supplier": "", "api_key": ""}
        vision = {"model": "", "supplier": "", "api_key": ""}
        config_error = redact_error(e)
    return {
        "status": "ok",
        "text_model": text["model"],
        "vision_model": vision["model"],
        "text_supplier": text["supplier"],
        "vision_supplier": vision["supplier"],
        "model_configured": bool(text["api_key"] or vision["api_key"]),
        "config_error": config_error,
        "admin_configured": bool(settings.admin_token),
        "mode": settings.app_env,
    }


@app.get("/api/v1/info/version")
def info_version():
    return {
        "api_version": app.version,
        "api_prefix": "/api/v1",
        "schema_version": "m2",
    }


def _mask(secret: str) -> str:
    return "已配置" if secret else ""


@app.get("/api/v1/admin/model-config", dependencies=[Depends(admin)])
def model_config():
    text = settings.text_provider
    vision = settings.vision_provider
    return {
        "text": {
            "supplier": text["supplier"],
            "model": text["model"],
            "base_url": text["base_url"],
            "api_key": _mask(text["api_key"]),
            "configured": bool(text["api_key"]),
            "capabilities": {
                "tools": text["supports_tools"],
                "json": text["supports_json"],
                "vision": False,
            },
        },
        "vision": {
            "supplier": vision["supplier"],
            "model": vision["model"],
            "base_url": vision["base_url"],
            "api_key": _mask(vision["api_key"]),
            "configured": bool(vision["api_key"]),
            "capabilities": {
                "tools": vision["supports_tools"],
                "json": vision["supports_json"],
                "vision": vision["supports_vision"],
            },
        },
        "limits": {
            "daily_model_call_limit": settings.daily_model_call_limit,
            "session_model_call_limit": settings.session_model_call_limit,
            "max_agent_steps": settings.max_agent_steps,
            "budget_timezone": settings.budget_timezone,
        },
        "pricing_configured": bool(settings.price_config_json),
        "note": "此处仅显示密钥配置状态；真实密钥由后端读取，不会通过浏览器回传。",
    }


class ProbeInput(BaseModel):
    target: Literal["text", "vision"] = "text"


def _tiny_image_data_url():
    import base64, io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 255, 255)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


@app.post("/api/v1/admin/model-probes", dependencies=[Depends(admin)])
def model_probe(body: ProbeInput):
    """Explicit capability probe. Counts against the budget like any real call."""
    from .provider import Provider, ModelError as _ModelError

    probe = Provider("probe-" + uid())
    try:
        if body.target == "vision":
            provider = settings.vision_provider
            probe.complete(
                [
                    {"role": "system", "content": "只回复 OK。"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "这是一张测试图。"},
                            {
                                "type": "image_url",
                                "image_url": {"url": _tiny_image_data_url()},
                            },
                        ],
                    },
                ],
                vision=True,
                stage="probe",
            )
        else:
            provider = settings.text_provider
            probe.complete(
                [{"role": "user", "content": "只回复 OK。"}], stage="probe"
            )
        return {
            "ok": True,
            "kind": "live_probe",
            "target": body.target,
            "model": provider["model"],
            "supplier": provider["supplier"],
        }
    except (_ModelError, ConfigError) as e:
        return {
            "ok": False,
            "kind": "live_probe",
            "target": body.target,
            "error": redact_error(e),
        }


@app.get("/api/v1/admin/usage", dependencies=[Depends(admin)])
def admin_usage():
    return repo.usage_summary()


@app.get("/api/v1/stats")
def stats():
    return repo.stats()


@app.get("/api/v1/sources")
def sources():
    return repo.list_sources()


@app.get("/api/v1/sources/{source_id}")
def source(source_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            raise HTTPException(404, "文献不存在。")
        data = dict(row)
        data.pop("local_path")
        data["metadata"] = json.loads(data.pop("metadata_json"))
        return data


@app.get("/api/v1/sources/{source_id}/pages")
def pages(source_id: str, offset: int = 0, limit: int = 50):
    if offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(400, "分页范围错误。")
    with connect() as db:
        return [
            dict(r)
            for r in db.execute(
                "SELECT * FROM pages WHERE source_id=? ORDER BY scan_index LIMIT ? OFFSET ?",
                (source_id, limit, offset),
            )
        ]


@app.get("/api/v1/pages/{page_id}")
def page(page_id: str):
    data = repo.page_info(page_id)
    data.pop("local_path", None)
    # Restricted sources may still be catalogued, but no body text is released here.
    if not repo.source_is_public(data):
        data["version"] = None
    return data


@app.get("/api/v1/pages/{page_id}/versions/{version_id}")
def historical_version(page_id: str, version_id: str):
    with connect() as db:
        row = db.execute(
            f"""SELECT v.* FROM versions v JOIN pages p ON p.id=v.page_id JOIN sources s ON s.id=p.source_id
          WHERE v.id=? AND v.page_id=? AND v.ever_published=1 AND {repo.PUBLIC_SOURCE_SQL}""",
            (version_id, page_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "没有可访问的历史发布版本。")
    return dict(row)


@app.get("/api/v1/pages/{page_id}/versions")
def page_version_list(page_id: str):
    """Ever-published versions only; unpublished drafts are not listed."""
    repo.page_info(page_id)
    try:
        return repo.page_versions(page_id)
    except PublicAccessError:
        raise HTTPException(404, "该来源当前不可公开访问。")


@app.get("/api/v1/pages/{page_id}/image")
def image(page_id: str):
    data = repo.page_info(page_id)
    # Rights and visibility are re-checked on every request; a withdrawn source
    # stops serving here even if the browser cached an earlier response.
    if not repo.source_is_public(data):
        raise HTTPException(403, "影像公开展示范围待核实。")
    cache = settings.data_dir / "previews"
    cache.mkdir(exist_ok=True)
    path = cache / f"{data['sha256']}-{data['scan_index']}-1800-v1.jpg"
    if not path.exists():
        raw = render_page(data)
        # Atomic, uniquely named temporary file for simultaneous image requests.
        tmp = path.with_suffix("." + secrets.token_hex(4) + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(path)
    stat = path.stat()
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={
            # Revalidating cache so a later withdrawal takes effect on refresh.
            "Cache-Control": "private,no-cache,must-revalidate",
            "ETag": f'"{data["sha256"]}-{data["scan_index"]}-{int(stat.st_mtime)}"',
        },
    )


@app.get("/api/v1/search")
def search(q: str = Query(default="", max_length=500), source_id: str | None = None):
    return repo.search(q, source_id)


@app.get("/api/v1/knowledge/catalog")
def knowledge_catalog(q: str = Query(default="", max_length=100)):
    return knowledge.catalog(q)


@app.get("/api/v1/knowledge/search")
def knowledge_search(q: str = Query(default="", max_length=100), source_id: str | None = None):
    return knowledge.search_materials(q, source_id)


@app.get("/api/v1/knowledge/pages/{page_id}")
def recognition_preview(page_id: str, version_id: str | None = None):
    preview = knowledge.recognition_preview(page_id, version_id)
    if not preview:
        raise HTTPException(404, "本页暂无可展示的识别成果。")
    return preview


@app.get("/api/v1/knowledge/sources/{source_id}/pages")
def knowledge_pages(source_id: str, offset: int = 0, limit: int = 50):
    try:
        return knowledge.material_pages(source_id, offset, limit)
    except PublicAccessError as error:
        raise HTTPException(404, str(error))


@app.post("/api/v1/sessions")
def session(request: Request, response: Response):
    current = request.cookies.get("yulin_session")
    if not current or len(current) != 64:
        response.set_cookie(
            "yulin_session",
            secrets.token_hex(32),
            httponly=True,
            samesite="strict",
            max_age=86400,
            secure=settings.production or request.url.scheme == "https",
        )
    return {"status": "ready"}


class RunInput(BaseModel):
    kind: Literal["explain", "recognize"] = "explain"
    question: str = Field(default="", max_length=2000)
    page_id: str | None = None
    source_id: str | None = None
    version_id: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=100)


def enqueue(body, request):
    configured = settings.vision_provider if body.kind == "recognize" else settings.text_provider
    if not configured["api_key"]:
        raise HTTPException(
            503, "项目模型 API 尚未配置，请在整理工作台检查模型设置后重试。"
        )
    who = owner(request)
    if body.page_id:
        p = repo.page_info(body.page_id)
        if body.source_id and p["source_id"] != body.source_id:
            raise HTTPException(400, "页面与文献不匹配。")
        if body.version_id:
            # The chosen version must belong to the page and be publicly readable.
            if not repo.read_evidence(body.page_id, body.version_id):
                raise HTTPException(409, "所选文本版本不属于该页或当前不可公开访问。")
    elif body.version_id:
        raise HTTPException(400, "指定文本版本时必须同时指定页面。")
    if body.kind == "recognize" and not body.page_id:
        raise HTTPException(400, "识别必须指定页面。")
    if body.kind == "explain" and not body.question.strip():
        raise HTTPException(400, "请输入问题。")
    payload = body.model_dump(exclude={"idempotency_key"})
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute(
            "SELECT id,request_json FROM runs WHERE owner=? AND idempotency_key=?",
            (who, body.idempotency_key),
        ).fetchone()
        if previous:
            if json.loads(previous["request_json"]) != payload:
                raise HTTPException(409, "幂等键已用于其他请求。")
            return {"run_id": previous["id"], "reused": True}
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        if (
            db.execute(
                "SELECT count(*) FROM runs WHERE created_at>?", (since,)
            ).fetchone()[0]
            >= settings.max_runs_per_hour
        ):
            raise HTTPException(429, "已达到全站每小时运行上限。")
        if (
            db.execute(
                "SELECT count(*) FROM runs WHERE status IN ('queued','running')"
            ).fetchone()[0]
            >= settings.max_queued_runs
        ):
            raise HTTPException(429, "任务队列已满。")
        run_id = uid()
        db.execute(
            "INSERT INTO runs(id,owner,kind,request_json,status,created_at,updated_at,idempotency_key) VALUES(?,?,?,?,?,?,?,?)",
            (
                run_id,
                who,
                body.kind,
                json.dumps(payload, ensure_ascii=False),
                "queued",
                now(),
                now(),
                body.idempotency_key,
            ),
        )
    return {"run_id": run_id, "reused": False}


@app.post("/api/v1/agent/runs")
def new_run(body: RunInput, request: Request):
    if body.kind != "explain":
        raise HTTPException(403, "识别作业仅管理员可创建。")
    return enqueue(body, request)


@app.post("/api/v1/admin/agent/runs", dependencies=[Depends(admin)])
def new_admin_run(body: RunInput, request: Request):
    return enqueue(body, request)


@app.get("/api/v1/agent/runs/{run_id}")
def run(run_id: str, request: Request):
    return get_run(run_id, request)


@app.post("/api/v1/agent/runs/{run_id}/cancel")
def cancel(run_id: str, request: Request):
    row = get_run(run_id, request)
    if row["status"] in ("queued", "running"):
        finish(
            run_id, "cancelled", error="用户取消；已发出的模型请求可能仍计入调用量。"
        )
    return {
        "status": "cancelled"
        if row["status"] in ("queued", "running")
        else row["status"]
    }


@app.get("/api/v1/agent/runs/{run_id}/stream")
async def stream(
    run_id: str,
    request: Request,
    last_event_id: str = Header(default="0"),
    after_event_id: str | None = None,
):
    get_run(run_id, request)
    # Cursor only sets position; ownership was already verified above.
    raw_cursor = after_event_id if after_event_id is not None else last_event_id
    try:
        cursor = max(0, int(raw_cursor))
    except (TypeError, ValueError):
        raise HTTPException(400, "无效事件游标。")

    async def events():
        nonlocal cursor
        ticks = 0
        while not await request.is_disconnected():
            with connect() as db:
                rows = db.execute(
                    "SELECT * FROM run_events WHERE run_id=? AND id>? ORDER BY id LIMIT 100",
                    (run_id, cursor),
                ).fetchall()
                status = db.execute(
                    "SELECT status FROM runs WHERE id=?", (run_id,)
                ).fetchone()[0]
            for row in rows:
                cursor = row["id"]
                data = {
                    "id": cursor,
                    "type": row["type"],
                    "created_at": row["created_at"],
                    **json.loads(row["payload"]),
                }
                yield f"id: {cursor}\nevent: update\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            if status in ("completed", "failed", "cancelled") and len(rows) < 100:
                yield "event: end\ndata: {}\n\n"
                return
            ticks += 1
            if ticks % 15 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/admin/pages/{page_id}", dependencies=[Depends(admin)])
def draft(page_id: str):
    data = repo.page_info(page_id, True)
    data.pop("local_path")
    return data


class PublishInput(BaseModel):
    reviewed: bool = False
    reason: str = Field(default="", max_length=500)


@app.post("/api/v1/admin/versions/{version_id}/publish", dependencies=[Depends(admin)])
def publish(version_id: str, body: PublishInput):
    repo.publish(version_id, body.reviewed, reason=body.reason)
    return {"version_id": version_id, "published": True, "reviewed": body.reviewed}


class EditInput(BaseModel):
    transcription: str = Field(min_length=1, max_length=16000)
    translation: str = Field(default="", max_length=16000)
    notes: list[str] = Field(default_factory=list, max_length=30)
    base_version_id: str | None = None
    revision_note: str = Field(default="", max_length=500)
    translation_applicable: bool = True


@app.post("/api/v1/admin/versions/{version_id}/revise", dependencies=[Depends(admin)])
def revise(version_id: str, body: EditInput):
    with connect() as db:
        row = db.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone()
    if not row:
        raise HTTPException(404, "文本版本不存在。")
    head = repo.latest_version_id(row["page_id"])
    # Optimistic concurrency: a stale baseline means another tab already saved.
    if body.base_version_id and body.base_version_id != head:
        raise HTTPException(
            409,
            {
                "detail": "该页已有更新的草稿版本，请比较后再保存。",
                "current_version_id": head,
            },
        )
    new_id = repo.save_version(
        row["page_id"],
        body.transcription,
        body.translation,
        body.notes,
        "human_revision",
        row["model"],
        row["input_hash"],
        base_version_id=version_id,
        revision_note=body.revision_note,
        translation_applicable=body.translation_applicable,
    )
    return {"version_id": new_id}


@app.delete(
    "/api/v1/admin/pages/{page_id}/publication", dependencies=[Depends(admin)]
)
def withdraw_publication(page_id: str, reason: str = ""):
    result = repo.withdraw(page_id, reason=reason)
    return {"page_id": page_id, "withdrawn": result["withdrawn"]}


class PublicationInput(BaseModel):
    version_id: str


@app.put("/api/v1/admin/pages/{page_id}/publication", dependencies=[Depends(admin)])
def set_publication(page_id: str, body: PublicationInput):
    with connect() as db:
        row = db.execute(
            "SELECT page_id FROM versions WHERE id=?", (body.version_id,)
        ).fetchone()
    if not row or row["page_id"] != page_id:
        raise HTTPException(404, "该版本不属于此页。")
    repo.publish(body.version_id)
    return {"page_id": page_id, "version_id": body.version_id, "published": True}


@app.get("/api/v1/admin/versions/{version_id}", dependencies=[Depends(admin)])
def admin_version(version_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone()
    if not row:
        raise HTTPException(404, "文本版本不存在。")
    return dict(row)


@app.get("/api/v1/admin/pages/{page_id}/versions", dependencies=[Depends(admin)])
def admin_page_versions(page_id: str):
    """Full draft and version list for curators (not the public history list)."""
    repo.page_info(page_id)
    return repo.all_page_versions(page_id)


@app.get("/api/v1/admin/audit-logs", dependencies=[Depends(admin)])
def audit_logs(offset: int = 0, limit: int = 50):
    if offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(400, "分页范围错误。")
    return {"items": repo.list_audit_logs(limit, offset)}


RIGHTS_CHOICES = (
    "source_declared_public_domain",
    "pending",
    "restricted",
)


@app.post("/api/v1/admin/imports", dependencies=[Depends(admin)], status_code=201)
async def create_import(
    file: UploadFile = File(...),
    edition_id: str = Form(...),
    edition_title: str = Form(default=""),
    title: str = Form(default=""),
    source_volume: str = Form(default=""),
    verified_volume: str = Form(default=""),
    year: str = Form(default=""),
    source_url: str = Form(default=""),
    rights_status: str = Form(default="pending"),
    license_note: str = Form(default=""),
    material_type: str = Form(default="scan"),
):
    """Validate and import one material file.

    The import runs synchronously and returns the final result inline (simple for
    local single-user use). The file is validated by header, stored under a safe
    generated name, deduplicated by SHA-256 and never left half-created.
    """
    if rights_status not in RIGHTS_CHOICES:
        raise HTTPException(400, "许可状态取值无效。")
    if not edition_id.strip():
        raise HTTPException(400, "必须提供版本标识（edition_id）。")
    data = await file.read()
    try:
        parsed_year = int(year) if year.strip() else None
    except ValueError:
        raise HTTPException(400, "刊行年必须是整数或留空。")
    record_id = uid()
    with connect() as db:
        db.execute(
            "INSERT INTO imports(id,filename,sha256,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (record_id, file.filename or "upload", "", "running", now(), now()),
        )
    try:
        result = importer.import_bytes(
            data,
            file.filename or "upload",
            edition_id=edition_id.strip(),
            edition_title=edition_title.strip(),
            year=parsed_year,
            source_url=source_url.strip(),
            rights_status=rights_status,
            license_note=license_note.strip(),
            source_volume=source_volume.strip(),
            verified_volume=verified_volume.strip(),
            title=title.strip(),
            material_type=material_type.strip() or "scan",
        )
    except ValueError as e:
        with connect() as db:
            db.execute(
                "UPDATE imports SET status=?,error=?,updated_at=? WHERE id=?",
                ("failed", str(e), now(), record_id),
            )
        raise HTTPException(400, str(e))
    with connect() as db:
        db.execute(
            "UPDATE imports SET source_id=?,sha256=?,status=?,error=?,updated_at=? WHERE id=?",
            (
                result.get("source_id"),
                result.get("sha256", ""),
                result["status"],
                "",
                now(),
                record_id,
            ),
        )
    return {"import_id": record_id, **result}


@app.get("/api/v1/admin/imports", dependencies=[Depends(admin)])
def list_imports(offset: int = 0, limit: int = 50):
    if offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(400, "分页范围错误。")
    with connect() as db:
        items = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM imports ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        ]
        total = db.execute("SELECT count(*) FROM imports").fetchone()[0]
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@app.get("/api/v1/admin/imports/{import_id}", dependencies=[Depends(admin)])
def get_import(import_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM imports WHERE id=?", (import_id,)).fetchone()
    if not row:
        raise HTTPException(404, "导入记录不存在。")
    return dict(row)


@app.get("/api/v1/admin/editions", dependencies=[Depends(admin)])
def admin_editions():
    return importer.editions_summary()


@app.get("/api/v1/sources/{source_id}/reading-order")
def source_reading_order(source_id: str):
    with connect() as db:
        exists = db.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone()
    if not exists:
        raise HTTPException(404, "文献不存在。")
    return repo.reading_order(source_id)


@app.get("/api/v1/editions/{edition_id}")
def edition_detail(edition_id: str):
    material = repo.edition_chronology(edition_id)
    if not material:
        raise HTTPException(404, "版本不存在。")
    return {"edition_id": edition_id, "materials": material}


@app.get("/api/v1/admin/dedup", dependencies=[Depends(admin)])
def dedup_groups():
    return dedup_service.find_groups()


@app.get("/api/v1/admin/dedup/stats", dependencies=[Depends(admin)])
def dedup_stats():
    return dedup_service.duplicate_stats()


class DedupJudgeInput(BaseModel):
    page_a: str
    page_b: str
    idempotency_key: str = Field(min_length=8, max_length=100)


@app.post(
    "/api/v1/admin/dedup/judgements",
    dependencies=[Depends(admin)],
    status_code=202,
)
def dedup_judge(body: DedupJudgeInput):
    """Run a model judgement between two pages as a background-capable task."""
    for pid in (body.page_a, body.page_b):
        repo.page_info(pid)
    if not settings.glm_api_key and not settings.text_api_key:
        raise HTTPException(503, "尚未配置文本模型，不能做重复判定。")
    run_id = uid()
    with connect() as db:
        previous = db.execute(
            "SELECT id FROM runs WHERE owner='admin-dedup' AND idempotency_key=?",
            (body.idempotency_key,),
        ).fetchone()
        if previous:
            return {"run_id": previous["id"], "reused": True}
        db.execute(
            "INSERT INTO runs(id,owner,kind,request_json,status,created_at,updated_at,idempotency_key) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                run_id,
                "admin-dedup",
                "dedup",
                json.dumps({"page_a": body.page_a, "page_b": body.page_b}),
                "queued",
                now(),
                now(),
                body.idempotency_key,
            ),
        )
    return {"run_id": run_id, "reused": False}


@app.get("/api/v1/admin/dedup/judgements/{run_id}", dependencies=[Depends(admin)])
def dedup_judgement(run_id: str):
    with connect() as db:
        row = db.execute(
            "SELECT * FROM runs WHERE id=? AND kind='dedup'", (run_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "判定任务不存在。")
    data = dict(row)
    data["result"] = (
        json.loads(data.pop("result_json")) if data["result_json"] else None
    )
    return data


class DedupDecisionInput(BaseModel):
    page_id: str
    other_page_id: str
    decision: Literal["merged", "dismissed"]
    reason: str = Field(default="", max_length=500)


@app.post("/api/v1/admin/dedup/decisions", dependencies=[Depends(admin)])
def dedup_decide(body: DedupDecisionInput):
    if body.decision == "merged":
        return dedup_service.mark_superseded(
            body.other_page_id, body.page_id, body.reason
        )
    report_id = dedup_service.record_decision(
        body.page_id, body.other_page_id, "dismissed", "human", reason=body.reason
    )
    return {"report_id": report_id, "decision": "dismissed"}


class BatchInput(BaseModel):
    source_id: str
    kind: Literal["recognize"] = "recognize"
    from_scan: int | None = None
    to_scan: int | None = None
    page_ids: list[str] = Field(default_factory=list, max_length=4000)
    only_unrecognized: bool = False
    only_failed: bool = False


def _select_batch_pages(body: BatchInput):
    with connect() as db:
        rows = db.execute(
            "SELECT id,scan_index FROM pages WHERE source_id=? ORDER BY scan_index",
            (body.source_id,),
        ).fetchall()
        selected = list(rows)
        if body.page_ids:
            wanted = set(body.page_ids)
            selected = [r for r in selected if r["id"] in wanted]
        if body.from_scan is not None:
            selected = [r for r in selected if r["scan_index"] >= body.from_scan]
        if body.to_scan is not None:
            selected = [r for r in selected if r["scan_index"] <= body.to_scan]
        if body.only_unrecognized or body.only_failed:
            kept = []
            for r in selected:
                has = db.execute(
                    "SELECT 1 FROM versions WHERE page_id=? LIMIT 1", (r["id"],)
                ).fetchone()
                if body.only_unrecognized and has:
                    continue
                kept.append(r)
            selected = kept
        # Pages already handled in another unfinished batch are not re-queued.
        pending = {
            x["page_id"]
            for x in db.execute(
                """SELECT i.page_id FROM batch_items i JOIN batches b ON b.id=i.batch_id
                WHERE b.control='active' AND i.status IN ('pending','running')"""
            )
        }
        selected = [r for r in selected if r["id"] not in pending]
        return [r["id"] for r in selected], len(rows)


@app.post("/api/v1/admin/batches", dependencies=[Depends(admin)], status_code=201)
def create_batch(body: BatchInput):
    page_ids, total = _select_batch_pages(body)
    if not page_ids:
        raise HTTPException(409, "没有可处理的页面（可能都已识别或已在其他批次中）。")
    if len(page_ids) > settings.batch_max_pages * 50:
        raise HTTPException(413, "批次页数超过上限。")
    batch_id = batch_service.create_batch(
        body.source_id,
        page_ids,
        kind=body.kind,
        params={
            "from_scan": body.from_scan,
            "to_scan": body.to_scan,
            "only_unrecognized": body.only_unrecognized,
            "max_pages": settings.batch_max_pages,
        },
    )
    preview = {
        "待处理": len(page_ids),
        "材料总页数": total,
        "每批上限": settings.batch_max_pages,
    }
    return {"batch_id": batch_id, "preview": preview}


@app.get("/api/v1/admin/batches", dependencies=[Depends(admin)])
def list_batches(limit: int = 20):
    if not 1 <= limit <= 100:
        raise HTTPException(400, "分页范围错误。")
    return {"items": batch_service.list_batches(limit)}


@app.get("/api/v1/admin/batches/{batch_id}", dependencies=[Depends(admin)])
def get_batch(batch_id: str):
    try:
        return batch_service.get_batch(batch_id)
    except batch_service.BatchError as e:
        raise HTTPException(404, str(e))


class BatchControlInput(BaseModel):
    control: Literal["active", "paused", "cancelled"]


@app.patch("/api/v1/admin/batches/{batch_id}", dependencies=[Depends(admin)])
def control_batch(batch_id: str, body: BatchControlInput):
    try:
        summary = batch_service.set_control(batch_id, body.control)
    except batch_service.BatchError as e:
        raise HTTPException(409, str(e))
    return {"batch_id": batch_id, "control": body.control, "summary": summary}


@app.post(
    "/api/v1/admin/batches/{batch_id}/retries",
    dependencies=[Depends(admin)],
    status_code=202,
)
def retry_batch(batch_id: str):
    try:
        return {"batch_id": batch_id, **batch_service.retry_failed(batch_id)}
    except batch_service.BatchError as e:
        raise HTTPException(409, str(e))


@app.get("/api/v1/timeline")
def timeline():
    return repo.list_timeline(published_only=True)


class TimelineInput(BaseModel):
    sort_year: int = Field(ge=-9999, le=9999)
    year_label: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=3000)
    event_type: Literal["gazetteer", "event", "milestone", "name_change"]
    source_note: str = Field(min_length=1, max_length=300)
    source_url: str = Field(default="", max_length=500)


@app.get("/api/v1/admin/timeline", dependencies=[Depends(admin)])
def admin_timeline():
    return repo.list_timeline(published_only=False)


@app.post("/api/v1/admin/timeline", dependencies=[Depends(admin)])
def add_timeline_event(body: TimelineInput):
    event_id = repo.save_timeline_event(
        body.sort_year,
        body.year_label,
        body.title,
        body.body,
        body.event_type,
        body.source_note,
        body.source_url,
    )
    return {"event_id": event_id}


class TimelinePublishInput(BaseModel):
    published: bool


@app.post(
    "/api/v1/admin/timeline/{event_id}/publish", dependencies=[Depends(admin)]
)
def publish_timeline_event(event_id: str, body: TimelinePublishInput):
    repo.set_timeline_published(event_id, body.published)
    return {"event_id": event_id, "published": body.published}


@app.delete("/api/v1/admin/timeline/{event_id}", dependencies=[Depends(admin)])
def remove_timeline_event(event_id: str):
    repo.delete_timeline_event(event_id)
    return {"event_id": event_id, "deleted": True}
