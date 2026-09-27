# v3 Schema / Semantics 最终独立复审（整改后第 1 轮）

- **复审者**：`dynamic-workflow v3-final-review-1`（最终独立只读复审；不是文档作者或实现者）
- **复审日期**：2026-09-24
- **复审对象**：`SOURCE_PRD.md`、`PRD_SUPPLEMENT.md`、`IMPLEMENTATION_CONTRACT.md`；整改记录 `reviews/v3-final-remediation.md`；最近报告 `reviews/v3-schema-semantics-review-round-3.md`、`reviews/v3-schema-semantics-review-round-1.md`、`reviews/dependency-contract.md`
- **基线 commit**：`ca99e0485fb12fbecbd565e79d81f745f8d4a748`
- **结论**：**批准（`PASS` / `R0_DESIGN_PASS`）**。第三轮 C-01～C-04 已按冻结决策消除；前两轮所有 PASS 项与 B-01～B-07 整改未回退；本轮 65/65 个只读静态断言通过，未发现 BLOCKER/HIGH 规范内部矛盾。
- **阶段边界**：本结论只批准阶段 D 的 schema/semantics、owner、授权、blockedBy、handoff 保留谓词与 gate 定义。G0～G6、AC-1～AC-8 和实现工作均未在本轮运行/验收；当前候选文档仍禁止自行改写 Frozen，最终状态更新与 R0 JSON artifact 由有权的文档/流程 owner 依本轮 PASS 另行完成。

> 复审边界严格限于前轮已通过项与 C-01～C-04；没有把 G0～G6 尚未实现或 NOT RUN 当作设计失败，也没有扩张新需求。没有修改三份主文档、生产代码或 migration；只创建本报告并同步 canonical review。

## 0. 输入、当前 bundle 与执行证据

### 0.1 本轮读取的输入

本轮实际读取：

- `SOURCE_PRD.md`（33 行，SHA-256 `11e56e68a68017b84b5ae577dede8a74f6217d4a7877f5e358a18f14e66807ee`）；
- `PRD_SUPPLEMENT.md`（692 行，SHA-256 `077d36b386c3b77dfb381602fad84e4b3b4f70065e016e8c0dc6bcf818c56347`）；
- `IMPLEMENTATION_CONTRACT.md`（2641 行，SHA-256 `9f24b5100976280b32ceeb8df977b4f1e208460acdfc27777ff7b3e49ef400fb`）；
- `reviews/v3-final-remediation.md`（84 行，SHA-256 `c2d9965ecda48763127775ed14cee6140087fde1a1914781988c4c75eef746c6`）；
- `reviews/v3-schema-semantics-review-round-3.md` 与同步 canonical `reviews/v3-schema-semantics-review.md`（同步前均为 572 行、SHA-256 `ca07449cabc484556080a45b3b8228f0182fef1d788c8b7a33a2d91d7a517955`）；
- `reviews/v3-schema-semantics-review-round-1.md`（342 行，SHA-256 `0c4a2fd502c6537d4dfcb6ac19d81a3a2417a5264c12288fc672a63e39fb5b2c`）；
- `reviews/dependency-contract.md`（341 行，SHA-256 `87bf74d66d55531c62bd2d298d40cd7bfb7950f1bd6fbeea215c509cf6384780`）。

`SOURCE_PRD.md:2-10` 仍明确其为不可变需求输入，且其 SHA-256 与 round-1/round-3 报告记录一致；本轮未观察到源 PRD 漂移。

### 0.2 当前 canonical bundle（可重算）

执行：

```text
python -c "import hashlib,json,subprocess,pathlib; root=pathlib.Path(r'C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage'); rels=['docs/hydraulic-twin/SOURCE_PRD.md','docs/hydraulic-twin/PRD_SUPPLEMENT.md','docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md']; base=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(); docs=[{'path':r,'sha256':hashlib.sha256((root/r).read_bytes()).hexdigest()} for r in sorted(rels)]; obj={'base_commit':base,'documents':docs,'schema':'v3-review-bundle-v1'}; raw=json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode(); print('base_commit='+base); [print(d['path']+'='+d['sha256']) for d in docs]; print('canonical='+raw.decode()); print('document_bundle_sha256='+hashlib.sha256(raw).hexdigest())"
```

