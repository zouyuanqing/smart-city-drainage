# 验证证据台账（VALIDATION_EVIDENCE）

> 记录日期：2026-09-28
> 用途：登记本轮**门禁（final gates）**、**阶段结果（stages）**与**环境事实**，并核对 `implementation/` 与 `reviews/` 两目录的模块证据覆盖情况。
>
> **本文件是证据登记，不是完成度声明。** 任何"未运行""环境不可用""失败"条目一律照原样保留，不折算为通过，不改写为计划。

## 0. 证据分级约定

| 级别 | 含义 |
|---|---|
| `VERIFIED / PASS` | 该命令本次实际执行，`exitCode=0`，且证据串中含结果计数或成功标记 |
| `VERIFIED / FAIL` | 该命令本次实际执行，`exitCode != 0`。区分**测试真失败**与**调用方式错误**（后者未执行任何测试） |
| `ENVIRONMENT-BLOCKED` | 检查本身未执行到底，原因是运行环境不满足冻结目标（如目标 Node/CPU/RAM 不匹配、Docker 不可用） |
| `NOT RUN` | 本轮明确没有执行；文档内不写任何通过 |
| `MISSING EXIT CODE` | 证据串里没有 exitCode 字段，无法判定是否通过 |

> 证据来源标注：**[payload]** = 本次 ask 传入的证据串，我未复跑；**[verified-2026-09-28]** = 我在本次会话中亲自执行并记录的命令与输出。

---

## 1. 环境事实

### 1.1 payload 提供的事实（原样登记）

| 项 | payload 值 |
|---|---|
| `gitHead` | `ca99e0485fb12fbecbd565e79d81f745f8d4a748` |
| `pythonExact` | `Python 3.14.4` |
| `nodeVersion` | `v25.9.0` |
| `dockerAvailable` | `false` |

### 1.2 我在本次会话中复跑的环境事实 [verified-2026-09-28]

```text
$ git rev-parse HEAD
ca99e0485fb12fbecbd565e79d81f745f8d4a748          # 与 payload gitHead 一致

$ git status --porcelain | wc -l
157                                              # 工作树 dirty，157 条变更

$ where python
C:\Python314\python.exe
C:\Users\zyq\scoop\apps\msys2\current\ucrt64\bin\python.exe
C:\Users\zyq\AppData\Local\Microsoft\WindowsApps\python.exe

$ python --version
Python 3.14.4                                    # 与 payload pythonExact 一致

$ backend/.venv/Scripts/python.exe --version
Python 3.11.15                                  # ← 跑 pytest 用的 venv 不是 3.14.4

$ cat backend/.venv/pyvenv.cfg
version_info = 3.11.15
home = .../uv/python/cpython-3.11-windows-x86_64-none

$ node --version
v25.9.0                                         # 与 payload nodeVersion 一致

$ docker --version
Docker version 29.4.2, build 055a478

$ docker info --format "{{.ServerVersion}}"
29.4.2                                          # daemon 可达，与 payload dockerAvailable=false 矛盾

$ docker ps --format "{{.Names}}"    (部分)
scd-readyz8-portable-postgres-portable-1
scd-readyz8-postgis-postgres-postgis-1
scn-hydraulic-probe-postgres-1
scd-hydraulic-round2-portable-postgres-portable-1
scd-hydraulic-round2-postgis-postgres-postgis-1
scd-hydraulic-round1-postgis-postgres-postgis-1
deepseek-search-searxng

$ cat .python-version            → 3.11
$ cat frontend/.node-version     → 18.20.8
```

### 1.3 必须在使用前读懂的三条环境事实

1. **两个 Python 解释器并存。** `python`（PATH，`C:\Python314\python.exe`）= **3.14.4**；`backend/.venv/Scripts/python.exe` = **3.11.15**。所有 pytest 证据串里的路径（`…\backend\.venv\Lib\site-packages\…`）指向 **3.11.15 venv**。因此 payload 的 `pythonExact: "Python 3.14.4"` 与 pytest 实际运行环境**不是同一个解释器**，两者都真实、不可混用。
2. **Node 版本与冻结目标不符。** 实测 `v25.9.0`，仓库冻结 `.node-version` = `18.20.8`。这正是 stages 中 `frontend G5 remediation round 2` 记录 `target_environment_mismatches: ["node_version=25.9.0; expected=18.20.8"]` 的同一条事实。
3. **`dockerAvailable: false` 与两处证据相互矛盾，本台账不替任何一方裁决：**
   - 与 **payload 自身**矛盾：final gates 中「全新 Compose build/up」「Compose 业务 smoke」「Compose 清理」三项 `exitCode=0`，这三项没有 Docker daemon 无法执行。
   - 与 **我的实测**矛盾：`docker info` 返回 ServerVersion `29.4.2`，`docker ps` 列出 7 个运行中容器。
   - 与 **历史文档**一致：`reviews/g6-artifact-completion.md:6` 记录 2026-09-25 时 daemon 不可用（`docker info --format {{.ServerVersion}}` 无输出）。
   - **结论登记为：`dockerAvailable` 字段反映的是一个较早时间点的快照，不能用于描述 final gates 运行时刻的环境。**
4. **工作树 dirty（157 条）。** 所有证据对应当前工作树，不是一个干净 checkout；`gitHead` 仅表示 HEAD 提交号。

---

## 2. final gates 结果（payload，16 项）

