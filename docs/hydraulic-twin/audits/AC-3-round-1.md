# AC-3 独立验收报告 · 第 1 轮

> 验收人：独立验收员（只读，未修改任何被验收代码）
> 审计日期：2026-09-28
> 仓库：`C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage`
> HEAD：`ca99e0485fb12fbecbd565e79d81f745f8d4a748`（工作树 dirty，157 条未提交变更 —— 本报告所有结论对应当前工作树，不是干净检出）
> 审计范围：AC-3「50 年一遇 24h 仿真进度、结果、地图时间轴可回放」

---

## 0. 结论

**AC-3 判定：`partial`（不通过）。**

AC-3 的**性能分项**（24 h / 289 帧 / `py_mw` 真实求解 / worker wall time ≤180 s）在本轮由我亲自跑通；但 AC-3 的**端到端链路**（50 年暴雨 → 真实 solver → worker → 帧落库 → API 结果 → 前端时间轴回放）**在代码层是断开的**，且冻结的 acceptance 入口自身把 AC-3 判为 `BLOCKED`。

冻结入口本轮实跑输出：

```
$ backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py --gate acceptance --ac ac3 --evidence-dir artifacts/ac3-audit/acceptance
HYDRAULIC_STACK_STATUS=BLOCKED
HYDRAULIC_STACK_EXIT=2
"acceptance": [{"ac": "ac3", "required_gates": ["g3", "g5", "e2e"],
  "missing_or_invalid_artifacts": ["g3", "g5", "e2e",
    "missing artifact: .../g3/result.json",
    "missing artifact: .../g5/result.json",
    "missing artifact: .../e2e/ac-smoke.json"], "status": "BLOCKED"}]
```

`PASS` 不成立。存在 4 个阻断级、全部可由代码修复的缺陷（见 §3 F-1…F-4）。

---

## 1. 判据来源

| 来源 | 位置 | 内容 |
|---|---|---|
| SOURCE_PRD | `docs/hydraulic-twin/SOURCE_PRD.md:33` | 「AC-3 50 年一遇 24h 仿真进度、结果、地图时间轴可回放」 |
| PRD_SUPPLEMENT | `docs/hydraulic-twin/PRD_SUPPLEMENT.md:588` | 自动化证据＝4 核 solver benchmark、worker E2E、289-frame manifest、Playwright timeline；判据＝每次 worker ≤180 s；289 帧；diff ≤300 s；进度 0/100；取消/重试/回放可复现；**当前状态「未实现/本轮未运行」** |
| acceptance_requirements.json | `backend/tests/fixtures/hydraulic/acceptance/v1/acceptance_requirements.json` → `cases["AC-3"]` | 8 条 required_observations + 4 个 required_gates（g3/g5/e2e/g6）+ 5 个 required_artifact_suffixes |
| matrix.json | `backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json` → `cases[id=AC-3]` | `status: "NOT RUN"`、`pass_nodeids: []`，reason 自述「request metadata smoke plus in-memory frame and repository injection」 |
| IMPLEMENTATION_CONTRACT | `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2549` | 「AC-3 \| NOT RUN（无 solver/worker/frame）」 |
| VALIDATION_EVIDENCE | `docs/hydraulic-twin/VALIDATION_EVIDENCE.md:149`（基准 wall time）、`:157`（frame_sha256 互不相同） | gate 15 基准明细（5k/10k/24h，289 帧，59.03/56.96/59.59 s）；`:157` 记上一轮三次 run `frame_sha256` 互不相同 |

### AC-3 的 8 条 required_observations 逐条核对

`backend/tests/fixtures/hydraulic/acceptance/v1/acceptance_requirements.json`：

```json
"AC-3": {
  "required_gates": ["g3","g5","e2e","g6"],
  "required_artifact_suffixes": ["g3/result.json","g5/result.json","e2e/ac-smoke.json",
                                 "g6/postgis/result.json","g6/portable/result.json"],
  "required_observations": [
    "50-year 24-hour design storm runs through the real solver on four physical cores",
    "worker wall time is at most 180 seconds for every repeat",
    "289 persisted frames have monotonic timestamps, non-empty hydraulic content and checksums",
    "quality and conservation checks pass without silent solver fallback",
    "worker kill recovers a PostgreSQL lease and increments the fencing token",
    "stale attempts cannot publish frames or results",
    "timeout, cancel, retry backoff, attempt exhaustion and workspace cleanup are proven",
    "API progress, cancellation and map timeline replay are reproducible"
  ]
}
```

