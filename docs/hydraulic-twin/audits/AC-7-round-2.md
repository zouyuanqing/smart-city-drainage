# AC-7 独立验收（round 2）：pytest / vitest / build 与零回归

- 验收日期：2026-09-28（UTC 2026-09-27T21:0x 起算，见各命令时间戳）
- 验收人：独立验收员（只读；**未修改任何实现 / 测试 / 契约 / 台账**。唯一新增文件是本审计，以及 `verify_hydraulic_stack.py` 与 e2e runner 在其自身 evidence 目录下自动生成的运行产物）
- 仓库：`smart-city-drainage`
- 验收对象：AC-7「pytest/vitest/build/后端启动全过且旧测试零回归」（`SOURCE_PRD.md:33`）
- 上一轮：`audits/AC-7-round-1.md`（结论 `partial`）

## 0. 结论

**AC-7 = `partial`（较 round 1 明显收敛，但仍未达 `passed`）。**

判据取自 `PRD_SUPPLEMENT.md:592` 一行：

> | AC-7 质量与零回归 | Python 3.11 pytest/Black/isort/Flake8/mypy；Node 18.20.8/npm 10.8.2 的 `npm ci`/Vitest/lint/format/build；startup；旧 API/SSE characterization | **全部 exit 0；mypy 不 continue-on-error；后端可启动；旧测试零回归** | … |

本轮实测汇总（每一条都是本轮亲自运行，命令与输出见 §1）：

| 判据项 | 本轮结果 | 相对 round 1 |
|---|---|---|
| 后端 pytest 全量 | ✅ exit 0，`803 passed, 8 skipped` | round 1 `797 passed` → **+6** |
| 前端 Vitest | ✅ exit 0，`32 files / 150 tests` | 不变（150/150） |
| 前端 build | ✅ exit 0，`✓ built in 13.55s` | 不变 |
| ESLint / Prettier | ✅ exit 0 / exit 0 | 不变 |
| Black / isort / Flake8 | ✅ exit 0 / exit 0 / exit 0 | Black 计数 112 → **113**（与台账 :97 记的 113 对上了） |
| 后端启动 lifespan | ✅ exit 0，`200 {"status":"healthy"}` | 不变（但见 §2.6 新发现） |
| 旧 API/SSE characterization（in-process） | ✅ exit 0，`1 passed` | 不变 |
| 浏览器 e2e（3 场景） | ✅ exit 0，`status PASS` | 不变，`index_sha256` 与 round 1 **逐字相同** |
| **mypy** | ❌ **exit 1，`Found 1 error in 1 file`** | round 1 `117 errors in 14 files` → **−116** |
| **`npm ci`** | ❌ `NOT RUN`（`--dry-run` 即 exit 1 EBADENGINE） | 不变 |
| **Node 18.20.8 / npm 10.8.2 目标环境** | ❌ `NOT RUN`（本机 `v25.9.0` / `11.12.1`） | 不变 |
| **`scripts/check_contract_ownership.py`** | ❌ 仍**不存在**（exit 2） | **未修复** |
| **AC-7 冻结入口** | ❌ `BLOCKED / exit 2` | 不变；`required_gates` 由 `["g0","e2e"]` **收紧为 `["g0","e2e","g6"]`** |
| **「旧测试零回归」** | ⚠️ **仍不可判定**（无改动前基线工件） | 不变 |

三句话概括：

1. **单元与构建层面是绿的，而且比 round 1 更绿**：mypy 从 117 条塌到 **1 条**，`conftest.py` 的共享 `test.db` 缺陷（round 1 的 G7）**已修复**，`check_contract_ownership` 的 `required_gates` 与冻结源**已对齐**（`verify_hydraulic_stack.py:1774` 改为读 `case["required_gates"]`）。本轮**没有发现任何新的回归**。
2. **AC-7 判据是「全部 exit 0」，mypy 那唯一 1 条就仍然否掉它**。同时 `scripts/check_contract_ownership.py` 依然不存在、G0 在本机连版本强制检查都过不去，所以 G0 结构上不可能 PASS。
3. **「零回归」这一项仍然无法用证据判定**——不是"已通过"也不是"已失败"。我在测试清单层面能给出确定结论（§3），但"改动前后行为一致"缺对照基线，且工作树有 812 条未提交条目，CI 检出的是 HEAD 提交树、不是这份工作树。

---

## 1. 本轮实际执行的检查（完整命令与真实输出）

### 1.0 环境事实

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage" \
  && git rev-parse HEAD && git status --porcelain | wc -l \
  && git status --porcelain --untracked-files=all | wc -l \
  && node --version && npm --version && backend/.venv/Scripts/python.exe --version
ca99e0485fb12fbecbd565e79d81f745f8d4a748
160
812
v25.9.0
11.12.1
Python 3.11.15
```

| 目标（`PRD_SUPPLEMENT.md:592`） | 实测 | 结论 |
|---|---|---|
| Python 3.11 | `backend/.venv` = **3.11.15** | ✅ 命中 |
| Node **18.20.8** | `v25.9.0` | ❌ 不符 |
| npm **10.8.2** | `11.12.1` | ❌ 不符 |

> PATH 上的 `python` 是 `C:\Python314\python.exe`（`VALIDATION_EVIDENCE.md:42-51` 已登记）。本轮所有 Python 检查一律用 `backend/.venv/Scripts/python.exe`（3.11.15）。
>
> **工作树 dirty 数从 round 1 的 157 → 本轮 160（默认）/ 747 → 812（`-uall`）**，即本轮相对 round 1 又有新增改动。本报告结论对应**本轮运行时的当前工作树**。

### 1.1 后端 pytest 全量 —— ✅ exit 0

**按 G0 冻结入口的原样 cwd/env**（`scripts/verify_hydraulic_stack.py:606-621`：`cwd=backend`、`DATABASE_URL=sqlite+aiosqlite:///./test.db`、Redis/Influx 指向 `127.0.0.1:1`、`JWT_SECRET_KEY=g0-test-secret-not-for-production`）：

