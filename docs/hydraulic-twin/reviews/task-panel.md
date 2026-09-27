# 仿真任务面板独立审查

- 模块：`task-panel`
- 审查日期：2026-09-25
- 审查方式：只读复核冻结契约、实现、测试；运行题目指定的 4 个聚焦测试文件；未重复最终全量门禁
- 结论：**NOT APPROVED / BLOCKED**

## 1. 审查依据

- 本次规格要求覆盖提交、进度、历史、状态筛选、取消、失败重试、幂等状态展示，并要求“SSE 断线时 REST 恢复并轮询兜底”。
- 冻结状态/事实源约束：进度单调、终态不可回退，`GET status` 是事实源、SSE 不是进度事实源，见 `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1403-1421`。
- 冻结 SSE 恢复约束：queue 满或 sequence gap 不得静默丢失，必须 REST snapshot + Last-Event-ID；cursor 过期也必须 REST resync，见 `docs/hydraulic-twin/PRD_SUPPLEMENT.md:384-395`。
- 冻结前端连接约束：必须使用单一 provider/connection，Dashboard、Simulation、AlertCenter 不得各自建流，见 `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1959-1964`。
- R0 artifact 仅批准设计冻结，明确不代表 G0-G6 或 AC-1-AC-8 通过，见 `artifacts/contracts/v3-independent-review.json:44-71`。

## 2. 聚焦测试

执行命令：

```text
npm --prefix "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend" test -- src/features/simulation/test/taskState.test.ts src/features/simulation/test/taskStream.test.tsx src/features/simulation/test/taskController.test.tsx src/features/simulation/test/taskPanel.test.tsx --reporter=dot
```

结果：exit 0；`4 passed` test files，`14 passed` tests，Vitest 1.2.0，duration 4.48s。输出包含 3 条 `ReactDOMTestUtils.act is deprecated` 警告。

该结果只证明现有 14 个断言通过；下列高/中风险路径没有对应测试，不能据此宣称任务面板验收通过。

## 3. 阻断发现

### H-01 — 轮询兜底既不能发现新任务，也会对全部活动任务发起 N+1 detail 请求

**证据**

- 轮询 effect 在没有活动任务时直接退出：`frontend/src/features/simulation/hooks/useSimulationTask.ts:367-368`。
- 有活动任务时，`pollActive` 只对当前 `activeTasks` 逐个调用 `client.get`，没有周期性 `client.list`：`frontend/src/features/simulation/hooks/useSimulationTask.ts:311-327`。
- `reconcileActive` 只在 SSE open、transport lost 或 resync 时触发：`frontend/src/features/simulation/hooks/useSimulationTask.ts:298-308`。若 SSE 从未连上且 REST 随后恢复，初始 list 之后不会再自动 list。
- `queued` 也属于 active 状态：`frontend/src/features/simulation/model/types.ts:30-36`；因此一个大队列会每轮并行 GET 每个 queued task，而不是用一次 list 获取队列状态。
- 多任务部分失败时，只要一个成功就把全局 `isStale` 设为 false：`frontend/src/features/simulation/hooks/useSimulationTask.ts:323-335`，其余失败 task 的陈旧状态被隐藏。

**影响**

SSE 不可用时，其他用户/worker 新建的任务、当前页不再包含的新历史、REST 恢复后产生的新状态都不会自动出现；当所有任务已终态时轮询完全停止。这直接不满足本次规格的“断线后 REST 恢复并轮询兜底”。同时，50 个 queued task 可在 2 秒轮询周期内形成 50 个 detail 请求，放大 API 负载。

**建议**

分离“列表校准”和“执行中 detail 刷新”：断线时至少周期性执行一次 network-scoped list；SSE 连接时保留低频安全 list。detail 只轮询 leased/running/cancelling 等真正需要的高频状态，并限制并发。部分 detail 失败必须保留 stale/错误状态。增加“零活动任务后服务端新增任务”“多活动任务部分失败”“大队列请求数”测试。

