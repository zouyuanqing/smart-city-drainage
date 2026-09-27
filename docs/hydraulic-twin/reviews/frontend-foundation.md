# 前端基础模块审查（frontend-foundation）

## 结论

**NOT_APPROVED / `approved=false`**

本次只读复核了 `frontend-foundation` 的类型、API client、normalizer、任务状态、Zustand store 和 fixtures，并与当前冻结的 `IMPLEMENTATION_CONTRACT.md` / `PRD_SUPPLEMENT.md` 对照。发现 7 个高严重度问题：target-manifest 路径与冻结 API 不一致；真实 map/frame DTO 与 renderer/cache 不兼容；多个 API 返回值绕过类型转换；normalizer 对坏任务/帧 fail-open；任务合并接受非法状态转换和跨 revision 数据；提交边界没有落实幂等/强类型约束；切换 network revision 会保留旧 timeline/比较选择。高风险问题未解决前不能批准。

没有修改实现代码；本文件是本次唯一新增的审查产物。

## 契约基线

- 用户规格要求：feature-local 仿真类型、Axios client、Zustand 仿真 store、任务状态/错误/重试模型，且大几何/帧数组不进入响应式 store。
- 规范实现契约：`smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1470-1494`（强类型 REST、幂等和错误 envelope）、`:1572-1675`（simulation submit/status/frame）、`:1899-1964`（feature-local 状态、时间轴、FrameCache 和 SSE 边界）。
- v3 补充基线：`smart-city-drainage/docs/hydraulic-twin/PRD_SUPPLEMENT.md:249-269`（幂等和 immutable target manifest）、`:484-487`（A/B topology/time-grid compatibility）、`:503-560`（HTTP/async error matrix）。
- `smart-city-drainage/docs/hydraulic-twin/contracts/frontend.md` 明确标为 superseded/non-normative，未将其旧路径或旧状态名作为规范依据。

## 本次实际检查

### 定向测试（通过）

命令：

```text
cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend && npx vitest run src/features/simulation/model/errors.test.ts src/features/simulation/model/normalizers.test.ts src/features/simulation/model/taskState.test.ts src/features/simulation/api/client.test.ts src/features/simulation/store/simulationStore.test.ts --reporter=dot
```

实际结果：`Test Files 5 passed (5)`、`Tests 18 passed (18)`，耗时 `1.57s`。这只证明列出的 18 个断言通过，不证明真实 API→renderer 集成、失败边界或全量门禁通过。

### 类型、lint、格式（通过）

```text
cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend && npx tsc --noEmit --pretty false
```

exit 0，无输出。

```text
cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend && npx eslint src/features/simulation/api src/features/simulation/model src/features/simulation/store --report-unused-disable-directives --max-warnings 0
```

exit 0，无输出。

```text
cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend && npx prettier --check "src/features/simulation/api/**/*.{ts,tsx,json}" "src/features/simulation/model/**/*.{ts,tsx,json}" "src/features/simulation/store/**/*.{ts,tsx,json}" "src/features/simulation/test/fixtures/**/*.{ts,tsx,json}"
```

实际结果：`All matched files use Prettier code style!`。

### 端点/字段定向诊断

```text
cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage && node - <<'EOF'
const fs=require('fs');
const c=fs.readFileSync('docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md','utf8');
const s=fs.readFileSync('frontend/src/features/simulation/api/client.ts','utf8');
const contract=[...c.matchAll(/GET `([^`]*target-manifest[^`]*)`/g)].map(m=>m[1]);
const impl=[...s.matchAll(/url: `([^`]*target-manifest[^`]*)`/g)].map(m=>m[1]);
console.log({contractPaths:contract, implementationPath:impl});
EOF
```

实际输出：

```text
{ contractPaths: [ '/api/networks/{id}/target-manifest' ],
  implementationPath: [ '/simulations/${pathId(simulationId)}/target-manifest' ] }
```

```text
cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage && node - <<'EOF'
const fs=require('fs');
const f=JSON.parse(fs.readFileSync('frontend/src/features/simulation/test/fixtures/simulation-frame-v1.json','utf8'));
const map=fs.readFileSync('frontend/src/features/simulation/map/types.ts','utf8');
const cache=fs.readFileSync('frontend/src/features/simulation/map/frameCache.ts','utf8');
console.log({wireFrameTime:f.frame_time, wireNodeSurcharge:f.nodes.surcharged, wirePipeFlow:f.pipes.flow_m3s, mapHasTime:/time: string/.test(map), mapHasSurcharge:/surcharge\?:/.test(map), mapHasVelocity:/velocity_mps\?:/.test(map), cacheRequiresTime:/frame\.time/.test(cache), cacheValidatesFlowM3s:/flow_m3s/.test(cache)});
EOF
```

