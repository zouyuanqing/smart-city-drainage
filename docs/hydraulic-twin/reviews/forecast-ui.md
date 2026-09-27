# Forecast 卡片与风险榜：独立代码审查

- **Module:** `forecast-ui`
- **审查结论:** **NOT_APPROVED / `approved=false`**
- **原因:** 发现多个高风险正确性、状态语义和 SSE 恢复问题；不能以现有 4 个通过的 Vitest 用例替代冻结契约验收。
- **审查方式:** 只读检查冻结契约、模块规格、真实源码和现有测试；未修改源代码或测试。

## 规范与范围

本审查使用的规范是：

- `smart-city-drainage/docs/hydraulic-twin/PRD_SUPPLEMENT.md`（Frozen v3）。
- `smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md`（当前实施契约）。
- 模块规格 `C:/Users/zyq/Documents/opencode-pn/goal测试/.zcode/workflow-drafts/排水数字孪生-PRD-全链路交付.dwf.ts:366-371`。
- `docs/hydraulic-twin/contracts/forecast.md` 和 `contracts/frontend.md` 标为 superseded，因此没有用其旧字段或旧路径覆盖 v3 契约。

实际读取的实现文件：

- `frontend/src/features/simulation/forecast/types.ts`
- `frontend/src/features/simulation/forecast/ranking.ts`
- `frontend/src/features/simulation/forecast/stream.ts`
- `frontend/src/features/simulation/forecast/ForecastDashboard.tsx`
- `frontend/src/features/simulation/forecast/index.ts`
- `frontend/src/features/simulation/test/forecastDashboard.test.tsx`
- 相关集成上下文：`frontend/src/pages/Dashboard.tsx`、`frontend/src/services/api.ts`、`frontend/src/features/simulation/hooks/useSimulationStream.ts`、中英文 i18n 资源。

## 已执行检查

| 检查 | 实际结果 |
|---|---|
| `npm --prefix frontend test -- --run src/features/simulation/test/forecastDashboard.test.tsx` | **通过**：`1 passed`，`4 tests passed`；输出有 ReactDOMTestUtils `act` deprecation warning。 |
| `npx eslint src/features/simulation/forecast src/features/simulation/test/forecastDashboard.test.tsx --report-unused-disable-directives --max-warnings 0` | **通过**，exit 0，无输出。 |
| `npx tsc -p tsconfig.json --noEmit --pretty false` | **exit 2**；报错均在其他并行模块（`hooks/useSimulationTask.ts`、`map/*`、`tasks/taskState.ts`、`test/timelinePlayer.test.ts`、`workers/frameDecodeProtocol.ts`），本次未报告 forecast 源文件错误；这不是完整 build 门禁。 |
| `vite build` 的 `write:false` 诊断（入口为 `src/features/simulation/forecast/index.ts`） | 入口输出为 `assets/index-DbQsXMpB.js`，`dynamicImports: []`，且 `hasDashboardCopy: true`；公共 barrel 没有生成独立 forecast 动态 chunk。 |
| Vite SSR 诊断：不完整 SSE alert（只有 `id/version`） | `parseForecastSse` **接受**该帧并返回 `event.alert={id:'a',version:1}`。 |
| Vite SSR 诊断：`forecast.resync_required` 帧 | `parseForecastSse(...)` 返回 **`null`**。 |
| Vite SSR 诊断：客户端时钟与 `server_time` 相差约 5h48m、expected_at=10:30 | `summariseTwoHourForecast` 返回 `active:0`，尽管按服务端 10:00 计算该告警在 2h 窗内。 |
| Vite SSR 诊断：相同 alert version、不同 network 的事件 | reducer 接受并用新 payload 覆盖旧 payload；没有 network 隔离。 |

未运行最终全量 `npm run build`、全量 Vitest、浏览器 E2E、真实 forecast API/SSE 或性能门禁；本审查没有把这些项目标为通过。

## Findings

### H-01 — 公共 lazy boundary 实际没有代码分割

