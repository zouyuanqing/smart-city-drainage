# Compose worker 与就绪探针实施记录

日期：2026-09-24  
模块：`compose-worker`  
状态：**平台配置已实现并通过静态/配置/探针测试；真实 simulation worker 运行与完整镜像构建仍被明确依赖或网络条件阻断。**

## 1. 需求与契约依据

- 源 PRD 要求仿真 worker、健康检查和一键 Compose，验收项要求全新 Compose 可复现：`docs/hydraulic-twin/SOURCE_PRD.md:28`、`:32`。
- 补充契约冻结了 20 MiB 输入、2 GiB workspace、64 MiB frame、8 GiB simulation output，以及流式帧持久化：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:157-162`。
- 实施契约要求目标拓扑包含 `migrate`、`simulation-worker`、API 和 Nginx；migration 成功后 backend/worker 才能启动：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2083-2105`。
- 实施契约区分 API `/health` 与 `/readyz`，要求 worker 提供 `/healthz` 和 `/readyz`，后者必须反映 DB/claim/heartbeat 能力：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2129-2134`。
- 实施契约要求 forecast SSE 关闭 proxy buffering/cache、保留 Authorization，并把超时设为 1 小时：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2136-2150`。
- 文件所有权把 `docker-compose.yml` 归 integration-config，把 `docker/backend/**`、`docker/nginx/**`、`docker/frontend/**` 归 compose-worker；真实 `backend/app/workers/simulation_worker.py` 归 SIM-WORKER：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2343-2353`、`:2300`。
- 实施前读取到应用启动时直接打开 `logs/app.log`：`backend/app/main.py:46-73`；已有 `/readyz` 仅调用 schema readiness，`/health` 无条件 200：`backend/app/main.py:304-316`；该 schema 检查当前只核对 Alembic head/profile：`backend/app/core/database.py:151-206`。

## 2. 实际改动

### 2.1 一键 Compose 与 worker 服务

- `docker-compose.yml:30-54` 保留 one-shot `migrate`，并让 backend 等待 `service_completed_successfully`。
- frontend build 使用 `additional_contexts.scn_frontend_deploy=./docker/frontend`，使 build context 内的 Dockerfile能读取 owned path 中的两份 Nginx 配置，而不是错误地从 `frontend/` 源码目录复制不存在的文件。
- `docker-compose.yml:102-170` 为 API 增加 readiness healthcheck、日志卷、非 root、只读根文件系统、cap drop、`no-new-privileges`、CPU/内存/PID 限制。
- `docker-compose.yml:175-238` 新增真实业务入口服务：

  ```text
  image: smart-city-drainage-backend:local
  command: ["python", "-m", "app.workers.simulation_worker"]
  user: "10001:10001"
  read_only: true
  mem_limit: 4g
  cpus: 2.0
  pids_limit: 512
  ```

  worker 只在 `migrate` 成功后启动；Nginx 在 `docker-compose.yml:266-269` 同时等待 API 和 worker healthy，缺失 worker 时不会把整个入口伪报为 ready。
- worker 的持久目录为：

  ```text
  HYDRAULIC_SANDBOX_ROOT=/var/lib/smart-drainage/sandbox
  HYDRAULIC_EXPORT_ROOT=/var/lib/smart-drainage/results
  simulation_sandbox -> /var/lib/smart-drainage/sandbox
  simulation_results -> /var/lib/smart-drainage/results
  ```

  对应 `docker-compose.yml:198-220` 和 `docker-compose.yml:358-360`。`simulation_results` 只保存导出/派生产物；PostgreSQL/Timescale 仍是完成结果的冻结事实源，不得把该卷当业务事实源。
- 一键生产启动命令固定为：

  ```bash
  docker compose --profile production up --build
  ```

  当前真实 worker 模块尚未落地，所以该命令在现状下会被 readiness 依赖阻断；这是显式 blocked dependency，不是成功结果。

### 2.2 给 SIM-WORKER 的精确 typed handoff

后续 `backend/app/workers/simulation_worker.py` 必须满足以下容器契约：

1. **入口**：`python -m app.workers.simulation_worker`；不得用 shell sleep、空转或只返回 200 的 stub。
2. **监听**：读取 `SIMULATION_WORKER_HEALTH_HOST=0.0.0.0`、`SIMULATION_WORKER_HEALTH_PORT=8010`。
3. **liveness**：`GET /healthz` 在进程事件循环和健康服务器可响应时返回 200；不得要求有排队任务。
4. **readiness**：`GET /readyz` 只有在 DB 可连接、Alembic head/profile 匹配、worker 已注册、能 claim 并刷新 heartbeat 时返回 200；任一失败返回 503。
5. **任务语义**：只 claim `general` slot；不得借用 nowcast slot；遵守 600 s attempt、30 s lease、10 s heartbeat、1 s cancel poll、最多 3 attempts 和 `[10s,60s]` backoff。环境值见 `docker-compose.yml:198-213`。
6. **容量/存储**：输入 20 MiB、workspace 2 GiB、frame 64 MiB、simulation output 8 GiB、retention 90 天；只能写 sandbox/results/logs 三个挂载卷和 `/tmp`。
7. **信号/退出**：收到 SIGTERM 后停止 claim，终止 solver 进程树，执行 cleanup，并在 Compose 的 30 秒 `stop_grace_period` 内退出；异常退出必须非零，禁止伪造完成状态。
8. **ready 失败原因**：响应至少应能区分 DB 不可用、schema/head/profile 不匹配、workspace/result 目录不可写、worker registration/heartbeat 不可用；日志不得输出密码、完整 DB URL 或 sandbox 绝对路径。

本轮用以下命令确认真实入口当前不存在：

```text
$ if [ -f backend/app/workers/simulation_worker.py ]; then echo PRESENT; else echo MISSING_BLOCKED; fi
MISSING_BLOCKED
```

因此本模块只交付平台接线，没有声称 DB claim、heartbeat、general slot、任务状态更新或 cleanup 已实现。

### 2.3 liveness、readiness 与健康脚本

- 新增无第三方依赖的探针 `docker/backend/healthcheck.py:26-49`：只接受 HTTP 2xx；连接错误、超时、HTTP 503 均非零退出。
- API 镜像级 healthcheck 使用 `/health`，Compose 服务 healthcheck 使用 `/readyz`：`docker/backend/Dockerfile.cpu:36-37`、`docker-compose.yml:163-169`。
- worker Compose healthcheck 固定探测 `http://127.0.0.1:8010/readyz`：`docker-compose.yml:229-235`。
- 外层 Nginx 暴露 `/nginx-health` 并代理 `/health`、`/readyz`：`docker/nginx/conf.d/default.conf:90-108`。
- 前端镜像以非 root 用户在 8080 运行，并有自身 healthcheck：`docker/frontend/Dockerfile:10-17`。
- Redis 探针修复为使用 `REDISCLI_AUTH`，并让后端 Redis URL 与 `REDIS_PASSWORD` 同步：`docker-compose.yml:57-75`、`:131`。

