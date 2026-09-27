# AC-4 独立验收（round 1）：nowcast 预测告警、SSE 与抑制

- 验收日期：2026-09-28
- 验收人：独立验收员（只读；本轮除新增本审计文件外未修改任何实现、测试或契约）
- 仓库：`smart-city-drainage`
- 基线：`git rev-parse --short=8 HEAD` = `ca99e048`；`git status --porcelain --untracked-files=all | wc -l` = `732`（工作树非干净，结论对应当前工作树）
- 验收对象：AC-4「nowcast 产生含提前量的 flood_forecast，进入告警中心与 SSE 且窗口内不重复」

---

## 0. 结论

**AC-4 = BLOCKED。本轮未取得任何 AC-4 的 PASS 证据；且我判定 AC-4 在当前代码下"结构性不可达"，不只是"证据未采集"。**

核心事实是一条可复现的断链：

> **没有任何代码路径会写入 forecast alert 或 event outbox。**
> 默认 nowcast 物化 runner 明确以退出码 4 拒绝产出预报（`FORECAST_MATERIALIZER_UNAVAILABLE`）；`ForecastAlertService` 只被测试引用，零生产调用方；REST forecast 读路径绑定在一个永远为空的进程内字典上。

因此 `nowcast → horizon point → alert → event_outbox → Redis → SSE` 这条链在第一环就断了。抑制逻辑本身（30 分钟半开窗）**实现正确且有测试**，但它挂在一个没有生产调用方的服务上，无法被 AC-4 观察到。

诚实度方面，**实现方没有虚报**：`docs/hydraulic-twin/implementation/forecast-alerts.md:5` 自述"PostgreSQL 持久化适配器及 nowcast/API/SSE 接线未在本任务 ownedPaths 内完成"；`backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json` 的 AC-4 条目记 `status: "NOT RUN"` 且理由准确；`VALIDATION_EVIDENCE.md` 的 16 项 final gates 中**没有** AC-4 门禁。**本审计不发现"文档勾选冒充实现"的造假，问题是能力缺口。**

---

## 1. 验收口径（本次读取的材料）

| 来源 | 与 AC-4 相关的条款 |
|---|---|
| `docs/hydraulic-twin/SOURCE_PRD.md:23`（FR-4） | 默认每 10 分钟 nowcast；**0–2h、5 分钟步长**；新增 `flood_forecast` 与 `pipe_surcharge`；告警含**目标、预计时刻、lead_time_minutes、深度、置信度、simulation_id**；进入**告警中心、SSE、通知**并有**预计/已发生视觉区分**；Top-N 风险；**默认 30 分钟事件窗抑制** |
| `docs/hydraulic-twin/SOURCE_PRD.md:31`（非功能） | **nowcast 到告警不超过 3 分钟** |
| `docs/hydraulic-twin/SOURCE_PRD.md:33`（AC-4） | nowcast 产生含提前量的 flood_forecast，进入告警中心与 SSE 且**窗口内不重复** |
| `docs/hydraulic-twin/PRD_SUPPLEMENT.md:589` | AC-4 门禁口径：G4 + G5 nowcast harness；fake clock 100 时隙 + 30 真实周期 + 两 API worker durable event + calibration/rule artifact hash；**每周期 ≤180s**；30m 一个 created business event。当前登记状态：「未实现/本轮未运行」 |
| `docs/hydraulic-twin/PRD_SUPPLEMENT.md:186-187` | nowcast SLO 从 `scheduled_for` 到 **alert + event outbox 原子提交**，P95 与 max 均 ≤180s |
| `docs/hydraulic-twin/PRD_SUPPLEMENT.md:219-234` | suppression_key 唯一输入、终态后半开冷却窗 `[terminal_at, terminal_at+30m)`、冷却后新 `alert_id` |
| `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2494`（G4） | 完整 forecast transition、policy sidecar/hash、artifact ID+hash 复合 FK、outbox single path、跨 worker/restart replay、gap/resync、30m suppression、delivery retry/DLQ |
| `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2513-2514`（§17.1.1 G5） | 固定命令含 `scripts/benchmark_hydraulic_api.py` 与 `scripts/benchmark_hydraulic_nowcast.py` |
| `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1032` | AC-4 必交 fixture 五个路径（calibration/rule/policy config/sidecar/expected-ac4） |
| `docs/hydraulic-twin/VALIDATION_EVIDENCE.md` | 16 项 final gates + 21 项 stages 中**无 AC-4 门禁条目**；`§5` 未运行清单亦未列 AC-4 |

