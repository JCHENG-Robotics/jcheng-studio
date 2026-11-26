from __future__ import annotations

import csv
import io
import math
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domain.trajectory import (
    TrajectoryDataError,
    analyze,
    build_demo_dataset,
    normalize_dataset,
    prepare_dataset,
)


def _dataset(
    *,
    feedback_truth: str = "measured",
    feedback_positions: list[float] | None = None,
    feedback_times: list[float] | None = None,
) -> dict[str, Any]:
    """One joint, linear planned motion, deliberately simple arithmetic."""
    times = [0.0, 1.0, 2.0]
    feedback_times = feedback_times or [0.0, 0.5, 1.5, 2.0]
    feedback_positions = feedback_positions or [0.0, 0.75, 1.75, 2.2]
    return {
        "schema_version": 1,
        "name": "unit-test trajectory",
        "description": "fixed offline dataset",
        "timebase": "shared_trajectory_start",
        "joint_names": ["joint1"],
        "planned": {
            "truth": "planned",
            "points": [
                {"t_s": t, "positions": [float(t)]} for t in times
            ],
        },
        "commanded": {
            "truth": "commanded",
            "points": [
                {"t_s": t, "positions": [float(t)]} for t in times
            ],
        },
        "feedback": {
            "truth": feedback_truth,
            "points": [
                {"t_s": t, "positions": [p]}
                for t, p in zip(feedback_times, feedback_positions)
            ],
        },
        "executor": {"status": "Succeeded", "truth": "simulated"},
    }


def test_analysis_metrics_are_exact_on_fixed_dataset() -> None:
    dataset, analysis, digest = prepare_dataset(_dataset())
    assert len(digest) == 64
    assert analysis["analysis_version"] == "g2-1"
    assert dataset["joint_names"] == ["joint1"]

    tracking = analysis["tracking"]
    assert tracking["available"] is True
    assert tracking["samples_compared"] == 4
    joint = tracking["per_joint"]["joint1"]
    assert joint["max_abs_error_rad"] == pytest.approx(0.25)
    assert joint["rmse_rad"] == pytest.approx(math.sqrt(0.04125))
    assert joint["final_error_rad"] == pytest.approx(0.2)
    assert joint["mean_abs_error_rad"] == pytest.approx(0.175)

    quality = analysis["sampling_quality"]["feedback"]
    assert quality["samples"] == 4
    assert quality["duration_s"] == pytest.approx(2.0)
    assert quality["effective_rate_hz"] == pytest.approx(1.5)
    assert quality["max_gap_s"] == pytest.approx(1.0)
    assert quality["gaps_over_1_5x_median"] == 1
    assert quality["dropout_ratio"] == pytest.approx(0.2)

    fidelity = analysis["command_fidelity"]
    assert fidelity["available"] is True
    assert fidelity["aggregate"]["max_abs_error_rad"] == pytest.approx(0.0)


def test_command_echo_feedback_has_no_tracking_error() -> None:
    _, analysis, _ = prepare_dataset(_dataset(feedback_truth="commanded"))
    tracking = analysis["tracking"]
    assert tracking["available"] is False
    assert tracking["aggregate"] is None
    assert tracking["per_joint"] == {}
    assert "not measured feedback" in tracking["reason"]
    assert any("命令回显" in note for note in analysis["notes"])


def test_invalid_datasets_are_rejected_with_paths() -> None:
    bad_time = _dataset()
    bad_time["planned"]["points"][2]["t_s"] = 0.5
    with pytest.raises(TrajectoryDataError) as error:
        normalize_dataset(bad_time)
    assert any("strictly increasing" in issue["message"] for issue in error.value.issues)

    bad_positions = _dataset()
    bad_positions["feedback"]["points"][0]["positions"] = [float("nan")]
    with pytest.raises(TrajectoryDataError) as error:
        normalize_dataset(bad_positions)
    assert any("finite numbers" in issue["message"] for issue in error.value.issues)

    bad_timebase = _dataset()
    bad_timebase["timebase"] = "unknown"
    with pytest.raises(TrajectoryDataError) as error:
        normalize_dataset(bad_timebase)
    assert any("timebase" in issue["path"] for issue in error.value.issues)

    duplicate_names = _dataset()
    duplicate_names["joint_names"] = ["joint1", "joint1"]
    with pytest.raises(TrajectoryDataError):
        normalize_dataset(duplicate_names)

    bad_version = _dataset()
    bad_version["schema_version"] = True
    with pytest.raises(TrajectoryDataError) as error:
        normalize_dataset(bad_version)
    assert any("schema_version" in issue["path"] for issue in error.value.issues)