### H-02 — 任务 hook 自建第二条 SSE 连接，违反冻结的单一连接契约

**证据**

- 冻结契约要求单一 provider/connection：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1961-1964`。
- 任务控制器默认直接调用 `useSimulationStream`：`frontend/src/features/simulation/hooks/useSimulationTask.ts:298-308`；该 hook 对每个挂载实例建立自己的 fetch reader 生命周期：`frontend/src/features/simulation/hooks/useSimulationStream.ts:134-278`。
- 现有 ForecastDashboard 已独立调用 `useHydraulicForecastStream`：`frontend/src/features/simulation/forecast/ForecastDashboard.tsx:27-39`。
- `SimulationTaskPanel` 没有消费共享 provider，默认 `enableStream !== false` 时会建立任务流：`frontend/src/features/simulation/hooks/useSimulationTask.ts:298-304`。

**影响**

当 Simulation 页面同时挂载 ForecastDashboard 与 TaskPanel 时，同一 network 会建立两条独立连接、两套 cursor/seen/backoff；多面板会继续倍增。任务流与 forecast 流的 gap/replay 状态可能不一致，并额外消耗 SSE 连接限流。关闭任务流规避重复连接时，又失去任务事件通知。

**建议**

由 foundation/integration 提供单一 SSE provider，Dashboard、TaskPanel、AlertCenter 只订阅筛选后的事件；任务 controller 接收共享连接状态/事件，不自行 fetch。测试应同时挂载 forecast 与 task consumer，并断言底层 fetch 只有一次。

### H-03 — network 切换没有请求代次，旧网络异步结果可写回新网络面板

**证据**

- 所有 REST 写入只检查 `mountedRef`，它只表示组件尚未卸载，不表示结果仍属于当前 network：`frontend/src/features/simulation/hooks/useSimulationTask.ts:123`、`frontend/src/features/simulation/hooks/useSimulationTask.ts:187-224`、`frontend/src/features/simulation/hooks/useSimulationTask.ts:248-277`。
- network 变化时仅清空部分 ref 并启动新 refresh，没有 AbortController 或 generation guard：`frontend/src/features/simulation/hooks/useSimulationTask.ts:347-365`。
- `commitTasks` 不校验入站 task 的 `network_id` 是否等于当前 `options.networkId`：`frontend/src/features/simulation/hooks/useSimulationTask.ts:157-170`。
- submit/cancel/retry 的 in-flight maps 也未在 network 切换时取消或失效，pending UI state 未重置：`frontend/src/features/simulation/hooks/useSimulationTask.ts:132-155`、`frontend/src/features/simulation/hooks/useSimulationTask.ts:347-365`。

**影响**

典型失败序列：network A 的 list/detail 尚未返回，界面切到 network B；B 清空状态并开始请求，随后 A 的旧 Promise 因组件仍 mounted 而提交 A 的任务到 B 面板。旧 submit/cancel/retry 也可能回链到 B，或让 pending/loading 状态卡住。该问题会破坏 network 隔离，属于高风险数据边界缺陷。

**建议**

为每个 network 建立递增 generation 或独立 AbortController；每个异步提交前同时验证 generation、networkId 和 AbortSignal。切换时取消/失效所有 in-flight action，完整重置 pending/submitting 状态；`commitTasks` 再做一次 network identity 校验。增加 deferred Promise 的 `rerender({ networkId: B })` 测试。

### H-04 — SSE 终态先写入后会永久压过 REST，违反“REST 是事实源”

**证据**

- 收到带 task snapshot 的 SSE 事件时，代码先直接 `commitTasks`，只有 snapshot 缺失或已终态才随后 GET：`frontend/src/features/simulation/hooks/useSimulationTask.ts:287-295`。
- REST 与 SSE 共用同一个无来源标记的 merge；当前值一旦 terminal，`chooseStatus` 永远返回当前终态，并锁住 progress/error/published attempt：`frontend/src/features/simulation/model/taskState.ts:148-193`。
- 冻结契约明确 GET status 是事实源、SSE 不是：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1418-1421`。
- 当前测试还把“先观察到的 terminal 获胜”固化为期望，而没有覆盖 REST 覆盖错误 SSE terminal：`frontend/src/features/simulation/test/taskState.test.ts:77-99`。