---

## 2. 本轮实际执行的检查（完整命令 + 真实输出）

### 2.1 冻结 G4 门禁（AC-4 的授权门禁）

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage"
$ python -B scripts/verify_hydraulic_stack.py --gate g4 > .runtime/g4-audit.json 2>&1; echo "PROCESS_EXIT=$?"
PROCESS_EXIT=2
$ grep -o '"status": "[A-Z]*"' .runtime/g4-audit.json | head -3
"status": "BLOCKED"
"status": "BLOCKED"
$ grep -o '"missing_fixtures": \[[^]]*\]' .runtime/g4-audit.json
"missing_fixtures": ["backend/tests/fixtures/hydraulic/forecast/v1/manifest.json", "backend/tests/fixtures/hydraulic/realtime/outbox-sse-v1.json"]
```

**G4 = BLOCKED，进程退出码 2。** 门禁在 fixture 存在性这一步即返回，**未执行任何行为断言**。

### 2.2 AC-4 验收汇总门禁

```text
$ python -B scripts/verify_hydraulic_stack.py --gate acceptance --ac ac4 > .runtime/ac4-acceptance-audit.json 2>&1; echo "PROCESS_EXIT=$?"
PROCESS_EXIT=2
$ grep -o '"ac": "ac4".\{0,400\}' .runtime/ac4-acceptance-audit.json | head -2
"ac": "ac4", "artifact_paths": [], "missing_or_invalid_artifacts": ["g4", "e2e",
 "missing artifact: ...\.runtime\evidence\g4\result.json",
 "missing artifact: ...\.runtime\evidence\e2e\ac-smoke.json"],
 "required_gates": ["g4", "e2e"], "status": "BLOCKED"
```

**AC-4 acceptance = BLOCKED，进程退出码 2。**（`scripts/verify_hydraulic_stack.py:1708` 把 `ac4` 映射到 `("g4", "e2e")`。）

### 2.3 默认 nowcast 物化 runner 实跑

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend"
$ echo '{"deadline_at":"2099-01-01T00:00:00+00:00","policy_bindings":{"flood_probability_id":"x","flood_rule_id":"y"}}' \
    | ../backend/.venv/Scripts/python.exe -B -m app.workers.nowcast_materializer; echo "EXITCODE=$?"
FORECAST_POLICY_ARTIFACT_HASH_MISMATCH
FORECAST_MATERIALIZER_UNAVAILABLE: this tree has no forecast materializer
(backend/app/services/forecast/ is not implemented); point NOWCAST_RUNNER_COMMAND at a real one
EXITCODE=4
```

**无论输入什么，runner 都不产出预报。** `backend/app/workers/nowcast_materializer.py:183-184` 是无条件出口：校验通过也打印 `FORECAST_MATERIALIZER_UNAVAILABLE` 并 `return EXIT_MATERIALIZER_UNAVAILABLE`(4)。

### 2.4 定向测试（本轮实跑）

```text
$ ./backend/.venv/Scripts/python.exe -m pytest \
    backend/tests/hydraulic/test_forecast_alerts.py \
    backend/tests/hydraulic/test_forecast_events.py \
    backend/tests/hydraulic/test_forecast_round1_regressions.py \
    backend/tests/hydraulic/test_nowcast.py \
    backend/tests/test_forecast_policy_artifact.py \
    backend/tests/test_services/test_sse_manager.py -q
69 passed, 1 warning in 0.23s

$ ./backend/.venv/Scripts/python.exe -m pytest backend/tests/api/test_hydraulic_api.py -q -k "forecast or sse or alert"
1 passed, 4 deselected, 1 warning in 0.28s

$ ./backend/.venv/Scripts/python.exe -m pytest backend/tests/acceptance/test_acceptance_matrix.py -q -k "ac4"
1 passed, 17 deselected, 1 warning in 0.29s

$ ./backend/.venv/Scripts/python.exe -m pytest backend/tests/hydraulic backend/tests/test_durable_worker_runtime.py -q
450 passed, 1 skipped, 1 warning in 40.18s
```

