# v3 Schema / Semantics 独立复审（第 3 轮）

- **复审者**：`dynamic-workflow v3-schema-semantics-review-3`（独立只读复审；不是文档作者或实现者）
- **复审日期**：2026-09-24
- **复审对象**：`SOURCE_PRD.md`、`PRD_SUPPLEMENT.md`、`IMPLEMENTATION_CONTRACT.md`，以及最新 `implementation/dependency-contract.md`、`implementation/hydraulic-schema.md`、`reviews/dependency-contract.md`、上一轮 `reviews/v3-schema-semantics-review.md`（内容等同 `reviews/v3-schema-semantics-review-round-1.md`，两文件字节一致）
- **基线 commit**：`ca99e0485fb12fbecbd565e79d81f745f8d4a748`（短名 `ca99e048`）
- **结论**：**不批准（`NOT_APPROVED` / `R0_FAIL`）**。上一轮 §3.1～§3.8 与 B-01～B-07 已在本轮逐条静态复核中确认消除，上一轮 §4 的 PASS 项也基本保持；但本轮独立复核在 schema/semantics 层新发现 1 个 BLOCKER、1 个 HIGH、1 个 MEDIUM、1 个 LOW 级别的规范内部矛盾，其中 C-01 直接违反“错误码/幂等单一矩阵”这一批准条件。

> 证据边界：本轮只读代码、三份主文档和 remediation/review 文档；没有修改生产代码、migration 或三份主文档。除本报告及同步覆盖的 `reviews/v3-schema-semantics-review.md` 外，没有创建实现、gate、fixture、handoff、R0 JSON artifact 或任何运行 artifact。
>
> 批准条件判定：任务要求“只有 schema/semantics、owner、授权和依赖图均无矛盾时才批准”。C-01 是幂等语义与表级唯一约束之间的规范内部矛盾，属于 schema/semantics，故不满足批准条件。

## 0. 输入、版本与本轮实际执行

### 0.1 文档内容哈希

执行命令（仓库根）：

```text
$ sha256sum docs/hydraulic-twin/SOURCE_PRD.md docs/hydraulic-twin/PRD_SUPPLEMENT.md docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md
11e56e68a68017b84b5ae577dede8a74f6217d4a7877f5e358a18f14e66807ee *docs/hydraulic-twin/SOURCE_PRD.md
33c7c6e723fcebfc55a409b7fa597be5a04a91d0dad73d03fc4f049855f112af *docs/hydraulic-twin/PRD_SUPPLEMENT.md
1aae4cc84651dfcdcd75ad53677705d383b63e5fb08f4e8588d192d9cf0f6cb6 *docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md
```

按 `IMPLEMENTATION_CONTRACT.md:2350` 的 canonical bundle 定义（`{"schema":"v3-review-bundle-v1","base_commit":...,"documents":[...]}`，documents 按 path 排序，UTF-8 canonical JSON）只读计算，输出：

```text
base_commit = ca99e0485fb12fbecbd565e79d81f745f8d4a748
canonical_bytes = {"base_commit":"ca99e0485fb12fbecbd565e79d81f745f8d4a748","documents":[{"path":"docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md","sha256":"1aae4cc84651dfcdcd75ad53677705d383b63e5fb08f4e8588d192d9cf0f6cb6"},{"path":"docs/hydraulic-twin/PRD_SUPPLEMENT.md","sha256":"33c7c6e723fcebfc55a409b7fa597be5a04a91d0dad73d03fc4f049855f112af"},{"path":"docs/hydraulic-twin/SOURCE_PRD.md","sha256":"11e56e68a68017b84b5ae577dede8a74f6217d4a7877f5e358a18f14e66807ee"}],"schema":"v3-review-bundle-v1"}
document_bundle_sha256 = e49df8297af5f8f4829aa423490eeb1cd4c6956e9a2f37e551bd9ce062b7f815
```

该 bundle 与 round-1 报告记录的 `8ce47dc3…`、以及 `PRD_SUPPLEMENT.md:37` / `IMPLEMENTATION_CONTRACT.md:2351` 自述的 round-2 被审 bundle `bd451b60…` 三者都不同，说明 N-01～N-07 整改确实再次改变了三文档内容；任何旧 verdict 都不能复用于本轮输入。`SOURCE_PRD.md:2-11` 仍声明其为不可变需求归档，本轮未改写其正文（其 hash 与 round-1 记录一致）。

### 0.2 仓库状态

执行命令及输出：

```text
$ git rev-parse --short=8 HEAD
ca99e048
$ git rev-parse HEAD
ca99e0485fb12fbecbd565e79d81f745f8d4a748
$ git status --porcelain --untracked-files=all | wc -l
46
$ git status --porcelain --untracked-files=all
 M .env.example
 M .github/workflows/ci.yml
 M README.md
 M backend/app/api/routes.py
 M backend/app/core/config.py
 M backend/app/core/security.py
 M backend/app/workers/inference_worker.py
 M backend/pyproject.toml
 M backend/requirements-cpu.txt
 M backend/requirements.txt
 M backend/tests/test_api/test_auth.py
 M docker-compose.yml
 M docker/backend/Dockerfile
 M docker/backend/Dockerfile.cpu
 M docker/frontend/Dockerfile
 M frontend/package-lock.json
 M frontend/package.json
?? .python-version
?? backend/requirements-hydraulic-py311.lock
?? backend/requirements-hydraulic.txt
?? backend/requirements-py311-gpu.lock
?? backend/requirements-py311.lock
?? backend/tests/test_requirements_matrix.py
?? backend/tests/test_security_tokens.py
?? backend/tests/test_simulation_settings.py
?? docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md
?? docs/hydraulic-twin/PRD_SUPPLEMENT.md
?? docs/hydraulic-twin/SOURCE_PRD.md
?? docs/hydraulic-twin/contracts/data.md
?? docs/hydraulic-twin/contracts/forecast.md
?? docs/hydraulic-twin/contracts/frontend.md
?? docs/hydraulic-twin/contracts/hydraulics.md
?? docs/hydraulic-twin/contracts/operations.md
?? docs/hydraulic-twin/implementation/dependency-contract.md
?? docs/hydraulic-twin/implementation/hydraulic-schema.md
?? docs/hydraulic-twin/recon/backend-data.md
?? docs/hydraulic-twin/recon/deployment.md
?? docs/hydraulic-twin/recon/frontend.md
?? docs/hydraulic-twin/recon/quality.md
?? docs/hydraulic-twin/recon/realtime-security.md
?? docs/hydraulic-twin/recon/simulation.md
?? docs/hydraulic-twin/reviews/dependency-contract.md
?? docs/hydraulic-twin/reviews/v3-schema-semantics-review-round-1.md
?? docs/hydraulic-twin/reviews/v3-schema-semantics-review.md
?? frontend/.node-version
?? frontend/.npmrc
```

```text
$ git status --porcelain -- backend/alembic/versions
(no output)
$ git ls-tree -r --name-only HEAD -- docs
(no output)
```

`docs/` 树当前未被 Git 跟踪，Git 无法用 tracked diff 证明 `SOURCE_PRD.md` 来源；本轮只能以 SHA-256 与 round-1 记录一致证明未观察到内容漂移。`backend/alembic/versions` 目录本轮仍无工作树变化。

