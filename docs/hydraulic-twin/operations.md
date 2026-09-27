# 水力数字孪生演示与运维手册

> 最后按 live files 核对：2026-09-28。命令根目录均为 `smart-city-drainage/`。
>
> 证据边界：本文提供可复制的**当前命令**与目标验收步骤。R0 `R0_DESIGN_PASS` 只表示设计冻结通过；它不等于 G0～G6、AC-1～AC-8、真实 worker、nowcast、50 年一遇仿真或备份恢复已经通过。当前限制见第 1 节。
>
> 本轮（2026-09-28）实际执行的命令与原始输出记录在
> [`implementation/delivery-docs.md`](./implementation/delivery-docs.md)。凡本文写“已核对/已通过”的行，都对应那里的一条命令；没有对应命令的行，本文不写通过。

相关入口：

- 规范优先级与证据索引：[`CONTRACT_INDEX.md`](./CONTRACT_INDEX.md)
- 不可变需求：[`SOURCE_PRD.md`](./SOURCE_PRD.md)
- 冻结产品/验收基线：[`PRD_SUPPLEMENT.md`](./PRD_SUPPLEMENT.md)
- 可执行技术契约：[`IMPLEMENTATION_CONTRACT.md`](./IMPLEMENTATION_CONTRACT.md)

## 1. 当前真实能力与阻断

### 1.1 快照表

行内标注的是**该行结论被实测的那一轮**，不是"最新一轮"。带日期的"该轮"指该次执行；
未标日期的行表示 **2026-09-28 本轮**重新取过。完整命令与输出见
[`implementation/delivery-docs.md`](./implementation/delivery-docs.md)。

| 项目 | 当前事实 | 操作结论 |
|---|---|---|
| 设计门禁 | `artifacts/contracts/v3-independent-review.json` 为 `R0_DESIGN_PASS`，scope 明确只覆盖 stage D | 可引用设计已冻结；不可引用运行验收已通过 |
| migration 静态门禁 | `python -B scripts/check_migration_gate.py ...` **该轮（2026-09-26）** exit 0，输出 `MIGRATION_PHASE=POST_MIGRATION_APPROVED` / `MIGRATION_GATE=PASS`；**2026-09-27 与 2026-09-28 两轮均未重跑** | 只证明 migration 静态/审批边界，不证明空库 live upgrade |
| dependency handoff | **该轮（2026-09-26）** 相同 checker exit 0，输出 `DEPENDENCY_HANDOFF=PASS_WITH_DEFERRED`、`DEFERRED=frontend-target-runtime-evidence`、`G0_G5_BLOCKED_BY_DEFERRED=true`；**2026-09-27 与 2026-09-28 两轮均未重跑** | hash 漂移已消除；G0/G5 仍因前端目标环境证据 deferred 而未解锁 |
| G6 Compose 静态门禁 | **2026-09-28 本轮重跑**：`--gate g6 --mode config --profile portable` **exit 0**、`static_failures: []`；换 `--profile postgis` **exit 1**：`["postgis profile does not select an explicit PostGIS image", "postgis profile renders the portable default postgres image"]` | portable 的 G6 **静态**项已过；postgis 仍阻断，因为根 Compose 的 `postgres` 固定 `timescale/timescaledb@sha256:3f770468…`（`docker-compose.yml:9`），无 PostGIS 变体。两者都只是配置渲染，报告自带 `scope_note` 说不是 live G6/AC-8 |
| Compose 渲染 | `docker compose --env-file "$CLEAN_ENV" ... config --quiet` 2026-09-28 本轮 exit 0 | 仅证明配置可渲染，不是 live G6/AC-8。**前提是 `--env-file` 指向一份补齐 14 个必填变量的合成 env**，见 §3.1 |
| Compose 必填变量 | 根 Compose 有 **14 个 `${VAR:?}` 必填变量**；`.env.example` 里有 **6 个是空值**（`POSTGRES_PASSWORD`、`GRAFANA_ADMIN_PASSWORD`、`INFLUXDB_PASSWORD`、`JWT_PRIVATE_KEY`、`JWT_PUBLIC_KEY`、`REDIS_PASSWORD`） | `cp .env.example .env` **不足以**让 `config` 通过；必须再补这 6 项。`--env-file` 指向**空文件**同样不行（=零变量），见 §3.1 |
| Compose 实际 services | `config --services` 2026-09-28 本轮返回 **15** 个服务的集合（见 §3.1；**输出顺序随 Compose 版本变化，不是契约**） | 默认 **12** + `production` 的 `frontend` + `monitoring` 的 `prometheus`/`grafana`。本轮新增 `notification-sink`；上一版文档的 14 与更早的 11/7 已过期 |
| Compose 主机端口 | 2026-09-28 本轮 `config --format json` 显示只有 `backend 8000`、`nginx 8080`、`frontend 3000`、`prometheus 9090`、`grafana 3001` 发布主机端口；`postgres`/`redis`/`influxdb`/`migrate` 与 6 个 worker 无 `ports`（worker 用 `expose` 8010/8021/8022/8023/**8030**/8024） | InfluxDB 控制台默认不可从主机访问；见 README 端口表 |
| production API store | `hydraulic_store.durable_backend` 初始化为 `False`（`backend/app/api/hydraulic_routes.py:297`），production middleware 对水力路径返回 503 `DURABLE_STORE_UNAVAILABLE`（`backend/app/main.py:1273-1284`） | production 水力路由 fail closed；开发 API 仅是进程内诊断 adapter |
| simulation worker | **2026-09-28 本轮重取**：`backend/app/workers/simulation_worker.py:1880` 有 `def main(argv)`，`:1924` 起是 `if __name__ == "__main__":`；Compose 侧有 healthcheck（`docker-compose.yml:268` 打 `http://127.0.0.1:8010/readyz`）与 `expose: 8010`（`:253-254`）。**根 Compose 在 `docker-compose.yml:250` 默认了** `${SIMULATION_SOLVER_COMMAND:-python,-m,app.services.hydraulic.solvers.cli}` | 更早一轮实测：带该默认值 → 报 `database_unavailable`；不设该变量 → 报 `solver_command_unavailable`（都是 exit 3）。**2026-09-28 那台栈里该 worker 是 `healthy`**。`.env.example` 仍无此键 |
| readiness | `backend/app/main.py:1392` 的 `/readyz` 要求 `required_tables`、`extensions`、`hypertables`、`forecast_policy` 与 5 个 worker heartbeat（`_WORKER_HEARTBEAT_KEYS` 见 `backend/app/main.py:84-90`）全部有效（`required` 判定在 `:917-926`） | **2026-09-28 实测：`/readyz` 返回 200**，`ready=true`、`reason=null`，六项全过。上一轮"固定 503"的结论**已过期** |
| forecast policy 阻断 | **已解除**。`_validate_forecast_artifacts` 的 `validate_file` 现把 `artifact_format` 作为入参接收（`backend/app/main.py:665-673`），对 `forecast_rules` 一路显式传 `None`（`:781`），不再从 rules 行读该列。旧根因（`:711-717` 的 SELECT 缺 `artifact_format` + `:678` 读 `row["artifact_format"]`）在当前源码里**已不存在** | 2026-09-28 实测 `checks.forecast_policy=true`。但这只证明 readiness 过了，**不证明** forecast 能产出告警——见下一行 |
| nowcast | 有 forecast read/transition/SSE 路由（`backend/app/api/hydraulic_routes.py:3771` 起），没有 HTTP nowcast trigger；根 Compose 有 `nowcast-worker` 服务（`docker-compose.yml:356-433`） | **实测 `hydraulic.nowcast_runs` 0 行、`forecast_alerts` 0 行**：不能把 `/forecast/latest` 的 `not_ready` 当作成功 nowcast。production 下该路由还受 `backend/app/main.py:1285-1298` 的 policy 门禁 |
| observed-comparison | 实现固定返回 `status=not_ready`、`pairs=[]`、`observation_count=0`（`backend/app/api/hydraulic_routes.py:3466-3481`） | 率定不能产生真实结论；AC-5 仍 BLOCKED |
| AC 状态 | `backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json` 明确 AC-1/3/4/6/7/8 为 `NOT RUN`，AC-2/5 为 `BLOCKED` | README 只能提供诊断命令和目标步骤，不能宣称 AC PASS |
| 后端 focused 测试 | **该轮** 契约解释器（Python 3.11 + `requirements-py311.lock`）跑 7 个水力测试文件：`105 passed`；**2026-09-27 与 2026-09-28 两轮均未重跑** | 只覆盖这 7 个文件；**不等于**全量 pytest、Vitest 或 AC-7 |
| 前端 G5 | **该轮** `npm --prefix frontend run test:perf:twin ...` exit 2，`status=BLOCKED`，`failures=["target_environment_mismatch"]`；**2026-09-27 与 2026-09-28 两轮均未重跑** | 正确的 fail-closed；见 §2.3 |
| 备份脚本离线测试 | **该轮** `python -B scripts/backup_test.py` 11/11 通过；**2026-09-27 与 2026-09-28 两轮均未重跑** | 这是离线 fake-runner 测试，不是 live PostgreSQL/Redis/Influx 恢复证据 |
| 种子数据 | **2026-09-28 实测运行中的库**：`hydraulic.forecast_rules` 2 行、`hydraulic.probability_calibrations` 2 行、`hydraulic.networks` **1 行**（`code=SINK-TEST` 的 notification-sink 投递测试网络，`current_revision_id` 为空）。而 `network_revisions`/`network_nodes`/`pipe_segments`/`catchments`/`rainfall_scenarios`/`network_jobs`/`simulations`/`nowcast_runs`/`forecast_alerts`/`ab_comparisons`/`calibration_runs`/`observation_samples` **全部 0 行** | 上一版"完全没有 network"的结论**已过期**（现在有 1 行），但**演示仍跑不通**：那 1 行不是可仿真的拓扑（无 revision、无节点管段）。演示前必须先跑 §4.3 的 seed/import 造出真实拓扑 |

### 1.2 当前不能完成的演示

以下命令可以复制，但当前仓库**不能产生可验收结果**。注意阻断点已经变了：
**readiness 现在是过的**（§1.1），卡住的是**没有可用的演示数据与已验证的执行路径**。

1. seed/import job 从 `queued` 进入 durable terminal 并发布 network revision ——
   实测 `hydraulic.network_jobs` 0 行、`network_revisions` 0 行；
