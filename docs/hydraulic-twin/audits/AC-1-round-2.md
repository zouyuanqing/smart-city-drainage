# AC-1 独立验收（round 2）

> 验收人：独立验收员（只读；本轮未修改任何被验收代码，未修复任何缺陷）
> 验收时间：2026-09-28
> 仓库：`C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage`
> 基线 commit：`ca99e0485fb12fbecbd565e79d81f745f8d4a748`（`git log --oneline -3`；工作树 dirty，`git status --porcelain --untracked-files=all | wc -l` = 806）
> 环境：`backend/.venv/Scripts/python.exe --version` → `Python 3.11.15`（满足 `SOURCE_PRD.md:15`）
> 前序：`audits/AC-1-round-1.md`（同 commit、同工作树）。本轮不继承其结论，全部重新取证。
> 本文件只登记本轮实际读到/实际跑到的证据。凡未运行的检查一律记 NOT RUN。

---

## 0. 结论摘要

| 判据（`PRD_SUPPLEMENT.md:586`） | round-1 | **round-2** | 变化 |
|---|---|---|---|
| C1 合法 INP 导入计数正确 | partial | **partial** | API↔worker 计数已统一（进步）；但无 durable 提交、导入永远停在 queued |
| C2 合法 GeoJSON 导入计数正确 | partial | **partial（更差）** | 新发现：合法 GeoJSON 在 worker 端**必然解析失败**，无任何 durable 执行路径 |
| C3 产出分级校验/拓扑报告 | blocked | **blocked** | 未变；`validation_runs[*].report` 仍恒 `None` |
| C4 round-trip | partial | **partial** | 库级 parse→export→parse 再次实测通过；API 级「导入→导出」仍断裂 |
| C5 error 不半提交 | unverified | **partial（可验证的那一半通过）** | 本轮实测 preflight/staging 层 fail-closed、零残留；durable 侧仍无提交对象 |
| C6 双 spatial profile 同轮证据 | not run | **not run** | 无双 profile 测试；g1/g2/g6 仍无产物 |
| C7 机器证据（run_id/commit/artifact sha/PASS 判据） | not run | **not run（BLOCKED）** | `--gate g2` exit 2；`--gate acceptance --ac ac1` exit 2 |

**AC-1 契约判定：BLOCKED。** 依据 `PRD_SUPPLEMENT.md:11.3`「脚本存在但任一子命令非零时为 FAIL」与 `PRD_SUPPLEMENT.md:586` 的「未实现/本轮未运行」，以及本轮实跑的 exit 2。台账 `CONTRACT_INDEX.md:63` 现记 `BLOCKED` 并给出准确理由，与本轮实测一致，**无「勾选冒充通过」**。

### 本轮相对 round-1 的净变化

**已修复（4 项，均有本轮实跑证据）**
1. **F2 staging + 指纹口径** → 已修。`_stage_job_payload` 落地字节，`request_fingerprint` 改为 payload 自身 sha256。
2. **F4 两套计数** → 已修。worker 改为透传 `network.counts`，与 API preflight 完全一致。
3. **F10 AC-1 门禁映射** → 已修。`ac1` 的 `required_gates` 由硬编码 `("g2",)` 改为读冻结 fixture 的 `["g1","g2","g6"]`，判定更严。
4. **新增回归测试** `tests/acceptance/test_import_staging_contract.py`（4 项），acceptance 套件 61 → **65 passed**。

**仍开放（6 项代码可修 + 2 项基础设施 + 2 项测试资产）**，其中 G3 为本轮**新发现**的 BLOCKER。

---

## 1. 判据来源（逐条引用）

| # | 判据原文 | 位置 |
|---|---|---|
| N1 | 「AC-1 合法 inp/GeoJSON 导入计数正确且有校验报告。」 | `docs/hydraulic-twin/SOURCE_PRD.md:33` |
| N2 | AC-1 证据 = 「两 spatial profile 的 import contract、validation golden JSON、round-trip」；通过判据 = 「计数、stable key、WKT/GeoJSON、issue code、topology 完全一致；error 不半提交」；当前状态 = 「**未实现/本轮未运行**」 | `docs/hydraulic-twin/PRD_SUPPLEMENT.md:586` |
| N3 | 「`PASS` 只允许在目标命令本轮 exit 0 且 artifact 存在、hash 校验通过时填写。静态阅读、`alembic heads`、Compose config、build 或 mock 测试不能替代对应 AC。」 | `docs/hydraulic-twin/PRD_SUPPLEMENT.md` §11.1 |
| N4 | 「脚本/fixture 未实现时固定为 **NOT RUN**，脚本存在但任一子命令非零时为 **FAIL**。」 | `docs/hydraulic-twin/PRD_SUPPLEMENT.md:11.3` |
| N5 | 「AC-1～AC-8 的判据和通用 artifact 字段见 `PRD_SUPPLEMENT.md:11`。每项状态只能为 `PASS/FAIL/NOT RUN/BLOCKED`，其中 `PASS` 必须附本轮目标命令 exit 0 和 artifact SHA-256。」 | `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2541` |
| N6 | 冻结证据要求（AC-1）：`required_gates: ["g1","g2","g6"]`；`required_observations` 含「durable import job reaches a terminal state in **both spatial profiles**」「network revision commits only after validation and materialization succeed」「INP and GeoJSON counts, **stable keys**, geometry and issues round-trip」「failed imports leave no network, revision, file or idempotency residue」 | `backend/tests/fixtures/hydraulic/acceptance/v1/acceptance_requirements.json`（AC-1 case） |
| N7 | 旧 supporting contract（非主基线）：AC-1 artifact 形态 = `artifacts/hydraulic/<run>/ac1/{postgis,portable}.json` | `docs/hydraulic-twin/contracts/operations.md:369` |

