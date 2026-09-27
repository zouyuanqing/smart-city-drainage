# v3 Schema / Semantics 独立复审（第 1 轮，整改后复审）

- **复审者**：`dynamic-workflow v3-schema-semantics-review-1`（独立只读复审；不是文档作者或实现者）
- **复审日期**：2026-09-24
- **复审对象**：`SOURCE_PRD.md`、`PRD_SUPPLEMENT.md`、`IMPLEMENTATION_CONTRACT.md`，以及最新 `implementation/dependency-contract.md`、`reviews/dependency-contract.md`、上一轮 `reviews/v3-schema-semantics-review.md`
- **基线 commit**：`ca99e0485fb12fbecbd565e79d81f745f8d4a748`（短名 `ca99e048`）
- **结论**：**不批准（`NOT_APPROVED` / `R0_FAIL`）**。上一轮 §3.1～§3.8 的原始结构性整改大部分已经写入当前文档，但本轮独立复核仍发现 6 个会阻止设计冻结的 schema/semantics 或依赖图矛盾；不能把静态整改断言当成 R0 PASS。

> 证据边界：本轮只读代码、三份主文档和 remediation/review 文档；没有修改生产代码、migration 或三份主文档。本报告文件本身是用户要求的审查输出。除报告文件外，没有创建实现、gate、fixture、handoff 或运行 artifact。

## 0. 输入、版本与本轮实际执行

### 0.1 文档内容哈希

执行命令：

```text
sha256sum docs/hydraulic-twin/SOURCE_PRD.md docs/hydraulic-twin/PRD_SUPPLEMENT.md docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md
```

输出：

```text
11e56e68a68017b84b5ae577dede8a74f6217d4a7877f5e358a18f14e66807ee  docs/hydraulic-twin/SOURCE_PRD.md
b227ff99a50533efd3fdf08800e4773fac58fb79e06be0eacb33b4eb84b5f48f  docs/hydraulic-twin/PRD_SUPPLEMENT.md
0f70e83a6638acf15dfa2127cff5e0ce80f2be6a6cca230899bd09871bd2ea6d  docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md
```

按 `IMPLEMENTATION_CONTRACT.md:2268-2269` 的 canonical bundle 定义执行只读计算，输出：

```text
base_commit = ca99e0485fb12fbecbd565e79d81f745f8d4a748
document_bundle_sha256 = 8ce47dc3285f0154a8df41484f0b332502e0ba4eb923f8b01a98470dab37f2b5
```

当前 bundle 与上一轮报告记录的 `be4cf0e4…` 不同，说明整改确实改变了 PRD/IC 内容；旧 verdict 不能复用于当前输入。`SOURCE_PRD.md:2-11` 仍声明其为不可变需求归档，本轮没有改写其正文。

### 0.2 仓库状态

执行命令及输出：

```text
$ git -C <repo> rev-parse --short=8 HEAD
ca99e048
$ git -C <repo> rev-parse HEAD
ca99e0485fb12fbecbd565e79d81f745f8d4a748
$ git -C <repo> status --porcelain --untracked-files=all | wc -l
45
$ git -C <repo> status --porcelain -- backend/alembic/versions
(no output)
$ git -C <repo> ls-tree -r --name-only HEAD -- docs
(no output)
```

`SOURCE_PRD.md` 位于未跟踪的 `docs/` 树中，因此 Git 不能用 tracked diff 证明其来源；本轮只能以 SHA-256 与上一轮记录一致（`11e56e68…`）证明未观察到内容漂移。migration 目录本轮仍无工作树变化。

### 0.3 契约点名的 gate/checker/harness 命令

本轮按当前文档 `IMPLEMENTATION_CONTRACT.md:2280-2305` 从仓库根执行了 G0、G1（两 profile）、G2、G3、G4、G5、G6、acceptance，以及三个 checker、三个 benchmark harness 和 frontend perf script。统一 `RUN_ID` 为：

```text
audit-20260924T110726Z-ca99e048
```

代表性完整输出：

