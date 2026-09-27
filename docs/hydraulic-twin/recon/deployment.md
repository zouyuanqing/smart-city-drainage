# 部署与数据库现状侦察

> 侦察对象：`smart-city-drainage` 当前工作树（最新已读提交 `ca99e0485fb12fbecbd565e79d81f745f8d4a748`，提交说明为 `chore: remove stray isort diff log`）。本文只把本次实际读取的代码/配置和本次实际运行的命令输出当作现状证据；PRD 摘要作为目标约束单独标注，不把仓库 README/规划文档中的勾选当作实现事实。
>
> 路径均相对于 `smart-city-drainage` 根目录。除本报告外，本次没有修改源码、迁移、Compose 或测试文件。

## 1. 结论摘要

1. **当前部署是旧的“排水监测 + AI 视频”应用，不是水力数字孪生部署。** Compose 有 PostgreSQL/TimescaleDB、Redis、InfluxDB、FastAPI、Nginx 和可选监控 profile；后端只有 30 个 HTTP 路由和 1 个 WebSocket 路由，未发现 `networks`、`rainfall-scenarios`、`simulations`、`forecast`、`hydraulic` 或 `swmm` 实现符号。
2. **PostGIS/Timescale 双路径尚未建立。** Compose 只使用 `timescale/timescaledb:latest-pg15`，没有 PostGIS 镜像、`CREATE EXTENSION postgis` 或无 PostGIS 的 WKT/GeoJSON 适配层。`docker/init-db.sql` 却在设备索引表达式中调用 `ST_SetSRID(ST_MakePoint(...))`（`docker/init-db.sql:76-79`），而扩展启用列表只有 TimescaleDB 和 uuid-ossp（`docker/init-db.sql:4-9`）。这证明 SQL 契约不自洽；因为本次 Docker daemon 不可用，未宣称全新数据库初始化一定成功或必然失败。
3. **`init-db.sql`、Alembic 和 ORM 不是同一 schema 事实来源。** `init-db.sql` 独有 `sensor_readings` hypertable（`docker/init-db.sql:100-118`）；Alembic 迁移没有该表、没有 `create_hypertable`，只有六张旧业务表（`backend/alembic/versions/0001_initial.py:20-241`）；ORM 也只有六张表（`backend/app/models/db_models.py:46-295`）。应用启动还调用 `create_all_tables()`（`backend/app/main.py:132-141`），形成 Compose init、Alembic、运行时 `create_all` 三条路径。
4. **没有水力仿真 worker/队列部署。** 现有 `InferenceWorker` 是视频帧 YOLO 协程（`backend/app/workers/inference_worker.py:55-298`），不是 hydraulic simulation worker；Compose 没有 worker service、任务队列、取消/超时/重试/崩溃恢复配置。Redis 客户端的 Pub/Sub 和 SSE manager 可以复用，但当前事件历史是单进程内存（`backend/app/services/sse_manager.py:32-43`），不满足多 worker 可恢复 replay。
5. **健康检查是“可启动”级别，不是依赖就绪级别。** PostgreSQL、Redis、InfluxDB 在 Compose 有健康检查（`docker-compose.yml:24-28,42-46,67-71`）；CPU/GPU Dockerfile 有 `/health` HEALTHCHECK（`docker/backend/Dockerfile.cpu:22-24`、`docker/backend/Dockerfile:34-36`）。但应用根 `/health` 永远返回 `{"status": "healthy"}`（`backend/app/main.py:302-305`），启动时数据库/Redis/Influx 失败会降级并继续运行（`backend/app/main.py:132-169`）；Dockerfile 的 `httpx.get(...)` 没有检查 HTTP 状态码。因此“容器 healthy”不等于数据库迁移、空间扩展或仿真依赖可用。
6. **当前 Compose 文件可解析，但本次没有运行容器。** `docker compose config --quiet` 返回 0，仅给出 `version` 字段过时警告；随后 `docker compose build backend` 因 Docker Desktop Linux engine pipe 不存在失败。故没有真实启动、PostGIS/Timescale SQL、迁移、live health 或 API 验证结果。

## 2. 当前 Compose、镜像和启动拓扑

### 2.1 服务图

