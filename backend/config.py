from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import BaseModel, field_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    postgres_host: str = 'localhost'
    postgres_port: int = 5432
    postgres_db: str = 'rescuer_locator'
    postgres_user: str = 'rescuer'
    postgres_password: str = 'change_me'

    secret_key: str = 'change_me'
    refresh_token_expire_days: int = 7
    access_token_expire_minutes: int = 60

    # Dev-only simulation endpoints (POST /api/test/simulate) write fabricated positions
    # into location_events. Off by default so a field deployment can't be polluted.
    enable_test_endpoints: bool = False

    # GET /api/locations/live ignores devices whose last fix is older than this, so
    # trackers from a previous operation don't linger on the map as ghosts.
    live_position_max_age_hours: int = 24

    serial_port: str = '/dev/ttyUSB0'
    serial_baud: int = 9600

    hid_vendor_id:  int = 0x0ACD
    hid_product_id: int = 0xFAAF

    location_retention_days: int = 90

    offline_maps_password: str = 'change_me'

    # Pending migrations are applied only after a backup of THIS database in its current state:
    # the backup scripts write this marker next to the dump (start.ps1/start.sh back up into
    # data/backups). Resolved relative to the project folder, like uploads/.
    backup_marker_path: Path = Path(__file__).resolve().parent.parent / 'data' / 'backups' / 'last-backup.json'
    # Developers on dev data only (e.g. uvicorn --reload): migrate without that backup, with a WARNING.
    allow_migrate_without_backup: bool = False

    @field_validator("backup_marker_path")
    def resolve_marker_path(cls, v):
        # a relative BACKUP_MARKER_PATH is relative to the project folder, not to the working directory
        return v if v.is_absolute() else Path(__file__).resolve().parent.parent / v

    @field_validator("hid_vendor_id", "hid_product_id", mode="before")
    def parse_int(cls, v):
        if isinstance(v, str):
            return int(v, 16) if v.startswith("0x") else int(v)
        return v

    @field_validator("hid_vendor_id", "hid_product_id")
    def validate_range(cls, v, info):
        if not (0 <= v <= 0xFFFF):
            raise ValueError(f"{info.field_name} must be 0–65535")
        return v


settings = Settings()
