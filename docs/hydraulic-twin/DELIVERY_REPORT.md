# 排水管网水力数字孪生 · 交付报告（DELIVERY_REPORT）

> 撰写日期：2026-09-28；**修订：第 2 轮**（系统 UTC 读数 2026-09-27T21:4xZ）
> 撰写者：交付报告撰写员（只写文档，**未修改任何生产代码**，未 commit、未 push）
> 仓库：`smart-city-drainage`；`git rev-parse --short=8 HEAD` = **`ca99e048`**
>
> **⚠ 关于本文所有工作树计数：它们是快照，不是稳定量。** 每跑一次门禁都会往 `.runtime/evidence/` 增目录，`git status` 计数随之变化。文中每个数字都标注了采集时点；**重跑 §6.1 的清单会改变 §1.1/§5.2 的数字，这是预期的**。不要把任何计数当成断言去核对。
>
> **第 2 轮修订摘要**（依据冷读反馈，逐条已在本会话复现）：更正 §8.1 的 DELETE 绝对句（A1）、§3 FR-6 的 `production_modules` 对应（A2）、§4.1/§6.2 的"唯一权威"措辞（A3）、§0.2 的"其他项目"（A4）；修正 §9 R-1 的无出处数字（B1）、§5.2 整张表的统计口径与成因（B2）、§6.1 缺快照语义（B3）、§5.4 的 audits 数量自相矛盾（B4）；补 §11.2/§7.2 的 14 个必填变量清单与另外 6 个留空项的说明（C1）、§12 的"最小可跑命令"列（C2）、§2.2 第 10 项分级（C3）、§8 C-7 行号（C4）、§8 C-5 的 `published_frames` 测试写入点（C5）、§3 FR-1/FR-5 的零 INSERT 强证据（C6）。**另新增 §0 的 `DOC_CHECKS_EXIT` 自我更正**（见下）。

> **⚠ 第 2 轮自我更正（本撰写者的测量错误）**：第 1 轮在 §6.1 记的 `DOC_CHECKS_EXIT=0` **是错的**——那条命令带了管道 `… doc_checks.py 2>&1 | tail -20; echo $?`，`$?` 取到的是 **`tail` 的退出码**，不是脚本的。第 2 轮去掉管道重测：**.runtime/doc_checks.py 的真实退出码是 1**（`REAL_DOC_CHECKS_EXIT=1`），原因与本报告无关：`ROUTE_CITATION_MISMATCH` 有 40 条**既有**命中，全部来自 `CONTRACT_INDEX.md` 的路由表，而 `route_bad` 在脚本的失败集合内（`.runtime/doc_checks.py:342`）。
> 这正是 `[文档]` `reviews/delivery-docs.md:123-124` 提醒过的坑（"用管道取尾部时 shell 的 `$?` 是 `tail` 的退出码，不作为证据使用"）——**第 1 轮我自己踩了同一个坑，在此更正。**
> 另注：`doc_checks.py` 的扫描范围是硬编码的 4 个文件（`.runtime/doc_checks.py:20-25` 的 `OWNED`），**不含本报告**（`WS_FILES_CHECKED=4`，输出中 `DELIVERY_REPORT` 0 命中），故本报告的任何修改都不会影响该检查器。
>
> **我本次（两轮累计）在磁盘上的全部写入**（除此之外未动任何文件）：
> 1. 本文件 `docs/hydraulic-twin/DELIVERY_REPORT.md`（1 个）
> 2. 门禁证据目录 `.runtime/evidence/dr-r1-portable/`、`dr-r1-postgis/`、`dr-r1-acceptance/` —— 执行 §2.1 三条门禁命令的必然副产物，`.runtime/` 未被 Git 跟踪，与前几轮做法一致
>
> 写入后复核：被跟踪文件的 `M` 状态仍为 **79**（两轮之间未变，无生产代码被动过）。

---

## 0. 怎么读这份报告（先读这一节，否则容易误判）

### 0.1 证据来源标注

本文每条结论都带来源标签，**请按标签决定信任度**：

| 标签 | 含义 | 复核成本 |
|---|---|---|
| `[本轮实跑]` | 撰写本报告的会话中**由我亲自执行**，命令与输出如实摘录 | 低，命令都在文中，可重跑 |
| `[payload]` | 本次 ask 传入的证据串，**我未复跑** | 中，需自己重跑才能确认 |
| `[文档]` | 既有文档/审计报告的记录，我读过后引用，**未独立验证其产生过程** | 视文档而定 |

### 0.2 一句话结论

**8 个 AC 全部 BLOCKED，不得放行任何一个。** 代码量很大、单元测试大面积为绿，但**从 API 进去的写路径没有一条落到 Timescale/PostgreSQL**，因此"跑得起来"与"AC 判据要求的跨重启可复现"是两件正交的事。

- 我本轮实跑 `--gate acceptance --ac ac1..ac8` → `HYDRAULIC_STACK_STATUS=BLOCKED / EXIT=2`，8/8 `status: BLOCKED`，全部因 `missing artifact: .../g{1..6}/.../result.json`。
  **这个 BLOCKED 判定的是"门禁证据是否存在"，不是"AC 实质不合格"**——两者必须分开读，见 §4.1。
- **全仓零处写拓扑表与帧表**（`[本轮实跑]`，§3 FR-1 行给了完整命令与输出）：`networks` / `network_nodes` / `pipe_segments` / `catchments` / `simulation_frames` / `network_revisions` 全部零命中 INSERT。worker 侧那 5 条 INSERT 写的是任务簿记表，不是数据表。
- 本机**没有任何运行中的 `smart-city-drainage` 栈**。`docker ps` 现有 7 个容器，其中 4 个（`scn-hydraulic-probe-postgres-1`、`scd-hydraulic-round1-postgis-postgis-postgis-1`、`scd-hydraulic-round2-portable-postgres-portable-1`、`scd-hydraulic-round2-postgis-postgis-postgis-1`）**是本项目水力栈的早期残留**、停在 alembic `0003`（§1.2），不是可用演示环境。

### 0.3 三条最容易踩的坑（先看这里）

1. **本机 Node/npm 不符合冻结目标。** 实测 `node v25.9.0` / `npm 11.12.1`；`IMPLEMENTATION_CONTRACT.md:2439` 冻结要求 `18.20.8` / `10.8.2`。`[payload]` 里的前端 lint/Vitest/build **是在这个非目标 Node 上跑的**。
2. **G5 solver 基准的"PASS"不可复现，且其生产代码从未进版本控制。** `[payload]` 记 `status:"PASS"`，而 `[文档]` `audits/final-audit-round-2.md` C-3 在**同一 commit、同一工作树**实跑得 `FAIL/exit 1`；我本轮确认两个产出该结果的脚本都是 `??` untracked（见 §2.4）。**在修复前，任何 G5 数字都不得用于判读 AC-3。**
3. **全量 pytest 的 797 passed 不能当 AC 进展量。** 65 个 acceptance 用例全部跑在 `DEPLOYMENT_ENVIRONMENT=development` 下，而持久化守卫 `_require_durable_api_store()` 只在 `production` 生效——产生绿灯的那个环境，恰好是唯一不做持久化校验的环境（`[文档]` `audits/final-audit-round-2.md` C-2）。

---

## 1. 环境事实

### 1.1 我本轮实跑的环境事实 `[本轮实跑]`

> **计数是快照。** 下表工作树计数采集于**第 1 轮开始时**；第 2 轮修订时 `git status --porcelain -uall | wc -l` 已变为 **908**（差额来自门禁证据目录累积）。**任何计数都会随"跑门禁"而变，不要当断言核对**；稳定量只有 `HEAD` 与各工具版本。

| 项 | 实测值 | 冻结目标 | 是否达标 |
|---|---|---|---|
| `git rev-parse --short=8 HEAD` | `ca99e048` | — | — |
| `git status --porcelain \| wc -l`（第 1 轮） | `160` | — | — |
| `git status --porcelain -uall \| wc -l`（第 1 轮） | `895` | — | — |
| `git status --porcelain -uall \| wc -l`（第 2 轮） | `908` | — | — |
| `git status --porcelain -uall \| grep -c '^??'`（第 2 轮） | `829` | — | — |
| 被跟踪文件 `M` 状态数（两轮均为） | `79` | — | ✅ 稳定 |
| `docker --version` | `Docker version 29.4.2, build 055a478` | Docker 可用 | ✅ 可用 |
| `docker compose version` | `v5.1.3` | — | ✅ |
| `node --version` | **`v25.9.0`** | `v18.20.8` | ❌ |
| `npm --version` | **`11.12.1`** | `10.8.2` | ❌ |
| `backend/.venv/Scripts/python.exe` | `Python 3.11.15` | `3.11` | ✅ |
| 门禁自报 `physical_cores` | **`null`** | 4 physical cores | ❌ 未取到 |
| 门禁自报 `ram_bytes` | **`null`** | 8589934592 (8 GiB) | ❌ 未取到 |
| 门禁自报 `logical_cpus` | `32` | — | — |
| 门禁自报 OS | `Windows-10-10.0.26340-SP0` | — | — |

> **`physical_cores=null` / `ram_bytes=null` 是一条重要事实**：G5 的两条硬门槛（4 物理核 / 8 GiB）在 Windows 上根本没被采集到。因此**即便某次 G5 报 PASS，也无法证明它跑在冻结目标环境上**。`[payload]` 的 G5 摘录里 `environment` 块正好不在摘录内（`[文档]` `VALIDATION_EVIDENCE.md:179`），两边都指向同一结论。

### 1.2 当前运行中的容器 `[本轮实跑]`

```text
$ docker ps --format "{{.Names}}"
scd-readyz8-portable-postgres-portable-1
scd-readyz8-postgis-postgres-postgis-1
scn-hydraulic-probe-postgres-1
scd-hydraulic-round2-portable-postgres-portable-1
scd-hydraulic-round2-postgis-postgres-postgis-1
scd-hydraulic-round1-postgis-postgis-postgis-1
deepseek-search-searxng
```

