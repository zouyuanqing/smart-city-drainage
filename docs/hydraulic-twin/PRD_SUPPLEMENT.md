# 排水数字孪生 PRD 补充与验收基线

> 状态：**Frozen v3 — APPROVED**。通过轮次：最终独立复审（整改后第 1 轮，reviewer=`dynamic-workflow v3-final-review-1`，2026-09-24）；复审报告：`reviews/v3-schema-semantics-review.md`（`PASS / R0_DESIGN_PASS`）。本批准仅覆盖阶段 D 设计冻结；实现与门禁仍按既有 blockedBy/phase 语义执行。
>
> 日期：2026-09-24。
>
> 上游需求：`SOURCE_PRD.md`；技术落位：`IMPLEMENTATION_CONTRACT.md`。
>
> 规范词：**MUST / MUST NOT / SHOULD / MAY** 分别表示必须、禁止、应、可选。
>
> 证据边界：本文只整合本次实际读取的源码、六份 recon、五份被取代的 supporting contracts 和工作流草案。带行号的源码结论可点击复核；recon 中的历史测试结果只作为“报告所述”。本轮没有重跑后端、前端、Docker、迁移、solver/worker 或性能测试。
>
> 仓库基线：`ca99e04 chore: remove stray isort diff log`。整改轮执行 `git -C "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage" rev-parse --short=8 HEAD`，输出 `ca99e048`；契约统一引用 `ca99e04`。

## 0. 设计审查处置与 v3 约束

### 0.1 处置

- **不批准 v1 或 v2。** v2 仍存在会改变 migration、API/SSE、worker claim 和验收结果的跨模块矛盾：共享文件 ownership、Timescale frame unique、retry 可执行时间、错误码/幂等、forecast 状态机、观测可信度、reserved slot、缺失工件、initial-state hash 与 G5 harness。
- `SOURCE_PRD.md` 正文仍是不可变需求输入；本轮只修订补充基线与实施契约，不把整改决策伪装成源 PRD 原文。
- `contracts/*.md` 保持 **SUPERSEDED / NON-NORMATIVE**；不得用其中旧状态机、路径、命令或阈值覆盖 v3 候选。
- 本轮仍只形成 v3 审查候选；未创建 gate/harness/fixture、未运行任何实现或运行时套件，不宣称已批准或已通过。

### 0.2 v3 最低整改基线

1. **Schema/semantics 先于并行实现**：先冻结 frame registration/hypertable 唯一性、四类 durable job 的 `next_attempt_at`、单一错误码/幂等矩阵、完整 forecast transition table、观测 trust level、reserved-slot 禁借规则、缺失工件 fail-closed 行为，以及 network revision / initial state / materialized snapshot 三类独立 hash；simulation request fingerprint 另绑定 target-manifest hash。
2. **唯一 owner、授权例外与显式依赖**：全周期 owner 见 IMPLEMENTATION_CONTRACT §15.1～15.6；dependency-config 本阶段唯一授权清单为 §15.0 `DEP-AUTH-01..13`，其中 `DEP-PREP allowed` 是 R0 PASS 前工作树可出现的唯一子集，`DEP-LANDING allowed` 仅在 R0 PASS 后生效。PRD/§12/§16/发布阻断不得复制另一套路径集。schema owner 独占全部 hydraulic ORM/migration。
3. **调度可恢复**：simulation/network-job/nowcast/calibration 四类 durable job 的 `retry_wait` 必须持久化 `next_attempt_at`、backoff 和 `last_retry_error_code`；claim 只取 `next_attempt_at<=DB now` 的行，terminal/claim 时清空。
4. **单一 API/状态语义**：`UNSAFE_PATH=422`；import/validate/seed 三个提交端点都必须有 8..128 字符 `Idempotency-Key`；forecast 的 create/acknowledge/observe/refresh/escalate/resolve/expire、终态、抑制和 outbox 行为由一张表唯一决定。
5. **可信输入边界**：观测 trust level 由版本化 source policy 决定，不由字符串 `source` 自报；只有 `external_verified` 可参与正式同化、observed transition、confidence 验证和 AC-5。
6. **Nowcast 资源/工件**：reserved slot 在 v1 不借给 general simulation；工件在调度前缺失/过期/越域则不创建 run 并返回 policy-unavailable reason，工件在运行中失效则 terminal failed，均不生成 `publishable=false` 正式时隙。
7. **Hash 归属**：三类独立 hash（network revision、initial state、materialized snapshot）之外，simulation request fingerprint 还必须绑定 target-manifest hash。
8. **Gate/fixtures 最后补齐且同轮执行**：冻结 G0～G6、solver/API/nowcast/frontend 四类 G5 harness、固定 fixture/manifest/命令；实现完成后在同一候选提交上执行 G0～G6 和 AC-1～AC-8，缺任一入口或 artifact 均为 NOT RUN/FAIL。

### 0.3 v3 独立复审状态门禁

- 当前状态：`R0_ROUND_3_FAILED / NOT_APPROVED / ALL_IMPLEMENTATION_WORK_BLOCKED`。
- 最新 round-3 reviewer=`dynamic-workflow v3-schema-semantics-review-3`；人类证据路径为 `reviews/v3-schema-semantics-review-round-3.md` 与同步覆盖的 `reviews/v3-schema-semantics-review.md`。候选 PRD/实施契约不得内嵌历史 review 的文件 hash、输入 hash 或 document bundle；下一轮 reviewer 必须从 live files 重算，且只有最终批准 artifact 可以记录当轮可重算 bundle。
- round-3 verdict 为 `NOT_APPROVED / R0_FAIL`，确认 §3.1～§3.8、B-01～B-07 与上一轮 §4 PASS 项保持，并发现 C-01～C-04；本次编辑会再次改变三文档 bundle。整改后 bundle 不在本文自引用，下一轮 reviewer 必须从 live files 重算并写入新 review/JSON；gate/checker/harness 实际执行继续 NOT RUN，静态整改不能当作 R0 PASS。
- R0 JSON 唯一路径为 `artifacts/contracts/v3-independent-review.json`，owner=`r0-reviewer`。由于 `docs/` 当前未跟踪，artifact 不再要求不存在的 reviewed commit；改用 `base_commit + document_bundle_sha256 + 三文档逐文件 SHA-256`。bundle 定义见 IMPLEMENTATION_CONTRACT 17.0，任一文件变化立即使旧 verdict 失效。
- **verdict=PASS 前不得创建、修改或重命名 `backend/alembic/versions/*`，不得新增/改写 ORM migration，不得运行/宣称 G1 PASS，dependency patch 不得 merge/落地。**
- 复审 PASS 后，dependency-config 最小 patch 先落地并生成 handoff；hydraulic-schema 才能开始 linear migrations。后续 integration owner 接手 shared path 时必须保留 dependency-config 产物。

### 0.4 设计冻结与实现验证是两个阶段

- **阶段 D（当前）：设计审查/冻结候选。** 独立复审只判断 schema/semantics、每路径恰一 owner、blockedBy、dependency handoff 保留谓词，以及 G0～G6/AC 命令、fixture、阈值定义是否无矛盾且可定位。只有新的独立复审 verdict=PASS 后，独立复审者/文档 owner 才可更新批准状态；本轮保持候选状态，MUST NOT 自行写 Frozen。
- **阶段 V（后续）：实现与运行验收。** 只有阶段 D 独立通过、授权 handoff/migration/实现完成后，才按同一候选 commit 与 RUN_ID 实际执行 G0～G6 和 AC-1～AC-8。缺失脚本、fixture、artifact 或未执行命令固定记为 **NOT RUN**；NOT RUN 不能被设计复审改写为 PASS，也不能反向冒充设计已实现。
- 两个阶段不得互相替代：设计复审 PASS 不等于任何 G0～G6 PASS；后续 G0～G6 PASS 也不能倒推替代独立设计复审。

## 1. 补充目标与不可变边界

源 PRD 给出 FR-1～FR-7、AC-1～AC-8，但以下问题未定义，必须由本补充冻结，否则不同模块会产生不兼容实现：

1. 传感器事实源与 mock/真实数据边界；
2. PostGIS 与无 PostGIS 路径的物理模型和切换规则；
3. network revision、A/B 和历史结果的可重跑边界；
4. solver 身份、EPA 优先与禁止同任务静默降级；
5. 持久任务状态机、租约、取消、超时和重试；
6. nowcast 3 分钟 SLO 与总并发 2 的资源关系；
7. 告警处置状态与“预计/已发生”证据阶段的正交关系；
8. 30 分钟抑制、事件窗、幂等和外部投递语义；
9. 20 MiB 输入上限与大规模输出的容量冲突；
10. API P95、180 s、25 fps、90 天 retention 的精确测量边界；
11. 401/403/409/410/422/429/503/504 的用户可见语义；
12. 每个 AC 的机器证据、目标环境和通过门槛。

本补充不扩大产品范围，不把领域契约中的“可实现替代方案”反向改写成源 PRD 原文。

## 2. 当前基线与实施起点

### 2.1 已有可复用骨架

