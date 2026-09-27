"""
API 路由汇总
-------------
所有 REST API 端点。
使用 FastAPI Router 进行模块化组织。
"""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import os
import tempfile
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from app.api.hydraulic_routes import router as hydraulic_router
from app.core.config import settings
from app.core.database import get_db_session
from app.core.model_manager import (
    MODEL_EXTENSIONS,
    UnsafeModelPath,
    get_model_manager,
    model_storage_root,
    safe_model_path,
    validate_model_version,
)
from app.core.security import (
    OutboundURLRejected,
    authenticate_access_token,
    create_access_token,
    extract_bearer_token,
    require_auth,
    require_role,
    resolve_outbound_url,
    verify_password,
)
from app.models.db_models import Alert as AlertModel
from app.models.db_models import Device as DeviceModel
from app.models.db_models import RoleEnum
from app.models.db_models import User as UserModel
from app.schemas.schemas import (
    AlertAcknowledge,
    InferenceRequest,
    InferenceResponse,
    ModelStatusResponse,
    ModelSwitchRequest,
    ModelUploadResponse,
    StreamCreate,
    TokenRequest,
    TokenResponse,
    UserBrief,
)
from app.services.inference_service import inference_service
from app.services.mock_data_generator import mock_generator
from app.services.sse_manager import SSEManager
from app.services.stream_service import stream_service
from app.services.system_status import get_system_status
from app.workers.inference_worker import MediaResourceLimitExceeded

router = APIRouter(prefix="/api")
sse_manager = SSEManager.get_instance()
_MAX_INFERENCE_IMAGE_BYTES = 10 * 1024 * 1024


async def _authenticate_websocket_token(token: str) -> dict[str, Any] | None:
    """Validate a WebSocket token against the current user in deployments."""
    try:
        async with get_db_session() as db:
            return await authenticate_access_token(token, db)
    except HTTPException:
        return None


async def _require_websocket_role(
    websocket: WebSocket, allowed_roles: set[str]
) -> dict[str, Any] | None:
    """Authenticate header-capable and native-browser control clients.

    Browser WebSocket constructors cannot set Authorization headers and query
    credentials leak into history/proxy logs. When a header is absent, accept
    the socket and require one short-lived authentication frame immediately.
    """
    token = extract_bearer_token(websocket.headers.get("authorization"))
    if token is None:
        await websocket.accept()
        try:
            message = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
        except (
            asyncio.TimeoutError,
            WebSocketDisconnect,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            await websocket.close(code=1008)
            return None
        if (
            not isinstance(message, dict)
            or message.get("type") != "authenticate"
            or not isinstance(message.get("token"), str)
            or len(message["token"]) > 8192
        ):
            await websocket.close(code=1008)
            return None
        token = message["token"]

    claims = await _authenticate_websocket_token(token)
    role = claims.get("role") if claims is not None else None
    if not isinstance(role, str) or role not in allowed_roles:
        await websocket.close(code=1008)
        return None

    await websocket.accept()
    return claims


async def _fetch_public_image(url: str) -> bytes:
    """Fetch an image with redirect, address and response-size checks."""
    import httpx

    current_url = url
    timeout = httpx.Timeout(10.0, connect=5.0)
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=timeout,
            trust_env=False,
        ) as client:
            for _ in range(3):
                target = resolve_outbound_url(
                    current_url, allowed_schemes={"http", "https"}
                )
                request_extensions = (
                    {"sni_hostname": target.sni_hostname}
                    if httpx.URL(target.connection_url).scheme == "https"
                    else {}
                )
                async with client.stream(
                    "GET",
                    target.connection_url,
                    headers={"Host": target.host_header},
                    extensions=request_extensions,
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise OutboundURLRejected(
                                "outbound redirect has no location"
                            )
                        current_url = str(httpx.URL(current_url).join(location))
                        continue
                    if response.status_code >= 400:
                        raise OutboundURLRejected("outbound image request failed")
                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            if int(content_length) > _MAX_INFERENCE_IMAGE_BYTES:
                                raise OutboundURLRejected("outbound image is too large")
                        except ValueError as exc:
                            raise OutboundURLRejected(
                                "outbound image length is invalid"
                            ) from exc
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > _MAX_INFERENCE_IMAGE_BYTES:
                            raise OutboundURLRejected("outbound image is too large")
                        chunks.append(chunk)
                    return b"".join(chunks)
    except OutboundURLRejected:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise OutboundURLRejected("outbound image request failed") from exc
    raise OutboundURLRejected("too many outbound redirects")


# ============================================================
# 健康检查
# ============================================================


@router.get("/health")
async def health_check():
    """系统健康检查"""
    manager = get_model_manager()
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
        "model_ready": manager.is_ready,
        "active_model": manager.active_version,
        "sse_clients": sse_manager.client_count,
    }


