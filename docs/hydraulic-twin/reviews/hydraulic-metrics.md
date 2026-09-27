# 仿真可观测性独立审查

- 模块：`hydraulic-metrics`（仿真可观测性）
- 审查日期：2026-09-25
- 审查方式：只读复核冻结契约、真实实现、focused tests 与小型定向诊断；未修改实现代码、测试或契约，未重复最终全量门禁。
- 结论：**NOT APPROVED / `approved=false`**

## 1. 审查依据与范围

本次任务规格要求暴露任务时长、失败率、队列深度、运行中数量、结果规模指标，并要求结构化日志带 `simulation_id`；任务同时要求检查计数、耗时直方图、失败与队列状态。冻结实现契约进一步规定：

- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2171-2199` 列出 21 个 Prometheus metric family，禁止把 `simulation_id`、`alert_id`、`event_id` 放入 label。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2201-2203` 要求适用的结构化日志包含 request/user/network/revision/rainfall/simulation/attempt/solver/nowcast/alert/event/phase/error 等字段。
- `docs/hydraulic-twin/operations.md:285-287` 明确 rules/dashboard 不能替代 backend 真实 exporter；backend metrics owner 必须真实暴露指标。
- 真实实现为 `backend/app/services/hydraulic/metrics.py`，focused tests 为 `backend/tests/hydraulic/test_hydraulic_metrics.py`；实现摘要 `docs/hydraulic-twin/implementation/hydraulic-metrics.md:196-202` 已自认 runtime import/call-site 尚未完成。

## 2. 本次实际执行的检查

### 2.1 题目指定的 focused pytest

命令：

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && .venv/Scripts/python.exe -m pytest tests/hydraulic/test_hydraulic_metrics.py -q --disable-warnings
```

解释器：`Python 3.14.4`。

输出：

```text
............                                                             [100%]
12 passed, 376 warnings in 0.03s
```

这只证明 isolated registry 下的 12 个模块测试通过，不证明生产 `/metrics` 接线、worker 接线、3.11 运行时或真实 Prometheus 行为。

### 2.2 Python 3.11 focused pytest（未通过启动条件）

命令：

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && py -V:Astral/CPython3.11.15 -m pytest tests/hydraulic/test_hydraulic_metrics.py -q
```

输出：

```text
C:\Users\zyq\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe: No module named pytest
```

因此 Python 3.11 runtime test **NOT RUN/PASS 未成立**；不能把 3.14 focused PASS 当作 3.11 门禁。

### 2.3 应用 registry / 生产接线诊断

命令：

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && .venv/Scripts/python.exe -c "import app.main as m; from prometheus_client import REGISTRY; n={s.name for f in REGISTRY.collect() for s in f.samples}; print('hydraulic_metrics_in_main=', 'hydraulic_metrics' in vars(m)); print('queue_in_default_registry=', 'simulation_queue_depth' in n); print('duration_in_default_registry=', 'simulation_duration_seconds_count' in n)"
```

输出：

```text
hydraulic_metrics_in_main= False
queue_in_default_registry= False
duration_in_default_registry= False
```

生产接线扫描命令：

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage" && rg -n 'hydraulic_metrics|HYDRAULIC_METRICS|record_simulation_terminal|set_queue_state|set_active_slots|record_simulation_retry|record_solver_fallback|log_simulation_event' backend/app/main.py backend/app/workers/simulation_worker.py || true
```

输出：无匹配（exit 0 由 `|| true` 保留）。

### 2.4 边界/一致性定向诊断

以下均为本次实际执行的 inline Python probe（使用 isolated `CollectorRegistry`，不写仓库文件）：

```text
queue_depth_after_reject= [9.0]
general_after_reject= [1.0]
duration_count_after_reject= [1.0]
nowcast_duration_count_after_reject= [1.0]
```

该 probe 先提交有效值，再提交会抛 `ValueError` 的值；输出证明被拒绝的调用已经改变部分 collector。

```text
arbitrary_labels= True
slot_lines= ['simulation_active_slots{lane="general"} 7.0', 'simulation_active_slots{lane="nowcast"} 9.0']
nowcast_line= ['forecast_nowcast_duration_seconds_count{solver="not-a-solver",status="not-a-terminal-state"} 1.0']
```

```text
duration_sample_on_init= False
failure_sample_on_init= False
nonempty_queue_without_age= [('simulation_queue_depth', 5.0), ('simulation_oldest_queued_seconds', 0.0)]
```

