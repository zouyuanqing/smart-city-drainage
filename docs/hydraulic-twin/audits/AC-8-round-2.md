# AC-8 独立验收（round-2）

> 验收员：独立验收员-2（只读，不改实现代码）
> 验收时间：2026-09-28（本轮所有命令的 `started_at` / `utc_timestamp` 见 §1、§2；本机时区 UTC+8）
> 仓库根：`C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage`
> 目标提交：`ca99e048`（`git rev-parse --short=8 HEAD`），**工作树 dirty（160 个改动文件）**
> RUN_ID（本轮）：`AC8-round2-20260928`
> 前序：`audits/AC-8-round-1.md`（独立验收员-1，结论 FAIL，F1～F9）

## 0. 结论

**AC-8 = FAIL**（不是 NOT RUN）。

判据来源（三份基线，本轮逐条复读）：
- `SOURCE_PRD.md:33`「AC-8 全新 docker compose 可复现 README 演示」；
- `PRD_SUPPLEMENT.md:593` 通过判据「空卷可复现；无 latest/固定 secret；**演示全链路**；**恢复 hash/API 校验通过**」，自动化证据「`down -v`、digest-pinned `up --build`、**两 profile**、migration、worker、**API/SSE**、README、**backup/restore**」；
- `IMPLEMENTATION_CONTRACT.md:2537`（§17.2 G6 行）「postgis 与 portable 各自独立命令/evidence/fresh volume；两者均通过 README demo、backup/restore、old API/SSE zero regression，**缺任一 profile 即 FAIL**」；`:2497`（§17.1 G6 行）要求两者都完成「migration/worker/API/SSE、README demo、backup→destroy→restore hash/API、legacy characterization」。

**与 round-1 相比的净变化：**

| | round-1 | round-2 | 变化 |
|---|---|---|---|
| F1 postgis profile | FAIL | **仍 FAIL**（同两条 static_failures，逐字相同） | 未修复 |
| F2 G6 未接线 backup→restore | 阻断 | **仍阻断**（`gate_g6` 全函数 0 处 backup/restore） | 未修复 |
| F5 fresh 卷无演示数据 | 阻断（演示） | **仍阻断**，且本轮查到更硬的根因 | 未修复 + 恶化定位 |
| F3 live 探针无 SSE | 高 | **已加探针，但引入新阻断**（见 N1/N2） | 引入回归 |
| F4 `/readyz` 只断言 200 | 中 | **已加 body 断言**，实测立即 FAIL（N2） | 引入回归 |
| F6 基础镜像未钉 digest | 中 | **仍未钉** | 未修复 |
| F7 门禁硬编码 secret | 低 | **仍在** | 未修复 |
| F8 门禁不隔离 project name | 中 | **已修**（`--project-name hydraulic-gate-<profile>`） | 已修复 |
| F9 文档过期 | 低 | **仍过期** | 未修复 |
| **N1（本轮新发现）** | — | **live G6 在 SSE 探针上永久挂起**，20 分钟不出 `result.json` | 新阻断 |
| **N2（本轮新发现）** | — | **`/api/forecast/latest` 实测 503**，被新探针判 FAIL | 新阻断 |
| **N3（本轮新发现）** | — | **production 下全部水力 API 恒 503**（`durable_backend` 硬编码 `False`，无任何开关） | 新阻断（演示根因） |
| **N4（本轮新发现）** | — | live 探针集合与冻结 lock fixture 声明漂移（5 vs 3） | 新缺口 |

一句话：**门禁变严格之后，原来被 200 掩盖的两个真实运行时缺陷（`/readyz` 自称 not-ready、forecast API 恒 503）暴露出来；而 G6 live 现在根本跑不完。**

---

## 1. 本轮实际执行的命令（全部为「我跑的」，非转述）

### 1.1 环境

```bash
$ docker version ; docker compose version
Client: Version: 29.4.2 / API 1.54 / OS: windows/amd64
Server: Docker Desktop 4.72.0 (225998) / Engine 29.4.2 / OS: linux/amd64
Docker Compose version v5.1.3
```
→ daemon 可用（与 `VALIDATION_EVIDENCE.md:30` payload 的 `dockerAvailable: false` 矛盾，round-1 已记录同一矛盾，本轮再次实测为**可用**）。

```bash
$ git rev-parse --short=8 HEAD     → ca99e048
$ git status --porcelain | wc -l   → 160     # round-1 为 157，本轮又多了 3 个
$ python --version                  → Python 3.14.4
```

### 1.2 G6 config —— portable（PASS，但工具自述不是 AC-8 证据）

```bash
$ python scripts/verify_hydraulic_stack.py --gate g6 --profile portable --mode config \
    --evidence-dir ".runtime/evidence/ac8r2/g6-portable-config" --run-id "AC8-round2-20260928"
HYDRAULIC_STACK_STATUS=PASS   HYDRAULIC_STACK_EXIT=0
HYDRAULIC_STACK_ARTIFACT_SHA256=73b17c7890d06c8518a08114a69dfc5a11fa2bd5991895f84dcc958362feafda
```
报告内 `"scope_note": "Compose config is a configuration check only; it is not live G6 or AC-8 evidence."`——入口自己不把 config 结果当 AC-8 证据，这个自我约束是对的。
子步骤：`compose config --quiet` exit 0（0.225s）、`config --format json` exit 0（0.386s）、`static-contract` exit 0（`static_failures: []`）、冻结 fixture 2 个 hash 校验通过。

### 1.3 G6 config —— postgis（**FAIL，与 round-1 逐字相同**）

