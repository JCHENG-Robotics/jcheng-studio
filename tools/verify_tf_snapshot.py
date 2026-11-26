#!/usr/bin/env python3
"""Compose a gateway /tf snapshot and print one root-to-child transform.

This is a read-only verification helper. It consumes a JSON snapshot captured
with ``curl -H "X-Armpi-Token: ..." http://<robot>:8765/tf`` and uses the same
composition code as the G4 Visual Workbench.

    armpi-debug-studio/backend/.venv/bin/python \
      armpi-debug-studio/tools/verify_tf_snapshot.py \
      --snapshot evidence/studio_g5/20261118/gateway_tf.json \
      --child link5

Compare the printed translation/quaternion against ``tf2_echo base_link link5``
inside the vendor container. It never connects to ROS or the robot.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domain.visual import compose_tf_frames  # noqa: E402


def _quaternion_from_matrix(values: list[float]) -> list[float]:
    """Return xyzw from a row-major 3x3 matrix."""
    m00, m01, m02, m10, m11, m12, m20, m21, m22 = values
    trace = m00 + m11 + m22
    if trace > 0:
        s = 0.5 / math.sqrt(trace + 1.0)
        w = 0.25 / s
        x = (m21 - m12) * s
        y = (m02 - m20) * s
        z = (m10 - m01) * s
    elif m00 > m11 and m00 > m22:
        s = 2.0 * math.sqrt(1.0 + m00 - m11 - m22)
        w = (m21 - m12) / s
        x = 0.25 * s
        y = (m01 + m10) / s
        z = (m02 + m20) / s
    elif m11 > m22:
        s = 2.0 * math.sqrt(1.0 + m11 - m00 - m22)
        w = (m02 - m20) / s
        x = (m01 + m10) / s
        y = 0.25 * s
        z = (m12 + m21) / s
    else:
        s = 2.0 * math.sqrt(1.0 + m22 - m00 - m11)
        w = (m10 - m01) / s
        x = (m02 + m20) / s
        y = (m12 + m21) / s
        z = 0.25 * s
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    return [x / norm, y / norm, z / norm, w / norm]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, help="gateway /tf JSON")
    parser.add_argument("--root", default="base_link")
    parser.add_argument("--child", required=True)
    parser.add_argument("--json-out", help="write the composed record as JSON")
    args = parser.parse_args(argv)

    with open(args.snapshot, encoding="utf-8") as handle:
        payload = json.load(handle)
    result = compose_tf_frames(payload, args.root)
    if result["issues"]:
        for issue in result["issues"]:
            print(f"issue: {issue}", file=sys.stderr)
    frame = next(
        (item for item in result["frames"] if item["name"] == args.child),
        None,
    )
    if frame is None:
        print(
            f"error: {args.child!r} is not reachable from {args.root!r}",
            file=sys.stderr,
        )
        return 1

    record = {
        "root": args.root,
        "child": args.child,
        "parent": frame.get("parent"),
        "translation_m": frame["position_m"],
        "quaternion_xyzw": [
            round(value, 9) for value in _quaternion_from_matrix(frame["rotation"])
        ],
        "matrix_row_major": frame["rotation"],
        "source": frame.get("source"),
        "truth": frame.get("truth"),
        "static": frame.get("static"),
        "age_ms": frame.get("age_ms"),
        "gateway_status": payload.get("status"),
        "dynamic_edges": payload.get("dynamic_edges"),
        "static_edges": payload.get("static_edges"),
    }
    print(json.dumps(record, indent=2, sort_keys=True))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