实际输出：

```text
{ wireFrameTime: '2026-09-24T01:00:00Z',
  wireNodeSurcharge: [ false, false ],
  wirePipeFlow: [ 0.4, 1.7 ],
  mapHasTime: true,
  mapHasSurcharge: true,
  mapHasVelocity: true,
  cacheRequiresTime: true,
  cacheValidatesFlowM3s: false }
```

### Fixtures 格式诊断

```text
cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage && node - <<'EOF'
const fs=require('fs');
const path=require('path');
const dir='frontend/src/features/simulation/test/fixtures';
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const sha=/^[0-9a-f]{64}$/;
const files=['simulation-task-v1.json','simulation-list-v1.json','simulation-frame-v1.json','network-map-v1.json','api-error-v1.json'];
for(const file of files){
 const x=JSON.parse(fs.readFileSync(path.join(dir,file),'utf8'));
 const ids=[]; const hashes=[];
 (function walk(v){ if(v&&typeof v==='object'){ for(const [key,val] of Object.entries(v)){ if(key==='id'||key.endsWith('_id')||key==='request_id') ids.push([key,val]); if(key.includes('sha256')||key==='manifest-sha256-fixture-1') hashes.push([key,val]); walk(val); } } })(x);
 const badIds=ids.filter(([,v])=>typeof v==='string' && !uuid.test(v));
 const badHashes=hashes.filter(([,v])=>typeof v==='string' && !sha.test(v));
 console.log(file, 'badIds=',badIds.length,'badHashes=',badHashes.length, badIds.slice(0,2), badHashes.slice(0,2));
}
EOF
```

实际输出显示：`simulation-task-v1.json` 有 6 个非法 UUID、1 个非法 hash；`simulation-list-v1.json` 有 6 个非法 UUID；`simulation-frame-v1.json` 有 5 个非法 UUID、1 个非法 hash；`network-map-v1.json` 有 5 个非法 UUID、1 个非法 hash；`api-error-v1.json` 有 1 个非法 `request_id`。

### 覆盖搜索

```text
cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend && rg -n "getMapData|getTargetManifest|compare|getFrame|getFrames|getResult|setNetworkSelection|buffering|activeSimulation|Filtered|status: 403|status: 409|status: 410|status: 422|Idempotency-Key|parameter_set_id|requested_solver_id" src/features/simulation/{api,model,store}/*.test.ts || true
```

实际只命中了 `simulationStore.test.ts:7,32` 的 filtered selector、`errors.test.ts:47` 的 409 code、`client.test.ts:70,86` 的幂等 header/422；没有命中 map/target/compare/frame/result API、401/403/410/429/503 矩阵、非法 merge 或 revision 切换测试。

## Findings

### H-01（高）— `getTargetManifest` 使用了错误的资源路径和资源身份

- **文件**：`smart-city-drainage/frontend/src/features/simulation/api/client.ts:331-352`；规范端点见 `smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1511-1513`、manifest 的 revision scope 见 `:442-463`。
- **证据**：实现的 URL 是 `/simulations/${simulationId}/target-manifest`，而冻结 REST 表只列出 `/api/networks/{id}/target-manifest`。本次 Node 诊断实际输出 contract path 为 `/api/networks/{id}/target-manifest`、implementation path 为 `/simulations/${pathId(simulationId)}/target-manifest`。该方法还只检查对象存在后直接 `as SimulationTargetManifest`，没有检查 network/revision/manifest/hash。
- **影响**：真实调用会对不存在的路径发起请求，或者把 simulation 资源误当作 network manifest；A/B、frame 和 map 无法证明使用同一 immutable target manifest。
- **建议修复**：改为 network/revision-scoped API（必要时显式传 revision），在边界校验 manifest schema、`network_revision_id`、target types、counts、dense feature index 和 hash；不要用类型断言代替转换。

### H-02（高）— 共享 API DTO 与现有 map/frame cache 的运行时契约不兼容

