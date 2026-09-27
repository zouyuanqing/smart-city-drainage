# AC-3 独立验收报告 · 第 2 轮

> 验收人：独立验收员（只读；未修改任何被验收代码。唯一新增文件是本报告与 `artifacts/ac3-audit-r2/` 下的只读诊断脚本/产物）
> 审计日期：2026-09-28
> 仓库：`C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage`
> HEAD：`ca99e0485fb12fbecbd565e79d81f745f8d4a748`（**与第 1 轮相同**；工作树 dirty，`git status --porcelain | wc -l` = **160**，第 1 轮为 157。本轮结论对应当前工作树，不是干净检出）
> 审计范围：AC-3「50 年一遇 24h 仿真进度、结果、地图时间轴可回放」
> 上一轮：`docs/hydraulic-twin/audits/AC-3-round-1.md`（10 项 finding：4 阻断 / 6 重大）

---

## 0. 结论

**AC-3 判定：`blocked`（不通过；较第 1 轮的 `partial` 恶化）。**

第 1 轮的唯一性能证据（G5 solver benchmark `PASS` / exit 0 / wall 81.56 s）**在本轮已不复现**。F-3 的整改把三个硬编码常量 `True` 换成了 sink 自述，方向正确，但自述为假的同时**没有**提供真的数据库 sink，于是门禁由「假绿」翻成「真红」：

```
$ backend/.venv/Scripts/python.exe scripts/benchmark_hydraulic.py \
    --fixture backend/tests/fixtures/performance/solver/5k_10k_24h_v1/manifest.json \
    --warmup 1 --repeats 3 --cpus 4 --memory 8g --threads 4 \
    --output artifacts/ac3-audit-r2/solver.json
"status": "FAIL",  "exit_code": 1,  "benchmark_executed": false,
"error": {"message": "executor persistence.database_committed must be true", "type": "BenchmarkError"}
HYDRAULIC_BENCHMARK_STATUS=FAIL
HYDRAULIC_BENCHMARK_EXIT=1
```

即：AC-3 的「每次 worker ≤180 s」判据现在**通过冻结入口取不到任何数字**（`benchmark_executed: false`，报告里没有任何 timing 字段）。

冻结 acceptance 入口本轮实跑：

```
$ backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py \
    --gate acceptance --ac ac3 --evidence-dir artifacts/ac3-audit-r2/acceptance
"required_gates": ["g3", "g5", "e2e", "g6"],
"missing_or_invalid_artifacts": ["g3","g5","e2e","g6",
  "missing artifact: .../g3/result.json", "missing artifact: .../g5/result.json",
  "missing artifact: .../e2e/ac-smoke.json",
  "missing artifact: .../g6/postgis/result.json", "missing artifact: .../g6/portable/result.json"],
"status": "BLOCKED"
HYDRAULIC_STACK_STATUS=BLOCKED
HYDRAULIC_STACK_EXIT=2
```

`PASS` 不成立。核心链路缺陷原封未动：API 结果/帧端点在生产中恒 409，仿真进度恒为 0（见 F-1、F-2 的运行时实测）。

### 相对第 1 轮的净变化

| # | 第 1 轮 finding | 本轮状态 |
|---|---|---|
| F-1 | worker↔API 无 durable 桥，结果/帧恒 409 | **未修复**（运行时复测确认） |
| F-2 | 进度无生产者，恒 0 | **未修复**（运行时复测确认） |
| F-3 | benchmark「已落库」是硬编码 `True` | **半修复 + 引入回归**（常量已去除，门禁由绿翻红，见 N-1） |
| F-4 | 冻结入口 AC→gate 映射弱于需求表 | **已修复**（唯一来源改为冻结 fixture，已实测生效） |
| F-5 | 50 年一遇量级非物理 | **未修复**（数值复测一致） |
| F-6 | 未真正绑定 4 物理核、无峰值 RSS | **半修复**（新增 win32 采集分支，但在本审计主机上恒返回 `None`，见 N-2；4 核仍未强制） |
| F-7 | G5 fixture 不是 50 年 | **未修复** |
| F-8 | 帧 hash 不可逐位复现 | **未修复，且现已不可测量**（benchmark 中止，不再产出 `frame_sha256`） |
| F-9 | 时间轴 e2e 只有 2 帧、非 Playwright | **未修复** |
| F-10 | worker E2E 空 solver + 内存仓库 | **部分推进**（新增真实 session 仓储的 live-DB 契约测试，但本机全 skip，且不覆盖 kill/fencing，见 §3） |
| N-1 | —（新） | G5 solver benchmark **回归**：`PASS/exit 0` → `FAIL/exit 1` |
| N-2 | —（新） | `peak_rss_bytes()` win32 分支恒返回 `None`（ctypes `restype` 缺陷） |
| N-3 | —（新） | `VALIDATION_EVIDENCE.md` §2.2 仍宣称 gate 15 `status: "PASS"`、`persistence ... = true`，与当前代码可复现结果矛盾 |

**修复计数：1 项修复（F-4），1 项半修复（F-3/F-6），6 项未动（F-1/F-2/F-5/F-7/F-8/F-9），1 项部分推进（F-10），新增 3 项。全部缺口均可由代码修复，无一需要外部决策。**

---

## 1. 判据来源

