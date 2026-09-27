# AC-6 独立验收（round 1）：A/B 情景关键指标差异

- 验收日期：2026-09-28
- 验收人：独立验收员（只读；未修改任何实现/测试/文档，仅新增本审计文件，并运行门禁脚本在其自身 evidence 目录下产生的运行产物）
- 仓库：`smart-city-drainage`，`git rev-parse --short=8 HEAD` = `ca99e048`，`git status --porcelain | wc -l` = `157`（工作树非干净，本报告结论对应**当前工作树**）
- 验收对象：AC-6「A/B 指标差异可查」

## 0. 结论

**AC-6 = `partial`（能力未通过；门禁为 `BLOCKED`，本轮 `NOT RUN`）。未取得任何 PASS 证据。**

三句话概括：

1. **契约口径与实现形状基本对得上，但依赖的前置条件在生产里不可满足。** 路由、响应顶层字段、409 语义、零基线 `relative=null`、浏览器不重算 delta/方向——这些都按 `PRD_SUPPLEMENT.md §9.3` / `IMPLEMENTATION_CONTRACT.md §11.6` 实现并有测试。
2. **但 compare 端点依赖 `result.metric_units` 与 `result.better_directions` 两个字段，而 `backend/app` 中没有任何生产代码产生它们**（全仓 grep 只有读取方，没有写入方）。我用只读诊断证明：只要 result 是"任何现有生产路径能产出的形状"，compare 必定返回 `409 COMPARISON_INCOMPATIBLE`。**AC-6 在结构上不可达**，不只是"证据没采"。
3. **差异不可持久化、不可追溯。** `hydraulic.ab_comparisons` 有 migration、ORM、readyz 必查表，但**没有任何路由读写它**；A/B 的事实源是进程内 dict。因此 SOURCE_PRD 的"可查"当前只能靠单进程内存，重启即失。

诚实度方面：实现方**没有虚报**。`VALIDATION_EVIDENCE.md:209` 记录了 `test_ac6_comparison_rejects_metric_unit_mismatch` 曾失败；`IMPLEMENTATION_CONTRACT.md:2552` 记 AC-6 `NOT RUN（无 comparison）`；`docs/hydraulic-twin/operations.md:869-872` 明确写"AC-6 仍 `NOT RUN`……不得用注入内存结果的单元测试代替"。**问题是能力缺口，不是声明造假。**

---

## 1. 本轮实际执行的检查（完整命令与真实输出）

### 1.1 契约冻结入口：`acceptance --ac ac6` → `BLOCKED / exit=2`

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage" && backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py --gate acceptance --ac ac6 --evidence-dir ".runtime/audit-ac6/acceptance" > ".runtime/audit-ac6/acceptance-run.log" 2>&1; echo "REAL_EXITCODE=$?"
```

真实输出（截取）：

```json
{"acceptance": [{"ac": "ac6",
  "artifact_paths": [],
  "missing_or_invalid_artifacts": ["e2e", "missing artifact: ...\\.runtime\\audit-ac6\\e2e\\ac-smoke.json"],
  "required_gates": ["e2e"],
  "status": "BLOCKED"}],
 "commit": "ca99e0485fb12fbecbd565e79d81f745f8d4a748",
 "dirty_state": true,
 "environment": {"python_version": "3.11.15", "node_version": "v25.9.0", "npm_version": "11.12.1", "git_dirty": true},
 "exit_code": 2,
 "run_id": "20260927T191904Z-ca99e048",
 "schema": "hydraulic-acceptance-report-v1",
 "status": "BLOCKED",
 "utc_timestamp": "2026-09-27T19:19:04.318374Z"}

