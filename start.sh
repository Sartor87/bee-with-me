#!/usr/bin/env bash
# Starts the whole Bee With Me stack on Linux: Postgres/PostGIS (Podman or Docker), the
# FastAPI backend, and the Vue frontend - instead of starting each one by hand.
# Linux counterpart of start.ps1.
#
# Like start.ps1, it lives in the project root and uses its own location to
# find the project, so it works wherever the folder is copied to.
#
# Unlike start.ps1, backend and frontend run in THIS terminal (output prefixed
# with [backend] / [frontend]) rather than in new windows - there is no terminal
# emulator that is guaranteed to exist on every distro. Ctrl+C stops both.
#
#   ./start.sh [--project-path DIR] [--skip-containers] [--no-browser]
#
#   --project-path DIR  Path to the project folder (default: this script's folder).
#   --skip-containers  Don't start the database container (database already running). --skip-docker still works.
#   --no-browser        Don't auto-open the frontend once it's up.

set -euo pipefail

PROJECT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKIP_CONTAINERS=0
NO_BROWSER=0

step() { printf '\033[36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project-path) [[ $# -ge 2 ]] || die '--project-path needs a value'; PROJECT_PATH="$2"; shift 2 ;;
        --skip-containers|--skip-docker) SKIP_CONTAINERS=1; shift ;;
        --no-browser)   NO_BROWSER=1; shift ;;
        -h|--help)      sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)              die "Unknown option: $1 (see --help)" ;;
    esac
done

[[ -d "$PROJECT_PATH" ]] || die "Project folder not found: $PROJECT_PATH
Pass the real location with --project-path, e.g.:
  ./start.sh --project-path ~/bee-with-me"
ROOT="$(cd "$PROJECT_PATH" && pwd)"
cd "$ROOT"
step "Using project folder: $ROOT"

# Returns 0 once something is listening on localhost:$1 (bash /dev/tcp - no nc needed)
port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# -- .env ------------------------------------------------------------------------
if [[ ! -f "$ROOT/.env" ]]; then
    step 'No .env found - copying .env.example'
    cp "$ROOT/.env.example" "$ROOT/.env"
    warn 'Edit .env with real values (POSTGRES_PASSWORD, SECRET_KEY, HID_VENDOR_ID/HID_PRODUCT_ID, ...) before relying on this for anything but a quick test.'
fi

# -- Database container (Podman first, Docker as the alternative) --------------------
ENGINE="${CONTAINER_ENGINE:-}"
if [[ -z "$ENGINE" ]]; then
    if command -v podman >/dev/null 2>&1; then ENGINE=podman
    elif command -v docker >/dev/null 2>&1; then ENGINE=docker
    fi
fi

if [[ $SKIP_CONTAINERS -eq 0 ]]; then
    [[ -n "$ENGINE" ]] \
        || die 'Neither podman nor docker was found on PATH. Install Podman (https://podman.io) or Docker, or re-run with --skip-containers if the database is already running elsewhere.'
    if ! "$ENGINE" info >/dev/null 2>&1; then
        if [[ "$ENGINE" == podman ]]; then
            die "Podman is installed but not usable by $(whoami). Rootless podman needs no daemon; check 'podman info' for the error (on macOS/Windows: podman machine start)."
        fi
        die "Docker is installed but not usable by $(whoami). Either the daemon isn't running
  (sudo systemctl start docker) or you're not in the docker group
  (sudo usermod -aG docker $(whoami), then log out and back in)."
    fi

    # Podman on macOS/Windows runs in a VM: named volume + host network for Postgres
    # (see docker/docker-compose.podman-machine.yaml for why). Native-Linux Podman uses the base file.
    COMPOSE_FILES=(-f "$ROOT/docker/docker-compose.yaml")
    case "$(uname -s)" in
        Darwin|MINGW*|MSYS*|CYGWIN*)
            [[ "$ENGINE" == podman ]] && COMPOSE_FILES+=(-f "$ROOT/docker/docker-compose.podman-machine.yaml") ;;
    esac

    step "Starting database ($ENGINE compose up -d)"
    "$ENGINE" compose "${COMPOSE_FILES[@]}" up -d

    step 'Waiting for Postgres to accept connections'
    PG_PORT="$(grep -E '^\s*POSTGRES_PORT\s*=' "$ROOT/.env" | tail -n1 | cut -d= -f2- | tr -dc '0-9' || true)"
    PG_PORT="${PG_PORT:-5432}"

    ready=0
    for _ in $(seq 60); do
        if port_open "$PG_PORT"; then ready=1; break; fi
        sleep 1
    done
    [[ $ready -eq 1 ]] \
        || warn "Postgres didn't come up on port $PG_PORT within 60s - continuing anyway. Check: $ENGINE compose -f docker/docker-compose.yaml logs"
fi

