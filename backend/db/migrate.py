"""
Database migration runner.

Migrations are numbered SQL files in backend/db/migrations/ (NNNN_name.sql) with dbmate-style
markers; only the `-- migrate:up` section runs. Each file runs in its own transaction under a
transaction-scoped advisory lock, so a failing file leaves the database as it was and two backend
processes starting at once apply every file exactly once. Applied versions and a SHA-256 checksum
are recorded in schema_migrations.

The checksum covers the up section only: the text between `-- migrate:up` and `-- migrate:down`
with every line ending (CRLF, CR) normalised to LF and trailing whitespace of the section stripped.
A Windows (CRLF) and a Linux (LF) checkout of the same file therefore have the same checksum. The
down section is documentation (it never runs), so editing it never changes the checksum; any edit
of the up section, including a comment, does and is logged as "changed after apply".

    python -m backend.db.migrate status   # exit 0 up to date, 10 pending, 2 database is newer,
                                          # 3 database not reachable, 1 bad files / failed migration
    python -m backend.db.migrate up

Pending migrations are applied (by `up` and at start-up) only when the database has no user tables
yet (fresh install), when ALLOW_MIGRATE_WITHOUT_BACKUP=true (dev data only; logs a WARNING), or when
the backup marker (settings.backup_marker_path, written by scripts/backup.ps1 / backup.sh) shows a
backup of THIS server (same system_identifier) and database (same name) in its current state (same
applied versions), taken less than 24 h ago (and not in the future), and its dump (a plain file name
next to the marker) exists and is not empty. `status` never checks the marker.

Because the runner owns the transaction, a migration file must not contain transaction control
(BEGIN, START TRANSACTION, COMMIT, END, ROLLBACK, ABORT, SAVEPOINT, RELEASE, PREPARE TRANSACTION)
outside dollar-quoted bodies; load_migrations rejects such files. For the same reason, statements
that cannot run inside a transaction block are not supported: CREATE INDEX CONCURRENTLY, and
ALTER TYPE ... ADD VALUE when the new value is used in the same file.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / 'migrations'
LOCK_KEY = 7_342_001

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NEWER_DB = 2
EXIT_UNREACHABLE = 3   # could not connect (Postgres down or still starting): worth retrying
EXIT_PENDING = 10

BACKUP_MAX_AGE = timedelta(hours=24)
# Names the backup scripts give their dumps: beewithme_<stamp>_<suffix>.dump
_BACKUP_DUMP_NAME = re.compile(r'beewithme_[A-Za-z0-9_-]+\.dump')

_NAME_RE = re.compile(r'^(\d{4})_[a-z0-9_]+\.sql$')
_UP_RE = re.compile(r'^--\s*migrate:up\s*$', re.MULTILINE)
_DOWN_RE = re.compile(r'^--\s*migrate:down\s*$', re.MULTILINE)
_DOLLAR_TAG_RE = re.compile(r'\$([A-Za-z_][A-Za-z0-9_]*)?\$')
_TX_CONTROL_RE = re.compile(
    r'^(BEGIN|START\s+TRANSACTION|COMMIT|END|ROLLBACK|ABORT|SAVEPOINT|RELEASE|PREPARE\s+TRANSACTION)\b',
    re.IGNORECASE,
)

_CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version    VARCHAR(16) PRIMARY KEY,
        checksum   CHAR(64)    NOT NULL,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
"""


class MigrationError(RuntimeError):
    pass


class BackupRequiredError(MigrationError):
    """Pending migrations, existing data, and no valid backup marker for this database."""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    up_sql: str
    checksum: str


@dataclass(frozen=True)
class Status:
    applied: list[str]
    pending: list[str]
    unknown: list[str]
    changed: list[str]


def _top_level_sql(sql: str) -> str:
    """The SQL with comments, string literals, quoted identifiers and dollar-quoted bodies blanked out."""
    out = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if sql.startswith('--', i):
            end = sql.find('\n', i)
            i = n if end == -1 else end
            continue
        if sql.startswith('/*', i):
            end = sql.find('*/', i + 2)
            i = n if end == -1 else end + 2
            out.append(' ')
            continue
        if ch == "'":
            escapes = i > 0 and sql[i - 1] in 'eE' and (i == 1 or not (sql[i - 2].isalnum() or sql[i - 2] == '_'))
            j = i + 1
            while j < n:
                if escapes and sql[j] == '\\':
                    j += 2
                    continue
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
            out.append(' ')
            continue
        if ch == '"':
            end = sql.find('"', i + 1)
            i = n if end == -1 else end + 1
            out.append(' ')
            continue
        if ch == '$' and not (i > 0 and (sql[i - 1].isalnum() or sql[i - 1] == '_')):
            tag = _DOLLAR_TAG_RE.match(sql, i)
            if tag:
                end = sql.find(tag.group(0), tag.end())
                i = n if end == -1 else end + len(tag.group(0))
                out.append(' ')
                continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _check_no_transaction_control(filename: str, up_sql: str) -> None:
    for statement in _top_level_sql(up_sql).split(';'):
        statement = statement.strip()
        if _TX_CONTROL_RE.match(statement):
            raise MigrationError(
                f'{filename}: transaction control statement {statement.split()[0].upper()!r} is not '
                f'allowed; the runner wraps each file in its own transaction'
            )


