# 排水管网数据契约

> 状态：**SUPERSEDED / NON-NORMATIVE — 不得作为实现契约**。本文件已被 `../PRD_SUPPLEMENT.md` 整改候选 v2 与 `../IMPLEMENTATION_CONTRACT.md` 整改候选 v2 取代，仅保留首轮领域审查轨迹。
>
> 规范来源优先级：`SOURCE_PRD.md`（不可变需求）→ `PRD_SUPPLEMENT.md`（候选产品/验收）→ `IMPLEMENTATION_CONTRACT.md`（候选实现）。本文件与其冲突时没有规范效力。
>
> 2026-09-24 设计审查未批准 v1。实现前必须按以下替换表重写本文件：`wkt`→`portable`；`revision_no/topology_revision`→`current_revision_id/network_revision_id`；强类型 `NetworkSnapshot/PondCell/BoundaryCondition/InitialState` + closed properties schema；revision-scoped dense target manifest；import/validate/seed→`network_jobs` + durable worker/lease/fencing；source event 四元组幂等；DELETE→只 archive、保留 revision；portable 仍要求 PostgreSQL + TimescaleDB。
>
> 以下旧段落中凡出现不同表名、状态名、物理删除、vanilla PostgreSQL 路径、API BackgroundTasks、无 fencing frame 幂等或不同 gate 命令，均视为审查历史，禁止实现。

## 1. 不可回避的现状与决策

| 编号 | 明确决策 | 依据与影响 |
|---|---|---|
| D-01 | **Alembic 是唯一可部署 schema 事实源**；`docker/init-db.sql` 改为由 Alembic 生成/校验的演示 SQL，或从首次启动路径移除；生产启动不得以 `create_all` 补洞。 | 现有 Compose 挂载 init SQL（`docker-compose.yml:19-22`），而初始迁移没有 init SQL 的 `sensor_readings`（`docker/init-db.sql:100-118` 对比 `backend/alembic/versions/0001_initial.py:25-228`）；应用仍调用开发用 `create_all`（`backend/app/core/database.py:137-142`）。保留三路建表无法证明 AC-8。 |
| D-02 | **空间后端在部署时二选一，且一个数据库终身固定**：`postgis` 或 `portable`。两者都是 PostgreSQL + TimescaleDB；portable 仅指无 PostGIS 的 WKT+bbox 路径。不能在一次请求中混用、运行时切换，也不能先创建普通表再“打开 PostGIS”。 | 任务 PRD 要求 PostGIS 与无 PostGIS 的 WKT/GeoJSON 降级路径；现状无 geometry、无 PostGIS extension，只有设备浮点经纬度（`backend/app/models/db_models.py:99-103`）。固定后端让 schema、索引、回滚和性能测试可判定。 |
| D-03 | **逻辑模型仅一份，几何物理列按后端差异化**。API/导入/导出/仿真只面对统一 DTO；不得让业务层出现 `if postgis`。 | 否则“等价双路径”只能靠人工约定，不能做同输入、同计数、同拓扑的自动对照。 |
| D-04 | **一个已发布 network revision 不可原地修改**。CRUD 的 update 默认创建新 revision；simulation/forecast 只引用 `network_revision_id`，从而可重跑、可导出、可回滚。 | PRD 要求可回跑 inp 和结果保留；没有不可变 revision 时，历史结果无法证明使用了哪套几何/拓扑。 |
| D-05 | **空间查询不承担拓扑连接查询**。bbox 是空间裁剪；节点/管段 CRUD 先按 `(network_revision_id, stable_key)` 定位，拓扑/仿真使用关系表与强类型 snapshot。 | portable 路径无 PostGIS 原生空间索引；将两者混用会迫使 API 扫全表。 |
| D-06 | **旧 `devices` 只做可选观测绑定，不与管网表建立多态“万能外键”**。通过独立 join 表约束绑定对象必须存在。 | 现有 `Device` 只关联摄像头、告警（`backend/app/models/db_models.py:119-125`），不存在管网对象；不可在 `devices` 上硬塞一个无外键约束的 `node_id` 字符串。 |
| D-07 | **删除采用 archive-only v1 语义**：`DELETE network` 幂等设置 archived 并保留所有 revision/历史；实体通过新 revision 替换。v1 API 不提供物理 purge。 | 任务 PRD 要求可重跑与可回滚；物理删除会破坏 revision、audit 和历史结果。 |