```text
$ python scripts/verify_hydraulic_stack.py --gate g0 --evidence-dir "artifacts/hydraulic/${RUN_ID}/g0"
C:\Python314\python.exe: can't open file '...\\scripts\\verify_hydraulic_stack.py': [Errno 2] No such file or directory
exit=2

$ python scripts/verify_hydraulic_stack.py --gate g1 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g1/postgis"
... can't open file '...\\scripts\\verify_hydraulic_stack.py'
exit=2

$ python scripts/verify_hydraulic_stack.py --gate g1 --profile portable --evidence-dir "artifacts/hydraulic/${RUN_ID}/g1/portable"
... can't open file '...\\scripts\\verify_hydraulic_stack.py'
exit=2

$ python scripts/verify_hydraulic_stack.py --gate g2 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g2"
... can't open file '...\\scripts\\verify_hydraulic_stack.py'
exit=2

$ python scripts/verify_hydraulic_stack.py --gate g3 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g3"
... can't open file '...\\scripts\\verify_hydraulic_stack.py'
exit=2

$ python scripts/verify_hydraulic_stack.py --gate g4 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g4"
... can't open file '...\\scripts\\verify_hydraulic_stack.py'
exit=2

$ python scripts/verify_hydraulic_stack.py --gate g5 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g5"
... can't open file '...\\scripts\\verify_hydraulic_stack.py'
exit=2

$ python scripts/verify_hydraulic_stack.py --gate g6 --profile postgis --evidence-dir "artifacts/hydraulic/${RUN_ID}/g6"
... can't open file '...\\scripts\\verify_hydraulic_stack.py'
exit=2

$ python scripts/verify_hydraulic_stack.py --gate acceptance --ac ac1,ac2,ac3,ac4,ac5,ac6,ac7,ac8 --evidence-dir "artifacts/hydraulic/${RUN_ID}/acceptance"
... can't open file '...\\scripts\\verify_hydraulic_stack.py'
exit=2

$ python scripts/check_contract_ownership.py --contract docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md --phase current
... can't open file '...\\scripts\\check_contract_ownership.py'
exit=2

$ python scripts/check_contract_ownership.py --contract docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md --phase post-handoff
... can't open file '...\\scripts\\check_contract_ownership.py'
exit=2

$ python scripts/check_migration_gate.py --review-artifact artifacts/contracts/v3-independent-review.json --base-ref ca99e0485fb12fbecbd565e79d81f745f8d4a748 --versions-dir backend/alembic/versions
... can't open file '...\\scripts\\check_migration_gate.py'
exit=2

$ python scripts/check_dependency_handoff.py --handoff artifacts/contracts/dependency-config-handoff.json --expected backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json --python-version-file .python-version --node-version-file frontend/.node-version --npmrc frontend/.npmrc --package frontend/package.json --lock frontend/package-lock.json --env-example .env.example
... can't open file '...\\scripts\\check_dependency_handoff.py'
exit=2

$ npm --prefix frontend run test:perf:twin -- --fixture frontend/performance/fixtures/twin-map-5k-10k-v1.json --duration-seconds 60 --output "artifacts/hydraulic/${RUN_ID}/g5/frontend.json"
npm error Missing script: "test:perf:twin"
exit=1
```

三个 Python benchmark 入口也分别以 `No such file or directory`、exit 2 失败。当前不存在 `artifacts/contracts/v3-independent-review.json`、`artifacts/contracts/dependency-config-handoff.json`、官方 checker、verify entrypoint、三个 benchmark harness、依赖 handoff fixture 或 frontend perf script。故 G0～G6、AC-1～AC-8 在本轮均为 **NOT RUN/未通过入口检查**，不是 PASS。该边界与 `PRD_SUPPLEMENT.md:611-623,649-672`、`IMPLEMENTATION_CONTRACT.md:2265,2280-2331` 的 fail-closed 声明一致。

## 1. 结论摘要：当前仍阻止批准的事项