---

## 2. 本轮实际运行的检查（命令原文 + 输出）

### 2.1 AC-1 三个 HTTP 诊断测试

```text
$ cd backend && .venv/Scripts/python.exe -m pytest \
    "tests/acceptance/test_acceptance_matrix.py::test_ac1_in_memory_adapter_valid_inp_import_is_accepted_and_replayed" \
    "tests/acceptance/test_acceptance_matrix.py::test_ac1_in_memory_adapter_valid_geojson_import_is_accepted_and_replayed" \
    "tests/acceptance/test_acceptance_matrix.py::test_ac1_production_api_fails_closed_without_durable_repository" -v
PASSED / PASSED / PASSED
3 passed, 1 warning in 0.57s
```

**这些断言仍只证明「排队成功、什么都没落地」**。第三个测试本身就是 AC-1 生产路径不可用的证明（`tests/acceptance/test_acceptance_matrix.py:339-354` 断言 `503 DURABLE_STORE_UNAVAILABLE` 且 `len(hydraulic_store.jobs) == 0`）。三者都不证明「计数正确」或「有校验报告」。

### 2.2 新增的 staging 契约测试（round-1 之后新增）

```text
$ cd backend && .venv/Scripts/python.exe -m pytest tests/acceptance/test_import_staging_contract.py -v
test_an_api_import_job_is_readable_by_the_worker PASSED
test_a_staged_job_yields_the_parser_counts_the_api_reported PASSED
test_staging_is_per_job_and_never_shared PASSED
test_a_mismatched_staging_file_fails_the_attempt_closed PASSED
4 passed, 1 warning in 0.62s
```

这 4 项是**本轮唯一新增的、对 round-1 BLOCKER 的真实回归防线**。它们走真实 HTTP 路由 → 真实 staging 文件 → 真实 `read_staged_input`/`compute_outcome`，不再断言「已排队」。源码：`backend/tests/acceptance/test_import_staging_contract.py:1-191`。

### 2.3 整个 acceptance 套件

```text
$ cd backend && .venv/Scripts/python.exe -m pytest tests/acceptance -q
65 passed, 1 warning in 4.80s
```

（round-1 为 61 passed；增量 4 = 2.2 的新文件。）

### 2.4 库层 round-trip 实测（复跑）

```text
$ cd backend && .venv/Scripts/python.exe -c "<parse_swmm → export_inp → parse_swmm>"
parse1 counts: {"conduits": 1, "coordinates": 2, "junctions": 1, "nodes": 2, "outfalls": 1, "slopes": 1, "subcatchments": 1}
parse2 counts: {"conduits": 1, "coordinates": 2, "junctions": 1, "nodes": 2, "outfalls": 1, "slopes": 1, "subcatchments": 1}
counts equal: True | canonical equal: True
```

拓扑报告本体（`validate_topology`）也再次实测可用：

```text
report status: valid | simulation_ready: True
report counts: {"components": 1, "cycles": 0, "errors": 0, "nodes": 2, "pipes": 1, "warnings": 0}
report issue codes: []
to_dict keys: ['algorithm_version', 'components', 'counts', 'cycles', 'issues', 'simulation_ready', 'status']
content_sha256 attr: 2761825b6c2166c975a7ff9e55181c12e8ee04740669d0a7af1f227edaf1472d
algorithm_version: topology-v1
```

**注意：`content_sha256` 只作为对象属性存在，不在 `to_dict()` 里**（见 G6）。

### 2.5 端到端 HTTP 探针（本轮自建，只读，已删除）

用 `httpx.ASGITransport` 直连 `app.main:app`，`HYDRAULIC_IMPORT_ROOT` 指向临时目录，跑完后 `shutil.rmtree` + `rm` 探针脚本，`git status` 确认 0 残留。

