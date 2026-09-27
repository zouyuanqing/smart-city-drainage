# 内涝时间轴播放器独立审查

- 审查模块：`timeline-player`
- 审查日期：2026-09-25
- 结论：**不批准（changes requested）**
- 严重度汇总：High 2，Medium 1，Low 1
- 审查方式：只读核对冻结契约、实施记录、真实源码、现有调用关系和指定测试；临时定向诊断文件均在执行后删除。

## 1. 结论

基础播放语义实现正确：指定测试 19/19 通过，1x/2x/4x、末帧停止、seek 重置时钟、100 ms 拖拽节流、hidden 暂停、预取并发/去重/abort/卸载清理均有覆盖。UI 也提供键盘替代、焦点样式和 reduced-motion 处理。

但当前实现仍有两个高风险问题：

1. 预取失败后会在 `finally` 中立即重新泵取同一帧，没有错误分类、次数上限或退避，持续 503/网络错误会形成紧循环请求风暴，也不满足冻结的 410/409/422/429/503 语义。
2. 播放器尚未接入生产页面/FrameCache/Store；仓库内没有 `/simulation` 路由或任何生产调用点，AC-3 fixture 要求的 `simulation-frame-timeline` 与 `simulation-playback-4x` 选择器也不存在。因此“预取”和“24 h 回放”目前只是组件能力，不是可交付的用户流程。

另有一个中风险边界：页面隐藏或 A/B 播放能力在拖拽中被关闭后，陈旧的 `resumeAfterScrubRef` 会在稍后 `endScrub` 时恢复播放，违背“hidden 返回后必须由用户恢复”和不兼容状态不得同步播放。

## 2. Findings

### High 1 — 预取错误被无界立即重试，可能形成请求风暴

**问题**

`pumpPrefetch` 的失败分支调用 `onPrefetchError` 后没有记录失败、退避或终止状态；`finally` 删除 in-flight 记录后立即再次调用 `pumpPrefetch`。同一 frame index 仍属于 desired 集合且不在 completed 集合，所以会被立即重新请求。冻结契约要求 410 停止播放、409 刷新 metadata、422 不重试、429 遵守 `Retry-After`、503 仅按 0.5/1/2 s 且最多重试；当前 hook 对所有错误一视同仁。

**证据**

