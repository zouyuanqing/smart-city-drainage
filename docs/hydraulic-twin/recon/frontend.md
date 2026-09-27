# 前端现状侦察报告

> 范围：前端路由与权限、Leaflet、ECharts、Zustand、i18n、`useSSE`、Dashboard、性能，以及与水力孪生 PRD 的接口/页面缺口。
>
> 证据口径：以下结论均来自本次实际读取的 `frontend/src`、前端配置、测试文件，以及为契约核对而读取的后端路由/SSE/安全代码。PRD 目标来自本次任务给出的摘要；未被代码证实的目标均标为“缺口”，不把规划文档当成现状事实。

## 1. 结论摘要

当前前端是一个 React 18 + TypeScript + Vite 的“智慧城市神经末梢”监测/视频/告警应用，而不是水力数字孪生前端。现状具备可复用的页面壳、登录、角色菜单、Leaflet 设备点位、ECharts 趋势图、Zustand 状态和 SSE 基础能力，但 PRD 要求的水力核心域基本尚未落地：

- 路由只有 `/`、`/login`、`/dashboard`、`/map`、`/video`、`/alerts`、`/settings`；没有 `/simulation`，也没有网络、降雨、仿真、预测、率定、实测对比页面。证据：`frontend/src/App.tsx:17-100`。
- 权限目前是“前端菜单隐藏 + token 存在性路由守卫”，不是完整的路由/操作级 RBAC。`ProtectedRoute` 只检查 `localStorage` token；`AppLayout` 再按角色隐藏菜单；后端才是实际 API 权限边界。证据：`frontend/src/App.tsx:17-24`、`frontend/src/components/layout/AppLayout.tsx:71-110`、`backend/app/core/security.py:118-135`。
- Leaflet 目前只渲染 `Device` marker、告警浮层和设备详情；没有管网/节点/汇水区/充满度/淹没面/时间轴/播放控制，也没有视口裁剪、抽稀、Canvas 或空间索引。证据：`frontend/src/components/map/MapVisualization.tsx:236-317`。
- ECharts 目前是两个手写 imperative 图表（液位线、流量柱），没有 forecast、不确定性带、A/B 对比或共享图表抽象；每个图表直接 `echarts.init`，并响应 Zustand `readingHistory`。证据：`frontend/src/components/charts/WaterLevelChart.tsx:14-153`、`frontend/src/components/charts/FlowTrendChart.tsx:14-142`。
- Zustand 是单一全局 store，保存设备/读数/告警/选择项/UI 状态；读数历史每设备最多 30 条、告警最多 200 条。适合迁移为“实时域缓存”，但没有 simulation/rainfall/forecast/network slice，也没有持久化、请求状态或版本化事件模型。证据：`frontend/src/store/useAppStore.ts:35-144`。
- i18n 已接入 `i18next`、中英文资源和 localStorage/navigator 检测，但绝大多数页面文案仍硬编码中文；只有 Layout、Login、Settings 等使用 `t()`。证据：`frontend/src/i18n/index.ts:6-23`、`frontend/src/components/layout/AppLayout.tsx:33-37`、`frontend/src/pages/Dashboard.tsx:128-157`。
- `useSSE` 支持连接、`connected`/`sensors`/`alerts` 事件分发和指数退避重连；后端有 1000 条内存历史与 `Last-Event-ID` 重放。但目前没有 `forecast`/`simulation_progress` 等 PRD 事件，且 EventSource 不能携带 Authorization header，存在认证/事件契约扩展风险。证据：`frontend/src/hooks/useSSE.ts:31-135`、`backend/app/api/routes.py:393-413`、`backend/app/services/sse_manager.py:90-189`。
- Dashboard 目前是三栏监测驾驶舱，含设备、地图、视频、告警、液位/流量图；没有 PRD 要求的 forecast 卡片、Top-N 风险榜、仿真时间轴、A/B 指标。证据：`frontend/src/pages/Dashboard.tsx:178-558`。
- 本次环境中 `frontend/node_modules` 不存在；`npm test`、`npm run lint`、`npm run build` 均未能进入测试/构建执行，分别因找不到 `vitest`/`eslint`、TypeScript 配置/依赖错误失败。`npm run format:check` 可运行但报告 36 个文件格式问题。

## 2. 实际结构与可复用模式

### 2.1 应用壳、路由和错误边界