2. 50 年一遇 24 h / 5 min 的真实 289 帧求解、质量报告和 worker 进度 ——
   `simulations` 0 行；
3. 10 分钟 nowcast 周期、180 秒 SLO、durable alert/outbox/SSE replay ——
   `nowcast_runs` / `forecast_alerts` 均 0 行；
4. 两个真实 completed simulation 的 A/B —— `ab_comparisons` 0 行；
5. 外部可信观测驱动的率定改善 —— `observation_samples` / `calibration_runs` 均 0 行；
6. postgis + portable 两套 fresh-volume G6 与 live backup/destroy/restore。

不要通过手工修改数据库、注入内存结果或把 `queued`/`not_ready` 改成成功来绕过这些阻断。

## 2. 工具链与脚本真值

### 2.1 固定版本

| 工具 | 契约版本 | 依据 |
|---|---:|---|
| Python | 3.11 | `backend/pyproject.toml:9`（`requires-python = ">=3.11,<3.12"`）、`.python-version` |
| Node.js | 18.20.8 | `frontend/.node-version`、`frontend/package.json:6-10` |
| npm | 10.8.2 | `frontend/package.json:6-10`、`.npmrc` 的 `engine-strict=true` |
| Docker Compose | **契约未固定版本号** | `IMPLEMENTATION_CONTRACT.md` 全文未约束 Compose 版本；2026-09-26 实测 `v5.1.3`（`docker compose version`）。根 `docker-compose.yml` 只用 `services`/`profiles` 结构，这**不能**推出某个版本 |
| Bash examples | Git Bash / Linux Bash | PowerShell 备份入口见第 9 节 |

本机若不是上述版本，诊断结果不能作为 G0/G5 证据。`node -v` 与 `npm -v` 必须先记录。

### 2.2 真实 package scripts

`frontend/package.json:15-28` 当前只有这些验收相关脚本：

```bash
# RUN_ID 必须先导出：裸写 <RUN_ID> 会被 bash 当成 "从名为 RUN_ID> 的文件重定向"，
# 命令在运行时才失败，bash -n 这类语法检查抓不到。统一用 "$RUN_ID"。
export RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short=8 HEAD)"

npm --prefix frontend ci
npm --prefix frontend run test
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend run build
npm --prefix frontend run test:perf:twin -- \
  --fixture frontend/performance/fixtures/twin-map-5k-10k-v1.json \
  --duration-seconds 60 \
  --output "artifacts/hydraulic/$RUN_ID/g5/frontend.json"
```

注意：

- **不要在 ```bash 块里用裸 `<PLACEHOLDER>`**：bash 会把它解析成重定向而不是占位符，
  块在运行时才失败。本手册所有需要替换的路径/ID 一律写成 `"$VAR"` 形式并在上文 `export`。
- `RUN_ID` 的生成式见 §3.3（`date -u +%Y%m%dT%H%M%SZ` + `git rev-parse --short=8 HEAD`），
  §12 复用同一个变量，两处产物路径才能对上。
- 不存在 `npm run test:unit`、`npm run typecheck` 或 `npm run e2e`；不要在手册中发明这些脚本。
- `test:perf:twin` 不支持 `--help`。它要求 `--fixture`、`--output`，非 diagnostic 模式还要求 `--duration-seconds 60`（`frontend/performance/run-twin-map.mjs`）。
- 浏览器验收脚本是 `frontend/e2e/run-ac-smoke.mjs`，它**不是** package script。
- 后端没有根级 npm/package script；质量命令直接使用 `python -m pytest`、Black、isort、Flake8、mypy，入口约束见 `IMPLEMENTATION_CONTRACT.md` §17。

### 2.3 本轮执行环境与它为什么不能当 G5 证据

本轮实际环境：`node v25.9.0`、`npm 11.12.1`、Python 3.14.4（文档写作环境）、
`physical_cores=16`、`ram_bytes=59252035584`（约 59.25 GiB）。

`test:perf:twin` 因此在测量前 fail-closed，本轮原始输出：

```text
FRONTEND_PERFORMANCE_STATUS=BLOCKED
FRONTEND_PERFORMANCE_SMOKE_STATUS=BLOCKED
FRONTEND_PERFORMANCE_EXIT=2
```

artifact 内的 `target_environment_mismatches`：

```text
node_version=25.9.0; expected=18.20.8
available_cores=16; expected=4
ram_bytes=59252035584; expected=8589934592
```

`BLOCKED` 是 harness 的正确行为，既不是 G5 PASS 也不是 G5 FAIL；必须换到
Node 18.20.8 / 4 核 / 8 GiB 的目标机重跑。

## 3. 全新环境 Compose

### 3.1 仅做配置渲染（可安全复制）

以下值只用于让 Compose 插值通过，**不是生产凭据，也不能启动后端认证**：

```bash
cd smart-city-drainage

export COMPOSE_PROJECT_NAME=scn-hydraulic-doc
export HYDRAULIC_SPATIAL_BACKEND=portable
export POSTGRES_PASSWORD=doc-postgres-only
export REDIS_PASSWORD=doc-redis-only
export INFLUXDB_PASSWORD=doc-influx-only
export INFLUXDB_TOKEN=doc-influx-token-only
export INFLUXDB_ORG=smart-city
export INFLUXDB_BUCKET=sensor_data
export DATABASE_URL='postgresql+asyncpg://drainage_admin:doc-postgres-only@postgres:5432/smart_drainage'
export DATABASE_URL_SYNC='postgresql://drainage_admin:doc-postgres-only@postgres:5432/smart_drainage'
export REDIS_URL='redis://:doc-redis-only@redis:6379/0'
export JWT_PRIVATE_KEY=doc-private-key-not-runtime-valid
export JWT_PUBLIC_KEY=doc-public-key-not-runtime-valid
export JWT_ISSUER=smart-city-drainage
export JWT_AUDIENCE=smart-city-api
# grafana 服务的 ${GRAFANA_ADMIN_PASSWORD:?...}（docker-compose.yml:676）。Compose 对
# 整个文件做插值，**不带 --profile monitoring 时同样会因它 exit 1**；`.env.example` 不提供它。
export GRAFANA_ADMIN_PASSWORD=doc-grafana-only

docker compose --profile production --profile monitoring \
  -f docker-compose.yml config --quiet

docker compose --profile production --profile monitoring \
  -f docker-compose.yml config --services
```

> **别让本机 `.env` 掩盖缺失的 export。** `docker compose` 会自动读取工作目录下的 `.env`；
> 仓库根的 `.env` 被 `.gitignore:31` 忽略（`git ls-files .env` → 0 条），它恰好定义了这些必填变量。
> 要确认这段在**干净 clone** 上也能过，用：
>
> ```bash
> # 复制 .env.example 拿默认值，再补齐它留空的 6 个必填变量
> export CLEAN_ENV=/tmp/clean.env
> cp .env.example "$CLEAN_ENV"
> cat >> "$CLEAN_ENV" <<'ENV'
> POSTGRES_PASSWORD=doc-postgres-only
> GRAFANA_ADMIN_PASSWORD=doc-grafana-only
> INFLUXDB_PASSWORD=doc-influx-only
> JWT_PRIVATE_KEY=doc-private-key-not-runtime-valid
> JWT_PUBLIC_KEY=doc-public-key-not-runtime-valid
> REDIS_PASSWORD=doc-redis-only
> REDIS_URL=redis://:doc-redis-only@redis:6379/0
> ENV
> docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring \
>   -f docker-compose.yml config --quiet
> echo "clean-env exit=$?"
> ```
>
> ⚠️ **不能用空文件。** `--env-file` 整体替换默认 env 解析，空文件 = 零变量。
> 2026-09-28 实测空文件直接 exit 1：
> `required variable DATABASE_URL is missing a value: set DATABASE_URL to the encoded PostgreSQL URL`。
> 根 Compose 里 `${VAR:?set …}` 形式的必填变量共 **14 个**：
> `DATABASE_URL`、`DATABASE_URL_SYNC`、`GRAFANA_ADMIN_PASSWORD`、`INFLUXDB_BUCKET`、
> `INFLUXDB_ORG`、`INFLUXDB_PASSWORD`、`INFLUXDB_TOKEN`、`JWT_AUDIENCE`、`JWT_ISSUER`、
> `JWT_PRIVATE_KEY`、`JWT_PUBLIC_KEY`、`POSTGRES_PASSWORD`、`REDIS_PASSWORD`、`REDIS_URL`。
> `.env.example` 只把其中 **6 个**留了空值（上面 `cat >>` 补的 6 项 + 覆盖 `REDIS_URL`）。
> 注意 `REDIS_URL` 必须在 `.env` 里指向 compose 网络内的 `redis` 主机名；
> `.env.example` 的默认值是 `localhost`，直接沿用会让容器内连不上。

本轮环境：`Docker 29.4.2`、`Docker Compose v5.1.3`（2026-09-28 重跑）。
上面整段合成 env 的 `config --quiet` **exit 0**；把 `cat >>` 那段删掉重跑则 **exit 1**，
报 `required variable POSTGRES_PASSWORD is missing a value`。
两种情况的原始输出见
[`implementation/delivery-docs.md`](./implementation/delivery-docs.md) §2.3。
`config --services` 的**输出顺序随 Compose 版本变化，因此不是契约**——按下表的**集合**核对，不要逐行 diff：

| Profile | Services（集合，无序） | 数量 |
|---|---|---|
| 默认（无 `--profile`） | `postgres`、`redis`、`influxdb`、`migrate`、`backend`、`simulation-worker`、`network-job-worker`、`nowcast-worker`、`outbox-relay`、`notification-sink`、`notification-worker`、`nginx` | 12 |
| `production` | 默认集合 + `frontend` | 13 |
| `monitoring` | 默认集合 + `prometheus`、`grafana` | 14 |
| 两者都开 | 上面全部 | **15** |

服务名集合（15 个；`G6_REQUIRED_SERVICES` 只要求其中 14 个，**2026-09-28 全部已在**）：

```text
backend  frontend  grafana  influxdb  migrate
network-job-worker  nginx  notification-sink  notification-worker
nowcast-worker  outbox-relay  postgres  prometheus  redis
simulation-worker
```

对照 `scripts/verify_hydraulic_stack.py:44-58` 的 `G6_REQUIRED_SERVICES`（14 个，**不含**
`notification-sink`），**当前没有缺口**：多出来的那 1 个是"多于要求"，不影响门禁。
G6 静态 config 检查实测：

```bash
python -B scripts/verify_hydraulic_stack.py --gate g6 --mode config \
  --profile portable --evidence-dir .runtime/evidence/dd-r7-portable
