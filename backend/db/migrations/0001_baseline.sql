-- migrate:up
-- Baseline: the complete schema of v1.7.1, written to be safe on ANY existing database.
-- Fresh installs get everything from the CREATE statements; databases created from an older
-- schema.sql get the missing pieces from the IF NOT EXISTS / ADD COLUMN lines. This file is the
-- single source of truth for the schema; backend/db/schema.sql no longer exists.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

DO $$ BEGIN
    CREATE TYPE app_role AS ENUM ('admin', 'rescuer', 'viewer');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE device_type AS ENUM ('bee', 'repeater');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS users (
    id             UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    username       VARCHAR(64)  UNIQUE,
    password_hash  VARCHAR(255),
    pin            VARCHAR(20),
    pin_hash       VARCHAR(255),
    first_name     VARCHAR(128) NOT NULL DEFAULT '',
    last_name      VARCHAR(128) NOT NULL DEFAULT '',
    full_name      VARCHAR(255) NOT NULL,
    email          VARCHAR(255),
    phone          VARCHAR(64)  NOT NULL DEFAULT '',
    rank           VARCHAR(64),
    blood_type     VARCHAR(5),
    photo_url      VARCHAR(500),
    notes          TEXT,
    is_radio_enthusiast BOOLEAN NOT NULL DEFAULT FALSE,
    radio_initials      VARCHAR(20),
    role           app_role     NOT NULL DEFAULT 'viewer',
    is_active      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
-- added in 1.2.1
ALTER TABLE users ADD COLUMN IF NOT EXISTS pin VARCHAR(20);
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_radio_enthusiast BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS radio_initials VARCHAR(20);

CREATE TABLE IF NOT EXISTS groups (
    id           UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    name         VARCHAR(255) UNIQUE NOT NULL,
    description  TEXT,
    organization VARCHAR(255),
    color        VARCHAR(7)   NOT NULL DEFAULT '#3388ff',
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_groups (
    user_id    UUID        NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    group_id   UUID        NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    is_leader  BOOLEAN     NOT NULL DEFAULT FALSE,
    joined_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, group_id)
);

CREATE TABLE IF NOT EXISTS devices (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    dev_sn      INTEGER     UNIQUE NOT NULL,
    name        VARCHAR(255),
    device_type device_type NOT NULL DEFAULT 'bee',
    user_id     UUID        REFERENCES users(id) ON DELETE SET NULL,
    assigned_at TIMESTAMPTZ,
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- added in 1.6.x (trail cut-off on reassignment)
ALTER TABLE devices ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS location_events (
    id               BIGSERIAL    PRIMARY KEY,
    device_id        UUID         NOT NULL REFERENCES devices(id),
    user_id          UUID         REFERENCES users(id),
    msg_id           SMALLINT     NOT NULL,
    recorded_at      TIMESTAMPTZ  NOT NULL,
    received_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    position         GEOMETRY(Point, 4326) NOT NULL,
    latitude         DOUBLE PRECISION NOT NULL,
    longitude        DOUBLE PRECISION NOT NULL,
    mgrs             VARCHAR(20)  NOT NULL,
    altitude_m       SMALLINT,
    speed_knots      REAL,
    course_deg       SMALLINT,
    gnss_satellites  SMALLINT,
    battery_voltage  REAL,
    sos_active       BOOLEAN      NOT NULL DEFAULT FALSE,
    repeater_mode    BOOLEAN      NOT NULL DEFAULT FALSE,
    raw_flags        SMALLINT,
    gnss_valid       BOOLEAN      NOT NULL DEFAULT TRUE
);
-- added in 1.7.0
ALTER TABLE location_events ADD COLUMN IF NOT EXISTS gnss_valid BOOLEAN NOT NULL DEFAULT TRUE;

CREATE TABLE IF NOT EXISTS repeater_events (
    id              BIGSERIAL   PRIMARY KEY,
    device_id       UUID        NOT NULL REFERENCES devices(id),
    msg_id          SMALLINT    NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    battery_voltage REAL
);

CREATE TABLE IF NOT EXISTS sos_alerts (
    id           UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    device_id    UUID        NOT NULL REFERENCES devices(id),
    user_id      UUID        REFERENCES users(id),
    triggered_at TIMESTAMPTZ NOT NULL,
    resolved_at  TIMESTAMPTZ,
    resolved_by  UUID        REFERENCES users(id),
    notes        TEXT
);

CREATE INDEX IF NOT EXISTS idx_location_events_device_time     ON location_events (device_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_location_events_user_time       ON location_events (user_id,   recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_location_events_sos             ON location_events (sos_active) WHERE sos_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_location_events_position        ON location_events USING GIST (position);
CREATE INDEX IF NOT EXISTS idx_location_events_device_received ON location_events (device_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_sos_alerts_open                 ON sos_alerts (device_id) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_sos_alerts_triggered            ON sos_alerts (triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_groups_group_id            ON user_groups (group_id);
CREATE INDEX IF NOT EXISTS idx_devices_user_id                 ON devices (user_id);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_users_updated_at ON users;
CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_groups_updated_at ON groups;
CREATE TRIGGER trg_groups_updated_at
    BEFORE UPDATE ON groups
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- migrate:down
-- Forward-only. To undo, restore the backup taken before migrating (data/backups/).
