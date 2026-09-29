"""initial analyses schema

IF NOT EXISTS so databases created before migrations (by the old startup schema) are adopted as-is.

Revision ID: 0001
Revises:
Create Date: 2026-09-28
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
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
    )
    """)
    # One live analysis per clip: the database, not app code, decides who wins a race.
    # Failed rows and fresh=true runs fall outside the index.
    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS one_live_analysis_per_clip
        ON analyses (cache_key)
        WHERE status IN ('queued', 'processing', 'completed') AND NOT fresh
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS analyses")
