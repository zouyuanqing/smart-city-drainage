# AC-8 独立验收（round-1）

> 验收员：独立验收员-1（只读，不改实现代码）
> 验收时间：2026-09-28（UTC 运行时间戳见各条命令输出）
> 仓库根：`C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage`
> 目标提交：`ca99e048`（`git rev-parse --short=8 HEAD`），**工作树 dirty（157 个改动文件）**
> RUN_ID（本轮）：`AC8-audit-20260927T191252Z-ca99e048`

## 0. 结论

**AC-8 = FAIL**（不是 NOT RUN）。

判据来源：`SOURCE_PRD.md:33`「AC-8 全新 docker compose 可复现 README 演示」；
`PRD_SUPPLEMENT.md:593` 通过判据「空卷可复现；无 latest/固定 secret；演示全链路；恢复 hash/API 校验通过」；
`IMPLEMENTATION_CONTRACT.md:2537` G6「postgis 与 portable 各自独立命令/evidence/fresh volume；
两者均通过 README demo、backup/restore、old API/SSE zero regression，**缺任一 profile 即 FAIL**」。

本轮**实测通过**的部分：空卷 fresh Compose 确实能起（portable，exit 0），水力 schema 完整迁移。
本轮**实测失败**的部分有两条各自独立、单独即可判 FAIL 的阻断：postgis profile 在根 Compose 结构上不存在；
G6 冻结入口**根本不执行** backup→destroy→restore。

与既有文档的差异：`IMPLEMENTATION_CONTRACT.md:2554` 记「AC-8 NOT RUN；recon 报告 Docker daemon 不可用」。
**本轮 Docker daemon 可用**，我实际跑了 G6。NOT RUN 这个理由已不成立，但换成 FAIL。

---

## 1. 本轮实际执行的命令（全部为「我跑的」，非转述）

### 1.1 环境

```bash
$ docker version ; docker compose version
Client: Version: 29.4.2 / API 1.54 / OS: windows/amd64
Server: Docker Desktop 4.72.0 (225998) / Engine 29.4.2 / OS: linux/amd64
Docker Compose version v5.1.3
```
→ daemon **可用**。文档记录的「Docker daemon 不可用」阻断在本轮不成立。

```bash
$ git rev-parse --short=8 HEAD            # ca99e048
$ git status --porcelain | wc -l          # 157
```

### 1.2 G6 config —— portable（PASS，但工具自述不是 AC-8 证据）

```bash
$ python scripts/verify_hydraulic_stack.py --gate g6 --profile portable --mode config \
    --evidence-dir ".runtime/evidence/ac8/g6-portable" --run-id "$RUN_ID"
HYDRAULIC_STACK_STATUS=PASS   HYDRAULIC_STACK_EXIT=0
ARTIFACT_SHA256=165d38d630a76d1ca4abb9aa695988c0032e2d10121c3caa9b3da14614182610
```
报告内 `"scope_note": "Compose config is a configuration check only; it is not live G6 or AC-8 evidence."`
→ 入口自己不把 config 结果当 AC-8 证据，这个自我约束是对的。

### 1.3 G6 config —— postgis（**FAIL**）

```bash
$ python scripts/verify_hydraulic_stack.py --gate g6 --profile postgis --mode config \
    --evidence-dir ".runtime/evidence/ac8/g6-postgis" --run-id "$RUN_ID"
HYDRAULIC_STACK_STATUS=FAIL   HYDRAULIC_STACK_EXIT=1
STATIC_FAILURES: [
  "postgis profile does not select an explicit PostGIS image",
  "postgis profile renders the portable default postgres image"
]
ARTIFACT_SHA256=e66967cc8d53b84d8601ad33392db972fd1abf15da8c3e1d8c48125f997fef82
```

### 1.4 G6 **live** —— portable（PASS，隔离 project，已清理）

为避免 `down -v` 删掉本机默认 project `smart-city-drainage` 的 9 个既有卷
（入口 `_compose_command` 不带 `--project-name`，`run_command` 又继承 `os.environ`，
见 `scripts/verify_hydraulic_stack.py:249-251`），我用 `COMPOSE_PROJECT_NAME=ac8audit-portable` 隔离后执行：

```bash
$ export COMPOSE_PROJECT_NAME=ac8audit-portable
$ python scripts/verify_hydraulic_stack.py --gate g6 --profile portable --mode live \
    --live-timeout-seconds 2400 --evidence-dir ".runtime/evidence/ac8/g6-portable-live" --run-id "$RUN_ID"
HYDRAULIC_STACK_STATUS=PASS   LIVE_EXIT=0
ARTIFACT_SHA256=28d677b707b91e5779c8e9a5d2f25d2c59182ef4aab28cb793d89b1b696c4b01
```