- **File:** `frontend/src/features/simulation/forecast/ForecastDashboard.tsx:111-117`; `frontend/src/features/simulation/forecast/index.ts:1`
- **Problem:** `lazy(() => import('./ForecastDashboard'))` 在实现模块内部自引用，而 barrel 又静态 re-export 同一个模块的 default 和 boundary。按实现文档要求从 `@/features/simulation/forecast` 导入时，ForecastDashboard 会被静态纳入入口，不能实现模块规格所说的“可懒加载 Dashboard 组件”。
- **Evidence:** 模块规格 `...dwf.ts:366-371` 明确要求 lazy Dashboard；源码 `ForecastDashboard.tsx:111`、`index.ts:1` 如上。实际执行 `vite build({ write:false, input:'src/features/simulation/forecast/index.ts' })` 的输出为：`entry=assets/index-DbQsXMpB.js`、`dynamicImports=[]`、`hasDashboardCopy=true`。
- **Impact:** 初始 Dashboard bundle 会包含 Forecast UI/stream 依赖，违反懒加载目标并可能拖慢现有大屏首屏；`Suspense` 只能显示 fallback，不能减少已加载代码。
- **Fix:** 将 lazy wrapper 放到不静态引用实现模块的独立入口文件；公共 barrel 只导出 wrapper（以及确需静态导出的纯 helper），不要从同一实现文件静态 re-export default。增加构建产物断言，要求 forecast 实现位于非入口动态 chunk。

### H-02 — latest REST 请求没有按 `networkId` 取数

- **File:** `frontend/src/features/simulation/forecast/ForecastDashboard.tsx:7-8,17-22,41-57`
- **Problem:** 默认 loader 固定调用 `api.get('/forecast/latest')`，没有必需的 `network_id` query，也没有校验返回的 `snapshot.network_id`。组件的 `networkId` 只用于 SSE URL 和 effect 依赖，未用于 REST snapshot。
- **Evidence:** v3 契约要求 `GET /api/forecast/latest?network_id=&limit=`（`PRD_SUPPLEMENT.md:501-509`；实施契约 `IMPLEMENTATION_CONTRACT.md:1688-1701`）。实际代码只出现 `'/forecast/latest'`，如 `ForecastDashboard.tsx:8`。
- **Impact:** 真实后端会返回 422，或在存在默认行为时显示错误网络的数据；同一 Dashboard 切换 network 时尤其容易产生跨网络误报。
- **Fix:** loader 接收当前 `networkId` 并使用 query params；响应进入状态前校验 `network_id === networkId`，network 切换时取消/丢弃旧请求结果。

### H-03 — `stale/not_ready` 被渲染成“暂无风险预警”

- **File:** `frontend/src/features/simulation/forecast/ForecastDashboard.tsx:23-25,59-87`; `frontend/src/features/simulation/forecast/ranking.ts:22-43`
- **Problem:** `ForecastSnapshot.data_state` 虽在类型中定义，但组件和摘要函数完全不读取它。任何非空 snapshot（包括 `not_ready`、`stale` 且 alerts 为空）都会进入普通摘要并显示“未来 2 小时暂无风险预警/暂无风险预警”。
- **Evidence:** 类型定义 `types.ts:1,19-34`；组件分支 `ForecastDashboard.tsx:59-67,78-87` 没有 `data_state` 判断。契约规定 `ready|stale|not_ready` 的独立 freshness 语义（`IMPLEMENTATION_CONTRACT.md:1722-1726`），且 `not_ready` 不得伪造空风险（`PRD_SUPPLEMENT.md:534-536`）；前端失败语义要求明确显示 stale/gap，而不是把缺失数据当正常（`PRD_SUPPLEMENT.md:741-750`）。
- **Impact:** 数据不可用或已过期时向运营人员提供“没有风险”的错误安全信号，属于高风险告警误导。
- **Fix:** 对 `not_ready` 显示“预测尚未就绪”；对 `stale` 显示最后成功 run/时间和风险榜禁用或明确 stale 标记；只有 `ready` 且有效数据为空时才显示空态。

### H-04 — 风险榜绕过 canonical risk-ranking，并隐藏 stale/run/评分分量

