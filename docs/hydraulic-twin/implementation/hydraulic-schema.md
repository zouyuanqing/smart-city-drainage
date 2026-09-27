# Hydraulic schema implementation summary

## Status

**IMPLEMENTED AND UNIT/OFFLINE-SQL VERIFIED; LIVE G1 NOT RUN.**

The three required authorization artifacts were present before any migration was written:

- `artifacts/contracts/v3-independent-review.json:1-71` records `R0_DESIGN_PASS` against base commit `ca99e0485fb12fbecbd565e79d81f745f8d4a748`.
- `artifacts/contracts/dependency-config-handoff.json:1-16,207-213,1546-1554` records the dependency handoff and `READY_WITH_DEFERRED` state.
- `artifacts/contracts/r0-gate-execution.json:1-20,91-102,138-149` records the two pre-implementation checks as exit `0` and the verified input hashes.

The implementation follows the frozen spatial/result rules in `docs/hydraulic-twin/PRD_SUPPLEMENT.md:120-135,253-268` and `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:109-115,165-299,596-644,646-815,897-965,1034-1150,1251-1277,2109-2127`.

## Real changes

### ORM and compatibility

- Added `hydraulic` SQLAlchemy models for:
  - network/revision/node/pipe/catchment: `backend/app/models/hydraulic_models.py:206-629`;
  - rainfall scenario/points: `backend/app/models/hydraulic_models.py:631-719`;
  - simulation run, attempt, frame registration, Timescale frame, and result: `backend/app/models/hydraulic_models.py:780-1127`;
  - calibration run/report: `backend/app/models/hydraulic_models.py:1129-1248`;
  - probability calibration, nowcast run, forecast alert/transition: `backend/app/models/hydraulic_models.py:1250-1566`;
  - result retention metadata with a default of 90 days: `backend/app/models/hydraulic_models.py:1568-1600`.
- Added process-profile-aware PostGIS geometry columns without adding GeoAlchemy/Shapely dependencies. Portable metadata has WKT+bbox only; PostGIS metadata maps generated geometry columns.
- Registered hydraulic metadata with Alembic while preserving the historical six legacy models. `Base.metadata.create_all()` without an explicit table list remains legacy-only; explicit table lists are available to isolated tests (`backend/app/models/db_models.py:32-118,378`).
- Preserved all existing public exports and added hydraulic exports in `backend/app/models/__init__.py:6-62`.

### Pydantic and geometry contracts

- Added finite EPSG:4326 `BoundingBox`, canonical WKT/GeoJSON dual-path `GeometryDocument`, and entity-specific geometry validation in `backend/app/schemas/hydraulic_schemas.py:142-492`.
- Added closed request/result contracts for network, node, pipe, catchment, rainfall scenario, simulation, frame/result, calibration, forecast alert, and retention metadata in `backend/app/schemas/hydraulic_schemas.py:495-741`.
- The codec rejects SQL-like trailing WKT, non-finite coordinates, out-of-range lon/lat, unclosed/short polygon rings, identical-point LineStrings, mismatched WKT/GeoJSON, and mismatched bbox values. These cases are exercised at `backend/tests/test_hydraulic_schema.py:99-205`.

### Alembic and Timescale

- Added linear revision `0002_hydraulic_schema`, down revision `0001_initial`, with upgrade/downgrade at `backend/alembic/versions/0002_hydraulic_schema.py:1389-1481`.
- `postgis` path:
  - creates/verifies PostGIS;
  - adds generated `Point`/`LineString`/`Geometry` columns from canonical WKT;
  - adds geometry validity/type checks and GiST indexes.
  The implementation is at `backend/alembic/versions/0002_hydraulic_schema.py:1329-1386`; assertions are at `backend/tests/test_hydraulic_schema.py:579-603`.
- `portable` path:
  - requires TimescaleDB but never creates/queries PostGIS;
  - persists WKT+bbox and bbox checks.
  Assertions are at `backend/tests/test_hydraulic_schema.py:605-621`.
- `hydraulic.simulation_frames` uses primary key `(frame_time, attempt_id, frame_index)` and is converted to a Timescale hypertable by `frame_time`. Cross-partition `(attempt_id, frame_index)` uniqueness remains in ordinary PostgreSQL `simulation_frame_registrations`.
- The migration adds a 90-day Timescale retention policy and records table/hypertable/partition/chunk metadata at `backend/alembic/versions/0002_hydraulic_schema.py:1269-1326`.
- Downgrade is reverse-order and fail-closed when hydraulic rows exist; it retains shared extensions and never drops/alters legacy public tables (`backend/alembic/versions/0002_hydraulic_schema.py:1459-1481`).

### Tests

- Added 19 focused tests covering constraints, named enums, cascade behavior, legacy-table compatibility, migration SQL, both spatial profiles, Timescale retention, bbox/WKT/GeoJSON serialization, and invalid geometry (`backend/tests/test_hydraulic_schema.py:99-684`).

## Authorization checks run before editing

Commands were run from `smart-city-drainage/` with Python 3.11 and `backend/requirements-py311.lock`.