```bash
$ python scripts/verify_hydraulic_stack.py --gate g6 --profile postgis --mode config \
    --evidence-dir ".runtime/evidence/ac8r2/g6-postgis-config" --run-id "AC8-round2-20260928"
HYDRAULIC_STACK_STATUS=FAIL   HYDRAULIC_STACK_EXIT=1
STATIC_FAILURES: [
  "postgis profile does not select an explicit PostGIS image",
  "postgis profile renders the portable default postgres image"
]
HYDRAULIC_STACK_ARTIFACT_SHA256=52a1a7bd2229bdff1e574fe5ce283ddbb0283b52001bd0aa6ebce3269c2b9d1b
```

### 1.4 G6 **live** —— portable（**不终止**；这是本轮最实质的发现）

```bash
$ python scripts/verify_hydraulic_stack.py --gate g6 --profile portable --mode live \
    --live-timeout-seconds 2400 --evidence-dir ".runtime/evidence/ac8r2/g6-portable-live" \
    --run-id "AC8-round2-20260928" > .runtime/ac8r2-g6-portable-live.log 2>&1
```

实测时间线（`docker ps` 与证据目录文件 mtime）：

| 时刻 | 观测 |
|---|---|
| 05:07 | `g6-portable-down-fresh` 落盘（空卷，`down -v --remove-orphans` exit 0） |
| 05:08 | `g6-portable-up-build-wait` 落盘，15 个服务全部 Up，`migrate-1 Exited (0)`，backend/nginx/frontend/5 worker/redis/postgres/influxdb 均 healthy |
| 05:08 | `g6-portable-compose-ps` 落盘（exit 0） |
| 05:08 → **05:21** | 之后 **13 分钟** 无任何新文件；`result.json` **始终不存在**；stdout log **0 字节** |
| 05:21 | 我主动 `TaskStop` 终止该进程（**不是**它自己结束） |

**结论：live G6 在 HTTP 探针阶段永久挂起。** 挂起点已定位并单独复现，见 §2 的 N1。

### 1.5 挂起后对**同一台活栈**的直接探查（curl，本轮实测）

栈仍在运行时我直接打端点，拿到 round-1 报告里没有的运行期事实：

```bash
$ curl -s -o /dev/null -w "%{http_code} %{time_total}\n" --max-time 10 http://127.0.0.1:8000/health
200 0.005664
$ curl ... --max-time 10 http://127.0.0.1:8000/readyz
200 0.083037
$ curl ... --max-time 10 http://127.0.0.1:8080/health
200 0.006757
$ curl -s -o /dev/null -w "%{http_code} %{time_total}\n" --max-time 25 -H "Accept: application/json" \
    http://127.0.0.1:8000/api/sse/events
200 25.015363          # ← 200 但 25 s 都不结束；被 --max-time 掐断
$ curl -s -o /dev/null -w "%{http_code} %{time_total}\n" --max-time 10 http://127.0.0.1:8000/api/forecast/latest
503 0.004056          # ← 不在门禁期望集合 {200,401,403} 内
```

响应体（本轮实测原文）：

```json
# GET /readyz  → HTTP 200
{"ready":true,"code":"RUNTIME_NOT_READY","schema":{"ready":true,"code":"SPATIAL_BACKEND_UNAVAILABLE",
 "expected_alembic_revision":"0008_hydraulic_retention_seed","expected_spatial_backend":"portable",
 "alembic_revision":"0008_hydraulic_retention_seed","schema_metadata_revision":"0008_hydraulic_retention_seed",
 "spatial_backend":"portable"},"checks":{"required_tables":true,"missing_tables":[],"extensions":true,
 "missing_extensions":[],"hypertables":true,"worker_heartbeats":{"network-job":true,"simulation":true,
 "nowcast":true,"outbox-relay":true,"notification":true},"forecast_policy":true}}

# GET /api/forecast/latest → HTTP 503
{"detail":"hydraulic durable repository is not configured","code":"DURABLE_STORE_UNAVAILABLE",
 "request_id":"cb637014-2b74-4e45-8673-a038b0d0fcf4","errors":[]}
```

SSE 响应头与 30 s 内容（`timeout 30 curl -sN -D ...`，退出码 124）：

```text
HTTP/1.1 200 OK
content-type: text/event-stream; charset=utf-8
transfer-encoding: chunked
x-accel-buffering: no

（30 秒共 221 字节，从不结束）
retry: 3000

event: connected
data: {"client_id": "f9fc4848", "status": "connected"}

: ping - 2026-09-27 21:15:19.857532
: heartbeat 1790543719
: ping - 2026-09-27 21:15:34.856189
: heartbeat 1790543734
```

### 1.6 四个非流式探针的逐个判定（定向诊断，本轮实测）

复用门禁自己的 `_http_probe`（不改代码）：

```bash
$ python /tmp/probe_diag2.py
backend-health:        status=PASS http=200 reasons=[]
backend-readyz:        status=FAIL http=200 reasons=["code='RUNTIME_NOT_READY' not in ['OK', None]"]
nginx-health:          status=PASS http=200 reasons=[]
backend-forecast-api:  status=FAIL http=503 reasons=None err=None
```

SSE 探针单独复现挂起：

```bash
$ timeout 150 python /tmp/probe_diag.py     # 调用 _http_probe("backend-sse-events", ...)
WRAPPER_EXIT=124   # 被 timeout 杀掉，即探针 150 s 仍未返回
```

### 1.7 fresh 卷上的演示数据实测（`psql` 直查，非转述）

```bash
$ docker exec hydraulic-gate-portable-postgres-1 psql -U drainage_admin -d smart_drainage -At \
    -c "SELECT count(*) FROM pg_tables WHERE schemaname='hydraulic'"
38
$ ... "SELECT relname||'='||n_live_tup FROM pg_stat_user_tables
      WHERE schemaname='hydraulic' AND n_live_tup>0 ORDER BY relname"
forecast_rules=2
probability_calibrations=2
result_retention_policies=2
schema_metadata=1
worker_slots=2
$ ... 逐表 count(*)
networks=0  network_revisions=0  network_nodes=0  pipe_segments=0  catchments=0
rainfall_scenarios=0  network_jobs=0  simulations=0  simulation_frames=0
nowcast_runs=0  forecast_alerts=0  ab_comparisons=0  calibration_runs=0
observation_samples=0  forecast_rules=2
```