- **文件**：`smart-city-drainage/frontend/src/features/simulation/model/types.ts:254-275,315-330`；`smart-city-drainage/frontend/src/features/simulation/map/types.ts:40-48,81-97,127-158`；`smart-city-drainage/frontend/src/features/simulation/map/frameCache.ts:92-116,156-184`；canonical fixture `smart-city-drainage/frontend/src/features/simulation/test/fixtures/simulation-frame-v1.json:9-23`。
- **证据**：model 使用 `frame_time`、节点 `surcharged`、管段 `flow_m3s`，map 类型/cache 使用 `time`、`surcharge`、`velocity_mps`；map bbox 是 `{west,south,east,north}`，model `NetworkMapData.bbox` 是四元组。cache 的 `validateFrame` 明确要求 `frame.time`，其字段白名单也没有 `flow_m3s`。本次字段诊断确认 fixture 是 `frame_time/surcharged/flow_m3s`，而 cache 要求 `time` 且不识别 `flow_m3s`。
- **影响**：真实 `getFrame` 返回值不能直接进入 `FrameCache`/lookup；即使调用方用断言绕过，`time` 缺失会使帧被拒绝，字段名不一致还会静默丢失流速/超载语义。真实 map tuple bbox 也会在 `normalizeMapData` 的对象 bbox 校验处失败。
- **建议修复**：选定一个 canonical wire/renderer 契约，并提供显式 adapter（tuple→BBox、`frame_time`→`time`、`surcharged`→`surcharge`、`flow_m3s`→`velocity_mps`），同时校验 manifest/revision；或者统一所有层字段名。必须把真实 API fixture 直接送入 client→cache→lookup 的集成测试。

### H-03（高）— target/map/comparison API 绕过 normalizer，错误或部分响应会被当作强类型对象

- **文件**：`smart-city-drainage/frontend/src/features/simulation/api/client.ts:343-352,373-377,399-403`；宽类型见 `smart-city-drainage/frontend/src/features/simulation/model/types.ts:224-233,259-275,315-343,356-381`。
- **证据**：三个方法只做 `typeof body === 'object'`，随后直接 `as` 返回；没有 `normalizeTargetManifest`、`normalizeNetworkMapData` 或 comparison validator。`SimulationResult`/`SimulationFrame` 还带 `[key: string]: unknown`，`NetworkMapData` 的 `geometry` 是 `unknown`，所以缺字段、错误 schema、错误 revision/hash、错误 counts 都能通过 TypeScript。
- **影响**：服务端返回 malformed/partial payload 时，调用方仍会得到“已类型化”的对象；尤其 map 的 `complete=false`/`truncated_layers` 和 comparison 的 topology compatibility 不能被可靠解释，违反强类型 response 和 fail-closed 边界。
- **建议修复**：为每个 endpoint 增加严格 normalizer，校验必填字段、精确 schema/version、UUID/hash、finite 数值、frame count/index、map complete/truncated/counts 和 comparison compatibility；校验失败抛出结构化错误，不返回断言后的对象。

### H-04（高）— normalizer 对缺失/非法任务、列表和帧元数据 fail-open

- **文件**：`smart-city-drainage/frontend/src/features/simulation/model/normalizers.ts:171-190,307-314,334-367,371-404`。
- **证据**：
  - 缺少 `created_at` 时用 `updated_at` 代替；`progress`、`attempt`、`max_attempts` 缺失或非法时分别默认为 0/0/3；超范围值被 clamp/round 而不是拒绝（`:177-190`）。
  - 列表对象没有数组字段时直接返回 `{items: [], nextCursor: null}`（`:307-314`），把 schema drift 显示成“没有任务”。
  - 帧索引缺少 `frame_count` 时用 `frames.length` 补齐（`:355-367`），不检查重复/缺失/越界 index、严格时间网格或 checksum；帧 `published_attempt_no` 非法时默认为 1，schema 只要求非空字符串（`:371-404`）。
- **影响**：坏响应可以进入 store：例如 progress=150 会变成 100，缺失 `created_at` 会被当成合法任务，缺列表会被当成成功空页，残缺帧索引会被当作完整索引。UI 可能显示错误的 100%/成功状态或用不完整时间轴，违反 `GET status`/FrameCache 的事实源和 fail-closed 要求。
- **建议修复**：对 required 字段、0..100/0..3、UTC RFC3339、exact schema、frame count/index 连续性、checksum/bytes 和 retry metadata 做拒绝式校验；禁止用默认值掩盖 malformed response；增加每个负例的单测。

### H-05（高）— 任务 merge 没有执行冻结状态转换和 immutable identity 校验