**影响**

一旦支持的 SSE snapshot 先送来错误/过期的 `cancelled`，随后 REST 返回权威 `completed`，merge 仍永久保留 `cancelled`；反向竞态同理。调用方无法再从真实状态恢复，只能手工刷新到新的空列表。取消/完成竞态虽然由服务端决定，客户端却错误地让“先到的数据源”决定。

**建议**

不要把 terminal SSE snapshot 当权威终态。SSE snapshot 只作进度提示/失效通知，terminal 必须等待 REST；或者为 merge 增加 `source/authoritative` 语义，使 REST terminal 可以纠正 SSE terminal，同时仍阻止旧 attempt/旧时间回退。增加“SSE cancelled → REST completed”和相反顺序测试。

## 4. 中风险发现

### M-01 — `token` prop 没有传给 simulation list 请求

**证据**

- controller 的 token 用于 detail、submit、cancel、SSE，例如 `frontend/src/features/simulation/hooks/useSimulationTask.ts:187-200`。
- 但 `refresh`、`loadMore`、`reconcileActive` 只调用 `client.list({ networkId, cursor, limit })`，没有 command context：`frontend/src/features/simulation/hooks/useSimulationTask.ts:203-277`。
- foundation client 的 `list` 只接收 query，request context 也只传 signal，不传 token：`frontend/src/features/simulation/api/client.ts:188-200`；接口定义同样没有 list context/token：`frontend/src/features/simulation/model/types.ts:174-195`。

**影响**

调用方按 `SimulationTaskPanel token={...}` 传入 bearer token、但 localStorage 没有同一 token 时，首次列表、分页和 REST resync 会 401；与此同时 detail/SSE 使用显式 token，造成同一面板认证行为分裂。

**建议**

让 list 接受 `SimulationCommandContext`（或注入已绑定 token 的 client），并让三条 list 路径都传 `options.token`。增加断言 list Authorization header 与显式 token 一致的 client/controller 测试。

### M-02 — SSE 只验证 `network_id` 非空，没有验证它属于当前订阅

**证据**

- parser 只检查 `network_id` 是非空字符串：`frontend/src/features/simulation/hooks/useSimulationStream.ts:61-74`。
- `processBlock` 在接受事件前没有把 event network、stream id/sequence 与当前订阅比较：`frontend/src/features/simulation/hooks/useSimulationStream.ts:158-188`。
- task hook 信任 candidate 自带的 network，缺失时才采用 event network：`frontend/src/features/simulation/hooks/useSimulationTask.ts:41-53`；candidate 与 event/subscription 之间没有一致性检查。

**影响**

配置错误、代理串流或恶意事件可以把 network B 的 snapshot 合并进 network A 面板，并触发对错误 simulation id 的 REST detail。实现摘要声称“严格校验 network_id”，真实代码未达到该声明。

**建议**

将 expected network 传入 parser/stream hook；event.network_id、snapshot.network_id、event.simulation_id、snapshot.id 任一不一致即拒绝并触发 resync。增加跨 network event 测试。

### M-03 — retry 按钮只检查 resolver 函数存在，不检查该任务是否真的可解析

**证据**

- UI 使用 `Boolean(task.request || resolveRetryRequest)`：`frontend/src/features/simulation/tasks/TaskPanel.tsx:282-284`，并据此启用按钮：`frontend/src/features/simulation/tasks/TaskPanel.tsx:362-373`。
- controller 实际调用 resolver；返回 undefined 时才 fail closed 并抛错：`frontend/src/features/simulation/hooks/useSimulationTask.ts:548-554`。

**影响**