- FastAPI 已有 lifespan、统一 `/api` router、Prometheus `/metrics`、请求 ID 和处理时间中间件：`backend/app/main.py:101-218,240-265`。
- 已有 async SQLAlchemy engine/session、pool pre-ping 和事务回滚模式：`backend/app/core/database.py:57-129`。
- 已有 `viewer/operator/admin` 角色和声明式 RBAC 工厂：`backend/app/models/db_models.py:35-38`、`backend/app/core/security.py:108-135`。
- 已有 Redis Pub/Sub、SSE 客户端队列、心跳和有限 `Last-Event-ID` 原型：`backend/app/core/redis_client.py:50-164`、`backend/app/services/sse_manager.py:32-189`。
- 已有 InfluxDB 传感器 measurement 读写封装：`backend/app/services/influxdb_service.py:70-212`。
- 前端已有 React 18、Leaflet、ECharts、Zustand、Vite/Vitest 和 Axios Bearer 注入：`frontend/package.json:1-61`、`frontend/src/services/api.ts:11-39`。

### 2.2 不可直接复用的边界

- 当前 ORM 只有六张旧域表，没有 network、rainfall、simulation、frame、forecast 或 calibration 实体：`backend/app/models/db_models.py:46-295`。
- 传感器唯一自动写路径是 mock generator 写 Influx，再广播 SSE/Redis：`backend/app/services/mock_data_generator.py:147-200`；没有真实硬件 ingest API。
- 传感器 API 在 Influx 无数据时静默回退 mock/random：`backend/app/api/routes.py:589-649`。水力域 MUST NOT 复制此行为。
- schema 有三条路径：Compose 挂 `docker/init-db.sql`（`docker-compose.yml:19-22`）、Alembic 0001（`backend/alembic/versions/0001_initial.py:20-258`）、应用启动 `create_all`（`backend/app/main.py:132-141`）；且 init SQL 独有 `sensor_readings`：`docker/init-db.sql:100-118`。
- init SQL 使用 PostGIS 函数但未启用 PostGIS：`docker/init-db.sql:4-9,76-79`。
- 当前 SSE 事件 ID/历史只在单进程内存，最多 1000 条，队列满即断连：`backend/app/services/sse_manager.py:32-43,90-120,142-189`。
- 当前告警 producer 先本地广播再 Redis 发布，应用又订阅 Redis，存在重复广播路径：`backend/app/workers/inference_worker.py:191-223`、`backend/app/main.py:78-94,143-152`。
- 当前通知是 async 函数中的同步 SMTP/webhook，无投递记录和重试：`backend/app/services/notification_service.py:27-107`。
- 当前前端没有 `/simulation`，路由守卫只检查 token 是否存在：`frontend/src/App.tsx:17-100`；地图逐设备创建 DOM Marker：`frontend/src/components/map/MapVisualization.tsx:26-40,274-295`。
- 当前依赖没有 SWMM、GeoAlchemy、Shapely 或其他水力内核：`backend/requirements-cpu.txt:1-34`。

## 3. v3 候选产品决策（未批准）

### D-01：事实源分层，不允许多个“当前值”

| 事实 | 唯一事实源 | 其他存储的职责 |
|---|---|---|
| network revision、simulation 状态、frame、forecast alert、事件审计 | PostgreSQL/Timescale | Redis 只唤醒/扇出；前端只缓存 |
| 现有传感器原始历史 | InfluxDB `sensor_readings` | PostgreSQL `observation_samples` 是带来源指针/hash 的规范化、append-only 投影 |
| 降雨情景 | PostgreSQL immutable scenario revision | worker materialize 为内存/沙箱输入，不另建可编辑事实源 |
| 求解器结果 | `simulation_frames` + simulation summary/quality report | 临时文件不是完成结果；导出是派生物 |
| 告警处置状态 | `forecast_alerts` + transition audit | 旧 `public.alerts` 保持 legacy 行为，告警中心双源合并 |

MUST NOT 以 Redis key、进程内变量、浏览器 localStorage 或 mock fallback 表示任务/告警完成状态。

### D-02：传感器事实源与生产可用性

1. **现有事实源是 InfluxDB。** 当前 `InfluxDBService` 写 measurement `sensor_readings`：`backend/app/services/influxdb_service.py:70-124`。因此新增水力观测适配器 MUST 保留 `source="influx"` 的来源，不得声称 Timescale `sensor_readings` 是当前运行事实源。
2. **规范化投影用于率定和同化，但幂等边界先落在 source event。** `hydraulic.observation_ingest_events` 以 `UNIQUE(source_system, source_stream, source_partition, source_offset)` 接收一次真实来源事件，并保存 `source_event_id`（由该四元组 canonical hash 得出）、payload hash、source observed time、received time、adapter version 和 trust policy/version。`hydraulic.observation_samples` 通过 `ingest_event_id` 外键保存 target/metric/value/unit/quality/raw ref；同 event identity + 同 payload hash 重放返回既有 event/rows，同 identity + 不同 payload 返回 `SOURCE_EVENT_CONFLICT`，不得静默忽略。旧 Influx 点若不能提供稳定 partition/offset 或等价上游事件 ID，只能标为 `external_unverified`，不得宣称 source-event exactly-once、不得用于正式 forecast 或 AC-5。
3. **可信等级固定且由 policy 决定：**
   - `external_verified`：真实来源、source event identity 完整、`auth_verified=true`、active device-network binding 存在、quality=good；可用于生产同化、observed transition、AC-5 和正式 forecast；
   - `external_unverified`：真实来源但缺任一上述条件，只用于诊断/展示，不得进入正式同化、observed、confidence 验证或 AC-5；旧无稳定 event ID 的 Influx 点固定属于此级；
   - `synthetic`：固定算法/seed 的实验数据，不是观测；
   - `mock`：smoke/UI 数据，不是观测。
4. `trust_level` MUST 由 `observation_trust_policies(source_system,policy_version,auth_requirement,binding_requirement,trust_level,valid_from,valid_until)` 解析，客户端、payload `source` 字段或 adapter 返回值不得自报升级。event 保存 nullable policy ID/version/hash、`trust_policy_resolution`、`auth_verified`、`binding_verified`。无匹配 policy 时 policy 列为 NULL、resolution=`missing`、trust=`external_unverified`、status=`quarantined`；`quarantined` 只能是 ingest status，不是 trust level。有效期重叠由 DB exclusion constraint 拒绝。
5. 当前仓库没有真实采集写入口；因此实现完成前，外部观测能力 MUST 显示 `not_configured/not_ready`，MUST NOT 用 mock 填平。
6. 设备必须通过 `device_network_bindings` 显式绑定 node/pipe/segment；MUST NOT 仅按经纬度最近点自动宣称绑定。当前 `Device` 只有普通经纬度：`backend/app/models/db_models.py:84-117`。
7. `bad/missing/stale` 观测不参与率定；未来时刻观测不得进入当前状态的训练或评估输入。

### D-03：Alembic 是唯一可部署 schema 事实源

- 所有 hydraulic 表、enum、索引、Timescale hypertable、retention 和 seed revision MUST 由 Alembic 管理。
- `docker/init-db.sql` 只可保留 extension bootstrap；不得继续独立创建业务表、hypertable 或用户/设备种子。依据是当前 init SQL 与 0001 已发生表集合漂移：`docker/init-db.sql:100-118` 对比 `backend/alembic/versions/0001_initial.py:25-228`。
- 生产启动 MUST 移除 `create_all_tables()`；`/readyz` 必须校验 `alembic current` 等于镜像声明 head。
- migration 发布采用 expand → backfill/verify → app switch → 后续 contract；生产回滚以旧应用镜像 + additive schema 为主，不自动 destructive downgrade。

### D-04：空间 profile 固定为 `postgis | portable`

- 一个数据库从创建到退役 MUST 只使用一个 `HYDRAULIC_SPATIAL_BACKEND` profile。
- 两个 profile 都使用 PostgreSQL + TimescaleDB；`portable` 仅表示不安装/不依赖 PostGIS，不表示可以删除 Timescale 帧与 retention 能力。
- 公共逻辑列永久保存规范化 `geometry_wkt` 和数值 bbox；API/worker 不感知物理 profile。
- `postgis` profile 在相同逻辑表上增加由 WKT 生成的 typed geometry 与 GiST；`portable` profile 不创建 PostGIS 依赖，永久保存 WKT+bbox，使用 bbox 粗过滤和同一 geometry codec 精确判定。
- 对外坐标固定 `EPSG:4326`、lon/lat；局部工程坐标必须携带可验证 transform，否则拒绝仿真提交和 bbox/GeoJSON 导出。
- profile 缺失或与数据库不匹配时 fail closed，返回 `SPATIAL_BACKEND_UNAVAILABLE`；MUST NOT 在同一数据库运行时“悄悄切换”。

### D-05：network revision 不可变

- network 是逻辑聚合，network revision 是一次不可变快照。
- 对 active network 的 PATCH、导入、seed 更新 MUST 创建新 revision；simulation/forecast/A/B 只引用 `network_revision_id`。
- `content_sha256` 对 canonical JSON 计算；原始上传另存 `source_sha256`。当前设备坐标字段不能证明拓扑（`backend/app/models/db_models.py:99-103`）。
- revision、node、pipe、catchment、profile 一经创建即不可物理删除。`DELETE /api/networks/{id}` 的 v1 语义固定为幂等逻辑 archive：设置 `status=archived`、阻止新 simulation/nowcast，保留 current revision 与全部历史；重复 DELETE 返回 204。物理 purge 不在 v1 API，确有历史和 import/simulation/alert 引用的数据永远禁止 purge。

### D-06：solver 身份与 EPA 优先

稳定 solver ID：

