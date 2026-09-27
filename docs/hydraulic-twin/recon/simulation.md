# 水力仿真与队列现状侦察

> 侦察对象：`smart-city-drainage`（分支 `main`，基线提交 `ca99e04`）
>
> 侦察日期：2026-09-24
>
> 目的：为 PRD 的管网数据层、降雨、仿真、预测告警、API、前端和部署约束建立“现状—可复用—缺口”基线。本文只记录本次实际读取的代码、配置和命令结果；没有把 README/CODE_WIKI 中的产品性描述当作实现事实。

## 1. 结论先行

1. **当前仓库没有水力仿真实现。** 在 `backend/app`、`backend/alembic`、`backend/tests`、`frontend/src`、`docker` 和 README 中搜索 `hydraulic|simulation|swmm|rainfall|forecast|nowcast|solver|network_nodes|pipe_segments|catchments`，除通用的 `Worker`/SSE/视频流词汇外没有命中；Git 跟踪文件中也没有仿真/水力文件名。现有系统是“设备监测 + 传感器时序 + YOLO 视频推理 + 告警”平台，而不是排水管网数字孪生。
2. **可复用的异步生命周期模式存在，但没有任务队列。** `InferenceWorker`/`WorkerPool` 提供 `start/stop/create_task/status`，`StreamService` 提供子进程、健康检查、指数退避和清理；这些可以作为水力 worker 的接口/生命周期参考，但不能直接满足持久化、并发 2、幂等、超时、取消、重试和崩溃恢复。
3. **现有 Redis 是 Pub/Sub + 简单 KV，不是队列。** `RedisClient` 有 `publish/subscribe/get/set/delete`，没有任务 claim、租约、ack、重试队列或死信；SSE 的历史仅在进程内存中保留 1000 条，进程崩溃即丢失。
4. **传感器来源目前是 InfluxDB 查询 + 模拟生成器回退。** 有液位、流量、pH、温度、湿度、电量、信号等字段，但没有 `RainfallSource`、管网节点/管段映射、观测同化接口、率定参数或 RMSE/NSE 计算。
5. **持久化存在明显初始化漂移。** `docker/init-db.sql` 创建 `sensor_readings` TimescaleDB hypertable，但 Alembic 初始迁移和 SQLAlchemy ORM 都没有该表；因此 `alembic upgrade head` 与 Docker 初始化 SQL 不会得到同一 schema。
6. **没有 SWMM/纯 Python 水力求解器依赖或 Windows 可执行文件。** 依赖声明没有 `swmm-toolkit`、`pyswmm`、`swmm5`、`scipy`（`scipy` 仅在本机环境偶然可见，未声明）；本机 `where swmm5` 未找到，FFmpeg、Node、Docker 可用。
7. **基线检查不是全绿。** 前端 build 和 lint 通过；前端 Vitest 8 项中 6 通过、2 失败；后端 pytest 在收集阶段因缺少 `pytest_asyncio` 失败，导入 FastAPI 又因缺少 `prometheus_fastapi_instrumentator` 失败。Docker Compose 配置可解析，但未启动全栈。

## 2. 侦察方法与可复核搜索

### 2.1 文件与分支

- 根目录：`C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage`
- `git log -1 --oneline`：`ca99e04 chore: remove stray isort diff log`
- `git status --short --branch`：主分支与 `origin/main` 对齐；本次测试产生了未跟踪的 `.coverage`/报告类文件，未修改业务源码。
- 当前未发现 `.codegraph/` 索引，因此本报告使用实际文件读取和精确文本搜索，而不是 CodeGraph 图结果。

### 2.2 关键搜索命令

```text
rg -n -i 'hydraulic|simulation|swmm|rainfall|forecast|nowcast|solver|network_nodes|pipe_segments|catchments' backend/app backend/alembic backend/tests frontend/src docker README.md CODE_WIKI.md
```

结果：没有水力/仿真/降雨/预报实现命中；后续宽搜索只命中了 `WorkerPool`、`asyncio.Queue`、SSE 和视频 worker 等既有功能。

```text
git ls-files | rg -i 'simulation|hydraulic|swmm|rainfall|network|queue|forecast'
```

结果：没有跟踪的仿真/水力/队列文件。

```text
rg -n --glob '*.py' 'celery|arq|rq|dramatiq|backgroundtasks|ProcessPool|ThreadPool|Executor|queue'
```

