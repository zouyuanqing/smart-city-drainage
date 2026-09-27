# 纯 Python fallback hydraulics 实施摘要

- **实施日期**：2026-09-25
- **模块**：`py_mw`（运动波）与 `py_dw`（扩散波）纯 Python 一维筛查求解器
- **实现范围**：本轮只修改用户授权的 solver/test 两个代码路径，并新增本摘要；未提交、未推送。
- **结论**：功能实现和小网/环网/质量守恒测试已通过；全量后端、性能基准、worker 接入和冻结 `NetworkSnapshot`/`FrameSink` 集成未在本轮完成，不能据此宣称 AC-3 或 5000/10000 性能门禁通过。

## 1. 需求与契约依据

1. 源 PRD 的 FR-3 要求 Windows 可运行的纯 Python 运动波/扩散波降级求解器、5 分钟帧和守恒质量门禁（`docs/hydraulic-twin/SOURCE_PRD.md:20`）；源 PRD 还把 5000 节点/10000 管段/24h、4 核纯 Python 180 秒列为 NFR（`docs/hydraulic-twin/SOURCE_PRD.md:30`）。
2. 冻结补充契约固定 `py_mw`/`py_dw` 身份、产品保真边界，以及禁止把 `py_dw` 冒充 EPA SWMM 动态波（`docs/hydraulic-twin/PRD_SUPPLEMENT.md:143-155`）。质量台账公式、`1e-4/1e-3` 残差门禁、Courant `<=0.5` 和最多 20 个子步见 `docs/hydraulic-twin/PRD_SUPPLEMENT.md:342-361`。
3. 实施契约要求 `py_mw` 使用节点连续性 + Manning 管段关系并支持树/环及容量受限超载近似，`py_dw` 对超临界/压力流 fail-closed，并要求相同 frame schema（`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1361-1374`）。
4. 现有稳定键拓扑校验和环路识别已存在于 `backend/app/services/hydraulic/topology.py:295-488`；树环种子生成及连通性检查已存在于 `backend/app/services/hydraulic/seed.py:461-495`。solver 不重复修改这些共享模块。

## 2. 实际代码改动

### 2.1 `backend/app/services/hydraulic/solvers/python_hydraulics.py`

新增独立、无 ORM/DB/文件系统依赖的 fallback solver，公开入口为 `solve_network(...)`（`backend/app/services/hydraulic/solvers/python_hydraulics.py:1373-1562`）。

- **强类型本地输入**：`HydraulicNode`、`HydraulicPipe`、`Catchment`、`HydraulicNetwork`、`RainfallForcing`、`SolverConfig`；输入校验拒绝重复 key、悬空引用、逆坡、非有限尺寸、非法初值和非法时间配置（`backend/app/services/hydraulic/solvers/python_hydraulics.py:120-408`）。
- **运动波**：用 pipe slope Manning target、节点水深修正和容量受限 demand；流量方向保持有向管段方向（`backend/app/services/hydraulic/solvers/python_hydraulics.py:908-925`）。
- **扩散波**：用节点 water-surface head difference 的 Manning resistance；Froude `>=0.95`、压力流和快速反向会抛出 `UnsupportedRegimeError`（`backend/app/services/hydraulic/solvers/python_hydraulics.py:927-969`）。
- **截面与充满度**：圆形管采用稳定的圆弓形面积/水力半径查表，矩形管采用 Manning 迭代；输出 `flow_m3s`、`velocity_mps`、`depth_m`、`fill_ratio`、capacity、demand/surcharge ratio、方向和状态（`backend/app/services/hydraulic/solvers/python_hydraulics.py:692-890`、`backend/app/services/hydraulic/solvers/python_hydraulics.py:1009-1049`）。
- **节点汇流与环网**：每个内部步同时计算所有 pipe，并按 source storage 比例限制出流，再将同一 transfer 作为 destination inflow；没有先树化再丢弃环路边（`backend/app/services/hydraulic/solvers/python_hydraulics.py:1123-1303`）。节点报告水深、体积、水面高程、流入/流出和 `surcharged` 状态。
- **稳定时间步**：初始步长为 `min(rain_step, report_interval, 60)`，再按流速/水深计算 CFL 步长；每个 reporting interval 强制 `max_substeps<=20`，极端短管会明确失败而不是输出不稳定结果（`backend/app/services/hydraulic/solvers/python_hydraulics.py:1079-1101`、`backend/app/services/hydraulic/solvers/python_hydraulics.py:1436-1494`）。同时提供 `courant_number` 和 `stable_time_step` 公共 helper（`backend/app/services/hydraulic/solvers/python_hydraulics.py:658-689`）。
- **超载/压力状态**：demand 超过 capacity 时保留 `surcharge_ratio>1` 并标记 `SURCHARGED`；capacity 附近标记 `PRESSURIZED`（`backend/app/services/hydraulic/solvers/python_hydraulics.py:972-986`）。
- **质量守恒诊断**：`MassBalanceLedger` 实现
  `initial_storage + rainfall + boundary_in - infiltration - outfall - overflow - boundary_out - correction - final_storage`，相对残差和质量状态阈值见 `backend/app/services/hydraulic/solvers/python_hydraulics.py:477-538`。每个 frame 和最终 summary 都带残差、Courant、最小步长、子步数和 quality status（`backend/app/services/hydraulic/solvers/python_hydraulics.py:563-631`）。
