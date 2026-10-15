import json
import time
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from .config import settings, ConfigError
from .db import connect, now, event


class ModelError(RuntimeError):
    pass


def day_start_utc():
    """Start of the configured budget day, converted to UTC, in a stable format."""
    try:
        tz = ZoneInfo(settings.budget_timezone or "UTC")
    except Exception:
        tz = timezone.utc
    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError) as e:
        raise ModelError(
            "模型未返回有效 JSON；本次结果未入库，请调整模型或重试。"
        ) from e


def _price_for(model):
    if not settings.price_config_json:
        return None
    try:
        table = json.loads(settings.price_config_json)
    except (ValueError, TypeError):
        return None
    entry = table.get(model)
    return entry if isinstance(entry, dict) else None


def _estimate_cost(model, usage):
    price = _price_for(model)
    if not price:
        return None, ""
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if prompt is None and completion is None:
        return None, str(price.get("version", ""))
    cost = 0.0
    if prompt is not None:
        cost += float(prompt) / 1000.0 * float(price.get("input", 0) or 0)
    if completion is not None:
        cost += float(completion) / 1000.0 * float(price.get("output", 0) or 0)
    return round(cost, 6), str(price.get("version", ""))


def classify_http_error(status, headers=None):
    """Map an HTTP status to a stable error class and an actionable message."""
    headers = headers or {}
    if status == 429:
        retry_after = headers.get("Retry-After")
        suffix = f"建议 {retry_after} 秒后重试。" if retry_after else "请稍后重试。"
        return "rate_limited", f"模型服务限流（HTTP 429），{suffix}本次未自动重复发送。"
    if status in (401, 403):
        return "auth_error", f"模型服务认证或权限失败（HTTP {status}）；请核对密钥与模型权限。"
    if status >= 500:
        return "server_error", f"模型服务暂时不可用（HTTP {status}）；可稍后重试。"
    return "http_error", f"模型服务返回 HTTP {status}；请核对模型名与账号额度。"


def resolve(vision: bool, tools: bool, json_mode: bool):
    """Pick and validate the provider before any request is sent."""
    provider = settings.vision_provider if vision else settings.text_provider
    if not provider["api_key"]:
        label = "VISION_API_KEY/GLM_API_KEY" if vision else "GLM_API_KEY 或 TEXT_API_KEY"
        raise ModelError(f"尚未配置模型 API Key，请在项目 .env 中设置 {label}。")
    if vision and not provider.get("supports_vision", True):
        raise ModelError("当前视觉配置未声明支持图像输入，已阻止发送图片。")
    if tools and not provider.get("supports_tools", True):
        raise ModelError("当前模型未声明支持工具调用，无法执行工具循环。")
    if json_mode and not provider.get("supports_json", True):
        raise ModelError("当前模型未声明支持 JSON 输出，无法保证结构化结果。")
    return provider


class Provider:
    def __init__(self, run_id):
        self.run_id = run_id

    def complete(self, messages, tools=None, vision=False, json_mode=False, stage=""):
        try:
            provider = resolve(vision, bool(tools), json_mode)
        except ConfigError as e:
            raise ModelError(str(e)) from e
        key = provider["api_key"]
        base = provider["base_url"]
        model = provider["model"]
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        started_at = now()
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            used = db.execute(
                "SELECT count(*) FROM model_calls WHERE created_at>=?", (day_start_utc(),)
            ).fetchone()[0]
            if used >= settings.daily_model_call_limit:
                raise ModelError("已达到今日模型调用上限。")
            call_id = db.execute(
                "INSERT INTO model_calls(run_id,model,status,supplier,stage,request_id,started_at,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    self.run_id,
                    model,
                    "started",
                    provider["supplier"],
                    stage,
                    request_id,
                    started_at,
                    started_at,
                ),
            ).lastrowid
        event(self.run_id, "model.started", model=model, vision=vision, stage=stage)
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 8000 if vision else 3500,
            "stream": False,
        }
        if tools:
            body.update(tools=tools, tool_choice="auto")
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            with httpx.Client(timeout=settings.model_timeout_seconds) as client:
                r = client.post(
                    base.rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + key},
                    json=body,
                )
            duration_ms = int((time.monotonic() - started) * 1000)
            if r.status_code != 200:
                # Do not persist raw upstream responses, which may echo content or credentials.
                error_class, message = classify_http_error(r.status_code, r.headers)
                self._fail(call_id, error_class, duration_ms)
                raise ModelError(message)
            data = r.json()
            choice = data["choices"][0]
            if choice.get("finish_reason") in ("length", "content_filter"):
                self._fail(call_id, "truncated", duration_ms)
                raise ModelError("模型输出被截断或拦截，本次结果未保存。")
            message = choice["message"]
            if not message.get("content") and not message.get("tool_calls"):
                self._fail(call_id, "empty", duration_ms)
                raise ModelError("模型返回空结果。")
            usage = data.get("usage", {}) or {}
            cost, price_version = _estimate_cost(model, usage)
            with connect() as db:
                db.execute(
                    "UPDATE model_calls SET status=?,usage_json=?,finished_at=?,duration_ms=?,"
                    "input_tokens=?,output_tokens=?,estimated_cost=?,price_version=? WHERE id=?",
                    (
                        "completed",
                        json.dumps(usage),
                        now(),
                        duration_ms,
                        usage.get("prompt_tokens"),
                        usage.get("completion_tokens"),
                        cost,
                        price_version,
                        call_id,
                    ),
                )
            event(
                self.run_id, "model.completed", model=model, usage=usage, stage=stage
            )
            return {
                k: v
                for k, v in message.items()
                if k in ("role", "content", "tool_calls")
            }
        except Exception as e:
            if isinstance(e, ModelError):
                raise
            self._fail(call_id, "connection", int((time.monotonic() - started) * 1000))
            raise ModelError("模型连接失败或响应无效；本次未产生可用结果。") from e

    def _fail(self, call_id, error_class, duration_ms):
        with connect() as db:
            db.execute(
                "UPDATE model_calls SET status=?,finished_at=?,duration_ms=?,error_class=? WHERE id=?",
                ("failed", now(), duration_ms, error_class, call_id),
            )
