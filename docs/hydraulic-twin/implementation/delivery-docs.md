# 演示与运维文档交付记录（第 7 轮 / 阻断点再迁移 + 干净环境配方失效）

> 执行日期：2026-09-28。执行者只改文档，**未修改任何后端/Compose/前端代码**，**未 commit、未 push**。
> ownedPaths：`smart-city-drainage/README.md`、`smart-city-drainage/docs/hydraulic-twin/*.md`。
> 证据规则：§2 是本轮**真实执行过**的命令与输出；§5 列出本轮**一条都没跑**的东西。
> 凡本文写"通过/失败/已修复"都有对应命令；没有对应命令的，一律不写通过。

---

## 0. 本轮一句话结论

上一轮文档的两条核心结论**都已经过期**，方向还相反：

- 上一轮写"`forecast_policy` 因 `NoSuchColumnError: artifact_format` 恒 false →
  `/readyz` 固定 503 → backend unhealthy"。**该缺陷在当前源码里已被别的 owner 改掉**：
  `validate_file` 改为接收 `artifact_format` 入参（`backend/app/main.py:665-673`），
  对 `forecast_rules` 一路显式传 `None`（`:781`）。本轮实测 `GET /readyz` 返回
  **200 / `ready=true` / `reason=null`**，六项检查全过。
- 上一轮写"根 Compose 没有 `network-job-worker`"。该服务在 `docker-compose.yml:283-355`，
  `config --services` 本轮返回 **15** 个服务（上一轮 14，本轮新增 `notification-sink`）。

**但演示仍然跑不通——阻断点第三次迁移**：现在缺的是**演示数据**。
实测 `hydraulic.networks` 只有 1 行，且是 notification-sink 的投递测试网络
（`code=SINK-TEST`、`current_revision_id` 为空），而 `network_revisions` /
`network_nodes` / `pipe_segments` / `catchments` / `rainfall_scenarios` /
`network_jobs` / `simulations` / `nowcast_runs` / `forecast_alerts` /
`ab_comparisons` / `calibration_runs` / `observation_samples` **全部 0 行**。

另外发现两处**本轮新出现的文档配方失效**（都是"照抄就报错"，不是"照抄就误判"）：

1. `README`/`operations.md`/`CONTRACT_INDEX.md` 里的"`--env-file` 指向**空文件**"配方
   **已不能工作**。`--env-file` 是整体替换，空文件 = 零变量，2026-09-28 实测直接 exit 1
   （`required variable DATABASE_URL is missing a value`）。本轮把三处都改成了
   "`.env.example` + 补齐 6 个空值"的可复制配方，并实跑 exit 0。
2. `cp .env.example .env` **不足以**让 `config` 通过：14 个必填变量里有 **6 个在
   `.env.example` 中是空值**。本轮实测确认。

---

## 1. 本轮读取的材料

| 材料 | 用途 |
|---|---|
| `docs/hydraulic-twin/SOURCE_PRD.md` | 不可变需求基线；确认 AC-3/AC-4/AC-5/AC-6/AC-8 口径 |
| `docs/hydraulic-twin/operations.md`、`CONTRACT_INDEX.md`、`README.md` | 本轮修改对象 |
| `docs/hydraulic-twin/implementation/delivery-docs.md`（上一轮，357 行） | 上一轮自述 |
| `docs/hydraulic-twin/reviews/delivery-docs.md`（第 6 轮审查，383 行） | 上一轮结论 **NOT APPROVED / 1 high + 3 medium + 2 low**；本轮全部处置 |
| `backend/app/main.py`（1521 行）、`backend/app/api/hydraulic_routes.py`、`docker-compose.yml`（802 行）、`.env.example` | 行号与结论的取数来源 |
| `scripts/verify_hydraulic_stack.py`、`scripts/restore.py`、`backend/app/workers/simulation_worker.py`、`frontend/src/App.tsx`、`frontend/package.json` | 门禁入口、脚本 argparse、worker 入口、前端路由、package scripts 取数来源 |
| `.runtime/doc_checks.py`、`.runtime/route_dump.py`、`.runtime/api_cmd_check.py` | 三个检查器的实现（读了源码，确认各自校验什么、不校验什么） |

> 说明：`PRD_SUPPLEMENT.md` 与 `IMPLEMENTATION_CONTRACT.md` 本轮**未通读**——
> 本轮全部改动都是"live 取数 → 修正文档指针"，不涉及新增或改写产品语义。
> 涉及的口径（AC 状态、G0～G6 定义）一律沿用既有文档，本轮**没有改判任何 AC**。

---

## 2. 本轮实际执行的检查

### 2.1 起点：三个检查器

```text
$ python -B .runtime/doc_checks.py ; echo EXIT=$?
DOC_CHECKS_EXIT=1
ROUTE_CITATION_MISMATCH=['PATH-NOT-FOUND /health cited=backend/app/main.py:1392',
                         'PATH-NOT-FOUND /readyz cited=backend/app/main.py:1383',
                         'PATH-NOT-FOUND /metrics cited=backend/app/main.py:1146']
$ python -B .runtime/route_dump.py ; echo EXIT=$?      → ROUTE_DUMP_EXIT=0
$ python -B .runtime/api_cmd_check.py ; echo EXIT=$?   → API_CMD_EXIT=0
```

