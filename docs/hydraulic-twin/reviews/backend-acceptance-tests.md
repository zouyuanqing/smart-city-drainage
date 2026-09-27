# 后端验收测试补齐审查

- 审查对象：`backend/tests/acceptance/**`、`backend/tests/fixtures/hydraulic/acceptance/v1/**`，并对照真实水力 API、任务/率定/预测服务。
- 规范口径：采用 `docs/hydraulic-twin/PRD_SUPPLEMENT.md` 的 v3 AC/G0～G6 条款和 `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md` 的证据/错误/任务条款；`SOURCE_PRD.md:16-32` 保留 FR/AC 原始边界。
- 结论：**NOT_APPROVED / BLOCKED**。本轮只读审查，没有修改生产代码或测试。

## 本轮实际检查

| 检查 | 命令 | 结果与解释 |
|---|---|---|
| 目标验收套件 | `cd smart-city-drainage/backend && .venv/Scripts/python.exe -m pytest -q tests/acceptance` | `26 passed, 3 xfailed, 1238 warnings in 4.90s`；3 个 xfail 仍是已知契约缺口，不是完整通过。 |
| 恶意路径定向诊断 | `cd smart-city-drainage/backend && .venv/Scripts/python.exe -m pytest -q -rx tests/acceptance/test_negative_security.py -k 'windows-drive or unc-path'` | `2 xfailed`。实际响应分别为 `415/UNSUPPORTED_IMPORT_FORMAT` 和 `422/INVALID_INP`，不是 fixture 要求的 `422/UNSAFE_PATH`。 |
| 率定定向诊断 | `cd smart-city-drainage/backend && .venv/Scripts/python.exe -m pytest -q tests/acceptance/test_acceptance_matrix.py::test_ac5_calibration_fixture_meets_improvement_thresholds tests/acceptance/test_acceptance_matrix.py::test_ac5_synthetic_observations_cannot_claim_ac5_eligibility tests/acceptance/test_acceptance_matrix.py::test_ac5_observed_comparison_exposes_missing_verified_pairs` | `2 passed, 1 xfailed`；正的测试只证明内存校准函数能算出改善，观测端点仍为空。 |
| 旧 SSE 回归 | `cd smart-city-drainage/backend && .venv/Scripts/python.exe -m pytest -q tests/security/test_auth_hardening.py::test_production_legacy_sse_remains_public` | `1 passed`，但被测测试把 generator 替换成不产出事件的假 generator（见 finding 6）。 |
| 生产 durable gate 交叉检查 | `cd smart-city-drainage/backend && .venv/Scripts/python.exe -m pytest -q tests/integration/test_backend_integration.py::test_production_hydraulic_api_fails_before_route_execution` | `1 passed`；现有生产门禁明确返回 `503/DURABLE_STORE_UNAVAILABLE`。这与 acceptance 中把 API 层行为标为 verified 的做法矛盾。 |
| 收集/矩阵静态检查 | `cd smart-city-drainage/backend && .venv/Scripts/python.exe -m pytest --collect-only -q tests/acceptance`；矩阵 Python 检查 | `29 tests collected`；矩阵状态为 AC-1/2/4/6/7=`verified`，AC-3=`partial`，AC-5=`gap`，AC-8=`not_run`；AC-8 的 `tests` 为空。 |
| multipart 文件名探针 | `cd smart-city-drainage/backend && .venv/Scripts/python.exe - <<'PY' ... ASGITransport ...` | `C:\\Windows\\system.ini` 到达 ASGI 应用时变成 `system.ini`；UNC 名称被改写为 `\\server\\share%0Dain.inp`。因此当前 HTTP fixture 并不等价于原始恶意文件名到达服务端。 |

实现摘要中的“全后端 509 passed”等结果不是本轮运行结果；本轮没有重复最终全量门禁，也没有运行 G0～G6、Docker/PostGIS/portable、真实 worker/Redis、solver benchmark、前端或 backup/restore。

## Findings

### F-01（高）：AC 状态/证据与真实 durable 实现相矛盾，多个 AC 被误标为 verified