**这些通过项证明的是**：领域服务（告警状态机 / 30 分钟抑制 / 抑制键 / lead time / 风险排序）、SSE manager 的内存 replay/cursor/gap 逻辑、nowcast 调度与保留槽控制面代码**行为自洽**。它们**不**证明 nowcast 物化、durable outbox、跨 worker/restart replay 或 3 分钟 SLO——见 §3。

### 2.5 资产存在性核查

```text
$ ls scripts/ | grep -i bench
benchmark_hydraulic.py
generate_solver_benchmark_fixture.py

$ ls backend/tests/fixtures/performance/
solver

$ ls backend/tests/fixtures/hydraulic/
acceptance  compose  contracts  imports

$ ls backend/app/services/
__init__.py  hydraulic  inference_service.py  influxdb_service.py  mock_data_generator.py
notification_service.py  sse_manager.py  stream_service.py  system_status.py
```

- `scripts/benchmark_hydraulic_api.py` 与 `scripts/benchmark_hydraulic_nowcast.py` **不存在**（契约 §17.1.1 逐字要求的 G5 命令）。
- `backend/tests/fixtures/performance/nowcast/30-cycle-v1.json` **不存在**（`performance/` 下只有 `solver`）。
- `backend/app/services/forecast/` **不存在**（AC-4 的物化层）。
- `backend/tests/fixtures/hydraulic/forecast/` 与 `realtime/` **不存在**（G4 两个冻结 fixture）。

---

## 3. 逐条发现

### F-01 —— BLOCKER：nowcast 物化层不存在，且被接线的 runner 按设计拒绝产出预报
**是否可由代码修复：是**（工作量：新写一个物化模块 + 接线，不是一行补丁）

- `backend/app/workers/nowcast_materializer.py:1-33` 的模块 docstring 自述："the forecast materialization layer (`app.services.forecast.*`) … **does not exist in this tree**"。
- 同文件 `:174-184`：即使全部 lineage 校验通过，**无条件**打印 `FORECAST_MATERIALIZER_UNAVAILABLE` 并返回退出码 4。
- `docker-compose.yml:401` 把生产默认 runner 指向它：`NOWCAST_RUNNER_COMMAND: ${NOWCAST_RUNNER_COMMAND:-python,-m,app.workers.nowcast_materializer}`。
- `backend/app/workers/nowcast_worker.py:734-740` 把任意非零退出当作 attempt 失败 → run 进 `retry_wait` → 最终 failed。

**影响**：AC-4 的第一环断裂。没有 horizon point，就没有 `flood_forecast`、没有 `lead_time_minutes`、没有 `simulation_id`、没有告警、没有 SSE。SOURCE_PRD:23 与 AC-4（:33）在当前代码下**不可达**。

---

### F-02 —— BLOCKER：没有任何代码写 `forecast_alerts` / `event_outbox` / `forecast_alert_windows` / `forecast_alert_transitions`
**是否可由代码修复：是**（需要一个 PostgreSQL 适配器把 `ForecastAlertStore` 端口落到实库，并在 nowcast finalize 事务中调用）

```text
$ grep -rn "INSERT INTO hydraulic" backend/app/ --include=*.py
backend/app/workers/network_job_worker.py:275:  "INSERT INTO hydraulic.network_job_attempts "
backend/app/workers/network_job_worker.py:482:  "INSERT INTO hydraulic.network_imports "
backend/app/workers/network_job_worker.py:523:  "INSERT INTO hydraulic.validation_runs "
backend/app/workers/network_job_worker.py:546:  "INSERT INTO hydraulic.network_validation_issues "
backend/app/workers/nowcast_worker.py:994:      "INSERT INTO hydraulic.nowcast_runs "
```