```text
$ cd ".../smart-city-drainage/backend" \
  && DATABASE_URL="sqlite+aiosqlite:///./test.db" REDIS_URL="redis://127.0.0.1:1/0" \
     INFLUXDB_URL="http://127.0.0.1:1" JWT_SECRET_KEY="g0-test-secret-not-for-production" \
     PYTHONPATH=".../backend" \
     .venv/Scripts/python.exe -m pytest tests/ -q --tb=short
........................................................................ [  8%]
...
803 passed, 8 skipped, 3 warnings in 53.87s
PYTEST_EXIT=0
```

`--collect-only` 复核：`811 tests collected`（= 803 passed + 8 skipped，自洽）。

> 差异说明（诚实标注）：G0 的 argv 是 `-v --tb=short`（`:615`），我用了 `-q --tb=short`。**cwd、env、测试路径（`tests/`）与 G0 完全一致**，只把逐用例 `-v` 换成汇总模式。**pytest 全量本身是同一检查。**

**round 1 观察到的"并发 pytest 互相 `no such table: users`"本轮未复现**，因为根因已被修掉：`backend/tests/conftest.py:22-23`

```python
TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="hydraulic-tests-"))
TEST_DATABASE_URL = f"sqlite+aiosqlite:///{(TEST_DB_DIR / 'test.db').as_posix()}"
```

会话级临时库 + `:35` 的 `shutil.rmtree(TEST_DB_DIR, ignore_errors=True)` 收尾。`git diff --stat -- backend/tests/conftest.py` = `15 insertions(+), 1 deletion(-)`。**round 1 的 G7 已修复。**

### 1.2 前端 Vitest —— ✅ exit 0（**仍在非冻结 Node 上**）

```text
$ npm --prefix frontend run test
 Test Files  32 passed (32)
      Tests  150 passed (150)
   Start at  05:07:56
   Duration  14.83s
VITEST_EXIT=0
```

与 round 1 的 `32 files / 150 tests` **逐项一致**。**但仍是 Node v25.9.0 / npm 11.12.1，不是契约冻结的 18.20.8 / 10.8.2。**

### 1.3 前端 build / lint / format —— ✅ exit 0

```text
$ npm --prefix frontend run build
dist/assets/echarts-BBjs3wlc.js  1,030.30 kB │ gzip: 341.85 kB
(!) Some chunks are larger than 1000 kB after minification.
✓ built in 13.55s
BUILD_EXIT=0

$ npm --prefix frontend run lint
> eslint . --report-unused-disable-directives --max-warnings 0
LINT_EXIT=0

$ npm --prefix frontend run format:check
All matched files use Prettier code style!
FMT_EXIT=0
```

`echarts-BBjs3wlc.js 1,030.30 kB` 与台账 `VALIDATION_EVIDENCE.md:104`、round 1 记的产物名/大小**逐字一致**。

### 1.4 后端 lint 家族 —— 3 项 PASS，mypy FAIL

```text
$ cd ".../backend"
$ .venv/Scripts/python.exe -m black --check app tests
All done! ✨ 🍰 ✨
113 files would be left unchanged.
BLACK_EXIT=0

$ .venv/Scripts/python.exe -m isort --check-only app tests
ISORT_EXIT=0

$ .venv/Scripts/python.exe -m flake8 app tests
FLAKE8_EXIT=0
```

> Black 现在是 `113 files`，与台账 `:97` 记的一致；round 1 记的 `112` 才是偏差。**round 1 的 G10 已被后续改动自然消解。**

### 1.5 ❌ mypy —— 仍 FAIL，但只剩 1 条

```text
$ cd ".../backend" && .venv/Scripts/python.exe -m mypy app --ignore-missing-imports
app\services\hydraulic\forecast_alerts.py:1401: note: By default the bodies of
  untyped functions are not checked, consider using --check-untyped-defs  [annotation-unchecked]
app\core\config.py:278: error: Incompatible types in assignment
  (expression has type "int", variable has type "Literal[2]")  [assignment]
Found 1 error in 1 file (checked 60 source files)
MYPY_EXIT=1
```

**round 1 → round 2：117 errors in 14 files → 1 error in 1 file。** round 1 标为"可能真缺陷"的两处（`system_status.py:21` 的 `attr-defined`、`network_job_worker.py:719` 的 `SeedNetwork.junctions`）**已消失**。

**剩下一条的根因（可由代码修复，是，且是一行）**：

```text
backend/app/core/config.py:48   SIM_GLOBAL_CONCURRENCY_CONTRACT = 2
backend/app/core/config.py:278  SIM_GLOBAL_CONCURRENCY: Literal[2] = SIM_GLOBAL_CONCURRENCY_CONTRACT
```

第 48 行的模块级常量没有标注，mypy 推断为 `int`；第 278 行要求 `Literal[2]`，赋值不收窄。给第 48 行加 `Literal[2]` 标注即可消除。`SIM_GLOBAL_CONCURRENCY_CONTRACT` 全仓库只在 `:48`（定义）和 `:278`（使用）两处出现（`grep -rn` 确认），改动面封闭。

**为什么这一条就仍然否掉 AC-7**：

