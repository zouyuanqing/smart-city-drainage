# 水力预测、告警与实时分发契约

> 状态：**SUPERSEDED / NON-NORMATIVE — 不得作为实现契约**。本文件已被 `../PRD_SUPPLEMENT.md` 与 `../IMPLEMENTATION_CONTRACT.md` 的整改候选 v2 取代，仅保留首轮领域审查轨迹。
>
> 2026-09-24 设计审查未批准 v1。规范实现必须增加：nowcast `issued_at/forecast_origin_at/published_at`；独立 900 s freshness；版本化 probability-calibration 与 forecast-rule 工件（schema/hash/validity/domain）；真实 source observation event identity；JWT/fallback/RBAC/rate-limit 前置门禁；统一 `GET /api/forecast/events`。
>
> 以下旧段落中的缺 issued_at、用 deadline 判 stale、lead 必须为 5 的倍数、无 calibration/rule artifact 表、旧 observation receipt 幂等、旧文件所有权和 AC-4 pytest 命令均被显式取代，禁止实现。

## 1. 审查结论与明确决策

1. **当前没有 forecast 实现可直接扩展。** 对 `backend/app`、`backend/alembic`、`frontend/src`、`docker` 搜索 `flood_forecast|pipe_surcharge|lead_time_minutes|simulation_id|nowcast|risk-ranking`，本次运行结果全部为 0；当前 API 静态计数仍是 30 个 HTTP 路由、1 个 WebSocket 路由。证据：`docs/hydraulic-twin/recon/backend-data.md:81-89`、`docs/hydraulic-twin/recon/realtime-security.md:72-81`。
2. **MUST NOT 把 forecast 写入现有 `alerts` 表或复用 `/api/sse/events`。** 现有告警只有 `is_acknowledged/is_resolved` 布尔字段和 AI `detection_confidence`，没有目标类型、预计时刻、提前量、forecast 状态机或版本；SSE 又使用进程内计数、1000 条内存历史和 256 队列。证据：`backend/app/models/db_models.py:181-225`、`backend/app/services/sse_manager.py:32-120`、`backend/app/api/routes.py:394-414`。
3. **采用独立 forecast 领域模型 + 旧告警中心双源合并。** forecast 写入新表；`GET /api/alerts` 与旧 SSE 行为保持不变；告警中心并行读取旧告警和 `GET /api/forecast/alerts`，以 `source=legacy|forecast` 区分并按全局 UUID 去重。
4. **采用两个正交状态维度。** `status` 管理处置生命周期：`open → acknowledged → resolved|expired`；`phase` 表达证据阶段：`predicted → observed`。确认不是“已发生”，预计时刻到达也不是“已发生”；只有合格实测越过规则阈值才可转成 `observed`。
5. **30 分钟事件窗采用“活动告警合并 + 终态冷却”，不采用固定时间桶。** 同一 `suppression_key` 在活动期始终复用同一 `alert_id`；终态后 `[terminal_at, terminal_at+30m)` 内的合格信号只计数为 suppressed，不创建新告警、不发新 SSE/通知；恰好到 `terminal_at+30m` 可创建新告警。
6. **幂等边界分五层。** nowcast 时隙、候选帧、告警事件窗、状态转换命令、事件/通知投递分别有数据库唯一约束或事务锁。传输只承诺 at-least-once；通过稳定 `event_id`、聚合版本和唯一键实现“业务效果一次”。
7. **`confidence` MUST 是校准后的阈值超越概率，不得复用 AI `detection_confidence`。** 其定义为：在 `expected_at` 开始的 5 分钟预测桶内，指标达到规则阈值的概率。确定性单次求解器没有概率输出时，MUST NOT 伪造 `1.0`；没有有效概率校准时只保存 `publishable=false` 的 horizon point，不发布正式告警。
8. **Top-N v1 使用可解释、版本化公式，不使用不可解释的 ML 排名。** 分数由 confidence、severity、urgency、normalized impact 四项组成；响应必须返回各项分量。该分数是处置优先级，不是概率，也不是生命安全评级。
9. **新增认证 SSE 端点 `GET /api/forecast/events`。** 旧 `/api/sse/events` 不承载 forecast。前端使用 `fetch` 流而不是原生 `EventSource`，以便发送 `Authorization: Bearer`；JWT 不进入 query string。
10. **通知从持久化 delivery outbox 异步发送。** 告警事务成功与邮件/webhook 成功解耦；SMTP/webhook 失败不得回滚告警，也不得阻塞 nowcast 主链路。
11. **AC-4 的 3 分钟 SLO 从计划时隙 `scheduled_for` 计算，到 alert + event outbox 原子提交为止。** nowcast 必须拥有保留/优先执行通道；否则它可能排在 10 分钟超时的一般仿真之后，PRD 的 FR-3 与 NFR 无法同时保证。

## 2. 现状证据与影响

| 现状 | 已读证据 | 对本契约的影响 |
|---|---|---|
| ORM 只有旧监测/AI 域 | `backend/app/models/db_models.py:46-295`；`docs/hydraulic-twin/recon/backend-data.md:103-124` | 必须新增 forecast 表，不能把节点/管段目标塞进 `device_id` |
| 旧告警只有布尔状态 | `backend/app/models/db_models.py:213-224`；`backend/app/schemas/schemas.py:130-153` | 必须新增显式状态机、版本与审计事件 |
| 旧告警找不到仍返回 200 | `backend/app/api/routes.py:865-892` | forecast 转换必须使用 404/409 的严格语义 |
| SSE ID 为进程内自增、历史仅 1000 条 | `backend/app/services/sse_manager.py:35-43,90-120` | 多 worker/重启 replay 不可用；必须使用数据库序列和 outbox |
| SSE 无认证 | `backend/app/api/routes.py:394-414` | forecast 不进入旧匿名流 |
| 生产者存在 direct broadcast + Redis 回声 | `backend/app/workers/inference_worker.py:192-223`；`backend/app/main.py:79-94,144-152` | forecast 只允许 outbox relay 单路径，避免重复 |
| 通知同步 SMTP、失败仅返回 `False` | `backend/app/services/notification_service.py:27-107` | 必须有 delivery 表、lease、重试、死信和非阻塞 provider |
| 前端仅监听 `sensors/alerts` | `frontend/src/hooks/useSSE.ts:66-93` | 新增 feature-local forecast SSE hook，不修改旧事件语义 |
| 前端告警动作乐观更新并吞错 | `frontend/src/pages/AlertCenter.tsx:136-189` | forecast action 必须失败回滚并显示服务端冲突 |
| 测试夹具不能替换直接调用的 DB context manager | `backend/tests/conftest.py:33-42`；`backend/app/core/database.py:119-129` | 并发/锁/唯一约束测试必须使用真实 PostgreSQL，不能只靠 SQLite |

## 3. 领域边界