| # | required_observation | 判定 | 依据 |
|---|---|---|---|
| 1 | 50 年 24h 设计暴雨过真实 solver @4 物理核 | **not met** | F-5 / F-6 / F-7 |
| 2 | 每次 worker wall ≤180 s | **partial** | §4 P-3：实测 81.56 s，但非 4 物理核、非 worker（是进程内 executor） |
| 3 | 289 帧已持久化、单调时间戳、非空水力内容与校验和 | **not met** | F-1 / F-3：帧写临时文件后即删；`database_committed` 是硬编码 `True` |
| 4 | 质量与守恒通过且无静默 solver 降级 | **partial** | 守恒残差 0.0（实测）；「无静默降级」在 benchmark 中无断言 |
| 5 | worker kill 恢复 PostgreSQL lease 并递增 fencing token | **not met** | F-10：仅 `InMemoryTaskRepository` + 空 solver；Windows SIGTERM 用例 skip |
| 6 | stale attempt 不能发布帧/结果 | **partial** | 仅在 `InMemoryTaskRepository` 上验证 |
| 7 | timeout/cancel/retry backoff/attempt 耗尽/工作区清理 | **partial** | 单测级内存仓库；无 24h 规模端到端 |
| 8 | API 进度、取消、地图时间轴回放可复现 | **not met** | F-2（进度恒为 0）+ F-1（结果恒 409）+ F-9（回放用 2 帧 fixture） |

---

## 2. 本轮我实际执行的命令（及其真实输出）

| # | 命令 | 结果 |
|---|---|---|
| R-1 | `git rev-parse --short=8 HEAD` / `git status --porcelain \| wc -l` | `ca99e048` / `157` |
| R-2 | `backend/.venv/Scripts/python.exe scripts/benchmark_hydraulic.py --fixture backend/tests/fixtures/performance/solver/5k_10k_24h_v1/manifest.json --warmup 1 --repeats 3 --cpus 4 --memory 8g --threads 4 --output artifacts/ac3-audit/solver.json` | `HYDRAULIC_BENCHMARK_STATUS=PASS` / `EXIT=0` / `SHA256=7d797f697be16cac02c53e65bcd3fe680df12378384e9c0099d7340272bd35b3`；`worker_wall_time_max_seconds=81.5556`（阈值 180）、`frame_count=289`、`mass_balance_relative_residual=0.0` |
| R-3 | `backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py --gate acceptance --ac ac3 --evidence-dir artifacts/ac3-audit/acceptance` | `HYDRAULIC_STACK_STATUS=BLOCKED` / `HYDRAULIC_STACK_EXIT=2` |
| R-4 | `cd backend && ./.venv/Scripts/python.exe -m pytest tests/acceptance/ -q` | `61 passed, 1 warning in 4.82s` |
| R-5 | `cd backend && ./.venv/Scripts/python.exe -m pytest tests/acceptance/test_acceptance_matrix.py -k "ac3 or fr3_in_memory_task_repository" -q` | `3 passed, 15 deselected in 0.59s` |
| R-6 | `cd backend && ./.venv/Scripts/python.exe -m pytest tests/hydraulic/test_tasks_sandbox.py tests/hydraulic/test_simulation_worker_entry.py -q` | `79 passed, 1 skipped in 16.48s` |
| R-7 | `cd frontend && npx vitest run src/features/simulation/test/timelinePlayer.test.ts src/features/simulation/test/timelineControl.test.tsx` | `Test Files 2 passed (2) / Tests 19 passed (19)` |
| R-8 | `cd backend && ./.venv/Scripts/python.exe -c "from app.services.hydraulic.rainfall import generate_design_rainfall; s=generate_design_rainfall(50, duration_seconds=86400, step_seconds=300); ..."` | `return_period_years=50 / duration=86400 / step=300 / point_count=289 / peak=996.531 mm/h / total_depth=8000.008 mm` |
| R-9 | `grep -cn "progress" backend/app/workers/simulation_worker.py` | `0`（exit 1） |
| R-10 | `grep -rn "update_progress" backend/ --include=*.py \| grep -v ".venv\|build/lib"` | 仅 `tasks.py:820,1083,1597,2065,2074` + `tests/hydraulic/test_tasks_sandbox.py:250,251,259,261`；**app 生产代码零调用点** |
| R-11 | `grep -rn "ResultRepository" backend/app --include=*.py` | 仅 `results.py:456`（定义）、`results.py:1032`（`__all__`）、`calibration.py:26`（import）；**`simulation_worker.py` 无引用** |
| R-12 | `grep -rn "published_frames" backend/ --include=*.py \| grep -v ".venv\|build/lib"` | 仅 `hydraulic_routes.py:3267`（置 `{}`）、`:3404`、`:3408`（读取）+ `tests/acceptance/test_acceptance_matrix.py:198`（测试注入） |
| R-13 | `python -c "...json.load(frontend/e2e/fixtures/api-v1/baseline-frames.json)..."` | `frame_count=2`，`frames len=2`，`frame_index [0,1]`，`frame_time 2026-09-24T00:00:00Z / 00:05:00Z` |
| R-14 | `python -c "...json.load(backend/tests/fixtures/performance/solver/5k_10k_24h_v1/rainfall.json)..."` | `intensity_model = base + (peak-base)*exp(...)`，`peak_intensity_mm_h=40.0`，`sample_count=289`，**无 return_period 字段** |