起点 `doc_checks.py` **exit 1**：3 个根级路由的引用行号全部位移
（`/health` 1392→1401、`/readyz` 1383→1392、`/metrics` 1146→1155）。

### 2.2 终点（本轮改完后重跑）

```text
$ python -B .runtime/doc_checks.py ; echo EXIT=$?
WS_PROBLEMS=0
LINKS_MISSING=none
BASH_SYNTAX_FAILURES=none
PACKAGE_MISSING=none
BASH_PLACEHOLDERS=none
UNEXPORTED_VARS=none
ROUTE_CITATION_MISMATCH=none
CITATIONS_OUT_OF_RANGE=none
DOC_CHECKS_EXIT=0
```

**上面 8 个键是稳定不变量**——它们全是"违规计数"（0 或 `none`），不随本文内容漂移，
**这才是该引用的部分**。

以下 3 个是**计数**，会因本文自身被 `doc_checks.py` 扫描而漂移
（`OWNED` 列表含本文件，见 `.runtime/doc_checks.py:20-25`）：

| 键 | 为什么会漂移 |
|---|---|
| `BASH_BLOCKS_CHECKED` | 本文每加一个 ```bash 块就 +1 |
| `CITATIONS_CHECKED` | 本文每加一个 `` `path:line` `` 就 +1 |
| `CITATIONS_SHORTHAND_UNRESOLVED` | 同上（本文件的裸文件名简称） |

> 上一轮审查的 **MEDIUM-4** 正是这个问题：把这类会漂移的数字当成"本轮真实执行"逐字引用。
> 本轮实测它们的量级是 `BASH_BLOCKS_CHECKED` 约 48、`CITATIONS_CHECKED` 约 160、
> `CITATIONS_SHORTHAND_UNRESOLVED` 约 14——**只给量级，不给终值**：
> 本文件每改一个字都可能把终值推高（本轮就发生过一次，159 → 160）。
> **可复现的是 `EXIT=0` 与上面那 8 个不变量。** 需要精确计数时自己重跑，不要引用本表。

`CITATIONS_SHORTHAND_UNRESOLVED` 的条目**全部来自本文**里的裸 `main.py` /
`hydraulic_routes.py` / `simulation_worker.py` / `verify_hydraulic_stack.py` 简写
（为表格宽度用的简称）。检查器把它归为"未解析"但**不计入失败**
（`.runtime/doc_checks.py:307`：只有含 `/` 的才算真引用并参与退出码）。

`CITATIONS_SHORTHAND_UNRESOLVED` 的条目**全部来自本文**里的裸 `main.py` /
`hydraulic_routes.py` / `simulation_worker.py` / `verify_hydraulic_stack.py` 简写
（为表格宽度用的简称）。检查器把它归为"未解析"但**不计入失败**
（`.runtime/doc_checks.py:307`：只有含 `/` 的才算真引用并参与退出码）。

`BARE_NPM_RUN_IN_BASH_BLOCK` 那条是**检查器假阳性**：`README.md` 那个块的首行是
`cd smart-city-drainage/frontend`，命令正确；检查器只认字面量 `cd frontend`
（`.runtime/doc_checks.py:119`）。本轮未改检查器（不在 ownedPaths）。

```text
$ python -B .runtime/route_dump.py
GET /health   backend/app/main.py:1401
GET /readyz   backend/app/main.py:1392
GET /metrics  …/prometheus_fastapi_instrumentator/instrumentation.py:259
TOTAL=98  API_TOTAL=68  ROOT_TOTAL=8
ROUTE_DUMP_EXIT=0

$ python -B .runtime/api_cmd_check.py
DOC_API_PATHS_CHECKED=31
DOC_API_PATHS_UNMATCHED=none
API_CMD_EXIT=0
```

`PACKAGE_SCRIPTS` 与 `frontend/package.json` 实时一致（`package.json` 无 `e2e`，
`CONTRACT_INDEX.md` §4"不要引用不存在的 `npm run e2e`"这句本轮仍然成立）。

### 2.3 Compose 渲染、必填变量与服务集合

本轮实测：`Docker 29.4.2` / `Docker Compose v5.1.3` / `HEAD=ca99e048` / 工作树 `dirty_state: true`。

**必填变量共 14 个**（`grep -o '\${[A-Za-z0-9_]*:?[^}]*}' docker-compose.yml | sort -u`）：

```text
DATABASE_URL  DATABASE_URL_SYNC  GRAFANA_ADMIN_PASSWORD  INFLUXDB_BUCKET  INFLUXDB_ORG
INFLUXDB_PASSWORD  INFLUXDB_TOKEN  JWT_AUDIENCE  JWT_ISSUER  JWT_PRIVATE_KEY
JWT_PUBLIC_KEY  POSTGRES_PASSWORD  REDIS_PASSWORD  REDIS_URL
```

**`.env.example` 里其中 6 个是空值**（逐个 `grep -m1 "^VAR=" .env.example` 后判空）：

```text
EMPTY: GRAFANA_ADMIN_PASSWORD   EMPTY: INFLUXDB_PASSWORD   EMPTY: JWT_PRIVATE_KEY
EMPTY: JWT_PUBLIC_KEY           EMPTY: POSTGRES_PASSWORD   EMPTY: REDIS_PASSWORD
```

正/负样本（同一台机，只改 env 文件）：

```text
# 负样本：cp .env.example .env 直接用
$ docker compose --env-file /tmp/no_fills.env --profile production --profile monitoring \
    -f docker-compose.yml config --quiet
