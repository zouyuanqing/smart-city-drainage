# 预测告警、抑制与风险实施摘要

> 模块：`forecast-alerts`
> 日期：2026-09-25
> 状态：领域服务、事务端口、风险排序与聚焦测试已完成；PostgreSQL 持久化适配器及 nowcast/API/SSE 接线未在本任务 ownedPaths 内完成。
> 仓库：`smart-city-drainage`

## 1. 范围与契约依据

本轮先实际读取并按以下输入实施：

- `docs/hydraulic-twin/SOURCE_PRD.md:20-30`：10 分钟 nowcast、0–2 h/5 min horizon、`flood_forecast`/`pipe_surcharge`、告警追溯字段、Top-N 和默认 30 分钟抑制。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:189-230`：处置 `status` 与证据 `phase` 正交、lead-time 公式、概率校准、refresh 阈值、活动 episode 复用和终态后半开冷却窗。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:98-108`：canonical JSON 与 suppression key 的唯一输入。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1107-1182`：alert/event-window/transition/outbox 的持久化字段和约束。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1688-1726`：forecast API、lead time、freshness 与 risk-ranking 排序要求。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1764-1814`：唯一状态机、confidence 语义和可解释风险公式。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2303-2312`：`forecast_alerts.py`/`risk.py` 归 forecast-alerts owner，禁止修改 legacy Alert、HTTP 或 SSE。

代码修改严格限制在三个授权路径；另按任务显式要求创建本报告。未修改模型、migration、API、worker、SSE、legacy `Alert`、依赖或其他 owner 文件；未 commit、未 push。

## 2. 真实代码改动

### 2.1 `backend/app/services/hydraulic/forecast_alerts.py`

1. 新增强类型 forecast candidate/rule/probability calibration/alert/window/transition/outbox DTO；candidate 强制保存 network revision、target manifest、目标、深度、置信度、level、nowcast run 和 `simulation_id`，并拒绝 naive datetime、非有限数、非法 hash/UUID、越界 confidence/level（`forecast_alerts.py:391-546`）。
2. 实现冻结 suppression canonical object：仅包含 `schema/network_id/alert_type/target_type/target_id/rule_id`，以 UTF-8、排序 key、无 whitespace JSON 取 SHA-256；时间、phase、confidence、simulation/run ID 均不进入 key（`forecast_alerts.py:164-218`）。
3. 实现 `lead_time_minutes=max(0,floor((expected_at-issued_at)/60s))`，并将 horizon 限制为 2 h（`forecast_alerts.py:221-230`）。
4. 概率校准只接受冻结的四种 method，并校验 artifact hash、lead/magnitude domain 与 validity；forecast rule 校验 alert type、metric/operator/threshold、risk scale、refresh delta、step 与 validity（`forecast_alerts.py:233-388`）。
5. 定义 `ForecastAlertStore`/`ForecastAlertTransaction` 事务端口，明确生产适配器必须用 PostgreSQL insert-on-conflict + `FOR UPDATE` 锁住 `forecast_alert_windows`，并在同一事务写 alert、transition、event-window 与 outbox；模块内没有进程内完成态或抑制事实源（`forecast_alerts.py:642-672`）。
6. `ForecastAlertService.publish(...)`：
   - 校验 flood→node/water depth、pipe→pipe segment/fill ratio、policy/calibration 适用域和阈值（`forecast_alerts.py:675-742`）；
   - 首次合格候选生成 `open/predicted` alert、`alert.created` transition/outbox，并写 lead time、depth/fill ratio、confidence、simulation lineage（`forecast_alerts.py:828-943,1031-1079`）；
   - 同一 nowcast run 的完全相同重放返回 `unchanged`，同 run 改 payload 返回 `FORECAST_CANDIDATE_CONFLICT`（`forecast_alerts.py:1123-1152`）；
   - 活动 episode 复用同一 alert ID，刷新 expected/lead、simulation、confidence、measurement、risk、peak/version；level 只能升高；只有 rule delta 命中才写 `alert.updated`/`alert.escalated`（`forecast_alerts.py:1154-1247`）；
   - 终态冷却采用 `[terminal_at, terminal_at+30m)`：窗内只增加 `suppressed_count`，不产生新 alert/outbox，精确到 30 分钟边界可创建新 episode（`forecast_alerts.py:880-900,945-1029`）。