子步骤实测：

| 步骤 | exit | 耗时 | 观测 |
|---|---|---|---|
| `compose config --quiet` | 0 | 0.24s | — |
| `compose config --format json` | 0 | 0.42s | — |
| static-contract（无命令） | 0 | 0s | `static_failures: []` |
| 冻结 fixture 存在性 | 0 | 0s | 2 个 fixture hash 校验通过 |
| **`compose down -v --remove-orphans`** | **0** | 0.36s | 空卷 |
| **`compose up --build -d --wait`** | **0** | **70.4s** | 15 个服务全部 Healthy，`migrate` Exited(0) |
| `compose ps --all` | 0 | 0.98s | 全量容器清单 |
| HTTP probes ×3 | 0 | — | 全部 200 |

`/readyz` 实测返回体（截断）：
```json
{"ready":true,"code":"RUNTIME_NOT_READY","schema":{"ready":true,
 "code":"SPATIAL_BACKEND_UNAVAILABLE","expected_alembic_revision":"0008_hydraulic_retention_seed",
 "alembic_revision":"0008_hydraulic_retention_seed","expected_spatial_backend":"portable",
 "spatial_backend":"portable"},"checks":{"required_tables":true,"missing_tables":[],
 "extensions":true,"hypertables":true,"worker_heartbeats":{...}}}
```

**清理**：审计后 `docker compose down -v` 已删除 `ac8audit-portable` 全部容器/卷/网络；
本机 8 个既有 project（含 `scd-readyz8-*`、`scd-hydraulic-round1/2-*`、`scn-hydraulic-probe`）经复查均完好。

---

## 2. 逐条发现

### F1 —— postgis profile 在根 Compose 结构上不存在（**阻断**；可由代码修复）

- 根 `docker-compose.yml:9` 把 `postgres` 钉死在 `timescale/timescaledb@sha256:3f77…`，**非 PostGIS**。
- 唯一的 PostGIS 变体在 `docker/postgres/compose.yml:4-30`（`postgres-postgis` 服务），
  但那是一个**只含数据库**的 compose：`postgres-postgis` / `postgres-portable` 两个服务，
  挂 `../init-db.sql`，**没有 backend / worker / nginx / frontend / migrate**，
  无法承载 G6 要求的「migration/worker/API/SSE/README 演示/备份恢复」。
- 根 compose 里 `HYDRAULIC_SPATIAL_BACKEND` 只是环境变量（`docker-compose.yml:55,134,224,310,383,461,595`），
  默认 `portable`，**不是 Compose profile**；根 compose 的 profile 只有 `production` / `monitoring`。
- 实测：`--profile postgis` config 阶段 exit 1（§1.3）。
- 契约 `IMPLEMENTATION_CONTRACT.md:2537` 明确「缺任一 profile 即 FAIL」。
- **可修复**：是。需要在根 compose 增加 postgis 镜像变体（并让 G6 按 profile 选图），
  或让 G6 同时加载根 compose 与 `docker/postgres/compose.yml` 并真正跑通全栈。

### F2 —— G6 冻结入口根本不执行 backup→destroy→restore（**阻断**；可由代码修复）

这是本轮最实质的发现：**冻结 fixture 声明的 G6 命令来源与实际实现不符。**

```bash
$ grep -c "backup\.py\|restore\.py" scripts/verify_hydraulic_stack.py
0
```

`gate_g6` 的函数体（`scripts/verify_hydraulic_stack.py:1461-1526`）实际只跑这些：

| 行 | 命令 |
|---|---|
| 1480-1487 | `compose down -v --remove-orphans` |
| 1490-1497 | `compose up --build -d --wait` |
| 1500-1507 | `compose ps --all` |
| 1511-1513 | `_http_probe` ×3 |

即：**config、fixture 存在性、down、up、ps、3 个 HTTP 探针**。没有 backup、没有 destroy 后的 restore、
没有 restore 后的 hash 复算、没有 legacy characterization。

而 `backend/tests/fixtures/hydraulic/compose/restore-manifest-v1.json` 里写着：

- `"command_sources": ["scripts/backup.py::build_parser", "scripts/restore.py::build_parser",
  "scripts/verify_hydraulic_stack.py::gate_g6"]` —— 把 `gate_g6` 列为 backup/restore 命令来源，**与代码不符**；
