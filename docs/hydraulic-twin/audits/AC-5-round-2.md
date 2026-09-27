# AC-5 独立验收（round 2）：率定量化改善与报告

- 验收日期：2026-09-28
- 验收人：独立验收员（只读；未修改任何实现或测试，仅新增本审计文件）
- 仓库：`smart-city-drainage`，`git rev-parse --short=8 HEAD` = `ca99e048`（与 round-1 相同），`git status --porcelain --untracked-files=all | wc -l` = `806`（round-1 时为 `729`；工作树仍非干净，结论对应当前工作树）
- 验收对象：AC-5「率定相对默认参数有量化改善并有报告」（`SOURCE_PRD.md:33`）
- 前序：`docs/hydraulic-twin/audits/AC-5-round-1.md`（03:17 落盘）

## 0. 结论

**AC-5 仍为 BLOCKED，且本轮 NOT RUN。没有任何 PASS 证据。**

与 round-1 相比，实现方做了一处**真实且正确**的加固：新增 `TrustResolver` 钩子，`ac5_eligible` 现在要求调用方提供一个能对**每条**评估观测返回真的的旁证回调；不提供则一律 INELIGIBLE。round-1 中"零旁证也能拿到 `report_code='PASS'`"的路径已被关闭。

但**关闭得不彻底**：旁证本身仍由调用方自证。我构造的诊断脚本用一个回声式解析器（`lambda p: p.trust_level == "external_verified"`——只是把自报字符串读回来）配合**完全虚构的观测数值**，仍然拿到 `ac5_eligible=True` / `improved=True` / `report_code='PASS'`。仓库里唯一能产出 PASS 的测试用的正是这个回声式解析器（`backend/tests/hydraulic/test_results_calibration.py:489`）。

round-1 的其余 6 项阻断**逐条复现，无一修复**：

| 阻断 | round-1 | round-2 |
|---|---|---|
| AC-5-01 G3 门禁 | BLOCKED | **BLOCKED（同样 3 个 fixture 缺失）** |
| AC-5-02 无生产可达 `external_verified` 路径 | 阻断 | **阻断（无变化）** |
| AC-5-03 trust 自报可拿 PASS | 最高危 | **仍可拿 PASS（但需附一个自证解析器）** |
| AC-5-05 评估集内自选最优（in-sample） | 阻断 | **阻断（无变化）** |
| AC-5-06 provenance 仅自由文本断言 | 阻断 | **阻断（无变化）** |
| AC-5-08 无生产调用方 / API 不推进 | 最高优先工程缺口 | **阻断（无变化，另新增 503 死路）** |
| AC-5-09 dataset hash 覆盖错集合 | 部分 | **部分（无变化）** |

**诚实度：实现方没有虚报。** `backend/tests/fixtures/hydraulic/acceptance/v1/calibration_improvement.json` 仍标注 `"evidence_state": "BLOCKED"`、`"is_ac5_evidence": false`；`IMPLEMENTATION_CONTRACT.md:2551` 仍记 AC-5「BLOCKED（无真实 external observation/显式绑定）且 NOT RUN」。本轮同样**未发现"文档勾选冒充实现"的造假**；问题仍是能力缺口。

## 1. 本轮实际执行的检查（含完整命令与真实输出）

### 1.1 G3 门禁 —— AC-5 唯一被授权的机器证据门禁

AC-5 映射到 `g3 + e2e`；G3 的冻结 fixture 清单定义在 `scripts/verify_hydraulic_stack.py:104-108`（round-1 引用的行号在本轮重新核对，仍然一致）。契约把 G3 列为 AC-5 的必交证据（`IMPLEMENTATION_CONTRACT.md:951`、`:2493`、`:2534`；`PRD_SUPPLEMENT.md:590` 验收矩阵 AC-5 行）。

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage" && backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py --gate g3 --evidence-dir ".runtime/audit-ac5-r2/g3"
```

真实输出（截取关键字段）：

```json
{
 "status": "BLOCKED",
 "exit_code": 2,
 "gate": "g3",
 "commit": "ca99e0485fb12fbecbd565e79d81f745f8d4a748",
 "dirty_state": true,
 "run_id": "20260927T210617Z-ca99e048",
 "steps": [{
   "name": "g3.fixtures", "exit_code": 2, "status": "BLOCKED", "duration_seconds": 0.0,
   "missing_fixtures": [
     "backend/tests/fixtures/hydraulic/workers/lifecycle-v1.json",
     "backend/tests/fixtures/hydraulic/calibration/durable-lifecycle-v1.json",
     "backend/tests/fixtures/hydraulic/hydraulics/conservation-stability-v1.json"
   ],
   "stderr_summary": ["missing frozen fixtures: backend/tests/fixtures/hydraulic/workers/lifecycle-v1.json, backend/tests/fixtures/hydraulic/calibration/durable-lifecycle-v1.json, backend/tests/fixtures/hydraulic/hydraulics/conservation-stability-v1.json"]
 }]
}
```

```text
HYDRAULIC_STACK_STATUS=BLOCKED
HYDRAULIC_STACK_EXIT=2
HYDRAULIC_STACK_ARTIFACT_SHA256=9d43698029f6c46bb5f3f172ade7ec5b2dedb81c1f84c4da198f5cae6da1c38c
```

目录实况：`backend/tests/fixtures/hydraulic/` 下只有 `acceptance/ compose/ contracts/ imports/` 四个子目录，**无 `calibration/`、无 `workers/`、无 `hydraulics/`**（`find backend/tests/fixtures/hydraulic -type f` 实际输出 15 个文件，全部落在这四个目录）。

**结论：AC-5 所依赖的 G3 门禁本轮 BLOCKED。AC-5 自身状态只能是 BLOCKED / NOT RUN。**

### 1.2 率定服务单元测试

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && .venv/Scripts/python.exe -m pytest tests/hydraulic/test_results_calibration.py -q
19 passed, 1 warning in 0.19s
```

