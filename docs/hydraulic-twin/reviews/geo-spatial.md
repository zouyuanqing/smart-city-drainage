# GeoJSON 与空间查询独立审查

- **模块**：`geo-spatial`
- **审查范围**：冻结空间契约、真实 `geometry.py` / `hydraulic_schemas.py` / ORM 映射、目标测试与定向边界诊断。
- **结论**：**BLOCKED**，`approved=false`。存在 5 项高风险问题和 2 项中风险问题；指定测试通过，但没有证明端到端的双存储查询或完整 GeoJSON 导入契约。
- **变更边界**：本审查未修改源代码、测试、迁移或配置；只写入本报告。

## 实际执行的检查

### 指定测试文件

命令：

```text
cd "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage\backend" && PYTHONPATH="$PWD" .venv/Scripts/python.exe -m pytest tests/hydraulic/test_geo_spatial.py -q
```

输出：

```text
6 passed, 284 warnings in 0.04s
```

这只证明现有 6 个行为测试通过；下面的失败边界不在该测试矩阵中。

### 定向诊断

命令（在同一 backend 目录执行）：

```text
PYTHONPATH="$PWD" .venv/Scripts/python.exe - <<'PY'
# 依次检查 FeatureCollection、schema 旁路、MultiPolygon、portable filter、bbox 与 query plan
PY
```

关键输出：

```text
EXPORT_KEYS ['features', 'type']
ROOT_CRS_ACCEPTED True
DUPLICATE_STABLE_ACCEPTED [{'stable_key': 'N-1'}, {'stable_key': 'N-1'}]
DIRECT_SCHEMA_OUTSIDE_HOLE_ACCEPTED True
OVERLAPPING_MULTIPOLYGON_ACCEPTED True
NON_MAPPING_COUNTS 1 0 0 ['INVALID_ROW']
WRONG_ENTITY_MATCHES ['wrong']
PLAN_FIELDS ['backend', 'bbox', 'entity_type', 'exact_filter_required', 'geometry_column', 'order_by', 'parameters', 'table', 'where_sql']
BOOL_BBOX_ACCEPTED {'west': 0.0, 'south': 0.0, 'east': 2.0, 'north': 2.0}
ZERO_AREA_DIRECT_INTERSECTION True
DEEP_SCHEMA_RESULT RecursionError maximum recursion depth exceeded
MISSING_STABLE_KEY_ACCEPTED 1 None {}
MIXED_LAYER_ACCEPTED ['Point', 'LineString']
```

另有独立检查命令及输出：

```text
if [ -f app/services/hydraulic/repositories.py ]; then echo REPOSITORIES_PRESENT; else echo REPOSITORIES_ABSENT; fi
# REPOSITORIES_ABSENT

if grep -R -q --exclude-dir='__pycache__' -E 'build_spatial_query_plan|filter_rows_by_bbox' app; then ...; fi
# SPATIAL_CALLER_PRESENT；唯一命中为 geometry.py:383、geometry.py:433 的定义
```

`mypy` 定向检查也通过：

```text
.venv/Scripts/python.exe -m mypy app/services/hydraulic/geometry.py
Success: no issues found in 1 source file
```

未运行：完整 hydraulic/全量测试、真实 PostgreSQL/PostGIS/Timescale 查询、迁移、Alembic、性能基准、容器/Compose 集成；因此不能把本报告中的离线结果当作数据库门禁。

## Findings

### GS-01（高）：FeatureCollection 导入没有执行契约级身份/层级/范围校验

**问题**：`parse_geojson_feature_collection` 只检查根类型和 `features` 数组，然后逐个解析 geometry；没有检查集合级 `crs`/`bbox`、稳定键存在性与重复、layer/entity 类型或 network scope。结果是未知 CRS 会被默认为 `EPSG:4326`，同一 `stable_key` 可重复出现，既无 id 也无 stable key 的 feature 也能进入结果，混合 Point/LineString 集合也能成功解析。这不是完整的 GeoJSON 导入验证。

**证据**：

- `backend/app/services/hydraulic/geometry.py:224-276`：FeatureCollection 循环只做 `type`、properties JSON、id 类型和单 feature geometry 解析；没有集合级 CRS/bbox、stable key 去重、layer/entity/network 检查。
- `backend/app/services/hydraulic/geometry.py:192-221`：`parse_geojson` 只处理单个 geometry/Feature，不能替 FeatureCollection 做集合级校验。
- 诊断输出：`ROOT_CRS_ACCEPTED True`、`DUPLICATE_STABLE_ACCEPTED ...`、`MISSING_STABLE_KEY_ACCEPTED 1 None {}`、`MIXED_LAYER_ACCEPTED ['Point', 'LineString']`。
- 冻结约束要求 CRS 对外固定 `EPSG:4326`（`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:88-96`），GeoJSON 深层/非法输入必须阻断为 `INVALID_GEOJSON`（`docs/hydraulic-twin/PRD_SUPPLEMENT.md:302-317`），并须产生稳定 target manifest/稳定键（`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:442-463`）。

