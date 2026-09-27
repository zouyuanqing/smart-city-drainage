# 监控、备份与恢复实施摘要

> 模块：operations
> 日期：2026-09-24
> 仓库：`smart-city-drainage`
> 状态：ownedPaths 范围实现完成；监控 provisioning 与脚本可执行验证通过。完整 G6/AC-8、PostGIS live、backend 指标发射和 API/SSE 恢复 smoke 仍未通过/未运行，不能据此宣称整体验收完成。

## 1. 规范与现状输入

本轮实际读取并据以下冻结/基线输入实施：

- `docs/hydraulic-twin/SOURCE_PRD.md:20` 要求仿真队列指标；`SOURCE_PRD.md:28-32` 要求 90 天 retention、Prometheus 指标、可恢复部署和 AC-7/AC-8。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:591-618` 将 PostgreSQL/worker/API/SSE/Compose/backup-restore 纳入 G0-G6/AC-1-AC-8，且要求同轮真实证据，静态检查不能替代。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2165-2169` 冻结回滚顺序、manifest、Alembic/profile/extension/solver/hash、恢复后 schema/row/hash/topology/API/SSE 校验。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2171-2199` 冻结 21 个 metrics 及禁止高基数 ID label。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2372-2376` 把 backup/restore/Prometheus/Grafana 路径唯一归 operations owner。
- 实施前 `scripts/backup.sh:15-35`、`scripts/restore.sh:20-48` 硬编码 `scn-*` 容器和 Influx token、无 hash/退出校验；`docker/prometheus/prometheus.yml:1-8` 只抓 backend；`docker-compose.yml:168-201` 未挂载 Grafana provisioning。

## 2. 真实改动

### 2.1 备份/恢复实现

新增：

- `scripts/backup.py`
- `scripts/restore.py`
- `scripts/backup_manifest.py`
- `scripts/backup_test.py`
- `scripts/restore.ps1`

重写兼容入口：

- `scripts/backup.sh`
- `scripts/backup.ps1`
- `scripts/restore.sh`

实现要点：

1. **Compose service 参数化**：`OperationsBackup` 只构造 `docker compose -f ... [-p ...] exec/cp/run`，没有引用 `container_name`（`scripts/backup.py:188-215`；restore 对应 `scripts/restore.py:200-234`）。
2. **认证注入**：PostgreSQL、Redis、Influx token 仅从 secret file/环境变量读取，通过 stdin 传入容器 shell，不作为 Docker 参数（`scripts/backup.py:198-259`）。支持 Redis ACL `--redis-username`。
3. **PostgreSQL/Timescale/PostGIS**：`pg_dump --format=custom`，记录扩展、Alembic revision、规范化 schema/data hash、用户表行数和 topology；profile 与扩展双向 fail closed（`scripts/backup.py:303-360`、`scripts/backup.py:465-487`）。
4. **Redis**：认证 `redis-cli --rdb`，记录 `appenddirname`/`DBSIZE`，并检测 snapshot 期间 key count 漂移（`scripts/backup.py:489-557`）。
5. **Redis 恢复**：停 service；将 RDB 流式写入共享 volume；用无 TCP、仅 Unix socket 的临时 Redis 加载；生成 AOF base；启动正式 service；认证 PING/DBSIZE（`scripts/restore.py:613-702`）。这是对 Redis 7 AOF 优先语义的实际修复。
6. **PostgreSQL 恢复**：`pg_restore --clean --if-exists --single-transaction --exit-on-error`；恢复后复核 profile/PostGIS/Timescale、Alembic、schema/data hash、用户表行数、topology（`scripts/restore.py:460-554`）。
7. **安全归档**：sidecar SHA-256、manifest 自身 hash、file/tree hash、安全相对路径、symlink 拒绝（`scripts/backup_manifest.py:86-139`、`scripts/backup_manifest.py:147-224`；`scripts/restore.py:155-185`）。
8. **破坏性确认**：默认 verify-only；apply 必须同时传 `--apply` 和精确 `--confirm <backup_id>`。
9. **API/SSE smoke**：支持 URL、状态/content-type 校验和 `--smoke-header-env`；只记录 URL/status/content type（`scripts/restore.py:704-741`）。
10. **跨平台**：Bash/PowerShell 都调用同一 Python 实现，Windows/Linux 生成相同 `tar.gz + sidecar + manifest` 格式。