上表为本报告写入**之前**的实测状态（46 项）。写入本报告与同步覆盖 `reviews/v3-schema-semantics-review.md` 之后复测：

```text
$ git status --porcelain --untracked-files=all | wc -l
47
$ git status --porcelain --untracked-files=all | tail -22
?? docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md
?? docs/hydraulic-twin/PRD_SUPPLEMENT.md
?? docs/hydraulic-twin/SOURCE_PRD.md
?? docs/hydraulic-twin/contracts/data.md
?? docs/hydraulic-twin/contracts/forecast.md
?? docs/hydraulic-twin/contracts/frontend.md
?? docs/hydraulic-twin/contracts/hydraulics.md
?? docs/hydraulic-twin/contracts/operations.md
?? docs/hydraulic-twin/implementation/dependency-contract.md
?? docs/hydraulic-twin/implementation/hydraulic-schema.md
?? docs/hydraulic-twin/recon/backend-data.md
?? docs/hydraulic-twin/recon/deployment.md
?? docs/hydraulic-twin/recon/frontend.md
?? docs/hydraulic-twin/recon/quality.md
?? docs/hydraulic-twin/recon/realtime-security.md
?? docs/hydraulic-twin/recon/simulation.md
?? docs/hydraulic-twin/reviews/dependency-contract.md
?? docs/hydraulic-twin/reviews/v3-schema-semantics-review-round-1.md
?? docs/hydraulic-twin/reviews/v3-schema-semantics-review-round-3.md
?? docs/hydraulic-twin/reviews/v3-schema-semantics-review.md
?? frontend/.node-version
?? frontend/.npmrc
```

新增的唯一路径是 `docs/hydraulic-twin/reviews/v3-schema-semantics-review-round-3.md`（本报告），其 owner 为 `r0-reviewer`（`IMPLEMENTATION_CONTRACT.md:2300` 的 `docs/hydraulic-twin/reviews/**`），不改变 §0.4 的 owner 解析结论。

### 0.3 契约点名的 gate/checker/harness 入口

本轮按 `IMPLEMENTATION_CONTRACT.md:2376-2388` 与 `PRD_SUPPLEMENT.md:597-609` 从仓库根执行了全部 18 个点名命令。统一 `RUN_ID` 为：

```text
audit3-20260924T121655Z-ca99e048
```

命令与实际输出（全部逐条执行）：

```text
$ python scripts/verify_hydraulic_stack.py --gate g0 --evidence-dir "artifacts/hydraulic/${RUN_ID}/g0"
C:\Python314\python.exe: can't open file '...\\scripts\\verify_hydraulic_stack.py': [Errno 2] No such file or directory
exit=2

$ python scripts/verify_hydraulic_stack.py --gate g1 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g1/postgis"
... No such file or directory ; exit=2
$ python scripts/verify_hydraulic_stack.py --gate g1 --profile portable --evidence-dir "artifacts/hydraulic/${RUN_ID}/g1/portable"
... No such file or directory ; exit=2
$ python scripts/verify_hydraulic_stack.py --gate g2 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g2"
... No such file or directory ; exit=2
$ python scripts/verify_hydraulic_stack.py --gate g3 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g3"
... No such file or directory ; exit=2
$ python scripts/verify_hydraulic_stack.py --gate g4 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g4"
... No such file or directory ; exit=2
$ python scripts/verify_hydraulic_stack.py --gate g5 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g5"
... No such file or directory ; exit=2
$ python scripts/verify_hydraulic_stack.py --gate g6 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g6/postgis"
... No such file or directory ; exit=2
$ python scripts/verify_hydraulic_stack.py --gate g6 --profile portable --evidence-dir "artifacts/hydraulic/${RUN_ID}/g6/portable"
... No such file or directory ; exit=2
$ python scripts/verify_hydraulic_stack.py --gate acceptance --ac ac1,ac2,ac3,ac4,ac5,ac6,ac7,ac8 --evidence-dir "artifacts/hydraulic/${RUN_ID}/acceptance"
... No such file or directory ; exit=2

$ python scripts/check_contract_ownership.py --contract docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md --phase current
... No such file or directory ; exit=2
$ python scripts/check_contract_ownership.py --contract docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md --phase post-handoff
... No such file or directory ; exit=2
$ python scripts/check_migration_gate.py --review-artifact artifacts/contracts/v3-independent-review.json --base-ref ca99e0485fb12fbecbd565e79d81f745f8d4a748 --versions-dir backend/alembic/versions
... No such file or directory ; exit=2
$ python scripts/check_dependency_handoff.py --handoff artifacts/contracts/dependency-config-handoff.json --expected backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json --python-version-file .python-version --node-version-file frontend/.node-version --npmrc frontend/.npmrc --package frontend/package.json --lock frontend/package-lock.json --env-example .env.example
... No such file or directory ; exit=2

$ python scripts/benchmark_hydraulic.py --fixture backend/tests/fixtures/performance/solver/5k_10k_24h_v1/manifest.json --warmup 1 --repeats 3 --cpus 4 --memory 8g --threads 4 --output "artifacts/hydraulic/${RUN_ID}/g5/solver.json"
... No such file or directory ; exit=2
$ python scripts/benchmark_hydraulic_api.py --fixture backend/tests/fixtures/performance/api/control-plane-v1.json --warmup-seconds 60 --duration-seconds 600 --vu 20 --profile postgis --output "artifacts/hydraulic/${RUN_ID}/g5/api.json"
... No such file or directory ; exit=2
$ python scripts/benchmark_hydraulic_nowcast.py --fixture backend/tests/fixtures/performance/nowcast/30-cycle-v1.json --fake-cycles 100 --real-cycles 30 --profile postgis --output "artifacts/hydraulic/${RUN_ID}/g5/nowcast.json"
... No such file or directory ; exit=2

$ npm --prefix frontend run test:perf:twin -- --fixture frontend/performance/fixtures/twin-map-5k-10k-v1.json --duration-seconds 60 --output "artifacts/hydraulic/${RUN_ID}/g5/frontend.json"
npm error Missing script: "test:perf:twin"
exit=1
```

本机解释器/工具链（仅作环境记录，不是门禁证据）：

```text
$ python --version
Python 3.14.4
$ node --version
v25.9.0
$ npm --version
11.12.1
```

契约要求 G0/G5 在 Python 3.11 / Node 18.20.8 / npm 10.8.2 下运行（`IMPLEMENTATION_CONTRACT.md:2355,2423`），本机版本不满足；由于入口脚本本身不存在，这只说明本轮不可能在合规工具链下运行，不能记为 FAIL verdict，只记为 NOT RUN。

逐项存在性审计（同一 RUN_ID）：