结果：没有任务队列框架或进程池实现；只有 `asyncio.Queue`（SSE 客户端队列）和 `asyncio.create_task`（既有后台协程）。

## 3. 现有架构与可复用模式

### 3.1 现有 API、后台协程和数据流

| 层 | 实际实现 | 可复用价值 | 不能直接复用的原因 |
|---|---|---|---|
| FastAPI 生命周期 | `backend/app/main.py:101-197` 启动模型、数据库、Redis、InfluxDB、健康监控和模拟数据；关闭时停止并释放资源 | 统一启动/关闭钩子，可挂接仿真 worker 生命周期 | 当前启动的是 YOLO/视频服务，没有 solver 或任务恢复步骤 |
| 数据库会话 | `backend/app/core/database.py:57-129` 使用 async SQLAlchemy engine/session；`137-142` 调 `Base.metadata.create_all` | 可复用的 async session 模式 | `create_all` 只覆盖 ORM 已声明实体；当前 ORM 没有 `sensor_readings` 或仿真实体（见 §5） |
| Redis 通信 | `backend/app/core/redis_client.py:24-167` 提供连接、Pub/Sub、简单 KV | 可复用跨服务事件通知和短期状态 | 没有 list/stream/ack/claim/lease/重试语义 |
| SSE | `backend/app/services/sse_manager.py:32-120` 为每个客户端建立有界 `asyncio.Queue`；`142-189` 做事件生成、心跳和有限重放 | 可复用进度/结果事件 fan-out、`Last-Event-ID` 交互形态 | `_MAX_HISTORY=1000` 在内存；事件 ID 仅进程内；队列满直接断开客户端（`103-120`） |
| 前端 SSE | `frontend/src/hooks/useSSE.ts:28-30,47-107` 指数退避重连并监听 `sensors`/`alerts` | 可复用重连和事件订阅模式 | 没有仿真进度、取消、预测事件类型 |
| 监控 | `backend/app/main.py:214-218` 暴露 Prometheus `/metrics`；`docker/prometheus/prometheus.yml:4-9` 抓取后端 | 可扩展任务时长、失败率、队列深度指标 | 当前没有仿真任务指标或 `simulation_id` 结构化日志 |

### 3.2 视频流/进程监督模式

`StreamService` 是当前最接近“长任务 worker”的实现：

- `backend/app/services/stream_service.py:50-67` 以内存字典保存 `StreamProcess`，保存进程句柄、启动时间、重启次数、健康状态和错误。
- `71-123` 启动 FFmpeg 后保存 `subprocess.Popen`，并尝试挂接 `WorkerPool`。
- `125-165` 停止进程、等待 5 秒、必要时 kill，并删除 HLS 临时目录。
- `169-209` 启动周期健康检查，检查进程存活和播放列表更新时间。
- `211-249` 以指数退避重启，单次延迟上限 `MAX_RESTART_DELAY=60`（`61-62`）。
- `252-327` 组装 FFmpeg 参数并通过 `subprocess.Popen` 启动；`303-310` 对非 Windows 使用 `os.setsid`，Windows 走 `None`。

**可复用建议（仅架构模式，不是已实现能力）：** 水力 worker 可以沿用“显式 start/stop/status、健康检查、失败重启、临时目录清理、取消信号”接口；但必须把状态从内存迁移到数据库/Redis 任务记录，不能把 `StreamService` 的进程表直接当作仿真任务表。

### 3.3 推理 Worker/WorkerPool 模式

- `backend/app/workers/inference_worker.py:44-100` 的 `InferenceWorker` 持有运行标志、`asyncio.Task`、统计计数和 `stop()` 取消逻辑。
- `102-150` 的循环对视频流读取失败做指数退避（1、2、4…最大 30 秒），异常后继续运行。
- `247-298` 的 `WorkerPool` 用 `dict[str, InferenceWorker]` 管理 worker，提供 `add_worker`、`remove_worker`、`stop_all`、`get_status`。
- `backend/app/workers/__init__.py:1-9` 导出单例 `worker_pool`。

**可复用部分：** worker 对象的 `start/stop/status` 形态、任务取消、计数器和单例注册方式。

**不能直接复用的部分：**

