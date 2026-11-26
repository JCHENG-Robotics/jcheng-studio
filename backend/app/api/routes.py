"""REST and WebSocket routes.

The API never exposes a serial port or arbitrary ROS call. It exposes named
business actions with structured validation results.
"""

from __future__ import annotations

import asyncio
import hmac
from typing import Any

from fastapi import (
    APIRouter,
    Body,
    Header,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ..domain.models import MotionTarget
from ..domain.profile import ProfileParams
from ..errors import ServiceError
from ..service import RobotService

router = APIRouter(prefix="/api")


def _ws_authorized(websocket: WebSocket) -> bool:
    """Check the optional shared token for a WebSocket handshake."""
    token = websocket.app.state.settings.api_token
    if not token:
        return True
    supplied = websocket.query_params.get("token") or (
        websocket.headers.get("x-armpi-token") or ""
    )
    return hmac.compare_digest(supplied, token)


class TargetIn(BaseModel):
    joint: str = Field(min_length=1, max_length=64)
    target_rad: float
    duration_s: float


class ProfileIn(BaseModel):
    """The fast task path: one dense S-curve instead of a single point."""

    vmax_rad_s: float = Field(gt=0, le=4.0)
    amax_rad_s2: float = Field(gt=0, le=20.0)
    dt_s: float = Field(default=0.05, ge=0.025, le=0.5)


class PreviewIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    targets: list[TargetIn] = Field(min_length=1, max_length=12)
    profile: ProfileIn | None = None
    fast: bool = False


class ExecuteIn(PreviewIn):
    lease_id: str = Field(min_length=1, max_length=128)
    preview_token: str = Field(min_length=1, max_length=256)
    command_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=256)


class LeaseIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    ttl_s: int | None = Field(default=None, ge=1, le=3600)


class HeartbeatIn(LeaseIn):
    lease_id: str = Field(min_length=1, max_length=128)


class ReleaseIn(BaseModel):
    lease_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)


class StopIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    lease_id: str | None = Field(default=None, max_length=128)
    command_id: str | None = Field(default=None, max_length=128)


class FaultIn(BaseModel):
    kind: str = Field(min_length=1, max_length=64)
    joint: str | None = Field(default=None, max_length=64)


class CameraReferenceIn(BaseModel):
    note: str = Field(default="", max_length=500)


def _service(request: Request) -> RobotService:
    return request.app.state.service


def _targets(items: list[TargetIn]) -> list[MotionTarget]:
    return [
        MotionTarget(joint=item.joint, target_rad=item.target_rad, duration_s=item.duration_s)
        for item in items
    ]


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    return _service(request).health()


@router.get("/state")
def state(request: Request) -> dict[str, Any]:
    return _service(request).get_state()


@router.get("/connection")
def connection(request: Request) -> dict[str, Any]:
    return _service(request).connection_report()


@router.get("/peripherals")
def peripherals(request: Request) -> dict[str, Any]:
    return _service(request).peripherals()


@router.get("/peripherals/camera/snapshot")
def camera_snapshot(request: Request) -> Response:
    content, media_type = _service(request).camera_snapshot()
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/peripherals/aux-camera/snapshot")
def aux_camera_snapshot(request: Request) -> Response:
    content, media_type = _service(request).aux_camera_snapshot()
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/safety")
def safety(
    request: Request,
    lease_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    require_lease: bool = Query(default=False),
) -> dict[str, Any]:
    return _service(request).safety_report(
        lease_id=lease_id, session_id=session_id, require_lease=require_lease
    )


@router.get("/diagnostics")
def diagnostics(request: Request) -> dict[str, Any]:
    return _service(request).diagnostics()


@router.post("/diagnostics/probe")
def probe(request: Request) -> dict[str, Any]:
    return _service(request).probe()


@router.post("/control/acquire")
def acquire(request: Request, body: LeaseIn) -> dict[str, Any]:
    return _service(request).acquire_lease(body.session_id, body.ttl_s)


@router.post("/control/heartbeat")
def heartbeat(request: Request, body: HeartbeatIn) -> dict[str, Any]:
    return _service(request).heartbeat(body.lease_id, body.session_id, body.ttl_s)


@router.post("/control/release")
def release(request: Request, body: ReleaseIn) -> dict[str, Any]:
    return _service(request).release_lease(body.lease_id, body.session_id)


