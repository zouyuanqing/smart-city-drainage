# 纯 Python 管网求解器独立审查

- 审查日期：2026-09-25
- 审查对象：`backend/app/services/hydraulic/solvers/python_hydraulics.py`、`backend/tests/hydraulic/test_python_hydraulics.py`
- 实施摘要：`docs/hydraulic-twin/implementation/fallback-hydraulics.md`
- 审查方式：只读复核冻结契约、真实实现、测试与实施摘要；仅写入本审查报告，没有修改实现或测试；没有重复最终全量门禁。
- 结论：**不批准（`approved=false`）**。发现 5 个 high、3 个 medium；high 包括有限雨型尾部被重复使用、初始管段状态没有进入守恒台账、无效拓扑被标记为 verified、`py_dw` 不满足冻结的扩散波算法边界，以及返回值不能作为冻结的 attempt-scoped frame/summary 发布。

## 1. 契约与范围依据

本次按任务给定规格审查：实现一维运动波/扩散波、稳定时间步、节点汇流、管段流量/充满度/超载，优先正确性和可测试性，并覆盖干管、串联、环网、降雨脉冲、零值与极端参数。冻结补充契约还要求：

- 帧必须流式写入 attempt-scoped sink，不能先在内存中积累全部帧：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:161-163`。
- 断开分量不得进入 simulation-ready：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:327-330`；强类型 initial state 必须覆盖 node/pipe/pond 并带不可变 hash：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:264-269`。
- `py_mw` 要有节点连续性与 Manning 管段关系，`py_dw` 要有守恒通量、显式省略局部惯性，并对超临界/压力流 fail closed；所有 solver 要有相同 frame schema、版本和质量诊断：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1361-1374`。
- 24 h/5 min、5000 节点/10000 管段的 180 s 是 `py_mw` 硬性能门禁，不是当前 focused test 可以替代的证据：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:407-414`。

实现摘要已明确写出本轮尚未接入冻结 `NetworkSnapshot`、`FrameSink`、worker 持久化和性能基准：`docs/hydraulic-twin/implementation/fallback-hydraulics.md:67-73`。本次没有把摘要中的历史测试结果当成本轮通过证据。

## 2. 本轮实际执行的检查

### 2.1 任务指定的 focused test

命令：

```text
cd "/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && python -m pytest tests/hydraulic/test_python_hydraulics.py -q
```

实际输出：

```text
..................                                                       [100%]
18 passed, 396 warnings in 0.06s
```

解释器为 Python 3.14.4；warning 来自 `pytest-asyncio` 弃用提示。这只证明现有 18 个断言通过，不证明下列失败边界不存在。

### 2.2 任务列出的 hydraulic test 目录

命令：

```text
cd "/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && python --version && python -m pytest tests/hydraulic -q
```

实际输出：

```text
Python 3.14.4
160 passed, 4801 warnings in 1.79s
```

这是本轮执行的 `tests/hydraulic` 目录测试，不是全 backend、构建、性能或 AC 最终门禁。

### 2.3 编译检查

命令：

```text
cd "/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && python -m py_compile app/services/hydraulic/solvers/python_hydraulics.py tests/hydraulic/test_python_hydraulics.py
```

实际输出：无 stdout/stderr，exit 0。

### 2.4 目标 Python 3.11 尝试

命令：

```text
cd "/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && py -V:Astral/CPython3.11.15 -m pytest tests/hydraulic/test_python_hydraulics.py -q
```

实际输出：

```text
C:\Users\zyq\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe: No module named pytest
```

因此本轮没有 Python 3.11 focused PASS；不能把 3.14.4 结果外推为冻结目标运行时结果。

### 2.5 定向诊断（均未写仓库文件）

**D1 — 有限雨型序列耗尽后的行为**

```text
cd "/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && python - <<'PY'
from app.services.hydraulic.solvers import python_hydraulics as h
f = h.RainfallForcing((100.0,), 60.0)
print([(t, f.intensity_at(t)) for t in (0, 59.999, 60, 120, 180)])
PY
```

输出：

```text
[(0, 100.0), (59.999, 100.0), (60, 100.0), (120, 100.0), (180, 100.0)]
```

同一 180 s、1000 m² catchment 的实际求解输出为 `rainfall_input_m3=5.000000000000002`；按一个 60 s、100 mm/h 脉冲计算应为 `1.6666666666666667 m³`。

**D2 — 初始管段流量与守恒台账**

```text
cd "/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && python - <<'PY'
from app.services.hydraulic.solvers import python_hydraulics as h
n = h.HydraulicNetwork(
    (h.HydraulicNode('A', 1.0, 2.0), h.HydraulicNode('OUT', .9, 2.0, kind='outfall')),
    (h.HydraulicPipe('P', 'A', 'OUT', length_m=20.0, manning_n=.013,
                     upstream_invert_m=1.0, downstream_invert_m=.9,
                     diameter_m=.3, initial_flow_m3s=.01),),
)
r = h.solve_network(n, h.RainfallForcing((), 60.0),
                    config=h.SolverConfig(1.0, 1.0, 1.0))
print(r.frames[0].pipes[0].to_dict())
print(r.frames[1].pipes[0].to_dict())
print(r.mass_balance.to_dict())
PY
```

输出的关键部分：

```text
frame0 flow_m3s=0.01 depth_m=0.077521... state=open_channel
frame1 flow_m3s=0.0 depth_m=0.0 state=dry
initial_storage_m3=0.0 rainfall_input_m3=0.0 outfall_discharge_m3=0.0
final_storage_m3=0.0 residual_m3=0.0 quality_status=verified
```

**D3 — 空网络与断开分量**

```text
cd "/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && python - <<'PY'
from app.services.hydraulic.solvers import python_hydraulics as h
empty = h.solve_network(h.HydraulicNetwork((), ()), h.RainfallForcing((), 60.0),
                        config=h.SolverConfig(1.0, 1.0, 1.0))
print('empty', len(empty.frames), empty.quality_status.value)
PY
```

输出：

```text
empty 2 verified
```

另一个包含两个互不连通分量、没有 outfall 的网络，使用以下命令构造并求解：

```text
cd "/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && python - <<'PY'
from app.services.hydraulic.solvers import python_hydraulics as h
n = h.HydraulicNetwork(
    (h.HydraulicNode('A1', 2.0, 2.0), h.HydraulicNode('B1', 1.9, 2.0),
     h.HydraulicNode('A2', 2.0, 2.0), h.HydraulicNode('B2', 1.9, 2.0)),
    (h.HydraulicPipe('P1', 'A1', 'B1', 20.0, .013, 2.0, 1.9, .3),
     h.HydraulicPipe('P2', 'A2', 'B2', 20.0, .013, 2.0, 1.9, .3)),
    (h.Catchment('C1', 'A1', area_m2=1000.0),),
)
r = h.solve_network(n, h.RainfallForcing((1000.0,), 60.0),
                    config=h.SolverConfig(60.0, 60.0, 60.0))
print('accepted', r.quality_status.value,
      'node_states', [x.state.value for x in r.frames[-1].nodes],
      'overflow', r.mass_balance.overflow_m3)
PY
```

输出：

```text
accepted verified node_states ['surcharged', 'surcharged', 'dry', 'dry'] overflow 16.266666666666673
```

**D4 — 干管陡坡的 diffusion-wave 误拒绝**

```text
cd "/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && python - <<'PY'
from app.services.hydraulic.solvers import python_hydraulics as h
n = h.HydraulicNetwork(
    (h.HydraulicNode('A', 100.0, 2.0), h.HydraulicNode('B', 0.0, 2.0)),
    (h.HydraulicPipe('P', 'A', 'B', 20.0, .013, 100.0, 0.0,
                     shape='rectangular', width_m=10.0, height_m=.01),),
)
for mode in (h.RoutingMode.MOTION_WAVE, h.RoutingMode.DIFFUSION_WAVE):
    try:
        r = h.solve_network(n, h.RainfallForcing((), 60.0), routing_mode=mode,
                            config=h.SolverConfig(1.0, 1.0, 1.0))
        print(mode.value, [(f.pipes[0].flow_m3s, f.pipes[0].state.value) for f in r.frames])
    except Exception as exc:
        print(mode.value, type(exc).__name__, str(exc))
