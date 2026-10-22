from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BUDDY_", env_file=".env", extra="ignore")

    app_name: str = "MEMO"
    database_url: str = "sqlite:///./buddy.db"
