# Database image profiles

Both images use PostgreSQL 15 and TimescaleDB. `postgis` adds PostGIS; `portable`
does not install or reference PostGIS. Alembic remains the only writer for
business tables, hypertables, seed rows, and schema metadata.

## Reproducible inputs

| Profile | Digest-pinned base | Purpose |
|---|---|---|
| `postgis` | `timescale/timescaledb-ha:pg15@sha256:f49bf91b8966318e9bab4c44e4cc6c6533539dd44fcb049ee3334349dfcb233f` | PostgreSQL + TimescaleDB + PostGIS |
| `portable` | `timescale/timescaledb:2.17.2-pg15@sha256:d33b93c43b9db7a065f660847d4390d4a84ea1dd72395258aa8873f314de58c7` | PostgreSQL + TimescaleDB, no PostGIS dependency |

The Compose file can override `POSTGIS_BASE_IMAGE` or `PORTABLE_BASE_IMAGE`
for a registry mirror, but an override must retain the same digest. The image
entrypoint rejects attempts to run a PostGIS image as `portable` or vice versa.

## Build and start a fresh profile

Run from the repository root and provide a non-default password:

```bash
export POSTGRES_PASSWORD='replace-with-a-local-secret'

docker compose -f docker/postgres/compose.yml --profile postgis build
docker compose -f docker/postgres/compose.yml --profile postgis down -v --remove-orphans
docker compose -f docker/postgres/compose.yml --profile postgis up -d --wait

docker compose -f docker/postgres/compose.yml --profile portable build
docker compose -f docker/postgres/compose.yml --profile portable down -v --remove-orphans
docker compose -f docker/postgres/compose.yml --profile portable up -d --wait
```

The fragment deliberately has no `container_name`; use the Compose service name
or `docker compose ... ps` to discover the generated container. The host ports
default to `127.0.0.1:55432` (`postgis`) and `127.0.0.1:55433` (`portable`)
for explicit local verification only.

## Apply the schema and verify

The init hook creates only the profile's extensions. Then run Alembic with the
same immutable profile used to build the image:

```bash
(
  cd backend
  HYDRAULIC_SPATIAL_BACKEND=postgis \
  DATABASE_URL_SYNC='postgresql://drainage_admin:<password>@127.0.0.1:55432/smart_drainage' \
    python -m alembic upgrade head
)

python scripts/verify_database.py \
  --profile postgis \
  --dsn 'postgresql://drainage_admin:<password>@127.0.0.1:55432/smart_drainage'
```

Use `HYDRAULIC_SPATIAL_BACKEND=portable`, port `55433`, and
`--profile portable` for the fallback image. The verifier is read-only and
checks extension presence/absence, the Alembic and immutable profile heads,
the `simulation_frames` hypertable and 90-day retention policy, and all three
PostGIS generated geometry columns plus GiST indexes (or their absence in the
portable profile).

Never point both profiles at the same volume or change
`HYDRAULIC_SPATIAL_BACKEND` after the database has been initialized.