- 入口通过 `BrowserRouter`、Ant Design `ConfigProvider` 包裹应用，并加载全局暗黑主题与 i18n：`frontend/src/main.tsx:6-53`。
- `App` 采用集中式 `<Routes>`；每个受保护页面重复包裹 `ProtectedRoute + AppLayout + ErrorBoundary`：`frontend/src/App.tsx:35-100`。这是可直接复用的页面壳模式，但新增路由应避免继续复制嵌套样板，最好抽出 `ProtectedLayoutRoute`。
- 全局快捷键在 `App` 挂载，统一处理侧栏、Escape 和数字键导航：`frontend/src/App.tsx:26-33`、`frontend/src/hooks/useKeyboardShortcuts.ts:14-51`。数字键当前只映射旧页面，扩展 `/simulation` 时需同步权限过滤，否则会出现“菜单不可见但快捷键可跳转”的权限绕过。
- `ErrorBoundary` 能显示异常并重试，但没有日志上报、错误上下文或路由级 reset：`frontend/src/components/common/ErrorBoundary.tsx:13-49`。

### 2.2 认证与权限模式

- 登录调用 `POST /api/auth/login`，将 `access_token`、用户对象和角色写入三个 localStorage key：`frontend/src/pages/LoginPage.tsx:54-75`、`frontend/src/services/api.ts:17-47`。
- Axios 请求拦截器从 `scn_access_token` 注入 `Bearer`；401 清理认证状态并跳转 `/login`：`frontend/src/services/api.ts:17-39`。
- 路由守卫只检查 token 是否存在，不解析 JWT、不检查过期、不调用 `/auth/me`：`frontend/src/App.tsx:17-24`。后端提供 `/api/auth/me` 且有 `require_auth`，但前端未使用：`backend/app/api/routes.py:155-162`。
- 菜单角色层级是 `admin(3) > operator(2) > viewer(1)`，`hasRole` 从 localStorage 读取角色并按数值比较：`frontend/src/store/useAppStore.ts:8-33`。菜单项要求 viewer/operator/admin 的方式见 `frontend/src/components/layout/AppLayout.tsx:71-110`。
- 后端 `require_role` 是真正的 API 授权，使用 token 中 role，未认证 401、角色不匹配 403：`backend/app/core/security.py:95-135`。后端模型切换仅 admin，流启动/停止、告警确认、设备写操作等是 admin/operator，设备删除仅 admin：`backend/app/api/routes.py:177-209`、`315-342`、`864-891`、`955-1030`。
- **可复用模式**：继续把后端作为最终权限来源；前端将 `hasRole` 用于导航/按钮呈现，新增 hydraulic 写操作不得只依赖隐藏按钮。
- **契约/安全风险**：`localStorage` 角色可被用户篡改；角色层级是前端自定义，而后端是精确角色枚举匹配。当前层级能覆盖 admin/operator/viewer 的常见关系，但未来若新增角色或继承语义，前后端可能分叉。路由守卫也没有把角色传给 `ProtectedRoute`，所以 viewer 可直接输入 `/settings` URL；页面里的模型/模拟操作可能被后端 403，但 UI 边界不完整。

### 2.3 Leaflet 现状

- `MapVisualization` 使用 `react-leaflet` 的 `MapContainer`、`TileLayer`、`Marker`、`Popup`、`useMap`，底图为 CARTO dark tiles，中心固定上海 `[31.23, 121.47]`：`frontend/src/components/map/MapVisualization.tsx:5-16`、`246-281`。
- 每个设备生成一个 `L.DivIcon`，颜色由在线/离线/故障/告警决定；marker 用 `memo`，但比较器只比较设备 id、状态、选中、告警，不比较读数：`frontend/src/components/map/MapVisualization.tsx:26-40`、`51-122`。这会导致读数变化时不重绘 marker（当前颜色不由读数决定，若未来按充满度着色则必须修正）。
- `MapController` 用 `ResizeObserver` 调 `invalidateSize`，首次设备加载时 `fitBounds`，选中设备时 `flyTo`：`frontend/src/components/map/MapVisualization.tsx:167-205`。
- 告警浮层只取未确认的前 5 条，使用 Framer Motion；图例和左上角 HUD 都是设备/告警计数：`frontend/src/components/map/MapVisualization.tsx:124-165`、`208-234`、`297-315`。
- CSS 有 Leaflet 容器背景覆盖、脉冲 marker 动画和 `will-change`：`frontend/src/styles/globals.css:322-371`。
- **可复用模式**：`MapController` 的尺寸监听/fitBounds/选中 flyTo 可作为水力地图生命周期模板；`DeviceMarker` 的 memo 壳可复用到节点/管段，但应拆分为几何图层与交互图层。
- **PRD 缺口**：没有 `GeoJSON`/WKT 导入展示、网络线、节点、汇水区、充满度色带、预测/已发生区分、淹没面图层、5 分钟帧序列、播放/暂停/拖拽/1x/2x/4x 控制，也没有 bbox/视口裁剪、抽稀、Canvas/WebGL/预渲染。当前实现直接 `.map` 创建 marker：`frontend/src/components/map/MapVisualization.tsx:285-294`，不能支撑 PRD 的万级管段/千级多边形 25fps 目标。