- nowcast worker 只插入 `nowcast_runs`（`backend/app/workers/nowcast_worker.py:994`）。
- 消费侧（`backend/app/main.py:113,158,185,215,233`；`backend/app/workers/notification_worker.py:292`）全部只读 `hydraulic.event_outbox` 或只更新 `published_at`。
- `ForecastAlertService`（`backend/app/services/hydraulic/forecast_alerts.py:1366`）**只有测试引用**：

```text
$ grep -rn "ForecastAlertService" backend/ --include=*.py | grep -v "backend/build\|backend/.venv"
backend/app/services/hydraulic/forecast_alerts.py:1366:class ForecastAlertService:
backend/app/services/hydraulic/forecast_alerts.py:2153:    "ForecastAlertService",
backend/tests/acceptance/test_acceptance_matrix.py:530:    service = ForecastAlertService(
backend/tests/hydraulic/test_forecast_alerts.py:301,325,356,375,400,446: ...
backend/tests/hydraulic/test_forecast_round1_regressions.py:145: ...
```

**影响**：即使 F-01 被修复、有了 horizon point，也**没有**代码把它转成 alert + outbox。抑制、告警中心、SSE 三个 AC-4 交付面全部悬空。

---

### F-03 —— HIGH：REST forecast 读路径绑在永远为空的进程内字典，且 forecast 路由**不做** fail-closed
**是否可由代码修复：是**

- `backend/app/api/hydraulic_routes.py:310-311`：`forecast_runs` / `forecast_alerts` 在 `reset()` 中初始化为空 dict。
- 全仓库对这两个属性的访问只有**读**（`:3752`、`:3765`、`:3886`、`:3908`），**没有一处写**。
- `_require_durable_api_store()`（`:327-335`，production 下 `durable_backend=False` 时返回 503）只在 `:921, 1002, 2860, 2920, 3055, 3166, 3655` 被调用——`:3655` 属于 `submit_calibration`；**forecast 路由段 `:3771-4018` 一次都没调用**。

**后果（并且被测试固化为"预期行为"）**：

```text
$ sed -n '298,310p' backend/tests/api/test_hydraulic_api.py
    assert latest.status_code == 200
    assert latest.json()["data_state"] == "not_ready"
    ...
    assert ranking.status_code == 200
    assert ranking.json()["items"] == []
```

即 `/api/forecast/latest` 永远 200 + `not_ready`；`/api/forecast/alerts` 永远空列表；`/api/forecast/alerts/{id}/transitions` 永远 404。**在 production 环境下这些端点也不会 fail-closed 返回 503**，这与 `PRD_SUPPLEMENT.md:390` 要求的"policy 不可用 → forecast `not_ready` + `FORECAST_POLICY_UNAVAILABLE`"的显式降级语义不同——它是静默空态，且与 durable 事实源脱钩（违反 `PRD_SUPPLEMENT.md:105` "MUST NOT 以 Redis key、进程内变量……表示告警完成状态"）。

---

### F-04 —— HIGH（能力已实现，但 AC-4 不可观测）：30 分钟抑制在服务层正确且有测试，只有内存 store
**是否可由代码修复：是**（补持久化适配器 + 真实并发/重启测试）

**已实现且我实跑通过的部分**：

| 契约要求 | 实现位置 | 本轮验证 |
|---|---|---|
| suppression_key 唯一输入 = canonical JSON（不含 phase/level/confidence/expected_at/lead/simulation_id/run_id/时间） | `forecast_alerts.py:216-242`（canonical payload）、`:245-270`（`json.dumps(sort_keys=True, separators=(",",":"))` + SHA-256） | `test_forecast_alerts.py:239` `test_suppression_key_uses_only_the_frozen_canonical_identity`；`test_forecast_round1_regressions.py` 覆盖伪造 level/lineage |
| 半开窗 `[terminal_at, terminal_at+30m)` | `forecast_alerts.py:37` `SUPPRESSION_WINDOW = timedelta(minutes=30)`；`:1842` `cooldown_until=terminal_at + SUPPRESSION_WINDOW` | — |
| 窗内只增 `suppressed_count`，不建 alert/outbox | `forecast_alerts.py:1508-1528`（cooldown 分支只 `update_event_window`，返回 `AlertMutation(action="suppressed", alert=None)`） | `test_forecast_alerts.py:398-433` `test_terminal_cooldown_suppresses_inside_and_creates_at_exact_boundary`（断言 `cooldown_until == terminal_at + 30m`，窗内 `alert is None`，边界新建 `alert.id != first.alert.id`） |
| AC-4「窗口内不重复」验收集合 | `docs/hydraulic-twin/contracts/forecast.md:392` 定义：同一 key 在活动/冷却期只有一个 active 或 cooldown terminal alert，且只有一次 `alert.created` | `test_acceptance_matrix.py:527-576` `test_ac4_in_memory_forecast_suppression_uses_half_open_30_minute_window` — **本轮 1 passed** |

