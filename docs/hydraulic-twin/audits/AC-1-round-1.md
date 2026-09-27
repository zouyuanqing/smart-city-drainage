# AC-1 独立验收（round 1）

> 验收人：独立验收员（只读，未修改任何被验收代码）
> 验收时间：2026-09-28
> 仓库：`C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage`
> 基线 commit：`ca99e0485fb12fbecbd565e79d81f745f8d4a748`（`git log --oneline -1`；工作树 dirty，`git status --porcelain --untracked-files=all | wc -l` = 722）
> 本文件只登记本次实际读到/实际跑到的证据。凡未运行的检查一律记 NOT RUN，不折算为通过。

---

## 0. 结论摘要

| 判据（拆自 `PRD_SUPPLEMENT.md:586`） | 状态 | 一句话 |
|---|---|---|
| C1 合法 INP 导入计数正确 | **partial** | 库层计数正确且稳定；API 适配层与 durable worker 对同一次导入给出**两套不同**的计数字典，且只有前者被测试断言 |
| C2 合法 GeoJSON 导入计数正确 | **partial** | 仅 `{"features": N}` 一个总数（`hydraulic_routes.py:1194`）；无按 entity_type 拆分、无 stable_key 落库校验 |
| C3 产出分级校验/拓扑报告 | **blocked（代码可修）** | `validate_topology` 真实可用，但 API 的 `validation_runs[*].report` 在**任何模式下**恒为 `None`，无处写入 |
| C4 round-trip（INP/WKT/GeoJSON、stable key） | **partial** | parser→exporter→reparser 语义稳定（本次实测通过）；但"导入→网络→导出"整链在 API 层断裂 |
| C5 error 不半提交 | **未验证 / 代码可修** | 提交路径根本不存在（见 F1），因此"不半提交"无对象可测；导出侧已实测 fail-closed |
| C6 两个 spatial profile 同轮证据 | **not run（基础设施缺失）** | 无双 profile import contract 测试；g1/g2/g3/g4 在 CI 与本地均不产生产物 |
| C7 机器证据（run_id/commit/artifact sha256/PASS 判据） | **not run（fail-closed 正确）** | 冻结入口已存在，但 `--gate g2` 实测 exit 2 / BLOCKED；`--gate acceptance --ac ac1` 实测 exit 2 / BLOCKED |

**AC-1 契约判定：BLOCKED（不是 NOT RUN）。** 与 `matrix.json` 自述的 `NOT RUN` 相比，本轮更严格一档：入口已存在且 fail-closed 地返回了非零退出码，因此按 `PRD_SUPPLEMENT.md:11.3`「脚本存在但任一子命令非零时为 FAIL」、按 `verify_hydraulic_stack.py` 自身的语义为 **BLOCKED/exit 2**。

**其中 4 项（F1/F2/F3/F4）可由代码修复**，且不依赖任何真实外部观测数据；F5/F6/F7 需要新增冻结 fixture 与 CI job，F7/F8/F9 需要新增测试代码。

---

## 1. 判据来源（逐条引用）

| # | 判据原文 | 位置 |
|---|---|---|
| N1 | 「AC-1 合法 inp/GeoJSON 导入计数正确且有校验报告。」 | `docs/hydraulic-twin/SOURCE_PRD.md:33` |
| N2 | AC-1 证据 = 「两 spatial profile 的 import contract、validation golden JSON、round-trip」；通过判据 = 「计数、stable key、WKT/GeoJSON、issue code、topology 完全一致；error 不半提交」；当前状态 = 「**未实现/本轮未运行**」 | `docs/hydraulic-twin/PRD_SUPPLEMENT.md:586` |
| N3 | 「`PASS` 只允许在目标命令本轮 exit 0 且 artifact 存在、hash 校验通过时填写。静态阅读、`alembic heads`、Compose config、build 或 mock 测试不能替代对应 AC。」 | `docs/hydraulic-twin/PRD_SUPPLEMENT.md` §11.1 |
| N4 | G2 门禁内容：「合法 golden、恶意矩阵、durable network job、RBAC、路径、SSRF、JWT 负向测试，以及 …idempotency…fixture 全绿」 | `docs/hydraulic-twin/PRD_SUPPLEMENT.md:11.3` 第 3 条 |
| N5 | 「脚本/fixture 未实现时固定为 **NOT RUN**，脚本存在但任一子命令非零时为 **FAIL**。」 | `docs/hydraulic-twin/PRD_SUPPLEMENT.md:11.3` |
| N6 | 「AC-1～AC-8 的判据和通用 artifact 字段见 `PRD_SUPPLEMENT.md:11`。每项状态只能为 `PASS/FAIL/NOT RUN/BLOCKED`，其中 `PASS` 必须附本轮目标命令 exit 0 和 artifact SHA-256。」 | `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2541` |
| N7 | 旧 supporting contract（非主基线）：AC-1 证据 = 「两个 profile 的 `test_import_contract` + API golden JSON」；artifact = `artifacts/hydraulic/<run>/ac1/{postgis,portable}.json` + pytest JUnit | `docs/hydraulic-twin/contracts/operations.md:369` |
| N8 | 旧 supporting contract：「AC-1～AC-8 每行都有机器证据和明确的 `PASS/FAIL/NOT RUN`」才允许标记完成 | `docs/hydraulic-twin/contracts/operations.md:474` |

