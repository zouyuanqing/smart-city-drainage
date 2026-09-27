# 水力数字孪生契约与证据索引

> 最后按 live files 核对：2026-09-28。本文是导航和冲突检查表，不新增或覆盖冻结契约。
>
> 核心规则：需求/契约决定“应该是什么”，live code/Compose/scripts 决定“现在实际有什么”；两者不一致时记录阻断，不能把实现现状反写成规范，也不能把设计批准写成运行通过。
>
> 本索引的“live 实现位置”行号有**两个来源**，不要混为一谈：
>
> 1. `hydraulic_routes.py` / `routes.py` 的引用来自一次真实的 `/api` 路由导出
>    （见 [`implementation/delivery-docs.md`](./implementation/delivery-docs.md) §2.1；
>    §2.2 是文档 API 路径与 live 路由的逐条对齐，§2.1 末尾是 43 行 method+path 的复核结果）。
> 2. **根级路由** `/health`、`/readyz`、`/metrics` **不在 `/api` 导出里**（它们没有
>    `/api` 前缀），其行号由**直查源码**得到，并由 `doc_checks.py` 的路由引用检查逐条
>    复核（`README.md:209` 的反向声明同源）。
>
> 行号会随实现漂移，复核时请重新导出／重跑检查，而不是沿用旧值。

## 1. 规范优先级

| 优先级 | 文档/工件 | 作用 | 证据边界 |
|---:|---|---|---|
| 1 | [`SOURCE_PRD.md`](./SOURCE_PRD.md) | 原始需求 FR-1～FR-7、AC-1～AC-8 | 不可变需求正文；来源可核验不等于功能已实现 |
| 2 | [`PRD_SUPPLEMENT.md`](./PRD_SUPPLEMENT.md) | 冻结产品语义、异常、量化工况、性能与 AC 口径 | 设计与验收标准；测试未运行时保持 NOT RUN |
| 3 | [`IMPLEMENTATION_CONTRACT.md`](./IMPLEMENTATION_CONTRACT.md) | 数据模型、API、worker、部署、owner、G0～G6 的可执行契约 | 规范优先级见其 §0；不是运行证据 |
| 4 | `contracts/*.md` | 历史 supporting contracts | **SUPERSEDED / NON-NORMATIVE**；不得覆盖前三份 |
| 5 | `recon/**`、`implementation/**`、`reviews/**` | 实施/审查历史 | 报告所述必须标明“本轮未重跑”或“历史结果” |
| 6 | live code、Compose、scripts、artifacts | 当前实现与机器证据 | 不能静默改变前三份规范；差异必须暴露 |

冲突处理顺序与 `IMPLEMENTATION_CONTRACT.md` 的 §0.2 一致。若实现确需改变非协商语义，应先由相应 owner 修改三份主文档并重新独立审查。

## 2. 当前批准与未批准

### 2.1 已有的设计证据

| 工件 | 本轮读取到的事实 | 能证明什么 | 不能证明什么 |
|---|---|---|---|
| `artifacts/contracts/v3-independent-review.json` | `verdict=R0_DESIGN_PASS`，scope 只覆盖 stage D 设计冻结 | 三文档 bundle 的设计冻结 | migration live、G0～G6、AC、Compose、worker、solver |
| `artifacts/contracts/approved-migration-0003.json` | head=`0003_hydraulic_schema_gap`、`APPROVED_POST_MIGRATION`（`approved-migration-0002.json` 保留为 0002 阶段的历史审批） | migration 审批/文件边界 | PostgreSQL/PostGIS/portable 空库 upgrade |
| `artifacts/contracts/r0-gate-execution.json` | 历史记录 migration PASS、dependency `PASS_WITH_DEFERRED` | 当时那组输入的静态 checker 结果 | 当前工作树仍然通过；跨时点/跨 commit 复用 |

2026-09-26 本轮重新执行：

```text
python -B scripts/check_migration_gate.py --review-artifact artifacts/contracts/v3-independent-review.json --base-ref ca99e0485fb12fbecbd565e79d81f745f8d4a748 --versions-dir backend/alembic/versions
# exit 0; MIGRATION_PHASE=POST_MIGRATION_APPROVED; MIGRATION_GATE=PASS

python -B scripts/check_dependency_handoff.py --handoff artifacts/contracts/dependency-config-handoff.json --expected backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json --python-version-file .python-version --node-version-file frontend/.node-version --npmrc frontend/.npmrc --package frontend/package.json --lock frontend/package-lock.json --env-example .env.example
# exit 0; DEPENDENCY_HANDOFF=PASS_WITH_DEFERRED; DEFERRED=frontend-target-runtime-evidence; G0_G5_BLOCKED_BY_DEFERRED=true
```

