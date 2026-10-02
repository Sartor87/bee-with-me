#!/usr/bin/env bash
# Dumps the Bee With Me database to a timestamped file.
#
# Run it after every operation, and on a schedule between them. The whole record of a
# callout lives in one Docker volume on one machine; this is the only thing standing
# between a disk failure and losing it.
#
#   ./scripts/backup.sh [OUT_DIR] [KEEP]
#
# OUT_DIR should be a USB stick or a second drive — a backup on the same disk as the
# database is not a backup. Defaults to ./backups, KEEP defaults to 30.
#
# Works with Podman (default) or Docker; set CONTAINER_ENGINE to force one. Dumps are pg_dump custom format (.dump); restore with pg_restore (hint printed at the end).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-$ROOT/backups}"
KEEP="${2:-30}"

# Container engine: Podman first, Docker as the alternative. Override with CONTAINER_ENGINE.
ENGINE="${CONTAINER_ENGINE:-}"
if [ -z "$ENGINE" ]; then
  if command -v podman >/dev/null 2>&1; then ENGINE=podman
  elif command -v docker >/dev/null 2>&1; then ENGINE=docker
  else echo "Neither podman nor docker was found on PATH." >&2; exit 1
  fi
fi

# Read DB settings out of .env so this never drifts from the running config
DB="$(grep -E '^\s*POSTGRES_DB\s*=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- | xargs || echo rescuer_locator)"
USER_NAME="$(grep -E '^\s*POSTGRES_USER\s*=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- | xargs || echo rescuer)"
DB="${DB:-rescuer_locator}"
USER_NAME="${USER_NAME:-rescuer}"

# Git Bash/MSYS on Windows: stop it rewriting container paths (/tmp/...) into Windows paths,
# and give the engine the output folder as a Windows path it understands.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) export MSYS_NO_PATHCONV=1; OUT_DIR="$(cygpath -m "$OUT_DIR")" ;;
esac

mkdir -p "$OUT_DIR"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
TARGET="$OUT_DIR/beewithme_$STAMP.dump"

# Find the db container by its compose label: works for podman and docker compose alike.
CONTAINER="$("$ENGINE" ps -q --filter 'label=com.docker.compose.service=db' | head -n1)"
if [ -z "$CONTAINER" ]; then
  echo "Database container is not running - start it with: $ENGINE compose -f docker/docker-compose.yaml up -d" >&2
  exit 1
fi

# Custom format (-Fc): binary and compressed; written inside the container, then copied out.
IN_CONTAINER="/tmp/beewithme_$STAMP.dump"
echo "==> Dumping $DB to $TARGET ($ENGINE)"
"$ENGINE" exec "$CONTAINER" pg_dump -Fc -U "$USER_NAME" -d "$DB" -f "$IN_CONTAINER"
"$ENGINE" cp "$CONTAINER:$IN_CONTAINER" "$TARGET"
"$ENGINE" exec "$CONTAINER" rm -f "$IN_CONTAINER" >/dev/null

if [ ! -s "$TARGET" ]; then
  echo "Dump is empty - check the container logs" >&2
  rm -f "$TARGET"
  exit 1
fi
echo "==> Wrote $(du -h "$TARGET" | cut -f1)"

# Prune old dumps, keeping the most recent $KEEP (older plain .sql dumps are left alone)
ls -1t "$OUT_DIR"/beewithme_*.dump 2>/dev/null | tail -n "+$((KEEP + 1))" | while read -r old; do
  echo "    pruning $(basename "$old")"
  rm -f "$old"
done

echo
echo "To restore (replaces the current data):"
echo "  $ENGINE cp $TARGET $CONTAINER:/tmp/restore.dump"
echo "  $ENGINE exec $CONTAINER pg_restore --clean --if-exists -U $USER_NAME -d $DB /tmp/restore.dump"