| # | 门禁 | exitCode | 分级 | 关键输出（原样保留） |
|---|---|---|---|---|
| 1 | 后端 pytest | 0 | `VERIFIED / PASS` | `797 passed, 8 skipped, 3 warnings in 61.42s (0:01:01)` |
| 2 | Black | 0 | `VERIFIED / PASS` | `113 files would be left unchanged.` |
| 3 | isort | 0 | `VERIFIED / PASS` | `Skipped 3 files` |
| 4 | Flake8 | 0 | `VERIFIED / PASS` | （无输出） |
| 5 | 后端导入 | 0 | `VERIFIED / PASS` | `backend import ok` + pydantic `UserWarning: Field "model_version" has conflict with protected namespace "model_"` |
| 6 | 前端 ESLint | 0 | `VERIFIED / PASS` | `> eslint . --report-unused-disable-directives --max-warnings 0`（无告警输出） |
| 7 | 前端 Prettier | 0 | `VERIFIED / PASS` | `All matched files use Prettier code style!` |
| 8 | 前端 Vitest | 0 | `VERIFIED / PASS` | 证据串仅含 `findDOMNode` deprecation 与 React Router future flag 警告；**汇总行不在摘录内**，计数未在证据中留存 |
| 9 | 前端 build | 0 | `VERIFIED / PASS` | `✓ built in 11.99s`；产物 `echarts-BBjs3wlc.js 1,030.30 kB`；伴随 `[vite:css] end value has mixed support` 与 chunk >1000 kB 告警 |
| 10 | Compose config | 0 | `VERIFIED / PASS` | （证据串为空） |
| 11 | Git diff check | 0 | `VERIFIED / PASS` | 仅 `LF will be replaced by CRLF` 提示（`frontend/src/**` 多文件），无内容冲突 |
| 12 | 全新 Compose build/up | 0 | `VERIFIED / PASS` | `scn-hydraulic-accept-{redis,influxdb,notification-sink,migrate,backend,simulation-worker,network-job-worker,nowcast-worker,outbox-relay,notification-worker}-1` 依次 Started/Healthy；`migrate-1 Exited`（一次性迁移容器）；`nginx-1 Started` |
| 13 | Compose 业务 smoke | 0 | `VERIFIED / PASS`（**但响应体带非 PASS 码，见 2.1**） | `HYDRAULIC_STACK_STATUS=PASS` / `HYDRAULIC_STACK_EXIT=0` / `HYDRAULIC_STACK_ARTIFACT_SHA256=1dfbaed220029552a57997c1a34b717c9dbb98bdb9ab84c6540095456d2bd2e1` |
| 14 | Compose 清理 | 0 | `VERIFIED / PASS` | 全部 `scn-hydraulic-accept-*` 容器 Stopped/Removed；`Network scn-hydraulic-accept_scn-network Removed` |
| 15 | 5000节点/10000管段 24h 基准 | 0 | `VERIFIED / PASS` | `HYDRAULIC_BENCHMARK_STATUS=PASS` / `EXIT=0` / `ARTIFACT_SHA256=255fb83ac7afea83b7070b8876f2e4dedc3b58e365feb4846b8bb8ea39f35f36` |
| 16 | G5 pinned Node 18.20.8 / 4CPU / 8GiB 浏览器 gate | 0 | `VERIFIED / PASS` | `FRONTEND_PERFORMANCE_STATUS=PASS` / `SMOKE_STATUS=PASS` / `EXIT=0` / `ARTIFACT_SHA256=c1f13e13f4c3285fe3271671dd9279169dcb301276e6a8799afa687c3cc77c1d` |

**final gates 汇总：16 项全部 `exitCode=0`。** 这不改变 stages 中记录的任何失败，也不构成发布结论。

### 2.1 gate 13 响应体：PASS 标记与内部状态码不一致（原样登记，不裁决）

`backend-readyz` 探针 `http://127.0.0.1:8000/readyz` → HTTP 200、`status: PASS`，响应体摘要：

```json
{"ready":true,"code":"RUNTIME_NOT_READY",
 "schema":{"ready":true,"code":"SPATIAL_BACKEND_UNAVAILABLE",
   "expected_alembic_revision":"0008_hydraulic_retention_seed","expected_spatial_backend":"portable",
   "alembic_revision":"0008_hydraulic_retention_seed",
   "schema_metadata_revision":"0008_hydraulic_retention_seed","spatial_backend":"portable"},
 "checks":{"required_tables":true,"missing_tables":[],"extensions":true,"missing_extensions":[],
   "hypertables":true,"worker_heartbeats":{"network-job":true,"simula…（摘录截断）}}
```

同一响应体里 `ready:true` 与 `code:"RUNTIME_NOT_READY"`、`schema.code:"SPATIAL_BACKEND_UNAVAILABLE"` 并存。本台账只登记该事实；**不**判定哪个码为准，也**不**把它改写成 `/readyz` 不健康或健康。摘录在 `worker_heartbeats` 处被截断，5 个心跳键未完整留存。

同批另两个探针：`backend-health`（`/health`）200 `{"status":"healthy"}`；`nginx-health`（`http://127.0.0.1:8080/health`）200 `{"status":"healthy"}`。静态项 `smoke-portable.fixtures` → `all 2 frozen fixtures exist`，两 fixture SHA-256：

```text
backend/tests/fixtures/hydraulic/compose/restore-manifest-v1.json  c4ecfcaf9c61ea171be2cdb671158a50a6680a2c1dd7c8e79f49b5261c307626
docker/hydraulic-gates/compose-v1.lock.json                        0e622b9adeb006b97fabc322de4da3102a157b9c1faa886229cd887c202d3216
```

两值与 stages 的 `G6 artifact completion` 记录完全一致（该阶段记 `c4ecfcaf…7626` / `0e622b9a…d3216`）。

### 2.2 gate 15 基准明细（原样保留）

| 项 | 值 |
|---|---|
| `fixture_id` | `5k_10k_24h_v1` |
| `solver` | `py_mw` / `py_mw/1.0.0`，`engine_sha256=8effaeb1f26d404f254f19471da14bbe9c8e3565d54ea89e483967ab96fac4ff` |
| `routing_mode` | `motion_wave` |
| `frame_count` | 289（3 次 measured run 全部一致） |
| `frame_bytes` | 115756 |
| wall time | run1 `59.030137000008835s`、run2 `56.95742559999053s`、run3 `59.5926260999986s`（阈值 `max_worker_wall_time_seconds: 180.0`） |
| cpu time | 56.75 / 59.078125 / 59.078125（run3 记 `59.078125`） |
| `mass_balance_relative_residual` | `0.0`（三次均为 `quality_status: "verified"`、`worst_frame_mass_residual_relative: 0.0`） |
| 阈值 | `max_mass_balance_relative_residual: 0.0001`、`measured_runs: 3`、`warmup_runs: 1` |
| `persistence` | 三次均 `all_frames_committed/database_committed/published/quality_report_committed = true`，`sink_finalized: true` |
| `failures` | 未在摘录中出现 `failures` 字段；`status: "PASS"` |
| `utc_timestamp` | `2026-09-27T19:01:09.770925Z` |

注意：三次 run 的 `frame_sha256` **互不相同**（`…9e7e753b…`、`4143db54…`、`7228b025…`），即结果非逐位可复现；证据串未说明该差异是否被契约允许。

### 2.3 gate 16 G5 浏览器明细（原样保留，仅摘录中可见部分）

| 项 | 值 |
|---|---|
| `presented_fps` | `49.3516966520131`（阈值 25 fps floor，来自 frontend G5 remediation 阶段记录） |
| `request_animation_frame_p95_ms` | `34.5`（阈值 50 ms） |
| `request_animation_frame_max_ms` | `46.5` |
| `render_p95_ms` | `5` |
| `long_task_count_above_200ms` / `long_task_max_ms` | `0` / `0` |
| `actual_execution_seconds` | `60.01820000000298`（`requested_duration_seconds: 60`） |
| `raf_sample_count` / `render_sample_count` | 2962 / 2962 |
| `cache_peak_bytes` | `149632617`，`cache_frame_count: 289` |
| `average_visible_features` / `visible_fraction` | `1797.66` / `0.11235379388926402` |
| `feature_operations` / `feature_operations_per_second` | 5324671 / 88717.6 |
| `browser` | `/usr/bin/chromium`，`Chrome/136.0.7103.113`，Headless，`gpu: disabled_software_rasterization`，viewport 1920×1080，`executable_sha256=314ea0a8…` |
| `build` | production，`index_sha256=e83ae4d9…`，3 个 asset 带 SHA-256 |
| `production_modules` | `@/features/simulation/map/layerController`、`frameCache`、`viewport` |
| `failures` | `[]`（空） |
| `result_summary` | `Frozen G5 frontend browser gate passed.` |

