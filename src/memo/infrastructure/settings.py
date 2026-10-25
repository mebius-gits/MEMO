from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from memo.domain.attention import AttentionPolicy


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    app_name: str = "MEMO"
    database_url: str = "sqlite:///./memo.db"
    cors_origins: list[str] = []
    notifications_enabled: bool = True
    # OpenAI-style Chat Completions settings; without a model name replies use templates.
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: AnyHttpUrl = AnyHttpUrl("https://api.openai.com/v1")
    openai_model: str = ""
    openai_timeout_seconds: float = Field(default=30, gt=0, le=300)
    # Optional OpenAI request parameters, sent only when set (some servers reject unknown ones).
    # Reasoning models spend most of their time thinking; "none"/"low" keeps short replies fast.
    openai_reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None
    openai_max_completion_tokens: int | None = Field(default=None, ge=16, le=8192)
    # Compatibility switches for models/proxies that ignore a system role (e.g. Gemma behind
    # a proxy that injects its own) or answer "{}" in JSON mode; the prompt still asks for JSON.
    openai_system_role: bool = True
    openai_json_mode: bool = True
    # Send uploaded images to the model; turn off for models without image input.
    openai_vision: bool = True
    # Look tasks aim to speak within about 20 s of a photo: each photo's analysis and the
    # spoken notice get short budgets; a slow notice falls back to a template.
    openai_vision_timeout_seconds: float = Field(default=8, gt=0, le=120)
    look_notice_timeout_seconds: float = Field(default=5, gt=0, le=60)


class AttentionSettings(BaseSettings):
    """How often MEMO may speak on its own; read by both the API and the MQTT worker."""

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    timezone: str = "Asia/Taipei"
    # Local "HH:MM-HH:MM" (may cross midnight); only danger-level notices speak inside it.
    quiet_hours: str = Field(default="", pattern=r"^$|^\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}$")
    max_proactive_per_hour: int = Field(default=6, ge=1, le=60)

    def policy(self) -> AttentionPolicy:
        return AttentionPolicy.from_settings(
            self.quiet_hours, self.max_proactive_per_hour, self.timezone
        )


class MqttSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MQTT_", env_file=".env", extra="ignore")

    host: str = "localhost"
    port: int = Field(default=1883, ge=1, le=65535)
    client_id: str = Field(default="memo-environment-worker", min_length=1)
    topic: str = "memo/v1/+/telemetry"
    queue_size: int = Field(default=256, ge=1, le=10000)
    max_payload_bytes: int = Field(default=16384, ge=1, le=1048576)
    username: str | None = None
    password: str | None = None
    tls: bool = False


class SearchSettings(BaseSettings):
    """Self-hosted SearXNG for web look-ups; empty url means MEMO says search is not set up."""

    model_config = SettingsConfigDict(env_prefix="SEARCH_", env_file=".env", extra="ignore")

    url: str = ""
    timeout_seconds: float = Field(default=6, gt=0, le=30)
    max_results: int = Field(default=3, ge=1, le=5)
    language: str = "zh-TW"
    cache_seconds: int = Field(default=600, ge=0, le=86400)
