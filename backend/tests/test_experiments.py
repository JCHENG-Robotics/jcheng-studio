from __future__ import annotations

import csv
import io
import math

import pytest
from fastapi.testclient import TestClient

from app.domain.experiments import (
    FAILURE_CODES,
    ExperimentDataError,
    analyze_experiment,
    build_demo_experiment,
    normalize_experiment,
    normalize_trial,
)


def _experiment(**overrides) -> dict:
    payload = {
        "name": "paired grasp test",
        "purpose": "compare baseline and intervention under the same fixture",
        "data_origin": "simulated",
        "operator": "tester",
        "robot_id": "armpi-ultra-01",
        "hardware_config": "simulated",
        "calibration_version": "test-calibration",
        "mapping_version": "vendor-provisional-1",
        "stop_rules": "stop on any safety fault",
        "planned_pairs": 2,
        "seed": 7,
        "fixtures": ["fixture_A"],
        "bootstrap_samples": 500,
        "confidence": 0.95,
    }
    payload.update(overrides)
    return payload


def _trial(entry: dict, *, success: bool = True, error: float | None = 2.0, **overrides) -> dict:
    payload = {
        "pair_id": entry["pair_id"],
        "trial_order": entry["trial_order"],
        "method": entry["method"],
        "fixture": entry["fixture"],
        "grasp_success": success,
        "continuous_error_mm": error if success else None,
        "rotation_error_deg": 0.8 if success else None,
        "failure_code": None if success else "GRASP_FAILED",
        "failure_detail": "" if success else "missed the object",
        "duration_s": 5.0,
        "measurement_source": "simulated",
        "operator_notes": "test trial",
    }
    payload.update(overrides)
    return payload


def test_plan_is_deterministic_and_pairs_are_complete() -> None:
    first = normalize_experiment(_experiment())
    second = normalize_experiment(_experiment())
    assert first["plan"] == second["plan"]
    assert first["plan"]["n_pairs"] == 2
    assert len(first["plan"]["entries"]) == 4
    for pair_id in ("p001", "p002"):
        entries = [item for item in first["plan"]["entries"] if item["pair_id"] == pair_id]
        assert sorted(item["method"] for item in entries) == ["baseline", "intervention"]
        assert sorted(item["trial_order"] for item in entries) == [1, 2]
        assert all(item["fixture"] == "fixture_A" for item in entries)


def test_experiment_definition_validation() -> None:
    with pytest.raises(ExperimentDataError) as error:
        normalize_experiment(_experiment(name="", purpose=""))
    paths = {issue["path"] for issue in error.value.issues}
    assert {"name", "purpose"} <= paths

    with pytest.raises(ExperimentDataError):
        normalize_experiment(_experiment(data_origin="replay"))
    with pytest.raises(ExperimentDataError):
        normalize_experiment(_experiment(planned_pairs=3, fixtures=["a", "b"]))
    with pytest.raises(ExperimentDataError):
        normalize_experiment(_experiment(bootstrap_samples=10))


def test_trial_validation_requires_error_or_failure_code() -> None:
    experiment = normalize_experiment(_experiment())
    entry = experiment["plan"]["entries"][0]
    with pytest.raises(ExperimentDataError) as error:
        normalize_trial(_trial(entry, success=True, error=None), experiment)
    assert "continuous_error_mm" in {issue["path"] for issue in error.value.issues}

    with pytest.raises(ExperimentDataError) as error:
        normalize_trial(
            _trial(entry, success=False, error=None, failure_code=None), experiment
        )
    assert "failure_code" in {issue["path"] for issue in error.value.issues}

    with pytest.raises(ExperimentDataError):
        normalize_trial(_trial(entry, success=True, failure_code="GRASP_FAILED"), experiment)

    # A plan mismatch is rejected instead of being silently accepted.
    with pytest.raises(ExperimentDataError):
        normalize_trial(_trial(entry, fixture="other_fixture"), experiment)
    other_order = 2 if entry["trial_order"] == 1 else 1
    with pytest.raises(ExperimentDataError):
        normalize_trial(_trial(entry, trial_order=other_order), experiment)


