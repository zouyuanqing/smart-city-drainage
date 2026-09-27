# 后端与数据现状侦察

> 侦察对象：`smart-city-drainage` 当前工作树。基线 commit 为 `ca99e04`（`git log -1`：`chore: remove stray isort diff log`），侦察开始时 `git status --short --branch` 输出 `## main...origin/main`。
>
> 本文只把实际读到的代码/配置和本次运行的命令输出当作事实；仓库 README 中的架构描述仅作为对照，不当作实现证据。路径均相对于 `smart-city-drainage` 根目录，行号以本次读取的文件为准。

## 1. 结论摘要

1. **当前后端是“智慧城市监测 + AI 视频”应用，不是水力数字孪生后端。** FastAPI 已有生命周期、统一 `/api` 路由、Prometheus 暴露、JWT/RBAC、SQLAlchemy async 会话和 Redis/SSE 模式；但水力域模型、路由和求解器均不存在。实际 ORM 只有 `users`、`devices`、`camera_streams`、`alerts`、`model_versions`、`inference_results` 六张表（`backend/app/models/db_models.py:46-295`）。
2. **PostGIS/Timescale 现状不一致。** Alembic 只启用 `timescaledb`（`backend/alembic/versions/0001_initial.py:20-24`），不启用 PostGIS；`docker/init-db.sql` 创建 `sensor_readings` hypertable 并调用 `ST_MakePoint`，却没有 `CREATE EXTENSION postgis`（`docker/init-db.sql:4-9,76-79,100-118`）。ORM 没有 `SensorReading` 模型，且没有 `geometry`/`geography` 字段，只有设备的两个 `Float` 经纬度（`backend/app/models/db_models.py:84-117`）。
3. **传感器真实数据路径是 mock → InfluxDB，旁路 Redis/SSE。** `MockDataGenerator` 每轮生成读数、尝试写 Influx、广播 SSE、再发布 Redis（`backend/app/services/mock_data_generator.py:147-200`）；`InfluxDBService` 直接写/查 Flux（`backend/app/services/influxdb_service.py:70-212`）。后端没有传感器写入 API，也没有 MQTT/Modbus/OPC 等硬件接入端点；当前读数 API 在 Influx 无数据时回退到随机/模拟值（`backend/app/api/routes.py:589-649`）。
4. **迁移与启动路径没有形成单一事实来源。** 容器只挂载 `docker/init-db.sql`（`docker-compose.yml:19-22`），后端镜像命令直接启动 Uvicorn（`docker/backend/Dockerfile.cpu:15-27`），没有 `alembic upgrade head`；应用启动又会调用 `create_all_tables()`（`backend/app/main.py:132-141`）。`init-db.sql` 比 Alembic 多一张 `sensor_readings` 表，且已有字段类型漂移，详见第 5 节。
5. **现有 API 契约可作为扩展壳，但不能直接承载 PRD 的水力 API。** 静态路由统计为 30 个 HTTP 路由 + 1 个 WebSocket；不存在 `/api/networks`、`rainfall-scenarios`、`simulations`、`forecast` 或 SSE `forecast` 事件。许多设备/告警/传感器接口返回裸 `dict`，且设备 schema、传感器时间字段、告警 ID 形状存在前后端不一致（第 3、4 节）。
6. **本次未能证明后端可启动或全量测试通过。** 当前环境是 Python `3.14.4`，而项目声明/镜像目标是 Python 3.11；`prometheus-fastapi-instrumentator`、`pytest-asyncio`、`aiosqlite` 未安装。应用导入在 `prometheus_fastapi_instrumentator` 处失败；全量 pytest 在收集阶段因 `pytest_asyncio` 缺失失败；Docker daemon 不可用，因此没有运行数据库初始化、迁移或 compose 集成测试（第 8 节）。

## 2. 现有后端骨架与可复用模式

### 2.1 FastAPI 生命周期、路由和可观测性

- `backend/app/main.py:101-197` 使用 `asynccontextmanager` 管理启动/关闭：启动时初始化模型、数据库、Redis、InfluxDB、流健康监控和 mock 数据；关闭时停止 mock/流、断开 Influx/Redis、释放 SQLAlchemy engine。
- `backend/app/main.py:204-218` 创建 `FastAPI`，固定暴露 `/docs`、`/redoc`、`/openapi.json`，并用 `Instrumentator` 暴露 `/metrics`。请求 ID 与处理时间中间件在 `backend/app/main.py:225-258`。
- 路由统一挂在 `/api` 前缀下（`backend/app/api/routes.py:60-62`），可以复用作水力 API 的命名空间；但当前只有健康、认证、模型、推理、流、SSE、传感器、告警、设备和 mock 端点（路由装饰器统计见第 8 节）。
- 已有统一异常入口：参数验证返回 `422` 且包含 `detail` 与 `errors`（`backend/app/main.py:313-322`），未处理异常返回 `500`、`detail` 和异常类型（`backend/app/main.py:325-335`）。这可以作为新 API 的错误基线，但当前各业务函数还大量使用裸 `dict`/`HTTPException`，并没有统一响应模型。