```text
MISSING scripts/verify_hydraulic_stack.py
MISSING scripts/check_contract_ownership.py
MISSING scripts/check_migration_gate.py
MISSING scripts/check_dependency_handoff.py
MISSING scripts/benchmark_hydraulic.py
MISSING scripts/benchmark_hydraulic_api.py
MISSING scripts/benchmark_hydraulic_nowcast.py
MISSING artifacts/contracts/v3-independent-review.json
MISSING artifacts/contracts/dependency-config-handoff.json
MISSING backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json
MISSING backend/tests/fixtures/hydraulic/network_jobs/v1/manifest.json
MISSING backend/tests/fixtures/hydraulic/workers/lifecycle-v1.json
MISSING backend/tests/fixtures/hydraulic/calibration/durable-lifecycle-v1.json
MISSING backend/tests/fixtures/hydraulic/hydraulics/conservation-stability-v1.json
MISSING backend/tests/fixtures/hydraulic/forecast/v1/manifest.json
MISSING backend/tests/fixtures/hydraulic/realtime/outbox-sse-v1.json
MISSING backend/tests/fixtures/hydraulic/schema/manifest-v1.json
MISSING docker/hydraulic-gates/compose-v1.lock.json
MISSING backend/tests/fixtures/hydraulic/compose/restore-manifest-v1.json
MISSING backend/tests/fixtures/performance/solver/5k_10k_24h_v1/manifest.json
MISSING backend/tests/fixtures/performance/api/control-plane-v1.json
MISSING backend/tests/fixtures/performance/nowcast/30-cycle-v1.json
MISSING frontend/performance/fixtures/twin-map-5k-10k-v1.json
```

G0～G6、AC-1～AC-8 在本轮均为 **NOT RUN（入口缺失）**，不是 PASS，也不是脚本级 FAIL。这与 `PRD_SUPPLEMENT.md:595,630`、`IMPLEMENTATION_CONTRACT.md:2413,2455,2553` 的 fail-closed 声明一致。

### 0.4 本轮实际执行的静态结构断言

以下为只读 Python 断言（读取三份主文档与 `git status`，不写任何文件），用于替代官方 checker 的“定义层”核对：

```text
=== DEP-AUTH registry ===
rows: 13
ids: DEP-AUTH-01..DEP-AUTH-13（连续、无重复）
prep/landing flags: 01..12 = true/true；13 = false/true

=== owner tables §15.1～§15.6 ===
owner rows: 119
unique path patterns: 119
patterns with >1 row: []
§15.1 十行与 DEP-AUTH-01..06,09..12 的 full-cycle owner 全部一致（mismatch=0）
registry paths missing from §15.1: backend/tests/test_simulation_settings.py, backend/tests/test_requirements_matrix.py, artifacts/contracts/dependency-config-handoff.json
（这三项由 §15.6 的 dependency-config / dependency-config 行覆盖，owner 同值，不构成矛盾）

=== worktree candidate resolution（按 §15.0:2128 的 exact > 最长 literal prefix 规则） ===
candidates: 46
unmatched: []
ambiguous: []
owner distribution: dependency-config 16, delivery-docs 10, recon-docs 6, security-gate 3, compose-worker 3, r0-reviewer 3, ci-quality 1, backend-integration 1, integration-config 1, legacy-backend 1, dependency-docs 1

=== blockedBy 表（§16:2311-2338） ===
node count: 26
unknown deps: []
cycles: []
DOC-OWNERSHIP <- []
GATE-BOOTSTRAP <- ['R0-REVIEW'] ; INTEGRATION <- ['DEP-LANDING','NETWORK-WORKER','SIM-WORKER','FORECAST-NOWCAST','FRONTEND-FOUNDATION']
GATE-HARNESS <- ['INTEGRATION'] ; CI-QUALITY <- ['GATE-HARNESS'] ; ACCEPTANCE <- ['G0'..'G6']

=== durable job 状态枚举 ===
status enums: network_jobs(497) / simulations(671) / calibration_runs(893) / nowcast_runs(1024) 全部为
queued|leased|running|retry_wait|cancelling|cancelled|completed|failed|timed_out
IC 状态转换表头出现次数: 1（唯一）
PRD 状态转换表头出现次数: 0（已委托给 IC §6.1）

=== 20 项 B-01..B-07 / §3 / §4 关键锚点断言 ===
PASS B-01 busy=false full null
PASS B-01 busy=true lease notnull + >
PASS B-02 idempotency id UUID PK
PASS B-02 FK to idempotency_records.id
PASS B-03 UNIQUE(network_revision_id,schema_version)
PASS B-04 no SIMULATION_TIMEOUT in failed rows
PASS B-05 G3 four types
PASS B-05 calibration fixture registered
PASS B-06 single graph clause
PASS B-07 two g6 commands
PASS S3.1 slot seed null
PASS S3.2 single transition table
PASS S3.3 single DEP-AUTH registry
PASS S3.5 phase vocab
PASS S3.6 DOC-OWNERSHIP emdash
PASS S3.7 three hash wording
PASS S4 frame reg PK
PASS S4 hypertable PK includes frame_time
PASS S4 forbid cross-part unique
PASS S4 obs trust 4 levels
PASS S4 fail-closed no run
PASS S4 reserved slot literal
PASS S4 IC 18 blocklist 29 idempotency FK
PASS S4 IC 18 blocklist 44 generation
ALL: True

=== §12 Settings registry 与实现面 ===
§12 registry 变量数: 54
§12 变量全部存在于 .env.example: True
§12 变量全部存在于 backend/app/core/config.py 字段: True
config.py 字段数: 101（47 个 legacy/安全/部署字段不在 §12 registry 中）
.env.example 变量数: 101
config.py 中不存在于 .env.example 的字段: []
```

这些断言是**定义层静态核对**，不等于官方 checker PASS（checker 不存在，见 §0.3）。

## 1. 结论摘要

| ID | 严重性 | 结论 | 核心证据 |
|---|---|---|---|
| C-01 | **BLOCKER** | `idempotency_records` 的 24h TTL + generation 语义与 `simulations` / `calibration_runs` 的永久 `UNIQUE(requested_by, idempotency_key)` 冲突：24h 后同 key 必然无法创建新 generation 资源 | `PRD_SUPPLEMENT.md:235,241,246`；`IMPLEMENTATION_CONTRACT.md:670,691,906,909,1215,2517` |
| C-02 | **HIGH** | §6.1 强制四类 durable job 表都带 `CHECK ((status='retry_wait') = (next_attempt_at IS NOT NULL))`，但 §3 冻结 DDL 的四张表全部缺该 CHECK | `IMPLEMENTATION_CONTRACT.md:1370` vs `:489-527,641-704,882-912,1009-1074` |
| C-03 | MEDIUM | §17.0 保留谓词要求 Settings 字段名集合“与第 12 节 v3 Settings registry 完全相等”，但 §12 registry 只列 54 项而 `Settings` 有 101 字段，谓词按字面不可满足 | `IMPLEMENTATION_CONTRACT.md:2358` vs `:1938-1993`；`backend/app/core/config.py` 101 字段 |
| C-04 | LOW | `PRD_SUPPLEMENT.md:37` / `IMPLEMENTATION_CONTRACT.md:2351` 自述的 round-2 被审 hash/bundle 与本轮实际文件 hash 不符，且该 hash 对应的文件内容已不存在于工作树，证据链自指失效 | `PRD_SUPPLEMENT.md:37`；本轮 `sha256sum` 输出 |
| B-01～B-07 | — | 上一轮 7 个阻断项**本轮全部确认消除**（静态） | 见 §2 |
| G0～G6 / AC-1～AC-8 | — | 全部 **NOT RUN**（入口与 fixture 均不存在） | 见 §0.3 |