**摘录边界（必须知道）：** 证据串**从 JSON 中段开始**（首字符即 `nd":null,…`），`environment` 块不在摘录内。因此**本台账无法从证据串确认本次 PASS 运行确实处于 Node 18.20.8 / 4 CPU / 8 GiB 冻结环境**——该点仅来自门禁 label，未被摘录中的 `environment` 字段佐证。stages 中 `frontend G5 remediation round 2` 的 `target_environment_mismatch` 记录描述的是**另一次**运行（`target_environment_match:false`，artifact SHA `6e158316…`），两者不可互相代替。

---

## 3. stages 结果（payload，21 项）

### 3.1 exitCode=0 且已验证

| label | exitCode | 分级 | 关键输出 |
|---|---|---|---|
| schema pytest | 0 | `VERIFIED / PASS` | `29 passed, 784 warnings in 1.68s` |
| network pytest | 0 | `VERIFIED / PASS` | `96 passed, 2341 warnings in 0.28s` |
| frontend-feature vitest | 0 | `VERIFIED / PASS` | `Test Files 16 passed (16)` / `Tests 84 passed (84)` / `Duration 5.40s` |
| forecast pytest | 0 | `VERIFIED / PASS` | `38 passed, 1643 warnings in 0.59s` |
| security pytest | 0 | `VERIFIED / PASS` | `49 passed, 1831 warnings in 1.31s` |
| backend-integration pytest | 0 | `VERIFIED / PASS` | `77 passed, 2924 warnings in 2.25s` |
| frontend-acceptance-tests vitest | 0 | `VERIFIED / PASS` | exit 0；**摘录只有 `findDOMNode` deprecation 与 React Router future flag 警告，测试计数行不在摘录内** |
| 0003 schema gap | 0 | `VERIFIED / PASS`（复合） | 见 3.3 |
| solver runtime | 0 | `VERIFIED / PASS`（复合，**内含失败**） | 见 3.4 |
| dependency handoff re-approval | 0 | `VERIFIED / PASS`（复合） | 见 3.5 |
| contract surface schema 0004+ | 0 | `VERIFIED / PASS`（复合，**内含 7 项失败**） | 见 3.6 |
| 跨 owner contract gates round 1 | 0 | `VERIFIED / PASS`（复合） | 见 3.7 |
| notification transport | 0 | `VERIFIED / PASS`（证据不足） | 见 3.8 |

### 3.2 exitCode != 0（失败 / 环境阻断）—— 逐项保留，不折算

| label | exitCode | 分级 | 事实 |
|---|---|---|---|
| simulation pytest | **4** | `VERIFIED / FAIL` — **调用方式错误，未执行任何测试** | `ERROR: file or directory not found: backend/tests/hydraulic/test_tasks_sandbox.py`（exit 4 = pytest usage error）。证据串中只有 `64 warnings in 0.00s`（fastapi `asyncio.iscoroutinefunction` deprecation）。**没有 collected、没有 passed/failed 计数**——不得读作"simulation 测试失败"或"simulation 测试通过"。 |
| frontend-integration vitest | **1** | `VERIFIED / FAIL` — **调用方式错误，未执行任何测试** | `No test files found, exiting with code 1`。根因（该证据串自带说明）：cwd 已是 `.../smart-city-drainage/frontend`，filters 仍写成 `frontend/src/...`，Vitest 因此查找 `frontend/frontend/src/...`。 |
| backend-acceptance-tests pytest | **1** | `VERIFIED / FAIL` — **真实测试失败** | `7 failed, 57 passed, 1961 warnings in 5.55s`。失败清单：<br>`test_acceptance_matrix.py::test_acceptance_contract_runtime_is_python_311`<br>`test_acceptance_matrix.py::test_acceptance_matrix_requires_every_case_to_have_pass_evidence`<br>`test_acceptance_matrix.py::test_ac5_observed_comparison_requires_real_verified_pairs`<br>`test_acceptance_matrix.py::test_ac6_comparison_rejects_metric_unit_mismatch`<br>`test_negative_security.py::test_ac2_malicious_import_case_is_rejected_without_state_mutation[duplicate-stable-key]`<br>`test_negative_security.py::test_ac2_malicious_import_case_is_rejected_without_state_mutation[duplicate-fanout-resource-sample]`<br>`test_negative_security.py::test_ac2_raw_multipart_filename_is_rejected_without_parser_rewrite[windows-drive]`<br>**证据串未给出逐条失败原因**，本台账不推断根因。 |
| simulation worker entrypoint | **4** | `VERIFIED / FAIL` — **调用方式错误，未执行任何测试** | `ERROR: file or directory not found: smart-city-drainage/backend/tests/hydraulic/test_simulation_worker.py`。同阶段文字记录真实的入口测试文件为 `tests/hydraulic/test_simulation_worker_entry.py`（`62 passed, 1 skipped`），并记录 `.venv/…/python.exe -m app.workers.simulation_worker --preflight-only` → `EXITCODE=3`（`SIMULATION_SOLVER_COMMAND is empty`）。 |
| frontend G5 remediation round 2 | **2** | `ENVIRONMENT-BLOCKED` | `FRONTEND_PERFORMANCE_STATUS=BLOCKED` / `FRONTEND_PERFORMANCE_SMOKE_STATUS=BLOCKED` / `FRONTEND_PERFORMANCE_EXIT=2` / `ARTIFACT_SHA256=6e15831696d36edcf9766a355018c863bcf7733623bf9aa37a1f61fb7f6f3454`；`failures:["target_environment_mismatch"]`；`result_summary:"G5 was not run because the host does not match the frozen target environment."`；`target_environment_mismatches:["node_version=25.9.0; expected=18.20.8","available_cores=16; expected=4","ram_bytes=59252035584; expected=8589934592"]`；`gpu:"disabled_software_rasterization"`。<br>同阶段文字记录 4 次 4-core cpuset 内运行的 rAF p95 为 34.30/34.30/34.70/35.00 ms、fps 48.47/49.29/48.22/47.41，但这些数字**未被 G5 checker 采信为 PASS**（该阶段自身以 BLOCKED 收尾）。 |
| contract surface runtime | **1** | `VERIFIED / FAIL` — **真实运行失败（`/readyz` 503）** | 单元侧全绿（compose config exit 0；19 + 59 + 25 tests OK；`pytest tests/` 789 passed / 8 failed 记为 pre-existing）。真实容器侧：5 个 worker 全部 healthy、5 个 `hydraulic:worker:heartbeat:*` 键在真实 Redis 中 TTL 26–30s、10 个 `/healthz`+`/readyz` 探针 200。**但 `/readyz` 返回 503**，根因（该阶段自述）：`app/main.py` 的 `_validate_forecast_artifacts` 从 `hydraulic.forecast_rules` 选 `artifact_uri, artifact_sha256, valid_from, valid_until`，而 `validate_file()` 读 `row["artifact_format"]` → `NoSuchColumnError`。该阶段称补上 `'json'::varchar AS artifact_format` 后容器内实测 `READY: True`、`schema ready: True 0008_hydraulic_retention_seed`。 |
| post-schema dependency handoff | **1** | `VERIFIED / FAIL` — **真实门禁失败** | `DEPENDENCY_HANDOFF=FAIL: result SHA-256 mismatch: backend/app/core/config.py`，`EXIT=1`。三个独立阻塞（B1/B2/B3）：<br>B1 `.env.example:24` 仍 `HYDRAULIC_SCHEMA_REVISION=0003_hydraulic_schema_gap`，而 `config.py:268-270` 已是 `Literal["0008_hydraulic_retention_seed"]` → `ValidationError`（literal_error）。<br>B2 `config.py` 现为 `d6f7142af0d4d4b5c90014137b9d43f9c2d63a1ceeec0a7c3fad3b17790b3cf7`，已批准值为 `6b21a3f674703dde5d66c8ab7e5e9704de92339183d607bc02276011a5527608`。<br>B3 `check_dependency_handoff.py:31` 仍钉 `approved-migration-0003.json`，而测试从 `config.py` 导入的 `HEAD_REVISION` 已是 `0008_hydraulic_retention_seed`，相等断言为 `False`（`check_migration_gate.py:30` 已指向 `approved-migration-0008.json`）。<br>复测：`3 failed`（三个具名 node id）、`3 failed, 119 passed, 2 skipped`。 |

