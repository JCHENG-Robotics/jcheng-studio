"""Offline M3 session replay and commanded-vs-measured analysis.

The input is a sealed or open M2a session record (including M2b intent events)
loaded from the local store or an export. The output keeps commanded and
gateway-reported measured values in separate fields; a derived delta is only
computed for a COMPLETED joint step and only when the session recorded the
profile joint order. No robot or gateway is contacted.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

ANALYSIS_SCHEMA_VERSION = 1


class SessionAnalysisError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def analysis_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _duration_s(opened_at: str | None, closed_at: str | None) -> float | None:
    if not opened_at or not closed_at:
        return None
    try:
        start = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((end - start).total_seconds(), 3)


def _command_target(command: dict[str, Any]) -> tuple[float | None, str | None, str | None]:
    kind = str(command.get("kind", ""))
    if kind == "JOINT_STEP":
        positions = command.get("target_positions")
        names = command.get("joint_names")
        if isinstance(positions, list) and positions:
            joint = str(names[0]) if isinstance(names, list) and names else None
            return float(positions[0]), joint, None
        return None, None, None
    if kind == "GRIPPER":
        gripper = command.get("gripper")
        if isinstance(gripper, dict) and gripper.get("position_rad") is not None:
            return float(gripper["position_rad"]), None, "r_joint"
        return None, None, "r_joint"
    return None, None, None


def _intent_from_event(
    event: dict[str, Any],
    *,
    joint_order: list[str],
    gripper_joint: str | None,
) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    command = payload.get("command") if isinstance(payload.get("command"), dict) else {}
    validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else None
    result = payload.get("result") if isinstance(payload.get("result"), dict) else None
    error = payload.get("error") if isinstance(payload.get("error"), dict) else None

    target, joint, gripper = _command_target(command)
    measured_positions = (
        result.get("measured_positions")
        if isinstance(result, dict) and isinstance(result.get("measured_positions"), list)
        else None
    )
    measured_joint_rad: float | None = None
    measurement_note: str | None = None
    if joint is not None and measured_positions is not None:
        if joint_order and joint in joint_order:
            index = joint_order.index(joint)
            if index < len(measured_positions):
                measured_joint_rad = float(measured_positions[index])
            else:
                measurement_note = "measured_positions is shorter than the recorded joint order"
        else:
            measurement_note = (
                "session calibration has no joint_order; measured joint position is not mapped"
            )
    elif gripper is not None:
        measurement_note = (
            "MotionResult carries arm positions only; gripper measured position is not in the record"
        )

    status = str(event.get("type", ""))
    outcome = str(result["outcome"]) if isinstance(result, dict) and result.get("outcome") else None
    delta: float | None = None
    if (
        event.get("type") == "intent_executed"
        and outcome == "COMPLETED"
        and target is not None
        and measured_joint_rad is not None
    ):
        delta = round(measured_joint_rad - target, 9)

    return {
        "intent_id": command.get("intent_id"),
        "event_type": status,
        "at": event.get("at"),
        "kind": command.get("kind"),
        "profile": command.get("profile"),
        "joint": joint,
        "gripper_joint": gripper,
        "commanded_target_rad": target,
        "command_truth": "commanded" if target is not None else None,
        "measurement_truth": "measured" if measured_positions is not None else None,
        "measurement_source": "gateway_motion_result" if measured_positions is not None else None,
        "gateway_measured_positions": measured_positions,
        "measured_joint_rad": measured_joint_rad,
        "delta_rad": delta,
        "delta_truth": "derived" if delta is not None else None,
        "measurement_note": measurement_note,
        "validation": (
            {
                "approved": bool(validation.get("approved")),
                "reason_code": validation.get("reason_code"),
                "violations": validation.get("violations", []),
            }
            if isinstance(validation, dict)
            else None
        ),
        "result": (
            {
                "outcome": outcome,
                "reason_code": result.get("reason_code"),
                "message": result.get("message"),
                "bus_verified": bool(result.get("bus_verified")),
                "calibration_invalidated": bool(result.get("calibration_invalidated")),
                "completed_at": result.get("completed_at"),
                "evidence_id": result.get("evidence_id"),
            }
            if isinstance(result, dict)
            else None
        ),
        "error": error,
        "lease_id": command.get("lease_id"),
        "source": command.get("source"),
        "contract": command.get("contract"),
    }


def analyze_session(record: dict[str, Any]) -> dict[str, Any]:
    """Return a provenance-labelled replay of one session record."""
    if not isinstance(record, dict):
        raise SessionAnalysisError("SESSION_INVALID", "session record must be an object")
    session_id = record.get("session_id")
    events = record.get("events")
    if not session_id or not isinstance(events, list):
        raise SessionAnalysisError("SESSION_INVALID", "session_id and events are required")

    calibration = record.get("calibration") if isinstance(record.get("calibration"), dict) else {}
    raw_order = calibration.get("joint_order")
    joint_order = [
        str(name) for name in raw_order
    ] if isinstance(raw_order, list) else []
    gripper_joint = calibration.get("gripper_joint")
    if not isinstance(gripper_joint, str):
        gripper_joint = "r_joint" if "r_joint" in calibration else None

    intents: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if not str(event.get("type", "")).startswith("intent_"):
            continue
        if event.get("type") == "intent_requested":
            item = _intent_from_event(
                event, joint_order=joint_order, gripper_joint=gripper_joint
            )
            item["status"] = "pending"
            intents.append(item)
            continue
        # Terminal event: update the matching requested intent, or add it if
        # the requested event is missing from the record.
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        command = payload.get("command") if isinstance(payload.get("command"), dict) else {}
        intent_id = command.get("intent_id")
        item = next(
            (candidate for candidate in intents if candidate.get("intent_id") == intent_id),
            None,
        )
        if item is None:
            item = _intent_from_event(
                event, joint_order=joint_order, gripper_joint=gripper_joint
            )
            intents.append(item)
        else:
            item.update(
                _intent_from_event(
                    event, joint_order=joint_order, gripper_joint=gripper_joint
                )
            )
        item["status"] = (
            str(item["result"]["outcome"])
            if isinstance(item.get("result"), dict) and item["result"].get("outcome")
            else ("UNKNOWN" if event.get("type") == "intent_unknown" else "REJECTED")
        )
        if event.get("type") == "intent_rejected" and item.get("validation") is not None:
            item["status"] = "VALIDATION_REJECTED"

    completed = [item for item in intents if item.get("status") == "COMPLETED"]
    deltas = [abs(float(item["delta_rad"])) for item in completed if item.get("delta_rad") is not None]
    outcomes = [str(item.get("status", "")) for item in intents]
    limitations = [
        "Measured positions are the gateway's MotionResult array; the Studio did not re-read the bus.",
        "Gripper measured position is not present in MotionResult; no gripper delta is computed.",
        "This is an offline record analysis, not hardware validation.",
    ]
    if not joint_order:
        limitations.append(
            "This session did not record joint_order in calibration; joint deltas are unavailable."
        )

    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "session": {
            "session_id": session_id,
            "schema_version": record.get("schema_version"),
            "operator": record.get("operator"),
            "purpose": record.get("purpose"),
            "state": record.get("state"),
            "opened_at": record.get("opened_at"),
            "closed_at": record.get("closed_at"),
            "close_reason": record.get("close_reason"),
            "duration_s": _duration_s(record.get("opened_at"), record.get("closed_at")),
            "record_hash": record.get("record_hash"),
        },
        "provenance": {
            "studio_version": record.get("studio_version"),
            "studio_commit": record.get("studio_commit"),
            "contract_hash": record.get("contract_hash"),
            "robot_profile": record.get("robot_profile"),
            "gateway_url": record.get("gateway_url"),
            "calibration": calibration,
        },
        "summary": {
            "intent_count": len(intents),
            "completed": outcomes.count("COMPLETED"),
            "rejected": outcomes.count("REJECTED") + outcomes.count("VALIDATION_REJECTED"),
            "aborted": outcomes.count("ABORTED"),
            "unknown": outcomes.count("UNKNOWN"),
            "pending": outcomes.count("pending"),
            "bus_verified": sum(
                1
                for item in intents
                if isinstance(item.get("result"), dict) and item["result"].get("bus_verified")
            ),
            "max_abs_delta_rad": round(max(deltas), 9) if deltas else None,
            "measured_joint_count": sum(
                1 for item in intents if item.get("measured_joint_rad") is not None
            ),
        },
        "intents": intents,
        "degraded_periods": record.get("degraded_periods", []),
        "events": events,
        "limitations": limitations,
    }