---

## 2. 本次实际运行的检查（全部命令原文 + 输出）

> 环境：`backend/.venv/Scripts/python.exe --version` → `Python 3.11.15`（满足 `SOURCE_PRD.md:15` 的 3.11 约束；非系统 `python` 的 3.14.4）。

### 2.1 验收矩阵自述状态（读，非运行）

```text
$ python -c "读 backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json 并打印每个 case"
AC-1 | NOT RUN | The current HTTP diagnostics use HydraulicApiStore(durable_backend=False) and prove only q
AC-2 | BLOCKED | ...
```

AC-1 完整条目（`backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json`）：

```json
{
  "id": "AC-1",
  "title": "valid INP and GeoJSON import is durable, idempotent and round-trips",
  "status": "NOT RUN",
  "reason": "The current HTTP diagnostics use HydraulicApiStore(durable_backend=False) and prove only queued, uncommitted adapter state. Same-run G1/G2/G6 artifacts for both profiles are absent.",
  "tests": [
    "tests/acceptance/test_acceptance_matrix.py::test_ac1_in_memory_adapter_valid_inp_import_is_accepted_and_replayed",
    "tests/acceptance/test_acceptance_matrix.py::test_ac1_in_memory_adapter_valid_geojson_import_is_accepted_and_replayed",
    "tests/acceptance/test_acceptance_matrix.py::test_ac1_production_api_fails_closed_without_durable_repository"
  ],
  "pass_nodeids": [],
  "fixtures": ["acceptance/v1/network_valid.geojson", "imports/swmm_minimal_wgs84.inp"],
  "required_gates": ["g1", "g2", "g6"],
  "required_artifact_suffixes": ["g1/postgis/result.json", "g1/portable/result.json", "g2/result.json", "g6/postgis/result.json", "g6/portable/result.json"]
}
```

**判定：文档自述与实现现状一致，无「勾选冒充通过」问题。** `pass_nodeids` 为空、`status` 不是 PASS。

### 2.2 AC-1 三个 HTTP 诊断测试

```text
$ cd backend && .venv/Scripts/python.exe -m pytest \
    "tests/acceptance/test_acceptance_matrix.py::test_ac1_in_memory_adapter_valid_inp_import_is_accepted_and_replayed" \
    "tests/acceptance/test_acceptance_matrix.py::test_ac1_in_memory_adapter_valid_geojson_import_is_accepted_and_replayed" \
    "tests/acceptance/test_acceptance_matrix.py::test_ac1_production_api_fails_closed_without_durable_repository" -v
tests/acceptance/test_acceptance_matrix.py::test_ac1_in_memory_adapter_valid_inp_import_is_accepted_and_replayed PASSED
tests/acceptance/test_acceptance_matrix.py::test_ac1_in_memory_adapter_valid_geojson_import_is_accepted_and_replayed PASSED
tests/acceptance/test_acceptance_matrix.py::test_ac1_production_api_fails_closed_without_durable_repository PASSED
3 passed, 1 warning in 0.53s
```

**但这三个测试的断言本身就是"未提交"证明，不是"导入正确"证明**（`backend/tests/acceptance/test_acceptance_matrix.py:264-303`）：

```python
assert body["status"] == "queued"
assert body["result"] is None  # durable job has not been finalized yet
...
assert detail.json()["committed"] is False
assert detail.json()["counts"]["conduits"] == 1
assert len(hydraulic_store.networks) == 0   # ← 明确断言没有网络被创建
```

