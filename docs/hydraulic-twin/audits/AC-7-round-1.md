# AC-7 独立验收（round 1）：pytest / vitest / build 与零回归

- 验收日期：2026-09-28
- 验收人：独立验收员（只读；未修改任何实现/测试/契约。仅新增本审计文件，并运行门禁脚本在其自身 evidence 目录下产生的运行产物）
- 仓库：`smart-city-drainage`，`git rev-parse HEAD` = `ca99e0485fb12fbecbd565e79d81f745f8d4a748`，`git status --porcelain | wc -l` = `157`（`-uall` 为 `747`；工作树非干净，本报告结论对应**当前工作树**）
- 验收对象：AC-7「pytest/vitest/build/后端启动全过且旧测试零回归」（`SOURCE_PRD.md:33`）

## 0. 结论

**AC-7 = `partial`。**

判据是 `PRD_SUPPLEMENT.md:592` 的一行：

> | AC-7 质量与零回归 | Python 3.11 pytest/Black/isort/Flake8/mypy；Node 18.20.8/npm 10.8.2 的 `npm ci`/Vitest/lint/format/build；startup；旧 API/SSE characterization | **全部 exit 0；mypy 不 continue-on-error；后端可启动；旧测试零回归** | **当前基线报告不绿；本轮未重跑** |

这一行有三个必答项：**全部 exit 0**、**mypy 不 continue-on-error**、**旧测试零回归**。本轮实测：

| 判据 | 本轮结果 |
|---|---|
| 后端 pytest 全量 | ✅ exit 0，`797 passed, 8 skipped`（按 G0 的 cwd/env 原样复现） |
| 前端 Vitest | ✅ exit 0，`Test Files 32 passed (32)` / `Tests 150 passed (150)` |
| 前端 build | ✅ exit 0，`✓ built in 12.72s` |
| lint / format / Black / isort / Flake8 | ✅ 五项全部 exit 0 |
| 后端启动 | ✅ exit 0，`200 {"status":"healthy"}` |
| 旧 API/SSE characterization | ✅ exit 0（`test_ac7_legacy_api_and_sse_wire_contract_has_no_regression` 1 passed）+ 浏览器 e2e 3 场景全 PASS |
| **mypy** | ❌ **exit 1，117 errors in 14 files** |
| **`npm ci`** | ❌ 本机 Node/npm 与 `engines` 不符，`engine-strict=true` 使其**在安装阶段 exit 1** |
| **G0 冻结入口** | ❌ 不可通过：它调用的 `scripts/check_contract_ownership.py` **在仓库中不存在** |
| **AC-7 冻结入口** | ❌ `verify_hydraulic_stack.py --gate acceptance --ac ac7` → **BLOCKED / exit 2** |

三句话概括：

1. **单元与构建层面确实是绿的**，而且绿得比台账记录的更完整：本轮补上了台账里"计数未留存"和"mypy 未跑"两处空白。
2. **但 AC-7 的判据是"全部 exit 0"，mypy 单独一项就否掉了它**——117 个类型错误，且 `pyproject.toml:92-96` 开着 `warn_return_any = true`（比默认更严），不是"零错误基线偶尔抖一下"。
3. **"零回归"目前无法用证据判定**，不是"已通过"也不是"已失败"：HEAD 之外有 66 个 `backend/app` 源文件和 30+ 其他文件是未提交改动，仓库里**没有任何一份"改动前"的 pytest 运行工件**可以作为对照基线；唯一被改动的既有测试文件 `backend/tests/test_api/test_auth.py` 本身也动了（+153/-2）。

---

## 1. 本轮实际执行的检查（完整命令与真实输出）

以下每条都是本轮亲自运行，未复述台账。

