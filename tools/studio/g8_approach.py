#!/usr/bin/env python3
"""Bring a floor cube into the direct-grasp band with bounded base pulses.

The G8 task planner refuses a cube whose corrected target falls outside the
validated 0.220-0.255 m band. This runner asks the planner for the approach
vector (`APPROACH_REQUIRED`), pulses the open-loop chassis through
`POST /api/chassis/pulse`, waits for the coast, re-plans from a fresh cloud and
repeats until the plan is valid (or the pulse budget runs out). It never runs
the arm task; use `g8_task_run.py --execute` for that.

    # plan only: shows whether an approach is needed (no motion)
    python3 tools/studio/g8_approach.py --backend http://127.0.0.1:8000 \
        --out /tmp/g8_approach.json

    # pulse until aligned (operator beside the robot, arm at the view)
    python3 tools/studio/g8_approach.py --backend http://127.0.0.1:8000 \
        --session operator-g81 --out /tmp/g8_approach.json --execute \
        --i-have-clear-workspace

Every pulse is capped by the gateway (duty, duration) and by the backend
contract; the loop stops at the first valid plan. The base has no odometry:
the camera re-measurement is the only confirmation.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from g8_task_run import Client, now_iso  # noqa: E402


def plan_once(client: Client) -> dict[str, Any]:
    plan = client.request("POST", "/api/task/plan")
    codes = [issue.get("code") for issue in plan.get("errors", [])]
    return {"plan": plan, "codes": codes}


def describe(approach: dict[str, Any]) -> str:
    parts = []
    forward = float(approach.get("forward_mm", 0.0))
    left = float(approach.get("left_mm", 0.0))
    if abs(forward) >= 0.5:
        parts.append(f"{'forward' if forward > 0 else 'back'} {abs(forward):.0f} mm")
    if abs(left) >= 0.5:
        parts.append(f"{'left' if left > 0 else 'right'} {abs(left):.0f} mm")
    return " / ".join(parts) or "trim"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="http://127.0.0.1:8000")
    parser.add_argument("--session", default="operator-g81")
    parser.add_argument("--lease-ttl", type=int, default=600)
    parser.add_argument("--max-pulses", type=int, default=3)
    parser.add_argument("--settle", type=float, default=0.8)
    parser.add_argument("--out", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--i-have-clear-workspace", action="store_true")
    args = parser.parse_args(argv)
    if args.execute and not args.i_have_clear_workspace:
        raise SystemExit("--execute needs --i-have-clear-workspace")

    client = Client(args.backend)
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "g8_approach",
        "generated_at": now_iso(),
        "execute": bool(args.execute),
        "initial_plan": None,
        "pulses": [],
        "final_plan": None,
        "valid": False,
        "error": None,
    }
    out = Path(args.out)
    lease: dict[str, Any] | None = None

    def save() -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    try:
        first = plan_once(client)
        report["initial_plan"] = first
        print("initial plan:", "VALID" if first["plan"].get("valid") else first["codes"])
        if first["plan"].get("valid"):
            report["valid"] = True
            report["final_plan"] = first
            return 0
        approach = first["plan"].get("approach")
        if not approach or "APPROACH_REQUIRED" not in first["codes"]:
            raise RuntimeError(f"planner did not request an approach: {first['codes']}")
        print("  need base approach:", describe(approach),
              f"(duty {approach['duty']}, {approach['duration_s']} s, "
              f"nominal {approach['nominal_travel_mm']} mm)")
        if not args.execute:
            return 0

        lease_response = client.request(
            "POST",
            "/api/control/acquire",
            {"session_id": args.session, "ttl_s": args.lease_ttl},
        )
        if lease_response.get("lease_id") is None:
            raise RuntimeError(f"lease acquire failed: {lease_response}")
        lease = lease_response
        print("lease:", lease["lease_id"])

        current = first
        for index in range(args.max_pulses):
            approach = current["plan"].get("approach")
            if not approach:
                break
            print(f"\n== pulse {index + 1}/{args.max_pulses}: {describe(approach)}")
            response = client.request(
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
            if "error" in response:
                raise RuntimeError(f"chassis pulse rejected: {response['error']}")
            gateway = response.get("gateway") or {}
            print(
                "  pulse:", response.get("status"), response.get("verification"),
                "matched", gateway.get("subscribers_matched"),
                "elapsed", gateway.get("elapsed_s"),
            )
            report["pulses"].append(
                {"approach": approach, "response": response}
            )
            heartbeat = client.request(
                "POST",
                "/api/control/heartbeat",
                {"lease_id": lease["lease_id"], "session_id": args.session},
            )
            if heartbeat.get("lease_id"):
                lease = heartbeat
            time.sleep(max(0.2, args.settle))
            current = plan_once(client)
            print("  plan:", "VALID" if current["plan"].get("valid") else current["codes"])
            if current["plan"].get("valid"):
                break
            if "APPROACH_REQUIRED" not in current["codes"]:
                break
        report["final_plan"] = current
        report["valid"] = bool(current["plan"].get("valid"))
        if report["valid"]:
            point = current["plan"].get("point") or []
            target = current["plan"].get("target") or []
            radius = (target[0] ** 2 + target[1] ** 2) ** 0.5 if len(target) > 1 else 0.0
            print(
                "\naligned: point", [round(value, 4) for value in point],
                "target radius %.4f m" % radius,
            )
        else:
            report["error"] = "not aligned within the pulse budget"
            print("\nnot aligned within the pulse budget")
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
        print("result:", "PASS" if report["valid"] else "FAIL")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
