# G1 Mock vertical slice — verification record

Date: 2026-11-14  
Scope: ArmPi Debug Studio G1, Mock mode only. No hardware access.

> Update 2026-11-17: the E7 read-only gateway is now implemented and
> hardware-verified; see
> [`e7-gateway-verification.md`](e7-gateway-verification.md). This document
> remains the 2026-11-14 Mock verification record.

## 1. Backend tests

Command:

```bash
cd backend
pytest -q
```

Result:

```text
.........................................                                [100%]
41 passed
```

Coverage areas:

- mapping conversion and provisional limits
- NaN/Inf, out-of-limit, step-limit, duration, duplicate-joint, unknown-joint,
  gripper-exclusion rejection
- command state machine (`Acknowledged` unreachable, `Dispatched → Completed`
  illegal, `Unknown → Observed` allowed)
- mock adapter: completion, timeout→Unknown, disconnect, empty feedback, NaN
  feedback, missing servo, driver exit, stop four states, reset
- API: health/state/diagnostics, preview valid/invalid, execute without lease,
  preview→execute→Completed, idempotency, single-use token, stop semantics,
  mock fault injection/reset, probe, CSV export
- Hardware mode: `hardware_motion=false`, execute rejected with
  `GATE_REJECTED` + `hardware_motion_enabled` blocker, probe returns
  `NOT_IMPLEMENTED`

## 2. Frontend build

Command:

```bash
cd frontend
npm install
npm run build
```

Result:

```text
tsc -b && vite build
vite v5.4.21 building for production...
✓ 60 modules transformed.
dist/index.html                   0.41 kB
dist/assets/index-B9l5Qp-_.css   14.64 kB
dist/assets/index-CHc2fwKk.js   272.50 kB
✓ built in 892ms
```

This verifies TypeScript strict mode and a production bundle. It is not a
Playwright/browser acceptance suite.

## 3. Real HTTP end-to-end (running uvicorn + mock adapter)

Script summary:

```text
health ok
state SIMULATION 5
lease ba283b1d
preview True cmd_7c68e771ec5840ee True
execute 200 Dispatched
idempotent 200 True
after 0.1 0.1 simulated
stages ['Requested', 'Validated', 'Dispatched', 'Observed', 'Completed']
fault 200
fault state ['unavailable', 'unavailable', 'unavailable'] True
reset 200
reset state ['valid', 'valid', 'valid'] 0
stop StopObserved True
csv True
```

Interpretation:

- Preview returned a valid result and a token.
- Execute was accepted with a lease, `command_id`, token, and
  `Idempotency-Key`.
- The same idempotency key returned the same command instead of dispatching
  again.
- The mock motion completed and the state moved to 0.1 rad with
  `truth=simulated`.
- The audit trail contains Requested → Validated → Dispatched → Observed →
  Completed.
- `empty_feedback` made all joints unavailable; reset restored valid mock data.
- Stop returned `StopObserved` with explicit “not a hardware emergency stop”
  evidence.
- CSV export contains the `event_id` column.

## 3.1 Browser acceptance (Playwright + headless Chromium)

A Playwright smoke test ran against the Vite dev server and the real uvicorn
backend. All checks passed with no browser console errors:

```text
PASS dashboard loads with SIMULATION
PASS joint control route
PASS lease acquired from UI
PASS preview valid
PASS confirmation modal
PASS execute reaches Completed
PASS mock probe runs
PASS event timeline shows completed
PASS settings shows provisional mapping
CONSOLE_ERRORS []
```

The test exercised: open Dashboard → acquire lease → enter target → preview →
confirmation modal → execute → observe Completed → run mock probe → check Event
Timeline → check Settings. This is still a Mock-only UI test; it does not
validate hardware or ROS integration.

> Note (2026-11-17 audit): this was a one-off browser session. No Playwright
> file or script was committed, so the PASS list above is **not reproducible**
> from the repository. E8 (committed Playwright/contract tests) remains open.

## 4. What this does NOT prove

- No ArmPi hardware was connected.
- No serial port (`/dev/rrc`, `/dev/ttyACM*`) was opened.
- No ROS 2 node was started; E7 was not implemented at the time of this record
  (see the 2026-11-17 update above).
- No joint limit was mechanically measured.
- Mock positions are synthetic and are not sensor feedback.
- The vendor driver defects documented in G0 remain unpatched.

## 5. Reproduce

```bash
# backend
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000

# frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Open <http://127.0.0.1:5173>. Expect a `SIMULATION` badge and no hardware
motion controls in Hardware Mode.