### 1.1 不可行点与替代方案

1. **不可行：把“无 PostGIS”实现为任意 bbox 都自动 <500ms。** 普通 PostgreSQL 不提供 GiST 空间索引；当前依赖也未声明 PostGIS/GeoAlchemy/Shapely（`backend/requirements-cpu.txt:0-33`）。  
   **替代方案：** WKT 路径必须做应用层 bbox 候选集与复杂度基准；P95 目标只在固定规模和硬件上验收。若达不到，API 返回可用的分页结果但记录性能门禁失败，不能伪报达标。
2. **不可行：让 portable 被误解为 vanilla PostgreSQL。** PRD 的“无 PostGIS”不等于“无 TimescaleDB”；FR-7 与 90 天保留要求 frame/observation 使用 Timescale。  
   **替代方案：** `portable` 固定为 PostgreSQL + TimescaleDB + WKT/bbox，不安装 PostGIS；两 profile 都执行时序/retention migration。
3. **不可行：宣称任意 INP 都能“原样回跑”。** PRD 未给出 INP 允许的 section 白名单、版本和不可映射字段策略。  
   **替代方案：** 定义 `inp_profile` 白名单；成功导入时保存原始文件 SHA-256、解析版本、规范化摘要。仅对通过 round-trip fixture 的 profile 开放导出；否则返回明确的 `export_not_representable`，而非生成不可运行文件。
4. **不可行：只靠 geometry 相同就判定两条管段“重复”。** 管网可能合法平行。  
   **替代方案：** 拓扑 validator 将“相同端点 + 规范化几何”判为 error；仅“端点相同但几何不同”判 warning，交由 operator 处理。
5. **不可行：把 `docker/init-db.sql` 与 Alembic 各自长期手写并声称一致。** 当前已经发生 sensor hypertable 漂移（`docker/init-db.sql:100-118` 对比初始迁移）。  
   **替代方案：** SQL 只能是 migration 的生成物；一致性测试比较 live schema，不比较两份人工文本。

## 2. 统一逻辑实体与约束

所有 UUID 均由应用生成；所有时间使用 `TIMESTAMPTZ` UTC；所有长度/深度/标高以 **m**、流量以 **m³/s**、面积为 **m²** 为规范单位。API 不接受无单位数字。

### 2.1 核心实体

| 实体 | 必需列（逻辑契约） | 关键约束/索引 | 删除与所有权 |
|---|---|---|---|
| `networks` | `id`, `code`, `name`, `status(draft/active/archived)`, `current_revision_id`, `created_by`, timestamps | `UNIQUE(code)`；仅 active 可提交新任务；DELETE 只 archive | 后端水力 domain 独占；revision/历史永不物理删除。 |
| `network_revisions` | `id`, `network_id`, `revision_no`, `parent_revision_id`, `source_kind(import/seed/crud)`, `content_sha256`, `manifest`, `created_by`, `created_at` | `UNIQUE(network_id,revision_no)`；`parent -> network_revisions.id`；hash 非空 | 不可变；有 simulation 引用时永久保留。 |
| `network_nodes` | revision-scoped `id/stable_key/kind/elevations/geometry_wkt/bbox/properties schema` | `UNIQUE(network_revision_id,stable_key)`；Point；closed properties | 不可原地改/删；新 revision 替换。 |
| `pipe_segments` | revision-scoped id/key/endpoints/dimensions/roughness/geometry_wkt/bbox/properties | `UNIQUE(network_revision_id,stable_key)`；复合 FK 两端同 revision；LineString；closed properties | 同上。 |
| `catchments` | revision-scoped id/key/outlet/area/geometry_wkt/bbox/properties | `UNIQUE(network_revision_id,stable_key)`；outlet 同 revision；Polygon/MultiPolygon | 同上。 |
| `elevation_profiles` | `id`, `pipe_segment_id`, `offset_m`, `station_m`, `elevation_m` | `UNIQUE(pipe_segment_id,offset_m,station_m)`；offset/station/elevation 有界 | 可选；随 segment revision 快照，不反向改写几何。 |
| `device_network_bindings` | device/network/revision target stable key/observation kind/mapping version/valid revision range/active | 显式 target；seed 只产 candidate，operator/admin 确认；`device_id -> devices.id` | 不用经纬度最近点自动激活。 |
| `network_jobs` + attempts / `network_imports` | durable job status/attempt/lease/fencing/request hash；import detail source/result | API 202 后独立 worker；result hash 确定；状态不可回退 | 原始文件只存 hash/metadata；API 不执行 parser。 |
| `network_validation_issues` | `validation_run_id`, severity/code/entity/message/details | import/validate 共用；唯一 report key | 报告可重建；不是拓扑权威来源。 |
| `simulation_inputs` | `simulation_id`, `network_revision_id`, `rainfall_scenario_id`, `input_sha256`, `solver_name`, `solver_version` | 四个来源与 hash 均不可变 | simulation 契约拥有 job；本表/外键归数据契约协作维护。 |