- **File:** `frontend/src/features/simulation/forecast/ForecastDashboard.tsx:61-67`; `frontend/src/features/simulation/forecast/types.ts:36-39`; `frontend/src/features/simulation/forecast/ranking.ts:14-19`
- **Problem:** 组件没有调用 `GET /api/forecast/risk-ranking`，而是从 `snapshot.alerts` 过滤 open/acknowledged 后用 `as ForecastRiskItem[]` 强制转换。该转换绕过了 `score_components`、`policy_version` 等必需字段，也没有排除来源 run stale 的告警。
- **Evidence:** 实施契约规定 risk-ranking 只返回 open/acknowledged、来源 run 未 stale，并返回 score components/policy version，按 `risk_score DESC, expected_at ASC, id ASC` 排序（`IMPLEMENTATION_CONTRACT.md:1726`；`PRD_SUPPLEMENT.md:486-493`）。v3 响应是带 `data_state/risk_policy_version/nowcast_run_id/items[]` 的 envelope，item 内含 `alert` 和 `rank`（`PRD_SUPPLEMENT.md:572-597`），而本地类型是 flat `ForecastAlert` + `policy_version`（`types.ts:36-39`），组件实际未请求该接口（`ForecastDashboard.tsx:61-67`）。
- **Impact:** 风险榜可能显示 stale 风险、缺少可解释分量/政策版本，且类型断言使 DTO 漂移在编译期不可见。
- **Fix:** 增加 typed risk-ranking response/normalizer，按 network、ready 状态和 limit 请求；按返回的 `alert.risk_score/expected_at/id` 做确定性排序，保留 policy version、as-of/run id 和 score components；禁止用类型断言掩盖缺失字段。

### H-05 — SSE 解析会静默丢弃非法帧，并错误丢弃 `forecast.resync_required`

- **File:** `frontend/src/features/simulation/forecast/stream.ts:5-12,19-51,102-110`
- **Problem:** parser 只检查 schema、event_id、sequence、event_type、alert.id/version，不检查完整 envelope/alert 必填字段、network_id、枚举、有限数值和 safe integer；不合法帧在流循环中直接 `continue`，不触发 resync/错误回调。`EVENT_TYPES` 也没有 `forecast.resync_required`，所以服务端要求重同步的控制事件被静默忽略。
- **Evidence:** 契约要求未知 schema/缺字段不得写 store并触发 resync，且 gap/overflow 要发 resync 或断线（`IMPLEMENTATION_CONTRACT.md:1869-1874`；前端契约 `PRD_SUPPLEMENT.md:749-750`）。实际 SSE 诊断：只有 `{id,version}` 的 alert 被 `parseForecastSse` 接受；`event_type:'forecast.resync_required'` 的帧返回 `parsed:null`。流循环 `stream.ts:103-104` 对 null 直接跳过。
- **Impact:** 不完整 payload 可进入 reducer；随后组件访问 `alert.type`、`risk_score.toFixed`、`target.id` 等字段，可能直接崩溃。控制帧被丢弃时，客户端继续使用缺失事件的旧 snapshot，形成静默数据缺口。
- **Fix:** 在边界做完整 runtime schema validation（事件 envelope、alert 必填字段、network、phase/status、finite/range、safe integer）；非法帧调用 protocol-error + REST resync，不写 store；把 `forecast.resync_required` 作为独立控制分支处理，不当作 `ForecastEvent` 业务事件。

### H-06 — SSE cursor、gap、重复和 409 恢复不符合契约