### 3.3 stage `0003 schema gap`（exit 0）明细

| 命令 | 结果 |
|---|---|
| `python -B scripts/check_migration_gate.py …` | `MIGRATION_GATE=PASS`，`MIGRATION_PHASE=POST_MIGRATION_APPROVED` |
| `python -B scripts/check_dependency_handoff.py …` | `DEPENDENCY_HANDOFF=PASS_WITH_DEFERRED`（deferred 仍为 frontend-target-runtime-evidence） |
| `pytest tests/test_hydraulic_schema.py` | `34 passed` |
| `pytest tests/test_contract_gate_scripts.py` | `16 passed`（改哈希后曾 `2 failed`，即 checker 正确拦截） |
| `pytest tests/integration/test_database_schema.py` | `10 passed, 2 skipped` → 补 `DATABASE_TEST_DSN_*` 后 `2 passed` |
| `pytest tests`（全套） | `622 passed, 3 skipped`（跳过项：Windows SIGTERM、fallback-only profile） |
| `python -c "import app.main"` | 成功；`/readyz` 路由在；三个 ORM 表已注册 |
| `docker compose config` | exit 0；`HYDRAULIC_SCHEMA_REVISION: 0003_hydraulic_schema_gap` 落在 migrate / backend / simulation-worker 三服务 |

**该阶段自述的未解决问题（原样保留）：** 运行结束时 `/readyz` **仍不健康**——`_REQUIRED_HYDRAULIC_TABLES`（`main.py:61-82`）共 18 张，0001+0002 建了 7 张，本阶段补 3 张后 live 库实测 **10/18，仍缺 8 张**：`ab_comparisons`、`forecast_alert_windows`、`network_imports`、`network_validation_issues`、`observation_ingest_events`、`observation_samples`、`observation_trust_policies`、`validation_runs`；`checks["required_tables"]` 仍为 `False`。另记 `backend/build/lib/app/core/config.py:55` 为陈旧构建副本（仍写 `0002_hydraulic_schema`）。未执行：`docker compose up` 全栈、真实 HTTP 打 `/readyz`、`verify_hydraulic_stack.py` G6。

### 3.4 stage `solver runtime`（exit 0）明细——**内含一项真实失败**

| 命令 | 结果 |
|---|---|
| `pytest backend/tests/hydraulic/test_python_fallback_solvers.py -q` | `40 passed` |
| `pytest backend/tests/hydraulic/test_solver_cli.py -q` | `27 passed` |
| `pytest backend/tests/hydraulic/test_solver_worker_start.py -q` | `10 passed` |
| **`pytest backend/tests -q`（全量）** | **`2 failed, 695 passed, 5 skipped` ← 失败，未被 exit 0 掩盖** |

其他记录：flake8 在 8 个新文件上 exit 0；mypy 在 4 个 solver 模块上 clean（唯一错误为既有的 `config.py:275`）。Live CLI：4 节点/3 管段、6 帧设计暴雨 → `EXIT=0`，6 帧落盘 = manifest 6 帧，质量守恒 `rainfall 66.25 − infiltration 23.375 − outfall 41.906146 = final storage 1.152682`，`residual_relative 0.0` → `verified`，`max_courant 0.3318 ≤ 0.5`，`max_substeps 17 ≤ 20`。`SIMULATION_SOLVER_COMMAND` 置于 `docker-compose.yml:228`。

**该阶段自述的未完成/未运行项（原样保留）：** `py_ponding` 未实现；CLI 输出尚未接入 attempt staging / `ResultRepository` finalize；真实 EPA 二进制、`.out` 解析、Docker/Compose 启动、AC-1–AC-8、G0–G6、5k/10k/24h 性能门禁**全部 NOT RUN**；`py_dw` 仅有一次 `verified` 运行（平缓次临界网），在设计暴雨上正确地以 `UNSUPPORTED_FEATURE` 拒绝而非降级。**该阶段同时自述两项事故：** 用 `git stash push`+`git stash drop` 误毁**先前已存在的未提交 `config.py`**（从 `git fsck --unreachable` 恢复）；`.env.example` 被同样事故截断后重建为 141 行 / 105 唯一键，`result_sha256` 由 `05ac2097…` 变为 `fac68149…`，并升级为需 owner 重新批准。

### 3.5 stage `dependency handoff re-approval`（exit 0）明细