`assert len(hydraulic_store.networks) == 0` 出现在 **PASSED** 的测试里：AC-1 判据「导入计数正确且有校验报告」在 API 层没有被证明，只证明了「排队成功且什么都没落地」。

### 2.3 整个 acceptance 套件

```text
$ cd backend && .venv/Scripts/python.exe -m pytest tests/acceptance -q
61 passed, 1 warning in 4.69s
```

（注：`docs/hydraulic-twin/VALIDATION_EVIDENCE.md:209` 记的是 `7 failed, 57 passed` 的 `VERIFIED / FAIL` 行。该行相对**当前工作树**已过期；本轮实测 61 passed。台账的 FAIL 记录本身我不改写，此处只登记差异。）

### 2.4 库层 round-trip 实测（本轮新跑）

```text
$ cd backend && .venv/Scripts/python.exe -c "
import pathlib, json
from app.services.hydraulic.importers.swmm import parse_swmm
from app.services.hydraulic.exporters.swmm import export_inp
p = pathlib.Path('tests/fixtures/hydraulic/imports/swmm_minimal_wgs84.inp')
raw = p.read_bytes()
n1 = parse_swmm(raw, strict=True, filename=p.name)
print('parse1 counts:', json.dumps(n1.counts, sort_keys=True))
out = export_inp(n1)
n2 = parse_swmm(out.encode('utf-8'), strict=True, filename='roundtrip.inp')
print('parse2 counts:', json.dumps(n2.counts, sort_keys=True))
print('counts equal:', n1.counts == n2.counts)
print('canonical equal:', n1.canonical_json() == n2.canonical_json())"
parse1 counts: {"conduits": 1, "coordinates": 2, "junctions": 1, "nodes": 2, "outfalls": 1, "slopes": 1, "subcatchments": 1}
parse2 counts: {"conduits": 1, "coordinates": 2, "junctions": 1, "nodes": 2, "outfalls": 1, "slopes": 1, "subcatchments": 1}
counts equal: True
canonical equal: True
```

**这是本次唯一一项 AC-1 子判据被完整验证通过的检查**：parser → exporter → reparser 的 INP round-trip 语义稳定。已有回归测试覆盖同一性质（`backend/tests/hydraulic/test_swmm_io.py:175-189` 断言 `reparsed.content_sha256 == model.content_sha256`）。

### 2.5 计数一致性实测（本轮新跑，发现 F4）

```text
$ cd backend && .venv/Scripts/python.exe -c "... 分别打印 API preflight 计数、worker import 计数、worker validate 报告计数 ..."
API preflight counts : {"conduits": 1, "coordinates": 2, "junctions": 1, "nodes": 2, "outfalls": 1, "slopes": 1, "subcatchments": 1}
worker import counts : {"conduits": 1, "junctions": 1, "outfalls": 1}
worker validate counts: {"components": 1, "cycles": 0, "errors": 0, "nodes": 2, "pipes": 1, "warnings": 0}
api-vs-import count keys identical: False
report status: valid | simulation_ready: True | errors: 0 | warnings: 0
report issue codes: []
components: (('J-1', 'OUT'),) | cycles: 0
```

拓扑报告**本身是真的**（有 algorithm_version、counts、components、cycles、content_sha256、issue code 体系），但导入计数在两处实现里形状不同。

### 2.6 冻结门禁 G2（AC-1 唯一要求的 gate）

```text
$ backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py --gate g2 \
    --evidence-dir "artifacts/hydraulic/_ac1-audit-probe/g2"
...
"status": "BLOCKED", "exit_code": 2,
"steps": [{"name": "g2.fixtures", "status": "BLOCKED", "exit_code": 2,
  "missing_fixtures": [
    "backend/tests/fixtures/hydraulic/network_jobs/v1/manifest.json",
    "backend/tests/fixtures/hydraulic/contracts/idempotency-ttl-v1.json",
    "backend/tests/fixtures/hydraulic/security/jwt-rbac-ratelimit-v1.json"
  ],
  "stderr_summary": ["missing frozen fixtures: ..."]}]
HYDRAULIC_STACK_STATUS=BLOCKED
HYDRAULIC_STACK_EXIT=2
HYDRAULIC_STACK_ARTIFACT_SHA256=1370bce93d6264075342883f0894befec3e23b9470903cfb6dbcbdfc334f3445
```