### 2.4 非 root、日志目录和资源限制

- CPU/GPU backend 镜像均创建 UID/GID 10001、预建并授权 `/app/logs`、media、models，然后 `USER 10001:10001`；CPU 证据见 `docker/backend/Dockerfile.cpu:15-32`。
- API、worker、Nginx、frontend 均配置非 root、只读根文件系统、`cap_drop: ALL` 和 `no-new-privileges:true`。
- worker 限制为 4 GiB、2 CPU、512 PID；API 为 2 GiB、2 CPU、256 PID；Nginx/frontend 各为 256 MiB、1 CPU、100 PID。
- backend 和 worker 分别使用 `backend_logs`、`worker_logs` 命名卷，避免 `RotatingFileHandler` 因镜像内目录不存在或 root ownership 失败。

### 2.5 数据库 URL、Redis 与 SSE 部署修复

- Compose 内 asyncpg URL 将密码中的 `@` 和 `!` 编码为 `%40`、`%21`；migrate/backend/worker 使用同一安全 URL。回归测试位于 `docker/backend/tests/test_deployment_contract.py:91-99`。
- 外层和前端 Nginx 都为 `/api/forecast/events` 设置精确 location、Authorization 透传、`proxy_buffering off`、`proxy_cache off`、`gzip off` 和 `X-Accel-Buffering no`：`docker/nginx/conf.d/default.conf:29-44`、`docker/frontend/nginx-default.conf:31-46`。
- legacy `/api/sse/` 同样关闭 buffering；SSE access log 关闭。通用 access log 使用 `$uri` 而不是 `$request`，不记录 query string；Authorization 从未写入 log format。
- 两套 Nginx 改用 8080 非特权端口、仅 `/tmp` 可写、pid/temp/cache 均位于 `/tmp`，适配 UID 101 和只读根文件系统。

## 3. 本轮实际执行的验证