- **文件**：`smart-city-drainage/frontend/src/features/simulation/model/taskState.ts:167-179,187-217`；状态表见 `smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1382-1405`。
- **证据**：`canTransitionSimulationStatus` 定义了合法边，但 `mergeSimulationTask`/`chooseStatus` 不调用它；`chooseStatus` 在同一 attempt 看到 incoming terminal 就直接接受 incoming（`:170-171`），所以 `queued→completed`、`retry_wait→completed` 等非法边也会被接受。merge 只比较 `id`（`:191-192`），随后把 incoming 的 network/revision/manifest/错误/retry 字段覆盖到 current；过期包还可能清空 `next_attempt_at` 或原始 submit request。
- **影响**：迟到/错误 SSE 或 REST 快照可以把不可能状态锁成 completed，或者把同 ID 的另一 revision/manifest 数据混入当前任务；这会污染取消/重试、A/B 兼容性和结果选择。
- **建议修复**：merge 前验证 `canTransitionSimulationStatus`，并拒绝/隔离同 ID 但 network/revision/target-manifest/hash 不一致的包；按 attempt/version/time 选择完整快照，不能只 spread 部分字段；增加所有非法转换和跨 revision 包测试。

### H-06（高）— simulation submit/cancel 边界没有落实强类型请求和幂等约束

- **文件**：`smart-city-drainage/frontend/src/features/simulation/model/types.ts:110-123`；`smart-city-drainage/frontend/src/features/simulation/model/normalizers.ts:117-154`；`smart-city-drainage/frontend/src/features/simulation/api/client.ts:228-241,262-274`；规范见 `smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1473-1483,1576-1596`。
- **证据**：`parameter_set_id` 在 client 类型中可省略/为 null，但冻结 simulation 表和 submit 示例要求非空 parameter set；`requested_solver_id` 类型被扩成任意 `string`，normalizer 只检查非空字符串，不检查 explicit 必须提供 solver；`ponding_enabled`/`strict_stability` 缺失时 normalizer 静默补默认值。client 直接发送调用者对象，`idempotencyKey` 只写入 header，没有检查冻结的 8..128 字符，也没有校验 UUID/UTC/finite/cross-field 约束。
- **影响**：类型系统允许构造服务端必拒绝的请求，或把同一 fingerprint 交给错误 idempotency scope；错误会在网络层才暴露，且可能造成重复/冲突语义。Auto/explicit solver 约束也不能在提交前给出可定位错误。
- **建议修复**：使用 discriminated request union（auto/explicit），把 `parameter_set_id`、时间、solver、duration/frame interval 和 booleans 设为严格必填/运行时校验；在发送前验证 UUID、UTC RFC3339、finite、显式 solver 和 8..128 字符 key，submit/cancel 都走同一 guard。

### H-07（高）— 切换同一 network 的 revision 不会清空旧 timeline/比较选择

- **文件**：`smart-city-drainage/frontend/src/features/simulation/store/simulationStore.ts:183-200,204-218`；immutable revision/A-B 规则见 `smart-city-drainage/docs/hydraulic-twin/PRD_SUPPLEMENT.md:484-487`、`smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1953-1954`。
- **证据**：`setNetworkSelection` 的 `changed` 只比较 `selectedNetworkId`，没有比较 `selectedNetworkRevisionId`；同一 network 换 revision 时只更新 revision，保留 `selectedSimulationId`、`selectedTaskId`、`selectedComparison`、`frameIndex`、`playing` 和 `lastFrameTime`。代码也没有在 revision 变化时清理比较选择。
- **影响**：旧 revision 的 frame index/播放状态和旧 A/B pair 会继续被 UI 使用，可能把旧拓扑/旧 manifest 的状态显示到新 revision；这违反 immutable revision 和 A/B compatibility 边界。
- **建议修复**：把 network+revision 作为原子 selection identity；任一变化都 pause、清除 accumulator/last frame、归零 frame index，并清理 comparison/过期 pending action；增加同 network 换 revision 的 store 测试。

### M-01（中）— retry policy 对 frame 使用了错误的 backoff，并对所有 5xx 过度重试

