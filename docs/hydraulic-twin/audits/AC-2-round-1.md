# AC-2 独立验收（round 1）：非法、超大、恶意文件安全拒绝

- 验收员：独立验收员 AC-2-1（只读；未修改任何生产代码或测试）
- 审计日期：2026-09-28
- 仓库 HEAD：`ca99e0485fb12fbecbd565e79d81f745f8d4a748`（工作树 dirty，审计开始时 157 条变更；本审计新建本文件，未改动其他文件）
- 结论：**PARTIAL（功能行为层已达标；AC 级机器证据不成立）**

---

## 0. 一句话结论

生产代码在 `/api/networks/import` 上确实做到了 22 个恶意/超大用例的 fail-closed 拒绝（本次亲跑 `40 passed`），**行为层无越权写入、无半成品**；但 AC-2 所需的 **quarantine/目录清理、前后 readiness 探针、CPU/memory 样本、G2/G6 同轮工件**四项**未实现或未运行**，且仓库自身 `matrix.json` 仍把 AC-2 标为 `BLOCKED`。因此 **AC-2 不可判 PASS**。

---

## 1. 冻结要求（来源逐条列出）

| # | 要求 | 出处 |
|---|---|---|
| R1 | 「AC-2 非法、超大、恶意文件明确拒绝且服务稳定」 | `docs/hydraulic-twin/SOURCE_PRD.md:33` |
| R2 | 非功能：「导入白名单、无任意文件读写」 | `docs/hydraulic-twin/SOURCE_PRD.md:31` |
| R3 | AC-2 判据：20 MiB+1、截断、zip、脚本、路径矩阵、深层/NaN GeoJSON、CPU/memory 样本 → 预期 400/413/415/422；无越界/半成品；目录清理；前后 readiness 正常 | `docs/hydraulic-twin/PRD_SUPPLEMENT.md:587` |
| R4 | 错误码矩阵：`>20MiB`→413 `INPUT_TOO_LARGE`；非 `.inp/.geojson`/magic 不符→415；`..`/盘符/UNC/symlink/junction→422 `UNSAFE_PATH`；INP 语法→422 `INVALID_INP`；GeoJSON 非 FC/NaN/过深→422 `INVALID_GEOJSON` | `docs/hydraulic-twin/PRD_SUPPLEMENT.md:306-315` |
| R5 | 「读取到 `limit+1` 立即中止并删除；用户文件名只作展示元数据，API 不返回绝对路径」 | `docs/hydraulic-twin/PRD_SUPPLEMENT.md:317` |
| R6 | 4.4 Import：「流式写 quarantine，读取到 20 MiB+1 立即 413」「finally 清理 quarantine/attempt workspace」 | `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1281,1287` |
| R7 | 「路径穿越、盘符、UNC、ADS、symlink/junction 一律 422 `UNSAFE_PATH`；格式白名单 415；大小 413」 | `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1289` |
| R8 | G2 必交：`network_jobs/v1/manifest.json`、`idempotency-ttl-v1.json`、`security/jwt-rbac-ratelimit-v1.json` + 「UNSAFE_PATH=422」+ JWT/RBAC/rate-limit 负向 | `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2482,2492` |
| R9 | AC 状态只能是 `PASS/FAIL/NOT RUN/BLOCKED`，PASS 需目标命令 exit 0 + artifact + hash | `docs/hydraulic-twin/PRD_SUPPLEMENT.md:580`；`IMPLEMENTATION_CONTRACT.md:2539-2554` |

---

## 2. 本次实际执行的命令与输出

> 解释器：`backend/.venv/Scripts/python.exe` = **Python 3.11.15**（非 PATH 上的 3.14.4，与 `VALIDATION_EVIDENCE.md:50-51` 记录一致）。

```text
$ backend/.venv/Scripts/python.exe -m pytest tests/acceptance/test_negative_security.py -q
40 passed, 1 warning in 0.82s

$ backend/.venv/Scripts/python.exe -m pytest tests/acceptance/ -q
61 passed, 1 warning in 4.76s

$ backend/.venv/Scripts/python.exe -m pytest tests/security/ -q -rs
49 passed, 1 warning in 1.38s        # 本轮 0 skip

$ backend/.venv/Scripts/python.exe -B ../scripts/verify_hydraulic_stack.py \
      --gate g2 --evidence-dir artifacts/hydraulic/_ac2_audit_tmp/g2
HYDRAULIC_STACK_STATUS=BLOCKED
HYDRAULIC_STACK_EXIT=2
missing_frozen_fixtures: backend/tests/fixtures/hydraulic/network_jobs/v1/manifest.json,
  backend/tests/fixtures/hydraulic/contracts/idempotency-ttl-v1.json,
  backend/tests/fixtures/hydraulic/security/jwt-rbac-ratelimit-v1.json
# 证据目录已删除；git status --porcelain | wc -l 回到 157
```

