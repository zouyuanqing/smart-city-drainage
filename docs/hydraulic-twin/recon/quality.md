# 测试与验收现状侦察

- 侦察对象：`smart-city-drainage`，基线提交 `ca99e04`（`git log -1 --oneline` 输出）。
- 侦察时间：2026-09-24。
- 范围：实际代码、测试、CI、构建、部署、备份、可观测性、性能与旧接口/SSE/告警回归风险。
- 方法：只读检查；没有把仓库内 `.trae/specs/**/checklist.md` 的勾选当作事实。代码结论均来自实际文件；命令结论均来自本次执行。

## 0. 结论摘要

当前仓库是“智慧城市神经末梢”设备/传感器/视频/AI 告警系统，不是水力数字孪生实现。水力核心关键词（`hydraulic`、`simulation`、`rainfall`、`forecast`、`pipe_segment`、`network_nodes`、`catchment`、`swmm`、`inp`、`GeoJSON`、`WKT`、`nowcast`、`NSE`、`RMSE`、`calibrat`）在代码/配置中没有实现命中；仅在初始化 SQL 的 `users`/`devices` 插入语句中出现无关的 `users` 等词。执行：

```text
$ grep -RInE 'hydraulic|simulation|rainfall|forecast|pipe_segment|network_nodes|network|catchment|swmm|\.inp|GeoJSON|WKT|nowcast|NSE|RMSE|calibrat' backend frontend docker scripts .github --exclude='package-lock.json' --exclude-dir='node_modules' --exclude-dir='dist' --exclude-dir='__pycache__' || true
backend/alembic/versions/0001_initial.py:237: ... INSERT INTO users ...
docker/init-db.sql:170: ... INSERT INTO users ...
docker/init-db.sql:177: ... INSERT INTO devices ...
```

因此，当前基线不能直接证明 PRD 的 FR-1～FR-7 或 AC-1～AC-6；质量工作首先应建立水力领域的测试/契约基线，而不是把现有 14 个后端测试和 8 个前端测试当作水力验收。

当前可复用基础设施有一定价值，但质量闸门并未形成闭环：

- 后端 pytest 在临时 Python 3.14.4 环境中 `14 passed`，覆盖率仅 `48%`；没有在目标 Python 3.11 上运行。
- 前端 `npm run lint` 与 `npm run build` 通过，但 `npm run format:check` 失败（36 个文件），`npm test` 仅 `6/8` 通过；`test:coverage` 因缺少 `@vitest/coverage-v8` 直接失败。
- 后端 Black/isort/flake8 通过；mypy 发现 30 个错误，而 CI 对 mypy 使用 `continue-on-error: true`，所以类型错误不会阻止合并。
- 后端真实启动检查失败：`RotatingFileHandler("logs/app.log")` 的父目录不存在，`Application startup failed`。
- `docker compose config --quiet` 语法检查通过（仅有 `version` 过时警告）；`docker compose up -d`、两项 CI Docker build 均因本机 Docker daemon 未运行而未执行成功。
- Prometheus `/metrics` 可返回默认 HTTP/Python 指标，但没有仿真任务时长/失败率/队列深度/SSE/推理指标；无仿真实现可观测。
- 备份/恢复脚本能通过 Shell/PowerShell 语法解析，但未做真实备份/恢复；脚本没有校验和、加密、保留策略、回滚或恢复后验收。

## 1. 实际测试资产与覆盖边界

### 1.1 后端 pytest

实际发现的测试只有以下 6 个测试模块文件（`find backend/tests ...`）：

- `backend/tests/conftest.py`
- `backend/tests/test_api/test_health.py`
- `backend/tests/test_api/test_auth.py`
- `backend/tests/test_api/test_devices.py`
- `backend/tests/test_services/test_mock_generator.py`
- `backend/tests/test_services/test_sse_manager.py`

测试内容的实际范围很窄：健康/系统状态、登录成功/失败/缺字段、设备/告警/最新传感器响应、模拟设备字段、SSE 单例/连接/广播。`backend/tests/test_api/test_health.py:3-17` 只断言状态码与响应键；`backend/tests/test_api/test_devices.py:3-26` 只断言列表存在；没有水力导入、仿真生命周期、预测告警、率定、A/B、恶意文件或性能场景。