### 1.8 静态复核命令

```bash
$ grep -c ':latest' docker-compose.yml                                                  → 0
$ grep -nE "(PASSWORD|SECRET|TOKEN|KEY)" docker-compose.yml | grep -v '\${'
148:  ACCESS_TOKEN_EXPIRE_MINUTES: 1440
542:  # not a secret.  Export ALERT_WEBHOOK_SECRET before any real deployment.
627:  # Export a real ALERT_WEBHOOK_SECRET and override ALERT_WEBHOOK_URLS with
（后两条是注释行）
$ head -1 docker/backend/Dockerfile.cpu      → FROM python:3.11-slim            # 无 digest
$ head -3 docker/frontend/Dockerfile          → ARG NODE_IMAGE=node:18.20.8-alpine  # 无 digest
$ head -12 docker/frontend/Dockerfile         → FROM nginx:alpine                # 浮动 tag
$ ls scripts/benchmark_hydraulic*.py         → 只有 benchmark_hydraulic.py
```

### 1.9 `gate_g6` 函数体关键词扫描（判定 F2）

```python
# 我用脚本切出 scripts/verify_hydraulic_stack.py 中 gate_g6 的函数体（1535–1630 行）后逐行扫描：
gate_g6 starts at line 1535 ; ends before line 1632
  'backup'  : 0 line(s) -> []
  'restore' : 0 line(s) -> []
  'destroy' : 0 line(s) -> []
  'seed'    : 0 line(s) -> []
  'demo'    : 0 line(s) -> []
  'README'  : 0 line(s) -> []
  'readme'  : 0 line(s) -> []
```

### 1.10 契约测试（顺带复核 round-1 记为失败的两条）

```bash
$ backend/.venv/Scripts/python.exe -m pytest \
    docker/backend/tests/test_deployment_contract.py backend/tests/test_contract_gate_scripts.py -q
35 passed, 1 warning in 12.24s
```
→ round-1 / `VALIDATION_EVIDENCE.md:343` 记的「`2 failed, 24 passed`（断言 docker-compose.yml 硬编码 secret）」**本轮已不复现**；这是 F7 唯一变好的地方（但见 §2 的 N5，根因未真正消除）。

### 1.11 清理（审计副作用，已核销）

```bash
$ docker compose --project-name hydraulic-gate-portable --profile production --profile monitoring \
    -f docker-compose.yml down -v --remove-orphans
DOWN_EXIT=0        # 15 个容器 + 13 个卷 + 1 个网络全部 Removed
$ docker ps -a | grep -c hydraulic-gate     → 0
$ docker volume ls | grep -c hydraulic-gate → 0
$ docker ps --format "{{.Names}}"
scd-readyz8-portable-postgres-portable-1    Up 8 hours (healthy)
scd-readyz8-postgis-postgres-postgis-1      Up 8 hours (healthy)
scn-hydraulic-probe-postgres-1              Up 13 hours (healthy)
scd-hydraulic-round2-portable-postgres-portable-1  Up 13 hours (healthy)
scd-hydraulic-round2-postgis-postgres-postgis-1    Up 13 hours (healthy)
scd-hydraulic-round1-postgis-postgres-postgis-1    Up 13 hours (healthy)
deepseek-search-searxng                      Up 13 hours
$ git status --porcelain | wc -l            → 160   # 与审计前一致，未被我改动
```
→ 本轮 7 个既有容器全部完好，审计 project 零残留，**实现代码零改动**。

---

## 2. 逐条发现

### F1 —— postgis profile 在根 Compose 结构上不存在（**阻断**；可由代码修复｜round-1 未修复）

- 根 `docker-compose.yml:8-9` 仍把 `postgres` 钉死在 `timescale/timescaledb@sha256:3f770468…`，**非 PostGIS**，且无任何 `postgis` Compose profile。
- 唯一 PostGIS 变体仍在 `docker/postgres/compose.yml:1-30` 的 `postgres-postgis` 服务，但那是一个 **只含数据库**的 compose（`postgres-postgis` / `postgres-portable` 两个服务），**没有 backend / worker / nginx / frontend / migrate**，无法承载 G6 要求的「migration/worker/API/SSE/README 演示/备份恢复」。
- 静态失败的两条来源都还在：`scripts/verify_hydraulic_stack.py:995-999`（postgis 必须选到含 `postgis` 的镜像）与 `scripts/verify_hydraulic_stack.py:1250-1258`（`_pinned_image_failures` 的 postgis 分支：渲染镜像等于 lock 里的 portable 默认镜像即失败）。
- 本轮实测 `--profile postgis --mode config` → `HYDRAULIC_STACK_EXIT=1`，两条 `static_failures` 与 round-1 **逐字相同**。
- 契约 `IMPLEMENTATION_CONTRACT.md:2537`「缺任一 profile 即 FAIL」。
- **可修复**：是。round-1 §6 给的两条路径仍然有效：① 根 compose 增加 postgis 镜像变体并让 G6 按 profile 选图；② 让 G6 同时 `-f docker-compose.yml -f docker/postgres/compose.yml` 并真正跑通全栈。
- **未修复的后果（比 round-1 更重）**：live 模式在 static 失败时**根本不会启动**（`scripts/verify_hydraulic_stack.py:1622-1628` 追加 `"live G6 was not started because config/static or frozen fixture prerequisites failed"` 并把 static step 置 FAIL/exit 1），所以 postgis 连一次 live 往返都不可能有。