round-1 为 17 passed，本轮 19 passed——新增 2 个测试，**两个都是 fail-closed 方向**（见 §3 AC-5-03）。仍然没有新增任何能证明 AC-5 达成的测试。

### 1.3 AC-5 验收矩阵测试

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && .venv/Scripts/python.exe -m pytest tests/acceptance/test_acceptance_matrix.py -q -k ac5
3 passed, 15 deselected, 1 warning in 1.45s
```

**通过。** 三个测试全部是**反向/就绪度**证据：`test_ac5_unverified_fixture_cannot_claim_eligibility`（`test_acceptance_matrix.py:606`）、`test_ac5_synthetic_observations_cannot_claim_ac5_eligibility`（`:628`，断言抛 `INSUFFICIENT_OBSERVATIONS`）、`test_ac5_observed_comparison_requires_real_verified_pairs`（`:668`，断言 `status == "not_ready"`、`observation_count == 0`）。它们证明"未验证/synthetic 观测**不能**通过 AC-5"，是防误报证据，不是达成证据。

### 1.4 定向诊断脚本（只读；导入 app 代码，不改仓库）

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && .venv/Scripts/python.exe /tmp/ac5_r2_probe.py
```

脚本构造 6 个事件共 36 条**完全虚构**的观测（数值 `50.0 + 10.0*(i%6) + 0.37*e`，无任何上游来源），`split_manifest` 指定 `evaluation_split="validation"`、`event_ids=["event-3","event-4","event-5"]`，provenance 六个字段填自造值。真实输出：

```text
=== A. Can a caller still fabricate a PASS? (tautological resolver) ===
  ac5_eligible = True
  improved      = True
  report_code   = PASS
  improvement   = {"rmse_reduction_pct": 100.0, "nse_delta": 0.3427498915196592,
                   "peak_error_change_pct": -100.0,
                   "thresholds": {"rmse_calibrated_lte_0_95_baseline": true,
                                  "nse_delta_gte_0_05": true,
                                  "peak_lte_1_10_baseline": true,
                                  "no_quality_or_stability_failure": true}}

=== B. No resolver at all (fail-closed default) ===
  ac5_eligible = False | report_code = INELIGIBLE_OBSERVATIONS

=== C. Is there a held-out fitting split? (6 events in, only 3 scored) ===
  events supplied          : ['event-0', 'event-1', 'event-2', 'event-3', 'event-4', 'event-5']
  events named in split   : ['event-3','event-4','event-5']
  baseline sample_count    : 18 of 36
  events that survived fit : ['event-3', 'event-4', 'event-5']
  -> events 0,1,2 are dropped; search + scoring share the same 3 events

=== D. observation_dataset_sha256 coverage ===
  full supplied = 36 | actually scored = 18
  split event_ids recorded = ['event-3', 'event-4', 'event-5']
  -> dataset hash is computed over `eligible` (all 6), not the 3 scored

=== E. ObservationPoint trust-bearing fields ===
  fields: ['observed_at', 'target_id', 'value_mm', 'event_id', 'trust_level', 'quality', 'measurement', 'field']
   auth_verified      present: False
   binding_verified   present: False
   trust_policy_id    present: False
   source_event_id    present: False
   ingest_event_id    present: False

=== F. Report provenance keys ===
  ['baseline_parameter_sha256', 'evaluation_code_sha256', 'network_revision_id',
   'rainfall_scenario_id', 'solver_id', 'solver_version']
  candidate/calibrated side revision field present: False
```