- `PRD_SUPPLEMENT.md:592` 判据原文："**全部 exit 0；mypy 不 continue-on-error**"。
- `IMPLEMENTATION_CONTRACT.md:2490` G0 定义："全量 pytest/Black/isort/Flake8/**mypy（不可 continue-on-error）**"。
- `scripts/verify_hydraulic_stack.py:629-633` 把 `g0-backend-mypy` 作为 G0 的一步，且**没有** `blocked_exit_codes`，即 exit 1 = **FAIL**。
- ✅ **正面确认**：全仓库 `grep -rn "continue-on-error" .github/workflows/ scripts/ backend/pyproject.toml` **无任何命中**。"mypy 不 continue-on-error" 这半条判据**成立**。
- ❌ 坏消息：**已登记的"16 项 final gates 全部 exit 0"里依然没有 mypy、也没有 `npm ci`**（`VALIDATION_EVIDENCE.md:94-111` 的 16 行 = pytest/Black/isort/Flake8/后端导入/ESLint/Prettier/Vitest/build/Compose×4/基准/G5 浏览器）。那句汇总**不构成 AC-7 的证据**。**round 1 的 G8 未修复。**

### 1.6 后端启动 —— ✅ exit 0（但见 §2.6）

```text
$ cd ".../backend" && DATABASE_URL="sqlite+aiosqlite:///./test.db" ... \
  .venv/Scripts/python.exe -c "from fastapi.testclient import TestClient; from app.main import app;
  client = TestClient(app); client.__enter__(); response = client.get('/health');
  print(response.status_code, response.text); assert response.status_code == 200;
  client.__exit__(None, None, None)"
200 {"status":"healthy"}
LIFESPAN_EXIT=0
```

stderr 里有一条**上一轮没记录、这一轮必须记的**日志（原文）：

```text
初始化异步数据库引擎: sqlite+aiosqlite://:***@localhost:5432/./test.db      ← app.core.database
数据库连接失败: Invalid SQLite URL: sqlite+aiosqlite://:***@localhost:5432/./test.db
Valid SQLite URL forms are: sqlite:///:memory: … sqlite:///relative/path/to/file.db
⚠️  数据库不可用；仅开发/测试模式继续启动                                      ← app.main
```

G0 明明配置了一个**合法**的 sqlite URL，引擎却拿它建不起来，应用 fail-soft 之后 `/health` 仍然 200，于是门禁照样 exit 0。详见 §2.6（真实代码缺陷）。

### 1.7 G0 的三个静态子门禁 —— 1 PASS / 1 **仍缺文件** / 1 PASS_WITH_DEFERRED

```text
$ backend/.venv/Scripts/python.exe -B scripts/check_migration_gate.py \
    --review-artifact artifacts/contracts/v3-independent-review.json \
    --base-ref ca99e0485fb12fbecbd565e79d81f745f8d4a748 --versions-dir backend/alembic/versions
MIGRATION_PHASE=POST_MIGRATION_APPROVED
MIGRATION_GATE=PASS
MIGRATION_EXIT=0

$ backend/.venv/Scripts/python.exe -B scripts/check_contract_ownership.py \
    --contract docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md --phase post-handoff
…python.exe: can't open file '…\scripts\check_contract_ownership.py': [Errno 2] No such file or directory
OWNERSHIP_EXIT=2

$ backend/.venv/Scripts/python.exe -B scripts/check_dependency_handoff.py … （G0 原样参数）
"deferred": ["frontend-target-runtime-evidence"],
"g0_g5_blocked_by_deferred": true,
"status": "READY_WITH_DEFERRED"
DEPENDENCY_HANDOFF=PASS_WITH_DEFERRED
DEFERRED=frontend-target-runtime-evidence
G0_G5_BLOCKED_BY_DEFERRED=true
HANDOFF_EXIT=0
```

**`scripts/check_contract_ownership.py` 依然不存在。** 目录实测（`ls scripts/`）：`check_dependency_handoff.py`、`check_migration_gate.py` 在，**没有** `check_contract_ownership.py`。它仍是 `IMPLEMENTATION_CONTRACT.md:2490` 明列的 G0 第一步、`verify_hydraulic_stack.py:569-581` 实际调用。**round 1 的 G2 未修复。** G0 中它的 `blocked_exit_codes=frozenset({2})`（`:580`），所以缺文件被记为 **BLOCKED**——两种都不是 PASS。

**checker 自报 `G0_G5_BLOCKED_BY_DEFERRED=true`**，与本机 Node 25.9.0 ≠ 18.20.8 的事实一致。

> 附带事实：新文件 `backend/tests/test_contract_gate_scripts.py`（16 个用例）**只覆盖 `check_migration_gate` 与 `check_dependency_handoff`**（`:31-32` 的两个 `_load_script` 调用，grep 确认全文无 `check_contract_ownership`）。**缺失的脚本连测试都没有。**

### 1.8 `node --check` 两项 —— ✅ exit 0

```text
$ node --check frontend/performance/run-twin-map.mjs   → EXIT1=0
$ node --check frontend/e2e/run-ac-smoke.mjs           → EXIT2=0
```

### 1.9 `npm ci` —— ❌ `NOT RUN`（本机结构上不可能通过）

```text
$ npm --prefix frontend ci --dry-run
npm error code EBADENGINE
npm error engine Unsupported engine
npm error engine Not compatible with your version of node/npm: smart-city-drainage-frontend@1.0.0
npm error notsup Required: {"node":"18.20.8","npm":"10.8.2"}
npm error notsup Actual:   {"node":"v25.9.0","npm":"11.12.1"}
NPM_DRY_EXIT=1
```

`frontend/.npmrc:7` 是 `engine-strict=true`，`frontend/package.json` 的 `engines` 是 `{"node":"18.20.8","npm":"10.8.2"}`。与 round 1 完全一致。

