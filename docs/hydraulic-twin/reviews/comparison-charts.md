# A/B 与预测实测图独立审查

- **模块**：`comparison-charts`
- **审查角色**：独立只读代码审查者
- **审查日期**：2026-09-24
- **结论**：**NOT_APPROVED / `approved=false`**
- **范围**：`frontend/src/features/simulation/charts/**`、`frontend/src/features/simulation/comparison/**`、`frontend/src/features/simulation/test/chart-comparison.test.tsx`，并对照冻结的 `PRD_SUPPLEMENT.md`、`IMPLEMENTATION_CONTRACT.md`、R0 artifact 与实现摘要。

## 结论摘要

定向 Vitest、owned-file TypeScript、ESLint、Prettier 检查均通过，但这只能证明局部 option/helper 与 mock 生命周期测试通过，不能证明 AC-6 的生产链路。发现两个阻断级问题：

1. A/B DTO/图表路径没有 fail-closed 的兼容性与 published-attempt 约束；不同帧网格会被静默合并，无法保证比较的是同一批不可变结果。
2. 冻结文档的 R0 状态自相矛盾：一部分声明 `Frozen/APPROVED`，另一部分仍声明 `R0_FAIL/ALL_IMPLEMENTATION_WORK_BLOCKED`；在状态未由 owner 原子统一前不能把本模块视为已获准落地。

另有不确定性带未进入图例、异常时间/重复时间静默污染数据、零差异域伪造 ±1 色标、ECharts 生命周期缺少失败保护，以及生产路由/API/i18n 尚未接入等中风险项。

## 实际执行的检查

以下命令均在本轮审查中执行；输出按实际结果记录。

### 1. 指定定向测试（通过）

命令：

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage' && npm --prefix frontend test -- src/features/simulation/test/chart-comparison.test.tsx
```

实际结果：exit 0；`1` 个文件通过、`10` 个测试通过。仅有 Vitest 输出的 `ReactDOMTestUtils.act` deprecated warning。该结果是**单文件定向测试**，不是全量 Vitest 或最终门禁。

### 2. Owned-file TypeScript 检查（通过）

命令：

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend' && files=(src/features/simulation/charts/*.ts src/features/simulation/charts/*.tsx src/features/simulation/comparison/*.ts); node node_modules/typescript/bin/tsc --noEmit --target ES2020 --module ESNext --moduleResolution bundler --jsx react-jsx --strict --skipLibCheck --lib ES2020,DOM,DOM.Iterable "${files[@]}"
```

实际结果：exit 0，无诊断。此检查只覆盖 owned production files，不等同于项目 `tsc -b` 或 build。

### 3. Owned-file ESLint（通过）

命令：

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend' && node node_modules/eslint/bin/eslint.js --config eslint.config.js --report-unused-disable-directives --max-warnings 0 src/features/simulation/charts src/features/simulation/comparison src/features/simulation/test/chart-comparison.test.tsx
```

实际结果：exit 0，无 warning/error。

### 4. Owned-file Prettier（通过）

命令：

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend' && node node_modules/prettier/bin/prettier.cjs --check src/features/simulation/charts src/features/simulation/comparison src/features/simulation/test/chart-comparison.test.tsx
```

实际结果：exit 0，`All matched files use Prettier code style!`。

### 5. Option/边界只读诊断

使用临时文件（命令结束时删除，未修改仓库实现）调用真实 TS option builder/aligner，输出：

```text
{"legend":["Observed","Predicted"],"zeroDomainCss":"linear-gradient(90deg, #1D4ED8 0%, #446DDD 10%, #6C8CE2 20%, #93AAE6 30%, #BBC9EB 40%, #E2E8F0 50%, #DCC7C2 60%, #D5A595 70%, #CF8467 80%, #C8623A 90%, #C2410C 100%)","invalidTimestampTuple":[null,false,1],"duplicateBaseline":9,"duplicateDelta":-4}
```

补充诊断：`node -e "const f=new Intl.NumberFormat('en-US',{maximumFractionDigits:6}); console.log(JSON.stringify({negativeZero:f.format(-0),zero:f.format(0)}))"` 实际输出：

```text
{"negativeZero":"-0","zero":"0"}
```

补充不等帧网格诊断的实际输出为：

```json
[{"timestamp":"2026-09-24T00:00:00.000Z","timestamp_ms":1790208000000,"baseline":1,"candidate":1,"delta":0},{"timestamp":"2026-09-24T00:05:00.000Z","timestamp_ms":1790208300000,"baseline":2,"candidate":null,"delta":null},{"timestamp":"2026-09-24T00:10:00.000Z","timestamp_ms":1790208600000,"baseline":3,"candidate":3,"delta":0}]
```

