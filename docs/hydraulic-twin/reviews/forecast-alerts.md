# 预测告警、抑制与风险：独立代码审查

- **Module:** `forecast-alerts`
- **审查日期:** 2026-09-25
- **结论:** **NOT_APPROVED / `approved=false`**
- **审查方式:** 只读核对冻结实施契约、任务规格、实施摘要、真实实现和 focused tests；未修改实现代码、测试或契约；未重复最终全量门禁。

## 1. 规范、范围与真实文件

本轮以 `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md` 和 `docs/hydraulic-twin/PRD_SUPPLEMENT.md` 为规范依据，并核对了任务规格中列出的 `SOURCE_PRD.md`。`docs/hydraulic-twin/contracts/forecast.md` 明确标为 **SUPERSEDED / NON-NORMATIVE**，没有用其中已被取代的字段或命令覆盖 v3 契约。

实际读取的实现与测试：

- `backend/app/services/hydraulic/forecast_alerts.py`
- `backend/app/services/hydraulic/risk.py`
- `backend/tests/hydraulic/test_forecast_alerts.py`
- `docs/hydraulic-twin/implementation/forecast-alerts.md`
- 相关持久化/集成上下文：`backend/app/models/hydraulic_models.py`、`backend/app/api/hydraulic_routes.py`、`backend/app/services/notification_service.py`

任务规格要求的模块行为是生成 `flood_forecast`/`pipe_surcharge`，写入提前量、深度、置信度和 `simulation_id`，按设备及 30 分钟事件窗抑制/更新并输出 Top-N；冻结契约还要求工件 fail-closed、完整状态机、可审计 lineage、事务 outbox 和风险排序。

## 2. 实际执行的检查

### 2.1 任务指定的 focused pytest

命令：

```text
cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend && .venv/Scripts/python.exe -m pytest tests/hydraulic/test_forecast_alerts.py -q --tb=short
```

解释器/pytest：

```text
Python 3.14.4
pytest 8.0.1
```

实际输出：

```text
..........                                                               [100%]
10 passed, 573 warnings in 0.06s
```

这只证明 isolated fake-store 的 10 个 focused case 通过，不证明 PostgreSQL 锁、事务回滚、outbox、nowcast finalizer、API/SSE 或通知接线。

### 2.2 Python 3.11 focused check

命令：

```text
cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend && py -V:Astral/CPython3.11.15 -m pytest tests/hydraulic/test_forecast_alerts.py -q --tb=short
```

实际输出：

```text
C:\Users\zyq\AppData\Roaming\uv\python\python-3.11.15-windows-x86_64-none\python.exe: No module named pytest
```

因此 Python 3.11 focused test **NOT RUN**；不能把 3.14 的 10-pass 解释为冻结 Python 3.11 门禁通过。

### 2.3 只读定向诊断

以下诊断均通过 `cd .../backend && .venv/Scripts/python.exe - <<'PY'` 的 inline Python 运行，未写实现文件；同一 consolidated probe 的关键输出如下：

```text
premature_expire terminal expired 2026-09-25T10:30:00+00:00
post_terminal_replay suppressed 1
fabricated_artifact_hash created bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
mismatched_target_key created 00000000-0000-4000-8000-000000000007 node-of-another-target
rule_refresh refreshed 0.4 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa True
same_run_changed_issue unchanged 2026-09-25T10:00:00+00:00
below_peak_refresh_event alert.updated 0.75 0.7
escalation alert.escalated refresh
event_fields <missing> <missing>
notification_level Alert Level: info
risk_network_filter 1 0
confidence_precision 0.86001 1
risk_policy unregistered-v99 67.67
partial_rows_after_outbox_failure 1 1 1 0
missing_state_fields ['acknowledged_at', 'acknowledged_by', 'observation_ingest_event_id', 'observed_at', 'resolution_reason', 'resolved_at', 'resolved_by']
```

另有独立 probe 确认过去时刻候选仍可创建告警：

```text
PAST_EXPECTED_AT_ACCEPTED 2026-09-24T10:00:00+00:00 0
```

以及 level 透传 probe：

