# Hydraulic API implementation summary

## Scope and evidence

本轮按任务要求先读取了：

- `docs/hydraulic-twin/SOURCE_PRD.md:16-24,30-32`（管网/降雨/仿真/预报/API 原始需求）；
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:231-251,270-290,489-560`（幂等、角色、错误 envelope、导入安全、forecast 状态与响应约束）；
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1466-1754`（REST 路径、角色、请求/响应和 SSE 契约）；
- 现有 `backend/app/api/routes.py`、`backend/app/main.py`、`backend/app/core/security.py`、`backend/app/schemas/hydraulic_schemas.py`、`backend/app/services/hydraulic/{geometry,rainfall,topology,seed,importers,exporters}.py`。

本轮只编辑了任务拥有的 API/测试文件；没有提交或推送。工作树中其他模块的并行改动未触碰。

## Real changes

### 1. Hydraulic API surface

新增 `backend/app/api/hydraulic_routes.py`：

- 路由注册和 typed request models：`hydraulic_routes.py:60-522`；
- JWT Bearer 校验、viewer/operator/admin 依赖、未知角色 fail-closed：`hydraulic_routes.py:119-178`；
- 8..128 字符 `Idempotency-Key` 校验：`hydraulic_routes.py:181-195`；
- 可注入的 API 状态适配器、canonical JSON/hash、分页 cursor：`hydraulic_routes.py:264-309`、`hydraulic_routes.py:822-902`；
- 20 MiB 流式上传预算、文件名/路径/二进制/UTF-8/GeoJSON/INP 预检：`hydraulic_routes.py:996-1098`；
- network CRUD、archive、revision clone/activate、nodes/pipes/catchments/elevation-profile CRUD：`hydraulic_routes.py:1258-2320`；
- import/validate/seed job envelope、job/import/validation 查询：`hydraulic_routes.py:1314-1376`、`hydraulic_routes.py:2418-2740`；
- target manifest、portable bbox 精确过滤、稳定 feature index、GeoJSON/INP export：`hydraulic_routes.py:1460-1469`、`hydraulic_routes.py:2321-2556`；
- rainfall scenario list/create/read/update/delete/copy：`hydraulic_routes.py:2769-3000`；
- simulation submit/list/read/cancel/result/frame index/frame/observed comparison/A-B compare：`hydraulic_routes.py:3001-3505`；
- calibration submit/get 和 simulation calibration：`hydraulic_routes.py:3506-3621`；
- forecast latest/horizon/risk-ranking/alerts/manual transition/SSE：`hydraulic_routes.py:3622-3868`。

已覆盖任务要求的 networks/import/export/validate/seed、rainfall-scenarios、simulations、results、calibrations、forecast 路由，并在 `/openapi.json` 中生成请求模型和路径。

### 2. Router integration

- `backend/app/api/routes.py:31` 导入 hydraulic router；
- `backend/app/api/routes.py:1092-1095` 在 legacy `/api` router 末尾合并 hydraulic router，保留旧路由优先级和同一 OpenAPI 文档。

### 3. Error/validation contract

`backend/app/main.py:323-417` 现在统一输出：

- `detail`、`code`、`request_id`、`errors`；
- `RequestValidationError` 的字段化 `errors`；
- HTTP 错误状态到稳定 code 的映射；
- `X-Request-ID` 回传；
- 500 也保留结构化 envelope，不把异常细节返回给客户端。

### 4. API tests

新增 `backend/tests/api/test_hydraulic_api.py:1-331`，覆盖：

- network/entity CRUD、revision 变更、bbox 过滤、viewer 写操作 403、缺 token 401：`test_hydraulic_api.py:67-126`；
- 路径穿越、20 MiB+1、合法 GeoJSON 幂等重放：`test_hydraulic_api.py:130-180`；
- rainfall create/replay/copy/read：`test_hydraulic_api.py:184-230`；
- simulation submit、initial state、cancel 幂等、forecast not-ready/ranking：`test_hydraulic_api.py:234-313`；
- OpenAPI 路径完整性：`test_hydraulic_api.py:317-331`。

