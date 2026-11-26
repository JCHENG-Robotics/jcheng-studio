from __future__ import annotations

from app.adapters.mock import MockRobotAdapter
from app.domain.enums import CommandStage, ConnectionState, StopState, Truth, Validity
from app.domain.mapping import MappingConfig


def _targets(joint: str = "joint1", value: float = 0.1) -> list[dict]:
    return [{"joint": joint, "target_rad": value, "duration_s": 1.0, "target_raw": 0}]


def test_initial_state_is_simulated(mapping: MappingConfig, clock) -> None:
    adapter = MockRobotAdapter(mapping, clock=clock)
    state = adapter.snapshot()
    assert state.source_label.value == "SIMULATION"
    assert state.connection_state is ConnectionState.SIMULATION
    assert len(state.joints) == 5
    assert all(joint.truth is Truth.SIMULATED for joint in state.joints)
    assert all(joint.validity is Validity.VALID for joint in state.joints)
    assert state.capabilities.hardware_motion is False
    assert state.capabilities.mock_faults is True


def test_dispatch_and_completion(mapping: MappingConfig, clock) -> None:
    adapter = MockRobotAdapter(mapping, clock=clock)
    result = adapter.dispatch(_targets(), "cmd_1")
    assert result.status is CommandStage.DISPATCHED
    clock.advance(2.0)
    state = adapter.snapshot()
    joint = state.joints[0]
    assert joint.position_rad == 0.1
    events = adapter.drain_events()
    stages = [event["stage"] for event in events]
    assert stages == ["Observed", "Completed"]
    assert state.connection_state is ConnectionState.SIMULATION


def test_timeout_fault_yields_unknown(mapping: MappingConfig, clock) -> None:
    adapter = MockRobotAdapter(mapping, clock=clock)
    adapter.inject("timeout")
    result = adapter.dispatch(_targets(), "cmd_timeout")
    assert result.status is CommandStage.UNKNOWN
    assert result.error_code == "MOCK_TIMEOUT"
    clock.advance(2.0)
    adapter.snapshot()
    events = adapter.drain_events()
    assert [event["stage"] for event in events] == ["Unknown"]


def test_disconnect_fault(mapping: MappingConfig, clock) -> None:
    adapter = MockRobotAdapter(mapping, clock=clock)
    adapter.inject("disconnect")
    state = adapter.snapshot()
    assert state.connection_state is ConnectionState.DISCONNECTED
    assert state.stop_state is StopState.STOP_UNKNOWN
    assert all(joint.validity is Validity.UNAVAILABLE for joint in state.joints)
    assert adapter.connection_report().gateway_online is False


def test_empty_feedback_fault(mapping: MappingConfig, clock) -> None:
    adapter = MockRobotAdapter(mapping, clock=clock)
    adapter.inject("empty_feedback")
    state = adapter.snapshot()
    assert all(joint.validity is Validity.UNAVAILABLE for joint in state.joints)
    assert all(joint.position_rad is None for joint in state.joints)


def test_nan_feedback_fault(mapping: MappingConfig, clock) -> None:
    adapter = MockRobotAdapter(mapping, clock=clock)
    adapter.inject("nan_feedback")
    state = adapter.snapshot()
    assert all(joint.validity is Validity.INVALID for joint in state.joints)
    assert all(joint.position_rad is None for joint in state.joints)


def test_missing_servo_fault_is_per_joint(mapping: MappingConfig, clock) -> None:
    adapter = MockRobotAdapter(mapping, clock=clock)
    adapter.inject("missing_servo", joint="joint2")
    state = adapter.snapshot()
    by_name = {joint.name: joint for joint in state.joints}
    assert by_name["joint2"].validity is Validity.UNAVAILABLE
    assert by_name["joint1"].validity is Validity.VALID


def test_driver_exit_fault(mapping: MappingConfig, clock) -> None:
    adapter = MockRobotAdapter(mapping, clock=clock)
    adapter.inject("driver_exit")
    state = adapter.snapshot()
    assert state.connection_state is ConnectionState.DEGRADED
    assert adapter.connection_report().driver_online is False


def test_stop_four_states(mapping: MappingConfig, clock) -> None:
    adapter = MockRobotAdapter(mapping, clock=clock)
    first = adapter.stop("stop_1")
    assert first.state is StopState.STOP_OBSERVED

    adapter.dispatch(_targets(), "cmd_stop")
    clock.advance(0.2)
    second = adapter.stop("stop_2")
    assert second.state is StopState.STOP_DISPATCHED
    assert "not a hardware emergency stop" in second.evidence
    clock.advance(0.3)
    state = adapter.snapshot()
    assert state.stop_state is StopState.STOP_OBSERVED
    assert any(event["stage"] == "StopObserved" for event in adapter.drain_events())


def test_reset_clears_faults(mapping: MappingConfig, clock) -> None:
    adapter = MockRobotAdapter(mapping, clock=clock)
    adapter.inject("disconnect")
    adapter.reset()
    state = adapter.snapshot()
    assert state.connection_state is ConnectionState.SIMULATION
    assert state.faults == []
