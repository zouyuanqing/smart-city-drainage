# 排水管网水力数字孪生：前端数据与交互契约

> 状态：**SUPERSEDED / NON-NORMATIVE — 不得作为前端实现契约**。本文件已被 `../PRD_SUPPLEMENT.md` 与 `../IMPLEMENTATION_CONTRACT.md` 的整改候选 v2 取代，仅保留首轮前端审查轨迹。
>
> 2026-09-24 设计审查未批准 v1。规范实现只使用：`network_revision_id` 与 immutable target manifest；`published_attempt_id/no`（旧 `result_version` 只能作只读别名）；`GET /api/forecast/events` + fetch Authorization Bearer；forecast `issued_at` + 独立 freshness；安全门禁先于 UI/API 开放。
>
> 以下旧段落中的 `topology_revision` 独立编号、ticket `/api/sse/hydraulic`、独立 result root、旧 API/文件路径和旧测试命令均被显式取代，禁止实现。

## 0. 规范用语与边界

- **必须**：实现与验收均不可省略。
- **应该**：默认实现；偏离时必须在 PR 中记录原因和替代证据。
- **可以**：可选，不构成 AC 门禁。
- 本文所称“帧”是求解器输出时间点；24 h、5 min 步长必须含起点和终点，因此 `frame_count = floor(86400 / 300) + 1 = 289`。
- 所有 ID 为 UUID 字符串；所有时间为 UTC RFC 3339（`2026-09-24T10:00:00Z`）；所有深度使用 `*_m` 或明确单位，不复用旧设备的 `water_level_mm`。
- 前端不得把 `NaN`、`Infinity`、本地化日期字符串或无单位数字写入 API/store；服务端必须先拒绝或归一化。
- 水力域接口不得回退到旧 mock/random 数据。依赖或数据不可用时必须显式失败，不能返回看似有效的 `source: "mock"`。

## 1. 证据基线

### 1.1 任务消息中的 PRD 约束

以下目标直接来自本次任务给出的 PRD，而非当前代码事实：

- FR-5：`/api/networks` CRUD/import/validate/export/seed、rainfall scenarios、simulations、forecast latest/horizon/risk-ranking，以及新 `forecast` SSE 事件；写操作沿用 JWT admin/operator；错误和 Swagger 兼容。
- FR-6：`/simulation` 路由、菜单权限、i18n；Leaflet 管网/节点/汇水区和充满度；淹没面时间轴播放/暂停/拖拽/1x/2x/4x；A/B；预测/实测与不确定性带；5000 节点/10000 管段/千级 polygons 至少 25 fps。
- FR-3：节点/管段/积水按不超过 5 min 帧序列化，TimescaleDB 保留 90 天。
- NFR：新增 API P95 不超过 500 ms；5000 节点/10000 管段 24 h 仿真在 4 核纯 Python 不超过 180 s。
- AC-3：50 年一遇 24 h 仿真进度、结果和地图时间轴可回放；AC-6：A/B 指标差异可查。

### 1.2 六份现状报告的交叉结论

| 来源 | 已证实现状 | 对本契约的影响 |
|---|---|---|
| `docs/hydraulic-twin/recon/backend-data.md:54-101` | 当前只有旧监测 API；无 networks/rainfall/simulations/forecast；SSE 事件仅旧域。 | 新 API 必须独立冻结 schema，不复制旧裸 `dict` 风格。 |
| `docs/hydraulic-twin/recon/backend-data.md:126-168` | Alembic、init SQL、ORM 三路 schema 漂移；无水力/PostGIS 表和 Timescale retention。 | 帧与网络数据的后端所有权必须先落到单一 schema 事实源。 |
| `docs/hydraulic-twin/recon/realtime-security.md:55-117` | SSE replay 仅单进程内存，事件无 schema/version，forecast 不存在。 | 新 forecast 使用独立、认证、持久游标的事件流；不改旧匿名流语义。 |
| `docs/hydraulic-twin/recon/realtime-security.md:128-154` | 告警无显式状态机、审计和通知幂等。 | forecast 的 `phase` 与处理 `status` 分离。 |
| `docs/hydraulic-twin/recon/frontend.md:39-65` | Leaflet 仅设备 DOM Marker；Zustand 为单 store；没有 frame/forecast slice。 | 不能把现有 Marker 组件或全局 store 直接扩成水力域。 |
| `docs/hydraulic-twin/recon/frontend.md:121-139` | 无 viewport culling、抽稀、Canvas/预渲染、FPS 测试。 | 25 fps 尚无实现或验收证据。 |
| `docs/hydraulic-twin/recon/simulation.md:133-185` | Redis 不是任务队列；无 Simulation/Frame/Forecast 实体或 90 天策略。 | 任务/帧 REST 契约必须可持久化、可重试、可恢复。 |
| `docs/hydraulic-twin/recon/simulation.md:235-270` | 水力 API、SSE、worker、沙箱和 PostGIS 均缺失。 | 本契约是新增能力，不把旧接口推断为已实现。 |
| `docs/hydraulic-twin/recon/deployment.md:8-13` | Compose 无 PostGIS/仿真 worker；health 为浅层。 | 新接口的 500/503 必须反映真实 readiness，不能静默降级。 |
| `docs/hydraulic-twin/recon/deployment.md:121-129` | init SQL、Alembic、ORM 结构漂移。 | `network_revision_id + target_manifest_id/hash` 必须成为仿真/A-B 的乐观并发边界。 |
| `docs/hydraulic-twin/recon/quality.md:139-151` | 无 e2e/性能资产；A/B、回放、25 fps 均未自动验收。 | 必须新增独立 fixture、Playwright 和性能门禁。 |
| `docs/hydraulic-twin/recon/quality.md:275-300` | 当前地图每个设备创建一个 Marker；构建体积和帧率未闭环。 | 现有实现不能满足万级几何性能。 |

### 1.3 直接源码证据

