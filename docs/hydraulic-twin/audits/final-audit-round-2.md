# 跨模块最终审计 · round 2

> 审计人：跨模块最终审计员-2（全新上下文、只读）
> 审计时间：2026-09-28（系统 UTC 读数 2026-09-27T21:29Z 起，同一工作树）
> 基线：`git rev-parse --short=8 HEAD` = `ca99e048`；`git status --porcelain | wc -l` = 160；`-uall` = 868
> 输入：本轮 8 份子验收 JSON（AC-1…AC-8 round 2）+ `docs/hydraulic-twin/audits/` 全部 17 份报告 + 代码/证据实跑
> **本审计不采信任何子审计的「通过」结论。** 单模块通过 ≠ 系统通过；下列每一条 finding 均由本审计独立实跑或独立读码得到。

---

## 1. 最终结论

**8 个 AC 全部 BLOCKED，系统级验收不通过。不得放行任何一个 AC。**

本审计独立实跑了 8 个 AC 的冻结验收入口，全部 `status=BLOCKED / exit_code=2`：

```
for ac in ac1..ac8: verify_hydraulic_stack.py --gate acceptance --ac $ac
→ ac1..ac8 全部 status=BLOCKED, exit=2
```

但**系统级结论比「8 个 AC 都 BLOCKED」更严重**，有三条本轮新发现、且互相独立于任何单个子审计的发现：

1. **不存在「可用且持久」的部署配置。** 非 production 环境（默认值即 `development`）放行全部写路径，数据落进程内 dict、重启即全失；production（`docker-compose.yml:131`）因 `durable_backend=False` 硬编码而对所有水力写路径 503。这是一个**二选一的空档**，不是一个可以靠"补一点"收敛的缺口。
2. **"绿灯"测试信号测的是一个重启即消失的内存实现。** 65 个 acceptance 用例全部在 `DEPLOYMENT_ENVIRONMENT=development` 下跑，而 `_require_durable_api_store()` 只在 `production` 生效 —— 也就是说，产生全部绿色信号的那个环境，恰好是唯一不做持久化守卫的环境。**测试全绿与 AC 判据要求的"跨重启可复现"是正交的。**
3. **G5 性能门禁存在已被自身整改推翻的历史绿灯。** `VALIDATION_EVIDENCE.md:154,157` 登记 gate 15 `persistence` 四项全 `true` / `status:"PASS"`，而本审计在**同一 commit、同一工作树**实跑得到 `FAIL / exit 1`。且产出该 PASS 的两个脚本 `backend/app/services/hydraulic/benchmark.py` 与 `scripts/benchmark_hydraulic.py` **均为 untracked（`??`）**，即该 PASS 记录所依据的代码从未进入版本控制。

**关于"是否有勾选冒充通过"：本审计未发现任何一个 AC 被冒充为 PASS。** 冻结 `matrix.json` 8 项全为 `NOT RUN`/`BLOCKED` 且 `pass_nodeids` 全为空数组；acceptance 测试模块 docstring 自述"intentionally use the process-local adapter ... mapped as diagnostics only"。台账的偏差方向是**低报**（说"无实现"）而非虚报，唯一一处虚高是 G5 benchmark 的子项结果。

---

## 2. 跨模块 Findings

### C-1 【BLOCKER·本轮新发现】系统不存在"可用且持久"的运行配置

**证据（本审计实跑）**

| 检查 | 命令 / 读码 | 结果 |
|---|---|---|
| 持久化开关 | `grep -rn "durable_backend" --include=*.py backend/` | **仅 3 处**：`hydraulic_routes.py:311` `self.durable_backend = False`（硬编码）、`:343` 读、`.venv` 外 `main.py:1273` 读。**全仓（含 yml/env/json/compose）无任何置 True 处** |
| 守卫条件 | `hydraulic_routes.py:341-349` | `if settings.DEPLOYMENT_ENVIRONMENT == "production" and not durable_backend: raise 503` |
| 环境默认值 | `grep -rn DEPLOYMENT_ENVIRONMENT backend/app/core/config.py` | 默认 **`development`**（`:441`） |
| 部署实际值 | `grep -n DEPLOYMENT_ENVIRONMENT docker-compose.yml` | `:131 DEPLOYMENT_ENVIRONMENT: production` |

**运行时实测（本审计自写探针，走真实 HTTP）**

```
POST /api/networks/import (network_valid.geojson)  → 202
  import_id=7f45730e…  job_id=4e5edbd7…
GET /api/network-imports/{id}  → 200 {"counts":{"features":2}} committed=False status=queued
GET /api/networks              → items=0
（模拟进程重启：reset_hydraulic_api_store()，该函数 docstring 自述 "for isolated API tests and explicit app restarts"）
GET /api/network-imports/{id}  → 404 NETWORK_IMPORT_NOT_FOUND
```

**影响面（一次探针同时坐实 4 个子审计的核心结论，均为本审计独立复现）**

| 现象 | 对应子审计结论 | 本审计是否独立确认 |
|---|---|---|
| 导入永不离开 queued、`committed=False` | AC-1 G4 | ✅ 是 |
| 已接受的导入记录重启后 404，无 durable 回流 | AC-6「durable 结果未回流」 | ✅ 是 |
| `counts` 仅 `{"features":2}`，无 stable key | AC-1 G5 | ✅ 是 |
| 暂存文件 `.inp` 跨"重启"存活，无清理 | AC-2 N-1 | ✅ 是 |