C-01～C-03 是设计文档内部可在纸面上复现的矛盾，不是“尚未实现所以未通过”的同义词，因此按 `PRD_SUPPLEMENT.md:45-47` 的设计冻结条件不能批准。

## 2. 上一轮 §3.1～§3.8 逐项复核

### §3.1 `worker_slots` CHECK 与初始化/释放矛盾 — **已彻底消除（PASS，静态）**

`IMPLEMENTATION_CONTRACT.md:716-724` 的 CHECK 现在两个分支都完整：

```text
(busy=false AND worker_id IS NULL AND simulation_job_id IS NULL AND nowcast_run_id IS NULL
 AND lease_until IS NULL AND heartbeat_at IS NULL)
OR
(busy=true AND worker_id IS NOT NULL AND lease_until IS NOT NULL AND heartbeat_at IS NOT NULL
 AND lease_until>heartbeat_at AND ((lane='general' AND simulation_job_id IS NOT NULL AND nowcast_run_id IS NULL)
 OR (lane='nowcast' AND simulation_job_id IS NULL AND nowcast_run_id IS NOT NULL)))
```

空闲分支现在同时约束 lease/heartbeat 为 NULL；忙分支强制 lease/heartbeat 非空且 `lease_until>heartbeat_at`；`:728` 进一步固定初始化两行 seed 全 NULL、claim 原子写入、heartbeat 10 s 刷新、cancelling 原子释放谓词（`slot_id + busy=true + worker_id + 当前 job/run 绑定`）、terminal 后全空断言、timeout/crash recovery 只领 `busy=true AND lease_until<=clock_timestamp()`。上一轮的 `busy=false + 三个绑定 NULL + lease/heartbeat 非 NULL` 反例已被 CHECK 排除。

### §3.2 durable job 状态机三套描述 — **整改保持（PASS，静态）**

`PRD_SUPPLEMENT.md:166` 明确“本节不重复维护任何转换行……唯一规范状态集、全部转换行及 nowcast/general 守卫是 `IMPLEMENTATION_CONTRACT.md` §6.1 的单一转换表”。本轮断言：

```text
IC 状态转换表头出现次数: 1
PRD 状态转换表头出现次数: 0
四表 status 枚举（497/671/893/1024）完全一致
```

`IMPLEMENTATION_CONTRACT.md:1350-1373` 是唯一转换表，`:1365-1367` 明确 `retry_wait→timed_out` 只属 nowcast、所有 `→timed_out` 原子写 `error_code=SIMULATION_TIMEOUT`、`→failed` 的 error_code MUST NOT 为 `SIMULATION_TIMEOUT`。上一轮的多套互不一致描述已消除；本轮新问题见 C-01/C-02，不改变本项判定。

### §3.3 dependency-config 授权路径多套集合 — **整改保持（文档定义 PASS，执行 NOT RUN）**

唯一阶段注册表仍是 `IMPLEMENTATION_CONTRACT.md:2132-2146` 的 `DEP-AUTH-01..13`（13 行、ID 连续、prep/landing flags 唯一）。`PRD_SUPPLEMENT.md:26`、`IMPLEMENTATION_CONTRACT.md:2130,2148-2153,2353-2359,2508` 与 `implementation/dependency-contract.md:11-15,82` 全部只引用该注册表；`implementation/dependency-contract.md:82` 明确“是否允许 dependency-config 在 R0 前准备某路径，一律只查 §15.0”。`--phase` 唯一词汇 `current|post-handoff` 在 `:2150-2152` 与 G0 `:2404` 一致。授权歧义已消除。handoff 本身不存在（§0.3），故 handoff/G0 只能 NOT RUN。

### §3.4 实施摘要中的过期 owner 断言 — **整改保持（PASS）**

`implementation/dependency-contract.md:11-15,82` 只引用 `DEP-AUTH-06` 与 `DEP-AUTH-01..13`，不再声称 dependency owner 只拥有 requirements/pyproject，也不再把 `config.py` 交给 integration。`IMPLEMENTATION_CONTRACT.md:2153` 仍把旧断言标为 stale evidence，并在“后续文本重新出现这些断言或另列授权子集”同样按 stale evidence 阻断 handoff。上一轮的失效行号引用不再出现。

### §3.5 handoff 保留谓词不可判定 — **整改保持（定义 PASS，实际 checker NOT RUN）**

`IMPLEMENTATION_CONTRACT.md:2354-2359` 仍写死精确 Node/npm 版本、`.python-version`、`.node-version`、`.npmrc`、`packageManager`、`overrides.rollup`、唯一 `test:perf:twin`、lockfileVersion/root metadata、`DEP-AUTH-01..13`、Settings 字段注册表及 invalid cases。本轮读取工作树事实：

```text
.python-version = "3.11\n"
frontend/.node-version = "18.20.8\n"
frontend/.npmrc 含 engine-strict=true、registry、package-lock=true、lockfile-version=3、save-exact=true
frontend/package.json engines = {"node":"18.20.8","npm":"10.8.2"}；packageManager = "npm@10.8.2"；overrides.rollup = "4.22.0"
frontend/package.json scripts = [dev, build, preview, lint, format, format:check, lint:fix, test, test:watch, test:coverage]  <-- 无 test:perf:twin
frontend/package-lock.json lockfileVersion = 3；packages[""].engines = {"node":"18.20.8","npm":"10.8.2"}
```

区间/精确值歧义已消除；但事实仍不满足谓词（无 `test:perf:twin`、无 handoff artifact、checker 不存在），且 C-03 表明 Settings 字段集合谓词按字面不可满足。故本项只能判“定义整改 PASS / handoff 保留检查 NOT RUN”，并且 C-03 使其定义本身存在缺陷。

### §3.6 `DOC-OWNERSHIP` 空集标记 — **整改保持（PASS）**

`IMPLEMENTATION_CONTRACT.md:2307` 仍规定无前置固定写 `—`（解析为空集），`:2313` 的 `DOC-OWNERSHIP` 行使用该标记。本轮建图按空集解析，26 节点、0 unknown、0 cycle。上一轮 LOW 已消除。

### §3.7 “三类 hash”与 target-manifest hash 措辞 — **整改保持（措辞 PASS）**

`PRD_SUPPLEMENT.md:26,32` 仍明确“三类独立 hash（revision/initial-state/snapshot）之外，request fingerprint 另绑定 target-manifest hash”；`IMPLEMENTATION_CONTRACT.md:700` 的 `request_fingerprint` 列表与 `:356,439,2506` 细节一致。措辞 LOW 已消除；manifest 表唯一约束问题另见 C-01 的相邻上下文（该问题已在 §3.3 之外单独复核为已消除，见 §2 B-03 复核段）。

### §3.8 `--phase` 词汇表未定义 — **整改保持（定义 PASS）**

`IMPLEMENTATION_CONTRACT.md:2150` 仍规定唯一词汇 `current|post-handoff`，`:2151-2152` 定义两阶段 owner/prep/landing 判定，`:2152` 与 `:2404` 明确 G0 固定使用 `post-handoff`。官方 checker 仍缺失，实际 phase 校验未运行。

## 3. 上一轮 B-01～B-07 七个阻断项逐项复核

### B-01 `worker_slots` 空闲 CHECK 缺 lease/heartbeat — **已消除（PASS，静态）**