| 发现 | 源码证据 | 决策 |
|---|---|---|
| 路由只到 settings，没有 `/simulation` | `frontend/src/App.tsx:35-100` | 新路由使用 route metadata + `ProtectedRoute`，viewer 可读，operator/admin 才显示写操作。 |
| 路由守卫仅检查 localStorage token | `frontend/src/App.tsx:17-24` | 仿真路由必须同时验证认证态/角色；后端 RBAC 仍为最终边界。 |
| 菜单按 localStorage role 过滤 | `frontend/src/components/layout/AppLayout.tsx:71-110` | 权限矩阵只能改善 UX，不能作为授权。 |
| Axios 把错误压成 `Error(message)`，丢失 status/code/request_id | `frontend/src/services/api.ts:26-39` | 新水力客户端必须保留结构化 `ApiClientError`。 |
| 当前类型没有水力域 | `frontend/src/types/index.ts:4-130` | 新类型独立于 `Device`/`SensorReading`。 |
| SSE 用原生 EventSource，无法加 Bearer header | `frontend/src/hooks/useSSE.ts:47-93` | 新水力 SSE 使用 fetch streaming + Authorization Bearer；不放 query token。 |
| Dashboard/MapView 各自建立 SSE | `frontend/src/pages/Dashboard.tsx:221-235`、`frontend/src/pages/MapView.tsx:26-29` | 新水力事件由一个 provider 管理；不在页面重复连接。 |
| SSE manager 使用进程内计数/1000 条历史/256 队列 | `backend/app/services/sse_manager.py:32-43,52-120,142-189` | 不能把现有 replay 当 forecast 的可靠事实源。 |
| 旧 SSE endpoint 无认证 | `backend/app/api/routes.py:394-414` | 保持旧 endpoint 行为；新水力流另建认证 endpoint。 |
| 旧校验错误是 `detail + errors`，500 是 `detail + type` | `backend/app/main.py:314-335` | 新错误 envelope 只能加字段，不能移除现有字段。 |
| Leaflet 为每个设备创建 `Marker + DivIcon` | `frontend/src/components/map/MapVisualization.tsx:26-122,274-295` | 万级几何不得使用 React Marker/Popup 列表。 |
| 现有地图只做 resize/fit/flyTo | `frontend/src/components/map/MapVisualization.tsx:167-205` | 生命周期可复用；数据渲染需重写为自定义 Canvas layer。 |
| Zustand 每设备只保留 30 条、告警 200 条 | `frontend/src/store/useAppStore.ts:78-110` | 帧缓存不得进入当前全局 store。 |
| Dashboard 解构整个 Zustand store | `frontend/src/pages/Dashboard.tsx:179-191` | 水力 UI 使用细粒度 selector；地图帧不触发 React 树重建。 |
| 图表全量 `import * as echarts` 并 `setOption(..., true)` | `frontend/src/components/charts/WaterLevelChart.tsx:5-8,71-151`、`FlowTrendChart.tsx:5-8,71-139` | 新图表使用 modular import、registry 和增量/局部更新。 |
| 现有 SSE 握手前后端字段不一致 | `frontend/src/hooks/useSSE.ts:66-70` 对比 `backend/app/services/sse_manager.py:152-155` | 对旧 `connected` 只做加字段兼容；新流不复用该握手。 |
| 测试 setup 只加载 jest-dom | `frontend/src/test/setup.ts:1` | 新的 EventSource/ResizeObserver/localStorage/Canvas 测试需显式 fixture。 |
| CI 目标 Node 20，但无性能/e2e job | `.github/workflows/ci.yml:53-93` | 新 gate 必须进入 CI，不能只写本地脚本。 |

## 2. 明确决策、不可行点与替代方案

### 2.1 已冻结决策

| ID | 决策 | 原因与验收口径 |
|---|---|---|
| D-01 | 新水力 API 全部使用强类型 request/response model；成功响应不返回裸 `dict`。 | OpenAPI 必须可生成 TypeScript 联合 fixture；现有 `detail/errors` 错误字段保持兼容。 |
| D-02 | 保持旧 `GET /api/sse/events` 的事件名和 envelope；新增认证的 `GET /api/forecast/events`，以 fetch Authorization Bearer 访问，事件名固定为 `forecast`。 | 避免给旧匿名流补认证导致零回归破坏；新流不复用进程内 replay。 |
| D-03 | 帧按“每帧一行/一个不可变快照 + 与静态 `feature_index` 对齐的紧凑数组”提供；禁止一次返回 90 天全部帧。 | 5000+10000 目标下，row-per-value 查询难以满足 500 ms；数组可一次读取并直接转 typed arrays。 |
| D-04 | `1x = 每 1 秒切换一个 5 min 帧`，`2x = 2 帧/秒`，`4x = 4 帧/秒`；UI 同时显示模拟时刻。 | PRD 未定义 1x 基准。采用 `PLAYBACK_BASE_FPS=1`，24 h 在 4x 下约 72 s。 |
| D-05 | 不插值伪造水力状态。相邻帧之间只允许地图相机 rAF 绘制；帧值在离散帧切换。 | 避免对水深/充满率线性插值产生物理上错误的状态。 |
| D-06 | Leaflet 底图继续保留；水力几何使用一个命令式自定义 Canvas `L.Layer`，React 只控制 UI。 | 10k 管段不能创建 10k React-Leaflet layer；一个 canvas 可共享几何、typed arrays 和样式桶。 |
| D-07 | 帧缓存为浏览器内存中的 byte-bounded LRU，v1 不写 IndexedDB/Cache Storage。 | 90 天保留是服务端事实；本地持久化会引入敏感数据、过期和登出清理问题。 |
| D-08 | A/B v1 使用单地图 `A / B / Δ` 三模式，不创建两个 Leaflet 实例。 | 双地图会复制瓦片、DOM、事件和绘制成本；AC-6 要求指标差异可查，不要求双画布。 |
| D-09 | A/B 必须同一 `network_revision_id` + 同一 `target_manifest_id/hash`、同一精确帧网格；不自动重采样。 | revision/manifest 或帧时刻不同会产生误导性空间/指标差异。 |
| D-10 | 水力请求失败必须显示错误，不用 mock/random 数据替代。 | 可重复推演和 AC-3/AC-6 不能接受伪数据。 |
| D-11 | P95≤500 ms 仅约束同步控制面与目标 fixture 下的 map/frame 读取；20 MB import/validate/export 改为 202 job 或流式响应。 | 20 MB 解析/导出无法可靠保证 500 ms。job/流式接口另测排队和 TTFB。 |
| D-12 | `/simulation` 对 viewer 只读；operator 可建/复制情景、提交/取消仿真；admin 额外可 seed/删除/修改受保护配置。 | 对齐现有 admin/operator/viewer 角色，同时满足“写操作 admin/operator”。 |

### 2.2 当前不可行点与可行替代

1. **现状 Marker 架构不能达到 25 fps。** `MapVisualization` 为每个设备创建 `L.DivIcon`/`Marker`（`frontend/src/components/map/MapVisualization.tsx:26-69,285-294`）。把 10000 管段改成 10000 React Marker 只会扩大问题。
   - 选定方案：单 Canvas layer + bbox culling + zoom LOD + 后端简化。
   - 失败替代：先收紧服务端 LOD；若仍失败，使用 Leaflet `ImageOverlay` 播放服务端预渲染帧，牺牲逐要素命中/A/B 细粒度，必须作为范围变更重新验收。
2. **把全部帧放进 Zustand 不可接受。** 当前 store 一次更新会替换 Map/数组（`frontend/src/store/useAppStore.ts:78-89`），Dashboard 又订阅整个 store（`frontend/src/pages/Dashboard.tsx:179-191`）。289 帧 × 15000 要素会造成多份 JS 对象、深比较和内存放大。
   - 选定方案：Zustand 只存 `frameIndex/playing/speed/status`；大数组存 FrameCache，转为 `Float32Array/Uint8Array`。