| 服务 | 镜像/构建 | 端口与持久化 | 依赖/健康 | 证据 |
|---|---|---|---|---|
| `postgres` | `timescale/timescaledb:latest-pg15` | `5432`，`pgdata` | `pg_isready` 每 10s，最多 5 次 | `docker-compose.yml:8-28` |
| `redis` | `redis:7-alpine` | `6379`，`redisdata` | `redis-cli --raw incr ping` 每 10s | `docker-compose.yml:30-46` |
| `influxdb` | `influxdb:2.7-alpine` | `8086`，`influxdata`；原始 bucket 90d | `influx ping` 每 10s | `docker-compose.yml:48-71` |
| `backend` | context `./backend`，`../docker/backend/Dockerfile.cpu` | `8000`；模型只读挂载，HLS 共享卷 | 等待 postgres/redis/influx healthy | `docker-compose.yml:73-109` |
| `nginx` | `nginx:alpine` | `8080:80`、`8443:443` | 等待 backend healthy | `docker-compose.yml:111-127`；代理配置 `docker/nginx/conf.d/default.conf:27-70` |
| `frontend` | `node:20-alpine` 多阶段构建后 `nginx:alpine` | `3000:80` | 仅 `production` profile | `docker-compose.yml:129-142`；`docker/frontend/Dockerfile:0-11` |
| `prometheus` | `prom/prometheus:v2.48.0` | `9090`，配置挂载 | `monitoring` profile；仅依赖 backend | `docker-compose.yml:144-158`；`docker/prometheus/prometheus.yml:1-8` |
| `grafana` | `grafana/grafana:10.3.0` | `3001:3000` | `monitoring` profile；依赖 Prometheus | `docker-compose.yml:160-177` |

本次执行的有效 Compose 图命令及输出：

```text
$ docker compose config --format json | python -c "import sys,json; d=json.load(sys.stdin); print('services=' + ','.join(d['services'])); [print(n, 'healthcheck=' + str('yes' if 'healthcheck' in s else 'no'), 'depends=' + ','.join(s.get('depends_on',{}))) for n,s in d['services'].items()]"
services=backend,influxdb,nginx,postgres,redis
backend healthcheck=no depends=influxdb,postgres,redis
influxdb healthcheck=yes depends=
nginx healthcheck=no depends=backend
postgres healthcheck=yes depends=
redis healthcheck=yes depends=
```

`backend healthcheck=no` 是 Compose 配置 JSON 的事实：健康检查定义在 Dockerfile 的 `HEALTHCHECK`，不是 Compose service 的 `healthcheck` 字段。Docker daemon 可用时，镜像元数据仍可能使容器具备该 healthcheck；本次没有运行容器，不能验证运行时状态。

### 2.2 镜像和启动命令

- CPU 镜像基于 `python:3.11-slim`，安装 ffmpeg 和媒体库后执行 `uvicorn app.main:app --host 0.0.0.0 --port 8000`（`docker/backend/Dockerfile.cpu:0-27`）。Compose 明确选 CPU 镜像（`docker-compose.yml:75-78`），符合无 GPU 方向，但镜像仍安装 `ultralytics` 等视频 AI 依赖（`backend/requirements-cpu.txt:11-14`），不是水力求解器镜像。
- GPU 镜像基于 `nvidia/cuda:12.1-runtime-ubuntu22.04`，使用 4 个 Uvicorn worker（`docker/backend/Dockerfile:0-39`）。这属于旧视频推理部署，不包含 SWMM/纯 Python hydraulic solver 的安装或 worker 入口。
- 两个 Dockerfile 的 healthcheck 都只执行 `httpx.get('http://localhost:8000/health')`（`docker/backend/Dockerfile.cpu:22-24`；`docker/backend/Dockerfile:34-36`），没有 `raise_for_status()`，也没有检查 `/api/system/status`、数据库迁移版本、扩展或队列。
- 前端 Dockerfile 使用 `npm install` 而非 `npm ci`，先 `npm run build` 再复制 `dist`（`docker/frontend/Dockerfile:0-11`）；可复用的生产静态托管模式存在，但依赖安装可重复性弱于 CI 中的 `npm ci`。

### 2.3 Compose 配置风险

- `docker-compose.yml:1` 仍声明 `version: "3.9"`；本次 `docker compose config` 明确提示该属性 obsolete，但命令仍返回 0。
- Compose 把数据库、Influx、Redis、JWT、Grafana 的固定凭据直接写入环境（`docker-compose.yml:13-16,53-60,81-89,168-171`），并且将 PostgreSQL/Redis/Influx 端口暴露到宿主机（`docker-compose.yml:17-18,36-37,61-62`）。这适合演示，不应作为生产 secret 管理方案。
- PostgreSQL URL 在 Compose 中原样包含密码 `Dr@inage_S3cur3_2024!`（`docker-compose.yml:81`），而 `backend/app/core/database.py:33-54` 只有在 hostname 为 `localhost/127.0.0.1` 时才重建并 URL-encode 用户名/密码；容器 hostname 是 `postgres`，会走原样返回分支。Alembic 默认 URL 也原样含特殊字符（`backend/alembic.ini:2-5`）。这是需要在实际 asyncpg/Alembic 连接中验证的高风险契约。
- `docker-compose.yml:19-22` 只把 `init-db.sql` 挂到 `/docker-entrypoint-initdb.d/init.sql`，没有 `alembic upgrade head` 服务或 entrypoint 步骤。已有 volume 的数据库也不会因该挂载自动重新执行初始化脚本；本次没有运行 live DB 验证。
- Compose 默认 profile 不包含 frontend、Prometheus、Grafana；README 所述的 `docker compose up -d` 实际有效服务由本次命令确认只有 `backend,influxdb,nginx,postgres,redis`。生产前端和监控需显式 profile（`docker-compose.yml:141-142,148-149,164-165`）。

