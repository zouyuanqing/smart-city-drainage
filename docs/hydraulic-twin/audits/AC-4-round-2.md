# AC-4 独立验收（round 2）：nowcast 预测告警、SSE 与抑制

- 验收日期：2026-09-28
- 验收人：独立验收员（只读；本轮除新增本审计文件与 `.runtime/` 下的临时输出外，未修改任何实现、测试、契约或 fixture）
- 仓库：`smart-city-drainage`
- 基线：`git rev-parse --short=8 HEAD` = `ca99e048`（与 round 1 相同，**无新提交**）；`git status --porcelain --untracked-files=all | wc -l` = `806`（round 1 为 `732`，工作树已变动，**故 round 1 结论不能直接沿用，本轮逐条重测**）
- 验收对象：AC-4「nowcast 产生含提前量的 flood_forecast，进入告警中心与 SSE 且窗口内不重复」
- 前序：`audits/AC-4-round-1.md`（2026-09-28 03:20）。本轮对 round 1 的 F-01～F-10 逐条复核，**其中一条被本轮实测证伪**（见 F-03），其余成立。

---

## 0. 结论

**AC-4 = BLOCKED。** 与 round 1 相同的根因在当前工作树下依然成立：`nowcast → horizon point → alert → event_outbox → Redis → SSE` 链在第一环就断。

**本轮相对 round 1 的三点变化（均已实跑取证）**：

1. **一条 round-1 结论被证伪**：round 1 的 F-03 断言「production 下 forecast 路由不做 fail-closed，仍返回 200」。本轮 production profile 实测四个 forecast 读端点全部返回 **503 `DURABLE_STORE_UNAVAILABLE`**——`backend/app/main.py:1253` 的中间件已提供该保护。round 1 只看了路由体内是否调用 `_require_durable_api_store()`，遗漏了中间件。**该条判定作废并在此更正。**
2. **新增真实资产**（round 1 未提及）：`docker/hydraulic/forecast-policy/` 与 `docker/hydraulic/artifacts/` 已落盘，policy sidecar 经本轮重算**逐字匹配**；`frontend/e2e/fixtures/api-v1/` 有 forecast/SSE fixture，`frontend/e2e/run-ac-smoke.mjs` 的 **e2e 门禁本轮实跑 PASS**（但见 §3 的 F-10a：其 AC-4 断言落在 fixture 支撑的空态上）。
3. **F-02 被加强**：不仅「没有代码写 `forecast_alerts`/`event_outbox`」，而且 nowcast worker 的 finalize 事务**结构上就不可能**写——见 F-02 的 `complete()` 证据。

**诚实度：未发现文档勾选冒充实现。** `matrix.json` AC-4 仍 `status: "NOT RUN"`、`pass_nodeids: []`；`CONTRACT_INDEX.md:66` 记 NOT RUN；`acceptance_requirements.json` 的 AC-4 门禁要求比 round 1 更严（4 个门禁 + 7 条 required_observations）；`VALIDATION_EVIDENCE.md` 的 16 项 final gates 无 AC-4 条目。**问题仍是能力缺口，不是造假。**

---

## 1. 验收口径（本次读取的材料）

| 来源 | 与 AC-4 相关的条款 |
|---|---|
| `SOURCE_PRD.md:23`（FR-4） | 默认每 10 分钟 nowcast；0–2h、5 分钟步长；`flood_forecast` + `pipe_surcharge`；告警含**目标、预计时刻、lead_time_minutes、深度、置信度、simulation_id**；进入**告警中心、SSE、通知**并有**预计/已发生视觉区分**；Top-N 风险；**默认 30 分钟事件窗抑制** |
| `SOURCE_PRD.md:31` | nowcast 到告警 **≤3 分钟** |
| `SOURCE_PRD.md:33`（AC-4） | nowcast 产生含提前量的 flood_forecast，进入告警中心与 SSE 且**窗口内不重复** |
| `PRD_SUPPLEMENT.md:186-187` | SLO 从 `scheduled_for` 到 **alert + event outbox 原子提交**，P95 与 max 均 ≤180s |
| `PRD_SUPPLEMENT.md:189-208`（D-10） | 唯一状态机表；`resolved/expired` 终态；`observed` 不能 expire |
| `PRD_SUPPLEMENT.md:219-231`（D-11） | suppression_key 唯一输入；`[terminal_at, terminal_at+30m)` 半开窗；**终态后到达的 observed candidate 在冷却窗内同样 suppressed 并记录 receipt/suppressed metric** |
| `PRD_SUPPLEMENT.md:215` | policy config canonical bytes + sidecar；工件失效 fail-closed |
| `PRD_SUPPLEMENT.md:589` | AC-4 门禁口径：G4 + G5 nowcast harness；100 fake slot + 30 真实周期 + 两 API worker + artifact hash；**每周期 ≤180s**；30m 一个 created business event |
| `IMPLEMENTATION_CONTRACT.md:1032` | AC-4 必交 fixture 五路径 + sidecar/hash 漂移即 gate 失败 |
| `IMPLEMENTATION_CONTRACT.md:2494`（G4） | 完整 transition、policy sidecar、复合 FK、outbox single path、跨 worker/restart replay、gap/resync、30m suppression、delivery retry/DLQ |
| `IMPLEMENTATION_CONTRACT.md:2513-2514`（§17.1.1 G5） | 固定四条 harness 命令（含 `benchmark_hydraulic_api.py`、`benchmark_hydraulic_nowcast.py`） |
| `acceptance_requirements.json` → `cases["AC-4"]`（本轮实读） | `required_gates = ["g4","g5","e2e","g6"]`；5 个 artifact suffix；**7 条 required_observations**（含「29m59s suppression、30m boundary 与 observed-stage suppression 是 durable」「两 worker + 重启 exactly-once SSE」） |

---

## 2. 本轮实际执行的检查（完整命令 + 真实输出）

### 2.1 基线

