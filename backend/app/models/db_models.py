"""
SQLAlchemy ORM 数据库模型
---------------------------
定义所有持久化实体。
使用异步 SQLAlchemy 2.0 风格。
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey,
    Integer, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""
    pass


class RoleEnum(str, enum.Enum):
    admin = "admin"
    operator = "operator"
    viewer = "viewer"


# ============================================================
# 用户表
# ============================================================

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(Enum(RoleEnum), default=RoleEnum.operator)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # 关系
    alerts_acknowledged: Mapped[list["Alert"]] = relationship(
        "Alert", foreign_keys="Alert.acknowledged_by", back_populates="acknowledger"
    )

    def __repr__(self) -> str:
        return f"<User {self.username}>"


# ============================================================
# 设备表
# ============================================================

class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    device_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    device_type: Mapped[str] = mapped_column(String(64), default="manhole_cover")
    status: Mapped[str] = mapped_column(
        Enum("online", "offline", "fault", "maintenance", name="device_status"),
        default="offline",
    )
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    altitude: Mapped[float] = mapped_column(Float, default=0.0)
    address: Mapped[Optional[str]] = mapped_column(Text)
    district: Mapped[Optional[str]] = mapped_column(String(128))
    install_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    firmware_version: Mapped[Optional[str]] = mapped_column(String(32))
    battery_level: Mapped[float] = mapped_column(Float, default=100.0)
    signal_strength: Mapped[int] = mapped_column(Integer, default=100)
    extra_data: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    last_heartbeat: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # 关系
    camera_streams: Mapped[list["CameraStream"]] = relationship(
        "CameraStream", back_populates="device", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["Alert"]] = relationship(
        "Alert", foreign_keys="Alert.device_id", back_populates="device"
    )

    def __repr__(self) -> str:
        return f"<Device {self.device_code} [{self.status}]>"


# ============================================================
# 摄像头流表
# ============================================================

class CameraStream(Base):
    __tablename__ = "camera_streams"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    stream_url: Mapped[str] = mapped_column(Text, nullable=False)
    protocol: Mapped[str] = mapped_column(
        Enum("rtsp", "hls", "webrtc", "local", name="stream_protocol"),
        default="rtsp",
    )
    hls_url: Mapped[Optional[str]] = mapped_column(Text)
    webrtc_url: Mapped[Optional[str]] = mapped_column(Text)
    username: Mapped[Optional[str]] = mapped_column(String(128))
    password_encrypted: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    resolution_width: Mapped[int] = mapped_column(Integer, default=1920)
    resolution_height: Mapped[int] = mapped_column(Integer, default=1080)
    fps: Mapped[int] = mapped_column(Integer, default=25)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # 关系
    device: Mapped["Device"] = relationship("Device", back_populates="camera_streams")

    def __repr__(self) -> str:
        return f"<CameraStream {self.name} [{self.protocol}]>"


# ============================================================
# 告警表
# ============================================================

class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    device_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL")
    )
    alert_type: Mapped[str] = mapped_column(
        Enum(
            "water_accumulation", "manhole_anomaly", "intrusion",
            "illegal_parking", "water_level_high", "flow_anomaly",
            "device_offline", "system_error",
            name="alert_type",
        ),
        nullable=False,
    )
    level: Mapped[str] = mapped_column(
        Enum("critical", "warning", "info", name="alert_level"),
        default="info",
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    snapshot_url: Mapped[Optional[str]] = mapped_column(Text)
    bbox_coordinates: Mapped[Optional[dict]] = mapped_column(JSON)
    detection_confidence: Mapped[Optional[float]] = mapped_column(Float)
    is_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    alert_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )

    # 关系
    device: Mapped[Optional["Device"]] = relationship(
        "Device", foreign_keys=[device_id], back_populates="alerts"
    )
    acknowledger: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[acknowledged_by], back_populates="alerts_acknowledged"
    )

    def __repr__(self) -> str:
        return f"<Alert [{self.level}] {self.title}>"


# ============================================================
# 模型版本表
# ============================================================

class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version_name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    sha256_hash: Mapped[Optional[str]] = mapped_column(String(64))
    model_type: Mapped[str] = mapped_column(String(64), default="yolov8")
    status: Mapped[str] = mapped_column(
        Enum("loading", "active", "unloading", "error", name="model_status"),
        default="loading",
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    deployed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    deployed_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<ModelVersion {self.version_name} [{self.status}]>"


# ============================================================
# 推理结果表
# ============================================================

class InferenceResult(Base):
    __tablename__ = "inference_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("camera_streams.id", ondelete="CASCADE")
    )
    model_version: Mapped[Optional[str]] = mapped_column(String(32))
    inference_time_ms: Mapped[Optional[float]] = mapped_column(Float)
    detections: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    frame_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<InferenceResult detections={len(self.detections)}>"