| ID | 严重性 | 结论 | 核心证据 |
|---|---|---|---|
| B-01 | BLOCKER | `worker_slots` 的空闲 CHECK 没有约束 lease/heartbeat 清空 | `IMPLEMENTATION_CONTRACT.md:692-713` |
| B-02 | BLOCKER | `idempotency_records` 没有 `id`，但两个业务表引用 `idempotency_record_id UUID` | `IMPLEMENTATION_CONTRACT.md:493-502,1051-1064,1108-1122` |
| B-03 | BLOCKER | target manifest 的唯一约束允许同一 revision/schema 多个不同 hash | `IMPLEMENTATION_CONTRACT.md:443-458` |
| B-04 | BLOCKER | `SIMULATION_TIMEOUT` 同时被定义为 `timed_out` 和 `simulation failed` | `PRD_SUPPLEMENT.md:363-380,540-548`；`IMPLEMENTATION_CONTRACT.md:1262-1268` |
| B-05 | BLOCKER | G3 gate 只覆盖三类 job，遗漏四类状态机中的 calibration run | `IMPLEMENTATION_CONTRACT.md:1251-1273,2320-2329`；`PRD_SUPPLEMENT.md:165-174,603-608` |
| B-06 | BLOCKER | §16 ASCII 依赖图遗漏 3 个 work-item ID，并使用非 ID 的 `domain/integration` | `IMPLEMENTATION_CONTRACT.md:2210-2259` |
| B-07 | HIGH | G6 命令只显式传 `--profile postgis`，文字却要求两 profile，入口语义未闭合 | `IMPLEMENTATION_CONTRACT.md:2295-2305,2320-2329` |

这些问题不是“尚未实现所以未通过”的同义词：即使本轮不运行 G0～G6，B-01～B-06 仍可在设计文档内部复现为约束/状态/依赖图矛盾，违反 `PRD_SUPPLEMENT.md:34-47` 的设计冻结条件。因此不能批准。

## 2. 上一轮 §3.1～§3.8 逐项复核

### §3.1 `worker_slots` CHECK 与初始化/释放矛盾 — **原问题已部分修复，但仍有 BLOCKER**

上一轮指出无条件 lane/job CHECK 会拒绝空闲 seed。当前 `IMPLEMENTATION_CONTRACT.md:704-710` 已增加 `busy=false` 分支，且 `:713` 明确两行 seed、terminal/cancelling 的释放语义；这确实消除了上一轮“初始化必然违反 job/run 绑定 CHECK”的直接矛盾。

但是空闲分支只约束：

```text
busy=false AND worker_id IS NULL
          AND simulation_job_id IS NULL
          AND nowcast_run_id IS NULL
```

并未约束 `lease_until IS NULL`、`heartbeat_at IS NULL`；忙分支也没有强制 `lease_until IS NOT NULL`。因此形如 `busy=false + 三个绑定列为 NULL + lease/heartbeat 非 NULL` 的行仍满足表 CHECK，却违反同一段文字的“worker/job/run/lease/heartbeat 全为 NULL”（`:713`）。本轮紧凑断言实际输出：

```text
worker_slot_busy_false_full_null False
```

这不是运行时测试结果，而是对当前冻结 schema 文本的可执行结构检查；应补全 CHECK 或把释放不变量下沉为明确约束。

### §3.2 durable job 状态机三套描述 — **整改通过（本项 PASS，静态）**

`PRD_SUPPLEMENT.md:165-179` 不再复制转换行，明确把四类 job 的唯一转换表交给 `IMPLEMENTATION_CONTRACT.md §6.1`。当前 IC `:1251-1276` 只有一张转换表，明确包含 `queued→cancelling`、`retry_wait→failed`、`running→failed` 和 nowcast 专属 `retry_wait→timed_out`；四张表的 status 枚举也一致：`network_jobs:493`、`simulations:661`、`nowcast_runs:961`、`calibration_runs:870`。

本轮只读脚本输出：

```text
status_enums: network_jobs/simulations/nowcast_runs/calibration_runs 均 queued|leased|running|retry_wait|cancelling|cancelled|completed|failed|timed_out
ic_transition_rows=11
prd_transition_table_headers=1
ic_transition_table_headers=1
```

这证明上一轮的“多套互不一致转换描述”已消除；但它不掩盖 B-04 的错误码/终态矛盾，也不代表 G3 已覆盖 calibration（见 B-05）。

### §3.3 dependency-config 授权路径多套集合 — **整改通过（文档定义 PASS，执行 NOT RUN）**

当前唯一阶段注册表为 `IMPLEMENTATION_CONTRACT.md:2033-2055` 的 `DEP-AUTH-01..13`；`PRD_SUPPLEMENT.md:26-28` 和 `IMPLEMENTATION_CONTRACT.md:1837-1839,2261-2284` 都引用注册表/ID，没有再维护独立路径集合。最新实施摘要 `implementation/dependency-contract.md:7-15,78-86` 也只引用该注册表，并明确 `DEP-PREP`/`DEP-LANDING` 边界。

