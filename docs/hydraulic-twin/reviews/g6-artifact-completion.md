# G6 验收工件补齐记录（G0–G6 fixture / compose worker 现状复核）

> 日期：2026-09-25。执行者：本轮工件补齐子流程（`G0-G6验收工件补齐者`）。
>
> **本文不是 gate result，不写任何 PASS/FAIL 裁决。** `docs/hydraulic-twin/reviews/**` 在 `IMPLEMENTATION_CONTRACT.md:2384` 归 `r0-reviewer` 所有，本文只记录“本轮实际读到的代码事实 / 本轮实际执行的命令与输出 / 精确 blocker”，不代替独立复审。
>
> 环境：Windows `10.0.26340`，`docker 29.4.2` / `Docker Compose v5.1.3`，Python `3.11.15`（`backend/.venv/Scripts/python.exe`），逻辑 CPU 32，`physical_cores` 不可读。Docker daemon 不可用（`docker info --format {{.ServerVersion}}` 无输出），因此 **未运行任何 live G6**。

## 1. 结论摘要

| 交付项 | 结果 | 证据 |
|---|---|---|
| G6 fixture 1 `docker/hydraulic-gates/compose-v1.lock.json` | **已交付**（契约工件，非证据） | `g6-*.fixtures` step PASS，见 §4.1 |
| G6 fixture 2 `backend/tests/fixtures/hydraulic/compose/restore-manifest-v1.json` | **已交付**（契约工件，非证据） | 同上 |
| verifier 的 G6 fixture 契约静态交叉校验 | **已实现并通过负向验证** | §4.3 |
| 4 个 durable worker service 定义 | **BLOCKED：被引用模块不存在** | §3.1，逐 worker 列出 |
| frozen solver manifest `5k_10k_24h_v1` | **BLOCKED：5 个真实输入工件无法产生** | §3.2 |
| postgis profile 的 PostGIS 镜像选择 | **BLOCKED：仓库内无真实 PostGIS+Timescale 镜像 digest** | §3.3 |
| G6 门禁本身 | **仍 FAIL/1**（两个 profile 都是） | §4.1、§4.2 |

`docker-compose.yml` 本轮**未修改**（mtime `2026-09-25T22:14:32`，早于本轮首次写入 `23:38:01`；见 §4.4 的 mtime 对照）。

## 2. 读取到的事实基线

### 2.1 契约侧要求

- 冻结 G6 fixture registry：`IMPLEMENTATION_CONTRACT.md:2486` —— `docker/hydraulic-gates/compose-v1.lock.json`、`backend/tests/fixtures/hydraulic/compose/restore-manifest-v1.json`。
- G6 语义：`IMPLEMENTATION_CONTRACT.md:2496` —— postgis / portable 两条独立命令、两个独立 evidence 目录、fresh volume、README demo、backup→destroy→restore hash/API、legacy characterization，各自输出 profile manifest。
- G5 solver fixture：`IMPLEMENTATION_CONTRACT.md:2505` —— `backend/tests/fixtures/performance/solver/5k_10k_24h_v1/manifest.json`，含 network/rainfall/target-manifest/initial-state/solver config SHA-256。
- 缺失 gate/harness/fixture 只能记 NOT RUN：`IMPLEMENTATION_CONTRACT.md:40`；使用不存在的 entrypoint 属发布阻断：`IMPLEMENTATION_CONTRACT.md:2583`。
- 禁止伪造/placeholder：`IMPLEMENTATION_CONTRACT.md:544`（“禁止测试运行时生成‘期望值’掩盖非确定性”）、`:2583`。

### 2.2 gate 侧要求