### N1 —— live G6 在 SSE 探针上**永久挂起**（**阻断；可由代码修复｜本轮新发现**）

这是 round-1 F3「加 SSE 探针」这条修复**引入的新阻断**，比原缺口更严重。

- `scripts/verify_hydraulic_stack.py:1607-1611` 新增：
  ```python
  _http_probe("backend-sse-events", "http://127.0.0.1:8000/api/sse/events", {200, 307, 401, 403}),
  ```
- `_http_probe`（`:1384-1397`）对每个探针做 `urlopen(request, timeout=20)` 后 `response.read(4096)`。**`timeout` 是每次 socket 操作的超时，不是整体截止时间**；`read(4096)` 在内部循环 `recv` 直到攒满 4096 字节或 EOF。
- `/api/sse/events`（`backend/app/api/routes.py:591`）返回 `content-type: text/event-stream`、`transfer-encoding: chunked`，实测 **30 秒只发 221 字节且永不关闭**（§1.5）。每 ~15 s 一条 `: ping` 让每次 `recv` 都在超时前拿到数据，于是 `read(4096)` 永远不会返回、也永远不会抛超时。
- 结论：`--gate g6 --mode live` 在这个栈上**不终止**。实测 13 分钟无 `result.json`（§1.4），单独复现 150 s 仍未返回（§1.6），只能由我 `TaskStop` 杀掉。
- `--live-timeout-seconds 2400` 只传给 `up --build -d --wait`（`:1563-1571`），**探针阶段没有任何超时上限**。
- **可修复**：是。SSE 这类长连流不能用「读满 4096 字节」的探针。正确做法至少其一：① 只断言响应头（`urlopen` 后立刻 `response.status` + `response.headers`，不 `read` body）；② 整体 `deadline` 包裹（单调时钟上限），到点即按「收到响应头」判 PASS；③ 改用 `HEAD`/带 `Connection: close` 的短读。
- **这条同时把 F3 从「缺口」变成「已修但引入阻断」**：SSE 端点现在**被覆盖了**，但覆盖方式让门禁不可用。

### N2 —— `/api/forecast/latest` 实测 503，被新探针判 FAIL（**阻断；可由代码修复｜本轮新发现**）

- `scripts/verify_hydraulic_stack.py:1612-1616` 新增：
  ```python
  _http_probe("backend-forecast-api", "http://127.0.0.1:8000/api/forecast/latest", {200, 401, 403}),
  ```
- 实测该端点在**健康栈**上返回 **HTTP 503 `DURABLE_STORE_UNAVAILABLE`**（§1.5 响应体原文）。503 ∉ `{200,401,403}` → `_http_probe` 走 `except HTTPError` 分支（`:1418-1427`）判 `"status": "FAIL"`。
- 定向诊断直接复现：`backend-forecast-api: status=FAIL http=503`（§1.6）。
- 根因是 N3（下一条），不是探针写错：**门禁这次是对的，栈是错的**。但从「让 G6 能跑通」的角度，修法二选一：要么接通 durable store（正解，N3），要么把期望集合改成 `{200,401,403,503}`（掩盖问题，不推荐）。
- **可修复**：是，但必须修 N3，不能只改期望集合。

### N3 —— production 下**全部水力 API 恒 503**（**阻断演示；可由代码修复｜本轮新发现**）

round-1 的 F5 把「fresh 卷没有演示数据」列为阻断，方向对但**只找到一半根因**。本轮查到另一半，而且这一半与有没有 seed 无关：

- `backend/app/api/hydraulic_routes.py:309-311`：`HydraulicApiStore.__init__` 里 `self.durable_backend = False`。
- `backend/app/api/hydraulic_routes.py:338-348` `_require_durable_api_store()`：`if settings.DEPLOYMENT_ENVIRONMENT == "production" and not getattr(hydraulic_store, "durable_backend", False): raise 503 DURABLE_STORE_UNAVAILABLE`。
- `backend/app/main.py:1273-1284`：同样的 503 门禁。
- 全仓扫描（排除 `.venv` 与陈旧的 `backend/build/lib/` 构建副本）：
  ```bash
  $ grep -rn "durable_backend" --include="*.py" backend/ | grep -v .venv
  backend/app/api/hydraulic_routes.py:311:        self.durable_backend = False
  backend/app/api/hydraulic_routes.py:343:        hydraulic_store, "durable_backend", False
  backend/app/main.py:1273:        if not getattr(hydraulic_store, "durable_backend", False):
  ```
  → **全仓没有任何一处把它置 `True`，也没有任何环境变量/开关能置 `True`**；`docker-compose.yml` 里 `grep -n "HYDRAULIC_STORE\|DURABLE"` 零命中。
- 含义：Compose 栈（`DEPLOYMENT_ENVIRONMENT=production`）上，**创建 network、seed、提交 simulation 等全部写路径都是 503**。所以 AC-8 的「演示全链路」不是「缺 seed 数据」这么轻——**即使把 N1/F5 的 seed 数据全部准备好，README 演示链第一步（建 network）就会 503 失败**。
- **可修复**：是。这是实现缺口（把 durable store 接到 SQLAlchemy repository 并在生产装配），工作量属于后端模块，不是 gate 接线。

### F2 —— G6 冻结入口根本不执行 backup→destroy→restore（**阻断；可由代码修复｜round-1 未修复）