### 3.1 通过

1. Compose 全量配置：

   ```text
   $ docker compose config --quiet
   (no output; exit 0)
   $ docker compose --profile production config --quiet
   (no output; exit 0)
   ```

2. 健康脚本与部署静态回归：

   ```text
   $ python -m unittest discover -s docker/backend/tests -p 'test_*.py' -v
   Ran 10 tests in 0.815s
   OK
   ```

   10 项覆盖 HTTP 200/503、DB URL 编码、worker 入口/卷、worker readiness、非 root/资源限制、日志目录、frontend additional build context、Redis auth、两套 Nginx SSE 和非特权端口。

3. backend Dockerfile build-definition 检查：

   ```text
   $ docker build --check -f docker/backend/Dockerfile.cpu backend
   Check complete, no warnings found.
   ```

4. 两套 Nginx 在 UID 101、只读根文件系统、`/tmp` tmpfs、模拟 backend DNS 下配置检查：

   ```text
   outer:  nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
           nginx: configuration file /etc/nginx/nginx.conf test is successful
   frontend: nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
             nginx: configuration file /etc/nginx/nginx.conf test is successful
   ```

5. 两套 Nginx 真实容器启动并探测自身 health endpoint，输出均为：

   ```text
   ok
   ```

6. owned diff whitespace 检查：

   ```text
   $ git diff --check -- docker-compose.yml docker/backend docker/nginx docker/frontend
   (no output; exit 0)
   ```

### 3.2 未通过或未完成

1. 完整组合镜像构建命令：

   ```text
   $ docker compose build backend simulation-worker frontend
   exit 1
   target frontend: failed to solve
   failed to fetch anonymous token from https://auth.docker.io/...
   ```

   失败点是拉取 `node:18.20.8-alpine` metadata 时 Docker Hub 连接超时；并发 backend build 随该命令被取消。单独重试 backend build时，Debian apt 下载 133 MB 长时间只有约 78.9–224 kB/s，运行超过本轮等待窗口后停止，未得到最终镜像。因此**镜像构建未记为 PASS**。
2. frontend Dockerfile 的 `docker build --check` 同样因无法获取 `node:18.20.8-alpine` token 而 exit 1；这不是 Dockerfile parser 错误，但本轮无法完成该项。
3. 真实 worker 容器没有启动测试：业务模块 `backend/app/workers/simulation_worker.py` 当前不存在。Compose 已正确配置为失败可见，不允许以 stub 伪造健康。
4. 没有执行完整 `docker compose ... up`：backend/frontend 新镜像未在本轮构建完成，且真实 worker 业务模块缺失；不能声称一键运行通过。

## 4. 风险与后续责任

- **阻断依赖**：SIM-WORKER 必须按第 2.2 节实现入口、readiness、claim/heartbeat、general slot、cleanup 和退出语义；之后才能运行 worker 容器与完整 Compose。
- **readiness 完整性**：应用当前 `/readyz` 只验证 Alembic head/profile（`backend/app/core/database.py:151-206`），尚未覆盖冻结契约要求的 Timescale/PostGIS、outbox、worker registration 等。Compose 入口通过同时等待 backend 和 simulation-worker healthy 做了平台层补强，但直接调用 API `/readyz` 仍不能作为完整 AC-8 证据；需由 API/schema owner 补齐。
- **构建环境**：Docker Hub/Debian 网络不稳定是本轮完整构建未完成的直接原因；应在网络可用环境重跑 `docker compose build backend frontend` 和 `docker compose --profile production up --build`。
- **生产秘密与端口**：Compose 仍保留开发演示凭据、宿主机数据库/Redis/Influx 端口，并使用 `timescale/timescaledb:latest-pg15`；不满足冻结契约的 secret-file、无默认凭据、digest pin 和默认不映射端口要求。本轮没有伪称 production-hardened。
- **init SQL**：`docker/init-db.sql` 仍由独立 database-bootstrap owner 管理；本模块未越权修改。完整部署前仍需由该 owner 验证其只做 extension bootstrap，且不与 Alembic 竞争业务 schema。
- **结果卷语义**：`simulation_results` 是 worker export/派生文件卷，不是 simulation 完成事实源；DB frame/summary/quality/publish 指针必须继续按冻结契约持久化。
- **测试范围**：本轮只运行部署配置、探针、镜像定义和 Nginx 容器级检查；没有运行 worker 业务测试、迁移、性能、完整 E2E 或 AC-1～AC-8。