# → HYDRAULIC_STACK_STATUS=PASS / HYDRAULIC_STACK_EXIT=0 / "static_failures": []
```

`--profile postgis` 仍 exit 1（根 Compose 没有 PostGIS 镜像变体）。
两条都是**配置渲染**结论，报告自带 `scope_note`：不是 live G6，也不是 AC-8 证据。

### 3.2 fresh-volume 启动诊断

> 破坏性命令：会删除本 Compose project 的命名卷。只在专用演示 project 上执行。

```bash
# 承接 3.1 的 CLEAN_ENV
docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring \
  -f docker-compose.yml down -v --remove-orphans

docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring \
  -f docker-compose.yml up --build -d

docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring \
  -f docker-compose.yml ps --all

curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/readyz
```

起完之后按这个顺序判断（这是**故障定位步骤**，不是"AC-8 已通过"的命令）：

1. `/health` 200 —— 只证明进程可响应，**不含任何求解/nowcast/告警执行**。
2. `/readyz` —— 应看 `ready` 字段而不是只看 HTTP 码。**2026-09-28 实测 200 且
   `ready=true`**（`required_tables`/`extensions`/`hypertables`/`forecast_policy` 四项全 `true`，
   5 个 worker heartbeat 全新鲜）。上一轮文档写的"固定 503"**已过期**。
3. `frontend` 的 `depends_on: backend: service_healthy`（`docker-compose.yml:742-744`）
   与 `nginx` 的 6 条 `service_healthy` 依赖（`:690-705`）——backend healthy 后二者才会起。
   若它们停在 `Created`，先回第 2 步看 `/readyz` 的 `ready`，再回第 4 步看 worker。
4. `docker compose ps --all` 里 6 个 worker 是否 `healthy`。任一个不 healthy，
   `/readyz` 的 `worker_heartbeats` 会有 `false`，`ready` 随之 `false`。
5. Prometheus/Grafana 的 rules/dashboard 文件没有在根 Compose 里挂载，
   不会仅因文件存在而自动加载。

**2026-09-28 的实跑边界（务必读）**：本轮**没有**执行上面这组 `down -v` / `up`——
它会删卷。只在一台**别的 project** 已存在的栈上做了只读观测：
`docker ps` 显示 12 个容器（10 个 `Up (healthy)` + `migrate` `Exited (0)` + `notification-sink` `Up`），
`/health` 与 `/readyz` 均 200，**且那台栈没起 `production` profile**，
所以 `nginx`/`frontend` 容器根本不存在。那是"当前代码的真实状态"，
**不是**本模块自己起的栈，也**不构成** live G6 或 AC-8 证据。

#### 3.2.1 `/readyz` 仍不 ready 时的定位命令

以下四条在**栈已起来**的机器上执行，只读：

```bash
# 1) 先看后端容器到底哪一项没过
docker compose exec backend python -c "
import asyncio, json
from app.main import check_runtime_readiness
r = asyncio.get_event_loop().run_until_complete(check_runtime_readiness())
print(json.dumps(r.get('checks'), ensure_ascii=False, indent=2, default=str))
"
```

⚠️ **裸进程里 `worker_heartbeats` 会全 `false`，那是假象**：`check_runtime_readiness` 里的
`_check_worker_heartbeats`（`backend/app/main.py:808-825`）要连 Redis 才读得到值，
裸 `python -c` 没连 Redis。**先 `redis_client.connect()` 再测**，否则会误判成 worker 故障：

```bash
docker compose exec backend python -c "
import asyncio, json
from app.core.redis_client import redis_client
from app.main import check_runtime_readiness
async def run():
    await redis_client.connect()
    r = await check_runtime_readiness()
    print(json.dumps(r.get('checks'), ensure_ascii=False, indent=2, default=str))
    print('ready =', r.get('ready'))
asyncio.get_event_loop().run_until_complete(run())
"
```

要直接看 forecast policy 的根因，单独调一次校验函数（它抛出的异常类型就是线索）：

```bash
docker compose exec backend python -c "
import asyncio
from app.main import _load_forecast_policy_config, _validate_forecast_artifacts
cfg = _load_forecast_policy_config()
print('config_loaded =', cfg is not None)
try:
    print(asyncio.get_event_loop().run_until_complete(_validate_forecast_artifacts(cfg)))
except Exception as e:
    print('ARTIFACT_VALIDATION_ERROR:', type(e).__name__, e)
"
```

> **历史线索**：2026-09-27 那一轮，这条命令输出的是
> `ARTIFACT_VALIDATION_ERROR: NoSuchColumnError … 'artifact_format'`。
> **该缺陷已在当前源码中不存在**——`validate_file` 现在把 `artifact_format` 作为**入参**接收
> （`backend/app/main.py:665-673`），对 `forecast_rules` 一路显式传 `None`（`:781`），
> 对 `probability_calibrations` 一路才读 `row["artifact_format"]`（`:757`）。
> 2026-09-28 实测该函数不再抛异常，`/readyz` 的 `forecast_policy` 为 `true`。
> 若你**又**看到同一个 `NoSuchColumnError`，说明你跑的是**旧镜像**——先
> `docker compose build backend && docker compose up -d backend`，再复现。

**不要**把 policy 校验失败误判成 `forecast-policy.json` 或它的 `.sha256` sidecar 损坏——
sidecar 记的是 **canonical JSON** 的哈希，不是文件原始字节的哈希。
用 `sha256sum` 直接哈原始字节会得到**假阳性的"不匹配"**。正确校验方式：

```bash
docker compose exec backend python -c "
import hashlib, json, pathlib
p = pathlib.Path('/opt/scn/hydraulic/forecast-policy/forecast-policy.json')
payload = json.loads(p.read_text(encoding='utf-8'))
canon = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(',', ':'), allow_nan=False).encode('utf-8')
expected = hashlib.sha256(canon).hexdigest()
sidecar = pathlib.Path(str(p) + '.sha256').read_text(encoding='utf-8')
if sidecar.endswith('\n'):
    sidecar = sidecar[:-1]
print('expected =', expected)
print('sidecar  =', sidecar)
print('MATCH    =', sidecar == expected)
"
```

> 上面这 4 条 `docker compose exec` 命令 **2026-09-28 本轮一条都没有运行**——
> 它们是留给你在**自己的**栈上定位用的。**这不等于 AC-8 通过。**


### 3.3 postgis/portable 目标门禁

`docker/postgres/compose.yml` 只定义 `postgres-postgis` 与 `postgres-portable`
两个数据库 profile，并没有把完整应用栈接入；不能把它当成 G6。数据库 profile
本身的构建/校验步骤见 `docker/postgres/README.md`（host 端口 `127.0.0.1:55432`
postgis、`127.0.0.1:55433` portable，仅供本地显式校验）。发布候选必须从仓库根
分别执行：

```bash
export RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short=8 HEAD)"

# 静态配置预检（不是 live G6/AC-8；脚本自己会写这一句 scope_note）
python -B scripts/verify_hydraulic_stack.py \
  --gate g6 --profile portable --mode config \
  --run-id "$RUN_ID" \
  --evidence-dir "artifacts/hydraulic/$RUN_ID/g6/portable"

# live G6：两个 profile 必须各自执行、不得互相替代
python -B scripts/verify_hydraulic_stack.py \
  --gate g6 --profile portable --mode live \
  --run-id "$RUN_ID" \
  --evidence-dir "artifacts/hydraulic/$RUN_ID/g6/portable"

python -B scripts/verify_hydraulic_stack.py \
  --gate g6 --profile postgis --mode live \
  --run-id "$RUN_ID" \
  --evidence-dir "artifacts/hydraulic/$RUN_ID/g6/postgis"
```

本轮 `--mode config` 的两个 profile **分别**执行，结果不同（2026-09-28）：

```text
# --profile portable
HYDRAULIC_STACK_STATUS=PASS
HYDRAULIC_STACK_EXIT=0
"static_failures": []

# --profile postgis
HYDRAULIC_STACK_STATUS=FAIL
HYDRAULIC_STACK_EXIT=1
"static_failures": ["postgis profile does not select an explicit PostGIS image",
                    "postgis profile renders the portable default postgres image"]
```

两条 artifact 的 `scope_note` 都写着同一句：
`Compose config is a configuration check only; it is not live G6 or AC-8 evidence.`

结论：**portable 的静态项已无缺口**（上一轮文档在这里写 FAIL 并列出
`missing target services: network-job-worker, notification-worker, nowcast-worker, outbox-relay`，
那是**已被修复的旧问题**，本轮不再复现）；**postgis 仍 FAIL**，因为根 Compose 的 `postgres`
固定 `timescale/timescaledb@sha256:3f770468…`（`docker-compose.yml:9`），没有 PostGIS 变体。
两个 profile 不能互相替代，也不能用 `config` 结果代替上面那两条 `--mode live`。


## 4. 本地 API 诊断（非 acceptance）

该流程使用 `HydraulicApiStore` 进程内 adapter，目的是检查 HTTP wire contract。重启后数据消失，**不得作为 G2/G3/G6 证据**。

### 4.1 启动

先按 `backend/requirements-py311.lock` 安装 Python 3.11 依赖，然后：

```bash
cd smart-city-drainage/backend
export DEPLOYMENT_ENVIRONMENT=development
export AUTH_FALLBACK_ADMIN_ENABLED=true
export SCN_ENV_FILE=.env.example

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

固定 fallback 仅在 development/test 可用；production Compose 强制 `AUTH_FALLBACK_ADMIN_ENABLED=false`。不要把 `Admin@123456` 当生产账户。

另开 Bash 终端：

```bash
cd smart-city-drainage
export BASE=http://127.0.0.1:8000
export TOKEN="$(
  curl -fsS -X POST "$BASE/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"Admin@123456"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
)"

curl -fsS "$BASE/health"
curl -i "$BASE/readyz"   # 当前诊断环境预期可能 503
```

### 4.2 创建 network revision

所有 hydraulic 路由都在 `router = APIRouter(prefix="/api")` 下（`backend/app/api/routes.py:80` 及挂载处）。创建 mutation 需要 operator/admin 和 8～128 字符 `Idempotency-Key`。`NetworkCreateRequest` 的字段与默认值见 `backend/app/api/hydraulic_routes.py:363-370`。

