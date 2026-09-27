# 监控、备份与恢复独立审查

- 模块：`operations`
- 审查日期：2026-09-25
- 结论：**NOT APPROVED**
- 原因：发现 4 个 high、2 个 medium。high 影响冻结的恢复完整性、API/SSE smoke、G6 元数据真实性及监控可用性。

## 1. 审查依据与边界

本次按任务给出的冻结规格审查，目标包括仿真指标监控、Grafana/provisioning、Redis 认证备份恢复、PostGIS/Timescale 备份恢复及可执行运维验证；并核对真实实现而非仅采信实现摘要。冻结契约要求备份 manifest 记录 image digest、恢复后验证 schema/行数/hash/topology/API/SSE（`smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2159-2169`），冻结 21 个指标及高基数标签禁令（`smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2171-2199`）。

`docker-compose.yml` 不在本模块 `ownedPaths` 内；下述 H-04 是已确认的 integration 依赖，不建议 operations 越权修改，但端到端目标在依赖解决前不能批准。

## 2. Findings

### H-01 HIGH — PostgreSQL data hash 丢失表边界，跨表错位数据可得到相同 PASS hash

**问题**

`canonicalize_pg_dump(..., sort_lines=True)` 对 data-only dump 的**全部行**执行全局排序（`smart-city-drainage/scripts/backup_manifest.py:59-76`）。备份和恢复分别用该结果计算/复核 `data_sha256`（`smart-city-drainage/scripts/backup.py:475-486`、`smart-city-drainage/scripts/restore.py:521-526`）。全局排序打散 `COPY <table>` 与其数据行的关系，因此两个表之间交换相同行值后，规范化字节完全相同。现有行数校验只比较计数（`smart-city-drainage/scripts/restore.py:527-540`），topology 校验也只比较存在性和计数（`smart-city-drainage/scripts/restore.py:430-447`），不能补足该缺口。

**执行证据**

```bash
python - <<'PY'
import sys
sys.path.insert(0, r'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/scripts')
from backup_manifest import canonicalize_pg_dump, sha256_bytes
original = "COPY public.table_a (id) FROM stdin;\n1\n\\.\n\nCOPY public.table_b (id) FROM stdin;\n2\n\\.\n"
rows_swapped_between_tables = "COPY public.table_a (id) FROM stdin;\n2\n\\.\n\nCOPY public.table_b (id) FROM stdin;\n1\n\\.\n"
h1 = sha256_bytes(canonicalize_pg_dump(original, sort_lines=True))
h2 = sha256_bytes(canonicalize_pg_dump(rows_swapped_between_tables, sort_lines=True))
print(f'original_hash={h1}')
print(f'swapped_hash={h2}')
print(f'hashes_equal={h1 == h2}')
PY
```

输出：

```text
original_hash=c1458eb0bfb9fe3d2e55787ac6f20fd71b8451909221ad0fac860c8cb17382e0
swapped_hash=c1458eb0bfb9fe3d2e55787ac6f20fd71b8451909221ad0fac860c8cb17382e0
hashes_equal=True
```

现有测试 double 对任何 data-only dump 都返回同一个 `data-v1`（`smart-city-drainage/scripts/backup_test.py:99-107`），恢复测试只断言命令含 `pg_restore`/`--single-transaction`（`smart-city-drainage/scripts/backup_test.py:326-339`），因此没有覆盖该语义碰撞。

**影响**

损坏或错误目标数据库即使发生跨表行错配，也可能通过冻结契约要求的恢复 hash 校验，形成错误的 `RESTORED/PASS` 证据。

**修复方向**

按 `COPY`/表边界分别规范化，只允许在同一表的数据行内排序，并让表名/列结构进入摘要；增加“两表行互换但行数不变”的负向恢复测试。

### H-02 HIGH — `--smoke-header-env` 把环境变量名当 HTTP header 名，文档中的认证 smoke 实际不会发送 `Authorization`

**问题**