### 2.2 监控、Grafana 与文档

新增/修改：

- `docker/prometheus/prometheus.yml`
- `docker/prometheus/rules/hydraulic-recording.yml`
- `docker/prometheus/rules/hydraulic-alerts.yml`
- `docker/prometheus/validate_monitoring.py`
- `docker/grafana/provisioning/datasources/prometheus.yml`
- `docker/grafana/provisioning/dashboards/hydraulic.yml`
- `docker/grafana/provisioning/{alerting,notifiers,plugins}/.gitkeep`
- `docker/grafana/dashboards/hydraulic-overview.json`
- `docs/hydraulic-twin/operations.md`

实现要点：

- Prometheus 加载 rule files 并抓 `backend:8000/metrics`（`docker/prometheus/prometheus.yml:6-22`）。
- 11 条 recording rules 覆盖 queue、active slots、failure ratio、duration/nowcast P95、outbox/lag（`docker/prometheus/rules/hydraulic-recording.yml:4-35`）。
- 10 条 alert rules 覆盖 metrics absent、queue backlog、failure ratio、slots、180 秒 nowcast SLO、outbox、publish lag、SSE overflow、notification、cleanup（`docker/prometheus/rules/hydraulic-alerts.yml:3-120`）。
- Grafana datasource UID 固定 `prometheus`（`docker/grafana/provisioning/datasources/prometheus.yml:2-12`）；dashboard provider 固定 `/var/lib/grafana/dashboards`（`docker/grafana/provisioning/dashboards/hydraulic.yml:2-12`）。
- Dashboard 固定 UID `smart-city-hydraulic`，15 panels、24 PromQL targets；无 `simulation_id`/`alert_id`/`event_id` label。
- 运维手册覆盖 secret 注入、备份、离线验证、恢复、PostGIS/Timescale/Redis 语义、监控、告警处置、故障排查和 G6 证据边界（`docs/hydraulic-twin/operations.md:21-45`、`:60-166`、`:168-253`、`:255-408`）。

## 3. 本轮实际执行的验证

### 3.1 离线脚本套件

执行：

```bash
python smart-city-drainage/scripts/backup_test.py
```

输出：

```text
Ran 11 tests in 2.291s
OK
```

覆盖 secret 仅 stdin、service 参数化、无旧容器名、manifest/sidecar/tree hash、tar traversal、PostGIS/portable mismatch、PostgreSQL schema/data/row/topology、Redis stop/RDB/AOF/start/PING/DBSIZE、Influx tree/auth、精确 backup ID 确认。对应测试入口见 `scripts/backup_test.py:290-441`。

### 3.2 Python / wrapper 验证

执行：

```bash
python -m compileall -q \
  smart-city-drainage/scripts/backup.py \
  smart-city-drainage/scripts/backup_manifest.py \
  smart-city-drainage/scripts/backup_test.py \
  smart-city-drainage/scripts/restore.py \
  smart-city-drainage/docker/prometheus/validate_monitoring.py

bash -n smart-city-drainage/scripts/backup.sh smart-city-drainage/scripts/restore.sh
pwsh -NoProfile -Command "& './smart-city-drainage/scripts/backup.ps1' -Python python '--help' | Select-Object -First 1"
pwsh -NoProfile -Command "& './smart-city-drainage/scripts/restore.ps1' -Python python '--help' | Select-Object -First 1"
```

输出：