| solver_id | 含义 | 产品保真声明 |
|---|---|---|
| `swmm_kw` | EPA SWMM 运动波 | engine exact，仍需输入/率定验证 |
| `swmm_dw` | EPA SWMM 动态波 | engine exact，不等同 `py_dw` |
| `py_mw` | 纯 Python 运动波 | fast screening；5000/10000/24h 的首发性能硬门禁 |
| `py_dw` | 纯 Python 扩散波 | reduced fidelity；限缓坡、亚临界 |
| `py_ponding` | 预离散简化积水单元 | reduced fidelity；不得称二维水动力真值 |

提交使用 `solver_selection=auto|explicit`。`explicit` 必须给出 `requested_solver_id`，不可用时 503。`auto` 在提交时 probe：给出 requested solver 时优先使用该 solver（EPA 不可用则冻结为 `py_mw`），未给出时优先 `swmm_kw`、再退到 `py_mw`，并记录 fallback reason。**启动后 solver 失败 MUST NOT 在同一 `simulation_id` 下换 solver**；用户要比较降级结果必须另建任务。

### D-07：20 MiB 是不可信输入预算，不是结果总容量

- `SIM_INPUT_MAX_BYTES=20*1024*1024`，约束单个 INP/GeoJSON 及 materialize 后输入物料。
- 24 h/5 min 含首尾共 289 帧；5000 nodes + 10000 pipes 共有 4,335,000 个对象-帧状态，因此 20 MiB 不可能是全量结果工作区上限。
- worker 私有工作区默认上限 2 GiB；单 frame 默认 64 MiB、单个 simulation 默认 8 GiB，均可调低。超限为 terminal、不可重试的 `OUTPUT_LIMIT_EXCEEDED`。
- 帧 MUST 流式写 TimescaleDB；MUST NOT 先在内存或沙箱积累全部 289 帧。

### D-08：durable job 统一状态机

本节不重复维护任何转换行。simulation、network job、nowcast run、calibration run 的唯一规范状态集、全部转换行及 nowcast/general 守卫是 `IMPLEMENTATION_CONTRACT.md` §6.1 的单一转换表；四类 repository/worker MUST 调用同一 `durable_job_state` policy，禁止复制、删减或增补转换。本文及 supporting/recon 文档均不得另列第二套 durable-job 状态机。nowcast 的 `scheduled_for` 是 due time，不是状态。

- `attempt=0` 表示尚未执行；首次 claim 为 1；`max_attempts=3`。
- `progress` 是已提交的单调 0..100；100 只在所有 frame、summary、quality report 成功提交后出现。
- worker heartbeat 最长 1 秒响应取消，外部 solver 必须杀整个进程树。
- 每次 attempt 的 wall-clock 上限 600 s，包含 materialize、solver、frame serialization 和提交前校验。
- timeout、用户取消、非法输入、能力不支持、输出超限不自动重试；worker crash、lease 过期、瞬态 I/O/数据库错误可重试，最多两次。
- 进入 `retry_wait` 的同一事务 MUST 写 `next_attempt_at=DB now()+backoff`、`retry_backoff_seconds`、`last_retry_error_code`；claim 只选择 `status=retry_wait AND next_attempt_at<=DB now`。成功 claim、取消或终态 MUST 清空该字段，索引固定 `(status,next_attempt_at)`。
- claim 使用数据库 lease/fencing token；旧 worker 即使恢复也 MUST NOT 提交 frame 或 completed。
- 每次 claim 创建 `simulation_attempt`。frame 只写 attempt-scoped staging；普通 PG `simulation_frame_registrations(attempt_id,frame_index)` 是唯一幂等键，Timescale hypertable 只以含 `frame_time` 的主键约束分区数据，二者必须同事务。`simulations.published_attempt_id` 是唯一可见指针。
- attempt 发布前不可被结果 API 读取。finalize 事务必须验证 frame 数量/时间网格/target manifest/hash/summary/quality，再原子设置 attempt=`published`、更新 `published_attempt_id` 和 simulation=`completed`。
- retry/timeout/cancel 将旧 attempt 置为 `discarded`；新 attempt 重新求解，旧帧永不删除后复用 slot、也永不成为部分结果。相同 attempt 的恢复写仅在 payload hash 完全相同时幂等，否则 `FRAME_ATTEMPT_CONFLICT`。
- 成功发布只移动可见指针，不复制大体量 frame；discarded staging 由 retention/cleanup sweeper 清理，清理失败不回滚 published 结果。

### D-09：并发 2 与 nowcast 3 分钟 SLO

- 全局求解槽位固定为 2，nowcast 保留槽固定为 1；二者都是不可配置 Literal。数据库 `worker_slots` 初始化恰好两行且 `busy=false`，此时 worker/job/run/lease/heartbeat 必须全部为 NULL。claim 置 busy=true，按 lane 绑定对应 job/run，并同时写 `heartbeat_at=DB now`、`lease_until=DB now+30s`；busy=true 时 lease/heartbeat 必须非空且 `lease_until>heartbeat_at`，lane 绑定 CHECK 只在该分支生效。heartbeat 每次原子刷新这两个时间戳。状态进入 `cancelling` 时立即释放槽；到达 `cancelled|completed|failed|timed_out` 任一 terminal 后，以及 timeout/crash recovery claim 时，绑定字段必须仍为空且 busy=false。Redis 不承担并发事实。
- slot 1=`general`，slot 2=`nowcast`；**v1 禁止 general simulation 借用 slot 2，即使该槽当前空闲**。nowcast claim 只能绑定 nowcast_run，general claim 只能绑定 simulation；import/validate/seed 使用独立 network-job concurrency，不能占用求解槽。
- 因此 general 的持续容量固定为 1，nowcast 槽空闲时允许闲置；不得用“预计能在 180 秒前结束”抢占，因为 general attempt 可持续 600 秒且估计不可作为迁移/调度事实。
- nowcast SLO 从 `scheduled_for` 到 alert + event outbox 原子提交，P95 和每次最大值均 `<=180 s`；排队等待计入 SLO，普通 simulation 的 180 s solver 指标不计入。
- nowcast 关联 simulation 的业务 deadline 固定为 `scheduled_for+180 s`（即使同一 simulation 表的普通 attempt 上限为 600 s）；未在 180 秒内完成标记 timed_out，late result 永不发布告警。

### D-10：forecast alert 唯一状态机

处置状态和证据阶段必须分别判断；每次变化使用 `expected_version` CAS，并在同一事务写 alert、transition audit、必要的 event outbox。

| action | 当前 status | 当前 phase | 守卫 | 结果 | business event |
|---|---|---|---|---|---|
| create | 无 | predicted | policy 工件有效、候选超阈、非冷却 | `open/predicted` | `alert.created` |
| acknowledge | open | predicted/observed | operator/admin、reason 非空 | `acknowledged`，phase 不变 | `alert.updated` |
| observe | open/acknowledged | predicted | `external_verified`、auth/binding/quality good；`issued_at<=observed_at<=expected_at+expiry_grace`；按 rule operator 越过 threshold | phase=observed，status 不变 | `alert.observed` |
| refresh | open/acknowledged | predicted/observed | 四项绝对差任一 `>=` rule 对应 refresh delta | 始终更新值，不改 status/phase | 命中才 `alert.updated` |
| escalate | open/acknowledged | predicted/observed | level 只能升高 | 更新 level/risk | `alert.escalated` |
| resolve | open/acknowledged | predicted/observed | 人工 reason；或 clear telemetry 连续满足 rule debounce；或连续 N 个 fresh run 不再预测 | `resolved` | `alert.resolved` |
| expire | open/acknowledged | predicted | `now>expected_at+expiry_grace`，或版本化 rule 的等价过期条件 | `expired` | `alert.expired` |

- refresh 的 delta 和 `>=` 运算符全部来自绑定的 forecast_rule：expected shift steps、peak delta、confidence delta、risk delta；四项任一命中才发 updated。
- observe 事件必须同 target/metric/unit，`observed_at` 位于 `[issued_at, expected_at+expiry_grace]`；窗口外数据不得改变该 episode。
- 公共 transition API 只接受人工 `acknowledge|resolve`；create/observe/refresh/escalate/expire 是版本化 rule 驱动的内部事务动作，客户端不得伪造。
- `expected_at` 到达、时间推进、acknowledge、refresh、risk 变化均不得产生 observed；observed 只能由表中唯一 `observe` 转换。
- `resolved/expired` 是终态，status 和 phase 均不得再变；同一 episode 不 reopen。冷却窗外的真实复发创建新 `alert_id`。
- `observed` 告警不能 expire，只能人工或合格 clear evidence resolve。`ignore` 不是状态或 action。
- `create/observe/refresh/escalate/resolve/expire` 的自动执行者必须写 `actor_id=null` 和稳定 reason code；人工 action 写 user ID。重复命令由 `Idempotency-Key + expected_version` 处理，不用“当前状态看起来相同”猜测幂等。

`issued_at` 是输入、工件和结果事务冻结后的业务发布时刻；`forecast_origin_at` 是 5 分钟网格原点；nowcast `published_at` 是 alert/outbox 与 run completed 同一 finalize transaction 提交后、relay 首次可读取 outbox 的时刻，不是 Redis relay 已处理时刻。三者不得互相替代；`event_outbox.published_at` 仅由 relay 成功发布后写入。`lead_time_minutes=max(0,floor((expected_at-issued_at)/60s))`。

freshness 独立为 `FORECAST_FRESH_MAX_AGE_SECONDS=900`；180 秒 deadline 只决定当次 run 是否可发布。`confidence` 只能是绑定有效 probability-calibration 工件的阈值超越概率，MUST NOT 复用 AI detection confidence、solver success probability 或固定 0.5。