- **问题**：验收 fixture 只重置并使用 `hydraulic_store`，没有把 durable repository 接入测试；生产路由在 `durable_backend=False` 时会 fail closed。因而 AC-1/2/4/6/7 的绿色结果不能证明生产 API 可用。
- **证据**：
  - `backend/tests/acceptance/test_acceptance_matrix.py:71-85` 只调用 `reset_hydraulic_api_store()`，没有设置 durable backend。
  - `backend/app/api/hydraulic_routes.py:277-305` 将 adapter 初始化为 `durable_backend = False`；`:319-327` 和 `backend/app/main.py:1155-1179` 在 production 拒绝该 adapter。
  - AC-1 测试 `backend/tests/acceptance/test_acceptance_matrix.py:242-273` 明确只断言 `status=queued`、`result=None`、`committed=false`、没有 network 行；它没有验证导入最终提交、validation report 或 round-trip。
  - `backend/tests/integration/test_backend_integration.py:306-336` 的真实 production gate 断言返回 `503/DURABLE_STORE_UNAVAILABLE`；本轮该检查通过。
  - 矩阵却在 `backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json:23-35,44-70` 把 AC-1、AC-2、AC-4、AC-6、AC-7 标为 `verified`。冻结契约要求 AC 状态只能是 `PASS/FAIL/NOT RUN/BLOCKED`，且 PASS 必须有目标命令和 artifact：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2539-2554`。
- **影响**：CI 可以全绿而 production hydraulic API 仍返回 503；AC 状态会给集成 owner 错误的完成信号。
- **建议修复**：将当前测试明确命名为 adapter/smoke，状态改为 partial/blocked；或把真实 durable repository、两个 spatial profile、worker finalize 和 machine-readable evidence 接入后再允许 verified/PASS。不得用内存 store 的结果替代 production gate。

### F-02（高）：AC-2 恶意文件矩阵不完整，且已知 UNSAFE_PATH 缺口被 xfail 后仍标 verified

- **问题**：fixture 只有 14 个声明式案例；缺少冻结契约要求的截断、真实 zip bomb、symlink/junction、重复 stable key、CPU/内存炸弹、清理和 readiness 探针。Windows drive/UNC 案例当前返回错误分类后被 strict xfail，整个套件仍 exit 0。
- **证据**：
  - `backend/tests/acceptance/test_negative_security.py:30-49` 对 `windows-drive`、`unc-path` 预置 `pytest.mark.xfail(strict=True)`。
  - 定向命令输出：`415/UNSUPPORTED_IMPORT_FORMAT` 与 `422/INVALID_INP`，而 fixture 要求两者均为 `422/UNSAFE_PATH`。
  - `backend/tests/fixtures/hydraulic/acceptance/v1/malicious_imports.json:1-99` 的 `zip-content` 只有 ZIP magic `504b0304`，没有压缩展开；没有 symlink/junction、truncated、duplicate-key、CPU/memory 样本。
  - `backend/tests/acceptance/test_negative_security.py:90-120` 只比较 job/import/idempotency 三个内存字典长度，并只检查 `/api/networks` 返回 200；没有检查 network 行、quarantine/part/sandbox 文件、绝对路径泄露、进程存活或 `/readyz`。
  - 规范要求路径在扩展名分类前返回 `UNSAFE_PATH=422`、读取 limit+1 删除、覆盖 symlink/junction 和资源炸弹：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:303-316`；G2 还要求前后 readiness 与清理证据：`IMPLEMENTATION_CONTRACT.md:2492`。
  - multipart 探针还表明 fixture filename 在 HTTP parser 层已被改写，不能证明服务端收到原始 Windows/UNC 名称。
- **影响**：路径穿越/资源耗尽回归可能保持绿色；xfail 也没有把错误提升为 gate failure。
- **建议修复**：加入 raw multipart 或直接 ASGI 文件名 fixture，完整覆盖 POSIX/Windows/UNC/ADS/盘符、symlink/junction、截断、真实压缩炸弹、重复 key、CPU/内存样本；对每个案例断言无文件系统和 network 状态残留、readiness 前后正常，并将契约错误作为非 xfail 失败或独立 BLOCKED gate。