```text
SPOOFED_LEVEL_ACCEPTED critical 74.77
MEASUREMENT 0.31 RULE_THRESHOLD 0.3
```

### 2.4 持久化与调用面扫描

命令：

```text
cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage && grep -RIn --exclude-dir=__pycache__ -E "ForecastAlertWindow|forecast_alert_windows|class EventOutbox|event_outbox" backend/app/models/hydraulic_models.py backend/alembic/versions/0002_hydraulic_schema.py || true && grep -RIn --exclude-dir=__pycache__ -E "ForecastAlertService|calculate_suppression_key|rank_top_risk" backend/app backend/tests | head -120
```

关键输出：

```text
---forecast persistence symbols---
---service call sites---
backend/app/services/hydraulic/forecast_alerts.py:194:def calculate_suppression_key(
backend/app/services/hydraulic/forecast_alerts.py:828:class ForecastAlertService:
backend/app/services/hydraulic/forecast_alerts.py:847:        suppression_key = calculate_suppression_key(
backend/app/services/hydraulic/risk.py:199:def rank_top_risk(
backend/tests/hydraulic/test_forecast_alerts.py:17:    ForecastAlertService,
backend/tests/hydraulic/test_forecast_alerts.py:449:        ranking = rank_top_risk(
```

第一段无输出，说明当前真实 ORM/migration 中没有 `ForecastAlertWindow`/`forecast_alert_windows`/`EventOutbox` 符号；第二段显示服务只在自身定义处和 focused test 中出现，没有生产调用点。实施摘要也明确承认 PostgreSQL adapter、nowcast/API/SSE 接线未完成（`docs/hydraulic-twin/implementation/forecast-alerts.md:4,140-146`）。

## 3. Findings

### H-01 — outbox 事件丢失 `level` 和 `risk_score`，critical/warning 会被通知层降成 info（高）

- **File:** `backend/app/services/hydraulic/forecast_alerts.py:522-545,616-626`; `backend/app/services/notification_service.py:294-317`
- **Problem:** `ForecastAlert.to_dict()` 返回的 alert envelope 没有 `level` 或 `risk_score`，而 `ForecastBusinessEvent.payload()` 直接把该 envelope 放入 outbox。通知服务读取 `alert.get("level")`，缺省为 `info`；所以服务生成的 critical `alert.created`/`alert.escalated` 事件不会按 warning/critical 策略触发外部通知，风险消费者也拿不到物化分数。
- **Evidence:** 冻结事件 wire schema 要求 alert 携带 `risk_score`（`IMPLEMENTATION_CONTRACT.md:1836-1858`）；真实 DTO 输出源码在上述范围没有这两个 key。定向诊断输出：`event_fields <missing> <missing>`、`notification_level Alert Level: info`；这正是一个 `level="critical"` 的服务事件。
- **Impact:** 这是静默的告警降级：数据库内可能是 critical，但 SSE/通知消费者按 info 处理，违反告警中心/通知和可解释风险契约。
- **Fix:** 在统一的 JSON-ready alert DTO 中显式加入 `level`、`risk_score` 及契约要求的其它 lineage/threshold 字段；用服务产出的真实 event payload 经过 `NotificationService` 和风险排序 round-trip 测试，不能只用测试手工构造完整事件。

### H-02 — `expired` 转换没有时间/规则守卫，可提前终止并启动冷却（高）

- **File:** `backend/app/services/hydraulic/forecast_alerts.py:944-1000`
- **Problem:** `mark_terminal()` 对 `expired` 只检查 `alert.phase != "predicted"`，没有检查 `terminal_at > expected_at + expiry_grace`、rule expiry 条件、当前时间单调性或 nowcast deadline。任何调用者都可以把仍在未来窗口内的 predicted alert 立即置为 expired，并写入 30 分钟 cooldown。
- **Evidence:** 冻结状态机要求 expire 只能由 `now > expected_at + expiry_grace` 或同 rule 过期条件触发（`IMPLEMENTATION_CONTRACT.md:1774-1787`；补充契约 `PRD_SUPPLEMENT.md:193-208`）。真实代码的 terminal 守卫仅在 `:975-976`。定向诊断在 issued time 直接 expire，输出：`premature_expire terminal expired 2026-09-25T10:30:00+00:00`；该候选的 `expected_at` 是 10:35，说明告警在预计时刻前五分钟就被终止。
- **Impact:** 一次错误的内部调用即可制造假“已过期”、丢失后续真实预测并抑制未来 30 分钟复发；也使 `expired` 状态无法作为可靠审计事实。
- **Fix:** 将 `expiry_grace_minutes`/rule version 作为不可变输入或由 store 返回，执行 `terminal_at`、当前 DB time、expected_at 和 deadline 的完整守卫；补充未到预计时刻、恰好到 grace、超过 grace、late run 的边界测试，并禁止 public API 伪造该内部 action。

