# 水力孪生运维与数据契约（Operations Contract）

> **状态**：**SUPERSEDED / NON-NORMATIVE — 不得作为运维实现契约**。本文件已被 `../PRD_SUPPLEMENT.md` 与 `../IMPLEMENTATION_CONTRACT.md` 的整改候选 v2 取代，仅保留首轮运维审查轨迹。
>
> **基线**：`smart-city-drainage@ca99e04`。2026-09-24 设计审查未批准 v1。
>
> **强制替换**：`wkt`→`portable`（仍含 Timescale）；solver mode→v2 solver ID/auto|explicit；simulation/import API 直接执行→durable workers；`forecast_runs/events`→`nowcast_runs/event_outbox`；旧 G-0/G-1 与 AC-4 pytest 路径→仓库根 `scripts/verify_hydraulic_stack.py` 冻结 entrypoint；DELETE→archive；新增 issued_at/freshness/artifact/JWT-fallback-RBAC-rate-limit 门禁。
>
> 以下旧段落凡与两份候选主文档冲突的表名、状态、路径、profile、gate 和安全语义均无规范效力。

## 1. 审查结论

### 1.1 必须在实现前解决的阻断项

1. **PostGIS 当前不是可声称可用的数据库能力。** Compose 使用未固定 digest 的 `timescale/timescaledb:latest-pg15`（`docker-compose.yml:8-21`），而 `docker/init-db.sql:4-9` 只启用 TimescaleDB/uuid-ossp，`docker/init-db.sql:76-79` 却调用 `ST_SetSRID/ST_MakePoint`。本次只读比较得到 `init_postgis=False`、`migration_postgis=False`；不能由 `timescale` 镜像名称推断已安装 PostGIS。
2. **Schema 有三条不一致的创建路径。** `docker/init-db.sql:100-117` 独有 `sensor_readings` hypertable，Alembic `backend/alembic/versions/0001_initial.py:25-228` 只有六张旧表，ORM `backend/app/models/db_models.py:46-295` 也只有六张旧表；应用启动还在 `backend/app/main.py:132-141` 调 `create_all_tables()`。因此现状不能证明 AC-8 的空卷、迁移和启动一致。
3. **不存在水力 API、任务队列或仿真 worker。** 本次执行路由统计得到 `http_routes=30`、`websocket_routes=1`，`networks/rainfall-scenarios/simulations/forecast` 路由声明数均为 `0`；水力关键词扫描得到 `hydraulic_keyword_hits=0`。Compose 服务只有 `redis influxdb postgres backend nginx`，没有 worker（本次 `docker compose -f smart-city-drainage/docker-compose.yml config --services` 输出）。现有 `InferenceWorker` 是视频 YOLO worker（`backend/app/workers/inference_worker.py:44-100,191-228`），Redis 只有 Pub/Sub/KV（`backend/app/core/redis_client.py:50-164`）。
4. **现有 SSE 不能作为可靠 forecast 事实源。** `backend/app/services/sse_manager.py:32-43,90-120,142-189` 的历史是单进程内存最多 1000 条，队列满即丢事件；Redis 发布也不是持久 outbox。现状报告还记录了 producer 先本地广播再 Redis 发布的重复路径（`recon/realtime-security.md:55-81`）。
5. **现有质量/启动基线未闭环。** 六份报告一致指出：Docker daemon 未运行，未执行真实 `compose up`、Alembic upgrade、PostGIS/Timescale 检查；质量报告记录后端 pytest 收集失败、前端 Vitest `6/8`、`format:check` 失败、真实启动因 `logs/app.log` 父目录不存在而失败（`recon/quality.md:20-28,208-229,380-391`）。这些不是本次重跑结果，因此本契约把它们作为“发布阻断”，不是“当前通过”。

### 1.2 目标可行性判断

| 目标 | 判断 | 约束/解释 |
|---|---|---|
| PostGIS + 无 PostGIS 两条路径 | **可行，但必须做 profile-specific Alembic** | 逻辑 schema 共用；空间列/索引按 profile 条件迁移。不能让同一数据库运行时悄悄切换。 |
| Timescale 90 天结果保留 | **可行** | 只对 `simulation_frames`（以及明确声明的 observation/frame 时序表）设置 retention；simulation/alert 元数据留在普通 PostgreSQL。 |
| 纯 Python 180s、地图 25fps | **可验收，当前不可证明** | 必须在固定 4 核、固定 fixture、固定浏览器环境运行；没有基准脚本就不能宣称达标。 |
| EPA SWMM 在 Linux Compose 中“优先” | **不可直接保证** | Windows 可执行文件和许可证/版本需显式安装；Compose 默认使用已验证的纯 Python adapter，Windows 部署可显式启用 SWMM。 |
| 20MB 作为“沙箱全部输出” | **与 10,000 管段、24h、5min 帧目标存在冲突** | 本契约将 20 MiB 定义为单个源文件/输入物料预算，输出另设可配置上限；若产品坚持把输出也限制为 20 MiB，应退回范围或改需求。 |
| 生产数据可无损 `alembic downgrade` | **不能对所有已写入数据承诺** | 采用 additive/expand-contract 和应用回滚；schema downgrade 只在无生产水力数据或显式备份后执行。 |

## 2. 不可变决策

以下决策是实现、测试和发布的非协商边界；实现者不得以“临时兼容”替换。

### D-001：Alembic 是唯一 schema 事实源

- 所有 `hydraulic` 表、索引、约束、Timescale hypertable、retention/compression policy、seed revision 都由 Alembic 管理。
- `docker/init-db.sql` 降为**扩展 bootstrap**：只创建/确认 `timescaledb`、`uuid-ossp`，PostGIS profile 另外创建/确认 `postgis`；不得在其中创建业务表、enum、hypertable 或插入用户/设备种子。
- Compose 新增一次性 `migrate` service；它成功退出（`service_completed_successfully`）后 backend、scheduler、worker 才可启动。backend 启动不得再调用 `Base.metadata.create_all()`；`create_all_tables()` 仅保留给明确标记的单元测试 fixture。
- 迁移前必须将现有 `sensor_readings` 漂移收敛到 Alembic/ORM 契约，或明确把它标成 legacy-only 并在同一 revision 中记录；不能让 init SQL 继续偷偷创建它。
- 生产 readiness 校验：`alembic current` 等于 `HYDRAULIC_SCHEMA_REVISION`，数据库 profile 与配置一致；不一致返回非 ready，不进入静默 mock 模式。