**未覆盖（AC-4 实际不可达的部分）**：

- 全部上述测试的 store 都是 `InMemoryForecastAlertStore`（`forecast_alerts.py:982`；`test_forecast_alerts.py:301/325/…`、`test_acceptance_matrix.py:530`）。**没有 PostgreSQL 适配器**——`ForecastAlertStore` / `ForecastAlertTransaction`（`forecast_alerts.py:874-904`）只是 `Protocol`。
- **无并发验证**：没有 20 并发候选、复用同一 suppression key 的竞争用例。
- **无跨 worker / 重启 replay 验证**。
- `_transaction()`（`:1391-1413`）对内存 store 做的是**事后快照回滚**（catch Exception 后把 dict/list 还原），不是数据库事务；这一点在注释里写明是"for simple test stores"。

---

### F-05 —— HIGH：G4 门禁与 AC-4 验收汇总双双 BLOCKED，且补 fixture 文件本身不足以通过
**是否可由代码修复：是**，但需注意"补文件"不是全部

`scripts/verify_hydraulic_stack.py` 的 g4 行为链：

1. `:718-723` `gate_domain()` 先跑 `fixture_step()`；任一 fixture 缺失 → `BLOCKED` 并**直接返回**，不执行后续。
2. fixture 存在后走 `optional_manifest_commands(root, manifest_path)` 解析 manifest 里的 `gate_commands`（`GATE_COMMANDS_KEY`，`:116`）。
3. `:746-765` 若 manifest **没有**声明可执行的 `gate_commands`，返回 `BLOCKED`，stderr 明写：`"... does not define executable gate_commands; static fixture presence is not gate evidence"`。

**含义**：交付 `backend/tests/fixtures/hydraulic/forecast/v1/manifest.json` 与 `backend/tests/fixtures/hydraulic/realtime/outbox-sse-v1.json` 两个文件**本身不够**；manifest 必须声明真正会跑的 harness 命令。契约要求的五个 AC-4 fixture（`IMPLEMENTATION_CONTRACT.md:1032`）目前一个都不存在（§2.5）。

---

### F-06 —— HIGH：G5 的 nowcast SLO harness 缺失，「每周期 ≤180s」不可测量
**是否可由代码修复：是**（需新写两个 benchmark 脚本 + 一个 fixture）

- `IMPLEMENTATION_CONTRACT.md:2513-2514` 逐字要求：
  ```
  python scripts/benchmark_hydraulic_api.py --fixture .../api/control-plane-v1.json --warmup-seconds 60 --duration-seconds 600 --vu 20 --profile postgis --output ...
  python scripts/benchmark_hydraulic_nowcast.py --fixture .../nowcast/30-cycle-v1.json --fake-cycles 100 --real-cycles 30 --profile postgis --output ...
  ```
- 实测 `ls scripts/ | grep -i bench` 只有 `benchmark_hydraulic.py`。
- 实测 `backend/tests/fixtures/performance/` 下只有 `solver`，`nowcast/30-cycle-v1.json` 与 `api/control-plane-v1.json` 均不存在。

**后果**：`PRD_SUPPLEMENT.md:589` 的「每周期 ≤180s；30 真实周期」与「30m 一个 created business event」在 AC-4 门禁下**无法被证明也无法被证伪**。