**注意：** `stable_key` 是导入和导出的稳定业务标识，不能是临时数组下标。禁止两个 active revision 共享同一 revision 编号；禁止 simulation 只保存 `network_id` 而不保存 revision。

### 2.2 外键与删除规则

| 外键 | ON DELETE | 原因 |
|---|---|---|
| `network_revisions.network_id -> networks.id` | `RESTRICT` | 禁止级联抹去审计链。 |
| `network_nodes.network_id -> networks.id` | `RESTRICT` | 由 API 的草稿/归档流程显式处理。 |
| `pipe_segments.upstream_node_id/downstream_node_id -> network_nodes.id` | `RESTRICT` | 悬空端点必须先修复，不能静默删节点。 |
| `catchments.outfall_node_id -> network_nodes.id` | `SET NULL` | 可选出口丢失必须可见；不能伪造新出口。 |
| `elevation_profiles.pipe_segment_id -> pipe_segments.id` | `CASCADE` | 仅在未发布草稿删除时允许。 |
| `device_network_bindings.device_id -> devices.id` | `RESTRICT` | 防止观测绑定静默消失。 |
| `network_revisions.parent_revision_id -> network_revisions.id` | `SET NULL` | 根 revision 合法；其余必须可追溯。 |
| `simulation_inputs.*` | `RESTRICT` | 任何历史 simulation 都锁定输入。 |

**跨 network 约束：** PostgreSQL 外键无法仅凭 `node_id` 判断其 network；必须由 repository 在同一事务中读取并断言 `node.network_id == pipe.network_id`，并有真实 PostgreSQL 集成测试。不得只在 TypeScript/Pydantic 校验。

### 2.3 网络聚合与版本规则

- `network_revisions.manifest` 至少包含：`{node_count, pipe_count, catchment_count, profile_count, parser_version, source_sha256, topology_summary}`；计数来自**实际成功写入的稳定 key 数量**，不是请求中宣称的数字。
- `content_sha256` 对 canonical JSON 计算：键按字典序、UTF-8、浮点保留业务规定精度、数组按稳定 key 排序。它用于“语义相同”判断；原始文件另存 `source_sha256`。
- 更新已 active 的 network 时：创建新 `network_revisions`，复制并修改实体，原子更新 `networks.current_revision_id`；失败时事务回滚，旧 revision 不变。
- 删除草稿 segment 时，先检查是否有引用它的 elevation/设备/仿真输入；任一存在即 409。禁止依赖 ORM relationship 级联偷偷删除证据。

## 3. PostGIS 与 WKT/GeoJSON 双路径

### 3.1 共同的逻辑几何类型

| 实体 | 允许类型 | 规范化规则 |
|---|---|---|
| `network_nodes` | Point | 仅 2D；经度 [-180,180]、纬度 [-90,90]；默认 SRID 4326。 |
| `pipe_segments` | LineString | 仅 2D；至少两个不同点；端点与 upstream/downstream 坐标相同，允许数值容差 `1e-7°`。 |
| `catchments` | Polygon/MultiPolygon | 仅 2D；自环禁止；环方向不作为业务差异；面积为正。 |
| `elevation_profiles` | 无 geometry | station/offset 表达横纵断面，避免用 LineString 混淆平面线与纵剖面。 |

**坐标系决策：** 首发只接受 **EPSG:4326，坐标顺序经度、纬度**。缺少 `crs` 的 INP 视为“局部工程坐标”，必须带 `local_to_wgs84` 仿射/控制点元数据；没有元数据时可导入拓扑，但禁止 bbox/GeoJSON 导出并在报告中给出 `CRS_UNKNOWN` error。若未来接受 3857/4490，必须新增显式转换库和 golden fixture，不允许静默假定。

