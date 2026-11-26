"""G3 experiment operations, split out of RobotService.

The methods are moved verbatim as a structural boundary; they still use the
service facade for the store, lock, events and analysis helpers.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from typing import Any

from ..domain.enums import ErrorCode
from ..domain.experiments import (
    ANALYSIS_VERSION as EXPERIMENT_ANALYSIS_VERSION,
    DEMO_SIGNATURE,
    FAILURE_CODES,
    METHOD_LABELS,
    ExperimentDataError,
    analyze_experiment,
    build_demo_experiment,
    normalize_experiment,
    normalize_trial,
)
from ..domain.models import utc_now_iso
from ..errors import ServiceError, StorageError


class ExperimentOperations:
    # ------------------------------------------------------ experiments (G3)
    @staticmethod
    def _experiment_summary(row: dict[str, Any]) -> dict[str, Any]:
        protocol = row["protocol"]
        n_trials = int(row["n_trials"])
        n_complete = int(row["n_complete_pairs"])
        planned = int(protocol.get("planned_pairs", 0))
        if n_trials == 0:
            status = "planned"
        elif planned > 0 and n_complete >= planned:
            status = "complete"
        else:
            status = "in_progress"
        n_baseline = int(row["n_baseline"])
        n_intervention = int(row["n_intervention"])
        return {
            "experiment_id": row["experiment_id"],
            "created_at": row["created_at"],
            "name": row["name"],
            "data_origin": row["data_origin"],
            "operator": row["operator"],
            "truth": "simulated" if row["data_origin"] == "simulated" else "measured",
            "schema_version": row.get("schema_version", 1),
            "analysis_version": row["analysis_version"],
            "planned_pairs": planned,
            "seed": protocol.get("seed"),
            "n_trials": n_trials,
            "n_complete_pairs": n_complete,
            "n_baseline": n_baseline,
            "n_intervention": n_intervention,
            "baseline_success_rate": (
                int(row["n_baseline_successes"]) / n_baseline if n_baseline else None
            ),
            "intervention_success_rate": (
                int(row["n_intervention_successes"]) / n_intervention
                if n_intervention
                else None
            ),
            "status": status,
        }

    def list_experiments(self, limit: int = 50) -> dict[str, Any]:
        try:
            rows = self.store.list_experiments(limit)
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "experiment store read failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        return {
            "items": [self._experiment_summary(row) for row in rows],
            "failure_codes": FAILURE_CODES,
            "methods": METHOD_LABELS,
            "schema_version": 1,
        }

    def _experiment_or_404(self, experiment_id: str) -> dict[str, Any]:
        try:
            record = self.store.get_experiment(experiment_id)
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "experiment store read failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        if record is None:
            raise ServiceError(
                ErrorCode.NOT_FOUND.value,
                f"experiment {experiment_id!r} does not exist",
                status_code=404,
            )
        return record

    def _experiment_detail(self, record: dict[str, Any]) -> dict[str, Any]:
        try:
            trials = self.store.list_trials(record["experiment_id"])
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "trial store read failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        return {
            "experiment": record,
            "trials": trials,
            "analysis": analyze_experiment(record, trials),
        }

    def create_experiment(self, raw: Any) -> dict[str, Any]:
        try:
            definition = normalize_experiment(raw)
        except ExperimentDataError as exc:
            raise ServiceError(
                ErrorCode.BAD_REQUEST.value,
                "experiment definition is invalid",
                status_code=400,
                details={"issues": exc.issues},
            ) from exc
        record = {
            "experiment_id": f"exp_{uuid.uuid4().hex[:12]}",
            "created_at": utc_now_iso(),
            "analysis_version": EXPERIMENT_ANALYSIS_VERSION,
            **definition,
        }
        try:
            inserted = self.store.insert_experiment(
                experiment_id=record["experiment_id"],
                created_at=record["created_at"],
                signature=None,
                definition=record,
            )
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "experiment store write failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        if not inserted:
            raise ServiceError(
                ErrorCode.INTERNAL_ERROR.value,
                "experiment insert was ignored",
                status_code=500,
            )
        storage_error = None
        try:
            self._append(
                self._event(
                    "ExperimentCreated",
                    None,
                    "experiment",
                    f"experiment created: {record['name']}",
                    None,
                    {
                        "experiment_id": record["experiment_id"],
                        "data_origin": record["data_origin"],
                        "planned_pairs": record["protocol"]["planned_pairs"],
                    },
                )
            )
        except ServiceError as exc:
            storage_error = exc.details
        return {
            **self._experiment_detail(record),
            "duplicate": False,
            "storage_error": storage_error,
        }

    def import_demo_experiment(self) -> dict[str, Any]:
        try:
            existing = self.store.find_experiment_by_signature(DEMO_SIGNATURE)
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "experiment store read failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        if existing is not None:
            return {
                **self._experiment_detail(existing),
                "duplicate": True,
                "storage_error": None,
            }

        definition, raw_trials = build_demo_experiment()
        record = {
            "experiment_id": f"exp_{uuid.uuid4().hex[:12]}",
            "created_at": utc_now_iso(),
            "analysis_version": EXPERIMENT_ANALYSIS_VERSION,
            **definition,
        }
        try:
            inserted = self.store.insert_experiment(
                experiment_id=record["experiment_id"],
                created_at=record["created_at"],
                signature=DEMO_SIGNATURE,
                definition=record,
            )
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "experiment store write failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        if not inserted:
            existing = self.store.find_experiment_by_signature(DEMO_SIGNATURE)
            if existing is not None:
                return {
                    **self._experiment_detail(existing),
                    "duplicate": True,
                    "storage_error": None,
                }
            raise ServiceError(
                ErrorCode.INTERNAL_ERROR.value,
                "demo experiment insert was ignored",
                status_code=500,
            )

        for raw in raw_trials:
            self._record_trial(record, raw, audit=False)
        storage_error = None
        try:
            self._append(
                self._event(
                    "ExperimentSeeded",
                    None,
                    "experiment",
                    "simulated demo experiment seeded",
                    None,
                    {
                        "experiment_id": record["experiment_id"],
                        "n_trials": len(raw_trials),
                        "data_origin": "simulated",
                    },
                )
            )
        except ServiceError as exc:
            storage_error = exc.details
        return {
            **self._experiment_detail(record),
            "duplicate": False,
            "storage_error": storage_error,
        }

    def get_experiment(self, experiment_id: str) -> dict[str, Any]:
        return self._experiment_detail(self._experiment_or_404(experiment_id))

    def _record_trial(
        self,
        experiment: dict[str, Any],
        raw: Any,
        *,
        audit: bool = True,
    ) -> dict[str, Any]:
        try:
            existing = self.store.list_trials(experiment["experiment_id"])
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "trial store read failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        try:
            trial = normalize_trial(raw, experiment, existing)
        except ExperimentDataError as exc:
            raise ServiceError(
                ErrorCode.BAD_REQUEST.value,
                "trial is invalid",
                status_code=400,
                details={"issues": exc.issues},
            ) from exc
        if trial["trajectory_id"]:
            try:
                trajectory = self.store.get_trajectory(trial["trajectory_id"])
            except StorageError as exc:
                raise ServiceError(
                    ErrorCode.STORAGE_WRITE_FAILED.value,
                    "trajectory store read failed",
                    status_code=503,
                    details={"storage_error": str(exc)},
                ) from exc
            if trajectory is None:
                raise ServiceError(
                    ErrorCode.BAD_REQUEST.value,
                    "trial is invalid",
                    status_code=400,
                    details={
                        "issues": [
                            {
                                "path": "trajectory_id",
                                "message": "trajectory dataset does not exist",
                            }
                        ]
                    },
                )
        record = {
            **trial,
            "trial_id": f"trl_{uuid.uuid4().hex[:12]}",
            "experiment_id": experiment["experiment_id"],
            "created_at": utc_now_iso(),
        }
        try:
            inserted = self.store.insert_trial(
                trial_id=record["trial_id"],
                experiment_id=record["experiment_id"],
                created_at=record["created_at"],
                trial=record,
            )
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "trial store write failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        if not inserted:
            raise ServiceError(
                ErrorCode.CONFLICT.value,
                "pair/method or pair/trial_order is already recorded",
                status_code=409,
            )
        if audit:
            self._append(
                self._event(
                    "TrialRecorded",
                    None,
                    "experiment",
                    f"trial recorded: {record['pair_id']} / {record['method']}",
                    record["failure_code"],
                    {
                        "experiment_id": record["experiment_id"],
                        "trial_id": record["trial_id"],
                        "pair_id": record["pair_id"],
                        "method": record["method"],
                        "grasp_success": record["grasp_success"],
                        "failure_code": record["failure_code"],
                        "trajectory_id": record["trajectory_id"],
                        "truth": record["truth"],
                    },
                )
            )
        return record

    def record_trial(self, experiment_id: str, raw: Any) -> dict[str, Any]:
        experiment = self._experiment_or_404(experiment_id)
        return self._record_trial(experiment, raw, audit=True)

    def experiment_analysis(self, experiment_id: str) -> dict[str, Any]:
        experiment = self._experiment_or_404(experiment_id)
        try:
            trials = self.store.list_trials(experiment_id)
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "trial store read failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        return analyze_experiment(experiment, trials)

    def export_experiment_csv(self, experiment_id: str) -> tuple[str, str]:
        self._experiment_or_404(experiment_id)
        try:
            trials = self.store.list_trials(experiment_id)
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "trial store read failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(
            [
                "experiment_id",
                "trial_id",
                "pair_id",
                "trial_order",
                "method",
                "fixture",
                "grasp_success",
                "continuous_error_mm",
                "rotation_error_deg",
                "failure_code",
                "failure_detail",
                "duration_s",
                "measurement_source",
                "trajectory_id",
                "truth",
                "artifacts_json",
                "operator_notes",
                "target_json",
                "created_at",
                "schema_version",
            ]
        )
        for trial in trials:
            writer.writerow(
                [
                    experiment_id,
                    trial["trial_id"],
                    trial["pair_id"],
                    trial["trial_order"],
                    trial["method"],
                    trial["fixture"],
                    1 if trial["grasp_success"] else 0,
                    "" if trial["continuous_error_mm"] is None else trial["continuous_error_mm"],
                    "" if trial["rotation_error_deg"] is None else trial["rotation_error_deg"],
                    trial["failure_code"] or "",
                    trial["failure_detail"],
                    "" if trial["duration_s"] is None else trial["duration_s"],
                    trial["measurement_source"],
                    trial["trajectory_id"] or "",
                    trial["truth"],
                    json.dumps(trial["artifacts"], sort_keys=True),
                    trial["operator_notes"],
                    json.dumps(trial["target"], sort_keys=True),
                    trial["created_at"],
                    trial["schema_version"],
                ]
            )
        return f"armpi-experiment-{experiment_id}.csv", buffer.getvalue()

    def export_experiment_json(self, experiment_id: str) -> dict[str, Any]:
        record = self._experiment_or_404(experiment_id)
        detail = self._experiment_detail(record)
        return {
            "schema_version": 1,
            "exported_at": utc_now_iso(),
            "experiment": detail["experiment"],
            "trials": detail["trials"],
            "analysis": detail["analysis"],
        }
