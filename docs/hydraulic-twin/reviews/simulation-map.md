# 管网与积水面图层审查（simulation-map）

## 结论

**NOT_APPROVED / `approved=false`**

本次复核发现 7 个高严重度问题：真实 API 的地图/帧类型无法直接送入命令层、默认 worker 路径失效、连续视口没有实时刷新、revision 切换会保留旧帧、帧索引/checksum 校验不足、控制器绕过 Leaflet 生命周期、typed-array/worker 缓存路径没有接入实际绘制。它们会阻断真实集成或使万级要素的内存/帧率目标无法成立，因此不能批准。

审查范围仅为 `simulation-map` 的 map/worker/map 测试路径；没有修改实现代码。

## 契约基线

- 用户规格要求：Leaflet Canvas 节点/管段/汇水区、充满度/流速色带、tooltip/图层开关、视口 bbox/抽稀/帧缓存/资源清理，并支撑 5,000 节点、10,000 管段、1,000 汇水区。
- 冻结地图/帧契约：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1913-1938`，尤其是单 Canvas、视口 culling、LOD、typed arrays、256 MiB 预算和完整帧校验。
- 冻结性能口径：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:436-452`，要求 60 秒 pan/zoom + 4x 播放、25 FPS、FrameCache ≤256 MiB；`map-data` 的截断不能冒充全量（`IMPLEMENTATION_CONTRACT.md:1270-1277`）。

## 本次实际检查

1. **四个指定测试文件（通过）**

   ```text
   cd smart-city-drainage/frontend && npx vitest run src/features/simulation/test/mapCore.test.ts src/features/simulation/test/mapCanvasLayer.test.ts src/features/simulation/test/mapFixture.test.ts src/features/simulation/test/mapWorker.test.ts --reporter=dot
   ```

   实际结果：`Test Files 4 passed (4)`、`Tests 17 passed (17)`，耗时 `1.14s`。这证明现有单测通过，不证明真实 API 兼容、真实 worker、浏览器 25 FPS 或资源预算通过。

2. **目标文件类型检查（通过但未覆盖调用方）**

   ```text
   npx tsc --noEmit --target ES2020 --module ESNext --moduleResolution bundler --lib ES2020,DOM,DOM.Iterable --strict --skipLibCheck src/features/simulation/map/index.ts src/features/simulation/api/client.ts --pretty false
   ```

   实际结果：exit 0、无输出。该命令没有把 API client 返回值实际传给 `SimulationMapController`，所以没有覆盖下述跨模块类型错误。

3. **真实 API 类型与 controller 的定向类型探针（失败）**

   使用 TypeScript compiler API 在内存中建立 `SimulationMapController().setData(apiData)` 和 `setFrame(frame)` 调用，没有写入仓库文件。实际输出：

   ```text
   Argument of type 'NetworkMapData' is not assignable to parameter of type 'NetworkMapData | NormalizedMapData'.
     Types of property 'bbox' are incompatible.
       Type '[number, number, number, number]' is not assignable to type 'BBox'.
   Argument of type 'SimulationFrame' is not assignable to parameter of type 'SimulationFrame'.
     Property 'time' is missing in type 'SimulationFrame' but required in type 'SimulationFrame'.
   ```

4. **worker URL 解析检查（失败）**

   ```text
   node -e "const fs=require('fs'); const p=new URL('../../workers/frameDecode.worker.ts','file:///C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/map/frameDecoder.ts'); console.log(p.pathname); console.log(fs.existsSync(new URL('../../workers/frameDecode.worker.ts', 'file:///C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/frontend/src/features/simulation/map/frameDecoder.ts')));"
   ```

   实际输出指向 `.../src/features/workers/frameDecode.worker.ts`，第二行输出 `false`；实际 worker 位于 `src/features/simulation/workers/frameDecode.worker.ts`。

5. **默认世界 bbox 的 cell 规模诊断**

   ```text
   node -e "const cell=0.05; const w=Math.floor(180/cell)-Math.floor(-180/cell)+1; const h=Math.floor(90/cell)-Math.floor(-90/cell)+1; console.log({worldCells:w*h,width:w,height:h});"
   ```

   实际输出：`{worldCells: 25930801, width: 7201, height: 3601}`。

