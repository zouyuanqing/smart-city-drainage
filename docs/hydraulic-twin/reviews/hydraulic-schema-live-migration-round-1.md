# Hydraulic schema live migration repair audit — round 1

Date: 2026-09-25
Owner scope: hydraulic ORM, hydraulic Pydantic schema, Alembic 0002, hydraulic schema tests, and this audit only. `docker/init-db.sql` and Compose were not modified.

## Baseline reproduction

The supplied live reproduction was confirmed with a fresh portable volume:

```text
cd .../smart-city-drainage/backend
HYDRAULIC_SPATIAL_BACKEND=portable \
DATABASE_URL=postgresql+asyncpg://drainage_admin:<local-test-password>@127.0.0.1:55433/smart_drainage \
DATABASE_URL_SYNC=postgresql://drainage_admin:<local-test-password>@127.0.0.1:55433/smart_drainage \
.venv/Scripts/python.exe -m alembic upgrade head
```

Before the repair, exit code was `1`. The live PostgreSQL error was:

```text
asyncpg.exceptions.PostgresSyntaxError: syntax error at or near "draft"
[SQL: CREATE TYPE hydraulic.network_status AS ENUM (draft, active, archived)]
```

The failure was in the pre-fix `_create_enums()` implementation at `backend/alembic/versions/0002_hydraulic_schema.py:129-133`, reached by `upgrade()` before any profile-specific geometry work.

## Repairs

1. `_create_enums()` now compiles every enum label through the PostgreSQL dialect with literal binds (`backend/alembic/versions/0002_hydraulic_schema.py:130-146`). This quotes and escapes labels and prevents the original `ENUM (draft, ...)` syntax error. The static regression test asserts the quoted first enum and rejects the old unquoted form (`backend/tests/test_hydraulic_schema.py:1022-1039`).
2. The retention policy table now has a PostgreSQL `uuid_generate_v4()` server default for its non-null primary key (`backend/alembic/versions/0002_hydraulic_schema.py:2363-2372`). The ORM metadata carries the same server default (`backend/app/models/hydraulic_models.py:2683-2699`). Without this, the migration's raw `bulk_insert` supplied no ORM client-side default and live PostgreSQL rejected the policy row with `NotNullViolationError`.
3. Downgrade emptiness is fail-closed across all 25 business tables, not only `networks` (`backend/alembic/versions/0002_hydraulic_schema.py:2525-2558`). A real portable probe inserted a `parameter_sets` row, `alembic downgrade 0001_initial` exited `1` with `hydraulic downgrade refused: hydraulic data exists`, and the probe row was then removed.
4. Downgrade explicitly drops the two schema-owned trigger functions before `DROP SCHEMA` (`backend/alembic/versions/0002_hydraulic_schema.py:2620-2633`). The first real empty downgrade attempt exposed the remaining PostgreSQL dependency error: `function hydraulic.guard_current_revision_network() depends on schema hydraulic`; the post-fix empty downgrade and upgrade cycle passed.

No Pydantic behavior required a code change: the hydraulic DTO enum definitions and the ORM enum names already matched the migration values read in this round.

## Live evidence

Both profile databases were started as separate fresh Compose projects. The following commands ran during this audit:

```text
POSTGRES_PASSWORD=local-test-only docker compose -p scd-hydraulic-round1-portable -f docker/postgres/compose.yml --profile portable up -d --wait postgres-portable
POSTGRES_PASSWORD=local-test-only docker compose -p scd-hydraulic-round1-postgis -f docker/postgres/compose.yml --profile postgis up -d --wait postgres-postgis
```

For each profile, the executed migration sequence was `alembic upgrade head`, `alembic current`, repeat `alembic upgrade head`, `alembic downgrade 0001_initial`, `alembic current`, `alembic upgrade head`, and `alembic current`. Both sequences exited `0`; final `alembic current` was `0002_hydraulic_schema (head)`.

Portable live inspection output:

```text
extensions=btree_gist:1.7,timescaledb:2.17.2,uuid-ossp:1.1
alembic=0002_hydraulic_schema
profile=portable,revision=0002_hydraulic_schema
hydraulic_tables=27
hypertable=hydraulic.simulation_frames,time=frame_time,interval=1 day
spatial_geometry_columns=0
spatial_gist_indexes=0
key_checks=4
retention=90,1 day,true,drop_after=90 days
```

The portable live verifier command also exited `0` with `ok: true`:

```text
.venv/Scripts/python.exe ../scripts/verify_database.py --profile portable --dsn <portable-dsn> --compact
```

PostGIS live inspection output:

```text
extensions=btree_gist:1.7,postgis:3.6.4,timescaledb:2.28.3,uuid-ossp:1.1
alembic=0002_hydraulic_schema
profile=postgis,revision=0002_hydraulic_schema
hydraulic_tables=27
hypertable=hydraulic.simulation_frames,time=frame_time,interval=1 day
spatial_geometry_columns=3
spatial_gist_indexes=3
key_checks=4
geometry=catchments,geometry,geometry(Geometry,4326),generated=s
geometry=network_nodes,geometry,geometry(Point,4326),generated=s
geometry=pipe_segments,geometry,geometry(LineString,4326),generated=s
gist=ix_hydraulic_catchments_geometry_gist
gist=ix_hydraulic_network_nodes_geometry_gist
gist=ix_hydraulic_pipe_segments_geometry_gist
retention=90,1 day,true,drop_after=90 days
```

The live enum query returned all 12 expected enum types and labels, including `network_status:draft,active,archived`, for both profiles.

## Tests and known non-owned failure

The updated schema contract suite passed:

```text
cd .../smart-city-drainage/backend
.venv/Scripts/python.exe -m pytest -q tests/test_hydraulic_schema.py
# 29 passed, 804 warnings
```

The explicitly configured two-profile integration command was run, not skipped:

```text
DATABASE_TEST_DSN_PORTABLE=<portable-dsn> DATABASE_TEST_DSN_POSTGIS=<postgis-dsn> \
.venv/Scripts/python.exe -m pytest -q tests/integration/test_database_schema.py
# 5 passed, 1 failed
```

The PostGIS live manifest case fails before its assertion because the non-owned `scripts/verify_database.py` receives PostgreSQL `attgenerated` as `bytes` and then raises `TypeError: Object of type bytes is not JSON serializable` while emitting JSON. This is a verifier serialization defect, not a migration/ORM failure; the direct PostGIS catalog queries above independently passed. That script is outside this owner's allowed paths and was not changed.

## Scope and gate conclusion

The requested real Alembic migration and downgrade/upgrade checks pass for both profiles, and direct live catalog checks pass for extensions, 27 hydraulic tables, the `frame_time` hypertable, retention, generated PostGIS geometry/GiST indexes, and the key CHECK constraints. The unrelated non-owned PostGIS verifier serialization defect means the broader live integration test is not fully green and must not be reported as passed.

LIVE_MIGRATION_GATE=FAIL