3. **“所有新增 API P95≤500 ms”按字面包含 20 MB import/validate/export 不可实现。**
   - 选定方案：import/validate 使用 job（202 + progress）；export 使用流式 200；同步列表/详情/submit/cancel 以及 fixture 下 map/frame GET 纳入 500 ms。
4. **1/2/4 倍速基准未定义。** 若 1x 等于真实时间，24 h 回放需 24 h，AC-3 不可实用。
   - 选定方案：1 帧/s 为 1x，并在 UI 明示“5 min/frame”；未来可配置，但 URL、cache key、测试必须带 base FPS。
5. **旧 SSE 原地加认证会破坏旧页面。** 旧 endpoint 当前匿名（`backend/app/api/routes.py:394-414`），且现有 EventSource 无 Authorization（`frontend/src/hooks/useSSE.ts:56-57`）。
   - 选定方案：新水力 endpoint `GET /api/forecast/events` + fetch Authorization Bearer；旧 endpoint 和旧 payload 保持不变。长期可统一为 HttpOnly cookie，但不在 v1 强制迁移。
6. **拓扑/时间网格不同的仿真不能做严格 A/B。** 客户端插值会掩盖差异。
   - 选定方案：返回 `409 COMPARISON_INCOMPATIBLE` 和逐项 reason；UI禁用同步播放，只允许查看各自结果。

## 3. 通用 API 契约

### 3.1 基础约定

- Base path：`/api`。
- 认证：JWT Bearer；写操作继续使用 `require_role(admin, operator)`。前端隐藏按钮不是授权。
- 内容：JSON 请求使用 `application/json`；导入使用 `multipart/form-data`；导出流式响应使用 `text/csv`、`application/geo+json` 或 `application/octet-stream`。
- `Idempotency-Key`：simulation submit、rainfall copy、network import 必须要求；key 为调用方生成的 UUID/ULID，服务端按 `(user_id, route, key)` 唯一。
- Cursor：列表统一 `limit`（1..200，默认 50）+ opaque `cursor`；响应 `next_cursor`，禁止无界列表。
- Cache：不可变结果使用强 ETag；`If-None-Match` 命中返回 304。帧 HTTP cache 最大 1 天，服务端仍按 90 天 retention 删除。

### 3.2 错误 envelope

新水力接口的错误必须保持现有顶层 `detail`：

```json
{
  "detail": "请求参数验证失败",
  "code": "VALIDATION_ERROR",
  "errors": [
    { "loc": ["query", "bbox"], "msg": "必须是 west,south,east,north", "type": "value_error" }
  ],
  "request_id": "019..."
}
```

- 422 必须保留当前 `detail + errors`（`backend/app/main.py:314-323`）。
- 500 可额外保留当前 `type`，但不得泄漏 stack/文件路径；`request_id` 必须与 `X-Request-ID` 相同。
- Axios 适配器必须产生：

```ts
class ApiClientError extends Error {
  status: number
  code: string
  detail: string
  errors: ApiFieldError[]
  requestId?: string
  retryAfterSeconds?: number
}
```

不得再只 `throw new Error(msg)`（当前行为见 `frontend/src/services/api.ts:26-39`）。

### 3.3 状态码与失败语义

| HTTP | code 示例 | 前端行为 |
|---|---|---|
| 400 | `MALFORMED_QUERY`, `UNSUPPORTED_GEOMETRY` | 不重试；保留输入并显示字段错误。 |
| 401 | `AUTH_REQUIRED`, `TOKEN_EXPIRED` | 清认证态并回 `/login`。 |
| 403 | `ROLE_FORBIDDEN` | 保留只读页面；禁用写操作并解释所需角色。 |
| 404 | `NETWORK_NOT_FOUND`, `SIMULATION_NOT_FOUND` | 停止相关请求；提供返回列表动作。 |
| 409 | `TOPOLOGY_CHANGED`, `IDEMPOTENCY_CONFLICT`, `COMPARISON_INCOMPATIBLE` | 不自动换 revision；刷新元数据后由用户确认。 |
| 410 | `FRAME_EXPIRED`, `RESULT_EXPIRED` | 停止播放，保留元数据/截图，明确“已过 90 天保留期”。 |
| 422 | `VALIDATION_ERROR`, `FRAME_INDEX_OUT_OF_RANGE` | 不重试；定位表单/时间轴。 |
| 429 | `RATE_LIMITED` | 遵守 `Retry-After`；SSE ticket 和轮询退避。 |
| 503 | `SOLVER_UNAVAILABLE`, `DEPENDENCY_NOT_READY` | 仿真可显示排队/重试；map/frame 不返回 mock。 |
| 504 | `SIMULATION_TIMEOUT` | 终态失败，保留 `simulation_id` 和日志关联。 |

## 4. API 数据与接口契约

### 4.1 网络与 bbox 地图数据

#### CRUD/导入边界

- `GET/POST /api/networks`
- `GET/PATCH/DELETE /api/networks/{network_id}`
- `POST /api/networks/import`：multipart，最大 20 MiB，202 job
- `POST /api/networks/{network_id}/validate`：202 job
- `GET /api/networks/{network_id}/exports/{format}`：`format=geojson|inp`，流式下载
- `POST /api/networks/{network_id}/seed`：admin

前端只依赖以下稳定字段：`id/name/status/network_revision_id/target_manifest_id/target_manifest_sha256/crs/bounds/node_count/pipe_count/catchment_count/created_at/updated_at`。`crs` 对外统一 `EPSG:4326`；PostGIS 内部存储 CRS 不泄漏给 UI。

#### 地图数据

`GET /api/networks/{network_id}/map-data?bbox={west},{south},{east},{north}&zoom={z}&layers=nodes,pipes,catchments&simplify=true`

```json
{
  "schema_version": "network-map-v2",
  "network_id": "uuid",
  "network_revision_id": "uuid",
  "target_manifest_id": "uuid",
  "target_manifest_sha256": "...",
  "crs": "EPSG:4326",
  "bbox": [121.30, 31.15, 121.60, 31.32],
  "zoom": 13,
  "complete": true,
  "simplified": true,
  "source_counts": { "nodes": 5000, "pipes": 10000, "catchments": 1000 },
  "returned_counts": { "nodes": 5000, "pipes": 10000, "catchments": 1000 },
  "nodes": { "type": "FeatureCollection", "features": [] },
  "pipes": { "type": "FeatureCollection", "features": [] },
  "catchments": { "type": "FeatureCollection", "features": [] },
  "generated_at": "2026-09-24T10:00:00Z"
}
```

约束：

