"""Command state machine.

Evidence rules:
- `Acknowledged` is never reachable: the vendor SDK has no command ACK.
- `Dispatched -> Completed` is rejected; observed state must be explicit.
- Unknown can become Observed when late evidence arrives, but never Completed
  without an intervening Observed transition.
"""

from __future__ import annotations

from .enums import CommandStage
from .models import CommandRecord, utc_now_iso


class InvalidTransition(ValueError):
    pass


ALLOWED_TRANSITIONS: dict[CommandStage, set[CommandStage]] = {
    CommandStage.REQUESTED: {CommandStage.VALIDATED, CommandStage.FAILED},
    CommandStage.VALIDATED: {CommandStage.DISPATCHED, CommandStage.FAILED},
    CommandStage.DISPATCHED: {CommandStage.OBSERVED, CommandStage.FAILED, CommandStage.UNKNOWN},
    CommandStage.ACKNOWLEDGED: set(),
    CommandStage.OBSERVED: {CommandStage.COMPLETED, CommandStage.FAILED, CommandStage.UNKNOWN},
    CommandStage.COMPLETED: set(),
    CommandStage.FAILED: set(),
    CommandStage.UNKNOWN: {CommandStage.OBSERVED, CommandStage.FAILED},
}

TERMINAL_STAGES = {CommandStage.COMPLETED, CommandStage.FAILED}

UNREACHABLE_STAGES = {CommandStage.ACKNOWLEDGED}


def validate_transition(current: CommandStage, new: CommandStage) -> None:
    if new in UNREACHABLE_STAGES:
        raise InvalidTransition(
            f"{new.value} is unreachable: the vendor SDK has no command ACK"
        )
    if current == new:
        return
    if new not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(f"illegal command transition: {current.value} -> {new.value}")


def transition(
    record: CommandRecord,
    new: CommandStage,
    *,
    error_code: str | None = None,
    details: dict | None = None,
) -> CommandRecord:
    validate_transition(record.status, new)
    now = utc_now_iso()
    record.status = new
    record.updated_at = now
    if error_code is not None:
        record.error_code = error_code
    if details:
        record.details.update(details)
    record.stage_history.append(
        {
            "stage": new.value,
            "at": now,
            "error_code": error_code,
            "details": details or {},
        }
    )
    return record