### F-03（高）：FR-3/任务恢复只测 InMemoryTaskRepository，不证明 worker 崩溃恢复

- **问题**：测试只验证内存 repository 的租约过期、第二次 claim 和旧 token `complete` 被拒绝；没有真实 worker kill、数据库 lease/fencing、frame/result fence、超时/取消、重试耗尽、workspace 清理或并发槽位。
- **证据**：
  - `backend/tests/acceptance/test_acceptance_matrix.py:374-403` 直接实例化 `InMemoryTaskRepository`，只调用 `recover_expired`、claim 和一次 stale `complete`。
  - `backend/app/services/hydraulic/tasks.py:877-899` 明确该实现是单进程内存 store；真正的 SQL repository 在同文件 `:1446-1833`，本测试没有触及。
  - 规范要求实际 kill worker、数据库扫描、attempt 3 exhausted、frame staging/publish、清理和 2-slot 语义：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:299-305`、`IMPLEMENTATION_CONTRACT.md:2493`。
- **影响**：旧 worker 仍可能写 frame、lease 恢复丢任务、取消/超时留下工作区等问题不会被验收套件发现。
- **建议修复**：增加 PostgreSQL/Timescale 集成 fixture，真实启动/杀掉 worker，验证 DB lease/fencing、旧 attempt frame/result 写入失败、10/60 秒 backoff、第三次失败、600 秒 timeout、cancel finally cleanup 和 slot release；本模块的内存测试保留为单元测试但不计入 AC-3/G3。

### F-04（高）：AC-4 只测顺序调用内存 ForecastAlertService，未证明 nowcast、outbox、SSE 或抑制并发

- **问题**：AC-4 测试使用 `InMemoryForecastAlertStore`、假 artifact resolver 和手工构造 candidate；只覆盖一次 active、一次冷却内和边界新建。没有 20 并发候选、持续 2 小时复用、predicted→observed、冷却内 observed、policy 失效、durable outbox、两 worker/restart replay 或 3 分钟 SLO。
- **证据**：
  - `backend/tests/acceptance/test_acceptance_matrix.py:437-470` 的 resolver 只回显传入 hash，未读取受控 artifact；candidate 由 fixture 字段直接构造。
  - `backend/tests/acceptance/test_acceptance_matrix.py:473-523` 只有顺序 `publish → mark_terminal → inside → boundary`，没有 API `/forecast/latest`、`/forecast/events`、Redis 或数据库行锁。
  - `backend/app/services/hydraulic/forecast_alerts.py:939-953` 是单进程内存 store；本测试没有使用 PostgreSQL `forecast_alert_windows`/`event_outbox`。
  - 规范 AC-4/G4 要求 fake-clock 100 时隙、30 真实周期、两 API worker、durable event、artifact hash 和 30 分钟业务事件唯一：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:588,614-618`、`IMPLEMENTATION_CONTRACT.md:2494`。
  - 矩阵却把 AC-4 标为 `verified`：`backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json:44-49`。
- **影响**：告警重复、SSE 丢失/重发错误、policy 失效仍发布、跨 worker replay 失败等核心风险没有验收保护。
- **建议修复**：增加真实 durable transaction fixture 和 fake-clock/Redis replay 测试；至少覆盖并发唯一 active、活动期复用、29m59s/30m 边界、observed 冷却抑制、invalid/expired artifact、outbox/transition 原子性和 SLO 上限。

### F-05（高）：AC-5 正向 fixture 是自报 external_verified 的内存校准，不是可追溯真实观测