- §1.9 的逐行扫描给出硬证据：`gate_g6`（`scripts/verify_hydraulic_stack.py:1535-1630`）全函数体 **0 处** `backup` / `restore` / `destroy` / `seed` / `demo` / `README`。
- 实际执行的子步骤只有 6 个：config `--quiet`、config `--format json`、static-contract、冻结 fixture 存在性、`down -v --remove-orphans`、`up --build -d --wait`、`compose ps --all`、HTTP 探针（`:1552-1621`）。
- `backend/tests/fixtures/hydraulic/compose/restore-manifest-v1.json` 里的 `assertions`（R-1…R-6）**一条都没有被执行路径**；`scripts/verify_hydraulic_stack.py:1271-1345` 的 `_restore_manifest_failures` 只对 fixture 做**静态自洽性**校验（schema / content_sha256 / evidence_state 仍为 `NOT_RUN` / observed_results 必须为空 / components 齐全 / assertions 格式），**不跑任何 backup 或 restore 命令**。
- AC-8 判据明写「**恢复 hash/API 校验通过**」（`PRD_SUPPLEMENT.md:593`），G6 又被 AC-8 唯一依赖（§2 的 N6）→ 该判据结构上无法产出证据。
- **可修复**：是，且仍是**纯接线**：`scripts/backup.py`（31102 B）与 `scripts/restore.py`（36066 B）都在，CLI 可用（本轮 `--help` 侧由 §1.10 的测试覆盖间接确认可加载），缺的只是 `gate_g6` 里的调用编排 + restore 后的 hash 复算 + API/SSE smoke。

### F3 —— live 探针不含 SSE 端点（**已修，但修法引入 N1**）

- round-1 F3 成立的事实已改变：`scripts/verify_hydraulic_stack.py:1607-1611` 现在确实有 `backend-sse-events` 探针，覆盖了 `IMPLEMENTATION_CONTRACT.md:2497` 要求的「API/SSE」。
- 但按 §2 的 N1，这个覆盖使 live G6 **不可终止**。**判定：F3 本身已关闭，作为 N1 的成因记录。**
- round-1 F3 附带指出「`G6_SSE_NGINX_DIRECTIVES`、`outbox-sse-v1.json` 只是静态断言」——本轮同意：`:66-71` 的四条 nginx 指令只做字符串包含检查（`:1063-1066`），不构成运行时 SSE 语义证据。

### F4 —— `/readyz` 只断言 HTTP 200，不校验语义（**已修，且立刻抓出真实缺陷**）

- `scripts/verify_hydraulic_stack.py:1594-1599` 现在带 `required_fields={"ready": True, "code": ["OK", None]}`；`_probe_status`（`:1344-1381`）逐字段断言，语义正确。
- 实测判定：`backend-readyz: status=FAIL http=200 reasons=["code='RUNTIME_NOT_READY' not in ['OK', None]"]`（§1.6）。
- **round-1 的判断被本轮数据完全证实**：`/readyz` 在一个 `ready:true`、六项 check 全 true 的健康栈上，**顶层 `code` 仍是 `RUNTIME_NOT_READY`、`schema.code` 仍是 `SPATIAL_BACKEND_UNAVAILABLE`**。这不是探针太严，是 `/readyz` 的 `code` 字段与 `ready` 字段自相矛盾。
- **可修复**：是，且要分两处：① 修 `/readyz` 的 `code` 语义（`backend/app/main.py` 的 `check_runtime_readiness` 附近），使其在 `ready:true` 时不返回 `RUNTIME_NOT_READY` / `SPATIAL_BACKEND_UNAVAILABLE`；② 或者让门禁断言与真实契约一致（但那样就等于放弃 F4 的修复价值）。**推荐 ①。**

### N4 —— live 探针集合与冻结 lock fixture 声明漂移（**缺口；可由代码修复｜本轮新发现**）

- 冻结 lock `docker/hydraulic-gates/compose-v1.lock.json` 的 `contract.health_contract` 声明：
  - `"probe_source": "scripts/verify_hydraulic_stack.py::gate_g6"`；
  - `"probes"` = 3 条（`backend-health` / `backend-readyz` / `nginx-health`）。
- `scripts/verify_hydraulic_stack.py:1186-1188` 断言 `health.get("probes") == [dict(item) for item in G6_PROBES]`，而 `G6_PROBES`（`:72-89`）**仍是 3 条**。
- 但 `gate_g6` 的 live 分支（`:1583-1618`）现在探 **5 条**，且**没有复用 `G6_PROBES`**，是内联的第二份副本。
- 后果：① 冻结 lock 声称描述 gate_g6 的探针契约，实际已经不符，且这个漂移**没有任何检查会发现**（因为检查比对的是 `G6_PROBES` 而 gate_g6 不用它）；② `gate_smoke`（`:1665-1667`）仍用 `G6_PROBES` 的 3 条，**smoke 门禁与 g6 门禁探针集合不一致**——同一次「smoke PASS」不能推出「g6 探针会 PASS」。
- **可修复**：是。把 `gate_g6` 改为复用 `G6_PROBES`（把 SSE/forecast 两条加进 `G6_PROBES`），同步更新 lock fixture 的 `probes` 与其 `content_sha256`，并给 `G6_PROBES` 的每项加上 `required_fields`（否则 lock 无法表达 body 契约）。

### F5 —— README「演示全链路」在新鲜卷上不可执行（**阻断演示；部分可由代码修复**）

- 本轮在**自己拉起的真·fresh volume** 上直查（§1.7）：`hydraulic` schema **38 张表**全部迁移成功，但有数据的只有 5 张且全是配置/种子：
  `forecast_rules=2`、`probability_calibrations=2`、`result_retention_policies=2`、`schema_metadata=1`、`worker_slots=2`；
  `networks`、`network_revisions`、`network_nodes`、`pipe_segments`、`catchments`、`rainfall_scenarios`、
  `network_jobs`、`simulations`、`simulation_frames`、`nowcast_runs`、`forecast_alerts`、
  `ab_comparisons`、`calibration_runs`、`observation_samples` **全部 0 行**。
