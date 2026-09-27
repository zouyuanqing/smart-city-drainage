# Compose worker 与就绪探针审查

- 审查日期：2026-09-25
- 审查对象：`docker-compose.yml`、`docker/backend/**`、`docker/nginx/**`、`docker/frontend/**` 及其真实依赖
- 冻结依据：`docs/hydraulic-twin/SOURCE_PRD.md:29,33`、`docs/hydraulic-twin/PRD_SUPPLEMENT.md:158-163`、`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2083-2163`
- 结论：**不批准（blocked）**。发现 6 个高风险问题和 2 个中风险问题；其中 worker 入口不存在、结果卷不可写、API readiness 过宽，均会阻止真实一键 Compose 通过。

## 发现

### H-01（高）：`simulation-worker` 指向不存在的 Python 模块，worker 永远不会 ready

- **位置**：`smart-city-drainage/docker-compose.yml:173-237`，尤其是 `:179`、`:227-232`。
- **问题**：Compose 声明了真实入口 `python -m app.workers.simulation_worker`，但真实 `backend/app/workers/` 目录只有 `__init__.py` 和 `inference_worker.py`，没有 `simulation_worker.py`。因此服务会立即以模块不存在退出；Nginx 又在 `docker-compose.yml:264-268` 等待该服务 healthy，默认入口无法启动。
- **契约影响**：冻结契约要求 simulation worker 做 DB claim、general slot、registration/heartbeat，并提供 `/healthz` 与 `/readyz`（`IMPLEMENTATION_CONTRACT.md:2094`、`2134`）；当前没有这些实现。
- **证据**：
  - 真实目录检查输出：`__init__.py`、`inference_worker.py`，随后为 `SIMULATION_WORKER_MISSING`。
  - 直接执行配置中的入口：`cd backend && python -m app.workers.simulation_worker`，退出码 1，输出 `No module named app.workers.simulation_worker`。
- **建议**：先交付真实 worker 模块和健康服务器/DB claim/heartbeat/退出清理实现，再启用 service；增加容器级启动 smoke test，而不要仅检查 Compose 字符串。

### H-02（高）：非 root worker 的全新 sandbox/results 命名卷不可写

- **位置**：`smart-city-drainage/docker-compose.yml:180-181,217-220`；`docker/backend/Dockerfile.cpu:25-31`；`docker/backend/Dockerfile:33-37`。
- **问题**：worker 以 UID/GID `10001:10001` 运行，但两个 Dockerfile 只预建并授权 `/app/logs`、`/app/media`、`/app/models`，没有预建 `/var/lib/smart-drainage/sandbox` 和 `/var/lib/smart-drainage/results`。全新 named volume 挂载到这些不存在于镜像的路径时由 root 创建，worker 无法写入。
- **证据**：使用相同 fresh named-volume 模式、UID 10001 的诊断输出为：`uid=10001`、`dir=0:0:755`、`WRITE_DENIED`。这与 Compose 的两个空 named volume 和上述 Dockerfile 路径组合一致。
- **影响**：即使补上 worker 业务模块，干净机器首次启动也会在创建 workspace/导出文件时失败；已有 root-owned volume 还会持续失败。
- **建议**：在两个镜像中预创建目录并 `chown 10001:10001`，或用受控的 root init/entrypoint 修复 volume ownership 后再降权；为 fresh volume 和已有 volume 都增加非 root 写入测试。

### H-03（高）：API `/readyz` 只验证 Alembic head/profile，会产生假就绪

- **位置**：`smart-city-drainage/backend/app/main.py:305-311`；`backend/app/core/database.py:152-207`。
- **问题**：`/readyz` 唯一调用 `check_hydraulic_schema_readiness()`。该函数只查询 `alembic_version` 和 `hydraulic.schema_metadata`，然后比较 revision/profile；没有检查 Timescale extension/hypertable、按 profile 的 PostGIS、network-job-worker registration、artifact config/hash、outbox relay heartbeat、simulation/nowcast registration 和 migration state。
- **契约影响**：`IMPLEMENTATION_CONTRACT.md:2129-2134` 明确要求这些依赖任一失败都返回 503。当前 DB 连接是隐式发生的，但依赖集合远不完整。
- **影响**：Compose backend healthcheck（`docker-compose.yml:161-166`）和 Nginx `/readyz` 代理（`docker/nginx/conf.d/default.conf:97-100`）可能把缺少 worker/扩展/配置的系统标为 ready。
- **建议**：实现统一的 fail-closed readiness aggregator，覆盖契约列出的每项依赖并返回可诊断但不泄密的 503 原因；为每类失败增加负向测试。

### H-04（高）：根 Compose 的 `postgis` profile 没有连接到 PostGIS 数据库

