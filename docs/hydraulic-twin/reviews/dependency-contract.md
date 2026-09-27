# 依赖与配置契约独立审查

- **模块**：`dependency-contract`
- **审查日期**：2026-09-24
- **基线**：`ca99e048`（工作树已有并行改动）
- **结论**：**不批准**。发现 4 个 high、5 个 medium；存在配置被静默忽略、部署安全门禁缺口和生产 JWT 不可用等阻断问题。
- **审查方式**：只读核对冻结契约、实施摘要、真实源码、依赖文件和指定测试；只运行任务指定的两个测试文件及轻量诊断，没有重复全量门禁，也没有修改实现代码。

## 审查依据

- 任务规格要求：Python 3.11 / Node 18 的最小可复现依赖；EPA SWMM 仅作可选外部能力；纯 Python 降级路径不能依赖 Windows 不可用的原生库；所有仿真变量统一由 pydantic-settings 解析。
- 冻结契约的槽位和 nowcast 约束见 `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:712,1844,1863-1865,1876,1882,1894`；EPA 的提交时 probe 规则见 `docs/hydraulic-twin/PRD_SUPPLEMENT.md:154`。
- 依赖分层、锁文件、`.env.example` 和测试的当前真实内容分别见 `backend/pyproject.toml`、`backend/requirements*.txt`、`backend/requirements-*.lock`、`.env.example`、`backend/tests/test_simulation_settings.py`。

## Findings

### HIGH-1：契约要求的 `NETWORK_JOB_ATTEMPT_TIMEOUT_SECONDS` 根本没有进入 Settings

**证据**

- `.env.example:72-79` 声明了 `NETWORK_JOB_ATTEMPT_TIMEOUT_SECONDS=600`。
- `backend/app/core/config.py:328-340` 定义了 network job 的并发、lease、heartbeat、attempts、backoff 和 input 上限，但没有 attempt-timeout 字段；同时 `model_config` 在 `config.py:84-89` 使用 `extra="ignore"`。
- `backend/tests/test_simulation_settings.py:53-60` 把该键放入契约集合，但 `test_env_example_contains_every_contract_variable` 在 `test_simulation_settings.py:237-247` 只做子集断言，因此这个“模板有、实现没有”的差异不会失败。
- 冻结契约 `IMPLEMENTATION_CONTRACT.md:1875-1881` 明确列出该变量。

**复现**

执行：

```text
cd backend && uv run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python - <<'PY'
import os
from app.core.config import Settings
print([k for k in Settings.model_fields if k == "NETWORK_JOB_ATTEMPT_TIMEOUT_SECONDS"])
os.environ["NETWORK_JOB_ATTEMPT_TIMEOUT_SECONDS"] = "123"
s = Settings(_env_file=None)
print(hasattr(s, "NETWORK_JOB_ATTEMPT_TIMEOUT_SECONDS"), s.model_dump().get("NETWORK_JOB_ATTEMPT_TIMEOUT_SECONDS"))
PY
```

输出：

```text
[]
False None
```

同一诊断还确认 `NETWORK_JOB_LEASE_SECONDS=60` 与 `NETWORK_JOB_HEARTBEAT_SECONDS=30` 会被接受；当前实现只检查二者的大小关系，没有把 D-15 的 30s/10s 协议边界落实为配置约束。

**影响**

操作者修改 600 秒或任何超时值都会被静默丢弃，network job 没有可审计的契约化 timeout；在 `extra="ignore"` 下，拼写错误也会伪装成成功配置。

**建议**

增加字段、正数/上限/固定协议校验，并让 worker 使用该字段；把环境模板键与 `Settings.model_fields` 做精确集合校验（允许显式声明的 `SCN_ENV_FILE` 例外），增加超时越界和 lease/heartbeat 越界测试。

### HIGH-2：staging 仍可开启固定 admin fallback，数据库故障时可直接铸造 admin token

**证据**

- `.env.example:104-106` 和 `README.md:428` 明确写着该开关“仅 development/test 可显式开启”。
- `config.py:343-350` 将 `staging` 定义为合法环境，但 `auth_fallback_admin_allowed` 在 `config.py:457-467` 只排除 `production`，没有限制为 `development|test`。
- `backend/app/api/routes.py:115-135` 在数据库不可用或用户不存在时直接使用该属性，并接受固定 `admin / Admin@123456`。
- 现有测试 `test_simulation_settings.py:249-277` 只覆盖 production 和 development，没有 staging 负向用例。

**复现**

执行：