error while interpolating services.postgres.environment.POSTGRES_PASSWORD:
  required variable POSTGRES_PASSWORD is missing a value: set POSTGRES_PASSWORD
NO_FILLS_EXIT=1

# 负样本：空文件（上一轮文档的配方）
$ export CLEAN_ENV=/tmp/empty.env && : > "$CLEAN_ENV"
$ docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring \
    -f docker-compose.yml config --quiet
error while interpolating services.network-job-worker.environment.DATABASE_URL:
  required variable DATABASE_URL is missing a value: set DATABASE_URL to the encoded PostgreSQL URL
EMPTY_ENV_EXIT=1

# 正样本：本轮写进 README §1 的配方，逐字照抄
$ cp .env.example "$CLEAN_ENV" && cat >> "$CLEAN_ENV" <<'ENV' …（7 行）… ENV
$ docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring \
    -f docker-compose.yml config --quiet
clean-env exit=0
```

服务集合（`--env-file` 指向正样本那份 env）：

| Profile | 数量 |
|---|---|
| 默认（无 `--profile`） | **12** |
| `production` | **13** |
| `monitoring` | **14** |
| 两者都开 | **15** |

15 个服务里含上一轮没有的 `notification-sink`（`docker-compose.yml:513-567`，`expose: 8030`）。
`G6_REQUIRED_SERVICES`（`scripts/verify_hydraulic_stack.py:44-58`）仍是 14 个，**不含**
`notification-sink` → 属"多于要求"，不是缺口。

主机端口 / expose（`config --format json` 的 `services[*].ports` 与 `[*].expose`）：

```text
backend 8000 | frontend 3000→8080 | grafana 3001→3000 | nginx 8080 | prometheus 9090
expose: simulation-worker 8010, network-job-worker 8021, nowcast-worker 8022,
        outbox-relay 8023, notification-sink 8030, notification-worker 8024
postgres/redis/influxdb/migrate: 两者皆无
```

**上一轮排障表给的核对命令实测返回值 21，不是服务数**：

```text
$ grep -c '^  [a-z-]*:' docker-compose.yml   → 21
$ grep -c '^  [a-z-]*:$' docker-compose.yml  → 21
```

多出的 6 个不是服务，是 `networks:` 的 `scn-network`（`:788`）和 5 个卷
（`pgdata`/`redisdata`/`influxdata`/`prometheusdata`/`grafanadata`）。本轮把该行改成
`config --services | sort` 口径命令。

### 2.4 G6 静态门禁（两个 profile 分别执行）

```text
$ python -B scripts/verify_hydraulic_stack.py --gate g6 --mode config --profile portable \
    --evidence-dir .runtime/evidence/dd-r7-portable
HYDRAULIC_STACK_STATUS=PASS
HYDRAULIC_STACK_EXIT=0
artifact sha256 = 58d1187f3a41dcedf1ff573240c0d724aa7e4801ace55667568458dc8784e85a
"static_failures": []
"scope_note": "Compose config is a configuration check only; it is not live G6 or AC-8 evidence."

$ python -B scripts/verify_hydraulic_stack.py --gate g6 --mode config --profile postgis \
    --evidence-dir .runtime/evidence/dd-r7-postgis
HYDRAULIC_STACK_STATUS=FAIL
HYDRAULIC_STACK_EXIT=1
artifact sha256 = a233accf37c1399bba2ff70edfa16072a21bcc8428204fb6baacbe99b20ad312
"static_failures": ["postgis profile does not select an explicit PostGIS image",
                    "postgis profile renders the portable default postgres image"]
```

**portable = PASS，postgis = FAIL**。两条报告的 `scope_note` 都自带限定，本文照抄，
**不把 exit 0 写成 G6 通过**。

> 上一轮审查的 **HIGH-1** 指出 `operations.md` §3.3 与 §12 把 portable 写成 FAIL、
> 而同文件另外三处 + README + CONTRACT_INDEX 写成 PASS。本轮已把 §3.3 与 §12
> 改成与本节同源的 PASS/FAIL 两段，并删掉"唯一未修的是这 4 个 worker"。

### 2.5 对**已在运行的栈**的只读观测（本轮最重要的发现）

限定先行：**这台栈不是本模块 `docker compose up` 起来的**，也不对应本模块的专用 project。
它是"当前代码的真实状态"证据，**不是** live G6，也**不是** AC-8 证据。

```text
$ docker ps --format '{{.Names}}\t{{.Status}}'
smart-city-drainage-backend-1            Up 16 minutes (healthy)
smart-city-drainage-network-job-worker-1 Up 18 minutes (healthy)
smart-city-drainage-simulation-worker-1  Up 18 minutes (healthy)
smart-city-drainage-outbox-relay-1       Up 18 minutes (healthy)
smart-city-drainage-nowcast-worker-1     Up 18 minutes (healthy)
smart-city-drainage-notification-worker-1 Up 23 minutes (healthy)
smart-city-drainage-postgres-1           Up 25 minutes (healthy)
smart-city-drainage-redis-1              Up 25 minutes (healthy)
smart-city-drainage-influxdb-1           Up 25 minutes (healthy)
smart-city-drainage-notification-sink-1  Up 26 minutes (healthy)
$ docker ps -a --format … --filter name=smart-city-drainage
smart-city-drainage-migrate-1             Exited (0) 16 minutes ago
（无 nginx / frontend 容器 —— 该栈未起 production profile）
```

`/readyz` —— **上一轮的"固定 503"已不成立**：

```text
$ curl -s http://127.0.0.1:8000/readyz
ready= True
reason= None
  required_tables = True
  missing_tables = []
  extensions = True
  missing_extensions = []
  hypertables = True
  worker_heartbeats = {'network-job': True, 'simulation': True, 'nowcast': True,
                       'outbox-relay': True, 'notification': True}
  forecast_policy = True