本轮静态核对输出：

```text
DEP-AUTH rows=13, ids=DEP-AUTH-01..DEP-AUTH-13, prep_true=12, prep_false=1
owner rows=119, unique path patterns=119
registry/full-cycle-owner mismatches=0
worktree candidates=45, unmatched=0, ambiguous=0
authorized candidate paths=16
```

这消除了上一轮的授权集合歧义。`DEP-AUTH-13` 尚未出现在工作树，符合 prep=false；handoff 本身仍不存在，所以 handoff/G0 只能 NOT RUN。

### §3.4 实施摘要中的过期 owner 断言 — **整改通过（PASS）**

当前 `implementation/dependency-contract.md:11-15` 只引用 `DEP-AUTH-06` 和 `DEP-AUTH-01..13`，不再声称 dependency owner 只拥有 requirements/pyproject，也不再把 `config.py` 交给 integration；`IMPLEMENTATION_CONTRACT.md:2056` 还明确把旧断言标为 stale evidence。上一轮指出的失效 `IMPLEMENTATION_CONTRACT.md:1963-1980` 行号引用不再出现在当前实施摘要中。

### §3.5 handoff 保留谓词不可判定 — **整改通过（定义 PASS，实际 checker NOT RUN）**

当前 `IMPLEMENTATION_CONTRACT.md:2272-2284` 已写死：精确 Node/npm 版本、`.python-version`、`.node-version`、`.npmrc`、`packageManager`、`overrides.rollup`、唯一 `test:perf:twin`、lockfileVersion/root metadata、`DEP-AUTH-01..13`、Settings 字段注册表及 invalid cases。上一轮的“区间还是精确值”歧义已消除。

但当前事实仍不满足该谓词：`frontend/package.json:14-24` 没有 `test:perf:twin`，预期 fixture `backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json` 不存在，官方 checker 也不存在。因此只能判“定义整改 PASS / handoff 保留检查 NOT RUN”，不能判 handoff PASS。

### §3.6 `DOC-OWNERSHIP` 空集标记 — **整改通过（PASS）**

当前 `IMPLEMENTATION_CONTRACT.md:2210` 规定无前置固定写 `—`，`:2232` 的 `DOC-OWNERSHIP` 已使用该标记；本轮建图按空集解析，26 个表节点、0 unknown、0 cycle。该上一轮 LOW 已消除。

### §3.7 “三类 hash”与 target-manifest hash 措辞 — **整改通过（措辞 PASS）**

当前 `PRD_SUPPLEMENT.md:26,32` 已明确“三类独立 hash（revision/initial-state/snapshot）之外，request fingerprint 另绑定 target-manifest hash”；`IMPLEMENTATION_CONTRACT.md:66,354,437,688,2424` 的细节也一致。上一轮 LOW 措辞问题已消除。target-manifest 表本身的唯一约束问题另列 B-03。

### §3.8 `--phase` 词汇表未定义 — **整改通过（定义 PASS）**

当前 `IMPLEMENTATION_CONTRACT.md:2053-2055` 明确规定唯一词汇为 `current|post-handoff`，并定义两阶段的 owner/prep/landing 判定；G0 固定使用 `post-handoff`（`:2322`）。上一轮 phase 未定义问题已消除。官方 checker 仍缺失，故实际 phase 校验未运行。

## 3. 上一轮 §4 全部 PASS 项重验

