# 水力领域模型与迁移独立审查

- **模块**：`hydraulic-schema`（水力领域模型与迁移）
- **审查方式**：只读复核冻结契约、授权工件、ORM/Pydantic、Alembic 与定向诊断；未修改实现代码或迁移。
- **审查结论**：**BLOCKED / `approved=false`**
- **判定依据**：发现 6 项高风险、2 项中风险问题；尤其是冻结模型不完整、跨 network/revision 关联未被数据库约束、PostGIS generated column 与 ORM 写入不兼容、profile/readiness 集成未闭合。

## 1. 授权与范围证据

本次读取了三份冻结授权工件：

- `artifacts/contracts/v3-independent-review.json:1-71`：`R0_DESIGN_PASS`，基线为 `ca99e0485fb12fbecbd565e79d81f745f8d4a748`。
- `artifacts/contracts/dependency-config-handoff.json:1-16,207-213,1546-1554`：`READY_WITH_DEFERRED`，记录 54 个授权设置与 47 个 preserve-only 设置。
- `artifacts/contracts/r0-gate-execution.json:1-20,91-102,138-149`：记录 migration gate 与 dependency handoff 的直接命令 `exit_code=0`；同一工件明确把 database startup、Alembic upgrade/downgrade、G0-G6、AC-1-AC-8 列为 `NOT RUN`。

因此，**本审查没有把“迁移已获授权”和“迁移已在真实数据库验证”混为一谈**。上述两个 gate 是实现前授权证据；按本任务要求，本次没有重复最终全量门禁，也没有把下面的离线/SQLite 结果当作 G1 替代品。

## 2. 本次实际运行的定向检查

### 2.1 默认 profile 的 focused schema suite

命令（仓库根）：

```text
C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements backend/requirements-py311.lock python -B -m pytest backend/tests/test_hydraulic_schema.py -q
```

输出：

```text
...................                                                      [100%]
19 passed, 1 warning in 1.18s
```

这证明现有 19 个 focused tests 在默认 `portable` ORM 导入路径通过；不证明 PostgreSQL/PostGIS/Timescale 可用。

### 2.2 Alembic 图检查

命令（`backend/`）：

```text
C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B -m alembic -c alembic.ini heads
```

输出：

```text
0002_hydraulic_schema (head)
```

### 2.3 PostGIS profile 下的 focused suite

命令（`backend/`）：

```text
HYDRAULIC_SPATIAL_BACKEND=postgis C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B -m pytest tests/test_hydraulic_schema.py -q
```

输出摘要：

```text
2 failed, 17 passed, 1 warning in 0.75s
```

其中 SQLite `create_all` 在 `geometry(POINT,4326)` 处报 `sqlite3.OperationalError: near "POINT": syntax error`，且 `test_postgis_orm_metadata_is_process_profile_specific` 失败。该结果不是 PostgreSQL 兼容性证明；它说明当前测试只覆盖了“默认 portable 导入 + 静态 PostGIS SQL”，没有覆盖真实 PostGIS 写入。

### 2.4 只读 ORM/契约差异诊断

运行的静态 ORM 列清单命令输出：

```text
SimulationRun missing= ['cancel_requested_at', 'deadline_at', 'fencing_token', 'initial_state_content_sha256', 'initial_state_id', 'last_retry_error_code', 'lease_owner', 'lease_token', 'lease_until', 'max_attempts', 'network_revision_content_sha256', 'next_attempt_at', 'parameter_set_id', 'phase', 'published_attempt_no', 'requested_by', 'retry_backoff_seconds', 'snapshot_content_sha256', 'started_at', 'target_manifest_id', 'target_manifest_sha256']
SimulationAttempt missing= ['network_revision_id', 'summary_id', 'target_manifest_id', 'target_manifest_sha256']
SimulationFrame missing= ['solver_id', 'solver_version', 'target_manifest_id', 'target_manifest_sha256']
CalibrationRun missing= ['cancel_requested_at', 'deadline_at', 'fencing_token', 'last_retry_error_code', 'lease_owner', 'lease_token', 'lease_until', 'max_attempts', 'next_attempt_at', 'retry_backoff_seconds', 'split_manifest', 'started_at', 'updated_at']
CalibrationReport missing= ['split_manifest']
NowcastRun missing= ['config_hash', 'fencing_token', 'flood_forecast_probability_calibration_id', 'flood_forecast_probability_sha256', 'flood_forecast_rule_id', 'flood_forecast_rule_sha256', 'last_retry_error_code', 'lease_owner', 'lease_token', 'lease_until', 'max_attempts', 'model_calibration_id', 'model_version', 'next_attempt_at', 'pipe_surcharge_probability_calibration_id', 'pipe_surcharge_probability_sha256', 'pipe_surcharge_rule_id', 'pipe_surcharge_rule_sha256', 'retry_backoff_seconds', 'solver_id', 'solver_version', 'target_manifest_id', 'target_manifest_sha256']
ForecastAlert missing= ['acknowledged_at', 'acknowledged_by', 'first_qualifying_at', 'forecast_rule_id', 'forecast_rule_sha256', 'last_qualifying_at', 'measurements', 'network_revision_id', 'nowcast_issued_at', 'peak_at', 'peak_value', 'probability_calibration_sha256', 'resolution_reason', 'resolved_at', 'resolved_by', 'risk_policy_version', 'target_manifest_id', 'target_manifest_sha256', 'threshold']
```