## Checks actually run

所有命令均从 `C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage\backend` 执行。

| 命令 | 实际结果 |
|---|---|
| `.venv/Scripts/pytest.exe -q tests/api/test_hydraulic_api.py` | **5 passed**, 524 warnings |
| `.venv/Scripts/pytest.exe -q tests/api/test_hydraulic_api.py tests/test_api/test_auth.py tests/test_api/test_health.py tests/test_api/test_devices.py` | **19 passed**, 1273 warnings |
| `.venv/Scripts/python.exe -m py_compile app/main.py app/api/routes.py app/api/hydraulic_routes.py tests/api/test_hydraulic_api.py` | exit 0 |
| `.venv/Scripts/black.exe --check app/main.py app/api/routes.py app/api/hydraulic_routes.py tests/api/test_hydraulic_api.py` | `4 files would be left unchanged` |
| `.venv/Scripts/isort.exe --check-only app/main.py app/api/routes.py app/api/hydraulic_routes.py tests/api/test_hydraulic_api.py` | exit 0 |
| `.venv/Scripts/flake8.exe app/main.py app/api/routes.py app/api/hydraulic_routes.py tests/api/test_hydraulic_api.py` | exit 0 |
| `.venv/Scripts/python.exe -c "from app.main import app; s=app.openapi(); print(len(s['paths']), '/api/networks/{network_id}/validate' in s['paths'])"` | `70 True` |

### Full-suite result

执行 `.venv/Scripts/pytest.exe -q` 的最终结果为 **399 passed, 4 skipped, 5 failed**。失败均在本任务 owned paths 之外，且是当前并行工作树已有门禁状态：

1. `tests/test_contract_gate_scripts.py::test_live_migration_gate_passes_without_running_alembic`：`0002_hydraulic_schema.py` 路径集与签名 artifact 不一致；
2. `tests/test_contract_gate_scripts.py::test_migration_gate_cli_accepts_workspace_prefixed_paths`：同一 migration gate 失败；
3. `tests/test_contract_gate_scripts.py::test_live_dependency_handoff_matches_fixture_and_settings`：`.env.example` SHA-256 与 handoff 不一致；
4. `tests/test_contract_gate_scripts.py::test_dependency_handoff_cli_accepts_workspace_prefixed_paths`：同一 dependency handoff 失败；
5. `tests/test_contract_gate_scripts.py::test_frontend_performance_entrypoint_remains_deferred`：并行前端性能文件已存在但旧测试仍要求不存在。

这些失败没有由本轮四个 owned API 文件引起；本轮没有覆盖对应 owner 的文件来“修复”它们。

## Risks / handoff requirements

1. `HydraulicApiStore` 是 API contract adapter，目前为进程内状态，**不是 PostgreSQL/Timescale durable repository**。生产接入必须将其替换为 `network_jobs`、`idempotency_records`、simulation/forecast/calibration 的 SQLAlchemy repository，并由 worker 负责 parser/validator/seed/solver；否则重启、多进程和 exactly-once 语义不成立（见 `hydraulic_routes.py:5-8,264-309`）。
2. 为使 API 测试可独立运行，import/validate/seed 当前在 adapter 内生成可查询的 candidate/result，同时仍返回 202 envelope；接入 durable worker 时应移除 API 进程内 materialize，并让 GET job 成为唯一事实源。
3. 本轮没有运行 live PostgreSQL/PostGIS/Timescale、Redis relay、跨 worker/restart SSE、真实 solver/worker 或 Docker/Compose 性能门禁；不能把这些记为 AC/G0-G6 PASS。
4. `mypy app/api/hydraulic_routes.py` 已执行但当前仓库还存在既有 `config.py/security.py` 错误，API adapter 的动态 JSON 边界也产生类型错误；本轮未通过修改非 owned 文件掩盖这些问题。Flake8/isort/Black/pytest focused checks 已通过，结果见上表。
5. 任务文档的 R0/迁移/依赖 handoff 状态仍由其他 owner 管理；本轮没有修改 migration、Settings、security 或 artifact 来绕过该门禁。

未提交、未推送。