**影响**：错误坐标系、重复身份、跨 layer/network 的 feature 可能进入后续导入流程，导致错误的 canonical DTO、计数、manifest 和拓扑报告；集合级 bbox 错误也会被静默丢弃。

**修复建议**：在导入边界显式要求 `entity_type`/layer、network/revision scope 和稳定键来源；校验集合级 CRS/bbox，拒绝缺失/重复 stable key、重复 segment 及混合 layer；把这些规则接到 durable import worker 的 blocking validation，并为 root CRS、重复键、缺 stable key、混合类型和非法 bbox 增加 golden tests。不能仅依赖此函数的逐 feature 语法解析。

### GS-02（中）：GeoJSON 导出丢失项目要求的 CRS 元数据

**问题**：`export_geojson_feature_collection` 只返回 `type` 与 `features`，没有项目契约要求的 `crs: EPSG:4326`。导出的 geometry 字符串虽可被内部 codec 读回，但外部消费者无法从导出 envelope 得到明确的坐标参考系。

**证据**：

- `backend/app/services/hydraulic/geometry.py:279-300`：返回字典固定为 `{"type": "FeatureCollection", "features": exported}`，没有 CRS 字段。
- 诊断输出：`EXPORT_KEYS ['features', 'type']`。
- 项目全局约束固定对外 CRS 为 `EPSG:4326`（`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:88-96`）；数据契约的 GeoJSON 导出条款明确要求包含 `crs: EPSG:4326`（`docs/hydraulic-twin/contracts/data.md:163-171`，该文件自身标注为历史/非规范，但其明确的项目格式意图与当前全局 CRS 约束一致）。

**影响**：下游导入器若严格依赖显式 CRS，会把合法导出判为未知 CRS；不同后端/客户端的坐标语义也可能不一致。

**修复建议**：按项目冻结格式在 FeatureCollection 和/或每个 geometry 的约定位置输出明确的 EPSG:4326 CRS，并增加导出后重新解析及 CRS 断言测试。若项目决定采用 RFC 7946 的“无 crs、默认 WGS84”语义，应先更新冻结契约，而不是让实现和契约静默分叉。

### GS-03（高）：生产实体 schema 绕过 `geometry.py` 的增强拓扑/深度校验

**问题**：`NetworkNodeCreate`、`PipeSegmentCreate`、`CatchmentCreate` 直接把 `GeometryDocument` 作为字段模型并调用其 `require_entity_geometry`，没有调用 `geometry.py` 的 `_validate_canonical_geometry` 或 coordinate-shape guard。`GeometryDocument` 自身只做基础 ring 简单性/闭合检查，未做 hole containment、hole overlap 和 MultiPolygon 跨组件检查；递归 `_flatten_coordinates` 对恶意深嵌套直接抛 `RecursionError`，而不是可定位的 `INVALID_GEOJSON`/422 报告。

**证据**：

- `backend/app/schemas/hydraulic_schemas.py:174-195`：`_flatten_coordinates` 递归展开坐标，没有深度上限。
- `backend/app/schemas/hydraulic_schemas.py:240-269`：基础 polygon 校验只检查闭合、数量和单环自交，不检查 hole 关系。
- `backend/app/schemas/hydraulic_schemas.py:589-653`：生产实体模型直接使用 `GeometryDocument`，`CatchmentCreate` 仅调用 `require_entity_geometry`。
- `backend/app/services/hydraulic/geometry.py:104-170`、`:652-687` 才包含严格 coordinate shape 和 hole/环拓扑逻辑，但应用代码搜索只发现测试导入，未发现生产调用。
- 诊断输出：`DIRECT_SCHEMA_OUTSIDE_HOLE_ACCEPTED True`、`DEEP_SCHEMA_RESULT RecursionError maximum recursion depth exceeded`。
- 冻结要求所有浮点 finite（`IMPLEMENTATION_CONTRACT.md:88-96`），过深 GeoJSON 必须返回 `INVALID_GEOJSON`（`PRD_SUPPLEMENT.md:302-317`），两条后端必须使用同一受测 codec（`IMPLEMENTATION_CONTRACT.md:1261-1264`）。

**影响**：API/worker 若直接使用这些 Pydantic DTO，会把 wrapper 明确拒绝的几何写入 canonical 模型；深嵌套输入还可能使请求异常退出，形成可重复的 CPU/栈耗尽路径。