`_smoke()` 读取环境变量后执行 `headers[env_name] = value`（`smart-city-drainage/scripts/restore.py:719-725`）。运维手册导出 `SMOKE_AUTHORIZATION` 并原样传入 `--smoke-header-env 'SMOKE_AUTHORIZATION'`（`smart-city-drainage/docs/hydraulic-twin/operations.md:181-210`），实际产生 header `Smoke_authorization`，而不是 `Authorization`。脚本只检查 2xx/content-type（`smart-city-drainage/scripts/restore.py:703-717`），因此在受保护端点会失败，在不校验该自定义 header 的端点则可能无认证也被记为 PASS。

**执行证据**

```bash
python - <<'PY'
import os
import sys
from unittest.mock import MagicMock, patch
sys.path.insert(0, r'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/scripts')
import restore
args = restore.build_parser().parse_args([
    'archive.tar.gz', '--api-smoke-url', 'https://example.invalid/health',
    '--sse-smoke-url', 'https://example.invalid/events',
    '--smoke-header-env', 'SMOKE_AUTHORIZATION',
])
ops = restore.OperationsRestore(args)
response = MagicMock()
response.__enter__.return_value = response
response.status = 200
response.headers = {'Content-Type': 'text/event-stream'}
response.read.return_value = b': ready\n\n'
with patch.dict(os.environ, {'SMOKE_AUTHORIZATION': 'Bearer fixture-token'}), patch('restore.urllib.request.urlopen', return_value=response) as mocked:
    result = ops._smoke()
    sent_headers = [call.args[0].headers for call in mocked.call_args_list]
print(f'smoke_status={result["status"]}')
print(f'sent_header_names={[sorted(headers) for headers in sent_headers]}')
print(f'authorization_present={[("Authorization" in headers) for headers in sent_headers]}')
PY
```

输出：

```text
smoke_status=PASS
sent_header_names=[['Smoke_authorization'], ['Smoke_authorization']]
authorization_present=[False, False]
```

`restore_args()` 的测试入口没有提供任何 API/SSE URL 或 header（`smart-city-drainage/scripts/backup_test.py:227-265`），所以 11 个测试全绿也不能发现该问题。

**影响**

冻结契约要求的 API/SSE smoke 证据可能无法认证真实业务请求，或把未认证的 2xx 响应误记为恢复成功。

**修复方向**

把环境变量和 header 名显式映射，例如 `ENV_VAR=HEADER_NAME` 或独立的 `--smoke-header-env ENV_VAR --smoke-header-name Authorization`；增加本地受保护 HTTP/SSE fixture，同时断言实际 header 和无认证 401 不会 PASS。

### H-03 HIGH — `metadata_complete=true` 不验证 image digest 是否真实存在

**问题**

冻结契约要求记录 image digests（`smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2163,2169`）。实现只把 `docker compose images` 返回的任意 dict 原样收集（`smart-city-drainage/scripts/backup.py:260-272`），完整性判断仅检查列表是否为空（`smart-city-drainage/scripts/backup.py:649-655`），不要求每个目标服务都具有非空、不可变的 registry/content digest。测试 fixture 人工提供了 `Digest`（`smart-city-drainage/scripts/backup_test.py:67-71`），掩盖了真实输出可能只有本地 image ID 的情况。

**执行证据**

```text
METADATA_COMPLETE=true
image_rows=1
image_digest_fields=[None]
metadata_complete=True
metadata_gaps=[]
```

该结果由一个 `images` 行仅含 `ID`、不含 `Digest` 的 runner 生成；所有组件、solver hash、writer pause 和 commit 均齐全。

**影响**

备份可以输出 `METADATA_COMPLETE=true`，却没有契约要求的 image digest；后续把该 manifest 当作 G6/可复现发布证据会被错误放行。

**修复方向**

定义必需的 digest 字段并对每个目标 service fail closed；必要时通过 `docker image inspect` 补充 RepoDigest/镜像 ID 证据。增加缺失、空值、重复 service digest 的测试，并让测试 fixture 至少包含一个“无 digest 必为 gap/失败”的负例。

