"""Runtime configuration for the G1 backend."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .domain.enums import Mode
from .domain.mapping import MappingConfig, load_mapping
from .domain.motion import SUPERVISED_MOTION_ACK
from .domain.takeover import TAKEOVER_ACK, load_takeover_config

LOGGER = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_MAPPING = PROJECT_ROOT / "configs" / "joint_mapping.yaml"
DEFAULT_TAKEOVER = PROJECT_ROOT / "configs" / "takeover.yaml"
# The production xacro is a vendor-derived asset and is not shipped here.
# The visual workbench reads it only when ARMPI_URDF_PATH points at the robot
# description in the frozen jcheng-manipulation baseline (or another copy).
DEFAULT_URDF = PROJECT_ROOT / "configs" / "urdf" / "armpi_ultra.xacro"
DEFAULT_VISUAL_MODEL = PROJECT_ROOT / "configs" / "visual_model.yaml"
# Runtime event data belongs outside the repository (docs/SESSIONS.md).
DEFAULT_DB = Path.home() / ".local" / "state" / "armpi-studio" / "studio.db"
DEFAULT_SESSION_DB = Path.home() / ".local" / "state" / "armpi-studio" / "sessions.db"
DEFAULT_SESSION_EXPORTS = Path.home() / ".local" / "state" / "armpi-studio" / "exports"


def _studio_version() -> str:
    try:
        return (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


STUDIO_VERSION = _studio_version()

MOTION_SCOPES = {"step", "pose"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    mode: Mode
    robot_id: str
    bind_host: str
    port: int
    db_path: Path
    mapping_path: Path
    frontend_dist: Path
    log_level: str
    hardware_motion_enabled: bool
    supervised_motion: bool = False
    motion_scope: str = "step"
    takeover_acknowledged: bool = False
    takeover_config_path: Path = DEFAULT_TAKEOVER
    gateway_url: str | None = None
    gateway_token: str | None = None
    camera_gateway_url: str | None = None
    camera_gateway_token: str | None = None
    # Ecosystem gateway (jcheng-system /v1): read-only HTTP, Bearer auth.
    gateway_v1_url: str | None = None
    gateway_v1_token: str | None = None
    gateway_v1_contract_hash: str | None = None
    gateway_v1_timeout_s: float = 2.0
    # M5 teleop panel: read-only teleop status URL and the one system write
    # (operator stop). Both are loopback-only and disabled by default.
    teleop_status_url: str | None = None
    teleop_status_timeout_s: float = 1.0
    system_stop_enabled: bool = False
    # Deprecated legacy motion (frozen pre-ecosystem gateway). The double
    # switch ARMPI_MODE=legacy + ARMPI_ENABLE_LEGACY_MOTION=1 is the only way
    # to set this; the default runtime never dispatches or stops motion
    # locally. See app/legacy_motion.py.
    legacy_motion_enabled: bool = False
    # System intent (M2b) is opt-in and hardware-only: the Studio forwards the
    # operator's System-issued lease to /v1 and never acquires one itself.
    system_intent_enabled: bool = False
    # Non-fatal startup findings from Settings.from_env (e.g. a mock process
    # that was configured with a real /v1 gateway and had it cleared).
    startup_warnings: tuple[str, ...] = ()
    # Offline demo: mock data with an explicit DEMO label. Requires mock mode.
    demo: bool = False
    api_token: str | None = None
    urdf_path: Path = DEFAULT_URDF
    visual_model_path: Path = DEFAULT_VISUAL_MODEL
    # M2 operator-session store (outside the repository).
    session_db_path: Path = DEFAULT_SESSION_DB
    session_export_dir: Path = DEFAULT_SESSION_EXPORTS
    studio_version: str = STUDIO_VERSION
    studio_commit: str | None = None
    api_version: str = "1.0.0"
    schema_version: int = 1

    @classmethod
    def from_env(cls, *, mode: str | None = None, db_path: str | None = None) -> "Settings":
        raw_mode = (mode or os.getenv("ARMPI_MODE", "mock")).strip().lower()
        if raw_mode not in {
            Mode.MOCK.value,
            Mode.HARDWARE.value,
            Mode.LEGACY.value,
        }:
            raise ValueError(
                "ARMPI_MODE must be 'mock', 'hardware' or 'legacy', "
                f"got {raw_mode!r}"
            )
        resolved_mode = Mode(raw_mode)
        warnings: list[str] = []

        bind_host = (os.getenv("BIND_HOST", "127.0.0.1") or "127.0.0.1").strip()
        api_token = (os.getenv("ARMPI_BACKEND_TOKEN") or "").strip() or None
        if bind_host not in LOOPBACK_HOSTS and not api_token:
            raise ValueError(
                f"BIND_HOST={bind_host!r} is not loopback; set "
                "ARMPI_BACKEND_TOKEN or keep BIND_HOST=127.0.0.1 and use an "
                "SSH tunnel"
            )

        # Hardware motion is impossible in mock mode. In hardware mode it still
        # requires an explicit opt-in; G1 defaults to false. The deprecated
        # legacy mode replaces that flag with its own double switch:
        # ARMPI_MODE=legacy AND ARMPI_ENABLE_LEGACY_MOTION=1.
        motion_flag = _env_bool("ARMPI_HARDWARE_MOTION", False)
        legacy_flag = _env_bool("ARMPI_ENABLE_LEGACY_MOTION", False)
        legacy_motion_enabled = legacy_flag and resolved_mode is Mode.LEGACY
        if resolved_mode is Mode.LEGACY and not legacy_flag:
            raise ValueError(
                "ARMPI_MODE=legacy requires ARMPI_ENABLE_LEGACY_MOTION=1; "
                "the legacy gateway is deprecated and never starts implicitly"
            )
        if legacy_flag and resolved_mode is not Mode.LEGACY:
            message = (
                "ARMPI_ENABLE_LEGACY_MOTION=1 is ignored unless "
                "ARMPI_MODE=legacy; the legacy motion endpoints stay disabled"
            )
            warnings.append(message)
            LOGGER.warning(message)
        hardware_motion_enabled = (
            resolved_mode is Mode.HARDWARE and motion_flag
        ) or (resolved_mode is Mode.LEGACY and legacy_motion_enabled)
        mapping_path = Path(os.getenv("ARMPI_JOINT_MAPPING", str(DEFAULT_MAPPING)))
        gateway_url = os.getenv("ARMPI_GATEWAY_URL") or None
        gateway_token = os.getenv("ARMPI_GATEWAY_TOKEN") or None
        # The pose/motion gateway deliberately keeps its camera off, while the
        # persistent read-only gateway owns the cameras (the UVC aux device is
        # exclusive). Allow a separate camera gateway; both gateways share the
        # same token file unless an explicit camera token is supplied.
        camera_gateway_url = os.getenv("ARMPI_CAMERA_GATEWAY_URL") or None
        camera_gateway_token = (
            os.getenv("ARMPI_CAMERA_GATEWAY_TOKEN") or gateway_token
        ) if camera_gateway_url else None
        # Ecosystem gateway (/v1). Separate from the legacy gateway above:
        # the contract, auth header and paths are different.
        gateway_v1_url = os.getenv("ARMPI_GATEWAY_V1_URL") or None
        gateway_v1_token = os.getenv("ARMPI_GATEWAY_V1_TOKEN") or None
        gateway_v1_contract_hash = (
            os.getenv("ARMPI_GATEWAY_V1_CONTRACT_HASH") or ""
        ).strip() or None
        try:
            gateway_v1_timeout_s = float(
                os.getenv("ARMPI_GATEWAY_V1_TIMEOUT_S", "2.0")
            )
        except ValueError as exc:
            raise ValueError(
                "ARMPI_GATEWAY_V1_TIMEOUT_S must be a number"
            ) from exc
        if gateway_v1_timeout_s <= 0:
            raise ValueError("ARMPI_GATEWAY_V1_TIMEOUT_S must be positive")
        teleop_status_url = (os.getenv("JCHENG_TELEOP_STATUS_URL") or "").strip() or None
        try:
            teleop_status_timeout_s = float(
                os.getenv("JCHENG_TELEOP_STATUS_TIMEOUT_S", "1.0")
            )
        except ValueError as exc:
            raise ValueError("JCHENG_TELEOP_STATUS_TIMEOUT_S must be a number") from exc
        if teleop_status_timeout_s <= 0:
            raise ValueError("JCHENG_TELEOP_STATUS_TIMEOUT_S must be positive")

        # A mock process must never reach a real system gateway. Clear the /v1
        # coordinates (and therefore the system stop and intent transport) and
        # leave a loud warning instead of silently talking to the robot.
        if resolved_mode is Mode.MOCK and (gateway_v1_url or gateway_v1_token):
            message = (
                "ARMPI_MODE=mock was configured with ARMPI_GATEWAY_V1_URL/"
                "ARMPI_GATEWAY_V1_TOKEN; both were cleared so the mock process "
                "cannot call a real system gateway"
            )
            warnings.append(message)
            LOGGER.warning(message)
            gateway_v1_url = None
            gateway_v1_token = None
            gateway_v1_contract_hash = None

        system_intent_flag = _env_bool("ARMPI_SYSTEM_INTENT", False)
        system_intent_enabled = (
            system_intent_flag and resolved_mode is Mode.HARDWARE
        )
        if system_intent_flag and resolved_mode is not Mode.HARDWARE:
            message = (
                "ARMPI_SYSTEM_INTENT=1 requires ARMPI_MODE=hardware; the "
                "intent endpoints stay disabled"
            )
            warnings.append(message)
            LOGGER.warning(message)
        elif system_intent_flag and not (gateway_v1_url and gateway_v1_token):
            message = (
                "ARMPI_SYSTEM_INTENT=1 requires ARMPI_GATEWAY_V1_URL and "
                "ARMPI_GATEWAY_V1_TOKEN; intent requests fail closed"
            )
            warnings.append(message)
            LOGGER.warning(message)

        system_stop_enabled = _env_bool("ARMPI_SYSTEM_STOP", False)
        if system_stop_enabled and resolved_mode is Mode.MOCK:
            message = (
                "ARMPI_SYSTEM_STOP=1 is not available in mock mode; the "
                "system stop proxy is disabled so mock cannot reach a real "
                "system"
            )
            warnings.append(message)
            LOGGER.warning(message)
            system_stop_enabled = False
        if system_stop_enabled and not (gateway_v1_url and gateway_v1_token):
            raise ValueError(
                "ARMPI_SYSTEM_STOP=1 requires ARMPI_GATEWAY_V1_URL and "
                "ARMPI_GATEWAY_V1_TOKEN"
            )
        demo = _env_bool("ARMPI_DEMO", False)
        if demo and resolved_mode is not Mode.MOCK:
            raise ValueError(
                "ARMPI_DEMO=1 requires ARMPI_MODE=mock; hardware mode never "
                "falls back to simulated data"
            )
        session_db_path = Path(
            os.getenv("ARMPI_SESSION_DB", str(DEFAULT_SESSION_DB))
        )
        session_export_dir = Path(
            os.getenv("ARMPI_SESSION_EXPORTS", str(DEFAULT_SESSION_EXPORTS))
        )
        studio_commit = (
            os.getenv("ARMPI_STUDIO_COMMIT") or os.getenv("STUDIO_COMMIT") or ""
        ).strip() or None
        ack = (os.getenv("ARMPI_SUPERVISED_MOTION_ACK") or "").strip()

        raw_scope = (os.getenv("ARMPI_MOTION_SCOPE", "step") or "step").strip().lower()
        if raw_scope not in MOTION_SCOPES:
            raise ValueError(
                f"ARMPI_MOTION_SCOPE must be one of {sorted(MOTION_SCOPES)}, "
                f"got {raw_scope!r}"
            )
        takeover_ack = (os.getenv("ARMPI_TAKEOVER_ACK") or "").strip()

        supervised_motion = hardware_motion_enabled and ack == SUPERVISED_MOTION_ACK
        if hardware_motion_enabled:
            if gateway_url is None:
                raise ValueError(
                    "ARMPI_HARDWARE_MOTION=1 requires ARMPI_GATEWAY_URL; "
                    "the backend has no direct serial path"
                )
            if not gateway_token:
                raise ValueError(
                    "ARMPI_HARDWARE_MOTION=1 requires ARMPI_GATEWAY_TOKEN; "
                    "do not enable motion on an unauthenticated gateway"
                )
            if not supervised_motion and not load_mapping(mapping_path).verified:
                raise ValueError(
                    "ARMPI_HARDWARE_MOTION=1 with unverified joint limits requires "
                    f"ARMPI_SUPERVISED_MOTION_ACK={SUPERVISED_MOTION_ACK}"
                )

        # G7 pose takeover is a wider write surface than G6, so it has its own
        # acknowledgement and refuses unverified limits even in mock mode.
        takeover_config_path = Path(
            os.getenv("ARMPI_TAKEOVER_CONFIG", str(DEFAULT_TAKEOVER))
        )
        if raw_scope == "pose":
            mapping = load_mapping(mapping_path)
            if not mapping.verified:
                raise ValueError(
                    "ARMPI_MOTION_SCOPE=pose requires a verified joint mapping"
                )
            if takeover_ack != TAKEOVER_ACK:
                raise ValueError(
                    "ARMPI_MOTION_SCOPE=pose requires "
                    f"ARMPI_TAKEOVER_ACK={TAKEOVER_ACK}"
                )
            if resolved_mode is Mode.HARDWARE and not hardware_motion_enabled:
                raise ValueError(
                    "ARMPI_MOTION_SCOPE=pose requires ARMPI_HARDWARE_MOTION=1 "
                    "in hardware mode"
                )
            load_takeover_config(takeover_config_path)  # fail at startup, not later

        return cls(
            mode=resolved_mode,
            robot_id=os.getenv("ARMPI_ROBOT_ID", "armpi-ultra-01"),
            bind_host=bind_host,
            port=int(os.getenv("PORT", "8000")),
            db_path=Path(db_path or os.getenv("DB_PATH", str(DEFAULT_DB))),
            mapping_path=mapping_path,
            frontend_dist=Path(os.getenv("FRONTEND_DIST", str(PROJECT_ROOT / "frontend" / "dist"))),
            log_level=os.getenv("LOG_LEVEL", "info"),
            hardware_motion_enabled=hardware_motion_enabled,
            supervised_motion=supervised_motion,
            motion_scope=raw_scope if (resolved_mode is Mode.MOCK or hardware_motion_enabled) else "step",
            takeover_acknowledged=takeover_ack == TAKEOVER_ACK,
            takeover_config_path=takeover_config_path,
            gateway_url=gateway_url,
            gateway_token=gateway_token,
            camera_gateway_url=camera_gateway_url,
            camera_gateway_token=camera_gateway_token,
            gateway_v1_url=gateway_v1_url,
            gateway_v1_token=gateway_v1_token,
            gateway_v1_contract_hash=gateway_v1_contract_hash,
            gateway_v1_timeout_s=gateway_v1_timeout_s,
            teleop_status_url=teleop_status_url,
            teleop_status_timeout_s=teleop_status_timeout_s,
            system_stop_enabled=system_stop_enabled,
            legacy_motion_enabled=legacy_motion_enabled,
            system_intent_enabled=system_intent_enabled,
            startup_warnings=tuple(warnings),
            demo=demo,
            api_token=api_token,
            urdf_path=Path(os.getenv("ARMPI_URDF_PATH", str(DEFAULT_URDF))),
            visual_model_path=Path(
                os.getenv("ARMPI_VISUAL_MODEL", str(DEFAULT_VISUAL_MODEL))
            ),
            session_db_path=session_db_path,
            session_export_dir=session_export_dir,
            studio_commit=studio_commit,
        )

    def load_mapping(self) -> MappingConfig:
        return load_mapping(self.mapping_path)

    def load_takeover(self):
        if self.motion_scope != "pose":
            return None
        return load_takeover_config(self.takeover_config_path)
