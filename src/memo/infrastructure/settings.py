from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BUDDY_", env_file=".env", extra="ignore")

    app_name: str = "MEMO"
    database_url: str = "sqlite:///./buddy.db"
    cors_origins: list[str] = []


class MqttSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEMO_MQTT_", env_file=".env", extra="ignore")

    host: str = "localhost"
    port: int = Field(default=1883, ge=1, le=65535)
    client_id: str = Field(default="memo-environment-worker", min_length=1)
    topic: str = "buddy/v1/+/telemetry"
    queue_size: int = Field(default=256, ge=1, le=10000)
    max_payload_bytes: int = Field(default=16384, ge=1, le=1048576)
    username: str | None = None
    password: str | None = None
    tls: bool = False