工件规则：每个 nowcast run 为 flood/surcharge 分别以 `(artifact_id,artifact_sha256)` 复合 FK 绑定 calibration/rule。`FORECAST_POLICY_CONFIG_PATH` 指向的 closed JSON 不含自身 hash；其 canonical bytes 由同目录固定 sidecar `<FORECAST_POLICY_CONFIG_PATH>.sha256` 保存 64 位小写 hash，启动/gate 必须重算并逐字比较，缺失或不匹配即 component not ready。调度前 policy 工件缺失、过期、越域或 config/artifact hash 不符时不创建 run，记录 `FORECAST_POLICY_UNAVAILABLE` 并使 forecast `not_ready`；run 创建后到 finalize 前失效则 run terminal failed `FORECAST_POLICY_INVALID`，均不产生正式 horizon point、alert 或 outbox。历史只读查询可以返回旧数据，但不得重新发布。

### D-11：30 分钟事件窗

`suppression_key` 的唯一输入是 canonical JSON object：

```json
{"schema":"forecast-suppression-v1","network_id":"...","alert_type":"flood_forecast|pipe_surcharge","target_type":"node|pipe_segment","target_id":"...","rule_id":"..."}
```

按 IMPLEMENTATION_CONTRACT 1.2 序列化后取 SHA-256；MUST NOT 使用管道拼接或依赖字段书写顺序。MUST NOT 包含 phase、level、confidence、expected_at、lead time、simulation_id、nowcast_run_id 或当前时间。

- 活动期持续复用同一 `alert_id`；predicted→observed 是同一 alert 的 phase transition，不创建第二个活动 episode。
- 终态后半开冷却窗 `[terminal_at,terminal_at+30m)` 内不创建新 alert/SSE business event/notification，只增加 suppressed 计数。
- 若 predicted 已终态后才收到 observed candidate，在冷却窗内同样 suppressed；记录 observation receipt 和 suppressed metric，不得绕过 suppression 另建 observed alert。
- 冷却结束后的合格候选创建新 `alert_id`；新 episode 的 phase 固定从 predicted 开始，只有新的合格观测才能 transition 为 observed。

### D-12：幂等与 exactly-once 边界

- PostgreSQL 唯一约束、事务和条件更新保证业务行只创建一次；SSE/SMTP/webhook 只承诺 at-least-once。
- 唯一幂等矩阵如下；`Idempotency-Key` 均为 8..128 字符。幂等复用窗口固定 24h：创建时 `expires_at=created_at+24h`；窗口内同 scope+key+user+同 request hash 返回首次 status/body/resource，不同 hash 返回 409 `IDEMPOTENCY_KEY_REUSED`。过期记录先原子标记 retired，再以递增 generation 创建新记录；并发 lookup/retire/insert 由同一 scope/key/user advisory lock + active partial unique constraint 串行化。retired 记录用于 FK/audit，不与新 generation 混用。

| 操作 | 额外并发条件 | 重复语义 |
|---|---|---|
| POST network | key | 返回原 network |
| import/validate/seed network job | key | 返回原 network_job_id/status |
| simulation submit/cancel | key | 24h 窗口内返回原 simulation/首次 cancel response；过期后新 generation 可创建新 simulation，cancel 同样引用其 generation-specific record |
| rainfall create/update/copy | key；update 带 `expected_version` | 返回原 scenario/version |
| versioned node/pipe/catchment/profile create/update/delete | key + `expected_revision_id` 或 If-Match | 返回首次创建的新 revision；旧 expected 重放不产生第二 revision |
| revision clone/activate | key + `expected_revision_id` / `expected_current_revision_id` | clone/activate 首次结果；current 已前进但 key 不同则 409 |
| forecast transition | key + `expected_version` | 返回首次 transition response |
| calibration submit | key | 24h 窗口内返回原 calibration_run；过期后新 generation 可创建新 calibration_run |
| protected config/notification target mutation | key | 返回首次配置结果 |
| network archive DELETE | 无 key | HTTP DELETE 本身幂等，重复 204 |
| observation ingest | **禁止写 idempotency_records/HTTP key** | 只用 source event identity+payload hash；按 D-02 |

- `network_jobs`、`simulations`、`calibration_runs` 三张 resource 表各自只保留一个 `idempotency_record_id UUID NOT NULL FK -> idempotency_records.id`，并以 `UNIQUE(idempotency_record_id)` 保证一个 generation record 最多创建一个 resource；三表 MUST NOT 保存 `idempotency_key` 或声明永久 `UNIQUE(user/requested_by,idempotency_key)`。simulation submit 的 record 由 `simulations.idempotency_record_id` 引用；simulation cancel 使用独立 `scope=simulation.cancel` 的 idempotency record 保存 `resource_id/response`，在同一 cancel transaction 校验 simulation/user/fingerprint，不新增第二个 resource FK 列。calibration submit 同样只引用其单列。retired generation 保留审计但不阻止新 generation resource。
- API 只创建 idempotency record 与 durable job，不在请求进程执行解析、校验、seed、solver 或 notification。retention sweeper 至少每 5 分钟把 `expires_at<=DB now AND retired_at IS NULL` 的行原子置 `retired_at=expires_at`；`retired_at+90d` 后仅可删除无 resource/transition FK 引用的行，被引用记录继续保留审计。G2 必须覆盖未到期重放、冲突、到期后新 generation、同 key 并发和 FK 保留。

### D-13：结果保留

- Timescale 只承载有时序语义且体积大的 attempt-scoped `simulation_frames` 和规范化 `observation_samples`；默认 90 天。retention 删除 frame 时不得删除 `simulations.published_attempt_id` 所指 summary，也不得让 discarded attempt 被误发布。
- simulation summary、每个 attempt 的状态/manifest/hash、输入/solver hash、质量报告、forecast alert、transition、event audit、calibration/probability/rule 工件元数据和 calibration 报告保留在普通 PostgreSQL。
- frame 过期后 API 返回 410 `FRAME_RETENTION_EXPIRED`，仍返回 simulation 摘要和 `finished_at`；MUST NOT 删除告警审计或让 `simulation_id` 悬空。

### D-14：强类型求解输入与 target manifest

- solver 只能接收版本化 `NetworkSnapshot`；禁止把 ORM rows、未校验 `properties` 或任意 JSON 直接传入。
- snapshot 至少包含 `NodeSnapshot`、`PipeSegmentSnapshot`、`CatchmentSnapshot`、`PondCellSnapshot`、`BoundaryConditionSnapshot` 和 `InitialStateSnapshot`。所有数组为 tuple/list，引用使用 stable key + revision UUID；禁止重复/缺失/悬空引用。
- `PondCell` 必含有效面积/水面面积曲线或等强类型 volume curve、cell elevation、roughness、depression storage、cell links 和 sewer exchange links；`ponding_enabled=false` 时不得携带 active pond state。
- boundary 必须是 discriminated union：`none|free_outfall|fixed_stage|rating_curve|timeseries_inflow`；固定 stage/rating/time series 必须带单位、单调时间/流量校验和参数 schema version。
- initial state 只能是 `dry` 或绑定不可变 warm state；warm request 必须覆盖 node/pipe/pond 的全部对象且每个对象恰好一次，携带 `observed_as_of`、source event refs 和独立 `initial_state_sha256`。dry request 可省略 input ID，但 submit transaction 必须生成并持久化同 revision 的 immutable dry snapshot；`simulations.initial_state_id` 最终始终非空，并以 revision+ID+hash 复合 FK 绑定。禁止隐式使用“上次同网络结果”。
- `network_revision_content_sha256` 只覆盖 revision 的结构、几何、properties schema、boundary、ponding 和 target manifest 输入；**不包含 initial state**。materializer 另生成 `snapshot_content_sha256`，simulation request fingerprint 必须同时绑定 revision hash、snapshot hash、initial-state hash 和 target-manifest hash。
- `properties` 仅用于非求解语义注释，schema 必须 `additionalProperties=false`；物理字段不得藏在 properties。每个 revision 保存 `properties_schema_id/version/hash`，未知字段/版本在 import、CRUD、snapshot materialization 和 solver validate 均 fail closed。
- map-data 与唯一 target manifest 都按 stable key UTF-8 byte 升序；`target_types` 固定包含 `nodes,pipes,catchments,pond_cells`，每类 `feature_index` 必须是从 0 连续的 dense index。数据库对 `network_revision_id + schema_version` 建唯一约束，因此同一 revision/schema 只能存在一个 immutable target-manifest hash；simulation、attempt、frame 与 forecast alert 必须保存 network revision，并以 revision+manifest ID+hash 复合 FK 绑定，所有消费者校验同一 manifest；NULL 不得解释为 0。

### D-15：import/validate/seed 是 durable network job

