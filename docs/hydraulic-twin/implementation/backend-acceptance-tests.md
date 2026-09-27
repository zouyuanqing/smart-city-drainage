# 后端验收测试补齐实施摘要

## 范围与依据

本轮只新增 `backend/tests/**` 下的验收测试与 fixture，没有修改生产代码、依赖、迁移或提交/推送 Git。需求基线按任务要求阅读：

- `docs/hydraulic-twin/SOURCE_PRD.md:16-24`（FR-1～FR-7、非功能约束）与 `:32`（AC-1～AC-8）。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:164-179`（durable job/retry/fencing）、`:216-229`（30 分钟半开抑制窗）、`:303-316`（恶意导入错误码）、`:453-480`（率定改善门槛）、`:581-592`（AC 矩阵）。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1466-1516`（最终 REST API）、`:1574-1635`（仿真/结果/帧）、`:1677-1699`（率定、A/B、forecast）、`:2476-2497`（固定 fixture/gate 入口）、`:2539-2554`（AC 证据规则）。

## 实际新增内容

### 1. AC/FR 映射与集成测试

- `backend/tests/acceptance/test_acceptance_matrix.py`
  - `:207-233`：加载并校验 `matrix.json` 的 FR-1～FR-7、AC-1～AC-8 映射、fixture 存在性与 SHA-256。
  - `:236-304`：AC-1 合法 INP/GeoJSON 导入、job/import detail、幂等重放。
  - `:307-371`：AC-3 50 年一遇、24 h、5 min 步长的 289 点/289 帧提交与已发布帧回放。
  - `:374-403`：FR-3 任务崩溃恢复、10 秒 retry backoff、attempt 2、fencing 拒绝旧 worker。
  - `:474-523`：AC-4 forecast 30 分钟半开冷却窗，窗内抑制、边界新建 episode。
  - `:526-630`：AC-5 率定 fixture 的 RMSE/NSE/峰值改善门槛，以及 synthetic 不能冒充 `external_verified`。
  - `:633-670`：预测—实测 endpoint 的严格 xfail，明确暴露当前 API 没有真实 verified pairs，而不是伪造 PASS。
  - `:673-717`：AC-6 相同 revision/frame grid 的 A/B delta、relative delta、better direction，以及不兼容 grid 的 409。
  - 末尾 legacy API characterization 保留 `/api/health`、`/api/alerts`、`/api/sensors/latest`、OpenAPI 路径；legacy SSE 实际请求继续由已有 `tests/security/test_auth_hardening.py::test_production_legacy_sse_remains_public` 覆盖，避免测试客户端绑定第三方 SSE 进程退出事件造成顺序性假失败。

### 2. 负向安全测试

- `backend/tests/acceptance/test_negative_security.py:30-49`：对冻结契约中绝对路径/UNC 名称的已知实现差异使用 `strict xfail`。
- `:90-120`：每个恶意 fixture 都断言 HTTP status/code，并断言 job、import、idempotency 计数不变，随后 `/api/networks` 仍可用。
- `:123-177`：缺 token=401、viewer/unknown role=403、缺 `Idempotency-Key`=422、合法导入=202、同 key 不同 payload=409 `IDEMPOTENCY_KEY_REUSED`。
- `:180-189`：forecast 缺认证必须在资源访问前返回 401。

### 3. 集成 fixture registry

新增目录 `backend/tests/fixtures/hydraulic/acceptance/v1/`：

- `matrix.json`：FR/AC 映射、测试 node id、当前状态与 fixture SHA-256；状态明确区分 `verified`、`partial`、`gap`、`not_run`（`:1-80`）。
- `network_valid.geojson`：合法 FeatureCollection（2 个 node）。
- `malicious_imports.json`：14 个恶意/边界案例，覆盖 traversal、Windows drive、UNC、ADS、扩展名白名单、zip、脚本内容、unsafe content reference、空文件、非法 UTF-8、NaN、深层 JSON、20 MiB+1（`:1-99`）。
- `rainfall_50y_24h.json`：50 年、86400 s、300 s、289 点/289 帧的可复现配置。
- `calibration_improvement.json`：3 个事件 × 12 点、baseline/candidate bias、provenance 与 AC-5 阈值。
- `forecast_suppression.json`：固定 UUID lineage、terminal 时间、冷却 30 分钟与边界时间。
- `legacy_api.json`：旧 API/SSE/OpenAPI 路径清单。

## 执行结果（本轮实际运行）

### 目标验收套件

命令：

