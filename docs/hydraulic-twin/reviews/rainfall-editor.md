# 降雨情景编辑器独立审查

- **模块**：`rainfall-editor`
- **审查角色**：独立只读代码审查者
- **审查日期**：2026-09-25
- **结论**：**NOT_APPROVED / `approved=false`**
- **范围**：`frontend/src/features/simulation/rainfall/**`、`frontend/src/features/simulation/test/rainfall*.test.*`，并对照冻结的 `PRD_SUPPLEMENT.md`、`IMPLEMENTATION_CONTRACT.md`、后端 rainfall schema 以及实现摘要。

## 结论摘要

定向 rainfall Vitest（2 个文件、9 个测试）、owned-file TypeScript、ESLint 和 Prettier 检查均通过；但通过只覆盖正常路径。复核真实实现和边界诊断后发现以下高风险问题：

1. 用户可编辑的非法时间会原样进入 ECharts HTML tooltip，形成 DOM XSS sink。
2. duration 在 72h 校验前直接按用户输入分配样本数组，超大输入可耗尽浏览器内存。
3. 清空 random seed 会在 `onChange` 中抛异常并使编辑器崩溃。
4. 没有 `onSubmit`/`onScenarioSubmit` 时仍显示“Scenario submitted.”，产生明确的假成功。
5. 导出的 submit payload 与冻结后端创建 schema 不兼容，且没有 `Idempotency-Key`/API adapter；当前“提交”不是可落库的 API 提交。

因此本模块不能标记为 approved。另有初始数据静默修正、校验 fail-open、naive timestamp、固定样本数操作死路、表格选择/焦点和默认 CSS class 等中风险问题。

## 实际执行的检查

以下检查均在本轮审查中执行。临时诊断脚本均位于系统临时目录并在命令结束时删除；没有修改实现或测试文件。

### 1. 指定 rainfall 定向测试（通过）

命令：

```text
npm --prefix "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend" test -- --run rainfall
```

实际结果：exit 0；`2 passed` test files，`9 passed` tests。仅有 Vitest/React 的 `ReactDOMTestUtils.act` deprecated warning。该结果不是全量 Vitest、生产 build 或最终门禁。

### 2. owned-file TypeScript（通过）

命令：

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend" && npx tsc --noEmit --target ES2020 --lib ES2020,DOM,DOM.Iterable --module ESNext --moduleResolution bundler --jsx react-jsx --strict --skipLibCheck --esModuleInterop src/features/simulation/rainfall/model.ts src/features/simulation/rainfall/validation.ts src/features/simulation/rainfall/RainfallChart.tsx src/features/simulation/rainfall/RainfallScenarioEditor.tsx src/features/simulation/rainfall/RainfallEditor.tsx src/features/simulation/rainfall/types.ts src/features/simulation/rainfall/validate.ts src/features/simulation/rainfall/index.ts
```

实际结果：exit 0，无诊断；不覆盖项目 `tsc -b`。

### 3. owned-file ESLint（通过）

命令：

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend" && npx eslint "src/features/simulation/rainfall" "src/features/simulation/test/rainfall*.test.*"
```

实际结果：exit 0，无 error/warning。

### 4. owned-file Prettier（通过）

命令：

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend" && npx prettier --check "src/features/simulation/rainfall/**/*.{ts,tsx,css}" "src/features/simulation/test/rainfall*.test.*"
```

实际结果：exit 0，`All matched files use Prettier code style!`。

### 5. 只读边界诊断

本轮使用 `node_modules/.bin/vite-node --script` 调用真实 TS helper/React 组件；以下是与发现直接相关的实际输出摘录：

```text
# duration 259201s / 60s
{"count":4321,"valid":false,"issues":["DURATION_TOO_LONG"]}

# 无 submit handler
{"submittedText":"Scenario submitted.","validation":"Scenario is ready to submit.Scenario submitted."}

# 清空 seed
{"changed":false,"error":"Unable to find an element by: [data-testid=\"rainfall-validation\"]"}
# React error: Error: randomSeed must be finite

# 非法初始 duration
{"rendered":false,"error":"durationSeconds must be a positive integer"}

# naive/date-only timestamps
{"naive":true,"dateOnly":true,"naiveIssues":[],"dateOnlyIssues":[]}

# 缺 metadata / 超长 name
{"valid":true,"issues":[]}
{"valid":true,"nameLength":256}

# add/remove 固定样本数
{"baseValid":true,"addedValid":false,"removedValid":false,"baseCount":5,"addedCount":6,"removedCount":4}