```text
cd backend && uv run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python - <<'PY'
import asyncio
from contextlib import asynccontextmanager
from app.api import routes
from app.schemas.schemas import TokenRequest

@asynccontextmanager
async def unavailable():
    raise RuntimeError("database unavailable")
    yield

async def main():
    old_session, old_env, old_flag = routes.get_db_session, routes.settings.DEPLOYMENT_ENVIRONMENT, routes.settings.AUTH_FALLBACK_ADMIN_ENABLED
    routes.get_db_session, routes.settings.DEPLOYMENT_ENVIRONMENT, routes.settings.AUTH_FALLBACK_ADMIN_ENABLED = unavailable, "staging", True
    try:
        result = await routes.login(TokenRequest(username="admin", password="Admin@123456"))
        print(result.role, result.access_token[:16])
    finally:
        routes.get_db_session, routes.settings.DEPLOYMENT_ENVIRONMENT, routes.settings.AUTH_FALLBACK_ADMIN_ENABLED = old_session, old_env, old_flag

asyncio.run(main())
PY
```

输出包含：

```text
staging_login_role= admin token_prefix= eyJhbGciOiJIUzI1
```

**影响**

任何将 staging 暴露给测试/运维网络、并显式打开该开关的部署，都能在数据库故障时获得固定 admin 身份；这不是“仅开发便利开关”，而是可被远程触发的认证绕过。

**建议**

将属性改为 `DEPLOYMENT_ENVIRONMENT in {"development", "test"}`，对 staging 的 `true` 在 Settings 构造时直接拒绝，并增加 staging + DB outage 的 API 负向测试。

### HIGH-3：按契约启用 RS256 后，production 认证无法签发 token

**证据**

- 冻结契约 `IMPLEMENTATION_CONTRACT.md:1882` 指定 `JWT_ALGORITHMS=RS256`；`IMPLEMENTATION_CONTRACT.md:1894` 要求 production 的 JWT 配置通过安全门禁。
- `.env.example:96-102` 已明确承认当前没有 RSA 私钥配置项，但仍保留 `JWT_SECRET_KEY` 作为唯一密钥。
- `config.py:129-134` 只有 `JWT_SECRET_KEY`，没有算法对应的私钥/公钥或密钥路径字段。
- `backend/app/core/security.py:69-78` 用该字符串调用 `jwt.encode(..., algorithm=settings.JWT_ALGORITHM)`；`security.py:105-111` 也用同一字段验证。

**复现**

执行：

```text
cd backend && env DEPLOYMENT_ENVIRONMENT=production AUTH_FALLBACK_ADMIN_ENABLED=false JWT_SECRET_KEY=a-random-production-secret JWT_ALGORITHM=RS256 'JWT_ALGORITHMS=["RS256"]' JWT_ISSUER=smart-city-drainage JWT_AUDIENCE=smart-city-api INFLUXDB_TOKEN=a-real-looking-influx-token DATABASE_URL=postgresql+asyncpg://user:real-password@db:5432/app DATABASE_URL_SYNC=postgresql://user:real-password@db:5432/app REDIS_URL=redis://:real-password@redis:6379/0 uv run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -c "from app.core.config import settings; from app.core.security import create_access_token; print(settings.JWT_ALGORITHM); create_access_token(\"u\")"
```

Settings 成功加载，但输出为：

```text
RS256
JWSError Unable to load PEM file. See https://cryptography.io/en/latest/faq/#why-can-t-i-import-my-pem-file for more details. MalformedFraming
```

**影响**

一个满足当前 production Settings 校验的部署会在登录/签发路径返回 500；默认模板没有可用的 RSA 密钥材料，契约要求的 RS256 部署不可运行。

**建议**

增加受控的 RSA 私钥/公钥（最好是受控路径或 secret provider）配置，按算法选择签名/验证密钥，禁止把对称 `JWT_SECRET_KEY` 当作 PEM；增加 production RS256 登录和错误密钥负向测试。

### HIGH-4：SWMM availability 被 Settings 永久缓存，不能履行“提交时 probe”

**证据**

- `PRD_SUPPLEMENT.md:154` 要求 `auto` 在提交时 probe，EPA 不可用时冻结到 `py_mw`。
- `config.py:91-92` 为每个实例设置 `_swmm_probe`；`config.py:483-485` 一旦有缓存就直接返回，`config.py:517-518` 将结果永久写回；模块级单例在 `config.py:579` 创建。
- `config.py:499-515` 只检查文件存在/非 Windows 执行权限、版本字符串格式和 SHA-256，没有执行外部程序或核对程序报告的版本。
- `test_simulation_settings.py:89-103` 实际用任意字节文件并期待 `available=True`，没有覆盖文件删除、替换或真实 executable 失败。

**复现**