### 3.1 本契约拥有

- nowcast 周期的调度状态、3 分钟 deadline、重试次数和引用仿真。
- `flood_forecast`、`pipe_surcharge` 的正式告警、状态转换、事件窗和审计。
- confidence 发布门禁、风险分和 Top-N 排序。
- forecast REST DTO、认证 SSE、事件 replay 和 outbox relay。
- forecast 的邮件/webhook delivery 状态与重试。

### 3.2 本契约只消费

- `Network/Node/PipeSegment/Catchment` 的稳定 UUID 与规则绑定。
- `RainfallScenario` 的不可变 revision。
- `Simulation` 的 `simulation_id`、状态、solver/model version、结果 frames。
- 原始 `simulation_frames`；forecast MUST NOT 再复制一份同义帧表。
- 现有 JWT/RBAC 身份声明，但在实施 forecast 前必须先修复缺 token 500、坏 role 500 和 fallback admin。

### 3.3 非目标

- 不训练新的预报模型。
- 不把水力模型 RMSE/NSE 率定等同于概率置信度校准。
- v1 不基于人口、资产脆弱性或道路中断做生命安全排名；如需，必须新增数据契约和新的 `risk_policy_version`。
- 不改变旧 `/api/alerts`、`/api/sse/events` 的响应形状和业务行为。

## 4. 数据契约

### 4.1 枚举与不可变字段

```text
NowcastStatus =
  scheduled | submitted | running | retry_wait | cancel_requested |
  cancelled | succeeded | failed | timed_out

ForecastAlertType = flood_forecast | pipe_surcharge
ForecastAlertStatus = open | acknowledged | resolved | expired
ForecastPhase = predicted | observed
ForecastLevel = info | warning | critical
ForecastEventType =
  alert.created | alert.updated | alert.escalated | alert.observed |
  alert.resolved | alert.expired
NotificationChannel = in_app | email | webhook
NotificationStatus =
  pending | sending | retry_wait | delivered | dead_letter | suppressed
ConfidenceMethod =
  ensemble_exceedance_frequency | calibrated_residual | conformal |
  calibrated_regression
```

以下字段在正式告警中 MUST 存在：

| 字段 | 类型/约束 | 语义 |
|---|---|---|
| `id` | UUID | 全局告警 ID；legacy/forecast 共用去重空间但不同表 |
| `network_id` | UUID | 所属管网版本 |
| `nowcast_run_id` | UUID | 生成/更新该告警的 nowcast 周期 |
| `simulation_id` | UUID | 产生预测的仿真；不得为空 |
| `alert_type` | enum | `flood_forecast` / `pipe_surcharge` |
| `target` | object | `type=node|pipe_segment`、`id`、`display_id?` |
| `expected_at` | UTC timestamptz | 本次 horizon 中最早且满足发布门禁的超阈时刻 |
| `lead_time_minutes` | integer 0..120 | `max(0, floor((expected_at - nowcast.issued_at)/60s))`；不要求为 5 的倍数 |
| `phase` | enum | `predicted` / `observed` |
| `status` | enum | 处置状态 |
| `level` | enum | 来自版本化 `forecast_rule` |
| `confidence` | decimal(5,4)，0..1 | `expected_at` 五分钟桶的校准超阈概率 |
| `confidence_method` | enum | 概率来源 |
| `probability_calibration_id` | UUID/string | 概率校准工件版本；与 RMSE/NSE 模型率定 ID 分开 |
| `threshold` | object | `metric/operator/value/unit/rule_id/rule_version` |
| `measurements` | object | `depth_m`、`fill_ratio`；至少一个与 alert type 匹配且非空 |
| `risk_score` | decimal(6,2)，0..100 | 处置优先级，不是概率 |
| `risk_policy_version` | string | 排序公式版本 |
| `version` | integer | 乐观锁版本，每次物化变化单调加一 |

### 4.2 `nowcast_runs`

| 列 | 约束/说明 |
|---|---|
| `id` | UUID PK |
| `network_id` | UUID NOT NULL |
| `scheduled_for` | UTC timestamptz NOT NULL，10 分钟对齐 |
| `forecast_origin_at` | UTC，solve 前冻结的 5 分钟网格原点 |
| `issued_at` | 成功 transaction 的不可变业务发布时刻 |
| `published_at` | outbox relay 可读取时刻，不早于 issued_at |
| `deadline_at` | 默认 `scheduled_for + 180s`，与 freshness 独立 |
| `status` | `NowcastStatus` |
| `attempt_count` | 0..2，表示已使用重试次数；总尝试最多 3 次 |
| `simulation_id` | UUID；进入 `submitted` 后必须存在 |
| `rainfall_scenario_id` | UUID NOT NULL |
| `model_version` | string NOT NULL |
| `solver_name` / `solver_version` | string NOT NULL |
| `model_calibration_id` | RMSE/NSE 等水力率定工件；不能替代概率校准 |
| `flood_forecast_probability_calibration_id/hash` + `pipe_surcharge_probability_calibration_id/hash` | 两类不可变概率工件；缺失/过期/越域/hash 错不得发布 |
| `flood_forecast_rule_id/hash` + `pipe_surcharge_rule_id/hash` | 两类不可变阈值/clear/expiry/risk 工件 |
| `config_hash` | 对 nowcast 输入、calibration/rule ID+hash+validity 做 canonical hash |
| `error_code` / `error_message` | 失败原因；message 不得含密钥或完整输入文件 |
| `started_at` / `completed_at` / `created_at` / `updated_at` | UTC |

约束：

- `UNIQUE(network_id, scheduled_for)`：调度器重复触发只能得到同一周期。
- `CHECK(deadline_at = scheduled_for + interval '180 seconds')`；不得用配置放宽 3 分钟 SLO。
- `succeeded` MUST 在告警/事件 outbox 事务提交后才设置。
- `timed_out` 后到达的旧仿真结果只保留审计，不得生成告警。

### 4.2.1 概率与规则工件（v2 必选）

`hydraulic.probability_calibrations` 和 `hydraulic.forecast_rules` 是不可变 registry：至少保存 schema/version、method/rule version、solver/model/calibration link、metric、lead/magnitude domain、valid_from/valid_until、artifact URI/format/SHA-256 和质量证据。工件只来自受控本地 artifact root/registry；nowcast run 显式绑定 ID+hash，禁止运行时下载未锁定 URL或从“最新记录”猜测。

发布门禁必须验证 artifact hash/schema/有效期和当前 target/lead/magnitude 适用域。RMSE/NSE `model_calibration_id` 与 probability calibration 分开；缺失/过期/越域/hash 不符只允许 horizon point `publishable=false`。

### 4.3 `forecast_alerts`

除第 4.1 节公共字段外，必须有：