### 2.4 ECharts 现状

- 依赖同时声明 `echarts` 与 `echarts-for-react`，但实际两个组件均 `import * as echarts from 'echarts'`，未使用 wrapper：`frontend/package.json:23-25`、`frontend/src/components/charts/WaterLevelChart.tsx:5-8`、`frontend/src/components/charts/FlowTrendChart.tsx:5-8`。
- 图表流程是：按 `deviceId` 调 `sensorAPI.getHistory(deviceId, 6, 5)`，再监听 `readingHistory` 取最近 30 条；数据变化后重新构造完整 option 并 `chart.setOption(option, true)`：`WaterLevelChart.tsx:20-54`、`56-151`，`FlowTrendChart.tsx:20-54`、`56-139`。
- 水位图是平滑折线+面积渐变+LTTB sampling+固定 180mm 警戒线：`WaterLevelChart.tsx:111-147`；流量图是柱状图+渐变+动画：`FlowTrendChart.tsx:110-135`。
- 初始化和销毁正确使用 `echarts.init`、`ResizeObserver`、`dispose`：`WaterLevelChart.tsx:56-69`、`FlowTrendChart.tsx:56-69`。这是可复用的生命周期模式。
- Vite 将 ECharts 单独分到 `echarts` manual chunk，但没有按需引入：`frontend/vite.config.ts:46-58`。全量 `import * as echarts` 可能使首屏/按需加载收益有限；本次未测 bundle 体积。
- **PRD 缺口**：没有 simulation frame chart、forecast/observed 对照、置信区间/不确定性带、A/B 指标差异、RMSE/NSE 展示。现有 `SensorReading` 也没有 simulation/forecast 字段：`frontend/src/types/index.ts:19-31`。

### 2.5 Zustand 现状

- 单一 `useAppStore` store 定义用户、传感器 Map、每设备 30 条历史、最多 200 条告警、设备、选择项、侧栏/视频 UI 状态：`frontend/src/store/useAppStore.ts:35-67`、`69-144`。
- `setSensorReadings` 用新 Map 替换读数，并在每个设备历史上 `push` 后 `shift` 保留 30 条：`frontend/src/store/useAppStore.ts:78-89`。
- 告警按 id 去重，插入头部并裁剪到 200；确认/解决通过 map 更新状态：`frontend/src/store/useAppStore.ts:100-125`。
- 组件有 selector 用法（设备详情逐字段选择），但 Dashboard、MapView、Layout 多处使用整个 store 解构，会在任意 slice 更新时重新渲染：`frontend/src/components/device/DeviceDetailPanel.tsx:16-26`、`frontend/src/pages/Dashboard.tsx:179-191`、`frontend/src/pages/MapView.tsx:12-22`、`frontend/src/components/layout/AppLayout.tsx:33-40`。
- **可复用模式**：保留单一实时域 store 的 Map 索引和历史裁剪思路；新增 hydraulic state 时建议按 domain slice（network/simulation/rainfall/forecast/alert）拆分 selector，避免把所有帧和设备一次性放入全局对象。
- **契约/性能风险**：simulation frame 序列若直接进入此 store，会触发大范围 rerender 和深比较压力；当前没有 `persist`、请求状态、版本/游标、事件去重键规范化，也没有 abort/cancel 或分页/增量加载。

### 2.6 i18n 现状

- `i18next` 初始化中英文资源，语言检测顺序为 localStorage、navigator，localStorage key 为 `scn_language`，fallback 为 zh：`frontend/src/i18n/index.ts:6-23`。
- 当前资源只有 app/nav/login/dashboard/map/video/alerts/settings/common：`frontend/src/i18n/zh.json:1-94`、`frontend/src/i18n/en.json:1-94`。
- `AppLayout`、`LoginPage`、`SettingsPage` 使用 `useTranslation`；Dashboard、AlertCenter、MapVisualization、VideoMonitor、AlertPanel 等大量中文文本直接写在 JSX 中，例如 `frontend/src/pages/Dashboard.tsx:128-157`、`313-365`、`450-474`，`frontend/src/pages/AlertCenter.tsx:84-102`，`frontend/src/components/map/MapVisualization.tsx:208-230`。
- **可复用模式**：沿用 `useTranslation` + JSON namespace + detector/cache；语言切换已经在 Settings 中实现：`frontend/src/pages/SettingsPage.tsx:342-363`。
- **PRD 缺口**：没有 `/simulation` 菜单/页面，也没有 simulation、rainfall、forecast、risk、network、calibration、A/B 等 key；无法满足“路由菜单权限 i18n”。切换语言后 Dashboard 仍会出现中文硬编码。