另一个只读表格提取诊断比较了契约中有标题的 hydraulic 表与 `0002` 的 `op.create_table`：

```text
EXPECTED 35
CREATED 21
MISSING_FROM_0002 16 ['ab_comparisons', 'device_network_bindings', 'elevation_profiles', 'event_outbox', 'forecast_alert_windows', 'forecast_rules', 'network_imports', 'network_jobs', 'network_validation_issues', 'notification_deliveries', 'observation_ingest_events', 'observation_samples', 'observation_trust_policies', 'parameter_sets', 'validation_runs', 'worker_slots']
```

该正则没有计入契约中以代码块而不是四级标题列出的 `pond_cells`、`boundary_conditions`、`initial_state_snapshots`、`network_target_manifests` 等表，因此 16 是下限，不是完整缺表数。

## 3. Findings

### HS-01（高）：冻结的数据模型只是部分投影，simulation/frame/calibration/forecast 的强制关联键缺失

**证据：**

- 冻结契约要求 simulation 绑定 `requested_by`、target manifest 三元组、revision/snapshot/initial-state/parameter-set hash、retry/lease/fencing/deadline 字段，并要求 attempt/frame/nowcast/alert 使用复合 FK：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:649-719,746-816,1034-1152`。
- 实际 `SimulationRun` 只有基础 network/revision/rainfall/idempotency/solver 字段：`backend/app/models/hydraulic_models.py:780-879`；`SimulationAttempt` 没有 revision、target manifest、summary 绑定：`:904-944`；`SimulationFrame` 没有 target manifest、solver identity：`:1008-1069`。
- 实际 `CalibrationRun/Report` 没有 `split_manifest` 及完整 durable lifecycle：`backend/app/models/hydraulic_models.py:1129-1246`；`NowcastRun` 只有一个 `probability_calibration_id`，没有 flood/pipe 两套 calibration/rule ID+hash、manifest/config hash：`:1304-1402`；`ForecastAlert` 没有 revision/manifest/rule/issued-at/threshold/measurements：`:1405-1522`。
- Alembic 重复了这些缺口：`backend/alembic/versions/0002_hydraulic_schema.py:622-897,901-1009,1012-1265`。
- 契约还要求 target manifest、initial state、parameter sets、network jobs、worker slots、observation、forecast rules/windows/outbox/notification 等表：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:285-465,467-597,633-645,721-744,820-896,953-1008,1155-1223`；静态提取至少显示 16 个有标题的表没有出现在 `0002`。
- 实现摘要也明确承认 worker lifecycle、target-manifest/initial-state、observation、完整 forecast outbox/notification 尚未实现：`docs/hydraulic-twin/implementation/hydraulic-schema.md:148-156`。

**影响：** 无法在数据库层证明同一 revision/manifest、同一 simulation/attempt、同一 policy/rule 的结果血缘；无法满足任务所称的 calibration/forecast 关联模型与冻结契约，跨表写入可产生“看似有效但不可追溯”的数据。

**修复建议：** 在同一冻结契约下补齐所有必需表、列、复合 FK/unique/check，并同步 ORM 与 Pydantic；若本轮明确只交付核心子集，应先由 owner 缩窄/重签契约，不能以“follow-on”注释替代必需关联。

### HS-02（高）：`network_id` 与 `network_revision_id` 没有复合约束，跨网络 revision 可直接落库

**证据：**

