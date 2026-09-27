# AC-5 独立验收（rate 1）：率定量化改善与报告

- 验收日期：2026-09-28
- 验收人：独立验收员（只读；未修改任何实现或测试，仅新增本审计文件）
- 仓库：`smart-city-drainage`，`git rev-parse --short=8 HEAD` = `ca99e048`，`git status --porcelain --untracked-files=all | wc -l` = `729`（工作树非干净，结论对应当前工作树）
- 验收对象：AC-5「率定相对默认参数有量化改善并有报告」

## 0. 结论

**AC-5 = BLOCKED，且本轮 NOT RUN。未取得任何 PASS 证据。**

三重阻断，彼此独立：

1. **G3 门禁 BLOCKED**（我实际执行）：AC-5 唯一被授权的机器证据门禁返回 `status=BLOCKED`、`exit=2`，三个冻结 fixture 全部不存在。
2. **无生产可达的 `external_verified` 观测路径**：AC-5 的准入条件在代码里只能由调用方手工构造的 dataclass 字段满足；唯一的生产观测源（Influx）把 trust level 硬编码为 `external_unverified`。因此 AC-5 在结构上不可达，不只是"证据未采集"。
3. **率定搜索在评估集上自选最优（in-sample）**：契约要求 calibration/validation/test 三段切分并"预先指定 validation 或 test 集合"评分；实现的搜索目标函数只在被评估的那一段上计算，选择偏差未被任何代码路径阻断。

同时，`run_calibration` **没有任何生产调用方**——只有测试。API 的 `POST /api/calibrations` 写入进程内字典后永不变更状态。

诚实度方面，实现方**没有虚报**：`backend/tests/fixtures/hydraulic/acceptance/v1/calibration_improvement.json` 显式标注 `"evidence_state": "BLOCKED"`、`"is_ac5_evidence": false`；`VALIDATION_EVIDENCE.md:439` 把 AC-1～AC-8 全部记为 `NOT RUN`；`IMPLEMENTATION_CONTRACT.md:2551` 记 AC-5「BLOCKED（无真实 external observation/显式绑定）且 NOT RUN」。本审计不发现"文档勾选冒充实现"的造假，问题是**能力缺口**，不是**声明造假**。

## 1. 本轮实际执行的检查（含完整命令与真实输出）

### 1.1 G3 门禁 —— AC-5 的授权门禁

契约把 AC-5 映射到 `g3 + e2e`（`scripts/verify_hydraulic_stack.py:1709`），G3 的冻结 fixture 清单见 `scripts/verify_hydraulic_stack.py:104-108`。

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage" && backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py --gate g3 --evidence-dir ".runtime/audit-ac5/g3"
```

真实输出（截取关键字段）：

```json
{
 "status": "BLOCKED",
 "exit_code": 2,
 "gate": "g3",
 "commit": "ca99e0485fb12fbecbd565e79d81f745f8d4a748",
 "dirty_state": true,
 "steps": [{
   "name": "g3.fixtures", "exit_code": 2, "status": "BLOCKED", "duration_seconds": 0.0,
   "missing_fixtures": [
     "backend/tests/fixtures/hydraulic/workers/lifecycle-v1.json",
     "backend/tests/fixtures/hydraulic/calibration/durable-lifecycle-v1.json",
     "backend/tests/fixtures/hydraulic/hydraulics/conservation-stability-v1.json"
   ],
   "stderr_summary": ["missing frozen fixtures: ... 同上三项 ..."]
 }]
}
```

```text
HYDRAULIC_STACK_STATUS=BLOCKED
HYDRAULIC_STACK_EXIT=2
HYDRAULIC_STACK_ARTIFACT_SHA256=622683a53ebf4d51bfeaf1f5bd5ba4bf216125df0616d21a704f64744b93fb39
```

**结论：AC-5 所依赖的 G3 门禁本轮 BLOCKED。AC-5 自身状态只能是 BLOCKED / NOT RUN。**

### 1.2 率定服务单元测试

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && .venv/Scripts/python.exe -m pytest tests/hydraulic/test_results_calibration.py -q
17 passed, 1 warning in 0.17s
```