| 来源 | 位置 | 内容 |
|---|---|---|
| SOURCE_PRD | `docs/hydraulic-twin/SOURCE_PRD.md:33` | 「AC-3 50 年一遇 24h 仿真进度、结果、地图时间轴可回放」 |
| SOURCE_PRD | `docs/hydraulic-twin/SOURCE_PRD.md:21`（FR-3） | 队列需支持「提交、**0-100% 进度**、取消、10 分钟超时、最多两次重试、幂等、并发 2、崩溃恢复」；帧「按不超过 5 分钟帧序列化」；非功能 `:31`「5000 节点/10000 管段 24h 仿真在 **4 核**纯 Python 不超过 180s」 |
| PRD_SUPPLEMENT | `docs/hydraulic-twin/PRD_SUPPLEMENT.md:588` | 自动化证据＝4 核 solver benchmark、worker E2E、289-frame manifest、Playwright timeline；判据＝每次 worker `≤180s`；289 帧；diff `≤300s`；进度 0/100；取消/重试/回放可复现；**当前状态「未实现/本轮未运行」** |
| PRD_SUPPLEMENT | `docs/hydraulic-twin/PRD_SUPPLEMENT.md:163` | 「帧 MUST 流式写 TimescaleDB；MUST NOT 先在内存或沙箱积累全部 289 帧」 |
| PRD_SUPPLEMENT | `docs/hydraulic-twin/PRD_SUPPLEMENT.md:409-412` | 目标环境「**4 个物理核**、无 GPU、BLAS/OMP 总线程 4」；须报告「P95、CPU time、**峰值 RSS**、分项时间、frame bytes、质量残差」 |
| acceptance_requirements.json | `backend/tests/fixtures/hydraulic/acceptance/v1/acceptance_requirements.json` → `cases["AC-3"]` | 4 个 required_gates（`g3/g5/e2e/g6`）+ 5 个 required_artifact_suffixes + 8 条 required_observations |
| matrix.json | `backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json` → `cases[id=AC-3]`（`:72-83`） | `status: "NOT RUN"`、`pass_nodeids: []`，reason 自述「Current tests are request metadata smoke plus in-memory frame and repository injection」 |
| IMPLEMENTATION_CONTRACT | `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2549` | 「AC-3 \| NOT RUN（无 solver/worker/frame）」 |
| IMPLEMENTATION_CONTRACT | `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2539`（G5 行）、`:2509` | G5 ＝「第 17.1.1 节四个固定 harness 全部 exit 0」；目标环境「4 physical cores/8 GiB/无 GPU」 |
| IMPLEMENTATION_CONTRACT | `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2570`（§18 第 6 条） | 「py_mw 180s、质量或确定性未在固定 4 核实际通过」是发布阻断条件 |
| VALIDATION_EVIDENCE | `docs/hydraulic-twin/VALIDATION_EVIDENCE.md:140-157` | gate 15 基准明细（wall 59.03/56.96/59.59 s、289 帧、残差 0.0、`persistence` 四项 true、`status: "PASS"`）；`:157` 记三次 `frame_sha256` 互不相同 |
| VALIDATION_EVIDENCE | `docs/hydraulic-twin/VALIDATION_EVIDENCE.md:449` | 「simulation 提交路径的真实生产者 \| NOT FOUND」 |

### AC-3 的 8 条 required_observations 逐条核对

`backend/tests/fixtures/hydraulic/acceptance/v1/acceptance_requirements.json` → `cases["AC-3"]`：

| # | required_observation | 判定 | 依据 |
|---|---|---|---|
| 1 | 50-year 24-hour design storm runs through the real solver on four physical cores | **not met** | F-5 / F-6 / F-7 + 本轮 R-2 benchmark `FAIL` |
| 2 | worker wall time ≤180 s for every repeat | **unverified（本轮不可测量）** | N-1：`benchmark_executed: false`，报告无 timing；第 1 轮的 81.56 s 不可复现 |
| 3 | 289 persisted frames, monotonic timestamps, non-empty content, checksums | **not met** | F-1（生产恒 409）+ F-3/N-1（sink 仍只写临时文件，代码自述「no frame reached hydraulic.simulation_frames」） |
| 4 | quality and conservation pass without silent solver fallback | **unverified** | 本轮无法产出质量报告（benchmark 中止）；第 1 轮测得残差 0.0，但已不可复现 |
| 5 | worker kill recovers a PostgreSQL lease and increments the fencing token | **not met** | F-10：本机 live-DB 测试 3 skip；无 kill/fencing 用例 |
| 6 | stale attempts cannot publish frames or results | **partial** | 仍只在 `InMemoryTaskRepository` 上验证（`test_simulation_worker_entry.py` 13 处） |
| 7 | timeout, cancel, retry backoff, attempt exhaustion, workspace cleanup | **partial** | 单测级内存仓库，无 24h 规模端到端 |
| 8 | API progress, cancellation and map timeline replay are reproducible | **not met** | 本轮 R-4 运行时实测：进度恒 0、状态恒 `queued`、result/frames/frames/{0,144,288} 全部 409（F-2/F-1）；回放 e2e 仍 2 帧（F-9） |

---

## 2. 本轮我实际执行的命令（及其真实输出）