**我仍然只跑了 `--dry-run`，没有跑真的 `npm ci`**，理由同 round 1 并在此重申：`npm ci` 会先删掉整个 `frontend/node_modules` 再重装；一旦中途失败会把本机前端依赖树弄坏，而 AC-7 ask 点名的三项（pytest / vitest / build）全部依赖这棵树。`--dry-run` 已在 engine 校验处 exit 1，真跑会在同一处终止。**⇒ `npm ci` 记为 `NOT RUN`，在目标环境（18.20.8 / 10.8.2）上同样 `NOT RUN`。round 1 的 G3 未修复。**

### 1.10 旧 API/SSE characterization —— ✅ exit 0（两层）

**第一层：in-process 线级契约**

```text
$ backend/.venv/Scripts/python.exe -m pytest \
    "backend/tests/acceptance/test_acceptance_matrix.py::test_ac7_legacy_api_and_sse_wire_contract_has_no_regression" \
    -q -p no:cacheprovider
1 passed, 1 warning in 3.28s
AC7_TEST_EXIT=0
```

**第二层：真实浏览器 e2e**（消费我 §1.3 刚 build 出的 `frontend/dist`）

```text
$ node frontend/e2e/run-ac-smoke.mjs --fixture frontend/e2e/fixtures/ac-smoke-v1.json \
    --output "C:/Users/zyq/AppData/Local/Temp/ac7-round-2-e2e.json" --run-id ac7-round-2
status PASS, exit_code 0, browser_executed true
commit ca99e0485fb12fbecbd565e79d81f745f8d4a748, dirty_state true
fixture ac-smoke-v1-current-contract sha256 4561e8a6e4ed53622e227df59c9fbd96a4a3a1b6e9f1ab613007316fe9a1797a
build  frontend/dist index_sha256 0657115cc700384734fc165a4a92abbf989bd7bbfeccbc047102665bf57c0dda
browser Chrome/154.0.8037.57, Headless
  AC-7-viewer-simulation-read-only              PASS  AC-7              2324ms
  AC-3-AC-4-AC-6-operator-simulation-workflow   PASS  AC-3,AC-4,AC-6,AC-7 2096ms
  AC-7-unauthenticated-simulation-redirect      PASS  AC-7               293ms
E2E_EXIT=0
```

**`index_sha256` 与 round 1 记的 `0657115cc7…` 逐字相同**——同一份源码两次构建产出字节一致的 `dist/index.html`。同样，**这次 e2e 也跑在 Node 25.9.0 上，不是冻结环境。**

报告写在系统 temp，**没有**写进 `artifacts/` 或 `.runtime/`，理由同 round 1 §1.11：acceptance 只认同 RUN_ID 下的成套工件，我手上没有 `g0/result.json`，落一份 e2e 报告只会制造"半套工件"。

### 1.11 AC-7 冻结入口 —— ❌ `BLOCKED` / exit 2

```text
$ backend/.venv/Scripts/python.exe -B scripts/verify_hydraulic_stack.py \
    --gate acceptance --ac ac7 --evidence-dir ".runtime/ac7-round-2-audit/acceptance"
"acceptance": [{"ac": "ac7", "artifact_paths": [],
  "missing_or_invalid_artifacts": ["g0", "e2e", "g6", …],
  "required_gates": ["g0", "e2e", "g6"],
  "status": "BLOCKED"}]
"status": "BLOCKED", "exit_code": 2, "run_id": "20260927T211544Z-ca99e048"
HYDRAULIC_STACK_STATUS=BLOCKED
HYDRAULIC_STACK_EXIT=2
ACC_EXIT=2
```

**本轮相对 round 1 的一处实质变化（对 AC-7 不利）**：`required_gates` 由 `["g0","e2e"]` **收紧为 `["g0","e2e","g6"]`**。三份来源现在一致：

- `backend/tests/fixtures/hydraulic/acceptance/v1/acceptance_requirements.json:123` → `["g0","e2e","g6"]`
- `backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json:162` → `["g0","e2e","g6"]`
- 脚本 `verify_hydraulic_stack.py:1774` 现在 `gates = case.get("required_gates")`（从 case 读，而不是硬编码）

`audits/final-audit-round-1.md:87` 记的 X 项（"脚本单方面把 g6 从 AC-1~AC-7 删掉"）**已修复**。但代价是：**AC-7 现在还要求 `g6/postgis/result.json` 与 `g6/portable/result.json`**，也就是 AC-8（双 profile Compose）的产物。AC-7 与 AC-8 之间新增了一条硬依赖。

即使我把 §1.1–§1.8 手工拼起来，**G0 仍过不了**（缺 `check_contract_ownership.py`、mypy exit 1、`npm ci` 在目标环境未验证、本机 Node/npm 被 `enforce_version_step` 硬判 FAIL）。**我不把"手工拼起来的绿"当作门禁通过。**

### 1.12 我**没有**跑的

| 未跑项 | 为什么 |
|---|---|
| `verify_hydraulic_stack.py --gate g0` 完整入口 | 其第 12 步是 `npm ci`（§1.9）。更关键：本机 Node 25.9.0 会被 `enforce_version_step(node_version_step, "v18.20.8")`（`verify_hydraulic_stack.py:534-540`）**硬判 FAIL**，npm 同理。跑了也只能得到一个必然的 FAIL，且有毁掉 `node_modules` 的风险。**我改为逐条跑它的 14 个子步骤，只跳过 `npm ci` 与两个版本强制步骤。这是替代，不是同一检查。** |
| 真 `npm ci` | 见 §1.9。 |
| Node 18.20.8 / npm 10.8.2 上的任何前端命令 | 本机 Node 25.9.0；切换版本管理器属于改本机状态，且 `README.md:1149-1151` 把它列为用户操作。 |
| G5 浏览器性能门禁（`test:perf:twin`） | `check_dependency_handoff.py` 自报 `G0_G5_BLOCKED_BY_DEFERRED=true`。非本 ask 点名项，本轮未跑。 |
| G1~G6 / 双 profile Compose | 属 AC-8 范围。**注意 AC-7 现在经 `required_gates` 依赖 `g6`（§1.11），但我按 ask 只做本审计，未替 AC-8 代跑。** |
| CI 实跑 | `.github/workflows/ci.yml` 全用 `actions/checkout@v4`（`:29,61,82,122,172,217,256,300`），检出的提交树不含本工作树的 812 条改动（见 §3.2）。 |

