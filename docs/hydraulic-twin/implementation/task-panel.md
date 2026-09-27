# 仿真任务面板实施摘要

## 1. 需求与契约依据

- 源 PRD 要求仿真任务支持提交、0–100% 进度、取消、最多两次重试、幂等与历史：`docs/hydraulic-twin/SOURCE_PRD.md:20-26`。
- 冻结补充契约规定进度单调、REST 状态为事实源、取消/完成竞态由服务端终态决定，以及 SSE gap/断线后 REST resync：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:164-178`、`docs/hydraulic-twin/PRD_SUPPLEMENT.md:390-394`。
- 实施契约固定 simulation submit/list/get/cancel、`Idempotency-Key` 和 canonical 状态：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1376-1420`、`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1466-1489`、`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1572-1635`；前端任务面板 owner 与禁止 SSE-only 约束见 `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2320-2335`。
- 当前 R0 独立复审 artifact 为 `R0_DESIGN_PASS`：`artifacts/contracts/v3-independent-review.json:1-71`。

## 2. 真实代码改动

### 任务面板

- 新增 `SimulationTaskPanel`，支持提交、刷新、分页历史、精确状态筛选、进度/attempt/phase、错误详情、取消、失败/超时重新提交和幂等回执展示：`frontend/src/features/simulation/tasks/TaskPanel.tsx:19-50`、`frontend/src/features/simulation/tasks/TaskPanel.tsx:84-240`、`frontend/src/features/simulation/tasks/TaskPanel.tsx:265-392`。
- 写操作默认关闭，由 `canWrite` 控制；`submitRequest` 或 `resolveRetryRequest` 缺失时不会伪造可重试状态：`frontend/src/features/simulation/tasks/TaskPanel.tsx:84-116`、`frontend/src/features/simulation/tasks/TaskPanel.tsx:179-191`、`frontend/src/features/simulation/tasks/TaskPanel.tsx:273-283`。
- 视觉层沿用现有暗色工业控制台令牌，并使用 `content-visibility` 降低长历史列表渲染成本：`frontend/src/features/simulation/tasks/TaskPanel.css:1-35`、`frontend/src/features/simulation/tasks/TaskPanel.css:112-129`。

### 状态、REST 与幂等

- 任务类型不再复制 foundation 契约，而是复用 `frontend/src/features/simulation/model/types.ts` 的 canonical DTO/client：`frontend/src/features/simulation/tasks/types.ts:1-37`。
- 默认 REST client 复用 foundation-owned Axios client，保留结构化错误与 `Idempotency-Replayed`：`frontend/src/features/simulation/tasks/api.ts:1-11`。
- 状态合并、排序、筛选和请求 fingerprint 复用 foundation model；本模块仅补充“能否解析原请求”的手动重试判断：`frontend/src/features/simulation/tasks/taskState.ts:1-13`。
- `useSimulationTask` 实现：首屏/手动 REST 刷新、分页历史、SSE 事件合并、活动任务轮询、页面隐藏降频、失败退避、REST 重同步：`frontend/src/features/simulation/hooks/useSimulationTask.ts:186-336`、`frontend/src/features/simulation/hooks/useSimulationTask.ts:346-410`。
- 提交按 canonical request fingerprint 复用同一幂等键；相同请求安全重放显示 `replayed`，409 `IDEMPOTENCY_KEY_REUSED` 显示 `conflict` 并在下一次提交轮换 key：`frontend/src/features/simulation/hooks/useSimulationTask.ts:412-496`。
- 取消不乐观宣告 `cancelled`；cancel 失败后读取 REST detail，若任务已进入终态则采用服务端终态，否则显示动作错误：`frontend/src/features/simulation/hooks/useSimulationTask.ts:498-538`。
- 失败/超时重试使用新幂等 key 创建新 simulation，并将新 task ID 回链到源任务；刷新后若无原请求则 fail closed：`frontend/src/features/simulation/hooks/useSimulationTask.ts:540-570`。

### SSE 与轮询兜底

- `useSimulationStream` 使用 Bearer-authenticated fetch streaming，不使用原生 EventSource；默认 endpoint 为 `/api/forecast/events?network_id=...`：`frontend/src/features/simulation/hooks/useSimulationStream.ts:21-40`、`frontend/src/features/simulation/hooks/useSimulationStream.ts:98-121`。
- 严格校验 schema/event_id/sequence/network_id，拒绝未知 schema；重复 event ID 丢弃，sequence gap/regression 和 invalid event 触发 REST resync：`frontend/src/features/simulation/hooks/useSimulationStream.ts:42-96`、`frontend/src/features/simulation/hooks/useSimulationStream.ts:157-188`。
- 断线后 1/2/4…最长 30 秒指数重连并携带 `Last-Event-ID`；409 cursor expired 会清空 cursor 后重同步：`frontend/src/features/simulation/hooks/useSimulationStream.ts:190-260`。
- SSE 只作为加速/失效通知；活动任务始终保留 REST 轮询。断线时可见页默认 2 秒、隐藏页 10 秒，SSE 已连接时 30 秒安全校准，失败最多退避 30 秒：`frontend/src/features/simulation/hooks/useSimulationTask.ts:25-29`、`frontend/src/features/simulation/hooks/useSimulationTask.ts:297-336`、`frontend/src/features/simulation/hooks/useSimulationTask.ts:366-410`。

## 3. 新增测试