实际输出：

```text
base_commit=ca99e0485fb12fbecbd565e79d81f745f8d4a748
docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md=9f24b5100976280b32ceeb8df977b4f1e208460acdfc27777ff7b3e49ef400fb
docs/hydraulic-twin/PRD_SUPPLEMENT.md=077d36b386c3b77dfb381602fad84e4b3b4f70065e016e8c0dc6bcf818c56347
docs/hydraulic-twin/SOURCE_PRD.md=11e56e68a68017b84b5ae577dede8a74f6217d4a7877f5e358a18f14e66807ee
canonical={"base_commit":"ca99e0485fb12fbecbd565e79d81f745f8d4a748","documents":[{"path":"docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md","sha256":"9f24b5100976280b32ceeb8df977b4f1e208460acdfc27777ff7b3e49ef400fb"},{"path":"docs/hydraulic-twin/PRD_SUPPLEMENT.md","sha256":"077d36b386c3b77dfb381602fad84e4b3b4f70065e016e8c0dc6bcf818c56347"},{"path":"docs/hydraulic-twin/SOURCE_PRD.md","sha256":"11e56e68a68017b84b5ae577dede8a74f6217d4a7877f5e358a18f14e66807ee"}],"schema":"v3-review-bundle-v1"}
document_bundle_sha256=624dbd15fb4fbc7122de22b0018fa6f80de5a9f1c2dee16c5817a10eeccea109
```

canonical 规则来自 `IMPLEMENTATION_CONTRACT.md:2432-2435`：base commit + 三份文档逐文件 SHA-256、documents 按 path 排序、UTF-8 canonical JSON bytes，再取 SHA-256。该 bundle 是本批准报告所绑定、可由 live files 重算的输入。

### 0.3 实际执行的静态检查

本轮编写并执行一个临时只读 Python 检查器，读取三份主文档、`backend/app/core/config.py`、`.env.example` 与 `git status`，逐表/逐段提取 DDL、Settings 两个授权数组、owner 表、DEP-AUTH registry 与 blockedBy 表并断言；执行后已删除临时检查器。修正检查器自身的抽取边界后，最终实际命令：

```text
python -B "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage\docs\hydraulic-twin\reviews\.v3-final-review-check.py"
```

最终关键输出：

```text
DDL network_jobs lines 56 idempotency [... UNIQUE(idempotency_record_id) ...]
DDL simulations lines 70 idempotency [... UNIQUE(idempotency_record_id) ...]
DDL calibration_runs lines 38 idempotency [... UNIQUE(idempotency_record_id) ...]
SETTINGS authorized 54 unauthorized 47 cfg 101 env 101 overlap []
CHECKS 65 PASS 65 FAIL 0
...
PASS.owner_rows119 | rows=119
PASS.owner_unique
PASS.dep_auth13 | ids=['DEP-AUTH-01', ..., 'DEP-AUTH-13']
PASS.blockedby26 | nodes=26
PASS.blockedby_unknown | unknown=[]
PASS.blockedby_acyclic | cycles=[]
PASS.doc_owner_empty
PASS.migrations_empty | ''
```

另执行：

```text
python -c "import ast; ... Settings ... print(len(fields), len(set(fields)))"
→ Settings.model_fields 101 unique 101

python -c "... parse .env.example keys ..."
→ env_keys 101 unique 101

git -C "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage" status --porcelain -- backend/alembic/versions
→ (no output)
```

这些是定义层/工作树静态证据，不替代尚不存在的官方 checker、migration 或 G0～G6 运行证据。

## 1. 第三轮 C-01～C-04 逐项复核