### 3.2 `postgis` 物理路径

- migration 在已安装 PostGIS 的 PostgreSQL 上执行 `CREATE EXTENSION IF NOT EXISTS postgis`；扩展缺失时 **fail closed**，错误为 `SPATIAL_BACKEND_UNAVAILABLE`，不创建半套 geometry 表。
- 物理列采用 `geometry(Point,4326)`、`geometry(LineString,4326)`、`geometry(MultiPolygon,4326)`，而非无类型 geometry；catchment 需要 Polygon/MultiPolygon 时可用 `geometry(Geometry,4326)` + `CHECK(ST_GeometryType IN ('ST_Polygon','ST_MultiPolygon'))`，但该选择须由 migration contract 固定。
- 创建 GiST 索引；bbox 查询使用 `geometry && ST_MakeEnvelope(minx,miny,maxx,maxy,4326)`，并在 service 层校验 bbox 与 geometry SRID 一致。
- `outfall`/设备绑定等拓扑正确性由关系 FK 与 service 断言完成，不能由 PostGIS 空间包含推断。

### 3.3 `portable` 物理路径（PostgreSQL + TimescaleDB，无 PostGIS）

- 物理列为 `geometry_wkt TEXT NOT NULL` + `min_x/min_y/max_x/max_y DOUBLE PRECISION`，所有列 CHECK 有限且 `min <= max`；不引用 PostGIS 类型。
- 统一在 service 层用同一个受测 geometry codec 解析/规范化 WKT；该 codec 必须声明依赖和版本，**禁止在业务代码散落 regex WKT parser**。
- bbox 候选先用四个数值列做粗过滤，再由 codec 精确相交判断；分页排序固定为 `network_id, stable_key`。没有空间索引时不得承诺固定规模 P95。
- GeoJSON 导入先解析为逻辑 DTO，再在事务中写 WKT；不得将请求原文直接当 WKT SQL/命令执行。

### 3.4 双路径等价性

同一 canonical fixture 在两套数据库中必须得到：相同 stable key、相同规范化 WKT、相同 bbox、相同导入计数、相同 validator code/count、相同 topology summary。PostGIS 输出可以额外带 GeoJSON，但不得改变业务字段。

## 4. bbox 与空间查询契约

### 4.1 输入/输出

`GET /api/networks/{network_id}/map-data?bbox=west,south,east,north&zoom=…&layers=nodes,pipes,catchments&simplify=true`

- bbox 四个值均为有限数字；`min_lon < max_lon`、`min_lat < max_lat`；SRID 固定 4326。
- `limit` 默认 1000、最大 5000；超限 422，不静默截断。
- 返回固定 envelope：`{network_revision_id,target_manifest_id,target_manifest_sha256,bbox,crs,layers,complete,truncated_layers}`。feature_index 来自 immutable manifest，按 stable key UTF-8 byte 升序且 dense；LOD 子集不重编号。
- 同一 `network_id + bbox + layers + cursor` 的排序确定；cursor 是不透明、签名/校验过的排序键，不得由客户端提供 SQL/字段名。
- API P95 ≤500ms 仅适用于已建立索引的 PostGIS 路径和 WKT 路径的**已验收规模**；不在没有基准证据时把该限制强加给任意数据量。

### 4.2 约束

- 设备不能以经纬度最近点自动绑定管网对象；种子生成必须输出“候选绑定”，由 operator/admin 确认。现有设备只有浮点坐标（`backend/app/models/db_models.py:99-103`），无法证明其语义位于某节点/管段。
- PostGIS `bbox` 不能证明“节点在汇水区内”或“设备属于某汇水区”；需要显式 relation/binding。
- `bbox` 不能替代 graph traversal；孤立子图和环路由 topology validator 产出。

## 5. 导入、导出与 round-trip

### 5.1 共同安全前置

