from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import Mode
from app.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = PROJECT_ROOT / "configs" / "joint_mapping.yaml"


def _settings(tmp_path: Path) -> Settings:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>studio</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('studio');", encoding="utf-8")
    return Settings(
        mode=Mode.MOCK,
        robot_id="armpi-ultra-01",
        bind_host="127.0.0.1",
        port=8000,
        db_path=tmp_path / "studio.db",
        mapping_path=MAPPING_PATH,
        frontend_dist=dist,
        log_level="info",
        hardware_motion_enabled=False,
    )


def test_client_side_routes_fall_back_to_index(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        for route in (
            "/",
            "/joints",
            "/diagnostics",
            "/trajectory",
            "/experiments",
            "/visual",
            "/events",
            "/settings",
        ):
            response = client.get(route)
            assert response.status_code == 200, route
            assert "<title>studio</title>" in response.text, route

        asset = client.get("/assets/app.js")
        assert asset.status_code == 200
        assert "console.log" in asset.text


def test_unknown_api_paths_stay_404(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/api/does-not-exist")
        assert response.status_code == 404
        assert "studio" not in response.text


def test_spa_shell_is_not_cached_but_assets_are(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        for route in ("/", "/experiments"):
            shell = client.get(route)
            assert shell.headers["cache-control"] == "no-store, must-revalidate", route

        asset = client.get("/assets/app.js")
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
