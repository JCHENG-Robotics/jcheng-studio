"""Optional backend token auth and the non-loopback bind guard."""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import create_app


def _auth_settings(settings: Settings) -> Settings:
    return replace(settings, api_token="local-secret")


def test_non_loopback_bind_requires_a_token(monkeypatch):
    monkeypatch.setenv("BIND_HOST", "0.0.0.0")
    monkeypatch.delenv("ARMPI_BACKEND_TOKEN", raising=False)
    with pytest.raises(ValueError, match="ARMPI_BACKEND_TOKEN"):
        Settings.from_env(mode="mock")


def test_non_loopback_bind_accepts_a_token(monkeypatch):
    monkeypatch.setenv("BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("ARMPI_BACKEND_TOKEN", "remote-secret")
    settings = Settings.from_env(mode="mock")
    assert settings.bind_host == "0.0.0.0"
    assert settings.api_token == "remote-secret"


def test_http_token_guard(settings, clock):
    app = create_app(settings=_auth_settings(settings), clock=clock)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        rejected = client.get("/api/state")
        assert rejected.status_code == 401
        assert rejected.json()["error"]["code"] == "UNAUTHORIZED"
        assert client.get(
            "/api/state", headers={"X-Armpi-Token": "wrong"}
        ).status_code == 401
        assert client.get(
            "/api/state", headers={"X-Armpi-Token": "local-secret"}
        ).status_code == 200
        assert client.get("/api/state?token=local-secret").status_code == 200


def test_websocket_token_guard(settings, clock):
    app = create_app(settings=_auth_settings(settings), clock=clock)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/ws/state"):
                pass
        with client.websocket_connect(
            "/api/ws/state?token=local-secret"
        ) as websocket:
            payload = websocket.receive_json()
            assert "joints" in payload