---

## 2. 逐条发现（证据 / 缺口 / 可否由代码修复）

### 2.1 【PASS】单元与构建全绿

**证据**：§1.1（`803 passed, 8 skipped`, exit 0）、§1.2（`150 tests`, exit 0）、§1.3（build/lint/format 三项 exit 0）、§1.4（Black/isort/Flake8 三项 exit 0）。
**缺口**：pytest/vitest/build 跑在 **Node 25.9.0 / npm 11.12.1**，非冻结的 18.20.8 / 10.8.2。
**可由代码修复**：❌ 不是代码问题——是环境问题。需在目标运行时重取证据。

### 2.2 【FAIL】mypy 仍 exit 1（1 条）

**证据**：§1.5，`Found 1 error in 1 file (checked 60 source files)`，`app/core/config.py:278`。
**缺口**：AC-7 判据"全部 exit 0"直接被否。
**可由代码修复**：✅ **是，一行**。`backend/app/core/config.py:48` 加 `Literal[2]` 标注（该常量全仓库仅 `:48` 定义、`:278` 使用两处，改动面封闭）。修完 mypy 归零。

### 2.3 【BLOCKED】`scripts/check_contract_ownership.py` 仍然不存在

**证据**：§1.7，exit 2 `[Errno 2] No such file or directory`；`ls scripts/` 无此文件；`verify_hydraulic_stack.py:569-581` 仍在调它；`IMPLEMENTATION_CONTRACT.md:2490` 仍把它列为 G0 第一步。
**缺口**：G0 结构上不可能 PASS。`backend/tests/test_contract_gate_scripts.py` 只覆盖另外两个 checker（`:31-32`），**这个脚本连测试都没有**。
**可由代码修复**：✅ **是**（补交脚本；owner 在 `IMPLEMENTATION_CONTRACT.md:2356` 已指定为 `ci-quality-bootstrap`），**但需要设计输入**：脚本要验证"effective owner table 每个 path 恰一个 owner"，而 owner table 的解析规则写在契约里，属于需要实现的规格，不是补一个空壳能过的。

### 2.4 【NOT RUN】`npm ci` 与目标运行时

**证据**：§1.9，`npm ci --dry-run` → exit 1 `EBADENGINE`；本机 `node v25.9.0` / `npm 11.12.1` vs `frontend/package.json` `engines {"node":"18.20.8","npm":"10.8.2"}`、`frontend/.node-version` = `18.20.8`、`frontend/.npmrc:7` `engine-strict=true`。
**缺口**：AC-7 判据明列 `npm ci`，**零证据**。
**可由代码修复**：❌ 环境问题。CI 侧其实已正确接线（`.github/workflows/ci.yml:88-90` 用 `actions/setup-node` + `node-version-file: frontend/.node-version`，`:94-99` 还会 `test "$(node --version)" = "v18.20.8"` / `test "$(npm --version)" = "10.8.2"` 硬断言）——**缺的只是一次真实 CI run 的证据**。

### 2.5 【BLOCKED】AC-7 冻结入口

**证据**：§1.11，`status BLOCKED`, `exit_code 2`, `required_gates ["g0","e2e","g6"]`。
**缺口**：三个必需 gate 全部缺工件；且 G0 本身在本机过不了。
**可由代码修复**：⚠️ **间接**。补齐 `check_contract_ownership.py`（§2.3）+ 修 mypy（§2.2）能在本机把 G0 推近 PASS，但 `npm ci` 与版本强制仍需目标运行时，`g6` 需 AC-8。**不是单点代码修复能解决的。**

### 2.6 【新发现 · 代码缺陷】`get_engine()` 用**掩码后**的 URL 建引擎，G0 的 sqlite 配置直接失效

**这是本轮唯一一个新发现的真实代码 bug，round 1 未记录。**

`backend/app/core/database.py:34-56`：

```python
def _build_safe_db_url() -> str:
    raw = settings.DATABASE_URL
    parsed = urlparse(raw)
    if (parsed.hostname
            and parsed.hostname != "localhost"
            and parsed.hostname != "127.0.0.1"):
        return raw
    safe_url = urlunparse((parsed.scheme,
        f"{quote_plus(parsed.username or '')}:{quote_plus(parsed.password or '')}@"
        f"{parsed.hostname or 'localhost'}:{parsed.port or 5432}",
        parsed.path, ...))
    return safe_url
```

`urlparse("sqlite+aiosqlite:///./test.db")` 的 `hostname` 是 **`None`**，守卫 `if parsed.hostname and …` 为假，于是落进重建分支，**凭空注入** `user:pass@localhost:5432`。而 `database.py:64-75` 把这个**重建后的** `safe_url`（不是 `settings.DATABASE_URL`）传给了 `create_async_engine`：

```python
safe_url = _build_safe_db_url()          # :64
logger.info("初始化异步数据库引擎: %s", _mask_password(safe_url))   # :66  ← 只给日志用
_engine = create_async_engine(
    safe_url,                            # :68  ← 却拿它建引擎
```

**独立复现（本轮实跑）**：

