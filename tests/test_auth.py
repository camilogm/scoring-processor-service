import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from app.settings import Settings


def _settings(tmp_path, database_url, **auth) -> Settings:
    return Settings(
        data_dir=tmp_path, database_url=database_url, run_worker=False, auto_run_migrations=True, _env_file=None,
        **auth,
    )


@pytest.fixture
def locked(tmp_path, database_url):
    settings = _settings(tmp_path, database_url, basic_auth_user="camilo", basic_auth_password="s3cret")
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.mark.parametrize("path", ["/demo", "/analyses", "/analyses/an_x/report", "/docs", "/openapi.json"])
def test_every_page_asks_for_credentials(locked, path):
    res = locked.get(path)

    assert res.status_code == 401
    assert res.headers["www-authenticate"] == 'Basic realm="clip-scoring"'
    assert res.json() == {"error": {"code": "unauthorized", "message": "Sign in with the deployment's credentials."}}


def test_wrong_credentials_are_rejected(locked):
    assert locked.get("/analyses", auth=("camilo", "nope")).status_code == 401
    assert locked.get("/analyses", auth=("someone", "s3cret")).status_code == 401


def test_malformed_authorization_header_is_rejected(locked):
    assert locked.get("/analyses", headers={"Authorization": "Basic !!!not-base64"}).status_code == 401


def test_right_credentials_get_through(locked):
    assert locked.get("/analyses", auth=("camilo", "s3cret")).status_code == 200


def test_health_stays_open_for_the_platform_check(locked):
    assert locked.get("/health").status_code == 200


def test_auth_is_off_without_credentials(tmp_path, database_url):
    with TestClient(create_app(_settings(tmp_path, database_url))) as c:
        assert c.get("/analyses").status_code == 200


def test_half_configured_auth_refuses_to_start():
    with pytest.raises(ValidationError, match="BASIC_AUTH_USER and BASIC_AUTH_PASSWORD"):
        Settings(basic_auth_user="camilo", _env_file=None)