- G6 必备 service 集合（14 个）：`scripts/verify_hydraulic_stack.py:44`（`G6_REQUIRED_SERVICES`；配套常量 `:40`、`:60`、`:66`、`:67`、`:73`）。
- 静态检查项：digest pin、postgres/redis/influx 不暴露宿主端口、migrate 必须是 `alembic upgrade head`、4 个 service 必须 `depends_on migrate: service_completed_successfully`、compose 内无硬编码 secret、`docker/init-db.sql` 无业务 schema marker、nginx 4 条 SSE 指令 —— `scripts/verify_hydraulic_stack.py:944`（`_compose_static_failures`）。
- live 探针：`backend/health`、`backend/readyz`、`nginx/health` —— `scripts/verify_hydraulic_stack.py:73`（`G6_PROBES`），消费点在 `gate_g6`（`:1362`）。
- solver manifest 校验规则：`scripts/benchmark_hydraulic.py:198-371`（`schema=hydraulic-solver-benchmark-v1`、5 个 `path`+`sha256` 引用、`input_counts` 必须精确 5000/10000/1000、`frames` 289/5min/1440min、`solver.id=py_mw` 且 `engine_sha256` 合法、`executor.module` 必须是 `app.*`）。
- backup manifest 契约：`scripts/backup_manifest.py:13-16`（`smart-city-drainage-backup-v2`、`manifest.json`、`manifest.sha256`）。
- backup/restore CLI 真实参数：`scripts/backup.py:730-771`、`scripts/restore.py:827-862`；组件集合 `scripts/backup.py:40`。

### 2.3 就绪契约（worker 心跳）

- `backend/app/main.py:84-90` 定义 5 个 worker 心跳键：`network-job` / `simulation` / `nowcast` / `outbox-relay` / `notification` → `hydraulic:worker:heartbeat:<name>`。
- `backend/app/main.py:798`：新鲜度窗口 `0 < now - observed <= 75.0` 秒。
- `backend/app/main.py:893-902`：`readyz` 要求 5 个心跳**全部**为真，否则 `ready=False`。
- **全仓无任何写入这些键的代码**（`grep -rn heartbeat backend/app` 只命中 `tasks.py` 的任务租约心跳与 `main.py` 的读取方；`redis_client.set` 存在但从未以 `hydraulic:worker:heartbeat:` 调用）。这 5 个键目前**没有 publisher**。

## 3. 精确 blocker（本轮未伪造绕过）

### 3.1 B1 —— 4 个 durable G6 worker service：被引用的 worker 模块不存在

`docker-compose.yml` 现有 `simulation-worker`（`:174-241`，`command: ["python","-m","app.workers.simulation_worker"]`）。G6 还要求 `network-job-worker`、`nowcast-worker`、`outbox-relay`、`notification-worker`，本轮实测：

- `backend/app/workers/` 实际只有 `__init__.py`、`inference_worker.py`、`simulation_worker.py`（目录列举）。
- 全仓唯一 `if __name__ == "__main__"` 在 `backend/app/main.py:1488`，且无条件 `uvicorn.run("app.main:app", port=8000)`，不能当 worker 入口。
- `backend/app/workers/simulation_worker.py` 无 `__main__` 块，文件以 `__all__` 结束（约 `:722-731`）。因此**既有** `simulation-worker` 服务的 `python -m app.workers.simulation_worker` 只 import 后立即退出，`restart: unless-stopped` 下会循环退出；其健康检查目标 `http://127.0.0.1:8010/readyz` 也没有任何服务在监听（`backend/app` 内无 HTTP server）。
- `SIMULATION_WORKER_HEALTH_HOST` / `SIMULATION_WORKER_HEALTH_PORT`（compose `:217-218`）在 `backend/app/core/config.py` 中**不是 Settings 字段**（该文件无此名），而 `Settings.model_config` 是 `extra="forbid"`（`config.py:133`）——这两个变量当前无消费方。
- 逐 worker 的“已有能力 / 缺什么”：
  - `network-job-worker`：状态机侧已就绪（`backend/app/services/hydraulic/tasks.py:79-83` 的 `TaskKind.network_job`、`DurableJobStateMachine:293`、`SqlAlchemySimulationTaskRepository:1446`；解析/校验/seed 实现在 `importers/swmm.py:2399`、`topology.py`、`seed.py`），**缺** claim→execute→publish 的 worker 循环与 `python -m` 入口，以及 `hydraulic:worker:heartbeat:network-job` 的 publisher。
  - `nowcast-worker`：`backend/app/services/hydraulic/nowcast.py:1754` 有 `NowcastScheduler` 与 `run_once_async`（`:2229`），**缺** 常驻循环/入口与 `hydraulic:worker:heartbeat:nowcast` publisher。
  - `outbox-relay`：`backend/app/main.py:165` 的 `DatabaseForecastOutboxRelay` 目前**由 API 进程持有**（`main.py:928` 写入 `app.state`，`main.py:993` `asyncio.create_task(_forecast_relay_loop())`）。要作为独立 service 必须先把 relay 从 API 进程搬出，否则会出现第二个逻辑 publisher，与 `IMPLEMENTATION_CONTRACT.md:2494`（outbox single path）、`:2568`（producer 双发布为发布阻断）冲突。搬动 `backend/app/main.py` 超出本轮授权路径。
  - `notification-worker`：`backend/app/services/notification_service.py:33` 的 `NotificationService` 有 `notify_forecast_event`（`:305`）/ `notify_alert`（`:448`），**缺** claim/lease/retry/DLQ worker 循环、入口与 `hydraulic:worker:heartbeat:notification` publisher。
