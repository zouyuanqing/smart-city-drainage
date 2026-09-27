# PostGIS 与初始化双路径独立审查

- **模块**：`database-bootstrap`
- **审查方式**：只读复核冻结契约、ownedPaths 实现、跨文件 Alembic/Compose 集成，并运行定向诊断；未修改实现代码、迁移或配置。
- **结论**：**BLOCKED / `approved=false`**。存在 6 项高风险阻断或集成阻断，以及 3 项中风险验证/可复现性问题；不能宣称双 profile 已可复现部署或 G1 已通过。

## 1. 契约与范围

本次对照的冻结要求是：

- Alembic 是唯一可部署 schema writer，`docker/init-db.sql` 只能做 extension bootstrap：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:121-126`、`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2125-2127`。
- 数据库从创建到退役只能使用一个 `postgis|portable` profile；profile 缺失或不匹配必须 fail closed，不能静默切换：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:128-135`。
- PostGIS 镜像必须同时含 PostgreSQL/Timescale/PostGIS 并按 digest 固定；portable 必须含 Timescale 且不依赖 PostGIS：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2109-2112`。
- G1 要求两个 profile 分别从空 volume 执行 `upgrade → current → repeat upgrade → clone downgrade → upgrade head`，并核对 extension/table/index/hypertable/retention/profile：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2491-2496`。
- `docker-compose.yml` 的 owner 是 `integration-config`，`docker/init-db.sql` 与 `docker/postgres/**` 才是本模块 owner：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2347-2350`。因此下文把根 Compose 问题明确标成跨 owner 集成阻断，而不是把越权修改当成已完成。

## 2. 本次实际运行的检查

### 2.1 用户指定测试

```text
python -m pytest -q "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/tests/integration/test_database_schema.py"
....ss                                                                   [100%]
4 passed, 2 skipped, 194 warnings in 0.30s
```

两个 live case 的跳过是代码明确设计的：没有 `DATABASE_TEST_DSN_POSTGIS` / `DATABASE_TEST_DSN_PORTABLE` 就 `pytest.skip`（`backend/tests/integration/test_database_schema.py:234-249`）。本次环境中两者均为 unset，因此这不是 PostGIS/portable 真实数据库通过证据。

### 2.2 静态/配置检查

```text
POSTGRES_PASSWORD=local-test-only docker compose -f .../docker/postgres/compose.yml --profile postgis config --quiet
# exit 0
POSTGRES_PASSWORD=local-test-only docker compose -f .../docker/postgres/compose.yml --profile portable config --quiet
# exit 0
bash -n .../docker/postgres/entrypoint-profile.sh
# exit 0
python -m py_compile .../scripts/verify_database.py
# exit 0
```

### 2.3 定向数据库诊断

- 使用本地 portable 镜像对 `init-db.sql` 连续重放两次：两次均 exit 0，扩展为 `btree_gist=1.7`、`timescaledb=2.17.2`、`uuid-ossp=1.1`，未出现 PostGIS。该结果只证明 portable 首次执行和 SQL 重放，不证明 PostGIS 或完整 migration。
- fresh portable 数据库执行 `alembic upgrade head`：exit 1，在 `0002_hydraulic_schema` 的 enum DDL 失败。
- 启动后的普通数据库 session 查询 `hydraulic.spatial_backend`：exit 1，PostgreSQL 返回 `unrecognized configuration parameter`。
- 禁用 Timescale retention job 后，job 的 `scheduled=false` 而 `drop_after` 仍为 `90 days`；构造同一 manifest 送入 verifier 仍得到 `result_ok=True`。

## 3. Findings

### DB-01（高）：PostGIS profile 的 named volume 挂在错误的数据目录，数据库不会持久化

**证据：**

- `docker/postgres/compose.yml:19-21` 对 PostGIS 挂载 `postgis-data:/var/lib/postgresql/data`，但 `docker/postgres/postgis.Dockerfile:1-13` 没有覆盖 `PGDATA`。
- 对 Dockerfile 默认 digest `sha256:f49bf91b8966318e9bab4c44e4cc6c6533539dd44fcb049ee3334349dfcb233f` 解析到的 HA amd64 image config 执行：

  ```text
  python -c "import json; c=json.load(open('/tmp/postgis-config.json')); print(next(x for x in c['config']['Env'] if x.startswith('PGDATA='))); print(c['config']['Labels'].get('com.timescaledb.image.postgis.version'))"
  PGDATA=/home/postgres/pgdata/data
  3.6.4
  ```

  同一 base 的 `WorkingDir` 是 `/home/postgres`，而 portable 基础镜像的 `PGDATA` 才是 `/var/lib/postgresql/data`。
- 因此 PostGIS 数据目录 `/home/postgres/pgdata/data` 不在 named volume 内；容器 writable layer 才是实际数据位置。

**影响：** `docker compose down/up`、容器替换、备份或重建镜像后 PostGIS 数据可能丢失；volume 看起来正常但实际没有保存数据库。数据持久化是该交付物的基本要求。

**修复建议：** 在 profile image/compose 中统一设置正确的 `PGDATA` 并挂载其父目录（或显式把 volume 挂到 `/home/postgres/pgdata/data`），同时增加“写入 sentinel → 删除容器 → 重建容器 → sentinel 仍在”的 PostGIS restart test。不要只依据 volume 名称判断已持久化。

### DB-02（高，跨 owner 阻断）：两个 profile 的 fresh-volume Alembic upgrade 都在到达目标 schema 前失败

**证据：**

- `backend/alembic/versions/0002_hydraulic_schema.py:130-133` 用 `", ".join(values)` 生成未引用 enum labels；`upgrade()` 在 `:2489-2504` 无条件先调用 `_create_enums()`，PostGIS 分支更晚才执行。
- 离线 SQL 定向渲染：

  ```text
  cd .../smart-city-drainage/backend && HYDRAULIC_SPATIAL_BACKEND=portable python -m alembic -c alembic.ini upgrade 0001_initial:0002_hydraulic_schema --sql
  exit=0
  CREATE TYPE hydraulic.network_status AS ENUM (draft, active, archived);
  ```

  离线渲染不会替数据库验证语法，所以 exit 0 不是可用性证明。
- fresh portable 数据库实际执行同一路径：

  ```text
  command=portable alembic upgrade head
  exit=1
  ... Running upgrade 0001_initial -> 0002_hydraulic_schema
  asyncpg.exceptions.PostgresSyntaxError: syntax error at or near "draft"
  [SQL: CREATE TYPE hydraulic.network_status AS ENUM (draft, active, archived)]
  ```

  日志定位到 `0002_hydraulic_schema.py:133`，与离线 SQL 完全一致。

**影响：** portable 和 PostGIS 都在同一个无条件 enum 阶段失败，无法创建 hypertable、generated geometry、GiST index 或 retention manifest；本模块交付的“可复现双路径”和 G1 均不成立。该文件不在本模块 ownedPaths，需由 schema/migration owner 修复后重新跑两条 fresh-volume 链。

**修复建议：** 使用 SQLAlchemy `ENUM`/正确 literal quoting 或安全的 PostgreSQL identifier/value quoting，确保每个 label 都是合法 enum value；随后对两个 profile 分别执行完整 G1 链，不能只依赖 offline SQL。

### DB-03（高）：profile 选择依赖 client-only `PGOPTIONS`，缺失时还会静默默认 portable，未满足不可变/fail-closed 契约

**证据：**

- `docker/postgres/entrypoint-profile.sh:21-26` 把 profile 写入 `PGOPTIONS`，并注释称后续 session 会看到同一 profile；`docker/init-db.sql:10-14` 在 GUC 缺失时 `COALESCE(..., 'portable')`。
- 在本地 portable 镜像完成初始化后，用普通 `docker exec ... psql` 查询：

  ```text
  command=SELECT current_setting(...)
  output=ERROR:  unrecognized configuration parameter "hydraulic.spatial_backend"
  exit=1
  ```

  `PGOPTIONS` 是 libpq client 环境，不会自动成为 postmaster 的持久 `postgresql.conf` 设置。
- `docker/init-db.sql:23-31` 只在新 volume 初始化时检查 PostGIS extension；没有读取既有 `hydraulic.schema_metadata`、没有在 postmaster 启动时检查既有 volume 的 profile。
- `scripts/verify_database.py:322-326` 连接时主动注入 `server_settings={"hydraulic.spatial_backend": profile}`，所以 verifier 自己制造了该 GUC，不能证明镜像/服务器实际持久化了请求 profile。
- 冻结契约要求 profile 缺失/不匹配 fail closed，而不是默认一个 profile：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:128-135`。

