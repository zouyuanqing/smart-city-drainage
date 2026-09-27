# 前端验收测试实施摘要

日期：2026-09-25  
模块：前端验收测试补齐  
实现范围：仅新增/修改本任务获准的 `frontend/src/test/**`、`frontend/e2e/**`；未修改生产前端代码，未提交或推送。

## 1. 需求与代码依据

- 源 PRD 要求 `/simulation` 权限/i18n、地图时间轴与 1/2/4 倍速、降雨编辑器、A/B、预测实测、forecast 告警和万级数据性能：`docs/hydraulic-twin/SOURCE_PRD.md:26-32`。
- 冻结补充契约规定 viewer/operator/admin 路由边界、FrameCache 256 MiB、离散时间轴、A/B 三模式、fetch-SSE 重连/游标和图表清理：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:1901-1964`。
- 冻结实施契约把前端 e2e/performance 路径分别交给 `frontend-acceptance`，并规定 60 s/4x/万级 fixture、presented FPS/RAF/long-task/cache 等正式门槛：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2320-2371`、`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2501-2525`。
- 真实实现的路由、编辑器、时间轴、A/B、SSE、缓存清理入口分别在 `frontend/src/App.tsx:75-105`、`frontend/src/auth/RouteGuard.tsx:10-19`、`frontend/src/features/simulation/rainfall/RainfallScenarioEditor.tsx:259-289`、`frontend/src/features/simulation/hooks/useTimelinePlayer.ts:535-597`、`frontend/src/features/simulation/integration/ComparisonWorkbench.tsx:34-167`、`frontend/src/features/simulation/forecast/stream.ts:153-284`、`frontend/src/features/simulation/map/frameCache.ts:622-698`。

## 2. 实际改动

### Vitest 行为与资源清理

新增：

- `frontend/src/test/acceptance/frontend-acceptance.test.tsx`
  - 未登录跳转 `/login`、viewer 越权跳转 `/dashboard`、operator 访问 simulation（`:165-180`）。
  - 降雨编辑器缺 typed context 时不调用 create、显示错误、disabled 状态保持不可操作（`:182-213`）。
  - 时间轴 End/Shift+Arrow/Space/4x 的离散帧、播放状态和回调（`:215-254`）。
  - A/B overlay→difference、刷新回调，以及不兼容 frame grid 时禁用 difference（`:256-290`）。
  - observed/predicted 空值不连线、uncertainty band、issue-time markLine（`:292-329`）。
- `frontend/src/test/acceptance/stream-cleanup.test.ts`
  - legacy `useSSE` 的连接、传感器/告警分发、1s/2s 指数退避、open 后重置、显式 disconnect 和卸载取消重连（`:166-225`）。
  - forecast fetch-SSE 的 sequence gap、resync、Last-Event-ID/Bearer cursor 重连和 AbortSignal 清理（`:228-276`）。
  - 非法事件、跨网络事件拒绝，以及 predicted→observed→resolved 和版本回退保护（`:278-328`）。
  - FrameCacheClient 重复请求去重、clear/dispose、in-flight abort、FrameRequestScheduler queued cancel/dispose（`:330-381`）。
- `frontend/src/test/acceptance/performance-scale.test.ts`
  - 10,000 feature viewport index、稳定 feature index、LOD 预算和有界 typed frame cache（`:53-86`）。

这些用例断言 DOM 状态、回调、事件顺序、错误、AbortSignal、缓存字节和资源生命周期，不使用快照替代行为断言。

### 浏览器 e2e

新增 `frontend/e2e/fixtures/frontend-acceptance-v1.json`：

- viewer `/simulation` 页面、时间轴、编辑器、submit、observed 状态和 A/B workbench 可见，并验证对应 API 请求（`:12-82`）。
- operator `/simulation` 的 A/B map mode、4x 时间轴和编辑器可见，并验证核心请求（`:84-129`）。
- 未登录访问 `/simulation` 必须落在 `/login`（`:131-143`）。