- 任务只在当前 FastAPI 进程内，进程崩溃后没有恢复或持久化状态。
- 没有并发槽位/信号量；`WorkerPool` 没有最大 worker 数、排队长度、租约或幂等键。
- 这里的“retry”仅是视频流重连退避，不是 PRD 要求的“最多两次任务重试”。
- `backend/app/services/stream_service.py:113-120`、`150-152`、`165` 以及 `backend/app/api/routes.py:362-384` 对异步 `add_worker/remove_worker/stop_all` 有未 `await` 的调用；照搬到仿真 worker 会产生协程未执行和状态假象。
- `WorkerPool.get_status()` 返回 `running`（`backend/app/workers/inference_worker.py:279-289`），而 `StreamService.get_stream_status()` 读取 `is_running`（`backend/app/services/stream_service.py:331-347`），已有字段契约不一致。

### 3.4 SSE 事件模式

后端 `SSEManager.broadcast()` 在 `backend/app/services/sse_manager.py:90-120` 为每个客户端 `put_nowait`，队列上限为 256（`52-71`）；队列满时记录告警并断开，而不是持久化或重试。`142-189` 支持 `Last-Event-ID` 在当前进程历史中重放。

这可以复用来推送 `simulation.progress`、`simulation.completed`、`forecast.created` 等事件，但需要先定义：

- 事件是否允许丢失、客户端断线后从哪里重放；
- 进度事件是否只更新 Redis，还是同时写任务表；
- 多 worker/多 API 进程下事件 ID 和顺序如何保持；
- 仿真取消/超时事件与普通日志事件的契约。

## 4. 传感器来源与同化现状

### 4.1 当前来源

1. **InfluxDB 服务**：`backend/app/services/influxdb_service.py:12-59` 管理连接；`70-124` 写入 `sensor_readings` measurement，字段包括液位、流量、温度、电量、信号，并可选 pH/湿度；`126-212` 查询最新值和按时间窗口聚合历史值。
2. **TimescaleDB 初始化表**：`docker/init-db.sql:100-118` 定义 `sensor_readings`，包含 `time`、`device_id`、液位、流量、pH、浊度、温度、湿度、电池电压、信号和 `extra`，并调用 `create_hypertable`、建立 `(device_id,time)` 索引。
3. **模拟源**：`backend/app/services/mock_data_generator.py:23-104` 定义 8 个上海设备；`107-229` 的 `MockDataGenerator` 生成液位、流量、pH、温度、湿度、电量、信号读数，并在 `161-175` 尝试写 InfluxDB、广播 SSE/Redis。
4. **API 回退**：`backend/app/api/routes.py:590-615` 优先读 InfluxDB，无数据时回退到模拟读数；`618-650` 的历史接口同样回退到随机生成数据。两条响应都带 `source: influxdb|mock`。

### 4.2 可作为同化输入的现有字段

`backend/app/schemas/schemas.py:160-180` 的 `SensorReading`/`SensorReadingBatch` 定义了时间、设备 ID、液位、流量、pH、浊度、温度、湿度、电池电压、信号和扩展字段。现有前端类型也只覆盖这些遥测字段（`frontend/src/types/index.ts:18-31`）。

### 4.3 同化缺口

当前没有以下任何实现或契约：

- `RainfallSource` 抽象、合成雨型、情景持久化；
- 传感器到 `network_node`、`pipe_segment`、汇水区或观测方程的映射；
- 传感器质量标志、缺测处理、空间插值、边界条件生成；
- 水位/流量观测同化、状态校正、参数率定；
- RMSE、NSE、预测-实测查询或 A/B 指标持久化。

因此，InfluxDB 只能作为“已有遥测输入适配器”的起点，不能宣称已经具备水力模型同化来源。`MockDataGenerator` 也只是随机数据生成器（`mock_data_generator.py:208-229`），不是降雨源或可复现实验源。

### 4.4 传感器链路的实现风险

- InfluxDB 服务方法是同步函数（`influxdb_service.py:70-212`），却在 async FastAPI 路由中直接调用（`backend/app/api/routes.py:590-650`），查询量大时会阻塞事件循环。
- `InfluxDBService.connect()` 在 `ping()` 失败时只记录 warning，仍保留 `_client`（`influxdb_service.py:28-47`）；`is_connected` 只判断 `_client is not None`（`57-59`），健康状态可能与真实可用性不一致。
- `backend/app/services/system_status.py:18-25` 导入 `influxdb_client`，但 `backend/app/services/influxdb_service.py:215` 实际导出的是 `influxdb_service`；该导入错误会被捕获并报告为 `not_configured`，需要修复后再把健康检查作为仿真依赖门禁。
- InfluxDB 查询把设备 ID 直接拼入 Flux 字符串（`influxdb_service.py:133-145`、`181-188`）；当前设备 ID 来自内部路径/数据库，但未来开放观测筛选时必须参数化或严格校验。