$ curl -s -w ' HTTP=%{http_code}' http://127.0.0.1:8000/health
{"status":"healthy"} HTTP=200
```

**代码侧根因也已消失**（`grep -n` 逐条确认）：

| 引用 | 实测内容 |
|---|---|
| `backend/app/main.py:665-673` | `def validate_file(row, schema_version, artifact_format)`，docstring 明写"caller states the value it actually holds rather than the validator reading a key that the rules SELECT never selects" |
| `backend/app/main.py:757` | `validate_file(row, schema_version, row["artifact_format"])` ← probability 一路 |
| `backend/app/main.py:781` | `validate_file(row, schema_version, None)` ← **rules 一路传 None** |
| `backend/app/main.py:725` | `"valid_until FROM hydraulic.forecast_rules"` ← SELECT 仍不含 `artifact_format`，但也不再读它 |

→ 旧的 `NoSuchColumnError` 路径在当前源码中**已不可达**。
所以若有人再看到同一个错，第一嫌疑是**旧镜像**而不是代码，本轮把这条写进了
`operations.md` §3.2.1 与 §11。

**但演示数据仍然为空**（`psql` 只读计数）：

```text
hydraulic.networks                 = 1   （code=SINK-TEST, current_revision_id 为空）
hydraulic.forecast_rules           = 2
hydraulic.probability_calibrations = 2
alembic_version                    = 0008_hydraulic_retention_seed

