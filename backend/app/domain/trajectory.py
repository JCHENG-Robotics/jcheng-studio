"""Offline trajectory datasets for the G2 Trajectory Inspector.

The contract keeps three streams separately named and labelled:

* ``planned``   - MoveIt/planner output
* ``commanded`` - the target sequence actually sent to the controller
* ``feedback``  - samples received back, with an explicit truth label

Everything in this module is dependency-free and offline. It validates a JSON
dataset, computes metrics that can be recomputed from the CSV export, and has
no robot adapter, serial port, or motion write path. Replay is a client-side
playback of recorded data.
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import io
import json
import math
import random
import statistics
from typing import Any

SCHEMA_VERSION = 1
ANALYSIS_VERSION = "g2-1"
TIMEBASE = "shared_trajectory_start"
STREAMS = ("planned", "commanded", "feedback")
MAX_JOINTS = 8
MAX_POINTS = 20000
MIN_POINTS = 2
FEEDBACK_TRUTHS = {
    "measured",
    "measured_on_demand",
    "simulated",
    "commanded",
    "unknown",
}


class TrajectoryDataError(ValueError):
    """A dataset failed contract validation."""

    def __init__(self, issues: list[dict[str, str]]) -> None:
        super().__init__("trajectory dataset is invalid")
        self.issues = issues


def _issue(path: str, message: str) -> dict[str, str]:
    return {"path": path, "message": message}


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_name(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= 64


def _finite_list(value: Any, length: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == length
        and all(_is_number(item) for item in value)
    )


def _validate_stream(
    raw: Any,
    key: str,
    joint_count: int,
    issues: list[dict[str, str]],
) -> dict[str, Any] | None:
    path = key
    if not isinstance(raw, dict):
        issues.append(_issue(path, "must be an object"))
        return None

    expected_truth = {"planned": "planned", "commanded": "commanded"}.get(key)
    truth = raw.get("truth")
    if expected_truth is not None:
        if truth != expected_truth:
            issues.append(_issue(f"{path}.truth", f"must be '{expected_truth}'"))
    elif truth not in FEEDBACK_TRUTHS:
        issues.append(
            _issue(
                f"{path}.truth",
                "must be one of " + ", ".join(sorted(FEEDBACK_TRUTHS)),
            )
        )

    offset = raw.get("time_offset_s", 0.0)
    if not _is_number(offset):
        issues.append(_issue(f"{path}.time_offset_s", "must be a finite number"))
        offset = 0.0

    source = raw.get("source", "")
    if not isinstance(source, str) or len(source) > 200:
        issues.append(_issue(f"{path}.source", "must be a string of at most 200 chars"))
        source = ""

    points = raw.get("points")
    if not isinstance(points, list):
        issues.append(_issue(f"{path}.points", "must be a list"))
        return None
    if not MIN_POINTS <= len(points) <= MAX_POINTS:
        issues.append(
            _issue(
                f"{path}.points",
                f"must contain {MIN_POINTS}..{MAX_POINTS} samples",
            )
        )
        return None

    normalized_points: list[dict[str, Any]] = []
    previous_t: float | None = None
    for index, point in enumerate(points):
        point_path = f"{path}.points[{index}]"
        if not isinstance(point, dict):
            issues.append(_issue(point_path, "must be an object"))
            continue
        t_s = point.get("t_s")
        if not _is_number(t_s) or float(t_s) < 0:
            issues.append(_issue(f"{point_path}.t_s", "must be a finite number >= 0"))
            continue
        t_value = float(t_s)
        if previous_t is not None and t_value <= previous_t:
            issues.append(_issue(f"{point_path}.t_s", "must be strictly increasing"))
        previous_t = t_value
        positions = point.get("positions")
        if not _finite_list(positions, joint_count):
            issues.append(
                _issue(
                    f"{point_path}.positions",
                    f"must be {joint_count} finite numbers",
                )
            )
            continue
        item: dict[str, Any] = {
            "t_s": t_value,
            "positions": [float(value) for value in positions],
        }
        velocities = point.get("velocities")
        if velocities is not None:
            if not _finite_list(velocities, joint_count):
                issues.append(
                    _issue(
                        f"{point_path}.velocities",
                        f"must be {joint_count} finite numbers when present",
                    )
                )
            else:
                item["velocities"] = [float(value) for value in velocities]
        normalized_points.append(item)

    return {
        "truth": truth,
        "time_offset_s": float(offset),
        "source": source,
        "points": normalized_points,
    }


def normalize_dataset(raw: Any) -> dict[str, Any]:
    """Validate a dataset and return a canonical copy.

    Raises:
        TrajectoryDataError: with a list of ``{path, message}`` issues.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(raw, dict):
        raise TrajectoryDataError([_issue("$", "dataset must be a JSON object")])

    version = raw.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        issues.append(_issue("schema_version", f"must be {SCHEMA_VERSION}"))

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        issues.append(_issue("name", "must be a non-empty string of at most 200 chars"))

    description = raw.get("description", "")
    if not isinstance(description, str) or len(description) > 2000:
        issues.append(_issue("description", "must be a string of at most 2000 chars"))

    timebase = raw.get("timebase", TIMEBASE)
    if timebase != TIMEBASE:
        issues.append(
            _issue(
                "timebase",
                f"must be '{TIMEBASE}' (no cross-device sync is inferred)",
            )
        )

    joint_names = raw.get("joint_names")
    if (
        not isinstance(joint_names, list)
        or not 1 <= len(joint_names) <= MAX_JOINTS
        or not all(_is_name(item) for item in joint_names)
        or len(set(joint_names)) != len(joint_names)
    ):
        issues.append(
            _issue(
                "joint_names",
                f"must be 1..{MAX_JOINTS} unique non-empty names",
            )
        )
        joint_names = []

    executor = raw.get("executor")
    normalized_executor = None
    if executor is not None:
        if not isinstance(executor, dict):
            issues.append(_issue("executor", "must be an object when present"))
        else:
            status = executor.get("status", "")
            detail = executor.get("detail", "")
            truth = executor.get("truth", "unknown")
            if not isinstance(status, str) or len(status) > 100:
                issues.append(_issue("executor.status", "must be at most 100 chars"))
            if not isinstance(detail, str) or len(detail) > 500:
                issues.append(_issue("executor.detail", "must be at most 500 chars"))
            if not isinstance(truth, str) or len(truth) > 40:
                issues.append(_issue("executor.truth", "must be at most 40 chars"))
            normalized_executor = {
                "status": status,
                "detail": detail,
                "truth": truth,
            }

    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict):
        issues.append(_issue("metadata", "must be an object when present"))
        metadata = {}

    streams: dict[str, Any] = {}
    joint_count = len(joint_names)
    for key in STREAMS:
        stream = _validate_stream(raw.get(key), key, joint_count, issues)
        if stream is not None:
            streams[key] = stream

    if issues:
        raise TrajectoryDataError(issues)

    return {
        "schema_version": SCHEMA_VERSION,
        "name": name.strip(),
        "description": description,
        "timebase": TIMEBASE,
        "joint_names": list(joint_names),
        "planned": streams["planned"],
        "commanded": streams["commanded"],
        "feedback": streams["feedback"],
        "executor": normalized_executor,
        "metadata": metadata,
    }