```text
D1 status: 202
D1 api counts: {"conduits": 1, "coordinates": 2, "junctions": 1, "nodes": 2, "outfalls": 1, "slopes": 1, "subcatchments": 1}
D1 import record: {"committed": false, "counts": {...7 键...}, "network_id": null, "status": "queued", "validation": null}
D1 networks created: 0
D2 INSERT INTO hydraulic.network_jobs in app/: 0
D3 worker counts: {"conduits": 1, ..., "subcatchments": 1}
D3 counts identical to API: True                ← F4 已修
D4 geojson status: 202
D4 geojson counts: {"features": 2}
D4 actual feature count: 2
D4 actual by geometry type: {'Point': 2}
D4 worker geojson raised: SwmmParseError INVALID_INP at <input> line 1: data appears before the first [SECTION]   ← G3 新发现
E1 validate: 202 {"network_job_id": ..., "report": null, "status": "queued", "validation_run_id": ...}
E1 GET validation-run: 200 {... "report": null, "status": "queued" ...}
E1 report is None: True                          ← F3 未变
E2 job after import: {"error_code": null, "phase": "accepted", "progress": 0, "result": null, "status": "queued"}
E2 import record committed/status: False queued validation: None
E3 export geojson: 200 len=278    export inp: 422 EMPTY_NETWORK    export wkt: 422 "format must be geojson or inp"
E4 geojson preflight: {"counts": {"features": 2}, "format": "geojson"}
E4 fixture stable_keys: ['N-ACCEPTANCE-1', 'N-ACCEPTANCE-2']   ← 落库记录里没有
E4 staged file exists: True
E4 staged suffix used by worker for job_type='import': 77c1dfb5-....inp
E5 read_staged_input bytes: 761 b'{\n  "type": "FeatureCollection",\n  "prop'
E5 compute_outcome raised: SwmmParseError None INVALID_INP at <input> line 1: data appears before the first [SECTION]
D6 relevant assignment sites:
    app\api\hydraulic_routes.py:2648: hydraulic_store.validation_runs[validation_id] = {
```

**D6 是 C3 的决定性证据**：全 `app/` 目录中 `validation_runs[` 的写入点**只有 1 处**，写入值即 `report: None`（`hydraulic_routes.py:2653`）。没有任何代码把真实报告写回去。

### 2.6 error 不半提交实测（C5，本轮新证据）

在真实 fixture 上做定向变异（把 `C-1 J-1 OUT` 的 `OUT` 换成 `GHOST`；删除 `OUT` 的 COORDINATES 行）：

```text
dangling -> 422 {"code":"INVALID_INP", "detail":"conduit refers to an unknown node"}
   jobΔ 0  impΔ 0  netΔ 0  staged []
nocoords -> 422 {"code":"INVALID_INP", "detail":"node has no COORDINATES row"}
   jobΔ 0  impΔ 0  netΔ 0  staged []
valid    -> 202
   jobΔ 1  impΔ 1  netΔ 0  staged ['10fd41a4-....inp']
```

GeoJSON 侧：

```text
G2 duplicate stable_key -> 422 {"code":"INVALID_GEOJSON","detail":"feature 1 duplicates stable_key 'N-ACCEPTANCE-1'"}
F2 first: 202 replay: 202 same job: True
F2 staged files after two same-key posts: 1     ← 幂等重放不留第二个 staging 文件
```

**这是本轮唯一一条从「unverified」升到「partial 且实测通过」的判据**：preflight 与 staging 层 fail-closed，非法输入不留 job / import 记录 / 网络 / staging 文件，幂等重放返回同一 job id。
**但**：判据原文「error 不半提交」在 N6 中的完整表述是「failed imports leave no network, revision, file or **idempotency residue**」且限定在 durable 语义下——durable 提交路径根本不存在（见 G1），所以这一半无法验证。

### 2.7 冻结门禁 G2

```text
$ backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py --gate g2 \
    --evidence-dir "artifacts/hydraulic/_ac1-r2-probe/g2"
"exit_code": 2, "status": "BLOCKED",
"steps": [{"name": "g2.fixtures", "status": "BLOCKED", "exit_code": 2,
  "missing_fixtures": [
    "backend/tests/fixtures/hydraulic/network_jobs/v1/manifest.json",
    "backend/tests/fixtures/hydraulic/contracts/idempotency-ttl-v1.json",
    "backend/tests/fixtures/hydraulic/security/jwt-rbac-ratelimit-v1.json"
  ]}]
HYDRAULIC_STACK_STATUS=BLOCKED
HYDRAULIC_STACK_EXIT=2
HYDRAULIC_STACK_ARTIFACT_SHA256=e853322a3c47bb4a2584f753b71d4cccef9d65f060bfdf23f6005253189de1e7
```

与 round-1 完全一致的三个缺失 fixture（`GATE_FIXTURES["g2"]`，`scripts/verify_hydraulic_stack.py:99-104`）。

### 2.8 冻结验收汇总门禁