**变化记录**：上一轮（2026-09-25）dependency handoff 曾因 `frontend/package.json` 的
SHA-256 漂移而 `exit 1 / FAIL`；本轮已 `PASS_WITH_DEFERRED`。但 `DEFERRED` 项
`frontend-target-runtime-evidence` 仍在，因此 **G0/G5 依然未解锁**，不能引用
`r0-gate-execution.json` 的历史 PASS 作为当前状态。

### 2.2 当前 AC 状态

`backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json` 是当前明确的验收状态源
（本轮读取）：

| AC | 状态 | 核心缺口 | required_gates |
|---|---|---|---|
| AC-1 import/round-trip | BLOCKED | 门禁实跑 BLOCKED/exit 2（G2 冻结 fixture 缺失）；非 NOT RUN。API 侧 staging/指纹/计数已接线，durable 提交路径与双 profile G1/G6 仍缺 | g1, g2, g6 |
| AC-2 恶意输入/安全 | BLOCKED | Windows/UNC/ADS 原始 filename、重复 stable key、durable idempotency 等 | g2, g6 |
| AC-3 50 年/24 h/worker/frame | NOT RUN | 真实 4 核 solver、worker 可执行入口、289 持久帧、timeline 缺失 | g3, g5, e2e, g6 |
| AC-4 nowcast/告警/SSE | NOT RUN | durable nowcast/outbox/notification/180 s/restart replay 缺失 | g4, g5, e2e, g6 |
| AC-5 率定 | BLOCKED | 无 external_verified source event + active binding + good dataset | g3, g5, e2e, g6 |
| AC-6 A/B | NOT RUN | 单元级已绿（单位/方向不一致正确 409），但缺两个真实 completed simulation 的同轮 artifact | g3, e2e, g6 |
| AC-7 零回归 | NOT RUN | 无同轮 G0/e2e/G6 artifacts | g0, e2e, g6 |
| AC-8 fresh Compose/rollback | NOT RUN | 无 postgis + portable 两套 live G6/backup/restore artifact | g6 |

## 3. Live API 路由索引

路由汇总 router 的 prefix 是 `/api`（`backend/app/api/routes.py:80`），hydraulic router 在该
文件挂入。下表路径均已含 `/api` 前缀。行号来自本轮 `app.routes` 导出。

### 3.1 Network / job / rainfall

| Method | Path | 角色/Header | live 实现位置 |
|---|---|---|---|
| POST | `/networks` | operator/admin + `Idempotency-Key` | `backend/app/api/hydraulic_routes.py:1383` |
| GET | `/networks` | viewer | `backend/app/api/hydraulic_routes.py:1364` |
| POST | `/networks/import` | operator/admin + key | `backend/app/api/hydraulic_routes.py:1420` |
| GET | `/networks/{network_id}` | viewer | `backend/app/api/hydraulic_routes.py:1474` |
| PATCH | `/networks/{network_id}` | operator/admin + key + expected revision | `backend/app/api/hydraulic_routes.py:1482` |
| DELETE | `/networks/{network_id}` | admin；幂等 archive（非物理删除） | `backend/app/api/hydraulic_routes.py:1522` |
| GET | `/networks/{network_id}/revisions` | viewer | `backend/app/api/hydraulic_routes.py:1533` |
| POST | `/networks/{network_id}/revisions` | operator/admin + key | `backend/app/api/hydraulic_routes.py:1583` |
| POST | `/networks/{network_id}/revisions/{revision_id}/activate` | operator/admin + key | `backend/app/api/hydraulic_routes.py:1622` |
| GET | `/networks/{network_id}/target-manifest` | viewer | `backend/app/api/hydraulic_routes.py:1557` |
| GET/POST | `/networks/{network_id}/nodes[/{node_id}]` | viewer / operator/admin + key | `:1744` / `:1765` / `:1820` / `:1831` / `:1884` |
| GET/POST | `/networks/{network_id}/pipes[/{pipe_id}]` | viewer / operator/admin + key | `:1925` / `:1946` / `:2011` / `:2022` / `:2075` |
| GET/POST/DELETE | `/networks/{network_id}/pipes/{pipe_id}/elevation-profile` | viewer / operator/admin | `:2325` / `:2343` / `:2390` |
| GET/POST | `/networks/{network_id}/catchments[/{catchment_id}]` | viewer / operator/admin + key | `:2116` / `:2137` / `:2204` / `:2217` / `:2277` |
| GET | `/networks/{network_id}/map-data` | viewer | `backend/app/api/hydraulic_routes.py:2441` |
| POST | `/networks/{network_id}/validate` | operator/admin + key；202 建 `queued` job | `backend/app/api/hydraulic_routes.py:2535`（**已有 live 实现**） |
| GET | `/networks/{network_id}/exports/{file_format}` | viewer | `backend/app/api/hydraulic_routes.py:2684` |
| POST | `/networks/{network_id}/seed` | admin + key；202 建 `queued` job | `backend/app/api/hydraulic_routes.py:2738` |
| GET | `/network-jobs/{job_id}` | viewer | `backend/app/api/hydraulic_routes.py:2600`（路径参数名是 `job_id`） |
| GET | `/validation-runs/{validation_id}` | viewer | `backend/app/api/hydraulic_routes.py:2590` |
| GET | `/network-imports/{import_id}` | viewer | `backend/app/api/hydraulic_routes.py:2610` |
| POST/GET | `/rainfall-scenarios` | operator/admin + key / viewer | `backend/app/api/hydraulic_routes.py:2854` / `:2827` |
| GET/PATCH/DELETE | `/rainfall-scenarios/{scenario_id}` | viewer / operator/admin + key + `expected_version` | `:2901` / `:2913` / `:3018` |
| POST | `/rainfall-scenarios/{scenario_id}/copy` | operator/admin + key | `backend/app/api/hydraulic_routes.py:3045` |