**相关但已实现的部分**（记为 partial，不记为 AC-4 通过）：`nowcast.py:288,308-310,332` 建模 `deadline_seconds=180` 并拒绝 `>180`；`nowcast.py:2055` `deadline = scheduled_at + timedelta(seconds=deadline_seconds)`；`nowcast_worker.py:91` `NOWCAST_SLOT_ID = 2` 保留槽 + `:294-320` lane 校验（`PRD_SUPPLEMENT.md:184` 要求 nowcast 槽禁借）；`nowcast_worker.py:688` runner `timeout_seconds=180.0`。**这些是控制面与配置面的 180s，没有一条被执行过**（因为 F-01，runner 永远非零退出）。

---

### F-07 —— MEDIUM：outbox→Redis→SSE 传输层是**真实**且有 durable 语义的，但只有假 store 测试
**是否可由代码修复：是**（补 live-DB 测试）

这一段是我确认**不是空壳**的部分：

| 组件 | 位置 | 语义 |
|---|---|---|
| Durable replay store | `backend/app/main.py:93-163` `DatabaseForecastReplayStore`，从 `hydraulic.event_outbox` 按 `sequence > cursor AND expires_at > now() AND occurred_at >= now()-retention_hours` 重放；`:129-133` 校验 envelope 身份与行一致 |
| 单逻辑 relay | `backend/app/main.py:166-235` `DatabaseForecastOutboxRelay`：`pg_try_advisory_xact_lock(0x4859444F)` 保证单 relay；`FOR UPDATE SKIP LOCKED LIMIT 100`；带 fencing 的 `UPDATE ... published_at`（`:215-224`） |
| 启动接线 | `backend/app/main.py:950-952`（`set_forecast_replay_store` + `forecast_outbox_source`）、`:1017`（db_ok 时 `asyncio.create_task(_forecast_relay_loop())`，循环体 `:344`）、`:983` `redis_client.on_forecast(_handle_redis_forecast)` |
| Redis 独立通道 | `backend/app/core/redis_client.py:25` `FORECAST_REDIS_CHANNEL = "scn:forecast"`、`:125-177` `publish_forecast_event`（发布前强制校验 `event_id/schema_version/event_type/sequence/network_id/nowcast_run_id/simulation_id/occurred_at/alert`） |
| SSE 端点 | `backend/app/api/hydraulic_routes.py:3985-4018` `GET /forecast/events`；cursor 过期 → 409 `SSE_CURSOR_EXPIRED` + `X-SSE-Earliest/Latest-Sequence` |
| Envelope 必填 | `backend/app/services/sse_manager.py:38-47` `_FORECAST_REQUIRED_FIELDS`（含 `simulation_id`） |

**缺口**：现有测试全部是 fake——`backend/tests/integration/test_backend_integration.py:142-178` 用一个只返回 0 的 `SubscriberlessRedis` stub；`:395-405` 只做 `isinstance` 断言。**没有一条针对 `DatabaseForecastReplayStore` / `DatabaseForecastOutboxRelay` 的真实 PostgreSQL 测试**，因此 G4 要求的"跨 worker/restart replay""producer `published_at` 与 relay `event_outbox.published_at` 分责""gap/resync"均**未验证**。

---

### F-08 —— MEDIUM：`/forecast/horizon` 从告警派生，不是 0–2h / 5 分钟步长的 horizon 序列
**是否可由代码修复：是**

`backend/app/api/hydraulic_routes.py:3823-3835`：

```python
points = [
    {
        "target": {...},
        "expected_at": item["expected_at"],
        "lead_time_minutes": item["lead_time_minutes"],
        "value": item.get("measurements", {}).get("depth_m"),
        "confidence": item["confidence"],
    }
    for item in alerts
    if item["target_type"] == target_type
    and item["target_id"] == target_id
    and item["lead_time_minutes"] <= horizon_minutes
]
```

`step_minutes` 在 `:3843` 只是把 `settings.NOWCAST_STEP_MINUTES`（`config.py:412`，`Literal[5]`）原样回显，**没有生成任何 5 分钟栅格**。且 points 只包含**越过阈值的告警**，不含未告警的 horizon 值。这不满足 SOURCE_PRD:23「0–2h、5 分钟步长」。