产物：`artifacts/ac3-audit/solver.json`（+ `.sha256`）、`artifacts/ac3-audit/acceptance/result.json`（+ `.sha256`）。

---

## 3. 缺口（findings）

### F-1 ——【阻断 · 可由代码修复】API 结果与帧端点在生产中永远返回 409；durable worker 与 API 之间没有任何桥

`/api/simulations/{id}/result` 与 `/frames` 读的是进程内字典 `hydraulic_store`：

- `backend/app/api/hydraulic_routes.py:286-317` —— `class HydraulicApiStore` 自述「Thread-safe API adapter for resources **not yet attached to a worker repo**」，`self.durable_backend = False` 恒为 `False`，`self.simulations: dict[str, dict[str, Any]] = {}`。
- `backend/app/api/hydraulic_routes.py:3267` —— 提交时 `"published_frames": {}`，此后**生产代码中再无任何写入**（R-12）。
- `backend/app/api/hydraulic_routes.py:3403-3406` —— `_frame_payload` 在 `published_frames` 为空时 `raise _error(409, "SIMULATION_RESULT_NOT_READY", ...)`。
- `backend/app/api/hydraulic_routes.py:3316-3322` —— `_require_completed` 要求 `status=="completed" and published_attempt_id and result`；而 R-13/grep 显示 app 中**唯一**改写 `item["status"]` 的地方是取消端点 `hydraulic_routes.py:3305`（置 `"cancelled"`）。没有任何代码把 queued→running→completed。
- `backend/app/workers/simulation_worker.py:43-55` —— 导入只含 `app.core.config`、`app.services.hydraulic.sandbox`、`app.services.hydraulic.tasks`；**没有 `app.services.hydraulic.results`**（R-11）。
- `backend/app/workers/simulation_worker.py:277-282` —— `ExecutionResult(return_code=return_code, result_committed=return_code == 0)`：所谓「结果已提交」等价于「solver 进程退出码为 0」，与是否落库无关。
- `backend/app/main.py:1271-1273` —— readiness 读 `hydraulic_store.durable_backend`，恒 `False`；`hydraulic_routes.py:327-335` 在 `DEPLOYMENT_ENVIRONMENT == production` 时对写操作返回 503 `DURABLE_STORE_UNAVAILABLE`。

`backend/app/services/hydraulic/results.py:456` 的 `ResultRepository`（`write_frame`/`finalize`，写 `simulation_frames` hypertable，`models/hydraulic_models.py:1744`）实现完整，但**没有任何 simulation 路径调用它**。

**后果**：AC-3 的「结果」与「时间轴回放」在生产中不可能成立。`tests/acceptance/test_acceptance_matrix.py:176-205` 的 `_inject_in_memory_result` 函数文档串自己写明「Inject an in-memory diagnostic result; **this is never worker evidence**」——测试是靠直接改内存字典造出 completed 状态的。

**可修复**：需要把 simulation worker 的 finalize 接到 `ResultRepository.write_frame/finalize`，并让 API 从同一 durable 仓储读帧/结果，替换 `hydraulic_store` 的 simulation 子集。属实现缺口，非配置问题。

---

### F-2 ——【阻断 · 可由代码修复】仿真进度永不推进，0→100 是一次跳变

