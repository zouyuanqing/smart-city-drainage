# 求解器接口与 SWMM 适配独立审查

- **模块**：`solver-contract`
- **审查日期**：2026-09-25
- **审查方式**：只读复核冻结契约、真实实现、fake-SWMM 测试与实现报告；仅运行指定定向测试和轻量诊断
- **结论**：**不批准（NOT APPROVED）**
- **阻断原因**：6 个 High、3 个 Medium。指定测试虽通过，但未覆盖且不能消除下述生产阻断。

## 1. 审查依据

已读取：

- 冻结规格：`docs/hydraulic-twin/PRD_SUPPLEMENT.md`、`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md`
- 实现：`backend/app/services/hydraulic/solvers/base.py`
- 实现：`backend/app/services/hydraulic/solvers/swmm_adapter.py`
- 测试：`backend/tests/hydraulic/test_solver_contract.py`
- 实现摘要：`docs/hydraulic-twin/implementation/solver-contract.md`
- 持久 phase 约束：`backend/app/models/hydraulic_models.py:1251-1254`

关键冻结要求：提交时 probe 并冻结 solver（`IMPLEMENTATION_CONTRACT.md:1313-1320`）；frame 必须流式写、不得构造全量 list（`:1339-1350`）；Windows 必须使用 Job Object 并有无孤儿测试（`:2152-2157`）；600 s 覆盖 materialize、solver、frame serialization 和提交前校验（`PRD_SUPPLEMENT.md:169-173`）；`.rpt/.out` parser 必须绑定 engine version（`IMPLEMENTATION_CONTRACT.md:1352-1359`）；不得在内存或沙箱积累全部帧（`PRD_SUPPLEMENT.md:158-163`）。

## 2. 已执行检查

### 2.1 指定定向测试（PASS）

执行：

```text
PYTHONPATH="C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/.venv/Scripts/python.exe" -m pytest "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/tests/hydraulic/test_solver_contract.py" -q
```

输出：

```text
.........                                                                [100%]
9 passed, 448 warnings in 0.10s
```

该结果只证明这 9 个 fake-SWMM 场景通过；它不覆盖真实 EPA `.out`、Windows Job Object、全量帧内存、parser/engine 强绑定或完整 attempt deadline。

### 2.2 轻量诊断

均使用上述 `.venv` Python 与相同 `PYTHONPATH`，以内联 fake SWMM 脚本执行。

1. 输入 manifest 可变性与 registry probe：

```text
mutable_manifest_hash_changed=True before=6e171e0a78ce after=296774af73c7
unprobed_selected=py_mw reason=no_probe_method
```

2. parser 绑定、预先取消与独立 capability report：

```text
unbound_parser_probe_available=True parser=unbound-parser-v1
precancelled_error=SIMULATION_CANCELLED runner_calls=1 process_killed=True
standalone_report_fallback=py_mw default_registry_size=0
```

3. 解析阶段不服从 `timeout_seconds`：

```text
timeout_seconds=0.01 elapsed=0.085s success=verified frames=2
```

4. parser 提供的 frame payload hash 不一致仍被接受：

```text
mismatched_frame_hash_accepted=True result_status=verified
```

5. 搜索 Windows Job Object 实现：

```text
rg -n -i "job object|createjobobject|assignprocesstojobobject|job_object" smart-city-drainage/backend/app smart-city-drainage/backend/tests/hydraulic
```

输出：无匹配。

6. 搜索具体 SWMM output parser：

```text
rg -n "SwmmOutputParser|simulation\.out|\.out parser|parse.*out|SWMM.*parser|engine_version" smart-city-drainage/backend/app smart-city-drainage/backend/tests/hydraulic
```

输出只命中 `SwmmOutputParser` 协议、`test_solver_contract.py` 的 `FakeParser`，以及 INP parser；没有生产 `.rpt/.out` parser 实现。

## 3. Findings

### High 1 — Windows 取消/超时没有按冻结契约使用 Job Object

**证据**

