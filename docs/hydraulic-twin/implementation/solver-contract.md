# Solver Contract / SWMM Adapter 实施报告

- **实施日期**：2026-09-25
- **模块**：求解器统一契约、solver registry、EPA SWMM 能力探测/适配
- **工作树基线**：`ca99e048`（本轮读取 `git rev-parse --short=8 HEAD` 的输出）
- **提交/推送**：未提交、未推送
- **结论**：契约层、注册选择层、可注入的 SWMM 进程适配层和 fake-SWMM 契约测试已完成；真实 EPA SWMM 二进制/`.out` parser、worker wiring 和 live Windows orphan-process 验证未在本轮执行，不能宣称 EPA 端到端或 Job Object 门禁已通过。

## 1. 规范依据（已读取）

1. 源 PRD 的 FR-3 要求 EPA SWMM 优先、纯 Python Windows 降级、0--100 进度、10 分钟超时、最多两次重试和沙箱（`docs/hydraulic-twin/SOURCE_PRD.md:20`）。
2. 冻结补充 PRD D-06 固定 `swmm_kw`、`swmm_dw`、`py_mw`、`py_dw`、`py_ponding` 身份，并规定 `auto|explicit`、提交时 probe、运行中不得换 solver（`docs/hydraulic-twin/PRD_SUPPLEMENT.md:144-156`）。
3. 实施契约的 `SolverAdapter`/`FrameSink` 协议、流式 frame、EPA path/version/SHA-256、`Popen(..., shell=False)` 和 explicit 不降级语义在 `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1313-1359`；进度阶段在 `:1407-1420`；Windows/Job Object 要求在 `:2152-2157`。
4. 现有配置探测已经有 EPA path/version/hash 事实来源（`backend/app/core/config.py:520-573`），现有 worker/sandbox 已有进程隔离与 attempt workspace 模式（`backend/app/workers/simulation_worker.py:147-229`、`backend/app/services/hydraulic/sandbox.py:138-180`），现有结果层要求 sink finalize 原子发布（`backend/app/services/hydraulic/results.py:543-705`）。

## 2. 实际改动

### 2.1 `backend/app/services/hydraulic/solvers/base.py`

- 保留并完善统一协议 DTO：`SolverCapabilities`、`SolverProbe`、`SolverSelectionResult`、`SolverDegradationReport`、`ValidationReport`、`SimulationFrame`、`SolveSummary`、`FrameSink`、`RunControl`（`base.py:189-350`、`base.py:458-613`）。`SolveSummary` 现在显式携带 network revision、materialized snapshot、initial state、target manifest、rainfall 和 solver config hash，并校验结果状态和有限数值（`base.py:458-573`）。
- 增加 `SolverInputManifest`/`SolverInput`（`base.py:1065-1205`）。`input_sha256` 绑定五类独立内容 hash、solver/config/schema、CRS/transform、开始时间、持续时间、帧间隔/帧数和 ponding 开关；`compute_solver_input_hash` 保留兼容参数，同时支持嵌套 `input_manifest` 和独立 snapshot/config hash（`base.py:893-1046`）。
- 增加严格的 0--100 进度事件、同步/异步 callback、`RunControl.check()` 的取消/deadline 检查和单调 `ProgressTracker`（`base.py:589-791`）。测试中发现并修复了原文件 `ValidationIssue.field` 类属性遮蔽 dataclasses `field` 的导入冲突：统一改用 `dataclasses_field`（`base.py:29-30`、`base.py:353-377`）。
- `SolverRegistry` 现在支持注册、probe、显式/自动选择和完整能力报告（`base.py:1309-1584`）：
  - `explicit` 只允许请求的 solver，probe 不可用即 `SOLVER_UNAVAILABLE`；
  - `auto` 无请求时按 `swmm_kw -> py_mw`，有请求时先请求 solver，再在注册了 fallback 时冻结 `py_mw`；
  - 报告保留每个 probe、fallback reason 和 degraded 状态；
  - 选择结果只负责提交时冻结身份，运行时异常不会由 registry 换 solver；
  - 提供 `create_solver_registry`，默认 registry 仍为空，避免 import-time 加载可选 native engine（`base.py:1601-1616`）。