### 2.7 SSE 与实时通道

- `useSSE` 固定连接 `/api/sse/events`，用 `EventSource`；连接成功后重置重试次数，接收 `connected`、`sensors`、`alerts`，解析失败只记录日志：`frontend/src/hooks/useSSE.ts:47-93`。
- 断线后指数退避 1s、2s、4s…最大 30s，显式 `reconnect` 可用；卸载时清 timer、关 EventSource：`frontend/src/hooks/useSSE.ts:28-45`、`95-135`。
- 后端 SSE endpoint 读取 `Last-Event-ID` 并交给 manager：`backend/app/api/routes.py:393-413`；manager 保留最近 1000 个事件，重连时只重放 id 大于 Last-Event-ID 的历史，并在无事件时 15s heartbeat：`backend/app/services/sse_manager.py:32-43`、`142-189`。
- Vite dev proxy 对包含 `/sse/` 的请求设置 `Connection: keep-alive`：`frontend/vite.config.ts:24-39`；生产 nginx 的 `/api/` 使用 HTTP/1.1、300s read timeout，但没有显式 `proxy_buffering off` 或 SSE 专用 location：`frontend/nginx-default.conf:28-40`。
- **可复用模式**：hook 的 ref 保存 EventSource/timer、指数退避、连接状态和手动重连可以复用于 forecast 事件；后端已有 Last-Event-ID 语义可作为新事件契约基础。
- **关键风险**：
  1. EventSource API 不能像 Axios 一样注入 `Authorization` header；当前 `/api/sse/events` 后端也未加认证依赖（`backend/app/api/routes.py:393-413`）。如果新 PRD 要求 SSE 认证，需要 cookie、同源 session 或 query/短期 token 等明确方案，不能假设现有 Bearer 逻辑自动生效。
  2. 当前 hook 没有 `system` 事件监听，虽然后端 Redis model handler 会广播 `system`：`backend/app/main.py:78-94`；也没有 `forecast`、simulation progress/cancel/result 事件。
  3. 后端 SSE 的 `sensors`/`alerts` payload 形状与前端类型/调用存在漂移风险：后端 `broadcast_sensor` 包 `{readings,timestamp}`、`broadcast_alert` 包 `{alert,timestamp}`：`backend/app/services/sse_manager.py:122-140`；hook 期待 `payload.readings`、`payload.alert`：`frontend/src/hooks/useSSE.ts:75-92`。这与现有 mock 告警字段 `alert_id` 而非前端 `Alert.id` 也不一致：`backend/app/services/mock_data_generator.py:231-248`、`frontend/src/types/index.ts:45-61`。
  4. `useSSE` 在 Dashboard、MapView、AlertCenter 各自调用，路由切换会销毁/重建连接；如果未来把 SSE 提升到布局级，必须避免多连接重复消费。证据：`frontend/src/pages/Dashboard.tsx:221-235`、`frontend/src/pages/MapView.tsx:26-29`、`frontend/src/pages/AlertCenter.tsx:44-46`。
  5. 生产 nginx 没有明确关闭 SSE buffering；应在部署验证中用 `curl -N`/浏览器 Network 检查首包和 heartbeat，而不是仅依赖 dev proxy 配置。

## 3. Dashboard 与当前数据流

- Dashboard 初始化并行请求 `deviceAPI.getList()` 与 `sensorAPI.getLatest()`，设备字段在页面内映射成前端 `Device`，然后把读数写入 store：`frontend/src/pages/Dashboard.tsx:237-259`。
- Dashboard 的 SSE 回调把读数写入 store、把告警 prepend；另有本地流状态请求：`frontend/src/pages/Dashboard.tsx:221-275`。
- 统计条的“在线设备”用 `battery_level > 0` 推断，而非 `devices` 的 `status`：`frontend/src/pages/Dashboard.tsx:119-124`。这在数据语义上是脆弱的，可能把电量>0的离线设备算在线；应改为设备状态或后端统计。
- Dashboard 地图只展示设备 marker，右侧是告警、液位/流量图和视频矩阵：`frontend/src/pages/Dashboard.tsx:309-475`。系统运行状态卡片是静态文字（SSE/WS/AI 始终显示正常/就绪），而 SSE 实际状态另有 badge：`frontend/src/pages/Dashboard.tsx:477-500`、`337-357`。
- 告警点击会设置选中告警/设备并打开视频抽屉，而不是仿真结果或风险详情：`frontend/src/pages/Dashboard.tsx:277-287`、`513-555`。
- **PRD 缺口**：无 forecast latest/horizon/risk-ranking 卡片、Top-N 风险榜、预测/已发生视觉区分、仿真历史/进度/取消、A/B 对比、RMSE/NSE 率定卡、预测与实测/不确定性带。现有 `StatsBar` 的“网络延迟”“AI 推理”等也是硬编码展示，不是实测指标：`frontend/src/pages/Dashboard.tsx:127-173`。

