# 前端验收测试补齐独立审查

- 模块：`frontend-acceptance-tests`
- 审查日期：2026-09-25
- 结论：**NOT_APPROVED / 不批准**
- 审查范围：只读复核 `frontend/src/test/acceptance/**`、`frontend/e2e/**`、`frontend/performance/**` 及其直接使用的真实前端实现；未修改生产代码或测试代码。

## 结论摘要

本轮实际执行的定向 Vitest 为 **3 个文件、14 个测试通过**，浏览器 smoke 为 **3/3 scenarios PASS**，独立 Node 性能 smoke 为 **PASS**。但这些 PASS 不能作为冻结前端验收证据：

1. 浏览器 e2e 只验证 DOM 元素存在和 API 路径，未执行编辑、播放、A/B、预测告警/SSE 等用户行为；fixture 的任务、降雨、地图、forecast 数据为空，禁用控件也会通过。
2. 冻结的 `npm --prefix frontend run test:perf:twin -- ... --duration-seconds 60` 入口不存在；现有脚本明确标记 `NON_ACCEPTANCE_SMOKE`，没有浏览器 FPS/RAF/long-task/Canvas/API 证据。
3. 该 smoke 用调用方指定的 duration 作吞吐分母，且只统计本地随机数组的 value 字节，能够在错误/不完整的性能模型下返回 PASS。
4. 新增 forecast stream 测试绕过 `/simulation` 实际使用的共享 `HydraulicRealtimeProvider`；同时发现相同版本的 forecast 事件可把已 `resolved` 的告警覆盖回 `open`，而新增用例没有覆盖该边界。
5. e2e/performance JSON 文件本身没有写出契约要求的 `artifact_sha256` 字段。

因此至少存在多个高风险测试/证据缺口，不能将本模块标记为已验收。

## 本轮实际执行的检查

以下检查均在本轮执行；没有把作者摘要中的未复核全量结果当作本轮结果。

### 1. 定向 Vitest（通过）

命令：

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend" && npx vitest run src/test/acceptance/frontend-acceptance.test.tsx src/test/acceptance/stream-cleanup.test.ts src/test/acceptance/performance-scale.test.ts --reporter=dot
```

结果：exit 0；`Test Files 3 passed (3)`；`Tests 14 passed (14)`。仅有既有 React `act` deprecation、React Router future flag 和 Node `--localstorage-file` 警告。该结果只证明这三个文件的断言通过，不证明浏览器或正式性能门禁通过。

### 2. 浏览器 e2e smoke（通过，但覆盖不足）

命令：

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend" && node e2e/run-ac-smoke.mjs --fixture frontend/e2e/fixtures/frontend-acceptance-v1.json --output ../artifacts/frontend-acceptance/e2e-browser.json --timeout-seconds 120 --scenario-timeout-seconds 20
```

结果：exit 0；`AC_E2E_STATUS=PASS`；3/3 scenarios PASS；stdout artifact SHA-256 为 `4de53ab089402b9e737d36669753863f4cffee6c740bb91c8494b7da31f77ef7`。产物 `artifacts/frontend-acceptance/e2e-browser.json:18-20,50-74,176-181,233-256` 只记录 `selector_visible`/`path_equals` 和请求路径，没有行为动作、值变化或 disabled 状态断言。

### 3. Node 性能 smoke（命令通过，但不是验收门禁）

命令：

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend" && node performance/run-twin-map.mjs --fixture frontend/performance/fixtures/twin-map-5k-10k-v1.json --duration-seconds 1 --output ../artifacts/review-frontend-perf-smoke.json
```

结果：exit 0；`FRONTEND_PERFORMANCE_SMOKE_STATUS=PASS`，但报告 `artifacts/review-frontend-perf-smoke.json:25-29,39-50,52-73` 明确为 `NON_ACCEPTANCE_SMOKE`/`NOT_EVALUATED`，并列出所有浏览器指标未验证。关键输出为 `duration_seconds=1`、`actual_execution_seconds=30.4047491`、`feature_operations_per_simulated_second=3946874`、`cache_peak_bytes=18496000`、`evictions=0`。

### 4. 冻结性能入口检查（失败）

命令：

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend" && npm run test:perf:twin -- --fixture frontend/performance/fixtures/twin-map-5k-10k-v1.json --duration-seconds 60 --output ../artifacts/review-frontend-perf-gate.json
```

结果：exit 1，`npm error Missing script: "test:perf:twin"`。

### 5. 负向诊断（用于验证测试盲区，不是修改代码）