---

### F-09 —— LOW：REST 告警体缺 `simulation_id` / `nowcast_run_id`
**是否可由代码修复：是**（一行 dict 字段）

`backend/app/api/hydraulic_routes.py:3722-3744` 的 `_forecast_alert_response` 输出 `id/type/status/phase/level/target/expected_at/lead_time_minutes/measurements/confidence/confidence_method/risk_score/network_revision_id/target_manifest_*/version`，**没有** `simulation_id` 与 `nowcast_run_id`。

SOURCE_PRD:23 要求告警含 `simulation_id`。SSE envelope 顶层有这两个字段（`sse_manager.py:38-47`；路由 transition 事件在 `:3966-3968` 填充），所以 **SSE 路径满足、REST 路径不满足**。

---

### F-10 —— LOW（超出本轮后端范围，仅登记）：告警中心读的是 legacy `/alerts`，不是 forecast 告警源
**是否可由代码修复：是**（前端 owner）

`frontend/src/pages/AlertCenter.tsx:13` → `AlertPanel`；`frontend/src/services/api.ts:68` `api.get('/alerts', ...)`。全仓 grep `告警中心|AlertCenter|alert-center|/alerts` 未发现任何对 `/api/forecast/alerts` 的引用。即使 F-01～F-03 全部修复，AC-4 的"进入告警中心"仍不会发生。

`frontend/e2e/fixtures/ac-smoke-v1.json` 实测只含 `{schema, fixture_id, extends, extends_sha256}` 三个键，`ac4` 出现 0 次、`forecast` 0 次、`/api/sse` 0 次——**AC-4 没有任何 e2e 场景**。这与 §2.2 中 `e2e/ac-smoke.json` 缺失一致。

---

### F-11 —— 正面结论（不计为通过，但确认无造假）
- `backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json` 的 AC-4 条目：`"status": "NOT RUN"`，理由 `"The current test is a sequential in-memory service characterization with a fake artifact resolver. Durable transactions, nowcast, outbox, concurrency, restart replay and 180-second SLO evidence are absent."` — **准确，且与我独立得出的结论一致**。
- `docs/hydraulic-twin/implementation/forecast-alerts.md:5` 自述接线未完成 — **准确**。
- `docs/hydraulic-twin/implementation/nowcast.md:75` 与 `implementation/forecast-alerts.md:147` 都明确写了 G4/G5/AC-4 live gate 未运行 — **准确**。
- `VALIDATION_EVIDENCE.md` 的 16 项 final gates **不含** AC-4 — 与 AC-4 未通过一致。

**AC-4 没有任何一条被文档或矩阵勾成通过。**

---

## 4. AC-4 需求逐项对照

| AC-4 / FR-4 要求 | 状态 | 依据 |
|---|---|---|
| 默认每 10 分钟 nowcast | **partial** | 调度与时隙对齐已实现（`nowcast_worker.py:930-947`、`config.py:410` `NOWCAST_INTERVAL_MINUTES=10`），但无一次成功 run |
| 产生 `flood_forecast` / `pipe_surcharge` | **未实现** | F-01：物化层不存在，runner 退出码 4 |
| 告警含目标/预计时刻/lead_time_minutes/深度/置信度 | **partial** | 服务层字段齐全（`forecast_alerts.py:593-744`），REST 序列化亦齐全（`hydraulic_routes.py:3722-3744`），但**无数据可序列化**（F-02/F-03） |
| 告警含 `simulation_id` | **partial / REST 缺失** | F-09；SSE envelope 满足 |
| 进入告警中心 | **未实现** | F-10 |
| 进入 SSE | **partial（传输可用，无生产者）** | F-07（传输层真实）+ F-01/F-02（无事件可发） |
| 通知（SMTP/webhook） | **partial（消费侧真实，无事件）** | `notification_worker.py:282-320` 只读 `event_outbox`；无 producer |
| 预计/已发生视觉区分 | **partial** | `phase` 字段在服务层与 API 均存在（`forecast_alerts.py`、`hydraulic_routes.py:3727`）；UI 未审 |
| Top-N 风险 | **partial** | `risk.py:rank_top_risk` 有测试；`/api/forecast/risk-ranking` 恒空（F-03） |
| **30 分钟窗口内不重复** | **partial（服务层通过，AC-4 不可观测）** | F-04 |
| **nowcast→告警 ≤3 分钟** | **未验证且不可测** | F-06：harness 脚本与 fixture 缺失 |