- **与 round-1 的一处差异（据实更正）**：round-1 实测 `networks` 有 1 行 `SINK-TEST`；本轮 fresh 卷上 `networks=0`。两者不矛盾——round-1 观测的是一台**已存在的**栈（notification-sink 投递测试留下的行），本轮是 `down -v` 之后从零起。**fresh 卷的准确数字是 0。**
- README 的诚实度：`README.md:173-181` 与 `:239-241` 已写明「演示数据仍然为空」「当前的真实缺口是演示数据」，`docs/hydraulic-twin/operations.md:47-60`（§1.2）逐条列出 6 项当前不能完成的演示。这部分文档**属实**，不是自述美化。
- **但 round-1 漏了更硬的一半**：见 N3。即使补齐 seed，`/api/networks`（POST）在 production 栈上恒 503，README 演示链**第一步**就跑不通。所以 F5 的可修复性要下调：
  - 「缺 seed/演示数据」→ **可修复**（补 seed 链路）；
  - 「演示链跑不通」这个结论本身 → **部分可修复**，真正的前置是 N3。
- **可修复**：**部分**。需要 demo/seed 数据链路 **+** N3 的 durable store 装配，两者缺一不可。

### F6 —— 自建镜像的基础镜像未做 digest 钉定（**缺口；可由代码修复｜round-1 未修复**）

| 文件:行 | 内容 | digest |
|---|---|---|
| `docker-compose.yml:9` | `timescale/timescaledb@sha256:3f77…` | 是 |
| `docker-compose.yml`（redis / influxdb / nginx / prometheus / grafana） | `@sha256:…` | 是 |
| `docker/backend/Dockerfile.cpu:1` | `FROM python:3.11-slim` | **否** |
| `docker/frontend/Dockerfile:3` | `ARG NODE_IMAGE=node:18.20.8-alpine` | **否** |
| `docker/frontend/Dockerfile:12` | `FROM nginx:alpine` | **否（浮动 tag）** |

- AC-8 证据清单要求「digest-pinned `up --build`」（`PRD_SUPPLEMENT.md:593`）。本轮实测 `up --build` 成功（exit 0，15 服务 healthy），但**基础镜像仍是浮动 tag**，「digest-pinned」只覆盖第三方直接引用镜像，不覆盖自建镜像的基座。
- **可修复**：是。

### F7 —— 固定 secret（**部分通过｜compose 层通过，门禁层未改**）

- **通过的部分**：`grep -c ':latest' docker-compose.yml` → **0**；`grep -nE "(PASSWORD|SECRET|TOKEN|KEY)" docker-compose.yml | grep -v '\${'` 只有 3 条命中，其中 2 条是注释、1 条是 `ACCESS_TOKEN_EXPIRE_MINUTES: 1440`（非 secret）。14 个必填变量全部以 `${VAR:?set …}` 要求外部注入。
- **未改的部分**：`scripts/verify_hydraulic_stack.py:913-938` 的 `_compose_env(profile)` 仍硬编码
  `POSTGRES_PASSWORD=ci-postgres-secret`、`REDIS_PASSWORD=ci-redis-secret`、
  `INFLUXDB_PASSWORD=ci-influx-secret`、`INFLUXDB_TOKEN=ci-influx-token-secret`、
  `DOCKER_INFLUXDB_ADMIN_TOKEN`、`DATABASE_URL`/`DATABASE_URL_SYNC` 内嵌 `ci-postgres-secret`、
  `REDIS_URL=redis://:ci-redis-secret@…`、`JWT_SECRET_KEY=ci-jwt-secret-at-least-32-characters`、
  `JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY`/`GRAFANA_ADMIN_PASSWORD=ci-grafana-secret`。
- **可修复**：是。注意 §1.10 显示 `test_deployment_contract.py` 现在全绿，说明门禁侧已有别的约束落地，但 `_compose_env` 的字面量本身仍在。

### F8 —— 新鲜卷可复现性（**部分通过｜隔离已修，dirty 仍在**）

- **已修（round-1 的破坏性风险）**：`scripts/verify_hydraulic_stack.py:941-960` 的 `_compose_command` 现在显式带
  `--project-name _compose_project_name(profile)`（`:963-964` = `hydraulic-gate-<profile>`）。
  本轮**无需**再像 round-1 那样手工 `export COMPOSE_PROJECT_NAME` 隔离——我直接跑默认命令，
  栈起在 `hydraulic-gate-portable`，审计结束 `down -v` 后本机 7 个既有容器全部完好（§1.11）。**这条是真修复。**
- **已复现成功**：`down -v --remove-orphans` → `up --build -d --wait` 在 portable 上从空卷成功，
  `migrate-1 Exited (0)`、15 服务 healthy、`/readyz` 200。工程部分是成立的。
- **仍在**：`git status --porcelain | wc -l` = **160**（round-1 为 157），入口报告自带 `"dirty_state": true`。
  根 `docker-compose.yml` 是未提交的工作树改动，HEAD `ca99e048` 只有 190 行（`README.md:311-312` 亦承认）。
  → **只靠提交 `ca99e048` 复现不出本轮这台栈。**
- **可修复**：隔离部分**已修**；dirty 部分**不可由代码修复**（需提交，属流程）。

### N6 —— AC-8 在门禁里只有 G6 一个依赖，而 G6 必须两 profile 同时 PASS（**结构性阻断｜本轮新发现**）

- `backend/tests/fixtures/hydraulic/acceptance/v1/acceptance_requirements.json`：
  `AC-8 -> ['g6']`（本轮实读该 fixture 全部 8 条：AC-1→g1,g2,g6；AC-2→g2,g6；AC-3→g3,g5,e2e,g6；AC-4→g4,g5,e2e,g6；AC-5→g3,g5,e2e,g6；AC-6→g3,e2e,g6；AC-7→g0,e2e,g6；**AC-8→g6**）。
