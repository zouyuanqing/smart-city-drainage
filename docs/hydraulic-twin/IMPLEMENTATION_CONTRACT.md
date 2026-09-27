# 排水数字孪生实施契约（Implementation Contract）

> 状态：**Frozen v3 — APPROVED**。通过轮次：最终独立复审（整改后第 1 轮，reviewer=`dynamic-workflow v3-final-review-1`，2026-09-24）；复审报告：`reviews/v3-schema-semantics-review.md`（`PASS / R0_DESIGN_PASS`）。本批准仅覆盖阶段 D 设计冻结；实现与门禁仍按既有 blockedBy/phase 语义执行。
>
> 日期：2026-09-24。
>
> 基线：`ca99e04 chore: remove stray isort diff log`。
>
> 上游：`SOURCE_PRD.md`（不可变需求归档）、`PRD_SUPPLEMENT.md`（整改候选产品/验收基线）。
>
> 规范词：**MUST / MUST NOT / SHOULD / MAY**。
>
> supporting contracts：`contracts/*.md` 已标记 **SUPERSEDED / NON-NORMATIVE**，仅保留审查轨迹；与本文冲突的旧名称、状态、路径和门禁不得实现。
>
> 范围：候选数据模型、空间双路径、API、SSE、持久任务、求解器、前端、部署、可观测性、迁移、文件所有权和验收证据。本文不表示任何功能已经实现；本轮只修改文档，没有修改生产代码。

## 0. 证据与决策优先级

### 0.1 当前代码事实

- FastAPI/lifespan/Prometheus/请求 ID 已有：`backend/app/main.py:101-218,240-265`。
- async SQLAlchemy 模式已有：`backend/app/core/database.py:57-129`。
- 当前 ORM 只有六张旧表：`backend/app/models/db_models.py:46-295`。
- 当前 Alembic 只有 `0001_initial`：`backend/alembic/versions/0001_initial.py:14-17`；init SQL 另有 `sensor_readings`：`docker/init-db.sql:100-118`。
- 应用启动仍执行 `create_all`：`backend/app/main.py:132-141`。
- 当前传感器自动路径是 mock → InfluxDB → SSE/Redis：`backend/app/services/mock_data_generator.py:147-200`、`backend/app/services/influxdb_service.py:70-124`。
- 当前 Redis 只有 publish/subscribe 和简单 KV：`backend/app/core/redis_client.py:82-164`。
- 当前 SSE history/ID 是进程内状态：`backend/app/services/sse_manager.py:32-43,90-120,142-189`。
- 当前没有水力 solver 依赖：`backend/requirements-cpu.txt:1-34`。
- 当前前端没有 `/simulation`，地图为逐设备 DOM Marker：`frontend/src/App.tsx:35-100`、`frontend/src/components/map/MapVisualization.tsx:274-295`。
- 当前 Compose 没有 migrate、simulation worker、nowcast、outbox 或 notification worker：`docker-compose.yml:7-177`。

### 0.2 冲突解决顺序

1. `SOURCE_PRD.md` 定义必须交付的产品目标。
2. `PRD_SUPPLEMENT.md` 定义本轮补充的产品语义和验收口径。
3. 本文定义实现方式；`contracts/*.md` 是 **SUPERSEDED / NON-NORMATIVE**，不能以“领域契约”身份覆盖本文。文中为迁移方便提及旧名称时，必须按第 0.4 节 canonical mapping 实现。
4. 任何需要改变本文的非协商决策都必须先修改三份主文档并重新审查，MUST NOT 在代码中静默偏离。
5. 当前状态不是“已冻结”：只有新的独立设计复审通过后，独立复审者/文档 owner 才能更新三份主文档的批准状态；本轮保持候选状态，MUST NOT 自行写 Frozen。
6. **设计冻结与实现验收分离**：R0 阶段 D 只审 schema/semantics、owner、blockedBy、handoff 谓词和 G0～G6/AC 定义的内部一致性；阶段 V 在设计独立通过且实现/landing 完成后，才实际运行 G0～G6/AC。定义存在而脚本/fixture 缺失时只能记 NOT RUN，不能计 PASS；阶段 V 的结果也不能倒推替代阶段 D。实现顺序仍受第 16 节安全/数据依赖门禁约束。

### 0.3 v3 整改决议（替代 supporting contracts）

| 冲突 | v3 候选结果（未批准） |
|---|---|
| `wkt` vs `portable` 命名 | 配置和部署统一 `HYDRAULIC_SPATIAL_BACKEND=postgis|portable`；两者都用 PostgreSQL + TimescaleDB，“portable”仅指 WKT+bbox 且无 PostGIS |
| `/api/sse/hydraulic` vs `/api/forecast/events` | v1 只实现认证的 `GET /api/forecast/events`；不新增 query ticket 变体 |
| solver `auto` 是否允许运行中降级 | 提交时使用 `solver_selection=auto|explicit` 冻结实际 solver；同一 simulation 禁止运行中换 solver |
| `running/completed` vs `leased/succeeded` | canonical 状态使用 `leased/running/completed` |
| 总并发 2 与 nowcast 180 s | 两个数据库槽位中一个 lane=nowcast、一个 lane=general；普通持续容量为 1 |
| 20 MiB 是输入还是总输出 | 20 MiB 是不可信输入/输入物料预算；workspace/frame/job 另设上限 |
| PostGIS 路径是否永久保存 WKT | 是；公共 WKT+bbox 是逻辑规范，PostGIS geometry 是 profile-specific 派生/加速列 |
| `wkt` / `topology_revision` / `succeeded` 等旧名 | 一律不得实现；使用第 0.4 节 canonical mapping |
| 帧幂等键 | 从 `(simulation_id, frame_index)` 改为 attempt-scoped `(attempt_id, frame_index)`，通过 `published_attempt_id` 发布 |
| observation idempotency | 不再把 `UNIQUE(source, source_event_id)` 当来源保证；先有唯一 ingest event identity + payload conflict 检查 |
| import/validate/seed 执行 | 从 API/无租约后台任务改为 `network_jobs` + 独立 durable worker + lease/fencing |
| nowcast 时间 | `issued_at`、`forecast_origin_at`、`published_at` 分离；freshness 独立于 180 s deadline |
| probability/rule | 均为不可变、带 hash/有效期/适用域工件；nowcast run 显式绑定 |
| network DELETE | v1 幂等 archive，不物理删除 revision/历史 |
| G0～G6/AC | 只从仓库根调用冻结 entrypoint；G5 四类 harness/fixture 路径固定 |
| Timescale frame unique | hypertable 不声明跨 partition `UNIQUE(attempt_id,frame_index)`；普通 PG registration 承担唯一性并与 frame 同事务 |
| retry_wait 调度 | simulation/network-job/nowcast 都持久化 `next_attempt_at`，DB due claim |
| shared-file ownership | 全周期 owner 由 §15.1～15.6 唯一决定；dependency-config 本阶段授权仅由 §15.0 `DEP-AUTH-01..13` 及 prep/landing flags 决定 |
| 观测可信度 | `external_verified|external_unverified|synthetic|mock`，由版本化 source policy 决定 |
| reserved slot | v1 禁止 general 借用 nowcast slot |
| 缺失 policy 工件 | 调度前不创建 run；运行中失效 terminal failed；均不发布 |
| initial-state hash | 独立于 network revision hash；snapshot hash 另算；request fingerprint 另绑定 target-manifest hash |
| UNSAFE_PATH / job idempotency | 422；import/validate/seed 均强制 Idempotency-Key |

### 0.4 v3 canonical mapping（实现时只使用右列）

| 旧 supporting 名称/语义 | v3 唯一名称/语义 |
|---|---|
| `HYDRAULIC_SPATIAL_BACKEND=wkt` | `HYDRAULIC_SPATIAL_BACKEND=portable`；数据仍含 WKT+bbox |
| `topology_revision` / `networks.revision_no` | `network_revision_id` / `networks.current_revision_id` |
| `queued -> running -> succeeded` | `queued -> leased -> running -> completed` |
| `forecast_runs` / `hydraulic.events` | `nowcast_runs` / `event_outbox` |
| `/api/sse/hydraulic` / `/api/hydraulic/events` / query ticket | `GET /api/forecast/events` + Authorization Bearer |
| `solver_mode=auto|epa_swmm|python` | `solver_selection=auto|explicit` + `solver_id=swmm_kw|swmm_dw|py_mw|py_dw|py_ponding` |
| `result_version` 独立结果根 | `published_attempt_id/published_attempt_no`；旧文档中的 result_version 只能是该 attempt 的只读别名 |
| 物理删除无引用 network | v1 DELETE 只 archive；无 API purge |
| API `BackgroundTasks`/进程内 import/validate/seed | `network_jobs` + `network-job-worker` |
| `frame` 直接按 simulation 唯一 | attempt staging + 单 published pointer 原子发布 |
| `source_event_id` 单列唯一 | `observation_ingest_events` 四元组 identity + payload hash + sample rows |

## 1. 全局技术约束

### 1.1 类型、单位、时间

- 所有业务 ID：应用生成 UUID v4；客户端不得指定服务端 PK。
- 所有时间：`TIMESTAMPTZ`，UTC RFC 3339；API 输出必须带 `Z` 或明确 UTC offset。
- 所有 hash：规范 JSON/二进制的小写 SHA-256 hex。
- 长度/深度/高程：m；流量：m³/s；速度：m/s；面积：m²；降雨强度：mm/h；体积：m³。
- API 字段 MUST 带单位或由 schema 明确单位；MUST NOT 使用无单位 `level/flow/depth`。
- 所有浮点输入必须 finite；禁止 NaN/Infinity。
- CRS 对外固定 `EPSG:4326`，坐标顺序 lon/lat。

### 1.2 canonical JSON/hash

1. object key 按 UTF-8 字典序；
2. 数组按业务 stable key/frame index 排序；
3. 浮点按各字段定义精度序列化，禁止先转二进制再 hash；
4. 排除服务器绝对路径、用户原始路径、secret、access token；
5. `source_sha256` 哈希原始字节；`content_sha256` 哈希 canonical 语义内容；
6. request idempotency hash 哈希方法、规范化 path、query、body 和影响结果的 header，不哈希 `Idempotency-Key` 本身。
7. canonical JSON 序列化固定为 UTF-8、无 whitespace、object key 按 Unicode code point 升序；UUID/enum 使用 schema 指定的小写字符串，时间使用 UTC RFC3339 `Z`。
8. `source_event_id` 的唯一输入固定为 `{"schema":"observation-source-event-v1","source_system":...,"source_stream":...,"source_partition":...,"source_offset":...}` 的 canonical JSON bytes。
9. `suppression_key` 的唯一输入固定为 `{"schema":"forecast-suppression-v1","network_id":...,"alert_type":...,"target_type":...,"target_id":...,"rule_id":...}` 的 canonical JSON bytes。禁止管道拼接、字段顺序变体或语言默认 JSON。两者均取 `SHA-256(bytes)` 小写 hex。

### 1.3 数据库命名

- legacy 表保留 `public.*`。
- 新水力对象默认 schema 为 `hydraulic`。
- API 不暴露 schema 前缀。
- 外键引用 legacy：`hydraulic.*.created_by -> public.users.id`、`device_network_bindings.device_id -> public.devices.id`。
- PostgreSQL enum 使用显式命名，禁止修改 legacy `public.alert_type/alert_level/device_status`。

## 2. 逻辑架构与数据流

```text
Browser
  ├─ REST (JWT) ───────────────> FastAPI /api/*
  │                                  │
  │                                  ├─ import/validate/simulation transaction
  │                                  │      -> PostgreSQL hydraulic.*
  │                                  │      -> hydraulic.event_outbox
  │                                  │
  │                                  └─ GET /api/forecast/events (fetch SSE)
  │                                             ^
  │                                             │ Redis wake-up/fan-out
  │                                      OutboxRelay
  │
  └─ /simulation Canvas + timeline

InfluxDB sensor_readings
  -> ObservationAdapter
  -> hydraulic.observation_samples (append-only projection)
  -> calibration / assimilation

simulation-worker
  -> claim leased job + fencing token
  -> TaskSandbox
  -> SolverAdapter
  -> FrameSink
  -> hydraulic.simulation_frames + summary/quality
  -> completed

nowcast-scheduler (singleton)
  -> reserved worker slot
  -> simulation
  -> forecast alert materialization
  -> alert + transition + event outbox atomic commit

notification-worker
  -> notification_deliveries
  -> SMTP / allowlisted HTTPS webhook
```

### 2.1 事实源

- PostgreSQL/Timescale：任务、revision、frame、forecast、alert、event audit、规范化 observation projection。
- InfluxDB：现有传感器原始历史。
- Redis：worker wake-up、outbox relay 通知和 SSE fan-out；丢失只造成延迟，不能丢业务事实。
- 进程内状态：仅 cache/连接，不表示业务完成。

## 3. 冻结数据模型

### 3.1 Schema metadata

#### `hydraulic.schema_metadata`

| 列 | 约束/语义 |
|---|---|
| `key` | PK；固定 `spatial_backend` |
| `value` | `postgis|portable` |
| `alembic_revision` | 镜像声明 head |
| `created_at/updated_at` | UTC |

启动时与 Settings 比较；不一致返回 503，readiness 不通过。profile 不允许原地切换。

### 3.2 Network 与不可变 revision

#### `hydraulic.networks`

```text
id UUID PK
code VARCHAR UNIQUE NOT NULL
name VARCHAR NOT NULL
status draft|active|archived
current_revision_id UUID NULL FK -> network_revisions.id
created_by UUID FK -> public.users.id
created_at/updated_at TIMESTAMPTZ
```

- `current_revision_id` 在 revision 建好后设置。
- v1 `DELETE /api/networks/{id}` 只执行逻辑 archive：设置 `status=archived`，阻止新 simulation/nowcast 与 revision mutation，保留 `current_revision_id`、全部 revision 和历史引用；重复 DELETE 返回 204。v1 API 不提供物理 purge，任何物理清理必须另行离线审批。

#### `hydraulic.network_revisions`

```text
id UUID PK
network_id UUID FK -> networks.id ON DELETE RESTRICT
revision_no INTEGER
parent_revision_id UUID NULL FK -> network_revisions.id
source_kind import|seed|crud
source_sha256 CHAR(64) NULL
content_sha256 CHAR(64) NOT NULL
parser_version VARCHAR NOT NULL
manifest JSONB NOT NULL
created_by UUID FK -> public.users.id
created_at TIMESTAMPTZ
UNIQUE(network_id, revision_no)
UNIQUE(network_id, content_sha256)
UNIQUE(id, content_sha256)
```

- revision 创建后不可 UPDATE/DELETE。
- manifest 至少含 node/pipe/catchment/profile counts、topology summary、parser/profile version、source hash。
- `parent_revision_id` 根 revision 可空；禁止自引用。

### 3.3 版本化空间实体

以下实体全部属于一个 `network_revision_id`；API 读取 active/current revision，历史 simulation 读取自己的 revision。

#### `hydraulic.network_nodes`

```text
id UUID PK
network_id UUID NOT NULL FK -> networks.id RESTRICT
network_revision_id UUID NOT NULL FK -> network_revisions.id RESTRICT
stable_key VARCHAR(128) NOT NULL
kind junction|outfall|manhole|storage|other
geometry_wkt TEXT NOT NULL
min_lon/min_lat/max_lon/max_lat DOUBLE PRECISION NOT NULL
invert_elevation_m DOUBLE PRECISION NULL
rim_elevation_m DOUBLE PRECISION NULL
max_depth_m DOUBLE PRECISION NULL
properties JSONB NOT NULL DEFAULT '{}'
UNIQUE(network_revision_id, stable_key)
UNIQUE(network_revision_id, id)
CHECK bbox finite and min<=max
```

#### `hydraulic.pipe_segments`

```text
id UUID PK
network_id UUID NOT NULL FK -> networks.id RESTRICT
network_revision_id UUID NOT NULL FK -> network_revisions.id RESTRICT
stable_key VARCHAR(128) NOT NULL
upstream_node_id UUID NOT NULL
downstream_node_id UUID NOT NULL
nominal_diameter_m DOUBLE PRECISION NOT NULL
length_m DOUBLE PRECISION NOT NULL
roughness_manning_n DOUBLE PRECISION NOT NULL
geometry_wkt TEXT NOT NULL
bbox columns NOT NULL
properties JSONB NOT NULL DEFAULT '{}'
UNIQUE(network_revision_id, stable_key)
UNIQUE(network_revision_id, id)
FK(network_revision_id, upstream_node_id) -> network_nodes(network_revision_id,id)
FK(network_revision_id, downstream_node_id) -> network_nodes(network_revision_id,id)
CHECK upstream_node_id <> downstream_node_id
CHECK diameter>0, length>0, roughness>0
```

#### `hydraulic.catchments`

```text
id UUID PK
network_id UUID NOT NULL FK -> networks.id RESTRICT
network_revision_id UUID NOT NULL FK -> network_revisions.id RESTRICT
stable_key VARCHAR(128) NOT NULL
area_m2 DOUBLE PRECISION NOT NULL
outfall_node_id UUID NULL
geometry_wkt TEXT NOT NULL
bbox columns NOT NULL
properties JSONB NOT NULL DEFAULT '{}'
UNIQUE(network_revision_id, stable_key)
UNIQUE(network_revision_id, id)
FK(network_revision_id, outfall_node_id) -> network_nodes(network_revision_id,id)
CHECK area_m2>0
```

#### `hydraulic.elevation_profiles`

```text
id UUID PK
network_revision_id UUID NOT NULL FK -> network_revisions.id CASCADE
pipe_segment_id UUID NOT NULL
station_m DOUBLE PRECISION NOT NULL
offset_m DOUBLE PRECISION NOT NULL
elevation_m DOUBLE PRECISION NOT NULL
UNIQUE(pipe_segment_id, station_m, offset_m)
FK(network_revision_id, pipe_segment_id) -> pipe_segments(network_revision_id,id)
```

- Point/LineString/Polygon 规则和 error/warning 见 `PRD_SUPPLEMENT.md:5`。
- PostGIS profile 在上述表增加 generated/stored typed geometry 和 GiST；portable 不创建。
- `length_m`/area 必须来自导入的工程测量或明确投影计算，MUST NOT 直接把经纬度差当米。

#### 3.3.1 Pond、boundary 与 initial state 的物理模型

```text
hydraulic.pond_cells
id, network_id, network_revision_id, stable_key
area_m2, cell_elevation_m, roughness_manning_n, depression_storage_m
stage_area_curve JSONB                 # closed schema: [{stage_m, area_m2}] strictly increasing
geometry_wkt + bbox
properties JSONB
UNIQUE(network_revision_id,stable_key)

hydraulic.pond_cell_links
id, network_revision_id, from_pond_cell_id, to_pond_cell_id
crest_elevation_m, width_m, roughness_manning_n, max_capacity_m3s
direction forward|bidirectional
properties JSONB
FK both cells in same network_revision_id

hydraulic.pond_sewer_exchanges
id, network_revision_id, pond_cell_id, target_node_id
direction inflow|outflow|bidirectional
capacity_model_schema_id/version/hash
capacity_parameters JSONB            # closed discriminated schema
enabled BOOLEAN
properties JSONB

hydraulic.boundary_conditions
id, network_revision_id, stable_key
target_type node|pipe_segment
target_id UUID
kind none|free_outfall|fixed_stage|rating_curve|timeseries_inflow
unit m|m3s|ratio|none
parameters_schema_id/version/hash
parameters JSONB                     # closed discriminated schema
content_sha256
UNIQUE(network_revision_id,stable_key)

hydraulic.initial_state_snapshots
id UUID PK
network_revision_id UUID FK -> network_revisions.id RESTRICT
kind dry|warm_state
observed_as_of TIMESTAMPTZ NULL
source_event_ids UUID[] NOT NULL
initial_state_content_sha256 CHAR(64) NOT NULL
created_by UUID FK -> users.id
created_at TIMESTAMPTZ NOT NULL
UNIQUE(network_revision_id,initial_state_content_sha256)
UNIQUE(network_revision_id,id,initial_state_content_sha256)

hydraulic.initial_node_states(initial_state_id,node_id,depth_m,hydraulic_head_m,volume_m3,surcharged)
hydraulic.initial_pipe_states(initial_state_id,pipe_id,flow_m3s,velocity_mps,depth_m,fill_ratio,state)
hydraulic.initial_pond_states(initial_state_id,pond_cell_id,water_surface_elev_m,depth_m,volume_m3)
```

- 每个 FK 都带 `network_revision_id` 复合约束，禁止跨 revision/跨 network 引用。stage-area、capacity、boundary、initial-state payload 均必须由冻结 schema 校验；`dry` snapshot 由 materializer 确定性生成全零 typed rows，warm snapshot 必须完整覆盖全部对象且 `observed_as_of <= simulation_start_at`。
- boundary target、pond link、sewer exchange 和 revision 内结构/几何进入 `network_revisions.content_sha256`；initial state **不进入** revision hash，而以独立 `initial_state_content_sha256` 引用。materialized NetworkSnapshot 另算 `snapshot_content_sha256`；simulation request fingerprint 同时绑定 revision、snapshot、initial-state、target-manifest hash。不得作为 worker 临时默认值或 solver 代码常量。

#### 3.3.2 强类型 solver snapshot（不可用 ORM/任意 JSON 替代）

实现 MUST 在 Pydantic/domain 层定义以下闭合 schema；概念类型名是稳定契约，具体 Python 类名可调整但字段/约束不可：

```text
NetworkSnapshot
  schema_version: Literal["network-snapshot-v2"]
  network_id/network_revision_id: UUID (same revision)
  network_revision_content_sha256: lowercase sha256
  snapshot_content_sha256: lowercase sha256
  initial_state_id: UUID|null
  initial_state_content_sha256: lowercase sha256
  spatial_backend: postgis|portable
  public_crs: Literal["EPSG:4326"]
  solver_crs: str
  solver_crs_wkt: str
  transform_id: UUID (only when public_crs != solver_crs)
  bounds_wgs84: [lon,lat,lon,lat]
  nodes: tuple[NodeSnapshot,...]
  pipes: tuple[PipeSegmentSnapshot,...]
  catchments: tuple[CatchmentSnapshot,...]
  pond_cells: tuple[PondCellSnapshot,...]
  boundaries: tuple[BoundaryConditionSnapshot,...]
  initial_state: InitialStateSnapshot
  properties_schema_id/version/hash: str/int/sha256
  target_manifest_id/content_sha256: UUID/sha256
```