def dataset_sha256(dataset: dict[str, Any]) -> str:
    canonical = json.dumps(
        dataset,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _times(stream: dict[str, Any]) -> list[float]:
    offset = float(stream.get("time_offset_s", 0.0))
    return [float(point["t_s"]) + offset for point in stream["points"]]


def _positions(stream: dict[str, Any]) -> list[list[float]]:
    return [point["positions"] for point in stream["points"]]


def _stream_quality(stream: dict[str, Any]) -> dict[str, Any]:
    times = _times(stream)
    count = len(times)
    start = times[0]
    end = times[-1]
    duration = end - start
    intervals = [b - a for a, b in zip(times, times[1:])]
    median_dt = statistics.median(intervals) if intervals else None
    mean_dt = statistics.fmean(intervals) if intervals else None
    max_gap = max(intervals) if intervals else None
    gap_count = 0
    dropout_ratio = None
    jitter_ms = None
    if median_dt and median_dt > 0:
        gap_count = sum(1 for gap in intervals if gap > 1.5 * median_dt)
        expected = round(duration / median_dt) + 1 if duration > 0 else count
        missing = max(0, expected - count)
        dropout_ratio = missing / expected if expected > 0 else 0.0
    if len(intervals) > 2:
        jitter_ms = statistics.pstdev(intervals) * 1000.0
    effective_rate = (count - 1) / duration if duration > 0 else None
    return {
        "truth": stream["truth"],
        "time_offset_s": float(stream.get("time_offset_s", 0.0)),
        "samples": count,
        "start_s": round(start, 9),
        "end_s": round(end, 9),
        "duration_s": round(duration, 9),
        "median_interval_s": round(median_dt, 9) if median_dt is not None else None,
        "mean_interval_s": round(mean_dt, 9) if mean_dt is not None else None,
        "effective_rate_hz": round(effective_rate, 6) if effective_rate else None,
        "max_gap_s": round(max_gap, 9) if max_gap is not None else None,
        "gaps_over_1_5x_median": gap_count,
        "dropout_ratio": round(dropout_ratio, 6) if dropout_ratio is not None else None,
        "interval_jitter_ms": round(jitter_ms, 6) if jitter_ms is not None else None,
    }


def _interpolate(t_s: float, times: list[float], values: list[float]) -> float:
    if t_s <= times[0]:
        return values[0]
    if t_s >= times[-1]:
        return values[-1]
    index = bisect.bisect_left(times, t_s)
    t0, t1 = times[index - 1], times[index]
    v0, v1 = values[index - 1], values[index]
    if t1 == t0:
        return v1
    ratio = (t_s - t0) / (t1 - t0)
    return v0 + ratio * (v1 - v0)


def _align(
    reference: dict[str, Any],
    sample: dict[str, Any],
    joint_count: int,
) -> tuple[list[float], list[list[float]], list[list[float]]]:
    ref_times = _times(reference)
    ref_positions = _positions(reference)
    ref_columns = [
        [positions[joint] for positions in ref_positions] for joint in range(joint_count)
    ]
    sam_times = _times(sample)
    sam_positions = _positions(sample)
    start = max(ref_times[0], sam_times[0])
    end = min(ref_times[-1], sam_times[-1])
    aligned_times: list[float] = []
    aligned_ref: list[list[float]] = []
    aligned_sample: list[list[float]] = []
    for t_s, positions in zip(sam_times, sam_positions):
        if t_s < start or t_s > end:
            continue
        aligned_times.append(t_s)
        aligned_ref.append(
            [_interpolate(t_s, ref_times, ref_columns[joint]) for joint in range(joint_count)]
        )
        aligned_sample.append(positions)
    return aligned_times, aligned_ref, aligned_sample


def _metrics(
    reference: dict[str, Any],
    sample: dict[str, Any],
    joint_names: list[str],
) -> dict[str, Any]:
    aligned_times, reference_rows, sample_rows = _align(
        reference, sample, len(joint_names)
    )
    if not aligned_times:
        return {
            "available": False,
            "reason": "no overlapping samples between planned and sample streams",
            "per_joint": {},
            "aggregate": None,
        }

    per_joint: dict[str, Any] = {}
    all_squared: list[float] = []
    all_abs: list[float] = []
    max_abs_final = 0.0
    for index, joint in enumerate(joint_names):
        errors = [row[index] - ref[index] for row, ref in zip(sample_rows, reference_rows)]
        squared = [error * error for error in errors]
        max_abs = max(abs(error) for error in errors)
        rmse = math.sqrt(statistics.fmean(squared))
        mean_abs = statistics.fmean(abs(error) for error in errors)
        per_joint[joint] = {
            "max_abs_error_rad": round(max_abs, 9),
            "rmse_rad": round(rmse, 9),
            "mean_abs_error_rad": round(mean_abs, 9),
            "final_error_rad": round(errors[-1], 9),
        }
        all_squared.extend(squared)
        all_abs.extend(abs(error) for error in errors)
        max_abs_final = max(max_abs_final, abs(errors[-1]))

    return {
        "available": True,
        "reason": None,
        "samples_compared": len(aligned_times),
        "overlap_start_s": round(aligned_times[0], 9),
        "overlap_end_s": round(aligned_times[-1], 9),
        "per_joint": per_joint,
        "aggregate": {
            "max_abs_error_rad": round(max(all_abs), 9),
            "rmse_rad": round(math.sqrt(statistics.fmean(all_squared)), 9),
            "mean_abs_error_rad": round(statistics.fmean(all_abs), 9),
            "max_abs_final_error_rad": round(max_abs_final, 9),
        },
    }


def analyze(dataset: dict[str, Any]) -> dict[str, Any]:
    """Compute sampling quality and tracking metrics for a normalized dataset."""
    joint_names = dataset["joint_names"]
    planned = dataset["planned"]
    commanded = dataset["commanded"]
    feedback = dataset["feedback"]

    command_fidelity = _metrics(planned, commanded, joint_names)
    command_fidelity.update(
        {
            "reference_stream": "planned",
            "sample_stream": "commanded",
            "sample_truth": "commanded",
            "note": "planned vs the target sequence actually sent",
        }
    )

    feedback_truth = feedback["truth"]
    if feedback_truth in {"commanded", "unknown"}:
        tracking = {
            "available": False,
            "reason": (
                f"feedback truth={feedback_truth}; this stream is not measured "
                "feedback, so tracking error is not reported"
            ),
            "reference_stream": "planned",
            "sample_stream": "feedback",
            "sample_truth": feedback_truth,
            "samples_compared": 0,
            "per_joint": {},
            "aggregate": None,
        }
    else:
        tracking = _metrics(planned, feedback, joint_names)
        tracking.update(
            {
                "reference_stream": "planned",
                "sample_stream": "feedback",
                "sample_truth": feedback_truth,
            }
        )
        if tracking["available"]:
            tracking["note"] = (
                "planned vs feedback; interpolate the planned stream at each "
                "feedback timestamp (same method as the CSV export)"
            )

    notes = [
        "timebase 是声明的共享轨迹起点；未记录同步证据，因此不计算跨设备时延",
        "指标在每个样本时间戳上对计划流做线性插值；CSV 导出可复算同一结果",
    ]
    if feedback_truth == "commanded":
        notes.append(
            "反馈是厂商 joint_states 命令回显，不是编码器反馈"
        )
    if feedback_truth == "simulated":
        notes.append(
            "反馈真值=模拟：示例为合成数据，不是硬件验证"
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_version": ANALYSIS_VERSION,
        "dataset_sha256": dataset_sha256(dataset),
        "joint_names": list(joint_names),
        "timebase": dataset["timebase"],
        "sampling_quality": {
            "planned": _stream_quality(planned),
            "commanded": _stream_quality(commanded),
            "feedback": _stream_quality(feedback),
        },
        "command_fidelity": command_fidelity,
        "tracking": tracking,
        "executor": dataset.get("executor"),
        "notes": notes,
    }


def prepare_dataset(raw: Any) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Validate, analyze, and hash a raw dataset."""
    normalized = normalize_dataset(raw)
    digest = dataset_sha256(normalized)
    return normalized, analyze(normalized), digest


def summary(trajectory: dict[str, Any]) -> dict[str, Any]:
    analysis = trajectory["analysis"]
    tracking = analysis.get("tracking") or {}
    feedback_quality = analysis["sampling_quality"]["feedback"]
    return {
        "trajectory_id": trajectory["trajectory_id"],
        "created_at": trajectory["created_at"],
        "name": trajectory["name"],
        "source": trajectory["source"],
        "schema_version": trajectory["schema_version"],
        "dataset_sha256": trajectory["dataset_sha256"],
        "analysis_version": trajectory["analysis_version"],
        "joint_names": analysis["joint_names"],
        "planned_samples": analysis["sampling_quality"]["planned"]["samples"],
        "commanded_samples": analysis["sampling_quality"]["commanded"]["samples"],
        "feedback_samples": feedback_quality["samples"],
        "feedback_truth": feedback_quality["truth"],
        "duration_s": feedback_quality["duration_s"],
        "tracking_available": bool(tracking.get("available")),
        "tracking_reason": tracking.get("reason"),
    }


def alignment_rows(
    dataset: dict[str, Any], sample_stream: str
) -> tuple[list[str], list[list[str]]]:
    """Return a CSV table aligned on the chosen sample stream's timestamps."""
    if sample_stream not in {"commanded", "feedback"}:
        raise ValueError(f"unsupported sample stream {sample_stream!r}")
    joint_names = dataset["joint_names"]
    sample = dataset[sample_stream]
    aligned_times, reference_rows, sample_rows = _align(
        dataset["planned"], sample, len(joint_names)
    )
    header = ["stream", "truth", "t_s"]
    for joint in joint_names:
        header.extend(
            [
                f"{joint}_planned_rad",
                f"{joint}_sample_rad",
                f"{joint}_error_rad",
            ]
        )
    rows: list[list[str]] = []
    for t_s, reference, observed in zip(aligned_times, reference_rows, sample_rows):
        row = [sample_stream, sample["truth"], f"{t_s:.12g}"]
        for index in range(len(joint_names)):
            row.extend(
                [
                    f"{reference[index]:.12g}",
                    f"{observed[index]:.12g}",
                    f"{observed[index] - reference[index]:.12g}",
                ]
            )
        rows.append(row)
    return header, rows


def alignment_csv(dataset: dict[str, Any], sample_stream: str) -> str:
    header, rows = alignment_rows(dataset, sample_stream)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def build_demo_dataset() -> dict[str, Any]:
    """Build a deterministic, clearly simulated 5-joint dataset.

    The demo exists so the Inspector is usable without hardware. Its feedback is
    synthetic (first-order lag plus seeded noise) and must never be presented as
    hardware validation.
    """
    joint_names = [f"joint{index}" for index in range(1, 6)]
    start = [0.20, -0.15, 0.10, -0.20, 0.15]
    target = [0.60, 0.25, -0.35, 0.10, 0.05]
    rate_hz = 50.0
    duration_s = 4.0
    count = int(round(duration_s * rate_hz)) + 1
    move_start = 0.4
    move_end = 3.4

    def planned_positions(t_s: float) -> list[float]:
        if t_s <= move_start:
            return list(start)
        if t_s >= move_end:
            return list(target)
        u = (t_s - move_start) / (move_end - move_start)
        smooth = 3.0 * u * u - 2.0 * u * u * u
        return [
            start[index] + (target[index] - start[index]) * smooth
            for index in range(len(start))
        ]

    planned_points = []
    for index in range(count):
        t_s = round(index / rate_hz, 6)
        planned_points.append({"t_s": t_s, "positions": planned_positions(t_s)})

    commanded_points = [
        planned_points[index] for index in range(0, count, 2)
    ]
    if commanded_points[-1]["t_s"] < planned_points[-1]["t_s"]:
        commanded_points.append(planned_points[-1])

    rng = random.Random(42)
    feedback_points = []
    lagged = list(start)
    drop_indices = set(range(60, 65)) | {140}
    for index, point in enumerate(planned_points):
        for joint in range(len(start)):
            desired = point["positions"][joint]
            lagged[joint] += 0.35 * (desired - lagged[joint])
        noisy = [
            lagged[joint] + rng.uniform(-0.002, 0.002) for joint in range(len(start))
        ]
        if index in drop_indices:
            continue
        feedback_points.append(
            {"t_s": point["t_s"], "positions": [round(value, 9) for value in noisy]}
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "name": "G2 示例 - 模拟五关节运动",
        "description": (
            "确定性离线示例：类梯形计划运动、25 Hz 指令流、带刻意间隔的模拟滞后反馈。"
            "仅含合成数据。"
        ),
        "timebase": TIMEBASE,
        "joint_names": joint_names,
        "planned": {
            "truth": "planned",
            "time_offset_s": 0.0,
            "source": "示例生成器",
            "points": planned_points,
        },
        "commanded": {
            "truth": "commanded",
            "time_offset_s": 0.0,
            "source": "示例生成器（25 Hz 目标流）",
            "points": commanded_points,
        },
        "feedback": {
            "truth": "simulated",
            "time_offset_s": 0.0,
            "source": "示例生成器（一阶滞后、固定种子噪声、两处间隔）",
            "points": feedback_points,
        },
        "executor": {
            "status": "Succeeded",
            "detail": "模拟执行器；未涉及控制器或硬件",
            "truth": "simulated",
        },
        "metadata": {
            "origin": "生成示例",
            "hardware": False,
            "seed": 42,
            "warning": "合成数据；不是硬件验证",
        },
    }
