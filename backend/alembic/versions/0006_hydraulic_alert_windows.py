"""Forecast alert suppression window that /readyz requires.

Revision ID: 0006_hydraulic_alert_windows
Revises: 0005_hydraulic_observations
Create Date: 2026-09-27

Position 6 in the contract §13.2 revision chain.  It follows 0005 because
§13.2 fixes the order; the table itself depends only on ``forecast_alerts``,
which 0002 already created.

Every column comes from
``docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md`` §3.8
(``hydraulic.forecast_alert_windows``, contract lines 1157-1166) and the §8.1
state machine that consumes it.  The frozen DDL declares no CHECK constraint
for this table, so none is invented here.

The revision is profile-independent: the table carries no spatial column, so
the rendered DDL is byte-identical for the ``portable`` and ``postgis``
profiles.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_hydraulic_alert_windows"
down_revision: Union[str, None] = "0005_hydraulic_observations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "hydraulic"
PRIOR_REVISION = "0005_hydraulic_observations"

# The primary key is the suppression key itself, so the only supporting indexes
# are the two parent/child directions §8.1 needs: releasing an alert back to
# the pool, and scanning for cooldowns that have expired.
INDEX_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "ix_hydraulic_forecast_alert_windows_active_alert",
        "forecast_alert_windows",
        ("active_alert_id",),
    ),
    (
        "ix_hydraulic_forecast_alert_windows_cooldown_until",
        "forecast_alert_windows",
        ("cooldown_until",),
    ),
)


def _timestamp(name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def _create_forecast_alert_windows() -> None:
    # Contract §3.8.  ``suppression_key`` is the table's identity: §8.1 line
    # 1791 fixes one key per alert episode across the predicted->observed
    # transition, so the window row is never duplicated per phase.
    op.create_table(
        "forecast_alert_windows",
        sa.Column("suppression_key", sa.String(64), nullable=False),
        sa.Column("active_alert_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_candidate_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("last_qualifying_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "suppressed_count",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "version", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint(
            "suppression_key", name="pk_hydraulic_forecast_alert_windows"
        ),
        sa.ForeignKeyConstraint(
            ["active_alert_id"],
            [f"{SCHEMA}.forecast_alerts.id"],
            name="fk_hydraulic_forecast_alert_windows_alert",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )


def _create_indexes() -> None:
    for index_name, table_name, columns in INDEX_DEFINITIONS:
        op.create_index(index_name, table_name, list(columns), schema=SCHEMA)


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
        "'0006 upgrade refused: hydraulic.schema_metadata is not a single row'; "
        "END IF; "
        "SELECT alembic_revision INTO stamped FROM hydraulic.schema_metadata "
        "WHERE key = 'spatial_backend'; "
        f"IF stamped IS DISTINCT FROM '{previous}' THEN RAISE EXCEPTION "
        "'0006 upgrade refused: hydraulic.schema_metadata is not stamped by "
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
    _create_forecast_alert_windows()
    _create_indexes()
    _restamp_schema_metadata(PRIOR_REVISION, revision)


def _assert_reversible() -> None:
    op.execute(
        "DO $$ BEGIN "
        f"IF EXISTS (SELECT 1 FROM {SCHEMA}.forecast_alert_windows LIMIT 1) "
        "THEN RAISE EXCEPTION "
        "'0006 downgrade refused: forecast alert window data exists'; "
        "END IF; "
        "END $$;"
    )


def downgrade() -> None:
    _assert_reversible()
    for index_name, table_name, _columns in reversed(INDEX_DEFINITIONS):
        op.drop_index(index_name, table_name=table_name, schema=SCHEMA)
    op.drop_table("forecast_alert_windows", schema=SCHEMA)
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM hydraulic.schema_metadata "
        "WHERE key = 'spatial_backend' "
        f"AND alembic_revision = '{revision}') THEN "
        "RAISE EXCEPTION '0006 downgrade refused: schema metadata is not at "
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
    # 0001-0005 keep the schema, its extensions, and every table they created.