#### 已执行命令

第一次直接执行 CI 形式命令时，主机 Python 缺少测试依赖：

```text
$ cd backend && python -m pytest tests/ -v --tb=short
ImportError while loading conftest ... No module named 'pytest_asyncio'
```

随后在仓库外临时 venv 补齐 `pytest-asyncio`、`aiosqlite`、`prometheus-fastapi-instrumentator`、`python-json-logger` 后，以相同 pytest 参数运行：

```text
$ cd backend && /tmp/scn-quality-venv/Scripts/python.exe -m pytest tests/ -v --tb=short
14 passed, 1 warning in 20.87s
```

这证明的是当前主机 Python 3.14.4 + 临时依赖下的单元测试通过，不是 Python 3.11/CI 镜像通过。主机检查结果为 `Python 3.14.4`，目标 3.11 未安装/未运行。

覆盖率命令：

```text
$ cd backend && /tmp/scn-quality-venv/Scripts/python.exe -m pytest tests/ --cov=app --cov-report=term-missing
14 passed, 1 warning
TOTAL 2210 statements, 1152 missed, 48%
```

低覆盖模块包括 `routes.py` 34%、`inference_service.py` 29%、`influxdb_service.py` 26%、`stream_service.py` 28%、`inference_worker.py` 23%。CI 的 pytest 命令没有 `--cov` 或覆盖率阈值，见 `.github/workflows/ci.yml:32-51`。

#### 测试夹具存在“看似隔离、实际未隔离”的风险

`backend/tests/conftest.py:12-31` 创建了独立 SQLite engine/session，但 `backend/tests/conftest.py:33-42` 将 `get_db_session` 放入 `app.dependency_overrides`。实际路由直接调用 `get_db_session()` 上下文管理器（例如 `backend/app/api/routes.py:93-126`、`:731-800`、`:899-945`），不是通过 `Depends(get_db_session)` 注入，因此这个 override 不会替换这些直接调用。测试通过很大程度上依赖数据库失败后的 mock/fallback 分支，而不是实际 ORM 读写。

这会造成以下假绿：

- 数据库断开时，`get_devices` 在 `backend/app/api/routes.py:899-945` 捕获异常并返回 `PRESET_DEVICES`。
- 告警查询在 `backend/app/api/routes.py:725-800` 捕获异常并生成随机 mock 告警。
- 登录在 `backend/app/api/routes.py:112-126` 仍有硬编码 `admin/Admin@123456` fallback。
- `test_system_status` 只检查键存在，不检查 PostgreSQL/InfluxDB/Redis/Model 状态正确。

应将路由改为真正可替换的 repository/session dependency，或在测试中使用真实 PostgreSQL/Timescale 容器并禁止生产 fallback；否则 CRUD、事务、迁移和错误契约均未被验证。

### 1.2 前端 Vitest

`frontend/vitest.config.ts:18-23` 配置了 `jsdom`、setup 和 CSS；实际只有：

- `frontend/src/test/__tests__/LoginPage.test.tsx`
- `frontend/src/test/__tests__/useAppStore.test.ts`
- `frontend/src/test/setup.ts`

`frontend/src/test/setup.ts:1` 只有 `import '@testing-library/jest-dom'`，没有 localStorage、EventSource、ResizeObserver、Leaflet、fetch/axios mock。`LoginPage.test.tsx:21-24` 每个测试直接调用 `localStorage.clear()`。

安装锁定依赖后执行：

```text
$ cd frontend && npm ci
# exit 0

$ cd frontend && npm test
Test Files 1 failed | 1 passed (2)
Tests      2 failed | 6 passed (8)
TypeError: localStorage.clear is not a function
```

失败位置是 `frontend/src/test/__tests__/LoginPage.test.tsx:24`。当前主机为 Node `25.9.0`，CI 固定 Node 20（`.github/workflows/ci.yml:57-61`），所以不能直接把本机结果断言为 Node 20 必现；但测试环境隔离缺失是实际代码风险，应在 CI 的 Node 20 上补跑并固定 jsdom/localStorage 策略。

其他前端命令：