相同版本 forecast 终态覆盖探针：

```text
node --experimental-strip-types --input-type=module -e "const {applyForecastEvent}=await import('./src/features/simulation/forecast/ranking.ts'); const base={id:'a',type:'flood_forecast',status:'resolved',phase:'predicted',target:{type:'node',id:'n'},expected_at:'2026-09-24T00:30:00Z',lead_time_minutes:30,measurements:{},confidence:1,confidence_method:'x',risk_score:1,version:2}; const e={schema_version:1,event_id:'same',sequence:3,event_type:'alert.updated',occurred_at:'2026-09-24T00:01:00Z',network_id:'n',nowcast_run_id:'r',simulation_id:'s',alert:{...base,status:'open',version:2}}; console.log(JSON.stringify(applyForecastEvent([base],e,'n')));"
```

输出把 `status:"resolved", version:2` 改成了 `status:"open", version:2`。

小缓存探针：

```text
node --experimental-strip-types --input-type=module -e "... new FrameCache(16 * 1024) ... 放入 3 个单 feature frame ..."
```

输出：`{"maxBytes":16384,"size":3,"sizeBytes":54,"keys":["f0","f1","f2"]}`，没有发生 eviction。

artifact 字段探针：

```text
node -e "const fs=require('fs'); for (const p of ['C:/Users/zyq/Documents/opencode-pn/goal测试/artifacts/frontend-acceptance/e2e-browser.json','C:/Users/zyq/Documents/opencode-pn/goal测试/artifacts/review-frontend-perf-smoke.json']) { const j=JSON.parse(fs.readFileSync(p,'utf8')); console.log(p, 'artifact_sha256' in j, j.evidence_sha256); }"
```

输出：两份 JSON 均为 `artifact_sha256 in j false`，只有 `evidence_sha256`。

## Findings

### F-01 — High：浏览器 e2e 是存在性 smoke，空 fixture 可掩盖核心行为回归

**问题**：`selector_visible` 实际只执行 `Boolean(document.querySelector(selector))`，不检查元素是否可见、是否 disabled、是否可点击；fixture 也没有任何 click、keyboard、文本值或状态转换断言。因而 editor submit、timeline seek/play、A/B difference、预测/实测图和 forecast SSE 状态即使失效，e2e 仍可能 PASS。API 匹配还只保留 method/path，丢弃 query、headers 和 body。

**证据**：

- `frontend/e2e/run-ac-smoke.mjs:648-675`：`selector_visible` 只返回 `Boolean(document.querySelector(...))`，支持类型只有可见存在、文本、路径和数量。
- `frontend/e2e/run-ac-smoke.mjs:679-680`：expected request key 只有 `METHOD path`。
- `frontend/e2e/fixtures/frontend-acceptance-v1.json:13-145`：三个场景只列 selector/path 断言，没有用户动作。
- `frontend/e2e/fixtures/api-v1/current-tasks.json:1-3`、`current-rainfall.json:1-3`：任务和降雨列表为空。
- `frontend/e2e/fixtures/api-v1/current-map.json:13-21`：所有 source/returned feature counts 为 0。
- `frontend/e2e/fixtures/api-v1/current-forecast.json:1-8`：alerts 为空。
- 真实页面在 `frontend/src/pages/SimulationPage.tsx:561-573` 将无 baseline/candidate/comparison 时的 difference 按钮设为 disabled，在 `:791-801` 将 viewer/无 submitRequest 的提交按钮设为 disabled；这些状态不会被 e2e 检查。
- 本轮 e2e 输出仍为 3/3 PASS（`artifacts/frontend-acceptance/e2e-browser.json:18-20,129-171,233-256`）。

**影响**：不能证明 AC-7 所要求的用户行为、read-only 权限或预测/实测/A-B 数据链路；禁用控件、空图表和空告警都可被记为通过。

**修复建议**：使用非空且可交互的固定任务/降雨/map/forecast fixture；增加 click/keyboard、enabled/disabled、属性值、图表 phase/series、请求 query/header/body、SSE 事件到 UI 状态的断言，并让 runner 拒绝隐藏元素和缺失动作。

### F-02 — High：冻结的 G5 性能入口不存在，现有脚本不是浏览器验收 harness

