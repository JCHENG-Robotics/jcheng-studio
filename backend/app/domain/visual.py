"""G4 visual model: URDF parsing and forward kinematics for the Workbench.

This module is read-only and dependency-light. It parses the project URDF
(including its xacro include) as XML, builds the joint tree, and computes link
frames from a joint-position map. The result is explicitly a *model*, not a
live tf2 snapshot: the caller must label the source and joint truth.

The camera extrinsic is loaded from ``configs/visual_model.yaml`` (the validated
TSAI hand-eye), so the Workbench can show the measured optical frame while
keeping the nominal URDF camera frame clearly marked as model-only.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import yaml

Matrix = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]

MOVABLE_TYPES = {"revolute", "continuous", "prismatic"}


class VisualModelError(ValueError):
    """The URDF or camera model is unusable."""

    def __init__(self, issues: list[str]) -> None:
        super().__init__("visual model is invalid")
        self.issues = issues


@dataclass
class UrdfJoint:
    name: str
    joint_type: str
    parent: str
    child: str
    origin_xyz: tuple[float, float, float]
    origin_rpy: tuple[float, float, float]
    axis: tuple[float, float, float]
    lower: float | None
    upper: float | None
    velocity: float | None
    mimic: str | None = None
    mimic_multiplier: float = 1.0
    mimic_offset: float = 0.0

    @property
    def movable(self) -> bool:
        return self.joint_type in MOVABLE_TYPES


@dataclass
class UrdfModel:
    source_path: str
    sha256: str
    robot_name: str
    joints: dict[str, UrdfJoint]
    links: list[str]
    root_link: str
    children: dict[str, list[UrdfJoint]]
    child_joint: dict[str, UrdfJoint]
    movable_order: list[str]
    verified: bool
    source_note: str
    warnings: list[str] = field(default_factory=list)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _floats(text: str | None, default: tuple[float, ...]) -> tuple[float, ...]:
    if text is None:
        return tuple(default)
    try:
        values = tuple(float(item) for item in text.split())
    except ValueError:
        return tuple(default)
    if len(values) != len(default):
        return tuple(default)
    return values


def _parse_joint(element: ElementTree.Element) -> UrdfJoint:
    name = element.get("name") or ""
    joint_type = element.get("type") or "fixed"
    parent = ""
    child = ""
    origin = element.find("origin")
    axis_element = element.find("axis")
    limit = element.find("limit")
    mimic = element.find("mimic")
    for item in element:
        tag = _local(item.tag)
        if tag == "parent":
            parent = item.get("link") or ""
        elif tag == "child":
            child = item.get("link") or ""
    xyz = _floats(origin.get("xyz") if origin is not None else None, (0.0, 0.0, 0.0))
    rpy = _floats(origin.get("rpy") if origin is not None else None, (0.0, 0.0, 0.0))
    axis = _floats(
        axis_element.get("xyz") if axis_element is not None else None,
        (1.0, 0.0, 0.0),
    )
    lower = upper = velocity = None
    if limit is not None:
        try:
            lower = float(limit.get("lower")) if limit.get("lower") is not None else None
            upper = float(limit.get("upper")) if limit.get("upper") is not None else None
            velocity = (
                float(limit.get("velocity")) if limit.get("velocity") is not None else None
            )
        except ValueError:
            lower = upper = velocity = None
    mimic_joint = mimic.get("joint") if mimic is not None else None
    mimic_multiplier = 1.0
    mimic_offset = 0.0
    if mimic is not None:
        try:
            mimic_multiplier = float(mimic.get("multiplier") or 1.0)
            mimic_offset = float(mimic.get("offset") or 0.0)
        except ValueError:
            mimic_multiplier = 1.0
            mimic_offset = 0.0
    return UrdfJoint(
        name=name,
        joint_type=joint_type,
        parent=parent,
        child=child,
        origin_xyz=(xyz[0], xyz[1], xyz[2]),
        origin_rpy=(rpy[0], rpy[1], rpy[2]),
        axis=(axis[0], axis[1], axis[2]),
        lower=lower,
        upper=upper,
        velocity=velocity,
        mimic=mimic_joint,
        mimic_multiplier=mimic_multiplier,
        mimic_offset=mimic_offset,
    )


def _resolve_include(source: Path, filename: str | None) -> Path | None:
    if not filename:
        return None
    match = re.match(r"^\$\(find [^)]+\)(.*)$", filename)
    relative = match.group(1) if match else filename
    candidate = source.parent / Path(relative).name
    return candidate if candidate.is_file() else None


def load_urdf(path: str | Path, *, verified: bool = False, source_note: str = "") -> UrdfModel:
    """Parse the URDF/xacro and build the kinematic tree.

    xacro properties/macros are not evaluated; the project URDF keeps joint
    origins, axes and limits as literal XML, so a plain XML parse is enough.
    Mesh ``$(find ...)`` paths are ignored.
    """
    root_path = Path(path)
    if not root_path.is_file():
        raise VisualModelError([f"URDF not found: {root_path}"])
    pending = [root_path]
    seen_files: set[Path] = set()
    joints: dict[str, UrdfJoint] = {}
    links: list[str] = []
    robot_name = ""
    digest = hashlib.sha256()
    issues: list[str] = []
    warnings: list[str] = []

    while pending:
        current = pending.pop()
        resolved = current.resolve()
        if resolved in seen_files:
            continue
        seen_files.add(resolved)
        try:
            digest.update(resolved.read_bytes())
            root = ElementTree.parse(resolved).getroot()
        except (OSError, ElementTree.ParseError) as exc:
            issues.append(f"cannot parse {resolved}: {exc}")
            continue
        if not robot_name:
            robot_name = root.get("name") or ""
        for element in root:
            tag = _local(element.tag)
            if tag == "include":
                included = _resolve_include(resolved, element.get("filename"))
                if included is None:
                    warnings.append(
                        f"unresolved xacro include: {element.get('filename')}"
                    )
                else:
                    pending.append(included)
            elif tag == "link":
                name = element.get("name") or ""
                if name and name not in links:
                    links.append(name)
            elif tag == "joint":
                joint = _parse_joint(element)
                if not joint.name:
                    issues.append("joint without a name")
                    continue
                if joint.name in joints:
                    issues.append(f"duplicate joint: {joint.name}")
                    continue
                if not joint.parent or not joint.child:
                    issues.append(f"joint {joint.name} is missing parent/child")
                    continue
                joints[joint.name] = joint
                for link in (joint.parent, joint.child):
                    if link not in links:
                        links.append(link)

    if issues:
        raise VisualModelError(issues)
    for joint in joints.values():
        if joint.parent not in links or joint.child not in links:
            issues.append(f"joint {joint.name} references unknown links")
    if not links:
        issues.append("URDF has no links")
    if issues:
        raise VisualModelError(issues)

    children: dict[str, list[UrdfJoint]] = {}
    child_joint: dict[str, UrdfJoint] = {}
    for joint in joints.values():
        children.setdefault(joint.parent, []).append(joint)
        if joint.child in child_joint:
            issues.append(f"link {joint.child} has more than one parent joint")
        child_joint[joint.child] = joint
    if issues:
        raise VisualModelError(issues)

    child_links = set(child_joint)
    roots = [link for link in links if link not in child_links]
    if not roots:
        raise VisualModelError(["URDF has no root link (cycle?)"])
    if len(roots) > 1:
        warnings.append("multiple root links: " + ", ".join(sorted(roots)))
    root_link = "base_link" if "base_link" in roots else sorted(roots)[0]

    for start in links:
        chain: set[str] = set()
        current = start
        while current in child_joint:
            if current in chain:
                raise VisualModelError([f"kinematic cycle at link {current}"])
            chain.add(current)
            current = child_joint[current].parent

    for joint in joints.values():
        if joint.joint_type not in MOVABLE_TYPES | {"fixed"}:
            warnings.append(
                f"unsupported joint type {joint.joint_type} on {joint.name}; "
                "treated as fixed in FK"
            )

    return UrdfModel(
        source_path=str(root_path),
        sha256=digest.hexdigest(),
        robot_name=robot_name,
        joints=joints,
        links=links,
        root_link=root_link,
        children=children,
        child_joint=child_joint,
        movable_order=[joint.name for joint in joints.values() if joint.movable],
        verified=verified,
        source_note=source_note,
        warnings=warnings,
    )


def load_visual_config(path: str | Path) -> dict[str, Any]:
    """Load ``configs/visual_model.yaml`` with safe defaults on failure."""
    config_path = Path(path)
    result: dict[str, Any] = {
        "urdf_verified": False,
        "urdf_note": "",
        "warnings": [],
        "camera": {
            "available": False,
            "parent_link": None,
            "frame": "camera_optical_calibrated",
            "body_frame": "rgb_camera_link",
            "calibration_version": "unavailable",
            "calibration_source": "",
            "truth": "unknown",
            "translation_m": [0.0, 0.0, 0.0],
            "rpy_deg": [0.0, 0.0, 0.0],
            "notes": [],
        },
    }
    if not config_path.is_file():
        result["warnings"].append(f"visual model config not found: {config_path}")
        return result
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        result["warnings"].append(f"cannot read visual model config: {exc}")
        return result
    if not isinstance(data, dict):
        result["warnings"].append("visual model config must be a mapping")
        return result
    result["urdf_verified"] = bool(data.get("urdf_verified", False))
    result["urdf_note"] = str(data.get("urdf_note", ""))
    camera = data.get("camera")
    if not isinstance(camera, dict):
        result["warnings"].append("visual model config has no camera block")
        return result
    translation = camera.get("translation_m")
    rpy = camera.get("rpy_deg")
    if (
        not isinstance(translation, list)
        or len(translation) != 3
        or not all(isinstance(value, (int, float)) for value in translation)
        or not isinstance(rpy, list)
        or len(rpy) != 3
        or not all(isinstance(value, (int, float)) for value in rpy)
    ):
        result["warnings"].append("camera translation_m/rpy_deg are invalid")
        return result
    result["camera"] = {
        "available": True,
        "parent_link": str(camera.get("parent_link", "")),
        "frame": str(camera.get("frame", "camera_optical_calibrated")),
        "body_frame": str(camera.get("body_frame", "rgb_camera_link")),
        "calibration_version": str(camera.get("calibration_version", "")),
        "calibration_source": str(camera.get("calibration_source", "")),
        "truth": str(camera.get("truth", "measured")),
        "translation_m": [float(value) for value in translation],
        "rpy_deg": [float(value) for value in rpy],
        "notes": [str(item) for item in camera.get("notes", [])],
    }
    return result


# ----------------------------------------------------------------- transforms
def _identity() -> Matrix:
    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def _matmul(left: Matrix, right: Matrix) -> Matrix:
    return tuple(
        tuple(
            sum(left[row][k] * right[k][column] for k in range(4))
            for column in range(4)
        )
        for row in range(4)
    )  # type: ignore[return-value]


def _transform(
    xyz: list[float] | tuple[float, ...],
    rpy: list[float] | tuple[float, ...],
) -> Matrix:
    roll, pitch, yaw = rpy
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return (
        (cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr, xyz[0]),
        (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr, xyz[1]),
        (-sp, cp * sr, cp * cr, xyz[2]),
        (0.0, 0.0, 0.0, 1.0),
    )


def _axis_rotation(axis: tuple[float, float, float], angle: float) -> Matrix:
    length = math.sqrt(sum(value * value for value in axis))
    if length == 0.0 or angle == 0.0:
        return _identity()
    x, y, z = (value / length for value in axis)
    c, s = math.cos(angle), math.sin(angle)
    one_c = 1.0 - c
    return (
        (c + x * x * one_c, x * y * one_c - z * s, x * z * one_c + y * s, 0.0),
        (y * x * one_c + z * s, c + y * y * one_c, y * z * one_c - x * s, 0.0),
        (z * x * one_c - y * s, z * y * one_c + x * s, c + z * z * one_c, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def _translation(axis: tuple[float, float, float], distance: float) -> Matrix:
    length = math.sqrt(sum(value * value for value in axis))
    if length == 0.0:
        return _identity()
    return (
        (1.0, 0.0, 0.0, axis[0] / length * distance),
        (0.0, 1.0, 0.0, axis[1] / length * distance),
        (0.0, 0.0, 1.0, axis[2] / length * distance),
        (0.0, 0.0, 0.0, 1.0),
    )


def _rotation_list(matrix: Matrix) -> list[float]:
    return [
        matrix[0][0],
        matrix[0][1],
        matrix[0][2],
        matrix[1][0],
        matrix[1][1],
        matrix[1][2],
        matrix[2][0],
        matrix[2][1],
        matrix[2][2],
    ]


def _quaternion_matrix(values: list[float]) -> Matrix:
    x, y, z, w = (float(value) for value in values)
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm == 0.0:
        return _identity()
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return (
        (1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy), 0.0),
        (2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx), 0.0),
        (2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy), 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def compose_tf_frames(
    payload: dict[str, Any],
    root_link: str = "base_link",
) -> dict[str, Any]:
    """Compose a gateway TF snapshot into root-relative frame records.

    The gateway returns edges; this function walks them from ``root_link``.
    Edges above the root (for example ``world -> base_link``) are ignored.
    Unresolved or cyclic edges are reported, never guessed.
    """
    transforms = payload.get("transforms")
    if not isinstance(transforms, list):
        return {
            "frames": [],
            "issues": ["TF payload has no transforms list"],
            "unresolved": [],
        }
    by_parent: dict[str, list[dict[str, Any]]] = {}
    issues: list[str] = []
    for index, item in enumerate(transforms):
        if not isinstance(item, dict):
            issues.append(f"transform[{index}] is not an object")
            continue
        parent = item.get("parent")
        child = item.get("child")
        translation = item.get("translation_m")
        rotation = item.get("rotation_xyzw")
        if (
            not isinstance(parent, str)
            or not isinstance(child, str)
            or not parent
            or not child
        ):
            issues.append(f"transform[{index}] has invalid parent/child")
            continue
        if (
            not isinstance(translation, list)
            or len(translation) != 3
            or not all(isinstance(value, (int, float)) for value in translation)
            or not isinstance(rotation, list)
            or len(rotation) != 4
            or not all(isinstance(value, (int, float)) for value in rotation)
        ):
            issues.append(f"transform[{index}] has invalid translation/rotation")
            continue
        by_parent.setdefault(parent, []).append(item)

    frames: dict[str, Matrix] = {root_link: _identity()}
    order: list[str] = [root_link]
    queue = [root_link]
    unresolved: list[dict[str, str]] = []
    while queue:
        parent = queue.pop(0)
        for edge in by_parent.get(parent, []):
            child = str(edge["child"])
            if child in frames:
                issues.append(f"duplicate live TF frame: {child}")
                continue
            matrix = _matmul(
                frames[parent],
                _matmul(
                    _transform(edge["translation_m"], (0.0, 0.0, 0.0)),
                    _quaternion_matrix(edge["rotation_xyzw"]),
                ),
            )
            frames[child] = matrix
            order.append(child)
            queue.append(child)

    for parent, edges in by_parent.items():
        if parent == root_link or parent in frames:
            continue
        for edge in edges:
            unresolved.append({"parent": parent, "child": str(edge["child"])})

    records = []
    for link in order:
        matrix = frames[link]
        parent_edge = None
        if link != root_link:
            parent_edge = next(
                (
                    edge
                    for edge in transforms
                    if isinstance(edge, dict) and edge.get("child") == link
                ),
                None,
            )
        record: dict[str, Any] = {
            "name": link,
            "parent": parent_edge.get("parent") if parent_edge else None,
            "parent_joint": "tf_edge",
            "position_m": [
                round(matrix[0][3], 9),
                round(matrix[1][3], 9),
                round(matrix[2][3], 9),
            ],
            "rotation": [round(value, 9) for value in _rotation_list(matrix)],
            "source": "live_tf",
            "live": True,
            "nominal": False,
            "is_camera": "camera" in link,
            "static": bool(parent_edge.get("static")) if parent_edge else False,
            "age_ms": parent_edge.get("age_ms") if parent_edge else None,
            "stamp_ns": parent_edge.get("stamp_ns") if parent_edge else None,
            "truth": "measured_tf",
        }
        records.append(record)
    return {"frames": records, "issues": issues, "unresolved": unresolved}


def compute_frames(
    model: UrdfModel,
    joint_positions: dict[str, float],
    camera_config: dict[str, Any],
) -> dict[str, Any]:
    """Compute link frames for a joint-position map.

    Missing movable joints are treated as 0.0 and reported; a mimic joint uses
    its source joint's value. No TF is queried and no robot command is sent.
    """
    missing: list[str] = []
    derived: dict[str, float] = {}
    cache: dict[str, float] = {}

    def resolve(name: str, stack: set[str]) -> float:
        if name in cache:
            return cache[name]
        if name in stack:
            missing.append(name)
            return 0.0
        joint = model.joints.get(name)
        if joint is None:
            missing.append(name)
            return 0.0
        if joint.mimic:
            stack.add(name)
            source_value = resolve(joint.mimic, stack)
            stack.discard(name)
            value = joint.mimic_multiplier * source_value + joint.mimic_offset
            derived[name] = value
        elif name in joint_positions:
            value = float(joint_positions[name])
        else:
            missing.append(name)
            value = 0.0
        cache[name] = value
        return value

    frames: dict[str, Matrix] = {model.root_link: _identity()}
    order: list[str] = [model.root_link]
    queue = [model.root_link]
    while queue:
        parent = queue.pop(0)
        for joint in model.children.get(parent, []):
            if joint.child in frames:
                missing.append(f"duplicate frame for link {joint.child}")
                continue
            value = 0.0
            if joint.movable or joint.mimic:
                value = resolve(joint.name, set())
            motion = _identity()
            if joint.joint_type in {"revolute", "continuous"}:
                motion = _axis_rotation(joint.axis, value)
            elif joint.joint_type == "prismatic":
                motion = _translation(joint.axis, value)
            frames[joint.child] = _matmul(
                _matmul(frames[parent], _transform(joint.origin_xyz, joint.origin_rpy)),
                motion,
            )
            order.append(joint.child)
            queue.append(joint.child)

    frame_records = []
    for link in order:
        matrix = frames[link]
        joint = model.child_joint.get(link)
        record: dict[str, Any] = {
            "name": link,
            "parent": joint.parent if joint else None,
            "parent_joint": joint.name if joint else None,
            "position_m": [round(matrix[0][3], 9), round(matrix[1][3], 9), round(matrix[2][3], 9)],
            "rotation": [round(value, 9) for value in _rotation_list(matrix)],
            "source": "urdf_fk",
            "nominal": link in {"camera_link", "camera_optical_frame", "camera_connet_link"},
            "is_camera": link in {"camera_connet_link", "camera_link", "camera_optical_frame"},
        }
        frame_records.append(record)

    for link in model.links:
        if link not in frames:
            missing.append(f"unreachable link from {model.root_link}: {link}")

    camera = camera_config.get("camera") or {}
    camera_frame = None
    if camera.get("available"):
        parent_link = camera.get("parent_link")
        if parent_link not in frames:
            missing.append(
                f"camera parent link {parent_link!r} is not in the URDF frames"
            )
        else:
            extrinsic = _matmul(
                frames[parent_link],
                _transform(
                    camera["translation_m"],
                    [math.radians(value) for value in camera["rpy_deg"]],
                ),
            )
            camera_frame = {
                "name": camera.get("frame", "camera_optical_calibrated"),
                "parent": parent_link,
                "parent_joint": "hand_eye_extrinsic",
                "position_m": [
                    round(extrinsic[0][3], 9),
                    round(extrinsic[1][3], 9),
                    round(extrinsic[2][3], 9),
                ],
                "rotation": [round(value, 9) for value in _rotation_list(extrinsic)],
                "source": "measured_hand_eye",
                "nominal": False,
                "is_camera": True,
                "optical": True,
                "calibration_version": camera.get("calibration_version", ""),
                "truth": camera.get("truth", "measured"),
            }
            frame_records.append(camera_frame)

    issues = [f"缺少关节状态或 FK 输入：{name}" for name in sorted(set(missing))]
    return {
        "frames": frame_records,
        "camera_frame": camera_frame,
        "missing": sorted(set(missing)),
        "derived_joints": derived,
        "issues": issues,
    }