- 契约要求所有版本化实体属于同一 revision，并要求跨 network/revision 引用由复合约束阻止：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:221-243,261-282,356`。
- ORM 对 node、pipe、catchment、simulation、nowcast 使用彼此独立的单列 FK；例如 node 的两个 FK 是 `backend/app/models/hydraulic_models.py:384-403`，migration 对应 `backend/alembic/versions/0002_hydraulic_schema.py:267-321`。
- 只读 SQLite 诊断创建 network A、network B 及各自 revision，然后插入 `network_id=A, network_revision_id=revision(B)` 的 node，输出：

```text
MISMATCH_ACCEPTED True True
```

同一诊断将 `parent_revision_id` 设置为自身，输出：

```text
SELF_PARENT_ACCEPTED True
```

**影响：** 拓扑实体可以挂到另一 network 的 revision；pipe/catchment 的 node 端点虽按 revision 复合约束，但 `network_id` 仍可与该 revision 不一致。历史 simulation、nowcast 和 alert 也可能沿错误血缘继续写入。

**修复建议：** 为 revisions 增加可被复合引用的 `(network_id,id)` 唯一键，并让所有版本化实体使用 `(network_id,network_revision_id)` 复合 FK；对 `networks.current_revision_id` 同样绑定 network，增加 parent 非自引用检查及 revision 不可变/不可删除保护。

### HS-03（高）：durable job 的 fencing、retry/idempotency 约束不完整，不能阻止陈旧 worker 或错误 generation

**证据：**

- 契约要求 `UNIQUE(simulation_id,fencing_token)`、attempt 与 simulation/revision/manifest 的复合绑定、retry_wait 的 DB check，以及 idempotency 24 小时/retired 语义：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:703-719,746-767,1225-1245`。
- 实际 `SimulationAttempt` 只有 `UNIQUE(simulation_id,attempt_no)`，没有 fencing 唯一约束：`backend/app/models/hydraulic_models.py:904-916`；migration 同样只有 attempt number unique：`backend/alembic/versions/0002_hydraulic_schema.py:720-756`。
- `SimulationFrame` 的 `simulation_id`、`attempt_id` 是两个独立 FK，`SimulationResult` 也分别指向 simulation 和 attempt，没有保证二者属于同一 attempt：`backend/app/models/hydraulic_models.py:1027-1054,1090-1107`。
- `idempotency_records` 的 migration 只有 generation/key 长度检查，没有 `expires_at = created_at + 24 hours` 或 `retired_at = expires_at`：`backend/alembic/versions/0002_hydraulic_schema.py:571-621`。
- 只读诊断插入两个相同 fencing token 的 attempts，输出 `DUPLICATE_FENCING_ACCEPTED True`；插入 `created_at` 到 `expires_at` 相隔 365 天的 idempotency row，输出 `NON_24H_IDEMPOTENCY_ACCEPTED 365`。

**影响：** 两个 worker 可以共享 fencing token，旧 worker 不能被数据库 CAS 拒绝；frame/result 可以跨 attempt 拼接；任意服务路径可写入超过 24 小时或错误退休状态的幂等事实源，破坏 exactly-once/generation 语义。

**修复建议：** 补齐并同步 ORM/migration 的 fencing 唯一约束、attempt/frame/result 复合 FK、retry 状态等价 CHECK、TTL/retirement CHECK；所有 worker 更新必须带当前 fencing token 条件，并用正反例数据库测试覆盖。

### HS-04（高）：PostGIS generated column 在 ORM 中被映射成普通可写列，PostGIS 写入路径不可用

**证据：**

- Alembic 使用 `GENERATED ALWAYS AS (ST_GeomFromText(...)) STORED NOT NULL`：`backend/alembic/versions/0002_hydraulic_schema.py:1329-1350`。
- ORM 却把 `geometry` 声明为普通 `mapped_column(PostGISGeometry(...), nullable=False)`，没有 `Computed`/只读/插入排除设置：`backend/app/models/hydraulic_models.py:423-425,520-523,612-615`。
- 定向诊断打印 migration SQL：

```text
MIGRATION_GENERATED= ['ALTER TABLE hydraulic.network_nodes ADD COLUMN geometry geometry(POINT,4326) GENERATED ALWAYS AS (ST_GeomFromText(geometry_wkt, 4326)) STORED NOT NULL;', ...]
```

在 `HYDRAULIC_SPATIAL_BACKEND=postgis` 下编译 ORM INSERT 的输出包含：

```text
INSERT INTO hydraulic.network_nodes (..., geometry) VALUES (..., %(geometry)s)
```

