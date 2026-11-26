# Operator sessions and records — jcheng-studio

> Status: **Frozen (M2a)** as of 2026-11-26. Version 0.5.0.
> The append-only SQLite store, the authorization gate, the record hash and the
> export exist. M2a is read-only for robot motion; M2b adds joint/gripper
> intent. The field set below is the frozen `schema_version: 1` record.
>
> **Ported v0.2.0 note:** the backend includes the frozen baseline's SQLite
> event store (`backend/app/persistence/store.py`). It is a legacy console log
> for commands and events, not the session store described here. The M2a store
> is `backend/app/sessions/` and lives outside the repository.

## 1. Purpose

A Studio session is the unit of operator authorization and the unit of
evidence. It answers four questions without reading code: who authorized the
session, what the operator did, what the gateway and the system layer reported,
and which software, contract and calibration were in force.

The record is deliberately separate from the system layer's own evidence. The
system layer records what it executed; the Studio records what the operator was
shown, asked for and told in response. Neither record overrides the other.

## 2. Principles

- **Explicit authorization.** Every session is opened by a named operator for a
  named purpose. A previous session's authorization never carries over.
- **Record before action.** An action that cannot be written to the session
  store is not sent.
- **Fail closed.** Stale state, a contract mismatch, a rejected token or a
  store failure ends or degrades the session; it never widens it.
- **No secrets.** The gateway token, SSH material and unrelated personal data
  never enter a record or an export.
- **No self-arming.** A restart, a reconnect or a recovered stream does not
  re-open a session or re-enable an action.
- **Honest provenance.** Commanded values, measured values, gateway reports and
  operator annotations are separate fields. Missing data stays missing.

## 3. Lifecycle

| Transition | Trigger | Recorded |
|---|---|---|
| `open` | Operator confirms identity, purpose, gateway URL and robot identity | Operator, purpose, versions, contract hash, calibration identifiers |
| `read-only` | Gateway reachable; no authorization yet | Health, contract and state reads |
| `authorize` | Operator authorizes this session; token and contract checks pass | Authorization event, scope and expiry |
| `degrade` | State or safety stale; gateway or stream fault | Cause, last good timestamp, displayed age |
| `close` | Operator ends the session, or a fail-closed condition fires | Reason, duration, counts, export hash |
| `seal` | Record written, hashed and made read-only | Final record hash |

Rules:

- One session at a time per Studio instance. A second request is refused and
  recorded, not queued.
- `degrade` and `close` are terminal for the authorization: the operator
  authorizes again explicitly.
- A backend restart closes the session. The record notes the restart and the
  watchdog action if any.
- The Studio never holds the system's control lease and never claims to. The
  system layer's lease state is displayed as reported, with its age.

## 4. Session record (frozen, `schema_version: 1`)

The storage format is SQLite; the field set below is the frozen M2 record.

| Field | Meaning |
|---|---|
| `schema_version` | record schema version, integer |
| `session_id` | immutable identifier, unique per session |
| `opened_at`, `closed_at` | ISO 8601 timestamps with timezone |
| `operator` | operator identity as configured locally; no credentials |
| `purpose` | short free-text purpose entered at open |
| `studio_version`, `studio_commit` | `VERSION` and git commit of the running backend |
| `contract_hash` | contract identity reported by the gateway at admission |
| `gateway_url` | configured URL with credentials and query removed |
| `robot_profile` | robot profile identifier or hash, when available |
| `calibration` | camera/wrist/extrinsic calibration identifiers in force |
| `events` | ordered actions, reads, gateway responses and operator annotations |
| `actions` | action class, parameters, gateway response and system result, if any |
| `degraded_periods` | intervals with cause and last good timestamp |
| `close_reason` | operator, fail-closed condition or backend restart |
| `record_hash` | SHA-256 over the sealed record |
| `export` | optional export path, format and hash |

Each event carries its own timestamp and provenance: `operator`, `gateway`,
`system` or `studio`. A displayed value that came from a gateway response is
never re-labelled as a measurement.

## 5. Action classes

| Class | Examples | Requires |
|---|---|---|
| Read-only | Health, contract, state, safety, event history | A reachable gateway; no authorization |
| Diagnostic | Refresh a stream, re-read the contract, open the local log | An open session record; no motion |
| Intent | One joint step or one gripper command (M2b) | An authorized session **and** an operator-supplied lease issued by the system layer |
| Export | Write a session record or experiment bundle | A sealed or open record; redaction applied |

The Studio has no class that writes a device, executes a trajectory or changes
the system's safety state directly. Motion is always an intent sent to the
gateway; the system layer decides.

## 6. Storage, export and retention

