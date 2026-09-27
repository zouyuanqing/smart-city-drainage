# 仿真可观测性实施摘要

> 模块：`hydraulic-metrics`  
> 日期：2026-09-25  
> 状态：owned code + focused tests 已完成；worker/main 运行时接线与 Python 3.11 测试环境仍待 integration owner。  
> 仓库：`smart-city-drainage`

## 1. 规范输入与边界

本轮实际读取并按以下输入实施：

- `docs/hydraulic-twin/SOURCE_PRD.md:20-30`：仿真队列要求，NFR 要求 Prometheus 暴露任务时长/失败率/队列深度，日志包含 `simulation_id`。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:68-76`：现有 FastAPI 已有 `/metrics`、Prometheus instrumentator、请求 ID 与处理时间中间件。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:402-415`：worker wall time 的计时边界、queue wait 单列、frame bytes 与质量结果要求。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2171-2203`：冻结 21 个 metric family、`simulation_duration_seconds{solver,status}` 标签、禁止高基数 ID label，以及适用结构化日志字段。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2284-2300`：`backend/app/services/hydraulic/metrics.py` 唯一 owner 为 `hydraulic-metrics`，禁止高基数 ID label。
- `backend/app/main.py:206-220`：现有 `prometheus_fastapi_instrumentator.Instrumentator` 在 `/metrics` 暴露默认 registry。
- `docs/hydraulic-twin/reviews/operations.md:164-178`：现有 dashboard/rules 只做静态配置校验，backend exporter 需由本模块真实提供。

代码修改严格限制在任务授权的两个路径；另按任务显式要求创建本报告。未修改 `main.py`、worker、Compose、PRD、契约、依赖或其他 owner 文件；未 commit、未 push。

## 2. 真实改动

### 2.1 Prometheus collectors

新增 `backend/app/services/hydraulic/metrics.py`：

1. 完整定义冻结契约的 21 个 metric family（`metrics.py:30-53,243-370`），包括：
   - queue depth / oldest queued age；
   - terminal duration histogram；
   - failures / retries / cancelled / timeout counters；
   - active solver slots、solver fallback、frame count；
   - nowcast、forecast alert、outbox、SSE、notification、cleanup 指标。
2. 新增任务目标要求但冻结列表未单列的 `simulation_result_size_bytes{solver,status}` histogram，bucket 覆盖 1 KiB 到 8 GiB；同时保留 `simulation_frames_total`（`metrics.py:70-79,294-305`）。
3. 失败率不发布错误的“累计比值 gauge”；由 `simulation_failures_total` 与 `simulation_duration_seconds_count` 在 Prometheus 中按时间窗口计算。这与现有 recording rule `hydraulic-recording.yml:19-23` 的输入一致。
4. queue/running 状态使用显式 snapshot API，数据库仍为事实源：
   - `set_queue_state(depth, oldest_queued_seconds)`；
   - `set_active_slots(general, nowcast)`（`metrics.py:386-410`）。
5. terminal API `record_simulation_terminal(...)` 统一记录：
   - duration histogram；
   - failed/cancelled/timed_out 对应计数；
   - frame count 与 result byte histogram；
   - terminal structured log（`metrics.py:429-498`）。
6. 对负数、NaN/Inf、非法 status、失败缺 error code、空 label fail closed，避免污染 collector 或生成不可信指标（`metrics.py:84-150,443-455`）。
7. 默认 `hydraulic_metrics` singleton 注册到 Prometheus 默认 registry，并初始化 queue/两个 lane 的零值序列（`metrics.py:374-384,588-596`）。测试可用独立 `CollectorRegistry` 构造 `HydraulicMetrics`，避免全局状态串扰。

### 2.2 结构化日志

新增 allow-list 日志 API `log_simulation_event(...)`（`metrics.py:153-229`）：

- 每条仿真事件强制包含 `simulation_id` 与 `event`；
- 支持契约中的 `request_id`、`user_id`、network/revision、rainfall scenario、attempt、solver id/version、nowcast/alert/event ID、aggregate version、phase、error code；
- 额外记录 status、duration、frame count、result bytes；
- 显式关键字 allow-list 阻止调用方顺手传入 JWT、secret、完整输入或 sandbox 绝对路径；
- `record_simulation_started` 和 `record_simulation_terminal` 自动调用该 API，失败/超时/取消按日志级别区分（`metrics.py:412-427,480-498`）。

### 2.3 指标测试

新增 `backend/tests/hydraulic/test_hydraulic_metrics.py`，共 12 个 pytest case，覆盖：

