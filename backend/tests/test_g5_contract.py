"""A G5-shaped recorder dataset must pass the G2 import contract unchanged.

The gateway recorder itself lives in the frozen jcheng-manipulation baseline
(`armpi-debug-studio/ros2_gateway`). This test keeps the consumer side of the
contract in the console: the dataset shape the recorder emits is still accepted
by `normalize_dataset`, and the feedback stream is labelled `measured`.
"""

from __future__ import annotations

from app.domain.trajectory import analyze, normalize_dataset

ARM_JOINTS = ("joint1", "joint2", "joint3", "joint4", "joint5")


def _samples(start: float, count: int, value: float) -> list[dict[str, object]]:
    return [
        {"t_s": start + index * 0.02, "positions": [value] * len(ARM_JOINTS)}
        for index in range(count)
    ]


def _stream(
    truth: str, source: str, points: list[dict[str, object]]
) -> dict[str, object]:
    return {
        "truth": truth,
        "time_offset_s": 0.0,
        "source": source,
        "points": points,
    }


def test_recorder_dataset_is_accepted_by_g2_and_tracking_is_measured() -> None:
    dataset = {
        "schema_version": 1,
        "name": "g5 contract bridge",
        "description": "",
        "timebase": "shared_trajectory_start",
        "joint_names": list(ARM_JOINTS),
        "planned": _stream(
            "planned",
            "static hold fixture (no motion)",
            [
                {"t_s": 0.0, "positions": [0.0] * len(ARM_JOINTS)},
                {"t_s": 1.0, "positions": [0.0] * len(ARM_JOINTS)},
            ],
        ),
        "commanded": _stream("commanded", "vendor command echo", _samples(0.02, 20, 0.0)),
        "feedback": _stream("measured", "servo_states", _samples(0.03, 20, 0.001)),
        "executor": None,
        "metadata": {"recorder": "armpi_studio_trajectory_recorder"},
    }
    normalized = normalize_dataset(dataset)
    assert normalized["feedback"]["truth"] == "measured"
    assert len(normalized["feedback"]["points"]) == 20

    result = analyze(normalized)
    assert result["tracking"]["available"] is True
    assert result["tracking"]["aggregate"]["max_abs_error_rad"] > 0
    assert result["command_fidelity"]["available"] is True