**G2 的三个冻结 fixture 全部不存在**（与 `ls backend/tests/fixtures/hydraulic/` 实际只有 `acceptance/ compose/ contracts/ imports/` 一致）。G2 连 fixture 步骤都过不去，因此**不可能**产出 AC-1 证据。

### 2.7 冻结验收汇总门禁

```text
$ backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py --gate acceptance --ac ac1 \
    --run-id "20260927T191527Z-ca99e048" --evidence-dir "artifacts/hydraulic/_ac1-audit-probe/acceptance"
overall status: BLOCKED | exit_code: 2
ac1 entry: {"ac": "ac1", "status": "BLOCKED", "required_gates": ["g2"],
  "missing_or_invalid_artifacts": ["g2", "non-PASS artifact: .../_ac1-audit-probe/g2/result.json"]}
HYDRAULIC_STACK_STATUS=BLOCKED
HYDRAULIC_STACK_EXIT=2
```

**汇总门禁是 fail-closed 的**：不会因为 g2 artifact「存在」就判 PASS。行为正确。

> 副作用声明：2.6/2.7 在 `artifacts/hydraulic/_ac1-audit-probe/` 下写入了 4 个文件，我已 `rm -rf` 清理并确认工作树无残留（`git status --porcelain --untracked-files=all | grep ac1-audit` 无输出）。未修改任何被验收代码。

### 2.8 未运行的检查（明确列出）

| 检查 | 状态 | 原因 |
|---|---|---|
| `--gate g1` / `g3` / `g4` | **NOT RUN** | 不在 AC-1 的 `required_gates`（`verify_hydraulic_stack.py:1705` 中 `"ac1": ("g2",)`）；AC-1 不依赖 |
| Docker Compose 双 profile 全链路导入 | **NOT RUN** | 属 G6/AC-8 范围，本轮不跑全量门禁 |
| 对真实 PostGIS / Timescale 库跑 import worker | **NOT RUN** | 无 fixture、无 G2 入口；不作为本轮诊断 |
| 全量 backend pytest（797 项） | **NOT RUN** | 属 AC-7 范围；本轮只跑 AC-1 相关子集 |
| `test_geo_spatial.py` / `test_topology_seed.py` / `test_swmm_io.py` | **已跑，64 passed** | 见下 |

```text
$ cd backend && .venv/Scripts/python.exe -m pytest tests/hydraulic/test_swmm_io.py \
    tests/hydraulic/test_geo_spatial.py tests/hydraulic/test_topology_seed.py -q
64 passed, 1 warning in 0.10s
```

---

## 3. 发现（按严重度）

### F1 —【BLOCKER / 代码可修】导入从不落地为网络：`_import_entities` 与 `_network_from_entities` 是死代码

- `backend/app/api/hydraulic_routes.py:1420-1467` 的 `import_network()` 只做三件事：`_preflight_import`、建 job、写 `hydraulic_store.imports[import_id]`，然后返回 202。注释自称「The durable worker owns materialization, revision publication」。
- 把 INP/GeoJSON 变成 `nodes/pipes/catchments` 的两个函数确实存在：
  - `_import_entities()` — `backend/app/api/hydraulic_routes.py:1244-1322`（INP 坐标 join、缺失坐标 → 422 `MISSING_COORDINATES`、悬空端点 → 422 `DANGLING_NODE`、GeoJSON 分支走 `_entity_from_import_feature`）
  - `_network_from_entities()` — `backend/app/api/hydraulic_routes.py:1325-…`
- 全仓调用点检查：

```text
$ cd backend && grep -rn "_import_entities\|_network_from_entities" --include=*.py app tests
--- _import_entities ---
--- _network_from_entities ---
（两个函数只有定义行，零调用点）
```

**影响**：FR-1/AC-1 要求的「导入 → 管网数据层 → 导出 GeoJSON 与可回跑 inp」整链在 API 层不存在。`GET /api/networks/{id}/exports/{fmt}`（`hydraulic_routes.py:2684-2736`）只能导出**由 CRUD 创建**的 revision，永远导不出「导入得来的」网络。AC-1 判据里的 round-trip 因此无法通过 API 达成。
**可由代码修复**：是。接线 + 决定物化时机（同步 vs 交 worker）即可。

