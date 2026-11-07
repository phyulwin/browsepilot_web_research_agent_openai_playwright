# backend/config.py — validated server-only environment configuration.
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load bounded execution settings without exposing credentials to clients."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    data_dir: Path = Path("data")
    frontend_dir: Path = Path("frontend/dist")
    max_steps: int = Field(default=18, ge=1, le=60)
    max_runtime_seconds: int = Field(default=240, ge=30, le=900)
    max_concurrent_sessions: int = Field(default=2, ge=1, le=4)
    max_session_tokens: int = Field(default=60000, ge=8000, le=200000)
    max_model_calls: int = Field(default=32, ge=4, le=100)
    api_timeout_seconds: int = Field(default=35, ge=5, le=90)
    access_password: str = ""
    app_env: str = "development"
    cookie_secure: bool = False
    allowed_origins: str = ""

    @model_validator(mode="after")
    def production_access(self):
        # Require an access gate when explicitly deployed in production mode.
        if self.app_env == "production" and len(self.access_password) < 16:
            raise ValueError("Production requires ACCESS_PASSWORD with at least 16 characters")
        return self


settings = Settings()