**为什么这是 BLOCKER 而非"多个小缺口叠加"**：dev/test 档放行但重启即失 → 表现为"能跑、测试全绿、但数据不落地"；production 档 503 → 表现为"功能不存在"。两个档位各自都**不能**满足 AC 判据中的任一条 durability 要求（`acceptance_requirements.json` AC-1 明确要求 "durable import job reaches a terminal state"、AC-6 要求 "comparison remains reproducible after API and worker restart"）。中间没有可用档位。

**代码自证**：`HydraulicApiStore.__init__` docstring（`hydraulic_routes.py:300-307`）自述 "resources not yet attached to a worker repo / A production integration can replace it with SQLAlchemy repositories"。即这是**已知的、自陈的占位实现**，不是被掩盖的缺陷 —— 这一点应记在实现方的账上，但也意味着"补齐 bridge"是从零接线，不是改一个开关。

---

### C-2 【BLOCKER·本轮新发现】"测试全绿"与"AC 要求跨重启可复现"测的不是同一件事

**证据**

- `backend/tests/acceptance/test_acceptance_matrix.py:1-7` 模块 docstring（本审计实读）：

  > "The HTTP cases below intentionally use the process-local adapter where durable wiring is absent. Those cases are named and mapped as **diagnostics only**. The matrix gate binds every diagnostic to the current pytest session and **refuses to treat an in-memory result as AC evidence**."

- 本审计实跑：`cd backend && .venv/Scripts/python.exe -m pytest tests/acceptance -q` → **`65 passed` in 4.80s**（与 AC-1、AC-2 报告的计数一致，可复现）。
- 但 acceptance 用例运行时 `DEPLOYMENT_ENVIRONMENT=development`（探针实测打印 `DEPLOYMENT_ENVIRONMENT = development`），而 `_require_durable_api_store()` 在该档位**完全不生效**（见 C-1）。

**结论**：65 passed 是真实且可复现的，但它衡量的是"HTTP 契约形状 + 预检 fail-closed + 幂等键"，**不衡量任何一个 AC 的持久化判据**。任何把这 65 当作 AC-1/3/5/6 进展量的读法都是错的。子审计 AC-1/AC-2 引用 65 passed 时是作为"零回归"证据使用的，用法正确；但**整个 PR/交付叙述若把它当作"AC 在推进"的量度则是错的**，本审计在此明确切断这一推论。

**正面记录**：模块 docstring 与 `tests/acceptance/matrix_gate.py` 的存在说明实现方**主动设置了防误判护栏**。这是本轮少数值得肯定的工程决策。

---

### C-3 【BLOCKER·本轮新发现】G5 性能门禁的真红，以及一条无法复现的历史绿灯

**本审计独立实跑（完整命令，非替代检查）**

```
backend/.venv/Scripts/python.exe scripts/benchmark_hydraulic.py \
  --fixture backend/tests/fixtures/performance/solver/5k_10k_24h_v1/manifest.json \
  --warmup 1 --repeats 3 --cpus 4 --memory 8g --threads 4 \
  --output .runtime/_finalr2/solver.json
```

```
status               = FAIL
exit_code            = 1
benchmark_executed   = False
error                = {"message":"executor persistence.database_committed must be true","type":"BenchmarkError"}
measurements         = 不存在        frame_sha256 = 不存在
run_id               = 20260927T212948Z-ca99e048
HYDRAULIC_BENCHMARK_ARTIFACT_SHA256 = f2117b121acb3f1274770678e400661eacc96d3c25324a8074911fdb9eaff4c8
```

**根因（代码判定，非环境偶发）**

- `backend/app/services/hydraulic/benchmark.py:273-306` `_sink_persistence_evidence`：向 sink 索取真实证据，缺则 `database_committed: False`（**方向正确** —— 注释明写替换掉"used to be literal True values, which made the harness check vacuous"）。
- `scripts/benchmark_hydraulic.py:481-497` `BenchmarkFrameSink.persistence_evidence()`：docstring 明写 "it **never writes TimescaleDB**"，恒返回 `database_committed: False, persistence_target: "file"`。
- `scripts/benchmark_hydraulic.py:614-624` `require(persistence.get(name) is True, ...)` 四个标志强制为 True。

三者相乘 ⇒ **只要 sink 仍是文件 sink，该门禁在任何环境下都不可能 PASS**。这是**确定性**的，不是 DB 缺失导致的偶发。AC-3 报告称 round-1 为 PASS/exit 0/81.56s，本审计**未复跑 round-1**（无法在同一 commit 复现，见下），但本审计确认当前状态为确定性 FAIL。

**历史绿灯不可复现 + 无版本控制出处**

- `docs/hydraulic-twin/VALIDATION_EVIDENCE.md:154,157`（本审计实读）仍登记：gate 15 `persistence` 三次均 `all_frames_committed/database_committed/published/quality_report_committed = true`、`sink_finalized: true`、`status: "PASS"`、`utc_timestamp 2026-09-27T19:01:09.770925Z`。
- 本审计在同 commit `ca99e048`、同工作树实跑得 `FAIL / exit 1`。
- 加重情节（本审计新增取证）：`git status --porcelain -- backend/app/services/hydraulic/benchmark.py scripts/benchmark_hydraulic.py` → 两者**均为 `??` untracked**；`git show HEAD:<path>` 对两者**均无输出**（HEAD 上不存在这些文件）。

