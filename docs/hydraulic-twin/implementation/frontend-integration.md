# Frontend vertical-slice integration

日期：2026-09-25  
模块：`frontend-integration`  
状态：已完成代码集成与本轮前端回归；未提交、未推送。

## 1. 依据与范围

本轮先阅读并按以下冻结输入实施：

- `docs/hydraulic-twin/SOURCE_PRD.md`：FR-6 要求 `/simulation`、地图/时间轴/任务/雨型/A-B/图表和 Dashboard forecast/risk（`SOURCE_PRD.md:24-30`）。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md`：角色矩阵、预测/实测、A/B、错误和前端容量边界（`PRD_SUPPLEMENT.md:435-451,489-560`）。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md`：前端路由/RBAC、i18n、状态隔离、Canvas、FrameCache、时间轴、A/B、SSE 和懒加载契约（`IMPLEMENTATION_CONTRACT.md:1899-1964`）。

本轮只修改用户授权的 `frontend/src/**` 路径；没有修改后端、Compose、Docker 或冻结 PRD/契约。工作树中其他并行模块的既有改动未回滚、未覆盖。

## 2. 实际改动

### 2.1 路由、菜单和角色守卫

- `frontend/src/App.tsx` 集中认证布局，认证页面使用 `React.lazy`/`Suspense`，新增 `/simulation` 路由，并让 video/alerts/settings 继续使用原有页面。
- `frontend/src/auth/RouteGuard.tsx`、`roles.ts`、`routeAccess.ts` 新增 fail-closed 角色读取与路由矩阵：simulation 为 viewer+，video/alerts 为 operator+，settings 为 admin+；无 token/未知角色不会获得写权限。
- `frontend/src/components/layout/AppLayout.tsx` 增加 simulation 菜单、导航高亮、可访问的菜单按钮、退出清理和基于安全角色 helper 的菜单过滤。
- `frontend/src/i18n/en.json`、`zh.json` 增加 `simulation`、`network`、`map`、`timeline`、`tasks`、`rainfall`、`forecast`、`comparison`、`errors` 文案，并保持中英文结构对应。

### 2.2 仿真工作台

- `frontend/src/pages/SimulationPage.tsx` 和 `SimulationPage.css` 组合：网络选择、单 Canvas 水力地图、时间轴、任务面板、降雨编辑器、A/B 指标/图表、预测/实测状态和运行配置。
- `frontend/src/features/simulation/integration/useHydraulicWorkspace.ts` 管理网络/地图/帧索引/当前帧元数据和取消；大几何不进入 legacy Zustand。
- `frontend/src/features/simulation/integration/HydraulicMapPanel.tsx` 使用已有 `SimulationMapController`/`HydraulicCanvasLayer`，以 imperative Leaflet layer 承载节点、管段、汇水区和积水，不创建每要素 React Marker。
- `frontend/src/features/simulation/integration/ComparisonWorkbench.tsx` 接入已有 A/B 表格、差异图例和 modular ECharts；比较仍由服务端返回的 compatibility/metrics 决定。
- `frontend/src/features/simulation/integration/networkApi.ts`、`rainfallApi.ts`、`observedApi.ts` 提供页面边界上的 typed network/rainfall/observed 请求适配，保留 canonical DTO normalizer 的 fail-closed 行为。

### 2.3 Dashboard、forecast/risk 懒加载和 SSE

- `frontend/src/pages/Dashboard.tsx` 增加 `ForecastRiskPanel`，网络选择在 integration hook 中维护，forecast/risk 组件通过 `React.lazy` 单独打包。
- `frontend/src/components/layout/ForecastRiskCard.tsx` 和 `frontend/src/features/simulation/integration/ForecastRiskCard.tsx` 只负责卡片/边界，不复制 forecast reducer。
- `frontend/src/features/simulation/realtime/HydraulicRealtimeProvider.tsx` 建立单 bearer-authenticated fetch-SSE 连接，统一分发 forecast 和 task 事件，保留 `Last-Event-ID`、去重、sequence gap/resync、409 cursor reset 和指数退避。
- `frontend/src/features/simulation/forecast/ForecastStreamProvider.tsx` 优先消费共享 realtime provider；独立组件/测试环境才使用原 fetch hook fallback。
- 旧 `frontend/src/hooks/useSSE.ts` 未修改；Dashboard 现有 legacy SSE 行为保持原路径。

### 2.4 测试稳定性

- `frontend/src/test/setup.ts` 在测试缺少完整 localStorage API 时安装最小 memory storage，并在每轮清理，修复既有 LoginPage 测试的 `localStorage.clear is not a function` 环境错误。
- 新增角色矩阵、网络 envelope、Dashboard forecast panel、共享 forecast stream boundary 测试；既有 task/forecast/map/timeline/chart 测试继续运行。