network_revisions=0  network_nodes=0  pipe_segments=0  catchments=0
rainfall_scenarios=0  network_jobs=0  simulation_attempts=0  nowcast_runs=0
forecast_alerts=0  ab_comparisons=0  calibration_runs=0  observation_samples=0
```

`hydraulic.networks` 那 1 行的内容：

```text
11111111-0000-0000-0000-0000000000aa | SINK-TEST | notification-sink delivery test | active | (revision 为空)
```

即：**它不是可仿真的拓扑**（无 revision、无节点管段），是 notification-sink 的投递测试残留。
所以"有 network 了"和"演示能跑"是两回事，文档里必须分开写。

**登录 401 是预期的，不是文档缺陷**：

```text
$ curl -s -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"Admin@123456"}'
HTTP=401  {"detail":"用户名或密码错误","code":"AUTH_REQUIRED", …}
```

README §3 的那组凭据依赖 `AUTH_FALLBACK_ADMIN_ENABLED=true`（development adapter 专用），
在 Compose 栈上必然 401。本轮把这条写进 README §3，避免读者误判。

### 2.6 smoke-header 探针（沿用上一轮结论，本轮未重跑）

`operations.md` §9.4.1 的三个用例结论**本轮没有重跑**——上一轮已实测
`scripts/restore.py:188-197` 的 `--smoke-header` 与 `:722-726` 的 `--smoke-header-env`
语义不同，本轮只重新核对了这两处**行号仍然成立**（`_parse_header` 确实在 `:188`，
`headers[env_name] = value` 确实在 `:722`）。结论沿用并标注为上一轮证据。

### 2.7 simulation worker 的两个分支（沿用上一轮实测，本轮未重跑）

上一轮实测：带 Compose 默认值（现 `:250`）→ `reason=database_unavailable`；
不设该变量 → `reason=solver_command_unavailable`（都 exit 3）。
本轮**没有重跑**这两个分支，只更新了行号，并补了本轮的新事实：
那台运行中的栈里 `simulation-worker-1` 是 `healthy`。

### 2.8 行号全量重取

`CONTRACT_INDEX.md` §8 第 13 条要求：凡在"可核验"段落里引用 Compose 行号，先 `grep -n` 重取。
本轮照做，并按第 10 条**逐条人工比对了那一行的内容**（检查器只验范围）。
`docker-compose.yml` 本轮 190(HEAD) → **802**（工作树），再次整体位移：

| 旧引用 | 旧声称内容 | 本轮实测 |
|---|---|---|
| `docker-compose.yml:9` | postgres 固定 timescale 镜像 | `:9`（未动） |
| `docker-compose.yml:172-177` | backend healthcheck `/readyz` | `:180-185`，URL 在 `:181` |
| `docker-compose.yml:195` | worker command | `:203` |
| `docker-compose.yml:242` | `SIMULATION_SOLVER_COMMAND` | `:250` |
| `docker-compose.yml:243-244` | `SIMULATION_WORKER_HEALTH_HOST/PORT` | `:251-252` |
| `docker-compose.yml:245-246` | `expose: 8010` | `:253-254` |
| `docker-compose.yml:259-264` | worker healthcheck | `:268`（URL 在该行） |
| `docker-compose.yml:275-343` | `network-job-worker` 块 | `:283-355` |
| `docker-compose.yml:348-420` | `nowcast-worker` 块 | `:356-433` |
| `docker-compose.yml:588-604` | nginx depends_on | `:690-705` |
| `docker-compose.yml:640-642` | frontend depends_on | `:742-744` |
| `docker-compose.yml:676` | `GF_SECURITY_ADMIN_PASSWORD` | `:778` |
| `docker-compose.yml:52` | `HYDRAULIC_SCHEMA_REVISION` | `:56`/`:135`/`:225`/`:311`/`:384`/`:462`/`:596` |

`backend/app/main.py` 1512 → **1521** 行：

| 旧引用 | 旧声称 | 本轮实测 |
|---|---|---|
| `main.py:1146` | `/metrics` 调用点 | `:1155`（构造 `:1151-1154`） |
| `main.py:1250-1275` | durable store 门禁 | `:1273-1284`（`DURABLE_STORE_UNAVAILABLE` 在 `:1280`） |
| `main.py:1276-1290` | forecast policy 门禁 | `:1285-1298`（`FORECAST_POLICY_UNAVAILABLE` 在 `:1295`） |
| `main.py:1379`/`1383` | `/readyz` | `:1392` |
| `main.py:1388`/`1392` | `/health` | `:1401` |
| `main.py:909-917` | readiness `required` 判定 | `:917-926` |
| `main.py:799-818` | `_check_worker_heartbeats` | `:808-825` |
| `main.py:711-717` | rules SELECT（缺 `artifact_format`） | 该代码**已不存在**；现 `:725` 是 SELECT 末行 |
| `main.py:678` | 读 `row["artifact_format"]` | 该代码**已不存在**；现 `:687` 是入参比较 |
| `simulation_worker.py:1874` | `def main(argv)` | `:1880`（`:1924` 是 `__main__`） |
| `verify_hydraulic_stack.py:44-59` | `G6_REQUIRED_SERVICES` | `:44-58` |

**未被本轮改动、且本轮抽查仍然成立的**：`hydraulic_routes.py:297`（`durable_backend = False`）、
`:2788`（`_rainfall_content_hash`）、`:3466-3481`（observed-comparison 固定 `not_ready`）、
`:3771`（`/forecast/latest`）、`frontend/src/App.tsx` 全部 10 行（`:57`–`:108` 逐条命中）、
`scripts/restore.py:188-197` / `:722-726` / `:823-864`、
`backend/alembic/versions/0002_hydraulic_schema.py:1805`（`artifact_format` 列定义）。

---

## 3. 上一轮审查 finding 的处置

| # | 严重度 | finding | 本轮处置 |
|---|---|---|---|
| 1 | **HIGH** | `operations.md` §3.3 与 §12 把 G6 portable 写成 FAIL，与同文件三处及实测矛盾 | ✅ 已改。§3.3 换成 portable PASS / postgis FAIL 两段并附 `scope_note`；删掉"唯一未修的是这 4 个 worker"；§12 改为"portable exit 0 / postgis exit 1" |
| 2 | MEDIUM | "根 Compose 没有 `network-job-worker`" 是过期事实（§4.3、§7.0） | ✅ 已改。两处都改成"根 Compose 已有（`:283-355`），真实原因是进程内 adapter + `DURABLE_STORE_UNAVAILABLE` 门禁" |
| 3 | MEDIUM | 7 处指向 `delivery-docs.md` 的 `§4.x` 悬空 | ✅ 已改。全部重指到本轮实际存在的子节（§2.1/§2.2/§2.3/§2.5/§2.6/§2.7/§9），并给本文件重排成 `§0～§9` 使这些锚点真实存在 |
| 4 | MEDIUM | 排障表给的 `grep -c` 返回 21 不是 15 | ✅ 已改。换成 `config --services \| sort` 口径命令，并写明 `grep -c` 会把 `networks:` 与 5 个卷一起数进去 |
| 5 | MEDIUM | 摘要 §2.1 引用的"终点"数字与实测不一致（自指漂移） | ✅ 已改，且**根治**：§2.2 不再引用会漂移的计数，只列 8 个稳定不变量（违规计数），把三个计数键的**漂移机制**和量级写进表里并明确"不要引用本表" |
| 6 | LOW | §9.4 的 `$NETWORK_ID` 无守卫（LOW-1） | ✅ 已改。§9.4 开头补 `export NETWORK_ID=""` + `: "${NETWORK_ID:?…}"`，并写明来源是 §4.2 |
| 7 | LOW | README §1 预检块与"前提"声明不是同一条命令（LOW-2） | ✅ 已改。主块现在自带 `--env-file "$CLEAN_ENV"`，且该文件在同一块里被构造出来 |

### 3.1 顺带修掉的两类**新**过期结论

| 项 | 上一轮写法 | 本轮实测 | 处置 |
|---|---|---|---|
| readiness / `forecast_policy` | "`/readyz` 固定 503 → backend unhealthy" | 200 / `ready=true` / `forecast_policy=true` | 三份文档全部改写；旧根因标注为"已不存在"并给出"若又看到先怀疑旧镜像" |
| 种子数据 | "`hydraulic.networks` 0 行" | 1 行，但是 `SINK-TEST` 测试残留 | 改写为"有 1 行但不是可仿真拓扑"，其余 12 张表仍 0 行 |
| `--env-file` 空文件配方 | "任意空文件" | exit 1（零变量） | 三份文档全部改为"`.env.example` + 补 6 项"配方，并实跑 |
| `.env.example` 6 个必填空值 | 上一轮只注意到 `GRAFANA_ADMIN_PASSWORD` | 实测 6 个 | README FAQ 0.1 与 `operations.md` §3.1 都写全 |
| `migrate` 与 `.env.example` revision 不一致 | "`0003_hydraulic_schema_gap` vs `0008`" | `.env.example` 已改为 `0008`，实测库 `alembic_version=0008` | §11 该行改为"已修"，附本轮实测值 |
| 服务集合 | 14 | 15（新增 `notification-sink`） | 三份文档全部更新，并说明它不在 `G6_REQUIRED_SERVICES` 内 |

---

## 4. 实际文档改动

### 4.1 `README.md`

1. **§1 Compose 配置预检整节重写**：合并成**一个自包含、逐字实跑 exit 0** 的块
   （`cp .env.example "$CLEAN_ENV"` + `cat >>` 补 6 项 + `--env-file` 渲染 + `config --services`），
   消除了上一轮"主块不带 `--env-file`"的自相矛盾（LOW-2）。
2. 新增"**别用空文件当 `--env-file`**"警告 + 14 个必填变量清单 + `.env.example` 6 个空值的实测。
3. **服务表加数量列**（12/13/14/15），并说明 `notification-sink` 属"多于要求"。
4. **"快速开始"的阻断说明整段重写**：删掉"两条可核验的原因"（`/readyz` 503 那套），
   换成"✅ readiness 已解除（附 200 与四项 true 的实测）" + "⚠️ 演示数据仍空（附 13 张表的 0 行计数）"
   + "⚠️ 那台栈没起 production profile，3000/8080 本轮未观测到可用"。
5. 端口表：`/readyz` 从"预期 503"改为"实测 200"；补 6 个 worker 的 `expose` 端口。
6. §2 fresh-volume 块全部加 `--env-file "$CLEAN_ENV"`，并写明**本轮没跑**这组命令、
   本轮只对另一台栈做了只读观测。
7. §3 补一条：README 那组 admin 凭据依赖 `AUTH_FALLBACK_ADMIN_ENABLED`，
   在 Compose 栈上实测 401 是预期行为。
8. **FAQ 0 重写**：根因改成"这两个地址需要 `--profile production`"（并给出正确起法），
   旧的 `NoSuchColumnError` 归因降级为历史线索。**FAQ 0.1 重写**：14 个必填变量 +
   "`cp .env.example .env` 不够"。

### 4.2 `docs/hydraulic-twin/operations.md`

1. **§1.1 快照表 17 行里 13 行重写**（G6、Compose 渲染、必填变量、services、端口、
   production API store、simulation worker、readiness、forecast policy、nowcast、
   种子数据），并给 5 类既往检查加上"2026-09-27 与 2026-09-28 两轮均未重跑"。
2. **§1.2 改写**：6 条"不能完成的演示"逐条附上本轮的**表计数证据**，
   并明说阻断点已从"readiness 挂了"变成"没有演示数据"。
3. **§3.1 干净环境配方重写**（见上）；服务表 12/13/14/15；G6 证据目录换成本轮的
   `.runtime/evidence/dd-r7-portable`。
4. **§3.2 整节重写**：判断顺序改成"看 `ready` 字段而不是只看 HTTP 码"；
   新增 **§3.2.1** 收纳 4 条只读定位命令 + 实跑边界声明。
5. **§3.3 改写**（HIGH-1）：portable PASS / postgis FAIL 两段。
6. §4.3 种子数据、§7.0 A/B 前置：删掉"根 Compose 没有 `network-job-worker`"（MEDIUM-1），
   改归因为进程内 adapter + durable store 门禁。
7. §9.4 补 `$NETWORK_ID` 守卫（LOW-1）；§9.4.1 的交叉引用改成 §2.6。
8. **§11 排障表改 5 行**（clean-env 配方、migrate/revision、forecast_policy、
   seed queued、nginx/frontend Created）+ 1 行口径命令（MEDIUM-3）。
9. §12 改写（HIGH-1）；**§13 改写并新增 §13.1**（本轮对运行中栈的只读观测 + 其限定）。
10. 8 处交叉引用改到本文件真实存在的锚点（§2.3 / §2.6 / §9）。

### 4.3 `docs/hydraulic-twin/CONTRACT_INDEX.md`

1. 头部 3 处交叉引用改锚点（§4.2/§4.3/§4.10 → §2.1/§2.2/§2.6/§2.7/§2.5）。
2. **§3.3 路由表**：`/health` → `main.py:1401`、`/readyz` → `:1392`、`/metrics` → `:1155`；
   forecast 门禁 → `:1285-1298`；`nowcast-worker` → `docker-compose.yml:356-433`。
3. **§5.1 整节重写**：可复制的干净 env 配方 + 12/13/14/15 数量表 +
   `notification-sink` 说明 + 6 个 expose 端口。
4. **§5.2**：证据目录换成本轮；`G6_REQUIRED_SERVICES` 行号 `:44-59` → `:44-58`，
   并写明**不含** `notification-sink`。
5. **§5.3 三条 bullet 重写**：worker 行号更新；`forecast_policy` 改为"已解除"并给出
   `:665-673`/`:757`/`:781` 三处证据；**新增**"当前真实阻断是数据不是代码"（13 张表 0 行）
   与"观测边界"（非本模块起的栈、无 production profile）两条。
6. **§6.1 / §5.3 的 `delivery-docs.md` 锚点**改到 §2.6 / §2.7 / §2.5。
7. **§8 维护规则**：
   - 第 12 条补"**`--env-file` 是整体替换不是追加**"与"`.env.example` 有 6 个必填空值"；
   - 第 13 条补本轮第二次整体位移的实例（802 行）与 4 组具体偏移；
   - **新增第 14 条**："已修复"和"仍然坏着"同样会过期，两种都要重新取证；
   - **新增第 15 条**："不把未验证项写成已通过"要**对称**执行——
     过期失败结论的破坏性与夸大成功相同，且 `doc_checks.py` 结构上抓不到自然语言结论的真伪。

---

## 5. 本轮未运行 / 不作为证据

以下**本轮一条都没有执行**，文中一律不写通过：

- `docker compose build/up/ps/down -v` 的 live 启动与破坏性卷操作（本轮只做 `config` **渲染**）；
- §3.2 的 4 条 `docker compose exec` 定位命令、§3.2.1 的 `down -v`/`up --build -d`/`ps --all`；
- §4～§9 的全部 HTTP 命令（seed、50 年、nowcast、告警/SSE、A/B、率定、备份恢复、回滚）——
  除一次**预期失败**的 `POST /api/auth/login`（401）外，没有任何写操作；
- 全量 `pytest`、任何 `vitest`、`npm ci` / `npm test` / `npm run build` / `lint` / `format:check`；
- `scripts/check_migration_gate.py`、`check_dependency_handoff.py`、`verify_database.py`、
  `backup_test.py`、`validate_monitoring.py`、Alembic live upgrade/downgrade、备份→销毁→恢复；
- §2.6 的 smoke-header 三用例、§2.7 的 worker 双分支（**沿用上一轮实测**，本轮只复核了行号）；
- 50 年一遇 289 点真实求解、nowcast 周期、告警落库、SSE replay、A/B、率定 —— 均 `NOT RUN`；
- G0～G5、live G6、acceptance —— 全部 `NOT RUN` / `BLOCKED`，本模块无权改判。

**关于那台运行中的栈的诚实限定**：§2.5 全部是**真实观测**
（`docker ps` / `curl` / `psql`，只读，未重启、未改任何容器）。
但它**不是**本模块 `docker compose up` 出来的，也不对应本模块的专用 project，
而且**没有起 `production` profile**。因此它是"当前代码的真实状态"证据，
**不是** AC-8、也不是 live G6 证据。

---

## 6. 变更边界与完整性

本模块只改了 4 个文件（3 个 ownedPaths + 本摘要）。改完后重算：

```text
<hash>  README.md
<hash>  docs/hydraulic-twin/operations.md
<hash>  docs/hydraulic-twin/CONTRACT_INDEX.md
```

（最终 hash 见本文件定稿后的 `sha256sum`；上一轮把 hash 写进摘要本身造成了自指漂移，
本轮不再在正文里贴自己的 hash。）

```text
$ git rev-parse --short=8 HEAD                → ca99e048
$ wc -l docker-compose.yml                    → 802
$ git show HEAD:./docker-compose.yml | wc -l  → 190   （未提交改动，+612 行）
$ git ls-files docs | wc -l                   → 0     （整棵 docs/ 未被跟踪）
$ wc -l backend/app/main.py                   → 1521
```

**越界项自查**：`docker-compose.yml` 与 `backend/**` 的 `M` 状态在本轮开始前就存在
（并行 owner 的未提交改动）。本轮**没有**写入这两个位置。
本轮唯一的额外写入是 `.runtime/evidence/dd-r7-{portable,postgis}/` ——
G6 门禁强制 evidence-dir 必须在仓库根内（`scripts/verify_hydraulic_stack.py` 会做
仓库根相对路径校验），`.runtime` 未被 Git 跟踪，与前几轮做法一致。

---

## 7. 风险与发布阻断

1. **演示数据是当前唯一的硬阻断**。`hydraulic.networks` 那 1 行是 `SINK-TEST` 测试残留，
   12 张表为 0 行。50 年/nowcast/告警/A/B/率定的所有步骤都依赖先有真实拓扑。
2. **postgis 门禁仍 FAIL**。根 Compose 没有 PostGIS 镜像变体，"双 profile G6"目前无法在
   根 Compose 上达成。
3. **§2.5 那台栈没有 `production` profile**，`http://localhost:8080` / `http://localhost:3000`
   本轮**未观测到可用**。文档已写明，但读者仍可能在自己起栈时遇到别的问题。
4. **根 Compose 是未提交的大幅改动**（190 → 802 行，本轮又整体位移一次）。
   本轮所有 Compose 行号只在 2026-09-28 当天成立。
5. **`.env.example` 缺 6 个必填值**这件事本身是配置模板的缺陷（不是文档缺陷），
   本轮只在文档里绕过，未改 `.env.example`（不在 ownedPaths）。
6. **本轮未重跑** 5 类既往检查。它们在 `operations.md` §1.1 里已逐行标注为"该轮"。

---

## 8. 交接给其他 owner（不在本模块执行）

| # | 动作 | 归属 | 证据位置 |
|---|---|---|---|
| 1 | 给 `.env.example` 补上那 6 个必填空值（或明确写"必须自行填写"），让 `cp .env.example .env` + `config` 自洽 | config owner | `operations.md` §3.1；本文 §2.3 |
| 2 | 评估根 Compose 是否需要 PostGIS 镜像变体，以解开 `--profile postgis` 的 G6 FAIL | compose owner | 本文 §2.4 |
| 3 | 给 `notification-sink` 一个明确定位说明（它不在 `G6_REQUIRED_SERVICES` 里，但会进默认 profile） | compose owner | 本文 §2.3 |
| 4 | 造一份可仿真的种子拓扑（带 revision + 节点 + 管段），否则 §4.3 之后的所有演示步骤都无法执行 | data / seed owner | 本文 §2.5；`operations.md` §1.2 |
| 5 | 清理或标注 `hydraulic.networks` 里那条 `SINK-TEST` 残留，避免被误当成演示数据 | data owner | 本文 §2.5 |
| 6 | 建议给 `doc_checks.py` 加"§N(.N)* 锚点必须能在被引文档标题里解析"的守卫（上一轮 MEDIUM-2 的根因）；另可考虑把 `BARE_NPM_RUN_IN_BASH_BLOCK` 的 `cd frontend` 判据放宽到含 `*/frontend` | 工具 owner（不在本模块 ownedPaths） | 本文 §3 第 3 条、§2.2 方框 |

---

## 9. 本模块未修的守卫缺口

沿用上一轮记录，仍未修（检查器在 `.runtime/`，不在本模块 ownedPaths）：

`doc_checks.py` 的 `BASH_PLACEHOLDERS` 正则是
`<[A-Za-z][A-Za-z0-9_]{2,}>`（`.runtime/doc_checks.py:133`），
**字符集里没有 `-`**，所以 `<alert-uuid>` 这类带连字符的占位符它抓不到。
本轮已再次全文扫过一遍，三份文档的 ```bash 块内**没有**这类残留
（`BASH_PLACEHOLDERS=none` 可佐证不含 `_`/纯字母的形态，但**不能**佐证含 `-` 的形态，
所以这一条靠人工确认）。要根治需改检查器正则，属工具 owner。