**准确表述（避免过度指控）**：这**不是**伪造证据的指控 —— 记录人忠实抄录了他们当时跑到的输出。问题是**证据链断裂**：一条 `status:"PASS"` 的台账记录，其生产代码从未进入版本控制，因此任何人（包括本次审计）都无法从 commit 复现或证伪它，只能在某个漂移的工作树上重新测。这使得 `VALIDATION_EVIDENCE.md` 整体**不能作为可信来源被引用**，其中 16 项 final gate 的 `exitCode=0` 登记同理需要逐条重取。

---

### C-4 【HIGH·本轮新发现】暂存目录的 CWD 相对默认值把已接收载荷写进源码树，量级为子审计报告的 43 倍

**证据（本审计实跑测量）**

```
config.py:271   HYDRAULIC_IMPORT_ROOT: str = "./backend/hydraulic/import"
```

该默认值以 `./` 开头，**相对进程 CWD 解析**。所有测试调用方式均为 `cd backend && pytest ...`，因此实际落点是 `backend/backend/hydraulic/import/` —— 源码树内部。

| 测量 | 结果 |
|---|---|
| `ls backend/backend/hydraulic/import/*.inp \| wc -l`（本审计首次运行 pytest **之前**） | **86** |
| 一次 `pytest tests/acceptance -q`（65 passed, exit 0）后的 delta | **+6** |
| 目录体积 | 174 KB（92 文件时） |
| 文件 mtime 分布 | 全部 2026-09-28，即全部产生于本轮审计期间 |
| `.gitignore` 对 `hydraulic` / `.runtime` / `*.db` 的匹配数 | **0 / 0 / 0**（仅有 `backend/test.db` 一条） |
| 清理路径 | `grep -n "unlink\|rmtree\|os.remove" backend/app/workers/network_job_worker.py` → 仅 `:941 marker.unlink()`（启动探针标记），**staging 无任何删除路径** |

**与 AC-2 N-1 的关系（确认 + 更正）**：AC-2 报告的根因（无清理）与量级（2 文件 @ `backend/hydraulic/import/`）方向正确但**位置与量级均需更正**。本审计的 before/after 计数是决定性证据：默认路径 `./backend/hydraulic/import` 在标准调用方式下根本不会落到 `backend/hydraulic/`，因此 AC-2 清理掉的那 2 个文件不是主泄漏点，**主泄漏点在 `backend/backend/`，且已累积 86 个**。

**为什么列为 HIGH 而非 BLOCKER**：受认证与限流约束（AC-2 探针实测恶意流量零落盘），未经认证的攻击者无法写入；但**已认证的普通 operator 每次导入都会永久残留一份原始载荷到源码树**，无 TTL、无容量上限、无条目数上限，且不在 `.gitignore` 内 —— 任何 `git add -A` 都会把它们纳入提交。在生产档（`HYDRAULIC_IMPORT_ROOT` 可由 compose 覆写）单个文件上限 20 MiB（AC-2 探针实测 `413 INPUT_TOO_LARGE` 阈值），无上限即无界磁盘增长。

**设计层面的讽刺**：应用在上传端点投入 24 个用例严防 `UNSAFE_PATH`（AC-2 实测 40 passed），却在配置默认值上使用一个**同类**的相对路径写入，且该默认值恰好使数据落入一个**与源码包同名的目录**。这类默认应改为绝对路径或 `tempfile.gettempdir()` 派生。

---

### C-5 【HIGH·跨 6 个 AC】durable 写→读桥在 API 面整体缺失（独立复核，非新增）

本审计对子审计的该结论做独立代码取证，全部确认：

| 检查 | 命令 | 结果 |
|---|---|---|
| API 是否入队 durable job | `grep -rn "INSERT INTO hydraulic.network_jobs" backend/app/` | **0**。全 `app/` 仅 5 条 `INSERT INTO hydraulic.*`，全部位于 `network_job_worker.py:275,482,523,546` 与 `nowcast_worker.py:995` —— **worker 侧有 INSERT，API 侧一条没有** |
| 校验报告是否可读 | `grep -rn 'validation_runs\[' backend/app/` | 唯一写入点 `hydraulic_routes.py:2648`，其 `"report": None` 写死在 `:2653`（另 `:2661`/`:3781` 同为 `None`） |
| 仿真结果/帧是否有写方 | `grep -rn "published_frames" backend/app/` | 仅 3 处：`:3346` 初始化 `{}`、`:3485` 读守卫、`:3489` 读 —— **零写入**；`"result": None` 在 `:1098`/`:3344` 初始化，无后续赋值 |
| A/B 对比是否落库 | `grep -rn "metric_units\|better_direction" backend/app/` | 11 处**全为读取/比较**（`:3641-3671`），**零写入**；`AbComparison` ORM（`hydraulic_models.py:3366`）无任何 service 读写 |
| 率定是否有生产调用 | `grep -rn "run_calibration" backend/app/` | 定义 `calibration.py:536` + `:1221` 导出，**零生产调用点**；`calibrations[` 唯一写入点 `hydraulic_routes.py:3785`；`backend/app/workers/` 无 calibration worker |
| 是否有数据删除路径 | `grep -rn "DELETE FROM hydraulic\.\|TRUNCATE" backend/app/` | **0 条** |

**最后一行是重要的正面结论**：**全库无任何 `DELETE FROM hydraulic.*` 或 `TRUNCATE`**。因此当前不存在"数据被误删"的风险；数据风险形态是**永不落库**（丢失）与**永不清理**（堆积），不是误删。这一点必须写明，否则读者会误以为 `0008_hydraulic_retention_seed` 迁移与 `result_retention_policies` 表意味着有保留期策略在生效 —— 实际上没有任何 worker 发出 DELETE。

