import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings

CONTRACT = json.loads((Path(__file__).parents[1] / "docs" / "report-example.json").read_text())


@pytest.fixture
def client(tmp_path, database_url):
    settings = Settings(data_dir=tmp_path, database_url=database_url, run_worker=False, _env_file=None)
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def spec(client):
    return client.get("/openapi.json").json()


def _codes(spec, method, path):
    return set(spec["paths"][path][method]["responses"])


def test_swagger_ui_is_served(client):
    res = client.get("/docs")

    assert res.status_code == 200
    assert "swagger" in res.text.lower()


def test_documented_status_codes_match_the_real_ones(spec):
    assert _codes(spec, "post", "/analyses") == {"200", "202", "400", "413", "415", "422", "500"}
    assert _codes(spec, "get", "/analyses/{analysis_id}") == {"200", "404", "500"}
    assert _codes(spec, "get", "/analyses/{analysis_id}/report") == {"200", "404", "500"}
    assert _codes(spec, "get", "/health") == {"200"}


def test_framework_validation_error_is_not_advertised(spec):
    # Our handler turns request validation errors into 400 invalid_request, never FastAPI's 422 shape.
    assert "HTTPValidationError" not in spec["components"]["schemas"]


def test_analysis_schema_documents_the_contract(spec):
    schemas = spec["components"]["schemas"]
    analysis = schemas["Analysis"]

    assert list(analysis["properties"]) == list(CONTRACT)
    assert list(schemas["Dimension"]["properties"]) == list(CONTRACT["dimensions"][0])
    assert list(schemas["Signals"]["properties"]) == list(CONTRACT["signals"])
    assert list(schemas["Provenance"]["properties"]) == list(CONTRACT["provenance"])
    assert set(schemas["ErrorResponse"]["properties"]) == {"error"}


def test_error_responses_use_the_error_schema(spec):
    for code in ("400", "413", "415", "422"):
        content = spec["paths"]["/analyses"]["post"]["responses"][code]["content"]["application/json"]
        assert content["schema"]["$ref"].endswith("/ErrorResponse")


def test_report_errors_are_documented_as_json_not_html(spec):
    # The report returns HTML, but its errors are JSON like every other endpoint.
    content = spec["paths"]["/analyses/{analysis_id}/report"]["get"]["responses"]["404"]["content"]

    assert list(content) == ["application/json"]
    assert content["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