| 项 | 值 |
|---|---|
| 恢复校验 | `cp -f .env.example artifacts/contracts/env-example-recovered.txt` + `cmp` → `BYTE_IDENTICAL`；两文件 4914 字节，`fac68149630b3ef489120f1b2a1da2e29bba44acb8fd51de0d044dd569cea6cc` |
| 字段划分 | 105 = 54 authorized + 51 preserve-only；名称集合与 `Settings.model_fields` 完全一致；类型 canonical；无重复键；secret scan 干净 |
| 顺序 | checker 实际强制的顺序均正确（`settings.fields` == sorted authorized；`auth_ids`/`files` == DEP-AUTH-01..13/12；`paths` 均排序）。`.env.example` key 顺序与 `config.py` 声明顺序**不一致**（模板把 `DEPLOYMENT_ENVIRONMENT` 放最前），checker 比较的是排序后的**集合**，故无门禁依赖此项——记录而不静默修改 |
| 有意分歧（4 项） | `DATABASE_URL`/`DATABASE_URL_SYNC` 用 `YOUR_PASSWORD`（非真实密码）；`JWT_ISSUER`/`JWT_AUDIENCE` 非空 |
| **Finding F-1（非阻断）** | 重建把 `config.py` 的开发默认值写进了 3 个 **preserve-only** 键（`.env.example:97-141`）：`INFLUXDB_TOKEN`、`REDIS_URL`、`JWT_SECRET_KEY`，而 base `ca99e04` 中这些是占位符。这些字面量已逐字存在于 `config.py:185/190/196`，未新增披露；因契约对 preserve-only 字段未记录 `env_example` 值，该阶段**无法逐字段认证**，只能观察 |
| 测试 | 基线 `2 failed, 1 passed`（`DEPENDENCY_HANDOFF=FAIL: result SHA-256 mismatch: .env.example`）→ 修复后三个具名 node id `3 passed`；相关全套 `88 passed, 2 skipped`（skip 为既有 `test_requirements_matrix.py:172: fallback-only profile`） |
| 摘要重建 | 走 checker 自己的纯 helper `build_expected_contract_snapshot`（`:956-982`），序列化为仓库既有 CRLF / `indent=2` / `sort_keys=True`；expected fixture `c0054611e950…` → `750552939e6cf9f3c74d598dc198b309ca5d05ef8c928a09d81cd819bb7064ac`；DEP-AUTH-02 `result_sha256` `05ac2097…` → `fac68149…`；handoff `expected_fixture.sha256` 同步 |
| 变更最小性证明 | 新旧快照结构 diff 恰好 1 个节点不同；把旧摘要按字节回填可复现变更前 fixture（1 行不同）与 handoff（2 行不同）。无旧 hash 被复用或伪造 |
| checker 终态 | `DEPENDENCY_HANDOFF=PASS_WITH_DEFERRED`，`G0_G5_BLOCKED_BY_DEFERRED=true`，`EXIT=0`（工作区父目录 + `smart-city-drainage/` 前缀路径两种调用均为 EXIT=0） |

**该阶段自述的更正/限制：** ask 里的"147 行"与实际不符，文件实为 **141 行**（105 `KEY=value` + 26 注释 + 10 空行），105 键数正确。旧批准字节**不可恢复**（`git ls-files --error-unmatch` 显示 handoff 与 expected fixture 从未被 git 跟踪）——该阶段认证的是**当前字节**，不是原始字节。`frontend-target-runtime-evidence` 仍 DEFERRED，故 G0–G5 仍 blocked。

### 3.6 stage `contract surface schema 0004+`（exit 0）明细——**内含 7 项失败**

命令与结果（两 profile：portable 与 postgis）：

```text
alembic downgrade -1 → current=0003_hydraulic_schema_gap  → exit=0
alembic upgrade head → current=0008_hydraulic_retention_seed (head) → exit=0
python scripts/check_migration_gate.py --review-artifact artifacts/contracts/v3-independent-review.json \
  --base-ref ca99e0485fb12fbecbd565e79d81f745f8d4a748 --versions-dir backend/alembic/versions
  → EXIT=0, MIGRATION_PHASE=POST_MIGRATION_APPROVED, MIGRATION_GATE=PASS,
    8 revisions, head 0008_hydraulic_retention_seed, 绑定 artifacts/contracts/approved-migration-0008.json (sha256 99bf55b3…)
```

Live 状态（两 profile）：`alembic_version = 0008_hydraulic_retention_seed`；`schema_metadata = (portable|postgis, 0008_hydraulic_retention_seed)`；38 张 hydraulic 表；`8 required tables missing []`；hypertables `observation_samples` + `simulation_frames`；retention ledger `(observation_samples, True, observed_at, 90, 1 day, True)`；Timescale job `policy_retention drop_after 90 days`；`ex_hydraulic_observation_trust_policies_validity` 存在；geometry 列 portable 0 / postgis 3。`check_hydraulic_schema_readiness()` 在两 profile 均返回 `ready: true`（`main.py:849` required-tables 与 `main.py:887` hypertable 谓词皆 True，未放宽任何 readyz 条件）。0001/0002/0003 校验为逐字节一致（`cd3516a9…`、`3c2f6539…`、`8ccc00d6…`）。

测试：

```text
pytest tests/test_hydraulic_schema.py tests/test_hydraulic_schema_readyz_tables.py → 49 passed, 1 failed
   （对 live portable DB 失败一次，对 postgis 再失败一次）
pytest tests/integration/test_database_schema.py（两个 live DSN）  → 11 passed, 1 failed
pytest tests（全套）                                                  → 711 passed, 7 failed, 3 skipped
flake8 全部改动文件 → clean
```

**7 项失败逐条归因（该阶段自述，本台账原样保留）：**

1. `test_alembic_is_the_only_schema_writer_and_declares_the_runtime_head` — `docker-compose.yml:52/127/203` 仍钉 `HYDRAULIC_SCHEMA_REVISION: 0003_hydraulic_schema_gap`。
2. `test_env_example_contains_every_contract_variable` — `.env.example:24` 仍 `0003_hydraulic_schema_gap` → `ValidationError`。
3. `test_database_verifier_cli_serializes_bytes_manifest` — `scripts/verify_database.py:32` 的 `DEFAULT_REVISION` 陈旧。
4–5. `test_contract_gate_scripts.py` ×2 — `GateError: result SHA-256 mismatch: backend/app/core/config.py`。
6–7. `test_simulation_worker_live_database` ×2 — 既有 harness 缺陷：`live_database` fixture（第 100–107 行）把**同步** `postgresql://` DSN 塞进异步 `settings.DATABASE_URL`，`run_startup_checks` 在引擎构造阶段即 `InvalidRequestError: The asyncio extension requires an async driver`。

**授权改动范围：** 仅 `backend/app/core/config.py` 两处（`HYDRAULIC_SCHEMA_HEAD` 与 `HYDRAULIC_SCHEMA_REVISION` 由 `0003_hydraulic_schema_gap` 改为 `0008_hydraulic_retention_seed`），sha256 `6b21a3f6…` → `d6f7142a…`。**该阶段未改动** `.env.example`、`docker-compose.yml`、`scripts/verify_database.py`、`artifacts/contracts/dependency-config-handoff.json` 及其 fixture，故失败 1–5 在各自 owner 动作前保持红色。未触碰 `docs/hydraulic-twin/implementation/hydraulic-schema.md`（§15.6 归 `delivery-docs`，且禁止声称迁移已实现）；`check_contract_ownership.py` 在仓库中不存在，因此**未运行任何 ownership 检查**。