### 2.2 `backend/app/services/hydraulic/solvers/swmm_adapter.py`

- 新增无 import-time EPA/native 依赖的 `SwmmAdapter`（别名 `SWMMAdapter`、`EpaSwmmAdapter`），可从 settings 构造（`swmm_adapter.py:242-327`）。不配置时 probe 返回结构化 `not_configured`，不会阻塞 Windows 启动；配置完整时异步在线程中校验文件 SHA-256，并检查 parser 与 engine version 绑定（`swmm_adapter.py:330-393`）。`capability_report()/probe_report()` 可单独输出该 adapter 的能力/降级事实（`swmm_adapter.py:395-423`）。
- `SwmmSolveRequest` 限制 materialised INP 为 20 MiB；adapter 只使用 attempt workspace 内固定文件名 `simulation.inp/.rpt/.out`，不接收用户 filename（`swmm_adapter.py:213-229`、`swmm_adapter.py:720-771`）。
- 默认 runner 使用 `subprocess.Popen` 的 argv list、`shell=False`；Windows 设置 `CREATE_NEW_PROCESS_GROUP`，超时/取消时先 `taskkill /T /F` 再回退 leaf kill，POSIX 使用独立 process group（`swmm_adapter.py:103-190`）。
- `run()` 按 `validation -> solving -> parsing -> persisting -> completed` 发进度，校验 solver/attempt/frame index/时间网格/target manifest，逐帧写 sink，只有完整结果才调用 `finalize`（`swmm_adapter.py:484-688`）。默认 deadline 为 600 s，也接受 `RunControl.deadline_at`/`timeout_seconds`；取消轮询不超过 0.5 s（`swmm_adapter.py:776-835`）。
- probe/hash/version、启动异常、非零退出、超时、取消、输出缺失、parser 异常和 frame schema 异常均映射为稳定 solver error；runtime failure 不会调用 Python fallback。`SWMM_DEFAULT_TIMEOUT_SECONDS`、输入上限和 adapter/parser 版本写入结果诊断（`swmm_adapter.py:49-61`、`swmm_adapter.py:576-688`）。

### 2.3 `backend/tests/hydraulic/test_solver_contract.py`

新增 9 个 fake SWMM 契约测试（fake process/runner/parser/sink，不启动真实 EPA）：

- 调用 argv、`shell=False`、sandbox 文件、frame streaming、summary/finalize 和完整进度序列（`test_solver_contract.py:211-254`）；
- canonical input hash 重放稳定且五类独立材料/config/时间网格变化会改变 hash（`:257-280`）；
- 超时杀进程且不写 frame/summary（`:283-303`）；
- 启动异常稳定映射且不发布结果（`:306-334`）；
- 已冻结的 SWMM 运行失败绝不切换 Python fallback（`:337-363`）；
- auto 不可用时记录 `not_configured`、选择 `py_mw` 并生成完整降级报告（`:366-394`）；
- explicit 不可用拒绝、不降级（`:397-409`）；
- Windows 缺少 EPA 时 probe 在 0.2 s 内返回（`:412-426`）；
- binary SHA-256 mismatch 在提交选择阶段被报告并降级（`:429-450`）。

## 3. 本轮实际执行的验证

> 测试命令使用工作区已有 `.venv`（Python 3.14.4）；Python 3.11.15 只执行了 compile/import smoke，因为该 3.11 环境没有 pytest（实际命令输出为 `No module named pytest`）。

### 3.1 目标 fake-SWMM 测试（PASS）

```text
PYTHONPATH="C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/.venv/Scripts/python.exe" -m pytest "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/tests/hydraulic/test_solver_contract.py" -q
9 passed, 448 warnings in 0.12s
```

### 3.2 全 hydraulic 测试目录（PASS）

```text
PYTHONPATH="C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/.venv/Scripts/python.exe" -m pytest "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/tests/hydraulic" -q
169 passed, 5470 warnings in 1.45s
```

### 3.3 既有 EPA settings 兼容测试（PASS）

```text
PYTHONPATH="C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/.venv/Scripts/python.exe" -m pytest "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/tests/test_simulation_settings.py" -q
41 passed, 739 warnings in 0.54s
```

### 3.4 格式、导入、类型和 Python 3.11 编译（PASS）