@router.get("/system/status")
async def system_status():
    return await get_system_status()


# ============================================================
# 认证
# ============================================================


@router.post("/auth/login", response_model=TokenResponse)
async def login(request: TokenRequest):
    user = None
    auth_backend_unavailable = False
    try:
        async with get_db_session() as db:
            result = await db.execute(
                select(UserModel).where(UserModel.username == request.username)
            )
            user = result.scalar_one_or_none()
            if user is not None:
                if not user.is_active:
                    raise HTTPException(status_code=401, detail="用户名或密码错误")
                if not verify_password(request.password, user.hashed_password):
                    raise HTTPException(status_code=401, detail="用户名或密码错误")
                user.last_login = datetime.now(timezone.utc)
                await db.commit()
    except HTTPException:
        raise
    except Exception:
        user = None
        auth_backend_unavailable = True

    if user is None:
        # The fixed demo identity is opt-in and never available in production,
        # so a database outage can not mint a repository-credential admin token.
        if not settings.auth_fallback_admin_allowed:
            if auth_backend_unavailable:
                raise HTTPException(
                    status_code=503, detail="认证后端暂时不可用，请稍后重试"
                )
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        if request.username == "admin" and request.password == "Admin@123456":
            user = type(
                "FallbackUser",
                (),
                {
                    "id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
                    "username": "admin",
                    "email": "admin@smartcity.local",
                    "full_name": "系统管理员",
                    "role": "admin",
                },
            )()
        else:
            raise HTTPException(status_code=401, detail="用户名或密码错误")

    expires_minutes = settings.ACCESS_TOKEN_EXPIRE_MINUTES
    access_token = create_access_token(
        subject=str(user.id),
        expires_delta=timedelta(minutes=expires_minutes),
        extra_claims={
            "username": user.username,
            "role": user.role.value if isinstance(user.role, RoleEnum) else user.role,
            "is_active": True,
        },
    )

    user_role = user.role.value if isinstance(user.role, RoleEnum) else user.role

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_minutes * 60,
        role=user_role,
        user=UserBrief(
            id=user.id,
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            role=user_role,
        ),
    )


@router.get("/auth/me")
async def get_me(user: dict = Depends(require_auth)):
    """获取当前用户信息"""
    return {
        "user_id": user.get("sub"),
        "username": user.get("username"),
        "role": user.get("role"),
    }


# ============================================================
# 模型管理 (Model Hot-Switching)
# ============================================================


@router.get("/models/status", response_model=ModelStatusResponse)
async def get_model_status():
    """获取当前模型状态"""
    manager = get_model_manager()
    return manager.get_status()


@router.post("/models/switch")
async def switch_model(
    request: ModelSwitchRequest,
    user: dict = Depends(require_role(RoleEnum.admin)),
):
    """
    热切换 AI 模型 (零停机)

    支持异步后台切换。如果 verify=True，会先验证新模型再原子替换。
    """
    manager = get_model_manager()

    try:
        validate_model_version(request.target_version)
        # 使用后台线程异步切换
        manager.switch_model_async(request.target_version)
        return {
            "message": f"模型热切换已启动: → {request.target_version}",
            "from_version": manager.active_version,
            "to_version": request.target_version,
            "status": "switching",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"模型切换失败: {exc}")