### H-03 — rule/calibration 的“hash 校验”只有字符串格式，没有工件内容、schema 或受控 registry 校验（高）

- **File:** `backend/app/services/hydraulic/forecast_alerts.py:234-290,295-388,674-742`
- **Problem:** `ProbabilityCalibrationRef` 和 `ForecastRuleRef` 只用 `_sha256()` 检查 64 位小写 hex；服务没有 artifact URI/format/schema version，也没有读取受控 artifact root/registry 并计算实际 SHA-256。`publish()` 因此会接受任意格式正确的伪造 hash，并把它写入正式 alert。validity 和 applicability domain 有检查，但工件身份/内容没有被证明。
- **Evidence:** 冻结契约要求受控本地 artifact、schema/version、hash、有效期和适用域，并在缺失/过期/hash 错时 fail closed（`IMPLEMENTATION_CONTRACT.md:969-1015,1793-1802`）。真实 DTO 字段和发布路径如上。定向诊断把 rule/calibration hash 改为未验证的 `b*64` 后仍得到 `fabricated_artifact_hash created ...`。
- **Impact:** 配置/供应链漂移可以被伪装成合法预测；过期或错误工件仍能产生 critical 告警，违反 confidence 和 policy fail-closed 的安全边界。
- **Fix:** 在 service port 中加入受控 artifact resolver/registry，要求 schema/version、URI/format、实际 bytes hash、validity、domain 和 run binding 全部通过后才允许 `publish`; 未解析工件应产生明确的 `FORECAST_POLICY_UNAVAILABLE/INVALID`，并补 hash 漂移、schema、URI、过期和越域负例。

### H-04 — 活动 episode 刷新可改变 threshold，却不更新 rule identity/hash，破坏 lineage 和 suppression 语义（高）

- **File:** `backend/app/services/hydraulic/forecast_alerts.py:1122-1205`
- **Problem:** `_refresh_active()` 的 `same_run_values` 会比较 rule id/hash，但跨 run 刷新时更新了 `threshold`、risk 和 calibration，却完全没有写回 `forecast_rule_id` 或 `forecast_rule_sha256`；`suppression_key` 仍沿用旧 rule。传入同 rule ID、不同 hash/threshold 的 rule 会得到 `refreshed`，导致新阈值语义挂在旧 rule hash 和旧 suppression key 下。
- **Evidence:** 冻结模型要求 alert 保存 rule id/hash，rule/calibration 工件不可变（`IMPLEMENTATION_CONTRACT.md:1107-1149,988-1008`）；诊断输出 `rule_refresh refreshed 0.4 aaaa... True`，即 threshold 已变为 0.4、stored hash 仍是旧 `a...`、suppression key 未变。
- **Impact:** 事后无法根据 alert 的 rule hash 重放阈值/风险；同一物理 episode 可以在不同规则下被解释，Top-N 和审计结果不可信。
- **Fix:** 明确 episode 只能绑定一个 immutable rule ID+hash；发现 rule ID/hash 变化应拒绝或创建新 episode。若允许同 episode 刷新，必须同时更新完整 lineage、threshold、policy version、suppression window/key，并把它纳入 CAS/幂等比较和测试。

### H-05 — target stable key 与 target ID/revision/manifest 没有服务端绑定校验（高）

