# 🏙️ 智慧城市神经末梢 — Code Wiki

> 市政排水智能监测与AI安防系统 · 完整代码文档

---

## 目录

1. [项目概述](#1-项目概述)
2. [整体架构](#2-整体架构)
3. [技术栈与依赖](#3-技术栈与依赖)
4. [后端模块详解](#4-后端模块详解)
5. [前端模块详解](#5-前端模块详解)
6. [数据库设计](#6-数据库设计)
7. [API 接口文档](#7-api-接口文档)
8. [实时通信机制](#8-实时通信机制)
9. [AI 推理管道](#9-ai-推理管道)
10. [部署与运行](#10-部署与运行)
11. [配置参考](#11-配置参考)

---

## 1. 项目概述

本系统是一个面向市政排水场景的**数字孪生智能监测平台**，核心能力包括：

| 能力 | 说明 |
|------|------|
| 🔭 实时监测 | 液位、流量、水质等传感器数据毫秒级推送（SSE） |
| 🤖 AI 视觉 | 基于 YOLOv8/v10 的积水识别、井盖异常检测、安防入侵告警 |
| 🗺️ GIS 数字孪生 | 基于 Leaflet 的城市排水管网可视化（深色瓦片 + 脉冲标记） |
| 📹 多源视频 | RTSP → HLS 转码、本地摄像头、HLS.js 播放 |
| 🔄 零停机更新 | AI 模型热切换，后台预加载 → 验证 → 原子替换 → 旧模型卸载 |

系统采用**前后端分离**架构，后端 FastAPI 提供 REST API + SSE + WebSocket，前端 React 18 + TypeScript 构建科幻风格驾驶舱大屏。

---

## 2. 整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                     Frontend (React 18 + TypeScript)              │
│  Vite · Tailwind CSS · Zustand · Ant Design · ECharts · Leaflet  │
│  Framer Motion · HLS.js · Axios                                   │
├──────────────────────────────────────────────────────────────────┤
│                    Nginx (反向代理 + 静态资源 + HLS 缓存)           │
│  /api/* → backend:8000  |  /hls/* → 静态文件  |  SSE/WebSocket   │
├──────────────────────────────────────────────────────────────────┤
│                  FastAPI Backend (Python 3.11+)                    │
│  ┌──────────────┐  ┌────────────────┐  ┌──────────────────────┐  │
│  │ API Service   │  │ Inference      │  │ Stream Service       │  │
│  │ (CRUD/Auth)   │  │ Worker         │  │ (FFmpeg/RTSP→HLS)   │  │
│  └──────────────┘  └────────────────┘  └──────────────────────┘  │
│  ┌──────────────┐  ┌────────────────┐  ┌──────────────────────┐  │
│  │ ModelManager  │  │ SSE Manager    │  │ Mock Data Generator  │  │
│  │ (热切换单例)   │  │ (广播推送)     │  │ (演示模式)           │  │
│  └──────────────┘  └────────────────┘  └──────────────────────┘  │
├──────────────────────────────────────────────────────────────────┤
│  PostgreSQL/TimescaleDB  │  InfluxDB  │  Redis  │  Model Weights │
│  (关系数据 + 时序超表)     │  (时序数据) │  (缓存) │  (YOLO .pt)    │
└──────────────────────────────────────────────────────────────────┘
```

### 数据流架构

```
传感器/IoT设备 ──→ 后端API ──→ PostgreSQL (持久化)
                      │
                      ├──→ Redis Pub/Sub ──→ SSE Manager ──→ 前端 (实时推送)
                      │
视频流(RTSP) ──→ FFmpeg ──→ HLS分片 ──→ Nginx静态服务 ──→ 前端HLS.js
                      │
                      └──→ InferenceWorker ──→ YOLO推理 ──→ 告警 ──→ SSE广播
```

---

## 3. 技术栈与依赖

### 后端核心依赖

| 分类 | 包名 | 版本 | 用途 |
|------|------|------|------|
| Web 框架 | `fastapi` | 0.109.2 | 异步 HTTP 框架 |
| ASGI 服务器 | `uvicorn[standard]` | 0.27.1 | 高性能异步服务器 |
| SSE | `sse-starlette` | 1.8.2 | Server-Sent Events 支持 |
| ORM | `sqlalchemy[asyncio]` | 2.0.27 | 异步数据库 ORM |
| 数据库驱动 | `asyncpg` | 0.29.0 | PostgreSQL 异步驱动 |
| 数据库迁移 | `alembic` | 1.13.1 | 数据库 Schema 版本管理 |
| 时序数据库 | `influxdb-client` | 1.41.0 | InfluxDB 传感器数据写入/查询 |
| 缓存 | `redis` | 5.0.1 | 异步 Redis 客户端 |
| 认证 | `python-jose[cryptography]` | 3.3.0 | JWT Token 编解码 |
| 密码哈希 | `passlib[bcrypt]` | 1.7.4 | bcrypt 密码哈希 |
| AI/CV | `ultralytics` | 8.1.0 | YOLOv8/v10 推理 |
| 深度学习 | `torch` / `torchvision` | ≥2.1.0 | PyTorch 推理引擎 |
| 图像处理 | `opencv-python-headless` | 4.9.0.80 | 视频帧处理、标注绘制 |
| 视频转码 | `ffmpeg-python` | 0.2.0 | FFmpeg Python 绑定 |
| 视频解码 | `av` | 11.0.0 | PyAV 视频解码 |
| 配置管理 | `pydantic-settings` | 2.1.0 | 环境变量 + .env 配置 |
| 监控 | `prometheus-fastapi-instrumentator` | 6.1.0 | Prometheus 指标暴露 |

### 前端核心依赖

| 分类 | 包名 | 版本 | 用途 |
|------|------|------|------|
| UI 框架 | `react` / `react-dom` | 18.3.1 | 声明式 UI |
| 路由 | `react-router-dom` | 6.22.1 | SPA 路由管理 |
| UI 组件库 | `antd` | 5.14.0 | 企业级组件库（暗黑主题） |
| 图标 | `@ant-design/icons` | 5.3.0 | Ant Design 图标集 |
| 状态管理 | `zustand` | 4.5.1 | 轻量级全局状态 |
| 图表 | `echarts` | 5.5.0 | 数据可视化 |
| 地图 | `leaflet` / `react-leaflet` | 1.9.4 / 4.2.1 | GIS 地图渲染 |
| 视频播放 | `hls.js` | 1.5.8 | HLS 流播放 |
| 通用播放 | `react-player` | 2.14.1 | 多源视频播放 |
| 动画 | `framer-motion` | 11.0.3 | 声明式动画 |
| HTTP 客户端 | `axios` | 1.6.7 | API 请求 |
| CSS 框架 | `tailwindcss` | 3.4.1 | 原子化 CSS |
| 构建工具 | `vite` | 5.1.4 | 前端构建与开发服务器 |

---

## 4. 后端模块详解

### 4.1 项目结构

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI 应用入口、生命周期管理
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes.py            # 全部 REST API 路由定义
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py            # pydantic-settings 全局配置
│   │   ├── database.py          # 异步 SQLAlchemy 引擎与会话
│   │   ├── model_manager.py     # AI 模型管理器（热切换核心）
│   │   ├── redis_client.py      # Redis 异步客户端（Pub/Sub + 缓存）
│   │   └── security.py          # JWT 认证 + bcrypt 密码哈希
│   ├── models/
│   │   ├── __init__.py
│   │   └── db_models.py         # SQLAlchemy ORM 模型定义
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── schemas.py           # Pydantic 请求/响应模式
│   ├── services/
│   │   ├── __init__.py
│   │   ├── inference_service.py # YOLO 推理服务
│   │   ├── mock_data_generator.py # 模拟数据生成器
│   │   ├── sse_manager.py       # SSE 连接管理器
│   │   └── stream_service.py    # 视频流转码服务
│   ├── utils/
│   │   └── __init__.py
│   └── workers/
│       ├── __init__.py
│       └── inference_worker.py  # 推理 Worker + Worker 池
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial.py      # 初始数据库迁移
├── alembic.ini
├── media/
│   ├── hls/                     # HLS 视频分片输出
│   └── screenshots/             # 推理快照输出
└── requirements.txt
```

### 4.2 应用入口 — `app/main.py`

**职责**：FastAPI 应用创建、生命周期管理、中间件注册、路由挂载。

**关键流程**：

| 阶段 | 操作 |
|------|------|
| 启动 | 初始化 ModelManager → 连接数据库 → 连接 Redis → 启动流健康监控 → 启动模拟数据生成器 |
| 运行 | 处理 HTTP/SSE/WS 请求 |
| 关闭 | 停止模拟数据 → 停止视频转码 → 断开 Redis → 关闭数据库连接池 |

**关键函数**：

- `lifespan(app)` — 异步上下文管理器，管理应用生命周期
- `add_process_time_header(request, call_next)` — 请求计时中间件
- `validation_exception_handler(request, exc)` — 请求参数验证异常处理
- `global_exception_handler(request, exc)` — 全局异常兜底

### 4.3 核心配置 — `app/core/config.py`

**类**：`Settings(BaseSettings)`

使用 `pydantic-settings` 从环境变量和 `.env` 文件加载配置，提供类型安全和验证。

| 配置分组 | 关键字段 | 默认值 |
|----------|----------|--------|
| 应用基础 | `APP_NAME`, `APP_VERSION`, `DEBUG`, `LOG_LEVEL` | Smart City Neural Endpoints / 1.0.0 |
| 数据库 | `DATABASE_URL`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | PostgreSQL asyncpg / 20 / 10 |
| InfluxDB | `INFLUXDB_URL`, `INFLUXDB_TOKEN`, `INFLUXDB_ORG`, `INFLUXDB_BUCKET` | localhost:8086 |
| Redis | `REDIS_URL`, `REDIS_CHANNEL_ALERTS/SENSOR/MODEL` | redis://localhost:6379 |
| JWT | `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES` | HS256 / 1440min |
| YOLO 模型 | `MODEL_STORAGE_PATH`, `DEFAULT_MODEL_VERSION`, `MODEL_INFERENCE_DEVICE`, `MODEL_CONFIDENCE_THRESHOLD` | ./models/weights / cuda:0 / 0.45 |
| 视频流 | `FFMPEG_PATH`, `HLS_OUTPUT_DIR`, `HLS_SEGMENT_TIME`, `HLS_LIST_SIZE` | ffmpeg / 4s / 6 |
| CORS | `CORS_ORIGINS` | JSON 数组解析 |

**特殊逻辑**：`CORS_ORIGINS` 字段使用 `@field_validator` 支持从 JSON 字符串或逗号分隔字符串解析为列表。

**全局单例**：`settings = Settings()`

### 4.4 数据库层 — `app/core/database.py`

**架构**：SQLAlchemy 2.0 异步风格，全局懒初始化单例引擎。

| 函数 | 说明 |
|------|------|
| `get_engine()` | 获取/创建全局异步引擎（单例，懒初始化） |
| `get_session_factory()` | 获取/创建异步会话工厂 |
| `get_db()` | FastAPI 依赖注入：为每个请求提供数据库会话，请求结束自动提交/回滚 |
| `get_db_session()` | 非依赖注入上下文的会话管理器（手动查询用） |
| `create_all_tables()` | 创建所有声明的表（开发用，生产应使用 Alembic） |
| `check_database_connection()` | 测试数据库连接 |
| `dispose_engine()` | 关闭引擎，释放所有连接 |

**引擎配置**：`pool_pre_ping=True`（连接健康检查）、`pool_recycle=3600`（1小时回收）、PostgreSQL 应用名标识。

### 4.5 模型管理器 — `app/core/model_manager.py`

**核心类**：`ModelManager`（线程安全单例）

这是系统最核心的模块之一，实现了**工业级 AI 模型热切换**。

#### 设计模式

```
双重检查锁定单例 → 后台线程预加载 → 验证 → 原子替换 → 旧模型卸载
```

#### 关键数据结构

| 类型 | 说明 |
|------|------|
| `ModelStatus` | 枚举：`LOADING` / `ACTIVE` / `UNLOADING` / `ERROR` / `STANDBY` |
| `ModelMetadata` | 数据类：版本、路径、文件大小、SHA256、状态、推理统计 |

#### 核心方法

| 方法 | 说明 |
|------|------|
| `get_instance()` | 双重检查锁定获取单例 |
| `scan_repository()` | 扫描 `models/weights/` 目录，发现 `.pt/.onnx/.engine` 文件 |
| `load_model(version)` | 加载指定版本到内存（不激活，用于预加载） |
| `load_and_activate(version)` | 加载并直接激活（启动时使用） |
| `switch_model(target_version, verify)` | **原子热切换**：预加载 → 验证 → 加锁替换 → 卸载旧模型 |
| `switch_model_async(target_version)` | 后台线程异步执行热切换，立即返回 |
| `predict(image, **kwargs)` | 线程安全推理，使用 `_inference_lock` 互斥 |
| `on_switch(callback)` | 注册切换回调 |
| `get_status()` | 获取模型管理器完整状态 |

#### 热切换流程（5 阶段）

```
Phase 1: 预加载目标模型（后台线程，不阻塞推理）
    ↓
Phase 2: 验证模型（可选，用空白图像跑一次推理确认可用）
    ↓
Phase 3: 原子替换（加 _switch_lock 写锁，更新全局指针）
    ↓
Phase 4: 卸载旧模型（锁外执行，gc.collect + torch.cuda.empty_cache）
    ↓
Phase 5: 触发切换回调
```

#### 便捷函数

- `get_model_manager()` → 返回 `ModelManager.get_instance()`

### 4.6 安全认证 — `app/core/security.py`

| 函数 | 说明 |
|------|------|
| `verify_password(plain, hashed)` | bcrypt 密码验证 |
| `get_password_hash(password)` | 生成 bcrypt 哈希 |
| `create_access_token(subject, expires_delta, extra_claims)` | 创建 JWT Token |
| `decode_access_token(token)` | 解码验证 JWT，失败返回 `None` |

**JWT Payload 结构**：`exp`（过期时间）、`sub`（用户ID）、`iat`（签发时间）、`type`（"access"）、自定义 claims（`username`, `role`）。

### 4.7 Redis 客户端 — `app/core/redis_client.py`

**类**：`RedisClient`（单例）

| 方法 | 说明 |
|------|------|
| `connect()` | 建立 Redis 异步连接 |
| `disconnect()` | 断开连接，取消监听任务 |
| `publish(channel, message)` | 发布消息到频道 |
| `publish_alert(alert)` | 发布到 `scn:alerts` 频道 |
| `publish_sensor_data(readings)` | 发布到 `scn:sensor_data` 频道 |
| `publish_model_status(status)` | 发布到 `scn:model_status` 频道 |
| `on(channel, handler)` | 注册消息处理器 |
| `start_listener()` | 启动消息监听协程 |
| `get/set/delete` | 基础缓存操作 |

**频道定义**：

| 频道 | 用途 |
|------|------|
| `scn:alerts` | 告警事件广播 |
| `scn:sensor_data` | 传感器实时数据 |
| `scn:model_status` | 模型状态变更 |

**全局单例**：`redis_client = RedisClient.get_instance()`

### 4.8 推理服务 — `app/services/inference_service.py`

**类**：`InferenceService`（无状态单例）

封装 ModelManager 的推理能力，提供多种图像输入方式：

| 方法 | 说明 |
|------|------|
| `infer_from_base64(image_b64, ...)` | 从 Base64 编码图像推理 |
| `infer_from_url(image_url, ...)` | 从 URL 拉取图像推理 |
| `infer_from_frame(frame, ...)` | 从 numpy 帧数据推理（视频流管道用） |

**自定义类别映射**（排水系统专用）：

| 类别 ID | 类别名 | 说明 |
|---------|--------|------|
| 80 | `water_accumulation` | 积水 |
| 81 | `missing_manhole` | 井盖缺失 |
| 82 | `damaged_manhole` | 井盖破损 |
| 83 | `shifted_manhole` | 井盖移位 |
| 84 | `intruder` | 非法闯入 |
| 85 | `illegal_parking` | 违停车辆 |

**返回结构**：`detections`（检测列表）、`inference_time_ms`、`model_version`、`image_width/height`、可选 `annotated_image_base64`。

### 4.9 视频流服务 — `app/services/stream_service.py`

**类**：`StreamService`

管理 FFmpeg 子进程，将 RTSP 流转码为 HLS。

| 方法 | 说明 |
|------|------|
| `start_stream(camera_id, rtsp_url)` | 启动 RTSP → HLS 转码 |
| `stop_stream(camera_id)` | 停止转码进程 |
| `stop_all()` | 停止所有转码 |
| `start_health_monitor()` | 启动后台健康检查协程 |

**数据类**：`StreamProcess` — 封装转码进程状态（camera_id、source_url、ffmpeg_process、restart_count、is_healthy 等）。

**FFmpeg 命令参数**：

```
ffmpeg -rtsp_transport tcp -stimeout 10000000 -i <rtsp_url>
  -c:v libx264 -preset veryfast -crf 28 -r 25 -g 50
  -hls_time 4 -hls_list_size 6 -hls_flags delete_segments+append_list
  -f hls <output>/index.m3u8
```

**健康监控**：每 10 秒检查进程存活 + HLS 文件更新时间，异常时自动重启（指数退避，最大 60 秒延迟）。

**全局单例**：`stream_service = StreamService()`

### 4.10 SSE 管理器 — `app/services/sse_manager.py`

**类**：`SSEManager`（单例）

管理所有 SSE 客户端连接，使用 `asyncio.Queue` 实现异步消息分发。

| 方法 | 说明 |
|------|------|
| `connect()` | 注册新 SSE 客户端，返回 `(client_id, queue)` |
| `disconnect(client_id)` | 注销客户端 |
| `broadcast(event_type, data)` | 向所有客户端广播事件 |
| `broadcast_sensor(readings)` | 广播传感器数据 |
| `broadcast_alert(alert_data)` | 广播告警 |

**事件类型**：`sensors`（传感器数据）、`alerts`（告警推送）、`system`（系统状态）。

**队列策略**：每个客户端 `maxsize=256`，队列满时断开并清理。

### 4.11 模拟数据生成器 — `app/services/mock_data_generator.py`

**类**：`MockDataGenerator`

在无硬件环境下生成逼真的传感器数据和告警事件，用于演示。

| 属性 | 默认值 | 说明 |
|------|--------|------|
| `sensor_interval` | 4.0s | 传感器数据广播间隔 |
| `alert_interval_seconds` | 60s | 告警生成间隔 |
| `alert_probability` | 0.6 | 每次触发时生成告警的概率 |
| `alert_count_per_batch` | 1 | 每次生成的告警数量 |

**预设设备**：8 个上海地区的模拟井盖设备（黄浦区、浦东新区、徐汇区等）。

**传感器读数字段**：`water_level_mm`（液位）、`flow_rate_m3h`（流量）、`water_quality_ph`（pH值）、`temperature_c`（温度）、`humidity_pct`（湿度）、`battery_level`（电量）、`signal_strength`（信号强度）。

**全局单例**：`mock_generator = MockDataGenerator(sensor_interval=4.0)`

### 4.12 推理 Worker — `app/workers/inference_worker.py`

**类**：`InferenceWorker`

对单个视频流执行持续 AI 推理，检测异常时通过 SSE 广播告警。

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `inference_interval` | 2.0s | 推理间隔 |
| `snapshot_interval` | 30.0s | 快照保存间隔 |
| `confidence_threshold` | 0.45 | 检测置信度阈值 |

**告警触发规则**：

| 检测类别 | 置信度阈值 | 告警级别 |
|----------|-----------|---------|
| `water_accumulation` | 0.55 | critical |
| `missing_manhole` | 0.55 | critical |
| `damaged_manhole` | 0.50 | warning |
| `shifted_manhole` | 0.50 | warning |
| `intruder` | 0.60 | critical |
| `illegal_parking` | 0.55 | warning |

**类**：`WorkerPool` — 管理多个摄像头的推理任务，提供 `add_worker`、`remove_worker`、`stop_all`、`get_status` 操作。

**全局单例**：`worker_pool = WorkerPool()`

### 4.13 Pydantic 模式 — `app/schemas/schemas.py`

定义所有 API 请求/响应的数据验证与序列化模式：

| 模式 | 用途 |
|------|------|
| `TokenRequest` / `TokenResponse` / `UserBrief` | 认证相关 |
| `DeviceBase` / `DeviceCreate` / `DeviceUpdate` / `DeviceResponse` | 设备 CRUD |
| `AlertResponse` / `AlertAcknowledge` | 告警管理 |
| `SensorReading` / `SensorReadingBatch` | 传感器数据 |
| `ModelSwitchRequest` / `ModelStatusResponse` / `ModelUploadResponse` | 模型管理 |
| `InferenceRequest` / `DetectionResult` / `InferenceResponse` | AI 推理 |
| `StreamCreate` / `StreamResponse` | 视频流 |
| `WSMessage` / `SSESensorEvent` / `SSEAlertEvent` | 实时通信 |
| `PaginationParams` / `PaginatedResponse` | 通用分页 |

### 4.14 ORM 模型 — `app/models/db_models.py`

详见 [第6节 数据库设计](#6-数据库设计)。

---

## 5. 前端模块详解

### 5.1 项目结构

```
frontend/
├── src/
│   ├── main.tsx                     # 应用入口（Ant Design 暗黑主题配置）
│   ├── App.tsx                      # 根组件（路由 + 守卫）
│   ├── components/
│   │   ├── alerts/
│   │   │   └── AlertPanel.tsx       # 告警滚动列表（分级显示 + 确认操作）
│   │   ├── charts/
│   │   │   ├── FlowTrendChart.tsx   # 流量趋势柱状图（ECharts）
│   │   │   └── WaterLevelChart.tsx  # 液位实时折线图（ECharts）
│   │   ├── common/
│   │   │   ├── EmptyState.tsx
│   │   │   ├── ErrorBoundary.tsx    # 错误边界
│   │   │   ├── Loading.tsx
│   │   │   └── index.ts
│   │   ├── device/
│   │   │   ├── DeviceDetailPanel.tsx # 设备详情抽屉
│   │   │   └── DeviceSearchFilter.tsx # 设备搜索过滤
│   │   ├── layout/
│   │   │   └── AppLayout.tsx        # 驾驶舱整体布局
│   │   ├── map/
│   │   │   └── MapVisualization.tsx # GIS 地图可视化
│   │   └── video/
│   │       └── VideoPlayer.tsx      # 多源视频播放器
│   ├── hooks/
│   │   ├── useAlertNotifications.tsx # 告警通知弹窗
│   │   ├── useKeyboardShortcuts.ts  # 全局键盘快捷键
│   │   ├── useSSE.ts               # SSE 客户端 Hook
│   │   └── useWebSocket.ts         # WebSocket 控制通道 Hook
│   ├── pages/
│   │   ├── AlertCenter.tsx         # 告警中心页面
│   │   ├── Dashboard.tsx           # 驾驶舱大屏主页
│   │   ├── LandingPage.tsx         # 着陆页
│   │   ├── LoginPage.tsx           # 登录页
│   │   ├── MapView.tsx             # GIS 地图全屏页
│   │   ├── SettingsPage.tsx        # 系统设置页
│   │   └── VideoMonitor.tsx        # 视频监控页
│   ├── services/
│   │   └── api.ts                  # API 服务层（axios 封装）
│   ├── store/
│   │   └── useAppStore.ts          # Zustand 全局状态
│   ├── styles/
│   │   └── globals.css             # 全局样式
│   └── types/
│       └── index.ts                # TypeScript 类型定义
├── index.html
├── package.json
├── vite.config.ts
├── tailwind.config.js
├── postcss.config.js
├── tsconfig.json
└── tsconfig.node.json
```

### 5.2 应用入口 — `main.tsx`

- 使用 `ConfigProvider` 配置 Ant Design 暗黑主题
- 主题色：`#00D4FF`（霓虹蓝）、背景：`#0A0E17`（深空黑）
- 中文本地化：`zhCN`

### 5.3 路由结构 — `App.tsx`

| 路径 | 组件 | 认证 | 说明 |
|------|------|------|------|
| `/` | `LandingPage` | 否 | 着陆页 |
| `/login` | `LoginPage` | 否 | 登录页 |
| `/dashboard` | `Dashboard` | 是 | 驾驶舱总览 |
| `/map` | `MapView` | 是 | GIS 地图 |
| `/video` | `VideoMonitor` | 是 | 视频监控 |
| `/alerts` | `AlertCenter` | 是 | 告警中心 |
| `/settings` | `SettingsPage` | 是 | 系统设置 |

**路由守卫**：`ProtectedRoute` 组件检查 `localStorage` 中的 `scn_access_token`，未登录重定向到 `/login`。

### 5.4 全局状态 — `store/useAppStore.ts`

**Zustand Store**，管理以下状态域：

| 状态域 | 字段 | 说明 |
|--------|------|------|
| 传感器数据 | `sensorReadings` (Map) | 每设备最新读数 |
| | `readingHistory` (Map) | 每设备最近 30 条历史 |
| 告警 | `alerts` (Array) | 告警列表（最多 200 条，自动去重） |
| 设备 | `devices` (Array) | 设备列表 |
| UI 状态 | `selectedDeviceId` | 当前选中设备 |
| | `selectedAlertId` | 当前选中告警 |
| | `sidebarCollapsed` | 侧边栏折叠状态 |
| | `isVideoPanelOpen` | 视频面板开关 |

### 5.5 API 服务层 — `services/api.ts`

基于 `axios` 封装，统一管理所有后端 API 调用：

| 命名空间 | 方法 | 对应端点 |
|----------|------|---------|
| `authAPI` | `login` | `POST /api/auth/login` |
| `sensorAPI` | `getLatest` / `getHistory` | `GET /api/sensors/latest` / `GET /api/sensors/history/{id}` |
| `alertAPI` | `getList` / `acknowledge` | `GET /api/alerts` / `POST /api/alerts/{id}/acknowledge` |
| `deviceAPI` | `getList` | `GET /api/devices` |
| `modelAPI` | `getStatus` / `switchModel` / `getVersions` | `GET /api/models/status` 等 |
| `inferenceAPI` | `detectFromBase64` / `detectFromUrl` | `POST /api/inference/detect` |
| `streamAPI` | `start` / `stop` / `getStatus` | `POST /api/streams/start` 等 |
| `mockAPI` | `getStatus` / `getConfig` / `updateConfig` / `start` / `stop` | 模拟数据控制 |

**拦截器**：
- 请求拦截器：自动注入 JWT Token（`Authorization: Bearer <token>`）
- 响应拦截器：统一错误处理

### 5.6 自定义 Hooks

#### `useSSE` — SSE 客户端

- 自动连接 `/api/sse/events`
- 监听 `sensors` 和 `alerts` 事件
- 自动重连（默认 5 秒间隔）
- 返回 `{ isConnected, reconnect, disconnect }`

#### `useWebSocket` — WebSocket 控制通道

- 连接 `/api/ws/control`
- 自动重连
- 提供 `send(type, payload)` 方法
- 支持 `ping/pong` 心跳

#### `useAlertNotifications` — 告警通知

- 监听 Zustand store 中的新告警
- 使用 Ant Design `notification` 弹窗提示
- 按告警级别区分样式：critical（不自动关闭）、warning（8秒）、info（5秒）

#### `useKeyboardShortcuts` — 全局快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+B` | 切换侧边栏 |
| `Escape` | 取消选中/关闭面板 |
| `1-5` | 快速导航到对应页面 |

### 5.7 核心组件

#### `Dashboard` — 驾驶舱大屏

三栏栅格布局：
- **左侧（2/12）**：设备状态看板 + SSE 连接指示器
- **中间（7/12）**：GIS 地图 + 视频矩阵
- **右侧（3/12）**：告警列表 + 液位趋势图 + 流量趋势图 + 系统状态

子组件：`DeviceCard`（设备卡片）、`DigitalClock`（数字时钟）、`StatsBar`（统计条）。

#### `MapVisualization` — GIS 地图

- 基于 Leaflet + CARTO 深色瓦片
- 脉冲标记（`pulse-marker`）按设备状态着色
- 设备选中时 `flyTo` 动画
- 告警浮层（`AlertOverlay`，Framer Motion 动画）
- 图例 + HUD 信息面板
- `ResizeObserver` 自动刷新地图尺寸

#### `VideoPlayer` — 多源视频播放器

- **HLS 模式**：使用 `hls.js`，支持 Safari 原生 HLS
- **本地摄像头**：`getUserMedia`，完善的权限拒绝引导
- 截帧功能（`captureFrame`）
- 状态管理：`idle` / `loading` / `playing` / `error` / `permission_denied`

#### `AlertPanel` — 告警面板

- 分级显示（critical/warning/info）
- 按未确认优先 + 级别排序
- Framer Motion 进出动画
- 确认操作按钮

#### `WaterLevelChart` / `FlowTrendChart` — ECharts 图表

- 液位折线图：平滑曲线 + 渐变填充 + 警戒线（`markLine`）
- 流量柱状图：渐变柱体 + 弹性动画
- 定时器模拟实时数据更新

### 5.8 TypeScript 类型 — `types/index.ts`

定义了完整的类型系统：`Device`、`SensorReading`、`Alert`、`AlertLevel`、`AlertType`、`SSEEvent`、`SSESensorPayload`、`SSEAlertPayload`、`Detection`、`InferenceResult`、`ModelStatus`、`StreamInfo`、`HistoricalDataPoint`。

---

## 6. 数据库设计

### 6.1 ER 关系图

```
users ──1:N──→ alerts (acknowledged_by)
  │
  └──1:N──→ model_versions (deployed_by)

devices ──1:N──→ camera_streams (device_id, CASCADE)
  │
  ├──1:N──→ alerts (device_id, SET NULL)
  │
  └──→ sensor_readings (device_id, TimescaleDB hypertable)

camera_streams ──1:N──→ inference_results (camera_id, CASCADE)
```

### 6.2 表结构

#### `users` — 用户表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID (PK) | 主键 |
| username | VARCHAR(64) UNIQUE | 用户名 |
| email | VARCHAR(255) UNIQUE | 邮箱 |
| hashed_password | VARCHAR(255) | bcrypt 哈希密码 |
| full_name | VARCHAR(128) | 全名 |
| role | VARCHAR(32) | 角色（默认 "operator"） |
| is_active | BOOLEAN | 是否激活 |
| last_login | TIMESTAMPTZ | 最后登录时间 |
| created_at / updated_at | TIMESTAMPTZ | 时间戳 |

#### `devices` — 设备表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID (PK) | 主键 |
| device_code | VARCHAR(64) UNIQUE | 设备编码 |
| name | VARCHAR(255) | 设备名称 |
| device_type | VARCHAR(64) | 设备类型（默认 "manhole_cover"） |
| status | ENUM | online/offline/fault/maintenance |
| latitude / longitude | FLOAT | 经纬度 |
| altitude | FLOAT | 海拔 |
| district | VARCHAR(128) | 区域 |
| battery_level | FLOAT | 电量 |
| signal_strength | INTEGER | 信号强度 |
| metadata | JSONB | 扩展数据 |
| last_heartbeat | TIMESTAMPTZ | 最后心跳时间 |

#### `camera_streams` — 摄像头流表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID (PK) | 主键 |
| device_id | UUID (FK → devices) | 关联设备 |
| name | VARCHAR(255) | 流名称 |
| stream_url | TEXT | 流地址 |
| protocol | ENUM | rtsp/hls/webrtc/local |
| hls_url / webrtc_url | TEXT | 转码后地址 |
| is_active | BOOLEAN | 是否活跃 |
| resolution_width / resolution_height | INTEGER | 分辨率 |
| fps | INTEGER | 帧率 |

#### `alerts` — 告警表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID (PK) | 主键 |
| device_id | UUID (FK → devices, SET NULL) | 关联设备 |
| alert_type | ENUM | water_accumulation/manhole_anomaly/intrusion/illegal_parking/water_level_high/flow_anomaly/device_offline/system_error |
| level | ENUM | critical/warning/info |
| title | VARCHAR(255) | 告警标题 |
| description | TEXT | 描述 |
| snapshot_url | TEXT | 快照图片 URL |
| bbox_coordinates | JSONB | 检测框坐标 |
| detection_confidence | FLOAT | 检测置信度 |
| is_acknowledged | BOOLEAN | 是否已确认 |
| acknowledged_by | UUID (FK → users) | 确认人 |
| is_resolved | BOOLEAN | 是否已解决 |
| metadata | JSONB | 扩展数据 |

#### `model_versions` — 模型版本表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID (PK) | 主键 |
| version_name | VARCHAR(32) UNIQUE | 版本名 |
| file_path | VARCHAR(512) | 文件路径 |
| file_size_bytes | INTEGER | 文件大小 |
| sha256_hash | VARCHAR(64) | SHA256 哈希 |
| model_type | VARCHAR(64) | 模型类型 |
| status | ENUM | loading/active/unloading/error |
| metrics | JSONB | 性能指标 |
| deployed_by | UUID (FK → users) | 部署人 |

#### `inference_results` — 推理结果表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID (PK) | 主键 |
| camera_id | UUID (FK → camera_streams) | 关联摄像头 |
| model_version | VARCHAR(32) | 使用的模型版本 |
| inference_time_ms | FLOAT | 推理耗时 |
| detections | JSONB | 检测结果列表 |
| frame_timestamp | TIMESTAMPTZ | 帧时间戳 |

#### `sensor_readings` — 传感器读数表（TimescaleDB 超表）

| 字段 | 类型 | 说明 |
|------|------|------|
| time | TIMESTAMPTZ | 时间戳（分区键） |
| device_id | UUID | 设备 ID |
| water_level_mm | FLOAT | 液位（mm） |
| flow_rate_m3h | FLOAT | 流量（m³/h） |
| water_quality_ph | FLOAT | pH 值 |
| water_quality_turbidity | FLOAT | 浊度 |
| temperature_c | FLOAT | 温度 |
| humidity_pct | FLOAT | 湿度 |
| battery_voltage | FLOAT | 电池电压 |
| signal_strength | INTEGER | 信号强度 |
| extra | JSONB | 扩展数据 |

**索引**：`idx_sensor_device_time ON sensor_readings (device_id, time DESC)`

---

## 7. API 接口文档

所有 API 前缀：`/api`

### 7.1 认证

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/api/auth/login` | 否 | 用户登录（默认 admin / Admin@123456） |
| GET | `/api/auth/me` | 是 | 获取当前用户信息 |

### 7.2 健康检查

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | Docker 健康检查 |
| GET | `/api/health` | 系统健康检查（含模型状态） |

### 7.3 模型管理

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/api/models/status` | 否 | 获取当前模型状态 |
| POST | `/api/models/switch` | 是 | 热切换 AI 模型 |
| POST | `/api/models/upload` | 是 | 上传新模型权重（.pt/.onnx/.engine） |
| GET | `/api/models/versions` | 否 | 列出所有可用模型版本 |

### 7.4 AI 推理

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/api/inference/detect` | 否 | YOLO 目标检测（Base64 或 URL 图像） |

### 7.5 视频流

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/api/streams/start` | 是 | 启动 RTSP → HLS 转码 |
| POST | `/api/streams/{camera_id}/stop` | 是 | 停止转码 |
| GET | `/api/streams/status` | 否 | 获取所有流状态 |

### 7.6 传感器数据

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sensors/latest` | 获取最新传感器数据 |
| GET | `/api/sensors/history/{device_id}` | 获取历史数据（参数：hours, interval_minutes） |

### 7.7 告警管理

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/api/alerts` | 否 | 获取告警列表（参数：limit, level） |
| POST | `/api/alerts/{alert_id}/acknowledge` | 是 | 确认/解决/忽略告警 |

### 7.8 设备管理

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/api/devices` | 否 | 获取所有设备列表 |
| POST | `/api/devices` | 是 | 创建设备 |
| PUT | `/api/devices/{device_id}` | 是 | 更新设备 |
| DELETE | `/api/devices/{device_id}` | 是 | 删除设备 |

### 7.9 模拟数据控制

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/mock/status` | 获取生成器状态 |
| GET | `/api/mock/config` | 获取生成器配置 |
| PUT | `/api/mock/config` | 更新生成器配置 |
| POST | `/api/mock/start` | 启动模拟数据 |
| POST | `/api/mock/stop` | 停止模拟数据 |

### 7.10 实时通信

| 类型 | 路径 | 说明 |
|------|------|------|
| SSE | `/api/sse/events` | 事件流（sensors/alerts/system 事件） |
| WebSocket | `/api/ws/control` | 控制通道（PTZ 云台控制、设备状态） |

---

## 8. 实时通信机制

### 8.1 SSE（Server-Sent Events）

**用途**：服务器主动推送传感器数据和告警到前端。

**数据流**：

```
MockDataGenerator / InferenceWorker
    ↓ (broadcast_sensor / broadcast_alert)
SSEManager.broadcast()
    ↓ (put into asyncio.Queue per client)
SSE EventSourceResponse → 前端 EventSource
```

**前端连接**：

```typescript
const es = new EventSource('/api/sse/events');
es.addEventListener('sensors', (e) => { /* 处理传感器数据 */ });
es.addEventListener('alerts', (e) => { /* 处理告警 */ });
```

**心跳机制**：30 秒超时后发送 `: heartbeat` 注释行保持连接。

### 8.2 WebSocket

**用途**：双向控制通道，前端发送控制指令（如 PTZ 云台控制），后端推送设备状态变更。

**消息格式**：

```json
{ "type": "control", "payload": { "command": "ptz_left" } }
{ "type": "control_ack", "payload": { "status": "received" } }
{ "type": "ping" } → { "type": "pong" }
```

### 8.3 Redis Pub/Sub

**用途**：跨服务实时通信（预留，当前 SSE 直连）。

| 频道 | 消息类型 |
|------|---------|
| `scn:alerts` | 告警事件 |
| `scn:sensor_data` | 传感器实时数据 |
| `scn:model_status` | 模型状态变更 |

---

## 9. AI 推理管道

### 9.1 推理流程

```
图像输入 (Base64/URL/帧)
    ↓
InferenceService._run_inference()
    ↓
ModelManager.predict()  ←── 线程安全（_inference_lock）
    ↓
YOLO model.predict()    ←── Ultralytics 推理
    ↓
ModelManager._parse_results()  ←── 标准化检测列表
    ↓
返回检测结果 + 可选标注图像
```

### 9.2 视频流推理管道

```
RTSP 视频流
    ↓
InferenceWorker._run_loop()
    ↓ (按 inference_interval 间隔)
cv2.VideoCapture.read() → 帧
    ↓
ModelManager.predict(frame)
    ↓
检测结果 → ALERT_CLASSES 匹配
    ↓ (超过阈值)
SSEManager.broadcast_alert() → 前端
    ↓
保存快照 (alert_*.jpg)
```

### 9.3 模型热切换时序

```
API: POST /api/models/switch { target_version: "v2" }
    ↓
ModelManager.switch_model_async("v2")
    ↓ (后台线程)
Phase 1: load_model("v2")  ←── 不阻塞当前推理
Phase 2: _verify_model()   ←── 空白图像验证
Phase 3: _switch_lock → 原子替换 _model 指针
Phase 4: _unload_model(old) ←── gc + CUDA cache 清理
Phase 5: _fire_switch_callbacks()
```

---

## 10. 部署与运行

### 10.1 Docker Compose 一键部署

```bash
cd smart-city-drainage
docker-compose up -d
```

**服务编排**：

| 服务 | 镜像 | 端口 | 依赖 |
|------|------|------|------|
| `postgres` | timescale/timescaledb:latest-pg15 | 5432 | — |
| `redis` | redis:7-alpine | 6379 | — |
| `influxdb` | influxdb:2.7-alpine | 8086 | — |
| `backend` | 自建 (nvidia/cuda:12.1-runtime) | 8000 | postgres, redis, influxdb |
| `nginx` | nginx:alpine | 80/443 | backend |
| `frontend` | 自建 (node:20-alpine → nginx:alpine) | 3000 | — (production profile) |

**网络**：`scn-network`（bridge 驱动）

**数据卷**：`pgdata`、`redisdata`、`influxdata`、`hls_media`

### 10.2 本地开发

#### 后端

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

#### 前端

```bash
cd frontend
npm install
npm run dev    # http://localhost:5173
```

**开发代理**：Vite 配置了 `/api` 和 `/hls` 代理到 `http://localhost:8000`。

### 10.3 Docker 镜像构建

#### 后端镜像

- 基础镜像：`nvidia/cuda:12.1-runtime-ubuntu22.04`
- 安装 Python 3.11、FFmpeg、OpenCV 系统依赖
- 虚拟环境 `/opt/venv`
- 健康检查：`httpx.get('http://localhost:8000/health')`

#### 前端镜像

- 构建阶段：`node:20-alpine` → `npm run build`
- 运行阶段：`nginx:alpine` → 托管 `dist/` 静态文件

### 10.4 Nginx 配置

| 路径 | 代理目标 | 特殊配置 |
|------|---------|---------|
| `/hls/` | 静态文件 | HLS 缓存（`hls_cache` zone, 1GB） |
| `/api/` | `backend:8000` | WebSocket 升级支持 |
| `/api/sse/` | `backend:8000` | 禁用缓冲，24h 超时 |
| `/api/ws/` | `backend:8000` | WebSocket 升级，24h 超时 |
| `/health` | `backend:8000/health` | 无访问日志 |

### 10.5 数据库初始化

- Docker 启动时自动执行 `docker/init-db.sql`
- 创建 TimescaleDB 扩展 + 所有表 + 枚举类型 + 索引
- 插入默认管理员用户 + 示例设备数据
- Alembic 迁移：`alembic upgrade head`

---

## 11. 配置参考

### 环境变量（`.env`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `postgresql+asyncpg://...` | 异步数据库连接串 |
| `DATABASE_URL_SYNC` | `postgresql://...` | 同步数据库连接串 |
| `INFLUXDB_URL` | `http://localhost:8086` | InfluxDB 地址 |
| `INFLUXDB_TOKEN` | `scn-influx-token-2024-secure` | InfluxDB Token |
| `INFLUXDB_ORG` | `smart-city` | InfluxDB 组织 |
| `INFLUXDB_BUCKET` | `sensor_data` | InfluxDB Bucket |
| `REDIS_URL` | `redis://:...@localhost:6379/0` | Redis 连接串 |
| `JWT_SECRET_KEY` | `scn-jwt-secret-key-...` | JWT 签名密钥 |
| `JWT_ALGORITHM` | `HS256` | JWT 算法 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Token 过期时间（分钟） |
| `MODEL_STORAGE_PATH` | `./models/weights` | 模型文件目录 |
| `DEFAULT_MODEL_VERSION` | `v1` | 默认模型版本 |
| `MODEL_INFERENCE_DEVICE` | `cuda:0` | 推理设备（cuda:0 或 cpu） |
| `MODEL_CONFIDENCE_THRESHOLD` | `0.45` | 检测置信度阈值 |
| `MODEL_IOU_THRESHOLD` | `0.45` | NMS IoU 阈值 |
| `HLS_OUTPUT_DIR` | `./backend/media/hls` | HLS 输出目录 |
| `SCREENSHOT_DIR` | `./backend/media/screenshots` | 截图输出目录 |
| `HLS_SEGMENT_TIME` | `4` | HLS 分片时长（秒） |
| `HLS_LIST_SIZE` | `6` | HLS 播放列表保留分片数 |
| `FFMPEG_PATH` | `ffmpeg` | FFmpeg 可执行文件路径 |
| `CORS_ORIGINS` | `["http://localhost:5173",...]` | CORS 允许来源 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

### Vite 路径别名

| 别名 | 路径 |
|------|------|
| `@` | `./src` |
| `@components` | `./src/components` |
| `@pages` | `./src/pages` |
| `@hooks` | `./src/hooks` |
| `@store` | `./src/store` |
| `@services` | `./src/services` |
| `@types` | `./src/types` |
| `@assets` | `./src/assets` |

### 构建分包策略

| chunk | 包含 |
|-------|------|
| `vendor` | react, react-dom, react-router-dom |
| `antd` | antd, @ant-design/icons |
| `echarts` | echarts, echarts-for-react |
| `leaflet` | leaflet, react-leaflet |

---

> 📝 本文档由代码分析自动生成，最后更新时间：2026-05-15