**影响：** PostgreSQL 会把 generated column 视为不可直接 INSERT 的列；ORM repository 一旦走 `NetworkNode`/`PipeSegment`/`Catchment` 写入，可能直接报 `cannot insert into generated column`。同时自定义类型没有真实 PostgreSQL round-trip 证据。

**修复建议：** 将 geometry 映射为 SQLAlchemy computed/read-only 列或完全从写入映射中排除，保留 WKT+bbox 为唯一业务写源；在真实 PostgreSQL/PostGIS 上执行 ORM insert/select 回归，而不是只断言 offline SQL 字符串。

### HS-05（高）：空间 codec 不是 fail-closed：错误 CRS、3D/自交几何及 portable 原始 WKT 均可进入模型

**证据：**

- 契约固定 EPSG:4326/lon-lat，并要求非匹配 CRS fail closed：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:128-135`；PostGIS/portable 必须使用同一受测 codec：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1253-1264`。
- `_parse_geojson_geometry` 只读取 `type` 与 `coordinates`，完全忽略 GeoJSON 的 `crs` 成员：`backend/app/schemas/hydraulic_schemas.py:261-293`；`GeometryDocument` 固定默认 `crs=EPSG:4326` 并回写结果：`:427-482`。
- 定向诊断输入 `EPSG:3857` GeoJSON，输出：

```text
ACCEPTED_CRS EPSG:4326 POINT (121.3 31.2)
```

- `_flatten_coordinates` 对嵌套坐标只取前两个值：`backend/app/schemas/hydraulic_schemas.py:166-187`；诊断输入带 Z 坐标，输出 `ACCEPTED Point POINT (121.3 31.2)`，即静默丢弃 Z。
- `_validate_polygon` 只检查闭合、长度与三个 distinct 点，不检查自交/环拓扑：`:190-204`；诊断输入 bow-tie polygon，输出 `ACCEPTED Polygon POLYGON ((...))`。
- ORM 的 portable `geometry_wkt` 是无验证的普通 Text：`backend/app/models/hydraulic_models.py:404-425`；portable migration 没有 WKT 语法/类型检查。SQLite 诊断写入 `POINT (181 31); DROP TABLE users`，输出 `INVALID_WKT_ACCEPTED ...`。

**影响：** 3857 坐标会被静默当成 4326，3D 数据会静默降维，portable 与 PostGIS 对同一非法几何产生不同接受结果；这些数据一旦进入 simulation/solver，可能造成错误位置、错误面积或错误拓扑。

**修复建议：** 明确拒绝非 EPSG:4326 的 GeoJSON CRS 与非二维坐标；实现共享的 2D/拓扑有效性检查（WKT 与 GeoJSON 必须调用同一规则），并在 portable 写入边界强制执行；为 CRS、Z、自交、非法 WKT 增加负向测试。

### HS-06（高）：Alembic 唯一事实源、profile/readiness 与 Compose bootstrap 未闭合

**证据：**