@router.post("/models/upload", response_model=ModelUploadResponse)
async def upload_model(
    file: UploadFile = File(...),
    version: str = Query(..., description="模型版本名"),
    user: dict = Depends(require_role(RoleEnum.admin)),
):
    """
    上传新模型权重文件到仓库
    """
    from app.core.config import settings

    if not file.filename:
        raise HTTPException(status_code=415, detail="模型文件名不能为空")
    ext = Path(file.filename).suffix.lower()
    if ext not in MODEL_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail="不支持的文件格式，请上传 .pt / .onnx / .engine 文件",
        )

    import hashlib

    try:
        version = validate_model_version(version)
        root = model_storage_root()
        save_path = safe_model_path(version, ext, root=root)
    except UnsafeModelPath as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    max_bytes = int(settings.MAX_UPLOAD_SIZE_MB) * 1024 * 1024
    if max_bytes <= 0:
        raise HTTPException(status_code=503, detail="模型上传大小限制未配置")

    # 先写同目录临时文件，超过限制时立即删除，成功后原子替换。
    temporary_path: Path | None = None
    sha = hashlib.sha256()
    size_bytes = 0
    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{version}.", suffix=".part", dir=str(root)
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(fd, "wb") as output:
            while chunk := await file.read(8192):
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"模型文件不能超过 {settings.MAX_UPLOAD_SIZE_MB} MB",
                    )
                output.write(chunk)
                sha.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, save_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    # 扫描仓库
    manager = get_model_manager()
    manager.scan_repository()

    return ModelUploadResponse(
        version=version,
        file_path=save_path.name,
        file_size_mb=round(size_bytes / 1e6, 2),
        sha256=sha.hexdigest(),
        message=f"模型 '{version}' 已上传，可通过 /api/models/switch 激活",
    )


@router.get("/models/versions")
async def list_model_versions():
    """列出所有可用模型版本"""
    manager = get_model_manager()
    registry = manager.scan_repository()
    return {
        "versions": [
            {
                "version": v,
                "model_type": m.model_type,
                "file_size_mb": round(m.file_size_bytes / 1e6, 2),
                "sha256": m.sha256_hash[:16] + "..." if m.sha256_hash else "",
                "status": m.status.value,
            }
            for v, m in registry.items()
        ]
    }


# ============================================================
# AI 推理
# ============================================================


@router.post("/inference/detect", response_model=InferenceResponse)
async def detect_objects(
    request: InferenceRequest,
    current_user: dict = Depends(require_role(RoleEnum.admin, RoleEnum.operator)),
):
    """
    YOLO 目标检测推理

    支持 Base64 图像或 URL 图像。返回检测框、类别、置信度。
    """
    try:
        if request.image_url:
            image_bytes = await _fetch_public_image(request.image_url)
            result = await inference_service.infer_from_base64(
                base64.b64encode(image_bytes).decode("ascii"),
                confidence_threshold=request.confidence_threshold,
                iou_threshold=request.iou_threshold,
                return_annotated=request.return_annotated,
            )
        elif request.image_base64:
            result = await inference_service.infer_from_base64(
                request.image_base64,
                confidence_threshold=request.confidence_threshold,
                iou_threshold=request.iou_threshold,
                return_annotated=request.return_annotated,
            )
        else:
            raise HTTPException(
                status_code=400, detail="必须提供 image_base64 或 image_url"
            )

        return InferenceResponse(**result)

    except OutboundURLRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"推理服务异常: {exc}")


# ============================================================
# 视频流管理
# ============================================================