# -- Python venv + backend deps ---------------------------------------------------
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    step 'Creating Python virtual environment (.venv)'
    PY="$(command -v python3 || command -v python || true)"
    [[ -n "$PY" ]] || die 'Python was not found on PATH (tried "python3" and "python"). Install Python 3.11+ and re-run.'
    "$PY" -m venv "$ROOT/.venv" \
        || die "Failed to create the virtual environment at $ROOT/.venv. On Debian/Ubuntu the venv module is a separate package: sudo apt install python3-venv"
fi

step 'Installing/checking backend dependencies'
"$ROOT/.venv/bin/python" -m pip install -q -r "$ROOT/backend/requirements.txt"

# -- Database migrations: back up first if any are pending ---------------------------
if [[ $SKIP_CONTAINERS -eq 0 ]]; then
    step 'Checking database migrations'
    # Exit 1 usually means Postgres isn't accepting connections yet (slow after a reboot or
    # `podman machine start`): retry for up to 90 s rather than let the backend migrate later
    # without the backup this check exists for.
    mig_deadline=$((SECONDS + 90))
    while :; do
        set +e
        ( cd "$ROOT" && "$ROOT/.venv/bin/python" -m backend.db.migrate status )
        mig=$?
        set -e
        [[ $mig -eq 1 && $SECONDS -lt $mig_deadline ]] || break
        warn 'Database not reachable yet - retrying in 3 s'
        sleep 3
    done
    case "$mig" in
        0)  ;;
        10) step 'Migrations pending - taking a backup first (data/backups)'
            "$ROOT/scripts/backup.sh" "$ROOT/data/backups" \
                || die 'Backup failed - not starting, so the database is never migrated without a backup.' ;;
        2)  die 'The database is newer than this version of Bee With Me. Update the app (git pull) instead of starting an older one.' ;;
        *)  die "Could not check database migrations (database not reachable?) - not starting, so the database is never migrated without a backup. Check: $ENGINE compose -f docker/docker-compose.yaml logs" ;;
    esac
fi

# -- Frontend deps ------------------------------------------------------------------
command -v npm >/dev/null 2>&1 || die 'npm was not found on PATH. Install Node.js (LTS) and re-run.'
if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
    step 'Installing frontend dependencies (first run only - this can take a minute)'
    (cd "$ROOT/frontend" && npm install)
fi

# -- USB HID gateway permissions ---------------------------------------------------
# On Linux, /dev/hidraw* is root-only by default. Without a udev rule the backend
# runs fine but never sees the gateway - worth a loud hint rather than silence.
if ! grep -rqsi 'hidraw' /etc/udev/rules.d/ 2>/dev/null; then
    warn 'No udev rule for hidraw found - the backend may not be able to open the USB gateway as a normal user.
  Fix once with (VID/PID from .env, without the 0x prefix, lowercase):
    echo '"'"'KERNEL=="hidraw*", ATTRS{idVendor}=="0acd", ATTRS{idProduct}=="faaf", MODE="0666"'"'"' | sudo tee /etc/udev/rules.d/99-bee-gateway.rules
    sudo udevadm control --reload-rules && sudo udevadm trigger
  then unplug and replug the gateway.'
fi

# -- Backend + frontend (this terminal) ---------------------------------------------
# On Ctrl+C / exit, kill the whole process group: uvicorn --reload and npm/vite
# both spawn children that would otherwise be orphaned and keep the ports busy.
trap 'trap - INT TERM EXIT; echo; step "Stopping backend and frontend"; kill 0 2>/dev/null; wait 2>/dev/null' INT TERM EXIT

step 'Starting backend (uvicorn)'
( cd "$ROOT" && exec "$ROOT/.venv/bin/uvicorn" backend.main:app --reload ) 2>&1 \
    | sed -u 's/^/[backend]  /' &

step 'Starting frontend (vite)'
( cd "$ROOT/frontend" && exec npm run dev ) 2>&1 \
    | sed -u 's/^/[frontend] /' &

if [[ $NO_BROWSER -eq 0 ]] && command -v xdg-open >/dev/null 2>&1; then
    (
        for _ in $(seq 60); do
            if port_open 5173; then xdg-open 'http://localhost:5173' >/dev/null 2>&1; exit 0; fi
            sleep 1
        done
    ) &
fi

echo
printf '\033[32m%s\033[0m\n' 'Bee With Me is starting up:'
echo '  Backend:  http://localhost:8000  (API docs at /docs)'
echo '  Frontend: http://localhost:5173'
echo
printf '\033[90m%s\033[0m\n' 'Press Ctrl+C to stop backend and frontend.'
printf '\033[90m%s\033[0m\n' 'The database keeps running in its container until you stop it yourself:'
printf '\033[90m%s\033[0m\n' "  ${ENGINE:-podman} compose -f docker/docker-compose.yaml down"
echo

wait