- **文件**：`smart-city-drainage/frontend/src/features/simulation/model/errors.ts:212-241,244-292`；`smart-city-drainage/frontend/src/features/simulation/api/client.ts:180-185,314-327`；FrameCache 规则见 `smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1930-1938`。
- **证据**：默认 delay 是 `[10_000, 60_000]`，而 `getFrame` 与其他 GET 共用该策略；冻结 FrameCache 语义要求 503 使用 `0.5/1/2s`、最多两次。`isRetryableApiError` 在 status 500..599 时无条件返回 true，没有先排除 `SPATIAL_BACKEND_UNAVAILABLE`、policy-unavailable 或其他非重试业务 code。
- **影响**：真实 frame 503 会阻塞 10 秒/60 秒，拖慢播放和 buffering；不可重试的部署/策略错误也会产生无意义请求。429 的 Retry-After 行为虽被保留，但不能修复 503 和业务 code 判断。
- **建议修复**：按 endpoint/surface 配置 retry；frame 单独使用 0.5/1/2 秒、最多两次；先按 error code 判断是否可重试，再考虑 HTTP status；对 429 单独遵守 Retry-After。

### M-02（中）— 缺少服务端 code 时，HTTP 状态被转换成非规范的 `HTTP_<status>`

- **文件**：`smart-city-drainage/frontend/src/features/simulation/model/errors.ts:181-195`；错误矩阵见 `smart-city-drainage/docs/hydraulic-twin/PRD_SUPPLEMENT.md:503-538`。
- **证据**：当 body 没有 `code` 时，`toApiClientError` 直接生成 `HTTP_401`、`HTTP_403`、`HTTP_422` 等；当前规范要求 401=`AUTH_REQUIRED/UNAUTHENTICATED`、403=`FORBIDDEN`、422=`VALIDATION_ERROR` 等稳定 code。`client.test.ts:76-99` 只覆盖了 body 已带 code 的 422，没有覆盖无 code/legacy envelope。
- **影响**：权限、限流、认证和表单错误分支若按冻结 code 判断会落入未知分支；`selectCanRetryTask` 等消费者也无法稳定按 code 工作。
- **建议修复**：为 HTTP status 提供 canonical fallback code（服务端 code 优先），并对 401/403/409/410/422/429/503/504 增加结构化负例测试。

### M-03（中）— timeline store 没有 buffering 状态，且 `NaN` frame index 会被写入

- **文件**：`smart-city-drainage/frontend/src/features/simulation/store/simulationStore.ts:22-27,285-305`；失败语义见 `smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1937-1938`。
- **证据**：`SimulationTimelineState` 只有 `frameIndex/playing/speed/lastFrameTime`，没有 frame loading 所需的 `buffering`；`Math.max(0, Math.trunc(timeline.frameIndex))` 对 `NaN` 结果仍为 `NaN`，`setFrameIndex` 没有 finite/integer/range guard。
- **影响**：调用方无法从 store 表达“保留上一有效帧、显示 buffering”的冻结失败状态；NaN index 会传播到 timeline/播放逻辑，造成不可预测的 seek/label。
- **建议修复**：加入受控 buffering transition，校验 finite integer 和 frame count 范围；补充 503、过期、NaN、末帧测试。

### M-04（中）— active/filtered selector 每次返回新数组，破坏细粒度 selector 的引用稳定性

- **文件**：`smart-city-drainage/frontend/src/features/simulation/store/simulationStore.ts:330-334`；性能基线见 `smart-city-drainage/docs/hydraulic-twin/PRD_SUPPLEMENT.md:435-441`。
- **证据**：`selectActiveSimulationTasks` 每次 `.filter`，`selectFilteredSimulationTasks` 每次调用 `filterSimulationTasks`，即使 task 数组未变也返回新引用。现有测试只验证 `selectSimulationTasks` 在 loading 更新时引用不变（`simulationStore.test.ts:35-37`），没有验证这两个 selector。
- **影响**：任意 timeline/loading/error 更新都可能让使用 active/filtered selector 的面板重新渲染，放大 25fps/大列表场景的无效工作。
- **建议修复**：在 store 中缓存派生数组，或使用按 task/status 签名计算的稳定 selector；增加 selector 引用稳定性测试。

### M-05（中）— 默认导出的 client 没有接入现有 401 清理/登录跳转

- **文件**：`smart-city-drainage/frontend/src/features/simulation/api/client.ts:186-197,425-427`；legacy 认证处理 `smart-city-drainage/frontend/src/services/api.ts:17-39`；安全基线 `smart-city-drainage/docs/hydraulic-twin/PRD_SUPPLEMENT.md:285-290`。
- **证据**：factory 只在调用者显式提供 `onUnauthorized` 时回调；默认 `simulationApi` 用独立 `simulationAxios` 创建，没有 callback，也没有复用 legacy Axios response interceptor。现有 legacy interceptor 才会清 token 并跳转 `/login`。
- **影响**：使用默认导出的 simulation API 时，401 只抛出结构化错误，过期 token 仍留在 localStorage，页面不会按既有认证语义清理/回登录。若 host 永远不注入 callback，这是可见的认证回归。
- **建议修复**：在应用组合层给默认 client 绑定统一 auth handler，或让 factory 强制要求 handler；不要假定独立 Axios instance 会继承 legacy interceptor，并补 401 集成测试。