1. GeoJSON 坐标顺序固定 `[longitude, latitude]`。
2. 每个 feature 必须有稳定 UUID `id` 和从 0 开始的 `properties.feature_index`；仿真帧数组按该 index 对齐。
3. `feature_index` 只在 `target_manifest_id` 内稳定，按 stable key UTF-8 byte 升序生成且 dense；任何拓扑写入都生成新 revision/manifest，不允许原地复用旧 index。
4. PostGIS 与 WKT/GeoJSON 降级路径必须返回相同业务字段和 ID/index；差异只在 `storage_mode`/服务实现，不在 UI contract。
5. `returned_counts` 任一图层超过 25000 features 时必须返回 `complete:false` 和 `truncated_layers[]`；前端必须显示“已简化/部分图层”，不得静默当作全量。
6. 服务端根据 zoom 做几何简化；目标 fixture 在 z13 的简化不能删除 critical outfall、溢流口或已 surcharge 管段。
7. 强 ETag：`W/"network-{id}-r{revision}-z{zoom}-{bbox_hash}-{layers}"`。
8. 目标 fixture P95≤500 ms；bbox 查询不得 N+1 查询每条要素。

### 4.2 降雨情景

- `GET/POST /api/rainfall-scenarios`
- `GET/PATCH/DELETE /api/rainfall-scenarios/{scenario_id}`
- `POST /api/rainfall-scenarios/{scenario_id}/copy`，要求 `Idempotency-Key`

前端模型至少包括：

```ts
interface RainfallScenario {
  id: string
  name: string
  return_period_years: 1 | 2 | 3 | 5 | 10 | 20 | 50 | 100 | 'custom'
  duration_seconds: number
  step_seconds: 60 | 300 | 900
  source_type: 'synthetic'
  random_seed: number
  samples: Array<{ time: string; rainfall_mm_per_hour: number }>
  created_at: string
  updated_at: string
}
```

- 自定义 duration≤72 h；`samples.length = floor(duration/step)+1`。
- 相同 `source_type/seed/duration/step` 必须生成相同样本；复制情景生成新 ID，但保留 seed。
- 422 明确指出 return period、step、duration、seed 的不合法组合。

### 4.3 Simulation 提交与状态机

#### 提交

`POST /api/simulations` + `Idempotency-Key`

```json
{
  "network_id": "uuid",
  "network_revision_id": "uuid",
  "rainfall_scenario_id": "uuid",
  "solver_selection": "auto",
  "requested_solver_id": "swmm_kw",
  "start_at": "2026-09-24T00:00:00Z",
  "duration_seconds": 86400,
  "frame_interval_seconds": 300,
  "parameters": {}
}
```

成功返回 202：

```json
{
  "id": "uuid",
  "status": "queued",
  "progress_pct": 0,
  "attempt": 0,
  "max_attempts": 3,
  "network_id": "uuid",
  "network_revision_id": "uuid",
  "target_manifest_id": "uuid",
  "target_manifest_sha256": "...",
  "rainfall_scenario_id": "uuid",
  "solver_selection": "auto",
  "solver_id": "swmm_kw",
  "solver_version": "...",
  "created_at": "2026-09-24T10:00:00Z",
  "links": {
    "self": "/api/simulations/uuid",
    "cancel": "/api/simulations/uuid/cancel"
  }
}
```

- `max_attempts=3` 表示首次执行 + 最多两次重试。
- 同 key 同 body 重放返回同一 simulation；同 key 不同 body 返回 `409 IDEMPOTENCY_CONFLICT`。
- explicit 不可用 solver 在提交事务返回 503；`auto` 在提交事务 probe 并冻结实际 solver，运行后不得换 solver。
- `duration_seconds`、`frame_interval_seconds` 必须匹配 rainfall scenario；不匹配返回 422。

#### 状态机

```text
queued -> running -> completed
queued|running -> cancelling -> cancelled
running -> retry_wait -> running       (最多 2 次)
queued|running -> failed
queued|running -> timed_out
```

- `progress_pct` 为 0..100 的整数，对整个 job 单调不减；retry 不把总进度清零。
- `attempt` 为 0..3；`retry_wait` 必须返回 `retry_in_seconds` 和最后错误 code。
- `cancelling` 不是终态；worker 在帧边界检查 cancel token。
- `completed` 必须包含 `published_attempt_id/no`、`frame_count`、target-manifest URL；只读 `result_version` 如保留必须等于 published_attempt_no。

#### 查询与取消

- `GET /api/simulations?status=&network_id=&cursor=&limit=`
- `GET /api/simulations/{simulation_id}`
- `POST /api/simulations/{simulation_id}/cancel` → 202 `{status:"cancelling"}`；终态重复取消返回 200 且状态不变。
- 前端 active job 每 2 s 查询；页面隐藏时可降为 10 s；terminal 后停止。SSE 不作为任务进度的唯一事实源。

### 4.4 结果、目标清单与帧索引

- `GET /api/simulations/{id}/target-manifest`
- `GET /api/simulations/{id}/frames`（只返回索引/时刻/checksum/bytes，不返回值数组）
- `GET /api/simulations/{id}/frames/{frame_index}?fields=nodes.depth_m,pipes.fill_ratio,ponding.depth_m`

`targets` 响应：

```json
{
  "schema_version": "simulation-targets-v1",
  "simulation_id": "uuid",
  "published_attempt_id": "uuid",
  "published_attempt_no": 1,
  "network_revision_id": "uuid",
  "target_manifest_id": "uuid",
  "target_manifest_sha256": "...",
  "node_ids": ["..."],
  "pipe_ids": ["..."],
  "ponding_ids": ["..."],
  "units": {
    "depth": "m",
    "velocity": "m/s",
    "area": "m2"
  }
}
```

目标清单必须不可变并可按 ETag 缓存；A/B 两次相同 topology 可复用。

`frames` 索引响应：

```json
{
  "simulation_id": "uuid",
  "published_attempt_id": "uuid",
  "published_attempt_no": 1,
  "frame_interval_seconds": 300,
  "frame_count": 289,
  "frames": [
    { "frame_index": 0, "time": "2026-09-24T00:00:00Z", "etag": "..." , "bytes": 412331 }
  ]
}
```

不得提供“返回所有帧值”的 convenience endpoint。

### 4.5 单帧 payload

`GET .../frames/{frame_index}`：

```json
{
  "schema_version": "hydraulic-frame-v1",
  "simulation_id": "uuid",
  "published_attempt_id": "uuid",
  "published_attempt_no": 1,
  "network_revision_id": "uuid",
  "target_manifest_id": "uuid",
  "target_manifest_sha256": "...",
  "frame_index": 12,
  "time": "2026-09-24T01:00:00Z",
  "interval_seconds": 300,
  "nodes": {
    "feature_index": [0, 1, 2],
    "depth_m": [0.0, 0.12, null],
    "surcharge": [false, false, null]
  },
  "pipes": {
    "feature_index": [0, 1, 2],
    "fill_ratio": [0.21, 0.88, null],
    "velocity_mps": [0.4, 1.7, null]
  },
  "ponding": {
    "feature_index": [8, 19],
    "depth_m": [0.03, 0.27]
  },
  "metrics": {
    "flooded_node_count": 2,
    "surcharge_pipe_count": 1,
    "flooded_area_m2": 1540.0
  }
}
```