### 2.2 配置模式

`backend/app/core/config.py:16-24` 使用 `pydantic-settings.BaseSettings`、`.env` 和大小写不敏感环境变量；数据库、InfluxDB、Redis、JWT、模型、媒体、CORS、上传和通知配置集中在同一 `Settings` 单例（`backend/app/core/config.py:26-120`）。可复用的是“所有运行参数从 Settings 注入”的模式，但当前没有 PRD 所需的仿真并发、重试/超时、求解器选择、结果保留期、nowcast 周期、风险窗口和 worker 沙箱配置；这些配置目前不存在于 `Settings`。

配置中的数据库/Redis/JWT 默认值包含固定凭据或密钥（`backend/app/core/config.py:33-61`），Compose 也把固定密码写入环境（`docker-compose.yml:13-16,30-35,80-89`）。这不是本次功能实现结论，但属于后续部署/契约风险：生产环境必须显式覆盖，且不应把默认值作为安全边界。

### 2.3 SQLAlchemy async 模式

`backend/app/core/database.py` 提供了可直接复用的 async 基础：

- `_build_safe_db_url()` 对连接串用户名/密码做 URL 编码（`backend/app/core/database.py:33-54`）；`get_engine()` 使用 `create_async_engine()`，配置连接池、pre-ping、recycle 和 debug echo（`backend/app/core/database.py:57-72`）。
- `async_sessionmaker` 配置 `expire_on_commit=False`、`autoflush=False`（`backend/app/core/database.py:79-94`）。
- `get_db()` 是 FastAPI 依赖，异常时 rollback（`backend/app/core/database.py:102-116`）；`get_db_session()` 是供业务代码直接使用的异步上下文管理器（`backend/app/core/database.py:119-129`）。
- `create_all_tables()` 明确标注“开发用，生产应使用 Alembic”（`backend/app/core/database.py:132-142`），但 `main.py:132-141` 在应用启动中实际调用它。这一模式应保留用于测试/开发，生产启动应改为迁移门禁。

当前路由没有使用 `Depends(get_db)`，而是在处理函数中反复 `async with get_db_session()`（例如登录 `backend/app/api/routes.py:93-110`、设备查询 `backend/app/api/routes.py:899-924`）。因此测试中的依赖覆盖不能替换这些直接调用（见第 8 节）。

### 2.4 ORM 与 DTO 模式

- `Base(DeclarativeBase)` 加 `Mapped[...]`/`mapped_column` 的 SQLAlchemy 2.0 声明式模式可复用（`backend/app/models/db_models.py:14-32`）。
- `Device`、`CameraStream`、`Alert` 已有删除级联/外键/关系定义（`backend/app/models/db_models.py:119-125,136-170,180-232`），可作为水力节点/管段外键和告警关联的参考，但现有关系不包含传感器或管网。
- Pydantic schema 已覆盖 Token、设备、告警、传感器、模型、推理和流（`backend/app/schemas/schemas.py:45-121,129-174,187-295`），但路由并未一致使用这些 response/request model；这是新水力 API 应避免复制的缺口。
- JWT/RBAC 的 `require_auth`、`require_role` 依赖工厂可直接复用于新写接口（`backend/app/core/security.py:95-135`）。角色定义为 `admin/operator/viewer`（`backend/app/models/db_models.py:35-38`）。

### 2.5 Redis/SSE 模式

- Redis 客户端封装连接、三个 channel、发布/订阅和简单缓存（`backend/app/core/redis_client.py:24-41,50-99,106-163`）。应用启动时把 alerts/sensors/model channel 映射到 SSE broadcast（`backend/app/main.py:143-152`）。
- SSE 管理器使用每客户端 `asyncio.Queue(maxsize=256)`，进程内保存最多 1000 条历史，并支持 `Last-Event-ID` 重放（`backend/app/services/sse_manager.py:32-43,52-76,90-120,142-189`）。这套模式可复用来推送 `forecast`，但当前历史只在单进程内存中，重启/多 worker 不共享；新预测事件若要求可靠重放，需要持久事件 ID/Redis Stream 或数据库事件表。
- 现有事件类型只有 `sensors`、`alerts`、`system` 和握手 `connected`（`backend/app/api/routes.py:393-413`；`backend/app/services/sse_manager.py:122-140,152-155`），没有 `forecast`。