**没有 `smart-city-drainage-*` 容器。** `[文档]` `implementation/delivery-docs.md` §2.5 观测到的那台 11 容器栈（backend + 6 worker + postgres/redis/influxdb + migrate）**已不存在**。

这些残留 DB 也不是演示数据源。对 `scd-hydraulic-round2-portable-postgres-portable-1` 实查 `[本轮实跑]`：

```text
hydraulic.networks=0  network_revisions=0  network_nodes=0  pipe_segments=0
hydraulic.catchments=0  rainfall_scenarios=0  network_jobs=0  simulations=0
hydraulic.nowcast_runs=0  forecast_alerts=0  calibration_runs=0
hydraulic.observation_samples  →  关系不存在
alembic_version = 0003_hydraulic_schema_gap          ← 不是 0008
schema_metadata = portable | 0003_hydraulic_schema_gap
```

即：**这些是早期 stage 的过期容器（停在 0003），不是当前工作树的 0008 schema，也不含任何演示数据。** 不要基于它们做任何判断。

### 1.3 当前工作树的 schema 版本已自洽 `[本轮实跑]`

```text
$ ls backend/alembic/versions/ | sort
0001_initial.py  0002_hydraulic_schema.py  0003_hydraulic_schema_gap.py
0004_hydraulic_imports.py  0005_hydraulic_observations.py  0006_hydraulic_alert_windows.py
0007_hydraulic_ab_comparisons.py  0008_hydraulic_retention_seed.py

$ grep -n HYDRAULIC_SCHEMA_REVISION docker-compose.yml   → 7 处，全部 0008_hydraulic_retention_seed
$ grep -n HYDRAULIC_SCHEMA_REVISION .env.example         → :24 = 0008_hydraulic_retention_seed
$ grep -n HYDRAULIC_SCHEMA config.py                     → :59 HEAD=0008 / :268 Literal["0008_hydraulic_retention_seed"]
```

`[文档]` `VALIDATION_EVIDENCE.md:286-287` 记录的"`docker-compose.yml:52/127/203` 仍钉 0003"**在当前工作树已不成立**（那是 `contract surface schema 0004+` 阶段的状态）。但注意：**`docker-compose.yml` 整份是未提交改动**（见 §5），这个自洽性只存在于工作树，不存在于 HEAD。

---

## 2. 门禁结果：分四类看，不要混用

### 2.1 我本轮实跑的门禁 `[本轮实跑]`

| 门禁 | 命令 | 结果 |
|---|---|---|
| acceptance | `backend/.venv/Scripts/python.exe -B scripts/verify_hydraulic_stack.py --gate acceptance --ac ac1,ac2,ac3,ac4,ac5,ac6,ac7,ac8 --evidence-dir .runtime/evidence/dr-r1-acceptance` | `HYDRAULIC_STACK_STATUS=BLOCKED` / `HYDRAULIC_STACK_EXIT=2` / artifact `af0968b5db13f349501edbe13655a50948843b5632254ecfb15c30944c4c8865` |
| G6 config · portable | 同上 `--gate g6 --mode config --profile portable --evidence-dir .runtime/evidence/dr-r1-portable` | `HYDRAULIC_STACK_STATUS=PASS` / `EXIT=0` / artifact `d8552de5b8304bd548c10e96c180fd0976006671f0a4b9a5d979b082c0938b78` |
| G6 config · postgis | 同上 `--gate g6 --mode config --profile postgis --evidence-dir .runtime/evidence/dr-r1-postgis` | `HYDRAULIC_STACK_STATUS=FAIL` / `EXIT=1` / artifact `0acb72142cb9ddc0423e792c6e54db71ef77fd856e10dce4f2c21d6d0f7f31d3` |
| acceptance 用例收集 | `backend/.venv/Scripts/python.exe -m pytest backend/tests/acceptance -q --collect-only` | `65 tests collected in 0.28s`（**只收集，未执行**） |

**G6 portable 报告自带的限定（必须一起读，否则会误判）**：

```json
"scope_note": "Compose config is a configuration check only; it is not live G6 or AC-8 evidence."
```

**G6 postgis 的两条 `static_failures`（原样）**：

```text
postgis profile does not select an explicit PostGIS image
postgis profile renders the portable default postgres image
```

acceptance 报告里 ac1 的原始 `missing_or_invalid_artifacts`（节选）：

```text
"required_gates": ["g1","g2","g6"],
"status": "BLOCKED",
"missing_or_invalid_artifacts": ["g1","g2","g6",
  "missing artifact: .../.runtime/evidence/g1/postgis/result.json",
  "missing artifact: .../.runtime/evidence/g1/portable/result.json",
  "missing artifact: .../.runtime/evidence/g2/result.json",
  "missing artifact: .../.runtime/evidence/g6/postgis/result.json",
  "missing artifact: .../.runtime/evidence/g6/portable/result.json"]
```

**关键读法**：BLOCKED 的直接原因是 **g1～g6 的 artifact 不存在**（本轮没跑 G1～G5，也没有 live G6）。`g6/postgis` 与 `g6/portable` 的缺失**不是**我 §2.1 那两条 `--mode config` 报告能填上的——config 模式产出的 artifact 路径与 acceptance 要求的 `g6/<profile>/result.json` 不同，**不能互相顶替**。

### 2.2 `[payload]` 的 16 项 final gates（原样登记，我未复跑）

`[文档]` `VALIDATION_EVIDENCE.md:92-111` 已把 payload 逐条登记并分级。摘要如下，**请把"exitCode=0"与"AC 是否推进"严格分开**：

| # | 门禁 | exitCode | 分级 | 关键输出 |
|---|---|---|---|---|
| 1 | 后端 pytest | 0 | PASS | `797 passed, 8 skipped, 3 warnings in 61.42s` |
| 2 | Black | 0 | PASS | `113 files would be left unchanged.` |
| 3 | isort | 0 | PASS | `Skipped 3 files` |
| 4 | Flake8 | 0 | PASS | （无输出） |
| 5 | 后端导入 | 0 | PASS | `backend import ok` + pydantic `model_version` protected-namespace 警告 |
| 6 | 前端 ESLint | 0 | PASS | 无告警输出（`--max-warnings 0`） |
| 7 | 前端 Prettier | 0 | PASS | `All matched files use Prettier code style!` |
| 8 | 前端 Vitest | 0 | **PASS 但计数未留存** | 摘录里只有 `findDOMNode` deprecation 与 React Router future flag 警告，**`Tests N passed` 汇总行不在摘录内** |
| 9 | 前端 build | 0 | PASS | `✓ built in 11.99s`；`echarts-*.js 1,030.30 kB`；伴随 `end value has mixed support` 与 chunk>1000kB 告警 |
| 10 | Compose config | 0 | **PASS 但证据串为空——按第 8 项同标准降级，见下** | （证据串为空，无任何输出/计数） |
| 11 | Git diff check | 0 | PASS | 仅 `LF will be replaced by CRLF` 提示 |
| 12 | 全新 Compose build/up | 0 | PASS | `scn-hydraulic-accept-*` 11 个服务 Started/Healthy，`migrate-1 Exited`，`nginx-1 Started` |
| 13 | Compose 业务 smoke | 0 | PASS（**但响应体自相矛盾**） | `HYDRAULIC_STACK_STATUS=PASS` / `EXIT=0` / artifact `1dfbaed2…` |
| 14 | Compose 清理 | 0 | PASS | 全部 `scn-hydraulic-accept-*` Stopped/Removed，network Removed |
| 15 | 5000 节点/10000 管段 24h 基准 | 0 | **PASS 与 C-3 冲突，见 §2.4** | `HYDRAULIC_BENCHMARK_STATUS=PASS` / artifact `255fb83a…` |
| 16 | G5 浏览器 gate | 0 | **PASS 但环境不可证，见 §7.3** | `FRONTEND_PERFORMANCE_STATUS=PASS` / artifact `c1f13e13…` |

**gate 13 的内部矛盾（原样摘录，`[payload]`）**：`/readyz` 返回 HTTP 200、`ready:true`，同一响应体里却有 `"code":"RUNTIME_NOT_READY"` 与 `schema.code:"SPATIAL_BACKEND_UNAVAILABLE"`。本报告**不裁决哪个码为准**，只登记该事实。

**第 8、10 两项的一致降级（修订）**：`VALIDATION_EVIDENCE.md:103` 把 gate 8（前端 Vitest）标为「PASS，但汇总行不在摘录内，计数未在证据中留存」——这是正确的处置。**gate 10（Compose config）适用同一标准**：其证据串**一行输出都没有**，却仍被计入「16 项全绿」。两项都应视为 `exitCode=0` 但**证据不足以支撑任何结论**，引用时须写明这一点。

**gate 16 的目标环境不可证**：`[文档]` `VALIDATION_EVIDENCE.md:179` 明确记录该证据串"从 JSON 中段开始，`environment` 块不在摘录内"。结合我 §1.1 实测本机 `node v25.9.0 / physical_cores=null / ram_bytes=null`，**没有任何证据表明这次 G5 跑在冻结目标环境上**。

### 2.3 `[payload]` stages 里的 7 项非零（不得被 16 项全绿掩盖）

`[文档]` `VALIDATION_EVIDENCE.md:203-213` 逐条保留。分类如下（这个分类很重要——**3 项是"调用方式错误，测试根本没跑"**）：