约束：

1. 正常帧可以返回目标子集，但 `feature_index` 必须升序；null 表示无结果，不能用 0 代替。
2. `frame_index` 范围错误 422；已过 retention 410；simulation 未 completed 409。
3. `fields` 只允许白名单；未知字段 422。
4. 服务端按“每帧一行/一个不可变快照 + PostgreSQL array/JSONB + TOAST/gzip”存储，避免前端 N+1 和 row-per-value 连接爆炸。
5. 24 h/5 min 的 180 s 仿真预算必须包含 frame serialization/persistence；只测 solver、不测落库不算 AC 通过。
6. 目标 fixture 单帧 GET P95≤500 ms；数据以 ETag/304 和 gzip 降低传输。不得把“所有帧”作为一个响应。

### 4.6 Forecast API 与 SSE

#### REST

- `GET /api/forecast/latest?network_id=&limit=`
- `GET /api/forecast/horizon?network_id=&from=&to=&target_ids=&step_minutes=5`
- `GET /api/forecast/risk-ranking?network_id=&horizon_minutes=120&limit=10`

`limit` 1..200。Forecast 对象：

```ts
interface Forecast {
  id: string
  type: 'flood_forecast' | 'pipe_surcharge'
  phase: 'predicted' | 'observed'
  status: 'new' | 'active' | 'acknowledged' | 'resolved' | 'expired'
  target_type: 'node' | 'pipe' | 'ponding'
  target_id: string
  expected_at: string
  lead_time_minutes: number
  depth_m: number | null
  confidence: number
  simulation_id: string
  severity: 'critical' | 'warning' | 'info'
  suppression_key: string
  created_at: string
  updated_at: string
}
```

`confidence` 为 `[0,1]`；`lead_time_minutes` 为非负整数；`expected_at` 必须晚于 forecast 生成基准（observed 转换除外）。

#### 认证 SSE

1. `GET /api/forecast/events?network_id=<uuid>` 使用 `Authorization: Bearer <JWT>`，fetch streaming 读取；只发送 `forecast`。
2. v1 不实现 query ticket 或 `/api/sse/hydraulic` 别名；JWT 不得进入 URL，访问日志必须脱敏 Authorization。
3. 安全门禁必须先验证 JWT alg/iss/aud/exp/role、RBAC、fallback 禁用和 rate limit；未 PASS 时不开放该 endpoint。
4. `Last-Event-ID` 使用持久、单调的 decimal sequence；`event_id` 另为 UUID。sequence 与业务事件在 PostgreSQL/outbox 同一事务提交。

SSE `data`：

```json
{
  "schema_version": 1,
  "event_id": "uuid",
  "sequence": 10482,
  "event_type": "forecast.created",
  "occurred_at": "2026-09-24T10:00:00Z",
  "network_id": "uuid",
  "forecast": {}
}
```

- 同一业务事件只经一个 relay 发布；SSE、告警中心、通知共享 `forecast.id`。
- 30 分钟 suppression 在服务端持久化；客户端不能自行决定“抑制成功”。
- queue overflow/replay gap 时发 `forecast.resync_required`，payload 含 `from_sequence/to_sequence`；客户端立即调用 latest/horizon 重同步。
- 未知 `schema_version`、缺字段、重复或倒退 sequence：记录结构错误并重同步，禁止写入 store。

### 4.7 A/B comparison

`GET /api/simulations/{baseline_id}/compare/{candidate_id}?metrics=peak_depth,flooded_node_count,flooded_area_m2,rmse,nse`

```json
{
  "schema_version": "simulation-comparison-v2",
  "compatible": true,
  "network_revision_id": "uuid",
  "target_manifest_id": "uuid",
  "target_manifest_sha256": "...",
  "frame_grid": {
    "start_at": "2026-09-24T00:00:00Z",
    "end_at": "2026-09-25T00:00:00Z",
    "interval_seconds": 300,
    "frame_count": 289
  },
  "baseline": {
    "simulation_id": "uuid",
    "published_attempt_id": "uuid",
    "published_attempt_no": 1,
    "rainfall_scenario_id": "uuid-a",
    "solver": "swmm-5.2",
    "parameters_hash": "sha256:..."
  },
  "candidate": {
    "simulation_id": "uuid",
    "published_attempt_id": "uuid",
    "published_attempt_no": 1,
    "rainfall_scenario_id": "uuid-b",
    "solver": "python-dw-1",
    "parameters_hash": "sha256:..."
  },
  "metrics": [
    {
      "key": "flooded_node_count",
      "unit": "count",
      "baseline": 12,
      "candidate": 8,
      "absolute_delta": -4,
      "relative_delta": -0.333333,
      "better_direction": "lower"
    }
  ]
}
```

- baseline/candidate 必须 completed 且未过 retention。
- `network_revision_id` 或 `target_manifest_id/hash` 不同 → 409。
- `start_at/end_at/frame_interval_seconds` 任一不同 → 409；服务端不插值。
- rainfall scenario、solver、parameters 可以不同，正是 A/B 的比较变量。
- baseline 为 0 时 `relative_delta=null`。
- `better_direction` 是领域规则，不由浏览器猜测；无优劣方向的指标为 `null`。
- compare endpoint 读取完成时预计算的 metrics，避免每次扫描帧。

## 5. Leaflet 地图渲染契约

### 5.1 所有权

- 后端拥有：拓扑 revision、bbox、几何简化、feature index、帧值、单位和比较指标。
- 前端拥有：Leaflet projection、viewport culling、屏幕像素换算、样式桶、命中测试、渲染调度和交互状态。
- 前端不得重写坐标、重算拓扑、改变单位或把本地插值结果写回 API。

### 5.2 图层结构

1. `TileLayer`：底图，继续由 Leaflet 管理。
2. `HydraulicCanvasLayer`：一个自定义 `L.Layer`/overlay；内部可按 catchment/pipe/node 分组绘制，但共享一个 RAF 调度。
3. `InteractionLayer`：Canvas 命中测试和 popup；不为每个 feature 创建 DOM marker。
4. `ForecastOverlay`：目标高亮/预计-已发生样式，仍写入同一 Canvas，不创建新 Leaflet 实例。

React 组件只在以下事件更新：network/revision/result 选择、layer visibility、缩放级别、frame 完成、选择对象。逐帧值不进入 React props。

### 5.3 Culling、LOD 与抽稀

