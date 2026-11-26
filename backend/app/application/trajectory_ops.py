"""G2 trajectory import/export operations, split out of RobotService.

The methods are moved verbatim as a structural boundary; they still use the
service facade for the store, settings, lock and event append.
"""

from __future__ import annotations

import uuid
from typing import Any

from ..domain.enums import ErrorCode
from ..domain.models import utc_now_iso
from ..domain.trajectory import (
    TrajectoryDataError,
    alignment_csv,
    analyze,
    build_demo_dataset,
    prepare_dataset,
    summary,
)
from ..errors import ServiceError, StorageError


class TrajectoryOperations:
    # -------------------------------------------------------- trajectories (G2)
    def list_trajectories(self, limit: int = 50) -> dict[str, Any]:
        try:
            items = self.store.list_trajectories(limit)
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "trajectory store read failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        return {"items": items, "schema_version": 1}

    def import_trajectory(
        self, raw: Any, source: str = "import"
    ) -> dict[str, Any]:
        """Validate, analyze, and store an offline dataset.

        Duplicate datasets (same canonical SHA-256) return the existing record.
        This path never calls the adapter and never dispatches motion.
        """
        try:
            dataset, analysis, digest = prepare_dataset(raw)
        except TrajectoryDataError as exc:
            raise ServiceError(
                ErrorCode.BAD_REQUEST.value,
                "trajectory dataset is invalid",
                status_code=400,
                details={"issues": exc.issues},
            ) from exc

        try:
            existing = self.store.find_trajectory_by_hash(digest)
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "trajectory store read failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        if existing is not None:
            return {**existing, "duplicate": True, "storage_error": None}

        record: dict[str, Any] = {
            "trajectory_id": f"trj_{uuid.uuid4().hex[:12]}",
            "created_at": utc_now_iso(),
            "name": dataset["name"],
            "source": source,
            "schema_version": dataset["schema_version"],
            "dataset_sha256": digest,
            "analysis_version": analysis["analysis_version"],
            "dataset": dataset,
            "analysis": analysis,
        }
        summary_fields = summary(record)
        try:
            inserted = self.store.insert_trajectory(
                trajectory_id=record["trajectory_id"],
                created_at=record["created_at"],
                name=record["name"],
                source=record["source"],
                schema_version=record["schema_version"],
                dataset_sha256=digest,
                analysis_version=record["analysis_version"],
                dataset=dataset,
                analysis=analysis,
                summary=summary_fields,
            )
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "trajectory store write failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        if not inserted:
            stored = self.store.find_trajectory_by_hash(digest)
            if stored is not None:
                return {**stored, "duplicate": True, "storage_error": None}
            raise ServiceError(
                ErrorCode.INTERNAL_ERROR.value,
                "trajectory insert was ignored without a matching stored record",
                status_code=500,
            )

        storage_error = None
        try:
            self._append(
                self._event(
                    "TrajectoryImported",
                    None,
                    "trajectory",
                    f"offline dataset stored: {record['name']}",
                    None,
                    {
                        "trajectory_id": record["trajectory_id"],
                        "dataset_sha256": digest,
                        "source": source,
                        "feedback_truth": dataset["feedback"]["truth"],
                        "tracking_available": summary_fields["tracking_available"],
                    },
                )
            )
        except ServiceError as exc:
            storage_error = exc.details
        return {
            **record,
            "duplicate": False,
            "storage_error": storage_error,
        }

    def import_demo_trajectory(self) -> dict[str, Any]:
        return self.import_trajectory(build_demo_dataset(), source="demo")

    def _trajectory_or_404(self, trajectory_id: str) -> dict[str, Any]:
        try:
            record = self.store.get_trajectory(trajectory_id)
        except StorageError as exc:
            raise ServiceError(
                ErrorCode.STORAGE_WRITE_FAILED.value,
                "trajectory store read failed",
                status_code=503,
                details={"storage_error": str(exc)},
            ) from exc
        if record is None:
            raise ServiceError(
                ErrorCode.NOT_FOUND.value,
                f"trajectory {trajectory_id!r} does not exist",
                status_code=404,
            )
        return record

    def get_trajectory(self, trajectory_id: str) -> dict[str, Any]:
        return self._trajectory_or_404(trajectory_id)

    def recompute_trajectory(self, trajectory_id: str) -> dict[str, Any]:
        record = self._trajectory_or_404(trajectory_id)
        fresh = analyze(record["dataset"])
        return {
            "trajectory_id": trajectory_id,
            "analysis_version": fresh["analysis_version"],
            "stored_analysis_version": record["analysis_version"],
            "matches_stored": fresh == record["analysis"],
            "analysis": fresh,
        }

    def export_trajectory_csv(
        self, trajectory_id: str, sample: str = "feedback"
    ) -> tuple[str, str]:
        record = self._trajectory_or_404(trajectory_id)
        try:
            text = alignment_csv(record["dataset"], sample)
        except ValueError as exc:
            raise ServiceError(
                ErrorCode.BAD_REQUEST.value, str(exc), status_code=400
            ) from exc
        return f"armpi-trajectory-{trajectory_id}-{sample}.csv", text

    def export_trajectory_json(self, trajectory_id: str) -> dict[str, Any]:
        return self._trajectory_or_404(trajectory_id)
