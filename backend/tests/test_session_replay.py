"""M3 offline session replay tests: provenance, truth labels and deltas."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.adapters.gateway_v1 import EXPECTED_CONTRACT_HASH
from app.analysis import analysis_hash, analyze_session
from app.main import create_app
from app.sessions import SessionStore

CALIBRATION = {
    "camera": "handeye-tsai-20261117",
    "joint_order": ["joint1", "joint2", "joint3", "joint4", "joint5"],
    "gripper_joint": "r_joint",
}
COMMAND = {
    "intent_id": "intent-1",
    "idempotency_key": "idem-1",
    "lease_id": "lease-1",
    "source": "studio:sess-1",
    "kind": "JOINT_STEP",
    "profile": "step",
    "contract": {"major": 0, "minor": 1, "patch": 0, "contract_hash": EXPECTED_CONTRACT_HASH},
    "joint_names": ["joint1"],
    "target_positions": [0.05],
    "max_velocity_rad_s": 0.35,
    "duration_s": 1.0,
    "verify_bus": True,
    "require_measured_state": True,
}
VALIDATION = {"approved": True, "reason_code": "OK", "violations": []}
RESULT = {
    "intent_id": "intent-1",
    "outcome": "COMPLETED",
    "reason_code": "OK",
    "message": "done",
    "safety": {"state": "ARMED_IDLE"},
    "measured_positions": [0.06, 0.0, 0.0, 0.0, 0.0],
    "bus_verified": True,
    "calibration_invalidated": False,
    "completed_at": "2026-11-26T00:00:02Z",
    "evidence_id": "ev-1",
}
GRIPPER_COMMAND = {
    **COMMAND,
    "intent_id": "intent-g",
    "kind": "GRIPPER",
    "profile": "gripper",
    "joint_names": [],
    "target_positions": [],
    "gripper": {"position_rad": 0.8, "max_velocity_rad_s": 0.5, "duration_s": 2.0, "verify_bus": True},
}
GRIPPER_RESULT = {**RESULT, "intent_id": "intent-g", "evidence_id": "ev-g"}


def _event(seq: int, kind: str, payload: dict) -> dict:
    return {
        "event_id": f"evt-{seq}",
        "seq": seq,
        "at": f"2026-11-26T00:00:0{seq}Z",
        "type": kind,
        "provenance": "studio",
        "payload": payload,
    }


def _record(*, joint_order: bool = True) -> dict:
    calibration = dict(CALIBRATION)
    if not joint_order:
        calibration.pop("joint_order", None)
    events = [
        _event(1, "session_opened", {"operator": "alice", "purpose": "replay"}),
        _event(2, "authorized", {"contract_hash": EXPECTED_CONTRACT_HASH}),
        _event(3, "intent_requested", {"command": COMMAND}),
        _event(4, "intent_executed", {"command": COMMAND, "validation": VALIDATION, "result": RESULT}),
        _event(5, "intent_requested", {"command": GRIPPER_COMMAND}),
        _event(
            6,
            "intent_executed",
            {"command": GRIPPER_COMMAND, "validation": VALIDATION, "result": GRIPPER_RESULT},
        ),
        _event(7, "intent_requested", {"command": {**COMMAND, "intent_id": "intent-bad"}}),
        _event(
            8,
            "intent_rejected",
            {
                "command": {**COMMAND, "intent_id": "intent-bad"},
                "validation": {"approved": False, "reason_code": "STEP_LIMIT", "violations": ["too big"]},
            },
        ),
        _event(9, "intent_requested", {"command": {**COMMAND, "intent_id": "intent-unknown"}}),
        _event(
            10,
            "intent_unknown",
            {
                "command": {**COMMAND, "intent_id": "intent-unknown"},
                "validation": VALIDATION,
                "result": {**RESULT, "intent_id": "intent-unknown", "outcome": "UNKNOWN", "bus_verified": False},
            },
        ),
        _event(11, "intent_requested", {"command": {**COMMAND, "intent_id": "intent-pending"}}),
        _event(12, "closed", {"reason": "done"}),
    ]
    return {
        "schema_version": 1,
        "session_id": "sess-1",
        "opened_at": "2026-11-26T00:00:00Z",
        "closed_at": "2026-11-26T00:01:00Z",
        "operator": "alice",
        "purpose": "replay",
        "studio_version": "0.7.0",
        "studio_commit": "abc123",
        "contract_hash": EXPECTED_CONTRACT_HASH,
        "gateway_url": "http://127.0.0.1:8766",
        "robot_profile": "armpi-ultra-01",
        "calibration": calibration,
        "state": "closed",
        "close_reason": "done",
        "actions": [],
        "degraded_periods": [],
        "events": events,
        "record_hash": "sha256:" + "a" * 64,
    }


def test_replay_keeps_commanded_and_measured_separate() -> None:
    analysis = analyze_session(_record())
    joint = analysis["intents"][0]
    assert joint["kind"] == "JOINT_STEP"
    assert joint["commanded_target_rad"] == 0.05
    assert joint["command_truth"] == "commanded"
    assert joint["measurement_truth"] == "measured"
    assert joint["measurement_source"] == "gateway_motion_result"
    assert joint["measured_joint_rad"] == 0.06
    assert joint["delta_rad"] == 0.01
    assert joint["delta_truth"] == "derived"
    assert joint["status"] == "COMPLETED"
    assert joint["result"]["bus_verified"] is True
    assert analysis["summary"]["max_abs_delta_rad"] == 0.01

    gripper = analysis["intents"][1]
    assert gripper["kind"] == "GRIPPER"
    assert gripper["commanded_target_rad"] == 0.8
    assert gripper["measured_joint_rad"] is None
    assert gripper["delta_rad"] is None
    assert "gripper measured position" in gripper["measurement_note"]

    assert analysis["provenance"]["contract_hash"] == EXPECTED_CONTRACT_HASH
    assert analysis["provenance"]["calibration"]["camera"] == "handeye-tsai-20261117"


def test_replay_summary_counts_rejections_unknown_and_pending() -> None:
    analysis = analyze_session(_record())
    summary = analysis["summary"]
    assert summary["intent_count"] == 5
    assert summary["completed"] == 2
    assert summary["rejected"] == 1
    assert summary["unknown"] == 1
    assert summary["pending"] == 1
    assert summary["bus_verified"] == 2
    statuses = [item["status"] for item in analysis["intents"]]
    assert "VALIDATION_REJECTED" in statuses
    assert "UNKNOWN" in statuses
    assert "pending" in statuses


def test_replay_without_joint_order_does_not_guess() -> None:
    analysis = analyze_session(_record(joint_order=False))
    joint = analysis["intents"][0]
    assert joint["measured_joint_rad"] is None
    assert joint["delta_rad"] is None
    assert any("joint_order" in item for item in analysis["limitations"])


def test_analysis_api_and_export(tmp_path: Path, settings) -> None:
    store = SessionStore(
        tmp_path / "sessions.db",
        tmp_path / "exports",
        studio_version="0.7.0",
        studio_commit="abc123",
        robot_id="armpi-ultra-01",
        gateway_url="http://127.0.0.1:8766",
        calibration=CALIBRATION,
    )
    session = store.open_session(operator="alice", purpose="api replay")
    session_id = session["session_id"]
    store.record_intent(session_id, "intent_requested", {"command": COMMAND})
    store.record_intent(
        session_id,
        "intent_executed",
        {"command": COMMAND, "validation": VALIDATION, "result": RESULT},
    )
    store.close_session(session_id, reason="done")
    store.close()

    configured = replace(
        settings,
        session_db_path=tmp_path / "sessions.db",
        session_export_dir=tmp_path / "exports",
    )
    app = create_app(settings=configured)
    with TestClient(app) as client:
        response = client.get(f"/api/session/{session_id}/analysis")
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["summary"]["completed"] == 1
        assert analysis["intents"][0]["delta_rad"] == 0.01

        exported = client.get(f"/api/session/{session_id}/analysis/export")
        assert exported.status_code == 200, exported.text
        body = exported.json()
        path = Path(body["export_path"])
        assert path.is_file()
        payload = json.loads(path.read_text())
        assert payload["analysis_hash"] == body["analysis_hash"]
        assert payload["analysis_hash"] == analysis_hash(analysis)