### 1.0 环境事实

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage" && git rev-parse HEAD && git status --porcelain | wc -l && node --version && npm --version && backend/.venv/Scripts/python.exe --version
ca99e0485fb12fbecbd565e79d81f745f8d4a748
157
v25.9.0
11.12.1
Python 3.11.15
```

| 目标（`PRD_SUPPLEMENT.md:592`） | 实测 | 结论 |
|---|---|---|
| Python 3.11 | `backend/.venv` = 3.11.15 | ✅ 命中 |
| Node 18.20.8 | `v25.9.0` | ❌ 不符 |
| npm 10.8.2 | `11.12.1` | ❌ 不符 |

> PATH 上的 `python` 是 `C:\Python314\python.exe` = 3.14.4（`VALIDATION_EVIDENCE.md:42-51` 已记录）。本轮所有 Python 检查一律用 `backend/.venv/Scripts/python.exe`（3.11.15），与 G0 的 `sys.executable` 语义一致。

### 1.1 后端 pytest 全量 —— ✅ PASS

**按 G0 冻结入口的原样调用**（`scripts/verify_hydraulic_stack.py:617-629`：`cwd=backend`、`DATABASE_URL=sqlite+aiosqlite:///./test.db`、Redis/Influx 指向 `127.0.0.1:1`、`JWT_SECRET_KEY=g0-test-secret-not-for-production`）：

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && DATABASE_URL="sqlite+aiosqlite:///./test.db" REDIS_URL="redis://127.0.0.1:1/0" INFLUXDB_URL="http://127.0.0.1:1" JWT_SECRET_KEY="g0-test-secret-not-for-production" PYTHONPATH=".../backend" .venv/Scripts/python.exe -m pytest tests/ -q --tb=line
G0_PYTEST_EXIT=0
797 passed, 8 skipped, 3 warnings in 45.34s
```

计数与 `VALIDATION_EVIDENCE.md:96` 记录的 `797 passed, 8 skipped` **逐项一致**。

**⚠️ 但同一命令族在本轮出现过两次 16 失败，必须记进审计。** 从仓库根跑同一套测试：

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage" && backend/.venv/Scripts/python.exe -m pytest backend/tests -q -p no:cacheprovider
PYTEST_EXIT=1
16 failed, 781 passed, 8 skipped, 3 warnings in 66.59s
```

失败清单（两次完全一致）：

```text
FAILED backend\tests\security\test_auth_hardening.py::test_inactive_user_cannot_obtain_a_token
FAILED backend\tests\security\test_auth_hardening.py::test_deactivated_user_revokes_an_existing_admin_token[development]
FAILED backend\tests\security\test_auth_hardening.py::test_deactivated_user_revokes_an_existing_admin_token[staging]
FAILED backend\tests\security\test_auth_hardening.py::test_viewer_cannot_change_mock_configuration
FAILED backend\tests\security\test_auth_hardening.py::test_operator_can_change_mock_configuration
FAILED backend\tests\security\test_auth_hardening.py::test_control_websocket_rejects_deactivated_existing_token
FAILED backend\tests\security\test_auth_hardening.py::test_control_websocket_accepts_browser_authentication_frame
FAILED backend\tests\security\test_auth_hardening.py::test_control_websocket_allows_operator
FAILED backend\tests\security\test_ssrf_boundaries.py::test_inference_rejects_private_url_before_fetch
FAILED backend\tests\security\test_upload_paths.py::test_model_upload_rejects_path_traversal
FAILED backend\tests\security\test_upload_paths.py::test_model_upload_rejects_windows_path_separator
FAILED backend\tests\security\test_upload_paths.py::test_model_upload_enforces_streaming_size_limit
FAILED backend\tests\security\test_upload_paths.py::test_model_upload_is_atomic_and_does_not_return_absolute_path
FAILED backend\tests\test_api\test_auth.py::test_login_success - sqlalchemy.e...
FAILED backend\tests\test_api\test_auth.py::test_login_wrong_password - sqlal...
FAILED backend\tests\test_api\test_auth.py::test_unknown_user_without_opt_in_is_rejected
```

根因取自 traceback，是**同一个**：

```text
E   sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) no such table: users
E   [SQL: INSERT INTO users (id, username, email, hashed_password, full_name, role, is_active, last_login, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)]
E   assert 503 == 403
E    +  where 503 = <Response [503 Service Unavailable]>.status_code
```

定位到 `backend/tests/conftest.py:10,13-21`：

```python
TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"      # :10  ← 相对 CWD，不是 per-session
@pytest_asyncio.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)        # :20  ← 会话结束销毁全部表
```

**这是"两个 pytest 进程共享同一个 `./test.db`"的互相破坏，不是 16 个独立缺陷。** 佐证：单独跑 `pytest backend/tests/security backend/tests/test_api` → `63 passed, exit 0`；跑 `acceptance api hydraulic integration security test_api` → `546 passed, 6 skipped, exit 0`。

**诚实边界（这里必须说清楚，不能含糊）**：

- **第二次** 16 失败**有确定的并发源**——是我自己：那一轮我同时在跑 `test_login_success` 单测与两个 bisect 子集，它们共用仓库根的 `./test.db`。
- **第一次** 16 失败**没有确定的并发源**。我在那轮期间只跑了 `npm` 命令（不碰 `test.db`），而我在**两次失败都结束之后**才做的进程排查（`Get-CimInstance Win32_Process | Where CommandLine -match pytest`）只返回我自己的查询进程。**事后排查证明不了事发当时没有别的进程。** 本工作流是多验收员并行，我无法排除兄弟 agent 当时正在跑同一套测试。
- 所以我能确定的是**机制**（共享 CWD 相对的 `test.db` + 会话末 `drop_all` ⇒ 互相破坏，症状恰好是 `no such table: users` + 503），**不能确定**第一次的并发来源。把根因写成"确定"会超出证据。