## 3. 当前 API 契约

### 3.1 路由盘点

本次对 `backend/app/api/routes.py` 的静态统计命令：

```text
rg -c '^@router\.(get|post|put|delete|patch|websocket)' app/api/routes.py
# 31
rg -c '^@router\.(get|post|put|delete|patch)' app/api/routes.py
# 30
rg -c '^@router\.websocket' app/api/routes.py
# 1
```

| 范围 | 当前端点 | 证据 | 现状/水力影响 |
|---|---|---|---|
| 健康/状态 | `GET /api/health`、`GET /api/system/status` | `backend/app/api/routes.py:69-85` | 裸 dict；system status 的 Influx 检查见下方缺陷 |
| 认证 | `POST /api/auth/login`、`GET /api/auth/me` | `backend/app/api/routes.py:93-162` | TokenResponse 有明确 schema；登录有固定 fallback 管理员 |
| 模型/推理 | `/api/models/*`、`POST /api/inference/detect` | `backend/app/api/routes.py:170-307` | AI 视频域；`detect` 无认证依赖 |
| 流媒体 | `/api/streams/*` | `backend/app/api/routes.py:315-385` | RTSP/HLS 域；写操作 admin/operator |
| 实时 | `GET /api/sse/events`、`WS /api/ws/control` | `backend/app/api/routes.py:393-582` | 通用传感器/告警/控制，不是 forecast |
| 传感器 | `GET /api/sensors/latest`、`GET /api/sensors/history/{device_id}`、`GET /api/sensors/export` | `backend/app/api/routes.py:589-717` | 只读查询；无导入/写入 API |
| 告警 | `GET /api/alerts`、导出、确认 | `backend/app/api/routes.py:725-891` | 既有告警中心可复用；无 `flood_forecast`/`pipe_surcharge` 类型 |
| 设备 | `GET/POST/PUT/DELETE /api/devices` | `backend/app/api/routes.py:948-1031` | 设备 lat/lng，不是管网拓扑 |
| mock | `/api/mock/*` | `backend/app/api/routes.py:1039-1076` | 演示控制，无认证；不应成为生产传感器来源 |

在本次限定范围（`backend/app`、`backend/alembic`、`backend/tests`、`docker`）执行水力域关键词搜索：

```text
rg -ni '(network|rainfall|simulation|forecast|pipe|catchment|swmm|geojson|wkt|flood)' backend/app backend/tests
# 无输出
```

因此没有发现 PRD 要求的 `/api/networks`、`/api/rainfall-scenarios`、`/api/simulations`、`/api/forecast`，也没有 `networks`、`network_nodes`、`pipe_segments`、`catchments`、`elevation_profiles`、`rainfall`、`simulation`、`flood_forecast` 等实现符号。这里的“未发现”限定于当前仓库文件和 commit，不代表外部系统不存在。

### 3.2 请求/响应形状风险