```text
$ cd frontend && npm run lint
# exit 0

$ cd frontend && npm run build
✓ 4102 modules transformed
✓ built in 12.62s
(!) Some chunks are larger than 1000 kB after minification.
dist/assets/echarts-*.js  1,034.95 kB
 dist/assets/antd-*.js      693.76 kB
 dist/assets/index-*.js     831.14 kB

$ cd frontend && npm run format:check
# exit 1
[warn] 36 files
Code style issues found in 36 files.
```

`frontend/package.json:14-16` 声明了 `test:coverage`，但 `frontend/package.json:38-60` 没有 `@vitest/coverage-v8`：

```text
$ cd frontend && npm run test:coverage
MISSING DEPENDENCY Cannot find dependency '@vitest/coverage-v8'
```

没有 API service mock 测试、SSE hook 测试、地图/大数据渲染测试、权限/i18n 测试或 A/B/时间轴测试；`.trae` 文档中声称的测试不作为本报告证据。

### 1.3 e2e、性能与负载测试

实际文件扫描结果：

```text
$ find ... | grep -Ei '(^|/)(e2e|playwright|cypress)(/|\.|$)'
e2e/playwright/cypress files: none

$ find ... | grep -Ei '(^|/)(perf|performance|benchmark|load|locust|k6|jmeter)(/|\.|$)'
performance/load benchmark files: none
```

`frontend/package.json:38-60` 也没有 Playwright/Cypress、k6/Locust/JMeter 或浏览器性能依赖。因此 AC-3 的 50 年一遇 24h 回放、AC-4 nowcast→SSE/告警、AC-6 A/B、旧页面回归和“万级管段/千级多边形至少 25fps”均没有自动化验收资产。

## 2. CI 当前状态与缺口

`.github/workflows/ci.yml` 现有 job：

- `backend-lint`：Black/isort/flake8/mypy，见 `:9-30`；mypy 明确 `continue-on-error: true`。
- `backend-test`：Python 3.11、`requirements-cpu.txt`、额外安装 `aiosqlite`、pytest，见 `:32-51`。
- `frontend-lint`：Node 20、npm ci、lint、format check，见 `:53-68`。
- `frontend-test`：Node 20、npm test，见 `:70-83`。
- `docker-build`：仅执行两个 `docker build`，见 `:85-93`。

本次按 CI 语义执行的静态检查：

```text
black --check .        -> 36 files unchanged
isort --check-only .   -> exit 0（Skipped 1 files）
flake8 app/            -> exit 0
mypy app/ --ignore-missing-imports -> 30 errors in 10 files
```

mypy 错误覆盖 `stream_service.py` 未 await 的协程、`routes.py` 访问不存在的 `StreamService.streams`、`system_status.py` 导入不存在的 `influxdb_client`、InfluxDB 客户端类型、Redis 返回类型等；例如 `backend/app/services/stream_service.py:113-121`、`:150-166`，`backend/app/api/routes.py:352-386`。这些不是未来水力代码的风格问题，而是现有 API/worker 回归风险。

CI 缺口：

1. 没有后端启动/lifespan 检查；pytest 的 ASGITransport 默认不启动 lifespan，所以没有覆盖日志目录、数据库、Redis、InfluxDB、模拟生成器启动。
2. 没有 `alembic upgrade head`、`alembic current`、迁移回滚、迁移与 `init-db.sql` parity 检查。
3. 没有 `docker compose up`、依赖健康、API/SSE/WebSocket 冒烟、备份/恢复 smoke test。
4. 没有 e2e、覆盖率阈值、性能/负载/浏览器帧率、仿真故障注入测试。
5. 前端 job 没有独立 `npm run build`；构建仅在 Docker build 阶段间接覆盖。
6. CI 没有固定镜像 digest、锁文件审计或 Python 3.11 本地复现门禁。

本次 Docker 检查：

```text
$ docker compose config --quiet
# exit 0；warning: attribute `version` is obsolete

$ docker compose config --services
redis
influxdb
postgres
backend
nginx

$ docker build -f docker/backend/Dockerfile.cpu -t backend-test ./backend
ERROR: failed to connect to the docker API ... DockerDesktopLinuxEngine ... daemon is not running

$ docker build -f docker/frontend/Dockerfile -t frontend-test ./frontend
ERROR: failed to connect to the docker API ... daemon is not running

$ docker compose up -d
ERROR: failed to connect to the docker API ... daemon is not running
```

