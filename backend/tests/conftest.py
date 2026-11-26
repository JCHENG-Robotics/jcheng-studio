from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.mock import MockRobotAdapter
from app.config import Settings
from app.domain.enums import Mode
from app.domain.mapping import MappingConfig, load_mapping
from app.main import create_app
from app.persistence.store import EventStore
from app.service import RobotService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = PROJECT_ROOT / "configs" / "joint_mapping.yaml"
TEST_URDF = Path(__file__).resolve().parent / "fixtures" / "armpi_ultra_test.urdf"


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture()
def mapping() -> MappingConfig:
    return load_mapping(MAPPING_PATH)


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        mode=Mode.MOCK,
        robot_id="armpi-ultra-01",
        bind_host="127.0.0.1",
        port=8000,
        db_path=tmp_path / "studio.db",
        mapping_path=MAPPING_PATH,
        urdf_path=TEST_URDF,
        frontend_dist=tmp_path / "dist",
        log_level="info",
        hardware_motion_enabled=False,
    )


@pytest.fixture()
def service(settings: Settings, mapping: MappingConfig, clock: FakeClock):
    store = EventStore(settings.db_path)
    adapter = MockRobotAdapter(mapping, clock=clock)
    svc = RobotService(settings, mapping, adapter, store, clock=clock)
    yield svc
    svc.close()


@pytest.fixture()
def client(settings: Settings, clock: FakeClock):
    app = create_app(settings=settings, clock=clock)
    with TestClient(app) as test_client:
        yield test_client