---

### C-6 【HIGH】合法 GeoJSON 导入在 worker 端结构性必失败

**代码（独立读码）**

```
network_job_worker.py:576   suffix = ".json" if job_type == "seed" else ".inp"
network_job_worker.py:718   network = parse_swmm(payload, strict=claim.job_type != "validate", ...)
```

载荷格式**不随 job 携带**。任何非 seed 任务一律以 `.inp` 落盘并无条件走 `parse_swmm`。

**本审计运行时佐证**：C-1 探针提交的是 `network_valid.geojson`（`application/geo+json`），返回 202，暂存文件名 `4e5edbd7-….inp` —— **扩展名与实际格式不符**。即便 C-1 的入队缺口修好，该任务仍会在 worker 端以 `SwmmParseError` 失败。AC-1 报告的 G3 确认成立。

**注意修复顺序依赖**：G3 的修复**独立于** C-1 且成本最低，但单独修 G3 不会让任何 AC 前进（因为任务根本不会被 claim）。两者必须同轮修、且 G3 的测试必须在 C-1 修好之后才有意义。

---

### C-7 【MEDIUM】G6 live 门禁的 SSE 探针结构上不终止（代码确认，运行时未由本审计复现）

**代码（独立读码）**

```
verify_hydraulic_stack.py:1391  with urlopen(request, timeout=timeout) as response:
verify_hydraulic_stack.py:1393      body = response.read(4096).decode(...)
verify_hydraulic_stack.py:1607  _http_probe("backend-sse-events", "http://127.0.0.1:8000/api/sse/events", {200,307,401,403})
```

`urlopen(timeout=)` 只覆盖**连接与响应头**；`response.read(4096)` 对 `text/event-stream`（chunked 长连流）会阻塞直到读满 4096 字节或连接关闭。SSE 每 ~15 s 发一条 `:ping`，永不关闭、永不读满 ⇒ **`read()` 永不返回也永不抛超时**。且探针阶段无整体 deadline（`--live-timeout-seconds` 只传给 `up --wait`）。

**本审计未复现该挂起**：需要完整 Compose live 栈（约 15 分钟起栈 + 挂起），本审计未执行，记 NOT RUN。AC-8 报告的 13 分钟挂起观测本审计采信为合理推断，因代码结构与该现象完全自洽。

**影响**：G6 是 AC-1/2/3/4/5/6/7/8 **全部 8 个 AC 的必交门禁**（见 §4 门禁矩阵）。门禁不可终止 ⇒ 任何 AC 都不可能取得 G6 artifact。**这是唯一一个"堵住其他所有修复"的单点。**

---

### C-8 【MEDIUM】CI 中不存在 g1/g2/g3/g4 任何门禁 job

**本审计实跑**

```
grep -nE "^  [a-z0-9-]+:" .github/workflows/ci.yml
→ push / contents / group / cancel-in-progress / dependency-lock / host-interpreter /
  g0-quality / frontend-quality / compose-config / frontend-e2e / g5-performance / acceptance-summary
grep -n "verify_hydraulic_stack" .github/workflows/ci.yml
→ :104 :183 :197 :275 :326（5 处）
```

与子审计 AC-1 G8、AC-8 一致。而 `acceptance_requirements.json` 要求 AC-1 需 g1+g2+g6、AC-2 需 g2+g6、AC-3/4/5 需 g3/g4/g5/e2e/g6。**即使代码全部修好，CI 也永远不会产生使任何 AC PASS 所需的 artifact。** AC-7 报告指出 CI 全部用 `actions/checkout@v4` 检出 HEAD 提交树（不含当前 812+ 条工作树改动），本审计确认 HEAD 仍为 `ca99e048` 且工作树 868 个 untracked 文件 —— **CI 绿灯与当前状态无对应关系**。

---

### C-9 【MEDIUM·本轮新发现】`get_engine()` 用掩码 URL 建引擎，sqlite 配置下直接抛异常

**代码（独立读码，`backend/app/core/database.py`）**

```
:34-56   _build_safe_db_url()  —— urlparse 后 hostname 为 None（sqlite 形式）时落入重建分支，
                                 凭空注入 f"{username or ''}:{password or ''}@localhost:5432"
:64-75   get_engine()          —— create_async_engine(safe_url, ...)   ← 传的是重建后的 safe_url
                                 而非 settings.DATABASE_URL
```

**本审计实跑复现**

```
DATABASE_URL="sqlite+aiosqlite:///./test.db"
settings.DATABASE_URL    = sqlite+aiosqlite:///./test.db
_build_safe_db_url()     = sqlite+aiosqlite://:@localhost:5432/./test.db     ← 凭凭空注入
get_engine()             → sqlalchemy.exc.ArgumentError: Invalid SQLite URL
```

**为何这是跨模块问题而非 AC-7 私有问题**：G0 门禁的 lifespan 步（`verify_hydraulic_stack.py:647-658`）配置的正是 sqlite URL。该配置下引擎建不起来 → 应用走 fail-soft（"数据库不可用；仅开发/测试模式继续启动"）→ `/health` 仍返回 200 → **门禁照样 exit 0**。即"DB 完全不可用"这一失败模式在 G0 中被健康检查掩盖。当前测试套件覆盖不到，因为 `backend/tests/conftest.py` 已改用 per-session 临时库绕开了 `get_engine()` 路径。修复后必须补一条针对 `get_engine()` 本身的 URL 构造用例，否则该路径永远无人测。