## 3. 数据库现状：init-db、Alembic、ORM

### 3.1 `docker/init-db.sql` 实际内容

初始化脚本启用 `timescaledb` 和 `uuid-ossp`（`docker/init-db.sql:4-9`），创建旧域枚举和表：users、devices、camera_streams、sensor_readings、alerts、model_versions、inference_results（`docker/init-db.sql:10-166`）。其中：

- `sensor_readings` 以 `time` 为主时间列，调用 `create_hypertable('sensor_readings', 'time', if_not_exists => TRUE)` 并建立 `(device_id, time DESC)` 索引（`docker/init-db.sql:100-118`）。
- 设备位置索引调用 `ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)`（`docker/init-db.sql:76-79`），但没有 `CREATE EXTENSION postgis`，也没有 `geometry`/`geography` 列定义。
- 种子写入固定管理员和 10 个设备（8 个井盖、2 个摄像头）（`docker/init-db.sql:168-187`）；这些设备没有 network/node/segment/catchment 外键或 hydraulic seed 标识。

本次静态比较命令及输出：

```text
$ python - <<'PY' ... compare init/migration markers ... PY
init_tables=users,devices,camera_streams,sensor_readings,alerts,model_versions,inference_results
migration_tables=users,devices,camera_streams,alerts,model_versions,inference_results
init_only=sensor_readings
migration_only=
postgis init=False migration=False
geometry init=False migration=False
geography init=False migration=False
sensor_readings init=True migration=False
create_hypertable init=True migration=False
timescaledb init=True migration=True
```

该比较只证明文本契约差异，不等价于实际数据库执行结果；Docker daemon 不可用，未执行 `init.sql`。

### 3.2 Alembic 实际内容

仓库只有一个 revision：`0001_initial`（`backend/alembic/versions/0001_initial.py:14-17`）。`upgrade()` 启用 uuid-ossp 和 timescaledb（`backend/alembic/versions/0001_initial.py:20-24`），创建 users、devices、camera_streams、alerts、model_versions、inference_results 六张表（`backend/alembic/versions/0001_initial.py:25-228`），创建两个 alerts 索引并插入管理员（`backend/alembic/versions/0001_initial.py:230-241`）。

缺失项：

- 没有 `sensor_readings` 表、`create_hypertable`、Timescale retention policy 或传感器 frame 表。
- 没有 PostGIS extension、geometry/geography 列或空间索引。
- `downgrade()` 删除六张表和 enum（`backend/alembic/versions/0001_initial.py:244-258`），但不删除 TimescaleDB/uuid 扩展；也没有可回滚的 sensor hypertable。

`backend/alembic/env.py:18` 用 `settings.DATABASE_URL` 覆盖 ini URL，`42-55` 使用 async engine 执行迁移；这证明迁移执行路径是异步 SQLAlchemy，但不证明迁移在真实 PostgreSQL 上成功。

本次从正确工作目录执行：

```text
$ cd backend && python -m alembic heads
0001_initial (head)
```

这只证明迁移图可读取；没有执行 `alembic upgrade head`，因此不能报告迁移通过。

### 3.3 ORM 实际内容

ORM `Base` 位于 `backend/app/models/db_models.py:29-32`，声明的持久化实体只有：

- `User`（`46-76`）
- `Device`（`84-129`）
- `CameraStream`（`136-172`）
- `Alert`（`180-235`）
- `ModelVersion`（`243-268`）
- `InferenceResult`（`276-294`）

`Device` 只有普通 `Float` 经纬度（`backend/app/models/db_models.py:99-103`），没有 geometry；`Alert.bbox_coordinates` 是普通 JSON（`backend/app/models/db_models.py:207-221`），不是空间字段。没有 `SensorReading`、network、node、pipe、catchment、elevation、rainfall、simulation 或 forecast 模型。

### 3.4 三条建表路径及漂移