1. **设备 schema 与实现字段名不一致。** `DeviceBase`/`DeviceCreate` 声明 `device_code`、`latitude`、`longitude`（`backend/app/schemas/schemas.py:79-95`），但 `create_device` 接收裸 `dict` 并读取 `code`、`lat`、`lng`（`backend/app/api/routes.py:955-971`）。因为路由没有使用 `DeviceCreate`，Swagger 不会给出上述强类型请求契约；同一个 API 的文档与实际 payload 不同。
2. **传感器时间字段不统一。** Pydantic `SensorReading` 要求 `time`（`backend/app/schemas/schemas.py:160-174`）；mock 和 Influx latest 返回 `timestamp`（`backend/app/services/mock_data_generator.py:217-229`、`backend/app/services/influxdb_service.py:150-164`），history 返回 `time`（`backend/app/api/routes.py:639-647`）。路由没有 `response_model`，所以 OpenAPI 不会稳定地表达这一区别。
3. **告警 ID 在 SSE 和 REST 间不一致。** REST 列表显式返回 `id`（`backend/app/api/routes.py:750-769`）；mock 告警/SSE 负载使用 `alert_id`（`backend/app/services/mock_data_generator.py:231-248`、`backend/app/services/sse_manager.py:132-140`）。前端 `Alert` 类型要求 `id`（`frontend/src/types/index.ts:45-60`），并在去重、确认、通知中直接使用 `alert.id`（`frontend/src/pages/AlertCenter.tsx:64-80,136-143`）。新 forecast 事件必须先固定事件 envelope 和目标 ID 规则，不能复制这种双形状。
4. **SSE 握手字段与前端期待不一致。** 服务端握手只发 `client_id`、`status`（`backend/app/services/sse_manager.py:152-155`），前端却读取 `sse_clients`（`frontend/src/hooks/useSSE.ts:66-70`）。这不会阻止连接，但说明现有事件契约未形成端到端 schema。
5. **写操作认证覆盖不一致。** 设备、流、告警确认、导出等写/敏感操作使用 `require_role`（例如 `backend/app/api/routes.py:315-320,864-870,955-959,986-1016`）；但 `/api/inference/detect`（`backend/app/api/routes.py:273-307`）和 mock 配置/启停（`backend/app/api/routes.py:1051-1076`）没有认证依赖。若按 PRD 将“写操作”泛化到新水力 API，应明确区分公开读、设备控制、管理员操作和模拟控制，不能按当前路由机械复制。
6. **异常/回退会隐藏数据层故障。** 设备和告警查询在数据库异常时回退到 mock（`backend/app/api/routes.py:899-945,730-800`），传感器历史在 Influx 无数据时生成随机序列（`backend/app/api/routes.py:625-649`）。这适合演示，但会让 API 的 `source`、可重复性和监控指标失去生产语义；水力导入/仿真/预测接口不应静默回退。

### 3.3 SSE/事件契约可复用点与限制

前端使用 `EventSource('/api/sse/events')`（`frontend/src/hooks/useSSE.ts:47-58`），后端支持 `Last-Event-ID`（`backend/app/api/routes.py:403-413`）。因此新增 forecast SSE 可以复用连接、心跳和事件分发模式；但当前事件历史是进程内列表（`backend/app/services/sse_manager.py:35-43,93-99`），没有跨 worker/重启的持久 replay，也没有 forecast 事件 schema、窗口抑制或 `simulation_id` 字段。

## 4. SQLAlchemy 模型现状

### 4.1 已有六张 ORM 表

`backend/app/models/db_models.py` 的声明如下：

- `User`（`46-76`）：UUID 用户名、bcrypt 密码、角色、最后登录。
- `Device`（`84-129`）：设备编码、类型、状态、纬度/经度/海拔、地址/区域、电池/信号、心跳和 JSON metadata。
- `CameraStream`（`136-172`）：设备关联的 RTSP/HLS/WebRTC 流。
- `Alert`（`180-235`）：设备告警、级别、快照、JSON bbox/metadata、确认/解决状态。
- `ModelVersion`（`243-268`）：AI 模型版本与状态。
- `InferenceResult`（`276-294`）：摄像头推理 JSON 结果。

`models/__init__.py:6-24` 只导出这六类模型；不存在传感器时序模型或水力域模型。

### 4.2 坐标与空间能力

- `Device.latitude`/`longitude` 是普通 `Float`，没有 PostGIS geometry（`backend/app/models/db_models.py:99-103`）。
- `Alert.bbox_coordinates` 是 JSON（`backend/app/models/db_models.py:207-221`），不是空间字段。
- 代码中没有 `geometry`、`geography`、WKT/GeoJSON 解析、管段拓扑、bbox 查询或网络连通性分析。对 `backend/app` 的坐标搜索只找到设备字段、AI 检测框和前端式 lat/lng 映射；没有 `bbox` API。

因此，当前“空间数据”最多是设备点坐标加一个由初始化 SQL 创建的表达式 GiST 索引，不能支撑 PRD 的 PostGIS 网络/汇水区/管线几何路径。

## 5. 迁移、初始化 SQL 与数据库一致性

### 5.1 Alembic 现状

- 仓库只有一个 revision：`backend/alembic/versions/0001_initial.py:14-17`；本次离线命令输出 `0001_initial (head)`、`Parent: <base>`。
- `backend/alembic/env.py:13-20` 从 `settings.DATABASE_URL` 覆盖 Alembic URL；`42-55` 使用 async engine 执行迁移。
- `0001_initial.py:20-24` 只创建 `uuid-ossp` 和 `timescaledb` 扩展；`25-228` 创建上述六张表；`230-232` 创建告警索引；`234-241` 插入固定管理员。
- `downgrade()` 只删表和 enum（`244-258`），没有删除 Timescale/uuid 扩展，也没有 sensor hypertable 的迁移可回滚。