```text
compileall=PASS
bash_syntax=PASS
usage: backup.py [-h] [--backup-id BACKUP_ID] [--components COMPONENTS]
usage: restore.py [-h] [--verify-only | --apply] [--confirm CONFIRM]
```

### 3.3 真实 Redis 7 认证备份→漂移→恢复

使用临时 Compose fixture；service 未设置 `container_name`，Redis 使用 `--requirepass` 和 appendonly。执行的准确核心命令：

```bash
python "$repo/scripts/backup.py" \
  --backup-id live-redis-001 --components redis \
  --compose-file "$compose_file" --project-name "$project" \
  --output-dir "$out" --redis-service redis \
  --redis-password-file "$secret_file" --writers-paused

python "$repo/scripts/restore.py" \
  "$out/live-redis-001.tar.gz" --verify-only \
  --compose-file "$compose_file" --report "$tmp/verify.json"

python "$repo/scripts/restore.py" \
  "$out/live-redis-001.tar.gz" --apply --confirm live-redis-001 \
  --components redis --compose-file "$compose_file" \
  --project-name "$project" --redis-service redis \
  --redis-password-file "$secret_file" --report "$tmp/restore.json"
```

备份后新增 `after-marker` 制造漂移，再执行恢复。输出：

```text
backup_status=BACKUP_STATUS=COMPLETE
verify_status=RESTORE_STATUS=VERIFIED
restore_status=RESTORE_STATUS=RESTORED
marker_after_restore=present
post_backup_key_after_restore=<nil>
dbsize_after_restore=1
```

临时 container/network/volume 已由 trap 清理；最终 `docker ps -a --filter "name=scn-ops-"` 无输出。

### 3.4 真实 TimescaleDB portable 备份→clean target 恢复

使用临时 `timescale/timescaledb:latest-pg15` fixture，创建 Timescale extension、Alembic 表和 hydraulic topology；恢复到新 database。执行的准确核心命令：

```bash
python "$repo/scripts/backup.py" \
  --backup-id live-timescale-pass --components postgres \
  --compose-file "$compose_file" --project-name "$project" \
  --output-dir "$out" --postgres-service postgres \
  --postgres-user drainage_ops --postgres-db smart_drainage \
  --pg-password-file "$secret_file" --schema-profile portable \
  --solver-sha256 1111111111111111111111111111111111111111111111111111111111111111 \
  --writers-paused

python "$repo/scripts/restore.py" "$out/live-timescale-pass.tar.gz" --verify-only ...
python "$repo/scripts/restore.py" "$out/live-timescale-pass.tar.gz" \
  --apply --confirm live-timescale-pass --components postgres \
  --postgres-db smart_drainage_restore ...
```

输出：

```text
backup_status=BACKUP_STATUS=COMPLETE
verify_status=RESTORE_STATUS=VERIFIED
restore_status=RESTORE_STATUS=RESTORED
timescale_extension=timescaledb
restored_network_nodes=2
```

该验证实际经过 `pg_dump custom`、Timescale extension、Alembic revision、schema/data hash、用户表行数和 topology 复核。临时资源已清理。

### 3.5 PostGIS 镜像能力检查

执行：

```bash
docker run --rm --entrypoint sh timescale/timescaledb:latest-pg15 -ec \
  'sharedir=$(pg_config --sharedir); \
   test -f "$sharedir/extension/timescaledb.control" && echo timescaledb=yes || echo timescaledb=no; \
   test -f "$sharedir/extension/postgis.control" && echo postgis=yes || echo postgis=no; \
   postgres --version'
```

输出：

```text
timescaledb=yes
postgis=no
postgres (PostgreSQL) 15.17
```

因此本轮 **PostGIS live backup/restore 未运行**。PostGIS profile 分支由离线测试覆盖（`scripts/backup_test.py:327-370`），但不能替代真实 PostGIS 镜像/空库证据。

### 3.6 监控静态与目标版本验证

执行：

