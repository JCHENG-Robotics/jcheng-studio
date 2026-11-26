"""G3 Experiment Lab: experiment definitions, paired trials, and analysis.

The statistical formulas mirror ``ros2_ws/src/armpi_analysis/armpi_analysis/
paired_trials.py`` (the Phase 3 implementation) through the shared
``jcheng_stats`` package, so the Studio and ROS tooling report the same
numbers for the same records. This module does not touch an adapter, ROS, or a
serial port.

Experiment definitions are immutable and trials are append-only. A pair means
the same fixture attempted once with ``baseline`` and once with ``intervention``;
the plan randomizes pair order and within-pair method order from a seed.
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, timezone
from typing import Any

from jcheng_stats import (
    bootstrap_mean_ci as _bootstrap_mean_ci,
    exact_binomial_two_sided as _exact_binomial_two_sided,
    summary as _summary,
)

SCHEMA_VERSION = 1
ANALYSIS_VERSION = "g3-1"
METHODS = ("baseline", "intervention")
METHOD_LABELS = {
    "baseline": "厂商基线",
    "intervention": "项目方法",
}
FAILURE_CODES = {
    "COMMUNICATION": "通信失败",
    "TIMEOUT": "超时",
    "JOINT_LIMIT": "关节限位",
    "PLANNING_FAILED": "规划失败",
    "TRAJECTORY_FAILED": "轨迹执行失败",
    "VISION_FAILED": "视觉定位失败",
    "CALIBRATION_INVALID": "标定无效",
    "GRASP_FAILED": "抓取失败",
    "SAFETY_VETO": "安全门禁否决",
    "UNKNOWN": "未知",
}
MEASUREMENT_SOURCES = {
    "vision",
    "caliper",
    "fixture",
    "operator",
    "simulated",
    "unknown",
}
ARTIFACT_KINDS = {"rosbag2", "log", "image", "csv", "trajectory", "other"}
DATA_ORIGINS = {"hardware", "simulated"}
MAX_PAIRS = 200
MAX_ARTIFACTS = 20
LIMITATIONS = [
    "分析质量取决于夹具与测量本身。",
    "精确符号检验忽略效应量；应与平均差值和置信区间一起报告。",
    "McNemar 只检验配对成功率；它不能解释失败原因。",
    "顺序效应是观测性的，不是随机交叉设计。",
    "假设同一夹具在各配对间几何一致。",
]


class ExperimentDataError(ValueError):
    """An experiment definition or trial failed validation."""

    def __init__(self, issues: list[dict[str, str]]) -> None:
        super().__init__("experiment data is invalid")
        self.issues = issues


def _issue(path: str, message: str) -> dict[str, str]:
    return {"path": path, "message": message}


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value: Any, path: str, issues: list[dict[str, str]], limit: int = 500) -> str:
    if not isinstance(value, str) or len(value) > limit:
        issues.append(_issue(path, f"must be a string of at most {limit} chars"))
        return ""
    return value


def build_plan(planned_pairs: int, seed: int, fixtures: list[str]) -> dict[str, Any]:
    """Build a seeded pair schedule; identical to the Phase 3 plan semantics."""
    if planned_pairs == 0:
        return {
            "schema_version": SCHEMA_VERSION,
            "seed": seed,
            "n_pairs": 0,
            "pairs": [],
            "entries": [],
        }
    if len(fixtures) == 0:
        fixture_list = [f"fixture_{index + 1:03d}" for index in range(planned_pairs)]
    elif len(fixtures) == 1:
        fixture_list = [fixtures[0]] * planned_pairs
    elif len(fixtures) == planned_pairs:
        fixture_list = list(fixtures)
    else:
        raise ValueError("fixtures must be empty, length 1, or planned_pairs")

    generator = random.Random(seed)
    order = list(range(planned_pairs))
    generator.shuffle(order)
    pairs: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    sequence = 0
    for index in order:
        pair_id = f"p{index + 1:03d}"
        fixture = fixture_list[index]
        method_order = list(METHODS)
        generator.shuffle(method_order)
        pairs.append({"pair_id": pair_id, "fixture": fixture, "order": method_order})
        for trial_order, method in enumerate(method_order, start=1):
            sequence += 1
            entries.append(
                {
                    "sequence": sequence,
                    "pair_id": pair_id,
                    "trial_order": trial_order,
                    "method": method,
                    "fixture": fixture,
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "seed": seed,
        "n_pairs": planned_pairs,
        "pairs": pairs,
        "entries": entries,
    }


def normalize_experiment(raw: Any) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    if not isinstance(raw, dict):
        raise ExperimentDataError([_issue("$", "experiment must be a JSON object")])

    name = _text(raw.get("name"), "name", issues, 200).strip()
    if not name:
        issues.append(_issue("name", "must be a non-empty name"))
    purpose = _text(raw.get("purpose"), "purpose", issues, 2000).strip()
    if not purpose:
        issues.append(_issue("purpose", "must describe the experiment purpose"))

    data_origin = raw.get("data_origin", "hardware")
    if data_origin not in DATA_ORIGINS:
        issues.append(_issue("data_origin", "must be 'hardware' or 'simulated'"))
        data_origin = "hardware"

    operator = _text(raw.get("operator", ""), "operator", issues, 200)
    robot_id = _text(raw.get("robot_id", ""), "robot_id", issues, 100)
    hardware_config = _text(
        raw.get("hardware_config", ""), "hardware_config", issues, 500
    )
    calibration_version = _text(
        raw.get("calibration_version", ""), "calibration_version", issues, 100
    )
    mapping_version = _text(
        raw.get("mapping_version", ""), "mapping_version", issues, 100
    )
    stop_rules = _text(raw.get("stop_rules", ""), "stop_rules", issues, 2000)
    notes = _text(raw.get("notes", ""), "notes", issues, 2000)

    planned_pairs = raw.get("planned_pairs", 0)
    if not _is_int(planned_pairs) or not 0 <= planned_pairs <= MAX_PAIRS:
        issues.append(
            _issue("planned_pairs", f"must be an integer 0..{MAX_PAIRS}")
        )
        planned_pairs = 0

    seed = raw.get("seed", 7)
    if not _is_int(seed) or not 0 <= seed <= 2**31 - 1:
        issues.append(_issue("seed", "must be an integer 0..2^31-1"))
        seed = 7

    bootstrap_samples = raw.get("bootstrap_samples", 10000)
    if not _is_int(bootstrap_samples) or not 100 <= bootstrap_samples <= 50000:
        issues.append(_issue("bootstrap_samples", "must be an integer 100..50000"))
        bootstrap_samples = 10000

    confidence = raw.get("confidence", 0.95)
    if not _is_number(confidence) or not 0.5 <= float(confidence) <= 0.999:
        issues.append(_issue("confidence", "must be a number 0.5..0.999"))
        confidence = 0.95

    fixtures = raw.get("fixtures", [])
    if not isinstance(fixtures, list):
        issues.append(_issue("fixtures", "must be a list of fixture names"))
        fixtures = []
    else:
        cleaned = []
        for index, fixture in enumerate(fixtures):
            if not isinstance(fixture, str) or not fixture.strip() or len(fixture) > 100:
                issues.append(
                    _issue(f"fixtures[{index}]", "must be a non-empty name <=100 chars")
                )
            else:
                cleaned.append(fixture.strip())
        fixtures = cleaned
    if fixtures and len(fixtures) not in {1, planned_pairs}:
        issues.append(
            _issue("fixtures", "must be empty, length 1, or length planned_pairs")
        )

    if issues:
        raise ExperimentDataError(issues)

    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "purpose": purpose,
        "data_origin": data_origin,
        "operator": operator,
        "robot_id": robot_id,
        "hardware_config": hardware_config,
        "calibration_version": calibration_version,
        "mapping_version": mapping_version,
        "stop_rules": stop_rules,
        "protocol": {
            "planned_pairs": planned_pairs,
            "seed": seed,
            "fixtures": fixtures,
            "bootstrap_samples": bootstrap_samples,
            "confidence": float(confidence),
            "primary_metric": "continuous_error_mm",
        },
        "plan": build_plan(planned_pairs, seed, fixtures),
        "notes": notes,
    }


def _normalize_target(value: Any, issues: list[dict[str, str]]) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        issues.append(_issue("target", "must be an object when present"))
        return {}
    allowed = {"x_mm", "y_mm", "z_mm", "yaw_deg"}
    target: dict[str, float] = {}
    for key, item in value.items():
        if key not in allowed:
            issues.append(_issue(f"target.{key}", "unknown target field"))
            continue
        if not _is_number(item):
            issues.append(_issue(f"target.{key}", "must be a finite number"))
            continue
        target[key] = float(item)
    return target


def _normalize_artifacts(value: Any, issues: list[dict[str, str]]) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        issues.append(_issue("artifacts", "must be a list"))
        return []
    if len(value) > MAX_ARTIFACTS:
        issues.append(_issue("artifacts", f"at most {MAX_ARTIFACTS} references"))
        return []
    artifacts = []
    for index, item in enumerate(value):
        path = f"artifacts[{index}]"
        if not isinstance(item, dict):
            issues.append(_issue(path, "must be an object"))
            continue
        kind = item.get("kind")
        if kind not in ARTIFACT_KINDS:
            issues.append(
                _issue(f"{path}.kind", "must be one of " + ", ".join(sorted(ARTIFACT_KINDS)))
            )
            continue
        uri = item.get("path")
        if not isinstance(uri, str) or not uri or len(uri) > 500:
            issues.append(_issue(f"{path}.path", "must be a non-empty path/URI <=500 chars"))
            continue
        note = item.get("note", "")
        if not isinstance(note, str) or len(note) > 200:
            issues.append(_issue(f"{path}.note", "must be a string <=200 chars"))
            note = ""
        artifacts.append({"kind": kind, "path": uri, "note": note})
    return artifacts


def _find_plan_entry(
    plan: dict[str, Any], pair_id: str, method: str
) -> dict[str, Any] | None:
    for entry in plan.get("entries") or []:
        if entry.get("pair_id") == pair_id and entry.get("method") == method:
            return entry
    return None


def normalize_trial(
    raw: Any,
    experiment: dict[str, Any],
    existing_trials: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    if not isinstance(raw, dict):
        raise ExperimentDataError([_issue("$", "trial must be a JSON object")])
    existing_trials = existing_trials or []

    pair_id = raw.get("pair_id")
    if not isinstance(pair_id, str) or not pair_id.strip() or len(pair_id) > 64:
        issues.append(_issue("pair_id", "must be a non-empty string <=64 chars"))
        pair_id = ""
    else:
        pair_id = pair_id.strip()

    method = raw.get("method")
    if method not in METHODS:
        issues.append(_issue("method", "must be 'baseline' or 'intervention'"))
        method = ""

    trial_order = raw.get("trial_order")
    if not _is_int(trial_order) or trial_order not in (1, 2):
        issues.append(_issue("trial_order", "must be 1 or 2 within the pair"))
        trial_order = 0

    fixture = raw.get("fixture", "")
    if not isinstance(fixture, str) or not fixture.strip() or len(fixture) > 100:
        issues.append(_issue("fixture", "must be a non-empty name <=100 chars"))
        fixture = ""
    else:
        fixture = fixture.strip()

    entry = None
    if experiment["plan"]["entries"]:
        entry = _find_plan_entry(experiment["plan"], pair_id, method)
        if entry is None:
            issues.append(
                _issue(
                    "pair_id/method",
                    "pair/method is not in the experiment randomization plan",
                )
            )
        else:
            if trial_order != entry["trial_order"]:
                issues.append(
                    _issue(
                        "trial_order",
                        f"plan requires {entry['trial_order']} for this pair/method",
                    )
                )
            if fixture != entry["fixture"]:
                issues.append(
                    _issue("fixture", f"plan requires fixture '{entry['fixture']}'")
                )

    for item in existing_trials:
        if item.get("pair_id") == pair_id and item.get("method") == method:
            issues.append(
                _issue("pair_id/method", "this pair/method is already recorded")
            )
        if item.get("pair_id") == pair_id and item.get("trial_order") == trial_order:
            issues.append(
                _issue("trial_order", "this pair/trial_order is already recorded")
            )

    success = raw.get("grasp_success")
    if not isinstance(success, bool):
        issues.append(_issue("grasp_success", "must be a boolean"))
        success = False

    error = raw.get("continuous_error_mm")
    if error is not None:
        if not _is_number(error) or float(error) < 0.0:
            issues.append(
                _issue("continuous_error_mm", "must be null or finite and non-negative")
            )
            error = None
        else:
            error = float(error)
    elif success:
        issues.append(
            _issue("continuous_error_mm", "a successful trial needs a measured error")
        )

    rotation = raw.get("rotation_error_deg")
    if rotation is not None:
        if not _is_number(rotation) or float(rotation) < 0.0:
            issues.append(
                _issue("rotation_error_deg", "must be null or finite and non-negative")
            )
            rotation = None
        else:
            rotation = float(rotation)

    failure_code = raw.get("failure_code")
    failure_detail = _text(
        raw.get("failure_detail", ""), "failure_detail", issues, 500
    )
    if success:
        if failure_code not in (None, "", "NONE"):
            issues.append(
                _issue("failure_code", "a successful trial must not have a failure code")
            )
        if failure_detail:
            issues.append(
                _issue("failure_detail", "a successful trial must not have failure detail")
            )
        failure_code = None
        failure_detail = ""
    else:
        if failure_code not in FAILURE_CODES:
            issues.append(
                _issue(
                    "failure_code",
                    "a failed trial needs one of: " + ", ".join(FAILURE_CODES),
                )
            )

    duration = raw.get("duration_s")
    if duration is not None:
        if not _is_number(duration) or float(duration) < 0.0:
            issues.append(_issue("duration_s", "must be null or finite and non-negative"))
            duration = None
        else:
            duration = float(duration)

    measurement_source = raw.get("measurement_source", "unknown")
    if measurement_source not in MEASUREMENT_SOURCES:
        issues.append(
            _issue(
                "measurement_source",
                "must be one of " + ", ".join(sorted(MEASUREMENT_SOURCES)),
            )
        )
        measurement_source = "unknown"

    trajectory_id = raw.get("trajectory_id")
    if trajectory_id is not None and (
        not isinstance(trajectory_id, str)
        or not trajectory_id.strip()
        or len(trajectory_id) > 128
    ):
        issues.append(_issue("trajectory_id", "must be null or a non-empty id"))
        trajectory_id = None
    elif isinstance(trajectory_id, str):
        trajectory_id = trajectory_id.strip()

    artifacts = _normalize_artifacts(raw.get("artifacts"), issues)
    target = _normalize_target(raw.get("target"), issues)
    operator_notes = _text(
        raw.get("operator_notes", ""), "operator_notes", issues, 2000
    )

    if issues:
        raise ExperimentDataError(issues)

    return {
        "schema_version": SCHEMA_VERSION,
        "pair_id": pair_id,
        "trial_order": trial_order,
        "method": method,
        "fixture": fixture,
        "grasp_success": success,
        "continuous_error_mm": error,
        "rotation_error_deg": rotation,
        "failure_code": failure_code,
        "failure_detail": failure_detail,
        "duration_s": duration,
        "measurement_source": measurement_source,
        "trajectory_id": trajectory_id,
        "artifacts": artifacts,
        "operator_notes": operator_notes,
        "target": target,
        "truth": "simulated" if experiment["data_origin"] == "simulated" else "measured",
    }


# --------------------------------------------------------------- statistics








def _count_failures(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        code = record.get("failure_code")
        if code:
            counts[code] = counts.get(code, 0) + 1
    return counts


def _method_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [
        record["continuous_error_mm"]
        for record in records
        if record.get("continuous_error_mm") is not None
    ]
    rotations = [
        record["rotation_error_deg"]
        for record in records
        if record.get("rotation_error_deg") is not None
    ]
    durations = [
        record["duration_s"]
        for record in records
        if record.get("duration_s") is not None
    ]
    successes = sum(1 for record in records if record.get("grasp_success"))
    return {
        "n": len(records),
        "error": _summary(errors),
        "rotation_error_deg": _summary(rotations),
        "duration_s": _summary(durations),
        "successes": successes,
        "success_rate": successes / len(records) if records else None,
        "failure_codes": _count_failures(records),
    }


def _validation_summary(trials: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for trial in trials:
        groups.setdefault(trial["pair_id"], []).append(trial)
    complete = []
    incomplete = []
    for pair_id, group in groups.items():
        methods = {item["method"] for item in group}
        if methods == set(METHODS):
            complete.append(pair_id)
        else:
            incomplete.append(
                {
                    "pair_id": pair_id,
                    "methods": sorted(methods),
                    "n_records": len(group),
                }
            )
    return {
        "n_input": len(trials),
        "n_valid": len(trials),
        "n_invalid": 0,
        "invalid": [],
        "complete_pairs": sorted(complete),
        "incomplete_pairs": sorted(incomplete, key=lambda item: item["pair_id"]),
    }


def analyze_experiment(
    experiment: dict[str, Any],
    trials: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a deterministic paired-trial report for the stored records."""
    validation = _validation_summary(trials)
    protocol = experiment["protocol"]
    seed = protocol["seed"]
    bootstrap_samples = protocol["bootstrap_samples"]
    confidence = protocol["confidence"]

    complete = []
    for pair_id in validation["complete_pairs"]:
        group = [item for item in trials if item["pair_id"] == pair_id]
        by_method = {item["method"]: item for item in group}
        complete.append(
            {
                "pair_id": pair_id,
                "baseline": by_method["baseline"],
                "intervention": by_method["intervention"],
            }
        )

    base = {
        "schema_version": SCHEMA_VERSION,
        "analysis_version": ANALYSIS_VERSION,
        "experiment_id": experiment.get("experiment_id"),
        "data_origin": experiment["data_origin"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "bootstrap_samples": bootstrap_samples,
        "confidence": confidence,
        "validation": {
            "n_input": validation["n_input"],
            "n_valid": validation["n_valid"],
            "n_invalid": validation["n_invalid"],
            "invalid": validation["invalid"],
            "n_complete_pairs": len(complete),
            "incomplete_pairs": validation["incomplete_pairs"],
        },
        "limitations": LIMITATIONS,
    }
    if not complete:
        return {
            **base,
            "available": False,
            "reason": (
                "没有完整配对；至少为一对记录一次基线和一次干预"
            ),
            "methods": {},
            "paired": None,
            "success": None,
            "randomization": {"baseline_first": 0, "intervention_first": 0},
            "order_effect": {
                "groups": {},
                "difference_mm": None,
                "note": "观测性结果，不是随机交叉设计",
            },
            "failure_codes": {"baseline": {}, "intervention": {}},
            "warnings": ["还没有完整配对，未生成比较结果。"],
        }

    baseline_records = [pair["baseline"] for pair in complete]
    intervention_records = [pair["intervention"] for pair in complete]
    paired = []
    for pair in complete:
        baseline_error = pair["baseline"]["continuous_error_mm"]
        intervention_error = pair["intervention"]["continuous_error_mm"]
        if baseline_error is None or intervention_error is None:
            continue
        paired.append(
            {
                "pair_id": pair["pair_id"],
                "difference_mm": intervention_error - baseline_error,
                "baseline_first": (
                    pair["baseline"]["trial_order"] < pair["intervention"]["trial_order"]
                ),
            }
        )

    differences = [item["difference_mm"] for item in paired]
    difference_summary = _summary(differences)
    improvement = (
        -difference_summary["mean"] if difference_summary["mean"] is not None else None
    )
    interval = _bootstrap_mean_ci(differences, bootstrap_samples, seed, confidence)
    wins = sum(1 for value in differences if value < -1e-12)
    losses = sum(1 for value in differences if value > 1e-12)
    ties = len(differences) - wins - losses
    sign_p = _exact_binomial_two_sided(wins, losses)
    effect_size = None
    if difference_summary["std"] and difference_summary["std"] > 0.0:
        effect_size = difference_summary["mean"] / difference_summary["std"]

    baseline_summary = _method_summary(baseline_records)
    intervention_summary = _method_summary(intervention_records)
    baseline_only = sum(
        1
        for pair in complete
        if pair["baseline"]["grasp_success"] and not pair["intervention"]["grasp_success"]
    )
    intervention_only = sum(
        1
        for pair in complete
        if not pair["baseline"]["grasp_success"] and pair["intervention"]["grasp_success"]
    )
    mcnemar_p = _exact_binomial_two_sided(intervention_only, baseline_only)

    order_groups = {
        "baseline_first": [
            item["difference_mm"] for item in paired if item["baseline_first"]
        ],
        "intervention_first": [
            item["difference_mm"] for item in paired if not item["baseline_first"]
        ],
    }
    order_effect = {name: _summary(values) for name, values in order_groups.items()}
    order_difference = None
    if (
        order_effect["baseline_first"]["mean"] is not None
        and order_effect["intervention_first"]["mean"] is not None
    ):
        order_difference = (
            order_effect["intervention_first"]["mean"]
            - order_effect["baseline_first"]["mean"]
        )
    baseline_first_count = sum(
        1
        for pair in complete
        if pair["baseline"]["trial_order"] < pair["intervention"]["trial_order"]
    )

    warnings = []
    if experiment["data_origin"] == "simulated":
        warnings.append("数据为模拟生成，不是硬件验证。")
    if len(complete) < 8:
        warnings.append(
            f"仅有 {len(complete)} 个完整配对；比较结果只能作为描述性参考。"
        )
    if baseline_only + intervention_only < 10:
        warnings.append(
            "配对成功率检验功效不足 "
            f"（{baseline_only + intervention_only} 个不一致对）；"
            "不要宣称成功率有显著改善。"
        )
    if len(paired) < len(complete):
        warnings.append(
            f"{len(complete) - len(paired)} 个完整配对缺少连续误差，"
            "已从连续结果分析中排除。"
        )

    return {
        **base,
        "available": True,
        "reason": None,
        "methods": {
            "baseline": baseline_summary,
            "intervention": intervention_summary,
        },
        "paired": {
            "n_pairs": len(complete),
            "n_pairs_with_error": len(paired),
            "difference_mm": difference_summary,
            "improvement_mm": improvement,
            "ci95_difference_mm": interval,
            "effect_size_dz": effect_size,
            "sign_test": {
                "wins": wins,
                "losses": losses,
                "ties": ties,
                "p_value": sign_p,
            },
        },
        "success": {
            "baseline_rate": baseline_summary["success_rate"],
            "intervention_rate": intervention_summary["success_rate"],
            "difference": (
                intervention_summary["success_rate"] - baseline_summary["success_rate"]
                if baseline_summary["success_rate"] is not None
                and intervention_summary["success_rate"] is not None
                else None
            ),
            "discordant": {
                "baseline_only": baseline_only,
                "intervention_only": intervention_only,
            },
            "mcnemar_exact_p": mcnemar_p,
        },
        "randomization": {
            "baseline_first": baseline_first_count,
            "intervention_first": len(complete) - baseline_first_count,
        },
        "order_effect": {
            "groups": order_effect,
            "difference_mm": order_difference,
            "note": "observational; not a randomized crossover",
        },
        "failure_codes": {
            "baseline": baseline_summary["failure_codes"],
            "intervention": intervention_summary["failure_codes"],
        },
        "warnings": warnings,
    }


DEMO_SIGNATURE = hashlib.sha256(b"armpi-debug-studio-g3-demo-v1").hexdigest()


def build_demo_experiment() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build a deterministic simulated experiment; never hardware evidence."""
    raw_experiment = {
        "name": "G3 示例 - 模拟配对抓取",
        "purpose": (
            "演示基线/干预的配对记录与分析。全部试验均为合成数据。"
        ),
        "data_origin": "simulated",
        "operator": "示例生成器",
        "robot_id": "armpi-ultra-01",
        "hardware_config": "模拟；未使用机械臂或相机",
        "calibration_version": "demo-not-calibrated",
        "mapping_version": "vendor-provisional-1",
        "stop_rules": "不适用：示例不执行任何运动",
        "planned_pairs": 16,
        "seed": 7,
        "fixtures": ["demo_fixture_A"],
        "bootstrap_samples": 5000,
        "confidence": 0.95,
        "notes": "G3 合成示例；不是硬件验证。",
    }
    experiment = normalize_experiment(raw_experiment)
    generator = random.Random(20261118)
    failure_pool = ["VISION_FAILED", "GRASP_FAILED", "TIMEOUT", "TRAJECTORY_FAILED"]
    pair_baseline: dict[str, float] = {}
    trials = []
    for entry in experiment["plan"]["entries"]:
        baseline = entry["method"] == "baseline"
        if baseline:
            baseline_error = max(0.4, generator.gauss(2.6, 0.55))
            pair_baseline[entry["pair_id"]] = baseline_error
        else:
            baseline_error = pair_baseline.get(
                entry["pair_id"], max(0.4, generator.gauss(2.6, 0.55))
            )
        if baseline:
            error: float | None = baseline_error
            success = generator.random() < 0.76
        else:
            error = max(0.2, baseline_error - 1.0 + generator.gauss(0, 0.35))
            success = generator.random() < 0.94
        if not success and generator.random() < 0.4:
            error = None
        failure_code = None
        failure_detail = ""
        if not success:
            failure_code = failure_pool[generator.randrange(len(failure_pool))]
            failure_detail = "合成示例失败"
        rotation = None if error is None else abs(generator.gauss(0.9, 0.3))
        trials.append(
            {
                "pair_id": entry["pair_id"],
                "trial_order": entry["trial_order"],
                "method": entry["method"],
                "fixture": entry["fixture"],
                "grasp_success": success,
                "continuous_error_mm": error,
                "rotation_error_deg": rotation,
                "failure_code": failure_code,
                "failure_detail": failure_detail,
                "duration_s": generator.uniform(4.0, 8.0),
                "measurement_source": "simulated",
                "artifacts": [
                    {
                        "kind": "other",
                        "path": f"simulated://g3-demo/{entry['pair_id']}/{entry['method']}",
                        "note": "合成引用",
                    }
                ],
                "operator_notes": "合成示例；无硬件。",
            }
        )
    return experiment, trials