| # | 命令 | 结果 |
|---|---|---|
| R-1 | `git rev-parse --short=8 HEAD` / `git status --porcelain \| wc -l` | `ca99e048` / **160** |
| R-2 | `backend/.venv/Scripts/python.exe scripts/benchmark_hydraulic.py --fixture backend/tests/fixtures/performance/solver/5k_10k_24h_v1/manifest.json --warmup 1 --repeats 3 --cpus 4 --memory 8g --threads 4 --output artifacts/ac3-audit-r2/solver.json` | `HYDRAULIC_BENCHMARK_STATUS=FAIL` / `EXIT=1` / `status:"FAIL"` / `benchmark_executed:false` / `error.message:"executor persistence.database_committed must be true"` / `SHA256=cd13eea84309bc9e19aefa1f15fa3649724fdaade26ea721cb66dd9f90969c08` |
| R-2a | 同上但 `--warmup 0 --repeats 1`（探明是否为缩减规模所致） | `FAIL` / `EXIT=1` / `error.message:"--warmup must be exactly 1"`（`:1049-1050` 强制 `--warmup==1`、`--repeats==3`，无缩减规模的可能） |
| R-3 | `backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py --gate acceptance --ac ac3 --evidence-dir artifacts/ac3-audit-r2/acceptance` | `HYDRAULIC_STACK_STATUS=BLOCKED` / `EXIT=2`；`required_gates:["g3","g5","e2e","g6"]`（**含 g6**）；`SHA256=bf5f8ab4839a04b0c21e937566f2b5e70bcb50d8d75d70c6c033772974bf96b9` |
| R-4 | `backend/.venv/Scripts/python.exe artifacts/ac3-audit-r2/probe_ac3_progress_frames.py`（本轮新增只读诊断，复用仓库自带 `tests/acceptance/test_acceptance_matrix.py` 的 `_create_network/_create_rainfall/_submit_simulation` 与 `rainfall_50y_24h.json` fixture，对真实 FastAPI app 提交 50y/24h/289 帧仿真并轮询） | `submit: status="queued", progress=0, phase="accepted", frame_count=289`；4 次轮询全部 `status="queued", progress=0, phase="accepted"`；`result/frames/frames?fields/frames/0/frames/144/frames/288` **全部 http 409 `VALIDATION_ERROR`**；`in_process_item: progress=0, published_frames_len=0, result_is_none=true`；`durable_backend_flag=false` |
| R-5 | `cd backend && ./.venv/Scripts/python.exe -m pytest tests/acceptance/ -q` | `65 passed, 1 warning in 4.85s`（第 1 轮同命令为 `61 passed`） |
| R-6 | `cd backend && ./.venv/Scripts/python.exe -m pytest tests/acceptance/test_acceptance_matrix.py -k "ac3 or fr3_in_memory_task_repository" -q` | `3 passed, 15 deselected in 0.54s`（与第 1 轮 R-5 同选择器，可比） |
| R-7 | `cd backend && ./.venv/Scripts/python.exe -m pytest tests/hydraulic/test_tasks_sandbox.py tests/hydraulic/test_simulation_worker_entry.py -q` | `79 passed, 1 skipped, 1 warning in 12.93s`（第 1 轮 R-6：`79 passed, 1 skipped in 16.48s`，用例数未变） |
| R-8 | `cd backend && ./.venv/Scripts/python.exe -m pytest tests/hydraulic/test_python_hydraulics.py -q` | `26 passed, 1 warning in 0.06s`（含 `test_same_input_is_deterministic`） |
| R-9 | `cd backend && ./.venv/Scripts/python.exe -m pytest tests/integration/test_simulation_worker_live_database.py -q` | `3 skipped, 1 warning in 0.03s`（无 `DATABASE_TEST_DSN_*`） |
| R-10 | `cd frontend && npx vitest run src/features/simulation/test/timelinePlayer.test.ts src/features/simulation/test/timelineControl.test.tsx src/features/simulation/test/taskStream.test.tsx src/features/simulation/test/taskController.test.tsx` | `Test Files 4 passed (4)` / `Tests 26 passed (26)` / `Duration 2.63s` |
| R-11 | `backend/.venv/Scripts/python.exe -c "from app.services.hydraulic.rainfall import generate_design_rainfall; ..."` | T=1/2/5/10/20/50/100 → 24h 总量 `160.0/320.0/800.0/1600.0/3200.0/**8000.0**/16000.0` mm；50 年峰值 `996.531` mm/h；各 289 点 |
| R-12 | `grep -c progress backend/app/workers/simulation_worker.py` | `0`（exit 1） |
| R-13 | `grep -rn "update_progress" backend/app --include=*.py` | 仅 `tasks.py:820,1083,1597`（各仓储方法定义）+ `tasks.py:2071`（`SessionSimulationTaskRepository` 方法）+ `tasks.py:2080`（其内部对仓储的唯一调用）。**app 生产代码无任何调用点** |
| R-14 | `grep -rn "published_frames" backend/app --include=*.py` | 仅 `hydraulic_routes.py:3346`（置 `{}`）、`:3485`、`:3489`（读取）。**无任何生产写入** |
| R-15 | `grep -rn "ResultRepository\|from app.services.hydraulic" backend/app/workers/simulation_worker.py` | 仅 `:44 app.services.hydraulic.sandbox`、`:55 app.services.hydraulic.tasks`。**无 results 模块** |
| R-16 | `python -c "json.load(frontend/e2e/fixtures/api-v1/baseline-frames.json)"` | `frame_count 2` |
| R-17 | `grep -rn "playwright" frontend/package.json` / `ls frontend/node_modules/@playwright` / `ls frontend/e2e/` | package.json 无命中；`node_modules/@playwright` 不存在；`frontend/e2e/` 仅 `fixtures` 与 `run-ac-smoke.mjs` |
| R-18 | `backend/.venv/Scripts/python.exe -c "…peak_rss_bytes(); linux_cpu_details(); total_ram_bytes(); hasattr(os,'sched_setaffinity'); import resource…"` | `peak_rss_bytes(): None`；`linux_cpu_details(): (None, None)`；`total_ram_bytes(): None`；`has sched_setaffinity: False`；`resource module: MISSING` |
| R-19 | `backend/.venv/Scripts/python.exe -c "…直接调用 GetProcessMemoryInfo…"` | 默认调用：`psapi success = 0`，`PeakWorkingSetSize = 0`，结构体 `size = 72`。加 `GetCurrentProcess.restype = ctypes.c_void_p` 后：`success = 1`，`PeakWorkingSetSize = 13336576` |
| R-20 | `grep -n "DATABASE_TEST_DSN" .github/workflows/ci.yml docker-compose.yml` | 两处均无命中（仅 `backend/tests/integration/*.py` 中读取） |
| R-21 | `grep -n "def gate_g5" -A 45 scripts/verify_hydraulic_stack.py` | G5 门禁以**完全相同**参数（`--warmup 1 --repeats 3 --cpus 4 --memory 8g --threads 4`，`timeout_seconds=1200`）调用 `scripts/benchmark_hydraulic.py`（`:808-834`）→ R-2 的 FAIL 即 G5 的 FAIL |
| R-22 | `grep -c "playwright\|@playwright/test" frontend/package.json` + `cat frontend/e2e/run-ac-smoke.mjs \| head -20` | 0；文件头自述「Dependency-light AC end-to-end smoke … launches a pinned local Chrome/Chromium through the **DevTools Protocol**」，非 Playwright |

产物：`artifacts/ac3-audit-r2/solver.json`（SHA256 `cd13eea8…`，`status: FAIL`）、`artifacts/ac3-audit-r2/acceptance/result.json`（SHA256 `bf5f8ab4…`，`status: BLOCKED`）、诊断脚本 `artifacts/ac3-audit-r2/probe_ac3_progress_frames.py`。

---

## 3. 缺口（findings）

### N-1 ——【阻断 · 可由代码修复 · 本轮新引入的回归】G5 solver benchmark 由 `PASS` 翻为 `FAIL`，AC-3 性能判据现已不可测量

- `backend/app/services/hydraulic/benchmark.py:273-306` —— `_sink_persistence_evidence` 改为向 sink 索取证据，缺证据时返回 `database_committed: False`（fail-closed 方向正确）：
  ```python
  evidence_fn = getattr(sink, "persistence_evidence", None)
  if evidence_fn is None:
      return {"database_committed": False, ..., "detail": "sink exposes no persistence_evidence(); nothing is claimed"}
  ```
- `scripts/benchmark_hydraulic.py:481-497` —— 唯一的 sink 诚实自述「我只写了本地文件」：
  ```python
  return {"database_committed": False, "all_frames_committed": ...,
          "quality_report_committed": False, "published": False,
          "persistence_target": "file",
          "detail": "frames were streamed to a local file and fsynced; no frame reached hydraulic.simulation_frames"}
  ```