- 结论：写 `command: ["python","-m","app.workers.<x>"]` 会指向不存在的模块（容器 `ModuleNotFoundError`），写 `sleep`/空转则违反“不创建空转服务”。两者都不做。**这 4 个 service 定义必须等 worker 模块 + 心跳 publisher + `/readyz` 监听真实落地后再补。**

### 3.2 B2 —— frozen solver manifest `5k_10k_24h_v1`：5 个输入工件无法真实产生

`scripts/benchmark_hydraulic.py:198-371` 强制要求 manifest 引用 5 个**真实文件**（`network`/`rainfall`/`target_manifest`/`initial_state`/`solver_config`），路径相对 fixture 目录、SHA-256 逐个校验，且 `input_counts` 必须精确 `{nodes:5000, pipes:10000, catchments:1000}`、`executor.module` 必须是 `app.*` 的真实可调用。实测阻塞点：

- 仓库内无任何 5000/10000/1000 规模的网络输入：`backend/tests/fixtures/` 实有文件只有 `hydraulic/acceptance/v1/*`（最大 9852 字节 `matrix.json`）与 `hydraulic/imports/swmm_minimal_*.inp`（407/409 字节）。
- 无生产 materializer 可产出 `target_manifest` / `initial_state` 实体工件：`hydraulic.network_target_manifests` / `initial_state_snapshots` 只有 ORM（`backend/app/models/hydraulic_models.py:917`、`:965`），没有生成 `stable_keys`/`feature_indexes`/`tree_sha256` 之类内容的代码；当前 simulation 提交路径把 `initial_state_content_sha256` 算成 `{"state":"dry","revision_id":...,"initial_state_id":...}` 的指纹（`backend/app/api/hydraulic_routes.py:3206-3212`），并写进进程内 `hydraulic_store`（`:3269`）——`backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json` 自己也写明当前 HTTP 诊断只证明 “queued, uncommitted adapter state”。
- 契约 §3.3.2 要求的强类型 `NetworkSnapshot` 在 `backend/app/schemas/hydraulic_schemas.py` 中**不存在**；solver 侧用的是 `python_hydraulics.py` 自有的 `HydraulicNetwork`/`HydraulicNode`/`HydraulicPipe` dataclass（`:274`、`:122`、`:160`），与 `:2576`（solver 不得接收 ORM/任意 properties）的目标相反。
- 因此我**没有**写 `manifest.json`：写出来必然是 5 个自造输入的哈希 + 一个不存在的 `executor`，`validate_manifest` 会 BLOCKED/2，属于“伪造 golden 数值”。