| 类别 | 门禁 | exitCode | 事实 |
|---|---|---|---|
| **调用方式错误（未执行任何测试）** | simulation pytest | 4 | `ERROR: file or directory not found: backend/tests/hydraulic/test_tasks_sandbox.py`；**没有 collected、没有 passed/failed 计数**，不得读作"simulation 测试失败"或"通过" |
| **调用方式错误（未执行任何测试）** | frontend-integration vitest | 1 | `No test files found`；cwd 已是 `frontend`，filters 仍写 `frontend/src/...` |
| **调用方式错误（未执行任何测试）** | simulation worker entrypoint | 4 | `ERROR: file or directory not found: .../test_simulation_worker.py`（真实文件是 `test_simulation_worker_entry.py`） |
| **真实测试失败** | backend-acceptance-tests pytest | 1 | `7 failed, 57 passed`；证据串未给逐条原因 |
| **真实运行失败** | contract surface runtime | 1 | `/readyz` 503，根因 `_validate_forecast_artifacts` 读 `row["artifact_format"]` 触发 `NoSuchColumnError` |
| **真实门禁失败** | post-schema dependency handoff | 1 | `DEPENDENCY_HANDOFF=FAIL: result SHA-256 mismatch: backend/app/core/config.py` |
| **环境阻断** | frontend G5 remediation round 2 | 2 | `FRONTEND_PERFORMANCE_STATUS=BLOCKED`，`target_environment_mismatches:["node_version=25.9.0; expected=18.20.8","available_cores=16; expected=4","ram_bytes=59252035584; expected=8589934592"]` |

另有 3 项 `exitCode=0` 但**内含真实失败**：`solver runtime`（`2 failed, 695 passed`）、`contract surface schema 0004+`（`711 passed, 7 failed`）、`G6 artifact completion`（`2 failed, 24 passed`）；1 项 `exitCode=0` 但**证据不足**（`notification transport`，`compose=` 与 `pytest=` 两字段均为空）。

### 2.4 G5 solver 基准：PASS 与 FAIL 的直接冲突（本轮已独立取证）

| 来源 | 结论 | 证据 |
|---|---|---|
| `[payload]` gate 15 | `status:"PASS"`，`persistence` 四项全 `true`，3 次 measured run `frame_count=289`、`mass_balance_relative_residual=0.0`、`worker_wall_time` 56.96～59.59s（阈值 180s），`utc_timestamp 2026-09-27T19:01:09.770925Z` | `VALIDATION_EVIDENCE.md:140-157` |
| `[文档]` `audits/final-audit-round-2.md` C-3 | **同一 commit、同一工作树**实跑 → `FAIL / exit_code=1 / benchmark_executed=false`，`error = {"message":"executor persistence.database_committed must be true"}`，**无 measurements、无 frame_sha256** | 报告 §2 C-3，含完整命令 |
| `[本轮实跑]` | 产出该 PASS 的两个脚本**从未进入版本控制**：<br>`?? backend/app/services/hydraulic/benchmark.py`<br>`?? scripts/benchmark_hydraulic.py`<br>`git show HEAD:…` → `fatal: path '…' exists on disk, but not in 'HEAD'`（两者均是） | 见本节命令输出 |

**根因（`[文档]` C-3 的代码判定）**：`scripts/benchmark_hydraulic.py` 的 `BenchmarkFrameSink.persistence_evidence()` docstring 明写 *"it **never writes TimescaleDB**"*，恒返回 `database_committed: False`；而 `require(persistence.get(name) is True, ...)` 强制四标志为 True。**只要 sink 还是文件 sink，该门禁在任何环境下都不可能 PASS。** 这是确定性的，不是偶发。

**准确的表述（不要过度指控）**：记录人忠实抄录了当时跑到的输出，问题不是伪造，而是**证据链断裂**——一条 `status:"PASS"` 的台账记录，其生产代码从未提交，任何人（包括本轮）都无法从 commit 复现或证伪。

**处置建议**：`VALIDATION_EVIDENCE.md:154,157` 的 gate 15 记录应标为 **STALE**，在 sink 修复前从任何 AC 判读中移除。

**另有一条未被解释的差异**：`[payload]` 三次 run 的 `frame_sha256` **互不相同**（`…9e7e753b…` / `4143db54…` / `7228b025…`），即结果非逐位可复现。契约要求相同输入产生可比较结果；该差异是否被允许，证据串未说明。**这是接手工程师需要向 owner 确认的第一个问题。**

### 2.5 我本轮实跑的其他代码级取证 `[本轮实跑]`

| 检查 | 命令 | 结果 | 对应审计编号 |
|---|---|---|---|
| `durable_backend` 是否有处置 True 处 | `grep -rn "durable_backend" --include=*.py backend/` | 全仓 5 处，**无一处置 True**：`hydraulic_routes.py:311` 硬编码 `False`、`:343` 读、`main.py:1273` 读；另 2 处是 `backend/build/lib/` 下的陈旧构建副本 | C-1 |
| ownership checker 是否存在 | `ls scripts/` | 15 个文件，**无 `check_contract_ownership.py`** | C-11 |

> 附带发现：`backend/build/lib/app/**` 下有一份**陈旧的构建副本**（`hydraulic_routes.py:288`、`main.py:1169` 同样硬编码 `durable_backend = False`）。`[文档]` `VALIDATION_EVIDENCE.md:228` 也记录过同类现象。**任何 grep 统计都必须排除 `backend/build/`**，否则会重复计数。

---

## 3. FR 矩阵（功能需求，来自 `SOURCE_PRD.md`）

图例：**实现+有证据** = 代码存在且有可指认的验证产出；**实现但持久化未证** = 代码在、跑得通，但没有落库/跨重启证据；**未实现/未运行** = 无实现或本轮未执行。

| FR | 需求摘要 | 状态 | 证据 / 缺口 |
|---|---|---|---|
| **FR-1** | 管网数据层：PostGIS/portable 双路径、SWMM inp + GeoJSON 导入导出、拓扑校验报告、CRUD+bbox、树环混合种子 | **实现但持久化未证** | 代码在：`backend/app/services/hydraulic/{geometry,topology,seed,spatial_repository}.py`、`importers/`、`exporters/`；migration 0001–0008。<br>**最强缺口证据（修订，`[本轮实跑]`）**：<br>`grep -rnE "INSERT INTO (hydraulic\.)?(networks\|network_nodes\|pipe_segments\|catchments\|simulation_frames\|network_revisions)\b" --include=*.py backend scripts`（排除 `build/`）→ **零命中**。<br>即**全仓没有任何一处写拓扑表或帧表**。worker 侧那 5 条 INSERT 的真实目标是 `network_imports`(:482)、`network_job_attempts`(:275)、`network_validation_issues`(:546)、`validation_runs`(:523)、`nowcast_runs`(`nowcast_worker.py:995`)——**全是任务簿记表，不是数据表**。<br>另：合法 GeoJSON 被暂存为 `.inp`（载荷格式不随 job 携带，C-6）；staging 落进源码树且无删除路径（C-4）。<br>AC-1 **BLOCKED** |
| **FR-2** | 降雨：1/2/3/5/10/20/50/100 年重现期，1/5/15 min 步长，自定义≤72h，可复现随机雨型，情景 CRUD/复制，仅内置合成源 | **实现+有证据**（局部） | `[文档]` `implementation/delivery-docs.md` §2.8 实跑 289 点生成器（`point_index` 0→288 dense、`valid_at` 严格递增、`content_sha256` 64 位），并经 `reviews/delivery-docs.md:174-188` 独立复核逐条满足 `RainfallScenarioRequest` 校验器。<br>**缺口**：A/B 与仿真侧的引用未接通；AC-3 BLOCKED |
| **FR-3** | 仿真：EPA SWMM 优先 + 纯 Python 运动波/扩散波/简化积水降级；队列提交/进度/取消/超时/重试/幂等/并发 2/崩溃恢复；沙箱；≤5min 帧序列化；Timescale 90 天保留；RMSE/NSE | **部分实现** | **有**：`solvers/python_hydraulics.py`（`engine_sha256=8effaeb1…`，`py_mw/1.0.0`）、`tasks.py`、`sandbox.py`、`results.py`、`calibration.py`、`metrics.py`。<br>**未实现**：`py_ponding`（`[文档]` `VALIDATION_EVIDENCE.md:437` 记 NOT RUN）。<br>**未运行**：真实 EPA SWMM 二进制、`.out` 解析（`[文档]` 同上 `:438`）。<br>**证据链断裂**：G5 solver 基准见 §2.4。<br>AC-3 **BLOCKED** |
| **FR-4** | 预测告警：默认 10 min nowcast、0–2h/5 min 步长；`flood_forecast` + `pipe_surcharge`；告警含 lead_time/深度/置信度/simulation_id；告警中心/SSE/通知；预计/已发生视觉区分；Top-N 风险；30 min 抑制 | **部分实现** | **有**：`forecast_alerts.py`、`nowcast.py`、`sse_manager.py`、`frontend/src/pages/AlertCenter.tsx`。<br>**缺口**：G4 门禁 **BLOCKED**（缺 `forecast/v1/manifest.json` + `realtime/outbox-sse-v1.json` 两份冻结 fixture）；`DEPLOYMENT_ENVIRONMENT=production` 下因 `durable_backend=False` 对全部水力写路径 503；**SSE 长连的 live 行为本轮未跑**。<br>AC-4 **BLOCKED** |
| **FR-5** | API：`/api/networks` CRUD/import/validate/export/seed；rainfall-scenarios；simulations 提交/查询/取消/结果/率定/实测比对；forecast latest/horizon/risk-ranking；SSE 新 forecast 事件；JWT admin/operator | **实现但持久化未证** | **有**：`/simulation` 路由在 `frontend/src/App.tsx:77`；`[文档]` `reviews/delivery-docs.md:62` 实测路由导出 `TOTAL=98 API_TOTAL=68 ROOT_TOTAL=8`。<br>**缺口（修订措辞）**：不是笼统的「API 侧无 INSERT」——`network_jobs` 确是模型表（`backend/app/models/hydraulic_models.py:1156`）但 API 侧不写它；**更强的证据是 FR-1 行的零命中表清单**：`networks`/`network_nodes`/`pipe_segments`/`catchments`/`simulation_frames`/`network_revisions` 全仓零写入，这直接支撑"写路径不落库"。G2 门禁 **BLOCKED**（缺 3 份冻结 fixture）。<br>AC-1/AC-2 BLOCKED |
| **FR-6** | 前端：`/simulation` 菜单权限 i18n；Leaflet 管网/节点/汇水区+充满度色带；淹没面时间轴 1/2/4 倍速；降雨编辑器；任务进度/历史/取消；A/B 对比；预测实测与不确定性带；Dashboard forecast 卡片与风险榜；**万级管段千级多边形 ≥25fps**，需抽稀/视口裁剪/Canvas/预渲染 | **实现+有证据**（性能口径存疑） | **有**：`frontend/src/features/simulation/map/` 下 10 个 `.ts`（`[本轮实跑] ls frontend/src/features/simulation/map/*.ts \| wc -l` = 10）：`canvasLayer.ts`、`colorScales.ts`、`frameCache.ts`、`frameDecoder.ts`、`frameLookup.ts`、`index.ts`、`layerController.ts`、`mapData.ts`、`types.ts`、`viewport.ts`。<br>**修订（更正第 1 轮的过度对应）**：这 10 个里**只有 3 个**被 G5 报告列为 `production_modules`——`layerController`、`frameCache`、`viewport`（`VALIDATION_EVIDENCE.md:175`）。**其余 7 个（`canvasLayer.ts`、`colorScales.ts`、`frameDecoder.ts`、`frameLookup.ts`、`index.ts`、`mapData.ts`、`types.ts`）未被该门禁点名，不得计入性能证据。** 第 1 轮写"10 个模块对应 production_modules"是错的。<br>**证据**：`[payload]` gate 16 `presented_fps=49.35`、`raf_p95=34.5ms`、`long_task=0`、`cache_peak=149,632,617 B`（≈142.7 MiB < 256 MiB）——**这 3 个模块的**证据。<br>**但**：目标环境不可证（§1.1 / §2.2 / §7.3），且 `echarts` chunk 1,030.30 kB 的构建告警未处理。<br>AC-7 相关 |
| **FR-7** | 部署数据：PostGIS/Timescale 镜像或明确安装方案、无 PostGIS 降级、仿真 worker、健康检查、一键 compose；Alembic 与 `docker/init-db.sql` 结果一致；pydantic-settings 管理并发/调度/预报/保留期/求解器 | **部分实现** | **有**：`docker-compose.yml` 工作树 802 行；`config --services` 12/13/14/15（按 profile）；7 处 `HYDRAULIC_SCHEMA_REVISION=0008`；migration 0001–0008 自洽（§1.3）。<br>**缺口**：**G6 postgis 静态项 FAIL**（未选定显式 PostGIS 镜像，§2.1）；`docker/init-db.sql` 是否仍独立建表未在本文复核（`[文档]` `PRD_SUPPLEMENT.md:124` 记其为待收敛项）。<br>AC-8 **BLOCKED** |