**通过。** 但这是阈值/守卫逻辑的单元测试，不是 AC-5 证据（见 §3 各项的"证据解读"）。

### 1.3 AC-5 验收矩阵测试

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && .venv/Scripts/python.exe -m pytest tests/acceptance/test_acceptance_matrix.py -q -k ac5
3 passed, 15 deselected, 1 warning in 0.49s
```

**通过。** 但这 3 个测试全部是**反向测试**：证明未验证/synthetic 观测**不能**通过 AC-5（`test_ac5_unverified_fixture_cannot_claim_eligibility`、`test_ac5_synthetic_observations_cannot_claim_ac5_eligibility`）以及预测实测比对返回 `not_ready`。它们是"防误报"证据，不是"AC-5 达成"证据。

### 1.4 定向诊断：切分泄漏、trust 字段、provenance

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && .venv/Scripts/python.exe /tmp/ac5_probe.py
```

脚本构造 6 个 `external_verified` 事件：`fit-0..fit-2`（预留给拟合）与 `val-0..val-2`（作为 validation 评估集），`split_manifest.evaluation_split="validation"`, `event_ids=["val-0","val-1","val-2"]`，网格搜索 5 个候选。真实输出：

```text
=== A. events supplied vs. events the search/scoring used ===
events supplied to run_calibration : ['fit-0', 'fit-1', 'fit-2', 'val-0', 'val-1', 'val-2']
events named in split_manifest     : ['val-0', 'val-1', 'val-2']
events the evaluator scored on     : ['val-0', 'val-1', 'val-2']
baseline observation_count         : 36 of 72
=> 'fit-*' events are dropped entirely: the search is fitted on the SAME
   validation events that AC-5 is then scored on. No held-out fitting split.

=== B. report verdict ===
  ac5_eligible                 = True
  improved                     = True
  diagnostic_numeric_improved  = True
  report_code                  = 'PASS'
  improvement = {"nse_delta": 0.2536231884057971, "peak_error_change_pct": -16.666666666666664,
                 "rmse_reduction_pct": 23.623738417402667,
                 "thresholds": {"no_quality_or_stability_failure": true, "nse_delta_gte_0_05": true,
                 "peak_lte_1_10_baseline": true, "rmse_calibrated_lte_0_95_baseline": true}}

=== C. ObservationPoint trust fields ===
  fields: ['observed_at', 'target_id', 'value_mm', 'event_id', 'trust_level', 'quality', 'measurement', 'field']
  auth_verified        present: False
  binding_verified     present: False
  trust_policy_id      present: False
  source_event_id      present: False
  ingest_event_id      present: False

=== D. provenance keys in an AC-5-eligible report ===
  ['baseline_parameter_sha256', 'evaluation_code_sha256', 'network_revision_id',
   'rainfall_scenario_id', 'solver_id', 'solver_version']
  candidate/calibrated revision field present: False
```

（第一次诊断尝试时用 4 个事件、evaluation 只含 1 个事件，直接被
`INSUFFICIENT_OBSERVATIONS: need at least 3 independent events, got 1` 拒绝——
说明 §9.1 的 3 事件门槛是**在评估段内**校验的，这一点本身符合契约，见 §3 AC-5-04。）

## 2. 契约要求（逐条引自冻结文档）

