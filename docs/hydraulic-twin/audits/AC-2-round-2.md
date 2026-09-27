# AC-2 独立验收（round 2）：非法、超大、恶意文件安全拒绝

- 验收员：独立验收员 AC-2-2（只读；未修改任何生产代码或测试）
- 审计日期：2026-09-28
- 仓库 HEAD：`ca99e0485fb12fbecbd565e79d81f745f8d4a748`（工作树 dirty，审计开始时 160 条变更；本审计仅新建本文件，临时工件已清理）
- 上一轮：本目录 `AC-2-round-1.md`（round 1，结论 PARTIAL，遗留 G-1～G-8）
- 结论：**PARTIAL（行为层持续达标；AC 级证据层未推进，且新增磁盘持久化缺口 N-1）**

---

## 0. 一句话结论

恶意/超大文件在 `/api/networks/import` 上仍然 fail-closed 拒绝（本次亲跑 `40 passed`，并新增磁盘状态探针证实恶意请求**零落盘残留**）；但 round 1 的 **G-1～G-8 八项缺口无一项关闭**，且 04:52 新增的 `_stage_job_payload` 让 API 开始把已接收载荷写入 `HYDRAULIC_IMPORT_ROOT` 而**全库无任何删除路径**，使「目录清理」判据从「无法评估」恶化为「明确不成立」。**AC-2 维持 BLOCKED，不得记 PASS。**

---

## 1. 冻结要求（来源逐条列出）

| # | 要求 | 出处 |
|---|---|---|
| R1 | 「AC-2 非法、超大、恶意文件明确拒绝且服务稳定」 | `docs/hydraulic-twin/SOURCE_PRD.md:33` |
| R2 | 非功能：「导入白名单、无任意文件读写」 | `docs/hydraulic-twin/SOURCE_PRD.md:31` |
| R3 | 判据：20 MiB+1、截断、zip、脚本、路径矩阵、深层/NaN GeoJSON、CPU/memory 样本 → 预期 400/413/415/422；无越界/半成品；**目录清理**；前后 readiness 正常 | `docs/hydraulic-twin/PRD_SUPPLEMENT.md:587` |
| R4 | 错误码矩阵：`>20MiB`→413 `INPUT_TOO_LARGE`；非白名单/magic 不符→415；`..`/盘符/UNC/symlink/junction→422 `UNSAFE_PATH`；INP 语法→422 `INVALID_INP`；GeoJSON→422 `INVALID_GEOJSON` | `docs/hydraulic-twin/PRD_SUPPLEMENT.md:306-315` |
| R5 | 「读取到 `limit+1` 立即中止并删除；用户文件名只作展示元数据，API 不返回绝对路径」 | `docs/hydraulic-twin/PRD_SUPPLEMENT.md:317` |
| R6 | 4.4 Import：「流式写 quarantine，读取到 20 MiB+1 立即 413」「**finally 清理 quarantine/attempt workspace**」 | `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1281,1287` |
| R7 | 「路径穿越、盘符、UNC、ADS、symlink/junction 一律 422 `UNSAFE_PATH`；格式白名单 415；大小 413」 | `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1289` |
| R8 | G2 必交：`network_jobs/v1/manifest.json`、`idempotency-ttl-v1.json`、`security/jwt-rbac-ratelimit-v1.json` | `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2482,2492` |
| R9 | AC 状态只能 `PASS/FAIL/NOT RUN/BLOCKED`；PASS 需目标命令 exit 0 + artifact + hash | `docs/hydraulic-twin/PRD_SUPPLEMENT.md:580`；`IMPLEMENTATION_CONTRACT.md:2539-2554` |

---

## 2. 本次实际执行的命令与输出

> 解释器：`backend/.venv/Scripts/python.exe` = **Python 3.11.15**（`backend/.venv/Scripts/python.exe -V` 实测；PATH 上的 `python` 为 3.14.4，未使用）。