所以 Docker 镜像、PostGIS/Timescale 初始化、SSE 长连接和全新 compose 验收本次均未通过执行；不能把 CI 文件存在等同于流水线绿色。

## 3. 后端启动与运行回归风险

### 3.1 启动被日志目录阻断

`backend/app/main.py:43-73` 的 `setup_logging()` 创建 `RotatingFileHandler("logs/app.log")`，但 `backend/app/main.py:119-120` 在 lifespan 中无保护地调用它；`docker/backend/Dockerfile.cpu:20` 只创建 `media/hls`、`media/screenshots`、`models/weights`，没有创建 `logs`。

本次真实启动检查：

```text
$ cd backend && timeout 20s /tmp/scn-quality-venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --log-level warning
FileNotFoundError: ... backend/logs/app.log
Application startup failed. Exiting.
```

这直接阻塞 AC-7 的“后端启动全过”和 AC-8 的 compose 演示。应在代码层保证日志目录/可写路径并把 liveness/readiness 分开。

### 3.2 健康检查不是依赖就绪检查

- `backend/app/main.py:302-305` 的 `/health` 无条件返回 `{"status": "healthy"}`。
- `backend/app/api/routes.py:69-80` 的 `/api/health` 也不检查数据库/Redis/InfluxDB。
- `docker/backend/Dockerfile.cpu:22-23` 的 healthcheck 只执行 `httpx.get`，没有 `raise_for_status`，HTTP 500 也会以进程成功退出。
- `docker-compose.yml:125-127` 让 Nginx 依赖这个 backend healthcheck，因此可能在依赖未就绪时仍被判为可用。

### 3.3 现有 worker/API 缺陷会污染水力 worker 的复用

- `backend/app/services/stream_service.py:113-121`、`:150-166` 调用 `add_worker/remove_worker/stop_all` 时没有 `await`；`backend/app/api/routes.py:363-384` 也有同类问题。mypy 已报告 `unused-coroutine`。
- `backend/app/api/routes.py:359-360` 访问 `stream_service.streams`，而实现字段是 `backend/app/services/stream_service.py:65` 的 `_streams`。本次带 admin JWT 的 ASGI 探针实际抛 `AttributeError: 'StreamService' object has no attribute 'streams'`，请求未得到正常业务响应。
- `backend/app/services/system_status.py:18-34` 导入 `influxdb_client`，但实际模块导出的是 `influxdb_service`（`backend/app/services/influxdb_service.py:215`）；本次 `/api/system/status` 探针返回 `influxdb.status=not_configured`、`detail=influxdb_service module not found`。
- `backend/app/api/routes.py:112-126` 保留硬编码管理员 fallback；这与真正的数据库认证/零回归要求冲突，也使测试在数据库故障时仍然“成功”。

这些问题应在新增水力 worker 前建立旧 API contract test，否则很容易把 SSE、worker 生命周期和数据库故障处理带入新仿真队列。

## 4. 数据库、迁移与部署一致性

### 4.1 Alembic 与 init SQL 表集合不一致

本次静态比较脚本输出：

```text
migration_tables ['alerts', 'camera_streams', 'devices', 'inference_results', 'model_versions', 'users']
init_tables      ['alerts', 'camera_streams', 'devices', 'inference_results', 'model_versions', 'sensor_readings', 'users']
only_init        ['sensor_readings']
only_migration   []
```

证据位置：

- `backend/alembic/versions/0001_initial.py:20-228` 创建 6 张表，没有 `sensor_readings` hypertable。
- `docker/init-db.sql:100-117` 创建并转换 `sensor_readings` 为 hypertable。
- `backend/app/core/database.py:137-142` 启动时使用 `Base.metadata.create_all`，而 `backend/app/models/db_models.py` 也没有 SensorReading ORM 表。

`alembic heads` 本次成功输出 `0001_initial (head)`；`alembic current` 因无 PostgreSQL 连接失败（`socket.gaierror`/连接不可用）。没有执行真实 upgrade/downgrade，因此 AC-8 的迁移一致性仍未验证。

### 4.2 PostGIS 依赖未声明