6. **测试覆盖搜索**

   ```text
   rg -n "FrameCacheClient|frame_time|surcharged|flow_m3s|move\\b|zoom\\b|keydown|tabIndex|major|truncated_layers|complete" src/features/simulation/test/map*.test.ts || true
   ```

   实际只命中 `mapCanvasLayer.test.ts:201,226` 的 `onRemove`、`mapCore.test.ts:64,150` 的普通字段；没有 `FrameCacheClient` 并发去重、canonical wire fixture、move/zoom、键盘、major pipe 或 truncated-layer 测试。

未运行全仓测试、production build、真实 Chromium/Worker、60 秒性能脚本或 API latency gate；这些不是本模块审查中可由单测替代的证据。

## Findings

### H-01（高）— 地图/帧的本地类型与真实 API 类型不兼容，真实响应无法进入命令层

- **文件**：`frontend/src/features/simulation/model/types.ts:250-267,306-320`；`frontend/src/features/simulation/test/fixtures/network-map-v1.json:7-20`；`frontend/src/features/simulation/test/fixtures/simulation-frame-v1.json:9-23`；`frontend/src/features/simulation/map/types.ts:81-97,148-165`；`frontend/src/features/simulation/map/frameCache.ts:92-115`；`frontend/src/features/simulation/map/frameLookup.ts:32-70`。
- **证据**：共享 API client 返回的 `NetworkMapData.bbox` 是 `[west,south,east,north]`（model type），共享 `SimulationFrame` 使用 `frame_time`、节点 `surcharged`、管段 `flow_m3s`；map 自己重新定义成对象 `BBox`、`time`、`surcharge`、`velocity_mps`。仓库真实 fixture 也使用 tuple/`frame_time`/`surcharged`/`flow_m3s`，而四个 map 测试全部改用自定义的 `time`/`velocity_mps` 形状。`normalizeMapData` 直接对 `bbox` 调用 `isValidBBox`，tuple 没有 `.west/.south/.east/.north`，会 fail；`validateFrame` 要求 `frame.time`，因此 canonical `frame_time` payload 也会 fail。即使调用方用类型断言绕过，`validateLayer` 的白名单没有 `flow_m3s`/`surcharged`，这些值会被静默忽略，无法完成流速/超载样式映射。
- **影响**：真实 `getMapData`/`getFrame` 结果在编译期或运行期不能交给 `SimulationMapController`；这是集成阻断，不是测试 fixture 差异。帧的 revision/manifest、单位和 checksum 也没有在同一类型边界上被验证。
- **建议修复**：以共享 model/transport 类型为唯一输入，在 map 边界提供显式、可测试的 adapter（显式处理 tuple bbox、`frame_time`、`surcharged`、`flow_m3s` 与单位/manifest/checksum），或统一所有层的字段契约；增加直接喂入两个真实 JSON fixture 的测试。
- **缺测**：当前 `mapCore.test.ts:70-85` 与 `mapWorker.test.ts:5-20` 使用非 canonical 字段，未覆盖该阻断。

### H-02（高）— `FrameDecoder` 的默认 worker URL 指向不存在目录

- **文件**：`frontend/src/features/simulation/map/frameDecoder.ts:57-69,81-90`；实际 worker 为 `frontend/src/features/simulation/workers/frameDecode.worker.ts:1-29`。
- **证据**：`new Worker(new URL('../../workers/frameDecode.worker.ts', import.meta.url), { type: 'module' })` 从 `simulation/map/` 解析到 `simulation/../workers`，即 `src/features/workers/frameDecode.worker.ts`；本次 URL 解析诊断输出该路径且 `fs.existsSync` 为 `false`。只有 `postMessage` 同步抛错时才走主线程 fallback（`frameDecoder.ts:60-69`）；worker 异步加载失败会走 `error` handler（`:101-107`）并 reject pending，不会 fallback。
- **影响**：默认 `useWorker=true` 的真实浏览器路径无法加载已交付的 worker；模块的 worker/transferable deliverable 实际不可用。`mapWorker.test.ts:35-39` 强制 `useWorker:false`，所以测试全绿不能捕获它。
- **建议修复**：修正为从 `simulation/map/` 到 `simulation/workers/` 的正确相对路径，并增加 production build + 真实 module Worker 的启动/消息/transfer 测试；同步启动失败和异步 worker error 都要有明确的 fallback 语义。