### F2 —【BLOCKER / 代码可修】durable worker 读不到 API 写的任务：staging 无生产者，且指纹口径不一致

- worker 侧契约（`backend/app/workers/network_job_worker.py:27-33`、`575-618`）：payload 必须落盘在 `${HYDRAULIC_IMPORT_ROOT}/<job_id>.inp`，且 `sha256(payload)` 必须等于 `claim.request_fingerprint`，否则非重试失败 `STAGED_INPUT_HASH_MISMATCH`。
- API 侧实际算的（`backend/app/api/hydraulic_routes.py:1429-1432`）：

```python
fingerprint = _fingerprint(
    {"filename": filename, "sha256": hashlib.sha256(data).hexdigest()}
)
```

  `_fingerprint` 是 `hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()`（`hydraulic_routes.py:217-218`）——这是**信封 JSON 的哈希**，不是 payload 字节的哈希。两者数学上不可能相等。
- 生产者缺失：

```text
$ grep -rn "HYDRAULIC_IMPORT_ROOT" --include=*.py backend/app
backend/app/core/config.py:271:    HYDRAULIC_IMPORT_ROOT: str = "./backend/hydraulic/import"
backend/app/workers/network_job_worker.py:909:    import_root=Path(settings.HYDRAULIC_IMPORT_ROOT),
backend/app/workers/network_job_worker.py:1000:    import_root=Path(settings.HYDRAULIC_IMPORT_ROOT)
```

  只有 worker 的**读**方与配置项；`app/` 内没有任何把上传字节写到该目录的代码。

**影响**：即使把 G2 fixture 补齐、把 job 塞进 `hydraulic.network_jobs`，worker 也会立刻以 `STAGED_INPUT_MISSING`（非重试）失败。这是 AC-1 durable 路径的硬阻断，且现有单测查不出来——`tests/test_durable_worker_runtime.py:510-597` 直接构造 `request_fingerprint="0"*64` 的假 claim 调纯函数 `compute_outcome()`，**完全绕开** staging 与指纹校验。
**可由代码修复**：是。三处需对齐：staging 写入点、`request_fingerprint` 口径（建议直接用 payload 的 sha256）、幂等指纹与载荷指纹解耦。

### F3 —【BLOCKER / 代码可修】校验报告在任何模式下都取不到：`validation_runs[*].report` 恒为 `None`

```text
$ cd backend && grep -n '"report": None' app/api/hydraulic_routes.py
2574:        "report": None,     # POST /networks/{id}/validate 写入
2582:        "report": None,     # 202 响应体
3700:        "report": None,
```

`grep -n '\["report"\]' app/api/hydraulic_routes.py` → **无输出**：没有任何代码把 `report` 从 `None` 改成真实报告。

同理 `hydraulic_store.imports[*]["committed"]` 只在 `hydraulic_routes.py:1465` 被写成 `False`，全仓无置 `True` 的代码；`hydraulic_store.jobs[*]["status"]` 也无 `completed` 迁移。

**影响**：AC-1 的「有校验报告」在 API 交付面完全不可达。`GET /api/validation-runs/{id}`（`hydraulic_routes.py:2591-2599`）永远返回 `report: null`。
**可由代码修复**：是。durable 侧已有完整实现（`network_job_worker.py:508-556` 把 `report.to_dict()` 写进 `hydraulic.validation_runs` + 逐条 issue 写 `network_validation_issues`），缺的是「API 读 durable 表」这一段。

### F4 —【HIGH / 代码可修】同一次导入有两套计数，AC-1「计数正确」在 API 层未被证明

见 §2.5 实测：

| 来源 | 计数键 | 代码位置 |
|---|---|---|
| API adapter（`/api/network-imports/{id}` 返回） | `conduits, coordinates, junctions, nodes, outfalls, slopes, subcatchments`（7 键，来自 `parsed.counts`） | `hydraulic_routes.py:1453`（`preflight.get("counts", {})`）、`hydraulic_routes.py:1122` |
| durable worker（写 `hydraulic.network_imports.counts`） | `conduits, junctions, outfalls`（3 键） | `network_job_worker.py:717-722` |

GeoJSON 更弱：`_preflight_import` 的 GeoJSON 分支只返回 `{"features": len(features)}`（`hydraulic_routes.py:1191-1194`），没有按 `entity_type` 拆分，没有 pipe 端点计数，也没有把 `stable_key` 集合落库留痕。

