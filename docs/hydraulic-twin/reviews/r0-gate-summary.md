# R0 gate 与 dependency handoff 实施摘要

- 日期：2026-09-24
- 范围：阶段 D 的 R0 设计批准工件、dependency-config handoff、两个只读前置 checker 及其定向 pytest。
- 明确未做：未启动数据库/schema，未运行 Alembic upgrade/downgrade，未创建、修改或重命名 migration，未运行 G0～G6、AC-1～AC-8、Vitest、npm ci、性能 harness 或 Docker。

## 1. 实际读取的规范与当前事实

本轮读取并用于实现的冻结输入：

- `docs/hydraulic-twin/SOURCE_PRD.md`：不可变源需求与 Python 3.11/Node 18 技术边界（`SOURCE_PRD.md:2-14,28-32`）。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md`：R0 与实现验收分离、Settings 54/47 边界及 G0/G1 前置（`PRD_SUPPLEMENT.md:34-47,594-618`）。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md`：canonical JSON、54/47 Settings、DEP-AUTH-01..13、post-handoff owner、R0 bundle 与两条 checker 命令（`IMPLEMENTATION_CONTRACT.md:97-107,1966-2079,2209-2238,2428-2456`）。
- `docs/hydraulic-twin/reviews/v3-final-review-round-1.md`：独立 reviewer=`dynamic-workflow v3-final-review-1`、结论 `PASS / R0_DESIGN_PASS`、五项范围和未运行边界（`v3-final-review-round-1.md:2-9,90-172,216-238`）。
- `docs/hydraulic-twin/implementation/dependency-contract.md`：先前依赖配置改动、Node/npm 目标、历史测试边界和未交付脚本（`dependency-contract.md:16-35,58-88`）。
- 当前 `backend/app/core/config.py`、`.env.example`、`.python-version`、`frontend/.node-version`、`frontend/.npmrc`、`frontend/package.json`、`frontend/package-lock.json`、Python requirements/locks、`backend/pyproject.toml` 与当前 worktree。

最终独立报告内嵌的是其复审当时的历史 bundle；本轮按任务要求从当前 live files 重算正式 R0 artifact。`check_migration_gate.py` 同时校验报告中的 reviewer/verdict/base 与当前 live bundle，不把历史内嵌值冒充当前值。

## 2. 生成的工件与 live hash

### 2.1 R0 review artifact

路径：`artifacts/contracts/v3-independent-review.json`

- `verdict`：`R0_DESIGN_PASS`
- `reviewer`：`dynamic-workflow v3-final-review-1`
- `review_report`：`docs/hydraulic-twin/reviews/v3-final-review-round-1.md`
- `base_commit`：`ca99e0485fb12fbecbd565e79d81f745f8d4a748`
- `document_bundle_sha256`：`5b9b2e3f54bb910706a2d130ff564ebaf3e2292c9db3ace449b62cd5006fa55d`
- artifact SHA-256：`90106f147bc7709d929ba678ed96c45d4d4745533654f61cbb84f822cc64bf9f`

逐文件 SHA-256：

| 文档 | SHA-256 |
|---|---|
| `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md` | `5fa5f57096e5ff7ca44d85797e8476c7f329525f582f2e039e6c882c19558141` |
| `docs/hydraulic-twin/PRD_SUPPLEMENT.md` | `120b644023d14d42f54d48bb068361964afc5dd98343434d92518449ed741551` |
| `docs/hydraulic-twin/SOURCE_PRD.md` | `11e56e68a68017b84b5ae577dede8a74f6217d4a7877f5e358a18f14e66807ee` |

实际重算命令：

```text
python -c "import hashlib,json,pathlib; root=pathlib.Path(r'C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage'); rels=['docs/hydraulic-twin/SOURCE_PRD.md','docs/hydraulic-twin/PRD_SUPPLEMENT.md','docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md']; docs=[{'path':r,'sha256':hashlib.sha256((root/r).read_bytes()).hexdigest()} for r in sorted(rels)]; obj={'base_commit':'ca99e0485fb12fbecbd565e79d81f745f8d4a748','documents':docs,'schema':'v3-review-bundle-v1'}; raw=json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode(); [print(d['path']+'='+d['sha256']) for d in docs]; print('canonical='+raw.decode()); print('document_bundle_sha256='+hashlib.sha256(raw).hexdigest())"
```

实际输出：