本轮真实运行（契约 §17.1.1 的 G5 命令）：

```text
$ backend/.venv/Scripts/python.exe scripts/benchmark_hydraulic.py --fixture backend/tests/fixtures/performance/solver/5k_10k_24h_v1/manifest.json --warmup 1 --repeats 3 --cpus 4 --memory 8g --threads 4 --output artifacts/hydraulic/g6-final/g5/solver.json --run-id g6-final
HYDRAULIC_BENCHMARK_STATUS=BLOCKED
HYDRAULIC_BENCHMARK_EXIT=2
error: {"message": "fixture manifest is missing: ...\\backend\\tests\\fixtures\\performance\\solver\\5k_10k_24h_v1\\manifest.json", "type": "BenchmarkError"}
benchmark_executed=false
exit=2
```

harness 自身可用的旁证（同一台机器、同一解释器）：

```text
$ backend/.venv/Scripts/python.exe scripts/benchmark_hydraulic.py --self-test
{"checks": [{"name": "valid_manifest", "status": "PASS"}, {"name": "hash_validation", "status": "PASS"},
            {"name": "hash_drift_rejected", "status": "PASS"}, {"name": "p95_nearest_rank", "status": "PASS"}],
 "schema": "hydraulic-solver-benchmark-self-test-v1", "status": "PASS"}
exit=0
```

即：阻塞来自缺失的真实输入工件，而不是 harness 缺陷。

### 3.3 B3 —— postgis profile 的 PostGIS 镜像

`scripts/verify_hydraulic_stack.py` 的静态规则要求：profile=postgis 时 postgres 镜像名含 `postgis`，profile=portable 时不含。当前 `docker-compose.yml:9` 是 `timescale/timescaledb@sha256:3f77…`。`grep -rn "postgis/"` 全仓无任何镜像 digest（只有 fixture/evidence 路径字符串）。verifier 用单个 `-f docker-compose.yml` 调用（`_compose_command`），且 `_compose_env` 只注入 `HYDRAULIC_SPATIAL_BACKEND` 等固定变量，没有按 profile 切换 image 的机制。

可选修法（都需要本轮授权之外的决定，故未执行）：(a) 把 `postgres.image` 改为 `${POSTGRES_IMAGE:-<portable 默认 digest>}`，由 postgis 运行环境导出真实的 PostGIS+Timescale 镜像 digest（必须同时满足契约 G1「portable 仍含 TimescaleDB、postgis 含 PostGIS」，我无法在本机验证这样一个镜像的 digest）；(b) 拆成 per-profile compose 文件并改 verifier 调用（改 gate 语义，本轮不做）。

## 4. 本轮交付物与实测命令

### 4.1 两个 G6 fixture（本轮新增）

`docker/hydraulic-gates/compose-v1.lock.json`
`sha256 = 0e622b9adeb006b97fabc322de4da3102a157b9c1faa886229cd887c202d3216`，4579 bytes，`schema=hydraulic-compose-lock-v1`。
每个字段都取自真实来源，无自造数值：required_services / migration_gate_services / services_without_host_ports / sse_nginx_directives / probes 直接对齐 `scripts/verify_hydraulic_stack.py` 的同名常量；`digest_pinned_images` 是 `docker-compose.yml` 里 6 个真实 digest；`worker_heartbeat_keys` 逐字取自 `backend/app/main.py:84-90`；`worker_heartbeat_window_seconds=75` 取自 `backend/app/main.py:798`；`local_image_services` 声明 4 个待补 worker 必须复用 `smart-city-drainage-backend:local` + `../docker/backend/Dockerfile.cpu`（与既有 `simulation-worker` 一致）。`worker_heartbeat_publishers` 显式写 `"NOT IMPLEMENTED"`，`evidence_state="NOT_RUN"`。

