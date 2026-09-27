# Database bootstrap / PostGIS dual-profile implementation

> 状态：**PARTIAL / BLOCKED**。ownedPaths 内的镜像方案、extension-only init hook、验证脚本和测试已落盘；真实 fresh-volume migration 在 `0002_hydraulic_schema.py` 的 enum DDL 处失败，因此 **G1、PostGIS live 验证、portable live schema 验证均不得记为 PASS**。本轮未修改 Alembic/ORM 或根 `docker-compose.yml`。

## 1. 契约依据

- 源需求要求 PostGIS 与无 PostGIS 两条路径、TimescaleDB、Alembic 与 `docker/init-db.sql` 结果一致：`docs/hydraulic-twin/SOURCE_PRD.md:14-16,28-32`。
- 冻结补充要求 Alembic 是唯一可部署 schema writer，init SQL 只能做 extension bootstrap；两个 profile 都使用 PostgreSQL + TimescaleDB，profile 运行中不可切换：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:120-134`。
- 结果保留和 90 天 retention：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:253-258`。
- PostGIS 必须同时包含 PostgreSQL/Timescale/PostGIS，portable 仍须包含 Timescale 但不能依赖 PostGIS；init SQL 不得创建表/hypertable/seed：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2109-2127`。
- owned path 由 `database-bootstrap` 独占，根 `docker-compose.yml` 属于 `integration-config`：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2343-2351`。
- G1 要求两个 profile 分别 fresh volume 执行 migration/idempotent upgrade/downgrade/upgrade，并核对 extension、table、index、hypertable、retention：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2490-2496`。

## 2. 实际改动

### 2.1 `docker/init-db.sql`

进入本任务前，工作树中的该文件已被其他并行工作从 189 行业务 DDL/seed 收敛为 `uuid-ossp + timescaledb` 两行；本轮在该工作树基础上：

- 增加 `btree_gist`，使 init extension 集与 `0001_initial.py:20-24`、`0002_hydraulic_schema.py:117-127` 的实际 migration extension 集一致：`docker/init-db.sql:6-8`。
- 从镜像入口传入的 `hydraulic.spatial_backend` 读取不可变 profile，未设置时 fail-safe 默认 `portable`：`docker/init-db.sql:11-21`。
- `postgis` profile 仅以幂等方式 `CREATE EXTENSION IF NOT EXISTS "postgis"`：`docker/init-db.sql:23-24`。
- `portable` profile 若发现 PostGIS extension 已安装则拒绝启动，不静默切换：`docker/init-db.sql:25-32`。
- 没有新增业务表、hypertable、seed、用户或 migration SQL；Alembic 保持唯一业务 schema writer。

### 2.2 `docker/postgres/**`

- `docker/postgres/postgis.Dockerfile:1-13`
  - 基础镜像固定为 `timescale/timescaledb-ha:pg15@sha256:f49bf91b8966318e9bab4c44e4cc6c6533539dd44fcb049ee3334349dfcb233f`。
  - 镜像自身固定 `HYDRAULIC_IMAGE_SPATIAL_BACKEND=postgis`。
- `docker/postgres/portable.Dockerfile:1-13`
  - 基础镜像固定为 `timescale/timescaledb:2.17.2-pg15@sha256:d33b93c43b9db7a065f660847d4390d4a84ea1dd72395258aa8873f314de58c7`。
  - 不使用 `timescaledb-ha`，不包含/依赖 PostGIS。
- `docker/postgres/entrypoint-profile.sh:3-26`
  - 校验运行时 profile 与镜像烘焙 profile 完全一致；不一致以 exit 64 拒绝。
  - 将 profile 写入 `PGOPTIONS` 的 namespaced custom GUC 后 `exec docker-entrypoint.sh`。
- `docker/postgres/compose.yml:1-65`
  - 提供 `postgis` / `portable` 两个独立 Compose profile、独立 volume、必需密码变量和 healthcheck。
  - 该 owned fragment **没有 `container_name`**；测试在 `backend/tests/integration/test_database_schema.py:86-124` 固定这一约束。
- `docker/postgres/README.md:1-65` 记录 digest、build/fresh-volume/migration/verify 命令和无固定容器名的使用方式。

### 2.3 `scripts/verify_database.py`

新增只读验证器，公开接口包括：

- `validate_manifest(...)`：`scripts/verify_database.py:80-309`，验证：
  - PostgreSQL 身份和 server version；
  - 两 profile 必需的 `uuid-ossp`、`timescaledb`、`btree_gist`，以及 PostGIS 存在/不存在的排他性；
  - `alembic_version` 与 `hydraulic.schema_metadata` 的 head/profile；
  - `hydraulic.simulation_frames` 的 `frame_time` hypertable 和 1-day chunk；
  - 90-day retention policy；
  - PostGIS 下三张表的 generated typed geometry 和三个 GiST index；
  - portable 下 geometry column/GiST 完全不存在。
- `collect_manifest(...)`：`scripts/verify_database.py:313-407`，仅执行 catalog 查询。
- CLI：`scripts/verify_database.py:446-505`，支持 `--profile`、`--dsn`、`--expected-revision`、`--retention-days`、`--output`，输出 JSON 并对 DSN/password 脱敏。

### 2.4 `backend/tests/integration/test_database_schema.py`

新增五个行为测试：

- init 与 0001/0002 的 extension 集一致且 init 不写业务 schema：`:68-83`。
- 两个镜像 digest 固定、profile 不可切换、owned Compose 无固定容器名：`:86-124`。
- verifier 接受完整 PostGIS/portable manifest：`:188-200`。
- verifier 对 extension、profile、hypertable、GiST 漂移 fail closed：`:203-232`。
- 设置 `DATABASE_TEST_DSN_POSTGIS` / `DATABASE_TEST_DSN_PORTABLE` 时运行真实双 profile manifest 验证：`:235-255`。

## 3. 本轮实际执行的命令与结果

### 3.1 通过的静态/单元检查

1. Compose 两 profile 配置：

```text
POSTGRES_PASSWORD=local-test-only docker compose -f C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/docker/postgres/compose.yml --profile postgis config --quiet
POSTGRES_PASSWORD=local-test-only docker compose -f C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/docker/postgres/compose.yml --profile portable config --quiet
```

两条命令均 exit 0、无输出。

2. verifier Python 语法和 CLI：

```text
python -m py_compile C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/scripts/verify_database.py
python C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/scripts/verify_database.py --help
```

exit 0。

3. entrypoint shell 语法：

```text
bash -n C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/docker/postgres/entrypoint-profile.sh
```

exit 0。

4. owned diff whitespace：

```text
git -C C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage diff --check -- docker/init-db.sql docker/postgres scripts/verify_database.py backend/tests/integration/test_database_schema.py
```

exit 0、无输出。

5. TDD/目标测试：

```text
python -m pytest -q C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/tests/integration/test_database_schema.py::test_bootstrap_extensions_match_migration_extensions_and_create_no_schema
# 1 passed

python -m pytest -q C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/tests/integration/test_database_schema.py::test_database_images_are_digest_pinned_and_keep_profiles_immutable
# 1 passed

python -m pytest -q C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/tests/integration/test_database_schema.py -k verifier
# 2 passed, 2 deselected
```

此前同文件的目标测试在实现前分别真实失败：init 测试报缺少 `btree_gist/postgis`，镜像测试报文件不存在，verifier 测试报 `scripts/verify_database.py` 不存在；上述为对应 green 结果。可选 live 参数化测试本轮没有在成功 migration 后执行，不能计为 live PASS。

### 3.2 镜像与 extension bootstrap

1. 官方 registry 解析失败：

```text
docker buildx imagetools inspect timescale/timescaledb-ha:pg15
# exit 1: registry-1.docker.io:443 connect timeout

docker buildx imagetools inspect timescale/timescaledb:2.17.2-pg15
# exit 1: registry-1.docker.io:443 connect timeout
```

2. 使用同内容镜像代理解析出实际 multi-arch index digest：

```text
docker buildx imagetools inspect docker.1ms.run/timescale/timescaledb-ha:pg15
# Digest: sha256:f49bf91b8966318e9bab4c44e4cc6c6533539dd44fcb049ee3334349dfcb233f

docker buildx imagetools inspect docker.1ms.run/timescale/timescaledb:2.17.2-pg15
# Digest: sha256:d33b93c43b9db7a065f660847d4390d4a84ea1dd72395258aa8873f314de58c7
```

3. portable 镜像真实构建：

```text
POSTGRES_PASSWORD=local-test-only PORTABLE_BASE_IMAGE=docker.1ms.run/timescale/timescaledb:2.17.2-pg15@sha256:d33b93c43b9db7a065f660847d4390d4a84ea1dd72395258aa8873f314de58c7 docker compose -f C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/docker/postgres/compose.yml --profile portable build --pull postgres-portable
# exit 0; smart-city-drainage-postgres-portable:local Built
```

4. portable fresh volume 启动：

```text
POSTGRES_PASSWORD=local-test-only docker compose -p scd-db-portable-test -f C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/docker/postgres/compose.yml --profile portable up -d --wait postgres-portable
# exit 0; Container ... Healthy
```

5. 查询 init 后 extension：

```text
docker compose -p scd-db-portable-test -f .../compose.yml --profile portable exec -T postgres-portable psql -v ON_ERROR_STOP=1 -U drainage_admin -d smart_drainage -Atc "SELECT extname || '=' || extversion FROM pg_extension WHERE extname IN ('uuid-ossp','timescaledb','btree_gist','postgis') ORDER BY extname; SELECT 'profile=' || current_setting('hydraulic.spatial_backend');"
```

第一段真实输出：

```text
btree_gist=1.7
timescaledb=2.17.2
uuid-ossp=1.1
```

没有 `postgis`，portable extension 集正确。第二段报 `unrecognized configuration parameter "hydraulic.spatial_backend"`，原因是该查询通过 `docker compose exec` 新建客户端进程，不会继承 entrypoint 仅向 postmaster 进程导出的 `PGOPTIONS`；这不能作为 init profile 查询通过证据。随后测试容器和专用 volume 已用同一 project 名安全删除。

6. PostGIS 镜像构建 **未完成**：

```text
POSTGRES_PASSWORD=local-test-only POSTGIS_BASE_IMAGE=docker.1ms.run/timescale/timescaledb-ha:pg15@sha256:f49bf91b8966318e9bab4c44e4cc6c6533539dd44fcb049ee3334349dfcb233f docker compose -f .../compose.yml --profile postgis build --pull postgres-postgis
```

镜像层约 896.16 MB；镜像代理在约 13 分钟仅下载到 31.46 MB，无法形成可运行镜像。为避免遗留后台下载，已终止本轮启动的 compose/build 进程；`docker image inspect smart-city-drainage-postgres-postgis:local` 返回 `No such image`。因此 PostGIS build/live 均是 **NOT RUN/NOT PASS**，不是测试通过。

### 3.3 真实 migration 阻断

在上述 fresh portable 库上执行：

```text
cd C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend && HYDRAULIC_SPATIAL_BACKEND=portable DATABASE_URL='postgresql+asyncpg://drainage_admin:***@127.0.0.1:55433/smart_drainage' DATABASE_URL_SYNC='postgresql://drainage_admin:***@127.0.0.1:55433/smart_drainage' python -m alembic upgrade head
```

真实结果：exit 1。`0001_initial` 已运行，进入 `0002_hydraulic_schema` 后在 `backend/alembic/versions/0002_hydraulic_schema.py:129-133` 失败，精确 SQL 为：

```sql
CREATE TYPE hydraulic.network_status AS ENUM (draft, active, archived)
```

PostgreSQL/asyncpg 错误：

```text
asyncpg.exceptions.PostgresSyntaxError: syntax error at or near "draft"
sqlalchemy.exc.ProgrammingError: ... [SQL: CREATE TYPE hydraulic.network_status AS ENUM (draft, active, archived)]
```

`upgrade()` 在 `0002_hydraulic_schema.py:2489-2504` 中先调用 `_create_extensions()`，随后无条件调用 `_create_enums()`，PostGIS geometry 分支在更后面。因此该错误与 profile 无关，**同时阻断 portable 和 PostGIS 的 fresh-volume upgrade**；本轮没有伪称 PostGIS 也已实跑失败，但代码路径证明它会在同一 enum 语句失败。

## 4. 必须由 hydraulic-schema owner 修复的内容

`0002_hydraulic_schema.py:27-78,129-133` 的 12 个 enum 标签当前被拼成未引用 SQL literal。schema owner 需确保每个 label 都作为合法 PostgreSQL enum value 创建，并验证所有允许值/禁止值，而不只是修第一条：

1. `network_status`: `draft|active|archived`
2. `network_node_kind`: `junction|outfall|manhole|storage|other`
3. `rainfall_scenario_kind`: `design|custom|synthetic`
4. `simulation_status`: `queued|leased|running|retry_wait|cancelling|cancelled|completed|failed|timed_out`
5. `simulation_attempt_status`: `running|discarded|published|cleanup_pending`
6. `frame_quality_status`: `verified|warning|failed`
7. `calibration_status`: 与 `simulation_status` 相同
8. `forecast_alert_type`: `flood_forecast|pipe_surcharge`
9. `forecast_alert_status`: `open|acknowledged|resolved|expired`
10. `forecast_alert_phase`: `predicted|observed`
11. `confidence_method`: `ensemble_exceedance_frequency|calibrated_residual|conformal|calibrated_regression`
12. `forecast_transition_action`: `create|acknowledge|observe|resolve|expire|refresh|escalate`

修复 owner 合并后，两个 profile 必须分别从空 volume 重跑：

```text
upgrade head -> current -> repeat upgrade -> clone downgrade -> upgrade head
```

并重新执行：

- `init-db.sql` 至少两次的同库 psql 重放，验证所有 `CREATE EXTENSION IF NOT EXISTS` 幂等；
- extension 集：portable=`uuid-ossp+timescaledb+btree_gist` 且无 PostGIS；postgis 再含 PostGIS；
- `alembic_version` 和 `hydraulic.schema_metadata` 均为 `0002_hydraulic_schema` 且 profile 匹配；
- `simulation_frames` 为 `frame_time` hypertable，retention=90 days；
- PostGIS 三张表存在 generated `Point/LineString/Geometry`, SRID 4326 和三个 GiST index；portable 三表均无 geometry/GiST；
- `scripts/verify_database.py --profile postgis|portable` 对真实数据库 exit 0。

## 5. 剩余风险与未完成项

1. **根 Compose 未集成且仍含固定容器名。** owned fragment 已移除 `container_name`，但根 `docker-compose.yml:12,52,69,96,139,160,172,188` 仍有 `scn-*`，并且 `docker-compose.yml:11` 仍引用 `timescale/timescaledb:latest-pg15`。该文件不在本任务 ownedPaths，必须由 `integration-config` owner 删除固定名、选择上述 digest-pinned image，并给 PostGIS 路径接入 profile-aware entrypoint。
2. **G1/G6/AC-8 未通过。** 真实 upgrade 在 0002 enum 处失败；PostGIS 镜像未构建；两 profile 的 live verifier 未成功运行。
3. **init 重复执行尚未 live 证明。** 本轮只完成 `IF NOT EXISTS` 静态测试和 portable fresh-volume 首次执行；未在同一数据库第二次执行 `psql -f init-db.sql`。
4. **canonical registry digest 未独立确认。** 官方 registry 连接超时；digest 来自可达镜像代理的 multi-arch OCI index。默认 Dockerfile 仍固定该 digest，集成环境应通过官方 registry 或可信内部 mirror 复核。
5. **`btree_gist` 是为等价于当前 migration 而加入的 extension。** 它不创建业务 schema，但实施契约 `IMPLEMENTATION_CONTRACT.md:2126` 的括号示例未显式列出该 extension；schema owner/contract owner 应确认“只 CREATE EXTENSION”允许该 migration-required contrib extension。
6. 未提交、未推送；未修改 migration、ORM、根 Compose 或其他非 owned 文件。
