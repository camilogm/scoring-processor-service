"""Alembic environment. Migrations are hand-written SQL (no ORM models, so no autogenerate)."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import text

from app.settings import get_settings
from app.storage.migrations import MIGRATION_LOCK, engine

config = context.config

# Only the CLI (alembic.ini) configures logging; when the app migrates on startup it keeps its own.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# The app passes its URL in; the CLI reads DATABASE_URL / .env like the service does.
database_url = config.attributes.get("database_url") or get_settings().database_url


def run_offline() -> None:
    context.configure(dialect_name="postgresql", literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    eng = engine(database_url)
    try:
        with eng.connect() as conn:
            context.configure(connection=conn)
            with context.begin_transaction():
                # Held until commit: a concurrent process waits here, then finds the schema at head.
                conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK})
                context.run_migrations()
    finally:
        eng.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