- completed/failed terminal count、duration histogram count/sum、failure counter、frame count、result byte histogram（`test_hydraulic_metrics.py:52-134`）；
- cancelled、timeout、retry、solver fallback 独立计数（`:137-173`）；
- queue depth、oldest age、general/nowcast running slot（`:176-203`）；
- JSON 结构化日志中的 `simulation_id` 与安全字段（`:205-265`）；
- 21 个冻结 metric family 均可实际发出，并验证无 `simulation_id`/`alert_id`/`event_id` Prometheus label（`:268-309`）；
- 默认 registry 的 queue/slot 零值序列（`:312-317`）；
- 非法负数、NaN、status、失败 error code 的 fail-closed 行为（`:320-361`）。

## 3. 本轮实际执行的检查

### 3.1 专项 pytest

执行：

```bash
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" \
  && .venv/Scripts/python.exe -m pytest tests/hydraulic/test_hydraulic_metrics.py -q --disable-warnings
```

输出：

```text
............                                                             [100%]
12 passed, 376 warnings in 0.03s
```

376 条均为现有 FastAPI/pytest-asyncio 在 Python 3.14 下的 DeprecationWarning；无测试失败。

### 3.2 格式、lint、类型与 Python 3.11 语法

执行：

```bash
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" \
  && .venv/Scripts/python.exe -m black --check app/services/hydraulic/metrics.py tests/hydraulic/test_hydraulic_metrics.py \
  && .venv/Scripts/python.exe -m isort --check-only app/services/hydraulic/metrics.py tests/hydraulic/test_hydraulic_metrics.py \
  && .venv/Scripts/python.exe -m flake8 app/services/hydraulic/metrics.py tests/hydraulic/test_hydraulic_metrics.py
```

输出：

```text
All done! ✨ 🍰 ✨
2 files would be left unchanged.
```

isort 与 flake8 无输出，组合命令 exit 0。

执行：

```bash
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" \
  && .venv/Scripts/python.exe -m mypy app/services/hydraulic/metrics.py
```

输出：

```text
Success: no issues found in 1 source file
```

执行：

```bash
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" \
  && py -V:Astral/CPython3.11.15 -m compileall -q app/services/hydraulic/metrics.py tests/hydraulic/test_hydraulic_metrics.py \
  && echo PY311_COMPILE=PASS
```

输出：

```text
PY311_COMPILE=PASS
```

### 3.3 监控配置契约

执行：

```bash
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage" \
  && backend/.venv/Scripts/python.exe docker/prometheus/validate_monitoring.py
```

输出：

```text
MONITORING_VALIDATION=PASS
CONTRACT_METRICS=21
GRAFANA_PANELS=15
PROMQL_TARGETS=24
HIGH_CARDINALITY_ID_LABELS=0
```

该脚本只验证配置文本/dashboard；真实 collector 发射由 3.1 的独立 registry 测试覆盖。

### 3.4 全 hydraulic 回归尝试

执行：

```bash
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" \
  && .venv/Scripts/python.exe -m pytest tests/hydraulic -q
```

结果：**未通过，且不是本模块测试失败**。collection 阶段有两个并行模块实现尚未落盘：

```text
ERROR tests/hydraulic/test_python_hydraulics.py
E   ModuleNotFoundError: No module named 'app.services.hydraulic.solvers.python_hydraulics'

ERROR tests/hydraulic/test_results_calibration.py
E   ModuleNotFoundError: No module named 'app.services.hydraulic.calibration'

Interrupted: 2 errors during collection
```

因此本轮只能确认 focused metrics tests 通过，不能宣称完整 hydraulic suite 通过。

### 3.5 Python 3.11 pytest 未运行

现有 `backend/.venv` 是 Python 3.14.4。已安装的 Python 3.11.15 解释器没有 pytest；执行：

```bash
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" \
  && py -V:Astral/CPython3.11.15 -m pytest tests/hydraulic/test_hydraulic_metrics.py -q
```

输出：

```text
C:\Users\zyq\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe: No module named pytest
```

所以 Python 3.11 只完成 `compileall`，未完成运行时 pytest；不能把 3.14 focused PASS 冒充 3.11 gate PASS。

## 4. 风险与后续接线

1. **运行时 import/call-site 未在本 ownedPaths 内完成。** `backend/app/main.py` 与 simulation worker 都不在本任务授权范围；integration/task-sandbox owner 仍需导入 `hydraulic_metrics`，并从 durable DB snapshot 调用 `set_queue_state`/`set_active_slots`、在 terminal finalize 后调用 `record_simulation_terminal`。在接线前，模块定义和测试存在，但生产 `/metrics` 不会自动因本文件而导入。
2. **Compose monitoring mount 仍是 integration 风险。** operations 实施报告 `docs/hydraulic-twin/implementation/operations.md:251-259` 已记录 rules/Grafana 尚未挂载；本模块无权修改 `docker-compose.yml`。
3. **完整 backend suite 仍有 5 个 contract-gate 失败**，均来自当前工作区中 integration 拥有的 migration/dependency handoff 与 frontend performance 状态；本轮 hydraulic focused suite 已独立通过。
4. **Python 3.11 环境门禁未运行。** 3.11 compile 已通过，运行时测试需在安装锁定依赖的 3.11 环境重跑。
5. **未运行完整 G0-G6/AC、真实 worker、数据库/Redis/Compose 或 `/metrics` HTTP smoke。** 本轮只完成模块级 Prometheus/日志测试和静态监控配置校验。