@router.get("/motion/presets")
def motion_presets(request: Request) -> dict[str, Any]:
    return _service(request).motion_presets()


class TaskVerifyIn(BaseModel):
    plan_id: str | None = Field(default=None, max_length=128)


class TaskPlanIn(BaseModel):
    form: str | None = Field(default=None, max_length=64)


@router.post("/task/plan")
def task_plan(
    request: Request,
    body: TaskPlanIn | None = None,
) -> dict[str, Any]:
    return _service(request).task_plan(body.form if body else None)


@router.post("/task/verify")
def task_verify(
    request: Request,
    body: TaskVerifyIn | None = None,
) -> dict[str, Any]:
    return _service(request).task_verify(body.plan_id if body else None)


def _profile(value: ProfileIn | None) -> ProfileParams | None:
    if value is None:
        return None
    return ProfileParams(
        vmax_rad_s=value.vmax_rad_s,
        amax_rad_s2=value.amax_rad_s2,
        dt_s=value.dt_s,
    )


@router.post("/motion/preview")
def preview(request: Request, body: PreviewIn) -> dict[str, Any]:
    return _service(request).preview(
        body.session_id,
        _targets(body.targets),
        profile=_profile(body.profile),
        fast=body.fast,
    )


@router.post("/motion/execute")
def execute(
    request: Request,
    body: ExecuteIn,
    idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    key = body.idempotency_key or idempotency_header
    if not key:
        raise ServiceError(
            "BAD_REQUEST",
            "Idempotency-Key header or body.idempotency_key is required",
            status_code=400,
        )
    return _service(request).execute(
        session_id=body.session_id,
        lease_id=body.lease_id,
        preview_token=body.preview_token,
        command_id=body.command_id,
        targets=_targets(body.targets),
        idempotency_key=key,
        profile=_profile(body.profile),
        fast=body.fast,
    )


class ChassisPulseIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    lease_id: str = Field(min_length=1, max_length=128)
    vx: float
    vy: float
    duty: float
    duration_s: float


@router.post("/chassis/pulse")
def chassis_pulse(request: Request, body: ChassisPulseIn) -> dict[str, Any]:
    return _service(request).chassis_pulse(
        session_id=body.session_id,
        lease_id=body.lease_id,
        vx=body.vx,
        vy=body.vy,
        duty=body.duty,
        duration_s=body.duration_s,
    )


@router.post("/motion/stop")
def stop(request: Request, body: StopIn) -> dict[str, Any]:
    return _service(request).stop(
        session_id=body.session_id,
        lease_id=body.lease_id,
        command_id=body.command_id,
    )


@router.get("/events")
def events(
    request: Request,
    limit: int = Query(default=200, ge=1, le=2000),
    stage: str | None = Query(default=None),
    source: str | None = Query(default=None),
    command_id: str | None = Query(default=None),
    after_seq: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return _service(request).events(
        limit=limit,
        stage=stage,
        source=source,
        command_id=command_id,
        after_seq=after_seq,
    )


@router.get("/events/export.csv", response_class=PlainTextResponse)
def export_events(request: Request) -> PlainTextResponse:
    csv_text = _service(request).export_csv()
    return PlainTextResponse(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="armpi-events.csv"'},
    )


@router.post("/mock/faults")
def inject_fault(request: Request, body: FaultIn) -> dict[str, Any]:
    return _service(request).inject_fault(body.kind, body.joint)


@router.post("/mock/reset")
def reset_mock(request: Request) -> dict[str, Any]:
    return _service(request).reset_mock()


# --------------------------------------------------------------- trajectories
# G2 offline data paths. There is deliberately no endpoint that turns a stored
# trajectory into a motion command: replay is client-side playback only.


@router.get("/trajectories")
def list_trajectories(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    return _service(request).list_trajectories(limit)


@router.post("/trajectories")
def import_trajectory(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return _service(request).import_trajectory(body, source="import")


@router.post("/trajectories/demo")
def import_demo_trajectory(request: Request) -> dict[str, Any]:
    return _service(request).import_demo_trajectory()


@router.get("/trajectories/{trajectory_id}")
def get_trajectory(request: Request, trajectory_id: str) -> dict[str, Any]:
    return _service(request).get_trajectory(trajectory_id)


@router.post("/trajectories/{trajectory_id}/recompute")
def recompute_trajectory(request: Request, trajectory_id: str) -> dict[str, Any]:
    return _service(request).recompute_trajectory(trajectory_id)


@router.get("/trajectories/{trajectory_id}/export.csv", response_class=PlainTextResponse)
def export_trajectory_csv(
    request: Request,
    trajectory_id: str,
    sample: str = Query(default="feedback", pattern="^(feedback|commanded)$"),
) -> PlainTextResponse:
    filename, text = _service(request).export_trajectory_csv(trajectory_id, sample)
    return PlainTextResponse(
        content=text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/trajectories/{trajectory_id}/export.json")
def export_trajectory_json(request: Request, trajectory_id: str) -> JSONResponse:
    record = _service(request).export_trajectory_json(trajectory_id)
    return JSONResponse(
        content=record,
        headers={
            "Content-Disposition": (
                f'attachment; filename="armpi-trajectory-{trajectory_id}.json"'
            )
        },
    )


# ---------------------------------------------------------------- experiments
# G3 records experiment definitions and append-only trials. There is no
# experiment endpoint that executes motion or touches the robot adapter.


@router.get("/experiments")
def list_experiments(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    return _service(request).list_experiments(limit)


@router.post("/experiments")
def create_experiment(
    request: Request, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    return _service(request).create_experiment(body)


@router.post("/experiments/demo")
def import_demo_experiment(request: Request) -> dict[str, Any]:
    return _service(request).import_demo_experiment()


@router.get("/experiments/{experiment_id}")
def get_experiment(request: Request, experiment_id: str) -> dict[str, Any]:
    return _service(request).get_experiment(experiment_id)


@router.post("/experiments/{experiment_id}/trials")
def record_trial(
    request: Request,
    experiment_id: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    return _service(request).record_trial(experiment_id, body)


@router.get("/experiments/{experiment_id}/analysis")
def experiment_analysis(request: Request, experiment_id: str) -> dict[str, Any]:
    return _service(request).experiment_analysis(experiment_id)


@router.get("/experiments/{experiment_id}/export.csv", response_class=PlainTextResponse)
def export_experiment_csv(request: Request, experiment_id: str) -> PlainTextResponse:
    filename, text = _service(request).export_experiment_csv(experiment_id)
    return PlainTextResponse(
        content=text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/experiments/{experiment_id}/export.json")
def export_experiment_json(request: Request, experiment_id: str) -> JSONResponse:
    record = _service(request).export_experiment_json(experiment_id)
    return JSONResponse(
        content=record,
        headers={
            "Content-Disposition": (
                f'attachment; filename="armpi-experiment-{experiment_id}.json"'
            )
        },
    )


# ------------------------------------------------------------------- visual
# G4 is read-only: URDF FK, camera metadata and reference bookkeeping. No
# endpoint here can move the arm or acquire a control lease.


@router.get("/visual")
def visual_snapshot(request: Request) -> dict[str, Any]:
    return _service(request).visual_snapshot()


@router.get("/visual/camera-references")
def list_camera_references(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    return _service(request).list_camera_references(limit)


@router.post("/visual/camera-references")
def save_camera_reference(
    request: Request,
    body: CameraReferenceIn,
) -> dict[str, Any]:
    return _service(request).save_camera_reference(body.note)


@router.websocket("/ws/state")
async def ws_state(websocket: WebSocket) -> None:
    if not _ws_authorized(websocket):
        await websocket.close(code=1008)
        return
    service: RobotService = websocket.app.state.service
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(service.get_state())
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        return
    except Exception:
        with_suppress = getattr(websocket, "close", None)
        if with_suppress is not None:
            try:
                await websocket.close()
            except Exception:
                pass


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    if not _ws_authorized(websocket):
        await websocket.close(code=1008)
        return
    service: RobotService = websocket.app.state.service
    try:
        after = int(websocket.query_params.get("after", "0"))
    except ValueError:
        after = 0
    await websocket.accept()
    try:
        while True:
            payload = service.events(after_seq=after, limit=100)
            items = sorted(payload["items"], key=lambda item: item["seq"])
            for item in items:
                await websocket.send_json(item)
                after = max(after, int(item["seq"]))
            await asyncio.sleep(0.25)
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass
