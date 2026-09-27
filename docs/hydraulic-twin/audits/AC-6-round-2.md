# AC-6 独立验收（round 2）：A/B 情景关键指标差异

- 验收日期：2026-09-28
- 验收人：独立验收员（只读；未修改任何实现/测试/文档，仅新增本审计文件，以及门禁脚本自身在 `.runtime/ac6r2/` 下产生的运行产物）
- 仓库：`smart-city-drainage`
- `git rev-parse --short=8 HEAD` = `ca99e048`
- `git status --porcelain | wc -l` = `160`（工作树非干净；本报告结论对应**当前工作树**）
- 验收对象：AC-6「A/B 指标差异可查」

## 0. 结论

**AC-6 = `partial`（能力未通过）。契约门禁 `BLOCKED / exit=2`，AC 状态 = `NOT RUN`。本轮未取得任何 PASS 证据。**

与 round 1（`AC-6-round-1.md`）相比：

- **两个阻断项（round 1 §2.2、§2.5）均未修复**，我用本轮实跑的只读诊断再次复现（§1.5）。
- **门禁本身被加强了**：AC-6 的必需门禁从 round 1 记录的 `("e2e",)` 变为 **`("g3", "e2e", "g6")`**，且来源改为冻结 fixture。**round 1 §1.1 对该映射的引用是错的，本轮予以更正（§2.0）。**
- **诚实度进一步改善**：冻结台账 `matrix.json` 现已显式写 `"status": "NOT RUN"`、`"pass_nodeids": []`，并给出准确 reason；`operations.md` 亦记录 `ab_comparisons` 0 行。**没有任何虚报。**
- **新增一项三方口径冲突**（round 1 未发现）：冻结 fixture 要求 rainfall 参与匹配，`PRD_SUPPLEMENT.md` 明确允许 rainfall 不同，实现两者都不比较（§2.9）。

**一句话**：路由形状、delta 计算、零基线、409 语义都对；但 A/B 的**数据源根本没接上真实结果发布链**（`result` 在生产中恒为 `None`），**比较结果也不落库**。AC-6 的成功路径在生产中不可达，且不可仅靠补证据转为 PASS。

---

## 1. 本轮实际执行的检查（完整命令与真实输出）

### 1.1 契约冻结入口：`acceptance --ac ac6` → `BLOCKED / exit=2`

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage" && mkdir -p .runtime/ac6r2 && backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py --gate acceptance --ac ac6 --evidence-dir ".runtime/ac6r2/acceptance" > ".runtime/ac6r2/acceptance-run.log" 2>&1; echo "REAL_EXITCODE=$?"
REAL_EXITCODE=2
```

真实输出（截取自 `.runtime/ac6r2/acceptance-run.log`）：

```json
{"acceptance": [{"ac": "ac6",
  "artifact_paths": [],
  "missing_or_invalid_artifacts": [
    "g3", "e2e", "g6",
    "missing artifact: ...\\.runtime\\ac6r2\\g3\\result.json",
    "missing artifact: ...\\.runtime\\ac6r2\\e2e\\ac-smoke.json",
    "missing artifact: ...\\.runtime\\ac6r2\\g6\\postgis\\result.json",
    "missing artifact: ...\\.runtime\\ac6r2\\g6\\portable\\result.json"],
  "required_gates": ["g3", "e2e", "g6"],
  "status": "BLOCKED"}],
 "commit": "ca99e0485fb12fbecbd565e79d81f745f8d4a748",
 "dirty_state": true,
 "environment": {"node_version": "v25.9.0", "npm_version": "11.12.1", "python_version": "3.11.15", "git_dirty": true},
 "exit_code": 2,
 "run_id": "20260927T210905Z-ca99e048",
 "schema": "hydraulic-acceptance-report-v1",
 "status": "BLOCKED",
 "utc_timestamp": "2026-09-27T21:09:06.513276Z"}

