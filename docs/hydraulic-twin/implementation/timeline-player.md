# 内涝时间轴播放器实施记录

- 日期：2026-09-24
- 模块 owner：`timeline-player`
- 状态：owned scope 已实现；本模块定向测试、类型检查、ESLint、Prettier 均通过。全量前端门禁仍因其他 owner 的并行改动失败，未宣称全绿。
- 提交/推送：未提交、未推送。

## 1. 需求与契约依据

- 源 PRD 的 FR-6 要求淹没面时间轴支持播放/暂停、拖拽和 1/2/4 倍速，AC-3 要求 24 h 仿真结果可回放：`docs/hydraulic-twin/SOURCE_PRD.md:26,32`。
- 冻结补充契约要求 60 s 连续 pan/zoom + 4x 播放仍达到 `presented FPS >=25`、frame-time P95 `<=50 ms`，并禁止把 90 天帧放入浏览器持久化：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:435-450`。
- 冻结实施契约规定：
  - UI 文案必须由 integration owner 接入 `timeline` i18n namespace：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1905`；
  - FrameCache 请求采用 current P0、next/previous P1、最多 2 个并发：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1930-1938`；
  - canonical 速度为 1/2/4 frames/s，使用 `performance.now()` accumulator，不插值水力状态，页面 hidden 暂停且不自动恢复，键盘支持 Space/Left/Right/Shift/Home/End：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1940-1948`；
  - `frontend/src/features/simulation/timeline/**` 的 owner 是 `timeline-player`，禁止水力插值：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2320-2330`。
- 实施前只读核验了后续正式门禁工件：
  - `artifacts/contracts/v3-independent-review.json:29-41,65-71` 为 `R0_DESIGN_PASS`；
  - `artifacts/contracts/dependency-config-handoff.json:207-212` 为 `post-handoff`；
  - `artifacts/contracts/r0-gate-execution.json:2-20,101` 记录 migration/dependency checker 均 exit 0；
  - 现场重算三份文档 SHA-256 与 canonical bundle hash，bundle 为 `5b9b2e3f54bb910706a2d130ff564ebaf3e2292c9db3ace449b62cd5006fa55d`，与正式 R0 artifact 一致。

## 2. 真实代码改动

### 2.1 播放器 hook

新增 `frontend/src/features/simulation/hooks/useTimelinePlayer.ts`：

- 公共 API、注入式虚拟时钟/RAF scheduler、1/2/4 类型和控制器接口见 `useTimelinePlayer.ts:2-47`；调用方只注入 frame 元数据/预取函数，不把水力数组交给 hook，见 `useTimelinePlayer.ts:60-78`。
- 单一离散帧时钟使用 RAF + accumulator；1x/2x/4x 对应 1000/500/250 ms，迟到超过 250 ms 的 RAF gap 被丢弃，末帧自动停止，见 `useTimelinePlayer.ts:394-458`。
- `seek`、`step`、拖拽开始/100 ms preview/最终 commit/取消以及恢复原播放状态见 `useTimelinePlayer.ts:315-392`。`endScrub(finalFrameIndex)` 强制提交最终指针值，避免最后一次 preview 被 100 ms 节流吞掉。
- 页面 `visibilitychange` 隐藏时立即暂停；恢复可见后保持暂停，必须显式再次播放，见 `useTimelinePlayer.ts:298-313,400-406`。
- `timelineKey` 切换会暂停、清 accumulator、重置 frame、清空 scrub/prefetch，见 `useTimelinePlayer.ts:223-241`。
- 预取按 `current+1, current+2, current-1` 排序，最多 2 个相邻预取并发；同帧去重，seek/key/显式清理/卸载时 abort 过期请求，见 `useTimelinePlayer.ts:129-206,243-296`。
- `onFrameChange`、`onPlayingChange`、`onPrefetchError` 通过 latest-ref 对外通知，避免把大 frame payload 放入 React 状态。

### 2.2 时间轴 UI

新增：

- `frontend/src/features/simulation/timeline/TimelineControl.tsx`
- `frontend/src/features/simulation/timeline/timeline.css`
- `frontend/src/features/simulation/timeline/index.ts`

功能与可访问性：

- 播放/暂停按钮、1×/2×/4×分段按钮、原生 range slider、模拟时刻、`x/N`、分钟/帧标签：`TimelineControl.tsx:16-52,186-264`。
- 指针拖拽在 down 时暂停，change 发布节流 preview，up 强制提交最终值并恢复原状态，cancel/lost capture 回滚：`TimelineControl.tsx:97-137,223-240`。
- Space、Left/Right、Shift+Left/Right、Home/End：`TimelineControl.tsx:139-172`。
- hidden 和 A/B 同步不可用状态通过调用方提供的本地化 labels 显示；组件没有硬编码中文：`TimelineControl.tsx:16-29,200-209`。
- 样式沿用现有暗黑指挥中心主题，包含 focus-visible、响应式布局和 `prefers-reduced-motion`；视觉动效不参与回放正确性。

### 2.3 深度图例

新增 `frontend/src/features/simulation/timeline/DepthLegend.tsx`：

- 接收调用方提供的 title/unit/ordered stops；公开类型和组件说明见 `DepthLegend.tsx:3-24`。
- 每档同时展示数值范围、单位和色块；最高档支持 `Infinity` 并显示 `>=min`，不以颜色作为唯一信息：`DepthLegend.tsx:31-47`。
- 色带、焦点、移动端布局由 `timeline.css` 提供。

### 2.4 导出面

`frontend/src/features/simulation/timeline/index.ts:1-10` 导出 `TimelineControl`、`DepthLegend`、hook 及其公共类型，便于 integration owner 从 timeline feature 边界接入。

## 3. 单测

新增：

- `frontend/src/features/simulation/test/timelinePlayer.test.ts`
  - 虚拟 RAF 时间和 1x/2x/4x：`timelinePlayer.test.ts:33-51,350-377`；
  - late RAF reset、seek/末帧/倍速 accumulator reset：`:53-79,312-348`；
  - 100 ms 拖拽节流、最终 commit、恢复播放：`:276-310`；
  - 预取并发上限、去重、过期 abort、卸载清理和 listener 清理：`:172-237`；
  - 页面 hidden 暂停且不自动恢复：`:239-274`；
  - A/B 同步禁用与 timeline identity reset：`:119-156`。
- `frontend/src/features/simulation/test/timelineControl.test.tsx`
  - 时间标签、1/2/4 倍速、键盘 seek、播放状态：`:29-59`；
  - pointer scrub 暂停/恢复：`:61-86`；
  - 深度图例数值范围和单位：`:89-108`。

## 4. 本轮实际执行的检查

### 4.1 通过

1. 定向 Vitest（最终版本）：

```text
$ npm --prefix /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend test -- src/features/simulation/test/timelinePlayer.test.ts src/features/simulation/test/timelineControl.test.tsx
Test Files  2 passed (2)
Tests       19 passed (19)
```

2. owned production 文件独立 TypeScript 检查：

```text
$ cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend && node_modules/.bin/tsc --noEmit --target ES2020 --useDefineForClassFields true --lib ES2020,DOM,DOM.Iterable --module ESNext --moduleResolution bundler --allowImportingTsExtensions --resolveJsonModule --isolatedModules --jsx react-jsx --skipLibCheck --strict --types vite/client src/vite-env.d.ts src/features/simulation/hooks/useTimelinePlayer.ts src/features/simulation/timeline/TimelineControl.tsx src/features/simulation/timeline/DepthLegend.tsx src/features/simulation/timeline/index.ts
(exit 0, no output)
```

3. owned production + tests + setup 的 TypeScript 检查：

```text
$ cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend && node_modules/.bin/tsc --noEmit --target ES2020 --useDefineForClassFields true --lib ES2020,DOM,DOM.Iterable --module ESNext --moduleResolution bundler --allowImportingTsExtensions --resolveJsonModule --isolatedModules --jsx react-jsx --skipLibCheck --strict --types vite/client,vitest/globals,node src/vite-env.d.ts src/test/setup.ts src/features/simulation/hooks/useTimelinePlayer.ts src/features/simulation/timeline/TimelineControl.tsx src/features/simulation/timeline/DepthLegend.tsx src/features/simulation/timeline/index.ts src/features/simulation/test/timelinePlayer.test.ts src/features/simulation/test/timelineControl.test.tsx
(exit 0, no output)
```

4. owned ESLint：

```text
$ /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/node_modules/.bin/eslint --config /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/eslint.config.js /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/timeline/DepthLegend.tsx /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/timeline/TimelineControl.tsx /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/timeline/index.ts /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/hooks/useTimelinePlayer.ts /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/test/timelinePlayer.test.ts /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/test/timelineControl.test.tsx --report-unused-disable-directives --max-warnings 0
(exit 0, no output)
```

5. owned Prettier：

```text
$ /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/node_modules/.bin/prettier --check /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/timeline/DepthLegend.tsx /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/timeline/TimelineControl.tsx /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/timeline/index.ts /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/timeline/timeline.css /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/hooks/useTimelinePlayer.ts /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/test/timelinePlayer.test.ts /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/test/timelineControl.test.tsx
All matched files use Prettier code style!
```

### 4.2 全量门禁失败（均在本模块 owned paths 之外）

1. 全量 Vitest：

```text
$ npm --prefix /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend test
Test Files  1 failed | 16 passed (17)
Tests       2 failed | 81 passed (83)
Failure: frontend/src/test/__tests__/LoginPage.test.tsx:24
TypeError: localStorage.clear is not a function
```

本模块 19 个测试在该次全量运行中仍全部通过。

2. 全量 production build：

```text
$ npm --prefix /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend run build
exit 1
```

`tsc -b` 失败来自并行 owner 文件：`src/features/simulation/model/errors.ts`、`model/normalizers.ts`、`api/client.ts`、`model/taskState.ts`、`store/simulationStore.ts`、`test/fixtures/index.ts`。本轮未越界修改这些问题。

3. 全量 ESLint：

```text
$ npm --prefix /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend run lint
exit 1: 43 errors, 2 warnings
```

剩余问题位于 `frontend/e2e/run-ac-smoke.mjs`、`frontend/performance/run-twin-map.mjs`、`frontend/src/features/simulation/api/client.ts`；owned timeline 文件不在最终错误列表中。

4. 全量 format check：

```text
$ npm --prefix /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend run format:check
exit 1: 47 pre-existing/out-of-scope files reported
```

owned timeline 文件不在警告列表，且定向 Prettier 通过。

## 5. 风险与 integration handoff

1. **i18n 尚未接线**：`frontend/src/i18n/**` 和 `SimulationPage.tsx` 不在本任务 owned paths。integration owner 必须从 `timeline` namespace 构造 `TimelineControlLabels` 并传入 `TimelineControl`；组件本身没有新增硬编码中文。
2. **FrameCache 协调仍是跨模块责任**：本 hook 最多并发 2 个“相邻预取”，但 current P0 与 P1 的总并发 2 必须由 FrameCache/integration 层共享协调；`prefetchFrame` 应接入该缓存的 promise 去重/AbortController，而不是另建 fetch 队列。
3. **A/B 同步**：`playbackDisabled={false}` 只应在 comparison 的 revision/manifest/time grid 已判定 compatible 时传入；`timelineKey`/`prefetchKey` 应包含 simulation/result identity，切换时本实现会暂停并清空旧请求。
4. **全量门禁未绿**：不得把本模块定向通过写成全量 Vitest/build/lint/format 通过；需由对应 owner 修复上述并行文件后重跑。
5. **性能证据未生成**：本任务未运行 60 s 地图 benchmark，不能宣称 25 fps、frame-time P95 或 FrameCache 256 MiB 门禁通过。
6. **文档历史状态 handoff**：`PRD_SUPPLEMENT.md` / `IMPLEMENTATION_CONTRACT.md` 尾部仍保留旧 `R0_ROUND_3_FAILED` 历史文字，但顶部 Frozen 状态和正式 R0/dependency/gate 工件均为 PASS；该陈旧文字应由 `delivery-docs` 后续清理，本任务未越界修改冻结文档。

## 6. 变更文件清单

```text
frontend/src/features/simulation/hooks/useTimelinePlayer.ts
frontend/src/features/simulation/timeline/DepthLegend.tsx
frontend/src/features/simulation/timeline/TimelineControl.tsx
frontend/src/features/simulation/timeline/index.ts
frontend/src/features/simulation/timeline/timeline.css
frontend/src/features/simulation/test/timelineControl.test.tsx
frontend/src/features/simulation/test/timelinePlayer.test.ts
docs/hydraulic-twin/implementation/timeline-player.md
```
