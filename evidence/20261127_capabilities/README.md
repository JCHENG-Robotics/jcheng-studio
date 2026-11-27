# Capability registry and demo mode — 2026-11-27

Scope: the always-visible capability registry (`GET /api/capabilities` and the
`/capabilities` page) and the offline demo mode (`--demo`, `ARMPI_DEMO=1`).
No robot motion was involved.

## Environment

- **hardware**: mode `hardware`, read-only legacy gateway `8765`,
  `ARMPI_HARDWARE_MOTION=0`, real robot reachable.
- **demo**: mode `mock`, `ARMPI_DEMO=1`, source label `SIMULATION`.
- Laptop backend from this checkout; frontend production build served by
  FastAPI.

## Results

| Run | Features | Summary |
|---|---|---|
| hardware | 19 | available 10, read_only 3, degraded 1, not_configured 4, offline 1 |
| demo | 19 | available 13, read_only 1, degraded 1, not_configured 4, offline 0 |

- Backend suite: **220 passed** (`backend-tests.txt`).
- Frontend: `tsc -b && vite build` passed (`frontend-build.txt`); the page is
  reachable at `http://127.0.0.1:8000/#/capabilities` (hardware) and
  `http://127.0.0.1:8010/#/capabilities` (demo).
- The hardware registry marked `motion`/`joints`/`task` **read_only**, the
  demo registry marked `motion` **read_only** with the demo banner, and every
  page/integration/data row stayed present in both runs.
- The capability page was opened in the operator browser on both instances:
  hardware showed the 19-row registry with the read-only banner, demo showed
  the DEMO banner with 13 available rows. Verification was DOM-level
  (accessibility snapshot); no Playwright spec and no screenshots.
- Repository gate: `gate.txt` (13 tests).

## Files

- `hardware-capabilities.json` — full `GET /api/capabilities` response,
  hardware read-only.
- `demo-capabilities.json` — full response, demo mode.
- `backend-tests.txt` — `python -m pytest -q` output.
- `frontend-build.txt` — `tsc -b && vite build` output.
- `gate.txt` — `bash scripts/check_repo_gate.sh` output.

## Limits

- No hardware motion; `hardware_motion_enabled` stayed false.
- The static offline fallback on the capability page was code-reviewed and
  built, but not exercised with a stopped backend in this record.
