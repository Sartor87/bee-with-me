-- migrate:up
-- Fire data from Copernicus EFFIS/GWIS plus operator field reports. No PostGIS types: points are
-- latitude/longitude columns and burnt-area polygons are GeoJSON in JSONB, so this feature does
-- not deepen the PostGIS dependency.

-- RFC 9562 UUIDv7: 48-bit Unix milliseconds, version 7, variant 10, random rest.
-- Replace with the native uuidv7() once on PostgreSQL 18+.
CREATE OR REPLACE FUNCTION uuid_generate_v7() RETURNS uuid
LANGUAGE plpgsql VOLATILE AS $$
DECLARE
    unix_ms bigint := floor(extract(epoch FROM clock_timestamp()) * 1000);
    bytes   bytea  := uuid_send(gen_random_uuid());
BEGIN
    bytes := overlay(bytes PLACING substring(int8send(unix_ms) FROM 3) FROM 1 FOR 6);
    bytes := set_byte(bytes, 6, (get_byte(bytes, 6) & 15) | 112);
    RETURN encode(bytes, 'hex')::uuid;
END
$$;

CREATE TYPE fire_data_source AS ENUM ('viirs', 'modis', 'field_report');

CREATE TABLE fire_hotspots (
    id                 UUID             PRIMARY KEY DEFAULT uuid_generate_v7(),
    source             fire_data_source NOT NULL,
    effis_id           TEXT,
    acquired_at        TIMESTAMPTZ      NOT NULL,
    latitude           DOUBLE PRECISION NOT NULL CHECK (latitude  BETWEEN -90  AND 90),
    longitude          DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    h3_r8              BIGINT,
    effis_class        TEXT,
    first_seen_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    last_seen_at       TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    dismissed_at       TIMESTAMPTZ,
    dismissed_by       UUID             REFERENCES users(id)   ON DELETE SET NULL,
    dismiss_notes      TEXT,
    reported_by        UUID             REFERENCES users(id)   ON DELETE SET NULL,
    reported_device_id UUID             REFERENCES devices(id) ON DELETE SET NULL,
    extinguished_at    TIMESTAMPTZ,
    extinguished_by    UUID             REFERENCES users(id)   ON DELETE SET NULL,
    notes              TEXT,
    created_at         TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    UNIQUE (source, effis_id),
    CHECK ((source = 'field_report') = (effis_id IS NULL))
);
CREATE INDEX idx_fire_hotspots_acquired_at  ON fire_hotspots (acquired_at DESC);
CREATE INDEX idx_fire_hotspots_last_seen_at ON fire_hotspots (last_seen_at);

CREATE TABLE fire_burnt_areas (
    id            UUID             PRIMARY KEY DEFAULT uuid_generate_v7(),
    source        fire_data_source NOT NULL CHECK (source <> 'field_report'),
    effis_id      TEXT             NOT NULL,
    effis_fire_id TEXT,
    started_at    TIMESTAMPTZ,
    ended_at      TIMESTAMPTZ,
    area_ha       REAL,
    geometry      JSONB            NOT NULL,
    first_seen_at TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    last_seen_at  TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    UNIQUE (source, effis_id)
);
CREATE INDEX idx_fire_burnt_areas_last_seen_at ON fire_burnt_areas (last_seen_at);

-- migrate:down
-- Forward-only. To undo, restore the backup taken before migrating (data/backups/).