```text
first_qualifying_at
last_qualifying_at
peak_at
peak_value
observed_at?
acknowledged_by? / acknowledged_at?
resolved_at? / resolved_by? / resolution_reason?
suppression_key
metric_value
metric_unit
created_at / updated_at
```

约束：

- `suppression_key` 为 canonical SHA-256，不使用 UUID 随机替代。
- `flood_forecast.measurements.depth_m MUST NOT be null`。
- `pipe_surcharge.measurements.fill_ratio MUST NOT be null`。
- `lead_time_minutes` 由 `max(0,floor((expected_at-issued_at)/60s))` 计算；horizon frame 保持 5 分钟网格，lead 不要求为 5 的倍数。
- `observed_at IS NOT NULL` 当且仅当 `phase=observed`。
- `resolved/expired` 为终态；终态不能原地 reopen。
- 风险分和字段物化在 `nowcast_runs.status='succeeded'` 的同一事务中完成。

### 4.4 `forecast_alert_windows`

```text
suppression_key        PK
active_alert_id?       FK -> forecast_alerts.id
cooldown_until?        UTC
last_candidate_at
last_qualifying_at?
suppressed_count
version
created_at / updated_at
```

该表是事件窗并发闸门。首次候选使用 insert-on-conflict；冲突后 MUST `SELECT ... FOR UPDATE` 再判断，禁止“先查后插”的竞态实现。

### 4.5 `forecast_alert_transitions`

不可变审计表：

```text
id, alert_id, aggregate_version,
from_status, to_status,
from_phase, to_phase,
action, reason, actor_id,
idempotency_record_id?,
occurred_at
UNIQUE(alert_id, aggregate_version)
```

`action`：`create|acknowledge|observe|resolve|expire|refresh|escalate`。

### 4.6 `forecast_event_outbox`

```text
sequence             BIGSERIAL PK，兼作 SSE Last-Event-ID
event_id             UUID UNIQUE
alert_id             UUID
aggregate_version    integer
event_type           ForecastEventType
network_id           UUID
payload              JSONB
occurred_at          UTC
published_at?        UTC
expires_at           UTC
created_at           UTC
UNIQUE(alert_id, aggregate_version, event_type)
```

- `payload` 在插入后 MUST NOT 修改。
- outbox relay 使用 PostgreSQL advisory lock 选出全局单一逻辑 relay；`FOR UPDATE SKIP LOCKED ORDER BY sequence` 逐条发布，避免多 relay 乱序。
- relay 在 Redis 发布后才设置 `published_at`；崩溃窗口允许重复发布，但 `event_id` 不变。
- 默认 replay 保留 168 小时；清理只删除 `published_at IS NOT NULL AND expires_at < now()` 的行。

### 4.7 `notification_deliveries`

```text
id, event_id, alert_id, channel, destination_id,
template_version, status,
attempt_count, next_attempt_at?, lease_until?,
provider_message_id?, last_error_code?,
created_at, sent_at?, updated_at
UNIQUE(event_id, channel, destination_id, template_version)
```

### 4.8 `forecast_command_idempotency`

```text
scope                 varchar（例如 forecast-alert-transition）
key                    varchar
request_hash           char(64)
response_status        integer
response_body          JSONB
created_at / expires_at
PRIMARY KEY(scope, key)
```

相同 key + 相同 request hash 返回原响应；相同 key + 不同 body 返回 409。默认保留 24 小时。

### 4.9 `forecast_observation_receipts`

```text
id, observation_ingest_event_id, alert_id,
target_type, target_stable_key, metric, value, unit,
observed_at, quality_flag, created_at
UNIQUE(observation_ingest_event_id, alert_id)
FK(observation_ingest_event_id) -> observation_ingest_events(id)
```

来源幂等由 `observation_ingest_events(source_system,source_stream,source_partition,source_offset)` 保证；receipt 只防止同一来源事件重复绑定多个活动 forecast。质量不合格的观测不得把 `phase` 改为 `observed`。

## 5. 状态机

### 5.1 Nowcast 状态机

| 当前 | 事件/守卫 | 下一状态 |
|---|---|---|
| `scheduled` | 获得仿真槽位 | `submitted` |
| `submitted` | worker claim + heartbeat | `running` |
| `running` | 仿真成功；frames/告警/outbox 原子提交 | `succeeded` |
| `submitted/running` | 瞬态失败且 `attempt_count < 2` | `retry_wait` |
| `retry_wait` | backoff 到期 | `submitted` |
| `scheduled/submitted/running` | cancel accepted | `cancel_requested` |
| `cancel_requested` | worker 确认停止/清理 | `cancelled` |
| `submitted/running/retry_wait` | 非瞬态失败或重试耗尽 | `failed` |
| `submitted/running/retry_wait` | `now >= deadline_at` | `timed_out` |

规则：

1. 状态更新 MUST 使用 `WHERE id=? AND status=? AND version=?` 的 compare-and-swap；影响行数不是 1 时返回冲突。
2. `succeeded/failed/timed_out/cancelled` 为终态。
3. 同一 `scheduled_for` 的重复 scheduler 触发返回已有 run，不得新建。
4. 仿真重试产生新的 `simulation_id` 时，更新 run 的当前 attempt 引用；历史引用写审计日志。
5. deadline 后的 late success MUST NOT 发布 forecast，避免旧预测覆盖新事实。

### 5.2 Forecast alert 状态机

`status` 与 `phase` 分开判断：

| 动作 | 允许的 status | 允许的 phase | 结果 |
|---|---|---|---|
| create | 无 | `predicted` | `open/predicted` |
| acknowledge | `open` | 任意 | `acknowledged`，phase 不变 |
| observe | `open/acknowledged` | `predicted` | status 不变，phase=`observed` |
| resolve | `open/acknowledged` | 任意 | `resolved` |
| expire | `open/acknowledged` | 仅 `predicted` | `expired` |
| escalate | `open/acknowledged` | 任意 | level 提升，status/phase 不变 |
| refresh | `open/acknowledged` | 任意 | 更新预计时刻/峰值/confidence；仅物化变化达到阈值时发事件 |

终态规则：

- `resolved/expired` MUST NOT 回到非终态；后续复发创建新 `alert_id`。
- `observed` 告警只能因合格清空观测或人工操作转 `resolved`，不能转 `expired`。
- 预计时刻已过但未观测：`expected_at + expiry_grace` 后可 `expired`。
- 最新两次成功 nowcast 均不再预测：`no_longer_predicted` 后可提前 `resolved`。
- 观测低于 clear threshold 连续 `clear_debounce_minutes`：`condition_cleared` 后 `resolved`。
- nowcast stale/缺失本身不能证明条件已清除；除预计超时外，不得仅因数据缺失 resolve。

### 5.3 转换 API 语义

`POST /api/forecast/alerts/{alert_id}/transitions`

```json
{
  "action": "acknowledge|resolve",
  "reason": "可选，1..500",
  "expected_version": 3
}
```