HYDRAULIC_STACK_STATUS=BLOCKED
HYDRAULIC_STACK_EXIT=2
HYDRAULIC_STACK_ARTIFACT_SHA256=ed8162838b267fad4cc8467689793336d90d3765fe82bb265e74e063d4e6f436
REAL_EXITCODE=2
```

第一次把 `--evidence-dir` 指向 `%TEMP%` 时脚本按设计拒绝：`GateError: evidence directory escapes repository root`（`scripts/verify_hydraulic_stack.py:186-191`）。改为仓库内 `.runtime/audit-ac6/` 后正常执行。

AC-6 只依赖 `e2e` 一个 artifact（`scripts/verify_hydraulic_stack.py:1710`：`"ac6": ("e2e",)`），缺失即 `BLOCKED`。

### 1.2 后端 A/B 定向测试 —— 三个 AC-6 用例全绿

```text
$ backend/.venv/Scripts/python.exe -m pytest backend/tests/acceptance/test_acceptance_matrix.py -k "ac6" -q
...                                                                      [100%]
3 passed, 15 deselected, 1 warning in 0.48s
```

整文件：

```text
$ backend/.venv/Scripts/python.exe -m pytest backend/tests/acceptance/test_acceptance_matrix.py -q
..................                                                       [100%]
18 passed, 1 warning in 4.38s
```

> **与 `VALIDATION_EVIDENCE.md:209` 的偏差（该文档记录此时已过时）**：台账把 `test_acceptance_matrix.py::test_ac6_comparison_rejects_metric_unit_mismatch` 列入 7 项失败（`7 failed, 57 passed`）。我在当前工作树实测该文件 18 项全过。**该台账条目对 AC-6 已过期**，但这不改变 AC-6 的 `NOT RUN` 状态——理由见 §2.1、§2.2。台账本身声明过"证据对应当前工作树而非干净检出"（`VALIDATION_EVIDENCE.md:476`）。

### 1.3 前端 A/B 定向测试 —— 全绿

```text
$ cd frontend && npm test -- --run src/features/simulation/integration/comparisonApi.test.ts src/features/simulation/test/chart-comparison.test.tsx
 ✓ src/features/simulation/integration/comparisonApi.test.ts (1 test) 4ms
 ✓ src/features/simulation/test/chart-comparison.test.tsx (10 tests) 76ms
 Test Files  2 passed (2)
      Tests  11 passed (11)
```

```text
$ cd frontend && npm test -- --run src/pages/SimulationPage.test.tsx
 ✓ src/pages/SimulationPage.test.tsx (1 test) 218ms
 Test Files  1 passed (1)
      Tests  1 passed (1)
```

Node 实际为 `v25.9.0`，仓库冻结 `.node-version` = `18.20.8`（与 `VALIDATION_EVIDENCE.md:82` 同一条环境事实）。单元测试不受影响，但**这不是 AC-6 的合规运行证据**。

### 1.4 只读诊断：真实 result 形状下 compare 必定 409

诊断脚本写在仓库外 `%TEMP%\ac6_diag.py`，不新增仓库文件。它按生产 API 真实路径创建 network/rainfall/两个 simulation，然后注入一个"**只有 `metrics`、没有 `metric_units` / `better_directions`**"的 completed result（这是当前 `backend/app` 唯一可能产出的形状），再调 compare：

```text
$ PYTHONPATH=".../backend" ./.venv/Scripts/python.exe /tmp/ac6_diag.py
NETWORK_CREATE 201 {...}
RAINFALL_CREATE 201 {...}
SIM_SUBMIT ac6-diag-s1 202 {...}
SIM_SUBMIT ac6-diag-s2 202 {...}
REALISTIC_RESULT_WITHOUT_UNITS status= 409 code= COMPARISON_INCOMPATIBLE detail= simulation metrics have incompatible units or directions
```

这条诊断是本报告最关键的一条：**AC-6 的成功路径在生产中不可达。**

---

## 2. 逐条验收：证据 / 缺口 / 是否可由代码修复

### 2.1 兼容性与 409 语义 —— **通过（代码层）**

**要求**：`PRD_SUPPLEMENT.md:485-486`「baseline/candidate 必须同 `network_revision_id + target_manifest_id/hash`、同 start/end/frame interval/frame count；不同 topology/time grid 返回 409 `COMPARISON_INCOMPATIBLE`」。

**证据**：`backend/app/api/hydraulic_routes.py:3536-3553` 逐字段比较 `network_revision_id / target_manifest_id / target_manifest_sha256 / start_at / duration_seconds / frame_interval_seconds`，任一不等即 `409 COMPARISON_INCOMPATIBLE`。`frame_count` 未直接比较，但由 `_frame_count()`（`:3325-3331`）从 `duration_seconds // frame_interval_seconds + 1` 推导，而这三项已被比较，故 frame count 恒等——**推导成立**。测试：`backend/tests/acceptance/test_acceptance_matrix.py:726-742`（改 `duration_seconds=300` → 409）。