## 4. API/类型契约核对

### 4.1 已有前端 API

`frontend/src/services/api.ts` 只封装认证、传感器、告警、设备、模型、推理、视频、系统、mock 和导出：`frontend/src/services/api.ts:42-184`。没有 networks、rainfall-scenarios、simulations、forecast、calibration/observations API 封装。

现有前端调用与后端现状对照：

- `authAPI.login` → `POST /api/auth/login`：`frontend/src/services/api.ts:45-47`；后端实际路由：`backend/app/api/routes.py:93-152`。
- `sensorAPI.getLatest/getHistory` → `/api/sensors/latest`、`/api/sensors/history/{device_id}`：`frontend/src/services/api.ts:52-60`；后端：`backend/app/api/routes.py:589-649`。
- `alertAPI.getList/acknowledge` → `/api/alerts`、`/api/alerts/{id}/acknowledge`：`frontend/src/services/api.ts:65-71`；后端：`backend/app/api/routes.py:725-800`、`864-891`。
- `streamAPI.start` 发送 `device_id/stream_url/name/protocol`：`frontend/src/services/api.ts:124-136`；后端 `StreamCreate` 要求 UUID `device_id`、`name`、`stream_url`，路由接收并转换：`backend/app/schemas/schemas.py:271-279`、`backend/app/api/routes.py:315-343`。
- 导出 API 以 blob 下载 CSV：`frontend/src/services/api.ts:171-181`；后端有对应权限保护的导出路由：`backend/app/api/routes.py:652-717`、`803-861`。

### 4.2 契约风险清单

1. **SSE 告警 id 漂移**：前端 `Alert` 要求 `id`、`created_at`、`is_acknowledged`、`is_resolved`：`frontend/src/types/index.ts:45-61`；mock SSE 生成的是 `alert_id`、`timestamp`，没有前端状态字段：`backend/app/services/mock_data_generator.py:231-248`。如果直接把 `payload.alert` 交给 `addAlert`，可能出现 undefined id、通知去重失效、确认按钮/详情字段缺失。
2. **SSE 传感器字段可能不一致**：前端要求 `device_code/device_name/timestamp`，`SensorReading` 还要求温度、电池、信号等：`frontend/src/types/index.ts:19-31`；mock 通常提供这些字段，但数据库 schema 的 `SensorReading` 字段名为 `time`、没有前端同名的 `device_code/device_name/battery_level`：`backend/app/schemas/schemas.py:160-180`。需在边界层做明确 normalization，而不是在各组件兼容。
3. **事件命名不足**：后端 route 文档列出 `sensors`、`alerts`、`system`：`backend/app/api/routes.py:396-407`；hook 只监听前两个加握手：`frontend/src/hooks/useSSE.ts:66-93`。PRD 需要新增 `forecast`、simulation 状态/进度、结果/错误等事件时，必须先冻结 event name 和 envelope。
4. **认证边界**：Axios 有 Bearer，但原生 EventSource 和 WebSocket hook 没有 token 注入：`frontend/src/services/api.ts:17-24`、`frontend/src/hooks/useSSE.ts:56-57`、`frontend/src/hooks/useWebSocket.ts:26-27`。当前后端 WebSocket `/api/ws/control` 也没有 `require_auth`：`backend/app/api/routes.py:421-431`。新管理/仿真写通道不能照搬这一无认证模式。
5. **错误结构**：Axios 响应拦截器把后端 `detail` 提取为字符串并重新抛出 `Error`：`frontend/src/services/api.ts:26-39`；PRD 要求 Swagger/错误格式兼容，前端应保留 status、detail、字段错误等结构，不能只保留 message。
6. **角色枚举与页面操作不一致**：前端 `hasRole` 采用层级，API 拦截只处理 401，不处理 403 的权限提示/跳转：`frontend/src/store/useAppStore.ts:18-28`、`frontend/src/services/api.ts:26-39`。新增 simulation 提交/取消/降雨编辑必须为每个按钮绑定 operator/admin 规则，并处理 403。

## 5. 性能现状与 PRD 目标差距

### 已有可复用措施

