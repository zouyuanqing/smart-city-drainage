# 管网与积水面图层实施摘要

## 1. 范围与契约依据

本摘要只覆盖本任务拥有的地图/worker/map 测试路径；没有修改 legacy `MapVisualization`、全局 Zustand、API client、后端或并行模块的共享文件。

- 源 PRD 要求 Leaflet 管网/节点/汇水区与充满度色带、淹没面时间轴，以及万级管段/千级多边形的 Canvas/抽稀/视口裁剪能力：`docs/hydraulic-twin/SOURCE_PRD.md:16-30`。
- 冻结补充基线把前端门槛明确为 5000 nodes、10000 pipes、1000 catchments、60 秒 pan/zoom + 4x 播放、FPS ≥25、FrameCache ≤256 MiB：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:435-451`。
- 实施契约要求一个命令式 custom Canvas `L.Layer`、服务端 bbox/zoom 简化、客户端 viewport index/culling/LOD/hit test；FrameCache key 为 `v1/{simulation_id}/{frame_index}/{fields_hash}/{network_revision_id}`，数值使用 typed arrays，最多两个 frame request 并发，卸载时清理：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1913-1938`。
- 文件所有权契约将 `frontend/src/features/simulation/map/**` 与 `frontend/src/features/simulation/frame/**` 分给 simulation-map；本任务只写 map/workers/map 测试：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2320-2334`。
- 现有地图仍是逐设备 `Marker` DOM 列表，不能直接承载万级水力几何：`frontend/src/components/map/MapVisualization.tsx:274-295`。
- 实施契约当前仍记录 R0 设计复审未批准、因此 G0～G6/AC 尚不能宣称通过：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2430-2436`。本次只完成 owned implementation 与单测，不伪造 gate/性能通过证据。

## 2. 实际改动

### 2.1 命令式 Leaflet Canvas 层

- 新增 `HydraulicCanvasLayer`，继承一个 `L.Layer`，在 overlay pane 中只创建一个 canvas，不为每个要素创建 Marker：`frontend/src/features/simulation/map/canvasLayer.ts:212-264`、`448-483`。
- 支持 `nodes`、`pipes`、`catchments`、`ponding` 四层绘制；`renderNow()` 先按 bbox 查询，再按 LOD/预算抽稀，最后批量画图并产出 `RenderStats`：`frontend/src/features/simulation/map/canvasLayer.ts:382-433`。
- 提供命令式 `setData`、`setFrame`、`setLayerVisibility`、`toggleLayer`、`setColorMetric`、`setSelectedFeatures`、`setFrameCacheKey`：`frontend/src/features/simulation/map/canvasLayer.ts:279-380`。
- 只有一个可复用 tooltip DOM 节点；mousemove/click 使用屏幕空间几何 hit test，按节点 > 管段 > 积水 > 汇水区优先级处理重叠要素：`frontend/src/features/simulation/map/canvasLayer.ts:514-539`、`654-704`。
- `onRemove()` 取消 RAF、移除 Leaflet/canvas 事件监听、移除 canvas/tooltip、清空可见集合，并按选项清理 FrameCache；`destroy()` 进一步释放数据、索引和选择状态：`frontend/src/features/simulation/map/canvasLayer.ts:486-512`。
- 新增 `SimulationMapController`，作为命令式 owner 保证多次 data/frame/图层命令复用同一个 layer，并在 `dispose()` 时释放 layer/cache：`frontend/src/features/simulation/map/layerController.ts:17-110`。

### 2.2 bbox、空间索引与抽稀

- bbox 解析、finite/range 校验、hash、包含/相交/扩展和几何 bounds 计算：`frontend/src/features/simulation/map/viewport.ts:25-203`。
- `ViewportIndex` 使用固定经纬度网格，支持点退化 bbox、超大几何 oversized 候选、稳定排序和 clear：`frontend/src/features/simulation/map/viewport.ts:227-303`。
- 冻结 zoom LOD：`<11` 隐藏节点、`11-13` 隐藏节点、`14-15` 节点/选中标签、`>=16` critical 标签，并设置每层预算：`frontend/src/features/simulation/map/viewport.ts:305-369`。
- `decimateFeatures()` 保留 critical 要素，返回原对象和原 `feature_index`，不会重新编号：`frontend/src/features/simulation/map/viewport.ts:371-413`。
- API FeatureCollection/数组统一规范化，计算 bounds 并保留 manifest/feature index；可选择 strict 模式拒绝坏几何：`frontend/src/features/simulation/map/mapData.ts:50-145`。
- typed geometry、layer、frame、interaction、render stats 契约集中在 `frontend/src/features/simulation/map/types.ts:40-194`，并由 `frontend/src/features/simulation/map/index.ts:1-97` 对外导出。

