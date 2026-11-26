#!/usr/bin/env python3
"""No-motion verification of the G7 pose-takeover backend.

Runs against a pose-scope backend connected to a pose-scope gateway and
checks the read-only/validation surface only. It never calls
``/api/motion/execute``, so the arm cannot move.

    python3 tools/studio/verify_takeover.py \
        --backend http://127.0.0.1:8010 \
        --out evidence/studio_g7/20261121/verification.json

It exits non-zero when a check fails and always writes the report.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ARM_JOINTS = ("joint1", "joint2", "joint3", "joint4", "joint5")
GRIPPER = "r_joint"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Client:
    def __init__(self, base: str, timeout: float = 5.0, token: str | None = None) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.token = token
        # Never send a private Ethernet/localhost request through an
        # environment proxy; the Studio and robot are on a direct link.
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({})
        )

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        data = None
        headers = {"Accept": "application/json"}
        if self.token:
            headers["X-Armpi-Token"] = self.token
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base}{path}", data=data, headers=headers, method=method
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8")
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return {"error": {"code": f"HTTP_{exc.code}", "message": payload}}

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, body: Any) -> Any:
        return self._request("POST", path, body)


def _pulses(state: dict[str, Any]) -> dict[str, int]:
    out = {
        joint["name"]: joint["raw_position"]
        for joint in state.get("joints", [])
        if joint.get("raw_position") is not None
    }
    gripper = state.get("gripper")
    if isinstance(gripper, dict) and gripper.get("raw_position") is not None:
        out[GRIPPER] = gripper["raw_position"]
    return out


def _preview(
    client: Client,
    session: str,
    targets: dict[str, float],
    duration: float,
) -> dict[str, Any]:
    return client.post(
        "/api/motion/preview",
        {
            "session_id": session,
            "targets": [
                {"joint": joint, "target_rad": value, "duration_s": duration}
                for joint, value in targets.items()
            ],
        },
    )


def _expected_error(preview: dict[str, Any], codes: set[str]) -> bool:
    found = {issue.get("code") for issue in preview.get("errors", [])}
    return preview.get("valid") is False and bool(found & codes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="http://127.0.0.1:8010")
    parser.add_argument("--session", default="g7-no-motion-verification")
    parser.add_argument(
        "--gateway",
        default="",
        help="optional pose gateway base URL; stores read-only /health, "
        "/motion/status and /state in the report",
    )
    parser.add_argument(
        "--gateway-token",
        default=None,
        help="gateway token; prefer ARMPI_GATEWAY_TOKEN to keep it out of argv",
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    client = Client(args.backend)
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "g7_pose_takeover_no_motion",
        "generated_at": now_iso(),
        "backend": args.backend,
        "gateway": args.gateway or None,
        "checks": [],
        "measured_before": {},
        "measured_after": {},
        "measured_max_delta_pulses": None,
        "dispatched": False,
    }

    def check(check_id: str, ok: bool, detail: Any) -> None:
        report["checks"].append(
            {"check": check_id, "ok": bool(ok), "detail": detail}
        )

    if args.gateway:
        token = args.gateway_token or os.getenv("ARMPI_GATEWAY_TOKEN") or None
        gateway = Client(args.gateway, token=token)
        gw_health = gateway.get("/health")
        gw_status = gateway.get("/motion/status")
        gw_state = gateway.get("/state")
        report["gateway_snapshot"] = {
            "health": gw_health,
            "motion_status": gw_status,
            "measured": gw_state.get("measured", {}),
        }
        check(
            "gateway_pose_mode",
            gw_health.get("motion_enabled") is True
            and gw_health.get("motion_mode") == "pose"
            and gw_health.get("measured_ready") is True,
            {
                "motion_enabled": gw_health.get("motion_enabled"),
                "motion_mode": gw_health.get("motion_mode"),
                "measured_ready": gw_health.get("measured_ready"),
            },
        )
        check(
            "gateway_idle_contract",
            gw_status.get("state") in ("idle", "completed")
            and gw_status.get("mode") == "pose"
            and gw_status.get("max_velocity_rad_s") == 0.5,
            {
                "state": gw_status.get("state"),
                "mode": gw_status.get("mode"),
                "max_velocity_rad_s": gw_status.get("max_velocity_rad_s"),
                "safety": gw_status.get("safety"),
            },
        )
        check(
            "gateway_gripper_contract",
            (gw_status.get("gripper") or {}).get("joint") == GRIPPER
            and GRIPPER in (gw_status.get("measured") or {}),
            {
                "gripper": gw_status.get("gripper"),
                "measured_r_joint": (gw_status.get("measured") or {}).get(GRIPPER),
            },
        )

    health = client.get("/api/health")
    caps = health.get("capabilities", {})
    check(
        "health_pose_scope",
        caps.get("motion_scope") == "pose"
        and caps.get("gripper_control") is True
        and caps.get("pose_gate") is True,
        caps,
    )

    state = client.get("/api/state")
    report["measured_before"] = _pulses(state)
    measured_joints = [
        joint["name"]
        for joint in state.get("joints", [])
        if joint.get("truth") in ("measured", "measured_on_demand", "commanded")
    ]
    check(
        "measured_arm_joints",
        set(measured_joints) == set(ARM_JOINTS),
        measured_joints,
    )
    gripper = state.get("gripper") or {}
    check(
        "measured_gripper",
        gripper.get("joint") == GRIPPER
        and gripper.get("available") is True
        and gripper.get("truth")
        in ("measured", "measured_on_demand", "commanded"),
        {
            "joint": gripper.get("joint"),
            "raw": gripper.get("raw_position"),
            "truth": gripper.get("truth"),
            "age_ms": gripper.get("sample_age_ms"),
        },
    )

    diagnostics = client.get("/api/diagnostics")
    safety = diagnostics.get("safety", {})
    check(
        "safety_ready",
        safety.get("ready") is True and not safety.get("blockers"),
        {"blockers": safety.get("blockers"), "warnings": safety.get("warnings")},
    )
    check(
        "pose_takeover_visible",
        any(
            item.get("check_id") == "pose_takeover" and item.get("passed")
            for item in safety.get("checks", [])
        ),
        [item.get("check_id") for item in safety.get("checks", [])],
    )

    presets_payload = client.get("/api/motion/presets")
    presets = {item["id"]: item for item in presets_payload.get("presets", [])}
    check(
        "presets_available",
        presets_payload.get("available") is True
        and len(presets) >= 4
        and all(item.get("limit_ok") for item in presets.values()),
        {
            "count": len(presets),
            "ids": sorted(presets),
            "gate_error": presets_payload.get("gate_error"),
            "current_tcp_m": presets_payload.get("current", {}).get("tcp_m"),
        },
    )

    vmax = float(caps.get("max_velocity_rad_s") or 0.5)
    min_duration = float(caps.get("min_duration_s") or 1.0)
    max_duration = float(caps.get("max_duration_s") or 10.0)

    def duration_for(target: dict[str, float]) -> float:
        delta = 0.0
        for joint in ARM_JOINTS:
            joint_state = next(
                (
                    item
                    for item in state.get("joints", [])
                    if item["name"] == joint
                ),
                None,
            )
            if joint_state is None or joint_state.get("position_rad") is None:
                continue
            delta = max(delta, abs(target[joint] - joint_state["position_rad"]))
        return min(max_duration, max(min_duration, math.ceil(delta / vmax * 10) / 10))

    grasp = presets.get("grasp_low", {}).get("joints")
    if grasp:
        duration = duration_for(grasp)
        preview = _preview(client, args.session, grasp, duration)
        check(
            "valid_preset_preview",
            preview.get("valid") is True
            and preview.get("preview_token")
            and (preview.get("pose") or {}).get("ok") is True,
            {
                "duration_s": duration,
                "errors": preview.get("errors"),
                "warnings": preview.get("warnings"),
                "pose": preview.get("pose"),
            },
        )
        partial = _preview(
            client, args.session, {"joint1": grasp["joint1"], "joint2": grasp["joint2"]}, duration
        )
        check(
            "partial_joint_set_rejected",
            _expected_error(partial, {"POSE_JOINT_SET"}),
            partial.get("errors"),
        )

        low_yaw = dict(grasp)
        low_yaw["joint1"] = 0.5
        yaw_preview = _preview(client, args.session, low_yaw, duration)
        check(
            "low_pose_yaw_rejected",
            _expected_error(yaw_preview, {"LOW_POSE_YAW"}),
            yaw_preview.get("errors"),
        )

        fast = _preview(client, args.session, grasp, 1.0)
        check(
            "velocity_cap_rejected",
            _expected_error(fast, {"VELOCITY_EXCEEDED"}),
            fast.get("errors"),
        )

    # Choose the farther gripper endpoint from the *command cache* baseline so
    # the speed probe is state-independent (the cache may already be at an
    # endpoint after a previous move).
    cache_raw = gripper.get("raw_position")
    open_raw = int(gripper.get("open_raw", 170))
    close_raw = int(gripper.get("close_raw", 700))
    if (
        isinstance(cache_raw, int)
        and abs(open_raw - cache_raw) >= abs(close_raw - cache_raw)
    ):
        gripper_target = float(gripper.get("min_rad", -2.22))
        gripper_target_raw = open_raw
    else:
        gripper_target = float(gripper.get("max_rad", 0.0))
        gripper_target_raw = close_raw
    gripper_duration = 5.0
    grip = client.post(
        "/api/motion/preview",
        {
            "session_id": args.session,
            "targets": [
                {
                    "joint": GRIPPER,
                    "target_rad": gripper_target,
                    "duration_s": gripper_duration,
                }
            ],
        },
    )
    check(
        "gripper_preview_valid",
        grip.get("valid") is True
        and grip.get("targets", [{}])[0].get("completion") == "timed",
        {
            "errors": grip.get("errors"),
            "targets": grip.get("targets"),
            "cache_raw": cache_raw,
            "target_raw": gripper_target_raw,
        },
    )
    grip_fast = client.post(
        "/api/motion/preview",
        {
            "session_id": args.session,
            "targets": [
                {"joint": GRIPPER, "target_rad": gripper_target, "duration_s": 1.0}
            ],
        },
    )
    check(
        "gripper_speed_rejected",
        _expected_error(grip_fast, {"VELOCITY_EXCEEDED"}),
        grip_fast.get("errors"),
    )
    grip_out = client.post(
        "/api/motion/preview",
        {
            "session_id": args.session,
            "targets": [{"joint": GRIPPER, "target_rad": 0.5, "duration_s": 5.0}],
        },
    )
    check(
        "gripper_range_rejected",
        _expected_error(grip_out, {"GRIPPER_RANGE"}),
        grip_out.get("errors"),
    )

    after = client.get("/api/state")
    after_pulses = _pulses(after)
    report["measured_after"] = after_pulses
    before = report["measured_before"]
    deltas: dict[str, int | None] = {}
    for joint in set(before) | set(after_pulses):
        left = after_pulses.get(joint)
        right = before.get(joint)
        deltas[joint] = None if left is None or right is None else abs(left - right)
    numeric = [value for value in deltas.values() if value is not None]
    max_delta = max(numeric) if numeric else None
    report["measured_max_delta_pulses"] = max_delta
    check(
        "no_motion_dispatched",
        max_delta == 0 and None not in deltas.values(),
        {"deltas": deltas, "note": "no /api/motion/execute call was made"},
    )

    report["ok"] = all(item["ok"] for item in report["checks"])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for item in report["checks"]:
        print(("PASS " if item["ok"] else "FAIL ") + item["check"])
    print(f"report: {out}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