---

## 5. 是否可由代码修复

**是——全部 10 条发现都属于代码/资产缺口，没有一条需要外部裁决、产品决策或新数据。** 但工作量不是对等的：

| 类别 | 发现 | 性质 |
|---|---|---|
| 需要**新写一个模块** | F-01 | 物化层（`app/services/forecast/`）不存在 |
| 需要**新写持久化适配器 + 接线** | F-02 | `ForecastAlertStore` 端口 → PostgreSQL；接到 nowcast finalize 事务 |
| 需要**重接读路径** | F-03 | forecast 路由改读 DB + 加 `_require_durable_api_store()` |
| 需要**新写测试资产** | F-05, F-06, F-07 | G4 manifest+fixture、两个 G5 benchmark 脚本 + fixture、live-DB replay/relay 测试 |
| **局部补丁** | F-04（并发/重启用例）、F-08（horizon 栅格）、F-09（加字段）、F-10（前端数据源） | — |

**修复顺序建议**：F-01 → F-02 → F-03 →（F-05/F-06 的 harness）→ F-07。在 F-01 与 F-02 之前修 F-03 会得到一个"更早、更明确地 503"的空系统；反之修完 F-01/F-02 而不补 F-05/F-06，AC-4 仍然拿不到门禁 PASS。

---

## 6. 本轮**未**执行 / 无法验证的项（明确登记，不折算）

| 项 | 状态 | 原因 |
|---|---|---|
| G4 行为断言 | **NOT RUN** | 门禁在 fixture 存在性处 BLOCKED 返回（§2.1），无行为断言被执行 |
| G5 nowcast/API harness | **NOT RUN（不存在）** | 脚本与 fixture 均不存在（F-06） |
| 100 fake slot + 30 真实周期 | **NOT RUN** | 依赖 F-01/F-06 |
| 真实 PostgreSQL 下的 alert/outbox 事务、跨 worker/restart replay | **NOT RUN** | 无 producer（F-02），且本轮未起 live DB 栈 |
| 两 API worker + durable event | **NOT RUN** | 同上 |
| 真实 Redis/PostgreSQL 下 `DatabaseForecastReplayStore` / `DatabaseForecastOutboxRelay` | **NOT RUN** | 仅 fake-store 测试存在（F-07） |
| Compose live 栈上的 `/api/forecast/*` 实际响应 | **NOT RUN** | 本轮未启动 Compose；F-03 由代码路径与既有 API 测试断言推定 |
| 告警中心 UI 是否展示"预计/已发生" | **NOT RUN** | 前端范围，未审 |
| 通知投递 retry/DLQ 端到端 | **NOT RUN** | 无 outbox 事件可投递；仅 `outcome_from_result` 单测（F-02/F-07） |
| `docs/hydraulic-twin/reviews/forecast-alerts.md`（2026-09-25，NOT_APPROVED）结论 | **参考，不作为本轮依据** | `VALIDATION_EVIDENCE.md:420-425` 记该批审查早于 2026-09-27 的 final gates，代码状态可能已变；本审计的每条发现均以本轮实测为准 |

---

## 7. 审计边界

- 本轮为**只读验收**：除新增本文件外未修改任何实现、测试、契约或 fixture。
- 新增的两个临时文件 `.runtime/g4-audit.json`、`.runtime/ac4-acceptance-audit.json` 是门禁命令的重定向输出，位于已被 `.gitignore` 覆盖的 `.runtime/` 下。
- 每条发现均以**本轮亲读的代码行**或**本轮亲跑的命令输出**为据；无法验证的一律在 §6 登记为未执行，未以任何推断补齐。
- `VALIDATION_EVIDENCE.md` 中的 16 项 final gates / 21 项 stages **本轮一条也未复跑**，本文不引用其结论作为 AC-4 的证据。