**依据**：当前 Alembic 只启用 uuid/Timescale（`backend/alembic/versions/0001_initial.py:20-24`），init SQL 额外调用空间函数并创建 hypertable（`docker/init-db.sql:4-9,76-79,100-117`），启动仍 `create_all`（`backend/app/main.py:132-141`）；报告的差距矩阵也要求先锁定 schema 门禁（`recon/backend-data.md:298-310`、`recon/deployment.md:121-129`）。

### D-002：双空间 backend 采用“共用逻辑 schema + profile 条件 DDL”

- 配置只接受 `HYDRAULIC_SPATIAL_BACKEND=postgis|portable`，默认演示 profile 为 `postgis`；数据库 profile 写入 `hydraulic.schema_profile`，启动后不可原地切换。
- 两种 profile 共用相同逻辑表、列、ID、拓扑、bbox 语义和 API DTO。永久存储的规范几何是 WKT（`geom_wkt`）加有限数值 bbox；portable 仍使用 PostgreSQL + TimescaleDB。
- `postgis` profile 在相同表上增加由 `geom_wkt` 生成的 geometry 列和 GiST 索引：节点为 `geometry(Point,4326)`，管段为 `geometry(LineString,4326)`，汇水区为 `geometry(Polygon|MultiPolygon,4326)`。`portable` profile 不创建这些列/索引，使用预计算 bbox 做候选过滤。
- PostGIS 镜像必须使用同时包含 TimescaleDB 与 PostGIS 的、**按 digest 固定**的镜像；禁止 `latest`。当前 `timescale/timescaledb:latest-pg15` 不得作为 AC-8 证据。
- PostGIS profile 缺扩展或 profile 不匹配时，hydraulic readiness 失败并返回 `SPATIAL_BACKEND_UNAVAILABLE`；不允许悄悄降级成 WKT。需要 WKT 时部署独立的 portable profile。
- `bbox(min_lon, min_lat, max_lon, max_lat)` 必须校验有限数、经度 `[-180,180]`、纬度 `[-90,90]` 并规范化 min/max。portable 的 bbox 查询是候选集，不宣称与 PostGIS 空间谓词逐行完全相同；两者必须通过同一 fixture 的 API ID/计数契约测试。

**替代方案及取舍**：

1. **单一 PostGIS schema**：实现简单，但不满足无 PostGIS 降级；拒绝。
2. **全量 WKT、无 PostGIS 加速**：最易回滚，但不满足 PostGIS 路径和空间索引要求；拒绝。
3. **推荐的 profile-specific Alembic**：保留逻辑兼容，同时能真实验证两种数据库；采用。

### D-003：Timescale 只承载有明确时间轴的数据

- 使用 Timescale hypertable：
  - `hydraulic.simulation_frames`：每行一个 `frame_time` + `simulation_id`，包含 nodes、pipe_segments、surface_flooding 三个 payload；
  - `hydraulic.observation_samples`：用于率定/预测实测比对的规范化观测；
  - 现有 `sensor_readings` 若保留，必须单独声明其 Timescale 身份和保留策略。
- `simulation_frames` 的唯一键为 `(simulation_id, frame_index, frame_time)`，时间列 `frame_time` 是 Timescale partitioning key；每个 payload 带 `schema_version`、`frame_time`、`frame_index`、数组 count 和 SHA-256。节点/管段/水面对象不得只存在 worker 内存。
- 默认输出帧间隔为 300 秒或更小；首帧 `t=0`，24h AC fixture 应产生 `0,300,...,86400` 共 289 个时间点。每个时间点三类状态在同一 payload 中可查询，不能用“最后进度百分比”代替结果。
- `simulation_frames` 开启按 `simulation_id` 的压缩（启用时间由配置控制），90 天 retention 使用 Timescale retention policy；元数据、摘要指标、日志和告警引用不随 chunk 删除。过期结果查询返回 `410 FRAME_RETENTION_EXPIRED`，仍返回 simulation 摘要和 `finished_at`。
- 输出大小有独立上限：默认单帧 64 MiB、单个 simulation 8 GiB，可由 `SIMULATION_FRAME_MAX_BYTES`/`SIMULATION_OUTPUT_MAX_BYTES` 调低或调高。超限是非重试 `OUTPUT_LIMIT_EXCEEDED`，不能提交“部分成功”。
- 20 MiB 只约束上传源文件和 worker 输入物料；它不覆盖 solver 输出。否则 AC-3 的大规模帧序列没有可实现的存储空间。

**依据**：当前 `sensor_readings` 只在 init SQL 中 hypertable 化（`docker/init-db.sql:100-117`），Alembic/ORM 没有该实体（`backend/alembic/versions/0001_initial.py:25-228`、`backend/app/models/db_models.py:46-295`）；现有 Influx bucket 的 90d 不能代替 Timescale 结果保留（`docker-compose.yml:48-60`、`recon/simulation.md:180-185`）。

### D-004：PostgreSQL 任务表 + outbox 是队列事实源，Redis 只做通知

不新增 Celery/RQ/Arq 等产品。现有 Redis 客户端只有 publish/subscribe/get/set/delete（`backend/app/core/redis_client.py:50-164`），不能承担任务事实源。

- API 在一个数据库事务中写入 `hydraulic.simulations`、幂等记录和必要 outbox；事务提交后返回 `202`。forecast 使用 canonical `hydraulic.event_outbox`。
- worker 用 `SELECT ... FOR UPDATE SKIP LOCKED` 领取任务，并以 `hydraulic.worker_slots` 的两行（slot 1/2）强制全局并发上限为 2；不能以“启动两个进程”替代数据库约束。
- Redis 丢失只会延迟 SSE/唤醒，不丢 simulation、progress、forecast 或 alert；API 的 GET 状态是事实源，Redis 恢复后由 outbox relay 补发。
- outbox relay 是单一发布路径：先提交 durable event，再通过 Redis 通知各 API 进程；禁止 producer “本地 direct broadcast + Redis publish”双发。事件落库后才允许 SSE 发送。
- scheduler 单独作为 singleton（PostgreSQL advisory lock）运行 nowcast/retention 检查，不把定时器放在 API 进程生命周期中。