- 契约 D-03 要求 Alembic 是唯一可部署 schema writer、移除生产 `create_all`、readyz 校验 `alembic current`：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:121-126`；profile 不得静默切换：`:128-135`。
- 配置仍是 `HYDRAULIC_SCHEMA_REVISION=0001_initial`：`backend/app/core/config.py:191-197`；定向诊断输出：

```text
SETTINGS_SCHEMA_REVISION 0001_initial
MIGRATION_HEAD 0002_hydraulic_schema
```

- 生产 lifespan 仍调用 `create_all_tables()`：`backend/app/main.py:133-142`；而 `_SchemaAwareMetaData._selected_tables()` 在无显式 tables 时故意过滤所有 `hydraulic.*`：`backend/app/models/db_models.py:31-50`。因此应用启动不会创建/校验本次 hydraulic schema。
- Compose 的数据库仍挂载 `docker/init-db.sql`：`docker-compose.yml:9-22`；该 SQL 自己创建 legacy enum/table/hypertable：`docker/init-db.sql:10-38,40-79,100-118`，而 `0001_initial.py:20-48` 又无重复保护地创建相同类型/表。契约明确要求 init SQL 只保留 extension bootstrap。
- 当前 Compose 只声明 `timescale/timescaledb:latest-pg15`：`docker-compose.yml:9-12`，没有已验证的 PostGIS profile；postgis migration 会在 `0002_hydraulic_schema.py:115-125` 无条件 `CREATE EXTENSION postgis`。
- 实施摘要也明确记录：revision 仍为 0001、启动仍 `create_all`、live PostGIS/Timescale 尚未验证：`docs/hydraulic-twin/implementation/hydraulic-schema.md:148-155`。

**影响：** 进程可能以错误的 declared head 启动；fresh Compose bootstrap 与 Alembic 可能争抢 legacy schema writer；postgis profile 在未证明具备 PostGIS 的镜像上无法启动。该问题会使“可回滚迁移”即使 SQL 静态存在，也没有可部署保证。

**修复建议：** 将 init SQL 降为 extension bootstrap；由明确的 Alembic service/entrypoint 负责 migration；更新 `HYDRAULIC_SCHEMA_REVISION` 并在 readiness 比较 `alembic_version`/`schema_metadata`；为 portable 与 postgis 使用可验证、版本锁定的数据库镜像，并在两者 fresh volume 上跑 G1。

### HS-07（中）：ORM metadata 与 Alembic CHECK 集合漂移，测试对 profile 有顺序/环境依赖

**证据：**

- Pipe ORM 只有 bbox order check：`backend/app/models/hydraulic_models.py:432-470`；migration 另有 longitude/latitude range checks：`backend/alembic/versions/0002_hydraulic_schema.py:349-367`。
- Catchment ORM 同样只有 order/area check：`backend/app/models/hydraulic_models.py:541-562`；migration 另有范围检查：`backend/alembic/versions/0002_hydraulic_schema.py:431-442`。
- migration 的 `simulations` 有 solver-selection check：`backend/alembic/versions/0002_hydraulic_schema.py:668-682`；ORM `SimulationRun` 没有该 check：`backend/app/models/hydraulic_models.py:780-798`。
- `HYDRAULIC_SPATIAL_BACKEND=postgis` 的 focused suite 实际为 `2 failed, 17 passed`（见 §2.3），且测试本身在 `backend/tests/test_hydraulic_schema.py:661-688` 依赖默认 import profile 与 subprocess，未验证真实 PostgreSQL generated-column insert。

**影响：** `Base.metadata.create_all`、autogenerate、SQLite 合同测试与生产 Alembic 可能对同一行给出不同接受结果；当前 19-pass 结果不能覆盖所有 profile。

**修复建议：** 统一约束来源并加 schema-contract 断言；为 portable/PostGIS 使用相应真实 dialect，至少在 PostgreSQL 上测试正反例、generated column 写入/读取和 migration repeat。

### HS-08（中）：Pydantic 时间契约没有拒绝 naive datetime

**证据：**

- 契约要求所有业务时间为 UTC/TIMESTAMPTZ：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:87-105`。
- `SimulationRunCreate.start_at`、rainfall point `valid_at` 等字段只是 `datetime`，没有统一 timezone validator：`backend/app/schemas/hydraulic_schemas.py:576-593,627-638`。
- 定向诊断用无时区 `datetime(2026,1,1)`，输出：

```text
NAIVE_START_ACCEPTED None
NAIVE_RAINFALL_ACCEPTED None
```

**影响：** naive 时间进入不同服务/数据库时会被按本地时区解释，导致 frame grid、deadline、rainfall series 和 forecast freshness 偏移。

**修复建议：** 对所有 request/domain datetime 统一要求 timezone-aware、归一化 UTC `Z`；拒绝 naive datetime，并加入跨 API/ORM 的边界测试。

## 4. 未运行与不能据此宣称 PASS 的项目

- 未运行真实 PostgreSQL/PostGIS/Timescale 的 `upgrade → current → repeat → clone downgrade → upgrade`；`r0-gate-execution.json:91-102` 明确将 database startup、Alembic upgrade/downgrade、G0-G6、AC-1-AC-8 列为 `NOT RUN`。
- 未运行 Docker/Compose、PostGIS 镜像能力探测、Timescale retention policy 实测；`hydraulic-schema.md:151-155` 也明确记录这些未运行。
- 本次 focused suite 的 19 个通过项只证明默认 portable 的离线/SQLite 契约；不能替代 G1、G3、G4 或生产写入验证。

## 5. 最终决定

**不批准。** 授权前置工件本身已落盘且记录了 exit 0，但实现尚未满足冻结 schema 的关联完整性、PostGIS 双路径可写性、profile/readiness 集成和真实数据库验证要求。至少应先修复 HS-01～HS-06，再补齐对应数据库负向/正向测试与 G1 双 profile 证据；在此之前不能将本模块标记为 approved，也不能将 offline SQL 或 19 个 SQLite 测试解释为迁移完成。