只要父组件传了 resolver 函数，即使该函数对某个刷新后的 task 无法重建请求，按钮仍显示可用；用户点击后必然失败。这与“不会伪造可重试状态”的实现摘要不符。

**建议**

由 controller 暴露逐 task 的 `canRetry/retryRequest`，或以安全、缓存的 resolver 结果计算按钮状态；resolver 抛错也应显示 unavailable，而不是启用后失败。增加 `resolveRetryRequest={() => undefined}` 测试。

### M-04 — 手动 reconnect 存在旧连接 catch 读取新 controller 的竞态

**证据**

- 每次 connect 先 abort 共享 `controller`，再立即把它替换为新 controller：`frontend/src/features/simulation/hooks/useSimulationStream.ts:191-198`。
- 旧 connect 的 catch 检查的是共享变量 `controller.signal.aborted`，不是“这次 connect 自己的 signal”：`frontend/src/features/simulation/hooks/useSimulationStream.ts:253-261`。
- `reconnect()` 可在旧 fetch/reader 尚未 settle 时启动新 connect：`frontend/src/features/simulation/hooks/useSimulationStream.ts:265-268`。

**影响**

旧连接因 abort 抛错时，可能看到新 controller 尚未 abort，从而把本次人为重连误判为 transport loss，额外安排一次 reconnect、覆盖 status，并可能中断刚建立的新连接。这也是重复连接/连接抖动的放大器。

**建议**

每次 connect 捕获局部 `const connection = new AbortController()`；catch/finally 仅在 `controller === connection` 时更新共享状态。增加 pending fetch + 手动 reconnect 的竞态测试。

### M-05 — 现有测试没有覆盖规格点名的恢复/竞态边界

**证据**

- controller 的 5 个用例仅覆盖重复 submit、cancel 后 REST terminal、单次失败重试、单 active detail 轮询、idempotency conflict：`frontend/src/features/simulation/test/taskController.test.tsx:65-291`。
- stream 的 2 个用例仅覆盖基础 parse 与 gap/自动重连：`frontend/src/features/simulation/test/taskStream.test.tsx:66-129`。
- panel 的 2 个用例仅覆盖基本渲染/筛选/提交重试和初次 list error：`frontend/src/features/simulation/test/taskPanel.test.tsx:60-151`。
- 没有用例覆盖：SSE 不可用后零/多活动任务的 list 恢复、network rerender、显式 token 的 list、terminal SSE 被 REST 纠正、跨 network event、resolver 返回 undefined、部分 detail 失败、手动 reconnect 竞态、StrictMode effect 重放。

**影响**

当前 `14 passed` 对本规格关键失败模式没有证明力；H-01～H-04 与 M-01～M-04 均可在现有测试全绿时发生。

**建议**

按上述边界补回归测试，并至少包含一个 controller+stream 集成测试，验证 gap/断线→REST list/detail→状态收敛，而不是只分别测试 callback 被调用。

## 5. 通过项与未执行项

### 已确认通过

- 题目指定的 4 个聚焦测试文件在本轮实际执行中全部通过（见第 2 节）。
- 状态筛选、基础进度渲染、分页按钮、提交/失败重试 UI、幂等回执展示的基本路径已有测试。
- SSE Bearer header、Last-Event-ID、sequence gap 回调和指数重连的基础路径已有测试。

### 本轮未运行

- 未运行最终全量 Vitest、全树 TypeScript、全量 lint/format、build、浏览器 E2E 或真实后端联调；这是按本次审查要求避免重复最终全量门禁。
- 因此不能宣称 AC-7、G0-G6 或整体前端门禁通过；R0 artifact 本身也明确未声明这些门禁，见 `artifacts/contracts/v3-independent-review.json:44-71`。

## 6. 决策

**approved = false**。存在 4 个高风险发现（H-01～H-04），分别破坏轮询恢复、冻结 SSE 架构、network 数据隔离和 REST 事实源语义。应先修复并补充对应竞态/恢复测试，再进行独立复审。