```text
$ git rev-parse --short=8 HEAD
ca99e048
$ git status --porcelain --untracked-files=all | wc -l
806
$ git log --oneline -3
ca99e04 chore: remove stray isort diff log
```

### 2.2 G4 门禁（AC-4 的授权门禁）—— BLOCKED

```text
$ backend/.venv/Scripts/python.exe -B scripts/verify_hydraulic_stack.py --gate g4
PROCESS_EXIT=2
"status": "BLOCKED"
"missing_fixtures": ["backend/tests/fixtures/hydraulic/forecast/v1/manifest.json",
                     "backend/tests/fixtures/hydraulic/realtime/outbox-sse-v1.json"]
"stderr_summary": ["missing frozen fixtures: ...forecast/v1/manifest.json, ...realtime/outbox-sse-v1.json"]
```

报告头：`"commit": "ca99e0485fb12…"`、`"dirty_state": true`、`"profile": "portable"`。**门禁在 fixture 存在性一步即返回，未执行任何行为断言。**

### 2.3 AC-4 acceptance 汇总门禁 —— BLOCKED

```text
$ backend/.venv/Scripts/python.exe -B scripts/verify_hydraulic_stack.py --gate acceptance --ac ac4
PROCESS_EXIT=2
"ac": "ac4", "missing_or_invalid_artifacts": ["g4", "g5", "e2e", "g6",
  "missing artifact: ...\.runtime\evidence\g4\result.json", "...\g5\result.json",
  "...\e2e\ac-smoke.json", ...],
"required_gates": ["g4", "g5", "e2e", "g6"], "status": "BLOCKED"
```

对照 `ls .runtime/evidence/`：`g4` / `g5` / `e2e` 三个目录**均不存在**（现存 `ac8 ac8r2 compose-smoke dd-r7-* g6-* rev-dd-r8-* review-dd-*`）。

### 2.4 默认 nowcast runner 实跑 —— 拒绝产出预报

```text
$ cd backend
$ echo '{"deadline_at":"2099-01-01T00:00:00+00:00","policy_bindings":{...}}' \
    | ../backend/.venv/Scripts/python.exe -B -m app.workers.nowcast_materializer
FORECAST_POLICY_ARTIFACT_HASH_MISMATCH
FORECAST_MATERIALIZER_UNAVAILABLE: this tree has no forecast materializer
  (backend/app/services/forecast/ is not implemented); point NOWCAST_RUNNER_COMMAND at a real one
EXITCODE=4
```

### 2.5 e2e 门禁（AC-4 的 4 个 required_gates 之一）—— **PASS**（本轮首次实跑）

```text
$ export CHROME_PATH="C:\Program Files\Google\Chrome\Application\chrome.exe"
$ node frontend/e2e/run-ac-smoke.mjs --fixture frontend/e2e/fixtures/ac-smoke-v1.json \
       --output .runtime/ac4r2-e2e/ac-smoke.json
"status":"PASS","exit_code":0,"browser_executed":true
"browser": {"product":"Chrome/154.0.8037.57","executable":"…chrome.exe"}
scenario AC-3-AC-4-AC-6-operator-simulation-workflow  ac=AC-3,AC-4,AC-6,AC-7  status=PASS
scenario AC-7-viewer-simulation-read-only            ac=AC-7                status=PASS
scenario AC-7-unauthenticated-simulation-redirect    ac=AC-7                status=PASS
"browser_errors": []
```

该 AC-4 场景的 `requests` 中确实出现 `GET /api/forecast/events`、`GET /api/forecast/latest`、`GET /api/forecast/risk-ranking`；`steps` 中 `wait_for_selector [data-testid="forecast-empty"]` = PASS。
**但所有 API 响应来自 `frontend/e2e/fixtures/api-v1/manifest.json` 的 fixture**（见 F-10a），与真实后端无关。

### 2.6 production fail-closed 定向诊断（本轮新增，**证伪 round 1 的 F-03 判定**）

诊断脚本 `.runtime/ac4r2-prod-failclosed.py`（本轮自写，位于 gitignore 的 `.runtime/`；设 `DEPLOYMENT_ENVIRONMENT=production` 与一次性 RS256 密钥，用 `ASGITransport` 直接打路由，DB 依赖覆盖为 no-op）：

```text
$ cd backend && ../backend/.venv/Scripts/python.exe -B ../.runtime/ac4r2-prod-failclosed.py
GET /api/forecast/latest        -> 503  {"code":"DURABLE_STORE_UNAVAILABLE", ...}
GET /api/forecast/risk-ranking  -> 503  {"code":"DURABLE_STORE_UNAVAILABLE", ...}
GET /api/forecast/horizon       -> 503  {"code":"DURABLE_STORE_UNAVAILABLE", ...}
GET /api/forecast/alerts        -> 503  {"code":"DURABLE_STORE_UNAVAILABLE", ...}
```

### 2.7 定向测试（本轮实跑）

```text
$ backend/.venv/Scripts/python.exe -m pytest backend/tests/hydraulic/test_forecast_alerts.py \
    backend/tests/hydraulic/test_forecast_events.py backend/tests/hydraulic/test_forecast_round1_regressions.py \
    backend/tests/hydraulic/test_nowcast.py backend/tests/test_forecast_policy_artifact.py \
    backend/tests/test_services/test_sse_manager.py -q
69 passed, 1 warning in 0.25s

$ backend/.venv/Scripts/python.exe -m pytest backend/tests/api/test_hydraulic_api.py -q -k "forecast or sse or alert"
1 passed, 4 deselected, 1 warning in 0.30s

$ backend/.venv/Scripts/python.exe -m pytest backend/tests/acceptance/test_acceptance_matrix.py -q -k "ac4"
1 passed, 17 deselected, 1 warning in 0.31s

$ backend/.venv/Scripts/python.exe -m pytest backend/tests/hydraulic backend/tests/test_durable_worker_runtime.py -q
452 passed, 1 skipped, 1 warning in 28.18s        # round 1 为 450 passed；增量来自
                                                   # test_results_calibration.py（09-28 04:44），与 AC-4 无关
$ cd frontend && npx vitest run \
    src/features/simulation/integration/ForecastRiskPanel.test.tsx \
    src/features/simulation/integration/ForecastStreamBoundary.test.tsx
Test Files  2 passed (2)   Tests  5 passed (5)
```