**该阶段的一处自我更正（原样保留）：** 第一次 postgis compose 调用静默失败（`Bind for 127.0.0.1:55432 failed: port is already allocated`）——当时看到的 exit 0 来自管道而非 compose；此前引用的 postgis 数字来自一个预先存在的 `scd-hydraulic-round1-postgis` 容器，不是它自己启动的。**在 55452 专用容器上重取后才使用。**

### 3.7 stage `跨 owner contract gates round 1`（exit 0）明细

命令与结果：

| 命令 | 结果 |
|---|---|
| `backend/.venv/Scripts/python.exe -m pytest backend/tests -q` | `483 passed, 4 skipped, 21042 warnings in 27.27s`；4 skip 来自未修改的 DSN / fallback profile / Windows symlink 条件 guard（如 `backend/tests/integration/test_database_schema.py:338`、`backend/tests/test_requirements_matrix.py:172`、`backend/tests/security/test_upload_paths.py:125`） |
| `cd frontend && npm test -- src/…（12 个相对路径文件）` | `Test Files 12 passed (12)` / `Tests 56 passed (56)` / `Duration 5.85s` |
| `python -B scripts/check_migration_gate.py --review-artifact … --base-ref ca99e048…` | `MIGRATION_PHASE=POST_MIGRATION_APPROVED` / `MIGRATION_GATE=PASS` / `versions_changed=["backend/alembic/versions/0002_hydraulic_schema.py"]` |
| `python -B scripts/check_dependency_handoff.py --handoff … --expected …` | `DEPENDENCY_HANDOFF=PASS_WITH_DEFERRED` / `settings_counts={"authorized":54,"preserve_only":51}` / `frontend_performance.state=APPROVED_CONTRACT` / `G0_G5_BLOCKED_BY_DEFERRED=true` |
| `node --check frontend/performance/run-twin-map.mjs` | 通过 |
| `node frontend/performance/run-twin-map.mjs --fixture … --duration-seconds 60 --output <TEMP>/…` | `FRONTEND_PERFORMANCE_SMOKE_STATUS=PASS` / `EXIT=0` / `sample_count=2880` / `cache_peak_bytes=18496000` / `failures=[]` |

**该阶段明确保留的边界（原样保留）：** 性能 smoke 结果**严格标记为 `NON_ACCEPTANCE_SMOKE`**，未被提升为浏览器或 PRD acceptance 证据；npm `test:perf:twin` 与目标运行时证据仍为 `DEFERRED`，**没有冒充 G0/G5 runtime PASS**（`scripts/check_dependency_handoff.py:251`、`:1154`）。对 `frontendIntegrationGate` 的 `No test files found`，该阶段定位为**路径重复**（cwd 已是 `frontend/`，filters 仍写 `frontend/src/…` → Vitest 找 `frontend/frontend/src/…`），并注明外部 gate 调度定义不在允许修改范围内，故以相对 `src/…` filters 重跑。

工件 hash（该阶段 `sha256sum` 输出，8 个文件）：

```text
90106f147bc7709d929ba678ed96c45d4d4745533654f61cbb84f822cc64bf9f  artifacts/contracts/v3-independent-review.json
fe0b013c88800c9bef3bde533e039a19a350a0e6de42d59d7d77bbde1b7a9250  artifacts/contracts/r0-gate-execution.json
db2254857dc754a5ba38958b52144c841e3342b0f59c982c1b9c0330537c68b3  artifacts/contracts/approved-migration-0002.json
8981491fab871e7db0faa12662f627a706dace35cea7a59215c863e07eae4166  artifacts/contracts/dependency-config-handoff.json
35aaf29a3a6d7bf6ced2136cd362110ceeb2f8b7e0f90bd502636cce6864a598  backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json
7e7f311d7a05491aa9e18127254078ea5fcb5a129236f90569ca8bc234338947  frontend/performance/fixtures/twin-map-5k-10k-v1.json
0f2602a72ff3c607d0f207b3cee3cea934b9ab6d0127430b32e1bf64ec52a1f1  frontend/performance/run-twin-map.mjs
4ce0f69e1d959bc5fea404943fc9546ac866ae3997a7a72937d2a72eea9deb21  .env.example
```

该阶段记：不可变 R0 设计工件仍为原 hash `90106f…`；`r0-gate-execution.json` 未重写。

### 3.8 stage `notification transport`（exit 0）— **证据不足，不足以判为通过**

该阶段 `exitCode=0`，但证据串全文仅为：

```text
Everything in scope is built and verified. One item is genuinely blocked by a decision outside my reach — let me get it answered.

compose=
pytest=
```

`compose=` 与 `pytest=` 两字段**为空**。没有命令、没有输出、没有计数。**本台账登记为 `exitCode=0` 但证据不足以支撑 `VERIFIED / PASS`**；不推断其实际结果。

### 3.9 无 exitCode 字段的阶段

| label | exitCode | 分级 | 事实 |
|---|---|---|---|
| G6 artifact completion | （缺失） | `NOT RUN`（live G6）+ 工件已交付 | 证据串自述："No live G6 was run — the Docker daemon is unavailable here." 交付 3 个文件：`docker/hydraulic-gates/compose-v1.lock.json`（4579 B，sha256 `0e622b9a…d3216`，含 `evidence_state: "NOT_RUN"`）、`backend/tests/fixtures/hydraulic/compose/restore-manifest-v1.json`（7031 B，sha256 `c4ecfcaf…7626`，`observed_results` 全 `null`）、`scripts/verify_hydraulic_stack.py` 新增 `g6_fixture_contract_failures()`（`:1070`，接线于 `:1409`）。该阶段自述的门禁结果：`--gate g6` 在 portable 与 postgis 两 profile **仍 FAIL/1**；`g6-*.fixtures` step 由 BLOCKED → PASS；剩余静态失败为 4 个缺失 worker service + postgis 专属"未选定显式 PostGIS 镜像"。`pytest backend/tests` → `553 passed, 4 skipped`；`pytest docker/backend/tests/test_deployment_contract.py backend/tests/test_contract_gate_scripts.py` → `2 failed, 24 passed`（两条均为既有失败：断言 `docker-compose.yml` 中硬编码 secret 字面量，而 G6 secret 规则现已禁止）。报告写在 `docs/hydraulic-twin/reviews/g6-artifact-completion.md`，该文件按 `IMPLEMENTATION_CONTRACT.md:2384` 归 `r0-reviewer` 所有，故**不写 PASS/FAIL 裁决**。 |

---

## 4. `implementation/` 与 `reviews/` 模块证据核对