## 5. 任务持久化、队列与数据库现状

### 5.1 现有持久化实体

`backend/app/models/db_models.py` 只有以下 ORM 类：

- `User`（`47-77`）
- `Device`（`85-128`）
- `CameraStream`（`137-172`）
- `Alert`（`181-235`）
- `ModelVersion`（`244-268`）
- `InferenceResult`（`277-295`）

没有 `Network`、`NetworkNode`、`PipeSegment`、`Catchment`、`RainfallScenario`、`Simulation`、`SimulationFrame`、`Forecast`、`CalibrationRun` 或 `Task` 实体。

`backend/app/api/routes.py` 的路由装饰器清单在 `70-1073` 只覆盖 auth、models、inference、streams、SSE/WebSocket、sensors、alerts、devices、mock；没有 networks、rainfall-scenarios、simulations、forecast 路由。

### 5.2 Redis 不是任务队列

`backend/app/core/redis_client.py:50-62` 建立单个异步 Redis client（`max_connections=10`）；`83-99` 只做 `publish`；`102-149` 是 Pub/Sub listener；`150-164` 是 `get/set/delete`。没有：

- `LPUSH/RPOP`、Streams、consumer group；
- pending/claimed/lease/visibility timeout；
- retry count、dead-letter、幂等键；
- 任务状态与结果的事务性落库。

Docker Compose 实际服务只有 `postgres`、`redis`、`influxdb`、`backend`、`nginx`；`docker compose config --services` 的输出为：

```text
postgres
redis
influxdb
backend
nginx
```

没有独立 `simulation-worker` 服务，也没有 worker 镜像/入口/健康检查。

### 5.3 Alembic 与 Docker 初始化不一致

这是后续仿真任务持久化前必须处理的阻断项：

- `docker/init-db.sql:100-118` 创建并 hypertable 化 `sensor_readings`。
- `backend/alembic/versions/0001_initial.py:20-258` 只创建 users、devices、camera_streams、alerts、model_versions、inference_results，没有 `sensor_readings`。
- `backend/app/models/db_models.py:46-295` 没有 `SensorReading` ORM，因此 `backend/app/core/database.py:137-142` 的 `Base.metadata.create_all` 也不会创建该表。
- `docker/init-db.sql:76-79` 使用 `ST_SetSRID`/`ST_MakePoint` 和 GIST，但没有 `CREATE EXTENSION postgis`；`docker-compose.yml:10` 使用的是 `timescale/timescaledb:latest-pg15`，仓库内没有 PostGIS 镜像或扩展安装步骤。不能假设 PostGIS 路径可启动，需在真实数据库上验证。

### 5.4 保留期现状

- InfluxDB Compose 初始化设置 `DOCKER_INFLUXDB_INIT_RETENTION: 90d`（`docker-compose.yml:48-60`）。
- `docker/influxdb-setup.sh:10-31` 另建 `sensor_data_1h` bucket，保留 365 天，并创建小时聚合任务。
- TimescaleDB `sensor_readings` 初始化 SQL 没有 `add_retention_policy`；更没有仿真结果/帧表的 90 天策略。因此 PRD 的“仿真结果 TimescaleDB 90 天”目前没有落点。

## 6. 求解器依赖与性能基线

### 6.1 声明的依赖

`backend/requirements.txt:5-54` 声明 FastAPI、SQLAlchemy/asyncpg、InfluxDB client、Redis、Ultralytics/YOLO、PyTorch、OpenCV、NumPy、FFmpeg 等；`27-34` 是视觉推理依赖。`backend/requirements-cpu.txt:3-24` 保留大部分 CPU/服务依赖，但没有显式 `torch`、`torchvision`、`onnxruntime` 或任何 SWMM 依赖。

精确依赖扫描：

```text
rg -n -i 'swmm|pyswmm|scipy|shapely|geojson|geoalchemy|postgis|pyproj|networkx|numba' backend/requirements*.txt backend/pyproject.toml docker README.md CODE_WIKI.md
```

结果：无命中。

本机检查命令及结果：