见 §2 §3.1。当前 CHECK 两分支完整（`:716-724`），空闲残留 lease/heartbeat 的反例被排除。

### B-02 `idempotency_record_id` 无可引用主键 — **已消除（PASS，静态）**

`IMPLEMENTATION_CONTRACT.md:1193-1213` 的 `hydraulic.idempotency_records` 现在有 `id UUID PK`（`:1196`）；`network_jobs.idempotency_record_id UUID NOT NULL FK -> idempotency_records.id RESTRICT`（`:506`）与 `forecast_alert_transitions.idempotency_record_id UUID NULL FK -> idempotency_records.id RESTRICT`（`:1147`）都指向该 `id`；`:1217` 明确“两列均以 UUID FK 指向本表 `id`，不得以未声明的复合键或自由 UUID 代替”，并要求同事务验证 scope/key/user/fingerprint/resource_id 一致。上一轮“UUID 无对应表/列”的引用完整性断裂已消除。

### B-03 target manifest 唯一约束允许多 hash — **已消除（PASS，静态）**

`IMPLEMENTATION_CONTRACT.md:455` 现在是 `UNIQUE(network_revision_id,schema_version)`，旧的 `UNIQUE(network_revision_id,schema_version,content_sha256)` 已不存在；`:460` 明确该约束“使同一 revision/schema 只能存在一个 `content_sha256`”，`:456-457` 提供 `UNIQUE(id,content_sha256)` 与 `UNIQUE(network_revision_id,id,content_sha256)` 供下游复合 FK；`:462` 要求 simulation/attempt/frame/nowcast/alert 五列复合 FK 绑定同一行。`PRD_SUPPLEMENT.md:267` 同步。上一轮“一 revision/schema 多 hash”已消除。

### B-04 `SIMULATION_TIMEOUT` 终态自相矛盾 — **已消除（PASS，静态）**

`PRD_SUPPLEMENT.md:373` 任务错误表 `SIMULATION_TIMEOUT → timed_out`；`:545` 的 `simulation failed` 行不再含 `SIMULATION_TIMEOUT`；`:552-553` 明确 `simulation/calibration/network-job timed_out` 与 `nowcast timed_out` 都用 `SIMULATION_TIMEOUT` + status=`timed_out`，且 `:552` 写明“MUST NOT 出现在 failed 行”；`:538` 明确 504 只用于同步等待接口。`IMPLEMENTATION_CONTRACT.md:1367` 明确所有 `→timed_out` 原子写 `error_code=SIMULATION_TIMEOUT`、`→failed` MUST NOT 为该码。同一 code 不再同时映射到两个终态。

### B-05 G3 遗漏 calibration durable job — **已消除（PASS，静态）**

`PRD_SUPPLEMENT.md:615` 的 G3 定义与 `IMPLEMENTATION_CONTRACT.md:2407` 的 G3 定义现在都写“四类 durable job（simulation、network job、nowcast、calibration）”，并要求 calibration 另验 dataset/split 固定、metrics+独立 report artifact 原子 finalize、retry/failed/timed_out fixture。fixture registry `:2397` 已含 `backend/tests/fixtures/hydraulic/calibration/durable-lifecycle-v1.json`，`:926` 规定其必交内容。`PRD_SUPPLEMENT.md:588` 的 AC-5 行也引用 G3 calibration durable lifecycle fixture。上一轮“三类 job”遗漏已消除。

### B-06 ASCII blockedBy 图不完整 — **已消除（PASS，静态）**

`IMPLEMENTATION_CONTRACT.md:2309` 现在明确“本文不再维护第二份 ASCII/Mermaid 依赖图；下表是 blockedBy 的唯一规范图示，checker 必须从表中 26 个 work-item ID 重建有向图”，并禁止 `domain`/`integration` 等集合别名与自然语言占位符。`:2311-2338` 的表恰含 26 个唯一 work-item ID，blockedBy 列只使用这些 ID 或 `—`（`DOC-OWNERSHIP`）。本轮建图：26 节点、0 unknown、0 cycle。上一轮的缺 3 节点与非 ID 别名已消除。

### B-07 G6 只显式传一个 profile — **已消除（PASS，静态）**

`IMPLEMENTATION_CONTRACT.md:2385-2386` 与 `PRD_SUPPLEMENT.md:607-608` 现在各有两条独立命令：

```text
python scripts/verify_hydraulic_stack.py --gate g6 --profile postgis  --evidence-dir "artifacts/hydraulic/${RUN_ID}/g6/postgis"
python scripts/verify_hydraulic_stack.py --gate g6 --profile portable --evidence-dir "artifacts/hydraulic/${RUN_ID}/g6/portable"
```

`IMPLEMENTATION_CONTRACT.md:2410` 与 `PRD_SUPPLEMENT.md:618` 明确“两 profile 各自独立命令/evidence 目录；每个命令只负责其显式 profile，不得递归或隐式代替另一 profile；acceptance 需两个 artifact 同 RUN_ID/commit 且均 exit 0”。上一轮的入口语义不闭合已消除。

## 4. 上一轮 §4 全部 PASS 项重验