> 核对方法 [verified-2026-09-28]：`ls implementation | sed 's/\.md$//' | sort` 与 `ls reviews | …` 后 `comm` 对比，并逐文件读头部（模块名/日期/结论/状态行）。**未修改任何文件。**

### 4.1 文档覆盖统计

| 项 | 数量 |
|---|---|
| `implementation/*.md` | **34** |
| `reviews/*.md` | **41** |
| 其中按模块的独立审查 | **33** |
| 非模块类审查（轮次/整改/门禁摘要） | **8**：`g6-artifact-completion`、`hydraulic-schema-live-migration-round-1`、`r0-gate-summary`、`v3-final-remediation`、`v3-final-review-round-1`、`v3-schema-semantics-review`、`v3-schema-semantics-review-round-1`、`v3-schema-semantics-review-round-3` |
| **有 implementation、无 review** | **1**：`python-solver-runtime` |
| 有 review、无 implementation | 0（模块维度） |

`reviews/v3-schema-semantics-review.md` 与 `reviews/v3-schema-semantics-review-round-1.md` 内容等同（`reviews/v3-schema-semantics-review-round-3.md` 记载"两文件字节一致"），属同一审查的重复副本，**不重复计入模块数**。

### 4.2 模块结论总览：33 个模块审查中，32 个 `NOT_APPROVED / BLOCKED`，1 个 `APPROVED`

| 模块 | 独立审查结论（原文口径） | 严重度（审查自述） |
|---|---|---|
| backend-acceptance-tests | `NOT_APPROVED / BLOCKED` | — |
| backend-integration | `BLOCKED / approved=false` | — |
| ci-quality | `NOT APPROVED / approved=false` | 8 High、1 Medium |
| comparison-charts | `NOT_APPROVED / approved=false` | — |
| compose-worker | 不批准（blocked） | 6 高、2 中 |
| database-bootstrap | `BLOCKED / approved=false` | 6 高、3 中 |
| **delivery-docs** | **`APPROVED` —— 无阻断项、无高风险项** | 10 medium + 3 low（建议本轮内修完） |
| dependency-contract | 不批准 | 4 high、5 medium |
| fallback-hydraulics | 不批准（`approved=false`） | 5 high、3 medium |
| forecast-alerts | `NOT_APPROVED / approved=false` | — |
| forecast-events | 不批准 | 5 High、1 Medium |
| forecast-ui | `NOT_APPROVED / approved=false` | — |
| frontend-acceptance-tests | `NOT_APPROVED / 不批准` | — |
| frontend-foundation | `NOT_APPROVED / approved=false` | 7 个高严重度 |
| frontend-integration | `NOT_APPROVED / approved=false` | High 9、Medium 5、Low 1 |
| geo-spatial | `BLOCKED`，`approved=false` | 5 高、2 中 |
| hydraulic-api | `NOT_APPROVED / 不批准` | 7 High、3 Medium |
| hydraulic-metrics | `NOT APPROVED / approved=false` | — |
| hydraulic-schema | `BLOCKED / approved=false` | 6 高、2 中 |
| nowcast | 不批准（`approved=false`） | 5 high、3 medium |
| operations | `NOT APPROVED` | 4 high、2 medium |
| rainfall | 不批准 | 4 high、3 medium |
| rainfall-editor | `NOT_APPROVED / approved=false` | — |
| result-calibration | 不批准（`approved=false`） | 4 high、1 low |
| security-hardening | 不批准（`approved=false`） | 2 个高风险 |
| simulation-map | `NOT_APPROVED / approved=false` | 7 个高严重度 |
| solver-contract | 不批准（`NOT APPROVED`） | 6 High、3 Medium |
| surface-flood | `NOT_APPROVED / approved=false` | — |
| swmm-io | `NOT_APPROVED / BLOCKED` | 5 高、4 中 |
| task-panel | `NOT_APPROVED / BLOCKED` | — |
| task-sandbox | 不批准（`approved=false`） | 6 个高风险 |
| timeline-player | 不批准（changes requested） | High 2、Medium 1、Low 1 |
| topology-seed | 不通过（`approved=false`） | 4 high、1 medium |

**唯一 `APPROVED` 的 `delivery-docs` 审查（`reviews/delivery-docs.md:16-26`，第 7 轮，2026-09-28）明确限定了自身范围**：批准的是"**不把未验证项写成已通过**"这一属性，而非产品发布就绪；审查者称 readiness 阻断解除、演示数据为空、G6 portable PASS / postgis FAIL、干净 env 配方 exit 0、12/13/14/15 服务集合等均由其**亲手重跑**并与文档逐字对上，`NOT RUN` 清单也真实覆盖未执行项；同时列出 6 类文档缺陷（4 处 `path:line` 落在错误行、1 处容器计数与原始观测矛盾、1 处 README 对同一症状给出两个相反根因、1 处 Compose 读取 `.env` 的机制被写错、1 处 G6 PASS 缺前提），并称这些"不改变'演示跑不通'的结论"。

### 4.3 设计冻结（R0）链的审查结论序列（原样登记）

| 审查文件 | 结论 |
|---|---|
| `reviews/v3-schema-semantics-review-round-3.md` | `NOT_APPROVED / R0_FAIL`（新发现 1 BLOCKER、1 HIGH、1 MEDIUM、1 LOW；C-01 违反"错误码/幂等单一矩阵"批准条件） |
| `reviews/v3-final-remediation.md` | `R0_ROUND_3_FAILED / NOT_APPROVED / ALL_IMPLEMENTATION_WORK_BLOCKED`（整改记录，非 reviewer artifact） |
| `reviews/v3-final-review-round-1.md`（= `v3-schema-semantics-review.md`，字节一致） | **`PASS / R0_DESIGN_PASS`**，65/65 只读静态断言通过，无 BLOCKER/HIGH |
| `reviews/r0-gate-summary.md` | 记 `verdict: R0_DESIGN_PASS`；`MIGRATION_GATE=PASS`；`DEPENDENCY_HANDOFF=PASS_WITH_DEFERRED`，`deferred: ["frontend-performance-entrypoint","frontend-target-runtime-evidence"]`，`g0_g5_blocked_by_deferred: true`；`settings_counts={"authorized":54,"preserve_only":47}`。**该文件明确未做：** 未启动数据库/schema、未运行 Alembic upgrade/downgrade、未创建/修改/重命名 migration、未运行 G0–G6、AC-1–AC-8、Vitest、npm ci、性能 harness 或 Docker |

### 4.4 implementation 文档自述状态中与本台账直接冲突的两处

1. **`reviews/dependency-contract.md` 与 `implementation/dependency-contract.md` 均记 round-3 R0 为 `NOT_APPROVED / R0_FAIL`**，而 `reviews/v3-final-review-round-1.md` 记 `R0_DESIGN_PASS`。时间上后者（2026-09-24）不晚于前者，但**本台账不判定哪一个是当前有效的 R0 状态**——这需要 owner 裁决，不属于证据整理范围。
2. **`implementation/compose-worker.md:5` 与 `implementation/database-bootstrap.md:3` 自述"实现完成/静态通过"**，而其对应审查（`reviews/compose-worker.md`、`reviews/database-bootstrap.md`）结论为不批准/BLOCKED。同一模块"实现自述"与"独立审查"方向相反，**两者都保留**。