7. 输出 `ForecastBusinessEvent.payload()`，包含 event/network/run/simulation/alert envelope，供 outbox owner 同事务持久化（`forecast_alerts.py:602-627`）。

### 2.2 `backend/app/services/hydraulic/risk.py`

1. 实现冻结 v1 可解释公式：confidence、severity、urgency、normalized impact 四分量，权重 `0.35/0.30/0.20/0.15`，结果四舍五入两位；非有限值、非法 level/phase/lead/risk scale 均 fail closed（`risk.py:131-196`）。
2. `rank_top_risk(...)` 只接受 `open/acknowledged` 且来源 `nowcast_issued_at` 未 stale 的告警，固定排序 `risk_score DESC, expected_at ASC, id ASC`，Top-N 限制 1..100（`risk.py:199-257`）。
3. 每个排名项返回 score components、policy version 和 frontend/API 兼容 `to_dict()`；风险分明确是处置优先级而非概率（`risk.py:73-107`）。

### 2.3 `backend/tests/hydraulic/test_forecast_alerts.py`

新增 10 个聚焦 case，覆盖：

- canonical suppression 输入和禁止字段、秒级 floor lead time 与 2 h 上限（`test_forecast_alerts.py:221-277`）；
- flood/pipe 两类创建以及 depth/fill ratio、confidence、simulation ID、lead time 追溯（`:280-303`）；
- 活动窗 confidence/simulation/lead 更新但 alert ID 不变，小变化不发 outbox（`:305-334`）；
- 同 nowcast 幂等重放与冲突（`:336-353`）；
- level 不允许在活动期降级（`:355-377`）；
- 终态后 29m59s 抑制、精确 30m00s 新建（`:380-423`）；
- 不同设备 Top-N 风险顺序、score components、stale 排除，以及手算风险分/NaN 拒绝（`:426-496`）。

## 3. 本轮实际执行的检查

### 3.1 Python 3.11 聚焦测试（任务要求）

执行：

```bash
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" \
  && uvx --python 3.11 --from pytest==8.0.1 --with pytest-asyncio==0.23.6 --with-requirements requirements-cpu.txt pytest tests/hydraulic/test_forecast_alerts.py -q --tb=short
```

结果：

```text
..........                                                               [100%]
10 passed, 1 warning in 0.06s
```

唯一 warning 是现有 Pydantic `model_version` protected-namespace 提示，与本模块无关。

### 3.2 Python 3.11 全部 hydraulic 回归

执行：

```bash
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" \
  && uvx --python 3.11 --from pytest==8.0.1 --with pytest-asyncio==0.23.6 --with-requirements requirements-cpu.txt pytest tests/hydraulic -q --tb=short
```

结果：

```text
242 passed, 1 warning in 3.09s
```

### 3.3 owned 文件格式、import、lint、类型与 Python 3.11 语法

执行：

```bash
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" \
  && .venv/Scripts/python.exe -m black --check app/services/hydraulic/forecast_alerts.py app/services/hydraulic/risk.py tests/hydraulic/test_forecast_alerts.py \
  && .venv/Scripts/python.exe -m isort --check-only app/services/hydraulic/forecast_alerts.py app/services/hydraulic/risk.py tests/hydraulic/test_forecast_alerts.py \
  && .venv/Scripts/python.exe -m flake8 app/services/hydraulic/forecast_alerts.py app/services/hydraulic/risk.py tests/hydraulic/test_forecast_alerts.py
```

结果：Black 输出 `3 files would be left unchanged.`；isort 与 flake8 无输出；组合命令 exit 0。

执行：