Header：`Idempotency-Key: <8..128 chars>` 必填。

- 缺 header/body：422。
- 不存在：404。
- `expected_version` 不匹配：409 `FORECAST_VERSION_CONFLICT`，返回 current version/status/phase。
- 非法转换：409 `INVALID_FORECAST_TRANSITION`。
- 同 key + 同 body：返回首次响应，并带 `Idempotency-Replayed: true`。
- 同 key + 不同 body：409 `IDEMPOTENCY_KEY_REUSED`。
- `ignore` 不属于 forecast 状态机，返回 422；不能伪装成 acknowledged。

## 6. 30 分钟事件窗

### 6.1 `suppression_key`

canonical 输入固定为：

```text
forecast-alert-v1
network_id
alert_type
target_type
target_id
rule_id
```

以 UTF-8、管道符分隔、无空格编码后 SHA-256。下列字段 MUST NOT 进入 key：level、confidence、expected_at、lead time、nowcast_run_id、simulation_id、用户、当前时间。

原因：同一场持续内涝的 5 分钟帧会生成多个预测时刻；把时间或 simulation 放入 key 会每 10 分钟制造新告警。

### 6.2 事务算法

对每个 `publishable=true` 的候选：

1. 计算 `suppression_key`。
2. 插入或锁定 `forecast_alert_windows` 行。
3. 若 `active_alert_id` 指向 `open/acknowledged`：
   - 复用该告警；更新 `expected_at`、metric、peak、confidence、risk score、`last_qualifying_at`。
   - level 提升时 `escalate`；phase 仅由合格观测改变。
   - 不创建新 `alert_id`，不重置 acknowledged 状态。
4. 若上一告警为终态，且 `candidate.issued_at < cooldown_until`：
   - `suppressed_count += 1`；
   - 不创建告警、SSE 或外部通知；
   - 写可查询的抑制计数/指标。
5. 否则创建新 `open/predicted` 告警，并令 `active_alert_id` 指向它。

边界为半开区间：

```text
suppressed: terminal_at <= candidate_time < terminal_at + 30m
new alert:  candidate_time >= terminal_at + 30m
```

### 6.3 什么叫“物化变化”

以下变化更新数据库但不单独制造“新告警”：

- 只有 confidence 小数第四位变化。
- 只有 `updated_at` 变化。
- 相同 severity 下峰值轻微变化，且 risk score 变化 < 0.5。

以下变化可发 `alert.updated`，但 `alert_id` 不变：

- `expected_at` 提前或推迟至少一个 5 分钟步长。
- `lead_time_minutes` 改变。
- `risk_score` 跨过配置档位或变化 ≥ 5。
- level、phase、status 改变。

AC-4 的“窗口内不重复”验收集合是：**同一 suppression key 在活动/冷却期只有一个 active 或 cooldown terminal alert，且只有一次 `alert.created`**；`alert.updated/escalated/observed` 不计为重复告警。

## 7. 幂等契约

| 边界 | 幂等键/约束 | 重放结果 |
|---|---|---|
| nowcast 调度 | `UNIQUE(network_id, scheduled_for)` | 返回同一 run |
| horizon 物化 | `UNIQUE(simulation_id,target_type,target_id,frame_at)` | upsert，不重复帧 |
| 活动告警 | `forecast_alert_windows` 行锁 + active_alert_id | 更新同一告警 |
| 终态冷却 | `cooldown_until` + 事务锁 | suppressed 或新建一个告警 |
| 人工转换 | `Idempotency-Key` + request hash | 原响应重放或 409 |
| SSE | `sequence` + `event_id` + aggregate version | 客户端重复 event_id 丢弃；旧 aggregate version 丢弃 |
| 观测 ingest | source event 四元组 + payload hash | 同 payload 重放；不同 payload `SOURCE_EVENT_CONFLICT` |
| 观测绑定 | `UNIQUE(observation_ingest_event_id,alert_id)` | 返回已绑定观测 |
| 外部通知 | `UNIQUE(event_id,channel,destination_id,template_version)` | 一个逻辑 delivery |

**exactly-once 边界：** PostgreSQL 唯一约束/事务保证业务行只创建一次；SSE、SMTP、webhook 是 at-least-once 传输，外部系统故障下不能诚实承诺物理 exactly-once。Webhook MUST 发送 `Idempotency-Key: <event_id>`；邮件 Message-ID SHOULD 包含 event ID。

## 8. 置信度契约

### 8.1 定义

对规则阈值 `T`，令 `X_t` 为 `expected_at` 五分钟桶内的预测指标：

```text
confidence = P(X_t >= T | 截至 nowcast.issued_at 的输入)
```

它不是：

- 模型检测框置信度；
- 求解器“成功完成”的概率；
- 发生灾害的概率；
- 水力模型 RMSE/NSE。

现有 AI 字段 `detection_confidence` 来自视觉推理（`backend/app/models/db_models.py:212`），forecast MUST 使用独立字段。

### 8.2 可接受算法

| method | 定义 | 最低证据 |
|---|---|---|
| `ensemble_exceedance_frequency` | ensemble 中超阈成员比例 | 成员数、rainfall/parameter seed、版本 |
| `calibrated_residual` | 用历史残差分布映射点预测为超阈概率 | 样本数、适用 lead-time/量级区间、有效期 |
| `conformal` | conformal prediction set 是否包含超阈区域 | calibration set、coverage target |
| `calibrated_regression` | 独立训练的 probability calibration model | reliability curve、Brier score、有效期 |

### 8.3 发布门禁

正式 alert `confidence MUST NOT be null`。若：

- method 缺失；
- 校准工件过期；
- 输入量级/lead time 超出校准域；
- confidence 非有限数或不在 0..1；

则 horizon point 仍可保存，但：

```text
publishable = false
publication_block_reason = confidence_unavailable|out_of_domain|stale_calibration
```

且 MUST NOT 创建 forecast alert、SSE 或通知。

### 8.4 两套校准必须分离

- `model_calibration_id`：RMSE/NSE 参数率定，服务 AC-5。
- `probability_calibration_id`：事件概率校准，服务 FR-4 confidence。

高质量 NSE 不自动证明 confidence 校准良好。测试必须分别报告 RMSE/NSE 与 reliability/Brier score。

## 9. 风险分与 Top-N

### 9.1 v1 公式

对活动告警定义：

```text
severity = info:0.25, warning:0.60, critical:1.00
urgency = observed ? 1.0 : max(0, 1 - lead_time_minutes / 120)
impact = min(1.0, max(0, (metric_value - threshold_value) / risk_scale))
confidence_term = confidence

risk_score = round(100 * (
    0.35 * confidence_term +
    0.30 * severity +
    0.20 * urgency +
    0.15 * impact
), 2)
```

`risk_scale > 0` MUST 来自版本化 `forecast_rule`，例如某节点水深从阈值再多 0.30m 达到 impact=1；MUST NOT 在代码中写死跨项目深度。