- 断言 `R-1`（manifest.sha256 校验）、`R-2`（restore 后逐个 payload hash 复算）、
  `R-3`（spatial profile 一致）、`R-4`（restore 后 API **和 SSE** smoke 均 PASS）、
  `R-5`（legacy characterization PASS）、`R-6`（profile 内聚、同 RUN_ID/commit）
  —— **6 条断言在 G6 中一条都没有被执行**；
- fixture 自身结尾也写明：「a value here is never execution evidence and cannot make G6 or AC-8 pass」。

AC-8 判据明写「**恢复 hash/API 校验通过**」。当前入口结构上无法产出该证据 → 该判据无法满足。

- **可修复**：是，且是**接线缺口**而非工具缺失——`scripts/backup.py` 与 `scripts/restore.py` 都在，
  `--help` 正常，CLI 参数与 fixture 声明的命令完全对应（已实测两者 `--help` 均可运行）。
  缺的只是 `gate_g6` 里的调用编排 + restore 后的 hash/API/SSE 复算步骤。

### F3 —— live 探针不含任何 SSE 端点（**缺口**；可由代码修复）

`G6_PROBES`（`scripts/verify_hydraulic_stack.py:73-89`）完整定义只有 3 条：

```python
backend-health  → http://127.0.0.1:8000/health   {200}
backend-readyz  → http://127.0.0.1:8000/readyz   {200}
nginx-health    → http://127.0.0.1:8080/health   {200}
```

AC-8 证据清单点名「API/SSE」，fixture `R-4` 要求 restore 后 SSE smoke 通过，
但 live 阶段**一次 SSE 都没探**。入口里出现的 `sse` 相关内容（`G6_SSE_NGINX_DIRECTIVES`、
`outbox-sse-v1.json`）都是**静态配置断言**，不是运行时 SSE 探测。
- **可修复**：是。

### F4 —— `/readyz` 只断言 HTTP 200，不校验语义（**弱点**；可由代码修复）

探针写死 `{200}`（`scripts/verify_hydraulic_stack.py:1512`）。本轮实测 `/readyz` 返回 **200**，
但响应体同时写着 `"code":"RUNTIME_NOT_READY"` 和 `schema.code:"SPATIAL_BACKEND_UNAVAILABLE"`。

也就是说：**一个自称 not-ready、且 spatial backend 不可用的 readiness 载荷，会被判为 G6 PASS。**
探针在它最该起作用的失败模式上是失效的。
- **可修复**：是——把 body 字段（`ready` / `checks.*` / `schema.code`）纳入断言。

### F5 —— README「演示全链路」在新鲜卷上不可执行（**阻断演示**；非接线问题）

实测 fresh volume 的水力 schema（`psql` 直接查，非转述）：

- `hydraulic` schema **39 张表全部迁移成功**（networks / pipe_segments / simulations / simulation_frames /
  forecast_alerts / ab_comparisons / calibration_* … 都在，alembic 到 `0008_hydraulic_retention_seed`）；
- 但**有数据的只有 5 张，且全是种子/配置**：
  `forecast_rules=2`、`probability_calibrations=2`、`result_retention_policies=2`、
  `schema_metadata=1`、`worker_slots=2`；
- `networks`、`network_nodes`、`pipe_segments`、`catchments`、`rainfall_scenarios`、
  `simulations`、`simulation_frames`、`forecast_alerts`、`ab_comparisons`、
  `calibration_runs`、`calibration_reports` **全部 0 行**。

`README.md:239-240` 已经诚实写明这一点（「当前的真实缺口是演示数据」）。
补充一点 README 未强调的：即使 G6 接线了 backup/restore，**新起的空卷上也没有任何可演示的业务数据**，
「演示全链路」仍然跑不通。
- **可修复**：部分——需要 demo/seed 数据链路，这是功能缺口，不是 gate 接线缺口。

> 自我更正记录：我第一次用未限定 schema 的表名查询，得到「networks 等表 missing」的错误结论。
> 实际这些表存在于 `hydraulic` schema（不在 `public`），已用 `pg_tables` 复核并更正。

### F6 —— 自建镜像的基础镜像未做 digest 钉定（**缺口**；可由代码修复）

AC-8 证据清单要求「digest-pinned `up --build`」。compose 里的**第三方镜像确实钉了 digest**，
但被 `up --build` 构建的两个镜像，其基础镜像只有 tag：