### D-005：simulation 状态、租约和重试语义冻结

状态只允许：

```text
queued -> leased -> running -> completed
queued/leased/running -> cancelling -> cancelled
running -> timed_out
leased/running -> retry_wait -> leased -> running ...
queued/leased/retry_wait -> failed
```



- `progress` 是数据库中的单调 `0..100` 小数/整数；`0` 表示已接受，`1..94` 表示求解/写帧，`95` 表示最终校验，`100` 只有在所有帧和摘要事务提交后才出现。事件最多每 1% 或每 5 秒写一次，GET 读取最后已提交值。
- 取消只写 `cancel_requested_at`；worker 在 heartbeat（最长 1 秒）内停止 solver、杀掉进程树、清理目录并提交 `cancelled`。已 `completed/timed_out/failed` 的取消返回 `409 SIMULATION_NOT_CANCELLABLE`；已 `cancelled` 重复取消幂等返回 200。
- 600 秒是整个 attempt 的 wall-clock 上限，包含 materialize、solver、帧序列化和提交前校验；超时是 `timed_out`，**不自动重试**。用户取消、输入错误、输出超限也不重试。
- worker 崩溃、租约过期、临时数据库/网络错误可重试；总尝试次数为 3（首次 + 最多两次重试），超过后 `failed`。不能让一次 10 分钟超时再重试两次而把 SLO 变成 30 分钟。
- 领取时生成不可预测的 `lease_token`/`fencing_token`，heartbeat 每 10 秒更新 `lease_until=now()+30s`；所有状态、帧和结果写入都带 fencing 条件。旧 worker 即使恢复也不能提交结果。
- 同一 `Idempotency-Key` + 同一请求 hash 在 24 小时内返回原 `simulation_id`；同 key 不同 hash 返回 `409 IDEMPOTENCY_KEY_REUSED`。`simulation_id`、输入 hash、network revision、solver 名称/版本都写入结果元数据。
- network revision 在提交时冻结；worker 开始时 revision 不一致则 `NETWORK_REVISION_CONFLICT`，不重试。rainfall scenario 一旦被 simulation 引用即不可原地修改，复制/编辑产生新版本。

### D-006：solver adapter 接口和 EPA/纯 Python 选择

实现必须提供 `IMPLEMENTATION_CONTRACT.md:5.2` 的稳定接口；`solver_selection=auto|explicit`，实际 `solver_id` 在提交事务内 probe 并冻结。首轮 `solver_mode=auto|epa_swmm|python` 语义已废止。

- `SolverProbe` 报告 `solver_id/version/hash` 与能力；运行后禁止同一 simulation 换 solver。
- 纯 Python adapter 实现 `py_mw|py_dw|py_ponding`，输出相同 frame protocol；不能把视频 worker/mock sensor 冒充 solver。
- EPA 进程只能由 worker 用参数数组启动，`shell=False`；输入只来自已验证的 task sandbox，输出只能写入 task sandbox。Windows 用 Job Object/进程树清理，POSIX 用独立进程组；取消和超时都必须验证没有孤儿进程。
- `auto` 的 fallback 必须在 AC-3 报告中显式标注；否则“优先 EPA”被误读为“必定 EPA”。

### D-007：文件所有权、输入白名单和沙箱预算

#### 物理目录所有权

| 根目录/对象 | 唯一写入者 | 可读者 | 生命周期 |
|---|---|---|---|
| `HYDRAULIC_IMPORT_ROOT/quarantine/<upload_id>.part` | import service | import validator | 未完成文件最长 24h；异常 finally 删除 |
| `HYDRAULIC_IMPORT_ROOT/accepted/<import_id>/source.*` | import service | materializer（只读） | 成功导入后按配置保留 30d；只存服务端生成名 |
| `HYDRAULIC_SANDBOX_ROOT/<simulation_id>/attempt-<n>/` | worker | 当前 attempt 的 solver | 终态后最长 24h；不得被 API 直接访问 |
| `HYDRAULIC_EXPORT_ROOT/<export_id>/` | export service | 下载服务 | 默认 24h；到期清理 |
| `MODEL_STORAGE_PATH` | 现有 model manager | model manager | 与 hydraulic 文件完全隔离；不得复用其路径拼接逻辑 |