```text
NodeSnapshot
  stable_key, kind, geometry_wgs84
  invert_elevation_m, crown_elevation_m, rim_elevation_m, max_depth_m
  boundary_ref: stable key into boundaries|null
  initial_node_state_ref: stable key into initial_state.nodes
  properties: schema-validated closed object

PipeSegmentSnapshot
  stable_key, upstream_node_key, downstream_node_key
  shape: circular|rectangular|box (discriminated dimensions)
  length_m, roughness_manning_n
  invert_from_m, invert_to_m, crown_from_m, crown_to_m
  geometry_wgs84, profile_ref?
  initial_pipe_state_ref
  properties

CatchmentSnapshot
  stable_key, outlet_node_key
  area_m2, width_m, slope_percent, impervious_fraction
  depression_storage_mm, infiltration_model + typed parameters
  initial_node_state_ref
  properties

PondCellSnapshot
  stable_key, area_m2, elevation_m, roughness_manning_n
  depression_storage_m
  stage_area_curve: non-empty tuple[PondStageArea{stage_m,area_m2}, ...] (stage strictly increasing; area positive)
  cell_links: tuple[PondCellLink,...]
  sewer_exchange_links: tuple[PondSewerExchange,...]
  initial_pond_state_ref
  properties

BoundaryConditionSnapshot
  stable_key, target_type, target_stable_key
  kind: none|free_outfall|fixed_stage|rating_curve|timeseries_inflow
  unit: m|m3s|ratio|none
  parameters: discriminated typed object
  schema_version, parameters_sha256

InitialStateSnapshot
  kind: dry|warm_state
  initial_state_id: UUID|null
  observed_as_of: UTC|null
  source_event_ids: tuple[UUID,...]
  nodes/pipes/pond_cells: complete maps keyed by stable key
  initial_state_content_sha256
```

- `properties` 的 JSON Schema 必须 `additionalProperties=false`；每个实体类型有独立 schema ID/version/hash，保存 `property_keys`。未知字段/版本返回 422/terminal `PROPERTIES_SCHEMA_INVALID`。solver 所需物理量不得藏在 properties。
- boundary/initial-state/parameter object 必须 `extra=forbid`；禁止 `dict[str, Any]`、`object` 或未标记 union。
- 跨字段约束：所有 key 在所属 tuple 内唯一；pipe 两端存在且不同；端点与 geometry 端点误差 `<=1e-7°`；length/dimensions/area/roughness/depression 有限且合法；crown/rim/invert 关系合法；catchment outlet 存在；boundary target 存在；profile 属于对应 pipe；引用数/顺序/曲线严格递增；`kind=dry` 时所有初值为 0 且无未来 source event；warm state 对全部 node/pipe/pond 恰好覆盖一次；`ponding_enabled` 与 pond/links/state 一致；solver capability 与所有 typed fields 交叉校验。
- `dry` 可省略逐对象初值并由 materializer 确定性补零；warm state 不得缺对象。任何默认值必须进入 canonical hash，不能依赖进程环境或 ORM 默认。
- materializer 输出 canonical JSON + `snapshot_content_sha256`；simulation request fingerprint 同时绑定 `network_revision_content_sha256`、`snapshot_content_sha256`、`initial_state_content_sha256`、target-manifest hash、snapshot/properties/transform schema hash。

#### 3.3.3 Revision-scoped target manifest

```text
hydraulic.network_target_manifests
id UUID PK
network_revision_id UUID FK -> network_revisions.id RESTRICT
schema_version INTEGER NOT NULL
ordering_rule VARCHAR NOT NULL       # stable_key UTF-8 byte ascending
target_types VARCHAR[] NOT NULL       # exactly nodes,pipes,catchments,pond_cells
stable_keys JSONB NOT NULL            # per-type dense ordered arrays
feature_indexes JSONB NOT NULL        # stable_key -> dense 0-based index
counts JSONB NOT NULL
content_sha256 CHAR(64) NOT NULL
created_at TIMESTAMPTZ NOT NULL
UNIQUE(network_revision_id,schema_version)
UNIQUE(id,content_sha256)
UNIQUE(network_revision_id,id,content_sha256)
```

- manifest 不可变；`UNIQUE(network_revision_id,schema_version)` 使同一 revision/schema 只能存在一个 `content_sha256`，新 ordering/schema 只能创建新 revision 或新 schema_version。`UNIQUE(network_revision_id,id,content_sha256)` 专供下游 revision-bound 复合 FK。
- `target_types` 精确等于 `nodes,pipes,catchments,pond_cells`；每类数组长度必须等于 counts 且索引恰为 `0..count-1`。catchment 与 map-data `layers=catchments` 共用此唯一 manifest，不存在第二套 catchment index。LOD/simplification 只能返回子集，但必须保留原 feature_index，NULL 表示未返回而非 0。
- simulation、attempt、frame 与 nowcast run 直接保存 `network_revision_id + target_manifest_id + target_manifest_sha256`，并以 `FOREIGN KEY(network_revision_id,target_manifest_id,target_manifest_sha256) -> network_target_manifests(network_revision_id,id,content_sha256)` 绑定；forecast alert 保存同三列并通过五列 simulation 复合 FK 间接命中同一行。map-data、target endpoint、frame decode 和 forecast target lookup MUST 使用同一 revision/manifest，任何组件均不得各自重新排序。

### 3.4 设备绑定、导入与验证

#### `hydraulic.device_network_bindings`

```text
id UUID PK
device_id UUID NOT NULL FK -> public.devices.id RESTRICT
network_id UUID NOT NULL FK -> networks.id RESTRICT
target_type node|pipe_segment
target_stable_key VARCHAR(128) NOT NULL
observation_kind water_level|flow|rainfall|other
mapping_version INTEGER NOT NULL
valid_from_revision_id UUID NOT NULL FK -> network_revisions.id
valid_to_revision_id UUID NULL FK -> network_revisions.id
calibration_offset_m DOUBLE PRECISION NOT NULL DEFAULT 0
active BOOLEAN NOT NULL
created_by UUID NOT NULL FK -> public.users.id
created_at/updated_at TIMESTAMPTZ
UNIQUE(device_id, observation_kind, target_type, target_stable_key, valid_from_revision_id)
```

- seed 只生成 candidate binding；operator/admin 显式确认后 `active=true`。
- `valid_to_revision_id` 失效，不物理删除历史映射。

#### `hydraulic.network_jobs` 与 attempts

```text
hydraulic.network_jobs
id UUID PK
job_type import|validate|seed
network_id UUID NULL FK -> networks.id RESTRICT
base_network_revision_id UUID NULL FK -> network_revisions.id RESTRICT
status queued|leased|running|retry_wait|cancelling|cancelled|completed|failed|timed_out
progress SMALLINT 0..100
phase accepted|staging|parsing|validating|publishing|cleanup
attempt SMALLINT 0..3
max_attempts SMALLINT DEFAULT 3
next_attempt_at TIMESTAMPTZ NULL
retry_backoff_seconds INTEGER NULL
last_retry_error_code VARCHAR NULL
request_fingerprint CHAR(64) NOT NULL
idempotency_record_id UUID NOT NULL FK -> idempotency_records.id RESTRICT
parser_version/profile_version/seed_algorithm_version VARCHAR NULL
lease_owner/lease_token/lease_until
fencing_token BIGINT DEFAULT 0
deadline_at TIMESTAMPTZ NULL
result_artifact_id UUID NULL
error_code/error_message
created_by UUID FK -> users.id
created_at/started_at/finished_at/updated_at
UNIQUE(idempotency_record_id)
CHECK ((status='retry_wait') = (next_attempt_at IS NOT NULL))
CHECK (
  (status='retry_wait' AND retry_backoff_seconds IS NOT NULL AND retry_backoff_seconds>0
   AND last_retry_error_code IS NOT NULL AND btrim(last_retry_error_code)<>'')
  OR
  (status<>'retry_wait' AND retry_backoff_seconds IS NULL AND last_retry_error_code IS NULL)
)

hydraulic.network_job_attempts
id UUID PK
job_id UUID FK -> network_jobs.id RESTRICT
attempt_no SMALLINT 1..3
fencing_token BIGINT NOT NULL
status running|discarded|published|cleanup_pending
workspace_ref VARCHAR
parser_result_sha256 CHAR(64) NULL
error_code/error_message
started_at/finished_at
UNIQUE(job_id,attempt_no)
UNIQUE(job_id,fencing_token)
```

- import/validate/seed 三个 POST 都要求 8..128 字符 Idempotency-Key；同 key+同 request fingerprint 返回原 `network_job_id`，同 key+不同 fingerprint 返回 409 `IDEMPOTENCY_KEY_REUSED`，默认 TTL 24h。
- API transaction 只写 job/idempotency record 后返回 202；MUST NOT 使用 FastAPI BackgroundTasks、asyncio task、进程内 queue 或无 lease 的独立函数。
- `network-job-worker` 以 `FOR UPDATE SKIP LOCKED` claim，30 s lease/10 s heartbeat；每次 claim 增加 fencing token。parser/validator/seed 的所有持久写和状态更新必须校验当前 token。
- claim 条件固定为 `status='queued' OR (status='retry_wait' AND next_attempt_at<=clock_timestamp())`，并有 `(status,next_attempt_at)` 索引。retry_wait 事务写 next time/backoff/error；claim、cancel 或 terminal 清空。禁止 Redis timer 或进程 sleep 代替。
- import/seed 的候选 revision、全部实体、issues、target manifest、job completion 在一个最终事务发布；blocking error/失败/cancel 不产生半张 network。validate 只写 report/result，不修改 revision。
- retry 先把旧 attempt 标 `discarded` 并清理其 workspace，再创建新 attempt；新 attempt 不得复用旧 parser result。相同 attempt 的恢复只在 result hash 相同时候可幂等完成。
- fixture/parser/profile/seed algorithm 必须版本化；golden input 相同则 canonical DTO、validation report、target manifest hash 和 seed network hash 必须确定。安全 fixture 见 `PRD_SUPPLEMENT.md:D-15`。
- 必交 fixture 目录固定为 `backend/tests/fixtures/hydraulic/network_jobs/v1/`，至少含 `valid-minimal.inp`、`valid-minimal.geojson`、`local-transform.json`、`tree-loop.inp`、错误坐标/悬空/逆坡/恶意矩阵、seed candidate binding 和 expected validation/import/seed golden JSON；manifest 固定列 path/sha256/parser/profile/seed version。禁止测试运行时生成“期望值”掩盖非确定性。

#### `hydraulic.network_imports`（job result detail）

```text
id UUID PK
network_id UUID NULL FK -> networks.id
network_revision_id UUID NULL FK -> network_revisions.id
network_job_id UUID NOT NULL UNIQUE FK -> network_jobs.id RESTRICT
source_filename VARCHAR(512)              # display only
source_format inp|geojson
source_sha256 CHAR(64) NOT NULL
byte_size BIGINT NOT NULL
parser_version VARCHAR NOT NULL
committed BOOLEAN NOT NULL DEFAULT false
counts JSONB NOT NULL DEFAULT '{}'
created_by UUID FK -> public.users.id
created_at/finished_at
error_code/error_message
```

#### `hydraulic.validation_runs`

```text
id UUID PK
network_job_id UUID NOT NULL UNIQUE FK -> network_jobs.id RESTRICT
network_id UUID NOT NULL FK -> networks.id
network_revision_id UUID NULL FK -> network_revisions.id
status_value valid|valid_with_warnings|invalid
counts JSONB
simulation_ready BOOLEAN
topology JSONB
created_by UUID FK -> public.users.id
created_at/completed_at
error_code/error_message
```

#### `hydraulic.network_validation_issues`

```text
id UUID PK
validation_run_id UUID FK -> validation_runs.id CASCADE
severity error|warning|info
code VARCHAR NOT NULL
entity_type VARCHAR NOT NULL
entity_key VARCHAR NOT NULL
message TEXT NOT NULL
source_line INTEGER NULL
details JSONB
UNIQUE(validation_run_id, code, entity_type, entity_key, source_line)
```

### 3.5 Rainfall 与参数集

#### `hydraulic.rainfall_scenarios`

```text
id UUID PK
network_id UUID FK -> networks.id
root_scenario_id UUID NULL FK -> rainfall_scenarios.id
version INTEGER NOT NULL
name VARCHAR NOT NULL
kind design|custom|synthetic
return_period_years NUMERIC NULL
step_seconds INTEGER NOT NULL             # 60|300|900
duration_seconds INTEGER NOT NULL         # <=259200
source_type synthetic
algorithm VARCHAR NOT NULL
random_seed BIGINT NOT NULL
content_sha256 CHAR(64) NOT NULL
created_by UUID FK -> public.users.id
created_at
```

约束：design 的 return period 只能 1/2/3/5/10/20/50/100；scenario 一旦被 simulation 引用不可更新。复制生成新 ID，保留 root/seed/version+1。

#### `hydraulic.rainfall_points`

```text
scenario_id UUID FK -> rainfall_scenarios.id CASCADE
point_index INTEGER
valid_at TIMESTAMPTZ
intensity_mm_h DOUBLE PRECISION
PRIMARY KEY(scenario_id, point_index)
CHECK intensity_mm_h>=0
UNIQUE(scenario_id, valid_at)
```

#### `hydraulic.parameter_sets`

```text
id UUID PK
name VARCHAR NOT NULL
kind default|calibrated
parameters JSONB NOT NULL
content_sha256 CHAR(64) NOT NULL
created_by UUID FK -> public.users.id
created_at
```

v1 可率定字段白名单：Manning multiplier、depression storage、infiltration 参数、catchment width/slope correction、initial loss/remaining parameters。`parameters` 必须按 `parameter_schema_id/version/hash` 强类型解析，`extra=forbid`；未知字段、单位不匹配或非有限值 422。schema/hash 进入 parameter/simulation fingerprint。

### 3.6 Simulation、worker 与 frame

#### `hydraulic.simulations`

```text
id UUID PK
requested_by UUID FK -> public.users.id
network_id UUID FK -> networks.id
network_revision_id UUID NOT NULL
target_manifest_id UUID NOT NULL
target_manifest_sha256 CHAR(64) NOT NULL
FOREIGN KEY(network_revision_id,target_manifest_id,target_manifest_sha256)
  -> network_target_manifests(network_revision_id,id,content_sha256)
network_revision_content_sha256 CHAR(64) NOT NULL
FOREIGN KEY(network_revision_id,network_revision_content_sha256)
  -> network_revisions(id,content_sha256)
snapshot_content_sha256 CHAR(64) NOT NULL
initial_state_id UUID NOT NULL
initial_state_content_sha256 CHAR(64) NOT NULL
FOREIGN KEY(network_revision_id,initial_state_id,initial_state_content_sha256)
  -> initial_state_snapshots(network_revision_id,id,initial_state_content_sha256)
rainfall_scenario_id UUID FK -> rainfall_scenarios.id
parameter_set_id UUID FK -> parameter_sets.id
solver_selection auto|explicit
requested_solver_id VARCHAR NULL
solver_id VARCHAR NOT NULL
solver_version VARCHAR NOT NULL
engine_binary_sha256 CHAR(64) NULL
solver_fallback_reason TEXT NULL
solver_config_hash CHAR(64) NOT NULL
request_fingerprint CHAR(64) NOT NULL
idempotency_record_id UUID NOT NULL FK -> idempotency_records.id RESTRICT
status queued|leased|running|retry_wait|cancelling|cancelled|completed|failed|timed_out
progress SMALLINT NOT NULL DEFAULT 0
phase accepted|validating|solving|quality|persisting|finalizing
attempt SMALLINT NOT NULL DEFAULT 0
max_attempts SMALLINT NOT NULL DEFAULT 3
next_attempt_at TIMESTAMPTZ NULL
retry_backoff_seconds INTEGER NULL
last_retry_error_code VARCHAR NULL
lease_owner VARCHAR NULL
lease_token UUID NULL
lease_until TIMESTAMPTZ NULL
fencing_token BIGINT NOT NULL DEFAULT 0
published_attempt_id UUID NULL FK -> simulation_attempts.id
published_attempt_no SMALLINT NULL
cancel_requested_at TIMESTAMPTZ NULL
deadline_at TIMESTAMPTZ NULL
error_code VARCHAR NULL
error_message TEXT NULL
result_summary JSONB NULL
created_at/started_at/finished_at/updated_at
UNIQUE(idempotency_record_id)
UNIQUE(id, network_revision_id)
UNIQUE(id, network_id, network_revision_id, target_manifest_id, target_manifest_sha256)
UNIQUE(id, fencing_token)
CHECK progress BETWEEN 0 AND 100
CHECK attempt BETWEEN 0 AND 3
CHECK ((status='retry_wait') = (next_attempt_at IS NOT NULL))
CHECK (
  (status='retry_wait' AND retry_backoff_seconds IS NOT NULL AND retry_backoff_seconds>0
   AND last_retry_error_code IS NOT NULL AND btrim(last_retry_error_code)<>'')
  OR
  (status<>'retry_wait' AND retry_backoff_seconds IS NULL AND last_retry_error_code IS NULL)
)
```

- `solver_id` 在提交事务内 probe 并冻结。
- `request_fingerprint` 明确包含 network revision hash、target-manifest hash、snapshot hash、initial-state hash、schema/transform、rainfall、solver/config、parameter schema/set、start/duration/frame interval、ponding 选项；四类 hash 不得互相替代。
- dry submit 的 API input `initial_state_id` 可空，但 transaction 必须先创建同 revision 的 immutable dry snapshot；持久化 simulations 的 `initial_state_id` 始终非空并由 revision+ID+hash 复合 FK 约束。
- 创建 simulation 的同一 transaction 写入 submit-scope idempotency record，并由唯一 `simulations.idempotency_record_id` 引用；transaction 验证 record user/fingerprint/resource_id。首次 cancel 另写 `scope=simulation.cancel` 的 idempotency record，保存 `resource_id=simulation.id` 与首次 response，并在同一 cancel transaction 验证 user/fingerprint；cancel record 不增加第二个 resource FK 列。simulation 表 MUST NOT 恢复 `idempotency_key` 或永久 `UNIQUE(requested_by,idempotency_key)`。24h 后新 generation 使用新的 submit-scope record 和新 `idempotency_record_id` 创建新 simulation。
- `published_attempt_id/no` 在 completed 前必须为空，且只能指向同一 simulation 的 `published` attempt。
- state/frame/result update 必须同时带当前 `fencing_token` 条件。

#### `hydraulic.worker_slots`

```text
slot_id SMALLINT PK CHECK(slot_id IN (1,2))
lane general|nowcast
busy BOOLEAN NOT NULL DEFAULT false
worker_id VARCHAR NULL
simulation_job_id UUID NULL FK -> simulations.id
nowcast_run_id UUID NULL FK -> nowcast_runs.id
lease_until TIMESTAMPTZ NULL
heartbeat_at TIMESTAMPTZ NULL
updated_at TIMESTAMPTZ
CHECK (
  (busy=false AND worker_id IS NULL AND simulation_job_id IS NULL AND nowcast_run_id IS NULL
   AND lease_until IS NULL AND heartbeat_at IS NULL)
  OR
  (busy=true AND worker_id IS NOT NULL AND lease_until IS NOT NULL AND heartbeat_at IS NOT NULL
   AND lease_until>heartbeat_at AND
   ((lane='general' AND simulation_job_id IS NOT NULL AND nowcast_run_id IS NULL)
    OR (lane='nowcast' AND simulation_job_id IS NULL AND nowcast_run_id IS NOT NULL)))
)
```

初始化必须恰好两行且 `busy=false`：slot 1=`general`、slot 2=`nowcast`；两条 seed 的 worker/job/run/lease/heartbeat 均为 NULL。`SIM_GLOBAL_CONCURRENCY=2` 和 `SIM_NOWCAST_RESERVED_SLOTS=1` 都是固定 Literal。claim 在同一事务置 busy=true、worker、对应 job/run，并写 `heartbeat_at=clock_timestamp()`、`lease_until=clock_timestamp()+INTERVAL '30 seconds'`、`updated_at=clock_timestamp()`；busy=true 分支要求 lease/heartbeat 非空且 `lease_until>heartbeat_at`。heartbeat 最多每 10 s 在同一事务刷新这两个时间戳并保持严格大于关系。lane→绑定对象的互斥 CHECK 只在 busy=true 分支生效。进入 cancelling 时，状态转换与 slot release 必须同事务，以 `slot_id + busy=true + worker_id + 当前 job/run 绑定` 为条件，置 busy=false 并清空 worker/job/run/lease/heartbeat；到达 cancelled/completed/failed/timed_out 后再次断言全空。timeout/crash recovery 只领取 `busy=true AND lease_until<=clock_timestamp()` 的行，并在同一事务清空全部占用字段。nowcast 槽即使空闲也不可借给 general。

#### `hydraulic.simulation_attempts`

```text
id UUID PK
simulation_id UUID NOT NULL
network_revision_id UUID NOT NULL
FOREIGN KEY(simulation_id,network_revision_id) -> simulations(id,network_revision_id)
attempt_no SMALLINT NOT NULL CHECK(attempt_no BETWEEN 1 AND 3)
fencing_token BIGINT NOT NULL
status running|discarded|published|cleanup_pending
target_manifest_id UUID NOT NULL
target_manifest_sha256 CHAR(64) NOT NULL
FOREIGN KEY(network_revision_id,target_manifest_id,target_manifest_sha256)
  -> network_target_manifests(network_revision_id,id,content_sha256)
frame_count INTEGER NULL
first_frame_time/last_frame_time TIMESTAMPTZ NULL
frame_manifest_sha256 CHAR(64) NULL
summary_id UUID NULL
quality_report_sha256 CHAR(64) NULL
started_at/finished_at/published_at
UNIQUE(simulation_id,attempt_no)
UNIQUE(simulation_id,fencing_token)
```

#### `hydraulic.simulation_frame_registrations`（普通 PG 幂等索引）

```text
attempt_id UUID NOT NULL FK -> simulation_attempts.id
frame_index INTEGER NOT NULL
frame_time TIMESTAMPTZ NOT NULL
payload_bytes BIGINT NOT NULL
payload_sha256 CHAR(64) NOT NULL
quality_status verified|warning|failed
PRIMARY KEY(attempt_id, frame_index)
UNIQUE(attempt_id, frame_time)
```

registration 与对应 hypertable frame 必须同事务写入；普通 PG 承担跨时间分区唯一性，Timescale 表不承担不兼容的跨 partition unique 假设。

#### `hydraulic.simulation_frames`（attempt-scoped staging hypertable）