```bash
export DEMO_CODE="SH-DEMO-$(date +%s)"
export NETWORK_CREATE_KEY="network-create-$DEMO_CODE"

NETWORK_JSON="$(
  curl -fsS -X POST "$BASE/api/networks" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Idempotency-Key: $NETWORK_CREATE_KEY" \
    -H 'Content-Type: application/json' \
    -d "{\"code\":\"$DEMO_CODE\",\"name\":\"水力演示网络\",\"status\":\"active\",\"crs\":\"EPSG:4326\",\"spatial_backend\":\"portable\"}"
)"

export NETWORK_ID="$(printf '%s' "$NETWORK_JSON" | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
export REVISION_ID="$(printf '%s' "$NETWORK_JSON" | python -c 'import json,sys; print(json.load(sys.stdin)["current_revision_id"])')"

printf '%s\n' "$NETWORK_JSON"
```

### 4.3 种子数据

seed 只允许 admin；路由是 `POST /api/networks/{network_id}/seed`
（`backend/app/api/hydraulic_routes.py:2738`），body 模型 `SeedRequest`
（`backend/app/api/hydraulic_routes.py:423-432`，`n<=100`、`m<=100`、`k<=99`）：

```bash
SEED_JSON="$(
  curl -fsS -X POST "$BASE/api/networks/$NETWORK_ID/seed?expected_revision_id=$REVISION_ID" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Idempotency-Key: seed-$DEMO_CODE" \
    -H 'Content-Type: application/json' \
    -d '{"n":3,"m":4,"k":1,"random_seed":50,"west":121.30,"south":31.15,"east":121.60,"north":31.32}'
)"

export SEED_JOB_ID="$(printf '%s' "$SEED_JSON" | python -c 'import json,sys; print(json.load(sys.stdin)["network_job_id"])')"
printf '%s\n' "$SEED_JSON"

curl -fsS "$BASE/api/network-jobs/$SEED_JOB_ID" \
  -H "Authorization: Bearer $TOKEN"
```

查询 job 的真实路径参数名是 `{job_id}`（`backend/app/api/hydraulic_routes.py:2600`），
不是 `{network_job_id}`；路径模板只影响 OpenAPI 展示，URL 拼写两者都能命中，
但引用路由时不要写错名字。

导入/校验 job 同样可用（都会停在 `queued`）：

```bash
VALIDATE_JSON="$(
  curl -fsS -X POST "$BASE/api/networks/$NETWORK_ID/validate" \
    -H "Authorization: Bearer $TOKEN" \
    -H 'Idempotency-Key: validate-$DEMO_CODE' \
    -H 'Content-Type: application/json' \
    -d "{\"strict\":true,\"expected_revision_id\":\"$REVISION_ID\"}"
)"
printf '%s\n' "$VALIDATE_JSON"

# 从响应里取 ID，不要把 <validation_id> 直接写进 URL：
# 引号里的尖括号会被 bash 原样发出，URL 里带着 "<validation_id>"，得到的是一个
# 看不出原因的 404。取不到就停下。
export VALIDATION_ID="$(printf '%s' "$VALIDATE_JSON" | python -c 'import json,sys; print(json.load(sys.stdin)["validation_run_id"])')"
: "${VALIDATION_ID:?VALIDATION_ID 未取到——先看上面 VALIDATE_JSON 的实际内容}"

curl -fsS "$BASE/api/validation-runs/$VALIDATION_ID" -H "Authorization: Bearer $TOKEN"
```

字段名以实现为准，不要照着 URL 模板猜：validate 响应给的是 **`validation_run_id`**
（`backend/app/api/hydraulic_routes.py:2578-2583`），不是 `validation_id`；查询路由的路径
参数才叫 `{validation_id}`（`:2590`）。

import 同理，先 `POST /api/networks/import` 拿响应里的 `import_id`，再查询。注意 import
只接受一个 `file` 字段（`backend/app/api/hydraulic_routes.py:1420-1426`），**没有**
`expected_revision_id`：

```bash
IMPORT_JSON="$(
  curl -fsS -X POST "$BASE/api/networks/import" \
    -H "Authorization: Bearer $TOKEN" \
    -H 'Idempotency-Key: import-one' \
    -F "file=@./sample.inp"
)"
printf '%s\n' "$IMPORT_JSON"
export IMPORT_ID="$(printf '%s' "$IMPORT_JSON" | python -c 'import json,sys; print(json.load(sys.stdin)["import_id"])')"
: "${IMPORT_ID:?IMPORT_ID 未取到——先看上面 IMPORT_JSON 的实际内容}"

curl -fsS "$BASE/api/network-imports/$IMPORT_ID" -H "Authorization: Bearer $TOKEN"
```

当前 adapter 的真实预期是 `status=queued`。注意归因：**根 Compose 早已有 `network-job-worker`**
（`docker-compose.yml:283-355`，`config --services` 返回含它的 15 个服务），所以停住的原因不是
"缺 worker"。真实原因是这一节走的是**进程内 `HydraulicApiStore` adapter**
（`hydraulic_store.durable_backend` 初始化为 `False`，`backend/app/api/hydraulic_routes.py:297`），
在 `DEPLOYMENT_ENVIRONMENT=production` 下还会先被 `DURABLE_STORE_UNAVAILABLE` 门禁挡住
（`backend/app/main.py:1273-1284`）。不要轮询到超时后手工改成 completed。

## 5. 50 年一遇 / 24 h / 5 min

### 5.1 生成冻结的 289 点请求

fixture `backend/tests/fixtures/hydraulic/acceptance/v1/rainfall_50y_24h.json` 明确标记
`is_ac3_evidence=false`，其固定值为 `kind=design`、`return_period_years=50`、
`step_seconds=300`、`duration_seconds=86400`、`point_count=289`、
`start_at=2026-09-25T00:00:00Z`。以下命令只生成与该 fixture 一致的 HTTP request：

```bash
mkdir -p .runtime/hydraulic-demo
export RAINFALL_REQUEST="$PWD/.runtime/hydraulic-demo/rainfall-50y.json"

python - "$NETWORK_ID" > "$RAINFALL_REQUEST" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

network_id = sys.argv[1]
fixture = json.loads(Path("backend/tests/fixtures/hydraulic/acceptance/v1/rainfall_50y_24h.json").read_text(encoding="utf-8"))
start = datetime.fromisoformat(fixture["start_at"].replace("Z", "+00:00"))
pattern = fixture["intensity_pattern_mm_h"]
points = [
    {
        "point_index": index,
        "valid_at": (start + timedelta(seconds=fixture["step_seconds"] * index)).isoformat().replace("+00:00", "Z"),
        "intensity_mm_h": float(pattern[index % len(pattern)]),
    }
    for index in range(fixture["point_count"])
]
payload = {
    "network_id": network_id,
    "name": "50-year 24-hour request-contract smoke",
    "kind": fixture["kind"],
    "return_period_years": fixture["return_period_years"],
    "step_seconds": fixture["step_seconds"],
    "duration_seconds": fixture["duration_seconds"],
    "source_type": "synthetic",
    "algorithm": "synthetic-design-chicago-v1",
    "random_seed": 50,
    "points": points,
}
hash_input = {
    "network_id": payload["network_id"],
    "name": payload["name"],
    "kind": payload["kind"],
    "return_period_years": payload["return_period_years"],
    "step_seconds": payload["step_seconds"],
    "duration_seconds": payload["duration_seconds"],
    "source_type": payload["source_type"],
    "algorithm": payload["algorithm"],
    "random_seed": payload["random_seed"],
    "points": payload["points"],
}
canonical = json.dumps(hash_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
payload["content_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
print(json.dumps(payload, ensure_ascii=False, allow_nan=False))
PY
```

提交：

```bash
RAINFALL_JSON="$(
  curl -fsS -X POST "$BASE/api/rainfall-scenarios" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Idempotency-Key: rainfall-50y-$DEMO_CODE" \
    -H 'Content-Type: application/json' \
    --data-binary "@$RAINFALL_REQUEST"
)"

export RAINFALL_ID="$(printf '%s' "$RAINFALL_JSON" | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
printf '%s\n' "$RAINFALL_JSON"
```

请求体约束由 `RainfallScenarioRequest`（`backend/app/api/hydraulic_routes.py:439-496`）强制：
`step_seconds ∈ {60,300,900}`；`points` 数量必须等于 `duration_seconds // step_seconds + 1`；
`point_index` 必须 dense 递增；`valid_at` 必须严格递增；`kind=design` 时
`algorithm` 必须是 `synthetic-design-chicago-v1`（其它 kind 对应另外两个算法字面量）。

`content_sha256` 的服务端算法是 `_rainfall_content_hash`
（`backend/app/api/hydraulic_routes.py:2788-2803`），与上面生成器的 canonical JSON
输入一致（同样 **10** 个 key：`network_id`、`name`、`kind`、`return_period_years`、
`step_seconds`、`duration_seconds`、`source_type`、`algorithm`、`random_seed`、`points`；
外加 `sort_keys=True`、`separators=(",",":")`、`ensure_ascii=False`、
`allow_nan=False`）。**该校验只在 `DEPLOYMENT_ENVIRONMENT=production` 时强制**
（`backend/app/api/hydraulic_routes.py:2884-2892`）；development 下服务端会用自己的
计算结果覆盖你提交的值，因此 development 诊断**不能**用来证明 hash 契约。

### 5.2 提交 simulation request

body 模型 `SimulationSubmitRequest`（`backend/app/api/hydraulic_routes.py:513-542`）：
`duration_seconds` 必须能被 `frame_interval_seconds` 整除，且 `frame_interval_seconds <= 300`。

```bash
export PARAMETER_SET_ID="$(python -c 'import uuid; print(uuid.uuid4())')"

SIM_JSON="$(
  curl -fsS -X POST "$BASE/api/simulations" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Idempotency-Key: simulation-50y-$DEMO_CODE" \
    -H 'Content-Type: application/json' \
    -d "{
      \"network_id\":\"$NETWORK_ID\",
      \"network_revision_id\":\"$REVISION_ID\",
      \"rainfall_scenario_id\":\"$RAINFALL_ID\",
      \"parameter_set_id\":\"$PARAMETER_SET_ID\",
      \"initial_state_id\":null,
      \"solver_selection\":\"auto\",
      \"requested_solver_id\":null,
      \"start_at\":\"2026-09-25T00:00:00Z\",
      \"duration_seconds\":86400,
      \"frame_interval_seconds\":300,
      \"ponding_enabled\":false,
      \"strict_stability\":true
    }"
)"

export SIMULATION_ID="$(printf '%s' "$SIM_JSON" | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
printf '%s\n' "$SIM_JSON"

curl -fsS "$BASE/api/simulations/$SIMULATION_ID" \
  -H "Authorization: Bearer $TOKEN"
```