- `scripts/benchmark_hydraulic.py:614-624` —— 但 harness 仍要求四个标志全部为真：
  ```python
  for name in ("database_committed", "all_frames_committed",
               "quality_report_committed", "published"):
      require(persistence.get(name) is True, f"executor persistence.{name} must be true")
  ```
- R-2 实跑：`status:"FAIL"`、`exit_code:1`、`benchmark_executed:false`、`error.message:"executor persistence.database_committed must be true"`。**报告里没有任何 timing 字段**（无 `measurements`、无 `worker_wall_time_max_seconds`、无 `frame_sha256`）。
- 第 1 轮同一命令为 `HYDRAULIC_BENCHMARK_STATUS=PASS` / `EXIT=0` / `worker_wall_time_max_seconds=81.5556`。R-2a 确认无法用缩减规模绕过（`:1049-1050` 强制 `--warmup==1`、`--repeats==3`）。
- R-21 确认 G5 门禁以相同参数调用同一脚本，因此 **G5 门禁现在也是 FAIL**，AC-3 的 `g5` 依赖无法满足。
- 帧落库问题本身**依旧存在**：`scripts/benchmark_hydraulic.py:1061-1067` 的 sink 路径仍落在 `tempfile.TemporaryDirectory(prefix="hydraulic-benchmark-")` 下，上下文退出即删除。这与 `PRD_SUPPLEMENT.md:163`「帧 MUST 流式写 TimescaleDB」直接冲突。

**后果**：AC-3 判据「每次 worker ≤180 s」「289 帧」「质量与守恒」「回放可复现」在冻结入口上**同时失去可测量性**。这不是「证据不足」，是「证据通道被自己关掉」。

**可修复**：给 G5 solver harness 提供一个真正写 `simulation_frames` 的 sink（走 `ResultRepository.write_frame`，`backend/app/services/hydraulic/results.py:456`），或让 `benchmark_hydraulic.py` 在 `--sink database` 下接受 `--dsn` 并 fail-closed 要求 DSN。在此之前，AC-3 的性能分项应记为 `NOT RUN` 而非任何形式的通过/失败判读。

---

### F-1 ——【阻断 · 可由代码修复 · 未修复】API 结果与帧端点在生产中永远返回 409；durable worker 与 API 之间仍无任何桥

第 1 轮结论**完全成立**，本轮补上了运行时证据。

- `backend/app/api/hydraulic_routes.py:300-311` —— 类名与自述未变：
  ```
  300: class HydraulicApiStore:
  301:     """Thread-safe API adapter for resources not yet attached to a worker repo.
  311:         self.durable_backend = False
  ```
- `hydraulic_routes.py:8` 模块 docstring 仍写「that have not yet attached the durable worker repositories; worker code must …」。
- `hydraulic_routes.py:3346` —— 提交时 `"published_frames": {}` 是 app 内**唯一**写入点（R-14）；`published_frames_len` 在 R-4 实测中为 `0`。
- `hydraulic_routes.py:3395-3401` —— `_require_completed` 要求 `status=="completed" and published_attempt_id and result`，否则 `raise _error(409, "VALIDATION_ERROR", "simulation has not published a result")`。
- `hydraulic_routes.py:3483-3489` —— `_frame_payload` 在 `published_frames` 为空时同样 409。
- `hydraulic_routes.py` 内唯一改写 `item["status"]` 的地方仍是取消端点（置 `"cancelled"`）；没有任何代码把 `queued→running→completed`。
- R-15：`simulation_worker.py:44,55` 只导入 `sandbox` 与 `tasks`，**无 `app.services.hydraulic.results`**。
- `simulation_worker.py:281` —— `result_committed=return_code == 0`：「结果已提交」仍等价于「solver 进程退出码为 0」。

**R-4 运行时实测（新增证据）**：

| 端点 | HTTP | code |
|---|---|---|
| `GET /api/simulations/{id}/result` | 409 | `VALIDATION_ERROR` |
| `GET /api/simulations/{id}/frames` | 409 | `VALIDATION_ERROR` |
| `GET /api/simulations/{id}/frames?fields=depth_m` | 409 | `VALIDATION_ERROR` |
| `GET /api/simulations/{id}/frames/0` | 409 | `VALIDATION_ERROR` |
| `GET /api/simulations/{id}/frames/144` | 409 | `VALIDATION_ERROR` |
| `GET /api/simulations/{id}/frames/288` | 409 | `VALIDATION_ERROR` |

`backend/app/services/hydraulic/results.py:456` 的 `ResultRepository`（`write_frame`/`finalize`，写 `simulation_frames` hypertable）实现依旧完整，但没有任何 simulation 路径调用它。

**说明一处与第 1 轮的差异**：第 1 轮记录的 409 code 是 `SIMULATION_RESULT_NOT_READY`；本轮实测为 `VALIDATION_ERROR`（`_require_completed` 的分支）。`SIMULATION_RESULT_NOT_READY` 仍保留在 `_frame_count`（`hydraulic_routes.py:3408-3410`）但本场景走不到。**语义未变，仍是 409。**

**可修复**：同第 1 轮 —— 把 worker 的 finalize 接到 `ResultRepository.write_frame/finalize`，让 `/simulations/{id}/result|frames|frames/{i}` 改读 durable 仓储，替换 `hydraulic_store` 的 simulation 子集；移除 `tests/acceptance/test_acceptance_matrix.py` 的内存注入依赖。

---

### F-2 ——【阻断 · 可由代码修复 · 未修复】仿真进度永不推进，0→100 是一次跳变

第 1 轮结论**完全成立**，本轮补上了运行时证据。

- R-12：`grep -c progress backend/app/workers/simulation_worker.py` → `0`。
- R-13：`update_progress` 在 app 生产代码中**仍无调用点**（`tasks.py:2080` 是 `SessionSimulationTaskRepository` 包装器对其自身仓储的调用，不是业务生产者）。
- `backend/app/services/hydraulic/tasks.py:1597-1626` 的 `SqlAlchemySimulationTaskRepository.update_progress` 实现依旧完整（0..99 校验、单调性、lease 断言）。

**R-4 运行时实测（新增证据）**：提交后 4 次轮询（每次间隔 0.5 s）全部返回

```
{"http": 200, "status": "queued", "progress": 0, "phase": "accepted"}
```

进程内字典亦然：`{"progress": 0, "status": "queued", "phase": "accepted", "published_frames_len": 0, "result_is_none": true}`。