```text
terminal_missing= ['aggregate_version', 'alert_id', 'error_code', 'event_id', 'network_id', 'network_revision_id', 'nowcast_run_id', 'rainfall_scenario_id', 'request_id', 'solver_version', 'user_id']
sensitive_values_logged= C:/sandbox/secret.bin Bearer sentinel-secret
```

```text
[('simulation_duration_seconds_count', 2.0), ('simulation_frames_total', 2.0)]
```

最后一个 probe 对同一 `simulation_id`/同一终态调用两次 `record_simulation_terminal`，结果 duration count 和 frame counter 都从 1 变为 2。

## 3. Findings

### H-01（高）：指标模块没有接入生产 `/metrics`，当前交付实际上不可观测

**证据**

- FastAPI 只在 `backend/app/main.py:217-221` 暴露 instrumentator 的默认 registry；`app.main` 没有导入 `hydraulic_metrics`，应用 registry 诊断显示连 `simulation_queue_depth` 和 `simulation_duration_seconds_count` 都不存在。
- `backend/app/workers/simulation_worker.py:320-326,394-404` 的 worker 日志/执行路径没有 metrics hook；对 `main.py` 与 `simulation_worker.py` 的精确扫描无匹配。
- Prometheus 只 scrape `backend:8000`：`docker/prometheus/prometheus.yml:9-17`；simulation worker 是独立 service，端口为 8010：`docker-compose.yml:174-217`。即使未来 worker 更新自己的进程内 registry，也不会自动出现在当前唯一 scrape target。
- 实现摘要 `docs/hydraulic-twin/implementation/hydraulic-metrics.md:198-202` 明确写明 runtime import/call-site 与 worker/main 接线仍待 integration owner。

**影响**

当前生产 `/metrics` 不会暴露本模块的队列、运行中槽位、终态时长/失败或结果规模指标；任务规格中的“暴露”目标未实现。后续若只在 worker 进程调用本模块，API 进程的 `/metrics` 仍看不到 worker 的进程内计数。

**建议**

由 integration owner 明确指标拓扑并在授权范围内接线：要么由 API 进程从 durable DB snapshot 汇总后更新自己的 registry，要么给 worker 提供真实 exporter 并把 scrape target/共享 multiprocess 目录接入 Prometheus。必须增加启动应用后请求 `/metrics` 的 runtime smoke，而不是只测 isolated registry。

### M-01（中）：冻结的完整结构化日志字段契约没有落地

**证据**

- 冻结契约要求适用日志字段见 `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2201-2203`。
- `record_simulation_terminal` 的签名 `backend/app/services/hydraulic/metrics.py:429-440` 没有 `request_id/user_id/network_id/network_revision_id/rainfall_scenario_id/solver_version` 等上下文参数；实际 log 调用 `:486-498` 只能写入 `simulation_id、solver_id、attempt、phase、status、error_code、duration/result` 的子集，且 `phase` 被固定为 terminal status。
- `record_simulation_retry` 与 `record_solver_fallback` 在 `:500-509` 只增加 counter，完全不产生带 `simulation_id` 的结构化日志。
- 诊断输出显示正常 completed terminal 缺少至少 `request_id、user_id、network_id、network_revision_id、rainfall_scenario_id、solver_version`。

**影响**

直接调用 start/terminal helper 时，窄义要求的 `simulation_id` 存在；但失败、重试、fallback 与终态无法按 request/user/revision/rainfall 关联，不能满足冻结的完整日志字段契约，故障调查和审计关联不完整。

**建议**

引入强类型 simulation context/event，强制携带该事件适用的 request/user/network/revision/rainfall/solver version/phase；retry/fallback 也必须用同一事件上下文写一条结构化日志。测试应从生产 JSON formatter 输出验证字段，而不是只断言 `record.simulation_id`。

### M-02（中）：校验发生在部分写入之后，fail-closed 调用会留下半更新指标

**证据**

- `set_queue_state` 在 `metrics.py:394-400` 先写 queue depth，再校验 oldest age。
- `set_active_slots` 在 `:405-410` 先写 general lane，再校验 nowcast lane。
- `record_simulation_terminal` 在 `:457-478` 先 observe duration、更新 failure/frames，再校验 result bytes/frames；`record_nowcast_terminal` 在 `:521-532` 先 observe duration，再校验 alert latency。
- 定向诊断在被拒绝调用后输出：`queue_depth_after_reject=[9.0]`、`general_after_reject=[1.0]`、`duration_count_after_reject=[1.0]`、`nowcast_duration_count_after_reject=[1.0]`。

**影响**