@router.post("/streams/start", response_model=dict)
async def start_stream(
    request: StreamCreate,
    user: dict = Depends(require_role(RoleEnum.admin, RoleEnum.operator)),
):
    """启动 RTSP → HLS 转码"""
    try:
        target = resolve_outbound_url(
            request.stream_url,
            allowed_schemes={"rtsp", "rtsps", "rtmp", "rtmps"},
        )
        stream = await stream_service.start_stream(
            camera_id=str(request.device_id),
            rtsp_url=target.connection_url,
            source_url=request.stream_url,
        )
        return {
            "camera_id": stream.camera_id,
            "hls_url": f"/hls/{stream.camera_id}/index.m3u8",
            "status": "started" if stream.is_healthy else "error",
            "error": stream.error_message,
        }
    except OutboundURLRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MediaResourceLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"启动转码失败: {exc}")


@router.post("/streams/{camera_id}/stop")
async def stop_stream(
    camera_id: str,
    user: dict = Depends(require_role(RoleEnum.admin, RoleEnum.operator)),
):
    """停止视频流转码"""
    await stream_service.stop_stream(camera_id)
    return {"status": "stopped", "camera_id": camera_id}


@router.get("/streams/status")
async def get_streams_status():
    """获取所有流状态"""
    return {"streams": stream_service.get_all_streams_status()}


@router.post("/streams/{camera_id}/inference/start")
async def start_inference(
    camera_id: str,
    current_user: dict = Depends(require_role(RoleEnum.admin, RoleEnum.operator)),
):
    from app.services.stream_service import stream_service

    stream_info = stream_service.get_stream(camera_id)
    if not stream_info:
        raise HTTPException(status_code=404, detail="视频流不存在")
    try:
        await stream_service.worker_pool.add_worker(
            camera_id=camera_id,
            stream_url=getattr(stream_info, "connection_url", stream_info.source_url),
            host_header=getattr(stream_info, "host_header", None),
            inference_interval=2.0,
            confidence_threshold=0.45,
        )
        return {"camera_id": camera_id, "inference": "started"}
    except MediaResourceLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"启动推理失败: {exc}")


@router.post("/streams/{camera_id}/inference/stop")
async def stop_inference(
    camera_id: str,
    current_user: dict = Depends(require_role(RoleEnum.admin, RoleEnum.operator)),
):
    from app.services.stream_service import stream_service

    try:
        await stream_service.worker_pool.remove_worker(camera_id)
        return {"camera_id": camera_id, "inference": "stopped"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"停止推理失败: {exc}")


# ============================================================
# SSE 实时数据推送
# ============================================================


@router.get("/sse/events")
async def sse_events(request: Request):
    """
    SSE 事件流端点

    推送:
      - sensors: 传感器实时数据
      - alerts: 告警事件
      - system: 系统状态变更

    支持 Last-Event-ID 请求头实现断线重连后事件重放。

    前端使用 EventSource 连接:
      const es = new EventSource('/api/sse/events');
      es.addEventListener('sensors', (e) => {...});
    """
    last_event_id = request.headers.get("Last-Event-ID")
    client_id, queue = await sse_manager.connect(last_event_id)
    return EventSourceResponse(
        sse_manager.event_generator(client_id, queue, last_event_id)
    )


# ============================================================
# WebSocket
# ============================================================