```text
$ backend/.venv/Scripts/python.exe -m pytest tests/acceptance/test_negative_security.py -q
40 passed, 1 warning in 0.98s

$ backend/.venv/Scripts/python.exe -m pytest tests/acceptance/ -q
65 passed, 1 warning in 4.79s          # round 1 为 61；新增 4 条来自 test_import_staging_contract.py

$ backend/.venv/Scripts/python.exe -m pytest tests/security/ -q -rs
49 passed, 1 warning in 1.30s          # 本轮 0 skip

$ backend/.venv/Scripts/python.exe -B scripts/verify_hydraulic_stack.py \
      --gate g2 --evidence-dir artifacts/_ac2_r2_g2
HYDRAULIC_STACK_STATUS=BLOCKED
HYDRAULIC_STACK_EXIT=2
missing frozen fixtures:
  backend/tests/fixtures/hydraulic/network_jobs/v1/manifest.json,
  backend/tests/fixtures/hydraulic/contracts/idempotency-ttl-v1.json,
  backend/tests/fixtures/hydraulic/security/jwt-rbac-ratelimit-v1.json
# 临时证据目录已删除；git status --porcelain | wc -l 回到 160
```

### 2.1 独立磁盘状态探针（临时脚本置于系统 Temp，未落库）

复用 `tests/conftest.py:48-56` 的 `ASGITransport` + 临时 SQLite 装配，把 `HYDRAULIC_IMPORT_ROOT` 重定向到临时目录，实测输出：

```json
{
  "BEFORE /health": 200,
  "BEFORE /readyz": 503,
  "VALID status": 202,
  "VALID staged_after_request": ["a18c691f-6ef2-42a4-9bcb-24108518e7f7.inp"],
  "hostile": [
    {"case": "oversize_20MiB_plus1", "status": 413, "code": "INPUT_TOO_LARGE",   "staged_delta": []},
    {"case": "zip_magic",            "status": 415, "code": "UNSUPPORTED_IMPORT_FORMAT", "staged_delta": []},
    {"case": "windows_drive",        "status": 422, "code": "UNSAFE_PATH",       "staged_delta": []},
    {"case": "traversal",            "status": 422, "code": "UNSAFE_PATH",       "staged_delta": []}
  ],
  "FINAL staging contents": ["a18c691f-6ef2-42a4-9bcb-24108518e7f7.inp"],
  "FINAL staging bytes": 409,
  "AFTER /health": 200,
  "AFTER /readyz": 503,
  "long_line status": 413,
  "long_line code": "INPUT_TOO_LARGE"
}
```

---

## 3. 逐条验收结果

### 3.1 行为层：通过（本轮亲跑，附独立探针）

| 判据项 | 结论 | 证据 |
|---|---|---|
| 20 MiB+1 → 413 | ✅ | fixture `oversized`（`test_negative_security.py:70-71` 构造 `b"x" * (20*1024*1024+1)`）；探针 `oversize_20MiB_plus1` 独立复现 413 `INPUT_TOO_LARGE` |
| 截断 | ✅ | `truncated-inp`→422 `INVALID_INP`、`truncated-zip`→415 |
| zip / 压缩炸弹 | ✅ | `zip-magic`、`truncated-zip`、`bounded-zip-ratio-bomb`（`test_negative_security.py:74-80` 真实 `ZIP_DEFLATED` 8 MiB 零字节） |
| 脚本 | ✅ | `script-content` → 422 `INVALID_INP` |
| 路径矩阵 | ✅（缺 symlink/junction） | 10 个路径用例全 422 `UNSAFE_PATH`；**原始** Windows/UNC 文件名由 `main.py:1091-1170` 的 `PreserveRawMultipartFilename` 中间件在 starlette 改写前捕获（`:1125-1133` 写入 scope，`:1170` 注册），本轮确认该中间件仍在位 |
| 深层/NaN GeoJSON | ✅ | `nan-geojson`、`deep-geojson`（1100 层）、`duplicate-stable-key`、`duplicate-fanout-resource-sample`（2000 feature） |
| 400 分类 | ✅ | `empty-upload` → 400 `MALFORMED_REQUEST`（`hydraulic_routes.py:1140-1141`） |
| 格式白名单 415 | ✅ | `unsupported-extension` + 3 个 zip 用例；magic 兜底 `hydraulic_routes.py:1142-1145` |
| JWT/RBAC/限流负向 | ✅ | 401/403/422 缺 key/409 幂等复用/429 + `Retry-After`/7 类 JWT 伪造矩阵 |
| **恶意请求零落盘**（本轮新增验证） | ✅ | 探针 4 个恶意用例 `staged_delta` 全为 `[]`；`staging_after_hostile` 与 `VALID` 后一致 |
| **服务稳定**（本轮新增验证） | ✅ | 恶意流量前后 `/health` 均为 200；`/readyz` 前后均 503（本机无 DB，属环境项，非回归） |
| 无越权状态写入 | ✅（**仅内存 adapter**） | `test_negative_security.py:160,186` 比较 `_state_counts()`；实现侧拒绝发生在 `_idempotent_lookup` 之前 |