```text
$ cd ".../backend" && DATABASE_URL="sqlite+aiosqlite:///./test.db" PYTHONPATH=".../backend" \
  .venv/Scripts/python.exe -c "
import asyncio
from app.core.database import get_engine, _build_safe_db_url
from app.core.config import settings
print('settings.DATABASE_URL =', settings.DATABASE_URL)
print('_build_safe_db_url()  =', _build_safe_db_url())
e = get_engine()
print('engine.url            =', e.url)"
settings.DATABASE_URL = sqlite+aiosqlite:///./test.db
_build_safe_db_url()  = sqlite+aiosqlite://:@localhost:5432/./test.db
Traceback (most recent call last):
  File "…/backend/app/core/database.py", line 67, in get_engine
    _engine = create_async_engine(
sqlalchemy.exc.ArgumentError: Invalid SQLite URL: sqlite+aiosqlite://:***@localhost:5432/./test.db
```

**对 AC-7 的两条具体后果**：

1. **G0 的启动门禁在数据库坏掉的情况下照样 exit 0。** §1.6 里 `/health` 返回 200，是因为应用 fail-soft（`app.main`："数据库不可用；仅开发/测试模式继续启动"），而 `/health` 不碰 DB。**"后端可启动"这一条判据目前只证明了"应用在数据库不可用时仍能起来"，没有证明数据库链路可用。**
2. **这不是死代码**：`verify_hydraulic_stack.py:606-621` 的 G0 pytest 步与 `:647-658` 的 lifespan 步**都**把 `DATABASE_URL` 配成 sqlite。测试之所以仍全绿，是因为 `backend/tests/conftest.py` 已改用 per-session 临时库，**绕开了 `get_engine()` 这条路径**——所以 pytest 覆盖不到它。

**影响面（诚实界定）**：`hostname` 为 `localhost`/`127.0.0.1` 的 Postgres URL 走重建分支后**语义不变**（user/pass/host/port/path 都被保留），因此 **Docker/生产路径（host = `postgres`）走 `return raw`，不受影响**；`.env.example` 与 `README.md` 中也 grep 不到 sqlite 配置。**真正被破坏的是"sqlite 形式 + localhost/无 host 形式"的 `DATABASE_URL`**，而这正是 G0 自己使用的形式。

**可由代码修复**：✅ **是，且很小**。把 `create_async_engine` 的第一个参数改回 `settings.DATABASE_URL`，`safe_url` 只用于 `logger.info`（它本来就只该用于日志）；并给 `_build_safe_db_url` 的 `hostname is None` 情形加一条早返回。改完后建议在 `test_contract_gate_scripts.py` 之外补一个针对 `get_engine()` URL 构造的用例——**这是本轮发现的、当前测试套件的结构性盲区**。

### 2.7 【陈旧】`frontend/.npmrc:5-6` 的过期断言，且其引用源已改口

`frontend/.npmrc:1-6` 原文：

```text
; Evidence (see README FAQ 0.1): npm ci + npm test + npm run build pass on
; Node 18.20.8/npm 10.8.2, while Node 25.9.0/npm 11.12.1 fails the vitest suite
; ("TypeError: localStorage.clear is not a function").
```

**两处都与本轮实测冲突**：

- `.npmrc` 说 Node 25.9.0 会 vitest 失败 → **本轮实测 `150 passed (150)`，exit 0**。垫片确实在 `frontend/src/test/setup.ts:27-35`（`beforeEach` 里 `typeof current.clear !== 'function'` 时 `Object.defineProperty` 覆盖）。
- `.npmrc` 把证据指向 "README FAQ 0.1"，但 **`README.md:1127-1135` 现在写的是"上表六个格子全部是 `NOT RUN`"、并明确说"任何一格都不是实测结论"**——README 已经不再声称 25.9.0 失败，`.npmrc` 的引用已成断链。

**可由代码修复**：✅ **是**（改 `.npmrc` 注释）。`CONTRACT_INDEX.md:405-414` 的维护规则 14/15 要求"已修复"和"仍然坏着"的结论都要重取证据。**round 1 的 G9 未修复，且现在多了一层"引用源已改口"的问题。**

### 2.8 【陈旧】台账与契约仍写 `NOT RUN`，且 pytest 计数已过期

| 位置 | 现文 | 本轮实测 |
|---|---|---|
| `PRD_SUPPLEMENT.md:592` | "**当前基线报告不绿；本轮未重跑**" | 已重跑（§1.1/§1.2/§1.3） |
| `IMPLEMENTATION_CONTRACT.md:2553` | "\| AC-7 \| NOT RUN；recon 报告基线不绿，但本轮未重跑 \|" | 同上 |
| `VALIDATION_EVIDENCE.md:97` | pytest `797 passed, 8 skipped, 3 warnings in 61.42s` | **803 passed, 8 skipped**（+6） |
| `VALIDATION_EVIDENCE.md:33` | `git status --porcelain \| wc -l` = `157` | **160**（`-uall` 747 → **812**） |
| `VALIDATION_EVIDENCE.md:94-111` | 16 项 final gates 全部 exit 0 | 仍**不含 mypy、不含 `npm ci`**（§1.5 末） |
| `VALIDATION_EVIDENCE.md:209`（`final-audit-round-1.md:194` 引用） | acceptance pytest `7 failed / 57 passed` | 过期（`final-audit-round-1.md:194` 已独立指出） |

**可由代码修复**：✅ **是**（台账/契约文本更新）。但注意这三份文档目前都是 **untracked**（`git status --porcelain` 全部显示 `??`），从未进入任何提交。

### 2.9 【不可判定】「旧测试零回归」——测试清单层面已可判定，行为层面仍缺基线

这是 AC-7 的第四个必答项，**本轮结论与 round 1 相同：证据不足以判定**。但我能在**测试清单层面**给出比 round 1 更硬的结论。