```text
docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md=5fa5f57096e5ff7ca44d85797e8476c7f329525f582f2e039e6c882c19558141
docs/hydraulic-twin/PRD_SUPPLEMENT.md=120b644023d14d42f54d48bb068361964afc5dd98343434d92518449ed741551
docs/hydraulic-twin/SOURCE_PRD.md=11e56e68a68017b84b5ae577dede8a74f6217d4a7877f5e358a18f14e66807ee
canonical={"base_commit":"ca99e0485fb12fbecbd565e79d81f745f8d4a748","documents":[{"path":"docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md","sha256":"5fa5f57096e5ff7ca44d85797e8476c7f329525f582f2e039e6c882c19558141"},{"path":"docs/hydraulic-twin/PRD_SUPPLEMENT.md","sha256":"120b644023d14d42f54d48bb068361964afc5dd98343434d92518449ed741551"},{"path":"docs/hydraulic-twin/SOURCE_PRD.md","sha256":"11e56e68a68017b84b5ae577dede8a74f6217d4a7877f5e358a18f14e66807ee"}],"schema":"v3-review-bundle-v1"}
document_bundle_sha256=5b9b2e3f54bb910706a2d130ff564ebaf3e2292c9db3ace449b62cd5006fa55d
```

### 2.2 Dependency handoff 与 expected fixture

路径：

- `artifacts/contracts/dependency-config-handoff.json`，SHA-256 `6b7ff01dd7e5426dbaa22b42826a15fc30a10d35d640974f5a66b52263db4e85`
- `backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json`，SHA-256 `6cf8412697444fba1d3029abd797d028f0db4a5a34cefda3d22e1976653839ce`

记录内容：

- `auth_ids` 精确为 `DEP-AUTH-01..13`，无重复；`files` 精确覆盖 `DEP-AUTH-01..12` 的 live 文件。
- 每个 `DEP-AUTH-01..12` 路径记录 base 状态/base SHA-256 与 live result SHA-256；base 不存在的新文件明确记录 `base_state=ABSENT`、`base_sha256=null`，没有伪造空文件 hash。
- post-handoff effective owner：`DEP-AUTH-01..06,09..12 -> integration-config`；`DEP-AUTH-07,08,13 -> dependency-config`。
- Settings：54 个 `authorized=true` 字段逐项记录 canonical type/default/constraints/validators/env example/invalid cases；47 个 preserve-only 字段只记录 `name/authorized=false/preserve_only=true`；两组并集与 live `Settings.model_fields`、`.env.example` 的 101 个 key 相等。
- 版本：Python `3.11`、Node `18.20.8`、npm `10.8.2`、`packageManager=npm@10.8.2`、Rollup override `4.22.0`、`lockfileVersion=3`（JSON number）；package-lock 根 dependencies/devDependencies/engines 与 package.json 相等。

## 3. 明确 deferred 的后续 owner 项目

handoff 状态是 `READY_WITH_DEFERRED`，不是 G0/G5 PASS：

1. `frontend-performance-entrypoint`
   - 路径：`frontend/package.json#scripts.test:perf:twin`
   - owner：`frontend-acceptance`
   - 冻结命令：`node performance/run-twin-map.mjs`
   - 原因：可执行性能 harness、固定 fixture 与 evidence 属于 integration 后的 frontend acceptance owner；dependency-config 不得创建假脚本。
   - 当前事实：script、`frontend/performance/run-twin-map.mjs` 和固定 browser fixture 均不存在；checker 明确输出 `G0_G5_BLOCKED_BY_DEFERRED=true`。
2. `frontend-target-runtime-evidence`
   - 路径：`artifacts/hydraulic/<RUN_ID>/g0`
   - owner：`ci-quality-bootstrap`
   - 原因：本 handoff checker 只读验证版本声明；目标环境中 `node --version == v18.20.8`、`npm --version == 10.8.2` 的运行证据由后续 G0 执行 owner 产生。本轮未把声明验证扩写成 runtime PASS。

## 4. 最终只读 checker 命令与结果

### 4.1 Migration gate

实际命令：

```text
uv run --isolated --no-project --python 3.11 --with-requirements "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage\backend\requirements-py311.lock" python -B "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage\scripts\check_migration_gate.py" --review-artifact artifacts/contracts/v3-independent-review.json --base-ref ca99e0485fb12fbecbd565e79d81f745f8d4a748 --versions-dir backend/alembic/versions
```

实际结果：exit 0。