当前可验证的 request metadata：`return_period_years=50`、`duration_seconds=86400`、`frame_interval_seconds=300`、`frame_count=289`（= 86400/300 + 1）。当前真实预期仍是 `queued/progress=0`；它不是 AC-3。

只有真实 worker 发布 attempt 后才能执行：

```bash
curl -fsS "$BASE/api/simulations/$SIMULATION_ID/result" -H "Authorization: Bearer $TOKEN"
curl -fsS "$BASE/api/simulations/$SIMULATION_ID/frames" -H "Authorization: Bearer $TOKEN"
curl -fsS "$BASE/api/simulations/$SIMULATION_ID/frames/0?fields=depth_m,fill_ratio,flow_m3s,surcharged" -H "Authorization: Bearer $TOKEN"
curl -fsS "$BASE/api/simulations/$SIMULATION_ID/frames/288?fields=depth_m,fill_ratio,flow_m3s,surcharged" -H "Authorization: Bearer $TOKEN"
```

完成 AC-3 还必须有 4 physical-core solver、289 个非空持久 frame、质量/守恒、worker kill/retry/cleanup 和 UI timeline artifact。

前端真实水力路由是 `/simulation`（`frontend/src/App.tsx:77-84`）。当前 Compose 容器路径下
`http://localhost:3000` 不会启动（见 §3.2），要看这个页面请用 §4 的本地 Vite 路径；
且它依赖 API 返回真实 published frames，development adapter 只会给 `not_ready`。


## 6. Nowcast、告警与 SSE

### 6.1 当前只读/诊断命令

没有公开的“立即执行 nowcast”HTTP 路由。可查询：

```bash
curl -fsS "$BASE/api/forecast/latest?network_id=$NETWORK_ID" \
  -H "Authorization: Bearer $TOKEN"

# target_id 必须来自 target manifest。target-manifest 响应里的 target_ids 是
# {nodes|pipes|catchments|pond_cells: [id,...]}（backend/app/api/hydraulic_routes.py:617-641），
# 而 /forecast/horizon 的 target_type 取值是单数 {node,pipe_segment}
# （backend/app/api/hydraulic_routes.py:3817）。
export TARGET_ID="$(
  curl -fsS "$BASE/api/networks/$NETWORK_ID/target-manifest" \
    -H "Authorization: Bearer $TOKEN" \
  | python -c 'import json,sys; print(json.load(sys.stdin)["target_ids"]["nodes"][0])'
)"
: "${TARGET_ID:?TARGET_ID 未取到——target-manifest 的 target_ids.nodes 为空时先 seed 网络}"

curl -fsS "$BASE/api/forecast/horizon?network_id=$NETWORK_ID&target_type=node&target_id=$TARGET_ID&horizon_minutes=120" \
  -H "Authorization: Bearer $TOKEN"

curl -fsS "$BASE/api/forecast/risk-ranking?network_id=$NETWORK_ID&limit=10" \
  -H "Authorization: Bearer $TOKEN"

curl -fsS "$BASE/api/forecast/alerts?network_id=$NETWORK_ID&limit=50" \
  -H "Authorization: Bearer $TOKEN"
```

> 不要把 `<node-uuid>` 之类的占位串直接写进 URL：它在**双引号内**不会被 bash 当重定向，
> 会被原样发出，得到一个看不出成因的 404。取法见上方 `export TARGET_ID` + `:?` 守卫。

当前无 published nowcast run 时，`latest` 的真实语义是 `data_state=not_ready`、child reason `FORECAST_POLICY_UNAVAILABLE`（`backend/app/api/hydraulic_routes.py:3771-3780`）。该路由在 production 还额外受 forecast policy runtime 门禁约束，未就绪时 middleware 直接返回 503 `FORECAST_POLICY_UNAVAILABLE`（`backend/app/main.py:1285-1298`）。

### 6.2 SSE

JWT 只能放 `Authorization` header，JWT 不能放 query：

```bash
curl -N --max-time 30 \
  "$BASE/api/forecast/events?network_id=$NETWORK_ID" \
  -H "Authorization: Bearer $TOKEN"
```

断线重连才发送 `Last-Event-ID`。游标是服务端推送时写在每条事件的 `id:` 行上的整数
sequence（`backend/app/services/sse_manager.py:637`、`:645-646` 把它按 `int` 处理），
所以正确做法是**先连一次把 id 抓下来**，而不是手写占位串：

```bash
# 第一次连接：只取最后一条事件的 id 作为游标
export LAST_EVENT_ID="$(
  curl -sN --max-time 10 \
    "$BASE/api/forecast/events?network_id=$NETWORK_ID" \
    -H "Authorization: Bearer $TOKEN" \
  | sed -n 's/^id:[[:space:]]*//p' | tail -n 1
)"

# 没抓到游标就不要重连：没有游标就是"从头开始"的合法语义，硬发一个占位串没有意义
: "${LAST_EVENT_ID:?未捕获到 SSE 事件 id——当前没有 nowcast/outbox worker，不会推送事件}"

# 重连：服务端从该 sequence 之后 replay
curl -N --max-time 30 \
  "$BASE/api/forecast/events?network_id=$NETWORK_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Last-Event-ID: $LAST_EVENT_ID"
```

> 不要写 `Last-Event-ID: <last-sequence>`：它在**单引号内**不会被 bash 当重定向，会被
> 原样当成 header 值发出。服务端 `_parse_cursor` 只接受十进制数字串或非负 int，
> 其余一律抛 `SSECursorExpiredError`（`backend/app/services/sse_manager.py:897-910`），
> 路由把它转成 **409 `SSE_CURSOR_EXPIRED`**（`backend/app/api/hydraulic_routes.py:4007-4016`）。
> 也就是说，占位串不是"悄悄从头 replay"，而是一个会被 §11 排障表捕获的明确错误码——
> 但正确做法仍然是从第一次连接里把真实 sequence 抓下来。

当前没有 nowcast/outbox/notification worker，不能把“连接成功但无事件”写成 AC-4。`network_id` 是必填 query，缺失返回 422。cursor 过期应处理 409 `SSE_CURSOR_EXPIRED` 并用 REST snapshot resync。

> 恢复演练里也必须用真实的 `Authorization` header；`scripts/restore.py` 的
> `--smoke-header-env` 语义见 §9.4.1，不能用它冒充认证。

### 6.3 人工告警处置

仅当 `/forecast/alerts` 返回真实 open alert 时：

```bash
# 从 §6.1 的 /forecast/alerts 响应里取，不要把 '<alert-uuid>' / '<version>' 直接
# 写进 URL 或 JSON：引号里的尖括号会被原样发出，得到的是看不出原因的 404/422。
export ALERT_ID=""
export ALERT_VERSION=""

: "${ALERT_ID:?ALERT_ID 未设置——先从 /forecast/alerts 响应里取真实 alert 的 id}"
: "${ALERT_VERSION:?ALERT_VERSION 未设置——先取该 alert 当前的服务端 version}"

curl -fsS -X POST "$BASE/api/forecast/alerts/$ALERT_ID/transitions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: alert-ack-$ALERT_ID-$ALERT_VERSION" \
  -H 'Content-Type: application/json' \
  -d "{\"action\":\"acknowledge\",\"reason\":\"已通知运维\",\"expected_version\":$ALERT_VERSION}"
```

`resolve` 同理，但 `action` 改为 `resolve`；version 必须精确等于服务端当前值。终态不能 reopen。

`ForecastTransitionRequest`（`backend/app/api/hydraulic_routes.py:566-569`）只接受
`action ∈ {acknowledge, resolve}`、`reason` 长度 1～2000、`expected_version >= 1`。
`create/observe/refresh/escalate/expire` 是 rule 驱动的内部动作，客户端伪造不了。

## 7. A/B 对比

### 7.0 前置：两个真实 completed simulation

> **当前边界（重要）**：A/B 与率定都需要**两个真实 completed simulation**。
> 按 §5.2 提交的 simulation 在当前仓库里拿不到 completed。原因**不是**缺 worker——
> 根 Compose 的 `network-job-worker`（`docker-compose.yml:283-355`）与 `simulation-worker`
> （`:193-282`）都在（见 §3.1 的 15 个服务集合）；真实原因是这一节用的是**进程内
> `HydraulicApiStore` adapter**（`backend/app/api/hydraulic_routes.py:297`），
> production 下还会先被 `DURABLE_STORE_UNAVAILABLE` 门禁挡下（`backend/app/main.py:1273-1284`），
> 见 §1.1 与 §3.2。因此**本节命令在当前状态下拿不到有效结论**。下面给出的是变量衔接与
> 调用方式，不是"已跑通"的演示。

`$BASELINE_ID` / `$CANDIDATE_ID` **不来自 §4.2～§5.2 的任何一步**（那几步只导出
`NETWORK_ID`、`REVISION_ID`、`DEMO_CODE`、`SEED_JOB_ID`、`RAINFALL_ID`、
`PARAMETER_SET_ID`、`SIMULATION_ID`）。两者必须由你从**真实已完成**的 simulation 中取：

```bash
# 承接 §4.1 的 BASE / TOKEN
# A/B 的两侧必须共用同一 network_revision_id、target_manifest_id/sha256、start_at、
# duration_seconds、frame_interval_seconds，且都是 completed（见下方硬校验）。
export BASELINE_ID="$SIMULATION_ID"          # 只有当 §5.2 那次真的 completed 才能用
# CANDIDATE_ID 刻意**留空**：留空才能被下面的 `:?` 守卫捕获。
# 不要在这里写 '<UUID>' 或任何占位字符串——非空占位串不会触发 `:?`，
# 会一路拼进 URL 变成 404，而不是 fail-fast。
export CANDIDATE_ID=""

# 失败即停：变量没设/为空时给出明确报错，而不是静默展开成空串后拿到误导性的 404
: "${BASELINE_ID:?BASELINE_ID 未设置——先确认该 simulation 状态为 completed}"
: "${CANDIDATE_ID:?CANDIDATE_ID 未设置（当前为空）——先从真实 completed simulation 取其 UUID 再 export}"

# 额外一层：占位符没替换就早停。`:?` 只看空值，抓不住被填进去的占位文本。
case "$CANDIDATE_ID" in
  *"<"*|*">"*|*"REPLACE_ME"*|*"你的"*|*"第二个"*)
    echo "CANDIDATE_ID 仍是占位符：$CANDIDATE_ID" >&2
    exit 1
    ;;
esac

curl -fsS "$BASE/api/simulations/$BASELINE_ID" -H "Authorization: Bearer $TOKEN"
curl -fsS "$BASE/api/simulations/$CANDIDATE_ID" -H "Authorization: Bearer $TOKEN"
```

