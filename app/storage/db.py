"""Postgres storage. Every state change is a committed transaction before it's reported."""

import secrets
import time
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

LIVE_STATUSES = ("queued", "processing", "completed")

# Arbitrary key: serializes schema creation when several processes start at once.
_SCHEMA_LOCK = 7_401_223

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id               TEXT PRIMARY KEY,
    status           TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'completed', 'failed')),
    current_step     TEXT,
    external_id      TEXT,
    file_sha256      TEXT NOT NULL,
    file_path        TEXT NOT NULL,
    cache_key        TEXT NOT NULL,
    fresh            BOOLEAN NOT NULL DEFAULT FALSE,
    input_json       JSONB NOT NULL,
    metadata_json    JSONB NOT NULL,
    result_json      JSONB,
    error_code       TEXT,
    error_message    TEXT,
    model_id         TEXT,
    prompt_version   TEXT,
    rubric_version   TEXT,
    pipeline_version TEXT,
    cost_usd         DOUBLE PRECISION,
    duration_ms      INTEGER,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ
);

-- One live analysis per clip: the database, not app code, decides who wins a race.
-- Failed rows and fresh=true runs fall outside the index.
CREATE UNIQUE INDEX IF NOT EXISTS one_live_analysis_per_clip
    ON analyses (cache_key)
    WHERE status IN ('queued', 'processing', 'completed') AND NOT fresh;
"""


def new_id() -> str:
    # Time-sortable: millisecond prefix + randomness.
    return f"an_{int(time.time() * 1000):012x}{secrets.token_hex(6)}"


class Store:
    def __init__(self, database_url: str):
        # Thread-safe: shared by request handlers and the worker thread.
        self.pool = ConnectionPool(
            database_url, min_size=1, max_size=10, kwargs={"row_factory": dict_row}, open=False
        )

    @contextmanager
    def _tx(self):
        with self.pool.connection() as conn:  # commits on success, rolls back on error
            yield conn

    def init(self) -> None:
        self.pool.open(wait=True, timeout=10)
        with self._tx() as c:
            c.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK,))
            c.execute(SCHEMA)

    def close(self) -> None:
        self.pool.close()

    def get(self, analysis_id: str) -> dict | None:
        with self._tx() as c:
            return c.execute("SELECT * FROM analyses WHERE id = %s", (analysis_id,)).fetchone()

    def create_or_get(
        self,
        *,
        cache_key: str,
        fresh: bool,
        file_sha256: str,
        file_path: str,
        external_id: str | None,
        input_facts: dict,
        metadata: dict,
        model_id: str,
        prompt_version: str,
        rubric_version: str,
        pipeline_version: str,
    ) -> tuple[dict, bool]:
        """Insert a queued analysis, or return the live one for the same clip. Returns (row, created)."""
        for _ in range(3):
            with self._tx() as c:
                # A concurrent insert of the same clip blocks here until it commits, then conflicts.
                created = c.execute(
                    """
                    INSERT INTO analyses (id, status, external_id, file_sha256, file_path, cache_key, fresh,
                        input_json, metadata_json, model_id, prompt_version, rubric_version, pipeline_version)
                    VALUES (%s, 'queued', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    RETURNING *
                    """,
                    (
                        new_id(), external_id, file_sha256, file_path, cache_key, fresh,
                        Jsonb(input_facts), Jsonb(metadata), model_id, prompt_version,
                        rubric_version, pipeline_version,
                    ),
                ).fetchone()
                if created:
                    return created, True
                existing = c.execute(
                    "SELECT * FROM analyses WHERE cache_key = %s AND NOT fresh AND status = ANY(%s)",
                    (cache_key, list(LIVE_STATUSES)),
                ).fetchone()
                if existing:
                    return existing, False
            # The live row failed between our insert and select: try again.
        raise RuntimeError("could not insert or find analysis after 3 attempts")

    def mark_processing(self, analysis_id: str, step: str) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE analyses SET status = 'processing', current_step = %s, started_at = now() WHERE id = %s",
                (step, analysis_id),
            )

    def set_step(self, analysis_id: str, step: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE analyses SET current_step = %s WHERE id = %s", (step, analysis_id))

    def complete(self, analysis_id: str, result: dict, *, cost_usd: float, duration_ms: int) -> None:
        with self._tx() as c:
            c.execute(
                """UPDATE analyses SET status = 'completed', current_step = NULL, result_json = %s,
                   model_id = %s, cost_usd = %s, duration_ms = %s, finished_at = now() WHERE id = %s""",
                (Jsonb(result), result["provenance"]["model"], cost_usd, duration_ms, analysis_id),
            )

    def fail(self, analysis_id: str, code: str, message: str) -> None:
        with self._tx() as c:
            c.execute(
                """UPDATE analyses SET status = 'failed', current_step = NULL, error_code = %s, error_message = %s,
                   finished_at = now() WHERE id = %s""",
                (code, message[:2000], analysis_id),
            )

    def sweep_interrupted(self) -> int:
        """On startup: anything left queued or processing was lost with the in-memory queue."""
        with self._tx() as c:
            cur = c.execute(
                """UPDATE analyses SET status = 'failed', current_step = NULL, error_code = 'interrupted_by_restart',
                   error_message = 'The service restarted before this analysis finished. Resubmit the clip.',
                   finished_at = now() WHERE status IN ('queued', 'processing')"""
            )
            return cur.rowcount