- `backend/app/services/hydraulic/tasks.py:1597-1626` —— `SqlAlchemySimulationTaskRepository.update_progress` 实现完整（0..99 校验、单调性、lease 断言 `SimulationRun.progress <= progress`）。
- 但 R-10 显示 `update_progress` 在**整个 app 生产代码中零调用点**；R-9 显示 `backend/app/workers/simulation_worker.py` 中 `progress` 出现 **0 次**。
- 唯一的调用者是 `backend/tests/hydraulic/test_tasks_sandbox.py:250,251,259,261`，即仓储方法自身的单测。

**后果**：AC-3 判据「进度 0/100」不成立——真实 24h 仿真期间 API 只会返回 `progress=0`，任务结束时直接跳到 100。`hydraulic_routes.py:3223`（`"progress": 0` at submit）与 `:3628`（读取）之间的空档没有任何生产者。PRD_SUPPLEMENT D-08 要求「progress 是已提交的单调 0..100」，实现侧只有状态机、没有生产者。

**可修复**：在 solver CLI 的流式帧输出回调里按 `frame_index/total` 上报进度（CLI 已有 `frame_manifest`：`solvers/cli.py:479-488`），worker 侧调用 `repository.update_progress`。

---

### F-3 ——【阻断 · 可由代码修复】G5 solver benchmark 的「已落库」标志是硬编码字面量，帧写临时文件后即删

- `backend/app/services/hydraulic/benchmark.py:379-385` ——

  ```python
  "persistence": {
      "database_committed": True,
      "all_frames_committed": published["frame_count"] == frames["count"],
      "quality_report_committed": True,
      "published": True,
  },
  ```

  `database_committed` / `quality_report_committed` / `published` 三个值是**常量 `True`**，不来自任何提交结果。只有 `all_frames_committed` 是真实比较。
- `scripts/benchmark_hydraulic.py:1041-1046` —— sink 落在 `tempfile.TemporaryDirectory(prefix="hydraulic-benchmark-")` 下的 `{phase}-{index}.jsonl`；上下文退出即删除。
- `scripts/benchmark_hydraulic.py:402-403` —— `class BenchmarkFrameSink`，`self._handle = path.open("wb")`，是普通文件句柄，不是数据库连接。

这与 `PRD_SUPPLEMENT.md:163`「帧 MUST 流式写 TimescaleDB；MUST NOT 先在内存或沙箱积累全部 289 帧」直接冲突。

**后果**：R-2 的 `PASS` 只证明「py_mw 在本机 81.56 s 内算完 289 帧且守恒残差为 0」，**不证明任何一帧进入 `simulation_frames`**。AC-3 判据 3「289 persisted frames」未满足。

**可修复**：让 benchmark sink 写真实 hypertable（或经 `ResultRepository`），并把三个常量换成提交结果。

---

### F-4 ——【阻断 · 可由代码修复】冻结 acceptance 入口的 AC→gate 映射弱于冻结需求表，AC-3 可在缺 G6 时被判 PASS

- `scripts/verify_hydraulic_stack.py:1702-1710` —— 硬编码：

  ```python
  ac_requirements = {
      "ac1": ("g2",), "ac2": ("g2",),
      "ac3": ("g3", "g5", "e2e"),
      "ac4": ("g4", "e2e"), "ac5": ("g3", "e2e"),
      "ac6": ("e2e",), "ac7": ("g0", "e2e"), "ac8": ("g6",),
  }
  ```
  判定逻辑 `status = "PASS" if not missing and not reasons else "BLOCKED"`（同文件 `:1723`）。
- `backend/tests/fixtures/hydraulic/acceptance/v1/acceptance_requirements.json` 中各 AC 的 `required_gates` 实为：ac1 `[g1,g2,g6]`、ac2 `[g2,g6]`、ac3 `[g3,g5,e2e,g6]`、ac4 `[g4,g5,e2e,g6]`、ac5 `[g3,g5,e2e,g6]`、ac6 `[g3,e2e,g6]`、ac7 `[g0,e2e,g6]`、ac8 `[g6]`。

除 ac8 外**每一项**都被脚本削弱。对 AC-3 具体意味着：只要 g3 + g5 + e2e 三个 artifact 存在，AC-3 就会记 PASS，**即使 g6/postgis 与 g6/portable 全部缺失**。本轮 R-3 的输出里 `artifact_errors.g6` 明确列出两个缺失 artifact，而 ac3 条目的 `required_gates` 里根本没有 g6。

