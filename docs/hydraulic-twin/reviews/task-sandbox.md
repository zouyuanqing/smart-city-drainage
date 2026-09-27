# 任务队列与受限沙箱审查

- 模块：`task-sandbox`
- 审查日期：2026-09-25
- 结论：**不批准（approved = false）**
- 阻断原因：发现 6 个高风险问题；其中 SQL worker 当前缺少冻结契约要求的 `hydraulic.worker_slots` schema，且 queued/retry-wait 取消、子进程 finalize、受限文件系统、进程树清理和 cleanup-pending 持久化均存在可复现缺口。

## 审查范围与依据

已读取真实实现：

- `backend/app/services/hydraulic/tasks.py`
- `backend/app/services/hydraulic/sandbox.py`
- `backend/app/workers/simulation_worker.py`
- `backend/tests/hydraulic/test_tasks_sandbox.py`
- `docs/hydraulic-twin/implementation/task-sandbox.md`

已读取冻结/补充契约：

- `docs/hydraulic-twin/SOURCE_PRD.md:20,30`
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:721-816,1350-1464`
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:232-252`

当前目录没有 `.codegraph/`，因此按项目指令未使用 CodeGraph。

## 已执行检查

### 1. 任务指定的测试文件

精确命令：

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && python -m pytest tests/hydraulic/test_tasks_sandbox.py -q -W ignore::DeprecationWarning
```

输出：

```text
.............                                                            [100%]
13 passed in 1.00s
```

测试解释器精确检查：

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && python --version
```

输出：

```text
Python 3.14.4
```

### 2. worker slot schema 存在性

精确命令：

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage' && rg -n 'worker_slots' backend/app/models backend/alembic; code=$?; printf 'rg_exit=%s\n' "$code"
```

输出：

```text
rg_exit=1
```

即当前真实 ORM/Alembic 文件没有 `worker_slots` 定义或引用。

### 3. 小型定向诊断

在同一 backend 目录用 `python - <<'PY'` 执行了内存 repository/worker/sandbox 诊断；完整命令见本报告末尾。关键输出：

```text
queued_cancel: cancelling claim_after_cancel= None
subprocess_exit_zero: retry_wait retry_wait RESULT_PERSIST_FAILED
sandbox_subprocess_escape: returncode= 0 outside_exists= True
stream_limit_during_write: observed_bytes= 4096 limit= 16
```

另执行父进程先退出、子进程继续运行的 Windows 进程树诊断，输出：

```text
parent_exit_zero= 0 child_pid= 68240 child_alive_after_kill= True
```

诊断中的遗留子进程已由诊断脚本显式 `taskkill` 清理。

## 阻断与高风险发现

### H1. queued / retry_wait 取消会永久卡在 `cancelling`

**问题**

状态机允许 `queued` 和 `retry_wait` 进入 `cancelling`，但 claim 只选择 `queued` 或到期的 `retry_wait`；`mark_cancelled` 又强制要求已有 `TaskClaim`。因此尚未 claim 的任务没有任何组件能完成 `cancelling -> cancelled`，会永久卡住；retry_wait 任务同样没有活跃 worker/claim 可调用 `mark_cancelled`。

这直接违反冻结状态表中 `queued -> cancelling -> cancelled` 以及 `leased/running/retry_wait` 取消闭合要求（`IMPLEMENTATION_CONTRACT.md:1384-1392`）。

**证据**

- `backend/app/services/hydraulic/tasks.py:505-529`：queued/retry_wait 被转为 `cancelling`。
- `backend/app/services/hydraulic/tasks.py:974-987`：claim 候选只有 queued/retry_wait，不含 cancelling。
- `backend/app/services/hydraulic/tasks.py:1096-1111`、`1276-1285`：`mark_cancelled` 必须由 claim 调用。
- `backend/app/services/hydraulic/tasks.py:1609-1643`：SQL repository 具有同样结构。
- `backend/tests/hydraulic/test_tasks_sandbox.py:199-241`：只覆盖已 claim 的 running 取消，没有 queued/retry_wait 取消。
- 定向诊断输出：`queued_cancel: cancelling claim_after_cancel= None`。

**修复要求**

增加无活跃 attempt 的取消终结路径：在同一事务中原子完成 `cancelling -> cancelled`、确认无 slot/attempt 占用并完成必要 cleanup；或让专门 worker 可靠 claim/finalize cancelling。补充 queued 与 retry_wait 两种回归测试，并验证重复 cancel 幂等。

### H2. SQL worker 依赖的冻结表在真实 schema 中不存在，生产 claim 必然失败

**问题**

SQL claim 首先查询 `hydraulic.worker_slots`，但当前 ORM 和 Alembic 迁移没有该表。真实数据库执行第一条 slot 查询就会报 `UndefinedTable`，所以持久队列、lease/fencing 和崩溃恢复无法启动。

冻结契约明确要求两行固定 slot、general/nowcast lane、CHECK、seed 和同事务 claim/release（`IMPLEMENTATION_CONTRACT.md:721-745`）。实施摘要也承认该依赖，但当前真实代码库并未交付它。

**证据**

- `backend/app/services/hydraulic/tasks.py:1443-1457`：直接查询 `hydraulic.worker_slots`。
- `backend/app/services/hydraulic/tasks.py:1514-1537`、`1743-1812`、`1930-1999`：后续持续更新/恢复/释放该表。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:721-745`：冻结表与 seed 规范。
- `backend/alembic/versions/0002_hydraulic_schema.py:1021-1262` 创建 simulations，`:1263-1350` 创建 simulation_attempts，但没有 worker_slots；`backend/app/models/hydraulic_models.py:1234-1615` 也没有对应 ORM。
- 精确检查输出：`rg_exit=1`。
- `backend/tests/hydraulic/test_tasks_sandbox.py` 没有 live PostgreSQL slot claim/recovery 集成测试。