> `elevation-profile` 的写方法是 **POST**，不是 PATCH。handler 叫 `put_elevation_profile`，
> 但装饰器是 `@router.post`（`backend/app/api/hydraulic_routes.py:2343`），冻结契约
> `IMPLEMENTATION_CONTRACT.md:1531-1532` 写的也是 `GET/POST` + `DELETE`。
> 早前几版本文把它写成 `GET/PATCH/DELETE`，与 live 路由和契约都矛盾，本轮已改。

### 3.2 Simulation / A/B / calibration

| Method | Path | 角色/Header | live 实现位置 |
|---|---|---|---|
| POST | `/simulations` | operator/admin + key | `backend/app/api/hydraulic_routes.py:3160` |
| GET | `/simulations` | viewer | `backend/app/api/hydraulic_routes.py:3131` |
| GET | `/simulations/{simulation_id}` | viewer | `:3277` |
| POST | `/simulations/{simulation_id}/cancel` | operator/admin + key | `:3287` |
| GET | `/simulations/{simulation_id}/result` | viewer，completed only | `:3339` |
| GET | `/simulations/{simulation_id}/frames` | viewer，completed only | `:3364` |
| GET | `/simulations/{simulation_id}/frames/{frame_index}` | viewer，completed only | `:3436` |
| GET | `/simulations/{simulation_id}/observed-comparison` | viewer；**当前固定返回 `not_ready`/`pairs=[]`** | `:3466` |
| GET | `/simulations/{simulation_id}/calibration` | viewer | `:3485` |
| GET | `/simulations/{baseline_id}/compare/{candidate_id}` | viewer；单位/方向不一致 409 | `:3520` |
| POST | `/calibrations`（别名 `/calibrate`） | operator/admin + key | `:3643` |
| GET | `/calibrations/{calibration_id}` | viewer | `:3712` |

### 3.3 Forecast / system

| Method | Path | 角色 | live 实现位置 |
|---|---|---|---|
| GET | `/forecast/latest?network_id=...` | viewer | `backend/app/api/hydraulic_routes.py:3771` |
| GET | `/forecast/horizon?...` | viewer | `:3809` |
| GET | `/forecast/risk-ranking?network_id=...` | viewer | `:3849` |
| GET | `/forecast/alerts?network_id=...` | viewer | `:3878` |
| POST | `/forecast/alerts/{alert_id}/transitions` | operator/admin + key + `expected_version` | `:3899` |
| GET | `/forecast/events?network_id=...` | viewer；JWT 只放 Authorization header | `:3985` |
| GET | `/health` | public | `backend/app/main.py:1401` |
| GET | `/readyz` | public；`ready=false` 时 503 | `backend/app/main.py:1392` |
| GET | `/metrics` | Prometheus | `backend/app/main.py:1155`（`prometheus_fastapi_instrumentator.expose` 的调用点） |
| GET | `/api/health` | public | `backend/app/api/routes.py:203` |