### 9.2 排序

`GET /api/forecast/risk-ranking`：

1. 只返回 `status IN (open, acknowledged)`。
2. 只返回其来源 nowcast 未 stale 的告警。
3. `ORDER BY risk_score DESC, expected_at ASC, id ASC`。
4. `limit` 默认 20，范围 1..100。
5. `as_of` 可指定已完成 run；省略时取最新成功 run。
6. 响应必须返回 `risk_policy_version`、run ID、生成时间和每项分量。

v1 分数不得显示为“内涝概率/伤亡概率”。如果业务需要人口暴露、道路阻断或关键设施权重，必须新增数据字段并发布 `risk_policy_version=v2`。

## 10. REST API

所有 forecast GET 需要已认证 viewer/operator/admin；转换需要 operator/admin。响应使用强类型 schema，不返回裸 dict。

### 10.1 `GET /api/forecast/latest`

Query：

```text
network_id: UUID required
limit: int 1..200 default 100
status: open|acknowledged optional
```

响应：

```json
{
  "schema_version": 1,
  "data_state": "ready|stale|not_ready",
  "nowcast_run": {
    "id": "uuid",
    "scheduled_for": "2026-09-24T10:00:00Z",
    "forecast_origin_at": "2026-09-24T10:00:00Z",
    "issued_at": "2026-09-24T10:02:10Z",
    "published_at": "2026-09-24T10:02:11Z",
    "simulation_id": "uuid"
  },
  "alerts": [],
  "stale": false,
  "age_seconds": 0,
  "server_time": "2026-09-24T10:02:30Z"
}
```

语义：

- 从未成功：200 + `data_state=not_ready`，不伪造空风险为“正常”。
- 最近成功但 `server_time-issued_at > FORECAST_FRESH_MAX_AGE_SECONDS`（默认 900 s），或已有 due 时隙未成功：200 + `data_state=stale`；deadline 只控制当次发布资格，不能刷新 age。
- DB/依赖不可用：503，禁止 mock fallback。

### 10.2 `GET /api/forecast/horizon`

Query：

```text
network_id: UUID required
target_type: node|pipe_segment required
target_id: UUID required
from: UTC time required
to: UTC time required
from <= to，且跨度 <= 2h
step_minutes: fixed 5
as_of: 可选，选已完成 simulation/run
```

每个 point：

```json
{
  "frame_at": "2026-09-24T10:35:00Z",
  "depth_m": 0.42,
  "fill_ratio": null,
  "threshold": 0.30,
  "exceedance": true,
  "confidence": 0.86,
  "confidence_method": "ensemble_exceedance_frequency",
  "publishable": true,
  "simulation_id": "uuid",
  "nowcast_run_id": "uuid"
}
```

`from/to` 非 5 分钟网格、跨度超 2h、目标不匹配网络时返回 422。接口直接读 simulation frame repository，MUST NOT 依赖浏览器已有 Zustand 缓存。

### 10.3 `GET /api/forecast/risk-ranking`

```json
{
  "schema_version": 1,
  "data_state": "ready",
  "risk_policy_version": "v1",
  "as_of": "2026-09-24T10:02:10Z",
  "nowcast_run_id": "uuid",
  "items": [
    {
      "rank": 1,
      "alert": {},
      "risk_score": 82.4,
      "score_components": {
        "confidence": 0.86,
        "severity": 1.0,
        "urgency": 0.7083,
        "impact": 0.4
      }
    }
  ]
}
```

无新鲜 run 时返回 `data_state=stale|not_ready` 和空 items；不得把旧风险包装成当前风险。

### 10.4 `GET /api/forecast/alerts`

供告警中心使用，支持 `status/phase/level/cursor/limit`。必须返回 `source=forecast`。旧 `GET /api/alerts` 保持原路径和旧字段；前端用 `id + source` 合并并以 `id` 最终去重。

### 10.5 错误 envelope

保持现有 `detail` 兼容，并增加稳定 `code/request_id/errors`：

```json
{
  "detail": "告警版本已变化",
  "code": "FORECAST_VERSION_CONFLICT",
  "request_id": "uuid",
  "errors": []
}
```

| HTTP | code | 语义 |
|---|---|---|
| 401 | `UNAUTHENTICATED` | 无/坏 token |
| 403 | `FORBIDDEN` | 角色不足 |
| 404 | `FORECAST_ALERT_NOT_FOUND` / `NETWORK_NOT_FOUND` | 资源不存在 |
| 409 | `FORECAST_VERSION_CONFLICT` | expected_version 过期 |
| 409 | `INVALID_FORECAST_TRANSITION` | 非法状态转换 |
| 409 | `IDEMPOTENCY_KEY_REUSED` | key 被不同 body 使用 |
| 409 | `SSE_CURSOR_EXPIRED` | Last-Event-ID 早于保留边界 |
| 422 | `VALIDATION_ERROR` | 字段/范围/网格错误 |
| 503 | `FORECAST_UNAVAILABLE` | DB/必要依赖不可用 |

## 11. SSE 契约

### 11.1 连接与认证

`GET /api/forecast/events?network_id=<uuid>`

- JWT 在 `Authorization` header；禁止 query token。
- 前端 MUST 使用 fetch streaming，不能直接复用当前 `new EventSource('/api/sse/events')`（`frontend/src/hooks/useSSE.ts:47-57`）。
- 旧 SSE 的公开/匿名问题另行修复，但 forecast 不等待该修复，也不得继承其匿名策略。

### 11.2 wire format

```text
id: 1842
event: forecast
retry: 3000
data: {"schema_version":1,"event_id":"uuid","event_type":"alert.created",...}
```

data envelope：

```json
{
  "schema_version": 1,
  "event_id": "5a6d...",
  "event_type": "alert.created",
  "occurred_at": "2026-09-24T10:02:11Z",
  "network_id": "uuid",
  "nowcast_run_id": "uuid",
  "simulation_id": "uuid",
  "alert": {
    "id": "uuid",
    "type": "flood_forecast",
    "status": "open",
    "phase": "predicted",
    "target": {"type": "node", "id": "uuid"},
    "expected_at": "2026-09-24T10:35:00Z",
    "lead_time_minutes": 35,
    "measurements": {"depth_m": 0.42, "fill_ratio": null},
    "confidence": 0.86,
    "confidence_method": "ensemble_exceedance_frequency",
    "risk_score": 78.4,
    "version": 1
  }
}
```

### 11.3 提交与 replay

