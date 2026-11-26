#!/usr/bin/env bash
# Repository gate for jcheng-studio. Run locally or from CI.
#
#   bash scripts/check_repo_gate.sh
#
# It checks the required document set, the absence of a LICENSE file, secrets,
# session databases, operator env files, model weights and recordings, oversized
# files, relative links and the tests. It touches no robot, opens no device and
# joins no DDS domain.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail=0
require_file() {
  if [[ ! -f "$1" ]]; then
    echo "MISSING: $1" >&2
    fail=1
  fi
}

for f in \
  README.md \
  README.zh-CN.md \
  ROADMAP.md \
  CHANGELOG.md \
  ACCEPTANCE.md \
  LICENSE-REVIEW.md \
  VERSION \
  .gitignore \
  capability.yaml \
  docs/ARCHITECTURE.md \
  docs/INTEGRATION.md \
  docs/SESSIONS.md \
  docs/MIGRATION.md \
  docs/PRD.md \
  docs/GATEWAY_V1.md \
  docs/legacy/README.md \
  backend/pyproject.toml \
  backend/tests/fixtures/armpi_ultra_test.urdf \
  packages/jcheng_core/pyproject.toml \
  frontend/package.json \
  frontend/package-lock.json \
  configs/calibration/calibration_table_down_20261118.json \
  tests/test_contract.py \
  tests/test_no_hardware.py \
  scripts/check_repo_gate.sh \
  evidence/20261126_scaffold/README.md \
  evidence/20261126_console_port/README.md \
  evidence/20261126_gateway_v1/README.md \
  evidence/20261126_gateway_ws/README.md \
  evidence/20261126_sessions/README.md \
  evidence/20261126_intent/README.md \
  .github/workflows/ci.yml
do
  require_file "$f"
done

# The organization license review is open: no LICENSE file yet.
if [[ -e LICENSE ]]; then
  echo "ERROR: LICENSE must not exist before the license review is approved." >&2
  fail=1
fi

# No credentials. This script is excluded because it contains the pattern.
# Operator tokens belong in ~/.config/armpi-studio/studio.env (0600), never git.
if grep -RIqE \
  --exclude-dir=.git \
  --exclude-dir=.venv \
  --exclude-dir=node_modules \
  --exclude-dir=__pycache__ \
  --exclude-dir=.pytest_cache \
  --exclude-dir=dist \
  --exclude-dir=playwright-report \
  --exclude-dir=test-results \
  --exclude='check_repo_gate.sh' \
  'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----' \
  . ; then
  echo "ERROR: possible secret material found." >&2
  fail=1
fi

# No model weights, session databases, recordings or operator env files.
if find . \
    \( -path ./.git -o -name .venv -o -name node_modules \
       -o -path ./frontend/dist \
       -o -path ./frontend/playwright-report -o -path ./frontend/test-results \
       -o -name __pycache__ -o -name .pytest_cache \) -prune -o \
    \( -name '*.pt' -o -name '*.onnx' -o -name '*.engine' \
       -o -name '*_openvino_model' -o -name '*.bag' -o -name '*.mcap' \
       -o -name '*.db3' -o -name '*.mp4' -o -name '*.avi' \
       -o -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \
       -o -name '*.env' \) -print \
    | grep -q .; then
  echo "ERROR: model weights, session data or operator configuration found; keep them outside the repository." >&2
  fail=1
fi

# No dataset, model or session store inside the repository.
for d in datasets models data; do
  if [[ -d "$d" ]] && find "$d" -type f -print | grep -q .; then
    echo "ERROR: $d/ must stay outside the repository." >&2
    fail=1
  fi
done

# No oversized files (catches accidental blobs before they enter history).
big="$(find . \
    \( -path ./.git -o -name .venv -o -name node_modules \
       -o -path ./frontend/dist \
       -o -path ./frontend/playwright-report -o -path ./frontend/test-results \
       -o -name __pycache__ -o -name .pytest_cache \) -prune -o \
    -type f -size +5M -print)"
if [[ -n "$big" ]]; then
  echo "ERROR: files larger than 5 MB:" >&2
  echo "$big" >&2
  fail=1
fi

# Relative Markdown links must resolve from the file that contains them.
while IFS= read -r f; do
  dir="$(dirname "$f")"
  while IFS= read -r link; do
    case "$link" in
      http://*|https://*|mailto:*|'#'*|'') continue ;;
    esac
    target="${link%%#*}"
    [[ -z "$target" ]] && continue
    if [[ ! -e "$dir/$target" ]]; then
      echo "BROKEN LINK in $f: $link" >&2
      fail=1
    fi
  done < <(grep -oE '\]\([^)]+\)' "$f" | sed -e 's/^](//' -e 's/)$//')
done < <(find . \
    \( -path ./.git -o -name .venv -o -name node_modules \
       -o -path ./frontend/dist \
       -o -path ./frontend/playwright-report -o -path ./frontend/test-results \
       -o -name __pycache__ -o -name .pytest_cache \) -prune -o \
    -name '*.md' -print)

if [[ "$fail" -ne 0 ]]; then
  echo "Repository gate FAILED" >&2
  exit 1
fi

echo "== tests"
python3 -m pytest -q tests

echo "Repository gate OK: document set, manifest, no LICENSE, no secrets, no weights, no session data, links resolve"
