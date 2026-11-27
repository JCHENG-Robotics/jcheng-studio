# Always-visible console

The Studio keeps every surface visible and reachable. Four rules make that
true; none of them hides a feature when a dependency is missing.

## 1. Fixed capability registry

`GET /api/capabilities` returns a fixed list of 19 rows: the ten console pages
(the capability page is the registry itself), the motion/session/replay/gateway/stop
integrations, and four data sources. Each row carries a live status:

| Status | Meaning |
|---|---|
| `available` | usable now |
| `read_only` | visible and readable; execution is disabled by the mode |
| `degraded` | configured but stale, unreachable or missing an asset |
| `not_configured` | an optional integration is not set up |
| `blocked` | the safety gate refuses execution right now |
| `offline` | the data source has no samples |

The catalog lives in `backend/app/application/capability_ops.py`. Adding a
surface means adding one catalog entry; the response shape does not change.

The `/capabilities` page renders the registry. If the backend cannot be
reached, it falls back to a static copy of the same list and marks every row
`offline`, so the operator still sees what the console can do.

## 2. Full rendering

Mode-dependent panels stay in the DOM. In hardware mode the mock fault
injection card and the `/v1` diagnostics card remain visible and explain why
their controls are disabled; motion pages render with a disabled state and a
reason instead of disappearing. Banners may differ by mode, but a feature is
never removed because a dependency is missing.

## 3. Offline demo mode

`tools/studio/start_studio.sh --demo` sets `ARMPI_MODE=mock` and
`ARMPI_DEMO=1`. The console then runs the full mock adapter, every page works,
and the source is labelled `SIMULATION` with a DEMO banner. `ARMPI_DEMO=1`
requires mock mode; hardware mode never falls back to simulated data. Demo
mode never executes motion.

Mock mode is read-only for motion in this build: legacy dispatch is disabled by
default, so the joints/task pages render previews and explain the disabled
execution (`410 Gone`). A mock process also clears any `/v1` gateway URL/token
at startup and cannot call a real system.

```bash
tools/studio/start_studio.sh --demo
# http://127.0.0.1:8000/#/capabilities
```

## 4. Persistence

The backend and the SPA are one FastAPI process. On the operator laptop it is
kept alive by the `jcheng-ops` `armpi-studio-watchdog.service` (systemd user
unit): a `/api/health` probe every 30 s, a restart after two failures, and a
guard that never kills a healthy console while the robot is unreachable. The
watchdog's `studio.env` points at this checkout and runs read-only hardware
mode by default.

If the process is down, the watchdog starts it again within about a minute;
while it is down, the browser shows the connection error, not the console.
Use `tools/studio/start_studio.sh --demo` when no robot is available and the
full UI is needed for a demonstration.

## Limits

- The console is not an e-stop; the platform has no e-stop, brake or encoder.
- The registry reports the Studio's view, not the system layer's motion
  authority. `read_only` and `blocked` are advisory for the operator; the
  system gateway still enforces every lease and safety gate.
- The static fallback on `/capabilities` is not a data source; it only keeps
  the feature list visible.
