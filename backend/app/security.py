"""Deployment safeguards. Private values never appear in errors or responses."""

import re
import time
from collections import OrderedDict, deque
from urllib.parse import urlsplit
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from .config import settings


def redact_error(value: object) -> str:
    message = str(value)
    for secret in sorted((settings.glm_api_key, settings.vision_api_key, settings.text_api_key, settings.admin_token), key=len, reverse=True):
        if secret:
            message = message.replace(secret, "[已隐藏密钥]")
    message = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}", r"\1[已隐藏令牌]", message)
    message = re.sub(r"(?i)([?&](?:api[_-]?key|token|secret|password)=)[^&\s]+", r"\1[已隐藏凭据]", message)
    return message


class SecurityMiddleware:
    """Pure ASGI middleware preserves streaming; limits use the trusted peer IP."""

    def __init__(self, app):
        self.app = app
        self.windows = OrderedDict()

    def allow(self, key: tuple[str, str], limit: int) -> bool:
        now = time.monotonic()
        bucket = self.windows.setdefault(key, deque())
        while bucket and bucket[0] <= now - 60:
            bucket.popleft()
        self.windows.move_to_end(key)
        while len(self.windows) > 10000:
            self.windows.popitem(last=False)
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        path = scope["path"]
        method = scope["method"]
        unsafe = method not in ("GET", "HEAD", "OPTIONS")

        async def protected_send(message):
            if message["type"] == "http.response.start":
                protected = list(message.get("headers", []))
                names = {name.lower() for name, _ in protected}
                for name, value in ((b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"), (b"referrer-policy", b"same-origin")):
                    if name not in names:
                        protected.append((name, value))
                if path.startswith("/api/"):
                    protected = [(name, value) for name, value in protected if name.lower() != b"cache-control"]
                    protected.append((b"cache-control", b"no-store"))
                message = {**message, "headers": protected}
            await send(message)

        if settings.production:
            try:
                authority = urlsplit("//" + headers.get("host", ""))
                valid_host = authority.hostname in settings.trusted_hosts and not authority.username and not authority.password and not authority.path and not authority.query and not authority.fragment
                _ = authority.port
            except ValueError:
                valid_host = False
            if not valid_host:
                return await JSONResponse({"detail": "无效的访问域名。"}, status_code=400)(scope, receive, protected_send)
            if unsafe:
                origin = headers.get("origin")
                if headers.get("sec-fetch-site") == "cross-site" or (origin and origin != settings.site_origin):
                    return await JSONResponse({"detail": "请求来源不受信任。"}, status_code=403)(scope, receive, protected_send)
                if scope.get("scheme") != "https":
                    return await JSONResponse({"detail": "生产写入请求需要 HTTPS。"}, status_code=400)(scope, receive, protected_send)
            peer = (scope.get("client") or ("unknown", 0))[0]
            if path.startswith("/api/v1/admin/"):
                category, limit = "admin", settings.admin_requests_per_minute
            elif method == "POST" and path in ("/api/v1/sessions", "/api/v1/agent/runs"):
                category, limit = "public-write", settings.public_requests_per_minute
            else:
                category, limit = "", 0
            if category and not self.allow((peer, category), limit):
                return await JSONResponse({"detail": "请求过于频繁，请稍后重试。"}, status_code=429, headers={"Retry-After": "60"})(scope, receive, protected_send)

        await self.app(scope, receive, protected_send)