**影响：** 任何绕过 entrypoint 的 init/replay 都可能静默选择 portable；把另一 profile 的 image 复用到已有 volume 时，entrypoint 只比较环境变量而不核对数据库状态，可能出现同一数据库的 image/profile 与实际 schema 分离。普通 session 也无法读取脚本声称的 profile。

**修复建议：** 将 profile 作为数据库持久事实（例如 server config + schema metadata）并在每次启动核对；缺失必须拒绝启动，不能默认 portable。verifier 应读取并验证服务器实际 profile，而不是通过 `server_settings` 覆盖它；在已有 volume 上也必须拒绝 profile/extension/schema 不一致。

### DB-04（高）：README 的 Alembic 命令设置了错误的 DSN 变量，会迁移到默认数据库而非所选 profile

**证据：**

- `docker/postgres/README.md:44-49` 的迁移示例只设置 `DATABASE_URL_SYNC`。
- `backend/alembic/env.py:18-19` 实际把 `settings.DATABASE_URL` 写入 Alembic；`Settings` 的 async `DATABASE_URL` 不会从 `DATABASE_URL_SYNC` 自动派生。
- 定向配置检查：

  ```text
  cd .../smart-city-drainage/backend && python -c "from app.core.config import Settings; ... s=Settings(_env_file=None, DATABASE_URL_SYNC='postgresql://drainage_admin:local-test-only@127.0.0.1:55432/smart_drainage'); print(s.DATABASE_URL sanitized)"
  settings.DATABASE_URL= postgresql+asyncpg://localhost:5432/smart_drainage
  ```