### 1.5 定向诊断：calibration API 是否推进状态

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && .venv/Scripts/python.exe /tmp/ac5_api_probe.py
after submit : {'status': 'queued', 'progress': 0, 'metrics': {}, 'improved': None, 'report': None}
durable_backend flag: False
```

```text
$ grep -n "calibrations\[" backend/app/api/hydraulic_routes.py
3785:    hydraulic_store.calibrations[item["id"]] = item
```

`calibrations[` 在整个 routes 文件里**只有第 3785 行一处写入**。没有任何代码把 `status` 从 `queued` 推进到 `completed`，`improved` 永远是 `None`。

## 2. 契约要求（逐条引自冻结文档）

| 编号 | 要求 | 出处 |
|---|---|---|
| R1 | AC-5 率定相对默认参数有量化改善并有报告 | `SOURCE_PRD.md:33` |
| R2 | 至少 3 个独立降雨事件；整事件按时间划分 calibration/validation/test，推荐 60/20/20 或 70/15/15 | `PRD_SUPPLEMENT.md:458` |
| R3 | validation/test 至少 12 对有效观测；不足返回 `INSUFFICIENT_OBSERVATIONS` | `PRD_SUPPLEMENT.md:459` |
| R4 | 只有 `external_verified` 观测可使 AC-5 通过 | `PRD_SUPPLEMENT.md:460`；`IMPLEMENTATION_CONTRACT.md:872,895` |
| R5 | baseline/calibrated 同 network revision/rainfall/boundary/initial state/solver/evaluation code，只允许 parameter set 不同 | `PRD_SUPPLEMENT.md:461` |
| R6 | RMSE/NSE 公式；NSE 分母为 0 返回 `NSE_UNDEFINED`，MUST NOT 伪造 | `PRD_SUPPLEMENT.md:465-469` |
| R7 | 预先指定 validation 或 test 集合，并同时满足 4 项门槛：`NSE_delta>=0.05`、`RMSE_cal<=0.95*RMSE_base`、peak 恶化<=10%、无守恒/稳定性失败 | `PRD_SUPPLEMENT.md:473-479` |
| R8 | 报告保存 dataset/network/rainfall/solver/parameter/code hash 和事件切分 | `PRD_SUPPLEMENT.md:479` |
| R9 | 只有 `external_verified + auth_verified=true + binding_verified=true + quality good` 可用于 AC-5；**trust 不得由 payload/source 自报** | `IMPLEMENTATION_CONTRACT.md:872,871` |
| R10 | `calibration_runs` / `calibration_reports` 表、原子 finalize、唯一 report、`INSUFFICIENT_OBSERVATIONS` terminal failed | `IMPLEMENTATION_CONTRACT.md:898-951` |
| R11 | G3 必交 `backend/tests/fixtures/hydraulic/calibration/durable-lifecycle-v1.json`，含 dataset/split hash、baseline/candidate simulation IDs、claim/retry/cancel/deadline 时间线、expected rows、metrics、report SHA-256/artifact URI | `IMPLEMENTATION_CONTRACT.md:951,2483,2493` |
| R12 | G3 门禁须验证 calibration 的 claim/finalize/terminal 机器证据；缺 fixture 即 G3 FAIL | `IMPLEMENTATION_CONTRACT.md:2493,2534` |
| R13 | 发布阻断：mock/synthetic 可满足 AC-5 即 MUST NOT 宣称完成 | `IMPLEMENTATION_CONTRACT.md:2563`（发布阻断第 4 条） |
| R14 | 发布阻断：trust level 由 payload/source 自报，或 `external_unverified` 可进入 AC-5 | `IMPLEMENTATION_CONTRACT.md:2590`（发布阻断第 31 条） |
| R15 | 四类 durable job（simulation/network-job/nowcast/**calibration**）共用同一状态机 | `IMPLEMENTATION_CONTRACT.md:1380,2493`（发布阻断第 28 条） |

## 3. 逐条验收发现

### AC-5-00 — round-1 之后确有改动（背景事实，非独立发现）

- **证据**：`ls -la --time-style=full-iso` 显示 `backend/app/services/hydraulic/calibration.py` mtime = `2026-09-28 04:43:10`，`backend/tests/hydraulic/test_results_calibration.py` = `04:44:34`，`backend/app/api/hydraulic_routes.py` = `04:52:51`，`scripts/verify_hydraulic_stack.py` = `04:41:21`，`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md` = `04:58:13`；而 round-1 审计文件落盘于 `03:17:05`。`git status --porcelain --untracked-files=all | wc -l` 由 round-1 的 `729` 变为本轮的 `806`。HEAD 未变（`ca99e048`），故全部实现仍在工作树中、未提交。
- **说明**：本轮因此**逐条重验**了 round-1 的每项发现，而不是照抄 round-1 结论。
- **缺口 / 可修复**：不适用。

### AC-5-01 — G3 门禁仍 BLOCKED，AC-5 授权证据不存在 —— **未修复**

- **证据**：§1.1 实跑输出。`status=BLOCKED`、`exit_code=2`，缺 `backend/tests/fixtures/hydraulic/calibration/durable-lifecycle-v1.json` 等三项，与 round-1 **完全相同**。清单定义 `scripts/verify_hydraulic_stack.py:104-108`；fixture 目录实况见 §1.1。
- **缺口**：R11、R12 要求的 durable lifecycle fixture 与 G3 机器证据全部缺失。round-1 后没有任何新增 fixture 文件。
- **可由代码修复**：**是**（补 fixture + calibration durable job 实现与测试），但见 §3 AC-5-08——目前连可供 fixture 记录的对象都不存在；且补齐后仍受 AC-5-02/03/05 阻断，fixture 本身不能替代真实观测。

### AC-5-02 — 无生产可达的 `external_verified` 观测路径 —— **未修复**

- **证据**（本轮重跑的三条命令）：
  ```text
  $ grep -rn "run_calibration" --include=*.py backend/app scripts/ backend/tests | grep -v ".venv|__pycache__|build/lib"
  backend/app/services/hydraulic/calibration.py:536:def run_calibration(
  backend/app/services/hydraulic/calibration.py:1221:    "run_calibration",
  backend/tests/acceptance/test_acceptance_matrix.py:30,614,653
  backend/tests/hydraulic/test_results_calibration.py:30,438,478,523,573,574,666,736,775
  ```
  → `backend/app/` 与 `scripts/` 下**仍然没有任何生产调用点**（与 round-1 相同）。

  ```text
  $ grep -rn "observation_ingest_events|ObservationIngestEvent|observation_samples|ObservationSample" --include=*.py backend/app
  ```
  命中仍只有：`backend/app/main.py:75,76`（readiness 表存在性检查）、`:898`（表清单）、`backend/app/models/hydraulic_models.py`（ORM 定义：`:3235 ObservationIngestEvent`、`:3317 ObservationSample`）、`backend/app/models/__init__.py:43,47,106,107`（再导出）、`backend/app/services/hydraulic/nowcast.py:540`（`ObservationSample = SensorReading`，是**另一个同名类型**，与 ORM 的 `ObservationSample` 无关）。**没有任何服务读这两张表来装配率定观测。**

  §1.4 输出 E：`ObservationPoint` 字段仍是 `observed_at, target_id, value_mm, event_id, trust_level, quality, measurement, field`——**无 `auth_verified`、无 `binding_verified`、无 `trust_policy_id`、无 `source_event_id`/`ingest_event_id`**。
- **缺口**：R4/R9/R14 要求的准入链（真实来源 + 稳定 source event identity + 认证 + active binding + quality good）在率定服务里**仍无承载字段，也仍无加载路径**。`calibration.py:1-7` 的模块 docstring 仍声明 Influx 点是 `external_unverified`。
- **可由代码修复**：**是**（新增 repository 把 `trust_level/auth_verified/binding_verified/trust_policy_id/source_event_id` 从 ingest 表带入 `ObservationPoint`）。**但真实上游是否具备稳定 event identity 不是代码能解决的**——`calibration.py` 的 docstring 已指出 legacy Influx 无此身份。

### AC-5-03 — trust 自报仍可拿 PASS；本轮新增的 `TrustResolver` 是**正确的方向但不充分**

这是本轮**唯一有实质变化的发现**，故拆成"已改善"与"仍不足"两半。

#### (a) 已改善：零旁证不再能拿到 PASS

- **证据**：
  - 新增类型别名 `TrustResolver = Callable[["ObservationPoint"], bool]`，注释明写"A caller-supplied service that corroborates an observation's trust claim against the observation ingest/binding records"（`backend/app/services/hydraulic/calibration.py:55`）。
  - `run_calibration` 新增参数 `trust_resolver: TrustResolver | None = None`（`calibration.py:552`），docstring（`:555-563`）明确 "`trust_level` on an `ObservationPoint` is a *claim* made by whoever built the point, so it cannot by itself make a report AC-5 eligible… With no resolver the report is INELIGIBLE, never PASS."
  - `ac5_eligible` 现在要求 `trust_resolver is not None`（`calibration.py:702`）且对**每条**评估观测 `trust_resolver(item)` 为真（`calibration.py:706-712`）。
  - §1.4 输出 B 证实：不传解析器时 `ac5_eligible=False`、`report_code=INELIGIBLE_OBSERVATIONS`。
  - 新增两个 fail-closed 测试：`test_a_self_declared_trust_level_alone_cannot_reach_pass`（`test_results_calibration.py:722`，显式注释 `// deliberately no trust_resolver`，断言 INELIGIBLE）、`test_a_resolver_that_rejects_an_observation_also_blocks_pass`（`:767`，解析器对 `event-2` 返回 False，断言 INELIGIBLE）。二者均在本轮 §1.2 的 19 passed 内。
  - **对比 round-1**：round-1 §1.4 输出的 B 段是在**不传任何旁证**的情况下拿到 `report_code='PASS'`。该路径已被关闭。**这是真实的、可验证的进展。**
- **缺口**：无。**此子项通过。**
- **可由代码修复**：不适用。

#### (b) 仍不足：旁证本身可由调用方自证，PASS 仍可伪造

- **证据**：
  - `TrustResolver` 是一个**任意 Python 可调用对象**，框架不校验其实现，也无从校验。`ac5_eligible` 只检查 `trust_resolver(item)` 的返回值（`calibration.py:709`）。
  - §1.4 输出 A：`trust_resolver=lambda p: p.trust_level == "external_verified"`（**纯回声**——把自报字符串读回来，不查任何外部记录）配合**完全虚构**的观测，得到 `ac5_eligible=True` / `improved=True` / `report_code='PASS'`，`nse_delta=0.3427`、`rmse_reduction_pct=100.0`。
  - 仓库里**唯一能产出 PASS 的测试用的就是这个回声式解析器**：`test_results_calibration.py:489` → `trust_resolver=lambda point: point.trust_level == "external_verified"`，注释写"# The trust claim is only half the evidence; a resolver must confirm it."——但该 lambda 并没有 confirm 任何东西。
  - `backend/app/` 与 `scripts/` 下**不存在任何生产用 `TrustResolver` 实现**（§1.4 之外的 `grep -rn "trust_resolver|TrustResolver"` 全仓库命中：定义 4 处 + 测试 3 处，**无 app 生产实现**）。
- **缺口**：直接命中 R9（"payload/source 不得自报 trust_level"）与 R14 / `IMPLEMENTATION_CONTRACT.md:2590`（发布阻断第 31 条）以及 R13 / `:2563`（发布阻断第 4 条）。加固把"必须提供旁证"变成了硬性 API 契约，但**没有把旁证变成数据层事实**。契约要求的 `auth_verified / binding_verified / trust_policy_id / quality good` 仍只能由构造 `ObservationPoint` 的调用方以字符串/布尔量形式声称。
- **可由代码修复**：**部分**。见 AC-5-02——把 trust 判定下沉到 `observation_ingest_events` / `observation_samples` 的真实列上（即让 `ObservationPoint` 携带这些列、让 `ac5_eligible` 从列派生而非从字符串派生）即可关闭自证路径。**"是否存在可信真实观测"是数据/环境阻断，非代码可修复。**

### AC-5-04 — 评估段内事件数门槛正确 —— **通过（本轮复验）**

- **证据**：`_validate_observation_admission`（`calibration.py:897`，调用点 `:592`）在**过滤后的评估段**上校验 `event_count >= MIN_EVENT_COUNT(3)` 与 `len(observations) >= MIN_EVALUATION_PAIRS(12)`，常量在 `calibration.py:34-35`。§1.4 输出 C 显示传入 6 个事件、split 只留 3 个事件时基线配对数为 `18 of 36`（≥12 通过），而 §1.3 的 `test_ac5_synthetic_observations_cannot_claim_ac5_eligibility`（`test_acceptance_matrix.py:628`）在 split 不足时被 `INSUFFICIENT_OBSERVATIONS` 拒绝——即 R3 的行为证据。
- **缺口**：无。**通过。**
- **可由代码修复**：不适用。

### AC-5-05 — 率定搜索在评估集上自选最优（in-sample） —— **未修复**

- **证据**：
  - `split_manifest` 仍只有**单一** `evaluation_split`，取值域仍是 `{validation, test}`：`calibration.py:1009-1012`（`if split_name not in {"validation", "test"}` 抛 `INVALID_SPLIT_MANIFEST`）。**不存在独立的 calibration/拟合段字段。**
  - `run_calibration` 仍先把观测过滤到评估段（`calibration.py:583-590`），再让**全部候选**在这同一段上算目标函数（`calibration.py:627-631` 的 `metrics = compute_metrics(evaluation_observations, evaluation.predictions, ...)`），并 `best = min(usable, key=objective)`，随后 `_improvement_report` 在**同一集合**上算改善。
  - §1.4 输出 C：传入 6 个事件、`event_ids` 只列 `event-3..5`，`events 0,1,2` 被**完全丢弃**（`sample_count: 18 of 36`），搜索与评分共用 `event-3,4,5`。
- **缺口**：R2 要求"整事件按时间划分 calibration/validation/test"；R7 要求"预先指定 validation 或 test 集合"用于判定。实现只有一段受评集，搜索目标函数直接在其上最小化 → 报告里的"量化改善"是**样本内选择后的数字**，不是留出集上的泛化改善，会系统性偏高（§1.4 的 `nse_delta=0.3427` 即典型）。
- **可由代码修复**：**是**。把 `split_manifest` 扩为三段（`calibration_event_ids` + `evaluation_split` + `evaluation_event_ids`），用 calibration 段驱动 `evaluator` 选参，只用 evaluation 段算最终 `improvement`，并断言两段事件不相交。

### AC-5-06 — "同 network revision" 仍由自由文本断言 —— **未修复**

- **证据**：
  - §1.4 输出 F：AC-5-eligible 报告的 `provenance` 键仍只有 `network_revision_id, rainfall_scenario_id, solver_id, solver_version, baseline_parameter_sha256, evaluation_code_sha256`，**无 candidate/calibrated 侧的 revision 字段**。
  - `_FORMAL_PROVENANCE_FIELDS`（`calibration.py:39-48`）未变；两个 hash 字段用 `_SHA256_RE`（`calibration.py:58`）校验 64 位十六进制，其余四个字段**只校验非空字符串**。我填的 `rainfall_scenario_id` 是 `"00000000-0000-0000-0000-000000000001"` 这样**在任何表里都不存在**的串，照样通过。
  - `run_calibration` 仍不接收任何 `simulations` 行，不做 revision/rainfall/solver/initial-state 的等价核对。
- **缺口**：R5/R8 的"保存 hash"只做到"字段非空"；hash 是调用方自填，不是从真实对象计算。ORM 层**有**能力做这件事——`CalibrationRun` 已有复合 FK 把 baseline/candidate 绑定到同一 `network_revision_id`（`backend/app/models/hydraulic_models.py:1984` `fk_hydraulic_calibration_runs_baseline_revision` / `:1993` `..._candidate_revision`），但服务层完全没用到（无任何 repository，见 AC-5-08）。
- **可由代码修复**：**是**（让 `run_calibration` 接收两侧 revision/rainfall/solver/initial-state 标识并交叉校验，或由 worker 在装配 `provenance` 时从 `simulations` 实际计算）。前提是先接上 AC-5-08 的 durable 链路。

### AC-5-07 — 指标公式与三项数值门槛正确 —— **通过（数值层）**

- **证据**：`compute_metrics` 实现 `RMSE = sqrt(mean((obs-sim)^2))` 与 `NSE = 1 - SSE/SST`，分母近 0 时 `nse=None` + `nse_code="NSE_UNDEFINED"`，未伪造 0/1。`test_nse_zero_denominator_is_reported_undefined_not_zero`（`test_results_calibration.py:582`）与 `test_verified_observations_meet_all_frozen_improvement_thresholds`（`:465`）在本轮 §1.2 的 19 passed 内。§1.4 输出 A 的 `thresholds` 四项在虚构数据上全为 `true`，说明门槛判定逻辑本身工作正常。
- **缺口**：数值门槛本身无缺口。遗留项（与 round-1 相同）：`quality_threshold_met` 等于 `require_verified and baseline.quality_ok and calibrated.quality_ok`，而 `quality_ok` 来自调用方在 `SimulatedWaterLevel.quality_status/quality_errors` 上的标注——**率定服务内没有任何守恒/稳定性实算**，R7 第 4 项"无守恒/稳定性失败"是**转述**而非独立判定。该 fixture（`hydraulics/conservation-stability-v1.json`）尚不存在（AC-5-01），分工未定。
- **可由代码修复**：**部分**（判定逻辑可改为消费 `quality_report` 的既有产物）。

### AC-5-08 — `run_calibration` 无生产调用方；API 是不会推进的进程内字典 —— **未修复，并新增一条 503 死路**

- **证据**：
  - `run_calibration` 的调用点见 AC-5-02 的 grep：`backend/app/` 与 `scripts/` 下**零命中**。
  - `POST /api/calibrations`（`backend/app/api/hydraulic_routes.py:3724`）构造 `item`，含 `"status": "queued", "progress": 0, "metrics": {}, "improved": None, "report": None`（`:3772-3781`），然后 `hydraulic_store.calibrations[item["id"]] = item`（`:3785`）。`grep -n "calibrations\[" backend/app/api/hydraulic_routes.py` 只有这一行写入。
  - `hydraulic_store.calibrations` 是进程内 dict：`hydraulic_routes.py:323`；同处 `self.durable_backend = False`（`:311`）。
  - §1.5 实跑：提交后 `{'status': 'queued', 'progress': 0, 'metrics': {}, 'improved': None, 'report': None}`，`durable_backend flag: False`。
  - **本轮新增**：`submit_calibration` 顶部加了 `_require_durable_api_store()`（`hydraulic_routes.py:3736`），该守卫在 `DEPLOYMENT_ENVIRONMENT == "production"` 且 `durable_backend` 为假时抛 503 `DURABLE_STORE_UNAVAILABLE`（`hydraulic_routes.py:341-348`）。但 `grep -rn "durable_backend" --include=*.py backend/app` 的**全部**命中是 `hydraulic_routes.py:311`（置 False）、`:343`（读，默认 False）、`main.py:1273`（读）——**全仓库没有任何地方把 `durable_backend` 置为 True**。
  - `backend/app/workers/` 目录实况：`__init__.py durable_runtime.py inference_worker.py network_job_worker.py notification_worker.py nowcast_materializer.py nowcast_worker.py outbox_relay.py simulation_worker.py`——**无 calibration worker**。`docker-compose.yml` 的 worker 服务段无 calibration worker。
  - `backend/app/services/hydraulic/tasks.py` 中 `TaskKind.calibration_run` 仍只有 4 行元数据（`:83` 定义、`:137` retry 白名单 `{LEASE_EXPIRED, RESULT_PERSIST_FAILED}`、`:144` `TaskLane.independent`、`:324` backoff），**无 claim/consume/finalize 逻辑**。
  - ORM 侧 `CalibrationRun`（`hydraulic_models.py:1949`，含 `uq_..._idempotency`、`uq_..._id_fencing`、`ck_..._retry` 等约束）与 `CalibrationReport`（`:2107`）确实存在且相当完整，但**没有任何服务读写它们**。
- **缺口**：R10、R15 要求的第四类 durable job 完全未实现。净效果是**两条路都走不通**：生产环境下 `POST /api/calibrations` 直接 503（因为 `durable_backend` 永远为 False 且无处开启）；非生产环境下写入进程内 dict 后状态永不推进。`GET /api/calibrations/{id}` 永远返回 `status=queued, metrics={}, report=null`。
- **可由代码修复**：**是**，工作量最大的一项：calibration worker、`calibration_runs` repository、原子 finalize（report + metrics + improved + status=completed + progress=100 同事务）、`INSUFFICIENT_OBSERVATIONS` terminal failed、`RESULT_PERSIST_FAILED` → retry_wait（最多两次）、deadline → timed_out + `SIMULATION_TIMEOUT`。

### AC-5-09 — 报告结构与可追溯性：结构达标，来源与覆盖仍不达标 —— **未修复**

- **证据**（通过部分，本轮复验）：`CalibrationReport.to_dict()` 输出的结构（`schema`、`observation_dataset_sha256`、`split_manifest`（含 `split_sha256`）、`paired_cohort_sha256`、`provenance`、`best_parameter_set_sha256`、`improvement`、`trials`、`report_code`）未变；`content_sha256` 基于 `canonical_json()`（`sort_keys=True, separators=(",",":"), allow_nan=False`）保证确定性，`test_random_search_is_reproducible_for_the_same_seed`（`test_results_calibration.py:558`）与 `test_calibration_report_writes_a_hashed_controlled_artifact`（`:664`）在本轮 19 passed 内。
- **缺口**（未修复部分）：
  - `provenance` 六个字段中四个是自填非空字符串（AC-5-06，§1.4 输出 F）。
  - `observation_dataset_sha256=_observation_dataset_sha256(eligible)`（`calibration.py:725`）——`eligible` 是**全部**事件（36 条），而实际参与评分的只有评估段（18 条）。§1.4 输出 D 证实。**报告声明的 dataset hash 与实际评分样本不是同一集合**，与 R8「报告保存 dataset … hash 和事件切分」的可追溯意图相悖。
  - 契约 `IMPLEMENTATION_CONTRACT.md:951` 要求 completed transaction 验证唯一 report 行的 dataset/split/metrics 与 run 一致；当前服务层无 run/report 实体可比对（AC-5-08）。
- **可由代码修复**：**是**。`observation_dataset_sha256` 应对 `evaluation_observations` 计算（或同时记录 full + evaluation 两个 hash）。

### AC-5-10 — 现有 fail-closed 行为正确 —— **通过，不应被误读为 AC-5 达成**

- **证据**（本轮实跑）：§1.4 输出 B（无解析器 → INELIGIBLE）；§1.3 的三个 acceptance 反向测试（`test_acceptance_matrix.py:606,628,668`）全部通过；§1.2 中 `test_a_self_declared_trust_level_alone_cannot_reach_pass`（`:722`）与 `test_a_resolver_that_rejects_an_observation_also_blocks_pass`（`:767`）通过。
- **缺口**：无。**通过。** 但请注意：这些守卫**全部是"防止误报 PASS"**；结合 §1.4 输出 A 可知，**没有任何一条能独立阻止伪造 PASS**——因为解析器可由调用方自证。
- **可由代码修复**：不适用。

## 4. 汇总

| ID | 结论 | 相比 round-1 | 是否代码可修复 | 阻断 AC-5 |
|---|---|---|---|---|
| AC-5-00 | round-1 后确有工作树改动，已逐条重验 | 新增（背景） | — | 否 |
| AC-5-01 | G3 BLOCKED，3 个 fixture 仍缺 | **未变** | 是 | 是 |
| AC-5-02 | 无生产可达 `external_verified` 路径 | **未变** | 部分（真实数据需外部解决） | 是 |
| AC-5-03a | 零旁证不再能 PASS（新增 `TrustResolver` 硬性要求） | **已改善** | — | 否（通过） |
| AC-5-03b | 旁证可自证，虚构数据仍可拿 PASS | 部分缓解，仍阻断 | 部分 | 是 |
| AC-5-04 | 评估段 3 事件 / 12 对门槛正确 | 未变 | — | 否（通过） |
| AC-5-05 | 搜索在评估集上自选最优（in-sample） | **未变** | 是 | 是 |
| AC-5-06 | revision/solver 等价性仅自由文本断言 | **未变** | 是 | 是 |
| AC-5-07 | RMSE/NSE 公式与三门槛正确；守恒为转述 | 未变 | 部分 | 否（数值层通过） |
| AC-5-08 | 无生产调用方 / 无 worker / API 不推进 / 生产 503 | **未变（更差：新增 503 死路）** | 是（工作量最大） | 是 |
| AC-5-09 | 报告结构与确定性达标；dataset hash 覆盖错集合 | **未变** | 是 | 部分 |
| AC-5-10 | fail-closed 守卫正确 | 增强（+2 测试） | — | 否（通过） |

**AC-5 状态：`BLOCKED` + `NOT RUN`。** 按 `PRD_SUPPLEMENT.md:580`，"静态阅读、`alembic heads`、Compose config、build 或 mock 测试不能替代对应 AC"；本轮全部结论均不构成 PASS。

## 5. 我本轮**没有**运行的项目（不得当作已验证）

- G0～G6 中除 g3 外的所有门禁；`--gate acceptance` 全量聚合。
- 全量 pytest / Vitest / npm ci / Black / isort / Flake8 / mypy / 前端 build（AC-7 范畴）。我只跑了 `tests/hydraulic/test_results_calibration.py` 与 `tests/acceptance/test_acceptance_matrix.py -k ac5` 两个文件，**不等于 AC-7 的门禁**。
- live PostgreSQL/TimescaleDB、live InfluxDB、live Docker Compose（G1/G6/AC-8 范畴）。
- `hydraulics/conservation-stability-v1.json` 相关数值稳定性基准（fixture 不存在，无从运行）。
- G5 四个性能 harness 与 `npm run test:perf:twin`。
- 任何 fixture 的 SHA-256 一致性核对（fixture 本身缺失）。
- `POST /api/calibrations` 的**真实 HTTP 端到端**（我只用探针脚本复现了 route 内的字典写入，未起服务、未带 JWT/Idempotency-Key 实际 POST）。因此"生产环境返回 503"是**由 `_require_durable_api_store` 代码路径与 `durable_backend` 恒为 False 推出的**，非实测响应。
- `VALIDATION_EVIDENCE.md` 中标注 `[payload]` 的各项——该文件 mtime 为 `2026-09-28 03:09:10`，早于 round-1，本轮**未修改**；其 §5（`:437-441`）只把 AC-1–AC-8 记为 solver-runtime 阶段的 `NOT RUN`，**不含 AC-5 独立状态行**。AC-5 的当前状态以 `IMPLEMENTATION_CONTRACT.md:2551` 为准。**本审计不引用 `VALIDATION_EVIDENCE.md` 作为 AC-5 证据。**

## 6. 建议的最小修复顺序（供实现方参考，非验收结论）

1. **继续封 AC-5-03b**：新增从 `observation_ingest_events JOIN observation_samples` 读取的 repository，把 `auth_verified` / `binding_verified` / `trust_policy_id` / `source_event_id` / `quality` 作为**列**带入 `ObservationPoint`，`ac5_eligible` 从这些列派生；把 `test_results_calibration.py:489` 的回声式 lambda 换成真正的记录查询。在那之前，`TrustResolver` 只是把自报从"隐式"挪到了"显式"。
2. **修 AC-5-05 的 in-sample 选择**：`split_manifest` 扩为三段，搜索用 calibration 段、判定用 evaluation 段，断言两段事件不相交。
3. **接 AC-5-08 的 durable 链路**：calibration worker + `calibration_runs` repository + 原子 finalize + 真正开启 `durable_backend`；然后才有 `calibration/durable-lifecycle-v1.json` 的可写对象，也才能让 G3 从 BLOCKED 走出来。
4. **修 AC-5-06 / AC-5-09**：provenance 从 `simulations` 实际计算 hash 并交叉校验两侧 revision；`observation_dataset_sha256` 覆盖实际参与评分的样本集。
5. 以上完成且**真实 external_verified 观测数据到位**后，才谈 AC-5 的定量改善与报告。

---

### 附：本审计新增的文件

`docs/hydraulic-twin/audits/AC-5-round-2.md`（本文件）与两份临时探针（`/tmp/ac5_r2_probe.py`、`/tmp/ac5_api_probe.py`，仓库外，不入库）。除此之外**未修改任何实现、测试、契约或既有证据文档**。