`backend/tests/fixtures/hydraulic/compose/restore-manifest-v1.json`
`sha256 = c4ecfcaf9c61ea171be2cdb671158a50a6680a2c1dd7c8e79f49b5261c307626`，7031 bytes，`schema=hydraulic-compose-restore-manifest-v1`。
内容取自真实实现：`manifest_schema`/`manifest.sha256` 来自 `scripts/backup_manifest.py:13-16`；`required_manifest_keys` 来自 `scripts/backup.py:662-692` 的 manifest 字面量；`components` 来自 `scripts/backup.py:40`；命令与 flag 全部来自 `scripts/backup.py:730-771` / `scripts/restore.py:827-862`；`restore_report.required_fields` 与 `stdout_markers` 来自 `scripts/restore.py:789-819`。**不含任何观测值**：`observed_results` 全部为 `null`，`evidence_state="NOT_RUN"`，`fail_closed_rules` 明确禁止跨 RUN_ID/commit 证据与把 config 模式当 restore 证据。

两文件都带 `content_sha256`（对去掉该字段后的 canonical JSON bytes 计算，符合 `IMPLEMENTATION_CONTRACT.md:98-108` 的 canonical JSON 规则），格式与仓库既有 fixture 一致（`backend/tests/fixtures/hydraulic/acceptance/v1/matrix.json` 的 `fixture_sha256` + “只冻结要求、不是执行证据” 的写法）。

### 4.2 G6 config 模式实测（两个 profile）

```text
$ backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py --gate g6 --profile portable --mode config --run-id g6-final --evidence-dir artifacts/hydraulic/g6-final/g6/portable
HYDRAULIC_STACK_STATUS=FAIL / HYDRAULIC_STACK_EXIT=1
artifact sha256 = 474fe15ce302b169316b9e787a80a68c80f2ae45c30c67e4a6ea3fd7a45f815d
  g6-portable-compose-config-quiet PASS
  g6-portable-compose-config-json   PASS
  g6-portable-static-contract       FAIL
  g6-portable.fixtures              PASS   <- 本轮从 BLOCKED 变为 PASS
  static_failures: ["missing target services: network-job-worker, notification-worker, nowcast-worker, outbox-relay"]

$ backend/.venv/Scripts/python.exe scripts/verify_hydraulic_stack.py --gate g6 --profile postgis --mode config --run-id g6-final --evidence-dir artifacts/hydraulic/g6-final/g6/postgis
HYDRAULIC_STACK_STATUS=FAIL / HYDRAULIC_STACK_EXIT=1
artifact sha256 = f21ca1dc2e9a1ce2116bf563a0bbdc559787a3adfdc0cc7952fa8abc76fdf1ee
  static_failures: ["missing target services: network-job-worker, notification-worker, nowcast-worker, outbox-relay",
                    "postgis profile does not select an explicit PostGIS image",
                    "postgis profile renders the portable default postgres image"]
```

基线对照（补 fixture 之前，同一 run-id 家族）：

```text
g6-portable.fixtures BLOCKED missing_fixtures=[docker/hydraulic-gates/compose-v1.lock.json,
                                               backend/tests/fixtures/hydraulic/compose/restore-manifest-v1.json]
```

`docker compose config` 直接执行（env 取 verifier 的 `_compose_env`）：

```text
$ HYDRAULIC_SPATIAL_BACKEND=portable ... docker compose --profile production --profile monitoring -f docker-compose.yml config --quiet   -> exit 0
$ HYDRAULIC_SPATIAL_BACKEND=portable ... docker compose ... config --format json                                                            -> exit 0, 18481 bytes
services: ['backend','frontend','grafana','influxdb','migrate','nginx','postgres','prometheus','redis','simulation-worker']
backend spatial: portable ; postgres image: timescale/timescaledb@sha256:3f77…
$ HYDRAULIC_SPATIAL_BACKEND=postgis ... docker compose --profile production --profile monitoring -f docker-compose.yml config --quiet  -> exit 0
```

