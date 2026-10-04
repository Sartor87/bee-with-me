import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .auth import hash_password
from .config import settings
from .version import APP_VERSION
from .database import close_pool, get_pool, init_pool
from .db.migrate import BackupRequiredError, MigrationError, migrate
from .routers import auth, devices, export, fire, groups, locations, users, ws, test, hardware_reader, tiles
from .routers import settings as settings_router
from .ws import manager
from .fire import poller as fire_poller
from .fire.repository import anonymise_resolved_alerts, prune_fire_data
from .fire.service import service as fire_alarm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

UPLOADS_DIR = Path(__file__).resolve().parent / 'uploads'


def _log_task_failure(task: asyncio.Task, name: str) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error('%s task crashed: %s', name, exc, exc_info=exc)


async def _cleanup_old_locations() -> None:
    first_pass = True
    while True:
        # Run once shortly after boot, then daily. A field laptop is powered down between
        # operations, so a task that sleeps 24h before its first pass never runs at all.
        await asyncio.sleep(30 if first_pass else 86_400)
        first_pass = False
        try:
            async with get_pool().acquire() as conn:
                deleted = await conn.fetchval(
                    "WITH d AS (DELETE FROM location_events"
                    " WHERE recorded_at < NOW() - ($1 || ' days')::interval RETURNING 1)"
                    " SELECT COUNT(*) FROM d",
                    str(settings.location_retention_days),
                )
                logger.info('Location cleanup: removed %d events older than %d days',
                            deleted or 0, settings.location_retention_days)
        except Exception as exc:
            logger.warning('Location cleanup failed: %s', exc)
        try:
            async with get_pool().acquire() as conn:
                anonymised = await anonymise_resolved_alerts(conn, settings.location_retention_days)
                logger.info('Fire alert anonymisation: anonymised %d resolved alerts', anonymised)
        except Exception as exc:
            logger.warning('Fire alert anonymisation failed: %s', exc)
        try:
            async with get_pool().acquire() as conn:
                pruned = await prune_fire_data(conn)
                logger.info('Fire data cleanup: removed %d hotspots, %d burnt areas, %d disabled zones',
                            pruned['fire_hotspots'], pruned['fire_burnt_areas'], pruned['fire_suppression_zones'])
        except Exception as exc:
            logger.warning('Fire data prune failed: %s', exc)


def _warn_insecure_defaults() -> None:
    """Shout about shipped-default credentials on every boot.

    Deliberately warns rather than refusing to start: this runs on a field laptop, and a
    hard failure at the moment someone is setting up for a callout is worse than an
    insecure key. Make it impossible to miss in the log instead.
    """
    problems = []
    if settings.secret_key in ('change_me', 'change_me_to_a_long_random_string'):
        problems.append('SECRET_KEY is still the example value — JWTs can be forged. Set a long random string in .env')
    if settings.offline_maps_password in ('change_me', ''):
        problems.append('OFFLINE_MAPS_PASSWORD is unset or still the example value')
    if settings.postgres_password in ('change_me', ''):
        problems.append('POSTGRES_PASSWORD is unset or still the example value — set a real database password in .env')
    if settings.enable_test_endpoints:
        problems.append('ENABLE_TEST_ENDPOINTS=true — /api/test/simulate can write fabricated positions. Never enable during a real operation')
    if not problems:
        return
    logger.error('=' * 72)
    for p in problems:
        logger.error('INSECURE CONFIG: %s', p)
    logger.error('=' * 72)


async def _run_migrations() -> None:
    """Bring the schema up to date before anything touches the database.

    Unlike the insecure-defaults check this refuses to start: running on a half-migrated schema
    would fail in confusing ways mid-operation. Each migration runs in a transaction, so a failure
    leaves the database exactly as it was before that file.
    """
    async with get_pool().acquire() as conn:
        try:
            applied = await migrate(conn)
        except BackupRequiredError as exc:
            logger.critical('=' * 72)
            logger.critical('NOT MIGRATING THE DATABASE: %s', exc)
            logger.critical('Nothing was changed. Start with start.ps1 / start.sh (they back up first), or '
                            'run scripts/backup.ps1 / scripts/backup.sh -> data/backups, then start again.')
            logger.critical('Development data only: ALLOW_MIGRATE_WITHOUT_BACKUP=true skips this check.')
            logger.critical('=' * 72)
            raise
        except MigrationError as exc:
            logger.critical('=' * 72)
            logger.critical('DATABASE MIGRATION FAILED: %s', exc)
            logger.critical('The failed migration was rolled back; the database is as it was before it.')
            logger.critical('The start script took a backup before migrating: see data/backups/.')
            logger.critical('To go back to it: stop the backend, then run scripts/restore.ps1 (Windows) or '
                            'scripts/restore.sh with that dump (see README, Backup and restore).')
            logger.critical('=' * 72)
            raise
    if applied:
        logger.info('Applied database migrations: %s', ', '.join(applied))