- 服务端先按 bbox + zoom 简化；客户端为每个 feature 预计算 bbox，`move` 时只绘制与 viewport 相交者。
- Canvas 绘制通过 `requestAnimationFrame` 合并同一帧的 move/zoom/data 更新；禁止 `setInterval(draw, 16)`。
- 默认 LOD：

| zoom | catchments | pipes | nodes | labels |
|---|---|---|---|---|
| <11 | outline + aggregate | major pipes only | hidden | none |
| 11–13 | simplified fill | visible | hidden | none |
| 14–15 | visible | visible | circles | selected only |
| ≥16 | visible | visible | circles | selected + critical only |

- “major pipes” 由后端 `properties.major=true` 或 diameter/上游汇水面积规则决定；前端不得用随机抽样删掉 critical features。
- 文本标签只绘制选中对象和告警对象；不得为 5000 节点全量 `fillText`。
- 命中测试使用屏幕空间 grid，容差 6 px；mousemove 最多 30 Hz，点击不受该节流影响。

### 5.4 样式与可访问性

管段 fill ratio 固定分桶：

- `[0, 0.25)`：normal
- `[0.25, 0.50)`：warning-low
- `[0.50, 0.75)`：warning-high
- `[0.75, 1.0]`：critical
- `>1.0` 或 `surcharge=true`：surcharge

积水深度固定分桶：dry `<0.01 m`、`0.01–0.15`、`0.15–0.30`、`0.30–0.50`、`≥0.50 m`。

- 颜色之外必须有线宽/虚实/图标或文本：surcharge 使用粗线 + warning icon；predicted 使用虚线轮廓 + “预计”；observed 使用实线轮廓 + “已发生”。
- 图例列出数值范围、单位、predicted/observed，不只展示色块。
- popup/popover 必须支持键盘打开；Canvas 自身提供可聚焦 feature list 的等价入口，避免只靠鼠标命中。

### 5.5 25 fps 验收

基准环境：production build、4 vCPU x86_64、8 GiB RAM、无 GPU、1920×1080、当前 CI Node 20 对应 Chromium；底图使用本地固定 tile fixture以隔离公网；API 为本机目标 fixture。

Fixture：5000 nodes、10000 pipes、1000 catchments、289 frames、frame interval 300 s、A/B 两结果同时可选。

脚本运行 60 s：连续 pan/zoom，timeline 以 4x 播放，周期性切换 layer/selection。门槛：

- 实际 presented frames / elapsed ≥25 fps；
- RAF frame time p95≤50 ms；
- 初始加载后的 long task 不得 >200 ms；
- FrameCache 估算字节≤256 MiB；
- map/frame API P95≤500 ms；
- 自动化输出 JSON：`scripts/perf/results/twin-map-<commit>.json`。

未生成该 JSON 或浏览器采样不足 55 s 时，不得宣称 AC 通过。若自定义 Canvas 仍失败，先收紧服务端 LOD；仍失败才采用预渲染 `ImageOverlay` 替代并重新走产品验收。

## 6. 帧缓存契约

### 6.1 缓存键与数据表示

- Key：`v2/{simulation_id}/{published_attempt_id}/{target_manifest_id}/{frame_index}/{fields_hash}`。
- HTTP ETag/response bytes 用于去重；相同 key 正在请求时复用同一个 Promise。
- Worker 将 JSON 数值数组转换为：
  - `Float32Array`：depth/fill/velocity；
  - `Uint8Array` 或 `Int8Array`：surcharge/style class；
  - `Uint32Array`：feature index。
- 原始 ArrayBuffer 在 decode 后可释放；缓存 size 以 `byteLength` 计，不以对象个数计。
- Map 静态几何有独立 namespace，可与 frame 共享总 256 MiB 上限。

### 6.2 调度

- 最多 2 个 frame network request 并发；当前帧优先，playing 时预取 `next+1..+2` 和 `previous-1`。
- 拖拽时立即 abort 非当前请求；目标帧 P0，邻近帧 P1，其他 P2。
- A/B 为 P0 的 A/B 当前帧，P1 为各自 next，A/B 总缓存仍受 256 MiB 限制。
- current A/B frame pin 住，不被 LRU 淘汰；内存压力下先淘汰非相邻旧帧。
- cache 命中不发网络请求；304 视为命中。
- 组件卸载不销毁全局 cache；登出、切换 user/network、显式“清除仿真缓存”时销毁全部 hydraulic cache。

### 6.3 缓存失败语义

| 情况 | 行为 |
|---|---|
| 422 frame index/fields | 停止该请求，标出 timeline 数据无效；不自动改 index。 |
| 409 未完成/拓扑变化 | 停止播放，刷新 simulation metadata。 |
| 410 retention 过期 | 停止播放，显示 `FRAME_EXPIRED`；可查看仍存在的 aggregate metrics。 |
| 503 dependency not ready | 指数退避 0.5/1/2 s，最多 2 次；保持上一有效帧并显示 buffering。 |
| decode/checksum 失败 | 丢弃该 cache entry，保留上一帧；上报 `FRAME_DECODE_FAILED`，不显示部分数组。 |
| 429 | 按 `Retry-After`；SSE ticket 与 frame GET 使用不同 rate-limit bucket。 |

不得在数组未完整校验时开始绘制；必须先验证长度、单调 feature index、有限数和 checksum。

## 7. 时间轴契约

### 7.1 单一状态机

Zustand 只保存：

```ts
interface TimelineState {
  simulationId: string | null
  resultVersion: number | null
  frameIndex: number
  playing: boolean
  speed: 1 | 2 | 4
  buffering: boolean
  lastFrameTime: string | null
}
```

- 播放/暂停/拖拽/速度/选择 simulation 是命令，写入 store。
- 当前 frame 值从 `FrameCache.getCurrent(...)` 读取；不把数组或整个 frame object 放入 store。
- A/B 只有一个 `frameIndex` 和一个速度；同步兼容时共用一个 clock。

### 7.2 调度算法

- 使用 `performance.now()` 的 accumulator，不用固定 `setInterval`。
- `nextSwitchAt += 1000 / speed`；落后超过 250 ms 时重置 accumulator，避免 spiral of death。
- 到末帧默认停止；再次点击 play 从 0 开始。v1 不自动 loop。
- 页面 hidden 时暂停并记录用户状态；返回后不自动恢复，需用户点击，减少突发 frame fetch。
- 切换 simulation/result/version 时先 pause、清 accumulator，再原子切 frameIndex。

### 7.3 拖拽与键盘

- `pointerdown` 暂停；拖动中最多 100 ms 发布一次 preview index；`pointerup` commit 并恢复原 playing 状态。
- 每次 commit 取消旧请求，只加载目标 P0。
- 键盘：Space 播放/暂停；Left/Right ±1 frame；Shift+Left/Right ±10 frames；Home/End 首/末帧。
- 所有控制有 `aria-label`、当前模拟时刻和 frame `x/N`；1x/2x/4x 文案明确为 `1/2/4 frames per second`。