**可由代码修复：是。** 把 `TEST_DATABASE_URL` 换成 per-session 唯一文件（`tmp_path_factory`）即可同时消除跨 CWD 串扰、并发互毁，以及"跑一次 pytest 就把仓库工作树里的 `test.db` 全部表 drop 掉"的副作用。

### 1.2 前端 Vitest —— ✅ PASS（在**非冻结** Node 上）

```text
$ cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage" && npm --prefix frontend run test > /tmp/vitest.log 2>&1; echo "VITEST_EXIT=$?"
VITEST_EXIT=0
 Test Files  32 passed (32)
      Tests  150 passed (150)
   Duration  12.40s
```

补齐了台账的两处空白：`VALIDATION_EVIDENCE.md:103` 记"前端 Vitest exit 0 但**汇总行不在摘录内**，计数未在证据中留存"；`:195` 记 frontend-acceptance-tests vitest"**测试计数行不在摘录内**"。本轮两个计数都在：`32 files / 150 tests`。也覆盖并超出 `:191` 记的 `16 files / 84 tests`（那是 feature 子集）。

**必须在结论里带上的一句**：这次 PASS 是在 **Node v25.9.0 / npm 11.12.1** 上取得的，不是契约冻结的 18.20.8/10.8.2。按 AC-7 原文"Node 18.20.8/npm 10.8.2 的 …Vitest…全部 exit 0"，**本轮没有在目标环境上验证过 Vitest**。

运行中 Node 反复打印 `(node:…) Warning: '--localstorage-file' was provided without a valid path`（每条测试文件一次）。它**没有**导致失败，但说明 Node 25 提供的原生 `localStorage` 与 `frontend/src/test/setup.ts:27-35` 的兜底垫片在抢定义权：

```ts
beforeEach(() => {
  const current = globalThis.localStorage as Partial<StorageLike> | undefined
  if (!current || typeof current.clear !== 'function') {   // :29
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: createMemoryStorage() })
  }
})
```

**顺带纠正一条过期结论**：`frontend/.npmrc:5-6` 的注释写着 Node 25.9.0/npm 11.12.1 "fails the vitest suite (`TypeError: localStorage.clear is not a function`)"。**本轮实测该说法不成立**——垫片在 `setup.ts:27-35` 存在且生效，25.9.0 上 150/150 全绿。按 `CONTRACT_INDEX.md:405-414`（维护规则 14/15，"已修复"和"仍然坏着"同样会过期，两种都要重新取证），这条注释属于该重取证据而没取的过期断言。

### 1.3 前端 build / lint / format —— ✅ PASS

```text
$ npm --prefix frontend run build; echo "BUILD_EXIT=$?"
BUILD_EXIT=0
✓ built in 12.72s
dist/assets/echarts-BBjs3wlc.js   1,030.30 kB │ gzip: 341.85 kB
(!) Some chunks are larger than 1000 kB after minification.

$ npm --prefix frontend run lint; echo "LINT_EXIT=$?"
LINT_EXIT=0
> eslint . --report-unused-disable-directives --max-warnings 0

$ npm --prefix frontend run format:check; echo "FMT_EXIT=$?"
FMT_EXIT=0
All matched files use Prettier code style!
```

build 同样跑在 Node 25.9.0 上。`echarts-BBjs3wlc.js 1,030.30 kB` 与台账 `:104` 记的产物名/大小逐字一致。

### 1.4 后端 lint 家族 —— 4 项里 3 项 PASS，mypy FAIL

```text
$ cd ".../backend"
$ .venv/Scripts/python.exe -m black --check app tests; echo "EXIT=$?"
EXIT=0
All done! ✨ 🍰 ✨
112 files would be left unchanged.

$ .venv/Scripts/python.exe -m isort --check-only app tests; echo "EXIT=$?"
EXIT=0

$ .venv/Scripts/python.exe -m flake8 app tests; echo "EXIT=$?"
EXIT=0
```

> `112 files` 与台账 `:97` 记的 `113 files would be left unchanged.` 差 1。源码本轮无改动，这个差值要么来自台账记录时刻与现在的文件数变化，要么来自台账转录。**不是回归**（Black 仍 exit 0），但说明台账该行已不精确。

### 1.5 ❌ mypy —— **FAIL，AC-7 的直接否决项**

```text
$ cd ".../backend" && .venv/Scripts/python.exe -m mypy app --ignore-missing-imports; echo "MYPY_EXIT=$?"
MYPY_EXIT=1
Found 117 errors in 14 files (checked 60 source files)
```

