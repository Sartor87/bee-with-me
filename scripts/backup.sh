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
# Works with Podman (default) or Docker; set CONTAINER_ENGINE to force one. Dumps are pg_dump custom format (.dump); restore with scripts/restore.sh, which runs pg_restore (hint printed at the end).

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

# Dumps hold every position and name of a callout: readable by this user only.
umask 077
mkdir -p "$OUT_DIR"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
TARGET="$OUT_DIR/beewithme_$STAMP.dump"

# Find the db container by its compose labels (project + service): works for podman and docker
# compose alike, and never picks another compose project's `db` service.
CONTAINER="$("$ENGINE" ps -q --filter 'label=com.docker.compose.project=bee-with-me' --filter 'label=com.docker.compose.service=db' | head -n1)"
if [ -z "$CONTAINER" ]; then
  echo "Database container is not running - start it with: $ENGINE compose -f docker/docker-compose.yaml up -d" >&2
  exit 1
fi

# Custom format (-Fc): binary and compressed; written inside the container, then copied out.
IN_CONTAINER="/tmp/beewithme_$STAMP.dump"
# Always remove the temp dump inside the container, also when the dump or the copy fails.
trap '"$ENGINE" exec "$CONTAINER" rm -f "$IN_CONTAINER" >/dev/null 2>&1 || true' EXIT

# State for the backup marker (last-backup.json, see below), read just before the dump: which server
# this is and which migrations it has. The backend migrates only after a backup of this exact state.
psql_at() { "$ENGINE" exec "$CONTAINER" psql -U "$USER_NAME" -d "$DB" -Atc "$1" | tr -d '\r'; }
CREATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SYSTEM_ID="$(psql_at 'SELECT system_identifier FROM pg_control_system()')"
[[ "$SYSTEM_ID" =~ ^[0-9]+$ ]] || { echo "Could not read the database's system_identifier ($SYSTEM_ID)" >&2; exit 1; }
APPLIED=""
if [ "$(psql_at "SELECT to_regclass('public.schema_migrations') IS NOT NULL")" = t ]; then
  APPLIED="$(psql_at 'SELECT version FROM schema_migrations ORDER BY version' | paste -sd, -)"
fi
[[ "$APPLIED" =~ ^[0-9,]*$ ]] || { echo "Unexpected schema_migrations versions: $APPLIED" >&2; exit 1; }

echo "==> Dumping $DB to $TARGET ($ENGINE)"
"$ENGINE" exec "$CONTAINER" pg_dump -Fc -U "$USER_NAME" -d "$DB" -f "$IN_CONTAINER"
"$ENGINE" cp "$CONTAINER:$IN_CONTAINER" "$TARGET"
chmod 600 "$TARGET"   # the engine copies the container file's mode (0644)

if [ ! -s "$TARGET" ]; then
  echo "Dump is empty - check the container logs" >&2
  rm -f "$TARGET"
  exit 1
fi
echo "==> Wrote $(du -h "$TARGET" | cut -f1)"

# Backup marker next to the dump: the backend applies pending migrations only when the marker in
# data/backups (BACKUP_MARKER_PATH) is of this server, in its current state, and under 24 h old.
MARKER="$OUT_DIR/last-backup.json"
APPLIED_JSON="$(printf '%s' "$APPLIED" | awk -F, '{ for (i = 1; i <= NF; i++) printf "%s\"%s\"", (i > 1 ? ", " : ""), $i }')"
printf '{"system_identifier": "%s", "applied": [%s], "dump": "%s", "created_at": "%s"}\n' \
  "$SYSTEM_ID" "$APPLIED_JSON" "$(basename "$TARGET")" "$CREATED_AT" > "$MARKER.tmp"
mv -f "$MARKER.tmp" "$MARKER"
echo "==> Marker $MARKER"

# Prune old dumps, keeping the most recent $KEEP (older plain .sql dumps are left alone)
ls -1t "$OUT_DIR"/beewithme_*.dump 2>/dev/null | tail -n "+$((KEEP + 1))" | while read -r old; do
  echo "    pruning $(basename "$old")"
  rm -f "$old"
done

echo
echo "To restore (replaces the whole database; stop the backend first):"
echo "  \"$ROOT/scripts/restore.sh\" \"$TARGET\""