**非功能（`SOURCE_PRD.md:31`）逐条**：

| NFR | 目标 | 状态 |
|---|---|---|
| 5000/10000/24h ≤180s @4 核纯 Python | ≤180s | **不可判定**——`[payload]` 报 56.96–59.59s 但证据链断裂（§2.4），且本机 `physical_cores=null` |
| nowcast→告警 ≤3 min | ≤180s | **未运行**（G4 BLOCKED） |
| 新增 API P95 ≤500ms | ≤500ms | **未运行**（G5 API harness 未跑） |
| 仿真故障隔离/可重跑/迁移可回滚 | — | **未运行**（live G6 BLOCKED） |
| Prometheus 暴露任务时长/失败率/队列深度 | — | **未在本报告核验** |
| 日志含 simulation_id | — | **未在本报告核验** |
| 现有 API/SSE/告警/页面零回归 | — | **未运行**（`[文档]` `VALIDATION_EVIDENCE.md:592` 记 AC-7 基线不绿） |

---

## 4. AC 矩阵（验收标准）

### 4.1 本轮唯一权威判定（**修订：措辞收紧**）`[本轮实跑]`

**8 个 AC 全部 `BLOCKED`，无一可放行。** 来源：`verify_hydraulic_stack.py --gate acceptance`，`exit_code=2`。

> **修订说明（第 2 轮）**：第 1 轮把本节标题写作「唯一权威的 **AC 判定**」，`§6.2` 表格也写「唯一权威的 AC 判定」。这个措辞过强，**已更正**。
>
> **准确的表述：这是唯一权威的「门禁 artifact 存在性」判定，不是对 AC 实质合格与否的判定。** 本次 BLOCKED 的直接原因是 `g1`～`g6` 的 `result.json` **不存在**（§2.1 机器读出的 `missing_or_invalid_artifacts`），**不是因为逐条执行 AC 判据后不合格**。
>
> **BLOCKED ≠ 实质不合格，也不 ≠ 实质合格。** 它只意味着"**在拿到 g1～g6 证据之前，任何 AC 都不能被声称通过**"——这正是门禁设计的意图。
>
> **这条判定对"跑没跑过"极度敏感**：`[本轮实跑]` `ls -1 .runtime/evidence/ | grep -c g6` = **11**（`coldread-g6-portable`、`g6-docs-r6` 等历史目录），但 `ls .runtime/evidence/g6` → **`No such file or directory`**。**acceptance 只认 `g6/<profile>/result.json` 这条固定路径**（`IMPLEMENTATION_CONTRACT.md:2471-2472`），历史证据目录再多也不算数。同理，§2.1 的 `--mode config` 产物也**不能**顶替 live G6。

| AC | artifact 存在性判定 | required_gates | 直接阻断原因（机器读出） |
|---|---|---|---|
| AC-1 合法 INP/GeoJSON | **BLOCKED** | g1, g2, g6 | g1/g2/g6 artifact 全部 missing |
| AC-2 恶意文件安全拒绝 | **BLOCKED** | g2, g6 | g2/g6 artifact missing |
| AC-3 50 年一遇 24h 回放 | **BLOCKED** | g3, g5, e2e, g6 | 同上 |
| AC-4 nowcast/告警/SSE/抑制 | **BLOCKED** | g4, g5, e2e, g6 | 同上 |
| AC-5 率定改善 | **BLOCKED** | g3, g5, e2e, g6 | 同上 |
| AC-6 A/B 差异 | **BLOCKED** | g3, e2e, g6 | 同上 |
| AC-7 质量与零回归 | **BLOCKED** | g0, e2e, g6 | 同上 |
| AC-8 全新 Compose/演示 | **BLOCKED** | g6 | g6/postgis + g6/portable missing |

`g6` 出现在**全部 8 个 AC** 的 `required_gates` 中 —— **G6 是单点锁死项**（`[文档]` C-7/C-8）。

### 4.2 冻结状态源之间的矛盾（必须先裁决，否则任何"AC-1 状态"自述都不可采信）

`[文档]` C-12 逐份实读后给出四份台账对同一批 AC 的四套说法。接手工程师**必须先处理这一条**：

| 台账 | AC-1 | AC-2 | AC-5 |
|---|---|---|---|
| `PRD_SUPPLEMENT.md:586-593` | 未实现/未运行 | 同 | 同 |
| `IMPLEMENTATION_CONTRACT.md:2547-2554` | NOT RUN（**无实现**） | NOT RUN（**无实现**） | BLOCKED+NOT RUN |
| `backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json:28` | **NOT RUN** | BLOCKED | BLOCKED |
| `docs/hydraulic-twin/CONTRACT_INDEX.md:63` | **BLOCKED** | BLOCKED | BLOCKED |
| **本轮实跑 `--gate acceptance`** | **BLOCKED** | **BLOCKED** | **BLOCKED** |

**AC-1 在 `CONTRACT_INDEX.md:63`（BLOCKED）与 `matrix.json:28`（NOT RUN）之间直接矛盾。** 另需注意 `[文档]` C-12 判定 `IMPLEMENTATION_CONTRACT.md:2547-2554` 的 8 条"无实现"理由"全部为假"（脚本、API、forecast、comparison、Docker daemon 均已存在）——**这意味着契约 §17.3 的状态表本身已过期，是必须重写的文档债**。

**方向性结论（`[文档]` C-12，本报告采信）**：台账的偏差方向是**低报**而非虚报。除 G5 子项外，**无任何台账声称 AC 通过**；`matrix.json` 8 项 `pass_nodeids` 全为空数组。

---

## 5. 文件改动摘要

### 5.1 首要事实：**没有可用的 per-commit 变更集**

| 项 | 值 |
|---|---|
| HEAD | `ca99e048` |
| 已修改的**被跟踪**文件 | **79** |
| 未跟踪文件（`-uall`） | **895** |
| `git status --porcelain` 合计 | 160 |
| `git ls-files docs \| wc -l` | `0`（`[文档]` `implementation/delivery-docs.md:518`：整棵 `docs/` 未被跟踪） |

**后果（接手工程师第一件要处理的事）**：本次交付的**全部**产物——后端、前端、migration、Compose、文档、门禁脚本——**都不在任何提交里**。无法用 `git diff` 回答"这次改了什么"，也无法用 `git log` 回溯任何设计决策。`[文档]` C-13 流程项 13 原话："一条 `status:"PASS"` 的台账记录，因为其生产代码从未提交，在半年后既无法复现也无法证伪。"

### 5.2 变更面（按目录，`[本轮实跑]` 实测）

**未跟踪（`??`）分布**——**全表由这一条命令产出**：

```bash
git status --porcelain -uall | grep '^??' | sed 's|^?? ||' | cut -d/ -f1-2 | sort | uniq -c | sort -rn | head -14
```

| 未跟踪数 | 路径 | 磁盘文件总数 | 性质 |
|---|---|---|---|
| 186 | `artifacts/hydraulic/` | 234 | 门禁产物，**未进 `.gitignore`** |
| **128** | `frontend/src/` | 164 | 真实交付代码（另 36 个为已跟踪的 `M`） |
| 111 | `docs/hydraulic-twin/` | 111 | 契约 + 实施 + 审查 + 审计（含本报告） |
| **86** | `backend/backend/` | 86 | **staging 泄漏，见 §8 R-2**（`hydraulic/import/*.inp`） |
| **64** | `backend/tests/` | 289 | 测试（另 2 个为 `M`） |
| 50 | `.runtime/evidence/` | 154 | 门禁证据，未进 `.gitignore`（**第 2 轮为 50，第 1 轮为 44**） |
| **35** | `backend/app/` | 176 | 生产代码（另 17 个为 `M`） |
| 26 | `.runtime/_finalr2/` | 30 | 审计门禁产物 |
| 25 | `frontend/e2e/` | 25 | e2e |
| 12 | `docker/hydraulic/` | 12 | 门禁 fixture |
| 9 | `artifacts/contracts/` | 9 | R0 / migration / handoff 批准工件 |
| 7 | `backend/alembic/` | — | 0004–0008 migration（**均未提交**） |