- import/validate/seed 三个 POST 均要求 8..128 字符 `Idempotency-Key`；同 key+同 canonical request hash 返回原 job，不同 hash 返回 409。API 只校验认证、RBAC、输入元数据和 20 MiB 流式 quarantine，然后写 `network_jobs` + idempotency record，返回 202。
- `network_job_worker` 使用数据库 `FOR UPDATE SKIP LOCKED` claim、30 s lease、10 s heartbeat 和单调 fencing token；API 进程不得执行 parser/validator/seed。
- network job 进入 `retry_wait` 时同事务写 `next_attempt_at`、backoff 和错误；claim 只取到期的 retry job，不能依赖 Redis timer 或 worker 内存。deadline 超时进入 canonical `timed_out`，不得写 `failed` 代替。
- import/seed 的候选 revision、实体、validation issues、job result 在一个最终事务发布；crash/timeout/cancel 不留下半张 network。validate 只写 report/result，不改 revision。
- retry 只清理/替换当前 job attempt workspace；新 attempt 不能复用已丢弃 parser output。每个 job 记录 parser/profile/seed algorithm version、request/result hash、attempt 和 cleanup 状态。
- 固定 fixture 至少覆盖：最小合法 INP/GeoJSON、local transform、tree+loop、错误坐标/悬空/逆坡、恶意输入矩阵、seed 绑定候选和 expected validation golden JSON；相同 fixture/version 必须产生逐字节相同 canonical DTO/report。

### D-16：network DELETE 与历史完整性

- v1 DELETE 是逻辑 archive，不是物理删除；已 archived 重复 DELETE 幂等 204。
- archive 后禁止新 simulation/nowcast 和 CRUD mutation，但已提交 simulation 可继续使用其固定 revision 完成，已发 forecast 保持可审计。
- v1 API 不提供 purge。运维级物理清理不在本期，且任何引用链存在时 MUST refuse。

### D-17：forecast 安全门禁先于 API

- 在注册/公开 `GET /api/forecast/*` 和 `/api/forecast/events` 前，认证基础必须通过：仅允许锁定算法，校验签名、`exp/nbf/iat/sub/iss/aud` 和 role；缺/坏 token 为 401，角色不足为 403。
- 固定 admin fallback 和仓库默认 secret MUST 在生产 profile 启动时 fail closed；不得以开发 fallback 进入 forecast。
- forecast read、transition、simulation submit、network job submit、SSE connection 必须有独立 rate-limit bucket；429 返回 `Retry-After`。SSE 认证 JWT 只放 Authorization header。
- 安全门禁 artifact 必须覆盖缺 token、过期/错误签名/issuer/audience/role、viewer 写操作、横向 network id、限流 429 和日志不泄露 token。门禁未 PASS 时 forecast router 不得在 production 启动。

## 4. 降雨情景补充语义

1. 重现期只允许 `1/2/3/5/10/20/50/100/custom`。
2. 设计雨型步长只允许 `1/5/15 min`；自定义最大 72 h，且 `sample_count=floor(duration/step)+1`。
3. `RainfallSource` v1 只提供 `synthetic` 实现；接口必须固定 source/version/seed/algorithm/version/content hash。
4. 相同 network revision、scenario、solver、parameter set 和 idempotency key 重跑必须得到相同输入 hash 和可比较结果。
5. scenario 一旦被 simulation 引用 MUST NOT 原地修改；编辑或复制产生新 version/ID。
6. 非负、有限数、时间严格递增、总时长和 catchment 面积分配在提交前验证。

## 5. 导入、导出与拓扑异常语义

### 5.1 文件与解析异常

| 场景 | HTTP/任务结果 | code | 重试 |
|---|---|---|---|
| 超过 20 MiB | 413 | `INPUT_TOO_LARGE` | 否 |
| 非 `.inp/.geojson`、magic/编码不符 | 415 | `UNSUPPORTED_IMPORT_FORMAT` | 否 |
| `..`、盘符、UNC、symlink/junction | 422 | `UNSAFE_PATH` | 否 |
| INP 语法/重复 section/缺坐标 | 422 | `INVALID_INP` | 否 |
| GeoJSON 非 FeatureCollection、NaN/Infinity、过深嵌套 | 422 | `INVALID_GEOJSON` | 否 |
| 局部坐标无 transform | 422 | `CRS_UNKNOWN` | 否 |
| storage/profile 不一致 | 503 | `SPATIAL_BACKEND_UNAVAILABLE` | 否，部署修复 |
| 临时 DB/I/O 失败 | job retry_wait | `TRANSIENT_IMPORT_ERROR` | 最多两次 |

读取到 `limit+1` 立即中止并删除；用户文件名只作展示元数据，API 不返回绝对路径。输入白名单和 task root 规则见实施契约。

### 5.2 拓扑报告

| code | severity | 导入 | simulation-ready |
|---|---|---|---|
| `MISSING_COORDINATES` | error | 阻断 | 阻断 |
| `DANGLING_NODE` | error | 阻断 | 阻断 |
| `DUPLICATE_SEGMENT` | error | 阻断 | 阻断 |
| `SELF_LOOP` | error | 阻断 | 阻断 |
| `MISSING_INVERT` | error | 可导入 | 阻断 |
| `REVERSE_SLOPE` | error | 可导入 | 阻断 |
| `DISCONNECTED_COMPONENT` | warning/error | 依导入模式 | 阻断 |
| `CYCLE_DETECTED` | warning | 不阻断 | 依 solver policy/显式确认 |
| `MULTIPLE_OUTFALLS_PER_COMPONENT` | warning | 不阻断 | 需确认边界条件 |
| `PROFILE_STATION_OUT_OF_RANGE` | warning/error | 严格模式阻断 | 阻断 |

环路是报告项而非自动删除条件。错误导入 MUST 事务回滚，MUST NOT 提交半张网络。

### 5.3 INP round-trip

- 只有列入 `inp_profile` 白名单并通过 import → canonical revision → export → re-import fixture 的 profile 才可声明“可回跑”。
- 原始 `source_sha256` 与 canonical `content_sha256` 分离。
- 不可映射内容必须拒绝或返回显式 `lossy=true` manifest；MUST NOT 输出自称可运行但未经验证的文件。
- GeoJSON 永远从 canonical DTO 生成，坐标顺序固定 lon/lat。

## 6. 仿真数值质量与异常语义

### 6.1 质量门禁

- 所有 frame 状态必须有限且非负；负 depth/volume 超过数值容差即失败，MUST NOT 用 `max(0, x)` 静默修复。
- 质量台账：

```text
epsilon_m3 = initial_storage
           + rainfall_input
           + external_boundary_inflow
           - infiltration_loss
           - outfall_discharge
           - external_boundary_outflow
           - final_storage
```

- `py_*` 全程/逐 frame 相对残差 `<=1e-4` 才可 `verified`；`<=1e-3` 为 warning 且不得触发高等级自动告警；`>1e-3` 为 `MASS_BALANCE_FAILED`。
- 内部时间步满足 `Courant<=0.5`；每个 reporting interval 最多 20 个 substep，超出为 `NUMERICAL_STABILITY_LIMIT`。
- `py_dw` 遇到超临界、快速流向反转或压力流转段 MUST 失败为 `UNSUPPORTED_REGIME`，MUST NOT 继续给“稳定”结果。

### 6.2 任务错误

| code | 终态 | 自动重试 | 用户语义 |
|---|---|---:|---|
| `UNSUPPORTED_FEATURE` | submit 422 | 否 | 列出不支持对象和建议 solver |
| `SOLVER_UNAVAILABLE` | submit 503 | 否 | 显式 solver 不可用；auto 已在提交时选择 fallback |
| `SOLVER_VERSION_MISMATCH` | failed | 否 | engine binary/version/hash 不匹配 |
| `NUMERICAL_UNSTABLE` | retry_wait → failed | 最多两次 | 每次 refinement 产生新 config hash |
| `NUMERICAL_STABILITY_LIMIT` | retry_wait → failed | 最多两次 | CFL/substep 门禁未满足 |
| `MASS_BALANCE_FAILED` | failed | 否 | 不发布 frame/forecast |
| `SIMULATION_TIMEOUT` | timed_out | 否 | durable attempt 超过其 deadline；general 为 600 s，nowcast 为固定 180 s |
| `SIMULATION_CANCELLED` | cancelled | 否 | 幂等取消 |
| `OUTPUT_LIMIT_EXCEEDED` | failed | 否 | 不提交部分结果 |
| `FRAME_SCHEMA_INVALID` | failed | 否 | 帧 schema/count/hash 不一致 |
| `FRAME_ATTEMPT_CONFLICT` | failed | 否 | 同一 attempt/frame/index 出现不同 payload hash |
| `PROPERTIES_SCHEMA_INVALID` | submit 422 / failed | 否 | properties/parameter schema 未知字段或跨字段约束失败 |
| `LEASE_EXPIRED` | retry_wait | 最多两次 | worker crash/lease 过期 |
| `RESULT_PERSIST_FAILED` | retry_wait → failed | 最多两次 | 无孤立 summary |
| `SANDBOX_CLEANUP_PENDING` | 任务原终态保留 | 不重跑 | cleanup sweeper 后续重试 |

## 7. Forecast、事件与通知异常语义

| 场景 | API/状态 | 事件/通知 | 恢复 |
|---|---|---|---|
| 无成功 nowcast | 200 `data_state=not_ready` | 无新告警 | 下个时隙 |
| nowcast 超过 180 s | run=`timed_out` | late result 不发布 | 记录 SLO breach |
| calibration/rule 调度前缺失或失效 | 不创建 nowcast run；forecast=`not_ready` | 无 point/alert/outbox | `FORECAST_POLICY_UNAVAILABLE`，修复 policy 后下个时隙 |
| calibration/rule 运行中失效 | run=`failed` | 不发布 point/alert/outbox | `FORECAST_POLICY_INVALID`；late result 不得补发 |
| DB 不可用 | 503 | 无部分提交 | 连接恢复后按唯一键重放 |
| Redis/outbox relay 不可用 | REST 事实仍可读 | SSE 延迟 | relay 补偿 |
| SSE queue 满/sequence gap | 关闭连接或 `resync_required` | 不静默丢 | REST snapshot + Last-Event-ID 重连 |
| Last-Event-ID 超过 7 天保留 | 409 `SSE_CURSOR_EXPIRED` | 不伪造历史 | REST resync |
| SMTP/webhook timeout/5xx | alert 不变 | delivery `retry_wait` | `t+0,+1m,+5m,+15m,+1h`，最多 5 次 |
| webhook 4xx/DLQ | alert 不变 | `dead_letter` | 人工修复后重放 |
| forecast version 冲突 | 409 + current state | 不覆盖 | 客户端刷新后带新 version |
| 实测缺失/低质量 | 保持 predicted | 不发 observed | 合格观测到达后转换 |