- `swmm_adapter.py:116-139` 的 Windows 清理使用 `taskkill /T /F`，失败后只杀 leaf process。
- `swmm_adapter.py:166-173` 只创建 `CREATE_NEW_PROCESS_GROUP`；该 flag 本身不提供 kill-on-close 进程树生命周期。
- `swmm_adapter.py:836-839` 又静默吞掉所有 kill 异常，调用方无法知道可能留下孤儿。
- 冻结契约 `IMPLEMENTATION_CONTRACT.md:1357`、`:2157` 明确要求 Windows Job Object 与无孤儿测试。
- `rg` 全仓搜索 Job Object 无匹配；`test_solver_contract.py:283-303` 只验证 fake process 的 `killed` 布尔值。
- 实现摘要 `solver-contract.md:104-108` 也承认该差距。

**影响**

`taskkill` 不可用、权限不足、自身失败或清理竞态时，SWMM 及其后代可能继续运行，直接违反 Windows 无孤儿门禁；“Windows 可运行”不能因此宣称通过。

**建议**

实现 Windows Job Object（kill-on-close/TerminateJobObject），在启动后、任何 await/取消窗口前可靠绑定进程树；`taskkill` 只能作为受控后备且失败必须可见。增加真实 Windows 父子进程测试，验证 timeout、task cancel、worker cancellation 后父子均退出。

### High 2 — `timeout_seconds`/`deadline_at` 只约束子进程等待，不约束整个 attempt；预取消仍会启动 SWMM

**证据**

- `swmm_adapter.py:492-527` 在任何 `control.check()` 之前先 probe、写 INP、报告进度并启动进程。
- 取消检查直到 `swmm_adapter.py:795-800` 的 `_wait_for_process()` 才发生。
- `swmm_adapter.py:780-791` 在进程已经启动后才建立 timeout deadline。
- 进程退出后，`swmm_adapter.py:593-686` 的 parser、逐帧 sink write、summary/finalize 阶段没有 deadline/cancel 检查。
- 冻结契约 `PRD_SUPPLEMENT.md:171-173` 要求 600 s 覆盖 materialize、solver、frame serialization 与提交前校验。
- 定向诊断：预先设置的 cancel event 仍产生 `runner_calls=1`；`timeout_seconds=0.01` 的解析任务运行 `0.085s` 后仍返回 `verified`。

**影响**

已取消或已超时任务仍可执行外部进程；慢 parser、慢 sink 或 finalize 可无限延长 attempt，突破 600 s 门禁并占用 worker 槽。由于本模块又缺少 Job Object，该生命周期缺口会放大孤儿进程风险。

**建议**

在 `run()` 入口冻结绝对 monotonic/UTC deadline，并在任何文件写入/进程启动前检查；用覆盖 probe、materialize、process、parse、write、finalize 的统一 supervisor/race 控制取消与超时。解析和每次 sink await 也要检查；退出前必须确认进程树已清理。

### High 3 — SWMM run 明确构造并保留完整 frame list，直接违反流式契约

**证据**

- `base.py:579-585` 自身文档写明 solver 不得先构造完整 frame list。
- `swmm_adapter.py:594` 创建 `frames: list[SimulationFrame]`。
- `swmm_adapter.py:622-629` 每帧写 sink 后仍追加到该 list。
- `swmm_adapter.py:631-639` 完成后遍历整个 list，`:668-669` 再读取首尾帧。
- 冻结契约 `IMPLEMENTATION_CONTRACT.md:1347` 与 `PRD_SUPPLEMENT.md:161-163` 明确禁止全量内存积累。
- `test_solver_contract.py:211-254` 只有 2 帧，未检查 solver 内部保留量或峰值内存。

**影响**

结果规模越大，worker 内存随全部帧线性增长；即使 sink 已流式持久化，adapter 仍复制持有整批 `SimulationFrame`，违背 24 h/289 帧及更大场景的内存边界。

**建议**

只保留 `frame_count`、`first_frame`、`last_frame`、warning/failed 聚合等 O(1) 状态；每帧校验后立即写 sink 并释放引用。增加 289+ 帧测试，断言 adapter 不随帧数积累容器。

