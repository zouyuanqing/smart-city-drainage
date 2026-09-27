# 任务队列与受限沙箱实施摘要

## 范围与依据

本实现仅修改本任务 owned paths，并按要求额外写入本摘要；未修改 ORM/Alembic、API、配置、依赖或其他 worker。

实施依据：

- FR-3 要求队列进度、取消、10 分钟超时、最多两次重试、幂等、并发 2、崩溃恢复，以及临时目录、20 MiB 输入上限、路径穿越防护和清理：`docs/hydraulic-twin/SOURCE_PRD.md:20`。
- 唯一 durable-job 状态机、持久 `next_attempt_at`、lease/fencing、进度和 retry 语义：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1376-1446`。
- 两个固定 solver slot、general/nowcast lane 和 slot 释放规则：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:721-745`。
- attempt-scoped frame、发布指针和完成事务：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:746-816`。
- 沙箱目录、20 MiB/2 GiB 上限、路径与 symlink/junction 防护、finally cleanup：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1448-1464`。
- 24 小时幂等 generation 与重复/冲突语义：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:231-251`。

## 真实改动

### 1. 任务状态机、幂等与持久 repository

`backend/app/services/hydraulic/tasks.py`：

- 定义唯一 canonical 状态集、attempt/fencing/lease 字段和 transition policy；`progress` 仅允许 running 状态单调推进到 0..99，100 只在完成提交时产生：`tasks.py:292`、`tasks.py:525`。
- 按任务 kind 固定 retryable error surface，并固定 general `[10s, 60s]`、nowcast `[5s, 15s]`；attempt 1/2 可重试，attempt 3 exhausted 后进入 failed：`tasks.py:36`、`tasks.py:123`、`tasks.py:534`。
- 实现 24 小时幂等 registry：同 scope/key/user/fingerprint 重放原 resource，不同 fingerprint 抛 `IDEMPOTENCY_KEY_REUSED`，过期后 generation 递增：`tasks.py:719`。
- 提供可执行的内存 repository，覆盖 submit/claim/heartbeat/progress/cancel/timeout/retry/complete/recover 及两个固定 lane，用于确定性测试：`tasks.py:862`。
- 提供 PostgreSQL/SQLAlchemy 实现：
  - 幂等事务使用 `pg_advisory_xact_lock`，支持 active replay、冲突和过期 generation：`tasks.py:1309`、`tasks.py:1344`。
  - claim 使用 `FOR UPDATE SKIP LOCKED`，同时绑定 `hydraulic.worker_slots`、递增 fencing token、创建 attempt：`tasks.py:1434`、`tasks.py:1448`。
  - heartbeat、取消、失败、超时、崩溃恢复、完成均带 lease/fencing 条件；恢复将过期 attempt 标记 discarded 并释放 slot：`tasks.py:1552`、`tasks.py:1610`、`tasks.py:1744`。
  - 完成前验证 frame count、首尾时间、registration count、frame/quality hash，并原子发布 attempt 指针：`tasks.py:1699`、`tasks.py:2019`。
- 提供每次控制操作独立 session/transaction 的 worker-facing wrapper，避免 worker 长期持有 SQL transaction：`tasks.py:2052`。

### 2. worker、取消、超时与进程树 kill

`backend/app/workers/simulation_worker.py`：

- `SimulationWorker` 只 claim general simulation lane；取消检查不超过 1 秒，heartbeat 和 deadline 独立调度：`simulation_worker.py:278`、`simulation_worker.py:459`。
- cancel request 先释放 slot；worker 观察到 cancelling 后终止执行并落 `cancelled`。deadline 到达时落 `SIMULATION_TIMEOUT`/`timed_out`；取消先被持久化时取消优先：`simulation_worker.py:341`、`simulation_worker.py:478`。
- 外部 solver 使用 argv list、`shell=False`、attempt cwd 和独立环境；Windows 超时/取消执行 `taskkill /T /F`，POSIX 使用独立 process group 并 `killpg(SIGKILL)`：`simulation_worker.py:98`、`simulation_worker.py:113`、`simulation_worker.py:130`、`simulation_worker.py:148`。
- worker 启动时恢复过期 lease，清理确定性 attempt 路径；正常、失败、取消、超时和 task cancellation 均在 finally 终止 handle 并清理 sandbox：`simulation_worker.py:316`、`simulation_worker.py:341`。
- 运行期间轮询 workspace 使用量；超限终止为不可重试 `OUTPUT_LIMIT_EXCEEDED`，路径/reparse 异常 fail closed：`simulation_worker.py:459`。

### 3. 受限文件沙箱

`backend/app/services/hydraulic/sandbox.py`：

- 默认 20 MiB input、2 GiB workspace，并同时定义 64 MiB frame、8 GiB output 容量常量：`sandbox.py:21`。
- 每次任务使用独立 `<sandbox_root>/<simulation_id>/attempt-<n>`，已存在目录拒绝复用；attempt 只允许 1..3：`sandbox.py:139`。
- portable 相对路径校验拒绝 absolute、`..`、backslash/drive/UNC、ADS、NUL、非规范路径、Windows reserved name、尾随点/空格：`sandbox.py:93`。
- 每次 resolve 检查 root containment 和已有 symlink/junction/reparse point；usage 统计不跟随链接：`sandbox.py:204`、`sandbox.py:231`。
- `write_bytes`/stream writer 同时执行单 input/file 和 aggregate workspace 限制；超限删除本次未完成文件，不删除原有文件：`sandbox.py:260`、`sandbox.py:294`。
- context manager/finally cleanup、确定路径 crash cleanup 和 24 小时 stale cleanup 均不跟随链接：`sandbox.py:322`、`sandbox.py:368`、`sandbox.py:405`。

### 4. 测试

`backend/tests/hydraulic/test_tasks_sandbox.py` 新增 13 个测试，覆盖：

- 重复提交、fingerprint 冲突、24 小时后 generation：`test_tasks_sandbox.py:66-99`。
- 两次 retry、10/60 秒 backoff、attempt 3 exhausted：`test_tasks_sandbox.py:101-158`。
- 单调 progress、总 slot=2、nowcast reserved slot 禁借：`test_tasks_sandbox.py:160-198`。
- completed/cancelling 竞态：`test_tasks_sandbox.py:200-251`。
- 崩溃恢复、due retry、旧 fencing 拒绝、workspace 清理：`test_tasks_sandbox.py:253-303`。
- timeout kill 和 finally cleanup：`test_tasks_sandbox.py:305-346`。
- worker cancel 竞态：`test_tasks_sandbox.py:348-390`。
- Windows/POSIX subprocess 进程树终止路径中的实际子进程 kill：`test_tasks_sandbox.py:392-416`。
- traversal/absolute/drive/UNC/ADS/reserved-name、symlink escape、大小上限、attempt 隔离和异常 finally cleanup：`test_tasks_sandbox.py:418-503`。

## 本轮实际执行的检查

### 通过

```text
$ cd backend && python -m pytest tests/hydraulic/test_tasks_sandbox.py -q -W ignore::DeprecationWarning
.............                                                            [100%]
13 passed in 1.01s
```

```text
$ cd backend && python -m pytest tests/hydraulic -q -W ignore::DeprecationWarning
........................................................................ [ 45%]
........................................................................ [ 90%]
................                                                         [100%]
160 passed in 1.52s
```

```text
$ cd backend && /c/Users/zyq/AppData/Roaming/uv/python/cpython-3.11.15-windows-x86_64-none/python.exe -m py_compile app/services/hydraulic/tasks.py app/services/hydraulic/sandbox.py app/workers/simulation_worker.py tests/hydraulic/test_tasks_sandbox.py
(exit 0, no output)
```

```text
$ git -C . diff --check -- backend/app/services/hydraulic/tasks.py backend/app/services/hydraulic/sandbox.py backend/app/workers/simulation_worker.py backend/tests/hydraulic/test_tasks_sandbox.py
(exit 0, no output)
```

### 未运行/环境限制

- pytest 实际运行环境是 Python 3.14.4；目标 Python 3.11 可执行文件存在，但没有 pytest：

```text
$ /c/Users/zyq/AppData/Roaming/uv/python/cpython-3.11.15-windows-x86_64-none/python.exe -m pytest tests/hydraulic/test_tasks_sandbox.py -q
C:\Users\zyq\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe: No module named pytest
```

- 当前 Python 3.14 和 Python 3.11 环境均未安装 Black/isort/Flake8；以下检查没有通过结果，不能记为 PASS：

```text
$ python -m black --check ...
C:\Python314\python.exe: No module named black
$ python -m isort --check-only ...
C:\Python314\python.exe: No module named isort
$ python -m flake8 ...
C:\Python314\python.exe: No module named flake8
```

- 未运行 live PostgreSQL/PostGIS/Timescale、`FOR UPDATE SKIP LOCKED` 并发集成、真实 EPA SWMM、10 分钟真实 timeout 或 Docker/G3 gate；本轮只证明 Python 单元/回归测试、Python 3.11 语法编译和 whitespace 检查。

## 风险与集成依赖

1. **worker slot schema 依赖**：冻结契约要求 `hydraulic.worker_slots`，本实现 SQL repository 已按该表 fail-closed 使用，但本轮读取的 `backend/app/models/hydraulic_models.py` 和 `backend/alembic/versions/0002_hydraulic_schema.py` 中没有该表/ORM。当前 live PostgreSQL 运行会在 slot 查询处失败，直到 schema owner 交付两行固定 slot；因不在 owned paths，本轮未越界修改 schema。
2. **真实 solver finalizer 依赖**：solver/result adapter 必须先以当前 attempt/fencing token 原子持久化 frame registrations、summary、quality report；随后 worker completion 才会验证并移动 `published_attempt_id`。本模块不实现数值 solver 或 frame 内容。
3. **cleanup pending 持久化依赖**：worker 会调用可选的 repository `record_cleanup_pending` hook；当前 owned schema 没有独立 cleanup artifact/sweeper 表。正常 finally、过期 lease 和 stale attempt cleanup 已实现，但生产 cleanup-failure 重试仍需 schema/sweeper owner 提供持久化载体。
4. **环境验证差距**：测试运行于 Python 3.14.4，而项目目标为 Python 3.11；Python 3.11 `py_compile` 已通过，但没有可用的 Python 3.11 pytest/Black/isort/Flake8 环境。
5. **并行工作树**：仓库原本已有大量其他模块的未提交改动；本任务未修改、覆盖、提交或推送这些文件。