| 文件:行 | 内容 | 是否 digest |
|---|---|---|
| `docker-compose.yml:9` | `timescale/timescaledb@sha256:3f77…` | 是 |
| `docker-compose.yml:69,86,668,756,771` | redis / influxdb / nginx / prometheus / grafana `@sha256:…` | 是 |
| `docker/backend/Dockerfile.cpu:1` | `FROM python:3.11-slim` | **否** |
| `docker/frontend/Dockerfile:3` | `ARG NODE_IMAGE=node:18.20.8-alpine` | **否** |
| `docker/frontend/Dockerfile:12` | `FROM nginx:alpine` | **否（浮动 tag）** |

- **可修复**：是。

### F7 —— 根 Compose 无 `:latest`、无硬编码 secret（**通过**）

```bash
$ grep -c ':latest' docker-compose.yml                                   # 0
$ grep -nE "(PASSWORD|SECRET|TOKEN|KEY)" docker-compose.yml | grep -v '\${'   # 3 条，全是注释
+ ACCESS_TOKEN_EXPIRE_MINUTES: 1440（:148，非 secret）
```
所有密钥均以 `${VAR:?set …}` 要求外部注入（`docker-compose.yml` 内 `DATABASE_URL`、
`POSTGRES_PASSWORD`、`GRAFANA_ADMIN_PASSWORD`、`JWT_PRIVATE_KEY`、`JWT_PUBLIC_KEY`、
`INFLUXDB_*` 等），14 个必填变量。

补充（不构成 AC-8 判据违反，但需记录）：**固定 secret 出现在门禁 harness 里**——
`_compose_env`（`scripts/verify_hydraulic_stack.py:904-931`）硬编码
`ci-postgres-secret` / `ci-redis-secret` / `ci-influx-token-secret` / `ci-jwt-secret-at-least-32-characters`。
这是 CI 取值、不落到 compose 文件，但「无固定 secret」这条判据在**门禁侧**并未做到。

### F8 —— 新鲜卷可复现性（**部分通过**）

`down -v` → `up --build -d --wait` 在 portable profile 上确实从空卷复现成功（70.4s，exit 0），
15 服务 Healthy、migrate Exited(0)、39 张水力表迁移到位。这条判据的**工程部分是真的成立的**。

但两个可复现性缺口：
- **工作树 dirty**：`git status --porcelain` 157 个改动文件，入口自己记录 `"dirty_state": true`。
  根 `docker-compose.yml` 工作树 802 行，而 HEAD `ca99e048` 只有 190 行（`README.md:312` 亦承认）。
  结论：**只靠提交 `ca99e048` 复现不出本轮通过的栈**。`PRD_SUPPLEMENT.md:11.1` 要求证据含 `dirty_state`
  但未把它变成门禁条件。
- **入口不设 project name**：`_compose_command` 不加 `--project-name`（`scripts/verify_hydraulic_stack.py:932-945`），
  G6 会直接对**当前目录名的默认 project** 执行 `down -v`，会删掉该 project 的既有卷。
  本轮我用 `COMPOSE_PROJECT_NAME` 隔离才避免破坏本机数据。这是 gate 自身的破坏性风险。

### F9 —— 文档已过期（**文档缺口**）

| 文档位置 | 记载 | 本轮实测 |
|---|---|---|
| `IMPLEMENTATION_CONTRACT.md:632`、`:2499`、`:2641` | `verify_hydraulic_stack.py`「当前未交付/不存在」 | **存在**，67118 字节，可运行 |
| `IMPLEMENTATION_CONTRACT.md:2554` | AC-8「NOT RUN；Docker daemon 不可用」 | daemon 可用；我已实跑 |
| `PRD_SUPPLEMENT.md:593` | AC-8「Docker live 未运行；本轮未验证」 | 同上 |

仍属实的部分：G5 的 `scripts/benchmark_hydraulic_api.py` 与 `benchmark_hydraulic_nowcast.py`
**确实缺失**（`scripts/` 下只有 `benchmark_hydraulic.py`）——但属 G5，不在本轮 AC-8 范围。

---

## 3. 判据对照表

| AC-8 通过判据（`PRD_SUPPLEMENT.md:593`） | 本轮结果 | 依据 |
|---|---|---|
| 空卷可复现 | **部分通过** | portable live exit 0（§1.4）；但工作树 dirty 157 文件、入口会删默认 project 卷（F8） |
| 无 latest | **通过** | `grep -c ':latest' docker-compose.yml` = 0（F7） |
| 无固定 secret | **部分通过** | compose 层通过；门禁 harness 硬编码 CI secret（F7） |
| 演示全链路 | **不通过** | fresh 卷水力业务表 0 行（F5）；G6 也未执行任何演示命令 |
| 恢复 hash/API 校验通过 | **不通过** | G6 从不调用 backup/restore，R-1～R-6 一条未执行（F2） |
| 两 profile（`IMPLEMENTATION_CONTRACT.md:2537`） | **不通过** | postgis exit 1（F1）；「缺任一 profile 即 FAIL」 |