| 上一轮 §4 项目 | 本轮结果 | 证据与边界 |
|---|---|---|
| frame registration / hypertable 唯一性 | **PASS（静态）** | `PRD_SUPPLEMENT.md:176-178`；`IMPLEMENTATION_CONTRACT.md:734-777`。普通 PG registration PK `(attempt_id,frame_index)` 与 hypertable PK `(frame_time,attempt_id,frame_index)`、同事务和 published pointer 一致；没有重新引入跨 partition unique。未运行数据库 migration。 |
| `next_attempt_at` 持久化/due claim | **PASS（静态）** | `PRD_SUPPLEMENT.md:174`；`IMPLEMENTATION_CONTRACT.md:493-499,529,661-668,870-877,961-968,1000,1272-1273`。四表字段、统一 CHECK、DB due claim 和索引文字一致；但 G3 漏 calibration 是 B-05。 |
| 三类独立 hash + target-manifest hash | **部分 PASS** | hash 归属语义已通过 `IMPLEMENTATION_CONTRACT.md:354,437,688`；但 manifest 表唯一约束与“一 revision/schema 一 hash”文字冲突，见 B-03。 |
| 错误码 / 幂等单一矩阵 | **FAIL** | 代码表覆盖本身可读且 `UNSAFE_PATH=422` 在 `PRD_SUPPLEMENT.md:525-538`；但 B-02 的 idempotency record identity 不可落库、B-04 的 timeout status 冲突。 |
| forecast transition / refresh / observe | **PASS（静态）** | PRD `:193-209` 与 IC `:1645-1662` 七行 action 对齐；`refresh` 四个 delta/`>=`、observe 时间窗、终态不可 reopen 已冻结。未运行 forecast worker。 |
| observation trust 四级/policy/quarantine | **PASS（静态）** | PRD `:109-118`；IC `:781-834`。`quarantined` 只在 status，external_verified 需 auth/binding/quality，policy exclusion 文字一致。未运行真实 ingest。 |
| 工件 fail-closed | **PASS（静态）** | PRD `:213-215`；IC `:944-949,1671`。调度前缺失不建 run，运行中失效 terminal failed，均不发布 horizon/alert/outbox。未运行 policy gate。 |
| reserved-slot 禁借规则 | **FAIL** | 禁借文字 `PRD_SUPPLEMENT.md:181-187`、IC `:713` 一致，但 schema CHECK 仍允许空闲残留 lease/heartbeat，见 B-01。 |
| blockedBy 无环 | **表结构 PASS，完整依赖图 FAIL** | 表 26 节点、0 unknown、0 cycle；但 ASCII 图缺 `FRONTEND-FOUNDATION`、`GATE-HARNESS`、`CI-QUALITY` 并使用非 ID `domain/integration`，见 B-06。 |
| owner 表结构/工作树覆盖 | **PASS（静态）** | 119 owner rows、119 unique patterns；45 candidates 恰一命中，0 unmatched/ambiguous。官方 checker 不存在，故不是 checker PASS。 |
| R0 artifact 绑定 bundle | **定义 PASS，artifact NOT RUN** | `IMPLEMENTATION_CONTRACT.md:2267-2289` 的 base+bundle+逐文件 hash 绑定清楚；当前 JSON artifact 缺失，不能生成 PASS verdict。 |
| gate/harness 定义可执行性 | **FAIL（定义仍有覆盖矛盾）** | 命令/fixture 路径具体，但 G3 漏 calibration（B-05），G6 profile 文字/命令不闭合（B-07）；所有入口实际执行 exit 2/1。 |
| R0 PASS 前 migration 零变更 | **PASS（本轮实测）** | `git status --porcelain -- backend/alembic/versions` 空输出；这只证明当前工作树无 migration 改动，不证明未来 gate 已通过。 |

## 4. 新发现：必须整改的证据

### B-01 [BLOCKER] 空闲 slot 的 lease/heartbeat 约束缺失

证据：`IMPLEMENTATION_CONTRACT.md:695-710` 的 CHECK 只检查 `busy`、`worker_id` 和两类 job/run FK；`:713` 同时要求初始化及 terminal/cancelling 后 `worker/job/run/lease/heartbeat` 全为 NULL。当前 schema 可接受 `busy=false` 且 `lease_until` 或 `heartbeat_at` 非 NULL 的行。

影响：空闲槽仍携带过期 lease/heartbeat，crash recovery、取消和终态清理的审计事实不再由数据库约束保证；这正是 reserved-slot 语义的核心，而不是实现细节。

整改：把 `lease_until IS NULL AND heartbeat_at IS NULL` 纳入 busy=false 分支，并在 busy=true 分支明确要求有效 lease（heartbeat 的更新时机也要写清）；同时给出 cancelling/terminal 的原子释放谓词。

### B-02 [BLOCKER] `idempotency_record_id` 没有可引用的主键

证据：`hydraulic.idempotency_records` 在 `IMPLEMENTATION_CONTRACT.md:1108-1120` 只有复合主键 `(scope,idempotency_key,user_id)`，没有 `id UUID`；但 `network_jobs.idempotency_record_id UUID NOT NULL`（`:493-502`）和 `forecast_alert_transitions.idempotency_record_id UUID NULL`（`:1051-1064`）都使用 UUID 记录引用。当前文本没有声明该 UUID 对应哪个表/列。