**后果**：`SOURCE_PRD.md:21`（FR-3「0-100% 进度」）与 `PRD_SUPPLEMENT.md:588`（「进度 0/100」）在运行时不成立。真实 24h 仿真期间 API 只会返回 `progress=0`，且本轮连 `running` 状态都没有出现。

**可修复**：同第 1 轮 —— 在 solver CLI 的流式帧输出回调里按 `frame_index/total` 上报进度（CLI 已有 `frame_manifest`，`backend/app/services/hydraulic/solvers/cli.py:479-488`），worker 侧调用 `repository.update_progress`；并补一条「running 期间 progress 单调递增且 <100，completed 时 =100」的集成断言。

---

### F-4 ——【已修复 · 本轮唯一确认修复项】冻结 acceptance 入口的 AC→gate 映射不再弱于需求表

- `scripts/verify_hydraulic_stack.py:1749-1791` —— 新增 `acceptance_requirements(root)`，docstring 明写消除「第二份硬编码副本」：
  ```
  1751:    ``acceptance_requirements.json``.  A second hardcoded copy in this script
  1752:    silently weakened every gate it omitted, which let an AC be judged PASS
  1753:    while a required artifact (most often G6's two profile runs) was absent.
  1755:    A missing or malformed fixture blocks the gate rather than falling back to
  1756:    a weaker built-in list.
  ```
  读取 `ACCEPTANCE_REQUIREMENTS_FIXTURE`（`:121`），逐 case 校验 `required_gates` ⊆ `_ACCEPTANCE_GATES`，缺 case / 多 case / 格式错误一律 `GateError`。
- `:1856-1867` —— acceptance 判定改用该映射：`required = ac_requirements[ac_name]`。
- 旧的硬编码 `ac_requirements = {"ac3": ("g3","g5","e2e"), ...}` 已不存在（grep `ac_requirements` 仅命中函数与调用点）。

**R-3 实测生效**：本轮 `required_gates` 为 `["g3","g5","e2e","g6"]`，`missing_or_invalid_artifacts` 同时列出 `g6/postgis/result.json` 与 `g6/portable/result.json` —— 第 1 轮时 ac3 条目里根本没有 g6。

**残留风险（不构成 finding）**：`acceptance_requirements.json` 自身仍是仓库内可编辑的 fixture，脚本信任它。若 fixture 与 `PRD_SUPPLEMENT.md:588` 的证据清单漂移，判定会随之漂移。当前二者一致，本轮不判为缺陷。

---

### F-5 ——【重大 · 可由代码修复 · 未修复】「50 年一遇」的设计暴雨量级物理上不成立

`backend/app/services/hydraulic/rainfall.py:1007-1026` 与第 1 轮逐字相同：

```
1013:        return (round(20.0 * period, 8), 0.0)
1024:        values.append(round(20.0 * period * shape, 8))
```

峰值强度 = `20.0 × 重现期` mm/h，与重现期**线性成正比**。R-11 复测（`backend/.venv` Python 3.11.15）：

| T (yr) | 24h 总量 (mm) |
|---|---|
| 1 | 160.0 |
| 2 | 320.0 |
| 5 | 800.0 |
| 10 | 1600.0 |
| 20 | 3200.0 |
| **50** | **8000.0** |
| 100 | 16000.0 |

50 年 24 小时 8000 mm 降雨、峰值 996.531 mm/h，超过地球有记录的最高日降雨量一个数量级以上；真实 IDF 下 50 年一遇 24h 深度约为 1 年一遇的 2～2.5 倍而非 50 倍（上海 50 年一遇 24h 约 300～400 mm）。

**说明（同第 1 轮，仍然成立）**：AC-3 的验收测试不走这条生成路径。`tests/acceptance/test_acceptance_matrix.py:100-124` 的 `_create_rainfall` 用 fixture 的 `intensity_pattern_mm_h`（峰值 20 mm/h）直接提交 `points`；`return_period_years=50` 只是**调用方自报 intensities 上的一个标签**。存在两条不一致的 50 年路径。

**可修复**：改为查表式 IDF（如 `depth_mm = a*T^b`），并在 `RainfallSeries` 上加量级上界断言。

---

### F-6 ——【重大 · 可由代码修复 · 半修复】4 物理核仍未强制；新增的峰值 RSS 采集在本审计主机上恒为 `None`

**已改善的部分**：`scripts/benchmark_hydraulic.py:727-760` 新增 `peak_rss_bytes()`，含 win32 `GetProcessMemoryInfo` 分支；`:1097-1128` 已把它写进 `peak_rss_bytes_after_run` 与 `peak_rss_bytes_max`。这补上了 `PRD_SUPPLEMENT.md:409-412` 要求的报告项。

**但在本审计主机上该分支是失效的（N-2，见 §3 独立条目）**，R-18 实测 `peak_rss_bytes(): None`。R-2 的 `environment` 块同样：`"cpu_model": null, "physical_cores": null, "ram_bytes": null, "logical_cpus": 32`。

**4 物理核仍然只被「记录」未被「施加」**：`scripts/benchmark_hydraulic.py:834-877` 的 `configure_resources` 在 win32 上两个分支都走不到 —— R-18 实测 `hasattr(os, "sched_setaffinity") = False`、`resource` 模块缺失，因此 `affinity_before/after = None`、`address_space_limit_applied = False`，且**没有 fail-closed**。R-2 产出的 `resources` 块与第 1 轮完全一致：

```json
"resources": {"address_space_limit_applied": false, "affinity_before": null,
              "affinity_after": null, "requested_cpus": 4, "requested_memory_bytes": 8589934592,
              "pinned_environment": {"OMP_NUM_THREADS":"4","MKL_NUM_THREADS":"4","OPENBLAS_NUM_THREADS":"4",
                                      "NUMEXPR_NUM_THREADS":"4","CUDA_VISIBLE_DEVICES":""}}
```

实际生效的只有 5 个线程环境变量。第 1 轮建议的「无法设置时以非零退出（fail-closed）」**未实现**。

`scripts/benchmark_hydraulic.py:896-919` 的 `linux_cpu_details()` 仍只解析 `/proc/cpuinfo`；`:920-930` 的 `total_ram_bytes()` 依赖 `/proc/meminfo` 或 `os.sysconf` —— 两者在 Windows 上都返回 `None`（R-18）。

**可修复**：给 `linux_cpu_details()` / `total_ram_bytes()` 加 Windows 路径（WMI 或 `GetLogicalProcessorInformationEx`）；`configure_resources` 在 win32 用 Job Object 设内存上限、用 `SetProcessAffinityMask` 设 CPU 亲和性，并在无法设置时 fail-closed 非零退出。

