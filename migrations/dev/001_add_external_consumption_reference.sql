-- DEVELOPMENT ONLY: add idempotent external-source references to cement consumption.
-- Apply only to an isolated development database copy after taking a backup.
-- Do NOT run against production as part of this project stage.

BEGIN;

ALTER TABLE consumption
    ADD COLUMN IF NOT EXISTS source_system VARCHAR(50),
    ADD COLUMN IF NOT EXISTS source_trip_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'consumption_source_reference_pair_ck'
          AND conrelid = 'consumption'::regclass
    ) THEN
        ALTER TABLE consumption
            ADD CONSTRAINT consumption_source_reference_pair_ck
            CHECK (
                (source_system IS NULL AND source_trip_id IS NULL)
                OR
                (source_system IS NOT NULL AND source_trip_id IS NOT NULL)
            );
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_consumption_external_source_trip
    ON consumption (source_system, source_trip_id)
    WHERE source_system IS NOT NULL AND source_trip_id IS NOT NULL;

COMMENT ON COLUMN consumption.source_system IS
    'Originating application for automated consumption; NULL for existing/manual records.';
COMMENT ON COLUMN consumption.source_trip_id IS
    'Unique originating trip identifier; NULL for existing/manual records.';

COMMIT;