`backend/alembic.ini:2-5` 的默认 URL 是同步 PostgreSQL URL，而运行时 `env.py:18` 覆盖为 async URL；迁移代码没有复用 `database.py:_build_safe_db_url()` 的 URL 编码逻辑。当前默认密码含特殊字符，且没有真实数据库执行验证，因此应在迁移门禁中显式校验连接串，而不能把离线 `alembic heads` 通过当成迁移可运行证明。

### 5.2 `init-db.sql` 与 Alembic/ORM 漂移

`docker/init-db.sql` 的实际表清单由本次静态脚本确认：

```text
orm_tables=users,devices,camera_streams,alerts,model_versions,inference_results
init_tables=users,devices,camera_streams,sensor_readings,alerts,model_versions,inference_results
migration_tables=users,devices,camera_streams,alerts,model_versions,inference_results
init_postgis_extension=False
migration_sensor_readings=False
migration_hypertable=False
```

具体差异：

| 项目 | ORM | Alembic | init SQL | 影响 |
|---|---|---|---|---|
| `users.role` | `Enum(RoleEnum)`（`db_models.py:58`） | `sa.String(32)`（`0001_initial.py:34-39`） | `VARCHAR(32)`（`init-db.sql:41-52`） | 迁移库与 ORM 生成 DDL 不同；需在空库验证 |
| `devices.install_date` | `DateTime`（`db_models.py:104`） | `DateTime()`（`0001_initial.py:73`） | `DATE`（`init-db.sql:65`） | schema 类型不一致 |
| JSON 字段 | 通用 `JSON`（`db_models.py:108,210,220`） | `postgresql.JSONB()`（`0001_initial.py:77,161,170`） | `JSONB`（`init-db.sql:70,129,135`） | ORM metadata 与迁移/初始化不一致 |
| `model_versions.file_size_bytes` | `Integer`（`db_models.py:251`） | `Integer`（`0001_initial.py:187`） | `BIGINT`（`init-db.sql:147`） | 容量/迁移类型漂移 |
| 传感器 hypertable | 无 ORM 表 | 无 `sensor_readings`、无 `create_hypertable`（`0001_initial.py:20-228`） | `sensor_readings` + hypertable + index（`init-db.sql:100-118`） | 绕过 Alembic 时数据库结构取决于 init SQL |
| PostGIS | 无 geometry 类型 | 无 `CREATE EXTENSION postgis` | 使用 `ST_SetSRID(ST_MakePoint(...))` 建索引（`init-db.sql:76-79`），但没有启用扩展 | 全新 compose 是否能执行该表达式未验证；空间能力不完整 |

`Base.metadata.create_all` 只会创建 ORM 六表；它不会根据 `init-db.sql` 创建 `sensor_readings`。反过来，init SQL 的表不会被后续 Alembic revision 记录。当前应用启动先执行 `create_all_tables()`（`backend/app/main.py:132-141`），而 Compose 首次启动只挂载 init SQL（`docker-compose.yml:19-22`），所以“空卷 compose”“Alembic 升级”“应用自建表”可能是三条不同结构路径。

### 5.3 Timescale/Influx/保留期现状

- Compose 使用 `timescale/timescaledb:latest-pg15`，同时单独启动 InfluxDB 2.7（`docker-compose.yml:8-10,48-71`）。
- Influx bucket 配置为 90 天保留（`docker-compose.yml:53-60`）；`docker/influxdb-setup.sh:10-30` 另建 365 天 `sensor_data_1h` bucket 和小时降采样任务。
- PostgreSQL 侧只看到 Timescale extension 和 `sensor_readings` 初始化表；没有 Timescale retention policy、仿真结果 hypertable 或 90 天清理任务。
- `InfluxDBService` 实际把传感器写入 Influx measurement `sensor_readings`（`backend/app/services/influxdb_service.py:70-124`），而不是 SQLAlchemy/Timescale 表。两者不是同一个可查询数据源。

## 6. 传感器、设备种子与数据流

### 6.1 现有数据流