PY
```

输出：

```text
motion_wave returned [(0.0, 'dry'), (0.0, 'dry')]
diffusion_wave raised UnsupportedRegimeError diffusion-wave pressure flow is unsupported; Froude/regime gate requires open-channel flow
```

**D5 — 极端但有限输入的异常类型**

```text
cd "/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && python - <<'PY'
from app.services.hydraulic.solvers import python_hydraulics as h
for label, action in (
    ('tiny_report', lambda: h.SolverConfig(60.0, 1e-320, 1.0)),
    ('tiny_rain_step', lambda: h.RainfallForcing((1.0,), 1e-320).intensity_at(1.0)),
    ('huge_pipe', lambda: h._full_geometry(h.HydraulicPipe(
        'P','A','B',20.0,.013,1.0,.9,diameter_m=1e150))),
):
    try:
        action()
        print(label, 'accepted')
    except Exception as exc:
        print(label, type(exc).__name__, str(exc))
PY
```

输出：

```text
tiny_report OverflowError cannot convert float infinity to integer
tiny_rain_step OverflowError cannot convert float infinity to integer
huge_pipe OverflowError (34, 'Result too large')
```

**D6 — 返回 frame/result 字段**

```text
cd "/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && python - <<'PY'
from app.services.hydraulic.solvers import python_hydraulics as h
n = h.HydraulicNetwork(
    (h.HydraulicNode('A', 1.0, 2.0), h.HydraulicNode('OUT', .9, 2.0, kind='outfall')),
    (h.HydraulicPipe('P', 'A', 'OUT', length_m=20.0, manning_n=.013,
                     upstream_invert_m=1.0, downstream_invert_m=.9,
                     diameter_m=.3, initial_flow_m3s=.01),),
)
r = h.solve_network(n, h.RainfallForcing((), 60.0),
                    config=h.SolverConfig(1.0, 1.0, 1.0))
print('result_keys=', sorted(r.to_dict()))
print('frame_keys=', sorted(r.frames[-1].to_dict()))
PY
```

输出：

```text
result_keys= ['frames', 'is_authoritative', 'mass_balance', 'model_fidelity', 'quality_status', 'routing_mode', 'solver_id', 'stability']
frame_keys= ['mass_residual_m3', 'mass_residual_relative', 'max_courant', 'min_time_step_s', 'nodes', 'pipes', 'quality_status', 'substep_count', 'time_s']
```

## 3. Findings

### H-01（高）：有限雨型序列耗尽后重复最后一个非零雨强，脉冲总量被放大

**证据**

- `RainfallForcing` 的 docstring 明确声明“序列耗尽后剩余时间为 dry”：`backend/app/services/hydraulic/solvers/python_hydraulics.py:316-321`。
- 实际 `intensity_at()` 用 `min(index, len-1)` 把耗尽后的时间永久夹到最后一个样本：`:338-345`。
- D1 显示 `(100.0,), step=60` 在 60、120、180 s 仍返回 100 mm/h；实际求解把一个 60 s 脉冲计成 5.0 m³，而应为 1.6667 m³。

**影响**

任何以非零雨强结束、随后应干燥的自定义雨型都会持续产水；下游流量、节点库容、溢流和所有质量台账都会被系统性高估。这直接破坏任务规格中的“降雨脉冲”和质量守恒诊断。

**修复建议**

在索引达到序列长度时返回 0，或在 materialize 阶段显式补齐 dry 区间；同时验证序列长度与 `duration/step` 的一致性。补充“最后样本非零、尾部为空”的总量/流量回归测试。

### H-02（高）：初始管段流量/水深被展示但没有进入守恒台账，并在第一步静默丢失

**证据**

- `_initial_runtime()` 把 `initial_flow_m3s` 和由其计算的水深放进 runtime：`:989-1005`。
- 初始 storage 只由节点 `initial_depth_m * surface_area_m2` 构成：`:1387-1394`；`_ledger()` 也只对传入的节点 storage 求和：`:1121-1141`。
- 每个内部步重新根据节点深度请求流量，再把管段水深按流量查表：`:1172-1193`、`:1285-1313`；没有管段体积或初始管段流量的台账项。
- D2 中 frame 0 报告 `flow=0.01, depth≈0.0775`，frame 1 变为 `flow=0, depth=0`，但 `initial_storage=0`、`residual=0`、`quality=verified`。

**影响**

warm/非零初始管段状态无法被重现，初始非零流量可以在没有质量记录的情况下消失；质量守恒“通过”只证明节点 transfer 的代数闭合，不证明一维管段状态守恒。冻结契约要求 initial state 覆盖 node/pipe/pond 并带独立 hash：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:264-269`。