**缺口**：比较的是"请求侧时间网格参数"，**不是已发布帧网格**。若某 simulation 实际发布的帧数与参数不符（staging 不完整/被裁剪），compare 不感知。属低风险。

**可由代码修复**：是（把 `len(published_frames)` 或 published_attempt 的 frame_count 纳入比较）。

---

### 2.2 `metric_units` / `better_directions` 无任何生产生产者 —— **阻断（高）**

**要求**：`PRD_SUPPLEMENT.md:487-488`「返回 baseline、candidate、absolute delta、relative delta、unit、better direction；baseline 为 0 时 relative=null」「指标由后端预计算并可逐项重算」。

**证据（读取方）**：`backend/app/api/hydraulic_routes.py:3560-3578`

```python
baseline_units = (baseline.get("result") or {}).get("metric_units", {})
candidate_units = (candidate.get("result") or {}).get("metric_units", {})
baseline_directions = (baseline.get("result") or {}).get("better_directions", {})
...
if (not isinstance(unit, str) or not unit.strip()
    or candidate_units.get(key) != unit
    or better_direction not in {"lower", "higher"}
    or candidate_directions.get(key) != better_direction):
    raise _error(409, "COMPARISON_INCOMPATIBLE", ...)
```

**证据（写入方不存在）**：

```text
$ grep -rn "metric_units" --include=*.py . | grep -v "/build/lib/" | grep -v "/tests/"
./backend/app/api/hydraulic_routes.py:3560:    baseline_units = ...
./backend/app/api/hydraulic_routes.py:3561:    candidate_units = ...
```

排除 `build/lib` 陈旧副本与测试后，**只剩两行读取，没有任何写入**。`better_directions` 同样（唯一非测试命中是 `build/lib/app/api/hydraulic_routes.py:3474,3479` 的陈旧构建副本）。唯一生产者是测试助手 `backend/tests/acceptance/test_acceptance_matrix.py:175-206` 的 `_inject_in_memory_result()`，其 docstring 自述 **"Inject an in-memory diagnostic result; this is never worker evidence."**。`ResultRepository.finalize()`（`backend/app/services/hydraulic/results.py:610-700`）只发布 `summary` / `quality_report` / `frame_manifest`，不产生这两个字段。

**诊断证据**：§1.4，`status=409 code=COMPARISON_INCOMPATIBLE`。

**缺口**：AC-6 的 happy path 在生产不可达。要么 result 发布链补 `metric_units` + `better_directions`（需要一个版本化的 metric catalog：key→unit、key→better direction），要么 compare 端点降级为"缺单位即拒绝"以外的策略。**当前实现选择了最严格的 fail-closed，语义上没错，但前置条件永远不成立。**

**可由代码修复**：**是**。需要 (a) 定义 metric catalog（至少 `max_node_depth_m`/`max_pipe_fill_ratio`/`max_ponding_depth_m`/`total_flood_volume` 之类及其单位与方向），(b) 在 `ResultRepository.finalize` 的 summary 里按 catalog 预计算并写入 `metric_units` / `better_directions`，(c) 补一条真实 worker 路径的测试。

---

### 2.3 `absolute_delta` / `relative_delta` / 零基线 —— **通过（代码层）**

**证据**：`backend/app/api/hydraulic_routes.py:3580-3592` 在后端现算，不落库也不由前端猜测：

```python
delta = candidate_value - baseline_value
...
"absolute_delta": delta,
"relative_delta": delta / baseline_value if baseline_value else None,
"better_direction": better_direction,
```

测试 `test_ac6_comparison_returns_deltas_and_rejects_incompatible_grid` 断言 `absolute_delta == -5.0`、`relative_delta == -0.5`、`better_direction == "lower"`（`test_acceptance_matrix.py:718-720`）；`test_ac6_in_memory_comparison_handles_zero_baseline` 断言 `baseline=0` 时 `relative_delta is None`（`:771-772`）。二者本轮均通过（§1.2）。

**缺口**：`if baseline_value` 用真值判断，`baseline_value == -0.0` 也会落 `None`（等价，可接受）；`baseline_value` 为负数时 `relative_delta` 符号语义未在契约中定义（`PRD_SUPPLEMENT.md:487` 只规定"baseline 为 0 时 relative=null"）。属口径未定义，非缺陷。

**可由代码修复**：口径澄清即可，非阻断。