### H-03（高）— 只监听 `moveend/zoomend`，连续 pan/zoom 期间不重新 bbox/cull/LOD

- **文件**：`frontend/src/features/simulation/map/canvasLayer.ts:435-445,475-479`。
- **证据**：`requestRender` 确实使用 rAF，但唯一注册的地图事件是 `moveend`、`zoomend`、`resize`、`viewreset`，没有 `move` 或 `zoom`。因此手势进行中不会执行 `renderNow()`、`ViewportIndex.query()` 或 LOD 重新计算；只有手势结束后才刷新。冻结契约要求客户端 viewport culling，且性能验收要求连续 60 秒 pan/zoom。
- **影响**：新进入视口的要素在整个拖拽/缩放期间不会出现，LOD/bbox 统计也不是连续更新；当前 10k 测试只检查 `moveend` listener（`mapCanvasLayer.test.ts:186-204`），无法覆盖真实交互和 25 FPS 目标。
- **建议修复**：订阅 Leaflet 的连续 move/zoom 事件，以 rAF 合并更新；保持 pane transform 与屏幕坐标转换一致，并为新进入视口的要素增加真实事件测试。

### H-04（高）— `setData` 切换 revision/manifest 时保留旧 frame，可能把旧拓扑值画到新几何上

- **文件**：`frontend/src/features/simulation/map/canvasLayer.ts:318-324,343-364`；`frontend/src/features/simulation/map/frameCache.ts:207-214`。
- **证据**：`setData` 只替换 `data`、重建 index、请求 render；没有清除 `frame`、`frameLookup` 或 `frameKey`，也没有比较 `networkRevisionId`/`targetManifestId`。`setFrame` 接受调用方任意 `cacheKey`，本地 `SimulationFrame` 甚至没有 manifest/revision 字段来阻止错配。
- **影响**：网络/仿真切换期间，旧的合法 frame 会继续被 `FrameLookup` 和 `drawFeature` 使用，直到新 frame 到达；如果新旧 feature index 恰好存在，就会显示错误的水力状态。A/B/缓存契约要求 frame 与 immutable manifest/revision 对齐，这违反了该安全边界。
- **建议修复**：在 `setData` 检测 network/revision/manifest 变化时立即隔离/清除旧 frame，并要求 frame/cache key 携带并校验 manifest/revision；增加 revision 切换测试，断言旧值不绘制。

### H-05（高）— 帧校验没有验证升序/重复 feature index，也没有 checksum

- **文件**：`frontend/src/features/simulation/map/frameCache.ts:48-75,92-116`；`frontend/src/features/simulation/map/canvasLayer.ts:343-350`。
- **证据**：`validateSeries` 只检查长度为整数且非负、长度相等、数值 finite/non-negative；没有检查 `indexes[i] > indexes[i-1]`、重复或 manifest 归属。`validateFrame` 也只检查非空 `time`，没有验证 checksum。`setFrame` 只要 `validation.valid` 就直接绘制。
- **影响**：乱序/重复 index 会在 `FrameLookup` 的 `Map.set` 中覆盖前一行，稀疏 LOD 或错误 payload 可能把一个要素的值显示到另一个要素；checksum 错误/过期 payload 也会被接受。冻结契约明确要求 `feature_index` 升序、长度/count/hash 完整验证（`IMPLEMENTATION_CONTRACT.md:1673`、`:1937`）。
- **建议修复**：在进入 cache/绘制前验证严格升序（并验证 manifest/count/checksum），失败时保持上一有效 frame 并报告 decode error；为乱序、重复、缺 checksum、错误 checksum 加负例测试。

### H-06（高）— 控制器直接调用 `onRemove`，破坏 Leaflet layer registry，remove 后不能可靠复用