@router.websocket("/ws/control")
async def websocket_control(websocket: WebSocket):
    """
    WebSocket 控制通道

    用于:
      - 前端发送控制指令 (如 PTZ 云台控制)
      - 后端推送设备状态变更
    """
    if (
        await _require_websocket_role(
            websocket, {RoleEnum.admin.value, RoleEnum.operator.value}
        )
        is None
    ):
        return
    client_id = str(uuid.uuid4())[:8]
    logger = __import__("logging").getLogger(__name__)
    logger.info("🔌 WebSocket 客户端连接: %s", client_id)

    try:
        await websocket.send_json(
            {
                "type": "connected",
                "payload": {"client_id": client_id, "message": "控制通道已建立"},
            }
        )

        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong", "payload": {}})
            elif msg_type == "control":
                logger.info("📡 收到控制指令: %s", data)
                await websocket.send_json(
                    {
                        "type": "control_ack",
                        "payload": {
                            "status": "received",
                            "command": data.get("payload", {}),
                        },
                    }
                )
            elif msg_type == "device_control":
                payload = data.get("payload", {})
                device_id = payload.get("device_id")
                command = payload.get("command")

                if not device_id or not command:
                    await websocket.send_json(
                        {
                            "type": "control_error",
                            "payload": {"error": "缺少 device_id 或 command"},
                        }
                    )
                    continue

                async with get_db_session() as db:
                    result = await db.execute(
                        select(DeviceModel).where(
                            DeviceModel.id == uuid.UUID(device_id)
                        )
                    )
                    device = result.scalar_one_or_none()

                if not device:
                    await websocket.send_json(
                        {
                            "type": "control_error",
                            "payload": {"error": "设备不存在"},
                        }
                    )
                    continue

                if command == "restart":
                    await websocket.send_json(
                        {
                            "type": "control_ack",
                            "payload": {
                                "device_id": device_id,
                                "command": "restart",
                                "status": "executed",
                            },
                        }
                    )
                elif command == "status_query":
                    await websocket.send_json(
                        {
                            "type": "device_status",
                            "payload": {
                                "device_id": device_id,
                                "status": device.status,
                                "battery_level": device.battery_level,
                                "signal_strength": device.signal_strength,
                                "last_heartbeat": (
                                    device.last_heartbeat.isoformat()
                                    if device.last_heartbeat
                                    else None
                                ),
                            },
                        }
                    )
                else:
                    await websocket.send_json(
                        {
                            "type": "control_ack",
                            "payload": {
                                "device_id": device_id,
                                "command": command,
                                "status": "unknown_command",
                            },
                        }
                    )

            elif msg_type == "device_status_query":
                device_id = data.get("device_id")
                if not device_id:
                    await websocket.send_json(
                        {
                            "type": "control_error",
                            "payload": {"error": "缺少 device_id"},
                        }
                    )
                    continue

                async with get_db_session() as db:
                    result = await db.execute(
                        select(DeviceModel).where(
                            DeviceModel.id == uuid.UUID(device_id)
                        )
                    )
                    device = result.scalar_one_or_none()

                if device:
                    await websocket.send_json(
                        {
                            "type": "device_status",
                            "payload": {
                                "device_id": device_id,
                                "status": device.status,
                                "battery_level": device.battery_level,
                                "signal_strength": device.signal_strength,
                            },
                        }
                    )
                else:
                    await websocket.send_json(
                        {
                            "type": "control_error",
                            "payload": {"error": "设备不存在"},
                        }
                    )

            else:
                await websocket.send_json(
                    {
                        "type": "error",
                        "payload": {"message": f"未知消息类型: {msg_type}"},
                    }
                )

    except WebSocketDisconnect:
        logger.info("🔌 WebSocket 客户端断开: %s", client_id)
    except Exception as exc:
        logger.error("WebSocket 异常: %s", exc)


# ============================================================
# 传感器数据 (InfluxDB 查询)
# ============================================================


@router.get("/sensors/latest")
async def get_latest_sensor_data():
    from app.services.influxdb_service import influxdb_service

    if influxdb_service.is_connected:
        try:
            readings = influxdb_service.get_latest_readings()
            if readings:
                return {
                    "readings": readings,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "influxdb",
                }
        except Exception:
            pass

    from app.services.mock_data_generator import PRESET_DEVICES, MockDataGenerator

    readings = []
    now = datetime.now(timezone.utc)
    for device in PRESET_DEVICES:
        reading = MockDataGenerator._generate_sensor_reading(
            mock_generator, device, now
        )
        readings.append(reading)
    return {"readings": readings, "timestamp": now.isoformat(), "source": "mock"}