1. alert 状态、transition、event outbox 在一个 DB 事务提交。
2. outbox relay 发布 `scn:forecast` Redis channel；API 进程只消费并 fan-out，不直接广播业务事件。
3. SSE `id` 使用 outbox `sequence`；data 的 `event_id` 用于业务去重。
4. 客户端按 `alert.version` 幂等：更低版本忽略；同 event_id 重复忽略。
5. `Last-Event-ID` 有效时先按 sequence replay，再接 live。
6. cursor 无效或早于保留边界：连接前返回 409 `SSE_CURSOR_EXPIRED`；客户端 GET latest/risk 后以新 cursor 重连。
7. 客户端队列满时关闭连接并让客户端携带最后已处理 ID 重连；MUST NOT 静默跳过事件。
8. Redis 暂时不可用时 outbox 保持未发布；恢复后 relay 继续。告警 REST 事实不丢失。

### 11.4 事件触发

- `alert.created`：首次创建；默认产生告警中心/SSE，是否外发取决于通知策略。
- `alert.updated`：预计时刻/lead/risk 档位物化变化；同一 alert。
- `alert.escalated`：warning→critical；允许额外通知，但不是新告警。
- `alert.observed`：预测转实测；允许一次“已发生”通知。
- `alert.resolved/expired`：终态。

单纯 confidence 小数变化不发送事件，客户端以 REST horizon/latest 为准。

## 12. 通知契约

### 12.1 触发与抑制

- `alert.created`：info 只进告警中心/SSE；warning/critical 按配置进入 email/webhook。
- `alert.escalated`：触发一次升级通知。
- `alert.observed`：warning/critical 触发一次“已发生”通知。
- `alert.updated`：默认不通知。
- `alert.resolved`：critical 可配置通知；info 默认不通知。
- 30 分钟 suppressed candidate 永不产生 delivery。
- 外部通知失败不回滚 alert/outbox，也不改变 alert status。

### 12.2 delivery 状态机

```text
pending -> sending -> delivered
pending/sending -> retry_wait -> sending
retry_wait -> dead_letter（达到最大尝试）
policy 不匹配 -> suppressed
```

- worker claim 使用 lease；崩溃后 lease 到期可重领。
- 默认最多 5 次：`t+0, +1m, +5m, +15m, +1h`；每次记录 `attempt_count/next_attempt_at/last_error_code`。
- webhook 只允许管理员配置的 HTTPS allowlist；禁止私网/loopback/link-local、任意 redirect 和 DNS rebinding。
- webhook MUST 带 `X-Forecast-Event-Id`、`Idempotency-Key` 和 HMAC signature。
- SMTP MUST 设置 connect/read timeout，并在线程池或 async client 中执行；MUST NOT 在 event loop 直接调用同步 `smtplib`。
- HTML 标题/描述必须转义。
- 浏览器 notification 只是 UX 辅助，不是送达事实；前端去重键 MUST 使用 `event_id`，不能只使用 `alert.id`。

现有同步实现证据：`backend/app/services/notification_service.py:27-107`；producer 忽略返回值：`backend/app/workers/inference_worker.py:215-223`。

## 13. 失败与降级语义

| 故障 | API/任务结果 | 告警/事件结果 | 恢复 |
|---|---|---|---|
| 无成功 nowcast | 200 `not_ready` | 无新告警 | 下个时隙 |
| nowcast 超 3 分钟 | run=`timed_out` | 不发布 late alert | 记录 SLO breach；下个时隙 |
| 仿真失败/非瞬态错误 | run=`failed` | 原子回滚本 run 的候选发布 | 最多 2 次 retry |
| confidence 缺失/过期 | point=`publishable=false` | 不建 alert | 有效校准工件后重跑 |
| DB 不可用 | 503 | 无部分提交 | 连接恢复后按唯一键重放 |
| Redis/outbox relay 不可用 | REST 仍可读；SSE 延迟 | alert/outbox 已持久化 | relay 继续发布 |
| SSE queue 满 | 连接关闭 | 不静默丢；cursor replay | 客户端重连 |
| Last-Event-ID 过期 | 409 | 不伪造连续历史 | REST snapshot 后重连 |
| email/webhook timeout | delivery=`retry_wait` | alert 不变 | 指数退避/DLQ |
| 版本冲突 | 409 + current state | 不覆盖 | 客户端刷新后重试 |
| 实测缺失/低质量 | alert 保持 predicted | 不标记 observed | 合格观测到达后转换 |
| 旧 `/alerts` DB 查询异常 | 保持 legacy 现状，另行修复 | forecast 不依赖其 mock fallback | forecast 专用 repository |

**MUST NOT 的降级：** forecast API 不得返回随机值、mock generator 结果或无来源的 `confidence=0.5` 来伪装可用。

## 14. 配置契约

通过 pydantic-settings 注入，至少包括：

```text
NOWCAST_INTERVAL_MINUTES=10
NOWCAST_HORIZON_MINUTES=120
NOWCAST_STEP_MINUTES=5
NOWCAST_DEADLINE_SECONDS=180
NOWCAST_MAX_RETRIES=2
FORECAST_EVENT_WINDOW_MINUTES=30
FORECAST_FRESH_MAX_AGE_SECONDS=900
FORECAST_EXPIRY_GRACE_MINUTES=15
FORECAST_NO_FORECAST_CONFIRMATIONS=2
FORECAST_CLEAR_DEBOUNCE_MINUTES=10
FORECAST_RISK_POLICY_VERSION=v1
HYDRAULIC_ARTIFACT_ROOT=<path>
FORECAST_POLICY_CONFIG_PATH=<controlled local config>
FORECAST_SSE_QUEUE_SIZE=256
FORECAST_SSE_REPLAY_RETENTION_HOURS=168
FORECAST_SSE_MAX_CONNECTION_SECONDS=900
NOTIFICATION_MAX_ATTEMPTS=5
```

启动时校验：`horizon % step == 0`、`0 < deadline <= 180`、freshness 独立为正、`event_window > 0`、queue/retention 有上限；policy config 中每个 calibration/rule ID 必须存在、hash/有效期/适用域匹配。production 还必须先通过 JWT/fallback/RBAC/rate-limit G2；不得用弱默认 secret。

## 15. 文件所有权