按文件分布：

| 错误数 | 文件 |
|---:|---|
| 66 | `app/api/hydraulic_routes.py` |
| 23 | `app/services/hydraulic/nowcast.py` |
| 7 | `app/services/hydraulic/benchmark.py` |
| 4 | `app/workers/network_job_worker.py` |
| 3 | `app/services/influxdb_service.py` |
| 2 | `app/workers/nowcast_worker.py` |
| 2 | `app/services/mock_data_generator.py` |
| 2 | `app/services/hydraulic/tasks.py` |
| 2 | `app/services/hydraulic/forecast_alerts.py` |
| 2 | `app/api/routes.py` |
| 1 | `app/workers/durable_runtime.py` |
| 1 | `app/services/system_status.py` |
| 1 | `app/services/hydraulic/surface_flood.py` |
| 1 | `app/core/config.py` |

按错误码分布：`index` 31、`no-any-return` 28、`attr-defined` 17、`assignment` 12、`arg-type` 12、`union-attr` 10、`return-value` 3、`operator` 2、`var-annotated` 1、`misc` 1、`annotation-unchecked` 1。

样例（说明这不是"风格偏好"，有真实类型缺口）：

```text
app/api/hydraulic_routes.py:3065: error: Value of type "dict[str, Any] | None" is not indexable  [index]
app/api/hydraulic_routes.py:3065: error: Returning Any from function declared to return "dict[str, Any]"  [no-any-return]
app/api/hydraulic_routes.py:3336: error: Incompatible return value type (got "str | None", expected "str")  [return-value]
app/workers/durable_runtime.py:880: error: Argument 1 to "run" has incompatible type "Awaitable[int]"; expected "Coroutine[Any, Any, Never]"  [arg-type]
app/services/system_status.py:21: error: Module "app.services.influxdb_service" has no attribute "influxdb_client"  [attr-defined]
app/workers/network_job_worker.py:719: error: "SeedNetwork" has no attribute "junctions"  [attr-defined]
app/services/mock_data_generator.py:245: error: Unsupported operand types for + ("object" and "float")  [operator]
```

`app/services/system_status.py:21` 与 `network_job_worker.py:716-721`（把 `SwmmNetwork` 赋给 `SeedNetwork` 变量后访问 `junctions/outfalls/conduits`）看着像**真缺陷**而非注解噪声，值得单独查。

**为什么这条单独就足以否掉 AC-7**：

- `PRD_SUPPLEMENT.md:592` 判据原文："**全部 exit 0；mypy 不 continue-on-error**"。
- `IMPLEMENTATION_CONTRACT.md:2490` G0 定义："再运行 Python 3.11 全量 pytest/Black/isort/Flake8/**mypy（不可 continue-on-error）**/backend lifespan characterization"。
- `scripts/verify_hydraulic_stack.py:634` 确实把 `g0-backend-mypy` 作为 G0 的一步在跑。

**并且：已记录的"16 项 final gates 全部 exit 0"里根本没有 mypy。** `VALIDATION_EVIDENCE.md:94-111` 的 16 行是 pytest / Black / isort / Flake8 / 后端导入 / ESLint / Prettier / Vitest / build / Compose config / Git diff / Compose build-up / Compose smoke / Compose 清理 / 5000 节点基准 / G5 浏览器 gate——**没有 mypy，也没有 `npm ci`**。所以那句"16 项全部 `exitCode=0`"（`:113`）**不构成 AC-7 通过的证据**，它连 AC-7 要求的两项都没测。

**可由代码修复：是。** 117 条里 28 条 `no-any-return` 与 31 条 `index` 都是 `dict[str, Any] | None` 未收窄造成的机械修复；`warn_return_any = true`（`backend/pyproject.toml:94`）是仓库自选的严格度，修 117 条不需要降标准。

### 1.6 后端启动 —— ✅ PASS

按 G0 的 `startup_code`（`scripts/verify_hydraulic_stack.py:647-658`）原样跑：

```text
$ cd ".../backend" && DATABASE_URL="sqlite+aiosqlite:///./test.db" REDIS_URL="redis://127.0.0.1:1/0" INFLUXDB_URL="http://127.0.0.1:1" JWT_SECRET_KEY="g0-test-secret-not-for-production" PYTHONPATH=".../backend" .venv/Scripts/python.exe -c "from fastapi.testclient import TestClient; from app.main import app; client = TestClient(app); client.__enter__(); response = client.get('/health'); print(response.status_code, response.text); assert response.status_code == 200; client.__exit__(None, None, None)"; echo "LIFESPAN_EXIT=$?"
LIFESPAN_EXIT=0
200 {"status":"healthy"}
```