async def _ensure_default_admin() -> None:
    async with get_pool().acquire() as conn:
        count = await conn.fetchval('SELECT COUNT(*) FROM users')
        if count == 0:
            await conn.execute(
                """INSERT INTO users (username, password_hash, full_name, role)
                   VALUES ('admin', $1, 'Administrator', 'admin')""",
                hash_password('admin'),
            )
            logger.info('Default admin created — username: admin / password: admin')


@asynccontextmanager
async def lifespan(app: FastAPI):
    _warn_insecure_defaults()
    await init_pool()
    logger.info('Database pool ready')
    await _run_migrations()
    await _ensure_default_admin()

    notify_task  = asyncio.create_task(manager.listen_notifications())
    cleanup_task = asyncio.create_task(_cleanup_old_locations())
    fire_task = asyncio.create_task(fire_poller.run())
    fire_task.add_done_callback(lambda t: _log_task_failure(t, 'Fire poller'))
    # Hooks only signal re-evaluation; the single alarm actor does the work and never raises into a router/poller.
    fire_poller.after_refresh = fire_alarm.request_evaluation
    settings_router.after_change = fire_alarm.request_evaluation
    alarm_task = asyncio.create_task(fire_alarm.run())
    alarm_task.add_done_callback(lambda t: _log_task_failure(t, 'Fire alarm'))

    # Serial (LoRaWAN) reader — runs only when a real port is available
    serial_task = None
    try:
        from .hardware_reader.reader import run as hardware_reader_run
        serial_task = asyncio.create_task(hardware_reader_run())
        serial_task.add_done_callback(lambda t: _log_task_failure(t, 'Serial reader'))
        logger.info('Hardware reader task started')
    except Exception as exc:
        logger.warning('Serial reader not started: %s', exc)

    # HID device reader — runs only when the device is connected
    hid_task = None
    try:
        from .hardware_reader.hid_reader import run as hid_reader_run
        hid_task = asyncio.create_task(hid_reader_run())
        hid_task.add_done_callback(lambda t: _log_task_failure(t, 'HID reader'))
        logger.info('HID reader task started')
    except Exception as exc:
        logger.warning('HID reader not started: %s', exc)

    yield

    notify_task.cancel()
    cleanup_task.cancel()
    fire_task.cancel()
    alarm_task.cancel()
    if serial_task:
        serial_task.cancel()
    if hid_task:
        hid_task.cancel()
    # The alarm task may hold a pooled connection mid-tick: let its cancellation finish before the pool closes.
    with suppress(asyncio.CancelledError):
        await alarm_task
    await close_pool()


app = FastAPI(title='Bee With Me API', version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],  # tighten in production
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(groups.router)
app.include_router(devices.router)
app.include_router(locations.router)
app.include_router(export.router)
app.include_router(fire.router)
app.include_router(settings_router.router)
app.include_router(ws.router)
if settings.enable_test_endpoints:
    app.include_router(test.router)
    logger.warning('Test/simulation endpoints ENABLED — /api/test/simulate writes fabricated positions')
app.include_router(hardware_reader.router)
app.include_router(tiles.router)


UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount('/uploads', StaticFiles(directory=str(UPLOADS_DIR)), name='uploads')

Path(tiles.TILE_DIR).mkdir(parents=True, exist_ok=True)
app.mount('/tiles/bgmountains', StaticFiles(directory=tiles.TILE_DIR), name='tiles_bgmountains')


@app.get('/health')
async def health():
    return {'status': 'ok'}