```bash
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" \
  && .venv/Scripts/python.exe -m mypy --follow-imports=skip app/services/hydraulic/forecast_alerts.py app/services/hydraulic/risk.py
```

结果：`Success: no issues found in 2 source files`。

执行：

```bash
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" \
  && py -V:Astral/CPython3.11.15 -m compileall -q app/services/hydraulic/forecast_alerts.py app/services/hydraulic/risk.py tests/hydraulic/test_forecast_alerts.py \
  && printf '%s\n' PY311_COMPILE=PASS
```

结果：`PY311_COMPILE=PASS`。

### 3.4 全 backend pytest（额外回归，不替代上面的 hydraulic suite）

执行同一 Python 3.11/pytest 环境下的 `pytest -q --tb=short`，结果：

```text
6 failed, 391 passed, 4 skipped, 1 warning in 22.35s
```

6 个失败均不在本任务 ownedPaths，且本轮没有修改相应文件：

1. 两个 migration gate：相对 base 新增/变化的 `backend/alembic/versions/0002_hydraulic_schema.py` 路径集不一致；
2. 两个 dependency handoff gate：并行工作区 `.env.example` SHA-256 与 handoff 不一致；
3. frontend performance deferred gate：并行工作区已存在 `frontend/performance/run-twin-map.mjs`；
4. SQLite cascade：现有 `result_retention_policies.id DEFAULT uuid_generate_v4()` 在 SQLite DDL 报 `near "(": syntax error`。

因此本轮只声明 focused 与全部 hydraulic 测试通过，不声明全 backend 通过。

## 4. 风险、缺口与后续接线

1. **生产 PostgreSQL store 尚未实现。** 当前 `backend/app/models/hydraulic_models.py` 有 `ForecastAlert`，但本轮再次执行 `rg "ForecastAlertWindow|forecast_alert_windows|class EventOutbox|__tablename__ = \"event_outbox\"" backend/app/models/hydraulic_models.py backend/alembic/versions/0002_hydraulic_schema.py` 无输出；而冻结契约要求 `forecast_alert_windows`（`IMPLEMENTATION_CONTRACT.md:1155-1166`）和 `event_outbox`（`:1184-1199`）。schema/migration 不在本任务 ownedPaths，故本轮只交付并测试事务端口，未伪造 ORM/迁移。
2. **并发锁未在 live PostgreSQL 证明。** 测试 store 位于 `test_forecast_alerts.py:43-111`，验证版本 CAS、窗口复用、抑制计数和业务行数量的确定性算法；它不能替代真实 `INSERT ... ON CONFLICT ... FOR UPDATE`、唯一约束、事务回滚和 20 并发候选的 G4 测试。
3. **未接入 nowcast finalizer/API/SSE/notification。** `ForecastBusinessEvent` 是可持久化 payload，不是 direct broadcast；后续 owner 必须在同一 DB transaction 写 outbox，并由 relay/notification worker 异步发送。`http/sse` 文件不在 ownedPaths，本轮未越界接线。
4. **confidence 是输入工件结果，不在本服务训练或伪造。** 服务会 fail closed 校验 method/hash/domain/validity；真实 probability calibration 工件和 policy config sidecar 仍需 nowcast owner 提供。
5. **未运行 G4/G5/AC-4 live gate、真实 worker、PostgreSQL/Redis/SMTP/webhook 或两 worker replay。** 本轮通过的是模块级 Python 3.11 测试和 hydraulic 回归，不等价于完整 AC-4 PASS。
6. **全 backend 基线仍有 6 个 ownedPaths 外失败**，需 migration/dependency/frontend/schema owner 处理后重跑。

## 5. 变更文件

- `backend/app/services/hydraulic/forecast_alerts.py`（新增）
- `backend/app/services/hydraulic/risk.py`（新增）
- `backend/tests/hydraulic/test_forecast_alerts.py`（新增）
- `docs/hydraulic-twin/implementation/forecast-alerts.md`（本报告，按任务显式输出要求新增）

未提交、未推送。