（stderr 里有一屏 InfluxDB 连接 `127.0.0.1:1` 被拒的重试日志和末尾 `✅ 服务已安全关闭`，属预期的 fail-soft 路径。）

### 1.7 G0 的三个静态子门禁 —— 1 PASS / 1 **缺文件** / 1 DEFERRED

```text
$ backend/.venv/Scripts/python.exe -B scripts/check_migration_gate.py --review-artifact artifacts/contracts/v3-independent-review.json --base-ref ca99e0485fb12fbecbd565e79d81f745f8d4a748 --versions-dir backend/alembic/versions
EXIT=0
MIGRATION_PHASE=POST_MIGRATION_APPROVED
MIGRATION_GATE=PASS

$ backend/.venv/Scripts/python.exe -B scripts/check_contract_ownership.py --contract docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md --phase post-handoff
EXIT=2
... can't open file '...\scripts\check_contract_ownership.py': [Errno 2] No such file or directory

$ backend/.venv/Scripts/python.exe -B scripts/check_dependency_handoff.py --handoff artifacts/contracts/dependency-config-handoff.json --expected backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json --python-version-file .python-version --node-version-file frontend/.node-version --npmrc frontend/.npmrc --package frontend/package.json --lock frontend/package-lock.json --env-example .env.example
EXIT=0
DEPENDENCY_HANDOFF=PASS_WITH_DEFERRED
DEFERRED=frontend-target-runtime-evidence
G0_G5_BLOCKED_BY_DEFERRED=true
```

**`check_contract_ownership.py` 缺失是一条硬阻断。** 它不是可选检查：

- `IMPLEMENTATION_CONTRACT.md:2490` 把 `python scripts/check_contract_ownership.py --contract … --phase post-handoff` 明列为 **G0 第一步**（"证明 effective owner table 每个 path 恰一个 owner"）。
- `IMPLEMENTATION_CONTRACT.md:2356` 给它分配 owner `ci-quality-bootstrap`。
- `scripts/verify_hydraulic_stack.py:570-580` 的 `gate_g0()` 就在调它。
- `VALIDATION_EVIDENCE.md:446` 已登记它 `NOT RUN（文件不存在于仓库）`——**登记了，但至今没补上**。

在 G0 里它的 `blocked_exit_codes=frozenset({2})`（`:580`），所以缺文件会被记成 **BLOCKED** 而不是 FAIL。两种都不是 PASS：**G0 在补上这个脚本之前不可能通过。**

**可由代码修复：是**（补交 `scripts/check_contract_ownership.py`，owner 已在契约里指定）。

`G0_G5_BLOCKED_BY_DEFERRED=true` 是 checker 自己吐的：它明确说 G0/G5 仍被 `frontend-target-runtime-evidence` 挡住。这与 §1.2 观察到的事实吻合——本机就是那个"target runtime evidence 缺失"的现场（Node 25.9.0 ≠ 18.20.8）。

### 1.8 `node --check` 两项 —— ✅ PASS

```text
$ node --check frontend/performance/run-twin-map.mjs; echo "EXIT1=$?"   → EXIT1=0
$ node --check frontend/e2e/run-ac-smoke.mjs;         echo "EXIT2=$?"   → EXIT2=0
```

### 1.9 `npm ci` —— ❌ 本机**不可能**通过（未真实执行）

```text
$ npm --prefix frontend ci --dry-run > /tmp/npmdry.log 2>&1; echo "NPM_DRY_EXIT=$?"
NPM_DRY_EXIT=1
npm error engine Unsupported engine
npm error notsup Not compatible with your version of node/npm: smart-city-drainage-frontend@1.0.0
npm error notsup Required: {"node":"18.20.8","npm":"10.8.2"}
npm error notsup Actual:   {"node":"v25.9.0","npm":"11.12.1"}
```

`frontend/.npmrc:6` 是 `engine-strict=true`，所以这是**安装阶段的硬失败**，不是警告。

**我只跑了 `--dry-run`，没有跑真的 `npm ci`。** 理由：`npm ci` 会先删掉整个 `frontend/node_modules` 再重装；在 registry 可达性未验证的情况下，一旦中途失败会把本机前端依赖树弄坏，而 AC-7 ask 点名的三项是 pytest / vitest / build，不含 `npm ci`。`--dry-run` 已经在 engine 校验处 exit 1，说明真跑也会在同一处终止。

**⇒ `npm ci` 在本轮记为 `NOT RUN`，并在目标环境（Node 18.20.8 / npm 10.8.2）上同样 `NOT RUN`。** 它是 AC-7 判据里明确列出的一项，目前**没有任何证据**，本机也拿不到。