- `frontend/src/features/simulation/hooks/useTimelinePlayer.ts:135-149`：只要 frame 仍 desired 且未 completed，就重新创建请求。
- `frontend/src/features/simulation/hooks/useTimelinePlayer.ts:168-179`：错误仅通知回调，finally 立即再次 pump。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1935-1938`：冻结的 frame 错误、优先级和重试语义。
- 定向诊断命令：临时 Vitest 诊断让前 6 次预取立即 reject，之后挂起；输出 `PREFETCH_RETRY_CALLS 8`，证明仅刷新微任务便发生多次重发。临时文件已删除。
- 现有测试 `frontend/src/features/simulation/test/timelinePlayer.test.ts:171-236` 只覆盖 resolve/abort/unmount，没有 rejection、410/422/429/503 或 retry ceiling。

**影响**

服务端持续不可用时会产生无间隔网络请求，占用浏览器连接与 API 配额，并可能把非重试错误误当临时故障；4x 播放时该行为尤其容易扩大。

**修复建议**

把失败策略放在共享 FrameCache/request coordinator：区分 terminal、retryable 与 `Retry-After`；维护每 key 尝试次数/next-at；失败后不立即递归 pump。对 410 通知外层停止播放，对 409 刷新 metadata，422 固定失败，503 最多 0.5/1/2 s，429 使用服务端等待时间。补 reject 与错误分类测试。

### High 2 — 播放器未接入生产用户流程，AC-3 所需 DOM 不存在

**问题**

本次改动仅新增 hook、组件和导出；仓库没有生产调用点，`/simulation` 路由和 `SimulationPage` 也不存在。FrameCache 已由 simulation-map owner 实现，但 timeline 的 `prefetchFrame` 没有接到它；timeline 状态也没有同步到现有 simulation store。结果是默认不预取、时间轴不可由用户访问，现有 19 个测试仍可通过。

**证据**

- `frontend/src/App.tsx:35-100`：路由只有 `/`、`/login`、`/dashboard`、`/map`、`/video`、`/alerts`、`/settings`，无 `/simulation`。
- `frontend/src/features/simulation/timeline/TimelineControl.tsx:74-87`：hook 在组件内独立运行；只有调用方显式传入 callback/prefetch 才连接外部系统。
- `frontend/src/features/simulation/store/simulationStore.ts:20-25,86-89,253-275`：已有 timeline store 状态和命令，但生产代码没有调用 `setFrameIndex`、`setPlaying` 或 `setPlaybackSpeed`。
- `frontend/src/features/simulation/map/frameCache.ts:442-485`：已有 `FrameCacheClient`/scheduler，可作为真实加载和统一并发入口。
- 仓库搜索（排除测试和实施记录）只找到组件自身对 hook 的调用，没有其他生产引用。
- `frontend/e2e/fixtures/ac-smoke-v1.json:14-45`：AC-3 场景要求 `/simulation`、`data-testid="simulation-frame-timeline"` 与 `data-testid="simulation-playback-4x"`，这些均不存在。
- `frontend/src/features/simulation/timeline/TimelineControl.tsx:176-185,250-263`：只输出 `data-frame-index`/`data-playing`，没有上述 test id。

**影响**

交付物无法在应用中使用；预取、FrameCache 并发、Store 单一时间轴、A/B 单 clock 和 i18n 接线均未被真实路径验证。模块内“定向测试通过”不能等价于 FR-6/AC-3 播放器完成。

**修复建议**

由 integration owner 把 `TimelineControl` 接入 `/simulation`：用 `frameIndex/playing/speed` 细粒度 selector 同步 Zustand；以同一 `FrameCacheClient` 加载 current P0，并由共享 scheduler 发出 next/previous P1；将 identity 放进 `timelineKey`/`prefetchKey`；为 AC-3 提供稳定 test id；补一条真实 route → manifest 289 frames → play/pause/seek/1x/2x/4x 的 E2E。

### Medium 1 — 隐藏/禁用状态在拖拽结束后可被陈旧意图恢复播放

**问题**

`beginScrub` 保存 `resumeAfterScrubRef=true`，随后暂停。页面 hidden、`playbackDisabled` 或 frame identity 变化只清除外层播放状态；除 timeline identity 外，它们没有清除 scrub 的恢复意图。稍后 `endScrub` 在页面重新可见或能力重新开启后，会依据旧 ref 自动恢复播放，无需新的用户 play 命令。

**证据**

- `frontend/src/features/simulation/hooks/useTimelinePlayer.ts:338-347`：保存原播放状态。
- `frontend/src/features/simulation/hooks/useTimelinePlayer.ts:297-305`：hidden 仅 `setPlaying(false)`，不清 `resumeAfterScrubRef`。
- `frontend/src/features/simulation/hooks/useTimelinePlayer.ts:215-220`：playback disabled 仅暂停，不清 scrub 恢复意图。
- `frontend/src/features/simulation/hooks/useTimelinePlayer.ts:361-376`：`endScrub` 依据当前 hidden/disabled 条件和陈旧 ref 决定恢复。
- 定向诊断：
  - hidden → visible → `endScrub(4)` 输出 `{"frameIndex":4,"playing":true}`；
  - disabled → enabled → `endScrub(4)` 输出 `{"frameIndex":4,"playing":true}`。
- 冻结契约：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1946-1948` 要求 hidden 返回后用户恢复，且 A/B 不兼容禁用同步播放。
- 现有测试 `frontend/src/features/simulation/test/timelinePlayer.test.ts:275-309` 只覆盖普通 begin/preview/end，没有状态在 scrub 中变化的组合。