```text
cd smart-city-drainage/backend && .venv/Scripts/python.exe -m pytest -q tests/acceptance
```

结果：`26 passed, 3 xfailed, 1238 warnings in 4.96s`（exit 0）。3 个 xfailed 是严格缺口标记，不是伪造通过。

### 全后端回归

命令：

```text
cd smart-city-drainage/backend && .venv/Scripts/python.exe -m pytest -q
```

结果：`509 passed, 4 skipped, 3 xfailed, 23829 warnings in 30.10s`（exit 0）。本轮没有新增失败；全量套件包含新增验收测试。

### 质量检查

命令：

```text
cd smart-city-drainage/backend && .venv/Scripts/python.exe -m black --check tests/acceptance && .venv/Scripts/python.exe -m flake8 tests/acceptance && .venv/Scripts/python.exe -m isort --check-only tests/acceptance
```

结果：exit 0；Black 输出 `3 files would be left unchanged`，Flake8/isort 无输出。

### Fixture JSON 检查

命令：

```text
python - <<'PY'
import json
from pathlib import Path
root=Path(r'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/tests/fixtures/hydraulic/acceptance/v1')
for p in sorted(root.glob('*.json')):
    json.loads(p.read_text(encoding='utf-8'))
    print(f'valid-json {p.name}')
PY
```

结果：6 个 JSON fixture 全部输出 `valid-json ...`，exit 0。`matrix.json` 中的 SHA-256 还由 `test_acceptance_matrix.py:221-230` 在测试中重新计算并核对。

## AC 状态与真实风险

| AC | 本轮状态 | 证据/风险 |
|---|---|---|
| AC-1 | NOT RUN | 本行原写「verified（API adapter 层）」，与 `matrix.json` 的 NOT RUN 矛盾。API 侧 202/计数/幂等属 in-memory adapter 特征，不是 AC-1 证据；durable 提交与 live DB 未证明。 |
| AC-2 | BLOCKED | 本行原写「verified + 2 strict xfail」，但 acceptance 目录已无任何 xfail，且 `windows-drive`/`unc-path` 现返回 422 `UNSAFE_PATH` 并通过。行为层已达标，阻断来自 G2/G6 必需工件缺失。 |
| AC-3 | partial | 50 年/24 h/289 帧提交与 adapter 发布帧回放通过；没有运行真实 4 核 solver、worker wall-time、frame 持久化和前端时间轴，因此不能宣称完整 AC-3。契约的 G3/G5 要求见 `IMPLEMENTATION_CONTRACT.md:2491-2495`、`2511-2525`。 |
| AC-4 | verified（服务层） | 30 分钟抑制、半开边界、episode 区分通过；live nowcast/Redis/outbox/SSE 周期证据仍未运行。 |
| AC-5 | gap | 率定算法 fixture 达到 NSE/RMSE/峰值门槛，且 synthetic 被拒绝；但 `GET /api/simulations/{id}/observed-comparison` 当前固定返回空 pairs（生产代码 `backend/app/api/hydraulic_routes.py:3378-3394`），严格 xfail `test_acceptance_matrix.py:633-670` 保持缺口可见。AC-5 的真实 external observation/绑定要求见 `PRD_SUPPLEMENT.md:453-480`、`:589`。 |
| AC-6 | verified（已发布结果 API 层） | delta/relative delta/better direction 与不兼容 grid 409 通过；未连接真实持久化结果仓库。 |
| AC-7 | verified | 全后端回归 exit 0；旧 API characterization 与已有 legacy SSE regression 均通过。 |
| AC-8 | not_run | 本轮没有 Docker、PostGIS/portable、Timescale、Redis、worker、fresh-volume、backup/restore 运行；矩阵明确记录 `not_run`。契约要求见 `IMPLEMENTATION_CONTRACT.md:2476-2497`、`:2539-2554`。 |

## 工作树与后续

`git status --short --untracked-files=all -- backend/tests/acceptance backend/tests/fixtures/hydraulic/acceptance` 仅显示本轮新增的 10 个测试/fixture 文件；生产目录中已有的并行工作树改动未被本轮触碰。没有 commit、push 或 merge。

下一位集成 owner 应优先补齐：真实 observation ingest/binding 与 `observed-comparison` 数据源、API 层绝对/UNC 路径在扩展名分类前返回 `UNSAFE_PATH=422`、以及按契约运行 live worker/Compose AC-3/AC-5/AC-8 门禁。不得通过降低上述 strict xfail 或修改生产代码迎合测试来消除风险。