| 编号 | 要求 | 出处 |
|---|---|---|
| R1 | AC-5 率定相对默认参数有量化改善并有报告 | `SOURCE_PRD.md:33` |
| R2 | 至少 3 个独立降雨事件；整事件按时间划分 calibration/validation/test，推荐 60/20/20 或 70/15/15 | `PRD_SUPPLEMENT.md:458` |
| R3 | validation/test 至少 12 对有效观测；不足返回 `INSUFFICIENT_OBSERVATIONS` | `PRD_SUPPLEMENT.md:459` |
| R4 | 只有 `external_verified` 观测可使 AC-5 通过 | `PRD_SUPPLEMENT.md:460`；`IMPLEMENTATION_CONTRACT.md:872,895`；`PRD_SUPPLEMENT.md:590` |
| R5 | baseline/calibrated 同 network revision/rainfall/boundary/initial state/solver/evaluation code，只允许 parameter set 不同 | `PRD_SUPPLEMENT.md:461` |
| R6 | RMSE/NSE 公式；NSE 分母为 0 返回 `NSE_UNDEFINED`，MUST NOT 伪造 | `PRD_SUPPLEMENT.md:465-469` |
| R7 | 预先指定 validation 或 test 集合，并同时满足 4 项门槛：`NSE_delta>=0.05`、`RMSE_cal<=0.95*RMSE_base`、peak 恶化<=10%、无守恒/稳定性失败 | `PRD_SUPPLEMENT.md:473-479` |
| R8 | 报告保存 dataset/network/rainfall/solver/parameter/code hash 和事件切分 | `PRD_SUPPLEMENT.md:479` |
| R9 | 只有 `external_verified + auth_verified=true + binding_verified=true + quality good` 可用于 AC-5；trust 不得由 payload/source 自报 | `IMPLEMENTATION_CONTRACT.md:872,871` |
| R10 | `calibration_runs` / `calibration_reports` 表、原子 finalize、唯一 report、`INSUFFICIENT_OBSERVATIONS` terminal failed | `IMPLEMENTATION_CONTRACT.md:898-951` |
| R11 | G3 必交 `backend/tests/fixtures/hydraulic/calibration/durable-lifecycle-v1.json`，含 dataset/split hash、baseline/candidate simulation IDs、claim/retry/cancel/deadline 时间线、expected rows、metrics、report SHA-256/artifact URI | `IMPLEMENTATION_CONTRACT.md:951,2483,2493` |
| R12 | G3 门禁须验证 calibration 的 claim/finalize/terminal 机器证据；缺 fixture 即 G3 FAIL | `IMPLEMENTATION_CONTRACT.md:2493,2534` |
| R13 | 发布阻断：mock/synthetic 可满足 AC-5 即 MUST NOT 宣称完成 | `IMPLEMENTATION_CONTRACT.md:2563` |
| R14 | 发布阻断：trust level 由 payload/source 自报，或 `external_unverified` 可进入 AC-5 | `IMPLEMENTATION_CONTRACT.md:2590` |
| R15 | 四类 durable job（simulation/network-job/nowcast/**calibration**）共用同一状态机；calibration 是第四类 job | `IMPLEMENTATION_CONTRACT.md:1380,2493` |

## 3. 逐条验收发现

### AC-5-01 — G3 门禁 BLOCKED，AC-5 授权证据不存在

- **证据**：§1.1 实跑输出。`status=BLOCKED`, `exit_code=2`，缺失 `backend/tests/fixtures/hydraulic/calibration/durable-lifecycle-v1.json` 等三项。清单定义在 `scripts/verify_hydraulic_stack.py:104-108`；目录 `backend/tests/fixtures/hydraulic/` 下实际只有 `acceptance/ compose/ contracts/ imports/` 四个子目录，无 `calibration/`、无 `workers/`、无 `hydraulics/`。
- **缺口**：R11、R12 要求的 durable lifecycle fixture 与 G3 机器证据全部缺失。
- **可由代码修复**：**是**（补 fixture + 补 calibration durable job 实现与测试）。但补齐后仍受 AC-5-02/03/05 阻断——fixture 本身不能替代真实观测。

### AC-5-02 — 无生产可达的 `external_verified` 观测路径（AC-5 结构性不可达）

- **证据**：
  - 唯一的生产观测源 `InfluxWaterLevelObservationSource.fetch` 在 `backend/app/services/hydraulic/calibration.py:845-857` 构造 `ObservationPoint` 时**硬编码** `trust_level="external_unverified"`（第 855 行），模块 docstring 第 1-7 行亦声明这是有意为之。
  - `grep -rn "observation_ingest_events\|observation_samples" --include=*.py backend/app/` 只命中 `models/hydraulic_models.py`（模型定义）与 `main.py:75-76,898`（readiness 表存在性检查）——**没有任何服务读取这两张表来装配率定观测**。
  - `grep -rn "external_verified" --include=*.py backend/app/` 的全部命中为：模型枚举与约束（`hydraulic_models.py:248,3193,3196`）、`calibration.py` 的比较常量、`forecast_alerts.py:1662-1672`、`nowcast.py:33,917-919`。**没有一处是"从 ingest event 派生出 external_verified"的写入/装配代码**。
  - §1.4 输出 C：`ObservationPoint` 字段仅 `observed_at, target_id, value_mm, event_id, trust_level, quality, measurement, field` —— 无 `auth_verified`、无 `binding_verified`、无 `trust_policy_id`、无 `source_event_id`/`ingest_event_id`。
- **缺口**：R4/R9/R14 要求的准入链（真实来源 + 稳定 source event identity + 认证 + active binding + quality good）在率定服务里**没有承载字段，也没有加载路径**。`ac5_eligible` 判定（`calibration.py:688-696`）实际只看 `require_verified` 参数 + 调用方填的 `trust_level` 字符串。
- **可由代码修复**：**是**。需要新增从 `observation_ingest_events JOIN observation_samples` 读取的 repository，把 `trust_level/auth_verified/binding_verified/trust_policy_id/source_event_id` 带入 `ObservationPoint`，并让 `ac5_eligible` 从这些列派生而非从自报字符串派生。**但真实上游数据是否具备稳定 event identity 不是代码能解决的**——`calibration.py:834-844` 已指出 legacy Influx 无此身份，需先有可信上游。

### AC-5-03 — trust level 由调用方自报，诊断脚本即可拿到 `report_code='PASS'`

- **证据**：§1.4 的诊断脚本把**完全虚构的**数值（`50.0 + 10.0*(i%6)`）标为 `trust_level="external_verified"` 后，代码返回 `ac5_eligible=True`、`improved=True`、`report_code='PASS'`。§1.4 输出 B。
- **缺口**：直接命中 R14（`IMPLEMENTATION_CONTRACT.md:2590` "trust level 由 payload/source 自报"）与 R13（`:2563` mock/synthetic 可满足 AC-5）。**这是本轮最高危的发现**：`external_verified` 是 `_TRUST_LEVELS` frozenset 的成员之一，`ObservationPoint.__post_init__`（`calibration.py:101-125`）只校验取值合法性，不校验来源。
- **可由代码修复**：**部分**。见 AC-5-02——把 trust 判定下沉到数据层即可关闭自报路径。但"是否存在可信真实观测"是**数据/环境阻断，非代码可修复**。

### AC-5-04 — 评估段内事件数门槛正确（**通过项**）

- **证据**：`_validate_observation_admission`（`calibration.py:881-896`）在**过滤后的评估段**上校验 `event_count >= MIN_EVENT_COUNT(3)` 与 `len(observations) >= MIN_EVALUATION_PAIRS(12)`，常量定义在 `calibration.py:34-35`；`run_calibration` 另在 `calibration.py:597-601` 校验 baseline 配对数 `sample_count >= 12`。我的第一次诊断（evaluation 只含 1 事件）被 `INSUFFICIENT_OBSERVATIONS: need at least 3 independent events, got 1` 拒绝，即 R3 的行为证据。
- **缺口**：无。**通过。**
- **可由代码修复**：不适用。

### AC-5-05 — 率定搜索在评估集上自选最优（in-sample 选择偏差）

- **证据**：
  - `run_calibration` 先把观测过滤到评估段：`calibration.py:571-579`
    ```python
    _evaluation_events, split_payload = _evaluation_split_events(eligible, split_manifest)
    evaluation_observations = (tuple(item for item in eligible if item.event_id in _evaluation_events) if _evaluation_events else eligible)
    ```
  - 随后**全部候选**都在 `evaluation_observations` 上算目标函数：`calibration.py:608-633`，其中 `metrics = compute_metrics(evaluation_observations, evaluation.predictions, ...)`（第 615-620 行），`objective_value = objective.score(metrics)`（第 633 行）。
  - `best = min(usable, key=objective)`（`calibration.py:661-667`）——**在评估集上取 argmin**。
  - 随后 `_improvement_report(baseline_metrics, best.metrics, ...)`（`calibration.py:672-676`）在**同一集合**上算 NSE/RMSE/peak 改善，`improved = ac5_eligible and all_passed`（第 724 行）。
  - `CandidateEvaluator` 签名是 `Callable[[Mapping[str, float]], CandidateEvaluation]`（`calibration.py:445`），框架**不向 evaluator 传递拟合段**；且 `calibration.py:621-628` 强制候选预测必须与 baseline 配对到**同一 cohort**（`PAIRED_COHORT_MISMATCH`），因此任何候选的评分必然落在评估段。
  - §1.4 输出 A：传入 6 个事件（`fit-0..2` + `val-0..2`），`fit-*` 三事件被完全丢弃（`observation_count: 36 of 72`），搜索与评分同用 `val-*`。
- **缺口**：R2 要求"整事件按时间划分 calibration/validation/test"，R7 要求"预先指定 validation 或 test 集合"用于判定。实现的切分清单**只有一个 `evaluation_split`**（`calibration.py:992-997` 限定取值为 `validation|test`），**不存在独立的 calibration/拟合段**，搜索目标函数直接在受评段上最小化。因此"量化改善"是样本内选择后的数字，不是留出集上的泛化改善。这会让 §1.4 中 `nse_delta=0.2536` 这类数值系统性偏高。
- **可由代码修复**：**是**。需要把 `split_manifest` 扩为三段（如 `calibration_event_ids` + `evaluation_split` + `evaluation_event_ids`），用 calibration 段驱动 `evaluator` 选参，只用 evaluation 段算最终 `improvement`，并对两段做事件级不相交断言。**注意**：这是本轮**新发现**的问题——`docs/hydraulic-twin/reviews/result-calibration.md:182` 的 H1 关注的是 baseline/candidate 配对 cohort 不一致，该项已由 `PAIRED_COHORT_MISMATCH` 与 `test_ac5_rejects_candidate_paired_on_disjoint_events` 修复；**拟合段/评估段分离从未被提出，也未实现**。

### AC-5-06 — "同 network revision" 由自由文本断言，未绑定真实对象（R5）

- **证据**：
  - `_FORMAL_PROVENANCE_FIELDS`（`calibration.py:39-48`）只含 `network_revision_id, rainfall_scenario_id, solver_id, solver_version, baseline_parameter_sha256, evaluation_code_sha256`。
  - `_formal_provenance_complete`（`calibration.py:1130-1140`）只检查"键存在 + 非空"，其中两个 hash 字段用 `_SHA256_RE`（第 55 行）校验 64 位十六进制格式；其余四个字段**只校验非空字符串**。
  - §1.4 输出 D：一个 `ac5_eligible=True` 的报告中**没有 candidate/calibrated 侧的 revision 字段**——baseline 与 candidate 共用同一个 `network_revision_id` 字符串，无法证明两者跑在同一 revision 上。
  - `run_calibration` 不接收任何 `simulations` 行、不做 revision/rainfall/solver/initial-state 的等价核对（`calibration.py:533-548` 的签名可见）。
- **缺口**：R5 与 R8 的"保存 dataset/network/rainfall/solver/parameter/code hash"只做到"字段非空"；hash 是调用方自填的 64 位十六进制串，不是从真实对象计算。ORM 层其实**有**能力做这件事——`CalibrationRun` 已有复合 FK 把 baseline/candidate 绑定到同一 `network_revision_id`（`backend/app/models/hydraulic_models.py:1979-2000`，`fk_hydraulic_calibration_runs_baseline_revision` / `..._candidate_revision`），但服务层完全没用到。
- **可由代码修复**：**是**。让 `run_calibration` 接受 baseline/candidate 的 revision/rainfall/solver/initial-state 标识并交叉校验，或由 worker 在装配 `provenance` 时从 `simulations` 实际计算 hash。**前提**是先把 AC-5-08 的 durable 链路接上。

### AC-5-07 — 指标公式与三项数值门槛正确（**通过项**）

- **证据**：`compute_metrics`（`calibration.py:448-514`）实现 `RMSE = sqrt(mean((obs-sim)^2))`（第 476 行）与 `NSE = 1 - SSE/SST`（第 481-485 行），分母近 0 时 `nse=None` + `nse_code="NSE_UNDEFINED"`（第 508 行），未伪造 0/1。`_improvement_report`（`calibration.py:1087-1127`）的三个阈值：第 1110 行 `calibrated.rmse_mm <= RMSE_RATIO_THRESHOLD(0.95) * baseline.rmse_mm`、第 1111 行 `nse_delta >= NSE_DELTA_THRESHOLD(0.05)`、第 1112-1115 行 `peak <= PEAK_ERROR_RATIO_THRESHOLD(1.10) * baseline.peak`，常量见 `calibration.py:36-38`。单元测试 `test_nse_zero_denominator_is_reported_undefined_not_zero`（`backend/tests/hydraulic/test_results_calibration.py:580`）与 `test_verified_observations_meet_all_frozen_improvement_thresholds`（同文件 `:465`）通过（§1.2）。
- **缺口**：数值门槛本身无缺口。但 `quality_threshold_met`（`calibration.py:1116-1118`）= `require_verified and baseline.quality_ok and calibrated.quality_ok`，而 `quality_ok` 完全来自调用方在 `SimulatedWaterLevel.quality_status/quality_errors` 上的标注（`calibration.py:162-166`、`486-499`）——**率定服务内没有任何守恒/稳定性实算**。R7 第 4 项"无守恒/稳定性失败"因此是**转述**而非独立判定。
- **可由代码修复**：**部分**。判定逻辑可改为消费 `quality_report` 的既有产物；但"框架内实算守恒"是否必要，取决于 `hydraulics/conservation-stability-v1.json`（G3 fixture）落地后的分工——该 fixture 尚不存在（AC-5-01）。

### AC-5-08 — `run_calibration` 无生产调用方；API 是不会推进的进程内字典（**最高优先的工程缺口**）

- **证据**：
  - `grep -rn "run_calibration|compute_metrics|write_calibration_report" --include=*.py backend/ scripts/ | grep -v ".venv|__pycache__|/build/lib/"` 的全部命中：`calibration.py` 自身定义、`backend/tests/acceptance/test_acceptance_matrix.py:30,614,653`、`backend/tests/hydraulic/test_results_calibration.py:29-31,438,478,521,571-577,600,652,664,677`。**`backend/app/` 与 `scripts/` 下无任何生产调用点。**
  - `POST /api/calibrations`（`backend/app/api/hydraulic_routes.py:3643-3709`）构造 `item`，其中 `"status": "queued", "progress": 0, "metrics": {}, "improved": None, "report": None`（第 3691-3700 行），然后 `hydraulic_store.calibrations[item["id"]] = item`（第 3704 行）。
  - `hydraulic_store` 是进程内 `dict` 集合：`backend/app/api/hydraulic_routes.py:300-313`，`self.calibrations: dict[str, dict[str, Any]] = {}`（第 309 行），`self.durable_backend = False`（第 297 行）。
  - 全仓库对 `hydraulic_store.calibrations` 的**唯一**写入点就是第 3704 行（`grep -n "calibrations\[" backend/app/api/hydraulic_routes.py`）。**没有任何代码把 status 从 `queued` 推进到 completed**，`improved` 永远是 `None`。
  - `find . -iname "*calibration_worker*"` → 无结果。`backend/app/workers/` 只有 `inference_worker / network_job_worker / notification_worker / nowcast_worker / simulation_worker`。`docker-compose.yml` 的 worker 服务段（`:192`、`:279`、`:353`、`:444`、`:565`）无 calibration worker。
  - `backend/app/services/hydraulic/tasks.py:83,137,144,324` 存在 `TaskKind.calibration_run` 的**状态/退避定义**，但 `grep -n "calibration" backend/app/services/hydraulic/tasks.py` 仅这 4 行元数据，无 claim/consume/finalize 逻辑。
- **缺口**：R10、R15 要求的第四类 durable job 完全未实现。`calibration_runs`/`calibration_reports` 的 ORM 模型存在且相当完整（`backend/app/models/hydraulic_models.py:1949-2101`，含 `idempotency_record_id` 唯一约束、`fencing_token`、复合 FK 到 simulation+revision、`_durable_job_constraints` 的 retry CHECK），但**没有任何服务读写它**。`GET /api/calibrations/{id}` 永远返回 `status=queued, metrics={}, report=null`。
- **可由代码修复**：**是**，工作量最大的一项。需要：calibration worker、`calibration_runs` repository、原子 finalize（report + metrics + improved + status=completed + progress=100 同事务）、`INSUFFICIENT_OBSERVATIONS` terminal failed、`RESULT_PERSIST_FAILED` → retry_wait（最多两次）、deadline → timed_out + `SIMULATION_TIMEOUT`（R10）。

### AC-5-09 — 报告结构与可追溯性：结构达标，来源不达标（R8）

- **证据**：`CalibrationReport.to_dict()`（`calibration.py:411-435`）输出 `schema: "hydraulic-calibration-report-v1"`、`observation_dataset_sha256`、`split_manifest`（含 `split_sha256`，由 `calibration.py:1016-1018` 计算）、`paired_cohort_sha256`（`calibration.py:975-977`）、`provenance`、`best_parameter_set_sha256`（`calibration.py:716-718`）、`improvement`、`trials`、`report_code`。`content_sha256`（`calibration.py:441-442`）与 `canonical_json()`（`calibration.py:1165-1172`，`sort_keys=True, separators=(",",":"), allow_nan=False`）保证确定性——`test_random_search_is_reproducible_for_the_same_seed`（`test_results_calibration.py:556`）验证同 seed 同 hash，已通过。`write_calibration_report`（`calibration.py:730-742`）走 `write_controlled_json_artifact` 不可变工件，`test_calibration_report_writes_a_hashed_controlled_artifact`（`test_results_calibration.py:662`）已通过。
- **缺口**：报告**能**产出且可复现（这是本轮确认为**通过**的部分），但其 `provenance` 的六个字段中四个是自填非空字符串（见 AC-5-06），`observation_dataset_sha256` 只覆盖 `eligible`（含**全部**事件，`calibration.py:709` 调用点）而非实际参与评分的评估段——**报告声明的 dataset hash 与实际评分样本不是同一集合**。契约 §3.7 要求 report 行与 run 的 dataset/split/metrics 一致（`IMPLEMENTATION_CONTRACT.md:951`）；当前服务层无 run/report 实体可比对（AC-5-08）。
- **可由代码修复**：**是**。`observation_dataset_sha256` 应对 `evaluation_observations`（或同时记录 full + evaluation 两个 hash）计算。

### AC-5-10 — 现有 fail-closed 行为正确（**通过项，不应被误读为 AC-5 达成**）

- **证据**（均来自 §1.3 的 3 passed 及代码）：
  - `require_verified=False`（默认，`calibration.py:544`）时 `ac5_eligible` 必为 False（`calibration.py:688-696` 首项即 `require_verified and ...`）→ `report_code="INELIGIBLE_OBSERVATIONS"`、`improved=False`。`test_synthetic_grid_search_quantifies_improvement_but_never_claims_ac5`（`test_results_calibration.py:435`）覆盖。
  - `split_manifest=None` + `require_verified=True` 时 `_evaluation_events=()` → `bool(_evaluation_events)` 为 False → 同样 INELIGIBLE。这正是两个 acceptance 反向测试断言的路径。
  - 非 verified 运行时 `quality_threshold_met` 必为 False（`calibration.py:1116-1118`）→ `improved` 必为 False。
  - 候选 cohort 不一致抛 `CalibrationExecutionError`（`calibration.py:656-659`），`test_ac5_rejects_candidate_paired_on_disjoint_events`（`test_results_calibration.py:513`）覆盖。
- **缺口**：无。**通过。** 但请注意：这些守卫全部是"防止误报 PASS"，**没有任何一条能产生 PASS**。
- **可由代码修复**：不适用。

## 4. 汇总

| ID | 结论 | 是否代码可修复 | 阻断 AC-5 |
|---|---|---|---|
| AC-5-01 | G3 BLOCKED，fixture 缺失 | 是（工作量大） | 是 |
| AC-5-02 | 无生产可达 `external_verified` 路径 | 部分（真实数据需外部解决） | 是 |
| AC-5-03 | trust 自报，诊断脚本可拿 PASS | 部分（同上） | 是（最高危） |
| AC-5-04 | 评估段 3 事件 / 12 对门槛正确 | — | 否（通过） |
| AC-5-05 | 搜索在评估集上自选最优（in-sample） | 是 | 是 |
| AC-5-06 | revision/solver 等价性仅自由文本断言 | 是 | 是 |
| AC-5-07 | RMSE/NSE 公式与三门槛正确；守恒为转述 | 部分 | 否（数值层通过） |
| AC-5-08 | 无生产调用方 / 无 worker / API 状态不推进 | 是（工作量最大） | 是 |
| AC-5-09 | 报告结构与确定性达标；dataset hash 覆盖错集合 | 是 | 部分 |
| AC-5-10 | fail-closed 守卫正确 | — | 否（通过） |

**AC-5 状态：`BLOCKED` + `NOT RUN`。** 按 `PRD_SUPPLEMENT.md:580`，"静态阅读、`alembic heads`、Compose config、build 或 mock 测试不能替代对应 AC"；本轮全部结论均不构成 PASS。

## 5. 我本轮**没有**运行的项目（不得当作已验证）

- G0～G6 中除 g3 外的所有门禁；`--gate acceptance` 全量聚合。
- 全量 pytest / Vitest / npm ci / Black / isort / Flake8 / mypy / 前端 build（AC-7 范畴）。
- live PostgreSQL/TimescaleDB、live InfluxDB、live Docker Compose（G1/G6/AC-8 范畴）。
- `hydraulics/conservation-stability-v1.json` 相关数值稳定性基准（fixture 不存在，无从运行）。
- G5 四个性能 harness 与 `npm run test:perf:twin`。
- 任何 fixture 的 SHA-256 一致性核对（fixture 本身缺失）。
- `VALIDATION_EVIDENCE.md` 中标注 `[payload]` 的 16 项 final gates —— 该文件 §7 自己声明"我在本会话中没有复跑这些 pytest / vitest / compose / 性能命令"。**本审计不引用它们作为 AC-5 证据。**

## 6. 建议的最小修复顺序（供实现方参考，非验收结论）

1. **先封 AC-5-03 的自报路径**（否则任何后续工作都可能产出假 PASS）：`ObservationPoint` 增加 `auth_verified` / `binding_verified` / `trust_policy_id` / `source_event_id`，并新增一个从 `observation_ingest_events JOIN observation_samples` 读取的 repository；`ac5_eligible` 改由这些列派生。在此之前 `external_verified` 不应可由外部构造。
2. **修 AC-5-05 的 in-sample 选择**：`split_manifest` 扩为三段，搜索用 calibration 段、判定用 evaluation 段，断言两段事件不相交。
3. **接 AC-5-08 的 durable 链路**：calibration worker + `calibration_runs` repository + 原子 finalize，然后才有 `calibration/durable-lifecycle-v1.json` 的可写对象，也才能让 G3 从 BLOCKED 走出来。
4. **修 AC-5-06 / AC-5-09**：provenance 从 `simulations` 实际计算 hash 并交叉校验；`observation_dataset_sha256` 覆盖实际参与评分的样本集。
5. 以上完成且**真实 external_verified 观测数据到位**后，才谈 AC-5 的定量改善与报告。

---

### 附：本审计新增的文件

`docs/hydraulic-twin/audits/AC-5-round-1.md`（本文件）。除此之外**未修改任何实现、测试、契约或既有证据文档**。
