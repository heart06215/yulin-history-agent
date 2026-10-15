import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    """Raised for incomplete or contradictory model configuration."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("YULIN_ENV_FILE", ROOT / ".env"),
        env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True,
    )
    app_env: Literal["development", "production"] = "development"
    public_origin: str = ""
    allowed_hosts: str = ""
    data_dir: Path = ROOT / "data"
    glm_api_key: str = Field(default="", repr=False, exclude=True)
    glm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    glm_text_model: str = "glm-4-flash"
    vision_api_key: str = Field(default="", repr=False, exclude=True)
    vision_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    vision_model: str = "glm-4.6v-flash"

    # Optional generic text provider. When all three are set it takes priority;
    # when none are set the GLM_* configuration is used; a partial set is an error.
    text_api_key: str = Field(default="", repr=False, exclude=True)
    text_base_url: str = ""
    text_model: str = ""

    # Explicit capability declarations. The code never guesses what a model can do.
    text_supports_tools: bool = True
    text_supports_json: bool = True
    vision_supports_vision: bool = True
    vision_supports_tools: bool = False

    admin_token: str = Field(default="", repr=False, exclude=True)
    public_requests_per_minute: int = Field(default=12, ge=1, le=600)
    admin_requests_per_minute: int = Field(default=120, ge=1, le=3600)
    max_agent_steps: int = 8
    model_timeout_seconds: int = 90
    max_runs_per_hour: int = 30
    max_queued_runs: int = 20
    daily_model_call_limit: int = 300
    session_model_call_limit: int = 60
    budget_timezone: str = "Asia/Shanghai"
    # Optional JSON: {"model-name": {"input": <price per 1k tokens>, "output": <...>, "currency": "CNY", "version": "2026-01"}}
    price_config_json: str = ""
    batch_max_pages: int = 20
    batch_page_delay_seconds: float = 2.0
    rate_limit_backoff_seconds: int = 90
    rate_limit_max_backoff_seconds: int = 1200
    import_max_megabytes: int = 100
    import_max_pages: int = 1000
    import_max_pixels: int = 80_000_000
    dedup_share_pages: bool = True

    @property
    def production(self) -> bool:
        return self.app_env == "production"

    @property
    def site_origin(self) -> str:
        origin = urlsplit(self.public_origin)
        host = origin.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        port = origin.port
        return f"https://{host}" + (f":{port}" if port and port != 443 else "")

    @property
    def trusted_hosts(self) -> set[str]:
        configured = {host.strip().lower() for host in self.allowed_hosts.split(",") if host.strip()}
        hostname = urlsplit(self.public_origin).hostname
        return configured | ({hostname} if hostname else set()) | {"localhost", "127.0.0.1", "::1"}

    def validate_deployment(self) -> None:
        if not self.production:
            return
        try:
            origin = urlsplit(self.public_origin)
            valid_origin = origin.scheme == "https" and bool(origin.hostname) and not origin.username and not origin.password and not origin.query and not origin.fragment and origin.path in ("", "/")
            _ = origin.port
        except ValueError:
            valid_origin = False
        if not valid_origin:
            raise ConfigError("生产模式必须设置不含路径或凭据的 HTTPS PUBLIC_ORIGIN。")
        if any("*" in host for host in self.trusted_hosts):
            raise ConfigError("生产模式 ALLOWED_HOSTS 不能使用通配符。")
        token = self.admin_token
        if len(token) < 32 or len(set(token)) < 8 or token.lower().startswith(("changeme", "replace", "test-", "your-")):
            raise ConfigError("生产模式 ADMIN_TOKEN 必须使用至少 32 字符的独立随机令牌。")
        for provider in (self.text_provider, self.vision_provider):
            if not provider["api_key"]:
                continue
            try:
                address = urlsplit(provider["base_url"])
                valid_address = address.scheme == "https" and bool(address.hostname) and not address.username and not address.password and not address.query and not address.fragment
                _ = address.port
            except ValueError:
                valid_address = False
            if not valid_address:
                raise ConfigError("生产模式模型地址必须使用 HTTPS，且不能在 URL 中携带凭据。")

    @property
    def db_path(self):
        return self.data_dir / "catalog.sqlite3"

    @property
    def text_provider(self):
        configured = [
            bool(self.text_api_key),
            bool(self.text_base_url),
            bool(self.text_model),
        ]
        if any(configured) and not all(configured):
            raise ConfigError(
                "TEXT_* 配置不完整：请同时设置 TEXT_API_KEY、TEXT_BASE_URL、TEXT_MODEL，"
                "或全部留空以回退到 GLM_*。"
            )
        if all(configured):
            return {
                "supplier": "text",
                "api_key": self.text_api_key,
                "base_url": self.text_base_url,
                "model": self.text_model,
                "supports_tools": self.text_supports_tools,
                "supports_json": self.text_supports_json,
                "supports_vision": False,
            }
        return {
            "supplier": "glm",
            "api_key": self.glm_api_key,
            "base_url": self.glm_base_url,
            "model": self.glm_text_model,
            "supports_tools": self.text_supports_tools,
            "supports_json": self.text_supports_json,
            "supports_vision": False,
        }

    @property
    def vision_provider(self):
        return {
            "supplier": "vision",
            "api_key": self.vision_api_key or self.glm_api_key,
            "base_url": self.vision_base_url,
            "model": self.vision_model,
            "supports_tools": self.vision_supports_tools,
            "supports_json": True,
            "supports_vision": self.vision_supports_vision,
        }


settings = Settings()