```text
{"base_commit": "ca99e0485fb12fbecbd565e79d81f745f8d4a748", "document_bundle_sha256": "5b9b2e3f54bb910706a2d130ff564ebaf3e2292c9db3ace449b62cd5006fa55d", "documents": [{"path": "docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md", "sha256": "5fa5f57096e5ff7ca44d85797e8476c7f329525f582f2e039e6c882c19558141"}, {"path": "docs/hydraulic-twin/PRD_SUPPLEMENT.md", "sha256": "120b644023d14d42f54d48bb068361964afc5dd98343434d92518449ed741551"}, {"path": "docs/hydraulic-twin/SOURCE_PRD.md", "sha256": "11e56e68a68017b84b5ae577dede8a74f6217d4a7877f5e358a18f14e66807ee"}], "review_report": "docs/hydraulic-twin/reviews/v3-final-review-round-1.md", "review_report_sha256": "93f6e76ee0ff34581672d23371fc170a2c2e4a0e5482a281f078b166a65afe29", "status": "PASS", "verdict": "R0_DESIGN_PASS", "versions_changed": [], "versions_dir": "backend/alembic/versions"}
MIGRATION_GATE=PASS
```

该 checker 只读取 JSON/Markdown/live files 并调用 Git 查询；不导入应用、不调用 Alembic、不启动 schema。

### 4.2 Dependency handoff gate

实际命令：

```text
uv run --isolated --no-project --python 3.11 --with-requirements "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage\backend\requirements-py311.lock" python -B "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage\scripts\check_dependency_handoff.py" --handoff artifacts/contracts/dependency-config-handoff.json --expected backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json --python-version-file .python-version --node-version-file frontend/.node-version --npmrc frontend/.npmrc --package frontend/package.json --lock frontend/package-lock.json --env-example .env.example
```

实际结果：exit 0，状态为 `PASS_WITH_DEFERRED`。

```text
{"auth_ids": ["DEP-AUTH-01", "DEP-AUTH-02", "DEP-AUTH-03", "DEP-AUTH-04", "DEP-AUTH-05", "DEP-AUTH-06", "DEP-AUTH-07", "DEP-AUTH-08", "DEP-AUTH-09", "DEP-AUTH-10", "DEP-AUTH-11", "DEP-AUTH-12", "DEP-AUTH-13"], "base_commit": "ca99e0485fb12fbecbd565e79d81f745f8d4a748", "deferred": ["frontend-performance-entrypoint", "frontend-target-runtime-evidence"], "g0_g5_blocked_by_deferred": true, "r0_review": {"artifact": "artifacts/contracts/v3-independent-review.json", "document_bundle_sha256": "5b9b2e3f54bb910706a2d130ff564ebaf3e2292c9db3ace449b62cd5006fa55d", "verdict": "R0_DESIGN_PASS"}, "settings_counts": {"authorized": 54, "preserve_only": 47}, "status": "READY_WITH_DEFERRED", "versions": {"lockfileVersion": 3, "node": "18.20.8", "npm": "10.8.2", "packageManager": "npm@10.8.2", "python": "3.11", "rollupOverride": "4.22.0"}}
DEPENDENCY_HANDOFF=PASS_WITH_DEFERRED
DEFERRED=frontend-performance-entrypoint
DEFERRED=frontend-target-runtime-evidence
G0_G5_BLOCKED_BY_DEFERRED=true
```

### 4.3 定向 pytest

实际命令：

```text
uv run --isolated --no-project --python 3.11 --with-requirements "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage\backend\requirements-py311.lock" python -B -m pytest "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage\backend\tests\test_contract_gate_scripts.py" -q -p no:cacheprovider
```

实际结果：exit 0。

```text
........                                                                 [100%]
8 passed, 1 warning in 1.76s
```

唯一 warning 是既有 pydantic `model_version` protected namespace warning；本轮未修改生产 Settings。

### 4.4 格式、import 与 lint

实际执行并 exit 0：

```text
uv run --isolated --no-project --python 3.11 --with-requirements "...\requirements-py311.lock" black --check scripts/check_migration_gate.py scripts/check_dependency_handoff.py backend/tests/test_contract_gate_scripts.py
→ All done! 3 files would be left unchanged.

uv run --isolated --no-project --python 3.11 --with-requirements "...\requirements-py311.lock" isort --check-only --settings-path backend/pyproject.toml scripts/check_migration_gate.py scripts/check_dependency_handoff.py backend/tests/test_contract_gate_scripts.py
→ (no output)

uv run --isolated --no-project --python 3.11 --with-requirements "...\requirements-py311.lock" flake8 --max-line-length=88 scripts/check_migration_gate.py scripts/check_dependency_handoff.py backend/tests/test_contract_gate_scripts.py
→ (no output)
```