| 路径 | 触发方式 | 实际覆盖 | 风险 |
|---|---|---|---|
| Compose 初始化 | `docker-compose.yml:19-22` 挂载 init SQL | 7 张表，含 sensor hypertable、PostGIS 函数调用 | 依赖空 volume/首次初始化；无 Alembic 版本记录；扩展未显式启用 |
| Alembic | `backend/alembic/env.py:18-55` + `0001_initial.py:20-241` | 6 张表，无 sensor hypertable | 与 init SQL 不一致；`upgrade` 未执行验证 |
| 运行时 create_all | `backend/app/main.py:132-141` 调 `create_all_tables()` | 只会按 ORM metadata 建/确认六表 | 不会创建 init SQL 的 sensor hypertable；生产代码把开发建表路径带进启动 |

此外，ORM 与迁移/SQL 已有可观察的类型漂移：`users.role` 在 ORM 是 `Enum(RoleEnum)`（`backend/app/models/db_models.py:35-38,58`），迁移是 `String(32)`（`backend/alembic/versions/0001_initial.py:34-39`），init SQL 是 `VARCHAR(32)`（`docker/init-db.sql:41-52`）；`devices.install_date` 在 ORM/迁移为 DateTime（`db_models.py:104`、`0001_initial.py:73`），init SQL 为 DATE（`docker/init-db.sql:65`）；JSON/JSONB 和 `model_versions.file_size_bytes` 也存在 ORM、迁移、init SQL 类型差异。

## 4. PostGIS/Timescale/Influx 双路径现状

### 4.1 已有的 Timescale/Influx 事实

- PostgreSQL 镜像名包含 TimescaleDB（`docker-compose.yml:8-11`），init SQL 和 Alembic 都尝试启用 `timescaledb`（`docker/init-db.sql:4-6`；`backend/alembic/versions/0001_initial.py:20-24`）。
- 传感器服务实际写入/查询 InfluxDB `sensor_readings` measurement（`backend/app/services/influxdb_service.py:70-124,126-212`）；Compose 原始 Influx bucket retention 为 90d（`docker-compose.yml:53-61`），`docker/influxdb-setup.sh:10-30` 另外创建 365d 的小时聚合 bucket/task。
- PostgreSQL init SQL 虽创建 sensor hypertable，但当前后端没有 ORM 或 SQL repository 写它；因此“Timescale sensor table”和“实际 Influx sensor measurement”是两个未统一的数据源。

### 4.2 PostGIS 证据与结论

本次在 `backend/app`、`backend/tests`、`docker`、`docker-compose.yml`、`frontend/src`（排除 lock/md/json）执行 hydraulic/spatial 关键词搜索，命令无输出：

```text
$ grep -RInE --exclude='*.lock' --exclude='*.md' --exclude='*.json' \
  -i 'postgis|geometry|geography|network_nodes|pipe_segments|catchments|rainfall.?scenarios|simulations|forecast|nowcast|hydraulic|swmm|wkt|geojson|flood_forecast|pipe_surcharge' \
  backend/app backend/tests docker docker-compose.yml frontend/src
# no output
```

因此当前没有可复用的 PostGIS geometry repository、WKT/GeoJSON serializer、bbox query、network topology validator 或无 PostGIS fallback。仅凭 `ST_*` 文本不能把 PostGIS 能力视为已部署；扩展是否由基础镜像预装、init SQL 是否能执行，均需在 Docker daemon 可用后用 `\dx`、`CREATE EXTENSION` 和空库初始化实测。

## 5. 健康检查、启动降级与可观测性

### 5.1 端点和探针

- 根探针：`GET /health` 永远返回 healthy（`backend/app/main.py:302-305`），Dockerfile 使用它（`docker/backend/Dockerfile.cpu:22-24`）。
- API 健康：`GET /api/health` 返回固定 healthy、model 状态和 SSE 客户端数（`backend/app/api/routes.py:69-80`）。
- 依赖状态：`GET /api/system/status` 并发检查 PostgreSQL、Influx、Redis、model（`backend/app/api/routes.py:83-85`；`backend/app/services/system_status.py:6-81`）。该接口比根探针更适合作为 readiness，但当前 Compose/Nginx 尚未把它作为探针。
- Nginx `/health` 反代到 backend `/health`（`docker/nginx/conf.d/default.conf:66-70`），因此同样只反映应用浅层状态。
- Prometheus 已有 FastAPI instrumentator `/metrics`（`backend/app/main.py:214-219`），配置只抓取 `backend:8000/metrics`（`docker/prometheus/prometheus.yml:4-8`）。未发现 hydraulic task duration、failure rate、queue depth、simulation_id 等指标定义。

### 5.2 启动容错带来的 readiness 风险