---

### N-2 ——【重大 · 可由代码修复 · 本轮新发现】`peak_rss_bytes()` 的 win32 分支因 ctypes `restype` 缺陷恒返回 `None`

`scripts/benchmark_hydraulic.py:736-758`：

```python
process = ctypes.windll.kernel32.GetCurrentProcess()
success = ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
return int(counters.PeakWorkingSetSize) if success else None
```

`GetCurrentProcess` 未设置 `restype`，ctypes 默认按 `c_int` 处理返回值。在 64 位 Windows 上它返回伪句柄 `(HANDLE)-1 = 0xFFFFFFFFFFFFFFFF`，按 `c_int` 传参时被截断为 32 位，`GetProcessMemoryInfo` 拿到无效句柄返回 0。

**R-19 直接对照实验**：

| 调用方式 | `success` | `PeakWorkingSetSize` |
|---|---|---|
| 默认（脚本现状） | `0` | `0` → 探针返回 `None` |
| `GetCurrentProcess.restype = ctypes.c_void_p` | `1` | `13336576` |

（结构体大小 72 字节 = 4+4+8×8，与 Win32 `PROCESS_MEMORY_COUNTERS` 的 x64 布局一致，因此不是对齐问题；加 `restype` 后同一结构体即成功。）

**后果**：`PRD_SUPPLEMENT.md:409-412` 要求的「峰值 RSS」在 Windows 上永远报不出来——正是本次验收运行的主机。第 1 轮记的 `peak_rss_bytes: null` 看似「未实现」，实际是「实现了但没生效」。

**可修复**：一行改动 ——

```python
get_current_process = ctypes.windll.kernel32.GetCurrentProcess
get_current_process.restype = ctypes.c_void_p
process = get_current_process()
```

并建议同时设置 `GetProcessMemoryInfo.restype = ctypes.c_int` 与 `argtypes` 以免 64 位句柄再次被截断。

---

### F-7 ——【重大 · 可由代码修复 · 未修复】G5 benchmark 的降雨不是 50 年一遇

`backend/tests/fixtures/performance/solver/5k_10k_24h_v1/rainfall.json` 与第 1 轮逐字相同：

```json
"intensity_model": "base + (peak - base) * exp(-((phase - 0.42) / 0.16) ** 2)",
"peak_intensity_mm_h": 40.0, "sample_count": 289, "step_seconds": 300,
"schema": "hydraulic-solver-benchmark-rainfall-v1"
```

一个高斯包络，**无 `return_period_years` 字段**，与 50 年无关。

**后果**：即使 N-1/F-1/F-2 全部修好，G5 也只覆盖「24 h/289 帧/5000 节点/10000 管段」，不覆盖 AC-3 题面的「50 年一遇」。AC-3 仍缺一个 50 年输入的 solver+worker+落库+回放全链路用例。

**可修复**：新增一个 `50y_24h` 的 solver benchmark fixture（真实 50 年强度序列），使 G5 覆盖 AC-3 题面。

---

### F-8 ——【重大 · 可由代码修复 · 未修复且现已不可测量】帧结果不可逐位复现

- 第 1 轮 R-2 测得一次运行内 4 次 attempt 的 `frame_sha256` 互不相同；`VALIDATION_EVIDENCE.md:157` 记录上一轮三次 run 同样互不相同（`…9e7e753b…` / `4143db54…` / `7228b025…`）。
- 本轮**无法复测**：N-1 使 benchmark 在 warmup attempt 断言处即中止，不产出任何 `frame_sha256`（R-2 输出中无 `measurements` 字段）。
- 唯一现存的相关测试是 `backend/tests/hydraulic/test_python_hydraulics.py:436 test_same_input_is_deterministic`，R-8 显示通过（`26 passed`），但它只在 **2 节点 / 1 管段 / 180 s** 的玩具网络上比较 `canonical_json()`，覆盖不到 5000/10000/24h 规模下漂移的来源。

**AC-3 判据含「取消/重试/回放可复现」**。同一 fixture、同一 seed 下 hash 漂移说明求解或序列化存在未固定的非确定性来源；重试后无法保证回放一致。

**可修复**：先让 N-1 恢复可测量性，再在 5k/10k/24h 规模上定位并消除漂移（或在契约中显式定义「可复现」的容差与度量）。

---

### F-9 ——【重大 · 可由代码修复 · 未修复】时间轴回放的 e2e 证据仍是 2 帧 fixture，且仍不是 Playwright

- R-16：`frontend/e2e/fixtures/api-v1/baseline-frames.json` → `frame_count 2`（与第 1 轮相同）。
- `frontend/src/test/acceptance/frontend-acceptance.test.tsx:94,114` 仍是 `frame_count: 2`。
- `frontend/src/test/acceptance/performance-scale.test.ts:109` 确有 289 帧用例（`'retains all 289 published frames inside the frozen cache budget'`），但它是对 `FrameCache` 塞入**合成帧**后断言 `cache.size === 289` 且 `sizeBytes <= maxBytes` —— 是客户端缓存预算门禁，**不是「真实 50 年 24h 仿真结果的时间轴回放」**。
- R-22：`frontend/e2e/run-ac-smoke.mjs:1-11` 仍自述「Dependency-light AC end-to-end smoke … launches a pinned local Chrome/Chromium through the **DevTools Protocol**」；`frontend/package.json` 无 `playwright` 依赖，`frontend/node_modules/@playwright` 不存在，`frontend/e2e/` 只有 `fixtures` 与该 `.mjs`。仓库内**仍不存在** Playwright。

**`PRD_SUPPLEMENT.md:588` 点名的「Playwright timeline」依旧没有对应实现。**

**可修复**：把 e2e 时间轴 fixture 从 2 帧扩到 289 帧并接真实后端；引入 Playwright 或在契约中把验收实现改判为 CDP runner（后者需修订 `PRD_SUPPLEMENT.md:588`，属决策而非代码）。

---

### F-10 ——【重大 · 可由代码修复 · 部分推进】worker E2E 仍是空 solver + 内存仓库；新增的 live-DB 契约本机全部 skip 且不覆盖 kill/fencing

**推进的部分（本轮新增文件）**：`backend/tests/integration/test_simulation_worker_live_database.py`（208 行）是真实的新增覆盖 —— 它断言 `build_service` 装配的是 `SessionSimulationTaskRepository`（`:146`）而非内存控制面，lease/timeout 策略来自 `Settings`（`:148-152`），且 `/readyz` 200 必须经过一次真实的 `claim_next` 往返（`:157-197`）。