- **文件**：`frontend/src/features/simulation/map/layerController.ts:44-51,88-99`；Leaflet 实际实现 `frontend/node_modules/leaflet/src/layer/Layer.js:154-198`。
- **证据**：controller 在切换 map 和 `remove()` 时直接执行 `this.layer.onRemove(this.mountedMap)`，没有调用 `map.removeLayer(this.layer)`。Leaflet 的 `addLayer` 在 `Layer.js:157-173` 发现同一 layer id 已注册会直接 return；`removeLayer` 才会在 `:177-198` 调用 `onRemove` 并删除 `_layers` registry。
- **影响**：`controller.remove()` 后 map 仍认为 layer 已注册，随后 `addTo(map)` 可能成为 no-op，canvas 不会重新创建；切换 map 时也会留下旧 map 的 registry/生命周期状态。现有测试直接调用 `layer.onRemove`，没有覆盖 controller + 真实 Leaflet map 的 remove/re-add。
- **建议修复**：controller 只通过 `map.removeLayer(layer)`/`layer.removeFrom(map)` 操作；为同一 map remove→add、不同 map 切换、dispose 后 registry 清理增加真实 Leaflet 生命周期测试。

### H-07（高）— worker 产出的 `CachedFrame` 没有接入 Canvas 绘制，typed-array/256 MiB 路径实际未使用

- **文件**：`frontend/src/features/simulation/map/canvasLayer.ts:343-364`；`frontend/src/features/simulation/map/frameLookup.ts:25-70`；`frontend/src/features/simulation/map/frameCache.ts:156-184,220-235`；`frontend/src/features/simulation/map/frameDecoder.ts:24-112`。
- **证据**：Canvas 的公开 `setFrame` 只接受 `SimulationFrame`，随后保留原始对象并构造多个 JS `Map`；它不能接受 worker 返回的 `CachedFrame`。`CachedFrame` 只在 frame cache/decoder 内出现，`SimulationMapController` 没有 decoder/client 或 typed lookup 适配。`FrameCache.sizeBytes` 只统计编码后的动态数组，map geometry 仍由 `HydraulicCanvasLayer.data` 单独常驻，没有共享预算记账。
- **影响**：真实播放路径同时保留 raw arrays、typed arrays 和 JS lookup maps，无法兑现 worker 解码后释放 raw buffer、typed-array 查找和静态几何+动态帧共享 256 MiB 的要求；10k/289 帧时会造成额外分配和主线程工作。worker 测试只测协议/强制 fallback，未测 `CachedFrame → Canvas`。
- **建议修复**：定义并实现 `CachedFrame` 的直接查找/绘制接口，把 decoder、cache client、controller 串成单一路径；释放 raw arrays，按静态几何和所有动态帧统一 byte accounting，并测试 cache bytes、worker transfer 和 frame 切换。

## Medium findings

### M-01（中）— `<11` zoom 没有实现 major pipes only / catchment aggregate

- **文件**：`frontend/src/features/simulation/map/viewport.ts:304-367`；`frontend/src/features/simulation/map/canvasLayer.ts:394-419,658-693`。
- **证据**：`LodProfile` 只有 `showPipes/showCatchments/maxFeaturesPerLayer`，没有 major-only 或 aggregate 模式；低 zoom 仍把所有 pipes 放入同一候选集，decimation 只 preserve `critical`，不检查 `properties.major`。所有 catchment polygon 都走逐面填充。
- **影响**：对完整 10k fixture，低 zoom 会显示均匀抽样而非 major pipes，并逐个填充 catchment；不符合冻结 LOD 表，可能隐藏关键主干/误导拓扑。
- **建议修复**：增加明确的 major predicate 或服务端已过滤标记，以及 catchment outline/aggregate 路径；增加低 zoom critical/major/aggregate 测试。

### M-02（中）— mousemove hit-test 没有节流，每个事件扫描全部可见要素