**修复要求**

由 schema owner 在同一冻结迁移中增加 `hydraulic.worker_slots`、两行 seed、完整 CHECK/绑定与 downgrade，并增加真实 PostgreSQL 的并发 claim、cancel release、heartbeat 和 recovery 集成测试；在该依赖交付前，本模块不能宣称可部署。

### H3. `SubprocessSimulationExecutor` 的成功子进程永远不能完成任务

**问题**

子进程退出后 `wait_for_process()` 无条件返回 `result_committed=False`；worker 随即把零退出码解释为 `RESULT_PERSIST_FAILED` 并进入 retry。没有 finalizer hook 能把真实 DB commit 状态带回该 concrete executor，因此该执行器实现的 solver 最多重试两次后必然 failed。

这与冻结契约“进程退出不得 complete，必须原子 finalize”一致，但当前类没有可完成原子 finalize 的接口，形成永久不可完成路径。

**证据**

- `backend/app/workers/simulation_worker.py:219-224`：所有 subprocess 结果固定 `result_committed=False`。
- `backend/app/workers/simulation_worker.py:525-535`：false 被无条件映射为 `RESULT_PERSIST_FAILED`。
- `backend/app/workers/simulation_worker.py:541-547`：只有 `complete(..., result_committed=True)` 才能成功。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:814,1350,1395`：结果验证和发布必须原子完成。
- 定向诊断中真实 `sys.executable -c 'pass'` 返回 0，输出仍为：`subprocess_exit_zero: retry_wait retry_wait RESULT_PERSIST_FAILED`。
- `backend/tests/hydraulic/test_tasks_sandbox.py:391-415` 只测试 kill，不测试 subprocess 成功 finalize。

**修复要求**

为 concrete subprocess executor 提供与 attempt/fencing 绑定的 durable finalizer handle，在同一事务验证并发布 frame/summary/quality 后才返回成功；增加“子进程 0 + 已提交结果 -> completed”和“0 + 未提交/不完整结果 -> 两次 retry 后 failed”的集成测试。

### H4. 当前“沙箱”不是子进程的文件系统安全边界，大小限制也可在写入期间被突破

**问题**

路径校验只保护主动调用 `TaskSandbox.resolve()` 的 Python 调用方。concrete executor 把绝对 sandbox 路径放入环境并把 cwd 设为该目录，却没有 Windows restricted token/ACL、POSIX mount/rlimit/cgroup 或其他 OS 强制边界；子进程仍可使用自己的文件 API 任意读写进程账户有权限的位置。

此外 `open_write()` 直接把原始文件对象交给调用者，只在退出 context 后检查大小；worker 最多每秒轮询一次。写入者在检查前可以远超 2 GiB，造成磁盘耗尽。定向诊断中，16-byte sandbox 内已刷新文件达到 4096 bytes，且子进程成功在 sandbox 外创建文件。

**证据**

- `backend/app/workers/simulation_worker.py:173-203`：继承环境、暴露绝对路径、设置 cwd；没有 OS 文件隔离。
- `backend/app/services/hydraulic/sandbox.py:207-226`：保护仅在显式 `resolve()` 时生效。
- `backend/app/services/hydraulic/sandbox.py:292-319`：流式写入在 context 退出后才检查大小。
- `backend/app/workers/simulation_worker.py:486-505`：运行期仅轮询 `usage_bytes()`。
- `docs/hydraulic-twin/SOURCE_PRD.md:30` 要求故障隔离且无任意文件读写。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1355-1358,1459-1464` 要求输入/输出仅在 task sandbox 并有固定上限。
- 定向诊断输出：`sandbox_subprocess_escape: returncode= 0 outside_exists= True`；`stream_limit_during_write: observed_bytes= 4096 limit= 16`。
- 现有测试 `backend/tests/hydraulic/test_tasks_sandbox.py:417-475` 只验证 wrapper 路径和退出时大小行为，没有恶意/真实子进程逃逸或并发写限制。

