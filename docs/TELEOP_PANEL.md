# Teleop panel (ecosystem M5)

> Status: **Experimental**, 2026-11-26, version 0.8.0. Mock/loopback evidence
> only; no hardware.

The Studio panel at `/teleop` shows a live teleoperation session and offers the
system software stop. It is a presentation surface plus one narrowly scoped
write; it is not a lease holder and never executes motion.

## Data path

```text
teleop demo harness (loopback)
  GET /v1/status  (jcheng.teleop.status.v1)
        │
        ▼  studio backend, one GET, 1 s timeout
  GET /api/teleop/status  ──►  panel (1 Hz poll)

panel "system stop"
        │
        ▼  studio backend, Bearer token stays server-side
  POST /v1/motion/stop  (jcheng-system; latches)
        │
        ▼
  POST /api/teleop/stop  ──►  panel shows accepted/latency
```

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `JCHENG_TELEOP_STATUS_URL` | teleop status base URL, loopback with an explicit port | unset (panel shows `NOT_CONFIGURED`) |
| `JCHENG_TELEOP_STATUS_TIMEOUT_S` | status poll timeout | `1.0` |
| `ARMPI_SYSTEM_STOP` | enable the system stop proxy (`1`); requires the two `/v1` variables | off |
| `ARMPI_GATEWAY_V1_URL` | system gateway base URL, loopback with an explicit port | unset |
| `ARMPI_GATEWAY_V1_TOKEN` | system bearer token, backend-only | unset |

Both URLs are validated as loopback. A non-loopback URL is a configuration
error; the panel never exposes the token to the browser.

## Staleness and degraded state

The backend recomputes staleness from `input.age_ms` and `stale_after_ms`; it
does not trust a single boolean. The panel shows `degraded` when the payload is
missing, unreachable, invalid, or older than the threshold, and it shows the
age value next to the state. A missing endpoint or a 5xx is degraded, not
ready.

## Stop semantics

- `POST /api/teleop/stop` calls the system `POST /v1/motion/stop`. That route
  latches `SAFETY_STOP_LATCHED`, holds the base and aborts an in-flight motion;
  clearing it requires a system-side reset.
- It is a **software stop**, not an emergency stop. The platform has no e-stop,
  brake or encoder.
- The request carries `operator_id` and an optional reason; it requires no
  Studio lease and does not call the local motion adapter. The panel records an
  audit event with latency/status.
- This is the **only** stop path. The deprecated `POST /api/motion/stop`
  answers `410 Gone` by default (`LEGACY_MOTION_DISABLED`) and is not a
  substitute for the system stop.
- The backend round trip is asserted below 500 ms in tests; the UI shows the
  measured latency.

## Tests

```bash
cd backend && .venv/bin/python -m pytest tests -q        # 220 passed
cd frontend && npm run build && npm test                 # tsc/vite build; no unit suites
bash scripts/check_repo_gate.sh                          # 13 root tests, gate OK
```

`backend/tests/test_teleop_panel.py` (10 tests) covers: not-configured is
degraded; a fresh payload is not degraded; an old sample is stale; a bad schema
is invalid; an unreachable server; non-loopback URLs rejected; stop disabled by
default; the stop proxy sends the Bearer token and body to `/v1/motion/stop`
with latency <500 ms; routes expose status and refuse stop when disabled.

## Limits

- The panel is read-only except the stop proxy; there is no intent submission,
  lease acquisition or motion execution from it.
- No live gateway run in this evidence; the stop test uses a local HTTP server
  with the system contract shape. A mock-gateway run is the next evidence step.
- No browser Playwright E2E for the panel yet.
- `JCHENG_TELEOP_STATUS_URL` points at the demo harness's loopback status
  server; a production teleop process must provide the same contract.