---

## 10. 与上一轮的差异说明

| 项 | 上一轮（第 6 轮） | 本轮（第 7 轮） | 依据 |
|---|---|---|---|
| `doc_checks.py` | 起点 exit 1（4 处） | 起点 exit 1（3 处）→ 终点 **exit 0** | §2.1 / §2.2 || G6 静态 portable | PASS | **PASS** | §2.4 |
| G6 静态 postgis | FAIL | **FAIL** | §2.4 |
| `operations.md` 内 G6 结论 | 自相矛盾（PASS 与 FAIL 并存） | **统一为 portable PASS / postgis FAIL** | §3 第 1 条 |
| services 集合 | 14 | **15**（新增 `notification-sink`） | §2.3 |
| `/readyz` | 503，`forecast_policy=false` | **200，`forecast_policy=true`** | §2.5 |
| `forecast_policy` 根因 | `main.py:711-717` + `:678` | **该代码已不存在**；现 `:665-673`/`:757`/`:781` | §2.5 |
| `hydraulic.networks` | 0 行 | **1 行**（`SINK-TEST` 测试残留） | §2.5 |
| `--env-file` 空文件配方 | "任意空文件"，文档称可 exit 0 | **实测 exit 1**，三份文档已改配方 | §2.3 |
| `.env.example` 空必填值 | 只注意到 1 个 | **6 个** | §2.3 |
| `migrate` / `.env.example` revision | 不一致（`0003` vs `0008`） | **已一致**（都是 `0008`） | `operations.md` §11 |
| `main.py` 行数 | 1512 | **1521** | §2.8 |
| `docker-compose.yml` 行数 | 700 | **802** | §2.8 |
| 交叉引用锚点 | 7 处悬空 | **全部指向本轮真实子节** | §3 第 3 条 |
| `CONTRACT_INDEX.md` §8 规则 | 14 条 | **16 条**（新增"结论也会过期"与"对称执行"） | §4.3 |
