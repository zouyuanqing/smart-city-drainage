"""Network import and validation result detail that /readyz requires.

Revision ID: 0004_hydraulic_imports
Revises: 0003_hydraulic_schema_gap
Create Date: 2026-09-27

Contract §13.2 fixes a single linear revision chain with one head; this file
takes position 4 in that chain.  0001, 0002, and 0003 are digest-fixed base
revisions and are not modified by this file, so the eight missing /readyz
tables are delivered as new additive revisions instead of an edit to a sealed
migration.  Position 4 carries the network-job result detail (imports and
validation) because §3.4 declares those three tables against
``network_jobs``, ``networks``, and ``network_revisions`` — every object this
revision needs already exists after 0003.

Every column, constraint, and index below comes from
``docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md`` §3.4 and nothing else:

* ``hydraulic.network_imports`` (contract lines 549-564)
* ``hydraulic.validation_runs`` (contract lines 568-580)
* ``hydraulic.network_validation_issues`` (contract lines 584-595)

The revision is profile-independent: none of the three tables carries a
spatial column, so the rendered DDL is byte-identical for the ``portable`` and
``postgis`` profiles and no PostGIS object is created, queried, or altered.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_hydraulic_imports"
down_revision: Union[str, None] = "0003_hydraulic_schema_gap"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "hydraulic"
PRIOR_REVISION = "0003_hydraulic_schema_gap"

# Additive enum domains.  Every type in 0001/0002/0003 is left untouched.
ENUM_DEFINITIONS: dict[str, tuple[str, ...]] = {
    "import_source_format": ("inp", "geojson"),
    "validation_run_status": ("valid", "valid_with_warnings", "invalid"),
    "validation_issue_severity": ("error", "warning", "info"),
}

# Supporting indexes.  PostgreSQL never creates an index for a foreign key on
# its own, and §3.4 exposes each of these tables by its network/revision parent,
# so the parent lookup paths are indexed explicitly.
INDEX_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "ix_hydraulic_network_imports_network",
        "network_imports",
        ("network_id",),
    ),
    (
        "ix_hydraulic_network_imports_network_revision",
        "network_imports",
        ("network_revision_id",),
    ),
    ("ix_hydraulic_validation_runs_network", "validation_runs", ("network_id",)),
    (
        "ix_hydraulic_validation_runs_network_revision",
        "validation_runs",
        ("network_revision_id",),
    ),
)


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(
        *ENUM_DEFINITIONS[name],
        name=name,
        schema=SCHEMA,
        create_type=False,
    )


def _timestamp(name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def _create_enums() -> None:
    for name, values in ENUM_DEFINITIONS.items():
        # PostgreSQL requires enum labels to be string literals.  Compiling each
        # label through the PostgreSQL dialect quotes and escapes it, matching
        # the 0002/0003 helper that this file deliberately mirrors.
        value_sql = ", ".join(
            str(
                sa.literal(value).compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
            for value in values
        )
        op.execute(sa.text(f"CREATE TYPE {SCHEMA}.{name} AS ENUM ({value_sql})"))


def _create_network_imports() -> None:
    # Contract §3.4.  ``source_filename`` is display only, so it is nullable
    # and never participates in identity; ``source_sha256`` and
    # ``network_job_id`` are the real import identity.
    op.create_table(
        "network_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "network_revision_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("network_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_filename", sa.String(512), nullable=True),
        sa.Column(
            "source_format", _enum("import_source_format"), nullable=False
        ),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column(
            "committed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "counts",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        _timestamp("created_at"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_network_imports"),
        # One import detail row per job; the job is the result, never a series.
        sa.UniqueConstraint(
            "network_job_id", name="uq_hydraulic_network_imports_job"
        ),
        sa.ForeignKeyConstraint(
            ["network_id"],
            [f"{SCHEMA}.networks.id"],
            name="fk_hydraulic_network_imports_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id"],
            [f"{SCHEMA}.network_revisions.id"],
            name="fk_hydraulic_network_imports_network_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_job_id"],
            [f"{SCHEMA}.network_jobs.id"],
            name="fk_hydraulic_network_imports_job",
            ondelete="RESTRICT",
        ),
        # ``created_by`` targets the legacy public table, exactly like every
        # other hydraulic author column.
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["public.users.id"],
            name="fk_hydraulic_network_imports_created_by",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )


def _create_validation_runs() -> None:
    # Contract §3.4.  The column is named ``status_value`` because the frozen
    # DDL fixes that name; it is not an alias for a job status.
    op.create_table(
        "validation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "network_revision_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "status_value", _enum("validation_run_status"), nullable=False
        ),
        sa.Column("counts", postgresql.JSONB(), nullable=True),
        sa.Column("simulation_ready", sa.Boolean(), nullable=True),
        sa.Column("topology", postgresql.JSONB(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        _timestamp("created_at"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_validation_runs"),
        # §3.4: a validate job publishes exactly one report, and the report is
        # never rewritten in place.
        sa.UniqueConstraint(
            "network_job_id", name="uq_hydraulic_validation_runs_job"
        ),
        sa.ForeignKeyConstraint(
            ["network_job_id"],
            [f"{SCHEMA}.network_jobs.id"],
            name="fk_hydraulic_validation_runs_job",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_id"],
            [f"{SCHEMA}.networks.id"],
            name="fk_hydraulic_validation_runs_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id"],
            [f"{SCHEMA}.network_revisions.id"],
            name="fk_hydraulic_validation_runs_network_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["public.users.id"],
            name="fk_hydraulic_validation_runs_created_by",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )


def _create_network_validation_issues() -> None:
    # Contract §3.4.  Issues are owned by their run and disappear with it, so
    # the parent key cascades; ``source_line`` stays NULL for whole-network
    # findings, which is why the frozen unique key tolerates the NULL.
    op.create_table(
        "network_validation_issues",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "validation_run_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "severity", _enum("validation_issue_severity"), nullable=False
        ),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("entity_type", sa.String(128), nullable=False),
        sa.Column("entity_key", sa.String(128), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("source_line", sa.Integer(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint(
            "id", name="pk_hydraulic_network_validation_issues"
        ),
        sa.UniqueConstraint(
            "validation_run_id",
            "code",
            "entity_type",
            "entity_key",
            "source_line",
            name="uq_hydraulic_network_validation_issues_finding",
        ),
        sa.ForeignKeyConstraint(
            ["validation_run_id"],
            [f"{SCHEMA}.validation_runs.id"],
            name="fk_hydraulic_network_validation_issues_run",
            ondelete="CASCADE",
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
    # Contract §3.1: ``alembic_revision`` mirrors the declared head while the
    # profile value stays immutable.  The 0002 guard trigger has to be lifted
    # for exactly this one statement and is reinstalled immediately after.
    op.execute(
        "DO $$ DECLARE stamped text; BEGIN "
        "IF (SELECT count(*) FROM hydraulic.schema_metadata) <> 1 THEN "
        "RAISE EXCEPTION "
        "'0004 upgrade refused: hydraulic.schema_metadata is not a single row'; "
        "END IF; "
        "SELECT alembic_revision INTO stamped FROM hydraulic.schema_metadata "
        "WHERE key = 'spatial_backend'; "
        f"IF stamped IS DISTINCT FROM '{previous}' THEN RAISE EXCEPTION "
        "'0004 upgrade refused: hydraulic.schema_metadata is not stamped by "
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
    _create_enums()
    _create_network_imports()
    _create_validation_runs()
    _create_network_validation_issues()
    _create_indexes()
    # Restamped last: any failure above leaves the row at the 0003 stamp.
    _restamp_schema_metadata(PRIOR_REVISION, revision)


def _assert_reversible() -> None:
    # Contract §13.2: downgrade runs only against an empty database or a clone.
    # Fail closed rather than silently discarding import or validation history.
    op.execute(
        "DO $$ BEGIN "
        f"IF EXISTS (SELECT 1 FROM {SCHEMA}.network_imports LIMIT 1) "
        f"OR EXISTS (SELECT 1 FROM {SCHEMA}.network_validation_issues LIMIT 1) "
        f"OR EXISTS (SELECT 1 FROM {SCHEMA}.validation_runs LIMIT 1) THEN "
        "RAISE EXCEPTION '0004 downgrade refused: import or validation data exists'; "
        "END IF; "
        "END $$;"
    )


def downgrade() -> None:
    _assert_reversible()
    for index_name, table_name, _columns in reversed(INDEX_DEFINITIONS):
        op.drop_index(index_name, table_name=table_name, schema=SCHEMA)
    # Reverse creation order: issues own no child, runs own issues, and both
    # are referenced by network_imports' sibling job key.
    op.drop_table("network_validation_issues", schema=SCHEMA)
    op.drop_table("validation_runs", schema=SCHEMA)
    op.drop_table("network_imports", schema=SCHEMA)
    for name in reversed(tuple(ENUM_DEFINITIONS)):
        op.execute(sa.text(f"DROP TYPE IF EXISTS {SCHEMA}.{name}"))
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM hydraulic.schema_metadata "
        "WHERE key = 'spatial_backend' "
        f"AND alembic_revision = '{revision}') THEN "
        "RAISE EXCEPTION '0004 downgrade refused: schema metadata is not at "
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
    # 0001-0003 keep the schema, its extensions, and every table they created.