### 2.8 资产存在性核查

```text
$ for p in <11 条契约/门禁要求路径>; do …; done
MISSING backend/tests/fixtures/hydraulic/forecast/v1/probability-calibration-v1.json
MISSING backend/tests/fixtures/hydraulic/forecast/v1/forecast-rules-v1.json
MISSING backend/tests/fixtures/hydraulic/forecast/v1/policy-config-v1.json
MISSING backend/tests/fixtures/hydraulic/forecast/v1/policy-config-v1.json.sha256
MISSING backend/tests/fixtures/hydraulic/forecast/v1/expected-ac4-v1.json
MISSING backend/tests/fixtures/hydraulic/forecast/v1/manifest.json
MISSING backend/tests/fixtures/hydraulic/realtime/outbox-sse-v1.json
MISSING backend/tests/fixtures/performance/nowcast/30-cycle-v1.json
MISSING backend/tests/fixtures/performance/api/control-plane-v1.json
MISSING scripts/benchmark_hydraulic_api.py
MISSING scripts/benchmark_hydraulic_nowcast.py

$ ls backend/app/services/forecast
ls: cannot access 'backend/app/services/forecast': No such file or directory
```

**契约 §17.1.1 / IMPLEMENTATION_CONTRACT:1032 逐字要求的 AC-4 资产，11 项中 0 项存在。** 另有真实新增资产（非 fixture 路径）：

```text
$ ls -R docker/hydraulic/
artifacts/  flood_forecast_probability_v1.json(+.sha256) flood_forecast_rule_v1.json(+.sha256)
            pipe_surcharge_probability_v1.json(+.sha256) pipe_surcharge_rule_v1.json(+.sha256) registry.json
forecast-policy/  forecast-policy.json  forecast-policy.json.sha256  seed-artifact-rows.sql
```

### 2.9 工件 sidecar 重算（定向诊断）

```text
# policy config —— 按 main.py:621-631 的 canonical 规则重算
recomputed = e8d77b12aa1e0edcb46262cbdd7810b9cdda5a8435832136bbffd3041b0ef7da
sidecar    = e8d77b12aa1e0edcb46262cbdd7810b9cdda5a8435832136bbffd3041b0ef7da
MATCH

# artifacts —— 两种口径都算
MISMATCH   flood_forecast_probability_v1.json  canonical=6a987967ecbd1b13  sidecar=eff9d4a3ae96a7c3
MISMATCH   flood_forecast_rule_v1.json         canonical=8c4ad0a74bec6a16  sidecar=6162d10306c1777a
MISMATCH   pipe_surcharge_probability_v1.json  canonical=d1f2a19dd3da31cf  sidecar=35f7551fd350f4d8
MISMATCH   pipe_surcharge_rule_v1.json         canonical=45d59fe5f8b971c1  sidecar=30b881c1a78b5eed
RAWMATCH   ×4  (raw-bytes 口径全部匹配)；registry.json 无 sidecar
```

---

## 3. 逐条发现

### F-01 —— BLOCKER（成立）：物化层不存在，被接线的 runner 按设计拒绝产出预报
**是否可由代码修复：是**（需新写 `app/services/forecast/` 物化模块，非一行补丁）

- `backend/app/services/forecast/` **不存在**（§2.8 实测）。
- `backend/app/workers/nowcast_materializer.py:1-33` docstring 自述该层「does not exist in this tree」；`:174-184` 校验通过后**无条件**打印 `FORECAST_MATERIALIZER_UNAVAILABLE` 并 `return EXIT_MATERIALIZER_UNAVAILABLE`(4)。本轮实跑 `EXITCODE=4`（§2.4）。
- `docker-compose.yml:401` 生产默认仍指向它：`NOWCAST_RUNNER_COMMAND: ${NOWCAST_RUNNER_COMMAND:-python,-m,app.workers.nowcast_materializer}`。
- `backend/app/workers/nowcast_worker.py:734-741` 任何非零退出 → `NOWCAST_RUNNER_FAILED` → attempt 失败 → `retry_wait` → 最终 failed。

**影响**：AC-4 第一环断裂。没有 horizon point 就没有 `flood_forecast`、没有 `lead_time_minutes`、没有告警、没有 SSE 事件。

---

### F-02 —— BLOCKER（成立并**加强**）：无任何代码写 `forecast_alerts`/`event_outbox`/`forecast_alert_windows`/`forecast_alert_transitions`；nowcast finalize 事务**结构上也无法**写
**是否可由代码修复：是**（需 PostgreSQL 适配器把 `ForecastAlertStore` 端口落到实库，并把它挂进 nowcast finalize 事务）

```text
$ grep -rn "INSERT INTO hydraulic" backend/app/ --include=*.py
network_job_worker.py:275  network_job_attempts      network_job_worker.py:482  network_imports
network_job_worker.py:523  validation_runs           network_job_worker.py:546  network_validation_issues
nowcast_worker.py:995      nowcast_runs              # ← 仅此一条 nowcast 写
```