---

## 4. 缺口清单与可修复性

| ID | 缺口 | 严重度 | 可由代码修复 |
|---|---|---|---|
| F1 | 根 Compose 无 PostGIS 变体，postgis profile 结构上跑不通 | 阻断 | **是** |
| F2 | `gate_g6` 未接线 backup→destroy→restore，fixture R-1～R-6 从不执行 | 阻断 | **是**（工具已存在，纯接线） |
| F5 | fresh 卷无任何业务数据，「演示全链路」不可执行 | 阻断（演示） | **部分**（需 seed/演示数据链路） |
| F3 | live 探针无 SSE 端点 | 高 | **是** |
| F4 | `/readyz` 只断言 200，实测 payload 自称 `RUNTIME_NOT_READY` 仍 PASS | 中 | **是** |
| F6 | `python:3.11-slim` / `node:18.20.8-alpine` / `nginx:alpine` 未钉 digest | 中 | **是** |
| F7 | 门禁 harness 硬编码固定 secret | 低 | **是** |
| F8 | 门禁 `down -v` 不隔离 project name，有误删用户卷风险 | 中 | **是** |
| F8 | 工作树 dirty 157 文件，仅靠提交无法复现 | 中 | **否**（需提交，属流程） |
| F9 | 契约/补充文档关于「入口未交付 / daemon 不可用」的记载已过期 | 低 | **是**（改文档） |

---

## 5. 本轮**未**运行（明确不冒充）

- G0（pytest/Black/isort/Flake8/mypy/npm ci/Vitest/lint/format/build）——未运行，属 AC-7 范围。
- G1、G2、G3、G4、G5 —— 未运行。
- **backup→destroy→restore 实际往返**——未运行。工具存在且可用，但 G6 未接线；
  本轮未手工替代执行（该往返会 `down -v` 销毁栈，且手工执行不构成 G6 证据）。
- legacy characterization 零回归 —— 未运行。
- `/simulation` 页面 Playwright 演示 —— 未运行。
- postgis profile 的 **live** 全栈 —— 未跑，因 config 阶段即 exit 1，无法进入 live。

## 6. 建议的最小修复顺序

1. **F1**：给根 compose 增加 PostGIS 镜像变体，并让 `gate_g6` 按 `--profile` 选图；
   或让 G6 组合加载根 compose + `docker/postgres/compose.yml` 并跑通全栈。
   修复前 G6 不可能有 PASS。
2. **F2**：在 `gate_g6` 中按 `restore-manifest-v1.json` 的 `commands` 编排
   backup → verify-only → down -v → restore --apply --confirm → 复算 R-1/R-2 → API+SSE smoke（R-4）
   → legacy characterization（R-5），并落 `g6/<profile>/`。
3. **F3/F4**：探针补 SSE 端点；`/readyz` 改为断言响应体字段而非仅 200。
4. **F8**：给 `_compose_command` 加 `--project-name`（由 gate 显式生成，避免误删用户卷）。
5. **F5**：提供可重复的演示数据 seed，使 README 演示链在 fresh 卷上真能跑通。
6. **F6**：三个 `FROM`/`ARG` 补 digest。
7. **F9**：更新 `IMPLEMENTATION_CONTRACT.md:632/2499/2554/2641` 与 `PRD_SUPPLEMENT.md:593` 的过期记载。

---

## 7. 审计声明

- 本文件所有「实测」结论均来自 §1 记录的命令与其输出；未运行项在 §5 单独列出，未计入通过。
- 验收过程只读：未修改任何实现代码、migration、配置或 fixture。
- 唯一副作用：为执行 live G6 创建过隔离 project `ac8audit-portable`，已在审计结束后 `down -v` 清理干净
  （复查 0 容器 / 0 卷残留，本机 8 个既有 project 全部完好）。
- 结论不采信任何文档勾选或实现者自述；`README.md` 与契约文档的陈述均已用命令独立复核
  （复核后 README 关于「业务表为空」「postgis profile FAIL」的陈述属实，另补出 F2/F3/F4 三项文档未记载的缺口）。
