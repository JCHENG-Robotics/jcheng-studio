# M5 teleop panel (studio)

> Status: **Partially verified (mock gateway + loopback demo, 2026-11-26)**.
> No hardware. No browser E2E.

## What was run

1. `jcheng-system` mock gateway on `127.0.0.1:8766`, token from
   `JCHENG_GATEWAY_TOKEN` (not argv).
2. `jcheng_teleop_publisher` demo harness on domain 42 with the
   `JCHENG_TELEOP_STATUS_URL` loopback status server (`status_port:=8765`),
   profile `armpi_ultra_demo`, 12 s script.
3. The studio backend service built with
   `teleop_status_url=http://127.0.0.1:8765`,
   `gateway_v1_url=http://127.0.0.1:8766`, `system_stop_enabled=True`.

## Result

Studio status proxy:

```json
{"configured": true, "valid": true, "stale": false, "degraded": false,
 "phase": "running", "input_age_ms": 13.824, "deadman": true,
 "lease_ref": "sha256:bbf2b368be96c787737740600c16e04d9d4be0538964a3078c72cf0dafbd5126"}
```

Studio system stop (`POST /api/teleop/stop` service call → system
`POST /v1/motion/stop`):

```json
{"requested": true, "accepted": true, "latency_ms": 0,
 "status_code": 200, "storage_error": null}
```

System safety immediately afterwards:

```json
{"safety_state": "STOP_LATCHED", "stop_latched": true}
```

The stop was not reset; the latch is the expected software-stop behaviour.

## Tests

```text
backend/.venv/bin/python -m pytest tests -q          201 passed
frontend: npm run build (tsc -b && vite build)       pass
frontend: npm test                                   exit 0 (no unit suites; e2e excluded)
scripts/check_repo_gate.sh                           13 passed, gate OK
```

`backend/tests/test_teleop_panel.py` (10 tests): not-configured is degraded;
fresh payload is not; old sample is stale; bad schema is invalid; unreachable
server; non-loopback URLs rejected; stop disabled by default; stop proxy sends
Bearer + body to `/v1/motion/stop` with latency <500 ms; routes expose status
and refuse stop when disabled.

## Limits

- Mock gateway and the demo teleop harness only; no hardware, no real teleop
  process.
- The stop is a software stop that latches; clearing it requires the system
  reset path, which was not exercised here.
- No Playwright browser E2E for the panel; the frontend is validated by build
  and the backend by tests + this service-level run.
- Browser never holds the token; stop is backend-mediated. The panel does not
  show the system safety state itself; the diagnostics page does.