```text
$ backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py --gate acceptance --ac ac1 \
    --run-id "20260927T210928Z-ca99e048" --evidence-dir "artifacts/hydraulic/_ac1-r2-probe/acceptance"
"ac1 entry": {"ac": "ac1", "status": "BLOCKED", "required_gates": ["g1", "g2", "g6"],
  "missing_or_invalid_artifacts": ["g1", "g2", "g6",
    "missing artifact: .../g1/postgis/result.json",
    "missing artifact: .../g1/portable/result.json",
    "non-PASS artifact: .../g2/result.json",
    "missing artifact: .../g6/postgis/result.json",
    "missing artifact: .../g6/portable/result.json"]}
HYDRAULIC_STACK_STATUS=BLOCKED
HYDRAULIC_STACK_EXIT=2
HYDRAULIC_STACK_ARTIFACT_SHA256=b946fc15123e1dc0803aab26d0a8cc7565570f0e418b299e6e94798ed1540b80
```

汇总门禁 fail-closed 正确。`required_gates` 已从 round-1 的 `["g2"]` 收紧为 `["g1","g2","g6"]`，来源是冻结 fixture（`scripts/verify_hydraulic_stack.py:1749-1781` `acceptance_requirements()`），不再是脚本内第二份弱副本。**这是正确方向。**

### 2.9 AC-1 相关水力测试子集

```text
$ cd backend && .venv/Scripts/python.exe -m pytest tests/hydraulic/test_swmm_io.py \
    tests/hydraulic/test_geo_spatial.py tests/hydraulic/test_topology_seed.py \
    tests/test_durable_worker_runtime.py -q
123 passed, 1 warning in 0.25s
```

与 round-1 的 64 + 59 = 123 一致，**零回归**。

### 2.10 明确未运行的检查

| 检查 | 状态 | 原因 |
|---|---|---|
| 全量 backend pytest（797 项） | **NOT RUN** | 属 AC-7 范围；本轮只跑 AC-1 相关子集 |
| 对真实 PostGIS / Timescale 库跑 import worker | **NOT RUN** | 无 fixture、无 G2 入口；不作为本轮诊断 |
| Docker Compose 双 profile 导入（g1/g6） | **NOT RUN** | 属 G1/G6/AC-8；本轮不跑全量门禁 |
| `--gate g1` / `g3` / `g4` | **NOT RUN** | 不在本轮诊断范围（ac1 判据只点名 g2 与 acceptance 汇总） |
| 导入→导出在 API 层的整链 round-trip | **NOT RUN（不可达）** | 见 G1：无 durable 提交，`GET /networks/{id}/exports/*` 只能导出 CRUD 建出的 revision |
| `wkt` 导出 | **NOT RUN（端点不存在）** | `GET /api/networks/{id}/exports/wkt` → `422 VALIDATION_ERROR "format must be geojson or inp"`（探针 E3）。`geometry_wkt` 字段在 `app/` 内有 63 处（`grep -ri wkt app/`），但**没有 wkt 导出格式** |

---

## 3. 发现（按严重度）

### G1 —【BLOCKER / 代码可修】API 从不入队 durable job：worker 永远 claim 不到任何 API 创建的任务

- API 侧 `import_network`（`backend/app/api/hydraulic_routes.py:1503-1560`）把 job 写进**进程内 dict** `hydraulic_store.jobs`（`_new_job` 落盘点 `hydraulic_routes.py:1100`）。
- worker 侧 `SessionNetworkJobRepository.claim_next` 从**表**里取：`"FROM hydraulic.network_jobs "`（`backend/app/workers/network_job_worker.py:216`）。
- **两者之间没有任何写入点。** 本轮实测（探针 D2，遍历 `app/**/*.py` 全文）：

```text
D2 INSERT INTO hydraulic.network_jobs in app/: 0
```

  全 `app/` 目录中 `hydraulic.network_jobs` 只出现在 SELECT/UPDATE（worker）与 ORM 定义（`app/models/hydraulic_models.py:1161-1232`）。
- 叠加第二个断点：`HydraulicApiStore.durable_backend = False` 在构造器里**硬编码**（`hydraulic_routes.py:311`），全仓无任何置 `True` 的代码（`grep -rn "durable_backend" app tests` → 仅 `:311`、`:343`、`:343`、`main.py:1273`）。因此 `_require_durable_api_store()`（`hydraulic_routes.py:341-349`）在 `DEPLOYMENT_ENVIRONMENT=production` 时对所有写路径抛 `503 DURABLE_STORE_UNAVAILABLE`；`app/main.py:1273` 的中间件同码兜底。

**影响**：AC-1 的核心要求「durable import job reaches a terminal state」（N6）在两个方向上都不成立——生产环境直接 503，非生产环境任务永不离开内存。round-1 F1 描述的「`_import_entities` / `_network_from_entities` 是死代码」本轮**仍然成立**（`hydraulic_routes.py:1326`、`:1407` 两个定义，零调用点），但真正的阻断点比 round-1 所述更靠前：连入队都没有，物化函数接不接线无关紧要。
**可由代码修复**：是。需 1) 真正的 SQLAlchemy repository 把 job 写入 `hydraulic.network_jobs`；2) `durable_backend` 接真实依赖；3) 决定物化时机（同步或交 worker）并让 `_import_entities` 真正被调用或删除。

