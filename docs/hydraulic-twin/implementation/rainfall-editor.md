# 降雨情景编辑器实施摘要

## 状态

**编辑器、校验、图表联动和单测已实现；本模块的定向检查通过。仓库级全量测试、lint 和 build 仍有本任务范围外的失败，未宣称全仓通过。**

本次实现依据：

- `docs/hydraulic-twin/SOURCE_PRD.md:14-18`：React/TypeScript/ECharts 技术栈、降雨重现期/步长/72h/可复现雨型目标；
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:292-300`：重现期只允许 `1/2/3/5/10/20/50/100/custom`、步长 `1/5/15 min`、`sample_count=floor(duration/step)+1`、非负/有限数/严格递增时间等提交前校验；
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:596-630`：scenario/sample 字段、`step_seconds=60|300|900`、`duration_seconds<=259200`、synthetic source 和不可变内容约束；
- 现有前端测试/脚本模式：`frontend/package.json:14-68`、`frontend/vitest.config.ts:4-23`。

## 实际改动

### 1. 领域模型与快捷情景

- 新增 `frontend/src/features/simulation/rainfall/model.ts:8-20`：重现期、步长、72h 上限、synthetic 默认算法和默认 seed。
- `model.ts:22-57` 提供与后端契约一致的 snake_case `RainfallScenario`/`RainfallSample` 类型。
- `model.ts:89-100` 实现 `getSampleCount()`，按冻结契约使用 `floor(duration/step)+1`。
- `model.ts:109-183` 使用固定整数 hash 生成可复现、无 `Math.random()` 的 synthetic 雨型；相同 seed/重现期/时长/步长得到相同样本。
- `model.ts:185-245` 提供默认、自定义、快捷情景工厂和 `QUICK_RETURN_PERIODS`/`RETURN_PERIOD_OPTIONS` 别名。

### 2. 纯校验层

- 新增 `frontend/src/features/simulation/rainfall/validation.ts:10-51`：稳定的校验 code/issue/result 类型，包含 `valid` 与 `isValid` 兼容字段。
- `validation.ts:99-243` 同时支持对象参数和 `(samples, durationSeconds, stepSeconds)` 调用方式，校验：
  - 步长是否合法；
  - 时长为正整数且不超过 72h；
  - 样本数量是否为 `floor(duration/step)+1`；
  - 时间可解析、严格递增、落在所选步长网格，并覆盖所有完整时间间隔；
  - 每个强度为有限且非负数。
- `validation.ts:251-329` 提供 scenario 级校验、便捷别名和稳定提交 payload 序列化。

### 3. 编辑器与提交

- 新增 `frontend/src/features/simulation/rainfall/RainfallScenarioEditor.tsx:40-52`：`initialScenario`/`scenario`、`onSubmit`/`onScenarioSubmit`、`onChange`、`onValidationChange`、disabled 和自定义提交文案接口。
- `RainfallScenarioEditor.tsx:142-199`：切换重现期、时长、步长、seed 时确定性重建样本；保留 UTC 起点。
- `RainfallScenarioEditor.tsx:201-242`：表格行时间/强度编辑、添加/删除行，以及图表选中柱到表格分页的联动。
- `RainfallScenarioEditor.tsx:247-262`：只有校验通过才提交；支持同步/异步提交，阻止重复提交并显示提交错误。
- `RainfallScenarioEditor.tsx:286-359`：重现期快捷按钮、时长（小时）、步长和 seed 控件。
- `RainfallScenarioEditor.tsx:361-469`：ECharts 柱图、可编辑分页表格（每页 50 行）和分页控制。
- `RainfallScenarioEditor.tsx:473-496`：可访问的校验/提交状态；无效数据禁用提交按钮。
- 新增 `frontend/src/features/simulation/rainfall/rainfall.css:0-189`：深色面板、响应式控件、表格滚动和选中行样式。

### 4. ECharts 柱图

- 新增 `frontend/src/features/simulation/rainfall/RainfallChart.tsx:1-8`：只注册 Bar/Grid/Tooltip/Canvas 的 modular ECharts。
- `RainfallChart.tsx:25-92`：将同一 `samples` 数组转换为柱图数据，支持选中柱高亮、tooltip、UTC 标签。
- `RainfallChart.tsx:100-171`：实例生命周期、ResizeObserver/窗口 resize、点击选择回调、`replaceMerge` 增量更新和卸载 dispose。
- `RainfallChart.tsx:174-184` 暴露 `data-testid="rainfall-chart"`、点数和当前选择索引，便于无障碍/测试检查。

### 5. 公共导出与测试