- **File:** `backend/app/services/hydraulic/forecast_alerts.py:392-461,674-742,1103-1120`; `backend/app/models/hydraulic_models.py:2570-2572`
- **Problem:** candidate 同时携带 `target_id`、`target_stable_key`、revision 和 manifest hash，但 `_expected_alert_shape()` 没有 target-manifest resolver，也没有检查 stable key 是否属于该 revision/type；刷新时甚至只比较 target ID/type，不比较 stable key。ORM 的 `target_id`/`target_stable_key` 也是普通字段，没有对应复合外键。
- **Evidence:** 冻结契约要求 `target_type + target_stable_key` 必须在绑定 manifest 中存在且 `target_id` 解析到该 stable key（`IMPLEMENTATION_CONTRACT.md:1117-1153`）。定向诊断把 target ID 保持为 `...0007`、stable key 改成 `node-of-another-target`，仍得到 `mismatched_target_key created ...`。
- **Impact:** 错误的设备/管段 lineage 可以进入 alert、风险榜和通知，造成跨目标错报；仅靠 `network_id + target_id` 不能证明目标属于同一 revision/manifest。
- **Fix:** 在事务前增加 revision-scoped target resolver 和强类型绑定，校验 `(network_revision_id,target_manifest_id/hash,target_type,target_id,target_stable_key)`；持久化层增加可执行约束/FK 或等价 gate；补错 key、错 revision、错 manifest 和 refresh 跨 revision 负例。

### H-06 — 没有 durable store/nowcast 成功门禁，当前服务不能证明原子抑制或阻止 late result（高，集成阻断）

- **File:** `backend/app/services/hydraulic/forecast_alerts.py:392-410,641-672,827-865`; `backend/tests/hydraulic/test_forecast_alerts.py:43-111`; `docs/hydraulic-twin/implementation/forecast-alerts.md:4,140-146`
- **Problem:** `ForecastAlertStore` 只是 Protocol；真实 ORM/migration 没有 `forecast_alert_windows`/event outbox，生产代码也没有 `ForecastAlertService` 调用点。服务没有 nowcast run status、deadline 或 `issued_at` 的查询/守卫，任意带 UUID 的 candidate 都可进入 `publish()`。focused fake store 的 `__exit__()` 不回滚，注入 outbox failure 后仍留下 alert/window/transition 三类行。
- **Evidence:** 契约要求 PostgreSQL insert-on-conflict + `FOR UPDATE`、alert/transition/outbox 同事务，并要求 late result 不发布（`IMPLEMENTATION_CONTRACT.md:1100-1105,1155-1204,1863-1868`；G4 要求真实 PostgreSQL 并发/回滚）。持久化扫描第一段无输出；诊断输出 `partial_rows_after_outbox_failure 1 1 1 0`。过去时刻 probe 还输出 `PAST_EXPECTED_AT_ACCEPTED ... 0`。
- **Impact:** 当前 focused PASS 不能证明 20 并发候选只有一个 active alert、不能证明 crash rollback，也不能阻止 timed-out/late run 生成旧告警。即使 domain 算法正确，接入真实系统仍可能重复告警或产生半提交。
- **Fix:** 由持久化/nowcast owner 实现 adapter、唯一约束、窗口行锁、状态查询和 deadline gate；补真实 PostgreSQL 的并发、唯一冲突、事务回滚、late-result 和 outbox 原子性测试。服务层应 fail closed，而不是把关键安全条件交给未定义的调用方。

### H-07 — 完整 forecast 状态机/phase lineage 没有实现（高）