影响：API 的同 key 重放可以靠复合键实现，但 job/transition audit 行无法通过声明的 UUID 关联到唯一 idempotency record；数据库无法验证引用完整性，审计链断裂。

整改：要么给 `idempotency_records` 增加 `id UUID PK/UNIQUE` 并为引用列声明 FK；要么把两处引用改成完整复合键/明确审计引用类型，并规定可查询关系。

### B-03 [BLOCKER] target manifest 唯一约束不能实现“一 revision/schema 一 hash”

证据：`IMPLEMENTATION_CONTRACT.md:443-454` 的表约束是：

```text
UNIQUE(network_revision_id,schema_version,content_sha256)
```

而 `:456-458` 明确要求“同一 revision + schema 的 feature_index 只能有一个 hash”。由于 content hash 位于唯一键末尾，同一 `(network_revision_id,schema_version)` 可以插入多个不同 `content_sha256` 的 immutable manifest。

影响：同一 simulation revision/schema 可以存在多个互相不兼容的 feature ordering，违反 map/frame/forecast 使用同一 manifest 的语义。

整改：按实际版本模型选择 `UNIQUE(network_revision_id,schema_version)`，或增加不可变 manifest version/唯一 active 约束，并在 FK/服务校验中锁定 simulation 使用的 ID+hash。

### B-04 [BLOCKER] `SIMULATION_TIMEOUT` 的终态映射自相矛盾

证据：

- `PRD_SUPPLEMENT.md:373`：`SIMULATION_TIMEOUT` 的终态是 `timed_out`；
- `PRD_SUPPLEMENT.md:545`：异步矩阵把 `SIMULATION_TIMEOUT` 放在 `simulation failed`；
- `PRD_SUPPLEMENT.md:547`：nowcast timeout 又规定 `SIMULATION_TIMEOUT + status=timed_out`；
- `IMPLEMENTATION_CONTRACT.md:1262,1266`：nowcast/retry deadline 和 running deadline 都转 `timed_out`。

影响：同一个 error code 会让 GET status、slot release、late-result suppression 和 AC artifact 汇总得到不同终态；这直接破坏 durable retry/terminal 语义。

整改：从 `simulation failed` 行移除 `SIMULATION_TIMEOUT`，或新增明确的 `simulation timed_out` 行并统一所有 job 类型；同步 PRD 任务错误表、IC 状态表、HTTP 504 说明和 gate fixture。

### B-05 [BLOCKER] G3 没有覆盖 calibration durable job

证据：`IMPLEMENTATION_CONTRACT.md:1251-1273` 明确四类 durable job（simulation、network_jobs、nowcast_runs、calibration_runs）共用状态机；`PRD_SUPPLEMENT.md:165-174` 也声明四类。PRD 的 G3 描述 `PRD_SUPPLEMENT.md:603-608` 写“三类 job”，IC 的 G3 定义 `IMPLEMENTATION_CONTRACT.md:2325` 只列 `network-job/simulation/nowcast`，没有 calibration。

影响：即使 G3 通过，也不能证明 calibration 的 `next_attempt_at`、retry/backoff、lease/fencing、cancel/terminal 或 finalize 语义正确；四类统一状态机的关键分支没有机器证据。

整改：在 PRD/IC G3、fixture registry、harness 参数和 acceptance 映射中显式加入 calibration run；为 calibration 定义独立结果/report artifact、错误码和重试 fixture。

### B-06 [BLOCKER] ASCII blockedBy 图不是完整的 26 节点图

证据：`IMPLEMENTATION_CONTRACT.md:2230-2257` 的表有 26 个 work-item ID；`:2212-2227` 的 ASCII 图本轮解析只出现 23 个，缺少 `FRONTEND-FOUNDATION`、`GATE-HARNESS`、`CI-QUALITY`。同时 `:2226` 使用 `G0 + domain/integration -> ...`，但 `:2210` 规定 blockedBy 只能使用 work-item ID，`:2259` 又规定执行顺序严格由 ID 边决定。

影响：G5/G6 依赖 `GATE-HARNESS`、integration 依赖 `FRONTEND-FOUNDATION`、CI 依赖 `GATE-HARNESS` 的表边在图上消失；自然语言占位符可能被实现者误读为隐含边，违反“禁止自然语言先后关系”。

