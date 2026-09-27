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
  - [Docker Compose 启动（配置/诊断）](#docker-compose-启动配置诊断)
  - [水力数字孪生演示（当前边界）](#-水力数字孪生演示当前边界)
    - [1. Compose 配置预检](#1-compose-配置预检)
    - [2. fresh-volume 诊断](#2-fresh-volume-诊断)
    - [3. 本地 HTTP 诊断（非持久化验收）](#3-本地-http-诊断非持久化验收)
  - [本地开发](#本地开发)
- [水力契约与证据索引](docs/hydraulic-twin/CONTRACT_INDEX.md)
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

### Docker Compose 启动（配置/诊断）

#### 前置要求

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) 或 Docker Engine + Docker Compose v2
- 至少 8GB 可用内存（AI 推理模型较大）
- 至少 10GB 可用磁盘空间

#### 启动基础设施 + 后端 + Nginx

根 `docker-compose.yml` 对数据库、Redis、InfluxDB、JWT RS256 key/issuer/audience 使用必填变量；**裸执行 `docker compose up -d` 不是可复现命令**。请先按下方[“Compose 配置预检”](#1-compose-配置预检)设置环境变量，再在专用 project 执行：

```bash
docker compose --profile production --profile monitoring \
  -f docker-compose.yml up --build -d
```

当前 Compose 定义的基础服务为 PostgreSQL、Redis、InfluxDB、one-shot `migrate`、backend、6 个 worker
（`simulation-worker`、`network-job-worker`、`nowcast-worker`、`outbox-relay`、`notification-sink`、`notification-worker`）与 Nginx；
`frontend` 属于 `production` profile，Prometheus/Grafana 属于 `monitoring` profile。

> ✅ **`/readyz` 的 readiness 阻断已解除。** 2026-09-28 在一台正在运行的栈上实测：
> `GET http://127.0.0.1:8000/readyz` 返回 **HTTP 200**，`ready=true`、`reason=null`，
> 且 `required_tables` / `extensions` / `hypertables` / `forecast_policy` **全部 true**，
> 5 个 worker heartbeat 全部新鲜。上一版文档写的
> "`forecast_policy` 恒 false → `/readyz` 固定 503 → backend unhealthy"**已不复现**。
> 根因在代码里也确实被改掉了：`_validate_forecast_artifacts` 的 `validate_file` 现在把
> `artifact_format` 作为**入参**接收（`backend/app/main.py:665-673`），
> 对 `forecast_rules` 那一路显式传 `None`（`:781`），不再从 rules 行读那个不存在的列。
>
> ⚠️ **但演示数据仍然为空，所以"栈起来了"≠"演示能跑通"。** 同一时刻实测：
> `hydraulic.networks` 只有 1 行且是 notification-sink 的投递测试网络
> （`code=SINK-TEST`，`current_revision_id` 为空），而 `network_revisions` /
> `network_nodes` / `pipe_segments` / `catchments` / `rainfall_scenarios` /
> `network_jobs` / `simulations` / `nowcast_runs` / `forecast_alerts` /
> `ab_comparisons` / `calibration_runs` / `observation_samples` **全部 0 行**。
> §2 的 50 年一遇、nowcast、告警、A/B、率定都还需要先有 network 与 revision。
>
> ⚠️ **上面那台栈没有起 `production` profile**，`docker ps -a` 里没有 `nginx`/`frontend`
> 容器，所以 `http://localhost:8080` 与 `http://localhost:3000` **本轮未观测到可用**。
> 要看这两个地址请按 §2 自己起 `production` profile；`depends_on` 是
> `service_healthy`（`frontend` 在 `docker-compose.yml:742-744`，
> `nginx` 在 `:690-705`），backend 起来之后它们才会启动。
>
> > 上一版文档写的"`simulation-worker` 因 `SIMULATION_SOLVER_COMMAND` 为空而 exit 3"**已不成立**：
> > 根 Compose 在 `docker-compose.yml:250` 给出了默认值
> > `${SIMULATION_SOLVER_COMMAND:-python,-m,app.services.hydraulic.solvers.cli}`。
> > 该 worker 在实测栈里是 `healthy` 的。
>
> **想在不依赖 Compose 栈的情况下看页面，请用[本地 Vite + development adapter 路径](#3-本地-http-诊断非持久化验收)**（5173 端口）。完整边界与可复制步骤见 [`docs/hydraulic-twin/operations.md`](docs/hydraulic-twin/operations.md) 的 §3.2。

#### 访问地址

| 服务 | 地址 | 说明 |
|------|------|------|
| 前端（Compose `production` profile） | http://localhost:3000 | 2026-09-28 实测那台栈**没起该 profile**，本轮未观测到可用；本地开发用 http://localhost:5173 |
| 后端 API | http://localhost:8000 | FastAPI，可用；2026-09-28 实测 `/health` 与 `/readyz` **均 200** |
| Nginx 代理 | http://localhost:8080 | 同上，本轮未观测到容器运行 |
| API 文档 | http://localhost:8000/docs | Swagger UI |
| Prometheus | http://localhost:9090 | 属于 `monitoring` profile；rules/dashboard 未挂载 |
| Grafana | http://localhost:3001 | 属于 `monitoring` profile |
| InfluxDB 控制台 | 不可从主机访问 | 根 Compose 的 `influxdb` **不发布主机端口**；需要时用 `-f docker-compose.yml -f <你的 override>` 自行加端口 |

以上端口由 `docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring -f docker-compose.yml config --format json` 的 `services[*].ports` 得出（2026-09-28 重跑，Docker 29.4.2 / Compose v5.1.3；已用 `--env-file "$CLEAN_ENV"` 排除本机未跟踪的 `.env`）。
同一份 JSON 的 `expose` 给出 worker 内部端口：`simulation-worker 8010`、`network-job-worker 8021`、
`nowcast-worker 8022`、`outbox-relay 8023`、`notification-sink 8030`、`notification-worker 8024`；
`postgres` / `redis` / `influxdb` / `migrate` 两者都没有。

#### 停止所有服务

```bash
docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring down
```

#### 重新构建镜像

```bash
# 仅重建后端
docker compose --env-file "$CLEAN_ENV" build backend

# 仅重建前端
docker compose --env-file "$CLEAN_ENV" build frontend

# 重建全部
docker compose --env-file "$CLEAN_ENV" --profile production build
```

---

## 🚿 水力数字孪生演示（当前边界）

> 2026-09-28 按 live files 核对。R0 设计冻结与 migration 静态 gate 已有单独证据，但 **G0～G6、AC-1～AC-8、真实 50 年 worker/nowcast/双 profile Compose 尚未通过**。
> 根 Compose 的 **G6 静态 config 检查（portable）本轮 exit 0**（更早一版还是 FAIL，因缺 4 个 worker，现已补齐）；
> **postgis profile 仍 FAIL**（根 Compose 无 PostGIS 镜像变体）。
> 上一版写的 live 阻断 "`forecast_policy` 恒 false → `/readyz` 固定 503"**已解除**：
> 2026-09-28 实测 `/readyz` 返回 200、`forecast_policy=true`（见“快速开始”的说明）。
> **当前的真实缺口是演示数据**：除 1 行 notification-sink 测试网络外，
> 拓扑、降雨、仿真、nowcast、告警、A/B、率定的表全是 0 行。
> 完整边界与可复制步骤见 [`docs/hydraulic-twin/operations.md`](docs/hydraulic-twin/operations.md)，规范/证据优先级见 [`CONTRACT_INDEX.md`](docs/hydraulic-twin/CONTRACT_INDEX.md)。
>
> 本节每条**结论性**的“已核对”都对应 [`implementation/delivery-docs.md`](docs/hydraulic-twin/implementation/delivery-docs.md) 里一条真实执行过的命令；没有对应命令的，本文不写通过。
>
> 反过来也要说清楚：本节和 `operations.md` 里的 `docker compose up/down -v`、`uvicorn`、
> 全部 `curl`、G0～G6/acceptance 脚本等**都是待执行步骤，本轮一条都没有运行**。
> 完整未运行清单见 `operations.md` §13。

### 1. Compose 配置预检

根 `docker-compose.yml` 有 **14 个 `${VAR:?set …}` 必填变量**。`docker compose` 会**自动读取
工作目录下的 `.env`**；本仓库根的 `.env` 被 `.gitignore:31` 忽略（`git ls-files .env` → 0 条），
会掩盖缺失的 export。要在**干净 clone** 上复现，必须用 `--env-file` 指向一份**自己拼出来的**
env 文件。

**别用空文件当 `--env-file`**：`--env-file` 会**整体替换**默认 env 解析，空文件等于一个变量都没有，
2026-09-28 实测直接 exit 1（`required variable DATABASE_URL is missing a value`）。
正确做法是复制 `.env.example` 再补齐它留空的 7 项——下面这段是本轮**实跑 exit 0** 的完整命令：

```bash
cd smart-city-drainage
export COMPOSE_PROJECT_NAME=scn-hydraulic-doc
export CLEAN_ENV=/tmp/clean.env

# 1) 以 .env.example 为底，拿到全部非必填变量的默认值
cp .env.example "$CLEAN_ENV"

# 2) 补齐 .env.example 里**留空**的 7 个必填变量（2026-09-28 实测为空的那些）。
#    REDIS_URL 必须指向 compose 网络内的主机名 `redis`，不能沿用 .env.example 的 localhost。
#    这些值只用于配置渲染，不是生产 secret。
cat >> "$CLEAN_ENV" <<'ENV'
POSTGRES_PASSWORD=doc-postgres-only
GRAFANA_ADMIN_PASSWORD=doc-grafana-only
INFLUXDB_PASSWORD=doc-influx-only
JWT_PRIVATE_KEY=doc-private-key-not-runtime-valid
JWT_PUBLIC_KEY=doc-public-key-not-runtime-valid
REDIS_PASSWORD=doc-redis-only
REDIS_URL=redis://:doc-redis-only@redis:6379/0
ENV

# 3) 预检：exit 0 才继续
docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring \
  -f docker-compose.yml config --quiet
echo "clean-env exit=$?"

docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring \
  -f docker-compose.yml config --services
```

`GRAFANA_ADMIN_PASSWORD` 特别容易漏：它属于 `grafana` 服务（`docker-compose.yml:778`），
但因 Compose 对**整个文件**做插值，**不带 `--profile monitoring` 也会因它 exit 1**。

上面整段 2026-09-28 实跑 `exit=0`；把第 2 步整段删掉重跑则 exit 1 并指名缺失变量
（按上面那条命令实测，先报 `POSTGRES_PASSWORD`；补一项再报下一项，缺哪个取决于补到哪一步）。
两种情况的原始输出见
[`implementation/delivery-docs.md`](docs/hydraulic-twin/implementation/delivery-docs.md) §2.3。

本轮 `config --quiet` exit 0（Docker 29.4.2 / Compose v5.1.3）。
`config --services` 的**输出顺序随 Compose 版本变化，不是契约**——请按下表的集合核对：

| Profile | Services（集合，无序） | 数量 |
|---|---|---|
| 默认（无 `--profile`） | `postgres`、`redis`、`influxdb`、`migrate`、`backend`、`simulation-worker`、`network-job-worker`、`nowcast-worker`、`outbox-relay`、`notification-sink`、`notification-worker`、`nginx` | 12 |
| `production` | 默认集合 + `frontend` | 13 |
| `monitoring` | 默认集合 + `prometheus`、`grafana` | 14 |
| 两者都开 | 上面全部 | **15** |

> ⚠️ 这张表与前几版文档都不同（曾写过 7/8/10、11/12/13/14）。本轮新增了
> `notification-sink`（`docker-compose.yml:513-567`，`expose: 8030`）。
> `G6_REQUIRED_SERVICES` 只要求 14 个（`scripts/verify_hydraulic_stack.py:44-58`），
> `notification-sink` 不在其中，属"多于要求"而非缺口。
> 根 Compose 是未提交的工作树改动（2026-09-28 工作树 802 行，HEAD `ca99e048` 只有 190 行）。
> 引用前请按上面的命令重取。

配置渲染不是 live G6/AC-8。`scripts/verify_hydraulic_stack.py --gate g6 --mode config --profile portable`
在 2026-09-28 已 exit 0 / `static_failures: []`；换 `--profile postgis` 仍 FAIL
（根 Compose 的 `postgres` 固定用 `timescale/timescaledb@sha256:3f770468…`，无 PostGIS 变体）。

### 2. fresh-volume 诊断

> `down -v` 会删除本 Compose project 的卷，只在专用演示 project 使用。

```bash
# 承接 §1 导出的 CLEAN_ENV；带上 --env-file 才能排除本机未跟踪的 .env
docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring \
  -f docker-compose.yml down -v --remove-orphans
docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring \
  -f docker-compose.yml up --build -d
docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring \
  -f docker-compose.yml ps --all

curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/readyz
```

**本轮未运行**上面这组 `up/down -v`——它会删卷。2026-09-28 只在**另一台已存在的栈**上
只读观测到 `docker ps` 的 12 个容器全部 healthy、`migrate` Exited(0)，`/health` 与 `/readyz` 均 200。
那次观测对应的栈**没有起 `production` profile**，所以 `nginx`/`frontend` 容器不存在。
自己跑完上面这组后，先看 `ps --all` 里 `nginx`/`frontend` 是否从 `Created` 变为 `Running`；
它们的 `depends_on` 是 `service_healthy`（`frontend` 在 `docker-compose.yml:742-744`，
`nginx` 在 `:690-705`），backend healthy 之后才会启动。

不要把容器启动、`/health` 200 或 `/readyz` 200 写成验收通过——这三样都不含任何求解、
nowcast 或告警执行。发布候选必须由仓库根的 `scripts/verify_hydraulic_stack.py --gate g6`
分别运行 postgis/portable——2026-09-28 实测：`--profile portable` exit 0，
`--profile postgis` exit 1（根 Compose 没有 PostGIS 镜像变体）。

### 3. 本地 HTTP 诊断（非持久化验收）

在 Python 3.11 环境启动 development adapter：

```bash
cd smart-city-drainage/backend
export DEPLOYMENT_ENVIRONMENT=development
export AUTH_FALLBACK_ADMIN_ENABLED=true
export SCN_ENV_FILE=.env.example
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开终端：

```bash
export BASE=http://127.0.0.1:8000
export TOKEN="$(
  curl -fsS -X POST "$BASE/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"Admin@123456"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
)"

NETWORK_JSON="$(
  curl -fsS -X POST "$BASE/api/networks" \
    -H "Authorization: Bearer $TOKEN" \
    -H 'Idempotency-Key: readme-network-create-20260926' \
    -H 'Content-Type: application/json' \
    -d '{"code":"SH-README-20260926","name":"水力演示网络","status":"active","crs":"EPSG:4326","spatial_backend":"portable"}'
)"
printf '%s\n' "$NETWORK_JSON"
```

该 adapter 重启后丢数据，seed/import 会停在 `queued`；它只用于检查路由、RBAC、幂等和 payload。50 年 289 点生成、simulation submit、nowcast/告警/SSE、A/B、率定、备份恢复和回滚的完整命令见运维手册，并明确标出当前阻断。

> ⚠️ 上面这组 `admin/Admin@123456` 凭据依赖 `AUTH_FALLBACK_ADMIN_ENABLED=true`，那是
> **development adapter 专用**开关。2026-09-28 在上面那台 Compose 栈上用同一组凭据登录，
> 得到 `HTTP 401 {"code":"AUTH_REQUIRED"}`——这是预期行为，不是文档缺陷。
> Compose 栈要走真实的 RS256 签发流程，不要靠 fallback 账号。

**本地前端（替代 Compose 容器路径）**：

```bash
cd smart-city-drainage/frontend
nvm install 18.20.8 && nvm use 18.20.8   # 版本取自 frontend/.node-version
npm ci
npm run dev      # http://localhost:5173，已配置 /api 代理到 8000
```

前端真实水力页面路由是 `/simulation`（`frontend/src/App.tsx:77-84`），登录 http://localhost:5173 后访问 http://localhost:5173/simulation。development adapter 下它只会拿到 `queued`/`not_ready`，用于看 UI 与请求链路，不能作为 AC-3/AC-4 证据。

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

#### 0. 准备环境变量

```bash
cp .env.example .env   # 复制到项目根目录
```

Compose 还要求通过 secret manager/环境注入 `POSTGRES_PASSWORD`、`REDIS_PASSWORD`、`INFLUXDB_PASSWORD`、`INFLUXDB_TOKEN`，并让 `DATABASE_URL*`/`REDIS_URL` 使用与容器认证一致的编码值；`.env.example` 中的 `YOUR_PASSWORD` 只是占位符，不能直接启动。

`.env` 的加载与当前工作目录无关：优先 `SCN_ENV_FILE`，其次项目根 `.env`，
最后 `backend/.env`。因此在 `backend/` 下启动 uvicorn 也会读取根目录配置。

#### 1. 启动基础设施

```bash
docker compose --env-file .env up -d postgres redis influxdb
```

#### 2. 启动后端

```bash
cd backend

# 安装依赖（hash lock，Python 3.11；与 Docker 镜像、CI 使用同一份 lock）
pip install --require-hashes -r requirements-py311.lock        # CPU 模式（推荐无 GPU 环境）
# GPU 模式
pip install --require-hashes -r requirements-py311-gpu.lock
# 最小纯 Python 降级（无 torch/CUDA/onnxruntime，EPA SWMM 仍为外部可执行）
pip install --require-hashes -r requirements-hydraulic-py311.lock

# 数据库迁移
alembic upgrade head

# 启动开发服务器
uvicorn app.main:app --reload --port 8000
```

> `requirements*.txt` 是人工维护的输入，`requirements-*-py311.lock` 才是实际安装
> 依据（含 transitive 依赖与 SHA-256）。修改顶层依赖后需重新生成 lock。

**解释器矩阵**：`requires-python` 与三个 lock 都锁定 Python 3.11（仓库根
`.python-version` 也是 3.11，Docker/CI 用 3.11）。如果必须用本机更新的解释器
（例如 Windows 上的 3.13/3.14）安装，requirements 文件里带 marker 的备用 pin
会保证全程使用 wheel，不会回退到需要 `pg_config`/编译器的源码构建：

| 解释器 | 安装方式 | YOLO/ONNX 推理 |
|--------|----------|-----------------|
| 3.11（契约） | `pip install --require-hashes -r requirements-py311.lock` | 可用 |
| ≥ 3.13 | `pip install -r requirements-cpu.txt` | 不可用（无 cp313+ wheel），API 与纯 Python 水力路径正常 |

#### 3. 启动前端

```bash
cd frontend

# 切换到已验证的工具链（Node 18.20.8 / npm 10.8.2，见 frontend/.node-version）
nvm install 18.20.8 && nvm use 18.20.8    # 或 fnm install 18.20.8 && fnm use 18.20.8

# 安装依赖：只用 npm ci（严格按 package-lock.json），不要用 npm install
npm ci

# 启动开发服务器
npm run dev
```

`frontend/.npmrc` 设置了 `engine-strict=true`，并将 npm registry、lockfile 版本和
依赖保存策略固定下来：`registry=https://registry.npmjs.org/`、
`lockfile-version=3`、`save-exact=true`。`package.json` 的 `engines` 精确锁定
Node `18.20.8` / npm `10.8.2`（与 `.node-version` 一致）；不匹配时 npm 直接报错，
不会只打印 `EBADENGINE` 警告后继续安装。

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
│   ├── requirements.txt              # Python 依赖输入（GPU）
│   ├── requirements-cpu.txt          # Python 依赖输入（CPU only）
│   ├── requirements-hydraulic.txt    # Python 依赖输入（最小纯 Python 降级）
│   ├── requirements-py311.lock       # 实际安装用 hash lock（CPU）
│   ├── requirements-py311-gpu.lock   # 实际安装用 hash lock（GPU）
│   └── requirements-hydraulic-py311.lock  # 实际安装用 hash lock（降级）
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
| `JWT_SECRET_KEY` | scn-jwt-secret-key-... | HS256/HS384/HS512 签名密钥（**生产环境务必修改**） |
| `JWT_PRIVATE_KEY` | （空） | RS256/ES256 等非对称算法的 PEM 私钥，仅通过 secret manager/挂载注入 |
| `JWT_PUBLIC_KEY` | （空） | 非对称 token 验证用 PEM 公钥 |
| `JWT_ALGORITHM` | HS256 | 签名算法，必须包含在 `JWT_ALGORITHMS` 中 |
| `JWT_ALGORITHMS` | ["HS256", "RS256"] | 签发与验证共用的算法 allowlist；未知算法/`none` 会被拒绝 |
| `JWT_ISSUER` | （空） | 写入 `iss` 并在验证时校验；production 必填 |
| `JWT_AUDIENCE` | （空） | 写入 `aud` 并在验证时校验；production 必填 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 1440 | Token 过期时间（分钟） |
| `AUTH_FALLBACK_ADMIN_ENABLED` | false | 固定演示管理员开关，仅 development/test 生效 |

> `JWT_ALGORITHMS` 才是唯一的算法来源，`JWT_ALGORITHM` 必须落在其中，否则启动即失败。
> RS256 签发使用 `JWT_PRIVATE_KEY`、验证使用 `JWT_PUBLIC_KEY`，不会把
> `JWT_SECRET_KEY` 当作 PEM；production 的非对称签名缺少任一密钥都会拒绝启动。

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

业务端点前缀为 `/api`，认证端点需在 Header 中携带 `Authorization: Bearer <token>`。系统 liveness/readiness/metrics 位于根路径：`/health`、`/readyz`、`/metrics`。

#### 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 用户登录，返回 JWT Token |
| GET | `/api/auth/me` | 获取当前用户信息 🔒 |

#### 系统

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 进程 liveness（无条件 200） |
| GET | `/readyz` | DB/schema/worker/artifact readiness（失败 503） |
| GET | `/metrics` | Prometheus 指标 |
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

#### 水力数字孪生（hydraulic）

写操作需要 `operator`/`admin` 与 8～128 字符 `Idempotency-Key`；`seed` 与 network archive 仅 `admin`。完整路由表、角色矩阵与 live 实现行号见 [`CONTRACT_INDEX.md`](docs/hydraulic-twin/CONTRACT_INDEX.md) §3；可复制调用步骤见 [`operations.md`](docs/hydraulic-twin/operations.md) §4～§8。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST/GET | `/api/networks` | 创建 / 列表 🔒 / 公开 |
| POST | `/api/networks/import` | 提交 import job（202，停在 `queued`）🔒 |
| GET | `/api/networks/{network_id}` | network + current revision |
| DELETE | `/api/networks/{network_id}` | 幂等 archive（非物理删除）🔒 |
| GET | `/api/networks/{network_id}/map-data` | 节点/管段/汇水区地图数据 |
| GET | `/api/networks/{network_id}/target-manifest` | revision 级 immutable target manifest |
| POST | `/api/networks/{network_id}/validate` | 提交 validation job（202，停在 `queued`）🔒 |
| POST | `/api/networks/{network_id}/seed` | 提交 seed job（202，停在 `queued`）🔒 |
| GET | `/api/network-jobs/{job_id}` | job 状态 |
| GET | `/api/validation-runs/{validation_id}` | 校验报告 |
| POST/GET | `/api/rainfall-scenarios` | 降雨情景（1/2/3/5/10/20/50/100 年 + custom）🔒 / 公开 |
| POST | `/api/rainfall-scenarios/{scenario_id}/copy` | 复制情景 🔒 |
| POST/GET | `/api/simulations` | 提交 / 列表 🔒 / 公开 |
| GET | `/api/simulations/{simulation_id}` | 状态、进度、attempt |
| POST | `/api/simulations/{simulation_id}/cancel` | 幂等取消 🔒 |
| GET | `/api/simulations/{simulation_id}/result` | 结果摘要（仅 completed） |
| GET | `/api/simulations/{simulation_id}/frames[/{frame_index}]` | 帧列表 / 单帧（仅 completed） |
| GET | `/api/simulations/{simulation_id}/observed-comparison` | 实测比对（当前固定 `not_ready`） |
| GET | `/api/simulations/{baseline_id}/compare/{candidate_id}` | A/B 对比 |
| POST/GET | `/api/calibrations[/{calibration_id}]` | 率定提交 / 状态 🔒 |
| GET | `/api/forecast/latest\|horizon\|risk-ranking\|alerts` | 预报查询（`network_id` 必填） |
| POST | `/api/forecast/alerts/{alert_id}/transitions` | 人工 `acknowledge`/`resolve` 🔒 |
| GET | `/api/forecast/events` | forecast SSE（JWT 只能放 `Authorization` header） |

> ⚠️ 当前没有 `POST /api/nowcast`：nowcast 由目标 worker/scheduler 产生，根 Compose 尚无该服务。production 下这些端点在 durable store / forecast policy 未就绪时按契约 fail closed（503 `DURABLE_STORE_UNAVAILABLE` / `FORECAST_POLICY_UNAVAILABLE`）。

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
# 先准备完整 .env / secret 注入，再渲染检查
docker compose --env-file .env config --quiet

# 基础启动（基础设施 + 后端 + Nginx）
docker compose --env-file .env up --build -d

# 包含前端（生产模式；注意当前 backend/worker healthcheck 下不会真的起来，见“快速开始”）
docker compose --env-file .env --profile production up --build -d

# 包含监控
docker compose --env-file .env --profile monitoring up --build -d

# 使用 GPU 后端
# 修改 docker-compose.yml 中 backend 的 dockerfile 为 ../docker/backend/Dockerfile
# 并添加 NVIDIA runtime 配置
```

### 端口映射

> 以下是根 `docker-compose.yml` **当前真实发布**的主机端口（2026-09-27 核对）。
> `postgres`、`redis`、`influxdb` **不发布主机端口**，只在 `scn-network` 内部可达。

| 服务 | 容器端口 | 主机端口 | 说明 |
|------|----------|----------|------|
| Backend | 8000 | 8000 | API 服务 |
| Nginx | 8080 | 8080 | 反向代理 + HLS |
| Frontend | 8080 | 3000 | 前端静态文件（`production` profile） |
| Prometheus | 9090 | 9090 | `monitoring` profile |
| Grafana | 3000 | 3001 | `monitoring` profile |
| PostgreSQL | 5432 | 无 | 仅容器网络内 |
| Redis | 6379 | 无 | 仅容器网络内 |
| InfluxDB | 8086 | 无 | 仅容器网络内 |

5 个 worker 只用 `expose`（容器间可达），不发布主机端口：
`simulation-worker 8010`、`network-job-worker 8021`、`nowcast-worker 8022`、
`outbox-relay 8023`、`notification-worker 8024`。
`migrate` 是一次性服务，也没有端口。

本地显式校验数据库 profile 时才用 `docker/postgres/compose.yml` 的
`127.0.0.1:55432`（postgis）与 `127.0.0.1:55433`（portable），见该目录的 README。

### 数据卷

| 卷名 | 挂载点 | 说明 |
|------|--------|------|
| `pgdata` | /var/lib/postgresql/data | PostgreSQL 数据持久化 |
| `redisdata` | /data | Redis 数据持久化 |
| `influxdata` | /var/lib/influxdb2 | InfluxDB 数据持久化 |
| `hls_media` | /app/media | HLS 流媒体共享（backend 写、nginx 只读挂） |
| `backend_logs` | /app/logs | backend 日志 |
| `worker_logs` | /app/logs | 5 个 worker 共享日志卷 |
| `simulation_sandbox` | /var/lib/smart-drainage/sandbox | 仿真沙箱（simulation-worker 与 nowcast-worker 共用） |
| `simulation_results` | /var/lib/smart-drainage/results | 仿真导出 |
| `network_job_import` | /var/lib/smart-drainage/import | import/validate/seed 的 job 载荷暂存 |

`monitoring` profile 另有 `prometheusdata`（`/prometheus`）与 `grafanadata`（`/var/lib/grafana`）。

---

## 🔑 默认账户

### 管理员账户

| 项目 | 值 |
|------|----|
| 用户名 | `admin` |
| 密码 | `Admin@123456` |
| 角色 | admin |

> ⚠️ 该账户来自 `backend/alembic/versions/0001_initial.py` 的初始数据。
> 登录接口仅在 `AUTH_FALLBACK_ADMIN_ENABLED=true` 且 `DEPLOYMENT_ENVIRONMENT`
> 为 `development` 或 `test` 时才接受这组固定凭据；数据库不可用时同样受该
> 开关约束，staging/production 下永远返回 401/503 而不是签发 admin token。
> 生产环境请立即修改默认密码和 JWT 密钥。

### InfluxDB 控制台

| 项目 | 值 |
|------|----|
| 用户名 | `drainage_admin` |
| 密码 | 无默认值；使用 `INFLUXDB_PASSWORD` 注入 |

> 根 Compose 的 `influxdb` 服务不发布主机端口，因此该控制台默认**不能**从宿主机浏览器访问。
> 需要时用一个 Compose override 追加端口映射，不要把端口直接写回根 `docker-compose.yml`。

---

## ❓ 常见问题

### 0. Compose 起来了，但 `localhost:3000` / `localhost:8080` 打不开

先确认一件事：**这两个地址需要 `--profile production`**。只跑默认 profile 时，
`docker compose ps --all` 里根本不会有 `nginx`/`frontend` 容器——2026-09-28 观测到的那台栈
就是这样（backend 与 6 个 worker 全 healthy，但这两个容器不存在）。

正确起法：

```bash
docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring \
  -f docker-compose.yml up --build -d
```

如果 `--profile production` 已经带了还打不开，再按顺序查：

- `docker compose ps --all` 里 `backend` 是不是 `healthy`。它的 healthcheck 打
  `/readyz`（`docker-compose.yml:180-185`，URL 字面量在 `:181`），而 `/readyz` 要求
  `required_tables`、`extensions`、`hypertables`、`forecast_policy` 与 5 个 worker heartbeat
  全部有效（`backend/app/main.py:1392`，`required` 判定在 `:917-926`），否则 503；
- `nginx` 依赖 `backend` + 5 个 worker 全部 healthy（`docker-compose.yml:690-705`）；
- `frontend` 依赖 `backend` healthy（`docker-compose.yml:742-744`）；
- `docker compose logs backend` 与 `docker compose logs simulation-worker`。

> 上一版 FAQ 把根因写成"`forecast_policy` 因 `NoSuchColumnError` 恒 false"。**该缺陷已不存在**：
> 2026-09-28 实测 `/readyz` 返回 200、`forecast_policy=true`。若你又看到 `forecast_policy=false`，
> 先 `docker compose build backend && docker compose up -d backend`——多半是旧镜像。
> 精确定位命令见 [`docs/hydraulic-twin/operations.md`](docs/hydraulic-twin/operations.md) §3.2.1 与 §11。

要立刻看页面请用本地 Vite（`cd frontend && npm ci && npm run dev`，http://localhost:5173）。

### 0.1 `docker compose up -d` 报缺少变量

上面那 13 个都是必填（`docker-compose.yml` 里的 `${VAR:?...}`），加上 `GRAFANA_ADMIN_PASSWORD`
共 **14 个**。`GRAFANA_ADMIN_PASSWORD` 属于 `grafana` 服务（`docker-compose.yml:778`），
但**即使不加 `--profile monitoring` 也会因为它 exit 1**，因为 Compose 是对**整个文件**做变量插值，
而不是只对被启用的 profile 做插值。

⚠️ **`cp .env.example .env` 不够。** 2026-09-28 实测：`.env.example` 里这 14 个必填变量中有
**6 个是空值**——`POSTGRES_PASSWORD`、`GRAFANA_ADMIN_PASSWORD`、`INFLUXDB_PASSWORD`、
`JWT_PRIVATE_KEY`、`JWT_PUBLIC_KEY`、`REDIS_PASSWORD`。照抄之后 `config` 仍 exit 1。
另外 `.env.example` 的 `REDIS_URL` 指向 `localhost`，容器内要指向 `redis` 主机名。
可复制的补全步骤见[“Compose 配置预检”](#1-compose-配置预检) 的 `cat >> "$CLEAN_ENV"` 段。

### 0.2 Windows 上安装依赖时报 `pg_config executable not found` / `spawn npm ENOENT`

- `pg_config`：说明用了非契约解释器（仓库只支持 Python 3.11 的 lock）。请用
  Python 3.11 安装 `requirements-py311.lock`；若必须用 3.13/3.14，请改用带
  marker 的 `requirements*.txt`，它们会解析到有 wheel 的版本。
- `spawn npm ENOENT`：Windows 上 `npm` 是 `npm.cmd` shim，Node 直接 spawn
  `npm` 会解析到无扩展名的 shell 脚本。脚本里请用
  `spawnSync('npm.cmd', args, { shell: true })` 或
  `spawnSync(process.env.ComSpec, ['/d','/s','/c','npm', ...args])`。

### 0.3 前端 `npm ci` 报 `npm error code EBADENGINE`

```text
npm error notsup Required: {"node":"18.20.8","npm":"10.8.2"}
npm error notsup Actual:   {"node":"v25.x","npm":"11.x"}
```

`frontend/.npmrc` 的 `engine-strict=true` 会让超出 `engines` 的 Node/npm 在**安装阶段**失败，
而不是只警告后继续。`engines` 来自 `frontend/package.json`（`{"node":"18.20.8","npm":"10.8.2"}`），
与 `frontend/.node-version` 的 `18.20.8` 一致。

> **本轮只验证了机制，没有验证本仓库的 `npm ci` / `npm test` / `npm run build`。**
> 机制是在 `/tmp` 的**合成包**上实跑的（`package.json` 声明同样的 `engines`，无任何依赖）：
> `engine-strict=true` 时 exit 1 + `npm error code EBADENGINE`；删掉该行后只 `npm warn EBADENGINE`、exit 0。
> 这证明的是"该行会改变行为"，**不构成**"仓库 `frontend/` 在任一版本下能装上/能构建"的证据。

| Node / npm | `npm ci` | `npm test` | `npm run build` |
|------------|----------|------------|-----------------|
| 18.20.8 / 10.8.2（`frontend/.node-version`，CI/Docker 使用） | NOT RUN | NOT RUN | NOT RUN |
| 25.9.0 / 11.12.1（本次核对机 `node -v` / `npm -v`） | NOT RUN | NOT RUN | NOT RUN |

> **上表六个格子全部是 `NOT RUN`**——本轮没有在 `smart-city-drainage/frontend` 执行过
> `npm ci` / `npm test` / `npm run build`，任何一格都不是实测结论。
> 换到 18.20.8 之后会怎样属于目标环境行为；换到 25.9.0 会怎样属于本机行为，两者在本文都**未观测**。
> CI 用 `actions/setup-node` 的 `node-version-file: frontend/.node-version` 跑
> `npm --prefix frontend ci` 与 `npm --prefix frontend test`（`.github/workflows/ci.yml:90,129,131`），
> 那是 CI 侧的证据，不在本文核对范围；需要引用请查具体那次 CI run，不要引用本表。
>
> `npm test` = `vitest run`，而 `frontend/vitest.config.ts` 的 `test` 段**没有** `include`，
> 走 vitest 默认 glob，因此会收集 `frontend/src` 下**全部 32 个**测试文件
> （`find frontend/src -name "*.test.*" -o -name "*.spec.*" | wc -l` → `32`）。
> 任何"几个文件、几个用例通过"的数字都不能由 `package.json` 推出，必须实跑。

处理方式（版本以 `frontend/.node-version` 为准）：

```bash
node -v && npm -v                 # 先确认当前版本
nvm install 18.20.8 && nvm use 18.20.8    # nvm-windows；版本取自 frontend/.node-version
# fnm/asdf 用户：fnm install 18.20.8 && fnm use 18.20.8（fnm/asdf 读 .node-version）
# 没有版本管理器时：安装 Node 18.20.8（官方 zip / winget install CoreyButler.NVMforWindows
# 或 scoop install nvm），然后重新打开终端再执行 npm ci
```

> 无参 `nvm use` 只在 nvm-windows 的“已安装过该版本且已设为默认”时才等价于本行。
> 仓库里只有 `frontend/.node-version`、**没有** `.nvmrc`：nvm-windows 读 `.nvmrc`、
> fnm/asdf 读 `.node-version`，两者不等价，所以显式写出版本号更稳。

### 1. Docker 构建后端失败：`libgl1-mesa-glx` 不可用

Debian Trixie (python:3.11-slim 最新版) 中该包已被弃用。CPU 版 Dockerfile 已替换为 `libgl1 libglib2.0-0`，GPU 版 Dockerfile 也已同步修复。

### 2. 数据库连接失败：密码含特殊字符

密码中的 `@` 和 `!` 会被 URL 解析器误解析。系统在 `database.py` 中使用 `quote_plus()` 动态编码用户名和密码。Docker Compose 环境中直接使用原始密码（容器内网络不需要 URL 编码）。

### 3. Demo 模式下没有模拟设备

系统设计了降级逻辑：当 PostgreSQL 不可用或设备表为空时，API 自动返回内置的 `PRESET_DEVICES` 数据。可通过 `/api/mock/start` 启动模拟数据生成器。

### 4. 80 端口被占用

Nginx 的容器监听端口是 8080、主机映射到 8080（`docker/nginx/conf.d/default.conf:10`）。
如需修改，编辑 `docker-compose.yml` 中 nginx 服务的 `ports` 配置。

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