### H-04 HIGH — monitoring profile 未挂载 rules/dashboard，且 backend 尚无冻结指标定义，运行时监控未闭环

**问题**

Prometheus 配置要求加载 `/etc/prometheus/rules/*.yml`（`smart-city-drainage/docker/prometheus/prometheus.yml:6-7`），Grafana provider 要求从 `/var/lib/grafana/dashboards` 读取 dashboard（`smart-city-drainage/docker/grafana/provisioning/dashboards/hydraulic.yml:2-12`）。但实际 Compose 的 Prometheus 只挂载 `prometheus.yml` 和数据卷，Grafana 只挂载数据卷（`smart-city-drainage/docker-compose.yml:315-348`）。因此文档所述 monitoring profile 不会加载 11 条 recording rules、10 条 alerts 或 15 个 Grafana panels。

**执行证据**

```bash
python - <<'PY'
import json
import subprocess
compose = r'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/docker-compose.yml'
result = subprocess.run(['docker', 'compose', '-f', compose, '--profile', 'monitoring', 'config', '--format', 'json'], capture_output=True, text=True, check=True)
config = json.loads(result.stdout)
for name in ('prometheus', 'grafana'):
    mounts = [f'{item.get("source")}->{item.get("target")}' for item in config['services'][name].get('volumes', [])]
    print(f'{name}_mounts={mounts}')
PY
```

输出：

```text
prometheus_mounts=['...\\docker\\prometheus\\prometheus.yml->/etc/prometheus/prometheus.yml', 'prometheusdata->/prometheus']
grafana_mounts=['grafanadata->/var/lib/grafana']
```

执行精确名称扫描：

```bash
rg -n "simulation_queue_depth|simulation_oldest_queued_seconds|simulation_duration_seconds|forecast_nowcast_to_alert_seconds|hydraulic_event_outbox_backlog" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/app" || true
```

输出：无。`validate_monitoring.py` 只检查配置文本/JSON 中出现指标名、固定 UID 和 datasource/path 字符串（`smart-city-drainage/docker/prometheus/validate_monitoring.py:47-100`），不会发现 mount 缺失或 exporter 不发射指标。

**影响**

按当前 Compose 启动后，静态 dashboard/rules 文件不会成为实际监控状态；即使手工挂载文件，backend 无这些指标时仍无数据。冻结契约的可观测性目标未实际交付。

**修复方向**

由 integration owner 在 Compose 加只读 rules/dashboard/provisioning mounts；由 hydraulic-metrics owner 真实发射冻结指标。增加 Compose runtime smoke：Prometheus `/api/v1/rules` 应返回 21 条规则，Grafana search 应返回 `smart-city-hydraulic`，并验证 `/metrics` 包含全部 21 个指标。

### M-01 MEDIUM — 自定义 Redis `appenddirname` 在 AOF 转换时未传给临时 Redis

**问题**

备份允许并记录 `--redis-appenddirname`（`smart-city-drainage/scripts/backup.py:203-210,548-556`），恢复也用 manifest/参数选择目标 AOF 目录（`smart-city-drainage/scripts/restore.py:612-621`）。但临时转换进程的 `redis-server` 命令没有传 `--appenddirname`（`smart-city-drainage/scripts/restore.py:649-664`）；它只等待目标自定义目录中的 manifest。非默认目录会因此超时失败。

**执行证据**

```text
manifest_appenddirname=tenant-aof
awaits_tenant_manifest=True
passes_appenddirname_to_redis_server=False
```

对本地 `redis:7-alpine` 做无 TCP、无网络、退出即删除的定向检查，裸 `redis-server --appendonly yes` 生成：

```text
/tmp/restore-data/appendonlydir
/tmp/restore-data/appendonlydir/appendonly.aof.1.base.rdb
/tmp/restore-data/appendonlydir/appendonly.aof.1.incr.aof
/tmp/restore-data/appendonlydir/appendonly.aof.manifest
```

