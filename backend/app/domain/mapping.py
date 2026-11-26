"""Joint mapping and limit model.

The mapping is derived from the vendor `servo_controller.yaml`, but it is
explicitly provisional: the vendor's 0..1000 pulse range is a software range,
not a measured mechanical limit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_RADIANS_PER_TICK = 240.0 / 360.0 * (2.0 * 3.141592653589793) / 1000.0


class MappingError(ValueError):
    """Raised when a mapping file is missing, malformed, or inconsistent."""


@dataclass(frozen=True)
class JointMapping:
    name: str
    servo_id: int
    init_raw: int
    min_raw: int
    max_raw: int
    direction: int
    radians_per_tick: float
    limit_source: str = "software_config"
    verified: bool = False

    def raw_to_rad(self, raw: float) -> float:
        return self.direction * (raw - self.init_raw) * self.radians_per_tick

    def rad_to_raw(self, rad: float) -> float:
        return self.init_raw + self.direction * rad / self.radians_per_tick

    @property
    def min_rad(self) -> float:
        return min(self.raw_to_rad(self.min_raw), self.raw_to_rad(self.max_raw))

    @property
    def max_rad(self) -> float:
        return max(self.raw_to_rad(self.min_raw), self.raw_to_rad(self.max_raw))

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "servo_id": self.servo_id,
            "init_raw": self.init_raw,
            "min_raw": self.min_raw,
            "max_raw": self.max_raw,
            "direction": self.direction,
            "radians_per_tick": self.radians_per_tick,
            "min_rad": self.min_rad,
            "max_rad": self.max_rad,
            "limit_source": self.limit_source,
            "verified": self.verified,
        }


@dataclass(frozen=True)
class SafetyLimits:
    feedback_max_age_ms: int = 500
    max_step_rad: float = 0.35
    min_duration_s: float = 0.5
    max_duration_s: float = 10.0
    preview_ttl_s: int = 30
    lease_ttl_s: int = 60


@dataclass(frozen=True)
class MappingConfig:
    schema_version: int
    mapping_version: str
    source: str
    verified: bool
    verification_status: str
    radians_per_tick: float
    joints: dict[str, JointMapping]
    excluded: tuple[dict[str, Any], ...] = ()
    safety: SafetyLimits = field(default_factory=SafetyLimits)
    mapping_hash: str = ""

    def joint_names(self) -> list[str]:
        return list(self.joints)

    def get_joint(self, name: str) -> JointMapping | None:
        return self.joints.get(name)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mapping_version": self.mapping_version,
            "source": self.source,
            "verified": self.verified,
            "verification_status": self.verification_status,
            "radians_per_tick": self.radians_per_tick,
            "mapping_hash": self.mapping_hash,
            "joints": [j.to_public_dict() for j in self.joints.values()],
            "excluded": list(self.excluded),
            "safety": {
                "feedback_max_age_ms": self.safety.feedback_max_age_ms,
                "max_step_rad": self.safety.max_step_rad,
                "min_duration_s": self.safety.min_duration_s,
                "max_duration_s": self.safety.max_duration_s,
                "preview_ttl_s": self.safety.preview_ttl_s,
                "lease_ttl_s": self.safety.lease_ttl_s,
            },
        }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MappingError(message)


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise MappingError(f"joint mapping not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - malformed user file
        raise MappingError(f"invalid YAML in {path}: {exc}") from exc
    _require(isinstance(raw, dict), "mapping root must be a mapping")
    return raw


def load_mapping(path: str | Path) -> MappingConfig:
    path = Path(path)
    raw = _load_raw(path)

    schema_version = int(raw.get("schema_version", 1))
    mapping_version = str(raw.get("mapping_version", "")).strip()
    _require(bool(mapping_version), "mapping_version is required")

    raw_joints = raw.get("joints")
    _require(isinstance(raw_joints, list) and bool(raw_joints), "joints must be a non-empty list")

    radians_per_tick = float(raw.get("radians_per_tick", DEFAULT_RADIANS_PER_TICK))
    _require(radians_per_tick > 0, "radians_per_tick must be positive")

    joints: dict[str, JointMapping] = {}
    servo_ids: set[int] = set()
    for item in raw_joints:
        _require(isinstance(item, dict), "each joint must be a mapping")
        name = str(item.get("name", "")).strip()
        _require(bool(name), "joint name is required")
        _require(name not in joints, f"duplicate joint: {name}")

        servo_id = int(item["servo_id"])
        _require(servo_id not in servo_ids, f"duplicate servo_id: {servo_id}")
        servo_ids.add(servo_id)

        init_raw = int(item["init_raw"])
        min_raw = int(item["min_raw"])
        max_raw = int(item["max_raw"])
        direction = int(item.get("direction", 1))
        _require(direction in (-1, 1), f"{name}: direction must be 1 or -1")
        _require(init_raw == 500, f"{name}: unexpected init_raw {init_raw}; review vendor mapping")
        _require(min_raw != max_raw, f"{name}: min_raw and max_raw must differ")

        joints[name] = JointMapping(
            name=name,
            servo_id=servo_id,
            init_raw=init_raw,
            min_raw=min_raw,
            max_raw=max_raw,
            direction=direction,
            radians_per_tick=radians_per_tick,
            limit_source=str(item.get("limit_source", "software_config")),
            verified=bool(item.get("verified", False)),
        )

    safety_raw = raw.get("safety") or {}
    _require(isinstance(safety_raw, dict), "safety must be a mapping")
    safety = SafetyLimits(
        feedback_max_age_ms=int(safety_raw.get("feedback_max_age_ms", 500)),
        max_step_rad=float(safety_raw.get("max_step_rad", 0.35)),
        min_duration_s=float(safety_raw.get("min_duration_s", 0.5)),
        max_duration_s=float(safety_raw.get("max_duration_s", 10.0)),
        preview_ttl_s=int(safety_raw.get("preview_ttl_s", 30)),
        lease_ttl_s=int(safety_raw.get("lease_ttl_s", 60)),
    )

    excluded_raw = raw.get("excluded") or []
    _require(isinstance(excluded_raw, list), "excluded must be a list")
    excluded = tuple(dict(item) for item in excluded_raw if isinstance(item, dict))

    canonical = {
        "schema_version": schema_version,
        "mapping_version": mapping_version,
        "radians_per_tick": radians_per_tick,
        "joints": [joints[name].to_public_dict() for name in joints],
        "excluded": list(excluded),
        "safety": {
            "feedback_max_age_ms": safety.feedback_max_age_ms,
            "max_step_rad": safety.max_step_rad,
            "min_duration_s": safety.min_duration_s,
            "max_duration_s": safety.max_duration_s,
            "preview_ttl_s": safety.preview_ttl_s,
            "lease_ttl_s": safety.lease_ttl_s,
        },
    }
    mapping_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return MappingConfig(
        schema_version=schema_version,
        mapping_version=mapping_version,
        source=str(raw.get("source", "")),
        verified=bool(raw.get("verified", False)),
        verification_status=str(raw.get("verification_status", "provisional")),
        radians_per_tick=radians_per_tick,
        joints=joints,
        excluded=excluded,
        safety=safety,
        mapping_hash=mapping_hash,
    )