Webhook MUST 使用管理员配置的 HTTPS allowlist、阻止私网/loopback/link-local/任意 redirect/DNS rebinding，发送 `X-Forecast-Event-Id`、`Idempotency-Key` 和 HMAC；HTML 必须转义。现有 webhook 仅原样 POST，必须由新 delivery worker 隔离：`backend/app/services/notification_service.py:59-107`。

## 8. 性能与容量标准

### 8.1 求解性能

**硬门禁只适用于 `py_mw`。**

- fixture：5000 nodes、10000 pipes、1000 catchments、至少 10% 管段形成环、24 h/5 min、289 frames、峰值径流使目标管段非零。
- 环境：Python 3.11、4 个物理核、无 GPU、BLAS/OMP 总线程 4、锁依赖和 solver 版本。
- 计时：从 worker claim 成功到全部 frame + summary + quality report 持久化；不含 queue wait，但单独报告。
- 预热 1 次，正式 3 次；每次 `worker_wall_time<=180 s`，并报告 P95、CPU time、峰值 RSS、分项时间、frame bytes、质量残差。
- 性能通过但质量失败仍判失败。
- `py_dw`/`py_ponding` 在独立 benchmark 前 `validated_scale=null`，MUST NOT 宣传 5000/10000。

依据：当前无 solver/benchmark；CPU requirements 只有基础数值库 `numpy`：`backend/requirements-cpu.txt:1-34`。

### 8.2 API 性能

`P95<=500 ms` 只衡量以下同步读/轻量控制请求，在固定 5000/10000/1000 fixture 上执行 60 s warmup + 600 s、20 VU、错误率 `<1%`。固定权重：map-data 40%、single-frame 30%、latest 10%、risk-ranking 10%、status/list 5%、submit enqueue 3%、cancel ack 2%；每个纳入类别单独计算 P95。

- list/detail/status；
- map-data；
- single-frame；
- forecast latest/horizon/risk；
- simulation submit enqueue/cancel acknowledgement。

不纳入 500 ms：20 MiB import/validate、INP/GeoJSON export、SSE 长连接、率定搜索、长时间仿真。import/validate 使用 202 job，export 使用流式 200，分别验收 enqueue P95、TTFB、总时长和资源上限。

### 8.3 Nowcast SLO

- 默认 interval 10 min、horizon 120 min、step 5 min、deadline 180 s。
- fake-clock 跑 100 个时隙验证状态/重试/stale；staging/nightly 至少 30 个真实连续周期。
- 每个周期从 `scheduled_for` 到 alert + event outbox commit `<=180 s`，30 周期 P95 与 max 都必须达标；缺周期、policy 失效、晚到结果或 late publish 均失败。

### 8.4 前端性能

- production build、4 vCPU/8 GiB、无 GPU、1920×1080、固定本地 tile fixture。
- 5000 nodes、10000 pipes、1000 catchments、289 frames、A/B 可选。
- 连续 60 s pan/zoom + 4x 播放 + layer/selection 切换。
- 门槛：presented FPS `>=25`；RAF frame-time P95 `<=50 ms`；初始加载后无 `>200 ms` long task；FrameCache `<=256 MiB`；map/frame API P95 `<=500 ms`。
- 输出 `twin-map-<commit>.json`，包含环境、commit、浏览器、采样时长、FPS、frame-time、long task、cache bytes、API latency、culling/LOD/Canvas 开关。

依据：当前地图逐设备创建 Marker（`frontend/src/components/map/MapVisualization.tsx:26-40,285-294`），没有 viewport culling/Canvas/benchmark，不能据现状宣称达标。

### 8.5 容量预算

- 24 h/5 min：289 frames。
- 5000/10000 目标：4,335,000 object-frame states。
- 输入：20 MiB；workspace：2 GiB；frame：64 MiB；simulation output：8 GiB；均为默认上限，不是成功结果大小保证。
- FrameCache 256 MiB 为静态几何和所有动态帧共享预算。
- 大结果以分页/单帧/流式导出访问，MUST NOT 一次返回 90 天全部帧。

## 9. 率定与 A/B 标准

### 9.1 率定数据准入

- 至少 3 个独立降雨事件；整事件按时间划分 calibration/validation/test，推荐 60/20/20 或 70/15/15。
- validation/test 至少 12 对有效观测；不足返回 `INSUFFICIENT_OBSERVATIONS`。
- 只有 `external_verified` observation 可使 AC-5 通过；`external_unverified/synthetic/mock` MUST NOT。
- baseline/calibrated 使用相同 network revision、rainfall、boundary、initial state、solver/version、evaluation code；只允许 parameter set 不同。
- 指标：

```text
RMSE = sqrt(mean((obs - sim)^2))
NSE  = 1 - sum((obs - sim)^2) / sum((obs - mean(obs))^2)
```

NSE 分母为 0 时返回 `NSE_UNDEFINED`，MUST NOT 伪造 0/1。

### 9.2 AC-5 通过门槛

预先指定 validation 或 test 集合，并同时满足：

- `NSE_calibrated - NSE_baseline >= 0.05`；
- `RMSE_calibrated <= 0.95 * RMSE_baseline`；
- peak absolute error 恶化不超过 10%；
- 无守恒/稳定性失败；
- 报告保存 dataset/network/rainfall/solver/parameter/code hash 和事件切分。

该门槛是产品演示验收，不等同于工程认证。

### 9.3 A/B

- baseline/candidate 必须同 `network_revision_id + target_manifest_id/hash`、同 start/end/frame interval/frame count。
- rainfall、solver、parameter set 可不同；不同 topology/time grid 返回 409 `COMPARISON_INCOMPATIBLE`。
- 返回 baseline、candidate、absolute delta、relative delta、unit、better direction；baseline 为 0 时 relative=null。
- 指标由后端预计算并可逐项重算；浏览器不重采样、不猜测优劣方向。

## 10. API 与错误体验补充

### 10.1 角色矩阵

| 能力 | viewer | operator | admin |
|---|---:|---:|---:|
| 查看 network/map/frame/simulation/forecast/risk/A/B | 是 | 是 | 是 |
| 创建/复制 rainfall、提交/取消 simulation | 否 | 是 | 是 |
| 创建/更新 network、import/validate/export | 否 | 是 | 是 |
| acknowledge/resolve forecast | 否 | 是 | 是 |
| delete、seed、保护配置/通知目标 | 否 | 否 | 是 |

前端隐藏按钮只改善 UX；后端 RBAC 是最终边界。现有 token role 和依赖工厂可复用，但安全加固必须先移除 fallback admin、修复缺 token 500/坏 role 500，并验证 `type/issuer/audience`：`backend/app/api/routes.py:93-126`、`backend/app/core/security.py:74-135`。

### 10.2 错误 envelope

所有新错误保留现有 422 的 `detail + errors`，并加 `code/request_id`；已知业务失败 MUST NOT 返回 500：

```json
{
  "detail": "simulation input contains unsupported features",
  "code": "UNSUPPORTED_FEATURE",
  "request_id": "uuid",
  "errors": [
    {
      "field": "pumps",
      "message": "solver does not support pumps",
      "retryable": false,
      "details": {"object_ids": ["P-001"], "suggested_solver": "swmm_kw"}
    }
  ]
}
```

本表是唯一同步 HTTP 映射；异步 job/run 的终态码不伪造 HTTP 状态，只出现在 202 初始响应或 GET status 的 `error_code`，并由对应 terminal status 表达。

| HTTP | 稳定 code | 语义 |
|---|---|---|
| 400 | `MALFORMED_REQUEST` | JSON/multipart 结构不可解析 |
| 401 | `AUTH_REQUIRED` / `UNAUTHENTICATED` | 缺/坏 token |
| 403 | `FORBIDDEN` | 角色不足 |
| 404 | `NETWORK_NOT_FOUND` / `SIMULATION_NOT_FOUND` / `FORECAST_ALERT_NOT_FOUND` | 资源不存在 |
| 409 | `IDEMPOTENCY_KEY_REUSED` / `NETWORK_REVISION_CONFLICT` / `COMPARISON_INCOMPATIBLE` / `FORECAST_VERSION_CONFLICT` / `INVALID_FORECAST_TRANSITION` / `SSE_CURSOR_EXPIRED` / `SIMULATION_NOT_CANCELLABLE` / `SOURCE_EVENT_CONFLICT` | 幂等、状态、CAS、cursor 或同步 ingest identity 冲突 |
| 410 | `FRAME_RETENTION_EXPIRED` | frame 过期，summary 仍可读 |
| 413 | `INPUT_TOO_LARGE` | 输入预算超限 |
| 415 | `UNSUPPORTED_IMPORT_FORMAT` | 格式/内容不在白名单 |
| 422 | `UNSAFE_PATH` / `INVALID_INP` / `INVALID_GEOJSON` / `VALIDATION_ERROR` / `CRS_UNKNOWN` / `PROPERTIES_SCHEMA_INVALID` / `UNSUPPORTED_FEATURE` / `EXPORT_NOT_REPRESENTABLE` / `ROUNDTRIP_VALIDATION_FAILED` / `INSUFFICIENT_OBSERVATIONS` | 可定位 preflight 验证错误 |
| 429 | `RATE_LIMITED` | 返回 `Retry-After` |
| 503 | `SOLVER_UNAVAILABLE` / `SPATIAL_BACKEND_UNAVAILABLE` / `FORECAST_UNAVAILABLE` / `FORECAST_POLICY_UNAVAILABLE` | 依赖/component/policy 服务级不可用；policy child reason 见下 |
| 504 | `SIMULATION_TIMEOUT` | 仅同步等待接口；异步 submit 不返回 504，异步 deadline 只产生 status=`timed_out` + error_code=`SIMULATION_TIMEOUT` |