上面两条 `GET` 用来确认两侧确实 `status=completed`；不是 completed 就直接退出本节，
不要继续请求 compare。

### 7.1 对比调用

A/B 路由是：

```text
GET /api/simulations/{baseline_id}/compare/{candidate_id}
```

实现见 `backend/app/api/hydraulic_routes.py:3520-3600`。它要求两次 simulation 都
completed，且以下字段逐一相等：`network_revision_id`、`target_manifest_id`、
`target_manifest_sha256`、`start_at`、`duration_seconds`、`frame_interval_seconds`，
否则 409 `COMPARISON_INCOMPATIBLE`。

指标兼容性也强校验（`backend/app/api/hydraulic_routes.py:3558-3578`）：对每个共有
metric，`metric_units[key]` 必须是非空字符串、candidate 单位必须相同，
`better_directions[key]` 必须是 `lower`/`higher` 且两侧一致；任一不满足即
409 `COMPARISON_INCOMPATIBLE`。query：

```bash
curl -fsS \
  "$BASE/api/simulations/$BASELINE_ID/compare/$CANDIDATE_ID?metrics=rmse,nse" \
  -H "Authorization: Bearer $TOKEN"
```

返回值由后端预计算并给 `absolute_delta`、`relative_delta`（baseline 为 0 时为 `null`）、
`unit`、`better_direction`；浏览器不重采样。

> 上轮文档记录的“单位不一致仍返回 200”缺口**已修复**，本轮 focused 测试全绿。
> 但 AC-6 仍 `NOT RUN`：需要两个真实 completed simulation 的同轮 G3/e2e/G6 artifact，
> 不得用注入内存结果的单元测试代替。

## 8. 率定与实测对比

### 8.1 提交

> 本节复用 §7.0 导出的 `$BASELINE_ID`；若你从 §8 单独开始，先执行 §7.0 的前置块。
> 率定额外要求外部可信观测（见 §8.2），当前仓库没有 `external_verified` 观测源。

body 模型 `CalibrationSubmitRequest`（`backend/app/api/hydraulic_routes.py:545-564`）：

```bash
: "${BASELINE_ID:?BASELINE_ID 未设置——先执行 §7.0 的前置块}"

CALIBRATION_JSON="$(
  curl -fsS -X POST "$BASE/api/calibrations" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Idempotency-Key: calibration-$BASELINE_ID" \
    -H 'Content-Type: application/json' \
    -d "{
      \"network_id\":\"$NETWORK_ID\",
      \"network_revision_id\":\"$REVISION_ID\",
      \"baseline_simulation_id\":\"$BASELINE_ID\",
      \"candidate_simulation_id\":null,
      \"observation_start\":\"2026-09-25T00:00:00Z\",
      \"observation_end\":\"2026-09-25T06:00:00Z\",
      \"search_method\":\"grid\",
      \"seed\":50,
      \"iterations\":100,
      \"parameter_whitelist\":[],
      \"split_manifest\":{}
    }"
)"

export CALIBRATION_ID="$(printf '%s' "$CALIBRATION_JSON" | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
printf '%s\n' "$CALIBRATION_JSON"

curl -fsS "$BASE/api/calibrations/$CALIBRATION_ID" \
  -H "Authorization: Bearer $TOKEN"
```

（`POST /api/calibrations` 与旧别名 `POST /api/calibrate` 指向同一实现。）

当前开发 adapter 只会排队；production 在 durable adapter 未注入时返回 503 `DURABLE_STORE_UNAVAILABLE`。
`GET /api/simulations/{id}/observed-comparison` 的实现目前**固定**返回
`status=not_ready`、`pairs=[]`、`coverage.observation_count=0`、`metrics.nse=null`
与 `nse_code=NSE_UNDEFINED`（`backend/app/api/hydraulic_routes.py:3466-3481`），
所以不能产生真实率定结论。

### 8.2 AC-5 边界

只有 `external_verified`、auth verified、active binding、quality good 且事件切分不重叠的观测可进入正式率定。AC-5 还必须同时满足 `PRD_SUPPLEMENT.md` §9.2 的 NSE/RMSE/peak error 四项门槛；synthetic/mock/当前空 pairs 一律不能替代。

## 9. 备份、验证与恢复

### 9.1 secret 文件

不要把密码/token 放进命令行参数或仓库 `.env`。示例假设：

```text
/run/secrets/drainage-postgres-password
/run/secrets/drainage-redis-password
/run/secrets/drainage-influx-token
```

### 9.2 备份

先暂停 nowcast scheduler、停止接收新 simulation，并停止 API/所有 writer。`--writers-paused` 只是证据声明，不会自动停服务。

`--solver-sha256` 的默认值取自环境变量 `SOLVER_ENGINE_SHA256`（`scripts/backup.py:765`），
并且**必须是 64 位小写 hex**（`scripts/backup.py:786-789`）。不知道 engine hash 时
请把它导出成空串，不要写占位符——占位符会让 backup 直接失败退出。

```bash
export POSTGRES_USER=drainage_admin
export POSTGRES_DB=smart_drainage
export HYDRAULIC_SPATIAL_BACKEND=portable
export SOLVER_ENGINE_SHA256=''        # 已知时填 64 位小写 hex，否则留空
export BACKUP_ID="$(date -u +%Y%m%dT%H%M%SZ)-pre-release"

./scripts/backup.sh \
  --backup-id "$BACKUP_ID" \
  --compose-file ./docker-compose.yml \
  --components postgres,redis,influx \
  --postgres-service postgres \
  --postgres-user "$POSTGRES_USER" \
  --postgres-db "$POSTGRES_DB" \
  --schema-profile "$HYDRAULIC_SPATIAL_BACKEND" \
  --pg-password-file /run/secrets/drainage-postgres-password \
  --redis-service redis \
  --redis-password-file /run/secrets/drainage-redis-password \
  --influx-service influxdb \
  --influx-org smart-city \
  --influx-token-file /run/secrets/drainage-influx-token \
  --writers-paused
```

要求输出 `BACKUP_STATUS=COMPLETE`、archive sidecar、`MANIFEST_SHA256`；`METADATA_COMPLETE=false` 不能作为完整 G6 证据。
默认输出目录是 `./backups`；改目录用 `--output-dir`（`scripts/backup.py` argparse）。

PowerShell wrapper 使用同一个 Python 实现：

```powershell
.\scripts\backup.ps1 -Python python `
  --backup-id '<backup-id>' `
  --compose-file '.\docker-compose.yml' `
  --components postgres,redis,influx `
  --postgres-user drainage_admin `
  --postgres-db smart_drainage `
  --schema-profile portable `
  --pg-password-file 'C:\secure\postgres.password' `
  --redis-service redis `
  --redis-password-file 'C:\secure\redis.password' `
  --influx-service influxdb `
  --influx-org smart-city `
  --influx-token-file 'C:\secure\influx.token' `
  --writers-paused
```

### 9.3 离线验证

```bash
./scripts/restore.sh \
  "./backups/$BACKUP_ID.tar.gz" \
  --verify-only \
  --report "./artifacts/operations/$BACKUP_ID-verify.json"
```

必须验证 archive sidecar、manifest hash、成员路径、每个 component hash 和 evidence hash。不要使用 `--allow-missing-sidecar` 绕过损坏证据。

### 9.4 应用恢复

恢复是破坏性操作。先对目标做独立保护快照，再执行：

```bash
# 短时 token：恢复完成后立即失效，不要复用运维会话 token。
# 刻意**留空**再由你填：非空占位串（例如 'Bearer <占位>'）不会被下面的 :? 抓到，
# 会被 --smoke-header 原样拼进去，得到一个看起来像认证失败的 401/403。
export SMOKE_AUTHORIZATION=""
: "${SMOKE_AUTHORIZATION:?SMOKE_AUTHORIZATION 未设置——先从 §4.1 的登录流程重新取一个短时 token}"

# SSE smoke 需要 network_id。§9 全节不导出它，所以这里必须显式取回并守卫；
# 否则新开终端按 §9.1 起手时 $NETWORK_ID 展开为空 → "network_id=" → 422，
# 看起来像接口坏了。来源是 §4.2 导出的 NETWORK_ID。
export NETWORK_ID=""
: "${NETWORK_ID:?NETWORK_ID 未设置——先按 §4.2 建 network 并从响应里取回 id}"

./scripts/restore.sh \
  "./backups/$BACKUP_ID.tar.gz" \
  --apply \
  --confirm "$BACKUP_ID" \
  --compose-file ./docker-compose.yml \
  --components postgres,redis,influx \
  --postgres-service postgres \
  --postgres-user "$POSTGRES_USER" \
  --postgres-db "$POSTGRES_DB" \
  --pg-password-file /run/secrets/drainage-postgres-password \
  --redis-service redis \
  --redis-password-file /run/secrets/drainage-redis-password \
  --influx-service influxdb \
  --influx-org smart-city \
  --influx-token-file /run/secrets/drainage-influx-token \
  --stop-service backend \
  --stop-service simulation-worker \
  --stop-service nginx \
  --start-service backend \
  --api-smoke-url http://127.0.0.1:8000/health \
  --api-smoke-url http://127.0.0.1:8000/readyz \
  --sse-smoke-url "http://127.0.0.1:8000/api/forecast/events?network_id=$NETWORK_ID" \
  --smoke-header "Authorization=$SMOKE_AUTHORIZATION" \
  --report "./artifacts/operations/$BACKUP_ID-restore.json"