- `scripts/verify_hydraulic_stack.py:1838-1841`：`g6` 需要 `g6/postgis/result.json` **和** `g6/portable/result.json` 两份；
  `_acceptance_artifact`（`:1814-1815`）要求每份 `status == "PASS"` 且 `exit_code == 0`，且 `run_id`/`commit` 同轮同提交。
- 叠加 F1（postgis 必 FAIL）与 N1（portable live 不终止）→ **AC-8 在当前实现下无法取得 PASS artifact**。
- **可修复**：随 F1 / N1 一起修复；本条不是独立缺陷，是前两条的下游后果。记录它是为了说明「修好 portable 单侧也不够」。

### F9 —— 文档已过期（**文档缺口；可由代码修复｜round-1 未修复**）

| 文档位置 | 记载 | 本轮实测 |
|---|---|---|
| `IMPLEMENTATION_CONTRACT.md:2499` | 「当前所有 gate/harness/fixture 入口均不存在」 | **存在**：`scripts/verify_hydraulic_stack.py` 74939 B，CLI `--help` 正常，G0–G6/acceptance/smoke 全部可调 |
| `IMPLEMENTATION_CONTRACT.md:2554` | 「AC-8 \| NOT RUN；recon 报告 Docker daemon 不可用，本轮未启动」 | daemon **可用**（§1.1）；本轮已实跑 G6 config 两 profile + live portable |
| `IMPLEMENTATION_CONTRACT.md:2641` | 「`scripts/verify_hydraulic_stack.py`、三个 checker、三个 benchmark harness 和 `test:perf:twin` 当前未交付」 | 入口与 checker 已交付；**G5 的 `benchmark_hydraulic_api.py` / `benchmark_hydraulic_nowcast.py` 仍确实缺失**（`ls scripts/benchmark_hydraulic*.py` 只有 `benchmark_hydraulic.py`）——但属 G5，不在 AC-8 范围 |
| `PRD_SUPPLEMENT.md:593` | 「**Docker live 未运行；本轮未验证**」 | 本轮已跑（config 两 profile + live portable 部分步骤） |

- **可修复**：是（改文档）。

### N5 —— 关于 F7 的一个限定观察（记录，不单列为阻断）

`test_deployment_contract.py` 与 `test_contract_gate_scripts.py` 本轮 35 passed（§1.10），说明仓库里存在**针对 compose 文件层**的 secret 断言且已通过。但 `_compose_env` 的 CI 固定 secret 仍存在（§2 F7）。两者不冲突：前者管 compose 文件，后者管门禁 harness。**不因测试全绿就把 F7 判为通过。**

---

## 3. AC-8 判据对照表

| AC-8 通过判据（`PRD_SUPPLEMENT.md:593`） | round-1 | **round-2** | 本轮依据 |
|---|---|---|---|
| 空卷可复现 | 部分通过 | **部分通过** | portable `down -v`→`up --build --wait` exit 0、15 服务 healthy（§1.4）；工作树 dirty **160** 文件、仅靠 `ca99e048` 复现不出（F8） |
| 无 latest | 通过 | **通过** | `grep -c ':latest' docker-compose.yml` = 0（§1.8） |
| 无固定 secret | 部分通过 | **部分通过** | compose 层通过；门禁 harness 仍硬编码（F7） |
| 演示全链路 | 不通过 | **不通过（且根因更硬）** | fresh 卷 38 表、业务表 0 行（F5）；**且 production 全部水力 API 恒 503，README 演示第一步即失败**（N3） |
| 恢复 hash/API 校验通过 | 不通过 | **不通过** | `gate_g6` 全函数 0 处 backup/restore，R-1…R-6 无执行路径（F2） |
| 两 profile（`IMPLEMENTATION_CONTRACT.md:2537`） | 不通过 | **不通过** | postgis config exit 1，两条 static_failures 与 round-1 逐字相同（F1）；postgis live 连启动都被 static 失败挡住 |
| （隐含）G6 门禁本身可运行 | 未测（live 通过） | **不通过** | live G6 在 SSE 探针上 13 分钟不终止，单独复现 150 s 不返回（N1） |
| （隐含）API/SSE 运行时可用 | 未测 | **不通过** | `/api/forecast/latest` 实测 503（N2）；`/readyz` 200 但 `code=RUNTIME_NOT_READY`（F4） |

---

## 4. 缺口清单与可修复性

| ID | 缺口 | 严重度 | 可由代码修复 | round-1 状态 |
|---|---|---|---|---|
| F1 | 根 Compose 无 PostGIS 变体，postgis profile 结构上跑不通 | **阻断** | **是** | 未修复 |
| F2 | `gate_g6` 未接线 backup→destroy→restore，fixture R-1…R-6 无执行路径 | **阻断** | **是**（纯接线，工具已在） | 未修复 |
| N1 | live G6 在 SSE 探针上永久挂起（`read(4096)` 对长连流无解） | **阻断** | **是** | 新引入 |
| N2 | `/api/forecast/latest` 实测 503，被新探针判 FAIL | **阻断** | **是**（须先修 N3） | 新引入 |
| N3 | production 下全部水力 API 恒 503：`durable_backend` 硬编码 `False`，全仓无置 `True` 处、无开关 | **阻断（演示）** | **是**（后端实现缺口） | 新发现（round-1 只找到一半） |
| F5 | fresh 卷无业务数据，「演示全链路」不可执行 | **阻断（演示）** | **部分**（需 seed 链路 + N3） | 未修复 |
| N4 | live 探针集合（5）与冻结 lock 声明（3）漂移；`gate_g6` 不用 `G6_PROBES`，smoke 与 g6 探针不一致 | 高 | **是** | 新引入 |
| F4 | `/readyz` 200 但 `code=RUNTIME_NOT_READY` / `schema.code=SPATIAL_BACKEND_UNAVAILABLE` | 高 | **是**（门禁侧已修；须修 `main.py` 的 `code` 语义） | 门禁已修，缺陷被暴露 |
| F6 | `python:3.11-slim` / `node:18.20.8-alpine` / `nginx:alpine` 未钉 digest | 中 | **是** | 未修复 |
| F7 | 门禁 harness `_compose_env` 硬编码固定 secret | 低 | **是** | 未修复 |
| F8 | 门禁 `down -v` 会删默认 project 卷 | — | **已修** | **已修复** |
| F8 | 工作树 dirty 160 文件，仅靠提交无法复现 | 中 | **否**（需提交，属流程） | 未修复（且 +3） |
| F9 | 契约/补充文档关于「入口未交付 / daemon 不可用」的记载已过期 | 低 | **是**（改文档） | 未修复 |
| N6 | AC-8 只依赖 G6，而 G6 需两 profile 同时 PASS → 当前无 PASS 可能 | 结构性 | 随 F1/N1 一并解决 | 新记录（下游后果） |