应用启动先连接数据库；失败时只记录 warning 并继续使用模拟数据（`backend/app/main.py:132-141`）。Redis 失败时继续运行但实时消息不可用（`backend/app/main.py:143-153`），Influx 失败也只记录 warning（`backend/app/main.py:154-160`）。这符合旧演示模式，却不适合把 hydraulic 导入、仿真、预测数据丢失隐藏在“healthy”后面。

另外，`system_status.py:20-25` 导入 `influxdb_client` 并调用同步 `ping()`，而主服务对象是 `influxdb_service`；该状态接口的具体运行结果本次未通过 live API 验证，不能把静态代码当作连接通过。

## 6. Worker、队列、仿真和预测缺口

1. **现有 worker 不是仿真 worker。** `InferenceWorker` 打开 `cv2.VideoCapture`、抽帧、调用 YOLO、写快照并广播告警（`backend/app/workers/inference_worker.py:103-229`）；`WorkerPool` 只管理摄像头 worker（`248-298`）。没有 simulation job 表、任务状态机、进度、取消、幂等键、超时、最多两次重试、并发 2、临时目录/20MB/路径穿越防护/清理。
2. **Redis 目前是 Pub/Sub + cache，不是 durable job queue。** 客户端提供 connect/publish/subscribe/get/set/delete（`backend/app/core/redis_client.py:24-41,50-163`），没有队列 claim、租约、重试队列、dead-letter 或恢复扫描。
3. **SSE replay 仅进程内。** `SSEManager` 保存最多 1000 条历史（`backend/app/services/sse_manager.py:32-43,90-99`），连接队列上限 256（`64-66`），队列满时丢事件并断开客户端（`103-120`）。这不能直接承诺多 worker、重启后的 forecast/SSE 可靠重放。
4. **现有事件只有 sensors/alerts/system/connected。** Redis 监听注册在 `backend/app/main.py:143-150`；路由文档也只列这些类型（`backend/app/api/routes.py:393-413`），未发现 `forecast`、`flood_forecast`、`pipe_surcharge` 或 simulation progress/result 事件。
5. **保留期路径分裂。** Compose 只给 Influx 原始 bucket 配 90d（`docker-compose.yml:53-61`）；没有 Timescale `add_retention_policy`、仿真结果清理任务或可配置 `RESULT_RETENTION_DAYS`。PRD 要求的 90 天保留尚未有单一实现。

## 7. 可复用模式

以下模式有真实代码依据，可作为后续水力部署的起点，但必须补齐边界和契约：

1. **pydantic-settings 单例配置**：`Settings` 使用 `.env`、大小写不敏感和集中字段（`backend/app/core/config.py:16-24,26-120`）。可扩展 `SIMULATION_CONCURRENCY`、`SIMULATION_TIMEOUT_SECONDS`、`SIMULATION_MAX_RETRIES`、`RESULT_RETENTION_DAYS`、`NOWCAST_INTERVAL_MINUTES`、`FORECAST_SUPPRESSION_MINUTES`、solver/backend 路径等；目前这些字段不存在。
2. **异步 SQLAlchemy engine/session**：`create_async_engine`、pool size/overflow/pre-ping/recycle（`backend/app/core/database.py:57-72`），`async_sessionmaker` 的 `expire_on_commit=False`（`79-94`），以及 rollback 上下文（`102-129`）可复用。水力 worker 应显式使用同一 session 工厂，而不是新建同步连接。
3. **异步 Alembic 环境**：`async_engine_from_config(..., NullPool)` + `connection.run_sync`（`backend/alembic/env.py:42-55`）可复用。生产启动应采用明确的 migration gate，不能同时依赖 `create_all_tables()`。
4. **Compose dependency health gate**：postgres/redis/influx 的 `depends_on: condition: service_healthy`（`docker-compose.yml:103-110`）和 nginx 等待 backend（`125-127`）可复用；但要增加 migration/worker readiness、backend 的真实 healthcheck 状态和 worker 独立服务。
5. **CPU/GPU 镜像分层**：CPU Dockerfile 使用 Python 3.11 slim（`docker/backend/Dockerfile.cpu:0-27`），GPU Dockerfile 使用 CUDA runtime（`docker/backend/Dockerfile:0-40`）。水力纯 Python fallback 可沿用 CPU 镜像并拆分 solver 依赖，避免把 GPU/YOLO 作为必需条件。
6. **Nginx API/SSE/HLS 代理**：API、SSE、WebSocket 路由和长 read timeout 已配置（`docker/nginx/conf.d/default.conf:27-64`）；可复用作仿真 API/SSE 的入口，但应补 SSE buffering off、请求体/超时/上传限制和 worker 路由。
7. **Redis + SSE 事件分发**：Redis channel 注册（`backend/app/main.py:78-94,143-150`）、SSE client queue/heartbeat/Last-Event-ID（`backend/app/services/sse_manager.py:52-76,142-189`）可作为 forecast 事件原型；必须改成有持久游标的事件日志或 Redis Streams，并定义版本化 envelope。
8. **Prometheus instrumentator**：`/metrics` 已暴露（`backend/app/main.py:214-219`），可扩展任务指标；但当前只证明 HTTP 指标入口，不证明已有任务时长/失败率/队列深度。
9. **备份脚本的容器名/卷约定**：Linux/Windows 脚本通过 `scn-postgres`、`scn-influxdb`、`scn-redis` 备份（`scripts/backup.sh:15-35`、`scripts/backup.ps1:12-27`），可复用为水力 schema/仿真结果的备份入口；当前没有 migration history、PostGIS 扩展或 worker state 的专项备份/恢复验证。
10. **前端构建分层**（与部署验收相关）：Vite dev proxy 和 ECharts/Leaflet manual chunks（`frontend/vite.config.ts:24-61`）以及生产 Nginx 静态镜像模式可保留；本次不把前端报告中的规划当作部署现状。

