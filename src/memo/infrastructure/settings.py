from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    app_name: str = "MEMO"
    database_url: str = "sqlite:///./memo.db"
    cors_origins: list[str] = []
    notifications_enabled: bool = True
    # Chat Completions-compatible API; incomplete configuration uses templates.
    llm_base_url: AnyHttpUrl | None = None
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_timeout_seconds: float = Field(default=30, gt=0, le=300)


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
