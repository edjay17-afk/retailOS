"""Runtime configuration, read from environment variables or a .env file."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    storehub_username: str = ""
    storehub_token: str = ""
    storehub_base_url: str = "https://api.storehubhq.com"
    storehub_rps: float = Field(default=2.0, le=2.5)  # hard ceiling below StoreHub's limit of 3

    host: str = "127.0.0.1"
    port: int = 8000
    behind_proxy: bool = False  # true when a reverse proxy (Caddy) fronts the app: it then trusts X-Forwarded-Proto so cookies are marked secure
    base_url: str = ""  # how people reach this server, e.g. http://192.168.1.10:8000 — used for deep links in Lark alerts
    secret_key: str = "change-me"
    data_dir: Path = BASE_DIR / "data"

    # AI layer: provider "auto" uses ANTHROPIC_API_KEY, else GEMINI_API_KEY, else the local Claude command line, else a local Ollama model, else AI is off
    ai_provider: str = "auto"  # auto | api | gemini | cli | ollama | off
    anthropic_api_key: str = ""
    ai_model: str = "claude-sonnet-5-5"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"  # Google's alias for its current Flash model (free-tier keys have no Pro quota); set GEMINI_MODEL=gemini-pro-latest on a billed key
    gemini_fallback_models: str = "gemini-3.5-flash,gemini-3.1-flash-lite"  # tried in order when the main model is overloaded
    ai_cli_path: str = ""
    ai_cli_model: str = ""  # model for provider "cli", e.g. claude-haiku-5-5; empty = whatever the signed-in Claude uses by default
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_fallback: bool = True  # when the main model is down or over quota, try the local Ollama model instead

    # StoreHub back-office robot: signs in with a saved browser profile, exports the stock transfers and drops the CSV in the inbox folder
    storehub_bo_url: str = "https://dailycentro.storehubhq.com"
    storehub_bo_email: str = ""  # the back-office login the robot signs in with (type it in .env yourself)
    storehub_bo_password: str = ""
    robot_enabled: bool = True  # a kill switch: the robot only runs when the back-office login is also set
    robot_every_minutes: int = 60  # the robot runs when the last import is at least this old; checked every 10 minutes, so it also catches up after the PC sleeps
    robot_headless: bool = True

    lark_app_id: str = ""
    lark_app_secret: str = ""
    lark_base_token: str = ""
    lark_alert_chat_id: str = ""  # optional: a Lark chat that receives critical alerts

    # fixed UTC offset: the Philippines has no daylight saving time
    tz_offset_hours: int = 8
    scheduler_enabled: bool = True

    @property
    def db_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'retailos.db').as_posix()}"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def robot_dir(self) -> Path:
        return self.data_dir / "robot"

    @property
    def transfer_inbox(self) -> Path:
        return self.data_dir / "imports" / "stock-transfers"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
        for d in (_config.data_dir, _config.upload_dir, _config.backup_dir, _config.log_dir):
            d.mkdir(parents=True, exist_ok=True)
    return _config


def set_config(cfg: Config) -> None:
    """Used by tests to point at a temporary data directory."""
    global _config
    _config = cfg
    for d in (cfg.data_dir, cfg.upload_dir, cfg.backup_dir, cfg.log_dir):
        d.mkdir(parents=True, exist_ok=True)