### 1.10 旧 API/SSE characterization —— ✅ PASS（两层）

**第一层：in-process 线级回归测试**（`matrix.json:153-168` 为 AC-7 登记的唯一测试）：

```text
$ backend/.venv/Scripts/python.exe -m pytest "backend/tests/acceptance/test_acceptance_matrix.py::test_ac7_legacy_api_and_sse_wire_contract_has_no_regression" -q -p no:cacheprovider
AC7_TEST_EXIT=0
1 passed, 1 warning in 3.33s
```

它读 `backend/tests/fixtures/hydraulic/acceptance/v1/legacy_api.json`（`schema: acceptance-legacy-api-v2`），对 `/api/health`、`/api/alerts`、`/api/sensors/latest` 与 `/api/sse/events`（`connected`/`sensors`/`alerts`/`system` 事件 + `Last-Event-ID: 42`）逐项断言。

**第二层：真实浏览器 e2e**（AC-7 的 `e2e` 必需工件）：

```text
$ node frontend/e2e/run-ac-smoke.mjs --fixture frontend/e2e/fixtures/ac-smoke-v1.json --output "C:/Users/zyq/AppData/Local/Temp/ac7-e2e-report.json" --run-id ac7-round-1
E2E_EXIT=0
status PASS, exit_code 0, browser_executed true
commit ca99e0485fb12fbecbd565e79d81f745f8d4a748, dirty_state true
fixture ac-smoke-v1-current-contract sha256 4561e8a6e4ed53622e227df59c9fbd96a4a3a1b6e9f1ab613007316fe9a1797a
build  frontend/dist index_sha256 0657115cc700384734fc165a4a92abbf989bd7bbfeccbc047102665bf57c0dda
browser Chrome/154.0.8037.57, Headless
  AC-7-viewer-simulation-read-only                    PASS  AC-7  [role_readonly, timeline_playback, forecast_sse]
  AC-3-AC-4-AC-6-operator-simulation-workflow         PASS  AC-3,AC-4,AC-6,AC-7  [rainfall_edit_submit, timeline_playback, ab_comparison, forecast_sse]
  AC-7-unauthenticated-simulation-redirect            PASS  AC-7  [role_readonly]
```

**注意它消费的是我 §1.3 刚 build 出来的 `frontend/dist`**（`index_sha256` 与 build 产物对应），所以 build → e2e 这条链是自洽的。同样地，它跑在 Node 25.9.0 上，**不是**冻结环境。

**工件落点说明**：我故意把报告写到仓库外（系统 temp），没有写进 `artifacts/` 或 `.runtime/`。原因见 §1.11——acceptance 只认**同一 RUN_ID 下**的 `g0/result.json` + `e2e/ac-smoke.json`；我手上没有 `g0/result.json`（G0 过不了），把一份 e2e 报告放进 evidence 目录只会制造"半套工件"，诱导后续把 AC-7 误读成接近通过。

### 1.11 AC-7 冻结入口 —— ❌ BLOCKED

```text
$ backend/.venv/Scripts/python.exe -B scripts/verify_hydraulic_stack.py --gate acceptance --ac ac7 --evidence-dir ".runtime/ac7-audit/acceptance"
ACC_EXIT=2
{"acceptance": [{"ac": "ac7",
  "artifact_paths": [],
  "missing_or_invalid_artifacts": ["g0", "e2e",
     "missing artifact: ...\\.runtime\\ac7-audit\\g0\\result.json",
     "missing artifact: ...\\.runtime\\ac7-audit\\e2e\\ac-smoke.json"],
  "required_gates": ["g0", "e2e"],
  "status": "BLOCKED"}],
 "commit": "ca99e0485fb12fbecbd565e79d81f745f8d4a748", "dirty_state": true,
 "environment": {"python_version": "3.11.15", "node_version": "v25.9.0", "npm_version": "11.12.1"},
 "exit_code": 2, "run_id": "20260927T193003Z-ca99e048",
 "schema": "hydraulic-acceptance-report-v1", "status": "BLOCKED"}
HYDRAULIC_STACK_STATUS=BLOCKED
HYDRAULIC_STACK_EXIT=2
HYDRAULIC_STACK_RESULT=...\.runtime\ac7-audit\acceptance\result.json
HYDRAULIC_STACK_ARTIFACT_SHA256=ebc9a264603913038aa92b663a6be70fa4e6198abf8ecd0737eaab26b7e3d0cd
```