fixture 实为 **24 个用例**（`malicious_imports.json`，mtime 2026-09-25 18:04，**本轮未变**），由 `_param_cases()`/`_raw_multipart_cases()` 按 `multipart` 字段分流（`test_negative_security.py:39-52`）全部参数化。**round 1 报告记为「22 个」系计数有误，本轮更正。**

### 3.2 缺口（round 1 的 G-1～G-8 逐条复核 + 本轮新增）

| ID | 严重度 | 缺口 | 本轮状态 | 证据 | 能否由代码修复 |
|---|---|---|---|---|---|
| G-1 | **阻断 AC** | G2 门禁 BLOCKED，三份冻结 fixture 缺失 | **未修复** | 本轮实跑 `--gate g2` → `HYDRAULIC_STACK_EXIT=2`，`missing_frozen_fixtures` 三项；`backend/tests/fixtures/hydraulic/` 下仅存 `acceptance/ compose/ contracts/ imports/`，无 `network_jobs/`、无 `security/`，`contracts/` 仅 `dependency-handoff-v1.json` | **能**（补 fixture + manifest + harness） |
| G-2 | **阻断 AC** | AC-2 必需工件 `g2/result.json`、`g6/postgis/result.json`、`g6/portable/result.json` 全缺 | **未修复** | 文件系统直接检查三者均 MISSING；`artifacts/hydraulic/` 下无 `g2/`、`g6/` 目录；`matrix.json:64-69` 明确 `required_artifact_suffixes` | **否**（需在目标环境实跑 G2/G6 生成） |
| G-3 | **阻断 AC** | quarantine 流式落盘 + `finally` 清理 | **未修复，且方向恶化** | `_read_upload` 仍全内存累积（`hydraulic_routes.py:1128-1139`，`chunks: list[bytes]` + `b"".join`），磁盘上仍不存在任何 quarantine 目录；`grep -rni quarantine backend/app/` 仅命中 `hydraulic_models.py:267` 的枚举值 | **能**，但工作量最大（新增流式落盘 + 尝试工作区 + sweeper） |
| G-4 | **阻断 AC** | 测试缺 `/health`、`/readyz` 前后探针与进程存活探针 | **未修复** | `test_negative_security.py:162-163` 仍只断言 `GET /api/networks` 返回 200；`grep -rn "readyz\|/health" backend/tests/acceptance/ backend/tests/security/` 在**源码中零命中**（仅 `.pyc` 二进制残留） | 测试代码**能**；实际 PASS 需 live 栈（环境项） |
| G-5 | 中 | CPU/memory 样本缺失 | **未修复** | `grep -rni "cpu\|memory\|rss\|psutil" backend/tests/acceptance backend/tests/security` 源码命中全部为 `in_memory` 词形误配 | **能**（补采样断言） |
| G-6 | 中 | symlink/junction 用例缺失 | **未修复** | 24 个 fixture 用例中无 symlink/junction；`test_upload_paths.py:110-125` 的 symlink 仅覆盖**模型**上传路径（且本机 symlink 可用，本轮 security 套件 0 skip） | **能**（补 fixture） |
| G-7 | 中 | 「无半成品」仅在内存 adapter 上被证明 | **未修复** | `hydraulic_routes.py:311` 仍硬编码 `self.durable_backend = False`；`_require_durable_api_store`（`:341-343`）仅在 `DEPLOYMENT_ENVIRONMENT == "production"` 时 503。真实 `network_jobs` 表上的无半成品**未验证** | **能**（接 durable repository） |
| G-8 | 低 | 单行超长 INP 被误分类为 413 `INPUT_TOO_LARGE` | **未修复** | 探针 `long_line`（1,000,001 字符单行，远小于 20 MiB）→ `413 INPUT_TOO_LARGE`；映射逻辑仍在 `hydraulic_routes.py:1165`（`status = 413 if exc.code == "INPUT_TOO_LARGE" else 422`）。按 R4，413 专属于「超过 20 MiB」，该场景应为 422 `INVALID_INP` | **能**（拆分独立 code） |
| **N-1** | **高（本轮新增）** | **已接收载荷被写入 `HYDRAULIC_IMPORT_ROOT` 且全库无任何删除路径** | **新引入，且本轮在仓库内实证复现** | `_stage_job_payload`（`hydraulic_routes.py:982-1017`）write-then-rename 落盘为 `<job_id>.inp`；worker 侧 `read_staged_input`（`network_job_worker.py:580-617`）只读不删；`grep -n "unlink\|rmtree\|remove(" backend/app/workers/network_job_worker.py` 全库**仅 `:941` 启动探针 marker** 一处；`grep -rni "sweep\|prune\|retention" backend/app/` 无任何 staging 清理项。探针实证：合法导入后 `FINAL staging contents` 仍为该文件（409 bytes）。**另：跑完 `pytest tests/acceptance/` 后，仓库内 `backend/hydraulic/import/` 残留 2 个 `.inp` 未被删除（详见下方补充说明）** | **能**（补 worker 成功后 unlink + TTL sweeper + 启动期残留清理 + 隔离测试写入目录） |