`docker/init-db.sql:76-79` 创建使用 `ST_SetSRID`、`ST_MakePoint` 的 GIST 空间索引，但该文件和 `backend/alembic/versions/0001_initial.py:20-24` 都只启用 TimescaleDB 与 uuid-ossp，没有 `CREATE EXTENSION postgis`。Compose 使用 `timescale/timescaledb:latest-pg15`（`docker-compose.yml:9-21`），而不是明确包含 PostGIS 的镜像。当前无法在无 Docker daemon 环境中启动验证；这必须作为全新 compose 的阻断性检查，而不能按文档描述推定可用。

同时，ORM 设备只有 `latitude/longitude` 浮点字段（`backend/app/models/db_models.py:84-117`），没有 PostGIS geometry，也没有无 PostGIS 的 WKT/GeoJSON 降级实现。

### 4.3 Compose/初始化没有闭环

- `docker-compose.yml:48-64` 仅设置 InfluxDB retention 环境变量；`docker/influxdb-setup.sh:8-33` 虽有 downsampling 脚本，但 Compose 没有挂载/执行该脚本，因此不能证明任务已创建。
- `docker-compose.yml:144-177` 的 Prometheus/Grafana 处于 profile，默认 `docker compose config --services` 不包含它们；没有发现 Grafana dashboard JSON 或 alert rules。
- `docker-compose.yml:35` Redis 开启 `--requirepass`，但 `:42-45` healthcheck 使用未带认证的 `redis-cli --raw incr ping`；这与当前 Redis 启动参数不一致，需在真实 compose 中验证并修正。
- `docker-compose.yml:73-109` 没有独立仿真 worker、队列、恢复扫描或健康/就绪分层；只有一个 backend 进程。
- GPU Dockerfile `docker/backend/Dockerfile:39` 使用 4 个 Uvicorn workers，而 `backend/app/services/sse_manager.py:32-50` 和 `stream_service.py:64-68` 使用进程内单例/字典；多 worker 会分裂 SSE 客户端、流和 worker 状态，不能直接作为未来仿真/SSE 的部署模式。

## 5. 性能现状

### 5.1 构建体积

前端生产构建通过，但输出有明显体积警告：ECharts chunk 1,034.95 kB、Ant Design 693.76 kB、主 index 831.14 kB。`frontend/vite.config.ts:46-60` 虽有 manual chunks 和 `chunkSizeWarningLimit: 1000`，没有 bundle budget、动态导入或按路由拆分的验收。

### 5.2 地图与大数据路径

当前地图只渲染设备点：

- `frontend/src/components/map/MapVisualization.tsx:285-294` 对 `devices` 全量 `map` 为每个设备创建 React-Leaflet `Marker`。
- `:42-122` 每个 marker 独立创建 DOM `DivIcon`；没有 viewport culling、抽稀、Canvas/WebGL 或预渲染。
- `frontend/src/pages/MapView.tsx:26-51` 只请求设备列表和 SSE 传感器事件，没有管网/管段/汇水区图层。
- `frontend/src/pages/Dashboard.tsx:362-375` 同一套全量标记也嵌入 Dashboard。

因此当前代码无法证明 PRD 的“万级管段/千级多边形至少 25fps”；也没有以 4 核 CPU 测量 5000 节点/10000 管段/24h 仿真的 180s 上限。没有找到 k6/Locust/JMeter/pytest-benchmark/浏览器性能脚本（扫描输出 `performance/load benchmark files: none`）。

### 5.3 需要新增的性能验收

建议把性能作为独立、可重复的流水线 job，而不是手工观察：

1. 固定 5000 节点/10000 管段/1000 多边形 fixture，记录输入校验、求解、序列化、API P95。
2. 4 核 CPU、纯 Python solver、24h/5min 帧的 wall time、峰值内存、取消/超时/重试。
3. k6/Locust 验证 API P95、队列深度、并发 2、SSE 断线重连。
4. Playwright trace + 浏览器 performance API 验证 1/2/4 倍速、25fps、视口裁剪/抽稀。
5. bundle-size budget 和冷启动/首屏指标。

## 6. 可观测性现状

### 6.1 已有能力