**影响：** 运维按 README 执行时，migration 可能连接默认 localhost/5432，失败；若默认凭据/服务恰好存在，则可能在错误数据库执行 DDL。PostGIS/portable 验证命令因此不是可复现路径。

**修复建议：** README 同时明确设置 async `DATABASE_URL`（以及 verifier 使用的 DSN），或修改迁移入口以显式选择并校验目标 DSN；增加一个会拒绝错误端口/数据库名的 README command smoke test。

### DB-05（高，跨 owner 集成阻断）：默认根 Compose 仍绕过本模块的双 profile、固定容器名和 digest 约束

**证据：**

- 根 `docker-compose.yml:8-20` 仍使用 `timescale/timescaledb:latest-pg15`、`container_name: scn-postgres`，并挂载新的 init SQL；数据库 service 没有本模块 entrypoint 的 profile GUC。
- 根 `docker-compose.yml:30-55` 的 migrate service 只设置应用侧 `HYDRAULIC_SPATIAL_BACKEND`，并不能让数据库 service 变成 PostGIS/portable profile，也没有把新 profile fragment 接入启动顺序。
- 契约把根 Compose 归 `integration-config`：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2347-2350`；因此这是本模块的集成依赖，不应由本 reviewer 修改。

**影响：** 使用项目默认一键 Compose 时，仍可能初始化 vanilla Timescale image；请求 postgis 时，新的 init SQL 在缺少 GUC 时默认 portable，随后 migration 可能因缺 PostGIS 失败，且固定容器名/未锁定 image 仍违反交付目标。即使 owned fragment 的静态测试通过，真实默认部署仍不是双路径方案。

**修复建议：** integration-config owner 将根 Compose 改为引用已验证的 profile images/entrypoint，移除固定 `container_name`，显式设置 migrate profile，并让 backend/worker 依赖 migration 成功；两 profile 不得复用 volume。

### DB-06（高，验收阻断）：指定测试没有运行任何真实双 profile 验证，PostGIS image 也没有在本机可用

**证据：**

- live tests 在 `backend/tests/integration/test_database_schema.py:234-249` 无 DSN 时跳过；本次指定测试结果为 `4 passed, 2 skipped`。
- 环境检查输出：

  ```text
  DATABASE_TEST_DSN_POSTGIS=<unset>
  DATABASE_TEST_DSN_PORTABLE=<unset>
  ```

- `docker image inspect smart-city-drainage-postgres-postgis:local --format ...` 返回 `No such image`；本次只对已有 portable image 做了重放/迁移诊断。
- G1 的强制双 profile fresh-volume 链在 `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2491-2496`，不能用静态 manifest/mock verifier 替代。

**影响：** 扩展、hypertable、generated geometry、GiST、retention、profile mismatch、upgrade/downgrade/idempotence 都尚未获得真实 PostGIS/portable 证据；不能批准“可复现镜像方案”或报告 G1 PASS。

**修复建议：** 在两个独立 fresh volume 上实际运行并保存每条命令的 exit/artifact：`upgrade head → current → repeat upgrade → clone downgrade → upgrade head`、init SQL 至少两次重放、live verifier；PostGIS image build 和 capability probe 也必须成功。当前检查只能标为 NOT RUN。

### DB-07（中）：verifier 不检查 Timescale retention job 是否实际 scheduled，禁用 job 仍可通过

**证据：**

- `scripts/verify_database.py:355-363` 只收集 `p.enabled` 和 `j.config->>'drop_after'`，没有收集 `j.scheduled`、job schedule 或 job identity。
- `scripts/verify_database.py:199-211` 只比较 metadata `enabled`、retention days、chunk interval 和 `drop_after`。
- 真实 Timescale 2.17.2 诊断：

  ```text
  before: job_id=1000 scheduled=t config={"drop_after": "90 days", ...}
  SELECT alter_job(..., scheduled => FALSE);
  after:  job_id=1000 scheduled=f config={"drop_after": "90 days", ...}
  ```

  把 `scheduled=False` 放入 verifier manifest 的 retention 字段后，定向 Python probe 输出 `result_ok=True`。

**影响：** 数据库可以保留一条未调度的 90-day policy，而验证器仍报告 retention 成功；这不满足“90 天结果保留”的实际运行语义。

**修复建议：** 查询并返回 `scheduled`、schedule interval、proc/schema 和唯一 job identity；要求恰好一个启用的 `policy_retention` job 且 scheduled=true，并增加 disable/re-enable 的真实数据库负向测试。

### DB-08（中）：空间索引验证按 table 覆盖，额外/重复索引可被静默忽略

**证据：**

- `scripts/verify_database.py:250-269` 将 `spatial_indexes` 转为以 `table_name` 为 key 的 dict；同一表多个索引时后一个覆盖前一个。
- 构造包含预期 GiST index 加一个额外 `ix_extra_geometry_gist` 的 manifest，定向 Python probe 输出：

  ```text
  result_ok=True
  actual={'network_nodes': {'index_name': 'ix_hydraulic_network_nodes_geometry_gist', 'access_method': 'gist'}, ...}
  ```

**影响：** schema drift、重复索引或错误索引在某些排序下不会被 fail closed；验证器也没有核对 `pg_index.indisvalid/indisready`，不能把“catalog 中有名字”当成可用空间索引。

**修复建议：** 用 `(table_name,index_name)` 集合或完整 list 比较，要求精确集合；同时验证 `indisvalid`、`indisready`、索引定义/列，并为额外索引和 invalid index 增加负向测试。

### DB-09（中）：digest pin 可被无约束的 build arg 覆盖，README 约束不是机器门禁

**证据：**

- `docker/postgres/postgis.Dockerfile:1`、`portable.Dockerfile:1` 将 `BASE_IMAGE` 声明为可覆盖 ARG；`docker/postgres/compose.yml:5-10,33-38` 将 `POSTGIS_BASE_IMAGE`/`PORTABLE_BASE_IMAGE` 原样传入。
- README 仅以文字要求 override 保留同一 digest（`docker/postgres/README.md:13-15`），没有校验。
- 定向 Compose probe：

  ```text
  POSTGRES_PASSWORD=local-test-only POSTGIS_BASE_IMAGE=alpine:latest PORTABLE_BASE_IMAGE=alpine:latest docker compose -f ... --profile postgis config
  BASE_IMAGE: alpine:latest
  image: smart-city-drainage-postgres-postgis:local
  ```

**影响：** 运维可无意中构建未锁定、缺 PostgreSQL/Timescale/PostGIS 的镜像，测试仍只检查 Dockerfile 默认字符串，削弱可复现性和安全边界。

**修复建议：** 在 build/preflight 中拒绝没有 `@sha256:` 或不在允许 digest 集合的 override；若支持 registry mirror，保存 source/digest 映射并强制目标 digest 相同。将此约束加入测试，而不只放 README。

## 4. 通过项与未运行项

**已通过（仅限本次实际执行）：**

- 两个 profile 的 Compose YAML 解析：两个 `config --quiet` 均 exit 0。
- entrypoint `bash -n` 和 verifier `py_compile` 均 exit 0。
- portable init SQL 连续重放两次均 exit 0，扩展集合符合 portable 预期。
- verifier 的现有静态/manifest 单测在指定文件中通过。

**未运行或不能据此宣称 PASS：**

- PostGIS image build、PostGIS fresh-volume migration、PostGIS/portable 两套完整 G1 migration/idempotence/downgrade/upgrade。
- 两个 `DATABASE_TEST_DSN_*` live manifest test（本次明确 skipped）。
- 根 Compose 的端到端启动、默认部署 profile、完整 backup/restore。

## 5. 最终判定

当前至少 DB-01、DB-02、DB-03、DB-04、DB-05 是阻断或跨 owner 集成阻断；DB-06 使验收证据不完整。`approved` 必须为 **false**。实现者应先修复 migration owner、profile 持久化/校验、PostGIS volume 路径和 README/Compose 集成，再重新进行两 profile 的真实 G1 验证。