### 4.5 Migration 目录零变化

实际命令：

```text
git -C "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage" status --porcelain --untracked-files=all -- backend/alembic/versions
git -C "C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage" diff --name-only ca99e0485fb12fbecbd565e79d81f745f8d4a748 -- backend/alembic/versions
```

实际结果：两条命令均 exit 0、无输出；migration gate 同时返回 `versions_changed=[]`。

## 5. Checker 调试过程中真实出现并已修正的失败

以下失败发生在最终 PASS 之前，均由本轮 checker 自身缺陷触发，不是产品/契约失败：

1. 首次 migration checker 把 40 字符 base commit 错用 64 字符 SHA-256 predicate，exit 1：`review artifact base_commit must be lowercase SHA-1`。已拆成 commit/SHA-256 两个 validator。
2. 第二次把 ignored `backend/alembic/versions/__pycache__/...pyc` 当作 migration 源文件，exit 1。已改用 `git ls-files --cached --others --exclude-standard` 比较非忽略 live 路径。
3. 第三次直接比较 `git show` LF bytes 与 Windows checkout bytes，误报 CRLF 差异，exit 1。已改用 `git diff --name-only <base> -- versions-dir` 校验 Git-visible 内容变化。

最终结果以 §4 的 exit 0 命令为准；没有隐藏或改写上述中间失败。

## 6. 最终状态边界

- **阶段 D R0**：`R0_DESIGN_PASS` artifact 已生成并由只读 migration checker 对 live files 验证。
- **Dependency handoff**：`READY_WITH_DEFERRED`；配置/依赖声明、hash、Settings 54/47、Node/npm/lock 与 auth/effective-owner 集合已验证。
- **G0/G5**：因明确的 frontend performance entrypoint 与目标 runtime evidence 尚未由后续 owner 交付，仍 **BLOCKED / NOT RUN**，不得由本 handoff 冒充 PASS。
- **Schema/migration**：未启动、未写入；versions 目录相对 base commit 零变化。

## 7. 仓库根确定性执行证据

本轮新增机器工件：`artifacts/contracts/r0-gate-execution.json`，SHA-256 `fe0b013c88800c9bef3bde533e039a19a350a0e6de42d59d7d77bbde1b7a9250`。执行 cwd 固定为仓库根 `C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage`，两个 checker 均由 Python 3.11 的隔离 `uv` 环境直接调用。

### 7.1 Migration gate 根目录执行

实际命令：

```text
C:\Python314\Scripts\uv.EXE run --isolated --no-project --python 3.11 --with-requirements backend/requirements-py311.lock python -B scripts/check_migration_gate.py --review-artifact artifacts/contracts/v3-independent-review.json --base-ref ca99e0485fb12fbecbd565e79d81f745f8d4a748 --versions-dir backend/alembic/versions
```

实际结果：exit code `0`，stderr 为空。

```text
{"base_commit": "ca99e0485fb12fbecbd565e79d81f745f8d4a748", "document_bundle_sha256": "5b9b2e3f54bb910706a2d130ff564ebaf3e2292c9db3ace449b62cd5006fa55d", "documents": [{"path": "docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md", "sha256": "5fa5f57096e5ff7ca44d85797e8476c7f329525f582f2e039e6c882c19558141"}, {"path": "docs/hydraulic-twin/PRD_SUPPLEMENT.md", "sha256": "120b644023d14d42f54d48bb068361964afc5dd98343434d92518449ed741551"}, {"path": "docs/hydraulic-twin/SOURCE_PRD.md", "sha256": "11e56e68a68017b84b5ae577dede8a74f6217d4a7877f5e358a18f14e66807ee"}], "review_report": "docs/hydraulic-twin/reviews/v3-final-review-round-1.md", "review_report_sha256": "93f6e76ee0ff34581672d23371fc170a2c2e4a0e5482a281f078b166a65afe29", "status": "PASS", "verdict": "R0_DESIGN_PASS", "versions_changed": [], "versions_dir": "backend/alembic/versions"}
MIGRATION_GATE=PASS
```

### 7.2 Dependency handoff 根目录执行

实际命令：

