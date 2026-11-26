#!/usr/bin/env python3
"""Run the first G7 pose-takeover motion on the real robot, staged and audited.

The sequence is fixed and uses the presets served by the backend:

    view_table (1.5 s) -> grasp_low (2.5 s) -> view_table (2.5 s)
    -> gripper open (5 s) -> gripper close (5 s)

Every step is previewed and its FK gate checked before dispatch. A step that is
rejected, fails, or reports ``Unknown`` aborts the whole sequence; nothing is
retried. The script never invents joint values: it uses ``/api/motion/presets``
and the measured gripper range.

Without ``--execute`` it only previews the sequence (no lease, no motion).

    python3 tools/studio/g7_first_motion.py \
        --backend http://127.0.0.1:8000 \
        --session operator-onsite-20261121 \
        --out evidence/studio_g7/20261121/first_motion.json \
        --execute --i-have-clear-workspace
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ARM_JOINTS = ("joint1", "joint2", "joint3", "joint4", "joint5")
GRIPPER = "r_joint"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Client:
    def __init__(self, base: str, timeout: float = 8.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({})
        )

    def request(self, method: str, path: str, body: Any = None, headers=None) -> Any:
        data = None
        all_headers = {"Accept": "application/json"}
        if headers:
            all_headers.update(headers)
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            all_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base}{path}", data=data, headers=all_headers, method=method
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
        return self.request("GET", path)

    def post(self, path: str, body: Any, headers=None) -> Any:
        return self.request("POST", path, body, headers)


def terminal_stage(events: list[dict[str, Any]]) -> str | None:
    stages = {event.get("stage") for event in events}
    for stage in ("Unknown", "Failed", "Completed"):
        if stage in stages:
            return stage
    return None


def measured_pulses(state: dict[str, Any]) -> dict[str, int]:
    pulses = {
        joint["name"]: joint["raw_position"]
        for joint in state.get("joints", [])
        if joint.get("raw_position") is not None
    }
    gripper = state.get("gripper")
    if isinstance(gripper, dict) and gripper.get("raw_position") is not None:
        pulses[GRIPPER] = gripper["raw_position"]
    return pulses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="http://127.0.0.1:8000")
    parser.add_argument("--session", default="operator-onsite")
    parser.add_argument("--lease-ttl", type=int, default=300)
    parser.add_argument("--out", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--i-have-clear-workspace", action="store_true")
    parser.add_argument(
        "--steps",
        default="",
        help="comma-separated subset of view_table,grasp_low,view_table_return,"
        "gripper_open,gripper_close (default: all)",
    )
    parser.add_argument("--poll-s", type=float, default=0.2)
    args = parser.parse_args(argv)

    if args.execute and not args.i_have_clear_workspace:
        raise SystemExit(
            "--execute needs --i-have-clear-workspace; support the arm or clear "
            "the workspace first"
        )

    client = Client(args.backend)
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "g7_first_motion",
        "generated_at": now_iso(),
        "backend": args.backend,
        "execute": bool(args.execute),
        "session": args.session,
        "steps": [],
        "ok": False,
        "error": None,
        "measured_start": {},
        "measured_end": {},
    }
    lease: dict[str, Any] | None = None
    out = Path(args.out)

    def save() -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    try:
        health = client.get("/api/health")
        caps = health.get("capabilities", {})
        if caps.get("motion_scope") != "pose":
            raise SystemExit(f"backend is not in pose scope: {caps}")
        state = client.get("/api/state")
        report["measured_start"] = measured_pulses(state)
        print("起始实测脉冲:", report["measured_start"])

        presets_payload = client.get("/api/motion/presets")
        presets = {item["id"]: item for item in presets_payload.get("presets", [])}
        view = presets.get("view_table")
        grasp = presets.get("grasp_low")
        if view is None or grasp is None:
            raise SystemExit("view_table/grasp_low presets are missing")
        gripper = state.get("gripper") or {}
        if not gripper.get("available"):
            raise SystemExit("gripper telemetry is unavailable")

        steps: list[dict[str, Any]] = [
            {"label": "view_table", "kind": "pose", "joints": view["joints"], "duration_s": 1.5},
            {"label": "grasp_low", "kind": "pose", "joints": grasp["joints"], "duration_s": 2.5},
            {"label": "view_table_return", "kind": "pose", "joints": view["joints"], "duration_s": 2.5},
            {"label": "gripper_open", "kind": "grip", "target_rad": float(gripper["min_rad"]), "duration_s": 5.0},
            {"label": "gripper_close", "kind": "grip", "target_rad": float(gripper["max_rad"]), "duration_s": 5.0},
        ]
        if args.steps:
            wanted = {item.strip() for item in args.steps.split(",") if item.strip()}
            unknown = wanted - {step["label"] for step in steps}
            if unknown:
                raise SystemExit(f"unknown steps: {sorted(unknown)}")
            steps = [step for step in steps if step["label"] in wanted]

        if args.execute:
            lease_response = client.post(
                "/api/control/acquire",
                {"session_id": args.session, "ttl_s": args.lease_ttl},
            )
            if lease_response.get("lease_id") is None:
                raise SystemExit(f"lease acquire failed: {lease_response}")
            lease = lease_response
            print(f"控制权租约: {lease['lease_id']} ({lease.get('ttl_s')} s)")

        for step in steps:
            label = step["label"]
            print(f"\n== {label}")
            if step["kind"] == "pose":
                targets = [
                    {
                        "joint": joint,
                        "target_rad": float(step["joints"][joint]),
                        "duration_s": float(step["duration_s"]),
                    }
                    for joint in ARM_JOINTS
                ]
            else:
                targets = [
                    {
                        "joint": GRIPPER,
                        "target_rad": float(step["target_rad"]),
                        "duration_s": float(step["duration_s"]),
                    }
                ]

            preview = client.post(
                "/api/motion/preview",
                {"session_id": args.session, "targets": targets},
            )
            record: dict[str, Any] = {
                "label": label,
                "kind": step["kind"],
                "preview": preview,
                "executed": False,
                "stage": None,
                "events": [],
                "measured_after": {},
            }
            report["steps"].append(record)
            print(
                "  preview:",
                "VALID" if preview.get("valid") else "REJECTED",
                [issue.get("code") for issue in preview.get("errors", [])],
            )
            pose_check = preview.get("pose")
            if pose_check:
                print(
                    f"  pose gate: TCP={pose_check.get('tcp_m')} "
                    f"clearance={pose_check.get('table_clearance_m')} "
                    f"samples={pose_check.get('samples')}"
                )
            if not preview.get("valid") or not preview.get("preview_token"):
                raise RuntimeError(f"{label}: preview rejected")

            if not args.execute:
                continue

            execute = client.post(
                "/api/motion/execute",
                {
                    "session_id": args.session,
                    "lease_id": lease["lease_id"],
                    "preview_token": preview["preview_token"],
                    "command_id": preview["command_id"],
                    "targets": [
                        {
                            "joint": item["joint"],
                            "target_rad": item["target_rad"],
                            "duration_s": item["duration_s"],
                        }
                        for item in preview["targets"]
                    ],
                },
                headers={"Idempotency-Key": uuid.uuid4().hex},
            )
            record["execute"] = execute
            record["executed"] = True
            print("  dispatch:", execute.get("status"), execute.get("error_code"))
            if execute.get("status") not in ("Dispatched", "Completed"):
                raise RuntimeError(f"{label}: execute returned {execute}")

            duration = max(float(item["duration_s"]) for item in preview["targets"])
            deadline = time.monotonic() + duration + 12.0
            while time.monotonic() < deadline:
                events_payload = client.get(
                    f"/api/events?command_id={preview['command_id']}&limit=100"
                )
                events = sorted(
                    events_payload.get("items", []),
                    key=lambda event: event.get("seq", 0),
                )
                record["events"] = events
                stage = terminal_stage(events)
                if stage is not None:
                    record["stage"] = stage
                    break
                time.sleep(args.poll_s)
            if record["stage"] is None:
                raise RuntimeError(f"{label}: no terminal stage before the deadline")
            if record["stage"] != "Completed":
                raise RuntimeError(f"{label}: stage {record['stage']} (aborting)")

            time.sleep(0.3)  # let the measured stream settle
            after = client.get("/api/state")
            record["measured_after"] = measured_pulses(after)
            print("  completed; measured:", record["measured_after"])

        report["ok"] = True
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - report and fail loudly
        report["error"] = str(exc)
        print("\nABORT:", exc)
    finally:
        if lease is not None:
            try:
                client.post(
                    "/api/control/release",
                    {
                        "lease_id": lease["lease_id"],
                        "session_id": args.session,
                    },
                )
            except Exception:  # noqa: BLE001 - release is best effort
                pass
        try:
            report["measured_end"] = measured_pulses(client.get("/api/state"))
        except Exception:  # noqa: BLE001
            pass
        save()

    print(f"\nreport: {out}")
    print("result:", "PASS" if report["ok"] else "FAIL")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