注意：**没有 `POST /api/nowcast`**。nowcast 由 `nowcast-worker` 服务产生，根 Compose
**已有**该服务（`docker-compose.yml:356-433`）。production 下 forecast 路由还受
`backend/app/main.py:1285-1298` 的 runtime policy 门禁，未就绪时返回 503
`FORECAST_POLICY_UNAVAILABLE`（`Retry-After: 30`）。

`/metrics` 与其他路由不同：它没有 `@app.get` 装饰器，而是 `prometheus_fastapi_instrumentator`
在 `.expose(app, endpoint="/metrics")` 时注册的。因此路由导出会把它的**实现位置**显示成
site-packages 里的 `prometheus_fastapi_instrumentator/instrumentation.py`，而本表引用的是
**本仓库的调用点** `backend/app/main.py:1155`（`Instrumentator(...)` 构造在 `:1151-1154`，
`.expose(...)` 调用在 `:1146`）。这不是行号错误。

## 4. Frontend 路由索引

| 路由 | 页面 | 权限/说明 | live 实现位置 |
|---|---|---|---|
| `/` | `LandingPage` | public | `frontend/src/App.tsx:58` |
| `/login` | `LoginPage` | public | `frontend/src/App.tsx:59` |
| `/dashboard` | `Dashboard` | 登录后 | `frontend/src/App.tsx:61-62` |
| `/map` | `MapView` | 登录后 | `frontend/src/App.tsx:69-70` |
| `/simulation` | `SimulationPage` | 登录后；viewer 可只读 | `frontend/src/App.tsx:77-84` |
| `/video` | `VideoMonitor` | 登录后 | `frontend/src/App.tsx:85-86` |
| `/alerts` | `AlertCenter` | 当前 UI 路由要求 operator | `frontend/src/App.tsx:93-98` |
| `/settings` | `SettingsPage` | 登录后 | `frontend/src/App.tsx:101-102` |
| `*` | `Navigate to="/"` | fallback | `frontend/src/App.tsx:108` |

不要引用不存在的 `npm run e2e`。浏览器验收脚本是 `frontend/e2e/run-ac-smoke.mjs`；它不是
package script，只有在对应 owner 定义的 G6/e2e harness 中才有验收意义。

## 5. Compose 服务索引

### 5.1 当前根 Compose 的 services（集合口径）

**输出顺序随 Compose 版本变化，因此不是契约。** 2026-09-28 在 Docker 29.4.2 /
Compose v5.1.3 上执行（用 `--env-file` 指向一份"`.env.example` + 补齐它留空的 6 项"的
合成 env，排除本机未跟踪的 `.env`，否则那台机器上的 `.env` 会掩盖缺失的必填变量）。
完整可复制命令见 [`operations.md`](./operations.md) §3.1。**不要用空文件**：
`--env-file` 整体替换 env 解析，空文件 = 零变量，2026-09-28 实测直接 exit 1
（`required variable DATABASE_URL is missing a value`）。

```bash
export CLEAN_ENV=/tmp/clean.env
cp .env.example "$CLEAN_ENV"
cat >> "$CLEAN_ENV" <<'ENV'
POSTGRES_PASSWORD=doc-postgres-only
GRAFANA_ADMIN_PASSWORD=doc-grafana-only
INFLUXDB_PASSWORD=doc-influx-only
JWT_PRIVATE_KEY=doc-private-key-not-runtime-valid
JWT_PUBLIC_KEY=doc-public-key-not-runtime-valid
REDIS_PASSWORD=doc-redis-only
REDIS_URL=redis://:doc-redis-only@redis:6379/0
ENV
docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring -f docker-compose.yml config --services
```

exit 0，返回 **15** 个服务。按 profile 归类：

| Profile | Services（集合，无序） | 数量 |
|---|---|---|
| 默认（无 `--profile`） | `postgres`、`redis`、`influxdb`、`migrate`、`backend`、`simulation-worker`、`network-job-worker`、`nowcast-worker`、`outbox-relay`、`notification-sink`、`notification-worker`、`nginx` | 12 |
| `production` | 默认集合 + `frontend` | 13 |
| `monitoring` | 默认集合 + `prometheus`、`grafana` | 14 |
| 两者都开 | 上面全部 | **15** |