- `backend/app/main.py:214-218` 接入 `prometheus-fastapi-instrumentator` 并暴露 `/metrics`。
- `backend/app/main.py:43-73` 计划输出 JSON 日志并轮转 10MB × 10 个文件。
- `backend/app/main.py:240-258` 添加 `X-Process-Time` 与 `X-Request-ID` 响应头。

本次无 lifespan 的 ASGI 探针得到：

```text
GET /metrics -> 200
content-type: text/plain; version=1.0.0
has_http_requests True
metric_lines 35
simulation: False
queue: False
task: False
sse: False
inference: False
```

### 6.2 缺口与风险

- 没有 `simulation_duration`、`simulation_failures_total`、`queue_depth`、取消/重试/超时、solver 版本、nowcast 延迟、forecast 事件等指标；`docker/prometheus/prometheus.yml:4-9` 只配置了一个 backend scrape target，没有 rules/alerting。
- 请求 ID 只写回响应头，没有 context/日志字段关联；PRD 要求的日志含 `simulation_id` 当前完全不存在。
- `setup_logging` 父目录缺失导致启动失败，且 lifespan 中未捕获。
- `/api/system/status` 的 InfluxDB 导入错误会把“已配置但未连接”误报为 `not_configured`；状态探针本次实际返回该错误。
- 没有 Grafana dashboard 文件；`.trae` 文档的 dashboard 勾选未作为事实。

建议至少定义：任务状态 gauge、队列深度 gauge、耗时 histogram、失败/重试/取消 counter、solver/forecast 版本 label、`simulation_id` 结构化字段、liveness/readiness 分离，以及 Prometheus alert rules。

## 7. 备份、恢复与数据安全

### 7.1 已有脚本

- `scripts/backup.sh:15-45`：PostgreSQL `pg_dump`、InfluxDB `influx backup`、Redis `BGSAVE`/RDB，并压缩 tar.gz。
- `scripts/restore.sh:20-49`：解包后分别恢复 PostgreSQL、InfluxDB、Redis。
- `scripts/backup.ps1:12-30`：Windows 版本执行同类操作。

本次仅做语法检查：

```text
$ bash -n scripts/backup.sh && bash -n scripts/restore.sh
shell syntax ok
$ pwsh ... [scriptblock]::Create((Get-Content -Raw scripts/backup.ps1))
powershell syntax ok
```

真实 backup/restore 未运行（没有运行中的 Compose 容器/Docker daemon），因此不能宣称恢复可用。

### 7.2 备份缺口

- `backup.sh:17-18` 将 stderr 丢弃，脚本没有检查归档完整性、校验和、加密、对象存储复制或失败回滚。
- `backup.ps1:14-15` 启动 `pg_dump` 后只打印路径，没有检查 `$process.ExitCode`；且没有生成 `.tar.gz`，而 `restore.sh:5-10` 只接受 `.tar.gz`，Windows 备份与现有恢复脚本不兼容。
- 没有定时调度、保留/清理策略、异地副本、恢复演练或“恢复后 API/数据校验”步骤。
- 当前备份只覆盖 PostgreSQL/InfluxDB/Redis；没有模型权重、仿真输入/结果、告警派生数据、配置版本或 Alembic 版本清单。
- `restore.sh:21-24` 直接解包用户提供的归档，没有签名/哈希/路径白名单/防符号链接检查；没有失败时的事务边界或回滚。

建议使用一次性隔离 Compose 做 backup→删除→restore→行数/哈希/API 冒烟的 CI job，并记录备份 manifest、版本、校验和及恢复耗时。

## 8. 旧 API/SSE/告警回归风险清单