- The M2a store is SQLite at `~/.local/state/armpi-studio/sessions.db`
  (override `ARMPI_SESSION_DB`), outside this repository. Exports go to
  `~/.local/state/armpi-studio/exports/` (override `ARMPI_SESSION_EXPORTS`).
- The `session_events` table is append-only. The `sessions` row is a
  materialized current state; a correction is a new event, nothing is silently
  rewritten.
- Exports are self-describing bundles containing the record, its hash and the
  software/contract identity. A bundle with a hash mismatch is rejected.
- Exports redact the token, SSH paths and unrelated personal data. The gateway
  URL is kept without credentials.
- Retention is an operator decision recorded per session. Deleting a local
  record does not rewrite a published acceptance report; the report records
  that the record was withdrawn.
- No session database, export or recording enters git; the repository gate
  rejects them.

## 7. Evidence rules

| Class | Use |
|---|---|
| L0 / L1 | Contract and unit tests in CI; no session store |
| L2 | Mock gateway; sessions labelled `mock`, never `hardware` |
| L3 | Mock end-to-end; sessions labelled `mock` |
| L4 | Replay of a recorded session; labelled `replay` |
| L5 | Supervised hardware session with the system limits active |

- Synthetic, mock and replay sessions are never reported as hardware
  validation.
- An L5 record requires the operator's explicit authorization, the robot
  identity and the system layer's own evidence for the same interval.
- A failed action stays failed in the record. It is not replaced by a later
  successful run in the same acceptance claim.

## 8. M2 exit criteria

- The session store and record schema above are frozen in this document.
- A mock session can be opened, degraded, closed and exported, and the export
  hash verifies.
- Refusal paths are proven: no session without authorization, no intent on
  stale state, no fallback on contract mismatch, no action without a record.
- The repository gate rejects a committed store, export or env file.
- No robot access is used to satisfy these criteria.

M2a satisfies the store/lifecycle/export and authorization-refusal criteria.
M2b satisfies the intent criterion with an operator-supplied system lease
(validate + execute against a mock gateway); no robot is used.

## 9. M2a implementation (2026-11-26)

| Item | Implementation |
|---|---|
| Store | `backend/app/sessions/store.py`, SQLite, WAL, append-only `session_events` |
| Record | `schema_version: 1`; events carry `seq`, `at`, `type`, `provenance`, `payload` |
| Seal | SHA-256 over canonical JSON of the record (without `record_hash`) |
| Export | `*.json` bundle with record and hash; a stored-hash mismatch is rejected |
| API | `POST /api/session/open`, `/authorize`, `/degrade`, `/close`; `GET /current`, `/list`, `/{id}`, `/{id}/export` |
| Authorization gate | `/api/gateway/v1/status` must be configured, reachable, authenticated, contract-matching, fresh and not stop-latched; otherwise the session is degraded and the request is refused |
| Restart | The first session access closes any session left active with `backend restart` |
| Secrets | No token is stored; the gateway URL drops userinfo, query and fragment |
| Config | `ARMPI_SESSION_DB`, `ARMPI_SESSION_EXPORTS`, `ARMPI_STUDIO_COMMIT` |
| Tests | `backend/tests/test_sessions.py` (7 tests) |

### 9.1 M2b intent implementation (2026-11-26)

| Item | Implementation |
|---|---|
| Client | `backend/app/adapters/gateway_v1_intent.py`; only `/v1/motion/validate` and `/v1/motion/execute` |
| API | `POST /api/session/intent/joint`, `/api/session/intent/gripper` |
| Gate | Session must be `authorized`; contract hash must match; lease ID must be supplied by the operator |
| Lease | The Studio never calls `/v1/leases`; the system layer issues the lease and remains the only motion authority |
| Command | `JOINT_STEP`/`step` or `GRIPPER`/`gripper`; contract v0.1.0/hash; `verify_bus=true`, `require_measured_state=true` |
| Record | `intent_requested` before the call; `intent_rejected`, `intent_executed` or `intent_unknown` after it; `actions` is derived from these events |
| Fail closed | Validation refusal does not execute; an UNKNOWN outcome records the event and degrades the session; no retry |
| Tests | `backend/tests/test_gateway_v1_intent.py` (8 tests); mock gateway only |

## 10. Related

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — session states and planes.
- [`INTEGRATION.md`](INTEGRATION.md) — gateway contract and failure semantics.
- [`ROADMAP.md`](../ROADMAP.md) — M2 scope and exit criterion.
- [`ACCEPTANCE.md`](../ACCEPTANCE.md) — what is actually verified.
- [`jcheng-ops`](https://github.com/JCHENG-Robotics/jcheng-ops) — watchdog and incident runbook.