> **⚠ 第 1 轮此表是错的，已更正（B2）。** 第 1 轮用的命令是
> `git status --porcelain -uall \| awk '{print $2}' \| cut -d/ -f1-2 \| sort \| uniq -c`，
> 它**对全部 porcelain 行取第 2 字段**，因而把 ` M`（已修改已跟踪）与 `??`（未跟踪）**混在一起求和**：
> `frontend/src` 128+36=**164**、`backend/app` 35+17=**52**、`backend/tests` 64+2=**66**。
> 列头写的是「未跟踪分布」，数值却是「未跟踪+已修改」，**口径与标签不符**。
> 本轮改为上面的 `grep '^??'` 版本，并加列「磁盘文件总数」供交叉核对（`find <path> -type f \| wc -l`）。
> **`docs/hydraulic-twin` 110→111 与 `.runtime/evidence` 44→50 是真实增长**（本报告自身 + 第 1 轮的 3 个证据目录），不是统计错误。

**已修改的被跟踪文件（79，稳定）分布**：`frontend/src` 36、`backend/app` 17、`docker/*` 8、`backend/tests` 2、`scripts/*.{sh,ps1}` 4、`frontend/vite.config.ts` 1，其余零散。

### 5.3 关键未跟踪文件（**它们是当前功能的全部来源**）

```text
?? scripts/verify_hydraulic_stack.py          ← G0～G6/acceptance 唯一入口
?? scripts/benchmark_hydraulic.py             ← G5 solver harness（§2.4 冲突源）
?? backend/app/services/hydraulic/benchmark.py ← G5 solver 引擎（§2.4 冲突源）
?? backend/alembic/versions/0004..0008*.py    ← schema 0004–0008
?? docker/hydraulic-gates/compose-v1.lock.json
?? backend/tests/fixtures/hydraulic/**
```

### 5.4 文档交付物清单 `[文档]`

| 目录 | 数量 | 复核命令 | 说明 |
|---|---|---|---|
| `docs/hydraulic-twin/implementation/*.md` | 34 | `ls -1 … \| wc -l` | 33 个模块实施记录 + `python-solver-runtime` |
| `docs/hydraulic-twin/reviews/*.md` | 41 | `ls -1 … \| wc -l` | 33 个模块独立审查 + 8 个轮次/门禁摘要 |
| `docs/hydraulic-twin/audits/*.md` | **18** | `ls -1 …/audits/*.md \| wc -l` → **18** | AC-1～AC-8 各 round-1/round-2 = **16**，加 `final-audit-round-1.md` + `final-audit-round-2.md` = **2**，合计 18 |
| 顶层 `*.md` | **7** | `ls -1 docs/hydraulic-twin/*.md \| wc -l` → **7** | `SOURCE_PRD` / `PRD_SUPPLEMENT` / `IMPLEMENTATION_CONTRACT` / `CONTRACT_INDEX` / `operations.md` / `VALIDATION_EVIDENCE` / **`DELIVERY_REPORT`（本文件）** |

> **修订（B4）**：第 1 轮 audits 那格写「17」，同格括注又写「16+2=实际 18」，自相矛盾；顶层写「5」，实际是 5+`VALIDATION_EVIDENCE`+本文件=**7**。上表数字为 `[本轮实跑]` `ls | wc -l` 的直接结果，每行附复核命令。

**模块审查结论（`[文档]` `VALIDATION_EVIDENCE.md:364-402`）**：33 个模块中 **32 个 `NOT_APPROVED / BLOCKED`**，唯一 `APPROVED` 的是 `delivery-docs`（第 7 轮，2026-09-28）。`python-solver-runtime` **有实现无独立审查**（未审查，既非通过也非失败）。

**`delivery-docs` 的 APPROVED 范围（`[文档]` `reviews/delivery-docs.md:16-26`）**：批准的是"**不把未验证项写成已通过**"这一**属性**，**不是产品发布就绪**。该审查同时列出 10 medium + 3 low 文档缺陷。

---

## 6. 测试命令与结果

### 6.1 可复现命令清单（仓库根执行）

> **⚠ 修订（B3）：这份清单会改变它让你核对的数字。**
> 第 3 步的门禁命令每跑一次就往 `.runtime/evidence/` 增目录，而 `.git status --porcelain -uall` 会把它计入未跟踪数。
> 实测：第 1 轮采集时 `-uall | wc -l` = **895**，第 2 轮修订时已变为 **908**。
> **所以第 0 步的计数没有"期望值"，只有采集时点。** 第 0 步真正稳定的只有 `HEAD` 与三个工具版本；被跟踪文件 `M` 数 79 在两轮之间未变，可作弱断言。

```bash
cd smart-city-drainage

# 0) 环境事实（稳定项 vs 快照项，已在注释中标出）
git rev-parse --short=8 HEAD                                        # 稳定: ca99e048
git status --porcelain | grep -cE '^ ?M'                            # 稳定(弱): 79
git status --porcelain -uall | wc -l                                # 快照: 895 → 908（跑完第 3 步会变）
node --version                                                      # 稳定: v25.9.0（非冻结目标 18.20.8）
npm --version                                                       # 稳定: 11.12.1（非冻结目标 10.8.2）
docker --version && docker compose version                          # 稳定: 29.4.2 / v5.1.3

# 1) 权威验收判定（本报告 §4.1 的来源）
backend/.venv/Scripts/python.exe -B scripts/verify_hydraulic_stack.py \
  --gate acceptance --ac ac1,ac2,ac3,ac4,ac5,ac6,ac7,ac8 \
  --evidence-dir .runtime/evidence/dr-r1-acceptance
# → HYDRAULIC_STACK_STATUS=BLOCKED / HYDRAULIC_STACK_EXIT=2

# 2) G6 静态配置门禁（两个 profile 必须分别跑）
backend/.venv/Scripts/python.exe -B scripts/verify_hydraulic_stack.py \
  --gate g6 --mode config --profile portable --evidence-dir .runtime/evidence/dr-r1-portable
# → PASS / EXIT=0        （注意 scope_note：不是 live G6，不是 AC-8 证据）

backend/.venv/Scripts/python.exe -B scripts/verify_hydraulic_stack.py \
  --gate g6 --mode config --profile postgis --evidence-dir .runtime/evidence/dr-r1-postgis
# → FAIL / EXIT=1        （static_failures 见 §2.1）

# 3) 单元/集成测试（对应 [payload] gate 1，需自行确认）
cd backend && .venv/Scripts/python.exe -m pytest -q
cd backend && .venv/Scripts/python.exe -m pytest tests/acceptance -q   # 65 个用例
cd backend && .venv/Scripts/python.exe -m mypy app --ignore-missing-imports
```

### 6.2 结果与读法

| 检查 | 命令 | `[本轮实跑]` 结果 | `[payload]` 结果 | 读法 |
|---|---|---|---|---|
| acceptance 门禁 | 上 §1 | **BLOCKED / exit 2** | — | **唯一权威的「artifact 存在性」判定**（≠ 实质不合格，见 §4.1） |
| G6 config portable | 上 §2 | **PASS / exit 0** | — | 仅 config 渲染，非 live G6 |
| G6 config postgis | 上 §2 | **FAIL / exit 1** | — | 根 Compose 无 PostGIS 镜像变体 |
| acceptance 用例收集 | `pytest tests/acceptance --collect-only` | `65 tests collected` | — | 我**只收集、未执行** |
| 后端 pytest | `cd backend && pytest -q` | **未跑** | `797 passed, 8 skipped` in 61.42s | payload 数字，**我未复现** |
| 前端 Vitest | `npm test` | **未跑** | exit 0，**汇总行不在摘录内** | **连测试计数都没有留存，不可引用** |
| 前端 build | `npm run build` | **未跑** | `✓ built in 11.99s` | payload 数字；且在非冻结 Node 上 |
| mypy | `mypy app --ignore-missing-imports` | **未跑** | — | `[文档]` C-10：`exit 1`，1 error @ `config.py:278`（`int` vs `Literal[2]`）。**AC-7 要求 mypy exit 0，这是一票否决** |

### 6.3 `[payload]` 8 项 skips 的性质

`797 passed, 8 skipped`。`[文档]` `VALIDATION_EVIDENCE.md:447` 指出 Windows SIGTERM 优雅退出被 `@pytest.mark.skipif(sys.platform=="win32")` 跳过 —— **即"进程级优雅退出"这条语义在本平台从未被验证**。

---

## 7. Compose 与性能环境事实

### 7.1 Compose 服务集合 `[文档]`（`implementation/delivery-docs.md` §2.3，并经 `reviews/delivery-docs.md:78-79` 独立复现）

| Profile | 服务数 |
|---|---|
| 默认（无 `--profile`） | 12 |
| `production` | 13 |
| `monitoring` | 14 |
| 两者都开 | **15** |

15 个中含 `notification-sink`（`docker-compose.yml:513-567`，`expose: 8030`），它**不在** `G6_REQUIRED_SERVICES`（`scripts/verify_hydraulic_stack.py:44-58`，14 个）内 → 属"多于要求"，不是缺口。

主机端口 / expose：`backend 8000`、`frontend 3000→8080`、`grafana 3001→3000`、`nginx 8080`、`prometheus 9090`；expose `8010/8021/8022/8023/8030/8024`。

### 7.2 干净环境配方（**这是演示路径的第一步，必须逐字照抄**）`[文档]`