**修复建议**

明确 `initial_flow_m3s` 是初始状态还是边界条件；将管段初始体积/水深纳入 initial/final storage，或在首步显式记录该初始状态到最终状态的 transfer。增加非零初始管段流量的守恒与 warm-state round-trip 测试。

### H-03（高）：空网络、断开分量和无明确边界的网络会被接受并标记为 verified

**证据**

- `HydraulicNetwork.__post_init__()` 只检查重复 key 与悬空引用：`:280-307`；没有非空、连通分量、每分量 outfall/boundary 或 simulation eligibility 校验。
- `solve_network()` 直接从网络建立 runtime，没有 topology preflight：`:1387-1402`。
- `_node_results()` 只把“没有入/出管”的单个节点标为 `disconnected`，不会识别由多个节点组成的断开分量：`:1340-1357`。
- D3 的空网络返回 2 帧 `verified`；两个断开、无 outfall 的分量也返回 `verified`，并产生 `overflow=16.2667 m³`。
- 冻结拓扑语义规定 `DISCONNECTED_COMPONENT` 的 simulation-ready 必须阻断：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:327-330`。

**影响**

无网络、无边界或无法解释的局部结果可以进入 verified 质量门禁；调用方无法区分“确实 dry 的合法分量”和“求解器根本没有可验证边界”。这会把输入拓扑错误伪装成质量通过。

**修复建议**

在求解前增加非空、连通性、每分量合法 outfall/boundary、孤立分量和环策略检查；对不满足条件的输入返回结构化 `NetworkValidationError`/不可运行报告，而不是生成 verified frame。补空网络、断开分量、无 outfall 和多 outfall 测试。

### H-04（高）：`py_dw` 是全断面代数 Manning 关系，不是冻结契约要求的守恒扩散波输运

**证据**

- diffusion-wave 分支只根据端点水头差和全管几何的 `pipe.resistance` 计算 `sqrt(abs(head_difference)/resistance)`：`:927-953`。
- 管段水深随后直接由流量通过 `_normal_depth_for_flow()` 重新查表：`:1293-1309`；没有管段单元体积、沿程水面状态或相邻单元通量更新。
- source storage 的比例缩放发生在 regime 检查之后：`:1172-1237`；这既不是守恒通量求解，也不能代表局部惯性省略的有限体积/有限差分形式。
- 冻结契约要求 `py_dw` 使用守恒通量形式并对亚临界/压力流 fail closed：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1361-1374`。
- D2 同时证明非零管段状态没有独立存储，说明该实现无法承载真正的管段扩散/回水状态。

**影响**

它可以通过“端点有水头差就算流量”的小网 smoke test，但不能计算干湿锋、沿程回水或有限体积连续性；`py_dw` 身份与结果含义不符。由于没有独立管段状态/收敛迭代，回水与环网的物理正确性无法由现有测试证明。

