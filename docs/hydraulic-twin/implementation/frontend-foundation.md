# Frontend foundation implementation

日期：2026-09-24  
模块：`frontend-foundation`（仿真类型、API 与状态）

## 实施范围

本次只实现任务明确授权的 feature-local 目录：`frontend/src/features/simulation/api/**`、`model/**`、`store/**`、`test/fixtures/**`；另按任务要求写入本摘要文件。未修改 legacy `frontend/src/services/api.ts`、`frontend/src/store/useAppStore.ts` 或其他并行 owner 的文件。

实现依据：

- 源 PRD 明确 React 18、TypeScript、Leaflet、Zustand、Axios 技术栈，以及仿真进度/取消/最多两次重试和前端时间轴需求：`docs/hydraulic-twin/SOURCE_PRD.md:14-30`。
- 冻结补充契约要求结构化错误 envelope、REST 状态事实源、任务重试元数据和错误码矩阵：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:164-178`、`docs/hydraulic-twin/PRD_SUPPLEMENT.md:489-560`。
- 实施契约的 durable-job 状态转换、进度单调性、租约/重试和 `GET status` 事实源：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1376-1446`；simulation submit/list/cancel/frame API：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1466-1635`；feature-local 状态和大几何不进 Zustand：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1899-1964`。
- 现状代码把 Axios 错误压成 `Error(message)`：`frontend/src/services/api.ts:26-39`；legacy Zustand 读数更新会替换 Map：`frontend/src/store/useAppStore.ts:78-89`；本实现没有复用这些 legacy 响应式路径。

## 实际改动

### 1. API 契约与类型转换

- `model/types.ts:9-55` 定义 simulation 状态、终态/活动态、phase、solver selection/ID 和兼容别名；`model/types.ts:63-122` 定义稳定错误码、字段错误、submit request；`model/types.ts:131-221` 定义 task/page/client DTO；`model/types.ts:223-402` 定义 result、frame index、frame、target manifest、comparison、stream 和幂等回执。
- `model/normalizers.ts:116-164` 将 UI/submit draft 转为 snake_case wire payload，并拒绝缺 ID、无效时间、非正整数 duration/frame interval；`model/normalizers.ts:166-321` 将 REST/SSE task、list envelope、`progress_pct` 别名和 retry metadata 规范化，错误状态 fail closed；`model/normalizers.ts:333-404` 规范化 frame index/单帧并检查 frame count；`model/normalizers.ts:406-423` 提供稳定的 DTO 转换别名和 query 参数转换。
- `model/types.ts:297-330` 保留 GeoJSON/map/frame 大几何类型，但只作为 transport/result 类型；注释明确其不进入响应式 store。

### 2. Axios API client、错误与重试

- `api/client.ts:35-81` 提供可注入 Axios instance、token、Bearer 注入、401 回调和 retry 配置；`api/client.ts:136-161` 创建 `/api` Axios instance 并设置 JSON headers；`api/client.ts:164-198` 统一请求、取消、网络错误和 401 处理。
- `api/client.ts:200-279` 实现 simulation list/get/submit/cancel，提交与取消强制 `Idempotency-Key`，读取 `Idempotency-Replayed`；`api/client.ts:281-395` 实现 result、frame index、single frame、target manifest、map-data 和 A/B compare。
- `model/errors.ts:45-91` 兼容 hydraulic 与 FastAPI `detail/errors/loc/msg` envelope；`model/errors.ts:93-140` 的 `ApiClientError` 保留 `status/code/detail/errors/requestId/retryAfterSeconds`；`model/errors.ts:170-205` 将 Axios rejection 转为结构化错误；`model/errors.ts:225-315` 提供 transport retry 判定、固定 `[10s, 60s]` backoff、Retry-After、Abort 和 store error 转换。
- 导出了 `createSimulationApiClient`、`createHydraulicApiClient`、`simulationApi`、`createSimulationAxiosInstance` 及兼容别名，未修改 legacy 全局 API。

### 3. Zustand simulation store

- `store/simulationStore.ts:49-94` 的响应式状态只包含 network/revision/simulation 选择、任务元数据、timeline index/playing/speed、图层开关、请求状态和结构化错误；没有 geometry、frame arrays、map data 或 frame cache 字段。
- `store/simulationStore.ts:124-163` 提供可测试的初始状态和稳定 task snapshot；`store/simulationStore.ts:177-239` 实现 merge/upsert/remove、选择、请求状态、pending action 和 timeline/layer 命令。
- `store/simulationStore.ts:297-360` 导出 `useSimulationStore`、细粒度 selectors（selected/active/filtered task、progress、cancel/retry、timeline、network）和 `selectHasReactiveLargeData` 防护。任务数组 selector 在非任务状态更新时保持引用稳定，避免把大对象更新扩散到整个页面。
- `model/taskState.ts:23-75` 实现冻结状态转换边、终态/活动态、取消/手动重试判定；`model/taskState.ts:77-142` 实现 scheduled/manual/exhausted retry model；`model/taskState.ts:181-217` 合并 REST/SSE 时保持 progress 单调、旧 attempt 不回退、首个 terminal winner 不被迟到包覆盖；`model/taskState.ts:219-267` 提供排序、过滤和稳定请求 fingerprint。