**问题**：冻结契约要求的 `test:perf:twin` npm 入口在 `package.json` 中不存在；当前 `run-twin-map.mjs` 明确自标为 `NON_ACCEPTANCE_SMOKE`，不测 production build、presented FPS、RAF frame-time、浏览器 long task、Canvas renderer 或 map/frame API latency。报告还在 Node 25.9.0、32 logical CPUs、约 59 GiB RAM 上运行，不是契约要求的 Node 18.20.8、4 physical cores/8 GiB/无 GPU 环境。

**证据**：

- `smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2502-2526` 固定了 60 秒命令、browser fixture 和 `presented FPS >=25`、RAF P95 `<=50ms`、无 `>200ms` long task、cache `<=256MiB` 门槛。
- `smart-city-drainage/docs/hydraulic-twin/PRD_SUPPLEMENT.md:437-452` 要求 production build、4 vCPU/8 GiB、1920×1080、连续 60 秒浏览器 workload。
- `frontend/package.json:6-25` 只有 `test`、`test:watch` 等脚本，没有 `test:perf:twin`。
- 本轮执行冻结命令 exit 1：`npm error Missing script: "test:perf:twin"`。
- `frontend/performance/run-twin-map.mjs:488-512` 将自身标为 `NON_ACCEPTANCE_SMOKE` 并列出未验证指标；报告 `artifacts/review-frontend-perf-smoke.json:25-50` 与此一致。
- 报告 `artifacts/review-frontend-perf-smoke.json:34-38` 记录 `node_version:"v25.9.0"`、`cpu_count:32`、`ram_bytes:59252035584`。

**影响**：不能用该 PASS 宣称万级数据达到 25 FPS 或满足 G5；性能交付物在冻结入口层面实际不可执行。

**修复建议**：补齐冻结 npm entrypoint 和真实 Chromium/CDP harness，锁定依赖/环境并输出完整机器证据；若本轮不实现，应明确保持 G5/相关 AC 为 `NOT RUN`，不要把 standalone smoke 当作验收证据。

### F-03 — High：性能 smoke 的吞吐分母和 cache 计量都可产生假 PASS

**问题**：`evaluate()` 用调用方传入的 `durationSeconds` 计算 operations/sec，而不是实际执行时长；本轮请求 1 秒却实际执行 30.4047 秒，仍报告 3,946,874 operations/sec。smoke 还自行生成随机数组、使用本地简化 cache，只按 `Float32Array` value 字节计数，遗漏 production `CachedSeries` 的 feature index、flags、元数据和静态 geometry。它没有调用真实 `ViewportIndex`、Canvas renderer 或 production `FrameCache`。

**证据**：

- `frontend/performance/run-twin-map.mjs:293-336` 从 seed 生成随机 nodes/pipes/catchments，并建立脚本内的 `FrameCache`；不是 fixture 中的真实几何。
- `frontend/performance/run-twin-map.mjs:427-457` 的 `feature_operations_per_simulated_second` 使用传入 duration。
- `frontend/performance/run-twin-map.mjs:527-541` 用 `options.durationSeconds` 调用 evaluate，而不是 `actualDurationSeconds`。
- 本轮报告 `artifacts/review-frontend-perf-smoke.json:25-29,52-73`：`duration_seconds=1`、`actual_execution_seconds=30.4047491`、吞吐 `3946874`、`frame_bytes=64000`、`evictions=0`。
- `frontend/src/features/simulation/map/frameCache.ts:47-53,187-208,224-260` 显示 production cache 还包含 featureIndex、values、flags 等多个 typed arrays；脚本的单一 value 计数明显低估。
- 小缓存探针输出 `size=3,sizeBytes=54`，证明 `performance-scale.test.ts:72-86` 的 3 个单 feature frame 不会触发 eviction。

**影响**：缓存超预算、typed-array 膨胀、LRU/geometry eviction、真实视口裁剪和浏览器渲染回归都可能在 PASS 中漏过；吞吐数字本身不可解释。

**修复建议**：使用真实 fixture/生产 `ViewportIndex`、`selectVisibleFeatures`、`FrameCache` 和渲染层；按实际测量区间计算速率，强制 60 秒和目标硬件；统计所有 typed arrays/geometry，并加入 289 帧、LRU eviction、pin、clear/dispose、256 MiB 上限断言。

### F-04 — High：新增 forecast stream 测试绕过 `/simulation` 实际共享 provider，未证明真实重连链路