**修复建议**

为每条管段建立守恒的体积/水深状态和面通量，使用节点水头与沿程水面更新流量；实现显式迭代/收敛诊断，并补单管解析均匀流、静水、线性水面、缓坡回水、闭合环和时间步收敛测试。若产品只允许该代数筛查模型，应把它单独命名为非扩散波近似，而不是 `py_dw`。

### H-05（高）：返回值不是冻结的 attempt-scoped frame/summary，且把全部帧留在内存

**证据**

- `FrameResult` 只有 `time_s/nodes/pipes` 与少量质量字段：`:563-590`；没有 `schema_version`、`simulation_id`、`attempt_id`、`frame_index`、`valid_at`、network revision、target manifest/hash、solver version、payload hash。
- `SimulationResult.to_dict()` 只输出 solver id、模式、fidelity、ledger、stability 和 frames：`:593-631`；没有 `solver_version`、network/rainfall/solver-config hash、frame count、first/last time、warnings/degraded features 或 summary provenance。
- `solve_network()` 在 `:1402` 创建 `frames` list，在每个报告点 append，最后以 tuple 全部返回：`:1517-1554`；`to_dict()` 又一次性序列化全部帧：`:608-617`。
- 冻结 schema 要求 attempt-scoped frame registration、simulation/revision/manifest/solver version、frame index/time 和 payload hash：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:773-814`；`FrameSink` 要求流式 write/finalize：`:1339-1350`；补充契约明确禁止先积累全部 289 帧：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:161-163`。
- D6 的实际 key 集合与上述 frame/summary schema 不匹配。

**影响**

当前返回值无法被 durable worker 以幂等、fencing、发布指针和可审计 hash 的方式消费；24 h/5 min 目标下还会把 4,335,000 个对象-帧状态先堆在 Python 内存中，违反流式和容量边界。即使后续 integration owner 接线，也不能自动补回 frame 内缺失的 provenance 字段。

**修复建议**

先定义并实现 `HydraulicSolver`/`FrameSink` 适配器：solver 内部逐 frame 写出，不返回全量 list；每帧携带完整 attempt/target/solver/schema/payload 信息，summary 单独保存版本、输入/config hash、frame manifest、质量与 warning。增加 frame schema、幂等/fencing、frame count 和流式内存回归测试。

### M-01（中）：干管陡坡的零流量 `py_dw` 被错误判为压力流

**证据**

- `_requested_pipe_flow()` 先用 invert+节点水深形成 target，并在 target 上做 full-depth/Froude gate：`:927-953`。
- 只有之后 `_integrate_step()` 才按 source 可用水量和 capacity 缩放实际流量：`:1172-1237`。
- D4 中相同无水、陡坡、矩形管网络，`motion_wave` 正常返回 dry/0 flow，而 `diffusion_wave` 抛 `UnsupportedRegimeError`。

**影响**

一个应当是 dry 的零值网络无法运行，py_dw 会对“假想需求”而不是“实际可输送流量”做 regime 拒绝；这会误伤零降雨/干管边界，且与任务 testHint 的零值要求不符。

**修复建议**

在源节点没有可用水量或缩放后实际流量为零时跳过 regime gate；先确定可实现流量，再对实际流量做压力/Froude 判定。增加干陡坡、平坡静水和 dry→shallow→dry 的 py_dw 测试。

### M-02（中）：有限极端参数通过入口校验后在内部以裸 `OverflowError` 崩溃

**证据**

- `SolverConfig` 只检查字段 finite/正数；`:361-403` 未检查 `duration/reporting_interval` 的商和派生量。
- `_full_geometry()` 直接对用户尺寸做幂、平方和 Manning 运算：`:761-787`，没有 derived finite/overflow guard。
- D5 中三个有限输入分别产生：`SolverConfig(60, 1e-320, 1)` → `OverflowError: cannot convert float infinity to integer`；`RainfallForcing(..., 1e-320).intensity_at(1)` → 同一 `OverflowError`；`diameter_m=1e150` 的合法 finite pipe 在 `_full_geometry` → `OverflowError (34, 'Result too large')`。