### 4.5 审查日期与 final gates 的时间关系（必须注意）

全部 33 个模块审查的日期为 **2026-09-24 ～ 2026-09-25**（`delivery-docs` 审查为 2026-09-28，`python-solver-runtime` 实施记录为 2026-09-27 18:20）。而 final gates 证据串中时间戳最晚者为：

- `Compose 业务 smoke`：`utc_timestamp 2026-09-27T18:59:50.441315Z`
- `5000节点/10000管段 24h 基准`：`utc_timestamp 2026-09-27T19:01:09.770925Z`

**因此：32 个 `NOT_APPROVED / BLOCKED` 的模块审查结论是在 final gates 运行之前做出的，其针对的代码状态与 final gates 证明的状态不是同一状态。** 这些审查结论**没有**被 final gates 重新评估，也**不因** final gates 全绿而自动失效。本台账不把其中任何一方折算为最终裁决。

### 4.6 `python-solver-runtime` 无对应独立审查

`implementation/python-solver-runtime.md` 存在（2026-09-27 18:20，11159 字节），`reviews/` 下无同名文件。其相邻的 `implementation/solver-contract.md` 有对应审查（`reviews/solver-contract.md`，不批准，6 High / 3 Medium）。**本模块当前没有独立审查结论**——既非通过也非失败，属"未审查"。

---

## 5. 未运行 / 环境不可用清单（原样汇总，不折算）

| 项 | 状态 | 依据 |
|---|---|---|
| `py_ponding` 求解器 | `NOT RUN`（未实现） | stage `solver runtime` 自述 |
| 真实 EPA SWMM 二进制、`.out` 解析 | `NOT RUN` | stage `solver runtime` 自述；`registry.py` 仅在真实探测成功时注册 EPA |
| AC-1 – AC-8（solver runtime 阶段） | `NOT RUN` | stage `solver runtime` 自述 |
| G0 – G6（solver runtime 阶段） | `NOT RUN` | stage `solver runtime` 自述 |
| 5k/10k/24h 性能门禁（solver runtime 阶段） | `NOT RUN` | stage `solver runtime` 自述（后续由 final gate 15 覆盖，但那是另一次运行） |
| live G6 | `NOT RUN` | stage `G6 artifact completion`："No live G6 was run — the Docker daemon is unavailable here." |
| npm `test:perf:twin` 与目标运行时证据 | `DEFERRED` | `scripts/check_dependency_handoff.py:251`、`:1154`；`G0_G5_BLOCKED_BY_DEFERRED=true` |
| `docker compose up` 全栈 + 真实 HTTP 打 `/readyz` + `verify_hydraulic_stack.py` G6（0003 阶段） | `NOT RUN` | stage `0003 schema gap` 自述"本机无宿主 5432 监听、探针栈未在运行" |
| 0003 阶段的 8 张缺失表 | `NOT RUN`（未擅自扩到 `0004`） | stage `0003 schema gap`：`ab_comparisons`、`forecast_alert_windows`、`network_imports`、`network_validation_issues`、`observation_ingest_events`、`observation_samples`、`observation_trust_policies`、`validation_runs` |
| `check_contract_ownership.py` | `NOT RUN`（文件不存在于仓库） | stage `contract surface schema 0004+` |
| Windows 上的进程级 SIGTERM 优雅退出 | `NOT VERIFIED` | stage `simulation worker entrypoint`：`@pytest.mark.skipif(sys.platform == "win32")`；该阶段自述其 live run 的 `WORKER_EXIT_CODE=1` 是 Windows `Popen.terminate()` 硬杀，**不是** worker 行为 |
| simulation 提交路径的真实生产者 | `NOT FOUND` | stage `simulation worker entrypoint`：`grep -rn "\.submit(" app/` 在仓库类之外无结果；`SessionSimulationTaskRepository` 只暴露 worker 侧操作，真实 DB 断言循环跑在**空表**上 |
| G5 冻结目标环境（Node 18.20.8 / 4CPU / 8GiB）下的运行 | `ENVIRONMENT-BLOCKED` | stage `frontend G5 remediation round 2`：`target_environment_mismatch`，`EXIT=2` |
| `dockerAvailable` 字段 | 与两处证据矛盾 | 见 §1.3 第 3 条 |

---

## 6. 失败项汇总（不得被 final gates 全绿掩盖）

| 类别 | 数量 | 明细位置 |
|---|---|---|
| `exitCode != 0` 的 stage | **7** | §3.2（simulation pytest 4、frontend-integration vitest 1、backend-acceptance-tests pytest 1、simulation worker entrypoint 4、frontend G5 remediation round 2 = 2、contract surface runtime 1、post-schema dependency handoff 1） |
| 其中**调用方式错误、测试未执行** | **3** | simulation pytest、frontend-integration vitest、simulation worker entrypoint |
| 其中**环境阻断** | **1** | frontend G5 remediation round 2 |
| 其中**真实失败/门禁失败** | **3** | backend-acceptance-tests pytest、contract surface runtime、post-schema dependency handoff |
| `exitCode=0` 但内含真实失败的 stage | **3** | solver runtime（`2 failed, 695 passed`）、contract surface schema 0004+（`711 passed, 7 failed`）、G6 artifact completion（`2 failed, 24 passed`） |
| `exitCode=0` 但证据不足的 stage | **1** | notification transport（`compose=`/`pytest=` 均为空） |
| `exitCode=0` 但汇总行不在摘录内的 gate | **2** | 前端 Vitest、frontend-acceptance-tests vitest |
| 无 `exitCode` 字段的 stage | **1** | G6 artifact completion |
| 模块独立审查 `NOT_APPROVED / BLOCKED` | **32 / 33** | §4.2 |
| 有实现但**无独立审查**的模块 | **1** | python-solver-runtime（§4.6） |

---

## 7. 证据完整性声明

- 本文件中标 `[payload]` 的所有结论**均来自本次 ask 传入的证据串**，我在本会话中**没有复跑**这些 pytest / vitest / compose / 性能命令，因此不声称它们"由我验证"。
- 标 `[verified-2026-09-28]` 的内容是我本会话亲自执行的命令与其真实输出（见 §1.2、§4.1）。
- 证据串中**缺失**的命令、输出与汇总行，已在各表"摘录边界"处显式标注；**没有任何一处由我补写、推断或重排为通过**。
- `gitHead = ca99e048…` 只表示 HEAD 提交；工作树有 157 条未提交变更，**证据对应当前工作树而非该 commit 的干净检出**。