**影响**：AC-1 判据「计数…完全一致」在 API 与 durable 两个交付面之间不成立；现有 AC-1 测试只断言 adapter 侧的 `counts["conduits"] == 1`（`test_acceptance_matrix.py:302`，与下一行 `assert len(hydraulic_store.networks) == 0` 相邻），恰好绕开了不一致点。
**可由代码修复**：是。选定单一权威计数定义（建议以 `SwmmNetwork.counts` 为唯一源），worker 直接透传而非重算。

### F5 —【BLOCKER / 基础设施】G2 冻结 fixture 全部缺失，AC-1 无证据可产

见 §2.6。`GATE_FIXTURES["g2"]`（`scripts/verify_hydraulic_stack.py:99-103`）要求三个文件，实际 `backend/tests/fixtures/hydraulic/` 下只有 `acceptance/ compose/ contracts/ imports/` 四个目录。

另注：`gate_domain` 在 fixture 缺失时返回 BLOCKED（`verify_hydraulic_stack.py:430-449`），即使 fixture 齐全但 manifest 未定义 `gate_commands`（`GATE_COMMANDS_KEY`，`:116`）也会返回 BLOCKED 并写明「static fixture presence is not gate evidence」（`:746-766`）。**补 fixture 时必须同时在 manifest 里写真实可执行 argv**，否则仍然拿不到 AC-1。
**可由代码修复**：是（新增 fixture + manifest 命令），但需要先有 F1/F2/F3 的可执行实现，否则 G2 命令跑出来仍是 FAIL。

### F6 —【BLOCKER / 基础设施】CI 根本没有 G1～G4 job，AC-1 结构性永远 BLOCKED

```text
$ grep -n "^  [a-z0-9-]*:" .github/workflows/ci.yml
4:  push:
11:  contents: read
14:  group: ...
15:  cancel-in-progress: true
24:  dependency-lock:
56:  host-interpreter:
77:  g0-quality:
117:  frontend-quality:
149:  compose-config:
212:  frontend-e2e:
251:  g5-performance:
288:  acceptance-summary:
```

CI 只跑 G0（`:104-107`）、G6 portable/postgis（`:183-188`、`:197-202`）、G5（`:275-276`）、frontend e2e（`:236-238`）与 acceptance 汇总（`:326-330`）。而 `verify_hydraulic_stack.py:1705` 定义 `"ac1": ("g2",)`，`gate_acceptance` 在 `required` gate 无 artifact 时置 `BLOCKED`（`:1719`）。

**影响**：AC-1 的 PASS 在当前 CI 里**不可能**出现。这与 `docs/hydraulic-twin/reviews/ci-quality.md:206` 的既有结论一致（本轮独立复核确认）。
**可由代码修复**：是（新增 g1/g2 job + 上传 artifact），但依赖 F5。

### F7 —【HIGH / 需新增测试资产】validation golden JSON 不存在

```text
$ find backend/tests/fixtures -iname "*golden*" -o -iname "*validation*"
（无输出）
```

AC-1 判据明确要求「validation golden JSON」可逐字节比对（`PRD_SUPPLEMENT.md:586`），`contracts/operations.md:369` 进一步要求「API golden JSON」。当前 `tests/fixtures/hydraulic/acceptance/v1/` 下只有 `network_valid.geojson` 与矩阵类 JSON。
**可由代码修复**：是。`validate_topology` 已提供 `canonical_json()` 与 `content_sha256`（`topology.py:152-159`），生成 golden 的成本很低——先在实现正确后再冻结，否则 golden 会把错误实现固化下来。**建议顺序：先修 F1～F4，再冻结 golden。**

### F8 —【MEDIUM / 需新增测试】双 profile import contract 测试不存在

```text
$ cd backend && grep -rn "test_import_contract\|import_contract" --include=*.py .
（无输出）
$ grep -rln "postgis" tests --include=*.py
tests/acceptance/test_acceptance_matrix.py
tests/acceptance/test_matrix_gate.py
tests/hydraulic/test_geo_spatial.py
tests/integration/test_database_schema.py
tests/test_hydraulic_schema.py
tests/test_hydraulic_schema_readyz_tables.py
```