与我独立跑的 8 个子检查对照：即使我把 §1.1–§1.8 的结果全部手工拼起来，**G0 仍然过不了**（缺 `check_contract_ownership.py`、mypy exit 1、`npm ci` 在目标环境未验证），acceptance 依旧 BLOCKED。**我不把"手工拼起来的绿"当作门禁通过。**

### 1.12 我**没有**跑的

| 未跑项 | 为什么 |
|---|---|
| `verify_hydraulic_stack.py --gate g0` 完整入口 | 它第 7 步是 `npm ci`（§1.9：本机 engine-strict 必失败，且会删 `node_modules`）。我改为逐条跑它的 11 个子步骤，只跳过 `npm ci`。**这是替代，不是同一检查**，按 ask 的 scale 要求明确标注。 |
| 真 `npm ci` | 见 §1.9。 |
| Node 18.20.8 / npm 10.8.2 上的任何前端命令 | 本机 Node 是 25.9.0；`nvm`/版本管理器切换属于改本机状态，且 README `:1149-1151` 把它列为用户操作。 |
| G5 浏览器性能门禁（`test:perf:twin`） | 属 AC-7 判据里的 G0/G5 依赖；`check_dependency_handoff.py` 自报 `G0_G5_BLOCKED_BY_DEFERRED=true`。本轮未跑。 |
| G6 / 双 profile Compose | 属 AC-8 范围，非本 ask。 |
| CI 实跑 | `.github/workflows/ci.yml` 用 `actions/checkout@v4`，检出的提交树不含这 157 项工作树改动（见 §2.3）。 |

---

## 2. "旧测试零回归"专项

AC-7 的第四个必答项。我把它单独拆开，因为它的答案**不是**"通过"也不是"失败"，而是"证据不足以判定"。

### 2.1 能确认的部分

```text
$ git ls-tree -r HEAD --name-only | grep -E "(^|/)tests?/.*test.*\.py$"   → 8 个文件
$ git status --porcelain | grep -E "^ ?D"                                    → 空（工作树无任何删除）
$ for f in <上述 8 个>; do [ -f "$f" ] && echo OK || echo MISSING; done     → 8/8 OK
```

- HEAD 上**全部 8 个测试文件在当前工作树里都还在**，工作树**没有任何文件被删除**。
- 工作树共 43 个 `test_*.py`（`find backend/tests -name "test_*.py" | wc -l` = 43），比 HEAD 的 8 个多 35 个，全是新增。
- 唯一被**修改**的既有测试文件是 `backend/tests/test_api/test_auth.py`（`git diff --stat` = `153 insertions(+), 2 deletions(-)`），测试数 3 → 10。被删的 2 行是 `async def test_login_success(client):` 和 `async def test_login_wrong_password(client):` 的**函数签名**（改成带 fixture 参数），**没有删除任何测试，也没有删改任何断言**（`git diff … | grep '^-'` 只输出这两行加 diff 头）。

### 2.2 不能确认的部分 —— 这是本节的核心

**"零回归"是一个相对于"改动前"的断言，而"改动前"在这个仓库里没有可执行的基线。**

- HEAD = `ca99e048`，但工作树有未提交改动：`git status --porcelain | wc -l` = 157（默认折叠未跟踪目录），`git status --porcelain --untracked-files=all | wc -l` = **747**。其中 `backend/app/**` 有 **51** 个条目——**16 个既有源文件被改**（`app/main.py`、`app/api/routes.py`、`app/core/security.py`、`app/models/db_models.py`、`app/services/sse_manager.py` …）+ **35 个新增未跟踪文件**。要在 HEAD 上跑旧测试需要同时回退这 16 个被改文件，那不是只读验收能做的。
- 仓库里**没有任何"改动前"的 pytest 运行工件**。`artifacts/` 下只有 `g1..g6` 与 `ac3-audit`，没有 pre-change 基线。
- 因此："旧 API/SSE 行为在本次改动前后完全一致"这件事，**本轮无法用证据判定**。我只能说：现存的旧线级契约（§1.10）在**当前**代码上是绿的，且**现存**的旧测试文件**一个没少**。
- 台账自身也承认这一点：`PRD_SUPPLEMENT.md:592` 该行"当前状态"写的是"**当前基线报告不绿；本轮未重跑**"。本轮把"未重跑"消掉了，但**没有**、也无法把"零回归"变成已证。

**可由代码修复：部分。** 缺的不是一个代码 bug，而是**一条基线记录**：需要在改动前（或在冻结的 pre-change 镜像上）跑一次全量 pytest + Vitest 并把结果/hash 落成工件。有了它，"零回归"才是可判定的判定而不是推断。在此之前，任何"零回归已通过"的说法都缺少对照物。

### 2.3 CI 覆盖不到当前状态

`.github/workflows/ci.yml` 的 `g0-quality` job（`:77-115`）跑的就是冻结入口：