| ID | 结果 | 可复现证据 |
|---|---|---|
| C-01 | **PASS（已解决）** | `PRD_SUPPLEMENT.md:231-251` 与 `IMPLEMENTATION_CONTRACT.md:648-716,897-935,1225-1249` 对 24h generation、resource FK、cancel scope 和 retention 使用同一语义。 |
| C-02 | **PASS（已解决）** | 四张冻结 DDL 均显式含两类 retry CHECK，且 `IMPLEMENTATION_CONTRACT.md:1376-1403`、G1 predicate `2491` 与阻断项 `2605` 同步。 |
| C-03 | **PASS（已解决）** | `IMPLEMENTATION_CONTRACT.md:1966-2079,2442-2443,2606` 将 54 个授权字段与 47 个 preserve-only 字段分为互斥数组；实测两数组与 101 Settings/env key 并集完全相等。 |
| C-04 | **PASS（已解决）** | 候选 PRD/IC 不再内嵌 round-2 历史 hash/bundle；`PRD_SUPPLEMENT.md:34-40`、`IMPLEMENTATION_CONTRACT.md:2428-2436` 保留 live-file 重算与最终 artifact 规则。本报告记录当轮可重算 bundle。 |

### C-01：24h generation 与 resource 幂等引用 — PASS

- `PRD_SUPPLEMENT.md:234-250` 固定 24h、retire、递增 generation；simulation/calibration 过期后允许新 resource；network_jobs/simulations/calibration_runs 每表只保留一个 `idempotency_record_id` 与 `UNIQUE(idempotency_record_id)`，禁止恢复 resource `idempotency_key` 或永久 user+key unique。
- `IMPLEMENTATION_CONTRACT.md:505,514` 的 network_jobs、`:677,698` 的 simulations、`:921,924` 的 calibration_runs 均具备单列 UUID NOT NULL FK 与一次 UNIQUE；`IMPLEMENTATION_CONTRACT.md:716,935` 明确 24h 后以新 record 新建 resource。
- simulation cancel 使用 `scope=simulation.cancel` 的 record 保存 resource/response，但 `IMPLEMENTATION_CONTRACT.md:1249` 明确不增加 simulations 第二个 FK 列；`PRD_SUPPLEMENT.md:250` 一致。
- `IMPLEMENTATION_CONTRACT.md:1225-1249` 保留 UUID PK、generation+1、active partial unique、advisory lock、FK retention，并禁止用自由 UUID/永久 key unique 替代。
- 本轮结构检查对三张 resource 表逐一确认 FK=3/3、DDL UNIQUE=3/3、legacy key column/UNIQUE=0；simulation cancel scope 与禁止第二 FK 也同时通过。

结论：第三轮 C-01 的 TTL/generation 与永久唯一键矛盾已真实消除。

### C-02：四张 durable job 表 retry CHECK — PASS

- `IMPLEMENTATION_CONTRACT.md:515-521` network_jobs、`:704-710` simulations、`:926-932` calibration_runs、`:1090-1097` nowcast_runs 均逐表包含：
  - `CHECK ((status='retry_wait') = (next_attempt_at IS NOT NULL))`；
  - retry_wait 时 backoff>0 且 trim 后 error 非空；非 retry_wait 时二者均 NULL。
- `IMPLEMENTATION_CONTRACT.md:1400-1403` 把两类 CHECK 固定为四表 MUST；`PRD_SUPPLEMENT.md:613-615` 的 G1/G3 验收、G1 manifest `IMPLEMENTATION_CONTRACT.md:2491`、阻断项 `2605` 使用同一要求。
- 本轮检查结果 `C02.*.next` 与 `C02.*.metadata` 8/8 PASS。

结论：第三轮 C-02 的 prose-vs-DDL 缺口已消除。

### C-03：54 授权 Settings + 47 preserve-only — PASS