`N7` 点名的 `test_import_contract` 不存在。现有与 profile 相关的测试全是 **schema/几何层**（`test_hydraulic_schema.py`、`test_database_schema.py`、`test_geo_spatial.py`），**没有一个把导入契约在 postgis 与 portable 下各跑一遍并比对结果**。`HYDRAULIC_SPATIAL_BACKEND` 在 `app/models/hydraulic_models.py:86` 于**导入期**绑定为模块级常量（`_ACTIVE_SPATIAL_BACKEND`），同一进程内无法切 profile，因此这个测试需要子进程或两次独立运行。
**可由代码修复**：是。

### F9 —【MEDIUM / 需新增测试】durable network-job 控制面零覆盖

`tests/test_durable_worker_runtime.py` 中 `run_once` 的调用点（`:378/391/412/424`）属于 `OutboxRelayWorker`，**不是** `NetworkJobWorker`。`SessionNetworkJobRepository` 仅在 `:602` 与 `:616` 被构造，用于验证 backoff ladder 与构造参数。

未覆盖：`claim_next` 的 `pg_advisory_xact_lock` + `FOR UPDATE SKIP LOCKED` 语义、fencing token 递增与 lease 过期、`complete` 的 rowcount 校验、`_persist_import` / `_persist_validation` 的实际 INSERT。
**影响**：F2/F3 这类控制面缺陷正是这类测试该抓的；当前 59 个测试全绿（`pytest tests/test_durable_worker_runtime.py -q` → `59 passed`）并不能说明 durable 路径可运行。
**可由代码修复**：是。

### F10 —【LOW / 规格漂移】AC-1 artifact 路径在两份文档间不一致

`contracts/operations.md:369` 要求 `artifacts/hydraulic/<run>/ac1/{postgis,portable}.json`；已实现的 `gate_acceptance` 只写单一 `acceptance/result.json`（`verify_hydraulic_stack.py:1762-1765`），且 AC 级判据只由 `required_gates` 驱动，不区分 profile（`verify_hydraulic_stack.py:1719`）。

`contracts/*.md` 已被 `SOURCE_PRD.md:9` 声明为「已被取代的 supporting contracts」，因此这是**漂移不是阻断**，但意味着 N8「每行都有机器证据」的落地形态尚未确定。**建议在补 F5/F6 时一并冻结**：AC-1 需要 per-profile 产物，而当前 `ac_requirements["ac1"] = ("g2",)` 只有一个非 profile 化 gate。

---

## 4. 逐条判据结论

| 判据 | 状态 | 依据 | 代码可修 |
|---|---|---|---|
| C1 INP 导入计数正确 | **partial** | 库层 counts 正确且 round-trip 稳定（§2.4/§2.5 已验证）；API 侧与 durable 侧计数字典不一致（F4），且 API 侧唯一断言是 `conduits == 1` | 是 |
| C2 GeoJSON 导入计数正确 | **partial** | 仅 `features` 总数（`hydraulic_routes.py:1194`）；测试断言 `counts["features"] == 2`（`test_acceptance_matrix.py:327`），与 fixture 实际 feature 数一致，但无分类型/端点/稳定键校验 | 是 |
| C3 分级校验/拓扑报告 | **blocked** | 报告生成可用（§2.5 实测 `status=valid / components=(('J-1','OUT'),) / cycles=0`），但 API 侧 `report` 恒 `None`（F3） | 是 |
| C4 round-trip | **partial** | 库级 parse→export→parse 语义等价**已实测通过**（§2.4）；API 级「导入→导出」因 F1 断裂 | 是 |
| C5 error 不半提交 | **unverified** | 提交路径不存在（F1），无对象可测；导出侧已有 fail-closed 回归（`test_swmm_io.py:192-198` 断言 `SwmmExportError(code="MISSING_COORDINATES")` 而非半份 inp） | 是 |
| C6 双 profile 同轮证据 | **not run** | 无双 profile 测试（F8）；g1/g2/g3/g4 不产生产物（F5/F6） | 是（需新测试 + CI） |
| C7 机器证据 | **not run（BLOCKED）** | `--gate g2` exit 2（§2.6）、`--gate acceptance --ac ac1` exit 2（§2.7）；`artifacts/hydraulic/` 下无 `g1`/`g2`/`acceptance` 目录 | 是（需新 fixture） |

---

## 5. 台账与文档一致性