- **问题**：正向率定测试可把任意内存点标成 `external_verified`，并用 evaluator 直接从观测值构造 candidate；没有 source event identity、trust policy resolution、auth/binding/quality、真实 solver/prediction、rainfall/dataset artifact 或 durable calibration report。观测端点的 strict xfail 也明确当前没有 verified pairs。
- **证据**：
  - `backend/tests/fixtures/hydraulic/acceptance/v1/calibration_improvement.json:1-24` 使用固定重复的 dummy provenance hashes，fixture 只有 `trust_level` 字符串。
  - `backend/tests/acceptance/test_acceptance_matrix.py:529-561` 从同一组人工 observation 生成 baseline，再用 evaluator 直接生成 candidate；没有运行水力 solver。
  - `backend/tests/acceptance/test_acceptance_matrix.py:563-595` 只断言 `run_calibration` 的数值门槛和 hash 非空。
  - `backend/app/services/hydraulic/calibration.py:88-125` 的 `ObservationPoint` 允许调用方直接提供 `event_id/trust_level`，而本测试没有 source event、policy、binding 证据。
  - `backend/app/api/hydraulic_routes.py:3378-3394` 当前 observed-comparison 固定返回空 pairs；对应 strict xfail 在 `test_acceptance_matrix.py:632-670`。
  - 规范要求只有真实 source event + `external_verified` policy + active binding + good quality 才能进入 AC-5，并保存 dataset/solver/parameter/code hash：`PRD_SUPPLEMENT.md:108-118,455-480`。
- **影响**：数值改善可能是测试夹具自证的假象，不能用于生产 forecast/AC-5，也不能证明预测实测闭环。
- **建议修复**：用真实 ingest event 和显式 binding fixture，固定独立事件切分，实际运行 baseline/candidate solver 并写入 report artifact；在此之前保持 AC-5 `gap/blocked`，不要把正向内存测试标为 AC-5 通过。

### F-06（高）：AC-6 直接注入假结果，未证明已发布结果的 A/B 合同

- **问题**：`_publish_simulation` 直接修改内存 store，写入空 frame 数组和手填 metrics；A/B 测试只比较手填数字和 frame count，未证明真实 frame 发布、checksum/hash、manifest lineage、单位、zero-baseline relative delta 或可重算性。
- **证据**：
  - `backend/tests/acceptance/test_acceptance_matrix.py:173-203` 直接写 `status=completed`、`published_attempt_id`、metrics 和空 published frames，绕过 worker/finalizer。
  - `backend/tests/acceptance/test_acceptance_matrix.py:672-717` 只检查 289 count、RMSE/NSE delta 和 duration 不兼容；没有 frame 时间/manifest/hash、metric unit、baseline=0、重复结果或持久化重放断言。
  - 生产比较代码虽检查 revision/manifest/start/duration/frame interval：`backend/app/api/hydraulic_routes.py:3445-3455`，但测试没有证明这些值来自真实已发布结果。
  - 规范 A/B 要求同 revision/manifest/time grid、逐项重算、单位和 better direction：`PRD_SUPPLEMENT.md:482-487,590`。
  - 矩阵把 AC-6 标为 `verified`：`matrix.json:57-63`。
- **影响**：坏的 frame/result persistence、错误单位或无法重算的比较仍会被测试放过。
- **建议修复**：通过真实 finalizer/result repository 发布两套结果，断言完整 lineage、frame manifest/checksum、单位和边界（baseline=0、指标缺失、不同 revision/manifest），再保留一个独立 API contract test。

### F-07（高）：旧 API/SSE 零回归测试没有验证 legacy SSE 行为或响应形状

- **问题**：acceptance 只检查三个 GET 的 status 和 OpenAPI path 存在；SSE 只检查 router 中有 path。引用的 security test 将 generator 替换为不 yield 事件的空 generator，不能证明 `connected/sensors/alerts/system` 名称、payload、Last-Event-ID replay 或旧客户端兼容性。
- **证据**：
  - `backend/tests/acceptance/test_acceptance_matrix.py:720-742` 只循环 `legacy["read_paths"]` 的 status，并断言 `route.path` 存在；注释明确避免实际 SSE 请求。
  - `backend/tests/security/test_auth_hardening.py:178-199` 中 `events` 函数只有 `if False: yield {}`，测试只断言 HTTP 200 和 cursor 被传递。
  - `backend/tests/fixtures/hydraulic/acceptance/v1/legacy_api.json:1-14` 没有旧响应字段、事件名、payload 或 replay golden。
  - 生产 legacy route 明确承诺 `sensors/alerts/system` 和 Last-Event-ID：`backend/app/api/routes.py:592-612`。
  - 规范要求旧 API/SSE/告警零回归并比较旧事件/字段：`IMPLEMENTATION_CONTRACT.md:1699,1823`、`PRD_SUPPLEMENT.md:591`。