渲染出的 10 个 service 就是 §3.1 的缺口本体：4 个 durable worker service 不存在。config 模式通过 ≠ G6 通过（`scripts/verify_hydraulic_stack.py` 的 `scope_note` 亦如此声明）。

### 4.3 verifier 的 G6 fixture 契约交叉校验（本轮新增）

`scripts/verify_hydraulic_stack.py`：`sha256 = b9ed97dc86d809e3c4eae72579069170700bc875e9afb62aa8e82e852e90c425`。改动：

1. 把 gate 自身的期望提为模块常量（`G6_REQUIRED_SERVICES`、`G6_MIGRATION_GATE_SERVICES`、`G6_SERVICES_WITHOUT_HOST_PORTS`、`G6_SSE_NGINX_DIRECTIVES`、`G6_PROBES`、两个 fixture 路径），`_compose_static_failures` 改为消费这些常量——**门禁判据本身未放宽**：required service 集合、migration 依赖条件、端口限制、SSE 指令、live 探针逐项与改前一致。
2. 新增 `g6_fixture_contract_failures()`（`scripts/verify_hydraulic_stack.py:1070`），在 `gate_g6`（`:1362`）渲染 JSON 成功后执行（`:1409`），让两个 fixture 真正承重：校验 schema、`content_sha256` 自洽、`evidence_state="NOT_RUN"`、lock 的 4 组列表与 gate 常量逐项相等、lock 的镜像与**实际渲染出的** config 逐项比对（postgis 下 postgres 必须是另一个 digest-pinned 的 PostGIS 镜像）、lock 的心跳键/窗口与 `backend/app/main.py` 现场重读比对、restore fixture 的 `manifest_schema` 与 `scripts/backup_manifest.py` 现场重读比对、`observed_results` 必须全 `null`、assertion id 唯一且带 requirement+predicate。

负向验证（证明不是空转检查，改完已复原，实测输出）：

```text
# 篡改 lock：删掉 notification-worker 且不重算 hash
static_failures: [... ,
  "compose lock content_sha256 does not match its canonical body",
  "compose lock required_services does not match the gate required services this gate enforces"]

# 篡改 restore fixture：填入伪造 archive_sha256 + 把 manifest_schema 改成 v1
static_failures: [... ,
  "restore manifest content_sha256 does not match its canonical body",
  "restore manifest must not ship populated observed results: ['archive_sha256']",
  "restore manifest manifest_schema drifted from scripts/backup_manifest.py (smart-city-drainage-backup-v2)"]
```

复原后 fixture 哈希回到 `0e622b9a…` / `c4ecfcaf…`，`g6-*.fixtures` 回到 PASS。

格式与静态检查：

```text
$ backend/.venv/Scripts/python.exe -m black --line-length 88 --check scripts/verify_hydraulic_stack.py   -> exit 0
$ backend/.venv/Scripts/python.exe -m isort --profile black --check-only scripts/verify_hydraulic_stack.py  -> exit 0
$ backend/.venv/Scripts/python.exe -m flake8 --config backend/.flake8 scripts/verify_hydraulic_stack.py     -> exit 0
$ backend/.venv/Scripts/python.exe -m py_compile scripts/verify_hydraulic_stack.py                          -> exit 0
```

（注：不带 `--config backend/.flake8` 的 flake8 会用默认 79 列并报 ~110 条 E501；项目配置 `backend/.flake8` 是 `max-line-length=88` + `extend-ignore=E501`，与 ci-quality 记录的 “project Flake8 PASS/0” 一致。）

### 4.4 单测

```text
$ PYTHONPATH=backend DATABASE_URL=sqlite+aiosqlite:///./test.db REDIS_URL=redis://127.0.0.1:1/0 \
  INFLUXDB_URL=http://127.0.0.1:1 JWT_SECRET_KEY=ci-quality-test-secret \
  backend/.venv/Scripts/python.exe -m pytest backend/tests -q --tb=no
553 passed, 4 skipped, 3 warnings in 21.83s          # exit 0
```

