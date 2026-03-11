"""Tests for application settings."""

from health_coach.settings import Settings


def test_default_settings() -> None:
    settings = Settings(database_url="sqlite+aiosqlite://")
    assert settings.environment == "dev"
    assert settings.is_sqlite
    assert not settings.is_postgres
    assert settings.log_format == "console"
    assert settings.app_mode == "all"


def test_postgres_scheme_normalization() -> None:
    settings = Settings(database_url="postgresql://user:pass@localhost/db")
    assert settings.database_url == "postgresql+psycopg://user:pass@localhost/db"


def test_postgres_shorthand_normalization() -> None:
    settings = Settings(database_url="postgres://user:pass@localhost/db")
    assert settings.database_url == "postgresql+psycopg://user:pass@localhost/db"


def test_sqlite_no_normalization() -> None:
    settings = Settings(database_url="sqlite+aiosqlite:///./test.db")
    assert settings.database_url == "sqlite+aiosqlite:///./test.db"
    assert settings.is_sqlite


def test_is_postgres_flag() -> None:
    settings = Settings(database_url="postgresql+psycopg://user:pass@localhost/db")
    assert settings.is_postgres
    assert not settings.is_sqlite


def test_quiet_hours_defaults() -> None:
    settings = Settings(database_url="sqlite+aiosqlite://")
    assert settings.quiet_hours_start == 21
    assert settings.quiet_hours_end == 8
