from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError


class ListenerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    hmac_secret: str
    fallback_event: str | None = "recording.transcription.completed"


class SpeakrConfig(BaseModel):
    base_url: str
    api_token: str


class OllamaConfig(BaseModel):
    host: str
    model: str = "llama3"
    timeout_seconds: int = 90
    scoping_timeout_seconds: int = 180
    scoping_batch_size: int = Field(default=8, ge=1, le=100)
    scoping_context_tokens: int = Field(default=32768, ge=4096, le=131072)


class OneNoteConfig(BaseModel):
    notebook: str
    section: str
    manual_selection: bool = False


class ScopingConfig(BaseModel):
    enabled: bool = True
    api_token: str | None = None
    database_file: str = "scoping_jobs.db"
    output_directory: str = "generated/scoping"


class NotificationsConfig(BaseModel):
    enabled: bool = True


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "speakrbridge.log"


class AppConfig(BaseModel):
    listener: ListenerConfig
    speakr: SpeakrConfig
    ollama: OllamaConfig
    onenote: OneNoteConfig
    scoping: ScopingConfig = Field(default_factory=ScopingConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def load_config(config_path: str | Path = "config.yaml") -> AppConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Missing configuration file: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid configuration in {path}: {exc}") from exc