## 8. 关键文件索引

| 文件 | 关键位置 | 当前职责/判断 |
|---|---|---|
| `docker-compose.yml` | `8-28`, `30-71`, `73-109`, `111-177` | 服务、凭据、依赖健康、profiles；无 migration/worker/PostGIS service |
| `docker/init-db.sql` | `4-9`, `76-79`, `100-118`, `168-187` | 旧 schema、空间函数调用、sensor hypertable、种子；与迁移/ORM 漂移 |
| `backend/alembic/versions/0001_initial.py` | `20-24`, `25-241`, `244-258` | 唯一迁移；无 sensor hypertable/PostGIS/水力表 |
| `backend/alembic/env.py` | `18-20`, `42-55` | async 迁移执行路径 |
| `backend/alembic.ini` | `2-5` | 默认同步 URL，含未编码特殊字符密码 |
| `backend/app/core/config.py` | `16-24`, `33-61`, `102-120` | Settings、DB/Redis/安全/通知；无水力运行参数 |
| `backend/app/core/database.py` | `33-72`, `79-129`, `137-163` | async engine/session、运行时建表、连接检查 |
| `backend/app/main.py` | `101-197`, `214-219`, `302-305` | 生命周期、容错启动、metrics、浅 health |
| `backend/app/models/db_models.py` | `29-32`, `46-295` | 六个旧 ORM 模型；无水力实体 |
| `backend/app/api/routes.py` | `60-62`, `69-85`, `393-413`, `589-717`, `725-891`, `948-1076` | 旧 API/SSE/设备/传感器/告警；无 PRD 水力路由 |
| `backend/app/services/influxdb_service.py` | `70-212` | 实际传感器 Influx 读写 |
| `backend/app/core/redis_client.py` | `24-41`, `50-163` | Pub/Sub/cache；非 durable simulation queue |
| `backend/app/services/sse_manager.py` | `32-43`, `90-120`, `142-189` | 内存事件历史、队列、心跳、Last-Event-ID |
| `backend/app/workers/inference_worker.py` | `55-298` | 视频 AI worker；不可当 hydraulic worker |
| `docker/backend/Dockerfile.cpu` | `0-27` | Python 3.11 CPU 镜像与浅 healthcheck |
| `docker/backend/Dockerfile` | `0-40` | CUDA GPU 镜像与 4-worker Uvicorn |
| `docker/nginx/conf.d/default.conf` | `27-70` | API/SSE/WS/HLS 代理与 health proxy |
| `docker/prometheus/prometheus.yml` | `1-8` | 仅抓 backend `/metrics` |
| `docker/influxdb-setup.sh` | `10-30` | 365d 小时聚合 bucket/task；未由 Compose 显式执行 |
| `backend/requirements-cpu.txt` | `5-33` | SQLAlchemy/asyncpg/Alembic/Redis/Influx/旧 AI 依赖；无 hydraulic solver |
| `backend/requirements.txt` | `5-62` | GPU 依赖；无 SWMM/水力队列依赖 |
| `.github/workflows/ci.yml` | `32-51`, `70-93` | 后端 pytest、前端 Vitest、CPU/frontend Docker build；未覆盖 compose/live DB/迁移 |
| `backend/tests/conftest.py` | `9-42` | SQLite+aiosqlite 夹具和 ASGI client；当前业务路由直接调用 context manager，覆盖方式不完整 |
| `scripts/backup.sh` / `scripts/backup.ps1` | `15-35` / `12-27` | 旧三组件备份；无水力 schema/worker state 专项验证 |