**可修复**：改为从 `acceptance_requirements.json` 读取映射（该文件已是 fixture registry 的一部分），或至少把 ac3 补成 `("g3","g5","e2e","g6")`。

---

### F-5 ——【重大 · 可由代码修复】「50 年一遇」的设计暴雨量级物理上不成立

`backend/app/services/hydraulic/rainfall.py:1007-1025`：

```python
1013:        return (round(20.0 * period, 8), 0.0)
...
1023:        # The profile is a screening envelope, not a local IDF claim.
1024:        values.append(round(20.0 * period * shape, 8))
```

峰值强度 = `20.0 × 重现期` mm/h，即**与重现期线性成正比**。R-8 实测（`generate_design_rainfall`，`backend/.venv` Python 3.11.15）：

| T (yr) | peak mm/h | 24h total mm |
|---|---|---|
| 1 | 19.93 | 160.0 |
| 2 | 39.86 | 320.0 |
| 5 | 99.65 | 800.0 |
| 10 | 199.31 | 1600.0 |
| 20 | 398.61 | 3200.0 |
| **50** | **996.53** | **8000.0** |
| 100 | 1993.06 | 16000.0 |

24 小时 8000 mm 降雨、峰值约 1000 mm/h，超过地球有记录的最高日降雨量一个数量级以上；真实 IDF 关系下 50 年一遇 24h 深度约为 1 年一遇的 2～2.5 倍，而不是 50 倍（上海 50 年一遇 24h 约 300～400 mm）。

需要说明的是：AC-3 的验收测试并未走这条生成路径——`tests/acceptance/test_acceptance_matrix.py:114-142` 的 `_create_rainfall` 用 fixture 里 `intensity_pattern_mm_h = [0,2,5,8,12,16,20,16,12,8,5,2]`（峰值 20 mm/h，量级合理）直接提交 `points`。也就是说当前存在两条不一致的 50 年路径：生成器路径给出非物理量级，API 路径则把 `return_period_years=50` 仅仅当作**调用方自报 intensities 上的一个标签**，不由重现期反推强度。

**后果**：AC-3 的题面就是「50 年一遇」。以现有生成器驱动 solver 得到的水力结果不具解释性；这直接影响 required_observation #1 的成立。

**可修复**：改为查表式 IDF（如 `depth_mm = a*T^b`），并在 `RainfallSeries` 上加量级上界断言。

---

### F-6 ——【重大 · 可由代码修复】G5 solver benchmark 未真正绑定 4 物理核，且未报告峰值 RSS

R-2 本轮实跑产出的 `environment` / `resources` 块：

```json
"environment": {"cpu_model": null, "physical_cores": null, "ram_bytes": null,
                "logical_cpus": 32, "python_version": "3.11.15", ...}
"resources":  {"requested_cpus": 4, "requested_memory_bytes": 8589934592,
               "address_space_limit_applied": false,
               "affinity_before": null, "affinity_after": null,
               "pinned_environment": {"OMP_NUM_THREADS":"4","MKL_NUM_THREADS":"4", ...}}
```

- 宿主是 **32 逻辑核**，`physical_cores` 采集失败为 `null`。
- `affinity_before/after` 均为 `null`、`address_space_limit_applied=false`：`--cpus 4 --memory 8g` 只被记录为**请求值**，未被施加。
- 实际生效的只有 5 个线程环境变量（`OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=4`）。
- `peak_rss_bytes: null`（`measurements.peak_rss_bytes_max` 与每次 run 的 `peak_rss_bytes_after_run` 均为 null）。

`PRD_SUPPLEMENT.md:409-412` 要求「环境：Python 3.11、**4 个物理核**、无 GPU、BLAS/OMP 总线程 4」并「报告 P95、CPU time、**峰值 RSS**、分项时间、frame bytes、质量残差」。`IMPLEMENTATION_CONTRACT.md:2509` 同样写「目标环境：4 physical cores/8 GiB/无 GPU」。

**后果**：81.56 s 这个数字不能作为「4 核 ≤180 s」的证据；缺失的 RSS 报告也违反判据。

**可修复**：`configure_resources` 需要在支持的平台上真正设置 CPU affinity / 地址空间上限，并在无法设置时以非零退出（fail-closed）而不是照常记 PASS；补 `peak_rss` 采集。

---

### F-7 ——【重大】G5 benchmark 的降雨不是 50 年一遇

