from functools import lru_cache

from cryptography.fernet import Fernet
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    database_url: str = "sqlite:///./data/app.db"
    redis_url: str = ""
    app_env: str = "development"
    secret_key: str = ""
    encryption_key: str = ""
    storage_dir: str = "./data/files"
    frontend_dist: str = "./frontend/dist"
    session_hours: int = 12
    sandbox_enabled: bool = False

    @model_validator(mode="after")
    def validate_secrets(self):
        if self.sandbox_enabled:
            raise ValueError("Sandbox execution is disabled")
        if self.app_env == "production":
            if not self.redis_url:
                raise ValueError("Production REDIS_URL is required")
            if len(self.secret_key) < 32 or len(set(self.secret_key)) < 12:
                raise ValueError("Production SECRET_KEY must be a strong random secret")
            if not self.encryption_key:
                raise ValueError("Production ENCRYPTION_KEY is required")
        if self.encryption_key:
            Fernet(self.encryption_key.encode())
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