## 9. 本次实际运行的检查

### 9.1 通过的静态/离线检查

```text
$ docker compose config --quiet
warning: ... the attribute `version` is obsolete ...
compose_config_exit=0
```

这只证明 Compose YAML 可解析，不证明镜像、数据库或服务可运行。

```text
$ cd backend && python -m alembic heads
0001_initial (head)
```

只证明迁移图可读取，未执行 upgrade。

```text
$ python -m compileall -q backend/app
compileall exit=0
```

只证明 Python 源可编译，不证明依赖、导入、启动或数据库连接。

```text
$ git diff --check
git diff --check exit=0
```

只证明工作树 diff 无空白错误。

路由静态统计：

```text
$ python ... count route decorators ...
http 30
websocket 1
```

### 9.2 未通过或未运行的检查

```text
$ docker compose build backend
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct and if the daemon is running
exit 1
```

因此未运行：镜像构建、`docker compose up`、PostgreSQL init SQL、PostGIS extension 检查、Timescale hypertable 检查、Alembic upgrade、live health、API/SSE smoke。

```text
$ python -m pytest backend/tests/ -v --tb=short
ImportError while loading conftest '.../backend/tests/conftest.py'.
E   ModuleNotFoundError: No module named 'pytest_asyncio'
exit 4
```

这是当前环境依赖缺失导致的全量测试收集失败，不是测试通过，也不是源码断言失败。

```text
$ cd frontend && npm test -- --run
'vitest' 不是内部或外部命令，也不是可运行的程序
exit 1
```

Vitest 未运行。

```text
$ cd frontend && npm run lint
> eslint . --report-unused-disable-directives --max-warnings 0
exit 0
```

前端 lint 本次通过。

```text
$ cd frontend && npm run format:check
Code style issues found in 36 files.
exit 1
```

Prettier 检查未通过；本次没有自动修复。

```text
$ cd frontend && npm run build
✓ 4102 modules transformed.
✓ built in 25.57s
(!) Some chunks are larger than 1000 kB after minification.
exit 0
```

前端 production build 本次通过，但有大 chunk 警告；这不是 hydraulic 性能验收。

### 9.3 本机环境事实

```text
$ python --version
Python 3.14.4
$ node --version
v25.9.0
$ npm --version
11.12.1
$ docker --version
Docker version 29.4.2, build 055a478
$ docker compose version
Docker Compose version v5.1.3
```

项目 Dockerfile 目标是 Python 3.11（`docker/backend/Dockerfile.cpu:0`），本机 Python 3.14 只能用于部分静态检查，不能替代目标镜像验证。

## 10. 建议的部署/数据库验收命令（当前未全部运行）

以下是针对缺口的后续验收命令，不是本次已通过的声明：

### 10.1 唯一 schema 门禁与 PostGIS/Timescale

```bash
# 在 Docker daemon 可用后，先验证空卷初始化
docker compose down -v
 docker compose up -d postgres
 docker compose exec postgres psql -U drainage_admin -d smart_drainage -c "SELECT extname FROM pg_extension ORDER BY extname;"
 docker compose exec postgres psql -U drainage_admin -d smart_drainage -c "\\d+ sensor_readings"
 docker compose exec postgres psql -U drainage_admin -d smart_drainage -c "SELECT hypertable_schema, hypertable_name FROM timescaledb_information.hypertables;"

# 再验证迁移路径（应明确选择 init 或 Alembic，不能两条都当真）
cd backend
python -m alembic upgrade head
python -m alembic current
```

验收应比较 `pg_extension`、表/列/索引/约束、enum、hypertable 和 seed 集合；不能只检查 `alembic heads`。

### 10.2 Compose 运行与 readiness

```bash
docker compose config --quiet
docker compose up -d --build
 docker compose ps
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/api/health
curl -fsS http://localhost:8000/api/system/status
curl -fsS http://localhost:8080/health
curl -N http://localhost:8000/api/sse/events
```

水力版本应将 migration/worker readiness 与应用 liveness 分开；`/health` 浅层返回不能作为 AC-8 的数据库/扩展证明。

### 10.3 后端/前端质量门禁

```bash
cd backend
python -m pytest tests/ -v --tb=short
python -m black --check app tests
python -m isort --check-only app tests
python -m flake8 app tests
python -m mypy app --ignore-missing-imports

cd ../frontend
npm ci
npm test
npm run lint
npm run format:check
npm run build
```

当前已运行的对应结果见第 9 节；`npm ci` 未在本次执行。