- **File:** `backend/app/services/hydraulic/forecast_alerts.py:463-503,827-1028`
- **Problem:** service 只有 `publish()` 与 `mark_terminal()`；没有 acknowledge、observe、refresh/escalate 的完整 transition API/内部命令。`ForecastAlert` 也没有 `observation_ingest_event_id`、`observed_at`、acknowledged/resolved actor/time/reason 字段，无法表达 predicted→observed、observed 不可 expire、冷却内 observed suppressed 等冻结语义。
- **Evidence:** 状态机要求 `open→acknowledged→resolved|expired`、`predicted→observed` 和完整 audit/outbox（`IMPLEMENTATION_CONTRACT.md:1764-1791`；补充契约 `PRD_SUPPLEMENT.md:190-209`）。实际方法扫描只有 `forecast_alerts.py:834` `publish` 与 `:945` `mark_terminal`；诊断输出列出 `missing_state_fields` 的 7 个字段。当前 API 另有独立内存 transition 逻辑（`hydraulic_routes.py:2975-3025`），没有替代 service 的 observe/lineage 语义。
- **Impact:** 不能证明“预计/已发生”区分、合格观测绑定、observed 终态和 suppression receipt；同一规则在不同 owner 中可能产生不同状态/版本。
- **Fix:** 明确 forecast-alerts owner 的 transition command/aggregate，补齐 observation receipt 与状态字段、version CAS、actor/reason、稳定 event type 和 outbox；将 API 只作为鉴权/序列化层，禁止复制业务状态机。补全合法/非法转换、observed 时间窗、终态冷却和版本冲突测试。

### M-01 — escalation 的 transition audit 错记为 `refresh`（中）

- **File:** `backend/app/services/hydraulic/forecast_alerts.py:1213-1238`
- **Problem:** level 上升时业务 outbox 发 `alert.escalated`，但同一个 transition 始终以 `action="refresh"` 写入。契约 transition action 集合明确区分 `refresh` 与 `escalate`。
- **Evidence:** 诊断输出 `escalation alert.escalated refresh`；冻结模型/状态机见 `IMPLEMENTATION_CONTRACT.md:1174-1181,1774-1782`。
- **Impact:** 审计消费者按 action 重建状态或通知时会漏掉升级动作，event 与 audit 不一致。
- **Fix:** 根据 `escalated` 分支写 `action="escalate"`，并用同一 aggregate version 生成对应 outbox；增加 warning→critical 和 info→warning 的明确 golden case。

### M-02 — refresh 的 peak delta 取错基准，会制造虚假 `alert.updated`（中）

- **File:** `backend/app/services/hydraulic/forecast_alerts.py:1159-1177`
- **Problem:** 代码用 `abs(metric_value - current.peak_value)` 判断 peak material change，之后才计算 `new_peak_value=max(current.peak_value, metric_value)`。当新值低于历史峰值时，契约要求的 `abs(new_peak_value - previous_peak_value)` 应为 0，但当前实现仍可能命中 delta。
- **Evidence:** 冻结规则固定用 `abs(new_peak_value-previous_peak_value) >= refresh_peak_delta`（`IMPLEMENTATION_CONTRACT.md:1784`）。诊断先存 0.75、再发 0.70，输出 `below_peak_refresh_event alert.updated 0.75 0.7`。
- **Impact:** 轻微回落会制造无意义的更新事件、版本增长和下游刷新，违反“低于阈值不发 updated”的事件抑制语义。
- **Fix:** 先计算 `new_peak_value`，再用新旧 peak 的绝对差参与 material refresh；补上升、下降、相等和恰好等于 delta 的测试。

### M-03 — 同一 nowcast run 改写 `issued_at` 被错误当作 unchanged（中）

- **File:** `backend/app/services/hydraulic/forecast_alerts.py:1122-1152`
- **Problem:** `same_run_values` 比较了 run/simulation/frame 等字段，却没有比较 `candidate.issued_at`；同一 run ID 改写业务发布时刻会返回 `unchanged`，并保留旧 lineage/window 时间。
- **Evidence:** 冻结契约规定成功 run 的 `issued_at` immutable（`IMPLEMENTATION_CONTRACT.md:1100-1104`）。诊断输出 `same_run_changed_issue unchanged 2026-09-25T10:00:00+00:00`，尽管 probe 传入了 `START+1s`。
- **Impact:** 重放/数据修复可能静默接受不同 payload，DB 中的 issued/ordering 与调用方事实不一致，幂等边界不再是同请求。
- **Fix:** 把规范化 `issued_at` 纳入 same-run request fingerprint；同 run 同 hash 返回原结果，不同 hash 走 `FORECAST_CANDIDATE_CONFLICT`，并补时间冲突/重放测试。

