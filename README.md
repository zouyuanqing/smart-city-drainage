# 🏙️ 智慧城市神经末梢 — 市政排水智能监测与AI安防系统

<div align="center">

![Version](https://img.shields.io/badge/version-1.0.0-blue)
![Python](https://img.shields.io/badge/python-3.11+-3776AB)
![React](https://img.shields.io/badge/react-18.x-61DAFB)
![TypeScript](https://img.shields.io/badge/typescript-5.x-3178C6)
![FastAPI](https://img.shields.io/badge/fastapi-0.109-009688)
![License](https://img.shields.io/badge/license-Apache%202.0-blue)

**打造具有科幻电影驾驶舱视觉效果的城市排水数字孪生系统**

</div>

---

## 📋 目录

- [项目概述](#-项目概述)
- [核心能力](#-核心能力)
- [技术架构](#-技术架构)
- [技术栈](#-技术栈)
- [快速开始](#-快速开始)
  - [Docker 一键部署（推荐）](#docker-一键部署推荐)
  - [本地开发](#本地开发)
- [项目结构](#-项目结构)
- [环境变量配置](#-环境变量配置)
- [API 文档](#-api-文档)
- [数据库设计](#-数据库设计)
- [AI 推理系统](#-ai-推理系统)
- [视频流处理](#-视频流处理)
- [实时数据推送](#-实时数据推送)
- [前端页面](#-前端页面)
- [Docker 镜像说明](#-docker-镜像说明)
- [默认账户](#-默认账户)
- [常见问题](#-常见问题)
- [License](#-license)

---

## 📋 项目概述

本系统是一个基于现代 Web 架构的前后端分离系统，用于市政排水实时监测。集成 AI 计算机视觉（YOLOv8/v10），支持多源视频流接入，具备工业级模型热切换能力。

系统采用 **PostgreSQL (TimescaleDB) + InfluxDB + Redis** 三库协同架构，分别处理关系数据、时序数据和缓存/实时消息，通过 SSE + WebSocket 实现毫秒级数据推送。

### 核心能力

- 🔭 **实时监测**: 液位、流量、水质等传感器数据毫秒级推送（SSE）
- 🤖 **AI 视觉**: YOLO 积水识别、井盖异常检测、安防入侵告警
- 🗺️ **GIS 可视化**: 基于 Leaflet 的城市排水管网地理信息展示
- 📹 **多源视频**: RTSP → HLS 实时转码，支持多路视频流并发
- 🔄 **零停机更新**: AI 模型热切换，不中断推理服务
- 📊 **时序分析**: InfluxDB 存储传感器历史数据，支持趋势分析
- 🔔 **智能告警**: 多级告警体系（critical/warning/info），支持确认与解决流程
- 🎮 **模拟模式**: 内置模拟数据生成器，无需真实设备即可体验全部功能

---

## 🏗️ 技术架构

```
┌──────────────────────────────────────────────────────────────┐
│                     Frontend (React 18)                       │
│  Vite + TypeScript + Tailwind + Zustand + ECharts + Leaflet  │
├──────────────────────────────────────────────────────────────┤
│                    Nginx Reverse Proxy                        │
│            API 代理 / SSE 长连接 / HLS 流媒体                  │
├──────────────────────────────────────────────────────────────┤
│                  FastAPI Backend (Python 3.11)                │
│  ┌───────────┐ ┌────────────┐ ┌──────────────────┐          │
│  │ API Service│ │Inference   │ │ Stream Service   │          │
│  │ (CRUD/Auth)│ │ Worker     │ │ (FFmpeg/RTSP→HLS)│          │
│  └───────────┘ └────────────┘ └──────────────────┘          │
│  ┌───────────┐ ┌────────────┐ ┌──────────────────┐          │
│  │SSE Manager│ │Mock Data   │ │ System Status    │          │
│  │ (实时推送) │ │ Generator  │ │ (健康检查)        │          │
│  └───────────┘ └────────────┘ └──────────────────┘          │
├──────────────────────────────────────────────────────────────┤
│  PostgreSQL     InfluxDB      Redis       Model Weights      │
│  (TimescaleDB)  (时序数据)    (缓存/PubSub)  (YOLO .pt)      │
└──────────────────────────────────────────────────────────────┘
```

### 数据流架构

```
传感器/设备 ──→ FastAPI ──→ InfluxDB (时序存储)
                    │
                    ├──→ Redis Pub/Sub ──→ SSE Manager ──→ 前端 (实时推送)
                    │
RTSP 摄像头 ──→ FFmpeg ──→ HLS 流 ──→ Nginx ──→ 前端 (视频播放)
                    │
                    └──→ Inference Worker ──→ YOLO 推理 ──→ 告警生成
```

---

## 🛠 技术栈

### 后端

| 类别 | 技术 | 版本 | 说明 |
|------|------|------|------|
| Web 框架 | FastAPI | 0.109 | 高性能异步 API 框架 |
| ASGI 服务器 | Uvicorn | 0.27 | 支持 WebSocket 和 SSE |
| ORM | SQLAlchemy | 2.0 | 异步 ORM，支持 asyncpg |
| 数据库迁移 | Alembic | 1.13 | 数据库版本管理 |
| 关系数据库 | PostgreSQL (TimescaleDB) | PG15 | 关系数据 + 时序扩展 |
| 时序数据库 | InfluxDB | 2.7 | 传感器时序数据存储 |
| 缓存 | Redis | 7 | 缓存 + Pub/Sub 消息总线 |
| AI 推理 | Ultralytics (YOLOv8) | 8.1 | 目标检测与图像分割 |
| 深度学习 | PyTorch | 2.1+ | GPU/CPU 推理后端 |
| 视频处理 | FFmpeg | - | RTSP → HLS 转码 |
| 认证 | JWT (python-jose) | 3.3 | Token 认证 + bcrypt 密码哈希 |
| 配置管理 | pydantic-settings | 2.1 | 类型安全的环境变量管理 |

### 前端

| 类别 | 技术 | 版本 | 说明 |
|------|------|------|------|
| UI 框架 | React | 18.3 | 组件化 UI |
| 语言 | TypeScript | 5.x | 类型安全 |
| 构建工具 | Vite | 5.1 | 极速 HMR 和构建 |
| UI 组件库 | Ant Design | 5.14 | 企业级组件 |
| 状态管理 | Zustand | 4.5 | 轻量级状态管理 |
| 图表 | ECharts | 5.5 | 数据可视化 |
| 地图 | Leaflet + React-Leaflet | 1.9 / 4.2 | GIS 地图展示 |
| 视频播放 | hls.js | 1.5 | HLS 流播放 |
| 样式 | Tailwind CSS | 3.4 | 原子化 CSS |
| 动画 | Framer Motion | 11.0 | 流畅动画效果 |

---

## 🚀 快速开始

### Docker 一键部署（推荐）

#### 前置要求

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) 或 Docker Engine + Docker Compose v2
- 至少 8GB 可用内存（AI 推理模型较大）
- 至少 10GB 可用磁盘空间

#### 启动基础设施 + 后端 + Nginx

```bash
cd smart-city-drainage
docker compose up -d
```

这将启动以下服务：
- PostgreSQL (TimescaleDB) — 端口 5432
- Redis — 端口 6379
- InfluxDB — 端口 8086
- FastAPI 后端 — 端口 8000
- Nginx 反向代理 — 端口 8080

#### 启动前端（生产模式）

```bash
docker compose --profile production up -d frontend
```

前端将通过 Nginx 托管静态文件 — 端口 3000

#### 访问地址

| 服务 | 地址 | 说明 |
|------|------|------|
| 前端页面 | http://localhost:3000 | React SPA |
| 后端 API | http://localhost:8000 | FastAPI |
| Nginx 代理 | http://localhost:8080 | 反向代理 + HLS |
| API 文档 | http://localhost:8000/docs | Swagger UI |
| InfluxDB 控制台 | http://localhost:8086 | 时序数据管理 |

#### 停止所有服务

```bash
docker compose --profile production down
```

#### 重新构建镜像

```bash
# 仅重建后端
docker compose build backend

# 仅重建前端
docker compose build frontend

# 重建全部
docker compose --profile production build
```

---

### 本地开发

#### 前置要求

- Python 3.11+
- Node.js 18+
- PostgreSQL 15+ (或 TimescaleDB)
- Redis 7+
- InfluxDB 2.7+
- FFmpeg (用于视频流转码)
- CUDA 11.8+ (GPU 推理，可选)

#### 1. 启动基础设施

```bash
docker compose up -d postgres redis influxdb
```

#### 2. 启动后端

```bash
cd backend

# 安装依赖
pip install -r requirements.txt        # GPU 模式
# 或
pip install -r requirements-cpu.txt    # CPU 模式（推荐无 GPU 环境）

# 数据库迁移
alembic upgrade head

# 启动开发服务器
uvicorn app.main:app --reload --port 8000
```

#### 3. 启动前端

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

前端开发服务器默认运行在 http://localhost:5173，已配置代理将 `/api` 请求转发到后端 8000 端口。

---

## 📁 项目结构

```
smart-city-drainage/
├── backend/                          # FastAPI 后端
│   ├── app/
│   │   ├── main.py                   # 应用入口，生命周期管理
│   │   ├── api/
│   │   │   └── routes.py             # 全部 API 路由定义
│   │   ├── core/
│   │   │   ├── config.py             # pydantic-settings 配置管理
│   │   │   ├── database.py           # SQLAlchemy 异步引擎 + 会话
│   │   │   ├── model_manager.py      # YOLO 模型管理器（单例，热切换）
│   │   │   ├── redis_client.py       # Redis 客户端 + Pub/Sub
│   │   │   └── security.py           # JWT + bcrypt 认证工具
│   │   ├── models/
│   │   │   └── db_models.py          # SQLAlchemy ORM 模型
│   │   ├── schemas/                  # Pydantic 请求/响应模式
│   │   ├── services/
│   │   │   ├── inference_service.py  # YOLO 推理服务
│   │   │   ├── stream_service.py     # 视频流管理服务
│   │   │   ├── sse_manager.py        # SSE 事件推送管理器
│   │   │   ├── influxdb_service.py   # InfluxDB 时序数据读写
│   │   │   ├── mock_data_generator.py# 模拟数据生成器
│   │   │   └── system_status.py      # 系统状态检查服务
│   │   └── workers/
│   │       └── inference_worker.py   # 推理 Worker（独立协程）
│   ├── alembic/                      # 数据库迁移
│   │   └── versions/
│   │       └── 0001_initial.py       # 初始迁移
│   ├── media/                        # 运行时媒体输出
│   │   ├── hls/                      # HLS 流分片
│   │   └── screenshots/              # 推理截图
│   ├── requirements.txt              # Python 依赖（GPU）
│   └── requirements-cpu.txt          # Python 依赖（CPU only）
│
├── frontend/                         # React 前端
│   ├── src/
│   │   ├── components/
│   │   │   ├── charts/               # ECharts 图表组件
│   │   │   │   ├── WaterLevelChart.tsx
│   │   │   │   └── FlowTrendChart.tsx
│   │   │   ├── map/                  # Leaflet 地图组件
│   │   │   │   └── MapVisualization.tsx
│   │   │   ├── video/                # 视频播放器组件
│   │   │   │   └── VideoPlayer.tsx
│   │   │   ├── alerts/               # 告警面板组件
│   │   │   │   └── AlertPanel.tsx
│   │   │   ├── device/               # 设备详情组件
│   │   │   ├── common/               # 通用组件
│   │   │   └── layout/               # 布局组件
│   │   ├── pages/                    # 页面组件
│   │   │   ├── LandingPage.tsx       # 着陆页
│   │   │   ├── LoginPage.tsx         # 登录页
│   │   │   ├── Dashboard.tsx         # 仪表盘
│   │   │   ├── MapView.tsx           # 地图视图
│   │   │   ├── AlertCenter.tsx       # 告警中心
│   │   │   ├── VideoMonitor.tsx      # 视频监控
│   │   │   └── SettingsPage.tsx      # 系统设置
│   │   ├── hooks/                    # 自定义 React Hooks
│   │   │   ├── useSSE.ts             # SSE 连接 Hook
│   │   │   ├── useWebSocket.ts       # WebSocket Hook
│   │   │   └── useAlertNotifications.tsx
│   │   ├── store/
│   │   │   └── useAppStore.ts        # Zustand 全局状态
│   │   ├── services/
│   │   │   └── api.ts                # Axios API 客户端
│   │   ├── types/
│   │   │   └── index.ts              # TypeScript 类型定义
│   │   └── styles/
│   │       └── globals.css           # 全局样式
│   ├── nginx-default.conf            # 前端 Nginx 配置
│   └── package.json
│
├── models/                           # YOLO 模型仓库
│   └── weights/                      # 模型权重文件 (.pt)
│
├── docker/                           # Docker 相关
│   ├── init-db.sql                   # 数据库初始化 SQL
│   ├── backend/
│   │   ├── Dockerfile                # 后端镜像（GPU + CUDA）
│   │   └── Dockerfile.cpu            # 后端镜像（CPU only）
│   ├── frontend/
│   │   └── Dockerfile                # 前端镜像（多阶段构建）
│   └── nginx/
│       ├── nginx.conf                # Nginx 主配置
│       └── conf.d/
│           └── default.conf          # 站点配置（代理 + HLS）
│
├── docker-compose.yml                # Docker Compose 编排
├── .env                              # 环境变量
└── CODE_WIKI.md                      # 代码 Wiki 文档
```

---

## ⚙️ 环境变量配置

所有配置通过 `.env` 文件或环境变量管理，使用 `pydantic-settings` 提供类型安全和验证。

### 应用基础

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `APP_NAME` | Smart City Neural Endpoints | 应用名称 |
| `APP_VERSION` | 1.0.0 | 版本号 |
| `DEBUG` | false | 调试模式 |
| `LOG_LEVEL` | INFO | 日志级别 (DEBUG/INFO/WARNING/ERROR) |

### 数据库

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `DATABASE_URL` | postgresql+asyncpg://... | 异步数据库连接串 |
| `DATABASE_URL_SYNC` | postgresql://... | 同步数据库连接串 |
| `DB_POOL_SIZE` | 20 | 连接池大小 |
| `DB_MAX_OVERFLOW` | 10 | 最大溢出连接数 |

### InfluxDB

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `INFLUXDB_URL` | http://localhost:8086 | InfluxDB 地址 |
| `INFLUXDB_TOKEN` | scn-influx-token-2024-secure | API Token |
| `INFLUXDB_ORG` | smart-city | 组织 |
| `INFLUXDB_BUCKET` | sensor_data | 存储桶 |

### Redis

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `REDIS_URL` | redis://:...@localhost:6379/0 | Redis 连接串 |

### JWT 认证

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `JWT_SECRET_KEY` | scn-jwt-secret-key-... | JWT 签名密钥（**生产环境务必修改**） |
| `JWT_ALGORITHM` | HS256 | 签名算法 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 1440 | Token 过期时间（分钟） |

### AI 模型

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `MODEL_STORAGE_PATH` | ./models/weights | 模型文件存储路径 |
| `DEFAULT_MODEL_VERSION` | v1 | 默认加载的模型版本 |
| `MODEL_INFERENCE_DEVICE` | cuda:0 | 推理设备：`cuda:0` 或 `cpu` |
| `MODEL_CONFIDENCE_THRESHOLD` | 0.45 | 检测置信度阈值 |
| `MODEL_IOU_THRESHOLD` | 0.45 | NMS IoU 阈值 |

### 视频流

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `FFMPEG_PATH` | ffmpeg | FFmpeg 可执行文件路径 |
| `FFPROBE_PATH` | ffprobe | FFprobe 可执行文件路径 |
| `HLS_OUTPUT_DIR` | ./backend/media/hls | HLS 输出目录 |
| `SCREENSHOT_DIR` | ./backend/media/screenshots | 截图输出目录 |
| `HLS_SEGMENT_TIME` | 4 | HLS 分片时长（秒） |
| `HLS_LIST_SIZE` | 6 | HLS 播放列表保留分片数 |

### 安全

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `CORS_ORIGINS` | ["http://localhost:5173","http://localhost:3000"] | 允许的跨域来源 |
| `RATE_LIMIT_PER_MINUTE` | 60 | 每分钟请求限制 |
| `MAX_UPLOAD_SIZE_MB` | 100 | 最大上传大小（MB） |

---

## 📡 API 文档

启动后端后访问 http://localhost:8000/docs 查看完整的 Swagger UI 交互式文档。

### API 端点总览

所有端点前缀为 `/api`，认证端点需在 Header 中携带 `Authorization: Bearer <token>`。

#### 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 用户登录，返回 JWT Token |
| GET | `/api/auth/me` | 获取当前用户信息 🔒 |

#### 系统

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/system/status` | 系统状态（DB/Redis/InfluxDB/Model 连接状态） |

#### 设备

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/devices` | 获取所有设备列表 |
| POST | `/api/devices` | 创建设备 🔒 |
| PUT | `/api/devices/{device_id}` | 更新设备 🔒 |
| DELETE | `/api/devices/{device_id}` | 删除设备 🔒 |

#### 传感器

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sensors/latest` | 获取最新传感器数据 |
| GET | `/api/sensors/history/{device_id}` | 获取传感器历史数据 |

#### 告警

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/alerts` | 获取告警列表（支持 limit/level 过滤） |
| POST | `/api/alerts/{alert_id}/acknowledge` | 确认/解决告警 🔒 |

#### AI 模型

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/models/status` | 获取当前模型状态 |
| GET | `/api/models/versions` | 列出所有可用模型版本 |
| POST | `/api/models/switch` | 热切换 AI 模型 🔒 |
| POST | `/api/models/upload` | 上传新模型权重 🔒 |
| POST | `/api/inference/detect` | YOLO 目标检测推理 |

#### 视频流

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/streams/start` | 启动 RTSP → HLS 转码 🔒 |
| POST | `/api/streams/{camera_id}/stop` | 停止视频流 🔒 |
| GET | `/api/streams/status` | 获取所有流状态 |
| POST | `/api/streams/{camera_id}/inference/start` | 启动视频流推理 🔒 |
| POST | `/api/streams/{camera_id}/inference/stop` | 停止视频流推理 🔒 |

#### 实时通信

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sse/events` | SSE 实时事件流 |
| WebSocket | `/api/ws/control` | WebSocket 控制通道 |

#### 模拟数据

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/mock/status` | 获取模拟数据生成器状态 |
| GET | `/api/mock/config` | 获取模拟数据配置 |
| PUT | `/api/mock/config` | 更新模拟数据配置 |
| POST | `/api/mock/start` | 启动模拟数据生成 |
| POST | `/api/mock/stop` | 停止模拟数据生成 |

> 🔒 = 需要 JWT 认证

---

## 🗄️ 数据库设计

### PostgreSQL (TimescaleDB)

系统使用 PostgreSQL 存储关系数据，TimescaleDB 扩展处理传感器时序数据。

#### 枚举类型

| 类型 | 值 |
|------|----|
| `device_status` | online, offline, fault, maintenance |
| `alert_level` | critical, warning, info |
| `alert_type` | water_accumulation, manhole_anomaly, intrusion, illegal_parking, water_level_high, flow_anomaly, device_offline, system_error |
| `stream_protocol` | rtsp, hls, webrtc, local |
| `model_status` | loading, active, unloading, error |

#### 数据表

| 表名 | 说明 | 主要字段 |
|------|------|----------|
| `users` | 用户表 | id, username, email, hashed_password, role, is_active |
| `devices` | 设备表 | id, device_code, name, device_type, status, latitude, longitude, district |
| `camera_streams` | 摄像头视频流 | id, device_id, stream_url, protocol, hls_url, is_active |
| `sensor_readings` | 传感器时序数据 | time, device_id, water_level_mm, flow_rate_m3h, ph, turbidity |
| `alerts` | 告警表 | id, device_id, alert_type, level, title, is_acknowledged, is_resolved |
| `model_versions` | 模型版本 | id, version_name, file_path, status, metrics |
| `inference_results` | 推理结果 | id, camera_id, model_version, detections, inference_time_ms |

### InfluxDB

存储高频传感器时序数据，支持 downsampling 和数据保留策略。

- **Organization**: `smart-city`
- **Bucket**: `sensor_data`
- **Measurement**: `sensor_readings`
- **Tags**: `device_id`, `device_type`, `district`
- **Fields**: `water_level_mm`, `flow_rate_m3h`, `water_quality_ph`, `temperature_c`, `humidity_pct`

### Redis

用于缓存、实时状态和 Pub/Sub 消息总线。

| 频道 | 说明 |
|------|------|
| `scn:alerts` | 告警事件广播 |
| `scn:sensor_data` | 传感器数据广播 |
| `scn:model_status` | 模型状态变更广播 |

---

## 🤖 AI 推理系统

### 模型管理

系统通过 `ModelManager` 单例管理 YOLO 模型生命周期：

- **模型加载**: 启动时自动加载默认版本模型
- **热切换**: 通过 API 切换模型版本，不中断推理服务
- **多版本管理**: 支持上传和管理多个模型版本
- **设备适配**: 支持 CUDA GPU 和 CPU 推理

### 推理流程

```
图像输入 (Base64/URL/视频帧)
    │
    ▼
InferenceService.preprocess()
    │  图像解码 → Resize → 归一化
    ▼
ModelManager.predict()
    │  YOLOv8 推理 → 后处理 (NMS)
    ▼
InferenceService.postprocess()
    │  检测框 → 类别映射 → 置信度过滤
    ▼
输出结果 (JSON)
```

### 支持的检测类别

基于 COCO 数据集的 80 个类别，重点关注：

- **person**: 人员检测（入侵告警）
- **car/truck/bus**: 车辆检测（违停告警）
- 自定义类别：积水区域、井盖异常

### 模型文件

将 YOLO 模型权重文件 (`.pt`) 放入 `models/weights/` 目录：

```
models/weights/
├── v1/
│   └── best.pt          # 默认模型
├── v2/
│   └── best.pt          # 升级模型
└── ...
```

---

## 📹 视频流处理

### RTSP → HLS 转码流程

```
RTSP 摄像头
    │
    ▼ FFmpeg 转码
HLS 分片 (.ts + .m3u8)
    │
    ├──→ Nginx 静态托管 → 前端 hls.js 播放
    │
    └──→ InferenceWorker 抽帧 → YOLO 推理 → 告警生成
```

### FFmpeg 转码参数

- 视频编码: H.264
- HLS 分片时长: 4 秒
- 播放列表保留: 6 个分片
- 自适应码率: 支持

---

## 📡 实时数据推送

### SSE (Server-Sent Events)

前端通过 `/api/sse/events` 建立 SSE 长连接，接收实时推送：

| 事件类型 | 说明 |
|----------|------|
| `sensor_data` | 传感器数据更新 |
| `alert` | 新告警通知 |
| `model_status` | 模型状态变更 |
| `stream_status` | 视频流状态变更 |
| `system_status` | 系统状态更新 |

### WebSocket

通过 `/api/ws/control` 建立 WebSocket 连接，支持双向通信：

- PTZ 云台控制
- 设备远程控制
- 实时状态查询

---

## 🖥 前端页面

| 页面 | 路径 | 说明 |
|------|------|------|
| 着陆页 | `/` | 项目介绍、实时统计概览 |
| 登录页 | `/login` | JWT 认证登录 |
| 仪表盘 | `/dashboard` | 设备概览、传感器图表、视频矩阵、告警摘要 |
| 地图视图 | `/map` | GIS 地图展示设备位置和状态 |
| 告警中心 | `/alerts` | 告警列表、过滤、确认/解决 |
| 视频监控 | `/video` | 多路视频流监控、推理结果叠加 |
| 系统设置 | `/settings` | 模型管理、系统状态、模拟数据配置 |

---

## 🐳 Docker 镜像说明

### 镜像列表

| 镜像 | Dockerfile | 说明 |
|------|-----------|------|
| `smart-city-drainage-backend` | `docker/backend/Dockerfile.cpu` | CPU 模式后端（默认） |
| `smart-city-drainage-backend` | `docker/backend/Dockerfile` | GPU + CUDA 模式后端 |
| `smart-city-drainage-frontend` | `docker/frontend/Dockerfile` | 前端（多阶段构建） |

### 容器编排

```bash
# 基础启动（基础设施 + 后端 + Nginx）
docker compose up -d

# 包含前端（生产模式）
docker compose --profile production up -d

# 使用 GPU 后端
# 修改 docker-compose.yml 中 backend 的 dockerfile 为 ../docker/backend/Dockerfile
# 并添加 NVIDIA runtime 配置
```

### 端口映射

| 服务 | 容器端口 | 主机端口 | 说明 |
|------|----------|----------|------|
| PostgreSQL | 5432 | 5432 | 数据库 |
| Redis | 6379 | 6379 | 缓存 |
| InfluxDB | 8086 | 8086 | 时序数据库 |
| Backend | 8000 | 8000 | API 服务 |
| Nginx | 80 | 8080 | 反向代理 |
| Frontend | 80 | 3000 | 前端静态文件 |

### 数据卷

| 卷名 | 挂载点 | 说明 |
|------|--------|------|
| `pgdata` | /var/lib/postgresql/data | PostgreSQL 数据持久化 |
| `redisdata` | /data | Redis 数据持久化 |
| `influxdata` | /var/lib/influxdb2 | InfluxDB 数据持久化 |
| `hls_media` | /app/media | HLS 流媒体共享 |

---

## 🔑 默认账户

### 管理员账户

| 项目 | 值 |
|------|----|
| 用户名 | `admin` |
| 密码 | `Admin@123456` |
| 角色 | admin |

> ⚠️ **生产环境务必修改默认密码和 JWT 密钥！**

### InfluxDB 控制台

| 项目 | 值 |
|------|----|
| 用户名 | `drainage_admin` |
| 密码 | `Dr@inage_1nflux_2024!` |

---

## ❓ 常见问题

### 1. Docker 构建后端失败：`libgl1-mesa-glx` 不可用

Debian Trixie (python:3.11-slim 最新版) 中该包已被弃用。CPU 版 Dockerfile 已替换为 `libgl1 libglib2.0-0`，GPU 版 Dockerfile 也已同步修复。

### 2. 数据库连接失败：密码含特殊字符

密码中的 `@` 和 `!` 会被 URL 解析器误解析。系统在 `database.py` 中使用 `quote_plus()` 动态编码用户名和密码。Docker Compose 环境中直接使用原始密码（容器内网络不需要 URL 编码）。

### 3. Demo 模式下没有模拟设备

系统设计了降级逻辑：当 PostgreSQL 不可用或设备表为空时，API 自动返回内置的 `PRESET_DEVICES` 数据。可通过 `/api/mock/start` 启动模拟数据生成器。

### 4. 80 端口被占用

Nginx 默认映射到 8080 端口。如需修改，编辑 `docker-compose.yml` 中 nginx 服务的 `ports` 配置。

### 5. GPU 推理不可用

确保已安装 NVIDIA 驱动和 nvidia-container-toolkit，并修改 `docker-compose.yml` 使用 GPU 版 Dockerfile：

```yaml
backend:
  build:
    dockerfile: ../docker/backend/Dockerfile  # GPU 版
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
```

### 6. 前端构建 TypeScript 错误

确保 `tsconfig.node.json` 中包含 `"types": ["node"]`，且 `package.json` 的 devDependencies 中包含 `@types/node`。

---

## 📄 License

Copyright 2024 Smart City Neural Endpoints

本项目基于 [Apache License 2.0](./LICENSE) 开源。

### 使用要求

根据 Apache 2.0 许可证，使用本项目时您必须：

1. **保留版权声明** — 不得删除或修改源代码中的版权声明
2. **保留 NOTICE 文件** — 再分发时必须包含 [NOTICE](./NOTICE) 文件
3. **标注出处** — 在您的产品中显著位置展示以下文字：

```
Powered by Smart City Neural Endpoints — 智慧城市神经末梢
https://github.com/zouyuanqing/smart-city-drainage
```

4. **声明修改** — 如果您修改了源代码，必须在修改的文件中注明变更

### 标注位置建议

- Web 应用：页脚或"关于"页面
- 移动应用：启动页或设置页
- 文档：README 或首页
- API 服务：响应 Header 或文档页面

### 商业使用

商业使用完全允许，但必须遵守上述标注要求。详见 [LICENSE](./LICENSE) 文件。