`backend/tests/fixtures/performance/solver/5k_10k_24h_v1/rainfall.json`（R-14）：

```json
{"intensity_model": "base + (peak - base) * exp(-((phase - 0.42) / 0.16) ** 2)",
 "peak_intensity_mm_h": 40.0, "sample_count": 289, "step_seconds": 300}
```

一个高斯包络，**没有 `return_period_years` 字段**，与 50 年无关。该 fixture 的 schema 是 `hydraulic-solver-benchmark-rainfall-v1`，服务于 G5 性能门禁。

**后果**：即便 F-1…F-4 全部修好，R-2 的 PASS 也只覆盖「24 h/289 帧/5000 节点/10000 管段」，不覆盖 AC-3 题面的「50 年一遇」。AC-3 仍缺一个 50 年输入的 solver+worker+落库+回放全链路用例。

---

### F-8 ——【重大】帧结果不可逐位复现

R-2 一次运行内 4 次 attempt 的 `frame_sha256` **互不相同**：

```
warmup  8fa8fc249c1cb86eb7e3e0c80206231cb0fc1f3e73a2d0e8bc4a555f35dabe89
meas.1  0a44ed8a78104eafd49b4b823f0fd8db641eb26789debfab0f6cfef9fcb55309
meas.2  04b2983ccd64e1d9afae0d092b612b9337af749c19172d91ed0139ce9414e2b2
meas.3  c20096e2f50b493765a6c451362a9bbbef75c4d504d267a8dd760e027e67ae13
```

`VALIDATION_EVIDENCE.md:157` 记录上一轮三次 run 同样互不相同（`…9e7e753b…` / `4143db54…` / `7228b025…`），并明确写「证据串未说明该差异是否被契约允许」。