---

### C-10 【LOW】mypy 仍 exit 1（AC-7 判据"全部 exit 0"的单点否决）

**本审计实跑**

```
cd backend && .venv/Scripts/python.exe -m mypy app --ignore-missing-imports
→ app\core\config.py:278: error: Incompatible types in assignment
  (expression has type "int", variable has type "Literal[2]")  [assignment]
→ Found 1 error in 1 file (checked 60 source files)
MYPY_EXIT=1
```

根因：`config.py:48 SIM_GLOBAL_CONCURRENCY_CONTRACT = 2` 无标注（推断为 `int`），`:278 SIM_GLOBAL_CONCURRENCY: Literal[2] = SIM_GLOBAL_CONCURRENCY_CONTRACT`。全仓该常量仅此两处，一行可修。

**本审计未复现 round-1 的 117 errors / 14 files**（未取得该历史基线工件）。本审计只能确认**当前为 1 error**，无法确认收敛幅度。

---

### C-11 【LOW】`scripts/check_contract_ownership.py` 不存在，G0 结构上不可能 PASS

**本审计实跑**：`ls scripts/` → 15 个文件，含 `check_dependency_handoff.py`、`check_migration_gate.py`，**无 `check_contract_ownership.py`**。

该脚本是 `IMPLEMENTATION_CONTRACT.md:2490` 明列的 G0 第一步，且 `verify_hydraulic_stack.py:569-581` 实际调用、G0 中 `blocked_exit_codes={2}` 会将其记为 BLOCKED。子审计 AC-7 N2 报告称其实跑得 `Errno 2`；本审计只做了文件存在性检查，**未实跑该步骤**。

---

### C-12 【MEDIUM·台账】四份台账对同一批 AC 给出四套说法，其中三份已过期，且互相矛盾

本审计逐份实读并与冻结 fixture 对照：

| 台账 | AC-1 | AC-2 | AC-3 | AC-4 | AC-5 | AC-6 | AC-7 | AC-8 | 本审计判定 |
|---|---|---|---|---|---|---|---|---|---|
| `PRD_SUPPLEMENT.md:586-593` | 未实现/本轮未运行 | 同 | 同 | 同 | 同 | 同 | 同 | 同 | **已过期**（脚本、API、forecast、comparison、Docker daemon 均存在且本审计实跑过） |
| `IMPLEMENTATION_CONTRACT.md:2547-2554` | NOT RUN（**无实现**） | NOT RUN（**无实现**） | NOT RUN（**无 solver/worker/frame**） | NOT RUN（**无 forecast/SSE 实现**） | BLOCKED+NOT RUN | NOT RUN（**无 comparison**） | NOT RUN | NOT RUN（**Docker daemon 不可用**） | **8 项理由全部为假**，本审计逐条证伪 |
| `matrix.json:28-178`（自陈为 source of truth） | NOT RUN | BLOCKED | NOT RUN | NOT RUN | BLOCKED | NOT RUN | NOT RUN | NOT RUN | **方向正确**；`pass_nodeids` 全为空 |
| `CONTRACT_INDEX.md:63-70` | **BLOCKED** | BLOCKED | NOT RUN | NOT RUN | BLOCKED | NOT RUN | NOT RUN | NOT RUN | **AC-1 与 matrix.json 矛盾** |
| 本审计实跑 `--gate acceptance` | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | BLOCKED | **唯一权威** |
| `VALIDATION_EVIDENCE.md:154,157` | — | — | gate 15 `status:"PASS"` | — | — | — | — | — | **不可复现**（见 C-3） |

**要点**

1. **AC-1 存在两份冻结台账的直接矛盾**：`CONTRACT_INDEX.md:63` 记 `BLOCKED`，`matrix.json:28` 记 `NOT RUN`。AC-1 报告称 `:63` 是"见到的最准确的一条 AC-1 记录"——本审计认为该判断成立，但必须同时指出它与被脚本读取的 fixture 相矛盾。**在 `CONTRACT_INDEX.md:63` 与 `matrix.json:28` 对齐之前，任何"AC-1 状态"的自述都不可采信。**
2. **偏差方向是低报，不是虚报** —— 除 G5 子项外，无任何台账声称 AC 通过。这是本轮最重要的诚实度正面结论。
3. **全部台账均 untracked**：`git status --porcelain` 对 `docs/hydraulic-twin/CONTRACT_INDEX.md` 与 `docs/hydraulic-twin/audits/` 均返回 `??`。**这些记录从未进入任何提交**，因此上述矛盾无法通过 git 历史追溯裁决。
4. **`CONTRACT_INDEX.md:64` 亦已过期**：AC-2 行记缺口为"Windows/UNC/ADS 原始 filename、重复 stable key、durable idempotency 等"，而 AC-2 报告实测 windows-drive/unc-path 现返回 `422 UNSAFE_PATH` 并通过。

---

### C-13 【LOW·卫生】审计产物在仓库内无界累积，且默认 staging 目录与包同名

**本审计实测**

```
git status --porcelain -uall | awk '{print $2}' | cut -d/ -f1-2 | sort | uniq -c | sort -rn | head
→ 186 artifacts/hydraulic
  164 frontend/src
  109 docs/hydraulic-twin
   86 backend/backend      ← C-4 的泄漏点
   66 backend/tests
   52 backend/app
   38 .runtime/evidence
   12 docker/hydraulic …
```