```text
frame_time TIMESTAMPTZ NOT NULL
attempt_id UUID NOT NULL
simulation_id UUID NOT NULL
attempt_no SMALLINT NOT NULL
frame_index INTEGER NOT NULL
network_revision_id UUID NOT NULL
target_manifest_id UUID NOT NULL
target_manifest_sha256 CHAR(64) NOT NULL
solver_id VARCHAR NOT NULL
solver_version VARCHAR NOT NULL
schema_version INTEGER NOT NULL
payload JSONB NOT NULL
payload_bytes BIGINT NOT NULL
payload_sha256 CHAR(64) NOT NULL
quality_status verified|warning|failed
PRIMARY KEY(frame_time, attempt_id, frame_index)
FK(attempt_id) -> simulation_attempts(id)
FK(simulation_id,attempt_no) -> simulation_attempts(simulation_id,attempt_no)
FK(simulation_id,network_revision_id) -> simulations(id,network_revision_id)
FOREIGN KEY(network_revision_id,target_manifest_id,target_manifest_sha256)
  -> network_target_manifests(network_revision_id,id,content_sha256)
```

- Timescale hypertable 的 partition key 固定为 `frame_time`；其所有 unique/primary key 必须包含 `frame_time`。跨 partition 的 `(attempt_id,frame_index)` 唯一性只由普通 PG `simulation_frame_registrations` 承担，registration/frame 同事务；hypertable **MUST NOT** 再声明 `UNIQUE(attempt_id,frame_index)`。
- 24 h/5 min staging 必须有 index 0..288，且 frame count/first/last/manifest hash 与 attempt 汇总一致。
- frame 写入必须同时匹配 attempt、simulation、attempt_no、target manifest 和当前 fencing token；同 attempt/frame/index + 同 hash 的恢复写可幂等 no-op，不同 hash 为 `FRAME_ATTEMPT_CONFLICT`。
- 未发布 attempt 的 frame 对结果/forecast API 不可见。finalize 在一个 DB transaction 内验证全部 frame + summary + quality，原子设置 attempt=`published`、`simulations.published_attempt_id/no`、`status=completed`、`progress=100`。
- retry/timeout/cancel 把当前 attempt 置 `discarded` 并清 workspace；新 attempt 使用新 attempt_id 写完整 staging。旧 attempt 永远不能因 `(simulation_id,frame_index)` 冲突阻塞重试，也不能成为 partial result。
- 成功发布不复制 frame，只移动可见指针。90 天 retention 可删除 frame hypertable 数据，但 summary/attempt manifest/audit 保留；`FRAME_RETENTION_EXPIRED` 仍返回 published attempt metadata。discarded staging 由 sweeper 清理，cleanup_pending 不改变 simulation 终态。

### 3.7 Observation、calibration 与 A/B

#### `hydraulic.observation_trust_policies`

```text
id UUID PK
source_system VARCHAR NOT NULL
policy_version INTEGER NOT NULL
auth_requirement authenticated|allowlisted_local
binding_requirement active_required|none
trust_level external_verified|external_unverified|synthetic|mock
valid_from/valid_until TIMESTAMPTZ NOT NULL
policy_sha256 CHAR(64) NOT NULL
created_at TIMESTAMPTZ NOT NULL
UNIQUE(source_system,policy_version)
EXCLUDE USING gist (source_system WITH =, tstzrange(valid_from,valid_until,'[)') WITH &&)
```

迁移必须启用 `btree_gist`，上述 exclusion constraint 在 DB 层拒绝同一 source 的有效期重叠。adapter 按 source_observed_at 显式选择 policy version，禁止自动取“最新”。`external_verified` policy 必须同时声明 auth 与 active-binding requirement。

#### `hydraulic.observation_ingest_events`

```text
id UUID PK
source_system VARCHAR NOT NULL
source_stream VARCHAR NOT NULL
source_partition VARCHAR NOT NULL
source_offset VARCHAR NOT NULL
source_event_id CHAR(64) NOT NULL UNIQUE
source_observed_at TIMESTAMPTZ NOT NULL
received_at TIMESTAMPTZ NOT NULL
adapter_version VARCHAR NOT NULL
trust_policy_id UUID NULL FK -> observation_trust_policies.id
trust_policy_version INTEGER NULL
trust_policy_sha256 CHAR(64) NULL
trust_policy_resolution matched|missing|expired
trust_level external_verified|external_unverified|synthetic|mock
auth_verified BOOLEAN NOT NULL
binding_verified BOOLEAN NOT NULL
source_payload_sha256 CHAR(64) NOT NULL
status accepted|conflict|quarantined
error_code/error_message
UNIQUE(source_system, source_stream, source_partition, source_offset)
CHECK (trust_policy_resolution <> 'missing' OR
       (trust_policy_id IS NULL AND trust_policy_version IS NULL AND trust_policy_sha256 IS NULL))
CHECK (trust_policy_resolution NOT IN ('matched','expired') OR
       (trust_policy_id IS NOT NULL AND trust_policy_version IS NOT NULL AND trust_policy_sha256 IS NOT NULL))
```

- 正式 source 必须提供稳定 `(source_system, source_stream, source_partition, source_offset)`；`source_event_id=SHA-256(canonical JSON of that tuple)`。禁止用 observed timestamp、value 或本地数组下标冒充 source identity。
- observation ingest 明确不创建 `idempotency_records`、不接受 HTTP Idempotency-Key；其幂等事实源只有 source event identity。ingest transaction 先 `INSERT ... ON CONFLICT DO NOTHING`：若既有 event payload hash 相同，返回既有 event；若不同，标记/返回 `SOURCE_EVENT_CONFLICT`，绝不覆盖或插入 sample。
- event 接受后，其全部 sample rows 必须同事务成功；`binding_verified=true` 仅当 event 内每个 sample 都解析到当时 active 的 revision-scoped binding，否则整个 event 降为 `external_unverified`。event/sample 写入受数据库约束，不能出现半个 event。事件 identity/offset 的推进与消费必须可恢复且不丢事件。
- 无匹配 policy 时 `trust_policy_id/version/hash` 必须为 NULL，`trust_policy_resolution=missing`、`auth_verified=false`、`binding_verified=false`、`trust_level=external_unverified`、`status=quarantined`；过期 policy 同理并标记 `expired`。`quarantined` 只属于 status，绝不是 trust_level。
- policy matched 时仍须逐项验证：`external_verified` 只有在 `auth_verified=true`、`binding_verified=true` 且全部 sample quality good 时保持；任一失败自动降为 `external_unverified`。payload/source 不得自报 trust_level。
- 只有 `external_verified + auth_verified=true + binding_verified=true + quality good` 可用于正式同化、observed transition 和 AC-5。
- 当前 Influx measurement 若没有上游稳定 event ID/offset，adapter 必须登记 `external_unverified` 并 fail closed for formal forecast/AC-5；不得声称 exactly-once 或用读 cursor 代替 source identity。

#### `hydraulic.observation_samples`

```text
observed_at TIMESTAMPTZ NOT NULL
ingest_event_id UUID NOT NULL FK -> observation_ingest_events.id
sample_index INTEGER NOT NULL
network_id UUID NOT NULL
target_type node|pipe_segment
target_stable_key VARCHAR NOT NULL
metric water_depth_m|flow_m3s
value DOUBLE PRECISION NOT NULL
unit VARCHAR NOT NULL
quality_flag good|bad|missing|stale
source_point_ref JSONB NOT NULL
ingest_version VARCHAR NOT NULL
created_at TIMESTAMPTZ NOT NULL
PRIMARY KEY(observed_at, ingest_event_id, sample_index)
```

- Timescale hypertable，90 天 retention；每个 sample 的 `observed_at` 必须等于所属 event 的 `source_observed_at`，因此 `(ingest_event_id,sample_index)` 在同一分区键下唯一。
- `synthetic/mock/external_unverified` 可写 smoke/诊断数据，但 forecast materializer、正式 confidence、observed transition 与 AC-5 MUST 拒绝。
- 原始 Influx point 通过 `source_point_ref` 可回查；quality bad/missing/stale 不参与率定/observe，未来时刻不得进入当前状态输入。

#### `hydraulic.calibration_runs`

```text
id UUID PK
baseline_simulation_id UUID NOT NULL FK -> simulations.id
candidate_simulation_id UUID NULL FK -> simulations.id
observation_dataset_sha256 CHAR(64) NOT NULL
split_manifest JSONB NOT NULL
calibration_config_hash CHAR(64) NOT NULL
metrics JSONB NOT NULL DEFAULT '{}'
improved BOOLEAN NULL
status queued|leased|running|retry_wait|cancelling|cancelled|completed|failed|timed_out
progress SMALLINT NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100)
attempt SMALLINT NOT NULL DEFAULT 0
max_attempts SMALLINT NOT NULL DEFAULT 3
next_attempt_at TIMESTAMPTZ NULL
retry_backoff_seconds INTEGER NULL
last_retry_error_code VARCHAR NULL
lease_owner/lease_token/lease_until
fencing_token BIGINT NOT NULL DEFAULT 0
deadline_at TIMESTAMPTZ NULL
cancel_requested_at TIMESTAMPTZ NULL
error_code/error_message
requested_by UUID FK -> users.id
idempotency_record_id UUID NOT NULL FK -> idempotency_records.id RESTRICT
request_fingerprint CHAR(64) NOT NULL
created_at/started_at/finished_at/updated_at
UNIQUE(idempotency_record_id)
UNIQUE(id,fencing_token)
CHECK ((status='retry_wait') = (next_attempt_at IS NOT NULL))
CHECK (
  (status='retry_wait' AND retry_backoff_seconds IS NOT NULL AND retry_backoff_seconds>0
   AND last_retry_error_code IS NOT NULL AND btrim(last_retry_error_code)<>'')
  OR
  (status<>'retry_wait' AND retry_backoff_seconds IS NULL AND last_retry_error_code IS NULL)
)
```

calibration submit-scope record 必须在创建 run 的同一 transaction 写入并验证 user/fingerprint/resource_id；run 只以 `idempotency_record_id` 引用该 generation record，并由 `UNIQUE(idempotency_record_id)` 防止一 record 多 run。calibration_runs MUST NOT 声明 `idempotency_key` 或永久 `UNIQUE(requested_by,idempotency_key)`。24h 后新 generation 使用新的 record 创建新 calibration run。

#### `hydraulic.calibration_reports`

```text
id UUID PK
calibration_run_id UUID NOT NULL UNIQUE FK -> calibration_runs.id RESTRICT
observation_dataset_sha256 CHAR(64) NOT NULL
split_manifest JSONB NOT NULL
metrics JSONB NOT NULL
report_sha256 CHAR(64) NOT NULL
artifact_uri TEXT NOT NULL
created_at TIMESTAMPTZ NOT NULL
```

calibration completed transaction 必须验证唯一 report 行的 dataset/split/metrics 与 run 一致，并原子写 report、`metrics`、`improved`、status=`completed`、progress=100；非 completed 不得出现 published report。`RESULT_PERSIST_FAILED` 可按共享状态机进入 retry_wait，最多两次后 failed；`INSUFFICIENT_OBSERVATIONS` 为 terminal failed；deadline 只进入 timed_out + `SIMULATION_TIMEOUT`。G3 必交 `backend/tests/fixtures/hydraulic/calibration/durable-lifecycle-v1.json`，至少包含 dataset/split hash、baseline/candidate simulation IDs、claim/retry/cancel/deadline 时间线、retry/failed/timed_out expected rows、metrics、report SHA-256/artifact URI 和 completed report manifest；缺任一项或以 simulation fixture 代替均失败。

#### `hydraulic.ab_comparisons`

```text
id UUID PK
baseline_simulation_id UUID FK -> simulations.id
candidate_simulation_id UUID FK -> simulations.id
frame_grid JSONB NOT NULL
metrics JSONB NOT NULL
comparison_sha256 CHAR(64) NOT NULL
created_by UUID FK -> users.id
created_at
UNIQUE(baseline_simulation_id,candidate_simulation_id,frame_grid)
```

### 3.8 Nowcast、forecast、event 与 notification

#### `hydraulic.probability_calibrations`

```text
id UUID PK
artifact_schema_version INTEGER NOT NULL
method ensemble_exceedance_frequency|calibrated_residual|conformal|calibrated_regression
solver_id/model_version/model_calibration_id NULL
metric water_depth_m|fill_ratio
artifact_uri TEXT NOT NULL                 # controlled local artifact registry, not arbitrary URL
artifact_sha256 CHAR(64) NOT NULL
artifact_format VARCHAR NOT NULL
lead_min_minutes/lead_max_minutes INTEGER NOT NULL
magnitude_min/magnitude_max DOUBLE PRECISION NOT NULL
valid_from/valid_until TIMESTAMPTZ NOT NULL
sample_count/reliability_metric/brier_score NULL
created_at TIMESTAMPTZ NOT NULL
UNIQUE(id,artifact_sha256)
```

#### `hydraulic.forecast_rules`

```text
id UUID PK
rule_schema_version INTEGER NOT NULL
version VARCHAR NOT NULL
alert_type flood_forecast|pipe_surcharge
metric/operator/threshold_value/threshold_unit
clear_value/expiry_grace_minutes/no_forecast_confirmations/clear_debounce_minutes
refresh_expected_shift_steps INTEGER NOT NULL
refresh_peak_delta DOUBLE PRECISION NOT NULL
refresh_confidence_delta NUMERIC(5,4) NOT NULL
refresh_risk_delta NUMERIC(6,2) NOT NULL
risk_scale_m/risk_scale_ratio
artifact_uri TEXT NOT NULL
artifact_sha256 CHAR(64) NOT NULL
valid_from/valid_until TIMESTAMPTZ NOT NULL
created_at TIMESTAMPTZ NOT NULL
UNIQUE(alert_type,version)
UNIQUE(id,artifact_sha256)
```

- 两类工件不可变，文件只允许来自 `HYDRAULIC_ARTIFACT_ROOT` 的服务端路径或受控 artifact registry；启动/首次加载校验 SHA-256、schema、有效期和数值有限性。禁止运行时下载未锁定 URL。
- nowcast config 必须显式绑定 network 对应的 probability calibration 与 flood/surcharge rule ID；不能从“最新”记录或代码常量猜测。`config_hash` 覆盖全部 ID/hash/validity。
- 调度前必须同时解析该 network 的 flood/surcharge calibration+rule，并验证 ID 存在、artifact hash/schema/validity/domain。任何一项缺失时不创建 nowcast run，记录 `FORECAST_POLICY_UNAVAILABLE`，forecast state=`not_ready`；不得生成 `publishable=false` 正式时隙。
- run 创建后、finalize 前若任一 policy 失效，run terminal `failed` / `FORECAST_POLICY_INVALID`，不写 horizon point、alert 或 outbox。历史数据可只读，但不得补发。
- `FORECAST_POLICY_CONFIG_PATH` 使用 closed JSON schema，结构固定为 `schema_version + networks:{network_id:{probability_calibration_ids:{flood_forecast,pipe_surcharge},forecast_rule_ids:{flood_forecast,pipe_surcharge}}}`，文件内禁止 `content_sha256` 或其他自引用 hash 字段。
- policy config 的 `content_sha256` 唯一输入是上述 closed object 按 §1.2 生成的 UTF-8 canonical JSON bytes；同目录固定 sidecar `<FORECAST_POLICY_CONFIG_PATH>.sha256` trim 后必须精确为对应 64 位小写 hex，不允许第二行或其他 token。启动、scheduler 与 G4 重算并逐字比较；config/sidecar 缺失、JSON/schema/hash 非法、引用未知 ID、重复 key 或相对 artifact path 均使 forecast component not ready（API 503、scheduler 不运行）；合法配置中某 network 无 entry 只使该 network not_ready。
- `nowcast_runs.config_hash` 的唯一输入固定为以下 closed object 按 §1.2 生成的 UTF-8 canonical JSON bytes；不得再哈希数据库时间、绝对路径或 config 文件名：

```json
{
  "schema": "nowcast-run-config-v1",
  "network_id": "<uuid>",
  "network_revision_id": "<uuid>",
  "target_manifest_id": "<uuid>",
  "target_manifest_sha256": "<64-lowercase-hex>",
  "forecast_policy_config_sha256": "<64-lowercase-hex>",
  "flood_probability": {"id":"<uuid>","artifact_sha256":"<64-lowercase-hex>","artifact_schema_version":1,"valid_from":"<rfc3339>","valid_until":"<rfc3339>","lead_min_minutes":0,"lead_max_minutes":120,"magnitude_min":0.0,"magnitude_max":0.0},
  "pipe_probability": {"id":"<uuid>","artifact_sha256":"<64-lowercase-hex>","artifact_schema_version":1,"valid_from":"<rfc3339>","valid_until":"<rfc3339>","lead_min_minutes":0,"lead_max_minutes":120,"magnitude_min":0.0,"magnitude_max":0.0},
  "flood_rule": {"id":"<uuid>","artifact_sha256":"<64-lowercase-hex>","rule_schema_version":1,"alert_type":"flood_forecast","metric":"<metric>","operator":"<operator>","threshold_value":0.0,"threshold_unit":"<unit>","valid_from":"<rfc3339>","valid_until":"<rfc3339>"},
  "pipe_rule": {"id":"<uuid>","artifact_sha256":"<64-lowercase-hex>","rule_schema_version":1,"alert_type":"pipe_surcharge","metric":"<metric>","operator":"<operator>","threshold_value":0.0,"threshold_unit":"<unit>","valid_from":"<rfc3339>","valid_until":"<rfc3339>"}
}
```
- AC-4 必交 fixture 路径固定为 `backend/tests/fixtures/hydraulic/forecast/v1/probability-calibration-v1.json`、`forecast-rules-v1.json`、`policy-config-v1.json`、`policy-config-v1.json.sha256` 和 `expected-ac4-v1.json`；golden JSON 包含 config canonical hash/sidecar、artifact ID+hash、validity/domain、issued/producer-published/relay-published 时间、expected alert/outbox。fixture、sidecar 缺失或任一 hash 漂移时 gate 失败。

#### `hydraulic.nowcast_runs`

```text
id UUID PK
network_id UUID FK -> networks.id
network_revision_id UUID NOT NULL
target_manifest_id UUID NOT NULL
target_manifest_sha256 CHAR(64) NOT NULL
FOREIGN KEY(network_revision_id,target_manifest_id,target_manifest_sha256)
  -> network_target_manifests(network_revision_id,id,content_sha256)
scheduled_for TIMESTAMPTZ NOT NULL
forecast_origin_at TIMESTAMPTZ NOT NULL       # 5-minute grid origin frozen before solve
issued_at TIMESTAMPTZ NULL                     # business issue instant, immutable on success
published_at TIMESTAMPTZ NULL                  # producer finalize commit; outbox relay-eligible, not relay-delivered
deadline_at TIMESTAMPTZ NOT NULL
status queued|leased|running|retry_wait|cancelling|cancelled|completed|failed|timed_out
# scheduled_for is a due time, not a separate status
attempt SMALLINT NOT NULL DEFAULT 0
max_attempts SMALLINT NOT NULL DEFAULT 3
next_attempt_at TIMESTAMPTZ NULL
retry_backoff_seconds INTEGER NULL
last_retry_error_code VARCHAR NULL
lease_owner VARCHAR NULL
lease_token UUID NULL
lease_until TIMESTAMPTZ NULL
fencing_token BIGINT NOT NULL DEFAULT 0
simulation_id UUID NULL
FOREIGN KEY(network_id,simulation_id,network_revision_id,target_manifest_id,target_manifest_sha256)
  -> simulations(id,network_id,network_revision_id,target_manifest_id,target_manifest_sha256)
rainfall_scenario_id UUID FK -> rainfall_scenarios.id
model_version VARCHAR NOT NULL
solver_id/solver_version VARCHAR NOT NULL
model_calibration_id UUID NULL
flood_forecast_probability_calibration_id UUID NOT NULL
flood_forecast_probability_sha256 CHAR(64) NOT NULL
pipe_surcharge_probability_calibration_id UUID NOT NULL
pipe_surcharge_probability_sha256 CHAR(64) NOT NULL
flood_forecast_rule_id UUID NOT NULL
flood_forecast_rule_sha256 CHAR(64) NOT NULL
pipe_surcharge_rule_id UUID NOT NULL
pipe_surcharge_rule_sha256 CHAR(64) NOT NULL
FOREIGN KEY(flood_forecast_probability_calibration_id,flood_forecast_probability_sha256)
  -> probability_calibrations(id,artifact_sha256)
FOREIGN KEY(pipe_surcharge_probability_calibration_id,pipe_surcharge_probability_sha256)
  -> probability_calibrations(id,artifact_sha256)
FOREIGN KEY(flood_forecast_rule_id,flood_forecast_rule_sha256)
  -> forecast_rules(id,artifact_sha256)
FOREIGN KEY(pipe_surcharge_rule_id,pipe_surcharge_rule_sha256)
  -> forecast_rules(id,artifact_sha256)
config_hash CHAR(64) NOT NULL
cancel_requested_at TIMESTAMPTZ NULL
error_code/error_message
created_at/started_at/completed_at/updated_at
UNIQUE(network_id, scheduled_for)
UNIQUE(id, issued_at)
UNIQUE(id, fencing_token)
CHECK attempt BETWEEN 0 AND 3
CHECK ((status='retry_wait') = (next_attempt_at IS NOT NULL))
CHECK (
  (status='retry_wait' AND retry_backoff_seconds IS NOT NULL AND retry_backoff_seconds>0
   AND last_retry_error_code IS NOT NULL AND btrim(last_retry_error_code)<>'')
  OR
  (status<>'retry_wait' AND retry_backoff_seconds IS NULL AND last_retry_error_code IS NULL)
)
```

- scheduler 到 `scheduled_for` 时以 `status=queued` 插入/确保唯一 run；`scheduled/submitted/succeeded/cancel_requested` 均不是合法状态。lease/fencing 更新与 simulation 一样必须 CAS；nowcast worker 调用共享 `durable_job_state`，不复制状态机。
- `forecast_origin_at` 在 solve 前冻结并按 5 分钟对齐；`issued_at` 与 nowcast `published_at` 均由 nowcast finalizer 在同一 transaction 写 `transaction_timestamp()`，表示结果可见且 outbox 已提交、首次可被 relay 读取；二者随后不可修改。`event_outbox.published_at` 是 relay 成功发布后的独立时间戳。
- `lead_time_minutes = max(0, floor((expected_at - issued_at)/60s))`；不要求 lead 是 5 的倍数。
- `data_state` freshness 独立计算：默认 `now - issued_at <= 900s` 且无更新 due slot 失败时 ready，否则 stale。`deadline_at` 只控制本 run 是否可发布，不能刷新 age。
- completed 的 finalize transaction 必须同时写 issued_at/published_at、simulation published attempt、artifact 有效性结果和 alert/transition/outbox rows；不等待 Redis relay。nowcast `published_at` 的唯一 writer 是 finalizer，relay 禁止回写该列；late result 永不补发。
- nowcast `retry_wait` 同样持久化 `next_attempt_at/backoff/error`，scheduler/worker 只处理 DB due row；claim、cancel、terminal 时清空并使用 `(status,next_attempt_at)` 索引。nowcast deadline 仍是 scheduled_for+180 s，next time 不得越过 deadline。