### 2.3 色带、tooltip 与非颜色语义

- 充满度、流速、积水深度三套固定色带，包含无数据中性色、虚线/符号等非颜色 cue：`frontend/src/features/simulation/map/colorScales.ts:21-126`。
- frame 值严格按请求 metric 和 immutable `feature_index` 关联，不会用 depth 冒充 fill ratio；缺失/负值/非 finite 返回中性样式：`frontend/src/features/simulation/map/colorScales.ts:152-205`。
- `getFeatureStyle()` 同时输出颜色、线宽、虚线、symbol、critical、predicted/observed phase、label 和 accessible label：`frontend/src/features/simulation/map/colorScales.ts:222-268`。
- Canvas tooltip 使用 `textContent` 写入图层、指标和 feature id，避免把属性拼成未转义 HTML：`frontend/src/features/simulation/map/canvasLayer.ts:615-624`、`727-752`。

### 2.4 帧缓存、并发与 worker

- FrameCache 默认 256 MiB，按 byte 数计算 LRU；feature index/values/flags 转为 `Uint32Array`/`Float32Array`/`Uint8Array`，支持 pin/unpin、删除、clear 和失败回滚：`frontend/src/features/simulation/map/frameCache.ts:9-18`、`220-325`。
- key 与契约一致，并校验 frame index、时间、字段计数、feature index、finite/non-negative 值；无效 frame 不会被绘制：`frontend/src/features/simulation/map/frameCache.ts:48-116`、`187-214`。
- `FrameRequestScheduler` 默认最多 2 个并发请求，current/next/previous 有稳定优先级，支持 AbortSignal 和显式 dispose：`frontend/src/features/simulation/map/frameCache.ts:328-441`。
- 新增 typed-array frame decode worker protocol、transferable buffers 和 worker 入口：`frontend/src/features/simulation/workers/frameDecodeProtocol.ts:4-89`、`frontend/src/features/simulation/workers/frameDecode.worker.ts:15-26`。
- `FrameDecoder` 优先使用 module worker，worker 不可用时回退到同一 typed encode 逻辑，并在 dispose 时终止 worker/拒绝 pending：`frontend/src/features/simulation/map/frameDecoder.ts:19-112`。

## 3. 单测与真实检查

### 3.1 Owned 测试（通过）

命令：

```text
cd frontend && npx vitest run src/features/simulation/test/mapCore.test.ts src/features/simulation/test/mapCanvasLayer.test.ts src/features/simulation/test/mapFixture.test.ts src/features/simulation/test/mapWorker.test.ts --reporter=dot
```

实际结果：

```text
Test Files  4 passed (4)
Tests       17 passed (17)
```

覆盖内容：

- bbox 解析/非法范围、10k 点空间索引、critical 保留、稳定 feature index、zoom LOD：`frontend/src/features/simulation/test/mapCore.test.ts:106-155`。
- 充满度/流速/深度/无数据样式、typed-array FrameCache、pin/LRU、并发优先级：`frontend/src/features/simulation/test/mapCore.test.ts:157-228`。
- 5000 nodes + 10000 pipes + 1000 catchments fixture 的 normalize/index/bbox 验证：`frontend/src/features/simulation/test/mapFixture.test.ts:4-69`。
- layer 复用、10k pipe 抽稀、tooltip/hit-test、卸载清理、无效 frame 保留上一帧、FeatureCollection index 保留：`frontend/src/features/simulation/test/mapCanvasLayer.test.ts:170-253`。
- worker protocol、主线程 fallback、dispose：`frontend/src/features/simulation/test/mapWorker.test.ts:1-45`。

### 3.2 Owned lint/format/type 检查（通过）

命令及结果：