- 客户端文件名只用于展示和安全日志，绝不作为最终路径。最终目录名是 UUID/服务端 ID；`resolve()` 后必须仍在对应 root，Windows `..\`, `/`, 盘符、UNC、ADS、symlink/junction 全部拒绝。
- 只接受 `.inp`、`.geojson`；不接受 zip、pickle、模型、脚本、外部 URL。扩展名、magic/编码、section/token/feature 数量、坐标深度和有限数校验缺一不可。
- 单个上传文件和 materialize 后的输入物料各不超过 20 MiB；读取到 `limit+1` 立即中止并删除。worker 启动前检查输出上限和可用磁盘（至少 `max(1GiB, 2*SIMULATION_OUTPUT_MAX_BYTES)`）。
- `finally`、取消、超时、worker 启动恢复都必须清理 attempt 目录。清理失败不能重新排队 solver；记录 `cleanup_pending`、告警并由启动 sweeper 重试。
- API 不返回绝对路径；下载通过带权限检查的 export ID。simulation 失败不保留可被用户读取的原始文件。

### D-008：API、幂等、错误和 forecast 事件

- 既有路由前缀 `/api`（`backend/app/api/routes.py:60-62`）和 JWT 角色继续使用；水力 endpoint 不得复制当前无认证的 `/mock/*`（`routes.py:1052-1077`）。
- 角色矩阵：viewer 只读 forecast/result/frame；operator 可复制 rainfall、提交/取消 simulation、确认 forecast alert；admin 可删除、seed、网络导入和配置管理。SSE/WS 必须在服务端校验角色。
- API 最小集合：

```text
GET/POST/PUT/DELETE /api/networks
POST /api/networks/{id}/validate
POST /api/networks/import
GET  /api/networks/{id}/export
POST /api/networks/{id}/seed
GET/POST/PUT/DELETE /api/rainfall-scenarios
POST /api/simulations                 # Idempotency-Key 必填，返回 202
GET  /api/simulations/{id}
POST /api/simulations/{id}/cancel
GET  /api/simulations/{id}/result
GET  /api/simulations/{id}/frames
GET  /api/simulations/{id}/observed-comparison
GET  /api/simulations/{id}/calibration
GET  /api/forecast/latest
GET  /api/forecast/horizon
GET  /api/forecast/risk-ranking
GET  /api/forecast/events             # Authorization Bearer fetch-SSE
```

  - production 开启 forecast API/worker 前必须通过 G2：锁定 JWT alg 并校验 iss/aud/exp/role、fallback admin=false、后端 RBAC、每 endpoint rate limit、401/403/429 与日志脱敏负向测试。

```json
{
  "schema_version": 1,
  "event_id": "uuid",
  "sequence": 123,
  "event_type": "forecast",
  "occurred_at": "2026-09-24T10:00:00Z",
  "network_id": "uuid",
  "simulation_id": "uuid",
  "data": {
    "alert_id": "uuid",
    "alert_type": "flood_forecast",
    "phase": "predicted",
    "target_type": "node",
    "target_id": "uuid",
    "expected_at": "2026-09-24T10:35:00Z",
    "lead_time_minutes": 35,
    "depth_m": 0.42,
    "confidence": 0.86
  }
}
```

  - 旧 `/api/sse/events` 的 `sensors/alerts/system/connected` envelope 保持兼容；新 forecast SSE 使用版本化 envelope。
  - `Last-Event-ID` 对应 durable `event_outbox.sequence`，不从进程内计数器生成；重放至少覆盖 7 天。
  - 未知 `schema_version` 只能忽略并记录 metric，不得把缺字段对象写入旧 Alert store。现有 ID 漂移（REST `id`、mock/SSE `alert_id`，见 `recon/backend-data.md:90-101`）必须用 joint fixture 固化。
- 业务错误保持现有 FastAPI 422/500 基线：422 仍包含 `detail` 与 `errors`（`backend/app/main.py:313-322`）；新增业务错误额外带 `code` 和可选 `report_id/simulation_id`。禁止把新的 `detail` 改成破坏旧前端的结构。
- nowcast 默认每 10 分钟运行，horizon 0–2h、5 分钟步长；同一 `network_id + forecast_time` 只能有一个 run。Top-N 风险按预计深度/置信度/目标类型稳定排序，分页和 tie-breaker 必须确定。
- `flood_forecast`/`pipe_surcharge` 告警持久化后再 outbox 发送。30 分钟活动 episode/终态冷却的 suppression key 固定为 `forecast-alert-v1|network_id|alert_type|target_type|target_id|rule_id`；phase 不进入 key，但 predicted/observed 可分别成为活动事件。终态后半开窗 `[terminal_at,terminal_at+30m)` 不创建第二业务事件/通知。

### D-009：可观测性和 readiness

- 保留 `/metrics`，增加：`simulation_queue_depth`、`simulation_duration_seconds`、`simulation_failures_total`、`simulation_retries_total`、`simulation_cancelled_total`、`simulation_timeout_total`、`solver_fallback_total`、`forecast_run_latency_seconds`、`forecast_alert_suppressed_total`、`hydraulic_event_publish_lag_seconds`、`hydraulic_cleanup_pending_total`。
- 每条 simulation/forecast 日志必须含 `simulation_id`、`attempt`、`solver`、`solver_version`、`network_id`；不得记录密码、JWT、绝对 sandbox 路径或完整输入内容。
- `/health` 只能表示进程 liveness；新增 `/readyz` 检查数据库连接、schema revision/profile、Timescale extension、PostGIS（按 profile）、outbox relay、worker slot 可用性和 migration 状态。任一失败返回 503，API 不能返回 mock healthy。
- Compose 的 Redis healthcheck 必须带认证；backend healthcheck 必须检查 HTTP status；worker 有独立 `/healthz`（liveness）和 `/readyz`（能 claim/heartbeat）。现有 root health 永远返回 healthy（`backend/app/main.py:302-305`），不能直接复用为 AC-8 readiness。

## 3. 逻辑数据契约

以下是逻辑表名；默认物理 schema 为 `hydraulic`，API 不暴露 schema 前缀。所有 UUID 为 v4；所有时间为带时区 UTC；所有 hash 为小写 SHA-256 hex。

| 表 | 必要字段/约束 | 所有权与用途 |
|---|---|---|
| `hydraulic.networks` | `id PK`, `name`, `status`, `current_revision_id`, timestamps；DELETE 只 archive | data/import；network revision 参与 simulation 冻结 |
| `hydraulic.network_nodes` | `id PK`, `network_id FK`, `code`, `x`,`y`,`invert_m?`, `geom_wkt`, `min_lon/min_lat/max_lon/max_lat`, `UNIQUE(network_id,code)` | data/import；postgis profile 另有 generated Point/GiST |
| `hydraulic.pipe_segments` | `id PK`, `network_id FK`, `upstream_node_id/downstream_node_id`, `length_m>0`, `diameter_mm>0`, `roughness>=0`, `geom_wkt`, bbox, `UNIQUE(network_id,external_id)` | data/import；端点必须属于同一 network，由 repository validator 保证 |
| `hydraulic.catchments` | `id PK`, `network_id FK`, `outlet_node_id`, `area_m2>0`, `geom_wkt` Polygon/MultiPolygon, bbox | data/import；postgis profile generated Polygon/GiST |
| `hydraulic.elevation_profiles` | `id PK`, `network_id/node_id FK`, `station_m`, `invert_m`, `crown_m?`, `UNIQUE(node_id,station_m)` | data/import；可选 |
| `hydraulic.device_network_bindings` | revision-scoped target、mapping version、valid revision range、active；不可最近点自动激活 | seed/observation；不改 legacy `devices` 表 |
| `hydraulic.rainfall_scenarios` | `id PK`, `network_id FK`, `name`, `return_period_years?`, `duration_minutes<=4320`, `step_minutes in {1,5,15}`, `seed`, `source='synthetic'`, `content_sha256`, `version` | rainfall；被 simulation 引用后不可变 |
| `hydraulic.rainfall_points` | `scenario_id FK`, `point_index`, `time`, `intensity_mm_h>=0`, `PRIMARY KEY(scenario_id,point_index)` | rainfall；自定义 72h/1min 最多 4320 点 |
| `hydraulic.simulations` | `id PK`, `network_revision_id`, `target_manifest_id`, `solver_selection/solver_id/version`, `input_sha256`, canonical status, `attempt`, lease/fencing, `published_attempt_id`, idempotency | worker；唯一 `(requested_by,idempotency_key)` |
| `hydraulic.worker_slots` | `slot_id PK CHECK in (1,2)`, `worker_id`, `lease_until`, `job_id?`, `slot_class` | worker；数据库强制并发 2 |
| `hydraulic.simulation_attempts` + `simulation_frames` | attempt/fencing/frame manifest/hash；frames unique `(attempt_id,frame_index)`；published pointer | Timescale staging；90d retention、压缩 |
| `hydraulic.observation_ingest_events` | source_system/stream/partition/offset unique、payload hash、adapter version | source-event 幂等边界 |
| `hydraulic.observation_samples` | `ingest_event_id`, `observed_at`, revision target, metric/value/unit/quality | Timescale；90d |
| `hydraulic.nowcast_runs` | scheduled/origin/issued/published/deadline、simulation、calibration/rule IDs+hashes、config hash；同 network/scheduled 唯一 | scheduler/forecast |
| `hydraulic.forecast_alerts` | `id PK`, `forecast_run_id`, `target`, `phase`, `expected_at`, `lead_time_minutes`, `depth_m`, `confidence`, `simulation_id`, `suppression_key`, `first_emitted_at`, `window_end`, `state` | forecast/alert；不修改 legacy `alerts` enum |
| `hydraulic.event_outbox` | `sequence BIGSERIAL PK`, `event_id UNIQUE`, aggregate/version/type, payload, published/expiry | outbox/SSE；7d replay retention |
| `hydraulic.network_jobs` + attempts | job_type/status/attempt/lease/fencing/request hash/result hash | import/validate/seed durable worker |
| `hydraulic.network_imports` | job_id、source hash/format、counts、committed | import detail |
| `hydraulic.calibration_runs` | `id PK`, `baseline_simulation_id`, `candidate_simulation_id`, `dataset_sha256`, `rmse`, `nse`, `report JSONB` | calibration；A/B 查询的事实源 |
| `hydraulic.ab_comparisons` | `id PK`, `baseline_id`, `candidate_id`, `metric_delta JSONB`, `created_at` | A/B；同一输入/solver 版本才可比较 |

**逻辑等价规则**：同一 fixture 在 `postgis` 和 `portable` profile 中必须得到相同 network/node/segment/catchment 计数、ID、拓扑校验结果、导入报告 issue code 和 bbox API 响应；只允许空间索引实现和候选查询性能不同。`source_sha256`、`schema_profile`、solver/version、输入 hash 必须出现在结果摘要，不能只存日志。

## 4. 失败语义和 HTTP 映射

| 场景 | HTTP/状态 | code | retry/恢复语义 |
|---|---|---|---|
| 缺 JWT/低角色 | 401/403 | `AUTH_REQUIRED`/`FORBIDDEN` | 不入队；现有 422/401 外壳保持兼容 |
| 扩展名/魔数/解析失败 | 422 | `IMPORT_FORMAT_REJECTED` | 报告落库；不创建半成品 network |
| 源文件超过 20 MiB | 413 | `FILE_TOO_LARGE` | 读到 limit+1 即删除；服务继续可用 |
| 路径穿越/符号链接/UNC | 422 | `UNSAFE_PATH` | 记录脱敏路径 hash；不 echo 绝对路径 |
| 拓扑 error 级问题 | 422 | `NETWORK_VALIDATION_FAILED` | 全量事务回滚；warning 不阻止导入 |
| 同一幂等 key 不同 payload | 409 | `IDEMPOTENCY_KEY_REUSED` | 返回原 request hash/结果，不创建第二个 job |
| network revision 变化 | 409/terminal failed | `NETWORK_REVISION_CONFLICT` | 非重试，客户端重新提交 |
| 显式 EPA 不可用 | 503（提交时） | `SOLVER_UNAVAILABLE` | 不入队；auto 的 fallback 需在 job/report 中记录 |
| worker 暂不可用/DB 短暂失败 | 503/terminal retry | `QUEUE_UNAVAILABLE`/`TRANSIENT_WORKER_ERROR` | 可重试，最多两次 |
| 用户取消 | 202→200 | `SIMULATION_CANCELLED` | 无重试；清目录后终态 |
| 600s 超时 | terminal | `SOLVER_TIMEOUT` | 无自动重试；杀进程树并清理 |
| 输出超限/帧 schema 错 | terminal | `OUTPUT_LIMIT_EXCEEDED`/`FRAME_SCHEMA_INVALID` | 无重试；不能标 completed |
| lease 过期/worker crash | retry_wait | `LEASE_EXPIRED` | fencing 后可重试，累计最多 3 attempts |
| Timescale 写入失败 | retry_wait 或 503 | `FRAME_STORE_UNAVAILABLE` | 事务回滚；不能提交孤立摘要 |
| frame 已过 retention | 410 | `FRAME_RETENTION_EXPIRED` | 返回 simulation 摘要和过期原因 |
| 30m 内重复 forecast | 成功但不重复 | `FORECAST_SUPPRESSED` | 记录 suppressed metric；不发第二业务事件 |
| 清理失败 | 终态 + `cleanup_pending` | `SANDBOX_CLEANUP_PENDING` | 启动 sweeper 重试；不重跑 solver |

## 5. 门禁（Release Gates）

门禁是“必须通过”的可执行检查，不是建议清单。任何一项未运行都只能报告 `NOT RUN`，不能用静态阅读替代。

### G-0：当前基线门禁（先红后绿）

目标运行时固定为 Python 3.11、Node 20、目标 Compose image digest。当前 recon 报告记录的后端 pytest 收集失败、前端 `6/8`、`format:check` 36 文件、启动 `logs/app.log` 失败（`recon/quality.md:45-71,96-135,208-229`）必须先被修复或在 release note 明确阻断。

```bash
python scripts/verify_hydraulic_stack.py --gate g0 --evidence-dir "artifacts/hydraulic/${RUN_ID}/g0"
```

entrypoint MUST 分别在 `backend/` 与 `frontend/` 的锁定环境执行全量 pytest/Vitest、lint/format/build、mypy（不可 continue-on-error）和 backend import/startup；禁止文档继续维护易漂移的 `cd` 链。

- 通过条件：退出码全为 0；mypy 不能使用 `continue-on-error`；Python 3.11/Node 20 与 CI 矩阵一致。
- 旧 API/SSE/告警必须有 characterization fixture：旧事件名/字段不变；新增 forecast 使用新 schema，不把旧 `alert_id` 缺字段对象写入 store。

### G-1：schema/PostGIS/Timescale 门禁

对 `postgis` 和 `portable` 两个全新数据库分别运行：

```bash
python scripts/verify_hydraulic_stack.py --gate g1 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g1/postgis"
python scripts/verify_hydraulic_stack.py --gate g1 --profile portable --evidence-dir "artifacts/hydraulic/${RUN_ID}/g1/portable"
```

每个 entrypoint 独立从空 volume 执行 `upgrade head → current → repeat upgrade → clone downgrade → upgrade head` 并输出 manifest；portable 仍含 TimescaleDB，只是不含 PostGIS。

并执行数据库断言：

```sql
SELECT extname, extversion FROM pg_extension
 WHERE extname IN ('postgresql','timescaledb','uuid-ossp','postgis');
SELECT hypertable_schema, hypertable_name
  FROM timescaledb_information.hypertables;
SELECT * FROM timescaledb_information.jobs;
```

- `postgis` profile：必须有 `postgis`、Timescale、`hydraulic` 表、generated geometry、GiST 索引；`portable` profile：没有 geometry/GiST 依赖但 WKT/bbox/topology 结果相同。
- migration 与 init SQL 的结果比较只比较逻辑 schema 和 profile-specific allowlist；不再要求两个 profile 的物理列逐字相同。init SQL 不得含 `CREATE TABLE`/seed/hypertable。
- migration 失败、unknown head、重复 upgrade 改变结果、downgrade 失败均阻断发布。
- 当前没有 Docker daemon，因此上述 upgrade/downgrade/SQL 检查在本次审查中**未运行**；`docker compose config --services` 本次只证明默认服务列表，不证明数据库可启动。

### G-2：import/security/文件门禁

- 合法 `.inp`/`.geojson` fixture 在两个 profile 导入，计数和 issue code 与 golden JSON 完全一致。
- 负向样本：20 MiB+1、截断、zip bomb、pickle/脚本、重复 section、缺失坐标、NaN/Infinity、深度 GeoJSON、POSIX `/`、Windows `\`、盘符、UNC、symlink/junction、同名并发。每个样本必须得到预期 HTTP code，API liveness 前后均 200。
- 断言：拒绝文件没有 network 行、quarantine/part/sandbox 目录被清理、响应没有绝对路径；合法输入 count/validation report 可通过 API 查询。
- worker 进程树、CPU/内存/磁盘/文件数限制必须由测试或 OS 级 sandbox 证明，不能只检查 Python `Path.resolve()`。

### G-3：worker/恢复/结果门禁

必须覆盖：提交幂等、并发恰为 2、0→100 单调进度、取消、600s 超时、solver crash 两次重试、第三次失败、租约过期恢复、旧 worker fencing、network revision conflict、Timescale 写失败、清理 sweeper、进程树无孤儿、结果 hash 重跑一致。

- 失败重试只针对 transient/crash；取消、超时、输入/输出上限不重试。
- 重启测试要实际 kill worker 进程，再从数据库扫描 `queued/retry_wait/lease`；不能用“清空内存后重新调用函数”替代。
- frame contract 测试断言：24h/5min 为 289 个时间点，`max(diff)<=300s`，三类 payload count 与输入 topology 一致，重复 frame 被唯一约束拒绝。

### G-4：forecast/realtime 门禁

- fake clock 验证默认 10 分钟调度、0–2h/5 分钟 horizon、nowcast→alert P95 ≤180s；`lead_time_minutes = expected_at - issued_at`，必须为非负整数。
- 两个 API worker + Redis 环境下重启一个 worker，验证 durable event sequence、`Last-Event-ID` 重放、重复 producer 只有一个业务事件、队列满有 gap/resync 而不是静默丢弃。
- 30 分钟内相同 suppression key 只产生一个 `forecast_alert`、一个 SSE business event、一次通知；`predicted` 与 `observed` 分别可发。旧 `sensors/alerts/system/connected` fixture 继续通过。

### G-5：性能门禁

固定机器报告：CPU 型号/核心数、RAM、OS、Python 3.11、依赖 lock、solver/version、数据库 image digest、输入 fixture SHA-256。性能命令必须把结果写成 JSON，不接受截图或人工观察。

- solver：5000 nodes/10000 segments/24h、4 核、纯 Python、`p95 wall_time <=180s`；同时记录峰值内存、frame bytes、数据库写入时间。
- API：新增 hydraulic endpoint P95 <=500ms（不含大 frame 下载）；nowcast 到首个告警事件 P95 <=180s。
- 前端：万级管段、千级多边形、1/2/4 倍速，至少 25fps；记录 viewport culling、抽稀、Canvas/预渲染开关和 long task。没有 Playwright/浏览器 performance 报告就不能通过。
- 大 chunk warning 只能作为报告，不算性能通过；当前前端只存在手动 marker（`recon/frontend.md:131-139`）。

### G-6：Compose/回滚/备份门禁

```bash
docker compose down -v
docker compose up -d --build
docker compose ps
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/readyz
curl -fsS http://localhost:8080/health
```

随后在干净卷上完成：登录（无 fallback admin）→网络导入→校验→seed→rainfall scenario→50 年 24h simulation→progress/cancel/restart→forecast→frame replay→export。再执行 `alembic downgrade/upgrade` 的 clone 测试和 backup→删除→restore→行数/hash/API 校验。

- 必须证明 `init-db.sql` 不再是第二个 schema writer；backend/worker 等待 migrate 成功。
- 当前 Compose 使用固定明文 secret/宿主机端口（`docker-compose.yml:13-18,35-37,80-89`），AC-8 只能作为本地演示证据，不能作为生产安全通过。
- 现有 backup/restore 只做脚本形态参考：Linux 脚本对 stderr 丢弃且无 manifest/hash（`scripts/backup.sh:15-46`），Windows 脚本不检查 `pg_dump` 退出码且与 `restore.sh` 的 tar.gz 格式不兼容（`scripts/backup.ps1:12-30`、`scripts/restore.sh:20-48`）；水力发布门禁必须补可验证 manifest、SHA-256、退出码和恢复 smoke。

## 6. 回滚与恢复契约

### 6.1 发布顺序

1. **Expand**：先上线 additive `hydraulic` schema、Timescale 表/索引、worker slot、outbox；不改 legacy `users/devices/alerts` 的 enum/字段，不在 API 启动时建表。
2. **Dual-read/dual-write 验证**：旧 API/SSE 继续走 legacy 表；hydraulic forecast 走新表/新 schema；只通过版本化 union/adapter 给告警中心，不改变旧 REST 响应。
3. **Enable**：readiness、solver、scheduler、worker、forecast 逐项打开；默认先关闭用户 simulation，AC-3/AC-4 通过后再开放。
4. **Contract**：确认无旧客户端依赖后才在后续 release 清理兼容层；本 release 不删除 legacy 数据。

### 6.2 应用回滚

- 每个镜像使用 immutable digest；部署前记录 commit、image digest、Alembic revision、PostGIS/Timescale extension version、solver version。
- 回滚顺序：暂停 nowcast scheduler →停止接收新 simulation →让当前 attempt 完成或取消 →等待 lease drain/标记取消 →恢复上一 API/worker image →检查 `/readyz`、schema revision、队列深度、旧 API/SSE smoke →再恢复 scheduler。
- 旧应用必须能忽略 additive `hydraulic` schema；禁止回滚时执行 `drop schema hydraulic cascade` 或删除 frame。
- 事件 schema 只允许向后兼容读取：consumer 忽略未知字段/新 event type，不能因为新事件导致旧页面崩溃。

### 6.3 数据回滚边界

- `alembic downgrade` 在空库/clone 上必须可执行并通过 schema contract；它是发布前验证手段，不是生产数据删除手段。
- 已有 production frame/forecast 数据时，默认只做**应用回滚 + forward fix**。若必须降 schema，先停止 worker/scheduler、导出 PostgreSQL custom dump、记录 manifest/hash、在隔离库演练并获得显式数据处置批准。
- Timescale `DROP TABLE`/chunk 删除、retention 删除和旧 init SQL 重跑均视为破坏性操作；禁止在 rollback script 中无条件执行。
- PostGIS/portable profile 不能通过改一个环境变量原地切换。切换必须新建空库、从备份恢复并重新跑对应 migration，或执行经过演练的双写/回填计划。

## 7. AC 证据矩阵

“当前状态”只反映本次阅读的现状报告和本次执行的静态命令；`PASS` 只能在该行命令于目标环境实际退出 0 且 artifact 存在后填写。

| AC | 必须运行的证据 | 通过判据 | 证据文件/位置 | 当前状态 |
|---|---|---|---|---|
| AC-1 合法 inp/GeoJSON 导入计数/报告 | 两个 profile 的 `test_import_contract` + API golden JSON | 计数、ID、issue code、WKT/GeoJSON round-trip 与 fixture 完全相同 | `artifacts/hydraulic/<run>/ac1/{postgis,portable}.json`、pytest JUnit | **未实现/未运行** |
| AC-2 非法/超大/恶意拒绝且稳定 | `test_file_security`、路径矩阵、进程存活探针 | 预期 413/422；无半成品/任意路径；前后 `/health` 与 `/readyz` 正常；目录清理 | `artifacts/.../ac2/security.json`、日志 hash | **未实现/未运行**；现有模型上传代码还暴露绝对路径（`routes.py:204-246`） |
| AC-3 50 年一遇 24h 仿真/回放 | 固定 4 核 fixture、worker E2E、frame schema、浏览器 timeline | `p95<=180s`；289 个时间点；diff<=300s；进度 0/100；取消/重试/回放可复现 | `ac3/solver.json`、`frames.manifest.json`、Playwright trace | **未实现/未运行**；没有水力路由/worker/frame 表 |
| AC-4 nowcast→forecast/告警/SSE/抑制 | `verify_hydraulic_stack.py --gate ac4`（fake clock 100 时隙 + 30 真实周期 + 两 API worker + durable outbox） | issued/origin/published、独立 freshness、calibration/rule artifact、P95<=180s、字段齐全；30m 同 key 一个业务事件 | `ac4/forecast.json`、event/alert IDs、suppression ledger | **未实现/未运行；gate entrypoint 不存在** |
| AC-5 率定量化改善 | 固定 train/held-out dataset、默认参数 A、校准参数 B、RMSE/NSE report | held-out `NSE_B >= NSE_A+0.05` 且 `RMSE_B <=0.95*RMSE_A`、peak error 恶化≤10%；报告含 dataset/sim/solver hash | `ac5/calibration.json`、指标查询响应 | **未实现/未运行**；PRD 未给数值阈值，主候选基线冻结为可执行阈值 |
| AC-6 A/B 差异可查 | 同 network/rainfall/observation/solver，A/B API/UI | 只允许参数集不同；返回绝对值、百分比和 baseline/candidate IDs；可重算 | `ac6/ab.json`、OpenAPI fixture | **未实现/未运行** |
| AC-7 pytest/vitest/build/后端启动/旧测试零回归 | G-0 全部命令 + 旧 API/SSE characterization | 目标 Python 3.11/Node 20 全绿；启动能创建 logs；`/readyz` 真实依赖；旧测试无回归 | `ac7/backend.xml`、`frontend.xml`、startup.log、CI run URL | **当前阻断**；本次未重跑，报告记录后端收集失败/前端 `6/8`/format fail/启动失败 |
| AC-8 全新 compose README 演示 | `down -v`、`up -d --build`、migration、PostGIS/Timescale、worker、API/SSE、backup/restore | 空卷可复现；默认 PostGIS profile 完整演示；portable 另跑；无 latest/secret 依赖；可回滚 | `ac8/compose.log`、schema manifest、demo trace、backup manifest | **未验证/本次未运行**；本次仅 `config --services` 得到五个旧服务 |

每份 artifact 必须包含：`run_id`、UTC 时间、commit、Python/Node/OS 版本、镜像 digest、数据库扩展/版本、Alembic revision、solver name/version、fixture SHA-256、完整命令、退出码、stdout/stderr 摘要和测试数据行数/hash。不得用 recon 报告中的历史命令输出替代本次 artifact。

## 8. 不可行点、替代方案和决策记录

### 8.1 PostGIS 镜像/扩展

- **不可行点**：当前 init SQL 已调用 PostGIS 函数，却没有 `CREATE EXTENSION postgis`；不能仅修改 ORM 或增加 `geometry` 字段解决。
- **替代方案 A（采用）**：PostGIS+Timescale 固定 digest 镜像，Alembic profile-specific DDL；portable 独立 profile。
- **替代方案 B**：单独 PostGIS 服务/双数据库；连接拓扑和一致性成本高，拒绝。
- **验收**：两个 profile 的空库 `pg_extension`、hypertable、schema manifest、API fixture。

### 8.2 Redis queue

- **不可行点**：现有 Redis 只有 Pub/Sub/KV（`redis_client.py:50-164`），不能提供 claim、ack、租约、fencing、重试和重启恢复。
- **替代方案 A（采用）**：PostgreSQL simulation table + outbox + 两个 slot 行；Redis 仅唤醒/SSE。
- **替代方案 B**：Redis Streams/consumer group；可作为未来优化，但必须补 consumer ACK、pending reclaim、持久配置和 replay，当前不是现有能力。

### 8.3 EPA SWMM 与纯 Python

- **不可行点**：当前依赖声明没有 SWMM/Python solver（`backend/requirements-cpu.txt:1-34`），报告的本机 `where.exe swmm5` 为 `NOT_FOUND`（`recon/simulation.md:186-227`）。
- **替代方案 A（采用）**：统一 `SolverAdapter`；提交时 probe 并冻结实际 solver；Windows 显式 EPA，Compose/无 EPA 环境 auto→纯 Python，并记录 fallback。
- **替代方案 B**：只支持纯 Python；稳定但违背“优先 EPA”，拒绝为最终方案。
- **验收**：两 solver 的 frame schema golden、进程树取消/超时、版本和输入 hash 报告。

### 8.4 20 MiB 上限

- **不可行点**：若 20 MiB 包含所有 frame/积水面输出，10,000 管段×24h×5min 的结果无法同时满足存储、查询和回放；不能把截断结果标为完成。
- **替代方案 A（采用）**：20 MiB 源/输入物料；输出 64 MiB/frame、8 GiB/job 可配置，超限显式失败。
- **替代方案 B**：放宽上传上限；会削弱恶意文件防护。
- **替代方案 C**：结果只存对象存储/预渲染文件；引入新的持久化边界和清理/回滚复杂度，本期不采用。
- **验收**：limit+1 拒绝、标准 fixture 输出大小/内存、磁盘不足和清理测试。

### 8.5 90 天保留与告警引用

- **不可行点**：若 frame 与 simulation/alert 一起物理删除，90 天后告警中的 `simulation_id` 会变成悬空引用；若永久保留 frame，又违反 90 天策略。
- **替代方案 A（采用）**：Timescale 只保留 frame/observation 90d；普通 PostgreSQL 永久保留 simulation 摘要、solver/input hash、metrics、alert 和 event audit；过期 frame 返回 410。
- **验收**：retention job manifest、90d 后 metadata/alert 可查而 frame 410、恢复后 hash 一致。

### 8.6 迁移回滚

- **不可行点**：Timescale hypertable/retention 和已写入的 forecast/frame 不应通过无条件 downgrade 静默删除。
- **替代方案 A（采用）**：expand/contract、应用回滚、clone downgrade 验证、生产 forward fix。
- **替代方案 B**：自动 destructive downgrade；仅在空库/显式清理批准后使用，拒绝作为默认 rollback。
- **验收**：旧镜像读取 additive schema、clone upgrade/downgrade/upgrade、备份恢复、旧 API/SSE 回归。

## 9. 当前会话实际执行的只读检查

以下命令在本次审查中执行，输出原样摘录；它们不构成运行时通过：

```text
$ docker compose -f smart-city-drainage/docker-compose.yml config --services
redis
influxdb
postgres
backend
nginx
# exit 0；同时提示 docker-compose.yml:1 的 version 已过时
```

```text
$ python <只读 schema marker 比较脚本>
backend/alembic/versions/0001_initial.py tables=[]
docker/init-db.sql tables=['alerts', 'camera_streams', 'devices', 'inference_results', 'model_versions', 'sensor_readings', 'users']
backend/app/models/db_models.py tables=['alerts', 'camera_streams', 'devices', 'inference_results', 'model_versions', 'users']
init_postgis= False
migration_postgis= False
init_hypertable= True
migration_hypertable= False
```

补充的 migration 表名提取输出为：

```text
migration_tables=users,devices,camera_streams,alerts,model_versions,inference_results
migration_create_all=False
```

```text
$ python <只读路由/关键词统计脚本>
http_routes=30
websocket_routes=1
route_term_networks=0
route_term_rainfall-scenarios=0
route_term_simulations=0
route_term_forecast=0
hydraulic_keyword_hits=0
```

本次没有运行：Python 3.11 pytest、Node 20 Vitest/build、Docker build/up、Alembic upgrade/downgrade、PostGIS/Timescale SQL、SSE/worker E2E、性能基准、备份恢复。报告中的这些命令结果均按 recon 原文引用为历史证据，而不是本次通过证据（`recon/quality.md:416-423`、`recon/deployment.md:423-428`）。

## 10. 交付判定

在以下条件全部满足前，运维/水力功能不得标记“完成”：

- D-001～D-009 已实现并有对应测试；
- G-0～G-6 在目标运行时实际执行，artifact 完整；
- AC-1～AC-8 每行都有机器证据和明确的 `PASS/FAIL/NOT RUN`；
- `postgis` 与 `portable` 两 profile 的 schema/API 逻辑等价；
- worker crash/lease/cancel/timeout/retry/cleanup 有实测记录；
- rollback 演练证明旧 API/SSE/告警页面零回归，且不会删除已有 hydraulic 数据；
- AC-3 的 4 核 180s 和 AC-6 的 25fps 不是估算，而是固定 fixture 的可复现实验结果。

本文件是唯一写入的交付物；源码和现有报告未被修改。
