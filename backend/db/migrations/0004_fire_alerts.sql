-- migrate:up
-- Fire proximity alarm: suppression zones (false-positive areas) and alerts (the operation record).

CREATE TYPE fire_alert_target         AS ENUM ('hq', 'rescuer');
CREATE TYPE fire_alert_resolve_reason AS ENUM ('aged_out', 'out_of_range', 'dismissed', 'suppressed', 'disabled');

CREATE TABLE fire_suppression_zones (
    id          UUID             PRIMARY KEY DEFAULT uuid_generate_v7(),
    label       VARCHAR(255)     NOT NULL,
    latitude    DOUBLE PRECISION NOT NULL CHECK (latitude  BETWEEN -90  AND 90),
    longitude   DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    radius_m    INTEGER          NOT NULL DEFAULT 1000 CHECK (radius_m BETWEEN 50 AND 20000),
    is_active   BOOLEAN          NOT NULL DEFAULT TRUE,
    disabled_at TIMESTAMPTZ,
    disabled_by UUID             REFERENCES users(id) ON DELETE SET NULL,
    notes       TEXT,
    created_by  UUID             REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    CHECK (is_active = (disabled_at IS NULL))
);

CREATE TRIGGER trg_fire_suppression_zones_updated_at
    BEFORE UPDATE ON fire_suppression_zones
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE fire_hotspots
    ADD COLUMN suppressed_by_zone_id UUID REFERENCES fire_suppression_zones(id) ON DELETE SET NULL;

CREATE TABLE fire_alerts (
    id               UUID              PRIMARY KEY DEFAULT uuid_generate_v7(),
    hotspot_id       UUID              NOT NULL REFERENCES fire_hotspots(id) ON DELETE RESTRICT,
    target_type      fire_alert_target NOT NULL,
    device_id        UUID              REFERENCES devices(id) ON DELETE SET NULL,
    user_id          UUID              REFERENCES users(id)   ON DELETE SET NULL,
    distance_m       INTEGER           NOT NULL,
    triggered_at     TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    last_notified_at TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    acknowledged_at  TIMESTAMPTZ,
    acknowledged_by  UUID              REFERENCES users(id) ON DELETE SET NULL,
    resolved_at      TIMESTAMPTZ,
    resolved_by      UUID              REFERENCES users(id) ON DELETE SET NULL,
    resolve_reason   fire_alert_resolve_reason,
    notes            TEXT,
    CHECK (target_type = 'rescuer' OR device_id IS NULL),
    CHECK ((resolved_at IS NULL) = (resolve_reason IS NULL))
);

-- Uniqueness only among OPEN alerts: a target that leaves and re-enters range alarms again, and
-- resolved rows never collide when a deleted device's device_id is set to NULL.
CREATE UNIQUE INDEX idx_fire_alerts_open_hq      ON fire_alerts (hotspot_id)
    WHERE target_type = 'hq' AND resolved_at IS NULL;
CREATE UNIQUE INDEX idx_fire_alerts_open_rescuer ON fire_alerts (hotspot_id, device_id)
    WHERE target_type = 'rescuer' AND resolved_at IS NULL;
CREATE INDEX idx_fire_alerts_triggered ON fire_alerts (triggered_at DESC);

-- migrate:down
-- Forward-only. To undo, restore the backup taken before migrating (data/backups/).
