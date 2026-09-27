"""Observation trust, ingest, and the Timescale sample hypertable.

Revision ID: 0005_hydraulic_observations
Revises: 0004_hydraulic_imports
Create Date: 2026-09-27

Position 5 in the contract §13.2 revision chain.  It follows 0004 because the
observation tables need nothing from it; the ordering is fixed by §13.2 so the
chain stays linear with a single head.

Every column, constraint, and index below comes from
``docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md`` §3.7 and nothing else:

* ``hydraulic.observation_trust_policies`` (contract lines 822-834 and 836)
* ``hydraulic.observation_ingest_events`` (contract lines 840-872)
* ``hydraulic.observation_samples`` (contract lines 878-896)

The revision is profile-independent: none of the three tables carries a spatial
column, so the rendered DDL is byte-identical for the ``portable`` and
``postgis`` profiles.

Two dependencies are inherited rather than re-created:

* ``btree_gist`` is required by the frozen exclusion constraint.  Contract
  §13.2 keeps ``CREATE EXTENSION`` in the init SQL, and 0002 already ran
  ``CREATE EXTENSION IF NOT EXISTS "btree_gist"`` unconditionally on both
  profiles, so re-issuing it here would take extension ownership away from the
  revision that owns it.
* TimescaleDB is required by both profiles and is created by 0002 as well.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_hydraulic_observations"
down_revision: Union[str, None] = "0004_hydraulic_imports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "hydraulic"
PRIOR_REVISION = "0004_hydraulic_imports"

# Contract §3.7 line 894: "Timescale hypertable, 90-day retention".  The chunk
# interval is not fixed by the contract, so this revision reuses the physical
# value 0002 already applies to its only hypertable.
OBSERVATION_RETENTION_DAYS = 90
OBSERVATION_CHUNK_INTERVAL = "1 day"

# Additive enum domains.  ``observation_trust_level`` is shared by the policy
# and the ingest event because §3.7 declares one identical four-value domain on
# both tables.
ENUM_DEFINITIONS: dict[str, tuple[str, ...]] = {
    "observation_auth_requirement": ("authenticated", "allowlisted_local"),
    "observation_binding_requirement": ("active_required", "none"),
    "observation_trust_level": (
        "external_verified",
        "external_unverified",
        "synthetic",
        "mock",
    ),
    "observation_trust_policy_resolution": ("matched", "missing", "expired"),
    "observation_ingest_status": ("accepted", "conflict", "quarantined"),
    "observation_target_type": ("node", "pipe_segment"),
    "observation_metric": ("water_depth_m", "flow_m3s"),
    "observation_quality_flag": ("good", "bad", "missing", "stale"),
}

# Supporting indexes.  ``ix_..._source_observed`` exists because §3.7 line 836
# makes ``source_observed_at`` the explicit policy-version selector; the sample
# indexes follow the §3.7 lines 872/895 read paths (per-event rollback lookup
# and per-network assimilation windows).
INDEX_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "ix_hydraulic_observation_trust_policies_source",
        "observation_trust_policies",
        ("source_system", "valid_from"),
    ),
    (
        "ix_hydraulic_observation_ingest_events_trust_policy",
        "observation_ingest_events",
        ("trust_policy_id",),
    ),
    (
        "ix_hydraulic_observation_ingest_events_observed",
        "observation_ingest_events",
        ("source_observed_at",),
    ),
    (
        "ix_hydraulic_observation_samples_ingest_event",
        "observation_samples",
        ("ingest_event_id",),
    ),
    (
        "ix_hydraulic_observation_samples_network_observed",
        "observation_samples",
        ("network_id", "observed_at"),
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


def _create_observation_trust_policies() -> None:
    # Contract §3.7.  The policy row is the only place a trust level is
    # declared; an ingest event may never self-report one (§3.7 line 871).
    op.create_table(
        "observation_trust_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(128), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column(
            "auth_requirement",
            _enum("observation_auth_requirement"),
            nullable=False,
        ),
        sa.Column(
            "binding_requirement",
            _enum("observation_binding_requirement"),
            nullable=False,
        ),
        sa.Column(
            "trust_level", _enum("observation_trust_level"), nullable=False
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_sha256", sa.String(64), nullable=False),
        _timestamp("created_at"),
        sa.PrimaryKeyConstraint(
            "id", name="pk_hydraulic_observation_trust_policies"
        ),
        sa.UniqueConstraint(
            "source_system",
            "policy_version",
            name="uq_hydraulic_observation_trust_policies_version",
        ),
        # Contract §3.7 line 836: an external_verified policy must declare both
        # the authentication and the active-binding requirement.  The DB refuses
        # any combination that would claim verification without enforcing it.
        sa.CheckConstraint(
            "trust_level <> 'external_verified' OR "
            "(auth_requirement = 'authenticated' "
            "AND binding_requirement = 'active_required')",
            name="ck_hydraulic_observation_trust_policies_verified",
        ),
        schema=SCHEMA,
    )
    # Contract §3.7 line 833: validity windows of one source may never overlap,
    # so the adapter is forced to pick a version by source_observed_at instead
    # of silently taking the newest row.  btree_gist supplies the ``=`` operator
    # class for the text column.
    op.execute(
        "ALTER TABLE hydraulic.observation_trust_policies ADD CONSTRAINT "
        "ex_hydraulic_observation_trust_policies_validity "
        "EXCLUDE USING gist ("
        "source_system WITH =, "
        "tstzrange(valid_from, valid_until, '[)') WITH &&)"
    )


def _create_observation_ingest_events() -> None:
    # Contract §3.7.  Source identity is the stable offset tuple, so neither the
    # observed timestamp nor the payload may stand in for it.
    op.create_table(
        "observation_ingest_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(128), nullable=False),
        sa.Column("source_stream", sa.String(128), nullable=False),
        sa.Column("source_partition", sa.String(128), nullable=False),
        sa.Column("source_offset", sa.String(128), nullable=False),
        sa.Column("source_event_id", sa.String(64), nullable=False),
        sa.Column(
            "source_observed_at", sa.DateTime(timezone=True), nullable=False
        ),
        _timestamp("received_at"),
        sa.Column("adapter_version", sa.String(64), nullable=False),
        sa.Column("trust_policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trust_policy_version", sa.Integer(), nullable=True),
        sa.Column("trust_policy_sha256", sa.String(64), nullable=True),
        sa.Column(
            "trust_policy_resolution",
            _enum("observation_trust_policy_resolution"),
            nullable=False,
        ),
        sa.Column(
            "trust_level", _enum("observation_trust_level"), nullable=False
        ),
        sa.Column("auth_verified", sa.Boolean(), nullable=False),
        sa.Column("binding_verified", sa.Boolean(), nullable=False),
        sa.Column("source_payload_sha256", sa.String(64), nullable=False),
        sa.Column(
            "status", _enum("observation_ingest_status"), nullable=False
        ),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint(
            "id", name="pk_hydraulic_observation_ingest_events"
        ),
        sa.UniqueConstraint(
            "source_event_id",
            name="uq_hydraulic_observation_ingest_events_event",
        ),
        sa.UniqueConstraint(
            "source_system",
            "source_stream",
            "source_partition",
            "source_offset",
            name="uq_hydraulic_observation_ingest_events_offset",
        ),
        # Contract §3.7 lines 861-864, reproduced verbatim.
        sa.CheckConstraint(
            "(trust_policy_resolution <> 'missing') OR "
            "(trust_policy_id IS NULL AND trust_policy_version IS NULL "
            "AND trust_policy_sha256 IS NULL)",
            name="ck_hydraulic_observation_ingest_events_missing_policy",
        ),
        sa.CheckConstraint(
            "(trust_policy_resolution NOT IN ('matched','expired')) OR "
            "(trust_policy_id IS NOT NULL AND trust_policy_version IS NOT NULL "
            "AND trust_policy_sha256 IS NOT NULL)",
            name="ck_hydraulic_observation_ingest_events_resolved_policy",
        ),
        sa.ForeignKeyConstraint(
            ["trust_policy_id"],
            [f"{SCHEMA}.observation_trust_policies.id"],
            name="fk_hydraulic_observation_ingest_events_trust_policy",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )


def _create_observation_samples() -> None:
    # Contract §3.7.  ``observed_at`` leads the primary key because it is the
    # hypertable partition column; every sample of one event shares it, which is
    # what makes ``(ingest_event_id, sample_index)`` unique inside a chunk.
    # The frozen DDL declares no foreign key for ``network_id``, so none is
    # created here.
    op.create_table(
        "observation_samples",
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingest_event_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("sample_index", sa.Integer(), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "target_type", _enum("observation_target_type"), nullable=False
        ),
        sa.Column("target_stable_key", sa.String(128), nullable=False),
        sa.Column("metric", _enum("observation_metric"), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column(
            "quality_flag", _enum("observation_quality_flag"), nullable=False
        ),
        sa.Column("source_point_ref", postgresql.JSONB(), nullable=False),
        sa.Column("ingest_version", sa.String(64), nullable=False),
        _timestamp("created_at"),
        sa.PrimaryKeyConstraint(
            "observed_at",
            "ingest_event_id",
            "sample_index",
            name="pk_hydraulic_observation_samples",
        ),
        sa.ForeignKeyConstraint(
            ["ingest_event_id"],
            [f"{SCHEMA}.observation_ingest_events.id"],
            name="fk_hydraulic_observation_samples_ingest_event",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.execute(
        "SELECT create_hypertable("
        f"'{SCHEMA}.observation_samples', 'observed_at', "
        f"chunk_time_interval => INTERVAL '{OBSERVATION_CHUNK_INTERVAL}', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )
    op.execute(
        "SELECT add_retention_policy("
        f"'{SCHEMA}.observation_samples', "
        f"drop_after => INTERVAL '{OBSERVATION_RETENTION_DAYS} days', "
        "if_not_exists => TRUE, schedule_interval => INTERVAL '1 day')"
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
        "'0005 upgrade refused: hydraulic.schema_metadata is not a single row'; "
        "END IF; "
        "SELECT alembic_revision INTO stamped FROM hydraulic.schema_metadata "
        "WHERE key = 'spatial_backend'; "
        f"IF stamped IS DISTINCT FROM '{previous}' THEN RAISE EXCEPTION "
        "'0005 upgrade refused: hydraulic.schema_metadata is not stamped by "
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
    _create_observation_trust_policies()
    _create_observation_ingest_events()
    _create_observation_samples()
    _create_indexes()
    _restamp_schema_metadata(PRIOR_REVISION, revision)


def _assert_reversible() -> None:
    op.execute(
        "DO $$ BEGIN "
        f"IF EXISTS (SELECT 1 FROM {SCHEMA}.observation_samples LIMIT 1) "
        f"OR EXISTS (SELECT 1 FROM {SCHEMA}.observation_ingest_events LIMIT 1) "
        f"OR EXISTS (SELECT 1 FROM {SCHEMA}.observation_trust_policies LIMIT 1) "
        "THEN RAISE EXCEPTION "
        "'0005 downgrade refused: observation data exists'; "
        "END IF; "
        "END $$;"
    )


def downgrade() -> None:
    _assert_reversible()
    for index_name, table_name, _columns in reversed(INDEX_DEFINITIONS):
        op.drop_index(index_name, table_name=table_name, schema=SCHEMA)
    # Reverse creation order.  Dropping the hypertable removes its chunks and
    # its retention policy, so no Timescale object survives the rollback.
    op.drop_table("observation_samples", schema=SCHEMA)
    op.drop_table("observation_ingest_events", schema=SCHEMA)
    op.drop_table("observation_trust_policies", schema=SCHEMA)
    for name in reversed(tuple(ENUM_DEFINITIONS)):
        op.execute(sa.text(f"DROP TYPE IF EXISTS {SCHEMA}.{name}"))
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM hydraulic.schema_metadata "
        "WHERE key = 'spatial_backend' "
        f"AND alembic_revision = '{revision}') THEN "
        "RAISE EXCEPTION '0005 downgrade refused: schema metadata is not at "
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
    # 0001-0004 keep the schema, its extensions, and every table they created.
