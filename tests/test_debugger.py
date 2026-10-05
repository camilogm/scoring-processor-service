import sys
import types

import pytest

from app.debugger import start_debugger
from app.settings import Settings


@pytest.fixture
def fake_debugpy(monkeypatch):
    calls = []
    module = types.SimpleNamespace(listen=lambda address: calls.append(address))
    monkeypatch.setitem(sys.modules, "debugpy", module)
    return calls


def _settings(**debug) -> Settings:
    return Settings(_env_file=None, **debug)


def test_debugger_is_off_by_default(fake_debugpy):
    assert start_debugger(_settings()) is False
    assert fake_debugpy == []


def test_debugger_listens_on_loopback_by_default(fake_debugpy):
    assert start_debugger(_settings(debugpy_port=5678)) is True
    assert fake_debugpy == [("127.0.0.1", 5678)]


def test_debugger_host_is_configurable_for_docker(fake_debugpy):
    start_debugger(_settings(debugpy_port=5678, debugpy_host="0.0.0.0"))

    assert fake_debugpy == [("0.0.0.0", 5678)]


def test_port_in_use_does_not_stop_the_service(monkeypatch):
    def listen(address):
        raise RuntimeError("Can't listen for client connections: [Errno 48] Address already in use")

    monkeypatch.setitem(sys.modules, "debugpy", types.SimpleNamespace(listen=listen))

    assert start_debugger(_settings(debugpy_port=5678)) is False


def test_missing_debugpy_does_not_stop_the_service(monkeypatch):
    monkeypatch.setitem(sys.modules, "debugpy", None)  # makes `import debugpy` raise ImportError

    assert start_debugger(_settings(debugpy_port=5678)) is False