### 10.4 水力专属数据库/worker 验收（待实现后）

- PostGIS：空库 extension、geometry 列、GiST 索引、bbox 查询、WKT/GeoJSON round-trip。
- 无 PostGIS：同一业务 API 在 WKT/GeoJSON 存储路径上返回等价拓扑和导入计数。
- Alembic：upgrade、重复 upgrade、downgrade/回滚、seed 幂等；与 init SQL 做 schema diff。
- Timescale：simulation frame 时间列 hypertable、90 天 retention policy、取消/重试/崩溃恢复后的 job 状态。
- Worker：并发 2、进度 0–100、取消、10 分钟超时、最多两次重试、幂等提交、20MB/路径穿越/清理。
- Observability：Prometheus 至少暴露 task duration、failure rate、queue depth，并能用 `simulation_id` 关联日志/指标/结果。

## 11. 契约风险清单（按优先级）

### P0：会导致全新部署或数据不可信

1. **PostGIS 扩展与函数调用不自洽**：init SQL 调用 `ST_*`，但没有 `CREATE EXTENSION postgis`（`docker/init-db.sql:4-9,76-79`）；需要真实空库验证并决定 PostGIS 镜像/安装方案。
2. **schema 三路漂移**：init SQL 有 sensor hypertable，Alembic/ORM 没有（`docker/init-db.sql:100-118` 对比 `0001_initial.py:20-228`、`db_models.py:46-295`）。必须选定唯一 schema 事实来源并让另一条路径可验证地一致。
3. **启动不执行迁移**：Compose 只挂 init SQL（`docker-compose.yml:19-22`），后端 Dockerfile 直接启动 Uvicorn（`docker/backend/Dockerfile.cpu:15-27`）；应用用 `create_all_tables()` 兜底（`main.py:132-141`）。这会导致新 volume、已有 volume、Alembic 升级结果不同。
4. **连接 URL 特殊字符**：`Dr@inage_S3cur3_2024!` 原样进入 async URL（`docker-compose.yml:81`），非 localhost 分支不做编码（`database.py:33-54`）；需先做 asyncpg/Alembic 真实连接测试。
5. **旧服务失败被静默降级**：数据库/Redis/Influx 失败后仍报告浅层 healthy（`main.py:132-169,302-305`），可能掩盖数据丢失和不可重跑。

### P1：水力功能上线前必须补齐

6. 没有 network/node/pipe/catchment/elevation 表、WKT/GeoJSON fallback、bbox CRUD、拓扑校验和导入白名单。
7. 没有 rainfall scenario、return period/step/custom duration、RainfallSource 和可复现 seed。
8. 没有 simulation job/frame/calibration/observation/A-B 表，也没有 durable worker/queue/retry/cancel/timeout/crash recovery。
9. 没有 forecast latest/horizon/risk-ranking、`flood_forecast`/`pipe_surcharge`、30 分钟抑制和 `simulation_id` 事件契约。
10. 没有 Timescale retention policy；Influx 90d 与 PostgreSQL sensor/仿真保留未统一。
11. Prometheus 只有 HTTP instrumentation 入口，没有 PRD 要求的任务时长/失败率/队列深度指标（`main.py:214-219`）。

### P2：运维与兼容性

12. 固定 secrets、宿主机暴露数据库/Influx/Redis 端口、旧 Grafana 密码（`docker-compose.yml:13-16,36-37,53-60,61-62,168-171`）需要 secret/网络隔离。
13. `docker-compose.yml:1` 的过时 version 字段触发 warning；应清理。
14. Dockerfile healthcheck 不检查状态码；应将 liveness/readiness 分离并检查迁移/扩展/worker。
15. CI 只覆盖 pytest、Vitest、镜像 build（`.github/workflows/ci.yml:32-93`），没有 compose 启动、数据库迁移一致性、PostGIS/Timescale、worker 恢复和 SSE 端到端测试。

## 12. 结论边界

- 本次已实际读取 Compose、两个后端 Dockerfile、init SQL、Alembic 配置/迁移、Settings、数据库生命周期、ORM、路由、SSE/Redis/Influx、worker、测试和 CI。
- 本次已实际运行 Compose 解析、Alembic heads、Python compile、git diff check、backend pytest、frontend Vitest/lint/format/build，以及 backend Docker build 尝试；结果按第 9 节原样记录。
- **没有运行成功**：Docker daemon 依赖的镜像构建/启动、数据库初始化、Alembic upgrade、PostGIS/Timescale live 检查、live health/API/SSE、后端全量 pytest。不能把未运行项目写成通过。
- README、CODE_WIKI、已有勾选清单仅用于定位/对照，不作为水力功能已经实现的证据。