### M-06（中）— retry selector 不检查原始请求，UI 可显示可用但实际必失败

- **文件**：`smart-city-drainage/frontend/src/features/simulation/model/taskState.ts:63-75,117-133`；`smart-city-drainage/frontend/src/features/simulation/store/simulationStore.ts:353-361`；实际调用方 `smart-city-drainage/frontend/src/features/simulation/hooks/useSimulationTask.ts:541-555`。
- **证据**：`isManuallyRetryable` 只看 status 为 failed/timed_out；store 的 `selectCanRetryTask` 不调用 `hasRetryRequest`。hook 真正执行 retry 时却要求 `requestByTaskIdRef` 或 `task.request`，没有请求就抛出“original request unavailable”。
- **影响**：任务面板可能对没有可重放请求的终态任务显示 retry enabled；点击后才失败。对于 `MASS_BALANCE_FAILED`、`SIMULATION_TIMEOUT` 等终态，用户也无法区分“允许新建任务”和“当前 payload 可重放”。
- **建议修复**：selector 同时返回 `canRetry` 与 `requiresRequest/retryUnavailableReason`，或只在存在可解析 request 时启用；为无 request、非重试 code、exhausted attempt 加测试。

### M-07（中）— fixtures 使用非法 UUID/hash，并以 `as unknown` 隐藏契约错误

- **文件**：`smart-city-drainage/frontend/src/features/simulation/test/fixtures/index.ts:12-15`；`simulation-task-v1.json:3-17`、`simulation-list-v1.json:3-28`、`simulation-frame-v1.json:2-10`、`network-map-v1.json:2-5`、`api-error-v1.json:1-4`。
- **证据**：fixture 使用 `simulation-fixture-1`、`network-fixture-1`、`request-fixture-1` 等非 UUID，使用 `manifest-sha256-fixture-1` 而非 64 位小写 SHA-256；`index.ts` 用 `as unknown as SimulationTask/NetworkMapData` 绕过编译检查。本次 Node 诊断实际报告所有五类 fixture 都存在非法 ID/hash。
- **影响**：这些 fixture 不是当前冻结 schema 的 golden input，无法验证 UUID/hash/target-manifest 绑定；测试全绿反而可能掩盖 normalizer 未检查这些字段。
- **建议修复**：换成真实 UUID v4/64 位 hex hash，去掉 `as unknown`，由共享 normalizer/contract fixture validator 校验；同时加入 400/401/403/409/410/422/429/503 负例 fixture。

### M-08（中）— focused 18 tests 没有覆盖本模块最危险的 API 和 store 边界

- **文件**：`smart-city-drainage/frontend/src/features/simulation/api/client.test.ts:29-124`、`model/normalizers.test.ts:15-69`、`model/taskState.test.ts:22-100`、`store/simulationStore.test.ts:16-104`。
- **证据**：本次 coverage `rg` 实际没有命中 `getMapData/getTargetManifest/compare/getFrame/getFrames/getResult`、401/403/410/429/503 矩阵、revision switch、buffering 或非法 merge。当前 taskState 测试只断言四条合法/非法简单边，未断言 `mergeSimulationTask` 是否拒绝 `queued→completed` 或跨 revision 包；store 测试未断言同 network 换 revision。
- **影响**：18/18 通过不能支撑 API 契约、跨模块类型兼容或冻结失败语义；H-01～H-07 恰好都落在未覆盖路径。
- **建议修复**：补充 client endpoint contract tests、canonical frame→cache 集成测试、normalizer negative matrix、非法/跨 revision merge、revision switch/buffering/selector stability 测试；真实后端/SSE/性能仍需单独 gate。

## 决定

高严重度问题未解决，`approved=false`。优先修复顺序：H-01/H-02/H-03（API endpoint、canonical DTO、严格转换）→ H-04/H-05（fail-closed 与状态机）→ H-06/H-07（幂等请求和 revision 切换）→ M-01～M-08（重试、认证接线、派生状态、fixtures 和缺测）。本次没有运行全仓 Vitest、production build、真实后端/SSE、数据库或浏览器性能门禁；这些未执行项不能被本次 18 个 focused tests 替代。