#### N-1 补充说明（为何是 AC-2 缺口而非 AC-1 问题）

- R6 明文要求「**finally 清理 quarantine/attempt workspace**」，R3 判据明列「**目录清理**」。当前两者均不成立。
- round 1 记录「磁盘上不存在任何 quarantine/part/sandbox 目录」，当时该判据**无法评估**；本轮落盘面扩大后，同一判据变为**明确失败**。
- 增长无上界：每个被接受的任务写入至多 `NETWORK_INPUT_MAX_BYTES`（`config.py:436`）字节，staging 根**无容量上限、无 TTL、无条目数上限**。受认证与限流约束（429 已在测试覆盖），故定为高而非阻断。
- 卫生风险：默认根为**仓库相对路径** `./backend/hydraulic/import`（`config.py:271`），且 `.gitignore` 中 `hydraulic` 零命中（`grep -c "hydraulic" .gitignore` → `0`）。
- **本轮在仓库内实证复现 N-1（非仅代码推理）**：本审计执行 `pytest tests/acceptance/` 之后，`backend/hydraulic/import/` 中留下 2 个未被删除的载荷文件，且被 git 视为未跟踪：

  ```text
  $ ls -la --time-style=full-iso backend/hydraulic/import/
  28a95eb7-ff11-4cf9-9abd-1214f917d011.inp   409 bytes   2026-09-28 05:09:44
  7a60eb30-be87-4dca-aec3-122829773f42.inp   761 bytes   2026-09-28 05:09:44
  $ git status --porcelain backend/hydraulic/
  ?? backend/hydraulic/
  ```

  责任测试为 `tests/acceptance/test_acceptance_matrix.py:264` `test_ac1_in_memory_adapter_valid_inp_import_is_accepted_and_replayed`——它直接 POST `/api/networks/import`（`:272-275,284-287`）而**未**像 `test_import_staging_contract.py:53-61` 的 `staging_root` fixture 那样 monkeypatch 到 `tmp_path`，因此写入默认根；测试会话结束后文件仍在。
- 另需注意：默认根为相对路径，API 与 worker 若 CWD 不同将读到不同目录，导致 `STAGED_INPUT_MISSING`。该风险同样未被任何测试覆盖（相关测试要么重定向到 `tmp_path`，要么如上直接落在仓库内）。

### 3.3 仓库自述与文档陈旧（不采信文档勾选）

| # | 位置 | 声称 | 实测 | 判定 |
|---|---|---|---|---|
| S-1 | `docs/hydraulic-twin/implementation/backend-acceptance-tests.md:99` | 「AC-2 本行原写『verified + 2 strict xfail』」 | acceptance 目录确无 xfail；`windows-drive`/`unc-path` 现返回 422 `UNSAFE_PATH` 并通过 | **仍陈旧**，应更新；同行的 `BLOCKED` 结论**正确** |
| S-2 | `backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json:49-50` | 理由「原始 Windows 盘符文件名仍被降级为 basename；重复 stable key 被接受」 | 两项均已不成立（422 `UNSAFE_PATH`；两个 duplicate 用例转绿） | **理由陈旧**；`status: "BLOCKED"` 依然**正确** |
| S-3 | `docs/hydraulic-twin/contracts/operations.md:370` | 「现有模型上传代码还暴露绝对路径（`routes.py:204-246`）」 | `routes.py:204` 现为 `health_check`；模型上传在 `:354-425`，`:422` 返回 `save_path.name`（仅 basename） | **行号与结论双双陈旧** |
| S-4 | `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2548` | 「AC-2 \| NOT RUN（无实现）」 | 与本审计 PARTIAL/BLOCKED 结论方向一致，但「无实现」措辞不准确（行为层已实现并通过） | **措辞陈旧**，方向正确 |
| S-5 | `docs/hydraulic-twin/VALIDATION_EVIDENCE.md:209` | 记录 AC-2 有 3 条 pytest 失败（`duplicate-stable-key`、`duplicate-fanout-resource-sample`、`windows-drive`） | 本轮实跑 `40 passed`，三条均已转绿 | **已过期**，不得据此判定当前 AC-2 失败，亦不得据当前转绿判定 PASS |
| S-6 | `docs/hydraulic-twin/audits/AC-2-round-1.md:12,90-96` | 「22 个恶意/超大用例」 | fixture 实为 **24**（mtime 2026-09-25 18:04，未变） | **计数有误**，本轮更正 |

