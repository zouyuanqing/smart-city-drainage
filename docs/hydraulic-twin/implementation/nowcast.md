# Nowcast 调度与初始状态同化实施记录

## 范围

本轮交付 Nowcast 的三个边界：

1. **周期与预报配置**：默认调度间隔 10 分钟，预报范围 0–120 分钟，固定 5 分钟网格，发布 deadline 180 秒；配置拒绝超过 2 小时、非 5 分钟网格和超过 180 秒的 deadline。实现见 `backend/app/services/hydraulic/nowcast.py:244-359`。
2. **调度器**：以 UTC 对齐时间槽，保存 `(network_id, scheduled_for)` 唯一键；同一槽重复触发返回同一个不可变请求，不再次调用模型/传感器 provider。实现见 `backend/app/services/hydraulic/nowcast.py:1387-1745`。
3. **初始状态同化**：按当前时刻选择最近窗口内、质量为 `good` 且可信的传感器读数；未来、过期、低质量和不可信点被拒绝并记录；没有传感器时使用率定模型基线并保留 `missing_targets`。实现见 `backend/app/services/hydraulic/nowcast.py:1060-1256`。

该模块只生成可供 worker 消费的确定性请求，不直接广播 SSE、不执行 solver、不写数据库。仓储和 provider 均为注入边界，默认实现是可测试的内存实现；生产 worker 仍需注入 PostgreSQL durable repository、真实观测适配器和率定工件仓库。

## 依据与契约

- 源 PRD 要求默认每 10 分钟 nowcast、0–2h/5 分钟步长：`docs/hydraulic-twin/SOURCE_PRD.md:20-24`。
- 冻结补充基线规定 10/120/5/180 默认值、fake-clock 100 时隙和 nowcast SLO：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:429-433`。
- 同化只允许可信、绑定有效、质量良好的观测；`bad/missing/stale` 和未来观测不能进入当前状态：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:106-118`。
- 初始状态必须独立于 network revision hash，warm state 要绑定观测来源：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:264-269`。
- nowcast 使用统一 durable-job 状态和固定 deadline，不能复制另一套状态机：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1376-1405`。
- 现有 ORM 已有 `nowcast_runs(network_id, scheduled_for)` 唯一约束：`backend/app/models/hydraulic_models.py:2199-2205`；本文件的内存仓储复现该幂等边界，生产实现应由数据库事务最终裁决。

## 实际改动

### `backend/app/services/hydraulic/nowcast.py`

- 新增 `ManualClock`/`VirtualClock`/`FakeClock`，支持 `set`、`advance` 和 `advance_minutes`，调度器和同化器都通过可注入 clock 读取时间（`nowcast.py:151-207`）。
- 新增 `NowcastConfig`、配置校验、UTC 对齐和 inclusive horizon 生成（`nowcast.py:244-392`）。
- 新增 `SensorReading`、率定模型引用和 `InitialState` DTO；状态 hash 使用 canonical JSON + SHA-256，并保留观测来源、缺测和拒绝原因（`nowcast.py:396-771`）。
- 新增 `InitialStateAssimilator`：只取 `observed_at <= as_of` 且在 lookback 内的最新点，转换 `water_level_mm` 为 `depth_m`，未来/过期/低质量/不可信点不进入状态；支持 `require_complete=True` fail-closed（`nowcast.py:1060-1256`）。
- 新增 `InMemoryNowcastRepository` 和 `NowcastScheduler`；调度先查唯一槽，再调用 provider/同化器，保证重复触发不会创建第二个 task（`nowcast.py:1387-1745`）。
- 提供 `NowcastConfig.from_settings()`，默认 scheduler 从现有 Settings 的 NOWCAST 变量读取配置（`nowcast.py:293-306`、`nowcast.py:1450-1458`）。

### `backend/tests/hydraulic/test_nowcast.py`

新增 12 个 focused tests，覆盖：