**`cp .env.example .env` 不足以让 `docker compose config` 通过。** 14 个必填变量中（`[本轮实跑]` 逐个 grep 确认，完整表格见 §11.2 步骤 1）：

- `.env.example` **留空 2 个**：`JWT_PRIVATE_KEY`(`:109`)、`JWT_PUBLIC_KEY`(`:110`)
- **未提供 4 个**（grep 无输出）：`POSTGRES_PASSWORD`、`GRAFANA_ADMIN_PASSWORD`、`INFLUXDB_PASSWORD`、`REDIS_PASSWORD`
- **需覆盖 1 个**：`REDIS_URL`(`:104`) 指向 `localhost`，须改成 `redis`

> **口径提醒（C1）**：上面「2 空 + 4 缺」是在「14 个 compose 必填变量」这个口径下数的。`.env.example` 全文另有 **6 个留空项**（`EPA_SWMM_EXECUTABLE:52`、`EPA_SWMM_VERSION:53`、`EPA_SWMM_SHA256:54`、`FORECAST_POLICY_CONFIG_PATH:70`、`SMTP_USERNAME:136`、`SMTP_PASSWORD:137`），它们**不在必填 14 个之内**，是应用运行期才需要的。`docker compose config` 不需要它们。

**`--env-file` 指向空文件必然 exit 1**（`--env-file` 是整体替换，空文件 = 零变量）。

**关键机制更正**（`[文档]` `reviews/delivery-docs.md` MEDIUM-7，已实测）：`docker compose` 默认从**项目目录**（第一个 `-f` 指向的文件所在目录，或 `--project-directory`）读 `.env`，**不是工作目录**。因此在子目录里执行 `docker compose -f ../docker-compose.yml` 同样会读到仓库根那份 `.env`。

**注意（MEDIUM-8）**：G6 门禁脚本自己的 `docker compose … config --quiet` **不带 `--env-file`**（见 §2.1 报告的 `steps[0].command`）。**所以 §2.1 的 portable PASS 是在"工作树存在未跟踪 `.env`"这一前提下取得的**；在真正干净的 clone 上同一条命令会 exit 1。本机当前确有该文件：`-rw-r--r-- 1 zyq 197121 3427 Sep 27 18:04 .env`。

### 7.3 性能环境事实（**这是最容易被误读的一节**）

| 维度 | 冻结目标 | `[payload]` G5 报告 | 本机实测 | 结论 |
|---|---|---|---|---|
| Node | `18.20.8` | **摘录中无 `environment` 块** | `v25.9.0` | ❌ 不可证 |
| npm | `10.8.2` | 摘录中无 | `11.12.1` | ❌ 不可证 |
| physical cores | 4 | 摘录中无 | `null`（门禁自报未取到） | ❌ 不可证 |
| RAM | 8 GiB | 摘录中无 | `null` | ❌ 不可证 |
| GPU | 无 | `disabled_software_rasterization` | — | ✅ |
| 浏览器 | 锁定 headless Chromium | `/usr/bin/chromium` / `Chrome/136.0.7103.113` / `executable_sha256=314ea0a8…` | 本机为 Windows，无该路径 | ⚠️ 该运行发生在**另一台 Linux 主机**上 |

**G5 solver 基准环境**：同样不可证。`[payload]` 给了 3 次 measured run 的 `wall_time`（56.96–59.59s）与 `cpu_time`（56.75–59.08s），但**没有 `peak_rss_bytes_after_run`**（三次均为 `null`），而 `PRD_SUPPLEMENT.md:412` 要求报告峰值 RSS。加上 §2.4 的证据链断裂，**这两组数字都不应进入任何 AC 判读**。

### 7.4 已明确声明不做宣传的能力

`[文档]` `PRD_SUPPLEMENT.md:414`：**`py_dw` / `py_ponding` 在独立 benchmark 前 `validated_scale=null`，MUST NOT 宣传 5000/10000**。本报告据此**不声称**扩散波或积水单元具备任何规模能力。

---

## 8. 真实缺口清单（按解除后解锁的广度排序）

> 编号沿用 `[文档]` `audits/final-audit-round-2.md` 的 C-nn，便于回溯。

| # | 严重度 | 缺口 | 关键取证 | 解锁 |
|---|---|---|---|---|
| **C-7** | MEDIUM·**单点锁死** | G6 live 的 SSE 探针结构上不终止：`urlopen(timeout=)` 只覆盖连接与响应头，`response.read(4096)` 对 `text/event-stream` 会永久阻塞（每 ~15s 一条 `:ping`，永不关闭） | **修订（C4）**：行号更正为 `verify_hydraulic_stack.py:1395`（`urlopen`）与 `:1397`（`response.read(4096)`）；探针注册 `:1607` 不变。`[本轮实跑] grep -n` 实测确认第 1 轮写的 1391-1393 偏了 4 行。<br>（`[文档]` 读码判定；**运行时未复现**，需起栈约 15 min） | **g6 在全部 8 个 AC 的 required_gates 中** → 不修则任何 AC 都拿不到 artifact |
| **C-8** | MEDIUM | CI 中不存在 g1/g2/g3/g4/g6 任何 job | `.github/workflows/ci.yml` 顶层 job 列表实测无这些项（`[文档]`） | 即使代码全修好，CI 也永不产生使 AC PASS 的 artifact |
| **F1** | — | G6 postgis profile 未选定显式 PostGIS 镜像 | **我本轮实跑 `--mode config` → FAIL/exit 1**（§2.1） | AC-8 |
| **C-1** | **BLOCKER** | 系统不存在"可用且持久"的运行配置：`durable_backend` 全仓无处置 True 处（我本轮已复现，§2.5）；`DEPLOYMENT_ENVIRONMENT` 默认 `development` 放行全部写路径但数据落进程内 dict，production 档则对全部水力写路径 503 —— **中间没有可用档位** | `hydraulic_routes.py:311,341-349`；`HydraulicApiStore.__init__` docstring 自述"not yet attached to a worker repo" | AC-1/3/5/6/8 共同前置 |
| **C-2** | **BLOCKER** | "测试全绿"与"AC 要求跨重启可复现"测的不是同一件事：65 个 acceptance 用例全在 `development` 下跑，而持久化守卫只在 `production` 生效 | `backend/tests/acceptance/test_acceptance_matrix.py:1-7` docstring 自述这些用例"mapped as **diagnostics only**" | 防止把 65 passed 误读为 AC 进展量 |
| **C-5** | HIGH | durable 写→读桥在 API 面整体缺失：**生产代码** `published_frames` 零写入方、`metric_units`/`better_direction` 11 处全为读、`run_calibration` 零生产调用点、`validation_runs[...]["report"]` 写死 `None` | **修订（C5）**：`published_frames` 的唯一写入点在**测试**里——`backend/tests/acceptance/test_acceptance_matrix.py:198` `item["published_frames"] = {…}`。生产侧 `hydraulic_routes.py:3346` 只初始化 `{}`、`:3485`/`:3489` 只读，**结论不翻**。第 1 轮写「零写入方」漏了这个测试写入点，属与 §2.5 同样的 grep 口径疏漏。<br>其余三项为 `[文档]` 记录的 grep。 | AC-3/5/6 |
| **C-6** | HIGH | 合法 GeoJSON 在 worker 端结构性必失败：非 seed 任务一律以 `.inp` 落盘并无条件走 `parse_swmm` | `network_job_worker.py:576,718`（`[文档]`） | AC-1；**但单独修它不推进任何 AC**（任务根本不会被 claim），须与 C-1 同轮 |
| **C-4** | HIGH | staging 默认 `./backend/hydraulic/import` 以 CWD 相对解析 → 落进 `backend/backend/hydraulic/import/`（**与 Python 包同名**），无 TTL、无容量上限、无删除路径，且 `.gitignore` 对 `hydraulic` 匹配数为 0 | **我本轮实测该目录已有 86 个 `.inp`**（§5.2），`[文档]` C-4 实测每次 acceptance 运行 +6 | 卫生 + 容量 |
| **C-9** | MEDIUM | `get_engine()` 用掩码 URL 建引擎，sqlite 配置下直接抛 `ArgumentError: Invalid SQLite URL`；G0 的 lifespan 步正是配 sqlite URL，而 fail-soft 让 `/health` 仍 200 → **"DB 完全不可用"在 G0 中被健康检查掩盖** | `backend/app/core/database.py:34-56,64-75`（`[文档]`，含运行时复现） | AC-7 / G0 有效性 |
| **C-10** | LOW | mypy exit 1（1 error @ `config.py:278`），AC-7 判据是"全部 exit 0" | `[文档]` 实跑 | AC-7 单点否决 |
| **C-11** | LOW | `scripts/check_contract_ownership.py` 不存在 → G0 结构上不可能 PASS | **我本轮实跑 `ls scripts/` 确认不存在**（§2.5） | AC-7 / G0 |
| **C-12** | MEDIUM | 四份台账对同一批 AC 给出四套说法，其中三份已过期 | §4.2 | 文档债 |
| **C-13** | LOW | 审计产物无界累积且未进 `.gitignore`（186 个 `artifacts/hydraulic` + **50** 个 `.runtime/evidence`，第 2 轮实测；`[文档]` C-3 记为 38，其后各轮审计持续增长） | 我本轮实测 §5.2 | 卫生 |

### 8.1 两条正面结论（必须写明，否则会被误读为"数据会被误删"）

