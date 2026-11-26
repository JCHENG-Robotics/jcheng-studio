#!/usr/bin/env bash
# Start the backend in hardware (read-only) mode for the gated Playwright run.
#
# Required environment:
#   ARMPI_GATEWAY_URL       e.g. http://<robot-host>:8765
#   ARMPI_GATEWAY_TOKEN_FILE path on this host containing the shared token
#
# The token is read into ARMPI_GATEWAY_TOKEN and never printed or placed on a
# command line. This script only starts a local backend; it does not start the
# robot gateway or any ROS node.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PORT="${E2E_HARDWARE_PORT:-8767}"
DB_PATH="/tmp/armpi_studio_e2e_hardware_${PORT}.db"

: "${ARMPI_GATEWAY_URL:?set ARMPI_GATEWAY_URL to the read-only gateway}"
: "${ARMPI_GATEWAY_TOKEN_FILE:?set ARMPI_GATEWAY_TOKEN_FILE to a readable token file}"

if [[ ! -f "${ROOT}/frontend/dist/index.html" ]]; then
  echo "ERROR: frontend/dist is missing; run 'npm run build' first" >&2
  exit 1
fi
if [[ ! -r "${ARMPI_GATEWAY_TOKEN_FILE}" ]]; then
  echo "ERROR: token file is not readable: ${ARMPI_GATEWAY_TOKEN_FILE}" >&2
  exit 1
fi

rm -f "${DB_PATH}" "${DB_PATH}-wal" "${DB_PATH}-shm"

export ARMPI_MODE=hardware
export ARMPI_GATEWAY_URL
ARMPI_GATEWAY_TOKEN=$(cat "${ARMPI_GATEWAY_TOKEN_FILE}")
export ARMPI_GATEWAY_TOKEN
export ARMPI_HARDWARE_MOTION=0
# Never inherit a supervised acknowledgement from the operator shell.
unset ARMPI_SUPERVISED_MOTION_ACK
export DB_PATH
export BIND_HOST=127.0.0.1
export PORT

exec "${ROOT}/backend/.venv/bin/python" -m uvicorn app.main:create_app \
  --factory \
  --app-dir "${ROOT}/backend" \
  --host 127.0.0.1 \
  --port "${PORT}" \
  --log-level warning
