# 前端垂直切片集成独立审查

- **模块**：`frontend-integration`
- **审查日期**：2026-09-25
- **审查角色**：独立只读代码审查者
- **结论**：**NOT_APPROVED / `approved=false`**
- **严重度汇总**：High 9，Medium 5，Low 1
- **范围**：冻结规格、`frontend-integration` 实现摘要、真实前端源码、真实后端 hydraulic API/DDL/SSE 行为，以及题目列出的聚焦测试。未修改实现代码；本文件是本次唯一写入产物。

## 1. 判定摘要

路由、菜单角色矩阵、Dashboard forecast 动态 chunk 和 Compose 生产 Nginx 的 forecast SSE location 已经存在；但完整垂直切片仍有九个高风险问题：

1. `/simulation` 同时建立两条 forecast SSE，其中一条还把 forecast 事件误当成 task invalidation；
2. fetch-SSE 对 401/403 不终止认证恢复，而是持续重连；
3. 仿真提交硬编码不存在的 parameter-set UUID，和生产 FK 契约冲突；
4. 页面组合后的 rainfall create 每次都会因缺少必填 props 被编辑器自身拒绝；
5. timeline prefetch 与 current frame 共用同一加载函数，导致预取帧覆盖当前地图并取消其他预取；
6. 10k 管段目标只取每层前 2,000 条，且页面不显示 `complete=false/truncated_layers`；
7. A/B 图表数据恒为空，预测/实测/不确定性图未渲染，A/B 地图模式不存在；
8. Dashboard forecast 网络切换缺少 abort/generation，旧网络 snapshot 可写入新网络页面；
9. forecast、task、rainfall 新 UI 的实际 i18n key/文案路径不符合冻结 key 空间。

因此不能把当前工作台标记为“完整页面”，也不能宣称 AC-3、AC-6、角色/SSE/性能回归已经闭环。

## 2. 契约基线

