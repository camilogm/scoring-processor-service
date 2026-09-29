from concurrent.futures import ThreadPoolExecutor

import pytest

from app.storage.db import Store


@pytest.fixture
def store(database_url):
    s = Store(database_url)
    s.init()
    yield s
    s.close()


def _create(store, cache_key="k1", fresh=False):
    return store.create_or_get(
        cache_key=cache_key,
        fresh=fresh,
        file_sha256="sha",
        file_path="/tmp/clip.mp4",
        external_id="C07",
        input_facts={"duration_s": 42.0, "has_audio": True},
        metadata={"title": "Permits"},
        model_id="m",
        prompt_version="p1",
        rubric_version="r1",
        pipeline_version="0.1.0",
    )


def test_init_is_idempotent(store):
    store.init()


def test_json_columns_round_trip_as_dicts(store):
    row, created = _create(store)

    assert created is True
    assert row["status"] == "queued"
    assert row["fresh"] is False
    assert row["input_json"] == {"duration_s": 42.0, "has_audio": True}
    assert row["metadata_json"] == {"title": "Permits"}
    assert row["created_at"].tzinfo is not None


def test_concurrent_submissions_of_same_clip_create_one_analysis(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _create(store), range(8)))

    assert sum(created for _, created in results) == 1
    assert len({row["id"] for row, _ in results}) == 1


def test_fresh_runs_bypass_the_live_index(store):
    first, _ = _create(store)
    fresh, created = _create(store, fresh=True)

    assert created is True
    assert fresh["id"] != first["id"]


def test_complete_stores_result_and_cost(store):
    row, _ = _create(store)
    store.mark_processing(row["id"], "extract")

    store.complete(row["id"], {"provenance": {"model": "gemma3"}}, cost_usd=0.0021, duration_ms=900)

    done = store.get(row["id"])
    assert done["status"] == "completed"
    assert done["result_json"] == {"provenance": {"model": "gemma3"}}
    assert done["cost_usd"] == 0.0021
    assert done["started_at"] is not None and done["finished_at"] is not None


def test_sweep_fails_only_unfinished_runs(store):
    queued, _ = _create(store, "a")
    processing, _ = _create(store, "b")
    store.mark_processing(processing["id"], "extract")
    done, _ = _create(store, "c")
    store.complete(done["id"], {"provenance": {"model": "m"}}, cost_usd=0.0, duration_ms=1)

    assert store.sweep_interrupted() == 2
    assert store.get(queued["id"])["error_code"] == "interrupted_by_restart"
    assert store.get(done["id"])["status"] == "completed"