**G-3/N-1 相关补充**：`docs/hydraulic-twin/reviews/security-hardening.md:33-40`（HIGH-1）关于 `_authenticate()` 未校验 `is_active` 的指控，round 1 已判陈旧；本轮未复评该项（不在 AC-2 范围）。

---

## 4. AC 判定

- **行为层：通过。** 24 个恶意/超大用例在生产路由上 fail-closed，状态码与冻结矩阵一致；**本轮新增证据**证明恶意请求零落盘、且恶意流量前后 `/health` 恒为 200。`PreserveRawMultipartFilename` 仍在 starlette 改写前捕获原始文件名，这是 AC-2 路径矩阵能通过的关键。
- **AC 级证据层：不通过。** R3 五项判据中「目录清理」本轮**由无法评估恶化为明确失败**（N-1），「CPU/memory 样本」「前后 readiness 正常」两项仍无实现或无证据；R7 的 symlink/junction 仍无用例；R8 的 G2 门禁实测 `BLOCKED`/exit 2；R9 要求的同轮 G2/G6 工件全缺。
- **round 1 的八项缺口 G-1～G-8 本轮无一关闭。**
- **建议状态：维持 `BLOCKED`（与 `matrix.json:49` 一致），不得改写为 PASS。**

### 解除阻断的最短路径（较 round 1 更新）

1. 补 G2 三份冻结 fixture + manifest + 可执行 harness 命令 → 复跑 `--gate g2` 取 exit 0（G-1）。
2. **新增（优先级高于 round 1 的第 2 项）**：在 `read_staged_input` 成功后 unlink，并为 `HYDRAULIC_IMPORT_ROOT` 增加 TTL sweeper 与容量上限；把默认相对路径纳入 `.gitignore` 并补一条默认路径的测试（N-1，AC-2 新增高危）。
3. 落地 quarantine 流式落盘与 `finally` 清理（G-3，工作量仍最大）。
4. 测试中加 `/health`、`/readyz` 前后探针 + 进程存活探针 + CPU/memory 采样 + symlink/junction 用例（G-4/G-5/G-6）。
5. 在目标环境跑出 G2 + G6（portable/postgis）同轮工件（G-2，**非代码项**）。
6. 拆出单行超长的独立错误码，消除 413 语义误用（G-8）。
7. 修 S-1～S-6 六处陈旧文档。

---

## 5. 本审计未做的事（不得被当作已验证）

- 未启动真实 uvicorn / docker compose 进程；`/readyz` 的 live 语义与进程存活**未验证**。探针中的 `/readyz` 503 系本机无 PostgreSQL/Timescale 所致（stderr：`Hydraulic schema readiness check failed: [Errno 11001] getaddrinfo failed`），只证明「前后一致、未从 200 掉到 5xx」。
- 未连接 PostgreSQL/Timescale，`network_jobs` 表上的「无半成品」**未验证**（G-7）。
- 未实测 staging 根的磁盘耗尽速率（只证明无清理逻辑存在并观察到残留，未做压力/容量实验）。
- 未复跑全量后端 pytest、Black/isort/Flake8/mypy、前端 Vitest/build、Compose 门禁、5k/10k 性能门禁——本审计只跑 AC-2 相关套件与 G2 门禁。
- 未复评 AC-1、AC-3～AC-8。（注：`test_import_staging_contract.py` 属 AC-1 范围，本审计仅在 N-1 语境下引用其 staging 事实。）
- 未修改任何生产代码或测试；仅新建本文件。临时 G2 证据目录与探针脚本均在系统 Temp 且已删除。**由本审计的 `pytest tests/acceptance/` 运行产生的 `backend/hydraulic/import/` 下 2 个残留 `.inp` 已在取证记录后删除**，以免把测试残留留在工作树；`git status --porcelain | wc -l` 已回到审计开始时的基线。