`.gitignore` 对 `artifacts`、`.runtime`、`hydraulic`、`*.db` 的匹配数分别为 **0、0、0、0**（唯一 `.db` 条目是 `backend/test.db`）。全部 186 个 `artifacts/hydraulic` 与 38 个 `.runtime/evidence` 文件会以 untracked 形式出现在 `git status`，任何 `git add -A` 都会纳入提交。

`backend/backend/` 这一目录同时具备两个问题：① 它是 C-4 的 staging 泄漏落点；② 它的名字与 Python 包 `backend` 同名，位于 `backend/` 之内，属于典型的相对路径误配产物。

**工作区根目录遗留物（本审计实读，位于仓库之外）**

```
C:/Users/zyq/Documents/opencode-pn/goal测试/nul      51 B   2026-09-28 05:05
    内容： dir: cannot access '/b': No such file or directory      ← Windows 空设备重定向事故
C:/Users/zyq/Documents/opencode-pn/goal测试/test.db  73728 B  2026-09-28 02:58
    magic：SQLite format 3                                         ← 真实 SQLite 库
```

二者均在仓库之外，不影响 `git status`，但说明审计过程中存在 shell 重定向失误与 sqlite 相对路径副作用。`test.db` 很可能与 C-9 的 sqlite URL 问题同源。

---

## 3. Per-AC 汇总

本审计对每个 AC 的**独立**判定。子审计结论仅作参考，不构成本审计的证据。

| AC | 冻结 required_gates | 本审计实跑 acceptance 门禁 | 子审计自评 | **本审计判定** | 本审计独立取证要点 |
|---|---|---|---|---|---|
| AC-1 | g1, g2, g6 | **BLOCKED / exit 2** | partial | **BLOCKED** | g2 实跑 exit 2；C-1 探针实测 202 后 `committed=False status=queued`、重启后 404；C-6 实测 GeoJSON 被存为 `.inp`；C-4 staging 泄漏 |
| AC-2 | g2, g6 | **BLOCKED / exit 2** | partial | **BLOCKED** | g2 实跑 exit 2（缺 3 份冻结 fixture）；C-4 把其 N-1 的落点与量级从"2 文件 @ backend/hydraulic"更正为"86 文件 @ backend/backend/hydraulic，+6/次" |
| AC-3 | g3, g5, e2e, g6 | **BLOCKED / exit 2** | blocked | **BLOCKED** | **本审计完整实跑 benchmark → FAIL/exit 1，`executor persistence.database_committed must be true`**；g3 实跑 exit 2；C-5 确认 `published_frames` 零写方 |
| AC-4 | g4, g5, e2e, g6 | **BLOCKED / exit 2** | blocked | **BLOCKED** | g4 实跑 exit 2，缺 `forecast/v1/manifest.json` + `realtime/outbox-sse-v1.json`（与子审计逐字一致）；C-1 证明 production 档 forecast 全 503 |
| AC-5 | g3, g5, e2e, g6 | **BLOCKED / exit 2** | blocked | **BLOCKED** | g3 实跑 exit 2，缺 3 份 fixture（与子审计逐字一致）；独立确认 `run_calibration` 零生产调用点、`calibrations[` 唯一写入点 `:3785` |
| AC-6 | g3, e2e, g6 | **BLOCKED / exit 2** | partial | **BLOCKED** | 独立确认 `metric_units`/`better_directions` 11 处引用**全为读取**、零写入；C-1 探针证明 result 不回流 |
| AC-7 | g0, e2e, g6 | **BLOCKED / exit 2** | partial | **BLOCKED** | **mypy 实跑 exit 1**（1 error @ `config.py:278`）；`check_contract_ownership.py` 确认不存在；C-9 独立复现 `get_engine()` sqlite 缺陷。**未复现** 803 passed / vitest / build / npm ci |
| AC-8 | g6 | **BLOCKED / exit 2** | blocked | **BLOCKED** | **`--gate g6 --profile postgis --mode config` 实跑 exit 1**；C-7 读码确认 SSE 探针不终止；C-8 确认 CI 无 g6 live job |

### 门禁依赖矩阵（`acceptance_requirements.json`，本审计实读全部 8 条）

| AC | required_gates | required_artifact_suffixes | required_observations |
|---|---|---|---|
| AC-1 | g1, g2, g6 | 5 | 4 |
| AC-2 | g2, g6 | 3 | 6 |
| AC-3 | g3, g5, e2e, g6 | 5 | 8 |
| AC-4 | g4, g5, e2e, g6 | 5 | 7 |
| AC-5 | g3, g5, e2e, g6 | 5 | 7 |
| AC-6 | g3, e2e, g6 | 4 | 6 |
| AC-7 | g0, e2e, g6 | 4 | 5 |
| AC-8 | g6 | 2 | 6 |

`evidence_state: NOT_RUN`，自述 "This file freezes evidence requirements only; it is not execution evidence and cannot make an AC PASS." —— **诚实，采纳**。

**关键依赖结构**：`g6` 出现在 **全部 8 个 AC** 的 required_gates 中。而 C-7 表明 g6 live 探针结构上不终止、C-8 表明 CI 从不运行 g6、AC-8 报告表明 g6 postgis profile 静态检查必 exit 1。**g6 单点即锁死全部 8 个 AC。**

---

## 4. 本审计实际执行的检查（可复现命令清单）