**问题**：`stream-cleanup.test.ts` 直接渲染 `useHydraulicForecastStream`，但 `/simulation` 实际由 `ForecastStreamBoundary` 创建 `HydraulicRealtimeProvider`；当 shared provider 存在时，`ForecastStreamProvider` 将 standalone hook 设置为 `enabled: false`。因此测试中的 `Last-Event-ID`、gap 和 AbortSignal 断言不是页面实际使用的连接实现。现有 boundary 测试只覆盖 owner/heartbeat/401/403，没有覆盖 transport loss 重连、cursor replay、sequence gap、timer cleanup 和 provider unmount。

**证据**：

- `frontend/src/test/acceptance/stream-cleanup.test.ts:229-276` 直接调用 `useHydraulicForecastStream`。
- `frontend/src/features/simulation/integration/ForecastStreamBoundary.tsx:15-22` 明确嵌套 `HydraulicRealtimeProvider` 与 `ForecastStreamProvider`。
- `frontend/src/features/simulation/forecast/ForecastStreamProvider.tsx:31-38` 在 shared provider 存在时传入 `enabled: !shared`。
- `frontend/src/features/simulation/realtime/HydraulicRealtimeProvider.tsx:81-221` 有另一套 fetch、retry、gap、auth、cursor 和 cleanup 逻辑。
- 现有 `frontend/src/features/simulation/integration/ForecastStreamBoundary.test.tsx:62-105,108-147` 只断言连接 owner、heartbeat 和 401/403 terminality，没有 transport reconnect/gap/cleanup 场景。

**影响**：standalone hook 通过不能证明 `/simulation` 页面在断线、游标过期、401/403、组件卸载时正确重连或释放资源。

**修复建议**：以 `ForecastStreamBoundary`/`HydraulicRealtimeProvider` 为被测边界，使用真实 provider 的可控 fetch reader，覆盖正常 EOF、transport loss、gap、resync、409 cursor reset、401/403、token/network 变化和卸载后的 timer/abort/连接数。

### F-05 — High：相同版本 forecast 事件可覆盖终态，现有“版本回退保护”测试没有覆盖该反例

**问题**：`applyForecastEvent` 只拒绝 `event.alert.version < current.version`，相同版本仍会覆盖；因此一个同版本的 `alert.updated` 可把 `resolved`/`expired` 改回 `open`，违反冻结的 expected-version CAS 与终态不可变语义。新增测试只发送 version 1 回放到 version 2，未测试 equal version，也未测试终态后的同版本/更高版本事件。

**证据**：

- `frontend/src/features/simulation/forecast/ranking.ts:62-82` 的版本判断是 `if (event.alert.version < alerts[index].version) return`，之后无条件替换；终态没有单独保护。
- `frontend/src/test/acceptance/stream-cleanup.test.ts:285-328` 只断言 version 1 不能覆盖 version 2，以及一次 resolved。
- 冻结状态机要求见 `smart-city-drainage/docs/hydraulic-twin/PRD_SUPPLEMENT.md:191-209`：每次变化使用 expected_version CAS，`resolved/expired` 是终态且不得再变。
- 本轮直接调用生产函数的探针输出：`{"id":"a",...,"status":"open","phase":"predicted",...,"version":2}`，输入是相同 version 2 的 `resolved` 告警。

**影响**：告警中心可能在重复/乱序事件中重新显示已解决告警，预测/告警验收会产生错误状态；现有测试会对此回归保持绿色。

**修复建议**：实现 equal-version 拒绝和终态不可变保护（并校验合法 action/phase 转移）；加入 create/acknowledge/observe/refresh/escalate/resolve/expire 的完整状态矩阵及同版本、更高版本、乱序、重复事件反例。

### F-06 — Medium：显式 disconnect 的 late-error 清理竞态没有被覆盖

**问题**：`useSSE.disconnect()` 清除当前 timer 和 source，但没有“显式禁用/连接代次”标记；`onerror` 只检查 mounted，随后仍可能安排重连。现有用例只覆盖“先 error、再 disconnect、timer 被清除”，没有覆盖 close 后迟到的 error 回调或重入行为。

**证据**：

- `frontend/src/hooks/useSSE.ts:96-107`：`onerror` 在 mounted 时安排 reconnect。
- `frontend/src/hooks/useSSE.ts:111-122`：`disconnect` 没有设置 disabled/generation 标记。
- `frontend/src/test/acceptance/stream-cleanup.test.ts:218-225` 只断言显式 disconnect 后 60 秒没有新增实例，没有在 close 后再触发 error。

**影响**：资源清理的结论只覆盖一种顺序，不能证明显式断开在迟到事件/重入下不会重新建立 SSE。