- import/validate/seed API 只写 `network_jobs` + idempotency record 并返回 202；解析、校验、seed 由 `network-job-worker` 以 attempt/lease/fencing 执行，禁止 BackgroundTasks/无租约进程。
- 接受格式仅 `.inp`、`.geojson`（`application/geo+json` 或 JSON 实际内容）；不接受 zip、目录、符号链接、网络 URL、任意文件名路径。
- 读取流在 `limit+1` 中止并删除临时文件。PRD 的 20MB 是**仿真沙箱**上限；网络导入另设可配置且不得高于该值的限制（建议初始 20MB，最大节点/管段/坐标数同时限制）。当前 `MAX_UPLOAD_SIZE_MB=100` 只是未被执行的 Settings（`backend/app/core/config.py:102-105`）。
- 临时文件使用不可预测 UUID 目录；用户文件名仅存展示元数据。任务目录、原始文件名和 solver 路径不得拼接执行。
- 解析不执行 INP 的外部 include/脚本，也不连接 GeoJSON 中 URL；输入先转义/解析为受控 DTO。
- 同一请求以 `Idempotency-Key + source_sha256 + parser_version` 去重；相同 key 不同 hash 返回 409，不执行第二次导入。

### 5.2 INP 导入

请求必须携带：`network_id`（目标已存在 draft）、`inp_profile`、`units`、`coordinate_mode(local/wgs84)`；若 local，必须提供控制点。首发 profile 固定为受支持 section 列表，未知 section 采取“保留但不可导出”策略，而不是假装无损。

处理顺序：

1. 字节/结构/坐标/坐标系统检查；
2. 解析 `[JUNCTIONS] [OUTFALLS] [CONDUITS] [XSECTIONS] [SUBCATCHMENTS] [COORDINATES] [VERTICES] [POLYGONS] [RAINFALL]` 等白名单内容；
3. 构造稳定 key 与实体候选；所有端点引用必须在同一 network 内存在；
4. 计算规范化几何、bbox、内容 hash；
5. topology validator；
6. **单事务写入 draft revision**；任一 blocking error 不提交半张网络；
7. 写 import 计数和分级报告。

失败语义：`422 INVALID_INP` + code/line/entity/message；坐标未知为 `422 CRS_UNKNOWN`；超过限制为 `413 IMPORT_TOO_LARGE`；解析器/数据库错误不伪装成输入错误，import 状态为 `failed` 并可重试。

### 5.3 GeoJSON 导入

首发只接受 `FeatureCollection`，feature geometry 类型必须与 layer 对应；属性中的 `network_id/node_id/pipe_id` 必须经 scope 校验。缺 `id` 时按输入文件内稳定 `feature.id` 或显式 `stable_key` 生成，不使用 feature 数组下标。

- 坐标经度/纬度有界；禁止 NaN/Infinity、3D/Measure 混淆和超深嵌套。
- 同一 `stable_key` 多次出现为 `DUPLICATE_STABLE_KEY` error；同拓扑/几何管段为 `DUPLICATE_SEGMENT` error。
- 一个导入最多允许一个 network；跨 network FeatureCollection 返回 422，避免隐式批量写多个聚合。

### 5.4 导出

`GET /api/networks/{id}/export?revision_id=…&format=geojson|inp`

- 默认导出不可变 revision，不导出 mutable head。
- GeoJSON 永远从 canonical DTO 生成，包含 `crs: EPSG:4326`；坐标轴序固定为 lon,lat。
- INP 导出仅对 `exportable=true` 的 revision 开放。先运行 `roundtrip validator`，并对生成文件重新解析、比较实体计数、稳定 key、拓扑摘要和 SHA-256；失败返回 422，绝不返回一个声称可运行的文件。
- 导出不是数据库 dump：不得包含内部 UUID（除非 mapping 文件明确提供）、用户路径、密码、SQL 或内部 provenance 私密字段。
- 若导入 profile 包含未建模 section，导出必须明确 `lossy=true` 的 manifest，或直接拒绝；PRD 的“可回跑 inp”验收只认可 `roundtrip=true` 的 profile。

## 6. 拓扑与校验报告

### 6.1 规则表