| 文件/目录 | 唯一职责 | 明确禁止 |
|---|---|---|
| `backend/app/models/forecast_models.py` | 第 4 节 ORM/约束 | 不修改 legacy Alert 字段 |
| `backend/app/schemas/forecast.py` | REST/SSE Pydantic schema、枚举 | 不返回裸 dict |
| `backend/app/repositories/forecast_repository.py` | 状态转换事务、行锁、唯一冲突处理 | 不调用 Redis/SMTP |
| `backend/app/services/forecast/nowcast.py` | 周期状态、deadline、materialize | 不直接广播 |
| `backend/app/services/forecast/event_window.py` | suppression key、合并/冷却 | 不依赖进程内状态 |
| `backend/app/services/forecast/confidence.py` | 发布门禁与校准域判断 | 不调用 AI detection confidence |
| `backend/app/services/forecast/risk.py` | v1 公式和稳定排序 | 不直接访问 HTTP/SSE |
| `backend/app/services/forecast/event_publisher.py` | outbox relay、Redis fan-out、replay | 不创建 alert |
| `backend/app/services/notification/delivery.py` | delivery claim/retry/DLQ | 不持有 DB 事务等待网络 |
| `backend/app/api/routes/forecast.py` | 认证、参数、response/error mapping | 不放业务算法 |
| `backend/app/workers/nowcast_worker.py` | scheduler/worker 生命周期 | 不复制 simulation worker 状态机 |
| `backend/alembic/versions/0002_forecast_contract.py` | 本次表/约束/索引 | 不修改 `0001_initial` 历史 |
| `frontend/src/features/forecast/types.ts` | forecast discriminated unions | 不扩展 legacy Alert 为任意 JSON |
| `frontend/src/features/forecast/api.ts` | typed REST、错误保真 | 不把 403 转成普通 Error 字符串 |
| `frontend/src/features/forecast/useForecastSSE.ts` | fetch SSE、cursor、版本去重 | 不复用匿名 legacy EventSource |
| `frontend/src/features/forecast/store.ts` | active alerts/risk cache | 不存全量 90 天 frames |
| `backend/app/core/config.py` | 仅添加 Settings 字段和校验 | 不放业务公式 |
| `backend/app/main.py` | 注册 router/worker 生命周期 | 不写 materialization 逻辑 |

跨域所有权：simulation contract 拥有 `simulations/simulation_frames`；network contract 拥有节点/管段/规则绑定；本契约只通过 repository 接口消费。`backend/app/models/db_models.py`、`backend/app/services/sse_manager.py`、`frontend/src/hooks/useSSE.ts` 和旧 `notification_service.py` 保持 legacy 所有权，forecast 不把新语义塞入这些文件。

## 16. 可观测性

### 16.1 Metrics

```text
forecast_nowcast_duration_seconds{status,solver}
forecast_nowcast_to_alert_seconds
forecast_runs_total{terminal_status}
forecast_alerts_created_total{type,level}
forecast_alert_candidates_suppressed_total{type,reason}
forecast_active_alerts{type,level}
forecast_sse_publish_lag_seconds
forecast_sse_queue_overflow_total
forecast_stale_runs_total
notification_delivery_total{channel,status}
```

Prometheus label MUST NOT 使用 `alert_id/event_id/simulation_id` 等高基数 ID；这些 ID 放结构化日志。

### 16.2 日志

每条 nowcast/forecast/notification 日志 MUST 包含适用字段：

```text
request_id, nowcast_run_id, simulation_id, alert_id, event_id, transition_version
```

现有 middleware 只保证 request ID 和处理时间（`backend/app/main.py:241-258`），尚无 simulation/forecast 关联字段。

## 17. 验收证据

以下命令和断言是实现完成后的必需证据；本轮因功能尚不存在，**没有运行这些测试，也不能标记通过**。

### 17.1 状态机与事件窗

文件：`backend/tests/forecast/test_alert_state_machine.py`、`test_event_window.py`

必须证明：

1. 所有合法转换和非法转换符合第 5 节。
2. `observed` 不能由时间推进或 acknowledge 产生。
3. 20 个并发候选针对同一 suppression key 最终只有一个 active alert。
4. 活动期持续 2 小时不会产生第二个 alert ID。
5. 终态后 29m59s 的候选被 suppressed，30m00s 可创建新 alert。
6. 过期 run 的 late result 不创建告警。
7. 真实 PostgreSQL 锁/唯一约束测试通过，不能只跑 SQLite。

目标命令：

```bash
cd backend
python -m pytest tests/forecast/test_alert_state_machine.py tests/forecast/test_event_window.py -v --tb=short
```

### 17.2 幂等与 API

文件：`test_idempotency.py`、`test_forecast_api.py`

必须证明：

- 重复 scheduler 只有一个 run。
- 相同转换 key 重放同响应；不同 body 409。
- stale expected_version 409 且不覆盖状态。
- 401/403/404/409/422/503 错误 envelope 保留 `detail` 并有稳定 code。
- OpenAPI 中所有 forecast response 有 schema，Swagger 可生成。
- latest 的 `not_ready/stale/ready` 三态不会使用 mock。

目标命令：

```bash
cd backend
python -m pytest tests/forecast/test_idempotency.py tests/forecast/test_forecast_api.py -v --tb=short
```

### 17.3 Confidence 与风险

文件：`test_confidence.py`、`test_risk.py`

必须证明：

1. null、NaN、越界、过期校准、out-of-domain 均 `publishable=false`。
2. ensemble/conformal fixture 的 confidence 落在 0..1，方法与 calibration ID 可追溯。
3. 固定 fixture 的 risk score、分量、tie-break 与手算一致。
4. stale run 不进入 risk-ranking。
5. 分数不被标成概率。

目标命令：

```bash
cd backend
python -m pytest tests/forecast/test_confidence.py tests/forecast/test_risk.py -v --tb=short
```

### 17.4 SSE 与通知

文件：`test_sse_replay.py`、`test_notification_delivery.py`

必须用两个 API worker + PostgreSQL + Redis 证明：

- outbox 事务回滚时无 SSE。
- relay 在“发布后、标记前”崩溃会重复同一 event_id，但不会产生第二业务事件。
- Last-Event-ID 可跨 worker/重启 replay。
- cursor 过期返回 409 并要求 REST resync。
- 慢客户端 queue 满关闭后可从最后 ID 恢复。
- 未认证连接被拒绝。
- SMTP/webhook timeout/5xx 会 retry，4xx/耗尽进入 dead_letter，alert 不回滚。
- webhook HMAC、Idempotency-Key、HTML escaping 正确。

目标命令：

```bash
cd backend
python -m pytest tests/forecast/test_sse_replay.py tests/forecast/test_notification_delivery.py -v --tb=short
```

### 17.5 AC-4 端到端

固定时钟/固定 solver fixture：`issued_at=2026-09-24T10:00:00Z`，10:35 预测节点水位 0.42m、confidence 0.86。

必须断言：

0. `issued_at/forecast_origin_at/published_at` 单调且不可变；`server_time-issued_at` 跨 900 s 边界转 stale；probability/rule artifact hash/expiry/domain 负向均不发布。
1. 告警中心可查询到同一 `alert_id`。
2. `lead_time_minutes == 35` 且 `simulation_id` 可追溯。
3. 认证 SSE 收到一次 `forecast/alert.created`。
4. 10 分钟内后续 5 分钟 frames 更新同一 alert，不产生重复 ID。
5. 30 分钟冷却内重复候选 suppressed。
6. 合格实测到达后同一 alert `phase=observed`；仅时间经过不会转换。
7. resolve/expire 终态后重新发生才创建新 ID。
8. 100% 业务 `alert.created` 有可查询 transition/outbox；重复传输不增加业务行。