```bash
python smart-city-drainage/docker/prometheus/validate_monitoring.py
```

输出：

```text
MONITORING_VALIDATION=PASS
CONTRACT_METRICS=21
GRAFANA_PANELS=15
PROMQL_TARGETS=24
HIGH_CARDINALITY_ID_LABELS=0
```

执行：

```bash
MSYS_NO_PATHCONV=1 docker run --rm --entrypoint /bin/promtool \
  -v "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/docker/prometheus:/etc/prometheus:ro" \
  prom/prometheus:v2.48.0 check config /etc/prometheus/prometheus.yml
```

输出：

```text
SUCCESS: 2 rule files found
SUCCESS: /etc/prometheus/prometheus.yml is valid prometheus config file syntax
SUCCESS: 10 rules found  # hydraulic-alerts.yml
SUCCESS: 11 rules found  # hydraulic-recording.yml
```

Grafana 10.3.0 临时实例使用新增 provisioning/dashboard 只读挂载启动，`/api/health` 返回 database ok/version 10.3.0，dashboard search 返回 UID `smart-city-hydraulic` 和标题 `Hydraulic Twin Operations`。测试容器已删除。

### 3.7 其他检查

- `python -m json.tool docker/grafana/dashboards/hydraulic-overview.json`：`grafana_json=PASS`。
- `git diff --check -- <owned tracked paths>`：`tracked_diff_check=PASS`。
- owned 文件 trailing-whitespace scan：无输出。
- 生产脚本旧容器名/旧 secret scan：无输出。
- `python -m black/ruff/flake8 --version`：本机均显示 `No module named ...`，未将这些格式/静态工具记为通过。

## 4. 未完成与风险

1. **Backend 指标未实现/未在本工作树找到。** 执行精确名称扫描输出 `backend_hydraulic_metric_definitions=NOT_FOUND`。Dashboard/rules 已准备好，但真实 `/metrics` 仍不会产生 hydraulic 指标，直到 `backend/app/services/hydraulic/metrics.py` owner 落地。
2. **Compose 尚未挂载 monitoring 文件。** 执行 `rg "docker/prometheus/rules|docker/grafana/provisioning|docker/grafana/dashboards" docker-compose.yml` 输出 `compose_monitoring_mounts=NOT_PRESENT`。Integration owner 必须按 `docs/hydraulic-twin/operations.md:302-316` 增加只读挂载。
3. **PostGIS live 未运行。** 当前 `timescale/timescaledb:latest-pg15` 实测不含 PostGIS，且 `docker-compose.yml:9-16` 仍使用 `latest` 和固定明文 DB secret；均在 operations ownedPaths 外。必须由 database-bootstrap/integration-config 提供 digest-pinned、Timescale+PostGIS 镜像后再跑 G6。
4. **完整 G6/AC-8 未运行。** 未跑两 profile fresh volume、worker/nowcast、API/SSE、legacy zero-regression、完整 manifest metadata。当前 Redis/Timescale 证据是隔离组件级 live 验证，不是完整发布门禁。
5. **Influx live 未运行。** 目录树/auth/restore 由离线测试覆盖；目标环境 Influx service/token/bucket 尚未实测。
6. **生产 Compose secret/image 风险仍存在。** 本模块没有越权修改 `docker-compose.yml`；手册明确禁止把当前演示 secret/`latest` 用作生产证据。
7. 未运行 backend/frontend 全量测试、完整 G0-G6 或 acceptance；本任务只运行上述 operations 专项验证。

## 5. 变更边界

- 仅修改/新增 task ownedPaths：`scripts/backup*`、`scripts/restore*`、`docker/prometheus/**`、`docker/grafana/**`、`docs/hydraulic-twin/operations.md`。
- 另按任务显式输出要求创建本文件 `docs/hydraulic-twin/implementation/operations.md`。
- 未修改 backend、migration、Compose、README 或冻结 PRD/契约。
- 未 commit、未 push。