```text
python -c "import importlib.util; names=['pytest_asyncio','prometheus_fastapi_instrumentator','swmm','pyswmm','swmm_toolkit','scipy','numpy']; print({n: bool(importlib.util.find_spec(n)) for n in names})"
```

输出：

```text
{'pytest_asyncio': False, 'prometheus_fastapi_instrumentator': False, 'swmm': False, 'pyswmm': False, 'swmm_toolkit': False, 'scipy': True, 'numpy': True}
```

这只能说明当前 Python 3.14 环境有 SciPy/NumPy，不能替代项目依赖锁定；项目声明仍没有可审计的求解器依赖。

### 6.2 Windows 现状

本次主机是 Windows，`python --version` 输出 `Python 3.14.4`；项目 `backend/pyproject.toml:8-12` 的 mypy 目标为 Python 3.11，README 目标为 Python 3.11+。当前测试不是 3.11 环境。

可执行文件检查：

```text
where.exe swmm5   -> NOT_FOUND
where.exe ffmpeg  -> C:\Users\zyq\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe
where.exe docker  -> C:\Program Files\Docker\Docker\resources\bin\docker.exe
where.exe node    -> C:\Program Files\nodejs\node.exe
```

已有 Windows 脚本只有备份和模型下载：`scripts/backup.ps1:1-30`、`scripts/download-model.ps1:1-20`；恢复脚本是 Bash（`scripts/restore.sh:1-52`）。没有 Windows 仿真 worker、SWMM 安装/发现、solver 版本检查或 Windows 性能基准。

现有跨平台代码仅部分处理 Windows：

- `backend/app/services/stream_service.py:303-310` 对 Windows 不传 `preexec_fn`；
- `125-141` 仍使用 `SIGTERM`、等待和 `kill`；
- 这对 FFmpeg 视频进程有条件分支，但不能证明水力子进程、Windows service、Ctrl-C、进程树清理或文件锁行为已验证。

## 7. API、前端和告警契约风险

### 7.1 API 缺口

PRD 要求的以下端点在实际路由清单中不存在：

- `/api/networks` CRUD/import/validate/export/seed；
- `/api/rainfall-scenarios` CRUD；
- `/api/simulations` 提交/查询/取消/结果/率定/实测比对；
- `/api/forecast` latest/horizon/risk-ranking；
- 仿真进度/完成 SSE 事件。

现有认证/RBAC 可复用：`backend/app/core/security.py:108-135` 提供 `require_auth` 和 `require_role(RoleEnum...)`；现有写接口如 stream、device、alert 使用角色依赖（`backend/app/api/routes.py:315-320`、`955-960`、`864-870`）。但 `/mock/start` 和 `/mock/stop` 明确无认证（`1066-1077`），新仿真写接口不能照此开放。

### 7.2 现有错误格式和观测契约

- FastAPI 全局校验错误返回 `detail` + `errors`（`backend/app/main.py:313-322`），未处理异常返回 `detail` + `type`（`325-335`）。
- 请求 ID 和处理时间由 middleware 写入响应头（`backend/app/main.py:240-258`）。
- 现有 SSE 事件由后端广播为 `sensors`/`alerts`，前端只监听这两类（`backend/app/api/routes.py:393-413`；`frontend/src/hooks/useSSE.ts:75-93`）。
- 现有告警 ORM 类型只有 water accumulation、manhole anomaly、intrusion、illegal parking、water level high、flow anomaly、device offline、system error（`backend/app/models/db_models.py:189-201`）；没有 `flood_forecast` 或 `pipe_surcharge`。
- 现有告警 schema 没有 `simulation_id`、`lead_time_minutes`、目标节点/管段、预计时刻、预测深度、置信度等字段（`backend/app/schemas/schemas.py:129-146`）。

### 7.3 前端缺口

实际路由只有 `/dashboard`、`/map`、`/video`、`/alerts`、`/settings`（`frontend/src/App.tsx:37-99`），没有 `/simulation`。`frontend/src/services/api.ts:49-166` 只有 sensor、alert、device、model、inference、stream、mock、system API 命名空间。`frontend/src/types/index.ts:4-130` 没有 network、rainfall、simulation、frame、forecast 类型。

现有 Leaflet 只渲染设备点标记：`frontend/src/components/map/MapVisualization.tsx:274-295` 使用 `TileLayer` 和 `devices.map`，没有管网线、管段充满度、节点/汇水区图层或时间轴。该组件可以作为地图容器复用，但不能视为仿真地图实现。

