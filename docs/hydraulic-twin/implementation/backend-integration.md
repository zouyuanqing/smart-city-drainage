# 后端垂直切片集成实施摘要

- 模块：`backend-integration`
- 日期：2026-09-25
- 仓库：`smart-city-drainage`
- 状态：**生产 metrics wiring 已完成；应用进程可启动并真实响应 `/metrics`。全量 pytest 仍被 5 个已确认的 out-of-scope contract-gate 失败阻断，不能声明后端全量绿色。**

## 1. 规范输入与边界

本轮实际读取并按以下冻结输入实施：

- `docs/hydraulic-twin/SOURCE_PRD.md:16-24,30-32`：数据、降雨、仿真、nowcast/告警、API，以及 Prometheus 任务指标和零回归要求。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:120-125,164-179,188-214,231-251,383-400,453-480,489-560`：Alembic 单一 schema writer、durable job、forecast/outbox/通知、导入导出、率定和 API 错误契约。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1251-1297`：import/export 与 round-trip；`:1376-1405`：四类 durable job 状态机；`:2083-2107`：服务拓扑；`:2171-2203`：21 个 metric family 和结构化日志；`:2209-2318`：文件 ownership 与 integration handoff。
- `docs/hydraulic-twin/implementation/hydraulic-metrics.md:211-278`：明确要求 backend-integration 在 `Instrumentator` 初始化前导入并持有 `hydraulic_metrics` singleton，并用 `generate_latest(REGISTRY)` 或等价 HTTP scrape 回归。
- 真实应用与链路代码：`backend/app/main.py`、`backend/app/api/routes.py`、`backend/app/api/hydraulic_routes.py`、`backend/app/core/database.py`、`backend/app/core/redis_client.py`、`backend/app/services/sse_manager.py`、`backend/app/services/notification_service.py`、`backend/app/services/hydraulic/**`、`backend/app/workers/**`、ORM、Alembic 和现有测试。

仓库没有 `.codegraph/` 索引，因此按项目指令跳过 CodeGraph，使用真实文件读取与精确 `rg`/pytest 诊断。

## 2. 真实代码改动

### 2.1 生产 Prometheus collector wiring

- `backend/app/main.py:44` 在模块导入阶段导入 `hydraulic_metrics`；`Instrumentator(...)` 到 `backend/app/main.py:271-275` 才初始化。因此 collectors 在 Instrumentator 初始化前已注册到默认 `REGISTRY`。
- `backend/app/main.py:164-167` 在 lifespan 启动时将同一 singleton 放入 `app.state.hydraulic_metrics`，运行中的 FastAPI 应用明确持有它；`/metrics` 继续由同一个默认 registry 暴露。
- 未复制 collector、未创建第二个 registry、未把 `simulation_id` 等高基数 ID 加入 label，也未用测试 registry 替换生产 registry。

### 2.2 生产 wiring 回归测试

新增 `backend/tests/integration/test_backend_integration.py`：

- `:25-44` 用 AST 断言 singleton import 的源码行严格早于 `Instrumentator(...)`，直接保护 handoff 的顺序要求。
- `:47-55` 真实 `import app.main`，断言 `app.main.hydraulic_metrics.registry is REGISTRY`，再对 `generate_latest(REGISTRY)` 断言：
  - `simulation_queue_depth`
  - `simulation_duration_seconds_count`
  - `simulation_failures_total`
  - `simulation_retries_total`
  - `simulation_timeout_total`
  - `simulation_cancelled_total`
  - `simulation_active_slots`
- `:59-72` 通过真实 `httpx.ASGITransport(app=app)` 请求挂载的 `/metrics`，断言 HTTP 200、Prometheus text content type 和同一组任务指标；未 mock FastAPI、registry 或 collector。

### 2.3 Python 3.11 跨方言 schema 回归