def test_demo_is_deterministic_and_explicitly_simulated() -> None:
    first = build_demo_dataset()
    second = build_demo_dataset()
    _, first_analysis, first_hash = prepare_dataset(first)
    _, second_analysis, second_hash = prepare_dataset(second)
    assert first_hash == second_hash
    assert first_analysis["sampling_quality"]["feedback"]["truth"] == "simulated"
    assert first_analysis["executor"]["truth"] == "simulated"
    assert first_analysis["tracking"]["available"] is True
    assert any("合成" in note for note in first_analysis["notes"])


def test_demo_import_list_get_and_recompute(client: TestClient) -> None:
    imported = client.post("/api/trajectories/demo")
    assert imported.status_code == 200, imported.text
    body = imported.json()
    trajectory_id = body["trajectory_id"]
    assert body["duplicate"] is False
    assert body["source"] == "demo"
    assert body["analysis"]["tracking"]["available"] is True

    duplicate = client.post("/api/trajectories/demo")
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["trajectory_id"] == trajectory_id
    assert duplicate.json()["analysis"]["dataset_sha256"] == body["dataset_sha256"]

    listing = client.get("/api/trajectories")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["trajectory_id"] == trajectory_id
    assert items[0]["tracking_available"] is True

    detail = client.get(f"/api/trajectories/{trajectory_id}")
    assert detail.status_code == 200
    assert detail.json()["dataset"]["name"] == "G2 示例 - 模拟五关节运动"

    recomputed = client.post(f"/api/trajectories/{trajectory_id}/recompute")
    assert recomputed.status_code == 200
    assert recomputed.json()["matches_stored"] is True

    missing = client.get("/api/trajectories/trj_missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


def test_import_rejects_invalid_dataset(client: TestClient) -> None:
    bad = _dataset()
    bad["planned"]["points"][0]["positions"] = [0.0, 0.0]
    response = client.post("/api/trajectories", json=bad)
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "BAD_REQUEST"
    assert error["details"]["issues"][0]["path"].endswith("positions")


def test_csv_export_recomputes_analysis(client: TestClient) -> None:
    body = client.post("/api/trajectories/demo").json()
    trajectory_id = body["trajectory_id"]
    analysis = body["analysis"]

    response = client.get(f"/api/trajectories/{trajectory_id}/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows
    assert rows[0]["stream"] == "feedback"
    assert rows[0]["truth"] == "simulated"

    for joint in analysis["joint_names"]:
        squared = [float(row[f"{joint}_error_rad"]) ** 2 for row in rows]
        rmse = math.sqrt(sum(squared) / len(squared))
        expected = analysis["tracking"]["per_joint"][joint]["rmse_rad"]
        assert rmse == pytest.approx(expected, abs=1e-7)
        expected_max = analysis["tracking"]["per_joint"][joint]["max_abs_error_rad"]
        observed_max = max(abs(float(row[f"{joint}_error_rad"])) for row in rows)
        assert observed_max == pytest.approx(expected_max, abs=1e-7)

    commanded = client.get(
        f"/api/trajectories/{trajectory_id}/export.csv", params={"sample": "commanded"}
    )
    assert commanded.status_code == 200
    commanded_rows = list(csv.DictReader(io.StringIO(commanded.text)))
    assert commanded_rows[0]["stream"] == "commanded"

    exported = client.get(f"/api/trajectories/{trajectory_id}/export.json")
    assert exported.status_code == 200
    assert exported.headers["content-disposition"].endswith('.json"')
    assert exported.json()["trajectory_id"] == trajectory_id


def test_trajectory_import_is_offline_and_audited(client: TestClient) -> None:
    before = client.get("/api/events").json()["items"]
    client.post("/api/trajectories/demo")
    events = client.get("/api/events").json()["items"]
    before_ids = {event["event_id"] for event in before}
    new_events = [event for event in events if event["event_id"] not in before_ids]
    stages = [event["stage"] for event in new_events]
    assert "TrajectoryImported" in stages
    assert "Dispatched" not in stages
    assert "Requested" not in stages

    health = client.get("/api/health").json()
    assert health["capabilities"]["replay"] is True
    assert health["capabilities"]["hardware_motion"] is False