# invalid time tooltip formatter
{"formatted":"<b data-probe=\"xss\">bad</b><br/>1.00 mm/h","label":["<b data-probe=\"xss\">bad</b>"]}

# 默认 class
{"className":null,"hasEditorClass":false}

# remove row 后 selection 被 row click 覆盖
{"selected":"0","row1":"false","row2":"false"}

# 改时间后输入焦点
{"active":null,"value":"2026-01-01T00:00:01.000Z"}
```

对 `generateRainfallSamples({duration_seconds:100000000000, step_seconds:60})` 的同类临时诊断实际以 Node `FATAL ERROR: invalid table size ... JavaScript heap out of memory` 退出（exit 134）；因此上面不是理论上的静态风险。

### 6. 后端 schema 互操作诊断

命令在 `backend` 目录用 Python 调用真实 `RainfallScenarioCreate.model_validate`，输入等价于前端默认 payload（`samples` 而非 `points`，无 `network_id/kind/content_sha256`），实际输出：

```text
('network_id',): missing Field required
('kind',): missing Field required
('points',): missing Field required
('content_sha256',): missing Field required
('samples',): extra_forbidden Extra inputs are not permitted
```

对 custom 场景使用前端 `return_period_years: 'custom'` 时，真实 schema 输出：

```text
('return_period_years',): enum Input should be 1, 2, 3, 5, 10, 20, 50 or 100
```

## Findings

### HIGH-01 — 非法用户时间进入 ECharts HTML tooltip，形成 DOM XSS sink

**证据**：

- `frontend/src/features/simulation/rainfall/RainfallChart.tsx:34-37` 在 `Date.parse` 失败时直接返回原始 `value`。
- `frontend/src/features/simulation/rainfall/RainfallChart.tsx:51-56` 将该原始值拼入 HTML tooltip；`:160-170` 即使校验无效也会把整个 `samples` 数组送入图表。
- 本轮 formatter 诊断对输入 `<b data-probe="xss">bad</b>` 实际输出 `{"formatted":"<b data-probe=\"xss\">bad</b><br/>1.00 mm/h","label":["<b data-probe=\"xss\">bad</b>"]}`。
- ECharts 5.5 在有 DOM 时默认 `renderMode` 为 `html`（`frontend/node_modules/echarts/lib/util/model.js:628-634`），并将字符串 tooltip 赋给 `el.innerHTML`（`frontend/node_modules/echarts/lib/component/tooltip/TooltipHTMLContent.js:301-313`）。
- `frontend/src/features/simulation/test/rainfallEditor.test.tsx:5-26` 完全 mock 了 `RainfallChart`，没有真实 tooltip/DOM sink 覆盖。

**影响**：用户可以把带事件属性的非法时间输入到表格；即使提交被校验阻止，悬停对应柱仍可能把 HTML 注入应用 origin，形成可执行 DOM XSS。这个问题不依赖后端接受该样本。

**修复建议**：不要把未经转义的原始字符串放入 HTML tooltip；使用 `renderMode: 'richText'`/安全文本节点，或对所有用户文本做严格转义并在图表中过滤非法时间。补充真实 ECharts tooltip 的非法时间/XSS 回归测试。

### HIGH-02 — duration 上限在样本分配之后才校验，超大输入可 OOM

**证据**：

- `frontend/src/features/simulation/rainfall/model.ts:150-170` 只检查 duration/step 为正整数，随后按 `getSampleCount()` 直接 `Array.from({length: count})`；没有 `MAX_CUSTOM_DURATION_SECONDS` 或最大样本数保护。
- `frontend/src/features/simulation/rainfall/RainfallScenarioEditor.tsx:158-170` 在 `handleDurationHoursChange`（`:186-190`）进入 `regenerate` 时也只检查正整数/step，没有先拒绝超过 72h 的值；HTML `max` 属性不会阻止 React `onChange` 已经执行。
- 259201 秒/60 秒的真实诊断仍生成了 4321 个样本后才得到 `DURATION_TOO_LONG`：`{"count":4321,"valid":false,"issues":["DURATION_TOO_LONG"]}`。
- 对 100,000,000,000 秒/60 秒的真实 `vite-node` 诊断以 `FATAL ERROR: invalid table size ... JavaScript heap out of memory` 退出（exit 134）。

**影响**：一个键盘输入即可让 simulation 页面分配数十亿对象、阻塞主线程或使标签页崩溃；validation 本身无法保护已发生的分配。这是可由普通用户触发的可用性/资源耗尽问题。

**修复建议**：在生成器、默认工厂和 editor handler 的最前面统一拒绝 `duration_seconds > 259200`、非安全整数和不可接受的样本数；无效草稿只更新元数据并显示错误，不调用 `Array.from`。为超大 duration、负值/小数和边界值增加不分配大数组的测试。

### HIGH-03 — 清空 random seed 会同步抛异常并卸载编辑器

**证据**：

- `frontend/src/features/simulation/rainfall/RainfallScenarioEditor.tsx:200-203` 将空输入转换为 `Number.NaN`，随后无条件调用 `regenerate`。
- `frontend/src/features/simulation/rainfall/RainfallScenarioEditor.tsx:158-170` 的生成条件只检查 duration/step，仍调用 `generateRainfallSamples`。
- `frontend/src/features/simulation/rainfall/model.ts:157-163` 对非有限 seed 抛出 `randomSeed must be finite`。
- 真实 jsdom/vite-node 诊断在清空 seed 后捕获 React error：`{"changed":false,...}`，错误为 `Error: randomSeed must be finite`，组件树被卸载，validation 元素消失。

**影响**：清空再输入 seed 是普通编辑动作，不需要构造异常数据；当前会破坏编辑器状态并丢失用户正在编辑的表单。应显示 `SEED_INVALID`，而不是让事件异常冒泡。

**修复建议**：先提交/保留 seed 的原始编辑状态，只有 seed 为有限整数时才重生成；无效 seed 时保留原样本并展示校验错误。补充空字符串、NaN、负数/小数 seed 的 UI 测试。

### HIGH-04 — 没有提交 handler 仍显示“Scenario submitted.”

**证据**：

- `frontend/src/features/simulation/rainfall/RainfallScenarioEditor.tsx:255-258` 将 `onSubmit ?? onScenarioSubmit` 视为可选；不存在时跳过调用，但随后无条件 `setSubmitted(true)`。
- 真实无 handler 诊断输出：`{"submittedText":"Scenario submitted.","validation":"Scenario is ready to submit.Scenario submitted."}`。
- `frontend/src/features/simulation/test/rainfallEditor.test.tsx:99-114` 只覆盖存在 `onSubmit` mock 的成功路径，没有覆盖无 handler。

**影响**：默认导出的组件会让用户相信情景已经提交/持久化，实际没有任何 transport 或本地持久化动作；这是直接的假成功和数据丢失风险。

**修复建议**：有真实 submit handler 时只在 handler resolve 后显示成功；无 handler 时禁用提交并给出明确配置提示，或把行为命名为仅本地 draft，不能显示“submitted”。

### HIGH-05 — 当前 submit payload 不能满足冻结后端创建契约，也没有提交适配层

**证据**：

- `frontend/src/features/simulation/rainfall/model.ts:31-44` 的 `RainfallScenario` 只有前端 `samples`/`time`/`rainfall_mm_per_hour` 形状，没有 `network_id`、`kind`、`points`、`content_sha256`；`frontend/src/features/simulation/rainfall/validation.ts:340-349` 原样输出 `samples`。
- 冻结实现契约的创建 schema 要求 `network_id`、`kind`、duration/step/source/algorithm/seed、`points` 和 `content_sha256`（`backend/app/schemas/hydraulic_schemas.py:662-673`），并对 point 数量/索引做校验（`:675-704`）；POST rainfall scenario 还要求 `Idempotency-Key`（`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1565-1570`）。
- 本轮把前端等价 payload 交给真实 `RainfallScenarioCreate` 的输出明确列出 5 个错误：缺 `network_id/kind/points/content_sha256`，且 `samples` 为 extra-forbidden；custom 值 `'custom'` 也被真实 enum 拒绝。
- `RainfallScenarioEditor.tsx:45-49,255-257` 只暴露未类型化 callback；实现摘要 `docs/hydraulic-twin/implementation/rainfall-editor.md:115-121` 明确承认 API、鉴权、`Idempotency-Key` 和错误 envelope 尚未接入。

**影响**：若集成层把 callback payload 直接 POST，所有正常 scenario 都会 422；即使自行转换，当前模块没有保证 `kind`/custom 语义、point 索引、content hash、immutable version 和幂等键。规格中的“提交”目前只是可选 callback 边界，不是可验证的持久化提交。

**修复建议**：由 integration/API owner 提供明确的 typed adapter：生成 `points[{point_index,valid_at,intensity_mm_h}]`、计算/校验 canonical `content_sha256`、选择 `kind`/return-period 关系并传 `Idempotency-Key`；在 adapter 完成前应将 UI 文案标为 draft，不能宣称提交已交付。

### MEDIUM-01 — 初始 scenario 会静默修正非法元数据，非法 duration 则直接崩溃

**证据**：

- `frontend/src/features/simulation/rainfall/RainfallScenarioEditor.tsx:60-90` 对非法 return period 改成 10、非法 step 改成 300、非 synthetic source 改成 synthetic；`finiteOr` 还会把非有限 duration 静默改成默认值。
- 真实组件诊断传入 `{return_period_years: 4, step_seconds: 120, source_type: 'other'}` 后输出 `{"selectedPreset":"true","step":"5","valid":"true"}`，原非法值被隐藏且提交可用。
- 同一初始化路径对 `initialScenario={{duration_seconds: 0}}` 真实输出 `{"rendered":false,"error":"durationSeconds must be a positive integer"}`，而不是显示校验错误。

**影响**：加载后端/外部草稿时，非法数据可能被静默改写成另一份情景，造成审计和内容 hash 失真；另一类非法数据直接使页面崩溃。校验层没有机会向用户展示原始问题。

**修复建议**：区分“缺失字段”和“存在但非法字段”；保留非法值并让 validation fail closed，或在明确的 normalization adapter 中记录转换。初始化不得在验证前调用会抛异常/大量分配的生成器。

### MEDIUM-02 — 默认 scenario validator 对必填元数据、长度和 seed 边界 fail-open

**证据**：

- `frontend/src/features/simulation/rainfall/validation.ts:261-285` 在非 partial 模式只检查 name 非空、return period，以及“若存在”时的 source/seed；没有要求 `source_type`、`algorithm`、`random_seed` 存在，也没有 name 长度、algorithm 长度或 signed-64-bit seed 边界。
- 本轮缺 metadata 的真实诊断输出 `{"valid":true,"issues":[]}`；256 字符 name 也输出 `{"valid":true,"nameLength":256}`。
- 真实前端 validator 对 `2**63` 和 `2**53+2` 都输出 `valid:true`，而 backend `_validate_seed`（`backend/app/services/hydraulic/rainfall.py:271-280`）拒绝 `2**63`，JavaScript number 还会丢失 64 位 seed 精度。
- `RainfallScenarioEditor.tsx:131,249-257` 将该 validator 作为唯一提交门禁。

**影响**：公共 validator/`onChange` payload 可以看起来有效但无法通过冻结后端 schema；超长名称和越界 seed 会在提交后才失败，且大整数 seed 可能已经被错误取整/哈希。

**修复建议**：full 模式明确要求所有 wire 必填字段，执行 name/algorithm 长度和 signed-64-bit 安全整数校验；对超出 JS safe integer 的 seed 使用字符串/大整数边界并在生成前拒绝。`partial` 只能由明确调用者显式开启。

### MEDIUM-03 — validator 接受 backend 拒绝的 naive/date-only timestamp

**证据**：

- `frontend/src/features/simulation/rainfall/validation.ts:165-167` 只用 `Date.parse`，不检查 RFC3339、时区或 UTC 标记。
- `frontend/src/features/simulation/rainfall/model.ts:22-27` 的类型注释却承诺 UTC RFC 3339。
- 本轮真实诊断对 `2026-01-01T00:00:00` 和 `2026-01-01`（后续时间带 Z）均输出 `valid:true`、无 issue。
- 真实 backend `RainfallPoint.valid_at` 是 `AwareDatetime`（`backend/app/schemas/hydraulic_schemas.py:656-660`），对上述两个值均输出 `ValidationError`。

**影响**：编辑器会启用提交一个后端必拒绝的时间序列；若 transport 做隐式本地时区转换，还会改变事件时间和 hash，造成跨环境不可复现。

**修复建议**：在 UI validator 中要求带时区的 RFC3339（并统一规范为 UTC），拒绝 naive/date-only/非法 offset；补充前端与 backend schema 的成对 fixture。

### MEDIUM-04 — Add/Remove sample 在固定 count 契约下是死路

**证据**：

- `RainfallScenarioEditor.tsx:223-240` 的 Add/Remove 只改变 `samples`，不调整 duration、step 或期望 count。
- 冻结要求 `samples.length=floor(duration/step)+1`（`PRD_SUPPLEMENT.md:293-300`；`docs/hydraulic-twin/contracts/frontend.md:238-240`）。
- 真实诊断对一个合法 5 点场景执行等价 add/remove 得到：`{"baseValid":true,"addedValid":false,"removedValid":false,"baseCount":5,"addedCount":6,"removedCount":4}`。
- 现有测试没有点击 Add/Remove 或断言 count 变化后的可提交路径。

**影响**：从合法草稿点击任一编辑按钮都会立即进入不可提交状态；表格没有提供同步修改 duration/step 的路径，用户无法通过自定义表格完成合法增删。

**修复建议**：若 count 固定，移除这些按钮或明确它们只用于构造无效草稿；若支持增删，则同时计算并更新 duration/step（并处理最后 partial interval），提交前展示可恢复的 count 错误。补充 add/remove 后的合法提交测试。

### MEDIUM-05 — Remove 按钮事件冒泡会覆盖 selection，破坏图表联动

**证据**：

- `RainfallScenarioEditor.tsx:236-241` 的 `handleRemoveSample` 明确 `setSelectedIndex(null)`。
- 但删除按钮位于带 `onClick={() => setSelectedIndex(index)}` 的 `<tr>` 内（`:398-402,433-436`），事件冒泡后会把已删除行的旧 index 再次写回。
- 真实组件诊断删除第 1 行后输出 `{"selected":"0","row1":"false","row2":"false"}`；图表仍选中 index 0，而处理函数的意图是清空选择。

**影响**：删除行后图表高亮和表格选择可能错位，尤其在连续删除/分页场景中会误导用户以为编辑了另一行。

**修复建议**：对删除按钮 `event.stopPropagation()`，或把行选择 handler 限定为非按钮目标；删除后按新的相邻行规则明确选择/清空，并覆盖首行、末行和分页删除测试。

### MEDIUM-06 — 行 key 包含可编辑 time，时间编辑会卸载输入并丢焦点

**证据**：

- `RainfallScenarioEditor.tsx:398-400` 使用 `key={`${index}-${sample.time}`}`。
- 用户在 `RainfallScenarioEditor.tsx:405-411` 修改 time 后，key 必然变化，React 会卸载并重建该 `<tr>`。
- 真实 jsdom 诊断将已聚焦的 time input 改为 `2026-01-01T00:00:01.000Z` 后输出 `{"active":null,...}`，焦点从输入框消失。

**影响**：自定义时间字段无法连续输入，编辑体验和可访问性均受影响；该缺陷不会被只检查 value/validation 的测试发现。

**修复建议**：使用稳定 row id（至少 index/持久 point id）作为 key；补充编辑 time 前后 `document.activeElement` 保持不变的测试。

### MEDIUM-07 — 默认渲染没有应用 rainfall-editor CSS class

**证据**：

- `RainfallScenarioEditor.tsx:271-276` 将 `<form className={className}>` 直接作为唯一 class；默认 `className` 为 `undefined`，没有默认 `rainfall-editor`。
- `frontend/src/features/simulation/rainfall/rainfall.css:1-189` 的所有选择器都以 `.rainfall-editor` 开头。
- 真实默认渲染诊断输出 `{"className":null,"hasEditorClass":false}`。

**影响**：直接使用公开组件（测试和默认调用方式）时，深色面板、响应式 grid、表格滚动、选中行和分页样式全部不生效；只有调用者额外传入字面量 `rainfall-editor` 才能恢复 CSS。

**修复建议**：始终合并默认 class 与调用者 class（例如 `rainfall-editor ${className ?? ''}`），并增加默认渲染 class 断言/视觉 smoke。

## 通过项与边界

- 本轮确认的正常路径：快捷 return-period 切换、72h/4321 点边界、负值/NaN 强度阻断、确定性生成、图表 option 与同一 `samples` 数组的基本数据来源、mock 下的图表选中/表格编辑同步、带 callback 的同步 submit。
- `rainfallEditor.test.tsx` 的 chart mock 不能证明真实 ECharts tooltip、canvas、ResizeObserver、点击事件或 DOM 安全；真实浏览器/Chromium smoke 本轮未运行。
- 本轮没有运行仓库全量 Vitest、`npm run build`、全量 lint/format、真实 rainfall API、数据库或完整 gate；这些均不能被本报告宣称为通过。
- 未发现实现中直接使用 `dangerouslySetInnerHTML`；但 ECharts 自身的 HTML tooltip sink 仍构成上述 XSS 风险。

## 最终判定

**`approved=false` / NOT_APPROVED**。至少 HIGH-01～HIGH-05 必须关闭后才能重新审查：先修复非法时间到 HTML tooltip 的注入、超大 duration 的无界分配、清空 seed 的异常、无 handler 的假成功和不可提交的 wire/API 边界；随后补齐上述中风险交互与 fail-closed 校验测试。