独立探针（`/tmp` 临时脚本，未落库）：

```text
# (a) 20 MiB 读边界（直接调用生产 _read_upload）
NETWORK_INPUT_MAX_BYTES = 20971520 (20.0 MiB)
size= 20971519 -> ACCEPTED
size= 20971520 -> ACCEPTED
size= 20971521 -> REJECTED 413 INPUT_TOO_LARGE
size= 22020096 -> REJECTED 413 INPUT_TOO_LARGE

# (b) readiness 前后（同进程 ASGI）
BEFORE /health -> 200 ; BEFORE /readyz -> 503
AFTER  /health -> 200 ; AFTER  /readyz -> 503
（stderr: Hydraulic schema readiness check failed: [Errno 11001] getaddrinfo failed —— 本机无 DB）

# (c) import/validate/seed 的 Idempotency-Key 强制
validate without key -> 422 VALIDATION_ERROR
seed    without key -> 422 VALIDATION_ERROR
validate with key    -> 404 NETWORK_NOT_FOUND（进入业务逻辑）
seed    with key     -> 403 FORBIDDEN（进入业务逻辑，operator 无 admin 权）

# (d) 单行超长 INP（1 MiB，单行）
413 {"code":"INPUT_TOO_LARGE",
     "detail":"INPUT_TOO_LARGE at <input> line 3: line exceeds 1000000 characters"}
```

---

## 3. 逐条验收结果

### 3.1 行为层（本次亲跑，`test_negative_security.py` 40 passed）

| 判据项 | 覆盖情况 | 证据 |
|---|---|---|
| 20 MiB+1 → 413 | ✅ 通过 | fixture `malicious_imports.json:167-173`；探针 (a) 确认边界恰为 `>20 MiB` |
| 截断 | ✅ 通过 | `truncated-inp`(422 `INVALID_INP`)、`truncated-zip`(415) |
| zip / 压缩炸弹 | ✅ 通过 | `zip-magic`、`truncated-zip`、`bounded-zip-ratio-bomb`（`test_negative_security.py:74-80` 真实 `ZIP_DEFLATED` 8 MiB 零字节） |
| 脚本 | ✅ 通过 | `script-content` → 422 `INVALID_INP` |
| 路径矩阵 | ✅ 通过（缺 symlink/junction） | `parent-traversal`/`nested-traversal`/`absolute-posix`/`windows-drive`/`windows-forward-slash`/`unc-path`/`ads-path`/`encoded-traversal`/`leading-dot`/`filename-space` 全部 422 `UNSAFE_PATH`；且**原始** Windows/UNC 文件名由 `main.py:1091-1138` 的 `PreserveRawMultipartFilename` 中间件在 starlette 改写前捕获，`test_negative_security.py:166-186` 用裸 multipart 字节流验证 |
| 深层/NaN GeoJSON | ✅ 通过 | `nan-geojson`、`deep-geojson`（1100 层嵌套）→ 422 `INVALID_GEOJSON`；重复 stable key `duplicate-stable-key`、`duplicate-fanout-resource-sample`（2000 feature）亦 422 |
| 400 分类 | ✅ 通过 | `empty-upload` → 400 `MALFORMED_REQUEST`（`hydraulic_routes.py:1061-1062`） |
| 格式白名单 415 | ✅ 通过 | `unsupported-extension`、三个 zip 用例；另有 magic 兜底 `hydraulic_routes.py:1063-1066` |
| JWT/RBAC/限流负向 | ✅ 通过 | 401 `AUTH_REQUIRED`、403 `FORBIDDEN`、422 缺 key、409 `IDEMPOTENCY_KEY_REUSED`、429 `RATE_LIMITED` + `Retry-After: 60`、7 类 JWT 伪造矩阵 |
| 无越权状态写入 | ✅ 通过（**仅内存 adapter**） | `test_negative_security.py:116-123,160` 比较 `jobs/imports/idempotency/networks/revisions` 五个字典长度；实现侧 `_read_upload` 在 `_idempotent_lookup` 之前抛错（`hydraulic_routes.py:1427-1431`），拒绝路径不消耗幂等键 |

### 3.2 缺口（按严重度）