这证明当前 helper 会把不等网格变成普通 `null` gap，而不是报告 incompatible；也证明 invalid timestamp 会进入 chart option 的非有限时间值、重复绝对时间会后者覆盖前者。

### 6. R0 bundle 只读核对

命令：

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage' && sha256sum docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md docs/hydraulic-twin/PRD_SUPPLEMENT.md docs/hydraulic-twin/SOURCE_PRD.md && git rev-parse HEAD
```

实际输出：

```text
5fa5f57096e5ff7ca44d85797e8476c7f329525f582f2e039e6c882c19558141 *docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md
120b644023d14d42f54d48bb068361964afc5dd98343434d92518449ed741551 *docs/hydraulic-twin/PRD_SUPPLEMENT.md
11e56e68a68017b84b5ae577dede8a74f6217d4a7877f5e358a18f14e66807ee *docs/hydraulic-twin/SOURCE_PRD.md
ca99e0485fb12fbecbd565e79d81f745f8d4a748
```

这与 `artifacts/contracts/v3-independent-review.json:30-42,65-71` 的 live bundle 和 `R0_DESIGN_PASS` 一致；但不消除下列文档正文状态矛盾。

## Findings

### HIGH-01 — A/B 路径没有 fail-closed 的兼容性与 published-attempt 约束

**证据**：

- `frontend/src/features/simulation/comparison/types.ts:35-55` 中 `SimulationComparisonResponse` 只把 baseline/candidate 表示为 `simulation_id` 与可选 label，没有 `published_attempt_id/no` 或其他 result-version 身份；`compatible` 只是静态字段，没有运行时验证。
- `frontend/src/features/simulation/comparison/timeAlignment.ts:27-35` 对两组时间戳直接取 union 并排序，不校验 revision、target manifest、start/end、interval、frame count，也不拒绝不等网格。
- `frontend/src/features/simulation/charts/SimulationComparisonChart.tsx:10-35` 只接收 `AlignedSimulationPoint[]`，没有接收或检查 `SimulationComparisonResponse.compatible`；因此 `difference` 模式可以在没有兼容性证明的情况下渲染。
- 冻结要求 `docs/hydraulic-twin/PRD_SUPPLEMENT.md:482-487` 固定同 revision/manifest/time grid；`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1683-1686` 允许 A/B 指定 result version，`:718-719` 又规定结果只由 `published_attempt_id/no` 发布指针可见。
- 本轮不等网格诊断把 baseline 的 00:00/00:05/00:10 与 candidate 的 00:00/00:10 正常输出为三个 aligned 点，而不是错误。

**影响**：同一 simulation 的不同 published attempt 可能在 DTO 中不可区分；不等帧网格会被误显示为“缺测”，从而生成看似有效的交集 delta。AC-6 的结果身份、不可重跑边界和“不自动重采样/不兼容拒绝”无法由本模块保证。

**修复建议**：在 API decoder/feature boundary 做 closed-schema 校验，并让 chart/comparison orchestrator 显式携带 `compatible`、两侧 `published_attempt_id/no`、revision/manifest hash 与完整 frame grid；任一不一致时禁止同步播放和 `Δ`，而不是只产生 `null` gap。补充 unequal-grid、attempt mismatch、重复 timestamp 的负例测试。

### HIGH-02 — 冻结文档的 R0/授权状态互相矛盾，模块无法获得单一可审计的落地结论

**证据**：

- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2` 与 `docs/hydraulic-twin/PRD_SUPPLEMENT.md:2` 写明 `Frozen v3 — APPROVED`。
- 但 `docs/hydraulic-twin/PRD_SUPPLEMENT.md:36-40` 仍写 `R0_ROUND_3_FAILED / NOT_APPROVED / ALL_IMPLEMENTATION_WORK_BLOCKED`，并禁止实现；`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:38-39` 又说当前状态不是 Frozen，只有新复审通过后才能更新。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2391` 声明 R0 FAIL 时除 `DOC-OWNERSHIP`/`DEP-PREP` 外全部 blocked，`:2430-2436` 同样写 R0 round-3 failed；同仓库 `artifacts/contracts/v3-independent-review.json:65-71` 的实际 verdict 却是 `R0_DESIGN_PASS`。
- 本轮 SHA-256 核对显示 artifact 所绑定的三份 live 文档确实是当前文件，排除了“artifact 过期”这一解释；矛盾来自正文状态未同步。

**影响**：不同 gate/owner 可能对“是否允许 landing/实现”得出相反结论；本模块文件虽已存在，但不能以互相冲突的规范文字作为批准依据。该问题不是通过修改 chart 代码即可安全解决的。

**修复建议**：由 R0/document owner 原子统一文档状态、artifact 引用和 blockedBy 语义；明确 `R0_DESIGN_PASS` 是否已正式解锁实现，并重新执行 owner/migration/handoff 检查。状态未统一前保持 fail-closed，不应把本模块标为已落地。

### MEDIUM-01 — 不确定性带虽绘制但从 ECharts legend 中消失

**证据**：

- `frontend/src/features/simulation/charts/chartOptions.ts:258-289` 创建 `uncertainty-lower` 与 `uncertainty-band`，两者 `name` 都是 `config.labels.uncertainty`。
- `frontend/src/features/simulation/charts/chartOptions.ts:324-330` 却把 `legend.data` 固定为 `[observed, predicted]`，没有 uncertainty 项。
- 本轮真实 option builder 诊断输出 `legend:["Observed","Predicted"]`。
- `frontend/src/features/simulation/test/chart-comparison.test.tsx:207-246` 只断言 band series/data/markLine，没有断言 uncertainty legend 或可访问说明。

**影响**：用户看到紫色面积但无法从图例判断其含义；只依赖颜色的用户/辅助技术用户尤其无法区分“预测线”与“不确定性带”。这弱化了规格中“展示可用不确定性带”的可解释性。

**修复建议**：在图例中提供可识别的 uncertainty band/范围说明（或显式 range legend），并为 band 可见、图例名称、缺测时不连续添加测试；同时提供与图关联的文本/表格替代。

### MEDIUM-02 — 时间输入的 invalid/duplicate 情况会静默污染或覆盖数据

**证据**：

- `frontend/src/features/simulation/comparison/timeAlignment.ts:11-19` 用 `Map<number, number|null>` 写入数据；相同 epoch 的后一个点会无条件覆盖前一个点，未报告冲突。
- `frontend/src/features/simulation/charts/chartOptions.ts:57-66` 对非法 timestamp 使用 `Number.NEGATIVE_INFINITY`，而不是拒绝或提供可见错误；`observed/predicted` 直接传入时不会经过 A/B aligner 的 invalid 过滤。
- 本轮诊断实际得到 `invalidTimestampTuple:[null,false,1]`（JSON 中的 `null` 是 `-Infinity` 的序列化结果，`Number.isFinite` 为 false），以及重复时间的 `duplicateBaseline:9, duplicateDelta:-4`，说明后者覆盖前者。
- `frontend/src/features/simulation/test/chart-comparison.test.tsx:89-125` 只覆盖一个 invalid candidate timestamp 被忽略，没有重复时间、非法 observed/predicted 时间或冲突策略测试。

**影响**：坏数据可能被画到非法 x 坐标、重复帧可能改变 delta，且用户/监控无法知道结果被丢弃或覆盖。对水力指标图这是数据完整性风险，而不只是视觉瑕疵。

**修复建议**：在 feature boundary 严格验证 RFC3339/UTC 与有限值；重复 epoch 应拒绝/报告冲突，非法点应显式进入 error/quality 状态而不是静默改写；补齐重复、非法 observed time、非法 issue time 和非有限值测试。

### MEDIUM-03 — 全部无差异时图例伪造 ±1 的蓝—橙域并显示 `-0`

**证据**：

- `frontend/src/features/simulation/comparison/differenceScale.ts:48-57` 在 `domain <= 0` 时把安全域强制设为 `1`。
- `frontend/src/features/simulation/charts/DifferenceLegend.tsx:27-50` 仍按该域生成端点标签。
- 本轮 `differenceScaleCss(0)` 诊断输出完整蓝—橙渐变（`#1D4ED8` 到 `#C2410C`），而不是全 neutral；独立 Intl 诊断输出 `{"negativeZero":"-0","zero":"0"}`。

