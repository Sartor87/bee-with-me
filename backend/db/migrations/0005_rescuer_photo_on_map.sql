-- migrate:up
-- Operator toggle: show rescuer photos as map markers (default on; the photo URL is already in the live feed).
ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS is_rescuer_photo_on_map_enabled BOOLEAN NOT NULL DEFAULT TRUE;

-- migrate:down
-- Forward-only. To undo, restore the backup taken before migrating (data/backups/).