| 上一轮 §4 项目 | 本轮结果 | 证据与边界 |
|---|---|---|
| frame registration / hypertable 唯一性 | **PASS（静态）** | `IMPLEMENTATION_CONTRACT.md:753-764` registration PK `(attempt_id,frame_index)` + `UNIQUE(attempt_id,frame_time)`；`:768-792` hypertable PK `(frame_time,attempt_id,frame_index)`；`:766,794` 同事务且 MUST NOT 再声明 `UNIQUE(attempt_id,frame_index)`；`:797` finalize 原子发布。未运行 migration。 |
| `next_attempt_at` 持久化/due claim | **部分 PASS（定义层 DDL 有缺口，见 C-02）** | 四表字段与统一 prose CHECK 存在（`:1370`）、DB due claim 与索引文字一致（`:532,533,1073,173`）；但 §3 四张表 DDL 全部缺该 CHECK（C-02）。 |
| 三类独立 hash + target-manifest hash | **PASS（静态）** | `IMPLEMENTATION_CONTRACT.md:700` request_fingerprint 含四类 hash；`:455-462` manifest 唯一约束与五列复合 FK；`:356,439`。B-03 已消除。 |
| 错误码 / 幂等单一矩阵 | **FAIL** | B-02/B-04 已消除；但 C-01 的 TTL+generation 与永久 UNIQUE 冲突使“单一幂等矩阵”不可实现。`UNSAFE_PATH=422` 仍在 `PRD_SUPPLEMENT.md:534`。 |
| forecast transition / refresh / observe | **PASS（静态）** | PRD `:192-208` 与 IC `:1742-1758` 七个 action（create/acknowledge/observe/refresh/escalate/resolve/expire）status/phase/守卫/事件逐行对齐（措辞等价，refresh “四项任一/至少一项 >=” 等价）；`:1752` refresh 四个 delta/`>=`、`:1753` observe 窗口、`:1755` 终态不可 reopen 已冻结。未运行 forecast worker。 |
| observation trust 四级/policy/quarantine | **PASS（静态）** | PRD `:111-115`；IC `:804-817` policy 表 + exclusion constraint、`:821-855` ingest event 的 policy/auth/binding/trust 解析、`:853` `quarantined` 只作 status、`:855` `external_verified+auth+binding+quality good` 才可正式同化/observed/AC-5。未运行真实 ingest。 |
| 工件 fail-closed | **PASS（静态）** | PRD `:214,388-389`；IC `:987` 调度前缺失→不创建 run+`FORECAST_POLICY_UNAVAILABLE`+not_ready、`:988,1693,1768` 运行中失效→terminal failed `FORECAST_POLICY_INVALID`，均不产生 horizon/alert/outbox。未运行 policy gate。 |
| reserved-slot 禁借规则 | **PASS（静态）** | PRD `:183-185` 禁借 + 固定 Literal；IC `:707-728` slot CHECK 两分支完整，`:728` “nowcast 槽即使空闲也不可借给 general”，`:1969` `NOWCAST_RESERVED_SLOT_BORROWING=false`、`:1995` Settings 强制 false。B-01 已消除。 |
| blockedBy 无环 | **PASS（表结构，26 节点 0 unknown 0 cycle）** | IC `:2309` 唯一图示条款；`:2311-2338` 表；本轮建图 26 节点无环无 unknown。B-06 已消除。 |
| owner 表结构/工作树覆盖 | **PASS（静态）** | 119 rows、119 unique patterns、0 duplicate；46 candidates 全部恰一命中，0 unmatched/ambiguous。官方 checker 不存在，故不是 checker PASS。 |
| R0 artifact 绑定 bundle | **定义 PASS，artifact NOT RUN** | IC `:2348-2350` base+bundle+逐文件 hash 绑定清楚；本轮重算 bundle `e49df829…`；`artifacts/contracts/v3-independent-review.json` 不存在（C-04 亦使自述 round-2 bundle 失效）。 |
| gate/harness 定义可执行性 | **定义 PASS（覆盖已闭合），执行 NOT RUN** | G0～G6 定义（含 calibration 与双 profile）现已完整（见 B-05/B-07）；但 7 个入口脚本与全部 fixture 缺失（§0.3），G0～G6/AC 全部 NOT RUN。C-03 使 handoff 保留谓词定义存在缺陷。 |
| R0 PASS 前 migration 零变更 | **PASS（本轮实测）** | `git status --porcelain -- backend/alembic/versions` 空输出；这只证明当前工作树无 migration 改动，不证明未来 gate 已通过。 |

补充说明：上一轮 §4 “三类独立 hash”一行标为“部分 PASS”（因 B-03），本轮 B-03 已消除，该行升级为 PASS（静态）。上一轮 §4 “错误码/幂等”一行标为 FAIL，本轮仍为 FAIL（原因换成 C-01）。

## 5. 新发现：必须整改的证据

### C-01 [BLOCKER] 24h TTL + generation 幂等语义与 `simulations`/`calibration_runs` 永久 `UNIQUE(requested_by, idempotency_key)` 冲突

**证据**

- `PRD_SUPPLEMENT.md:235`（D-12）冻结幂等复用窗口 24h：“`expires_at=created_at+24h`；窗口内同 scope+key+user+同 request hash 返回首次 status/body/resource，不同 hash 返回 409。过期记录先原子标记 retired，再以递增 generation 创建新记录；……retired 记录用于 FK/audit，不与新 generation 混用。”即：24h 后同 key 应当可以创建新 generation 的新资源。
- `PRD_SUPPLEMENT.md:241`（矩阵）`| simulation submit/cancel | key | 返回原 simulation/首次 cancel response |`；`:246` `| calibration submit | key | 返回原 calibration_run |`。这两行的“返回原资源”语义在 24h 窗口内成立，但窗口外必须允许新 generation。
- `IMPLEMENTATION_CONTRACT.md:1215` 与之一致：“存在且 `expires_at>clock_timestamp()` 时……返回首次；已过期则先置 `retired_at=expires_at`，再以 `generation+1` 插入新 request。并发请求不得生成两个 active generation。”`idempotency_records` 的四列 unique（`:1208`）与 active partial unique（`:1209`）本身正确支持多 generation。
- **冲突点**：`IMPLEMENTATION_CONTRACT.md:670,691` 的 `simulations` 与 `:906,909` 的 `calibration_runs` 都声明 `idempotency_key VARCHAR(128) NOT NULL` + `UNIQUE(requested_by, idempotency_key)`（该 UNIQUE 出现 2 次）。这是一个**永久、不含 generation/active 条件**的唯一键：一旦某 user 用 key K 创建过 simulation/calibration_run，24h 后即使 `idempotency_records` 已 retire 并允许 `generation+1`，插入带同一 `(requested_by, K)` 的新资源行仍会违反该永久 UNIQUE。因此 24h 过期后的“新 generation 资源”在数据库层无法创建。
- 对比：`network_jobs` 用 `idempotency_record_id UUID FK -> idempotency_records.id`（`:506`）而非 `(created_by, key)` 永久 UNIQUE，且 `:530` 明确“默认 TTL 24h”，与 generation 语义自洽。`simulations`/`calibration_runs` 缺 `idempotency_record_id` FK，且 `§17.0` 的 FK 保留谓词 `:2358-2359` 只覆盖 `DEP-AUTH-06` Settings 与 `network_jobs`/`forecast_alert_transitions` 的 idempotency_record_id，不覆盖 simulations/calibration_runs 的 key→resource 关系。

**影响**

C-01 落在批准条件“错误码/幂等单一矩阵”之下：PRD 承诺的 24h 窗口与 generation 递增是幂等语义的唯一来源，但 `simulations`/`calibration_runs` 的表级永久唯一键使该语义在提交后 24h 之后不可实现。两种实现都违反规范：实现 generation（允许过期后新资源）→ 违反永久 UNIQUE；实现永久 UNIQUE（不允许过期后新资源）→ 违反 24h TTL + generation 递增的冻结语义。这与“同 key 同 fingerprint 返回首次、不同 fingerprint 409、过期 retire + 新 generation”这一单一幂等矩阵的其它行（network_jobs）行为不一致，同一 `Idempotency-Key` 在 simulation 与 import 端点会有不同的过期行为。

**整改（二选一，必须显式冻结）**

1. 让 `simulations` 与 `calibration_runs` 采用与 `network_jobs` 相同的 surrogate 引用：为两表增加 `idempotency_record_id UUID NOT NULL FK -> idempotency_records.id`，并把永久 `UNIQUE(requested_by, idempotency_key)` 移除或改为按 `idempotency_record_id` 的唯一；这样 24h 后新 generation 通过新的 record 引用创建新资源，与 `idempotency_records` 的 active partial unique 一致。
2. 明确声明该 key 对 simulation/calibration 是“永久一次性”（key 终身只映射一个资源，永不因 24h 过期而允许新 generation），并把 D-12 矩阵中这两行的过期语义改写为“窗口外同 key 仍返回原资源/409，generation 递增不适用于这两类”，同时从 `§17.0` 保留谓词与 `§18` 阻断项中移除或限定 generation 适用范围。

无论选哪条，都必须在 `PRD_SUPPLEMENT.md` D-12 矩阵、`IMPLEMENTATION_CONTRACT.md` §3.6/§3.7 表定义、`:1215-1217` 说明和 `:2358-2359` 保留谓词之间保持一致，并重新计算三文档 bundle。