### G2 —【BLOCKER / 代码可修】校验报告在任何模式下都取不到（round-1 F3 未变）

- `hydraulic_routes.py:2648-2655` 写入 `hydraulic_store.validation_runs[validation_id] = {..., "report": None}`；`:2661` 202 响应体同样 `"report": None`。
- `get_validation_run`（`hydraulic_routes.py:2669-2676`）直接 `return {"schema_version": 1, **result}`，不做任何 durable 兜底。
- 全 `app/` 目录中 `validation_runs[` 的**唯一**写入点是 `:2648`（探针 D6），其中 `report` 恒 `None`。
- 本轮实测（探针 E1）：`POST /api/networks/{id}/validate` → 202；`GET /api/validation-runs/{id}` → 200，`report: null`，`status: "queued"`。

**影响**：`SOURCE_PRD.md:33` 的「**且有校验报告**」在 API 交付面完全不可达。库层报告真实可用（§2.4 实测 `status=valid / components=[["J-1","OUT"]] / cycles=0 / algorithm_version=topology-v1`），durable 侧也有真实现（`network_job_worker.py:507-572` 把 `report.to_dict()` 写进 `hydraulic.validation_runs.topology` 并逐条 issue 写 `network_validation_issues`），**缺的是「API 读 durable 表」这一段**。
**可由代码修复**：是。

### G3 —【BLOCKER / 本轮新发现 / 代码可修】合法 GeoJSON 导入在 worker 端必然失败：无任何 durable 执行路径

- API 接受 GeoJSON 并正常入队（探针 D4/E4：202，preflight `format: "geojson"`）。
- staging 后缀由 `staged_input_path` 决定（`backend/app/workers/network_job_worker.py:575-577`）：

```python
def staged_input_path(import_root: Path, job_id: uuid.UUID, job_type: str) -> Path:
    suffix = ".json" if job_type == "seed" else ".inp"
    return Path(import_root) / f"{job_id}{suffix}"
```

  即**任何非 seed 任务一律落成 `.inp`**，与真实载荷格式无关。
- `compute_outcome`（`network_job_worker.py:685-...`）除 `seed` 分支外**无条件**走 SWMM 解析：

```python
network = parse_swmm(payload, strict=claim.job_type != "validate", filename=None)   # :718
```

- 本轮实测（探针 E5，喂入合法 fixture `tests/fixtures/hydraulic/acceptance/v1/network_valid.geojson`）：

```text
E4 staged file exists: True
E4 staged suffix used by worker for job_type='import': 77c1dfb5-....inp
E5 read_staged_input bytes: 761 b'{\n  "type": "FeatureCollection",\n  "prop'   ← 暂存与读回都成功
E5 compute_outcome raised: SwmmParseError None INVALID_INP at <input> line 1: data appears before the first [SECTION]
```

**影响**：这是 round-1 **没有发现**的独立 BLOCKER。即便 G1 的入队路径被补齐，任何合法 GeoJSON 导入任务一旦被 claim 就会因 `INVALID_INP` 失败（非重试或重试耗尽）。`SOURCE_PRD.md:17` 明确要求「导入 SWMM inp、**GeoJSON**」，AC-1 标题本身也是「合法 **inp/GeoJSON** 导入」。现有测试 `test_ac1_in_memory_adapter_valid_geojson_import_is_accepted_and_replayed` 只断言 `counts["features"] == 2`（`tests/acceptance/test_acceptance_matrix.py:307-337`），恰好停在 worker 之前。
**可由代码修复**：是。1) 载荷格式随 job 携带（如 `source_format` 入列）；2) `compute_outcome` 按格式分派到 GeoJSON 解析器；3) 补一条「合法 GeoJSON 任务能被 worker 成功执行」的测试。

### G4 —【HIGH / 代码可修】导入永远停在 `queued`，无终态、无 commit

本轮实测（探针 D1/E2，一次合法 INP 导入之后）：

```text
E2 job after import: {"status": "queued", "progress": 0, "phase": "accepted", "result": null, "error_code": null}
E2 import record committed/status: False queued validation: None
D1 networks created: 0
```

- `hydraulic_store.imports[...]["committed"]` 只在 `hydraulic_routes.py:1554` 被写成 `False`；`grep -n '"committed"\] *= *True\|committed.*True' app/api/hydraulic_routes.py` → **无输出**，全仓无置 `True` 的代码。
- `hydraulic_store.jobs[*]["status"]` 无 `completed` 迁移。
- worker 侧 `_persist_import`（`network_job_worker.py:474-505`）确实写 `committed=true` 入 `hydraulic.network_imports`，但 `network_id` 取自 `claim.network_id`（API 建 job 时为 `None`），且**只写一行元数据，不写 nodes/pipes/catchments**——即使 G1 修好，durable 侧也不会物化管网拓扑。
- API 侧 `GET /networks/{id}/exports/{fmt}` 只能导出 CRUD 建的 revision。探针 E3：空网络 `geojson` → 200 但 `features: []`；`inp` → `422 EMPTY_NETWORK`；`wkt` → 端点不存在。