- **文件**：`frontend/src/features/simulation/map/canvasLayer.ts:464-472,771-808`。
- **证据**：canvas 直接注册 `mousemove`，`handleMouseMove` 每次事件遍历 `HIT_LAYERS` 的全部 `lastVisible` 要素并反复做 `latLngToContainerPoint`/几何距离计算；没有时间戳、30 Hz 节流或 screen-space grid。
- **影响**：高 zoom 下 10k pipes/polygons 与浏览器高频 mousemove 会竞争主线程，和 25 FPS/交互性能目标冲突；tooltip DOM 也在每次命中时重建。
- **建议修复**：按冻结交互预算节流 hover，click 保持即时；缓存屏幕几何或使用屏幕空间 grid，避免每个事件重投影全部要素，并补充 10k hover 诊断。

### M-03（中）— 同 key 并发请求没有 Promise 去重，clear/dispose 不会阻止 active request 回填 cache

- **文件**：`frontend/src/features/simulation/map/frameCache.ts:350-440,442-485`。
- **证据**：`FrameCacheClient.getOrLoad` 每次都调用 `scheduler.request`，scheduler 没有按 key 保存 in-flight Promise；两个同 key 请求可同时进入 loader（`:455-470`）。`scheduler.dispose()` 只 pop/cancel queue 中的请求（`:429-432`），不 abort active loader；active loader 完成后仍执行 `cache.put`（`:465-469`），所以 `FrameCacheClient.clear()` 后仍可能回填已清理的旧数据。
- **影响**：同一帧可能重复请求，放大 API/rate-limit 压力；logout、network switch 或显式清理不能保证旧请求不发布/不占内存，且永不结束的 active loader 会留下 pending。
- **建议修复**：用 key→共享 Promise/in-flight controller 做去重；clear/dispose 时 abort active requests、使用 generation token 拒绝过期 put，并测试并发同 key与清理中的 resolve/reject。

### M-04（中）— map-data normalizer 对坏要素/缺失 complete 默认 fail-open，且丢弃 returned counts

- **文件**：`frontend/src/features/simulation/map/mapData.ts:50-83,105-125`；`frontend/src/features/simulation/map/types.ts:81-118`。
- **证据**：`normalizeFeature` 默认 `strict=false`，坏 geometry 直接返回 `null`；`complete` 缺失时默认 `true`，没有根据坏要素或 `truncated_layers` 调整。local `NetworkMapData`/`NormalizedMapData` 没有 `returned_counts`，normalize 也不保留 `zoom`/source/returned counts。
- **影响**：部分或损坏响应可能被当作完整快照显示，且调用方无法从 normalized result 解释“源数量/返回数量”；与 `IMPLEMENTATION_CONTRACT.md:1275-1276` 的不得冒充全量相冲突。
- **建议修复**：canonical map-data 默认 fail-closed（或明确返回 partial state），严格校验 feature/index/id/geometry，保留并展示 source/returned counts、complete/truncated 原因；增加 malformed、partial、truncated 负例。

### M-05（中）— `surcharge` 标志虽被解码/查找，但样式映射完全忽略它

- **文件**：`frontend/src/features/simulation/map/frameLookup.ts:40-56,73-79`；`frontend/src/features/simulation/map/colorScales.ts:196-205,221-255`。
- **证据**：`FrameLookup` 读取 `pipes.surcharge`，但 `metricValueForFeature` 只按请求的 `fill_ratio`/`velocity_mps`/`depth_m` 取值；`getFeatureStyle` 从未查询 surcharge boolean。一个 `fill_ratio=0.8, surcharge=true` 的 pipe 会走 `high` band，而不是 `surcharge` cue。
- **影响**：超载/满流状态在颜色、线型、tooltip 上可能仍显示为普通高充满度；这也使 H-01 中即使完成字段 adapter，样式语义仍不完整。
- **建议修复**：将 boolean surcharge 纳入 style mapping，并与 `>1` 的规则统一；增加 `surcharge=true` 且 ratio<1 的样式/tooltip 测试。

### M-06（中）— Canvas 没有键盘等价入口，phase/accessibility 文本也未形成可见或可读语义