**修复要求**

在 OS 层建立真正只允许访问 attempt root 的文件边界，并使用最小环境变量 allowlist；流式写入应使用可强制截断/拒绝的受限 writer，而非事后检查。增加子进程尝试绝对路径写入、链接逃逸、写入期间超限和并发写的集成测试。

### H5. Windows 未使用 Job Object，父进程先退出时 kill 会直接遗留后代进程

**问题**

冻结契约要求 Windows Job Object。实现仅设置 `CREATE_NEW_PROCESS_GROUP` 并调用 `taskkill /T /F`；若根进程已经退出，`_kill_tree_sync()` 在任何 kill 之前直接 return。solver 可以在退出前启动后台子进程，父进程返回后 timeout/cancel/finally cleanup 均无法发现或终止该子进程。`taskkill` 失败时的 fallback 也只杀根 PID。

这破坏 timeout/cancel 的进程树清理保证，并可能让旧 attempt 的 solver 继续运行、占用资源或写文件。

**证据**

- `backend/app/workers/simulation_worker.py:123-144`：根进程 `poll() is not None` 时立即返回；fallback 只 `self._process.kill()`。
- `backend/app/workers/simulation_worker.py:182-188`：只创建 process group，不是 Job Object。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1357` 明确要求 Windows Job Object；`:1428-1430,1464` 要求旧 worker/process tree 不得继续写并 finally cleanup。
- `backend/tests/hydraulic/test_tasks_sandbox.py:391-415` 所谓 process-tree 测试只启动一个 Python sleeper，没有创建或验证后代。
- 定向诊断输出：父进程返回 0 后调用 `kill_process_tree()`，`child_alive_after_kill= True`。

**修复要求**

Windows 使用 Job Object，并在 job 级别配置 kill-on-close、禁止 breakaway 和适当资源限制；POSIX 保持独立 process group 且不要以根进程已退出为提前成功条件。测试必须真实创建孙进程、覆盖父先退出/后代逃逸，并验证 PID 已消失且句柄可回收。

### H6. cleanup 失败没有持久化，生产 repository 也没有对应 hook 或 sweeper 调度

**问题**

sandbox 清理失败时，worker 只把内存结果 `cleanup_pending=True` 并写日志；`record_cleanup_pending` 是通过 `getattr` 获取的可选 hook，既不在 `TaskRepository` protocol 中，也未由 `SessionSimulationTaskRepository` 实现。数据库中没有 cleanup-pending 记录，任务此时通常已经是 terminal/lease 已释放；重启后的 recovery 不会返回该目录。`TaskSandbox.cleanup_stale()` 虽存在，但真实 worker 没有调用它。

这违反冻结契约“cleanup 失败写 cleanup_pending，由 sweeper 重试，不重跑 solver”，会导致最多 2 GiB 的目录永久孤儿，且没有可审计重试载体。

**证据**

- `backend/app/workers/simulation_worker.py:414-431`：只记录日志和可选 hook。
- `backend/app/services/hydraulic/tasks.py:787-858`：`TaskRepository` protocol 没有 cleanup-pending 方法。
- `backend/app/services/hydraulic/tasks.py:2052-2197`：生产 session wrapper 同样没有该方法。
- `backend/app/services/hydraulic/sandbox.py:403-451`：定义了 `cleanup_stale()`，但搜索真实 app 调用点只找到定义，没有 worker/sweeper 调用。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:816,1428,1464`：cleanup_pending 与 sweeper 是冻结要求。
- `backend/tests/hydraulic/test_tasks_sandbox.py:303-343,478-500` 只验证成功清理，没有注入 cleanup 失败并验证持久化/重试。