> ⚠️ 2026-09-28 复核时这套集合与上一版文档**不同**（上一版 14 个，不含 `notification-sink`；
> 更早还写过 7/8/10 与 11/12/13/14）。本轮新增 `notification-sink`（`docker-compose.yml:513-567`，
> `expose: 8030`），它**不在** `G6_REQUIRED_SERVICES` 里，属"多于要求"而非缺口。
> 根 Compose 是一份**未提交**的工作树改动（HEAD `ca99e048` 只有 190 行，工作树 802 行）。
> 引用本表前请按上面的命令重取。

`docker compose ... config --format json` 的 `services[*].ports` 给出真实主机端口：
`backend 8000`、`nginx 8080`、`frontend 3000`、`prometheus 9090`、`grafana 3001`；
`postgres`/`redis`/`influxdb`/`migrate` 与 6 个 worker 都没有 `ports`（worker 用
`expose`：`simulation-worker 8010`、`network-job-worker 8021`、`nowcast-worker 8022`、
`outbox-relay 8023`、`notification-sink 8030`、`notification-worker 8024`）。

### 5.2 目标服务缺口

`scripts/verify_hydraulic_stack.py:44-58` 的 `G6_REQUIRED_SERVICES` 要求 14 个服务
（**不含** `notification-sink`）。**2026-09-28 实测：`--gate g6 --mode config` 的 static
check 已无缺口。**

```bash
python -B scripts/verify_hydraulic_stack.py --gate g6 --mode config \
  --profile portable --evidence-dir .runtime/evidence/dd-r7-portable
```

```text
HYDRAULIC_STACK_STATUS=PASS
HYDRAULIC_STACK_EXIT=0
"static_failures": []
```

同命令换 `--profile postgis` **仍然 FAIL**：

```text
HYDRAULIC_STACK_STATUS=FAIL
HYDRAULIC_STACK_EXIT=1
"static_failures": ["postgis profile does not select an explicit PostGIS image",
                    "postgis profile renders the portable default postgres image"]
```

根因：根 Compose 的 `postgres` 固定用 `timescale/timescaledb@sha256:3f770468…`
（`docker-compose.yml:9`），**没有** PostGIS 变体，所以 `--profile postgis` 在根 Compose
上无处可选。PostGIS/portable 双 profile 只存在于 `docker/postgres/compose.yml`
（仅数据库，非完整应用栈），它**不能**当作 G6。

> G6 报告自带 `scope_note`："Compose config is a configuration check only; it is not
> live G6 or AC-8 evidence." 上面这两条 PASS/FAIL **只覆盖配置渲染**。
> `backend/app/main.py:84-90` 的 `_WORKER_HEARTBEAT_KEYS` 要求 5 个 heartbeat
> （network-job、simulation、nowcast、outbox-relay、notification）。

### 5.3 其他 live 阻断

- **（2026-09-28 实测）** `simulation-worker` 的 Compose command 是
  `python -m app.workers.simulation_worker`（`docker-compose.yml:203`），
  `backend/app/workers/simulation_worker.py:1880` 有 `def main(argv)`，`:1924` 起是
  `if __name__ == "__main__":`；Compose 侧有
  `SIMULATION_WORKER_HEALTH_HOST/PORT`（`:251-252`）、`expose: 8010`（`:253-254`）与打
  `http://127.0.0.1:8010/readyz` 的 healthcheck（`:268`）。
  根 Compose 已在 `docker-compose.yml:250` 给出默认值
  `${SIMULATION_SOLVER_COMMAND:-python,-m,app.services.hydraulic.solvers.cli}`，
  `.env.example` 仍然没有这个键。带该默认值实跑 worker，不再报
  `solver_command_unavailable`；不设该变量单独跑则仍 **exit 3** +
  `reason=solver_command_unavailable`（两个分支都实测过，见
  `implementation/delivery-docs.md` §2.7）。2026-09-28 那台栈里该 worker 是 `healthy`。
- **（2026-09-28 实测，`forecast_policy` 阻断已解除）** 上一版写的
  "`forecast_policy` 恒 false → `backend` unhealthy → `nginx`/`frontend` 不启动"
  **已不复现**：2026-09-28 实测 `GET /readyz` 返回 **200**，`ready=true`、`reason=null`，
  `required_tables`/`extensions`/`hypertables`/`forecast_policy` 四项全 `true`，
  5 个 worker heartbeat 全新鲜，`backend-1` 为 `healthy`，`migrate-1` `Exited (0)`。
  缺陷在源码里也确实改掉了：`_validate_forecast_artifacts` 的 `validate_file` 现在把
  `artifact_format` 作为**入参**接收（`backend/app/main.py:665-673`），
  对 `forecast_rules` 一路显式传 `None`（`:781`），只有 `probability_calibrations`
  一路才读 `row["artifact_format"]`（`:757`）。旧的 `NoSuchColumnError` 根因
  （`:711-717` 的 SELECT 缺该列 + `:678` 读 `row["artifact_format"]`）在当前源码中**不存在**。
  复现与定位见 `operations.md` §3.2.1 与 `implementation/delivery-docs.md` §2.5。