```text
C:\Python314\Scripts\uv.EXE run --isolated --no-project --python 3.11 --with-requirements backend/requirements-py311.lock python -B scripts/check_dependency_handoff.py --handoff artifacts/contracts/dependency-config-handoff.json --expected backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json --python-version-file .python-version --node-version-file frontend/.node-version --npmrc frontend/.npmrc --package frontend/package.json --lock frontend/package-lock.json --env-example .env.example
```

实际结果：exit code `0`，stderr 为空，状态为 `PASS_WITH_DEFERRED`。

```text
{"auth_ids": ["DEP-AUTH-01", "DEP-AUTH-02", "DEP-AUTH-03", "DEP-AUTH-04", "DEP-AUTH-05", "DEP-AUTH-06", "DEP-AUTH-07", "DEP-AUTH-08", "DEP-AUTH-09", "DEP-AUTH-10", "DEP-AUTH-11", "DEP-AUTH-12", "DEP-AUTH-13"], "base_commit": "ca99e0485fb12fbecbd565e79d81f745f8d4a748", "deferred": ["frontend-performance-entrypoint", "frontend-target-runtime-evidence"], "g0_g5_blocked_by_deferred": true, "r0_review": {"artifact": "artifacts/contracts/v3-independent-review.json", "document_bundle_sha256": "5b9b2e3f54bb910706a2d130ff564ebaf3e2292c9db3ace449b62cd5006fa55d", "verdict": "R0_DESIGN_PASS"}, "settings_counts": {"authorized": 54, "preserve_only": 47}, "status": "READY_WITH_DEFERRED", "versions": {"lockfileVersion": 3, "node": "18.20.8", "npm": "10.8.2", "packageManager": "npm@10.8.2", "python": "3.11", "rollupOverride": "4.22.0"}}
DEPENDENCY_HANDOFF=PASS_WITH_DEFERRED
DEFERRED=frontend-performance-entrypoint
DEFERRED=frontend-target-runtime-evidence
G0_G5_BLOCKED_BY_DEFERRED=true
```

### 7.3 Bundle、handoff 与已验证 JSON 不变性

根目录执行绑定的 review bundle：

- base commit：`ca99e0485fb12fbecbd565e79d81f745f8d4a748`
- `document_bundle_sha256`：`5b9b2e3f54bb910706a2d130ff564ebaf3e2292c9db3ace449b62cd5006fa55d`
- 三份文档 SHA-256：Implementation Contract `5fa5f570...58141`、PRD Supplement `120b6440...41551`、Source PRD `11e56e68...07ee`
- review verdict：`R0_DESIGN_PASS`
- review report SHA-256：`93f6e76ee0ff34581672d23371fc170a2c2e4a0e5482a281f078b166a65afe29`

根目录执行绑定的 handoff 摘要：

- phase=`post-handoff`，status=`READY_WITH_DEFERRED`
- `DEP-AUTH-01..13` 共 13 项；`DEP-AUTH-01..12` 共记录 16 个物理文件条目
- Settings：54 authorized + 47 preserve-only
- 版本：Python 3.11、Node 18.20.8、npm 10.8.2、lockfileVersion 3、Rollup override 4.22.0
- deferred：`frontend-performance-entrypoint`（owner=`frontend-acceptance`）与 `frontend-target-runtime-evidence`（owner=`ci-quality-bootstrap`）
- G0/G5 仍由 deferred 项阻断，没有放宽门禁

执行前后已验证输入 SHA-256 完全相同：

```text
artifacts/contracts/v3-independent-review.json=90106f147bc7709d929ba678ed96c45d4d4745533654f61cbb84f822cc64bf9f
artifacts/contracts/dependency-config-handoff.json=6b7ff01dd7e5426dbaa22b42826a15fc30a10d35d640974f5a66b52263db4e85
backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json=6cf8412697444fba1d3029abd797d028f0db4a5a34cefda3d22e1976653839ce
```

本轮没有修改上述三个已验证 JSON 的任何 hash 字段；只新增独立的 execution artifact 和本摘要。

### 7.4 非门控诊断失败

在正式根目录 `uv` 命令之前，曾尝试从外层隔离环境中再启动裸 `python -B scripts/check_dependency_handoff.py ...`；该嵌套 Python 未继承 `uv` ephemeral site-packages，exit code `1`，错误为 `ModuleNotFoundError: pydantic`。它不计为 gate execution，已在 `r0-gate-execution.json` 的 `superseded_diagnostic_attempt` 中如实记录。正式门禁只采用本节 7.1/7.2 两条 cwd=仓库根的直接 `uv` 命令，二者均 exit code `0`。