**修复要求**

在 attempt/独立 cleanup artifact 上持久化 workspace、状态、错误与重试时间；提供协议化 repository 操作和独立 sweeper，崩溃后继续清理但不重跑 solver。增加 cleanup 抛错、进程重启、sweeper 成功/再次失败的测试。

## 中风险发现

### M1. worker 启动前没有冻结契约要求的可用磁盘门禁

**问题**

worker 直接 recovery/claim/创建 sandbox 并启动 solver，没有检查可用磁盘是否至少为 `max(1 GiB, 2*job_output_limit)`。在低磁盘环境会先占 durable slot，再以 I/O 失败重试，无法在执行前 fail closed。

**证据**

- `backend/app/workers/simulation_worker.py:315-370`：进入执行前没有 `disk_usage`/quota 检查。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1463`：明确要求 worker 启动前检查可用磁盘。
- 指定测试没有覆盖低磁盘场景。

**修复要求**

在 claim/启动前加入可注入、可测试的磁盘门禁，使用冻结 output limit 计算阈值；覆盖阈值相等、不足和恢复后通过。

## 缺测归纳

现有 13 个测试通过，但未覆盖以下会失败的关键路径：

1. queued 与 retry_wait cancel 的最终状态；
2. live PostgreSQL `worker_slots` schema、两 worker 并发 claim/cancel/recovery；
3. concrete subprocess 成功后的 durable finalize；
4. 子进程向 sandbox 外写、写入期间超限及并发写；
5. 真正后代/孙进程以及父先退出后的 kill；
6. cleanup 抛错后的持久化、重启恢复和 sweeper；
7. 启动前低磁盘门禁。

## 未运行项

- 按要求未重复最终全量门禁，也未运行完整 `tests/hydraulic` suite。
- 未运行 live PostgreSQL/PostGIS/Timescale；因此本报告没有把 SQL 并发行为记为通过。
- 未运行目标 Python 3.11 pytest；本次 pytest 实际解释器为 Python 3.14.4。
- 未运行真实 EPA SWMM 或 600 秒真实超时。

## 积极项

- 内存状态机的默认 attempt=3（两次 retry）、单调 progress、fencing 检查和通用路径组件校验逻辑清晰。
- 定向测试确实覆盖重复提交、hash 冲突、generation、两次 retry、单调 progress、运行中取消、超时 callback kill、基础 traversal/symlink 和成功 finally cleanup。
- 上述积极项不足以抵消 6 个高风险实现/集成缺口。

## 定向诊断完整命令

<details>
<summary>queued cancel、subprocess finalize、sandbox 逃逸与流式限制</summary>

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && python - <<'PY'
import asyncio
import hashlib
import sys
import tempfile
from pathlib import Path
from uuid import UUID

from app.services.hydraulic.sandbox import SandboxLimitExceeded, TaskSandbox
from app.services.hydraulic.tasks import InMemoryTaskRepository, TaskKind
from app.workers.simulation_worker import SimulationWorker, SubprocessSimulationExecutor

FP = hashlib.sha256(b'diagnostic').hexdigest()
USER = UUID('00000000-0000-0000-0000-000000000001')

async def main():
    queued_repo = InMemoryTaskRepository()
    queued = await queued_repo.submit(kind=TaskKind.simulation, user_id=USER, idempotency_key='queued-cancel', request_fingerprint=FP)
    cancelling = await queued_repo.request_cancel(queued.resource.id)
    reclaims = await queued_repo.claim_next('replacement', kind=TaskKind.simulation)
    print('queued_cancel:', cancelling.status.value, 'claim_after_cancel=', reclaims)

    with tempfile.TemporaryDirectory() as td:
        success_repo = InMemoryTaskRepository()
        submitted = await success_repo.submit(kind=TaskKind.simulation, user_id=USER, idempotency_key='subprocess-success', request_fingerprint=FP)
        worker = SimulationWorker(
            success_repo,
            SubprocessSimulationExecutor(lambda _claim, _workspace: [sys.executable, '-c', 'pass']),
            sandbox_root=Path(td) / 'success',
            worker_id='diagnostic-success',
            poll_seconds=0.01,
            heartbeat_seconds=0.02,
            recover_on_start=False,
        )
        result = await worker.run_once()
        state = await success_repo.get(submitted.resource.id)
        print('subprocess_exit_zero:', result.reason, state.status.value, state.last_retry_error_code)

        outside = Path(td) / 'outside.txt'
        escape_repo = InMemoryTaskRepository()
        await escape_repo.submit(kind=TaskKind.simulation, user_id=USER, idempotency_key='escape-check', request_fingerprint=FP)
        claim = await escape_repo.claim_next('escape-worker', kind=TaskKind.simulation)
        assert claim is not None
        await escape_repo.heartbeat(claim)
        sandbox = TaskSandbox(Path(td) / 'escape-root', claim.task_id, claim.attempt_no)
        executor = SubprocessSimulationExecutor(
            lambda _claim, _workspace: [sys.executable, '-c', f'from pathlib import Path; Path({str(outside)!r}).write_text("escaped")']
        )
        execution = await executor.start(claim, sandbox).wait()
        sandbox.cleanup()
        print('sandbox_subprocess_escape: returncode=', execution.return_code, 'outside_exists=', outside.exists())

    with tempfile.TemporaryDirectory() as td:
        sandbox = TaskSandbox(td, UUID('00000000-0000-0000-0000-000000000099'), 1, max_bytes=16, input_max_bytes=16)
        observed = None
        try:
            with sandbox.open_write('stream.bin', max_bytes=16) as stream:
                stream.write(b'x' * 4096)
                stream.flush()
                observed = sandbox.resolve('stream.bin').stat().st_size
        except SandboxLimitExceeded:
            pass
        print('stream_limit_during_write: observed_bytes=', observed, 'limit=', sandbox.max_bytes)
        sandbox.cleanup()

asyncio.run(main())
PY
```