调用方收到 `ValueError` 后仍可能看到新 queue/terminal 状态；失败率、时长和队列快照会被污染，且“非法输入不影响 collector”的 fail-closed 语义不成立。现有 `test_invalid_metric_inputs_fail_closed` 只断言抛异常，没有断言 registry 未变化，见 `backend/tests/hydraulic/test_hydraulic_metrics.py:320-361`。

**建议**

先完整 normalize/validate 全部输入，再进行任何 mutation；对多步 snapshot 更新使用局部值并在全部成功后一次提交。补充每个拒绝分支的 before/after 状态断言。

### M-03（中）：label 值没有枚举/基数边界，错误或调用方输入可污染 Prometheus

**证据**

- `_as_label` 在 `metrics.py:84-99` 只检查非空和长度不超过 128，不检查 solver、error code、reason、channel、status、alert type/level 的允许集合。
- `record_simulation_retry`、`record_solver_fallback`、forecast/notification 方法 `:500-509,533-560,573-582` 将这些自由字符串直接作为 label；`record_nowcast_terminal` `:511-532` 甚至没有 terminal status allowlist。
- 冻结契约规定 solver 是稳定枚举 `swmm_kw|swmm_dw|py_mw|py_dw|py_ponding`（`IMPLEMENTATION_CONTRACT.md:37-42`），并禁止高基数 ID label（`:2199`）。
- 诊断接受了 128 字符 reason、`untrusted/from` 等值，并生成 `solver="not-a-solver",status="not-a-terminal-state"`。

**影响**

一个错误码/重试原因或被错误传递的外部值就能创建新的 time series，造成 Prometheus 内存、查询和告警成本随输入增长；非法 solver/status 还会污染 dashboard 与 failure ratio。这里虽未把 `simulation_id` 作为 label，但“有界 operational dimension”并未实现。

**建议**

对所有 label 使用领域 enum/allowlist；未知值拒绝或映射到单一 `other`，并对每类 label 设置 cardinality cap。测试应断言 canonical solver/status、未知值和超长/重复错误码的行为。

### M-04（中）：queue/slot snapshot 没有固定领域不变量

**证据**

- `set_queue_state` 的 `oldest_queued_seconds` 默认值为 0，且没有检查 `depth=0` 时 age 必须为 0，或 `depth>0` 时 age 是否来自真实队列；`metrics.py:386-400`。
- `set_active_slots` 只调用 `_as_nonnegative_int`，允许任意大计数：`:402-410`。
- 冻结任务模型固定只有两个 slot，且 general/nowcast 各一个，busy 分支约束见 `IMPLEMENTATION_CONTRACT.md:720-743`；配置也固定 `SIM_GLOBAL_CONCURRENCY=2` 与 one reserved nowcast lane。
- 诊断输出 `depth=5` 且省略 age 时为 `simulation_oldest_queued_seconds=0.0`；同时接受 `general=7,nowcast=9` 并暴露为 7/9。

**影响**

漏传 age 会隐藏队列积压；非法 slot 计数会触发 `SimulationSlotsSaturated` 或使容量 dashboard 失真。指标 snapshot 不能代表 durable DB 的真实队列/槽位状态。

**建议**

让 queue snapshot API 要求显式、可验证的 age，并校验空队列/非空队列关系；slot API 只接受每 lane 的 0/1（或改用与实际固定 slot 数一致的明确类型），并增加 invariant tests。

### M-05（中）：新进程的 duration sample 缺失会触发 `HydraulicMetricsAbsent` 误报

**证据**

- `simulation_duration_seconds` 是带 `solver,status` label 的 Histogram，定义于 `metrics.py:253-259`；初始化逻辑 `:374-384` 没有为固定 label child 建立零值 sample。
- 告警规则 `docker/prometheus/rules/hydraulic-alerts.yml:3-13` 在 10 分钟后对 `absent(simulation_duration_seconds_count)` 报 critical。
- 初始化 isolated registry 的诊断输出：`duration_sample_on_init=False`、`failure_sample_on_init=False`。

**影响**

服务刚启动、尚未完成任何 simulation 时，虽然模块已加载且运行正常，Prometheus 仍会认为 duration metric 缺失并触发假 critical；这会掩盖真正的 exporter 故障。

**建议**

为固定 solver/status label child 建立零值 histogram series（不伪造 observation），或增加明确的 metrics-ready gauge 并调整 absent 规则；增加“新 registry 初始 exposition 不触发 absent”的测试。

### M-06（中）：allow-list 只限制字段名，不限制值，日志仍可泄露 secret/绝对路径

**证据**