def _normalise_newlines(text: str) -> str:
    """CRLF and lone CR become LF, so the checkout's line endings never affect parsing or checksums."""
    return text.replace('\r\n', '\n').replace('\r', '\n')


def load_migrations(directory: Path | None = None) -> list[Migration]:
    directory = directory or MIGRATIONS_DIR
    migrations = []
    for path in sorted(directory.glob('*.sql')):
        match = _NAME_RE.match(path.name)
        if not match:
            raise MigrationError(f'Bad migration file name {path.name!r}: expected NNNN_lower_snake.sql')
        text = _normalise_newlines(path.read_bytes().decode('utf-8'))
        up = _UP_RE.search(text)
        if not up:
            raise MigrationError(f'{path.name}: missing "-- migrate:up" marker')
        down = _DOWN_RE.search(text, up.end())
        up_section = text[up.end():down.start() if down else len(text)]
        up_sql = up_section.strip()
        _check_no_transaction_control(path.name, up_sql)
        checksum = hashlib.sha256(up_section.rstrip().encode('utf-8')).hexdigest()
        migrations.append(Migration(match.group(1), path.stem, up_sql, checksum))
    versions = [mig.version for mig in migrations]
    duplicates = sorted({v for v in versions if versions.count(v) > 1})
    if duplicates:
        raise MigrationError(f'Duplicate migration version(s): {", ".join(duplicates)}')
    return migrations


async def status(conn: asyncpg.Connection, migrations: list[Migration] | None = None) -> Status:
    migrations = load_migrations() if migrations is None else migrations
    exists = await conn.fetchval("SELECT to_regclass('public.schema_migrations') IS NOT NULL")
    rows = await conn.fetch('SELECT version, checksum FROM schema_migrations') if exists else []
    applied = {r['version']: r['checksum'].strip() for r in rows}
    known = {mig.version: mig for mig in migrations}
    return Status(
        applied=sorted(applied),
        pending=[mig.version for mig in migrations if mig.version not in applied],
        unknown=sorted(v for v in applied if v not in known),
        changed=sorted(v for v, c in applied.items() if v in known and known[v].checksum != c),
    )


async def migrate(conn: asyncpg.Connection, migrations: list[Migration] | None = None) -> list[str]:
    migrations = load_migrations() if migrations is None else migrations
    current = await status(conn, migrations)
    if current.unknown:
        raise MigrationError(
            f'The database is newer than this version of the app (unknown migrations: '
            f'{", ".join(current.unknown)}). Update the app instead of starting an older one.'
        )
    for version in current.changed:
        logger.warning('Migration %s was changed after it was applied; the change is NOT re-applied', version)
    await _require_backup(conn, current)

    applied_now = []
    for mig in migrations:
        async with conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock($1)', LOCK_KEY)
            await conn.execute(_CREATE_TABLE)
            already = await conn.fetchval('SELECT 1 FROM schema_migrations WHERE version = $1', mig.version)
            if already:
                continue
            try:
                await conn.execute(mig.up_sql)
            except asyncpg.PostgresError as exc:
                raise MigrationError(f'Migration {mig.name} failed: {exc}') from exc
            await conn.execute(
                'INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)',
                mig.version, mig.checksum,
            )
        applied_now.append(mig.version)
        logger.info('Applied migration %s', mig.name)
    return applied_now


_USER_TABLES_SQL = """
    SELECT EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%'
          -- tables an extension created (postgis spatial_ref_sys, topology, tiger ...) are not data
          AND NOT EXISTS (SELECT 1 FROM pg_depend d
                          WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
    )
"""


async def _require_backup(conn: asyncpg.Connection, current: Status) -> None:
    """Refuse pending migrations on a database with data unless a matching, recent backup exists."""
    if not current.pending:
        return
    if not await conn.fetchval(_USER_TABLES_SQL):
        return   # fresh install: nothing to lose
    from ..config import settings
    pending = ', '.join(current.pending)
    if settings.allow_migrate_without_backup:
        logger.warning('ALLOW_MIGRATE_WITHOUT_BACKUP=true: applying migration(s) %s WITHOUT checking for a '
                       'backup - only acceptable on development data', pending)
        return
    problem = await _backup_marker_problem(conn, Path(settings.backup_marker_path), current.applied)
    if problem:
        raise BackupRequiredError(
            f'Not applying pending migration(s) {pending}: they need a backup of THIS database first '
            f'({problem}). Run scripts/backup.ps1 (Windows) or scripts/backup.sh into data/backups, or '
            f'start with start.ps1 / start.sh, which back up before migrating; see README, Backup and restore.'
        )


