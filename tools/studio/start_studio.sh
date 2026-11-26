#!/usr/bin/env bash
# Start the ported Studio console backend on the operator laptop.
#
# The console is an HTTP/WS client. This script never opens a device, never
# joins a DDS domain and never starts a ROS process. The robot-side legacy
# gateway (armpi-debug-studio/ros2_gateway in the frozen
# jcheng-manipulation baseline) is NOT part of this repository.
#
# Mock mode (default, no robot, no gateway):
#   tools/studio/start_studio.sh
#
# Read-only legacy gateway mode (the frozen baseline's pre-ecosystem gateway):
#   ARMPI_GATEWAY_URL=http://<gateway-host>:8765 \
#   ARMPI_GATEWAY_TOKEN_FILE=~/.config/armpi-studio/gateway-token \
#     tools/studio/start_studio.sh --hardware
#
# Legacy pose takeover (motion is opt-in and requires the fixed phrase):
#   ARMPI_GATEWAY_URL=http://<gateway-host>:8766 \
#   ARMPI_GATEWAY_TOKEN_FILE=~/.config/armpi-studio/gateway-token \
#   ARMPI_TAKEOVER_ACK=I_AM_AT_THE_ROBOT_WITH_A_CLEAR_WORKSPACE \
#     tools/studio/start_studio.sh --takeover
#
# Motion is never enabled by CI, a service unit or an unattended process. The
# console is not an e-stop. The platform has no e-stop, brake, encoder or
# self-collision model.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
HOST="127.0.0.1"
PORT="${PORT:-8000}"
STATE_DIR="${ARMPI_STUDIO_STATE_DIR:-$HOME/.local/state/armpi-studio}"
DB_PATH="${DB_PATH:-$STATE_DIR/studio.db}"
LOG="${LOG:-$STATE_DIR/console.log}"
MODE="mock"
TAKEOVER=0

usage() {
    sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mock) MODE="mock"; shift ;;
        --hardware) MODE="hardware"; shift ;;
        --takeover) MODE="hardware"; TAKEOVER=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

[[ -d "$BACKEND_DIR/app" ]] || fail "missing $BACKEND_DIR/app"
if [[ -x "$BACKEND_DIR/.venv/bin/python" ]]; then
    PYTHON="$BACKEND_DIR/.venv/bin/python"
else
    PYTHON="${PYTHON:-python3}"
fi

if [[ "$MODE" == "hardware" ]]; then
    [[ -n "${ARMPI_GATEWAY_URL:-}" ]] \
        || fail "set ARMPI_GATEWAY_URL to the legacy gateway (e.g. http://<gateway-host>:8765)"
    if [[ -n "${ARMPI_GATEWAY_TOKEN_FILE:-}" ]]; then
        [[ -r "$ARMPI_GATEWAY_TOKEN_FILE" ]] \
            || fail "token file is not readable: $ARMPI_GATEWAY_TOKEN_FILE"
        token_mode="$(stat -c '%a' "$ARMPI_GATEWAY_TOKEN_FILE" 2>&1 || true)"
        [[ "$token_mode" == "600" ]] \
            || fail "token file must be mode 0600 (found ${token_mode:-unknown})"
        ARMPI_GATEWAY_TOKEN="$(cat "$ARMPI_GATEWAY_TOKEN_FILE")"
    fi
    [[ -n "${ARMPI_GATEWAY_TOKEN:-}" ]] \
        || fail "set ARMPI_GATEWAY_TOKEN or ARMPI_GATEWAY_TOKEN_FILE"
    export ARMPI_MODE=hardware
    export ARMPI_GATEWAY_URL ARMPI_GATEWAY_TOKEN
    if [[ "$TAKEOVER" == "1" ]]; then
        [[ "${ARMPI_TAKEOVER_ACK:-}" == "I_AM_AT_THE_ROBOT_WITH_A_CLEAR_WORKSPACE" ]] \
            || fail "--takeover needs ARMPI_TAKEOVER_ACK=I_AM_AT_THE_ROBOT_WITH_A_CLEAR_WORKSPACE"
        export ARMPI_HARDWARE_MOTION=1
        export ARMPI_MOTION_SCOPE=pose
        export ARMPI_TAKEOVER_ACK
    else
        # Read-only legacy monitoring: never inherit a motion opt-in.
        export ARMPI_HARDWARE_MOTION=0
        unset ARMPI_MOTION_SCOPE ARMPI_TAKEOVER_ACK ARMPI_SUPERVISED_MOTION_ACK
    fi
else
    export ARMPI_MODE=mock
    export ARMPI_HARDWARE_MOTION=0
    unset ARMPI_GATEWAY_URL ARMPI_GATEWAY_TOKEN ARMPI_CAMERA_GATEWAY_URL
    unset ARMPI_MOTION_SCOPE ARMPI_TAKEOVER_ACK ARMPI_SUPERVISED_MOTION_ACK
fi

mkdir -p "$STATE_DIR"
export DB_PATH
export BIND_HOST="$HOST"
export PORT

# A busy port is reported, never killed: a restart must not cost a healthy
# console (the 2026-11-24 incident). Stop the previous PID yourself.
if ss -ltn 2>&1 | grep -q ":$PORT "; then
    fail "port $PORT is already in use; stop the previous console first"
fi

cd "$BACKEND_DIR"
nohup "$PYTHON" -m uvicorn app.main:create_app --factory \
    --host "$HOST" --port "$PORT" > "$LOG" 2>&1 &
BACKEND_PID=$!
echo "pid $BACKEND_PID, log $LOG"

up=0
health_tmp="$STATE_DIR/health.tmp"
for _ in $(seq 1 25); do
    if curl -sf -m 3 "http://$HOST:$PORT/api/health" > "$health_tmp"; then up=1; break; fi
    kill -0 "$BACKEND_PID" 2>&1 || { tail -20 "$LOG" >&2; fail "backend exited during startup"; }
    sleep 1
done
rm -f "$health_tmp"
[[ "$up" == "1" ]] || fail "backend did not answer /api/health within 25 s"

echo "mode: $MODE"
curl -s "http://$HOST:$PORT/api/health"; echo
cat <<EOF
Open http://$HOST:$PORT
Stop with: kill $BACKEND_PID
EOF