**影响**：N6「network revision commits only after validation and materialization succeed」无实现对象。
**可由代码修复**：是。

### G5 —【HIGH / 代码可修】stable key 未落库，GeoJSON 计数只有一个总数

- GeoJSON preflight 只返回 `{"features": len(features)}`（`hydraulic_routes.py:1273`），无按 `entity_type` 拆分、无管段端点计数。
- 探针 E4/F3：fixture 两个 feature 的 `stable_key` 是 `N-ACCEPTANCE-1` / `N-ACCEPTANCE-2`，但 import 记录里只有 `{"committed": false, "counts": {"features": 2}, ...}`，**没有任何 stable key 留痕**；INP 侧同理，7 键 counts 里不含节点/管段名。
- 唯一读得到 stable key 的地方是 `_entity_from_import_feature` 的**校验**分支（探针 G2 证明重复 stable_key 会被 422 拒绝），但该实体化链路属于 G1 的死代码。

**影响**：`PRD_SUPPLEMENT.md:586` 明列判据「计数、**stable key**、WKT/GeoJSON、issue code、topology 完全一致」中的 stable key 一项在两个交付面都无证据。
**可由代码修复**：是。

### G6 —【MEDIUM / 代码可修】报告 JSON 不含自描述 hash，golden 比对无法冻结

`ValidationReport.to_dict()` 实测键集为 `['algorithm_version', 'components', 'counts', 'cycles', 'issues', 'simulation_ready', 'status']`——**`content_sha256` 只作为对象属性存在**（探针实测 `2761825b6c2166c975a7ff9e55181c12e8ee04740669d0a7af1f227edaf1472d`）。

`_persist_validation`（`network_job_worker.py:523-545`）写 `"topology": json.dumps(document, ...)`，因此 durable 的 `hydraulic.validation_runs.topology` JSONB 里**没有**这个 hash。`JobOutcome.result_sha256` 用了它（`compute_outcome` 末行 `result_sha256=report.content_sha256`），但 API 读不到。

**影响**：N2 要求「validation golden JSON」可逐字节比对；没有自描述 hash，冻结的 golden 只能靠 diff 而不能靠 hash 校验（N3 要求 `artifact_sha256`）。
**可由代码修复**：是（小改：把 `content_sha256` 并入 `to_dict()`）。

### G7 —【BLOCKER / 基础设施，与 round-1 F5 相同】G2 冻结 fixture 仍全部缺失

见 §2.7。`GATE_FIXTURES["g2"]`（`scripts/verify_hydraulic_stack.py:99-104`）要求的三个文件在 `backend/tests/fixtures/hydraulic/` 下不存在——该目录当前只有 `acceptance/ compose/ contracts/ imports/` 四个子目录（`ls` 实测；`network_jobs/` 报 `No such file or directory`）。

另注：即使 fixture 补齐，若 manifest 未定义 `GATE_COMMANDS_KEY`（`scripts/verify_hydraulic_stack.py:116`），`gate_domain` 仍会 BLOCKED 并写明「static fixture presence is not gate evidence」（`:771`）。
**可由代码修复**：是（需先有 G1/G2/G3 的可执行实现，否则命令跑出来仍是 FAIL）。

### G8 —【BLOCKER / 基础设施，与 round-1 F6 相同】CI 仍无 G1～G4 job

```text
$ grep -n "^  [a-z0-9-]*:" .github/workflows/ci.yml
4:  push:      11:  contents: read      14:  group: ...      15:  cancel-in-progress: true
24:  dependency-lock:   56:  host-interpreter:   77:  g0-quality:
117: frontend-quality:  149: compose-config:  212: frontend-e2e:
251: g5-performance:     288: acceptance-summary:
```

与 round-1 逐行一致。CI 只调 `verify_hydraulic_stack.py` 五次（`:104`、`:183`、`:197`、`:275`、`:326`），对应 G0、G6 portable、G6 postgis、G5、acceptance 汇总。**没有 g1/g2/g3/g4**，而 `acceptance_requirements.json` 现在要求 ac1 必须有 `g1`、`g2`、`g6` 三者同轮产物 → AC-1 在当前 CI 里**结构性不可能 PASS**。
**可由代码修复**：是（依赖 G7）。

### G9 —【HIGH / 需新增测试资产】validation golden JSON 与双 profile import contract 仍不存在