1. `PRESET_DEVICES` 提供 8 个上海点位（黄浦、浦东、徐汇、杨浦、静安、长宁等），字段是 `id/code/name/lat/lng/district`（`backend/app/services/mock_data_generator.py:23-89`）。
2. `MockDataGenerator` 默认每 4 秒生成一轮；写 Influx（若连接）、SSE `sensors`、Redis sensor channel（`backend/app/services/mock_data_generator.py:107-115,147-200`）。
3. `GET /api/sensors/latest` 优先查 Influx，否则用同一 mock 生成器返回 `source: "mock"`（`backend/app/api/routes.py:589-614`）。
4. `GET /api/sensors/history/{device_id}` 优先查 Influx，否则生成随机历史点（`backend/app/api/routes.py:617-649`）。
5. `InfluxDBService.get_historical_readings()` 按小时窗口、分钟间隔聚合并返回 `time`（`backend/app/services/influxdb_service.py:171-212`）。

这可以复用作“合成源/演示源”适配器，但不应把 mock generator 当作真实传感器或降雨源。

### 6.2 设备种子与水力绑定缺口

`docker/init-db.sql:175-187` 插入 10 个设备（8 个井盖 + 2 个摄像头）；mock 常量只有 8 个井盖。两者 ID/类型没有水力网络、管段或汇水区外键。`Device` 关系只指向摄像头和告警（`backend/app/models/db_models.py:119-125`），所以当前只能按经纬度做空间关联，不能声明“设备绑定到某条管段/节点”。

### 6.3 输入安全与可重复性风险

- 历史查询把 `device_id` 直接拼进 Flux 字符串（`backend/app/services/influxdb_service.py:181-188`）；导出查询也直接拼 `start_time`、`end_time`、`device_id`（`backend/app/api/routes.py:665-674`）。这些参数没有看到白名单/参数化验证，导入新传感器数据前应先建立输入契约和恶意字符串测试。
- 传感器没有写入 API；仅有 service 方法（`backend/app/services/influxdb_service.py:70-124`）和 mock loop 调用（`backend/app/services/mock_data_generator.py:161-167`）。因此真实设备接入、批量上限、时间戳幂等和坏点隔离均未实现。
- `SensorReadingBatch` schema 允许最多 1000 条（`backend/app/schemas/schemas.py:176-180`），但没有路由使用它；这可以作为未来批量导入 DTO 的上限参考，不能视为已存在的 ingest 契约。

## 7. 现有测试与测试缺口

### 7.1 测试代码实际覆盖

- `backend/tests/conftest.py:9-20` 试图使用 `sqlite+aiosqlite:///./test.db` 创建/销毁 ORM 表；`34-42` 创建 ASGI client。
- API 测试只有认证、设备/告警/传感器基本 200、设备列表、健康/系统状态（`backend/tests/test_api/test_auth.py:3-25`、`test_devices.py:3-27`、`test_health.py:3-17`）。
- service 测试覆盖 SSE 单例/连接/广播和 mock 预设（`backend/tests/test_services/test_sse_manager.py:7-31`、`test_mock_generator.py:3-21`）。
- 没有迁移 vs init SQL、PostGIS/Timescale、传感器写入、RBAC 写操作、SSE 端到端、错误格式、导入恶意文件或水力域测试。

### 7.2 测试夹具风险

`tests/conftest.py:35-38` 把 `get_db_session` 放入 `app.dependency_overrides`，但业务路由是直接调用 async context manager，而不是 `Depends(get_db_session)`（例如 `backend/app/api/routes.py:97-106,899-905`）。因此该覆盖不能可靠替换路由实际数据库；测试可能落到配置中的 PostgreSQL，再因异常走 mock fallback。

此外，当前 `requirements.txt`/`requirements-cpu.txt` 没有 `aiosqlite`（依赖声明 `backend/requirements.txt:5-62`、`backend/requirements-cpu.txt:0-33`），而测试夹具硬编码该驱动；即使 pytest 插件安装成功，SQLite 夹具仍需补齐依赖并验证 PostgreSQL 专用 UUID/JSON 类型在 SQLite 的兼容性。

## 8. 本次实际运行的检查与结果

以下命令均在本次侦察中执行；`PYTHONDONTWRITEBYTECODE=1` 用于避免生成 `__pycache__`，pytest 使用 `-p no:cacheprovider` 避免写缓存。

### 8.1 语法与离线结构检查（通过）

从 `smart-city-drainage/backend` 执行：

```text
PYTHONDONTWRITEBYTECODE=1 python - <<'PY'
import ast, pathlib; root=pathlib.Path('app'); files=list(root.rglob('*.py')); [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in files]; print('parsed_python_files=' + str(len(files)))
PY
# parsed_python_files=25
```