- `hydraulic_store.forecast_runs` / `.forecast_alerts` 在 `hydraulic_routes.py:324-325` 初始化为空 dict；全仓库对二者的访问**只有读**（`:3833`、`:3846`、`:3967`、`:3989`），**零写**。
- `ForecastAlertService` 仍**只有测试引用**：`forecast_alerts.py:1372`（定义）、`test_acceptance_matrix.py:530`、`test_forecast_alerts.py:301/325/356/375/400/446`、`test_forecast_round1_regressions.py:145`。
- **本轮新增的决定性证据**：nowcast worker 的成功路径也写不出告警。
  - `nowcast_worker.py:749-760`：runner 的 stdout 只需 `{"status":"ok","result_sha256":"<64 hex>"}`；返回类型 `RunResult(status, result_sha256, stdout)`（`:671`）——**不解析任何 horizon / alert 内容**。
  - `nowcast_worker.py:457-485` `NowcastRepository.complete()`：只 `UPDATE hydraulic.nowcast_runs SET status='completed', issued_at=…, published_at=…` 并 `logger.info(...)`；**没有任何 alert / outbox / horizon 写入**。
  - `nowcast_worker.py:790-812` `run_once()`：`_attempt` → `repo.complete(claim, result_sha256=result.result_sha256)` → `self.completed += 1`；`result` 随即丢弃。
  - 契约 `PRD_SUPPLEMENT.md:186` 要求 SLO 终点是「alert + event outbox 原子提交」并与 run completed 同一 finalize 事务——**该事务当前只做 run 状态翻转**。

**影响（比 round 1 更强）**：即使把 `NOWCAST_RUNNER_COMMAND` 指向一个真实的物化器，**worker 也不会因为物化器产出了 horizon 而写出告警或 outbox 事件**。F-01 与 F-02 是两个独立的、都必须修的断点。

---

### F-03 —— HIGH（**部分更正**）：REST forecast 读路径仍绑在永远为空的进程内字典；但「production 不 fail-closed」的说法被本轮实测推翻
**是否可由代码修复：是**（读路径重接 DB + 打开 `durable_backend` 开关）

**(a) round-1 判定作废。** round 1 称 forecast 路由段一次都没调用 `_require_durable_api_store()`（这一点本轮复核**属实**：全部 7 个调用点为 `:940 :1081 :2939 :2999 :3134 :3245 :3736`，最后一个属于 `submit_calibration`），因而推出 production 不 fail-closed。**该推论遗漏了 `backend/app/main.py:1253` 的 `hydraulic_rate_limit` 中间件**：

- `main.py:1257-1279`：production 且 path ∈ `/api/networks…/api/forecast` 且 `durable_backend` 为假 → **503 `DURABLE_STORE_UNAVAILABLE`** + `Retry-After: 5`。
- `main.py:1280-1298`：production 且 `/api/forecast*` 且 `_forecast_runtime_ready()` 为假 → **503 `FORECAST_POLICY_UNAVAILABLE`** + `Retry-After: 30`（`main.py:789`）。
- `main.py:1300-1344`：`sse_connect` / `forecast_transition` / `forecast_read` 独立限流 bucket，限流器不可用时 503 `RATE_LIMITER_UNAVAILABLE`，超限 429 + `Retry-After`。

§2.6 的 production 实测证实四个 forecast 读端点全部 503。**`PRD_SUPPLEMENT.md:290` 要求的 fail-closed + 独立 bucket 在生产 profile 下确实存在。round 1 的该条判定不成立。**

**(b) 仍然成立的缺陷**：
- 读路径本身未修：`hydraulic_routes.py:3833/3846/3967/3989` 读的是 `:324-325` 初始化的空 dict。一旦 `durable_backend` 为真，这些路由返回的仍是**永远为空**的进程内状态——与 `PRD_SUPPLEMENT.md:105`「MUST NOT 以…进程内变量…表示告警完成状态」冲突。
- 非 production（test/development）下仍返回 200 + `not_ready`（`backend/tests/api/test_hydraulic_api.py:297-311` 本轮实跑通过），即开发态看到的是一个**永不填充**的空态而非显式降级。

---

### F-04 —— HIGH（成立，能力已实现但 AC-4 不可观测）：30 分钟半开抑制在服务层正确且有测试；仅内存 store，无并发/重启/observed-after-terminal 证据
**是否可由代码修复：是**（补持久化适配器 + 真实并发/重启/observed 冷却用例）

**已实现且本轮实跑验证的部分**：

| 契约要求 | 实现位置 | 本轮验证 |
|---|---|---|
| suppression_key 唯一输入 = canonical JSON（不含 phase/level/confidence/expected_at/lead/simulation_id/run_id/时间） | `forecast_alerts.py:246-271`（`calculate_suppression_key` + `json.dumps(sort_keys=True, separators=(",",":"), allow_nan=False)` + SHA-256）；`canonical_suppression_payload` 见 `:216-242` | `test_forecast_alerts.py` 全体通过 |
| 半开窗 `[terminal_at, terminal_at+30m)` | `forecast_alerts.py:37` `SUPPRESSION_WINDOW = timedelta(minutes=30)`；`:1848` `cooldown_until=terminal_at + SUPPRESSION_WINDOW` | `test_forecast_alerts.py:398-437` |
| 窗内只增 `suppressed_count`，不建 alert/outbox | `forecast_alerts.py:1514-1533`（cooldown 分支只 `update_event_window` 并返回 `AlertMutation(action="suppressed", alert=None, reason="cooldown")`） | 同上 |
| **29m59s suppressed、30m 边界新建 `alert_id`** | — | `test_forecast_alerts.py:415-437`：`issued_at=terminal_at+29m59s → action=="suppressed"`, `len(store.alerts)==1`；`issued_at=terminal_at+30m → action=="created"` 且 `boundary.alert.id != first.alert.id`，`len==2`。**本轮实跑通过** |
| AC-4「窗口内不重复」验收集合 | `test_acceptance_matrix.py::test_ac4_in_memory_forecast_suppression_uses_half_open_30_minute_window` | **本轮 1 passed** |