```text
$ find tests/fixtures -iname "*golden*" -o -iname "*validation*"
（无输出）
$ grep -rn "import_contract" --include=*.py tests app
（无输出）
$ grep -n "_ACTIVE_SPATIAL_BACKEND" app/models/hydraulic_models.py
86:_ACTIVE_SPATIAL_BACKEND = SpatialBackend(settings.HYDRAULIC_SPATIAL_BACKEND)
565/612/684/730: 条件分支
```

`_ACTIVE_SPATIAL_BACKEND` 在**导入期**绑定为模块级常量，同进程内无法切 profile；双 profile 测试必须子进程或两次独立运行。N2/N7 点名的 `test_import_contract` 与 validation golden 都不存在。
**可由代码修复**：是。**顺序建议：先修 G1/G2/G3，再冻结 golden**——否则 golden 会把错误实现固化进基线。

### G10 —【LOW / 规格漂移】AC-1 artifact 路径在两份文档间仍不一致

`contracts/operations.md:369`（旧 supporting contract）要求 `artifacts/hydraulic/<run>/ac1/{postgis,portable}.json`；`gate_acceptance` 实际只写单一 `acceptance/result.json`，per-profile 形态体现在 `g1/postgis`、`g6/postgis` 等 gate 级路径上，而非 AC 级。`contracts/*.md` 已被 `SOURCE_PRD.md:9` 声明为「已被取代的 supporting contracts」，故属漂移非阻断；但 N7 要求的落地形态仍未确定。
**可由代码修复**：是（在补 G7/G8 时一并冻结）。

---

## 4. 逐条判据结论

| 判据 | 状态 | 依据 | 代码可修 |
|---|---|---|---|
| **C1 合法 INP 导入计数正确** | **partial** | API 7 键 counts 与 worker `network.counts` **完全一致**（§2.5 D3 实测 `True`，F4 已修）；但无 durable 提交（G1）、无终态（G4）、无 stable key 留痕（G5） | 是 |
| **C2 合法 GeoJSON 导入计数正确** | **partial** | 只有一个总数 `{"features": 2}`（§2.5 E4）；更严重：合法 GeoJSON 在 worker 端**必然 `INVALID_INP` 失败**（G3，§2.5 E5），durable 路径不存在 | 是 |
| **C3 产出分级校验/拓扑报告** | **blocked** | 库层报告真实可用（§2.4）；API 侧 `report` 在任何模式下恒 `None`（G2，§2.5 E1 + D6 唯一写入点） | 是 |
| **C4 round-trip** | **partial** | 库级 parse→export→parse 语义等价**已实测通过**（§2.4，counts equal + canonical equal）；API 级「导入→导出」因 G1/G4 断裂；`wkt` 导出端点不存在（§2.10） | 是 |
| **C5 error 不半提交** | **partial** | preflight/staging 层**已实测 fail-closed**：422 且 jobΔ=0 / impΔ=0 / netΔ=0 / staged=[]，幂等重放不产生第二个 staging 文件（§2.6）。durable 侧无提交对象可测 | 是 |
| **C6 双 profile 同轮证据** | **not run** | 无双 profile 测试（G9）；g1/g2/g6 无产物（§2.7/§2.8） | 是（需新测试 + CI） |
| **C7 机器证据** | **not run（BLOCKED）** | `--gate g2` exit 2（§2.7）、`--gate acceptance --ac ac1` exit 2（§2.8）；`artifacts/hydraulic/` 下无 `g1`/`g2`/`acceptance` 目录（`ls` 实测） | 是（需新 fixture） |

---

## 5. 台账与文档一致性

| 台账声明 | 位置 | 本轮复核 |
|---|---|---|
| 「AC-1 import/round-trip \| **BLOCKED** \| 门禁实跑 BLOCKED/exit 2（G2 冻结 fixture 缺失）；非 NOT RUN。API 侧 staging/指纹/计数已接线，durable 提交路径与双 profile G1/G6 仍缺 \| g1, g2, g6」 | `docs/hydraulic-twin/CONTRACT_INDEX.md:63` | **一致**。本轮独立复核：门禁确为 BLOCKED/exit 2；staging/指纹/计数确已接线；durable 提交路径确缺；`required_gates` 确为 `g1, g2, g6`。这是本轮见到的**最准确的一条 AC-1 记录** |
| `matrix.json` AC-1 = `status: "NOT RUN"`, `pass_nodeids: []`, `reason: "...HydraulicApiStore(durable_backend=False) and prove only queued, uncommitted adapter state. Same-run G1/G2/G6 artifacts for both profiles are absent."` | `backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json` | **一致**。`pass_nodeids` 为空，`status` 不是 PASS；`durable_backend=False` 与 `G1/G2/G6` 缺失均被本轮实测确认 |
| `acceptance_requirements.json` AC-1 `evidence_state: "NOT_RUN"` + 「This file freezes evidence requirements only; it is not execution evidence and cannot make an AC PASS.」 | 同目录 | **一致且恰当**。四条 `required_observations` 与本轮 G1/G3/G4/G5 一一对应 |
| 「AC-1 – AC-8（solver runtime 阶段）\| `NOT RUN`」 | `docs/hydraulic-twin/VALIDATION_EVIDENCE.md:439` | **一致**。AC-1 至今无任何升级记录 |
| 「`scripts/verify_hydraulic_stack.py`、三个 checker、三个 benchmark harness 和 `test:perf:twin` 当前未交付…」 | `IMPLEMENTATION_CONTRACT.md:2641` | **已过期**。`scripts/verify_hydraulic_stack.py` 存在且本轮实际调用成功并 fail-closed 返回 exit 2 |
| 「AC-1～AC-8 均为 **NOT RUN**」（`IMPLEMENTATION_CONTRACT.md:2499`）与 `AC-1 \| NOT RUN（无实现）`（`:2547`） | 同上 | **方向正确但过于宽松**。入口已存在并返回 exit 2，按 `PRD_SUPPLEMENT.md:11.3` 应为 **BLOCKED**（`CONTRACT_INDEX.md:63` 已按此更新，两处文档尚未同步） |

