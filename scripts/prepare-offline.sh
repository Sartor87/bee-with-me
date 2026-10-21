#!/usr/bin/env bash
# Prepares this laptop to start Bee With Me without internet (B62). Run it at home, with internet,
# after installing or updating the app.
#
#   1. Downloads every backend package (and its dependencies) for this laptop's Python into vendor/wheels
#      (wheels only) and records their SHA-256 in vendor/wheels/SHA256SUMS.
#      start.sh installs from there when a package is missing and there is no internet.
#   2. Installs the backend packages into .venv from that folder.
#   3. Installs the frontend packages (npm ci), which also fills the local npm cache.
#
# vendor/ is not committed to git. Run again after every update that changes backend/requirements.txt
# or frontend/package-lock.json.
#
#   ./scripts/prepare-offline.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
WHEELS="$ROOT/vendor/wheels"

step() { printf '\033[36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

if [[ ! -x "$PYTHON" ]]; then
    PY="$(command -v python3 || command -v python || true)"
    [[ -n "$PY" ]] || die 'Python was not found on PATH (tried "python3" and "python"). Install Python 3.11+ and re-run.'
    step 'Creating Python virtual environment (.venv)'
    "$PY" -m venv "$ROOT/.venv" || die 'Could not create .venv (on Debian/Ubuntu: sudo apt install python3-venv).'
fi

step "Downloading backend packages into $WHEELS"
# Start from an empty folder, so no old or foreign wheel stays next to the fresh ones. Wheels only: a source
# archive would run its own build code during the install.
rm -rf "$WHEELS"
mkdir -p "$WHEELS"
"$PYTHON" -m pip download --only-binary :all: -r "$ROOT/backend/requirements.txt" -d "$WHEELS" || die 'pip download failed - is the internet up?'
# Record every wheel's SHA-256: start.sh uses the folder only while it still matches (scripts/wheelhouse.py).
"$PYTHON" "$ROOT/scripts/wheelhouse.py" write "$WHEELS" || die 'Could not write vendor/wheels/SHA256SUMS.'

step 'Installing backend packages from vendor/wheels'
"$PYTHON" -m pip install --no-index --find-links "$WHEELS" -r "$ROOT/backend/requirements.txt" \
    || die 'Installing from vendor/wheels failed - see the messages above.'

step 'Installing frontend packages (npm ci)'
(cd "$ROOT/frontend" && npm ci --no-audit --no-fund) || die 'npm ci failed - is the internet up?'

printf '\033[32m%s\033[0m\n' 'Done. This laptop can now start Bee With Me without internet.'