- Vite 配置了 vendor、Ant Design、ECharts、Leaflet manual chunks：`frontend/vite.config.ts:46-58`。
- 地图 marker 组件使用 `memo`，告警设备集合用 `useMemo`，地图 resize 用 `ResizeObserver`：`frontend/src/components/map/MapVisualization.tsx:51-122`、`167-184`、`256-263`。
- ECharts 使用 `ResizeObserver`、组件卸载时 `dispose`，水位线开启 `sampling: 'lttb'`：`frontend/src/components/charts/WaterLevelChart.tsx:56-69`、`111-123`。
- Zustand 对读数历史和告警数组做了固定上限：`frontend/src/store/useAppStore.ts:78-89`、`102-110`；告警面板只渲染前 50 条：`frontend/src/components/alerts/AlertPanel.tsx:112-150`。
- 设备详情/统计使用 CSS overflow 和滚动条；Dashboard 设备列表分栏滚动：`frontend/src/pages/Dashboard.tsx:309-335`、`frontend/src/styles/globals.css:65-84`。

### 主要瓶颈/风险

- **地图仍是 React DOM marker 列表**：每个 `devices.map` 产生一个 Leaflet Marker/DivIcon；没有 `preferCanvas`、GeoJSON layer、聚合、视口 bounds 过滤、空间索引或抽稀。证据：`frontend/src/components/map/MapVisualization.tsx:26-40`、`285-294`。PRD 的 5000 节点/10000 管段/千级多边形不能靠现状扩展。
- **全量 rerender 风险**：Dashboard/MapView/Layout 直接解构整个 Zustand store：`frontend/src/pages/Dashboard.tsx:179-191`、`frontend/src/pages/MapView.tsx:12-22`、`frontend/src/components/layout/AppLayout.tsx:33-40`；SSE 每批读数都会替换 `sensorReadings`，会带动大块页面更新。需拆分 selector/按设备订阅。
- **marker memo 比较不包含 reading**：`MapVisualization.tsx:117-122`；如果将 marker 颜色改为充满度/预测状态而不调整比较器，地图会显示旧值。
- **图表全量 option 重建**：两个图表每次 data 变化都重新构造 option 并 `setOption(..., true)`：`WaterLevelChart.tsx:71-151`、`FlowTrendChart.tsx:71-139`；多设备、多图、A/B 或长时间轴可能成为 CPU/内存瓶颈。可改为增量 series/数据点、限制窗口、按需渲染。
- **SSE 多连接**：Dashboard、MapView、AlertCenter 分别建立 EventSource；若这些页面未来并存于布局/弹窗，会重复消费和增加连接数。
- **动画常驻**：marker 脉冲、扫描线、dashboard 粒子/Framer Motion 等会占用合成线程；登录和 landing 页还在每次 render 的 style 中调用 `Math.random()`：`frontend/src/pages/LoginPage.tsx:20-42`、`frontend/src/pages/LandingPage.tsx:20-43`。大屏场景需考虑 `prefers-reduced-motion`、降低粒子数或只在静止状态启用。
- **没有性能测量**：本次未找到 25fps、FPS、地图绘制耗时、bundle size、内存、长任务或 Lighthouse/Playwright 性能脚本；因此不能声称满足 PRD 性能 AC。

## 6. 关键文件索引

| 领域 | 文件/符号 | 当前职责 | 迁移价值/风险 |
|---|---|---|---|
| 路由 | `frontend/src/App.tsx:17-100` `ProtectedRoute`, `Routes` | 旧页面路由和 token 存在性守卫 | 抽出统一受保护布局；补角色/未认证/过期态；新增 `/simulation` |
| 菜单权限 | `frontend/src/components/layout/AppLayout.tsx:33-110` `AppLayout`, `hasRole` | 顶栏、侧栏、角色菜单、模型状态轮询 | 复用菜单过滤；必须配合路由/按钮/后端 RBAC |
| 认证 API | `frontend/src/services/api.ts:11-47` | Axios、Bearer、401 清理、登录 | 扩展统一错误 envelope；勿把 token 放 query 当长期方案 |
| 状态 | `frontend/src/store/useAppStore.ts:35-144` `useAppStore` | 读数/告警/设备/UI 单 store | 可保留实时索引；拆 hydraulic domain slice、selector、帧缓存 |
| SSE | `frontend/src/hooks/useSSE.ts:31-135` `useSSE` | 连接、事件分发、重连 | 扩展 typed events；改为单连接服务/布局级生命周期；处理认证 |
| 地图 | `frontend/src/components/map/MapVisualization.tsx:246-317` `MapVisualization` | 设备点、告警、控制器 | 抽图层/交互/数据适配；换成高性能几何渲染 |
| 图表 | `frontend/src/components/charts/WaterLevelChart.tsx:14-153`; `FlowTrendChart.tsx:14-142` | 液位线、流量柱 | 抽 ECharts registry/生命周期；支持预测、不确定性、A/B |
| Dashboard | `frontend/src/pages/Dashboard.tsx:178-558` `Dashboard` | 旧监测三栏驾驶舱 | 保留布局/流/告警壳；新增 simulation/forecast/risk 卡片 |
| i18n | `frontend/src/i18n/index.ts:6-25`; `src/i18n/zh.json`; `en.json` | 语言检测与旧资源 | 新增 simulation 命名空间；清理硬编码 |
| 测试 | `frontend/src/test/__tests__/LoginPage.test.tsx`; `useAppStore.test.ts` | 登录渲染、store 基础行为 | 增 route/RBAC/SSE/地图/图表/性能测试 |
| 构建 | `frontend/vite.config.ts:24-61`; `vitest.config.ts:4-24` | dev proxy、manual chunks、Vitest | 新增 API proxy/SSE 生产配置和性能预算检查 |