```text
PYTHONDONTWRITEBYTECODE=1 python -m alembic -c alembic.ini heads
# 0001_initial (head)

PYTHONDONTWRITEBYTECODE=1 python -m alembic -c alembic.ini history --verbose
# Rev: 0001_initial (head)
# Parent: <base>
# Path: .../backend/alembic/versions/0001_initial.py
```

这只能证明 Python 源文件可解析、迁移图可读取；没有连接数据库，也没有执行 `upgrade`。

### 8.2 应用导入与依赖（未通过）

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=".../backend" python -c "from app.main import app; ..."
# Python 3.14.4
# ModuleNotFoundError: No module named 'prometheus_fastapi_instrumentator'
# exit 1
```

```text
python -m pip show pytest-asyncio prometheus-fastapi-instrumentator aiosqlite
# WARNING: Package(s) not found: aiosqlite, prometheus-fastapi-instrumentator, pytest-asyncio
# pip_show_exit=1
```

因此没有声称“后端启动通过”或“Swagger 已生成”。`pyproject.toml:14-16` 声明了 `asyncio_mode = "auto"`，但当前环境连 `pytest-asyncio` 都没有。

### 8.3 pytest（全量收集未通过；隔离 service 检查部分通过）

从 `backend` 执行：

```text
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider --collect-only tests
# ERROR: ImportError while loading conftest '.../backend/tests/conftest.py'.
# ModuleNotFoundError: No module named 'pytest_asyncio'
# exit 4
```

为区分依赖问题和 service 本身，执行了不加载上层 conftest 的隔离命令：

```text
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider --confcutdir=".../backend/tests/test_services" tests/test_services
# ...FFF
# 3 failed, 3 passed, 4 warnings in 0.35s
# 失败原因：async def functions are not natively supported; pytest-asyncio 未安装
```

这不是全量测试通过；全量 API 测试未执行。预期的项目测试命令仍是：

```text
cd smart-city-drainage/backend
python -m pytest -q
```

本次未用安装依赖或修改环境的方式绕过该问题。

### 8.4 Compose 语法（通过）与容器运行（未执行）

```text
docker compose -f ".../smart-city-drainage/docker-compose.yml" config --quiet
# warning: ... the attribute `version` is obsolete ...
# docker_compose_config_exit=0
```

这只证明 Compose 文件可解析，不证明镜像能启动、init SQL 可执行或数据库健康。检查本机镜像时：

```text
docker image inspect timescale/timescaledb:latest-pg15 --format '{{.RepoTags}}'
# failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine
# image_inspect_exit=1
```

因此本次没有运行 `docker compose up`、`alembic upgrade head`、PostGIS/Timescale SQL 或 live API 请求；这些检查均标记为**未运行**，不是通过。

## 9. 与 PRD 的差距矩阵

| PRD 域 | 当前代码证据 | 缺口/建议验收前置 |
|---|---|---|
| FR-1 管网数据层 | ORM 只有六张监测/AI 表（`db_models.py:46-295`）；没有 geometry、拓扑、bbox API | 新增 networks/nodes/segments/catchments/elevation_profiles 的迁移与 ORM；先解决 PostGIS 可用/无 PostGIS WKT/GeoJSON 双路径、校验报告和导入白名单 |
| FR-2 降雨 | `rg` 在 backend/app/tests 未发现 rainfall/scenario/return-period 符号 | 独立 `RainfallSource`、重现期/步长约束、可复现随机种子、情景 CRUD/复制；不要复用 mock sensor generator 作为降雨事实源 |
| FR-3 仿真 | 只有 `inference_worker.py` 的视觉推理 worker；无 hydraulic simulation/queue/progress/result frame 表 | 新增仿真任务/帧/率定/实测比对模型；队列幂等、取消、超时、最多两次重试、并发 2、worker 沙箱和 90 天保留需可观测且可恢复 |
| FR-4 预测告警 | Alert enum 仅已有 water_accumulation 等类型（`db_models.py:189-201`）；SSE 仅 sensors/alerts/system | 定义 `flood_forecast`/`pipe_surcharge` schema、`simulation_id`/lead time/confidence、Top-N、30 分钟抑制和 forecast SSE 事件 |
| FR-5 API | 现有 `/api` 路由及 JWT/RBAC 壳可复用（`routes.py:60-62`、`security.py:108-135`） | 新增 networks/import/validate/export/seed、rainfall-scenarios、simulations、forecast；所有响应应有稳定 Pydantic model，错误 envelope 统一 |
| FR-6 前端数据依赖 | 前端当前只消费设备/传感器/告警 API（`frontend/src/services/api.ts:49-180`） | 新增管网/仿真/预测类型与 API 适配时先冻结字段和 ID/时间格式，避免现有 `id`/`alert_id`、`time`/`timestamp` 漂移 |
| FR-7 部署数据 | Compose 有 Timescale+Influx，但无 PostGIS 启用、迁移命令或 worker service（`docker-compose.yml:8-109`） | 选定唯一 schema 门禁（Alembic 或 init SQL），让两者结果一致；显式配置 PostGIS/降级、worker、健康检查、保留期和求解器 |

对应 AC-1/2/3/4/5/6/7/8 在当前仓库没有可执行证据；不能把现有 mock 测试或 Compose 语法检查当作这些 AC 的通过。

## 10. 关键文件与符号索引

| 文件 | 关键符号/位置 | 用途与判断 |
|---|---|---|
| `backend/app/main.py` | `lifespan` 101-197；`app` 204-218；异常处理 313-335 | 应用生命周期、指标、启动建表、错误契约 |
| `backend/app/api/routes.py` | `router` 60；认证 93-162；SSE 393-413；传感器 589-717；告警 725-891；设备 948-1031 | 当前全部 REST/实时契约；水力路由不存在 |
| `backend/app/core/config.py` | `Settings` 16-145 | pydantic-settings；需扩展水力运行参数 |
| `backend/app/core/database.py` | `get_engine` 57；`get_db` 102；`get_db_session` 119；`create_all_tables` 137 | async engine/session 可复用；生产建表路径有风险 |
| `backend/app/models/db_models.py` | `Base` 29；六模型 46-295 | 当前持久化边界；无水力/传感器 ORM |
| `backend/app/schemas/schemas.py` | `Token*` 45-72；设备/告警/传感器 79-180 | 已有 DTO 可参考，但路由使用不一致 |
| `backend/alembic/env.py` | `run_async_migrations` 42-55 | async Alembic 模式 |
| `backend/alembic/versions/0001_initial.py` | `upgrade` 20-241；`downgrade` 244-258 | 单一初始迁移；缺 sensor/hypertable/PostGIS |
| `docker/init-db.sql` | 扩展 4-9；空间索引 76-79；sensor 100-118；种子 175-187 | Compose 初始化事实来源，但与迁移/ORM 漂移 |
| `backend/app/services/influxdb_service.py` | 写读 70-212 | 当前传感器时序实现；Flux 参数需收紧 |
| `backend/app/services/mock_data_generator.py` | `PRESET_DEVICES` 23-89；loop 147-200 | 合成源/SSE/Redis 演示模式 |
| `backend/app/services/sse_manager.py` | `connect` 52；`broadcast` 90；`event_generator` 142 | 可复用事件分发；内存 replay 限制 |
| `backend/app/core/redis_client.py` | channel/连接 50-120 | 跨服务 pub/sub 模式 |
| `backend/app/core/security.py` | `require_auth` 108；`require_role` 118 | 新写 API 的认证模式 |
| `backend/tests/conftest.py` | 9-42 | 当前测试数据库与 ASGI client；覆盖方式不对路由生效 |
| `docker-compose.yml` | postgres 8-28；influx 48-71；backend 73-109 | 现有部署拓扑；未含迁移/worker/PostGIS 门禁 |
| `docker/backend/Dockerfile.cpu` | Python 3.11 与启动 0-27 | 项目目标运行时；本次本机环境不是该镜像 |

## 11. 建议的后续侦察/实现顺序（仅记录，不在本次修改源码）

1. **先定 schema 门禁：** 决定 Alembic 是否成为唯一 PostgreSQL/Timescale/PostGIS 事实来源，明确 init SQL 是生成物还是弃用路径；补一份空库 `upgrade head` 与 Compose 初始化的可重复检查。
2. **再定空间双路径：** 明确 PostGIS geometry 列/索引与无 PostGIS 的 WKT/GeoJSON 存储映射，先锁定 `networks`、节点/管段外键、bbox 查询和设备绑定规则。
3. **随后定 DTO/错误 envelope：** 复用现有 JWT/RBAC、`async_sessionmaker`、SSE/Redis，但为水力 API 全部加 response model；统一 `id`、ISO 时间、错误 `detail` 和 SSE envelope。
4. **最后接入队列/worker/预测：** 把传感器与仿真帧写入 Timescale（若选择该路径），将 Influx 现状隔离为 legacy adapter；为任务进度、取消、重试、保留期和 forecast 事件补 Prometheus 指标与端到端测试。

本报告没有修改源码、迁移、Compose 或测试；唯一写入是本文件。