- `frontend/src/features/simulation/rainfall/index.ts`：导出模型、校验、图表和编辑器公共 API。
- `frontend/src/features/simulation/rainfall/RainfallEditor.tsx`、`types.ts`、`validate.ts`：兼容性命名出口，不改变核心 API。
- `frontend/src/features/simulation/test/rainfallValidation.test.ts:10-96`：覆盖 72h 边界、1 分钟 4321 点、超过 72h、负数/NaN、样本数量、时间严格递增、快捷情景可复现性。
- `frontend/src/features/simulation/test/rainfallEditor.test.tsx:32-125`：覆盖快捷选择、72h UI 阻断、负值阻断、图表选中/表格编辑同步、提交 payload 和图表 series 数据来源。

## 验证命令与真实结果

### 本模块定向检查（通过）

```text
$ npm --prefix "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage\frontend" test -- --run rainfall
Test Files  2 passed (2)
Tests       9 passed (9)
exit 0
```

运行中有 React Testing Library 的 `ReactDOMTestUtils.act` deprecated warning，但没有测试失败。

```text
$ cd "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage\frontend" && npx tsc --noEmit --target ES2020 --lib ES2020,DOM,DOM.Iterable --module ESNext --moduleResolution bundler --jsx react-jsx --strict --skipLibCheck --esModuleInterop src/features/simulation/rainfall/model.ts src/features/simulation/rainfall/validation.ts src/features/simulation/rainfall/RainfallChart.tsx src/features/simulation/rainfall/RainfallScenarioEditor.tsx src/features/simulation/rainfall/RainfallEditor.tsx src/features/simulation/rainfall/types.ts src/features/simulation/rainfall/validate.ts src/features/simulation/rainfall/index.ts
exit 0
```

```text
$ cd "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage\frontend" && npx eslint "src/features/simulation/rainfall" "src/features/simulation/test/rainfall*.test.*"
exit 0
```

```text
$ cd "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage\frontend" && npx prettier --check "src/features/simulation/rainfall/**/*.{ts,tsx,css}" "src/features/simulation/test/rainfall*.test.*"
All matched files use Prettier code style!
exit 0
```

### 仓库级回归（未通过，未归因于本模块）

```text
$ npm --prefix "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage\frontend" test -- --reporter=dot
Test Files  1 failed | 16 passed (17)
Tests       2 failed | 81 passed (83)
exit 1
```

唯一失败文件为 owned paths 之外的 `src/test/__tests__/LoginPage.test.tsx`，两个失败均为 `localStorage.clear is not a function`（`LoginPage.test.tsx:24`）。本模块两个 rainfall 测试文件在同次全量运行中通过。

```text
$ npm --prefix "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage\frontend" run build
tsc -b 失败；错误位于 src/features/simulation/model/errors.ts、normalizers.ts
exit 1
```

本模块所有源文件的独立 `tsc --noEmit` 已 exit 0；Vite production bundle 未执行到，因为项目级 `tsc -b` 先失败。

```text
$ npm --prefix "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage\frontend" run lint
42 problems (42 errors, 0 warnings)
exit 1
```

全量 lint 报错位于 `frontend/e2e/run-ac-smoke.mjs` 和 `frontend/performance/run-twin-map.mjs`；本模块定向 eslint 已 exit 0。

## 未完成/风险与集成事项

1. **提交目前是 typed callback 边界。** `RainfallScenarioEditor` 不直接伪造不存在的 rainfall REST endpoint；集成层应传入 `onSubmit`，并将 `Idempotency-Key`、鉴权和后端错误 envelope 接到该回调。现有 `frontend/src/services/api.ts:11-39` 仍是旧 API client，未在本 owned path 外修改。
2. **路由、i18n 和页面编排未修改。** `/simulation` 路由、菜单和翻译键属于其他 owner；本模块提供可挂载的 `RainfallScenarioEditor`/`RainfallEditor` 公共出口。
3. **图表单测使用可控 chart mock，纯 option 测试使用真实 ECharts option builder。** 真实浏览器 canvas resize/点击和 E2E 尚未运行；实际实例对 jsdom/零尺寸容器有 fail-open 保护。
4. **synthetic 雨型是 UI 侧确定性 fixture。** 后端仍是提交后权威来源；编辑器没有把客户端曲线当作事实源。
5. **表格采用 50 行分页。** 72h/1min 的 4321 个样本全部进入图表和校验，只有可见表格行分页渲染，避免一次性创建数千 DOM 行。
6. 仓库级 build/lint/full Vitest 的范围外失败需要由对应 integration/test owner 处理；本任务未覆盖或伪造这些文件的修复，也未提交或推送任何 commit。