**能确定的部分（本轮实测）**：

```text
$ git ls-tree -r HEAD --name-only | grep -E "(^|/)tests?/.*test.*\.py$"
  backend/tests/conftest.py, test_api/__init__.py, test_api/test_auth.py,
  test_api/test_devices.py, test_api/test_health.py, test_services/__init__.py,
  test_services/test_mock_generator.py, test_services/test_sse_manager.py     → 8 个

$ git status --porcelain | grep -E "^ ?D"        → 空（工作树无任何文件被删除）

$ 对每个文件比较 `^\s*(async )?def test_` 计数（HEAD vs 工作树）：
  backend/tests/conftest.py                        HEAD=1    WORKTREE=1
  backend/tests/test_api/test_auth.py              HEAD=3    WORKTREE=10（含 1 个 fixture）
  backend/tests/test_api/test_devices.py           HEAD=3    WORKTREE=3
  backend/tests/test_api/test_health.py            HEAD=2    WORKTREE=2
  backend/tests/test_services/test_mock_generator.py HEAD=3  WORKTREE=3
  backend/tests/test_services/test_sse_manager.py  HEAD=3    WORKTREE=3
  （两个 __init__.py 两侧均为 0）

$ 前端（HEAD 仅 2 个测试文件）：
  frontend/src/test/__tests__/LoginPage.test.tsx   HEAD=2    WORKTREE=2
  frontend/src/test/__tests__/useAppStore.test.ts  HEAD=6    WORKTREE=6
```

- **HEAD 上全部 8 个后端测试文件 + 2 个前端测试文件都在当前工作树里**，工作树**零删除**。
- **所有既有测试文件的测试函数计数都不少于 HEAD**。
- 唯一的实质内容改动：
  - `backend/tests/test_api/test_auth.py` = `153 insertions(+), 2 deletions(-)`；被删的 2 行是 `async def test_login_success(client):` 与 `async def test_login_wrong_password(client):` 的**函数签名**（改成带 fixture 参数），**没有删除任何测试函数、任何断言**。`pytest --collect-only` 实测该文件 **9 个**可收集用例（`grep -cE "^\s*(async )?def test_"` 报 10，是因为第 23 行的 `def test_database(monkeypatch, db_session)` 是 fixture；**round 1 写的"3 → 10"是把 fixture 算进去了，本轮更正为 3 → 9**）。
  - `frontend/src/test/setup.ts` = `39 insertions(+), 0 deletions(-)`（纯新增 localStorage 垫片与 `afterEach` 清理）。
  - `frontend/src/test/__tests__/LoginPage.test.tsx` 和 `useAppStore.test.ts` 在 `git status` 里显示为 `M`，但 **`git diff --numstat` 对这两个文件无任何输出**——`core.autocrlf=true` 造成的 CRLF 归一化假脏，**内容零差异**（`HEAD=2/6` 与 `WORKTREE=2/6` 也印证）。

**不能确定的部分 —— 这是本节的核心**：

"零回归"是一个**相对于"改动前"**的断言，而"改动前"在这个仓库里没有可执行的基线：

- HEAD 仍是 `ca99e048`，但工作树有 **160 条（`-uall` 812 条）**未提交条目，其中 `backend/app/**` 下 16 个既有源文件被改 + 35+ 个新增未跟踪文件。要在 HEAD 上跑旧测试需同时回退这 16 个被改文件——**不是只读验收能做的**。
- 仓库里**没有"改动前"的 pytest/Vitest 运行工件**：`find artifacts .runtime -maxdepth 4 -type d -name g0` **无任何命中**；`find .runtime/ac7-audit .runtime/ac7-round-2-audit -type f` 只有两个 `acceptance/result.json`（都是 BLOCKED 报告）。`artifacts/hydraulic/` 下全是 `ab-*` / 领域 benchmark，**没有一份 pre-change 质量基线**。
- 因此：**"旧 API/SSE 行为在本次改动前后完全一致"这件事，本轮无法用证据判定。** 我只能说：现存的线级契约（§1.10）在**当前**代码上是绿的，且**现存**的旧测试文件**一个没少、断言一条没删**。

**可由代码修复**：⚠️ **部分**。缺的不是一个代码 bug，而是**一条基线记录**：需要在改动前（或冻结的 pre-change 镜像上）跑一次全量 pytest + Vitest 并把结果/hash 落成工件。在此之前，任何"零回归已通过"的说法都缺少对照物。

### 2.10 【流程盲区】CI 覆盖不到当前状态

`.github/workflows/ci.yml` 8 处 `actions/checkout@v4`（`:29,61,82,122,172,217,256,300`）检出的都是 **HEAD 提交树**。当前全部实现都在未提交的工作树里，**所以即使 CI 现在全绿，绿的也不是这份工作树。**

`ci.yml:77-115` 的 `g0-quality` job 接线本身是**正确且严格**的：Python 3.11、`node-version-file: frontend/.node-version`、`pip install --require-hashes`、`test "$(node --version)" = "v18.20.8"` / `test "$(npm --version)" = "10.8.2"`，然后 `verify_hydraulic_stack.py --gate g0`。**缺的只是一次覆盖当前工作树的真实 CI run。**

**可由代码修复**：⚠️ 流程问题，非单点代码。

---

## 3. 缺口清单（按严重度）与 round 1 对照