- `IMPLEMENTATION_CONTRACT.md:1966-2025` 列出 54 个授权字段；`:2027-2077` 列出 47 个 preserve-only 字段，后者 fixture 每项只允许 `name/authorized=false/preserve_only=true`。
- `IMPLEMENTATION_CONTRACT.md:2442-2443` 明确 `settings.fields[]` 只含 54 项且逐项完整对账，`settings.unauthorized_fields[]` 单独含其余 47 项；两组互斥，并集等于 `Settings.model_fields` 和 `.env.example` keys。
- `IMPLEMENTATION_CONTRACT.md:2606` 的阻断条件同步；`PRD_SUPPLEMENT.md:614` 将 simulation/calibration expiry/new generation 纳入 G2。
- 本轮 AST 读取 `Settings` 得 101/101 unique 字段，解析 `.env.example` 得 101/101 unique key；文档授权数组为 54/47，交集空、并集与两个 101-key 集合相等。

结论：第三轮 C-03 的“54 registry 与 101 Settings 完全相等”不可满足矛盾已消除。

### C-04：不可回溯的历史 hash 自述 — PASS

- `PRD_SUPPLEMENT.md:37-39` 与 `IMPLEMENTATION_CONTRACT.md:2433-2435` 明确候选文档不记录历史 review 的输入 hash/document bundle，下一 reviewer 必须从 live files 重算，只有最终批准 artifact 可记录当轮可重算 bundle。
- 本轮搜索确认 PRD/IC 不含 round-2 的 `bd451b60…`、`fae14e1d…`、`115cc130…` 或 round-3 的 `e49df829…`。
- 本报告 §0.2 记录当前 live-file bundle `624dbd15…` 及 canonical bytes，符合冻结证据规则。

结论：第三轮 C-04 的历史证据自指问题已消除。

## 2. 前两轮 PASS/B 整改项未回退

### 2.1 B-01～B-07 与 §3.1～§3.8

| 历史项 | 本轮结果 | 证据 |
|---|---|---|
| worker_slots idle/busy CHECK、seed、release | **PASS（静态）** | `IMPLEMENTATION_CONTRACT.md:720-743` 两分支完整；idle 清空 lease/heartbeat，busy 非空且 lease>heartbeat，cancelling/terminal 全空，nowcast 禁借。 |
| 单一 durable-job transition table | **PASS（静态）** | `PRD_SUPPLEMENT.md:164-178` 委托唯一表；`IMPLEMENTATION_CONTRACT.md:1376-1405` 唯一转换表；四表 status 集合一致。 |
| dependency 授权单一注册表/phase | **PASS（定义）** | `IMPLEMENTATION_CONTRACT.md:2209-2238` 仅 DEP-AUTH-01..13，12 个 prep=true、13 prep=false，phase 仅 current|post-handoff。官方 checker NOT RUN。 |
| 实施摘要过期 owner 断言 | **PASS** | `IMPLEMENTATION_CONTRACT.md:2237` 将旧断言标为 stale evidence；没有另列授权子集。 |
| handoff 保留谓词 | **PASS（定义）** | `IMPLEMENTATION_CONTRACT.md:2437-2456` 版本、lock、Settings、auth IDs 与 C-01 检查可定位。执行 checker NOT RUN。 |
| DOC-OWNERSHIP 空集 | **PASS** | `IMPLEMENTATION_CONTRACT.md:2391-2397` 固定 `—`，本轮重建图解析为空集。 |
| hash 措辞 | **PASS** | `PRD_SUPPLEMENT.md:25,31` 与 `IMPLEMENTATION_CONTRACT.md:439,462,714` 保持四类 fingerprint hash 分离。 |
| B-02 idempotency UUID identity | **PASS** | `IMPLEMENTATION_CONTRACT.md:1225-1249` id UUID PK 与四类引用 FK 完整。 |
| B-03 target manifest 唯一 hash | **PASS** | `IMPLEMENTATION_CONTRACT.md:441-462` `UNIQUE(network_revision_id,schema_version)` 与复合 FK。 |
| B-04 timeout 终态 | **PASS** | `PRD_SUPPLEMENT.md:363-381,540-555`、`IMPLEMENTATION_CONTRACT.md:1397-1401` 只映射 timed_out。 |
| B-05 G3 calibration | **PASS（定义）** | `PRD_SUPPLEMENT.md:615`、`IMPLEMENTATION_CONTRACT.md:2418,2483,2493` 覆盖四类 job 与 calibration fixture。 |
| B-06 完整 blockedBy | **PASS（表结构）** | `IMPLEMENTATION_CONTRACT.md:2389-2424` 唯一 26-node 表；实测 26/0 unknown/0 cycle。 |
| B-07 G6 双 profile | **PASS（定义）** | `PRD_SUPPLEMENT.md:607-608,618`、`IMPLEMENTATION_CONTRACT.md:2471-2473,2496` 各有 postgis/portable 独立命令和 evidence。 |