**修复建议**：引入连接 generation 或 `manualDisconnectRef`，让失效连接的 error 回调成为 no-op；增加 close 后 error、重复 error、disconnect/reconnect 交错和 timer 清理断言。

### F-07 — Medium：e2e/performance 产物 JSON 缺少冻结证据格式要求的 `artifact_sha256`

**问题**：脚本在写 JSON 文件并计算 hash 后，才把 `report.artifact_sha256` 加到内存对象；stdout 有 hash、旁边有 sidecar，但落盘 JSON 本身没有该字段。按契约，机器证据 JSON 应包含 `artifact_sha256`，消费者若只读取 artifact 文件会得到不完整 schema。

**证据**：

- 冻结证据字段见 `smart-city-drainage/docs/hydraulic-twin/PRD_SUPPLEMENT.md:568-580`。
- `frontend/e2e/run-ac-smoke.mjs:760-770` 先写文件，`main` 的 `:879-885` 才给内存 report 增加 `artifact_sha256`。
- `frontend/performance/run-twin-map.mjs:461-470,548-554` 有同样的写入顺序。
- 落盘报告 `artifacts/frontend-acceptance/e2e-browser.json:283-284` 和 `artifacts/review-frontend-perf-smoke.json:75-76` 可见 `evidence_sha256`，但没有 `artifact_sha256`。
- 本轮 Node 字段探针对两份 JSON 均输出 `artifact_sha256 in j false`。

**影响**：artifact 与 stdout/sidecar 的 hash 语义不一致，G0/G5/acceptance 的严格 schema 校验可能失败或被迫绕过。

**修复建议**：在序列化前固定最终 report/hash 字段，或定义并实现可验证的 sidecar schema；让 runner 自检落盘 JSON 的必填字段和 hash，再输出 PASS。

### F-08 — Medium：Vitest acceptance 层仍有“断言属性而非行为”的盲区，路由/i18n 也未接入真实 App

**问题**：部分新增用例验证的是 mocked child props 或 DOM attribute，而不是用户行为：disabled editor 没有再次 click；timeline 的 fake runtime 不推进 RAF；A/B chart 被完全 mock，未验证 API/metric/map；路由只直接测 `ProtectedRoute`，没有测 `App` 的实际 route wiring、全部角色矩阵或 locale/i18n。

**证据**：

- `frontend/src/test/acceptance/frontend-acceptance.test.tsx:183-213` 对 disabled editor 只检查 `toBeDisabled()`，没有点击并断言 callback 未执行。
- `frontend/src/test/acceptance/frontend-acceptance.test.tsx:216-254` 注入 `requestFrame: () => 1`、`now: () => 0`，没有验证实际帧推进、4x 速率或 prefetch abort。
- `frontend/src/test/acceptance/frontend-acceptance.test.tsx:33-45,256-290` mock 掉 charts，只验证 mode prop/按钮和 incompatible 文案；没有验证 A/B 请求、指标 delta/unit、map difference 或真实图表。
- `frontend/src/test/acceptance/frontend-acceptance.test.tsx:166-180` 只渲染独立 `ProtectedRoute`；真实 `frontend/src/App.tsx:75-105` 的 `/simulation` wiring、operator/admin route 和 locale 切换未在该 acceptance 文件中验证。
- 源需求包含 `/simulation` 权限与 i18n（`smart-city-drainage/docs/hydraulic-twin/SOURCE_PRD.md:27`）。

**影响**：局部组件/属性仍正确但集成行为已坏时，Vitest 可能保持绿色；尤其无法支撑“用户行为、状态和清理”验收提示。

**修复建议**：保留纯函数测试，同时增加真实 App/route/i18n 和带真实子组件的交互测试；用可控时钟/RAF、mock API 响应和可观察 cleanup signal 验证播放、提交、A/B、forecast 状态与资源释放。

## 未在本轮执行的事项

以下事项没有执行，不能计为通过：全量 `npm test`、`npx tsc --noEmit`、全量 lint/format/build、G0～G6/AC 汇总、后端 pytest/启动、Docker/PostGIS/Redis/worker/live SSE、真实 60 秒 G5 浏览器性能 harness。作者实施摘要中关于全量 Vitest/lint/format 的声明仅作为材料阅读，未作为本轮独立证据。

## 最终决定

**不批准（approved=false）**。在 F-01～F-05 的高风险问题解决并重新执行冻结浏览器 e2e、真实 SSE provider 集成测试和 60 秒/目标环境性能门禁前，不应把本模块标记为完成或把当前 PASS 汇总为 AC-7/G5 通过。