- 状态单调、retry_wait 防回退、终态竞态锁定、筛选与稳定 fingerprint：`frontend/src/features/simulation/test/taskState.test.ts:36-129`。
- 幂等重放、取消竞态 REST 恢复、失败重新提交、轮询进度恢复、幂等冲突：`frontend/src/features/simulation/test/taskController.test.tsx:63-291`。
- SSE envelope、sequence gap、断线重连、Authorization 和 Last-Event-ID：`frontend/src/features/simulation/test/taskStream.test.tsx:64-129`。
- 面板进度/历史/筛选/错误/提交/重试/幂等回执：`frontend/src/features/simulation/test/taskPanel.test.tsx:58-151`。

## 4. 本轮实际执行命令与结果

### 通过

1. 聚焦任务测试：

```bash
npm --prefix "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend" test -- src/features/simulation/test/taskState.test.ts src/features/simulation/test/taskStream.test.tsx src/features/simulation/test/taskController.test.tsx src/features/simulation/test/taskPanel.test.tsx --reporter=dot
```

结果：exit 0；`4 passed` test files，`14 passed` tests。输出仍有 React Testing Library/React 18.3 的 `ReactDOMTestUtils.act is deprecated` 警告，不影响退出码。

2. owned-path ESLint（所有 task/hook/test TS/TSX，`--max-warnings 0`）：

```bash
npm --prefix "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend" exec -- eslint --config "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/eslint.config.js" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/tasks/TaskPanel.tsx" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/tasks/api.ts" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/tasks/index.ts" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/tasks/taskState.ts" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/tasks/types.ts" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/hooks/useSimulationTask.ts" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/hooks/useSimulationStream.ts" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/test/taskState.test.ts" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/test/taskStream.test.tsx" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/test/taskController.test.tsx" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/test/taskPanel.test.tsx" --report-unused-disable-directives --max-warnings 0
```

结果：exit 0，无输出。

3. owned-path Prettier check：

```bash
npm --prefix "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend" exec -- prettier --check "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/tasks/TaskPanel.css" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/tasks/TaskPanel.tsx" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/tasks/api.ts" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/tasks/index.ts" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/tasks/taskState.ts" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/tasks/types.ts" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/hooks/useSimulationTask.ts" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/hooks/useSimulationStream.ts" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/test/taskState.test.ts" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/test/taskStream.test.tsx" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/test/taskController.test.tsx" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/test/taskPanel.test.tsx"
```

结果：exit 0；`All matched files use Prettier code style!`。

### 未通过（全仓/并行模块问题，非本任务 owned paths）

4. 全量 Vitest：

```bash
npm --prefix "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend" test -- --reporter=dot
```

结果：exit 1；`18 passed / 2 failed` test files，`93 passed / 3 failed` tests。失败为：

- `src/test/__tests__/LoginPage.test.tsx:24`：2 项均因 `localStorage.clear is not a function`。
- `src/features/simulation/api/client.test.ts:41`：1 项期望 `nextCursor='next-1'`，实际 `null`。

本任务 4 个 test files 在该全量运行中均通过。

5. TypeScript 全树检查：

```bash
npm --prefix "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend" exec -- tsc --noEmit --pretty false -p "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/tsconfig.json"
```

结果：exit 2；错误均在本任务 owned paths 之外的并行模块：`src/features/simulation/api/client.test.ts:98`、`api/client.ts:108`、`model/errors.ts:180`、`model/normalizers.ts:109`、`model/taskState.ts:84`、`store/simulationStore.ts:133`、`test/fixtures/index.ts:2-12`。输出未包含 task-panel owned files 的错误；由于全树命令非零，不能宣称全仓 typecheck PASS。

6. 全量 lint：

```bash
npm --prefix "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend" run lint
```

结果：exit 1；42 个错误位于非 owned 的 `frontend/e2e/run-ac-smoke.mjs` 与 `frontend/performance/run-twin-map.mjs`。owned-path lint 已单独 exit 0。

7. 全量 format check：

```bash
npm --prefix "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend" run format:check
```

结果：exit 1；Prettier 报 38 个既有/并行文件格式问题。owned-path format check 已单独 exit 0。

8. 运行时版本：

```bash
node --version && npm --version
```

结果：本机为 Node `v25.9.0`、npm `11.12.1`；不是 `frontend/package.json:5-11` 固定的 Node 18.20.8/npm 10.8.2。

## 5. 风险与集成事项

1. **i18n 待 integration 落地**：组件使用 `simulation.tasks.*` key，并提供英文 default value；当前 owned paths 不含 `frontend/src/i18n/**`，中文资源需由 frontend-integration 补齐。
2. **刷新后重试依赖原请求**：当前会话提交会保留 request；若刷新后服务端列表不返回原 request 且页面未提供 `resolveRetryRequest`，重试按钮会 fail closed，而不是猜测输入。
3. **SSE 为可选加速层**：canonical `/api/forecast/events` 仍可能只提供 forecast 事件；任务面板在只有 `simulation_id` 时触发 REST detail刷新，并且无论 SSE 是否连接都保留轮询。
4. **未做浏览器/真实后端联调**：本轮未启动 FastAPI、PostgreSQL、Redis 或浏览器 E2E；单元测试使用注入 client/fetch stream。
5. **未运行 build**：`npm run build` 会写入 owned paths 外的 `frontend/dist/**`，本任务为严格路径隔离未执行。
6. **全仓门禁未绿**：全量 Vitest、tsc、lint、format check 的失败均已如实记录；integration 需在并行 owner 修复后重跑 G0/AC-7。