1. **生产写路径无 `DELETE FROM hydraulic.*` / `TRUNCATE`；唯一一处 DELETE 在 0008 migration 的 `downgrade()`。**
   `[本轮实跑]` `grep -rn "DELETE FROM" --include=*.py --include=*.sql backend scripts docker`（排除 `.venv` 与 `build/`）的全部命中只有 3 处：
   - `backend/alembic/versions/0008_hydraulic_retention_seed.py:176` — `"DELETE FROM hydraulic.result_retention_policies WHERE table_name = :table_name"`，经 `op.execute(sa.text(...).bindparams(...))`（同文件 `:172-178`）**在 `downgrade()` 中真正执行**；
   - `backend/tests/hydraulic/test_results_calibration.py:91,363` — **测试断言**，非执行路径。

   > **修订（A1）**：第 1 轮写的绝对句「**全库无任何** `DELETE FROM hydraulic.*` 或 `TRUNCATE`」**不成立**——它与 §10.1 把 `alembic downgrade` 列为可用回滚手段自相矛盾（那条路径就含 DELETE）。准确表述是「**生产写路径无 DELETE，0008 downgrade 是唯一一处**」。

   **该修正不改变任何结论**：没有任何 worker 发出 DELETE，因此数据风险形态仍是**永不落库**（丢失）与**永不清理**（堆积），**不是误删**。并且 `0008_hydraulic_retention_seed` 迁移与 `result_retention_policies` 表**并不意味着保留期策略在运行**——没有任何运行期进程发出 DELETE。
2. **无任何 AC 被冒充为 PASS。** `matrix.json` 8 项 `pass_nodeids` 全为空数组，状态全为 `NOT RUN`/`BLOCKED`；台账偏差方向是**低报**。

---

## 9. 风险

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | **证据链断裂**（未跟踪文件 829 个，`git status --porcelain -uall \| grep -c '^??'`，第 2 轮实测；第 1 轮为 895 行含 `M`） | 任何结论在半年后既无法复现也无法证伪。已发生 1 例：G5 PASS 的生产代码从未提交 | **提交工作树**——这是所有其他修复可被第三方复现的前提 |
| R-2 | **staging 写入源码树**（`backend/backend/hydraulic/import/`，已 86 个 `.inp`） | 任何 `git add -A` 都会把它们纳入提交；已认证 operator 每次导入永久残留一份原始载荷，无上限即无界磁盘增长 | 改绝对路径或 tempdir 派生 + 读后 `unlink` + TTL/容量上限 + 纳入 `.gitignore` |
| R-3 | **"修复中的绿灯"**（`durable_backend=False` 时 65 用例全绿） | 未来任何人重跑测试都可能再次把 in-memory 结果当 AC 证据 | 已有的 `matrix_gate.py` 护栏是正确方向，修复时**不得绕过** |
| R-4 | **过期结论的破坏性 = 夸大成功的破坏性** | 本项目已连续三轮出现"上一轮的失败结论被别的 owner 改掉"（`/readyz` 503 → 200、`forecast_policy` 根因代码已不存在、`.env.example` 6 空值 → 2 空 4 缺） | 任何"已修/仍坏"都必须附重跑命令与日期；`[文档]` `CONTRACT_INDEX.md` §8 规则 14/15 |
| R-5 | **G5 数字被误用** | 若把 `56–59s` 写进 AC-3 报告，即构成虚假验收 | sink 修复前，§2.4 的数字一律标 STALE |
| R-6 | **G6 门禁挂起会吃掉整轮工时** | C-7 使 g6 不可终止；`[文档]` 记录曾观测到 13 分钟挂起 | **先修 C-7 再谈"跑 g6 拿证据"** |
| R-7 | **`docker-compose.yml` 未提交且整体位移过**（HEAD 190 行 → 工作树 802 行） | 文档中的 Compose 行号只在特定工作树状态成立 | 提交后重新取行号 |
| R-8 | **非目标 Node 上跑前端门禁** | 本机 `v25.9.0`，G5 目标 `18.20.8`；Vitest/ESLint/Prettier/build 的绿灯不满足冻结目标 | 装 Node 18.20.8 + npm 10.8.2 后重跑 |
| R-9 | **`.env.example` 模板缺陷** | 2 空 + 4 缺 + 1 需覆盖；`cp .env.example .env` 不自洽 | config owner 补齐（`[文档]` `implementation/delivery-docs.md` §8 交接项 1） |
| R-10 | **`backend/build/lib/` 陈旧副本** | grep 统计会重复计数（本报告已实测到 2 处） | 清理或加 grep 排除 |

---

## 10. 回滚

### 10.1 当前可用的回滚手段（**极少**）

`[文档]` `PRD_SUPPLEMENT.md:126` 规定生产回滚以**旧应用镜像 + additive schema** 为主，不自动 destructive downgrade。

| 层 | 可回滚性 | 说明 |
|---|---|---|
| 应用代码 | **不可回滚** | HEAD `ca99e048` 早于全部 79 个 modified + 895 个 untracked 文件。**没有"上一版可用代码"可回退** |
| 数据库 schema | **可降级** | migration 0001–0008 齐备；`[文档]` `VALIDATION_EVIDENCE.md:264-265` 实测 `alembic downgrade -1 → 0003_hydraulic_schema_gap` exit 0、`upgrade head → 0008` exit 0 |
| Compose | 可回退 | `docker compose … down` 即可；但**卷数据不随之回退** |
| 门禁/审计产物 | 可丢弃 | `artifacts/hydraulic/**`（186）与 `.runtime/evidence/**`（44）纯产物，删除无影响 |

### 10.2 回滚演练状态

| 项 | 状态 |
|---|---|
| backup → destroy → restore | **NOT RUN**（live G6 BLOCKED） |
| clone downgrade / upgrade | `[文档]` 记 exit 0，但**本报告未复跑** |
| `docker compose down -v`（破坏性卷操作） | **NOT RUN** |
| Alembic live upgrade/downgrade | **NOT RUN**（本报告） |

### 10.3 数据丢失风险

- **不会误删**（生产写路径无 DELETE/TRUNCATE；唯一 DELETE 在 0008 `downgrade()`，只在**你主动执行降级**时触发，§8.1）。
- **会丢**：`C-1` 导致 API 写路径的数据落进程内存，重启即失。当前无任何真实业务数据受影响（`networks=0`），**这是修复 C-1 的最佳窗口期**。

### 10.4 建议的回滚准备（提交前）

1. 在提交前先做一次 `git stash` 之外的**完整备份**（不要用 stash——`[文档]` `VALIDATION_EVIDENCE.md:241` 记录过 `git stash push`+`drop` 误毁未提交文件的**真实事故**，`config.py` 与 `.env.example` 均被截断过）。
2. 首次提交建议按模块切分，而不是一次性提交全部未跟踪文件（第 2 轮实测 829 个 `??` + 79 个 `M`，§5.2）。
3. 提交后立刻重算 `VALIDATION_EVIDENCE.md:314-321` 那批工件 hash，使台账第一次有可追溯出处。

---

## 11. 演示路径

### 11.1 诚实的开场结论

**当前无法完成端到端演示。** 阻断有三层，缺一不可：

1. **没有运行中的栈** —— `docker ps` 无 `smart-city-drainage-*` 容器（§1.2）。
2. **没有演示数据** —— 残留 DB 停在 `0003`、12 张表全 0 行（§1.2）；`[文档]` 记录的 11 容器栈里 `hydraulic.networks` 仅 1 行且是 notification-sink 的 `SINK-TEST` 残留，`network_revisions`/`network_nodes`/`pipe_segments`/`catchments` 全 0。
3. **写路径不落库** —— 即便起栈并灌入拓扑，导入会停在 `queued`/`committed=False`，重启后 404（`[文档]` C-1 运行时探针）。

### 11.2 现在**可以真实复现**的部分

```bash
cd smart-city-drainage

# 步骤 1（必做）构造可用的 env
# 先看「哪 14 个变量是 docker-compose 强制要求的」：
#   grep -o '\${[A-Za-z0-9_]*:?[^}]*}' docker-compose.yml | sed 's/[:?].*//;s/\${//' | sort -u
# 它们的真实状态（[本轮实跑] 逐个 grep .env.example）：
#
#   变量                      .env.example          状态
#   POSTGRES_PASSWORD         —                     未提供（grep 0 命中）
#   GRAFANA_ADMIN_PASSWORD    —                     未提供
#   INFLUXDB_PASSWORD         —                     未提供
#   REDIS_PASSWORD            —                     未提供
#   JWT_PRIVATE_KEY           :109                  留空
#   JWT_PUBLIC_KEY            :110                  留空
#   REDIS_URL                 :104                  有值但指向 localhost，须覆盖为 redis
#   DATABASE_URL              :16                   有值 ✓
#   DATABASE_URL_SYNC         :17                   有值 ✓
#   INFLUXDB_BUCKET           :103                  有值 ✓
#   INFLUXDB_ORG              :102                  有值 ✓
#   INFLUXDB_TOKEN            :101                  有值 ✓
#   JWT_ISSUER                :83                   有值 ✓
#   JWT_AUDIENCE              :84                   有值 ✓
#
# ⇒ 「2 留空 + 4 未提供 + 1 须覆盖」= 需要手写的 6 项，再加 1 项覆盖。
cp .env.example "$CLEAN_ENV"
cat >> "$CLEAN_ENV" <<'ENV'
POSTGRES_PASSWORD=<自填>
GRAFANA_ADMIN_PASSWORD=<自填>
INFLUXDB_PASSWORD=<自填>
REDIS_PASSWORD=<自填>
JWT_PRIVATE_KEY=<自填>
JWT_PUBLIC_KEY=<自填>
REDIS_URL=redis://:<REDIS_PASSWORD>@redis:6379/0
ENV
# 依据：[文档] implementation/delivery-docs.md §2.3 实测；reviews/delivery-docs.md:70-101 独立复现
# 该配方退出码 0；`cp .env.example .env` 与空文件两种写法均 exit 1
#
# 【若 `config` 仍 exit 1：C1 补】.env.example 里另有 6 个留空项，但它们**不在上面 14 个
# 必填变量里**，所以 `docker compose config` 不需要它们——若你填了上面 6 项仍报错，
# 报错行会直接点名真正缺的那个变量（首个被点名的名字取决于遍历顺序，不是稳定契约）：
#   EPA_SWMM_EXECUTABLE :52 / EPA_SWMM_VERSION :53 / EPA_SWMM_SHA256 :54
#   FORECAST_POLICY_CONFIG_PATH :70 / SMTP_USERNAME :136 / SMTP_PASSWORD :137
# 这些是**应用运行期**才需要的（EPA 探测、forecast policy、SMTP），与 compose 插值无关。

# 步骤 2 渲染校验 + 确认服务集合（12/13/14/15）
docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring \
  -f docker-compose.yml config --quiet
docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring \
  -f docker-compose.yml config --services | sort

# 步骤 3 跑两个 G6 静态门禁（这是本报告 §2.1 复现过的部分）
backend/.venv/Scripts/python.exe -B scripts/verify_hydraulic_stack.py \
  --gate g6 --mode config --profile portable --evidence-dir .runtime/evidence/demo-portable
backend/.venv/Scripts/python.exe -B scripts/verify_hydraulic_stack.py \
  --gate g6 --mode config --profile postgis  --evidence-dir .runtime/evidence/demo-postgis
# 期望：portable PASS(0) / postgis FAIL(1)
```