- **（2026-09-28 实测，当前真实阻断是数据不是代码）** 同一时刻只读查库：
  `hydraulic.networks` **1 行**，且是 notification-sink 的投递测试网络
  （`code=SINK-TEST`，`current_revision_id` 为空）；而 `network_revisions` /
  `network_nodes` / `pipe_segments` / `catchments` / `rainfall_scenarios` /
  `network_jobs` / `simulations` / `nowcast_runs` / `forecast_alerts` /
  `ab_comparisons` / `calibration_runs` / `observation_samples` **全部 0 行**。
  所以 50 年一遇、nowcast、告警、A/B、率定的演示**仍然跑不通**，
  但阻断点已从"readiness 挂了"变成"没有可用的演示数据与已验证的执行路径"。
- **（2026-09-28 观测边界）** 上面那台栈**不是本模块起的**，也**没有起 `production` profile**，
  所以 `nginx`/`frontend` 容器不存在，`http://localhost:8080` / `http://localhost:3000`
  **本轮未观测到可用**。它是"当前代码的真实状态"证据，**不是** live G6，也不是 AC-8 证据。
- `docker/postgres/compose.yml` 只提供 `postgres-postgis` / `postgres-portable` 两个数据库
  profile，不含完整应用栈，**不能**当作 G6；它自己的构建与校验步骤见
  `docker/postgres/README.md`。
- Prometheus 只挂载自身 config，没有挂载 `docker/prometheus/rules`；Grafana 没有挂载
  provisioning/dashboard。

这些是 live 阻断，不是文档可绕过的选项。

## 6. 脚本与命令索引

| 目的 | 唯一入口 | 说明 |
|---|---|---|
| G0～G6/acceptance | `python -B scripts/verify_hydraulic_stack.py --gate ... --evidence-dir ...` | 冻结 entrypoint；`--mode config\|live`、`--profile`、`--run-id`、`--ac`、`--live-timeout-seconds` |
| migration 静态 gate | `python -B scripts/check_migration_gate.py ...` | 需 review artifact/base ref/versions dir |
| dependency handoff | `python -B scripts/check_dependency_handoff.py ...` | 需 expected fixture 与实际配置文件 |
| 数据库 profile 校验 | `python -B scripts/verify_database.py --profile <postgis\|portable> --dsn <url>` | 只读，校验 extension/hypertable/retention |
| 备份 | `./scripts/backup.sh ...` 或 `scripts/backup.ps1` | 参数见 `scripts/backup.py` argparse；`--solver-sha256` 默认取 `SOLVER_ENGINE_SHA256` |
| 离线验证/恢复 | `./scripts/restore.sh <archive> --verify-only` / `--apply --confirm <id>` | 参数见 `scripts/restore.py:823-864` |
| operations 离线测试 | `python -B scripts/backup_test.py` | 不需要 Docker daemon；**不是 live restore** |
| 监控静态验证 | `python -B docker/prometheus/validate_monitoring.py` | 验证 rules/dashboard 定义；不证明 Compose 已挂载 |
| 前端单测 | `npm --prefix frontend run test` | `vitest run` |
| 前端 lint/format/build | `npm --prefix frontend run lint` / `run format:check` / `run build` | 不存在额外自创 script |
| 前端 G5 | `npm --prefix frontend run test:perf:twin -- --fixture ... --duration-seconds 60 --output "artifacts/hydraulic/$RUN_ID/g5/frontend.json"` | 目标 Node 18.20.8/4 cores/8 GiB，否则 `BLOCKED`；`$RUN_ID` 必须先 `export`（见 [`operations.md`](./operations.md) §2.2） |

### 6.1 `--smoke-header` vs `--smoke-header-env`（易错）

`scripts/restore.py` 的两个参数语义**不同**，混用会让 forecast SSE smoke 必然 401/403：

- `--smoke-header NAME=VALUE`（`scripts/restore.py:188-197`）→ 发送 header `NAME`；`=` 必须是第一个分隔符，冒号写法会被拒。
- `--smoke-header-env ENV_NAME`（`scripts/restore.py:722-726`）→ 读取 `ENV_NAME` 的值，但**把 `ENV_NAME` 本身当 header 名**发送。

