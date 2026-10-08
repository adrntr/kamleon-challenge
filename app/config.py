from functools import lru_cache

from pydantic import PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DATABASE_SCHEME = "postgresql+psycopg"


def require_psycopg_scheme(url: PostgresDsn) -> PostgresDsn:
    """Reject any other driver instead of rewriting the URL: configuration is never guessed."""
    if url.scheme != DATABASE_SCHEME:
        raise ValueError(f"must use the {DATABASE_SCHEME}:// scheme, got {url.scheme}://")
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: PostgresDsn

    _check_scheme = field_validator("database_url")(require_psycopg_scheme)


@lru_cache
def get_settings() -> Settings:
    return Settings()