**未覆盖**：
- store 仍只有 `InMemoryForecastAlertStore`（`forecast_alerts.py:983`）；`ForecastAlertStore`（`:901`）与 `ForecastAlertTransaction`（`:874`）仍是 `Protocol`，**无 PostgreSQL 实现**。因此 `acceptance_requirements.json` AC-4 要求的「durable 29m59s/30m/observed-stage」一条都未验证。
- **无并发竞争验证**（同 suppression key 的多 worker 竞争）。
- **无跨 worker / 重启 replay 验证**。
- `_transaction()`（`:1398-1419`）对内存 store 做的是事后快照回滚，非数据库事务。
- **本轮新增缺口**：`PRD_SUPPLEMENT.md:229` 要求「predicted 已终态后才收到 observed candidate，在冷却窗内同样 suppressed；**记录 observation receipt 和 suppressed metric**」。实现是**直接抛错**：`forecast_alerts.py:1696-1698` `if alert.status not in _ACTIVE_STATUSES or alert.phase != "predicted": raise ForecastTransitionError("only predicted active alerts can be observed")`。既不返回 `suppressed` 也不增 `suppressed_count`、不记 receipt。全仓无该场景的测试（`grep "def test_.*observ"` 无 forecast 告警侧用例）。

---

### F-05 —— HIGH（成立）：G4 门禁 BLOCKED，且补 fixture 文件本身不足以通过
**是否可由代码修复：是**（需写 manifest + 真实 harness，不是补空文件）

- §2.2 实跑：exit 2、BLOCKED、两个冻结 fixture 缺失。
- `verify_hydraulic_stack.py:109-112` `GATE_FIXTURES["g4"]` 固定为 `forecast/v1/manifest.json` + `realtime/outbox-sse-v1.json`。
- `:727-765` `gate_domain()`：`fixture_step()` 非 PASS 即 `return steps`（**不执行任何行为断言**）；fixture 就绪后走 `optional_manifest_commands()`，`:116` `GATE_COMMANDS_KEY = "gate_commands"`，manifest 未声明可执行命令则返回 BLOCKED 并写明「static fixture presence is not gate evidence」。
- 契约 §17.1.1 之外的 5 个 AC-4 fixture（`IMPLEMENTATION_CONTRACT.md:1032`）也全部缺失（§2.8）。

---

### F-06 —— HIGH（成立）：G5 的 API/nowcast harness 缺失，「每周期 ≤180s」「100 fake slot + 30 真实周期」不可测量
**是否可由代码修复：是**（需新写 2 个 benchmark 脚本 + 2 个 fixture）

- 契约 `IMPLEMENTATION_CONTRACT.md:2513-2514` 逐字要求 `scripts/benchmark_hydraulic_api.py` 与 `scripts/benchmark_hydraulic_nowcast.py` + `performance/{api,nowcast}` fixture；§2.8 实测 **4 项全缺**（`ls scripts/ | grep bench` 只有 `benchmark_hydraulic.py`）。
- **控制面 180s 存在但从未执行**：`nowcast_worker.py:688` `timeout_seconds: float = 180.0`；`config.py:413` `NOWCAST_DEADLINE_SECONDS: int = Field(default=180, gt=0, le=180)`；`docker-compose.yml:393` `NOWCAST_DEADLINE_SECONDS: ${…:-180}`；`config.py:411-412` `NOWCAST_HORIZON_MINUTES=120` / `NOWCAST_STEP_MINUTES: Literal[5] = 5`。因 F-01，runner 永远非零退出。
- 现存 `.runtime/evidence/` 无 `g5/` 目录 → G5 从未产出 artifact。

---

### F-07 —— MEDIUM（成立）：outbox→Redis→SSE 传输层真实且有 durable 语义，但**仍无 live-DB 测试、且无生产者**
**是否可由代码修复：是**（补真实 PostgreSQL/Redis 测试 + 生产者）

| 组件 | 位置 | 语义 |
|---|---|---|
| Durable replay store | `main.py:93-135` `DatabaseForecastReplayStore`：`sequence > :after_sequence AND expires_at > clock_timestamp() AND occurred_at >= clock_timestamp() - make_interval(...)`；`:129-133` 校验 envelope `sequence`/`event_id` 与行一致，否则 `ValueError` |
| 单逻辑 relay | `main.py:166+` `DatabaseForecastOutboxRelay`：`pg_try_advisory_xact_lock(0x4859444F)`；`ORDER BY sequence LIMIT 100 FOR UPDATE SKIP LOCKED`；`:213-220` 带 fencing 的 `UPDATE … published_at = clock_timestamp() WHERE sequence=:sequence AND event_id=:event_id AND published_at IS NULL`，`rowcount != 1` 抛 `forecast outbox acknowledgement lost its fence` |
| 启动接线 | `main.py:950-952`（`set_forecast_replay_store` + `app.state.forecast_outbox_source`）、`:1017`（db_ok 时 `asyncio.create_task(_forecast_relay_loop())`，循环体 `:344`）、`:983` `redis_client.on_forecast(_handle_redis_forecast)` |
| Compose 服务 | `docker-compose.yml:434-470` `outbox-relay`（`python -m app.workers.outbox_relay`，独立 health 端口 8023） |
| SSE 端点 | `hydraulic_routes.py:4066+` `GET /forecast/events`；`Last-Event-ID` 过期 → 409 `SSE_CURSOR_EXPIRED` + `X-SSE-Earliest/Latest-Sequence` |
| Envelope 必填 | `sse_manager.py:38-47` `_FORECAST_REQUIRED_FIELDS`（含 `simulation_id`、`nowcast_run_id`） |
| 通知消费侧 | `notification_worker.py:282-320` `load_event` 读 `hydraulic.event_outbox` 并校验 envelope 身份 |

**缺口**：`grep -rn "DatabaseForecastReplayStore|DatabaseForecastOutboxRelay" backend/` 只命中 `main.py`、`outbox_relay.py:159-161` 与 `tests/integration/test_backend_integration.py:398/403`——后者是 `isinstance` 断言。**没有任何针对真实 PostgreSQL 的 replay/relay 测试**，G4 要求的「跨 worker/restart replay」「producer `published_at` 与 relay `published_at` 分责」「gap/resync」**全部未验证**。且因 F-02，**永远没有事件可重放**。

---

### F-08 —— MEDIUM（成立）：`/forecast/horizon` 从告警派生，不是 0–2h / 5 分钟步长的 horizon 序列
**是否可由代码修复：是**

