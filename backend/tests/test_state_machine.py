from __future__ import annotations

import pytest

from app.domain.enums import CommandStage
from app.domain.models import CommandRecord, utc_now_iso
from app.domain.state_machine import InvalidTransition, transition, validate_transition


def _record() -> CommandRecord:
    return CommandRecord(
        command_id="cmd_test",
        session_id="s1",
        status=CommandStage.REQUESTED,
        targets=[],
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )


def test_happy_path() -> None:
    record = _record()
    for stage in (
        CommandStage.VALIDATED,
        CommandStage.DISPATCHED,
        CommandStage.OBSERVED,
        CommandStage.COMPLETED,
    ):
        transition(record, stage)
    assert record.status is CommandStage.COMPLETED
    assert [item["stage"] for item in record.stage_history] == [
        "Validated",
        "Dispatched",
        "Observed",
        "Completed",
    ]


def test_dispatched_to_completed_rejected() -> None:
    with pytest.raises(InvalidTransition):
        validate_transition(CommandStage.DISPATCHED, CommandStage.COMPLETED)


def test_acknowledged_unreachable() -> None:
    with pytest.raises(InvalidTransition):
        validate_transition(CommandStage.DISPATCHED, CommandStage.ACKNOWLEDGED)


def test_unknown_can_become_observed() -> None:
    validate_transition(CommandStage.DISPATCHED, CommandStage.UNKNOWN)
    validate_transition(CommandStage.UNKNOWN, CommandStage.OBSERVED)


def test_unknown_cannot_become_completed_directly() -> None:
    with pytest.raises(InvalidTransition):
        validate_transition(CommandStage.UNKNOWN, CommandStage.COMPLETED)


def test_terminal_stage_is_final() -> None:
    with pytest.raises(InvalidTransition):
        validate_transition(CommandStage.COMPLETED, CommandStage.FAILED)