def test_analysis_matches_fixed_arithmetic() -> None:
    experiment = normalize_experiment(_experiment(planned_pairs=0))
    trials = [
        {
            "pair_id": "p1",
            "trial_order": 1,
            "method": "baseline",
            "fixture": "f",
            "grasp_success": True,
            "continuous_error_mm": 2.0,
            "rotation_error_deg": 1.0,
            "failure_code": None,
            "failure_detail": "",
            "duration_s": 4.0,
            "measurement_source": "simulated",
            "trajectory_id": None,
            "artifacts": [],
            "operator_notes": "",
            "target": {},
            "truth": "simulated",
        },
        {
            "pair_id": "p1",
            "trial_order": 2,
            "method": "intervention",
            "fixture": "f",
            "grasp_success": True,
            "continuous_error_mm": 1.0,
            "rotation_error_deg": 0.5,
            "failure_code": None,
            "failure_detail": "",
            "duration_s": 5.0,
            "measurement_source": "simulated",
            "trajectory_id": None,
            "artifacts": [],
            "operator_notes": "",
            "target": {},
            "truth": "simulated",
        },
        {
            "pair_id": "p2",
            "trial_order": 1,
            "method": "baseline",
            "fixture": "f",
            "grasp_success": True,
            "continuous_error_mm": 3.0,
            "rotation_error_deg": 1.0,
            "failure_code": None,
            "failure_detail": "",
            "duration_s": 4.0,
            "measurement_source": "simulated",
            "trajectory_id": None,
            "artifacts": [],
            "operator_notes": "",
            "target": {},
            "truth": "simulated",
        },
        {
            "pair_id": "p2",
            "trial_order": 2,
            "method": "intervention",
            "fixture": "f",
            "grasp_success": True,
            "continuous_error_mm": 2.5,
            "rotation_error_deg": 0.5,
            "failure_code": None,
            "failure_detail": "",
            "duration_s": 5.0,
            "measurement_source": "simulated",
            "trajectory_id": None,
            "artifacts": [],
            "operator_notes": "",
            "target": {},
            "truth": "simulated",
        },
    ]
    report = analyze_experiment(experiment, trials)
    assert report["available"] is True
    assert report["validation"]["n_complete_pairs"] == 2
    assert report["paired"]["n_pairs_with_error"] == 2
    assert report["paired"]["difference_mm"]["mean"] == pytest.approx(-0.75)
    assert report["paired"]["improvement_mm"] == pytest.approx(0.75)
    assert report["paired"]["sign_test"]["wins"] == 2
    assert report["paired"]["sign_test"]["losses"] == 0
    assert report["paired"]["sign_test"]["p_value"] == pytest.approx(0.5)
    assert report["success"]["discordant"] == {
        "baseline_only": 0,
        "intervention_only": 0,
    }
    assert report["success"]["mcnemar_exact_p"] == pytest.approx(1.0)
    assert report["methods"]["baseline"]["error"]["mean"] == pytest.approx(2.5)
    assert report["methods"]["intervention"]["error"]["mean"] == pytest.approx(1.75)
    assert any("模拟" in warning for warning in report["warnings"])


def test_api_create_record_analyze_and_export(client: TestClient) -> None:
    created = client.post("/api/experiments", json=_experiment())
    assert created.status_code == 200, created.text
    detail = created.json()
    experiment_id = detail["experiment"]["experiment_id"]
    entries = detail["experiment"]["plan"]["entries"]
    assert len(entries) == 4
    assert detail["analysis"]["available"] is False
    assert "没有完整配对" in detail["analysis"]["reason"]

    for entry in entries:
        error = 1.0 if entry["method"] == "intervention" else 2.0
        if entry["pair_id"] == "p002":
            error += 1.0
        response = client.post(
            f"/api/experiments/{experiment_id}/trials",
            json=_trial(entry, error=error),
        )
        assert response.status_code == 200, response.text

    analysis = client.get(f"/api/experiments/{experiment_id}/analysis")
    assert analysis.status_code == 200
    body = analysis.json()
    assert body["available"] is True
    assert body["validation"]["n_complete_pairs"] == 2
    assert body["paired"]["improvement_mm"] == pytest.approx(1.0)

    exported = client.get(f"/api/experiments/{experiment_id}/export.csv")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    assert len(rows) == 4
    assert rows[0]["truth"] == "simulated"
    by_pair = {}
    for row in rows:
        by_pair.setdefault(row["pair_id"], {})[row["method"]] = float(
            row["continuous_error_mm"]
        )
    differences = [
        values["intervention"] - values["baseline"] for values in by_pair.values()
    ]
    assert -math.fsum(differences) / len(differences) == pytest.approx(1.0)

    exported_json = client.get(f"/api/experiments/{experiment_id}/export.json")
    assert exported_json.status_code == 200
    assert set(exported_json.json()) == {
        "schema_version",
        "exported_at",
        "experiment",
        "trials",
        "analysis",
    }
    assert len(exported_json.json()["trials"]) == 4