### M-04 — `rank_top_risk()` 的 network_id 注解允许 str，但没有规范化，字符串调用会静默返回空榜（中）

- **File:** `backend/app/services/hydraulic/risk.py:52-68,198-256`
- **Problem:** `RankableRiskAlert.network_id` 和 `rank_top_risk(network_id=...)` 都允许 `UUID | str`，实现却直接比较 `alert.network_id != network_id`。真实 service alert 使用 UUID，而 API/序列化边界常传字符串，导致全部告警被过滤。
- **Evidence:** 诊断对同一 UUID 传 UUID 得到 `risk_network_filter 1 0`（UUID 输入 1 条、字符串输入 0 条）。契约 risk-ranking 要求 network scope（`IMPLEMENTATION_CONTRACT.md:1688-1726`）。
- **Impact:** 合法 API 请求可能显示空 Top-N，且没有错误提示；跨 network 过滤的安全边界依赖调用者恰好使用同一 Python 类型。
- **Fix:** 在边界将两者 canonicalize 为 UUID（非法值 fail closed），并覆盖 UUID/str 混合、跨 network 和 nil UUID 测试。

### M-05 — confidence 没有按 NUMERIC(5,4) 规范化，微小差异仍改变 aggregate version（中）

- **File:** `backend/app/services/hydraulic/forecast_alerts.py:451-456,1122-1205`; `backend/app/services/hydraulic/risk.py:148-150`
- **Problem:** service 接受任意 Python float 并以全精度参与幂等比较、version 和 risk 计算；没有按冻结字段精度规范化。`0.86000` 与 `0.86001` 在持久化 NUMERIC(5,4) 中可能相同，但服务会认为不同并递增版本。
- **Evidence:** 契约 alert confidence 为 `NUMERIC(5,4)`（`IMPLEMENTATION_CONTRACT.md:1126-1134`）；诊断输出 `confidence_precision 0.86001 1`，且第二个 candidate 仍被写入。
- **Impact:** 传输重放/数据库舍入后产生版本和事件漂移，confidence 物化与风险分不再可重放。
- **Fix:** 在 DTO 边界用 Decimal 按冻结规则量化，统一存储值、幂等 fingerprint、risk 输入和输出；补第四位舍入、边界 0/1、NaN/Infinity 测试。

### M-06 — v1 scorer 接受任意非空 policy version，未知策略仍可排名（中）

- **File:** `backend/app/services/hydraulic/risk.py:130-195,237-247`
- **Problem:** `calculate_risk()` 只检查 `policy_version` 非空，随后 `rank_top_risk()` 将持久化 alert 的任意版本传给 v1 公式并原样标注。没有注册表/版本分派或“v1 只能使用 risk-v1”的检查。
- **Evidence:** 诊断输出 `risk_policy unregistered-v99 67.67`；冻结契约要求可解释、版本化 risk policy（`IMPLEMENTATION_CONTRACT.md:1804-1814`）。
- **Impact:** 旧/未知策略的分数会被 v1 公式重算并伪装成新策略，Top-N 顺序和审计解释不可靠。
- **Fix:** 建立允许的 policy version 注册表并按版本选择实现；未知版本 fail closed 或返回 stale，不应只用非空字符串放行。

### M-07 — caller-controlled `level` 没有由 rule 派生/校验，风险与通知可被伪造（中）

- **File:** `backend/app/services/hydraulic/forecast_alerts.py:451-460,674-742,763-780`
- **Problem:** candidate 只做 `level in {info,warning,critical}` 成员检查，随后 `_risk()` 直接使用该 level；`ForecastRuleRef` 没有 severity band/level evaluator，也没有验证 level 与 metric/threshold 的关系。服务信任调用者传入的 severity。
- **Evidence:** 诊断用 depth 0.31、rule threshold 0.30 传入 `critical`，输出 `SPOOFED_LEVEL_ACCEPTED critical 74.77`；契约的 risk severity 公式和 alert level 语义见 `IMPLEMENTATION_CONTRACT.md:1126-1134,1804-1814`。
- **Impact:** 任意上游解析/适配器错误可把轻微超阈事件升级为 critical，改变 Top-N、通知策略和运营响应。
- **Fix:** 让 level 由绑定 rule 的版本化 severity policy 计算，或在 service 内验证其与 metric/threshold 的关系；不接受未由可信 policy evaluator 产生的自由字符串。

