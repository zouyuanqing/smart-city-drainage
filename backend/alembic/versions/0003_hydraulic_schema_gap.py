"""Close the hydraulic schema gap for the event outbox, worker slots, and
notification delivery tables that /readyz requires but 0002 never created.

Revision ID: 0003_hydraulic_schema_gap
Revises: 0002_hydraulic_schema
Create Date: 2026-09-26

Every column, constraint, index, and seed row below is taken from one of two
frozen sources and nothing else:

* ``docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md`` §3.1 (schema metadata),
  §3.6 (``hydraulic.worker_slots``), §3.8 (``hydraulic.event_outbox`` and
  ``hydraulic.notification_deliveries``), §9.3 (outbox replay/publish), and
  §10 (notification delivery state machine).
* The SQL that the live runtime already issues against these three tables in
  ``backend/app/main.py`` (outbox replay, relay claim, metrics gauges).

0002 is a digest-fixed base revision and is not modified by this file.  This
migration is additive only: it creates three tables and four enum types, seeds
the two frozen worker slots, and re-stamps ``hydraulic.schema_metadata`` so the
immutable profile value keeps mirroring the declared Alembic head.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_hydraulic_schema_gap"
down_revision: Union[str, None] = "0002_hydraulic_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "hydraulic"
PRIOR_REVISION = "0002_hydraulic_schema"

# The four enum domains are additive.  0002 owns every type it created and this
# migration never alters or drops one of them.
ENUM_DEFINITIONS: dict[str, tuple[str, ...]] = {
    "outbox_event_type": (
        "alert.created",
        "alert.updated",
        "alert.escalated",
        "alert.observed",
        "alert.resolved",
        "alert.expired",
    ),
    "worker_lane": ("general", "nowcast"),
    "notification_channel": ("in_app", "email", "webhook"),
    "notification_delivery_status": (
        "pending",
        "sending",
        "retry_wait",
        "delivered",
        "dead_letter",
        "suppressed",
    ),
}

# The frozen two-lane capacity: SIM_GLOBAL_CONCURRENCY=2 with
# SIM_NOWCAST_RESERVED_SLOTS=1.  ``lane`` is unique so the reserved nowcast lane
# can never be borrowed by a general worker (contract §0.3 "reserved slot").
WORKER_SLOT_SEED: tuple[tuple[int, str], ...] = (
    (1, "general"),
    (2, "nowcast"),
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
        # the 0002 helper that this file deliberately mirrors.
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


def _create_event_outbox() -> None:
    # Contract §3.8.  ``sequence`` is the SSE Last-Event-ID and is the only
    # ordering the relay and the replay endpoint are allowed to observe.
    op.create_table(
        "event_outbox",
        sa.Column("sequence", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", _enum("outbox_event_type"), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _timestamp("created_at"),
        # Contract §9.3 item 9 rejects an unknown schema_version, so the stored
        # version is a positive integer.
        sa.CheckConstraint(
            "schema_version >= 1", name="ck_hydraulic_event_outbox_schema_version"
        ),
        sa.PrimaryKeyConstraint("sequence", name="pk_hydraulic_event_outbox"),
        sa.UniqueConstraint(
            "event_id", name="uq_hydraulic_event_outbox_event"
        ),
        sa.UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            "event_type",
            name="uq_hydraulic_event_outbox_aggregate_version",
        ),
        schema=SCHEMA,
    )
    # Relay claim: main.py selects published_at IS NULL ordered by sequence with
    # FOR UPDATE SKIP LOCKED, and the backlog gauge counts the same predicate.
    op.create_index(
        "ix_hydraulic_event_outbox_pending",
        "event_outbox",
        ["sequence"],
        postgresql_where=sa.text("published_at IS NULL"),
        schema=SCHEMA,
    )
    # Replay: main.py filters an optional network_id and orders by sequence.
    op.create_index(
        "ix_hydraulic_event_outbox_network_sequence",
        "event_outbox",
        ["network_id", "sequence"],
        schema=SCHEMA,
    )


def _create_worker_slots() -> None:
    # Contract §3.6.  The occupancy CHECK is reproduced verbatim: a free slot
    # carries no occupant, a busy slot carries a worker, a strictly ordered
    # lease/heartbeat pair, and exactly the object its lane owns.
    op.create_table(
        "worker_slots",
        # The frozen slot identifiers are 1 and 2, so the column must not become
        # a generated SMALLSERIAL sequence.
        sa.Column("slot_id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("lane", _enum("worker_lane"), nullable=False),
        sa.Column(
            "busy", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("simulation_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("nowcast_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "slot_id IN (1,2)", name="ck_hydraulic_worker_slots_slot_id"
        ),
        sa.CheckConstraint(
            "((busy = false AND worker_id IS NULL AND simulation_job_id IS NULL "
            "AND nowcast_run_id IS NULL AND lease_until IS NULL "
            "AND heartbeat_at IS NULL) "
            "OR "
            "(busy = true AND worker_id IS NOT NULL AND lease_until IS NOT NULL "
            "AND heartbeat_at IS NOT NULL AND lease_until > heartbeat_at "
            "AND ((lane = 'general' AND simulation_job_id IS NOT NULL "
            "AND nowcast_run_id IS NULL) "
            "OR (lane = 'nowcast' AND simulation_job_id IS NULL "
            "AND nowcast_run_id IS NOT NULL))))",
            name="ck_hydraulic_worker_slots_occupancy",
        ),
        sa.PrimaryKeyConstraint("slot_id", name="pk_hydraulic_worker_slots"),
        sa.UniqueConstraint("lane", name="uq_hydraulic_worker_slots_lane"),
        # An occupied slot is released before its job or run can leave the
        # system, so RESTRICT is the fail-closed direction for both owners.
        sa.ForeignKeyConstraint(
            ["simulation_job_id"],
            [f"{SCHEMA}.simulations.id"],
            name="fk_hydraulic_worker_slots_simulation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["nowcast_run_id"],
            [f"{SCHEMA}.nowcast_runs.id"],
            name="fk_hydraulic_worker_slots_nowcast",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )
    # Contract §3.6: timeout/crash recovery only claims busy rows whose lease has
    # already expired.  The partial predicate keeps the index to occupied rows.
    op.create_index(
        "ix_hydraulic_worker_slots_lease",
        "worker_slots",
        ["lease_until"],
        postgresql_where=sa.text("busy"),
        schema=SCHEMA,
    )
    _seed_worker_slots()


def _seed_worker_slots() -> None:
    # Contract §3.6: "initialization must contain exactly two rows, both
    # busy=false; slot 1=general, slot 2=nowcast", with every occupancy column
    # NULL.  The busy=false branch of the CHECK enforces the NULL columns.
    for slot_id, lane in WORKER_SLOT_SEED:
        op.execute(
            sa.text(
                f"INSERT INTO {SCHEMA}.worker_slots "
                "(slot_id, lane, busy, worker_id, simulation_job_id, "
                "nowcast_run_id, lease_until, heartbeat_at) "
                "VALUES (:slot_id, :lane, false, NULL, NULL, NULL, NULL, NULL)"
            ).bindparams(
                # Bind the lane through its enum type so PostgreSQL receives
                # hydraulic.worker_lane rather than an untyped varchar.
                sa.bindparam("slot_id", slot_id, type_=sa.SmallInteger()),
                sa.bindparam("lane", lane, type_=_enum("worker_lane")),
            )
        )


def _create_notification_deliveries() -> None:
    # Contract §3.8.  The frozen DDL declares no foreign key for event_id or
    # alert_id, so this table is created without one; the unique key below is
    # the only cross-row identity the contract fixes.
    op.create_table(
        "notification_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", _enum("notification_channel"), nullable=False),
        sa.Column("destination_id", sa.String(255), nullable=False),
        sa.Column("template_version", sa.String(64), nullable=False),
        sa.Column(
            "status",
            _enum("notification_delivery_status"),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "attempt_count",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        _timestamp("created_at"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        _timestamp("updated_at"),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_hydraulic_notification_deliveries_attempt_count",
        ),
        # Contract §10 makes retry_wait the only status that schedules a retry,
        # so a due timestamp exists exactly in that status.
        sa.CheckConstraint(
            "((status = 'retry_wait') = (next_attempt_at IS NOT NULL))",
            name="ck_hydraulic_notification_deliveries_retry_due",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_notification_deliveries"),
        sa.UniqueConstraint(
            "event_id",
            "channel",
            "destination_id",
            "template_version",
            name="uq_hydraulic_notification_deliveries_target",
        ),
        schema=SCHEMA,
    )
    # Due-claim index; the same name and shape the 0002 durable job tables use.
    op.create_index(
        "ix_hydraulic_notification_deliveries_retry_due",
        "notification_deliveries",
        ["status", "next_attempt_at"],
        schema=SCHEMA,
    )
    # main.py reads the terminal delivery history ordered by updated_at DESC
    # filtered by status.
    op.create_index(
        "ix_hydraulic_notification_deliveries_status_updated",
        "notification_deliveries",
        ["status", "updated_at"],
        schema=SCHEMA,
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
    # Contract §3.1: ``alembic_revision`` mirrors the declared head, while the
    # ``value`` profile is immutable and never switches in place.  The 0002
    # guard trigger therefore has to be lifted for exactly this one statement
    # and is reinstalled immediately afterwards.  The DO block fails closed when
    # the row is missing, duplicated, or already carries another revision, so a
    # re-run can never silently overwrite a foreign stamp.
    op.execute(
        "DO $$ DECLARE stamped text; BEGIN "
        "IF (SELECT count(*) FROM hydraulic.schema_metadata) <> 1 THEN "
        "RAISE EXCEPTION "
        "'0003 upgrade refused: hydraulic.schema_metadata is not a single row'; "
        "END IF; "
        "SELECT alembic_revision INTO stamped FROM hydraulic.schema_metadata "
        "WHERE key = 'spatial_backend'; "
        f"IF stamped IS DISTINCT FROM '{previous}' THEN RAISE EXCEPTION "
        "'0003 upgrade refused: hydraulic.schema_metadata is not stamped by "
        "the prior revision'; "
        "END IF; "
        "END $$;"
    )
    # Drop the guard, restamp, then reinstall it.  Reinstalling before the
    # UPDATE would re-arm the very trigger that has to be lifted for the write.
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
    _create_event_outbox()
    _create_worker_slots()
    _create_notification_deliveries()
    # Restamped last: any failure above leaves the row at the 0002 stamp, which
    # is what a rolled-back transaction must leave behind.
    _restamp_schema_metadata(PRIOR_REVISION, revision)


def _assert_gap_tables_reversible() -> None:
    # Fail closed exactly like the 0002 downgrade: refuse to discard business
    # rows.  ``worker_slots`` is seeded with two idle rows by this very
    # migration, so only an occupied slot counts as data.
    op.execute(
        "DO $$ BEGIN "
        f"IF EXISTS (SELECT 1 FROM {SCHEMA}.event_outbox LIMIT 1) "
        f"OR EXISTS (SELECT 1 FROM {SCHEMA}.notification_deliveries LIMIT 1) "
        f"OR EXISTS (SELECT 1 FROM {SCHEMA}.worker_slots WHERE busy) THEN "
        "RAISE EXCEPTION '0003 downgrade refused: gap table data exists'; "
        "END IF; "
        f"IF (SELECT count(*) FROM {SCHEMA}.worker_slots) <> "
        f"{len(WORKER_SLOT_SEED)} THEN "
        "RAISE EXCEPTION '0003 downgrade refused: worker slot seed is not intact'; "
        "END IF; "
        "END $$;"
    )


def downgrade() -> None:
    _assert_gap_tables_reversible()
    # Reverse creation order.  The three tables hold no dependency on each
    # other, but the enum types cannot be dropped while a column still uses one.
    op.drop_table("notification_deliveries", schema=SCHEMA)
    op.drop_table("worker_slots", schema=SCHEMA)
    op.drop_table("event_outbox", schema=SCHEMA)
    for name in reversed(tuple(ENUM_DEFINITIONS)):
        op.execute(sa.text(f"DROP TYPE IF EXISTS {SCHEMA}.{name}"))
    # Restore the 0002 stamp so the intermediate revision is self-consistent.
    # The profile value is never touched.  The guard trigger is dropped for the
    # single UPDATE and reinstalled straight afterwards, mirroring the upgrade.
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM hydraulic.schema_metadata "
        "WHERE key = 'spatial_backend' "
        f"AND alembic_revision = '{revision}') THEN "
        "RAISE EXCEPTION '0003 downgrade refused: schema metadata is not at "
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
    # 0002 keeps the schema, its extensions, and every table it created.