---

### 2.4 响应顶层字段 —— **通过（代码层）**

**要求**：`IMPLEMENTATION_CONTRACT.md:1953`「comparison 顶层必须返回 `compatible/network_revision_id/target_manifest_id/target_manifest_sha256/frame_grid/baseline/candidate/metrics`」。

**证据**：`backend/app/api/hydraulic_routes.py:3596-3613` 全部返回，`schema_version="simulation-comparison-v2"`，`frame_grid` 含 `start_at/end_at/interval_seconds/frame_count`，与契约 §11.6 一致。前端 `normalizers.ts:1005-1051` 逐字段强校验（含 `end-start == (frame_count-1)*interval*1000` 的网格自洽断言 `:1027-1029`）。

**缺口**：`compatible` 在 200 响应中恒为 `true`（不兼容一律 409），所以前端为 `compatible===false` 准备的分支（`ComparisonWorkbench.tsx:118-124` 的 `comparison.incompatible` 警告）在真实后端下**不可达**。见 §2.7。

**可由代码修复**：是（后端可选返回 `compatible:false + reason`，或前端把 409 映射到该分支）。

---

### 2.5 差异可持久化 / 可追溯 —— **阻断（高）**

**要求**：`SOURCE_PRD.md:33`「**AC-6 A/B 指标差异可查**」；`PRD_SUPPLEMENT.md:483-488`；契约表 `IMPLEMENTATION_CONTRACT.md:953-966` 定义 `hydraulic.ab_comparisons`（含 `metrics JSONB`、`comparison_sha256 CHAR(64) NOT NULL`、`created_by`、`UNIQUE(baseline_simulation_id,candidate_simulation_id,frame_grid)`）。

**已具备**：migration `backend/alembic/versions/0007_hydraulic_ab_comparisons.py:51-98`（含 `uq_hydraulic_ab_comparisons_grid` 与三个 FK）；ORM `backend/app/models/hydraulic_models.py:3366-3414`；`backend/app/main.py:63` 把它列入 `/readyz` 必查表。

**缺口**：

```text
$ grep -rn "AbComparison" --include=*.py . | grep -v "/build/lib/"
./backend/app/models/hydraulic_models.py:3366:class AbComparison(Base):
./backend/app/models/hydraulic_models.py:3456:    "AbComparison",
./backend/app/models/__init__.py:17:    AbComparison,
./backend/app/models/__init__.py:104:    "AbComparison",
./backend/tests/test_hydraulic_schema_readyz_tables.py:696:        "AbComparison",
```

**只出现在 schema 注册与表名断言里，没有任何 INSERT/SELECT。** `compare_simulations` 每次请求现算现返回，**不落库、不写 `comparison_sha256`、无列表/历史查询端点**（`grep -n "comparisons" backend/app/api/hydraulic_routes.py` 无输出）。

叠加第二层问题：A/B 的事实源是**进程内 dict**。`backend/app/api/hydraulic_routes.py:286-315` 的 `HydraulicApiStore` 全部资源存在内存，`durable_backend = False`（`:297`）且**全仓无任何位置置为 `True`**（唯一读取方是 `backend/app/main.py:1273` 的 production 503 闸门）。这与 `PRD_SUPPLEMENT.md:105`「MUST NOT 以 …进程内变量… 表示任务/告警完成状态」冲突。

**后果**：AC-6 字面要求"**可查**"，当前只能在**单进程存活期间**查到一次；重启即全部丢失，多 worker 副本之间互不可见。

**可由代码修复**：**是**，但工作量不小：(a) 把 compare 结果持久化到 `ab_comparisons`（含 `comparison_sha256` = 规范化 `frame_grid+metrics` 的 SHA-256）；(b) 接到 SQLAlchemy repository 而不是 `HydraulicApiStore`；(c) 增加 `GET /api/ab-comparisons` 列表/详情。

---

### 2.6 浏览器不重采样 / 不猜方向 —— **基本通过，一处需澄清**

**要求**：`PRD_SUPPLEMENT.md:488`「指标由后端预计算并可逐项重算；浏览器不重采样、不猜测优劣方向」；`IMPLEMENTATION_CONTRACT.md:2333` 把 `frontend/src/features/simulation/comparison/**` 的 owner 定为 `comparison-charts`，禁止「浏览器重采样」。

**通过项**：