- Python 3.11 全量测试首次暴露 `ResultRetentionPolicy.id` 的 PostgreSQL `server_default=uuid_generate_v4()` 会令 SQLite `Base.metadata.create_all()` 生成非法 DDL。
- `backend/app/models/hydraulic_models.py:2683-2697` 保留 SQLAlchemy `default=uuid.uuid4`，移除只适用于 PostgreSQL 的 ORM `server_default`。这不会削弱部署 schema：`backend/alembic/versions/0002_hydraulic_schema.py:2370` 仍定义 `DEFAULT uuid_generate_v4()`，本轮未修改 migration。
- 现有真实约束测试 `tests/test_hydraulic_schema.py::test_sqlite_cascade_path_preserves_legacy_tables` 覆盖 legacy 表保留、FK、cascade 和 constraint 行为；未改成 skip/xfail。

### 2.4 Python 3.11 SWMM timeout 测试稳定性

- 首次 Python 3.11 全量运行显示两个真实进程超时测试使用 10ms deadline，线程调度可让 deadline 在 probe 完成、进程启动前耗尽，导致“已启动进程必须被杀/cleanup error 必须回传”的断言未进入目标路径。
- `backend/tests/hydraulic/test_solver_contract.py:405,430` 将测试 deadline 调整为 100ms；生产 `SwmmAdapter`、超时错误码、进程树终止、无 frame/summary 发布和 cleanup diagnostics 全部未改，也没有弱化断言。
- 修复后 Python 3.11 明确验证：进程被杀、cleanup failure 写入 `SolverTimeoutError.details["cleanup_error"]`、sink 不发布 frame/summary。

## 3. 实际执行检查

### 3.1 TDD RED → GREEN：生产 `/metrics`

修复前执行：

```bash
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" \
  && .venv/Scripts/python.exe -m pytest tests/integration/test_backend_integration.py -q --tb=short
```

输出：

```text
FF
FAILED tests/integration/test_backend_integration.py::test_application_import_registers_hydraulic_metrics_singleton
FAILED tests/integration/test_backend_integration.py::test_metrics_endpoint_exposes_hydraulic_collectors
2 failed, 328 warnings in 0.13s
```

最终默认解释器执行同一测试，输出：

```text
...                                                                      [100%]
3 passed, 364 warnings in 0.03s
```

Python 3.11 执行同一测试：

```text
...                                                                      [100%]
3 passed, 1 warning in 0.03s
```

metrics + integration focused 组合曾执行：

```bash
/c/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 \
  --with-requirements requirements-py311.lock \
  python -B -m pytest tests/integration/test_backend_integration.py \
  tests/hydraulic/test_hydraulic_metrics.py -q --tb=short
```

输出：`21 passed, 1 warning in 0.07s`（加入 import-order 第三条断言前；最终两个文件为 22 个 case，均已分别/全量包含在后续运行中）。

### 3.2 应用 import 与 `generate_latest`

默认解释器命令：

```bash
.venv/Scripts/python.exe -c "import app.main as m; from prometheus_client import REGISTRY, generate_latest; text=generate_latest(REGISTRY).decode('utf-8'); assert m.hydraulic_metrics.registry is REGISTRY; assert 'simulation_queue_depth' in text; assert 'simulation_duration_seconds_count' in text; print('APP_IMPORT=PASS'); print('GENERATE_LATEST_HYDRAULIC=PASS')"
```

输出：

```text
APP_IMPORT=PASS
GENERATE_LATEST_HYDRAULIC=PASS
```

Python 3.11 命令：

```bash
/c/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 \
  --with-requirements requirements-py311.lock \
  python -B -c "import app.main; from prometheus_client import REGISTRY, generate_latest; text=generate_latest(REGISTRY).decode('utf-8'); assert 'simulation_queue_depth' in text; assert 'simulation_duration_seconds_count' in text; print('PY311_APP_IMPORT=PASS'); print('PY311_GENERATE_LATEST_HYDRAULIC=PASS')"
```

输出：

```text
PY311_APP_IMPORT=PASS
PY311_GENERATE_LATEST_HYDRAULIC=PASS
```

### 3.3 真实 lifespan 启动与 HTTP scrape

在临时目录运行真实 `TestClient(app)` lifespan（没有替换 DB/Redis/Influx/worker），进入 startup、请求 `/metrics`、退出 shutdown。命令和关键输出：

```text
LIFESPAN_STARTUP=PASS
APP_STATE_HYDRAULIC_METRICS=PASS
HTTP_METRICS_QUEUE=PASS
HTTP_METRICS_TASK_DURATION=PASS
```