异步 `error_code` 矩阵：

| surface/status | codes |
|---|---|
| simulation retry_wait | `LEASE_EXPIRED` / `NUMERICAL_UNSTABLE` / `NUMERICAL_STABILITY_LIMIT` / `RESULT_PERSIST_FAILED` |
| simulation failed | `FRAME_ATTEMPT_CONFLICT` / `FRAME_SCHEMA_INVALID` / `MASS_BALANCE_FAILED` / `OUTPUT_LIMIT_EXCEEDED` / `PROPERTIES_SCHEMA_INVALID` / `SOLVER_VERSION_MISMATCH` / `UNSUPPORTED_REGIME`；`LEASE_EXPIRED` / `NUMERICAL_UNSTABLE` / `NUMERICAL_STABILITY_LIMIT` / `RESULT_PERSIST_FAILED` 仅在 attempt exhausted 后 |
| network-job retry_wait | `LEASE_EXPIRED` / `TRANSIENT_IMPORT_ERROR` / `RESULT_PERSIST_FAILED` |
| network-job failed | `TRANSIENT_IMPORT_ERROR` / `RESULT_PERSIST_FAILED` / `LEASE_EXPIRED`，三者仅在 attempt exhausted 后 |
| nowcast retry_wait | `LEASE_EXPIRED` / `RESULT_PERSIST_FAILED` |
| nowcast failed | `FORECAST_POLICY_INVALID`；`LEASE_EXPIRED` / `RESULT_PERSIST_FAILED` 仅在 attempt exhausted 后 |
| calibration retry_wait | `LEASE_EXPIRED` / `RESULT_PERSIST_FAILED` |
| calibration failed | `INSUFFICIENT_OBSERVATIONS`；`LEASE_EXPIRED` / `RESULT_PERSIST_FAILED` 仅在 attempt exhausted 后 |
| simulation/calibration/network-job timed_out | `SIMULATION_TIMEOUT`；必须同时为 status=`timed_out`，MUST NOT 出现在 failed 行 |
| nowcast timed_out | `SIMULATION_TIMEOUT` + status=`timed_out` |
| cancelled | `SIMULATION_CANCELLED` + status=`cancelled` |
| report-only | `NSE_UNDEFINED`（指标未定义，不是 HTTP/job failure） |
| validation issues | `MISSING_COORDINATES` / `DANGLING_NODE` / `DUPLICATE_SEGMENT` / `MISSING_INVERT` / `REVERSE_SLOPE` / `SELF_LOOP` / `DISCONNECTED_COMPONENT` / `CYCLE_DETECTED` / `MULTIPLE_OUTFALLS_PER_COMPONENT` / `PROFILE_STATION_OUT_OF_RANGE`；通过 validation report 返回，不伪造 job error_code |

retry matrix 的 code→status→attempt 规则：进入 retry_wait 时 `attempt` 保持当前已执行次数并写对应 `last_retry_error_code`；simulation/network-job/calibration 固定 backoff `[10s,60s]`，nowcast 固定 `[5s,15s]`，数组长度分别为 2。只有 `attempt=max_attempts=3` 时，共享状态机才可从 retry_wait 进入 failed，并把 `last_retry_error_code` 原样复制到 `error_code`；G3 lifecycle fixture 必须逐行覆盖四类 surface、due claim、backoff 和 exhausted→failed。

`FORECAST_UNAVAILABLE` 是 503 父码；`FORECAST_POLICY_UNAVAILABLE` 是其 policy child reason：全局 policy component 损坏时 HTTP 503 且 `details.reason=FORECAST_POLICY_UNAVAILABLE`，单一 network 无有效 entry 时 HTTP 200 `data_state=not_ready` + child reason。两者不是别名。

## 11. AC-1～AC-8 验收标准

### 11.1 证据通用格式

每份机器证据必须包含：

```text
run_id, utc_timestamp, commit, dirty_state
os, cpu_model, physical_cores, ram
python_version, node_version, dependency_lock_sha256
image_digests, database_extensions, alembic_revision
spatial_backend, solver_id, solver_version, engine_sha256
fixture_path, fixture_sha256, input_counts
command, exit_code, stdout_summary, stderr_summary
result_summary, artifact_sha256
```

`PASS` 只允许在目标命令本轮 exit 0 且 artifact 存在、hash 校验通过时填写。静态阅读、`alembic heads`、Compose config、build 或 mock 测试不能替代对应 AC。

### 11.2 验收矩阵

| AC | 自动化证据 | 通过判据 | 当前状态 |
|---|---|---|---|
| AC-1 合法 INP/GeoJSON | 两 spatial profile 的 import contract、validation golden JSON、round-trip | 计数、stable key、WKT/GeoJSON、issue code、topology 完全一致；error 不半提交 | **未实现/本轮未运行** |
| AC-2 恶意文件稳定拒绝 | 20 MiB+1、截断、zip、脚本、路径矩阵、深层/NaN GeoJSON、CPU/memory 样本 | 预期 400/413/415/422；无越界/半成品；目录清理；前后 readiness 正常 | **未实现/本轮未运行** |
| AC-3 50 年一遇 24 h 回放 | 4 核 solver benchmark、worker E2E、289-frame manifest、Playwright timeline | 每次 worker `<=180s`；289 帧；diff `<=300s`；进度 0/100；取消/重试/回放可复现 | **未实现/本轮未运行** |
| AC-4 nowcast/告警/SSE/抑制 | G4 + G5 nowcast harness + acceptance 映射：fake clock 100 时隙 + 30 真实周期 + 两 API worker durable event + calibration/rule artifact hash | `issued_at/forecast_origin_at/published_at` 可审计；完整状态机/抑制；工件 fail-closed；freshness 独立；每周期 `<=180s`；30m 一个 created business event | **未实现/本轮未运行；gate/harness 当前不存在** |
| AC-5 率定改善 | G3 calibration durable lifecycle fixture（dataset/split、retry/failed/timed_out、metrics+report artifact）以及真实 external observation 的事件切分、baseline/calibrated report | 四类 job 中 calibration 的 claim/finalize/terminal 有机器证据；达到 9.2 四项门槛；无 mock；hash 可追溯 | **受真实观测/绑定缺口阻断；G3/AC-5 本轮未运行** |
| AC-6 A/B 差异 | 同 revision/time grid comparison API + UI + 逐项重算 | 绝对/相对差异、单位和方向正确；不兼容 409 | **未实现/本轮未运行** |
| AC-7 质量与零回归 | Python 3.11 pytest/Black/isort/Flake8/mypy；Node 18.20.8/npm 10.8.2 的 `npm ci`/Vitest/lint/format/build；startup；旧 API/SSE characterization | 全部 exit 0；mypy 不 continue-on-error；后端可启动；旧测试零回归 | **当前基线报告不绿；本轮未重跑** |
| AC-8 全新 Compose/演示 | `down -v`、digest-pinned `up --build`、两 profile、migration、worker、API/SSE、README、backup/restore | 空卷可复现；无 latest/固定 secret；演示全链路；恢复 hash/API 校验通过 | **Docker live 未运行；本轮未验证** |

### 11.3 Release gates

阶段 D 的 R0（v3 独立设计复审）必须先于任何 migration 落地，也必须先于实现后的 G0～G6/acceptance；阶段 V 的 G0～G6/acceptance 从仓库根调用同一个冻结 entrypoint。禁止在不同文档复制会漂移的 `cd` 链、内联 pytest 路径或不存在的 npm script。`scripts/verify_hydraulic_stack.py` 是 G0～G6/acceptance 的实现期必交文件，CLI、fixture registry 与 evidence 目录由本文及 `IMPLEMENTATION_CONTRACT.md` §17.1 冻结；脚本/fixture 未实现时固定为 **NOT RUN**，脚本存在但任一子命令非零时为 **FAIL**。设计复审只审定义，不把 NOT RUN 记为 PASS。G0～G6 和 acceptance 必须使用同一 RUN_ID、同一 commit；acceptance 只汇总同轮 artifacts，不接受跨轮补证据。