- 指标表逐字渲染后端值，只做 locale 格式化：`frontend/src/features/simulation/charts/ComparisonMetricsTable.tsx:58`（`betterDirectionLabel(metric.better_direction, …)` 直接取后端字段），数值格式化在 `comparison/metrics.ts`，**不重算**。
- 归一化只校验不重算：`frontend/src/features/simulation/model/normalizers.ts:960-983` 逐字段 `fail()`，直接透传 `absolute_delta` / `relative_delta` / `better_direction`。
- 时序对齐**按绝对时间戳 join，不插值**：`frontend/src/features/simulation/comparison/timeAlignment.ts:49-76`，注释明写 "Join only equal absolute timestamps; never resample or silently overwrite."，重复时间戳直接抛 `TimeAlignmentError`。

**需澄清项（中）**：`frontend/src/features/simulation/integration/comparisonApi.ts` **在浏览器侧对帧数据做了降采样与聚合**——`MAX_COMPARISON_SERIES_POINTS = 240`（`:6`）、`sampledIndexes()` 等距取样（`:44-52`）、`frameMetric()` 在客户端对 `depth_m` / `fill_ratio` / `flow_m3s` / `surcharge` 做 `finiteMax` 聚合（`:16-42`）。这是**图表用的时间序列**，不是 §9.3 说的"指标"，两者口径不同；但 owner 表把该目录的禁令写成"浏览器重采样"，字面上命中。**判定：口径需要 owner 澄清，不是已证实的违规。** 建议要么把禁令范围写死为"聚合指标"，要么让后端提供 comparison series 端点。

**次要类型不一致（低）**：`frontend/src/features/simulation/comparison/types.ts:15` 声明 `BetterDirection = 'higher' | 'lower' | 'target'`，而运行时归一化（`normalizers.ts:970-973`）只接受 `null | 'lower' | 'higher'`，后端也只产生 `lower|higher`（`hydraulic_routes.py:3570`）。`'target'` 是**类型上允许、运行时必拒**的死分支。同一文件 `types.ts:26` 的 `unit: string | null` 实际也被 `requiredString` 收窄为非空。可由代码修复（收窄类型），非阻断。

---

### 2.7 409 在 UI 上退化为裸错误串 —— **中**

**证据**：`frontend/src/pages/SimulationPage.tsx:303-340` 中 `simulationApi.compare()` 抛错走 `catch`，执行 `setComparison(null); setComparisonSeries([]); setComparisonError(error.message)`（`:337-340`），随后在 `:711-715` 以 `<p className="simulation-error" role="alert">{comparisonError}</p>` 原样输出。契约与 `operations.md:854` 描述的"UI 禁用同步播放 / 展示 incompatible 说明"路径（`ComparisonWorkbench.tsx:118-124`）不会触发，因为那是 `comparison.compatible === false` 的分支（§2.4）。

**后果**：用户选到不兼容的一对 simulation 时，看到的是异常 message 文本，而不是设计好的不兼容提示 + 指标表禁用态。

**可由代码修复**：是（前端在 `catch` 里识别 `code === 'COMPARISON_INCOMPATIBLE'` 并映射到 `compatible:false + reason`，或后端改返回 200 + `compatible:false`）。契约两处口径（409 vs 200）需 owner 先裁定。

---

### 2.8 测试覆盖的性质 —— **高（决定 AC-6 不能记 PASS）**

**证据**：

- 后端三个 AC-6 用例全部依赖 `_inject_in_memory_result()`（`test_acceptance_matrix.py:175-206`），该函数 docstring 自述 **"this is never worker evidence"**。
- 该文件模块 docstring（`test_acceptance_matrix.py:1-7`）自述："The matrix gate … **refuses to treat an in-memory result as AC evidence**."
- `backend/tests/acceptance/conftest.py:44-70` 的 `validate_session_execution` 会在全量运行时把未真实执行的 case 判失败。
- 唯一覆盖 compare 端点的测试文件就是这一个：`grep -rln "compare/" --include=*.py backend/tests` → 仅 `test_acceptance_matrix.py`。
- **e2e 侧是 fixture 服务器，不是真后端**：`frontend/e2e/run-ac-smoke.mjs:534-560` 的 `createFixtureServer` 对 `/api/` 直接从 `frontend/e2e/fixtures/api-v1/manifest.json:104-109` 返回 hash-locked 的 `comparison.json`；从未连接真实 FastAPI。AC-6 场景步骤见 `frontend/e2e/fixtures/frontend-acceptance-v1.json:237-238, 346-420`（选 baseline/candidate、点 refresh、断言 `comparison-metrics` 表含 `max_node_depth_m`）。