**影响**

后台切回、比较结果短暂变为不兼容再恢复、或页面在拖拽期间隐藏时，播放器可能在用户未再次点击 Play 的情况下自动继续，造成意外网络预取和回放。

**修复建议**

建立单一 interruption/cancellation 路径：hidden、playbackDisabled 变为 true、frameCount 归零或 timeline identity 改变时，原子地结束/取消 scrub 并清除恢复意图。补 hidden/disabled/timelineKey 发生在 beginScrub 与 endScrub 之间的测试。

### Low 1 — 时间标签缺少输入一致性保护与失败测试

**问题**

`TimelineControl` 接收独立的 `frameCount` 和 `frameTimes`，但不验证 `frameTimes.length === frameCount`、索引连续性或 ISO 时间有效性。索引缺失时静默显示硬编码 `—`，而 `dateTime` 变为空；格式函数或 frameTimes 异常没有 fail-closed 状态。

**证据**

- `frontend/src/features/simulation/timeline/TimelineControl.tsx:33-44,90-95`：独立输入，缺时间时回退为 `—`。
- `frontend/src/features/simulation/timeline/TimelineControl.tsx:190-197,223-240`：缺失时间仍渲染 time/aria-valuetext。
- 定向诊断：`frameCount=3`、`frameTimes=['only-one']`，按 End 后输出 `MISMATCH {"text":"—","dateTime":null,"valueText":"—, 2/3"}`。
- `frontend/src/features/simulation/test/timelineControl.test.tsx:29-108` 仅测试长度匹配的正常数据和单档图例，没有 malformed/empty manifest。

**影响**

上游 manifest 不完整或错配时，用户得到没有可访问日期的“有效”时间轴，错误不易定位；默认 `—` 也绕过 i18n label 体系。

**修复建议**

在 integration/model 边界从 manifest 一次性派生并验证 `{frameCount, frameTimes}`，确保索引 0..N-1、时间严格递增且为有效 UTC RFC 3339；组件对不一致输入禁用播放并显示本地化数据无效状态，而不是静默占位。

## 3. 已确认的优点

- `useTimelinePlayer.ts:415-457` 使用 RAF + accumulator，没有 `setInterval`，并在 gap >250 ms 时丢弃迟到时间。
- `useTimelinePlayer.ts:399-405` 末帧再次 play 从 0 开始，默认不 loop；`step`/tick 到末帧停止。
- `useTimelinePlayer.ts:318-359` seek 重置 epoch，拖拽 preview 100 ms 节流并有 final commit。
- `useTimelinePlayer.ts:135-181` 相邻预取最多 2，resolve 后补泵、seek/key/disabled/unmount 会 abort 旧请求。
- `TimelineControl.tsx:139-172` 提供 Space、左右键、Shift±10、Home/End 键盘路径。
- `timeline.css:28-32,366-371` 有 focus-visible 与 `prefers-reduced-motion`；未使用 `transition: all`。
- `DepthLegend.tsx:31-47` 文本包含范围和单位，不仅依赖色块。

## 4. 本次实际执行的检查

### 4.1 指定测试（通过）

```text
$ npm test -- src/features/simulation/test/timelinePlayer.test.ts src/features/simulation/test/timelineControl.test.tsx
Test Files  2 passed (2)
Tests       19 passed (19)
Duration    2.11s
```

输出有 Testing Library/React 的 `act` deprecated warning，不影响本次断言通过。

### 4.2 轻量 FrameCache 回归检查（通过）

```text
$ npm test -- src/features/simulation/test/mapCore.test.ts
Test Files  1 passed (1)
Tests       8 passed (8)
```

该检查只证明现有 map/frame-cache 核心测试仍通过，不等于 timeline 已接线。

### 4.3 定向 TypeScript 检查（通过）

