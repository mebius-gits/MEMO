from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    # Low reasoning effort cuts gpt-oss latency several-fold for short spoken replies.
    openai_reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None
    openai_max_completion_tokens: int | None = Field(default=None, ge=16, le=8192)


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
