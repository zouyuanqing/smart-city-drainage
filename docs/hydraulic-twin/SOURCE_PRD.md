# 排水管网水力数字孪生与内涝推演引擎 PRD（源文档归档）

> 归档状态：原始需求基线，正文未经补充或改写；2026-09-24 再次核验来源。来源可核验不等于当前设计已批准；v1 与 v2 均未通过冻结审查。
>
> 来源：动态工作流草案 `C:\Users\zyq\Documents\opencode-pn\goal测试\.zcode\workflow-drafts\排水数字孪生-PRD-全链路交付.dwf.ts:146-158` 中的 `PRD_BRIEF`（`PRD_BRIEF` 声明开始于 146 行，结束表达式在 158 行）。
>
> 仓库基线：`ca99e04 chore: remove stray isort diff log`。整改轮执行 `git -C "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage" rev-parse --short=8 HEAD`，输出 `ca99e048`；契约统一引用 8 位短名 `ca99e04`。
>
> 审查处置：2026-09-24 的 v1、v2 冻结设计均未获批准。本归档继续作为不可变需求输入；整改后的产品语义见 `PRD_SUPPLEMENT.md`，可执行技术契约见 `IMPLEMENTATION_CONTRACT.md`，`contracts/*.md` 仅为已被取代的 supporting contracts，不得覆盖两份候选基线。
>
> 边界：以下正文逐条保留源 PRD。自行补充的产品决策、异常语义、性能口径和验收标准见 `PRD_SUPPLEMENT.md`；可执行技术契约见 `IMPLEMENTATION_CONTRACT.md`。

# 排水管网水力数字孪生与内涝推演引擎 PRD 执行摘要

技术约束：Python 3.11、FastAPI、SQLAlchemy async、Alembic、TimescaleDB、Redis、React 18、TypeScript、Leaflet、ECharts、Zustand；不新增数据库产品，不要求 GPU。

FR-1 管网数据层：支持 PostGIS 与无 PostGIS 的 WKT/GeoJSON 降级路径；表覆盖 networks、network_nodes、pipe_segments、catchments、可选 elevation_profiles；导入 SWMM inp、GeoJSON，导出 GeoJSON 与可回跑 inp；校验悬空节点、孤立子图、逆坡、重复管段、缺失坐标、环路并输出分级报告；CRUD+bbox；生成绑定现有上海设备的树环混合种子。

FR-2 降雨：1/2/3/5/10/20/50/100 年重现期，1/5/15 分钟步长；自定义不超过 72h；可复现随机雨型；情景 CRUD/复制；抽象 RainfallSource，仅内置合成源。

FR-3 仿真：优先 EPA SWMM 适配，必须有 Windows 可运行的纯 Python 运动波/扩散波与简化地表积水降级求解器；队列支持提交、0-100% 进度、取消、10 分钟超时、最多两次重试、幂等、并发 2、崩溃恢复；沙箱临时目录、20MB 上限、路径穿越防护与清理；节点/管段/积水按不超过 5 分钟帧序列化；TimescaleDB 90 天保留；RMSE/NSE 率定与预测实测查询。

FR-4 预测告警：默认每 10 分钟 nowcast，0-2h、5 分钟步长；新增 flood_forecast 与 pipe_surcharge；告警含目标、预计时刻、lead_time_minutes、深度、置信度、simulation_id；进入告警中心、SSE、通知并有预计/已发生视觉区分；Top-N 风险；默认 30 分钟事件窗抑制。

FR-5 API：/api/networks CRUD/import/validate/export/seed；rainfall-scenarios CRUD；simulations 提交查询取消结果率定实测比对；forecast latest/horizon/risk-ranking；SSE 新 forecast 事件。写操作沿用 JWT admin/operator，错误格式和 Swagger 保持兼容。

FR-6 前端：/simulation 路由菜单权限 i18n；Leaflet 管网/节点/汇水区与充满度色带；淹没面时间轴播放暂停拖拽 1/2/4 倍速；降雨编辑器；任务进度/历史/取消；A/B 对比；预测实测与不确定性带；Dashboard forecast 卡片和风险榜；万级管段千级多边形至少 25fps，需抽稀、视口裁剪、Canvas 或预渲染。

FR-7 部署数据：PostGIS/Timescale 镜像或明确安装方案、无 PostGIS 降级、仿真 worker、健康检查、一键 compose；Alembic 与 docker/init-db.sql 结果一致；pydantic-settings 管理并发、调度、预报、保留期和求解器。

非功能：5000 节点/10000 管段 24h 仿真在 4 核纯 Python 不超过 180s；nowcast 到告警不超过 3 分钟；新增 API P95 不超过 500ms；仿真故障隔离、可重跑、迁移可回滚、导入白名单、无任意文件读写；Prometheus 暴露任务时长/失败率/队列深度；日志含 simulation_id；现有 API/SSE/告警/页面零回归。

AC-1 合法 inp/GeoJSON 导入计数正确且有校验报告。AC-2 非法、超大、恶意文件明确拒绝且服务稳定。AC-3 50 年一遇 24h 仿真进度、结果、地图时间轴可回放。AC-4 nowcast 产生含提前量的 flood_forecast，进入告警中心与 SSE 且窗口内不重复。AC-5 率定相对默认参数有量化改善并有报告。AC-6 A/B 指标差异可查。AC-7 pytest/vitest/build/后端启动全过且旧测试零回归。AC-8 全新 docker compose 可复现 README 演示。