**修复建议**：把深度限制、严格二维/有限数、hole 拓扑和跨组件拓扑约束下沉到 `GeometryDocument` 的唯一 schema validator，或让所有生产入口强制调用一个无循环依赖的 canonical codec；捕获/转换 `RecursionError` 为稳定 validation issue，并给直接实体 DTO 增加回归测试。

### GS-04（高）：MultiPolygon 只逐 polygon 校验，组件重叠/包含会被接受

**问题**：`_validate_canonical_geometry` 对 MultiPolygon 的每个 polygon 单独调用 `_validate_polygon_topology`，但不检查 polygon 之间是否相交、重叠或包含。于是 portable codec 接受 PostGIS `ST_IsValid` 会拒绝的 MultiPolygon，双路径几何结果不一致。

**证据**：

- `backend/app/services/hydraulic/geometry.py:681-687`：循环逐 polygon 校验后立即结束，没有跨组件关系检查。
- `backend/app/services/hydraulic/geometry.py:637-678` 的关系检查只处理同一 polygon 的 shell/hole。
- `backend/app/models/hydraulic_models.py:686-698`：PostGIS catchment 约束明确要求 `ST_IsValid(geometry)` 及 Polygon/MultiPolygon 类型。
- 诊断输出：`OVERLAPPING_MULTIPOLYGON_ACCEPTED True`。
- 冻结契约要求 catchment 为 Polygon/MultiPolygon、geometry 必须由规范 WKT 生成/校验（`IMPLEMENTATION_CONTRACT.md:1253-1258`），portable 必须复用同一 codec（`:1261-1264`）。现有测试 `backend/tests/hydraulic/test_geo_spatial.py:94-118` 只覆盖 Polygon，没有 MultiPolygon 组件关系测试。

**影响**：portable 路径可能返回 PostGIS 路径拒绝的汇水区，造成计数/面积/空间结果不一致；重叠组件还可能被重复计入导出和统计。

**修复建议**：对 MultiPolygon 组件做边界相交、内部包含和重复组件检查，明确允许的接触规则；增加重叠、包含、相同组件和合法分离组件的 golden tests，并在两种 profile 使用同一 fixture。

### GS-05（高）：双存储路径目前只是无消费者的纯函数，且查询计划无法表达 network/revision scope

**问题**：`build_spatial_query_plan` 与 `filter_rows_by_bbox` 提供了 SQL 片段和纯 Python 后过滤，但没有实际 repository/API 消费者；`SpatialQueryPlan` 也没有 `network_id`/`network_revision_id` 或相应 SQL predicate。portable `where_sql` 只有 bbox 条件，若调用方直接执行表级查询，结果可能跨 network/revision 泄漏。实现摘要也明确把 repository 集成列为后续 owner 工作。

**证据**：

- `backend/app/services/hydraulic/geometry.py:89-101`：`SpatialQueryPlan` 字段只有 backend、entity_type、table、bbox、SQL、参数、排序和 geometry column，没有 network/revision scope。
- `backend/app/services/hydraulic/geometry.py:382-429`：`build_spatial_query_plan` 的输入和 SQL 生成没有 network/revision 参数；portable 条件仅为四个 min/max bbox 条件。
- `backend/app/services/hydraulic/geometry.py:432-486`：`filter_rows_by_bbox` 只接收 rows/bbox，不接收 entity/network/revision。
- 实际检查输出：`REPOSITORIES_ABSENT`；`build_spatial_query_plan|filter_rows_by_bbox` 在 `app/` 中只命中上述定义，没有调用方。
- `docs/hydraulic-twin/implementation/geo-spatial.md:66-70` 明确写明 repository 集成未修改，调用方仍需消费这些接口。
- 冻结契约要求 portable 先 SQL 粗过滤再由 service 精确相交（`IMPLEMENTATION_CONTRACT.md:1261-1265`），map-data 必须绑定 network/revision/manifest（`:1270-1277`），profile 不匹配必须 fail closed（`PRD_SUPPLEMENT.md:128-135`）。

**影响**：当前交付不能证明“无 PostGIS 文本路径”在真实数据库查询中可运行；更严重的是接口没有强制 network/revision 条件，未来 repository owner 若漏加 scope 就可能返回其他网络或错误 revision 的实体。当前排序字符串 `stable_key ASC, id ASC`（`geometry.py:426`）也没有实现契约要求的 target-manifest/UTF-8 stable-key 顺序和 opaque cursor。

**修复建议**：在 `hydraulic-schema` owner 的 repository handoff 中把 network/revision scope 设为必填并生成参数化 predicate；先执行 bbox 粗过滤，再强制 entity type 精确过滤，最后按冻结 manifest 顺序分页。增加真实 PostgreSQL portable/PostGIS 对照测试和跨 network/revision 负例；本模块不应越权直接改 `repositories.py`，但该集成缺失应保持为交付阻断。