### 4. Fixtures 与测试

- `test/fixtures/index.ts:6-44` 统一导出 task/list/frame/map/error/submit fixtures；静态 JSON 位于 `test/fixtures/simulation-task-v1.json`、`simulation-list-v1.json`、`simulation-frame-v1.json`、`network-map-v1.json`、`api-error-v1.json`。
- `model/normalizers.test.ts:15-69` 覆盖类型转换、list cursor、frame、fail-closed 输入和结构化 error fixture。
- `model/taskState.test.ts:22-100` 覆盖终态锁定、retry_wait、状态转换边、过滤、retry model 和 fingerprint。
- `model/errors.test.ts:3-49` 覆盖字段错误/request id/Retry-After、network-only retry 和非重试错误。
- `api/client.test.ts:29-124` 覆盖 Axios Bearer、list、Idempotency-Key/replay header、422 envelope 和 429/503 retry backoff。
- `store/simulationStore.test.ts:16-104` 覆盖 merge、细粒度 selector 引用稳定、timeline/layer 控制、无大几何响应式字段和结构化错误/重试可用性。

## 检查结果

以下命令均在 `smart-city-drainage/frontend`（或明确路径）执行。

| 命令 | 实际结果 |
|---|---|
| `npx vitest run src/features/simulation/model/errors.test.ts src/features/simulation/model/normalizers.test.ts src/features/simulation/model/taskState.test.ts src/features/simulation/api/client.test.ts src/features/simulation/store/simulationStore.test.ts --reporter=dot` | PASS：5 files、18 tests passed。输出末尾为 `Test Files 5 passed (5)`、`Tests 18 passed (18)`。 |
| `npx tsc --noEmit --pretty false --target ES2020 --module ESNext --moduleResolution bundler --lib ES2020,DOM --strict --skipLibCheck ...foundation files...` | PASS：exit 0，无输出。完整 production `tsc -b` 也在 `npm run build` 中通过。 |
| `npx eslint src/features/simulation/api src/features/simulation/model src/features/simulation/store --report-unused-disable-directives --max-warnings 0` | PASS：exit 0，无输出。 |
| `npx prettier --check "src/features/simulation/api/**/*.{ts,tsx,json}" "src/features/simulation/model/**/*.{ts,tsx,json}" "src/features/simulation/store/**/*.{ts,tsx,json}" "src/features/simulation/test/fixtures/**/*.{ts,tsx,json}"` | PASS：`All matched files use Prettier code style!`。 |
| `npm run build` | PASS：exit 0；`4042 modules transformed`，`✓ built in 11.25s`。仍有既有 `echarts` chunk 超过 1000 kB 警告，未由本模块处理。 |
| `npm run lint` | PASS：exit 0，无输出。 |
| `npm test -- --reporter=dot` | FAIL（当前并行工作树基线问题）：exit 1；21 files 中 19 passed、1 failed；98 tests 中 95 passed、2 failed。失败均为既有 `src/test/__tests__/LoginPage.test.tsx:24` 的 `localStorage.clear is not a function`；同次运行还报告 `ERR_WORKER_OUT_OF_MEM` 未处理错误。该路径不在本任务 ownedPaths，未修改。 |
| `npm run format:check` | FAIL（仓库全量基线）：exit 1，Prettier 报 37 个非 owned path 文件；owned foundation 路径的独立 check 已 PASS。未格式化或覆盖这些并行/legacy 文件。 |

## 风险与未完成项

1. 当前前端全量测试仍被 legacy LoginPage localStorage fixture 和 Vitest worker OOM 阻断；本模块 focused 18/18、build、lint 均通过，但不能把全量套件标为通过。
2. 并行 `features/simulation/tasks/**` 目前仍有自己的 fetch-based API/error/type 实现（其文件不在本任务 ownedPaths）；集成阶段应将 task panel/hook 切换到本目录的 Axios client、`ApiClientError`、normalizer 和 task state，避免两套契约继续漂移。
3. map/frame DTO 已类型化并通过转换测试，但本模块没有实现外部 byte-bounded frame cache/Canvas renderer；几何仍必须留在 `simulation-map` owner 的外部缓存，不得复制到 Zustand。
4. 未运行真实后端 API、数据库、SSE、worker、性能/浏览器基准或 Docker 验证；本摘要只报告本 ask 内实际执行的前端检查，不宣称 PRD AC 或 G0-G6 通过。
5. 未提交、未推送任何 commit；本模块只留下工作树改动和本摘要。
