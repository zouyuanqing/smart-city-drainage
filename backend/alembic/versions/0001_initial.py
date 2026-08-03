"""Initial migration — create all core tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable extensions
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    # Users
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("username", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(128)),
        sa.Column("role", sa.String(32), server_default="operator"),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("last_login", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )

    # Devices
    op.create_table(
        "devices",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "device_code", sa.String(64), unique=True, nullable=False, index=True
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("device_type", sa.String(64), server_default="manhole_cover"),
        sa.Column(
            "status",
            sa.Enum("online", "offline", "fault", "maintenance", name="device_status"),
            server_default="offline",
        ),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("altitude", sa.Float(), server_default="0"),
        sa.Column("address", sa.Text()),
        sa.Column("district", sa.String(128)),
        sa.Column("install_date", sa.DateTime()),
        sa.Column("firmware_version", sa.String(32)),
        sa.Column("battery_level", sa.Float(), server_default="100"),
        sa.Column("signal_strength", sa.Integer(), server_default="100"),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )

    # Camera streams
    op.create_table(
        "camera_streams",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("stream_url", sa.Text(), nullable=False),
        sa.Column(
            "protocol",
            sa.Enum("rtsp", "hls", "webrtc", "local", name="stream_protocol"),
            server_default="rtsp",
        ),
        sa.Column("hls_url", sa.Text()),
        sa.Column("webrtc_url", sa.Text()),
        sa.Column("username", sa.String(128)),
        sa.Column("password_encrypted", sa.String(255)),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("resolution_width", sa.Integer(), server_default="1920"),
        sa.Column("resolution_height", sa.Integer(), server_default="1080"),
        sa.Column("fps", sa.Integer(), server_default="25"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )

    # Alerts
    op.create_table(
        "alerts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "alert_type",
            sa.Enum(
                "water_accumulation",
                "manhole_anomaly",
                "intrusion",
                "illegal_parking",
                "water_level_high",
                "flow_anomaly",
                "device_offline",
                "system_error",
                name="alert_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "level",
            sa.Enum("critical", "warning", "info", name="alert_level"),
            server_default="info",
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("snapshot_url", sa.Text()),
        sa.Column("bbox_coordinates", postgresql.JSONB()),
        sa.Column("detection_confidence", sa.Float()),
        sa.Column("is_acknowledged", sa.Boolean(), server_default=sa.text("false")),
        sa.Column(
            "acknowledged_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("is_resolved", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )

    # Model versions
    op.create_table(
        "model_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("version_name", sa.String(32), unique=True, nullable=False),
        sa.Column("file_path", sa.String(512), nullable=False),
        sa.Column("file_size_bytes", sa.Integer()),
        sa.Column("sha256_hash", sa.String(64)),
        sa.Column("model_type", sa.String(64), server_default="yolov8"),
        sa.Column(
            "status",
            sa.Enum("loading", "active", "unloading", "error", name="model_status"),
            server_default="loading",
        ),
        sa.Column("metrics", postgresql.JSONB(), server_default="{}"),
        sa.Column("deployed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "deployed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )

    # Inference results
    op.create_table(
        "inference_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "camera_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("camera_streams.id", ondelete="CASCADE"),
        ),
        sa.Column("model_version", sa.String(32)),
        sa.Column("inference_time_ms", sa.Float()),
        sa.Column(
            "detections", postgresql.JSONB(), nullable=False, server_default="[]"
        ),
        sa.Column("frame_timestamp", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )

    # Create indexes
    op.create_index("idx_alerts_created", "alerts", ["created_at"])
    op.create_index("idx_alerts_device", "alerts", ["device_id", "created_at"])

    # Insert default admin user (password: Admin@123456)
    op.execute(
        "INSERT INTO users (username, email, hashed_password, full_name, role) VALUES "
        "('admin', 'admin@smartcity.local', "
        "'$2b$12$LJ3m4ys3GZfnYMz8kVsKaOTSxGhvzRq1ZQ0nOMWhbFPEHsGj5TJv.', "
        "'系统管理员', 'admin') "
        "ON CONFLICT (username) DO NOTHING"
    )


def downgrade() -> None:
    op.drop_table("inference_results")
    op.drop_table("model_versions")
    op.drop_table("alerts")
    op.drop_table("camera_streams")
    op.drop_table("devices")
    op.drop_table("users")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS alert_type")
    op.execute("DROP TYPE IF EXISTS alert_level")
    op.execute("DROP TYPE IF EXISTS model_status")
    op.execute("DROP TYPE IF EXISTS stream_protocol")
    op.execute("DROP TYPE IF EXISTS device_status")
