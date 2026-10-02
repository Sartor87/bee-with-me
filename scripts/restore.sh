#!/usr/bin/env bash
# Restores a Bee With Me database dump made by scripts/backup.sh or scripts/backup.ps1.
#
#   ./scripts/restore.sh [--yes] [--force] DUMP_FILE
#
# REPLACES the whole database with the dump: the database is dropped, created empty and the
# dump restored in a single transaction that stops at the first error, so nothing created after
# the backup (tables, schema_migrations rows) survives. Prints the migration status at the end.
#
# Stop the backend first: the script refuses while something listens on port 8000.
#   --yes    don't ask for confirmation
#   --force  restore even though something listens on port 8000 (the backend loses its connections)
#
# Works with Podman (default) or Docker; set CONTAINER_ENGINE to force one.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
YES=0
FORCE=0
DUMP=""

die() { echo "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1; shift ;;
    --force)  FORCE=1; shift ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "Unknown option: $1 (see --help)" ;;
    *)  [ -z "$DUMP" ] || die "Only one dump file, please (got '$DUMP' and '$1')"; DUMP="$1"; shift ;;
  esac
done
[ -n "$DUMP" ] || die "Usage: ./scripts/restore.sh [--yes] [--force] DUMP_FILE"
[ -s "$DUMP" ] || die "Dump file not found or empty: $DUMP"

# Container engine: Podman first, Docker as the alternative. Override with CONTAINER_ENGINE.
ENGINE="${CONTAINER_ENGINE:-}"
if [ -z "$ENGINE" ]; then
  if command -v podman >/dev/null 2>&1; then ENGINE=podman
  elif command -v docker >/dev/null 2>&1; then ENGINE=docker
  else die "Neither podman nor docker was found on PATH."
  fi
fi

# .env value of $1 (or $2 when unset/empty): surrounding quotes, a trailing CR and an inline
# " # comment" are not part of the value.
env_value() {
  local line value
  line="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$ROOT/.env" 2>/dev/null | tail -n1 || true)"
  value="${line#*=}"
  value="${value%$'\r'}"
  value="${value#"${value%%[![:space:]]*}"}"
  case "$value" in
    \"*) value="${value#\"}"; value="${value%%\"*}" ;;
    \'*) value="${value#\'}"; value="${value%%\'*}" ;;
    *)   value="${value%%[[:space:]]#*}"; value="${value%"${value##*[![:space:]]}"}" ;;
  esac
  printf '%s' "${value:-$2}"
}

# Read DB settings out of .env so this never drifts from the running config
DB="$(env_value POSTGRES_DB rescuer_locator)"
USER_NAME="$(env_value POSTGRES_USER rescuer)"

# Git Bash/MSYS on Windows: keep container paths (/tmp/...) as they are, hand the engine a Windows path.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) export MSYS_NO_PATHCONV=1; DUMP="$(cygpath -m "$DUMP")" ;;
esac

# Find the db container by its compose labels (project + service), like the backup script.
CONTAINER="$("$ENGINE" ps -q --filter 'label=com.docker.compose.project=bee-with-me' --filter 'label=com.docker.compose.service=db' | head -n1)"
[ -n "$CONTAINER" ] || die "Database container is not running - start it with: $ENGINE compose -p bee-with-me -f docker/docker-compose.yaml up -d"

# The backend must not write into the database while it is replaced.
if (exec 3<>/dev/tcp/127.0.0.1/8000) 2>/dev/null; then
  if [ $FORCE -eq 1 ]; then
    echo "WARNING: something is listening on port 8000 (the backend?) - restoring anyway (--force)." >&2
  else
    die "Something is listening on port 8000 - stop the backend first (Ctrl+C in its terminal), or pass --force."
  fi
fi

if [ $YES -eq 0 ]; then
  echo "This REPLACES database '$DB' in container $CONTAINER with $DUMP."
  read -r -p "Type yes to continue: " answer
  [ "$answer" = yes ] || die "Not restored."
fi

IN_CONTAINER="/tmp/beewithme_restore_$(date +%Y%m%d%H%M%S)_$$_$RANDOM.dump"
# Always remove the temp dump inside the container, also when a step fails.
trap '"$ENGINE" exec "$CONTAINER" rm -f "$IN_CONTAINER" >/dev/null 2>&1 || true' EXIT
"$ENGINE" cp "$DUMP" "$CONTAINER:$IN_CONTAINER"

echo "==> Replacing $DB ($ENGINE)"
"$ENGINE" exec "$CONTAINER" dropdb --if-exists --force -U "$USER_NAME" "$DB"
"$ENGINE" exec "$CONTAINER" createdb -U "$USER_NAME" -O "$USER_NAME" "$DB"
if ! "$ENGINE" exec "$CONTAINER" pg_restore --exit-on-error --single-transaction --no-owner -U "$USER_NAME" -d "$DB" "$IN_CONTAINER"; then
  die "pg_restore failed - nothing was restored and database '$DB' is now EMPTY. Fix the cause and run the restore again with the same dump."
fi
echo "==> Restored $DUMP"

# Show where the restored database stands (pending migrations are applied by the next start).
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$ROOT/.venv/Scripts/python.exe"
if [ -x "$PY" ]; then
  echo "==> python -m backend.db.migrate status"
  set +e
  ( cd "$ROOT" && "$PY" -m backend.db.migrate status )
  echo "    (exit $?: 0 up to date, 10 pending - applied by the next start, 2 database newer than the app)"
  set -e
else
  echo "No .venv found - check the state later with: python -m backend.db.migrate status"
fi