输出：

```text
queued_cancel: cancelling claim_after_cancel= None
subprocess_exit_zero: retry_wait retry_wait RESULT_PERSIST_FAILED
sandbox_subprocess_escape: returncode= 0 outside_exists= True
stream_limit_during_write: observed_bytes= 4096 limit= 16
```
</details>

<details>
<summary>Windows 父进程先退出后的后代清理</summary>

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && python - <<'PY'
import asyncio
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID

from app.services.hydraulic.sandbox import TaskSandbox
from app.services.hydraulic.tasks import InMemoryTaskRepository, TaskKind
from app.workers.simulation_worker import SubprocessSimulationExecutor

async def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        pid_file = root / 'child.pid'
        repo = InMemoryTaskRepository()
        await repo.submit(
            kind=TaskKind.simulation,
            user_id=UUID('00000000-0000-0000-0000-000000000001'),
            idempotency_key='orphan-process-check',
            request_fingerprint=hashlib.sha256(b'orphan').hexdigest(),
        )
        claim = await repo.claim_next('orphan-worker', kind=TaskKind.simulation)
        assert claim is not None
        sandbox = TaskSandbox(root / 'sandbox', claim.task_id, claim.attempt_no)
        code = (
            'import pathlib,subprocess,sys,time; '
            f'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
            f'pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); '
            'time.sleep(0.2)'
        )
        handle = SubprocessSimulationExecutor(lambda _c, _w: [sys.executable, '-c', code]).start(claim, sandbox)
        execution = await handle.wait()
        for _ in range(50):
            if pid_file.exists():
                break
            await asyncio.sleep(0.02)
        child_pid = int(pid_file.read_text())
        await handle.kill_process_tree()
        listing = subprocess.run(
            ['tasklist', '/FI', f'PID eq {child_pid}', '/NH', '/FO', 'CSV'],
            capture_output=True,
            text=True,
            check=False,
        )
        child_alive = str(child_pid) in listing.stdout
        print('parent_exit_zero=', execution.return_code, 'child_pid=', child_pid, 'child_alive_after_kill=', child_alive)
        if child_alive:
            subprocess.run(['taskkill', '/PID', str(child_pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        sandbox.cleanup()

asyncio.run(main())
PY
```

输出：

```text
parent_exit_zero= 0 child_pid= 68240 child_alive_after_kill= True
```
</details>