| 优先级 | 风险 | 实际证据 | 对水力孪生的影响 |
|---|---|---|---|
| P0 | 水力领域完全缺失 | OpenAPI 实际仅 29 条旧系统路径；无 networks/rainfall/simulations/forecast | FR-1～FR-5、AC-1～AC-6 无实现/无验收 |
| P0 | 后端无法启动 | `main.py:58-63` 写 `logs/app.log`，目录不存在；实测 `Application startup failed` | AC-7/AC-8 阻断 |
| P0 | Compose 初始化/健康未闭环 | Redis healthcheck 无密码、PostGIS 函数未声明 extension、init SQL 与 Alembic 表不一致 | 全新部署、数据层、回滚均不可信 |
| P1 | 测试假绿 | `conftest.py:33-42` override 不影响路由直接 `get_db_session()`；fallback mock 掩盖 DB 故障 | 新 API 可能沿用静默降级，破坏数据契约 |
| P1 | 前端质量门禁不绿 | `npm test` 6/8；`format:check` 36 文件失败；coverage 依赖缺失 | AC-7 旧页面零回归无法证明 |
| P1 | 流推理 API 直接异常 | `routes.py:359-360` 访问不存在的 `stream_service.streams`；实测 AttributeError | 旧视频/告警链路回归；worker 复用会放大问题 |
| P1 | worker 生命周期未 await | `stream_service.py:113-121,150-166`、`routes.py:363-384`；mypy 30 errors 中多项为 unused-coroutine | 仿真 worker 的启动/停止/恢复模式不能直接复制 |
| P1 | 系统状态误报 | `system_status.py:20` 导入不存在的 `influxdb_client`；实测 `not_configured` | 监控、故障隔离、告警判断不可信 |
| P1 | 前端/后端流响应契约不一致 | 后端 `get_all_streams_status()` 返回 list（`stream_service.py:349-352`）；前端 `api.ts:124-136` 声明 `Record`，`VideoMonitor.tsx:24-35` 过滤 `is_active`，而服务返回 `is_healthy` | 旧页面可能显示空流；未来时间轴/视频回归无基线 |
| P1 | SSE 仅进程内历史/有界队列 | `sse_manager.py:35-43,64-76,90-120`；队列满丢事件并断开 | 多 worker/重启/断线时 forecast 事件不可重放；无 forecast 事件类型 |
| P2 | 认证 fallback 与默认秘密 | `routes.py:112-126` 硬编码 admin；`.env.example:18-21` 仅模板但 compose/README 有固定秘密 | 旧 API 写操作权限和部署安全需先锁定 |
| P2 | 迁移/初始化双源 | `alembic/versions/0001_initial.py` 与 `docker/init-db.sql` 表集合不同 | 水力表扩展会继续产生 schema drift |

## 9. PRD 验收矩阵（本次状态）

| AC | 目标 | 本次状态 | 依据/缺口 |
|---|---|---|---|
| AC-1 | 合法 inp/GeoJSON 导入计数与分级校验报告 | **未实现/未运行** | OpenAPI 无 import/validate；无 hydraulic/import 代码命中 |
| AC-2 | 非法、超大、恶意文件拒绝且服务稳定 | **未实现/未运行** | 无文件白名单、大小限制、路径穿越测试；现有模型上传也未纳入水力验收 |
| AC-3 | 50 年一遇 24h 仿真、进度、结果、地图回放 | **未实现/未运行** | 无 simulation/router/worker/frame schema；无 `/simulation` 前端路由（`frontend/src/App.tsx:35-99`） |
| AC-4 | nowcast→flood_forecast→告警/SSE，提前量与抑制 | **未实现/未运行** | 无 forecast 表、路由、事件、lead_time、抑制逻辑 |
| AC-5 | RMSE/NSE 率定改善报告 | **未实现/未运行** | 无 calibration/RMSE/NSE 代码或测试 |
| AC-6 | A/B 指标差异可查 | **未实现/未运行** | 无 A/B scenario/simulation comparison；前端仅告警合并去重 |
| AC-7 | pytest/vitest/build/后端启动全过、旧测试零回归 | **部分且未通过完整门禁** | pytest 14 pass（Python 3.14 临时环境）；前端 test 6/8、format fail、coverage fail；build pass；真实 startup fail；Python 3.11/Node 20 未跑 |
| AC-8 | 全新 docker compose 可复现 README 演示 | **未验证/本机阻断** | compose config 仅语法通过；`up`/build 因 Docker daemon 未运行失败；迁移、PostGIS、备份恢复均未跑 |

## 10. 可复用模式（复用前需修正）