### 2.2 前轮 §4 PASS 项

| 项目 | 本轮结果 | 证据 |
|---|---|---|
| frame registration / hypertable uniqueness | **PASS（静态）** | `IMPLEMENTATION_CONTRACT.md:769-815` registration `(attempt_id,frame_index)` PK + time unique；hypertable PK 含 frame_time，禁止跨 partition `(attempt_id,frame_index)` unique。 |
| next_attempt_at 持久化/due claim | **PASS（静态）** | C-02 四表 DDL 已下沉；`IMPLEMENTATION_CONTRACT.md:537-543,1100-1105,1397-1403` 保持 DB due claim。 |
| 三类独立 hash + target manifest hash | **PASS（静态）** | `PRD_SUPPLEMENT.md:259-268`、`IMPLEMENTATION_CONTRACT.md:441-462,714`。 |
| 错误码/幂等单一矩阵 | **PASS（静态）** | C-01/D-12、`IMPLEMENTATION_CONTRACT.md:1225-1249,1489-1494` 一致；`UNSAFE_PATH=422` 保持。 |
| forecast transition/refresh/observe | **PASS（静态）** | `PRD_SUPPLEMENT.md:188-214` 与 `IMPLEMENTATION_CONTRACT.md:1764-1802` 七 action 对齐。 |
| observation trust/policy/quarantine | **PASS（静态）** | `PRD_SUPPLEMENT.md:106-118`、`IMPLEMENTATION_CONTRACT.md:819-895` 四级 trust、policy/exclusion、quarantine 与 formal-use 边界。 |
| 工件 fail-closed | **PASS（静态）** | `PRD_SUPPLEMENT.md:210-214,383-400`、`IMPLEMENTATION_CONTRACT.md:1010-1015,1100-1105,1800-1802`。 |
| reserved slot 禁借 | **PASS（静态）** | `PRD_SUPPLEMENT.md:180-186`、`IMPLEMENTATION_CONTRACT.md:720-743,1995-2001` 固定 Literal 与禁借。 |
| blockedBy 无环 | **PASS（表结构）** | 26 node、0 unknown、0 cycle；DOC-OWNERSHIP 空集。 |
| owner 表结构/授权 | **PASS（静态）** | 119 rows/119 unique；DEP-AUTH 13 行连续；§15.6 review owner 为 r0-reviewer。 |
| R0 bundle 绑定 | **PASS（定义）** | `IMPLEMENTATION_CONTRACT.md:2432-2436` 定义闭合；本轮 §0.2 bundle 可重算。JSON artifact 仍 NOT RUN。 |
| gate/harness 定义覆盖 | **PASS（定义）** | G0～G6/AC 命令、fixture、calibration、双 profile 均闭合；实际入口仍 NOT RUN。 |
| R0 PASS 前 migration 零变更 | **PASS（工作树）** | 本轮 `git status --porcelain -- backend/alembic/versions` 空输出。 |

## 3. Owner、授权与 blockedBy

### 3.1 Owner / Settings 授权子集

- `IMPLEMENTATION_CONTRACT.md:2209-2214` 规定每候选 path 最多一个 owner；`:2216-2230` 是唯一 dependency stage registry。
- `IMPLEMENTATION_CONTRACT.md:2240-2387` 六张 owner 表本轮解析为 119 行、119 个唯一 path pattern，无重复 pattern。
- `IMPLEMENTATION_CONTRACT.md:2378-2387` 将三份主文档归 delivery-docs、reviews 归 r0-reviewer、R0 JSON 归 r0-reviewer；本报告写入路径符合 owner。
- Settings 授权仍由 `DEP-AUTH-06` 唯一决定；54/47 是字段授权子集而不是第二套路径授权。`IMPLEMENTATION_CONTRACT.md:1966-1968,2209-2214` 明确这一点。
- 官方 `check_contract_ownership.py` / `check_dependency_handoff.py` 尚不存在，未把静态表结构误报为 checker PASS。

