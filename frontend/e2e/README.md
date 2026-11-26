# Frontend E2E (Playwright)

> Ported with the frozen baseline. The mock suite is the only suite intended
> for CI. The hardware suite is environment-gated, legacy and was not run in
> this repository.

Black-box browser tests against the **built** frontend served by the FastAPI
backend. The default suite runs in `mock` mode and touches no ROS, gateway,
serial port or hardware. A separate, environment-gated suite reads a private
live gateway and never moves the arm.

## Run (mock, default)

```bash
cd frontend
npm ci                          # once
npx playwright install chromium  # once, if the browser is missing
npm run test:e2e
```

`pretest:e2e` runs `npm run build` first, so the backend serves the current
bundle. The config starts `e2e/start-backend.sh`, which uses a temporary SQLite
database (`/tmp/armpi_studio_e2e_8766.db`) and port `8766`; override with
`E2E_PORT`. To reuse an already running mock backend, keep it on the same port
(local runs use `reuseExistingServer`; CI does not).

## Run (hardware, read-only, private gateway)

```bash
ARMPI_E2E_HARDWARE=1 \
ARMPI_GATEWAY_URL=http://<robot-host>:8765 \
ARMPI_GATEWAY_TOKEN_FILE=/tmp/armpi_gateway_token \
npm run test:e2e:hardware
```

This starts `e2e/start-backend-hardware.sh` on port `8767` with
`ARMPI_MODE=hardware` and `ARMPI_HARDWARE_MOTION=0`. The token is read from the
file and exported, never printed or placed on a command line. The hardware spec
checks live battery/camera/state, G4's live `/tf` path, and that a complete
preview/execute attempt is rejected with `GATE_REJECTED`. The launch script also
unsets `ARMPI_SUPERVISED_MOTION_ACK` so a supervised shell cannot leak into the
read-only run. Do not run it in CI or against a robot whose workspace is not
clear.

Artifacts:

- `playwright-report/` — HTML report (`npm run test:e2e:report`)
- `test-results/` — traces and screenshots on failure (gitignored)

## Coverage

| Spec | Checks |
|---|---|
| `dashboard.spec.ts` | 模拟 source badge, camera panel renders the mock frame (640×400), battery 2S panel, 5-joint table, safety gate 0 个阻塞, eight-page navigation, Settings mode/mapping |
| `motion-flow.spec.ts` | target beyond `max_step_rad` is rejected (`STEP_EXCEEDED`); lease → preview token → confirm dialog → execute → 已完成 → lease release |
| `trajectory.spec.ts` | G2 示例 import, 模拟 feedback label, tracking metrics + two charts, client-side replay switches the top bar to 回放, and play/pause appends no audit event |
| `experiments.spec.ts` | G3 示例 16-pair demo, 32 trial rows, paired analysis (improvement/CI/sign p), explicit underpowered-success warning, export links, and no motion audit stages |
| `visual.spec.ts` | G4 URDF model SVG, 暂定/no-live-TF warnings, camera frame names, 11-joint limit table, metadata reference save, and no motion audit stages |
| `api-contract.spec.ts` | `/api/health`, `/api/state`, `/api/peripherals`, camera snapshot and preview token match the frontend's zod contract |
| `hardware.spec.ts` | hardware-only: 实机 source, `measured` battery/camera, measured joint state from `servo_states`, live gateway `/tf`, and a full execute attempt rejected without `Dispatched`/`Completed` |

## Not covered

- Hardware mode in the default suite: the hardware spec is excluded unless
  `ARMPI_E2E_HARDWARE=1`; it is never part of CI.
- Visual regression: no screenshot baselines are committed. The suite asserts
  text, roles and counts only.
- Fault injection flows (mock faults) beyond the safety/motion paths above.