**但它在本审计主机上一条都没跑**：R-9 → `3 skipped`。跳过条件是 `_live_dsn()`（`:49-58`），而 R-20 显示 `DATABASE_TEST_DSN_PORTABLE` / `DATABASE_TEST_DSN_POSTGIS` 在 `.github/workflows/ci.yml` 与 `docker-compose.yml` 中**均无配置** —— 即 CI 上也会 skip。

**它不覆盖 AC-3 判据 5/6/7**：
- 无 24h 规模真实 `solvers.cli` attempt（`:83,94` 只把 argv 传给 `build_service`，`run_startup_checks` 只探测 argv[0]）；
- 无 worker kill 后的 lease 恢复；
- 无 fencing token 递增断言；
- 无 stale attempt 不得发布帧/结果；
- 无 attempt 耗尽。

**主控制循环测试未变**：`backend/tests/hydraulic/test_simulation_worker_entry.py` 仍是 13 处 `_build_worker`，仓储一律 `InMemoryTaskRepository`（`:27` import，`:697` 起），solver 一律 `python -c "pass"` 或 `python -c "import time; time.sleep(120)"`（`:699,725,749,779,809,835,874,910,956,977,…`）。R-7 实跑 `79 passed, 1 skipped`（用例数与第 1 轮完全相同），绿色但覆盖的是控制循环语义。

**Windows 进程级 SIGTERM 优雅退出仍为 skip**（R-7 的 1 skipped），`VALIDATION_EVIDENCE.md:449` 记「simulation 提交路径的真实生产者 \| NOT FOUND」——该自述与本轮 F-1/F-2 的实测一致。

**可修复**：在 `docker-compose.yml` 与 `.github/workflows/ci.yml` 中实际设置 `DATABASE_TEST_DSN_*`（或用 service container 起库），让 live-DB 契约真正执行；再补 24h 规模、真实 `solvers.cli`、kill/fencing/stale-publish 的集成用例。

---

### N-3 ——【重大 · 可由代码修复 · 本轮新发现】`VALIDATION_EVIDENCE.md` §2.2 的 gate 15 记录已与代码可复现结果矛盾

`docs/hydraulic-twin/VALIDATION_EVIDENCE.md:154`（persistence 行）与 `:157`（frame_sha256 行）仍记：

```
| `persistence` | 三次均 `all_frames_committed/database_committed/published/quality_report_committed = true`，`sink_finalized: true` |
| `failures` | 未在摘录中出现 `failures` 字段；`status: "PASS"` |
| `utc_timestamp` | `2026-09-27T19:01:09.770925Z` |
```

我本轮在 `2026-09-27T21:08:39Z`（R-2，同一 commit `ca99e048`、同一工作树）跑出的是 `status: "FAIL"` / `exit_code: 1` / `benchmark_executed: false`。即：**F-3 的整改使该 PASS 不可复现，但证据台账未随之更新。**

这与第 1 轮 §6 的结论（「文档自述诚实、未发现夸大」）相反：AC-3 相关的证据台账现在**高估了当前状态**。这正是本报告不能采信文档勾选的直接例证。

**可修复**：重跑并更新 `VALIDATION_EVIDENCE.md:140-157`，或在 F-3 变更的同一提交中把该节标为 stale。

---

## 4. 通过项（本轮我亲自跑出）

- **P-1 冻结 acceptance 入口把 AC-3 判为 `BLOCKED`。** R-3：`HYDRAULIC_STACK_STATUS=BLOCKED` / `EXIT=2`，5 个 required artifact 全部缺失。这与源码自述一致，`PASS` 不成立。
- **P-2 冻结入口的 AC→gate 映射已修复并实测生效。** R-3 输出中 ac3 的 `required_gates` 为 `["g3","g5","e2e","g6"]`，`missing_or_invalid_artifacts` 含两个 g6 artifact（F-4）。
- **P-3 `tests/acceptance/` 全绿且有增长。** R-5：`65 passed, 1 warning in 4.85s`（第 1 轮 `61 passed`）。仓库 venv `Python 3.11.15`，满足 `test_acceptance_contract_runtime_is_python_311`。
- **P-4 AC-3 命名的 3 个验收测试通过。** R-6：`3 passed, 15 deselected`（与第 1 轮 R-5 同一选择器，可比）。**但它们仍是请求元数据 smoke + 内存注入**：`tests/acceptance/test_acceptance_matrix.py:176-205` 的 `_inject_in_memory_result` docstring 仍写「Inject an in-memory diagnostic result; **this is never worker evidence**」，它直接改内存字典写 `status="completed"`、`progress=100`。
- **P-5 提交路径的 289 帧栅格仍是真算的。** R-4 实测提交响应 `frame_count: 289`；`hydraulic_routes.py` 的 `frame_count = duration_seconds // frame_interval_seconds + 1` 未变。`tests/acceptance/…::test_ac3_request_smoke_50_year_24_hour_preserves_289_frame_grid` 包含在 R-6 的 3 条中并通过。
- **P-6 worker 控制循环测试全绿。** R-7：`79 passed, 1 skipped in 12.93s`（第 1 轮 `79 passed, 1 skipped`），用例数未变。
- **P-7 求解器基础测试全绿，含确定性用例。** R-8：`26 passed, 1 warning in 0.06s`（`test_same_input_is_deterministic` 在内，但见 F-8 的规模限制）。
- **P-8 前端时间轴与任务面板单测全绿。** R-10：`Test Files 4 passed (4)` / `Tests 26 passed (26)`（`timelinePlayer` 16 + `timelineControl` 3 + `taskStream` 2 + `taskController` 5）。`useTimelinePlayer.ts` 的 1/2/4 倍速与 play/pause/seek/step 控制器未变。
- **P-9 前端到帧 API 的接线是真实的。** `frontend/src/features/simulation/api/client.ts:352-360` → `GET /simulations/{id}/frames/{frameIndex}`。**但后端该端点本轮实测恒 409（F-1），接线目前无法走通。**
- **P-10 生产代码能生成 50 年 / 24h / 5min 设计暴雨并给出 289 点。** R-11：各重现期均 289 点。**但量级见 F-5。**

---

## 5. 我没有跑的（不得读作通过）