## 8. 安全、文件与部署契约风险

1. **上传上限不一致。** 后端默认 `MAX_UPLOAD_SIZE_MB=100`（`backend/app/core/config.py:102-105`），Nginx `client_max_body_size 100M`（`docker/nginx/conf.d/default.conf:8-14`），而 PRD 水力仿真沙箱要求 20MB。仿真文件上传必须使用独立白名单、扩展名/MIME/内容校验、大小上限和临时目录，不能复用当前通用上传默认值。
2. **没有仿真临时目录沙箱。** 现有媒体目录由 `settings.model_storage_dir`、`hls_output_dir`、`screenshot_dir` 属性直接 `mkdir`（`backend/app/core/config.py:122-141`），没有任务级临时目录、路径穿越防护、磁盘配额或 finally 清理契约。
3. **没有任务级 metrics。** `/metrics` 存在，但没有 simulation duration、failure rate、queue depth、solver fallback 次数；日志格式也没有强制 `simulation_id`（`backend/app/main.py:43-73`）。
4. **Compose 初始化和 Alembic 双轨。** `backend/app/main.py:132-141` 启动时调用 `create_all_tables`，而 README 同时要求 `alembic upgrade head`；在没有 migration 检查的情况下，生产启动可能掩盖 schema 漂移。
5. **PostGIS 依赖未显式安装。** `docker/init-db.sql:76-79` 使用空间函数，但初始化只启用 TimescaleDB（`4-6`），Compose 没有 PostGIS image/extension 命令。
6. **CPU/GPU 依赖声明不一致。** GPU requirements 在 `backend/requirements.txt:33-34` 使用 `onnxruntime-gpu`，CPU requirements 没有对应 CPU runtime；这与仿真“纯 Python、无需 GPU”不是同一依赖策略，不能直接作为求解器方案。

## 9. 已执行检查与真实结果

| 命令 | 结果 | 说明 |
|---|---|---|
| `python --version` | `Python 3.14.4` | 不是项目目标 3.11；后续后端检查受环境影响 |
| `python -m pytest -q`（`backend/`） | 退出码 4；收集阶段 `ModuleNotFoundError: No module named 'pytest_asyncio'` | pytest 未能收集测试，不能宣称后端通过 |
| `python -m compileall -q app tests`（`backend/`） | 退出码 0 | Python 语法/字节码编译通过 |
| `python -c "from app.main import app; print(app.title)"`（`backend/`） | 退出码 1；`ModuleNotFoundError: No module named 'prometheus_fastapi_instrumentator'` | 应用无法在当前环境导入；不是业务测试通过 |
| `npm test -- --run`（`frontend/`） | 退出码 1；8 项中 6 通过、2 失败；失败均为 `localStorage.clear is not a function`（`LoginPage.test.tsx:24`） | 现有前端测试基线有环境/测试隔离问题 |
| `npm run build`（`frontend/`） | 退出码 0；Vite build 成功，11.59s；有 chunk >1000kB 警告 | 编译通过，不代表仿真 UI 存在或性能达标 |
| `npm run lint`（`frontend/`） | 退出码 0 | ESLint 通过 |
| `docker compose config --quiet` | 退出码 0；仅警告 `version` obsolete | Compose YAML 可解析；没有启动服务 |
| `docker compose config --services` | `postgres redis influxdb backend nginx` | 无 simulation-worker |
| `where.exe swmm5` | `NOT_FOUND` | 当前 Windows 没有 SWMM 可执行文件 |
| `where.exe ffmpeg` | `C:\Users\zyq\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe` | FFmpeg 可用，但与水力 solver 无关 |
| `git diff --check` | 无输出、退出码 0 | 本次没有已跟踪源码 diff |

## 10. 明确未运行的检查

以下项目本次没有运行，不能标记为通过：

- `docker compose up -d` 全栈启动、数据库迁移、PostGIS/Timescale 实际写入；
- `alembic upgrade head` / `alembic downgrade`；
- 5000 节点/10000 管段、24h、4 核、180s 性能基准；
- SWMM/纯 Python 求解器数值稳定性、帧序列化和 RMSE/NSE；
- 50 年一遇降雨情景、可复现随机雨型、72h 自定义情景；
- 任务取消、10 分钟超时、最多两次重试、幂等、并发 2、崩溃恢复；
- Windows 原生仿真 worker、进程树清理、Ctrl-C、文件锁和沙箱穿越测试；
- 新 API 的 P95、nowcast 3 分钟延迟、SSE 预测事件和 30 分钟告警抑制。