```text
cd frontend && npx eslint "src/features/simulation/map/**/*.ts" "src/features/simulation/workers/**/*.ts" "src/features/simulation/test/map*.test.ts" --report-unused-disable-directives --max-warnings 0
# exit 0, no output

cd frontend && npx prettier --check "src/features/simulation/map/**/*.ts" "src/features/simulation/workers/**/*.ts" "src/features/simulation/test/map*.test.ts"
# Checking formatting...
# All matched files use Prettier code style!

cd frontend && npx tsc --noEmit --target ES2020 --module ESNext --moduleResolution bundler --lib ES2020,DOM,DOM.Iterable --strict --skipLibCheck <owned map/worker/test files> --pretty false
# exit 0, no output
```

### 3.3 Bundle 检查（通过，但不是 G5 性能证据）

```text
cd frontend && npx vite build
# ✓ 4042 modules transformed.
# ✓ built in 11.95s
# 仅有既有 >1000 kB chunk warning
```

该命令证明 Vite 能打包 worker/import graph；没有把它当作 25 FPS、FrameCache 256 MiB 或 60 秒浏览器性能门禁。

### 3.4 全套前端检查（未通过，非 owned 路径）

```text
cd frontend && npm test -- --reporter=dot
# exit 1
# Test Files 1 failed, 16 passed (17)
# Tests 2 failed, 81 passed (83)
# 失败：frontend/src/test/__tests__/LoginPage.test.tsx:24
# TypeError: localStorage.clear is not a function
```

```text
cd frontend && npm run lint
# exit 1, 45 problems
# 主要位于未 owned 的 frontend/e2e/run-ac-smoke.mjs、frontend/performance/run-twin-map.mjs
# 以及 frontend/src/features/simulation/api/client.ts 的并行模块告警
```

```text
cd frontend && npm run build
# exit 1 in tsc -b
# 未进入 Vite；错误位于并行 simulation model/api/store/test fixtures，例如：
# frontend/src/features/simulation/model/errors.ts:180
# frontend/src/features/simulation/model/normalizers.ts:109
# frontend/src/features/simulation/api/client.ts:108
# frontend/src/features/simulation/test/fixtures/index.ts:2-12
```

这些失败没有通过越界修改 owned 之外的路径来“修复”；需要由对应 owner/integration 处理。

## 4. 风险与未验证项

1. **R0/gate 状态**：`IMPLEMENTATION_CONTRACT.md:2430-2436` 仍为 R0 未批准；本次没有运行 G0/G5，也没有把 17 个单测写成 AC/G5 PASS。
2. **未接入现有页面**：由于 ownedPaths 不含 `MapVisualization.tsx`、Simulation 页面或全局 store，本次交付是可被 integration owner 调用的命令式 API；现有页面仍不会自动显示该 Canvas 层。
3. **未做真实浏览器性能测量**：没有 Playwright/真实 Chromium 的 60 秒 pan/zoom+4x、presented FPS、RAF P95、long task 或 cache bytes artifact；Vite bundle 通过不等于 25 FPS。
4. **worker 浏览器路径未在 Vitest 实际启动**：测试覆盖 protocol 与主线程 fallback，Vite 能解析 worker URL；真实浏览器 worker/transfer 行为仍需 E2E。
5. **空间数据质量**：normalize 默认跳过坏 feature（strict 可改为 fail-closed）；API 的 `complete/truncated_layers` 仍应由后端保证，前端没有静默把截断数据当全量。
6. **全仓 lint/build/test 回归**：全套命令当前被上述并行 owner 文件和既有 `localStorage` 测试环境问题阻断，owned 子集检查通过。

## 5. 集成建议（不改本任务 ownedPaths）

由 frontend-integration 调用：

```ts
const controller = createSimulationMapController({ colorMetric: "fill_ratio" });
controller.addTo(map);
controller.setData(mapData);
controller.setFrame(
  frame,
  frameCacheKey(simulationId, frameIndex, fields, revisionId),
);
controller.setLayerVisibility({
  catchments: true,
  nodes: true,
  pipes: true,
  ponding: true,
});
// 页面卸载/网络切换
controller.dispose();
```

集成时保持 `network_revision_id + target_manifest_id + target_manifest_sha256` 与 frame key 一致；不要把大型几何或帧数组复制进 legacy Zustand store。

## 6. 提交状态

- 未提交、未推送。
- 本任务新增/修改仅位于：
  - `frontend/src/features/simulation/map/**`
  - `frontend/src/features/simulation/workers/**`
  - `frontend/src/features/simulation/test/map*.test.*`
  - 本摘要文件 `docs/hydraulic-twin/implementation/simulation-map.md`（用户明确要求的交付路径）。