### High 4 — `.rpt/.out` parser 既可无 engine 强绑定，也没有生产实现，SWMM 实际不可用

**证据**

- `swmm_adapter.py:210-220` 只定义 `SwmmOutputParser` Protocol。
- `swmm_adapter.py:341-352` 仅当 parser 自行提供 `engine_version` 时才比较；缺少该属性会被接受。
- `test_solver_contract.py:87-105` 的 `FakeParser` 正好没有 `engine_version`，而成功测试仍通过。
- 诊断输出：`unbound_parser_probe_available=True parser=unbound-parser-v1`。
- 全仓搜索未发现生产 `.rpt/.out` parser；实现摘要 `solver-contract.md:104-107` 明确说明真实 parser 未执行/未提供。
- 冻结契约 `IMPLEMENTATION_CONTRACT.md:1358` 要求 parser 与 engine version 绑定。

**影响**

仅配置正确的 SWMM binary 仍会因无 parser 而降级；注入任意仅声明非空 `parser_version` 的对象又可被当作可用 parser。错误版本的二进制布局可能被静默误解，最终产生伪 verified 帧。

**建议**

提供生产、版本绑定的 `.rpt/.out` parser；probe 必须要求 parser 声明并匹配 `engine_version`，最好绑定 engine SHA/parser compatibility manifest。用真实版本化二进制 fixture 覆盖 record layout、截断、continuity/error 与完整 frame grid；未绑定 parser 一律 fail closed。

### High 5 — parser 提供的 `payload_sha256` 未重算校验，错误 hash 的 frame 仍可发布为 verified

**证据**

- `base.py:425-434` 仅在 `payload_sha256 is None` 时计算 hash；调用方提供任意值时不验证。
- `swmm_adapter.py:841-928` 对现成 `SimulationFrame` 只校验 identity/index/time/quality，不重算 payload hash。
- 冻结 frame 幂等与冲突语义依赖可信 hash，见 `IMPLEMENTATION_CONTRACT.md:1347-1350`、`:770-813`。
- 诊断让 parser 为 payload `{"x":0}` 声称 `hash_json({"x":999})`，adapter 仍输出：`mismatched_frame_hash_accepted=True result_status=verified`。
- 当前成功测试只覆盖默认自动计算 hash，没有负向篡改用例。

**影响**

sink 可把内容 A 登记为内容 B 的 hash；重试幂等判断、`FRAME_ATTEMPT_CONFLICT`、frame manifest 与结果审计均被破坏，且 adapter 仍给出 verified summary。

**建议**

adapter 边界必须从实际 payload 重算 canonical SHA-256，校验 64 位小写 hex，并与 parser 声称值比较；不一致立即以稳定 result/schema error 拒绝，且不得 finalize。

### High 6 — `SolverInputManifest` 既不完整也不真正不可变，不能作为冻结 request/input fingerprint

**证据**

- 冻结契约 `IMPLEMENTATION_CONTRACT.md:714-716` 要求 fingerprint 绑定 parameter schema/set；`base.py:1064-1090` 没有 `parameter_set_id`、parameter content hash 或 parameter schema hash 字段，也没有强制它们存在于 `solver_config`。
- 测试 fixture `test_solver_contract.py:159-179` 只提供 routing config，仍被视为完整 manifest。
- `base.py:1144-1147` 对 `solver_config` 只做顶层 `dict(...)`；嵌套 dict/list 仍可变。
- `base.py:1149-1177` 每次访问 `solver_config_hash`/`input_sha256` 都重新读取可变 mapping。
- 诊断将已构造 manifest 的嵌套值从 1 改为 2 后，hash 从 `6e171e0a78ce...` 变为 `296774af73c7...`。

**影响**

同一个“frozen”对象可在提交后改变输入 hash，破坏 idempotency、结果重放、审计与 retry 隔离；parameter set 缺失时，不同标定参数可被错误折叠为同一请求身份。

**建议**