`hydraulic_routes.py:3891-3927`：`points` 由 `alerts` 过滤生成（`target_type`/`target_id`/`lead_time_minutes <= horizon_minutes`），值取 `item["measurements"]["depth_m"]`；`"step_minutes": settings.NOWCAST_STEP_MINUTES` 只是**回显**（`config.py:412` `NOWCAST_STEP_MINUTES: Literal[5] = 5`），**没有生成任何 5 分钟栅格**，且只含越过阈值的告警。不满足 `SOURCE_PRD.md:23` 的「0–2h、5 分钟步长」。

---

### F-09 —— LOW（成立）：REST 告警体缺 `simulation_id` / `nowcast_run_id`
**是否可由代码修复：是**（加两个 dict 字段 + store 映射）

`hydraulic_routes.py:3693-3719` `_forecast_alert_response()` 输出 `id/type/status/phase/level/target/expected_at/lead_time_minutes/measurements/confidence/confidence_method/risk_score/network_revision_id/target_manifest_id/target_manifest_sha256/version`——**无 `simulation_id`、无 `nowcast_run_id`**。`SOURCE_PRD.md:23` 明确要求告警含 `simulation_id`。
SSE 路径满足：transition 事件 envelope 在 `hydraulic_routes.py:4046-4049` 填了 `nowcast_run_id` 与 `simulation_id`。**故 REST 不满足、SSE 满足。**

---

### F-10 —— MEDIUM（范围修正）：告警中心不接 forecast 源；Dashboard forecast 面已存在且 e2e 门禁 PASS
**是否可由代码修复：是**（前端 owner：告警中心数据源）

**(a) 告警中心仍读 legacy `/alerts`**
`frontend/src/pages/AlertCenter.tsx` 对 `forecast`/`Forecast` 的 grep **0 命中**；它用 `alertAPI` → `frontend/src/services/api.ts:68` `api.get('/alerts', …)`。**AC-4 的「进入告警中心」在 UI 上不成立**，即使 F-01～F-03 全部修复。

**(b) 但 round-1 说得不够：forecast 前端面已存在**（本轮新增事实）
- `frontend/src/features/simulation/forecast/ForecastDashboard.tsx:50` `GET /forecast/latest`、`:62` `GET /forecast/risk-ranking`；渲染「预计/已发生」（`observed`/`predicted` label，`summary` 文案 `当前有效 {{active}} 项 · 预计 {{predicted}} 项 · 已发生 {{observed}} 项`）与 `Top-{{topN}} 风险榜`。
- `frontend/src/features/simulation/forecast/stream.ts:221` 订阅 `/api/forecast/events`；`:111` 处理 `forecast.resync_required`；`:49` 过滤 `flood_forecast`/`pipe_surcharge`。
- `frontend/src/components/layout/ForecastRiskCard.tsx` 挂载该面板；`ForecastRiskPanel`/`ForecastStreamBoundary` 有 vitest（§2.7，5 passed，但均通过注入 `fetchSnapshot`/`fetchRiskRanking` props，**只证明渲染、不证明数据**）。

**(c) F-10a —— e2e PASS 的证据边界（本轮新增）**
本轮 e2e 门禁 PASS（§2.5），但其 AC-4 断言完全落在**fixture 支撑的空态**上：
- `frontend/e2e/fixtures/api-v1/manifest.json` 提供 `forecast_latest`/`forecast_risk`/`forecast_events` 三个 canned 响应；`forecast_events.sse` 为脚本合成的 `alert.created` 事件流（`count:10, initial_delay_ms:1000, interval_ms:500, increment_sequence:true`），`data` 取自 `current-event.json`（`event_id:"event-1"`, `nowcast_run_id:"run-1"`, `alert.id:"a1"`, `type:"flood_forecast"`, `lead_time_minutes:30`）。
- 被断言的步骤是 `wait_for_selector [data-testid="forecast-empty"]` = PASS——**即「空态渲染」被固化成了期望**。

**结论**：e2e PASS 证明「前端会发起 forecast 三个请求、能消费 forecast SSE envelope、能渲染空态」，**不证明**任何 nowcast→alert→outbox→SSE 的真实链路。它不能替代 G4/G5 的 durable 证据，`acceptance_requirements.json` 把 e2e 列为 AC-4 的 4 个必需门禁之一这一点本身合理，但**该 artifact 目前不足以支撑 AC-4**。

---

### N-01 —— MEDIUM（新增）：production 下整个 hydraulic API 面被永久 503，因为 `durable_backend` 没有任何置真路径
**是否可由代码修复：是**（与 F-02 同一件工作：加 durable repository 接线）

`hydraulic_routes.py:311` `self.durable_backend = False`；全仓 `grep -rn "durable_backend" backend/app backend/tests` 只有 **3 处**：这一处赋值 + `hydraulic_routes.py:343` 与 `main.py:1273` 两处 `getattr(..., False)` 读。**没有任何代码把它置为 True**。
后果：`main.py:1257-1279` 的 503 守卫是**无条件**的，production 下 `/api/networks`、`/api/simulations`、`/api/calibrations`、`/api/forecast*` 全部 503。AC-4 的 REST 读路径**不存在可达的生产模式**。§2.6 实测佐证。

---

### N-02 —— LOW/MEDIUM（新增）：工件 sidecar 的哈希口径与 policy config 不同，且当前无任何校验器
**是否可由代码修复：是**（在 G4 harness 中确定并统一口径）

- `docker/hydraulic/forecast-policy/forecast-policy.json.sha256` = **canonical bytes** 的 SHA-256，本轮重算**逐字匹配**（§2.9），与 `main.py:621-631` 的实现及 `PRD_SUPPLEMENT.md:215` 的要求一致。
- `docker/hydraulic/artifacts/*.json.sha256` = **raw file bytes** 的 SHA-256：canonical 口径 4 项全 MISMATCH，raw 口径 4 项全 RAWMATCH；`registry.json` 无 sidecar（其内部 `sha256` 字段与 raw 口径一致）。
- 契约**未**规定 `artifact_sha256` 的规范化口径（只规定 policy config sidecar 为 canonical）。目前**没有任何代码或门禁校验这些 sidecar**。一旦 G4 harness 按 canonical 口径实现，这 4 个已落盘工件会立即 fail-closed。
- 建议：G4 前先冻结单一口径（建议与 policy config 一致的 canonical bytes）并重算 `docker/hydraulic/artifacts/*.sha256` 与 `registry.json`，或明确写入契约。