诊断先创建一个内容为 `not a real solver`、哈希匹配的文件，调用 `probe_epa_swmm()`，再删除文件并再次调用同一 Settings 实例；输出为：

```text
swmm_first: True verified
swmm_after_delete: True verified
```

**影响**

外部 executable 在进程运行期间被删除、替换或损坏后，Settings 仍会报告可用并可能选择 SWMM，违反提交时重新 probe 和安全降级契约；初始 probe 也不能区分“哈希匹配的任意文件”和可运行的 EPA SWMM。

**建议**

不要在全局 Settings 中永久缓存 probe；按提交建立 probe，或至少以文件 stat/哈希变化失效缓存。定义并实现真实 executable/version 验证（Windows 也不能跳过所有可运行性检查），并增加删除、替换、非 SWMM 文件测试。

### MEDIUM-1：`SIM_NOWCAST_RESERVED_SLOTS=0` 被接受，违反固定 Literal[1]

**证据**

- `IMPLEMENTATION_CONTRACT.md:712,1844,1894` 明确要求该值为固定 `1`。
- `config.py:192-198` 将字段声明为 `int` 且只设 `ge=0`；`config.py:359-362` 只验证它小于全局并发 2。
- 测试 `test_simulation_settings.py:164-170` 只拒绝值 2，没有拒绝 0。

**复现输出**：

```text
reserved_slots_zero: 0
```

**影响**

部署可以关闭 nowcast 保留槽，破坏固定 general/nowcast 槽模型和 3 分钟 SLO 的容量前提。

**建议**

使用 `Literal[1]` 或等价的精确 validator，并增加 0、负数、非整数测试。

### MEDIUM-2：nowcast horizon/step 只检查整除，不检查 0–2 小时和 5 分钟契约

**证据**

- `SOURCE_PRD.md:23` 要求 0–2h、5 分钟步长；`IMPLEMENTATION_CONTRACT.md:1863-1865` 固定 horizon 120、step 5。
- `config.py:315-318` 对 horizon 和 step 只有 `gt=0`；`config.py:363-366` 只检查 `horizon % step == 0`。
- 测试 `test_simulation_settings.py:199-200` 用 `121`（恰好不可整除）作为非法样例，未覆盖可整除但超过 120 的值，也未覆盖 step=1。

**复现输出**：

```text
horizon_125 accepted {'NOWCAST_HORIZON_MINUTES': 125, 'NOWCAST_STEP_MINUTES': 5}
step_1 accepted {'NOWCAST_STEP_MINUTES': 1, 'NOWCAST_HORIZON_MINUTES': 120}
```

**影响**

会产生契约未允许的 1 分钟网格或超过 2 小时的 forecast，改变 API/SSE/抑制逻辑和 nowcast SLO 假设。

**建议**

按冻结语义限制 horizon `<=120`、step 为允许的 5 分钟值（或明确 Literal），并添加 125、1、15 等边界测试。

### MEDIUM-3：相对 `SCN_ENV_FILE` 仍依赖当前工作目录，和文档承诺相反

**证据**

- `.env.example:4-8`、`README.md:217-218` 声称加载与进程 cwd 无关，并优先使用 `SCN_ENV_FILE`。
- `config.py:56-70` 对 override 只做 `Path(override).expanduser()`，没有绝对化或锚定到项目根。
- `test_simulation_settings.py:337-341` 只测试绝对 override；没有相对路径测试。

**复现输出**：

```text
relative_env_file: relative.env absolute: False exists_from_cwd: False
```

**影响**

从不同 cwd 启动时，同一个相对 `SCN_ENV_FILE` 会指向不同位置或静默找不到配置，随后回退到代码默认凭据/连接串。

**建议**

要求 override 为绝对路径，或统一锚定到明确的项目根/配置根；对相对路径给出启动错误而不是静默忽略。

### MEDIUM-4：Node 18 的 npm 版本和 registry 没有被项目配置真正锁定

**证据**

- `frontend/.node-version:1` 固定 Node 18.20.8；`frontend/package.json:6-10` 却允许 Node `>=18.18.0 <21`、npm `>=9 <11`，`packageManager` 字段本身不是 npm 的强制门禁。
- `frontend/.npmrc:1-5` 只有 `engine-strict=true`，没有 `registry=` 或 npm 10.8.2 的精确约束。
- 冻结契约 `IMPLEMENTATION_CONTRACT.md:2041-2044,2064-2067` 要求 Node 18.20.8、npm 10.8.2 和可复现安装。

**复现**：在 Node 18.20.8 下使用 npm 9.9.4 执行