| # | 命令 | 结果 |
|---|---|---|
| 1 | `git rev-parse --short=8 HEAD` / `git status --porcelain \| wc -l` / `-uall` | `ca99e048` / 160 / 868 |
| 2 | `grep -rn "durable_backend" --include=*.py backend/`（排除 .venv） | 3 处，`:311` 硬编码 `False`，无置 True |
| 3 | `grep -rn "INSERT INTO hydraulic.network_jobs" backend/app/` | 0；全 `app/` 仅 5 条 INSERT，全在 worker 侧 |
| 4 | `grep -rn "DELETE FROM hydraulic\.\|TRUNCATE" backend/app/` | **0** |
| 5 | `grep -rn "published_frames" backend/app/` | 3 处，0 写入 |
| 6 | `grep -rn 'validation_runs\[' backend/app/` | 唯一写入点 `:2648`，`"report": None` @ `:2653` |
| 7 | `grep -rn "metric_units\|better_direction" backend/app/` | 11 处全为读 |
| 8 | `grep -rn "run_calibration" backend/app/` | 定义 2 处，0 生产调用 |
| 9 | `grep -nE "^  [a-z0-9-]+:" .github/workflows/ci.yml` | 无 g1/g2/g3/g4/g6 job |
| 10 | `cd backend && .venv/Scripts/python.exe -m pytest tests/acceptance -q` | **65 passed in 4.80s**（跑了两次，计数一致） |
| 11 | `verify_hydraulic_stack.py --gate g2/g3/g4` | 三者均 **exit 2**，missing_fixtures 与子审计逐字一致 |
| 12 | `verify_hydraulic_stack.py --gate acceptance --ac ac1..ac8` | **8/8 BLOCKED exit 2** |
| 13 | `verify_hydraulic_stack.py --gate g6 --profile postgis --mode config` | **exit 1** |
| 14 | `scripts/benchmark_hydraulic.py --fixture …/5k_10k_24h_v1/manifest.json --warmup 1 --repeats 3 --cpus 4 --memory 8g --threads 4` | **FAIL / exit 1**，`benchmark_executed=false` |
| 15 | `git status --porcelain -- <两个 benchmark 脚本>` | 均 `??` untracked；`git show HEAD:<path>` 无输出 |
| 16 | `.venv/Scripts/python.exe -m mypy app --ignore-missing-imports` | **1 error, MYPY_EXIT=1** |
| 17 | `ls scripts/` | 无 `check_contract_ownership.py` |
| 18 | sqlite URL 复现探针 | `get_engine() → ArgumentError: Invalid SQLite URL` |
| 19 | HTTP 重启丢失探针（真实 ASGI + 真实 JWT + 真实 geojson fixture） | 202 → `committed=False queued` → 重启后 404；staging 文件存活 |
| 20 | staging 泄漏 before/after 计数 | 86 → +6/次；目录 `backend/backend/hydraulic/import/`；174 KB |
| 21 | `.gitignore` 匹配计数 | artifacts 0 / .runtime 0 / hydraulic 0 / *.db 0 |
| 22 | `grep -n "unlink\|rmtree\|os.remove" network_job_worker.py` | 仅 `:941`（启动 marker），staging 无删除 |
| 23 | `acceptance_requirements.json` 全 8 条 + `matrix.json` 全 8 条状态 | 见 §3 |
| 24 | `CONTRACT_INDEX.md:63-70` / `IMPLEMENTATION_CONTRACT.md:2547-2554` / `PRD_SUPPLEMENT.md:586-593` / `VALIDATION_EVIDENCE.md:140-165` 实读 | 见 C-12 |
| 25 | 工作区根 `nul` / `test.db` 实读 | 见 C-13 |

**清理声明**：本审计只读，未修改任何被验收代码。本审计自身产生的 12 个 staging 残留（两次 acceptance 运行各 6 个）已精确删除，`backend/backend/hydraulic/import/` 文件数由 104 恢复至审计开始时的 **86**。本审计的临时探针脚本（`.runtime_restart_probe.py`）已删除。本审计门禁产物写入 `.runtime/_finalr2/`。

---

## 5. 修复优先级（本审计建议）

排序依据是**解锁下游的广度**，不是单个 AC 的接近度。

**第 0 优先 —— 解锁全部 8 个 AC**
1. **C-7** SSE 探针不终止（G6 门禁不可用）。改法：只读响应头 / 整体 deadline / 短读。**在其修复前，任何"跑 g6 拿证据"的建议都是空谈。**
2. **C-8** CI 补 g1/g2/g3/g4/g6 job。
3. **F1**（AC-8 子审计）：g6 postgis profile 选图修复 —— 本审计实测 `--mode config` exit 1。

**第 1 优先 —— 打通持久化主链（一次改动解锁 AC-1/3/5/6/8）**

4. **C-1** durable 写→读桥：API 侧真实 SQLAlchemy repository 写 job 行 + `durable_backend` 接真依赖 + API 读 durable 报告/结果/帧/对比。**这一项是 C-2 的解药，也是 AC-1/3/5/6/8 的共同前置。**
   - 注意：`_import_entities`（`hydraulic_routes.py:1326`）与 `_network_from_entities`（`:1407`）当前零调用点，需接线或删除。
5. **C-6** 载荷格式随 job 携带 + 按格式分派（成本最低，但必须在 4 之后才有可测性）。
6. **C-4** staging：`HYDRAULIC_IMPORT_ROOT` 改绝对路径或 tempdir 派生 + 读后 `unlink` + TTL/容量上限 + 纳入 `.gitignore` + 把 `test_acceptance_matrix.py` 的导入测试重定向到 `tmp_path`（对齐 `test_import_staging_contract.py:55-58` 已有的正确做法）。