## 7. 可复用模式（建议优先级）

1. **统一页面壳**：从 `App.tsx` 的重复 `ProtectedRoute + AppLayout + ErrorBoundary` 抽出路由布局；所有新页面沿用 ErrorBoundary，但将权限检查放在路由元数据/守卫和 API 两层。
2. **领域适配层**：在 `services/api.ts` 旁增加 typed hydraulic API + normalization；SSE payload 先转换为现有/新领域类型，再进入 store/组件，避免组件直接适配后端漂移字段。
3. **Zustand 细粒度 selector**：保留 `Map` 索引与历史上限思路；按设备/网络/仿真任务分片，组件只订阅所需字段；帧数据使用 ring buffer/worker 或独立图表数据源，避免全局大对象。
4. **地图分层**：复用 `MapController` 的尺寸/fit/flyTo；将底图、管网、节点、汇水区、淹没面、告警、交互拆层。万级几何优先评估 Leaflet Canvas/GeoJSON + 视口 bounds + 抽稀，必要时预渲染。
5. **图表生命周期**：复用 `ResizeObserver`/`dispose`，进一步抽 ECharts instance registry、按需注册和增量更新；为 forecast 使用预测线、实测线、置信带三种 series。
6. **实时事件单例化**：把 `useSSE` 抽成 provider/store subscription 或 layout-level service，页面只订阅事件；为事件定义 TypeScript discriminated union、event id、version、sequence 和 replay 语义。
7. **i18n 命名空间**：沿用 detector/cache，但把旧硬编码文案迁移到 `common/dashboard/map/alerts/simulation/rainfall/forecast` namespace；菜单权限和页面标题必须走同一份 i18n key。

## 8. 建议新增/改造的契约（与 PRD 对齐的最低边界）

以下是依据本次 PRD 摘要和现状缺口推导的**建议契约，不是当前代码事实**：

- `GET /api/forecast/latest`、`GET /api/forecast/horizon`、`GET /api/forecast/risk-ranking` 返回明确的 forecast envelope；告警至少包含目标、预计时刻、`lead_time_minutes`、深度、置信度、`simulation_id`。
- `flood_forecast` 与 `pipe_surcharge` 事件必须有稳定 event name 和版本化 payload；SSE 事件 envelope 建议至少 `{id, type, occurred_at, data}`，并定义重放、去重和过期策略。
- simulation API 至少区分 submit/status/progress/cancel/result/history/calibration/observed-comparison；前端状态机应能表达 queued/running/retrying/cancelled/completed/failed/timeout，而不是只靠布尔 loading。
- rainfall scenario、网络图层、simulation frame、A/B comparison 的 TypeScript 类型要独立于旧 `Device`/`SensorReading`；禁止把 90 天帧序列直接无界放入 Zustand。
- 所有 simulation/rainfall/network 写操作的 UI role、API role、按钮 disabled 条件应由同一权限矩阵生成；后端仍为最终授权。

## 9. 测试与检查记录（本次实际运行）

> 依赖状态：运行时 `node --version` 输出 `v25.9.0`，`npm --version` 输出 `11.12.1`；`frontend/node_modules` 检查结果为 `absent`。因此不能把未安装依赖导致的失败误报为代码测试失败。