| ID | 严重度 | 缺口 | 证据 | 能否由代码修复 |
|---|---|---|---|---|
| G-1 | **阻断 AC** | **G2 门禁 BLOCKED**：三份冻结 fixture 缺失，`--gate g2` 退出码 2 | 本次实跑输出（见 §2）；`IMPLEMENTATION_CONTRACT.md:2482` | **能**（补 fixture + manifest + harness 命令） |
| G-2 | **阻断 AC** | **AC-2 必需工件不存在**：`artifacts/hydraulic/g2/result.json`、`g6/postgis/result.json`、`g6/portable/result.json` 三者全 MISSING；`matrix.json:64-69` 明确要求 | 文件系统直接检查；`matrix.json:64-69` | **否**（需在目标环境实跑 G2/G6 生成） |
| G-3 | **阻断 AC** | **quarantine 与目录清理未实现**。契约要求「流式写 quarantine」+「finally 清理 quarantine/attempt workspace」，代码 `hydraulic_routes.py:1049-1060` 把整个文件读进内存 `list` 后 `b"".join`，**磁盘上不存在任何 quarantine/part/sandbox 目录**，因而也谈不上清理。`backend/app/` 全库 `quarantine` 仅命中 `hydraulic_models.py:267` 一个枚举值 | `hydraulic_routes.py:1049-1060`；`IMPLEMENTATION_CONTRACT.md:1281,1287` | **能，但不是小改**（需新增流式落盘 + 尝试工作区 + sweeper 子系统） |
| G-4 | **阻断 AC** | **前后 readiness 探针缺失**。R3 判据明写「前后 readiness 正常」，`contracts/operations.md:370` 亦列 `/health` 与 `/readyz` 前后探针。测试只断言 `GET /api/networks` 返回 200（`test_negative_security.py:162-163`），**没有** `/health`、`/readyz` 探针，也没有进程存活探针。本环境 `/readyz` 实测 503（无 DB），无法产生该证据 | `test_negative_security.py:162-163`；探针 (b) | 测试代码**能**补；实际 PASS 需 live 栈 → 环境项 |
| G-5 | 中 | **CPU/memory 样本缺失**。R3 判据明列，`backend/tests/acceptance/` 与 `backend/tests/security/` 全库无 `psutil`/`rss`/CPU 采样 | `grep -rni "cpu\|memory\|rss\|psutil" backend/tests/acceptance backend/tests/security` 仅命中 `in_memory` 字样 | **能**（补采样断言） |
| G-6 | 中 | **symlink/junction 用例缺失**。R7 明列 symlink/junction→422 `UNSAFE_PATH`；`malicious_imports.json` 22 个用例中无此项。`test_upload_paths.py:110` 的 symlink 只覆盖**模型**上传路径，不覆盖水力 import | `malicious_imports.json` 全文；`test_upload_paths.py:110` | **能**（补 fixture；注：当前 import 路径不落盘，实为低风险） |
| G-7 | 中 | **状态未写入断言只覆盖内存 adapter**。`HydraulicApiStore.__init__` 硬编码 `durable_backend = False`（`hydraulic_routes.py:294-296`），`_require_durable_api_store` 仅在 `DEPLOYMENT_ENVIRONMENT=="production"` 时 503（`:327-335`）。因此「无半成品」在真实 `network_jobs` 表上**未被证明** | `hydraulic_routes.py:294-296,327-335` | **能**（接 durable repository）——与 `reviews/backend-acceptance-tests.md:25-33` F-01 同一根因 |
| G-8 | 低（可观测性） | **单行超长文件被误分类为 `INPUT_TOO_LARGE`**。1 MiB 的单行 INP 返回 413 `INPUT_TOO_LARGE`（来自 parser 的 `line exceeds 1000000 characters` 护栏），而 R4 规定 413 专属于「超过 20 MiB」。拒绝行为正确，但错误码语义与冻结矩阵不符，会误导客户端重试策略 | 探针 (d)；`_preflight_import` 的 `status = 413 if exc.code == "INPUT_TOO_LARGE"`（`hydraulic_routes.py:1086`） | **能**（拆分为独立 code） |

### 3.3 仓库自述与文档陈旧（按要求，不采信文档勾选）