async def _backup_marker_problem(conn: asyncpg.Connection, marker_path: Path, applied: list[str]) -> str | None:
    """None when the marker shows a backup of this server in its current state, else what is wrong."""
    try:
        marker = json.loads(marker_path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return f'no backup marker at {marker_path}'
    except (OSError, ValueError) as exc:
        return f'backup marker {marker_path} is unreadable: {exc}'
    if not isinstance(marker, dict):
        return f'backup marker {marker_path} is not a JSON object'
    try:
        here = str(await conn.fetchval('SELECT system_identifier FROM pg_control_system()'))
    except asyncpg.PostgresError as exc:
        return f"cannot read this server's system_identifier: {exc}"
    if str(marker.get('system_identifier', '')).strip() != here:
        return (f'the last backup ({marker.get("dump", "?")}) is of another database server '
                f'(system_identifier {marker.get("system_identifier")!r}, this one is {here})')
    try:
        this_db = await conn.fetchval('SELECT current_database()')
    except asyncpg.PostgresError as exc:
        return f"cannot read this connection's database name: {exc}"
    if marker.get('database') != this_db:
        return (f'the last backup ({marker.get("dump", "?")}) is of database {marker.get("database")!r}, '
                f'this one is {this_db!r}')
    dump = marker.get('dump')
    if not isinstance(dump, str) or not dump or dump in ('.', '..') or any(c in dump for c in '/\\:') \
            or '..' in dump:
        return f'backup marker dump {dump!r} is not a plain file name'
    if not _BACKUP_DUMP_NAME.fullmatch(dump):
        return f'backup marker dump {dump!r} is not a backup-script dump (beewithme_*.dump)'
    dump_path = marker_path.parent / dump
    try:
        if not dump_path.is_file() or dump_path.stat().st_size == 0:
            return f'the dump named by the backup marker ({dump_path}) is missing or empty'
        with dump_path.open('rb') as fh:
            magic = fh.read(5)
    except OSError as exc:
        return f'the dump named by the backup marker ({dump_path}) cannot be read: {exc}'
    if magic != b'PGDMP':
        return f'the dump named by the backup marker ({dump_path}) is not a pg_dump custom-format dump (no PGDMP header)'
    marked = marker.get('applied')
    if not isinstance(marked, list) or sorted(str(v) for v in marked) != sorted(applied):
        return (f'the last backup ({marker.get("dump", "?")}) was taken with applied migrations {marked!r}, '
                f'the database now has {applied!r}')
    try:
        created = datetime.fromisoformat(str(marker.get('created_at', '')).replace('Z', '+00:00'))
    except ValueError:
        return f'backup marker created_at {marker.get("created_at")!r} is not an ISO timestamp'
    if created.tzinfo is None:
        return f'backup marker created_at {marker.get("created_at")!r} has no time zone'
    age = datetime.now(timezone.utc) - created
    if age > BACKUP_MAX_AGE or age < -timedelta(minutes=5):
        return f'the last backup ({marker.get("dump", "?")}, {marker.get("created_at")}) is not from the last 24 h'
    return None


def _connect_kwargs() -> dict:
    from ..config import settings
    return dict(
        host=settings.postgres_host, port=settings.postgres_port,
        user=settings.postgres_user, password=settings.postgres_password,
        database=settings.postgres_db,
    )


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='python -m backend.db.migrate')
    parser.add_argument('command', choices=['status', 'up'])
    args = parser.parse_args(argv)
    try:
        conn = await asyncpg.connect(**_connect_kwargs(), timeout=10)
    except (OSError, asyncpg.PostgresError, asyncio.TimeoutError) as exc:
        print(f'Cannot connect to the database: {exc}', file=sys.stderr)
        return EXIT_UNREACHABLE
    try:
        if args.command == 'status':
            st = await status(conn)
            print(f'applied: {", ".join(st.applied) or "-"}')
            print(f'pending: {", ".join(st.pending) or "-"}')
            if st.changed:
                print(f'changed after apply (not re-applied): {", ".join(st.changed)}')
            if st.unknown:
                print(f'unknown (database is newer than this app): {", ".join(st.unknown)}')
                return EXIT_NEWER_DB
            return EXIT_PENDING if st.pending else EXIT_OK
        done = await migrate(conn)
        print(f'applied: {", ".join(done) or "nothing to do"}')
        return EXIT_OK
    except MigrationError as exc:
        # Bad file names, missing markers, a failing file or a database newer than the app.
        print(str(exc), file=sys.stderr)
        return EXIT_FAILED
    finally:
        await conn.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    sys.exit(asyncio.run(main(sys.argv[1:])))