def test_api_rejects_duplicate_and_plan_mismatch(client: TestClient) -> None:
    detail = client.post("/api/experiments", json=_experiment()).json()
    experiment_id = detail["experiment"]["experiment_id"]
    entry = detail["experiment"]["plan"]["entries"][0]
    first = client.post(
        f"/api/experiments/{experiment_id}/trials", json=_trial(entry)
    )
    assert first.status_code == 200
    duplicate = client.post(
        f"/api/experiments/{experiment_id}/trials", json=_trial(entry)
    )
    assert duplicate.status_code == 400
    assert "already recorded" in str(duplicate.json()["error"]["details"]["issues"])

    mismatch = client.post(
        f"/api/experiments/{experiment_id}/trials",
        json=_trial(entry, fixture="not_the_plan"),
    )
    assert mismatch.status_code == 400
    other_entry = next(
        item
        for item in detail["experiment"]["plan"]["entries"]
        if (item["pair_id"], item["method"]) != (entry["pair_id"], entry["method"])
    )
    missing_trajectory = client.post(
        f"/api/experiments/{experiment_id}/trials",
        json=_trial(other_entry, trajectory_id="trj_missing"),
    )
    assert missing_trajectory.status_code == 400
    assert missing_trajectory.json()["error"]["details"]["issues"][0]["path"] == (
        "trajectory_id"
    )

    missing = client.get("/api/experiments/exp_missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


def test_demo_is_idempotent_and_lists_failure_codes(client: TestClient) -> None:
    demo = client.post("/api/experiments/demo")
    assert demo.status_code == 200, demo.text
    detail = demo.json()
    assert detail["duplicate"] is False
    experiment_id = detail["experiment"]["experiment_id"]
    assert detail["experiment"]["data_origin"] == "simulated"
    assert len(detail["trials"]) == 32
    assert detail["analysis"]["available"] is True
    assert detail["analysis"]["validation"]["n_complete_pairs"] == 16
    assert detail["analysis"]["data_origin"] == "simulated"
    assert any("模拟" in warning for warning in detail["analysis"]["warnings"])

    again = client.post("/api/experiments/demo")
    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    assert again.json()["experiment"]["experiment_id"] == experiment_id
    assert len(again.json()["trials"]) == 32

    listing = client.get("/api/experiments")
    assert listing.status_code == 200
    body = listing.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "complete"
    assert body["items"][0]["n_complete_pairs"] == 16
    for code in (
        "COMMUNICATION",
        "TIMEOUT",
        "JOINT_LIMIT",
        "PLANNING_FAILED",
        "TRAJECTORY_FAILED",
        "VISION_FAILED",
        "CALIBRATION_INVALID",
        "GRASP_FAILED",
        "UNKNOWN",
    ):
        assert code in body["failure_codes"]
        assert code in FAILURE_CODES

    events = client.get("/api/events").json()["items"]
    stages = [event["stage"] for event in events]
    assert "ExperimentSeeded" in stages
    assert "Dispatched" not in stages
    assert "Requested" not in stages


def test_manual_experiment_without_plan(client: TestClient) -> None:
    detail = client.post(
        "/api/experiments",
        json=_experiment(planned_pairs=0, fixtures=[]),
    ).json()
    experiment_id = detail["experiment"]["experiment_id"]
    assert detail["experiment"]["plan"]["entries"] == []

    baseline = _trial(
        {"pair_id": "manual_1", "trial_order": 1, "method": "baseline", "fixture": "f"}
    )
    intervention = _trial(
        {"pair_id": "manual_1", "trial_order": 2, "method": "intervention", "fixture": "f"},
        error=1.25,
    )
    assert (
        client.post(
            f"/api/experiments/{experiment_id}/trials", json=baseline
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/experiments/{experiment_id}/trials", json=intervention
        ).status_code
        == 200
    )
    report = client.get(f"/api/experiments/{experiment_id}/analysis").json()
    assert report["available"] is True
    assert report["paired"]["improvement_mm"] == pytest.approx(0.75)


def test_demo_builder_is_deterministic() -> None:
    first_exp, first_trials = build_demo_experiment()
    second_exp, second_trials = build_demo_experiment()
    assert first_exp == second_exp
    assert first_trials == second_trials
    assert len(first_trials) == 32
    assert first_exp["data_origin"] == "simulated"
    assert all(trial["measurement_source"] == "simulated" for trial in first_trials)
