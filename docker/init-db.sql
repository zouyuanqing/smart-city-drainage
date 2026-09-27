-- Fresh-volume extension bootstrap only.
-- PostgreSQL/TimescaleDB is the sole business-schema writer (Alembic).
-- The profile-specific images set hydraulic.spatial_backend through PGOPTIONS.
\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "timescaledb";
CREATE EXTENSION IF NOT EXISTS "btree_gist";

DO $bootstrap$
DECLARE
    requested_profile text := COALESCE(
        NULLIF(current_setting('hydraulic.spatial_backend', TRUE), ''),
        'portable'
    );
BEGIN
    IF requested_profile NOT IN ('postgis', 'portable') THEN
        RAISE EXCEPTION
            'hydraulic.spatial_backend must be postgis or portable, got %',
            requested_profile;
    END IF;

    IF requested_profile = 'postgis' THEN
        EXECUTE 'CREATE EXTENSION IF NOT EXISTS "postgis"';
    ELSIF EXISTS (
        SELECT 1
        FROM pg_extension
        WHERE extname = 'postgis'
    ) THEN
        RAISE EXCEPTION
            'portable database profile must not have the postgis extension installed';
    END IF;
END
$bootstrap$;