@router.get("/sensors/history/{device_id}")
async def get_sensor_history(
    device_id: str,
    hours: int = Query(default=24, ge=1, le=168),
    interval_minutes: int = Query(default=5, ge=1, le=60),
):
    from app.services.influxdb_service import influxdb_service

    if influxdb_service.is_connected:
        try:
            data = influxdb_service.get_historical_readings(
                device_id, hours, interval_minutes
            )
            if data:
                return {"device_id": device_id, "data": data, "source": "influxdb"}
        except Exception:
            pass

    import random

    now = datetime.now(timezone.utc)
    data = []
    for i in range(hours * 60 // interval_minutes):
        ts = now - timedelta(minutes=i * interval_minutes)
        data.append(
            {
                "time": ts.isoformat(),
                "water_level_mm": round(80 + random.random() * 60, 1),
                "flow_rate_m3h": round(2 + random.random() * 5, 1),
            }
        )
    data.reverse()
    return {"device_id": device_id, "data": data, "source": "mock"}


@router.get("/sensors/export")
async def export_sensor_data(
    device_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    format: str = "csv",
    current_user: dict = Depends(require_role(RoleEnum.admin, RoleEnum.operator)),
):
    """导出传感器历史数据"""
    try:
        from app.services.influxdb_service import influxdb_service

        tables = influxdb_service.query_sensor_export(
            device_id=device_id,
            start_time=start_time,
            end_time=end_time,
        )
        rows: list[dict[str, Any]] = []
        for table in tables or []:
            for record in getattr(table, "records", []):
                values = record.values
                rows.append(
                    {
                        "_time": (
                            record.get_time().isoformat() if record.get_time() else ""
                        ),
                        "device_id": values.get("device_id", ""),
                        "_field": values.get("_field", ""),
                        "_value": values.get("_value", ""),
                    }
                )
    except Exception:
        rows = []

    output = io.StringIO()
    if format == "csv":
        writer = csv.writer(output)
        writer.writerow(["timestamp", "device_id", "field", "value"])
        for row in rows:
            writer.writerow(
                [
                    row.get("_time", ""),
                    row.get("device_id", ""),
                    row.get("_field", ""),
                    row.get("_value", ""),
                ]
            )
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=sensor_data_"
                    f"{datetime.now().strftime('%Y%m%d')}.csv"
                ),
            },
        )

    return {"error": "Unsupported format"}


# ============================================================
# 告警管理
# ============================================================


@router.get("/alerts")
async def get_alerts(
    limit: int = Query(default=50, ge=1, le=200),
    level: Optional[str] = Query(default=None, pattern="^(critical|warning|info)$"),
):
    try:
        async with get_db_session() as db:
            stmt = (
                select(AlertModel).order_by(AlertModel.created_at.desc()).limit(limit)
            )
            if level:
                stmt = stmt.where(AlertModel.level == level)
            result = await db.execute(stmt)
            db_alerts = result.scalars().all()

            alerts = []
            for a in db_alerts:
                device_name = None
                if a.device_id:
                    dev_result = await db.execute(
                        select(DeviceModel).where(DeviceModel.id == a.device_id)
                    )
                    dev = dev_result.scalar_one_or_none()
                    device_name = dev.name if dev else None

                alerts.append(
                    {
                        "id": str(a.id),
                        "alert_type": a.alert_type,
                        "level": a.level,
                        "title": a.title,
                        "description": a.description,
                        "device_id": str(a.device_id) if a.device_id else None,
                        "device_name": device_name,
                        "latitude": None,
                        "longitude": None,
                        "snapshot_url": a.snapshot_url,
                        "is_acknowledged": a.is_acknowledged,
                        "is_resolved": a.is_resolved,
                        "created_at": (
                            a.created_at.isoformat() if a.created_at else None
                        ),
                    }
                )
            return {"alerts": alerts, "total": len(alerts)}
    except Exception:
        __import__("logging").getLogger(__name__).exception("查询告警列表失败")

    import random

    from app.services.mock_data_generator import ALERT_TITLES, PRESET_DEVICES

    mock_alerts = []
    now = datetime.now(timezone.utc)
    for i in range(min(limit, 5)):
        alert_type, level, title = random.choice(ALERT_TITLES)
        device = random.choice(PRESET_DEVICES)
        ts = now - timedelta(minutes=random.randint(1, 360))
        mock_alerts.append(
            {
                "id": str(uuid.uuid4()),
                "alert_type": alert_type,
                "level": level,
                "title": title,
                "description": f"{device['name']} - {title}",
                "device_id": device["id"],
                "device_name": device["name"],
                "latitude": device["lat"],
                "longitude": device["lng"],
                "snapshot_url": None,
                "is_acknowledged": random.random() > 0.6,
                "is_resolved": random.random() > 0.8,
                "created_at": ts.isoformat(),
            }
        )
    return {"alerts": mock_alerts, "total": len(mock_alerts), "source": "mock"}


