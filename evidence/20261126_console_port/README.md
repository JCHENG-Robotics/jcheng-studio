# Console port evidence — 2026-11-26

Scope: the v0.2.0 port of the frozen ArmPi Debug Studio console. No robot,
gateway, device or DDS domain was used.

## Source

- `JerichoCheng/jcheng-manipulation`, HEAD `a08b8b6`, working tree 2026-11-26.
- Ported: backend, frontend, configs, hand-eye calibration, `jcheng_core`,
  operator runners and the G1–G8.1 records.
- Not ported: `ros2_gateway/` (ROS 2/DDS L2), `data/studio.db`, hardware
  evidence bundles and the vendor-derived URDF.

## Commands and results

| Check | Command | Result |
|---|---|---|
| Static tests | `python3 -m pytest -q tests` | 13 passed |
| Repository gate | `bash scripts/check_repo_gate.sh` | pass |
| Backend tests | `backend/.venv/bin/python -m pytest tests` | 154 passed |
| Frontend build | `cd frontend && npm run build` | pass |

Artifacts:

- [`gate.txt`](gate.txt) — repository gate output.
- [`backend-tests.txt`](backend-tests.txt) — backend suite summary.
- [`frontend-build.txt`](frontend-build.txt) — production build output.

## Limits

- Playwright browser E2E was not run.
- The legacy HTTP adapter was not exercised against a gateway.
- No hardware motion session was authorized.
- The `/v1` gateway client and the M2 session store are not implemented.