- **可复现输出**：所有数值序列按输入顺序计算，输出 canonical JSON 和 SHA-256；结果明确携带 `is_authoritative=false`、`fast_screening`/`reduced_fidelity`，不会把 fallback 宣称为工程真值（`backend/app/services/hydraulic/solvers/python_hydraulics.py:593-631`）。

### 2.2 `backend/tests/hydraulic/test_python_hydraulics.py`

新增 18 个 focused tests（测试文件共 428 行）：

- 干管零降雨、零流量、零库容和零残差（`backend/tests/hydraulic/test_python_hydraulics.py:82-109`）。
- 串联三节点、降雨脉冲传播、outfall 排放和逐项质量闭合（`backend/tests/hydraulic/test_python_hydraulics.py:112-148`）。
- 并联环路由两条 loop pipe 同时计算且无质量增益（`backend/tests/hydraulic/test_python_hydraulics.py:151-188`）。
- capacity 超载、fill ratio、surcharge ratio、节点 surcharged（`backend/tests/hydraulic/test_python_hydraulics.py:191-226`）。
- `py_dw` 反向流与 `py_mw` 不反向的差异（`backend/tests/hydraulic/test_python_hydraulics.py:229-273`）。
- 扩散波超临界拒绝（`backend/tests/hydraulic/test_python_hydraulics.py:276-309`）。
- 极端短管触发 20 子步限制（`backend/tests/hydraulic/test_python_hydraulics.py:312-338`）。
- CFL 公式、非法极端配置、非有限尺寸和相同输入 canonical JSON 确定性（`backend/tests/hydraulic/test_python_hydraulics.py:341-428`）。

## 3. 测试命令与真实结果

所有命令均在 `C:\Users\zyq\Documents\opencode-pn\goal测试\smart-city-drainage\backend` 执行；本轮实际解释器为 `Python 3.14.4`（`C:\Python314\python.exe`），不是契约目标 Python 3.11。

| 命令 | 结果 |
|---|---|
| `python -m py_compile app/services/hydraulic/solvers/python_hydraulics.py tests/hydraulic/test_python_hydraulics.py` | exit 0；编译通过 |
| `python -m pytest tests/hydraulic/test_python_hydraulics.py -q` | **18 passed**, 396 warnings；warnings 来自环境中的 pytest-asyncio deprecation，不是失败 |
| `python -m pytest tests/hydraulic -q` | **160 passed**, 4801 warnings；现有 hydraulic 目录测试全绿，warnings 仍为 pytest-asyncio deprecation |
| 自定义行长度检查（两个 owned 文件，`>88` 行） | 两个文件均输出 `[]`；这是辅助检查，不等价于 Black |

### 未运行/工具不可用

以下命令实际尝试过，但当前 Python 3.14 环境没有对应工具，不能伪称通过：

- `python -m black app/services/hydraulic/solvers/python_hydraulics.py tests/hydraulic/test_python_hydraulics.py` → `No module named black`（exit 1）。
- `python -m isort --check-only ...` → `No module named isort`（exit 1）。
- `python -m flake8 ...` → `No module named flake8`（exit 1）。
- `python -m mypy app/services/hydraulic/solvers/python_hydraulics.py` → `No module named mypy`（exit 1）。
- `python -m ruff check ...` → `No module named ruff`（exit 1）。

未运行：全 backend pytest、Black/isort/Flake8/mypy 的可用环境检查、5000/10000/24h 180 秒 benchmark、worker/数据库/FrameSink 端到端、EPA SWMM 对比、收敛/解析解验证。

## 4. 质量判断与剩余风险

1. **守恒是显式且可审计的**：本实现的网络内部 transfer 在 ledger 中成对抵消，catchment gross rainfall、depression/runoff loss、node overflow、outfall discharge 分项记录；测试观察到相对残差 `<=1e-12`。这证明当前离散实现的台账闭合，不等同于独立有限体积收敛证明。
2. **fallback 保真边界**：这是容量受限的 screening/reduced-fidelity 模型，未实现水锤、完整压力流、泵/闸/堰/孔口、rating curve/fixed stage/time-series boundary、LID/RDII 或二维积水。`py_dw` 对不支持流态主动失败，符合 fail-closed 方向（`backend/app/services/hydraulic/solvers/python_hydraulics.py:1-15`、`backend/app/services/hydraulic/solvers/python_hydraulics.py:940-968`）。
3. **输入尚未接入冻结强类型快照**：本轮 owned path 没有 `NetworkSnapshot`、PondCell、boundary union、`FrameSink` 或 worker 持久化代码；后续 integration owner 需要把 immutable snapshot materialize 成这些本地 dataclass，并将 frames 写入 attempt-scoped sink。不能把本模块直接宣称成完整 `HydraulicSolver` 协议实现。
4. **性能未证明**：纯标准库实现优先可测试性，尚未执行固定 5000/10000 fixture 的 180 秒门禁；大规模下每步遍历所有 pipe 的成本、报告帧序列化和结果持久化仍需 benchmark/profile。
5. **CFL 采取保守 full-depth 上界**：极短/极小管会早于 20 子步门禁失败；这是有意的保守拒绝，但真实 worker 需要按冻结 refinement profile 决定 retry/terminal 映射，本模块不擅自修改共享任务状态机。
6. **工作树并发变化**：仓库存在其他模块的未提交改动；本轮只新增/修改上述两个 owned code paths 和本摘要，没有覆盖、提交或推送其他文件。