| code | severity | 判定 | 是否阻断提交 |
|---|---|---|---|
| `MISSING_COORDINATES` | error | 节点无点、管段/汇水区无几何或坐标非有限 | 是 |
| `DANGLING_NODE` | error | 管段/汇水区引用不存在节点或跨 network | 是 |
| `DUPLICATE_SEGMENT` | error | 同 network、相同两端点、规范化几何一致 | 是 |
| `MISSING_INVERT` | error | 参与仿真的节点没有 invert elevation | 是（simulation-ready revision） |
| `REVERSE_SLOPE` | error | `upstream.invert < downstream.invert` 且超过容差 | 是（simulation-ready revision） |
| `SELF_LOOP` | error | 管段 upstream=downstream | 是 |
| `DISCONNECTED_COMPONENT` | warning/error | 无有效 outfall 的连通分量 | 导入可保存；仿真提交阻断 |
| `CYCLE_DETECTED` | warning | 存在有向环 | 不阻断导入；需 operator 显式确认或 solver policy 拒绝 |
| `MULTIPLE_OUTFALLS_PER_COMPONENT` | warning | 一个分量多出口 | 不阻断导入，仿真前必须确认边界条件 |
| `LOCAL_COORDS_WITHOUT_TRANSFORM` | error | 局部坐标缺控制点 | 是（GeoJSON/bbox/仿真提交） |
| `NON_TOPOLOGICAL_CATCHMENT_OUTLET` | error | outfall 不在 network | 是 |
| `PROFILE_STATION_OUT_OF_RANGE` | error/warning | profile station 超出 segment length | error（严格模式）/ warning（导入模式） |

**明确选择：** 环路不是导入错误。管网可有合法 loop；报告必须列出 loop 节点/边，`simulation_ready=false`，由仿真 profile 决定是否允许。这样满足 PRD “输出环路报告”而不把可运行网络误删。

### 6.2 报告契约

```json
{
  "schema_version": 1,
  "network_id": "uuid",
  "network_revision_id": "uuid",
  "status": "valid|valid_with_warnings|invalid",
  "counts": {"nodes": 0, "pipes": 0, "catchments": 0, "profiles": 0, "issues": 0},
  "issues": [
    {
      "severity": "error|warning|info",
      "code": "DANGLING_NODE",
      "entity_type": "pipe_segment",
      "entity_key": "P-001",
      "message": "upstream node does not exist",
      "line": 42,
      "details": {}
    }
  ],
  "simulation_ready": false,
  "topology": {"components": 0, "cycles": 0, "outfalls": 0}
}
```

- `counts` 必须与成功写入/候选集合一致；失败报告的 counts 是 `parsed` 候选数，另设 `committed=false`，不能冒充数据库行数。
- `status=invalid` 时不得出现部分写入；`valid_with_warnings` 仍必须无 error。
- 前端和 API 均按 `code` 判断，不按本地化 message 判断。

## 7. 迁移、部署与回滚

### 7.1 Migration 顺序（建议的新增链，不修改已部署 0001 的事实）

1. `0002_reconcile_existing_schema`：只处理/记录当前 init SQL 与 migration 漂移；不得假设空库。必须先在旧 0001 库、init SQL 库、当前 ORM metadata 三种 fixture 上盘点。
2. `0003_network_core`：创建 networks/revisions/nodes/segments/catchments/profile/binding 表、普通列与 FK；不创建 PostGIS geometry。
3. `0004_postgis_variant`：仅在 `SPATIAL_BACKEND=postgis` 且 `CREATE EXTENSION` 可用时执行，创建 geometry 列/GiST；WKT 库不得执行。
4. `0005_network_import_and_reports`：导入任务、校验 issue、binding 约束索引。
5. `0006_timescale_simulation_input`：postgis 与 portable 都创建 simulation/frame/observation hypertable 与 retention；portable 仅不含 PostGIS。

**禁止：** 在 0001 中直接重写历史 migration；在 `downgrade` 无条件 `DROP EXTENSION`；将 PostGIS 列和 WKT 列同时作为两个可写事实源。

### 7.2 扩展/回滚规则

- 先 additive migration → 回填并校验 → 应用切换读取 → 下一版再删除旧列；同一发布不得“先删旧列再部署新代码”。
- 每个新列先 nullable 或有 server_default；回填以 batch key 执行，记录 `rows_total/rows_migrated/rows_failed`。
- **发布回滚** 是恢复旧应用镜像并保持扩展后 schema；不是自动 destructive downgrade。
- **测试/空环境的 downgrade** 必须对称：先删 FK/索引/子表，再删 geometry/WKT 列和 hydraulic tables；不得删除共享 `users/devices/alerts` 或 Timescale/PostGIS 扩展。
- 任何 downgrade 发现 hydraulic 数据时，抛出显式错误并要求人工备份/清理；绝不能“成功 downgrade 但静默丢数据”。现有 0001 已表现出该风险：downgrade 删表却不删共享 extensions（`backend/alembic/versions/0001_initial.py:244-258`）。
- 迁移日志必须包含 revision、backend mode、开始/结束时间、行数；应用就绪时 `alembic current` 必须等于镜像声明 head。