| 台账声明 | 位置 | 本轮复核 |
|---|---|---|
| 「AC-1 \| NOT RUN \| adapter 只 queued/uncommitted；双 profile durable G1/G2/G6 缺失」 | `docs/hydraulic-twin/CONTRACT_INDEX.md:63` | **一致**。G1/G2/G6 证据确实全缺；但「NOT RUN」应升级为 **BLOCKED**（入口已存在并 fail-closed 返回 exit 2） |
| 「AC-1 \| verified（API adapter 层）」（`implementation/backend-acceptance-tests.md:98`） | 同上 | **已过期/不成立**。`matrix.json` 现为 `NOT RUN` + `pass_nodeids: []`；`implementation/` 下该表述与 fixture 矛盾 |
| 「AC-1 测试 `test_acceptance_matrix.py:242-273` 明确只断言 `status=queued`…」（`reviews/backend-acceptance-tests.md:29`） | 同上 | **一致**。行号已漂移到 `:264-303`（本轮实测），结论不变 |
| 「`scripts/verify_hydraulic_stack.py` …均尚不存在；所有 gate 当前只能做路径审计，不能执行通过」（`PRD_SUPPLEMENT.md:632`）与「当前所有 gate/harness/fixture 入口均不存在」（`IMPLEMENTATION_CONTRACT.md:2499`） | — | **已过期**。`scripts/verify_hydraulic_stack.py` 现存在（67,118 字节，Sep 28 02:53），本轮实际调用成功并 fail-closed 返回 exit 2 |
| 「backend-acceptance-tests pytest → 7 failed, 57 passed」（`VALIDATION_EVIDENCE.md:209`） | — | **已过期**。本轮实测 `61 passed` |
| 「AC-1～AC-8 在 solver runtime 阶段全为 NOT RUN」（`VALIDATION_EVIDENCE.md:241`） | — | 一致；AC-1 至今无任何升级记录 |

---

## 6. 建议的修复顺序（供实现者参考，本轮未执行任何修改）

1. **F2**（口径 + staging）→ **F1**（接线物化）→ **F3**（API 读 durable 报告）。这三项是 AC-1 的最小可运行闭环，缺一不可，且互相耦合，**必须同一轮改、同一轮测**。
2. **F4** 在 1 之后立刻做：定一份权威 counts 定义，避免两处实现再次漂移。
3. **F9** 补 durable 控制面集成测试（需要真库；G1 是它的前置）。
4. **F7** 最后冻结 golden —— 在 1～3 全绿之前冻结会把错误实现写进基线。
5. **F5 + F6 + F8 + F10**：新增 G2 fixture（manifest 必须带 `gate_commands`）+ CI g1/g2 job + per-profile AC-1 artifact 形态。
6. 上述完成后，按 `PRD_SUPPLEMENT.md:11.3` 用同一 `RUN_ID`、同一 commit 重跑 g1(postgis/portable)、g2、g6(postgis/portable) 与 `--gate acceptance --ac ac1`，才可能写下 `PASS`。

---

## 7. 复现命令汇总（本轮实际执行）

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage"

git log --oneline -1
git status --porcelain --untracked-files=all | wc -l

cd backend
.venv/Scripts/python.exe --version
.venv/Scripts/python.exe -m pytest tests/acceptance -q
.venv/Scripts/python.exe -m pytest tests/hydraulic/test_swmm_io.py tests/hydraulic/test_geo_spatial.py tests/hydraulic/test_topology_seed.py -q
.venv/Scripts/python.exe -m pytest tests/test_durable_worker_runtime.py -q
.venv/Scripts/python.exe -m pytest "tests/acceptance/test_acceptance_matrix.py::test_ac1_in_memory_adapter_valid_inp_import_is_accepted_and_replayed" \
    "tests/acceptance/test_acceptance_matrix.py::test_ac1_in_memory_adapter_valid_geojson_import_is_accepted_and_replayed" \
    "tests/acceptance/test_acceptance_matrix.py::test_ac1_production_api_fails_closed_without_durable_repository" -v
# 另有 3 段内联 python -c 诊断（round-trip / 计数一致性 / 报告内容），输出见 §2.4、§2.5

cd ..
backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py --gate g2 --evidence-dir "artifacts/hydraulic/_ac1-audit-probe/g2"
backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py --gate acceptance --ac ac1 --run-id "20260927T191527Z-ca99e048" --evidence-dir "artifacts/hydraulic/_ac1-audit-probe/acceptance"
rm -rf artifacts/hydraulic/_ac1-audit-probe
```