**影响**：两场仿真完全相等或所有 delta 缺失时，用户会看到不存在的 ±1 差异范围与负零，降低差异色标可信度。

**修复建议**：零域时渲染单色 neutral/“无差异”状态并把两端标为 0；只有存在非零 delta 时才使用对称域。补充 all-zero、all-null、single-sided-domain 测试。

### MEDIUM-04 — ECharts 初始化/更新/销毁没有失败保护，隐藏或卸载竞态可能使图表抛出

**证据**：

- `frontend/src/features/simulation/charts/useEChart.ts:15-18` 直接 `init`，`:23-26` 直接 `dispose`，`:30-35` 直接 `setOption`，均无 `try/catch`；`ResizeObserver` 不可用时也没有 window-resize fallback。
- 同一 feature 的既有 `frontend/src/features/simulation/rainfall/RainfallChart.tsx:117-125,133-170` 对 init、resize、setOption、dispose 做了保护，说明该生命周期边界已有可复用的 fail-open 模式。
- `frontend/src/features/simulation/test/chart-comparison.test.tsx:16-49` mock 了全部 ECharts module，且 `:267-292` 只覆盖正常 init/首次 setOption/unmount，不覆盖 init/setOption/resize 抛错或真实 canvas。

**影响**：组件在隐藏 tab、零尺寸中间态、浏览器不支持 ResizeObserver 或卸载竞态下可能把异常冒泡到 ErrorBoundary，影响同一 simulation 页面；当前测试不会发现。