```yaml
- uses: actions/checkout@v4
- name: Run G0 verifier (pytest, Vitest, build, lint, mypy, lifespan)
  run: >-
    python scripts/verify_hydraulic_stack.py --gate g0 …
```

`actions/checkout@v4` 检出的是 **HEAD 提交树**。当前所有实现都在未提交的工作树里，所以**即使 CI 现在全绿，绿的也不是这份工作树**。这是"零回归"在流程上的一个真实盲区：CI 绿灯与本轮实测结果之间没有对应关系。

---

## 3. 缺口清单（按严重度）

| # | 缺口 | 证据 | 可由代码修复 |
|---|---|---|---|
| G1 | **mypy exit 1，117 errors / 14 files** —— AC-7 判据"全部 exit 0"的直接否决项 | §1.5 | ✅ 是（28 条 `no-any-return` + 31 条 `index` 为机械收窄；`system_status.py:21`、`network_job_worker.py:716-721` 需单独查，可能是真缺陷） |
| G2 | **`scripts/check_contract_ownership.py` 不存在**，而它是 `IMPLEMENTATION_CONTRACT.md:2490` 明列的 G0 第一步、`verify_hydraulic_stack.py:570` 实际调用 | §1.7 | ✅ 是（补交脚本，owner 已指定 `ci-quality-bootstrap`） |
| G3 | **`npm ci` 无任何证据**：本机 engine-strict 必失败，目标环境 18.20.8/10.8.2 本轮 NOT RUN | §1.9 | ⚠️ 部分。需换到 Node 18.20.8 / npm 10.8.2 后实跑；不是代码问题 |
| G4 | **Vitest / build / e2e 的 PASS 全部在 Node 25.9.0 上取得**，非冻结环境 | §1.2 / §1.3 / §1.10 | ⚠️ 同上，环境问题 |
| G5 | **"旧测试零回归"不可判定**：无 pre-change 基线工件，16 个既有 `backend/app/**` 源文件被改、另新增 35 个未跟踪 app 文件 | §2.2 | ⚠️ 部分。需补跑并落盘一次改动前基线；不是代码 bug |
| G6 | **G0 冻结入口未整体执行**，因它含 `npm ci` | §1.12 | ⚠️ 受 G3 阻塞 |
| G7 | **`backend/tests/conftest.py:10` 共享 CWD 相对的 `./test.db` + `:20` 会话末 `drop_all`**：并发 pytest 互相破坏（实测 2 次 16 失败），且每次跑测试都会摧毁工作树里 `test.db` 的全部表 | §1.1 | ✅ 是（改 per-session 临时库） |
| G8 | **台账"16 项 final gates 全部 exit 0"不覆盖 AC-7**：其中没有 mypy、没有 `npm ci` | §1.5 末 / `VALIDATION_EVIDENCE.md:94-113` | ✅ 是（证据登记口径问题：把这两项补进门禁集合） |
| G9 | **`frontend/.npmrc:5-6` 的过期断言**：称 Node 25.9.0 会 vitest 失败，本轮实测 150/150 全绿 | §1.2 | ✅ 是（改注释；`CONTRACT_INDEX.md` 维护规则 14/15 要求重取） |
| G10 | 台账 Black `113 files` vs 实测 `112 files` | §1.4 | ✅ 是（台账转录/时效） |
| G11 | CI `checkout@v4` 检出的提交树不含本轮 157 条工作树改动 → CI 绿灯与当前状态无对应关系 | §2.3 | ⚠️ 流程问题，非单点代码 |

---

## 4. 给下一轮的建议（按顺序）

1. **先修 mypy**（G1）。它单独一条就让 AC-7 判据不成立，且 117 条里大部分是机械收窄，不需要动架构。
2. **补 `scripts/check_contract_ownership.py`**（G2）。没有它 G0 结构上就不可能 PASS，补它比修 mypy 快得多。
3. **切到 Node 18.20.8 / npm 10.8.2 后重跑** `npm ci` → vitest → lint → format → build（G3/G4）。本轮所有前端 PASS 都要在这套环境上重取才算数。
4. **落一份 pre-change 基线工件**（G5），否则"零回归"永远只能停在"不可判定"。
5. 顺手把 `conftest.py` 的共享 `test.db` 换成 per-session 临时库（G7）——这既修并发互毁，也修掉"跑一次测试就 drop 掉工作树 `test.db` 全部表"的副作用。

在 1、2、3 全部完成并由 `verify_hydraulic_stack.py --gate g0` 产出同 RUN_ID 的 `g0/result.json` 之前，**AC-7 应保持 `partial`，不得记 PASS**。