```text
$ node_modules/.bin/tsc --noEmit --target ES2020 --useDefineForClassFields true --lib ES2020,DOM,DOM.Iterable --module ESNext --moduleResolution bundler --allowImportingTsExtensions --resolveJsonModule --isolatedModules --jsx react-jsx --skipLibCheck --strict --types vite/client,vitest/globals,node src/vite-env.d.ts src/test/setup.ts src/features/simulation/hooks/useTimelinePlayer.ts src/features/simulation/timeline/TimelineControl.tsx src/features/simulation/timeline/DepthLegend.tsx src/features/simulation/timeline/index.ts src/features/simulation/test/timelinePlayer.test.ts src/features/simulation/test/timelineControl.test.tsx
(exit 0, no output)
```

### 4.4 定向 ESLint（通过）

```text
$ node_modules/.bin/eslint --config eslint.config.js src/features/simulation/timeline/DepthLegend.tsx src/features/simulation/timeline/TimelineControl.tsx src/features/simulation/timeline/index.ts src/features/simulation/hooks/useTimelinePlayer.ts src/features/simulation/test/timelinePlayer.test.ts src/features/simulation/test/timelineControl.test.tsx --report-unused-disable-directives --max-warnings 0
(exit 0, no output)
```

### 4.5 定向 Prettier（通过）

```text
$ node_modules/.bin/prettier --check src/features/simulation/timeline/DepthLegend.tsx src/features/simulation/timeline/TimelineControl.tsx src/features/simulation/timeline/index.ts src/features/simulation/timeline/timeline.css src/features/simulation/hooks/useTimelinePlayer.ts src/features/simulation/test/timelinePlayer.test.ts src/features/simulation/test/timelineControl.test.tsx
All matched files use Prettier code style!
```

### 4.6 定向行为诊断

- 4x 精确边界：249 ms 不切换、250 ms 切到 frame 1，输出 `BOUNDARY_OK frameIndex=1`。
- 常驻 16 ms RAF 的纯 accumulator 模拟：4 s 内 1x/2x/4x 分别切换 4/8/16 次，符合 canonical 速率。
- 预取失败：连续 reject 后输出 `PREFETCH_RETRY_CALLS 8`，确认 High 1。
- StrictMode：生产入口 `frontend/src/main.tsx:46-53` 启用 StrictMode；诊断观察到首轮 2 个请求被 abort、第二轮 2 个请求保留。清理路径有效，但会产生开发态双启动；这不是本次阻断项。
- hidden/disabled scrub：分别输出 `VISIBLE_AFTER_SCRUB ... "playing":true` 与 `DISABLED_SCRUB ... "playing":true`，确认 Medium 1。
- frameTimes 长度不匹配：输出 `MISMATCH {"text":"—","dateTime":null,"valueText":"—, 2/3"}`，确认 Low 1。

所有临时诊断测试均在命令结束时删除；最终检查未留下 `timeline-review-*` 目录。

## 5. 未运行 / 不可由本模块单独证明

- 未重复全量前端 test/build/lint/format；任务明确要求不要重复最终全量门禁。
- 未运行 60 s 地图性能门禁，不能宣称 presented FPS ≥25、RAF p95 ≤50 ms、long task 或 256 MiB cache 通过。
- 未运行真实 `/simulation` E2E；当前仓库缺少该路由，无法执行 AC-3 场景。
- 未验证 production FrameCache/A-B/i18n/store 集成，因为生产代码中尚无接线点。

## 6. 复审条件

1. 修复预取失败策略并覆盖 terminal/retryable/Retry-After/retry ceiling。
2. 完成 `/simulation` 生产接线，current P0 与 P1 共用 FrameCache scheduler，timeline 状态同步到单一 store/i18n。
3. 修复 scrub 在 hidden/disabled/identity 切换后的陈旧恢复意图。
4. 补 manifest/frameTimes 不一致防护。
5. 重新运行本模块两个指定测试，并提供真实 AC-3 route E2E；性能门禁仍由 frontend acceptance 独立出具。