- **File:** `frontend/src/features/simulation/forecast/stream.ts:68-75,78-119`; `frontend/src/features/simulation/forecast/ranking.ts:50-69`
- **Problem:** hook 没有 seen event-id 集合，也不拒绝 sequence regression；只在同一 effect 生命周期内、且 `previousSequence` 已初始化后检查 gap。重连后的第一条事件没有和持久 cursor 对齐校验；任意 409（包括 `SSE_CURSOR_EXPIRED`）都进入通用 catch，继续携带旧 `lastEventId` 重试。catch 只调用 resync，不清空 cursor。`applyForecastEvent` 只拒绝更低 `alert.version`，相同 version 的重复/伪造事件仍会覆盖；它不检查 event network。
- **Evidence:** 契约要求 Last-Event-ID sequence replay、重复 event_id 丢弃、低于当前 alert.version 忽略（`IMPLEMENTATION_CONTRACT.md:1869-1873`）。实现 `stream.ts:105-110` 无 seen-id/sequence regression 分支，`stream.ts:114-119` 无 409 分支；`ranking.ts:58-69` 无 event-id/network 校验。仓库内较完整的同类实现 `frontend/src/features/simulation/hooks/useSimulationStream.ts:166-185,220-228` 已展示 seen-id、sequence regression 和 409 清 cursor 的模式，但 Forecast hook 未复用。
- **Impact:** replay gap 可能被当成连续状态；重复事件可重复写入/覆盖；过期 cursor 会反复请求同一个失败 cursor而无法恢复；跨 network 的同 ID 事件可污染当前 Dashboard。
- **Fix:** 维护并跨重连保存 cursor、last sequence 和有界 seen event-id；首条 replay 事件校验 cursor+1；sequence regression/重复直接丢弃；检测 409 `SSE_CURSOR_EXPIRED` 后清 cursor、REST resync、再重连；按 `event.network_id` 和 aggregate version 做最终隔离。

### M-01 — 2h 摘要使用客户端墙钟，且没有时间推进刷新

- **File:** `frontend/src/features/simulation/forecast/ranking.ts:22-43`; `frontend/src/features/simulation/forecast/ForecastDashboard.tsx:61`
- **Problem:** `summariseTwoHourForecast` 默认使用 `Date.now()`，组件不使用响应中的 `server_time` 作为时间基准，也没有定时刷新；当客户端时钟偏移或告警自然跨过 2h 边界而没有 SSE 事件时，摘要会保留旧计数或错误变成 0。
- **Evidence:** 实际 SSR 诊断使用 `server_time=2026-09-24T10:00:00Z`、`expected_at=10:30Z`，客户端 `Date.now()` 为约 `15:47Z`；函数返回 `active:0`，而按 server time 应在 2h 窗内。`ForecastDashboard.tsx:61` 未传权威时间，函数没有 timer。
- **Impact:** 运营摘要可能漏报临近风险或长期显示过期计数。
- **Fix:** 以服务端时间/统一时钟计算窗口，校验无效时间；用受控 timer 或 snapshot server-time 更新触发重算，并测试边界 `now`、`now+2h`、时钟偏移。

### M-02 — 新 UI 文案全部硬编码中文，未接入冻结的 i18n 契约

- **File:** `frontend/src/features/simulation/forecast/ForecastDashboard.tsx:59-116`; `frontend/src/i18n/zh.json:1-94`; `frontend/src/i18n/en.json:1-94`
- **Problem:** loading、error、空态、标题、phase 标签和风险榜字段均为 JSX 中文字符串；现有中英文资源没有 `simulation.forecast`/`risk` namespace，切换语言不会改变此组件。
- **Evidence:** 实施契约要求所有新 UI 文案走 `simulation/.../forecast/risk/...` namespace并做中英文 key parity（`IMPLEMENTATION_CONTRACT.md:1899-1905`；`PRD_SUPPLEMENT.md:702-708`）。实际字符串见 `ForecastDashboard.tsx:59-60,70-87,92-102,115`；`zh.json`/`en.json` 当前只含 app/nav/login/dashboard/map/video/alerts/settings/common。
- **Impact:** 不满足双语门禁，语言切换时 Dashboard 仍混用中文；`aria-label` 也不能随语言切换。
- **Fix:** 将文案作为翻译 key/labels 注入组件并使用 `useTranslation`；由 `frontend-integration` owner 补齐 zh/en key parity 和 parity 测试。即使 i18n 文件不在本模块 ownedPaths，也不能以静态 handoff 代替最终门禁。

### M-03 — 没有单一 forecast stream provider/connection 边界

