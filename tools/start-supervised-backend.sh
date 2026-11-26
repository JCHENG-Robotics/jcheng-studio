#!/usr/bin/env bash
# Start the Studio backend with the supervised hardware motion path enabled.
#
# This is an operator action, not a service. It requires all three:
#   * a supervised motion-enabled gateway (its own instance, not the read-only
#     armpi-gateway.service);
#   * ARMPI_GATEWAY_TOKEN_FILE with that instance's token;
#   * ARMPI_SUPERVISED_MOTION_ACK exactly as printed below.
#
# The backend still applies its own gate: unverified limits are accepted only
# under this acknowledgement, one joint per command, max step < 0.10 rad and a
# 1.0 s duration floor.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ACK="I_HAVE_SUPPORT_AND_A_CLEAR_WORKSPACE"

: "${ARMPI_GATEWAY_URL:?set ARMPI_GATEWAY_URL to the supervised gateway, e.g. http://<gateway-host>:8766}"
: "${ARMPI_GATEWAY_TOKEN_FILE:?set ARMPI_GATEWAY_TOKEN_FILE to the token file for that gateway}"
: "${ARMPI_SUPERVISED_MOTION_ACK:?set ARMPI_SUPERVISED_MOTION_ACK=$ACK only after supporting the arm and clearing the workspace}"

if [[ "${ARMPI_SUPERVISED_MOTION_ACK}" != "${ACK}" ]]; then
  echo "ERROR: ARMPI_SUPERVISED_MOTION_ACK must be exactly ${ACK}" >&2
  exit 1
fi
if [[ ! -r "${ARMPI_GATEWAY_TOKEN_FILE}" ]]; then
  echo "ERROR: token file is not readable: ${ARMPI_GATEWAY_TOKEN_FILE}" >&2
  exit 1
fi

cat >&2 <<'WARN'
============================================================
SUPERVISED MOTION WILL BE ENABLED
  - limits are provisional (mapping_verified=false)
  - exactly one joint per command
  - max step <= 0.10 rad (pulse-aligned), duration >= 1.0 s
  - Stop is a hold command, not a brake and not an emergency stop
  - keep the arm supported and the workspace clear
============================================================
WARN

export ARMPI_MODE=hardware
export ARMPI_GATEWAY_URL
ARMPI_GATEWAY_TOKEN=$(cat "${ARMPI_GATEWAY_TOKEN_FILE}")
export ARMPI_GATEWAY_TOKEN
export ARMPI_HARDWARE_MOTION=1
export ARMPI_SUPERVISED_MOTION_ACK
STATE_DIR="${ARMPI_STUDIO_STATE_DIR:-$HOME/.local/state/armpi-studio}"
mkdir -p "$STATE_DIR"
export DB_PATH="${DB_PATH:-$STATE_DIR/supervised.db}"
export BIND_HOST="${BIND_HOST:-127.0.0.1}"
export PORT="${PORT:-8000}"

exec "${ROOT}/backend/.venv/bin/python" -m uvicorn app.main:create_app \
  --factory \
  --app-dir "${ROOT}/backend" \
  --host "${BIND_HOST}" \
  --port "${PORT}" \
  --log-level warning
