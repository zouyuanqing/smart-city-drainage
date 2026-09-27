"""A/B comparison record that /readyz requires.

Revision ID: 0007_hydraulic_ab_comparisons
Revises: 0006_hydraulic_alert_windows
Create Date: 2026-09-27

Position 7 in the contract §13.2 revision chain.  It follows 0006 because §13.2
fixes the order; the table itself depends only on ``simulations``, which 0002
already created.

Every column and key comes from
``docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md`` §3.7
(``hydraulic.ab_comparisons``, contract lines 955-965) and nothing else.  The
frozen DDL declares no CHECK constraint for this table, so none is invented
here.

The revision is profile-independent: the table carries no spatial column, so
the rendered DDL is byte-identical for the ``portable`` and ``postgis``
profiles.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_hydraulic_ab_comparisons"
down_revision: Union[str, None] = "0006_hydraulic_alert_windows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "hydraulic"
PRIOR_REVISION = "0006_hydraulic_alert_windows"

# The frozen unique key already leads with ``baseline_simulation_id``, so the
# only extra index is the candidate direction used when a comparison is looked
# up from the simulation under test.
INDEX_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "ix_hydraulic_ab_comparisons_candidate",
        "ab_comparisons",
        ("candidate_simulation_id",),
    ),
)


def _create_ab_comparisons() -> None:
    # Contract §3.7.  The unique key includes the frame grid so the same pair
    # of simulations may be re-compared on a different grid, while a repeat on
    # the same grid is idempotent.
    op.create_table(
        "ab_comparisons",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "baseline_simulation_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "candidate_simulation_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("frame_grid", postgresql.JSONB(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("comparison_sha256", sa.String(64), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_ab_comparisons"),
        sa.UniqueConstraint(
            "baseline_simulation_id",
            "candidate_simulation_id",
            "frame_grid",
            name="uq_hydraulic_ab_comparisons_grid",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_simulation_id"],
            [f"{SCHEMA}.simulations.id"],
            name="fk_hydraulic_ab_comparisons_baseline_simulation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_simulation_id"],
            [f"{SCHEMA}.simulations.id"],
            name="fk_hydraulic_ab_comparisons_candidate_simulation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["public.users.id"],
            name="fk_hydraulic_ab_comparisons_created_by",
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
        "'0007 upgrade refused: hydraulic.schema_metadata is not a single row'; "
        "END IF; "
        "SELECT alembic_revision INTO stamped FROM hydraulic.schema_metadata "
        "WHERE key = 'spatial_backend'; "
        f"IF stamped IS DISTINCT FROM '{previous}' THEN RAISE EXCEPTION "
        "'0007 upgrade refused: hydraulic.schema_metadata is not stamped by "
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
    _create_ab_comparisons()
    _create_indexes()
    _restamp_schema_metadata(PRIOR_REVISION, revision)


def _assert_reversible() -> None:
    op.execute(
        "DO $$ BEGIN "
        f"IF EXISTS (SELECT 1 FROM {SCHEMA}.ab_comparisons LIMIT 1) THEN "
        "RAISE EXCEPTION '0007 downgrade refused: A/B comparison data exists'; "
        "END IF; "
        "END $$;"
    )


def downgrade() -> None:
    _assert_reversible()
    for index_name, table_name, _columns in reversed(INDEX_DEFINITIONS):
        op.drop_index(index_name, table_name=table_name, schema=SCHEMA)
    op.drop_table("ab_comparisons", schema=SCHEMA)
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM hydraulic.schema_metadata "
        "WHERE key = 'spatial_backend' "
        f"AND alembic_revision = '{revision}') THEN "
        "RAISE EXCEPTION '0007 downgrade refused: schema metadata is not at "
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
    # 0001-0006 keep the schema, its extensions, and every table they created.