---

### N-03 —— LOW（新增）：契约 AC 状态表的 AC-4 理由文字已过时
**是否可由代码修复：是**（一行文档编辑）

`IMPLEMENTATION_CONTRACT.md:2550` 记 `| AC-4 | NOT RUN（无 forecast/SSE 实现） |`。**状态 NOT RUN 正确**（4 个必需门禁无一有 durable 证据），但括号里的理由不准确：SSE 传输层（`main.py:93/166`、`sse_manager.py`、`hydraulic_routes.py:4066`）、forecast 领域服务（`forecast_alerts.py`）、前端 forecast 面与 e2e fixture **都存在**。真实缺口是「没有生产者、没有持久化适配器、没有门禁证据」，不是「无实现」。**这不是虚报 PASS，但会误导后续修复排期。**

---

### N-04 —— 正面结论（不计为 AC-4 通过，但确认无造假）
- `backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json` AC-4：`"status": "NOT RUN"`、`"pass_nodeids": []`，理由「Durable transactions, nowcast, outbox, concurrency, restart replay and 180-second SLO evidence are absent.」——**准确，与本轮独立结论一致**。
- `acceptance_requirements.json` AC-4 比 round 1 更严：`required_gates = ["g4","g5","e2e","g6"]`（round 1 时门禁脚本映射为 `("g4","e2e")`），并列出 7 条 `required_observations`，其中「29m59s suppression、30m boundary and observed-stage suppression are durable」「authenticated canonical SSE replays exactly once across two workers and restart」**逐条对应本轮 F-04/F-07 的缺口**。这份需求文件是准确的。
- `docs/hydraulic-twin/implementation/forecast-alerts.md:5` 自述「PostgreSQL 持久化适配器及 nowcast/API/SSE 接线未在本任务 ownedPaths 内完成」——**准确**。
- `docs/hydraulic-twin/implementation/nowcast.md:75` 自述「没有执行 100 个 fake slot、30 个真实周期、PostgreSQL/Redis 双 worker 或 180 秒 SLO benchmark」——**准确**。
- `CONTRACT_INDEX.md:66`：AC-4 = NOT RUN，缺口记为「durable nowcast/outbox/notification/180 s/restart replay 缺失」——**准确**。
- `VALIDATION_EVIDENCE.md` 的 16 项 final gates 无 AC-4 条目；`:377-379` 记 forecast-alerts/forecast-events/forecast-ui 三份审查均不批准。
- `docs/hydraulic-twin/reviews/forecast-events.md:5`（5 High / 1 Medium）与 `reviews/nowcast.md:5`（5 high / 3 medium）均为不批准。本轮抽查其中两条：nowcast 审查的 H5（`ScheduledNowcast` 缺 lineage/policy 身份）**已修复**——`nowcast.py:1511-1540` 现含 `network_revision_id`/`target_manifest_*`/四个 artifact id+sha256/`forecast_policy_config_sha256`；M3（lead time 用创建时间）**已修复**——`nowcast.py:1577-1589` 优先取 `issued_at`，`forecast_alerts.py:273-282` 用 `max(0, floor((expected-issued)/60s))` 并拒绝 >2h。**但这些都不改变 F-01/F-02 的结构性阻断。**

**AC-4 依然没有任何一条被文档、矩阵或门禁勾成通过。**

---

## 4. AC-4 需求逐项对照

| AC-4 / FR-4 要求 | 状态 | 依据 |
|---|---|---|
| 默认每 10 分钟 nowcast | **partial** | 调度/对齐/保留槽已实现（`nowcast_worker.py:930` `align_slot`、`config.py:410` `NOWCAST_INTERVAL_MINUTES=10`、`nowcast_worker.py:91` `NOWCAST_SLOT_ID=2`），但**无一次成功 run**（F-01） |
| 0–2h / 5 分钟步长 horizon | **未实现** | F-08：`/forecast/horizon` 无栅格，且数据源为空（F-02/F-03） |
| 产生 `flood_forecast` / `pipe_surcharge` | **未实现** | F-01 物化层不存在；F-02 finalize 事务不写 |
| 告警含目标/预计时刻/lead_time_minutes/深度/置信度 | **partial** | 服务层字段齐全（`forecast_alerts.py:593-750`）、REST 序列化齐全（`hydraulic_routes.py:3693-3719`），**但无数据可序列化** |
| 告警含 `simulation_id` | **partial（REST 缺失）** | F-09；SSE envelope 满足（`hydraulic_routes.py:4046-4049`） |
| **进入告警中心** | **未实现** | F-10a：`AlertCenter.tsx` 零 forecast 引用，读 legacy `/alerts` |
| Dashboard forecast 卡片 / Top-N 风险榜 | **已实现（UI 层）** | `ForecastDashboard.tsx:50,62`、`ForecastRiskCard.tsx`；但数据源恒空（N-01） |
| **进入 SSE** | **partial（传输可用，无生产者）** | F-07 传输层真实 + F-01/F-02 无事件可发 |
| 通知（SMTP/webhook） | **partial（消费侧真实，无事件）** | `notification_worker.py:282-320` 只读 outbox；无 producer |
| 预计/已发生视觉区分 | **已实现（UI 层）** | `ForecastDashboard.tsx` observed/predicted label + summary 文案；e2e 实测渲染通过（空态） |
| **30 分钟窗口内不重复** | **partial（服务层通过，AC-4 不可观测）** | F-04：29m59s/30m 边界本轮实跑通过，但仅内存 store，无并发/重启证据；observed-after-terminal 走抛错而非 suppressed |
| **nowcast→告警 ≤3 分钟** | **未验证且不可测** | F-06：两个 harness 脚本 + 两个 fixture 全缺 |
| 工件 fail-closed / policy sidecar | **partial** | policy loader + sidecar 校验真实（`main.py:580-637`）且本轮重算 MATCH；N-02 工件口径未统一；G4 无 artifact 证据 |