| # | 位置 | 声称 | 实测 | 判定 |
|---|---|---|---|---|
| S-1 | `docs/hydraulic-twin/implementation/backend-acceptance-tests.md:99` | 「AC-2 **verified + 2 strict xfail**；Windows/UNC 文件名当前分别落入 415/`INVALID_INP`」 | acceptance 目录**无任何 xfail**（`matrix_gate.py:219-221` 反向禁止）；`windows-drive`/`unc-path` 现返回 **422 `UNSAFE_PATH`** 且测试通过 | **陈旧**，应更新 |
| S-2 | `backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json:50` | 理由「原始 Windows 盘符文件名仍被降级为 basename；重复 stable key 被接受」 | 同上，两项均已不成立 | **理由陈旧**；但 `status: "BLOCKED"`（`:49`）因 G-1～G-4 依然**正确** |
| S-3 | `docs/hydraulic-twin/contracts/operations.md:370` | 「现有模型上传代码还暴露绝对路径（`routes.py:204-246`）」 | `routes.py:204` 现是 `health_check`；模型上传在 `:352-425`，响应返回 `save_path.name`（`:421`），**只给 basename** | **行号与结论双双陈旧** |
| S-4 | `docs/hydraulic-twin/reviews/backend-acceptance-tests.md:35-52`（F-02） | 「`test_negative_security.py:30-49` 对 `windows-drive`/`unc-path` 预置 `strict xfail`」「fixture 只有 14 个案例，缺截断/真 zip bomb/重复 key」 | fixture 已 22 例，截断/真 zip bomb/重复 key 齐备，xfail 已移除 | **陈旧**（结论 BLOCKED 仍成立） |
| S-5 | `docs/hydraulic-twin/reviews/security-hardening.md:33-40`（HIGH-1） | 「水力 API 的 `_authenticate()` 直接调用 `decode_access_token()`，未校验 `is_active`」 | `hydraulic_routes.py:126` 已改为 `authenticate_access_token(token, db)`，`core/security.py:270` 有 `if not user.is_active` | **陈旧** |

**关于 `VALIDATION_EVIDENCE.md` 的两处必须点名：**

- `VALIDATION_EVIDENCE.md:209` 记录 `backend-acceptance-tests pytest` 有 3 条 AC-2 失败（`duplicate-stable-key`、`duplicate-fanout-resource-sample`、`windows-drive`）。**本轮实跑该文件 40 passed，三条均已转绿。** 该证据串对应的是更早的工作树状态，**已过期**，不得据此判定当前 AC-2 失败，也不得据当前测试转绿判定 AC-2 PASS。
- `VALIDATION_EVIDENCE.md:587`（§9 未运行清单）把 AC-1～AC-8 记为 NOT RUN；本审计只覆盖 AC-2，其余未复评。

---

## 4. AC 判定

- **行为层：通过。** 22 个恶意/超大用例在生产路由上 fail-closed，错误码与冻结矩阵一致，幂等键/认证/RBAC/限流边界齐全，且 `main.py:1091-1138` 确实在 starlette 改写前捕获原始文件名——这正是 `reviews/backend-acceptance-tests.md` F-02 当年指出的核心漏洞，现已修复。
- **AC 级证据层：不通过。** R3 的五项判据中「CPU/memory 样本」「目录清理」「前后 readiness 正常」三项无实现或无证据；R8 的 G2 门禁实测 BLOCKED；R9 要求的同轮 G2/G6 工件全缺。仓库自身 `matrix.json:49` 记 `BLOCKED`，与本审计一致。
- **建议状态：保持 `BLOCKED`，不要改写为 PASS。**

### 解除阻断的最短路径

1. 补 G2 三份冻结 fixture + manifest + 可执行 harness 命令 → 复跑 `--gate g2` 取 exit 0（G-1）。
2. 落地 quarantine 流式落盘与 finally 清理（G-3，工作量最大的一项）。
3. 测试中加 `/health`、`/readyz` 前后探针 + 进程存活探针 + CPU/memory 采样 + symlink/junction 用例（G-4/G-5/G-6）。
4. 在目标环境跑出 G2 + G6（portable/postgis）同轮工件（G-2，**非代码项**）。
5. 修 S-1～S-5 五处陈旧文档。

---

## 5. 本审计未做的事（不得被当作已验证）

- 未启动真实 uvicorn / docker compose 进程；`/readyz` 的 live 语义与进程存活未验证。
- 未连接 PostgreSQL/Timescale，`network_jobs` 表上的「无半成品」未验证（G-7）。
- 未复跑全量后端 pytest、Black/isort/Flake8、前端 Vitest/build、Compose 门禁、5k/10k 性能门禁——本审计只跑 AC-2 相关文件与 G2 门禁。
- 未复评 AC-1、AC-3～AC-8。
- 20 MiB 端到端 HTTP 探针（用真实 INP 结构、逐行构造）因构造 ~35 万行 junction 使 parser 耗时过长而中止；边界结论改由 §2(a) 直接调用生产 `_read_upload` 得出，该函数即 `_read_upload` 内 413 的唯一产生点（`hydraulic_routes.py:1052-1059`）。
- 未修改任何生产代码或测试；仅新建本文件，并已删除临时 G2 证据目录（`git status --porcelain | wc -l` 前后均为 157）。