目标命令：

```bash
python scripts/verify_hydraulic_stack.py --gate ac4 --profile postgis --cycles 30 --evidence-dir "artifacts/hydraulic/${RUN_ID}/ac4"
```

该 entrypoint 必须包含 fake-clock 100 时隙和 30 真实周期；当前脚本不存在，所以本轮为 NOT RUN。

### 17.6 3 分钟 SLO

- CI 使用 fake clock 跑 100 个 10 分钟时隙，验证状态、deadline、stale 和重试。
- staging/nightly 真实运行至少 30 个连续周期；每个 `forecast_nowcast_to_alert_seconds <= 180`，缺任一周期即失败。
- nowcast worker 必须有保留/优先槽；一般 24h 仿真占满并发 2 时仍需满足测试，否则 FR-3/NFR 组合被判不可行。

### 17.7 API P95 与前端

- 用 5000 节点、10000 管段、1000 polygon 数据集对新 API 压测 10 分钟、20 VU；P95 <= 500ms，错误率 < 1%。
- Vitest 覆盖 DTO normalization、fetch SSE 重连、event/version 去重、risk 排序、预测/已发生非纯颜色视觉。
- Playwright 覆盖 `/simulation` 告警卡片、Top-N、phase 文案/图标、401/403/409、刷新后状态一致。

目标命令：

```bash
cd frontend
npm ci
npm test
npm run lint
npm run format:check
npm run build
npx playwright test e2e/forecast
```

### 17.8 旧能力零回归

目标命令：

```bash
cd backend
python -m pytest tests/ -v --tb=short
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001

cd ../frontend
npm test
npm run build

cd ..
docker compose config --quiet
docker compose up -d --build
```

还必须比较改造前后的 OpenAPI：旧 `/alerts`、旧 SSE event name/payload 和旧页面行为不得改变。现有基线并非全绿，且目标环境/Compose 尚未在本轮验证，证据见 `docs/hydraulic-twin/recon/quality.md:380-391`。

## 18. 不可行点与替代方案

| 不可行点 | 原因 | 本契约选择 | 备选及代价 |
|---|---|---|---|
| 在旧 Alert 上加几个 metadata 字段 | 无 target/phase/version/事件窗，且 legacy mock fallback 会掩盖故障 | 独立 forecast 表 + 双源合并 | 扩旧表会牵涉 enum/旧 API/测试，回归面更大 |
| 复用旧 `/api/sse/events` 发 forecast | 匿名、进程内 ID/history、direct+Redis 双发 | 新认证 endpoint + DB outbox | 给旧 SSE 加认证会改变现有客户端；不应与 forecast 绑死 |
| SSE/邮件 exactly-once | 崩溃、网络和 provider ACK 无法提供物理 exactly-once | at-least-once + 稳定 ID + 幂等业务键 | exactly-once messaging 产品也需端到端幂等，且违反“不新增数据库产品”约束时收益有限 |
| 单次确定性结果直接给 confidence | 没有概率分布，填 1.0/0.5 都是伪科学 | ensemble/校准；否则不发布 | heuristic 可展示为 `model_score`，但不能满足“置信度”语义 |
| NSE/RMSE 代替概率校准 | 衡量连续误差，不等同事件概率 | 两套 calibration ID | 需额外 reliability/Brier 工件和测试 |
| 固定 30 分钟时间桶 | 事件跨桶会重复，桶边界可双发 | 活动 episode + 终态冷却 | tumbling window 实现简单但运营噪声高 |
| nowcast 排在一般仿真队列 | 仿真 timeout 10 分钟，无法保证 3 分钟告警 | 保留/优先 nowcast lane | 可提前预计算，但数据新鲜度和资源模型更复杂 |
| 原生 EventSource + Bearer | EventSource 不能设置 Authorization header | fetch-based SSE | HttpOnly cookie 或一次性 ticket 也可行，但会扩大认证改造 |
| 复用同步通知服务 | SMTP 阻塞 event loop、无投递记录/重试 | delivery outbox + 独立 worker | 同步发送实现快，但会破坏 3 分钟 SLO |
| 用旧 mock 当 nowcast | 无可复现输入/模型/校准链 | 正式路径 fail closed | demo 可显式 `source=synthetic`，但不得标为 production confidence |

## 19. PRD/AC 追踪

| PRD 项 | 契约位置 | 验收证据 |
|---|---|---|
| FR-4 10 分钟 nowcast、0–2h/5 分钟 | 4.2、5.1、14 | horizon schema、100 时隙 fake-clock、真实周期 |
| `flood_forecast` / `pipe_surcharge` | 3.1、4.1、4.3 | API schema + DB enum + 告警中心测试 |
| target/expected/lead/depth/confidence/simulation_id | 4.1、8、10 | 字段非空约束、AC-4 E2E |
| 告警中心/SSE/通知 | 10.4、11、12 | 双源合并、两 worker replay、delivery retry |
| 预计/已发生区分 | 5.2、11、17.7 | phase 转换与可访问视觉测试 |
| Top-N 风险 | 9 | golden formula/order、stale 排除 |
| 默认 30 分钟事件窗 | 6 | 并发、边界 29m59s/30m00s 测试 |
| FR-5 latest/horizon/risk-ranking/SSE | 10、11 | OpenAPI + 权限/错误/重连测试 |
| NFR nowcast→alert <=3 分钟 | 5.1、14、17.6 | 30 个真实周期 max <=180s |
| AC-4 窗口内不重复 | 6.2、17.5 | 一个 active ID、一次 created、suppressed 计数 |
| 现有 API/SSE 零回归 | 3.3、15、17.8 | legacy characterization + OpenAPI diff |

## 20. 本次审查实际运行的只读检查

以下检查在本次审查中执行；它们证明“缺失/现状”，不证明未来实现通过。

```text
$ git rev-parse --short HEAD
ca99e04

$ git status --short --branch
## main...origin/main
?? docs/

$ rg -c '^@router\.(get|post|put|delete|patch|websocket)' backend/app/api/routes.py
31
$ rg -c '^@router\.(get|post|put|delete|patch)' backend/app/api/routes.py
30
$ rg -c '^@router\.websocket' backend/app/api/routes.py
1
```

跨源码能力摘要：

```text
forecast_term_hits={'flood_forecast': 0, 'pipe_surcharge': 0,
                    'lead_time_minutes': 0, 'simulation_id': 0,
                    'nowcast': 0, 'risk-ranking': 0}
alert_model_has_forecast_fields=False
sse_history_1000=True
sse_queue_256=True
notify_sync_smtplib=True
notify_has_delivery_record=False
ts_has_forecast_event=False
```

未运行：pytest、Vitest、build、Compose、Alembic upgrade、真实 SSE/通知、性能测试。原因是本任务只产出契约，且相应实现尚不存在；不得把第 17 节目标命令报告为已通过。