整改：重画 ASCII 图，使其覆盖表中的全部 26 个 ID，并只使用真实 ID；或者删除 ASCII 图并明确表是唯一图示，但不能让不完整图与“严格 ID 边”并列成为规范。

### B-07 [HIGH] G6 的 profile 入口定义不闭合

证据：命令清单 `IMPLEMENTATION_CONTRACT.md:2295-2305` 只有：

```text
python scripts/verify_hydraulic_stack.py --gate g6 --profile postgis ...
```

而 G6 语义 `:2328` 写“两 profile fresh volume”；没有 G6 portable 命令，也没有明确说明 `postgis` entrypoint 内部必须递归运行 portable。若实现按命令逐项执行，G6 的两 profile 要求无法验收。

整改：增加明确的 `g6 --profile portable` 命令及 evidence 目录，或冻结一个唯一的 G6 aggregate 参数/内部双 profile 行为，并在 fixture/manifest/acceptance 中写明。

## 5. 最新 dependency remediation/review 文档的处理

本轮读取：

- `implementation/dependency-contract.md`：其 `:11-15,78-86` 已改为只引用 `DEP-AUTH` 注册表，并明确未交付 checker/harness/perf script；这与本轮 B-02/B-05/B-07 的“实现尚未交付”边界一致。
- `reviews/dependency-contract.md`：这是较早的 dependency 独立审查，记录 HIGH/MEDIUM 实现缺口；它不是当前 R0 schema verdict 的替代物。本轮没有把其中历史定向测试结果当作当前 G0/G5 证据，也没有重跑其测试套件。
- 当前源码只读观察显示，`backend/app/core/config.py` 已出现 staging fallback 限制、RSA key 字段和固定 nowcast 配置等整改文字/代码（`config.py:350-376,446-469`），`backend/pyproject.toml:10-30` 已把 NumPy/Pillow 放入 base dependencies；但这不改变本报告的 R0 结论，且没有在本轮将这些实现声明升级为 schema/semantics PASS。

## 6. 阶段 D / 阶段 V 边界与解除条件

`PRD_SUPPLEMENT.md:43-47`、`IMPLEMENTATION_CONTRACT.md:38-40,2265` 正确区分了设计审查和实现验收：缺失入口只能 NOT RUN，不能被写成 PASS；设计审查也不能代替 G0～G6。当前状态应保持：

```text
R0_ROUND_1_REVIEW = FAIL
NOT_APPROVED
ALL_IMPLEMENTATION_WORK_BLOCKED
```

解除本轮设计阻断的最小条件：

1. 修正 `worker_slots` busy=false/true 的 lease/heartbeat CHECK 和释放原子谓词（B-01）。
2. 为 idempotency record 建立可引用的 UUID 主键/FK，或重写两处引用（B-02）。
3. 使 target manifest 唯一约束真正实现同一 revision/schema 单一 hash（B-03）。
4. 统一 `SIMULATION_TIMEOUT` 的唯一终态和所有 HTTP/async 映射（B-04）。
5. 把 calibration run 纳入四类 durable job 的 G3/harness/acceptance（B-05）。
6. 重画完整的 26 节点 blockedBy ID 图，删除 `domain/integration` 等非 ID 边（B-06）。
7. 闭合 G6 两 profile 的命令/入口语义（B-07）。
8. 重新计算三文档 bundle，并由新的独立复审重新给出 R0 verdict；之后才可生成正式 JSON artifact、进入 handoff/landing，再在同一 RUN_ID/commit 实际执行 G0～G6 和 AC-1～AC-8。

## 7. 最终判定

**verdict = FAIL / NOT_APPROVED。**

本轮确实消除了上一轮 §3.2、§3.3、§3.4、§3.5（定义层）、§3.6、§3.7、§3.8 的原始问题，并确认 §4 中 frame 唯一性、hash 分离、forecast transition、observation trust、工件 fail-closed、owner 表静态覆盖和 migration 零改动等部分 PASS 项。但 B-01～B-07 仍使 schema/semantics、状态机、授权/owner 执行边界和完整依赖图不满足“无矛盾”批准条件；同时 G0～G6/AC 入口实际执行全部失败或缺失，不能记为实现通过。

本轮没有修改任何生产代码、migration 或三份主文档；只生成本报告及同步的复审报告输出。
