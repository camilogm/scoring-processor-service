import os
import secrets

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from app.config.rubric import load_rubric
from app.pipeline.model import DimensionDraft

# Server used to create one throwaway database per test (docker compose up -d db).
ADMIN_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://clip:clip@localhost:5432/postgres")


@pytest.fixture
def database_url():
    name = f"test_{secrets.token_hex(6)}"
    try:
        admin = psycopg.connect(ADMIN_DATABASE_URL, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.fail(f"Postgres not reachable at {ADMIN_DATABASE_URL} (run: docker compose up -d db): {exc}")
    with admin:
        admin.execute(f'CREATE DATABASE "{name}"')
        yield make_conninfo(ADMIN_DATABASE_URL, dbname=name)
        admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def rubric():
    return load_rubric()


@pytest.fixture
def make_drafts(rubric):
    """Build one draft per rubric dimension. Pass score=None to mark it not applicable."""

    def _make(confidence: str = "high", fixes: dict | None = None, **scores):
        fixes = fixes or {}
        drafts = {}
        for spec in rubric.dimensions:
            score = scores.get(spec.id, 5)
            drafts[spec.id] = DimensionDraft(
                id=spec.id,
                label=spec.label,
                weight=spec.weight,
                basis=spec.basis,
                score=score,
                applicable=score is not None,
                confidence=confidence,
                evidence=[],
                fix=fixes.get(spec.id, f"fix {spec.id}"),
            )
        return drafts

    return _make


@pytest.fixture
def base_signals():
    return {
        "duration_s": 60.0,
        "word_count": 150,
        "time_to_first_word_s": 0.5,
        "first_word": "Permits",
        "first_word_is_conjunction": False,
        "speech_at_start": False,
        "speech_at_end": False,
        "words_per_minute": 160.0,
        "speech_ratio": 0.85,
        "pause_count": 0,
        "pauses": [],
        "longest_pause": None,
        "cuts_per_minute": 4.0,
        "cuts_first_3s": 1,
        "loudness_lufs": -14.5,
        "true_peak_dbtp": -1.5,
        "silence_total_s": 0.0,
        "avg_logprob": -0.25,
        "has_audio": True,
    }