### C-02 [HIGH] §6.1 强制的 `next_attempt_at` CHECK 在 §3 四张 durable job 表 DDL 中全部缺失

**证据**

- `IMPLEMENTATION_CONTRACT.md:1370`（§6.1 状态机规则）：“simulation、network_jobs、nowcast_runs、calibration_runs 都 **MUST** 有 `CHECK ((status='retry_wait') = (next_attempt_at IS NOT NULL))`；非 retry 状态的 `next_attempt_at` 必须为 NULL。”
- 本轮按 §16/§3 表块边界逐块提取四张表的 DDL 并检索该 CHECK：

```text
network_jobs     (489-527): DDL contains mandated CHECK -> False
simulations      (641-704): DDL contains mandated CHECK -> False
nowcast_runs    (1009-1074): DDL contains mandated CHECK -> False
calibration_runs  (882-912): DDL contains mandated CHECK -> False
```

四张表的 DDL 只声明了 `status`、`progress`、`next_attempt_at`、`retry_backoff_seconds`、`last_retry_error_code` 等列，没有把 `status='retry_wait'` 与 `next_attempt_at IS NOT NULL` 的等价性写成 CHECK。`simulations` 只有 `CHECK progress`（`:695`）与 `CHECK attempt`（`:696`）；`nowcast_runs` 只有 `CHECK attempt`（`:1065`）；`calibration_runs` 只有 `progress` 内联 CHECK（`:894`）；`network_jobs` 无该等价 CHECK。

**影响**

C-02 与 B-01 是同一类缺陷的新实例：§6.1 用 prose 声明了一个不变量，但没有把它下沉到 §3 冻结 DDL。上一轮已因 `worker_slots` 的 prose-vs-DDL 缺口开出 B-01 并要求“把不变量下沉为明确约束”；`next_attempt_at` 的 retry_wait 等价约束在四张表上同样只停留在 prose。后果是：一张严格按 §3 DDL 实现的 migration 不会创建该 CHECK，`retry_wait` 行可以没有 `next_attempt_at`（claim 永远取不到），或非 retry 行残留 `next_attempt_at`（DB due claim 谓词依赖该字段清空）。这削弱了 §4 “`next_attempt_at` 持久化/due claim” 的 DB 级保证。

**整改**

在 §3.6 `network_jobs`、§3.6 `simulations`、§3.7 `calibration_runs`、§3.8 `nowcast_runs` 四张表的 DDL 中各自显式加入 `CHECK ((status='retry_wait') = (next_attempt_at IS NOT NULL))`（可再配合 `retry_backoff_seconds`/`last_retry_error_code` 的 retry_wait 联合 CHECK），使 §6.1 的 MUST 在 DDL 层可验证；随后更新 `:1370` 的措辞与 `:2545-2546` 的静态断言口径。

### C-03 [MEDIUM] §17.0 保留谓词要求 Settings 字段“与第 12 节 registry 完全相等”，但 §12 registry 只覆盖 54/101 字段

**证据**

- `IMPLEMENTATION_CONTRACT.md:2358`（§17.0 保留谓词第 4 条）:“`backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json` 的 `settings.fields[]` 是 `DEP-AUTH-06` 字段注册表：字段名集合必须与**第 12 节 v3 Settings registry 完全相等**；每项的 canonical `type/constraints/validators` 必须与 `Settings.model_fields` 及字段/模型 validator 相等……legacy 非授权字段可保留，但不得被 handoff 声称为本轮授权。”
- `:1938-1993`（§12）列出的 Settings 变量共 54 项（`HYDRAULIC_SPATIAL_BACKEND` … `NOTIFICATION_MAX_ATTEMPTS`），其中**不含** `DEPLOYMENT_ENVIRONMENT`、`JWT_ALGORITHM`、`JWT_ALGORITHMS` 的算法选择相关补充字段中的 `JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY`/`JWT_SECRET_KEY`（`:1983` 只有 `JWT_ALGORITHMS=RS256`、`JWT_ISSUER`、`JWT_AUDIENCE`），也不含 `FORECAST_POLICY_CONFIG_PATH` 之外的 legacy/安全/部署字段。
- 本轮读取 `backend/app/core/config.py` 与 `.env.example` 静态核对：

```text
§12 registry 变量数: 54
config.py 字段数: 101
.env.example 变量数: 101
§12 变量全部存在于 .env.example: True
§12 变量全部存在于 config.py 字段: True
config.py 字段不在 §12 registry 的有 47 个（含 DEPLOYMENT_ENVIRONMENT, JWT_ALGORITHM, JWT_PRIVATE_KEY, JWT_PUBLIC_KEY, JWT_SECRET_KEY, DATABASE_URL, REDIS_URL, INFLUXDB_*, SMTP_*, CORS_ORIGINS, APP_NAME 等）
§12 registry contains JWT_PRIVATE_KEY: False ; contains JWT_SECRET_KEY: False ; contains DEPLOYMENT_ENVIRONMENT: False
```

**影响**

`:2358` 同时要求“字段名集合与 §12 registry 完全相等”与“legacy 非授权字段可保留”。若按字面执行（fixture `settings.fields[]` 只列 §12 的 54 项并要求“完全相等”），checker 会因 `Settings.model_fields` 有 101 项而失败；若 fixture 列全部 101 项又违反“与 §12 完全相等”。这条保留谓词按当前 §12 registry 定义不可同时满足，属于 handoff 保留谓词的定义层矛盾（C-03）。它直接影响 §3.5 的 handoff 保留判定和 §18 阻断项 35（`:2508`）的 integration handoff 保留断言。

**整改**

二选一并同步 §12、§17.0、§18：

1. 把 §17.0 第 4 条改为“fixture `settings.fields[]` 覆盖 §12 registry 的 54 项（授权子集），并在同一 fixture 中显式列出 47 个 legacy/安全/部署字段的 `authorized=false` 标记，checker 断言授权子集与 §12 完全相等、非授权字段存在但不被声称为本轮授权”，即把“完全相等”限定到授权子集。
2. 或把 §12 扩为完整的 101 字段 Settings registry（把 legacy/安全/部署字段也纳入 §12 并标注 `authorized` 范围），使“完全相等”按字面可满足。

同时在 `§17.0` 明确 checker 对 `Settings.model_fields` 的比较是“授权字段逐项相等 + 非授权字段存在性”，避免再出现“完全相等”与“可保留 legacy”并列的歧义。

### C-04 [LOW] `PRD_SUPPLEMENT.md:37` / `IMPLEMENTATION_CONTRACT.md:2351` 自述的 round-2 被审 hash/bundle 与本轮实际文件不符，证据链自指失效

**证据**