## 8. A/B 页面与图表契约

### 8.1 页面行为

- A/B 固定选择 `(simulation_id,published_attempt_id)`；同一 simulation 只有 published attempt 可见，discarded attempt 不进入比较。
- 顶部显示 baseline/candidate 的 network revision、rainfall、solver、parameters hash。
- 不兼容时：禁用同步 play/Δ map，显示服务端 reason；仍可分别查看 A、B。
- 地图只有 `A | B | Δ` 模式；切换模式不创建第二个 Leaflet map。
- 指标表必须显示 baseline、candidate、absolute delta、relative delta、unit、better direction；无方向指标不显示绿色“好/坏”。
- AC-6 的最小通过条件：同一固定 fixture 的 comparison API 指标与逐项重算结果一致，页面可导出 machine-readable comparison JSON。

### 8.2 预测、实测、不确定性带

新 `ForecastChart` 使用 ECharts modular import：

- observed：实线；
- predicted：虚线；
- uncertainty：stacked lower/area 或 custom band，lower line transparent；
- now/issue time：竖直 markLine；
- A/B：可用两条 predicted line + delta 独立 subplot，不把 A/B 混成一个难读 series。

规则：

- 数据 >500 点时禁用通用动画；`prefers-reduced-motion` 时禁用装饰动画，但手动播放仍可用。
- 使用 LTTB/固定像素 decimation；tooltip 显示原始 API 值，不从抽样数据反算。
- chart instance registry 在 resize 时 `resize()`，卸载时 `dispose()`；禁止每次 data change 使用 `setOption(option, true)` 全量替换。
- 单位、时区、语言随 i18n 变化，但 API 数值和 cache key 不变。

## 9. 路由、权限、i18n 与零回归

### 9.1 权限矩阵

| 能力 | viewer | operator | admin |
|---|---:|---:|---:|
| 查看 network/map/result/frame/forecast/risk/A/B | ✓ | ✓ | ✓ |
| 创建/更新 rainfall scenario、copy |  | ✓ | ✓ |
| 提交/取消 simulation |  | ✓ | ✓ |
| network create/update/import/validate/export |  | ✓ | ✓ |
| network delete/seed、保护配置 |  |  | ✓ |
| acknowledge/resolve forecast |  | ✓ | ✓ |

前端 route metadata 和按钮使用同一矩阵；后端独立执行 RBAC。

### 9.2 i18n

新增 namespace/key 组：`simulation/rainfall/network/map/timeline/forecast/risk/comparison/errors`。以下必须走 i18n：

- 菜单名、页面标题、按钮、状态、错误解释、单位说明、图例、predicted/observed；
- 禁止在 JSX 中新增硬编码中文；
- 中英文 key parity 测试，缺 key build/test 失败。

### 9.3 旧 API/SSE 零回归

- 旧 `sensors/alerts/system` event name 和 envelope 不变。
- 旧 `connected` 可加 `sse_clients` 字段，但不能删除 `client_id/status`；修复 `frontend/src/hooks/useSSE.ts:66-70` 与 `backend/app/services/sse_manager.py:152-155` 的差异。
- 旧 Alert 的 `id/alert_id` 漂移只在单一 legacy adapter 中兼容；水力 forecast 严格要求 `id`。
- Dashboard/MapView/AlertCenter 的旧行为先有 characterization tests，再迁移 SSE provider；不在同一 PR 同时改旧 payload 和新水力 UI。
- `frontend/nginx-default.conf` 当前只有通用 `/api/`（`:28-40`），生产必须增加与 `docker/nginx/conf.d/default.conf:42-53` 等价的 SSE location：`proxy_buffering off`、no cache、长 timeout。验收需用 `curl -N` 首包/heartbeat，不以 build 代替。

## 10. 文件所有权与建议落点

以下是 v1 唯一写入所有者；其他文件不得绕过这些边界直接解析后端 payload。

| 所有权 | 建议文件 | 责任 |
|---|---|---|
| 后端 schema | `backend/app/schemas/hydraulic.py` | Pydantic v1 response models、错误字段、单位/枚举。 |
| 后端 network/map | `backend/app/api/routes/networks.py`、`backend/app/services/map_data.py` | bbox、revision、简化、feature index、ETag。 |
| 后端 simulation | `backend/app/api/routes/simulations.py`、`backend/app/services/simulation_results.py` | submit/status/cancel/result/frames；不依赖前端轮询逻辑。 |
| 后端 forecast | `backend/app/api/routes/forecasts.py`、`backend/app/services/forecast_events.py` | REST、suppression、outbox、durable sequence、SSE replay。 |
| 后端 comparison | `backend/app/services/simulation_comparison.py` | compatibility + precomputed metrics。 |
| 前端 schema | `frontend/src/types/hydraulic.ts` | 由联合 fixture 校验的 discriminated unions；不改旧 `types/index.ts` 语义。 |
| 前端 API | `frontend/src/services/hydraulicApi.ts`、`apiError.ts` | typed calls、结构化错误、取消和 ETag。 |
| 页面 | `frontend/src/pages/SimulationPage.tsx` | orchestration；不持有几何/帧大对象。 |
| Store | `frontend/src/store/useSimulationStore.ts` | selection/status/timeline/UI only。 |
| 帧缓存 | `frontend/src/features/simulation/frame/FrameCache.ts`、`frameCodec.worker.ts` | byte-bounded LRU、prefetch、abort、typed arrays。 |
| 时间轴 | `frontend/src/features/simulation/timeline/timelineController.ts`、`TimelineControl.tsx` | 单一 clock、speed、drag/keyboard。 |
| 地图 | `frontend/src/features/simulation/map/HydraulicCanvasLayer.ts`、`viewportIndex.ts`、`mapStyle.ts` | Leaflet custom layer、culling/LOD/hit-test/legend。 |
| 图表 | `frontend/src/features/simulation/charts/ForecastChart.tsx`、`ComparisonChart.tsx` | ECharts modular registry、预测/实测/band/A-B。 |
| A/B | `frontend/src/features/simulation/comparison/SimulationComparison.tsx` | pin versions、compatibility、A/B/Δ modes。 |
| 实时 | `frontend/src/features/simulation/realtime/HydraulicSseProvider.tsx` | 单一 fetch-SSE、Authorization Bearer、sequence gap/resync。 |
| 路由/i18n | `frontend/src/App.tsx`、`components/layout/AppLayout.tsx`、`i18n/*.json` | route metadata、菜单、i18n parity。 |

## 11. 失败语义与 UI 状态

