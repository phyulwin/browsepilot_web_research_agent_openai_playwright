# backend/config.py — validated server-only environment configuration.
from pathlib import Path

from dotenv import dotenv_values
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Resolve the credential file relative to the project, never the shell's working directory.
DOTENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def project_api_key():
    # Disable interpolation so ${OPENAI_API_KEY} cannot silently import a system credential.
    # An absent or blank file value intentionally overrides every other settings source.
    values = dotenv_values(DOTENV_FILE, encoding="utf-8-sig", interpolate=False)
    return {"openai_api_key": (values.get("OPENAI_API_KEY") or "").strip()}


class Settings(BaseSettings):
    """Load bounded execution settings without exposing credentials to clients."""

    model_config = SettingsConfigDict(env_file=DOTENV_FILE, env_file_encoding="utf-8-sig", extra="ignore")
    openai_api_key: str = Field(default="", repr=False)
    openai_model: str = "gpt-4.1-mini"
    data_dir: Path = Path("data")
    frontend_dir: Path = Path("frontend/dist")
    max_steps: int = Field(default=18, ge=1, le=60)
    max_runtime_seconds: int = Field(default=240, ge=30, le=900)
    max_concurrent_sessions: int = Field(default=2, ge=1, le=4)
    max_session_tokens: int = Field(default=60000, ge=8000, le=200000)
    max_model_calls: int = Field(default=32, ge=4, le=100)
    api_timeout_seconds: int = Field(default=35, ge=5, le=90)
    search_providers: str = "bing,duckduckgo,google"
    access_password: str = ""
    app_env: str = "development"
    cookie_secure: bool = False
    allowed_origins: str = ""

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        # Highest priority is reserved for the exact project-file API key, including an empty value.
        # Other settings retain their normal environment/constructor/file precedence.
        return (project_api_key, init_settings, env_settings, dotenv_settings, file_secret_settings)

    @model_validator(mode="after")
    def production_access(self):
        # Require an access gate when explicitly deployed in production mode.
        providers = [name.strip().lower() for name in self.search_providers.split(",") if name.strip()]
        if not providers or any(name not in {"bing", "duckduckgo", "google"} for name in providers):
            raise ValueError("SEARCH_PROVIDERS must list bing, duckduckgo, or google in fallback order")
        self.search_providers = ",".join(dict.fromkeys(providers))
        if self.app_env == "production" and len(self.access_password) < 16:
            raise ValueError("Production requires ACCESS_PASSWORD with at least 16 characters")
        return self


settings = Settings()