```text
black --check <base.py> <swmm_adapter.py> <test_solver_contract.py>
=> All done! 3 files would be left unchanged.

isort --check-only <same three files>
=> exit 0（无输出）

mypy --config-file backend/pyproject.toml <base.py> <swmm_adapter.py>
=> Success: no issues found in 2 source files

py -V:Astral/CPython3.11.15 -m py_compile <same three files>
=> exit 0（无输出）
```

### 3.5 Windows/无 native engine smoke（PASS，非 live EPA）

```text
PYTHONPATH="C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" py -V:Astral/CPython3.11.15 -c "import asyncio; from app.services.hydraulic.solvers.swmm_adapter import SwmmAdapter; a=SwmmAdapter('swmm_kw'); p=asyncio.run(a.probe()); r=asyncio.run(a.capability_report()); print(f'{p.solver_id} available={p.available} reason={p.reason} parser={a.parser_version} report_fallback={r.fallback_solver_id}')"
swmm_kw available=False reason=not_configured parser=unbound report_fallback=py_mw
```

### 3.6 Flake8

- `flake8 --ignore=E501,E704,W503 <three files>`：exit 0（可执行的非行长/既有风格规则通过）。
- 直接 `flake8 <three files>`：exit 1，输出为项目当前 79 列限制与 Black 88 列格式冲突的 `E501`（例如 `base.py:236`、`swmm_adapter.py:145`、`test_solver_contract.py:91`）；本轮未修改 pyproject/配置，也未把行长问题伪装成通过。

## 4. 风险、未完成项和边界

1. **没有真实 EPA SWMM 运行证据**：环境没有可执行 EPA binary、`swmmtoolbox`/native parser（实际检查 `swmmtoolbox=False`、`swmm=False`）。当前 adapter 接受注入的 `SwmmOutputParser`；若配置了 binary 但未提供 parser，probe fail-closed 为 `output_parser_not_configured`。因此本轮只能证明 fake engine 的调用/清理/契约，不证明真实 `.out` 解析、EPA 数值保真或 SWMM benchmark。
2. **Windows Job Object 仍有差距**：当前实现是 `CREATE_NEW_PROCESS_GROUP + taskkill /T /F` process-tree cleanup（`swmm_adapter.py:116-145`），没有 native Job Object。冻结契约 `IMPLEMENTATION_CONTRACT.md:2157` 明确要求 Job Object 和无孤儿测试；本轮只完成 fake process kill，未宣称该门禁 PASS。
3. **注册 wiring 不在本 ownedPaths**：`IMPLEMENTATION_CONTRACT.md:2288-2292` 还列出独立 `registry.py` 和 `python_mw.py` owner，但本任务允许修改列表不含这些路径。因此 registry 放在 `base.py`，默认 registry 保持空；实际应用必须由其 owner 注册 `py_mw`/其他 solver，不能假设本模块自动加载 fallback。
4. **未接入 simulation worker/DB**：本轮没有修改 `simulation_worker.py`、task repository 或结果 repository；adapter 已按 `FrameSink`/`RunControl` 边界提供接口，但 attempt fencing、数据库 finalize 和重试仍由其他 owner 接线。
5. **运行时版本差异**：pytest 实际运行在 `.venv` Python 3.14.4；Python 3.11.15 已完成 compile/import smoke，但没有 3.11 pytest suite。FastAPI/pytest-asyncio 的 deprecation warnings 仍存在，未由本模块引入修复。
6. **未运行**：真实 EPA、`.rpt/.out` parser、Docker/Compose、AC-1～AC-8、5000/10000/24h 性能门禁、真实 Windows orphan-process 测试均 NOT RUN。

## 5. 变更文件清单

仅修改/新增本任务允许的生产文件和测试文件，以及任务要求的本报告：

- `backend/app/services/hydraulic/solvers/base.py`
- `backend/app/services/hydraulic/solvers/swmm_adapter.py`
- `backend/tests/hydraulic/test_solver_contract.py`
- `docs/hydraulic-twin/implementation/solver-contract.md`

没有修改 `python_hydraulics.py`、worker、ORM、migration、requirements、配置或其他 owner 文件；没有 commit/push。