**影响**

不可信但 finite 的输入可能绕过结构化 `NetworkValidationError`/稳定错误码，直接让 worker 任务以未分类异常失败；这也使“极端参数应 fail closed”的测试提示无法成立。

**修复建议**

在构造阶段验证派生商、几何幂、容量和时间的有限范围；对所有算术溢出转换为稳定的 validation/stability error。补充有限但极大的 diameter、length、area、rain step、reporting interval 及边界组合测试，并断言失败前没有部分结果。

### M-03（中）：测试矩阵没有覆盖已复现的契约边界、解析/收敛和目标性能

**证据**

- 现有 focused 文件共 18 个测试，覆盖干管、串联、平行环、超载、py_dw 反向/超临界、短管 CFL、配置和确定性：`backend/tests/hydraulic/test_python_hydraulics.py:81-428`。
- 没有测试有限雨型尾部、非零初始管段状态、空/断开网络 eligibility、干陡坡 py_dw、矩形截面静水/回水、闭合环守恒 fixture、frame schema/流式 sink 或有限极端值。
- 本轮 D1–D5 都实际触发了 focused suite 未覆盖的失败；D6 也证明返回 schema 未被测试。
- 冻结稳定性/质量要求包含质量门禁、CFL、20 substep 和 `py_dw` 非法流态：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:343-362`；5000/10000/24 h 180 s 仍需独立 benchmark：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:407-414`。

**影响**

当前 18/160 个绿色结果不能防止本报告的 high 问题回归，也不能证明一维输运、环网物理正确性或目标规模性能；未来 integration owner 可能误把 focused pass 当作 solver 验收。

**修复建议**

按失败诊断补负向/守恒/收敛测试，再增加固定 5k/10k/24 h fixture 的独立 benchmark 和 Python 3.11 运行证据；测试应断言 frame schema、attempt 幂等、质量门禁和实际内存/输出规模，而不只断言返回对象存在。

## 4. 已确认的正面项

- 纯 Python、无 ORM/DB/文件系统副作用，适合隔离单测；`py_compile` 在本轮 exit 0。
- 在 Python 3.14.4 下，现有 focused 18 tests 与本轮 `tests/hydraulic` 160 tests 均通过。
- 现有测试确实覆盖了基本干管、串联传播、并行环、质量残差、超载、py_dw 反向/超临界拒绝、CFL helper 和相同输入 canonical JSON 确定性。
- `is_authoritative=false` 与 `fast_screening`/`reduced_fidelity` 标记方向正确。

这些正面项不能抵消上述 high；质量残差接近零目前只证明当前节点 transfer ledger 的代数闭合，不证明管段输运或发布 schema 正确。

## 5. 未运行/不能据此宣称通过

- 未运行全 backend pytest、Black/isort/Flake8/mypy、最终 G0–G6/AC 门禁、Compose/数据库/worker/FrameSink E2E。
- 未运行 EPA SWMM 对比、解析解/收敛验证、真实观测率定、5000/10000/24 h/180 s benchmark、峰值 RSS 和 289-frame 流式持久化。
- Python 3.11 focused test 实际尝试但因该解释器没有 pytest 而未运行；不能把 Python 3.14.4 的通过结果当作目标运行时证据。
- 本轮没有修改 owned implementation/test；只写入了本报告。

## 6. 最终判断

**`approved=false`。** H-01～H-05 分别破坏雨型输入语义、初始管段质量守恒、拓扑准入、`py_dw` 数值身份和结果发布契约，均属于核心交付或发布阻断问题。M-01～M-03 还应一并修复并补测。修复后需重新运行 focused、hydraulic 目录、Python 3.11 以及独立性能/worker 证据，再进行独立复审。