```text
$ ... -m pytest docker/backend/tests/test_deployment_contract.py backend/tests/test_contract_gate_scripts.py -q --tb=line
2 failed, 24 passed, 1 warning in 7.27s
FAILED ::DeploymentContractTests::test_database_url_percent_encodes_reserved_password_bytes
FAILED ::DeploymentContractTests::test_redis_probe_authenticates_without_changing_redis_url
```

这 2 个失败**不是本轮引入**：两者都断言 `docker-compose.yml` 内含硬编码字面量（`postgresql+asyncpg://drainage_admin:Dr%40inage_S3cur3_2024%21@postgres`、`REDIS_URL: redis://:${REDIS_PASSWORD:-`），而当前 compose 已是 `${DATABASE_URL:?…}` / `${REDIS_URL:?…}` 形式——后者正是 G6 静态规则 “compose 内无硬编码 secret/default” 所要求的形态。mtime 对照可证本轮未改 compose：`docker-compose.yml = 2026-09-25T22:14:32`，本轮首次写入 `scripts/verify_hydraulic_stack.py = 2026-09-25T23:38:01`。这两个测试断言与冻结 G6 secret 规则互斥，需由 `compose-worker`/`ci-quality` owner 决定改测试还是改 compose，本轮不动。

## 5. fixture registry 现状（同一 run 内静态列举）

```text
g1 MISSING  backend/tests/fixtures/hydraulic/schema/manifest-v1.json
g2 MISSING  backend/tests/fixtures/hydraulic/network_jobs/v1/manifest.json
g2 MISSING  backend/tests/fixtures/hydraulic/contracts/idempotency-ttl-v1.json
g2 MISSING  backend/tests/fixtures/hydraulic/security/jwt-rbac-ratelimit-v1.json
g3 MISSING  backend/tests/fixtures/hydraulic/workers/lifecycle-v1.json
g3 MISSING  backend/tests/fixtures/hydraulic/calibration/durable-lifecycle-v1.json
g3 MISSING  backend/tests/fixtures/hydraulic/hydraulics/conservation-stability-v1.json
g4 MISSING  backend/tests/fixtures/hydraulic/forecast/v1/manifest.json
g4 MISSING  backend/tests/fixtures/hydraulic/realtime/outbox-sse-v1.json
g6 PRESENT  docker/hydraulic-gates/compose-v1.lock.json
g6 PRESENT  backend/tests/fixtures/hydraulic/compose/restore-manifest-v1.json
g5 MISSING  backend/tests/fixtures/performance/solver/5k_10k_24h_v1/manifest.json
g5 MISSING  backend/tests/fixtures/performance/api/control-plane-v1.json
g5 MISSING  backend/tests/fixtures/performance/nowcast/30-cycle-v1.json
g5 PRESENT  frontend/performance/fixtures/twin-map-5k-10k-v1.json
g0 MISSING  backend/tests/fixtures/hydraulic/contracts/openapi-v1.json
g0 PRESENT  backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json
g0 PRESENT  frontend/e2e/fixtures/api-v1
```

G6 是当前唯一 fixture 齐备的 gate；G1–G4 与 G5 的其余 fixture 仍缺，对应 gate 只能记 NOT RUN。

## 6. 本轮未做 / 未运行

- 未修改 `docker-compose.yml`（B1 未解除前补 4 个 service 只会是空转或指向不存在模块）。
- 未创建 `backend/tests/fixtures/performance/**` 任何文件（B2）。
- 未运行 live G6：Docker daemon 不可用；`--mode live` 还需要 worker/API/SSE 真实就绪，而 B1 未解除。
- 未运行 G0–G5、AC-1～AC-8；未运行 `benchmark_hydraulic_api.py` / `benchmark_hydraulic_nowcast.py`（两者在本仓同样不存在）。
- 未改 `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md` 及其 status 字段；R0 状态不因本轮工件而变动。
- 未伪造任何 golden 数值、placeholder worker、skip/xfail，未放宽任何阈值。
