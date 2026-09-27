# 依赖与配置契约实施摘要

- **模块**：dependency-config
- **实施日期**：2026-09-24
- **基线**：`ca99e048`（工作树已有并行改动；本摘要只归因本 dependency-config 任务实际触及的路径，未提交、未推送）
- **状态**：先前 dependency-config 实现与定向验证摘要；round-3 R0 仍为 `NOT_APPROVED / R0_FAIL`。本次只同步 C-01～C-04 的文档整改边界，仍不代表设计 Frozen、landing、G0～G6 或产品发布通过。

## 1. 契约依据

- 源 PRD 要求 Python 3.11、EPA SWMM 优先和 Windows 可运行的纯 Python 降级路径：`docs/hydraulic-twin/SOURCE_PRD.md:14-21`。
- 补充契约规定 SWMM 使用外部 executable 的 path/version/SHA-256 probe，缺失时冻结到 `py_mw`：`docs/hydraulic-twin/PRD_SUPPLEMENT.md` D-06。
- 配置字段、硬上限和验证要求：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md` §12；实现范围只引用 `DEP-AUTH-06`。
- **全周期 owner**：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md` §15.1～§15.6。owner 表示该路径全生命周期修改权，不自动等于当前阶段已授权落地。
- **唯一阶段授权注册表**：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md` §15.0 `DEP-AUTH-01..13`。R0 PASS 前仅 `DEP-PREP allowed=true` 的 `DEP-AUTH-01..12` 可留在 dependency diff；`DEP-AUTH-13` 只可在 R0 PASS 后生成 handoff。R0 PASS 后才允许 `DEP-LANDING`，handoff 后按 `--phase post-handoff` 的 effective owner table 转交 integration。
- 本摘要不得复制另一套授权路径；owner checker 的 phase 词汇仅为 `current|post-handoff`，当前无 handoff 且 R0 FAIL 时使用 `current`。

## 2. 实际改动

### 2.1 依赖分层与可选 extras

- `backend/requirements-hydraulic.txt` 是 Windows/Python 3.11 纯 Python 降级 profile：包含 API、数据库、配置、NumPy 和 API 图像解码所需 Pillow；明确排除 SWMM、CUDA、Torch、OpenCV、ONNX Runtime、hiredis。`Pillow` 有 Windows wheel，且不是 solver engine。
- `backend/requirements-cpu.txt` 保留 CPU 部署所需的 AI wheel 依赖；`backend/requirements.txt` 保留 full/GPU 依赖。三者均使用 marker-guarded、精确版本 pins，Python 3.11 是契约版本。
- `backend/pyproject.toml` 保持 `requires-python = ">=3.11,<3.12"`，并声明 `hydraulic`、`ai-cpu`、`ai-gpu`、`test` extras。应用启动会无条件导入 NumPy/Pillow，因此两者也列入基础 `dependencies`；`hydraulic` 继续提供对应解释器 wheel pins；`swmm = []`，因为 EPA SWMM 是外部、版本/hash 锁定的 executable，不引入 PyPI native wrapper。
- 三个 hash lock 已重新生成/核验：`backend/requirements-py311.lock`、`backend/requirements-py311-gpu.lock`、`backend/requirements-hydraulic-py311.lock`。最小 fallback lock 不含 `torch`、`opencv`、`onnxruntime`、`hiredis` 或 SWMM 字符串。

### 2.2 pydantic-settings 与安全默认环境变量

- `.env.example` 现在列出 `Settings` 的全部字段（包括 `NETWORK_JOB_ATTEMPT_TIMEOUT_SECONDS`、RSA 私钥/公钥配置），并使用 pydantic-settings 可解析的 JSON 数组格式；未知配置键由 `extra="forbid"` 直接拒绝。
- 所有仿真/网络任务变量按契约记录：并发 2、nowcast 保留 1、attempt 600 秒、最多 3 次、输入 20 MiB、workspace 2 GiB、frame 64 MiB、output 8 GiB、90 天 retention；nowcast 10/120/5/180 分钟/秒；EPA 三个变量默认全空；forecast policy path 默认空且保持 `not_ready`。
- 安全默认值为 `SOLVER_DEFAULT_SELECTION=auto`、`AUTH_FALLBACK_ADMIN_ENABLED=false`（仅 development/test 可显式开启）、`NOTIFICATION_MAX_ATTEMPTS=5`、独立 rate limits 和正数容量上限；模板不写真实 secret，生产仍须注入随机凭据和 RSA 密钥。

### 2.3 测试

- `backend/tests/test_simulation_settings.py` 覆盖 typed JSON/逗号数组解析、契约变量完整性、非法并发/超时/容量/backoff/solver/JWT 值、SWMM executable 缺失/hash 不匹配降级到 `py_mw`、Windows fallback profile 无 native solver、pyproject extras/hash lock 结构。
- 缺失可选 SWMM 时 `resolve_auto_solver()` 和请求 EPA solver 都返回 `py_mw`；配置完整且 probe 成功时优先 `swmm_kw`，运行失败不通过 Settings 静默换 solver。

## 3. 先前 dependency-config 任务实际执行的命令与结果

以下是该实现任务当时记录的定向验证；本次 v3 契约整改未重跑这些命令，不得把历史结果当作当前 G0/G5 或发布证据。

### Python 3.11

1. `cd backend && uv run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -m pytest tests/test_simulation_settings.py -q -p no:cacheprovider`
   - **PASS**：`41 passed, 1 warning in 0.56s`。
   - 警告：既有 pydantic `model_version` protected namespace warning。
2. `cd backend && uv run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -m pytest tests/test_requirements_matrix.py -q -p no:cacheprovider`
   - **PASS**：`29 passed, 2 skipped, 1 warning in 0.07s`。
3. `cd backend && uv run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -m pytest tests/test_simulation_settings.py tests/test_requirements_matrix.py -q -p no:cacheprovider`
   - **PASS**：`70 passed, 2 skipped, 1 warning in 0.60s`。
4. `cd backend && uv run --isolated --no-project --python 3.11 --with-requirements requirements-hydraulic-py311.lock python -c "from app.core.config import Settings; s=Settings(_env_file=None); print(s.resolve_auto_solver(), s.HYDRAULIC_SPATIAL_BACKEND, s.SIM_INPUT_MAX_BYTES)"`
   - **PASS**：`py_mw portable 20971520`。
5. `cd backend && uv pip check --python C:\\Users\\zyq\\AppData\\Local\\uv\\cache\\archive-v0\\41x0ZNIPAy35J9c5\\Scripts\\python.exe`
   - **PASS**：`Checked 154 packages ... All installed packages are compatible`。
6. `cd backend && uv run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock black --check tests/test_simulation_settings.py && ... isort --check-only ... && ... flake8 ...`
   - **PASS**：Black unchanged、isort 无输出、Flake8 无输出。
7. `uv pip compile requirements-hydraulic.txt --python-version 3.11 --universal --generate-hashes --output-file requirements-hydraulic-py311.lock`
   - **PASS**：`Resolved 62 packages`; lock 包含 1,581 个 SHA-256 hash，且无 SWMM/Torch/OpenCV/ONNX Runtime。

### Node 18.20.8 / npm 10.8.2

8. `cd frontend && npx --yes --package=node@18.20.8 --package=npm@10.8.2 --call "node --version && npm --version"`
   - **PASS**：`v18.20.8`、`10.8.2`。
9. `npx ... --call "npm ci --engine-strict --registry=https://registry.npmjs.org"`
   - **PASS**：clean install 成功，`added 580 packages`；npm 报告 9 个依赖树漏洞（5 moderate、3 high、1 critical），未执行破坏性 `npm audit fix --force`。
10. `npx ... --call "npm test"`
    - **PASS**：2 个 test files、8 tests 通过；仅有既有 React/Router warnings。
11. `npx ... --call "npm run lint"`
    - **PASS**：exit 0。
12. `npx ... --call "npm run build"`
    - **PASS**：4042 modules transformed，exit 0；仅有 ECharts chunk >1000 kB warning。
13. `npx ... --call "npm run format:check"`
    - **FAIL**：既有 `frontend/src/**` 36 个文件不符合 Prettier；该路径不在本任务 ownedPaths，未修改。

### 差异卫生

14. `git diff --check`
    - **PASS**：无输出，exit 0。

## 4. 风险与边界

- 三份主文档仍标记 `NOT_APPROVED`；本摘要不作设计冻结、landing、G0～G6 或发布通过声明。设计冻结与实现后 G0～G6/acceptance 是不同阶段，后者只能在新的独立设计复审 PASS、授权 landing 和实现完成后执行。
- 最新 round-3 报告已写入 `reviews/v3-schema-semantics-review-round-3.md` 并同步覆盖 `reviews/v3-schema-semantics-review.md`，确认 §3.1～§3.8、B-01～B-07 与上一轮 §4 PASS 项保持，并发现 C-01～C-04：simulation/calibration generation、retry CHECK DDL、Settings 授权分类和可回溯 bundle。本次只修改契约/摘要，没有实现或运行这些 gate；整改后的 bundle 必须重新独立复审，不能沿用旧 FAIL 报告冒充 PASS。
- “本任务实际改动”是任务归因，不是第二套阶段授权。上文“未修改 `config.py`、CI、Compose、Node 源码或 migration”只描述本任务；是否允许 dependency-config 在 R0 前准备某路径，一律只查 §15.0 `DEP-AUTH-01..13` 的 `DEP-PREP allowed`，当前子集为 `DEP-AUTH-01..12`。`DEP-AUTH-13` 在 R0 PASS 前为 false。工作树中的其他并行改动保持原样，不能据此声称 owner 或授权。
- 最小 fallback lock 已在本机 Windows CPython 3.11.15 安装并导入 Settings；这是先前 dependency-config 定向验证的历史记录，只证明配置层降级选择，不证明实际 solver worker/180 秒性能或 EPA executable 实时运行。
- `pyproject.toml` 的 `swmm` extra 为空是刻意的 external-engine 边界；部署方必须同时提供经过 SHA-256 校验的 `EPA_SWMM_EXECUTABLE`、`EPA_SWMM_VERSION`、`EPA_SWMM_SHA256`。
- Node 依赖审计仍有中危/高危/严重漏洞，且格式检查仍失败；这些问题需要 frontend/security owner 在不越界的单独任务中处理。
- `scripts/check_contract_ownership.py`、`scripts/check_migration_gate.py`、`scripts/check_dependency_handoff.py`、`scripts/verify_hydraulic_stack.py`、三个 benchmark harness 与 `frontend/package.json` 的 `test:perf:twin` 当前均未交付。G1 已要求四表 retry CHECK manifest，G2 已要求 generation-specific resource FK 与 54/47 Settings inventory，G3 已定义四类 retry/calibration lifecycle，G4 已定义 policy sidecar 与 producer/relay 时序，G6 已定义双 profile 命令，但 owner/handoff 检查与 G0～G6/acceptance 仍保持 **NOT RUN**，不得因本摘要中的历史定向测试而记 PASS。
- `reviews/dependency-contract.md` 的历史模块审查仍为 `approved=false`；本次文档任务没有修改 Settings、JWT、SWMM probe、Node registry 或 dependency metadata，因此不得把该历史审查的 HIGH/MEDIUM 项或本摘要的定向测试升级为当前实现通过。
- `mypy`/backend 全量 pytest、Docker、Alembic live、真实 EPA、solver/worker、性能/Compose/G0～G6 未在本任务中运行，不能计为通过。

## 5. 交付物

- 依赖分层与可选 extras：`backend/requirements-hydraulic.txt`、`backend/requirements-cpu.txt`、`backend/requirements.txt`、`backend/pyproject.toml`。
- 可复现锁：`backend/requirements-py311.lock`、`backend/requirements-py311-gpu.lock`、`backend/requirements-hydraulic-py311.lock`。
- 环境变量与安全默认：`.env.example`。
- 配置/降级测试：`backend/tests/test_simulation_settings.py`。
- 本摘要：`docs/hydraulic-twin/implementation/dependency-contract.md`。