- **File:** `frontend/src/features/simulation/forecast/ForecastDashboard.tsx:27-39`; `frontend/src/features/simulation/forecast/stream.ts:135-148`
- **Problem:** Dashboard 每次挂载直接调用 `useHydraulicForecastStream`；导出的 `useForecastAlerts` 又独立创建同一 hook，且其 `onResyncRequired` 是 no-op。feature 没有 provider/context 或共享连接生命周期。
- **Evidence:** 实施契约要求“单一 provider/connection；Dashboard、Simulation、AlertCenter 不各自建流”（`IMPLEMENTATION_CONTRACT.md:1959-1964`）。当前 `ForecastDashboard.tsx:27-39` 和 `stream.ts:135-148` 各自实例化连接。
- **Impact:** 集成到多个页面/卡片后会产生重复 SSE 请求、重复回调和额外连接压力；`useForecastAlerts` 消费者在断线/gap 时不会恢复。
- **Fix:** 在 frontend-integration 的单一 provider 中持有连接和 alert snapshot，Dashboard 只订阅 context；或者让模块导出唯一 client/provider，并移除 no-op resync helper。

### M-04 — observed 行仍显示“分钟后”，把已发生事实误写成未来预测

- **File:** `frontend/src/features/simulation/forecast/ForecastDashboard.tsx:90-103`
- **Problem:** phase 为 `observed` 时仍无条件输出 `{lead_time_minutes} 分钟后`。`lead_time_minutes` 是预测发出时的提前量，不是 observed 发生后的剩余分钟数；对已跨过 `expected_at` 的告警会显示错误时间语义。
- **Evidence:** `ForecastDashboard.tsx:94-102` 只按 phase 改标签，不改时间字段；本地 `ForecastAlert` 也没有 `observed_at`（`types.ts:4-17`）。冻结状态契约区分 predicted 与 observed（`PRD_SUPPLEMENT.md:188-208`）。
- **Impact:** 运营人员可能把已经发生的告警误读为仍在等待的未来事件。
- **Fix:** 补齐 observed 时间字段；observed 显示“已发生于 …”或只显示预测提前量“预测提前量 X 分钟”，只有 predicted 才显示“X 分钟后”。

### M-05 — 定向测试没有覆盖契约要求的失败和恢复路径

- **File:** `frontend/src/features/simulation/test/forecastDashboard.test.tsx:48-117`
- **Problem:** 现有 4 个测试只覆盖 ready+空态、简单 score 降序、mocked observed callback 和一个合法 SSE frame。没有覆盖 `stale/not_ready`、network query、risk-ranking envelope/stale 排除、完整 DTO validation、sequence gap/regression、重复 event_id、409 cursor expiry、resync control frame、断线重连/卸载 abort、network 切换、i18n 或真实 lazy chunk。
- **Evidence:** 测试文件实际只有 `it` 四项（`forecastDashboard.test.tsx:51-61,63-71,73-103,105-117`）；v3 前端验收明确要求 DTO normalization、fetch SSE 重连、event/version 去重、risk 排序和 phase 视觉测试（`PRD_SUPPLEMENT.md:942-947`）。尽管本轮命令实际得到 4/4 PASS，这些未执行路径不能视为通过。
- **Impact:** 当前测试会在上述高风险缺陷存在时仍然全绿，无法保护重连、数据隔离和安全状态语义。
- **Fix:** 为每个 finding 增加负例和集成测试：not_ready/stale UI、API URL/response network、canonical risk envelope、非法 SSE 不写 store、resync/gap/409、重复/倒退/跨 network、unmount abort、lazy chunk/i18n parity。

## 交接观察（不替代上面的模块 finding）

当前 `frontend/src/pages/Dashboard.tsx` 没有导入 `LazyForecastDashboardBoundary` 或 forecast barrel；全仓搜索只命中 forecast 模块自身和测试。由于 workflow 将 Dashboard 路由、i18n 和组合交给 `frontend-integration`（`...dwf.ts:428-434`），这属于集成 owner 的明确 handoff，而不是本模块可以独自修复的范围；但在全链路验收前必须完成，否则 Forecast 卡片对用户不可达。

## 最终判定

**`approved=false`。** 至少 H-01～H-06 中的 REST 网络范围、not_ready/stale 假空态、canonical risk-ranking、SSE 校验/控制帧和 cursor/gap 恢复问题必须在合并/集成前处理。现有 focused Vitest **4/4 PASS** 仅证明四个 happy-path 断言，不改变该判定。