所以认证 smoke 必须写 `--smoke-header "Authorization=$SMOKE_AUTHORIZATION"`。
这会让 token 出现在 argv，属于已知脚本缺陷，需要 `operations` 脚本 owner 修复并补回归测试。
详见 [`operations.md`](./operations.md) §9.4.1 与
[`implementation/delivery-docs.md`](./implementation/delivery-docs.md) §2.6 末尾的
`smoke_header_probe.py` 三用例块。

不要把 `docker compose config`、静态 test 或单 profile build 写成 G6/AC-8 PASS。

## 7. 文档导航

### 7.1 规范与运维

- [`operations.md`](./operations.md)：Compose、API 诊断、50 年、nowcast/告警/SSE、A/B、率定、备份恢复、回滚、故障排查。
- [`implementation/delivery-docs.md`](./implementation/delivery-docs.md)：本模块本轮执行的命令、原始输出与风险。
- [`IMPLEMENTATION_CONTRACT.md`](./IMPLEMENTATION_CONTRACT.md)：唯一可执行技术契约。
- [`PRD_SUPPLEMENT.md`](./PRD_SUPPLEMENT.md)：产品语义与 AC 门槛。
- [`SOURCE_PRD.md`](./SOURCE_PRD.md)：原始需求。

### 7.2 Supporting contracts（历史）

- `contracts/data.md`
- `contracts/forecast.md`
- `contracts/frontend.md`
- `contracts/hydraulics.md`
- `contracts/operations.md`

它们保留审查轨迹，但状态是 superseded；不能从中复制旧 endpoint、旧 worker 名、旧状态机或旧阈值覆盖 v3 契约。

### 7.3 实施、recon、review

- `implementation/**`：按模块的实现记录；只能报告其实际执行范围。
- `recon/**`：重构前现状调查；历史测试结果不自动成为当前 PASS。
- `reviews/**`：独立审查证据；最终 verdict 仍以 JSON artifact 和 live rerun 为准。

## 8. 维护规则