- **位置**：`smart-city-drainage/docker-compose.yml:8-19,45-53,122-125`；`docker/init-db.sql:9-31`。
- **问题**：根 Compose 始终启动 `timescale/timescaledb:latest-pg15`，没有按 `HYDRAULIC_SPATIAL_BACKEND` 切换数据库镜像，也没有向 postgres 注入 profile/PGOPTIONS。应用却允许通过环境变量选择 `postgis`。`init-db.sql` 在没有数据库设置时默认按 `portable` bootstrap，因此“设置应用为 postgis”并不会得到 PostGIS 数据库。
- **契约影响**：`IMPLEMENTATION_CONTRACT.md:2111-2112` 要求 postgis profile 使用同时含 PostgreSQL/Timescale/PostGIS 的镜像，portable profile 不依赖 PostGIS。项目虽有 `docker/postgres/compose.yml:3-55` 的独立 profile 实现，但根 Compose 没有引用它。
- **证据**：`HYDRAULIC_SPATIAL_BACKEND=postgis docker compose --profile production config --format json` 的实际输出为：`postgres_image=timescale/timescaledb:latest-pg15`、`postgres_profile_env=None`、`backend_profile_env=postgis`、`postgres_pgoptions=None`。
- **建议**：把 profile-specific postgres service 纳入根编排并传递一致的 profile/PGOPTIONS，分别验证新 volume 的 portable 与 postgis bootstrap；不要只验证 YAML 能解析。

### H-05（高）：生产配置提交了仓库固定凭据且没有启用 production fail-closed

- **位置**：`smart-city-drainage/docker-compose.yml:11-15,60-62,81-86,122-137`。
- **问题**：Postgres 密码、Redis fallback 密码、Influx token 和 JWT secret 都直接写入 Compose；backend 没有设置 `DEPLOYMENT_ENVIRONMENT=production`，而 JWT allowlist 固定为 HS256。Settings 的 production 校验（`backend/app/core/config.py:462-497`）因此不会在启动时拒绝这些默认值，也没有要求生产 RS256 key material。
- **契约影响**：`IMPLEMENTATION_CONTRACT.md:2079` 和 `2161` 要求生产拒绝仓库固定 secret/credential，使用 env/secret file。
- **证据**：渲染 production Compose 的检查显示 `deployment_env=None`，且四类 secret 均解析为 Compose 字面量/fallback，而不是必填 secret。
- **建议**：删除仓库默认凭据，使用必填 env/secret file；显式设置 production 并配置符合契约的算法/key，提交前增加“缺 secret/默认 secret 必须启动失败”的测试。

### H-06（高）：镜像未 digest 固定且数据库/Redis/Influx 默认暴露到宿主机

- **位置**：`smart-city-drainage/docker-compose.yml:8,16,62-64,77-89,241`；`docker/backend/Dockerfile:1`、`docker/backend/Dockerfile.cpu:1`、`docker/frontend/Dockerfile:2,9`。
- **问题**：数据库使用 `latest-pg15`，Redis/Nginx 使用浮动 tag，backend/frontend 基础镜像也未固定 digest；Postgres、Redis、Influx 默认分别映射 `5432`、`6379`、`8086`。
- **契约影响**：`IMPLEMENTATION_CONTRACT.md:2111` 禁止 `latest`；`2162-2163` 要求镜像 digest 固定、数据库类服务默认不映射宿主机。
- **证据**：production Compose 渲染检查输出包含 `timescale/timescaledb:latest-pg15`、`redis:redis:7-alpine`、`influxdb:2.7-alpine`，以及三个公开端口；backend Dockerfile 和 frontend Dockerfile 的基础镜像也没有 digest。
- **建议**：所有生产镜像使用 digest；将宿主端口移到显式 demo profile 或至少绑定 loopback，并在部署清单记录镜像 digest。

### M-01（中）：部署契约测试是字符串存在性测试，已出现“绿灯但入口不存在”

- **位置**：`smart-city-drainage/docker/backend/tests/test_deployment_contract.py:103-121`。
- **问题**：`test_simulation_worker_is_a_real_module_entrypoint` 只断言 Compose 文本包含命令和卷名，没有检查 Python 模块、导入、worker HTTP 端口、DB claim/heartbeat 或 fresh-volume 权限；因此模块缺失和卷不可写仍能通过。
- **证据**：定向测试 10/10 通过，但同一审查中真实入口执行退出 1，且 fresh volume 诊断为 `WRITE_DENIED`。
- **建议**：增加 `find_spec`/容器启动 smoke、真实 worker `/healthz`/`/readyz` 轮询、DB readiness 负向测试和 fresh-volume 非 root 写入测试；静态 YAML 断言只能作为补充。