### 7.3 数据库启动与 Compose

- `docker compose` 启动顺序：Postgres/Redis/Influx health → 单独 `migrate` job (`alembic upgrade head`) → backend/worker。backend/worker 不执行 `alembic upgrade`，更不执行 `create_all`。
- PostGIS 模式使用明确包含 PostGIS+Timescale 的镜像/安装层；portable 模式使用 Timescale 但不安装 PostGIS。不可用 `timescale/timescaledb:latest-pg15` 并猜测 PostGIS 存在；当前 Compose 正是该未锁定镜像（`docker-compose.yml:8-21`）。
- readiness 必须检查：DB 连接、`alembic current`、空间 backend、核心表；根 `/health` 当前无条件 healthy（`backend/app/main.py:302-305`），不能作为数据 readiness 证据。
- 备份/恢复必须包含 Alembic revision、空间 backend、canonical content hash；恢复后做 schema diff、计数/拓扑摘要和 API smoke。

## 8. 错误与 API 兼容语义

新水力 API 保持现有 FastAPI 422 的 `detail + errors` 兼容外形（`backend/app/main.py:313-322`），但 `errors[].code` 必须稳定：

| HTTP | code 示例 | 语义 |
|---|---|---|
| 401/403 | `AUTH_REQUIRED` / `FORBIDDEN` | 沿用 JWT；不得泄漏文件路径或栈。 |
| 404 | `NETWORK_NOT_FOUND` | ID 不存在。 |
| 409 | `REVISION_IN_USE`, `IDEMPOTENCY_CONFLICT`, `GEOMETRY_BACKEND_MISMATCH` | 当前状态/版本冲突；不隐式覆盖。 |
| 413 | `IMPORT_TOO_LARGE` | 超过字节/资源上限。 |
| 415 | `UNSUPPORTED_IMPORT_FORMAT` | 非 `.inp`/`.geojson` 或内容不是白名单格式。 |
| 422 | `INVALID_INP`, `INVALID_GEOJSON`, `INVALID_BBOX`, `TOPOLOGY_INVALID`, `CRS_UNKNOWN` | 可定位的输入/拓扑问题。 |
| 503 | `SPATIAL_BACKEND_UNAVAILABLE` | PostGIS/DB 未就绪；禁止降级写成 WKT 后继续。 |

错误示例固定为：`{"detail":"导入失败","errors":[{"code":"DANGLING_NODE","message":"...","entity_type":"pipe_segment","entity_key":"P-001","line":42}]}`。前端按 code 分支；旧 API/SSE/告警 envelope 不改名。

## 9. 文件所有权（实现时）

| 文件/目录 | 唯一职责 | 禁止事项 |
|---|---|---|
| `backend/app/models/hydraulic_models.py` | hydraulic ORM、约束、关系；不含路由/解析。 | 不直接依赖 PostGIS 业务表达式以外的 API 层。 |
| `backend/app/models/__init__.py` | 导出模型给 Alembic。 | 不写 DDL。 |
| `backend/app/domain/hydraulic/geometry_codec.py` | WKT/GeoJSON 规范化、bbox、codec 版本。 | 不访问网络、不写 DB。 |
| `backend/app/domain/hydraulic/spatial_repository.py` | `postgis`/`portable` 的查询实现与统一 DTO。 | 不把 dialect 分支泄露到 routes。 |
| `backend/app/domain/hydraulic/importers/` | `.inp`/GeoJSON 白名单解析、稳定 key、计数。 | 不执行 solver、不写用户路径。 |
| `backend/app/domain/hydraulic/exporters/` | canonical GeoJSON/可验证 INP 输出。 | 不从 mutable ORM 直接拼字符串。 |
| `backend/app/domain/hydraulic/topology.py` | 连通分量、悬空、孤立、环路、重复与报告。 | 不用几何距离替代拓扑。 |
| `backend/app/api/routes/networks.py` | HTTP/RBAC、DTO、错误映射。 | 不包含 parser 或 SQL 拼接。 |
| `backend/app/schemas/hydraulic_schemas.py` | 请求/响应/错误/报告 Pydantic 契约。 | 不依赖 FastAPI route 对象。 |
| `backend/alembic/versions/0002_*.py`…`0006_*.py` | schema、索引、扩展、数据回填与可逆测试。 | 不修改 0001 历史语义；不删除共享 extension。 |
| `docker/init-db.sql`（若保留） | 由 migration 生成的空库演示/校验物。 | 不再独立手写业务表、hypertable 或 seed。 |
| `frontend/src/types/hydraulic.ts` | stable id、GeoJSON、bbox、validation report TS 类型。 | 不直接消费 DB geometry/WKT 列。 |
| `backend/tests/hydraulic/`、`frontend/src/test/__tests__/hydraulic*.test.*` | 对应单元/集成/契约测试。 | 不用 SQLite mock 代替 PostGIS/Timescale 证明。 |

