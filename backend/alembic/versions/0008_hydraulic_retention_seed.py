"""Retention bookkeeping seed for the observation hypertable.

Revision ID: 0008_hydraulic_retention_seed
Revises: 0007_hydraulic_ab_comparisons
Create Date: 2026-09-27

Position 8, the final slot of the contract §13.2 revision chain, which names it
``0008_hydraulic_indexes_retention_seed``.  The identifier is shortened because
Alembic's own ``alembic_version.version_num`` column is ``VARCHAR(32)`` and the
contract name does not fit; the position in the chain is unchanged.  §13.2
keeps the chain linear with a single head, so this revision runs last and owns
the cross-revision bookkeeping rather than any new table.

Its single job is derived from a gap the previous revisions leave behind: 0002
created ``hydraulic.result_retention_policies`` and seeded exactly one row for
its own ``simulation_frames`` hypertable, and 0005 attached a Timescale
retention policy to ``observation_samples`` without recording the row.  The
policy table is therefore the migration-owned ledger of every hypertable's
retention, and an operator reading it would see the observation hypertable as
unmanaged even though §3.7 line 894 fixes 90-day retention for it.

This revision adds no table, no column, and no index.  It seeds the ledger row
and re-stamps ``hydraulic.schema_metadata`` to the final head.  The indexes for
these tables are created in the revision that owns the table so each
downgrade drops exactly what its own upgrade created.

The revision is profile-independent: it contains no spatial reference.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0008_hydraulic_retention_seed"
down_revision: Union[str, None] = "0007_hydraulic_ab_comparisons"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "hydraulic"
PRIOR_REVISION = "0007_hydraulic_ab_comparisons"

# Must stay equal to 0005's frozen values: the ledger row and the live
# Timescale retention policy describe the same hypertable and may not drift.
OBSERVATION_TABLE = "observation_samples"
OBSERVATION_PARTITION_COLUMN = "observed_at"
OBSERVATION_RETENTION_DAYS = 90
OBSERVATION_CHUNK_INTERVAL = "1 day"


def _seed_observation_retention() -> None:
    # The Timescale policy itself was applied by 0005.  This writes the ledger
    # row that 0002's table requires, and the leading guard makes a re-run
    # fail closed instead of silently adding a duplicate claim.
    op.execute(
        "DO $$ BEGIN "
        f"IF EXISTS (SELECT 1 FROM {SCHEMA}.result_retention_policies "
        f"WHERE table_name = '{OBSERVATION_TABLE}') THEN "
        "RAISE EXCEPTION '0008 upgrade refused: observation retention ledger "
        "row already exists'; "
        "END IF; "
        "END $$;"
    )
    op.execute(
        "DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT 1 FROM {SCHEMA}.result_retention_policies "
        "WHERE table_name = 'simulation_frames') THEN "
        "RAISE EXCEPTION '0008 upgrade refused: the 0002 retention seed is "
        "not intact'; "
        "END IF; "
        "END $$;"
    )
    policy_table = sa.table(
        "result_retention_policies",
        sa.column("table_name", sa.String),
        sa.column("hypertable", sa.Boolean),
        sa.column("partition_column", sa.String),
        sa.column("retention_days", sa.Integer),
        sa.column("chunk_interval", sa.String),
        sa.column("enabled", sa.Boolean),
        schema=SCHEMA,
    )
    op.bulk_insert(
        policy_table,
        [
            {
                "table_name": OBSERVATION_TABLE,
                "hypertable": True,
                "partition_column": OBSERVATION_PARTITION_COLUMN,
                "retention_days": OBSERVATION_RETENTION_DAYS,
                "chunk_interval": OBSERVATION_CHUNK_INTERVAL,
                "enabled": True,
            }
        ],
    )


def _schema_metadata_guard_sql() -> tuple[str, ...]:
    return (
        f"CREATE OR REPLACE FUNCTION {SCHEMA}.guard_schema_metadata() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "RAISE EXCEPTION 'hydraulic schema metadata is immutable'; "
        "END $$",
        "DROP TRIGGER IF EXISTS trg_hydraulic_schema_metadata_immutable "
        f"ON {SCHEMA}.schema_metadata",
        "CREATE TRIGGER trg_hydraulic_schema_metadata_immutable "
        "BEFORE UPDATE OR DELETE ON hydraulic.schema_metadata "
        "FOR EACH ROW EXECUTE FUNCTION hydraulic.guard_schema_metadata()",
    )


def _drop_schema_metadata_guard() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_hydraulic_schema_metadata_immutable "
            f"ON {SCHEMA}.schema_metadata"
        )
    )


def _reinstall_schema_metadata_guard() -> None:
    for statement in _schema_metadata_guard_sql():
        op.execute(sa.text(statement))


def _restamp_schema_metadata(previous: str, current: str) -> None:
    op.execute(
        "DO $$ DECLARE stamped text; BEGIN "
        "IF (SELECT count(*) FROM hydraulic.schema_metadata) <> 1 THEN "
        "RAISE EXCEPTION "
        "'0008 upgrade refused: hydraulic.schema_metadata is not a single row'; "
        "END IF; "
        "SELECT alembic_revision INTO stamped FROM hydraulic.schema_metadata "
        "WHERE key = 'spatial_backend'; "
        f"IF stamped IS DISTINCT FROM '{previous}' THEN RAISE EXCEPTION "
        "'0008 upgrade refused: hydraulic.schema_metadata is not stamped by "
        "the prior revision'; "
        "END IF; "
        "END $$;"
    )
    _drop_schema_metadata_guard()
    op.execute(
        sa.text(
            "UPDATE hydraulic.schema_metadata "
            "SET alembic_revision = :current, updated_at = clock_timestamp() "
            "WHERE key = 'spatial_backend' AND alembic_revision = :previous"
        ).bindparams(current=current, previous=previous)
    )
    _reinstall_schema_metadata_guard()


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    _seed_observation_retention()
    _restamp_schema_metadata(PRIOR_REVISION, revision)


def downgrade() -> None:
    # Removing the ledger row restores the exact 0007 state.  The 0002
    # ``simulation_frames`` row is never touched: it predates this revision and
    # belongs to the base that the downgrade hands back.
    op.execute(
        "DO $$ BEGIN "
        f"IF (SELECT count(*) FROM {SCHEMA}.result_retention_policies "
        f"WHERE table_name = '{OBSERVATION_TABLE}') <> 1 THEN "
        "RAISE EXCEPTION '0008 downgrade refused: observation retention ledger "
        "row is not intact'; "
        "END IF; "
        "END $$;"
    )
    op.execute(
        sa.text(
            "DELETE FROM hydraulic.result_retention_policies "
            "WHERE table_name = :table_name"
        ).bindparams(table_name=OBSERVATION_TABLE)
    )
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM hydraulic.schema_metadata "
        "WHERE key = 'spatial_backend' "
        f"AND alembic_revision = '{revision}') THEN "
        "RAISE EXCEPTION '0008 downgrade refused: schema metadata is not at "
        "this revision'; "
        "END IF; END $$;"
    )
    _drop_schema_metadata_guard()
    op.execute(
        sa.text(
            "UPDATE hydraulic.schema_metadata "
            "SET alembic_revision = :previous, updated_at = clock_timestamp() "
            "WHERE key = 'spatial_backend' AND alembic_revision = :current"
        ).bindparams(previous=PRIOR_REVISION, current=revision)
    )
    _reinstall_schema_metadata_guard()
    # 0001-0007 keep the schema, its extensions, and every table they created.