### 11.3 需要先修才能演示的部分（**不要现在尝试**）

| 演示动作 | 前置 | 现状 |
|---|---|---|
| 起栈后访问 `http://localhost:8080` / `:3000` | `up --build -d` **必须带 `--env-file "$CLEAN_ENV"`** | `[文档]` README 主启动命令**没带**（MEDIUM-10），照抄在干净 clone 上必失败 |
| 登录 | README 凭据依赖 `AUTH_FALLBACK_ADMIN_ENABLED=true`（development adapter 专用） | Compose 栈上必然 401，**这是预期行为不是缺陷** |
| seed 网络 → 提交 50 年一遇 24h → 回放 289 帧 | C-1 + C-6 | 导入会停在 `queued`；GeoJSON 被当 `.inp` |
| nowcast 周期 → 告警 → SSE | G4 | 缺 2 份冻结 fixture；production 档 503 |
| A/B 对比 | C-5 | 11 处 `metric_units`/`better_direction` **全为读、零写入** |
| 率定 | 真实 `external_verified` 观测 + 设备绑定 | **仓库无真实采集写入口**；`run_calibration` 零生产调用点 |

### 11.4 给接手工程师的最小可演示里程碑

按 `[文档]` C-3/C-7 的优先级，最短路径是：

1. 修 **C-7**（SSE 探针不终止）→ G6 门禁才可终止。
2. 修 **C-8**（CI 补 g1/g2/g3/g4/g6 job）→ 证据才能自动产生。
3. 修 **C-1**（API 侧真 SQLAlchemy repository + `durable_backend` 接真依赖）→ 一次改动解锁 AC-1/3/5/6/8。
4. 同轮修 **C-6**（载荷格式随 job 携带）。
5. 造一份可仿真的种子拓扑（带 revision + 节点 + 管段）—— 归 data/seed owner。
6. 修 **F1**（G6 postgis 选显式 PostGIS 镜像）。
7. **提交工作树** —— 否则以上全部不可复现。

---

## 12. 本报告未运行 / 未覆盖（**不得读作通过或失败**）

**修订（C2）**：第 1 轮此节只有"未跑"，没告诉接手人**跑它需要什么**。下表每行补「最小可跑命令」与「依据文件」。

| # | 未运行项 | 最小可跑命令 | 依据文件 / 备注 |
|---|---|---|---|
| 1 | 全量后端 pytest（`[payload]` 记 `797 passed`，**我未复现**） | `cd backend && .venv/Scripts/python.exe -m pytest -q` | 我只跑过 `--collect-only`（65 collected） |
| 2 | 65 个 acceptance 用例的**执行** | `cd backend && .venv/Scripts/python.exe -m pytest tests/acceptance -q` | `[文档]` C-2 记两次运行均 `65 passed in 4.80s`；跑前先读其 docstring（`test_acceptance_matrix.py:1-7`）知悉它们是 diagnostics only |
| 3 | 前端 vitest / lint / format / build / npm ci | `cd frontend && npm test` / `npm run lint` / `npm run format:check` / `npm run build` / `npm ci` | **需先装 Node 18.20.8 + npm 10.8.2**（本机是 25.9.0/11.12.1） |
| 4 | Black / isort / Flake8 / mypy | `cd backend && .venv/Scripts/python.exe -m black --check .` / `-m isort --check-only .` / `-m flake8` / `-m mypy app --ignore-missing-imports` | mypy 当前预期 exit 1（`[文档]` C-10：`config.py:278`） |
| 5 | 任何 Compose live 栈（build/up/down -v/fresh volume/backup→restore/全链路演示） | `docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring -f docker-compose.yml up --build -d` | 必须带 `--env-file`；配方见 §11.2 步骤 1 |
| 6 | live G6 | `… --gate g6 --profile postgis --evidence-dir …/g6/postgis`（**去掉 `--mode config`**） | 产物路径必须是 `…/g6/postgis/result.json` 才被 acceptance 认（§4.1） |
| 7 | G0 / G1 / G2 / G3 / G4 / G5 全部门禁 | 见 §6.1 与 `IMPLEMENTATION_CONTRACT.md:2462-2473` 的冻结入口 | G0 会因 `check_contract_ownership.py` 缺失而 BLOCKED（§2.5） |
| 8 | G5 的 API / nowcast / frontend 三套 harness（solver 基准本轮亦未跑） | `IMPLEMENTATION_CONTRACT.md:2514-2517` 四条固定命令 | solver 基准当前预期 FAIL（§2.4） |
| 9 | C-7 SSE 探针挂起的运行时复现 | 同 #6 的 live G6 命令，起栈约 15 min 后观察 | 仅代码判定（`:1395`/`:1397`/`:1607`），**未起栈观测** |
| 10 | 真实 PostgreSQL / TimescaleDB / InfluxDB 下的写路径行为 | `docker compose … up` 后用 `psql` 直查 | 我只对**残留的 0003 旧容器**做了只读 SELECT（§1.2） |
| 11 | 真实 EPA SWMM 二进制、`.out` 解析 | 无可跑命令——`EPA_SWMM_EXECUTABLE/VERSION/SHA256` 三项 `.env.example` 留空（`:52-54`） | `[文档]` `VALIDATION_EVIDENCE.md:438` 记 NOT RUN |
| 12 | `py_ponding` 求解器 | **无可跑命令——未实现** | `[文档]` `VALIDATION_EVIDENCE.md:437` |
| 13 | e2e 全量 / Playwright timeline 回放 | `frontend/e2e/`（25 个文件，均未跟踪）；`package.json` **无 `e2e` script** | `[文档]` `implementation/delivery-docs.md:136-137` 明确"不要引用不存在的 `npm run e2e`" |
| 14 | 8 份子审计报告内部的行号引用正确性 | 需逐条 `sed -n '<行>p' <file>` 比对 | 我只复核了 C-1～C-13 所依赖的引用 |
| 15 | FR-1～FR-7 的逐条端到端功能验收 | 无单一命令；AC-1～AC-8 入口见 §6.1 | §3 是代码+证据盘点，**不是**端到端验收 |
| 16 | NFR「Prometheus 暴露任务时长/失败率/队列深度」 | 起栈后 `curl -s http://127.0.0.1:8000/metrics \| grep -E "hydraulic\|task\|queue"` | 指标定义见 `IMPLEMENTATION_CONTRACT.md:2173`（§14.1） |
| 17 | NFR「日志含 simulation_id」 | 起栈后 `docker compose logs backend \| grep simulation_id` | 日志契约见 `IMPLEMENTATION_CONTRACT.md:2201`（§14.2） |
| 18 | `[payload]` 16 项门禁的**任何一条** | 见 §2.2 各自的门禁名 | 全部为 `[payload]` 登记，**我未复跑**；其中 gate 8、gate 10 证据串本身不含汇总行/输出 |

---

## 13. 与既有台账的关系

| 台账 | 本报告的处置 |
|---|---|
| `VALIDATION_EVIDENCE.md:154,157`（gate 15 `status:"PASS"`） | **标为 STALE**，依据见 §2.4（我本轮已独立复现其生产代码 untracked） |
| `IMPLEMENTATION_CONTRACT.md:2547-2554`（AC 表 8 条"无实现"） | **已过期**（`[文档]` C-12 判定 8 条理由全为假），需 owner 重写 |
| `PRD_SUPPLEMENT.md:586-593`（AC 表"未实现/本轮未运行"） | **已过期** |
| `CONTRACT_INDEX.md:63`（AC-1=BLOCKED）vs `matrix.json:28`（AC-1=NOT RUN） | **直接矛盾，未裁决**，本报告采用实跑结果（BLOCKED），矛盾需 owner 处理 |
| `reviews/delivery-docs.md`（唯一 APPROVED） | 其 APPROVED 范围是"不把未验证项写成已通过"这一属性，**不是产品就绪**；该审查列出的 10 medium + 3 low 文档缺陷本报告未逐条处置 |

---

## 14. 交接清单（给下一位工程师）

**先做这三件，其余才有意义：**

1. **提交工作树。** 895 untracked / 79 modified，HEAD 仍是 `ca99e048`。不提交 = 任何结论不可复现（R-1，已发生 1 例实证）。
2. **裁决 AC 台账矛盾**（C-12），并把 `IMPLEMENTATION_CONTRACT.md:2547-2554`、`PRD_SUPPLEMENT.md:586-593` 的过期状态表重写。
3. **给 G5 solver harness 一个真写 `simulation_frames` 的 sink**（走 `results.py` 的 `ResultRepository.write_frame`），或加 `--sink database --dsn` 并在无 DSN 时 fail-closed。**在此之前，G5 的任何数字都不得用于判读 AC-3。**

**需要 owner 回答的两个问题（本报告无法自行裁决）：**

1. `[payload]` 三次 solver run 的 `frame_sha256` **互不相同** —— 契约要求的"相同输入产生可比较结果"是否允许这种非逐位可复现？（§2.4）
2. G6 live 的 `backend-sse-events` 探针**是否应改为"只读响应头"**？当前实现在 `text/event-stream` 上会永久阻塞，使 g6（8/8 AC 的必交门禁）不可终止。（C-7）