该检查只证明**应用进程/lifespan 和 `/metrics` 可用**。本机没有运行 PostgreSQL、Redis、InfluxDB，真实日志为连接拒绝；因此没有把它解释成 production readiness 或真实 DB/worker E2E。

### 3.4 Alembic 图

```bash
.venv/Scripts/alembic.exe heads && .venv/Scripts/alembic.exe history
```

输出：

```text
0002_hydraulic_schema (head)
0001_initial -> 0002_hydraulic_schema (head), Hydraulic schema, spatial profiles, and result retention.
<base> -> 0001_initial, Initial migration — create all core tables.
```

这是静态单 head 检查；本轮没有运行 live PostgreSQL/PostGIS/Timescale migration。

### 3.5 Python 3.11 回归修复 focused

```bash
/c/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 \
  --with-requirements requirements-py311.lock python -B -m pytest \
  tests/hydraulic/test_solver_contract.py::test_fake_swmm_timeout_kills_process_without_publishing_frames \
  tests/hydraulic/test_solver_contract.py::test_swmm_timeout_reports_process_tree_cleanup_failure \
  tests/test_hydraulic_schema.py::test_sqlite_cascade_path_preserves_legacy_tables \
  -q --tb=short
```

输出：`3 passed, 1 warning in 0.53s`。

### 3.6 题目要求的全量 pytest（Python 3.11）

```bash
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" \
  && /c/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 \
  --with-requirements requirements-py311.lock python -B -m pytest -q
```

最终输出：

```text
5 failed, 466 passed, 4 skipped, 1 warning in 21.61s
```

5 个失败全部是同一个 `tests/test_contract_gate_scripts.py` 中已确认的跨 owner 工件漂移；没有改测试为 expected failure、skip、xfail，也没有删除已批准 migration/artifact。

为确认 backend-owned 代码未留下其他失败，另运行同一全量 suite，**只 deselect 上述 5 个精确 node id**：

```text
466 passed, 4 skipped, 5 deselected, 1 warning in 20.35s
```

这只是补充诊断，不替代上面的题目指定全量命令；全量状态仍是 FAIL。

### 3.7 格式、lint、compile

```bash
.venv/Scripts/python.exe -m black --check app/main.py app/models/hydraulic_models.py tests/integration/test_backend_integration.py tests/hydraulic/test_solver_contract.py \
  && .venv/Scripts/python.exe -m isort --check-only app/main.py app/models/hydraulic_models.py tests/integration/test_backend_integration.py tests/hydraulic/test_solver_contract.py \
  && .venv/Scripts/python.exe -m flake8 app/main.py app/models/hydraulic_models.py tests/integration/test_backend_integration.py tests/hydraulic/test_solver_contract.py
```

最终输出：

```text
All done! ✨ 🍰 ✨
4 files would be left unchanged.
FORMAT_LINT=PASS
```

Python 3.11 compileall：

```bash
/c/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 \
  --with-requirements requirements-py311.lock \
  python -B -m compileall -q app tests
```

输出：`PY311_COMPILEALL=PASS`。

### 3.8 mypy（已执行但未通过）

```bash
.venv/Scripts/python.exe -m mypy app/main.py tests/integration/test_backend_integration.py
```

输出：`Found 76 errors in 7 files (checked 2 source files)`。错误来自当前并行工作树的 `config.py`、`influxdb_service.py`、`sse_manager.py`、`hydraulic_routes.py`、`system_status.py`、`mock_data_generator.py` 和 legacy `routes.py`；本轮新增测试的 `ASGITransport` 类型错误已用 `cast(Any, ...)` 消除，输出中不再包含 `tests/integration/test_backend_integration.py`。未把该失败写成 PASS。

## 4. Typed handoff：5 个 contract-gate 阻断

