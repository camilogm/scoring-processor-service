"""Schema migrations (Alembic, raw SQL). Applied on startup by Store.init and by `make migrate`."""

import threading
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, pool

SCRIPT_LOCATION = Path(__file__).with_name("alembic")

# Arbitrary key: serializes migrations when several processes start at once.
MIGRATION_LOCK = 7_401_223

# alembic.context is module-global, so two threads of one process can't migrate at the same time.
_in_process = threading.Lock()


def engine(database_url: str) -> Engine:
    # psycopg opens the connection, so any conninfo it accepts works (URL or key=value DSN),
    # with no URL rewriting and no configparser interpolation of percent-encoded passwords.
    return create_engine(
        "postgresql+psycopg://", creator=lambda: psycopg.connect(database_url), poolclass=pool.NullPool
    )


def alembic_config(database_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(SCRIPT_LOCATION))
    cfg.attributes["database_url"] = database_url
    return cfg


class PendingMigrationsError(RuntimeError):
    pass


def migrate(database_url: str, revision: str = "head") -> None:
    with _in_process:
        command.upgrade(alembic_config(database_url), revision)


def head_revision() -> str | None:
    return ScriptDirectory(str(SCRIPT_LOCATION)).get_current_head()


def current_revision(database_url: str) -> str | None:
    eng = engine(database_url)
    try:
        with eng.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        eng.dispose()


def ensure_up_to_date(database_url: str) -> None:
    """Refuse to start on a database behind the code, instead of failing on the first query."""
    current, head = current_revision(database_url), head_revision()
    if current != head:
        raise PendingMigrationsError(
            f"database is at migration {current or '<none>'}, the code expects {head}. "
            "Run `make migrate` (uv run alembic upgrade head) or start with AUTO_RUN_MIGRATIONS=true."
        )