- `PRD_SUPPLEMENT.md:37` 与 `IMPLEMENTATION_CONTRACT.md:2351` 声称被审 base commit=`ca99e0485fb12fbecbd565e79d81f745f8d4a748`、document bundle=`bd451b6008d429db0282f28567060bed372aa3738fe1b130fc9e83b11335491f`、被审输入 SOURCE=`11e56e68…`、PRD=`fae14e1d…`、IMPLEMENTATION=`115cc130…`。
- 本轮实际 `sha256sum`（§0.1）：SOURCE=`11e56e68…`（一致）、PRD=`33c7c6e7…`、IMPLEMENTATION=`1aae4cc8…`；按 §17.0 canonical 定义重算 bundle=`e49df8297af5f8f4829aa423490eeb1cd4c6956e9a2f37e551bd9ce062b7f815`。
- PRD=`fae14e1d…`、IMPLEMENTATION=`115cc130…`、bundle=`bd451b60…` 三个值与本轮工作树中任何文件都不匹配；这三个 hash 对应的历史文件内容已不存在于工作树（`docs/` 未跟踪、无法回溯）。round-2 复审结果“通过工作流返回但未落盘”，因此这三值没有可核验的源文件。

**影响**

这是证据可追溯性问题，不是 schema/semantics 矛盾，因此列为 LOW。但它使 `PRD_SUPPLEMENT.md:0.3` 与 `IMPLEMENTATION_CONTRACT.md:17.0` 关于“最新 round-2 独立复审结果”的陈述在当前工作树下无法被第三方独立复核（无法重算得到 `bd451b60…`）。这不构成批准阻断，但下一次 R0 PASS artifact 必须绑定本轮实际 bundle（`e49df829…`）并使其可由 checker 重算。

**整改**

在下一轮 R0 JSON artifact 中以本轮实际重算的 bundle 与三文件 hash 为准，并在 `PRD_SUPPLEMENT.md:0.3` / `IMPLEMENTATION_CONTRACT.md:17.0` 更新被审输入 hash（或删除对已不存在的 round-2 hash 的引用，改为只保留可重算的当前 bundle），避免自述证据指向不可回溯的历史字节。

## 6. 最新 dependency remediation/review 文档的处理

本轮读取：

- `implementation/dependency-contract.md`：`:11-15,82-86` 只引用 `DEP-AUTH` 注册表并明确未交付 checker/harness/perf script；这与本轮 C-01/C-02/C-03 的“实现尚未交付 + 定义仍有缺口”边界一致。其 `:81` 对 round-2 “B-01～B-07 已消除、N-01～N-07” 的陈述已被本轮独立复核在静态层确认（B-01～B-07 全部消除），本轮新增发现 C-01～C-04 是 round-2 未覆盖的新问题。
- `implementation/hydraulic-schema.md`：状态 `BLOCKED — implementation not started`（`:2-4`），`git status` 输出（`:23-55`）与本轮实测一致（`docs/` 下 3 个主文档 + 5 contracts + 8 recon + 2 implementation + 2 reviews = 20 个 `docs/hydraulic-twin/**` 未跟踪文件），未声称 migration 已实施。其引用的 `IMPLEMENTATION_CONTRACT.md:2074-2077` 行号在当前文件中指向 EPA/Windows 与 Secret/端口段（N-01 整改后行号已变），属过期行号引用，但不构成 owner/授权矛盾。
- `reviews/dependency-contract.md`：较早的 dependency 独立审查，4 HIGH / 5 MEDIUM，`approved=false`（`:5,340`）。本轮未把其历史定向测试当证据，也未重跑其测试套件。本轮只读核对了它曾指出的几项在当前实现面的现状：`config.py` 已加 `JWT_PRIVATE_KEY/JWT_PUBLIC_KEY`（`:446-469` 的 production RS256 校验）、`auth_fallback_admin_allowed` 已限制 `development|test`（`:491-502`）、`probe_epa_swmm` 已改为每次重新计算 sha256 并无永久缓存（`:511-554`）；这些是实现层观察，不改变 R0 verdict。
- `reviews/v3-schema-semantics-review.md` 与 `reviews/v3-schema-semantics-review-round-1.md`：本轮读取确认两文件内容完全一致（均为第 1 轮整改后复审，结论 `NOT_APPROVED / R0_FAIL`，B-01～B-07 阻断）。它们是 round-1 历史证据；本报告为 round-3 结论，将同步覆盖 `v3-schema-semantics-review.md`。

## 7. 阶段 D / 阶段 V 边界与解除条件

`PRD_SUPPLEMENT.md:43-47`、`IMPLEMENTATION_CONTRACT.md:38-40,2346,2413` 正确区分了设计审查和实现验收：缺失入口只能 NOT RUN，不能被写成 PASS；设计审查也不能代替 G0～G6。当前状态应保持：

```text
R0_ROUND_3_FAILED = FAIL
NOT_APPROVED
ALL_IMPLEMENTATION_WORK_BLOCKED
```

解除本轮设计阻断的最小条件（按优先级）：

1. **C-01（BLOCKER）**：在 `simulations`/`calibration_runs` 上二选一——引入 `idempotency_record_id` FK + 移除/条件化永久 `UNIQUE(requested_by, idempotency_key)`（与 network_jobs 对齐，支持 24h generation），或明确声明 simulation/calibration 的 key 永久一次性并改写 D-12 矩阵与 §17.0/§18 的 generation 适用范围。三文档保持一致后重算 bundle。
2. **C-02（HIGH）**：在 §3 四张 durable job 表 DDL 中显式加入 `CHECK ((status='retry_wait') = (next_attempt_at IS NOT NULL))`（必要时连同 backoff/error 联合 CHECK），使 §6.1 的 MUST 在 DDL 可验证。
3. **C-03（MEDIUM）**：把 §17.0 第 4 条“Settings 字段名集合与第 12 节 registry 完全相等”改为对授权子集成立（并显式标注 legacy 字段 `authorized=false`），或把 §12 扩为完整 Settings registry，使谓词按字面可满足。
4. **C-04（LOW）**：在下一轮 R0 JSON 中以本轮实际 bundle（`e49df829…`）与三文件 hash 为准，并更新或删除 `PRD_SUPPLEMENT.md:0.3` / `IMPLEMENTATION_CONTRACT.md:17.0` 中指向不可回溯 round-2 hash 的自述。
5. 重新计算三文档 bundle，并由新的独立复审重新给出 R0 verdict；之后才可生成正式 JSON artifact、进入 handoff/landing，再在同一 RUN_ID/commit 实际执行 G0～G6 和 AC-1～AC-8。

## 8. 最终判定

**verdict = FAIL / NOT_APPROVED。**

本轮确认上一轮 §3.1～§3.8 与 B-01～B-07 的整改在静态层全部真实消除，且上一轮 §4 的 PASS 项（frame 唯一性、hash 分离、target-manifest 唯一约束、forecast transition、observation trust、工件 fail-closed、reserved-slot 禁借、blockedBy 无环、owner 表静态覆盖、migration 零变更）在当前文档中保持成立。但新发现 C-01（幂等 TTL+generation 与永久唯一键冲突，落在“错误码/幂等单一矩阵”批准条件之下）、C-02（四张 durable job 表 DDL 缺 §6.1 强制的 retry_wait/next_attempt CHECK）、C-03（§17.0 Settings 字段“完全相等”谓词不可满足）三项规范内部矛盾，使 schema/semantics、owner、授权执行边界不满足“无矛盾”批准条件；C-04 为 LOW 证据问题。同时 G0～G6/AC-1～AC-8 入口实际执行全部缺失（NOT RUN），不得记为实现通过。

本轮没有修改任何生产代码、migration 或三份主文档；只创建本报告并同步覆盖 `reviews/v3-schema-semantics-review.md`。