---

## 5. 是否可由代码修复

**是——全部 13 条发现（F-01～F-10、N-01～N-03）都属于代码/资产缺口，没有一条需要外部裁决、产品决策或新数据来源。** 工作量不均等：

| 类别 | 发现 | 性质 |
|---|---|---|
| 需**新写一个模块** | F-01 | 物化层 `app/services/forecast/` |
| 需**新写持久化适配器 + 改写 finalize 事务** | F-02、N-01 | `ForecastAlertStore` → PostgreSQL；把 alert/outbox 写进 `nowcast_worker.py:457-485` 的 `complete()`；置 `durable_backend` |
| 需**重接读路径** | F-03(b) | forecast 路由改读 DB（503 守卫已存在，只需打开开关） |
| 需**补语义分支 + 测试** | F-04 | observed-after-terminal 在冷却窗内应 suppressed + 记 receipt/计数；并发竞争用例；跨 worker/重启用例 |
| 需**新写测试资产** | F-05、F-06、F-07 | G4 manifest + `gate_commands`；两个 G5 benchmark 脚本 + 两个 fixture；live-DB replay/relay 测试 |
| **局部补丁** | F-08（horizon 栅格）、F-09（加字段）、F-10a（前端数据源）、N-02（口径统一+重算 sidecar）、N-03（一行文档） | — |

**修复顺序建议**：`F-01 → F-02（含 N-01）→ F-03(b) → F-04 语义 → F-05/F-06 harness → F-07`。
先修 F-03 只会得到一个「更早、更明确地 503」的空系统；先修 F-01 而不修 F-02，则物化器算出的 horizon 依然无人转成告警（`complete()` 会直接丢弃 runner 结果）。反之，两者补齐而不补 F-05/F-06，AC-4 仍拿不到门禁 PASS。

**e2e 不应作为 AC-4 的主要证据**：本轮它 PASS，但断言落在 fixture 空态上（F-10a）。真正决定 AC-4 的是 G4（durable 状态机/抑制/outbox/replay）与 G5（100 fake slot + 30 真实周期 + 每周期 ≤180s）。

---

## 6. 本轮**未**执行 / 无法验证的项（明确登记，不折算）

| 项 | 状态 | 原因 |
|---|---|---|
| G4 行为断言（transition/hash/时序/replay/DLQ） | **NOT RUN** | 门禁在 fixture 存在性处 BLOCKED 返回（§2.2），无行为断言被执行 |
| G5 API/nowcast harness | **NOT RUN（不存在）** | 2 个脚本 + 2 个 fixture 全缺（F-06） |
| 100 fake slot + 30 真实周期、每周期 ≤180s | **NOT RUN** | 依赖 F-01/F-06 |
| 真实 PostgreSQL 下的 alert/outbox 事务 | **NOT RUN** | 无 producer（F-02），本轮未起 live DB 栈 |
| 两 API worker + 重启 exactly-once SSE replay | **NOT RUN** | 同上 |
| 真实 Redis/PostgreSQL 下 `DatabaseForecastReplayStore` / `DatabaseForecastOutboxRelay` | **NOT RUN** | 仅 fake-store 测试（F-07） |
| 通知投递 retry/DLQ 端到端 | **NOT RUN** | 无 outbox 事件可投递 |
| Compose live 栈上的 `/api/forecast/*` 实际响应 | **NOT RUN** | 未启动 Compose；§2.6 用 ASGITransport 在 production profile 下取得 503 证据，属**进程内**结果，不等于 live 栈 |
| development/test profile 下 forecast 端点的直接响应 | **NOT RUN（直接）** | 诊断脚本在 development profile 下因鉴权需要 DB session 而中止；改由 `test_hydraulic_api.py:297-311`（本轮 1 passed）作为间接证据 |
| AC-4 前端其余 UI 面（告警中心、地图预计/已发生图层） | **未审** | 本轮只跑了两个 forecast vitest 与 e2e ac-smoke |
| 全量 pytest / vitest / lint / mypy / build | **NOT RUN** | AC-7 范围；本轮只跑 §2.7 列出的定向集合 |
| `reviews/forecast-alerts.md`（NOT_APPROVED）、`forecast-events.md`、`nowcast.md` | **参考，非本轮依据** | 三份均早于 2026-09-27 的 final gates；本审计每条发现以本轮亲读代码或亲跑命令为准，仅在 N-04 抽查两条 |

---

## 7. 审计边界

- 本轮为**只读验收**：未修改任何实现、测试、契约或 fixture。新增文件只有本审计文件，以及 `.runtime/` 下已被 `.gitignore` 覆盖的临时产物：`ac4r2-g4.json`、`ac4r2-acceptance.json`、`ac4r2-e2e/ac-smoke.json`、`ac4r2-prod-failclosed.py`、`ac4r2-dev-forecast.py`（后者运行失败，见 §6）、`ac4r2-rs256-{private,public}.pem`（一次性诊断密钥，无生产用途）。
- 结论对应**当前工作树**（`ca99e048` + 806 项未提交改动），非某个已提交候选。
- 每条发现均以本轮亲读代码行或亲跑命令输出为据；无法验证的一律在 §6 登记为未执行，未以推断补齐。
- `VALIDATION_EVIDENCE.md` 的 16 项 final gates / 21 项 stages **本轮一条也未复跑**，本文不引用其结论作为 AC-4 的证据；仅 e2e 门禁（AC-4 的 4 个 required_gates 之一）被本轮实跑，并已在 F-10a 说明其证据边界。