## 11. 缺口与建议的下一侦察/实现顺序

以下是依据现状得出的优先级，不是已经完成的功能：

### P0：先锁定数据与任务契约

1. 明确 `networks/network_nodes/pipe_segments/catchments/elevation_profiles` 的 WKT/GeoJSON/PostGIS 双路径 schema；先解决 `init-db.sql`、Alembic、ORM 三者一致性。
2. 明确 `RainfallScenario`、`Simulation`、`SimulationFrame`、`Forecast`、`CalibrationRun` 的状态机、幂等键、重试次数、超时和取消语义。
3. 选择持久化任务存储：现有 PostgreSQL/TimescaleDB 作为事实源，Redis 只做通知/短期加速；不要把 SSE 内存历史或 Redis Pub/Sub 当任务事实源。

### P0：选择并验证求解器

1. 首选明确 EPA SWMM 的 Windows 可执行文件/版本、许可证、输入输出文件契约和超时杀进程方式。
2. 同时实现纯 Python 运动波/扩散波和简化地表积水降级路径；所有 solver 都应输出同一帧协议（时间、节点状态、管段状态、积水状态、指标、单位、版本）。
3. 在真实 4 核环境跑规模基准后再宣称满足 180s；当前依赖和环境无法支持该结论。

### P1：抽取可复用 worker 基础设施

1. 从 `InferenceWorker`/`StreamService` 抽取与业务无关的 task runner：持久化状态、heartbeat、lease、cancel token、timeout、retry、progress event、finally cleanup。
2. 修正现有异步调用契约：`add_worker/remove_worker/stop_all` 必须 await；统一 `running/is_running` 字段；避免 `time.sleep` 阻塞事件循环。
3. 为 worker 单独提供 Docker/Windows 启动入口和 `/health`，而不是让 API 进程内隐式创建不可恢复任务。

### P1：建立观测适配与校准边界

1. 把 InfluxDB、TimescaleDB、mock、未来的 SWMM rainfall 输入统一为版本化 adapter/observation schema。
2. 明确设备到网络对象的映射、缺测/异常值策略和同化时间窗。
3. 把率定参数、RMSE/NSE、A/B 差异持久化，避免把随机 mock 回退数据当作校准样本。

### P1：前端和部署补齐

1. 新增 `/simulation` 路由、权限/i18n、Leaflet 管网图层、帧时间轴和任务状态 API 类型；现有 `MapVisualization` 仅能复用 Leaflet 容器。
2. 在 `pydantic-settings` 增加 solver、并发、调度、预报、保留期配置；当前配置没有这些键。
3. 让 Compose 的 worker、数据库迁移、健康检查和 Windows 运行说明与 README/AC-8 同步；当前 Compose 没有 worker，且 PostGIS 初始化未验证。

## 12. 关键文件索引

- API/生命周期：`backend/app/api/routes.py:60-1077`、`backend/app/main.py:101-218`
- 配置：`backend/app/core/config.py:16-145`
- 数据库：`backend/app/core/database.py:57-185`、`backend/app/models/db_models.py:46-295`
- 迁移/SQL：`backend/alembic/versions/0001_initial.py:20-258`、`docker/init-db.sql:1-187`
- Redis/SSE：`backend/app/core/redis_client.py:24-167`、`backend/app/services/sse_manager.py:32-202`
- 传感器：`backend/app/services/influxdb_service.py:12-216`、`backend/app/services/mock_data_generator.py:23-277`
- Worker：`backend/app/workers/inference_worker.py:44-298`、`backend/app/services/stream_service.py:50-355`
- 依赖/镜像：`backend/requirements.txt:5-61`、`backend/requirements-cpu.txt:1-34`、`docker/backend/Dockerfile.cpu:1-28`、`docker/backend/Dockerfile:1-40`
- 前端契约：`frontend/src/App.tsx:35-102`、`frontend/src/services/api.ts:5-184`、`frontend/src/hooks/useSSE.ts:28-135`、`frontend/src/components/map/MapVisualization.tsx:236-318`
- Windows 脚本：`scripts/backup.ps1:1-30`、`scripts/download-model.ps1:1-20`、`scripts/restore.sh:1-52`