```text
npx --yes --package=node@18.20.8 --package=npm@9.9.4 --call "node --version && npm --version && npm ci --dry-run --engine-strict --ignore-scripts"
```

输出：

```text
v18.20.8
9.9.4
added 618 packages in 27s
```

同一环境的

```text
npx --yes --package=node@18.20.8 --package=npm@10.8.2 --call "npm config get registry"
```

输出为 `https://registry.npmmirror.com`；使用该有效 registry 执行 `npm audit` 返回 `404 ... /-/npm/v1/security/* not implemented yet`。显式指定官方 registry 后，生产依赖 audit 报告 4 个 moderate（ECharts、React Router），没有 high/critical。

**影响**

Node 18 本身可安装，但 npm 9 也会被接受，安装/审计行为受机器级 registry 影响，不能称为严格可复现的工具链。

**建议**

用精确 `engines`（或项目 wrapper/Corepack 强制）锁定 Node 18.20.8/npm 10.8.2，并在项目 `.npmrc` 明确 registry；为 audit/安装增加 CI 断言。ECharts/React Router 的 moderate 依赖另行安排兼容升级。

### MEDIUM-5：pyproject 的默认安装不包含应用启动时必选的 NumPy/Pillow

**证据**

- `backend/pyproject.toml:9-28` 的 base `dependencies` 没有 `numpy` 或 `Pillow`；它们只在 `hydraulic` extra（`pyproject.toml:35-39`）出现。
- `backend/app/main.py:31` 导入 routes，routes 在 `backend/app/api/routes.py:56` 无条件导入 `inference_service`，而 `backend/app/services/inference_service.py:16-17` 无条件导入 NumPy/Pillow。
- `requirements-hydraulic.txt:38-44` 确实包含二者，因此锁文件路径可用；这反而说明 base package metadata 与实际应用导入链不一致。

**诊断输出**：

```text
base_has_numpy False base_has_pillow False
hydraulic_extra ["numpy==1.26.4 ; python_version < '3.13'", "numpy==2.3.4 ; python_version >= '3.13'", "Pillow==10.2.0 ; python_version < '3.13'", "Pillow==12.1.0 ; python_version >= '3.13'"]
```

**影响**

标准 `pip install .` 或未选择正确 extra 的消费者可能在导入 `app.main` 时得到 `ModuleNotFoundError`；当前通过的 fallback 诊断只证明 requirements lock 路径，不证明 package metadata 自洽。

**建议**

把应用启动必需依赖放入 base，或将图像/推理导入改为真正的可选能力并在 metadata/README 中声明所需 extra；增加从干净环境 `pip install .`（及每个 extra）的 import smoke test。

## 已验证的正向结果

1. 任务指定 settings 测试：

   ```text
   cd backend && uv run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -m pytest tests/test_simulation_settings.py -q -p no:cacheprovider
   41 passed, 1 warning in 0.55s
   ```

2. 任务指定依赖矩阵测试：

   ```text
   cd backend && uv run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -m pytest tests/test_requirements_matrix.py -q -p no:cacheprovider
   29 passed, 2 skipped, 1 warning in 0.07s
   ```

3. 最小 fallback lock 的真实导入/Settings 选择：

   ```text
   cd backend && uv run --isolated --no-project --python 3.11 --with-requirements requirements-hydraulic-py311.lock python -c "from app.main import app; from app.core.config import Settings; print(app.title, Settings(_env_file=None).resolve_auto_solver())"
   Smart City Neural Endpoints py_mw
   ```

   这证明当前 Windows 主机上该 lock 可导入并选择 `py_mw`；不证明真实 solver、EPA executable 或 180 秒性能。

4. Node 18.20.8/npm 10.8.2 的 lockfile dry-run：

   ```text
   cd frontend && npx --yes --package=node@18.20.8 --package=npm@10.8.2 --call "node --version && npm --version && npm ci --dry-run --engine-strict --ignore-scripts"
   v18.20.8
   10.8.2
   added 38 packages in 825ms
   ```

5. `git diff --check`：`exit=0`。

## 未运行 / 不计入通过

- backend 全量 pytest、mypy、Docker build、Compose、Alembic live、GPU/CUDA/ONNX Runtime 实机安装。
- 真实 EPA SWMM executable/version probe、solver worker、性能基准。
- Node 18 下实际（非 dry-run）Vitest/lint/build、npm audit 的默认 registry 路径。
- 任何实现代码修改或依赖修复；本审查只写入本报告。

## 最终判定

`approved=false`。指定 settings/matrix 测试通过并不能消除上述运行时配置和安全缺口；在 HIGH-1～HIGH-4 修复并增加对应负向测试前，不应将本模块视为可落地或可发布。