把 parameter set ID/content hash/schema ID/version/hash 设为必填；构造时生成 canonical bytes/hash并冻结，优先保存 canonical immutable representation 或 deep-freeze；运行前重算并与冻结值比较。增加“构造后任何嵌套修改不可改变/不能成功”的测试及 parameter-set 变更 hash 测试。

### Medium 1 — solver progress phase 词汇与持久模型不兼容

**证据**

- `base.py:72-79` 定义 `validation`、`parsing`、`completed`。
- `swmm_adapter.py:514-525`、`:593-630`、`:684-686` 发出这些 phase。
- `test_solver_contract.py:247-254` 反而把该错误词汇固化为期望值。
- 持久约束 `backend/app/models/hydraulic_models.py:1251-1254` 只接受 `accepted|validating|solving|quality|persisting|finalizing`。

**影响**

若 `progress_cb` 按统一 solver 事件直接写 simulation，1% 的 `validation` 或 100% 的 `completed` 会违反 DB CHECK；当前接口没有定义任何映射层。

**建议**

统一使用持久 phase 枚举，或提供显式、唯一的 solver-event → durable-phase mapper；在写入前验证 vocabulary，并增加真实 repository 更新测试。

### Medium 2 — registry 把“没有 probe 方法”的对象视为 available

**证据**

- `base.py:1235-1245` 在 `probe_method is None` 时返回 `available=True, reason="no_probe_method"`。
- `base.py:1328-1338` 注册时只验证非空/已知 `solver_id`，不验证 `HydraulicSolver` 协议。
- 诊断注册仅有 `solver_id/version` 的对象后，auto 成功冻结 `py_mw`，probe reason 为 `no_probe_method`。
- 冻结契约 `IMPLEMENTATION_CONTRACT.md:1317-1320` 要求提交 probe 后才冻结。

**影响**

拼写错误或未完成实现的 fallback 会被当作可用并入队，违背“probe before simulation”，把本应在 submit 阶段发现的错误推迟到 worker。

**建议**

注册时执行结构协议检查；缺少 `probe/capabilities/validate/run` 一律拒绝。probe 返回的 solver ID/version 也必须与注册对象一致，并对通用 probe 加有界超时/取消。

### Medium 3 — adapter 独立降级报告硬编码 `py_mw`，即使 fallback 未注册也不报告不可用

**证据**

- `swmm_adapter.py:394-416` 无条件返回 `fallback_solver_id="py_mw"`。
- `base.py:1597-1603` 的默认 registry 初始为空。
- 诊断输出：`standalone_report_fallback=py_mw default_registry_size=0`。

**影响**

能力/降级报告不是实际可执行能力事实；调用方可能把不存在的 fallback 当成可提交路径，违背“明确降级”的可审计要求。

**建议**

standalone report 接受实际 registry/fallback resolver，只有已注册且 probe 可用时才声明 fallback；否则返回明确的 `fallback_unavailable`/null，并保留 SWMM probe 原因。

## 4. 正向观察

- `swmm_adapter.py` 无 import-time EPA/native 依赖；未配置 engine 的 probe 快速返回结构化 `not_configured`。
- 启动使用 argv list、`shell=False`，输入文件固定在 attempt workspace，且 `SwmmSolveRequest` 有 20 MiB 上限。
- explicit 不可用不会 fallback；运行期异常不会在同一 simulation 切换 Python solver。
- frame index/time grid、attempt/simulation/solver/target manifest 基础字段有检查。
- 指定的 9 个 fake-SWMM 测试全部通过，且实现摘要没有虚报真实 EPA/Job Object 已验证。

## 5. 未运行项与边界

按要求未重复最终全量门禁。以下均 **NOT RUN**：

- 全 `backend/tests/hydraulic` 或全 backend pytest；
- 真实 EPA SWMM binary、真实 `.rpt/.out` parser/数值 fixture；
- Windows 真实父子进程、Job Object、orphan-process 测试；
- 24 h/289 帧内存/性能与 workspace/output quota 门禁；
- DB/worker/finalize 的端到端接线。

本审查未修改任何生产代码或测试；仅写入本报告。