HYDRAULIC_STACK_STATUS=BLOCKED
HYDRAULIC_STACK_EXIT=2
HYDRAULIC_STACK_RESULT=...\runtime\ac6r2\acceptance\result.json
HYDRAULIC_STACK_ARTIFACT_SHA256=19d05ae6bcdc9f04b300e06334f8772751f7e4db77cc039fd95851e7f39dd731
```

Node 实际为 `v25.9.0`，仓库冻结 `.node-version` = `18.20.8`（`VALIDATION_EVIDENCE.md:1.1` 同一条环境事实）。**这不是 AC-6 的合规运行证据。**

### 1.2 后端 A/B 定向测试 —— 三个 AC-6 用例全绿

```text
$ backend/.venv/Scripts/python.exe -m pytest backend/tests/acceptance/test_acceptance_matrix.py -k "ac6" -q
...                                                                      [100%]
3 passed, 15 deselected, 1 warning in 0.58s
```

整文件：

```text
$ backend/.venv/Scripts/python.exe -m pytest backend/tests/acceptance/test_acceptance_matrix.py -q
18 passed, 1 warning in 4.13s
```

### 1.3 前端 A/B 定向测试 —— 全绿

```text
$ cd frontend && npx vitest run src/features/simulation/integration/comparisonApi.test.ts src/features/simulation/test/chart-comparison.test.tsx
 ✓ src/features/simulation/integration/comparisonApi.test.ts (1 test) 3ms
 ✓ src/features/simulation/test/chart-comparison.test.tsx (10 tests) 84ms
 Test Files  2 passed (2)
      Tests  11 passed (11)
```

### 1.4 静态检索：三个关键事实

```text
# (a) metric_units 在生产代码中只有读取方，没有写入方
$ grep -rn "metric_units" --include=*.py . | grep -v "/build/lib/" | grep -v "/tests/"
./backend/app/api/hydraulic_routes.py:3641:    baseline_units = (baseline.get("result") or {}).get("metric_units", {})
./backend/app/api/hydraulic_routes.py:3642:    candidate_units = (candidate.get("result") or {}).get("metric_units", {})

# 全仓（backend/app、frontend/src、scripts）无任何 metric catalog
$ grep -rn "metric_catalog|METRIC_CATALOG|better_direction" --include=*.py backend/app/
（仅 hydraulic_routes.py:3643,3644,3647,3652,3653,3671 —— 全是 compare 内部的读取/校验/透传）

# (b) ab_comparisons 只出现在 migration / ORM / readyz / schema 测试
$ grep -rn "AbComparison" --include=*.py . | grep -v "/build/lib/"
backend/app/models/hydraulic_models.py:3366  (class AbComparison)
backend/app/models/__init__.py:17, :104        (导出)
backend/tests/test_hydraulic_schema_readyz_tables.py:696
（无任何 route / service / repository 命中）