### M-02（中）：CPU 镜像安装 CUDA wheel，违背无 GPU 部署的轻量目标并放大构建失败面

- **位置**：`smart-city-drainage/docker/backend/Dockerfile.cpu:17-21`；`backend/requirements-py311.lock:1808-1862,3301-3388`。
- **问题**：标记为 CPU 模式的镜像安装 `requirements-py311.lock`，该 lock 在 Linux x86_64 上包含 `nvidia-cublas-cu12`、多组 CUDA 包、`torch` 和 `triton`。这会使 CPU 镜像下载/携带大量 GPU 依赖；冻结技术约束明确不要求 GPU（`SOURCE_PRD.md:15`）。
- **证据**：本轮 `docker compose build backend simulation-worker frontend` 的 backend 安装阶段开始下载 `nvidia-cublas-cu12==12.1.3.1 (410.6 MB)`，随后因 frontend registry token 网络错误被取消。
- **建议**：为 CPU 镜像使用独立、hash-locked 的 CPU-only 依赖（CPU torch/onnxruntime），并在 CI 检查 CPU 镜像不含 CUDA wheel；不要用 GPU lock 改名为 CPU lock。

## 已执行检查

以下检查均在本轮实际执行；未执行项单独列出。

| 检查 | 命令 | 结果 |
|---|---|---|
| Compose 默认/生产解析 | `cd smart-city-drainage && docker compose config --quiet && docker compose --profile production config --quiet` | **通过**，两条命令退出 0、无输出 |
| 定向部署契约/健康脚本 | `cd smart-city-drainage && python -m unittest docker/backend/tests/test_deployment_contract.py -v` | **通过**，`Ran 10 tests ... OK`；但见 M-01，属于静态绿灯 |
| 配置 worker 入口 | `cd smart-city-drainage/backend && python -m app.workers.simulation_worker` | **失败**，退出 1：`No module named app.workers.simulation_worker` |
| fresh named volume 非 root 权限 | `docker run --rm --user 10001:10001 -v <临时卷>:/var/lib/smart-drainage/sandbox nginx:alpine ...` | **失败**，输出 `dir=0:0:755`、`WRITE_DENIED`；临时卷已清理 |
| PostGIS profile 渲染 | `HYDRAULIC_SPATIAL_BACKEND=postgis docker compose --profile production config --format json | python ...` | **失败（语义）**，postgres 仍为 `latest-pg15` 且没有 profile/PGOPTIONS |
| production 镜像/端口/secret 渲染 | `docker compose --profile production config --format json | python ...` | **失败（契约）**，输出浮动 tag、三个宿主机端口、`deployment_env=None` 和字面量/fallback secret |
| Compose 镜像构建 | `cd smart-city-drainage && docker compose build backend simulation-worker frontend` | **未通过**，退出 1；frontend 拉取 `node:18.20.8-alpine` 时 Docker Hub anonymous token 连接失败，backend 构建被取消；不能记为镜像构建 PASS |
| owned diff whitespace | `git diff --check -- docker-compose.yml docker/backend/Dockerfile docker/backend/Dockerfile.cpu docker/backend/healthcheck.py docker/backend/tests/test_deployment_contract.py docker/frontend/Dockerfile docker/frontend/nginx.conf docker/frontend/nginx-default.conf docker/nginx/nginx.conf docker/nginx/conf.d/default.conf docs/hydraulic-twin/implementation/compose-worker.md` | **通过**，无输出、退出 0 |

## 未执行/不能宣称通过

- 未执行完整 `docker compose ... up`：真实 worker 模块缺失，且镜像构建未完成。
- 未执行真实 PostgreSQL/Timescale/PostGIS migration、DB claim/heartbeat、SSE 端到端流或 worker cleanup 测试。
- 未执行 GPU Dockerfile 的完整镜像构建；本轮只实际检查了 CPU/Compose 构建路径。

## 正向观察

- asyncpg Compose URL 已对密码中的 `@` 和 `!` 做百分号编码（`docker-compose.yml:45-47,123-125,193-195`），对应静态回归通过。
- `healthcheck.py:25-46` 对 HTTP 非 2xx、连接错误和超时返回非零，200/503 定向测试通过。
- 两套 Nginx 的 forecast SSE location 均关闭 buffering/cache/gzip、透传 Authorization 并设置 1 小时读超时（`docker/nginx/conf.d/default.conf:27-43`、`docker/frontend/nginx-default.conf:28-45`）。
- backend 日志目录和常见运行时目录已在镜像中预建并授权，API/worker/Nginx/frontend 的非 root、只读根、资源限制配置在 Compose 层存在；这些优点不能抵消上述运行时阻断。

**最终判定：BLOCKED / NOT APPROVED。**