@router.get("/alerts/export")
async def export_alert_data(
    level: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    format: str = "csv",
    current_user: dict = Depends(require_role(RoleEnum.admin, RoleEnum.operator)),
):
    """导出告警记录"""
    from app.models.db_models import Alert

    alerts: Sequence[Alert] = ()
    try:
        async with get_db_session() as db:
            query = select(Alert).order_by(Alert.created_at.desc())
            if level:
                query = query.where(Alert.level == level)
            result = await db.execute(query.limit(10000))
            alerts = result.scalars().all()
    except Exception:
        pass

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "device_id",
            "alert_type",
            "level",
            "title",
            "description",
            "is_acknowledged",
            "is_resolved",
            "created_at",
        ]
    )
    for alert in alerts:
        writer.writerow(
            [
                str(alert.id),
                str(alert.device_id),
                alert.alert_type,
                alert.level,
                alert.title,
                alert.description or "",
                alert.is_acknowledged,
                alert.is_resolved,
                str(alert.created_at) if alert.created_at else "",
            ]
        )
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f"attachment; filename=alerts_"
                f"{datetime.now().strftime('%Y%m%d')}.csv"
            ),
        },
    )


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str,
    action: AlertAcknowledge,
    user: dict = Depends(require_role(RoleEnum.admin, RoleEnum.operator)),
):
    try:
        async with get_db_session() as db:
            result = await db.execute(
                select(AlertModel).where(AlertModel.id == alert_id)
            )
            alert = result.scalar_one_or_none()
            if alert:
                alert.is_acknowledged = True
                alert.acknowledged_by = user.get("sub")
                alert.acknowledged_at = datetime.now(timezone.utc)
                if action.action == "resolve":
                    alert.is_resolved = True
                    alert.resolved_at = datetime.now(timezone.utc)
    except Exception:
        raise HTTPException(status_code=500, detail="告警确认操作失败")

    return {
        "alert_id": alert_id,
        "action": action.action,
        "status": "ok",
        "acknowledged_by": user.get("username", "unknown"),
    }


# ============================================================
# 设备管理 (数据库 + 模拟数据回退)
# ============================================================


async def _get_db_devices() -> list[dict[str, Any]]:
    try:
        async with get_db_session() as db:
            result = await db.execute(
                select(DeviceModel).order_by(DeviceModel.created_at)
            )
            devices = result.scalars().all()
            if devices:
                return [
                    {
                        "id": str(d.id),
                        "code": d.device_code,
                        "name": d.name,
                        "lat": d.latitude,
                        "lng": d.longitude,
                        "district": d.district,
                        "device_type": d.device_type,
                        "status": d.status,
                        "battery_level": d.battery_level,
                        "signal_strength": d.signal_strength,
                        "last_heartbeat": (
                            d.last_heartbeat.isoformat() if d.last_heartbeat else None
                        ),
                    }
                    for d in devices
                ]
    except Exception:
        pass

    from app.services.mock_data_generator import PRESET_DEVICES

    return [
        {
            "id": d["id"],
            "code": d["code"],
            "name": d["name"],
            "lat": d["lat"],
            "lng": d["lng"],
            "district": d["district"],
            "device_type": d.get("device_type", "manhole_cover"),
            "status": d.get("status", "online"),
            "battery_level": d.get("battery_level", 95),
            "signal_strength": d.get("signal_strength", 90),
            "last_heartbeat": None,
        }
        for d in PRESET_DEVICES
    ]