**未发现「勾选冒充通过」**：本轮读到的所有 AC-1 相关台账（`CONTRACT_INDEX.md`、`matrix.json`、`acceptance_requirements.json`、`VALIDATION_EVIDENCE.md`）都**没有**声称 AC-1 通过。

---

## 6. 建议的修复顺序（供实现者参考；本轮未执行任何修改）

1. **G3**（GeoJSON worker 分派）——成本最低、风险最小，且独立于其他项。修好后立即补一条「合法 GeoJSON 任务被 worker 成功执行」的测试。
2. **G1**（durable 入队 + `durable_backend` 接真依赖）→ **G4**（终态 + commit + 物化）→ **G2**（API 读 durable 报告）。这三项是 AC-1 的最小可运行闭环，**互相耦合，必须同一轮改、同一轮测**；单独修任何一项都不会让 AC-1 前进。
3. **G5 / G6** 紧跟其后：先定权威 counts + stable key 落库口径，再把 `content_sha256` 并入 `to_dict()`。
4. **G9** 的 golden 最后冻结——在 1～3 全绿之前冻结会把错误实现写进基线。
5. **G7 + G8 + G10**：新增 G2 fixture（manifest 必须带 `gate_commands`，否则 `:771` 仍 BLOCKED）+ CI 的 g1/g2 job + 冻结 per-profile AC-1 artifact 形态。
6. 全部完成后，用同一 `RUN_ID`、同一 commit 重跑 `g1(postgis/portable)`、`g2`、`g6(postgis/portable)` 与 `--gate acceptance --ac ac1`，才可能写下 `PASS`。

---

## 7. 复现命令汇总（本轮实际执行）

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage"
git log --oneline -3
git status --porcelain --untracked-files=all | wc -l
backend/.venv/Scripts/python.exe --version

cd backend
# 2.1 / 2.2 / 2.3 / 2.9
.venv/Scripts/python.exe -m pytest \
    "tests/acceptance/test_acceptance_matrix.py::test_ac1_in_memory_adapter_valid_inp_import_is_accepted_and_replayed" \
    "tests/acceptance/test_acceptance_matrix.py::test_ac1_in_memory_adapter_valid_geojson_import_is_accepted_and_replayed" \
    "tests/acceptance/test_acceptance_matrix.py::test_ac1_production_api_fails_closed_without_durable_repository" -v
.venv/Scripts/python.exe -m pytest tests/acceptance/test_import_staging_contract.py -v
.venv/Scripts/python.exe -m pytest tests/acceptance -q
.venv/Scripts/python.exe -m pytest tests/hydraulic/test_swmm_io.py tests/hydraulic/test_geo_spatial.py \
    tests/hydraulic/test_topology_seed.py tests/test_durable_worker_runtime.py -q
# 2.4 库层 round-trip + 报告结构（内联 python -c）
# 2.5 / 2.6 端到端 HTTP 探针（临时脚本 backend/_ac1_round2_probe.py，已删除）

# 静态取证
grep -rn "_import_entities\|_network_from_entities" --include=*.py app tests
grep -rn "durable_backend" --include=*.py app tests
grep -n "validation_runs\[" app/api/hydraulic_routes.py
grep -rn "import_contract" --include=*.py tests app
find tests/fixtures -iname "*golden*" -o -iname "*validation*"

cd ..
backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py --gate g2 \
    --evidence-dir "artifacts/hydraulic/_ac1-r2-probe/g2"
backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py --gate acceptance --ac ac1 \
    --run-id "20260927T210928Z-ca99e048" --evidence-dir "artifacts/hydraulic/_ac1-r2-probe/acceptance"

# 清理（已执行）
rm -f backend/_ac1_round2_probe.py
rm -rf artifacts/hydraulic/_ac1-r2-probe
git status --porcelain --untracked-files=all | grep -icE "ac1.?r2|_ac1_round2"   # → 0
```