**修复建议**：参考 RainfallChart 对 init/resize/setOption/dispose 做边界保护，提供 ResizeObserver 缺失 fallback，并增加失败路径测试。真实浏览器 canvas smoke 仍需由集成验收补上。

### MEDIUM-05 — 生产代码没有消费这些 exports，页面/API/i18n 接入仍是未完成 handoff

**证据**：

- 本轮执行（排除 charts/comparison/test 自身）：
  ```text
  rg -n "(SimulationComparisonChart|ObservedPredictedChart|ComparisonMetricsTable|DifferenceLegend|buildSimulationComparisonOption|buildObservedPredictedOption|alignSimulationSeries)" frontend/src --glob '!**/charts/**' --glob '!**/comparison/**' --glob '!**/test/**'
  ```
  实际无输出。
- `frontend/src/App.tsx:35-100` 没有 `/simulation` route；`frontend/src/i18n/zh.json:1-94` 与 `en.json:1-94` 没有 comparison/simulation 图表 namespace/key。
- 实现摘要 `docs/hydraulic-twin/implementation/comparison-charts.md:80-86` 明确承认 route/page、i18n、runtime API decoder、live ECharts/browser 均未执行，并把 mapping/wiring 留给 integration owner。
- 冻结前端要求 `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1903-1905,1950-1957` 要求 `/simulation`、i18n namespace 和 A/B/图表行为。

**影响**：当前是未接线的 feature-local primitives，不是用户可达的 A/B/预测实测页面；不能据此宣称 AC-6 或预测图表已交付。该项属于明确的 integration handoff，不应在 ownedPaths 内擅自修复，但必须在 release gate 前关闭。

**修复建议**：由 integration owner 接入 typed API decoder、发布 attempt/frame-grid 校验、`/simulation` route、comparison i18n keys，并用真实 API fixture + Chromium smoke 验证 A/B、Δ、预测/实测/band 和缺测 gap。

### LOW-01 — 单测没有覆盖真实 ECharts 注册和关键重渲染/错误路径

**证据**：`frontend/src/features/simulation/test/chart-comparison.test.tsx:16-49` 将 `echarts/core/charts/components/renderers` 全部 mock；`:267-292` 只断言首次 `setOption` 和 unmount，未触发数据 rerender、ResizeObserver callback、实际 module registration 或失败路径；也未覆盖 MEDIUM-01～03 的 legend/zero-domain/invalid-time 情形。

**影响**：option builder 的局部断言容易通过，但 ECharts 实际渲染、band stacking、resize/dispose 和运行时 registration 回归不会被捕获。

**修复建议**：增加 option-level 边界断言、rerender/resize/dispose/error 测试，并至少安排一次真实 ECharts（可用 headless browser）的 smoke；不要把当前 mock 测试当成 AC-6 视觉证据。

## 通过项与边界

- 时间戳等价时区对齐、缺测保持 `null`、`connectNulls:false`、candidate-baseline delta、overlay/split/difference 三种 option 结构、固定 difference endpoint、reduced-motion/>500 点动画门控、`replaceMerge` 和 dispose 的**局部测试**均通过。
- 本轮没有运行全量 Vitest、`npm ci`、生产 build、G0～G6、AC-1～AC-8、真实 API、Chromium canvas 或性能门禁；这些不是本报告的 PASS 证据。
- 本轮未发现 owned code 中直接的 secret 泄漏或明显 XSS 注入点；React 文本渲染和 CSS class 使用没有直接 `dangerouslySetInnerHTML`。这不等于全应用安全审查通过。

## 最终判定

**`approved=false` / NOT_APPROVED**。在 HIGH-01 的兼容性/attempt fail-closed 问题与 HIGH-02 的 R0 状态矛盾关闭，并由 integration owner 完成 MEDIUM-05 的生产接入与真实渲染验收之前，不应将 A/B 与预测实测图标记为可发布或 AC-6 已通过。