### M-08 — 终态后同一 candidate 的重放被计为新的 suppressed signal（中）

- **File:** `backend/app/services/hydraulic/forecast_alerts.py:879-899,1122-1147`
- **Problem:** 终态后 `active_alert_id=None` 且进入 cooldown 时，代码先按 candidate 时间进入 suppressed 分支，不先识别同一 nowcast run/simulation 的重放；同一业务 candidate 会被增加 `suppressed_count`，而不是返回原创建结果/幂等结果。
- **Evidence:** 诊断在 terminal 后重放同一 candidate，输出 `post_terminal_replay suppressed 1`；契约要求候选/事件窗幂等，重复信号不能被当成新的抑制信号（`IMPLEMENTATION_CONTRACT.md:393-407`）。
- **Impact:** 重试/崩溃恢复会虚增 suppressed metric；后续审计无法区分真正的新候选与重复传输。
- **Fix:** 先按 `(suppression_key,nowcast_run_id,simulation_id,target/frame)` 查重并返回原结果；cooldown 计数只对新的 qualifying candidate 增加，并增加 post-terminal replay 测试。

## 4. 测试覆盖缺口

现有 10 个 case 覆盖了 canonical key、基本 flood/pipe 创建、活动 episode 复用、同 run 简单重放/冲突、level 不降级、29m59s/30m 冷却边界、基本 Top-N 和 NaN risk；它们没有保护上述高风险路径：

1. 真实 PostgreSQL 的 `INSERT ... ON CONFLICT ... FOR UPDATE`、20 并发同 key、唯一约束和版本 CAS。
2. outbox/transition/alert/window 任一步失败时的事务回滚，以及跨 worker/restart replay。
3. late/timed-out nowcast、过去 expected_at、5 分钟 horizon grid 和 run-bound policy。
4. rule/calibration artifact schema/hash/registry/validity/out-of-domain 负例。
5. target stable key 与 revision/manifest/target ID 的错配和 refresh 跨 revision。
6. critical/warning event 的完整 payload → notification/risk consumer round-trip。
7. expire grace、acknowledge、observe、observed phase、终态冷却和 observation receipt。
8. escalation audit action、peak 回落、same-run issued_at、post-terminal replay、confidence 四位精度。
9. Top-N 的 mixed UUID/string、tie-break、acknowledged 过滤、未知 policy version 和 due-slot stale。

## 5. 通过项与未执行项

### 已确认通过

- 指定 focused 文件在 Python 3.14.4/pytest 8.0.1 下为 `10 passed`。
- canonical suppression payload 的字段集合和 basic SHA-256 计算在 focused case 中通过。
- 基本 lead floor、2h 上限、flood/pipe 创建、活动 ID 复用、冷却边界和手算 risk score 在 happy path 中通过。

### 本轮未运行/不能据此宣称通过

- Python 3.11 focused pytest：实际尝试但环境没有 pytest，结果为 NOT RUN。
- 未运行完整 hydraulic suite、backend suite、最终 G0-G6/AC 门禁；按任务要求没有重复最终全量门禁。
- 未启动真实 PostgreSQL/PostGIS、Redis、nowcast worker、API、SSE、通知 provider 或 Compose。
- 实施摘要中作者此前报告的全量测试/门禁结果不是本轮执行证据，本审查没有将其计入通过。

## 6. 最终判断

**`approved=false`。** H-01～H-07 涉及事件通知字段、终态安全、工件 fail-closed、rule lineage、目标绑定、durable transaction/nowcast gate 和状态机完整性，均会影响 AC-4 的告警事实、抑制幂等或可审计性。M-01～M-08 说明 focused 10-pass 仍未覆盖边界条件。至少应先完成事件 envelope、过期守卫、工件/target 绑定、持久化事务与状态机修复，并补真实 PostgreSQL/负向测试后再独立复审。