## 5. 变更文件

- `backend/app/services/hydraulic/metrics.py`（新增）
- `backend/tests/hydraulic/test_hydraulic_metrics.py`（新增）
- `docs/hydraulic-twin/implementation/hydraulic-metrics.md`（本报告，按任务显式输出要求新增）


## 6. Round 1 backend-integration handoff (2026-09-25)

本轮确认生产接线仍不可由 `hydraulic-metrics` owner 修改：`backend/app/main.py` 的 Instrumentator `/metrics` 是唯一 scrape target，但 `app.main` 当前不导入 `app.services.hydraulic.metrics`。本地直接检查命令：

```bash
.venv/Scripts/python.exe -c "import app.main; from prometheus_client import generate_latest, REGISTRY; text=generate_latest(REGISTRY).decode(); print('simulation_queue_depth' in text, 'hydraulic_metrics' in globals())"
```

输出：

```text
False False
```

backend-integration 必须：

- 在 `Instrumentator(...).instrument(app).expose(...)` 之前导入并持有 `hydraulic_metrics` singleton（或等价导入 collectors）；
- 确认 `/metrics` 实际出现 `simulation_queue_depth`、任务 duration/failure/retry/timeout/active-slot 等 collector；
- 增补 `import app.main; generate_latest(REGISTRY)` 或等价 HTTP scrape 回归断言；
- 在 worker/control-plane 接线 durable queue/slot snapshots 与 terminal events 后，再评估告警规则；collector 本身的 bounded labels、原子校验、初始 duration series、retry/fallback `simulation_id` 日志和 terminal replay 幂等已在本轮 focused tests 覆盖。

因此本 handoff 明确标记：生产 `/metrics` wiring **尚未完成，不能把该目标标为 PASS**。



Round 1 focused metrics recheck (executed after the collector changes):

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/hydraulic/test_hydraulic_metrics.py -q
```



## 7. Round 2 typed handoff (2026-09-25)

状态：`/metrics` = **deferred-to-backend-integration**。本阶段不得把该目标标为 PASS，也不得修改 `backend/app/main.py`。

唯一必要 wiring：在 `backend/app/main.py:216-220` 的 `Instrumentator(...).instrument(app).expose(...)` 之前导入并持有 `app.services.hydraulic.metrics` 的 `hydraulic_metrics` singleton（导入顺序必须在 Instrumentator 初始化前）。随后由 backend-integration 增加等价回归：

```python
import app.main
from prometheus_client import REGISTRY, generate_latest

exposition = generate_latest(REGISTRY).decode("utf-8")
assert "simulation_queue_depth" in exposition
assert "simulation_duration_seconds" in exposition
assert "simulation_failures_total" in exposition
```

也可使用同等 HTTP `/metrics` smoke。当前 round-2 直接检查仍为：

```text
False False
```



## 8. Round 3 verification (2026-09-25)

状态未改变：五个风险仍按 owner 边界 deferred；本阶段没有伪造 PASS。

- 测试路径错误已复核：`backend/tests/hydraulic/test_tasks_sandbox.py` 实际存在；从 `backend/` 目录运行 `.venv/Scripts/python.exe -m pytest tests/hydraulic/test_tasks_sandbox.py -q` 输出 `17 passed, 771 warnings in 1.87s`。从项目父目录错误地写成 `backend/tests/...` 会产生 user-supplied 的 `file or directory not found`，不是代码失败。
- 完整 hydraulic 回归：`.venv/Scripts/python.exe -m pytest tests/hydraulic -q` 输出 `211 passed, 7205 warnings in 2.47s`。
- 变更实现静态检查：Black 输出 `27 files would be left unchanged`；隔离 imports 的 mypy 输出 `Success: no issues found in 10 source files`。
- `/metrics` 仍必须由 backend-integration 在 `app.main` 的 Instrumentator 初始化前导入 singleton；本阶段 direct registry 检查仍为 `False False`，不重复编辑 shared `main.py`。
- Windows ACL/AppContainer filesystem broker、正式 worker_slots/nowcast schema、真实 EPA SWMM/performance/PostGIS/Timescale/Compose/G0-G6/AC 仍分别属于 platform/integration/schema/acceptance owner；本轮不宣称完成。

未提交、未推送。