## 3. 本轮实际执行的命令与结果

### 通过

1. 完整 Vitest：

   ```text
   cd smart-city-drainage/frontend && npm test -- --reporter=dot
   Test Files 26 passed (26)
   Tests      119 passed (119)
   ```

2. 完整 ESLint：

   ```text
   cd smart-city-drainage/frontend && npm run lint
   exit 0; no output
   ```

3. 完整 TypeScript/Vite build：

   ```text
   cd smart-city-drainage/frontend && npm run build
   tsc -b passed
   vite: 4116 modules transformed
   built in 11.63s
   ```

   构建明确生成了 `SimulationPage-*.js`、`ForecastDashboard-*.js` 和 `ForecastStreamBoundary-*.js` 独立 chunk；仍有既有 `echarts` chunk 超过 1000 kB 的 warning。

4. 本任务集成文件 Prettier 检查：

   ```text
   cd smart-city-drainage/frontend && npx prettier --check "src/App.tsx" "src/auth/**/*.{ts,tsx}" "src/components/layout/AppLayout.tsx" "src/components/layout/ForecastRiskCard.tsx" "src/features/simulation/forecast/ForecastStreamProvider.tsx" "src/features/simulation/integration/**/*.{ts,tsx}" "src/features/simulation/realtime/**/*.{ts,tsx}" "src/i18n/en.json" "src/i18n/zh.json" "src/pages/Dashboard.tsx" "src/pages/SimulationPage.tsx" "src/pages/SimulationPage.css" "src/test/setup.ts"
   All matched files use Prettier code style!
   ```

5. 关键 focused 回归（本轮中途和最终均执行）：

   ```text
   npm test -- --run src/auth/routeAccess.test.ts src/features/simulation/integration/networkApi.test.ts src/features/simulation/integration/ForecastRiskPanel.test.tsx src/features/simulation/integration/ForecastStreamBoundary.test.tsx src/features/simulation/test/forecastDashboard.test.tsx src/features/simulation/test/taskStream.test.tsx --reporter=dot
   Test Files 6 passed (6)
   Tests      12 passed (12)
   ```

6. `git diff --check -- frontend/src frontend/package.json frontend/vite.config.ts frontend/nginx-default.conf`：通过；仅报告 Git 的 LF→CRLF 工作树提示，无 whitespace error。

### 未通过/未作为通过证据

- 完整 `npm run format:check` 仍失败，报告 29 个并行/既有文件格式问题；本轮集成-owned 文件单独 Prettier 检查通过。
- 本轮没有启动 FastAPI、PostgreSQL、Redis、真实浏览器或 60 秒 pan/zoom 性能 harness；没有运行 G5/25 FPS/FrameCache 256 MiB 的真实机器证据。构建 chunk 拆分和已有 10k map/cache 单测不等于 G5 PASS。
- 没有执行真实 forecast/SSE 后端联调；SSE 证据来自确定性 fetch mock 单测。

## 4. 风险与后续事项

1. 当前 API adapter/后端仍是开发内存 adapter 的可能性由后端 owner 决定；页面不在前端伪造缺失数据，REST 状态和 SSE gap 仍按契约处理。
2. 现有 `echarts` 依赖仍产生约 1.03 MB chunk warning；本轮通过路由/forecast 懒加载隔离了 Simulation/Forecast chunk，但未改依赖或拆 ECharts core。
3. 完整 format gate 仍需由拥有剩余 legacy/并行文件的 owner 修复；本轮没有越界格式化那些文件。
4. 浏览器级 25 FPS、60 秒交互、API P95、cache bytes 和真实 SSE reconnect 仍需 acceptance/performance owner 运行并产出 artifact。

## 5. 变更文件

本轮新增/修改的核心文件：

- `frontend/src/App.tsx`
- `frontend/src/auth/*`
- `frontend/src/components/layout/AppLayout.tsx`
- `frontend/src/components/layout/ForecastRiskCard.tsx`
- `frontend/src/features/simulation/forecast/ForecastStreamProvider.tsx`
- `frontend/src/features/simulation/integration/*`
- `frontend/src/features/simulation/realtime/HydraulicRealtimeProvider.tsx`
- `frontend/src/i18n/en.json`
- `frontend/src/i18n/zh.json`
- `frontend/src/pages/Dashboard.tsx`
- `frontend/src/pages/SimulationPage.tsx`
- `frontend/src/pages/SimulationPage.css`
- `frontend/src/test/setup.ts`

未提交、未推送。