AC-3 判据含「取消/重试/**回放可复现**」。同一 fixture、同一 seed 下结果 hash 漂移，说明求解或序列化存在未固定的非确定性来源（如迭代顺序/并发/字典序），重试后无法保证回放一致。

**可修复**：需定位非确定性来源（很可能是并发迭代顺序或浮点归约顺序）并固定；或在契约中显式定义「可复现」的容差并给出度量。

---

### F-9 ——【重大】时间轴回放的 e2e 证据是 2 帧 fixture，且不是 Playwright

- `frontend/e2e/fixtures/api-v1/baseline-frames.json`（R-13）→ `frame_count: 2`，`frames` 长度 2，`frame_index [0, 1]`，`frame_time 2026-09-24T00:00:00Z / 2026-09-24T00:05:00Z`。
- `frontend/e2e/fixtures/frontend-acceptance-v1.json` 中带 `timeline_playback` 的场景步骤为：点击 `simulation-playback-4x` → 对 `[data-testid="simulation-frame-timeline"]` 按 `End` → 断言 `data-frame-index == "1"`。**终点是第 1 帧，共 2 帧。**
- `frontend/e2e/run-ac-smoke.mjs:5-11` 自述：「Dependency-light AC end-to-end smoke … launches a pinned local Chrome/Chromium through the **DevTools Protocol**」。这是自研 CDP runner，不是 `PRD_SUPPLEMENT.md:588` 点名的 Playwright。

**后果**：AC-3 判据里的「Playwright timeline」没有对应实现；289 帧规模的前端回放只有 G5 渲染性能门禁覆盖（`frontend/performance/fixtures/twin-map-5k-10k-v1.json` 的 `frames.count: 289`，`run-twin-map.mjs:34` `const EXPECTED_FRAMES = 289`），而那是**本地 fixture API 合成数据**的 FPS/long-task/cache 渲染门禁，不是「真实 50 年 24h 仿真结果的时间轴回放」。

---

### F-10 ——【重大】worker E2E 用空 solver + 内存仓库，不涉及 PostgreSQL

`backend/tests/hydraulic/test_simulation_worker_entry.py:866`：

```python
async def test_service_claims_runs_and_completes_a_real_simulation_attempt(tmp_path):
867:    repository = InMemoryTaskRepository()
...
873:    worker = _build_worker(repository, tmp_path,
874:        solver_argv=[sys.executable, "-c", "pass"],
875:        worker_id="entry-complete-worker")
```

`"-c", "pass"` 是一个**立即返回 0 的空程序**。全文件 13 处 `_build_worker` 调用都用同一个空 solver（`:699,725,749,779,809,835,874,977,1001,1020,1051,1136,1156`），仓储一律是 `InMemoryTaskRepository`（`:27` import，`:697` 起共 13 处）。

R-6 实跑该文件 + `test_tasks_sandbox.py`：`79 passed, 1 skipped`。绿色，但覆盖的是控制循环语义，不是「24 h 规模真实 solver + PostgreSQL lease/fencing + kill 恢复」。

`VALIDATION_EVIDENCE.md:447` 另记：Windows 上的进程级 SIGTERM 优雅退出 `NOT VERIFIED`（`@pytest.mark.skipif(sys.platform == "win32")`）——本轮 R-6 的那 1 个 skip 即是此用例。

**后果**：required_observation #5（worker kill 恢复 PostgreSQL lease 并递增 fencing token）在 24h 规模上无任何证据。

---

## 4. 通过项（我本轮亲自跑出）

- **P-1 生产代码能生成 50 年 / 24 h / 5 min 设计暴雨并给出 289 点。** R-8：`return_period_years=50, duration_seconds=86400, step_seconds=300, point_count=289`。`rainfall.py:29` `ALLOWED_RETURN_PERIODS = (1,2,3,5,10,20,50,100)`；`:31-32` `MAX_DURATION_SECONDS = 72*3600`、`DEFAULT_DURATION_SECONDS = 24*3600`。**但量级见 F-5。**
- **P-2 24 h/5 min 的 289 帧栅格在提交路径上是真算的**：`hydraulic_routes.py:3249` `"frame_count": request.duration_seconds // request.frame_interval_seconds + 1`；R-5 的 `test_ac3_request_smoke_50_year_24_hour_preserves_289_frame_grid` 断言 `frame_count == 289` 且通过。
- **P-3 帧时间栅格严格 300 s。** `scripts/benchmark_hydraulic.py:449-455` 强制 `frame_time == frame_start + timedelta(minutes=5*index)`，不等即抛错。AC-3 判据「diff ≤300 s」在 benchmark 层被结构性保证（差异恒为 0）。
- **P-4 `py_mw` 24 h/289 帧真实求解在 180 s 内完成。** R-2：`worker_wall_time_max_seconds = 81.55557310000586`（4 次 attempt：70.58 / 80.43 / 81.14 / 81.56 s），`mass_balance_relative_residual = 0.0`，`quality_status: "verified"`，`frame_bytes = 115756`，3 次 measured 全部 `all_frames_committed/database_committed/published/quality_report_committed = true`（**后三个是硬编码常量，见 F-3**）。CPU time 69.75/79.5/79.70/80.31 s。
- **P-5 AC-3 命名的 3 个验收测试通过。** R-5：`3 passed, 15 deselected`。**但它们是请求元数据 smoke + 内存注入**，见下条。
- **P-6 前端时间轴单元测试通过。** R-7：`19 passed`（`timelinePlayer.test.ts` 16 + `timelineControl.test.tsx` 3）。`useTimelinePlayer.ts:3` 定义 1/2/4 倍速离散帧播放，`:30-49` 的 controller 暴露 `play/pause/seek/step/beginScrub/previewSeek/endScrub/setSpeed`。
- **P-7 前端到帧 API 的接线是真实的**：`frontend/src/features/simulation/api/client.ts:352-360` → `GET /simulations/{id}/frames/{frameIndex}`；`integration/HydraulicMapPanel.tsx:139` 调 `loadFrame(...)`；`integration/useHydraulicWorkspace.ts:306,367` 实现加载。**但后端该端点恒 409（F-1），故接线目前无法走通。**
- **P-8 payload 记录的 7 个 acceptance 失败是调用/解释器问题，不是真实代码失败。** `VALIDATION_EVIDENCE.md:209` 记录 `backend-acceptance-tests pytest` exit 1、7 failed，含 `test_acceptance_contract_runtime_is_python_311`（该测试本身断言 `sys.version_info[:2] == (3, 11)`，见 `tests/acceptance/test_acceptance_matrix.py:208-213`）。用仓库 venv（`backend/.venv/Scripts/python.exe --version` → `Python 3.11.15`）重跑同一目录，R-4 得到 `61 passed`。**该目录本身是绿的。**

---

## 5. 我没有跑的（不得读作通过）

以下 AC-3 判据中点名的检查，本轮**未执行**：

| 未跑项 | 原因 |
|---|---|
| G3 worker/numeric 门禁 | 本次 ask 是定向验收，不重复全量门禁 |
| G5 API / nowcast / 前端浏览器三套 benchmark | 同上；我只跑了 G5 四件套中的 solver 一件 |
| `e2e/ac-smoke.json`（`run-ac-smoke.mjs` 全量） | 需要先有真实 frame 数据源（F-1 未修）；本轮未执行 |
| G6 Compose（postgis / portable）、README 演示、备份恢复 | 未执行 |
| Playwright timeline | 仓库内**不存在** Playwright 依赖或配置；`frontend/e2e/run-ac-smoke.mjs` 是 CDP runner |
| 真实 EPA SWMM 二进制、`.out` 解析 | `VALIDATION_EVIDENCE.md:438` 记 `NOT RUN`；本轮未变 |
| 4 物理核环境下的任何重跑 | 本机 32 逻辑核，`physical_cores` 采集为 null（F-6） |
| PostgreSQL 真实 lease / fencing / attempt 耗尽 | 需要 live DSN；本轮未起库 |
| Windows 进程级 SIGTERM 优雅退出 | 平台 skip（R-6 的 1 skipped） |

---

## 6. 关于「文档自述」的核对

仓库自身的台账与 AC-3 相关的自述是**诚实且准确**的，未发现夸大：

- `matrix.json` AC-3 条目：`status: "NOT RUN"`、`pass_nodeids: []`，reason 写明「request metadata smoke plus in-memory frame and repository injection … were not run」——与我在代码中看到的事实完全一致（F-1、F-10）。
- `backend/tests/fixtures/hydraulic/acceptance/v1/rainfall_50y_24h.json` 自带 `"is_ac3_evidence": false` 与 `missing_evidence` 五项：real four-core solver execution / quality and conservation report / 289 persisted non-empty frame checksums / worker progress, cancel, retry, kill and cleanup / map timeline replay——与 F-3、F-5、F-6、F-9、F-10 一一对应。
- `tests/acceptance/test_acceptance_matrix.py:176` 的 docstring 主动声明注入结果「never worker evidence」。

**唯一发现文档/代码不一致的地方是 F-4**：冻结入口 `verify_hydraulic_stack.py` 的 AC→gate 映射比冻结需求表 `acceptance_requirements.json` 弱，AC-3 少 `g6`（其余 7 个 AC 也各自少 gate）。这是**代码缺陷**，不是文档错误。

---

## 7. 建议

**判定：AC-3 = `partial`；不可宣称 PASS；不建议进入 G3/G5/e2e/G6 的 AC-3 判定。**

修复顺序（全部可由代码修复，无一需要外部决策）：

1. **F-1**（最关键，其余验收都依赖它）：把 simulation worker 的 finalize 接到 `ResultRepository.write_frame/finalize`，并让 `/simulations/{id}/result|frames|frames/{i}` 改读 durable 仓储。移除 `tests/acceptance/test_acceptance_matrix.py` 的内存注入依赖，改为断言真实仓储写入。
2. **F-2**：solver CLI 流式帧回调按 `frame_index/total` 调 `repository.update_progress`；补一条「running 期间 progress 单调递增且 <100，completed 时 =100」的集成断言。
3. **F-3**：benchmark sink 改写真实 hypertable；把 `database_committed` / `quality_report_committed` / `published` 三个常量换成提交返回值；无法落库时 fail-closed 非零退出。
4. **F-4**：`verify_hydraulic_stack.py` 的 `ac_requirements` 改为从 `acceptance_requirements.json` 读取，消除脚本与需求表的双份真相。
5. **F-5**：`rainfall.py:1013/1024` 的 `20.0 * period` 换成查表式 IDF，并加量级上界断言。
6. **F-6**：`configure_resources` 真正设置 CPU affinity / 内存上限，失败即 fail-closed；补 `peak_rss` 采集。
7. **F-7**：新增一个 `50y_24h` 的 solver benchmark fixture（真实 50 年强度序列），使 G5 覆盖 AC-3 题面。
8. **F-8**：定位并消除帧 hash 漂移，或在契约中定义可复现容差。
9. **F-9 / F-10**：把 e2e 时间轴 fixture 从 2 帧扩到 289 帧并接真实后端；把 worker E2E 从 `InMemoryTaskRepository` + 空 solver 换到 PostgreSQL + 真实 py_mw。

完成 1–3 后可重跑 `verify_hydraulic_stack.py --gate acceptance --ac ac3`；4 修好后该命令才会真正把 G6 纳入 AC-3 判定。
