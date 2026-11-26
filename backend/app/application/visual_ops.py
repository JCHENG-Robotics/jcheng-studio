"""G4 visual snapshot and camera-reference operations.

The methods are moved verbatim as a structural boundary; they still use the
service facade for state, adapter, settings, the visual cache and events.
"""

from __future__ import annotations

import uuid
from typing import Any

from ..domain.enums import ErrorCode
from ..domain.models import RobotState, jsonable, utc_now_iso
from ..domain.visual import (
    VisualModelError,
    compose_tf_frames,
    compute_frames,
    load_urdf,
    load_visual_config,
)
from ..errors import ServiceError, StorageError


_GATEWAY_NOTE_LABELS = {
    "TF is published by robot nodes. Dynamic transforms may be derived "
    "from commanded joint states, not encoder feedback.": (
        "TF 由机器人节点发布；动态变换可能由指令关节状态推导，不是编码器反馈。"
    ),
    "Only /tf_static has been received; dynamic links are not live.": (
        "只收到 /tf_static；动态连杆不是实时值。"
    ),
    "No /tf or /tf_static message has been received.": (
        "尚未收到 /tf 或 /tf_static 消息。"
    ),
}


def _localize_gateway_note(note: str) -> str:
    if note in _GATEWAY_NOTE_LABELS:
        return _GATEWAY_NOTE_LABELS[note]
    prefix = "Newest dynamic TF edge is "
    suffix = " ms old."
    if note.startswith(prefix) and note.endswith(suffix):
        age = note[len(prefix) : -len(suffix)]
        return f"最新动态 TF 边已过期 {age} ms。"
    return note

def _mimic_source_available(
    model: Any,
    name: str,
    positions: dict[str, float],
    seen: set[str] | None = None,
) -> bool:
    """True when a mimic joint's source chain ends in a real position."""
    seen = seen or set()
    if name in seen:
        return False
    seen.add(name)
    joint = model.joints.get(name)
    if joint is None:
        return False
    if not joint.mimic:
        return name in positions
    return _mimic_source_available(model, joint.mimic, positions, seen)