```

`API_SSE_SMOKE=PASS` 需要 API 与 SSE 两类 smoke；`PARTIAL/NOT_RUN` 不是 G6 PASS。

#### 9.4.1 `--smoke-header` / `--smoke-header-env` 的真实语义（必读）

两个参数**不能互换**，照抄错的形式会让 forecast SSE smoke 得到 401/403，
从而永远拿不到 `API_SSE_SMOKE=PASS`：

| 参数 | 实现 | 实际发送的 HTTP Header 名 |
|---|---|---|
| `--smoke-header NAME=VALUE` | `scripts/restore.py:188-197`，把 `NAME` 直接当 header 名 | `NAME`（`=` 必须是**第一个**分隔符） |
| `--smoke-header-env ENV_NAME` | `scripts/restore.py:722-726`，`headers[env_name] = value` | `ENV_NAME` 本身，**不是**它读到的值 |

因此：

- **只发 `Authorization` 时必须用 `--smoke-header "Authorization=$SMOKE_AUTHORIZATION"`。**
- `--smoke-header "Authorization: Bearer ..."`（冒号写法）会被拒绝，报
  `header must use NAME=VALUE syntax`。
- `--smoke-header-env SMOKE_AUTHORIZATION` 发出的是 `SMOKE_AUTHORIZATION: Bearer ...`，
  对 `GET /api/forecast/events`（`backend/app/api/hydraulic_routes.py:3985`，`_viewer` 依赖）
  无效。
- `--smoke-header-env` 目前只能用于“header 名恰好等于环境变量名”的非敏感 header。

本轮用真实 argparse → `OperationsRestore._smoke()` 路径（仅替换网络调用为捕获桩）
实测三种写法，结果见
[`implementation/delivery-docs.md`](./implementation/delivery-docs.md) §2.6 末尾。

**已知脚本缺陷与暴露面**（需由 `operations` 脚本 owner 修复，不在本模块 ownedPaths 内）：

1. CLI 无法表达“header 名 + 环境变量名”的映射，认证 smoke 只能用 `--smoke-header`，
   于是 token 会出现在进程 argv（同主机用户可从 `/proc/<pid>/cmdline` 读到）。
   缓解：只用于短时 token、恢复在受控主机执行、事后立即吊销；
   若不接受该暴露面，脚本 owner 需新增 `NAME=ENV_NAME` 形式并补
   “最终请求含 `Authorization` 且不含 `SMOKE_AUTHORIZATION`”的回归测试
   （`scripts/backup_test.py` 当前未覆盖 `--smoke-header-env` 语义）。
2. 在修复前，若不能接受 argv 暴露，就**不要**跑带认证的 SSE smoke；
   直接省略 `--sse-smoke-url`，此时输出是 `PARTIAL`（`scripts/restore.py:736-738`），
   记为 NOT RUN，不能当作 G6 证据。

## 10. 回滚

### 10.1 顺序

1. 停止 nowcast scheduler/outbox/notification writer 和新 simulation 入队；
2. 等待或幂等取消 active attempt，记录 terminal state；
3. 创建并离线验证发布前备份；
4. 恢复与目标旧 image 兼容的 additive schema/数据；
5. 将经过批准的旧 backend/frontend image 恢复到部署清单；
6. 先启动 API，再启动 workers/scheduler；
7. 验证 `/health`、`/readyz`、legacy API/SSE、forecast SSE；
8. 最后恢复 nowcast/外部通知。

禁止对有 hydraulic 数据的生产库自动 `alembic downgrade`。优先“旧应用 + additive schema”。

### 10.2 当前 Compose 的限制

根 Compose 把 backend 固定为 `smart-city-drainage-backend:local` 并带 build 配置，没有 `BACKEND_IMAGE`/digest override；也没有 nowcast/notification worker。因此本轮不能给出“旧 image 一键回滚已验证”的命令。若发布系统已把旧 image 重新标记为该 tag，可执行：

```bash
docker compose --profile production -f docker-compose.yml stop backend nginx frontend simulation-worker
# 由发布系统把经过批准且 digest 已记录的旧镜像标记为 smart-city-drainage-backend:local。
docker compose --profile production -f docker-compose.yml up -d --no-build backend
curl -i http://127.0.0.1:8000/health
```

在完整旧 worker 栈恢复前，`/readyz` 503 是正确结果。不要为了让 Nginx 启动而跳过 worker，也不要把 `/health` 当 readiness。

## 11. 故障排查

| 症状/code | 核查 | 处置 |
|---|---|---|
| `BASELINE_ID: parameter null or not set` | 手册的 `: "${BASELINE_ID:?...}"` 守卫 | 这是**故意的 fail-fast**：变量没设时立刻停下，而不是静默展开成空串再给你一个误导性的 404。回到 §7.0 接入两个真实 completed simulation |
| `CANDIDATE_ID 未设置（当前为空）` 或 `CANDIDATE_ID 仍是占位符` | §7.0 的 `: "${CANDIDATE_ID:?...}"` 与 `case` 占位符检查 | 这两条是**故意的 fail-fast**。`:?` 只抓“未设置/为空”，所以 §7.0 让 `CANDIDATE_ID` 留空而不是写 `'<UUID>'`；`case` 那一层专门抓“被填进去了但仍是占位文本”。看到报错就说明你还没接上第二个真实 completed simulation 的 UUID |
| A/B 返回 404 `SIMULATION_NOT_FOUND` | `$BASELINE_ID`/`$CANDIDATE_ID` 展开后到底是什么 | 三种成因：① 变量为空串（守卫被删或没导出）；② 变量是**没替换的占位文本**（`:?` 不触发，靠 §7.0 的 `case` 兜住）；③ UUID 真实存在但不属于你的租户/网络。`echo "$BASELINE_ID" "$CANDIDATE_ID"` 先看清再判断，不要靠 404 猜 |
| A/B 返回 409 `COMPARISON_INCOMPATIBLE` | 两侧的 revision / target_manifest / start_at / duration / frame_interval，或 `metric_units` / `better_directions` | 见 §7.1 的硬校验列表；改用同 frame grid、同单位、同方向的两侧结果，不要手工改写 metrics |
| 复制手册命令报 `RUN_ID: No such file or directory` | 是否在命令里写了裸 `<RUN_ID>` | 裸 `<PLACEHOLDER>` 被 bash 当成重定向。统一写成 `"$RUN_ID"` 并先 `export`（§2.2、§3.3、§12） |
| URL 里带着尖括号（`…/validation-runs/<validation_id>`）后拿到 404 | 是不是把占位串写进了 URL | **引号里的** `<...>` 不会被 bash 当重定向，会被**原样发出**，所以这个错误更安静。手册所有 ID 一律用 `export` + `: "${VAR:?}"` 从响应里取（§4.3、§6.1、§6.2、§6.3、§7.0、§9.4）。自动守卫 `.runtime/doc_checks.py` 的 `BASH_PLACEHOLDERS` 覆盖 `<xxx>` 与 `<XXX_YYY>` 两种形态（大小写不敏感），**但字符集里没有 `-`，所以 `<alert-uuid>` 这类带连字符的占位符它抓不到**——本轮已手工消除现存 3 处，守卫本身的这个缺口见 [`implementation/delivery-docs.md`](./implementation/delivery-docs.md) §9 |
| Compose 提示变量缺失 | `docker compose ... config` | 两类必填变量：① 常驻服务（`POSTGRES_PASSWORD`、`DATABASE_URL*`、Redis/Influx、JWT RS256 key/issuer/audience）；② **`GRAFANA_ADMIN_PASSWORD`**——它属于 `grafana` 服务（`docker-compose.yml:778`），但因 Compose 对整个文件做插值，**不带 `--profile monitoring` 也会因它 exit 1**。`.env.example` 不提供它，`cp .env.example .env` 不解决。先 `export` 再看报错；不要提交 secret |
| `config --quiet` exit 0 但干净机器上 exit 1 | 工作目录有没有未跟踪的 `.env` | `docker compose` 自动读 `./.env`；本仓库根的 `.env` 被 `.gitignore:31` 忽略且恰好定义了 `GRAFANA_ADMIN_PASSWORD`，会**掩盖**漏掉的 export。用 `docker compose --env-file "$CLEAN_ENV" ... config --quiet` 复现真实条件（`$CLEAN_ENV` 必须是 §3.1 那种"`.env.example` + 补齐 6 项"的合成 env；**不能用空文件**，空文件 = 零变量，直接 exit 1） |
| `migrate` 非零 | `docker compose logs migrate postgres` | 根 Compose 把 `HYDRAULIC_SCHEMA_REVISION` 钉在 `0008_hydraulic_retention_seed`（`docker-compose.yml:56`、`:135`、`:225`、`:311`、`:384`、`:462`、`:596`）。2026-09-28 实测 `.env.example` 已同步为 `0008_hydraulic_retention_seed`（上一版的 `0003_hydraulic_schema_gap` 不一致**已修**）；实测库 `alembic_version` = `0008_hydraulic_retention_seed`，`migrate` 容器 `Exited (0)`。仍非零就核对 head、profile 和唯一 schema writer；不要运行 `create_all` |
| `/readyz` 503 `schema_head_or_profile_mismatch` | DB response、Compose env、Alembic current | 修复 schema/profile；不要原地切换 postgis/portable |
| `/readyz` 503 `runtime_contract_check_failed`，`.checks.forecast_policy=false` | §3.2.1 的三条定位命令 | **2026-09-28 实测已不复现**：`forecast_policy=true`。该缺陷在当前源码中已修（`validate_file` 改为接收 `artifact_format` 入参，`backend/app/main.py:665-673`；rules 一路传 `None`，`:781`）。若你又看到，先怀疑**镜像过期**：`docker compose build backend && docker compose up -d backend` 后复现。sidecar 匹配要按 canonical JSON 校验，别用原始字节 `sha256sum` 误判 |
| `/readyz` 503 且 `.checks.worker_heartbeats` 全 false | 你是不是在**裸进程**里跑的 readiness | 裸进程没连 Redis，`_check_worker_heartbeats`（`backend/app/main.py:808-825`）读不到值就全判 false。**先 `redis_client.connect()` 再测**，否则会误判成 worker 故障 |
| 所有 hydraulic API 503 `DURABLE_STORE_UNAVAILABLE` | production middleware 与 `durable_backend` | 注入 durable repository；不要切到 mock store 冒充生产 |
| forecast 503 `FORECAST_POLICY_UNAVAILABLE` | config + `.sha256` sidecar、artifact ID/hash/validity | 修复受控 policy；不要生成 `publishable=false` 时隙 |
| seed/import/validate 一直 queued | `GET /api/network-jobs/{job_id}`、worker service | 根 Compose **已有** `network-job-worker`（`docker-compose.yml:283-355`），**不是缺服务**。2026-09-28 实测 `hydraulic.network_jobs` 0 行。仍 queued 时看该服务日志与租约，并确认是不是走了 §4 的进程内 adapter（那会停在 `queued`）；不要声称已提交网络 |
| simulation 一直 queued | status、worker logs、lease | 检查真实 executable worker/DB repository；不要手工改 status |
| `simulation-worker` 反复重启 | `docker compose logs simulation-worker`；`python -m app.workers.simulation_worker` | 两种分支都实测过（§1.1）：带 Compose 默认值（`docker-compose.yml:250`）时**不会**报 `solver_command_unavailable`；不设该变量才 **exit 3 + `reason=solver_command_unavailable`**。先确认你的 `SIMULATION_SOLVER_COMMAND` 是不是被覆盖成了空值 |
| `nginx`、`frontend` 一直 `Created` | `docker compose ps --all` 看 backend 是不是 `unhealthy` | 它们 `depends_on` 的是 `service_healthy`。2026-09-28 实测 backend `/readyz` 已 200，所以这一条若仍复现，多半是**旧镜像**或该栈没起 `production` profile。2026-09-28 那台栈里 backend healthy，但**没有** `nginx`/`frontend` 容器（未起该 profile）。先 `docker compose ps --all` 看这两者是否存在，再按 §3.2 逐项定位 |
| 401 `AUTH_REQUIRED/UNAUTHENTICATED` | Authorization header、iss/aud/exp/role | 重新登录；不要把 token 放 URL 或日志 |
| 403 `FORBIDDEN` | role 与 mutation 表 | viewer 只读；seed/archive 仅 admin |
| 409 `NETWORK_REVISION_CONFLICT` | `current_revision_id`/expected revision | 重新 GET，不盲目重放旧 expected revision |
| 409 `IDEMPOTENCY_KEY_REUSED` | 同 key 是否换了 request | 使用新 key，或重放完全相同 request |
| rainfall 422 | points 数量、valid_at 单调、kind/algorithm/hash | 24 h/5 min 必须 289 点；`design` 必须配 `synthetic-design-chicago-v1`；按第 5 节脚本生成 |
| 恢复 smoke 401/403 | `--smoke-header` 是否为 `Authorization=...` 形式 | `--smoke-header-env` 发的是环境变量名当 header 名；见 §9.4.1 |
| result 409 / frame 410 | simulation status、frame retention | 409 表示未发布；410 只允许保留 summary，不重用旧 frame |
| SSE 409 `SSE_CURSOR_EXPIRED` | `Last-Event-ID` 的**实际值** | 两种成因：① cursor 超出保留窗口（`X-SSE-Earliest-Sequence` / `X-SSE-Latest-Sequence` 会告诉你范围）；② header 里是**没替换的占位串**（`_parse_cursor` 只接受十进制数字串或非负 int，其余抛同一个错，`backend/app/services/sse_manager.py:897-910`）。先 `echo "$LAST_EVENT_ID"` 看清再判断，然后用 REST latest/horizon resync；不要伪造历史 |
| 429 `RATE_LIMITED` | `Retry-After` | 按 header 退避，不换 token/IP 绕过 |
| `EBADENGINE` | `node -v && npm -v` | `frontend/.npmrc` 的 `engine-strict=true` 会把 engines 不匹配从 warn 升级为 install 阶段失败（该机制在 `/tmp` 合成包上实跑过，见 README FAQ 0.3）。切到 Node 18.20.8/npm 10.8.2 后再 `npm ci`；仓库本体的 `npm ci` 结果本文是 `NOT RUN` |
| G5 `BLOCKED` / `target_environment_mismatch` | `node_version`、`available_cores`、`ram_bytes` | 换到 Node 18.20.8 / 4 核 / 8 GiB 目标机重跑；本机 BLOCKED 是正确行为，不是 PASS |
| `DEPENDENCY_HANDOFF=PASS_WITH_DEFERRED` | `DEFERRED` 列表 | hash 已一致；G0/G5 仍因 deferred 的目标环境证据未解锁，不要引用旧 artifact 冒充 |
| G6 config `FAIL: missing target services` | 根 Compose services 集合 | 2026-09-28 实测**已不复现**：`config --services` 返回 15 个服务，含全部 4 个 worker，`--profile portable` exit 0。若你又看到这个，用下面这条**口径命令**核对（不要用 `grep -c` 数顶层块——`^  [a-z-]*:` 会把 `networks:` 的 `scn-network` 和 5 个卷一起数进去，2026-09-28 实测返回 **21**，而真实服务数是 **15**）：<br>`docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring -f docker-compose.yml config --services \| sort`（同 §3.1） |
| G6 config `FAIL: postgis profile does not select an explicit PostGIS image` | 根 Compose 的 `postgres` 镜像 | 根 Compose 固定 `timescale/timescaledb@sha256:3f770468…`（`docker-compose.yml:9`），**没有** PostGIS 变体。要在根 Compose 上跑 postgis 门禁需先加镜像变体；`docker/postgres/compose.yml` 的双 profile 只含数据库，不能当 G6 |
| archive/manifest hash mismatch | sidecar、manifest、component | 停止恢复，隔离归档并取独立副本 |
| Redis `NOAUTH`/DBSIZE mismatch | secret/backup 后写入 | 保持 writers paused；Redis 不是任务/告警事实源 |
| `/api/forecast/events` 422 | 是否带 `network_id` | 补 query；JWT 仍只放 header |

## 12. 验收命令与证据

从仓库根执行，postgis/portable 不能互相替代：

```bash
export RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short=8 HEAD)"