复用已存在的 `frontend/e2e/run-ac-smoke.mjs` 和 `frontend/e2e/fixtures/api-v1/manifest.json`，没有改写 sibling-owned runner 或生产代码。

## 3. 实际执行的命令与结果

### Vitest

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend"
npx vitest run src/test/acceptance/frontend-acceptance.test.tsx src/test/acceptance/stream-cleanup.test.ts src/test/acceptance/performance-scale.test.ts --reporter=dot
```

结果：exit 0；3 个文件、14 个测试通过。

```text
npm test -- --reporter=dot
```

结果：exit 0；32 个测试文件、142 个测试通过。输出中只有既有 React `act` deprecation、React Router future flag 和 Node `--localstorage-file` 警告。

### 类型、lint、格式

```text
npx tsc --noEmit
npm run lint
npm run format:check
```

结果：均 exit 0；TypeScript 无输出，ESLint 无 warning/error，Prettier 报告 `All matched files use Prettier code style!`。

### 浏览器 e2e

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend"
node e2e/run-ac-smoke.mjs --fixture frontend/e2e/fixtures/frontend-acceptance-v1.json --output ../artifacts/frontend-acceptance/e2e-browser.json --timeout-seconds 120 --scenario-timeout-seconds 20
```

结果：exit 0，`AC_E2E_STATUS=PASS`，Chrome Headless 154.0.8037.57，3/3 scenarios PASS，browser errors 为 0；stdout artifact SHA-256：

```text
e9d8e09f35e8527722287ada0527c8f9ad20010a9308cd3ed553264c210ee315
```

临时 evidence 文件位于 ownedPaths 之外，读取 stdout/hash 后已清理，未留下越界文件。

### 性能 smoke

```text
node performance/run-twin-map.mjs --fixture frontend/performance/fixtures/twin-map-5k-10k-v1.json --duration-seconds 1 --output ../artifacts/frontend-acceptance/perf-smoke.json
```

结果：exit 0，`FRONTEND_PERFORMANCE_SMOKE_STATUS=PASS`；输入 5,000 nodes / 10,000 pipes / 1,000 catchments、289 frames，2,880 samples；p95 update `29.6705 ms`、max `43.1282 ms`、long tasks `0`、cache peak `18,496,000 bytes`、visible fraction `0.08565`。stdout artifact SHA-256：

```text
fdc0a0a242570dd1bbf24333616cb6547ae8b71aa11dc7ddaec8c6308cf7ed26
```

该脚本自身明确标记 `NON_ACCEPTANCE_SMOKE`，没有把它宣称为浏览器 presented FPS/RAF/真实 API latency 证据；临时 evidence 同样已清理。

## 4. 未覆盖与风险

1. e2e 使用已有 fixture API manifest，任务/帧数据为空，故浏览器层主要验证路由、页面状态、SSE 请求和无 console error；A/B、预测实测、告警转换的真实数据路径由 Vitest 固定 fixture 覆盖，尚未在浏览器中点击并断言完整后端链路。
2. 性能 smoke 是算法/缓存 Node workload，不是正式 G5 浏览器性能门禁；`presented_fps`、`request_animation_frame_p95_ms`、browser long-task、Canvas renderer、map/API latency 仍需按冻结契约的真实 G5 harness 执行。
3. 本任务没有运行 `npm run build`，避免生成/修改 ownedPaths 之外的 `frontend/dist`；e2e runner 已验证现有 production `frontend/dist/index.html` 存在并记录 index SHA-256。
4. 工作树在本任务开始时已有 sibling-owned 的 `frontend/src/test/setup.ts`、既有 e2e/performance 文件和其他未提交改动；本任务未覆盖或提交这些文件，完整 Vitest 结果是在该共享工作树上取得的。
5. 退出时未提交、未推送；未运行后端、Docker、真实传感器/PostGIS/Redis 或 G0-G6，不能据此宣称 AC-1～AC-8 全部通过。
