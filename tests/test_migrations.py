import multiprocessing
import threading

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from app.storage.db import Store
from app.storage.migrations import (
    PendingMigrationsError,
    current_revision,
    ensure_up_to_date,
    head_revision,
    migrate,
)


def _tables(url: str) -> set[str]:
    with psycopg.connect(url) as c:
        rows = c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()
    return {r[0] for r in rows}


def _indexes(url: str) -> set[str]:
    with psycopg.connect(url) as c:
        rows = c.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'analyses'").fetchall()
    return {r[0] for r in rows}


def test_migrate_on_empty_database_creates_schema_at_head(database_url):
    migrate(database_url)

    assert {"analyses", "alembic_version"} <= _tables(database_url)
    assert "one_live_analysis_per_clip" in _indexes(database_url)
    assert current_revision(database_url) == head_revision()


def test_migrate_twice_is_a_no_op(database_url):
    migrate(database_url)
    migrate(database_url)

    assert current_revision(database_url) == head_revision()


def test_migrate_adopts_a_database_created_before_migrations(database_url):
    # Databases created by the old startup SCHEMA have the table but no alembic_version.
    with psycopg.connect(database_url) as c:
        c.execute(
            """CREATE TABLE analyses (id TEXT PRIMARY KEY, status TEXT NOT NULL, current_step TEXT,
               external_id TEXT, file_sha256 TEXT NOT NULL, file_path TEXT NOT NULL, cache_key TEXT NOT NULL,
               fresh BOOLEAN NOT NULL DEFAULT FALSE, input_json JSONB NOT NULL, metadata_json JSONB NOT NULL,
               result_json JSONB, error_code TEXT, error_message TEXT, model_id TEXT, prompt_version TEXT,
               rubric_version TEXT, pipeline_version TEXT, cost_usd DOUBLE PRECISION, duration_ms INTEGER,
               created_at TIMESTAMPTZ NOT NULL DEFAULT now(), started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ)"""
        )
        c.execute("INSERT INTO analyses (id, status, file_sha256, file_path, cache_key, input_json, metadata_json) "
                  "VALUES ('an_old', 'completed', 's', 'p', 'k', '{}', '{}')")

    migrate(database_url)

    assert current_revision(database_url) == head_revision()
    with psycopg.connect(database_url) as c:
        assert c.execute("SELECT count(*) FROM analyses").fetchone()[0] == 1


def test_concurrent_processes_migrate_once(database_url):
    # Several containers starting together: the advisory lock serializes them.
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=migrate, args=(database_url,)) for _ in range(3)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)

    assert [p.exitcode for p in procs] == [0, 0, 0]
    assert current_revision(database_url) == head_revision()


def test_concurrent_threads_migrate_without_failing(database_url):
    errors = []

    def run():
        try:
            migrate(database_url)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert current_revision(database_url) == head_revision()


def test_store_init_migrates_to_head(database_url):
    store = Store(database_url)
    store.init(auto_migrate=True)
    store.close()

    assert current_revision(database_url) == head_revision()


def test_ensure_up_to_date_refuses_an_unmigrated_database(database_url):
    with pytest.raises(PendingMigrationsError) as exc:
        ensure_up_to_date(database_url)

    # The message tells whoever runs the service how to fix it.
    assert "make migrate" in str(exc.value)
    assert "AUTO_RUN_MIGRATIONS=true" in str(exc.value)


def test_ensure_up_to_date_passes_once_migrated(database_url):
    migrate(database_url)

    ensure_up_to_date(database_url)


def test_store_init_without_auto_migrate_refuses_an_unmigrated_database(database_url):
    store = Store(database_url)
    try:
        with pytest.raises(PendingMigrationsError):
            store.init(auto_migrate=False)
    finally:
        store.close()

    assert current_revision(database_url) is None


def test_auto_run_migrations_is_off_by_default_and_read_from_env(monkeypatch):
    assert Settings(_env_file=None).auto_run_migrations is False

    monkeypatch.setenv("AUTO_RUN_MIGRATIONS", "true")

    assert Settings(_env_file=None).auto_run_migrations is True


def _app(tmp_path, database_url, auto_run_migrations):
    settings = Settings(
        data_dir=tmp_path, database_url=database_url, run_worker=False,
        auto_run_migrations=auto_run_migrations, _env_file=None,
    )
    return create_app(settings)


def test_service_does_not_start_with_pending_migrations(tmp_path, database_url):
    app = _app(tmp_path, database_url, auto_run_migrations=False)

    with pytest.raises(PendingMigrationsError), TestClient(app):
        pass


def test_service_migrates_on_startup_when_auto_run_is_on(tmp_path, database_url):
    with TestClient(_app(tmp_path, database_url, auto_run_migrations=True)) as c:
        assert c.get("/health").status_code == 200

    assert current_revision(database_url) == head_revision()


def test_service_starts_without_auto_run_once_migrated(tmp_path, database_url):
    migrate(database_url)

    with TestClient(_app(tmp_path, database_url, auto_run_migrations=False)) as c:
        assert c.get("/health").status_code == 200