| 失败 test id | 本测试文件/外部实现 | owner | 期望语义 | 当前根因 |
|---|---|---|---|---|
| `test_live_migration_gate_passes_without_running_alembic` | `backend/tests/test_contract_gate_scripts.py:169-192`; `scripts/check_migration_gate.py:279,346` | contract-gate / R0 gate owner | R0 PASS 后应识别已批准 `0002_hydraulic_schema.py`，只拒绝未授权 migration 变化 | checker 把 live versions path set 与设计冻结 base 比较，任何新增 0002 都固定报错 |
| `test_migration_gate_cli_accepts_workspace_prefixed_paths` | `backend/tests/test_contract_gate_scripts.py:194-207`; 同 checker CLI | contract-gate / R0 gate owner | workspace-prefixed CLI 与直接调用具有相同 post-R0 migration 语义 | 同一 `_verify_versions_unchanged` 根因 |
| `test_live_dependency_handoff_matches_fixture_and_settings` | `backend/tests/test_contract_gate_scripts.py:210-234`; `scripts/check_dependency_handoff.py:917,1075` | dependency-config + contract-gate | handoff result SHA-256 与当前获准落地文件一致，输出 `PASS_WITH_DEFERRED` | `.env.example` 当前 hash 与 `artifacts/contracts/dependency-config-handoff.json` 不一致 |
| `test_dependency_handoff_cli_accepts_workspace_prefixed_paths` | `backend/tests/test_contract_gate_scripts.py:236-263`; 同 dependency checker CLI | dependency-config + contract-gate | workspace-prefixed CLI 同样验证当前 handoff | 同一 `.env.example` hash 根因 |
| `test_frontend_performance_entrypoint_remains_deferred` | `backend/tests/test_contract_gate_scripts.py:318-327`; `frontend/performance/run-twin-map.mjs` | CI/frontend-acceptance | 测试应反映已批准的前端 performance 工件实际状态 | 工件已存在，但旧测试仍断言文件必须不存在 |

这些路径均超出本任务 ownedPaths；没有越界修改。

## 5. 未完成与风险

1. **全量 pytest 仍非绿。** 最终 Python 3.11 全量为 `5 failed, 466 passed, 4 skipped`；必须由上述 owner reconciliation 后再宣称 G0/AC-7 通过。
2. **没有 live durable E2E 证据。** 本机 PostgreSQL/Redis/InfluxDB 均未运行；未执行两个 spatial profile 的 fresh-volume migration、worker claim/heartbeat、Timescale frame finalize、真实 outbox relay、notification provider 或 30 个 nowcast 周期。
3. **API 当前仍以进程内 `HydraulicApiStore` 为测试/开发 adapter。** `backend/app/api/hydraulic_routes.py:1-8,276-326` 明示 durable worker repository 尚未替换该 adapter；production 下 `durable_backend=false` 会 fail closed 为 503。故本轮不能把现有 API 单测称为真实 PostgreSQL 端到端 API。
4. **metrics collectors 已生产注册，但 worker→API 跨进程实时值未在本轮建立。** `/metrics` 已真实包含 queue/active-slot 零值与 duration/failure/retry/timeout/cancel series；独立 simulation worker 的 durable DB snapshot/terminal event 到 API registry 仍需要部署级 exporter 或 API snapshot poller，不能把“指标名已暴露”误报为“所有生产任务值已实时汇聚”。
5. **forecast outbox source 是显式注入边界。** `backend/app/main.py:118-138,190-200` 仅在 `app.state.forecast_outbox_source` 配置时启动 relay；本轮没有伪造 source，也没有 live relay 证据。
6. **mypy 未通过。** 76 个错误属于当前集成工作树的共享依赖/legacy/hydraulic route 类型债务，未在本任务中扩成跨 owner 重写。
7. **未运行 G0～G6/AC-1～AC-8、Compose、PostGIS/Timescale、真实 EPA SWMM/performance/backup-restore。** 这些状态是 NOT RUN，不能由 466 个 in-scope pytest 通过替代。

## 6. 变更文件

- `backend/app/main.py`：生产 metrics singleton import、lifespan 持有 singleton、Black 格式化邻近 integration 代码。
- `backend/app/models/hydraulic_models.py`：修复 retention policy ORM 的 SQLite/PostgreSQL default 兼容。
- `backend/tests/integration/test_backend_integration.py`：新增 import-order、`generate_latest` 和真实 `/metrics` 集成回归。
- `backend/tests/hydraulic/test_solver_contract.py`：稳定 Python 3.11 进程超时测试 deadline，不改变生产 solver 行为或断言。
- `docs/hydraulic-twin/implementation/backend-integration.md`：本报告。

未 commit、未 push；未修改 `backend/alembic/**`。