- 用户规格要求接入 `/simulation` 路由、菜单角色守卫与 i18n，组合地图/时间轴/任务/雨型/A-B/图表，Dashboard 懒加载 forecast 与风险榜，并保护现有页面与 SSE 重连。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1899-1905` 要求 viewer 只读 `/simulation`、authenticated layout、统一角色矩阵和全部新 UI 文案 i18n。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1913-1938` 要求单 Canvas、服务端 bbox/zoom、typed-array/FrameCache、当前帧与预取分离、checksum 后绘制。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1940-1957` 要求 1/2/4 fps、A/B 单 clock、预测/实测/uncertainty 图表。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1959-1964` 要求单一 bearer fetch-SSE provider/connection；task/forecast 不得各建流。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2136-2150` 要求 `/api/forecast/events` 关闭 buffering/cache 并透传 Authorization。
- `docs/hydraulic-twin/SOURCE_PRD.md:24-30` 的 FR-5/FR-6 明确要求仿真提交、完整 `/simulation`、A/B、预测实测、不确定性带和 10k/1k 地图容量。

## 3. 本次实际执行的检查

### 3.1 题目列出的聚焦前端测试：通过

命令：

```text
npm --prefix '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend' test -- --run src/auth/routeAccess.test.ts src/features/simulation/integration/networkApi.test.ts src/features/simulation/integration/ForecastRiskPanel.test.tsx src/features/simulation/integration/ForecastStreamBoundary.test.tsx src/features/simulation/test/forecastDashboard.test.tsx src/features/simulation/test/taskStream.test.tsx src/features/simulation/test/mapCore.test.ts src/features/simulation/test/mapFixture.test.ts src/features/simulation/test/timelinePlayer.test.ts src/features/simulation/test/timelineControl.test.tsx src/features/simulation/test/chart-comparison.test.tsx src/test/__tests__/LoginPage.test.tsx --reporter=dot
```

实际结果：exit 0；`Test Files 12 passed (12)`，`Tests 52 passed (52)`，duration `5.50s`。输出有 React `act` deprecated、`--localstorage-file` 和 React Router future flag 警告。

这只证明列出的局部断言通过；没有渲染真实 `SimulationPage`，也没有覆盖下述组合失败。

### 3.2 静态集成探针：确认多个阻断条件

本轮运行了四个 `node --input-type=module` 只读内联探针，分别读取真实源码/JSON；关键输出为：

```json
{
  "i18nParity": { "onlyEn": [], "onlyZh": [] },
  "translationPaths": {
    "forecastUsesSimulationForecast": true,
    "enHasSimulationForecast": false,
    "enHasTopLevelForecast": true,
    "taskUsesSimulationTasks": true,
    "enHasSimulationTasks": false,
    "enHasTopLevelTasks": true
  },
  "verticalSlice": {
    "comparisonSeriesFixedEmpty": true,
    "observedChartImported": false,
    "observedPairsRendered": false,
    "mapLimit2000": true,
    "pageHandlesMapCompleteness": false
  }
}
```

```json
{
  "rainfallCreate": {
    "pagePassesOnCreateScenario": true,
    "pagePassesNetworkId": false,
    "pagePassesIdempotencyKey": false,
    "editorRejectsMissingProps": true
  },
  "taskControllers": {
    "pageCreatesOuterController": 1,
    "panelCreatesInnerController": 1,
    "pageRendersTaskPanel": true
  }
}
```

```json
{
  "pageUsesFixedParameterSet": true,
  "fixedIdOccurrences": ["frontend/src/pages/SimulationPage.tsx"],
  "durableModelHasParameterSetForeignKey": true
}
```

```json
{
  "composeProductionNginx": {
    "exactForecastLocation": true,
    "bufferingOff": true,
    "cacheOff": true,
    "connectionEmpty": true,
    "authorizationForwarded": true
  },
  "standaloneNginxHasForecastLocation": false,
  "viteDevMatchesForecastEndpoint": false,
  "sseAuthBranches": {
    "sharedProviderHandles409": true,
    "sharedProviderHandles401": false,
    "forecastHookHandles409": true,
    "forecastHookHandles401": false
  }
}
```

### 3.3 Forecast 懒加载内存构建诊断：通过

命令使用 Vite JS API、`build.write=false`，未执行 `tsc -b`、未写 `dist`。实际输出：

```json
{
  "forecast": "assets/ForecastDashboard-Cof1Th_4.js",
  "importers": [
    {
      "fileName": "assets/ForecastStreamBoundary-BCugrJbD.js",
      "dynamicImports": [
        "assets/ForecastDashboard-Cof1Th_4.js",
        "assets/ForecastDashboard-Cof1Th_4.js"
      ]
    }
  ]
}
```

因此 Dashboard forecast 实现确实位于非入口动态 chunk；这证明代码分割存在，但不证明 REST/SSE、网络隔离或 Dashboard 状态正确。构建还输出了 Tailwind `content` 警告；本次未运行最终全量 build，不据此追加阻断项。

### 3.4 Heartbeat parser 定向探针：失败语义确认

通过 Vite SSR 加载真实 `parseForecastSse`：

```json
{ "heartbeat": null, "empty": null }
```

后端实际发送 comment heartbeat（`backend/app/services/sse_manager.py:517-521,560-570`），而 shared provider 将 parser `null` 解释为 `invalid_event` 并触发 resync，详见 H-02/M-02。

### 3.5 未运行

按“不要重复最终全量门禁”的要求，本次**未运行**：

- 全量 `npm test`；
- 全量 ESLint / Prettier / `npm run format:check`；
- 完整 `npm run build`（含 `tsc -b`）；
- 真实 FastAPI/PostgreSQL/Redis/worker 联调；
- 真实浏览器 E2E、60 秒 pan/zoom、presented FPS、RAF p95、long task、API P95；
- production Nginx `curl -N` heartbeat。

因此不能宣称 AC-7、25 fps、FrameCache 256 MiB、真实 SSE 重连或旧页面端到端零回归通过。

## 4. Findings

### H-01（高）— `/simulation` 建立两条 forecast SSE，并把 forecast 事件误路由为 task invalidation

- **文件**：`frontend/src/pages/SimulationPage.tsx:81-104,204-206,503-504`；`frontend/src/features/simulation/integration/ForecastStreamBoundary.tsx:14-22`；`frontend/src/features/simulation/hooks/useSimulationStream.ts:7,118-120,177-211`。
- **问题**：`SimulationPage` 在组件顶层调用 `useSimulationStream()`，而 `HydraulicRealtimeProvider` 是该组件返回树中的后代；React context 不会向 provider 的父组件回流，所以顶层 `useOptionalHydraulicRealtime()` 实际看不到它。页面因此同时有 provider 的 forecast fetch-SSE 和直接 hook 的第二条 fetch-SSE。
- **更严重的行为**：直接 hook 的默认 endpoint 也是 `/api/forecast/events`，并接受 `event: forecast`。真实 forecast envelope 带 `simulation_id`（`backend/app/api/hydraulic_routes.py:3850-3857`）；hook 在 `useSimulationStream.ts:204-211` 对其调用 `onInvalidate(simulation_id)`，页面又在 `SimulationPage.tsx:85-87` 用该 ID 设置 `selectedTaskId`。一条 forecast 事件可因此选中一个并非当前任务列表/当前任务的 simulation。
- **影响**：违反冻结的单连接契约；重复 cursor、seen-id、backoff 和限流；forecast 与 task 状态域串线，时间轴可能尝试加载被 forecast 事件指定的 simulation。
- **建议**：把唯一 `HydraulicRealtimeProvider` 放在 authenticated layout 或至少 `SimulationPage` 父级；删除页面直连 `useSimulationStream`；task controller 订阅 provider 的 task invalidation，或按冻结事实源仅用 REST polling。增加同时挂载 forecast/task consumer 的集成测试，断言底层 `fetch('/api/forecast/events')` 只有一次且 forecast event 不改变 task selection。

### H-02（高）— fetch-SSE 对 401/403 没有终止认证恢复，会用坏 token 无限重连

- **文件**：`frontend/src/features/simulation/realtime/HydraulicRealtimeProvider.tsx:145-188`；`frontend/src/features/simulation/forecast/stream.ts:203-250`；对照 `frontend/src/services/api.ts:26-38`。
- **问题**：两个 fetch-SSE 实现只特殊处理 409 cursor；其他非 2xx 都抛 generic error并进入指数重连。没有 401 清认证并跳登录，也没有 403 terminal forbidden/停止重连。legacy Axios 的 401 清理逻辑不会被 fetch 绕过自动继承。
- **证据**：本轮静态探针输出 `sharedProviderHandles401=false`、`forecastHookHandles401=false`。
- **影响**：token 过期后页面无限显示 reconnecting，既不清除失效认证态，也不回到登录；403 角色不足同样会反复打服务端。两条重复流会进一步放大请求。
- **建议**：在共享 transport 中统一处理 401（只执行一次 auth cleanup + `/login`）、403（停止并显示权限错误）、429（`Retry-After`）和 5xx（指数退避）；增加 401/403 fetch mock，断言不重连。

### H-03（高）— 仿真提交硬编码不存在的 parameter-set UUID，与生产 FK 契约冲突

- **文件**：`frontend/src/pages/SimulationPage.tsx:38-57`；生产模型 `backend/app/models/hydraulic_models.py:1383-1390`；冻结 schema `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:669,1576-1596`。
- **问题**：`buildSubmitRequest()` 无条件使用 `00000000-0000-4000-8000-000000000001`。全仓真实源码诊断显示该固定 ID 只出现在 `SimulationPage.tsx`；生产 `hydraulic_simulations.parameter_set_id` 是到 `parameter_sets.id` 的非空 FK。
- **影响**：开发内存 adapter 可能只校验 UUID 形状而暂时接受；真实 durable repository/数据库没有对应 seed row 时，operator 的提交无法落库，完整仿真提交路径不成立。
- **建议**：增加/复用 typed parameter-set 列表或由后端返回 network/revision 可用的 default parameter-set ID；页面必须从 API 选择真实 ID，禁止固定 UUID。用 FK-backed fixture 断言 submit body 和 durable response。

### H-04（高）— 页面组合后的 rainfall create 每次都会被编辑器自身拒绝

- **文件**：`frontend/src/pages/SimulationPage.tsx:420-431`；`frontend/src/features/simulation/rainfall/RainfallScenarioEditor.tsx:252-267`。
- **问题**：页面传了 `onCreateScenario`，却没有传 editor 要求的 `networkId` 和 `idempotencyKey`。editor 在调用回调前硬性检查这两个 prop，缺失即抛 `networkId and idempotencyKey are required`。
- **证据**：本轮探针输出 `pagePassesOnCreateScenario=true`、`pagePassesNetworkId=false`、`pagePassesIdempotencyKey=false`、`editorRejectsMissingProps=true`。
- **影响**：FR-6 rainfall editor 在真实页面无法创建情景；实现单元测试只覆盖 `onSubmit` 路径（`features/simulation/test/rainfallEditor.test.tsx:101-115`），没有覆盖页面使用的 `onCreateScenario` 路径。
- **建议**：让页面传入真实 network ID 和稳定 idempotency key，或把 command 构建职责收回页面并删除 editor 对已由 command 携带信息的重复 prop 要求；增加 `SimulationPage → editor → rainfallApi` 测试。

### H-05（高）— timeline prefetch 与 current frame 共用加载函数，预取会覆盖当前地图并取消其他预取

- **文件**：`frontend/src/features/simulation/integration/useHydraulicWorkspace.ts:169-195`；`frontend/src/pages/SimulationPage.tsx:331-360`；`frontend/src/features/simulation/hooks/useTimelinePlayer.ts:160-180,381-406`；`frontend/src/features/simulation/map/layerController.ts:79-95`。
- **问题**：`TimelineControl` 的 `prefetchFrame` 与 `onFrameChange` 都调用 `workspace.loadFrame()`。该函数每次先 abort 上一个请求，然后无论调用来自 current 还是 prefetch，都 `setCurrentFrame(frame)`。因此播放标签可停在 frame N，而 next/previous 预取完成后地图切到 N+1/N-1；连续预取又互相 abort，播放器声明的最多 2 并发被降为 1。
- **缓存路径**：虽然 controller 构造了 `FrameCacheClient`，`loadFrame()` 实际直接 `encodeFrame + cache.put`，没有用 `getOrLoad` 做 in-flight 去重/并发调度，所以 REST 预取仍重复请求。
- **影响**：时间标签与画面不一致、预取几乎失效、拖拽/4x 播放时额外请求和主线程编码压力；不符合 `IMPLEMENTATION_CONTRACT.md:1930-1938` 的 P0/P1 分离和 256 MiB cache 调度契约。
- **建议**：拆成 `loadCurrentFrame(P0)` 与 `prefetchFrame(P1)`；只有 current 更新 React frame；所有请求进入共享 `FrameCacheClient.getOrLoad`，以 canonical key 去重/取消/ pin；network、simulation、revision 切换使用 generation/abort 失效旧结果。

### H-06（高）— 10k 管段目标被静默截成每层 2,000 条，页面仍按完整地图展示

- **文件**：`frontend/src/features/simulation/integration/useHydraulicWorkspace.ts:110-140`；`frontend/src/pages/SimulationPage.tsx:276-280,301-328`；真实 API `backend/app/api/hydraulic_routes.py:2384-2469`。
- **问题**：页面固定 `zoom=13`、`limit=2000`，不传 bbox，也不消费 `next_cursor`。真实 API 每层最多返回 2,000，并在超出时设置 `complete=false` 和 `truncated_layers`；目标 fixture 的 5,000 nodes/10,000 pipes 必然被截断。页面只展示 `networkError/mapError`，不显示 completeness、source/returned counts 或 partial warning。
- **证据**：本轮探针输出 `mapLimit2000=true`、`pageHandlesMapCompleteness=false`；仓库生产搜索对 `SimulationPage.tsx` 的 `truncated_layers|returned_counts|mapData.complete` 无命中。
- **影响**：用户看到的是缺少 3,000 nodes/8,000 pipes 的地图，却没有任何“部分/已简化”提示；地图判断、风险定位和 25 fps 容量结论都不能基于该页面成立。
- **建议**：用当前 viewport bbox/zoom 拉取并按 cursor 合并所需层，或在明确 partial 状态只显示 source/returned counts；`complete=false` 时显示本地化警告并禁止“全量”语义。为 5k/10k/1k 页面级 fixture 增加断言。

### H-07（高）— A/B/预测实测图表仍是占位，A/B 地图模式不存在

- **文件**：`frontend/src/pages/SimulationPage.tsx:70-72,136-153,170-183,371-381,438-450`；`frontend/src/features/simulation/integration/ComparisonWorkbench.tsx:142-163`；`frontend/src/features/simulation/charts/SimulationComparisonChart.tsx:24-52`。
- **问题**：`comparisonSeries` 以 `useState([])` 初始化且没有 setter，comparison API 只提供 metrics；图表和 DifferenceLegend 永远收到空数组。`loadObservedComparison()` 返回的 `pairs` 被丢弃，只留下 `status` 字符串，页面没有导入/渲染已有的 `ObservedPredictedChart`。地图仍只渲染一个 `currentFrame`，没有 A/B/Δ map mode 或两侧 frame/共享 clock。
- **证据**：本轮探针输出 `comparisonSeriesFixedEmpty=true`、`observedChartImported=false`、`observedPairsRendered=false`；生产源码搜索中 `ObservedPredictedChart` 仅在自身组件/barrel 命中。
- **影响**：AC-6 只能看到指标表，不能查看 A/B 时序或空间差异；FR-6 的预测/实测与不确定性带没有用户可达实现。空 ECharts 仍会被创建，容易让“chart component 已组合”误认为图表已完成。
- **建议**：提供 typed 时序加载（comparison series/observed pairs），把数据传入现有 modular charts；实现单地图 A/B/Δ 模式和 A/B 同步播放，未通过 compatibility 时禁用 Δ/同步。增加真实 DTO → table/chart/map 的集成测试。

### H-08（高）— forecast Dashboard 网络切换没有取消/代次，旧网络风险 snapshot 可写入新网络

- **文件**：`frontend/src/features/simulation/forecast/ForecastDashboard.tsx:107-131,133-170`。
- **问题**：`refresh()` 直接在 await 后 `setSnapshot/setRiskRanking`，没有 AbortController 或 network generation；外层 effect 的 `active` 只控制 catch/finally，不控制 `refresh()` 内部 state commit，也没有在 `networkId` 变化时清空旧 snapshot/risk。
- **影响**：A→B 切换时，A 的迟到响应可在 B 页面提交 A 的 snapshot；若 B 请求失败，组件仍保留并渲染 A 的 ready 数据，形成跨网络风险误导。网络隔离是 fail-closed 边界，不能依赖响应自身 `network_id` 校验，因为 A 的响应对自己捕获的 A 参数本来合法。
- **建议**：为每个 network 建 generation/AbortController；network 变化立即清空 snapshot/risk/loading，提交前同时检查 generation、networkId 和 signal；增加 deferred A→B→resolve A 的 rerender 测试。

### H-09（高）— 新 simulation UI 的 i18n key/文案路径不符合冻结 key 空间

- **文件**：`frontend/src/features/simulation/forecast/ForecastDashboard.tsx:67-105`；`frontend/src/features/simulation/tasks/TaskPanel.tsx:122-129,305,400-437`；`frontend/src/features/simulation/rainfall/RainfallScenarioEditor.tsx:69-98,284-499`；`frontend/src/i18n/en.json:121-245`；`frontend/src/i18n/zh.json:121-245`。
- **问题**：forecast 组件请求 `simulation.forecast.*`，资源只有顶层 `forecast.*`；task panel 请求 `simulation.tasks.*`，资源只有顶层 `tasks.status.*`。rainfall editor 的标题、按钮、错误、验证和表格文案全部硬编码英文。en/zh 文件自身 key parity 为空差异，但调用路径不匹配，因此 parity 测试无法发现运行时 fallback。
- **运行结果**：英文环境的 ForecastDashboard 会回退到组件内中文 default；中文环境的任务面板会回退到 `DEFAULT_LABELS` 英文或原始英文 status；rainfall 始终是英文硬编码。
- **影响**：`IMPLEMENTATION_CONTRACT.md:1903-1905` 的 simulation/rainfall/network/map/timeline/forecast/risk/comparison/errors i18n 门禁未满足，语言切换不能正确覆盖新增工作台。
- **建议**：统一 key 层级并通过 labels/props 注入所有 feature UI；删除新组件硬编码文案；增加递归 key parity、missing-used-key 和 en/zh 实际渲染断言，而不只比较两个 JSON。

## 5. Medium findings

### M-01（中）— 同一页面创建两个 task controller，轮询和状态源重复

- **文件**：`frontend/src/pages/SimulationPage.tsx:90-111,391-398`；`frontend/src/features/simulation/tasks/TaskPanel.tsx:85-119`。
- **问题**：页面为 A/B/选择维护一个 `useSimulationTask`，`SimulationTaskPanel` 内部又无条件创建第二个。两个 controller 都维护 list/detail polling；panel 内 submit/cancel/retry 只更新内层状态，页面的 `tasks`/A-B options 依赖另一轮 REST 才收敛。
- **证据**：本轮探针输出 `pageCreatesOuterController=1`、`panelCreatesInnerController=1`、`pageRendersTaskPanel=true`。
- **影响**：请求量翻倍，暂时显示不同任务状态，A/B 选项可能延迟到下一次 30s safety poll。
- **建议**：把 controller 提升到页面并把 state/actions 作为受控 props 传给 panel，或让 panel 完全拥有任务状态并通过 callback 向页面发布 canonical snapshot。

### M-02（中）— 正常 SSE heartbeat 被当成协议错误并触发 REST resync

- **文件**：`frontend/src/features/simulation/forecast/stream.ts:69-127`；`frontend/src/features/simulation/realtime/HydraulicRealtimeProvider.tsx:103-108`；后端 heartbeat `backend/app/services/sse_manager.py:517-521,560-570`。
- **问题**：真实 heartbeat 是 `: heartbeat ...` comment；`parseForecastSse` 返回 null，provider 随即 `notifyResync('invalid_event')`。
- **证据**：本轮 Vite SSR 真实 parser 探针输出 `{"heartbeat":null,"empty":null}`。
- **影响**：稳定连接每约 15 秒都会触发 latest/risk-ranking 重取；REST 抖动时还会反复显示错误，制造额外 API 压力。
- **建议**：在 transport 层先识别 comment/empty keepalive 并直接忽略；只对非空 data block 做 protocol validation。补 heartbeat 不触发 onResync 的测试。

### M-03（中）— integration 默认 frame cache key 不是 canonical key，且未强制使用 manifest checksum

- **文件**：`frontend/src/features/simulation/integration/HydraulicMapPanel.tsx:108-121`；canonical helper `frontend/src/features/simulation/map/frameCache.ts:344-362`；校验 `frontend/src/features/simulation/map/frameCache.ts:124-165`。
- **问题**：默认 key 是 `networkId/networkRevisionId/frame_index`，第一段用了 network 而不是 simulation，也没有 fields hash。workspace 虽取得 frame index，却不把对应 checksum 传给 map controller；checksum 缺失时当前 validator 允许绘制。
- **影响**：同网络不同 simulation/frame 的 key 可碰撞，缓存无法正确去重；缺 checksum 或传输损坏的 frame 仍可能绘制，违背“完整校验后才绘制”。
- **建议**：统一调用 `frameCacheKey(simulationId, frameIndex, fields, revisionId)`，并将 frame index entry 的 checksum 传为 `expectedChecksum`；增加跨 simulation key 和 missing/bad checksum 负例。

### M-04（中）— 颜色指标可选 fill ratio/velocity，但图例始终是积水深度

- **文件**：`frontend/src/features/simulation/integration/useHydraulicWorkspace.ts:66-68`；`frontend/src/pages/SimulationPage.tsx:240-250,317-327`。
- **问题**：默认 metric 是 `fill_ratio`，页面也允许 depth/velocity，但地图下方始终渲染 `DepthLegend` 的 0–0.05/0.15/0.30 m 阈值。
- **影响**：管段按充满度或流速着色时，图例仍解释为水深，运营人员可能读错颜色；不满足非颜色编码和单位说明契约。
- **建议**：按当前 metric 动态渲染对应 fill-ratio/depth/velocity/surcharge 图例和单位，并补 metric 切换测试。

### M-05（中）— 现有测试只覆盖局部 primitive，没有组合页面边界

- **文件**：`frontend/src/features/simulation/integration/ForecastStreamBoundary.test.tsx:4-13,19-42`；`ForecastRiskPanel.test.tsx:4-25`；`frontend/src/features/simulation/test/rainfallEditor.test.tsx:101-115`。
- **问题**：boundary test mock 掉真实 `HydraulicRealtimeProvider` 并断言 fallback hook 被调用；panel test mock 掉真实 forecast card；rainfall test只走 `onSubmit`。仓库没有 `SimulationPage`、`useHydraulicWorkspace`、`ComparisonWorkbench` 的组合测试。
- **影响**：52 个聚焦测试全绿仍无法发现双 SSE、forecast→task 串线、旧网络 snapshot、map partial、图表空数据、rainfall prop 拒绝等高风险问题。
- **建议**：增加 route/page 组合测试和 deferred Promise/网络切换测试；SSE 测试必须保留真实 provider，只 mock `fetch` reader；rainfall 测试必须走页面实际 `onCreateScenario` 形状。

## 6. Low finding

### L-01（低）— owned `frontend/nginx-default.conf` 与实际 Compose 生产配置漂移

- **文件**：`frontend/nginx-default.conf:28-40`；`frontend/vite.config.ts:24-39`；实际生产 `docker/frontend/nginx-default.conf:28-45`；`docker-compose.yml:279-284`；`docker/frontend/Dockerfile:9-13`。
- **证据**：实际 Compose 通过 additional context 复制 `docker/frontend/nginx-default.conf`，该文件有精确 forecast location、关闭 buffering/cache、透传 Authorization，Compose 生产 SSE proxy 是正确的。owned `frontend/nginx-default.conf` 没有该 location；Vite dev 条件仍只匹配 `/sse/`，不匹配 `/forecast/events`。
- **影响**：当前 Compose 镜像不因该漂移阻断，但其他使用 frontend 根配置或 dev proxy 的部署可能复现缓冲/keepalive 差异。
- **建议**：删除重复配置或以生成/测试方式保持一致；dev 条件按 canonical `/api/forecast/events` 判断；不要把 standalone 文件误当 Compose 实际来源。

## 7. 已确认的正向结果

- `App.tsx` 已集中 authenticated layout，认证页使用 lazy import，`/simulation` 对 viewer+ 开放。
- 菜单与 route matrix 当前一致；video/alerts 为 operator+、settings 为 admin+，后端仍是最终 RBAC。
- Dashboard 的 `ForecastDashboard` 实现已形成独立动态 chunk；本次内存 Vite 诊断确认 `ForecastStreamBoundary` 动态 import 它。
- `Dashboard` 仍保留 legacy `useSSE` 路径，没有直接把水力 forecast 写入 legacy alert/sensor store。
- Compose 实际使用的 `docker/frontend/nginx-default.conf` 已满足 forecast SSE proxy 的关键 buffering/cache/Authorization 要求。
- `localStorage.clear` 测试 shim 修复有效；题目列出的 LoginPage 与 12 个聚焦测试文件本次均通过。

## 8. 最终决定

**`approved=false`。** 高风险问题未解决，不能发布或把 `frontend-integration` 标记为完成。优先修复顺序：

1. H-01/H-02：唯一 SSE owner、forecast/task 域隔离、401/403 terminal auth；
2. H-03/H-04：真实 parameter set、rainfall create 可用；
3. H-05/H-06：current/prefetch/cache 分离和 partial map 可见性；
4. H-07/H-08：A/B/observed 实际数据流与 network generation；
5. H-09/M-01～M-05：i18n 接线、单 task controller、heartbeat、canonical frame identity、页面级测试。

本审查没有运行最终全量门禁或真实浏览器/后端性能验收；上述通过项不能替代 AC-3/AC-6/AC-7/G5。