$ ls backend/app/services/hydraulic/
benchmark.py calibration.py exporters forecast_alerts.py geometry.py importers metrics.py
nowcast.py rainfall.py results.py risk.py sandbox.py seed.py solvers spatial_repository.py
surface_flood.py tasks.py topology.py
（无 comparison 模块；$ grep -rln "AbComparison" backend/app/services/ → 无输出）
```

### 1.5 只读诊断：两条生产可达路径的 compare 结果

诊断脚本写在**仓库外** `C:\Users\zyq\AppData\Local\Temp\ac6r2_diag.py`，不新增仓库文件。它按真实生产 HTTP API 创建 network / rainfall / 两个 simulation，然后分两种模式调 compare。

```text
$ cd backend && ./.venv/Scripts/python.exe "C:/Users/zyq/AppData/Local/Temp/ac6r2_diag.py"
NETWORK_CREATE 201
RAINFALL_CREATE 201
SIM_SUBMIT a 202
SIM_SUBMIT b 202
PRODUCTION_SHAPE_NO_RESULT status= 409 code= VALIDATION_ERROR detail= simulation has not published a result
FINALIZE_PUBLISHED_SHAPE status= 409 code= COMPARISON_INCOMPATIBLE detail= simulation metrics have incompatible units or directions
RESULT_KEYS= ['frame_manifest', 'metrics', 'quality_report', 'summary']
HAS_metric_units= False
HAS_better_directions= False
STORE_COMPARISON_ATTRS= []
```

两种模式：

- **Mode 1（任何现有生产路径能产出的形状）**：完全不做注入，直接 compare → `409 VALIDATION_ERROR "simulation has not published a result"`。
- **Mode 2（`ResultRepository.finalize` 真正持久化的形状）**：只把 `summary` / `quality_report` / `frame_manifest` / `metrics` 放进 result（这正是 `results.py:690-696` `published_summary` 的构成）→ **仍然** `409 COMPARISON_INCOMPATIBLE`。

**这两条是本报告最关键的证据：AC-6 的成功路径在生产中不可达，且失败点有两层。**

---

## 2. 逐条验收：证据 / 缺口 / 是否可由代码修复

### 2.0 更正 round 1 §1.1：AC-6 必需门禁是 `g3/e2e/g6`，不是 `e2e`

**round 1 的说法**：「AC-6 只依赖 `e2e` 一个 artifact（`scripts/verify_hydraulic_stack.py:1710`：`"ac6": ("e2e",)`）」。**本轮实测为假。**

**证据**：

- `scripts/verify_hydraulic_stack.py` 中 `grep -n '"ac6"\|ac6'` 只命中 `:1941`（`--ac` 的默认值 `"ac1,...,ac8"`），**没有任何硬编码的 `ac6` 映射**。
- 映射的唯一真源是冻结 fixture：`scripts/verify_hydraulic_stack.py:1749-1758` 的 `acceptance_requirements()`，其 docstring 明写：

  > "The AC->gate mapping has exactly one source of truth: the frozen `acceptance_requirements.json`. **A second hardcoded copy in this script silently weakened every gate it omitted**, which let an AC be judged PASS while a required artifact (most often G6's two profile runs) was absent."

- `backend/tests/fixtures/hydraulic/acceptance/v1/acceptance_requirements.json:105-112`：

  ```json
  "AC-6": {
    "required_gates": ["g3", "e2e", "g6"],
    "required_artifact_suffixes": ["g3/result.json", "e2e/ac-smoke.json",
                                   "g6/postgis/result.json", "g6/portable/result.json"],
  ```

- 本轮门禁输出 `required_gates: ["g3","e2e","g6"]`（§1.1），与 fixture 一致。

**结论**：round 1 引用的 `:1710` 行号与内容均已不成立（该脚本已被重构）。**门禁被加强了，不是被削弱了。** 本报告以本轮实测为准。这不改变 AC-6 结论——`e2e`、`g3`、`g6` 三个 artifact 一个都不存在，状态仍为 `BLOCKED`。

**可由代码修复**：不适用（属文档更正）。

---

### 2.1 兼容性与 409 语义 —— **通过（代码层）**

**要求**：`PRD_SUPPLEMENT.md:485-486`「baseline/candidate 必须同 `network_revision_id + target_manifest_id/hash`、同 start/end/frame interval/frame count。rainfall、solver、parameter set 可不同；不同 topology/time grid 返回 409 `COMPARISON_INCOMPATIBLE`」。

**证据**：`backend/app/api/hydraulic_routes.py:3617-3633` 逐字段比较

```python
compatible = all(
    baseline[field] == candidate[field]
    for field in ("network_revision_id", "target_manifest_id",
                  "target_manifest_sha256", "start_at",
                  "duration_seconds", "frame_interval_seconds")
)
if not compatible:
    raise _error(409, "COMPARISON_INCOMPATIBLE",
                 "simulations do not share a compatible frame grid")
```

`frame_count` 未直接比较，但由 `_frame_count()`（`:3404-3407`，`duration_seconds // frame_interval_seconds + 1`）推导，其三个输入项均已比较，故 frame count 恒等——**推导成立**。测试：`test_acceptance_matrix.py:726-742`（改 `duration_seconds=300` → 409），本轮通过（§1.2）。

**缺口（沿用 round 1，未修复）**：比较的是「请求侧时间网格参数」，**不是已发布帧网格**。若某 simulation 实际发布帧数与参数不符（staging 不完整/被裁剪），compare 不感知。

**可由代码修复**：是（把 `len(published_frames)` 或 published_attempt 的 frame_count 纳入比较）。

---

### 2.2 `metric_units` / `better_directions` 无任何生产生产者 —— **阻断（高，未修复）**

**要求**：`PRD_SUPPLEMENT.md:487-488`「返回 baseline、candidate、absolute delta、relative delta、unit、better direction；baseline 为 0 时 relative=null」；冻结 fixture `acceptance_requirements.json:116`「every compared metric has explicit units and a frozen better direction」。

**证据（读取方）**：`backend/app/api/hydraulic_routes.py:3641-3659`

```python
baseline_units = (baseline.get("result") or {}).get("metric_units", {})
candidate_units = (candidate.get("result") or {}).get("metric_units", {})
baseline_directions = (baseline.get("result") or {}).get("better_directions", {})
candidate_directions = (candidate.get("result") or {}).get("better_directions", {})
for key in sorted(set(baseline_metrics) & set(candidate_metrics)):
    unit = baseline_units.get(key)
    better_direction = baseline_directions.get(key)
    if (not isinstance(unit, str) or not unit.strip()
        or candidate_units.get(key) != unit
        or better_direction not in {"lower", "higher"}
        or candidate_directions.get(key) != better_direction):
        raise _error(409, "COMPARISON_INCOMPATIBLE",
                     "simulation metrics have incompatible units or directions")
```

**证据（写入方不存在）**：§1.4(a)。`metric_units` 在生产代码中**只有这两行读取**；`better_directions` 只有 compare 内部的读取/校验/透传。全仓无 metric catalog。

**证据（真实发布链也不产生）**：`backend/app/services/hydraulic/results.py:690-696`

```python
published_summary = {**dict(summary), "artifact_index": index.to_dict()}
```

`finalize()` 原样透传 worker 给的 `summary` + `artifact_index`，**不追加任何 `metric_units` / `better_directions`**。`provisional_index`（`:658-670`）也只有 frame/summary/quality 的 sha256。

**诊断证据**：§1.5 Mode 2 → `FINALIZE_PUBLISHED_SHAPE status=409 code=COMPARISON_INCOMPATIBLE`，`HAS_metric_units= False`、`HAS_better_directions= False`。

**唯一生产者仍是测试助手**：`backend/tests/acceptance/test_acceptance_matrix.py:175-206` 的 `_inject_in_memory_result()`，其 docstring 自述 **"Inject an in-memory diagnostic result; this is never worker evidence."**；该文件模块 docstring（`:1-7`）亦自述 "refuses to treat an in-memory result as AC evidence"。

**缺口**：AC-6 的 happy path 在生产不可达。当前实现选择了最严格的 fail-closed，**语义上没错，但前置条件永远不成立**。

**可由代码修复**：**是**。需要 (a) 定义版本化 metric catalog（key → unit / better_direction），(b) 在 `ResultRepository.finalize` 写 summary 时按 catalog 预计算并写入，(c) 补一条真实 worker 路径的测试。

---

### 2.3 差异可持久化 / 可追溯 —— **阻断（高，未修复；且本轮发现更严重）**

**要求**：`SOURCE_PRD.md:33`「**AC-6 A/B 指标差异可查**」；契约表 `IMPLEMENTATION_CONTRACT.md:953-966` 定义 `hydraulic.ab_comparisons`（含 `metrics JSONB NOT NULL`、`comparison_sha256 CHAR(64) NOT NULL`、`created_by`、`UNIQUE(baseline_simulation_id,candidate_simulation_id,frame_grid)`）；冻结 fixture `acceptance_requirements.json:119`「comparison remains reproducible after API and worker restart」。

**已具备**（与 round 1 相同）：

- migration `backend/alembic/versions/0007_hydraulic_ab_comparisons.py:51-98`（含 `uq_hydraulic_ab_comparisons_grid` 与三个 FK）
- ORM `backend/app/models/hydraulic_models.py:3366-3414`
- `backend/app/main.py:63` 把它列入 `/readyz` 必查表

**缺口一：没有任何代码写这张表。** §1.4(b)：全仓 `AbComparison` 只出现在 migration / ORM 导出 / schema 测试断言中；`backend/app/services/hydraulic/` 下**没有 comparison 模块**，也没有任何 repository 引用它。`compare_simulations`（`:3605-3692`）每次请求现算现返回，**不落库、不写 `comparison_sha256`、无列表/历史查询端点**（`grep -n "comparisons" backend/app/api/hydraulic_routes.py` 无输出）。诊断佐证：`STORE_COMPARISON_ATTRS= []`。

`docs/hydraulic-twin/operations.md:45` 亦独立记录 `ab_comparisons` **0 行**（2026-09-28 实测运行中的库）。

**缺口二（本轮新增、比 round 1 更严重）：durable 结果根本没有回流到 compare 的数据源。**

`compare_simulations` 只读 `hydraulic_store.simulations[...]`（`:3611-3612`）。而：

- 提交时 `hydraulic_store.simulations[item["id"]] = item`，其中 **`"result": None`**（`:3344`）、**`"published_frames": {}`**（`:3346`）。
- `grep -rn --include=*.py -E '\["result"\]\s*=|\["published_frames"\]\s*=' backend/app/`（排除 `build/lib`）→ **无任何写入方**。
- `_require_completed`（`:3395-3401`）要求 `status == "completed"` **且** `published_attempt_id` **且** `result` 非空，三者在生产中永不同时成立。

**后果**：`ResultRepository.finalize` 把结果写进 Postgres 后，**没有任何代码把它读回内存 store**。所以 AC-6 的数据源在生产中既不来自 DB、也不来自 worker，只来自测试注入。诊断 Mode 1 正是这条：`409 VALIDATION_ERROR "simulation has not published a result"`。

叠加 `hydraulic_store.durable_backend = False`（`:311`）且全仓无处置 `True`（唯一读取方是 `backend/app/main.py:1273` 的 production 503 闸门），这与 `PRD_SUPPLEMENT.md:105`「MUST NOT 以 …进程内变量… 表示任务/告警完成状态」冲突。

**可由代码修复**：**是**，但工作量不小：(a) 建立 durable 读路径，把已发布 attempt 的 result/frames 接入 compare；(b) 把 compare 结果持久化到 `ab_comparisons`（`comparison_sha256` = 规范化 `frame_grid + metrics` 的 SHA-256）；(c) 增加 `GET /api/ab-comparisons` 列表/详情；(d) 补「重启后仍可复现」的 e2e。

---

### 2.4 `absolute_delta` / `relative_delta` / 零基线 —— **通过（代码层）**

**证据**：`backend/app/api/hydraulic_routes.py:3660-3673` 在后端现算，不落库也不由前端猜测：

```python
delta = candidate_value - baseline_value
result_metrics.append({
    "key": key, "unit": unit,
    "baseline": baseline_value, "candidate": candidate_value,
    "absolute_delta": delta,
    "relative_delta": delta / baseline_value if baseline_value else None,
    "better_direction": better_direction,
})
```

测试 `test_ac6_in_memory_comparison_returns_deltas_and_rejects_incompatible_grid` 断言 `absolute_delta == -5.0`、`relative_delta == -0.5`、`better_direction == "lower"`（`test_acceptance_matrix.py:721-724`）；`test_ac6_in_memory_comparison_handles_zero_baseline` 断言 `absolute_delta == 1.0` 且 `relative_delta is None`（`:778-779`）。二者本轮均通过（§1.2）。

**缺口（低）**：`if baseline_value` 用真值判断，`-0.0` 也落 `None`（等价，可接受）；`baseline_value` 为负时 `relative_delta` 符号语义未在契约中定义（`PRD_SUPPLEMENT.md:487` 只规定「baseline 为 0 时 relative=null」）。属口径未定义，非缺陷。

**附带发现（低）**：`metrics` 查询参数在**单位/方向校验循环之后**才过滤（`:3674-3676`）。因此 `?metrics=nonexistent` 返回 `200` + 空 `metrics` 数组，而非 422。契约未规定，属口径未定义。

**可由代码修复**：口径澄清即可，非阻断。

---

### 2.5 响应顶层字段 —— **通过（代码层）**

**要求**：`IMPLEMENTATION_CONTRACT.md:1953`「comparison 顶层必须返回 `compatible/network_revision_id/target_manifest_id/target_manifest_sha256/frame_grid/baseline/candidate/metrics`」。

**证据**：`backend/app/api/hydraulic_routes.py:3677-3692` 全部返回，`schema_version="simulation-comparison-v2"`，`frame_grid` 含 `start_at/end_at/interval_seconds/frame_count`，与契约 §11.6 一致。前端 `frontend/src/features/simulation/model/normalizers.ts:960-983` 逐字段强校验（`unit` 走 `requiredString` 收窄为非空、`better_direction` 只接受 `null | 'lower' | 'higher'`）。

**缺口（沿用 round 1）**：`compatible` 在 200 响应中恒为 `true`（`:3679` 硬编码；不兼容一律 409）。因此前端为 `compatible === false` 准备的分支不可达，见 §2.7。

**可由代码修复**：是（后端可选返回 `compatible:false + reason`，或前端把 409 映射到该分支）。

---

### 2.6 浏览器不重采样 / 不猜方向 —— **基本通过，一处需澄清（与 round 1 同）**

**要求**：`PRD_SUPPLEMENT.md:488`「指标由后端预计算并可逐项重算；浏览器不重采样、不猜测优劣方向」；`IMPLEMENTATION_CONTRACT.md:2333` 把 `frontend/src/features/simulation/comparison/**` 的 owner 定为 `comparison-charts`，禁止「浏览器重采样」。

**通过项**：

- 指标表逐字渲染后端值，只做 locale 格式化：`frontend/src/features/simulation/charts/ComparisonMetricsTable.tsx` 直接取后端 `better_direction`；数值格式化在 `comparison/metrics.ts`，**不重算**。
- 归一化只校验不重算：`normalizers.ts:970-973` 对 `better_direction` 做 `fail()`，直接透传 `absolute_delta` / `relative_delta`。
- 时序对齐**按绝对时间戳 join，不插值**：`frontend/src/features/simulation/comparison/timeAlignment.ts:46` 注释明写 "Join only equal absolute timestamps; never resample or silently overwrite."，重复时间戳抛 `TimeAlignmentError`。
- **本轮补充核实**：owner 限制目录 `comparison/**` 内的 4 个文件（`differenceScale.ts` / `index.ts` / `metrics.ts` / `timeAlignment.ts` / `types.ts`）**确实没有重采样**——`grep -n "resample|interpolat|slice|Math.round"` 命中的只是 `timeAlignment.ts:46` 的禁止性注释和 `differenceScale.ts:9-11` 的颜色 hex 解析。

**需澄清项（中）**：客户端降采样与聚合实际位于 **`frontend/src/features/simulation/integration/comparisonApi.ts`**——`MAX_COMPARISON_SERIES_POINTS = 240`（`:6`）、`sampledIndexes()` 等距取样（`:44-52`）、`frameMetric()` 在客户端对 `depth_m` / `fill_ratio` / `flow_m3s` 做 `finiteMax` 聚合（`:16-42`）。这是**图表用的时间序列**，不是 §9.3 说的「指标」；且该文件**不在** owner 表 `:2333` 限制的 `comparison/**` 路径内。**判定：口径需 owner 澄清，不是已证实的违规。**

**次要类型不一致（低，沿用 round 1）**：`frontend/src/features/simulation/comparison/types.ts:15` 声明 `BetterDirection = 'higher' | 'lower' | 'target'`，而运行时归一化（`normalizers.ts:970-973`）与后端（`hydraulic_routes.py:3652`）都只接受 `lower | higher`。`'target'` 是**类型上允许、运行时必拒**的死分支。

**可由代码修复**：口径澄清 + 类型收窄，非阻断。

---

### 2.7 409 在 UI 上退化为裸错误串 —— **中，未修复**

**证据**：`frontend/src/pages/SimulationPage.tsx:335-340` 中 `simulationApi.compare()` 抛错走 `catch`，执行

```ts
setComparison(null)
setComparisonSeries([])
setComparisonError(error instanceof Error ? error.message : String(error))
```

随后在 `:711-715` 以 `<p className="simulation-error" role="alert">{comparisonError}</p>` 原样输出。

**对比设计好的分支**：`frontend/src/features/simulation/integration/ComparisonWorkbench.tsx:49` 计算 `const compatible = comparison?.compatible !== false`，`:125` 据此禁用 Δ 模式，`:132-137` 渲染 `t('comparison.incompatible')` 与 `comparison?.reason`。i18n key 存在（`frontend/src/i18n/en.json:338-339`、`zh.json:338-339`）。

**两条都不可达**：(a) `compatible` 被后端硬编码为 `true`（`:3679`），不兼容一律 409；(b) 后端**从不**在 comparison 响应里返回 `reason`（`grep -n '"reason"' backend/app/api/hydraulic_routes.py` 唯一命中 `:4035`，属别的端点）。

**后果**：用户选到不兼容的一对 simulation 时，看到的是异常 message 文本，而不是设计好的不兼容提示 + 指标表禁用态。

**可由代码修复**：是。前端在 `catch` 里识别 `code === 'COMPARISON_INCOMPATIBLE'` 并映射到 `compatible:false + reason`；或后端改返回 200 + `compatible:false + reason`。**契约两处口径（409 vs 200）需 owner 先裁定**——`PRD_SUPPLEMENT.md:486` 与 fixture `:117` 都写 409，§11.6 又暗示 200 形状。

---

### 2.8 测试与 e2e 的证据性质 —— **高（决定 AC-6 不能记 PASS）**

**冻结台账已自我声明**（`backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json:133-151`）：

```json
{
  "id": "AC-6",
  "title": "published A/B results expose recomputable lineage and metric deltas",
  "status": "NOT RUN",
  "reason": "The current comparison diagnostic injects completed simulations, empty frames and hand-written metrics into memory. Real result publication, checksums, units and restart replay are not proven.",
  "pass_nodeids": [],
  "required_gates": ["g3", "e2e", "g6"]
}
```

**这与我的独立结论一致**（§2.2、§2.3 的两个阻断项正是 "real result publication / units / restart replay not proven" 的具体所指）。

**证据补充**：

- 后端三个 AC-6 用例（`test_acceptance_matrix.py:690 / :746 / :783`）全部依赖 `_inject_in_memory_result()`，其 docstring 自述 "this is never worker evidence"；用例名本身也已诚实改名为 `test_ac6_in_memory_comparison_*`。
- `backend/tests/acceptance/conftest.py:44-70` 的 `validate_session_execution` 会在全量运行时把未真实执行的 case 判失败。
- **e2e 侧仍是 fixture 服务器，不是真后端**：`frontend/e2e/run-ac-smoke.mjs:533-540` 的 `createFixtureServer` 对 `/api/` 直接返回 hash-locked 的响应；comparison 走 `frontend/e2e/fixtures/api-v1/manifest.json:103-109`（`body_file: "comparison.json"`，`body_sha256` 冻结）→ `comparison.json`。**从未连接真实 FastAPI。**
- `docs/hydraulic-twin/operations.md:871-872` 明确写：「AC-6 仍 `NOT RUN`：需要两个真实 completed simulation 的同轮 G3/e2e/G6 artifact，**不得用注入内存结果的单元测试代替**」。
- `IMPLEMENTATION_CONTRACT.md:2552` 记 AC-6 `NOT RUN（无 comparison）`；`PRD_SUPPLEMENT.md:591` 记「**未实现/本轮未运行**」。

**结论**：现有测试证明的是 **(a) 路由逻辑在给定输入下的正确性** 与 **(b) UI 能渲染一份合规响应**，**不是**「两个真实 completed simulation 的 A/B 指标差异可查」。**实现方无虚报。**

---

### 2.9 新发现：rainfall 参与匹配的三方口径冲突 —— **中，需 owner 裁定**

三方说法不一致：

| 来源 | 对 rainfall 的要求 | 行号 |
|---|---|---|
| 冻结 fixture | 「network revision, target manifest, **rainfall**, start, duration, interval and frame checksums **match**」 | `acceptance_requirements.json:115` |
| `PRD_SUPPLEMENT.md` | 「**rainfall、solver、parameter set 可不同**；不同 topology/time grid 返回 409」 | `:486` |
| 实现 | **两者都不比较**——compare 字段列表无 `rainfall_scenario_id`、无 solver | `hydraulic_routes.py:3617-3627` |

**判定**：fixture 比 PRD 严（PRD 明确允许 rainfall 不同，fixture 要求相同），实现比两者都松（既不比 rainfall，也不比 solver）。**这是规格冲突，不是单纯实现缺陷**，本审计不替 owner 裁定。

**若以 fixture 为准**（rainfall 必须相同），则当前实现**少一个 409 分支**，且存在「A/B 实际是同一场雨、只换参数集」被当作「不同情景对比」的语义风险。
**若以 PRD 为准**（rainfall 可不同），则 fixture `:115` 需修订，否则 AC-6 永远无法在真实数据上通过门禁。

**可由代码修复**：**先裁定，后编码**。裁定为「必须相同」→ 补比较字段 + 测试（可由代码修复）；裁定为「可以不同」→ 修 fixture（属文档/fixture 变更，不在验收员权限内）。

---

## 3. 汇总

| # | 验收点 | 状态 | 阻断? | 可由代码修复 | 相对 round 1 |
|---|---|---|---|---|---|
| 2.0 | 门禁映射更正（`g3/e2e/g6`） | 更正 round 1 | 否 | 不适用 | **新** |
| 2.1 | 同 revision/manifest/时间网格校验 + 409 | 通过（代码层） | 否 | 是（补已发布帧网格比较） | 同 |
| 2.2 | `metric_units` / `better_directions` 前置可达 | **阻断** | 是 | **是**（需 metric catalog + finalize 写入） | **未修复** |
| 2.3 | 差异可持久化 / 结果回流 / 重启可复现 | **阻断**（本轮加重） | 是 | **是**（durable 读路径 + 落库 + 列表端点） | **未修复且加重** |
| 2.4 | absolute/relative delta、零基线 null | 通过（代码层） | 否 | 口径澄清 | 同 |
| 2.5 | 顶层字段齐全 | 通过（代码层） | 否 | 是（`compatible:false` 路径） | 同 |
| 2.6 | 浏览器不重采样/不猜方向 | 基本通过，需澄清 | 否 | 是（口径裁定 + 类型收窄） | 同 |
| 2.7 | 409 的 UI 退化路径 | 中 | 否 | 是（需先裁定 409/200 口径） | **未修复** |
| 2.8 | 证据性质（fixture 而非真实后端） | **阻断** | 是 | 是（需真实 e2e + 同轮 G3/G6 artifact） | 未修复（台账已自述） |
| 2.9 | rainfall 匹配口径三方冲突 | 中 | 否 | **先裁定再编码** | **新** |

**门禁状态**：`verify_hydraulic_stack.py --gate acceptance --ac ac6` → `BLOCKED / exit=2`，`required_gates=["g3","e2e","g6"]` 三个 artifact 全缺（§1.1）。
**AC-6 状态 = `NOT RUN`**，且存在两个结构性阻断（§2.2、§2.3）使其无法仅靠补证据转为 PASS。

## 4. 建议的最小修复顺序

1. **打通数据源**（最高优先，且是 round 1 遗漏的前置）：让已发布 attempt 的 result/frames 从 Postgres 读回 compare 路径；否则后面两步都无法在真实数据上验证 → 解锁 §2.3 的前半段。
2. 定义版本化 metric catalog（key → unit / better_direction），在 `ResultRepository.finalize` 写 summary 时预计算并写入 `metric_units` / `better_directions` → 解锁 §2.2。
3. compare 结果落 `ab_comparisons`（含 `comparison_sha256`），增加 `GET /api/ab-comparisons` 列表/详情，并补「重启后仍可复现」测试 → 解锁 §2.3 的后半段 = SOURCE_PRD 的「可查」。
4. **裁定 rainfall 匹配口径**（fixture vs PRD），再据此补/删 409 分支 → §2.9。
5. 裁定 409 vs `compatible:false`，前后端统一；顺带清掉 `types.ts:15` 的 `'target'` 死分支 → §2.7、§2.6。
6. 建真实 A/B e2e（真后端 + 两个由 `ResultRepository` 真实 finalize 的 simulation），产出同 RUN_ID 的 `g3`/`e2e`/`g6` artifact，让 `acceptance --ac ac6` 拿到 `PASS`。

## 5. 本轮未运行 / 不予裁决的项

- **未运行**：G0–G6 门禁（我只跑了 AC-6 范围内的 `acceptance --ac ac6`，未复跑 G3/e2e/G6 本身）、`e2e` 全流程、Docker/Compose、真实 EPA SWMM、`py_ponding`、5k/10k/24h 性能、浏览器 G5、pytest/vitest **全量**、`npm ci`、backend 启动。**我没有复跑 `VALIDATION_EVIDENCE.md` 中的任何 `[payload]` 条目**，因此不声称本报告验证或推翻其中任何一条。
- **未运行**：`frontend/src/pages/SimulationPage.test.tsx`（round 1 跑过；本轮未复跑，故不在本报告证据中）。
- **未裁决**：§2.9 的 rainfall 口径三方冲突，以及 §2.7 的 409 vs 200 口径冲突——均需 owner 决定。
- **未评估**：AC-1/3/4/5/7/8 是否达标，与本轮范围无关。
- **副作用声明**：本轮在仓库内新增/覆盖的文件仅：本审计文件；门禁脚本自身产生的 `.runtime/ac6r2/acceptance/result.json`（+ `.sha256`、run log）；诊断脚本写在仓库外的 `%TEMP%\ac6r2_diag.py`。**未修改任何实现或测试文件。**