现有 Redis 测试的 fake runner 对 `run` 直接返回成功（`smart-city-drainage/scripts/backup_test.py:92-97`），未执行 shell 或验证目录。

**影响**

真实使用自定义 Redis AOF 目录的部署无法完成恢复；默认目录 happy path 通过不能证明参数化承诺成立。

**修复方向**

让临时 `redis-server` 显式使用同一 `appenddirname`，并增加真实 Redis 7 自定义目录的备份→恢复→读取 marker 测试。

### M-02 MEDIUM — Redis/Influx 远程快照在 `docker compose cp` 失败时不会清理

**问题**

Redis 和 Influx 都先在容器 `/tmp` 生成快照，再 `compose cp`，最后才 `rm`（`smart-city-drainage/scripts/backup.py:522-540,572-590`）。`cp` 抛错时控制流直接跳过 `rm`；没有 `try/finally`。这会留下完整 Redis RDB/Influx backup，也可能导致相同 backup ID 的后续重试与残留目录冲突。

**执行证据**

用会在 Redis `cp` 阶段抛错的 runner 运行真实 `OperationsBackup.run()`，输出：

```text
copy_failure_raised=True
remote_snapshot_cleanup_attempted=False
```

**影响**

失败备份可能把缓存/扇出数据或历史数据长期留在运行容器 `/tmp`，扩大数据暴露面，并产生难以诊断的陈旧快照。

**修复方向**

把远程生成、复制和清理放入受控 `try/finally`；成功时验证 copy，失败时 best-effort 清理并记录未清理告警。增加 Redis 与 Influx 的 copy-failure 清理测试。

## 3. 已执行检查

### 用户指定的两个测试

```bash
python "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/scripts/backup_test.py"
```

结果：

```text
Ran 11 tests in 1.592s
OK
```

该套件同时打印多条 fixture `BACKUP_STATUS=COMPLETE` / `RESTORE_STATUS=VERIFIED|RESTORED`；本次没有把其 fake-runner 结果当作 live 恢复证据。

```bash
python "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/docker/prometheus/validate_monitoring.py"
```

完整输出：

```text
MONITORING_VALIDATION=PASS
CONTRACT_METRICS=21
GRAFANA_PANELS=15
PROMQL_TARGETS=24
HIGH_CARDINALITY_ID_LABELS=0
```

此 PASS 仅证明静态字符串/JSON 契约；H-04 证明它不覆盖 Compose/runtime 装载。

### 其他定向检查

- PowerShell 参数转发：

```bash
pwsh -NoProfile -File "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/scripts/backup.ps1" -Python python --help
pwsh -NoProfile -File "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/scripts/restore.ps1" -Python python --help
```

  两者均退出 0 并显示对应 Python CLI usage；未发现 PowerShell `--help` 转发故障。

- Prometheus YAML 语法（只挂载 `prometheus.yml`、不挂载 rules）：

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network none --entrypoint /bin/promtool \
  -v "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/docker/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
  prom/prometheus:v2.48.0 check config /etc/prometheus/prometheus.yml
```

```text
Checking /etc/prometheus/prometheus.yml
 SUCCESS: /etc/prometheus/prometheus.yml is valid prometheus config file syntax
```

这不表示 rules 已加载。

## 4. 未运行与批准结论

本次没有重复最终全量门禁，也没有运行真实 PostGIS、完整 Compose G6、两 profile destroy/restore、legacy API/SSE、Influx live restore 或 Prometheus/Grafana runtime smoke。提供的实现摘要也明确记载 PostGIS/Influx live 与完整 G6/AC-8 未运行（`smart-city-drainage/docs/hydraulic-twin/implementation/operations.md:184-205,251-259`）。这些未运行项不能记为 PASS。

由于 H-01 至 H-04 均为 high，**`approved=false`**。至少应先修复恢复 hash 的表级绑定、认证 header 映射、image digest fail-closed，并完成 monitoring runtime 装载闭环；随后补相应负向/真实 Redis 测试并重新独立审查。