| # | 缺口 | 本轮证据 | 可由代码修复 | round 1 状态 |
|---|---|---|---|---|
| N1 | **mypy exit 1，1 error / 1 file**（AC-7 "全部 exit 0" 的直接否决项） | §1.5 | ✅ 是（`config.py:48` 加 `Literal[2]`，一行） | G1 **大幅收敛**（117→1） |
| N2 | **`scripts/check_contract_ownership.py` 不存在**，G0 第一步无法执行 | §1.7 | ✅ 是（但需实现 owner-table 解析规格 + 补测试） | G2 **未修复** |
| N3 | **本机 Node/npm 被 G0 硬判 FAIL**（`enforce_version_step` 要求 `v18.20.8`/`10.8.2`），G0 在本机结构上不可跑 | `verify_hydraulic_stack.py:534-540`；§1.0 | ❌ 环境问题 | round 1 未单列，本轮明确化 |
| N4 | **`npm ci` 零证据**：本机 engine-strict 必失败，目标环境 `NOT RUN` | §1.9 | ❌ 环境问题 | G3 **未修复** |
| N5 | **Vitest / build / e2e 的 PASS 全在 Node 25.9.0 上**，非冻结环境 | §1.2 / §1.3 / §1.10 | ❌ 环境问题 | G4 **未修复** |
| N6 | **"旧测试零回归"行为层面不可判定**：无 pre-change 基线工件 | §2.9 | ⚠️ 部分（需补跑并落盘基线） | G5 **未修复** |
| N7 | **【新】`get_engine()` 用掩码 URL 建引擎，sqlite/无 host 的 `DATABASE_URL` 直接不可用**；G0 lifespan 在 DB 坏掉时仍 exit 0 | §2.6 | ✅ 是（小改：`create_async_engine(settings.DATABASE_URL, …)`） | **round 1 未发现** |
| N8 | **【新】AC-7 的 `required_gates` 收紧为含 `g6`**，AC-7 现在硬依赖 AC-8 的双 profile Compose 产物 | §1.11 | ❌ 非代码问题，是判据变更 | round 1 记录的 `["g0","e2e"]` 已过期 |
| N9 | 台账"16 项 final gates 全绿"不覆盖 AC-7（无 mypy、无 `npm ci`） | §1.5 末 / `VALIDATION_EVIDENCE.md:94-113` | ✅ 是（登记口径） | G8 **未修复** |
| N10 | **`frontend/.npmrc:5-6` 过期断言**（称 Node 25.9.0 vitest 失败，实测 150/150；且引用的 README FAQ 0.1 已改口为"全部 NOT RUN"） | §2.7 | ✅ 是（改注释） | G9 **未修复** |
| N11 | 台账/契约仍记 `NOT RUN`，pytest 计数 797→803、dirty 157→160 均已过期 | §2.8 | ✅ 是（台账更新） | round 1 未单列 |
| N12 | CI `checkout@v4` 检出的提交树不含本工作树 812 条改动 → CI 绿灯与当前状态无对应 | §2.10 | ⚠️ 流程问题 | G11 **未修复** |
| — | `conftest.py` 共享 `test.db` + 会话末 `drop_all` | `conftest.py:22-23,35` | — | G7 **✅ 已修复**（本轮 pytest 未复现并发互毁） |
| — | Black 计数 112 vs 台账 113 | `113 files would be left unchanged` | — | G10 **✅ 已消解** |

**本轮未发现任何新的回归。**

---

## 4. 结论与给下一轮的建议

**AC-7 应保持 `partial`，不得记 `PASS`。** 理由（逐条可复核）：

1. AC-7 判据是"**全部 exit 0**"，而 mypy **exit 1**（1 条，`config.py:278`）；
2. `scripts/check_contract_ownership.py` **不存在** ⇒ **G0 结构上不可能 PASS**；
3. `npm ci` 与目标运行时（18.20.8 / 10.8.2）**零证据**；
4. 冻结入口 `--gate acceptance --ac ac7` 实测 **BLOCKED / exit 2**，且现在还多要求 `g6` 的双 profile 工件；
5. "旧测试零回归"在**行为层面**仍**缺对照基线**，不可判定。

按顺序建议：

1. **修 mypy 剩的 1 条**（N1）——`backend/app/core/config.py:48` 加 `Literal[2]`。一行，AC-7 判据里唯一还在流血的可修项。
2. **补 `scripts/check_contract_ownership.py`**（N2）——比修 mypy 更结构性地卡住 G0；owner 已在 `IMPLEMENTATION_CONTRACT.md:2356` 指定，但需要实现 owner-table 解析规格，并像 `test_contract_gate_scripts.py:31-32` 那样配测试。
3. **修 `get_engine()` 的 URL 缺陷**（N7）——`create_async_engine` 改用 `settings.DATABASE_URL`；并补一条针对 URL 构造的用例，否则当前测试套件永远覆盖不到它。同时建议给 G0 lifespan 步加一条"DB 引擎可建"的断言，否则它会在 DB 坏掉时照样放行。
4. **切到 Node 18.20.8 / npm 10.8.2 重取全部前端证据**（N3/N4/N5）——本轮所有前端 PASS 都要在这套环境上重跑才算数；`npm ci` 必须真跑（会删 `node_modules`，请在可控窗口内做）。
5. **落一份 pre-change 基线工件**（N6）——否则"零回归"永远只能停在"不可判定"。
6. **更新台账与契约文本**（N9/N10/N11）——`PRD_SUPPLEMENT.md:592`、`IMPLEMENTATION_CONTRACT.md:2553`、`VALIDATION_EVIDENCE.md:33,97,94-111`、`frontend/.npmrc:5-6`。这三份文档目前全是 untracked，从未进入任何提交。

在 1、2、3 完成**且** 4 拿到目标环境证据**且** `verify_hydraulic_stack.py --gate g0` 产出同 RUN_ID 的 `g0/result.json`（以及 `g6/postgis` + `g6/portable` 的 AC-8 产物）之前，**AC-7 不得记 PASS**。