python -B scripts/verify_hydraulic_stack.py --gate g0 --run-id "$RUN_ID" --evidence-dir "artifacts/hydraulic/$RUN_ID/g0"
python -B scripts/verify_hydraulic_stack.py --gate g1 --profile postgis --run-id "$RUN_ID" --evidence-dir "artifacts/hydraulic/$RUN_ID/g1/postgis"
python -B scripts/verify_hydraulic_stack.py --gate g1 --profile portable --run-id "$RUN_ID" --evidence-dir "artifacts/hydraulic/$RUN_ID/g1/portable"
python -B scripts/verify_hydraulic_stack.py --gate g2 --profile postgis --run-id "$RUN_ID" --evidence-dir "artifacts/hydraulic/$RUN_ID/g2"
python -B scripts/verify_hydraulic_stack.py --gate g3 --profile postgis --run-id "$RUN_ID" --evidence-dir "artifacts/hydraulic/$RUN_ID/g3"
python -B scripts/verify_hydraulic_stack.py --gate g4 --profile postgis --run-id "$RUN_ID" --evidence-dir "artifacts/hydraulic/$RUN_ID/g4"
python -B scripts/verify_hydraulic_stack.py --gate g5 --profile postgis --run-id "$RUN_ID" --evidence-dir "artifacts/hydraulic/$RUN_ID/g5"
python -B scripts/verify_hydraulic_stack.py --gate g6 --profile postgis --run-id "$RUN_ID" --evidence-dir "artifacts/hydraulic/$RUN_ID/g6/postgis"
python -B scripts/verify_hydraulic_stack.py --gate g6 --profile portable --run-id "$RUN_ID" --evidence-dir "artifacts/hydraulic/$RUN_ID/g6/portable"
python -B scripts/verify_hydraulic_stack.py --gate acceptance --ac ac1,ac2,ac3,ac4,ac5,ac6,ac7,ac8 --run-id "$RUN_ID" --evidence-dir "artifacts/hydraulic/$RUN_ID/acceptance"
```

只有关键命令本轮 exit 0、artifact 存在且 hash 校验通过，才能写 PASS。本文没有执行上述 live G0～G6/acceptance；当前结论保持 NOT RUN/BLOCKED。本轮只额外执行了 `--mode config` 的 G6 静态预检：**portable exit 0 / postgis exit 1**（见 §3.3），它按脚本自己的 `scope_note` 不构成 live G6/AC-8。

## 13. 本轮核对范围（2026-09-28）

本轮**实际执行**的命令与原始输出见
[`implementation/delivery-docs.md`](./implementation/delivery-docs.md)。以下明确**没有**执行，
本文不为其写任何通过结论：

- `docker compose build/up/ps` 的 live build 与启动（本轮只做了 `config` **渲染**，
  且 §3.1 的 `exit 0` 是在**排除本机未跟踪 `.env`** 的前提下取得的）；
- §3.2 的 `down -v` / `up --build -d` / `ps --all`（**会删卷**）与 §3.2.1 的 4 条
  `docker compose exec` 定位命令；
- `GET /api/auth/login` 之后的一切写操作；§4～§9 的 HTTP 命令**一条都没有运行**；
- postgis 与 portable 两套 fresh-volume G6、backup → destroy → restore；
- G0～G5、acceptance 汇总；
- Alembic live `upgrade` / `current` / `downgrade`；
- 真实 4-core solver、289 个持久 frame、worker kill/retry/cleanup；
- nowcast 100 fake slots / 30 真实周期、durable alert/outbox/SSE replay；
- 真实 `external_verified` 观测驱动的率定；
- old image 回滚；
- 全量 pytest、Vitest、`npm ci`、`npm test`、`npm run lint`/`format:check`/`build`
  （前端只在一个 `/tmp` **合成包**上验证过 `engine-strict` 机制，不等于仓库本体）；
- Playwright / 浏览器 acceptance。

### 13.1 本轮对**已在运行的栈**做过的只读观测

这些是**真实观测**，但必须带上限定：那个栈**不是本模块 `docker compose up` 起来的**，
也不对应本模块的专用 project，所以它**不构成** live G6 或 AC-8 证据。

- `docker ps` / `docker ps -a`：10 个容器 `Up (healthy)`，`migrate-1` `Exited (0)`，
  **没有** `nginx`/`frontend` 容器（该栈未起 `production` profile）；
- `curl http://127.0.0.1:8000/health` → `{"status":"healthy"}`，HTTP 200；
- `curl http://127.0.0.1:8000/readyz` → HTTP 200，`ready=true`、`reason=null`，
  六项检查全过（§1.1 readiness 行）；
- `POST /api/auth/login`（README §3 的 development 凭据）→ HTTP 401
  `{"code":"AUTH_REQUIRED"}`。**这是预期的**：那组凭据依赖
  `AUTH_FALLBACK_ADMIN_ENABLED=true`，是 development adapter 专用开关（见 README §3 的说明）；
- `psql` 只读计数：§1.1 "种子数据"行列出的各表行数。