class VisualOperations:
    # ------------------------------------------------------------- visual (G4)
    def _load_visual_bundle(self) -> tuple[Any, dict[str, Any]]:
        if self._visual_bundle is not None:
            return self._visual_bundle
        if self._visual_error is not None:
            raise VisualModelError(self._visual_error)
        config = load_visual_config(self.settings.visual_model_path)
        try:
            model = load_urdf(
                self.settings.urdf_path,
                verified=config["urdf_verified"],
                source_note=config["urdf_note"],
            )
        except VisualModelError as exc:
            self._visual_error = exc.issues
            raise
        self._visual_bundle = (model, config)
        return self._visual_bundle

    @staticmethod
    def _joint_truth(state: RobotState) -> str:
        truths = {
            joint.truth.value
            for joint in state.joints
            if joint.position_rad is not None
        }
        if not truths:
            return "unknown"
        if len(truths) == 1:
            return next(iter(truths))
        return "mixed"

    @staticmethod
    def _max_sample_age(state: RobotState) -> int | None:
        ages = [
            joint.sample_age_ms
            for joint in state.joints
            if joint.sample_age_ms is not None
        ]
        return max(ages) if ages else None

    def _camera_block(
        self, camera: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, Any]:
        camera_config = config["camera"]
        warnings: list[str] = []
        if not camera.get("available"):
            warnings.append("相机不可用；无法显示画面。")
        if camera.get("truth") == "simulated":
            warnings.append("模拟：相机快照为合成画面。")
        if not camera_config.get("available"):
            warnings.append(
                "缺少实测光学外参；不显示光学框架。"
            )
        warnings.append(
            f"图像框架是 {camera_config.get('body_frame')}（机身）。"
            f"PnP 必须使用 {camera_config.get('frame')}，不能用 URDF 标称框架。"
        )
        return {
            **camera,
            "snapshot_url": "/api/peripherals/camera/snapshot",
            "frame": camera_config.get("body_frame"),
            "optical_frame": camera_config.get("frame"),
            "calibration": camera_config,
            "warnings": warnings,
        }

    def visual_snapshot(self) -> dict[str, Any]:
        """Read-only URDF/FK + camera view metadata; never queries live TF."""
        state = self._state()
        camera = jsonable(self.adapter.camera_status())
        config = load_visual_config(self.settings.visual_model_path)
        camera_block = self._camera_block(camera, config)
        base = {
            "schema_version": 1,
            "generated_at": utc_now_iso(),
            "mode": state.mode.value,
            "source_label": state.source_label.value,
            "robot_id": state.robot_id,
        }
        try:
            model, config = self._load_visual_bundle()
        except VisualModelError as exc:
            return {
                **base,
                "urdf": {
                    "available": False,
                    "path": str(self.settings.urdf_path),
                    "sha256": None,
                    "robot_name": None,
                    "verified": False,
                    "links": 0,
                    "joints": 0,
                    "source_note": config.get("urdf_note", ""),
                },
                "tf": {
                    "status": "invalid",
                    "source": "none",
                    "live": False,
                    "truth": "unavailable",
                    "joint_truth": self._joint_truth(state),
                    "max_sample_age_ms": self._max_sample_age(state),
                    "gateway_status": None,
                    "dynamic_edges": 0,
                    "static_edges": 0,
                    "max_tf_age_ms": None,
                    "frames": [],
                    "camera_frame": None,
                    "warnings": exc.issues,
                },
                "joints": [],
                "camera": camera_block,
                "warnings": exc.issues + config.get("warnings", []) + camera_block["warnings"],
            }

        positions = {
            joint.name: float(joint.position_rad)
            for joint in state.joints
            if joint.position_rad is not None
        }
        result = compute_frames(model, positions, config)
        joint_truth = self._joint_truth(state)
        max_age = self._max_sample_age(state)
        required_arm = set(self.mapping.joint_names())
        missing_arm = sorted(set(result["missing"]) & required_arm)
        warnings: list[str] = []
        warnings.extend(config.get("warnings", []))
        warnings.extend(model.warnings)
        warnings.extend(result["issues"])
        if not model.verified:
            warnings.append(
                "URDF 模型为暂定：关节限位与网格尚未经过硬件验证。"
            )
        if missing_arm:
            warnings.append(
                "缺少臂关节状态：" + ", ".join(missing_arm)
            )
        if max_age is not None and max_age > self.mapping.safety.feedback_max_age_ms:
            warnings.append(
                f"Joint state age {max_age} ms exceeds "
                f"{self.mapping.safety.feedback_max_age_ms} ms."
            )
        if not config["camera"].get("available"):
            warnings.append(
                "Measured camera extrinsic missing; optical frame unavailable."
            )

        live_tf = None
        live_result: dict[str, Any] | None = None
        try:
            live_tf = self.adapter.tf_snapshot()
        except Exception:  # noqa: BLE001 - a gateway hiccup must not break the page
            live_tf = None
        if (
            isinstance(live_tf, dict)
            and live_tf.get("available")
            and live_tf.get("transforms")
        ):
            live_result = compose_tf_frames(live_tf, model.root_link)
            warnings.extend(live_result["issues"])
            if live_result["unresolved"]:
                warnings.append(
                    f"有 {len(live_result['unresolved'])} 条 TF 边不在 "
                    f"{model.root_link} 根节点下，已忽略。"
                )
            gateway_notes = live_tf.get("notes")
            if isinstance(gateway_notes, list):
                warnings.extend(
                    _localize_gateway_note(str(note)) for note in gateway_notes
                )
            warnings.append(
                "来自网关的实时 TF 快照：动态变换可能由指令关节状态推导，"
                "不是编码器反馈。"
            )

        live_by_name: dict[str, dict[str, Any]] = {}
        if live_result is not None:
            live_by_name = {
                str(frame["name"]): frame for frame in live_result["frames"]
            }
        frames_out = []
        model_names = set()
        for frame in result["frames"]:
            model_names.add(frame["name"])
            live = live_by_name.get(frame["name"])
            if live is None:
                frames_out.append(frame)
                continue
            merged = {**frame, **live}
            merged["is_camera"] = bool(frame.get("is_camera") or live.get("is_camera"))
            merged["nominal"] = bool(frame.get("nominal"))
            frames_out.append(merged)
        if live_result is not None:
            for name, frame in live_by_name.items():
                if name in model_names:
                    continue
                added = dict(frame)
                added["is_camera"] = "camera" in name
                added["nominal"] = name in {
                    "camera_link",
                    "camera_optical_frame",
                    "camera_connet_link",
                }
                if name == config["camera"].get("frame"):
                    added["optical"] = True
                    added["truth"] = config["camera"].get("truth", "measured")
                    added["calibration_version"] = config["camera"].get(
                        "calibration_version", ""
                    )
                frames_out.append(added)

        camera_name = config["camera"].get("frame")
        camera_frame = next(
            (frame for frame in frames_out if frame.get("name") == camera_name),
            result["camera_frame"],
        )
        camera_block["tf_frames"] = [
            name
            for name in (
                config["camera"].get("body_frame"),
                config["camera"].get("frame"),
            )
            if name and any(frame.get("name") == name for frame in frames_out)
        ]
        if live_result is not None:
            missing_camera = [
                name
                for name in (
                    config["camera"].get("body_frame"),
                    config["camera"].get("frame"),
                )
                if name and not any(frame.get("name") == name for frame in frames_out)
            ]
            if missing_camera:
                camera_block["warnings"].append(
                    "Live TF does not include: " + ", ".join(missing_camera)
                )

        degraded = bool(missing_arm) or (
            max_age is not None
            and max_age > self.mapping.safety.feedback_max_age_ms
        )
        if live_result is None:
            tf_status = "degraded" if degraded else "model_only"
            tf_source = "urdf_fk"
            tf_truth = "model"
            warnings.append(
                "网关没有实时 tf2 订阅；框架来自 URDF 前向运动学"
                f"（关节真值={joint_truth} 位置）。"
            )
        else:
            gateway_status = str(live_tf.get("status", "unknown"))
            if gateway_status == "ok" and not live_result["issues"]:
                tf_status = "live"
            elif gateway_status == "static_only":
                tf_status = "live_static_only"
            else:
                tf_status = "degraded"
            tf_source = "tf2_topics"
            tf_truth = "measured_tf"
            if gateway_status == "static_only":
                warnings.append(
                    "只有 /tf_static 是实时的；静态图未覆盖的动态连杆框架"
                    "回退到 URDF。"
                )
            if gateway_status == "stale":
                warnings.append(
                    "实时 TF 已过期（最新动态边 "
                    f"{live_tf.get('freshest_age_ms')} ms）。"
                )
            missing_live = sorted(model_names - set(live_by_name))
            if missing_live:
                warnings.append(
                    f"有 {len(missing_live)} 个模型框架不在实时 TF 中，"
                    "使用 URDF FK：" + ", ".join(missing_live[:6])
                )
        camera_block["tf_status"] = tf_status

        joint_records = []
        for name in model.movable_order:
            joint = model.joints[name]
            mapping_joint = self.mapping.get_joint(name)
            position = positions.get(name)
            position_source = "state"
            if (
                position is None
                and name in result["derived_joints"]
                and _mimic_source_available(model, name, positions)
            ):
                position = result["derived_joints"][name]
                position_source = "mimic"
            elif position is None:
                position_source = "missing"
            if mapping_joint is not None:
                lower = mapping_joint.min_rad
                upper = mapping_joint.max_rad
                limit_source = mapping_joint.limit_source
                verified = mapping_joint.verified
            else:
                lower = joint.lower
                upper = joint.upper
                limit_source = "urdf"
                verified = False
            within_limits = None
            if position is not None and lower is not None and upper is not None:
                within_limits = lower <= position <= upper
            joint_records.append(
                {
                    "name": name,
                    "type": joint.joint_type,
                    "position_rad": position,
                    "position_source": position_source,
                    "truth": joint_truth,
                    "lower_rad": lower,
                    "upper_rad": upper,
                    "urdf_lower_rad": joint.lower,
                    "urdf_upper_rad": joint.upper,
                    "velocity_limit": joint.velocity,
                    "limit_source": limit_source,
                    "verified": verified,
                    "within_limits": within_limits,
                    "mimic_of": joint.mimic,
                    "mimic_multiplier": joint.mimic_multiplier if joint.mimic else None,
                }
            )

        return {
            **base,
            "urdf": {
                "available": True,
                "path": model.source_path,
                "sha256": model.sha256,
                "robot_name": model.robot_name,
                "verified": model.verified,
                "links": len(model.links),
                "joints": len(model.movable_order),
                "source_note": model.source_note,
                "warnings": model.warnings,
            },
            "tf": {
                "status": tf_status,
                "source": tf_source,
                "live": live_result is not None,
                "truth": tf_truth,
                "joint_truth": joint_truth,
                "max_sample_age_ms": max_age,
                "gateway_status": live_tf.get("status") if live_result is not None else None,
                "dynamic_edges": live_tf.get("dynamic_edges") if live_result is not None else 0,
                "static_edges": live_tf.get("static_edges") if live_result is not None else 0,
                # Static TF edges are latched and legitimately old; use the
                # dynamic maximum so the UI does not call a live graph stale.
                "max_tf_age_ms": (
                    live_tf.get("max_dynamic_age_ms", live_tf.get("max_age_ms"))
                    if live_result is not None
                    else None
                ),
                "frames": frames_out,
                "camera_frame": camera_frame,
                "warnings": warnings,
            },
            "joints": joint_records,
            "camera": camera_block,
            "warnings": warnings + camera_block["warnings"],
        }

    def list_camera_references(self, limit: int = 50) -> dict[str, Any]:
        try:
            items = self.store.list_camera_references(limit)
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "camera reference store read failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        return {"items": items, "schema_version": 1}

    def save_camera_reference(self, note: str = "") -> dict[str, Any]:
        if not isinstance(note, str) or len(note) > 500:
            raise ServiceError(
                ErrorCode.BAD_REQUEST.value,
                "note must be a string of at most 500 chars",
                status_code=400,
            )
        visual = self.visual_snapshot()
        camera = visual["camera"]
        if not camera.get("available"):
            raise ServiceError(
                ErrorCode.BAD_REQUEST.value,
                "camera is unavailable; no reference saved",
                status_code=400,
            )
        calibration = camera.get("calibration") or {}
        record = {
            "reference_id": f"ref_{uuid.uuid4().hex[:12]}",
            "created_at": utc_now_iso(),
            "mode": visual["mode"],
            "source_label": visual["source_label"],
            "truth": camera.get("truth", "unknown"),
            "camera_topic": camera.get("topic", ""),
            "width": camera.get("width"),
            "height": camera.get("height"),
            "encoding": camera.get("encoding"),
            "fps": camera.get("fps"),
            "frame_at": camera.get("last_frame_at"),
            "sample_age_ms": camera.get("sample_age_ms"),
            "calibration_version": calibration.get("calibration_version", ""),
            "calibration_source": calibration.get("calibration_source", ""),
            "tf_status": visual["tf"]["status"],
            "tf_source": visual["tf"]["source"],
            "note": note.strip(),
            "snapshot_url": camera.get("snapshot_url", ""),
            "schema_version": 1,
        }
        try:
            inserted = self.store.insert_camera_reference(
                reference_id=record["reference_id"],
                created_at=record["created_at"],
                record=record,
            )
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "camera reference store write failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        if not inserted:
            raise ServiceError(
                ErrorCode.INTERNAL_ERROR.value,
                "camera reference insert was ignored",
                status_code=500,
            )
        storage_error = None
        try:
            self._append(
                self._event(
                    "CameraReferenceSaved",
                    None,
                    "visual",
                    "camera metadata reference saved",
                    None,
                    {
                        "reference_id": record["reference_id"],
                        "truth": record["truth"],
                        "tf_status": record["tf_status"],
                        "calibration_version": record["calibration_version"],
                        "note": record["note"],
                    },
                )
            )
        except ServiceError as exc:
            storage_error = exc.details
        return {
            **record,
            "storage_error": storage_error,
        }