## 10. 验收证据与测试矩阵

### 10.1 必须存在的证据

| 验收 | 自动化证据 | 通过条件 |
|---|---|---|
| A-01 双路径实体/FK | 同一 fixture 分别在 PostGIS 与 portable（PostgreSQL+Timescale，无 PostGIS）执行 migration、CRUD、archive 测试。 | 表/约束/canonical DTO/target manifest/拓扑摘要一致；历史 revision 不可删。 |
| A-02 bbox | 固定 5000 节点/10000 管段/1000 polygon fixture；PostGIS 与 WKT 各跑 EXPLAIN ANALYZE 和 API 基准。 | 空间相交无漏项；排序/分页稳定；仅在记录硬件和规模后判定 P95。 |
| A-03 INP/GeoJSON 导入 | 最小/最大合法 fixture、截断、错误坐标、重复 stable key、环路、逆坡、悬空、深层 GeoJSON。 | 计数正确；error 不半提交；报告 code/line/entity 稳定。 |
| A-04 恶意输入 | 20MB+1、zip、symlink/junction、`..`/UNC/盘符、重复 key、CPU/内存炸弹样本。 | 明确 413/415/422；无越界读写；临时目录清理；服务继续健康。 |
| A-05 round-trip | 每个声明 profile：import → canonical revision → export → import 到新 network → 比较 stable key/count/topology/hash。 | `roundtrip=true` 才允许“可回跑 inp”；不可映射 profile 明确拒绝。 |
| A-06 迁移 | 0001 旧库快照、init SQL 旧库、空库：upgrade→检查→clone downgrade→upgrade；PostGIS/portable 两种模式。 | 无 schema/data 漂移；portable 含 Timescale；生产回滚保持扩展 schema；不静默删数据。 |
| A-07 Compose/启动 | 干净 volume 执行 migration job、backend、worker；检查 readiness 与 `/docs`。 | Alembic head、空间 backend、核心表正确；不以浅 `/health` 代替。 |
| A-08 旧 API 零回归 | 现有 pytest + 新增 API 集成测试。 | 旧 auth/device/alert/SSE 测试无回归；新数据写操作 admin/operator 受控。 |

### 10.2 不应作为“通过”的替代证据

- 仅运行 `alembic heads`：只证明迁移图可读；现状输出 `0001_initial (head)`（`backend/alembic/versions/0001_initial.py:14-17`）。
- 仅运行 `docker compose config --quiet`：只证明 YAML 可解析。
- 仅用 SQLite `Base.metadata.create_all`：现有夹具使用它（`backend/tests/conftest.py:9-20`），但实际路由直接调用 session context manager（同文件 `33-42`），不是水力 PostgreSQL 约束/索引证据。
- 仅用 PostGIS 查询成功：不能证明 WKT 降级、migration rollback 或 round-trip。
- 仅用静态 ORM metadata：不能证明 Alembic/live schema 一致。

## 11. 本次审查的检查状态

- 本次为只读源码/文档审查并写入本契约；**没有**修改 ORM、API、migration、Compose、init SQL 或测试。
- 本次未运行 pytest、Vitest、build、Docker、`alembic upgrade/downgrade`、PostGIS/Timescale 集成或性能基准；这些均标为**未运行**，不是通过。
- 六份 recon 报告记录的历史检查结果仅作为“现状报告所述”引用；本文件没有把它们冒充为本轮已执行证据。尤其现有报告已说明 Docker daemon 不可用、未完成真实迁移/Compose 验证（`docs/hydraulic-twin/recon/deployment.md:261-269`、`docs/hydraulic-twin/recon/quality.md:416-423`）。
