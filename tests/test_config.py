import pytest
from pydantic import ValidationError

from app.config import Settings


def test_accepts_the_psycopg_scheme() -> None:
    settings = Settings(database_url="postgresql+psycopg://u:p@localhost:5432/kamleon")

    assert settings.database_url.scheme == "postgresql+psycopg"


@pytest.mark.parametrize(
    "url", ["postgresql://u:p@localhost/kamleon", "postgresql+asyncpg://u:p@localhost/kamleon"]
)
def test_rejects_other_schemes_naming_the_expected_one(url: str) -> None:
    with pytest.raises(ValidationError, match=r"postgresql\+psycopg://"):
        Settings(database_url=url)