- 默认 10/120/5/180 配置和非法配置；
- 虚拟时钟推进、下一槽位、25 个 5 分钟 horizon 点；
- 同一 network/time slot 重复触发、ID 和 provider 调用不重复；
- 相邻槽位创建新任务；
- 最新可信传感器覆盖旧点；
- 未来、过期、低质量和不可信观测拒绝；
- 缺测目标使用模型基线并保留 `missing_targets`；
- `require_complete=True` 的缺测异常。

## 已执行检查

工作目录：`C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend`。

| 命令 | 结果 |
|---|---|
| `python -m py_compile app/services/hydraulic/nowcast.py tests/hydraulic/test_nowcast.py` | PASS，exit 0（Python 3.14） |
| `python -m pytest tests/hydraulic/test_nowcast.py -q --tb=short` | **12 passed**，exit 0；288 warnings |
| `python -m pytest tests/hydraulic -q --tb=short` | **244 passed**，exit 0；8552 warnings |
| `python -m pytest tests -q --tb=short` | **exit 1**：399 passed、4 skipped、5 failed；失败均在既有 contract-gate 测试，非本轮 owned paths |
| `C:/Users/zyq/AppData/Roaming/uv/python/cpython-3.11.15-windows-x86_64-none/python.exe -m py_compile ...` | PASS，exit 0 |
| `C:/Users/zyq/AppData/Roaming/uv/python/cpython-3.11.15-windows-x86_64-none/python.exe -m pytest tests/hydraulic/test_nowcast.py -q --tb=short` | 未执行成功：该 3.11 环境没有安装 pytest（exit 1） |
| `py -3.11 -m pytest tests/hydraulic/test_nowcast.py -q --tb=short` | 未执行成功：py launcher 找不到 3.11 runtime（exit 103） |
| `python -m black --check ...` | 未执行成功：当前 Python 环境没有 black 模块（exit 1） |
| `python -m mypy app/services/hydraulic/nowcast.py` | 未执行成功：当前 Python 环境没有 mypy 模块（exit 1） |

完整 backend suite 的 5 个失败及原因（本轮未修改相关文件）：

1. `test_live_migration_gate_passes_without_running_alembic`、`test_migration_gate_cli_accepts_workspace_prefixed_paths`：工作树已有 `backend/alembic/versions/0002_hydraulic_schema.py`，migration gate 报 `versions path set changed from base`。
2. `test_live_dependency_handoff_matches_fixture_and_settings`、`test_dependency_handoff_cli_accepts_workspace_prefixed_paths`：dependency handoff 报 `.env.example` result SHA-256 mismatch。
3. `test_frontend_performance_entrypoint_remains_deferred`：工作树已有 `frontend/performance/run-twin-map.mjs`，与该 deferred characterization 断言冲突。

另执行了 owned files 的 trailing-whitespace 检查，输出 `trailing_whitespace= []`。没有 commit、push、migration 或其它 owned-path 之外的代码修改。

## 风险与后续

- **持久化边界**：默认 `InMemoryNowcastRepository` 只用于确定性单测；生产必须把 `get_or_create` 接到 PostgreSQL `nowcast_runs` 的唯一约束/事务，并在 durable job/lease/fencing 体系内执行，不能把内存结果当生产事实源。
- **真实观测**：本轮没有 live Influx、真实 device-network binding、source event policy 或数据库同化验证。生产适配器必须只提供 `external_verified + auth_verified + binding_verified + quality=good` 的点，并保留稳定 source event identity；旧无身份 Influx 点不能直接升级为正式同化输入。
- **工件/发布**：本模块不实现 probability calibration、forecast rule、outbox、SSE 或通知发布；这些仍需按 `IMPLEMENTATION_CONTRACT.md:1793-1814`、`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1800-1802` 由对应 owner 接入。
- **运行时规模**：没有执行 100 个 fake slot、30 个真实周期、PostgreSQL/Redis 双 worker 或 180 秒 SLO benchmark；本轮通过的是模块级确定性测试，不是 AC-4/G5 发布门禁。
- **工具链**：当前可用解释器为 Python 3.14；Python 3.11 仅完成语法编译，pytest/black/mypy 依赖未安装，因此不能把 3.11 测试或格式/类型门禁宣称为通过。