#### `hydraulic.forecast_alerts`

```text
id UUID PK
network_id UUID FK -> networks.id
nowcast_run_id UUID FK -> nowcast_runs.id
simulation_id UUID NOT NULL
network_revision_id UUID NOT NULL
target_manifest_id UUID NOT NULL
target_manifest_sha256 CHAR(64) NOT NULL
FOREIGN KEY(network_id,simulation_id,network_revision_id,target_manifest_id,target_manifest_sha256)
  -> simulations(id,network_id,network_revision_id,target_manifest_id,target_manifest_sha256)
nowcast_issued_at TIMESTAMPTZ NOT NULL
alert_type flood_forecast|pipe_surcharge
target_type node|pipe_segment
target_id UUID NOT NULL
target_stable_key VARCHAR(128) NOT NULL
expected_at TIMESTAMPTZ NOT NULL
lead_time_minutes INTEGER NOT NULL
phase predicted|observed
status open|acknowledged|resolved|expired
level info|warning|critical
confidence NUMERIC(5,4) NOT NULL
confidence_method ensemble_exceedance_frequency|calibrated_residual|conformal|calibrated_regression
probability_calibration_id UUID NOT NULL
probability_calibration_sha256 CHAR(64) NOT NULL
forecast_rule_id UUID NOT NULL
forecast_rule_sha256 CHAR(64) NOT NULL
FOREIGN KEY(probability_calibration_id,probability_calibration_sha256)
  -> probability_calibrations(id,artifact_sha256)
FOREIGN KEY(forecast_rule_id,forecast_rule_sha256)
  -> forecast_rules(id,artifact_sha256)
threshold JSONB NOT NULL
measurements JSONB NOT NULL
risk_score NUMERIC(6,2) NOT NULL
risk_policy_version VARCHAR NOT NULL
suppression_key CHAR(64) NOT NULL
first_qualifying_at/last_qualifying_at/peak_at/peak_value
observation_ingest_event_id UUID NULL FK -> observation_ingest_events.id
observed_at/acknowledged_by/acknowledged_at/resolved_at/resolved_by/resolution_reason
version INTEGER NOT NULL DEFAULT 1
created_at/updated_at
FOREIGN KEY(nowcast_run_id, nowcast_issued_at) -> nowcast_runs(id, issued_at)
```

- `nowcast_runs` MUST 建立 `UNIQUE(id, issued_at)`；alert 只能引用已 issued 的 run，不能引用 scheduled/null-issued run。alert 的 simulation/revision/manifest 复合 FK 必须命中同一行；`target_type + target_stable_key` 必须在绑定 manifest 的对应 target type 中存在，`target_id` 必须解析到该 stable key。G4 fixture 必须逐 alert 校验这三项，禁止 service-only 的跨 revision/target 组合。
- `phase=observed` 当且仅当 `observation_ingest_event_id` 与 `observed_at` 非空；该 event 必须为 `external_verified`、binding verified、quality good 且匹配 alert target/rule。predicted 时两列必须为空。

#### `hydraulic.forecast_alert_windows`

```text
suppression_key CHAR(64) PK
active_alert_id UUID NULL FK -> forecast_alerts.id
cooldown_until TIMESTAMPTZ NULL
last_candidate_at TIMESTAMPTZ NOT NULL
last_qualifying_at TIMESTAMPTZ NULL
suppressed_count BIGINT NOT NULL DEFAULT 0
version BIGINT NOT NULL DEFAULT 0
created_at/updated_at
```

#### `hydraulic.forecast_alert_transitions`

```text
id UUID PK
alert_id UUID FK -> forecast_alerts.id
aggregate_version INTEGER NOT NULL
from_status/to_status VARCHAR
from_phase/to_phase VARCHAR
action create|acknowledge|observe|resolve|expire|refresh|escalate
reason TEXT
actor_id UUID NULL FK -> users.id
idempotency_record_id UUID NULL FK -> idempotency_records.id RESTRICT
occurred_at TIMESTAMPTZ NOT NULL
UNIQUE(alert_id, aggregate_version)
```

#### `hydraulic.event_outbox`

```text
sequence BIGSERIAL PK
event_id UUID UNIQUE NOT NULL
network_id UUID NOT NULL
event_type alert.created|alert.updated|alert.escalated|alert.observed|alert.resolved|alert.expired
aggregate_id UUID NOT NULL
aggregate_version INTEGER NOT NULL
schema_version INTEGER NOT NULL
payload JSONB NOT NULL
occurred_at TIMESTAMPTZ NOT NULL
published_at TIMESTAMPTZ NULL                 # relay success only; never nowcast producer time
expires_at TIMESTAMPTZ NOT NULL
created_at TIMESTAMPTZ NOT NULL
UNIQUE(aggregate_id, aggregate_version, event_type)
```

- sequence 是 SSE `Last-Event-ID`。
- payload 插入后不可修改。
- replay 默认 168 h；只清 `published_at IS NOT NULL AND expires_at<now()`。

#### `hydraulic.notification_deliveries`

```text
id UUID PK
event_id UUID NOT NULL
alert_id UUID NOT NULL
channel in_app|email|webhook
destination_id VARCHAR NOT NULL
template_version VARCHAR NOT NULL
status pending|sending|retry_wait|delivered|dead_letter|suppressed
attempt_count SMALLINT NOT NULL DEFAULT 0
next_attempt_at TIMESTAMPTZ NULL
lease_until TIMESTAMPTZ NULL
provider_message_id VARCHAR NULL
last_error_code VARCHAR NULL
created_at/sent_at/updated_at
UNIQUE(event_id, channel, destination_id, template_version)
```

#### `hydraulic.idempotency_records`

```text
id UUID PK
scope VARCHAR NOT NULL
idempotency_key VARCHAR NOT NULL
user_id UUID NOT NULL FK -> users.id
generation INTEGER NOT NULL DEFAULT 1
request_fingerprint CHAR(64) NOT NULL
resource_id UUID NULL
response_status INTEGER NULL
response_body JSONB NULL
created_at TIMESTAMPTZ NOT NULL
expires_at TIMESTAMPTZ NOT NULL
retired_at TIMESTAMPTZ NULL
UNIQUE(scope, idempotency_key, user_id, generation)
UNIQUE(scope, idempotency_key, user_id) WHERE retired_at IS NULL
CHECK(generation >= 1)
CHECK(expires_at = created_at + INTERVAL '24 hours')
CHECK(retired_at IS NULL OR retired_at = expires_at)
```

`id` 是 API 事务生成的不可变 surrogate identity；active record 由 partial unique `UNIQUE(scope,idempotency_key,user_id) WHERE retired_at IS NULL` 唯一，历史 generation 由四列 unique 保留。API transaction 必须先执行 `pg_advisory_xact_lock(hashtextextended(json_build_array(scope,idempotency_key,user_id)::text,0))`，再 `SELECT ... FOR UPDATE` active row：不存在则 generation=1 插入；存在且 `expires_at>clock_timestamp()` 时，同 fingerprint 返回首次 status/body/resource，不同 fingerprint 409；已过期则先置 `retired_at=expires_at`，再以 `generation+1` 插入新 request。并发请求不得生成两个 active generation。

`network_jobs.idempotency_record_id`、`simulations.idempotency_record_id`、`calibration_runs.idempotency_record_id` 与 `forecast_alert_transitions.idempotency_record_id` 均以 UUID FK 指向本表 `id`；前三个 resource 表各自 `UNIQUE(idempotency_record_id)`，不得以未声明的复合键、自由 UUID 或永久 key unique 代替。创建 resource/idempotency record 的同一事务必须验证 scope/key/user/fingerprint/resource_id 一致。simulation cancel 的 `scope=simulation.cancel` record 仍保存 `resource_id/response` 并参与 replay/retire，但不得增加 simulations 的第二个 FK 列。retention sweeper 至少每 5 分钟执行同一 retire 条件；`retired_at+90d` 后仅可删除 `resource_id IS NULL` 且无 transition FK 引用的行，任何 resource/cancel generation 永不物理删除。G2 fixture 必须覆盖 submit/cancel 未到期 replay、fingerprint conflict、expiry/new generation、同 key 并发及 FK/audit retention。

## 4. 空间双路径、bbox 与导入导出

### 4.1 PostGIS profile

- migration 必须先确认/创建 `postgis` extension；缺失即失败。
- 节点 geometry：`geometry(Point,4326)`；管段：`geometry(LineString,4326)`；汇水区：`geometry(Geometry,4326)` + type CHECK（Polygon/MultiPolygon）。
- geometry 由规范 WKT 生成/校验；PostGIS 列不是第二个可写业务事实源。
- 创建 GiST；bbox 使用 `geometry && ST_MakeEnvelope(...)`。
- 拓扑仍由关系 FK/graph traversal，PostGIS 不替代。

### 4.2 Portable profile

- 只使用 WKT+bbox 和同一受测 geometry codec。
- SQL 先以 min/max bbox 粗过滤，service 再精确相交。
- 固定分页排序；cursor 是签名/校验 opaque key，不能包含客户端 SQL 字段。
- 未跑固定规模 benchmark 前，能力响应 `validated_scale=null`。

### 4.3 Map-data API 逻辑

`GET /api/networks/{network_id}/map-data`：

- query：`bbox=west,south,east,north`、`zoom`、`layers=nodes,pipes,catchments,ponding`、`simplify=true`。
- bbox 必须 finite，lon `[-180,180]`、lat `[-90,90]`、min<max。
- `target_manifest_id/content_sha256` 必须返回；feature_index 直接引用该 immutable manifest，在同一 revision/schema 内稳定、dense、从 0 连续，按 stable key UTF-8 byte 升序生成。`catchments` layer 必须使用同一 manifest 的 `target_type=catchments`，不得另建 catchment index。
- LOD/simplification 只可减少返回 feature 集合；保留原 feature_index，缺失值用 null，不得重新编号。
- 超过服务端简化/返回预算时 `complete=false` + `truncated_layers[]`；MUST NOT 冒充全量。
- ETag 包含 network/revision/zoom/bbox hash/layers/simplify version。

### 4.4 Import

1. API 验证 JWT/RBAC/Idempotency-Key；流式写 quarantine，读取到 20 MiB+1 立即 413。
2. API 只在 `network_jobs`/idempotency transaction 中持久化 job 与脱敏 source metadata 后返回 202；不解析、不校验 topology、不 seed。
3. `network-job-worker` 以 lease/fencing claim；每个 job attempt 使用独立 workspace，解析/校验/seed 均不执行 INP include/脚本、不访问 GeoJSON URL。
4. import/seed 构造强类型 canonical DTO、target manifest、content hash、bbox、topology；validation fixture 必须使用冻结 parser/profile/seed version。
5. blocking error：attempt `discarded`，job terminal failed/cancelled，import detail `committed=false`；最终发布事务不写任何 revision/entity。
6. 成功：单事务写 revision/entities/target manifest/import/validation issues，并标 job completed；结果 API 只读取 published job/revision。
7. finally 清理 quarantine/attempt workspace；accepted source 由 retention sweeper 管理，cleanup_pending 不重跑 parser/seed。

HTTP 固定：multipart/JSON 无法解析为 400 `MALFORMED_REQUEST`；路径穿越、盘符、UNC、ADS、symlink/junction 一律 422 `UNSAFE_PATH`；格式白名单 415；大小 413；可定位 INP/GeoJSON 错误 422。四者都不可重试。

### 4.5 Export

- `GET /api/networks/{network_id}/exports/{format}`，`format=geojson|inp`，query `revision_id`。
- GeoJSON 从 canonical DTO 生成。
- INP 仅 `manifest.exportable=true`；生成后重新 parse，比较 stable key/count/topology/hash。
- round-trip 失败返回 422 `EXPORT_NOT_REPRESENTABLE` 或 `ROUNDTRIP_VALIDATION_FAILED`，不返回伪可运行文件。
- 响应不包含内部绝对路径、secret、SQL 或用户原始路径。

## 5. RainfallSource 与求解器接口

### 5.1 RainfallSource

```python
class RainfallSource(Protocol):
    source_id: str
    version: str

    def materialize(self, request: RainfallRequest) -> RainfallSeries: ...
```

`RainfallSeries` 必须含 start/end、native/internal step、intensity array、catchment allocation、seed/algorithm/version、total depth、content hash。

### 5.2 SolverAdapter

提交字段固定为 `solver_selection=auto|explicit`：

- `explicit`：`requested_solver_id` 必填；probe 不可用或 version/hash 不符时 503/terminal，不入队。
- `auto` + requested solver：优先 probe requested solver；EPA 不可用时在提交事务内冻结为 `py_mw` 并记录 fallback reason。
- `auto` + 无 requested solver：优先 `swmm_kw`，不可用时冻结为 `py_mw`。
- 无论哪种选择，worker 启动后不得换 solver。

```python
class SolverAdapter(Protocol):
    solver_id: str
    version: str

    async def probe(self) -> SolverProbe: ...
    def capabilities(self) -> SolverCapabilities: ...
    def validate(self, snapshot: NetworkSnapshot, request: SolveRequest) -> ValidationReport: ...
    async def run(
        self,
        request: SolveRequest,
        snapshot: NetworkSnapshot,
        frame_sink: FrameSink,
        control: RunControl,
    ) -> SolveSummary: ...
```

### 5.3 FrameSink

```python
class FrameSink(Protocol):
    async def write(self, frame: SimulationFrame) -> None: ...
    async def finalize(self, summary: SolveSummary) -> None: ...
```

- solver MUST 流式写当前 `attempt_id` 的 staging，MUST NOT 构造全量 list，也不得写其他 attempt。
- `write` 以 `(attempt_id, frame_index, payload_sha256, fencing_token)` 幂等；同 key 不同 hash 抛 `FRAME_ATTEMPT_CONFLICT`，旧 token 抛 `LEASE_EXPIRED`。
- DB/fencing error 由 sink 抛出，solver 不得吞掉；retry 会创建新 attempt，不复用旧 staging。
- `finalize` 必须在验证 target manifest、frame count/time grid/hash、summary、quality 后原子发布 attempt 并令 state=`completed`；仅写完 frame 或进程退出不得 complete。

### 5.4 EPA SWMM

- engine 固定 path、version、SHA-256；API/worker 不加载未校验 DLL。
- `subprocess.Popen(argv_list, shell=False)`。
- 输入/输出仅在 task sandbox；用户 filename 不进入 argv/path。
- Windows 使用 Job Object/进程树清理；POSIX 使用独立 process group。
- `.rpt/.out` parser 与 engine version 绑定；缺完整结果不能用 summary 伪造 frame。
- explicit EPA 不可用/version mismatch：submit 或 job terminal，不 fallback。

### 5.5 Python solver

- `py_mw`：节点连续性 + 管段 Manning 关系，支持树/环、容量受限压力流近似；不解析水锤。
- `py_dw`：守恒通量、局部惯性省略；只接受缓坡亚临界；Froude `>=0.95`、快速反转或压力流 fail。
- `py_ponding`：预离散 cell 蓄量/交换；UI/结果标 `simplified_extent=true`。
- 所有 solver 输出相同 frame schema、unit、solver/version、quality diagnostics。

### 5.6 质量/稳定

- mass ledger 和 transfer ledger 规则见 `PRD_SUPPLEMENT.md:6.1`。
- 初始 `dt=min(rain_step, reporting_interval, 60s)`。
- 每步 `Courant<=0.5`；reporting interval 最多 20 substeps。
- `py_*` 残差门禁：verified `<=1e-4`、warning `<=1e-3`、failed `>1e-3`。
- NaN/Inf、负状态超容差、未收敛、py_dw 非法流态均不得 verified。

## 6. 持久任务、租约与沙箱

### 6.1 状态转换表

本表是 `simulations`、`network_jobs`、`nowcast_runs`、`calibration_runs` 的唯一 durable-job 状态机。四个 repository 必须调用同一 `durable_job_state` service/transition policy；worker 禁止复制或分叉状态名/转换。nowcast 只额外要求 `deadline_at=scheduled_for+180s` 和 policy finalize，calibration 只额外绑定 dataset；二者不增加 `scheduled/submitted/succeeded/cancel_requested` 等别名。

| 当前 | 事件/守卫 | 下一状态 |
|---|---|---|
| queued | claim authorized worker/lease（simulation/nowcast 还须 claim 对应 worker_slot） | leased |
| queued | cancel requested before lease | cancelling |
| leased/running/retry_wait | cancel requested | cancelling |
| leased | worker heartbeat 成功 | running |
| leased/running | transient + attempt<max_attempts | retry_wait |
| retry_wait | nowcast only：`clock_timestamp()>=deadline_at` 或 `next_attempt_at>=deadline_at`；此守卫优先于 due claim | timed_out |
| retry_wait | `next_attempt_at<=clock_timestamp()`；nowcast 还须 `clock_timestamp()<deadline_at`；general 在新 claim 后设置 attempt deadline | leased |
| retry_wait | non-recoverable error、poison job 或 attempt exhausted | failed |
| cancelling | worker/process tree 或 task 停止、cleanup、commit | cancelled |
| running | 当前 attempt deadline exceeded | timed_out |
| leased/running | non-recoverable error 或 attempt exhausted | failed |
| running | domain finalize 原子成功（simulation=frame+summary+quality；job=result；nowcast=issued+producer-published+alerts+outbox-committed；calibration=metrics+report） | completed |

- 上表是四类 durable job 的**唯一转换表**。`queued` 不得直达 `cancelled`，只能按 `queued→cancelling`；`running` 的不可恢复失败按 `running→failed`；`retry_wait` 的不可恢复/耗尽按 `retry_wait→failed`。
- `retry_wait→timed_out` **只属于 nowcast**。general（simulation/network job/calibration）在 retry_wait 中没有该转换；其 attempt timeout 只能由表中的 `running→timed_out` 表达。nowcast 的固定 deadline 守卫优先于 due claim，general 新 claim 后才设置 `deadline_at=DB now()+600s`。
- 表中所有 `→timed_out` 转换必须原子写 `error_code=SIMULATION_TIMEOUT`；`→failed` 的 `error_code` MUST NOT 为 `SIMULATION_TIMEOUT`。同步 HTTP 504 与异步 status 是不同 surface：异步 submit 不返回 504，GET/202 只能报告 status=`timed_out` + 上述 code。
- 进入 retry_wait 的 transaction 写 `next_attempt_at = clock_timestamp()+retry_backoff_seconds`、backoff 和 `last_retry_error_code`；claim 仅取 `status='retry_wait' AND next_attempt_at<=clock_timestamp()`。成功 claim、cancel 或 terminal 清空；`(status,next_attempt_at)` 建索引。worker/Redis timer 不得决定可执行时间。
- 四类 surface 的 code/status 穷尽表唯一引用 `PRD_SUPPLEMENT.md:10.2`；进入 retry_wait 的 code 必须在该 surface 的 retry 行，attempt=3 时才可 retry_wait→failed 并把同一 code 复制到 error_code。simulation/network-job/calibration backoff 固定 `[10s,60s]`，nowcast 固定 `[5s,15s]`；G3 lifecycle fixture 必须逐行验证。
- simulation、network_jobs、nowcast_runs、calibration_runs 四张表都 MUST 显式包含 `CHECK ((status='retry_wait') = (next_attempt_at IS NOT NULL))`，并包含同形 retry metadata CHECK：`retry_wait` 时 `retry_backoff_seconds` 必须非 NULL 且 `>0`、`last_retry_error_code` 必须非 NULL 且 trim 后非空；非 `retry_wait` 时 backoff/error 两个字段都必须为 NULL。simulation/network job/calibration 进入 retry_wait 时清空当前 attempt `deadline_at`，新 claim 再设置；nowcast 的固定 deadline 不清空。
- 终态不可回退。
- completed 与 cancelling 竞态：只有全部结果已提交，completed 才能赢；否则 cancelled 赢。
- 上表的 cancel-requested 行是取消入口的穷尽集合；cancelling 重复 cancel 幂等返回 200；completed/failed/timed_out 409 `SIMULATION_NOT_CANCELLABLE`。

### 6.2 Progress

```text
0       accepted
1..4    validation/materialize
5..79   solving
80..94  parse/quality
95..99  frame/summary persistence
100     successful finalize commit
```

- monotonic；retry 不回退。
- progress 事件/DB 最多每 1% 或 5 s 更新一次。
- SSE 不是进度事实源；GET status 是。

### 6.3 Lease/fencing

- claim：`SELECT ... FOR UPDATE SKIP LOCKED`，同时锁 worker slot 和 job。
- lease 默认 30 s；heartbeat 每 10 s。
- `RunControl` 取消检查不晚于 1 s。
- 每次 claim 增加 `fencing_token`；state/frame/result update 必须 `WHERE id=? AND fencing_token=?`。
- lease 过期 recovery 只在 attempt<3 时进入 retry_wait；旧 attempt 标 `discarded`、清 workspace/parser output，但保留审计到 cleanup 完成。
- 旧 worker 恢复后即使持有旧 token，也不能写 attempt frame、更新 job 或完成 finalize。
- published attempt 永不因后续 recovery/cancel 变为 discarded；completed 与 cancelling 竞态按第 6.1 节。

### 6.4 Retry

| 错误 | retryable |
|---|---:|
| worker crash/lease expired | 是 |
| 瞬态 DB/network/I/O | 是 |
| 数值未收敛/稳定上限 | 是，带冻结 refinement profile |
| 用户 cancel | 否 |
| 600 s timeout | 否 |
| invalid input/topology/unsupported feature | 否 |
| solver unavailable/version mismatch | 否 |
| input/output limit | 否 |
| mass balance/schema invalid | 否 |

重试前必须清理旧 attempt 目录；每次 refinement 产生新 `solver_config_hash`。默认 backoff 固定为 general simulation/network job `[10s,60s]`，nowcast `[5s,15s]`。本段只解释第 6.1 节已有转换：general simulation 新 claim 时设置新的 `deadline_at=DB now()+600s`；nowcast 使用固定 run deadline，并严格按表中唯一的 `retry_wait→timed_out` 行处理。配置只能显式给出一组递增 backoff 并写入 evidence，不能由 worker 随机 jitter 改变判门禁。

### 6.5 Sandbox

目录：

| 路径 | 唯一 writer | reader | 生命周期 |
|---|---|---|---|
| `HYDRAULIC_IMPORT_ROOT/quarantine/<upload_id>.part` | import service | validator | 异常 finally 删除；stale 24 h |
| `HYDRAULIC_IMPORT_ROOT/accepted/<import_id>/source.*` | import service | materializer | 默认 30 d |
| `HYDRAULIC_SANDBOX_ROOT/<simulation_id>/attempt-<n>/` | 当前 worker/solver | 同 attempt | terminal 最长 24 h |
| `HYDRAULIC_EXPORT_ROOT/<export_id>/` | export service | download service | 默认 24 h |