### 3.2 blockedBy

`IMPLEMENTATION_CONTRACT.md:2391-2424` 明确无第二图示，只允许 26 个 work-item ID 或空集标记 `—`。本轮从该表重建图，结果：

```text
PASS.blockedby26 | nodes=26
PASS.blockedby_unknown | unknown=[]
PASS.blockedby_acyclic | cycles=[]
PASS.doc_owner_empty
```

R0 PASS 后只按表中 `R0-REVIEW` 的直接边解锁 `DEP-LANDING`、`SCHEMA-MIGRATIONS`、`SECURITY-GATE`、`ARTIFACTS`、`GATE-BOOTSTRAP`；不得跳过 DEP/SCHEMA/INTEGRATION 边。本轮没有发现 owner/授权/blockedBy 的内部矛盾。

## 4. Findings

### BLOCKER

无。

### HIGH

无。

### MEDIUM（非阻断、可追踪）

1. **M-01：候选状态文字仍冻结为整改前失败状态。** `PRD_SUPPLEMENT.md:2,34-40,622-631,658-691` 与 `IMPLEMENTATION_CONTRACT.md:2,2428-2436,2556-2641` 仍写 `R0_ROUND_3_FAILED / NOT_APPROVED / ALL_IMPLEMENTATION_WORK_BLOCKED`。这在本轮批准前是正确的 fail-closed 保护，但本报告通过后需要一个有权 owner 流程把正式 R0 JSON artifact、状态解锁与文档 Frozen 状态原子化处理；**不得只改一句状态而跳过 bundle/owner/migration gate**。本项不构成规范内部矛盾，不阻止本轮 review verdict。

### LOW（非阻断、可追踪）

1. **L-01：历史 implementation/hydraulic-schema 的旧行号引用会随主文档变化失效。** round-3 报告 `reviews/v3-schema-semantics-review-round-3.md:542-545` 已把该问题识别为过期引用。本轮没有把历史行号当规范；后续依赖文档应在状态更新时刷新引用或改用稳定章节 ID。
2. **L-02：当前 bundle 值只应存在于本最终 review/最终 R0 JSON，而不是回写候选文档。** 这是已冻结且正确的防自引用规则；提醒后续流程不要为“看起来更新”而重新把 `624dbd15…` 写回 PRD/IC。

## 5. 未运行与批准边界

以下项目在本轮均未运行，不能记为实现 PASS：

- 官方 owner/handoff/migration checker；
- G0～G6、AC-1～AC-8；
- pytest、Vitest、npm ci、Alembic live、Docker；
- solver/worker/calibration、性能、backup/restore；
- R0 JSON artifact 生成/消费。

上述 NOT RUN 不影响本轮阶段 D 设计批准，但不得将其扩写为实现完成、migration 可落地或任何 gate PASS。正式流程应先用本报告绑定的 live-file bundle 生成/校验 R0 JSON，再按 `IMPLEMENTATION_CONTRACT.md:2391-2424` 的 blockedBy 边推进。

## 6. 最终判定

**verdict = PASS / APPROVED（阶段 D 设计冻结范围）。**

C-01～C-04 均已按第三轮冻结决策解决；B-01～B-07、前两轮 §3/§4 PASS 项、Settings 54/47 授权子集、owner 矩阵和 26-node blockedBy 图均未回退。当前 live-file canonical bundle 为：

```text
624dbd15fb4fbc7122de22b0018fa6f80de5a9f1c2dee16c5817a10eeccea109
```

未发现 BLOCKER/HIGH 规范内部矛盾，证据可重算，因此 `approved=true`。M-01 与 L-01/L-02 仅作为后续有边界的可追踪改进，不移动本轮门禁。