**第 2 优先 —— 正确性与台账**

7. **C-3** 给 G5 harness 提供真正写 `simulation_frames` 的 sink（走 `results.py` 的 `ResultRepository.write_frame`），或加 `--sink database --dsn` 并在无 DSN 时 fail-closed。**在修复前，G5 的任何数字都不得用于判读 AC-3。**
8. **C-9** `get_engine()` 改用 `settings.DATABASE_URL` 建引擎 + 补 URL 构造用例（否则永远覆盖不到）。
9. **C-10** `config.py:48` 加 `Literal[2]` 标注。
10. **C-11** 补 `scripts/check_contract_ownership.py` + 配套测试。
11. **C-12** 同步四份台账：`CONTRACT_INDEX.md:63` vs `matrix.json:28` 的 AC-1 矛盾必须先裁决；`IMPLEMENTATION_CONTRACT.md:2547-2554` 八条"无实现"理由全部重写；`PRD_SUPPLEMENT.md:586-593` 状态列更新；`VALIDATION_EVIDENCE.md:154,157` 的 gate 15 记录标为 stale 或在修复后重取。
12. **C-13** `.gitignore` 补 `artifacts/`、`.runtime/`、`**/hydraulic/{import,sandbox,export,artifacts}/`；清理 `backend/backend/`。

**流程项（不做则以上全部无法被第三方复现）**

13. **提交工作树。** 当前 868 个 untracked 文件、160 个 dirty、HEAD 仍是 `ca99e048`。C-3 已经演示了后果：一条 `status:"PASS"` 的台账记录，因为其生产代码从未提交，**在半年后既无法复现也无法证伪**。

---

## 6. Verified（本审计亲自执行并通过）

- 8 个 AC 的冻结验收入口全部 BLOCKED/exit 2（`--gate acceptance --ac ac1..ac8`）
- G2 / G3 / G4 门禁全部 BLOCKED/exit 2，且 missing_fixtures 与对应子审计逐字一致
- G6 postgis profile 静态检查 exit 1
- G5 solver benchmark 全参数实跑：FAIL / exit 1 / `benchmark_executed=false` / 无 measurements / 无 frame_sha256
- `backend/tests/acceptance` 全量：`65 passed`，两次独立运行一致
- 无任何 AC 被冒充为 PASS：`matrix.json` 8 项 `pass_nodeids` 全空，状态全为 NOT RUN/BLOCKED
- `durable_backend` 无处置 True 处（含 yml/env/compose/json 全域）
- API 侧无任何 `INSERT INTO hydraulic.*`
- **全库无任何 `DELETE FROM hydraulic.*` / `TRUNCATE` —— 不存在误删数据路径**
- 注入后的 import 记录在模拟重启后 404（durable 不回流）
- GeoJSON 载荷被暂存为 `.inp`（格式分派缺陷运行时可见）
- staging 残留为 `backend/backend/hydraulic/import/`，86 个基线、+6/次运行
- `.gitignore` 对 artifacts/.runtime/hydraulic/*.db 均无匹配
- CI 无 g1/g2/g3/g4/g6 job
- mypy 当前仅 1 error，exit 1
- `scripts/check_contract_ownership.py` 不存在
- `get_engine()` 在 sqlite URL 下抛 `ArgumentError: Invalid SQLite URL`
- 两个 benchmark 脚本均 untracked，HEAD 上不存在
- `CONTRACT_INDEX.md` / `IMPLEMENTATION_CONTRACT.md` / `VALIDATION_EVIDENCE.md` / `audits/` 全部 untracked
- AC-1 在 `CONTRACT_INDEX.md:63`（BLOCKED）与 `matrix.json:28`（NOT RUN）之间存在矛盾

## 7. Not covered（本审计未执行，不得读作通过或失败）

- **全量 backend pytest**（子审计 AC-7 报告 803 passed / 8 skipped）—— 本审计只跑了 `tests/acceptance` 一个子集，**未复现该数字**
- **前端 vitest / build / lint / format / npm ci** —— 完全未跑；Node 版本亦未核对
- **任何 Docker Compose live 栈**（g1/g6 live、backup→destroy→restore、README 演示、fresh 卷查表）—— 未跑
- **C-7 SSE 探针挂起的运行时复现** —— 仅代码判定（读码 `:1391-1393` / `:1607`），未起栈观测
- **G5 API / nowcast / 前端浏览器三套 benchmark** —— 未跑
- **e2e ac-smoke 全量** —— 未跑
- **Playwright timeline 回放** —— 未跑
- **真实 PostgreSQL / TimescaleDB / InfluxDB** 下的任何行为 —— 未跑
- **round-1 的 mypy 117 errors / 14 files** —— 未取得该基线工件，**收敛幅度不可判定**
- **AC-3 round-1 的 benchmark PASS/exit 0/81.56s** —— 未复现；生产该结果的代码 untracked，**在当前工作树上不可复现也不可证伪**
- **`scripts/check_contract_ownership.py` 的实跑** —— 仅做文件存在性检查，未实跑该步骤（无脚本可跑）
- **8 份子审计报告内部的行号引用正确性** —— 本审计只复核了 C-1～C-13 所依赖的引用，未逐条校验 8 份报告的全部引用（AC-6 报告自述做过 22 文件全量引用自检并输出 `ALL_CITATIONS_IN_RANGE`）
- **FR-1～FR-7 的逐条功能验收** —— 本轮聚焦 AC-1～AC-8 与跨模块一致性，FR 级未系统展开