- client filename 永不作为最终 path。
- candidate `resolve()` 后必须位于 root；拒绝 `/`、`\`、盘符、UNC、ADS、`..`、symlink/junction。
- 接受 `.inp/.geojson`；拒绝 zip、pickle、script、URL、任意可执行文件。
- source/input 各 20 MiB；workspace 2 GiB；frame 64 MiB；job output 8 GiB。
- worker 启动前检查可用磁盘 `>=max(1GiB, 2*job_output_limit)`。
- cancel/timeout/crash/recovery 都 finally cleanup；cleanup 失败写 `cleanup_pending`，由 sweeper 重试，不重跑 solver。

## 7. REST API 冻结契约

### 7.1 通用规则

- base path `/api`；现有 router prefix 见 `backend/app/api/routes.py:60-62`。
- 所有 JSON 使用强类型 request/response model；OpenAPI 必须可生成。
- 列表统一 `limit`（1..200，除非另注）+ opaque cursor；响应 `next_cursor`。
- mutation 角色与 Idempotency-Key：

| Endpoint group | viewer | operator | admin | Idempotency-Key / 并发条件 |
|---|---:|---:|---:|---|
| read network/map/frame/sim/forecast/risk/A/B | 是 | 是 | 是 | 否 |
| create/update/copy rainfall | 否 | 是 | 是 | create/update/copy 均必需；update 另带 expected_version |
| submit/cancel simulation | 否 | 是 | 是 | 均必需 |
| create/update/export network | 否 | 是 | 是 | create/update 必需；update 另带 expected current；export 不要求 |
| import/validate/seed network | 否 | operator（seed 仅 admin） | 是 | 三者全部必需 |
| versioned entity create/update/delete | 否 | 是 | 是 | 必需 + expected_revision_id/If-Match |
| revision activate | 否 | 是 | 是 | 必需 + expected_current_revision_id |
| forecast transition | 否 | 是 | 是 | 必需 + expected_version |
| calibration submit | 否 | 是 | 是 | 必需 |
| archive network / protected config/notification target | 否 | 否 | 是 | archive 无 key；配置 mutation 必需 |
| observation ingest | internal only | internal only | internal only | **禁止 HTTP key/写 idempotency_records**；只用 source event identity |

本表与 `PRD_SUPPLEMENT.md:D-12` 是同一矩阵；其他章节不得增删 key 要求。

- 所有认证 endpoint MUST 校验签名算法、issuer/audience、exp/nbf/iat、sub/role；production fallback admin 必须禁用。forecast 启用还必须满足第 7.6.1 节 G2 前置门禁。
- forecast read/transition、simulation/network-job submit、SSE connect 使用独立限流 bucket；429 带 Retry-After。
- 新 API 不复制 `/mock/*` 的无认证行为：`backend/app/api/routes.py:1039-1076`。
- 错误 envelope/全部 HTTP 与 async error_code 映射唯一来源为 `PRD_SUPPLEMENT.md:10.2`；本文示例不得新增未映射 code。`FORECAST_UNAVAILABLE` 是 503 父码，`FORECAST_POLICY_UNAVAILABLE` 是其 child reason，不是别名。

### 7.2 Network API

| Method/Path | 语义 | 成功 |
|---|---|---|
| GET `/api/networks` | 分页列表 | 200 `NetworkList` |
| POST `/api/networks` | 创建逻辑 network + 空 draft revision | 201 `Network` |
| GET `/api/networks/{id}` | 当前 metadata/revision | 200 `Network` |
| PATCH `/api/networks/{id}` | 修改逻辑字段或创建新 revision | 200 `Network` |
| DELETE `/api/networks/{id}` | 幂等逻辑 archive；保留全部 revision/历史，阻止新任务 | 204 |
| GET `/api/networks/{id}/revisions` | revision 列表 | 200 |
| POST `/api/networks/import` | multipart INP/GeoJSON import | 202 `NetworkImportJob` |
| GET `/api/network-jobs/{id}` | import/validate/seed durable job 状态/attempt/result | 200 |
| GET `/api/network-imports/{id}` | import detail/计数/错误；状态以 parent job 为准 | 200 |
| POST `/api/networks/{id}/validate` | 异步 validation | 202 `ValidationJob` |
| GET `/api/validation-runs/{id}` | validation report | 200/202 |
| GET `/api/networks/{id}/map-data` | bbox/zoom/layers；返回 target manifest ID/hash | 200 `NetworkMapData` |
| GET `/api/networks/{id}/target-manifest` | immutable ordering/count/index manifest | 200 `NetworkTargetManifest` |
| GET `/api/networks/{id}/exports/{format}` | revision GeoJSON/INP | 200 stream |
| POST `/api/networks/{id}/seed` | 生成树环混合候选网络 | 202 |

`POST /api/networks/import`、`POST /api/networks/{id}/validate`、`POST /api/networks/{id}/seed` 全部要求 Idempotency-Key；三者响应统一引用 `network_job_id`，状态只从 `GET /api/network-jobs/{id}` 读取。

##### 版本化实体 CRUD

为满足 FR-1 的 CRUD + bbox，v1 明确提供以下实体端点；所有路径都必须在 service 层校验 `network_id` 与 revision 所属关系：

| Method/Path | 语义 |
|---|---|
| GET `/api/networks/{id}/nodes` | 读取指定/当前 revision 的 nodes，支持 cursor/filter |
| POST `/api/networks/{id}/nodes` | 从 `expected_revision_id` 克隆新 draft revision 并新增 node |
| GET/PATCH/DELETE `/api/networks/{id}/nodes/{node_id}` | 读取；PATCH/DELETE 产生新 draft revision |
| GET/POST `/api/networks/{id}/pipes` | pipe list/create |
| GET/PATCH/DELETE `/api/networks/{id}/pipes/{pipe_id}` | pipe read/mutate |
| GET/POST `/api/networks/{id}/catchments` | catchment list/create |
| GET/PATCH/DELETE `/api/networks/{id}/catchments/{catchment_id}` | catchment read/mutate |
| GET/POST `/api/networks/{id}/pipes/{pipe_id}/elevation-profile` | 读取/新增或替换该 pipe 的 profile |
| DELETE `/api/networks/{id}/pipes/{pipe_id}/elevation-profile` | 从新 draft revision 移除 profile |
| POST `/api/networks/{id}/revisions` | 从指定 revision 克隆新 draft |
| POST `/api/networks/{id}/revisions/{revision_id}/activate` | 校验通过后原子切换 current revision |

实体 mutation 和 `POST /revisions` clone 必须同时携带 Idempotency-Key 和 `expected_revision_id`（或 `If-Match` content hash）；同 key+同 request hash 返回首次创建的新 revision，不再次 clone/apply；不同 hash 409，旧 expected 且不同 key 也 409。DELETE entity 的成功响应是新 revision metadata，不是 204；历史 revision 永远保留。revision activate 必须携带 key + `expected_current_revision_id`，首次成功后同 key 重放返回该 revision，current 已由其他 key 前进则 409。

`Network` 最少字段：

```json
{
  "schema_version": 1,
  "id": "uuid",
  "code": "SH-001",
  "name": "上海示范网络",
  "status": "active",
  "current_revision": {
    "id": "uuid",
    "revision_no": 1,
    "content_sha256": "..."
  },
  "crs": "EPSG:4326",
  "bounds": [121.30,31.15,121.60,31.32],
  "counts": {"nodes": 5000,"pipes":10000,"catchments":1000,"ponding_cells":1000,"profiles":0},
  "simulation_ready": true,
  "created_at": "2026-09-24T10:00:00Z",
  "updated_at": "2026-09-24T10:00:00Z"
}
```

### 7.3 Rainfall API

| Method/Path | 语义 |
|---|---|
| GET `/api/rainfall-scenarios` | 分页/filter network/kind/return_period |
| POST `/api/rainfall-scenarios` | 创建 immutable scenario；`Idempotency-Key` |
| GET `/api/rainfall-scenarios/{id}` | metadata + samples/pagination |
| PATCH `/api/rainfall-scenarios/{id}` | 未引用时创建新 version；已引用 409 |
| DELETE `/api/rainfall-scenarios/{id}` | 无 simulation 引用时删除 |
| POST `/api/rainfall-scenarios/{id}/copy` | 复制；新 ID，保留 seed；`Idempotency-Key` |

### 7.4 Simulation API

#### Submit

`POST /api/simulations`，Header `Idempotency-Key` 必需。

```json
{
  "network_id": "uuid",
  "network_revision_id": "uuid",
  "rainfall_scenario_id": "uuid",
  "parameter_set_id": "uuid",
  "initial_state_id": "uuid",
  "solver_selection": "auto",
  "requested_solver_id": "swmm_kw",
  "start_at": "2026-09-24T00:00:00Z",
  "duration_seconds": 86400,
  "frame_interval_seconds": 300,
  "ponding_enabled": false,
  "strict_stability": true
}
```

- `auto` 可省略 requested solver；explicit 必须提供。
- request `initial_state_id` 可空；空只允许生成绑定当前 revision 的 immutable dry snapshot，202 response 与 simulations row 均返回非空 snapshot UUID。warm state 必须属于同一 revision、`observed_as_of<=start_at` 且完整覆盖所有启用对象。
- submit 成功 202：

```json
{
  "schema_version": 1,
  "id": "uuid",
  "status": "queued",
  "progress": 0,
  "attempt": 0,
  "max_attempts": 3,
  "solver_selection": "auto",
  "solver_id": "py_mw",
  "solver_version": "...",
  "solver_fallback_reason": "epa_swmm_probe_failed",
  "network_revision_id": "uuid",
  "target_manifest_id": "uuid",
  "target_manifest_sha256": "...",
  "network_revision_content_sha256": "...",
  "snapshot_content_sha256": "...",
  "initial_state_id": "uuid",
  "initial_state_content_sha256": "...",
  "rainfall_scenario_id": "uuid",
  "links": {
    "self": "/api/simulations/uuid",
    "cancel": "/api/simulations/uuid/cancel"
  }
}
```

| Method/Path | 语义 |
|---|---|
| GET `/api/simulations` | status/network/rainfall/cursor/limit |
| GET `/api/simulations/{id}` | status/progress/attempt/lease-free summary |
| POST `/api/simulations/{id}/cancel` | 幂等 cancel command；`Idempotency-Key` |
| GET `/api/simulations/{id}/result` | completed summary/quality/aggregate metrics |
| GET `/api/simulations/{id}/frames` | frame index/times/checksum/bytes，不含 value arrays |
| GET `/api/simulations/{id}/frames/{index}` | 单帧紧凑 payload |
| GET `/api/simulations/{id}/observed-comparison` | 预测-实测配对/覆盖/metrics |
| GET `/api/simulations/{id}/calibration` | 关联 calibration report |

Frame response：

```json
{
  "schema_version": "hydraulic-frame-v1",
  "simulation_id": "uuid",
  "network_revision_id": "uuid",
  "target_manifest_id": "uuid",
  "target_manifest_sha256": "...",
  "published_attempt_id": "uuid",
  "published_attempt_no": 1,
  "solver_id": "py_mw",
  "frame_index": 12,
  "frame_time": "2026-09-24T01:00:00Z",
  "nodes": {
    "feature_index": [0,1,2],
    "depth_m": [0.0,0.12,null],
    "surcharged": [false,false,null]
  },
  "pipes": {
    "feature_index": [0,1,2],
    "fill_ratio": [0.21,0.88,null],
    "flow_m3s": [0.4,1.7,null]
  },
  "ponding": {
    "feature_index": [8,19],
    "depth_m": [0.03,0.27]
  },
  "quality": {
    "status": "verified",
    "mass_residual_relative": 0.00001,
    "max_courant": 0.42
  }
}
```

- feature_index 必须引用 frame 的 target manifest；升序、长度/count/hash 完整验证后才发送。LOD 子集保留原 index，null 不转 0。
- 24 h/5 min 索引 0..288。
- 未完成 409；过期 410；非法 index/fields 422。

### 7.5 Calibration 与 A/B

| Method/Path | 语义 |
|---|---|
| POST `/api/calibrations` | 创建异步率定；`Idempotency-Key` |
| GET `/api/calibrations/{id}` | 状态/报告 |
| GET `/api/simulations/{id}/observed-comparison` | 单仿真预测实测 |
| GET `/api/simulations/{baseline}/compare/{candidate}` | A/B metrics |

Calibration submit 最少字段：baseline simulation、network revision、observation window/filter、search method/seed/iterations、parameter whitelist。A/B query 可指定 result version 和 metrics；revision/time grid 不同 409。

### 7.6 Forecast API

| Method/Path | 语义 | 角色 |
|---|---|---|
| GET `/api/forecast/latest` | 最新成功 run/alerts/data_state | viewer+ |
| GET `/api/forecast/horizon` | 单 target 0..2h/5min points | viewer+ |
| GET `/api/forecast/risk-ranking` | Top-N risk | viewer+ |
| GET `/api/forecast/alerts` | forecast alert 列表 | viewer+ |
| POST `/api/forecast/alerts/{id}/transitions` | acknowledge/resolve CAS | operator/admin |
| GET `/api/forecast/events` | 认证 SSE | viewer+ |

旧 `GET /api/alerts` 路径、字段和 legacy SSE 行为保持不变。告警中心并行读取 `GET /api/alerts` 与 `GET /api/forecast/alerts`，转换为 `UnifiedAlert{source: legacy|forecast, id, ...}`，按全局 UUID `id` 去重；forecast 不写入 `public.alerts`，也不把 legacy `alert_id` 直接当作 forecast id。

`latest`：

```json
{
  "schema_version": 1,
  "data_state": "ready",
  "network_id": "uuid",
  "nowcast_run": {
    "id": "uuid",
    "scheduled_for": "2026-09-24T10:00:00Z",
    "forecast_origin_at": "2026-09-24T10:00:00Z",
    "issued_at": "2026-09-24T10:02:10Z",
    "published_at": "2026-09-24T10:02:11Z",
    "simulation_id": "uuid"
  },
  "alerts": [],
  "age_seconds": 20,
  "server_time": "2026-09-24T10:02:30Z"
}
```

- `data_state=ready|stale|not_ready` 按 `FORECAST_FRESH_MAX_AGE_SECONDS=900` 和 `server_time-issued_at` 独立计算；`issued_at` 缺失、run 未成功或存在已逾期未成功新时隙均不得 ready。
- nowcast 关联 simulation 的业务 deadline 固定为 `scheduled_for+180 s`；普通 simulation 的 600 s attempt 上限不适用于 nowcast，late result 只留审计、不发布告警。deadline 与 freshness 是两个门禁。
- `forecast_origin_at` 固定 5 min horizon 网格；`lead_time_minutes=max(0,floor((expected_at-issued_at)/60s))`，不要求 lead 为 5 的倍数。
- horizon 固定 5 min grid，跨度 <=2 h；调度前 policy 工件缺失/失效时不创建 run，latest/horizon 返回 `not_ready` + `FORECAST_POLICY_UNAVAILABLE`；运行中失效时 run failed `FORECAST_POLICY_INVALID`。两者均不创建可发布 point。
- risk ranking 只返回 open/acknowledged、来源 run 未 stale；排序 `risk_score DESC, expected_at ASC, id ASC`；返回 score components 和 policy version。

Transition：

```json
{
  "action": "acknowledge",
  "reason": "已通知运维",
  "expected_version": 3
}
```

- `Idempotency-Key` 必需。
- version mismatch 409 `FORECAST_VERSION_CONFLICT`。
- 非法转换 409 `INVALID_FORECAST_TRANSITION`。
- forecast 不提供 `ignore` 伪状态。

### 7.6.1 Forecast 上线前安全门禁（顺序约束）

在 production 启用 forecast router/worker 之前，必须先完成并通过以下独立门禁；不得以 forecast 自身“尚未有数据”或前端隐藏按钮为由跳过：

1. `JWT_ALGORITHMS` 只允许锁定算法；验证签名、`exp/nbf/iat/sub/iss/aud`，未知/坏 role 不得抛 500。
2. production 禁止 fallback admin、仓库默认 JWT/DB/Redis/webhook secret；配置仍为 fallback 时 readiness/startup fail closed。
3. 缺/过期/坏 token=401，角色不足=403；viewer 不能 submit/cancel/transition/import/validate/seed，所有 forecast read/SSE 需认证。
4. network/revision/target ownership 做服务端 RBAC/资源范围校验，不能只隐藏按钮或信任 localStorage role。
5. 独立 rate-limit bucket：forecast read、transition、simulation submit、network job submit、SSE connect；429 必须含 `Retry-After`。SSE JWT 只在 Authorization header。
6. 负向测试覆盖 alg=none/HS-RS confusion、过期、错误 iss/aud、缺 role、viewer 写、跨 network id、burst limit 和日志 token 脱敏。

门禁 artifact 归属 G2，并由 `scripts/verify_hydraulic_stack.py --gate g2` 执行。未 PASS 时 production 必须不注册/不暴露 forecast endpoints，而不是返回 503 占位后继续宣称可用。

### 7.7 System endpoints

- `GET /health`：仅 liveness，进程可响应即 200。
- `GET /readyz`：检查 DB、schema head/profile、Timescale、PostGIS（按 profile）、outbox relay、worker registration、migration；失败 503。
- `GET /metrics`：Prometheus。

当前 `/health` 无条件 healthy（`backend/app/main.py:302-305`），实现 MUST 区分 liveness/readiness，不能把旧行为直接当 AC-8 证据。

## 8. Forecast 状态、抑制与 confidence

### 8.1 Forecast alert 唯一状态机

```text
status: open -> acknowledged -> resolved|expired
        open --------------> resolved|expired
phase:  predicted -> observed
```

| action | 允许 status | 允许 phase | 必需守卫 | 结果/事件 |
|---|---|---|---|---|
| create | 无 | predicted | policy 有效、非冷却、候选超阈 | `open/predicted` + `alert.created` |
| acknowledge | open | predicted/observed | operator/admin、reason | `acknowledged`，phase 不变 + `alert.updated` |
| observe | open/acknowledged | predicted | event=`external_verified`、auth/binding/quality good；`issued_at<=observed_at<=expected_at+expiry_grace`；按 rule operator 越过 threshold | phase=observed + `alert.observed` |
| refresh | open/acknowledged | predicted/observed | 四项绝对差至少一项 `>=` rule 对应 refresh delta | 始终更新值；命中任一项才 `alert.updated` |
| escalate | open/acknowledged | predicted/observed | level 只能升高 | `alert.escalated` |
| resolve | open/acknowledged | predicted/observed | 人工 reason；或 clear+debounce；或 N 个 fresh run no-forecast | `resolved` + `alert.resolved` |
| expire | open/acknowledged | predicted | `now>expected_at+expiry_grace` 或同 rule 过期条件 | `expired` + `alert.expired` |

- refresh 判定固定为：`abs(new_expected_at-previous_expected_at) >= step_minutes*refresh_expected_shift_steps` OR `abs(new_peak_value-previous_peak_value)>=refresh_peak_delta` OR `abs(new_confidence-previous_confidence)>=refresh_confidence_delta` OR `abs(new_risk_score-previous_risk_score)>=refresh_risk_delta`；所有 delta 来自同一 forecast_rule，运算符固定 `>=`。
- observe 只接受同一 target/metric/unit 且 `observed_at` 位于 `[issued_at, expected_at+expiry_grace]` 的 external_verified event；早于 issued_at 视为未来/泄漏并拒绝，晚于 grace 不改变 active/terminal alert，只可进入后续独立分析。
- 时间到达、acknowledge、refresh、risk 变化不产生 observed；observed 只由唯一 observe action 产生。
- resolved/expired 的 status/phase 都不可变且不 reopen；冷却外复发创建新 ID。observed 不能 expire，只能人工或合格 clear evidence resolve。
- `ignore` 非法。自动 action `actor_id=null` + stable reason code；人工 action 必须 actor/reason。
- transition 使用 `expected_version` CAS；alert 更新、transition audit、业务 event outbox 同事务。重复命令由 Idempotency-Key 处理。
- `POST /api/forecast/alerts/{id}/transitions` 只接受人工 `acknowledge|resolve`；`create/observe/refresh/escalate/expire` 是受版本化 rule 驱动的内部事务动作，客户端不能伪造。
- suppression key 不含 phase；predicted→observed 使用同一 alert。predicted 已终态后，冷却窗内 observed candidate 只计 receipt/suppressed，不建第二 episode。

### 8.2 Confidence

`confidence=P(metric threshold exceeded in expected_at five-minute bucket | data available at issued_at)`。

可接受 method：ensemble exceedance、calibrated residual、conformal、calibrated regression。每个 method 必须解析为 `hydraulic.probability_calibrations` 中不可变工件，保存 method、calibration/artifact ID+sha256、适用 lead/量级域、valid_from/valid_to、sample/reliability/Brier 证据；nowcast run 显式绑定。

- forecast threshold、clear/resolve/expiry/no-forecast/debounce/risk scale 必须来自同一 run 绑定的不可变 `forecast_rules` 工件；代码常量不能替代项目规则。
- method/rule 缺失、过期、out-of-domain、schema/hash 不符或非有限：调度前不创建 run（`FORECAST_POLICY_UNAVAILABLE`）；已创建 run 在 finalize 前失效则 `FORECAST_POLICY_INVALID` terminal failed。
- MUST NOT 用 AI detection confidence、solver success probability、固定 0.5 或单次确定性结果伪造 probability。
- 水力 RMSE/NSE calibration ID 与 probability calibration ID MUST 分开；前者不自动提供 reliability。

### 8.3 Risk v1

```text
severity = info:0.25, warning:0.60, critical:1.00
urgency = observed ? 1.0 : max(0, 1 - lead_time_minutes/120)
impact = clamp((metric_value-threshold_value)/risk_scale, 0, 1)
risk_score = round(100*(0.35*confidence + 0.30*severity
                        + 0.20*urgency + 0.15*impact), 2)
```

`risk_scale` 来自 versioned forecast rule。score 是处置优先级，不是概率/生命安全评级。

## 9. SSE 契约

### 9.1 Endpoint 与认证

- canonical endpoint：`GET /api/forecast/events?network_id=<uuid>`。
- 客户端使用 fetch streaming，`Authorization: Bearer <JWT>`；MUST NOT 把长期 JWT 放 query。
- v1 不实现 ticket 或 `/api/sse/hydraulic` 别名，避免两个认证/重放语义。
- legacy `GET /api/sse/events` 的 `connected/sensors/alerts/system` 行为保持不变，尽管其当前无认证/内存 replay：`backend/app/api/routes.py:393-413`。

### 9.2 Wire format

```text
id: 1842
event: forecast
retry: 3000
data: {"schema_version":1,"event_id":"uuid","event_type":"alert.created",...}
```

data：

```json
{
  "schema_version": 1,
  "event_id": "uuid",
  "sequence": 1842,
  "event_type": "alert.created",
  "occurred_at": "2026-09-24T10:02:11Z",
  "network_id": "uuid",
  "nowcast_run_id": "uuid",
  "simulation_id": "uuid",
  "alert": {
    "id": "uuid",
    "type": "flood_forecast",
    "status": "open",
    "phase": "predicted",
    "target": {"type": "node", "id": "uuid"},
    "expected_at": "2026-09-24T10:35:00Z",
    "lead_time_minutes": 35,
    "measurements": {"depth_m": 0.42, "fill_ratio": null},
    "confidence": 0.86,
    "confidence_method": "ensemble_exceedance_frequency",
    "risk_score": 78.4,
    "version": 1
  }
}
```

### 9.3 提交/replay/背压

1. alert state + transition + outbox 在一个 DB transaction 提交。
2. producer finalize transaction 先原子 commit alert/transition/outbox，并写 nowcast `issued_at/published_at`；relay 只选择 `event_outbox.published_at IS NULL` 的已提交行，Redis publish 成功后写该 event 自己的 `event_outbox.published_at`，MUST NOT 修改 `nowcast_runs.published_at`。
3. relay 崩溃窗口允许相同 event_id 重发；MUST NOT 创建第二业务行。
4. API 进程只消费 Redis 并 fan-out，MUST NOT producer direct broadcast + Redis echo 双发。
5. `Last-Event-ID` 按 sequence replay；有效范围 168 h。
6. cursor 过期：连接前 409 `SSE_CURSOR_EXPIRED`，客户端 REST resync。
7. queue 满/内部 gap：发送 `forecast.resync_required` 或关闭连接；MUST NOT静默跳过。
8. 客户端忽略低于当前 alert.version 的事件；重复 event_id 丢弃。
9. 未知 schema_version/缺字段：不写 store，记录 metric 并触发 resync。
10. heartbeat 每 15 s；连接可由 server 定期轮换，客户端用 cursor 重连。

### 9.4 事件触发

| event | 触发 |
|---|---|
| `alert.created` | 新 episode 首次创建 |
| `alert.updated` | expected_at/lead 至少一格、risk 档位或 >=5 分变化 |
| `alert.escalated` | warning→critical |
| `alert.observed` | 合格观测使 phase predicted→observed |
| `alert.resolved` | 条件清除/人工 resolve |
| `alert.expired` | predicted 超时/规则过期 |

单纯 confidence 小变化、updated_at 或低于阈值 refresh 不发事件。

## 10. Notification 契约

- delivery 状态：`pending→sending→delivered`；失败 `retry_wait→sending→dead_letter`。
- 计划：`t+0,+1m,+5m,+15m,+1h`，最多 5 次。
- SMTP 使用 async client/thread pool，不得在 event loop 直接调用同步 `smtplib`；当前问题证据：`backend/app/services/notification_service.py:27-57`。
- webhook 只允许 admin 配置的 HTTPS allowlist；校验 DNS/IP/redirect/response size；HMAC + event idempotency header。
- HTML title/description 自动转义。
- delivery 失败不回滚 alert/event outbox。
- 浏览器 notification 只是 UX；事实是 delivery row。

## 11. 前端契约

### 11.1 路由与权限

- 新路由 `/simulation`，viewer 只读；operator 可建/复制 rainfall、submit/cancel、forecast transition；admin 额外 delete/seed/config。
- 中央 `App.tsx` 抽出 authenticated layout，但后端 RBAC 仍是最终边界。当前 `ProtectedRoute` 只看 token：`frontend/src/App.tsx:17-24`。
- 新 UI 文案全部走 `simulation/rainfall/network/map/timeline/forecast/risk/comparison/errors` i18n namespace；中英文 key parity 是门禁。

### 11.2 Feature-local 状态

- 大几何/frame 不进入 legacy `useAppStore`（当前 store 每次读数会替换 Map：`frontend/src/store/useAppStore.ts:78-89`）。
- Zustand 只存 selection、network/revision、simulation/status/progress、timeline index/playing/speed、layer/filter、forecast snapshot。
- API error 必须保留 status/code/detail/errors/request_id/Retry-After；当前 Axios 把错误压成 `Error(message)`：`frontend/src/services/api.ts:26-39`。

### 11.3 地图

- Leaflet TileLayer 保留；水力几何使用一个命令式 custom Canvas `L.Layer`。
- 服务端 bbox/zoom 简化；客户端 viewport index/culling、zoom LOD、屏幕空间 hit test。
- React 不创建 10000 Marker；当前 Marker 架构证据：`frontend/src/components/map/MapVisualization.tsx:26-40,285-294`。
- LOD 默认：

| zoom | catchments | pipes | nodes | labels |
|---|---|---|---|---|
| <11 | outline/aggregate | major only | hidden | none |
| 11-13 | simplified fill | visible | hidden | none |
| 14-15 | visible | visible | circles | selected |
| >=16 | visible | visible | circles | selected+critical |

- fill ratio/depth 色带固定并配合线型/图标/文本，不能只靠颜色。
- predicted/observed：虚线/实线轮廓 + 文本/图标 + 可访问 feature list。

### 11.4 FrameCache

- key：`v1/{simulation_id}/{frame_index}/{fields_hash}/{network_revision_id}`。
- static geometry + dynamic frames 共享 256 MiB byte-bounded LRU。
- 数值转 `Float32Array`，flags `Uint8Array/Int8Array`，index `Uint32Array`。
- 最多 2 个 frame request 并发；当前帧 P0，next/previous P1；拖拽 abort 非当前。
- current A/B frame pin 住；logout/network switch/显式清理时销毁。
- 校验 count/finite/checksum 完整后才绘制；失败保留上一有效帧并显示 buffering。
- 410 停止播放；409 刷新 metadata；503 最多 0.5/1/2 s 重试；429 遵守 Retry-After。

### 11.5 时间轴

- canonical 1x=1 frame/s，2x=2 frames/s，4x=4 frames/s；5 min/frame。
- 24 h 在 4x 约 72 s。
- 不对水力状态插值；只在离散 frame 切换。
- `performance.now()` accumulator，不用固定 interval。
- 页面 hidden 暂停；返回后由用户恢复。
- 键盘：Space play/pause；Left/Right ±1；Shift ±10；Home/End。
- A/B 共用一个 frame index/clock；revision/time grid 不兼容时禁用同步播放。

### 11.6 A/B 与图表

- 单 Leaflet map，`A|B|Δ` 三模式，不建双地图。
- comparison 顶层必须返回 `compatible/network_revision_id/target_manifest_id/target_manifest_sha256/frame_grid/baseline/candidate/metrics`。
- baseline=0 时 relative=null；better_direction 由后端规则给。
- ECharts modular import/registry；observed 实线、predicted 虚线、uncertainty band、issue-time markLine。
- >500 点禁用通用动画；`prefers-reduced-motion` 禁装饰动画。
- resize `resize()`，卸载 `dispose()`；数据变化不 `setOption(option,true)` 全量替换。

### 11.7 SSE 客户端

- `useHydraulicForecastStream` 使用 fetch reader，不用原生 EventSource，以发送 Bearer。
- 单一 provider/connection；Dashboard、Simulation、AlertCenter 不各自建流。
- 指数退避 1/2/4...30 s；保留 cursor/event ID；gap/resync 调 latest/horizon。
- legacy `useSSE.ts` 保持旧域所有权，不能被水力 hook 直接复用业务 store。

## 12. 配置契约

Settings 的实现范围唯一引用 `DEP-AUTH-06`：下面 54 项是 v3 仿真/安全 Settings 的**授权字段集**，fixture 的 `settings.fields[]` 必须逐项且仅逐项覆盖它们；其余 47 项 legacy/安全/部署字段列入 `settings.unauthorized_fields[]`，每项固定 `authorized=false,preserve_only=true`。两组并集必须等于完整 101 项 `Settings.model_fields`，不得把非授权字段声称为本轮授权。本节不构成第二套路径授权。handoff 后 integration owner 接手其他 shared-file 变更，但必须按第 17.0 节保留该 scope：

```text
HYDRAULIC_SPATIAL_BACKEND=postgis|portable
HYDRAULIC_SCHEMA_REVISION=<head>
HYDRAULIC_IMPORT_ROOT=<path>
HYDRAULIC_SANDBOX_ROOT=<path>
HYDRAULIC_EXPORT_ROOT=<path>
SIM_GLOBAL_CONCURRENCY=2
SIM_NOWCAST_RESERVED_SLOTS=1  # Literal[1], fixed two-slot model
SIM_ATTEMPT_TIMEOUT_SECONDS=600
SIM_MAX_ATTEMPTS=3
SIM_RETRY_BACKOFF_SECONDS=10,60
SIM_LEASE_SECONDS=30
SIM_HEARTBEAT_SECONDS=10
SIM_CANCEL_POLL_SECONDS=1
SIM_INPUT_MAX_BYTES=20971520
SIM_WORKSPACE_MAX_BYTES=2147483648
SIMULATION_FRAME_MAX_BYTES=67108864
SIMULATION_OUTPUT_MAX_BYTES=8589934592
SIMULATION_RESULT_RETENTION_DAYS=90
SOLVER_DEFAULT_SELECTION=auto|explicit
EPA_SWMM_EXECUTABLE=<path or empty>
EPA_SWMM_VERSION=<locked version>
EPA_SWMM_SHA256=<64 hex or empty>
MAX_SUBSTEPS_PER_REPORT=20
COURANT_LIMIT=0.5
NOWCAST_INTERVAL_MINUTES=10
NOWCAST_HORIZON_MINUTES=120
NOWCAST_STEP_MINUTES=5
NOWCAST_DEADLINE_SECONDS=180
NOWCAST_MAX_RETRIES=2
NOWCAST_RETRY_BACKOFF_SECONDS=5,15
NOWCAST_RESERVED_SLOT_BORROWING=false
FORECAST_EVENT_WINDOW_MINUTES=30
FORECAST_FRESH_MAX_AGE_SECONDS=900
FORECAST_REPLAY_RETENTION_HOURS=168
FORECAST_SSE_QUEUE_SIZE=256
HYDRAULIC_ARTIFACT_ROOT=<path>
FORECAST_POLICY_CONFIG_PATH=<controlled local config>
NETWORK_JOB_GLOBAL_CONCURRENCY=2
NETWORK_JOB_ATTEMPT_TIMEOUT_SECONDS=600
NETWORK_JOB_LEASE_SECONDS=30
NETWORK_JOB_HEARTBEAT_SECONDS=10
NETWORK_JOB_MAX_ATTEMPTS=3
NETWORK_JOB_RETRY_BACKOFF_SECONDS=10,60
NETWORK_INPUT_MAX_BYTES=20971520
JWT_ALGORITHMS=RS256
JWT_ISSUER=<configured issuer>
JWT_AUDIENCE=<configured audience>
AUTH_FALLBACK_ADMIN_ENABLED=false
RATE_LIMIT_FORECAST_READ_PER_MINUTE=120
RATE_LIMIT_FORECAST_TRANSITION_PER_MINUTE=30
RATE_LIMIT_SIMULATION_SUBMIT_PER_MINUTE=20
RATE_LIMIT_NETWORK_JOB_SUBMIT_PER_MINUTE=10
RATE_LIMIT_SSE_CONNECT_PER_MINUTE=30
NOTIFICATION_MAX_ATTEMPTS=5
```

非授权 preserve-only 字段清单（47 项，fixture 中每项只允许 `name/authorized=false/preserve_only=true`）：

```text
APP_NAME
APP_VERSION
DEBUG
LOG_LEVEL
DATABASE_URL
DATABASE_URL_SYNC
DB_POOL_SIZE
DB_MAX_OVERFLOW
INFLUXDB_URL
INFLUXDB_TOKEN
INFLUXDB_ORG
INFLUXDB_BUCKET
REDIS_URL
REDIS_CHANNEL_ALERTS
REDIS_CHANNEL_SENSOR
REDIS_CHANNEL_MODEL
JWT_SECRET_KEY
JWT_PRIVATE_KEY
JWT_PUBLIC_KEY
JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES
MODEL_STORAGE_PATH
DEFAULT_MODEL_VERSION
MODEL_INFERENCE_DEVICE
MODEL_CONFIDENCE_THRESHOLD
MODEL_IOU_THRESHOLD
FFMPEG_PATH
FFPROBE_PATH
HLS_OUTPUT_DIR
SCREENSHOT_DIR
HLS_SEGMENT_TIME
HLS_LIST_SIZE
STREAM_HEARTBEAT_INTERVAL
CORS_ORIGINS
DEPLOYMENT_ENVIRONMENT
RATE_LIMIT_PER_MINUTE
MAX_UPLOAD_SIZE_MB
SMTP_ENABLED
SMTP_HOST
SMTP_PORT
SMTP_USE_TLS
SMTP_USERNAME
SMTP_PASSWORD
SMTP_FROM
WEBHOOK_ENABLED
ALERT_WEBHOOK_URLS
ALERT_EMAIL_RECIPIENTS
```

Settings 校验：horizon % step=0、deadline<=180、freshness >0 且独立配置、`SIM_GLOBAL_CONCURRENCY=2` 且 `SIM_NOWCAST_RESERVED_SLOTS=1`（均为 Literal，不做可配置大小比较）、`NOWCAST_RESERVED_SLOT_BORROWING=false`、三组 backoff 数组长度等于 max retries 且为严格递增正整数、input<=20 MiB、workspace/output/frame 有正上限、network job attempt-timeout/lease/heartbeat/max attempts 合法、EPA 显式模式必须有 path/version/hash。production 必须 `AUTH_FALLBACK_ADMIN_ENABLED=false`、JWT algorithms/issuer/audience 非空、所有 rate limit 为正；policy config 语法/schema/hash 非法则 forecast component not ready，引用 ID/hash/validity/domain 在调度时逐 network 校验。生产配置 MUST 拒绝仓库固定 secret/credential；当前弱默认证据：`backend/app/core/config.py:33-61,102-117`、`docker-compose.yml:13-18,35-37,53-62,80-89`。

## 13. 部署与运维契约

### 13.1 服务拓扑

目标 Compose 服务：

```text
postgres (postgis or portable profile, digest-pinned)
redis (auth, appendonly)
influxdb (existing sensor source)
migrate (one-shot alembic upgrade head)
backend (API; no create_all, no solver/parser/seed execution)
network-job-worker (DB claim; import/validate/seed; lease/fencing)
simulation-worker (DB claim; general slot)
nowcast-worker (DB claim; reserved slot + scheduler singleton)
outbox-relay (single logical publisher)
notification-worker (SMTP/webhook delivery)
nginx (API + fetch-SSE proxy)
frontend (production profile)
prometheus/grafana (monitoring profile)
```

- `migrate` 成功 `service_completed_successfully` 后 backend/worker 才启动。
- G2 安全门禁 PASS 是 forecast API/nowcast worker production enable 的前置条件；不能用 feature flag 跳过认证、RBAC 或限流实现。
- API 进程不执行长 simulation、import/validate/seed；worker 可独立重启/崩溃恢复。
- scheduler 使用 PostgreSQL advisory lock 保证 singleton。
- outbox relay 可多实例竞争 advisory lock，但同一 sequence 只由一个逻辑 relay 发布。

### 13.2 数据库 profile

- `postgis` 镜像必须同时含 PostgreSQL/Timescale/PostGIS，并按 digest 固定；禁止 `latest`。
- `portable` profile 使用 PostgreSQL + TimescaleDB，但不安装/不依赖 PostGIS；不能运行 PostGIS-only DDL。vanilla PostgreSQL（无 Timescale）不是本产品部署 profile。
- 0001 不重写；新增线性 migration：

```text
0002_reconcile_existing_schema
0003_hydraulic_network_core
0004_hydraulic_postgis_variant
0005_hydraulic_rainfall_and_jobs
0006_hydraulic_frames_and_observations
0007_hydraulic_forecast_and_outbox
0008_hydraulic_indexes_retention_seed
```

- profile-specific DDL 通过 Settings/环境显式分支，revision graph 保持单 head。
- init SQL 只 `CREATE EXTENSION`（uuid-ossp、Timescale；PostGIS profile 再 PostGIS），不 CREATE TABLE/hypertable/seed。
- `downgrade` 仅在空库/clone 测试；有 hydraulic 数据时显式拒绝，不能静默删除。

### 13.3 Liveness/readiness

- `/health` 只 liveness。
- `/readyz` 检查：DB SELECT 1、alembic head、schema profile、Timescale extension/hypertable、PostGIS（按 profile）、network-job-worker registration、artifact config/hash、outbox relay heartbeat、simulation/nowcast worker registration、migration state。
- 任一必要依赖失败 503；水力 API 禁止返回 mock healthy。
- worker `/healthz` 为进程存活；`/readyz` 为能 claim/heartbeat/连接 DB。

### 13.4 SSE proxy

Nginx 对 `/api/forecast/events`：

```nginx
proxy_http_version 1.1;
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 1h;
proxy_set_header Connection "";
proxy_set_header Authorization $http_authorization;
```

- access log 对 Authorization/query 中潜在 secret 脱敏。
- production 前端 nginx 同样关闭 buffering；当前 dev Vite 只对 `/sse/` 设置 keep-alive：`frontend/vite.config.ts:24-39`。

### 13.5 EPA/Windows

- Windows 纯 Python solver 必须可运行，不依赖 Linux-only native kernel。
- EPA engine 能力通过 `probe()`；Windows 显式路径/version/hash；Compose 默认可使用已验证 Python adapter。
- EPA 许可/二进制/版本/哈希进入 image manifest 或受控 volume；不得下载未校验 latest。
- Windows 取消/超时使用 Job Object；POSIX 使用 process group；两者都要无孤儿进程测试。

### 13.6 Secret 与端口

- DB/Redis/Influx/JWT/webhook secret 不在 Settings/Compose 硬编码；使用 env/secret file，启动时拒绝仓库默认值。
- PostgreSQL/Redis/Influx 默认不映射宿主机；演示 profile 可显式 opt-in。
- 镜像按 digest 固定；记录 commit/image digest/Alembic/extension/solver version。

### 13.7 回滚/备份

发布顺序：expand schema → deploy compatible app/worker → enable readiness/solver/nowcast → verify → later contract。生产回滚：暂停 nowcast →停止新 simulation →完成/取消当前 attempt →恢复旧 image →验证 readiness/旧 API/SSE →恢复 scheduler。

备份 manifest 至少含：commit、image digests、Alembic revision、schema profile、extension versions、solver/engine hash、PostgreSQL custom dump、Influx backup、Redis snapshot、SHA-256。恢复后必须验证 schema manifest、行数/hash、topology summary、API/SSE smoke；现有脚本仅能参考，当前无真实恢复证据：`scripts/backup.sh:15-46`、`scripts/backup.ps1:12-30`、`scripts/restore.sh:20-48`。

## 14. 可观测性

### 14.1 Metrics

```text
simulation_queue_depth
simulation_oldest_queued_seconds
simulation_duration_seconds{solver,status}
simulation_failures_total{error_code}
simulation_retries_total{reason}
simulation_cancelled_total
simulation_timeout_total
simulation_active_slots{lane}
solver_fallback_total{from,to}
simulation_frames_total
forecast_nowcast_duration_seconds{status,solver}
forecast_nowcast_to_alert_seconds
forecast_runs_total{terminal_status}
forecast_alerts_created_total{type,level}
forecast_alert_candidates_suppressed_total{type,reason}
forecast_active_alerts{type,level}
hydraulic_event_publish_lag_seconds
hydraulic_event_outbox_backlog
forecast_sse_queue_overflow_total
notification_delivery_total{channel,status}
hydraulic_cleanup_pending_total
```

Prometheus label MUST NOT 使用 simulation_id/alert_id/event_id 等高基数 ID；这些放结构化日志。

### 14.2 日志

每条适用日志包含：`request_id, user_id, network_id, network_revision_id, rainfall_scenario_id, simulation_id, attempt, solver_id/version, nowcast_run_id, alert_id, event_id, aggregate_version, phase, error_code`。禁止 secret、JWT、sandbox 绝对路径、完整输入内容。

## 15. 文件所有权契约

> 并行模块只能修改自己的 exclusive paths。需要共享文件时停止并交给 integration phase；不得用“顺手修复”覆盖其他模块。

### 15.0 唯一 owner 与共享文件规则

- 15.1～15.6 每行 MUST 恰好一个 path pattern 和一个 owner ID；禁止一格多路径、逗号列表、`..`、brace expansion、owner 注释或“legacy owner/integration”复合值。允许单个 `*`/`**` pattern，但必须 repo-root-relative、不得含省略前缀。
- 匹配规则唯一：exact path 优先于 glob；多个 glob 同时匹配时取 literal prefix 最长者；同深度仍重叠即 G0 FAIL。每个 tracked/untracked candidate path 最多命中一行。
- `check_contract_ownership.py` 必须按上述算法展开实际文件并输出 unmatched/overlap/duplicate；只对反引号字符串去重不算通过。
§15.1～15.6 的 owner 表定义 dependency-config 的**全周期修改权**；下表是唯一的 dependency-config 阶段授权注册表，`full-cycle owner` 列只用于与 §15.1 交叉校验，不构成第二套 owner 来源。PRD、§0.3、§12、§16、§17、§18 和发布阻断 MUST 引用本表 `DEP-AUTH-*` ID，禁止另列授权路径或 phase 副本；任一表间 owner 不一致即 G0 FAIL。

| auth_id | path pattern | full-cycle owner | DEP-PREP allowed | DEP-LANDING allowed（R0 PASS 后） | 最小 scope |
|---|---|---|---:|---:|---|
| `DEP-AUTH-01` | `.python-version` | dependency-config | true | true | Python 3.11 |
| `DEP-AUTH-02` | `.env.example` | dependency-config | true | true | v3 变量，无 secret |
| `DEP-AUTH-03` | `backend/requirements*.txt` | dependency-config | true | true | 仿真依赖 |
| `DEP-AUTH-04` | `backend/requirements*.lock` | dependency-config | true | true | 锁定依赖/hash |
| `DEP-AUTH-05` | `backend/pyproject.toml` | dependency-config | true | true | Python 3.11/extras |
| `DEP-AUTH-06` | `backend/app/core/config.py` | dependency-config | true | true | v3 仿真/安全 Settings、默认值、validators |
| `DEP-AUTH-07` | `backend/tests/test_simulation_settings.py` | dependency-config | true | true | 仅验证 DEP-AUTH-06 |
| `DEP-AUTH-08` | `backend/tests/test_requirements_matrix.py` | dependency-config | true | true | 仅验证 DEP-AUTH-03..05 |
| `DEP-AUTH-09` | `frontend/.node-version` | dependency-config | true | true | 18.20.8 |
| `DEP-AUTH-10` | `frontend/.npmrc` | dependency-config | true | true | npm 10.8.2、engine-strict 与可复现安装 |
| `DEP-AUTH-11` | `frontend/package.json` | dependency-config | true | true | Node 18.20.8/npm 10.8.2 + 唯一 `test:perf:twin` script |
| `DEP-AUTH-12` | `frontend/package-lock.json` | dependency-config | true | true | npm 10.8.2、lockfileVersion=3、根 package metadata 一致 |
| `DEP-AUTH-13` | `artifacts/contracts/dependency-config-handoff.json` | dependency-config | false | true | handoff artifact |

- §15.1～15.6 的 owner 表定义全周期修改权；本表 `DEP-PREP allowed` 定义 R0 PASS 前允许出现在工作树的**唯一子集**。两者不混用：owner=dependency-config 但 prep=false 的路径当前不得改；不在本表的路径即使被 owner checker 判给 dependency-config，也不得作为 DEP-PREP 落地。
- DEP-LANDING 只能在 R0 PASS 后执行，并可使用 prep=true 的 prepared diff与生成 DEP-AUTH-13。handoff 后配置/依赖路径转交 integration；integration 必须按第 17.0 节断言保留，任何语义变化重新 blockedBy dependency-config。
- `--phase` 的完整且唯一词汇是 `current|post-handoff`；缺失、空值或任何其他值必须 argparse/error exit 非零，MUST NOT 猜测默认值。
- `--phase current`：只允许无 handoff 的 R0 准备态；输出 §15 base owner table、每个 candidate 的匹配结果和 `DEP-AUTH-01..12` 的 prep allowlist 判定，R0 FAIL 期间 `DEP-AUTH-13` 不得出现在 dependency diff。unmatched/overlap/duplicate 或 prep=false 变更均非零。
- `--phase post-handoff`：必须有 handoff PASS；输出唯一 effective owner table，其中 `DEP-AUTH-01..06,09..12` 为 `integration-config`，`DEP-AUTH-07..08` 与 `DEP-AUTH-13` 仍为 `dependency-config`。missing/duplicate/transfer mismatch/保留谓词失败均非零；G0 固定使用 `post-handoff`。
- 第 2 轮复审时 `implementation/dependency-contract.md` 中“owner 只拥有 requirements/pyproject”“config.py 需 integration”及失效的 IC 行号引用属于 stale evidence；本轮摘要已改为只引用本注册表。若后续文本重新出现这些断言或另列授权子集，同样按 stale evidence 阻断 handoff。`test_security_tokens.py`/`test_api/test_auth.py` 属 security-gate，不在 DEP-AUTH。
- hydraulic-schema 独占全部 hydraulic ORM/Alembic migration；R0 PASS 前 create/edit/rename 一律禁止。领域 owner 只提交 typed handoff/DDL request。

### 15.1 依赖与配置

| 路径 | 唯一 owner | 禁止 |
|---|---|---|
| `.python-version` | dependency-config | 非 Python 3.11 目标 |
| `.env.example` | dependency-config | 非 v3 变量、真实 secret |
| `backend/requirements*.txt` | dependency-config | 未锁版本 native solver |
| `backend/requirements*.lock` | dependency-config | 手改 lock/hash |
| `backend/pyproject.toml` | dependency-config | Python 版本/extras 越权 |
| `backend/app/core/config.py` | dependency-config | 非 v3 仿真/安全 Settings、业务公式/DB 查询；handoff 后删除/改名/改默认值或 validator |
| `frontend/.node-version` | dependency-config | 非 18.20.8 |
| `frontend/.npmrc` | dependency-config | 非 npm 10.8.2/engine-strict/完整性策略 |
| `frontend/package.json` | dependency-config | Node 非 18.20.8、npm 非 10.8.2；`test:perf:twin` 以外新增/改写 script |
| `frontend/package-lock.json` | dependency-config | lockfileVersion 非 3、根 package metadata 不一致或非 npm 10.8.2 可复现结果 |

### 15.2 Backend schema/domain

| 路径 | 唯一 owner | 禁止 |
|---|---|---|
| `backend/app/models/hydraulic_models.py` | hydraulic-schema | route/parser/solver |
| `backend/app/models/__init__.py` | hydraulic-schema | DDL |
| `backend/app/schemas/hydraulic_schemas.py` | hydraulic-schema | FastAPI object dependency |
| `backend/app/schemas/forecast_schemas.py` | forecast | 修改 legacy Alert schema |
| `backend/app/domain/hydraulic/network_snapshot.py` | hydraulic-schema | ORM/任意 properties 穿透 |
| `backend/app/domain/hydraulic/target_manifest.py` | hydraulic-schema | frame/API 各自重排 |
| `backend/app/domain/hydraulic/durable_job_state.py` | task-sandbox | 复制四套状态机 |
| `backend/app/services/hydraulic/network_jobs.py` | network-job-worker | API BackgroundTasks/无 lease 执行 |
| `backend/app/workers/network_job_worker.py` | network-job-worker | solver/forecast 公式 |
| `backend/app/services/hydraulic/forecast_artifacts.py` | forecast | 运行时下载未锁定工件 |
| `backend/app/services/hydraulic/geometry.py` | geo-spatial | DB/network access |
| `backend/app/services/hydraulic/repositories.py` | hydraulic-schema | HTTP/SSE |
| `backend/app/services/hydraulic/importers/**` | swmm-io | 写用户路径/执行 solver |
| `backend/app/services/hydraulic/exporters/**` | swmm-io | 直接 mutable ORM 拼字符串 |
| `backend/app/services/hydraulic/topology.py` | topology-seed | 几何最近点替代拓扑 |
| `backend/app/services/hydraulic/seed.py` | topology-seed | 自动确认设备绑定 |
| `backend/app/services/hydraulic/rainfall.py` | rainfall | mock generator 复用为事实源 |
| `backend/alembic/versions/0002_*.py` | hydraulic-schema | 修改 0001/多 head |
| `backend/alembic/versions/0003_*.py` | hydraulic-schema | 修改 0001/多 head |
| `backend/alembic/versions/0004_*.py` | hydraulic-schema | 修改 0001/多 head |
| `backend/alembic/versions/0005_*.py` | hydraulic-schema | 修改 0001/多 head |
| `backend/alembic/versions/0006_*.py` | hydraulic-schema | 修改 0001/多 head |
| `backend/alembic/versions/0007_*.py` | hydraulic-schema | 修改 0001/多 head |
| `backend/alembic/versions/0008_*.py` | hydraulic-schema | 修改 0001/多 head |

### 15.3 Solver/task/results

| 路径 | 唯一 owner | 禁止 |
|---|---|---|
| `backend/app/services/hydraulic/solvers/base.py` | solver-contract | 具体数值法 |
| `backend/app/services/hydraulic/solvers/registry.py` | solver-contract | 运行中静默 fallback |
| `backend/app/services/hydraulic/solvers/swmm_adapter.py` | solver-contract | API/JWT/ORM |
| `backend/app/services/hydraulic/solvers/python_mw.py` | fallback-hydraulics | DB/network |
| `backend/app/services/hydraulic/solvers/python_dw.py` | fallback-hydraulics | 冒充 swmm_dw |
| `backend/app/services/hydraulic/surface_flood.py` | surface-flood | 宣称二维地表水动力 |
| `backend/app/services/hydraulic/tasks.py` | task-sandbox | solver 数值实现 |
| `backend/app/services/hydraulic/sandbox.py` | task-sandbox | API 返回绝对路径 |
| `backend/app/services/hydraulic/initial_state.py` | simulation-input | 修改 network revision hash；使用未绑定 observation |
| `backend/app/services/hydraulic/results.py` | result-calibration | 告警发布 |
| `backend/app/services/hydraulic/calibration.py` | result-calibration | 修改原始观测/mock AC-5 |
| `backend/app/services/hydraulic/metrics.py` | hydraulic-metrics | 高基数 ID label |
| `backend/app/workers/simulation_worker.py` | task-sandbox | API schema/直接广播 |
| `backend/app/workers/nowcast_worker.py` | forecast | 复制 durable_job_state |

### 15.4 Forecast/events/API

| 路径 | 唯一 owner | 禁止 |
|---|---|---|
| `backend/app/services/hydraulic/nowcast.py` | forecast | direct SSE |
| `backend/app/services/hydraulic/forecast_alerts.py` | forecast-alerts | 修改 legacy Alert |
| `backend/app/services/hydraulic/risk.py` | forecast-alerts | HTTP/SSE |
| `backend/app/services/hydraulic/event_publisher.py` | forecast-events | 创建 alert/direct+Redis 双发 |
| `backend/app/services/hydraulic/notification_delivery.py` | forecast-events | DB transaction 中等待网络 |
| `backend/app/workers/outbox_relay.py` | forecast-events | 进程内 replay 事实源 |
| `backend/app/workers/notification_worker.py` | forecast-events | 同步阻塞 event loop |
| `backend/app/api/hydraulic_routes.py` | hydraulic-api | parser/数值算法/SQL 拼接 |
| `backend/app/main.py` | backend-integration | materialization/forecast 公式 |
| `backend/app/api/routes.py` | backend-integration | 新 forecast 业务逻辑；legacy route 改动须 characterization gate |
| `backend/app/core/security.py` | security-gate | forecast 前绕过 issuer/audience/role/limit 门禁 |
| `backend/app/workers/inference_worker.py` | legacy-backend | 修改 forecast 状态/outbox |

### 15.5 Frontend

| 路径 | 唯一 owner | 禁止 |
|---|---|---|
| `frontend/src/features/simulation/model/**` | frontend-foundation | 组件逻辑 |
| `frontend/src/features/simulation/api/**` | frontend-foundation | 压扁结构化错误 |
| `frontend/src/features/simulation/store.ts` | frontend-foundation | frame 大数组/geometry |
| `frontend/src/features/simulation/frame/**` | simulation-map | Zustand persistence |
| `frontend/src/features/simulation/map/**` | simulation-map | 每 feature React Marker |
| `frontend/src/features/simulation/timeline/**` | timeline-player | 水力插值 |
| `frontend/src/features/simulation/rainfall/**` | rainfall-editor | 硬编码 validation |
| `frontend/src/features/simulation/tasks/**` | task-panel | SSE-only progress |
| `frontend/src/features/simulation/charts/**` | comparison-charts | 全量 ECharts import/重建 |
| `frontend/src/features/simulation/comparison/**` | comparison-charts | 浏览器重采样 |
| `frontend/src/features/simulation/forecast/**` | forecast-ui | 修改 legacy Alert 为 any |
| `frontend/src/features/simulation/realtime/**` | frontend-foundation | 原生 EventSource Bearer |
| `frontend/src/pages/SimulationPage.tsx` | frontend-integration | 持有几何/frame arrays |
| `frontend/src/App.tsx` | frontend-integration | 领域 DTO/frame decode |
| `frontend/src/components/layout/AppLayout.tsx` | frontend-integration | 领域 DTO/frame decode |
| `frontend/src/i18n/**` | frontend-integration | hardcoded key parity 绕过 |
| `frontend/src/hooks/useSSE.ts` | legacy-frontend | 新 forecast 状态注入 |
| `frontend/src/store/useAppStore.ts` | legacy-frontend | 新 forecast 状态注入 |

### 15.6 Platform/CI/docs

| 路径 | 唯一 owner | 禁止 |
|---|---|---|
| `docker-compose.yml` | integration-config | domain 业务逻辑 |
| `docker/hydraulic-gates/**` | ci-quality-bootstrap | 手改 gate result |
| `docker/init-db.sql` | database-bootstrap | 业务 schema/seed |
| `docker/postgres/**` | database-bootstrap | application migration |
| `docker/backend/**` | compose-worker | 修改应用业务 |
| `docker/nginx/**` | compose-worker | 修改 forecast 协议 |
| `docker/frontend/**` | compose-worker | 修改 bundle/source |
| `.github/workflows/**` | ci-quality | continue-on-error 绕过 gate |
| `scripts/verify_hydraulic_stack.py` | ci-quality-bootstrap | 修改 fixture 判据 |
| `scripts/check_contract_ownership.py` | ci-quality-bootstrap | 忽略 overlap/unmatched |
| `scripts/check_migration_gate.py` | ci-quality-bootstrap | 接受非 PASS review |
| `scripts/check_dependency_handoff.py` | ci-quality-bootstrap | 忽略保留字段/依赖 |
| `scripts/benchmark_hydraulic.py` | ci-quality | 降低 180s 门槛 |
| `scripts/benchmark_hydraulic_api.py` | ci-quality | 混合 excluded endpoint |
| `scripts/benchmark_hydraulic_nowcast.py` | ci-quality | 删除周期/放宽 180s |
| `backend/tests/fixtures/hydraulic/**` | acceptance-fixtures | 运行时生成 expected |
| `backend/tests/fixtures/performance/**` | acceptance-fixtures | 修改阈值 |
| `backend/tests/test_simulation_settings.py` | dependency-config | 非授权 Settings |
| `backend/tests/test_requirements_matrix.py` | dependency-config | 非授权依赖 |
| `backend/tests/test_security_tokens.py` | security-gate | 绕过 issuer/audience/role |
| `backend/tests/test_api/test_auth.py` | security-gate | fallback admin |
| `backend/app/security/hydraulic_rate_limits.py` | security-gate | 直接改 shared config |
| `backend/tests/security/test_forecast_prerequisites.py` | security-gate | 绕过 G2 |
| `frontend/performance/**` | frontend-acceptance | 伪造采样/改门槛 |
| `frontend/e2e/**` | frontend-acceptance | 绕过 RBAC/forecast E2E |
| `scripts/backup.sh` | operations | 未 hash manifest |
| `scripts/backup.ps1` | operations | 未 hash manifest |
| `scripts/restore.sh` | operations | 无恢复校验 |
| `docker/prometheus/**` | operations | 修改 gate 指标语义 |
| `docker/grafana/**` | operations | 修改 gate 指标语义 |
| `README.md` | delivery-docs | 宣称未通过 gate |
| `docs/hydraulic-twin/SOURCE_PRD.md` | delivery-docs | 改写不可变正文 |
| `docs/hydraulic-twin/PRD_SUPPLEMENT.md` | delivery-docs | 非技术负责人修改 |
| `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md` | delivery-docs | 非技术负责人修改 |
| `docs/hydraulic-twin/contracts/**` | delivery-docs | 恢复规范效力 |
| `docs/hydraulic-twin/recon/**` | recon-docs | 改写源码现状/伪造运行 |
| `docs/hydraulic-twin/implementation/hydraulic-schema.md` | delivery-docs | 宣称 migration 已实施 |
| `docs/hydraulic-twin/reviews/**` | r0-reviewer | 作者自审 PASS |
| `docs/hydraulic-twin/implementation/dependency-contract.md` | dependency-docs | 与 git diff 不一致的完成声明 |
| `artifacts/contracts/v3-independent-review.json` | r0-reviewer | implementation owner 写 verdict |
| `artifacts/contracts/dependency-config-handoff.json` | dependency-config | 删除授权 Settings/依赖 |

## 16. 迁移、并行和集成顺序

`blockedBy` 列只允许下列 work-item ID，逗号分隔；无前置固定写 `—`（解析为空集）。不得出现“已落地/所有实现/fixtures”等自由文本或人工别名。当前 R0 FAIL，因此除 `DOC-OWNERSHIP` 和 `DEP-PREP` 外全部 blocked。

本文不再维护第二份 ASCII/Mermaid 依赖图；下表是 blockedBy 的唯一规范图示，checker 必须从表中 26 个 work-item ID 重建有向图。图中不得出现 `domain`、`integration` 等集合别名或自然语言占位符；任何第二图示与表不一致均判 stale。

| work item ID | blockedBy（仅 ID） | 产物/限制 |
|---|---|---|
| `DOC-OWNERSHIP` | `—` | 本节 owner 矩阵；R0 输入 |
| `DEP-PREP` | `DOC-OWNERSHIP` | 只准备授权 diff，不 merge/landing |
| `R0-REVIEW` | `DOC-OWNERSHIP`,`DEP-PREP` | 独立 JSON；最新 round-3 `NOT_APPROVED / R0_FAIL` |
| `DEP-LANDING` | `R0-REVIEW` | 授权 diff 落地 + handoff |
| `SCHEMA-MIGRATIONS` | `R0-REVIEW` | ORM/migration；R0 前零变更 |
| `SECURITY-GATE` | `R0-REVIEW` | JWT/fallback/RBAC/rate-limit |
| `ARTIFACTS` | `R0-REVIEW` | calibration/rule/policy config |
| `GATE-BOOTSTRAP` | `R0-REVIEW` | verify/owner/migration/handoff checker；G0 前置 |
| `NETWORK-DOMAIN` | `SCHEMA-MIGRATIONS` | import/export/geometry/topology/rainfall |
| `SOLVER-DOMAIN` | `SCHEMA-MIGRATIONS` | solver/frame/quality/calibration |
| `OBSERVATION-DOMAIN` | `SCHEMA-MIGRATIONS`,`SECURITY-GATE` | ingest/trust/observation |
| `NETWORK-WORKER` | `SCHEMA-MIGRATIONS`,`NETWORK-DOMAIN` | durable network job |
| `SIM-WORKER` | `SCHEMA-MIGRATIONS`,`SOLVER-DOMAIN` | simulation attempt/frame |
| `FORECAST-NOWCAST` | `SCHEMA-MIGRATIONS`,`SIM-WORKER`,`OBSERVATION-DOMAIN`,`SECURITY-GATE`,`ARTIFACTS` | nowcast/alert/outbox/notification |
| `FRONTEND-FOUNDATION` | `SCHEMA-MIGRATIONS` | typed API/model/feature-local UI |
| `INTEGRATION` | `DEP-LANDING`,`NETWORK-WORKER`,`SIM-WORKER`,`FORECAST-NOWCAST`,`FRONTEND-FOUNDATION` | shared main/routes/App/compose；保留 handoff |
| `GATE-HARNESS` | `INTEGRATION` | G1～G6 fixtures/performance runner |
| `CI-QUALITY` | `GATE-HARNESS` | workflows/evidence aggregation |
| `G0` | `DEP-LANDING`,`GATE-BOOTSTRAP`,`INTEGRATION` | owner/R0/handoff + baseline |
| `G1` | `R0-REVIEW`,`DEP-LANDING`,`SCHEMA-MIGRATIONS`,`G0` | 两 profile migration |
| `G2` | `G0`,`SECURITY-GATE`,`NETWORK-DOMAIN`,`FORECAST-NOWCAST` | import/security |
| `G3` | `G0`,`GATE-HARNESS`,`NETWORK-WORKER`,`SIM-WORKER`,`FORECAST-NOWCAST` | 四类 durable job + worker/numeric/calibration report |
| `G4` | `G0`,`FORECAST-NOWCAST` | realtime/forecast |
| `G5` | `G0`,`GATE-HARNESS` | 四 performance harness |
| `G6` | `G0`,`GATE-HARNESS`,`INTEGRATION` | compose/rollback |
| `ACCEPTANCE` | `G0`,`G1`,`G2`,`G3`,`G4`,`G5`,`G6` | AC-1～AC-8 同轮汇总 |

执行顺序严格由 ID 边决定；不再使用自然语言先后关系。G0 所需 checker 属于 `GATE-BOOTSTRAP`，不是被 G0 阻断的 `CI-QUALITY`。

## 17. 验收与发布门禁

### 17.0 R0 v3 独立复审与 migration 状态门禁

当前状态：`R0_ROUND_3_FAILED / NOT_APPROVED / ALL_IMPLEMENTATION_WORK_BLOCKED`。R0 是独立于实现期 G0～G6/acceptance 的**设计状态门禁**：它审本文档的 schema/semantics、owner、blockedBy、handoff 谓词和 gate 定义，不执行或替代实现门禁。最新 round-3 确认 §3.1～§3.8、B-01～B-07 与上一轮 §4 PASS 项保持，并发现 C-01～C-04；本次整改后的 bundle 尚未独立复审，当前缺失的 gate/checker/harness 无论设计上是否已定义都保持 **NOT RUN**，不得写成 PASS。

- 唯一 artifact：`artifacts/contracts/v3-independent-review.json`，owner=`r0-reviewer`；Markdown review 只作人类证据，不能替代 JSON。
- JSON 必含 `verdict`、`reviewer`、`reviewed_at`、`base_commit`、`document_bundle_sha256`、`documents:[{path,sha256}]`、五项冻结条件逐项 PASS/FAIL、findings。`reviewed_commit` 删除；docs 未跟踪时由内容 bundle 绑定。
- bundle canonical input 固定为 `{"schema":"v3-review-bundle-v1","base_commit":"...","documents":[{"path":"docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md","sha256":"..."},{"path":"docs/hydraulic-twin/PRD_SUPPLEMENT.md","sha256":"..."},{"path":"docs/hydraulic-twin/SOURCE_PRD.md","sha256":"..."}]}`（documents 按 path 排序）的 UTF-8 canonical JSON bytes，bundle hash=SHA-256(bytes)。任一文件变化使旧 verdict 失效。
- 最新 round-3 reviewer=`dynamic-workflow v3-schema-semantics-review-3`；人类证据路径为 `reviews/v3-schema-semantics-review-round-3.md` 与同步覆盖的 `reviews/v3-schema-semantics-review.md`，verdict=`NOT_APPROVED / R0_FAIL`。候选 PRD/实施契约不得内嵌历史 review 的文件 hash、输入 hash 或 document bundle；C-01～C-04 整改后的 bundle 只由下一轮 reviewer 从 live files 重算，并且只有最终批准 artifact 可以记录该当轮可重算 bundle。Markdown 仍不等同 R0 JSON artifact。
- R0 PASS 只按第 16 节边解锁以 `R0-REVIEW` 为直接前置的 work item（包括 `DEP-LANDING`、`SCHEMA-MIGRATIONS`、`SECURITY-GATE`、`ARTIFACTS`、`GATE-BOOTSTRAP`），不等价于 G0～G6 任一 PASS；PASS 前 migration 目录必须与 base commit 完全一致，dependency patch 只能 preparation。
- dependency handoff 固定为 `artifacts/contracts/dependency-config-handoff.json`，owner=`dependency-config`；`auth_ids` 必须恰好为 `DEP-AUTH-01..13`，`files` 必须按 `DEP-AUTH-01..12` 记录 base/result SHA-256。`DEP-AUTH-13` 是该 JSON 自身，只做路径/schema/auth_ids 校验，hash 由 G0 evidence 外部记录。handoff 后配置/依赖路径转交 integration，但必须通过下述断言。
- handoff 期望值唯一来自 `backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json`；checker 同时读取实际文件和 handoff，任一不相等即非零：
  1. 版本文本只接受精确值：`.python-version` trim 后等于 `3.11`；`frontend/.node-version` trim 后等于 `18.20.8`；`.npmrc` 解析后的有效 `engine-strict` 恰好一个且为 `true`。`package.json.engines.node` 必须精确等于字符串 `18.20.8`，`engines.npm` 精确等于字符串 `10.8.2`，`packageManager` 精确等于 `npm@10.8.2`；semver 区间、一处或多处版本声明均失败。G0 runtime evidence 还必须分别记录 `node --version == v18.20.8`、`npm --version == 10.8.2`。
  2. `package.json.overrides.rollup` 精确等于 `4.22.0`；`package.json.scripts.test:perf:twin` 精确等于 `node performance/run-twin-map.mjs`。其他 scripts 相对 base 不得由 dependency-config 改动；缺少或改名 `test:perf:twin` 均失败。
  3. `package-lock.json.lockfileVersion` 必须是 JSON number `3`；`packages[""]` 的 dependencies/devDependencies/engines 与 `package.json` 对应 canonical 值逐项完全相等，缺 key、多 key 或值不同均失败。
  4. `backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json` 的 `settings.fields[]` **只**包含第 12 节 54 个授权字段，必须按名称排序、唯一、逐项 `authorized=true`，名称集合与第 12 节授权 registry 完全相等。`settings.unauthorized_fields[]` 单独包含其余 47 个字段，必须按名称排序、唯一、逐项 `authorized=false,preserve_only=true`，名称集合精确等于第 12 节 preserve-only 清单。checker 必须断言两组不相交，`fields.names ∪ unauthorized_fields.names == Settings.model_fields keys == .env.example keys`，两数组大小=`54/47`、无重复/别名/遗漏。
  5. 对 54 个 `settings.fields[]`，fixture/handoff 逐项包含 canonical `type/default/constraints/validators/env_example/invalid_cases`；type 与 `Settings.model_fields`、default 与模型 default、validators 与字段/模型 decorator token、env_example 与 `.env.example` 规范化值必须逐项相等，invalid cases 全部抛出 `ValidationError`。`constraints` key 和 `<field-or-model>:<before|after|wrap>:<function-name>` validators 去重后按 Unicode code point 排序，model validator 用 `<model>:model:<function-name>`，禁止依赖 `repr()`。47 个 `settings.unauthorized_fields[]` 只允许 `name/authorized/preserve_only`，checker 只验证名称存在和非授权标记，handoff MUST NOT 将其列入本轮授权变更；任一授权字段缺失/重复/数量不符/alias/默认值/constraint/validator 差异，或非授权字段缺失/被标 authorized，均失败。
  6. `simulations.idempotency_record_id` 与 `calibration_runs.idempotency_record_id` 必须是 `UUID NOT NULL FK -> idempotency_records.id`，两表都必须有且只允许一个 `UNIQUE(idempotency_record_id)`；`idempotency_key` 列和永久 `UNIQUE(requested_by,idempotency_key)` 必须不存在。simulation cancel 另以 `scope=simulation.cancel` record 保存 resource/response，不新增第二个 simulations FK 列。G2 fixture 必须证明 24h 后同 key 的新 generation 可分别创建新 simulation 与 calibration resource。
  7. handoff `auth_ids` 必须精确为 `DEP-AUTH-01..13` 且无重复；`files` 的 key 必须精确为可落盘的 `DEP-AUTH-01..12`。每个 result SHA-256 等于实际文件 hash，base SHA-256 等于 `DEP-PREP` 开始时记录。`DEP-AUTH-13` 是 handoff JSON 自身，只校验固定路径、schema 与 `auth_ids` 完整性，其 SHA-256 由 G0 evidence 在文件关闭后外部记录，禁止在文件内写自身 hash 形成自引用。`DEP-AUTH-13` 不得出现在 PREP 阶段。

状态检查命令（脚本当前不存在）：

```bash
python scripts/check_migration_gate.py --review-artifact artifacts/contracts/v3-independent-review.json --base-ref ca99e0485fb12fbecbd565e79d81f745f8d4a748 --versions-dir backend/alembic/versions
python scripts/check_dependency_handoff.py --handoff artifacts/contracts/dependency-config-handoff.json --expected backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json --python-version-file .python-version --node-version-file frontend/.node-version --npmrc frontend/.npmrc --package frontend/package.json --lock frontend/package-lock.json --env-example .env.example
```

- artifact 缺失、verdict 非 PASS、bundle/hash 不匹配或 base→candidate migration 有变化时，第一条非零；全部 landing/G1 blocked。
- R0 PASS 但 handoff/Node/npm/lock/perf script/Settings 保留不符时，第二条非零，G0/G1 不得 PASS。
- untracked docs 允许按 bundle hash 复审，但不得把 untracked 当作实现 commit；migration 禁止性单独由 git diff/checker 保证。

### 17.1 冻结 gate entrypoint

所有命令从仓库根执行，先设置一个实际 run ID；entrypoint 必须真实启动/清理测试依赖、传播子进程退出码并写 machine-readable evidence。禁止在文档中继续维护易漂移的分散命令。

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

必交 fixture registry（路径固定，manifest 必须含 SHA-256/schema/环境；缺文件即对应 gate NOT RUN）：

| Gate | 固定 fixture |
|---|---|
| G0 | `backend/tests/fixtures/hydraulic/contracts/openapi-v1.json`、`backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json`、`frontend/e2e/fixtures/api-v1/*.json` |
| G1 | `backend/tests/fixtures/hydraulic/schema/manifest-v1.json`（postgis/portable 期望清单） |
| G2 | `backend/tests/fixtures/hydraulic/network_jobs/v1/manifest.json`、`backend/tests/fixtures/hydraulic/contracts/idempotency-ttl-v1.json`、`backend/tests/fixtures/hydraulic/security/jwt-rbac-ratelimit-v1.json` |
| G3 | `backend/tests/fixtures/hydraulic/workers/lifecycle-v1.json`、`backend/tests/fixtures/hydraulic/calibration/durable-lifecycle-v1.json`、`backend/tests/fixtures/hydraulic/hydraulics/conservation-stability-v1.json` |
| G4 | `backend/tests/fixtures/hydraulic/forecast/v1/manifest.json`、`backend/tests/fixtures/hydraulic/realtime/outbox-sse-v1.json` |
| G5 | 第 17.1.1 节四个 performance/browser fixture |
| G6 | `docker/hydraulic-gates/compose-v1.lock.json`、`backend/tests/fixtures/hydraulic/compose/restore-manifest-v1.json` |

`verify_hydraulic_stack.py` 的 gate/子命令语义：

- **G0**：先验证 R0 PASS、运行 `python scripts/check_contract_ownership.py --contract docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md --phase post-handoff` 证明 effective owner table 每个 path 恰一个 owner，并运行 dependency-handoff checker证明 integration 保留授权 Settings/Node 依赖；再运行 Python 3.11 全量 pytest/Black/isort/Flake8/mypy（不可 continue-on-error）/backend lifespan characterization，以及 Node 18.20.8/npm 10.8.2 下 `npm ci`/Vitest/lint/format/build。每项独立 exit code/log/hash。
- **G1**：必须验证 R0 PASS、dependency handoff、base_commit 和 document_bundle_sha256；postgis/portable 各自从空 volume 执行 Alembic `upgrade head → current → repeat upgrade → clone downgrade → upgrade head`，核对 extension/table/index/hypertable/retention/profile manifest；四张 durable job 表的 `CHECK ((status='retry_wait') = (next_attempt_at IS NOT NULL))` 与 retry metadata CHECK（retry_wait 时 backoff>0/error 非空，非 retry 时二者 NULL）必须进入 schema manifest，并对两类约束做正反例测试；portable 仍含 TimescaleDB、不含 PostGIS。R0 非 PASS 时 G1 固定 NOT RUN。
- **G2**：deterministic network jobs + golden/恶意矩阵 + UNSAFE_PATH=422 + 三 job Idempotency-Key + `idempotency-ttl-v1.json`（network-job/simulation/calibration 未到期 replay/conflict、expiry generation、新 resource row、并发 active uniqueness、FK retention）+ JWT/fallback/RBAC/rate-limit 负向测试；未 PASS 禁止启用 forecast。
- **G3**：四类 durable job（simulation/network-job/nowcast/calibration）的 `next_attempt_at` due claim、cancel、general 600 s/nowcast 180 s、最多两次 retry、backoff、lease/fencing、terminal/code 配对与 slot 释放均须通过；`workers/lifecycle-v1.json` 必须逐行覆盖 10.2 的四类 retry surface、attempt=3 exhausted→failed 与 code 原样复制。simulation 另验 attempt frame registration/publish、2 slots 且 nowcast 禁借、quality/retention；calibration 必须消费 `calibration/durable-lifecycle-v1.json`，验证 dataset/split 固定、metrics+唯一 report artifact 原子 finalize、`RESULT_PERSIST_FAILED→retry_wait/failed`、`INSUFFICIENT_OBSERVATIONS→failed` 与 `SIMULATION_TIMEOUT+timed_out`。缺任一 lifecycle fixture/report 即 G3 FAIL。
- **G4**：完整 forecast transition table、policy config canonical hash+sidecar、probability/rule ID+hash 复合 FK、alert simulation/revision/manifest/target stable-key 绑定、producer nowcast published_at 与 relay event_outbox.published_at 分责、outbox single path、跨 worker/restart replay、gap/resync、30m suppression、delivery retry/DLQ；任一 hash/时序断言缺失即 FAIL。
- **G5**：必须调用第 17.1.1 节四个固定 harness；任一子项未运行/失败则 G5 FAIL。
- **G6**：必须分别执行 `postgis` 与 `portable` 两条命令并写入 `g6/postgis`、`g6/portable` 两个独立 evidence 目录；每个命令只负责其显式 profile，不得递归或隐式代替另一 profile。两者都从 fresh volume 完成 migration/worker/API/SSE、README demo、backup→destroy→restore hash/API、legacy characterization，并各自输出 profile manifest。acceptance 只有在两个 artifact 同 RUN_ID/commit 且均 exit 0 时通过 G6。
- **acceptance**：只消费同一 RUN_ID、同一 commit 的 G0～G6 artifacts，逐项校验 AC 映射和 hash，生成 AC-1～AC-8 JSON；不得用缺项 artifact 推断 PASS。

当前所有 gate/harness/fixture 入口均不存在，因此本轮 G0～G6、AC-1～AC-8 均为 **NOT RUN**。

#### 17.1.1 G5 可复现 harness

固定输入与资源：

- solver fixture：`backend/tests/fixtures/performance/solver/5k_10k_24h_v1/manifest.json`，含 network/rainfall/target-manifest/initial-state/solver config SHA-256；
- API fixture：`backend/tests/fixtures/performance/api/control-plane-v1.json`；
- nowcast fixture：`backend/tests/fixtures/performance/nowcast/30-cycle-v1.json`；
- browser fixture：`frontend/performance/fixtures/twin-map-5k-10k-v1.json`；
- 目标环境：4 physical cores/8 GiB/无 GPU，Python 3.11、Node 18.20.8/npm 10.8.2、锁定的 headless Chromium；线程池合计 4。

精确命令（由 G5 entrypoint 原样执行并记录）：

```bash
python scripts/benchmark_hydraulic.py --fixture backend/tests/fixtures/performance/solver/5k_10k_24h_v1/manifest.json --warmup 1 --repeats 3 --cpus 4 --memory 8g --threads 4 --output "artifacts/hydraulic/${RUN_ID}/g5/solver.json"
python scripts/benchmark_hydraulic_api.py --fixture backend/tests/fixtures/performance/api/control-plane-v1.json --warmup-seconds 60 --duration-seconds 600 --vu 20 --profile postgis --output "artifacts/hydraulic/${RUN_ID}/g5/api.json"
python scripts/benchmark_hydraulic_nowcast.py --fixture backend/tests/fixtures/performance/nowcast/30-cycle-v1.json --fake-cycles 100 --real-cycles 30 --profile postgis --output "artifacts/hydraulic/${RUN_ID}/g5/nowcast.json"
npm --prefix frontend run test:perf:twin -- --fixture frontend/performance/fixtures/twin-map-5k-10k-v1.json --duration-seconds 60 --output "artifacts/hydraulic/${RUN_ID}/g5/frontend.json"
```

判定：

- solver：预热 1 次、正式 3 次，每次 worker wall time `<=180s`，包含 frame serialization/persistence；质量失败即失败。
- API：固定权重 map-data 40%、single-frame 30%、latest 10%、risk-ranking 10%、status/list 5%、submit enqueue 3%、cancel ack 2%；600s/20 VU/60s warmup，纳入类别各自 P95 `<=500ms`、错误率 `<1%`。import/export/SSE 不混入。
- nowcast：100 个 fake slot + 30 个真实连续周期，policy 有效、issued/origin/published/outbox 完整；每周期 `scheduled_for→outbox<=180s`，P95 与 max 都 `<=180s`，缺周期失败。
- frontend：60s pan/zoom+4x playback+layer/selection；presented FPS `>=25`、frame-time P95 `<=50ms`、无 `>200ms` long task、cache `<=256MiB`。脚本、环境、sample count 和 JSON schema 缺一即失败。

### 17.2 Gates

| Gate | 必须通过 |
|---|---|
| G0 baseline | pytest/Vitest/lint/format/build/mypy/startup；旧 characterization |
| G1 schema | postgis+portable（均含 Timescale）空库 migration、idempotent upgrade、clone downgrade/upgrade、extension/index/retention |
| G2 import/security | durable network jobs、deterministic golden/恶意矩阵、idempotency 24h generation/retire/concurrency/FK retention、path/size/SSRF、JWT/fallback/RBAC/rate limit；forecast enable 前置 |
| G3 worker/numeric | 四类 durable job 的 due claim/cancel/timeout/retry/backoff/exhausted/lease/fencing/terminal+code；simulation attempt staging/publish、2 solver slots、质量/稳定/cleanup；calibration dataset/split、metrics+report artifact 与独立 lifecycle fixture |
| G4 realtime | policy config sidecar/hash、artifact ID+hash、alert revision/manifest/target、producer/relay published_at 分责、issued_at/freshness、outbox single path、跨 worker/restart replay、gap/resync、30m suppression、delivery retry/DLQ |
| G5 performance | 第 17.1.1 节四个固定 harness 全部 exit 0：solver/API/nowcast/frontend machine JSON 均达标 |
| G6 compose/rollback | postgis 与 portable 各自独立命令/evidence/fresh volume；两者均通过 README demo、backup/restore、old API/SSE zero regression，缺任一 profile 即 FAIL |

### 17.3 AC 证据

AC-1～AC-8 的判据和通用 artifact 字段见 `PRD_SUPPLEMENT.md:11`。每项状态只能为 `PASS/FAIL/NOT RUN/BLOCKED`，其中 `PASS` 必须附本轮目标命令 exit 0 和 artifact SHA-256。

当前状态：

| AC | 本轮状态 |
|---|---|
| AC-1 | NOT RUN（无实现） |
| AC-2 | NOT RUN（无实现） |
| AC-3 | NOT RUN（无 solver/worker/frame） |
| AC-4 | NOT RUN（无 forecast/SSE 实现） |
| AC-5 | BLOCKED（无真实 external observation/显式绑定）且 NOT RUN |
| AC-6 | NOT RUN（无 comparison） |
| AC-7 | NOT RUN；recon 报告基线不绿，但本轮未重跑 |
| AC-8 | NOT RUN；recon 报告 Docker daemon 不可用，本轮未启动 |

## 18. 发布阻断条件

以下任一项成立，MUST NOT 宣称水力孪生完成：

1. Alembic/init SQL/create_all 仍有多个 schema writer；
2. spatial profile 运行时可静默切换；
3. revision 可原地修改或历史 simulation 不固定 revision；
4. mock/synthetic 可满足 AC-5/正式 confidence；
5. solver 运行中静默换 engine；
6. py_mw 180s、质量或确定性未在固定 4 核实际通过；
7. task 无 durable lease/fencing/幂等/600s/两次 retry/cleanup；
8. nowcast 无独占 reserved capacity、general 借用 reserved slot，或 3 分钟 SLO 只在空队列证明；
9. SSE 无 durable sequence/replay/gap 语义，或 producer 双发布；
10. 30 分钟抑制不是数据库行锁/唯一约束；
11. notification 失败回滚 alert 或阻塞 nowcast；
12. API/frame 一次返回无界全量数据；
13. 前端 10000 feature 使用 DOM Marker 或 frame 进入全局响应式 store；
14. 25fps/P95/API/nowcast 没有 machine-readable artifact；
15. AC-1～AC-8 任一没有明确 PASS/FAIL/NOT RUN/BLOCKED 与证据；
16. legacy API/SSE/告警/页面回归未通过 characterization；
17. solver 接收 ORM/任意 properties，而非版本化强类型 NetworkSnapshot/PondCell/boundary/initial state；
18. map/frame/forecast 各自重排 feature index，同一 revision/schema 存在多个 target-manifest hash，或 simulation/attempt/frame 未以复合 FK 固定 target manifest ID+hash；
19. frame 直接按 simulation 唯一、retry 会暴露旧 attempt，或 completed 可在未原子 publish 时出现；
20. observation 没有 source event identity/payload conflict/trust policy 边界，却宣称 exactly-once 或可用于正式 forecast；
21. nowcast 没有 issued/origin/published 分离、独立 freshness、版本化 probability/rule 工件或缺失工件 fail-closed 行为；
22. import/validate/seed 在 API/BackgroundTasks/无 lease 进程执行；
23. network DELETE 会物理删除 revision/历史，或 archive 后仍能提交新任务；
24. G0～G6/acceptance 使用不存在的 entrypoint/harness、mypy continue-on-error、跨轮 artifact 或非仓库根命令；
25. JWT/fallback/RBAC/限流安全门禁未 PASS 就注册/公开 forecast API；
26. owner 表有重复 path、复合 owner，或领域模块与 integration 同时修改 shared file；
27. Timescale hypertable 声明跨 partition unique，或 registration/frame 不同事务；
28. simulation/network-job/nowcast/calibration 任一 durable job 缺 canonical status、lease/fencing 或 `next_attempt_at`；
29. `UNSAFE_PATH` 不是 422，import/validate/seed 任一不要求 Idempotency-Key，或 `idempotency_records.id`/引用列 FK 缺失；
30. forecast transition、terminal、suppression/outbox 仍存在第二套规则；
31. trust level 由 payload/source 自报，或 external_unverified 可进入正式同化/AC-5；
32. general 可借用 nowcast slot；
33. initial state 被计入 network revision hash，或 request fingerprint 未绑定 revision/snapshot/initial-state/target-manifest 四类 hash；
34. G5 任一 harness/fixture/参数未冻结，或 acceptance 使用跨 RUN_ID/commit artifact；
35. dependency-config 修改超出 §15.0 `DEP-AUTH-01..13` 的 path/scope/phase flag，或 DEP-PREP 改动 prep=false 路径，或 integration handoff 未通过第 17.0 保留断言；
36. R0 v3 独立复审非 PASS 时创建、修改、重命名 migration，或将 dependency preparation 误报为已落地；
37. 未经新的独立复审 PASS 自行标记 Frozen，或把设计冻结与实现期 G0～G6/acceptance 混为同一阶段，或把缺失/未执行入口写成 PASS；
38. calibration finalizer 写不存在的 progress 列，或 completed report/metrics 不一致；
39. nowcast completed 等待 relay 才写 producer `published_at`，或 relay 回写/混淆 `nowcast_runs.published_at` 与 `event_outbox.published_at`；
40. 四类 durable job 任一 retry surface/code 缺行，或 attempt=3 仍可继续重试/丢失 exhausted error code；
41. revision/manifest/initial-state/probability/rule 只做 service-only 校验而缺对应复合 FK，或 forecast alert target 未绑定同一 simulation/revision/manifest；
42. map-data catchments 未包含在唯一 target manifest；
43. policy config 把自身 hash 写入被 hash JSON、缺 sidecar，或 `nowcast_runs.config_hash` 无唯一 canonical 输入；
44. idempotency 缺 24h generation/retire/并发规则、active partial unique 或 FK audit retention；
45. simulations/calibration_runs 缺单列 `idempotency_record_id UUID NOT NULL FK`/`UNIQUE(idempotency_record_id)`，仍保存 `idempotency_key` 或以永久 user+key unique 阻断新 generation，或 simulation cancel 额外污染 simulations resource FK；
46. network_jobs/simulations/calibration_runs/nowcast_runs 任一 DDL 缺 `CHECK ((status='retry_wait') = (next_attempt_at IS NOT NULL))`，或缺一致的 retry metadata CHECK（retry_wait 时 backoff>0/error 非空；非 retry 时二者 NULL）；
47. handoff `settings.fields[]` 未逐项覆盖且仅覆盖 §12 的 54 个授权字段、`settings.unauthorized_fields[]` 未逐项记录其余 47 个 `authorized=false,preserve_only=true` 字段，或两组并集不等于完整 `Settings.model_fields`。

## 19. 本轮检查状态

本轮读取已落盘且双副本 SHA-256 一致的最新 round-3 复审，同时读取 `implementation/dependency-contract.md` 和历史 `reviews/dependency-contract.md`，只修改获准文档。实际执行：

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

C-01～C-04 已按冻结决策转为文档约束；逐项记录见 `reviews/v3-final-remediation.md`。§3.1～§3.8、B-01～B-07、N-01～N-07 与上一轮 §4 PASS 项未回退。整改后的 bundle 尚未独立复审，本文不记录其值；状态保持 `R0_ROUND_3_FAILED / NOT_APPROVED / ALL_IMPLEMENTATION_WORK_BLOCKED`，不得自行标记 Frozen。

本次未修改 SOURCE_PRD、生产代码、依赖实现或 migration；工作树中其他 production/test 改动来自并行工作流，新的独立复审 PASS 前不得 merge/landing。

未运行：新的独立复审、官方 owner/handoff/migration checker、G0～G6、AC-1～AC-8、pytest、Vitest、npm ci、Alembic live、Docker、solver/worker/calibration、性能与 backup/restore；均为 NOT RUN。`scripts/verify_hydraulic_stack.py`、三个 checker、三个 benchmark harness 和 `test:perf:twin` 当前未交付，静态断言不得冒充实现门禁通过。