| 命令 | 实际结果 | 解释 |
|---|---|---|
| `cd frontend && npm test -- --reporter=dot` | 退出码 1；输出：`'vitest' 不是内部或外部命令，也不是可运行的程序` | 测试套件未运行，原因是依赖目录不存在 |
| `cd frontend && npm run lint` | 退出码 1；输出：`'eslint' 不是内部或外部命令，也不是可运行的程序` | lint 未运行，原因是依赖目录不存在 |
| `cd frontend && npx tsc -b --pretty false --noEmit` | 退出码 1；缺少 `@types/node`，并报告 `baseUrl` deprecated、引用项目不能 `noEmit` | TypeScript 检查未通过；其中部分错误由当前 TypeScript/依赖环境触发，源码完整类型结果不能据此判定 |
| `cd frontend && npm run build` | 退出码 1；`tsc -b` 阶段失败，未进入 Vite build | 生产构建未通过/未完成；不是把 build 当作测试通过 |
| `cd frontend && npm run format:check` | 退出码 1；Prettier 报 36 个文件存在格式问题 | 这是实际运行的检查；未执行自动修复，因本任务只读源码 |
| `git diff --check -- frontend` | 退出码 0 | 仅表示当前 frontend diff 无空白错误，不等于 lint/test/build 通过 |
| `git status --short --branch` | `## main...origin/main` | 调研开始时工作树无未提交变更（报告写入路径除外尚未写入） |

现有测试文件只覆盖：

- 登录页静态渲染和输入数量：`frontend/src/test/__tests__/LoginPage.test.tsx:20-37`。
- Zustand 初始化、用户、告警、设备、30 条历史和 UI 状态：`frontend/src/test/__tests__/useAppStore.test.ts:15-107`。

没有发现路由守卫、角色菜单、Axios 401/403、SSE 重连/事件 payload、Leaflet 图层、ECharts 生命周期、i18n 切换、Dashboard 性能或 hydraulic API 测试。

## 10. 缺口与下一步建议（按优先级）

### P0：建立水力域契约和安全边界

1. 先冻结 `Network/Node/PipeSegment/Catchment/RainfallScenario/Simulation/Forecast` TypeScript 类型、API envelope、错误 envelope、SSE event schema；补 `services/hydraulicApi.ts` 或等价模块。
2. 新增 `/simulation` 路由及统一 route metadata；ProtectedRoute 改为验证认证状态（可调用 `/auth/me`），页面/按钮/后端三层一致执行 admin/operator 规则。
3. 定义 SSE 单连接生命周期和认证方案；不要直接把旧的匿名 EventSource 模式复制到 forecast/simulation 事件。

### P1：实现地图与实时回放骨架

1. 把 Leaflet 图层拆为 typed layers，先完成网络/节点/汇水区，再加入 simulation frame、充满度和 forecast/occurred 样式。
2. 为 5000 节点/10000 管段/千级多边形建立视口 bounds 过滤、几何抽稀、Canvas/预渲染基准；补 25fps 测试/采样脚本后才可宣称达标。
3. 把 timeline 状态（帧索引、播放、暂停、1/2/4 倍速）与渲染层分离，避免每帧重建 React 树。

### P1：Dashboard、图表和 i18n

1. Dashboard 增加 forecast 卡片、Top-N 风险榜、时间轴入口、任务进度/历史/取消、A/B 对比和率定指标；把静态状态卡替换成真实 API/SSE 状态。
2. ECharts 抽共享 registry 和按需注册，扩展预测/实测/不确定性带；限制窗口和点数，增量更新。
3. 新增 i18n namespaces，迁移 Dashboard/Map/Alert/Video/Settings 的硬编码文案；语言切换后验证菜单、权限提示、图表轴和单位。

### P2：验证与可观测性

1. 增加 Vitest：route/RBAC、API error normalization、SSE fake EventSource/reconnect、store selectors、Leaflet mock、ECharts dispose。
2. 增加 Playwright smoke：viewer/operator/admin 访问矩阵、SSE 断线、401/403、simulation 状态转移、forecast/risk 卡片。
3. 增加 bundle/FPS/long-task/memory 检查和 CI 门禁；当前仓库没有这些性能测试或脚本。

## 11. 不应误读的事项

- 后端当前确实实现了旧系统的 `/api/sensors`、`/api/alerts`、`/api/devices`、`/api/sse/events` 等端点；这不等于 PRD 的 networks/rainfall/simulations/forecast API 已存在。实际路由声明清单由 `rg` 扫描 `backend/app/api/routes.py` 得到，当前未发现这些 hydraulic 端点。
- 当前 mock generator 会广播传感器和告警，但 mock 告警字段是 `alert_id`/`timestamp`，不能直接证明符合前端 `Alert` 类型；必须在契约适配层解决。
- `hasRole` 的菜单隐藏不是安全控制；后端 `require_role` 才是实际 API 权限检查。
- 本次没有安装依赖、没有修改源码、没有执行自动格式化；所有未能运行的检查均在第 9 节按实际输出报告。
