# 后端垂直切片集成独立审查

- **模块**：`backend-integration`
- **审查日期**：2026-09-25
- **审查方式**：只读；阅读冻结契约、交接文档、真实源码/迁移/Compose，并执行定向诊断。没有修改生产代码、测试或迁移；本文件是按任务要求写入的唯一审查产物。
- **结论**：**BLOCKED / `approved=false`**。Prometheus collector 的注册顺序和 focused tests 是绿的，但真实 durable schema、worker、API repository、outbox/SSE replay、通知 worker 和生产启动链路仍未闭合；存在多个高风险阻断。

## 1. 规范基线

本次以以下冻结输入为准：

- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:0-14`（Frozen v3）及 `:117-163`（事实源/数据流）。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:538-545`、`:721-744`、`:838-896`、`:953-965`、`:1184-1223`（durable jobs、worker slots、观测、outbox、notification、replay）。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1279-1297`（import/export）、`:1376-1465`（durable state/lease）、`:2081-2134`（Compose/readiness）、`:2171-2203`（metrics/logging）。
- `docs/hydraulic-twin/implementation/hydraulic-metrics.md:211-232`、`:244-278`（singleton 必须在 Instrumentator 前导入并做真实 scrape 回归）。
- `docs/hydraulic-twin/implementation/backend-integration.md:1-18`、`:241-249`（实现方自己承认 durable E2E、worker→API metrics、outbox relay、live migration 尚未完成）。

## 2. 本次实际执行的检查

| 检查 | 命令 | 结果 |
|---|---|---|
| 目标 Python 3.11 focused suite | `/c/Python314/Scripts/uv.exe run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B -m pytest tests/integration/test_backend_integration.py tests/hydraulic/test_hydraulic_metrics.py tests/hydraulic/test_solver_contract.py tests/test_hydraulic_schema.py -q --tb=short` | **PASS**：`69 passed, 1 warning in 2.08s` |
| Python 3.11 app import + `generate_latest` | 同上环境执行 `python -B -c "import app.main ..."` | **PASS**：`PY311_APP_IMPORT=PASS`、`PY311_GENERATE_LATEST_HYDRAULIC=PASS` |
| 默认解释器 app import + `generate_latest` | `.venv/Scripts/python.exe -c "import app.main ..."` | **PASS**：`APP_IMPORT=PASS`、`GENERATE_LATEST_HYDRAULIC=PASS` |
| 无 lifespan 的 HTTP `/metrics` | `httpx.ASGITransport` 请求 `/metrics` | **PASS**：HTTP 200、text/plain、queue/duration 名称存在；这不包含真实 lifespan |
| 真实 lifespan 启动（3.14） | `TestClient(app)` | **FAIL**：`FileNotFoundError: .../backend/logs/app.log` |
| 真实 lifespan 启动（3.11） | 同一 `TestClient(app)`，锁定 3.11 环境 | **FAIL**：`PY311_LIFESPAN=FAIL FileNotFoundError: .../backend/logs/app.log` |
| simulation worker module | `timeout 10s .venv/Scripts/python.exe -m app.workers.simulation_worker` | **FAIL for integration**：输出 `EXIT_CODE=0`，模块立即退出，没有 worker loop/health server |
| Compose service inventory | `docker compose config --services` | 只有 `influxdb postgres migrate redis backend simulation-worker nginx`；没有 network-job/nowcast/outbox-relay/notification worker |
| ORM/migration table inventory | AST 扫描 `__tablename__` 并搜索 0002 migration | **FAIL**：冻结契约要求的 11 张表均不在 ORM/migration |
| outbox no-subscriber probe | 给 `RedisClient` 注入返回 0 的 fake Redis，再调用 `relay_forecast_outbox(..., mark_published=...)` | **FAIL**：`PUBLISHED_COUNT=0`、`MARKED_PUBLISHED=[]` |
| policy gate probe | 临时写入 `{"unexpected":"not-a-valid-policy"}` 及匹配 sidecar，调用 `_forecast_component_ready()` | **FAIL**：`ARBITRARY_POLICY_COMPONENT_READY= True` |
| rate-limit growth probe | 直接调用 `hydraulic_rate_limit` 1000 次，每次使用不同 Authorization header | **FAIL**：`RATE_BUCKET_KEYS_AFTER_1000_UNAUTH_HEADERS= 1000` |

**未运行（按本次 ask 的“不要重复最终全量门禁”）**：全量 pytest、Compose `up`、真实 PostgreSQL/PostGIS/Timescale migration、Redis/worker/notification/outbox E2E、G0～G6/AC。实现摘要中提到的“5 failed, 466 passed”是实现方先前记录，本审查没有把它当成本次通过证据。

## 3. Findings

### F-01 — HIGH — 冻结 durable schema 缺少垂直切片所需的事实表

**证据**：

- 契约要求 `worker_slots`（`IMPLEMENTATION_CONTRACT.md:721-744`）、观测 trust/event/sample（`:820-896`）、`ab_comparisons`（`:953-965`）、`event_outbox`（`:1184-1204`）、`notification_deliveries`（`:1206-1223`）以及 import/validation detail 表（`:547-595`）。
- 真实 `backend/app/models/hydraulic_models.py` 的 `__tablename__` 清单和 `backend/alembic/versions/0002_hydraulic_schema.py` 的 `op.create_table` 清单都没有这些表。实际 inventory 命令输出：
  ```text
  ORM_TABLE_COUNT= 27
  MISSING_REQUIRED_TABLES= ['network_imports', 'validation_runs', 'network_validation_issues', 'observation_trust_policies', 'observation_ingest_events', 'observation_samples', 'ab_comparisons', 'worker_slots', 'event_outbox', 'notification_deliveries', 'forecast_alert_windows']
  ```
  直接检查 SQLAlchemy metadata 也输出：
  ```text
  REQUIRED_IN_METADATA= []
  HAS_EVENT_OUTBOX= False
  HAS_WORKER_SLOTS= False
  ```

**问题/影响**：没有这些表就无法持久化 import/validation 结果、真实观测身份与率定数据、全局两个 worker slot、suppression window、SSE durable replay、outbox acknowledgement 和通知 retry/DLQ。API/worker 即使被接上也只能退回内存或丢失事实，直接违反本模块的端到端目标及 G1/G3/G4。

**建议**：在 Alembic 与 ORM 同时补齐所有必需表、外键/复合 lineage、约束、索引、Timescale retention 和 downgrade empty-only 守卫；随后用两个 spatial profile 的真实空库 migration/upgrade/downgrade/upgrade 验证，不能以 SQLite `Base.metadata.create_all` 代替。

### F-02 — HIGH — Compose 启动的 simulation worker 不是可运行 worker

**证据**：

- `docker-compose.yml:174-237` 将服务命令设为 `python -m app.workers.simulation_worker`，并把健康检查指向 `127.0.0.1:8010/readyz`。
- `backend/app/workers/simulation_worker.py` 只有 `SimulationWorker` 类和库函数；`rg -n "__main__|asyncio.run|SimulationWorker\\(" app/workers/simulation_worker.py` 无输出，文件末尾（`simulation_worker.py:693-708`）只有 `__all__`，没有启动循环或健康服务器。
- 实际执行 `python -m app.workers.simulation_worker` 的输出是 `EXIT_CODE=0`，即模块导入后立即退出，不会监听 8010、claim DB job 或维持 lease。
- `docker compose config --services` 输出没有 `network-job-worker`、`nowcast-worker`、`outbox-relay` 或 `notification-worker`。

**问题/影响**：Compose 的 worker healthcheck 永远无法通过，Nginx 因 `simulation-worker` healthy 依赖（`docker-compose.yml:265-269`）也无法成为可用后端；即使手工启动 simulation worker，import/nowcast/outbox/notification 仍没有执行者。

**建议**：增加真实、可测试的 worker entrypoint（DB session factory、solver/executor、claim loop、heartbeat、signal/cleanup、health/readiness server），为四类 durable job 提供独立进程/服务或在明确 owner 边界内接入；Compose 不得把“导入即退出”的模块当 worker。

### F-03 — HIGH — 生产 API 永远使用未接线的内存 store，durable guard 只能返回 503

**证据**：

- `backend/app/api/hydraulic_routes.py:277-305` 明示 `HydraulicApiStore` 是“not yet attached to a worker repo”的进程内字典；`:288` 固定 `self.durable_backend = False`。
- 全仓 `rg` 没有把该 flag 设为 true，也没有 lifespan 注入 SQLAlchemy repository。生产 guard 在 `hydraulic_routes.py:319-327` 直接返回 `503 DURABLE_STORE_UNAVAILABLE`。
- 定向诊断输出：
  ```text
  DURABLE_BACKEND_FLAG= False
  PRODUCTION_DURABLE_GUARD= HTTPException 503 {'detail': 'hydraulic durable repository is not configured', 'code': 'DURABLE_STORE_UNAVAILABLE', 'errors': []}
  ```
- simulation submit 仍只写 `hydraulic_store.simulations`（`hydraulic_routes.py:3083-3197`），而不是 ORM `SimulationRun`/idempotency transaction；calibration/forecast 同样使用内存字典。

**问题/影响**：生产 mutation 路径无法提交；开发/测试路径的 network、simulation、rainfall、calibration、forecast 状态在重启/多进程后丢失。更严重的是 worker 的 SQLAlchemy repository 与 API 读取的 `HydraulicApiStore` 是两套互不相通的事实源，即使 worker 后续能运行，结果 API 也读不到 worker 发布的数据。

**建议**：在 lifespan/应用组装阶段注入一个真实的 DB-backed repository adapter，API transaction 与 resource/idempotency/outbox 原子提交；删除或严格隔离 production 的内存 adapter，并添加 API↔worker 的真实 DB round-trip 测试。

### F-04 — HIGH — import API 在请求线程同步解析，且创建的 job 没有任何执行者

**证据**：

- `hydraulic_routes.py:1376-1389` 先完整 `_read_upload`，随后直接调用 `_preflight_import`；`:1392-1421` 才创建 `queued` job/import detail。
- 这与冻结契约 `IMPLEMENTATION_CONTRACT.md:1279-1287` 明确要求的“API 只写 network_jobs/idempotency 并返回 202，network-job-worker 负责 parse/validate/seed”相反。
- 路由注释（`:1390-1391`）声称 worker 负责后续工作，但仓内没有 network-job worker 文件，Compose inventory 也没有该服务。
- `_read_upload`（`:1025-1055`）还把最多 20 MiB 内容先聚合到内存，再在 API 中解析；解析失败直接同步返回 4xx，不会形成可查询的 durable job/report。

**问题/影响**：API 请求承担不受 durable lease/fencing 保护的解析 CPU/内存工作；合法 import 返回 202 后 job 永远 queued，`network_imports`/validation report 不落库，导入链路无法端到端完成。

**建议**：API 只做认证、流式 quarantine（limit+1 即停）、脱敏 metadata/idempotency transaction 并返回 202；由独立 network-job-worker 以 `FOR UPDATE SKIP LOCKED`、lease、heartbeat、fencing、attempt staging 执行 parser/validator/seed，最终单事务发布 revision/entities/issues 或失败报告。

### F-05 — HIGH — `/metrics` 只注册了零值 collectors，没有 worker/control-plane 的运行时接线

**证据**：

- singleton import 顺序本身正确：`main.py:43-44` 导入 `hydraulic_metrics`，`main.py:271-275` 才初始化 `Instrumentator`；对应 AST 测试通过。
- 但生产 call-site 搜索只找到 import 和 `app.state.hydraulic_metrics` 赋值：`rg -n "hydraulic_metrics|set_queue_state|set_active_slots|record_simulation_terminal|record_nowcast_terminal|record_notification_delivery" app --glob '*.py'` 的业务调用结果只有 `main.py:44,167` 和 metrics 模块自身定义，没有 worker/API/notification 调用。
- 默认 exposition 诊断显示：
  ```text
  simulation_queue_depth 0.0
  simulation_duration_seconds_count{solver="py_mw",status="completed"} 0.0
  simulation_active_slots{lane="general"} 0.0
  simulation_active_slots{lane="nowcast"} 0.0
  ```
- `test_backend_integration.py:48-56,60-73` 只断言 metric 名称 substring，不启动 worker、不注入 DB snapshot，也不验证非零值。

**问题/影响**：HTTP 200 和名称存在只能证明 collector 注册，不能证明 queue depth、active slots、duration/failure/retry/timeout/cancel、forecast/outbox/notification/cleanup 的生产值来自 durable DB。运维看到的是永久零值或空 series，违反本模块明确的 metrics handoff。

**建议**：由 DB snapshot poller/worker lifecycle 在同一应用或受控 exporter 中周期性调用 `set_queue_state`/`set_active_slots`，在 terminal/finalize/relay/notification 事务结果确认后调用相应 recording API；增加真实非零值、跨 worker、重启和失败路径的 `generate_latest`/HTTP 断言。

### F-06 — HIGH — outbox relay 不会可靠标记已发布事件，并会反复重发

**证据**：

- `main.py:118-127` 调用 `redis_client.relay_forecast_outbox(rows or ())`，没有传 `mark_published` callback。
- `redis_client.py:172-185` 只有在 `publish_forecast_event(...) > 0` 时才调用 `mark_published`；`publish_forecast_event` 返回的是 Redis subscriber count（`:157-168`），不是“发布命令成功”标志。
- 无订阅者是很正常的运行状态；定向 fake-Redis 诊断输出：
  ```text
  PUBLISHED_COUNT= 0
  MARKED_PUBLISHED= []
  ```
- 同一个 `app.state.forecast_outbox_source` 在全仓只有读取（`main.py:121,198`），没有生产赋值；即使外部注入 source，当前调用也没有 durable acknowledgement 回调。

**问题/影响**：没有 API SSE 客户端时，已成功写入 Redis 的 outbox row 仍会被当作未发布，下一轮重复 publish；有 `mark_published` 的调用方也必须依赖 subscriber count，造成无限重试/重复通知，并破坏 at-least-once 边界和 backlog 指标。

**建议**：relay 使用 DB claim/lease，Redis publish 成功（不依赖订阅者数量）后以事件 sequence/idempotency 条件写 `event_outbox.published_at`；main 必须传入真正的 mark callback，失败/超时保留可重试状态并记录 publish lag/backlog。

### F-07 — HIGH — 外部通知在每个 API Redis listener 中执行，没有 durable notification worker

**证据**：

- `main.py:106-115` 的 `_handle_redis_forecast` 在 API 进程的 Redis handler 内先 SSE fan-out，再直接 `await notification_service.notify_forecast_event(message)`。
- `notification_service.py:305-401` 会在该调用中执行 SMTP gather 和逐个 webhook 网络请求；它只返回计数，不写 delivery row。
- Redis listener `redis_client.py:219-247` 对每条消息同步 await handler；多个 API replica 会各自收到同一 Redis 消息并各自发送邮件/webhook。Compose service inventory 也没有 notification-worker。
- ORM/migration 缺少契约要求的 `notification_deliveries` 表（见 F-01）。

**问题/影响**：通知会在 API 副本数放大，失败没有 durable retry/DLQ/幂等记录；慢 webhook 会阻塞同一 Redis listener，延迟其它 forecast/SSE 消息。该路径也使 API 重启丢失投递状态。

**建议**：API 只 fan-out 已提交事件；由独立 notification-worker 从 `notification_deliveries` claim/retry，使用唯一 `(event_id, channel, destination, template_version)` 幂等键，失败写 retry/dead-letter，并独立记录 `notification_delivery_total`。

### F-08 — HIGH — forecast SSE 的 `Last-Event-ID` replay 仍是进程内 history

**证据**：

- `sse_manager.py:156-193` 允许注入 `ForecastReplayStore`，但默认 `None` 时 `connect_forecast`（`:253-258`）直接读 `_forecast_history`；`_durable_replay`（`:587-645`）只有注入 store 才会查询 durable outbox。
- `app/api/routes.py:79-80` 通过 `SSEManager.get_instance()` 创建默认 manager；全仓 `rg` 没有 `replay_store=` 或任何生产 DB replay store 注入。
- 契约要求 event sequence 来自 durable outbox、至少 168h replay（`IMPLEMENTATION_CONTRACT.md:1184-1204`），而 `sse_manager.py:187-190` 的 history/latest state 是内存字段，进程重启即丢失。

**问题/影响**：客户端重连或 API worker 切换后，`Last-Event-ID` 无法从 outbox 恢复；历史丢失时只能依赖本地 history，无法满足 durable replay、7-day retention 和多 API worker 一致性。

**建议**：实现并注入 PostgreSQL `ForecastReplayStore`，以 outbox sequence/bounds 查询 168h window；在连接建立时用 DB replay + live subscription 的原子边界，补跨重启/多 API/过期 cursor 测试。

### F-09 — HIGH — 文档规定的本地后端启动在进入 lifespan 时立即失败

**证据**：

- `main.py:68-73` 直接构造 `RotatingFileHandler("logs/app.log")`，没有创建父目录或使用已创建的绝对路径。
- README 的标准开发启动步骤是 `cd backend` 后执行 `uvicorn app.main:app --reload --port 8000`（`README.md:226-243`）；当前 `backend/logs` 不存在。
- 实际 3.14 与 3.11 `TestClient(app)` 都输出：
  ```text
  FileNotFoundError: [Errno 2] No such file or directory: '.../smart-city-drainage/backend/logs/app.log'
  ```
  3.11 诊断前缀为 `PY311_LIFESPAN=FAIL`。

**问题/影响**：应用 import 可以通过，但真实 lifespan、Uvicorn 启动、worker/API readiness 都失败；这直接否定“可启动后端”交付物。Dockerfile 恰好创建了 `/app/logs`，只掩盖了非容器启动路径，且 focused integration test 没有 lifespan，无法发现该回归。

**建议**：在 logging 初始化前以明确配置创建/解析日志目录（并处理权限/只读文件系统），或使用安全的 stdout-only fallback；增加真实 `TestClient`/Uvicorn startup smoke，且测试必须覆盖 lifespan。

### F-10 — HIGH — Compose 默认绕过 production fail-closed gates，并无条件启动 mock generator

**证据**：

- `Settings.DEPLOYMENT_ENVIRONMENT` 默认是 `development`（`backend/app/core/config.py:376-379`）；只有显式 production 才执行 RS256、issuer/audience、弱 secret 拒绝等检查（`:466-495`）。
- Compose backend 没有设置 `DEPLOYMENT_ENVIRONMENT=production`，并注入仓库默认 DB/Redis/Influx secret、HS256（`docker-compose.yml:123-138`）。
- lifespan 在 DB 不可用时记录“将使用模拟数据”（`main.py:180-188`），随后无论环境都调用 `mock_generator.start()`（`:211-219`）。mock 传感器/legacy 路由仍在 `routes.py` 中提供。

**问题/影响**：目标 Compose 运行时会绕过 `DURABLE_STORE_UNAVAILABLE`、forecast policy、RS256 和弱 credential 门禁，继续使用内存 hydraulic state 并产生 mock 数据；这违反“API 禁止 mock healthy/事实源”的冻结契约，也会让生产告警/结果混入演示数据。

**建议**：将目标部署明确设为 production profile，所有 secret/DB/Redis/JWT/Influx 值从 secret/env 注入并启动时拒绝默认值；production 禁止启动 mock generator，DB/Redis/Influx/worker/outbox 任一不可用时 readiness 503，而不是降级到模拟数据。

### F-11 — HIGH — forecast policy gate 只校验 sidecar hash，任意 JSON 都可被判定 ready

**证据**：

- `main.py:295-320` 的 `_forecast_component_ready` 只检查文件存在、payload 是 dict、禁止 `content_sha256` 字段，并比较 canonical JSON 的 SHA-256 sidecar；没有检查冻结的 closed schema、network entry、probability/rule ID/hash、artifact schema/validity/domain。
- 契约明确要求这些条件（`IMPLEMENTATION_CONTRACT.md:1010-1015`）。
- 定向诊断用不合法但匹配 sidecar 的 JSON 得到：
  ```text
  ARBITRARY_POLICY_COMPONENT_READY= True
  ```

**问题/影响**：运维只需提供一个任意对象和匹配 hash 就能让 production forecast middleware 放行；scheduler/API 可能继续使用不存在、过期或不适用的 policy，生成不可发布/错误告警。

**建议**：实现 closed-schema validator 和 artifact registry 查询，逐 network 校验 flood/surcharge probability/rule 的 ID、hash、schema、validity、domain；任何缺失/未知/重复/路径不合规都 fail closed，并增加 golden policy fixture。

### F-12 — HIGH — `/readyz` 只验证 Alembic head/profile，无法发现缺失 worker/outbox/extension/table

**证据**：

- `app/core/database.py:152-207` 只查询 `alembic_version` 和 `hydraulic.schema_metadata`，最终 `ready` 条件仅为 head、metadata revision、spatial profile 三项相等。
- `main.py:456-462` 直接把该结果作为 `/readyz`。
- 契约要求 readiness 检查 DB SELECT、schema profile、Timescale extension/hypertable、PostGIS（按 profile）、network-job/simulation/nowcast worker registration、artifact config、outbox relay heartbeat、migration state（`IMPLEMENTATION_CONTRACT.md:2129-2134`）。
- F-01 已证明当前 metadata/migration 缺少多张必需表，而 readiness 代码不会检查它们。

**问题/影响**：一个“head 正确但表/扩展/worker/outbox 不完整”的数据库可能被报告 ready，Compose/API 会继续接流量；这会把 F-01/F-02 的部署故障隐藏在健康检查之后。

**建议**：将 readiness 拆成有界、fail-closed 的 DB checks（表/列/constraint/extension/hypertable）、worker registration/heartbeat、outbox relay heartbeat、policy artifact checks；任一缺失返回 503，并增加缺表/缺扩展/worker 停止的负向测试。

### F-13 — MEDIUM — hydraulic rate limiter 是无界进程内字典，且在认证前按任意 Authorization 建 key

**证据**：

- `main.py:292` 定义全局 `_rate_buckets`；`:351-399` 对 `/api/...` 先读取原始 `Authorization` header、hash 后写入字典，没有 TTL、容量上限或清理。
- 中间件在路由依赖认证之前执行，因此未认证请求也会创建 key。
- 直接调用该 middleware 1000 次、每次使用不同 header 的诊断输出：
  ```text
  RATE_BUCKET_KEYS_AFTER_1000_UNAUTH_HEADERS= 1000
  ```

**问题/影响**：攻击者可持续发送随机 bearer 字符串造成进程内存增长；多 API 进程之间限流状态也不共享，不能作为生产 abuse control。

**建议**：先认证并以稳定 user/service principal 建 key；使用有 TTL/容量上限的共享 store（Redis/数据库）或进程内有界淘汰，并补未认证、随机 token、多进程和 bucket rollover 测试。

### F-14 — MEDIUM — focused integration tests 不能覆盖真实 lifespan、durable DB 或非零指标

**证据**：

- `tests/integration/test_backend_integration.py:59-73` 只用 `httpx.ASGITransport(app=app)` 请求 `/metrics`，没有启动 lifespan、worker、DB、Redis 或 outbox。
- `tests/conftest.py:10-20` 使用 SQLite `Base.metadata.create_all()`；这与冻结契约要求的 PostgreSQL/Timescale/PostGIS 或 portable live migration 不同。
- 测试断言只检查名称 substring（`test_backend_integration.py:53-56,70-73`），不检查 snapshot/terminal/relay 后的非零值。
- 本次 focused suite 虽然 `69 passed`，但同一工作区的真实 `TestClient` lifespan 诊断失败，说明该绿灯不能代表“应用可启动”。

**问题/影响**：回归测试会给未接线的 singleton、内存 store、缺表和启动失败提供假绿；无法证明用户要求的端到端 API/worker/metrics 链路。

**建议**：增加真实 lifespan smoke、DB repository round-trip、worker claim/lease/heartbeat/terminal、两 profile migration、outbox replay/restart、HTTP scrape 非零值测试；SQLite 测试只能保留为单元层，不得作为 integration gate。

## 4. 已确认的优点

- `app.main` 的 singleton import 确实早于 `Instrumentator`；AST 顺序回归和 Python 3.11 `generate_latest(REGISTRY)` 均通过。
- 本次目标 Python 3.11 的四个指定测试文件为 `69 passed`，说明 collector 本身、solver contract 和 SQLite schema 约束在 focused 范围内没有回归。
- `ResultRetentionPolicy` 的 ORM `default=uuid.uuid4` 与迁移的 PostgreSQL `server_default` 分层在本次指定 schema 测试中通过；这不能替代 live migration gate。

## 5. 发布判定

**不批准。** 至少 F-01～F-12 中的任一项都会阻断“真实端到端后端”或生产 readiness；在补齐 durable schema、worker/API repository、outbox/replay/notification wiring、production fail-closed 和真实 startup/E2E 证据前，不应把 `/metrics` 名称存在、focused pytest 绿色或实现摘要中的历史全量结果宣称为后端集成完成。