- `log_simulation_event` 的 docstring 声称 allow-list 可阻止 secret、raw input、sandbox path，`metrics.py:177-182`；但 `event、solver_id、phase、error_code、status` 等值只经 `_as_label` 后原样放入 `extra`，`:187-229`，没有 UUID/enum/path/token 检查或脱敏。
- 定向诊断实际捕获：`sensitive_values_logged= C:/sandbox/secret.bin Bearer sentinel-secret`。

**影响**

调用方一旦把异常文本、路径或 token 放入“允许字段”，仍会进入 JSON 日志，违反 `IMPLEMENTATION_CONTRACT.md:2203` 的禁止 secret/JWT/sandbox 绝对路径约束，并可能造成凭据泄露。

**建议**

对 ID 强制 UUID、对 solver/status/error code 使用 typed enum；对路径、Bearer/token 模式和异常原文拒绝或脱敏。增加允许字段携带 secret/path 的负向测试。

### M-07（中）：terminal metric recording 无幂等保护，durable replay 会重复计数

**证据**

- `record_simulation_terminal` `metrics.py:429-498` 没有 event/attempt identity 或去重存储，每次调用都 observe/increment。
- 冻结契约要求 durable retry/fencing 与可恢复状态，见 `IMPLEMENTATION_CONTRACT.md:703-719`；worker 恢复/终态处理可能重放同一任务。
- 对同一参数连续调用两次的诊断输出为：`simulation_duration_seconds_count=2.0`、`simulation_frames_total=2.0`。

**影响**

进程在 metric 更新后、数据库终态提交前崩溃或恢复重放时，duration、frame 和 failure ratio 会被重复计算；观测数据不再反映唯一终态事实。

**建议**

让 integration 只在带 fencing/唯一 terminal event 的提交后记录，并提供 bounded dedupe/幂等键；或从 durable DB 状态生成 snapshot。补充 crash-after-record/replay 测试。

### M-08（中）：现有 12 个测试没有覆盖上述生产与失败边界

**证据**

- `backend/tests/hydraulic/test_hydraulic_metrics.py:52-173` 覆盖正常 counter/histogram 和 retry/fallback；`:176-203` 覆盖 snapshot；`:205-265` 覆盖基本 JSON log；`:268-317` 覆盖 metric family/label 名称；`:320-361` 只检查非法输入抛异常。
- 没有测试启动 `app.main` 或请求真实 `/metrics`，没有 worker/API registry 接线测试；没有检查 terminal 必需上下文字段、敏感值拒绝、label allowlist/cardinality、初始 absent、slot/queue 不变量、rejected-call 状态不变或 terminal replay 去重。

**影响**

当前 focused suite 全绿并不能阻止 H-01、M-01～M-07 在生产或恢复路径发生；题目点名的“失败与队列状态”边界仍缺少回归保护。

**建议**

先补 runtime `/metrics` smoke 与 worker/API 拓扑测试，再补上述负向/重放测试；测试应比较 collector before/after 和 JSON formatter 的完整字段，而不是只断言函数返回值或异常类型。

## 4. 通过项与未执行项

### 已确认通过

- 题目指定的 focused metrics tests 在 Python 3.14.4 下实际通过：`12 passed`。
- 正常路径的 duration/failure/cancel/timeout/frame/result-size counter、queue/active snapshot、基本 `simulation_id` JSON 字段和无 `simulation_id` Prometheus label 均有测试覆盖。
- `simulation_result_size_bytes` histogram 的基本 count/sum 断言存在于 `backend/tests/hydraulic/test_hydraulic_metrics.py:121-134`。

### 本轮未运行/不能据此宣称通过

- Python 3.11 runtime pytest：该解释器没有 pytest，已实际尝试并得到 `No module named pytest`。
- 未启动真实 backend/worker、未请求实际 `/metrics`、未启动 Prometheus/Grafana、未运行真实 DB/Redis/Compose。
- 未运行完整 hydraulic suite 或最终 G0-G6/AC 门禁；这是按本次审查要求避免重复最终全量门禁，不能把 focused 12-pass 解释为整体发布通过。
- 未验证多进程 Prometheus registry/worker exporter 方案；当前代码和 Compose 证据反而显示该接线尚未闭合。

## 5. 决策

**`approved=false`。** H-01 使生产可观测性交付尚未成立；M-01～M-07 还分别破坏日志可追溯性、collector 一致性、label 基数/领域约束、队列状态真实性、告警启动语义、日志安全和重放幂等性。至少应先完成 H-01 的 runtime 接线与 `/metrics` smoke，再修复/补测 M-01～M-08 后重新独立审查。