- **文件**：`frontend/src/features/simulation/map/canvasLayer.ts:456-472,697-717,810-817`；`frontend/src/features/simulation/map/colorScales.ts:21-126,237-255`；冻结契约 `IMPLEMENTATION_CONTRACT.md:1927-1928`。
- **证据**：canvas 只设置 `aria-label`，没有 `tabIndex`、键盘事件或可聚焦 feature list；交互入口只有 mousemove/click。`phase` 只影响 dash，tooltip/accessible label 没有“预计/已发生”文本；色带和 tooltip 文案为硬编码中文，map options 没有 translator/i18n key。
- **影响**：键盘/屏幕阅读器用户无法打开要素详情，predicted/observed 只能依赖视觉线型；英文环境无法正确本地化。
- **建议修复**：提供可聚焦的 feature list/键盘激活和 ARIA 关联，tooltip 显式显示 phase 与单位，所有 label/tooltip 通过 i18n key 注入；补 keyboard/locale 测试。

### M-07（中）— Polygon holes 被逐环填充并被视为可命中区域

- **文件**：`frontend/src/features/simulation/map/canvasLayer.ts:118-130,157-175,658-693`。
- **证据**：`drawPolygon` 对每个 ring 独立 `fill()`，没有按 GeoJSON outer/holes 规则构造 even-odd path；`pointInPolygon` 对每个 ring 单独判断，落在 hole 内也会返回命中。
- **影响**：带孔的汇水区/积水区会显示错误填充，点击 hole 可能打开错误的 feature，视觉面积和 tooltip 语义均不可靠。
- **建议修复**：用单一 path + even-odd fill/stroke，并在 hit-test 中按 ring parity 计算；增加带 hole 的 polygon fixture。

### M-08（中）— 视口查询没有 cell-range 上限，全球 bbox 会扫描约 2,593 万个 cell

- **文件**：`frontend/src/features/simulation/map/viewport.ts:18-19,279-294`。
- **证据**：默认 cell size 为 `0.05°`，query 直接双层循环 `cellRange` 覆盖的每个 cell；本次诊断得到世界范围 `25,930,801` cells。`MAX_CELLS_PER_FEATURE` 只限制写入时的 feature，不限制 query 范围。
- **影响**：地图缩小到世界/大范围时，单次 query 可能产生长任务和 CPU 峰值，削弱 culling 的性能保护；用户可控 bbox 也可放大该路径。
- **建议修复**：对 query cell 数设上限并切换自适应 cell size/全量候选策略，或在超大 bbox 直接走受控 coarse pass；增加世界 bbox benchmark。

### M-09（中）— 10k fixture 只测 normalize/index，不测真实 Canvas、RAF、cache bytes 或 worker

- **文件**：`frontend/src/features/simulation/test/mapFixture.test.ts:56-68`；`frontend/src/features/simulation/test/mapCanvasLayer.test.ts:186-204`；`frontend/src/features/simulation/test/mapWorker.test.ts:35-39`；实施摘要 `docs/hydraulic-twin/implementation/simulation-map.md:130-136`。
- **证据**：5k/10k/1k fixture test 只验证 normalize 后长度和 `ViewportIndex.query`；Canvas test 使用 fake map 和 stub context，并把 pipes 限到 500；worker test 强制 `useWorker:false`。没有任何真实 `requestAnimationFrame`、presented FPS、frame-time p95、long task、cache bytes、transfer 或 canonical fixture 断言。摘要也明确记录未做真实浏览器性能测量。
- **影响**：本次 17/17 单测通过不能证明用户规格中的“万级要素”或冻结性能门槛；H-02/H-03/H-07 等问题正好会被这些测试遗漏。
- **建议修复**：增加独立真实浏览器/performance harness，至少覆盖 5k/10k/1k、连续 pan/zoom、4x frame switch、worker transfer、cache byte budget，并输出契约要求的 JSON artifact；把 canonical fixtures 直接喂给 map/controller。

## 决定

高严重度问题未解决，`approved=false`。优先顺序建议为：先修复 H-01/H-02 的 API/worker 边界，再修复 H-03/H-04/H-05/H-06 的交互、生命周期和数据一致性，最后以 H-07 的 typed-array 路径和 M-09 的真实性能证据验证万级目标。
