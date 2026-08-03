"""
Pydantic 数据模式
------------------
请求/响应的数据验证与序列化。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ============================================================
# 通用
# ============================================================


class PaginationParams(BaseModel):
    """分页参数"""

    page: int = Field(default=1, ge=1, description="页码")
    page_size: int = Field(default=20, ge=1, le=100, description="每页条数")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PaginatedResponse(BaseModel):
    """分页响应"""

    items: list[Any]
    total: int
    page: int
    page_size: int
    total_pages: int


# ============================================================
# 认证
# ============================================================


class TokenRequest(BaseModel):
    """登录请求"""

    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)


class TokenResponse(BaseModel):
    """Token 响应"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str
    user: "UserBrief"


class UserBrief(BaseModel):
    """用户简要信息"""

    id: uuid.UUID
    username: str
    email: str
    full_name: Optional[str] = None
    role: str

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 设备
# ============================================================


class DeviceBase(BaseModel):
    """设备基础字段"""

    device_code: str
    name: str
    device_type: str = "manhole_cover"
    latitude: float
    longitude: float
    altitude: float = 0.0
    address: Optional[str] = None
    district: Optional[str] = None


class DeviceCreate(DeviceBase):
    """创建设备"""

    pass


class DeviceUpdate(BaseModel):
    """更新设备"""

    name: Optional[str] = None
    status: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    battery_level: Optional[float] = None
    signal_strength: Optional[int] = None


class DeviceResponse(DeviceBase):
    """设备响应"""

    id: uuid.UUID
    status: str
    battery_level: float
    signal_strength: int
    last_heartbeat: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 告警
# ============================================================


class AlertResponse(BaseModel):
    """告警响应"""

    id: uuid.UUID
    device_id: Optional[uuid.UUID] = None
    alert_type: str
    level: str
    title: str
    description: Optional[str] = None
    snapshot_url: Optional[str] = None
    bbox_coordinates: Optional[dict] = None
    detection_confidence: Optional[float] = None
    is_acknowledged: bool = False
    is_resolved: bool = False
    created_at: datetime
    device_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AlertAcknowledge(BaseModel):
    """告警确认"""

    action: str = Field(..., pattern="^(acknowledge|resolve|ignore)$")


# ============================================================
# 传感器数据
# ============================================================


class SensorReading(BaseModel):
    """传感器读数"""

    time: datetime
    device_id: uuid.UUID
    water_level_mm: Optional[float] = None
    flow_rate_m3h: Optional[float] = None
    water_quality_ph: Optional[float] = None
    water_quality_turbidity: Optional[float] = None
    temperature_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    battery_voltage: Optional[float] = None
    signal_strength: Optional[int] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class SensorReadingBatch(BaseModel):
    """批量传感器读数"""

    readings: list[SensorReading] = Field(..., max_length=1000)


# ============================================================
# 模型管理
# ============================================================


class ModelSwitchRequest(BaseModel):
    """模型切换请求"""

    target_version: str = Field(
        ..., min_length=1, max_length=64, description="目标模型版本名"
    )
    verify: bool = Field(default=True, description="是否在切换前验证模型")

    @field_validator("target_version")
    @classmethod
    def sanitize_version(cls, v: str) -> str:
        """清理版本字符串"""
        return v.strip().lower()


class ModelStatusResponse(BaseModel):
    """模型状态响应"""

    active_version: str
    is_ready: bool
    device: str
    registry: dict[str, Any]


class ModelUploadResponse(BaseModel):
    """模型上传响应"""

    version: str
    file_path: str
    file_size_mb: float
    sha256: str
    message: str


# ============================================================
# 推理
# ============================================================


class InferenceRequest(BaseModel):
    """推理请求"""

    image_base64: Optional[str] = Field(default=None, description="Base64 编码的图像")
    image_url: Optional[str] = Field(default=None, description="图像 URL")
    camera_id: Optional[uuid.UUID] = Field(default=None, description="关联摄像头 ID")
    confidence_threshold: float = Field(default=0.45, ge=0.01, le=1.0)
    iou_threshold: float = Field(default=0.45, ge=0.01, le=1.0)
    return_annotated: bool = Field(default=False, description="是否返回标注后图像")

    @field_validator("image_base64")
    @classmethod
    def check_image_source(cls, v: Optional[str], info) -> Optional[str]:
        """至少提供一种图像来源"""
        image_url = info.data.get("image_url")
        if not v and not image_url:
            raise ValueError("必须提供 image_base64 或 image_url")
        return v


class DetectionResult(BaseModel):
    """单条检测结果"""

    class_id: int
    class_name: str
    confidence: float
    bbox: list[float]


class InferenceResponse(BaseModel):
    """推理响应"""

    detections: list[DetectionResult]
    inference_time_ms: float
    model_version: str
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    annotated_image_base64: Optional[str] = None


# ============================================================
# 视频流
# ============================================================


class StreamCreate(BaseModel):
    """创建视频流"""

    device_id: uuid.UUID
    name: str
    stream_url: str
    protocol: str = "rtsp"
    username: Optional[str] = None
    password: Optional[str] = None


class StreamResponse(BaseModel):
    """视频流响应"""

    id: uuid.UUID
    device_id: uuid.UUID
    name: str
    protocol: str
    hls_url: Optional[str] = None
    webrtc_url: Optional[str] = None
    is_active: bool
    resolution_width: int
    resolution_height: int

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# WebSocket / SSE
# ============================================================


class WSMessage(BaseModel):
    """WebSocket 消息"""

    type: str  # "sensor_update", "alert", "device_status", "control"
    payload: dict[str, Any]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SSESensorEvent(BaseModel):
    """SSE 传感器事件"""

    device_id: str
    water_level_mm: float
    flow_rate_m3h: float
    temperature_c: float
    battery_level: float
    signal_strength: int
    timestamp: str


class SSEAlertEvent(BaseModel):
    """SSE 告警事件"""

    alert_id: str
    alert_type: str
    level: str
    title: str
    description: str
    device_id: str
    device_name: str
    snapshot_url: Optional[str] = None
    latitude: float = 0.0
    longitude: float = 0.0
    timestamp: str
