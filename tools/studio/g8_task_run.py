#!/usr/bin/env python3
"""Run one G8 task sequence through the Studio API, with bus-verified steps.

The plan is produced by ``POST /api/task/plan`` (locate + deep-family fit +
FK/limit validation). A cube outside the grasp band gets the base approach
inside the same run: pulse, re-plan, repeat until the plan is valid. Each
pose command is one dense S-curve (the gateway validates every segment) and
each grip command is one timed bus-servo command; both go through
``/api/motion/preview`` and ``/api/motion/execute``, so the gateway's bus-servo
verification applies. A verify step checks the cube box at the view.
Any rejected preview, `Failed` or `Unknown` aborts the run; nothing is retried.

    # plan only (read-only, prints the located point and phases)
    python3 tools/studio/g8_task_run.py --backend http://127.0.0.1:8000 \
        --out /tmp/g8_plan.json

    # run it (operator beside the robot; clear workspace; power reachable)
    python3 tools/studio/g8_task_run.py --backend http://127.0.0.1:8000 \
        --session operator-g8 --out /tmp/g8_run.json --execute \
        --i-have-clear-workspace
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Client:
    def __init__(self, base: str, timeout: float = 25.0) -> None:
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


def terminal_event(client: Client, command_id: str, duration: float) -> dict[str, Any] | None:
    deadline = time.monotonic() + duration + 15.0
    while time.monotonic() < deadline:
        payload = client.request(
            "GET", f"/api/events?command_id={command_id}&limit=100"
        )
        items = sorted(payload.get("items", []), key=lambda e: e.get("seq", 0))
        for event in items:
            if event.get("stage") in ("Completed", "Failed", "Unknown"):
                return event
        time.sleep(0.3)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="http://127.0.0.1:8000")
    parser.add_argument("--session", default="operator-g8")
    parser.add_argument("--lease-ttl", type=int, default=600)
    parser.add_argument("--out", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--i-have-clear-workspace", action="store_true")
    parser.add_argument(
        "--form",
        default=None,
        help=(
            "task form id from task.forms in the takeover contract "
            "(default: cube_39, the 39.4 mm cube)"
        ),
    )
    args = parser.parse_args(argv)
    if args.execute and not args.i_have_clear_workspace:
        raise SystemExit("--execute needs --i-have-clear-workspace")

    client = Client(args.backend)
    plan_body = {"form": args.form}
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "g8_task_run",
        "generated_at": now_iso(),
        "execute": bool(args.execute),
        "plan": None,
        "steps": [],
        "approach_pulses": [],
        "verify": None,
        "ok": False,
        "error": None,
    }
    out = Path(args.out)
    lease: dict[str, Any] | None = None

    def save() -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    try:
        plan = client.request("POST", "/api/task/plan", plan_body)
        report["plan"] = plan
        if "error" in plan:
            raise RuntimeError(json.dumps(plan["error"]))
        print("规划:", "VALID" if plan.get("valid") else "REJECTED", "simulated", plan.get("simulated"))
        print("  形态", plan.get("form_id"), "目标点", plan.get("point"), "修正后目标", plan.get("target"))
        if plan.get("fit"):
            print(
                "  拟合 j1..j4",
                [round(v, 4) for v in plan["fit"]["joints"][:4]],
                "xy 误差 %.2f mm" % plan["fit"]["xy_error_mm"],
            )
        for issue in plan.get("errors", []):
            print("  ERROR", issue.get("code"), issue.get("message"))
        approach_codes = [issue.get("code") for issue in plan.get("errors", [])]
        approach_required = (
            not plan.get("valid")
            and "APPROACH_REQUIRED" in approach_codes
            and plan.get("approach")
        )
        if not plan.get("valid") and not approach_required:
            raise RuntimeError("the task plan is not valid")
        print(
            "  阶段",
            len(plan.get("steps", [])),
            "曲线",
            (plan.get("fast") or {}).get("mode", "?"),
        )
        if not args.execute:
            if approach_required:
                print("  运行时会先底盘接近:", plan["approach"])
            report["ok"] = True
            return 0

        lease_response = client.request(
            "POST",
            "/api/control/acquire",
            {"session_id": args.session, "ttl_s": args.lease_ttl},
        )
        if lease_response.get("lease_id") is None:
            raise RuntimeError(f"lease acquire failed: {lease_response}")
        lease = lease_response
        print("控制权租约:", lease["lease_id"])

        # One run does the base approach too: pulse, re-locate, repeat until
        # the plan is valid, then run the arm phases.
        if approach_required:
            presets = client.request("GET", "/api/motion/presets")
            contract = (presets.get("contract") or {}) if presets else {}
            approach_contract = (
                (contract.get("task") or {}).get("approach") or {}
            )
            max_pulses = int(approach_contract.get("max_pulses", 3))
            settle = max(0.6, float(approach_contract.get("settle_s", 0.5)))
            pulses: list[dict[str, Any]] = []
            report["approach_pulses"] = pulses
            for attempt in range(max_pulses):
                approach = plan.get("approach")
                if not approach:
                    break
                print(
                    "\n== 底盘接近 {}/{}: vx={:.3f} vy={:.3f} duty={} {:.2f} s "
                    "({:.0f} mm nominal)".format(
                        attempt + 1,
                        max_pulses,
                        approach["vx"],
                        approach["vy"],
                        approach["duty"],
                        approach["duration_s"],
                        approach["nominal_travel_mm"],
                    )
                )
                pulse = client.request(
                    "POST",
                    "/api/chassis/pulse",
                    {
                        "session_id": args.session,
                        "lease_id": lease["lease_id"],
                        "vx": approach["vx"],
                        "vy": approach["vy"],
                        "duty": approach["duty"],
                        "duration_s": approach["duration_s"],
                    },
                )
                print("  pulse:", pulse.get("status"), pulse.get("verification"))
                record = {"approach": approach, "response": pulse}
                pulses.append(record)
                if pulse.get("status") != "Completed":
                    raise RuntimeError(f"chassis pulse returned {pulse}")
                time.sleep(settle)
                plan = client.request("POST", "/api/task/plan", plan_body)
                report["plan"] = plan
                print(
                    "  重新规划:",
                    "VALID" if plan.get("valid") else
                    [issue.get("code") for issue in plan.get("errors", [])],
                )
                if plan.get("valid"):
                    break
                if not plan.get("approach"):
                    raise RuntimeError("the base approach did not yield a valid plan")
            if not plan.get("valid"):
                raise RuntimeError("the base approach did not get the cube into the band")

        for index, step in enumerate(plan["steps"]):
            label = step.get("label")
            kind = step.get("kind")
            print(f"\n== [{index + 1}/{len(plan['steps'])}] {label} ({kind})")
            record: dict[str, Any] = {
                "index": index,
                "label": label,
                "kind": kind,
                "commands": [],
            }
            report["steps"].append(record)
            if kind == "verify":
                verify = client.request(
                    "POST", "/api/task/verify", {"plan_id": plan.get("plan_id")}
                )
                report["verify"] = verify
                record["verify"] = verify
                print(
                    "  夹持:", verify.get("held"),
                    "clusters", verify.get("clusters"),
                    "code", verify.get("code"),
                )
                if not verify.get("held"):
                    raise RuntimeError(
                        "grip verify failed: " + str(verify.get("message") or verify)
                    )
                continue
            for command in step.get("commands") or []:
                command_label = command.get("label")
                targets = command.get("targets") or []
                body: dict[str, Any] = {
                    "session_id": args.session,
                    "targets": targets,
                    "profile": command.get("profile"),
                    "fast": bool(command.get("fast", False)),
                }
                preview = client.request("POST", "/api/motion/preview", body)
                command_record: dict[str, Any] = {
                    "label": command_label,
                    "profile": command.get("profile"),
                    "preview": preview,
                }
                record["commands"].append(command_record)
                if not preview.get("valid") or not preview.get("preview_token"):
                    print(
                        "  preview REJECTED",
                        [issue.get("code") for issue in preview.get("errors", [])],
                    )
                    raise RuntimeError(f"{label}/{command_label}: preview rejected")
                execute = client.request(
                    "POST",
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
                        "profile": command.get("profile"),
                        "fast": bool(command.get("fast", False)),
                    },
                    headers={"Idempotency-Key": uuid.uuid4().hex},
                )
                command_record["execute"] = execute
                if execute.get("status") not in ("Dispatched", "Completed"):
                    raise RuntimeError(
                        f"{label}/{command_label}: execute returned {execute}"
                    )
                duration = max(
                    float(item["duration_s"]) for item in preview["targets"]
                )
                event = terminal_event(client, preview["command_id"], duration)
                command_record["terminal"] = event
                if event is None:
                    raise RuntimeError(f"{label}/{command_label}: no terminal event")
                if event.get("stage") != "Completed":
                    raise RuntimeError(
                        f"{label}/{command_label}: {event.get('stage')} "
                        f"{event.get('error_code')}"
                    )
                details = event.get("details") or {}
                actual = {
                    joint: item.get("raw_position")
                    for joint, item in (details.get("actual") or {}).items()
                }
                deviations = details.get("deviations_pulses") or {}
                print(
                    "  {} Completed".format(command_label),
                    "actual", actual,
                    "dev", {k: v for k, v in deviations.items()},
                )
                heartbeat = client.request(
                    "POST",
                    "/api/control/heartbeat",
                    {"lease_id": lease["lease_id"], "session_id": args.session},
                )
                if heartbeat.get("lease_id"):
                    lease = heartbeat
        report["ok"] = True
        return 0
    except Exception as exc:  # noqa: BLE001 - report and fail loudly
        report["error"] = str(exc)
        print("\nABORT:", exc)
        return 1
    finally:
        if lease is not None:
            try:
                client.request(
                    "POST",
                    "/api/control/release",
                    {"lease_id": lease["lease_id"], "session_id": args.session},
                )
            except Exception:  # noqa: BLE001
                pass
        save()
        print(f"\nreport: {out}")
        print("result:", "PASS" if report["ok"] else "FAIL")


if __name__ == "__main__":
    raise SystemExit(main())