1. **CI 分层骨架**：`.github/workflows/ci.yml` 已有 backend/frontend/docker job，可扩展为 unit、contract、migration、e2e、performance、compose-smoke；但应取消 mypy 的 continue-on-error，增加 build 与启动 job。
2. **pytest fixture 结构**：`backend/tests/conftest.py:12-31` 的 session engine/session 生命周期可复用，但必须改成真正替换 repository/session，并在集成 job 使用真实 Postgres/Timescale。
3. **异步 SQLAlchemy 生命周期**：`backend/app/core/database.py:57-129` 的 engine/session factory/dispose 结构清晰，可作为水力 repository 基线；需要显式 SQLite/PostGIS/WKT dialect 分支。
4. **Prometheus Instrumentator**：`backend/app/main.py:214-218` 可保留，再增加任务/队列/forecast 自定义指标和 rules。
5. **SSE 重连骨架**：`backend/app/services/sse_manager.py:52-76,142-189` 与 `frontend/src/hooks/useSSE.ts:47-132` 已有 Last-Event-ID、指数退避、心跳；必须改为持久化事件日志/跨进程 broker，并为 forecast 定义不可变事件契约。
6. **Zustand 有界历史**：`frontend/src/store/useAppStore.ts:78-89` 每设备保留 30 条，可作为实时读数缓存模式；时间轴帧应独立分页/缓存，不应把全量结果塞入全局数组。
7. **ECharts sampling**：`frontend/src/components/charts/WaterLevelChart.tsx:111-149` 使用 `sampling: 'lttb'`，可复用到不确定性带/趋势图；大地图仍需抽稀、视口裁剪和 Canvas/预渲染。
8. **Compose 分层 profile**：`docker-compose.yml:129-177` 的 production/monitoring profile 可复用，但必须加入 worker、readiness、migration job、PostGIS 镜像/降级路径和备份 job。
9. **备份脚本形态**：`scripts/backup.sh:15-45` 可作为数据源清单起点，但必须补 manifest、校验、加密、版本、恢复验证和 Windows 统一归档格式。

## 11. 建议的最小验收闸门（按顺序）

1. **先锁定基线**：在 Python 3.11 + Node 20 + PostgreSQL/Timescale（PostGIS 与无 PostGIS 两套）运行当前测试；修复 `localStorage`、format、coverage 依赖和日志目录后再宣称旧测试绿色。
2. **建立 schema contract**：以 Alembic 为唯一 schema source，移除/生成 init SQL；增加 upgrade→downgrade→upgrade、SQL parity、表/索引/扩展检查。
3. **建立水力 fixture**：最小合法/非法 inp、GeoJSON、WKT、网络/节点/管段/汇水区/高程、50 年 24h 雨型、可复现随机种子；先做导入白名单、大小、路径和报告 schema 测试。
4. **建立 solver contract**：纯 Python 运动波/扩散波与地表积水降级、帧序列化、取消/超时/重试/幂等/崩溃恢复；每个测试带 `simulation_id` 和确定性 golden fixture。
5. **建立 API/SSE contract**：OpenAPI snapshot、错误格式、JWT 角色、flood_forecast/pipe_surcharge 字段、提前量、Top-N、30 分钟抑制、断线重放；覆盖多进程/重启场景。
6. **建立前端 e2e**：登录/权限、`/simulation`、导入校验报告、进度/取消/历史、1/2/4 倍速回放、A/B、预测实测/不确定性带、Dashboard forecast 卡片和风险榜。
7. **建立性能门禁**：4 核纯 Python 180s 上限、nowcast 3 分钟、API P95 500ms、万级管段/千级多边形 25fps；输出机器可读报告和趋势。
8. **建立可观测性/恢复门禁**：Prometheus 指标+alerts、结构化日志、liveness/readiness、备份→销毁→恢复→数据/API 校验；每次发布留存验收报告。

## 12. 本次未运行/不能宣称通过的项目

- GitHub Actions 远端运行：未运行；只执行了本地对应命令。
- Docker 镜像构建、`docker compose up`、真实 PostGIS/Timescale/Redis/InfluxDB：因 Docker daemon 不可用未运行。
- Python 3.11、Node 20 的目标矩阵：主机没有 Python 3.11/Node 20，未运行。
- 真实 e2e、性能/负载、仿真、nowcast、导入、率定、A/B：仓库无对应实现/脚本，未运行。
- 真实 backup/restore：只有语法检查，没有运行中的容器，未运行。
- Alembic `current/upgrade/downgrade`：只有 `heads` 成功；数据库不可用，未运行在线迁移。