1. 文档命令必须先与 live `package.json`、Compose、route decorator、脚本 argparse 对齐。
2. 每次写“通过”同时记录：精确命令、exit code、关键输出、artifact path/hash、环境/commit。
3. 没有运行写 `NOT RUN`；外部条件阻断写 `BLOCKED`；运行失败写 `FAIL`。
4. `R0_DESIGN_PASS`、migration static PASS、dependency handoff `PASS_WITH_DEFERRED`、单元测试 PASS、Compose config PASS 都不得替代 G0～G6/AC。
5. 旧 artifact 必须带执行时间/commit/input hash；工作树变化后重跑对应 checker。
6. **Compose service 列表与 `config --services` 的输出顺序不是契约**，只按集合核对。
7. 文档里 ```bash 块**不得出现裸 `<PLACEHOLDER>`**：bash 会把它解析成重定向，
   块在运行时才失败，`bash -n` 抓不到。所有可替换值一律写成 `"$VAR"` 并在块前 `export`。
   自动守卫 `.runtime/doc_checks.py` 的 `BASH_PLACEHOLDERS` 只覆盖 `<xxx>` 与 `<XXX_YYY>`
   （大小写不敏感），**不覆盖带连字符的 `<xxx-yyy>`**——该规则目前靠人工遵守。
8. 跨节引用的 shell 变量必须在手册里**显式导出**；用 `: "${VAR:?说明}"` 守卫，
   避免空串静默展开后产生误导性的 404。
   **注意 `:?` 只抓“未设置/为空”**。把 `'<某个 UUID>'` 这种**非空占位串**赋进去，守卫
   不会触发，请求会带着非法串发出并得到 404——比没有守卫更误导。要留占位就留空，
   再加一层 `case` 检查占位文本（`operations.md` §7.0 是范例）。
9. **行号必须逐条验证 method + path，不能只验证行号落在文件范围内。**
   `:2343` 指向一个真实装饰器，不代表它就是 `PATCH`——`elevation-profile` 的 `:2343`
   实际是 `@router.post`，文档曾长期写成 `PATCH` 而行号“看起来是对的”。
   写完路由表后重跑 `.runtime/doc_checks.py`：`ROUTE_CITATION_MISMATCH` 必须为 `none`。
10. **`path:line` 散文引用当前只校验"在文件范围内"，不校验"指向哪一行"。**
    `.runtime/doc_checks.py` 的 `CITATIONS_OUT_OF_RANGE` 判断只是行号 < 文件行数，
    所以 `backend/app/api/routes.py:79`（一个空行）能通过检查，而真实的
    `APIRouter(prefix="/api")` 在 `:80`。**不要把 `CITATIONS_OUT_OF_RANGE=none` 读成
    "所有行号都对"**：强的只有路由表的 method+path（第 9 条），弱的才是散文里的 `path:line`。
    改动这些行号时需人工比对那一行的内容。
11. **`/api` 路由导出不覆盖根级路由。** `/health`、`/readyz`、`/metrics` 没有 `/api`
    前缀，任何只导出 `/api` 的 dump 都不含它们。凡引用根级路由，必须直查源码并让
    `doc_checks.py` 的路由检查覆盖到，否则这类行号会静默漂移（本类问题已发生过一次，
    偏移 +11 行）。
12. **Compose 配置预检的 `exit 0` 必须写清前提。** `docker compose` 会自动读工作目录的
    `.env`；本仓库根的 `.env` 未被 Git 跟踪（`.gitignore:31`）却定义了这些必填变量。
    不写前提的 `exit 0` 会被误当成"在干净环境也成立"。
    ⚠️ **`--env-file` 不是"追加"而是"整体替换"**：指向空文件等于**零变量**，
    2026-09-28 实测直接 exit 1。干净环境复现必须指向一份
    "`.env.example` + 补齐它留空的必填项"的合成 env（见 `operations.md` §3.1）。
    另注：`.env.example` 里的 14 个必填变量中有 6 个是**空值**，
    所以 `cp .env.example .env` 本身不足以让 `config` 通过。
13. **`docker-compose.yml` 的 `path:line` 同样受未提交并行改动影响，跨轮复核必须重取。**
    根 Compose 是一份**未提交**的工作树改动（HEAD `ca99e048` 只有 190 行；2026-09-27
    复核时工作树 700 行，2026-09-28 已是 **802** 行）。第 10 条讲的是"检查器不验证
    指向哪一行"，本条讲的是"**目标文件本身会整体位移**"：2026-09-27 一轮里 5 处
    `docker-compose.yml:行号` 整体偏移，2026-09-28 又整体位移一次（`postgres` image
    `:9` 侥幸没动，但 worker command 从 `:195` → `:203`、`SIMULATION_SOLVER_COMMAND`
    从 `:242` → `:250`、`GRAFANA_ADMIN_PASSWORD` 从 `:676` → `:778`、
    `network-job-worker` 块从 `:275-343` → `:283-355`）。
    这些引用大多落在 README 自称"可核验"的段落里——**结论常常仍成立，坏的只是指针**。
    因此凡在"可核验"段落里引用 Compose 行号，先跑一次
    `grep -n "<锚点>" docker-compose.yml` 重取，再写进文档。
14. **"已修复"和"仍然坏着"同样会过期，两种都要重新取证。** 文档里最贵的错误不是行号偏移，
    而是把一个**已经被别的 owner 改掉**的缺陷继续写成"当前阻断"。本项目已发生两次：
    ① 2026-09-27 写"根 Compose 缺 4 个 worker"，2026-09-28 实测 15 个服务全在；
    ② 2026-09-27 写"`forecast_policy` 因 `NoSuchColumnError: artifact_format` 恒 false
    → `/readyz` 固定 503"，2026-09-28 实测 `/readyz` 200、该缺陷在源码里已不存在。
    正确做法：凡是"当前阻断/当前不能做"的断言，本轮都要重新取一次证据
    （本轮是 `curl /readyz` + `psql` 计数 + `docker ps`），
    并在文本里写明**是哪一轮**测的。**只把结论改成"已通过"同样违规**——
    要么给出这轮的真实命令与输出，要么明确标 `NOT RUN`。
15. **"不把未验证项写成已通过"要对称执行。** 只盯着"别夸大成功"会漏掉另一半：
    过期的"失败"结论的破坏性与夸大成功相同——它让运维去找不存在的缺口、
    或按"修复前不要绕过"停在早已修好的地方。`doc_checks.py` 结构上抓不到这类问题：
    它只验链接、`bash -n`、package script、`path:line` 范围和路由 method+path，
    **完全不校验自然语言结论的真伪**。这类只能靠每轮重新取证 + 标注轮次。
16. 本索引不修改 `SOURCE_PRD.md` 正文，也不把 `contracts/*.md` 恢复为规范。