| 场景 | UI 必须显示 | 禁止行为 |
|---|---|---|
| map-data `complete:false` | “图层已简化/部分返回”及 source/returned counts | 显示“全量管网” |
| frame loading | 保留上一有效帧 + buffering；不改变 frame label | 显示新 frame 时间但画旧值 |
| frame 410 | 停止播放、结果已过期、仍可看 summary | 无限重试或自动换 simulation |
| simulation failed/timeout | status、error code、simulation_id、retry/查看日志入口 | 仅 toast “失败” |
| cancel race | 依据服务端终态显示 cancelled 或 completed | 前端自行宣告取消成功 |
| SSE gap | `resync_required`，调用 latest/horizon | 把缺失事件当“无告警” |
| forecast 403 | 保留只读地图；说明 operator/admin 要求 | 隐藏整个 simulation 页面 |
| A/B incompatible | 列出 topology/time-grid mismatch | 自动重采样或换 revision |
| 429/503 | Retry-After/依赖状态和手动重试 | tight loop 或 mock fallback |

## 12. 验收证据

### 12.1 本次审查实际运行的检查

以下命令在本次审查中实际执行；结果不是 PRD AC 通过证明。

| 命令（工作目录：仓库根） | 实际结果 |
|---|---|
| `git -C smart-city-drainage status --short --branch && git -C smart-city-drainage log -1 --oneline` | `## main...origin/main`；`?? docs/`；`ca99e04 chore: remove stray isort diff log`。 |
| `node --version; npm --version; test -d smart-city-drainage/frontend/node_modules` | Node `v25.9.0`；npm `11.12.1`；node_modules present。 |
| `rg -n -i 'hydraulic|simulation|rainfall|forecast|nowcast|swmm|geojson|wkt|network_nodes|pipe_segments|catchments|risk-ranking' smart-city-drainage/frontend/src` | 无输出；没有水力域实现命中。 |
| `find smart-city-drainage/frontend -maxdepth 4 -type f \( -iname '*playwright*' -o -iname '*cypress*' -o -iname '*benchmark*' -o -iname '*performance*' -o -iname '*perf*' \) -print` | 仅 `frontend/node_modules/@types/node/perf_hooks.d.ts`；无项目 e2e/perf 文件。 |
| `rg -n '^@router\.(get|post|put|delete|patch|websocket)' smart-city-drainage/backend/app/api/routes.py \| wc -l` | `31`（30 HTTP + 1 WebSocket，与现状报告一致）。 |
| `npm --prefix smart-city-drainage/frontend test -- --reporter=dot` | exit 1；2 files 中 1 failed/1 passed，8 tests 中 2 failed/6 passed；失败为 `localStorage.clear is not a function`，位置 `LoginPage.test.tsx:24`。 |
| `npm --prefix smart-city-drainage/frontend run lint` | exit 0；仅通过不代表水力 UI/性能通过。 |
| `npm --prefix smart-city-drainage/frontend run format:check` | exit 1；Prettier 报 36 个文件格式问题。 |
| `npm --prefix smart-city-drainage/frontend run build` | **本次未运行**，避免在只写契约任务中生成 dist；历史报告的 build 结果不作为本次通过证据。 |
| 25 fps/API P95/真实 simulation/forecast/SSE/Compose | **本次未运行**；当前无实现/fixture，不能宣称通过。 |

当前本机 Node 是 25，而 CI 固定 Node 20（`.github/workflows/ci.yml:57-61`）；上述 Vitest 失败证明当前环境未绿，但不能直接断言 Node 20 必现。

### 12.2 必须新增的测试资产

#### Contract / unit（Vitest + pytest）

1. 同一 JSON fixture 同时喂给 Pydantic 和 TypeScript：network-map、simulation status、frame、forecast、comparison。
2. `ApiClientError` 保留 401/403/409/410/422/429/503、field errors、request id、Retry-After。
3. FrameCache：同 key promise 去重、current pin、byte LRU、A/B 共享预算、AbortController、410/503 行为。
4. Timeline：fake clock 验证 1/2/4 帧每秒、pause、seek、late RAF reset、末帧停止、A/B 单 clock。
5. viewport index：bbox culling、LOD、critical feature 不被简化/隐藏、screen hit-test。
6. A/B compatibility：topology/time-grid mismatch 409；baseline=0 时 relative null。
7. SSE：缺/坏/过期 JWT、重复 event_id、sequence gap、unknown schema、resync、rate limit；legacy `connected/sensors/alerts` characterization。
8. i18n key parity、route role matrix、按钮 disabled 条件。

#### Integration / e2e（目标 Node 20 + Python 3.11）

- `npm run test:e2e:twin`：viewer 只读；operator submit/cancel；admin seed；`/simulation` 直达权限。
- 50 年 24 h fixture：submit → 0/中间/100 进度 → completed → 289 frames → pause/seek/1x/2x/4x。
- cancel race、timeout/retry、worker restart、retention 410。
- forecast REST + authenticated SSE + alert center 同一 ID + 30 min suppression。
- A/B comparison table 与逐项重算一致；不兼容时不可同步。
- 旧 Dashboard/Map/Alert/SSE/video API characterization zero regression。
- production nginx 下 SSE `curl -N` 首包和 heartbeat。

#### 性能门禁

- `npm run test:perf:twin`：第 5.5 节 fixture 和 60 s 脚本，必须输出 commit、浏览器、硬件、API latency、presented FPS、frame-time p50/p95、long tasks、cache bytes JSON。
- `pytest -m performance`：4 vCPU、5000/10000、24 h/5 min、纯 Python；180 s 必须含 frame serialization/persistence。
- API load：map-data/frame/status/forecast P95≤500 ms；import job 测 enqueue P95 和完成时长，export 测 TTFB 和总时长。
- CI 固定 Node 20/Python 3.11；25 fps job 不允许 `continue-on-error`。

### 12.3 AC 映射与 Definition of Done

| AC/目标 | 必须存在的证据 |
|---|---|
| AC-3 50y/24h 可回放 | 289 帧 index+payload 校验、A/B/单次回放 e2e、timeline 录屏/trace。 |
| 预测/实测/不确定性 | chart fixture、forecast SSE event、REST/SSE/alert ID 一致测试。 |
| AC-6 A/B | comparison JSON、页面指标、逐项重算、compatibility 负例。 |
| 25 fps | `twin-map-<commit>.json` 达到第 5.5 节全部门槛。 |
| P95≤500 ms | load test 原始结果和请求类别；不得把 import/export/SSE 混算。 |
| 零回归 | 旧 route/API/SSE characterization + Vitest/e2e 全绿。 |
| AC-7 | `npm test`、`npm run lint`、`npm run format:check`、`npm run build`、Python 3.11 pytest 全部 exit 0；mypy 不得继续 `continue-on-error`。 |
| 90 天 retention | 过期 fixture 返回 410，UI 停止播放且 aggregate 仍可解释。 |

只有以上证据齐全，才能把本契约状态从 Proposed 改为 Implemented/Verified。