---

## 5. 本轮**未**运行（明确不冒充）

- G0（pytest/Black/isort/Flake8/mypy/npm ci/Vitest/lint/format/build）——未运行，属 AC-7 范围。
- G1、G2、G3、G4、G5 —— 未运行。
- **G6 live —— postgis** —— 未跑，config 阶段 exit 1，live 分支在 `:1622-1628` 直接被 static 失败挡住。
- **G6 live —— portable 的完整一轮** —— 未跑完。`down -v` / `up --build --wait` / `compose ps` 三步实测成功，
  但**在 HTTP 探针阶段永久挂起**，13 分钟后由我用 `TaskStop` 终止，**没有产出 `result.json`**。
  本轮不把它记为「live G6 通过」或「live G6 失败」——它**根本没有完成**。
- **backup→destroy→restore 实际往返** —— 未运行。工具存在，G6 未接线；本轮也未手工替代执行
  （手工执行不构成 G6 证据，且会额外销毁一台栈）。
- legacy characterization 零回归 —— 未运行。
- `/simulation` 页面 Playwright 演示 —— 未运行。
- acceptance gate —— 未运行（`AC-8 -> ['g6']` 且 G6 两 profile 均无 PASS artifact，运行只会得到 BLOCKED，
  不产生新信息）。AC-8 的阻断由 F1/N1/N6 的实测直接确定。
- F5 的业务数据查询 —— **已运行**（§1.7，`psql` 直查本轮自建 fresh 卷）。

## 6. 建议的最小修复顺序

1. **N1**（最紧急，因为门禁当前不可用）：把 SSE 探针改成只读响应头 / 整体 deadline / 短读，
   并给**整个探针阶段**加上限（现在只有 `up --wait` 有 `live_timeout_seconds`）。
2. **N3**：把 `durable_backend` 接到 SQLAlchemy repository 并在 production 装配（同时消解 N2）。
3. **F1**：根 compose 增加 PostGIS 镜像变体并让 G6 按 `--profile` 选图；或让 G6 组合加载
   `docker-compose.yml` + `docker/postgres/compose.yml` 跑通全栈。**修好前 G6 不可能有 PASS。**
4. **F2**：在 `gate_g6` 中按 `restore-manifest-v1.json` 编排 backup → verify-only → down -v →
   restore --apply --confirm → 复算 R-1/R-2 → API+SSE smoke（R-4）→ legacy characterization（R-5），
   落 `g6/<profile>/`。
5. **N4**：让 `gate_g6` 复用 `G6_PROBES`，把 SSE/forecast 两条并入（含 `required_fields`），
   同步 lock fixture 的 `probes` 与 `content_sha256`。
6. **F4**：修 `backend/app/main.py` 的 `/readyz` `code` 语义，使其在 `ready:true` 时不返回
   `RUNTIME_NOT_READY` / `SPATIAL_BACKEND_UNAVAILABLE`。
7. **F5**：提供可重复的演示数据 seed（并明确依赖 N3 先修）。
8. **F6**：三个 `FROM`/`ARG` 补 digest。**F7**：`_compose_env` 去掉固定 secret。
9. **F9**：更新 `IMPLEMENTATION_CONTRACT.md:2499/2554/2641` 与 `PRD_SUPPLEMENT.md:593` 的过期记载。
10. **流程（非代码）**：提交当前工作树，否则 AC-8 的任何证据都无法从 `ca99e048` 复现。

---

## 7. 审计声明

- 本文件所有「实测」结论均来自 §1 记录的命令与其真实输出；未运行项在 §5 单独列出，未计入通过。
- 验收过程只读：**未修改任何实现代码、migration、配置或 fixture**（§1.11 的 `git status --porcelain | wc -l`
  审计前后同为 160 可佐证）。
- 唯一副作用：创建过隔离 project `hydraulic-gate-portable`（由门禁自身的 `--project-name` 生成，
  见 F8），审计结束后已 `down -v` 清理干净（复查 0 容器 / 0 卷残留），本机 7 个既有容器全部完好。
- 结论不采信任何文档勾选或实现者自述。`README.md:173-181/239-241` 与 `operations.md:47-60` 关于
  「演示数据为空」「postgis FAIL」的陈述经本轮复核**属实**（README 保持诚实，这一点应记在实现方账上）；
  但 round-1 与本轮都在其基础上补出了文档未记载的更硬缺口：**N1 挂起、N2/N3 的 503**。
- 与 round-1 的差异据实更正：fresh 卷 `networks` 的准确行数是 **0**（round-1 记录的 1 行 `SINK-TEST`
  来自一台已存在的栈，不是 fresh 卷）。