**结论**：现有测试证明的是 **(a) 路由逻辑在给定输入下的正确性** 与 **(b) UI 能渲染一份合规响应**，**不是** "两个真实 completed simulation 的 A/B 指标差异可查"。这与 `docs/hydraulic-twin/operations.md:869-872` 的自述一致，也与 `PRD_SUPPLEMENT.md:580`「`PASS` 只允许在目标命令本轮 exit 0 且 artifact 存在、hash 校验通过时填写」一致。

---

## 3. 汇总

| # | 验收点 | 状态 | 阻断? | 可由代码修复 |
|---|---|---|---|---|
| 2.1 | 同 revision/manifest/时间网格校验 + 409 | 通过（代码层） | 否 | 是（补已发布帧网格比较） |
| 2.2 | `metric_units` / `better_directions` 前置可达 | **阻断** | 是 | **是**（需 metric catalog + finalize 写入） |
| 2.3 | absolute/relative delta、零基线 null | 通过（代码层） | 否 | 口径澄清 |
| 2.4 | 顶层字段齐全 | 通过（代码层） | 否 | 是（`compatible:false` 路径） |
| 2.5 | 差异可持久化/可追溯（SOURCE_PRD"可查"） | **阻断** | 是 | **是**（落 `ab_comparisons` + durable repo） |
| 2.6 | 浏览器不重采样/不猜方向 | 基本通过，需澄清 | 否 | 是（口径裁定 + 类型收窄） |
| 2.7 | 409 的 UI 退化路径 | 中 | 否 | 是（需先裁定 409/200 口径） |
| 2.8 | 证据性质（fixture 而非真实后端） | **阻断** | 是 | 是（需真实 e2e + 同轮 G-gate artifact） |

**门禁状态**：`verify_hydraulic_stack.py --gate acceptance --ac ac6` → `BLOCKED / exit=2`（§1.1）。AC-6 状态 = **`NOT RUN`**，且存在结构性阻断使其无法仅靠补证据转为 PASS。

## 4. 建议的最小修复顺序

1. 定义版本化 metric catalog（key → unit / better_direction），在 `ResultRepository.finalize` 写 summary 时预计算 → 解锁 §2.2。
2. compare 结果落 `ab_comparisons`（含 `comparison_sha256`），并把 A/B 读路径从 `HydraulicApiStore` 迁到 SQLAlchemy repository → 解锁 §2.5（= SOURCE_PRD 的"可查"）。
3. 裁定 409 vs `compatible:false` 口径，前后端统一；顺带清掉 `types.ts:15` 的 `'target'` 死分支 → §2.7、§2.6。
4. 建真实 A/B e2e（真后端 + 两个真实 completed simulation），产出同 RUN_ID 的 `e2e/ac-smoke.json`，让 `acceptance --ac ac6` 拿到 `PASS`。

## 5. 本轮未运行 / 不予裁决的项

- **未运行**：G0–G6 门禁、Docker/Compose、真实 EPA SWMM、`py_ponding`、5k/10k/24h 性能、浏览器 G5、Vitest/pytest 全量、`npm ci`、backend 启动。我**没有复跑** `VALIDATION_EVIDENCE.md` 中的任何 `[payload]` 条目（该文件 `:473` 亦如此声明），因此不声称本报告验证或推翻其中任何一条。
- **未裁决**：`docs/hydraulic-twin/contracts/frontend.md`（SUPERSEDED / NON-NORMATIVE，`PRD_SUPPLEMENT.md:21`）与 `IMPLEMENTATION_CONTRACT.md` §11.6 在"409 vs 200 incompatible 响应"上的口径冲突——需 owner 决定，本审计不替其裁决。
- **未评估**：AC-1/3/4/5/7/8 是否达标，与本轮范围无关。
- **副作用声明**：本轮在仓库内新增/覆盖的文件仅：本审计文件，以及门禁脚本自身产生的 `.runtime/audit-ac6/acceptance/result.json`（+ `.sha256`、run log）。**未修改任何实现或测试文件。**