@router.get("/devices")
async def get_devices():
    """获取所有设备列表 (DB 优先，回退到模拟数据)"""
    devices = await _get_db_devices()
    return {"devices": devices, "total": len(devices)}


@router.post("/devices")
async def create_device(
    device: dict[str, Any],
    user: dict = Depends(require_role(RoleEnum.admin, RoleEnum.operator)),
):
    """创建设备"""
    try:
        async with get_db_session() as db:
            new_device = DeviceModel(
                device_code=device.get("code", ""),
                name=device.get("name", ""),
                device_type=device.get("device_type", "manhole_cover"),
                latitude=device.get("lat", 0),
                longitude=device.get("lng", 0),
                district=device.get("district"),
                address=device.get("address"),
            )
            db.add(new_device)
            await db.commit()
            await db.refresh(new_device)
            return {
                "device": {
                    "id": str(new_device.id),
                    "code": new_device.device_code,
                    "name": new_device.name,
                }
            }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"创建失败: {exc}")


@router.put("/devices/{device_id}")
async def update_device(
    device_id: str,
    update: dict[str, Any],
    user: dict = Depends(require_role(RoleEnum.admin, RoleEnum.operator)),
):
    """更新设备"""
    try:
        async with get_db_session() as db:
            result = await db.execute(
                select(DeviceModel).where(DeviceModel.id == device_id)
            )
            device = result.scalar_one_or_none()
            if not device:
                raise HTTPException(status_code=404, detail="设备不存在")

            for key, value in update.items():
                if hasattr(device, key) and value is not None:
                    setattr(device, key, value)
            return {"status": "ok", "device_id": device_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"更新失败: {exc}")


@router.delete("/devices/{device_id}")
async def delete_device(
    device_id: str,
    user: dict = Depends(require_role(RoleEnum.admin)),
):
    """删除设备"""
    try:
        async with get_db_session() as db:
            result = await db.execute(
                select(DeviceModel).where(DeviceModel.id == device_id)
            )
            device = result.scalar_one_or_none()
            if not device:
                raise HTTPException(status_code=404, detail="设备不存在")
            await db.delete(device)
            return {"status": "deleted", "device_id": device_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"删除失败: {exc}")


# ============================================================
# 模拟数据控制
# ============================================================


@router.get("/mock/status")
async def get_mock_status():
    """获取模拟数据生成器状态"""
    return {"running": mock_generator.is_running}


@router.get("/mock/config")
async def get_mock_config():
    """获取模拟数据生成器配置"""
    return mock_generator.get_config()


@router.put("/mock/config")
async def update_mock_config(
    config: dict[str, Any],
    user: dict = Depends(require_role(RoleEnum.admin, RoleEnum.operator)),
):
    """更新模拟数据生成器配置 (可运行时调节)"""
    if "alert_interval_seconds" in config:
        mock_generator.alert_interval_seconds = float(config["alert_interval_seconds"])
    if "alert_probability" in config:
        mock_generator.alert_probability = float(config["alert_probability"])
    if "alert_count_per_batch" in config:
        mock_generator.alert_count_per_batch = int(config["alert_count_per_batch"])
    if "sensor_interval" in config:
        mock_generator.sensor_interval = float(config["sensor_interval"])
    return mock_generator.get_config()


@router.post("/mock/start")
async def start_mock_data(
    user: dict = Depends(require_role(RoleEnum.admin, RoleEnum.operator)),
):
    """启动模拟数据生成 (仅 operator/admin)"""
    await mock_generator.start()
    return {"status": "started", "message": "模拟数据生成已启动"}


@router.post("/mock/stop")
async def stop_mock_data(
    user: dict = Depends(require_role(RoleEnum.admin, RoleEnum.operator)),
):
    """停止模拟数据生成 (仅 operator/admin)"""
    await mock_generator.stop()
    return {"status": "stopped", "message": "模拟数据生成已停止"}


# Hydraulic routes are included after the legacy routes so existing public
# paths keep their precedence while the new /api surface remains one OpenAPI
# document.
router.include_router(hydraulic_router)