```text
$ C:\Python314\Scripts\uv.EXE run --isolated --no-project --python 3.11 --with-requirements backend/requirements-py311.lock python -B scripts/check_migration_gate.py --review-artifact artifacts/contracts/v3-independent-review.json --base-ref ca99e0485fb12fbecbd565e79d81f745f8d4a748 --versions-dir backend/alembic/versions
MIGRATION_GATE=PASS
exit 0
```

```text
$ C:\Python314\Scripts\uv.EXE run --isolated --no-project --python 3.11 --with-requirements backend/requirements-py311.lock python -B scripts/check_dependency_handoff.py --handoff artifacts/contracts/dependency-config-handoff.json --expected backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json --python-version-file .python-version --node-version-file frontend/.node-version --npmrc frontend/.npmrc --package frontend/package.json --lock frontend/package-lock.json --env-example .env.example
DEPENDENCY_HANDOFF=PASS_WITH_DEFERRED
G0_G5_BLOCKED_BY_DEFERRED=true
exit 0
```

The first two shell attempts used `cd /d` under Bash and exited `2` with `cd: too many arguments`; they made no changes. The commands above are the successful reruns.

## Verification actually run

### Focused schema suite

```text
$ cd backend && C:\Python314\Scripts\uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B -m pytest tests/test_hydraulic_schema.py -q
19 passed, 1 warning in 1.13s
exit 0
```

### Backend regression excluding the pre-authorization checker

The checker tests intentionally assert that `backend/alembic/versions` is unchanged from the R0 base commit. After the authorized `0002` file exists, that pre-authorization assertion is expected to fail; it is not silently rewritten because `tests/test_contract_gate_scripts.py` is outside this task's owned paths.

```text
$ cd backend && C:\Python314\Scripts\uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B -m pytest -q --ignore=tests/test_contract_gate_scripts.py
123 passed, 2 skipped, 1 warning in 15.38s
exit 0
```

The one warning is the pre-existing Pydantic protected-namespace warning for a `model_version` field outside this task's owned schema file.

### Full backend suite

```text
$ cd backend && C:\Python314\Scripts\uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B -m pytest -q
2 failed, 133 passed, 2 skipped, 1 warning in 19.79s
exit 1
```

Both failures are the expected post-authorization state of the pre-authorization migration checker: it reports `versions path set changed from base: ['backend/alembic/versions/0002_hydraulic_schema.py']`. The full suite is therefore **not claimed green**. Gate/G0 owner must define the post-landing transition without weakening the initial fail-closed check.

### Alembic graph and offline SQL

```text
$ cd backend && C:\Python314\Scripts\uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B -m alembic -c alembic.ini heads
0002_hydraulic_schema (head)
exit 0
```

```text
$ HYDRAULIC_SPATIAL_BACKEND=portable ... python -B -m alembic -c alembic.ini upgrade 0001_initial:0002_hydraulic_schema --sql
Generating static SQL ... Running upgrade 0001_initial -> 0002_hydraulic_schema
exit 0

$ HYDRAULIC_SPATIAL_BACKEND=postgis ... python -B -m alembic -c alembic.ini upgrade 0001_initial:0002_hydraulic_schema --sql
Generating static SQL ... Running upgrade 0001_initial -> 0002_hydraulic_schema
exit 0

$ HYDRAULIC_SPATIAL_BACKEND=postgis ... python -B -m alembic -c alembic.ini downgrade 0002_hydraulic_schema:0001_initial --sql
Generating static SQL ... Running downgrade 0002_hydraulic_schema -> 0001_initial
exit 0
```

### Formatting, lint, and type checks

```text
$ python -m black --check <six owned Python files>
All done! 6 files would be left unchanged.
exit 0

$ python -m isort --check-only --profile black <six owned Python files>
exit 0

$ python -m flake8 <six owned Python files>
exit 0

$ python -m mypy --follow-imports=skip app/models/hydraulic_models.py app/schemas/hydraulic_schemas.py
Success: no issues found in 2 source files
exit 0
```

A normal targeted mypy invocation also followed the shared `app/core/config.py` import and reported two existing errors at `backend/app/core/config.py:201` and `backend/app/core/config.py:480`; those lines were not modified because the file is outside owned paths.

## Not run and remaining risks

1. **No live PostgreSQL/PostGIS/Timescale database was available or started.** Offline SQL generation and SQLite contract tests are not substitutes for G1 fresh-volume `upgrade → current → repeat → clone downgrade/upgrade`; G1 remains **NOT RUN**.
2. The dependency handoff still reports frontend G0/G5 deferred work. This schema task does not satisfy or supersede those items.
3. `HYDRAULIC_SCHEMA_REVISION` remains `0001_initial` at `backend/app/core/config.py:193`, while the migration head is now `0002_hydraulic_schema`. The integration/config owner must update the declared head/readiness contract.
4. `backend/app/main.py:137` still calls the legacy startup `create_all_tables`; `backend/app/models/db_models.py:43-57` deliberately keeps that unqualified runtime call legacy-only, but the integration owner must still remove production `create_all` per the frozen contract.
5. The live PostGIS image and TimescaleDB version must be tested for generated-column and `add_retention_policy` compatibility before G1 can pass.
6. This task implemented the requested core hydraulic associations. Worker lifecycle columns, target-manifest/initial-state submodels, observation ingestion, and full forecast outbox/notification tables remain follow-on contract areas unless added by their owning migrations; they were not falsely marked complete here.