```bash
export RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short=8 HEAD)"
python scripts/verify_hydraulic_stack.py --gate g0 --evidence-dir "artifacts/hydraulic/${RUN_ID}/g0"
python scripts/verify_hydraulic_stack.py --gate g1 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g1/postgis"
python scripts/verify_hydraulic_stack.py --gate g1 --profile portable --evidence-dir "artifacts/hydraulic/${RUN_ID}/g1/portable"
python scripts/verify_hydraulic_stack.py --gate g2 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g2"
python scripts/verify_hydraulic_stack.py --gate g3 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g3"
python scripts/verify_hydraulic_stack.py --gate g4 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g4"
python scripts/verify_hydraulic_stack.py --gate g5 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g5"
python scripts/verify_hydraulic_stack.py --gate g6 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g6/postgis"
python scripts/verify_hydraulic_stack.py --gate g6 --profile portable --evidence-dir "artifacts/hydraulic/${RUN_ID}/g6/portable"
python scripts/verify_hydraulic_stack.py --gate acceptance --ac ac1,ac2,ac3,ac4,ac5,ac6,ac7,ac8 --evidence-dir "artifacts/hydraulic/${RUN_ID}/acceptance"
```

1. **G0 基线门禁**：先校验 R0 PASS、owner-map 和 dependency handoff（每 path 恰一个当前 owner；dependency-config 例外路径已 handoff 且 integration 保留），再在 Python 3.11/Node 18.20.8/npm 10.8.2 下执行全量 pytest、Black、isort、Flake8、mypy（不得 continue-on-error）、`npm ci`、Vitest、lint、format、build、backend lifespan characterization；不能用 build 代替 tests。
2. **G1 schema 门禁**：必须携带 R0 PASS artifact、base_commit 和 document_bundle_sha256；分别对 `postgis` 与 `portable` 空库执行 upgrade→current→重复 upgrade→clone downgrade/upgrade，并输出 extension/index/hypertable/retention/table manifest；四张 durable job 表的 retry_wait/next_attempt 等价 CHECK 必须进入 manifest 并做正反例约束测试。两个 profile 都包含 PostgreSQL + TimescaleDB，portable 仅表示无 PostGIS。R0 非 PASS 时不得生成 migration，G1 必须 NOT RUN。
3. **G2 import/security 门禁**：合法 golden、恶意矩阵、durable network job、RBAC、路径、SSRF、JWT 负向测试，以及 network-job/simulation/calibration 的 idempotency 未到期重放/冲突、到期新 generation/resource、并发串行化与 FK retention fixture 全绿；固定 admin fallback 禁用，forecast router 在安全门禁通过前不得 production enable。
4. **G3 worker/数值门禁**：四类 durable job（simulation、network job、nowcast、calibration）的 `next_attempt_at` due claim/backoff、cancel/600 s/nowcast 180 s、两次 retry、attempt exhausted、lease/fencing 和统一终态都必须逐行匹配 10.2 retry matrix；simulation 另验 attempt frame registration/publish、并发 2 且 nowcast slot 禁借、质量/稳定性和 90 天 retention，calibration 另验 dataset/split 固定、metrics+独立 report artifact 原子 finalize、retry/failed/timed_out fixture。
5. **G4 realtime 门禁**：完整 forecast transition/state suppression、policy config canonical hash+sidecar、artifact ID/hash 复合绑定、outbox 单发布、跨 worker/restart replay、gap/resync、30m suppression、delivery retry/DLQ；nowcast `issued_at`、producer `published_at`、relay `event_outbox.published_at` 与独立 freshness 必须有边界测试。
6. **G5 性能门禁**：固定执行 `scripts/benchmark_hydraulic.py`、`benchmark_hydraulic_api.py`、`benchmark_hydraulic_nowcast.py` 和 `npm --prefix frontend run test:perf:twin`；参数/fixture/输出路径唯一列于 `IMPLEMENTATION_CONTRACT.md:17.1.1`。四项必须同一 RUN_ID/commit 且全部达标。
7. **G6 Compose/回滚门禁**：`postgis` 与 `portable` 必须分别执行独立命令和 evidence 目录；两个 profile 都从全新卷完成 migration/worker/API/SSE、README 演示、备份→销毁→恢复与旧 API/SSE/页面零回归。G6 不得由单一 profile 命令隐式代替另一 profile。

## 12. 已知阻断与非目标

### 12.1 当前阻断

- 无水力 schema、solver、worker、rainfall、forecast 或 `/simulation` 实现；当前 ORM 证据：`backend/app/models/db_models.py:46-295`。
- 无 SWMM engine 工件/锁版本/许可 checksum；当前依赖证据：`backend/requirements-cpu.txt:1-34`。
- 无真实传感器 ingest、显式设备-水力对象绑定或 external observation dataset；当前只有 mock→Influx：`backend/app/services/mock_data_generator.py:147-200`。
- 当前安全边界存在固定 admin fallback、匿名 SSE/WS、无 size/path limit 的旧上传和直接出站 URL：`backend/app/api/routes.py:93-126,204-246,273-307,393-413,1039-1076`。
- 当前 Compose 没有 migrate/simulation-worker/nowcast/outbox/notification 服务：`docker-compose.yml:7-177`。
- 当前目标环境测试/启动/Compose 不是全绿；详见 recon 报告，**本轮未重跑，不能写成已通过**。
- v3 最新 round-3 独立复审为 `NOT_APPROVED / R0_FAIL`，正式 JSON artifact 尚不存在；B-01～B-07、N-01～N-07 与上一轮 §4 PASS 项已确认，C-01～C-04 的本次文档整改不产生 PASS artifact，状态为 `R0_ROUND_3_FAILED / ALL_IMPLEMENTATION_WORK_BLOCKED`。在新的独立复审 PASS 前禁止任何 migration 变更，G1 必须 NOT RUN。
- `scripts/verify_hydraulic_stack.py`、三个 benchmark harness、frontend perf script、G0～G6/AC fixture/evidence 均尚不存在；所有 gate 当前只能做路径审计，不能执行通过。
- JWT 严格校验、fallback admin 移除、forecast RBAC/限流尚无实现与负向测试；按 D-17，forecast API 不得先行上线。

### 12.2 非目标

- 不训练新的预报 ML 模型；只消费版本化概率校准工件。
- 不把简化积水单元称为二维水动力模型。
- 不承诺任意 INP 原样 round-trip。
- 不承诺无 PostGIS 路径在任意规模和硬件下都 `<500 ms`；只对固定 fixture 的验收规模声明。
- 不删除 legacy `alerts`、SSE 或旧页面；新 forecast 通过独立表/端点和双源适配隔离。
- 不新增 Celery/RQ/Arq、独立时序产品或对象存储作为本期事实源。
- 不在前端 localStorage/IndexedDB 保存 90 天帧。

## 13. 需求追踪

| 源 PRD | v3 候选补充 | 实施落位 |
|---|---|---|
| FR-1 | D-03～D-05、5.1～5.3 | data model、geometry/import/topology |
| FR-2 | 4 | rainfall scenario/source |
| FR-3 | D-06～D-09、6、8 | solver/task/frame/calibration |
| FR-4 | D-10～D-12、7、11 | forecast alert/outbox/notification |
| FR-5 | 10、11 | REST/OpenAPI/RBAC/errors |
| FR-6 | 8.2、8.4、8.5 | map/timeline/frame/A-B/i18n |
| FR-7 | D-03～D-04、D-08、11.3 | profiles/migration/Compose/retention |
| NFR | 8 | benchmark/load/performance artifacts |
| AC-1～AC-8 | 11 | G0～G6 与逐项 evidence |

## 14. 本轮实际执行与未执行项

本轮读取已落盘且双副本 SHA-256 一致的最新 round-3 复审，只修改三份获准文档。实际执行的只读核验：

```text
$ sha256sum "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/docs/hydraulic-twin/SOURCE_PRD.md"
11e56e68a68017b84b5ae577dede8a74f6217d4a7877f5e358a18f14e66807ee *C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/docs/hydraulic-twin/SOURCE_PRD.md

$ git -C "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage" status --porcelain --untracked-files=all | wc -l
47

$ git -C "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage" status --short -- backend/alembic/versions
(no output)
```

内联只读断言输出：

```text
final_semantic_check=PASS
C01_resource_fk=3/3 unique=3/3 legacy_key_columns=0
C02_next=4/4 metadata=4/4
C03_fields=101 authorized=54 unauthorized=47 env=101
C04_stale_bundle_values=0
previous_anchors=PASS transition_tables=prd:0,ic:1
owner_rows=119 candidate_paths=48 unmatched=0 ambiguous=0
blockedBy_nodes=26 unknown=0 cycles=0 DOC-OWNERSHIP=empty-set
candidate_status=NOT_APPROVED migrations=empty
```

本轮把 C-01～C-04 精确转为文档约束：simulation/calibration 单列 generation-specific idempotency FK/UNIQUE、四表 next-attempt 与 retry metadata CHECK、54 项 `settings.fields[]` + 47 项 `settings.unauthorized_fields[]`、候选文档不再自述历史 bundle 值。逐项记录见 `reviews/v3-final-remediation.md`。最新 round-3 verdict 仍为 `NOT_APPROVED / R0_FAIL`；整改后只能由下一轮 reviewer 从 live files 重算并在最终批准 artifact 中记录，状态保持 `R0_ROUND_3_FAILED / NOT_APPROVED / ALL_IMPLEMENTATION_WORK_BLOCKED`。

本次未修改 `SOURCE_PRD.md`、生产代码、依赖实现或 migration；其他 worktree 改动属于并行工作流，新的独立复审 PASS 前不得落地。

本次**未运行**：新的独立复审、官方 owner/handoff/migration checker、G0～G6、AC-1～AC-8、pytest、Vitest、npm ci、Alembic live、Docker、solver/worker/calibration、性能与 backup/restore；均为 NOT RUN。静态断言不能记为 R0 或实现门禁 PASS。