### GS-06（中）：portable 精确过滤不校验实体几何类型，并丢失非 mapping 坏行

**问题**：`filter_rows_by_bbox` 的签名没有 `entity_type`，所以 pipe 查询可以把 Point 行当作匹配；非 `Mapping` 行虽然产生 `INVALID_ROW` issue，却没有追加到 `invalid` 集合，违反“保留 rejected/invalid rows”的返回语义。

**证据**：

- `backend/app/services/hydraulic/geometry.py:432-437`：参数只有 rows、bbox、geometry_key，没有 entity type。
- `backend/app/services/hydraulic/geometry.py:446-480`：只调用通用 `validate_geometry(raw_geometry)`，不调用 `require_entity_geometry`；非 mapping 分支 `:447-455` 只追加 issue 后 `continue`。
- 诊断输出：`WRONG_ENTITY_MATCHES ['wrong']`；`NON_MAPPING_COUNTS 1 0 0 ['INVALID_ROW']`。
- 几何类型规则在 `backend/app/schemas/hydraulic_schemas.py:39-43,564-571`；冻结契约要求同一受测 codec（`IMPLEMENTATION_CONTRACT.md:1261-1264`）。

**影响**：数据库中混入错误类型或坏 row 时，portable 结果会与 PostGIS/entity schema 语义不一致，并且调用方无法从 `invalid` 集合审计丢失的行。

**修复建议**：让精确过滤接收并强制 `entity_type`，或由 repository 在调用前完成不可绕过的 typed DTO 校验；所有产生 `INVALID_ROW` 的输入都应同时保留在 `invalid`，并增加 wrong-type/non-mapping 回归测试。

### GS-07（中）：bbox 严格规则在不同入口不一致，bool 和零面积 bbox 可被接受

**问题**：`parse_query_bbox` 对 mapping/sequence 直接 `float(...)`，没有排除 Python `bool`；`geometry_intersects_bbox` 对已经是 `BoundingBox` 的对象不再调用严格的 `parse_query_bbox`，而底层 `BoundingBox` 允许 west=east/south=north。于是 query plan 会拒绝零面积 bbox，但直接精确过滤入口会接受。

**证据**：

- `backend/app/services/hydraulic/geometry.py:489-521`：`parse_query_bbox` 的 mapping/sequence 转换没有 bool guard；严格 west<east/south<north 只在后续显式检查。
- `backend/app/services/hydraulic/geometry.py:744-767`：`geometry_intersects_bbox` 对 `BoundingBox` 直接使用，仅依赖 `BoundingBox` 的范围/非严格顺序。
- `backend/app/schemas/hydraulic_schemas.py:149-163`：`BoundingBox` 的 schema validator 允许相等边界。
- 诊断输出：`BOOL_BBOX_ACCEPTED {'west': 0.0, ...}`、`ZERO_AREA_DIRECT_INTERSECTION True`。
- 冻结 map-data bbox 规则要求 finite 且 `min_lon < max_lon`、`min_lat < max_lat`（`IMPLEMENTATION_CONTRACT.md:1270-1274`；补充契约 `contracts/data.md:110-120`）。

**影响**：不同入口对同一非法 bbox 产生不同结果；零面积 viewport 可能产生看似有效的空间结果，bool 也可能被误当作数字接受。

**修复建议**：建立唯一的严格 query-bbox validator，拒绝 bool、NaN/Infinity、越界和相等边界，并让 `parse_query_bbox`、`build_spatial_query_plan`、`filter_rows_by_bbox`、`geometry_intersects_bbox` 全部调用它；为每个入口补负例测试。

## 通过项与覆盖缺口

- 指定 `test_geo_spatial.py` 的 6 个测试通过，覆盖 Point、LineString/Polygon round-trip、若干 bbox 边界、PostGIS/portable 计划差异和坏 WKT 文本路径。
- 现有测试没有覆盖 MultiPolygon 组件重叠、集合级 CRS/bbox、缺失/重复 stable key、混合 layer、production `CatchmentCreate` 旁路、深嵌套、非 mapping 行、wrong entity 和跨 network/revision scope；这些正是上述失败路径。
- 未进行真实 PostGIS/Timescale/PostgreSQL 验证，因此 `ST_MakeEnvelope`、GiST、generated geometry、portable SQL 列与实际 repository 行为均未证明。

## 最终判定

`approved=false`。在 GS-01、GS-03、GS-04、GS-05 等高风险问题解决并补齐真实双路径集成测试前，不应将本模块标记为可交付。GS-02、GS-06、GS-07 也应在同一修复批次中处理。