| 未跑项 | 原因 |
|---|---|
| G3 worker/numeric 门禁 | 本次 ask 是定向验收，不重复全量门禁 |
| G5 的 API / nowcast / 前端浏览器三套 benchmark | 同上；我只跑了 G5 四件套中的 solver 一件（且它 FAIL） |
| `e2e/ac-smoke.json`（`run-ac-smoke.mjs` 全量） | 需要先有真实 frame 数据源（F-1 未修）；本轮未执行 |
| G6 Compose（postgis / portable）、README 演示、备份恢复 | 未执行 |
| Playwright timeline | 仓库内**不存在** Playwright 依赖或配置（R-17/R-22）；本轮未变 |
| 真实 EPA SWMM 二进制、`.out` 解析 | `VALIDATION_EVIDENCE.md:438` 记 `NOT RUN`；本轮未变 |
| 4 物理核环境下的任何重跑 | 本机 32 逻辑核，`os.sched_setaffinity` 不存在、`resource` 模块缺失（F-6 / R-18） |
| PostgreSQL 真实 lease / fencing / attempt 耗尽 | 无 `DATABASE_TEST_DSN_*`；live-DB 测试 3 skip（R-9），CI 亦未配置（R-20） |
| Windows 进程级 SIGTERM 优雅退出 | 平台 skip（R-7 的 1 skipped） |
| 5k/10k/24h 规模下的帧 hash 复现性 | N-1 使 benchmark 中止，不再产出 `frame_sha256`；只能由 F-8 修复后重测 |
| `tests/acceptance/test_acceptance_matrix.py` 之外的全量 pytest | 本次 ask 是定向验收；仅跑 acceptance 全目录、AC-3 相关与 hydraulic 相关子集 |

---

## 6. 关于「文档自述」的核对

与第 1 轮相反，**本轮发现一处文档高估**：

- **不一致（新增）**：`VALIDATION_EVIDENCE.md:154,157` 仍宣称 gate 15 `status: "PASS"`、persistence 四项 `= true`，而当前代码在同一 commit 上复现为 `FAIL`（N-3）。
- **仍然一致的**：
  - `matrix.json` AC-3 条目（`:72-83`）：`status: "NOT RUN"`、`pass_nodeids: []`，reason 写明「Current tests are request metadata smoke plus in-memory frame and repository injection. Real four-core solver, worker kill, database fencing, timeout, retry, cleanup and timeline artifacts were not run.」——与 F-1/F-2/F-6/F-9/F-10 的实测完全一致。
  - `backend/tests/fixtures/hydraulic/acceptance/v1/rainfall_50y_24h.json`：`"is_ac3_evidence": false`，`missing_evidence` 五项（real four-core solver execution / quality and conservation report / 289 persisted non-empty frame checksums / worker progress, cancel, retry, kill and cleanup / map timeline replay）——与 F-1/F-3/F-6/F-9/F-10 一一对应。
  - `tests/acceptance/test_acceptance_matrix.py:176` 的 docstring 主动声明注入结果「never worker evidence」。
  - `PRD_SUPPLEMENT.md:588` 与 `IMPLEMENTATION_CONTRACT.md:2549` 的「未实现/本轮未运行」「NOT RUN」——准确。
  - `VALIDATION_EVIDENCE.md:449`「simulation 提交路径的真实生产者 NOT FOUND」——与 R-4 观测到的进度恒 0、状态恒 `queued` 一致。

唯一**代码**侧的文档/真相分歧是 F-4，而它已在本轮修复。

---

## 7. 建议

**判定：AC-3 = `blocked`；不可宣称 PASS；不可宣称「较上轮改善」。**

与第 1 轮相比，净变化是：修好了门禁映射（F-4），但把性能门禁从「假绿」改成「真红」（N-1），并使 AC-3 的性能/质量/可复现三项判据**同时失去可测量性**。若把「门禁诚实失败」看作正确方向，则当前状态是「诚实但空」；若把「上一轮有可复现的 81.56 s」看作可用证据，则本轮是净损失。无论采哪种读法，AC-3 都**不能**判为通过。

修复顺序（全部可由代码修复，无一需要外部决策）：

1. **N-1（最关键，其余验收都依赖它）**：给 G5 solver harness 一个真正写 `simulation_frames` 的 sink（走 `ResultRepository.write_frame`，`backend/app/services/hydraulic/results.py:456`），或加 `--sink database --dsn …` 并在无 DSN 时 fail-closed。在此之前不要用 G5 的任何数字判读 AC-3。
2. **F-1**：把 simulation worker 的 finalize 接到 `ResultRepository.write_frame/finalize`，让 `/simulations/{id}/result|frames|frames/{i}` 改读 durable 仓储；移除 `tests/acceptance/test_acceptance_matrix.py` 的内存注入依赖，改为断言真实仓储写入。
3. **F-2**：solver CLI 流式帧回调按 `frame_index/total` 调 `repository.update_progress`；补「running 期间 progress 单调递增且 <100，completed 时 =100」的集成断言。
4. **N-2**：`GetCurrentProcess.restype = ctypes.c_void_p`（一行），并补 `GetProcessMemoryInfo` 的 `restype/argtypes`。
5. **F-6**：`configure_resources` 在 win32 用 Job Object / `SetProcessAffinityMask` 真正施加限制并 fail-closed；`linux_cpu_details()` / `total_ram_bytes()` 补 Windows 路径。
6. **F-5**：`rainfall.py:1013,1024` 的 `20.0 * period` 换成查表式 IDF，并加量级上界断言。
7. **F-7**：新增 `50y_24h` 的 solver benchmark fixture，使 G5 覆盖 AC-3 题面。
8. **F-8**：N-1 恢复可测量性后，在 5k/10k/24h 规模定位并消除帧 hash 漂移，或在契约中定义可复现容差。
9. **F-9 / F-10**：e2e 时间轴 fixture 从 2 帧扩到 289 帧并接真实后端（并解决 Playwright 依赖问题）；在 CI/Compose 中实际配置 `DATABASE_TEST_DSN_*`，把 worker E2E 从 `InMemoryTaskRepository` + 空 solver 换到 PostgreSQL + 真实 py_mw，并补 kill/fencing/stale-publish 用例。
10. **N-3**：在 F-3 变更的同一提交内更新 `VALIDATION_EVIDENCE.md:140-157`，避免台账高估当前状态。

完成 1–3 后可重跑 `scripts/verify_hydraulic_stack.py --gate acceptance --ac ac3`；此时该命令会真正把 G5 的 FAIL 与 G6 的缺失一起计入 AC-3 判定。