- **影响**：旧页面/客户端的 wire-level regression 不会被 AC-7 捕获。
- **建议修复**：增加真实可控 SSE fixture，断言首帧、事件名、字段、版本、Last-Event-ID replay 和断线行为；对旧 REST 响应保存 golden/contract diff，而不是只比 HTTP status/path。

### F-08（中）：AC-8 没有测试或 fixture，且矩阵结构检查不会发现

- **问题**：AC-8 明确为 `not_run` 且 `tests=[]`，这与冻结契约要求两 profile fresh Compose、migration、worker、backup/restore 不同；当前矩阵测试不会把这种缺失转成 gate failure。
- **证据**：`backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json:71-78`；收集命令输出 `29 tests collected`，没有 AC-8 测试。冻结 gate 要求两个独立 profile artifact：`IMPLEMENTATION_CONTRACT.md:2490-2497`。
- **影响**：AC-8 缺失不会阻断“验收套件全绿”。
- **建议修复**：由统一 acceptance entrypoint 消费同一 RUN_ID/commit 的 G6 双 profile artifacts；缺任一 artifact 固定 FAIL/NOT RUN 并阻断汇总，而不是仅在 JSON 中写 `not_run` 后继续通过。

### F-09（中）：50 年一遇测试是元数据 smoke，不是 FR-3/AC-3 数值验收

- **问题**：`rainfall_50y_24h.json` 只提供重复强度 pattern 和计数；测试只验证提交后的 scenario/simulation 字段和 289 这个算术值，并通过 helper 手工发布空 frame。没有 4 核 solver、质量/守恒、实际 frame 时间间隔/内容、worker wall time、进度/取消/回放或地图时间轴。
- **证据**：`backend/tests/fixtures/hydraulic/acceptance/v1/rainfall_50y_24h.json:1-10`；`backend/tests/acceptance/test_acceptance_matrix.py:307-337`；helper `:173-203`。矩阵将 AC-3 标为 partial 是诚实的，但没有让缺失的 hard gate 失败。契约 AC-3/性能硬门禁见 `PRD_SUPPLEMENT.md:587,404-413` 和 `IMPLEMENTATION_CONTRACT.md:2522-2525`。
- **建议修复**：保留当前请求契约 smoke，但把 5k/10k solver、289-frame manifest、4 核 benchmark 和 worker E2E 独立设为不可跳过的 G3/G5；未运行时 acceptance 汇总必须为 NOT RUN/BLOCKED。

### F-10（中）：AC 映射验证器只检查字符串形状，不检查测试存在、执行和证据关联

- **问题**：manifest test 只检查 FR/AC ID 集合、状态子集、fixture hash 和 nodeid 前缀；不检查 nodeid 是否可收集、是否在本次运行、AC 所需断言是否存在、status 是否由结果/artifact 推导。AC-8 甚至允许空测试列表。
- **证据**：`backend/tests/acceptance/test_acceptance_matrix.py:206-232`；矩阵 `matrix.json:21-78`。本轮 `pytest --collect-only -q tests/acceptance` 只得到 29 个测试，没有执行 artifact 关联检查。
- **影响**：矩阵可以被改成任意合法字符串并保持绿色，不能作为验收事实源。
- **建议修复**：将矩阵与 `pytest --collect-only`/JUnit 结果、fixture hash、RUN_ID/commit/artifact hash 交叉校验；对每个 AC 强制要求至少一个实际执行的测试或明确 BLOCKED/NOT RUN reason。

## 结论与后续

当前 targeted suite 的通过数不能抵消上述高风险缺口。尤其是：production durable gate 仍是 503、恶意路径错误被 xfail、预测/率定/任务恢复均为内存或手工注入、legacy SSE 没有 wire-level 证据。因此 `approved=false`。

建议优先级：先修正 AC 状态/证据模型并接入真实 durable repository；再补 G2 security、G3 worker、G4 forecast/outbox 和 legacy SSE characterization；最后执行本轮明确未运行的 G0～G6、真实数据库/Redis/worker、solver performance、Compose 和 backup/restore 门禁。
