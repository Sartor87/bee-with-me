-- migrate:up
-- Operation-wide settings (single row). HQ moves here from per-browser localStorage.
CREATE TABLE settings (
    id                       SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    hq_latitude              DOUBLE PRECISION CHECK (hq_latitude  BETWEEN -90  AND 90),
    hq_longitude             DOUBLE PRECISION CHECK (hq_longitude BETWEEN -180 AND 180),
    is_hq_alarm_enabled      BOOLEAN  NOT NULL DEFAULT TRUE,
    is_rescuer_alarm_enabled BOOLEAN  NOT NULL DEFAULT TRUE,
    hq_radius_m              INTEGER  NOT NULL DEFAULT 10000 CHECK (hq_radius_m      BETWEEN 100 AND 100000),
    rescuer_radius_m         INTEGER  NOT NULL DEFAULT 3000  CHECK (rescuer_radius_m BETWEEN 100 AND 100000),
    alarm_max_age_hours      SMALLINT NOT NULL DEFAULT 24    CHECK (alarm_max_age_hours BETWEEN 1 AND 168),
    repeat_minutes           SMALLINT NOT NULL DEFAULT 5     CHECK (repeat_minutes BETWEEN 1 AND 60),
    updated_by               UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((hq_latitude IS NULL) = (hq_longitude IS NULL))
);

CREATE TRIGGER trg_settings_updated_at
    BEFORE UPDATE ON settings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

INSERT INTO settings DEFAULT VALUES ON CONFLICT DO NOTHING;

-- migrate:down
-- Forward-only. To undo, restore the backup taken before migrating (data/backups/).
